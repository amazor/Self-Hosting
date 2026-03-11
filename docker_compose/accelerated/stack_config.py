"""Accelerated VM stack configuration — registry entry for deploy.py and bootstrap.

Defines the accelerated stack's required vars, compose overlays, bootstrap
steps, and post-deploy hooks.  Consumed by BootstrapRunner and deploy.py.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from scripts.homelab_logging import StepTracker

SCRIPT_DIR = Path(__file__).resolve().parent

from scripts.homelab_common import (
    clear_env_var,
    get_real_user,
    is_placeholder,
)

# ---------------------------------------------------------------------------
# Stack identity
# ---------------------------------------------------------------------------

STACK_NAME = "accelerated"
REQUIRES_DOCKER = True

REQUIRED_VARS = [
    "MEDIA_LIBRARY_ROOT",
    "IMMICH_UPLOAD_ROOT",
    "IMMICH_DB_ROOT",
    "DB_PASSWORD",
]

COMPOSE_OVERLAYS: list[tuple[str, str, str, str]] = [
    ("ENABLE_OBSERVABILITY", "compose.observability.yml", "1", "Observability"),
    ("ENABLE_OBSERVABILITY", "compose.plex-exporter.yml", "1", "Plex exporter"),
]

POST_DEPLOY_ACTIONS = [
    "Claim Plex server if this is first run:\n"
    "Open http://<host>:32400/web and sign in with your Plex account",
]


# ---------------------------------------------------------------------------
# Bootstrap step functions
# ---------------------------------------------------------------------------

def _env_path_display(env_file: Path) -> str:
    try:
        return str(env_file.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(env_file.resolve())


def _validate_paths(
    env: dict[str, str], tracker: StepTracker,
) -> None:
    real_user, _ = get_real_user()

    media_root = env.get("MEDIA_LIBRARY_ROOT", "").strip()
    photos_root = env.get("IMMICH_UPLOAD_ROOT", "").strip()
    db_root = env.get("IMMICH_DB_ROOT", "").strip()

    missing = [v for v in ("MEDIA_LIBRARY_ROOT", "IMMICH_UPLOAD_ROOT", "IMMICH_DB_ROOT")
               if not env.get(v, "").strip()]
    if missing:
        tracker.fail(f"Required vars unset: {', '.join(missing)}")
        raise SystemExit(1)

    def _ensure_dir(path_str: str, label: str) -> None:
        p = Path(path_str)
        if not p.is_dir():
            try:
                p.mkdir(parents=True, exist_ok=True)
                shutil.chown(p, user=real_user)
                tracker.detail(f"Created {label}: {p}")
            except OSError as e:
                tracker.fail(f"{label} cannot be created: {p}\n{e}")
                raise SystemExit(1)

    _ensure_dir(media_root, "MEDIA_LIBRARY_ROOT")
    _ensure_dir(photos_root, "IMMICH_UPLOAD_ROOT")

    db_path = Path(db_root)
    if not db_path.is_dir():
        try:
            db_path.mkdir(parents=True, exist_ok=True)
            shutil.chown(db_path, user=real_user)
            tracker.detail(f"Created IMMICH_DB_ROOT: {db_path}")
        except OSError as e:
            tracker.fail(f"IMMICH_DB_ROOT cannot be created: {db_path}\n{e}")
            raise SystemExit(1)

    try:
        result = subprocess.run(["mountpoint", "-q", str(db_path)], capture_output=True)
        if result.returncode == 0:
            tracker.warn(
                f"IMMICH_DB_ROOT appears to be a mount point ({db_path}). "
                "Postgres requires local disk."
            )
    except OSError:
        pass

    tracker.success("Paths validated")


def _validate_db_password(
    env: dict[str, str], tracker: StepTracker, *, force: bool,
) -> None:
    pwd = env.get("DB_PASSWORD", "").strip()
    if pwd and not is_placeholder(pwd) and pwd.lower() != "postgres":
        tracker.detail("DB_PASSWORD... ok")
        return
    msg = "DB_PASSWORD is missing/placeholder or set to 'postgres'"
    if force:
        tracker.warn(f"{msg} (continuing with --force)")
    else:
        tracker.fail(msg)
        raise SystemExit(1)


def _warn_plex_claim(
    env: dict[str, str], config_base: Path, tracker: StepTracker, *, force: bool,
) -> None:
    claim = env.get("PLEX_CLAIM", "").strip()
    env_file = SCRIPT_DIR / ".env"
    plex_prefs = (
        config_base / "plex" / "Library" / "Application Support"
        / "Plex Media Server" / "Preferences.xml"
    )

    if claim and plex_prefs.is_file():
        if clear_env_var(env_file, "PLEX_CLAIM"):
            tracker.detail("Cleared stale PLEX_CLAIM (Plex already claimed)")
        return

    if claim or plex_prefs.is_file():
        return

    msg = (
        "PLEX_CLAIM is not set and Plex has not been started before.\n"
        "Get a claim token from https://www.plex.tv/claim (4 min expiry)"
    )
    if force:
        tracker.warn(msg + " (continuing with --force)")
    else:
        tracker.fail(msg)
        raise SystemExit(1)


def _gpu_guardrail(
    env: dict[str, str], tracker: StepTracker, *, force: bool,
) -> None:
    dev_dri = Path("/dev/dri")
    if not dev_dri.exists():
        msg = "/dev/dri missing — GPU passthrough may not be configured"
        if force:
            tracker.warn(msg + " (continuing with --force)")
        else:
            tracker.fail(msg)
            raise SystemExit(1)
        return

    compose_file = SCRIPT_DIR / "compose.yml"
    if not compose_file.is_file():
        return
    content = compose_file.read_text()
    missing_devices: list[str] = []
    for svc in ("plex", "immich-server"):
        svc_match = re.search(rf"^\s*{svc}:\s*$", content, re.MULTILINE)
        if not svc_match:
            continue
        chunk = "\n".join(content[svc_match.end():].splitlines()[:40])
        if "/dev/dri:/dev/dri" not in chunk:
            missing_devices.append(svc)

    if missing_devices:
        msg = f"GPU devices missing in compose for: {', '.join(missing_devices)}"
        if force:
            tracker.warn(msg + " (continuing with --force)")
        else:
            tracker.fail(msg)
            raise SystemExit(1)


def _ensure_config_dirs(config_base: Path, tracker: StepTracker) -> None:
    real_user, _ = get_real_user()
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
        for root, dirnames, _ in os.walk(config_base):
            for d in dirnames:
                try:
                    shutil.chown(Path(root) / d, user=real_user)
                except (PermissionError, LookupError):
                    pass
    except (PermissionError, LookupError):
        pass
    tracker.success(f"{len(dirs)} directories ready")


# ---------------------------------------------------------------------------
# Bootstrap steps
# ---------------------------------------------------------------------------

def _ensure_nfs_mounts(env: dict[str, str], tracker: StepTracker) -> None:
    """Configure NFS mounts for media and photos if NFS env vars are set."""
    nfs_host = env.get("NFS_HOST", "").strip()
    if not nfs_host:
        tracker.detail("NFS_HOST not set; skipping NFS setup")
        tracker.success("NFS check done (manual mount expected)")
        return

    from scripts.homelab_bootstrap import ensure_nfs_mount

    mounts = [
        ("NFS_MEDIA_EXPORT", env.get("MEDIA_LIBRARY_ROOT", "/mnt/media/library"), True),
        ("NFS_PHOTOS_EXPORT", env.get("IMMICH_UPLOAD_ROOT", "/mnt/photos/library"), False),
    ]
    for export_var, mount_point, read_only in mounts:
        export = env.get(export_var, "").strip()
        if not export:
            tracker.detail(f"{export_var} not set; skipping")
            continue
        ok = ensure_nfs_mount(nfs_host, export, mount_point, read_only=read_only, tracker=tracker)
        if ok:
            tracker.detail(f"Mounted {mount_point}")
        else:
            tracker.warn(f"NFS mount failed for {mount_point}")

    tracker.success("NFS mounts configured")


def bootstrap_steps(
    env: dict[str, str], config_base: Path, args,  # noqa: ANN001
) -> list[tuple[str, object]]:
    return [
        ("Configuring NFS mounts", lambda t: _ensure_nfs_mounts(env, t)),
        ("Validating paths", lambda t: _validate_paths(env, t)),
        ("Checking DB password", lambda t: _validate_db_password(env, t, force=args.force)),
        ("Creating config directories", lambda t: _ensure_config_dirs(config_base, t)),
        ("Checking Plex claim", lambda t: _warn_plex_claim(env, config_base, t, force=args.force)),
        ("GPU guardrail", lambda t: _gpu_guardrail(env, t, force=args.force)),
    ]


# ---------------------------------------------------------------------------
# Post-deploy hook
# ---------------------------------------------------------------------------

def post_deploy(
    env: dict[str, str], config_base: Path, tracker: StepTracker,
) -> None:
    """Configure Plex settings and library sections, then clear PLEX_CLAIM."""
    try:
        from docker_compose.accelerated.scripts import setup_accelerated_apps
        setup_accelerated_apps.setup(env)
        tracker.success("Plex configured via API")
    except Exception as exc:
        tracker.warn(f"Accelerated app setup failed: {exc}")

    env_file = SCRIPT_DIR / ".env"
    if clear_env_var(env_file, "PLEX_CLAIM"):
        tracker.detail("Cleared PLEX_CLAIM from .env (token consumed)")
