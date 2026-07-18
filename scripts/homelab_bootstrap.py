"""Common bootstrap runner for all homelab VM stacks.

Provides ``BootstrapRunner`` — a template that enforces a unified flow:

  1. (optional) re-exec as root
  2. parse args  (--force, --non-interactive/-y, --up, --verbose/-v)
  3. load and validate .env
  4. run stack-specific bootstrap steps
  5. wire observability sidecars (when enabled)
  6. validate compose
  7. (optional) bring up the stack
  8. print summary

Each VM stack provides a ``stack_config`` module that defines the
stack-specific pieces (required vars, compose overlays, bootstrap steps,
post-deploy actions).  See the plan's Phase 3 for the full contract.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from scripts.homelab_common import (
    is_placeholder,
    load_env,
    log,
    require_docker,
    resolve_config_base,
    setup_logging,
    setup_observability_config,
)
from scripts.homelab_logging import StepTracker, make_tracker


# ---------------------------------------------------------------------------
# Arg parsing (shared by all stacks)
# ---------------------------------------------------------------------------

def parse_common_args(
    stack_name: str,
    argv: list[str] | None = None,
) -> argparse.Namespace:
    """Parse the standard bootstrap CLI arguments."""
    parser = argparse.ArgumentParser(
        description=f"{stack_name} VM bootstrap (idempotent).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip overridable guardrails (placeholder checks, etc.).",
    )
    parser.add_argument(
        "--non-interactive",
        "-y",
        action="store_true",
        help="Skip prompts (used by deploy.py).",
    )
    parser.add_argument(
        "--up",
        action="store_true",
        help="Bring stack up after successful bootstrap.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show detailed progress within each step.",
    )
    args = parser.parse_args(argv)
    if os.environ.get("HOMELAB_DEPLOY"):
        args.non_interactive = True
    return args


# ---------------------------------------------------------------------------
# Root re-exec (for stacks that require root, e.g. media)
# ---------------------------------------------------------------------------

def reexec_as_root() -> None:
    """Re-exec the current script via sudo, preserving key env vars.

    Calls ``os.execvp`` which **replaces** the current process.
    Must be called before any other setup (logging, arg parsing, etc.).
    """
    if os.geteuid() == 0:
        return

    extra_args: list[str] = []
    if os.environ.get("HOMELAB_DEPLOY"):
        extra_args.append("--non-interactive")

    cmd = ["sudo"]
    for var in ("HOMELAB_DEPLOY", "VM_ROLE"):
        val = os.environ.get(var)
        if val:
            cmd.append(f"{var}={val}")
    cmd += [sys.executable, str(Path(sys.argv[0]).resolve())]
    cmd += extra_args + sys.argv[1:]
    os.execvp(cmd[0], cmd)


# ---------------------------------------------------------------------------
# OS check
# ---------------------------------------------------------------------------

def require_debian() -> None:
    """Exit if the host is not Debian or Ubuntu."""
    os_release = Path("/etc/os-release")
    if not os_release.is_file():
        log.error(
            "Cannot detect OS (/etc/os-release missing). "
            "Expect Debian or Ubuntu."
        )
        raise SystemExit(1)

    content = os_release.read_text()
    env: dict[str, str] = {}
    for line in content.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            env[k] = v.strip('"')

    distro_id = env.get("ID", "")
    id_like = env.get("ID_LIKE", "")
    if distro_id not in ("debian", "ubuntu") and "debian" not in id_like:
        log.error(
            f"This bootstrap targets Debian/Ubuntu. "
            f"Found: ID={distro_id} ID_LIKE={id_like}"
        )
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# Common validation
# ---------------------------------------------------------------------------

def validate_required_vars(
    env: dict[str, str],
    required: list[str],
    tracker: StepTracker,
    *,
    force: bool = False,
) -> None:
    """Check that required vars are set and not placeholders."""
    errors: list[str] = []
    for var in required:
        val = env.get(var, "")
        tracker.detail(f"Checking {var}... {'ok' if val and not is_placeholder(val) else 'MISSING'}")
        if not val or is_placeholder(val):
            errors.append(var)

    if errors:
        msg = f"Required vars missing/placeholder: {', '.join(errors)}"
        if force:
            tracker.warn(f"{msg} (continuing with --force)")
        else:
            tracker.fail(msg)
            raise SystemExit(1)

    tracker.success(f"Environment validated ({len(required)} vars checked)")


# ---------------------------------------------------------------------------
# Common compose helpers
# ---------------------------------------------------------------------------

def build_compose_command(
    stack_dir: Path,
    overlays: list[tuple[str, str, str, str]],
    env: dict[str, str],
) -> list[str]:
    """Build ``docker compose -f ... -f ...`` file args.

    *overlays* is a list of ``(env_var, filename, default, label)`` tuples.
    """
    files = ["-f", str(stack_dir / "compose.yml")]
    for env_var, filename, default, _label in overlays:
        fpath = stack_dir / filename
        if env.get(env_var, default) == "1" and fpath.exists():
            files += ["-f", str(fpath)]
    return files


def validate_compose(
    stack_dir: Path,
    compose_files: list[str],
    tracker: StepTracker,
) -> None:
    """Run ``docker compose config`` and report result."""
    result = subprocess.run(
        ["docker", "compose"] + compose_files + ["config"],
        capture_output=True,
        cwd=stack_dir,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode() if result.stderr else ""
        tracker.fail(f"Compose validation failed\n{stderr}")
        raise SystemExit(1)
    tracker.success("Compose valid")


def bring_up_stack(
    stack_dir: Path,
    compose_files: list[str],
    tracker: StepTracker,
) -> None:
    """Run ``docker compose up -d``."""
    result = subprocess.run(
        ["docker", "compose"] + compose_files + ["up", "-d"],
        cwd=stack_dir,
    )
    if result.returncode != 0:
        tracker.fail("docker compose up failed")
        raise SystemExit(1)
    tracker.success("Stack started")


# ---------------------------------------------------------------------------
# NFS helper (env-driven, replaces interactive prompts)
# ---------------------------------------------------------------------------

def ensure_nfs_mount(
    host: str,
    export: str,
    mount_point: str,
    *,
    read_only: bool = False,
    tracker: StepTracker | None = None,
) -> bool:
    """Ensure an NFS fstab entry exists and the mount is active.

    Also orders docker.service after the mount (see
    ``_ensure_docker_waits_for_mount``) so containers with bind mounts into
    mount_point don't race the NFS mount on VM/docker restart.

    Returns True if the mount is now active, False on failure.
    Idempotent: updates existing fstab entries if options differ.
    """
    rw = "ro" if read_only else "rw"
    # Read-write mounts use `hard`; read-only mounts use `soft`.
    #
    # `hard` retries an NFS op until the server answers instead of failing with
    # EIO on a timeout. For a RW mount that qBittorrent/SABnzbd/*arr hammer, that
    # matters: a `soft` mount can return EIO mid-write and leave silently partial
    # data, because the client gives up without knowing how much the server
    # applied. Observed in practice — deleting a 45 GB file over the `soft` RW
    # mount returned "Input/output error" even though the delete had actually
    # succeeded on the NAS; an *arr running that delete would treat the EIO as
    # failure and orphan the file. `hard` waits for the slow op to finish instead.
    #
    # Read-only mounts (e.g. Plex's library view) stay `soft`: no write to
    # corrupt, and returning EIO on a scan-time NFS blip is *preferable* to a
    # blocked scanner — Plex does not purge library items on an I/O error, only
    # on a clean "file not found", so `soft` avoids false-trashing during a blip.
    #
    # `hard` does NOT risk hanging boot: nofail + _netdev + the wait-for-nas
    # ordering below keep startup bounded whether or not the NAS is reachable.
    reliability = "soft" if read_only else "hard"
    fstab_spec = f"{host}:{export}"
    fstab_line = (
        f"{fstab_spec}\t{mount_point}\tnfs\t"
        f"nofail,_netdev,vers=3,{reliability},timeo=50,retrans=3,{rw}\t0\t0"
    )

    fstab = Path("/etc/fstab")
    fstab_lines = (
        fstab.read_text().splitlines(keepends=True)
        if fstab.is_file()
        else []
    )

    existing_idx: int | None = None
    for i, line in enumerate(fstab_lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        fields = stripped.split()
        if len(fields) >= 2 and fields[1] == mount_point:
            existing_idx = i
            break

    if existing_idx is not None:
        if fstab_lines[existing_idx].rstrip("\n") == fstab_line:
            if tracker:
                tracker.detail(f"fstab entry for {mount_point} already correct")
        else:
            if tracker:
                tracker.detail(f"Updating fstab entry for {mount_point}")
            fstab_lines[existing_idx] = fstab_line + "\n"
            fstab.write_text("".join(fstab_lines))
    else:
        if tracker:
            tracker.detail(f"Adding fstab entry: {fstab_spec} -> {mount_point}")
        with fstab.open("a") as f:
            f.write(fstab_line + "\n")

    mp = Path(mount_point)
    mp.mkdir(parents=True, exist_ok=True)

    _ensure_docker_waits_for_mount(host, mount_point, tracker)

    result = subprocess.run(["mount", "-a"], capture_output=True)
    if result.returncode != 0:
        if tracker:
            tracker.warn(
                f"mount -a failed for {mount_point} (NAS unreachable?). "
                "Boot will not hang (nofail)."
            )
        return False

    if tracker:
        tracker.detail(f"Mounted {mount_point}")
    return True


_WAIT_FOR_NAS_SCRIPT = "/usr/local/bin/wait-for-nas.sh"
_WAIT_FOR_NAS_SCRIPT_CONTENT = """\
#!/bin/sh
# Bounded wait for a host to answer ping before an NFS mount / docker start.
# Always exits 0 so it never blocks boot indefinitely if the NAS stays down
# (mirrors the nofail philosophy of the fstab entries this supports).
HOST="$1"
TIMEOUT="${2:-300}"
INTERVAL=5
elapsed=0
while [ "$elapsed" -lt "$TIMEOUT" ]; do
    if ping -c1 -W2 "$HOST" >/dev/null 2>&1; then
        exit 0
    fi
    elapsed=$((elapsed + INTERVAL))
    sleep "$INTERVAL"
done
exit 0
"""
_WAIT_FOR_NAS_TEMPLATE_CONTENT = """\
[Unit]
Description=Wait (bounded) for NAS %i to become reachable
After=network-online.target
Wants=network-online.target
DefaultDependencies=no

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/bin/wait-for-nas.sh %I
"""


def _write_if_changed(path: Path, content: str, *, executable: bool = False) -> bool:
    if path.is_file() and path.read_text() == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    if executable:
        path.chmod(0o755)
    return True


def _ensure_docker_waits_for_mount(
    host: str, mount_point: str, tracker: StepTracker | None = None
) -> None:
    """Make the NFS mount, and docker.service, wait for the NAS to be reachable.

    Two failure modes this guards against:
      1. VM reboot alone, NAS already up: the NFS mount can take tens of
         seconds, but Docker's restart:unless-stopped containers start the
         instant dockerd comes up, bind-mounting whatever is at mount_point
         right then (2026-07-10 Plex empty-library incident).
      2. Whole-house power outage: the NAS is the slowest thing to boot
         (RAID/DSM init can take minutes), and NFS's own soft-mount retry
         window (~15-20s) gives up long before that. Without an explicit
         wait, the mount fails, Docker starts anyway, and containers are
         stuck on an empty mount point until someone notices and restarts
         them by hand.

    Installs a bounded (5 min) ping-retry wait, instantiated per NAS host via
    a systemd template unit, ordered before both the mount unit itself and
    docker.service. Never blocks boot indefinitely: the wait always gives up
    and proceeds after the timeout (matching the fstab entries' `nofail`).
    """
    escape = subprocess.run(
        ["systemd-escape", "--path", "--suffix=mount", mount_point],
        capture_output=True,
        text=True,
        check=False,
    )
    if escape.returncode != 0:
        if tracker:
            tracker.warn(
                f"Could not resolve systemd mount unit for {mount_point}; "
                "docker.service ordering not configured"
            )
        return
    mount_unit = escape.stdout.strip()

    wait_escape = subprocess.run(
        ["systemd-escape", "--template=wait-for-nas@.service", host],
        capture_output=True,
        text=True,
        check=False,
    )
    if wait_escape.returncode != 0:
        if tracker:
            tracker.warn(
                f"Could not resolve systemd instance name for NAS host {host}; "
                "docker.service ordering not configured"
            )
        return
    wait_unit = wait_escape.stdout.strip()

    changed = False
    changed |= _write_if_changed(
        Path(_WAIT_FOR_NAS_SCRIPT), _WAIT_FOR_NAS_SCRIPT_CONTENT, executable=True
    )
    changed |= _write_if_changed(
        Path("/etc/systemd/system/wait-for-nas@.service"),
        _WAIT_FOR_NAS_TEMPLATE_CONTENT,
    )
    changed |= _write_if_changed(
        Path(f"/etc/systemd/system/{mount_unit}.d/wait-for-nas.conf"),
        f"[Unit]\nWants={wait_unit}\nAfter={wait_unit}\n",
    )
    changed |= _write_if_changed(
        Path(f"/etc/systemd/system/docker.service.d/nfs-mount-{mount_unit}.conf"),
        f"[Unit]\nWants={mount_unit} {wait_unit}\nAfter={mount_unit} {wait_unit}\n",
    )

    if changed:
        subprocess.run(["systemctl", "daemon-reload"], check=False, capture_output=True)
    if tracker:
        tracker.detail(
            f"docker.service + {mount_unit} now wait for NAS {host} "
            f"(bounded, {wait_unit})"
        )


# ---------------------------------------------------------------------------
# BootstrapRunner
# ---------------------------------------------------------------------------

class BootstrapRunner:
    """Unified bootstrap flow for all VM stacks.

    Instantiate with a ``stack_config`` module and call ``run()``.

    The stack_config module must define at minimum:
      - STACK_NAME: str
      - SCRIPT_DIR: Path
      - REQUIRED_VARS: list[str]
      - COMPOSE_OVERLAYS: list[tuple[str, str, str, str]]
      - bootstrap_steps(env, config_base, args) -> list[tuple[str, Callable]]

    Optional attributes:
      - REQUIRES_ROOT: bool       (default False)
      - REQUIRES_DEBIAN: bool     (default False)
      - REQUIRES_DOCKER: bool     (default True)
      - SUPPORTS_UP: bool         (default True)
      - POST_DEPLOY_ACTIONS: list[str]
    """

    def __init__(self, config) -> None:  # noqa: ANN001 — stack_config module
        self.config = config

    def run(self, argv: list[str] | None = None) -> int:
        cfg = self.config

        if getattr(cfg, "REQUIRES_ROOT", False):
            reexec_as_root()

        if getattr(cfg, "REQUIRES_DEBIAN", False):
            require_debian()

        args = parse_common_args(cfg.STACK_NAME, argv)
        setup_logging(verbose=args.verbose)

        deploy_mode = bool(os.environ.get("HOMELAB_DEPLOY"))

        if not deploy_mode:
            log.info(f"--- {cfg.STACK_NAME} VM bootstrap ---")

        # Load .env
        env_file = cfg.SCRIPT_DIR / ".env"
        if not env_file.is_file():
            env_example = cfg.SCRIPT_DIR / ".env.example"
            if env_example.is_file() and getattr(cfg, "COPIES_ENV_EXAMPLE", False):
                shutil.copy2(env_example, env_file)
                log.info("Created .env from .env.example.")
                log.info(f"Fill real values in {env_file}, then re-run bootstrap.")
                return 1
            log.error(
                f"No .env found for {cfg.STACK_NAME} stack. "
                "Copy .env.example to .env, fill required values, then re-run."
            )
            return 1

        env = load_env(env_file)
        config_base = resolve_config_base(
            env.get("CONFIG_ROOT", "./config"), cfg.SCRIPT_DIR,
        )

        # Require docker early if the stack declares it
        if getattr(cfg, "REQUIRES_DOCKER", True):
            require_docker()

        # Build the step list
        stack_steps = cfg.bootstrap_steps(env, config_base, args)

        # Determine extra common steps
        has_observability = env.get("ENABLE_OBSERVABILITY", "1") == "1"
        has_compose_validation = True
        supports_up = getattr(cfg, "SUPPORTS_UP", True)

        extra_steps = 1  # env validation
        if has_observability:
            extra_steps += 1
        if has_compose_validation:
            extra_steps += 1
        if supports_up and args.up and not deploy_mode:
            extra_steps += 1

        total = extra_steps + len(stack_steps)
        tracker = make_tracker(total, verbose=args.verbose)

        # Step: validate env
        with tracker.step("Validating environment"):
            validate_required_vars(
                env, cfg.REQUIRED_VARS, tracker, force=args.force,
            )

        # Stack-specific steps
        for step_name, step_fn in stack_steps:
            with tracker.step(step_name) as ctx:
                step_fn(tracker)
                ctx.mark_done()

        # Observability wiring
        if has_observability:
            with tracker.step("Configuring observability"):
                setup_observability_config(config_base, env, cfg.SCRIPT_DIR)
                tracker.success("Observability configured")

        # Compose validation
        compose_files = build_compose_command(
            cfg.SCRIPT_DIR, cfg.COMPOSE_OVERLAYS, env,
        )
        with tracker.step("Validating compose"):
            validate_compose(cfg.SCRIPT_DIR, compose_files, tracker)

        # Optional bring-up
        if supports_up and args.up and not deploy_mode:
            with tracker.step(f"Starting {cfg.STACK_NAME} stack"):
                bring_up_stack(cfg.SCRIPT_DIR, compose_files, tracker)

        # Post-deploy actions
        for action in getattr(cfg, "POST_DEPLOY_ACTIONS", []):
            tracker.add_action(action)

        # Summary
        if not deploy_mode:
            overlay_status = [
                (label, env.get(var, default) == "1")
                for var, _file, default, label in cfg.COMPOSE_OVERLAYS
            ]
            tracker.print_summary(
                cfg.STACK_NAME,
                config_path=str(config_base),
                overlays=overlay_status,
            )
        else:
            log.info("Bootstrap complete.")

        return 0
