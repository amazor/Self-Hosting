#!/bin/sh
# Proxmox VM hookscript: inject Proxmox node name into cloud-init before guest starts.
# Clones from the template inherit this hook; on pre-start we write the node name
# into a user snippet and regenerate cloud-init so the guest gets /etc/homelab/proxmox-node.
#
# Install: copy to /var/lib/vz/snippets/ and attach to the template:
#   qm set 9000 --hookscript local:snippets/inject-proxmox-node-hook.sh
# Clones then get the hook automatically; no manual step needed.

VMID="$1"
PHASE="$2"

# Only act before start; skip template (default 9000)
TEMPLATE_VMID="${TEMPLATE_VMID:-9000}"
SNIPPETS_DIR="${SNIPPETS_DIR:-/var/lib/vz/snippets}"
VENDOR_SNIPPET="${VENDOR_SNIPPET:-cloud-init-config.yaml}"

[ "$PHASE" = "pre-start" ] || exit 0
[ -n "$VMID" ] || exit 0
[ "$VMID" != "$TEMPLATE_VMID" ] || exit 0

# Avoid re-injecting if already have our user snippet (idempotent: overwrite is fine)
NODE_NAME="$(hostname)"
# YAML: strip control chars and collapse newlines; if empty, use a safe default
NODE_NAME="$(printf '%s' "$NODE_NAME" | tr -d '\n\r' | sed 's/ *$//;s/^ *//')"
[ -n "$NODE_NAME" ] || NODE_NAME="pve"

USER_SNIPPET_NAME="vm-${VMID}-proxmox-node.yaml"
USER_SNIPPET_PATH="${SNIPPETS_DIR}/${USER_SNIPPET_NAME}"

mkdir -p "$SNIPPETS_DIR"
# Use a here-doc that escapes any " in NODE_NAME so YAML stays valid
case "$NODE_NAME" in
  *'"'*) NODE_ESCAPED="$(printf '%s' "$NODE_NAME" | sed 's/"/\\"/g')" ;;
  *)    NODE_ESCAPED="$NODE_NAME" ;;
esac
cat > "$USER_SNIPPET_PATH" <<EOF
#cloud-config
# Injected by inject-proxmox-node-hook.sh — Proxmox node name for this VM.
write_files:
  - path: /etc/homelab/proxmox-node
    content: "$NODE_ESCAPED"
    permissions: '0644'
EOF

qm set "$VMID" --cicustom "user=local:snippets/${USER_SNIPPET_NAME},vendor=local:snippets/${VENDOR_SNIPPET}"
qm cloudinit update "$VMID"

exit 0
