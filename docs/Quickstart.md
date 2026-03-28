# Quickstart — Deploy the Homelab from Scratch

A concise, step-by-step reference for going from a bare Proxmox host to a fully running homelab. No journal, no "why" — just what to do, in the right order.

For reasoning and design decisions, see the chapter docs ([Chapter 0](Chapter0-hardware.md) through [Chapter 3D](Chapter3d-accelerated-stack.md)).

---

## Table of contents
- [Phase 0: Gather prerequisites](#phase-0-gather-prerequisites)
  - [Hardware](#hardware)
  - [Accounts and credentials](#accounts-and-credentials)
- [Phase 1: Proxmox template](#phase-1-proxmox-template)
- [Phase 2: Create VMs and assign IPs](#phase-2-create-vms-and-assign-ips)
  - [Clone each VM from template 9000](#clone-each-vm-from-template-9000)
  - [Record your IPs](#record-your-ips)
  - [GPU passthrough (accelerated only)](#gpu-passthrough-accelerated-only)
- [Phase 3: Fill out .env files](#phase-3-fill-out-env-files)
  - [Auto-detect what you can](#auto-detect-what-you-can)
  - [Core .env](#core-env)
  - [Monitoring .env](#monitoring-env)
  - [Media .env](#media-env)
  - [Accelerated .env](#accelerated-env)
- [Phase 4: Deploy stacks (order matters)](#phase-4-deploy-stacks-order-matters)
  - [Step 1 — Monitoring](#step-1--monitoring)
  - [Step 2 — Core](#step-2--core)
  - [Step 3 — Media](#step-3--media)
  - [Step 4 — Accelerated](#step-4--accelerated)
- [Phase 5: Post-deploy wiring](#phase-5-post-deploy-wiring)
- [Phase 6: Verify everything](#phase-6-verify-everything)
- [Quick reference](#quick-reference)

---

## Phase 0: Gather prerequisites

### Hardware

| Item | Role |
|------|------|
| Proxmox host (e.g. Beelink EQi13) | Hypervisor — runs all VMs |
| NAS (e.g. Synology DS220+) | Shared storage — media library, photos (optional for local-only testing) |
| Ethernet connection | Wired between Proxmox host, NAS, and your router |
| SSH key pair | Cloud-Init injects this into every VM |

### Accounts and credentials

Gather these **before** you start deploying. Some are time-sensitive (Plex claim expires in 4 minutes).

| Credential | Where to get it | Used by | When you need it |
|------------|-----------------|---------|------------------|
| **Public domain** | Any registrar (Namecheap, Cloudflare, etc.) | Core (Caddy, Authentik, public hostnames) | Before core deploy |
| **ExpressVPN activation code** | https://www.expressvpn.com/setup | Media (VPN container) | Before media deploy |
| **Plex claim token** | https://plex.tv/claim (expires in ~4 min) | Accelerated (first Plex run) | Immediately before accelerated deploy |
| DDNS provider credentials | Your registrar or DDNS service (optional) | Core (ddclient, if enabled) | Before core deploy (if using DDNS) |
| Authentik bootstrap password | You choose one | Core (SSO admin) | Before core deploy |
| Authentik bootstrap token | You generate one (any long random string) | Core (automated SSO blueprint) | Before core deploy (optional, enables auto-SSO) |
| Grafana admin password | You choose one | Monitoring (Grafana login) | Before monitoring deploy |
| Immich DB password | You choose one (**alphanumeric only** — no special chars) | Accelerated (Immich Postgres) | Before accelerated deploy |
| Usenet provider credentials | Your Usenet service (e.g. Astraweb, Eweka) — optional | Media (SABnzbd, if enabled) | Before media deploy (if using Usenet) |
| NZBGeek API key | https://nzbgeek.info/ — optional | Media (Prowlarr Usenet indexer) | Before media deploy (if using Usenet) |

> **Tip:** Generate strong random values now:
> - Most passwords: `openssl rand -base64 36`
> - Immich `DB_PASSWORD`: `openssl rand -hex 24` (alphanumeric only — base64 produces `/`, `+`, `=` which break Immich)

---

## Phase 1: Proxmox template

Full details: [Chapter 1 (Proxmox)](Chapter1-proxmox.md).

1. Install Proxmox VE on your host with a **static IP** (e.g. `192.168.1.50`).
2. Run the post-install script (community-scripts) to clean up repos.
3. Upload `cloud-init-config.yaml` and `inject-proxmox-node-hook.sh` from `proxmox/snippets/` to Proxmox (`local:snippets/`).
4. Run `create_template.sh` on the Proxmox host:

```bash
bash create_template.sh
```

This creates template **VMID 9000** (`debian-13-docker-cloudinit`) with Docker, Compose, NFS tools, qemu-guest-agent, and the Cloud-Init config baked in.

5. Set your **SSH public key** on template 9000 under **Cloud-Init → SSH public key** in the Proxmox GUI.

---

## Phase 2: Create VMs and assign IPs

### Clone each VM from template 9000

For each VM in this table, right-click template 9000 → **Clone** → set VMID, name, CPU, and RAM:

| VM | VMID | vCPU | RAM | After cloning |
|----|------|------|-----|---------------|
| `core` | 110 | 2 | 4 GB | [Chapter 2A](Chapter2a-core.md) |
| `monitoring` | 120 | 2 | 6 GB | [Chapter 2B](Chapter2b-monitoring.md) |
| `media` | 220 | 4 | 8 GB | [Chapter 2C](Chapter2c-media.md) |
| `accelerated` | 230 | 4 | 8 GB | [Chapter 2D](Chapter2d-accelerated.md) |

For each clone:

1. **Hardware** → set vCPU and RAM from the table.
2. **Cloud-Init** → confirm your SSH key is present. Set **IP config** to either:
   - **DHCP** (default) — note the IP after first boot, or
   - **Static** — assign a fixed IP now (recommended so you can fill `.env` files immediately).
3. **Start** the VM. Wait 1–2 minutes for Cloud-Init to finish (Docker install, repo clone, `setup_env.py`).
4. **Verify:**

```bash
ssh mazora@<VM_IP>
docker --version && systemctl status qemu-guest-agent --no-pager
```

5. **Snapshot** the clean VM before deploying anything.

### Record your IPs

Fill in this table as you boot each VM — you'll need every IP for the `.env` files:

| VM | IP address | Ports exposed |
|----|------------|---------------|
| `core` | __________ | 80, 443 (router forwards these) |
| `monitoring` | __________ | 3000 (Grafana), 3001 (Uptime Kuma), 3100 (Loki), 9090 (Prometheus) |
| `media` | __________ | 8989 (Sonarr), 7878 (Radarr), 9696 (Prowlarr), 8080 (qBittorrent) |
| `accelerated` | __________ | 32400 (Plex), 2283 (Immich) |

### GPU passthrough (accelerated only)

If using hardware transcoding, complete this **before** deploying the accelerated stack.

1. **Proxmox host** — enable IOMMU and VFIO, blacklist `i915` and `snd_hda_intel`. Full steps: [Chapter 1A (GPU passthrough)](Chapter1a-gpu-passthrough.md).
2. Assign the GPU to VM 230:

```bash
qm set 230 -hostpci0 0000:00:02.0,rombar=0
```

3. **Inside the accelerated VM** — install VA-API driver and verify:

```bash
sudo apt install -y intel-media-va-driver vainfo
vainfo
ls /dev/dri
```

---

## Phase 3: Fill out .env files

Each stack has a `.env.example` at `docker_compose/<stack>/.env.example`. Cloud-Init runs `setup_env.py` on first boot, which pre-fills auto-detectable values (hostname, PUID/PGID, Docker GID, LAN IP). You still need to fill in secrets, IPs, and domain names.

**Timezone:** Every stack has `TZ=Etc/UTC` by default. Set it to your local timezone (e.g. `America/New_York`, `Europe/London`) in each `.env` so container logs and schedules use the right time.

### Auto-detect what you can

On each VM, if `setup_env.py` hasn't already run:

```bash
cd ~/self-hosting
python3 scripts/setup_env.py
```

This pre-fills: `VM_HOSTNAME`, `PUID`, `PGID`, `DOCKER_GID`, `DNS_BIND_IP` (core), `AUTHENTIK_SECRET_KEY` (core), `EXPRESSVPN_LAN_CIDR` (media).

### Core .env

File: `docker_compose/core/.env.example` → deploy copies to `.env`

| Variable | What to set | Example |
|----------|-------------|---------|
| `AUTHENTIK_SECRET_KEY` | Long random string (auto-generated by `setup_env.py`) | `aB3x...` (keep the generated value) |
| `AUTHENTIK_POSTGRES_PASSWORD` | Strong random password | `openssl rand -base64 24` |
| `AUTHENTIK_BOOTSTRAP_EMAIL` | Your admin email | `admin@example.com` |
| `AUTHENTIK_BOOTSTRAP_PASSWORD` | Your chosen SSO admin password | — |
| `AUTHENTIK_BOOTSTRAP_TOKEN` | Long random string (enables auto blueprint) | `openssl rand -base64 36` |
| `PUBLIC_BASE_DOMAIN` | Your public domain | `example.com` |
| `AUTHENTIK_FQDN` | SSO subdomain | `auth.example.com` |
| `WHOAMI_FQDN` | Echo service subdomain | `whoami.example.com` |
| `DNS_BIND_IP` | This VM's LAN IP (auto-detected) | `192.168.1.110` |
| `DNS_UPSTREAM_1` | Primary upstream DNS resolver | `1.1.1.1` (Cloudflare, default) |
| `DNS_UPSTREAM_2` | Secondary upstream DNS resolver | `1.0.0.1` (Cloudflare, default) |
| `DNS_LOCAL_DOMAIN` | Internal domain for local DNS | `lab.arpa` |
| `DNS_LOCAL_RECORDS` | Map hostnames to VM IPs | See below |
| `CADDY_USE_INTERNAL_TLS` | Set `true` for LAN-only / no public domain (Caddy uses its own CA instead of Let's Encrypt) | `true` for testing, unset for production |
| `CADDY_EXTRA_SERVICES` | Reverse proxy routes for all apps (see [Phase 5 §2](#2-add-caddy-routes-for-services-behind-the-reverse-proxy) for full format) | `sonarr:192.168.1.220:8989:sso,...` |
| `AUTHENTIK_ADMIN_USERS` | Usernames auto-assigned to "Homelab Users" group (full access) on deploy. Requires `AUTHENTIK_BOOTSTRAP_TOKEN`. | `akadmin,mazora` |
| `AUTHENTIK_MEDIA_USERS` | Usernames auto-assigned to "Homelab Media" group (Sonarr + Radarr only) on deploy | `mazora` |
| `LOKI_URL` | `http://<MONITORING_IP>:3100` | `http://192.168.1.120:3100` |
| `PROMETHEUS_URL` | `http://<MONITORING_IP>:9090` | `http://192.168.1.120:9090` |
| `ENABLE_DDNS` | `1` if using dynamic DNS, `0` otherwise | `0` |
| `ENABLE_OBSERVABILITY` | `1` (default) — set `0` to skip sidecars | `1` |

**`DNS_LOCAL_RECORDS` format** (comma-separated `name:ip` pairs):

```
core:192.168.1.110,monitoring:192.168.1.120,media:192.168.1.220,accelerated:192.168.1.230
```

### Monitoring .env

File: `docker_compose/monitoring/.env.example`

| Variable | What to set | Example |
|----------|-------------|---------|
| `GRAFANA_ADMIN_PASSWORD` | Strong password (not `admin`) | — |
| `GF_SERVER_ROOT_URL` | Grafana's external URL (set when behind reverse proxy so OAuth redirects and links work) | `https://grafana.example.com` |
| `PROMETHEUS_RETENTION` | How long to keep metrics | `30d` |
| `LOKI_RETENTION` | How long to keep logs | `30d` |
| `SCRAPE_TARGETS` | `name:ip` pairs for remote node-exporter | `core:192.168.1.110,media:192.168.1.220,accelerated:192.168.1.230` |
| `PLEX_EXPORTER_TARGETS` | `name:ip` for Plex exporter | `accelerated:192.168.1.230` |
| `EXPRESSVPN_EXPORTER_TARGETS` | `name:ip` for VPN exporter | `media:192.168.1.220` |
| `SCRAPARR_EXPORTER_TARGETS` | `name:ip` for Scraparr exporter | `media:192.168.1.220` |
| `QBITTORRENT_EXPORTER_TARGETS` | `name:ip` for qBittorrent exporter | `media:192.168.1.220` |

Exporter targets are only needed if you enable exporters on those stacks (`ENABLE_EXPORTERS=1` on media, `ENABLE_OBSERVABILITY=1` on accelerated).

### Media .env

File: `docker_compose/media/.env.example`

| Variable | What to set | Example |
|----------|-------------|---------|
| `MEDIA_ROOT` | Path to media storage (NFS mount or local) | `/mnt/media` |
| `EXPRESSVPN_CODE` | ExpressVPN activation code (**required**) | `XXXX...` from expressvpn.com/setup |
| `EXPRESSVPN_SERVER` | VPN server location | `smart` (default) |
| `EXPRESSVPN_LAN_CIDR` | LAN + Docker subnets exempted from VPN kill switch. **Must include Docker bridge subnets** or *arr apps lose connectivity to qBittorrent after VPN restart. Auto-filled by `setup_env.py`. | `192.168.1.0/24,172.18.0.0/16` |
| `PLEX_HOST` | Accelerated VM's LAN IP (for library refresh) | `192.168.1.230` |
| `PLEX_TOKEN` | Plex auth token (fill after accelerated deploy) | — |
| `ARR_AUTH_METHOD` | `External` (default) = delegates auth to Authentik/Caddy SSO. Set `Forms` if not using SSO. | `External` |
| `LOKI_URL` | `http://<MONITORING_IP>:3100` | `http://192.168.1.120:3100` |
| `PROMETHEUS_URL` | `http://<MONITORING_IP>:9090` | `http://192.168.1.120:9090` |
| `ENABLE_RECYCLARR` | `1` to sync TRaSH quality profiles | `1` |
| `ENABLE_EXPORTERS` | `1` for Prometheus exporters | `1` |
| `ENABLE_OBSERVABILITY` | `1` (default) | `1` |

**Optional overlays** — set to `1` to enable:

| Variable | What it adds |
|----------|-------------|
| `ENABLE_CLEANUPARR` | Auto queue cleanup (stalled/slow/failed downloads) |
| `ENABLE_SABNZBD` | Usenet download client (requires Usenet provider credentials — see below) |
| `ENABLE_BAZARR` | Subtitle automation |
| `ENABLE_NTFY` | Lightweight push notifications |

**Usenet (when `ENABLE_SABNZBD=1`):** Set these in the media `.env`:

| Variable | What to set |
|----------|-------------|
| `USENET_SERVER_HOST` | Provider hostname (e.g. `ssl-eu.astraweb.com`) |
| `USENET_SERVER_PORT` | `563` (SSL) |
| `USENET_SERVER_USERNAME` | Your Usenet account username |
| `USENET_SERVER_PASSWORD` | Your Usenet account password |
| `USENET_SERVER_CONNECTIONS` | Max connections (start at `30`, tune up) |
| `USENET_SERVER_SSL` | `1` (enabled) |
| `NZBGEEK_API_KEY` | NZBGeek indexer API key (from https://nzbgeek.info/) |

Optional: fill/backup server vars (`USENET_SERVER2_*`) for a second Usenet provider on a different backbone.

**NFS mounts (if using NAS):** Set these in the `.env` and bootstrap auto-configures fstab + mount:

| Variable | Example |
|----------|---------|
| `NFS_HOST` | `nas.local` or NAS IP |
| `NFS_MEDIA_EXPORT` | `/volume1/media` |

Or mount manually:

```bash
sudo mkdir -p /mnt/media
sudo mount -t nfs <NAS_IP>:/volume1/media /mnt/media
```

Add to `/etc/fstab` for persistence. Bootstrap validates the mount exists.

**Local-only (no NAS):** Create the directory structure manually:

```bash
sudo mkdir -p /mnt/media/downloads /mnt/media/library/tv /mnt/media/library/movies /mnt/media/library/anime
sudo chown -R 1000:1000 /mnt/media
```

### Accelerated .env

File: `docker_compose/accelerated/.env.example`

| Variable | What to set | Example |
|----------|-------------|---------|
| `MEDIA_LIBRARY_ROOT` | Path to media library (NFS mount or local, read-only is fine) | `/mnt/media/library` |
| `IMMICH_UPLOAD_ROOT` | Path to photos storage | `/mnt/photos/library` |
| `IMMICH_DB_ROOT` | Immich Postgres data (**must be local**, not NFS) | `./config/immich-postgres` |
| `DB_PASSWORD` | Strong Postgres password — **alphanumeric only** (A–Z, a–z, 0–9, no special chars) per Immich docs | `openssl rand -hex 24` |
| `PLEX_CLAIM` | Claim token from plex.tv/claim (**get this right before deploying**) | `claim-xxxx` |
| `PLEX_TOKEN` | Auto-extracted after first deploy with `PLEX_CLAIM`. Used for Plex exporter and media VM notifications. Leave empty on first deploy. | — |
| `PLEX_HOST` | This VM's LAN IP (auto-detected by `setup_env.py`). Used by media VM to reach Plex. | `192.168.1.230` |
| `LOKI_URL` | `http://<MONITORING_IP>:3100` | `http://192.168.1.120:3100` |
| `PROMETHEUS_URL` | `http://<MONITORING_IP>:9090` | `http://192.168.1.120:9090` |
| `ENABLE_OBSERVABILITY` | `1` (default) | `1` |

**NFS mounts (if using NAS):** Set these in the `.env` and bootstrap auto-configures fstab + mount:

| Variable | Example |
|----------|---------|
| `NFS_HOST` | `nas.local` or NAS IP |
| `NFS_MEDIA_EXPORT` | `/volume1/media/library` |
| `NFS_PHOTOS_EXPORT` | `/volume1/photos/library` |

Or mount manually:

```bash
sudo mkdir -p /mnt/media/library /mnt/photos/library
sudo mount -t nfs <NAS_IP>:/volume1/media/library /mnt/media/library
sudo mount -t nfs <NAS_IP>:/volume1/photos /mnt/photos/library
```

**Local-only:**

```bash
sudo mkdir -p /mnt/media/library /mnt/photos/library
sudo chown -R 1000:1000 /mnt/media /mnt/photos
```

---

## Phase 4: Deploy stacks (order matters)

Deploy in this order: **monitoring → core → media → accelerated**. Each stack is deployed from its own VM.

> **Important:** `deploy.py all` uses alphabetical order, which is **not** the correct dependency order. Always deploy stacks explicitly in the order below, or pass them in order:
> ```bash
> python3 deploy.py monitoring core media accelerated --force --init-env -y
> ```
> (only works if all stacks are on the same host — in production, deploy from each VM)

### Step 1 — Monitoring

SSH into the **monitoring** VM:

```bash
cd ~/self-hosting
python3 deploy.py monitoring --force --init-env -y
```

What happens: validates `.env`, runs bootstrap, starts Grafana + Prometheus + Loki + Uptime Kuma.

**Verify:**
- Grafana: `http://<MONITORING_IP>:3000` — log in with `admin` / your `GRAFANA_ADMIN_PASSWORD`
- Prometheus: `http://<MONITORING_IP>:9090/targets` — local targets should be up
- Uptime Kuma: `http://<MONITORING_IP>:3001` — create an admin account

### Step 2 — Core

SSH into the **core** VM:

```bash
cd ~/self-hosting
python3 deploy.py core --force --init-env -y
```

What happens: validates `.env`, runs bootstrap (generates Caddyfile, dnsmasq config), starts Caddy + Authentik + dnsmasq + whoami. If `AUTHENTIK_BOOTSTRAP_TOKEN` is set, auto-applies the SSO blueprint.

**Verify:**
- Whoami: `https://<WHOAMI_FQDN>` — should return an echo response (accept cert warning if using internal TLS)
- Authentik: `https://<AUTHENTIK_FQDN>` — log in or complete setup wizard

**Router:** Forward ports **80** and **443** to the core VM's IP. This is the only VM that needs to be publicly reachable.

### Step 3 — Media

SSH into the **media** VM:

```bash
cd ~/self-hosting
python3 deploy.py media --force --init-env -y
```

What happens: validates `.env` (including `EXPRESSVPN_CODE`), sets up NFS mounts if configured, starts VPN + qBittorrent + Sonarr + Radarr + Prowlarr + FlareSolverr + overlays. Runs `setup_media_apps.py` post-deploy (configures indexers, download clients, root folders, naming, Prowlarr sync). Runs Recyclarr sync if enabled.

**Verify:**
- Sonarr: `http://<MEDIA_IP>:8989` — root folders and download client configured
- Radarr: `http://<MEDIA_IP>:7878` — root folders and download client configured
- Prowlarr: `http://<MEDIA_IP>:9696` — indexers present, apps synced
- qBittorrent: `http://<MEDIA_IP>:8080` — categories (`tv`, `movies`, `anime`) with correct paths

### Step 4 — Accelerated

**Get your Plex claim token now** — go to https://plex.tv/claim and paste it into the accelerated `.env` as `PLEX_CLAIM`. It expires in ~4 minutes.

SSH into the **accelerated** VM:

```bash
cd ~/self-hosting
python3 deploy.py accelerated --force --init-env -y
```

What happens: validates `.env`, checks GPU (`/dev/dri`), installs VA-API packages if needed, starts Plex + Immich. Runs `setup_accelerated_apps.py` post-deploy. Extracts `PLEX_TOKEN` and clears the used `PLEX_CLAIM`.

**Verify:**
- Plex: `http://<ACCELERATED_IP>:32400/web` — claim server, add library pointing to media
- Immich: `http://<ACCELERATED_IP>:2283` — create admin user, upload a test photo

---

## Phase 5: Post-deploy wiring

These cross-stack connections tie everything together.

### 1. Copy Plex credentials to media stack

After accelerated deploys, `deploy.py` prints the `PLEX_TOKEN`. Copy it to the **media** VM's `.env`:

```bash
# On the media VM, edit docker_compose/media/.env:
PLEX_HOST=<ACCELERATED_IP>
PLEX_TOKEN=<token from accelerated deploy output>
```

Then redeploy media for Sonarr/Radarr to notify Plex of new library items:

```bash
python3 deploy.py media --force -y
```

### 2. Add Caddy routes for services behind the reverse proxy

In the **core** `.env`, set `CADDY_EXTRA_SERVICES`. This single variable drives **both** the Caddyfile (reverse proxy routes) **and** the Authentik SSO blueprint (proxy providers + applications).

**Format:** comma-separated entries, each one: `subdomain:host:port[:sso]`

Subdomains are automatically expanded to FQDNs using `PUBLIC_BASE_DOMAIN` (e.g. `sonarr` becomes `sonarr.example.com`). Full FQDNs (containing a dot) are kept as-is for backward compatibility.

| Component | Meaning |
|-----------|---------|
| `subdomain` | Short name — expanded to `subdomain.PUBLIC_BASE_DOMAIN` |
| `subdomain/path` | Match only that path (e.g. `/api`) — used to bypass SSO for API endpoints |
| `host:port` | Backend VM IP and service port |
| `:sso` | Append to put this route behind Authentik forward auth |

**Typical pattern** — SSO on the UI, bypass for API calls (so Sonarr/Radarr API keys still work):

```
sonarr:192.168.1.220:8989:sso,sonarr/api:192.168.1.220:8989
```

The first entry puts the whole site behind SSO. The second entry creates a `/api` path match **without** SSO, so API-key-authenticated requests (Prowlarr sync, webhooks, etc.) pass through directly.

**Full example** (replace IPs with yours):

```
CADDY_EXTRA_SERVICES=sonarr:192.168.1.220:8989:sso,sonarr/api:192.168.1.220:8989,radarr:192.168.1.220:7878:sso,radarr/api:192.168.1.220:7878,prowlarr:192.168.1.220:9696:sso,qbittorrent:192.168.1.220:8080:sso,plex:192.168.1.230:32400,immich:192.168.1.230:2283,grafana:192.168.1.120:3000:sso,uptime:192.168.1.120:3001:sso
```

| Entry | SSO? | Notes |
|-------|------|-------|
| `sonarr:...:sso` | Yes | UI behind Authentik |
| `sonarr/api:...` | No | API keys pass through |
| `radarr:...:sso` | Yes | Same pattern as Sonarr |
| `radarr/api:...` | No | API bypass |
| `prowlarr:...:sso` | Yes | Indexer manager UI |
| `qbittorrent:...:sso` | Yes | Download client UI |
| `plex:...` | No | Plex handles its own auth |
| `immich:...` | No | Immich handles its own auth |
| `grafana:...:sso` | Yes | Dashboards behind SSO |
| `uptime:...:sso` | Yes | Uptime checks behind SSO |

Redeploy core to regenerate the Caddyfile and SSO blueprint:

```bash
python3 deploy.py core --force -y
```

### 3. Configure monitoring scrape targets

If you didn't set these during Phase 3, update the **monitoring** `.env` with the other VMs' IPs:

```
SCRAPE_TARGETS=core:192.168.1.110,media:192.168.1.220,accelerated:192.168.1.230
```

Redeploy monitoring to pick up the new targets:

```bash
python3 deploy.py monitoring --force -y
```

### 4. Point your DNS or hosts file at core

For **production**: create DNS A records (or use DDNS) pointing `*.example.com` → core's public IP. Forward router ports 80/443 → core's LAN IP.

For **local testing**: add entries to your PC's hosts file mapping all FQDNs to core's LAN IP:

```
192.168.1.110  example.com auth.example.com whoami.example.com sonarr.example.com radarr.example.com prowlarr.example.com qbittorrent.example.com plex.example.com immich.example.com grafana.example.com uptime.example.com
```

### 5. Uptime Kuma monitors

In Uptime Kuma (`http://<MONITORING_IP>:3001`), add HTTP(S) monitors for each service endpoint to track availability.

---

## Phase 6: Verify everything

| Service | URL | Expected result |
|---------|-----|-----------------|
| Whoami | `https://whoami.<domain>` | Echo response with headers |
| Authentik | `https://auth.<domain>` | SSO login page |
| Grafana | `https://grafana.<domain>` or `:3000` | Dashboard login |
| Prometheus | `http://<MONITORING_IP>:9090/targets` | All targets UP |
| Uptime Kuma | `https://uptime.<domain>` or `:3001` | Dashboard with monitors |
| Sonarr | `https://sonarr.<domain>` | SSO → Sonarr UI |
| Radarr | `https://radarr.<domain>` | SSO → Radarr UI |
| Prowlarr | `https://prowlarr.<domain>` | SSO → indexer list |
| qBittorrent | `https://qbittorrent.<domain>` | SSO → qBit UI |
| Plex | `https://plex.<domain>` | Plex web UI with library |
| Immich | `https://immich.<domain>` | Photo upload/browse |

**End-to-end test:** Search for a movie in Radarr → triggers download in qBittorrent via VPN → imports to library → Plex detects new media.

---

## Quick reference

| Item | Value |
|------|-------|
| Template VMID | 9000 |
| Core VMID / vCPU / RAM | 110 / 2 / 4 GB |
| Monitoring VMID / vCPU / RAM | 120 / 2 / 6 GB |
| Media VMID / vCPU / RAM | 220 / 4 / 8 GB |
| Accelerated VMID / vCPU / RAM | 230 / 4 / 8 GB |
| Deploy command | `python3 deploy.py <stack> --force --init-env -y` |
| Deploy order | monitoring → core → media → accelerated |
| Only public VM | core (router ports 80/443) |
| Media root (host → container) | `/mnt/media` → `/data` |
| Repo path on VMs | `~/self-hosting` (symlink to `/opt/self-hosting`) |
| Setup env (per VM) | `python3 scripts/setup_env.py` |
