# Deploy Guide — How to Use This Repo to Run the Stack

This guide explains how to use the repo to **deploy** the homelab. It does not cover application **UI configuration** (Caddy routes, Sonarr/Radarr, quality profiles, etc.); that lives in the chapter docs ([Chapter 3A](docs/Chapter3a-core-stack.md) for core, [Chapter 3c](docs/Chapter3c-media-stack.md) for media). After deploy, you must perform app-specific UI setup — see those chapters for what to do in each app’s UI.

For the big picture (mission, principles, VM layout), see the [README](README.md) and [Chapter 2: VM Architecture](docs/Chapter2-vms.md).

---

## Two ways to run

**You don’t need Proxmox.** You can run everything on a **single host**: clone this repo to `/opt/self-hosting`, create `.env` from the `.env.example` in each stack directory you care about (see the example files and chapter docs for what each variable does), then run `python3 deploy.py all`. That deploys all stacks and creates shell helpers (e.g. `media`, `core`) on one machine. Use `--default <stack>` if you want the generic `stack` command to target a specific stack.

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

If you followed parts 1 and 2, each VM has the repo at `/opt/self-hosting`. On **each VM**, run deploy for **that VM’s stack only** (e.g. on the media VM run `python3 deploy.py media`). Deploy creates a **symlink** in your home directory (e.g. `~/media`) and **shell helpers** (`media`, `core`, …). You don’t need to work from `/opt/self-hosting` after that — use the symlinked directory as your default user (e.g. `cd ~/media`).

**.env** is documented in each stack’s `.env.example` and in the chapter docs (Chapter 3A, 3c). Create `.env` from `.env.example` in the stack directory; deploy does **not** copy it for you (unless you pass `--init-env`; you still need to fill required vars).

**UI config** is required after deploy: proxy routes, *arr settings, quality profiles, etc. **Exception: core stack SSO** — if `AUTHENTIK_BOOTSTRAP_TOKEN` is set in `.env`, deploy automatically applies the Authentik blueprint via API after starting core (creates proxy providers, applications, and outpost assignment from `:sso` entries in `CADDY_EXTRA_SERVICES`; no manual GUI setup needed). See [Chapter 3A — Authentik](docs/Chapter3a-core-stack.md#authentik) for details. **Media stack automation:** After starting the media stack, deploy automatically adds Prowlarr indexers (13 public torrent sites with FlareSolverr proxy), configures qBittorrent categories and settings, sets up Sonarr/Radarr (root folders, download clients, TRaSH naming) and Prowlarr app sync via `setup_media_apps.py`, then triggers a Recyclarr sync (TRaSH quality profiles including anime). Only VPN credentials need manual setup in `.env`. See [Chapter 3C](docs/Chapter3c-media-stack.md) for details. For other UI config, see the **specific docs** for what to do in each app’s UI ([Chapter 3A](docs/Chapter3a-core-stack.md) for core, [Chapter 3c](docs/Chapter3c-media-stack.md) for media).

**Example (media VM):**

```bash
cd /opt/self-hosting
cp docker_compose/media/.env.example docker_compose/media/.env
# Edit docker_compose/media/.env (see .env.example and Chapter 3c for options)
python3 deploy.py media
source ~/.bashrc   # or open a new shell
cd ~/media         # work from the symlink as your default user
media up -d
```

Then do the **UI configuration** for Sonarr, Radarr, Prowlarr, qBittorrent, etc. as described in [Chapter 3c](docs/Chapter3c-media-stack.md).

Bootstrap (invoked by deploy) handles VM-specific setup: optional NFS mounts, config dirs, validation. Full deploy script usage: `python3 deploy.py --help`.

✅ Template stays boring.  
✅ Per-VM provisioning stays explicit and reproducible.

---

## First-time setup (after a fresh clone)

After cloning the repo on a new machine or VM, an optional setup script can stage auto-detectable values (e.g. generate `AUTHENTIK_SECRET_KEY`, detect `DNS_BIND_IP`, set `PUID`/`PGID`) into a `docker_compose/<stack>/.env.staged` file per stack. `.env.example` itself is never modified — it stays a plain, secret-free template. This saves manual work while keeping "verify config before use" as the default flow.

```bash
# 1. Stage auto-detected values into .env.staged for each stack
python3 scripts/setup_env.py
# If .env doesn't exist yet, this offers to copy .env.staged to .env for you
# (interactively only). Otherwise, copy it yourself once you've reviewed it:
cp docker_compose/core/.env.staged docker_compose/core/.env
cp docker_compose/media/.env.staged docker_compose/media/.env

# 2. Review and fill remaining required vars (secrets, domain, etc.)
#    See .env.example comments and chapter docs for what each variable does.

# 3. Deploy
python3 deploy.py core          # or: python3 deploy.py all
```

**Convenience:** Pass `--init-env` to have deploy copy to `.env` automatically when `.env` is missing — it prefers `.env.staged` (if `setup_env.py` was run) and falls back to the bare `.env.example` template otherwise. Required vars must still be filled — validation is unchanged even with `--force`.

---

## Observability sidecars

Every VM stack includes an observability overlay (`compose.observability.yml`) that runs **node_exporter**, **cAdvisor**, and **Alloy** as sidecars. The overlay is a symlink to `docker_compose/common/compose.observability.yml` — one canonical file, shared across all stacks.

Enabled by default via `ENABLE_OBSERVABILITY=1` in `.env`. Set to `0` to skip sidecars (e.g. if you don't have a monitoring VM). Required env vars when enabled:

| Variable | What it does |
|----------|-------------|
| **LOKI_URL** | Where Alloy pushes logs (e.g. `http://192.168.1.120:3100`). |
| **PROMETHEUS_URL** | Where Alloy pushes self-metrics (e.g. `http://192.168.1.120:9090`). |
| **DOCKER_GID** | Host `docker` group GID so Alloy can read the Docker socket. Pre-filled by `setup_env.py`. |

On the monitoring VM, set these to `http://host.docker.internal:3100` and `http://host.docker.internal:9090` (Loki and Prometheus are local). On other VMs, use the monitoring VM's LAN IP.

Bootstrap generates `config.alloy` with the correct endpoint URLs and identity labels (from **VM_HOSTNAME**, **VM_ROLE**, **PROXMOX_NODE** — all auto-detected with sensible defaults).

---

## `--force` flag

`--force` skips overridable validation — useful for **local testing only**:

- **deploy.py** — Skips `.env` placeholder checks (e.g. unfilled `LOKI_URL`, weak Grafana password). The stack deploys but sidecars may fail to connect to endpoints.
- **bootstrap.py** — Skips stack-specific guardrails (e.g. Grafana password strength, VPN config). Config generation still runs normally.

Sidecars (Alloy, node_exporter, cAdvisor) are resilient: they start and retry connections. With `--force`, a missing monitoring VM means Alloy logs retry warnings — no crashes, no data loss on the VM itself. This makes `--force` safe for offline/single-VM testing.
