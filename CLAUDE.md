# CLAUDE.md

Instructions for Claude Code working in this repo.

## What this repo is

A **field manual + source of truth** for a self-hosted homelab: part journal, part technical guide, part IaC. Mission: a robust, scalable, automated home server hosting core infra (ingress, auth, DNS), monitoring/observability, media automation pipelines, general apps, and GPU workloads (transcoding/CV).

Guiding principles — keep these in mind for any change:
- **Boring Core, Flexible Workloads** — the access plane (`core`) stays stable; workloads churn.
- **Cattle, Not Pets** — VMs are disposable; redeploy from baseline instead of snowflake-fixing.
- **Decoupled Compute and Data** — Proxmox = compute, Synology NAS = storage; data survives VM rebuilds.
- **Documentation-first** — decisions get a "why", not just a "what".

## Repo map

- `docs/` — chapter-by-chapter journal (Chapter0 hardware → Chapter1 Proxmox → Chapter2 VM architecture → Chapter3 per-stack deploy). `docs/Quickstart.md` is the condensed fast-path.
- `proxmox/` — template + Cloud-Init automation (`scripts/`, `snippets/`).
- `scripts/` — shared Python framework (`homelab_common.py`, `homelab_bootstrap.py` → `BootstrapRunner`, `homelab_logging.py` → `StepTracker`, `setup_env.py`).
- `docker_compose/<vm>/` — one self-contained stack per VM: `compose.yml`, `.env.example`, `bootstrap.py`, `stack_config.py`, optional `scripts/`. Shared overlays in `docker_compose/common/`.
- `deploy.py` — top-level orchestrator (validates `.env`, runs bootstrap, symlinks, `docker compose up -d`).
- `AGENTS.md` — Cursor Cloud sandbox specifics (Docker-in-Docker caveats, that environment's key commands).
- `.cursor/rules/*.mdc`, `.cursor/skills/*` — directory-scoped reference material written for Cursor, but the content is tool-agnostic. Read the relevant one before working in that area:
  - `proxmox/**` → `.cursor/rules/proxmox-automation.mdc` (VMID ranges, Cloud-Init conventions)
  - `docker_compose/**` → `.cursor/rules/docker-compose-stacks.mdc` (per-stack file contract)
  - media apps (Radarr/Sonarr/Prowlarr/Bazarr/Plex/qBittorrent, file/folder layout) → `.cursor/rules/trash-guides-*.mdc` (TRaSH Guides is the gold standard here — open the actual trash-guides.info page for current specifics, the `.mdc` is just a pointer)
  - Grafana dashboard work → `.cursor/skills/grafana-dashboard-architect/`
  - Editing `docs/` chapters → `.cursor/skills/docs-chapter-reviewer/`
  - General architecture/journal discussions → `.cursor/skills/homelab-architect/SKILL.md` — adopt this persona by default: opinionated but not dogmatic, slow down before big structural decisions, flag anything that hurts debuggability or adds avoidable security risk.

**VMID scheme** (also in `docs/Chapter2-vms.md`): `9000s` = Cloud-Init templates, `100s` = core infra (`core`=110, `monitoring`=120), `200s` = workloads (`apps`=210, `media`=220, `accelerated`=230), `800s` = throwaway/experiments.

## Environment reality — read before assuming network access

This repo has been worked on from environments with very different network access. Don't assume the current one matches a past one — check first.

- **Cursor Cloud** (see `AGENTS.md`) — ephemeral sandbox, no real secrets/domains, Docker-in-Docker (node-exporter fails to start, disable observability sidecars when testing multiple stacks at once, `dockerd` must be started manually). Good only for compose/bootstrap syntax validation, never real deploys.
- **A prior sandboxed assistant environment** — fully isolated; no route to the tailnet or home LAN at all, regardless of credentials present.
- **Claude Code via WSL2 (current, if you're reading this from there)** — runs as a process on the user's real Ubuntu/WSL2 machine. If Tailscale is up and an SSH key is present, this environment has **real** network access to the Proxmox host and every VM. Don't default to "generate a diff for the user to run" here just because that was necessary elsewhere — check reachability first (`tailscale status`, `ssh <host> true`) and act directly when it makes sense.

**Actual VM hostnames/IPs, SSH users, and the Proxmox host address are intentionally not in this file** — this repo is public. See `CLAUDE.local.md` (gitignored, machine-local — copy `CLAUDE.local.md.example` and fill it in) for real connection details. If it's missing or incomplete, ask the user rather than guessing at an IP.

## Working with live infrastructure

This is **production** — it serves the user's home and (via `core`) is reachable from the internet on 80/443. "Cattle, not pets" is a rebuild philosophy, not license to change things unprompted.

- **Confirm before:** redeploying a stack, hand-editing a running compose/`.env` file on a VM, restarting a service with active connections, or touching anything in `core` (proxy/auth/DNS — breaking it can break access to everything else).
- **Safe to do without asking:** read-only checks — SSH in and look at logs/status, query Grafana/Prometheus/Loki/Uptime Kuma on `monitoring` (120), `docker compose ps`/`docker logs` on any VM.
- **Preferred change flow:** edit in the repo (not on the VM), commit, push, then on the target VM `cd /opt/self-hosting && git pull && python3 deploy.py <stack> -y` (repo is cloned to `/opt/self-hosting` on each VM per `DEPLOY.md`; each VM also gets a `~/<stack>` symlink after first deploy). Avoid hand-editing files directly on a VM — it drifts from the repo.
- `monitoring` is the lowest-risk VM for poking around — self-contained, nothing else depends on it being up.

## Key commands

| Task | Command |
|------|---------|
| Stage auto-detected `.env` values | `python3 scripts/setup_env.py` |
| Deploy a stack | `python3 deploy.py <stack> --init-env -y` |
| Deploy all stacks (single-host mode) | `python3 deploy.py all --init-env -y` |
| Validate compose | `docker compose -f docker_compose/<stack>/compose.yml config` |
| Lint Python | `ruff check deploy.py scripts/ docker_compose/` |
| Compile-check | `python3 -m py_compile <file>` |
| Lint shell scripts | `shellcheck $(find . -iname '*.sh' -not -path './.git/*')` |
| Lint YAML | `yamllint -c .yamllint docker_compose/` |

`--force` (skip `.env` placeholder validation) is a Cursor Cloud/testing-only flag — don't use it against real infra, where `.env` should already be populated with real values.

`ruff` here is installed as a standalone binary (not a pip package) — `ruff check ...` works, `python3 -m ruff` does not.

## No tests or CI

Validation is `py_compile` + `ruff` + `shellcheck` + `yamllint` + `docker compose config` + `BootstrapRunner`'s own `.env`/compose checks — there's no automated suite. When in doubt on a live VM, check the actual container/service state rather than trusting that config alone is correct.
