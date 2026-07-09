# Chapter 3B — Monitoring Stack: Configuration and Deployment

## Introduction

**Prerequisites:** [Chapter 2B (Monitoring VM)](Chapter2b-monitoring.md) (VM purpose, app selection, data flow), [Chapter 2](Chapter2-vms.md) (VM overview), and [Chapter 3A (Core stack)](Chapter3a-core-stack.md) (deploy pattern).

Chapter 2B explains *why* the monitoring VM exists and *what* runs there (Grafana, Prometheus, Loki, Uptime Kuma). This chapter is the **hands-on guide**: the contents of `docker_compose/monitoring/`, how to configure them, and how to deploy the stack. Observability sidecars (node_exporter, cAdvisor, Alloy) are shared across all VMs via `compose.observability.yml` — see [Adding other VMs](#adding-other-vms-observability-sidecars).

You will walk through the environment template (`.env.example`), important parts of the Compose file, the bootstrap script, and two deployment paths — manual (on the VM) and repo-driven (`deploy.py`).

> ### 🧠 Philosophy: One Stack, One Directory
> The monitoring stack is self-contained under `docker_compose/monitoring/`. Compose file, env template, and bootstrap script live together so that cloning the repo and filling `.env` is enough to get a repeatable, documentable deployment.

> ### 🧠 Philosophy: Local Storage Only — No NFS
> All monitoring data (Prometheus TSDB, Loki index and chunks, Uptime Kuma SQLite, Grafana DB) must live on **local disk**. NFS is not supported for these backends: Prometheus and Loki rely on POSIX semantics (WAL, atomic renames, fsync) that NFS does not guarantee, and Uptime Kuma's SQLite needs reliable file locking. Using NFS risks data corruption. Size the VM disk (e.g. 40–50 GB for 30-day retention) and tune retention in `.env` instead.

---

## Table of contents

- [What's in `docker_compose/monitoring/`](#whats-in-docker_composemonitoring)
  - [Configuration reference (official docs)](#configuration-reference-official-docs)
- [Storage: Why local only and how to size](#storage-why-local-only-and-how-to-size)
- [Environment: `.env.example`](#environment-envexample)
- [Compose file: Notable details](#compose-file-notable-details)
- [Bootstrap script: What it does](#bootstrap-script-what-it-does)
- [Deploying the monitoring stack](#deploying-the-monitoring-stack)
  - [Path 1: Manual (on the Monitoring VM)](#path-1-manual-on-the-monitoring-vm)
  - [Path 2: Repo deploy script (`deploy.py`)](#path-2-repo-deploy-script-deploypy)
- [After first run](#after-first-run)
- [UI configuration how-tos](#ui-configuration-how-tos)
  - [Grafana](#grafana)
  - [Prometheus](#prometheus)
  - [Loki](#loki)
  - [Uptime Kuma](#uptime-kuma)
  - [node_exporter, cAdvisor, Alloy](#node_exporter-cadvisor-alloy)
- [Adding other VMs (observability sidecars)](#adding-other-vms-observability-sidecars)
- [Verification and troubleshooting](#verification-and-troubleshooting)
- [See also](#see-also)

---

## What's in `docker_compose/monitoring/`

| File or script | Purpose |
|----------------|---------|
| **compose.yml** | Stack definition: Grafana, Prometheus, Loki, Uptime Kuma. Monitoring-only backends. |
| **compose.observability.yml** | Symlink → `../common/compose.observability.yml`. Shared overlay containing node_exporter, cAdvisor, Alloy (same file on every VM). Enabled via `ENABLE_OBSERVABILITY=1` in `.env`. |
| **.env.example** | Template for required and optional env vars (no secrets; copy to `.env` and fill). |
| **bootstrap.py** | Idempotent first-run: validates `.env`, creates config dirs with correct ownership, generates starter configs (Prometheus, Loki, Grafana datasources), generates Alloy config via shared `setup_observability_config()`, generates Prometheus scrape targets from `SCRAPE_TARGETS`, checks disk space, validates compose. |

All paths are relative to the directory where you run `docker compose` (typically `docker_compose/monitoring` or a symlink like `~/monitoring`). The bootstrap script must be run from that same directory so generated config files land under `CONFIG_ROOT`.

### Configuration reference (official docs)

When changing config beyond what bootstrap generates, use the official sources:

- [Grafana — Docker install](https://grafana.com/docs/grafana/latest/setup-grafana/installation/docker/) and [Provisioning](https://grafana.com/docs/grafana/latest/administration/provisioning/)
- [Prometheus — Installation](https://prometheus.io/docs/prometheus/latest/installation/) and [Storage](https://prometheus.io/docs/prometheus/latest/storage/)
- [Loki — Docker install](https://grafana.com/docs/loki/latest/setup/install/docker/)
- [Uptime Kuma — README](https://github.com/louislam/uptime-kuma)
- [Grafana Alloy — Docker install](https://grafana.com/docs/alloy/latest/get-started/install/docker/) and [loki.source.docker](https://grafana.com/docs/alloy/latest/reference/components/loki/loki.source.docker/)

---

## Storage: Why local only and how to size

### Why NFS is unsupported

Three components require a POSIX-compliant local filesystem:

- **Prometheus TSDB** — Uses a write-ahead log (WAL) for crash recovery. NFS does not reliably provide the atomic rename and fsync guarantees the WAL needs; the result can be unrecoverable corruption. [Prometheus storage docs](https://prometheus.io/docs/prometheus/latest/storage/) explicitly state that NFS is not supported.
- **Loki (filesystem storage)** — Loki 3.x uses TSDB for its index; same POSIX requirements and corruption risk on NFS.
- **Uptime Kuma** — Uses SQLite, which depends on reliable file locking. NFS locking is notoriously unreliable; the [Uptime Kuma README](https://github.com/louislam/uptime-kuma) states that NFS is not supported.

So the monitoring VM is the one VM in the lab that should **not** store its working data on an NFS mount.

### Disk sizing and retention

Rough estimates for a homelab with ~5 VMs and moderate log volume (30-day retention):

| Component | Typical size |
|-----------|--------------|
| Prometheus | ~3–5 GB |
| Loki | ~5–15 GB |
| Grafana | &lt;200 MB |
| Uptime Kuma | &lt;100 MB |
| **Total** | ~10–25 GB; allocate **40–50 GB** for safety |

Bootstrap warns if free space at `CONFIG_ROOT` is below 10 GB. Tune **PROMETHEUS_RETENTION** and **LOKI_RETENTION** in `.env` to balance history depth and disk usage (e.g. `15d` if the disk is small).

---

## Environment: `.env.example`

Copy `.env.example` to `.env` and fill real values. The template is grouped by concern below.

### Base paths and locale

| Variable | Purpose |
|----------|---------|
| **CONFIG_ROOT** | Root for all state and config (Grafana, Prometheus, Loki, Uptime Kuma, Alloy). Relative paths are resolved from the stack directory. Default: `./config`. |
| **TZ** | Timezone for containers (e.g. `Etc/UTC`). |

### Grafana (required)

| Variable | Purpose |
|----------|---------|
| **GRAFANA_ADMIN_PASSWORD** | Admin password for first login. Must be changed from placeholder. Generate with: `openssl rand -base64 24`. |

Bootstrap (and `deploy.py`) will refuse to continue if **GRAFANA_ADMIN_PASSWORD** is still a placeholder, unless you use `--force` for local testing.

### Grafana: Optional

| Variable | Purpose |
|----------|---------|
| **GRAFANA_ADMIN_USER** | Override admin username (default: `admin`). |
| **GF_SERVER_ROOT_URL** | Set when behind a reverse proxy (e.g. `https://grafana.example.com`) for OAuth redirects and links. |

### Retention

| Variable | Purpose |
|----------|---------|
| **PROMETHEUS_RETENTION** | How long to keep metrics (e.g. `30d`, `15d`). Shorter = less disk. |
| **LOKI_RETENTION** | How long to keep logs (e.g. `30d`). Must match or align with Loki config; bootstrap writes this into `loki-config.yml` when generating it. |

### VM identity (observability labels)

| Variable | Purpose |
|----------|---------|
| **VM_HOSTNAME** | Hostname label on metrics and logs from this VM. Default: auto-detected from system hostname. |
| **VM_ROLE** | Role label (e.g. `monitoring`). Default: derived from stack directory name. |
| **PROXMOX_NODE** | Proxmox node name. Default: read from `/etc/homelab/proxmox-node`, else `pve1`. |

These labels flow into both Prometheus scrape configs and Alloy log labels, ensuring metrics and logs can be filtered by `host`, `vm_role`, and `node` across all VMs.

### Observability sidecars

| Variable | Purpose |
|----------|---------|
| **ENABLE_OBSERVABILITY** | Set to `0` to disable the sidecar overlay. Default: `1` (enabled). |
| **LOKI_URL** | Where Alloy pushes logs. On the monitoring VM: `http://host.docker.internal:3100`. On other VMs: `http://<monitoring-vm-ip>:3100`. |
| **PROMETHEUS_URL** | Where Alloy pushes self-metrics. On the monitoring VM: `http://host.docker.internal:9090`. On other VMs: `http://<monitoring-vm-ip>:9090`. |
| **PUID** / **PGID** | VM user for Alloy and Uptime Kuma. Pre-filled by `setup_env.py`. |
| **DOCKER_GID** | Docker group GID so Alloy can read the Docker socket. Pre-filled by `setup_env.py`. |

### Prometheus remote scrape targets (monitoring only)

| Variable | Purpose |
|----------|---------|
| **SCRAPE_TARGETS** | Comma-separated `hostname:ip` pairs for remote VMs running sidecars. Bootstrap writes a file_sd JSON that Prometheus auto-discovers. Leave empty for single-VM setups. Example: `core:192.168.1.110,media:192.168.1.111`. |

### Blackbox Exporter probe targets (optional, feeds D03/D00)

Bootstrap regenerates `prometheus/targets/blackbox-targets.json` from these on every run. Each is a comma-separated `name:target` list; `name` becomes the `probe_name` label shown in the D03 probe table. Leave any empty to skip that probe category — see [Chapter 2B](Chapter2b-monitoring.md) for what D03 does with them.

| Variable | Module | Format | Purpose |
|----------|--------|--------|---------|
| **BLACKBOX_HTTP_TARGETS** | `http_2xx` | `name:url` | HTTP(S) reachability. Also validates DNS + TLS in one probe; blackbox exposes `probe_ssl_earliest_cert_expiry` for `https://` targets. |
| **BLACKBOX_TCP_TARGETS** | `tcp_connect` | `name:host:port` | Raw TCP reachability (e.g. proxy port, NFS port `2049`). |
| **BLACKBOX_ICMP_TARGETS** | `icmp` | `name:host` | Ping reachability (e.g. the NAS). |
| **BLACKBOX_DNS_TARGETS** | `dns_udp` | `name:host:port` | Queries a resolver for the `google.com` A record (module hardcoded in `blackbox.yml`) — validates the resolver forwards upstream. |

### Image tags (reproducibility)

Optional overrides (e.g. **GRAFANA_TAG**, **PROMETHEUS_TAG**, **LOKI_TAG**, **UPTIME_KUMA_TAG**, **NODE_EXPORTER_TAG**, **CADVISOR_TAG**, **ALLOY_TAG**). Leave unset to use compose defaults; pin after validating for predictable redeploys.

---

## Compose file: Notable details

The full stack is in `docker_compose/monitoring/compose.yml`. Below are the parts that are easy to miss or that affect operations.

### Services at a glance

**In `compose.yml`** (monitoring backends):

| Service | Role |
|---------|------|
| **grafana** | Dashboards and log search; port 3000. Datasources (Prometheus, Loki) are auto-provisioned by bootstrap. |
| **prometheus** | Metrics store; scrapes node_exporter, cAdvisor, and itself; port 9090. Local targets via `host.docker.internal`; remote targets via `file_sd_configs` (generated from **SCRAPE_TARGETS**). |
| **loki** | Log aggregation; receives push from Alloy; port 3100 (must be reachable from other VMs for Alloy sidecars). |
| **uptime-kuma** | Uptime checks and alerting; port 3001; data in `/app/data` (SQLite). |

**In `compose.observability.yml`** (shared sidecar overlay — same on every VM):

| Service | Role |
|---------|------|
| **node-exporter** | Host metrics (CPU, RAM, disk, network); **host network** and **pid: host** for accuracy; port 9100. |
| **cadvisor** | Per-container metrics; host port 8081 (container 8080); requires `privileged: true` on Debian/Ubuntu for cgroup access. |
| **alloy** | Log shipper: discovers Docker containers via socket, pushes logs to Loki and self-metrics to Prometheus; port 12345 (management UI). |

### Network topology

- **monitoring_internal** bridge: Grafana, Prometheus, Loki, Uptime Kuma communicate by container name.
- **Observability sidecars** do not join `monitoring_internal`. node_exporter uses host network; cAdvisor and Alloy reach Loki/Prometheus via host-exposed ports (configured by **LOKI_URL** and **PROMETHEUS_URL** in `.env`).
- Prometheus scrapes node_exporter and cAdvisor at `host.docker.internal` (local) and via file_sd for remote VMs.

### Security hardening

Applied where they do not affect functionality:

- **security_opt: no-new-privileges:true** on all services except cAdvisor (which needs `privileged`).
- **cap_drop: ALL** on Grafana, Loki, Uptime Kuma, Alloy.
- **read_only: true** on node_exporter (read-only by design).
- Config and provisioning mounts are **read-only** (`:ro`).
- Grafana: `GF_ANALYTICS_REPORTING_ENABLED=false`, `GF_USERS_ALLOW_SIGN_UP=false`.

---

## Bootstrap script: What it does

`bootstrap.py` is **idempotent**: safe to run multiple times. It prepares the stack so `docker compose up` can succeed.

### Order of operations

1. **Prerequisites** — Docker and Docker Compose v2 installed and reachable.
2. **Env file** — If `.env` is missing, copy from `.env.example` and exit (you must fill values and re-run).
3. **Guardrails** — Unless `--force` is used, exit if **GRAFANA_ADMIN_PASSWORD** is missing or still a placeholder.
4. **Config directories** — Create under `CONFIG_ROOT`: `grafana/data`, `grafana/provisioning/datasources`, `prometheus`, `prometheus/data`, `prometheus/targets`, `loki`, `loki/data`, `uptime-kuma`.
5. **Disk space** — Warn if free space at `CONFIG_ROOT` is below 10 GB.
6. **Ownership** — Set data dir ownership to the UIDs used by the images: Prometheus data `65534`, Loki data `10001`, Grafana data `472`.
7. **Starter configs** (only if file missing — idempotent):
   - **prometheus/prometheus.yml** — Self, node_exporter at `host.docker.internal:9100`, cAdvisor at `host.docker.internal:8081` (local targets as `static_configs`); remote targets via `file_sd_configs` pointing to `targets/scrape-targets.json`.
   - **loki/loki-config.yml** — TSDB store, v13 schema, filesystem storage, retention from **LOKI_RETENTION**, compactor, query cache, analytics off.
   - **grafana/provisioning/datasources/datasources.yml** — Prometheus and Loki datasources so Grafana works out of the box.
8. **Scrape targets** (always regenerated) — Parse **SCRAPE_TARGETS** from `.env` and write `prometheus/targets/scrape-targets.json` with node-exporter and cAdvisor entries per remote VM. Prometheus picks this up via `file_sd_configs` (polls every 5 minutes).
9. **Observability config** (if `ENABLE_OBSERVABILITY=1`) — Call shared `setup_observability_config()`: create `alloy/` and `alloy/data/` dirs, set ownership, generate `config.alloy` with identity labels from **VM_HOSTNAME** / **VM_ROLE** / **PROXMOX_NODE** and endpoint URLs from **LOKI_URL** / **PROMETHEUS_URL**.
10. **Compose validation** — Run `docker compose config` and fail if invalid.
11. **Optional bring-up** — If `--up` was passed, run `docker compose up -d`.

### Flags

| Flag | Effect |
|------|--------|
| **--up** | After bootstrap checks, run `docker compose up -d`. |
| **--force** | Skip placeholder guardrails (for local/testing). |
| **--non-interactive**, **-y** | Accepted for `deploy.py` compatibility (no-op). |

### Common errors

- **GRAFANA_ADMIN_PASSWORD is missing/placeholder** — Set a strong password in `.env` and re-run bootstrap (or use `openssl rand -base64 24`).
- **Permission denied in Prometheus/Loki/Grafana data** — Run once: `sudo chown -R <UID>:<UID> <CONFIG_ROOT>/<service>/data` with the UID from the bootstrap output (65534, 10001, 472).
- **Low disk space** — Resize the VM disk or reduce **PROMETHEUS_RETENTION** and **LOKI_RETENTION**.

---

## Deploying the monitoring stack

You can either deploy **manually on the Monitoring VM** or use the **repo deploy script** from a machine that has the repo and SSH (or direct access) to the VM.

### Path 1: Manual (on the Monitoring VM)

Assumes the repo is cloned on the VM (e.g. under `~/Self-Hosting`).

1. **Clone or pull the repo** (if needed):
   ```bash
   git clone <your-repo-url> ~/Self-Hosting
   cd ~/Self-Hosting
   ```

2. **Create and fill `.env`**:
   ```bash
   cd docker_compose/monitoring
   cp .env.example .env
   # Edit .env: set GRAFANA_ADMIN_PASSWORD (required).
   # Optionally set PROMETHEUS_RETENTION, LOKI_RETENTION, SCRAPE_TARGETS.
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

From the **repo root** on a machine that can run the deploy script:

1. **Ensure `.env` exists and is filled** in `docker_compose/monitoring/`. Deploy does not create `.env` from `.env.example`; it validates required vars and then runs the stack's bootstrap.

2. **Run deploy**:
   ```bash
   python3 deploy.py monitoring
   ```

   Deploy will:
   - Validate required env vars for `monitoring` (e.g. **GRAFANA_ADMIN_PASSWORD**).
   - Run `docker_compose/monitoring/bootstrap.py`.
   - Create a symlink `~/monitoring` → repo's `docker_compose/monitoring` (if not already installed).
   - Run `docker compose up -d` in the stack directory.
   - Update shell helpers (e.g. `monitoring ps`, `monitoring logs -f`, `monitoring up -d`) in `~/.bashrc.d/stack-functions.sh`.

3. **Optional flags**:
   - `python3 deploy.py monitoring --force` — Continue even if env validation fails (use only for testing).
   - `python3 deploy.py monitoring --default monitoring` — Set `monitoring` as the default stack for the `stack` helper.

After a successful deploy, you can use `monitoring ps`, `monitoring logs -f`, `monitoring up -d`, etc., from any directory (after sourcing your shell rc or opening a new session).

---

## After first run

Recommended order:

1. **Grafana** — Log in at `http://<monitoring-vm-ip>:3000` with admin and your **GRAFANA_ADMIN_PASSWORD**. Datasources (Prometheus, Loki) are already provisioned; import a community dashboard (e.g. Node Exporter Full, ID `1860`) to get started.
2. **Uptime Kuma** — Open `http://<monitoring-vm-ip>:3001`, complete first-run setup, and add your first monitor (e.g. core whoami or Grafana).
3. **Prometheus** — Optionally add scrape targets for other VMs (see [Adding other VMs](#adding-other-vms-observability-sidecars)).
4. **Pin image tags** — After validating a release, set tags in `.env` (e.g. `GRAFANA_TAG=11.0.0`) and redeploy for predictable updates.

---

## UI configuration how-tos

Step-by-step configuration by service. Grafana and Uptime Kuma have UIs; Prometheus and Loki are configured via config files (and env for retention).

### Grafana

- **First login** — User: `admin` (or **GRAFANA_ADMIN_USER**). Password: **GRAFANA_ADMIN_PASSWORD** from `.env`.
- **Datasources** — Prometheus and Loki are auto-provisioned by bootstrap; no manual add needed. You can confirm under **Connections → Data sources**.
- **Dashboards** — **Dashboards → New → Import**; enter a dashboard ID (e.g. `1860` for Node Exporter Full) and load. Add a `$host` or `instance` variable to switch between VMs when you add more targets.
- **Explore (logs)** — Use **Explore**, select the Loki datasource, choose a time range, and filter by label (e.g. `host="monitoring"`, `container=~"grafana|prometheus"`).
- **Behind reverse proxy** — Set **GF_SERVER_ROOT_URL** in `.env` to your public URL (e.g. `https://grafana.example.com`) so redirects and links work; restart Grafana.

### Prometheus

Prometheus has no UI for editing config; you edit files and optionally reload.

- **Scrape targets** — Local targets (this VM) are configured as `static_configs` in `prometheus.yml`. Remote VMs are managed via **SCRAPE_TARGETS** in `.env`; bootstrap writes `config/prometheus/targets/scrape-targets.json` and Prometheus auto-discovers changes (no restart needed). See [Adding other VMs](#adding-other-vms-observability-sidecars).
- **Reload** — After manually editing `prometheus.yml`, trigger a reload: `curl -X POST http://localhost:9090/-/reload` (Prometheus is started with `--web.enable-lifecycle`). Or restart: `docker compose restart prometheus` (or `monitoring restart prometheus`). Note: scrape target file changes do not require a reload — Prometheus polls `file_sd_configs` every 5 minutes.
- **Ad-hoc queries** — Open `http://<monitoring-vm-ip>:9090` and use the Graph tab with PromQL (e.g. `up`, `node_memory_MemAvailable_bytes`).

### Loki

- **Retention** — Controlled by **LOKI_RETENTION** in `.env` and the generated `loki-config.yml`. To change retention: update **LOKI_RETENTION**, then either regenerate config (if you are okay replacing `loki-config.yml` with a new copy from bootstrap logic) or edit `CONFIG_ROOT/loki/loki-config.yml` manually (`limits_config.retention_period`, `compactor.retention_enabled: true`), then restart Loki.
- **No UI** — Loki is a backend; use Grafana Explore to query logs.

### Uptime Kuma

- **First run** — Open `http://<monitoring-vm-ip>:3001`; create the first admin user and password (stored in its SQLite DB).
- **Add a monitor** — **Add New Monitor**; choose type (HTTP(s), TCP, etc.), set URL or host:port, name, and optional notification.
- **Notifications** — Configure under **Settings → Notifications** (e.g. Discord, Slack, ntfy, email).
- **Backup** — The only state worth backing up is Uptime Kuma's SQLite DB and config (e.g. `CONFIG_ROOT/uptime-kuma`). See [Chapter 2B — Backup and rebuild](Chapter2b-monitoring.md#backup-and-rebuild).

### node_exporter, cAdvisor, Alloy

These services have no (or minimal) configuration UI:

- **node_exporter** — Exposes metrics on port 9100 (host network). Prometheus scrapes it; verify in Prometheus targets page (`http://localhost:9090/targets`).
- **cAdvisor** — Exposes metrics on host port 8081 (container port 8080). Prometheus scrapes it; verify in targets. Optional: open `http://<monitoring-vm-ip>:8081` for a simple container list.
- **Alloy** — Pushes Docker container logs to Loki. Config in `CONFIG_ROOT/alloy/config.alloy`. Management UI at `http://<monitoring-vm-ip>:12345`. Verify logs in Grafana Explore (Loki, filter by `host="monitoring"` or container name).

---

## Adding other VMs (observability sidecars)

Every VM can run the same observability sidecars (node_exporter, cAdvisor, Alloy) via the shared `compose.observability.yml` overlay. The overlay is already symlinked into each stack directory.

### On the new VM (e.g. core, media)

1. **Set `.env` variables** — In the VM's stack `.env`, ensure:
   - `ENABLE_OBSERVABILITY=1` (default)
   - `LOKI_URL=http://<monitoring-vm-ip>:3100`
   - `PROMETHEUS_URL=http://<monitoring-vm-ip>:9090`
   - `PUID`, `PGID`, `DOCKER_GID` (pre-filled by `setup_env.py`)
2. **Deploy** — Run `python3 deploy.py <stack>` (or bootstrap + compose up). Bootstrap generates `config.alloy` with the correct identity labels and endpoint URLs.
3. **Verify** — Alloy pushes logs to Loki and self-metrics to Prometheus. Check in Grafana Explore (Loki: `{host="core"}`).

### On the monitoring VM

1. **Add the new VM to `SCRAPE_TARGETS`** in `.env`:
   ```
   SCRAPE_TARGETS=core:192.168.1.110,media:192.168.1.111
   ```
2. **Re-run bootstrap** (or just deploy again): `python3 bootstrap.py`. This regenerates `config/prometheus/targets/scrape-targets.json`.
3. **Prometheus auto-discovers** the new targets within 5 minutes (via `file_sd_configs`). No restart needed.

### Disabling sidecars

Set `ENABLE_OBSERVABILITY=0` in `.env` and redeploy. The overlay is not included; no sidecar containers start. Alloy config is not generated. Useful for testing or VMs where you don't want/need observability.

### Network requirements

- Alloy on each VM needs to reach Loki (`:3100`) and Prometheus (`:9090`) on the monitoring VM.
- Prometheus on the monitoring VM needs to reach node_exporter (`:9100`) and cAdvisor (`:8081`) on each remote VM.
- Ensure these ports are reachable across VMs (no firewall blocking).

---

## Verification and troubleshooting

### Quick checks

- **Grafana** — `http://<monitoring-vm-ip>:3000`; log in and open Explore; select Prometheus and run `up`; select Loki and run a log query.
- **Prometheus targets** — `http://<monitoring-vm-ip>:9090/targets`; all targets for this VM (Prometheus, node_exporter, cAdvisor) should be up.
- **Uptime Kuma** — `http://<monitoring-vm-ip>:3001`; at least one monitor configured and up.
- **Loki** — From Grafana Explore, Loki datasource; query `{host="monitoring"}` over the last 5 minutes; you should see Alloy-fed logs.

### If something fails

- **Compose** — From the stack directory: `docker compose config` (or `monitoring config` after deploy). Fix any env or volume errors.
- **Logs** — `docker compose logs -f` (or `monitoring logs -f`). Check Grafana, Prometheus, Loki, Alloy for permission or connection errors.
- **Permission denied (data dirs)** — Bootstrap prints the required UIDs. Run `sudo chown -R <uid>:<uid> <CONFIG_ROOT>/<service>/data` for Prometheus (65534), Loki (10001), Grafana (472).
- **No logs in Loki** — Confirm Alloy is running and has the Docker socket mounted; check Alloy logs and `config.alloy`; ensure Loki is reachable at the **LOKI_URL** configured in `.env` (on the monitoring VM, `http://host.docker.internal:3100`; on other VMs, `http://<monitoring-vm-ip>:3100`).
- **node_exporter down in Prometheus** — Prometheus must reach `host.docker.internal:9100`; the compose file uses `extra_hosts: host-gateway`. If you are not on Docker 20.10+ or equivalent, `host.docker.internal` may be missing; use the host's LAN IP as target in `prometheus.yml` instead.
- **Recovery** — Restore from a Proxmox snapshot or backup of the VM and/or `CONFIG_ROOT`; re-run bootstrap and `docker compose up -d`. See [Chapter 2B — What breaks if the Monitoring VM disappears](Chapter2b-monitoring.md#what-breaks-if-the-monitoring-vm-disappears).

---

## See also

- [Chapter 2B — Monitoring VM (purpose and app selection)](Chapter2b-monitoring.md): Why the monitoring VM exists, what runs there, data flow, and design constraints.
- [Chapter 2 — VM overview](Chapter2-vms.md): VM inventory, VMID scheme, and spinning up VMs from the template.
- [Chapter 3A — Core stack](Chapter3a-core-stack.md): Same deployment pattern for the core VM.
- [Chapter 3C — Media stack](Chapter3c-media-stack.md): Same deployment pattern for the media VM.
- [Local testing guide](Local-testing-guide.md): End-to-end test of all stacks on a four-VM LAN setup.
