#!/usr/bin/env bash
# Generate Caddyfile from .env (no reload).
# Used by bootstrap and by update-caddyfile.sh. Run from docker_compose/core.
#
# CADDY_EXTRA_SERVICES format (comma-separated):
#   FQDN:host:port[:sso]           — whole site; :sso = behind Authentik
#   FQDN/path:host:port[:sso]      — path only (e.g. /api no SSO, / with SSO)
# Examples:
#   sonarr.example.com:192.168.1.130:8989:sso
#   sonarr.example.com/api:192.168.1.130:8989
#   sonarr-api.example.com:192.168.1.130:8989

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Error: .env not found. Copy .env.example to .env and configure." >&2
  exit 1
fi

set -a
# shellcheck source=/dev/null
source "$ENV_FILE"
set +a

CONFIG_BASE="${CONFIG_ROOT:-./config}"
[[ "$CONFIG_BASE" != /* ]] && CONFIG_BASE="$SCRIPT_DIR/$CONFIG_BASE"
CADDYFILE_PATH="$CONFIG_BASE/caddy/Caddyfile"
CADDY_DIR="$(dirname "$CADDYFILE_PATH")"

if [[ ! -d "$CADDY_DIR" ]]; then
  echo "Error: Caddy config dir missing: $CADDY_DIR. Run bootstrap first." >&2
  exit 1
fi

tls_line=""
if [[ -n "${CADDY_USE_INTERNAL_TLS:-}" ]] && [[ "${CADDY_USE_INTERNAL_TLS}" =~ ^(true|1|yes)$ ]]; then
  tls_line="  tls internal"
fi

# Authentik forward auth: URI from provider (default Caddy auth endpoint). Backend is authentik-server.
AUTHENTIK_FA_URI="${AUTHENTIK_FORWARD_AUTH_URI:-/outpost.goauthentik.io/auth/caddy}"
AUTHENTIK_FA_BACKEND="${AUTHENTIK_FORWARD_AUTH_BACKEND:-http://authentik-server:9000}"

# Parse one entry into fqdn, path, upstream, sso. Sets global vars parse_*.
parse_entry() {
  local entry="$1"
  parse_fqdn=""
  parse_path=""
  parse_upstream=""
  parse_sso=0
  local rest="${entry#*:}"   # after first colon
  local fqdn_part="${entry%%:*}"
  if [[ "$rest" != *:* ]]; then
    return 1
  fi
  local last="${rest##*:}"
  if [[ "$last" == "sso" ]]; then
    parse_sso=1
    rest="${rest%:*}"
  fi
  if [[ "$rest" != *:* ]]; then
    return 1
  fi
  parse_upstream="${rest%%:*}:${rest#*:}"
  if [[ "$fqdn_part" == */* ]]; then
    parse_fqdn="${fqdn_part%%/*}"
    parse_path="/${fqdn_part#*/}"
  else
    parse_fqdn="$fqdn_part"
    parse_path=""
  fi
  return 0
}

# Emit Caddy route block for one FQDN: list of "path:upstream" or "path:upstream:sso".
# Path "" = default handle. Path-specific handles first (longest path), then default.
emit_extra_server_block() {
  local fqdn="$1"
  shift
  local path_upstream_sso=("$@")
  local has_sso=0
  for item in "${path_upstream_sso[@]}"; do
    if [[ "$item" == *:sso ]]; then
      has_sso=1
      break
    fi
  done

  echo ""
  echo "# $fqdn"
  echo "$fqdn {"
  echo "${tls_line}"
  echo "  encode zstd gzip"
  echo "  route {"
  if [[ "$has_sso" -eq 1 ]]; then
    echo "    reverse_proxy /outpost.goauthentik.io/* $AUTHENTIK_FA_BACKEND"
  fi
  # Parse each item: path:upstream or path:upstream:sso (upstream = host:port)
  local -a parsed=()
  for item in "${path_upstream_sso[@]}"; do
    local path="${item%%:*}"
    local rest="${item#*:}"
    local sso_flag=""
    if [[ "$rest" == *:sso ]]; then
      sso_flag="sso"
      rest="${rest%:*}"
    fi
    local upstream="$rest"
    local path_len="${#path}"
    parsed+=("${path_len}|${path}|${upstream}|${sso_flag}")
  done
  # Sort by path length descending so default (empty path) is last
  local -a sorted=()
  mapfile -t sorted < <(printf '%s\n' "${parsed[@]}" | sort -t'|' -k1 -rn)
  for line in "${sorted[@]}"; do
    [[ -z "$line" ]] && continue
    IFS='|' read -r path_len path upstream sso_flag <<< "$line"
    if [[ -n "$path" ]]; then
      echo "    handle ${path}* {"
    else
      echo "    handle {"
    fi
    if [[ "$sso_flag" == "sso" ]]; then
      echo "      forward_auth $AUTHENTIK_FA_BACKEND {"
      echo "        uri $AUTHENTIK_FA_URI"
      echo "        copy_headers X-Authentik-Username X-Authentik-Groups X-Authentik-Email X-Authentik-Name X-Authentik-Uid X-Authentik-Jwt X-Authentik-Meta-Jwks X-Authentik-Meta-Outpost X-Authentik-Meta-Provider X-Authentik-Meta-App X-Authentik-Meta-Version"
      echo "        trusted_proxies private_ranges"
      echo "      }"
    fi
    echo "      reverse_proxy $upstream"
    echo "    }"
  done
  echo "  }"
  echo "}"
}

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT

{
  echo "{"
  echo "  email admin@${PUBLIC_BASE_DOMAIN:-example.com}"
  echo "}"
  echo ""
  echo "# Authentik web UI"
  echo "${AUTHENTIK_FQDN:-auth.example.com} {"
  echo "${tls_line}"
  echo "  encode zstd gzip"
  echo "  reverse_proxy authentik-server:9000"
  echo "}"
  echo ""
  echo "# whoami debug endpoint"
  echo "${WHOAMI_FQDN:-whoami.example.com} {"
  echo "${tls_line}"
  echo "  encode zstd gzip"
  echo "  reverse_proxy whoami:80"
  echo "}"

  if [[ -n "${CADDY_EXTRA_SERVICES:-}" ]]; then
    echo ""
    echo "# Extra services (from CADDY_EXTRA_SERVICES)"
    # Parse all entries and group by FQDN. Each group: list of "path:upstream:sso_or_empty"
    declare -A fqdn_entries
    IFS=',' read -ra entries <<< "$CADDY_EXTRA_SERVICES"
    for entry in "${entries[@]}"; do
      entry="${entry#"${entry%%[![:space:]]*}"}"
      entry="${entry%"${entry##*[![:space:]]}"}"
      [[ -z "$entry" ]] && continue
      if ! parse_entry "$entry"; then
        echo "# Skipping malformed entry (need FQDN:host:port or FQDN/path:host:port [:sso]): $entry" >&2
        continue
      fi
      key="${parse_path}:${parse_upstream}"
      [[ "$parse_sso" -eq 1 ]] && key="${key}:sso"
      if [[ -z "${fqdn_entries[$parse_fqdn]+x}" ]]; then
        fqdn_entries[$parse_fqdn]="$key"
      else
        fqdn_entries[$parse_fqdn]="${fqdn_entries[$parse_fqdn]}"$'\n'"$key"
      fi
    done
    for fqdn in "${!fqdn_entries[@]}"; do
      items=()
      while IFS= read -r line; do
        [[ -n "$line" ]] && items+=("$line")
      done <<< "${fqdn_entries[$fqdn]}"
      emit_extra_server_block "$fqdn" "${items[@]}"
    done
  fi
} > "$tmp"

cp "$tmp" "$CADDYFILE_PATH"
echo "Wrote $CADDYFILE_PATH"
