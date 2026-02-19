#!/usr/bin/env python3
"""Post-clone env setup — update .env.example files with pre-filled defaults.

Run once after a fresh clone:
  python3 scripts/setup_env.py

This script edits the .env.example files *in place* for stacks that have
auto-fillable values (e.g. random secrets, detected LAN IP, current UID/GID).
It does NOT create or modify .env — the user still copies .env.example to .env
and verifies before deploying.
"""

from __future__ import annotations

import base64
import os
import socket
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.homelab_common import log, setup_logging

_PREFILL_MARKER = "# (pre-filled by setup_env"


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------


def _generate_secret_key() -> str:
    """Generate a random base64 secret key (same format as openssl rand -base64 60)."""
    return base64.b64encode(os.urandom(60)).decode()


def _detect_lan_ip() -> str | None:
    """Return the primary LAN IP by probing a route to 8.8.8.8 (no traffic sent)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return None


# ---------------------------------------------------------------------------
# .env.example updater
# ---------------------------------------------------------------------------


def _update_env_example(
    path: Path,
    updates: dict[str, tuple[str, str]],
) -> list[str]:
    """Replace placeholder values in a .env.example file.

    Args:
        path: Path to the .env.example file.
        updates: Mapping of VAR_NAME to (new_value, short_note).
            The note is inserted as a comment above the variable line.

    Returns:
        List of variable names that were actually updated.
    """
    lines = path.read_text().splitlines()
    output: list[str] = []
    updated: list[str] = []

    for line in lines:
        stripped = line.strip()

        # Remove stale pre-fill markers from previous runs (idempotent)
        if stripped.startswith(_PREFILL_MARKER):
            continue

        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                new_val, note = updates[key]
                output.append(f"{_PREFILL_MARKER}: {note})")
                output.append(f"{key}={new_val}")
                updated.append(key)
                continue

        output.append(line)

    path.write_text("\n".join(output) + "\n")
    return updated


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    setup_logging()
    log.info("--- Pre-filling .env.example files ---")

    # -- Core stack --
    core_example = REPO_ROOT / "docker_compose" / "core" / ".env.example"
    if core_example.is_file():
        core_updates: dict[str, tuple[str, str]] = {}

        secret = _generate_secret_key()
        core_updates["AUTHENTIK_SECRET_KEY"] = (
            secret,
            "random secret via os.urandom; verify before use",
        )

        ip = _detect_lan_ip()
        if ip:
            core_updates["DNS_BIND_IP"] = (
                ip,
                f"detected LAN IP ({ip}); verify before use",
            )

        updated = _update_env_example(core_example, core_updates)
        if updated:
            log.info(f"Core .env.example: pre-filled {', '.join(updated)}")
        else:
            log.info("Core .env.example: nothing to update")
    else:
        log.warning(f"Core .env.example not found: {core_example}")

    # -- Media stack --
    media_example = REPO_ROOT / "docker_compose" / "media" / ".env.example"
    if media_example.is_file():
        media_updates: dict[str, tuple[str, str]] = {}

        uid = os.getuid()
        gid = os.getgid()
        media_updates["PUID"] = (str(uid), f"current user UID ({uid})")
        media_updates["PGID"] = (str(gid), f"current user GID ({gid})")

        updated = _update_env_example(media_example, media_updates)
        if updated:
            log.info(f"Media .env.example: pre-filled {', '.join(updated)}")
        else:
            log.info("Media .env.example: nothing to update")
    else:
        log.warning(f"Media .env.example not found: {media_example}")

    # -- Monitoring stack --
    # Grafana admin password is not pre-filled; user sets it in .env. Bootstrap
    # verifies it is changed (or use --force for local testing).
    mon_example = REPO_ROOT / "docker_compose" / "monitoring" / ".env.example"
    if mon_example.is_file():
        mon_updates: dict[str, tuple[str, str]] = {}

        updated = _update_env_example(mon_example, mon_updates)
        if updated:
            log.info(
                f"Monitoring .env.example: pre-filled {', '.join(updated)}"
            )
        else:
            log.info("Monitoring .env.example: nothing to update")
    else:
        log.warning(f"Monitoring .env.example not found: {mon_example}")

    log.info("")
    log.info(
        "Done. Review the updated .env.example files, then copy to .env "
        "and verify before deploying:"
    )
    log.info("  cp docker_compose/core/.env.example docker_compose/core/.env")
    log.info(
        "  cp docker_compose/media/.env.example docker_compose/media/.env"
    )
    log.info(
        "  cp docker_compose/monitoring/.env.example "
        "docker_compose/monitoring/.env"
    )


if __name__ == "__main__":
    main()
