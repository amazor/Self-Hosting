# Chapter 3A — Core Stack: Configuration and Deployment

## Introduction

**Prerequisites:** [Chapter 2A (Core VM)](Chapter2a-core.md) (VM provisioned and reachable), [Chapter 2](Chapter2-vms.md) (VM overview).

Chapter 2A explains *why* the Core VM exists and *what* runs there (Caddy, Authentik, dnsmasq, whoami). This chapter is the **hands-on guide**: the contents of `docker_compose/core/`, how to configure them, and how to deploy the stack.

You will walk through the environment template (`.env.example`), important parts of the Compose file, the bootstrap script, and two deployment paths — manual (on the VM) and repo-driven (`deploy.sh`).

> ### 🧠 Philosophy: One Stack, One Directory
> The core stack is self-contained under `docker_compose/core/`. Compose file, env template, and bootstrap script live together so that cloning the repo and filling `.env` is enough to get a repeatable, documentable deployment.

---

## Table of contents

- [What's in `docker_compose/core/`](#whats-in-docker_composecore)
- [Environment: `.env.example`](#environment-envexample)
- [Compose file: Notable details](#compose-file-notable-details)
- [Bootstrap script: What it does](#bootstrap-script-what-it-does)
- [Deploying the core stack](#deploying-the-core-stack)
  - [Path 1: Manual (on the Core VM)](#path-1-manual-on-the-core-vm)
  - [Path 2: Repo deploy script (`deploy.sh`)](#path-2-repo-deploy-script-deploysh)
- [After first run](#after-first-run)
- [UI configuration how-tos](#ui-configuration-how-tos)
  - [Authentik](#authentik)
  - [Caddy](#caddy)
  - [dnsmasq](#dnsmasq)
  - [whoami](#whoami)
- [Verification and troubleshooting](#verification-and-troubleshooting)
- [See also](#see-also)

---

## What's in `docker_compose/core/`

| File or script | Purpose |
|----------------|---------|
| **compose.yml** | Stack definition: Caddy, Authentik (server + worker), Postgres, Redis, dnsmasq, whoami |
| **.env.example** | Template for required and optional env vars (no secrets; copy to `.env` and fill) |
| **bootstrap.sh** | Idempotent first-run: creates/validates `.env`, config dirs, Caddyfile, dnsmasq.conf, optional `docker compose up` |
| **gen-caddyfile.sh** | Generates `config/caddy/Caddyfile` from `.env` (used by bootstrap and by [update-caddyfile.sh](#after-first-run)) |
| **update-caddyfile.sh** | Regenerate Caddyfile from `.env` and reload Caddy without full bootstrap |

All paths in the stack are relative to the directory where you run `docker compose` (typically `docker_compose/core` or a symlink like `~/core`). The bootstrap script must be run from that same directory so generated config files land in the right place.

---

## Environment: `.env.example`

Copy `.env.example` to `.env` and fill real values. The template is grouped by concern below.

### Base paths and locale

| Variable | Purpose |
|----------|---------|
| **CONFIG_ROOT** | Root for all state and config (Caddy, Authentik, dnsmasq). Relative paths are resolved from the stack directory. Default: `./config`. |
| **TZ** | Timezone for containers (e.g. `Etc/UTC`). |

### Image tags (reproducibility)

Pin tags after validating so redeploys are predictable. The template uses:

- **CADDY_TAG** — e.g. `2` (major version).
- **AUTHENTIK_TAG** — e.g. `latest` or a specific release.
- **POSTGRES_TAG**, **REDIS_TAG**, **DNSMASQ_TAG**, **WHOAMI_TAG** — optional overrides.

### Authentik and Postgres (required)

| Variable | Purpose |
|----------|---------|
| **AUTHENTIK_SECRET_KEY** | Long random value for signing/session crypto. Generate with: `openssl rand -base64 60`. |
| **AUTHENTIK_POSTGRES_DB**, **AUTHENTIK_POSTGRES_USER**, **AUTHENTIK_POSTGRES_PASSWORD** | Postgres database and credentials for Authentik. |

Set these before first start; the bootstrap script will refuse to continue if they are still placeholders (unless you use `--force` for local testing).

### Authentik: Optional first-run bootstrap

If you set these *before* the first `docker compose up`, Authentik creates the default `akadmin` user without the initial-setup UI:

- **AUTHENTIK_BOOTSTRAP_EMAIL** — email for the admin user.
- **AUTHENTIK_BOOTSTRAP_PASSWORD** — password for the admin user.
- **AUTHENTIK_BOOTSTRAP_TOKEN** — optional API token for `akadmin` (useful for automation).

Leave them empty to use the web UI flow after first start.

### DNS (dnsmasq)

| Variable | Purpose |
|----------|---------|
| **DNS_BIND_IP** | Bind address for DNS on the host. Use the VM’s LAN IP (e.g. `192.168.1.110`) to avoid listening on all interfaces; `0.0.0.0` is the default. |
| **DNS_UPSTREAM_1**, **DNS_UPSTREAM_2** | Upstream DNS servers (e.g. `1.1.1.1`, `1.0.0.1`). |
| **DNS_LOCAL_DOMAIN** | Local domain for the lab (e.g. `lab.arpa`). |
| **DNS_LOCAL_RECORDS** | Optional comma-separated `host:ip` pairs (e.g. `core:192.168.1.110,apps:192.168.1.120`) pre-seeded into dnsmasq. |

### Caddy and public hostnames

| Variable | Purpose |
|----------|---------|
| **PUBLIC_BASE_DOMAIN** | Your public domain (e.g. `example.com`). |
| **AUTHENTIK_FQDN**, **WHOAMI_FQDN** | Hostnames for Authentik and the whoami echo service (e.g. `auth.example.com`, `whoami.example.com`). |
| **WHOAMI_ALLOW_CIDRS** | Optional. When set, only these CIDRs can reach whoami (others get 403). Unset = allow all (for external uptime checkers). Suggested: `127.0.0.0/8 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 100.64.0.0/10` (private + Tailscale). Response is always `Content-Type: text/plain`. Rate limiting is not built into Caddy; see [Chapter 2A — whoami security](Chapter2a-core.md#troubleshooting-endpoint-whoami--echo-service) for options. |
| **CADDY_USE_INTERNAL_TLS** | Set to `true` for local/testing (e.g. `.home.arpa`); Caddy uses its internal CA instead of Let’s Encrypt. |

### Adding more services (Caddy routes)

**CADDY_EXTRA_SERVICES** — Comma-separated list. Each entry:

- **FQDN:host:port[:sso]** — whole site; add `:sso` to put it behind Authentik.
- **FQDN/path:host:port[:sso]** — path-only (e.g. `/api` without SSO, rest with SSO).

Examples (from `.env.example`):

```bash
# Whole site behind SSO:
# CADDY_EXTRA_SERVICES=sonarr.example.com:192.168.1.130:8989:sso
# Path-only (e.g. API no SSO):
# CADDY_EXTRA_SERVICES=sonarr.example.com/api:192.168.1.130:8989
```

The Caddyfile is **generated** from `.env` by `gen-caddyfile.sh`; after changing `CADDY_EXTRA_SERVICES`, re-run bootstrap or [update-caddyfile.sh](#after-first-run) and reload Caddy.

---

## Compose file: Notable details

The full stack is in `docker_compose/core/compose.yml`. Below are the parts that are easy to miss or that affect operations.

### Services at a glance

| Service | Role |
|---------|------|
| **caddy** | Reverse proxy; mounts config from `CONFIG_ROOT/caddy`; ports 80, 443 (TCP + UDP for HTTP/3). |
| **authentik-postgresql** | Postgres for Authentik; healthcheck so server/worker start only when DB is ready. |
| **authentik-redis** | Redis for Authentik queue/cache; lightweight persistence. |
| **authentik-server** | Authentik web/API; exposed only internally (9000/9443); Caddy is the public entrypoint. |
| **authentik-worker** | Authentik background worker; reads bootstrap env on first start. |
| **dnsmasq** | Internal DNS; config from `CONFIG_ROOT/dnsmasq`; ports 53 (TCP/UDP) bound via **DNS_BIND_IP**. |
| **whoami** | Echo endpoint; no published ports; reachable only via Caddy. |

### Caddy: Config and mounts

- Caddy runs with `--config /etc/caddy-config/Caddyfile` and `--adapter caddyfile`. The **directory** `CONFIG_ROOT/caddy` is mounted at `/etc/caddy-config` so the Caddyfile lives inside it (avoids file-vs-directory bind-mount issues).
- Separate volumes: `.../caddy/data` (TLS/certs), `.../caddy/config` (runtime), `.../caddy/site` (optional static files).

### Authentik: Healthchecks and bootstrap

- **authentik-server** and **authentik-worker** depend on Postgres and Redis with `condition: service_healthy`, so they start only after the database and cache are ready.
- Bootstrap env vars (`AUTHENTIK_BOOTSTRAP_EMAIL`, etc.) are read by the **worker** on first startup to create the initial admin user.

### dnsmasq and whoami: Security options

- **dnsmasq**: `security_opt: no-new-privileges:true` and `cap_add: NET_ADMIN` (needed for DNS). Config is mounted read-only.
- **whoami**: `read_only: true`, `no-new-privileges:true`, `cap_drop: ALL` so the container is as minimal as possible.

### Network

A single **core_internal** bridge network connects all services. Nothing is exposed to the host except the ports explicitly published (80, 443, 53).

---

## Bootstrap script: What it does

`bootstrap.sh` is **idempotent**: safe to run multiple times. It prepares the stack so `docker compose up` can succeed.

### Order of operations

1. **Prerequisites** — Docker and Docker Compose v2 installed and reachable.
2. **Env file** — If `.env` is missing, copy from `.env.example` and exit (you must fill values and re-run).
3. **Guardrails** — Unless `--force` is used, exit if `AUTHENTIK_SECRET_KEY`, `AUTHENTIK_POSTGRES_PASSWORD`, or `PUBLIC_BASE_DOMAIN` are still placeholders or example values.
4. **Config directories** — Create `CONFIG_ROOT` tree: `caddy`, `authentik/*`, `dnsmasq`.
5. **Config writable** — Ensure the current user can write to the config dir (fix ownership if Docker previously created dirs as root).
6. **Authentik media** — Set ownership of `authentik/media` to the Authentik UID/GID (default 1000) so uploads and migrations work.
7. **Caddyfile** — Run `gen-caddyfile.sh` to generate `config/caddy/Caddyfile` from `.env`.
8. **Caddyfile validation** — Fail if Caddyfile is missing or is a directory (e.g. from an old bind-mount).
9. **dnsmasq.conf** — If missing, write a starter `dnsmasq.conf` from env (upstreams, local domain, optional **DNS_LOCAL_RECORDS**).
10. **dnsmasq validation** — Fail if the config file is missing or a directory.
11. **Compose validation** — Run `docker compose config` to check syntax.
12. **Optional bring-up** — If `--up` was passed, run `docker compose up -d`.
13. **Caddy reload** — If Caddy is already running, reload config so the new Caddyfile is applied.

### Flags

| Flag | Effect |
|------|--------|
| **--up** | After bootstrap checks, run `docker compose up -d`. |
| **--force** | Skip placeholder guardrails (for local/testing with example domains). |
| **--help**, **-h** | Show usage. |

### Common errors (from script help)

- **Caddy: "Caddyfile: no such file or directory"** — Run bootstrap from the same directory where you run `docker compose`: `cd docker_compose/core && ./bootstrap.sh`.
- **Authentik: "Permission denied: /media/public"** — Run once: `sudo chown -R 1000:1000 <CONFIG_ROOT>/authentik/media`.
- **dnsmasq: "failed to read configuration file"** — Same as Caddy; run bootstrap from the stack directory so the config file is created there.

---

## Deploying the core stack

You can either deploy **manually on the Core VM** or use the **repo deploy script** from a machine that has the repo and SSH (or direct access) to the VM.

### Path 1: Manual (on the Core VM)

Assumes the repo is cloned on the VM (e.g. under `~/Self-Hosting` or `/opt/homelab`).

1. **Clone or pull the repo** (if needed):
   ```bash
   git clone <your-repo-url> ~/Self-Hosting
   cd ~/Self-Hosting
   ```

2. **Create and fill `.env`**:
   ```bash
   cd docker_compose/core
   cp .env.example .env
   # Edit .env: set AUTHENTIK_SECRET_KEY, AUTHENTIK_POSTGRES_PASSWORD,
   # PUBLIC_BASE_DOMAIN, AUTHENTIK_FQDN, WHOAMI_FQDN, DNS_BIND_IP (VM IP), etc.
   ```

3. **Run bootstrap** (and optionally start the stack):
   ```bash
   ./bootstrap.sh
   # If all checks pass, start the stack:
   docker compose up -d
   # Or in one step:
   ./bootstrap.sh --up
   ```

4. **Verify** — See [Verification and troubleshooting](#verification-and-troubleshooting).

### Path 2: Repo deploy script (`deploy.sh`)

From the **repo root** on a machine that can run the deploy script (e.g. your laptop or the VM):

1. **Ensure `.env` exists and is filled** in `docker_compose/core/`. Deploy does not create `.env` from `.env.example`; it validates required vars and then runs the stack’s bootstrap.

2. **Run deploy**:
   ```bash
   ./deploy.sh core
   ```

   Deploy will:
   - Validate required env vars for `core` (e.g. `AUTHENTIK_SECRET_KEY`, `AUTHENTIK_POSTGRES_PASSWORD`, `PUBLIC_BASE_DOMAIN`, `AUTHENTIK_FQDN`, `WHOAMI_FQDN`).
   - Run `docker_compose/core/bootstrap.sh`.
   - Create a symlink `~/core` → repo’s `docker_compose/core` (if not already installed).
   - Run `docker compose up -d` in the stack directory.
   - Update shell helpers (e.g. `core up -d`, `core logs -f`) in `~/.bashrc.d/stack-functions.sh`.

3. **Optional flags**:
   - `./deploy.sh core --force` — Continue even if env validation fails (use only for testing).
   - `./deploy.sh core --default core` — Set `core` as the default stack for the `stack` helper.

After a successful deploy, you can use `core ps`, `core logs -f`, `core up -d`, etc., from any directory (after sourcing your shell rc or opening a new session).

---

## After first run

Once the stack is up, do the following (each has a dedicated how-to below):

- **Authentik** — Log in (or complete the initial setup wizard if you didn’t use bootstrap env vars), then create a Proxy Provider and Application so Caddy can protect backends with SSO. See [Authentik](#authentik).
- **Caddy** — Add or change routes by editing `.env` and regenerating the Caddyfile. See [Caddy](#caddy).
- **dnsmasq** — Add local DNS records via `.env` or by editing the config file. See [dnsmasq](#dnsmasq).
- **Pinning image tags** — After validating a specific release (e.g. Authentik), set the tag in `.env` (e.g. `AUTHENTIK_TAG=2024.1.1`) and redeploy so updates are deliberate.

---

## UI configuration how-tos

Step-by-step configuration by service. Authentik has a web UI; Caddy and dnsmasq are configured via env and config files.

### Authentik

Authentik provides the SSO and identity UI. The core stack’s Caddyfile is already set up to use Authentik’s forward-auth endpoint (`/outpost.goauthentik.io/auth/caddy`); you only need to create a Provider and Application in the UI so that protected backends work.

#### First-time login or setup

**TODO (image):** Screenshot of Authentik login page or initial setup wizard (admin user creation).

- **If you set** `AUTHENTIK_BOOTSTRAP_EMAIL` and `AUTHENTIK_BOOTSTRAP_PASSWORD` in `.env` before first start: the `akadmin` user already exists. Log in at `https://<AUTHENTIK_FQDN>` with those credentials.
- **If you did not:** open `https://<AUTHENTIK_FQDN>` and complete the initial setup wizard (create admin user, set password). No bootstrap env vars are required for this path.

#### Create a Proxy Provider (forward auth for Caddy)

**TODO (image):** Screenshot of Providers list (Applications → Providers) and/or Proxy Provider create form with Mode and Forward auth URL fields.

1. In Authentik: **Applications → Providers → Create**.
2. Choose **Proxy Provider**.
3. **Name** — e.g. `Caddy forward auth`.
4. **Authorization flow** — Select or create a flow that includes the identification and consent steps you want (default “default-provider-authorization-implicit-consent” is fine to start).
5. **Mode** — **Forward auth (single application)**. This matches how the core Caddyfile calls Authentik: one provider handles all apps that use the same forward-auth URL.
6. **External host** — `https://<AUTHENTIK_FQDN>` (the same hostname Caddy uses for the Authentik UI, e.g. `https://auth.example.com`). Authentik uses this for redirects and cookie domain.
7. **Forward auth URL** — Leave default `/outpost.goauthentik.io/auth/caddy` (must match what Caddy uses; see `.env` [AUTHENTIK_FORWARD_AUTH_URI](#environment-envexample) if you override it).
8. **Save**.

#### Create an Application and link the provider

**TODO (image):** Screenshot of Application create form (Name, Slug, Provider dropdown).

1. **Applications → Applications → Create**.
2. **Name** — e.g. `Homelab SSO` or the name of the app you’re protecting.
3. **Slug** — Short identifier (e.g. `homelab`); used in URLs.
4. **Provider** — Select the Proxy Provider you created above.
5. **Launch URL** — Optional; e.g. the first app users see after login, or leave blank.
6. **Save**.

#### Assign the application to users

**TODO (image):** Screenshot of User or Group detail page with Application access section (assigning an application).

1. **Directory → Users** (or **Groups**): open the user or group.
2. Under **Application access**, add the Application you created so that user/group can use SSO-protected backends.
3. Users who have access will pass forward-auth when visiting backends that Caddy protects with `:sso` (see [Caddy — Adding a route](#adding-a-route)).

**Verify:** Visit a URL that is behind SSO in your Caddyfile (e.g. a service you added with `:sso` in **CADDY_EXTRA_SERVICES**). You should be redirected to Authentik to log in, then to the app.

For more (flows, policies, OAuth), see [Authentik documentation](https://docs.goauthentik.io/).

---

### Caddy

Caddy has no web UI. Routes are defined in the generated Caddyfile, which is built from `.env`. To add or change a route, edit `.env` and regenerate.

#### Adding a route

**TODO (image):** Optional — screenshot of `.env` snippet showing `CADDY_EXTRA_SERVICES` with an example entry.

1. Edit `docker_compose/core/.env` and set **CADDY_EXTRA_SERVICES** (see [Adding more services (Caddy routes)](#adding-more-services-caddy-routes)).
   - Whole site behind SSO: `FQDN:host:port:sso`
   - Whole site, no SSO: `FQDN:host:port`
   - Path-only: `FQDN/path:host:port` or `FQDN/path:host:port:sso`
2. From `docker_compose/core`, either:
   - Run **bootstrap** (or **deploy**): `./bootstrap.sh` or `./deploy.sh core` — regenerates Caddyfile and reloads Caddy, or
   - Run **update-caddyfile only**: `./update-caddyfile.sh` (or `./update-caddyfile.sh --no-reload` to only write the file).

**Verify:** `curl -k https://<FQDN>` (or open in a browser). For SSO routes, you should be redirected to Authentik if not logged in.

---

### dnsmasq

dnsmasq has no web UI. Local records are set via env (bootstrap) or by editing the config file.

#### Adding a local DNS record

**TODO (image):** Optional — screenshot of `.env` snippet showing `DNS_LOCAL_RECORDS` or of `dnsmasq.conf` with an `address=/...` line.

**Option A — Via `.env` (recommended for a small set of static records)**  
1. Edit `docker_compose/core/.env` and set **DNS_LOCAL_RECORDS** to a comma-separated list of `hostname:ip` (e.g. `core:192.168.1.110,apps:192.168.1.120`).  
2. Re-run bootstrap so dnsmasq.conf is regenerated: `./bootstrap.sh` (from `docker_compose/core`).  
3. Restart dnsmasq: `docker compose restart dnsmasq` (or `core restart dnsmasq` if using deploy helpers).

**Option B — Edit config directly**  
1. Edit `CONFIG_ROOT/dnsmasq/dnsmasq.conf` (e.g. `config/dnsmasq/dnsmasq.conf` in the stack directory).  
2. Add a line: `address=/hostname.<DNS_LOCAL_DOMAIN>/<IP>` (e.g. `address=/apps.lab.arpa/192.168.1.120`), or use `local=/lab.arpa/` with `/etc/hosts`-style lines in an included file.  
3. Restart dnsmasq: `docker compose restart dnsmasq`.

**Verify:** From a client that uses this VM as DNS: `nslookup hostname.<DNS_LOCAL_DOMAIN> <core-VM-IP>` (or `dig @<core-VM-IP> hostname.<DNS_LOCAL_DOMAIN>`).

---

### whoami

whoami is an echo service with no configuration. It is reachable only via Caddy at **WHOAMI_FQDN**. No UI or config steps required. The generated Caddyfile **hardens** it: Caddy always forces `Content-Type: text/plain` on the response. **Optional:** set **WHOAMI_ALLOW_CIDRS** (e.g. private + Tailscale: `127.0.0.0/8 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 100.64.0.0/10`) to restrict by IP; when unset, whoami is reachable from anywhere (for external uptime checkers). Rate limiting is not built into Caddy; see [Chapter 2A — whoami security](Chapter2a-core.md#troubleshooting-endpoint-whoami--echo-service) for options.

---

## Verification and troubleshooting

### Quick checks

- **Caddy and whoami**: `curl -k https://<WHOAMI_FQDN>` (or HTTP if using internal TLS) — should return whoami’s echo response.
- **Authentik**: Open `https://<AUTHENTIK_FQDN>` — should show login or initial setup.
- **DNS**: From a client that uses this VM as DNS, `nslookup core.<DNS_LOCAL_DOMAIN> <core-VM-IP>` (or dig) — should resolve if **DNS_LOCAL_RECORDS** or static records are set.

### If something fails

- **Compose**: `docker compose -f docker_compose/core/compose.yml config` — validates compose and env resolution.
- **Logs**: `docker compose -f docker_compose/core/compose.yml logs -f` (or `core logs -f` if deploy script was used).
- **Caddyfile**: Ensure `config/caddy/Caddyfile` exists and is a **file** (not a directory). If it was created as a directory by a previous mount, remove it and re-run bootstrap.
- **Recovery**: Restore from a Proxmox snapshot or backup of the VM and/or `CONFIG_ROOT`; then re-run bootstrap and `docker compose up -d`. See [Chapter 2A — What breaks if the Core VM disappears](Chapter2a-core.md#what-breaks-if-the-core-vm-disappears) for impact and out-of-band access.

---

## See also

- [Chapter 2A — Core VM (purpose and app selection)](Chapter2a-core.md): Why the Core VM exists, what runs there, and design constraints.
- [Chapter 2 — VM overview](Chapter2-vms.md): VM inventory, VMID scheme, and spinning up VMs from the template.
- **Chapter 3** *(planned)*: Overall Compose strategy and cross-stack patterns.
