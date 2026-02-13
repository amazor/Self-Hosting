#!/bin/sh

# Configuration (Change these or pass as arguments)
VM_ID=${1:-9000}
VM_NAME=${2:-debian-13-docker-cloudinit}
STORAGE=${3:-local-lvm}
USER_NAME="mazora"
IMG_URL="https://cloud.debian.org/images/cloud/trixie/latest/debian-13-genericcloud-amd64.qcow2"
IMG_FILE="debian-13-temp.qcow2"

# Check if VM ID already exists
if qm status $VM_ID > /dev/null 2>&1; then
    echo "Error: VM ID $VM_ID already exists. Choose a different ID."
    exit 1
fi

# Check that the chosen storage exists (avoids "no such logical volume" on clone)
if ! pvesm status 2>/dev/null | grep -q "^${STORAGE} "; then
    echo "Error: Storage '${STORAGE}' not found or not configured."
    echo "List available storage with: pvesm status"
    echo "Use local-lvm for LVM (ext4/single-disk) installs, local-zfs for ZFS installs."
    exit 1
fi

# Download image if not present
if [ ! -f "$IMG_FILE" ]; then
    echo "Downloading Debian Cloud Image..."
    wget -O $IMG_FILE $IMG_URL
fi

echo "Creating VM $VM_ID ($VM_NAME)..."

# 1. Create the VM Shell (no efidisk yet — create after import so OS disk gets disk-0)
qm create $VM_ID --name $VM_NAME \
  --memory 2048 --cores 2 \
  --machine q35 --bios ovmf \
  --agent enabled=1,fstrim_cloned_disks=1 \
  --net0 virtio,bridge=vmbr0

# 2. Import and attach the main disk first (gets vm-ID-disk-0; becomes base on template convert)
qm importdisk $VM_ID $IMG_FILE $STORAGE
qm set $VM_ID --scsihw virtio-scsi-pci \
  --scsi0 $STORAGE:vm-$VM_ID-disk-0,discard=on,ssd=1
qm resize $VM_ID scsi0 32G

# 3. Add EFI disk after main disk (gets vm-ID-disk-1 so clone does not expect missing disk-0)
qm set $VM_ID --efidisk0 $STORAGE:0,format=raw,pre-enrolled-keys=1

# 4. Add Cloud-Init drive and Serial Console
qm set $VM_ID --scsi1 $STORAGE:cloudinit
qm set $VM_ID --boot c --bootdisk scsi0
qm set $VM_ID --serial0 socket --vga serial0

# 5. Attach the Custom Vendor Snippet
qm set $VM_ID --cicustom "vendor=local:snippets/common-config.yaml"

# 6. Basic Cloud-Init Network Config
qm set $VM_ID --ciuser $USER_NAME --ipconfig0 ip=dhcp,ip6=dhcp

echo "VM $VM_ID Created. Now, add your SSH key in the Proxmox GUI and 'Convert to Template'."
