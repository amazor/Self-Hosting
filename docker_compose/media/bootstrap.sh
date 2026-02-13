#!/usr/bin/env bash
# Media VM — first-run bootstrap (opinionated, idempotent)
# Invoked by deploy.sh or run manually: cd docker_compose/media && sudo ./bootstrap.sh
# See docs/Chapter2c-media.md.
#
# Phases: 0 safety + args, 1 NFS (optional, interactive only), 2 .env check + config dirs + guardrails.
# Does NOT install packages (cloud-init provides jq, nfs-common, etc.), create .env, or start the stack.
# Deploy owns symlinks, stack-functions, and "up -d". Use --non-interactive when invoked by deploy.

set -e

# --- Phase 0: safety + assumptions ---
# Re-exec as root if not root (for fstab, chown, etc.)
if [[ $EUID -ne 0 ]]; then
  # Preserve deploy-driven non-interactive intent across sudo re-exec.
  # sudo often strips custom env vars unless configured to keep them.
  if [[ -n "${HOMELAB_DEPLOY:-}" ]]; then
    exec sudo "$0" --non-interactive "$@"
  fi
  exec sudo "$0" "$@"
fi

FORCE=0
NONINTERACTIVE=0
for arg in "$@"; do
  [[ "$arg" = "--force" ]] && FORCE=1
  [[ "$arg" = "--non-interactive" ]] && NONINTERACTIVE=1
done
[[ -n "${HOMELAB_DEPLOY:-}" ]] && NONINTERACTIVE=1

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VM_ROLE="media"
DEFAULT_MOUNT_PATH="/mnt/media"
COMPOSE_BASE="$SCRIPT_DIR/compose.yml"

# VPN guardrail: base stack must route torrent traffic via VPN (no direct egress).
# Check: compose.yml has "vpn" service and qbittorrent uses network_mode: service:vpn.
_vpn_guardrail_check() {
  [[ -f "$COMPOSE_BASE" ]] || return 0
  if ! grep -qE '^[[:space:]]*vpn:' "$COMPOSE_BASE" 2>/dev/null; then
    echo -e "\033[31mWARNING: compose.yml has no 'vpn' service. Torrent traffic must not have direct egress.\033[0m"
    return 1
  fi
  if ! grep -A25 '^[[:space:]]*qbittorrent:' "$COMPOSE_BASE" 2>/dev/null | grep -qE 'network_mode:[[:space:]]*service:vpn'; then
    echo -e "\033[31mWARNING: qbittorrent does not use network_mode: service:vpn. Torrent traffic must route through VPN.\033[0m"
    return 1
  fi
  return 0
}

# OS: Debian/Ubuntu-ish
if [[ ! -f /etc/os-release ]]; then
  echo "Cannot detect OS (/etc/os-release missing). Expect Debian or Ubuntu."
  exit 1
fi
# shellcheck source=/dev/null
source /etc/os-release
if [[ "${ID:-}" != "debian" ]] && [[ "${ID:-}" != "ubuntu" ]] && [[ "${ID_LIKE:-}" != *"debian"* ]]; then
  echo "This bootstrap targets Debian/Ubuntu. Found: ID=${ID:-} ID_LIKE=${ID_LIKE:-}"
  exit 1
fi

# Target user for .bashrc and UX file (user who ran sudo, or root)
REAL_USER="${SUDO_USER:-root}"
REAL_HOME="$(getent passwd "$REAL_USER" | cut -d: -f6)"
[[ -z "$REAL_HOME" ]] && REAL_HOME="/root"

echo "--- Media VM bootstrap ---"
echo "This script will (idempotent, safe to re-run):"
echo "  1. Optionally add an NFS mount to /etc/fstab (interactive only; skipped with --non-interactive)"
echo "  2. Require .env to exist; create config dirs under CONFIG_ROOT (base + enabled overlays)"
echo "  3. Run guardrails (e.g. VPN check; use --force to skip overridable checks)"
echo ""

# --- Phase 1: NFS mount (optional, interactive only) ---
# Media VM: one mount for the whole media tree. Skipped when deploy invokes with --non-interactive.
# Fstab options: nofail, _netdev, x-systemd.automount, timeout so boot doesn't hang if NAS is down.
WANT_NFS="n"
if [[ $NONINTERACTIVE -eq 0 ]] && [[ -t 0 ]]; then
  read -r -p "Configure NFS mount for media? [y/N] " WANT_NFS
  WANT_NFS="${WANT_NFS:-n}"
fi

if [[ "$WANT_NFS" =~ ^[yY] ]]; then
  NAS_HOST=""
  EXPORT_PATH=""
  MOUNT_PATH="$DEFAULT_MOUNT_PATH"
  NFS_RO="rw"

  read -r -p "NAS hostname or IP: " NAS_HOST
  read -r -p "Export path on NAS (e.g. /volume1/media): " EXPORT_PATH
  read -r -p "Local mount path [$DEFAULT_MOUNT_PATH]: " MOUNT_PATH
  MOUNT_PATH="${MOUNT_PATH:-$DEFAULT_MOUNT_PATH}"
  read -r -p "Mount read-only? [y/N] " NFS_RO_INPUT
  if [[ "${NFS_RO_INPUT:-n}" =~ ^[yY] ]]; then
    NFS_RO="ro"
  fi

  if [[ -n "$NAS_HOST" ]] && [[ -n "$EXPORT_PATH" ]]; then
    FSTAB_SPEC="$NAS_HOST:$EXPORT_PATH"
    FSTAB_LINE="${FSTAB_SPEC}\t${MOUNT_PATH}\tnfs\tnofail,_netdev,x-systemd.automount,timeout=10,vers=4,${NFS_RO}\t0\t0"
    if grep -qF "$MOUNT_PATH" /etc/fstab 2>/dev/null; then
      echo "Mount for $MOUNT_PATH already in /etc/fstab; skipping."
    else
      echo "Adding to /etc/fstab: $FSTAB_SPEC -> $MOUNT_PATH (nofail, automount)"
      printf "%b\n" "$FSTAB_LINE" >> /etc/fstab
    fi
    mkdir -p "$MOUNT_PATH"
    chown "$REAL_USER:" "$MOUNT_PATH" 2>/dev/null || true
    systemctl daemon-reload
    if ! mount -a 2>/dev/null; then
      echo "Warning: mount -a failed (e.g. NAS unreachable). Boot will not hang (nofail); fix and run: mount $MOUNT_PATH"
    else
      echo "Mounted $MOUNT_PATH"
    fi
  else
    echo "Skipping NFS (missing NAS host or export path)."
  fi
fi

# --- Phase 2: .env required, config dirs, guardrails ---
cd "$SCRIPT_DIR"

if [[ ! -f .env ]]; then
  echo ""
  echo "No .env found. Create .env from .env.example in this directory ($SCRIPT_DIR), set required vars and ENABLE_* as needed, then re-run bootstrap or deploy."
  exit 1
fi

# Load .env for validation and config dirs
set -a
# shellcheck source=/dev/null
[[ -f .env ]] && source ./.env
set +a

# Validate required vars (without echoing secrets)
if [[ -f .env ]]; then
  if [[ -z "${OPENVPN_USER:-}" ]] || [[ -z "${OPENVPN_PASSWORD:-}" ]]; then
    echo "Set OPENVPN_USER and OPENVPN_PASSWORD in .env (required for VPN). Then run: ./bootstrap.sh"
    exit 1
  fi
  if [[ -n "${MEDIA_ROOT:-}" ]] && [[ -d "$MEDIA_ROOT" ]]; then
    if ! mountpoint -q "$MEDIA_ROOT" 2>/dev/null; then
      echo "Note: $MEDIA_ROOT is not a mount point. If you use NFS, ensure it is mounted before starting the stack."
    fi
  fi
fi

# VPN guardrail: torrent must not have direct egress (skip with --force)
if [[ "$FORCE" -ne 1 ]] && ! _vpn_guardrail_check; then
  echo -e "\033[31mBootstrap expects a dedicated VPN service and qbittorrent using network_mode: service:vpn.\033[0m"
  if [[ $NONINTERACTIVE -eq 1 ]]; then
    echo "Re-run with --force to skip this check, or fix compose.yml."
    exit 1
  fi
  read -r -p "Type 'yes' to continue anyway (not recommended): " confirm
  if [[ "$confirm" != "yes" ]]; then
    echo "Exiting. Fix compose.yml or re-run with --force to skip this check."
    exit 1
  fi
fi

# Create config directories for enabled apps (CONFIG_ROOT from .env; resolve to absolute path)
CONFIG_BASE="${CONFIG_ROOT:-./config}"
[[ "$CONFIG_BASE" != /* ]] && CONFIG_BASE="$SCRIPT_DIR/$CONFIG_BASE"
mkdir -p "$CONFIG_BASE"
# Base stack (always present in compose.yml)
for dir in qbittorrent sonarr radarr prowlarr flaresolverr; do
  mkdir -p "$CONFIG_BASE/$dir"
done
# Optional overlays
[[ "${ENABLE_BUILDARR_RECYCLARR:-0}" = "1" ]] && for dir in buildarr recyclarr; do mkdir -p "$CONFIG_BASE/$dir"; done
[[ "${ENABLE_CLEANUPARR:-0}" = "1" ]]         && mkdir -p "$CONFIG_BASE/cleanuparr"
[[ "${ENABLE_SABNZBD:-0}" = "1" ]]            && mkdir -p "$CONFIG_BASE/sabnzbd"
[[ "${ENABLE_BAZARR:-0}" = "1" ]]             && mkdir -p "$CONFIG_BASE/bazarr"
[[ "${ENABLE_NTFY:-0}" = "1" ]]               && mkdir -p "$CONFIG_BASE/ntfy/cache"
chown -R "$REAL_USER:" "$CONFIG_BASE" 2>/dev/null || true
echo "Config directories created under $CONFIG_BASE (base + enabled overlays)."

echo ""
echo "--- Stack summary ---"
echo "Base: VPN (Gluetun), qBittorrent, Sonarr, Radarr, Prowlarr, FlareSolverr"
echo "Buildarr + Recyclarr: $([[ "${ENABLE_BUILDARR_RECYCLARR:-0}" = "1" ]] && echo "on" || echo "off")"
echo "Cleanuparr:            $([[ "${ENABLE_CLEANUPARR:-0}" = "1" ]] && echo "on" || echo "off")"
echo "SABnzbd:               $([[ "${ENABLE_SABNZBD:-0}" = "1" ]] && echo "on" || echo "off")"
echo "Bazarr:                $([[ "${ENABLE_BAZARR:-0}" = "1" ]] && echo "on" || echo "off")"
echo "ntfy:                  $([[ "${ENABLE_NTFY:-0}" = "1" ]] && echo "on" || echo "off")"
echo ""
echo "Bootstrap done. Deploy will start the stack and set up shell helpers (media, stack)."