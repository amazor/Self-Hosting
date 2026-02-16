# Chapter 3C — Media Stack: Configuration and Deployment

## Introduction

**Prerequisites:** [Chapter 2C (Media VM)](Chapter2c-media.md) (VM purpose, storage model, access model), [Chapter 2](Chapter2-vms.md) (VM overview), and [Chapter 3A (Core stack)](Chapter3a-core-stack.md) (deploy pattern).

Chapter 2C explains why the media VM is shaped the way it is. This chapter is the operational guide: what is inside `docker_compose/media/`, how `.env` drives overlays, how to deploy with `deploy.sh`, and how to configure each UI so imports work without remote path mappings.

The key design is unchanged from Chapter 2C: one host root (`/mnt/media`) mapped as one container root (`/data`) so every app sees the same filesystem view.

> ### 🧠 Philosophy: One Host Root, One Container Root
> A media stack is easier to debug when every service sees the same paths. If qBittorrent, SABnzbd, Sonarr, Radarr, and Bazarr all see `/data/...`, imports become predictable and hardlinks work as intended.

> ### 🧠 Philosophy: Simple Base, Optional Layers
> The base stack stays focused on the core pipeline. Optional services are overlays controlled by `ENABLE_*` flags, so you can start simple and add capability without rewriting the foundation.

---

## Table of contents

- [What's in `docker_compose/media/`](#whats-in-docker_composemedia)
- [Configuration reference (TRaSH)](#configuration-reference-trash)
- [Environment: `.env.example`](#environment-envexample)
- [Compose files: Notable details](#compose-files-notable-details)
- [Bootstrap script: What it does](#bootstrap-script-what-it-does)
- [Deploying the media stack](#deploying-the-media-stack)
  - [Path 1: Manual (on the Media VM)](#path-1-manual-on-the-media-vm)
  - [Path 2: Repo deploy script (`deploy.sh`)](#path-2-repo-deploy-script-deploysh)
- [After first run](#after-first-run)
- [UI configuration how-tos](#ui-configuration-how-tos)
  - [qBittorrent](#qbittorrent)
  - [SABnzbd (optional)](#sabnzbd-optional)
  - [Sonarr](#sonarr)
  - [Radarr](#radarr)
  - [Prowlarr and FlareSolverr](#prowlarr-and-flaresolverr)
  - [Bazarr (optional)](#bazarr-optional)
  - [Buildarr and Recyclarr (optional)](#buildarr-and-recyclarr-optional)
  - [Cleanuparr and ntfy (optional)](#cleanuparr-and-ntfy-optional)
- [Verification and troubleshooting](#verification-and-troubleshooting)
- [See also](#see-also)

---

## What's in `docker_compose/media/`

| File or script | Purpose |
|----------------|---------|
| **compose.yml** | Base services: Gluetun VPN, qBittorrent, Sonarr, Radarr, Prowlarr, FlareSolverr |
| **compose.sabnzbd.yml** | Optional Usenet downloader overlay |
| **compose.bazarr.yml** | Optional subtitle automation overlay |
| **buildarr.example.yml** / **recyclarr.example.yml** / **recyclarr.secrets.example.yml** | Example YAML configs for Buildarr and Recyclarr; bootstrap copies into `config/buildarr/` and `config/recyclarr/` when enabled and target missing (same idea as `.env` from `.env.example`) |
| **compose.buildarr-recyclarr.yml** | Optional profile-driven config sync overlay (bootstrap profile) |
| **compose.cleanuparr.yml** | Optional queue/download hygiene overlay |
| **compose.ntfy.yml** | Optional lightweight push notifications overlay |
| **.env.example** | Template for required values and optional feature toggles |
| **bootstrap.sh** | Idempotent first-run checks: env validation, mount checks, config directory creation, VPN guardrail |

All overlays are selected from `.env` by `ENABLE_*` flags and are automatically included by `deploy.sh` and the generated `media` shell helper.

---

## Configuration reference (TRaSH)

TRaSH Guides are the gold standard for media configuration. This chapter follows those guides while preserving this repo's Chapter 2C storage layout (`downloads/` + `library/` under one root):

- [TRaSH File and Folder Structure](https://trash-guides.info/File-and-Folder-Structure/)
- [TRaSH qBittorrent](https://trash-guides.info/Downloaders/qBittorrent/)
- [TRaSH SABnzbd](https://trash-guides.info/Downloaders/SABnzbd/)
- [TRaSH Sonarr](https://trash-guides.info/Sonarr/)
- [TRaSH Radarr](https://trash-guides.info/Radarr/)
- [TRaSH Prowlarr](https://trash-guides.info/Prowlarr/)
- [TRaSH Bazarr](https://trash-guides.info/Bazarr/)
- [TRaSH Guide Sync (Recyclarr/Configarr)](https://trash-guides.info/Guide-Sync/)
- [TRaSH Golden Rule (x264 vs x265)](https://trash-guides.info/Misc/x265-4k/)

---

## Environment: `.env.example`

Copy and edit:

```bash
cd docker_compose/media
cp .env.example .env
```

### Paths and identity

| Variable | Purpose |
|----------|---------|
| **MEDIA_ROOT** | Host root for both downloads and library. Default is `/mnt/media`. |
| **CONFIG_ROOT** | Host root for service config (default `./config` from this directory). |
| **PUID / PGID** | LinuxServer container user/group IDs for volume permissions. |
| **TZ** | Shared timezone for all services. |

Use `id your_user` on the host to get `PUID` and `PGID`.

### VPN (required for torrent routing)

| Variable | Purpose |
|----------|---------|
| **OPENVPN_USER** | VPN username (ExpressVPN with current compose defaults). |
| **OPENVPN_PASSWORD** | VPN password. |
| **SERVER_COUNTRIES / SERVER_CITIES** | Optional narrowing of endpoint selection. |

`OPENVPN_USER` and `OPENVPN_PASSWORD` are required and validated by `bootstrap.sh` and `deploy.sh`.

### Optional overlays

Set the following to `1` to enable extra compose files:

- `ENABLE_BUILDARR_RECYCLARR`
- `ENABLE_CLEANUPARR`
- `ENABLE_SABNZBD`
- `ENABLE_BAZARR`
- `ENABLE_NTFY`

### Optional image tags (reproducibility)

The template includes optional tag variables. Keep defaults while testing, then pin known-good versions in `.env` for predictable redeploys.

---

## Compose files: Notable details

### Services at a glance

| Service | Role |
|---------|------|
| **vpn (Gluetun)** | VPN egress boundary for qBittorrent traffic |
| **qbittorrent** | Torrent downloader running via `network_mode: service:vpn` |
| **sonarr / radarr** | TV and movie automation |
| **prowlarr** | Indexer management and sync to *arr apps |
| **flaresolverr** | Optional challenge bypass for protected indexers |
| **sabnzbd** *(overlay)* | Usenet downloader |
| **bazarr** *(overlay)* | Subtitle automation for Sonarr/Radarr libraries |
| **buildarr / recyclarr** *(overlay)* | Optional config discipline and TRaSH sync bootstrap jobs |
| **cleanuparr / ntfy** *(overlay)* | Optional cleanup and notifications |

### Single-root path pattern

Every media-touching service mounts:

```yaml
- ${MEDIA_ROOT:-/mnt/media}:/data
```

That gives a consistent view:

- Downloads: `/data/downloads/qbittorrent/...`, `/data/downloads/sabnzbd/...`
- Library: `/data/library/tv`, `/data/library/anime`, `/data/library/movies`

Because all apps see identical paths, Sonarr/Radarr imports work without remote path mapping.

### Host port exposure model

This stack intentionally keeps host `ports:` for direct UI access during setup and troubleshooting. In production, you can still route browser access through the Core VM reverse proxy as described in [Chapter 2C access model](Chapter2c-media.md#access-model-internal-by-default-external-through-core).

---

## Bootstrap script: What it does

`bootstrap.sh` is safe to rerun. It prepares the stack and guardrails, but it does not run `docker compose up -d` by itself.

### Order of operations

1. Re-exec as root if needed (for fstab and ownership tasks).
2. Parse `--force` and `--non-interactive`.
3. Optionally configure NFS mount (interactive runs only).
4. Require existing `.env` and load variables.
5. Validate required VPN credentials.
6. Validate media root and expected subdirectories.
7. Ensure base path strategy is the single `/data` mapping.
8. Enforce VPN guardrail: qBittorrent must route through `vpn`.
9. Create config directories for base and enabled overlays.
10. When `ENABLE_BUILDARR_RECYCLARR=1`, copy `buildarr.example.yml`, `recyclarr.example.yml`, and `recyclarr.secrets.example.yml` into the config dirs **only if the target files do not exist** (same as `.env` from `.env.example`).
11. Print stack summary.

### Flags

| Flag | Effect |
|------|--------|
| **--non-interactive** | Skip prompts (used by `deploy.sh`). |
| **--force** | Continue despite overridable guardrails. |

---

## Deploying the media stack

You can deploy directly on the Media VM or use the repo deploy workflow.

### Path 1: Manual (on the Media VM)

1. Clone/pull the repo and enter stack directory:

   ```bash
   cd ~/Self-Hosting/docker_compose/media
   ```

2. Create and edit `.env`:

   ```bash
   cp .env.example .env
   ```

3. Run bootstrap:

   ```bash
   ./bootstrap.sh
   ```

4. Start services (base only, or base + overlays):

   ```bash
   docker compose -f compose.yml up -d
   ```

### Path 2: Repo deploy script (`deploy.sh`)

From repo root:

1. Ensure `docker_compose/media/.env` exists and required values are set (`OPENVPN_USER`, `OPENVPN_PASSWORD`, `MEDIA_ROOT`, `CONFIG_ROOT`).
2. Deploy:

   ```bash
   ./deploy.sh media
   ```

`deploy.sh media` will:

- validate required env vars for media
- run `docker_compose/media/bootstrap.sh` in deploy mode
- include overlay compose files based on `ENABLE_*` flags
- create/update `~/media` symlink to this stack
- install shell helpers so you can run media commands from any directory

Common helper usage after deploy:

```bash
media ps
media logs -f
media up -d
media down
media boot
```

- `media up -d` starts base + enabled overlays.
- `media boot` runs bootstrap-profile jobs (Buildarr/Recyclarr) when enabled.

Optional deploy flags:

- `./deploy.sh media --force` for testing or temporary bypass of guardrails.
- `./deploy.sh media --non-interactive` for automation.

---

## After first run

Recommended order:

1. Confirm VPN is healthy and qBittorrent UI is reachable.
2. Configure qBittorrent categories and automatic management.
3. Configure Sonarr/Radarr root folders and download clients.
4. Configure Prowlarr indexers and app sync.
5. If enabled, configure SABnzbd and Bazarr.
6. Run `media boot` to apply Recyclarr/Buildarr sync if enabled.

---

## UI configuration how-tos

### qBittorrent

Reference: [TRaSH qBittorrent](https://trash-guides.info/Downloaders/qBittorrent/)

**Skip when using Buildarr/Recyclarr:** Buildarr can define qBittorrent as a download client in `buildarr.yml`; you still need to configure **paths and categories in the qBittorrent UI** (or ensure Buildarr applies the same paths). Recyclarr does not manage download clients.

1. Open UI on `http://<media-vm-ip>:8080`.
2. **Options -> Downloads**:
   - Default save path: `/data/downloads/qbittorrent`
   - Keep incomplete torrents in: `/data/downloads/qbittorrent/incomplete`
3. **Options -> Saving Management**:
   - Set **Default Torrent Management Mode** to **Automatic** (required).
4. Add categories (left sidebar):
   - `tv` with save path `completed/tv`
   - `movies` with save path `completed/movies`
5. Verify by starting a test download and confirming files land under category folders.

### SABnzbd (optional)

Reference: [TRaSH SABnzbd](https://trash-guides.info/Downloaders/SABnzbd/)

**Skip when using Buildarr/Recyclarr:** Not managed by Recyclarr. If you define SABnzbd in Buildarr, you can skip adding it again in Sonarr/Radarr UI; SABnzbd **paths and categories** are still set in the SABnzbd UI.

1. Open `http://<media-vm-ip>:8081`.
2. **Config -> Folders**:
   - Completed: `/data/downloads/sabnzbd/completed`
   - Temporary/intermediate: `/data/downloads/sabnzbd/tmp` and `/data/downloads/sabnzbd/intermediate`
3. **Config -> Categories**:
   - `tv` -> `tv` (relative folder)
   - `movies` -> `movies` (relative folder)
4. Verify with one test NZB and confirm destination category path.

### Sonarr

Reference: [TRaSH Sonarr](https://trash-guides.info/Sonarr/)

**Skip when using Buildarr/Recyclarr:** If Buildarr is configured with root folders and download clients in `buildarr.yml`, skip steps 3–4 for those. If Recyclarr is configured with TRaSH templates (quality definitions, custom formats, quality profiles, naming), skip step 5 and run `media boot` to sync instead. You still need Media Management options (Rename Episodes, Analyze video files) unless Buildarr manages them.

1. Open `http://<media-vm-ip>:8989`.
2. **Settings -> Media Management**:
   - Enable Show Advanced
   - Enable Rename Episodes
   - Enable Analyze video files
3. **Settings -> Media Management -> Root Folders**:
   - `/data/library/tv`
   - `/data/library/anime` (if used)
4. **Settings -> Download Clients**:
   - qBittorrent: host `qbittorrent`, port `8080`, category `tv`
   - SABnzbd (if enabled): host `sabnzbd`, port `8080`, category `tv`
5. **Settings -> Profiles / Custom Formats**:
   - Apply TRaSH quality settings, profiles, and custom formats.
6. Verify by adding a test series and checking import succeeds without remote path mapping.

### Radarr

Reference: [TRaSH Radarr](https://trash-guides.info/Radarr/)

**Skip when using Buildarr/Recyclarr:** If Buildarr manages root folders and download clients, skip steps 3–4 for those. If Recyclarr syncs TRaSH (quality definitions, custom formats, quality profiles, naming), skip step 5 and use `media boot` instead. Media Management toggles (Rename Movies, Analyze video files) are still needed unless defined in Buildarr.

1. Open `http://<media-vm-ip>:7878`.
2. **Settings -> Media Management**:
   - Enable Show Advanced
   - Enable Rename Movies
   - Enable Analyze video files
3. Add root folder: `/data/library/movies`.
4. Add download clients:
   - qBittorrent category `movies`
   - SABnzbd category `movies` (if enabled)
5. Apply TRaSH naming, quality profiles, and custom formats.
6. Verify by adding one test movie and confirming import path under `/data/library/movies`.

### Prowlarr and FlareSolverr

Reference: [TRaSH Prowlarr](https://trash-guides.info/Prowlarr/)

**Skip when using Buildarr/Recyclarr:** Buildarr can manage Prowlarr (apps, indexers) via `buildarr.yml`; if so, configure those in YAML and run `media boot`. Recyclarr does not manage Prowlarr.

1. Open Prowlarr on `http://<media-vm-ip>:9696`.
2. Add Sonarr and Radarr under **Settings -> Apps**.
3. Add indexers and run test.
4. Only configure proxy/FlareSolverr for indexers that need challenge handling.
5. If needed, add FlareSolverr endpoint (`http://flaresolverr:8191`) in indexer settings.
6. Verify indexer sync appears in Sonarr/Radarr.

### Bazarr (optional)

Reference: [TRaSH Bazarr](https://trash-guides.info/Bazarr/)

**Skip when using Buildarr/Recyclarr:** Bazarr is not managed by Buildarr or Recyclarr; configure it in the UI.

1. Open `http://<media-vm-ip>:6767`.
2. Configure Sonarr and Radarr connections:
   - `http://sonarr:8989`
   - `http://radarr:7878`
3. Confirm library paths map to `/data/library/...`.
4. Configure preferred subtitle languages and scoring.
5. Verify by running subtitle search for one existing item.

### Buildarr and Recyclarr (optional)

Reference: [TRaSH Guide Sync](https://trash-guides.info/Guide-Sync/)

Both tools are driven by **YAML configuration**:

- **Buildarr** — `buildarr.yml` (and optional per-instance config) under `${CONFIG_ROOT}/buildarr/`. Declarative *arr configuration: you define Sonarr, Radarr, and Prowlarr settings (root folders, download clients, quality, naming, etc.) in YAML; Buildarr applies them via API. Docs: [Buildarr](https://buildarr.github.io/).
- **Recyclarr** — `recyclarr.yml` (and optional per-instance overrides) under `${CONFIG_ROOT}/recyclarr/`. Syncs TRaSH Guide content (Custom Formats, Quality Profiles, Quality Settings / file size, Naming Scheme) into Sonarr and Radarr. Docs: [Recyclarr](https://recyclarr.dev/).

**When Buildarr and/or Recyclarr are enabled and configured:** You can skip or reduce the corresponding manual UI steps in this chapter. Recyclarr covers Quality Settings (file size), Custom Formats, Quality Profiles, and Naming in Sonarr/Radarr—so you do not need to configure those in the UI if your Recyclarr YAML is set up. Buildarr covers whatever you define in `buildarr.yml` (e.g. root folders, download clients, quality, naming); for those items, follow Buildarr’s config schema instead of the Sonarr/Radarr/Prowlarr UI steps below. See the "Skip when using Buildarr/Recyclarr" notes in each app section above. qBittorrent/SABnzbd paths and categories are still set in their UIs unless you model them in Buildarr.

**Example configs:** In `docker_compose/media/` you will find `buildarr.example.yml`, `recyclarr.example.yml`, and `recyclarr.secrets.example.yml`. When `ENABLE_BUILDARR_RECYCLARR=1`, **bootstrap copies these into `config/buildarr/` and `config/recyclarr/` only if the target file does not already exist** (same idea as `.env` from `.env.example`). Edit the copied files to set API keys; do not commit `secrets.yml`.

1. Ensure config files exist (bootstrap creates them from examples when enabled and missing):
   - `${CONFIG_ROOT}/buildarr/buildarr.yml`
   - `${CONFIG_ROOT}/recyclarr/recyclarr.yml`
   - `${CONFIG_ROOT}/recyclarr/secrets.yml` (API keys for Recyclarr)
2. Edit API keys and any settings; then run:

   ```bash
   media boot
   ```

3. Confirm sync logs show successful API updates to Sonarr/Radarr/Prowlarr.

### Cleanuparr and ntfy (optional)

1. Cleanuparr UI: `http://<media-vm-ip>:11011`
   - Add Sonarr/Radarr/download client API connections.
   - Start with conservative cleanup rules.
2. ntfy UI/API: `http://<media-vm-ip>:8099`
   - Add Sonarr/Radarr Connect notifications to chosen topics.
3. Verify with one test notification and one completed import event.

---

## Verification and troubleshooting

### Quick checks

- Rendered config with enabled overlays:

  ```bash
  media config
  ```

- Confirm unified path visibility:

  ```bash
  docker compose -f docker_compose/media/compose.yml exec sonarr ls /data/library
  docker compose -f docker_compose/media/compose.yml exec radarr ls /data/downloads
  ```

- Confirm no remote path mappings are needed:
  - In Sonarr and Radarr, **Settings -> Download Clients -> Remote Path Mappings** should remain empty for this deployment model.

### If imports fail

1. Re-check qBittorrent/SABnzbd paths and categories.
2. Re-check Sonarr/Radarr root folders and download client categories.
3. Confirm all apps use `/data/...` paths (not mixed `/downloads`, `/tv`, `/movies` views).
4. Check stack logs:

   ```bash
   media logs -f
   ```

5. If needed, restore from snapshot/backups and redeploy.

---

## See also

- [Chapter 2C — Media VM](Chapter2c-media.md): architecture, storage design, and why this stack exists.
- [Chapter 2 — VM overview](Chapter2-vms.md): VM inventory and lifecycle.
- [Chapter 3A — Core stack](Chapter3a-core-stack.md): same deployment pattern for another VM role.
