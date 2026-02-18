#!/usr/bin/env python3
"""Generate Caddyfile from .env variables.

Used by core bootstrap and update-caddyfile scripts.
Can be imported (call generate()) or run standalone.

CADDY_EXTRA_SERVICES format (comma-separated):
  FQDN:host:port[:sso]           — whole site; :sso = behind Authentik
  FQDN/path:host:port[:sso]      — path only (e.g. /api no SSO, / with SSO)
Examples:
  sonarr.example.com:192.168.1.130:8989:sso
  sonarr.example.com/api:192.168.1.130:8989
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.homelab_common import load_env, log, resolve_config_base, setup_logging

AUTHENTIK_COPY_HEADERS = (
    "X-Authentik-Username X-Authentik-Groups X-Authentik-Email "
    "X-Authentik-Name X-Authentik-Uid X-Authentik-Jwt "
    "X-Authentik-Meta-Jwks X-Authentik-Meta-Outpost X-Authentik-Meta-Provider "
    "X-Authentik-Meta-App X-Authentik-Meta-Version"
)


# ---------------------------------------------------------------------------
# Entry parsing
# ---------------------------------------------------------------------------


@dataclass
class _ExtraEntry:
    fqdn: str
    path: str  # "" for whole-site
    upstream: str  # host:port
    sso: bool


def _parse_entry(raw: str) -> _ExtraEntry | None:
    """Parse one CADDY_EXTRA_SERVICES entry."""
    raw = raw.strip()
    if not raw:
        return None

    fqdn_part, _, rest = raw.partition(":")
    if not rest or ":" not in rest:
        return None

    sso = rest.rsplit(":", 1)[-1] == "sso"
    if sso:
        rest = rest.rsplit(":", 1)[0]

    if ":" not in rest:
        return None

    upstream = rest

    if "/" in fqdn_part:
        fqdn, _, path_tail = fqdn_part.partition("/")
        path = "/" + path_tail
    else:
        fqdn = fqdn_part
        path = ""

    return _ExtraEntry(fqdn=fqdn, path=path, upstream=upstream, sso=sso)


# ---------------------------------------------------------------------------
# Caddyfile block builders
# ---------------------------------------------------------------------------


def _tls_directive(env: dict[str, str]) -> str:
    """Return '  tls internal' when enabled, else empty string."""
    val = env.get("CADDY_USE_INTERNAL_TLS", "")
    if val.lower() in ("true", "1", "yes"):
        return "  tls internal"
    return ""


def _global_block(env: dict[str, str]) -> str:
    domain = env.get("PUBLIC_BASE_DOMAIN", "example.com")
    return f"{{\n  email admin@{domain}\n}}"


def _authentik_block(env: dict[str, str], tls: str) -> str:
    fqdn = env.get("AUTHENTIK_FQDN", "auth.example.com")
    lines = [
        f"# Authentik web UI",
        f"{fqdn} {{",
    ]
    if tls:
        lines.append(tls)
    lines += [
        "  encode zstd gzip",
        "  reverse_proxy authentik-server:9000",
        "}",
    ]
    return "\n".join(lines)


def _whoami_block(env: dict[str, str], tls: str) -> str:
    fqdn = env.get("WHOAMI_FQDN", "whoami.example.com")
    cidrs = env.get("WHOAMI_ALLOW_CIDRS", "")
    lines = [
        "# whoami debug endpoint (text/plain enforced; optional IP allowlist)",
        f"{fqdn} {{",
    ]
    if tls:
        lines.append(tls)
    lines.append("  encode zstd gzip")

    if cidrs:
        lines += [
            f"  @whoami_allowed remote_ip {cidrs}",
            "  handle @whoami_allowed {",
            "    header Content-Type text/plain",
            "    reverse_proxy whoami:80",
            "  }",
            "  handle {",
            "    respond 403",
            "  }",
        ]
    else:
        lines += [
            "  header Content-Type text/plain",
            "  reverse_proxy whoami:80",
        ]

    lines.append("}")
    return "\n".join(lines)


def _extra_server_block(
    fqdn: str,
    entries: list[_ExtraEntry],
    tls: str,
    fa_uri: str,
    fa_backend: str,
) -> str:
    """Caddy server block for one FQDN with one or more path/upstream entries."""
    has_sso = any(e.sso for e in entries)
    lines = [f"# {fqdn}", f"{fqdn} {{"]

    if tls:
        lines.append(tls)
    lines += ["  encode zstd gzip", "  route {"]

    if has_sso:
        lines.append(
            f"    reverse_proxy /outpost.goauthentik.io/* {fa_backend}"
        )

    for entry in sorted(entries, key=lambda e: len(e.path), reverse=True):
        if entry.path:
            lines.append(f"    handle {entry.path}* {{")
        else:
            lines.append("    handle {")

        if entry.sso:
            lines += [
                f"      forward_auth {fa_backend} {{",
                f"        uri {fa_uri}",
                f"        copy_headers {AUTHENTIK_COPY_HEADERS}",
                "        trusted_proxies private_ranges",
                "      }",
            ]

        lines += [f"      reverse_proxy {entry.upstream}", "    }"]

    lines += ["  }", "}"]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate(env: dict[str, str], script_dir: Path) -> Path:
    """Generate the Caddyfile and return its path."""
    config_base = resolve_config_base(
        env.get("CONFIG_ROOT", "./config"), script_dir
    )
    caddyfile = config_base / "caddy" / "Caddyfile"

    if not caddyfile.parent.is_dir():
        log.error(
            f"Caddy config dir missing: {caddyfile.parent}. Run bootstrap first."
        )
        raise SystemExit(1)

    tls = _tls_directive(env)
    fa_uri = env.get(
        "AUTHENTIK_FORWARD_AUTH_URI", "/outpost.goauthentik.io/auth/caddy"
    )
    fa_backend = env.get(
        "AUTHENTIK_FORWARD_AUTH_BACKEND", "http://authentik-server:9000"
    )

    parts = [
        _global_block(env),
        "",
        _authentik_block(env, tls),
        "",
        _whoami_block(env, tls),
    ]

    extra_raw = env.get("CADDY_EXTRA_SERVICES", "")
    if extra_raw:
        groups: dict[str, list[_ExtraEntry]] = {}
        for token in extra_raw.split(","):
            entry = _parse_entry(token)
            if entry is None:
                log.warning(
                    f"Skipping malformed CADDY_EXTRA_SERVICES entry: "
                    f"{token.strip()!r}"
                )
                continue
            groups.setdefault(entry.fqdn, []).append(entry)

        if groups:
            parts.append("")
            parts.append("# Extra services (from CADDY_EXTRA_SERVICES)")
            for fqdn, entries in groups.items():
                parts.append("")
                parts.append(
                    _extra_server_block(fqdn, entries, tls, fa_uri, fa_backend)
                )

    caddyfile.write_text("\n".join(parts) + "\n")
    log.info(f"Wrote {caddyfile}")
    return caddyfile


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------


def main() -> None:
    setup_logging()
    env_file = SCRIPT_DIR / ".env"
    if not env_file.is_file():
        log.error(".env not found. Copy .env.example to .env and configure.")
        raise SystemExit(1)
    env = load_env(env_file)
    generate(env, SCRIPT_DIR)


if __name__ == "__main__":
    main()
