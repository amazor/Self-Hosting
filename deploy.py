#!/usr/bin/env python3
"""Homelab deploy orchestrator.

Owns:
  - stack-level validation before deploy
  - running per-stack bootstrap scripts
  - creating ~/STACK symlinks and shell helper functions
  - docker compose up -d for selected stacks

Does NOT own:
  - role-specific provisioning details (kept in each stack bootstrap.py)
  - generating per-stack .env files from .env.example

Usage:
  python3 deploy.py core
  python3 deploy.py media --non-interactive
  python3 deploy.py all --default core
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

DEPLOY_DIR = Path(__file__).resolve().parent
REPO_ROOT = (
    DEPLOY_DIR
    if (DEPLOY_DIR / "docker_compose").is_dir()
    else DEPLOY_DIR.parent
)
sys.path.insert(0, str(REPO_ROOT))

from scripts.homelab_common import (
    get_real_user,
    get_user_shell,
    is_placeholder,
    load_env,
    log,
    setup_logging,
)

# ---------------------------------------------------------------------------
# Required vars per stack (same as deploy.sh)
# ---------------------------------------------------------------------------

REQUIRED_VARS: dict[str, list[str]] = {
    "core": [
        "AUTHENTIK_SECRET_KEY",
        "AUTHENTIK_POSTGRES_PASSWORD",
        "PUBLIC_BASE_DOMAIN",
        "AUTHENTIK_FQDN",
        "WHOAMI_FQDN",
        "DNS_BIND_IP",
    ],
    "media": [
        "OPENVPN_USER",
        "OPENVPN_PASSWORD",
        "MEDIA_ROOT",
        "CONFIG_ROOT",
    ],
    "monitoring": [
        "GRAFANA_ADMIN_PASSWORD",
    ],
}


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Homelab deploy orchestrator.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Notes:\n"
            "  - Each stack must already have a configured .env file.\n"
            "  - Deploy validates required vars, then runs bootstrap "
            "and compose up -d.\n"
            "  - Use --init-env to copy .env.example to .env when missing "
            "(convenience only;\n"
            "    required vars must still be filled)."
        ),
    )
    parser.add_argument(
        "stacks",
        nargs="*",
        help="Stack name(s) or 'all'.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Continue when overridable validation fails.",
    )
    parser.add_argument(
        "--non-interactive",
        "-y",
        action="store_true",
        help="Pass non-interactive mode to bootstrap scripts.",
    )
    parser.add_argument(
        "--default",
        metavar="STACK",
        help="Set default 'stack' helper target.",
    )
    parser.add_argument(
        "--init-env",
        action="store_true",
        help=(
            "Copy .env.example to .env when missing "
            "(convenience; fill required vars before deploy)."
        ),
    )
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _stack_dir(stack: str) -> Path:
    return REPO_ROOT / "docker_compose" / stack


def _collect_all_stacks() -> list[str]:
    stacks: list[str] = []
    base = REPO_ROOT / "docker_compose"
    for d in sorted(base.iterdir()):
        if d.is_dir() and (d / "compose.yml").is_file():
            stacks.append(d.name)
    return stacks


def _validate_stack_env(stack: str) -> list[str]:
    """Return a list of error messages for the given stack's .env."""
    sdir = _stack_dir(stack)
    env_file = sdir / ".env"
    errors: list[str] = []

    if not env_file.is_file():
        errors.append(
            f"Missing .env for {stack}. "
            f"Create from {sdir}/.env.example, configure it, then re-run."
        )
        return errors

    env = load_env(env_file)
    for var in REQUIRED_VARS.get(stack, []):
        val = env.get(var, "")
        if not val:
            errors.append(f"Required var {var} is unset in {env_file}.")
        elif is_placeholder(val):
            errors.append(
                f"Required var {var} is placeholder/invalid in {env_file} "
                f"(e.g. CHANGE_ME, example.com, 0.0.0.0)."
            )
    return errors


# ---------------------------------------------------------------------------
# Compose file building (mirrors deploy.sh overlay logic)
# ---------------------------------------------------------------------------

_MEDIA_OVERLAYS: dict[str, str] = {
    "ENABLE_BUILDARR_RECYCLARR": "compose.buildarr-recyclarr.yml",
    "ENABLE_CLEANUPARR": "compose.cleanuparr.yml",
    "ENABLE_SABNZBD": "compose.sabnzbd.yml",
    "ENABLE_BAZARR": "compose.bazarr.yml",
    "ENABLE_NTFY": "compose.ntfy.yml",
}


def _build_compose_files(stack: str, sdir: Path) -> list[str]:
    env = load_env(sdir / ".env")
    files = ["-f", str(sdir / "compose.yml")]

    if stack == "core":
        if env.get("ENABLE_DDNS", "0") == "1":
            files += ["-f", str(sdir / "compose.ddclient.yml")]
    elif stack == "media":
        for var, filename in _MEDIA_OVERLAYS.items():
            if env.get(var, "0") == "1":
                files += ["-f", str(sdir / filename)]

    return files


# ---------------------------------------------------------------------------
# Bootstrap invocation
# ---------------------------------------------------------------------------


def _run_bootstrap(
    stack: str,
    sdir: Path,
    mode: str,
    *,
    force: bool,
    non_interactive: bool,
) -> bool:
    bootstrap_py = sdir / "bootstrap.py"
    bootstrap_sh = sdir / "bootstrap.sh"

    if bootstrap_py.is_file():
        cmd = [sys.executable, str(bootstrap_py)]
    elif bootstrap_sh.is_file() and os.access(bootstrap_sh, os.X_OK):
        cmd = [str(bootstrap_sh)]
    else:
        return True

    args: list[str] = []
    if mode == "update" or non_interactive:
        args.append("--non-interactive")
    if force:
        args.append("--force")

    env = os.environ.copy()
    env["VM_ROLE"] = stack  # bootstrap uses this for Alloy/labels (e.g. monitoring, core, media)
    if mode == "update" or non_interactive:
        env["HOMELAB_DEPLOY"] = "1"

    log.info(f"Running bootstrap for {stack}...")
    result = subprocess.run(cmd + args, env=env)
    if result.returncode != 0:
        log.error(
            f"Bootstrap failed for {stack}. Fix issues and re-run deploy."
        )
        return False
    return True


# ---------------------------------------------------------------------------
# Stack-functions.sh generation (bash helpers for the user's shell)
# ---------------------------------------------------------------------------

# %s is substituted with the directory path.
_CORE_HELPER = """\
core() {
  local dir="%s"
  local compose_files="-f $dir/compose.yml"
  if [[ -f "$dir/.env" ]]; then
    source "$dir/.env" 2>/dev/null
    [[ "${ENABLE_DDNS:-0}" = "1" ]] && compose_files="$compose_files -f $dir/compose.ddclient.yml"
  fi
  (cd "$dir" && docker compose $compose_files "$@")
}
"""

_MEDIA_HELPER = """\
media() {
  local dir="%s"
  local arg1="${1:-}"
  if [[ "$arg1" = "boot" ]] || [[ "$arg1" = "bootstrap" ]]; then
    local files="-f $dir/compose.yml"
    [[ -f "$dir/.env" ]] && source "$dir/.env" 2>/dev/null
    [[ "${ENABLE_BUILDARR_RECYCLARR:-0}" = "1" ]] && files="$files -f $dir/compose.buildarr-recyclarr.yml"
    (cd "$dir" && docker compose $files --profile bootstrap run --rm buildarr run) 2>/dev/null || true
    (cd "$dir" && docker compose $files --profile bootstrap run --rm recyclarr sync) 2>/dev/null || true
    return
  fi

  local compose_files="-f $dir/compose.yml"
  if [[ -f "$dir/.env" ]]; then
    source "$dir/.env" 2>/dev/null
    [[ "${ENABLE_BUILDARR_RECYCLARR:-0}" = "1" ]] && compose_files="$compose_files -f $dir/compose.buildarr-recyclarr.yml"
    [[ "${ENABLE_CLEANUPARR:-0}" = "1" ]] && compose_files="$compose_files -f $dir/compose.cleanuparr.yml"
    [[ "${ENABLE_SABNZBD:-0}" = "1" ]] && compose_files="$compose_files -f $dir/compose.sabnzbd.yml"
    [[ "${ENABLE_BAZARR:-0}" = "1" ]] && compose_files="$compose_files -f $dir/compose.bazarr.yml"
    [[ "${ENABLE_NTFY:-0}" = "1" ]] && compose_files="$compose_files -f $dir/compose.ntfy.yml"
  fi
  (cd "$dir" && docker compose $compose_files "$@")
}
"""


def _write_stack_functions(
    installed_dir: Path,
    real_home: Path,
    default_file: Path,
) -> None:
    """Regenerate ~/.bashrc.d/stack-functions.sh from installed markers."""
    bashrc_d = real_home / ".bashrc.d"
    bashrc_d.mkdir(parents=True, exist_ok=True)
    func_file = bashrc_d / "stack-functions.sh"

    default_stack = ""
    if default_file.is_file():
        default_stack = default_file.read_text().strip()

    lines = [
        "# Homelab stack helpers — generated by deploy.py. Do not edit by hand."
    ]

    for marker in sorted(installed_dir.iterdir()):
        if not marker.is_file():
            continue
        name = marker.name
        helper_dir = str(real_home / name)

        if name == "core":
            lines.append(_CORE_HELPER % helper_dir)
        elif name == "media":
            lines.append(_MEDIA_HELPER % helper_dir)
        else:
            lines.append(
                f'{name}() {{ (cd "{helper_dir}" && docker compose '
                f'-f compose.yml "$@"); }}'
            )

    if default_stack:
        lines.append("")
        lines.append(
            f'stack() {{ local d="{default_stack}"; '
            '[ -n "$d" ] && $d "$@"; }'
        )

    func_file.write_text("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Shell RC sourcing
# ---------------------------------------------------------------------------

_RC_MARKER = "# homelab stack-functions"
_RC_SOURCE = (
    '[[ -f ~/.bashrc.d/stack-functions.sh ]] '
    "&& source ~/.bashrc.d/stack-functions.sh"
)


def _ensure_rc_sources(rc_file: Path, label: str) -> None:
    if rc_file.is_file() and _RC_MARKER in rc_file.read_text():
        return

    with rc_file.open("a") as f:
        f.write(f"\n{_RC_MARKER}\n{_RC_SOURCE}\n")
    log.info(f"Added stack helper source line to {label}.")


def _ensure_shell_rc(real_user: str, real_home: Path) -> None:
    shell = get_user_shell(real_user)
    if "zsh" in shell:
        _ensure_rc_sources(real_home / ".zshrc", ".zshrc")
    elif "bash" in shell:
        _ensure_rc_sources(real_home / ".bashrc", ".bashrc")
    else:
        _ensure_rc_sources(real_home / ".bashrc", ".bashrc")
        _ensure_rc_sources(real_home / ".zshrc", ".zshrc")


# ---------------------------------------------------------------------------
# Deploy one stack
# ---------------------------------------------------------------------------


def _deploy_one(
    stack: str,
    *,
    real_user: str,
    real_home: Path,
    state_dir: Path,
    installed_dir: Path,
    default_file: Path,
    args: argparse.Namespace,
) -> bool:
    sdir = _stack_dir(stack)
    env_file = sdir / ".env"
    link_path = real_home / stack

    mode = "update" if (installed_dir / stack).is_file() else "install"
    if mode == "update":
        log.info(
            f"Stack {stack} already installed; updating "
            "(bootstrap, compose up -d, shell helpers)."
        )

    if not env_file.is_file():
        log.error(
            f"No .env found. Create .env from .env.example in {sdir}, "
            "configure it, then re-run deploy."
        )
        return False

    errors = _validate_stack_env(stack)
    if errors:
        if args.force:
            log.warning(
                f"Validation failed for {stack}; continuing with --force."
            )
        else:
            log.error(f"Required .env changes for {stack}:")
            for msg in errors:
                log.error(f"  - {msg}")
            log.error(f"Edit {env_file} then re-run deploy.")
            return False

    if not _run_bootstrap(
        stack,
        sdir,
        mode,
        force=args.force,
        non_interactive=args.non_interactive,
    ):
        return False

    installed_dir.mkdir(parents=True, exist_ok=True)
    if link_path.exists() and not link_path.is_symlink():
        log.warning(
            f"Replacing existing {link_path} (not a symlink) with symlink -> {sdir}"
        )
        if link_path.is_dir():
            link_path.rmdir()
        else:
            link_path.unlink()
    elif link_path.is_symlink():
        link_path.unlink()
    link_path.symlink_to(sdir)
    if not (installed_dir / stack).is_file():
        log.info(f"Created symlink {link_path} -> {sdir}")
    (installed_dir / stack).touch()

    # Set default stack
    if not default_file.is_file() or not default_file.read_text().strip():
        default_file.parent.mkdir(parents=True, exist_ok=True)
        default_file.write_text(stack + "\n")
        log.info(f"Set default stack to {stack}.")
    elif args.default and args.default == stack:
        default_file.write_text(stack + "\n")

    log.info(f"Starting {stack} stack...")
    compose_files = _build_compose_files(stack, sdir)
    result = subprocess.run(
        ["docker", "compose"] + compose_files + ["up", "-d"],
        cwd=sdir,
    )
    if result.returncode != 0:
        log.error(
            f"Compose up failed for {stack}. Fix the error and re-run deploy."
        )
        return False

    _write_stack_functions(installed_dir, real_home, default_file)
    return True


# ---------------------------------------------------------------------------
# Deploy summary
# ---------------------------------------------------------------------------


def _print_deploy_summary(stacks: list[str]) -> None:
    """Print a consolidated summary of deployed stacks and their overlays."""
    log.info("")
    log.info("--- Deploy summary ---")
    for stack in stacks:
        env = load_env(_stack_dir(stack) / ".env")
        enabled: list[str] = []
        disabled: list[str] = []

        if stack == "core":
            for label, var in [("DDNS", "ENABLE_DDNS")]:
                (enabled if env.get(var, "0") == "1" else disabled).append(
                    label
                )
        elif stack == "media":
            overlay_labels = {
                "ENABLE_BUILDARR_RECYCLARR": "Buildarr+Recyclarr",
                "ENABLE_CLEANUPARR": "Cleanuparr",
                "ENABLE_SABNZBD": "SABnzbd",
                "ENABLE_BAZARR": "Bazarr",
                "ENABLE_NTFY": "ntfy",
            }
            for var, label in overlay_labels.items():
                (enabled if env.get(var, "0") == "1" else disabled).append(
                    label
                )

        log.info(f"  {stack}:")
        if enabled:
            log.info(f"    Enabled:  {', '.join(enabled)}")
        if disabled:
            log.info(f"    Disabled: {', '.join(disabled)}")
        if not enabled and not disabled:
            log.info("    (no optional overlays)")

    log.info("")
    log.info(
        "Deploy done. Source your shell rc file (e.g. ~/.bashrc or ~/.zshrc), "
        "or open a new shell."
    )
    helpers = " | ".join(f"{s} ps" for s in stacks)
    log.info(
        f"Then use stack helpers: {helpers}, "
        "or <stack> up -d | logs -f | down."
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    setup_logging()
    args = parse_args(argv)

    raw_stacks = args.stacks or []
    deploy_all = "all" in raw_stacks
    stacks = [s for s in raw_stacks if s != "all"]

    if deploy_all and not stacks:
        stacks = _collect_all_stacks()

    if not stacks:
        log.error("No stack selected.")
        parse_args(["--help"])
        raise SystemExit(1)

    real_user, real_home = get_real_user()
    state_dir = real_home / ".homelab"
    installed_dir = state_dir / "installed"
    default_file = state_dir / "default"

    # --init-env: copy .env.example -> .env where missing
    if args.init_env:
        for stack in stacks:
            sdir = _stack_dir(stack)
            env_file = sdir / ".env"
            env_example = sdir / ".env.example"
            if not env_file.is_file() and env_example.is_file():
                shutil.copy2(env_example, env_file)
                log.info(
                    f"Copied .env.example to .env for {stack} "
                    "(review and fill required vars)."
                )

    # Validate all stacks up-front when deploying multiple
    if len(stacks) > 1:
        all_errors: dict[str, list[str]] = {}
        for stack in stacks:
            errs = _validate_stack_env(stack)
            if errs:
                all_errors[stack] = errs

        if all_errors and not args.force:
            for stack, errs in all_errors.items():
                log.error(f"Required .env changes for {stack}:")
                for msg in errs:
                    log.error(f"  - {msg}")
            log.error(
                "Fix the required .env changes above, then re-run deploy."
            )
            raise SystemExit(1)

    # Verify stacks exist
    for stack in stacks:
        if not (_stack_dir(stack) / "compose.yml").is_file():
            log.error(
                f"No such stack: {stack} "
                f"(missing docker_compose/{stack}/compose.yml)."
            )
            raise SystemExit(1)

    # Deploy each stack
    deployed: list[str] = []
    for stack in stacks:
        ok = _deploy_one(
            stack,
            real_user=real_user,
            real_home=real_home,
            state_dir=state_dir,
            installed_dir=installed_dir,
            default_file=default_file,
            args=args,
        )
        if not ok:
            raise SystemExit(1)
        deployed.append(stack)

    _ensure_shell_rc(real_user, real_home)
    _print_deploy_summary(deployed)


if __name__ == "__main__":
    main()
