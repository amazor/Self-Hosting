# Chapter 3D — Accelerated Stack: Configuration and Deployment

## Introduction

**Prerequisites:** [Chapter 2D (Accelerated VM)](Chapter2d-accelerated.md) (why this VM exists, storage and GPU design), [Chapter 2](Chapter2-vms.md) (VM overview).

Chapter 2D explains the **shape** of the Accelerated VM: one place for GPU complexity, Plex and Immich as core apps, and how it relates to the Media VM and NAS exports.

This chapter is the **operational guide**:

- What is inside `docker_compose/accelerated/`  
- How `.env` is structured  
- How Plex and Immich are wired (ports, volumes, GPU)  
- How the bootstrap script keeps guardrails in place  
- How to deploy the stack manually or via `deploy.py`  

> ### 🧠 Philosophy: Library-First, Hardware-Assisted
> The goal is a stable playback and photo platform built on top of a well-structured library.
> Hardware acceleration is an optimization — useful, but not a prerequisite for correctness.

---

## Table of contents

- [What's in `docker_compose/accelerated/`](#whats-in-docker_composeaccelerated)
- [Environment: `.env.example`](#environment-envexample)
- [Compose file: Notable details](#compose-file-notable-details)
- [Bootstrap script: What it does](#bootstrap-script-what-it-does)
- [Deploying the accelerated stack](#deploying-the-accelerated-stack)
  - [Path 1: Manual (on the Accelerated VM)](#path-1-manual-on-the-accelerated-vm)
  - [Path 2: Repo deploy script (`deploy.py`)](#path-2-repo-deploy-script-deploypy)
- [After first run](#after-first-run)
- [Verification and troubleshooting](#verification-and-troubleshooting)
- [See also](#see-also)

---

## What's in `docker_compose/accelerated/`

The Accelerated stack follows the same “one stack, one directory” pattern as `core` and `media`.

| File or script | Purpose |
|----------------|---------|
| **compose.yml** | Stack definition: Plex, Immich services (server, microservices, machine learning), Postgres, Redis. |
| **.env.example** | Template for required and optional env vars (no secrets; copy to `.env` and fill). Defines paths, IDs, image tags, GPU and observability toggles. |
| **bootstrap.py** | Idempotent first-run: validates `.env`, checks mounts, runs GPU guardrails, creates config directories, wires observability, validates compose, and optionally brings up the stack. |
| **compose.observability.yml** | Symlink to `docker_compose/common/compose.observability.yml`. Adds node_exporter, cAdvisor, and Alloy sidecars when `ENABLE_OBSERVABILITY=1`. |

All paths in the stack are relative to the directory where you run `docker compose`
(typically `docker_compose/accelerated` or a symlink like `~/accelerated`).
Run bootstrap from that same directory so generated config and state land in the right place.

---

## Environment: `.env.example`

Copy `.env.example` to `.env` and edit it before first deploy:

```bash
cd docker_compose/accelerated
cp .env.example .env
```

### Paths and identity

| Variable | Purpose |
|----------|---------|
| **MEDIA_LIBRARY_ROOT** | Host path for the final media library exported from the Media VM. Default: `/mnt/media/library`. Mounted read-only into Plex as `/data/library`. |
| **IMMICH_UPLOAD_ROOT** | Host path for Immich originals and derived assets. Default: `/mnt/photos/library`. Mounted into Immich as `/photos/library`. |
| **IMMICH_DB_ROOT** | Host path for Immich Postgres data. Default: `./config/immich-postgres` (VM-local disk). **Must not be on a network share** (per Immich docs). |
| **CONFIG_ROOT** | Root for stack config and app state (Plex config, Immich config). Default: `./config`. |
| **PUID / PGID** | Linux UID/GID used by LinuxServer and Immich containers for volume permissions. Run `id your_user` on the VM and copy the values. |
| **TZ** | Timezone for all services (e.g. `Etc/UTC`). |

> **Tip:** Run `python3 scripts/setup_env.py` from the repo root to pre-fill `PUID`, `PGID`, and `DOCKER_GID` based on the current host.

### Plex-specific

| Variable | Purpose |
|----------|---------|
| **PLEX_CLAIM** | Optional claim token for first registration of Plex with your Plex account. Get it from the Plex website before initial bring-up. Leave empty after first registration. |

Plex’s internal DB and metadata live under `${CONFIG_ROOT}/plex` and are not exposed to the network.

### Immich-specific

These values are derived from Immich’s official `example.env`, trimmed to what this stack needs.

| Variable | Purpose |
|----------|---------|
| **IMMICH_VERSION** | Version tag used for Immich containers (e.g. `v2`). Pin once you find a stable version. |
| **DB_PASSWORD** | Postgres password used by Immich services. Should be a random `A-Za-z0-9` string (no special characters) per Immich’s Docker docs. |

Other Immich settings (e.g. advanced ML tuning, job concurrency) can use their built-in defaults and be added to `.env` later as needed.

### VM identity and observability

| Variable | Purpose |
|----------|---------|
| **VM_HOSTNAME** | Optional override for the VM hostname label used in metrics/logs. Default: system hostname. |
| **VM_ROLE** | VM role label (default: `accelerated`). Used by observability tooling. |
| **PROXMOX_NODE** | Proxmox node name label. Default: read from `/etc/homelab/proxmox-node`, else `pve1`. |
| **ENABLE_OBSERVABILITY** | When `1`, includes the shared `compose.observability.yml` overlay (node_exporter, cAdvisor, Alloy). Default: `1`. |
| **LOKI_URL** | Loki HTTP endpoint on the Monitoring VM for Alloy log shipping (e.g. `http://monitoring-vm:3100`). |
| **PROMETHEUS_URL** | Prometheus remote_write endpoint on the Monitoring VM for Alloy self-metrics (e.g. `http://monitoring-vm:9090`). |
| **DOCKER_GID** | Docker group GID so Alloy can read the Docker socket. Find with `getent group docker | cut -d: -f3`. |

When observability is enabled, bootstrap generates an Alloy config that tags logs and metrics with consistent labels
(`instance`, `host`, `vm_role`, `node`, `service`) so existing dashboards can target this VM cleanly.

---

## Compose file: Notable details

The full stack lives in `docker_compose/accelerated/compose.yml`. Below are the parts that affect day‑to‑day operations.

### Services at a glance

| Service | Role |
|---------|------|
| **plex** | Media server reading the `media` VM’s organized library; optional hardware transcoding via Intel Quick Sync / VAAPI. |
| **immich-server** | Immich API and web backend. Handles requests from the web UI and mobile apps. |
| **immich-microservices** | Background worker for video processing, thumbnails, and jobs. |
| **immich-machine-learning** | Optional machine learning workloads (face recognition, smart search). Can be wired to GPU later via OpenVINO; defaults to CPU. |
| **immich-postgres** | Postgres database for Immich. Data lives on local disk (`IMMICH_DB_ROOT`). |
| **immich-redis** | Redis instance used by Immich for caching and queues. |

All services share a single internal bridge network (e.g. `accelerated_internal`) and are not exposed directly to the internet.
Plex and Immich publish ports on the VM’s LAN IP; `core` reverse‑proxies those ports based on hostnames.

### Single-root path pattern for Plex

The Plex container uses a single mount for the media library:

```yaml
services:
  plex:
    volumes:
      - ${CONFIG_ROOT:-./config}/plex:/config
      - ${MEDIA_LIBRARY_ROOT:-/mnt/media/library}:/data/library:ro
```

This mirrors the Media VM’s view of the library:

- The same underlying filesystem (`/mnt/media/library` export).  
- A read-only view inside Plex at `/data/library`.  

Plex libraries are then pointed at `/data/library/movies`, `/data/library/tv`, etc.
Downloads and temporary data remain invisible to Plex, as recommended by TRaSH.

### Immich mounts and database

Immich sees two important host paths:

```yaml
services:
  immich-server:
    volumes:
      - ${IMMICH_UPLOAD_ROOT:-/mnt/photos/library}:/photos/library

  immich-postgres:
    volumes:
      - ${IMMICH_DB_ROOT:-./config/immich-postgres}:/var/lib/postgresql/data
```

Design choices:

- **Photo library on NAS (`/mnt/photos`)** — resilient and shared for future tooling.  
- **Database on local disk (`IMMICH_DB_ROOT`)** — aligned with Immich docs, which explicitly
  caution against placing the Postgres data directory on a network share.

> **Important:** Do not move `IMMICH_DB_ROOT` onto NFS or other network filesystems.
> Immich’s own documentation treats that as unsupported and a source of corruption risk.

### GPU wiring

Plex and `immich-server` are wired to the host’s Intel GPU via `/dev/dri`:

```yaml
services:
  plex:
    devices:
      - /dev/dri:/dev/dri

  immich-server:
    devices:
      - /dev/dri:/dev/dri
```

This is the Docker-side contract for Intel Quick Sync / VAAPI:

- Proxmox passes the iGPU through to the Accelerated VM — see [Chapter 1A](Chapter1a-gpu-passthrough.md) for host-side setup.  
- The VM exposes it as `/dev/dri/*` after installing the VA-API driver — see [Chapter 2D — VM-Side GPU Prerequisites](Chapter2d-accelerated.md#vm-side-gpu-prerequisites).  
- Containers that need acceleration are granted access to `/dev/dri`.  

Immich’s ML container (`immich-machine-learning`) starts on CPU by default; GPU
acceleration (OpenVINO, CUDA, ROCm, etc.) can be enabled later by extending its
compose configuration with the official `hwaccel.ml.yml` patterns.

### Host port exposure model

By default, the stack publishes:

- Plex HTTP: `32400:32400`  
- Immich web/API: a single Immich frontend/backend port (e.g. `2283:2283`, depending on the final compose)  

These ports are reachable:

- From the LAN for initial setup and debugging  
- From the Core VM’s reverse proxy, which then exposes them under hostnames like
  `plex.domain` and `photos.domain` with TLS and (optionally) SSO  

No router port-forwarding is configured directly to `accelerated`; only `core` is public.

---

## Bootstrap script: What it does

`bootstrap.py` is **idempotent**: you can run it multiple times safely.
It prepares the Accelerated stack so `docker compose up` (or `deploy.py`) will succeed,
and enforces a few guardrails so mistakes show up early.

### Order of operations

1. **Safety and arguments**  
   - Sets up logging.  
   - Parses `--force`, `--non-interactive`, and `--up`.  
   - Optionally re‑execs as root if future NFS or mount tasks are added (same pattern as the Media VM bootstrap).

2. **Env file**  
   - Requires `.env` to exist next to `compose.yml`.  
   - If `.env` is missing, prints a clear error pointing at `.env.example` and exits.

3. **Env validation and guardrails**  
   - Verifies that `MEDIA_LIBRARY_ROOT` and `IMMICH_UPLOAD_ROOT` are set.  
   - Ensures those paths exist on the host, creating them when reasonable for local testing
     (with log messages clarifying that production should mount real exports).  
   - Verifies that `IMMICH_DB_ROOT` exists and appears to be on a local filesystem
     (warns if it looks like an NFS mount).  
   - Ensures `DB_PASSWORD` is not empty or a placeholder unless `--force` is used.

4. **GPU guardrail**  
   - Checks for `/dev/dri` on the host.  
   - If missing, logs a warning (or exits unless `--force` is set) with guidance to fix Proxmox passthrough.  
   - Optionally inspects `compose.yml` to confirm that `plex` and `immich-server` have the expected `devices: - /dev/dri:/dev/dri` stanza.

5. **Config directories and ownership**  
   - Resolves `CONFIG_ROOT` to an absolute path using `resolve_config_base()` from `scripts/homelab_common`.  
   - Creates config subdirectories for `plex`, `immich`, `immich-postgres`, and `immich-redis`.  
   - Attempts to set ownership to the real user (or PUID/PGID) so containers can write without permission errors.

6. **Observability wiring**  
   - When `ENABLE_OBSERVABILITY=1`, calls `setup_observability_config()` to generate Alloy config
     for this VM, reusing the same labeling conventions as other stacks.

7. **Compose validation**  
   - Runs `docker compose -f compose.yml config` from the stack directory.  
   - If validation fails, prints the error and exits, without starting any containers.

8. **Optional bring-up**  
   - If `--up` is provided (and not running under `HOMELAB_DEPLOY`), runs `docker compose up -d`.  
   - Otherwise, prints a summary and leaves starting the stack to the user or `deploy.py`.

9. **Summary logging**  
   - Prints a summary of key paths (library, photos, DB), whether observability is enabled,
     and what GPU checks passed or failed.

### Flags

| Flag | Effect |
|------|--------|
| **--up** | After bootstrap checks, run `docker compose up -d`. |
| **--force** | Skip overridable guardrails (e.g. placeholder DB password, missing `/dev/dri`) — use for local testing only. |
| **--non-interactive** | Suppresses prompts; used automatically when invoked by `deploy.py`. |

---

## Deploying the accelerated stack

You can deploy the stack directly on the Accelerated VM or via the repo deploy script.

### Path 1: Manual (on the Accelerated VM)

Assumes the repo is cloned on the VM (e.g. under `~/Self-Hosting`).

1. **Clone or pull the repo** (if needed):

   ```bash
   git clone <your-repo-url> ~/Self-Hosting
   cd ~/Self-Hosting
   ```

2. **Create and edit `.env`**:

   ```bash
   cd docker_compose/accelerated
   cp .env.example .env
   # Edit .env:
   #   - MEDIA_LIBRARY_ROOT  (e.g. /mnt/media/library)
   #   - IMMICH_UPLOAD_ROOT  (e.g. /mnt/photos/library)
   #   - IMMICH_DB_ROOT      (default local path is fine)
   #   - PUID / PGID / TZ
   #   - DB_PASSWORD         (strong A–Z / a–z / 0–9)
   #   - PLEX_CLAIM          (for first Plex registration, optional)
   #   - LOKI_URL / PROMETHEUS_URL / DOCKER_GID (if using observability)
   ```

3. **Run bootstrap** (and optionally start the stack):

   ```bash
   python3 bootstrap.py
   # If all checks pass, start the stack:
   docker compose up -d
   # Or in one step:
   python3 bootstrap.py --up
   ```

4. **Verify** — See [Verification and troubleshooting](#verification-and-troubleshooting).

### Path 2: Repo deploy script (`deploy.py`)

From the **repo root**:

1. **Ensure `.env` exists and is filled** in `docker_compose/accelerated/`.

2. **Run deploy**:

   ```bash
   python3 deploy.py accelerated
   ```

   Deploy will:

   - Validate required env vars for `accelerated`  
     (e.g. `MEDIA_LIBRARY_ROOT`, `IMMICH_UPLOAD_ROOT`, `IMMICH_DB_ROOT`, `DB_PASSWORD`).  
   - Run `docker_compose/accelerated/bootstrap.py` with `--non-interactive`.  
   - Create a symlink `~/accelerated` → repo’s `docker_compose/accelerated` (if not already installed).  
   - Run `docker compose up -d` in the stack directory, including the observability overlay when `ENABLE_OBSERVABILITY=1`.  
   - Regenerate shell helpers so you can use `accelerated` commands from any shell.

3. **Optional flags**:

   ```bash
   python3 deploy.py accelerated --force        # Continue even if env validation fails (testing only)
   python3 deploy.py accelerated --init-env -y # Auto-copy .env.example to .env for missing stacks
   ```

   `--init-env` is especially useful on first deploy in a clean environment; it mirrors
   the pattern used for other stacks in this repo.

After a successful deploy, you can use:

```bash
accelerated ps
accelerated logs -f
accelerated up -d
accelerated down
```

from any directory (after reloading your shell).

---

## After first run

Once the stack is up, do the following:

1. **Confirm mounts and GPU visibility**
   - From inside Plex and Immich containers, list library paths:

     ```bash
     accelerated exec plex ls /data/library
     accelerated exec immich-server ls /photos/library
     ```

   - On the host, run `ls /dev/dri` and confirm it exists.  
     Optionally use `intel_gpu_top` while transcoding to confirm GPU usage.

2. **Configure Plex**
   - Visit Plex at `http://<accelerated-vm-ip>:32400` for initial setup.  
   - Point libraries at:
     - `/data/library/movies`
     - `/data/library/tv`
     - `/data/library/anime` (if used)
   - Follow the [TRaSH Plex guide](https://trash-guides.info/Plex/) for server and client tuning.  
     This repo does not duplicate those settings; it assumes TRaSH as the source of truth.

3. **Configure Immich**
   - Visit the Immich web UI on its configured port (e.g. `http://<accelerated-vm-ip>:2283`).  
   - Create the admin account and complete initial setup.  
   - Ensure upload paths and storage mapping align with `/photos/library`.

4. **Wire through Core**
   - On the Core VM, add routes for Plex and Immich in `docker_compose/core/.env`
     via `CADDY_EXTRA_SERVICES` (see [Chapter 3A](Chapter3a-core-stack.md#caddy)).  
   - Regenerate the Caddyfile and reload Caddy.  
   - Decide whether Plex and/or Immich should sit behind SSO (`:sso` suffix) or use
     per-app authentication only.

5. **Pin image tags**
   - After confirming a stable deployment, pin:
     - `PLEX_TAG` (if you add it to `.env`)  
     - `IMMICH_VERSION`  
   - This keeps redeploys predictable.

---

## Verification and troubleshooting

### Quick checks

- **Rendered config with observability overlay**:

  ```bash
  accelerated config
  ```

  (Equivalent to `docker compose -f compose.yml -f compose.observability.yml config` inside the stack directory.)

- **Plex sees the library**:

  ```bash
  accelerated exec plex ls /data/library/movies
  accelerated exec plex ls /data/library/tv
  ```

- **Immich sees the photo root and DB directory**:

  ```bash
  accelerated exec immich-server ls /photos/library
  ls -ld docker_compose/accelerated/config/immich-postgres
  ```

- **GPU exposure**:

  ```bash
  ls /dev/dri
  accelerated exec plex ls /dev/dri
  accelerated exec immich-server ls /dev/dri
  ```

### If something fails

1. **Compose validation**  
   From `docker_compose/accelerated`:

   ```bash
   docker compose -f compose.yml config
   # Or, if observability is enabled:
   docker compose -f compose.yml -f compose.observability.yml config
   ```

2. **Logs**  

   ```bash
   accelerated logs -f
   ```

   Look for:

   - Plex startup errors (e.g. bad paths, permissions).  
   - Immich DB connection errors (e.g. wrong `DB_PASSWORD`).  

3. **Path mismatches**  
   - Ensure `MEDIA_LIBRARY_ROOT` and `IMMICH_UPLOAD_ROOT` exist and are mounted.  
   - Verify Proxmox and fstab entries match the expected paths (`/mnt/media`, `/mnt/photos`).  

4. **GPU issues**  
   - Verify passthrough is configured: [Chapter 1A](Chapter1a-gpu-passthrough.md).  
   - Verify VM-side drivers are installed: [Chapter 2D — VM-Side GPU Prerequisites](Chapter2d-accelerated.md#vm-side-gpu-prerequisites).  
   - Check `dmesg` and Proxmox logs for passthrough errors.  
   - Confirm no other VM or the host itself is using the GPU.  
   - Temporarily run with `--force` and CPU-only to isolate driver vs app issues.

5. **Recovery**  
   - Restore from a Proxmox snapshot or backup of the VM and/or `CONFIG_ROOT`.  
   - Reattach the same NAS exports and rerun `bootstrap.py`, then `docker compose up -d`.  

---

## See also

- [Chapter 1A — Intel iGPU Passthrough](Chapter1a-gpu-passthrough.md): Proxmox host-side IOMMU, VFIO, and PCI device assignment.  
- [Chapter 2D — Accelerated VM](Chapter2d-accelerated.md): purpose, storage design, GPU boundaries, VM-side GPU prerequisites, and app selection.  
- [Chapter 2C — Media VM](Chapter2c-media.md): how the media pipeline produces the library Plex reads.  
- [Chapter 3A — Core stack](Chapter3a-core-stack.md): reverse proxy and Caddy routing (how Plex and Immich become accessible).  
- [Immich documentation](https://immich.app/docs/) — official install, environment variables, hardware transcoding, and ML acceleration.  
- [TRaSH Guides — Plex](https://trash-guides.info/Plex/) — gold standard for Plex server and client tuning.

