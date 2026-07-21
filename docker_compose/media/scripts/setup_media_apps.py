#!/usr/bin/env python3
"""Post-deploy setup for media stack (*arr + qBittorrent + Prowlarr sync).

Configures all media-stack services via their APIs after docker compose up:
  - Prowlarr indexers + FlareSolverr proxy
  - qBittorrent preferences + categories (TRaSH guide)
  - Sonarr/Radarr root folders, download clients, TRaSH naming
  - Prowlarr app sync (connects Prowlarr → Sonarr/Radarr)

Called by deploy.py after the media stack starts, or run standalone:
  cd docker_compose/media && python3 setup_media_apps.py

Idempotent: skips resources that already exist.
"""

from __future__ import annotations

import json
import socket
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from xml.etree import ElementTree

SCRIPT_DIR = Path(__file__).resolve().parent
STACK_DIR = SCRIPT_DIR.parent
REPO_ROOT = STACK_DIR.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.homelab_common import load_env, log, resolve_config_base, setup_logging

HEALTH_TIMEOUT_S = 90
HEALTH_POLL_S = 5

# Fallbacks for settings introduced after the initial deploy. These MUST match
# the values in .env.example: a live .env is a copy taken when the stack was
# first set up and will not contain keys added later, so a fallback of "off"
# would make the setting a silent no-op on exactly the deployments that need
# it. An explicitly empty value in .env remains the opt-out.
DEFAULT_SAB_BANDWIDTH_MAX = "25M"      # bytes/sec -> 25 MB/s (~200 Mbps)
DEFAULT_QBIT_UPLOAD_MBPS = "5"         # decimal Mbps -> 625000 bytes/s

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
    {"name": "TorrentProject2", "definitionName": "torrentproject2"},
    {"name": "TorrentsCSV", "definitionName": "TorrentsCSV"},
    {"name": "showRSS", "definitionName": "showrss"},
    # Anime
    {"name": "Nyaa.si", "definitionName": "nyaasi", "anime": True,
     "fields": {"animeCategories": [5070]}},  # 5070 = Anime - English-translated
    {"name": "SubsPlease", "definitionName": "SubsPlease", "anime": True},
    {"name": "Tokyo Toshokan", "definitionName": "tokyotosho", "anime": True},
    {"name": "Shana Project", "definitionName": "shanaproject", "anime": True},
]

FLARESOLVERR_TAG_NAME = "flaresolverr"

# Sonarr tags used to keep anime episode searches off general torrent trackers (several sit
# behind FlareSolverr, and none of them carry anime — every search against them is a wasted,
# slow query). Mirrored onto series by quality profile: matching ANIME_PROFILE_NAME gets
# ANIME_TAG_NAME, everything else gets NON_ANIME_TAG_NAME. Usenet indexers (e.g. NZBgeek) are
# left untagged/universal — they're fast and do carry anime, so there's nothing to scope away.
ANIME_TAG_NAME = "anime"
NON_ANIME_TAG_NAME = "general"
ANIME_PROFILE_NAME = "[Anime] Remux-1080p"

# ---------------------------------------------------------------------------
# Usenet indexers to add to Prowlarr (require API keys from .env).
# Only added when ENABLE_SABNZBD=1 and the corresponding API key is set.
# ---------------------------------------------------------------------------

PROWLARR_USENET_INDEXERS = [
    {
        "name": "NZBgeek",
        "definitionName": "NZBgeek",
        "env_key": "NZBGEEK_API_KEY",
        "fields": {"apiKey": None},  # filled from env at runtime
    },
    # Different index source from NZBgeek, so it genuinely adds coverage rather than
    # duplicating hits. Both have open registration. Two usenet indexers is the
    # practical sweet spot — usenet indexers meter API hits, so more is not free.
    {
        "name": "NZBFinder",
        "definitionName": "NZBFinder",
        "env_key": "NZBFINDER_API_KEY",
        "fields": {"apiKey": None},
    },
]

# SABnzbd download categories per TRaSH guide (same structure as qBittorrent).
# Category dirs are relative to SABnzbd's Completed Download Folder.
# Ref: https://trash-guides.info/Downloaders/SABnzbd/Paths-and-Categories/
SABNZBD_CATEGORIES = {
    "tv": "tv",
    "movies": "movies",
    "anime": "anime",
}

SABNZBD_DL_HOST = "sabnzbd"
SABNZBD_DL_PORT = 8080

# qBittorrent categories per TRaSH guide.
# Ref: https://trash-guides.info/Downloaders/qBittorrent/
QBIT_CATEGORIES = {
    "tv": "completed/tv",
    "movies": "completed/movies",
    "anime": "completed/anime",
}

# Post-import categories: Sonarr/Radarr move a torrent here the moment it
# successfully imports (movieImportedCategory/tvImportedCategory below), and
# will not remove torrents in this category themselves. This is the safety
# gate for Cleanuparr's Download Cleaner seeding rules (see
# _print_cleanuparr_instructions) - only a torrent Sonarr/Radarr has
# confirmed importing ever lands here, so ratio/seed-time cleanup can never
# race an import. No dedicated save path: category change alone doesn't move
# files (Auto Torrent Management is off), it's purely a routing label.
QBIT_IMPORTED_CATEGORIES = {
    "tv-imported": "",
    "movies-imported": "",
}

# ---------------------------------------------------------------------------
# Sonarr / Radarr / Prowlarr configuration
# Root folders, download clients (qBittorrent), TRaSH naming, app sync.
# Replaces Buildarr (unmaintained, incompatible with Sonarr v4).
# ---------------------------------------------------------------------------

SONARR_ROOT_FOLDERS = ["/data/library/tv", "/data/library/anime"]
RADARR_ROOT_FOLDERS = ["/data/library/movies"]

# qBittorrent uses network_mode: service:vpn, so *arrs reach it at vpn:8080.
QBIT_DL_HOST = "vpn"
QBIT_DL_PORT = 8080

# TRaSH-recommended naming formats.
# Ref: https://trash-guides.info/Sonarr/Sonarr-recommended-naming-scheme/
SONARR_NAMING: dict[str, object] = {
    "renameEpisodes": True,
    "replaceIllegalCharacters": True,
    "multiEpisodeStyle": 5,  # prefixed-range
    "standardEpisodeFormat": (
        "{Series TitleYear} - S{season:00}E{episode:00} - "
        "{Episode CleanTitle} [{Custom Formats}{Quality Full}]"
        "{[MediaInfo VideoDynamicRangeType]}"
        "{[Mediainfo AudioCodec}{ Mediainfo AudioChannels]}"
        "{[MediaInfo VideoCodec]}{-Release Group}"
    ),
    "dailyEpisodeFormat": (
        "{Series TitleYear} - {Air-Date} - "
        "{Episode CleanTitle} [{Custom Formats}{Quality Full}]"
        "{[MediaInfo VideoDynamicRangeType]}"
        "{[Mediainfo AudioCodec}{ Mediainfo AudioChannels]}"
        "{[MediaInfo VideoCodec]}{-Release Group}"
    ),
    "animeEpisodeFormat": (
        "{Series TitleYear} - S{season:00}E{episode:00} - "
        "{absolute:000} - {Episode CleanTitle:90} "
        "{[Custom Formats]}{[Quality Full]}"
        "{[Mediainfo AudioCodec}{ Mediainfo AudioChannels]}"
        "{MediaInfo AudioLanguages}"
        "{[MediaInfo VideoDynamicRangeType]}"
        "[{Mediainfo VideoCodec }{MediaInfo VideoBitDepth}bit]"
        "{-Release Group}"
    ),
    "seriesFolderFormat": "{Series TitleYear} [tvdbid-{TvdbId}]",
    "seasonFolderFormat": "Season {season:00}",
}

# Ref: https://trash-guides.info/Radarr/Radarr-recommended-naming-scheme/
RADARR_NAMING: dict[str, object] = {
    "renameMovies": True,
    "replaceIllegalCharacters": True,
    "standardMovieFormat": (
        "{Movie CleanTitle} {(Release Year)} {edition-{Edition Tags}} "
        "[{Custom Formats}{Quality Full}]"
        "{[MediaInfo VideoDynamicRangeType]}"
        "{[Mediainfo AudioCodec}{ Mediainfo AudioChannels]}"
        "{[MediaInfo VideoCodec]}{-Release Group}"
    ),
    "movieFolderFormat": (
        "{Movie CleanTitle} ({Release Year}) [tmdbid-{TmdbId}]"
    ),
}

# TRaSH-recommended media management settings (Sonarr + Radarr).
# Enables hardlinks, media info analysis, subtitle/extra file imports,
# and defers proper/repack scoring to Custom Formats (Recyclarr syncs those).
# Ref: https://trash-guides.info/Sonarr/  https://trash-guides.info/Radarr/
MEDIA_MANAGEMENT: dict[str, object] = {
    "copyUsingHardlinks": True,
    "importExtraFiles": True,
    "extraFileExtensions": "srt,nfo,sub,idx,ass,ssa,smi",
    "enableMediaInfo": True,
    "downloadPropersAndRepacks": "doNotPrefer",
    "deleteEmptyFolders": True,
}

# Defaults for torrent indexer health settings.
# Applied to all torrent indexers in Sonarr/Radarr after Prowlarr sync.
# Ref: https://wiki.servarr.com/radarr/settings#torrent-tracker-configuration
TORRENT_MIN_SEEDERS_DEFAULT = 5
PROWLARR_SYNC_WAIT_S = 30

# ---------------------------------------------------------------------------
# Bazarr — subtitle automation (zero-credential providers only).
# Ref: https://trash-guides.info/Bazarr/  https://wiki.bazarr.media/
# ---------------------------------------------------------------------------

BAZARR_PROFILE_ID = 1
BAZARR_PROFILE_NAME = "English + Hebrew"
BAZARR_LANGUAGES = ["en", "he"]
# podnapisi removed upstream (Bazarr's own config.py: "delete podnapisi section since this
# provider doesn't exist anymore") — enabling it here is silently dropped with no log line,
# no error, nothing. Confirmed gone, not renamed: no podnapisi.py anywhere in Bazarr's
# subliminal/subliminal_patch provider directories.

# Anime gets its own English-only profile — see _bazarr_wanted_profile().
BAZARR_ANIME_PROFILE_ID = 2
BAZARR_ANIME_PROFILE_NAME = "English (Anime)"
BAZARR_ANIME_LANGUAGES = ["en"]

# Zero-credential providers, always enabled.  subf2m is the Subscene successor —
# a large English database that broadens release/hash matching (more providers =>
# higher chance of a high-scoring match; Bazarr queries them in parallel and
# keeps the best by score).
BAZARR_PROVIDERS = ["gestdown", "wizdom", "yifysubtitles", "animetosho", "subf2m"]

# OpenSubtitles.com (NOT .org) needs a free account, unlike the providers above,
# so it is only enabled when OPENSUBTITLES_USERNAME/PASSWORD are set in .env.  It
# is the practical fix for anime that lacks embedded English subs: animetosho,
# Bazarr's anime-native provider, is dead without an AniDB API client we do not
# provision, and OpenSubtitles.com covers anime (and the whole library) directly.
BAZARR_OPENSUBTITLESCOM = "opensubtitlescom"


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


def _read_bazarr_api_key(config_base: Path) -> str | None:
    """Read Bazarr API key from its config.yaml (simple line parse, no PyYAML)."""
    cfg = config_base / "bazarr" / "config" / "config.yaml"
    if not cfg.is_file():
        return None
    in_auth = False
    for line in cfg.read_text().splitlines():
        stripped = line.strip()
        if stripped == "auth:":
            in_auth = True
        elif in_auth and stripped.startswith("apikey:"):
            return stripped.split(":", 1)[1].strip().strip("'\"")
        elif not line.startswith((" ", "\t")) and stripped:
            in_auth = False
    return None


def _bazarr_form_post(url: str, apikey: str,
                      form: dict[str, str | list[str]]) -> int | None:
    """POST form-encoded data to Bazarr's API. Returns HTTP status or None."""
    full_url = f"{url}?apikey={apikey}"
    body = urllib.parse.urlencode(form, doseq=True).encode()
    req = urllib.request.Request(
        full_url, data=body, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        log.debug("Bazarr POST %s → HTTP %s", url, exc.code)
        return exc.code
    except (urllib.error.URLError, OSError) as exc:
        log.debug("Bazarr POST %s → %s", url, exc)
        return None


def _bazarr_get(url: str, apikey: str) -> dict | list | None:
    """GET JSON from Bazarr's API."""
    # url may already carry a query string (e.g. _bazarr_list's start/length
    # params) — a bare "?apikey=" there produces a second "?", which Bazarr
    # rejects with 401 instead of a parse error, and callers treat that
    # exactly like "no data" rather than "auth was malformed".
    sep = "&" if "?" in url else "?"
    full_url = f"{url}{sep}apikey={apikey}"
    req = urllib.request.Request(full_url)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except (urllib.error.HTTPError, urllib.error.URLError, OSError,
            json.JSONDecodeError) as exc:
        log.debug("Bazarr GET %s → %s", url, exc)
        return None


def _api(url: str, headers: dict, *, method: str = "GET",
         data: dict | None = None) -> dict | list | None:
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        log.debug("API %s %s → HTTP %s: %s", method, url, exc.code, detail)
        return None
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
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
    """Fetch the schema for a specific indexer from Prowlarr.

    Matches definitionName exactly first, then falls back to a case-insensitive
    match on definitionName or name. Prowlarr's preset names are inconsistently
    cased (e.g. "NZBgeek" vs "NZBGeek"), and an exact-only match fails silently —
    the indexer just never gets added, with no error anywhere.
    """
    schemas = _api(f"{prowlarr_url}/api/v1/indexer/schema", headers)
    if not schemas:
        return None

    for s in schemas:
        if s.get("definitionName") == definition_name:
            return s

    wanted = definition_name.casefold()
    for s in schemas:
        if s.get("definitionName", "").casefold() == wanted:
            return s
    for s in schemas:
        if s.get("name", "").casefold() == wanted:
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


def setup_prowlarr_indexers(prowlarr_url: str, api_key: str) -> bool:
    """Add FlareSolverr proxy and public torrent indexers to Prowlarr.

    Returns False only when Prowlarr itself is unreachable/unauthenticated
    (nothing at all could be configured). Individual indexer add failures
    (e.g. a tracker that's Cloudflare-banned) are logged but non-fatal —
    they're expected to happen occasionally and don't indicate a broken
    deploy.
    """
    headers = {"X-Api-Key": api_key, "Content-Type": "application/json"}

    if not _wait_for_service("Prowlarr", f"{prowlarr_url}/api/v1/health", headers):
        return False

    # Set up FlareSolverr proxy first — some indexers need it
    fs_tag_id = _ensure_flaresolverr_proxy(prowlarr_url, headers)

    existing = _api(f"{prowlarr_url}/api/v1/indexer", headers)
    if existing is None:
        log.warning("Could not list Prowlarr indexers.")
        return False
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

        field_overrides = idx_def.get("fields", {})
        if field_overrides:
            for field in schema.get("fields", []):
                name = field.get("name", "")
                if name in field_overrides:
                    field["value"] = field_overrides[name]

        result = _api(f"{prowlarr_url}/api/v1/indexer", headers, method="POST", data=schema)
        if result and result.get("id"):
            added += 1
        else:
            log.debug("Failed to add indexer %s", idx_def["name"])
            failed += 1

    log.info(
        "Prowlarr torrent indexers: %d added, %d already existed, %d failed.",
        added, skipped, failed,
    )
    return True


def setup_prowlarr_usenet_indexers(
    prowlarr_url: str, api_key: str, env: dict[str, str],
) -> None:
    """Add Usenet indexers (e.g. NZBGeek) to Prowlarr when API keys are set."""
    headers = {"X-Api-Key": api_key, "Content-Type": "application/json"}

    existing = _api(f"{prowlarr_url}/api/v1/indexer", headers)
    if existing is None:
        log.warning("Could not list Prowlarr indexers for Usenet setup.")
        return
    # Match on BOTH name and definitionName. Prowlarr stores every Newznab-preset
    # indexer with definitionName="Newznab" (the preset name survives only in
    # `name`), so a definitionName-only check never recognises NZBgeek as already
    # present: each run re-POSTs it, Prowlarr rejects the duplicate, and the run
    # reports a bogus "1 failed" while the indexer sits there working fine.
    existing_names = {
        value.casefold()
        for idx in existing
        for value in (idx.get("definitionName", ""), idx.get("name", ""))
        if value
    }

    added = 0
    skipped = 0
    failed = 0
    for idx_def in PROWLARR_USENET_INDEXERS:
        env_key = idx_def["env_key"]
        api_key_value = env.get(env_key, "").strip()
        if not api_key_value:
            log.info(
                "Skipping Prowlarr Usenet indexer %s (%s not set in .env).",
                idx_def["name"], env_key,
            )
            continue

        if {idx_def["definitionName"].casefold(), idx_def["name"].casefold()} & existing_names:
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
        schema["tags"] = []

        for key in ("id", "presets"):
            schema.pop(key, None)

        field_overrides = dict(idx_def.get("fields", {}))
        for field_name, val in field_overrides.items():
            if val is None:
                field_overrides[field_name] = api_key_value

        for field in schema.get("fields", []):
            name = field.get("name", "")
            if name in field_overrides:
                field["value"] = field_overrides[name]

        result = _api(
            f"{prowlarr_url}/api/v1/indexer", headers,
            method="POST", data=schema,
        )
        if result and result.get("id"):
            added += 1
        else:
            log.debug("Failed to add Usenet indexer %s", idx_def["name"])
            failed += 1

    if added or skipped or failed:
        log.info(
            "Prowlarr Usenet indexers: %d added, %d already existed, %d failed.",
            added, skipped, failed,
        )


# ---------------------------------------------------------------------------
# qBittorrent setup
# ---------------------------------------------------------------------------


def _qbit_upload_limit_bytes(env: dict[str, str]) -> int:
    """Global upload cap for qBittorrent, converted from Mbps to bytes/s.

    The setting is expressed in Mbps because that is the unit the WAN link is
    reasoned about in — qBittorrent's own UI uses KiB/s, and having to convert
    between the two is how you end up off by 8x. Mbps here is decimal (1 Mbps
    = 1e6 bits/s), matching how ISPs quote line rates.

    qBittorrent's up_limit is bytes/s and treats <= 0 as unlimited.

    Unset falls back to the documented default so that a deployment whose .env
    predates this setting still gets capped; an explicitly EMPTY value is the
    opt-out. A typo also yields unlimited — qBittorrent's own default is a
    safer landing spot than throttling seeding to an arbitrary crawl.
    """
    raw = env.get("QBITTORRENT_UPLOAD_LIMIT_MBPS", DEFAULT_QBIT_UPLOAD_MBPS).strip()
    if not raw:
        return 0
    try:
        mbps = float(raw)
    except ValueError:
        log.warning(
            "QBITTORRENT_UPLOAD_LIMIT_MBPS=%r is not a number; "
            "leaving upload unlimited.", raw,
        )
        return 0
    return int(mbps * 1_000_000 / 8) if mbps > 0 else 0


def setup_qbittorrent(qbit_url: str, env: dict[str, str]) -> bool:
    """Configure qBittorrent categories and settings per TRaSH guide.

    Returns False only if we could never authenticate to qBittorrent at all
    (nothing could be configured). Preference/category write failures after
    a successful login are logged but non-fatal — qBittorrent auto-creates
    categories on first torrent add even if the pre-seed here didn't take.
    """
    username = env.get("QBITTORRENT_USERNAME", "admin")
    password = env.get("QBITTORRENT_PASSWORD", "adminadmin")
    login_headers = {"Content-Type": "application/x-www-form-urlencoded"}

    # Wait for qBittorrent to be reachable
    deadline = time.monotonic() + HEALTH_TIMEOUT_S
    session_cookie = None
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
                if "Ok" in body or resp.status in (200, 204):
                    for cookie in resp.headers.get_all("Set-Cookie") or []:
                        name, _, rest = cookie.partition("=")
                        # Cookie name varies by version/port, e.g. QBT_SID_8080.
                        if "SID" in name.upper():
                            session_cookie = f"{name}={rest.split(';')[0]}"
                    break
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(HEALTH_POLL_S)

    if session_cookie is None:
        log.warning("Could not authenticate to qBittorrent. Skipping config.")
        return False

    headers = {"Cookie": session_cookie}

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
        # Upload is the scarce direction on an asymmetric residential line, and
        # it is the one Plex needs to serve remote streams. Uncapped seeding
        # starves playback long before it saturates the downstream.
        # qBittorrent wants bytes/s here; the .env value is KiB/s.
        "up_limit": _qbit_upload_limit_bytes(env),
        "max_active_downloads": 5,
        "max_active_uploads": 5,
        "max_active_torrents": 10,
        # --- BitTorrent / Privacy ---
        "encryption": 0,            # 0 = allow (prefer), not force
        "anonymous_mode": False,    # worse speeds; issues with private trackers
        # --- Seeding ---
        # No private trackers in use (checked tracker list: opentrackr, dler.org,
        # demonii, internetwarriors, exodus, bittor.pw, stealth.si - all public).
        # act=0 pauses (not act=3/remove+files): a global ratio/time timer can't
        # know whether Sonarr/Radarr has imported yet, so it races imports.
        # Actual disk reclaim is category-gated via Cleanuparr instead - see
        # "Post-import cleanup design" in docs/Chapter3c-media-stack.md.
        "max_ratio_enabled": True,
        "max_ratio": 1,
        "max_ratio_act": 0,
        "max_seeding_time_enabled": True,
        "max_seeding_time": 1440,
        "max_inactive_seeding_time_enabled": True,
        "max_inactive_seeding_time": 1440,
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
    all_categories = {**QBIT_CATEGORIES, **QBIT_IMPORTED_CATEGORIES}
    for cat_name, save_path in all_categories.items():
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
        except (urllib.error.URLError, OSError) as exc:
            log.warning("Failed to create qBittorrent category %s: %s", cat_name, exc)

    log.info(
        "qBittorrent categories configured: %s.",
        ", ".join(all_categories.keys()),
    )
    return True


# ---------------------------------------------------------------------------
# SABnzbd setup
# ---------------------------------------------------------------------------


def _sabnzbd_api(
    base_url: str, api_key: str, mode: str,
    extra_params: dict[str, str] | None = None,
) -> dict | None:
    """Call SABnzbd API (mode-based, not REST)."""
    params: dict[str, str] = {
        "mode": mode,
        "apikey": api_key,
        "output": "json",
    }
    if extra_params:
        params.update(extra_params)
    url = f"{base_url}/api?{urllib.parse.urlencode(params)}"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return None


def setup_sabnzbd(sabnzbd_url: str, config_base: Path, env: dict[str, str]) -> bool:
    """Verify SABnzbd configuration and ensure categories exist via API.

    The bootstrap pre-seeds sabnzbd.ini with server + categories + paths.
    This function acts as a post-start verification layer: it confirms the
    server is reachable and creates any missing categories via API (e.g.,
    if SABnzbd was already running before the INI was seeded).

    Returns False if SABnzbd's API key is missing or it never becomes
    reachable — i.e. this function did no verification at all, which
    matters because the caller only calls this when the user explicitly
    turned Usenet on (ENABLE_SABNZBD=1).
    """
    api_key = _read_sabnzbd_api_key(config_base)
    if not api_key:
        log.warning("SABnzbd API key not found; skipping SABnzbd config verification.")
        return False

    deadline = time.monotonic() + HEALTH_TIMEOUT_S
    config: dict | None = None
    while time.monotonic() < deadline:
        config = _sabnzbd_api(sabnzbd_url, api_key, "get_config")
        if config and config.get("config"):
            break
        config = None
        time.sleep(HEALTH_POLL_S)

    if not config:
        log.warning("SABnzbd not ready within %ds; skipping config verification.", HEALTH_TIMEOUT_S)
        return False

    sab_config = config["config"]

    servers = sab_config.get("servers", [])
    if servers:
        enabled = [s for s in servers if str(s.get("enable", "0")) == "1"]
        log.info(
            "SABnzbd: %d server(s) configured (%d enabled).",
            len(servers), len(enabled),
        )
    else:
        log.warning(
            "SABnzbd: no servers configured. Add your Usenet provider "
            "in the SABnzbd UI or set USENET_SERVER_* in .env and re-deploy."
        )

    # Download rate cap. This is the path that reaches an EXISTING deployment:
    # the bootstrap's ini seed returns early once sabnzbd.ini exists, so a
    # template-only change would silently never apply to a live box.
    #
    # bandwidth_max is BYTES/sec — "25M" is 25 MB/s (~200 Mbps), not 25 Mb/s.
    # SABnzbd downloads at bandwidth_perc% of it; that stays at its 100 default.
    desired_bw = env.get("SABNZBD_BANDWIDTH_MAX", DEFAULT_SAB_BANDWIDTH_MAX).strip()
    current_bw = str(sab_config.get("misc", {}).get("bandwidth_max", "")).strip()
    if current_bw == desired_bw:
        log.info("SABnzbd: download cap already %s.", desired_bw or "unlimited")
    elif _sabnzbd_api(
        sabnzbd_url, api_key, "set_config",
        {"section": "misc", "keyword": "bandwidth_max", "value": desired_bw},
    ):
        log.info(
            "SABnzbd: download cap set to %s (was %s).",
            desired_bw or "unlimited", current_bw or "unlimited",
        )
    else:
        log.warning("Failed to set SABnzbd download cap to %s.", desired_bw)

    existing_cats = {
        c.get("name", "").lower() for c in sab_config.get("categories", [])
    }
    added = 0
    for cat_name, cat_dir in SABNZBD_CATEGORIES.items():
        if cat_name.lower() in existing_cats:
            continue
        result = _sabnzbd_api(
            sabnzbd_url, api_key, "set_config",
            {
                "section": "categories",
                "keyword": cat_name,
                "name": cat_name,
                "pp": "3",
                "dir": cat_dir,
                "script": "Default",
                "priority": "-100",
                "newzbin": cat_name,
            },
        )
        if result:
            added += 1
        else:
            log.warning("Failed to add SABnzbd category %s.", cat_name)

    if added:
        log.info("SABnzbd: %d categories added via API (%s).",
                 added, ", ".join(SABNZBD_CATEGORIES.keys()))
    else:
        log.info("SABnzbd: categories already configured (%s).",
                 ", ".join(SABNZBD_CATEGORIES.keys()))
    return True


# ---------------------------------------------------------------------------
# Sonarr / Radarr setup (naming, root folders, download client)
# ---------------------------------------------------------------------------


def _setup_naming(app_name: str, base_url: str, headers: dict,
                  naming: dict[str, object]) -> None:
    """Update *arr naming config by merging into existing settings."""
    url = f"{base_url}/api/v3/config/naming"
    current = _api(url, headers)
    if current is None:
        log.warning("Could not read %s naming config.", app_name)
        return
    current.update(naming)
    result = _api(url, headers, method="PUT", data=current)
    if result is not None:
        log.info("%s naming config updated (TRaSH scheme).", app_name)
    else:
        log.warning("Failed to update %s naming config.", app_name)


def _setup_media_management(app_name: str, base_url: str,
                            headers: dict) -> None:
    """Configure media management settings per TRaSH guide."""
    url = f"{base_url}/api/v3/config/mediamanagement"
    current = _api(url, headers)
    if current is None:
        log.warning("Could not read %s media management config.", app_name)
        return
    current.update(MEDIA_MANAGEMENT)
    result = _api(url, headers, method="PUT", data=current)
    if result is not None:
        log.info(
            "%s media management updated (hardlinks, media info, extras).",
            app_name,
        )
    else:
        log.warning("Failed to update %s media management config.", app_name)


def _setup_root_folders(app_name: str, base_url: str, headers: dict,
                        folders: list[str]) -> None:
    """Add root folders idempotently (skips existing paths)."""
    url = f"{base_url}/api/v3/rootfolder"
    existing = _api(url, headers)
    existing_paths: set[str] = set()
    if existing:
        existing_paths = {rf.get("path", "") for rf in existing}

    added = 0
    for folder in folders:
        if folder in existing_paths:
            continue
        result = _api(url, headers, method="POST", data={"path": folder})
        if result and result.get("id"):
            added += 1
        else:
            log.warning("Failed to add %s root folder %s.", app_name, folder)

    if added:
        log.info("%s root folders: %d added.", app_name, added)
    else:
        log.info("%s root folders already configured.", app_name)


def _setup_download_client_qbit(app_name: str, base_url: str, headers: dict,
                                category: str, imported_category: str,
                                env: dict[str, str],
                                priority: int = 1) -> None:
    """Add qBittorrent download client via schema (idempotent).

    imported_category is the post-import routing category (see
    QBIT_IMPORTED_CATEGORIES) - the safety gate that lets Cleanuparr's
    Download Cleaner reclaim disk space without racing an import. Details:
    docs/Chapter3c-media-stack.md, "Post-import cleanup design".
    """
    url = f"{base_url}/api/v3/downloadclient"
    existing = _api(url, headers)
    if existing:
        for dc in existing:
            if dc.get("implementation") == "QBittorrent":
                changed = False
                for flag in ("removeCompletedDownloads", "removeFailedDownloads"):
                    if not dc.get(flag):
                        dc[flag] = True
                        changed = True
                if dc.get("priority", 1) != priority:
                    dc["priority"] = priority
                    changed = True
                for field in dc.get("fields", []):
                    name = field.get("name", "")
                    if "category" in name.lower() and "imported" in name.lower():
                        if field.get("value") != imported_category:
                            field["value"] = imported_category
                            changed = True
                if changed:
                    if _api(f"{url}/{dc['id']}", headers,
                            method="PUT", data=dc) is not None:
                        log.info(
                            "%s qBittorrent download client updated "
                            "(priority=%d, remove completed/failed).",
                            app_name, priority,
                        )
                    else:
                        log.warning(
                            "%s qBittorrent download client update FAILED "
                            "(wanted priority=%d); settings remain as-is.",
                            app_name, priority,
                        )
                else:
                    log.info(
                        "%s qBittorrent download client already configured.",
                        app_name,
                    )
                return

    schemas = _api(f"{url}/schema", headers)
    if not schemas:
        log.warning("Could not fetch %s download client schemas.", app_name)
        return

    schema = None
    for s in schemas:
        if s.get("implementation") == "QBittorrent":
            schema = dict(s)
            break
    if schema is None:
        log.warning("QBittorrent schema not found in %s.", app_name)
        return

    schema["name"] = "qBittorrent"
    schema["enable"] = True
    schema["priority"] = priority
    schema["removeCompletedDownloads"] = True
    schema["removeFailedDownloads"] = True
    schema.pop("id", None)
    schema.pop("presets", None)

    field_overrides = {
        "host": QBIT_DL_HOST,
        "port": QBIT_DL_PORT,
        "username": env.get("QBITTORRENT_USERNAME", "admin"),
        "password": env.get("QBITTORRENT_PASSWORD", "adminadmin"),
    }
    for field in schema.get("fields", []):
        name = field.get("name", "")
        if name in field_overrides:
            field["value"] = field_overrides[name]
        elif "category" in name.lower() and "imported" in name.lower():
            field["value"] = imported_category
        elif "category" in name.lower():
            field["value"] = category

    result = _api(url, headers, method="POST", data=schema)
    if result and result.get("id"):
        log.info(
            "%s qBittorrent download client added (category=%s, imported=%s, priority=%d).",
            app_name, category, imported_category, priority,
        )
    else:
        log.warning("Failed to add qBittorrent download client to %s.", app_name)


def _read_sabnzbd_api_key(config_base: Path) -> str | None:
    """Read api_key from SABnzbd's sabnzbd.ini (pre-seeded by bootstrap)."""
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


def _setup_download_client_sabnzbd(
    app_name: str, base_url: str, headers: dict,
    category: str, sabnzbd_api_key: str,
    priority: int = 1,
) -> None:
    """Add SABnzbd download client via schema (idempotent)."""
    url = f"{base_url}/api/v3/downloadclient"
    existing = _api(url, headers)
    if existing:
        for dc in existing:
            if dc.get("implementation") == "Sabnzbd":
                changed = False
                for flag in ("removeCompletedDownloads", "removeFailedDownloads"):
                    if not dc.get(flag):
                        dc[flag] = True
                        changed = True
                if dc.get("priority", 1) != priority:
                    dc["priority"] = priority
                    changed = True
                # Re-seeding sabnzbd.ini mints a NEW random API key. Without this,
                # the *arr would keep the stale key and every grab would fail auth
                # against SABnzbd, with nothing in the logs pointing at the cause.
                for field in dc.get("fields", []):
                    if field.get("name") == "apiKey" and field.get("value") != sabnzbd_api_key:
                        field["value"] = sabnzbd_api_key
                        changed = True
                        # Stated as intent, not outcome: the PUT below is what
                        # actually persists it, and it can fail.
                        log.info("%s SABnzbd API key had rotated; updating.", app_name)
                if changed:
                    if _api(f"{url}/{dc['id']}", headers,
                            method="PUT", data=dc) is not None:
                        log.info(
                            "%s SABnzbd download client updated "
                            "(priority=%d, remove completed/failed).",
                            app_name, priority,
                        )
                    else:
                        log.warning(
                            "%s SABnzbd download client update FAILED "
                            "(wanted priority=%d). If the API key had rotated "
                            "it is still stale, and every grab will fail auth.",
                            app_name, priority,
                        )
                else:
                    log.info(
                        "%s SABnzbd download client already configured.",
                        app_name,
                    )
                return

    schemas = _api(f"{url}/schema", headers)
    if not schemas:
        log.warning("Could not fetch %s download client schemas.", app_name)
        return

    schema = None
    for s in schemas:
        if s.get("implementation") == "Sabnzbd":
            schema = dict(s)
            break
    if schema is None:
        log.warning("Sabnzbd schema not found in %s.", app_name)
        return

    schema["name"] = "SABnzbd"
    schema["enable"] = True
    schema["priority"] = priority
    schema["removeCompletedDownloads"] = True
    schema["removeFailedDownloads"] = True
    schema.pop("id", None)
    schema.pop("presets", None)

    field_overrides = {
        "host": SABNZBD_DL_HOST,
        "port": SABNZBD_DL_PORT,
        "apiKey": sabnzbd_api_key,
    }
    for field in schema.get("fields", []):
        name = field.get("name", "")
        if name in field_overrides:
            field["value"] = field_overrides[name]
        elif "category" in name.lower() and "imported" not in name.lower():
            field["value"] = category

    result = _api(url, headers, method="POST", data=schema)
    if result and result.get("id"):
        log.info(
            "%s SABnzbd download client added (category=%s, priority=%d).",
            app_name, category, priority,
        )
    else:
        log.warning("Failed to add SABnzbd download client to %s.", app_name)


def _setup_delay_profile(
    app_name: str, base_url: str, headers: dict, env: dict[str, str],
) -> None:
    """Make Sonarr/Radarr actually prefer Usenet releases over torrents.

    Download-client priority does NOT do this. In Sonarr's DownloadDecisionComparer,
    client priority only breaks ties between clients of the *same* protocol; protocol
    preference comes solely from the delay profile's `preferredProtocol`. So setting
    SABnzbd to priority 1 and qBittorrent to 2 (which we also do, for tie-breaking
    among usenet clients) has zero effect on usenet-vs-torrent on its own.

    Delays are in minutes and are compared against the release's *age*, not against
    how long the *arr has known about it. They gate RSS sync only — interactive
    searches ignore them entirely, so a manual search still returns torrents at once.

    Ref: https://wiki.servarr.com/sonarr/settings#delay-profiles
    """
    url = f"{base_url}/api/v3/delayprofile"
    profiles = _api(url, headers)
    if not profiles:
        log.warning("Could not read %s delay profiles; skipping protocol preference.", app_name)
        return

    # The default catch-all profile is the untagged one — it always exists and
    # applies to everything not matched by a more specific tagged profile.
    default = next((p for p in profiles if not p.get("tags")), None)
    if default is None:
        log.warning("%s has no default (untagged) delay profile; skipping.", app_name)
        return

    def _minutes(var: str, fallback: int) -> int:
        try:
            return int(env.get(var, "").strip() or fallback)
        except ValueError:
            log.warning("%s is not an integer; using %d.", var, fallback)
            return fallback

    usenet_delay = _minutes("USENET_DELAY_MINUTES", 0)
    torrent_delay = _minutes("TORRENT_DELAY_MINUTES", 60)

    desired = {
        "enableUsenet": True,
        "enableTorrent": True,
        "preferredProtocol": "usenet",
        "usenetDelay": usenet_delay,
        "torrentDelay": torrent_delay,
    }
    if all(default.get(key) == val for key, val in desired.items()):
        log.info("%s delay profile already prefers Usenet.", app_name)
        return

    default.update(desired)
    result = _api(f"{url}/{default['id']}", headers, method="PUT", data=default)
    if result is not None:
        log.info(
            "%s delay profile updated: prefer Usenet "
            "(usenet delay %dm, torrent delay %dm).",
            app_name, usenet_delay, torrent_delay,
        )
    else:
        log.warning("Failed to update %s delay profile.", app_name)


def setup_sonarr(sonarr_url: str, api_key: str, env: dict[str, str],
                 sabnzbd_api_key: str | None = None) -> bool:
    """Configure Sonarr: TRaSH naming, media management, root folders, download clients.

    Returns False only if Sonarr itself never became reachable (nothing was
    configured at all). Individual settings writes (naming, media
    management, root folders, download clients, delay profile) log their
    own warnings on failure but don't fail the whole run — they're
    independently retryable on the next deploy.
    """
    headers = {"X-Api-Key": api_key, "Content-Type": "application/json"}
    if not _wait_for_service("Sonarr", f"{sonarr_url}/api/v3/health", headers):
        return False
    _setup_naming("Sonarr", sonarr_url, headers, SONARR_NAMING)
    _setup_media_management("Sonarr", sonarr_url, headers)
    _setup_root_folders("Sonarr", sonarr_url, headers, SONARR_ROOT_FOLDERS)
    qbit_priority = 2 if sabnzbd_api_key else 1
    _setup_download_client_qbit("Sonarr", sonarr_url, headers, "tv", "tv-imported", env, qbit_priority)
    if sabnzbd_api_key:
        _setup_download_client_sabnzbd("Sonarr", sonarr_url, headers, "tv", sabnzbd_api_key, 1)
        # Only touch the delay profile when Usenet is actually available — otherwise
        # we would tell a torrent-only stack to prefer a protocol it cannot use.
        _setup_delay_profile("Sonarr", sonarr_url, headers, env)
    return True


def setup_radarr(radarr_url: str, api_key: str, env: dict[str, str],
                 sabnzbd_api_key: str | None = None) -> bool:
    """Configure Radarr: TRaSH naming, media management, root folders, download clients.

    Returns False only if Radarr itself never became reachable (nothing was
    configured at all). See setup_sonarr for why sub-step failures don't
    fail the whole run.
    """
    headers = {"X-Api-Key": api_key, "Content-Type": "application/json"}
    if not _wait_for_service("Radarr", f"{radarr_url}/api/v3/health", headers):
        return False
    _setup_naming("Radarr", radarr_url, headers, RADARR_NAMING)
    _setup_media_management("Radarr", radarr_url, headers)
    _setup_root_folders("Radarr", radarr_url, headers, RADARR_ROOT_FOLDERS)
    qbit_priority = 2 if sabnzbd_api_key else 1
    _setup_download_client_qbit("Radarr", radarr_url, headers, "movies", "movies-imported", env, qbit_priority)
    if sabnzbd_api_key:
        _setup_download_client_sabnzbd("Radarr", radarr_url, headers, "movies", sabnzbd_api_key, 1)
        # See Sonarr above: delay profile is what makes Usenet actually win over torrents.
        _setup_delay_profile("Radarr", radarr_url, headers, env)
    return True


# ---------------------------------------------------------------------------
# Prowlarr app sync (connect Prowlarr → Sonarr / Radarr)
# ---------------------------------------------------------------------------


def setup_prowlarr_apps(prowlarr_url: str, prowlarr_key: str,
                        sonarr_key: str | None,
                        radarr_key: str | None) -> None:
    """Add Sonarr/Radarr as applications in Prowlarr for indexer sync."""
    headers = {"X-Api-Key": prowlarr_key, "Content-Type": "application/json"}

    existing = _api(f"{prowlarr_url}/api/v1/applications", headers)
    if existing is None:
        log.warning("Could not list Prowlarr applications.")
        return
    existing_impls = {
        app.get("implementation", "").lower() for app in existing
    }

    schemas = _api(f"{prowlarr_url}/api/v1/applications/schema", headers)
    if not schemas:
        log.warning("Could not fetch Prowlarr application schemas.")
        return

    apps_to_add: list[tuple[str, str, str]] = []
    if sonarr_key and "sonarr" not in existing_impls:
        apps_to_add.append(("Sonarr", sonarr_key, "http://sonarr:8989"))
    if radarr_key and "radarr" not in existing_impls:
        apps_to_add.append(("Radarr", radarr_key, "http://radarr:7878"))

    if not apps_to_add:
        log.info("Prowlarr app sync already configured.")
        return

    for app_name, api_key, base_url in apps_to_add:
        schema = None
        for s in schemas:
            if s.get("implementation") == app_name:
                schema = dict(s)
                break
        if not schema:
            log.warning("No Prowlarr schema found for %s.", app_name)
            continue

        schema["name"] = app_name
        schema.pop("id", None)
        schema.pop("presets", None)

        field_values = {
            "prowlarrUrl": "http://prowlarr:9696",
            "baseUrl": base_url,
            "apiKey": api_key,
            "syncLevel": "fullSync",
        }
        for field in schema.get("fields", []):
            name = field.get("name", "")
            if name in field_values:
                field["value"] = field_values[name]

        result = _api(
            f"{prowlarr_url}/api/v1/applications", headers,
            method="POST", data=schema,
        )
        if result and result.get("id"):
            log.info("Added %s to Prowlarr app sync.", app_name)
        else:
            log.warning("Failed to add %s to Prowlarr app sync.", app_name)


# ---------------------------------------------------------------------------
# Torrent indexer health defaults (Sonarr / Radarr)
# ---------------------------------------------------------------------------


def _configure_arr_indexers(app_name: str, base_url: str, headers: dict,
                            env: dict[str, str]) -> None:
    """Set minimum seeders and seed criteria on all torrent indexers.

    After Prowlarr syncs indexers to Sonarr/Radarr, this applies the
    TORRENT_MIN_SEEDERS / TORRENT_SEED_RATIO / TORRENT_SEED_TIME defaults
    from .env to every torrent indexer.  Polls briefly for indexers to
    appear if Prowlarr sync is still propagating.
    """
    try:
        min_seeders = int(env.get("TORRENT_MIN_SEEDERS",
                                  str(TORRENT_MIN_SEEDERS_DEFAULT)))
    except ValueError:
        min_seeders = TORRENT_MIN_SEEDERS_DEFAULT

    seed_ratio_str = env.get("TORRENT_SEED_RATIO", "")
    seed_ratio: float | None = None
    if seed_ratio_str:
        try:
            seed_ratio = float(seed_ratio_str)
        except ValueError:
            pass

    seed_time_str = env.get("TORRENT_SEED_TIME", "")
    seed_time: int | None = None
    if seed_time_str:
        try:
            seed_time = int(seed_time_str)
        except ValueError:
            pass

    url = f"{base_url}/api/v3/indexer"
    deadline = time.monotonic() + PROWLARR_SYNC_WAIT_S
    indexers: list | None = None
    while time.monotonic() < deadline:
        indexers = _api(url, headers)
        if indexers:
            break
        time.sleep(HEALTH_POLL_S)

    if not indexers:
        log.info(
            "%s: no indexers found (Prowlarr sync may not have run yet); "
            "re-run setup_media_apps.py to apply torrent health defaults.",
            app_name,
        )
        return

    torrent_indexers = [i for i in indexers if i.get("protocol") == "torrent"]
    if not torrent_indexers:
        log.info("%s: no torrent indexers to configure.", app_name)
        return

    updated = 0
    for idx in torrent_indexers:
        seeders_changed = idx.get("minimumSeeders") != min_seeders

        seed_criteria = idx.get("seedCriteria") or {}
        criteria_changed = False
        if seed_ratio is not None and seed_criteria.get("seedRatio") != seed_ratio:
            seed_criteria["seedRatio"] = seed_ratio
            criteria_changed = True
        if seed_time is not None and seed_criteria.get("seedTime") != seed_time:
            seed_criteria["seedTime"] = seed_time
            criteria_changed = True

        if not seeders_changed and not criteria_changed:
            continue

        if seeders_changed:
            idx["minimumSeeders"] = min_seeders
        if criteria_changed:
            idx["seedCriteria"] = seed_criteria

        # forceSave=true: without it, Sonarr re-tests the indexer's connectivity on every PUT
        # and rejects the whole update (tags included) if that indexer happens to be
        # erroring/rate-limited at that moment — unrelated to the field actually being changed.
        result = _api(
            f"{url}/{idx['id']}?forceSave=true", headers, method="PUT", data=idx,
        )
        if result is not None:
            updated += 1
        else:
            log.debug(
                "Failed to update indexer %s in %s.", idx.get("name"), app_name,
            )

    extras = []
    if seed_ratio is not None:
        extras.append(f"seedRatio={seed_ratio}")
    if seed_time is not None:
        extras.append(f"seedTime={seed_time}m")
    extra_str = (", " + ", ".join(extras)) if extras else ""

    if updated:
        log.info(
            "%s: updated %d torrent indexer(s) (minimumSeeders=%d%s).",
            app_name, updated, min_seeders, extra_str,
        )
    else:
        log.info(
            "%s: torrent indexer defaults already applied (%d indexer(s)).",
            app_name, len(torrent_indexers),
        )


def _get_or_create_tag(base_url: str, headers: dict, label: str) -> int | None:
    """Get or create a Sonarr/Radarr tag (v3 API), returning its ID."""
    tags = _api(f"{base_url}/api/v3/tag", headers)
    if tags:
        for t in tags:
            if t.get("label", "").lower() == label.lower():
                return t["id"]
    result = _api(f"{base_url}/api/v3/tag", headers, method="POST", data={"label": label})
    return result.get("id") if isinstance(result, dict) else None


def _tag_anime_indexer_scoping(sonarr_url: str, headers: dict) -> None:
    """Restrict anime episode searches to anime-native indexers.

    Sonarr only excludes an indexer for a series when the two share no tags at all — an
    untagged indexer is used for every series regardless of that series' own tags. So tagging
    only the anime-native indexers isn't enough: general torrent trackers would stay untagged
    (universal) and still get queried for anime. This tags both sides of the split — anime-
    native indexers + anime-profile series get ANIME_TAG_NAME, every other torrent indexer +
    series gets NON_ANIME_TAG_NAME — so each side only ever matches its own.
    """
    anime_tag_id = _get_or_create_tag(sonarr_url, headers, ANIME_TAG_NAME)
    general_tag_id = _get_or_create_tag(sonarr_url, headers, NON_ANIME_TAG_NAME)
    if anime_tag_id is None or general_tag_id is None:
        log.warning("Sonarr: could not create anime/general tags, skipping indexer tag-scoping.")
        return

    anime_indexer_names = {i["name"] for i in PROWLARR_INDEXERS if i.get("anime")}
    general_indexer_names = {i["name"] for i in PROWLARR_INDEXERS if not i.get("anime")}

    indexers = _api(f"{sonarr_url}/api/v3/indexer", headers) or []
    indexers_updated = 0
    for idx in indexers:
        # Prowlarr-synced indexers land in Sonarr as "<name> (Prowlarr)", not the bare name.
        name = idx.get("name", "").removesuffix(" (Prowlarr)")
        if name in anime_indexer_names:
            mine, other = anime_tag_id, general_tag_id
        elif name in general_indexer_names:
            mine, other = general_tag_id, anime_tag_id
        else:
            continue  # usenet / other indexers stay untagged (universal)
        current = set(idx.get("tags") or [])
        target = (current - {other}) | {mine}
        if current == target:
            continue
        idx["tags"] = sorted(target)
        # forceSave=true: skip Sonarr's connectivity re-test on PUT (see _configure_arr_indexers).
        if _api(f"{sonarr_url}/api/v3/indexer/{idx['id']}?forceSave=true", headers,
                method="PUT", data=idx) is not None:
            indexers_updated += 1

    profiles = _api(f"{sonarr_url}/api/v3/qualityprofile", headers) or []
    anime_profile_id = next(
        (p["id"] for p in profiles if p.get("name") == ANIME_PROFILE_NAME), None,
    )
    if anime_profile_id is None:
        log.info(
            "Sonarr: no '%s' quality profile found yet, tagged %d indexer(s) but left "
            "series untouched.", ANIME_PROFILE_NAME, indexers_updated,
        )
        return

    series_list = _api(f"{sonarr_url}/api/v3/series", headers) or []
    series_updated = 0
    for s in series_list:
        is_anime = s.get("qualityProfileId") == anime_profile_id
        wanted_tag = anime_tag_id if is_anime else general_tag_id
        other_tag = general_tag_id if is_anime else anime_tag_id
        current = set(s.get("tags") or [])
        target = (current - {other_tag}) | {wanted_tag}
        if current == target:
            continue
        s["tags"] = sorted(target)
        if _api(f"{sonarr_url}/api/v3/series/{s['id']}", headers,
                method="PUT", data=s) is not None:
            series_updated += 1

    log.info(
        "Sonarr: anime indexer scoping — %d indexer(s), %d series updated.",
        indexers_updated, series_updated,
    )


# ---------------------------------------------------------------------------
# Plex notification connection (Sonarr / Radarr → Plex library refresh)
# ---------------------------------------------------------------------------


def _setup_plex_connection(
    app_name: str,
    base_url: str,
    headers: dict,
    plex_host: str,
    plex_token: str,
) -> None:
    """Add Plex as a notification connection in Sonarr or Radarr.

    When media is imported, *arr will call the Plex API to refresh just the
    affected library section — no need to wait for Plex's scheduled scan.
    Idempotent: skips if a Plex connection already exists.
    """
    url = f"{base_url}/api/v3/notification"
    existing = _api(url, headers)
    if existing:
        for conn in existing:
            if conn.get("implementation") == "PlexServer":
                log.info("%s Plex notification connection already configured.", app_name)
                return

    payload = {
        "implementation": "PlexServer",
        "configContract": "PlexServerSettings",
        "name": "Plex",
        "onDownload": True,
        "onUpgrade": True,
        "fields": [
            {"name": "host", "value": plex_host},
            {"name": "port", "value": 32400},
            {"name": "useSsl", "value": False},
            {"name": "authToken", "value": plex_token},
            {"name": "updateLibrary", "value": True},
            {"name": "signIn", "value": "startOAuth"},
        ],
    }

    result = _api(url, headers, method="POST", data=payload)
    if result and result.get("id"):
        log.info("%s Plex notification connection added (host=%s:32400).", app_name, plex_host)
    else:
        log.warning("Failed to add Plex notification connection to %s.", app_name)


ANIME_SYNC_SCRIPT_PATH = "/config/scripts/anime-sync-touch.sh"


def _setup_anime_sync_notification(sonarr_url: str, headers: dict) -> None:
    """Wire Sonarr's onSeriesAdd event to the host-side anime-sync hook.

    Points at a script installed by stack_config.py's bootstrap step (inside
    Sonarr's own /config volume) that just touches a marker file — Sonarr
    never gets Bazarr's API key, only a systemd .path unit on the host does.
    See media-anime-sync.service/.path and sync_anime_only() below.
    Idempotent: skips if already configured with the current script path.
    """
    url = f"{sonarr_url}/api/v3/notification"
    existing = _api(url, headers) or []
    for conn in existing:
        if conn.get("implementation") == "CustomScript" and conn.get("name") == "Anime Auto-Sync":
            path_field = next(
                (f for f in conn.get("fields", []) if f.get("name") == "path"), None,
            )
            if path_field and path_field.get("value") == ANIME_SYNC_SCRIPT_PATH:
                log.info("Sonarr anime auto-sync notification already configured.")
                return
            break  # exists but stale — fall through and recreate

    payload = {
        "implementation": "CustomScript",
        "configContract": "CustomScriptSettings",
        "name": "Anime Auto-Sync",
        "onSeriesAdd": True,
        "fields": [{"name": "path", "value": ANIME_SYNC_SCRIPT_PATH}],
    }
    result = _api(url, headers, method="POST", data=payload)
    if result and result.get("id"):
        log.info("Sonarr anime auto-sync notification added (onSeriesAdd → %s).",
                  ANIME_SYNC_SCRIPT_PATH)
    else:
        log.warning("Failed to add Sonarr anime auto-sync notification.")


def setup_plex_connect(
    sonarr_url: str,
    sonarr_key: str | None,
    radarr_url: str,
    radarr_key: str | None,
    plex_host: str,
    plex_token: str,
) -> None:
    """Wire Plex library refresh into Sonarr and Radarr."""
    if sonarr_key:
        headers = {"X-Api-Key": sonarr_key, "Content-Type": "application/json"}
        _setup_plex_connection("Sonarr", sonarr_url, headers, plex_host, plex_token)
    if radarr_key:
        headers = {"X-Api-Key": radarr_key, "Content-Type": "application/json"}
        _setup_plex_connection("Radarr", radarr_url, headers, plex_host, plex_token)


# ---------------------------------------------------------------------------
# Cleanuparr post-deploy instructions
# ---------------------------------------------------------------------------


def _detect_lan_ip() -> str:
    """Return the primary LAN IP (same method as setup_env.py)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "<vm-ip>"


def _cleanuparr_is_configured(config_base: Path) -> bool:
    """True if Cleanuparr already has *arr connections in its SQLite db.

    Cleanuparr has no API to query, so read its db directly. Anything
    unexpected (missing file, schema change, locked db) is treated as
    "not configured" — printing the setup banner one extra time is a much
    cheaper failure than hiding it from someone who genuinely needs it.
    """
    db = config_base / "cleanuparr" / "cleanuparr.db"
    if not db.is_file():
        return False
    conn = None
    try:
        # Read-only URI so a concurrent Cleanuparr write can't be disturbed.
        # sqlite3's context manager commits, it does not close — hence finally.
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        row = conn.execute("SELECT COUNT(*) FROM arr_instances").fetchone()
        return bool(row and row[0] > 0)
    except sqlite3.Error as exc:
        log.debug("Cleanuparr db check failed (%s); assuming unconfigured.", exc)
        return False
    finally:
        if conn is not None:
            conn.close()


def _print_cleanuparr_instructions(config_base: Path,
                                   env: dict[str, str]) -> None:
    """Print connection details for Cleanuparr's web UI setup.

    Cleanuparr stores config in SQLite and has no public API for adding
    connections programmatically.  Instead, print the exact values so the
    user can paste them into the UI in under a minute.

    Skipped once Cleanuparr is configured: printing a "setup required"
    banner on every deploy trains people to ignore it, and it dumps
    credentials into the deploy log every time for no reason.
    """
    if _cleanuparr_is_configured(config_base):
        log.info("Cleanuparr already configured (*arr connections present).")
        return

    sonarr_key = _read_api_key(config_base, "sonarr") or "<check config/sonarr/config.xml>"
    radarr_key = _read_api_key(config_base, "radarr") or "<check config/radarr/config.xml>"
    qbit_user = env.get("QBITTORRENT_USERNAME", "admin")
    usenet_enabled = env.get("ENABLE_SABNZBD", "0") == "1"
    sabnzbd_key = _read_sabnzbd_api_key(config_base) if usenet_enabled else None
    vm_ip = _detect_lan_ip()

    step = 0

    log.info("")
    log.info("=" * 64)
    log.info("  Cleanuparr — one-time UI setup required")
    log.info("=" * 64)
    log.info("  Open: http://%s:11011", vm_ip)
    log.info("")
    step += 1
    log.info("  %d. Add Sonarr connection:", step)
    log.info("       Host: http://sonarr:8989")
    log.info("       API Key: %s", sonarr_key)
    log.info("")
    step += 1
    log.info("  %d. Add Radarr connection:", step)
    log.info("       Host: http://radarr:7878")
    log.info("       API Key: %s", radarr_key)
    log.info("")
    step += 1
    log.info("  %d. Add qBittorrent download client:", step)
    log.info("       Host: http://vpn:8080")
    log.info("       Username: %s", qbit_user)
    # Deliberately not echoed: unlike the *arr API keys this is a reusable
    # human credential, and deploy output gets scrolled, pasted and shipped
    # to Loki.
    log.info("       Password: <QBITTORRENT_PASSWORD in docker_compose/media/.env>")
    log.info("")
    if usenet_enabled:
        step += 1
        sab_key_display = sabnzbd_key or "<check config/sabnzbd/sabnzbd.ini>"
        log.info("  %d. Add SABnzbd download client:", step)
        log.info("       Host: http://sabnzbd:8080")
        log.info("       API Key: %s", sab_key_display)
        log.info("")
    step += 1
    log.info("  %d. Enable Queue Cleaner (strikes for stalled downloads).", step)
    log.info("     Recommended: start with defaults, tune thresholds later.")
    log.info("")
    step += 1
    log.info("  %d. Enable Download Cleaner (Settings > Download Cleaner) and add a"
              " seeding rule", step)
    log.info("     scoped to categories [tv-imported, movies-imported] only"
              " (ratio 1.0 / min")
    log.info("     seed time 24h / max seed time 168h / delete source files)."
              " qBittorrent's")
    log.info("     own ratio limit now only pauses (never deletes); Sonarr/Radarr"
              " move a")
    log.info("     torrent into the *-imported category the moment it confirms"
              " import, so")
    log.info("     scoping the rule to that category is what keeps cleanup from"
              " racing an")
    log.info("     import (Cleanuparr does NOT check hardlinks itself - see"
              " docs/Chapter3c-")
    log.info("     media-stack.md, 'Post-import cleanup design').")
    log.info("")
    log.info("     Tip: set CLEANUPARR_API_KEY in .env (Account Settings > API Key"
              " in the")
    log.info("     Cleanuparr UI) and re-run deploy - this step then configures"
              " itself.")
    log.info("")
    step += 1
    log.info("  %d. Enable Malware Blocker (Settings > Malware Blocker).", step)
    log.info("     Add official blocklists for Sonarr and Radarr:")
    log.info("       https://cleanuparr.pages.dev/static/blacklist")
    log.info("       https://cleanuparr.pages.dev/static/whitelist_with_subtitles")
    log.info("     Also enable 'Delete Known Malware' for auto-updated patterns.")
    log.info("")
    step += 1
    log.info("  %d. Enable Search (Settings > General > Search Enabled).", step)
    log.info("     Auto-searches for replacements after removing bad downloads.")
    log.info("=" * 64)
    log.info("")


CLEANUPARR_SEEDING_RULE_NAME = "post-import cleanup (24h+ratio1)"

# Strikes before Cleanuparr removes a stuck failed-import. At the default 5-min
# queue-cleaner cadence this is ~25 min of grace, enough for a transient import
# to resolve itself but short enough that genuinely-stuck items don't linger.
CLEANUPARR_FAILED_IMPORT_STRIKES = 5


def setup_cleanuparr_queue_cleaner(cleanuparr_url: str, api_key: str) -> None:
    """Enable Cleanuparr's Queue Cleaner failed-import handling via the REST API.

    Sonarr/Radarr keep downloads that fail to import (season packs that unpack
    to episodes already owned, releases they will only match to a series by ID
    and so refuse to auto-import) sitting in the queue as completed+warning
    forever. Nothing else clears them, so the queue accretes stuck items
    indefinitely — the qBittorrent seeding rule this PR adds only ever sees a
    torrent that imported cleanly.

    Enabling failed-import handling makes Cleanuparr strike such an item every
    run and, after maxStrikes, remove it from the queue + download client,
    blocklist the release, and trigger a re-search — so genuinely-missing
    episodes get re-requested and already-owned duplicates simply clear.

    patternMode MUST be "Exclude": "Include" with an empty pattern list is
    rejected by Cleanuparr's own validation ("At least one pattern must be
    specified") and would mean "match nothing" regardless. "Exclude" with no
    patterns is "act on every failed import", which is what we want.

    The GET returns the queue-cleaner's slow/stall rules as empty arrays but the
    server preserves them across this PUT (verified live), so the
    read-modify-write below does not clobber them.
    """
    headers = {"X-Api-Key": api_key, "Content-Type": "application/json"}
    config = _api(f"{cleanuparr_url}/api/configuration/queue_cleaner", headers)
    if not config:
        log.warning(
            "Could not reach Cleanuparr API (check CLEANUPARR_API_KEY); "
            "skipping Queue Cleaner automation."
        )
        return

    failed_import = config.get("failedImport") or {}
    if (failed_import.get("maxStrikes") == CLEANUPARR_FAILED_IMPORT_STRIKES
            and failed_import.get("patternMode") == "Exclude"):
        log.info("Cleanuparr failed-import handling already configured.")
        return

    failed_import["maxStrikes"] = CLEANUPARR_FAILED_IMPORT_STRIKES
    failed_import["patternMode"] = "Exclude"   # act on ALL failed imports
    failed_import["patterns"] = []
    config["failedImport"] = failed_import
    if _api(f"{cleanuparr_url}/api/configuration/queue_cleaner", headers,
            method="PUT", data=config) is not None:
        log.info(
            "Cleanuparr failed-import handling enabled (maxStrikes=%d) — stuck "
            "imports now auto-clear and re-search.",
            CLEANUPARR_FAILED_IMPORT_STRIKES,
        )
    else:
        log.warning("Failed to enable Cleanuparr failed-import handling.")


def setup_cleanuparr_download_cleaner(cleanuparr_url: str, api_key: str) -> None:
    """Enable Cleanuparr's Download Cleaner and ensure the post-import seeding
    rule exists (idempotent), via Cleanuparr's REST API.

    Requires the qBittorrent connection to already be registered in
    Cleanuparr - that first-time connection setup still has no API and
    needs the one-time UI step (see _print_cleanuparr_instructions). Once
    that's done and CLEANUPARR_API_KEY is set in .env, this closes the loop.
    Design rationale: docs/Chapter3c-media-stack.md, "Post-import cleanup
    design".
    """
    headers = {"X-Api-Key": api_key, "Content-Type": "application/json"}
    config = _api(f"{cleanuparr_url}/api/configuration/download_cleaner", headers)
    if not config:
        log.warning(
            "Could not reach Cleanuparr API (check CLEANUPARR_API_KEY); "
            "skipping Download Cleaner automation."
        )
        return

    qbit_client = next(
        (c for c in config.get("clients", [])
         if c.get("downloadClientTypeName") == "qBittorrent"),
        None,
    )
    if qbit_client is None:
        log.warning(
            "No qBittorrent connection found in Cleanuparr yet - add it via "
            "the UI first (see instructions above), then redeploy."
        )
        return

    if not config.get("enabled"):
        if _api(
            f"{cleanuparr_url}/api/configuration/download_cleaner", headers,
            method="PUT",
            data={
                "Enabled": True,
                "CronExpression": config.get("cronExpression") or "0 0 * * * ?",
                "UseAdvancedScheduling": config.get("useAdvancedScheduling", False),
                "IgnoredDownloads": config.get("ignoredDownloads", []),
            },
        ) is not None:
            log.info("Cleanuparr Download Cleaner enabled.")
        else:
            log.warning("Failed to enable Cleanuparr Download Cleaner.")

    target_categories = sorted(QBIT_IMPORTED_CATEGORIES.keys())
    rule_body = {
        "Name": CLEANUPARR_SEEDING_RULE_NAME,
        "Categories": target_categories,
        "TrackerPatterns": [],
        "TagsAny": [],
        "TagsAll": [],
        "PrivacyType": "Both",
        "MaxRatio": 1.0,
        "MinSeedTime": 24,
        "MaxSeedTime": 168,
        "MinSeeders": 0,
        "DeleteSourceFiles": True,
    }
    existing_rule = next(
        (r for r in qbit_client.get("seedingRules", [])
         if r.get("name") == CLEANUPARR_SEEDING_RULE_NAME),
        None,
    )
    client_id = qbit_client.get("downloadClientId")
    if existing_rule is None:
        if _api(f"{cleanuparr_url}/api/seeding-rules/{client_id}", headers,
                method="POST", data=rule_body) is not None:
            log.info(
                "Cleanuparr seeding rule '%s' created (categories=%s).",
                CLEANUPARR_SEEDING_RULE_NAME, target_categories,
            )
        else:
            log.warning("Failed to create Cleanuparr seeding rule.")
        return

    needs_update = (
        sorted(existing_rule.get("categories", [])) != target_categories
        or existing_rule.get("maxRatio") != rule_body["MaxRatio"]
        or existing_rule.get("minSeedTime") != rule_body["MinSeedTime"]
        or existing_rule.get("maxSeedTime") != rule_body["MaxSeedTime"]
        or not existing_rule.get("deleteSourceFiles")
    )
    if not needs_update:
        log.info("Cleanuparr seeding rule '%s' already configured.", CLEANUPARR_SEEDING_RULE_NAME)
        return

    if _api(f"{cleanuparr_url}/api/seeding-rules/{existing_rule['id']}", headers,
            method="PUT", data=rule_body) is not None:
        log.info("Cleanuparr seeding rule '%s' updated.", CLEANUPARR_SEEDING_RULE_NAME)
    else:
        log.warning("Failed to update Cleanuparr seeding rule.")


# ---------------------------------------------------------------------------
# Bazarr setup (subtitles)
# ---------------------------------------------------------------------------


def setup_bazarr(bazarr_url: str, sonarr_key: str | None,
                 radarr_key: str | None, config_base: Path,
                 env: dict[str, str]) -> bool:
    """Configure Bazarr: Sonarr/Radarr connections, language profile,
    providers, subtitle settings, and mass-assign profile.

    Uses Bazarr's form-encoded settings API.
    Ref: https://trash-guides.info/Bazarr/  https://wiki.bazarr.media/

    Returns False if Bazarr never became reachable, its API key is
    missing, the main settings POST didn't take, or the default-language-
    profile POST didn't take. That last one is deliberately fatal even
    though the rest of setup already ran: a previous incident had this
    step silently no-op (a KeyError in the profile payload), which meant
    every series/movie added *after* that deploy got skipped for subtitles
    with zero indication anything was wrong. Per-item profile-assignment
    stragglers (existing library items that didn't get reassigned) stay
    non-fatal — they're retried on the next deploy.
    """
    # 1. Wait for Bazarr (ping is unauthenticated)
    ping_url = f"{bazarr_url}/api/system/ping"
    deadline = time.monotonic() + HEALTH_TIMEOUT_S
    ready = False
    while time.monotonic() < deadline:
        try:
            req = urllib.request.Request(ping_url)
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    ready = True
                    break
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(HEALTH_POLL_S)
    if not ready:
        log.warning("Bazarr not ready within %ds; skipping setup.", HEALTH_TIMEOUT_S)
        return False

    # 2. Read API key
    apikey = _read_bazarr_api_key(config_base)
    if not apikey:
        log.warning("Bazarr API key not found in config.yaml; skipping setup.")
        return False

    settings_url = f"{bazarr_url}/api/system/settings"

    # 3. Check current state
    current = _bazarr_get(settings_url, apikey) or {}
    sonarr_configured = current.get("sonarr", {}).get("apikey", "")
    radarr_configured = current.get("radarr", {}).get("apikey", "")

    # 4–5. Sonarr + Radarr connections, minimum scores, subtitle/sync settings
    form: dict[str, str | list[str]] = {}

    if sonarr_key and sonarr_configured != sonarr_key:
        form.update({
            "settings-general-use_sonarr": "true",
            "settings-sonarr-ip": "sonarr",
            "settings-sonarr-port": "8989",
            "settings-sonarr-base_url": "",
            "settings-sonarr-ssl": "false",
            "settings-sonarr-apikey": sonarr_key,
        })
    if radarr_key and radarr_configured != radarr_key:
        form.update({
            "settings-general-use_radarr": "true",
            "settings-radarr-ip": "radarr",
            "settings-radarr-port": "7878",
            "settings-radarr-base_url": "",
            "settings-radarr-ssl": "false",
            "settings-radarr-apikey": radarr_key,
        })

    # Scoring (TRaSH recommended)
    form["settings-general-minimum_score"] = "90"
    form["settings-general-minimum_score_movie"] = "80"

    # Subtitle settings
    form.update({
        "settings-general-subfolder": "current",
        "settings-general-upgrade_subs": "true",
        "settings-general-days_to_upgrade_subs": "7",
        "settings-general-upgrade_manual": "true",
        "settings-general-adaptive_searching": "true",
        "settings-general-adaptive_searching_delay": "1w",
        "settings-general-adaptive_searching_delta": "4w",
    })

    # Embedded-subtitle handling — do NOT let an embedded image/complex subtitle
    # count as "we have this language", so Bazarr fetches an external .srt instead.
    # Why: Plex must BURN image-based (PGS/VobSub) and styled (ASS/SSA) subtitles
    # into the video, and the burn filter (inlineass) can't run on the Intel GPU —
    # it forces the whole transcode to software (libx264), pinning the CPU. A plain
    # text .srt is sent to the client as a soft track, so the video can direct-play
    # or hardware-transcode. use_embedded_subs stays on so an embedded *text* (SRT)
    # track still counts. See the "Anime forces software transcode" investigation.
    form.update({
        "settings-embeddedsubtitles-ignore_ass_subs": "true",
        "settings-embeddedsubtitles-ignore_pgs_subs": "true",
        "settings-embeddedsubtitles-ignore_vobsub_subs": "true",
    })

    # Subsync (TRaSH recommended thresholds)
    form.update({
        "settings-subsync-use_subsync": "true",
        "settings-subsync-use_subsync_threshold": "true",
        "settings-subsync-subsync_threshold": "96",
        "settings-subsync-use_subsync_movie_threshold": "true",
        "settings-subsync-subsync_movie_threshold": "86",
    })

    # Providers: the zero-credential set, plus OpenSubtitles.com when its free
    # account is configured in .env.  OpenSubtitles.com is what unblocks anime
    # (and improves the whole library) — see BAZARR_OPENSUBTITLESCOM.
    providers = list(BAZARR_PROVIDERS)
    os_user = env.get("OPENSUBTITLES_USERNAME", "").strip()
    os_pass = env.get("OPENSUBTITLES_PASSWORD", "").strip()
    if os_user and os_pass:
        providers.append(BAZARR_OPENSUBTITLESCOM)
        form.update({
            "settings-opensubtitlescom-username": os_user,
            "settings-opensubtitlescom-password": os_pass,
            # moviehash matching = far more accurate results, no downside.
            "settings-opensubtitlescom-use_hash": "true",
        })
        log.info("Bazarr: OpenSubtitles.com enabled (account %s).", os_user)
    else:
        log.info("Bazarr: OpenSubtitles.com skipped "
                 "(OPENSUBTITLES_USERNAME/PASSWORD not set in .env).")
    form["settings-general-enabled_providers"] = providers

    # 6. Enable subtitle languages
    form["languages-enabled"] = BAZARR_LANGUAGES

    # 7. Language profiles
    def _profile(profile_id: int, name: str, languages: list[str]) -> dict:
        return {
            "profileId": profile_id,
            "name": name,
            "items": [
                {"id": i + 1, "language": lang,
                 "hi": False, "forced": False, "audio_exclude": False,
                 "audio_only_include": False}
                for i, lang in enumerate(languages)
            ],
            "cutoff": None,
            "mustContain": [],
            "mustNotContain": [],
            "originalFormat": None,
        }

    form["languages-profiles"] = json.dumps([
        _profile(BAZARR_PROFILE_ID, BAZARR_PROFILE_NAME, BAZARR_LANGUAGES),
        _profile(BAZARR_ANIME_PROFILE_ID, BAZARR_ANIME_PROFILE_NAME,
                 BAZARR_ANIME_LANGUAGES),
    ])

    # NOTE: the default-profile settings are deliberately NOT part of this form —
    # posting them together with languages-profiles makes Bazarr return HTTP 500 and
    # silently drop them.  They go in their own call below (step 8b).

    # 8. POST all settings in one call
    status = _bazarr_form_post(settings_url, apikey, form)
    if status == 204:
        log.info("Bazarr configured (connections, languages, providers, scoring).")
    else:
        log.warning("Bazarr settings POST returned %s; check config manually.", status)
        return False

    # 8b. Apply a profile to series/movies added *after* this deploy.
    # Step 9 is a one-shot over the library as it exists right now; without these,
    # anything added later lands with profileId=None and Bazarr never searches
    # subtitles for it (it reports 0 missing rather than an error).  New anime still
    # lands on the shared profile here — step 9 moves it to the anime profile on the
    # next deploy, since Bazarr has no per-seriesType default.
    # Must be its own POST: bundled with languages-profiles, Bazarr 500s and drops it.
    defaults_status = _bazarr_form_post(settings_url, apikey, {
        "settings-general-serie_default_enabled": "true",
        "settings-general-serie_default_profile": str(BAZARR_PROFILE_ID),
        "settings-general-movie_default_enabled": "true",
        "settings-general-movie_default_profile": str(BAZARR_PROFILE_ID),
    })
    general = (_bazarr_get(settings_url, apikey) or {}).get("general", {})
    defaults_ok = bool(
        general.get("serie_default_enabled") and general.get("movie_default_enabled")
    )
    if defaults_ok:
        log.info("Bazarr: default language profile enabled for new series/movies.")
    else:
        log.warning("Bazarr: could not enable the default language profile "
                    "(HTTP %s); series added later will be skipped.", defaults_status)

    # 9. Put every existing series/movie on the right language profile
    _bazarr_assign_profiles(bazarr_url, apikey)

    return defaults_ok


def _bazarr_list(bazarr_url: str, apikey: str, kind: str) -> list[dict]:
    items = _bazarr_get(f"{bazarr_url}/api/{kind}?start=0&length=-1", apikey)
    if not items:
        return []
    return items.get("data", []) if isinstance(items, dict) else items


def _bazarr_wanted_profile(item: dict, kind: str) -> int:
    """Which language profile an item should be on.

    Anime gets an English-only profile.  The shared profile has no cutoff, so
    Bazarr wants *every* language on it and re-searches the missing ones forever
    — and Hebrew anime subtitles essentially do not exist, so putting anime on it
    means a permanent wanted-queue entry that hammers the providers and never
    succeeds.  seriesType is Sonarr's own Standard/Daily/Anime field, which Bazarr
    mirrors on its series objects.
    """
    if kind == "series" and item.get("seriesType") == "anime":
        return BAZARR_ANIME_PROFILE_ID
    return BAZARR_PROFILE_ID


def _bazarr_assign_profiles(bazarr_url: str, apikey: str) -> None:
    """Assign the right language profile to every series and movie.

    Bazarr populates its series/movies list from Sonarr/Radarr asynchronously
    after the connection is saved, so poll briefly instead of acting on a
    possibly-empty list immediately after configuring the connection.
    """
    for kind, id_field in [("series", "sonarrSeriesId"),
                           ("movies", "radarrId")]:
        data: list[dict] = []
        deadline = time.monotonic() + HEALTH_TIMEOUT_S
        while time.monotonic() < deadline:
            data = _bazarr_list(bazarr_url, apikey, kind)
            if data:
                break
            time.sleep(HEALTH_POLL_S)
        if not data:
            log.warning("Bazarr: no %s synced from Sonarr/Radarr yet; "
                        "skipping profile assignment.", kind)
            continue

        wanted = {
            item[id_field]: _bazarr_wanted_profile(item, kind)
            for item in data if id_field in item
        }
        needs_update = {
            item[id_field]: wanted[item[id_field]] for item in data
            if id_field in item and item.get("profileId") != wanted[item[id_field]]
        }
        if not needs_update:
            log.info("Bazarr: all %s already on the right profile.", kind)
            continue

        # Bazarr's bulk-assign endpoint only honors the first id when given
        # repeated form keys (e.g. seriesid=1&seriesid=2), so assign one at a
        # time. It also returns HTTP 500 on success in some versions, so
        # verify the result via GET rather than trusting the status code.
        post_key = "seriesid" if kind == "series" else "radarrid"
        for item_id, profile_id in needs_update.items():
            _bazarr_form_post(
                f"{bazarr_url}/api/{kind}", apikey,
                {post_key: str(item_id), "profileid": str(profile_id)},
            )

        still_wrong = sum(
            1 for item in _bazarr_list(bazarr_url, apikey, kind)
            if id_field in item
            and item.get("profileId") != _bazarr_wanted_profile(item, kind)
        )
        if still_wrong:
            log.warning("Bazarr: %d %s still on the wrong profile after "
                        "assignment.", still_wrong, kind)
        else:
            log.info("Bazarr: assigned profile to %d %s.", len(needs_update), kind)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def setup(env: dict[str, str], script_dir: Path) -> bool:
    """Run post-deploy media app setup.

    Returns True only if every "core" service (Prowlarr, Sonarr, Radarr,
    qBittorrent — always deployed, never behind an ENABLE_* flag) was
    reachable and got at least its load-bearing config applied, AND every
    optional service the operator explicitly turned on (ENABLE_BAZARR=1,
    ENABLE_SABNZBD=1) actually got configured rather than silently no-op'd.

    Returns False otherwise, after logging exactly which piece(s) failed
    via log.error. This is deliberately NOT the same as "zero warnings" —
    plenty of individual, independently-retryable sub-steps (a single
    Cloudflare-banned indexer, a cosmetic naming-scheme write, a handful of
    stragglers in Bazarr's mass profile-assignment) only log a warning and
    do not affect this return value. A disabled feature (ENABLE_*=0) or an
    unset optional credential (PLEX_TOKEN, an indexer API key) is a normal
    skip, not a failure.
    """
    config_base = resolve_config_base(
        env.get("CONFIG_ROOT", "./config"), script_dir
    )
    fatal: list[str] = []

    prowlarr_key = _read_api_key(config_base, "prowlarr")
    sonarr_key = _read_api_key(config_base, "sonarr")
    radarr_key = _read_api_key(config_base, "radarr")

    usenet_enabled = env.get("ENABLE_SABNZBD", "0") == "1"
    sabnzbd_key: str | None = None
    if usenet_enabled:
        sabnzbd_key = _read_sabnzbd_api_key(config_base)
        if not sabnzbd_key:
            log.warning(
                "SABnzbd enabled but API key not found in sabnzbd.ini; "
                "skipping SABnzbd download client setup in *arrs."
            )
            fatal.append("SABnzbd enabled but API key not found in sabnzbd.ini")

    if prowlarr_key:
        if not setup_prowlarr_indexers("http://localhost:9696", prowlarr_key):
            fatal.append("Prowlarr unreachable — no indexers were configured")
        if usenet_enabled:
            setup_prowlarr_usenet_indexers(
                "http://localhost:9696", prowlarr_key, env,
            )
    else:
        log.warning("Prowlarr API key not found; skipping indexer setup.")
        fatal.append("Prowlarr API key not found — indexer setup skipped entirely")

    if not setup_qbittorrent("http://localhost:8080", env):
        fatal.append("Could not authenticate to qBittorrent — nothing was configured")

    if usenet_enabled and sabnzbd_key:
        if not setup_sabnzbd("http://localhost:8082", config_base, env):
            fatal.append("SABnzbd unreachable — config verification skipped")

    if sonarr_key:
        if not setup_sonarr("http://localhost:8989", sonarr_key, env, sabnzbd_key):
            fatal.append("Sonarr unreachable — configuration skipped entirely")
    else:
        log.warning("Sonarr API key not found; skipping Sonarr setup.")
        fatal.append("Sonarr API key not found — setup skipped entirely")

    if radarr_key:
        if not setup_radarr("http://localhost:7878", radarr_key, env, sabnzbd_key):
            fatal.append("Radarr unreachable — configuration skipped entirely")
    else:
        log.warning("Radarr API key not found; skipping Radarr setup.")
        fatal.append("Radarr API key not found — setup skipped entirely")

    if prowlarr_key:
        setup_prowlarr_apps(
            "http://localhost:9696", prowlarr_key, sonarr_key, radarr_key,
        )

    if sonarr_key:
        sonarr_headers = {"X-Api-Key": sonarr_key, "Content-Type": "application/json"}
        _configure_arr_indexers("Sonarr", "http://localhost:8989", sonarr_headers, env)
        _tag_anime_indexer_scoping("http://localhost:8989", sonarr_headers)
        _setup_anime_sync_notification("http://localhost:8989", sonarr_headers)
    if radarr_key:
        _configure_arr_indexers(
            "Radarr", "http://localhost:7878",
            {"X-Api-Key": radarr_key, "Content-Type": "application/json"},
            env,
        )

    plex_host = env.get("PLEX_HOST", "").strip()
    plex_token = env.get("PLEX_TOKEN", "").strip()
    if plex_host and plex_token:
        setup_plex_connect(
            "http://localhost:8989", sonarr_key,
            "http://localhost:7878", radarr_key,
            plex_host, plex_token,
        )
    else:
        log.info(
            "Skipping Plex notification setup (PLEX_HOST and PLEX_TOKEN not set in .env).\n"
            "Set both in .env and re-run: python3 setup_media_apps.py"
        )

    if env.get("ENABLE_BAZARR", "0") == "1":
        bazarr_key = _read_bazarr_api_key(config_base)
        if not bazarr_key:
            log.warning("Bazarr enabled but API key not found; skipping Bazarr setup.")
            fatal.append("Bazarr enabled but API key not found")
        elif not sonarr_key or not radarr_key:
            log.warning("Bazarr enabled but missing Sonarr/Radarr API key(s); skipping Bazarr setup.")
            fatal.append("Bazarr enabled but missing Sonarr/Radarr API key(s)")
        else:
            if not setup_bazarr(
                "http://localhost:6767", sonarr_key, radarr_key, config_base, env,
            ):
                fatal.append("Bazarr enabled but its setup did not fully apply (see warnings above)")

    if env.get("ENABLE_CLEANUPARR", "0") == "1":
        _print_cleanuparr_instructions(config_base, env)
        cleanuparr_key = env.get("CLEANUPARR_API_KEY", "").strip()
        if cleanuparr_key:
            setup_cleanuparr_download_cleaner("http://localhost:11011", cleanuparr_key)
            setup_cleanuparr_queue_cleaner("http://localhost:11011", cleanuparr_key)

    if fatal:
        log.error(
            "Media app setup FAILED (%d issue(s)): %s",
            len(fatal), "; ".join(fatal),
        )
        return False
    return True


def sync_anime_only(env: dict[str, str], script_dir: Path) -> bool:
    """Re-tag indexers for anime scoping and reassign Bazarr language profiles.

    Lightweight counterpart to setup(), for the onSeriesAdd hook
    (media-anime-sync.service): a series added between deploys has no
    indexer tags and sits on Bazarr's shared profile until one of these two
    steps runs. Runs only that pair, skipping full app configuration.
    """
    config_base = resolve_config_base(env.get("CONFIG_ROOT", "./config"), script_dir)

    sonarr_key = _read_api_key(config_base, "sonarr")
    if not sonarr_key:
        log.warning("Sonarr API key not found; skipping anime sync.")
        return False
    sonarr_headers = {"X-Api-Key": sonarr_key, "Content-Type": "application/json"}
    _tag_anime_indexer_scoping("http://localhost:8989", sonarr_headers)

    if env.get("ENABLE_BAZARR", "0") == "1":
        bazarr_key = _read_bazarr_api_key(config_base)
        if bazarr_key:
            _bazarr_assign_profiles("http://localhost:6767", bazarr_key)
        else:
            log.warning("Bazarr enabled but API key not found; skipping Bazarr profile sync.")

    return True


def main() -> None:
    setup_logging()
    env_file = STACK_DIR / ".env"
    if not env_file.is_file():
        log.error("No .env found.")
        sys.exit(1)
    env = load_env(env_file)
    if "--sync-anime-only" in sys.argv[1:]:
        sys.exit(0 if sync_anime_only(env, STACK_DIR) else 1)
    setup(env, STACK_DIR)


if __name__ == "__main__":
    main()