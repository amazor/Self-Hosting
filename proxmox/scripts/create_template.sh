#!/bin/sh
set -e

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DEFAULT_VM_ID=9000
DEFAULT_VM_NAME="debian-13-docker-cloudinit"
DEFAULT_STORAGE="local-lvm"
DEFAULT_USER="mazora"

VM_ID="$DEFAULT_VM_ID"
VM_NAME="$DEFAULT_VM_NAME"
STORAGE="$DEFAULT_STORAGE"
USER_NAME="$DEFAULT_USER"

IMG_URL="https://cloud.debian.org/images/cloud/trixie/latest/debian-13-genericcloud-amd64.qcow2"
IMG_FILE="debian-13-temp.qcow2"
SNIPPET_NAME="cloud-init-config.yaml"
SNIPPET_DEST="/var/lib/vz/snippets/$SNIPPET_NAME"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SNIPPET_SRC="$SCRIPT_DIR/../snippets/$SNIPPET_NAME"

# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------
usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS] [VM_ID] [VM_NAME]

Create a Proxmox Cloud-Init template with Docker and homelab baseline.

Arguments:
  VM_ID               Template VM ID           (default: $DEFAULT_VM_ID)
  VM_NAME             Template name             (default: $DEFAULT_VM_NAME)

Options:
  -u, --user NAME     Cloud-Init username       (default: $DEFAULT_USER)
  -s, --storage NAME  Proxmox storage backend   (default: $DEFAULT_STORAGE)
  -h, --help          Show this help message

The script looks for the Cloud-Init vendor snippet in Proxmox's local
snippet storage (/var/lib/vz/snippets/) first, then falls back to the
repo-relative path (proxmox/snippets/). If --user is provided, the
installed snippet is updated with the custom username.

Examples:
  $(basename "$0")                              # all defaults
  $(basename "$0") 9001 my-template             # custom ID and name
  $(basename "$0") -u john -s local-zfs 9001    # custom user and storage
EOF
    exit 0
}

# ---------------------------------------------------------------------------
# Parse options
# ---------------------------------------------------------------------------
while [ $# -gt 0 ]; do
    case "$1" in
        -h|--help) usage ;;
        -u|--user) USER_NAME="$2"; shift 2 ;;
        -s|--storage) STORAGE="$2"; shift 2 ;;
        --) shift; break ;;
        -*) echo "Unknown option: $1" >&2; echo; usage ;;
        *) break ;;
    esac
done

[ $# -ge 1 ] && VM_ID="$1"
[ $# -ge 2 ] && VM_NAME="$2"

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
if ! echo "$USER_NAME" | grep -qE '^[a-zA-Z_][a-zA-Z0-9_-]{0,31}$'; then
    echo "Error: Username '$USER_NAME' is invalid (alphanumeric, _ and - only, max 32 chars)."
    exit 1
fi

if qm status "$VM_ID" > /dev/null 2>&1; then
    echo "Error: VM ID $VM_ID already exists. Choose a different ID."
    exit 1
fi

if ! pvesm status 2>/dev/null | grep -q "^${STORAGE} "; then
    echo "Error: Storage '${STORAGE}' not found or not configured."
    echo "List available storage with: pvesm status"
    echo "Use local-lvm for LVM (ext4/single-disk) installs, local-zfs for ZFS installs."
    exit 1
fi

if [ ! -f "$SNIPPET_SRC" ]; then
    echo "Error: Vendor snippet not found at $SNIPPET_SRC"
    echo "Run this script from the cloned repo."
    exit 1
fi

# ---------------------------------------------------------------------------
# 1. Install Cloud-Init vendor snippet
# ---------------------------------------------------------------------------
mkdir -p "$(dirname "$SNIPPET_DEST")"
if [ "$USER_NAME" = "$DEFAULT_USER" ]; then
    cp "$SNIPPET_SRC" "$SNIPPET_DEST"
else
    sed "s|$DEFAULT_USER|$USER_NAME|g" "$SNIPPET_SRC" > "$SNIPPET_DEST"
fi
echo "Installed vendor snippet → $SNIPPET_DEST (user: $USER_NAME)"

# ---------------------------------------------------------------------------
# 2. Download cloud image (cached if already present)
# ---------------------------------------------------------------------------
if [ ! -f "$IMG_FILE" ]; then
    echo "Downloading Debian Cloud Image..."
    wget -O "$IMG_FILE" "$IMG_URL"
fi

echo "Creating VM $VM_ID ($VM_NAME)..."

# ---------------------------------------------------------------------------
# 3. Create VM shell (no efidisk yet — OS disk must be disk-0 for clone ordering)
# ---------------------------------------------------------------------------
qm create "$VM_ID" --name "$VM_NAME" \
  --memory 2048 --cores 2 \
  --machine q35 --bios ovmf \
  --agent enabled=1,fstrim_cloned_disks=1 \
  --net0 virtio,bridge=vmbr0

# ---------------------------------------------------------------------------
# 4. Import OS disk (disk-0), then add EFI disk (disk-1)
#    Order matters — reversing this causes "missing disk-0" errors on clone.
# ---------------------------------------------------------------------------
qm importdisk "$VM_ID" "$IMG_FILE" "$STORAGE"
qm set "$VM_ID" --scsihw virtio-scsi-pci \
  --scsi0 "$STORAGE:vm-$VM_ID-disk-0,discard=on,ssd=1"
qm resize "$VM_ID" scsi0 32G
qm set "$VM_ID" --efidisk0 "$STORAGE:0,format=raw,pre-enrolled-keys=1"

# ---------------------------------------------------------------------------
# 5. Add Cloud-Init drive and serial console
# ---------------------------------------------------------------------------
qm set "$VM_ID" --scsi1 "$STORAGE:cloudinit"
qm set "$VM_ID" --boot c --bootdisk scsi0
qm set "$VM_ID" --serial0 socket --vga serial0

# ---------------------------------------------------------------------------
# 6. Attach the vendor snippet
# ---------------------------------------------------------------------------
qm set "$VM_ID" --cicustom "vendor=local:snippets/$SNIPPET_NAME"

# ---------------------------------------------------------------------------
# 6b. Install hook so clones auto-inject Proxmox node name into cloud-init
#     (pre-start hook runs when a clone starts; guest gets /etc/homelab/proxmox-node)
# ---------------------------------------------------------------------------
HOOK_NAME="inject-proxmox-node-hook.sh"
HOOK_SRC="$SCRIPT_DIR/../snippets/$HOOK_NAME"
HOOK_DEST="/var/lib/vz/snippets/$HOOK_NAME"
if [ -f "$HOOK_SRC" ]; then
    cp "$HOOK_SRC" "$HOOK_DEST"
    chmod +x "$HOOK_DEST"
    qm set "$VM_ID" --hookscript "local:snippets/$HOOK_NAME"
    echo "Installed hook: $HOOK_DEST (clones will auto-inject Proxmox node name)"
else
    echo "Warning: hook not found at $HOOK_SRC; clones will not auto-inject Proxmox node name"
fi

# ---------------------------------------------------------------------------
# 7. Cloud-Init user and network config
#    --ciuser keeps the user visible in the Proxmox GUI and enables SSH key
#    injection via the GUI. Produces a deprecation warning (Cloud-Init 22.2+,
#    removal planned for 27.2); Proxmox is expected to update before then.
# ---------------------------------------------------------------------------
qm set "$VM_ID" --ciuser "$USER_NAME" --ipconfig0 ip=dhcp,ip6=dhcp

# TODO: Uncomment once template is finalized to reclaim disk space
# rm -f "$IMG_FILE"

echo ""
echo "VM $VM_ID created. One-time setup before converting to template:"
echo "  1. Add your SSH public key in Proxmox GUI → Cloud-Init tab"
echo "  2. Convert to Template in the Proxmox GUI"
echo ""
echo "Clones from this template will auto-inject the Proxmox node name on first start"
echo "(pre-start hook). Guest gets /etc/homelab/proxmox-node for monitoring/Alloy."