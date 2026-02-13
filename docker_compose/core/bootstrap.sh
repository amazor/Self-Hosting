#!/usr/bin/env bash
# Core VM bootstrap (idempotent).
#
# Owns:
#   - creating/validating local .env for this stack
#   - creating core config directories and starter files
#   - validating compose syntax
#   - optional local bring-up with --up
#
# Does NOT own:
#   - symlinks, shell helper functions, or cross-stack orchestration (deploy.sh)
#
# Usage examples:
#   cd docker_compose/core && ./bootstrap.sh
#   cd docker_compose/core && ./bootstrap.sh --up
#   cd docker_compose/core && ./bootstrap.sh --force

set -e

# ---------------------------
# constants and defaults
# ---------------------------
# SCRIPT_DIR is the directory containing this script (stack dir). Used so paths work the same
# whether you run ./bootstrap.sh by hand or via deploy.sh; deploy also runs compose from this dir.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
COMPOSE_FILE="$SCRIPT_DIR/compose.yml"
ENV_FILE="$SCRIPT_DIR/.env"
ENV_EXAMPLE="$SCRIPT_DIR/.env.example"

FORCE=0
BRING_UP=0

# ---------------------------
# usage and argument parsing
# ---------------------------
usage() {
  cat <<EOF
Usage:
  ./bootstrap.sh [flags]

Flags:
  --up         Start stack after bootstrap checks complete.
  --force      Skip placeholder guardrails for local testing.
  --help, -h   Show this help text.

Authentik: Set AUTHENTIK_BOOTSTRAP_EMAIL and AUTHENTIK_BOOTSTRAP_PASSWORD in .env before
  first start to create the default akadmin user without visiting the initial-setup UI.

Common errors after 'docker compose up':
  - Caddy: 'Caddyfile: no such file or directory' → Run this bootstrap from the
    same directory where you run docker compose: cd docker_compose/core && ./bootstrap.sh
  - Authentik: 'Permission denied: /media/public' → Run once:
    sudo chown -R 1000:1000 <CONFIG_ROOT>/authentik/media
  - dnsmasq: 'failed to read configuration file' → Same as Caddy; run bootstrap from stack dir.
EOF
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --up)
        BRING_UP=1
        shift
        ;;
      --force)
        FORCE=1
        shift
        ;;
      --help|-h)
        usage
        exit 0
        ;;
      *)
        echo "Unknown option: $1" >&2
        usage >&2
        exit 1
        ;;
    esac
  done
}

# ---------------------------
# helper functions
# ---------------------------
_is_placeholder() {
  local val="$1"
  [[ -z "$val" ]] && return 0
  [[ "$val" == CHANGE_ME* ]] && return 0
  [[ "$val" == "example.com" ]] && return 0
  return 1
}

require_prereqs() {
  if ! command -v docker >/dev/null 2>&1; then
    echo "Docker is not installed. Install Docker Engine first." >&2
    exit 1
  fi
  if ! docker compose version >/dev/null 2>&1; then
    echo "docker compose plugin not found. Install Docker Compose v2 plugin." >&2
    exit 1
  fi
  if ! docker info >/dev/null 2>&1; then
    echo "Cannot connect to the Docker daemon (permission denied on socket)." >&2
    echo "Add your user to the docker group and start a new login session:" >&2
    echo "  sudo usermod -aG docker \$USER" >&2
    echo "  # then log out and back in, or run: newgrp docker" >&2
    exit 1
  fi
}

prepare_env_file() {
  if [[ -f "$ENV_FILE" ]]; then
    return 0
  fi
  if [[ ! -f "$ENV_EXAMPLE" ]]; then
    echo "Missing $ENV_EXAMPLE; cannot initialize .env." >&2
    exit 1
  fi

  cp "$ENV_EXAMPLE" "$ENV_FILE"
  echo "Created .env from .env.example."
  echo "Fill real values in $ENV_FILE, then re-run bootstrap."
  exit 1
}

load_env() {
  set -a
  # shellcheck source=/dev/null
  source "$ENV_FILE"
  set +a
}

validate_guardrails() {
  [[ "$FORCE" -eq 1 ]] && return 0

  if _is_placeholder "${AUTHENTIK_SECRET_KEY:-}"; then
    echo "AUTHENTIK_SECRET_KEY is missing/placeholder in .env." >&2
    echo "Generate one with: openssl rand -base64 60" >&2
    exit 1
  fi
  if _is_placeholder "${AUTHENTIK_POSTGRES_PASSWORD:-}"; then
    echo "AUTHENTIK_POSTGRES_PASSWORD is missing/placeholder in .env." >&2
    exit 1
  fi
  if _is_placeholder "${PUBLIC_BASE_DOMAIN:-}"; then
    echo "PUBLIC_BASE_DOMAIN still looks like an example value." >&2
    echo "Set your real domain, or use --force only for local testing." >&2
    exit 1
  fi
}

ensure_config_directories() {
  CONFIG_BASE="${CONFIG_ROOT:-./config}"
  [[ "$CONFIG_BASE" != /* ]] && CONFIG_BASE="$SCRIPT_DIR/$CONFIG_BASE"

  mkdir -p \
    "$CONFIG_BASE/caddy" \
    "$CONFIG_BASE/caddy/data" \
    "$CONFIG_BASE/caddy/config" \
    "$CONFIG_BASE/caddy/site" \
    "$CONFIG_BASE/authentik/media" \
    "$CONFIG_BASE/authentik/media/public" \
    "$CONFIG_BASE/authentik/custom-templates" \
    "$CONFIG_BASE/authentik/postgresql" \
    "$CONFIG_BASE/authentik/redis" \
    "$CONFIG_BASE/dnsmasq"

  echo "Ensured config directories under: $CONFIG_BASE"
}

# If Docker (or a previous root run) created config dirs as root, we can't write Caddyfile/dnsmasq.conf.
# Fix ownership so the current user can write. Uses sudo only when needed.
ensure_config_writable() {
  local testfile="$CONFIG_BASE/caddy/.bootstrap_write_test"
  if touch "$testfile" 2>/dev/null; then
    rm -f "$testfile"
    return 0
  fi
  local uid gid
  uid="$(id -u)"
  gid="$(id -g)"
  if sudo -n chown -R "${uid}:${gid}" "$CONFIG_BASE" 2>/dev/null; then
    echo "Set config ownership to current user (via sudo) so bootstrap can write Caddyfile and dnsmasq.conf."
  else
    echo "Error: Cannot write to $CONFIG_BASE (likely root-owned from Docker). Run once:" >&2
    echo "  sudo chown -R \$(id -u):\$(id -g) $CONFIG_BASE" >&2
    exit 1
  fi
}

# Authentik runs as UID 1000; migration expects /media/public. Ensure host dirs are writable by that user.
# See: https://docs.goauthentik.io/troubleshooting/image_upload
ensure_authentik_media_permissions() {
  local media_dir="$CONFIG_BASE/authentik/media"
  local uid="${AUTHENTIK_UID:-1000}"
  local gid="${AUTHENTIK_GID:-1000}"

  if chown -R "${uid}:${gid}" "$media_dir" 2>/dev/null; then
    echo "Set authentik media ownership to ${uid}:${gid}."
  elif sudo -n chown -R "${uid}:${gid}" "$media_dir" 2>/dev/null; then
    echo "Set authentik media ownership to ${uid}:${gid} (via sudo)."
  else
    echo "Note: Could not chown $media_dir to ${uid}:${gid}." >&2
    echo "      If Authentik fails with 'Permission denied: /media/public', run once:" >&2
    echo "      sudo chown -R ${uid}:${gid} $media_dir" >&2
  fi
  chmod -R ug+rwX "$media_dir" 2>/dev/null || sudo -n chmod -R ug+rwX "$media_dir" 2>/dev/null || true
}

# Generate Caddyfile from .env (auth, whoami, plus CADDY_EXTRA_SERVICES). Run every time so .env changes are applied.
generate_caddyfile() {
  "$SCRIPT_DIR/gen-caddyfile.sh"
}

# Fail fast if Caddyfile is missing or a directory (e.g. Docker created the mount as a dir).
validate_caddyfile_ready() {
  local path="${CONFIG_BASE:-$SCRIPT_DIR/config}/caddy/Caddyfile"
  if [[ -d "$path" ]]; then
    echo "Error: $path exists as a directory (often from a previous bind-mount before bootstrap)." >&2
    echo "Remove it and re-run bootstrap: rm -rf '$path'" >&2
    exit 1
  fi
  if [[ ! -f "$path" ]]; then
    echo "Error: Caddyfile missing: $path" >&2
    echo "Re-run bootstrap from docker_compose/core (on the same host where you run docker compose)." >&2
    exit 1
  fi
}

ensure_starter_dnsmasq_conf() {
  local old_ifs
  local pair
  local host
  local ip

  DNSMASQ_CONF_PATH="$CONFIG_BASE/dnsmasq/dnsmasq.conf"
  if [[ -f "$DNSMASQ_CONF_PATH" ]]; then
    echo "dnsmasq.conf already exists; not overwriting."
    return 0
  fi
  # If path exists as a directory (e.g. Docker created it on a previous failed mount), remove it
  # so we can create the file. Otherwise bind-mount fails with "directory onto a file".
  if [[ -d "$DNSMASQ_CONF_PATH" ]]; then
    rmdir "$DNSMASQ_CONF_PATH" 2>/dev/null || rm -rf "$DNSMASQ_CONF_PATH"
  fi

  {
    echo "# dnsmasq starter config (generated by bootstrap.sh)"
    echo "# Keep this file small and explicit for predictable operations."
    echo "domain-needed"
    echo "bogus-priv"
    echo "no-resolv"
    echo "server=${DNS_UPSTREAM_1:-1.1.1.1}"
    echo "server=${DNS_UPSTREAM_2:-1.0.0.1}"
    echo "local=/${DNS_LOCAL_DOMAIN:-lab.arpa}/"
    echo "expand-hosts"
    echo "domain=${DNS_LOCAL_DOMAIN:-lab.arpa}"
    echo "cache-size=1000"
    echo ""
    echo "# Optional static records:"
    echo "# address=/apps.${DNS_LOCAL_DOMAIN:-lab.arpa}/192.168.1.120"
    echo "# address=/media.${DNS_LOCAL_DOMAIN:-lab.arpa}/192.168.1.130"
    if [[ -n "${DNS_LOCAL_RECORDS:-}" ]]; then
      old_ifs="$IFS"
      IFS=','
      for pair in $DNS_LOCAL_RECORDS; do
        host="${pair%%:*}"
        ip="${pair##*:}"
        if [[ -n "$host" ]] && [[ -n "$ip" ]] && [[ "$host" != "$ip" ]]; then
          echo "address=/${host}.${DNS_LOCAL_DOMAIN:-lab.arpa}/${ip}"
        fi
      done
      IFS="$old_ifs"
    fi
  } > "$DNSMASQ_CONF_PATH"

  echo "Created starter dnsmasq config: $DNSMASQ_CONF_PATH"
}

# Fail fast if dnsmasq.conf is missing or a directory (e.g. Docker created it on a failed mount).
validate_dnsmasq_conf_ready() {
  local path="${CONFIG_BASE:-$SCRIPT_DIR/config}/dnsmasq/dnsmasq.conf"
  if [[ -d "$path" ]]; then
    echo "Error: $path exists as a directory (often from a previous failed bind-mount)." >&2
    echo "Remove it and re-run bootstrap: rm -rf '$path'" >&2
    exit 1
  fi
  if [[ ! -f "$path" ]]; then
    echo "Error: dnsmasq config file missing: $path" >&2
    echo "Re-run bootstrap from docker_compose/core so ensure_starter_dnsmasq_conf can create it." >&2
    exit 1
  fi
}

validate_compose() {
  if ! docker compose -f "$COMPOSE_FILE" config >/dev/null; then
    echo "docker compose config validation failed." >&2
    exit 1
  fi
  echo "Compose file validates successfully."
}

maybe_bring_up_stack() {
  if [[ "$BRING_UP" -eq 1 ]]; then
    echo "Starting core stack..."
    docker compose -f "$COMPOSE_FILE" up -d
    echo "Core stack started."
    if [[ -n "${AUTHENTIK_BOOTSTRAP_EMAIL:-}" ]] && [[ -n "${AUTHENTIK_BOOTSTRAP_PASSWORD:-}" ]]; then
      echo "Authentik: AUTHENTIK_BOOTSTRAP_* set — initial akadmin user will be created on first start (no UI setup needed)."
    fi
  else
    echo "Bootstrap complete. Run 'docker compose up -d' when ready."
  fi
  echo ""
  echo "Config is under: $CONFIG_BASE (relative to this directory: $SCRIPT_DIR)"
  echo "Caddyfile is generated from .env (set CADDY_EXTRA_SERVICES for more services). Re-run bootstrap or deploy to apply .env changes."
  echo "If Caddy reports 'Caddyfile: no such file or directory' or dnsmasq fails to read config,"
  echo "run this bootstrap from the same directory where you run docker compose:"
  echo "  cd $SCRIPT_DIR && ./bootstrap.sh"
}

# Reload Caddy so it picks up the generated Caddyfile (no container restart).
reload_caddy_if_running() {
  local caddy_container="${CADDY_CONTAINER:-caddy}"
  if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$caddy_container"; then
    docker exec "$caddy_container" caddy reload --config /etc/caddy-config/Caddyfile
    echo "Caddy reloaded."
  fi
}


# ---------------------------
# main
# ---------------------------
cd "$SCRIPT_DIR"
echo "--- Core VM bootstrap ---"

parse_args "$@"
require_prereqs
prepare_env_file
load_env
validate_guardrails
ensure_config_directories
ensure_config_writable
ensure_authentik_media_permissions
generate_caddyfile
validate_caddyfile_ready
ensure_starter_dnsmasq_conf
validate_dnsmasq_conf_ready
validate_compose
maybe_bring_up_stack
reload_caddy_if_running
