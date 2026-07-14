#!/usr/bin/env python3
"""Homelab deploy orchestrator.

Owns:
  - stack-level validation before deploy
  - running per-stack bootstrap scripts
  - creating ~/STACK symlinks and shell helper functions
  - docker compose up -d for selected stacks

Does NOT own:
  - role-specific provisioning details (kept in each stack's stack_config.py)
  - generating per-stack .env files from .env.example

Usage:
  python3 deploy.py core
  python3 deploy.py media --non-interactive
  python3 deploy.py all --default core
  python3 deploy.py core -v
"""

from __future__ import annotations

import argparse
import importlib
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
from scripts.homelab_bootstrap import build_compose_command
from scripts.homelab_logging import StepTracker


# ---------------------------------------------------------------------------
# Stack config registry
# ---------------------------------------------------------------------------


def _load_stack_config(stack: str):  # noqa: ANN202
    """Dynamically import a stack's stack_config module."""
    return importlib.import_module(f"docker_compose.{stack}.stack_config")


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
            "  - Use --init-env to copy .env.example to .env when missing."
        ),
    )
    parser.add_argument("stacks", nargs="*", help="Stack name(s) or 'all'.")
    parser.add_argument(
        "--force", action="store_true",
        help="Continue when overridable validation fails.",
    )
    parser.add_argument(
        "--non-interactive", "-y", action="store_true",
        help="Pass non-interactive mode to bootstrap scripts.",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Show detailed progress within each step.",
    )
    parser.add_argument(
        "--default", metavar="STACK",
        help="Set default 'stack' helper target.",
    )
    parser.add_argument(
        "--init-env", action="store_true",
        help="Copy .env.example to .env when missing.",
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


_OBSERVABILITY_VARS = ["LOKI_URL", "PROMETHEUS_URL"]


def _validate_stack_env(stack: str, config) -> list[str]:  # noqa: ANN001
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
    required = list(config.REQUIRED_VARS)

    if env.get("ENABLE_OBSERVABILITY", "1") == "1":
        required += _OBSERVABILITY_VARS

    for var in required:
        val = env.get(var, "")
        if not val:
            errors.append(f"Required var {var} is unset in {env_file}.")
        elif is_placeholder(val):
            errors.append(f"Required var {var} is placeholder in {env_file}.")
    return errors


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
    verbose: bool,
) -> bool:
    bootstrap_py = sdir / "bootstrap.py"
    if not bootstrap_py.is_file():
        return True

    cmd = [sys.executable, str(bootstrap_py)]
    args: list[str] = []
    if mode == "update" or non_interactive:
        args.append("--non-interactive")
    if force:
        args.append("--force")
    if verbose:
        args.append("--verbose")

    env = os.environ.copy()
    env["VM_ROLE"] = stack
    if mode == "update" or non_interactive:
        env["HOMELAB_DEPLOY"] = "1"

    result = subprocess.run(cmd + args, env=env)
    if result.returncode != 0:
        log.error(f"Bootstrap failed for {stack}. Fix issues and re-run deploy.")
        return False
    return True


# ---------------------------------------------------------------------------
# Shell helpers (bash functions for the user's shell)
# ---------------------------------------------------------------------------


def _build_shell_helper(stack: str, helper_dir: str, config) -> str:  # noqa: ANN001
    """Generate a shell function for a stack using its overlay config."""
    overlays = getattr(config, "COMPOSE_OVERLAYS", [])
    lines = [f'{stack}() {{', f'  local dir="{helper_dir}"']

    if stack == "media":
        lines += [
            '  local arg1="${1:-}"',
            '  if [[ "$arg1" = "boot" ]] || [[ "$arg1" = "bootstrap" ]]; then',
            '    local files="-f $dir/compose.yml"',
            '    [[ -f "$dir/.env" ]] && source "$dir/.env" 2>/dev/null',
            '    [[ "${ENABLE_RECYCLARR:-0}" = "1" ]] && files="$files -f $dir/compose.recyclarr.yml"',
            '    (cd "$dir" && python3 scripts/setup_media_apps.py)',
            '    [[ "${ENABLE_RECYCLARR:-0}" = "1" ]] && (cd "$dir" && docker compose $files exec recyclarr recyclarr sync)',
            '    return',
            '  fi',
        ]

    lines += [
        '  local compose_files="-f $dir/compose.yml"',
        '  if [[ -f "$dir/.env" ]]; then',
        '    source "$dir/.env" 2>/dev/null',
    ]

    for env_var, filename, default, _label in overlays:
        if default == "1":
            lines.append(
                f'    [[ "${{{env_var}:-1}}" = "1" ]] && [[ -f "$dir/{filename}" ]] '
                f'&& compose_files="$compose_files -f $dir/{filename}"'
            )
        else:
            lines.append(
                f'    [[ "${{{env_var}:-0}}" = "1" ]] && '
                f'[[ -f "$dir/{filename}" ]] && '
                f'compose_files="$compose_files -f $dir/{filename}"'
            )

    lines += [
        '  fi',
        '  (cd "$dir" && docker compose $compose_files "$@")',
        '}',
    ]
    return "\n".join(lines)


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
        try:
            config = _load_stack_config(name)
        except (ImportError, ModuleNotFoundError):
            config = None

        if config is not None:
            lines.append(_build_shell_helper(name, helper_dir, config))
        else:
            lines.append(
                f'{name}() {{\n'
                f'  local dir="{helper_dir}"\n'
                f'  local compose_files="-f $dir/compose.yml"\n'
                f'  if [[ -f "$dir/.env" ]]; then\n'
                f'    source "$dir/.env" 2>/dev/null\n'
                f'    [[ "${{ENABLE_OBSERVABILITY:-1}}" = "1" ]] && [[ -f "$dir/compose.observability.yml" ]] && compose_files="$compose_files -f $dir/compose.observability.yml"\n'
                f'  fi\n'
                f'  (cd "$dir" && docker compose $compose_files "$@")\n'
                f'}}'
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
    config,  # noqa: ANN001
    *,
    real_user: str,
    real_home: Path,
    state_dir: Path,
    installed_dir: Path,
    default_file: Path,
    args: argparse.Namespace,
) -> tuple[bool, list[str]]:
    sdir = _stack_dir(stack)
    env_file = sdir / ".env"
    link_path = real_home / stack

    mode = "update" if (installed_dir / stack).is_file() else "install"

    total_steps = 4 + (1 if hasattr(config, "post_deploy") else 0)
    tracker = StepTracker(total_steps, verbose=args.verbose)

    # Step 1: Validate env
    with tracker.step("Validating environment"):
        if not env_file.is_file():
            tracker.fail(f"No .env found for {stack}")
            return False, []

        errors = _validate_stack_env(stack, config)
        if errors:
            if args.force:
                tracker.warn(f"Validation issues for {stack} (continuing with --force)")
            else:
                for msg in errors:
                    tracker.fail(msg)
                return False, []
        tracker.success("Environment validated")

    # Step 2: Bootstrap
    with tracker.step("Running bootstrap"):
        if not _run_bootstrap(
            stack, sdir, mode,
            force=args.force,
            non_interactive=args.non_interactive,
            verbose=args.verbose,
        ):
            tracker.fail("Bootstrap failed")
            return False, []
        tracker.success("Bootstrap complete")

    # Step 3: Symlinks and state
    with tracker.step("Setting up workspace"):
        installed_dir.mkdir(parents=True, exist_ok=True)
        if link_path.exists() and not link_path.is_symlink():
            if link_path.is_dir():
                link_path.rmdir()
            else:
                link_path.unlink()
        elif link_path.is_symlink():
            link_path.unlink()
        link_path.symlink_to(sdir)
        (installed_dir / stack).touch()

        if not default_file.is_file() or not default_file.read_text().strip():
            default_file.parent.mkdir(parents=True, exist_ok=True)
            default_file.write_text(stack + "\n")
        elif args.default and args.default == stack:
            default_file.write_text(stack + "\n")

        tracker.success("Symlinks and state updated")

    # Step 4: Compose up
    with tracker.step(f"Starting {stack} stack"):
        env = load_env(env_file)
        compose_files = build_compose_command(sdir, config.COMPOSE_OVERLAYS, env)
        result = subprocess.run(
            ["docker", "compose"] + compose_files + ["up", "-d", "--wait"],
            cwd=sdir,
        )
        if result.returncode != 0:
            tracker.fail("docker compose up failed")
            return False, []
        tracker.success("Stack started")

    # Step 5: Post-deploy hooks
    if hasattr(config, "post_deploy"):
        with tracker.step("Post-deploy setup"):
            env = load_env(env_file)
            config_base_str = env.get("CONFIG_ROOT", "./config")
            from scripts.homelab_common import resolve_config_base
            config_base = resolve_config_base(config_base_str, sdir)
            config.post_deploy(env, config_base, tracker)
        # post_deploy() records failures via tracker.fail() rather than raising, so
        # nothing above this point would otherwise notice a failed post-deploy step —
        # deploy.py would print its summary and exit 0 even though setup genuinely
        # failed. Check explicitly and propagate.
        #
        # The stack itself is up at this point (compose up --wait succeeded) — only
        # post-deploy *configuration* failed. Write the shell helpers before bailing
        # so the operator still gets the `<stack>` helper for the VM they now have to
        # go debug on; returning early here would leave them without it.
        if tracker.failed:
            _write_stack_functions(installed_dir, real_home, default_file)
            return False, []

    # Collect post-deploy actions to surface in the deploy summary
    actions: list[str] = list(getattr(config, "POST_DEPLOY_ACTIONS", []))
    actions.extend(tracker._actions)

    # Shell helpers
    _write_stack_functions(installed_dir, real_home, default_file)

    return True, actions


# ---------------------------------------------------------------------------
# Deploy summary
# ---------------------------------------------------------------------------


def _print_deploy_summary(
    stacks: list[str],
    all_actions: list[tuple[str, list[str]]] | None = None,
) -> None:
    """Print a consolidated summary of deployed stacks and their overlays."""
    log.info("")
    log.info("--- Deploy summary ---")
    for stack in stacks:
        env = load_env(_stack_dir(stack) / ".env")
        try:
            config = _load_stack_config(stack)
            overlays = getattr(config, "COMPOSE_OVERLAYS", [])
        except (ImportError, ModuleNotFoundError):
            overlays = []

        enabled: list[str] = []
        disabled: list[str] = []
        for env_var, _file, default, label in overlays:
            if env.get(env_var, default) == "1":
                enabled.append(label)
            else:
                disabled.append(label)

        log.info(f"  {stack}:")
        if enabled:
            log.info(f"    Enabled:  {', '.join(enabled)}")
        if disabled:
            log.info(f"    Disabled: {', '.join(disabled)}")

    if all_actions:
        log.info("")
        log.info("\u26a0 Action required:")
        for stack, actions in all_actions:
            for action in actions:
                for i, line in enumerate(action.splitlines()):
                    prefix = f"  [{stack}] " if i == 0 else "    "
                    log.info(f"{prefix}{line}")

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
    args = parse_args(argv)
    setup_logging(verbose=args.verbose)

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

    if args.init_env:
        for stack in stacks:
            sdir = _stack_dir(stack)
            env_file = sdir / ".env"
            if env_file.is_file():
                continue
            # Prefer .env.staged (auto-detected values from setup_env.py,
            # e.g. AUTHENTIK_SECRET_KEY, detected LAN IP) over the bare
            # .env.example template, if setup_env.py has been run.
            env_staged = sdir / ".env.staged"
            env_example = sdir / ".env.example"
            if env_staged.is_file():
                shutil.copy2(env_staged, env_file)
                log.info(f"Copied .env.staged to .env for {stack}.")
            elif env_example.is_file():
                shutil.copy2(env_example, env_file)
                log.info(f"Copied .env.example to .env for {stack}.")

    # Load configs and validate up-front when deploying multiple stacks
    configs: dict[str, object] = {}
    for stack in stacks:
        if not (_stack_dir(stack) / "compose.yml").is_file():
            log.error(f"No such stack: {stack} (missing docker_compose/{stack}/compose.yml).")
            raise SystemExit(1)
        configs[stack] = _load_stack_config(stack)

    if len(stacks) > 1 and not args.force:
        all_errors: dict[str, list[str]] = {}
        for stack in stacks:
            errs = _validate_stack_env(stack, configs[stack])
            if errs:
                all_errors[stack] = errs
        if all_errors:
            for stack, errs in all_errors.items():
                log.error(f"Required .env changes for {stack}:")
                for msg in errs:
                    log.error(f"  - {msg}")
            log.error("Fix the required .env changes above, then re-run deploy.")
            raise SystemExit(1)

    deployed: list[str] = []
    all_actions: list[tuple[str, list[str]]] = []
    for stack in stacks:
        ok, actions = _deploy_one(
            stack,
            configs[stack],
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
        if actions:
            all_actions.append((stack, actions))

    _ensure_shell_rc(real_user, real_home)
    _print_deploy_summary(deployed, all_actions)


if __name__ == "__main__":
    main()
