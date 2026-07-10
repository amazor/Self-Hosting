#!/bin/bash
# misioslav/expressvpn's start.sh supervision loop hardcodes a check for
# /sys/class/net/tun0 to decide whether the tunnel is up:
#   if ! check_connected || [[ ! -d /sys/class/net/tun0 ]]; then
# Lightway/WireGuard protocols (our default, see PROTOCOL in compose.yml)
# create wgexpressvpn0 instead of tun0, so that check never passes and the
# loop logs a false "VPN down (missing tun0 or not connected)" reconnect
# every CONNECTION_CHECK_INTERVAL (default 30s) forever, even while
# genuinely connected. check_connected() (expressvpnctl get connectionstate)
# is already the authoritative check -- it's what the image's own Docker
# HEALTHCHECK relies on -- so drop the redundant interface check.
set -euo pipefail

pattern='! check_connected \|\| \[\[ ! -d /sys/class/net/tun0 \]\]'
if grep -qE "$pattern" /expressvpn/start.sh; then
    sed -i -E \
        -e "s#${pattern}#! check_connected#" \
        -e 's/VPN down \(missing tun0 or not connected\)/VPN down (not connected)/' \
        /expressvpn/start.sh
else
    echo "[vpn-entrypoint] WARNING: tun0 patch target not found in /expressvpn/start.sh (image start.sh may have changed) -- running unpatched, tun0 log spam may return" >&2
fi

exec /bin/bash /expressvpn/start.sh "$@"
