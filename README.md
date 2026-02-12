# 🏠 The Homelab Journey: From Bare Metal to Production

Welcome! This repository is my living **field manual** and **source of truth** for building a self-hosted homelab.

It’s intentionally:
- **Part journal** (what I was thinking at the time)
- **Part technical guide** (how to reproduce it)
- **Part Infrastructure-as-Code** (configs/scripts that actually run)

> A homelab is never “done” — it just reaches a stable state before the next upgrade.

---

## The Mission

Build a robust, scalable, and automated home server environment that can host:
- core infra (ingress, auth, DNS)
- monitoring/observability
- media automation pipelines
- general apps
- GPU workloads (transcoding/CV)

### Guiding principles

- **Boring Core, Flexible Workloads**  
  The access plane should be stable and predictable. Workloads can churn and be rebuilt.

- **Cattle, Not Pets**  
  VMs are disposable. If something breaks, I redeploy from a known baseline instead of “snowflake fixing.”

- **Decoupled Compute and Data**  
  Proxmox provides compute; the NAS provides storage. Data survives VM rebuilds.

- **Documentation-first**  
  Decisions include “why” notes, so future-me (and readers) can follow the logic.

---

## The Tech Stack

- **Hypervisor:** Proxmox VE
- **Compute host:** Beelink EQi13 (Intel i5-13500H, 32GB RAM)
- **Storage:** Synology NAS
- **VM OS baseline:** Debian (Cloud-Init template, Docker host)
- **Workloads:** Docker Compose (per-VM stacks)
- **Automation:** Cloud-Init + repo-driven bootstrap scripts (described in Chapter 3)

---

## Where to Start (The Chapters)

This journey is written as chronological chapters.

- **[Chapter 0: Hardware Foundation](docs/Chapter0-hardware.md)**  
  The physical build: why this hardware, what tradeoffs, what it enables.

- **[Chapter 1: Proxmox Foundation](docs/Chapter1-proxmox.md)**  
  Installing Proxmox and building a **Cloud-Init Docker template** (the “golden image”).

- **[Chapter 2: VM Architecture](docs/Chapter2-vms.md)**  
  VM boundaries, philosophy, and how the lab is separated (including VMID scheme).

- **[Chapter 2A: Core VM](docs/Chapter2a-core.md)**  
  The access plane: reverse proxy, HTTPS, SSO, DNS.

- **[Chapter 2C: Media VM](docs/Chapter2c-media.md)**  
  Media automation pipeline: *arr stack, qBittorrent, VPN, storage design, optional layers.

> Upcoming chapters will cover Docker Compose workflow, storage mounts (NFS), and the per-VM bootstrap approach in detail.

---

## The Workflow (How this repo is meant to be used)

### 1) Build the factory (template)
Follow **Chapter 1** to create a Cloud-Init Docker template (VMID `9000`).

### 2) Clone real VMs from the template
Follow **Chapter 2** to clone and size VMs (e.g., `110 core`, `120 monitoring`, etc.).

### 3) Role-specific “first run” setup inside each VM (bootstrap)
I keep the template generic. Anything role-specific is done *per VM*.

The intended first-time flow inside a VM is:

1. `git clone` this repo
2. `cd docker_compose/<vm>/`
3. run `./bootstrap.sh`

The bootstrap script is where VM-specific setup happens, such as:
- optional **NFS mounts** (scoped per VM — never mount “the whole NAS” everywhere)
- local quality-of-life helpers (bash functions for quick deploy/update/logs)
- environment file initialization and validation
- optional “bring the stack up” steps

✅ The template stays boring.  
✅ The VM role provisioning stays explicit and reproducible.

> Full bootstrap design and the Compose workflow live in the Docker/Compose chapter (planned).

---

## Important Architecture Notes (High-level)

### VM boundaries include storage boundaries
Separation isn’t only about containers and networking — it’s also about **what data each VM can see**.

Rule of thumb:
- each VM mounts only what it needs
- mounts are scoped to a subfolder/export
- `core` stays minimal and typically mounts nothing

### Only one VM is public
The router forwards **only ports 80/443** to the `core` VM.
Everything else stays private and is reachable through the reverse proxy (and admin access paths like Tailscale).

---

## Repository Structure (Current + Planned)

This repo is intentionally split between:
- **docs/** (the journey + reasoning)
- **proxmox/** (hypervisor/template automation)
- **docker_compose/** (per-VM stacks + bootstrap) *(planned / coming next)*

```text
.
├── docs/
│   ├── Chapter0-hardware.md
│   ├── Chapter1-proxmox.md
│   ├── Chapter2-vms.md
│   ├── Chapter2a-core.md
│   └── ... (more chapters as the journey continues)
│
├── proxmox/
│   ├── scripts/
│   │   └── ... (template creation, post-install helpers, etc.)
│   └── snippets/
│       └── ... (Cloud-Init snippets / common config)
│
├── docker_compose/                     # (planned / introduced in the Compose chapter)
│   ├── core/
│   │   ├── compose.yml
│   │   ├── .env.example
│   │   └── bootstrap.sh                # role provisioner: mounts, helpers, validation
│   ├── monitoring/
│   │   ├── compose.yml
│   │   ├── .env.example
│   │   └── bootstrap.sh
│   ├── apps/
│   │   ├── compose.yml
│   │   ├── .env.example
│   │   └── bootstrap.sh
│   ├── media/
│   │   ├── compose.yml
│   │   ├── .env.example
│   │   └── bootstrap.sh
│   └── accelerated/
│       ├── compose.yml
│       ├── .env.example
│       └── bootstrap.sh
│
└── README.md
```
## Roadmap (Short)

✅ Hardware foundation (Chapter 0)
✅ Proxmox + template (Chapter 1)
✅ VM architecture (Chapter 2)
✅ Core VM design (Chapter 2A)
✅ Media VM design (Chapter 2C)
🔜 Docker Compose workflow + bootstrap scripts
🔜 Storage strategy (NFS mounts, permissions, boundaries)
🔜 Monitoring (Chapter 2B), accelerated workloads (Chapter 2D)

## 🔮 The Future
This lab is a work in progress. Future expansions include:
* **3D Printed Rack:** Moving from the desk to a custom-printed 10-inch server rack.
* **Managed Networking:** Implementing an Omada or UniFi switch for full VLAN segmentation.
* **High Availability:** Potentially adding a second Beelink node for a Proxmox cluster.
* **UPS + Graceful Shutdown:** Battery backup + automated shutdown (especially for NAS + Proxmox).
* **3-2-1 Backups:** Encrypted local + offsite backups (and a tested restore path).
* **Security VM:** A dedicated security/host-insight layer (e.g., Wazuh) kept out of the `core` access plane.

---

> "A homelab is never finished; it just reaches a stable state before the next upgrade."
