"""Core VM stack configuration — registry entry for deploy.py and bootstrap.

Defines the core stack's required vars, compose overlays, bootstrap steps,
and post-deploy hooks.  Consumed by BootstrapRunner and deploy.py.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from scripts.homelab_logging import StepTracker

SCRIPT_DIR = Path(__file__).resolve().parent

from scripts.homelab_common import (
    is_placeholder,
    try_chown,
    try_sudo_chown,
)

# ---------------------------------------------------------------------------
# Stack identity
# ---------------------------------------------------------------------------

STACK_NAME = "core"
COPIES_ENV_EXAMPLE = True
REQUIRES_DOCKER = True

REQUIRED_VARS = [
    "AUTHENTIK_SECRET_KEY",
    "AUTHENTIK_POSTGRES_PASSWORD",
    "PUBLIC_BASE_DOMAIN",
    "AUTHENTIK_FQDN",
    "WHOAMI_FQDN",
    "DNS_BIND_IP",
]

COMPOSE_OVERLAYS: list[tuple[str, str, str, str]] = [
    ("ENABLE_DDNS", "compose.ddclient.yml", "0", "DDNS"),
    ("ENABLE_OBSERVABILITY", "compose.observability.yml", "1", "Observability"),
]

POST_DEPLOY_ACTIONS = [
    "Create an Authentik admin user:\n"
    "  Open https://auth.<domain>/if/flow/initial-setup/\n"
    "  (or set AUTHENTIK_BOOTSTRAP_EMAIL/PASSWORD in .env for automatic creation)",
    "Assign users to SSO groups:\n"
    "  Set AUTHENTIK_ADMIN_USERS and/or AUTHENTIK_MEDIA_USERS in .env\n"
    "  then re-deploy (requires AUTHENTIK_BOOTSTRAP_TOKEN)",
]


# ---------------------------------------------------------------------------
# Bootstrap step functions
# ---------------------------------------------------------------------------

def _ensure_config_directories(
    env: dict[str, str], config_base: Path, tracker: StepTracker,
) -> None:
    enable_ddns = env.get("ENABLE_DDNS", "0") == "1"
    dirs = [
        config_base / "caddy",
        config_base / "caddy" / "data",
        config_base / "caddy" / "config",
        config_base / "caddy" / "site",
        config_base / "authentik" / "media",
        config_base / "authentik" / "media" / "public",
        config_base / "authentik" / "custom-templates",
        config_base / "authentik" / "blueprints",
        config_base / "authentik" / "postgresql",
        config_base / "authentik" / "redis",
        config_base / "dnsmasq",
    ]
    if enable_ddns:
        dirs.append(config_base / "ddclient")

    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
        tracker.detail(f"Created {d}")

    tracker.success(f"{len(dirs)} directories ready")


def _ensure_config_writable(
    config_base: Path, tracker: StepTracker,
) -> None:
    test_file = config_base / "caddy" / ".bootstrap_write_test"
    try:
        test_file.touch()
        test_file.unlink()
        return
    except (PermissionError, OSError):
        pass

    uid, gid = os.getuid(), os.getgid()
    if try_sudo_chown(config_base, uid, gid, recursive=True):
        tracker.detail("Set config ownership to current user via sudo")
    else:
        tracker.fail(
            f"Cannot write to {config_base}. Run once:\n"
            f"  sudo chown -R $(id -u):$(id -g) {config_base}"
        )
        raise SystemExit(1)


def _ensure_authentik_media_permissions(
    env: dict[str, str], config_base: Path, tracker: StepTracker,
) -> None:
    media_dir = config_base / "authentik" / "media"
    uid = int(env.get("AUTHENTIK_UID", "1000"))
    gid = int(env.get("AUTHENTIK_GID", "1000"))

    if try_chown(media_dir, uid, gid, recursive=True):
        tracker.detail(f"Set authentik media ownership to {uid}:{gid}")
    elif try_sudo_chown(media_dir, uid, gid, recursive=True):
        tracker.detail(f"Set authentik media ownership to {uid}:{gid} (via sudo)")
    else:
        tracker.warn(
            f"Could not chown {media_dir} to {uid}:{gid}. "
            "Authentik may fail with permission errors."
        )

    for cmd_prefix in [[], ["sudo", "-n"]]:
        try:
            subprocess.run(
                cmd_prefix + ["chmod", "-R", "ug+rwX", str(media_dir)],
                check=True, capture_output=True,
            )
            break
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue


def _generate_caddyfile(
    env: dict[str, str], config_base: Path, tracker: StepTracker,
) -> None:
    from docker_compose.core.scripts import gen_caddyfile
    gen_caddyfile.generate(env, SCRIPT_DIR)

    caddyfile = config_base / "caddy" / "Caddyfile"
    if caddyfile.is_dir():
        tracker.fail(
            f"{caddyfile} exists as a directory. "
            f"Remove it and re-run: rm -rf '{caddyfile}'"
        )
        raise SystemExit(1)
    if not caddyfile.is_file():
        tracker.fail(f"Caddyfile missing after generation: {caddyfile}")
        raise SystemExit(1)
    tracker.success("Caddyfile written")


def _generate_authentik_blueprint(
    env: dict[str, str], config_base: Path, tracker: StepTracker,
) -> None:
    from docker_compose.core.scripts import gen_authentik_blueprint
    gen_authentik_blueprint.generate(env, SCRIPT_DIR)
    tracker.success("Authentik blueprint generated")


def _ensure_dnsmasq_config(
    env: dict[str, str], config_base: Path, tracker: StepTracker,
) -> None:
    conf = config_base / "dnsmasq" / "dnsmasq.conf"

    if conf.is_file():
        tracker.detail("dnsmasq.conf already exists; not overwriting")
        tracker.success("DNS config ready")
        return
    if conf.is_dir():
        shutil.rmtree(conf)

    upstream1 = env.get("DNS_UPSTREAM_1", "1.1.1.1")
    upstream2 = env.get("DNS_UPSTREAM_2", "1.0.0.1")
    domain = env.get("DNS_LOCAL_DOMAIN", "lab.arpa")

    lines = [
        "# dnsmasq starter config (generated by bootstrap)",
        "domain-needed",
        "bogus-priv",
        "no-resolv",
        f"server={upstream1}",
        f"server={upstream2}",
        f"local=/{domain}/",
        "expand-hosts",
        f"domain={domain}",
        "cache-size=1000",
    ]

    local_records = env.get("DNS_LOCAL_RECORDS") or ""
    has_local = False
    for pair in local_records.split(","):
        pair = pair.strip()
        if ":" not in pair:
            continue
        host, ip = pair.split(":", 1)
        if host and ip and host != ip:
            if not has_local:
                lines.append("")
                lines.append("# VM hostname records (from DNS_LOCAL_RECORDS)")
                has_local = True
            lines.append(f"address=/{host}.{domain}/{ip}")

    core_ip = env.get("DNS_BIND_IP", "").strip()
    caddy_fqdns = _collect_caddy_fqdns(env)
    if caddy_fqdns and core_ip and not is_placeholder(core_ip):
        lines.append("")
        lines.append("# Caddy FQDN records (auto-generated)")
        for fqdn in caddy_fqdns:
            lines.append(f"address=/{fqdn}/{core_ip}")

    if not has_local and not caddy_fqdns:
        lines.append("")
        lines.append(f"# address=/apps.{domain}/192.168.1.120")

    conf.write_text("\n".join(lines) + "\n")
    tracker.success("DNS config created")


def _collect_caddy_fqdns(env: dict[str, str]) -> list[str]:
    fqdns: list[str] = []
    for var in ("AUTHENTIK_FQDN", "WHOAMI_FQDN"):
        val = env.get(var, "").strip()
        if val and not is_placeholder(val):
            fqdns.append(val)

    base_domain = env.get("PUBLIC_BASE_DOMAIN", "example.com")
    seen: set[str] = set(fqdns)
    raw = env.get("CADDY_EXTRA_SERVICES", "")
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        fqdn = token.split(":")[0].split("/")[0]
        if "." not in fqdn and base_domain:
            fqdn = f"{fqdn}.{base_domain}"
        if fqdn and fqdn not in seen and not is_placeholder(fqdn):
            seen.add(fqdn)
            fqdns.append(fqdn)
    return fqdns


def _ensure_ddclient_config(
    env: dict[str, str], config_base: Path, tracker: StepTracker,
) -> None:
    if env.get("ENABLE_DDNS", "0") != "1":
        tracker.detail("DDNS disabled; skipping ddclient config")
        tracker.success("DDNS check done")
        return

    conf = config_base / "ddclient" / "ddclient.conf"
    if conf.is_file():
        tracker.detail("ddclient.conf already exists")
        tracker.success("DDNS config ready")
        return
    if conf.is_dir():
        shutil.rmtree(conf)

    password = env.get("DDNS_PASSWORD", "").strip()
    if not password or is_placeholder(password):
        tracker.fail(
            "DDNS_PASSWORD is required when ENABLE_DDNS=1. "
            "Set it in .env and re-run bootstrap."
        )
        raise SystemExit(1)

    protocol = env.get("DDNS_PROTOCOL", "namecheap")
    server = env.get("DDNS_SERVER", "dynamicdns.park-your-domain.com")
    domain = env.get("PUBLIC_BASE_DOMAIN", "example.com")
    interval = env.get("DDNS_INTERVAL", "300")

    hosts_raw = env.get("DDNS_HOSTS", "@, *")
    hosts = [h.strip() for h in hosts_raw.split(",") if h.strip()]

    lines = [
        "# ddclient config (generated by bootstrap)",
        "# Docs: https://ddclient.net/",
        f"daemon={interval}",
        "ssl=yes",
        f"use=web, web={server}/getip",
        f"protocol={protocol}",
        f"server={server}",
        f"login={domain}",
        f"password='{password}'",
    ]
    for host in hosts:
        lines.append(host)

    conf.parent.mkdir(parents=True, exist_ok=True)
    conf.write_text("\n".join(lines) + "\n")
    tracker.success("DDNS config created")


def _reload_caddy_if_running(
    env: dict[str, str], tracker: StepTracker,
) -> None:
    container = env.get("CADDY_CONTAINER", "caddy")
    try:
        result = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"],
            capture_output=True, text=True, check=True,
        )
        if container in result.stdout.strip().splitlines():
            subprocess.run(
                ["docker", "exec", container, "caddy", "reload",
                 "--config", "/etc/caddy-config/Caddyfile"],
                check=True,
            )
            tracker.detail("Caddy reloaded")
    except subprocess.CalledProcessError:
        pass


# ---------------------------------------------------------------------------
# Bootstrap steps (consumed by BootstrapRunner)
# ---------------------------------------------------------------------------

def bootstrap_steps(
    env: dict[str, str], config_base: Path, args,  # noqa: ANN001
) -> list[tuple[str, object]]:
    """Return ordered bootstrap steps for the core stack."""
    return [
        ("Creating config directories", lambda t: (
            _ensure_config_directories(env, config_base, t),
            _ensure_config_writable(config_base, t),
            _ensure_authentik_media_permissions(env, config_base, t),
        )),
        ("Generating Caddyfile", lambda t: _generate_caddyfile(env, config_base, t)),
        ("Generating Authentik blueprint", lambda t: _generate_authentik_blueprint(env, config_base, t)),
        ("Generating DNS config", lambda t: _ensure_dnsmasq_config(env, config_base, t)),
        ("Checking DDNS config", lambda t: _ensure_ddclient_config(env, config_base, t)),
        ("Reloading Caddy", lambda t: _reload_caddy_if_running(env, t)),
    ]


# ---------------------------------------------------------------------------
# Post-deploy hook (called by deploy.py after docker compose up)
# ---------------------------------------------------------------------------

def post_deploy(
    env: dict[str, str], config_base: Path, tracker: StepTracker,
) -> None:
    """Apply Authentik blueprint via API after core stack is up."""
    if not env.get("AUTHENTIK_BOOTSTRAP_TOKEN"):
        return

    blueprint_path = (
        config_base / "authentik" / "blueprints" / "homelab-sso.yaml"
    )
    if not blueprint_path.resolve().is_file():
        return

    try:
        from docker_compose.core.scripts import apply_authentik_blueprint
        apply_authentik_blueprint.apply(env, SCRIPT_DIR)
        tracker.success("Authentik blueprint applied")
    except Exception as exc:
        tracker.warn(f"Authentik blueprint apply failed: {exc}")