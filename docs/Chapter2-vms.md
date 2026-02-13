# 🧩 Chapter 2: VM Overview — How the Lab is Separated (and Why)

## 🛰️ Introduction: Defining the Shape of the Lab

**Prerequisites:** [Chapter 1](Chapter1-proxmox.md) (template created).

Before deploying Docker stacks, I want a crisp, durable answer to:

> **What VMs exist, what do they own, and what does each one contribute to the lab?**

This chapter is meant to be a **one-stop reference**:
- the VM split (the mental model)
- the primary apps that live in each VM
- a quick “what this app provides” view for readers (and future me)

The deeper “why did I choose *this* specific app” reasoning lives in the follow-ups:
- [Chapter 2A (core)](Chapter2a-core.md)
- **Chapter 2B** (`monitoring`) *(planned)*
- [Chapter 2C (media)](Chapter2c-media.md)
- **Chapter 2D** (`accelerated`) *(planned)*

Stack configuration and deployment (env, compose, bootstrap, deploy) for the core VM: [Chapter 3A (core stack)](Chapter3a-core-stack.md).

> ### 🧠 Philosophy: Boring Infrastructure, Flexible Workloads
> Foundational services should feel appliance-like: stable, predictable, and rarely changed.
> Workloads should be easy to iterate on, rebuild, and replace without threatening access to the lab.

---

## Table of contents
- [VM Inventory (At a Glance)](#-vm-inventory-at-a-glance)
- [VMID Naming & Numbering Scheme (Proxmox)](#vmid-naming--numbering-scheme-proxmox)
- [What Runs Where (Quick Reference)](#-what-runs-where-quick-reference)
- [VM-by-VM: The Boundary Rules](#vm-by-vm-the-boundary-rules-the-important-part)
- [Universal Sidecar Pattern](#a-small-preview-the-universal-sidecar-pattern)
- [The Practical Step: Spinning Up the VMs](#the-practical-step-spinning-up-the-vms-from-the-template)
  - [Per-VM quick reference (VMID + resources)](#per-vm-quick-reference-vmid--resources)
  - [Disk and storage (default 32GB, when to increase)](#disk-and-storage-default-32gb-when-to-increase)
  - [Clone steps (repeat per VM)](#clone-steps-repeat-per-vm)
- [When to add a new VM](#when-to-add-a-new-vm)
- [FAQ](#-frequently-asked-questions)

---

## 🧱 VM Inventory (At a Glance)

I’m not doing “one VM per app”, and I’m not doing “one giant Docker VM”.
Instead, I’m separating by **failure domain** and **type of complexity**.

| VM | What it owns | Why it exists |
|----|--------------|---------------|
| `core` | access + identity + naming | keep the front door stable |
| `monitoring` | health + visibility + operations | observability evolves constantly |
| `apps` | general user apps | keep convenience apps out of `core` |
| `media` | download + automation pipeline | contain higher-churn workflows |
| `accelerated` | GPU / transcoding workloads | isolate passthrough + driver complexity |

> ### 🧠 Design Note: Short Hostnames Are Intentional
> VM names become hostnames (SSH, dashboards, Avahi fallback).
> I’m optimizing for names that are easy to type and remember when something breaks.

---

## VMID Naming & Numbering Scheme (Proxmox)

I use a simple VMID range scheme so the Proxmox UI stays readable over time.

**Goals**
- Instantly see “what kind of VM this is” from the VMID
- Leave room to grow without renumbering
- Keep templates and throwaways clearly separated

**Ranges**
- **9000–9099** → Templates (Cloud-Init base images)
- **100–199** → Core infrastructure VMs (access plane + foundational services)
- **200–299** → Workload VMs (apps, media, GPU workloads)
- **800–899** → Temporary / experiments / throwaway VMs

**Current mapping**
| VM | VMID | Notes |
|----|------|------|
| `debian-13-docker-cloudinit` | 9000 | Cloud-Init Docker host template (do not run directly) |
| `core` | 110 | reverse proxy, SSO, DNS |
| `monitoring` | 120 | Grafana, Uptime Kuma, etc. |
| `apps` | 210 | general user apps |
| `media` | 220 | *arr + download pipeline |
| `accelerated` | 230 | GPU / transcoding / CV workloads |

> **Spacing note:** I use increments of 10 (110, 120, 130…) so I can insert new VMs later without renumbering.

---

## 🧩 What Runs Where (Quick Reference)

This section is intentionally compact: **what the app is + what it contributes**.
Full app-by-app reasoning belongs in the Chapter 2X files (2a, 2b, 2c, 2d).

### `core` — Access & Naming Foundation

| App | What it is | What it gives the lab |
|-----|------------|------------------------|
| Reverse Proxy | the traffic router | a single front door for all services |
| ACME / Let’s Encrypt | certificate automation | HTTPS everywhere without manual cert work |
| SSO (Authentik / Authelia) | identity layer | one login across many apps |
| DNS (internal) | local name resolution | stable hostnames and clean routing |

### `monitoring` — Observability & Operations

| App | What it is | What it gives the lab |
|-----|------------|------------------------|
| Grafana | dashboards | a unified view of metrics/logs over time |
| Uptime Kuma | uptime checks | “is it up?” + practical alerting |
| Komodo | stack management UI | centralized visibility & basic control |

### `apps` — General-purpose Applications

| App | What it is | What it gives the lab |
|-----|------------|------------------------|
| Homepage / Homarr / Dashy | dashboard / launcher | friendly landing page + service index (e.g. [gethomepage.dev](https://gethomepage.dev), Homarr, Dashy) |
| Mealie | recipes & meal planning | a real “daily-use” app that benefits from self-hosting |
| (more later) | misc apps | a clean home for non-infra services |

### `media` — Automation & Acquisition

| App | What it is | What it gives the lab |
|-----|------------|------------------------|
| Sonarr / Radarr / etc. | automation managers | hands-off acquisition + organization |
| qBittorrent | download client | moves content into the pipeline |
| VPN (for qBittorrent) | network isolation | download traffic routed safely |
| FlareSolverr | anti-bot helper | keeps indexers working when they get annoying |

### `accelerated` — GPU Workloads

| App | What it is | What it gives the lab |
|-----|------------|------------------------|
| Plex | media server | playback + optional transcoding |
| Immich | photo/video platform | personal photo/video cloud with acceleration support |

---

## VM-by-VM: The Boundary Rules (The Important Part)

This is the heart of the chapter: **what separates each VM from the others**.

### `core` — The Front Door (Infrastructure, Not “Apps”)

`core` exists to keep three things stable:
- **Access** (reverse proxy)
- **Identity** (SSO)
- **Naming** (DNS)

The boundary is simple:
> If it’s a foundational access primitive, it belongs in `core`.  
> If it’s a workload, it does not.

> ### 🧠 Design Intent: Keep DNS Low-Churn
> DNS lives in `core` because it is part of the access foundation — but it is deliberately designed to be **boring**.
> The goal is not to “perfectly model the network,” but to avoid turning DNS into a recurring maintenance task.
>
> In practice, the stable pattern is:
> - DNS stays steady
> - per-service changes happen in the reverse proxy layer (which I must touch anyway)

---

### `monitoring` — Visibility That Never Becomes a Dependency

Monitoring is where iteration happens:
dashboards evolve, retention gets tuned, exporters change, alerts get refined.

So it gets its own VM.

The rule:
> If `monitoring` is down, I should still be able to access the lab normally.

This keeps observability work from turning into an access prerequisite.

---

### `apps` — The “Pressure Valve” That Keeps `core` Clean

If you don’t define a place for general apps, they drift into infrastructure.

`apps` exists so that:
- `core` doesn’t become “core + 20 random containers”
- user-facing conveniences have a coherent home
- the architecture stays understandable over time

> ### 🧠 Clarification: “But the homepage is where users land…”
> True — and that’s exactly why `core` should not host it.
> `core` is the access layer; `apps` is where user-facing experiences live.

---

### `media` — Containing the Highest-Churn Workflows

The media pipeline is internet-adjacent and naturally chaotic:
indexers break, download automation misbehaves, and workflows evolve.

So it is intentionally isolated.

A hard rule applies:
- download clients (e.g., qBittorrent) are never exposed directly to the internet

Even if some *arr endpoints are reachable, the download client stays internal.

---

### `accelerated` — One Place for Hardware Complexity

Anything that needs GPU acceleration lives in `accelerated`.

This VM exists to isolate:
- passthrough configuration
- drivers
- transcoding behavior
- hardware-specific debugging

> ### 🧠 Practical Constraint: Passthrough is Usually “One VM”
> In a typical Proxmox setup, GPU passthrough is easiest and most stable when it is owned by **a single VM**.
> If I don’t isolate it this way, the alternative is usually worse:
> - running GPU workloads directly on the host, or
> - fighting complex multi-tenant GPU setups early in the journey
>
> `accelerated` keeps the hardware boundary explicit and the blast radius small.

---

## A Small Preview: The “Universal Sidecar” Pattern

Across multiple VMs, certain supporting containers repeat:
- monitoring helpers
- management helpers
- “glue” containers that make stacks easier to operate

Rather than inventing a new pattern per VM, I’m going to treat these as **universal sidecars**:
- consistent structure
- consistent labels
- predictable behavior across VMs

This chapter introduces the concept only.
The full Compose strategy and a universal snippet live in **Chapter 3** *(planned)*. For the core stack in detail — `.env`, compose, bootstrap, and deployment — see [Chapter 3A (core stack)](Chapter3a-core-stack.md).

---

## The Practical Step: Spinning Up the VMs (From the Template)

[Chapter 1](Chapter1-proxmox.md) builds the Cloud-Init Docker template.  
This chapter is where we actually turn that template into real VMs.

> ### ✅ The One To-Do in This Chapter
> **Create each VM from template `9000`** using the VMIDs and steps below.  
> This is the only hands-on procedure in Chapter 2. Once the VMs exist, the subchapters (2A, 2C, …) cover what to run *inside* each one.

### Per-VM quick reference (VMID + resources)

Use this table when cloning. Each row gives the exact VMID, name, and resources; subchapters add VM-specific setup *after* the VM is provisioned.

| VM | VMID | Clone as (name) | vCPU | RAM | Disk (default) | After cloning |
|----|------|-----------------|------|-----|----------------|----------------|
| `core` | 110 | `110 core` | 2 | 4GB | 32GB | [Chapter 2A (core)](Chapter2a-core.md#provisioning-the-core-vm-from-the-template) |
| `monitoring` | 120 | `120 monitoring` | 2 | 6GB | 32GB | *(Chapter 2B planned)* |
| `apps` | 210 | `210 apps` | 2 | 4GB | 32GB | — |
| `media` | 220 | `220 media` | 4 | 8GB | 32GB | [Chapter 2C (media)](Chapter2c-media.md) |
| `accelerated` | 230 | `230 accelerated` | 4 | 8GB | 32GB | *(Chapter 2D planned)* |

### Starting resource allocation (reference)

These are intentionally “good defaults”, not permanent decisions.

| VM (VMID) | vCPU | RAM | Why this is my starting point |
|-----------|------|-----|------------------------------|
| `core` (110) | 2 | 4GB | Access + identity should feel responsive and stable |
| `monitoring` (120) | 2 | 6GB | Observability stacks grow and benefit from memory |
| `apps` (210) | 2 | 4GB | General apps are moderate footprint |
| `media` (220) | 4 | 8GB | Higher churn + heavier pipeline services |
| `accelerated` (230) | 4 | 8GB | GPU workloads and related services like RAM |

> Disk stays “small OS disk” by default so snapshots/backups are fast. Data gets mounted/attached intentionally later.

### Disk and storage (default 32GB, when to increase)

Cloned VMs inherit the template's **32GB** system disk. That is intentional: a small OS disk keeps snapshots and backups fast; bulk data lives on **mounts** (NFS or extra virtual disks) as described in each VM's subchapter.

**Where to change disk size or attach storage (Proxmox):**

- **Resize the existing disk:** VM → **Hardware** → select **scsi0** → **Resize** → enter new size (e.g. `64G`). Then inside the VM, extend the partition and filesystem (e.g. `growpart` + `resize2fs` or use a live GParted).
- **Add a second disk:** VM → **Hardware** → **Add** → **Hard Disk** → choose size and storage. Then inside the VM, partition, format, and mount it (e.g. at `/mnt/media`). Subchapters (e.g. [Chapter 2C — Storage Design](Chapter2c-media.md#storage-design)) describe where each VM expects data mounts.

**When to give a VM more than 32GB:**

| VM | Default | Consider more if… |
|----|---------|--------------------|
| `core` | 32GB | Rarely. Proxy, SSO, DNS, and certs are small. |
| `monitoring` | 32GB | Metrics/log retention grows; 32GB is usually enough unless you keep years of data locally. |
| `apps` | 32GB | Usually enough. Add a disk or resize if you host large app data on the VM instead of mounts. |
| `media` | 32GB | If you use **local** storage for downloads/library (not NFS), add a second disk and mount it; see [Chapter 2C — Storage Design](Chapter2c-media.md#storage-design). |
| `accelerated` | 32GB | Transcoding temp and thumbnails can use space; 64GB or a second disk is reasonable if you see low-disk warnings. |

> Disk stays "small OS disk" by default so snapshots/backups are fast. Data gets mounted/attached intentionally later.

### Clone steps (repeat per VM)

Follow these steps for **each** VM, using the VMID and name from the Per-VM table above.

1. **Clone Template `9000`**
   - Right-click template → **Clone**
   - VMID/name: from table (e.g. `110 core`, `220 media`)

2. **Set CPU/RAM**
   - VM → **Hardware** → set cores + memory from the table above

3. **Cloud-Init sanity**
   - Ensure your SSH key is present in Cloud-Init
   - Keep DHCP for now (the architecture relies on DNS names, not static addressing)

4. **Boot the VM and wait for first-boot setup**
   - After the VM boots, Cloud-Init and any bootstrap scripts (e.g. adding your user to the `docker` group, cloning the self-hosted repo) can take a minute or two.
   - **Recommendation:** wait 1–2 minutes before logging in or running verify. If you log in too soon, your user might not yet be in the `docker` group and the repo may not be present yet.

5. **Verify + snapshot**
   - Run:
   ```bash
   docker --version && systemctl status qemu-guest-agent --no-pager && free -h
   ```
   - Take a snapshot once the VM is healthy and reachable; this becomes your clean rollback anchor before you start deploying stacks.

**Next:** Use the "After cloning" column in the Per-VM table to jump to the subchapter for that VM (stack setup, bootstrap, etc.).

---

## When to add a new VM

Consider adding a new VM when:

- A workload's **failure domain** no longer fits an existing VM (e.g. its failures or restarts would impact others).
- A workload's **resource profile** (CPU, RAM, GPU, or I/O) justifies isolation.
- A distinct **security or compliance boundary** is needed (e.g. a dedicated "data" or "DMZ" VM).
- The existing VM is becoming a grab-bag of unrelated services and the mental model is eroding.

The VMID ranges (100–199 for core, 200–299 for workloads) leave room to insert new VMs without renumbering.

---

## ❓ Frequently Asked Questions

**Q: If `core` is “boring and stable”, how do you avoid turning it into a constant-edit VM?**  
*A:* The goal is to minimize *types* of changes. `core` changes are constrained to access primitives (proxy routes, auth policy, DNS basics), and everything else lives elsewhere. Even when routes grow, the VM remains boring because it runs only foundational services, backed by snapshots and config-in-Git discipline.*

**Q: Why keep GPU apps in a dedicated VM instead of spreading them across the lab?**  
*A:* Because passthrough is typically easiest (and most stable) when a GPU is assigned to **one VM**. Without that, you usually end up either running GPU workloads on the Proxmox host or dealing with advanced GPU sharing early. A dedicated `accelerated` VM keeps hardware complexity contained and makes the “GPU boundary” explicit.*

**Q: Why is `apps` a separate VM instead of just putting these containers wherever there’s space?**  
*A:* Because “space-based placement” erodes architecture over time. `apps` is a deliberate home for general services, which keeps `core` clean and preserves clear boundaries as the lab grows.*

**Q: What happens if you outgrow this split?**  
*A:* The split is meant to be evolvable. The boundaries make it easier to add new VMs later (e.g., a dedicated “data” VM, or splitting DNS out) without rewriting the mental model of the lab.*

---
