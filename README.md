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
- **Automation:** Cloud-Init + repo-driven bootstrap/deploy scripts (described in Chapter 3)

### Application stack (at a glance)

| VM | Core apps | Optional apps |
|----|-----------|----------------|
| `core` | Caddy, Authentik, dnsmasq, whoami | ddclient |
| `monitoring` | Grafana, Uptime Kuma, Komodo | — |
| `apps` | Homepage / Homarr / Dashy, Mealie | — |
| `media` | Sonarr, Radarr, Prowlarr, qBittorrent, VPN container, FlareSolverr | Buildarr, Recyclarr, Cleanuparr, SABnzbd, Bazarr, ntfy |
| `accelerated` | Plex, Immich | — |

Full list, tiers, and reasoning: [Chapter 2 — What runs where](docs/Chapter2-vms.md#apps-at-a-glance).

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
  **Configuration reference:** [TRaSH Guides](https://trash-guides.info/) are the recommended source for Radarr, Sonarr, Prowlarr, Bazarr, Plex, downloaders, and file/folder structure. Cursor rules in `.cursor/rules/trash-guides-*.mdc` embed this reference for `docker_compose/media/`, `docker_compose/accelerated/`, and `docs/Chapter2c-media.md`.

- **[Chapter 3A: Core Stack](docs/Chapter3a-core-stack.md)**  
  Core VM stack: `.env`, compose, bootstrap, Caddyfile generation, and deploy (`python3 deploy.py core`).

> **Chapter 3 (WIP):** Full deploy design — deploy script (`python3 deploy.py`), bootstrap, per-stack `.env`, and shell helpers (`media`, `stack`). Upcoming chapters will cover Docker Compose workflow, storage mounts (NFS), and the per-VM bootstrap approach in detail.

---

## How to Run This Stack

To run this stack: build the template ([Chapter 1](docs/Chapter1-proxmox.md)), clone VMs ([Chapter 2](docs/Chapter2-vms.md)), then deploy and bootstrap per VM. **Step-by-step: [Deploy guide](DEPLOY.md).**

---

## Repository Structure (Current + Planned)

This repo is intentionally split between:
- **docs/** (the journey + reasoning)
- **proxmox/** (hypervisor/template automation)
- **docker_compose/** (per-VM stacks + bootstrap; core and media are documented in Chapter 3A and Chapter 2C)

```text
.
├── docs/
│   ├── Chapter0-hardware.md
│   ├── Chapter1-proxmox.md
│   ├── Chapter2-vms.md
│   ├── Chapter2a-core.md
│   ├── Chapter2c-media.md
│   ├── Chapter3a-core-stack.md
│   └── ... (more chapters as the journey continues)
│
├── proxmox/
│   ├── scripts/
│   │   └── ... (template creation, post-install helpers, etc.)
│   └── snippets/
│       └── ... (Cloud-Init snippets / common config)
│
├── deploy.py                            # stack deploy: python3 deploy.py core | media | ...
├── docker_compose/                     # per-VM stacks (core, media, etc.)
│   ├── core/
│   │   ├── compose.yml
│   │   ├── .env.example
│   │   └── bootstrap.py                # role provisioner: mounts, helpers, validation
│   ├── monitoring/
│   │   ├── compose.yml
│   │   ├── .env.example
│   │   └── bootstrap.py
│   ├── apps/
│   │   ├── compose.yml
│   │   ├── .env.example
│   │   └── bootstrap.py
│   ├── media/
│   │   ├── compose.yml
│   │   ├── .env.example
│   │   └── bootstrap.py
│   └── accelerated/
│       ├── compose.yml
│       ├── .env.example
│       └── bootstrap.py
│
├── DEPLOY.md                          # how to use the repo: template → clone → deploy
└── README.md
```
## Roadmap (Short)

✅ Hardware foundation (Chapter 0)
✅ Proxmox + template (Chapter 1)
✅ VM architecture (Chapter 2)
✅ Core VM design (Chapter 2A)
✅ Media VM design (Chapter 2C)
✅ Core stack deploy (Chapter 3A: .env, compose, bootstrap, deploy.py)
🔜 Full Docker Compose workflow doc (Chapter 3) + bootstrap scripts
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
