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
  - [Paths and identity](#paths-and-identity)
  - [Plex-specific](#plex-specific)
  - [Immich-specific](#immich-specific)
  - [iperf3](#iperf3)
  - [VM identity and observability](#vm-identity-and-observability)
- [Compose file: Notable details](#compose-file-notable-details)
- [Bootstrap script: What it does](#bootstrap-script-what-it-does)
- [Post-deploy automation: `setup_accelerated_apps.py`](#post-deploy-automation-setup_accelerated_appspy)
- [Deploying the accelerated stack](#deploying-the-accelerated-stack)
  - [Path 1: Manual (on the Accelerated VM)](#path-1-manual-on-the-accelerated-vm)
  - [Path 2: Repo deploy script (`deploy.py`)](#path-2-repo-deploy-script-deploypy)
- [After first run](#after-first-run)
- [iperf3 (network testing)](#iperf3-network-testing)
- [Verification and troubleshooting](#verification-and-troubleshooting)
- [See also](#see-also)

---

## What's in `docker_compose/accelerated/`

The Accelerated stack follows the same “one stack, one directory” pattern as `core` and `media`.

| File or script | Purpose |
|----------------|---------|
| **compose.yml** | Stack definition: Plex, Immich services (server, microservices, machine learning), Postgres, Redis. |
| **.env.example** | Template for required and optional env vars (no secrets; copy to `.env` and fill). Defines paths, IDs, Plex tokens, image tags, GPU and observability toggles. |
| **bootstrap.py** | Idempotent first-run: optional NFS mount setup, validates `.env`, PLEX_CLAIM guardrail, GPU guardrail, creates config directories, wires observability, validates compose. |
| **setup_accelerated_apps.py** | Post-deploy automation: waits for Plex to be ready, applies TRaSH-recommended server settings, and creates library sections (Movies, TV Shows, Anime). Called automatically by `deploy.py`. |
| **scripts/iperf3_test.py** | Print ready-to-paste iperf3 **client** commands (run from the remote end) and run a local sanity check that the iperf3 server is up. See [iperf3 (network testing)](#iperf3-network-testing). |
| **compose.observability.yml** | Symlink to `docker_compose/common/compose.observability.yml`. Adds node_exporter, cAdvisor, Alloy sidecars, and the Plex metrics exporter when `ENABLE_OBSERVABILITY=1`. |

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

> **Tip:** Run `python3 scripts/setup_env.py` from the repo root to stage `PUID`, `PGID`, and `DOCKER_GID` (based on the current host) into `.env.staged`, then copy it to `.env`.

### Plex-specific

| Variable | Purpose |
|----------|---------|
| **PLEX_CLAIM** | One-time server registration token. Get it from [plex.tv/claim](https://www.plex.tv/claim) (expires in 4 minutes) immediately before first deploy. Bootstrap blocks if this is empty on a fresh install. Leave empty on subsequent deploys after Plex has been claimed. |
| **PLEX_TOKEN** | Permanent Plex API token (your plex.tv account token). Used by `setup_accelerated_apps.py` (library creation, server settings) and the Plex Prometheus exporter. Unlike `PLEX_CLAIM`, this never changes — retrieve it from your plex.tv account before deploying. See [How to find your Plex token](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/). |
| **PLEX_HOST** | LAN IP or hostname of this VM (e.g. `192.168.1.140`). Set in both `accelerated/.env` and `media/.env` so Sonarr and Radarr can reach Plex for library refresh notifications after an import. |
| **PLEX_SERVER** | URL the Plex Prometheus exporter uses to reach Plex from within the container. Default: `http://plex:32400`. |

> ### 🧠 Design Note: Two Plex Tokens
> There are two distinct tokens and it is easy to confuse them:
> - **`PLEX_CLAIM`** — a one-time registration token from [plex.tv/claim](https://www.plex.tv/claim). It binds the new server to your account on first start and expires in 4 minutes. After Plex starts successfully, the token is consumed and never needed again.
> - **`PLEX_TOKEN`** (`X-Plex-Token`) — your permanent plex.tv account token. It exists independently of any server instance and can be retrieved from your account at any time. All automation — library creation, server settings, Prometheus exporter, and Sonarr/Radarr library refresh — uses this token.
>
> In practice: get `PLEX_TOKEN` first (any time, even before Plex is installed), then get `PLEX_CLAIM` last, right before running deploy, since it expires quickly.

Plex's internal DB and metadata live under `${CONFIG_ROOT}/plex` and are not exposed to the network.

### Immich-specific

These values are derived from Immich’s official `example.env`, trimmed to what this stack needs.

| Variable | Purpose |
|----------|---------|
| **IMMICH_VERSION** | Version tag used for Immich containers (e.g. `v2`). Pin once you find a stable version. |
| **DB_PASSWORD** | Postgres password used by Immich services. Should be a random `A-Za-z0-9` string (no special characters) per Immich’s Docker docs. |

Other Immich settings (e.g. advanced ML tuning, job concurrency) can use their built-in defaults and be added to `.env` later as needed.

### iperf3

| Variable | Purpose |
|----------|---------|
| **IPERF3_TAG** | Image tag for `mm404/iperf3`. Pin a release after validating (e.g. `3.20.0-alpine3.23.4`). |
| **IPERF3_PORT** | Port the iperf3 server listens on and clients connect to. Default `5201`. |
| **IPERF3_BIND_IP** | Host interface to bind. Default `0.0.0.0` so a router port-forward works out of the box; set to the VM LAN IP to restrict which NIC listens. |

The iperf3 server runs 24/7 but is only reachable from the internet while you manually enable a router port-forward of TCP+UDP `IPERF3_PORT`. See [iperf3 (network testing)](#iperf3-network-testing).

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
| **iperf3** | Network bandwidth test server (`mm404/iperf3`); always on; publishes TCP+UDP **5201** (bound via **IPERF3_BIND_IP**). Not HTTP, so not behind Caddy. Hardened (`read_only`, `no-new-privileges`, `cap_drop: ALL`). |

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

The stack also publishes **iperf3 on TCP+UDP `5201`** for network testing. Like Plex and Immich, it is only reachable on the LAN by default. **The one exception to "only `core` is public"** is a *temporary, manually-added* router port-forward of 5201 used only while running an internet bandwidth test — see [iperf3 (network testing)](#iperf3-network-testing). Otherwise, no router port-forwarding is configured directly to `accelerated`; only `core` is public.

---

## Bootstrap script: What it does

`bootstrap.py` is **idempotent**: you can run it multiple times safely.
It prepares the Accelerated stack so `docker compose up` (or `deploy.py`) will succeed,
and enforces a few guardrails so mistakes show up early.

### Order of operations

1. **NFS mount setup** *(interactive mode only; skipped by `--non-interactive` / `deploy.py`)*  
   - Offers to configure fstab entries for `MEDIA_LIBRARY_ROOT` and `IMMICH_UPLOAD_ROOT`.  
   - Asks for NAS host, export paths, and local mount points (defaults from `.env`).  
   - Writes `nofail,_netdev,x-systemd.automount` entries so boot does not hang if the NAS is unreachable.  
   - Runs `mount -a` to apply immediately.  
   - Installs a bounded (5 min) ping-retry wait for the NAS host (`wait-for-nas@<host>.service`, a systemd template unit), ordered before both the NFS mount unit and `docker.service`. This closes two gaps: a reboot racing dockerd against a slow-but-already-up NAS (containers with `restart: unless-stopped` — e.g. Plex — can start bind-mounted to the pre-mount empty directory), and a full power-outage recovery where the NAS itself is still booting (NFS's own soft-mount retry window is only ~15-20s, far shorter than a Synology's boot time). The wait always gives up and lets boot continue after the timeout — it never hangs indefinitely.

2. **Env file**  
   - Requires `.env` to exist next to `compose.yml`.  
   - If `.env` is missing, prints a clear error pointing at `.env.example` and exits.

3. **Env validation and guardrails**  
   - Verifies that `MEDIA_LIBRARY_ROOT` and `IMMICH_UPLOAD_ROOT` are set.  
   - Ensures those paths exist on the host, creating them locally when reasonable for testing
     (with log messages clarifying that production should use real NFS mounts).  
   - Verifies that `IMMICH_DB_ROOT` exists and appears to be on a local filesystem
     (warns if it looks like an NFS mount).  
   - Ensures `DB_PASSWORD` is not empty or a placeholder unless `--force` is used.

4. **PLEX_CLAIM guardrail**  
   - Checks if `PLEX_CLAIM` is set. If it is empty **and** Plex has never been started before
     (heuristic: `Preferences.xml` does not exist), bootstrap exits with a clear message
     explaining where to get the claim token and how to set it.  
   - Bypassed by `--force` (Plex will start unclaimed — avoid in production).

5. **GPU guardrail**  
   - Checks for `/dev/dri` on the host.  
   - If missing, warns (or exits unless `--force`) with guidance to fix Proxmox passthrough or VM-side drivers.  
   - Inspects `compose.yml` to confirm `plex` and `immich-server` declare the `/dev/dri:/dev/dri` devices stanza.

6. **Config directories and ownership**  
   - Creates config subdirectories for `plex`, `immich`, `immich-postgres`, and `immich-redis`.  
   - Attempts to set ownership to the real user so containers can write without permission errors.

7. **Observability wiring**  
   - When `ENABLE_OBSERVABILITY=1`, generates an Alloy config for this VM using the same
     labeling conventions as other stacks.

8. **Compose validation**  
   - Runs `docker compose config` for the active set of compose files.  
   - Exits if validation fails before starting any containers.

9. **Optional bring-up**  
   - If `--up` is provided (manual mode), runs `docker compose up -d`.  
   - Under `deploy.py`, bring-up is handled by deploy; bootstrap just validates and prepares.

### Flags

| Flag | Effect |
|------|--------|
| **--up** | After bootstrap checks, run `docker compose up -d`. |
| **--force** | Skip overridable guardrails (e.g. placeholder DB password, missing `/dev/dri`) — use for local testing only. |
| **--non-interactive** | Suppresses prompts; used automatically when invoked by `deploy.py`. |

---

## Post-deploy automation: `setup_accelerated_apps.py`

`setup_accelerated_apps.py` runs automatically after `docker compose up` when you use `deploy.py`. It can also be run standalone:

```bash
cd docker_compose/accelerated && python3 setup_accelerated_apps.py
```

It requires `PLEX_TOKEN` to be set in `.env`. If it is not set, the script logs a warning and exits cleanly — no harm done, but you will need to configure Plex manually or re-run the script later.

### What it does

1. **Waits for Plex to be ready** — polls `/identity` up to 120 seconds so it is safe to run immediately after `docker compose up`.

2. **Applies TRaSH-recommended server settings** via `PUT /:/prefs`:

   | Setting | Value | Why |
   |---------|-------|-----|
   | Filesystem event scanning | on | Fast partial scan on file change instead of full library scan |
   | Auto-empty trash | off | Let *arrs manage file lifecycle; do not auto-delete |
   | Hardware transcoding | on | Use Intel Quick Sync / VAAPI (requires GPU passthrough) |
   | Allow media deletion from clients | off | Prevent accidental deletes from Plex clients |
   | DLNA | off | Off unless explicitly needed |
   | Online media sources | off | Disable Plex's built-in noise in the library |

   Full reference: [TRaSH — Suggested Plex Media Server Settings](https://trash-guides.info/Plex/Plex-media-server-settings/).

3. **Creates library sections** — idempotent, skips existing libraries:

   | Library name | Type | Container path |
   |---|---|---|
   | Movies | movie | `/data/library/movies` |
   | TV Shows | show | `/data/library/tv` |
   | Anime | show | `/data/library/anime` |

   These paths align with the [TRaSH file and folder structure](https://trash-guides.info/File-and-Folder-Structure/) used by the Media VM.

### Running it standalone

If `PLEX_TOKEN` was not set at deploy time, add it to `.env` and re-run:

```bash
cd ~/accelerated   # or docker_compose/accelerated
# Edit .env: set PLEX_TOKEN=<your-token>
python3 setup_accelerated_apps.py
```

The script is fully idempotent — settings already at the target value are left alone, and existing library sections are skipped.

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
   #   - MEDIA_LIBRARY_ROOT  (e.g. /mnt/media/library — NFS mount from Media VM)
   #   - IMMICH_UPLOAD_ROOT  (e.g. /mnt/photos/library)
   #   - IMMICH_DB_ROOT      (default local path is fine)
   #   - PUID / PGID / TZ
   #   - DB_PASSWORD         (strong A–Z / a–z / 0–9)
   #   - PLEX_TOKEN          (permanent account token — get from plex.tv any time)
   #   - PLEX_HOST           (LAN IP of this VM, e.g. 192.168.1.140)
   #   - PLEX_CLAIM          (one-time token from plex.tv/claim — get last, expires in 4 min)
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
   - Run `bootstrap.py --non-interactive` (NFS prompts skipped; PLEX_CLAIM guardrail active).  
   - Create a symlink `~/accelerated` → repo's `docker_compose/accelerated` (if not already installed).  
   - Run `docker compose up -d`, including the observability and Plex exporter overlays when `ENABLE_OBSERVABILITY=1`.  
   - Run `setup_accelerated_apps.py` — applies TRaSH Plex settings and creates library sections (requires `PLEX_TOKEN`).  
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

Once the stack is up, most Plex configuration is handled automatically by `setup_accelerated_apps.py`. What remains is verification and a few steps that require human decisions.

1. **Confirm mounts and GPU visibility**

   ```bash
   accelerated exec plex ls /data/library
   accelerated exec immich-server ls /photos/library
   ls /dev/dri
   ```

   Use `intel_gpu_top` while transcoding to confirm GPU usage.

2. **Verify automated Plex setup**
   - Visit Plex at `http://<accelerated-vm-ip>:32400` (or through Caddy if already wired).  
   - Check that the three libraries (Movies, TV Shows, Anime) were created automatically.  
   - Server settings (hardware transcoding, no auto-delete, no media deletion from clients) were applied by `setup_accelerated_apps.py`. Verify in **Settings → Troubleshooting → Download Logs** if in doubt.  
   - If `PLEX_TOKEN` was not set at deploy time, run `setup_accelerated_apps.py` manually after adding it to `.env`.  
   - Follow the [TRaSH Plex guide](https://trash-guides.info/Plex/) for client-side tuning not covered by the automation.

   > ### ⚠️ Confirm the GPU Is Actually Used: Look for the `(hw)` Tag
   > Enabling hardware transcoding in settings does **not** guarantee it works — if the
   > driver or firmware isn't loaded, Plex silently falls back to CPU. To confirm: play a
   > file that forces a transcode (e.g. lower the quality in a client), then open
   > **Settings → Status → Now Playing** (or the **Dashboard**). The active stream should
   > read **`Transcode (hw)`**. If it shows just **`Transcode`** without the `(hw)` tag,
   > Plex is transcoding on the **CPU** — this causes a large CPU spike and video
   > stuttering. Recheck that hardware transcoding is enabled, that
   > `/dev/dri/renderD128` is accessible inside the container
   > (`accelerated exec plex ls -l /dev/dri`), and that the GPU firmware is loaded
   > (reboot the VM after installing `firmware-intel-graphics` — see
   > [Chapter 2D — VM-Side GPU Prerequisites](Chapter2d-accelerated.md#vm-side-gpu-prerequisites)).

3. **Connect Sonarr/Radarr → Plex (on the Media VM)**
   - Set `PLEX_HOST` and `PLEX_TOKEN` in `docker_compose/media/.env`.  
   - Run `setup_media_apps.py` on the Media VM (or redeploy media):

     ```bash
     cd ~/media   # or docker_compose/media
     python3 setup_media_apps.py
     ```

   - This adds Plex as a notification connection in both Sonarr and Radarr. After an import, the *arr will call Plex's API to refresh just the affected library section — no waiting for a scheduled scan.

4. **Configure Immich**
   - Visit the Immich web UI at `http://<accelerated-vm-ip>:2283`.  
   - Create the admin account and complete initial setup.  
   - Ensure upload paths align with `/photos/library`.

5. **Wire through Core**
   - Add routes for Plex and Immich in `docker_compose/core/.env` via `CADDY_EXTRA_SERVICES`
     (see [Chapter 3A — Core stack](Chapter3a-core-stack.md)).  
   - Decide access model: Plex has its own authentication and works well with direct LAN/Tailscale access on port `32400`. Immich benefits from a Caddy reverse proxy for external access.

6. **Pin image tags**
   - After confirming a stable deployment, pin `PLEX_TAG` and `IMMICH_VERSION` in `.env` to keep redeploys predictable.

---

## iperf3 (network testing)

iperf3 measures raw TCP/UDP throughput between two endpoints. It is the tool to use when remote Plex playback buffers over the internet and you want to confirm or rule out a **network bandwidth** problem. It lives on this VM because Plex does, so a test travels the same path Plex streaming uses. See [Chapter 2D — Network testing: iperf3](Chapter2d-accelerated.md#network-testing-iperf3) for the "why".

The iperf3 **server** runs 24/7 and needs no configuration. The test is **two-sided**: you run an iperf3 **client** from the remote location where playback is poor.

> ### 🧠 Why reverse mode (`-R`) matters
> Plex remote streaming sends data *from* your server *to* the viewer — i.e. it uses your home **upload** bandwidth, which is usually the bottleneck. By default an iperf3 client tests the *download* direction. Add **`-R`** so the server sends to the client, measuring the upload path that Plex actually uses.

### Running a test

1. **Enable the port-forward (temporarily).** iperf3 is not HTTP, so it cannot go behind Caddy. To test over the internet, add a router port-forward of **TCP and UDP `IPERF3_PORT` (default 5201)** to this VM's LAN IP. iperf3 has **no authentication**, so **remove the forward as soon as you finish**.
2. **Print the client commands.** From the stack directory:
   ```bash
   cd docker_compose/accelerated
   python3 scripts/iperf3_test.py
   ```
   This prints ready-to-paste commands, a Plex bandwidth reference, and runs a quick local sanity check that the server container is up. (If another LAN machine has `iperf3`, the helper also prints a LAN test against `PLEX_HOST` that needs no port-forward.)
3. **Run the client from the remote location.** Typical tests:
   ```bash
   # Bandwidth (TCP, Plex-like): reverse = server uploads to you
   iperf3 -c <public-domain-or-ip> -p 5201 -R -t 30 -O 5
   # Quality (UDP): jitter and packet loss near your target stream bitrate
   iperf3 -c <public-domain-or-ip> -p 5201 -u -R -b 25M -t 30
   ```
4. **Interpret.** Compare the TCP `-R` result to what Plex needs (roughly: 720p ≈ 5 Mbps, 1080p ≈ 15 Mbps, 4K HDR ≈ 60 Mbps per stream). If the UDP run shows high jitter or non-trivial packet loss, link quality (not just raw bandwidth) is likely causing the stutter. If bandwidth is comfortably above what the stream needs, the problem is probably **not** the network — look at transcoding (is it `Transcode (hw)`?), disk, or the client next.
5. **Remove the port-forward.**

The single `iperf3 -s` server handles every client variation (TCP/UDP, direction, bitrate, parallel streams) — there is no need for multiple server configs.

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

- **iperf3 server**:

  ```bash
  docker exec iperf3 iperf3 -c 127.0.0.1 -p 5201 -t 1
  # or: cd docker_compose/accelerated && python3 scripts/iperf3_test.py
  ```

  A loopback test should report a throughput result, confirming the server is listening.

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
- [Local testing guide](Local-testing-guide.md): End-to-end test of all stacks on a four-VM LAN setup.  
- [Immich documentation](https://immich.app/docs/) — official install, environment variables, hardware transcoding, and ML acceleration.  
- [TRaSH Guides — Plex](https://trash-guides.info/Plex/) — gold standard for Plex server and client tuning.

