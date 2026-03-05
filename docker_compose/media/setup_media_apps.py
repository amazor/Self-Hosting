#!/usr/bin/env python3
"""Post-deploy setup for media stack: Prowlarr indexers + qBittorrent config.

Adds public torrent indexers to Prowlarr and configures qBittorrent categories
and settings via their APIs. Runs after docker compose up, when the services
are healthy.

Called by deploy.py after the media stack starts, or run standalone:
  cd docker_compose/media && python3 setup_media_apps.py

Idempotent: skips indexers/categories that already exist.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from xml.etree import ElementTree

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.homelab_common import load_env, log, resolve_config_base, setup_logging

HEALTH_TIMEOUT_S = 90
HEALTH_POLL_S = 5

# ---------------------------------------------------------------------------
# Public torrent indexers to add to Prowlarr.
# "name" must match Prowlarr's built-in indexer definition name exactly.
# Ref: https://trash-guides.info/Prowlarr/
# ---------------------------------------------------------------------------

# flaresolverr=True means the indexer is commonly behind Cloudflare and needs
# the FlareSolverr proxy tag.  Ref: https://trash-guides.info/Prowlarr/prowlarr-setup-flaresolverr/
PROWLARR_INDEXERS = [
    # General — movies + TV
    {"name": "1337x", "definitionName": "1337x", "flaresolverr": True},
    {"name": "The Pirate Bay", "definitionName": "thepiratebay", "flaresolverr": True},
    {"name": "EZTV", "definitionName": "eztv", "flaresolverr": True},
    {"name": "KickAssTorrents.to", "definitionName": "kickasstorrents-to", "flaresolverr": True},
    {"name": "KickAssTorrents.ws", "definitionName": "kickasstorrents-ws", "flaresolverr": True},
    {"name": "LimeTorrents", "definitionName": "limetorrents", "flaresolverr": True},
    {"name": "YTS", "definitionName": "yts"},
    {"name": "Torrent Downloads", "definitionName": "torrentdownloads"},
    {"name": "Knaben", "definitionName": "Knaben"},
    # Anime
    {"name": "Nyaa.si", "definitionName": "nyaasi"},
    {"name": "SubsPlease", "definitionName": "SubsPlease"},
    {"name": "Tokyo Toshokan", "definitionName": "tokyotosho"},
    {"name": "Shana Project", "definitionName": "shanaproject"},
]

FLARESOLVERR_TAG_NAME = "flaresolverr"

# qBittorrent categories per TRaSH guide.
# Ref: https://trash-guides.info/Downloaders/qBittorrent/
QBIT_CATEGORIES = {
    "tv": "completed/tv",
    "movies": "completed/movies",
    "anime": "completed/anime",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_api_key(config_base: Path, app: str) -> str | None:
    """Read ApiKey from a *arr app's config.xml."""
    config_xml = config_base / app / "config.xml"
    if not config_xml.is_file():
        return None
    try:
        tree = ElementTree.parse(config_xml)
        elem = tree.find("ApiKey")
        return elem.text.strip() if elem is not None and elem.text else None
    except ElementTree.ParseError:
        return None


def _api(url: str, headers: dict, *, method: str = "GET",
         data: dict | None = None) -> dict | list | None:
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, json.JSONDecodeError) as exc:
        log.debug("API %s %s → %s", method, url, exc)
        return None


def _wait_for_service(name: str, url: str, headers: dict) -> bool:
    deadline = time.monotonic() + HEALTH_TIMEOUT_S
    while time.monotonic() < deadline:
        result = _api(url, headers)
        if result is not None:
            return True
        time.sleep(HEALTH_POLL_S)
    log.warning("%s not ready within %ds.", name, HEALTH_TIMEOUT_S)
    return False


# ---------------------------------------------------------------------------
# Prowlarr indexer setup
# ---------------------------------------------------------------------------


def _get_indexer_schema(prowlarr_url: str, headers: dict, definition_name: str) -> dict | None:
    """Fetch the schema for a specific indexer from Prowlarr."""
    schemas = _api(f"{prowlarr_url}/api/v1/indexer/schema", headers)
    if not schemas:
        return None
    for s in schemas:
        if s.get("definitionName") == definition_name:
            return s
    return None


def _ensure_flaresolverr_proxy(prowlarr_url: str, headers: dict) -> int | None:
    """Create the FlareSolverr tag and indexer proxy in Prowlarr. Returns tag ID."""
    # Create or find the tag
    tags = _api(f"{prowlarr_url}/api/v1/tag", headers)
    tag_id = None
    if tags:
        for t in tags:
            if t.get("label", "").lower() == FLARESOLVERR_TAG_NAME:
                tag_id = t["id"]
                break
    if tag_id is None:
        result = _api(
            f"{prowlarr_url}/api/v1/tag", headers, method="POST",
            data={"label": FLARESOLVERR_TAG_NAME},
        )
        if result:
            tag_id = result.get("id")

    if tag_id is None:
        log.warning("Could not create FlareSolverr tag in Prowlarr.")
        return None

    # Create or find the FlareSolverr indexer proxy
    proxies = _api(f"{prowlarr_url}/api/v1/indexerProxy", headers)
    if proxies:
        for p in proxies:
            if "flaresolverr" in p.get("name", "").lower():
                log.info("FlareSolverr proxy already exists in Prowlarr.")
                return tag_id

    proxy_payload = {
        "name": "FlareSolverr",
        "implementation": "FlareSolverr",
        "implementationName": "FlareSolverr",
        "configContract": "FlareSolverrSettings",
        "fields": [
            {"name": "host", "value": "http://flaresolverr:8191"},
            {"name": "requestTimeout", "value": 60},
        ],
        "tags": [tag_id],
    }
    result = _api(f"{prowlarr_url}/api/v1/indexerProxy", headers, method="POST", data=proxy_payload)
    if result and result.get("id"):
        log.info("Created FlareSolverr proxy in Prowlarr (tag=%s).", FLARESOLVERR_TAG_NAME)
    else:
        log.warning("Failed to create FlareSolverr proxy.")

    return tag_id


def setup_prowlarr_indexers(prowlarr_url: str, api_key: str) -> None:
    """Add FlareSolverr proxy and public torrent indexers to Prowlarr."""
    headers = {"X-Api-Key": api_key, "Content-Type": "application/json"}

    if not _wait_for_service("Prowlarr", f"{prowlarr_url}/api/v1/health", headers):
        return

    # Set up FlareSolverr proxy first — some indexers need it
    fs_tag_id = _ensure_flaresolverr_proxy(prowlarr_url, headers)

    existing = _api(f"{prowlarr_url}/api/v1/indexer", headers)
    if existing is None:
        log.warning("Could not list Prowlarr indexers.")
        return
    existing_defs = {idx.get("definitionName", "").lower() for idx in existing}

    added = 0
    skipped = 0
    failed = 0
    for idx_def in PROWLARR_INDEXERS:
        if idx_def["definitionName"].lower() in existing_defs:
            skipped += 1
            continue

        schema = _get_indexer_schema(prowlarr_url, headers, idx_def["definitionName"])
        if not schema:
            log.debug("No schema found for %s, skipping.", idx_def["name"])
            failed += 1
            continue

        schema["name"] = idx_def["name"]
        schema["enable"] = True
        schema["priority"] = idx_def.get("priority", 25)
        schema["appProfileId"] = 1

        tags = []
        if idx_def.get("flaresolverr") and fs_tag_id is not None:
            tags.append(fs_tag_id)
        schema["tags"] = tags

        for key in ("id", "presets"):
            schema.pop(key, None)

        result = _api(f"{prowlarr_url}/api/v1/indexer", headers, method="POST", data=schema)
        if result and result.get("id"):
            added += 1
        else:
            log.debug("Failed to add indexer %s", idx_def["name"])
            failed += 1

    log.info(
        "Prowlarr indexers: %d added, %d already existed, %d failed.",
        added, skipped, failed,
    )


# ---------------------------------------------------------------------------
# qBittorrent setup
# ---------------------------------------------------------------------------


def setup_qbittorrent(qbit_url: str, env: dict[str, str]) -> None:
    """Configure qBittorrent categories and settings per TRaSH guide."""
    username = env.get("QBITTORRENT_USERNAME", "admin")
    password = env.get("QBITTORRENT_PASSWORD", "adminadmin")
    login_headers = {"Content-Type": "application/x-www-form-urlencoded"}

    # Wait for qBittorrent to be reachable
    deadline = time.monotonic() + HEALTH_TIMEOUT_S
    sid = None
    while time.monotonic() < deadline:
        try:
            login_data = urllib.parse.urlencode(
                {"username": username, "password": password}
            ).encode()
            req = urllib.request.Request(
                f"{qbit_url}/api/v2/auth/login",
                data=login_data,
                headers=login_headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read().decode()
                if "Ok" in body or resp.status == 200:
                    for cookie in resp.headers.get_all("Set-Cookie") or []:
                        if "SID=" in cookie:
                            sid = cookie.split("SID=")[1].split(";")[0]
                    break
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(HEALTH_POLL_S)

    if sid is None:
        log.warning("Could not authenticate to qBittorrent. Skipping config.")
        return

    headers = {"Cookie": f"SID={sid}"}

    # Set preferences per TRaSH guide:
    # https://trash-guides.info/Downloaders/qBittorrent/Basic-Setup/
    prefs = {
        # --- Downloads / Saving Management ---
        "save_path": "/data/downloads/qbittorrent/",
        "temp_path_enabled": True,
        "temp_path": "/data/downloads/qbittorrent/incomplete/",
        "auto_tmm_enabled": True,
        "preallocate_all": False,
        # --- Connection ---
        "bittorrent_protocol": 1,   # TCP only (TRaSH: best performance)
        "listen_port": 6881,
        "upnp": False,              # behind VPN; no UPnP needed
        "random_port": False,       # use the VPN-forwarded port
        # --- Speed / Rate Limits ---
        "limit_utp_rate": True,     # prevent uTP flood
        "limit_tcp_overhead": False, # don't count overhead against limits
        "limit_lan_peers": True,
        "max_active_downloads": 5,
        "max_active_uploads": 5,
        "max_active_torrents": 10,
        # --- BitTorrent / Privacy ---
        "encryption": 0,            # 0 = allow (prefer), not force
        "anonymous_mode": False,    # worse speeds; issues with private trackers
        # --- Seeding (let *arr indexer settings handle this instead) ---
        "max_ratio_enabled": False,
        "max_seeding_time_enabled": False,
        "add_trackers_enabled": False,
        # --- Web UI ---
        "web_ui_csrf_protection_enabled": False,  # can cause issues behind reverse proxy
    }
    try:
        req = urllib.request.Request(
            f"{qbit_url}/api/v2/app/setPreferences",
            data=f"json={json.dumps(prefs)}".encode(),
            headers={**headers, "Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)
        log.info("qBittorrent preferences set (save path, auto TMM, limits).")
    except (urllib.error.URLError, OSError) as exc:
        log.warning("Failed to set qBittorrent preferences: %s", exc)

    # Get existing categories
    existing_cats: dict = {}
    try:
        req = urllib.request.Request(
            f"{qbit_url}/api/v2/torrents/categories", headers=headers
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            existing_cats = json.loads(resp.read())
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        pass

    # Create categories
    for cat_name, save_path in QBIT_CATEGORIES.items():
        if cat_name in existing_cats:
            continue
        try:
            body = f"category={cat_name}&savePath={save_path}".encode()
            req = urllib.request.Request(
                f"{qbit_url}/api/v2/torrents/createCategory",
                data=body,
                headers={**headers, "Content-Type": "application/x-www-form-urlencoded"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=10)
        except (urllib.error.URLError, OSError):
            pass

    log.info(
        "qBittorrent categories configured: %s.",
        ", ".join(QBIT_CATEGORIES.keys()),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def setup(env: dict[str, str], script_dir: Path) -> bool:
    """Run post-deploy media app setup. Returns True on success."""
    config_base = resolve_config_base(
        env.get("CONFIG_ROOT", "./config"), script_dir
    )

    prowlarr_key = _read_api_key(config_base, "prowlarr")
    if prowlarr_key:
        setup_prowlarr_indexers("http://localhost:9696", prowlarr_key)
    else:
        log.warning("Prowlarr API key not found; skipping indexer setup.")

    setup_qbittorrent("http://localhost:8080", env)

    return True


def main() -> None:
    setup_logging()
    env_file = SCRIPT_DIR / ".env"
    if not env_file.is_file():
        log.error("No .env found.")
        sys.exit(1)
    env = load_env(env_file)
    setup(env, SCRIPT_DIR)


if __name__ == "__main__":
    main()
