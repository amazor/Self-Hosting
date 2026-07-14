# Local network testing guide

Step-by-step setup to test every stack from your PC on the local network, using VM local storage (no NAS). Assumes you have four VMs with the IPs and pre-filled `.env` files from the repo.

**IPs:** core=192.168.1.175, monitoring=192.168.1.197, media=192.168.1.171, accelerated=192.168.1.126  
**Domain:** `test.arpa` (Caddy internal TLS; you use the hosts file on your PC).

## Table of contents
- [Part 1: Set up your PC](#part-1-set-up-your-pc)
- [Part 2: VM storage (no NAS)](#part-2-vm-storage-no-nas)
- [Part 3: Deploy stacks (order matters)](#part-3-deploy-stacks-order-matters)
- [Part 4: Test each service](#part-4-test-each-service)
- [Part 5: Download media (end-to-end)](#part-5-download-media-end-to-end)
- [Part 6: Quick reference](#part-6-quick-reference)

---

## Part 1: Set up your PC

### 1.1 Hosts file (Windows)

Edit `C:\Windows\System32\drivers\etc\hosts` as Administrator. Add:

```
192.168.1.175  test.arpa auth.test.arpa whoami.test.arpa
192.168.1.175  sonarr.test.arpa radarr.test.arpa prowlarr.test.arpa bazarr.test.arpa qbittorrent.test.arpa
192.168.1.175  plex.test.arpa immich.test.arpa
192.168.1.175  grafana.test.arpa uptime.test.arpa
```

All traffic to these hostnames goes to **core** (192.168.1.175); Caddy reverse-proxies to the right VM. The last line is only needed if you add Grafana/Uptime Kuma to Caddy (see Part 3).

### 1.2 Trust Caddy’s certificate (optional)

With `CADDY_USE_INTERNAL_TLS=true`, Caddy uses its own CA. Browsers will show certificate warnings until you either:

- **Accept the exception** each time, or  
- **Trust Caddy’s root:** After core is up, open `https://whoami.test.arpa`, download or view the cert chain, then install the root CA into Windows (e.g. “Trusted Root Certification Authorities”). Caddy stores the root under core’s `CONFIG_ROOT`; exact path is in Caddy docs.

### 1.3 Prerequisites on PC

- **Browser** — Any modern browser (Chrome, Edge, Firefox).
- **Network** — PC and all four VMs on the same LAN (e.g. 192.168.1.x) and able to reach each other (ping each IP from the PC).

---

## Part 2: VM storage (no NAS)

Each VM uses **local** directories at the mount points. Create them once per VM (or let bootstrap create them where it supports it).

### 2.1 Media VM (192.168.1.171)

SSH (or console) into the media VM.

- `MEDIA_ROOT` is `/mnt/media`. Bootstrap will create `/mnt/media`, `downloads/`, and `library/` if they don’t exist when you run deploy.
- For Sonarr/Radarr root folders, use subdirs under `library`:

```bash
sudo mkdir -p /mnt/media/downloads /mnt/media/library/tv /mnt/media/library/movies /mnt/media/library/anime
sudo chown -R 1000:1000 /mnt/media
```

(Use your media stack `PUID`/`PGID` if different from 1000.)

### 2.2 Accelerated VM (192.168.1.126)

SSH into the accelerated VM.

- Bootstrap will create `MEDIA_LIBRARY_ROOT` and `IMMICH_UPLOAD_ROOT` if missing. With the pre-filled `.env` that’s `/mnt/media/library` and `/mnt/photos/library`.
- Ensure they exist and are writable by the stack user (e.g. 1000:1000):

```bash
sudo mkdir -p /mnt/media/library /mnt/photos/library
sudo chown -R 1000:1000 /mnt/media /mnt/photos
```

**Getting Plex to see media:**  
- **Option A (same host):** Run both media and accelerated stacks on the **same** machine; use one `/mnt/media` so Sonarr/Radarr write there and Plex reads the same path.  
- **Option B (separate VMs):** On the media VM, Sonarr/Radarr write to `/mnt/media/library`. On the accelerated VM, either NFS-mount the media VM’s `/mnt/media` at `/mnt/media`, or copy/sync files for testing (e.g. `rsync` or manual copy). For a quick test you can add a single movie/show under `/mnt/media/library/movies` or `.../tv` on the accelerated VM.

### 2.3 Monitoring VM

Uses `CONFIG_ROOT` (e.g. `./config`) and local disk only; no extra mount points needed.

### 2.4 Core VM

Uses `CONFIG_ROOT` only; no media paths.

---

## Part 3: Deploy stacks (order matters)

Run from the **repo root** on the machine that will run each stack (usually the corresponding VM). Use `--force` and `-y` for the pre-filled local-test `.env` (placeholder passwords, etc.).

### 3.1 Monitoring first

On the **monitoring** VM (or the host that will run monitoring):

```bash
cd /path/to/Self-Hosting
python3 deploy.py monitoring --force -y
```

- Open **Grafana:** `https://grafana.test.arpa` (after adding it to Caddy in 3.3) or `http://192.168.1.197:3000`. Login: `admin` / value of `GRAFANA_ADMIN_PASSWORD` in `docker_compose/monitoring/.env`.
- **Uptime Kuma:** `http://192.168.1.197:3001` (or add `uptime.test.arpa` to Caddy and use that).
- **Prometheus:** `http://192.168.1.197:9090` (targets should include core, media, accelerated once they are up).

### 3.2 Core (reverse proxy + Authentik)

On the **core** VM:

```bash
python3 deploy.py core --force -y
```

- **Whoami:** `https://whoami.test.arpa` — should return a simple response; confirms Caddy + TLS.
- **Authentik:** `https://auth.test.arpa` — first run: complete the setup wizard (or set `AUTHENTIK_BOOTSTRAP_EMAIL` / `AUTHENTIK_BOOTSTRAP_PASSWORD` in core `.env` and restart to skip it).
- **SSO:** Use **automated blueprint apply** (Option A, recommended), **manual blueprint** (Option A alt), or create provider and applications in the GUI (Option B).

**Option A — Automated blueprint (recommended, no GUI):** Set `AUTHENTIK_BOOTSTRAP_EMAIL`, `AUTHENTIK_BOOTSTRAP_PASSWORD`, and `AUTHENTIK_BOOTSTRAP_TOKEN` in core `.env` before first deploy. When the token is set, `deploy.py` automatically applies the generated SSO blueprint via the Authentik API after starting the stack — it creates proxy providers, applications, and assigns them to the embedded outpost. No manual Authentik GUI setup needed. Then assign applications to your user under **Directory → Users** → **Application access**.

**Option A alt — Manual blueprint (no token):** Bootstrap generates `config/authentik/blueprints/homelab-sso.yaml` from **CADDY_EXTRA_SERVICES** (entries with `:sso`) and **AUTHENTIK_FQDN**. In Authentik go to **Customization → Blueprints**, click **Create**, and paste the contents of that file. Then assign applications to your user under **Directory → Users** → **Application access**.

**Option B — Authentik GUI — exact values for local testing**

| Item | Value |
|------|--------|
| **Authentik URL** | `https://auth.test.arpa` (resolves to core **192.168.1.175**) |
| **Proxy Provider** | Create one **Proxy Provider**, mode **Forward auth (single application)**. **External host:** `https://auth.test.arpa`. **Forward auth URL:** leave default `/outpost.goauthentik.io/auth/caddy`. |

**Applications to create** — one Application per service; use this **External URL** (or equivalent field) so Caddy’s forward-auth matches. Create only the apps you actually use.

| Application name | External URL |
|------------------|--------------|
| Sonarr | `https://sonarr.test.arpa` |
| Radarr | `https://radarr.test.arpa` |
| Prowlarr | `https://prowlarr.test.arpa` |
| Bazarr | `https://bazarr.test.arpa` |
| qBittorrent | `https://qbittorrent.test.arpa` |
| Plex | `https://plex.test.arpa` |
| Immich | `https://immich.test.arpa` |
| Grafana | `https://grafana.test.arpa` |
| Uptime Kuma | `https://uptime.test.arpa` |

All these hostnames resolve to **192.168.1.175** (core) via your hosts file; Caddy on core proxies to the correct VM (media 192.168.1.171, accelerated 192.168.1.126, monitoring 192.168.1.197). After creating the provider and applications, assign each application to your user/group under **Directory → Users** (or **Groups**) → **Application access**. Then test logging in via the URLs above.

### 3.3 Optional: Expose Grafana and Uptime Kuma via core

To use `https://grafana.test.arpa` and `https://uptime.test.arpa` instead of direct IP:port, add to **core** `docker_compose/core/.env` in `CADDY_EXTRA_SERVICES` (comma-separated, append to the existing list):

```
grafana:192.168.1.197:3000:sso,uptime:192.168.1.197:3001:sso
```

Then regenerate the Caddyfile and reload Caddy (re-run bootstrap or `./update-caddyfile.sh` on core), and ensure `grafana.test.arpa` and `uptime.test.arpa` are in your hosts file (see 1.1).

### 3.4 Media (VPN + download pipeline)

On the **media** VM:

1. Fill **VPN credentials** in `docker_compose/media/.env`: set `EXPRESSVPN_CODE` to your ExpressVPN activation code (from https://www.expressvpn.com/setup).
   Without a valid activation code, the VPN and qBittorrent containers will not start.
2. Deploy:

```bash
python3 deploy.py media --force -y
```

Containers: ExpressVPN (VPN) → qBittorrent, Sonarr, Radarr, Prowlarr, FlareSolverr (and overlays if enabled). After compose up, deploy automatically runs **setup_media_apps.py** (adds Prowlarr indexers, configures qBittorrent, sets up Sonarr/Radarr root folders + download clients + TRaSH naming, and connects Prowlarr app sync), then triggers **Recyclarr** (TRaSH quality profiles including anime).

### 3.5 Accelerated (Plex + Immich)

On the **accelerated** VM:

```bash
python3 deploy.py accelerated --force -y
```

- **Plex:** Open `https://plex.test.arpa` (or `http://192.168.1.126:32400/web`). First time: claim the server (use `PLEX_CLAIM` in `.env` or the Plex wizard).
- **Immich:** `https://immich.test.arpa` — create admin user, then upload photos (they go to `IMMICH_UPLOAD_ROOT`).

---

## Part 4: Test each service

### 4.1 Core

| Service        | URL                     | What to check                    |
|----------------|-------------------------|----------------------------------|
| Whoami         | https://whoami.test.arpa | Page loads, cert warning OK once |
| Authentik      | https://auth.test.arpa   | Login, create provider + apps    |

### 4.2 Monitoring

| Service     | URL (or direct)                    | What to check                          |
|-------------|------------------------------------|----------------------------------------|
| Grafana     | https://grafana.test.arpa or :3000 | Login, dashboards load                  |
| Prometheus  | http://192.168.1.197:9090          | Targets (core/media/accelerated) up     |
| Uptime Kuma | :3001 or https://uptime.test.arpa  | Add monitors for whoami, Grafana, etc. |

### 4.3 Media (*arr stack)

| Service     | URL                        | What to check                                      |
|-------------|----------------------------|----------------------------------------------------|
| Sonarr      | https://sonarr.test.arpa   | SSO login, add root folder `/data/library/tv`      |
| Radarr      | https://radarr.test.arpa   | SSO login, add root folder `/data/library/movies`  |
| Prowlarr    | https://prowlarr.test.arpa | SSO login, add indexers, sync to Sonarr/Radarr     |
| qBittorrent | https://qbittorrent.test.arpa | SSO login, default admin/adminadmin              |
| Bazarr      | https://bazarr.test.arpa   | If enabled; SSO login                              |

### 4.4 Accelerated

| Service | URL                      | What to check                          |
|---------|--------------------------|----------------------------------------|
| Plex    | https://plex.test.arpa   | Claim server, add library (e.g. `/data` → `/mnt/media/library`) |
| Immich  | https://immich.test.arpa | Create user, upload photos             |

---

## Part 5: Download media (end-to-end)

This walks through one TV show and one movie so the full pipeline works.

### 5.1 Prowlarr — indexers

1. Open **https://prowlarr.test.arpa** and log in (SSO or direct).
2. **Indexers:** Deploy now adds 16 public torrent indexers (with FlareSolverr proxy) automatically via `setup_media_apps.py`. Verify they appear under **Indexers** and are healthy. Add additional indexers (private trackers, Usenet) if desired.
3. **Apps** → Verify **Sonarr** and **Radarr** are listed (`setup_media_apps.py` configures Prowlarr app sync automatically). If not, add them manually (URLs like `http://sonarr:8989` and `http://radarr:7878` from Prowlarr’s perspective). Sync indexers to the apps.

### 5.2 qBittorrent — categories

Deploy now pre-configures qBittorrent automatically via `setup_media_apps.py`: categories (`tv`, `movies`, `anime`) with correct save paths, default save path, and Automatic torrent management mode. Open **https://qbittorrent.test.arpa** and verify the settings are applied. Adjust if needed:

1. **Options → Downloads:** default save path should be `/data/downloads/qbittorrent`.
2. **Categories:** `tv`, `movies`, `anime` should exist with save paths relative to the default save path. **Default Torrent Management Mode** should be **Automatic** (TRaSH recommendation).

### 5.3 Sonarr — root folder and download client

`setup_media_apps.py` automatically configures Sonarr on deploy: root folders (`/data/library/tv`, `/data/library/anime`), qBittorrent download client (category `tv`), TRaSH naming, and Prowlarr app sync. Verify in the UI:

1. **https://sonarr.test.arpa** → **Settings → Media Management → Root Folders:** `/data/library/tv` should be present.
2. **Settings → Download Clients:** qBittorrent should be listed (host `qbittorrent`, port 8080, category `tv`).
3. **Settings → General:** note the **API Key** if you need it for manual Prowlarr configuration.

### 5.4 Radarr — root folder and download client

`setup_media_apps.py` automatically configures Radarr on deploy: root folder (`/data/library/movies`), qBittorrent download client (category `movies`), TRaSH naming, and Prowlarr app sync. Verify in the UI:

1. **https://radarr.test.arpa** → **Settings → Media Management → Root Folders:** `/data/library/movies` should be present.
2. **Settings → Download Clients:** qBittorrent should be listed (category `movies`).
3. **Settings → General:** API Key for Prowlarr (if needed for manual config).

### 5.5 Trigger a download

1. **Sonarr:** **Add New** → search for a TV show → add with a quality profile → **Search for missing episodes** (or add one series and trigger search).
2. **Radarr:** **Add New** → search for a movie → add with a quality profile → **Search** for the movie.
3. In **qBittorrent** you should see the torrents; when complete, Sonarr/Radarr will import to `/data/library/tv` and `/data/library/movies`.

Containers see the media root as `/data` (host `MEDIA_ROOT` = `/mnt/media` → `/data` in containers). So root folders in Sonarr/Radarr are `/data/library/tv` and `/data/library/movies`.

### 5.6 Plex — see the library

- On the **accelerated** VM, Plex must see the same library. If media and accelerated are on the **same host**, point Plex library to `/mnt/media/library` (or `/data` if you use one compose with one volume). If they are on **different VMs**, ensure `/mnt/media` on the accelerated VM is the NFS mount from the media VM (or a copy of the library). In Plex: **Add Library** → Movies → `/data/movies` (or the path that maps to `library/movies`), TV → `/data/tv` (or `library/tv`). Scan; the downloaded movie and show should appear.

---

## Part 6: Quick reference

| Item | Value |
|------|--------|
| Core IP | 192.168.1.175 |
| Monitoring IP | 192.168.1.197 |
| Media IP | 192.168.1.171 |
| Accelerated IP | 192.168.1.126 |
| Domain | test.arpa |
| Deploy (per stack) | `python3 deploy.py <core\|monitoring\|media\|accelerated> --force -y` |
| Media root (in containers) | `/data` (= host `/mnt/media`) |
| Sonarr root folder | `/data/library/tv` |
| Radarr root folder | `/data/library/movies` |

If a stack fails: check `docker compose logs` in that stack’s directory; for media, ensure `EXPRESSVPN_CODE` is set and the VPN container is healthy so qBittorrent can start.
