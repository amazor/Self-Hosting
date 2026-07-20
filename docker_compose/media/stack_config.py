"""Media VM stack configuration — registry entry for deploy.py and bootstrap.

Defines the media stack's required vars, compose overlays, bootstrap steps,
and post-deploy hooks.  Consumed by BootstrapRunner and deploy.py.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import secrets
import shutil
import subprocess
from pathlib import Path
from xml.etree import ElementTree
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from scripts.homelab_logging import StepTracker

SCRIPT_DIR = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# Stack identity
# ---------------------------------------------------------------------------

STACK_NAME = "media"
REQUIRES_ROOT = True
REQUIRES_DEBIAN = True
REQUIRES_DOCKER = False
SUPPORTS_UP = False

REQUIRED_VARS = [
    "MEDIA_ROOT",
    "CONFIG_ROOT",
    "EXPRESSVPN_CODE",
]

COMPOSE_OVERLAYS: list[tuple[str, str, str, str]] = [
    ("ENABLE_RECYCLARR", "compose.recyclarr.yml", "0", "Recyclarr"),
    ("ENABLE_CLEANUPARR", "compose.cleanuparr.yml", "0", "Cleanuparr"),
    ("ENABLE_SABNZBD", "compose.sabnzbd.yml", "0", "SABnzbd"),
    ("ENABLE_BAZARR", "compose.bazarr.yml", "0", "Bazarr"),
    ("ENABLE_NTFY", "compose.ntfy.yml", "0", "ntfy"),
    ("ENABLE_EXPORTERS", "compose.exporters.yml", "0", "Exporters"),
    ("ENABLE_OBSERVABILITY", "compose.observability.yml", "1", "Observability"),
]

_ARR_APPS = ("sonarr", "radarr", "prowlarr")

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


# ---------------------------------------------------------------------------
# Bootstrap step functions
# ---------------------------------------------------------------------------

def _env_path_display(env_file: Path) -> str:
    try:
        return str(env_file.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(env_file.resolve())


def _validate_media_env(
    env: dict[str, str], args, tracker: StepTracker,  # noqa: ANN001
) -> None:
    """Validate MEDIA_ROOT structure and compose guardrails."""
    from scripts.homelab_common import get_real_user
    real_user, _ = get_real_user()

    media_root_str = env.get("MEDIA_ROOT", "")
    if not media_root_str:
        tracker.fail("MEDIA_ROOT not set in .env")
        raise SystemExit(1)

    media_root = Path(media_root_str)
    if not media_root.is_dir():
        required_subdirs = (
            "downloads", "downloads/qbittorrent", "downloads/qbittorrent/incomplete",
            "downloads/qbittorrent/completed", "downloads/qbittorrent/completed/movies",
            "downloads/qbittorrent/completed/tv", "downloads/qbittorrent/completed/anime",
            "library", "library/tv", "library/movies", "library/anime",
        )
        try:
            media_root.mkdir(parents=True, exist_ok=True)
            for sub in required_subdirs:
                (media_root / sub).mkdir(parents=True, exist_ok=True)
            try:
                shutil.chown(media_root, user=real_user)
            except (PermissionError, LookupError):
                pass
            tracker.detail(f"Created MEDIA_ROOT: {media_root}")
        except OSError as e:
            tracker.fail(f"Cannot create MEDIA_ROOT: {media_root}\n{e}")
            raise SystemExit(1)

    for subdir in ("downloads", "library"):
        if not (media_root / subdir).is_dir():
            tracker.fail(f"Missing required directory: {media_root / subdir}")
            raise SystemExit(1)

    for subdir in ("library/tv", "library/movies", "library/anime"):
        target = media_root / subdir
        if not target.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            try:
                shutil.chown(target, user=real_user)
            except (PermissionError, LookupError):
                pass

    dl_subdirs = [
        "downloads/qbittorrent", "downloads/qbittorrent/incomplete",
        "downloads/qbittorrent/completed", "downloads/qbittorrent/completed/movies",
        "downloads/qbittorrent/completed/tv", "downloads/qbittorrent/completed/anime",
    ]
    if env.get("ENABLE_SABNZBD", "0") == "1":
        dl_subdirs += [
            "downloads/sabnzbd", "downloads/sabnzbd/completed",
            "downloads/sabnzbd/completed/movies", "downloads/sabnzbd/completed/tv",
            "downloads/sabnzbd/completed/anime", "downloads/sabnzbd/tmp",
        ]
    for subdir in dl_subdirs:
        target = media_root / subdir
        if not target.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            try:
                shutil.chown(target, user=real_user)
            except (PermissionError, LookupError):
                pass

    # Compose guardrails
    compose_file = SCRIPT_DIR / "compose.yml"
    if not args.force and compose_file.is_file():
        content = compose_file.read_text()
        needle = "${MEDIA_ROOT:-/mnt/media}:/data"
        for svc in ("qbittorrent", "sonarr", "radarr"):
            svc_match = re.search(rf"^(\s*){re.escape(svc)}:", content, re.MULTILINE)
            if not svc_match:
                continue
            indent = len(svc_match.group(1))
            rest = content[svc_match.end():]
            end = re.search(rf"^\s{{0,{indent}}}[a-zA-Z0-9_-]+:", rest, re.MULTILINE)
            section = rest[:end.start()] if end else rest
            if needle not in section:
                tracker.warn(f"{svc} missing single-root /data mapping")

        if not re.search(r"^\s*vpn:", content, re.MULTILINE):
            tracker.warn("compose.yml has no 'vpn' service")

    tracker.success("Media environment validated")


def _ensure_config_directories(
    env: dict[str, str], config_base: Path, tracker: StepTracker,
) -> None:
    from scripts.homelab_common import get_real_user
    real_user, _ = get_real_user()

    for d in ("qbittorrent", "sonarr", "radarr", "prowlarr", "flaresolverr"):
        (config_base / d).mkdir(parents=True, exist_ok=True)

    overlay_dirs: dict[str, list[str]] = {
        "ENABLE_RECYCLARR": ["recyclarr"],
        "ENABLE_CLEANUPARR": ["cleanuparr"],
        "ENABLE_SABNZBD": ["sabnzbd"],
        "ENABLE_BAZARR": ["bazarr"],
        "ENABLE_NTFY": ["ntfy", "ntfy/cache"],
        "ENABLE_EXPORTERS": ["scraparr"],
    }
    count = 5
    for var, dirs in overlay_dirs.items():
        if env.get(var, "0") == "1":
            for d in dirs:
                (config_base / d).mkdir(parents=True, exist_ok=True)
                count += 1

    try:
        shutil.chown(config_base, user=real_user)
        for root, dirnames, _ in os.walk(config_base):
            for d in dirnames:
                shutil.chown(Path(root) / d, user=real_user)
    except (PermissionError, LookupError):
        pass

    tracker.success(f"{count} directories ready")


def _seed_arr_configs(
    env: dict[str, str], config_base: Path, tracker: StepTracker,
) -> None:
    from scripts.homelab_common import get_real_user
    real_user, _ = get_real_user()
    auth_method = env.get("ARR_AUTH_METHOD", "External")
    seeded: list[str] = []

    for app in _ARR_APPS:
        conf_file = config_base / app / "config.xml"
        if conf_file.is_file():
            continue
        (config_base / app).mkdir(parents=True, exist_ok=True)
        conf_file.write_text(
            _ARR_CONFIG_XML.format(
                auth_method=auth_method,
                api_key=secrets.token_hex(16),
                instance_name=app.title(),
            )
        )
        try:
            shutil.chown(conf_file, user=real_user)
        except (PermissionError, LookupError):
            pass
        seeded.append(app)

    if seeded:
        tracker.detail(f"Pre-seeded config.xml for {', '.join(seeded)} (auth={auth_method})")


def _seed_qbittorrent_config(
    env: dict[str, str], config_base: Path, tracker: StepTracker,
) -> None:
    from scripts.homelab_common import get_real_user
    real_user, _ = get_real_user()

    conf_dir = config_base / "qbittorrent" / "qBittorrent"
    conf_file = conf_dir / "qBittorrent.conf"
    if conf_file.is_file():
        return

    username = env.get("QBITTORRENT_USERNAME", "admin")
    password = env.get("QBITTORRENT_PASSWORD", "adminadmin")

    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha512", password.encode("utf-8"), salt, 100000, dklen=64)
    password_hash = f"@ByteArray({base64.b64encode(salt).decode()}:{base64.b64encode(dk).decode()})"

    conf_dir.mkdir(parents=True, exist_ok=True)
    conf_file.write_text(
        f'[LegalNotice]\nAccepted=true\n\n'
        f'[BitTorrent]\n'
        f'Session\\DefaultSavePath=/data/downloads/qbittorrent/\n'
        f'Session\\TempPath=/data/downloads/qbittorrent/incomplete/\n'
        f'Session\\TempPathEnabled=true\n'
        f'Session\\DisableAutoTMMByDefault=false\n\n'
        f'[Preferences]\n'
        f'WebUI\\Username={username}\n'
        f'WebUI\\Password_PBKDF2="{password_hash}"\n'
    )
    try:
        shutil.chown(conf_file, user=real_user)
    except (PermissionError, LookupError):
        pass
    tracker.detail(f"Pre-seeded qBittorrent.conf (user={username})")


def _ini_value(value: str) -> str:
    """Quote a value for SABnzbd's ini so configobj round-trips it intact.

    configobj treats an unquoted '#' as the start of a comment, so a perfectly
    legal password like 'Q#e6qDMf38L@N*' is silently truncated at the '#' and
    SABnzbd then fails to authenticate with no error pointing at the cause.
    Commas are just as bad — configobj reads an unquoted comma as a list separator.

    Quote anything that is not plainly safe, picking a quote character the value
    does not itself contain.
    """
    if value == "":
        return '""'
    if not any(c in value for c in '#,;=\'" ') and value == value.strip():
        return value
    if '"' not in value:
        return f'"{value}"'
    if "'" not in value:
        return f"'{value}'"
    # Value contains both quote characters; configobj's triple-quote form handles it.
    return f'"""{value}"""'


def _seed_sabnzbd_config(
    env: dict[str, str], config_base: Path, tracker: StepTracker,
) -> None:
    if env.get("ENABLE_SABNZBD", "0") != "1":
        return

    conf_file = config_base / "sabnzbd" / "sabnzbd.ini"
    if conf_file.is_file():
        return

    server_host = env.get("USENET_SERVER_HOST", "")
    if not server_host:
        tracker.detail("USENET_SERVER_HOST not set; skipping SABnzbd seed")
        return

    from scripts.homelab_common import get_real_user
    real_user, _ = get_real_user()

    server_name = server_host.replace(".", "_").replace("-", "_")
    second_server = ""
    s2_host = env.get("USENET_SERVER2_HOST", "")
    if s2_host:
        s2_name = s2_host.replace(".", "_").replace("-", "_")
        second_server = (
            f"[[{s2_name}]]\nname = {s2_name}\ndisplayname = {s2_host}\n"
            f"host = {s2_host}\nport = {env.get('USENET_SERVER2_PORT', '563')}\n"
            f"username = {_ini_value(env.get('USENET_SERVER2_USERNAME', ''))}\n"
            f"password = {_ini_value(env.get('USENET_SERVER2_PASSWORD', ''))}\n"
            f"connections = {env.get('USENET_SERVER2_CONNECTIONS', '8')}\n"
            f"ssl = {env.get('USENET_SERVER2_SSL', '1')}\n"
            "ssl_verify = 3\nenable = 1\npriority = 1\noptional = 1\n"
            "retention = 0\ntimeout = 120\nsend_group = 0\n"
        )

    (config_base / "sabnzbd").mkdir(parents=True, exist_ok=True)
    (config_base / "sabnzbd" / "nzb-backup").mkdir(parents=True, exist_ok=True)

    # SABnzbd's check_hostname() is a DNS-rebinding guard: it rejects any request
    # whose Host header is not an IP literal, "localhost", *.local, or an EXACT
    # entry in host_whitelist (no wildcards, no suffix matching). Everything else
    # gets "Access denied - Hostname verification failed".
    #
    # "sabnzbd" (the compose service name) is therefore mandatory, not cosmetic:
    # Sonarr, Radarr and sabnzbd-exporter all reach it as http://sabnzbd:8080.
    # Drop it and the *arrs silently stop grabbing and the exporter 403s.
    #
    # Add any reverse-proxy FQDN (e.g. sabnzbd.example.com) via SABNZBD_HOST_WHITELIST —
    # Caddy forwards the original Host header, so the public name must be listed too.
    whitelist = ["sabnzbd"]
    for extra in env.get("SABNZBD_HOST_WHITELIST", "").split(","):
        extra = extra.strip().lower()
        if extra and extra not in whitelist:
            whitelist.append(extra)
    host_whitelist = ",".join(whitelist)

    conf_file.write_text(
        f"__version__ = 19\n__encoding__ = utf-8\n[misc]\n"
        f"api_key = {secrets.token_hex(16)}\nnzb_key = {secrets.token_hex(16)}\n"
        f"host = 0.0.0.0\nport = 8080\nhost_whitelist = {host_whitelist},\n"
        f"complete_dir = /data/downloads/sabnzbd/completed\n"
        f"download_dir = /data/downloads/sabnzbd/tmp\n"
        f"nzb_backup_dir = /config/nzb-backup\nadmin_dir = admin\nlog_dir = logs\n"
        f"auto_browser = 0\ncheck_new_rel = 0\npropagation_delay = 5\n"
        # bandwidth_max is BYTES/sec ("25M" = 25 MB/s ~= 200 Mbps, not 25 Mb/s).
        # Uncapped, 30 connections at line rate swamp the ISP gateway's conntrack
        # table and induce bufferbloat across every other flow. Only reaches a
        # FRESH install — existing deployments are capped over the API by
        # setup_media_apps.py, since this function no-ops once the ini exists.
        f"bandwidth_max = {_ini_value(env.get('SABNZBD_BANDWIDTH_MAX', '25M'))}\n"
        f"direct_unpack = 1\nsafe_postproc = 1\ndeobfuscate_final_filenames = 1\n"
        f"sfv_check = 1\nenable_recursive = 1\nreplace_illegal = 1\n"
        f"action_on_unwanted_extensions = 2\n"
        f"[logging]\nlog_level = 1\nmax_log_size = 5242880\nlog_backups = 5\n"
        f"[servers]\n[[{server_name}]]\nname = {server_name}\n"
        f"displayname = {server_host}\nhost = {server_host}\n"
        f"port = {env.get('USENET_SERVER_PORT', '563')}\n"
        f"username = {_ini_value(env.get('USENET_SERVER_USERNAME', ''))}\n"
        f"password = {_ini_value(env.get('USENET_SERVER_PASSWORD', ''))}\n"
        f"connections = {env.get('USENET_SERVER_CONNECTIONS', '30')}\n"
        f"ssl = {env.get('USENET_SERVER_SSL', '1')}\n"
        f"ssl_verify = 3\nenable = 1\npriority = 0\noptional = 0\n"
        f"retention = 0\ntimeout = 120\nsend_group = 0\n"
        f"{second_server}"
        f"[categories]\n[[*]]\nname = *\npriority = -100\npp = 3\nscript = Default\n"
        f"newzbin =\norder = 0\ndir =\n"
        f"[[tv]]\nname = tv\npriority = -100\npp = 3\nscript = Default\n"
        f"newzbin = tv\norder = 1\ndir = tv\n"
        f"[[movies]]\nname = movies\npriority = -100\npp = 3\nscript = Default\n"
        f"newzbin = movies\norder = 2\ndir = movies\n"
        f"[[anime]]\nname = anime\npriority = -100\npp = 3\nscript = Default\n"
        f"newzbin = anime\norder = 3\ndir = anime\n"
    )
    try:
        shutil.chown(conf_file, user=real_user)
    except (PermissionError, LookupError):
        pass
    tracker.detail(f"Pre-seeded sabnzbd.ini (server={server_host})")


# A quoted "!secret foo" is a plain YAML string, not a tag, so base_url never resolves
# to a URL.  Recyclarr rejects every instance for it and still exits 0, so the sync is a
# silent no-op forever.  Configs seeded from the old example carry this and must be
# re-seeded — leaving them alone means the fix never reaches an existing deployment.
_QUOTED_SECRET_TAG = re.compile(r':\s*"!secret\s')


def _repair_recyclarr_config(config_base: Path, tracker: StepTracker) -> None:
    conf = config_base / "recyclarr" / "recyclarr.yml"
    example = SCRIPT_DIR / "recyclarr.example.yml"
    if not conf.is_file() or not example.is_file():
        return
    if not _QUOTED_SECRET_TAG.search(conf.read_text(encoding="utf-8")):
        return

    backup = conf.with_name("recyclarr.yml.broken")
    shutil.copy2(conf, backup)
    shutil.copy2(example, conf)
    tracker.warn(
        f"recyclarr.yml had quoted !secret tags (sync was a silent no-op); "
        f"re-seeded from example, previous file kept as {backup.name}",
    )


def _copy_example_configs(
    env: dict[str, str], config_base: Path, tracker: StepTracker,
) -> None:
    from scripts.homelab_common import get_real_user
    real_user, _ = get_real_user()

    if env.get("ENABLE_RECYCLARR", "0") == "1":
        copies = [
            (SCRIPT_DIR / "recyclarr.example.yml", config_base / "recyclarr" / "recyclarr.yml"),
            (SCRIPT_DIR / "recyclarr.secrets.example.yml", config_base / "recyclarr" / "secrets.yml"),
        ]
        for src, dst in copies:
            if not dst.is_file() and src.is_file():
                shutil.copy2(src, dst)
                tracker.detail(f"Created {dst.name} from example")

        _repair_recyclarr_config(config_base, tracker)

    if (config_base / "recyclarr").is_dir():
        try:
            shutil.chown(config_base / "recyclarr", user=real_user)
        except (PermissionError, LookupError):
            pass


def _read_api_key(config_base: Path, app: str) -> str | None:
    config_xml = config_base / app / "config.xml"
    if not config_xml.is_file():
        return None
    try:
        tree = ElementTree.parse(config_xml)
        elem = tree.find("ApiKey")
        return elem.text.strip() if elem is not None and elem.text else None
    except ElementTree.ParseError:
        return None


def _populate_api_keys(
    config_base: Path, tracker: StepTracker,
) -> None:
    from scripts.homelab_common import get_real_user
    real_user, _ = get_real_user()

    keys = {app: _read_api_key(config_base, app) for app in ("sonarr", "radarr", "prowlarr")}
    if not any(keys.values()):
        return

    replacements = {
        "YOUR_SONARR_API_KEY": keys.get("sonarr", ""),
        "YOUR_RADARR_API_KEY": keys.get("radarr", ""),
        "YOUR_PROWLARR_API_KEY": keys.get("prowlarr", ""),
    }

    secrets_file = config_base / "recyclarr" / "secrets.yml"
    if not secrets_file.is_file():
        return

    content = secrets_file.read_text(encoding="utf-8")
    changed = False
    for placeholder, real_key in replacements.items():
        if real_key and placeholder in content:
            content = content.replace(placeholder, real_key)
            changed = True
    if changed:
        secrets_file.write_text(content, encoding="utf-8")
        try:
            shutil.chown(secrets_file, user=real_user)
        except (PermissionError, LookupError):
            pass
        tracker.detail("Populated API keys in Recyclarr secrets.yml")


def _read_sabnzbd_api_key(config_base: Path) -> str | None:
    """Read api_key from the bootstrap-seeded sabnzbd.ini."""
    ini_file = config_base / "sabnzbd" / "sabnzbd.ini"
    if not ini_file.is_file():
        return None
    try:
        for line in ini_file.read_text().splitlines():
            stripped = line.strip()
            if stripped.startswith("api_key") and "=" in stripped:
                return stripped.split("=", 1)[1].strip()
    except OSError:
        pass
    return None


def _populate_env_api_keys(
    env: dict[str, str], config_base: Path, tracker: StepTracker,
) -> None:
    """Sync generated API keys back into .env so exporters can read them.

    Runs during bootstrap, i.e. before `docker compose up`, so the values are in
    place by the time Compose interpolates them into the exporter containers.
    """
    env_vars: dict[str, str] = {}

    # *arr keys feed scraparr (compose.exporters.yml).
    if env.get("ENABLE_EXPORTERS", "0") == "1":
        for app in ("sonarr", "radarr", "prowlarr"):
            key = _read_api_key(config_base, app)
            if key:
                env_vars[f"{app.upper()}_API_KEY"] = key

    # SABnzbd's key feeds sabnzbd-exporter (compose.sabnzbd.yml). The key is random
    # per-install, so without this the exporter would need manual copy-paste from the UI.
    if env.get("ENABLE_SABNZBD", "0") == "1":
        sab_key = _read_sabnzbd_api_key(config_base)
        if sab_key:
            env_vars["SABNZBD_API_KEY"] = sab_key

    if not env_vars:
        return

    env_file = SCRIPT_DIR / ".env"
    lines = env_file.read_text().splitlines()
    new_lines: list[str] = []
    seen: set[str] = set()

    for line in lines:
        stripped = line.strip()
        matched = False
        for var, val in env_vars.items():
            commented = re.match(rf"^#\s*{re.escape(var)}\s*=(.*)", stripped)
            uncommented = re.match(rf"^{re.escape(var)}\s*=(.*)", stripped)
            if commented or uncommented:
                seen.add(var)
                new_lines.append(f"{var}={val}")
                matched = True
                break
        if not matched:
            new_lines.append(line)

    for var, val in env_vars.items():
        if var not in seen:
            new_lines.append(f"{var}={val}")

    env_file.write_text("\n".join(new_lines) + "\n")
    tracker.detail(f"Synced exporter API keys to .env: {', '.join(env_vars)}")


def _seed_all_configs(
    env: dict[str, str], config_base: Path, tracker: StepTracker,
) -> None:
    _seed_arr_configs(env, config_base, tracker)
    _seed_qbittorrent_config(env, config_base, tracker)
    _seed_sabnzbd_config(env, config_base, tracker)
    _copy_example_configs(env, config_base, tracker)
    _populate_api_keys(config_base, tracker)
    _populate_env_api_keys(env, config_base, tracker)
    tracker.success("App configs seeded")


# ---------------------------------------------------------------------------
# Bootstrap steps
# ---------------------------------------------------------------------------

def _ensure_nfs_mounts(env: dict[str, str], tracker: StepTracker) -> None:
    """Configure NFS mount for MEDIA_ROOT if NFS env vars are set."""
    nfs_host = env.get("NFS_HOST", "").strip()
    nfs_export = env.get("NFS_MEDIA_EXPORT", "").strip()
    media_root = env.get("MEDIA_ROOT", "/mnt/media")

    if not nfs_host or not nfs_export:
        tracker.detail("NFS vars not set; skipping NFS setup")
        tracker.success("NFS check done (manual mount expected)")
        return

    from scripts.homelab_bootstrap import ensure_nfs_mount
    ok = ensure_nfs_mount(nfs_host, nfs_export, media_root, tracker=tracker)
    if ok:
        tracker.success(f"NFS mounted: {media_root}")
    else:
        tracker.warn(f"NFS mount failed for {media_root} (continuing)")


_ANIME_SYNC_TOUCH_SCRIPT = """\
#!/bin/sh
# Runs inside the Sonarr container on the onSeriesAdd notification.
# Just signals the host — see media-anime-sync.path/.service — because
# giving Sonarr's own container direct access to Bazarr's API key would
# mean a compromised Sonarr (it parses attacker-influenceable indexer
# metadata) could rewrite Bazarr's config, not just its own.
touch /config/.anime-sync-needed
"""

_ANIME_SYNC_SERVICE_TEMPLATE = """\
[Unit]
Description=Media anime auto-sync (indexer tag-scoping + Bazarr profile assignment)

[Service]
Type=oneshot
WorkingDirectory={stack_dir}
# The marker is consumed BEFORE the sync runs, not after, and this is load-bearing:
#   - A series added while a sync is in flight re-touches the marker. Deleting it
#     afterwards would throw that signal away — the .path unit re-arms, sees no
#     file, and never triggers again, so that series is silently never tagged or
#     given a subtitle profile. Deleting it first means the late touch survives the
#     run and the .path unit fires a second time, which is exactly what we want.
#   - ExecStartPost does not run when ExecStart fails. A failing sync would leave
#     the marker in place, re-triggering the unit immediately, over and over, until
#     systemd's default start limit (5 starts / 10s) trips and parks the unit in
#     `failed` — the hook then stays dead until someone notices and resets it.
# rm -f is exit-0 even when the marker is already gone, so it cannot fail the unit.
ExecStartPre=/usr/bin/rm -f {marker_path}
ExecStart=/usr/bin/python3 {stack_dir}/scripts/setup_media_apps.py --sync-anime-only
"""

_ANIME_SYNC_PATH_UNIT = """\
[Unit]
Description=Watch for a new-anime-series marker touched by Sonarr's onSeriesAdd hook

[Path]
PathExists={marker_path}
Unit=media-anime-sync.service

[Install]
WantedBy=multi-user.target
"""


def _ensure_anime_sync_hook(config_base: Path, tracker: StepTracker) -> None:
    """Install the host side of the anime auto-sync hook.

    A new anime series has no indexer tags and sits on Bazarr's shared
    subtitle profile until the two steps in setup_media_apps.sync_anime_only()
    run — normally that only happens on the next full deploy. This lets
    Sonarr's onSeriesAdd notification (configured by setup_media_apps.py
    against a script this step installs) trigger it immediately instead:
    Sonarr touches a marker file in its own config volume, a systemd .path
    unit on the host notices, and runs sync_anime_only() directly (same
    trust tier as running deploy.py yourself — no credentials cross into
    the Sonarr container).
    """
    from scripts.homelab_bootstrap import write_if_changed

    marker_path = config_base / "sonarr" / ".anime-sync-needed"
    script_path = config_base / "sonarr" / "scripts" / "anime-sync-touch.sh"
    changed = write_if_changed(script_path, _ANIME_SYNC_TOUCH_SCRIPT, executable=True)

    changed |= write_if_changed(
        Path("/etc/systemd/system/media-anime-sync.service"),
        _ANIME_SYNC_SERVICE_TEMPLATE.format(stack_dir=SCRIPT_DIR, marker_path=marker_path),
    )
    changed |= write_if_changed(
        Path("/etc/systemd/system/media-anime-sync.path"),
        _ANIME_SYNC_PATH_UNIT.format(marker_path=marker_path),
    )

    # Report what systemd actually did, not what we asked it to do. Reporting
    # success unconditionally here would mean a hook that failed to install looks
    # identical to one that works — the deploy goes green and new anime silently
    # never gets tagged, which is the whole class of bug this hook exists to close.
    # Warn rather than fail: this runs in bootstrap, and a failed bootstrap step
    # aborts the deploy before compose up. The stack itself is fine without the
    # hook (the next full deploy still syncs); only the auto-trigger is missing.
    if changed:
        reload_result = subprocess.run(
            ["systemctl", "daemon-reload"], check=False, capture_output=True, text=True,
        )
        if reload_result.returncode != 0:
            tracker.warn(
                "systemctl daemon-reload failed; anime auto-sync hook not active "
                f"({reload_result.stderr.strip()})"
            )
            return

    enable_result = subprocess.run(
        ["systemctl", "enable", "--now", "media-anime-sync.path"],
        check=False, capture_output=True, text=True,
    )
    if enable_result.returncode != 0:
        tracker.warn(
            "Could not enable media-anime-sync.path; new anime series will not be "
            "auto-synced until the next full deploy "
            f"({enable_result.stderr.strip()})"
        )
        return

    tracker.success("Anime auto-sync hook installed (media-anime-sync.path)")


def bootstrap_steps(
    env: dict[str, str], config_base: Path, args,  # noqa: ANN001
) -> list[tuple[str, object]]:
    return [
        ("Configuring NFS mounts", lambda t: _ensure_nfs_mounts(env, t)),
        ("Validating media environment", lambda t: _validate_media_env(env, args, t)),
        ("Installing anime auto-sync hook", lambda t: _ensure_anime_sync_hook(config_base, t)),
        ("Creating config directories", lambda t: _ensure_config_directories(env, config_base, t)),
        ("Seeding app configs", lambda t: _seed_all_configs(env, config_base, t)),
    ]


# ---------------------------------------------------------------------------
# Post-deploy hook
# ---------------------------------------------------------------------------

def post_deploy(
    env: dict[str, str], config_base: Path, tracker: StepTracker,
) -> None:
    """Configure media apps via API after docker compose up."""
    try:
        from docker_compose.media.scripts import setup_media_apps
        ok = setup_media_apps.setup(env, SCRIPT_DIR)
    except Exception as exc:
        tracker.fail(f"Media app setup crashed: {exc}")
    else:
        if ok:
            tracker.success("Media apps configured via API")
        else:
            tracker.fail(
                "Media app setup failed — a core service (Prowlarr/Sonarr/Radarr/"
                "qBittorrent) or an explicitly-enabled optional one (Bazarr/SABnzbd) "
                "did not get configured. See the warnings/errors above for which one."
            )

    if env.get("ENABLE_RECYCLARR", "0") == "1":
        from scripts.homelab_bootstrap import build_compose_command
        compose_files = build_compose_command(SCRIPT_DIR, COMPOSE_OVERLAYS, env)
        tracker.detail("Triggering initial Recyclarr sync...")
        result = subprocess.run(
            ["docker", "compose"] + compose_files
            + ["exec", "recyclarr", "recyclarr", "sync"],
            cwd=SCRIPT_DIR, capture_output=True, text=True,
        )
        # Recyclarr exits 0 even when it rejects every instance, so the exit code alone
        # will happily report success on a sync that changed nothing.
        output = (result.stdout or "") + (result.stderr or "")
        if result.returncode != 0:
            tracker.warn("Recyclarr sync failed (may need manual config)")
        elif "Configuration Errors" in output or "Invalid instances" in output:
            tracker.warn(
                "Recyclarr rejected its config and synced nothing — check "
                "config/recyclarr/recyclarr.yml (see 'Configuration Errors' in the sync log)",
            )
        else:
            tracker.detail("Recyclarr sync completed")
