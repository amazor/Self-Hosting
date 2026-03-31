# Chapter 1A – Intel iGPU Passthrough (Proxmox Host)

## Introduction

*Scope: this guide covers full PCI passthrough of an Intel integrated GPU on a single-socket Proxmox host. Written for Intel 12th–14th gen (Alder Lake / Raptor Lake / Raptor Lake Refresh) and Proxmox 8.x–9.x.*

This chapter documents the **Proxmox host-side** steps required to pass the Intel iGPU through to a single guest VM. Everything here happens on the Proxmox shell or BIOS — the guest-side setup lives in [Chapter 2D (Accelerated VM)](Chapter2d-accelerated.md#vm-side-gpu-prerequisites).

The Accelerated VM is the only VM that should ever own the GPU. Other VMs and the Proxmox host treat the device as "not there." See [Chapter 2D — Boundary Rules](Chapter2d-accelerated.md#boundary-rules-for-the-gpu) for why.

> ### 🧠 Philosophy: Why Full Passthrough (Not GVT-g or SR-IOV)?
> Intel dropped GVT-g (mediated virtual GPUs) after 11th gen. SR-IOV exists for 12th+ gen iGPUs
> but requires out-of-tree kernel patches and is not mainlined in Proxmox's default kernel.
> Full PCI passthrough is the simplest, most stable path. Since only one VM needs the GPU
> and Proxmox is managed headlessly (web UI + SSH), giving up host-side graphics is free.

---

## Table of contents

- [Prerequisites](#prerequisites)
- [Step 1 – BIOS Configuration](#step-1--bios-configuration)
- [Step 2 – Enable IOMMU in GRUB](#step-2--enable-iommu-in-grub)
- [Step 3 – Load VFIO Kernel Modules](#step-3--load-vfio-kernel-modules)
- [Step 4 – Blacklist Host GPU Drivers](#step-4--blacklist-host-gpu-drivers)
- [Step 5 – Rebuild Initramfs and Reboot](#step-5--rebuild-initramfs-and-reboot)
- [Step 6 – Verify IOMMU and Identify the GPU](#step-6--verify-iommu-and-identify-the-gpu)
- [Step 7 – Add the GPU to the VM](#step-7--add-the-gpu-to-the-vm)
- [Quick Reference](#quick-reference)
- [Troubleshooting](#troubleshooting)
  - [Emergency local CLI access via nomodeset](#want-to-temporarily-reclaim-host-video-emergency-local-cli-access)

---

## Prerequisites

Before starting:

- Proxmox is installed and accessible via the web UI ([Chapter 1](Chapter1-proxmox.md)).
- You have SSH or console access to the Proxmox host shell.
- The Accelerated VM (VMID 230) is **only needed for [Step 7](#step-7--add-the-gpu-to-the-vm)** — you can complete all host-side preparation (Steps 1–6) first and create the VM afterwards. When you do create it, use the **q35 + OVMF (UEFI)** machine type (the default from [Chapter 1's template script](Chapter1-proxmox.md#step-4--automation-script-the-template-maker)). q35 handles PCIe passthrough much better than the legacy i440fx chipset.

---

## Step 1 – BIOS Configuration

Enter the Beelink BIOS (typically `Del` or `F2` at boot) and confirm:

| Setting | Required Value | Where to Find |
|---------|---------------|---------------|
| **VT-d** (Intel Virtualization Technology for Directed I/O) | **Enabled** | Advanced → CPU Configuration (or similar) |
| **VT-x** (Intel Virtualization Technology) | **Enabled** | Should be enabled by default |
| **Integrated Graphics** | **Enabled** or **Auto** | Advanced → Graphics or Display Configuration |

VT-d is the hardware IOMMU feature. Without it, PCI passthrough cannot work.

> **Tip:** BIOS menu layouts vary between Beelink firmware versions. If you don't see VT-d under CPU Configuration, check under Chipset or Security menus.

---

## Step 2 – Enable IOMMU in GRUB

On the Proxmox host shell, edit the GRUB configuration:

```bash
nano /etc/default/grub
```

Find the `GRUB_CMDLINE_LINUX_DEFAULT` line and add the IOMMU parameters:

```
GRUB_CMDLINE_LINUX_DEFAULT="quiet intel_iommu=on iommu=pt"
```

Then update GRUB:

```bash
update-grub
```

**What these do:**

| Parameter | Purpose |
|-----------|---------|
| `intel_iommu=on` | Activates the Intel IOMMU (VT-d) at the kernel level |
| `iommu=pt` | Passthrough mode — only applies IOMMU to devices explicitly assigned to VMs, avoiding performance overhead on all other devices |

> **Note for systemd-boot users:** If your Proxmox install uses systemd-boot instead of GRUB (uncommon but possible), add `intel_iommu=on iommu=pt` to the kernel command line in `/etc/kernel/cmdline` and run `proxmox-boot-tool refresh`.

---

## Step 3 – Load VFIO Kernel Modules

VFIO is the kernel framework that allows safe device assignment to VMs.

Create a drop-in file under `/etc/modules-load.d/` (the modern replacement for `/etc/modules`):

```bash
cat > /etc/modules-load.d/vfio.conf << 'EOF'
vfio
vfio_iommu_type1
vfio_pci
EOF
```

---

## Step 4 – Blacklist Host GPU Drivers

Prevent Proxmox from claiming the iGPU before VFIO can bind to it.

Create `/etc/modprobe.d/blacklist-gpu.conf`:

```bash
cat > /etc/modprobe.d/blacklist-gpu.conf << 'EOF'
blacklist i915
blacklist snd_hda_intel
EOF
```

| Driver | Why Blacklist |
|--------|---------------|
| `i915` | The Intel graphics kernel driver — if it loads, the host claims the GPU and VFIO cannot |
| `snd_hda_intel` | The Intel HDMI/DisplayPort audio device, which shares an IOMMU group with the iGPU — if it binds, it can block passthrough of the whole group |

> ### ⚠️ You Will Lose Local Video Output
> Blacklisting `i915` means the Proxmox host has no local display output (HDMI/DP goes dark).
> This is fine — you manage Proxmox via the **web UI** and **SSH**. The noVNC console in the
> web UI still works for VM consoles. If you ever need emergency local access, boot with
> `nomodeset` on the GRUB command line to temporarily bypass the blacklist.

---

## Step 5 – Rebuild Initramfs and Reboot

Apply all module and blacklist changes:

```bash
update-initramfs -u -k all
reboot
```

The host will come back without local video. Access it via the web UI or SSH.

---

## Step 6 – Verify IOMMU and Identify the GPU

After reboot, SSH into the Proxmox host and run these checks:

### 6a. Confirm IOMMU is active

```bash
dmesg | grep -e DMAR -e IOMMU
```

Look for lines like:

```
DMAR: IOMMU enabled
DMAR: Intel(R) Virtualization Technology for Directed I/O
```

If you don't see these, VT-d is not enabled in BIOS or the GRUB parameter didn't take effect.

### 6b. Find the iGPU PCI address

```bash
lspci -nn | grep VGA
```

Expected output (address and device ID will vary by CPU):

```
00:02.0 VGA compatible controller [0300]: Intel Corporation Raptor Lake-P [...] [8086:a7a0]
```

The address `00:02.0` is standard for Intel iGPUs. Note the full address — you'll use it in Step 7.

### 6c. Check the IOMMU group

```bash
for d in /sys/kernel/iommu_groups/*/devices/*; do
  n=$(basename "$d")
  g=$(basename "$(dirname "$(dirname "$d")")")
  echo "IOMMU Group $g: $(lspci -nns "$n")"
done | sort -t: -k2 -n | grep "00:02"
```

You should see the iGPU (and possibly its audio sub-function) in its own IOMMU group. If it shares a group with unrelated devices, you would need an ACS override patch — but on Intel platforms, `00:02.0` almost always gets its own group.

---

## Step 7 – Add the GPU to the VM

### Option A: Proxmox Web UI

1. Select the Accelerated VM (VMID 230) → **Hardware** → **Add** → **PCI Device**
2. Select the Intel iGPU (device `0000:00:02.0`)
3. Settings:
   - **All Functions:** checked (passes the GPU and any sub-functions like audio)
   - **Primary GPU:** unchecked (the VM uses serial/virtio console, not GPU display)
   - **PCI-Express:** checked (for q35 machines)
   - **ROM-Bar:** unchecked (iGPUs have no separate option ROM)

### Option B: Command line

```bash
qm set 230 -hostpci0 0000:00:02.0,pcie=1,rombar=0
```

Replace `230` with your actual VMID if different.

### Verify the VM config

```bash
qm config 230 | grep -E "machine|bios|hostpci"
```

Expected:

```
bios: ovmf
machine: q35
hostpci0: 0000:00:02.0,pcie=1,rombar=0
```

---

## Quick Reference

All host-side steps in one block, for copy-paste after you've read the explanations above:

```bash
# --- GRUB ---
sed -i 's/^GRUB_CMDLINE_LINUX_DEFAULT=.*/GRUB_CMDLINE_LINUX_DEFAULT="quiet intel_iommu=on iommu=pt"/' /etc/default/grub
update-grub

# --- VFIO modules ---
cat > /etc/modules-load.d/vfio.conf << 'EOF'
vfio
vfio_iommu_type1
vfio_pci
EOF

# --- Blacklist GPU drivers ---
cat > /etc/modprobe.d/blacklist-gpu.conf << 'EOF'
blacklist i915
blacklist snd_hda_intel
EOF

# --- Apply and reboot ---
update-initramfs -u -k all
reboot

# --- After reboot: verify ---
dmesg | grep -e DMAR -e IOMMU           # should show "IOMMU enabled"
lspci -nn | grep VGA                     # note 00:02.0 address

# --- Assign to VM (replace 230 with your VMID) ---
qm set 230 -hostpci0 0000:00:02.0,pcie=1,rombar=0
```

---

## Troubleshooting

**IOMMU not enabled after reboot?**
- Verify VT-d is on in BIOS (some firmware updates can reset it).
- Check `cat /proc/cmdline` — the `intel_iommu=on` parameter should appear.
- If using systemd-boot, make sure you edited `/etc/kernel/cmdline` and ran `proxmox-boot-tool refresh`.

**VM fails to start with passthrough device?**
- Ensure `i915` is not loaded on the host: `lsmod | grep i915` should return nothing.
- Check that no other VM is using the same PCI device.
- Review the Proxmox task log (in the web UI, select the VM → Task History) for specific errors.

**GPU shows in wrong IOMMU group (shared with other devices)?**
- This is rare for `00:02.0` on Intel platforms but can happen with unusual BIOS configurations.
- An ACS override patch exists but carries security tradeoffs. Try updating the BIOS first.

**`/dev/dri` missing inside the VM even though passthrough looks correct?**
- Check `lspci | grep VGA` **inside the VM**. If the GPU appears (e.g. `Intel Corporation Raptor Lake-P [Iris Xe Graphics]`) but `/dev/dri/` is missing, the `i915` kernel driver is not loading.
- The most common cause: **cloud kernels** (e.g. `linux-image-*-cloud-amd64`) ship without GPU drivers like `i915`. Cloud kernels are stripped for virtual machines and assume no GPU access. Check with `uname -r` — if the kernel name contains `cloud`, that's the problem.
- **Fix:** Install the generic kernel and remove the cloud one:
  ```bash
  sudo apt install -y linux-image-amd64
  sudo apt remove --purge linux-image-cloud-amd64 linux-image-*-cloud-amd64
  sudo update-grub
  sudo reboot
  ```
  After reboot, `uname -r` should no longer say `cloud`, and `/dev/dri/card0` + `/dev/dri/renderD128` should appear.
- If `lspci` **does not** show a VGA device at all, the passthrough itself is not working — re-check IOMMU, VFIO, and the VM's `hostpci0` configuration above.

**Want to temporarily reclaim host video? (Emergency local CLI access)**

After blacklisting `i915`, the HDMI port goes dark permanently — but you can get a working CLI back for a single boot session without undoing anything. This is useful if SSH and the web UI are both unreachable.

> **What this does:** `nomodeset` tells the kernel not to switch the display to a graphics mode. The `i915` driver is still blacklisted, but the BIOS/UEFI hands off a basic text framebuffer that Linux keeps using. You get a usable (but low-resolution) console. The change is **not persistent** — the next normal boot returns to the no-display state.

**Step-by-step:**

1. **Connect** a keyboard and HDMI monitor to the Proxmox host.

2. **Reboot or power-cycle** the machine:
   ```bash
   reboot
   ```
   Or hold the power button if the host is unresponsive.

3. **Interrupt GRUB.** As soon as the machine starts:
   - Hold `Shift` (BIOS/legacy boot) **or** spam `Esc` (UEFI boot) until the GRUB menu appears.
   - On Proxmox the default timeout is short (3 seconds). Be ready — start pressing immediately after the BIOS splash clears.
   - If you miss the window, reboot and try again.

4. **Select the boot entry.** The top entry (`Proxmox VE GNU/Linux`) is already highlighted. Do **not** press `Enter` yet.

5. **Press `e`** to open the entry editor. You will see a multi-line kernel command block.

6. **Find the `linux` line.** It starts with `linux` and contains your kernel path and boot parameters, including `quiet intel_iommu=on iommu=pt`. It looks roughly like:
   ```
   linux /boot/vmlinuz-... root=... quiet intel_iommu=on iommu=pt ...
   ```

7. **Navigate to the end of the `linux` line** using the arrow keys. Add a space and then `nomodeset`:
   ```
   ... quiet intel_iommu=on iommu=pt nomodeset
   ```

8. **Boot with the edited entry:** press `Ctrl+X` or `F10`.

9. **Log in as root** when the login prompt appears. You now have a full shell — fix whatever needs fixing (network config, SSH keys, IP address, etc.).

10. **Reboot normally when done.** The `nomodeset` edit was not saved; the next boot returns to the standard (no-display) state.

> **Tip:** If the GRUB menu never appears, the timeout may be set to 0. In that case, try holding `Shift` earlier (right after power-on, before the BIOS splash). If the machine has already been configured to boot quickly and you consistently miss the window, you can make the timeout persistent from SSH: `sed -i 's/^GRUB_TIMEOUT=.*/GRUB_TIMEOUT=5/' /etc/default/grub && update-grub`.

---

**Next steps:**
- [Chapter 2D — Accelerated VM](Chapter2d-accelerated.md#vm-side-gpu-prerequisites): install VA-API drivers inside the guest and verify `/dev/dri`.
- [Chapter 3D — Accelerated Stack](Chapter3d-accelerated-stack.md#gpu-wiring): Docker-level GPU wiring for Plex and Immich.
