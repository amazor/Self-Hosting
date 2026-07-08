#!/usr/bin/env python3
"""Post-clone env setup — stage auto-detected values, then create/update .env.

Run once after a fresh clone (or again any time to pick up newly-added vars):
  python3 scripts/setup_env.py

For each stack, this renders docker_compose/<stack>/.env.staged: a copy of
.env.example with auto-fillable values (random secrets, detected LAN IP,
current UID/GID, etc.) filled in. .env.example itself is never touched — it
stays a plain, secret-free template, since it's tracked by git.

  - If .env doesn't exist yet, you're offered to copy .env.staged to .env on
    the spot (interactive only — non-interactive runs, e.g. Cloud-Init on
    first boot, just render .env.staged and print the cp command instead of
    blocking). Review .env.staged first if anything needs a second look
    (the generated AUTHENTIK_SECRET_KEY, detected IPs) before accepting.
  - If .env already exists, only variables that are still blank or missing
    get filled in — nothing that's already set (e.g. a live secret) is ever
    overwritten.
"""

from __future__ import annotations

import base64
import os
import shutil
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


def _prompt_yes_no(question: str) -> bool:
    """Ask a y/N question. Always answers No when not running interactively
    (e.g. Cloud-Init on first boot) rather than blocking or guessing."""
    if not sys.stdin.isatty():
        return False
    try:
        answer = input(f"{question} [y/N]: ").strip().lower()
    except EOFError:
        return False
    return answer in ("y", "yes")


# ---------------------------------------------------------------------------
# .env.staged renderer + .env filler
# ---------------------------------------------------------------------------


def _render_env_staged(
    example_path: Path,
    staged_path: Path,
    updates: dict[str, tuple[str, str]],
) -> list[str]:
    """Render .env.example + updates into .env.staged. Never touches .env.example.

    Args:
        example_path: Path to the (untouched) .env.example template.
        staged_path: Path to write the rendered .env.staged to.
        updates: Mapping of VAR_NAME to (new_value, short_note).
            The note is inserted as a comment above the variable line.

    Returns:
        List of variable names that were filled in.
    """
    import re

    present: set[str] = set()
    lines = example_path.read_text().splitlines()
    output: list[str] = []
    updated: list[str] = []

    for line in lines:
        stripped = line.strip()

        # Drop any stale pre-fill marker (e.g. left over from an old
        # .env.example that predates this script writing to .env.staged).
        if stripped.startswith(_PREFILL_MARKER):
            continue

        if stripped and "=" in stripped:
            if stripped.startswith("#"):
                # Commented-out variable, e.g. "# EXPRESSVPN_LAN_CIDR=" (an
                # opt-in var left disabled by default). Uncomment + replace it
                # the same as an active line if we have a value for it - don't
                # confuse this with prose like "# Example: KEY=value", which
                # this regex deliberately does not match.
                m = re.match(r"^#\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", stripped)
                if m:
                    key = m.group(1)
                    present.add(key)
                    if key in updates:
                        new_val, note = updates[key]
                        output.append(f"{_PREFILL_MARKER}: {note})")
                        output.append(f"{key}={new_val}")
                        updated.append(key)
                        continue
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

    staged_path.write_text("\n".join(output) + "\n")
    return updated


def _fill_missing_env_vars(
    path: Path,
    updates: dict[str, tuple[str, str]],
) -> list[str]:
    """Fill empty or missing variables in an existing .env file.

    Unlike _render_env_staged, this does NOT overwrite existing values.
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


# Vars worth calling out explicitly when offering to copy .env.staged to .env
# (secrets and auto-detected addresses can silently be wrong or sensitive).
_REVIEW_WORTHY = {
    "AUTHENTIK_SECRET_KEY",
    "DNS_BIND_IP",
    "PLEX_HOST",
    "EXPRESSVPN_LAN_CIDR",
}


def _stage_and_apply(
    stack: str,
    example_path: Path,
    updates: dict[str, tuple[str, str]],
) -> None:
    """Render .env.staged, then either fill an existing .env (blanks only)
    or offer to copy .env.staged to .env when none exists yet."""
    if not example_path.is_file():
        log.warning(f"{stack}: .env.example not found at {example_path}")
        return

    staged_path = example_path.with_name(".env.staged")
    env_path = example_path.with_name(".env")

    updated = _render_env_staged(example_path, staged_path, updates)
    if not updated:
        log.info(f"{stack}: nothing to stage")
        return
    log.info(f"{stack}: staged {', '.join(updated)} -> {staged_path.name}")

    if env_path.is_file():
        filled = _fill_missing_env_vars(env_path, updates)
        if filled:
            log.info(f"{stack}: filled missing .env vars: {', '.join(filled)}")
        return

    review = [v for v in updated if v in _REVIEW_WORTHY]
    log.warning(f"{stack}: no .env found.")
    if review:
        log.info(f"  Look these over before using them: {', '.join(review)}")
    log.info(
        f"  Configure options by editing {staged_path} before first deploy, "
        f"or copy it yourself: cp {staged_path} {env_path}"
    )
    if _prompt_yes_no(f"  Copy {staged_path.name} to .env now for {stack}?"):
        shutil.copy2(staged_path, env_path)
        log.info(f"  Copied to {env_path}. Review it before deploying.")
    else:
        log.info("  Skipped — copy it yourself when ready.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    setup_logging()
    log.info("--- Staging auto-detected .env values ---")

    # -- Shared identity / observability values (same for every stack) --
    uid = os.getuid()
    gid = os.getgid()
    docker_gid = _detect_docker_gid()
    proxmox_node = None
    proxmox_node_path = Path("/etc/homelab/proxmox-node")
    if proxmox_node_path.is_file():
        proxmox_node = proxmox_node_path.read_text().strip() or None
    hostname = socket.gethostname()

    def _identity_updates(role: str) -> dict[str, tuple[str, str]]:
        updates: dict[str, tuple[str, str]] = {
            "PUID": (str(uid), f"current user UID ({uid})"),
            "PGID": (str(gid), f"current user GID ({gid})"),
            "VM_HOSTNAME": (
                hostname,
                f"detected hostname ({hostname}); override if needed",
            ),
            "VM_ROLE": (role, f"stack role ({role})"),
        }
        if docker_gid is not None:
            updates["DOCKER_GID"] = (
                str(docker_gid),
                f"host docker group GID ({docker_gid})",
            )
        if proxmox_node:
            updates["PROXMOX_NODE"] = (
                proxmox_node,
                f"Proxmox node name ({proxmox_node}); override if needed",
            )
        return updates

    # -- Core stack --
    core_example = REPO_ROOT / "docker_compose" / "core" / ".env.example"
    core_updates = _identity_updates("core")
    core_updates["AUTHENTIK_SECRET_KEY"] = (
        _generate_secret_key(),
        "random secret via os.urandom; verify before use",
    )
    ip = _detect_lan_ip()
    if ip:
        core_updates["DNS_BIND_IP"] = (
            ip,
            f"detected LAN IP ({ip}); verify before use",
        )
    _stage_and_apply("core", core_example, core_updates)

    # -- Media stack --
    media_example = REPO_ROOT / "docker_compose" / "media" / ".env.example"
    media_updates = _identity_updates("media")
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
    _stage_and_apply("media", media_example, media_updates)

    # -- Monitoring stack --
    # Grafana admin password is not auto-filled; user sets it in .env. Bootstrap
    # verifies it is changed (or use --force for local testing).
    mon_example = REPO_ROOT / "docker_compose" / "monitoring" / ".env.example"
    mon_updates = _identity_updates("monitoring")

    # Derive media VM exporter targets from SCRAPE_TARGETS.
    # If .env exists and has a media: entry, pre-fill all media-hosted
    # exporter targets (ExpressVPN, scraparr, qBittorrent) with the same
    # hostname:ip — they all run on the media VM.
    mon_env = REPO_ROOT / "docker_compose" / "monitoring" / ".env"
    scrape_hosts = _parse_scrape_targets(mon_env if mon_env.is_file() else mon_example)
    media_ip = scrape_hosts.get("media")
    if media_ip:
        target = f"media:{media_ip}"
        for key in (
            "EXPRESSVPN_EXPORTER_TARGETS",
            "SCRAPARR_EXPORTER_TARGETS",
            "QBITTORRENT_EXPORTER_TARGETS",
        ):
            mon_updates[key] = (
                target,
                f"derived from SCRAPE_TARGETS (media VM at {media_ip})",
            )
    _stage_and_apply("monitoring", mon_example, mon_updates)

    # -- Accelerated stack --
    acc_example = REPO_ROOT / "docker_compose" / "accelerated" / ".env.example"
    acc_updates = _identity_updates("accelerated")
    if ip:
        acc_updates["PLEX_HOST"] = (
            ip,
            f"detected LAN IP ({ip}); address media VM uses to reach Plex",
        )
    _stage_and_apply("accelerated", acc_example, acc_updates)


if __name__ == "__main__":
    main()
