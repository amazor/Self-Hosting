---
name: grafana-dashboard-architect
description: Defines the architecture, philosophy, and structural contracts for the homelab Grafana dashboard system. Covers dashboard hierarchy, variable naming, drilldown flow, signal ownership, and scaling rules. Use when designing, building, or reviewing Grafana dashboards, panels, or queries for this homelab.
---

# Grafana Dashboard Architecture

This skill defines the **structural philosophy** for the homelab's Grafana dashboard system. It governs hierarchy, ownership boundaries, variable contracts, and drilldown flow. Individual dashboard specs live in reference files.

## Guiding Principles

1. **Overview is triage, workbenches are investigation.** The overview tells you WHAT is wrong and WHERE. Workbenches tell you WHY.
2. **Every dashboard knows its exit.** Every aggregated signal links to the next level of detail with context preserved.
3. **Variables are the API contract.** Same names, same semantics, across every dashboard. Drilldown URLs work without translation.
4. **Time is context.** Every drilldown preserves the time window. Grafana's time range IS the incident window.
5. **Labels are the schema.** The Alloy label contract defines the dashboard schema. No dashboard should reference labels outside that contract.
6. **Dashboards scale by labels, not by cloning.** New VMs appear automatically via label queries. Never hardcode VM names or service lists.
7. **Fewer dashboards, more variables.** Fight sprawl. A well-built workbench with good variables covers more ground than ten bespoke dashboards.

---

## Dashboard Hierarchy

```
D00: Homelab Overview               ← entry point (fleet health, triage)
 ├── D01: Infrastructure Workbench  ← host + container metrics deep dive
 ├── D02: Log Workbench             ← log exploration, error triage
 ├── D03: Network/Connectivity      ← probe results, DNS, throughput, TLS
 ├── D04: Media Pipeline            ← queue depth, transcoding, sessions (planned)
 └── D05: Hardware/Host             ← Proxmox host, temperatures, SMART (planned)
```

### Ownership Rules

| Dashboard | Owns | Does NOT Own |
|-----------|------|--------------|
| **D00 Overview** | Fleet health: availability, connectivity (public HTTP probe + internal DNS probe), error attribution (VM + service), change detection, resource saturation summaries, data freshness | Per-container detail, log text, service-specific metrics, historical trend analysis, warning-level log counts, per-probe breakdown |
| **D01 Infra Workbench** | Host metrics (CPU/RAM/disk/network/swap/inodes), container resource consumption, restart timeline, CPU steal time, OOM kill counter, I/O wait, load average, NFS mount filesystem metrics | Log content, application-level metrics, probe results |
| **D02 Log Workbench** | Log exploration, error/warn streams, log volume rates, pattern detection | Host resource metrics, container lifecycle |
| **D03 Network/Connectivity** | All Blackbox probe results, DNS resolution detail, inter-VM reachability matrix, per-service proxy health, network throughput per VM/container, packet errors/drops, TLS cert expiry | Host resource metrics, log content |
| **D04 Media Pipeline** | Download queue depth, indexer reachability, Plex/Jellyfin active sessions and transcoding load, library stats | Infrastructure metrics, log text |
| **D05 Hardware/Host** | Proxmox host CPU/RAM/storage, VM allocation vs capacity, CPU temperatures, SMART disk health, container image currency | Per-VM application metrics |

### When to Create an Exception Dashboard

Only when ALL of these are true:

1. The domain has signals that are meaningless outside its context (e.g., queue depth, download rates, library size)
2. Those signals cannot be surfaced via a workbench variable filter
3. The target audience would otherwise need to mentally join 2+ workbenches

If in doubt, add a row to a workbench first.

### Signals That Commonly Get Misplaced

| Signal | Wrong placement | Correct placement | Reason |
|--------|----------------|-------------------|--------|
| Network throughput per VM | D00 | D01 | No meaningful fleet threshold — 50 MB/s on media is normal, same on core is suspicious |
| Per-probe breakdown (which DNS/HTTP check failed) | D00 | D03 | D00 only needs binary pass/fail; investigation belongs on D03 |
| CPU steal time | D00 | D01 | Proxmox-specific signal; investigation context, not triage |
| OOM kill count | D01 only | D00 (change detection) + D01 (timeline) | An OOM kill is a fleet-level event worth surfacing immediately; `node_vmstat_oom_kill` is the metric |
| Inode exhaustion | disk panel | D01 separate panel | Standard disk % hides inode exhaustion; they are independent failure modes. `node_filesystem_files_free / node_filesystem_files` |
| TLS cert expiry | D00 | D03 (with days-to-expiry warning forwarded to D00 if critical) | Expiry timeline is investigation detail; only "expires in <3 days" is triage-urgent |
| NAS storage remaining | invisible (not currently shown) | D00 Disk panel (current %) + D04 (media volume trend) | VM root disks are shown but NAS is invisible. `node_filesystem_avail_bytes{fstype=~"nfs|nfs4"}` on the mounting VM requires no new tooling. |
| NFS mount health (stale/hung?) | D03 TCP probe alone | D03 TCP probe + D01 (mountpoint metrics present) + D02 (I/O error logs) | TCP to port 2049 confirms NAS is up; it does not confirm the mount is working. Three signals together cover the full failure surface. |
| Swap usage | memory panel | D01 separate stat | Swap being used at all means memory pressure exceeded RAM; deserves its own signal, not a footnote on the memory gauge |

---

## Source of Truth (Read Before Building)

Before designing panels or writing queries, read these files for the live state of what signals are available:

| File | What it tells you | Key functions / sections |
|------|-------------------|--------------------------|
| `docker_compose/monitoring/bootstrap.py` | Alloy label contract, Prometheus scrape targets, log normalization pipeline, Grafana datasource provisioning | `ensure_alloy_config()`, `ensure_prometheus_config()`, `ensure_grafana_provisioning()` |
| `docker_compose/monitoring/compose.yml` | Which metric/log containers exist, ports, network topology | Full file (short) |

The Alloy config in `ensure_alloy_config()` is the **canonical label contract** for logs. The Prometheus config in `ensure_prometheus_config()` mirrors the same contract for metrics via `metric_relabel_configs` on the cAdvisor job. If bootstrap.py and the tables below ever disagree, bootstrap.py wins.

### Planned Signal Sources (not yet in stack)

| Component | Adds | Needed by |
|-----------|------|-----------|
| **Blackbox Exporter** | HTTP/DNS/TCP/ICMP/TLS probes from monitoring VM | D00 Connectivity panel (HTTP + DNS + NAS TCP), D03 Network dashboard |
| **Proxmox node_exporter** (on pve1) | Host-level CPU, RAM, disk, temperatures | D05 Hardware/Host |
| **smartd_exporter** or `node_exporter` SMART collector | Disk SMART health data | D05 Hardware/Host |
| **Reverse proxy metrics endpoint** (Nginx stub_status, Traefik /metrics, Caddy /metrics) | Request rates, 5xx counts, response latency per service | D03 (per-service proxy health) |
| **Diun or Watchtower** | Container image update availability | D01 or D05 |
| **NAS node_exporter** (Synology community package or Docker) | NAS disk, CPU, RAM, temperature from the NAS itself | D04 (NAS storage trend), D05 (hardware health). Without this, NAS storage is visible only via the NFS-mounted filesystem metrics on the mounting VM — useful but indirect. |

**NAS/NFS signal availability without extra tooling:** If a VM (e.g., `media`) has the NAS NFS-mounted, node_exporter on that VM already exports `node_filesystem_avail_bytes{fstype=~"nfs|nfs4"}` for the mount path. This is collected by Prometheus today with no changes. The D00 Disk panel and D04 NAS storage trend can use this immediately for the NAS volumes that are mounted on that VM.

Do not design panels requiring these sources until the source is confirmed running and scraped by Prometheus.

---

## Label Contract (Quick Reference)

Summary of the labels available for dashboard queries. Authoritative source: `ensure_alloy_config()` in `bootstrap.py`.

### Required (always present on every log stream and metric series)

The `M` column indicates whether the label is also present on **Prometheus metrics** (via static_configs or cAdvisor metric_relabel_configs).

| Label | Meaning | Example | On Metrics |
|-------|---------|---------|------------|
| `node` | Proxmox host | `pve1` | M (all jobs) |
| `host` | VM hostname | `monitoring`, `core`, `media` | M (all jobs) |
| `vm_role` | VM's functional role | `monitoring`, `core`, `media`, `apps`, `accelerated` | M (all jobs) |
| `env` | Environment | `prod` | M (all jobs) |
| `service` | Logical service identity | `grafana`, `sonarr`, `alloy` | M (all jobs; cAdvisor via relabeling) |
| `container` | Container instance name | `grafana`, `sonarr-1` | M (cAdvisor via relabeling; alias for `name`) |
| `source` | Log origin type | `docker` | — (logs only) |

### Strong (present when available)

| Label | Meaning | On Metrics |
|-------|---------|------------|
| `level` | Normalized: `trace`, `debug`, `info`, `warn`, `error`, `fatal`, `unknown` | — (logs only) |
| `compose_project` | Docker Compose project name | M (cAdvisor via relabeling) |
| `image` | Container image (tag only, no digest) | M (cAdvisor; digest stripped via relabeling) |
| `stream` | `stdout` / `stderr` | — (logs only) |

### Compatibility (do not use as canonical in new dashboards)

| Label | Maps to |
|-------|---------|
| `job` | = `service` |
| `instance` | = `host` |

---

## Variable Contract

Every dashboard MUST use these variable names. This is what makes drilldown URLs portable.

### Cascade Order

```
$node → $vm_role → $host → $service
```

Each variable's query filters by the selections upstream. This ensures selecting `vm_role=media` only shows hosts and services that belong to the media VM.

### Variable Definitions

| Variable | Type | Multi | Default | Purpose |
|----------|------|-------|---------|---------|
| `$datasource_prometheus` | datasource | no | Prometheus | Allows switching without editing queries |
| `$datasource_loki` | datasource | no | Loki | Same for log queries |
| `$node` | query | yes | All | Proxmox node filter (multi-node future) |
| `$vm_role` | query | yes | All | Primary grouping axis |
| `$host` | query | yes | All | VM hostname, filtered by `$node` + `$vm_role` |
| `$service` | query | yes | All | Service identity, filtered by upstream vars |
| `$env` | custom | no | `prod` | Hidden; exists for future staging support |

Not every dashboard exposes every variable. The overview may hide `$service` (too granular). But the **names must match** so links work.

### Variable Queries (Pattern)

Variables cascade using label_values with filters:

- `$node`: `label_values(up, node)` or `label_values({__name__=~".+"}, node)`
- `$vm_role`: `label_values({node=~"$node"}, vm_role)` (metrics) or `label_values({node=~"$node"}, vm_role)` (logs)
- `$host`: `label_values({node=~"$node", vm_role=~"$vm_role"}, host)`
- `$service`: `label_values({host=~"$host"}, service)`

---

## Drilldown Flow

### URL Contract

Drilldown links pass context via URL parameters:

```
/d/<dashboard-uid>?from=${__from}&to=${__to}&var-vm_role=${vm_role}&var-host=${host}
```

Every drilldown link MUST include:

1. **Time range**: `&from=${__from}&to=${__to}`
2. **Relevant variables**: only the ones the target dashboard uses

### Flow Map

```
D00 (Overview)
 │
 ├─ [VM status / saturation] ──→ D01 (Infra Workbench)
 │     passes: time, $vm_role, $host
 │
 ├─ [Error rate spike] ──→ D02 (Log Workbench)
 │     passes: time, $vm_role, $host, level=error
 │
 └─ [Domain signal] ──→ D03+ (Exception)
       passes: time, $vm_role, $host

D01 (Infra Workbench)
 │
 └─ [Container with errors] ──→ D02 (Log Workbench)
       passes: time, $host, $service
```

### Incident Window Preservation

The Grafana time range IS the incident window. No custom time variables needed.

- Narrowing the time range on any dashboard focuses the investigation
- Drilldown links carry `from` and `to` — the receiving dashboard opens at the exact same window
- Annotations or alert markers that draw attention to a spike should be clickable entry points to drilldowns

---

## Interactivity Contract

Every visible signal should be clickable. Dashboards follow a **Focus → Investigate** progressive disclosure pattern. The Drilldown Flow section above covers cross-dashboard URL mechanics. This section defines the click *philosophy* — what happens when the operator clicks, and what insight they should expect next.

### Progressive Disclosure

Two click depths, applied consistently across all dashboards:

1. **Focus (same dashboard):** Clicking a series, row, or cell narrows the current view. A time series showing all containers' network traffic, when one container's line is clicked, updates `$service` to that container. The dashboard re-renders showing only that container's data across all panels.

2. **Investigate (cross-dashboard):** From a focused view, a second click drills to the workbench that owns the next level of detail. From the focused container on D01, a "View Logs" link opens D02 pre-filtered to that service at the same time window.

Every visible anomaly should have a click path to its explanation. The operator should never see something interesting without knowing where to click next.

### Implementation Patterns

**Same-dashboard focus (variable update via self-link):**

```
/d/${__dashboard.uid}?var-service=${__data.fields.service}&from=${__from}&to=${__to}&var-node=${node}&var-vm_role=${vm_role}&var-host=${host}
```

Navigates to the same dashboard with one variable narrowed. All upstream variables are preserved to prevent cascade resets. The dashboard re-renders with narrowed scope.

**Cross-dashboard drilldown:**

```
/d/<target-uid>?var-host=${__data.fields.host}&var-service=${__data.fields.service}&from=${__from}&to=${__to}
```

**Rules for all click links:**

- Every link includes `from=${__from}&to=${__to}` — the time window is the incident context
- Same-dashboard focus links preserve all upstream variables (`$node`, `$vm_role`, `$host`) to prevent cascade resets
- Cross-dashboard links include only the variables the target dashboard uses
- Use `${__data.fields.*}` for row/cell-specific values (tables, stat panels)
- Use `${variable_name}` for current dashboard variable values (filter-wide drilldowns)

### What the Next Click Should Show

The expected next insight depends on what the operator is looking at:

| Signal type on screen | Focus action (same dashboard) | Investigate action (cross-dashboard) |
|----------------------|------------------------------|-------------------------------------|
| Multi-host resource metric | Filter to one host | → D01 (from D00) or → D02 logs (from D01) |
| Multi-container time series | Update `$service` to one container | → D02 for that service's logs |
| Error count or rate | Update `$service` to one service | → D02 with `level=error` |
| Container restart event | — (already specific) | → D02 with time narrowed to restart |
| Probe failure | — (already specific) | → D02 for service logs; D01 for host health |
| High network throughput | Filter to one host or container | → D03 (from D01) or → D02 logs |
| Disk/storage warning | Filter to specific mount | → D02 for I/O error logs |
| Log error pattern | Filter log stream to that pattern | Terminal — operator reads and acts |
| Physical hardware signal | — (already specific) | → D01 for the affected VM |

### Click Depth by Dashboard

| Dashboard | Typical depth | Pattern |
|-----------|--------------|---------|
| **D00 Overview** | 1 click | Always routes outward to a workbench. D00 never focuses itself. |
| **D01 Infra** | 1–2 clicks | Click table row → focus on container (1). Click focused panel → D02 logs (2). |
| **D02 Logs** | 1–2 clicks | Click service line → focus (1). Read logs → act (terminal). |
| **D03 Network** | 1–2 clicks | Click VM in throughput → focus (1). Click focused signal → D02 or D01 (2). |
| **D04 Media** | 1 click | Click pipeline stage → D02 for logs or D01 for resources. |
| **D05 Hardware** | 1 click | Click VM in allocation → D01 for that VM. |

### D00 Is Always Depth 1

The overview never focuses itself. Every click on D00 routes to a workbench. This is by design — the overview's job is routing, not investigation. If the instinct is "filter D00 to just this VM," the right action is opening D01 for that VM.

Each dashboard spec includes a **Click Flow Map** table documenting every clickable element, its action type (focus or investigate), and its target.

---

## Scaling Rules

### Adding a New VM (e.g., Security)

Zero dashboard changes required if you follow this architecture:

1. Deploy the VM with Alloy sidecar using the label contract (`vm_role=security`, `host=security`, etc.)
2. Add Prometheus scrape targets for the new VM's node_exporter/cAdvisor
3. The new VM appears automatically in all dashboards via label queries

This works because:

- All variable queries use `label_values()` — new label values appear dynamically
- All panels use variable-filtered queries (`{vm_role=~"$vm_role"}`) — new VMs are included by default
- Repeating rows/panels (repeat by `$host` or `$vm_role`) generate new visual sections automatically

### Adding a New Signal Source

If you add a new exporter or metric source:

1. It goes on the **workbench** that owns that signal type (infra metrics → D01, logs → D02)
2. Only if the signal reveals a fleet-level health condition does it get a **summary** on D00
3. D00 summaries are always aggregations (counts, rates, max), never raw series

---

## Dashboard-Specific Specs

| Dashboard | File | Status |
|-----------|------|--------|
| D00 — Homelab Overview | [dashboard-00-overview.md](dashboard-00-overview.md) | Built |
| D01 — Infrastructure Workbench | [dashboard-01-infra-workbench.md](dashboard-01-infra-workbench.md) | Planning |
| D02 — Log Workbench | [dashboard-02-log-workbench.md](dashboard-02-log-workbench.md) | Planning |
| D03 — Network/Connectivity | [dashboard-03-network.md](dashboard-03-network.md) | Planning |
| D04 — Media Pipeline | [dashboard-04-media-pipeline.md](dashboard-04-media-pipeline.md) | Planning — requires exportarr, qbittorrent-exporter, Jellyfin metrics |
| D05 — Hardware/Host | [dashboard-05-hardware-host.md](dashboard-05-hardware-host.md) | Planning — requires node_exporter on pve1, SMART collector |
