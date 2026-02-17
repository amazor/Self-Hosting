# Deploy Guide — How to Use This Repo to Run the Stack

This guide explains how to use the repo to **deploy** the homelab. It does not cover application **UI configuration** (Caddy routes, Sonarr/Radarr, quality profiles, etc.); that lives in the chapter docs ([Chapter 3A](docs/Chapter3a-core-stack.md) for core, [Chapter 3c](docs/Chapter3c-media-stack.md) for media). After deploy, you must perform app-specific UI setup — see those chapters for what to do in each app’s UI.

For the big picture (mission, principles, VM layout), see the [README](README.md) and [Chapter 2: VM Architecture](docs/Chapter2-vms.md).

---

## Two ways to run

**You don’t need Proxmox.** You can run everything on a **single host**: clone this repo to `/opt/self-hosting`, create `.env` from the `.env.example` in each stack directory you care about (see the example files and chapter docs for what each variable does), then run `./deploy.sh all`. That deploys all stacks and creates shell helpers (e.g. `media`, `core`) on one machine. Use `--default <stack>` if you want the generic `stack` command to target a specific stack.

**The rest of this guide** describes the **usual flow**: Proxmox + one VM per role. If you followed that path, parts 1 and 2 give you the template and VMs; part 3 below is the concrete per-VM deploy step (with snippets). If you’re on a single host, the “all at once” approach above is enough; you can still use the `.env` and UI-config docs referenced below.

---

## Prerequisites (Proxmox flow)

- **Proxmox VE** and a **Cloud-Init Docker template** (VMID `9000`) as built in [Chapter 1](docs/Chapter1-proxmox.md).
- **VMs created from that template** (e.g. `110 core`, `220 media`) as in [Chapter 2](docs/Chapter2-vms.md). The repo is at `/opt/self-hosting` on each VM (via Cloud-Init or manual clone).

---

## The Workflow (Proxmox flow)

### 1) Build the factory (template)

Follow **[Chapter 1](docs/Chapter1-proxmox.md)** to create the Cloud-Init Docker template (VMID `9000`).

### 2) Clone real VMs from the template

Follow **[Chapter 2](docs/Chapter2-vms.md)** to clone and size VMs (e.g., `110 core`, `120 monitoring`, etc.).

### 3) Role-specific first run inside each VM (deploy + bootstrap)

If you followed parts 1 and 2, each VM has the repo at `/opt/self-hosting`. On **each VM**, run deploy for **that VM’s stack only** (e.g. on the media VM run `./deploy.sh media`). Deploy creates a **symlink** in your home directory (e.g. `~/media`) and **shell helpers** (`media`, `core`, …). You don’t need to work from `/opt/self-hosting` after that — use the symlinked directory as your default user (e.g. `cd ~/media`).

**.env** is documented in each stack’s `.env.example` and in the chapter docs (Chapter 3A, 3c). Create `.env` from `.env.example` in the stack directory; deploy does **not** copy it for you.

**UI config** is required after deploy: proxy routes, *arr settings, quality profiles, etc. See the **specific docs** for what to do in each app’s UI ([Chapter 3A](docs/Chapter3a-core-stack.md) for core, [Chapter 3c](docs/Chapter3c-media-stack.md) for media).

**Example (media VM):**

```bash
cd /opt/self-hosting
cp docker_compose/media/.env.example docker_compose/media/.env
# Edit docker_compose/media/.env (see .env.example and Chapter 3c for options)
./deploy.sh media
source ~/.bashrc   # or open a new shell
cd ~/media         # work from the symlink as your default user
media up -d
```

Then do the **UI configuration** for Sonarr, Radarr, Prowlarr, qBittorrent, etc. as described in [Chapter 3c](docs/Chapter3c-media-stack.md).

Bootstrap (invoked by deploy) handles VM-specific setup: optional NFS mounts, config dirs, validation. Full deploy script usage: `./deploy.sh --help`.

✅ Template stays boring.  
✅ Per-VM provisioning stays explicit and reproducible.
