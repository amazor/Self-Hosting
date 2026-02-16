#!/usr/bin/env bash
# Restart ddclient after editing ddclient.conf.
# Run from the core stack directory (docker_compose/core or ~/core).
# Only works when ENABLE_DDNS=1 in .env; otherwise exits with a short message.
#
# Usage: ./restart-ddclient.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [[ ! -f ".env" ]]; then
  echo "No .env in $(pwd). Run from the core stack directory." >&2
  exit 1
fi

# shellcheck source=/dev/null
source .env 2>/dev/null || true

if [[ "${ENABLE_DDNS:-0}" != "1" ]]; then
  echo "ENABLE_DDNS is not 1. Set ENABLE_DDNS=1 in .env to use DDNS, then re-run." >&2
  exit 1
fi

COMPOSE_FILES="-f compose.yml -f compose.ddclient.yml"
docker compose $COMPOSE_FILES restart ddclient
echo "ddclient restarted; it will re-read config/ddclient/ddclient.conf."
