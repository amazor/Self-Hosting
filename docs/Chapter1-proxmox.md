# 🏗️ Chapter 1: The Proxmox Foundation

## 🛰️ Introduction: The Core of the Lab

*Scope: this guide was written for Proxmox 9.x and Debian 13 (Trixie).*

This chapter is the most critical phase of the journey. We are not just installing an operating system; we are building a **Type-1 Hypervisor environment**. This layer sits directly on your hardware and acts as the "Manager" for every service, database, and media tool we will deploy in later chapters.


### What is Proxmox?
Proxmox Virtual Environment (PVE) is a complete, open-source platform for enterprise virtualization. It combines two powerful technologies:
1.  **KVM (Kernel-based Virtual Machine):** For full hardware virtualization (running entire OSs like Debian, Ubuntu, or Windows).
2.  **LXC (Linux Containers):** For lightweight, shared-kernel isolation.

> ### 🧠 Philosophy: Why Proxmox for this Lab?
> I chose Proxmox because it transforms physical hardware into flexible "Software-Defined Infrastructure."
> * **The Snapshot Safety Net:** Before making a risky configuration change or updating a complex Docker stack, we take a "Snapshot." If the update fails, we roll back to the exact working state in seconds.
> * **Service Decoupling:** Instead of one giant OS running 50 tools, we create specialized VMs. This ensures that a failure in your "Media VM" doesn't take down your "Home Automation VM."
> * **Template-Driven Workflow:** We treat our infrastructure as "code." By using Cloud-Init, we ensure that every VM is a perfect, reproducible clone of our original design.
> * **Cluster Ready:** While we are starting with a single node, Proxmox is built for **High Availability (HA)**. If you add a second or third node later, Proxmox can automatically migrate your running VMs between them.

> ### 🧠 Philosophy: The "VM-First" Strategy
> While Proxmox supports LXC (containers), this guide prioritizes **Virtual Machines (VMs)** for our Docker hosts:
> * **Isolation:** VMs provide a "hard" security boundary via hardware virtualization (VT-x/AMD-V).
> * **Kernel Integrity:** Docker is designed to interact with a specific Linux kernel. Running Docker in LXC requires "nesting," which often breaks after Proxmox kernel updates or requires complex manual security tweaks.
> * **Snapshots & Migration:** Moving a VM between different Proxmox nodes is significantly more reliable than moving LXCs with complex mount points or local ID mapping dependencies.

---

## Table of contents
- [Step 1 – The Base Install](#step-1--the-base-install)
- [Step 2 – Post-Install & Environment Prep](#step-2--post-install--environment-prep)
- [Step 3 – The Cloud-Init Bootstrap Snippet](#step-3--the-cloud-init-bootstrap-snippet)
- [Step 4 – Automation Script (The "Template Maker")](#step-4--automation-script-the-template-maker)
- [Step 5. Finalize in the GUI](#step-5-finalize-in-the-gui)
- [Step 6 – Verification](#step-6--verification-is-it-actually-working)
- [Philosophy & FAQ: The "Why" Behind the Defaults](#-philosophy--faq-the-why-behind-the-defaults)

---

## Step 1 – The Base Install
1. **Download:** Get the latest ISO from [proxmox.com](https://www.proxmox.com/en/downloads).
2. **Flash:** Use Ventoy (see [Chapter 0](Chapter0-hardware.md)) to boot the installer.
3. **Network:** Set a **Static IP** (e.g., `192.168.1.50`). Do not use DHCP; your server's address must stay permanent.

> ### 🧠 Reasoning: VM vs. LXC for Docker?
> This is a major homelab crossroads. I chose to run Docker inside a **VM** rather than an LXC container. 
> * **Consistency:** Docker expects a full Linux kernel. LXCs share the host kernel, which can lead to "nesting" errors and storage driver headaches.
> * **Security:** A VM provides a harder isolation boundary. 
> * **Portability:** Moving a VM between Proxmox nodes is often more seamless than moving LXCs with complex mount points.

> ### 🧠 Philosophy: Ethernet and Static IPs for Servers 
> > Proxmox does not work easily with DHCP, and for good reason. A server is a foundation; if its address changes, every bookmark, API connection, and DNS record pointing to it breaks. 
> >  * **Unchanging Identity:** A Static IP ensures your management interface is always where you expect it to be. 
> >  * **Ethernet is Mandatory:** In a homelab, Wi-Fi is the enemy of stability. Ethernet provides the consistent latency and full-duplex throughput required for cluster communication and high-speed storage backups.

**❓ Common Questions:**
* **Q: Which Filesystem should I choose?**
  * *A: Use **ext4** if you have a single drive. Use **ZFS (RAID1)** if you have two identical drives and want data redundancy.*
* **Q: I can't access the Web UI?**
  * *A: Ensure you are using `https://` and port `:8006`. Your browser will warn you about a "Self-Signed Certificate"—this is normal for local servers. Click "Advanced" and "Proceed."*

---

## Step 2 – Post-Install & Environment Prep

Once Proxmox is installed and you’ve logged into the Web UI (at `https://your-ip:8006`), it’s time to polish the experience. By default, Proxmox is configured for enterprise users with paid licenses. For a homelab, we want to switch to the community repositories to ensure we get updates without the "No Subscription" nagging.
### 🛠️ The "Proxmox Post-Install" Script

Instead of manually editing repository files, we use the industry-standard community script. This script will:
- Disable the **Enterprise Repository**.
- Enable the **No-Subscription Repository**.
- Remove the **"No Valid Subscription"** nag popup.
- Update your system to the latest stable packages.

**Instructions:**
1. Go to the [Proxmox VE Post Install](https://community-scripts.github.io/ProxmoxVE/scripts?id=post-pve-install) website.
2. Copy the script provided on the page.
3. In your Proxmox Web UI, click on your **Node Name** (e.g., `pve`) in the left sidebar.
4. Select **Shell** and paste the command.
5. Follow the prompts. (I recommend selecting **Yes** for most options to get a clean, updated base).

> ### 🧠 Philosophy: The "No-Subscription" Repository
> 
> Proxmox is open-source. The "No-Subscription" repository provides the same software as the Enterprise version, just without the official support contract and with slightly faster (and occasionally less tested) updates. For a lab environment, this is exactly what we want.

### 📂 Enabling "Snippets" via the GUI

Before we can use Cloud-Init automation (the "magic" part of this chapter), Proxmox needs permission to store configuration snippets on your local drive. This is disabled by default.

**Follow these steps in the Web UI:**
1. Navigate to **Datacenter** (at the top of the left-hand tree).
2. Select **Storage** in the middle menu.
3. Click on the storage named **local** (this is usually where your ISOs are stored).
4. Click the **Edit** button at the top.
5. Find the **Content** dropdown menu.
6. Ensure that **Snippets** is highlighted/selected alongside your other content types (ISO, VZDump, etc.).
7. Click **OK**.
    
> ### 🧠 Reasoning: Why do we need Snippets?
> In Proxmox, a "Snippet" is just a text file (like our `.yaml` Cloud-Init configs). By enabling this, we tell Proxmox: _"Hey, I’m going to put some automation instructions in `/var/lib/vz/snippets`, and I want you to be able to read them."_
---

## Step 3 – The Cloud-Init Bootstrap Snippet

This is where we move from "clicking buttons" to "infrastructure as code." If the Proxmox installation is the foundation, Cloud-Init is the automated crew that builds the house the moment you give them the blueprint.

### 🛰️ What is Cloud-Init?

Cloud-Init is an industry-standard tool used to automate the initialization of a virtual machine during its first boot. Instead of you manually logging in to every new VM to install Docker, create a user, and set up security, Cloud-Init reads a configuration file (the "blueprint") and does it all for you in the background.

> ### 🧠 Philosophy: Cattle, Not Pets
> 
> In the old days of IT, servers were "Pets"—you gave them names, you manually nursed them back to health, and you knew every detail of their configuration. In a modern homelab, we treat VMs as **"Cattle."** If a VM breaks or gets cluttered, we delete it and spin up a new one using this script. In 120 seconds, we have a fresh, perfectly configured Docker host ready to go.

---

### 🛠️ What are we configuring?

Our specific snippet handles the "boring" parts of server setup so you can get straight to the fun stuff. Here is exactly what the configuration below is doing:

- **Security & Users:** It creates a user named `mazora`, grants them `sudo` privileges without requiring a password for commands, and locks the default root account for better security.
- **The Docker Engine:** It adds the official Docker repository (rather than the older versions found in default Linux repos) and installs the latest version of Docker CE and Docker Compose.
- **System Health:** * **QEMU Guest Agent:** Vital for Proxmox to "talk" to the VM (reporting IP addresses and allowing graceful shutdowns).
    - **Log Rotation:** We limit Docker logs to **50MB**. Without this, runaway logs can eventually fill your entire virtual disk and crash the VM.
    - **Swap File:** We create a **2GB swap file**. This acts as a "safety net" for memory; if a Docker build suddenly spikes in RAM usage, the VM will use the swap space instead of crashing.

---

### 📝 The Blueprint (`common-config.yaml`)

Create a file called **common-config.yaml** in `/var/lib/vz/snippets/` through your **Proxmox Shell** to create the blueprint file. This is the directory we enabled in step 2.
```yaml
#cloud-config

# 1. Automatic Package Updates on first boot
package_update: true
package_upgrade: true
package_reboot_if_required: true

# 2. User Configuration
users: 
  - name: mazora 
    groups: sudo, docker 
    shell: /bin/bash 
    sudo: ALL=(ALL) NOPASSWD:ALL 
    lock_passwd: true

# 3. Official Docker Repo Setup
apt:
  sources:
    docker.list:
      source: "deb [arch=amd64] https://download.docker.com/linux/debian \$RELEASE stable"
      keyid: 9DC858229FC7DD38854AE2D88D81803C0EBFCD88

# 4. Essential Packages
packages:
  - git
  - curl
  - docker-ce
  - docker-ce-cli
  - containerd.io
  - docker-buildx-plugin
  - docker-compose-plugin
  - avahi-daemon
  - qemu-guest-agent
  - htop

# 5. Inject Docker Log Rotation (Safety First)
write_files:
  - path: /etc/docker/daemon.json
    content: |
      {
        "log-driver": "json-file",
        "log-opts": {
          "max-size": "50m",
          "max-file": "3"
        }
      }

# 6. Final System Tweaks
runcmd:
  - systemctl enable --now docker
  - systemctl enable --now qemu-guest-agent
  - systemctl enable --now avahi-daemon
  
  # Set up a 2GB Swap file to prevent crashes during heavy builds
  - fallocate -l 2G /swapfile
  - chmod 600 /swapfile
  - mkswap /swapfile
  - swapon /swapfile
  - echo '/swapfile none swap sw 0 0' >> /etc/fstab
  
  # Set swappiness to 10 (Last Resort mode)
  - sysctl vm.swappiness=10
  - echo 'vm.swappiness=10' >> /etc/sysctl.conf
```

### 📝 Line-by-Line Blueprint Breakdown

*This section explains each part of the Cloud-Init snippet; you can skip it if you only need the file.*

- **System Update & Upgrade**
    - `package_update: true`: Tells the VM to run `apt update` to refresh the list of available software as soon as it boots.
    - `package_upgrade: true`: Runs `apt upgrade` to ensure every pre-installed package is at the latest security version.
    - `package_reboot_if_required: true`: If a kernel update is installed, the VM will automatically reboot itself to apply it before you even log in.
- **User & Security Configuration**
    - `name: mazora`: Creates your primary administrative user account.
    - `groups: sudo, docker`: Adds the user to the `sudo` group (for admin rights) and the `docker` group (so you don't have to type `sudo` before every docker command).
    - `shell: /bin/bash`: Sets the default command-line environment to Bash.
    - `sudo: ALL=(ALL) NOPASSWD:ALL`: Allows the user to run administrative commands without being prompted for a password—essential for automation
    - `lock_passwd: true`: Disables password logins entirely. **This forces the use of SSH keys, making the VM immune to password-guessing (brute-force) attacks.**
- **Official Docker Repository**
    - `source: "deb [arch=amd64] ..."`: Adds the official Docker Inc. repository to the system so you get the latest version of Docker rather than the older versions in the standard Debian repos.
    - `keyid: 9DC85822...`: Automatically downloads and trusts the digital signature key from Docker to ensure the software hasn't been tampered with.
- **Package Installation**
    - `docker-ce` & `docker-ce-cli`: Installs the core Docker engine and the command-line tools.
    - `containerd.io`: Installs the industry-standard "runtime" that actually manages the lifecycle of containers.
    - `docker-buildx-plugin` & `docker-compose-plugin`: Adds modern features like multi-architecture builds and the `docker compose` command.
    - `qemu-guest-agent`: The "translator" that lets Proxmox see the VM’s IP address and send "Shutdown" signals.
    - `avahi-daemon`: Enables mDNS so you can find your VM at `hostname.local` instead of memorizing IP addresses.
- **Docker Log Rotation (The Safety Net)**
    - `path: /etc/docker/daemon.json`: Creates a configuration file for the Docker engine.
    - `"max-size": "50m"`: Tells Docker that once a container's log file reaches 50MB, it's time to start a new one.
    - `"max-file": "3"`: Limits Docker to keeping only the 3 most recent log files. This prevents a "chatty" container from filling up your entire virtual hard drive.
- **Performance & Swap Tuning**
    - `fallocate -l 2G /swapfile`: Reserves 2GB of space on the hard drive to act as "emergency RAM."
    - `mkswap` & `swapon`: Formats that 2GB space as Swap and activates it immediately.
    - `echo '/swapfile none swap sw 0 0' >> /etc/fstab`: Ensures the Swap file is turned back on every time the VM reboots.
    - `sysctl vm.swappiness=10`: Tells the Linux kernel: "Only use the Swap file if you are absolutely out of physical RAM." This keeps the VM fast while providing a safety net.

---
### 🔑 The SSH Key Requirement
Because we set `lock_passwd: true`, the user `mazora` **has no password**. If you open the "Console" in Proxmox, you will be stuck at a login prompt you cannot bypass.
To get in, you **must** use an SSH key. This is the single most common "gotcha" for new Cloud-Init users. You must paste your **Public Key** into the Proxmox Cloud-Init GUI tab before the first boot. Once the VM is up, you'll connect from your own terminal:
`ssh mazora@<VM_IP_or_hostname.local>`

## Step 4 – Automation Script (The "Template Maker")
Now that we have our Cloud-Init "blueprint" ready, we need a VM to use it. While you can create a VM in the Proxmox GUI, we are using a script to build our **"Golden Image"** template.
### ❓ Why a script instead of the GUI?
You might wonder why we’re heading back to the terminal. There are three critical reasons:
1. **Cloud-Image Support:** We are using official Cloud-Init images (`.qcow2` files). The Proxmox GUI is designed to mount ISOs; it actually doesn't have a button to "Import Disk" for these cloud-ready images. That must be done via the `qm importdisk` command.
2. **Custom Snippet Mapping:** Attaching our `common-config.yaml` as a "vendor" configuration is a specialized command (`--cicustom`) that isn't exposed in the standard Proxmox web interface.
3. **Consistency:** This script ensures that hardware optimizations—like **SSD Emulation**, **Discard (TRIM)**, and the **QEMU Agent**—are set perfectly every time.
    
> ### ⚠️ Hardware Note: x86 Only
> 
> This script is specifically designed for **x86_64 architecture** (Intel/AMD). It uses the `q35` machine type and `OVMF (UEFI)` bios, and pulls the `amd64` Debian image. This will not work on ARM-based Proxmox nodes (like a Raspberry Pi cluster) without modifying the image URL and machine settings.

---

### 🛠️ The Script (`create_template.sh`)

Copy this script into your Proxmox Shell. It is **idempotent**, meaning it will check if the VM ID already exists before trying to run, preventing you from accidentally overwriting an existing machine.

```bash
#!/bin/bash

# Configuration (Change these or pass as arguments)
VM_ID=${1:-9000}
VM_NAME=${2:-debian-13-template}
STORAGE=${3:-local-lvm}
USER_NAME="mazora"
IMG_URL="https://cloud.debian.org/images/cloud/trixie/latest/debian-13-genericcloud-amd64.qcow2"
IMG_FILE="debian-13-temp.qcow2"

# Check if VM ID already exists
if qm status $VM_ID > /dev/null 2>&1; then
    echo "Error: VM ID $VM_ID already exists. Choose a different ID."
    exit 1
fi

# Download image if not present
if [ ! -f "$IMG_FILE" ]; then
    echo "Downloading Debian Cloud Image..."
    wget -O $IMG_FILE $IMG_URL
fi

echo "Creating VM $VM_ID ($VM_NAME)..."

# 1. Create the VM Shell
qm create $VM_ID --name $VM_NAME \
  --memory 2048 --cores 2 \
  --machine q35 --bios ovmf \
  --agent enabled=1,fstrim_cloned_disks=1 \
  --net0 virtio,bridge=vmbr0

# 2. Initialize UEFI Storage
qm set $VM_ID --efidisk0 $STORAGE:0,format=raw,pre-enrolled-keys=1

# 3. Import and attach the disk (With SSD Emulation and Discard for NVMe health)
qm importdisk $VM_ID $IMG_FILE $STORAGE
qm set $VM_ID --scsihw virtio-scsi-pci \
  --scsi0 $STORAGE:vm-$VM_ID-disk-0,discard=on,ssd=1
qm resize $VM_ID scsi0 32G

# 4. Add Cloud-Init drive and Serial Console
qm set $VM_ID --scsi1 $STORAGE:cloudinit
qm set $VM_ID --boot c --bootdisk scsi0
qm set $VM_ID --serial0 socket --vga serial0

# 5. Attach the Custom Vendor Snippet
qm set $VM_ID --cicustom "vendor=local:snippets/common-config.yaml"

# 6. Basic Cloud-Init Network Config
qm set $VM_ID --ciuser $USER_NAME --ipconfig0 ip=dhcp,ip6=dhcp

echo "VM $VM_ID Created. Now, add your SSH key in the Proxmox GUI and 'Convert to Template'."
```

> ### 🧠 Reasoning: Technical Polish
> * **SSD Emulation & Discard:** Vital for SSD longevity. It allows the VM to tell the physical SSD which blocks are no longer in use (TRIM).
> * **QEMU Guest Agent:** This allows the Proxmox host to see the VM's IP address and send "graceful" shutdown commands. Without this, Proxmox just "pulls the plug."

### 🔍 Technical Highlights: Why these commands?
- **`--agent enabled=1`**: This is a "must-have." It allows Proxmox to see the internal IP address of the VM once it boots. Without this, you'll be hunting for the IP in your router's settings.
- **`discard=on,ssd=1`**: This enables **SSD Emulation**. It allows the guest OS (Debian) to send TRIM commands to the physical SSD. This prevents performance degradation and extends the life of your hardware.
- **`--serial0 socket --vga serial0`**: This redirects the VM's display to a serial terminal. It’s a cleaner way to view the boot process and Cloud-Init logs directly from the Proxmox dashboard.
- **`--cicustom`**: This is the "secret sauce." It tells Proxmox: _"When you generate the Cloud-Init ISO for this VM, include my custom configuration file as the 'vendor' layer."_

### Run the Script
You have two ways to run this script.
**Option A: Run with Defaults** This uses the values already in your script (VM ID 9000, Debian 13, local-lvm storage).
```sh
./create_template.sh
```

**Option B: Pass Custom Arguments** If you want to change the ID, Name, or Storage location on the fly, you can pass them as arguments:
```sh
# Syntax: ./create_template.sh [ID] [NAME] [STORAGE_POOL]
./create_template.sh 8000 my-docker-template local-zfs
```

#### Tips for this script:
- **The Snippet Requirement:** This script explicitly looks for `/var/lib/vz/snippets/common-config.yaml`. If you haven't created that file yet (following Step 3 of our journey), the script will finish, but the VM won't auto-install Docker when it boots.
- **Storage Pool:** The script defaults to `local-lvm`. If your Proxmox setup uses a different name for your main storage (like `local-zfs` or just `local`), make sure to change the `STORAGE` variable at the top of the script or pass it as the third argument.
- **Cleaning Up:** The script downloads the Debian image to your current folder (`debian-13-temp.qcow2`). Once your template is created, you can safely delete this file to save space: `rm debian-13-temp.qcow2`.


### 🔄 Post-Creation Flexibility

One of the best features of Proxmox is that nothing we’ve done in this script is "locked in." While the script sets a baseline (2 vCPUs, 2GB RAM, 32GB Storage), you can easily scale these resources up or down as your needs change.

**You can change these directly in the GUI:**
- **RAM & CPU:** Go to the **Hardware** tab of your VM. You can increase the memory or add more cores. (Note: Reducing RAM usually requires a reboot, but increasing it can often be done live if "Hotplug" is enabled).
- **Storage Space:** If your Docker images start taking up too much room, go to **Hardware > Hard Disk**, click **Disk Action**, and select **Resize**. After restarting the VM, the Cloud-Init setup we used will automatically grow the partition to fill the new space.
- **Network:** You can switch bridges or change MAC addresses without ever touching the command line again.

> ### 🧠 Philosophy: The "Golden" Baseline
> 
> Think of this template as your "Base Model" car. You can always add a roof rack or better tires later. We set the template to 2GB of RAM because it’s the "Sweet Spot"—enough to run most Docker stacks comfortably, but small enough to fit 3 or 4 VMs on modest hardware.

---
### Step 5. Finalize in the GUI

Once the script finishes and says `VM XXX Created`, head back to the Proxmox Web UI:

1. **Select the new VM** (e.g., 9000).
2. **Add SSH Key:** Go to the **Cloud-Init** tab. Double-click **SSH public key**, paste your key, and click OK.
3. **Convert to Template:** Right-click the VM in the left sidebar menu and select **Convert to Template**.

*Recovery:* If a clone fails Cloud-Init (e.g. no Docker after boot), delete the VM, re-clone from the template, re-add your SSH key in the Cloud-Init tab, and start again before first boot.

### ✅ Step 6 – Verification: Is it actually working?

The moment of truth. Let’s verify that the "Blueprint" and the "Template" are working together correctly.

1. **Clone the Template:** Right-click your Template (ID 9000) and select **Clone**. Give it a new ID (e.g., 100) and a name (e.g., `docker-test`).
2. **Add SSH Key:** Ensure your SSH key is in the **Cloud-Init** tab of the new VM.
3. **Start the VM:** Click Start and head to the **Console** tab.
   
You will see a lot of text flying by—this is Cloud-Init installing Docker and setting up your swap file. Once it settles at a login prompt, **do not try to log in there.** Instead, go to your main computer's terminal and run:

```sh
# Replace <VM_IP> with the IP address shown in the Proxmox Summary tab OR the name of the VM + ".local" if avahi is running
ssh mazora@<VM_IP>
```
#### The Success Checklist
Once you are logged in via SSH, run this one-liner to verify the entire setup:

```sh
docker --version && systemctl status qemu-guest-agent --no-pager && free -h
```

**If everything is correct, you will see:**
- **Docker:** A version number (e.g., `Docker version 27.x.x`).
- **Guest Agent:** A green `active (running)` status.
- **Swap:** A line showing `Swap: 2.0Gi` at the bottom of the memory report.

---

## 🧠 Philosophy & FAQ: The "Why" Behind the Defaults

In any homelab journey, the "Hardcoded Defaults" are rarely accidental. They represent a specific logic designed to keep your infrastructure organized, predictable, and performant.

### 🏛️ The Logic of the Defaults

* **Why VM ID 9000?**
    * In Proxmox, IDs start at 100. Using `9000+` for templates is a common sysadmin "best practice." It keeps your active, running VMs (the "Cattle") in the lower ranges and your "Golden Images" (the "Blueprints") far out of the way, preventing accidental deletions or ID collisions during automation.
* **Why Debian 13 (Trixie)?**
    * Debian is the gold standard for stability. Unlike Ubuntu, it doesn't come with "Snap" or extra bloatware. We use the latest branch (13) to ensure we have a modern kernel and up-to-date libraries required by the newest AI and media processing Docker containers.
* **Why 2 Cores and 2GB of RAM?**
    * This is the "Sweet Spot." It is enough to run almost any Docker stack comfortably while remaining small enough to fit 5 or 6 VMs on modest hardware. It’s easier to scale these numbers **up** in the GUI later than it is to scale them down.
* **Why `q35` Machine & `OVMF` (UEFI)?**
    * Modern hardware deserves modern chipsets. `q35` handles PCIe and hardware pass-through much better than the 1996-era `i440fx` default. Using UEFI/OVMF ensures your VM is future-proofed if you ever move it to different hardware.

---

### ❓ Frequently Asked Questions

**Q: Can I change the username `mazora`?**
* **A:** Yes, but you must change it in **both** the `common-config.yaml` snippet AND the `create_template.sh` script. If they don't match, you'll be trying to SSH into a user that doesn't exist on the system.

**Q: Why is the disk size set to 32GB?**
* **A:** 32GB is plenty for the OS and Docker engine. We keep the "OS Drive" small so that backups and snapshots are lightning-fast. For large data (like media libraries), we will attach separate virtual disks in later chapters.

**Q: Why use `virtio-scsi-pci` with `discard=on`?**
* **A:** If you are running on an NVMe or SSD, this is mandatory. It enables **TRIM**, which allows the VM to tell the physical SSD when data is deleted. This prevents your drive from wearing out prematurely and keeps performance high.

**Q: I’m at the Proxmox Console and I can't log in!**
* **A:** This is intentional. Because we set `lock_passwd: true` for security, the user has no password. You **must** access the machine via SSH using the public key you provided in the Cloud-Init tab.

**Q: How do I run the script?**
* **A:** Copy the script to your Proxmox host, make it executable with `chmod +x create_template.sh`, and run it with `./create_template.sh`. It must be run on the Proxmox host shell, not inside a VM.

**Q: Does this work on Raspberry Pi / ARM?**
* **A:** No. This specific script pulls `amd64` (x86) images and uses Intel/AMD chipset configurations. For ARM, you would need to point to an ARM64 cloud image and adjust the machine type.