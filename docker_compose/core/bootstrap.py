#!/usr/bin/env python3
"""Core VM bootstrap (idempotent).

Owns:
  - creating/validating local .env for this stack
  - creating core config directories and starter files
  - generating Caddyfile from .env
  - validating compose syntax
  - optional local bring-up with --up

Does NOT own:
  - symlinks, shell helper functions, or cross-stack orchestration (deploy.py)

Usage:
  cd docker_compose/core && python3 bootstrap.py
  cd docker_compose/core && python3 bootstrap.py --up
  cd docker_compose/core && python3 bootstrap.py --force
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

from scripts.homelab_common import (
    is_placeholder,
    load_env,
    log,
    require_docker,
    resolve_config_base,
    setup_logging,
    try_chown,
    try_sudo_chown,
)

import gen_caddyfile


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Core VM bootstrap (idempotent).",
        epilog=(
            "Authentik: Set AUTHENTIK_BOOTSTRAP_EMAIL and "
            "AUTHENTIK_BOOTSTRAP_PASSWORD in .env before first start to "
            "create the default akadmin user without visiting the "
            "initial-setup UI.\n\n"
            "Common errors after 'docker compose up':\n"
            "  - Caddy: 'Caddyfile: no such file or directory'\n"
            "      → Run bootstrap from the same dir where you run "
            "docker compose.\n"
            "  - Authentik: 'Permission denied: /media/public'\n"
            "      → sudo chown -R 1000:1000 <CONFIG_ROOT>/authentik/media\n"
            "  - dnsmasq: 'failed to read configuration file'\n"
            "      → Same as Caddy; run bootstrap from stack dir."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--up", action="store_true", help="Start stack after bootstrap."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip placeholder guardrails for local testing.",
    )
    parser.add_argument(
        "--non-interactive",
        "-y",
        action="store_true",
        help="Accepted for deploy.py compatibility (no-op).",
    )
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Bootstrap steps (same order as the original shell script)
# ---------------------------------------------------------------------------


def prepare_env_file() -> dict[str, str]:
    """Ensure .env exists (copy from .env.example if needed) and load it."""
    env_file = SCRIPT_DIR / ".env"
    env_example = SCRIPT_DIR / ".env.example"

    if env_file.is_file():
        return load_env(env_file)

    if not env_example.is_file():
        log.error(f"Missing {env_example}; cannot initialize .env.")
        raise SystemExit(1)

    shutil.copy2(env_example, env_file)
    log.info("Created .env from .env.example.")
    log.info(f"Fill real values in {env_file}, then re-run bootstrap.")
    raise SystemExit(1)


def validate_guardrails(env: dict[str, str]) -> None:
    """Verify required vars are not placeholders."""
    checks = {
        "AUTHENTIK_SECRET_KEY": "Generate one with: openssl rand -base64 60",
        "AUTHENTIK_POSTGRES_PASSWORD": None,
        "PUBLIC_BASE_DOMAIN": (
            "Set your real domain, or use --force only for local testing."
        ),
    }
    for var, hint in checks.items():
        if is_placeholder(env.get(var)):
            msg = f"{var} is missing/placeholder in .env."
            if hint:
                msg += f"\n{hint}"
            log.error(msg)
            raise SystemExit(1)


def ensure_config_directories(
    config_base: Path, enable_ddns: bool
) -> None:
    dirs = [
        config_base / "caddy",
        config_base / "caddy" / "data",
        config_base / "caddy" / "config",
        config_base / "caddy" / "site",
        config_base / "authentik" / "media",
        config_base / "authentik" / "media" / "public",
        config_base / "authentik" / "custom-templates",
        config_base / "authentik" / "postgresql",
        config_base / "authentik" / "redis",
        config_base / "dnsmasq",
    ]
    if enable_ddns:
        dirs.append(config_base / "ddclient")

    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)

    log.info(f"Ensured config directories under: {config_base}")


def ensure_config_writable(config_base: Path) -> None:
    """Make sure the current user can write into the config tree."""
    test_file = config_base / "caddy" / ".bootstrap_write_test"
    try:
        test_file.touch()
        test_file.unlink()
        return
    except (PermissionError, OSError):
        pass

    uid, gid = os.getuid(), os.getgid()
    if try_sudo_chown(config_base, uid, gid, recursive=True):
        log.info(
            "Set config ownership to current user (via sudo) so bootstrap "
            "can write Caddyfile and dnsmasq.conf."
        )
    else:
        log.error(
            f"Cannot write to {config_base} (likely root-owned from Docker). "
            "Run once:\n"
            f"  sudo chown -R $(id -u):$(id -g) {config_base}"
        )
        raise SystemExit(1)


def ensure_authentik_media_permissions(
    config_base: Path, env: dict[str, str]
) -> None:
    """Authentik runs as UID 1000; ensure /media/public is writable."""
    media_dir = config_base / "authentik" / "media"
    uid = int(env.get("AUTHENTIK_UID", "1000"))
    gid = int(env.get("AUTHENTIK_GID", "1000"))

    if try_chown(media_dir, uid, gid, recursive=True):
        log.info(f"Set authentik media ownership to {uid}:{gid}.")
    elif try_sudo_chown(media_dir, uid, gid, recursive=True):
        log.info(f"Set authentik media ownership to {uid}:{gid} (via sudo).")
    else:
        log.warning(
            f"Could not chown {media_dir} to {uid}:{gid}.\n"
            "If Authentik fails with 'Permission denied: /media/public', "
            "run once:\n"
            f"  sudo chown -R {uid}:{gid} {media_dir}"
        )

    for cmd_prefix in [[], ["sudo", "-n"]]:
        try:
            subprocess.run(
                cmd_prefix + ["chmod", "-R", "ug+rwX", str(media_dir)],
                check=True,
                capture_output=True,
            )
            break
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue


def validate_file_ready(path: Path, label: str) -> None:
    """Fail fast if *path* is missing or accidentally a directory."""
    if path.is_dir():
        log.error(
            f"{path} exists as a directory "
            "(often from a previous bind-mount before bootstrap).\n"
            f"Remove it and re-run bootstrap: rm -rf '{path}'"
        )
        raise SystemExit(1)
    if not path.is_file():
        log.error(
            f"{label} missing: {path}\n"
            "Re-run bootstrap from docker_compose/core."
        )
        raise SystemExit(1)


def ensure_starter_dnsmasq_conf(
    config_base: Path, env: dict[str, str]
) -> None:
    conf = config_base / "dnsmasq" / "dnsmasq.conf"

    if conf.is_file():
        log.info("dnsmasq.conf already exists; not overwriting.")
        return
    if conf.is_dir():
        shutil.rmtree(conf)

    upstream1 = env.get("DNS_UPSTREAM_1", "1.1.1.1")
    upstream2 = env.get("DNS_UPSTREAM_2", "1.0.0.1")
    domain = env.get("DNS_LOCAL_DOMAIN", "lab.arpa")

    lines = [
        "# dnsmasq starter config (generated by bootstrap)",
        "# Keep this file small and explicit for predictable operations.",
        "domain-needed",
        "bogus-priv",
        "no-resolv",
        f"server={upstream1}",
        f"server={upstream2}",
        f"local=/{domain}/",
        "expand-hosts",
        f"domain={domain}",
        "cache-size=1000",
        "",
        "# Optional static records:",
        f"# address=/apps.{domain}/192.168.1.120",
        f"# address=/media.{domain}/192.168.1.130",
    ]

    for pair in (env.get("DNS_LOCAL_RECORDS") or "").split(","):
        pair = pair.strip()
        if ":" not in pair:
            continue
        host, ip = pair.split(":", 1)
        if host and ip and host != ip:
            lines.append(f"address=/{host}.{domain}/{ip}")

    conf.write_text("\n".join(lines) + "\n")
    log.info(f"Created starter dnsmasq config: {conf}")


def ensure_starter_ddclient_conf(
    config_base: Path, enable_ddns: bool
) -> None:
    if not enable_ddns:
        return

    conf = config_base / "ddclient" / "ddclient.conf"
    if conf.is_file():
        return
    if conf.is_dir():
        shutil.rmtree(conf)

    conf.parent.mkdir(parents=True, exist_ok=True)
    conf.write_text(
        "# ddclient starter config (optional DDNS) — edit with your "
        "provider and credentials.\n"
        "# Start the container with: docker compose --profile ddns up -d\n"
        "# Docs: https://ddclient.net/  and  docs/Chapter2a-core.md\n"
        "#\n"
        "# Namecheap example (get DDNS password from Namecheap domain "
        "list -> Manage -> Dynamic DNS):\n"
        "# protocol=namecheap\n"
        "# server=dynamicdns.park-your-domain.com\n"
        "# login=yourdomain.com\n"
        "# password=YOUR_DDNS_PASSWORD\n"
        "# yourhost.yourdomain.com\n"
        "#\n"
        "# Cloudflare example (use API token with Zone:DNS Edit):\n"
        "# protocol=cloudflare\n"
        "# zone=yourdomain.com\n"
        "# password=YOUR_CLOUDFLARE_API_TOKEN\n"
        "# yourhost.yourdomain.com\n"
        "#\n"
        "# Uncomment and fill one block above, then restart ddclient.\n"
    )
    log.info(f"Created starter ddclient config: {conf}")


def build_compose_files(
    env: dict[str, str],
) -> list[str]:
    """Return docker compose ``-f`` args for this stack."""
    files = ["-f", str(SCRIPT_DIR / "compose.yml")]
    if env.get("ENABLE_DDNS", "0") == "1":
        files += ["-f", str(SCRIPT_DIR / "compose.ddclient.yml")]
    return files


def validate_compose(compose_files: list[str]) -> None:
    result = subprocess.run(
        ["docker", "compose"] + compose_files + ["config"],
        capture_output=True,
        cwd=SCRIPT_DIR,
    )
    if result.returncode != 0:
        log.error("docker compose config validation failed.")
        raise SystemExit(1)
    log.info("Compose file validates successfully.")


def bring_up_stack(
    compose_files: list[str], env: dict[str, str]
) -> None:
    log.info("Starting core stack...")
    subprocess.run(
        ["docker", "compose"] + compose_files + ["up", "-d"],
        check=True,
        cwd=SCRIPT_DIR,
    )
    log.info("Core stack started.")
    if env.get("AUTHENTIK_BOOTSTRAP_EMAIL") and env.get(
        "AUTHENTIK_BOOTSTRAP_PASSWORD"
    ):
        log.info(
            "Authentik: AUTHENTIK_BOOTSTRAP_* set — initial akadmin user "
            "will be created on first start (no UI setup needed)."
        )


def reload_caddy_if_running(env: dict[str, str]) -> None:
    container = env.get("CADDY_CONTAINER", "caddy")
    try:
        result = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            check=True,
        )
        if container in result.stdout.strip().splitlines():
            subprocess.run(
                [
                    "docker",
                    "exec",
                    container,
                    "caddy",
                    "reload",
                    "--config",
                    "/etc/caddy-config/Caddyfile",
                ],
                check=True,
            )
            log.info("Caddy reloaded.")
    except subprocess.CalledProcessError:
        pass


def print_summary(
    config_base: Path, enable_ddns: bool
) -> None:
    log.info("")
    log.info(f"Config: {config_base}")
    log.info(
        "Caddyfile is generated from .env (set CADDY_EXTRA_SERVICES for "
        "more services). Re-run bootstrap or deploy to apply .env changes."
    )
    log.info("")
    log.info(
        "If Caddy or dnsmasq can't find their config, run bootstrap from "
        "the same directory where you run docker compose:"
    )
    log.info(f"  cd {SCRIPT_DIR} && python3 bootstrap.py")
    log.info("")
    log.info("--- Service status ---")
    status = "enabled (ddclient overlay)" if enable_ddns else "disabled"
    log.info(f"  DDNS: {status}")
    if enable_ddns:
        log.info(
            "  Edit config/ddclient/ddclient.conf with your provider "
            "and credentials."
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    setup_logging()
    log.info("--- Core VM bootstrap ---")

    require_docker()
    env = prepare_env_file()

    compose_files = build_compose_files(env)

    if not args.force:
        validate_guardrails(env)

    config_base = resolve_config_base(
        env.get("CONFIG_ROOT", "./config"), SCRIPT_DIR
    )
    enable_ddns = env.get("ENABLE_DDNS", "0") == "1"

    ensure_config_directories(config_base, enable_ddns)
    ensure_config_writable(config_base)
    ensure_authentik_media_permissions(config_base, env)

    gen_caddyfile.generate(env, SCRIPT_DIR)
    validate_file_ready(config_base / "caddy" / "Caddyfile", "Caddyfile")

    ensure_starter_dnsmasq_conf(config_base, env)
    validate_file_ready(
        config_base / "dnsmasq" / "dnsmasq.conf", "dnsmasq config"
    )

    ensure_starter_ddclient_conf(config_base, enable_ddns)
    validate_compose(compose_files)

    deploy_mode = bool(os.environ.get("HOMELAB_DEPLOY"))

    if args.up:
        bring_up_stack(compose_files, env)
    else:
        if deploy_mode:
            log.info("Bootstrap complete.")
        else:
            log.info(
                "Bootstrap complete. Run 'docker compose up -d' "
                "(or use the core alias) when ready."
            )

    reload_caddy_if_running(env)
    if not deploy_mode:
        print_summary(config_base, enable_ddns)


if __name__ == "__main__":
    main()
