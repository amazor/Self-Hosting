# Chapter 3C — Media Stack: Configuration and Deployment

## Introduction

**Prerequisites:** [Chapter 2C (Media VM)](Chapter2c-media.md) (VM purpose, storage model, access model), [Chapter 2](Chapter2-vms.md) (VM overview), and [Chapter 3A (Core stack)](Chapter3a-core-stack.md) (deploy pattern).

Chapter 2C explains why the media VM is shaped the way it is. This chapter is the operational guide: what is inside `docker_compose/media/`, how `.env` drives overlays, how to deploy with `deploy.py`, and how to configure each UI so imports work without remote path mappings.

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
  - [Paths and identity](#paths-and-identity)
  - [VPN (required for torrent routing)](#vpn-required-for-torrent-routing)
  - [Plex integration](#plex-integration)
  - [*arr app authentication](#arr-app-authentication)
  - [Optional overlays](#optional-overlays)
- [Compose files: Notable details](#compose-files-notable-details)
- [Bootstrap script: What it does](#bootstrap-script-what-it-does)
- [Deploying the media stack](#deploying-the-media-stack)
  - [Path 1: Manual (on the Media VM)](#path-1-manual-on-the-media-vm)
  - [Path 2: Repo deploy script (`deploy.py`)](#path-2-repo-deploy-script-deploypy)
- [After first run](#after-first-run)
- [UI configuration how-tos](#ui-configuration-how-tos)
  - [qBittorrent](#qbittorrent)
  - [SABnzbd (optional)](#sabnzbd-optional)
  - [Sonarr](#sonarr)
  - [Radarr](#radarr)
  - [Prowlarr and FlareSolverr](#prowlarr-and-flaresolverr)
  - [Bazarr (optional)](#bazarr-optional)
  - [Recyclarr](#recyclarr)
  - [Cleanuparr (optional)](#cleanuparr-optional)
  - [ntfy (optional)](#ntfy-optional)
- [Verification and troubleshooting](#verification-and-troubleshooting)
- [See also](#see-also)

---

## What's in `docker_compose/media/`

| File or script | Purpose |
|----------------|---------|
| **compose.yml** | Base services: ExpressVPN, qBittorrent, Sonarr, Radarr, Prowlarr, FlareSolverr |
| **compose.sabnzbd.yml** | Optional Usenet downloader overlay |
| **compose.bazarr.yml** | Optional subtitle automation overlay |
| **recyclarr.example.yml** / **recyclarr.secrets.example.yml** | Example YAML configs for Recyclarr; bootstrap copies these into `config/recyclarr/` when `ENABLE_RECYCLARR=1`, only if target missing (same idea as `.env` from `.env.example`) |
| **compose.recyclarr.yml** | Optional Recyclarr overlay — TRaSH quality/format sync daemon |
| **compose.cleanuparr.yml** | Optional queue/download hygiene overlay |
| **compose.ntfy.yml** | Optional lightweight push notifications overlay |
| **.env.example** | Template for required values and optional feature toggles |
| **bootstrap.py** | Idempotent first-run checks: env validation, mount checks, config directory creation, VPN guardrail |
| **setup_media_apps.py** | Post-deploy automation via API: Prowlarr indexers (13 public torrent sites) + FlareSolverr proxy, qBittorrent categories and TRaSH settings, Sonarr/Radarr root folders + download clients + TRaSH naming, Prowlarr app sync, Plex library refresh connection (when `PLEX_HOST` and `PLEX_TOKEN` are set). Called automatically by `deploy.py`. |

All overlays are selected from `.env` by `ENABLE_*` flags and are automatically included by `deploy.py` and the generated `media` shell helper.

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
| **EXPRESSVPN_CODE** | ExpressVPN activation code (required). Get from https://www.expressvpn.com/setup. |
| **EXPRESSVPN_SERVER** | Server region or `smart` (auto-select nearest). Default: `smart`. |
| **EXPRESSVPN_PROTOCOL** | VPN protocol: `lightwayudp` (default, fastest), `lightwaytcp`, `wireguard`, `openvpntcp`, `openvpnudp`, `auto`. |
| **EXPRESSVPN_ALLOW_LAN** | Allow LAN access while Network Lock is on. Default: `true`. |
| **EXPRESSVPN_LAN_CIDR** | Comma-separated LAN subnets for return routes (optional). |

The activation code is required and validated by `bootstrap.py` and `deploy.py`. See the [ExpressVPN container docs](https://github.com/Misioslav/expressvpn) for available servers and protocols.

### Plex integration

| Variable | Purpose |
|----------|---------|
| **PLEX_HOST** | LAN IP or hostname of the Accelerated VM running Plex (e.g. `192.168.1.140`). Used by `setup_media_apps.py` to register a Plex notification connection in Sonarr and Radarr so they trigger a library refresh on every import. |
| **PLEX_TOKEN** | Permanent Plex API token (`X-Plex-Token`). Same token used in `accelerated/.env`. See [How to find your Plex token](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/). |

Both are optional — if either is absent, `setup_media_apps.py` skips Plex setup and logs a message. Add them and re-run `python3 setup_media_apps.py` to wire up Plex at any time (idempotent).

### *arr app authentication

| Variable | Purpose |
|----------|---------|
| **ARR_AUTH_METHOD** | Controls Sonarr/Radarr/Prowlarr authentication. Default: `External` (delegates to Authentik/Caddy forward auth). Set to `Forms` for built-in username/password login. Ref: [Authentik Sonarr integration](https://integrations.goauthentik.io/media/sonarr/). |

### Optional overlays

Set the following to `1` to enable extra compose files:

- `ENABLE_RECYCLARR` — **enabled by default** (`1` in `.env.example`). Adds Recyclarr overlay for TRaSH quality/format sync. API keys are auto-populated from pre-seeded `config.xml` by bootstrap.
- `RECYCLARR_CRON` — Recyclarr daemon schedule (default `@weekly`). Only relevant when `ENABLE_RECYCLARR=1`.
- `ENABLE_CLEANUPARR` — queue/download hygiene. Monitors for stalled, slow, and failed downloads and auto-removes + re-searches using a strike system. Requires one-time UI setup (connection details are printed by deploy). Ref: [Cleanuparr](https://cleanuparr.github.io/Cleanuparr/).
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
| **vpn (ExpressVPN)** | VPN egress boundary for qBittorrent traffic ([misioslav/expressvpn](https://github.com/Misioslav/expressvpn)) |
| **qbittorrent** | Torrent downloader running via `network_mode: service:vpn` |
| **sonarr / radarr** | TV and movie automation |
| **prowlarr** | Indexer management and sync to *arr apps |
| **flaresolverr** | Optional challenge bypass for protected indexers |
| **sabnzbd** *(overlay)* | Usenet downloader |
| **bazarr** *(overlay)* | Subtitle automation for Sonarr/Radarr libraries |
| **recyclarr** *(overlay)* | TRaSH quality/format sync daemon (weekly cron by default) |
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

`bootstrap.py` is safe to rerun. It prepares the stack and guardrails, but it does not run `docker compose up -d` by itself.

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
10. Pre-seed `config.xml` for Sonarr, Radarr, and Prowlarr (only if config.xml does not already exist). Sets `AuthenticationMethod=External` (delegates auth to Authentik/Caddy forward auth) and `AuthenticationRequired=DisabledForLocalAddresses` (so inter-container API calls work without credentials). Also generates a random API key for each app. Set `ARR_AUTH_METHOD=Forms` in `.env` to use app-level username/password login instead. Ref: [Authentik Sonarr integration](https://integrations.goauthentik.io/media/sonarr/).
11. Pre-seed `qBittorrent.conf` with a known WebUI password (from `QBITTORRENT_PASSWORD`, default: `adminadmin`). This prevents qBittorrent 4.6.1+ from generating a random temp password, so post-deploy automation and the Prometheus exporter can authenticate immediately.
12. Copy `recyclarr.example.yml` and `recyclarr.secrets.example.yml` into `config/recyclarr/` **only when `ENABLE_RECYCLARR=1`**. Target files are only written if they do not already exist (same as `.env` from `.env.example`).
13. Auto-populate API keys: reads the generated keys from the pre-seeded *arr `config.xml` files and injects them into Recyclarr secrets and `.env` (for Prometheus exporters). No manual API key filling is needed.
14. Print stack summary.

### Flags

| Flag | Effect |
|------|--------|
| **--non-interactive** | Skip prompts (used by `deploy.py`). |
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
   python3 bootstrap.py
   ```

4. Start services (base only, or base + overlays):

   ```bash
   docker compose -f compose.yml up -d
   ```

### Path 2: Repo deploy script (`deploy.py`)

From repo root:

1. Ensure `docker_compose/media/.env` exists and required values are set (`EXPRESSVPN_CODE`, `MEDIA_ROOT`, `CONFIG_ROOT`).
2. Deploy:

   ```bash
   python3 deploy.py media
   ```

`python3 deploy.py media` will:

- validate required env vars for media
- run `docker_compose/media/bootstrap.py` in deploy mode
- include overlay compose files based on `ENABLE_*` flags
- run `docker compose up -d`
- **post-deploy automation:** run `setup_media_apps.py` (adds Prowlarr indexers + FlareSolverr proxy, configures qBittorrent categories and [TRaSH-recommended settings](https://trash-guides.info/Downloaders/qBittorrent/Basic-Setup/), sets up Sonarr/Radarr root folders + download clients + TRaSH naming, and connects Prowlarr app sync to Sonarr/Radarr — all via API), then Recyclarr initial sync via `docker compose exec` (quality profiles including anime, custom formats — only when `ENABLE_RECYCLARR=1`)
- create/update `~/media` symlink to this stack
- install shell helpers so you can run media commands from any directory

The `media` helper is a pure compose pass-through (like `core` and `accelerated`). Common usage after deploy:

```bash
media ps
media logs -f
media up -d
media down
```

Optional deploy flags:

- `python3 deploy.py media --force` for testing or temporary bypass of guardrails.
- `python3 deploy.py media --non-interactive` for automation.

---

## After first run

**Authentication:** Bootstrap pre-seeds Sonarr, Radarr, and Prowlarr with `AuthenticationMethod=External` and a generated API key. When these apps are behind Authentik SSO (via Caddy forward auth), they will not show their own login page — authentication is handled by Authentik. If you are NOT using Authentik, set `ARR_AUTH_METHOD=Forms` in `.env` before first bootstrap so the apps use their built-in login page.

**What's already automated:** After a successful `deploy.py media` run, the following are configured automatically — no manual UI work needed:

- **qBittorrent** — WebUI password pre-seeded by bootstrap; save path, auto TMM, categories (`tv`, `movies`, `anime`), and [TRaSH-recommended settings](https://trash-guides.info/Downloaders/qBittorrent/Basic-Setup/) (TCP-only protocol, encryption, seeding limits disabled, UPnP off, CSRF off) via `setup_media_apps.py`
- **Prowlarr** — 13 public torrent indexers (1337x, TPB, EZTV, KAT, LimeTorrents, YTS, Torrent Downloads, Knaben, Nyaa.si, SubsPlease, Tokyo Toshokan, Shana Project) and a FlareSolverr proxy with a `flaresolverr` tag for Cloudflare-blocked sites, via `setup_media_apps.py`. Nyaa.si is configured with the "Anime - English-translated" category filter so results are pre-filtered for English subs at the indexer level
- **Sonarr/Radarr** — root folders, qBittorrent download clients, and TRaSH naming (including [anime naming format](https://trash-guides.info/Sonarr/Sonarr-recommended-naming-scheme/) with audio language and 10-bit tokens) via `setup_media_apps.py`; quality profiles (including anime), custom formats, and quality settings via Recyclarr (when enabled)
- **Prowlarr → Sonarr/Radarr app sync** via `setup_media_apps.py`
- **Plex library refresh connection** — Sonarr and Radarr are wired to call Plex's API after every import, so the library updates immediately. Requires `PLEX_HOST` and `PLEX_TOKEN` in `.env`. If absent at deploy time, add them and re-run `python3 setup_media_apps.py`
- **Recyclarr** — initial sync triggered on first deploy (via `docker compose exec`), then runs on its cron schedule (`RECYCLARR_CRON`, default `@weekly`)

Recommended post-deploy checks:

1. Confirm VPN is healthy and qBittorrent UI is reachable.
2. Verify qBittorrent categories and save paths look correct (should be pre-configured).
3. Verify Sonarr/Radarr root folders and download clients are present (should be set by `setup_media_apps.py`).
4. Verify Prowlarr indexers and app sync (should be configured by `setup_media_apps.py`).
5. If enabled, configure Cleanuparr connections in its UI (`http://<media-vm-ip>:11011`) — deploy prints the exact values to paste. See [Cleanuparr setup](#cleanuparr-optional).
6. If enabled, configure SABnzbd in its UI (not yet covered by automation).
7. If enabled, verify Bazarr shows Sonarr/Radarr connected and the language profile assigned (automated by `setup_media_apps.py`).

---

## UI configuration how-tos

### qBittorrent

Reference: [TRaSH qBittorrent Basic Setup](https://trash-guides.info/Downloaders/qBittorrent/Basic-Setup/)

**Mostly automated.** Bootstrap pre-seeds the WebUI password and `setup_media_apps.py` applies TRaSH-recommended settings and categories via API. Verify rather than configure:

1. Open UI on `http://<media-vm-ip>:8080` (default login: `admin` / `adminadmin`, or whatever `QBITTORRENT_PASSWORD` is set to in `.env`).
2. **Options → Downloads**: save path should be `/data/downloads/qbittorrent/`, incomplete path enabled.
3. **Options → Downloads → Saving Management**: Default Torrent Management Mode should be **Automatic**.
4. **Options → Connection**: protocol should be **TCP**, UPnP and random port should be **off**, listening port `6881`.
5. **Options → BitTorrent**: encryption **Allow** (prefer), anonymous mode **off**, seeding limits **off**.
6. **Categories** (left sidebar): `tv` → `completed/tv`, `movies` → `completed/movies`, `anime` → `completed/anime`.
7. Verify by starting a test download and confirming files land under the category folder.

**Not automated (personal preference):** global speed limits — TRaSH recommends 70–80% of your max upload/download if sharing the connection. Set in **Options → Speed**.

### SABnzbd (optional)

Reference: [TRaSH SABnzbd](https://trash-guides.info/Downloaders/SABnzbd/)

**Note:** SABnzbd is not managed by Recyclarr or `setup_media_apps.py`. Configure SABnzbd **paths and categories** in the SABnzbd UI, then add it as a download client in Sonarr/Radarr manually.

1. Open `http://<media-vm-ip>:8082`.
2. **Config -> Folders**:
   - Completed Download Folder: `/data/downloads/sabnzbd/completed`
   - Temporary Download Folder: `/data/downloads/sabnzbd/tmp`
3. **Config -> Categories**:
   - `tv` -> `tv` (relative folder)
   - `movies` -> `movies` (relative folder)

   **Important (TRaSH):** Category folders are specified **relative** to the Completed Download Folder — enter just `tv` or `movies`, not the full path. This is different from qBittorrent, which uses a default save path plus category subdirectories. See [TRaSH SABnzbd — Paths and Categories](https://trash-guides.info/Downloaders/SABnzbd/Paths-and-Categories/).
4. Verify with one test NZB and confirm destination category path.

### Sonarr

Reference: [TRaSH Sonarr](https://trash-guides.info/Sonarr/)

**Automated by deploy:** `setup_media_apps.py` configures root folders, download clients, TRaSH naming, and Plex library refresh connection on deploy — skip steps 2–5 for those. If Recyclarr is enabled, it syncs TRaSH quality definitions, custom formats, and quality profiles — skip step 6.

1. Open `http://<media-vm-ip>:8989`.
2. **Settings -> Media Management**:
   - Enable Show Advanced
   - Enable Rename Episodes
   - Enable Analyze video files
3. **Settings -> Media Management -> Root Folders**:
   - `/data/library/tv`
   - `/data/library/anime` (if used)
4. **Settings -> Download Clients**:
   - qBittorrent: host `vpn`, port `8080`, category `tv` (qBittorrent uses `network_mode: service:vpn`, so it's reachable at the `vpn` hostname)
   - SABnzbd (if enabled): host `sabnzbd`, port `8080`, category `tv`
5. **Settings -> Connect**:
   - Plex Media Server: host `<accelerated-vm-ip>`, port `32400`, token from `.env`. **Automated** when `PLEX_HOST` and `PLEX_TOKEN` are set in `.env`.
6. **Settings -> Profiles / Custom Formats**:
   - Apply TRaSH quality settings, profiles, and custom formats.
7. Verify by adding a test series and checking import succeeds without remote path mapping.

### Radarr

Reference: [TRaSH Radarr](https://trash-guides.info/Radarr/)

**Automated by deploy:** `setup_media_apps.py` configures root folders, download clients, TRaSH naming, and Plex library refresh connection on deploy — skip steps 2–5 for those. If Recyclarr is enabled, it syncs TRaSH quality definitions, custom formats, and quality profiles — skip step 6.

1. Open `http://<media-vm-ip>:7878`.
2. **Settings -> Media Management**:
   - Enable Show Advanced
   - Enable Rename Movies
   - Enable Analyze video files
3. Add root folder: `/data/library/movies`.
4. Add download clients:
   - qBittorrent category `movies`
   - SABnzbd category `movies` (if enabled)
5. **Settings -> Connect**:
   - Plex Media Server: host `<accelerated-vm-ip>`, port `32400`, token from `.env`. **Automated** when `PLEX_HOST` and `PLEX_TOKEN` are set in `.env`.
6. Apply TRaSH naming, quality profiles, and custom formats.
7. Verify by adding one test movie and confirming import path under `/data/library/movies`.

### Prowlarr and FlareSolverr

Reference: [TRaSH Prowlarr](https://trash-guides.info/Prowlarr/)

**Automated by deploy:** `setup_media_apps.py` configures Prowlarr indexers, FlareSolverr proxy, and app sync (Sonarr/Radarr connections) automatically during deploy. Recyclarr does not manage Prowlarr.

1. Open Prowlarr on `http://<media-vm-ip>:9696`.
2. Add Sonarr and Radarr under **Settings -> Apps**.
3. Add indexers and run test.
4. Only configure proxy/FlareSolverr for indexers that need challenge handling.
5. If needed, add FlareSolverr endpoint (`http://flaresolverr:8191`) in indexer settings.
6. Verify indexer sync appears in Sonarr/Radarr.

### Bazarr (optional)

Reference: [TRaSH Bazarr](https://trash-guides.info/Bazarr/), [TRaSH Suggested Scoring](https://trash-guides.info/Bazarr/Bazarr-suggested-scoring/)

**Automated by deploy** — when `ENABLE_BAZARR=1`, `setup_media_apps.py` configures Bazarr via its API:

- **Sonarr/Radarr connections** — `http://sonarr:8989` and `http://radarr:7878` with API keys from `config.xml`
- **Language profile** — "English + Hebrew" profile assigned to all existing and new series/movies
- **Minimum scores** — 90 (series) and 80 (movies) per TRaSH suggested scoring
- **Subtitle sync** — automatic synchronization with thresholds at 96 (series) / 86 (movies)
- **Adaptive searching** — slows down after 1 week of no results, spaces searches 4 weeks apart
- **Subtitle upgrades** — enabled with a 7-day upgrade window
- **Providers** — five zero-credential providers: Gestdown, Wizdom (Hebrew), Podnapisi, YIFY Subtitles, Animetosho

After deploy, verify by checking that Bazarr's UI (`http://<media-vm-ip>:6767`) shows Sonarr/Radarr connected and the language profile assigned.

**Additional providers (require accounts):** if the default zero-credential providers are insufficient, you can enable these manually in the Bazarr UI (Settings > Providers):

| Provider | Credentials | Coverage | Notes |
|----------|-------------|----------|-------|
| OpenSubtitles.com | Free account (username + password) | Largest DB, English + Hebrew + 180 languages | 20 downloads/day free; $20/yr VIP for 1000/day |
| Ktuvit | Free account + hashed password | Hebrew-specific | Requires generating hashed password via external script |
| Addic7ed | Free account (username + password) | English TV | Rate limited; Gestdown is a better free alternative |
| SubSource | Free API key (from profile page) | Multi-language | Newer provider |
| Subscene | Free account | Multi-language, good non-English | May require anti-captcha |

### Recyclarr

Reference: [TRaSH Guide Sync](https://trash-guides.info/Guide-Sync/)

**Recyclarr** syncs TRaSH Guide content (Custom Formats, Quality Profiles, Quality Definitions / file size) into Sonarr and Radarr from YAML configuration. Docs: [Recyclarr](https://recyclarr.dev/).

> ### 🧠 Design Note: Why not Buildarr?
> [Buildarr](https://buildarr.github.io/) is a declarative *arr configurator that can manage root folders, download clients, naming, and quality profiles from YAML. Earlier versions of this stack used it. We replaced it because Buildarr is unmaintained (last release ~2023) and its Pydantic models cannot parse newer Sonarr v4 API responses (`colonReplacementFormat` enum mismatch causes a hard crash on startup). Rather than pin old app versions or wait for a fix, we moved the settings Buildarr handled — root folders, download clients, TRaSH naming, and Prowlarr app sync — into `setup_media_apps.py`, which calls the *arr APIs directly and is version-agnostic. Recyclarr (actively maintained, v8) handles everything else: quality profiles, custom formats, and quality definitions.

- Config lives in `${CONFIG_ROOT}/recyclarr/recyclarr.yml` (v8 format with guide-backed quality profiles via `trash_id`).
- API keys live in `${CONFIG_ROOT}/recyclarr/secrets.yml` (auto-populated by bootstrap from pre-seeded `config.xml`).
- Recyclarr is **optional** — it lives in `compose.recyclarr.yml`, enabled by `ENABLE_RECYCLARR=1` (default). It runs as a daemon with `RECYCLARR_CRON` (default `@weekly`). On first deploy, `deploy.py` triggers an initial sync via `docker compose exec`; after that, the cron schedule handles recurring syncs.

**What's automated by deploy** (see also the "Automated by deploy" notes in each app section above):

- **`setup_media_apps.py`** (always) — Prowlarr indexers + FlareSolverr proxy, qBittorrent preferences + categories, Sonarr/Radarr root folders + qBittorrent download clients + TRaSH naming, Prowlarr app sync to Sonarr/Radarr, Bazarr connections + language profile + providers + scoring (when `ENABLE_BAZARR=1`)
- **Recyclarr** (when enabled) — TRaSH quality profiles for TV (WEB-1080p) and movies (HD Bluray + WEB), **plus anime** (Remux-1080p with `[Streaming Services] Asian` CF group for Crunchyroll/Funimation/HIDIVE scoring), quality definitions, custom formats, and Golden Rule HD scoring. Initial sync runs on first deploy; subsequent syncs follow the cron schedule.

Because deploy automates root folders, download clients, naming, quality profiles, custom formats, and Bazarr subtitle configuration, you can skip the corresponding manual UI steps in Sonarr, Radarr, Prowlarr, and Bazarr. qBittorrent preferences and categories are also automated. SABnzbd and Cleanuparr still require manual UI configuration (Cleanuparr has no public API; deploy prints the connection details to paste into its UI).

**Example configs:** In `docker_compose/media/` you will find `recyclarr.example.yml` and `recyclarr.secrets.example.yml`. Bootstrap copies these into `config/recyclarr/` **only when `ENABLE_RECYCLARR=1`** and only if the target does not already exist. **API keys are auto-populated** from the pre-seeded *arr `config.xml` files — no manual key editing is needed.

1. After deploy, verify config files exist (bootstrap creates and populates them automatically):
   - `${CONFIG_ROOT}/recyclarr/recyclarr.yml` (when `ENABLE_RECYCLARR=1`)
   - `${CONFIG_ROOT}/recyclarr/secrets.yml` (API keys — auto-populated from `config.xml`)
2. No manual sync commands are needed — `deploy.py` handles the initial Recyclarr sync automatically. To force a Recyclarr re-sync manually:

   ```bash
   media exec recyclarr recyclarr sync
   ```

3. Confirm sync logs show successful API updates to Sonarr/Radarr.

### Cleanuparr (optional)

Reference: [Cleanuparr docs](https://cleanuparr.github.io/Cleanuparr/)

Cleanuparr monitors Sonarr/Radarr download queues and automatically handles stalled, slow, or failed downloads using a **strike system**. When a download accumulates enough strikes (configurable), Cleanuparr removes it from the download client, blocklists the release in the *arr app, and triggers a replacement search. It also handles seeding rules, orphan removal, and malware blocking.

> ### 🧠 Why Cleanuparr?
> Without Cleanuparr, stalled torrents with zero seeders sit in the queue indefinitely. You'd need to manually remove and blocklist each one, then trigger a new search. Cleanuparr automates this loop: detect → strike → remove → blocklist → re-search. Combined with `TORRENT_MIN_SEEDERS` (which prevents grabbing low-seed torrents in the first place), it keeps the download pipeline flowing without babysitting.

**Not automatable:** Cleanuparr stores its configuration in SQLite and has no public API for adding connections. After deploy, `setup_media_apps.py` prints the exact connection details (API keys, hostnames, ports) so you can paste them into the UI in under a minute.

1. Open Cleanuparr UI: `http://<media-vm-ip>:11011`
2. Add **Sonarr** connection:
   - Host: `http://sonarr:8989`
   - API Key: from `config/sonarr/config.xml` (printed by deploy)
3. Add **Radarr** connection:
   - Host: `http://radarr:7878`
   - API Key: from `config/radarr/config.xml` (printed by deploy)
4. Add **qBittorrent** download client:
   - Host: `http://vpn:8080` (qBittorrent uses `network_mode: service:vpn`)
   - Username / Password: from `.env` (`QBITTORRENT_USERNAME` / `QBITTORRENT_PASSWORD`, default: `admin` / `adminadmin`)
5. Enable **Queue Cleaner** and configure strike thresholds. Recommended: start with defaults, tune later based on your indexer health and seeder counts.
6. Optionally enable **Download Cleaner** (seeding time/ratio rules) and **Malware Blocker**.
7. Verify by checking the Cleanuparr dashboard — it should show connected services and begin monitoring the queue.

### ntfy (optional)

1. ntfy UI/API: `http://<media-vm-ip>:8099`
   - Add Sonarr/Radarr Connect notifications to chosen topics.
2. Verify with one test notification and one completed import event.

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
- [Chapter 3D — Accelerated stack](Chapter3d-accelerated-stack.md): Plex (playback VM), `setup_accelerated_apps.py`, and the two-token model (`PLEX_CLAIM` vs `PLEX_TOKEN`).
- [Local testing guide](Local-testing-guide.md): End-to-end test of all stacks on a four-VM LAN setup.
