#!/usr/bin/env python3
"""Media VM — first-run bootstrap (opinionated, idempotent).

Invoked by deploy.py or run manually:
  cd docker_compose/media && sudo python3 bootstrap.py

Phases:
  0  safety + args
  1  NFS (optional, interactive only)
  2  .env check + config dirs + guardrails

Does NOT install packages, create .env, or start the stack.
Deploy owns symlinks, stack-functions, and "up -d".
Use --non-interactive when invoked by deploy.
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.homelab_common import (
    get_real_user,
    load_env,
    log,
    resolve_config_base,
    setup_logging,
    setup_observability_config,
)


# ---------------------------------------------------------------------------
# Phase 0 helpers
# ---------------------------------------------------------------------------


def _reexec_as_root() -> None:
    """Re-exec this script via sudo if not already root."""
    if os.geteuid() == 0:
        return

    extra_args: list[str] = []
    if os.environ.get("HOMELAB_DEPLOY"):
        extra_args.append("--non-interactive")

    cmd = ["sudo"]
    if os.environ.get("HOMELAB_DEPLOY"):
        cmd += [f"HOMELAB_DEPLOY={os.environ['HOMELAB_DEPLOY']}"]
    cmd += [sys.executable, str(Path(__file__).resolve())]
    cmd += extra_args + sys.argv[1:]
    os.execvp(cmd[0], cmd)


def _check_os() -> None:
    """Verify we are running on Debian/Ubuntu."""
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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Media VM bootstrap (idempotent)."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip overridable guardrails.",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Skip prompts (used by deploy).",
    )
    args = parser.parse_args(argv)
    if os.environ.get("HOMELAB_DEPLOY"):
        args.non_interactive = True
    return args


# ---------------------------------------------------------------------------
# Phase 1: NFS mount (optional, interactive only)
# ---------------------------------------------------------------------------

DEFAULT_MOUNT = "/mnt/media"


def phase_nfs(real_user: str) -> None:
    """Interactively configure an NFS fstab entry (skipped in deploy)."""
    if not sys.stdin.isatty():
        return

    answer = input("Configure NFS mount for media? [y/N] ").strip().lower()
    if answer not in ("y", "yes"):
        return

    nas_host = input("NAS hostname or IP: ").strip()
    export_path = input(
        "Export path on NAS (e.g. /volume1/media): "
    ).strip()
    mount_path = (
        input(f"Local mount path [{DEFAULT_MOUNT}]: ").strip()
        or DEFAULT_MOUNT
    )
    ro_answer = input("Mount read-only? [y/N] ").strip().lower()
    rw = "ro" if ro_answer in ("y", "yes") else "rw"

    if not nas_host or not export_path:
        log.info("Skipping NFS (missing NAS host or export path).")
        return

    fstab_spec = f"{nas_host}:{export_path}"
    fstab_line = (
        f"{fstab_spec}\t{mount_path}\tnfs\t"
        f"nofail,_netdev,x-systemd.automount,timeout=10,vers=4,{rw}\t0\t0"
    )

    fstab = Path("/etc/fstab")
    if fstab.is_file() and mount_path in fstab.read_text():
        log.info(f"Mount for {mount_path} already in /etc/fstab; skipping.")
    else:
        log.info(
            f"Adding to /etc/fstab: {fstab_spec} -> {mount_path} "
            "(nofail, automount)"
        )
        with fstab.open("a") as f:
            f.write(fstab_line + "\n")

    mp = Path(mount_path)
    mp.mkdir(parents=True, exist_ok=True)
    try:
        shutil.chown(mp, user=real_user)
    except (PermissionError, LookupError):
        pass

    subprocess.run(["systemctl", "daemon-reload"], check=False)
    result = subprocess.run(["mount", "-a"], capture_output=True)
    if result.returncode != 0:
        log.warning(
            f"mount -a failed (e.g. NAS unreachable). Boot will not hang "
            f"(nofail); fix and run: mount {mount_path}"
        )
    else:
        log.info(f"Mounted {mount_path}")


# ---------------------------------------------------------------------------
# Phase 2: .env, guardrails, config dirs
# ---------------------------------------------------------------------------


def _vpn_guardrail(compose_file: Path) -> bool:
    """Return True if compose has a vpn service and qbittorrent routes through it."""
    if not compose_file.is_file():
        return True
    content = compose_file.read_text()

    if not re.search(r"^\s*vpn:", content, re.MULTILINE):
        log.warning(
            "compose.yml has no 'vpn' service. "
            "Torrent traffic must not have direct egress."
        )
        return False

    qbt = re.search(r"^\s*qbittorrent:", content, re.MULTILINE)
    if qbt:
        after = content[qbt.end() :]
        chunk = "\n".join(after.splitlines()[:25])
        if not re.search(r"network_mode:\s*service:vpn", chunk):
            log.warning(
                "qbittorrent does not use network_mode: service:vpn. "
                "Torrent traffic must route through VPN."
            )
            return False
    return True


def _single_root_guardrail(compose_file: Path) -> bool:
    """Return True if qbittorrent/sonarr/radarr use the single /data mapping."""
    if not compose_file.is_file():
        return True
    content = compose_file.read_text()
    needle = "${MEDIA_ROOT:-/mnt/media}:/data"

    for svc in ("qbittorrent", "sonarr", "radarr"):
        svc_match = re.search(
            rf"^(\s*){re.escape(svc)}:", content, re.MULTILINE
        )
        if not svc_match:
            continue
        indent = len(svc_match.group(1))
        rest = content[svc_match.end() :]
        end = re.search(
            rf"^\s{{0,{indent}}}[a-zA-Z0-9_-]+:", rest, re.MULTILINE
        )
        section = rest[: end.start()] if end else rest
        if needle not in section:
            log.warning(
                f"{svc} is not using the single-root /data mapping "
                f"({needle})."
            )
            return False
    return True


def _env_path_display(env_file: Path) -> str:
    """Return the .env path relative to current working directory (e.g. deploy vs bootstrap)."""
    try:
        return str(env_file.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(env_file.resolve())


def _ask_continue(non_interactive: bool) -> None:
    """Prompt the user to confirm; exit if declined."""
    if non_interactive:
        log.error(
            "Re-run with --force to skip this check, or fix compose.yml."
        )
        raise SystemExit(1)
    answer = input("Type 'yes' to continue anyway (not recommended): ")
    if answer.strip() != "yes":
        log.error(
            "Exiting. Fix compose.yml or re-run with --force to skip."
        )
        raise SystemExit(1)


def validate_env(env: dict[str, str], real_user: str, env_file: Path) -> None:
    """Validate required .env variables for the media stack."""
    env_loc = _env_path_display(env_file)
    if not env.get("OPENVPN_USER") or not env.get("OPENVPN_PASSWORD"):
        log.error(
            "Set OPENVPN_USER and OPENVPN_PASSWORD in .env (required for VPN)."
        )
        log.error(f"Update: {env_loc}")
        raise SystemExit(1)

    media_root_str = env.get("MEDIA_ROOT", "")
    if not media_root_str:
        log.error("Set MEDIA_ROOT in .env (example: /mnt/media).")
        log.error(f"Update: {env_loc}")
        raise SystemExit(1)

    media_root = Path(media_root_str)
    if not media_root.is_dir():
        # Local testing: create MEDIA_ROOT and required subdirs so bootstrap
        # works without NFS. In prod, mount NFS at this path (mount replaces dir).
        required_subdirs = ("downloads", "library")
        try:
            media_root.mkdir(parents=True, exist_ok=True)
            for sub in required_subdirs:
                (media_root / sub).mkdir(parents=True, exist_ok=True)
            try:
                shutil.chown(media_root, user=real_user)
                for sub in required_subdirs:
                    shutil.chown(media_root / sub, user=real_user)
            except (PermissionError, LookupError):
                pass
            log.info(
                f"Created MEDIA_ROOT locally: {media_root} (with "
                f"{', '.join(required_subdirs)}). For NFS in prod, mount "
                "at this path before starting the stack."
            )
        except OSError as e:
            log.error(
                f"MEDIA_ROOT does not exist and could not be created: "
                f"{media_root}\n{e}\nCreate/mount it first, then re-run bootstrap."
            )
            log.error(f"Update: {env_loc}")
            raise SystemExit(1)

    result = subprocess.run(
        ["sudo", "-u", real_user, "test", "-w", str(media_root)],
        capture_output=True,
    )
    if result.returncode != 0:
        log.warning(
            f"{real_user} cannot write to MEDIA_ROOT ({media_root}). "
            "Imports or moves may fail."
        )

    result = subprocess.run(
        ["mountpoint", "-q", str(media_root)], capture_output=True
    )
    if result.returncode != 0:
        log.info(
            f"{media_root} is not a mount point. If you use NFS, "
            "ensure it is mounted before starting the stack."
        )

    for subdir in ("downloads", "library"):
        if not (media_root / subdir).is_dir():
            log.error(
                f"Expected directory missing: {media_root / subdir}\n"
                "Chapter 2c layout requires both downloads/ and library/ "
                "under MEDIA_ROOT."
            )
            log.error(f"Update: {env_loc}")
            raise SystemExit(1)


def ensure_config_directories(
    config_base: Path,
    env: dict[str, str],
    real_user: str,
) -> None:
    """Create config dirs for base stack and enabled overlays."""
    for d in ("qbittorrent", "sonarr", "radarr", "prowlarr", "flaresolverr"):
        (config_base / d).mkdir(parents=True, exist_ok=True)

    overlay_dirs: dict[str, list[str]] = {
        "ENABLE_BUILDARR_RECYCLARR": ["buildarr", "recyclarr"],
        "ENABLE_CLEANUPARR": ["cleanuparr"],
        "ENABLE_SABNZBD": ["sabnzbd"],
        "ENABLE_BAZARR": ["bazarr"],
        "ENABLE_NTFY": ["ntfy", "ntfy/cache"],
    }
    for var, dirs in overlay_dirs.items():
        if env.get(var, "0") == "1":
            for d in dirs:
                (config_base / d).mkdir(parents=True, exist_ok=True)

    try:
        shutil.chown(config_base, user=real_user)
        for root, dirnames, _filenames in os.walk(config_base):
            for d in dirnames:
                shutil.chown(Path(root) / d, user=real_user)
    except (PermissionError, LookupError):
        pass

    log.info(
        f"Config directories created under {config_base} "
        "(base + enabled overlays)."
    )


def _generate_api_key() -> str:
    """Generate a 32-char hex API key matching the *arr format."""
    return secrets.token_hex(16)


# Starr apps (Sonarr, Radarr, Prowlarr) require authentication even behind a
# reverse proxy.  Pre-seeding config.xml with External auth tells the app to
# delegate authentication to the proxy (Authentik forward auth via Caddy).
# DisabledForLocalAddresses lets inter-container API calls (e.g. Prowlarr →
# Sonarr) work without credentials.
#
# Reference: https://integrations.goauthentik.io/media/sonarr/

_ARR_CONFIG_XML = """\
<Config>
  <LogLevel>info</LogLevel>
  <AuthenticationMethod>{auth_method}</AuthenticationMethod>
  <AuthenticationRequired>DisabledForLocalAddresses</AuthenticationRequired>
  <ApiKey>{api_key}</ApiKey>
  <UrlBase></UrlBase>
  <InstanceName>{instance_name}</InstanceName>
</Config>
"""

_ARR_APPS = ("sonarr", "radarr", "prowlarr")


def seed_arr_configs(
    config_base: Path,
    env: dict[str, str],
    real_user: str,
) -> None:
    """Pre-seed config.xml for *arr apps so they start without auth dialogs.

    Only writes config.xml if it does not already exist.  The auth method
    defaults to External (for use behind Authentik/Caddy forward auth).
    Set ARR_AUTH_METHOD=Forms in .env to use app-level Forms login instead.
    """
    auth_method = env.get("ARR_AUTH_METHOD", "External")
    seeded: list[str] = []

    for app in _ARR_APPS:
        conf_dir = config_base / app
        conf_file = conf_dir / "config.xml"
        if conf_file.is_file():
            continue

        conf_dir.mkdir(parents=True, exist_ok=True)
        api_key = _generate_api_key()
        conf_file.write_text(
            _ARR_CONFIG_XML.format(
                auth_method=auth_method,
                api_key=api_key,
                instance_name=app.title(),
            )
        )
        try:
            shutil.chown(conf_file, user=real_user)
        except (PermissionError, LookupError):
            pass
        seeded.append(app)

    if seeded:
        log.info(
            "Pre-seeded config.xml for %s (auth=%s, ApiKey generated). "
            "Apps will start without the authentication setup dialog.",
            ", ".join(seeded),
            auth_method,
        )


def copy_example_configs(
    config_base: Path,
    env: dict[str, str],
    real_user: str,
) -> None:
    """Copy example Buildarr/Recyclarr configs when enabled and missing."""
    if env.get("ENABLE_BUILDARR_RECYCLARR", "0") != "1":
        return

    copies = [
        (
            SCRIPT_DIR / "buildarr.example.yml",
            config_base / "buildarr" / "buildarr.yml",
        ),
        (
            SCRIPT_DIR / "recyclarr.example.yml",
            config_base / "recyclarr" / "recyclarr.yml",
        ),
        (
            SCRIPT_DIR / "recyclarr.secrets.example.yml",
            config_base / "recyclarr" / "secrets.yml",
        ),
    ]
    for src, dst in copies:
        if not dst.is_file() and src.is_file():
            shutil.copy2(src, dst)
            log.info(f"Created {dst} from {src.name} (edit API keys/settings).")

    for d in ("buildarr", "recyclarr"):
        try:
            shutil.chown(config_base / d, user=real_user)
        except (PermissionError, LookupError):
            pass


def print_summary(env: dict[str, str]) -> None:
    """Print a clean stack-status block at the end."""
    overlays = {
        "Buildarr + Recyclarr": env.get("ENABLE_BUILDARR_RECYCLARR", "0"),
        "Cleanuparr": env.get("ENABLE_CLEANUPARR", "0"),
        "SABnzbd": env.get("ENABLE_SABNZBD", "0"),
        "Bazarr": env.get("ENABLE_BAZARR", "0"),
        "ntfy": env.get("ENABLE_NTFY", "0"),
    }

    log.info("")
    log.info("--- Stack summary ---")
    log.info(
        "Base: VPN (Gluetun), qBittorrent, Sonarr, Radarr, Prowlarr, "
        "FlareSolverr"
    )
    for name, val in overlays.items():
        status = "on" if val == "1" else "off"
        log.info(f"  {name + ':':<25s} {status}")
    log.info("")
    log.info(
        "Bootstrap done. Deploy will start the stack and set up "
        "shell helpers (media, stack)."
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    setup_logging()
    _reexec_as_root()

    args = parse_args(argv)
    _check_os()

    real_user, real_home = get_real_user()

    deploy_mode = bool(os.environ.get("HOMELAB_DEPLOY"))

    log.info("--- Media VM bootstrap ---")
    if not deploy_mode:
        log.info("This script will (idempotent, safe to re-run):")
        log.info(
            "  1. Optionally add NFS mount to /etc/fstab "
            "(interactive only; skipped with --non-interactive)"
        )
        log.info(
            "  2. Require .env; create config dirs under CONFIG_ROOT "
            "(base + enabled overlays)"
        )
        log.info(
            "  3. Run guardrails (e.g. VPN check; use --force to skip "
            "overridable checks)"
        )
        log.info("")

    # Phase 1: NFS
    if not args.non_interactive:
        phase_nfs(real_user)

    # Phase 2: .env, guardrails, config
    env_file = SCRIPT_DIR / ".env"
    if not env_file.is_file():
        log.error(
            "No .env found. Copy .env.example to .env, set required vars, "
            "then re-run bootstrap or deploy."
        )
        log.error(f"Create/edit: {_env_path_display(env_file)}")
        raise SystemExit(1)

    env = load_env(env_file)
    validate_env(env, real_user, env_file)

    compose_file = SCRIPT_DIR / "compose.yml"

    if not args.force:
        if not _single_root_guardrail(compose_file):
            log.error(
                "Bootstrap expects qbittorrent/sonarr/radarr to mount "
                "MEDIA_ROOT at /data."
            )
            _ask_continue(args.non_interactive)

        if not _vpn_guardrail(compose_file):
            log.error(
                "Bootstrap expects a dedicated VPN service and qbittorrent "
                "using network_mode: service:vpn."
            )
            _ask_continue(args.non_interactive)

    config_base = resolve_config_base(
        env.get("CONFIG_ROOT", "./config"), SCRIPT_DIR
    )
    ensure_config_directories(config_base, env, real_user)
    seed_arr_configs(config_base, env, real_user)
    copy_example_configs(config_base, env, real_user)

    if env.get("ENABLE_OBSERVABILITY", "1") == "1":
        setup_observability_config(config_base, env, SCRIPT_DIR)

    if not deploy_mode:
        print_summary(env)
    else:
        log.info("Bootstrap complete.")


if __name__ == "__main__":
    main()
