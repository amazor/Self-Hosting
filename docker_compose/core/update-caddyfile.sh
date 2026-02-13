#!/usr/bin/env bash
# Regenerate Caddyfile from .env and reload Caddy.
#
# Bootstrap (and deploy) already run gen-caddyfile.sh and reload Caddy, so
# re-running ./deploy.sh core or ./bootstrap.sh applies CADDY_EXTRA_SERVICES.
# This script is for when you only want to regenerate + reload without
# running the full bootstrap (e.g. after editing .env by hand).
#
# Run from docker_compose/core:
#   ./update-caddyfile.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CADDY_CONTAINER="${CADDY_CONTAINER:-caddy}"
NO_RELOAD=0

usage() {
  cat <<EOF
Usage: ./update-caddyfile.sh [--no-reload]

Regenerates Caddyfile from .env (CADDY_EXTRA_SERVICES) and reloads Caddy.

Normally you can just re-run bootstrap or deploy to apply .env changes:
  ./deploy.sh core   # or: ./bootstrap.sh

Flags:
  --no-reload   Only write the Caddyfile; do not run 'caddy reload'.
  --help        Show this help.
EOF
}

for arg in "$@"; do
  case "$arg" in
    --no-reload) NO_RELOAD=1 ;;
    --help|-h)   usage; exit 0 ;;
    *)           echo "Unknown option: $arg" >&2; usage >&2; exit 1 ;;
  esac
done

"$SCRIPT_DIR/gen-caddyfile.sh"

if [[ "$NO_RELOAD" -eq 1 ]]; then
  echo "Skipping reload (--no-reload). Run: docker exec $CADDY_CONTAINER caddy reload --config /etc/caddy-config/Caddyfile"
  exit 0
fi

if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$CADDY_CONTAINER"; then
  docker exec "$CADDY_CONTAINER" caddy reload --config /etc/caddy-config/Caddyfile
  echo "Caddy reloaded."
else
  echo "Caddy container '$CADDY_CONTAINER' not running. Start the stack to apply config."
fi
