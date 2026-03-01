#!/usr/bin/env python3
"""Generate Authentik blueprint YAML from .env (CADDY_EXTRA_SERVICES + AUTHENTIK_FQDN).

Creates one Proxy Provider (forward_single) and one Application per SSO-protected
FQDN in CADDY_EXTRA_SERVICES, so Authentik can be configured without the GUI.
Apply in Authentik: Customization → Blueprints → Create, then paste this file's contents.

Used by core bootstrap. Can be imported (call generate()) or run standalone.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.homelab_common import load_env, log, resolve_config_base, setup_logging

# Reuse Caddy extra-services parsing so SSO apps stay in sync with Caddyfile.
import gen_caddyfile as _gen_caddyfile

# Default flows shipped with Authentik (used for proxy providers).
DEFAULT_AUTHZ_FLOW_SLUG = "default-provider-authorization-implicit-consent"
DEFAULT_INVALIDATION_FLOW_SLUG = "default-provider-invalidation-flow"


def _slug_from_fqdn(fqdn: str) -> str:
    """Derive a safe slug from FQDN (e.g. sonarr.example.com -> sonarr)."""
    first = fqdn.split(".")[0] if fqdn else "app"
    # Alphanumeric and hyphen only
    return re.sub(r"[^a-z0-9-]", "-", first.lower()).strip("-") or "app"


def _name_from_fqdn(fqdn: str) -> str:
    """Friendly name from FQDN (e.g. sonarr.example.com -> Sonarr)."""
    first = fqdn.split(".")[0] if fqdn else "App"
    return first.strip().title() or "App"


def _collect_sso_fqdns(env: dict[str, str]) -> list[tuple[str, str, str]]:
    """Return list of (fqdn, slug, name) for each SSO entry in CADDY_EXTRA_SERVICES (whole-site only)."""
    raw = env.get("CADDY_EXTRA_SERVICES", "")
    seen: set[str] = set()
    out: list[tuple[str, str, str]] = []
    for token in raw.split(","):
        entry = _gen_caddyfile._parse_entry(token)
        if entry is None or not entry.sso or entry.path != "":
            continue
        if entry.fqdn in seen:
            continue
        seen.add(entry.fqdn)
        out.append((entry.fqdn, _slug_from_fqdn(entry.fqdn), _name_from_fqdn(entry.fqdn)))
    return out


def _build_blueprint_yaml(env: dict[str, str]) -> str:
    """Build blueprint YAML string from env."""
    auth_fqdn = env.get("AUTHENTIK_FQDN", "auth.example.com").strip()
    if "://" not in auth_fqdn:
        auth_fqdn = "https://" + auth_fqdn
    elif auth_fqdn.startswith("http://"):
        auth_fqdn = "https://" + auth_fqdn[7:]

    sso_apps = _collect_sso_fqdns(env)
    lines = [
        "# yaml-language-server: $schema=https://goauthentik.io/blueprints/schema.json",
        "# Generated from .env (AUTHENTIK_FQDN, CADDY_EXTRA_SERVICES with :sso).",
        "# Apply in Authentik: Customization → Blueprints → Create, then paste this file.",
        "",
        "version: 1",
        "metadata:",
        "  name: homelab-sso",
        "  labels:",
        "    blueprints.goauthentik.io/description: \"Homelab SSO (Caddy forward auth)\"",
        "",
        "entries:",
    ]

    for fqdn, slug, name in sso_apps:
        external_host = "https://" + fqdn if "://" not in fqdn else fqdn
        provider_id = f"provider_{slug}"
        lines.extend([
            "  # --- " + name + " ---",
            "  - model: authentik_providers_proxy.proxyprovider",
            "    id: " + provider_id,
            "    state: present",
            "    identifiers:",
            "      name: " + repr("Homelab " + name),
            "    attrs:",
            "      name: " + repr("Homelab " + name),
            "      external_host: " + repr(external_host),
            "      mode: forward_single",
            "      authorization_flow: !Find [authentik_flows.flow, [slug, " + repr(DEFAULT_AUTHZ_FLOW_SLUG) + "]]",
            "      invalidation_flow: !Find [authentik_flows.flow, [slug, " + repr(DEFAULT_INVALIDATION_FLOW_SLUG) + "]]",
            "  - model: authentik_core.application",
            "    id: app_" + slug,
            "    state: present",
            "    identifiers:",
            "      slug: " + repr(slug),
            "    attrs:",
            "      name: " + repr(name),
            "      slug: " + repr(slug),
            "      provider: !KeyOf " + provider_id,
            "",
        ])

    # Assign all proxy providers to the embedded outpost so forward auth works.
    # Without this, the /outpost.goauthentik.io/ endpoint won't serve any providers.
    lines.extend([
        "  # --- Embedded Outpost (forward auth endpoint) ---",
        "  - model: authentik_outposts.outpost",
        "    state: present",
        "    identifiers:",
        "      managed: 'goauthentik.io/outposts/embedded'",
        "    attrs:",
        "      name: 'authentik Embedded Outpost'",
        "      type: proxy",
        "      providers:",
    ])
    for _fqdn, slug, _name in sso_apps:
        lines.append("        - !KeyOf provider_" + slug)
    lines.append("")

    return "\n".join(lines)


def generate(env: dict[str, str], script_dir: Path) -> Path | None:
    """Write Authentik blueprint to CONFIG_ROOT/authentik/blueprints/homelab-sso.yaml.

    Uses AUTHENTIK_FQDN and SSO entries from CADDY_EXTRA_SERVICES.
    Returns the path written, or None if no SSO apps (file not written).
    """
    config_base = resolve_config_base(
        env.get("CONFIG_ROOT", "./config"), script_dir
    )
    blueprints_dir = config_base / "authentik" / "blueprints"
    blueprints_dir.mkdir(parents=True, exist_ok=True)
    path = blueprints_dir / "homelab-sso.yaml"

    sso_apps = _collect_sso_fqdns(env)
    if not sso_apps:
        if path.is_file():
            log.info(
                "Authentik blueprint: no SSO entries in CADDY_EXTRA_SERVICES; "
                "leaving existing %s unchanged.",
                path.name,
            )
        return None

    content = _build_blueprint_yaml(env)
    path.write_text(content, encoding="utf-8")
    log.info(
        "Wrote Authentik blueprint %s (%d app(s): %s).",
        path,
        len(sso_apps),
        ", ".join(name for _, _, name in sso_apps),
    )
    return path


def main() -> None:
    setup_logging()
    env_file = SCRIPT_DIR / ".env"
    if not env_file.is_file():
        log.error("No .env in %s; run bootstrap first or copy .env.example to .env.", SCRIPT_DIR)
        sys.exit(1)
    env = load_env(env_file)
    result = generate(env, SCRIPT_DIR)
    if result is None:
        log.info("Set CADDY_EXTRA_SERVICES with :sso entries to generate the blueprint.")


if __name__ == "__main__":
    main()
