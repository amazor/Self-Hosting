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


def _detect_lan_cidr(ip: str) -> str | None:
    """Return the /24 CIDR for the given LAN IP (e.g. 192.168.1.202 → 192.168.1.0/24)."""
    import ipaddress

    try:
        network = ipaddress.IPv4Network(f"{ip}/24", strict=False)
        return str(network)
    except ValueError:
        return None


def _detect_docker_gid() -> int | None:
    """Return the GID of the 'docker' group on this host, or None."""
    import grp

    try:
        return grp.getgrnam("docker").gr_gid
    except KeyError:
        return None


def _parse_scrape_targets(env_path: Path) -> dict[str, str]:
    """Read SCRAPE_TARGETS from an env file and return {hostname: ip} pairs."""
    if not env_path.is_file():
        return {}
    from scripts.homelab_common import load_env

    env = load_env(env_path)
    raw = env.get("SCRAPE_TARGETS", "").strip()
    if not raw:
        return {}
    result: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if ":" not in pair:
            continue
        parts = pair.split(":")
        if len(parts) >= 2:
            name, ip = parts[0].strip(), parts[1].strip()
            if name and ip:
                result[name] = ip
    return result


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
    present: set[str] = set()
    lines = path.read_text().splitlines()
    output: list[str] = []
    updated: list[str] = []

    for line in lines:
        stripped = line.strip()

        # Remove stale pre-fill markers from previous runs (idempotent)
        if stripped.startswith(_PREFILL_MARKER):
            continue

        if stripped and "=" in stripped:
            if stripped.startswith("#"):
                # Track commented-out variables so we don't append duplicates.
                # Handles lines like: # VM_HOSTNAME=core
                bare = stripped.lstrip("#").strip()
                if bare and "=" in bare:
                    present.add(bare.split("=", 1)[0].strip())
            else:
                key = stripped.split("=", 1)[0].strip()
                present.add(key)
                if key in updates:
                    new_val, note = updates[key]
                    output.append(f"{_PREFILL_MARKER}: {note})")
                    output.append(f"{key}={new_val}")
                    updated.append(key)
                    continue

        output.append(line)

    # Append variables that don't exist in the file (not even as comments).
    for key, (new_val, note) in updates.items():
        if key not in present and key not in updated:
            output.append(f"{_PREFILL_MARKER}: {note})")
            output.append(f"{key}={new_val}")
            updated.append(key)

    path.write_text("\n".join(output) + "\n")
    return updated


def _fill_missing_env_vars(
    path: Path,
    updates: dict[str, tuple[str, str]],
) -> list[str]:
    """Fill empty or missing variables in an existing .env file.

    Unlike _update_env_example, this does NOT overwrite existing values.
    Only fills vars that are missing, empty, or commented-out-and-empty.
    """
    import re

    lines = path.read_text().splitlines()
    new_lines: list[str] = []
    filled: list[str] = []
    seen: set[str] = set()

    for line in lines:
        stripped = line.strip()
        matched = False
        for var, (val, _note) in updates.items():
            commented = re.match(rf"^#\s*{re.escape(var)}\s*=(.*)", stripped)
            uncommented = re.match(rf"^{re.escape(var)}\s*=(.*)", stripped)

            if commented:
                seen.add(var)
                rhs = commented.group(1).strip()
                if not rhs:
                    new_lines.append(f"{var}={val}")
                    filled.append(var)
                else:
                    new_lines.append(line)
                matched = True
                break
            if uncommented:
                seen.add(var)
                rhs = uncommented.group(1).strip()
                if not rhs:
                    new_lines.append(f"{var}={val}")
                    filled.append(var)
                else:
                    new_lines.append(line)
                matched = True
                break

        if not matched:
            new_lines.append(line)

    for var, (val, _note) in updates.items():
        if var not in seen:
            new_lines.append(f"{var}={val}")
            filled.append(var)

    if filled:
        path.write_text("\n".join(new_lines) + "\n")

    return filled


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    setup_logging()
    log.info("--- Pre-filling .env.example files ---")

    # -- Shared identity / observability values (same for every stack) --
    uid = os.getuid()
    gid = os.getgid()
    docker_gid = _detect_docker_gid()
    proxmox_node = None
    proxmox_node_path = Path("/etc/homelab/proxmox-node")
    if proxmox_node_path.is_file():
        proxmox_node = proxmox_node_path.read_text().strip() or None
    hostname = socket.gethostname()

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

        core_updates["PUID"] = (str(uid), f"current user UID ({uid})")
        core_updates["PGID"] = (str(gid), f"current user GID ({gid})")
        if docker_gid is not None:
            core_updates["DOCKER_GID"] = (
                str(docker_gid),
                f"host docker group GID ({docker_gid})",
            )
        # VM identity
        core_updates["VM_HOSTNAME"] = (
            hostname,
            f"detected hostname ({hostname}); override if needed",
        )
        core_updates["VM_ROLE"] = ("core", "stack role (core)")
        if proxmox_node:
            core_updates["PROXMOX_NODE"] = (
                proxmox_node,
                f"Proxmox node name ({proxmox_node}); override if needed",
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

        media_updates["PUID"] = (str(uid), f"current user UID ({uid})")
        media_updates["PGID"] = (str(gid), f"current user GID ({gid})")

        ip = _detect_lan_ip()
        if ip:
            lan_cidr = _detect_lan_cidr(ip)
            if lan_cidr:
                # Docker Compose creates a custom bridge (typically 172.18.0.0/16).
                # The *arr containers reach qBittorrent via vpn:8080 over that bridge,
                # so it must be included alongside the LAN subnet.
                vpn_cidr = f"{lan_cidr},172.18.0.0/16"
                media_updates["EXPRESSVPN_LAN_CIDR"] = (
                    vpn_cidr,
                    f"detected LAN {lan_cidr} + Docker Compose bridge; verify before use",
                )
        if docker_gid is not None:
            media_updates["DOCKER_GID"] = (
                str(docker_gid),
                f"host docker group GID ({docker_gid})",
            )
        # VM identity
        media_updates["VM_HOSTNAME"] = (
            hostname,
            f"detected hostname ({hostname}); override if needed",
        )
        media_updates["VM_ROLE"] = ("media", "stack role (media)")
        if proxmox_node:
            media_updates["PROXMOX_NODE"] = (
                proxmox_node,
                f"Proxmox node name ({proxmox_node}); override if needed",
            )

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

        mon_updates["PUID"] = (str(uid), f"current user UID ({uid})")
        mon_updates["PGID"] = (str(gid), f"current user GID ({gid})")

        if docker_gid is not None:
            mon_updates["DOCKER_GID"] = (
                str(docker_gid),
                f"host docker group GID ({docker_gid})",
            )
        # VM identity
        mon_updates["VM_HOSTNAME"] = (
            hostname,
            f"detected hostname ({hostname}); override if needed",
        )
        mon_updates["VM_ROLE"] = ("monitoring", "stack role (monitoring)")
        if proxmox_node:
            mon_updates["PROXMOX_NODE"] = (
                proxmox_node,
                f"Proxmox node name ({proxmox_node}); override if needed",
            )

        # Derive media VM exporter targets from SCRAPE_TARGETS.
        # If .env exists and has a media: entry, pre-fill all media-hosted
        # exporter targets (ExpressVPN, scraparr, qBittorrent) with the same
        # hostname:ip — they all run on the media VM.
        mon_env = REPO_ROOT / "docker_compose" / "monitoring" / ".env"
        scrape_hosts = _parse_scrape_targets(
            mon_env if mon_env.is_file() else mon_example
        )
        media_ip = scrape_hosts.get("media")
        if media_ip:
            target = f"media:{media_ip}"
            mon_updates["EXPRESSVPN_EXPORTER_TARGETS"] = (
                target,
                f"derived from SCRAPE_TARGETS (media VM at {media_ip})",
            )
            mon_updates["SCRAPARR_EXPORTER_TARGETS"] = (
                target,
                f"derived from SCRAPE_TARGETS (media VM at {media_ip})",
            )
            mon_updates["QBITTORRENT_EXPORTER_TARGETS"] = (
                target,
                f"derived from SCRAPE_TARGETS (media VM at {media_ip})",
            )

        updated = _update_env_example(mon_example, mon_updates)
        if updated:
            log.info(
                f"Monitoring .env.example: pre-filled {', '.join(updated)}"
            )
        else:
            log.info("Monitoring .env.example: nothing to update")

        # Also fill empty/missing vars in .env (does not overwrite existing values).
        if mon_env.is_file() and mon_updates:
            env_filled = _fill_missing_env_vars(mon_env, mon_updates)
            if env_filled:
                log.info(
                    f"Monitoring .env: filled {', '.join(env_filled)}"
                )
    else:
        log.warning(f"Monitoring .env.example not found: {mon_example}")

    # -- Accelerated stack --
    acc_example = REPO_ROOT / "docker_compose" / "accelerated" / ".env.example"
    if acc_example.is_file():
        acc_updates: dict[str, tuple[str, str]] = {}

        acc_updates["PUID"] = (str(uid), f"current user UID ({uid})")
        acc_updates["PGID"] = (str(gid), f"current user GID ({gid})")
        if docker_gid is not None:
            acc_updates["DOCKER_GID"] = (
                str(docker_gid),
                f"host docker group GID ({docker_gid})",
            )
        # VM identity
        acc_updates["VM_HOSTNAME"] = (
            hostname,
            f"detected hostname ({hostname}); override if needed",
        )
        acc_updates["VM_ROLE"] = ("accelerated", "stack role (accelerated)")
        if proxmox_node:
            acc_updates["PROXMOX_NODE"] = (
                proxmox_node,
                f"Proxmox node name ({proxmox_node}); override if needed",
            )

        updated = _update_env_example(acc_example, acc_updates)
        if updated:
            log.info(
                f"Accelerated .env.example: pre-filled {', '.join(updated)}"
            )
        else:
            log.info("Accelerated .env.example: nothing to update")
    else:
        log.warning(f"Accelerated .env.example not found: {acc_example}")

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
    log.info(
        "  cp docker_compose/accelerated/.env.example "
        "docker_compose/accelerated/.env"
    )


if __name__ == "__main__":
    main()
