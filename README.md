🏗️ Homelab Setup – Proxmox Foundation

This document describes the initial setup of my homelab, focusing on installing Proxmox VE and creating a reusable Cloud-Init VM template for Docker-based workloads.
This is the foundation upon which all future infrastructure (Docker stacks, Plex, monitoring, etc.) will be built.

🖥️ Hardware Overview

Host machine: Beelink EQi13 Mini PC
CPU: Intel Core i5-13500
RAM: 32 GB
Storage: 500 GB NVMe SSD
Form factor: Low-power, always-on mini PC

Why this hardware?

- Excellent performance per watt → suitable for 24/7 operation
- Strong single-core and multi-core performance → great for Plex, Docker, and VMs
- 32 GB RAM → allows multiple VMs without memory pressure
- NVMe storage → fast VM disks and snapshots
- Compact & quiet → ideal for running at a family home

Step 0 – Boot Media Preparation (Ventoy)

Before installing Proxmox, I prepared a flexible boot USB using Ventoy.

Why Ventoy?
- One USB can boot multiple ISOs
- No reflashing needed when switching installers
- Perfect for homelab experimentation

Steps

1. Download Ventoy from https://www.ventoy.net

2. Install Ventoy onto a USB drive
3. Copy the Proxmox VE ISO onto the USB (no flashing needed)

This USB will remain useful for:
Proxmox installs
Rescue ISOs

Live Linux tools

Step 1 – Installing Proxmox VE
Why Proxmox?
- Bare-metal hypervisor (not nested)
- Excellent Web UI
- Native support for:
  - VMs
  - LXC
  - Snapshots
  - Cloud-Init
- Strong community and documentation

Installation notes

1. Booted the Beelink from the Ventoy USB
2. Selected Install Proxmox VE (Graphical)
3. Used local-lvm storage layout
4. Set a static hostname (e.g. pve1.local)

Networking via Ethernet (Wi-Fi not recommended for Proxmox)
After installation, Proxmox is managed entirely via the web interface.

Step 2 – Preparing Proxmox for Cloud-Init Templates
Enable Snippets Storage
Cloud-Init “snippets” allow injecting custom YAML config into VMs at boot time.
In the Proxmox UI:
Datacenter → Storage → local → Edit
Enable:

✅ Snippets

This creates (or uses):

/var/lib/vz/snippets

Step 3 – Creating a Reusable Cloud-Init Bootstrap Snippet

This snippet installs common tools and prepares the VM for Docker workloads.

Create the snippet file
mkdir -p /var/lib/vz/snippets
nano /var/lib/vz/snippets/common-config.yaml

common-config.yaml
#cloud-config
packages:
  - git
  - docker.io
  - curl
  - htop

runcmd:
  - usermod -aG docker mazora
  - systemctl enable --now docker

Why use a snippet?

Keeps OS identity separate from system opinionation

Reusable across many VMs

Easy to modify later without rebuilding everything

Cleaner than hardcoding logic into templates

Step 4 – Creating the Cloud-Init VM Template (CLI)

Using the CLI is the most reliable way to import official cloud images.

1️⃣ Download the Debian cloud image
```sh
wget https://cloud.debian.org/images/cloud/trixie/latest/debian-13-genericcloud-amd64.qcow2
```
2️⃣ Create the base VM
```sh
qm create 9000 --name debian-13-docker-template \
  --memory 2048 \
  --cores 2 \
  --machine q35 \
  --bios ovmf \
  --agent enabled=1,fstrim_cloned_disks=1 \
  --net0 virtio,bridge=vmbr0
```
2. Add the EFI Storage (This creates the actual .raw or .qcow2 file)
This is the step that makes it work reliably.
```sh
qm set 9000 --efidisk0 local-lvm:0,format=raw,pre-enrolled-keys=1
```
3️⃣ Import and attach, and resize the OS disk
```sh
qm importdisk 9000 debian-13-genericcloud-amd64.qcow2 local-lvm
qm set 9000 --scsihw virtio-scsi-pci --scsi0 local-lvm:vm-9000-disk-0
qm resize 9000 scsi0 32G
```

4️⃣ Add Cloud-Init drive and serial console
```sh
qm set 9000 --scsi1 local-lvm:cloudinit
qm set 9000 --boot c --bootdisk scsi0
qm set 9000 --serial0 socket --vga serial0
```

5️⃣ Attach the vendor snippet
```sh
qm set 9000 --cicustom "vendor=local:snippets/common-config.yaml"
```
Step 5 – Finalizing the Template in the Proxmox GUI

In the Proxmox Web UI:

Cloud-Init tab configuration
```
User: mazora
Password: (left empty – SSH key only)
SSH Public Key: paste id_rsa.pub
IP Config (net0): DHCP
```
Why SSH-only?

No shared passwords across clones

More secure

Cloud-native best practice

Step 6 – Convert to Template

Right-click the VM:

```Convert to Template```


At this point, the template is immutable and ready for cloning.

Step 7 – Deploying New VMs

To create a new VM:

Right-click the template → Clone

Mode: Full Clone

Name the VM (this becomes the hostname)

Start the VM

What happens automatically

Hostname set from VM name
SSH host keys regenerated
/etc/machine-id regenerated
User mazora created
Docker installed and enabled
Docker group permissions fixed
No manual setup required.
Resulting Architecture
Proxmox host → bare metal
Cloud-Init template → single source of truth
Cloned VMs → infra, media, apps, etc.
Docker everywhere, consistently
This setup prioritizes:

reproducibility

clarity

ease of management

future automation (Ansible / Terraform)

for a single-copypasteable script, copy this:
```sh
# Set Variables
VM_ID=9001
STORAGE=local-lvm
IMG_NAME=debian-13-genericcloud-amd64.qcow2
USER_NAME=mazora

# 1. Create the VM Shell
qm create $VM_ID --name debian-13-template \
  --memory 2048 --cores 2 \
  --machine q35 --bios ovmf \
  --agent enabled=1,fstrim_cloned_disks=1 \
  --net0 virtio,bridge=vmbr0

# 2. Initialize UEFI Storage
qm set $VM_ID --efidisk0 $STORAGE:0,format=raw,pre-enrolled-keys=1

# 3. Import and attach the disk with DISCARD and SSD EMULATION
qm importdisk $VM_ID $IMG_NAME $STORAGE
qm set $VM_ID --scsihw virtio-scsi-pci \
  --scsi0 $STORAGE:vm-$VM_ID-disk-0,discard=on,ssd=1
qm resize $VM_ID scsi0 32G


# 4. Add Cloud-Init drive (Using SCSI for modern UEFI compatibility)
qm set $VM_ID --scsi1 $STORAGE:cloudinit

# 5. Boot and Console settings
qm set $VM_ID --boot c --bootdisk scsi0
qm set $VM_ID --serial0 socket --vga serial0

# 6. Attach the Custom Vendor Snippet
qm set $VM_ID --cicustom "vendor=local:snippets/common-config.yaml"

# 7. Update some basic cloudinit
# Note: ip6=dhcp enables IPv6 auto-configuration
qm set $VM_ID --ciuser $USER_NAME \
  --ipconfig0 ip=dhcp,ip6=dhcp
```
