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
    set_env_var,
)

# ---------------------------------------------------------------------------
# Stack identity
# ---------------------------------------------------------------------------

STACK_NAME = "accelerated"
REQUIRES_ROOT = True
REQUIRES_DEBIAN = True
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
    "First-run Plex claim (one-time only):\n"
    "  1. Get a claim token: https://www.plex.tv/claim (expires in 4 minutes)\n"
    "  2. Set PLEX_CLAIM=claim-xxxx in .env\n"
    "  3. Re-run: python3 deploy.py accelerated\n"
    "  PLEX_TOKEN is auto-extracted from Preferences.xml after Plex starts.",
    "Immich hardware transcoding (one-time setup after first login):\n"
    "  1. Open Immich admin UI → Administration → Video Transcoding Settings\n"
    "  2. Set Hardware Acceleration to 'Quick Sync' (Intel QSV) or 'VAAPI'\n"
    "  3. Optionally enable Hardware Decoding for end-to-end GPU acceleration\n"
    "  Ref: https://immich.app/docs/features/hardware-transcoding",
    "Confirm Plex is transcoding on the GPU, not the CPU:\n"
    "  Play a file that forces a transcode, then check Plex → Settings →\n"
    "  Status → Now Playing (or the Dashboard). The stream should read\n"
    "  'Transcode (hw)'. If it shows 'Transcode' WITHOUT the (hw) tag, Plex is\n"
    "  using the CPU — expect high CPU load and stuttering. Recheck that\n"
    "  hardware transcoding is enabled and the GPU firmware/driver is loaded.",
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

    if plex_prefs.is_file():
        tracker.detail("Plex already claimed (Preferences.xml exists)")
        return

    if claim:
        tracker.detail(
            "PLEX_CLAIM is set — Plex will consume it on first start. "
            "PLEX_TOKEN will be auto-extracted afterward."
        )
        return

    msg = (
        "PLEX_CLAIM is not set and Plex has not been claimed.\n"
        "  1. Get a claim token: https://www.plex.tv/claim\n"
        "     (token expires in 4 minutes — set it and deploy promptly)\n"
        "  2. Set PLEX_CLAIM=claim-xxxx in .env\n"
        "  3. Re-run deploy. PLEX_TOKEN will be auto-extracted after Plex starts."
    )
    if force:
        tracker.warn(msg + "\n(continuing with --force)")
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
        config_base / "immich-postgres",
        config_base / "immich-ml-cache",
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
# VA-API driver installation
# ---------------------------------------------------------------------------

_VAAPI_PACKAGES = [
    # Binary firmware the i915 driver needs to fully init the passed-through iGPU.
    # Without it /dev/dri may appear but VA-API/QSV fails to initialize.
    "firmware-intel-graphics",
    "intel-media-va-driver-non-free",
    "vainfo",
    "intel-gpu-tools",
]


def _all_packages_installed(packages: list[str]) -> bool:
    """Return True if every package in the list is already installed."""
    for pkg in packages:
        result = subprocess.run(
            ["dpkg", "-s", pkg],
            capture_output=True,
        )
        if result.returncode != 0:
            return False
    return True


def _has_nonfree_component(line: str) -> bool:
    """Check if a line contains 'non-free' as a standalone apt component.

    Must not match 'non-free-firmware' (a different Debian component).
    """
    return "non-free" in line.split() or " non-free " in f" {line} "


def _ensure_nonfree_repo(tracker: StepTracker) -> bool:
    """Ensure the Debian non-free component is available for apt.

    Handles both traditional sources.list format and DEB822 .sources files.
    Returns True if the repo was added (apt-get update needed).
    """
    # --- Check DEB822 .sources files first (Debian 12+ default) ---
    sources_d = Path("/etc/apt/sources.list.d")
    if sources_d.is_dir():
        for src_file in sources_d.glob("*.sources"):
            content = src_file.read_text()
            for src_line in content.splitlines():
                if src_line.startswith("Components:") and _has_nonfree_component(src_line):
                    tracker.detail("non-free component already present in DEB822 sources")
                    return False
            if "Components:" in content and "main" in content:
                new_lines: list[str] = []
                modified = False
                for src_line in content.splitlines():
                    if src_line.startswith("Components:") and not _has_nonfree_component(src_line):
                        src_line = src_line.rstrip() + " non-free non-free-firmware"
                        modified = True
                    new_lines.append(src_line)
                if modified:
                    src_file.write_text("\n".join(new_lines) + "\n")
                    tracker.detail(f"Added non-free to {src_file.name}")
                    return True

    # --- Fall back to traditional sources.list ---
    sources_list = Path("/etc/apt/sources.list")
    if not sources_list.is_file():
        tracker.warn("No /etc/apt/sources.list found; cannot add non-free repo")
        return False

    content = sources_list.read_text()
    if any(_has_nonfree_component(ln) for ln in content.splitlines()):
        tracker.detail("non-free component already present in sources.list")
        return False

    new_lines = []
    modified = False
    for line in content.splitlines():
        stripped = line.strip()
        if (
            not stripped.startswith("#")
            and stripped.startswith("deb ")
            and "main" in stripped
            and not _has_nonfree_component(stripped)
        ):
            line = line.rstrip() + " non-free non-free-firmware"
            modified = True
        new_lines.append(line)

    if modified:
        sources_list.write_text("\n".join(new_lines) + "\n")
        tracker.detail("Added non-free component to sources.list")
    return modified


def _install_vaapi_packages(
    env: dict[str, str], tracker: StepTracker, *, force: bool,
) -> None:
    """Install Intel VA-API drivers and verification tools."""
    if _all_packages_installed(_VAAPI_PACKAGES):
        tracker.detail("VA-API packages already installed")
        tracker.success("VA-API ready")
        return

    # Track whether GPU firmware is being installed for the first time. Newly
    # installed firmware is not loaded until the i915 driver reloads (reboot),
    # so we surface a reboot reminder below only when it was actually missing.
    firmware_present_before = _all_packages_installed(["firmware-intel-graphics"])

    repo_changed = _ensure_nonfree_repo(tracker)
    if repo_changed:
        tracker.detail("Running apt-get update for new repo components...")
        subprocess.run(
            ["apt-get", "update", "-qq"],
            capture_output=True,
        )

    tracker.detail(f"Installing: {', '.join(_VAAPI_PACKAGES)}")
    result = subprocess.run(
        ["apt-get", "install", "-y", "-qq"] + _VAAPI_PACKAGES,
        capture_output=True,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode() if result.stderr else ""
        msg = f"Failed to install VA-API packages\n{stderr}"
        if force:
            tracker.warn(msg + " (continuing with --force)")
            return
        tracker.fail(msg)
        raise SystemExit(1)

    firmware_newly_installed = (
        not firmware_present_before
        and _all_packages_installed(["firmware-intel-graphics"])
    )

    # Verify GPU access via vainfo
    vainfo = subprocess.run(["vainfo"], capture_output=True, text=True)
    if vainfo.returncode == 0:
        for line in vainfo.stdout.splitlines()[:5]:
            tracker.detail(line.strip())
        tracker.success("VA-API packages installed and verified")
    else:
        tracker.warn(
            "VA-API packages installed but vainfo failed — "
            "GPU may not be passed through yet. "
            "Verify /dev/dri exists after configuring GPU passthrough."
        )

    if firmware_newly_installed:
        tracker.add_action(
            "Reboot the Accelerated VM so the i915 driver loads the newly installed\n"
            "GPU firmware (firmware-intel-graphics). Freshly installed firmware is not\n"
            "picked up until the driver reloads — until then Plex/Immich hardware\n"
            "transcoding may silently fall back to CPU.\n"
            "After reboot, verify with: vainfo"
        )


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
        ("Installing VA-API packages", lambda t: _install_vaapi_packages(env, t, force=args.force)),
        ("Checking Plex claim", lambda t: _warn_plex_claim(env, config_base, t, force=args.force)),
        ("GPU guardrail", lambda t: _gpu_guardrail(env, t, force=args.force)),
    ]


# ---------------------------------------------------------------------------
# Post-deploy hook
# ---------------------------------------------------------------------------

def post_deploy(
    env: dict[str, str], config_base: Path, tracker: StepTracker,
) -> None:
    """Extract PLEX_TOKEN if needed, configure Plex, then clear PLEX_CLAIM."""
    from docker_compose.accelerated.scripts import setup_accelerated_apps

    env_file = SCRIPT_DIR / ".env"
    plex_token = env.get("PLEX_TOKEN", "").strip()

    # Auto-extract PLEX_TOKEN from Preferences.xml on first run
    if not plex_token:
        tracker.detail("PLEX_TOKEN not set — attempting auto-extraction...")
        if setup_accelerated_apps.wait_for_plex_noauth():
            extracted = setup_accelerated_apps.extract_plex_token(config_base)
            if extracted:
                set_env_var(env_file, "PLEX_TOKEN", extracted)
                env["PLEX_TOKEN"] = extracted
                plex_token = extracted
                tracker.detail("PLEX_TOKEN extracted and saved to .env")
            else:
                tracker.warn(
                    "Could not extract PLEX_TOKEN from Preferences.xml. "
                    "Plex may not have been claimed yet."
                )
        else:
            tracker.warn("Plex not responding — skipping token extraction")

    # Apply TRaSH prefs and create libraries
    try:
        ok = setup_accelerated_apps.setup(env)
    except Exception as exc:
        tracker.warn(f"Accelerated app setup failed: {exc}")
    else:
        if ok:
            tracker.success("Plex configured via API")
        else:
            tracker.warn(
                "Plex setup skipped (PLEX_TOKEN missing or Plex unreachable). "
                "Set PLEX_TOKEN and re-run: python3 deploy.py accelerated"
            )

    # Clear one-time claim token
    if clear_env_var(env_file, "PLEX_CLAIM"):
        tracker.detail("Cleared PLEX_CLAIM from .env (token consumed)")

    # Cross-VM guidance: tell user what to copy to media VM
    plex_host = env.get("PLEX_HOST", "").strip()
    if plex_token and plex_host:
        tracker.add_action(
            "To enable Sonarr/Radarr → Plex refresh on the media VM, "
            "add to docker_compose/media/.env:\n"
            f"  PLEX_HOST={plex_host}\n"
            f"  PLEX_TOKEN={plex_token}\n"
            "Then redeploy: python3 deploy.py media"
        )
    elif plex_token:
        tracker.add_action(
            "PLEX_TOKEN was extracted. Set PLEX_HOST (this VM's LAN IP) in .env,\n"
            "then copy both PLEX_HOST and PLEX_TOKEN to docker_compose/media/.env\n"
            "to enable Sonarr/Radarr → Plex refresh notifications."
        )
