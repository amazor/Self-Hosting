#!/usr/bin/env python3
"""Print ready-to-paste iperf3 client commands and sanity-check the server.

The iperf3 server runs 24/7 on the Accelerated VM (see compose.yml) alongside
Plex, so a test against this VM traverses the same router -> VM path that real
remote-Plex streaming uses. The test is two-sided: you run an iperf3 *client*
from the remote internet location where Plex misbehaves. This helper reads
``.env`` and prints the exact commands to run remotely, plus a quick local check
that the server container is healthy.

Diagnosing remote Plex:
  - Plex remote streaming is server -> client (your home UPLOAD) over TCP/HTTPS,
    so the ``-R`` (reverse) TCP test is the most representative bandwidth check.
  - A UDP ``-R`` run exposes jitter / packet loss, which cause stutter even when
    raw bandwidth looks fine.

Run from the stack directory:
  python3 scripts/iperf3_test.py            # print commands + local sanity check
  python3 scripts/iperf3_test.py --no-check # print commands only
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
STACK_DIR = SCRIPT_DIR.parent
REPO_ROOT = STACK_DIR.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.homelab_common import load_env, log, setup_logging

# Approximate bandwidth Plex needs per stream (direct play / transcode target),
# including container/protocol overhead. Used to interpret iperf3 results.
PLEX_BANDWIDTH_REFERENCE = [
    ("720p (3-4 Mbps stream)", "~5 Mbps"),
    ("1080p (8-12 Mbps stream)", "~15 Mbps"),
    ("4K HDR (40-60 Mbps stream)", "~60 Mbps"),
]

PUBLIC_HOST_PLACEHOLDER = "<PUBLIC_DOMAIN_OR_IP>"


def _client_presets(host: str, port: str) -> list[tuple[str, str, str]]:
    """Return (label, command, why) tuples for the remote client to run."""
    base = f"iperf3 -c {host} -p {port}"
    return [
        (
            "1) Bandwidth (TCP, Plex-like)",
            f"{base} -R -t 30 -O 5",
            "Reverse = server uploads to you (the remote-Plex bottleneck). "
            "The most representative single number.",
        ),
        (
            "2) Quality (UDP jitter/loss)",
            f"{base} -u -R -b 25M -t 30",
            "Set -b near your target stream bitrate; read the jitter and "
            "lost/total datagrams. Loss/jitter cause stutter even with bandwidth.",
        ),
        (
            "3) Saturation (parallel streams)",
            f"{base} -R -P 4 -t 30",
            "If 4 streams beat 1 substantially, a single connection is "
            "window/latency-limited. Plex uses ~1 stream, so preset 1 is realistic.",
        ),
    ]


def _print_commands(env: dict[str, str]) -> None:
    port = env.get("IPERF3_PORT", "5201").strip() or "5201"

    print("\n=== Run these from the REMOTE location (where Plex stutters) ===")
    print(
        f"    Replace {PUBLIC_HOST_PLACEHOLDER} with your home's public domain or "
        "public IP.\n"
    )
    for label, cmd, why in _client_presets(PUBLIC_HOST_PLACEHOLDER, port):
        print(f"{label}")
        print(f"    {cmd}")
        print(f"    -> {why}\n")

    plex_host = env.get("PLEX_HOST", "").strip()
    if plex_host:
        print(
            f"LAN sanity test (from another machine on the LAN, no port-forward "
            f"needed):\n    iperf3 -c {plex_host} -p {port} -R -t 10\n"
        )

    print("=== Plex bandwidth reference (need >= upload result) ===")
    for quality, needed in PLEX_BANDWIDTH_REFERENCE:
        print(f"    {quality:<32} {needed}")

    print(
        "\n=== Reachability ===\n"
        f"    The server runs 24/7 but is internet-reachable ONLY while a router\n"
        f"    port-forward of TCP+UDP {port} -> this VM's LAN IP is enabled.\n"
        "    iperf3 has NO AUTH: remove the forward as soon as you finish testing."
    )


def _sanity_check(env: dict[str, str]) -> bool:
    """Run a 1s loopback client inside the container to confirm it's serving."""
    container = env.get("IPERF3_CONTAINER", "iperf3")
    port = env.get("IPERF3_PORT", "5201").strip() or "5201"

    try:
        running = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"],
            capture_output=True, text=True, check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        log.warning("Could not run 'docker ps'; skipping local sanity check.")
        return False

    if container not in running.stdout.split():
        log.warning(
            f"Container {container!r} is not running. Start the accelerated stack "
            "(deploy.py accelerated or docker compose up -d) before testing."
        )
        return False

    print("\n=== Local sanity check (loopback inside container) ===")
    try:
        result = subprocess.run(
            ["docker", "exec", container,
             "iperf3", "-c", "127.0.0.1", "-p", port, "-t", "1"],
            capture_output=True, text=True, check=True,
        )
    except subprocess.CalledProcessError as exc:
        log.warning(f"Loopback test failed:\n{exc.stderr or exc.stdout}")
        return False

    print(result.stdout.strip())
    log.info("iperf3 server is up and accepting connections.")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-check", action="store_true",
        help="Skip the local container sanity check.",
    )
    args = parser.parse_args()

    setup_logging()
    env_file = STACK_DIR / ".env"
    if not env_file.is_file():
        log.error(".env not found. Copy .env.example to .env and configure.")
        return 1

    env = load_env(env_file)
    _print_commands(env)
    if not args.no_check:
        _sanity_check(env)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
