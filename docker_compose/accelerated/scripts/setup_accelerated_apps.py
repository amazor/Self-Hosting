#!/usr/bin/env python3
"""Post-deploy setup for the accelerated stack (Plex).

Configures Plex via its HTTP API after docker compose up:
  - Applies TRaSH-recommended Plex server settings
  - Creates library sections (Movies, TV Shows, Anime) if missing

Called by deploy.py after the accelerated stack starts, or run standalone:
  cd docker_compose/accelerated && python3 setup_accelerated_apps.py

Idempotent: skips settings already at the target value, skips existing
library sections.

Requirements:
  - PLEX_TOKEN must be set in .env (permanent X-Plex-Token).
  - MEDIA_LIBRARY_ROOT must be set and point to the NFS-mounted library.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
STACK_DIR = SCRIPT_DIR.parent
REPO_ROOT = STACK_DIR.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.homelab_common import load_env, log, setup_logging  # noqa: E402

PLEX_BASE_URL = "http://localhost:32400"
HEALTH_TIMEOUT_S = 120
HEALTH_POLL_S = 5

# ---------------------------------------------------------------------------
# TRaSH-recommended Plex server settings
# Ref: https://trash-guides.info/Plex/Plex-media-server-settings/
# Applied via PUT /:/prefs
# ---------------------------------------------------------------------------

PLEX_PREFS: dict[str, object] = {
    # Scanning: let *arrs manage imports; avoid aggressive auto-scans that
    # interfere with hardlink/import operations.
    "FSEventLibraryUpdatesEnabled": 1,       # watch filesystem for changes
    "FSEventLibraryPartialScanEnabled": 1,   # partial scan on change (fast)
    "autoEmptyTrash": 0,                     # TRaSH: do NOT auto-empty trash
    # Media analysis: needed for accurate duration, bitrate, and chapter info.
    "ButlerScanForMetadata": 1,
    # Transcoding: keep original quality wherever possible.
    "TranscoderQuality": 2,                  # 2 = "Make my CPU hurt" (max quality)
    "TranscoderH264Preset": "veryfast",
    "HardwareAcceleratedCodecs": 1,          # enable hardware transcoding (GPU)
    "HardwareAcceleratedEncoders": 1,
    # Home/managed users: do not allow media deletion from clients.
    "allowMediaDeletion": 0,
    # Metadata language and agent defaults.
    "LanguageInCloud": 1,
    # Online media sources: disable Plex's own noise in the library.
    "OnlineMediaSources": 0,                 # 0 = disabled
    # DLNA: off unless explicitly needed.
    "DlnaEnabled": 0,
}

# ---------------------------------------------------------------------------
# Library sections to create if missing
# Path values are inside the container (/data/library/...).
# ---------------------------------------------------------------------------

PLEX_LIBRARIES = [
    {
        "name": "Movies",
        "type": "movie",
        "agent": "tv.plex.agents.movie",
        "scanner": "Plex Movie",
        "language": "en-US",
        "location": "/data/library/movies",
    },
    {
        "name": "TV Shows",
        "type": "show",
        "agent": "tv.plex.agents.series",
        "scanner": "Plex TV Series",
        "language": "en-US",
        "location": "/data/library/tv",
    },
    {
        "name": "Anime",
        "type": "show",
        "agent": "tv.plex.agents.series",
        "scanner": "Plex TV Series",
        "language": "en-US",
        "location": "/data/library/anime",
    },
]


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _headers(plex_token: str) -> dict[str, str]:
    return {
        "X-Plex-Token": plex_token,
        "Accept": "application/json",
        "X-Plex-Client-Identifier": "homelab-setup-script",
        "X-Plex-Product": "homelab-setup",
    }


def _get(url: str, token: str) -> dict | None:
    req = urllib.request.Request(url, headers=_headers(token))
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, json.JSONDecodeError) as exc:
        log.debug("GET %s → %s", url, exc)
        return None


def _put(url: str, token: str, params: dict) -> bool:
    qs = urllib.parse.urlencode(params)
    full_url = f"{url}?{qs}"
    req = urllib.request.Request(full_url, headers=_headers(token), method="PUT")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status in (200, 201, 204)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
        log.debug("PUT %s → %s", url, exc)
        return False


def _post_form(url: str, token: str, params: dict) -> bool:
    qs = urllib.parse.urlencode(params)
    full_url = f"{url}?{qs}"
    req = urllib.request.Request(
        full_url,
        data=b"",
        headers=_headers(token),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status in (200, 201)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
        log.debug("POST %s → %s", url, exc)
        return False


# ---------------------------------------------------------------------------
# Wait for Plex to be ready
# ---------------------------------------------------------------------------


def _wait_for_plex(plex_token: str) -> bool:
    log.info("Waiting for Plex to be ready (up to %ds)...", HEALTH_TIMEOUT_S)
    deadline = time.monotonic() + HEALTH_TIMEOUT_S
    while time.monotonic() < deadline:
        result = _get(f"{PLEX_BASE_URL}/identity", plex_token)
        if result is not None:
            return True
        time.sleep(HEALTH_POLL_S)
    log.warning("Plex not ready within %ds.", HEALTH_TIMEOUT_S)
    return False


def wait_for_plex_noauth() -> bool:
    """Poll Plex /identity without authentication headers.

    Used during first-run token extraction when PLEX_TOKEN is not yet known.
    The /identity endpoint responds without auth on the local network.
    """
    log.info("Waiting for Plex to be ready (no auth, up to %ds)...", HEALTH_TIMEOUT_S)
    deadline = time.monotonic() + HEALTH_TIMEOUT_S
    while time.monotonic() < deadline:
        req = urllib.request.Request(
            f"{PLEX_BASE_URL}/identity",
            headers={"Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, urllib.error.HTTPError, OSError):
            pass
        time.sleep(HEALTH_POLL_S)
    log.warning("Plex not ready within %ds.", HEALTH_TIMEOUT_S)
    return False


def extract_plex_token(config_base: Path) -> str | None:
    """Read PlexOnlineToken from Preferences.xml after Plex has consumed a claim.

    Returns the token string, or None if the file doesn't exist or the
    attribute is missing (e.g. Plex was never claimed).
    """
    prefs_path = (
        config_base / "plex" / "Library" / "Application Support"
        / "Plex Media Server" / "Preferences.xml"
    )
    if not prefs_path.is_file():
        log.debug("Preferences.xml not found: %s", prefs_path)
        return None

    try:
        tree = ET.parse(prefs_path)
        token = tree.getroot().get("PlexOnlineToken")
        if token:
            log.info("Extracted PLEX_TOKEN from Preferences.xml")
            return token
        log.debug("PlexOnlineToken attribute not found in Preferences.xml")
        return None
    except (ET.ParseError, OSError) as exc:
        log.warning("Failed to parse Preferences.xml: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Apply TRaSH-recommended server settings
# ---------------------------------------------------------------------------


def apply_server_settings(plex_token: str) -> None:
    """Apply TRaSH-recommended preferences via PUT /:/prefs."""
    log.info("Applying TRaSH-recommended Plex server settings...")
    ok = _put(f"{PLEX_BASE_URL}/:/prefs", plex_token, PLEX_PREFS)
    if ok:
        log.info("Plex server settings applied.")
    else:
        log.warning(
            "Failed to apply some Plex server settings. "
            "You can apply them manually via Settings → Troubleshooting."
        )


# ---------------------------------------------------------------------------
# Create library sections
# ---------------------------------------------------------------------------


def _get_existing_libraries(plex_token: str) -> set[str]:
    """Return set of existing library section names (case-insensitive)."""
    data = _get(f"{PLEX_BASE_URL}/library/sections", plex_token)
    if not data:
        return set()
    try:
        sections = data["MediaContainer"].get("Directory", [])
        return {s["title"].lower() for s in sections}
    except (KeyError, TypeError):
        return set()


def create_libraries(plex_token: str) -> None:
    """Create Plex library sections for Movies, TV Shows, and Anime if missing."""
    existing = _get_existing_libraries(plex_token)
    if existing:
        log.info("Existing Plex libraries: %s", ", ".join(sorted(existing)))

    added = 0
    for lib in PLEX_LIBRARIES:
        if lib["name"].lower() in existing:
            log.info("Library '%s' already exists; skipping.", lib["name"])
            continue

        params = {
            "name": lib["name"],
            "type": lib["type"],
            "agent": lib["agent"],
            "scanner": lib["scanner"],
            "language": lib["language"],
            "location": lib["location"],
        }
        ok = _post_form(f"{PLEX_BASE_URL}/library/sections", plex_token, params)
        if ok:
            log.info("Created Plex library: %s (%s)", lib["name"], lib["location"])
            added += 1
        else:
            log.warning(
                "Failed to create Plex library '%s'. "
                "You can create it manually in Settings → Libraries.",
                lib["name"],
            )

    if added == 0 and existing:
        log.info("All Plex libraries already configured.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def setup(env: dict[str, str]) -> bool:
    """Run post-deploy Plex setup. Returns True on success."""
    plex_token = env.get("PLEX_TOKEN", "").strip()
    if not plex_token:
        log.warning(
            "PLEX_TOKEN is not set in .env — skipping Plex setup.\n"
            "Set PLEX_TOKEN and re-run: python3 setup_accelerated_apps.py"
        )
        return False

    if not _wait_for_plex(plex_token):
        return False

    apply_server_settings(plex_token)
    create_libraries(plex_token)
    return True


def main() -> None:
    setup_logging()
    env_file = STACK_DIR / ".env"
    if not env_file.is_file():
        log.error("No .env found.")
        sys.exit(1)
    env = load_env(env_file)
    setup(env)


if __name__ == "__main__":
    main()
