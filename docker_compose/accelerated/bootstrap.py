#!/usr/bin/env python3
"""Accelerated VM — Plex + Immich bootstrap (idempotent).

Invoked by deploy.py or run manually:
  cd docker_compose/accelerated && python3 bootstrap.py
  cd docker_compose/accelerated && python3 bootstrap.py --up

Phases:
  0  logging + args
  1  .env + guardrails (paths, DB password)
  2  GPU guardrail (/dev/dri + compose devices stanza)
  3  config directories + ownership
  4  observability wiring (Alloy/node_exporter/cAdvisor) when enabled
  5  compose validation
  6  optional bring-up (--up)

Does NOT:
  - create .env from .env.example
  - edit fstab or manage NFS (future extension if needed)
  - start the stack unless --up is passed
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.homelab_common import (  # type: ignore[import]
    get_real_user,
    is_placeholder,
    load_env,
    log,
    resolve_config_base,
    setup_logging,
    setup_observability_config,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Accelerated VM bootstrap (Plex + Immich, idempotent)."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip overridable guardrails (DB password, GPU checks, etc.).",
    )
    parser.add_argument(
        "--non-interactive",
        "-y",
        action="store_true",
        help="Skip prompts (used by deploy).",
    )
    parser.add_argument(
        "--up",
        action="store_true",
        help="Bring stack up after successful bootstrap (docker compose up -d).",
    )
    args = parser.parse_args(argv)
    if os.environ.get("HOMELAB_DEPLOY"):
        args.non_interactive = True
    return args


def _env_path_display(env_file: Path) -> str:
    """Return the .env path relative to CWD when possible (nice error messages)."""
    try:
        return str(env_file.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(env_file.resolve())


def _load_env_or_die() -> tuple[dict[str, str], Path]:
    env_file = SCRIPT_DIR / ".env"
    if not env_file.is_file():
        log.error(
            "No .env found for accelerated stack. "
            "Copy .env.example to .env, fill required values, then re-run bootstrap."
        )
        log.error(f"Create/edit: {_env_path_display(env_file)}")
        raise SystemExit(1)
    env = load_env(env_file)
    return env, env_file


def _validate_paths(env: dict[str, str], real_user: str, env_file: Path) -> None:
    env_loc = _env_path_display(env_file)

    media_root = env.get("MEDIA_LIBRARY_ROOT", "").strip()
    photos_root = env.get("IMMICH_UPLOAD_ROOT", "").strip()
    db_root = env.get("IMMICH_DB_ROOT", "").strip()

    missing: list[str] = []
    if not media_root:
        missing.append("MEDIA_LIBRARY_ROOT")
    if not photos_root:
        missing.append("IMMICH_UPLOAD_ROOT")
    if not db_root:
        missing.append("IMMICH_DB_ROOT")
    if missing:
        for var in missing:
            log.error(f"Required var {var} is unset in {env_loc}.")
        raise SystemExit(1)

    def _ensure_dir(path_str: str, label: string) -> Path:  # type: ignore[name-defined]
        p = Path(path_str)
        if not p.is_dir():
            try:
                p.mkdir(parents=True, exist_ok=True)
                shutil.chown(p, user=real_user)
                log.info(f"Created {label} directory: {p}")
            except OSError as e:
                log.error(
                    f"{label} directory does not exist and could not be created: {p}\n"
                    f"{e}\nCreate/mount it first, then re-run bootstrap."
                )
                log.error(f"Update: {env_loc}")
                raise SystemExit(1)
        return p

    # MEDIA_LIBRARY_ROOT and IMMICH_UPLOAD_ROOT may be NFS mounts; we only ensure they exist.
    _ensure_dir(media_root, "MEDIA_LIBRARY_ROOT")
    _ensure_dir(photos_root, "IMMICH_UPLOAD_ROOT")

    # IMMICH_DB_ROOT must be on local disk; best-effort guardrail.
    db_path = Path(db_root)
    if not db_path.is_dir():
        try:
            db_path.mkdir(parents=True, exist_ok=True)
            shutil.chown(db_path, user=real_user)
            log.info(f"Created IMMICH_DB_ROOT locally: {db_path}")
        except OSError as e:
            log.error(
                f"IMMICH_DB_ROOT does not exist and could not be created: {db_path}\n"
                f"{e}\nCreate it on local disk (not NFS) and re-run bootstrap."
            )
            log.error(f"Update: {env_loc}")
            raise SystemExit(1)

    try:
        # If mountpoint -q returns 0, warn that this may be an NFS or remote FS.
        result = subprocess.run(
            ["mountpoint", "-q", str(db_path)],
            capture_output=True,
        )
        if result.returncode == 0:
            log.warning(
                f"IMMICH_DB_ROOT appears to be a mount point ({db_path}). "
                "Immich docs recommend local disk for Postgres; "
                "network shares are not supported for the database."
            )
    except OSError:
        # mountpoint may not exist in all environments; ignore.
        pass


def _validate_db_password(env: dict[str, str], env_file: Path, *, force: bool) -> None:
    env_loc = _env_path_display(env_file)
    pwd = env.get("DB_PASSWORD", "").strip()
    if pwd and not is_placeholder(pwd) and pwd.lower() != "postgres":
        return
    if force:
        log.warning(
            "DB_PASSWORD is missing/placeholder. Continuing due to --force "
            "(for testing only; do not use in production)."
        )
        return
    log.error(
        "DB_PASSWORD is missing, 'postgres', or placeholder in .env.\n"
        "Set a strong A–Z / a–z / 0–9 value (no special characters) per Immich docs."
    )
    log.error(f"Update: {env_loc}")
    raise SystemExit(1)


def _gpu_guardrail(env: dict[str, str], *, force: bool, non_interactive: bool) -> None:
    """Best-effort GPU guardrail: /dev/dri and compose devices stanza."""
    dev_dri = Path("/dev/dri")
    if not dev_dri.exists():
        msg = (
            "/dev/dri is missing on the host. GPU passthrough may not be configured.\n"
            "Plex and Immich can run in CPU-only mode, but hardware acceleration "
            "will not work until /dev/dri is present."
        )
        if force:
            log.warning(msg + " Continuing due to --force.")
            return
        log.warning(msg)
        if non_interactive:
            raise SystemExit(1)
        answer = input("Type 'yes' to continue without GPU (CPU-only): ")
        if answer.strip() != "yes":
            raise SystemExit(1)

    compose_file = SCRIPT_DIR / "compose.yml"
    if not compose_file.is_file():
        return
    content = compose_file.read_text()
    missing_devices: list[str] = []
    for svc in ("plex", "immich-server"):
        svc_match = re.search(rf"^\s*{svc}:\s*$", content, re.MULTILINE)
        if not svc_match:
            continue
        after = content[svc_match.end() :]
        # Look at the next ~40 lines for a /dev/dri devices stanza.
        chunk = "\n".join(after.splitlines()[:40])
        if "/dev/dri:/dev/dri" not in chunk:
            missing_devices.append(svc)

    if not missing_devices:
        return

    msg = (
        "GPU guardrail: the following services do not declare /dev/dri:/dev/dri "
        f"in compose.yml: {', '.join(missing_devices)}.\n"
        "Hardware acceleration will not be available for these containers."
    )
    if force:
        log.warning(msg + " Continuing due to --force.")
        return
    log.warning(msg)
    if non_interactive:
        raise SystemExit(1)
    answer = input("Type 'yes' to continue anyway: ")
    if answer.strip() != "yes":
        raise SystemExit(1)


def _ensure_config_dirs(config_base: Path, real_user: str) -> None:
    dirs = [
        config_base / "plex",
        config_base / "immich",
        config_base / "immich-postgres",
        config_base / "immich-redis",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
    try:
        shutil.chown(config_base, user=real_user)
        for root, dirnames, _filenames in os.walk(config_base):
            for d in dirnames:
                try:
                    shutil.chown(Path(root) / d, user=real_user)
                except (PermissionError, LookupError):
                    pass
    except (PermissionError, LookupError):
        # Non-fatal; container may fix ownership on first start.
        pass
    log.info(f"Config directories ensured under {config_base}")


def _compose_files(env: dict[str, str]) -> list[Path]:
    """Build list of compose files: base + observability + plex-exporter when enabled."""
    files = [SCRIPT_DIR / "compose.yml"]
    if env.get("ENABLE_OBSERVABILITY", "1") != "1":
        return files
    obs = SCRIPT_DIR / "compose.observability.yml"
    if obs.exists():
        files.append(obs)
    plex_exporter = SCRIPT_DIR / "compose.plex-exporter.yml"
    if plex_exporter.exists():
        files.append(plex_exporter)
    return files


def _validate_compose(env: dict[str, str]) -> None:
    files = _compose_files(env)
    cmd = ["docker", "compose"] + [arg for f in files for arg in ("-f", str(f))] + ["config"]
    result = subprocess.run(cmd, capture_output=True, cwd=SCRIPT_DIR)
    if result.returncode != 0:
        stderr = result.stderr.decode() if result.stderr else ""
        log.error(f"docker compose config validation failed.\n{stderr}")
        raise SystemExit(1)
    log.info("Compose file validates successfully.")


def _bring_up_stack(env: dict[str, str]) -> None:
    log.info("Starting accelerated stack (Plex + Immich)...")
    files = _compose_files(env)
    cmd = ["docker", "compose"] + [arg for f in files for arg in ("-f", str(f))] + ["up", "-d"]
    subprocess.run(cmd, check=True, cwd=SCRIPT_DIR)
    log.info("Accelerated stack started.")


def _print_summary(env: dict[str, str], config_base: Path) -> None:
    media_root = env.get("MEDIA_LIBRARY_ROOT", "/mnt/media/library")
    photos_root = env.get("IMMICH_UPLOAD_ROOT", "/mnt/photos/library")
    db_root = env.get("IMMICH_DB_ROOT", "./config/immich-postgres")

    log.info("")
    log.info("--- Accelerated stack summary ---")
    log.info(f"  Config root:        {config_base}")
    log.info(f"  Media library root: {media_root}")
    log.info(f"  Photos root:        {photos_root}")
    log.info(f"  Immich DB root:     {db_root}")
    log.info("")
    if env.get("ENABLE_OBSERVABILITY", "1") == "1":
        log.info("  Observability:      enabled (node_exporter + cAdvisor + Alloy + Plex exporter)")
    else:
        log.info("  Observability:      disabled (ENABLE_OBSERVABILITY=0)")
    log.info("")
    log.info(
        "Bootstrap done. Deploy will start the stack and set up "
        "shell helpers (accelerated, stack)."
    )


def main(argv: list[str] | None = None) -> None:
    setup_logging()
    args = parse_args(argv)

    log.info("--- Accelerated VM bootstrap ---")

    real_user, real_home = get_real_user()
    log.info(f"Real user: {real_user} (home: {real_home})")

    env, env_file = _load_env_or_die()

    _validate_paths(env, real_user, env_file)
    _validate_db_password(env, env_file, force=args.force)
    _gpu_guardrail(env, force=args.force, non_interactive=args.non_interactive)

    config_base = resolve_config_base(env.get("CONFIG_ROOT", "./config"), SCRIPT_DIR)
    _ensure_config_dirs(config_base, real_user)

    if env.get("ENABLE_OBSERVABILITY", "1") == "1":
        setup_observability_config(config_base, env, SCRIPT_DIR)

    _validate_compose(env)

    deploy_mode = bool(os.environ.get("HOMELAB_DEPLOY"))

    if args.up and not deploy_mode:
        _bring_up_stack(env)
    elif deploy_mode:
        log.info("Bootstrap complete (stack will be started by deploy.py).")
    else:
        log.info(
            "Bootstrap complete. Run 'docker compose up -d' "
            "or use the accelerated alias when ready."
        )

    if not deploy_mode:
        _print_summary(env, config_base)


if __name__ == "__main__":
    main()

