---
name: grafana-dashboard-architect
description: Defines the architecture, philosophy, and structural contracts for the homelab Grafana dashboard system. Covers dashboard hierarchy, variable naming, drilldown flow, signal ownership, and scaling rules. Use when designing, building, or reviewing Grafana dashboards, panels, or queries for this homelab.
---

# Grafana Dashboard Architecture

This skill defines the **structural philosophy** for the homelab's Grafana dashboard system. It governs hierarchy, ownership boundaries, variable contracts, and drilldown flow. Individual dashboard specs live in reference files.

> **Grafana version:** This skill targets **Grafana v12.x** (current stable: v12.3). Dashboard JSON uses the legacy schema (v1). The experimental schema v2 (dynamic dashboards, `GridLayout`/`TabsLayout`) is not adopted — it requires feature toggles and may cause irreversible changes to saved dashboards.
>
> **Reference docs:** [Variable syntax](https://grafana.com/docs/grafana/latest/visualizations/dashboards/variables/variable-syntax/) · [Prometheus template variables](https://grafana.com/docs/grafana/latest/datasources/prometheus/template-variables) · [Data links & actions](https://grafana.com/docs/grafana/latest/panels-visualizations/configure-data-links) · [Dashboard URL variables](https://grafana.com/docs/grafana/latest/dashboards/build-dashboards/create-dashboard-url-variables) · [Global variables](https://grafana.com/docs/grafana/latest/dashboards/variables/add-template-variables/#global-variables)

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
 ├── D01a: Host Workbench           ← VM-level metrics (CPU, memory, disk, I/O)
 ├── D01b: Container Workbench      ← container resource consumption, lifecycle, per-service detail
 ├── D02: Log Workbench             ← log exploration, error triage
 ├── D03: Network/Connectivity      ← probe results, DNS, throughput, TLS
 ├── D04: Media Pipeline            ← queue depth, transcoding, sessions (planned)
 └── D05: Hardware/Host             ← Proxmox host, temperatures, SMART (planned)
```

### Physical Topology Mapping

The dashboard hierarchy maps directly to the physical infrastructure layers. No dedicated "data center" or "node" dashboard is needed — the existing dashboards cover every level via the variable cascade.

| Physical Layer | Dashboard | Primary Variable | What You See |
|----------------|-----------|------------------|--------------|
| Fleet / DC | D00 Overview | `$node=All` | Aggregated health across all nodes and VMs |
| Proxmox Node | D05 Hardware/Host | `$node` | Bare-metal CPU, temps, SMART, VM allocation |
| VM | D01a Host Workbench | `$host` | Per-VM CPU modes, memory, disk, I/O |
| Container | D01b Container Workbench | `$service` | Per-container CPU, memory, network, lifecycle |

### Ownership Rules

| Dashboard | Owns | Does NOT Own |
|-----------|------|--------------|
| **D00 Overview** | Fleet health: availability, connectivity (public HTTP probe + internal DNS probe), error attribution (VM + service), change detection, resource saturation summaries, data freshness | Per-container detail, log text, service-specific metrics, historical trend analysis, warning-level log counts, per-probe breakdown |
| **D01a Host Workbench** | Host metrics (CPU/RAM/disk/swap/inodes), CPU steal time, OOM kill counter, I/O wait, I/O latency, load average, NFS mount filesystem metrics, compact container summary table (bridge to D01b) | Per-container time series, container lifecycle/restart timeline, log content, application-level metrics, probe results |
| **D01b Container Workbench** | Container resource consumption (CPU, memory, Mem Limit %, network), restart timeline, per-container detail time series, container lifecycle events | Host CPU modes, host memory breakdown, disk I/O, log content, probe results |
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
| Network throughput per VM | D00 | D01a | No meaningful fleet threshold — 50 MB/s on media is normal, same on core is suspicious |
| Per-probe breakdown (which DNS/HTTP check failed) | D00 | D03 | D00 only needs binary pass/fail; investigation belongs on D03 |
| CPU steal time | D00 | D01a | Proxmox-specific signal; investigation context, not triage |
| OOM kill count | D01a only | D00 (change detection) + D01a (timeline) | An OOM kill is a fleet-level event worth surfacing immediately; `node_vmstat_oom_kill` is the metric |
| Inode exhaustion | disk panel | D01a separate panel | Standard disk % hides inode exhaustion; they are independent failure modes. `node_filesystem_files_free / node_filesystem_files` |
| TLS cert expiry | D00 | D03 (with days-to-expiry warning forwarded to D00 if critical) | Expiry timeline is investigation detail; only "expires in <3 days" is triage-urgent |
| NAS storage remaining | invisible (not currently shown) | D00 Disk panel (current %) + D04 (media volume trend) | VM root disks are shown but NAS is invisible. `node_filesystem_avail_bytes{fstype=~"nfs|nfs4"}` on the mounting VM requires no new tooling. |
| NFS mount health (stale/hung?) | D03 TCP probe alone | D03 TCP probe + D01a (mountpoint metrics present) + D02 (I/O error logs) | TCP to port 2049 confirms NAS is up; it does not confirm the mount is working. Three signals together cover the full failure surface. |
| Swap usage | memory panel | D01a separate stat | Swap being used at all means memory pressure exceeded RAM; deserves its own signal, not a footnote on the memory gauge |
| Container Mem Limit % | D01a host view | D01b container resource table | Memory limit pressure is per-container investigation; meaningless without container context |
| Container restart timeline | D01a host view | D01b container lifecycle section | Restart events are container-level signals; the host workbench only needs the summary count in the bridge table |

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
| **Diun or Watchtower** | Container image update availability | D01b or D05 |
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
| `host` | VM hostname (globally unique) | `monitoring`, `core`, `media` | M (all jobs) |
| `vm_role` | VM's functional role | `monitoring`, `core`, `media`, `apps`, `accelerated` | M (all jobs) |
| `env` | Environment | `prod` | M (all jobs) |
| `service` | Logical service identity | `grafana`, `sonarr`, `alloy` | M (all jobs; cAdvisor via relabeling) |
| `container` | Container instance name | `grafana`, `sonarr-1` | M (cAdvisor via relabeling; alias for `name`) |
| `source` | Log origin type | `docker` | — (logs only) |

**`host` uniqueness constraint:** `host` MUST be globally unique across all Proxmox nodes. Every `by (host)` grouping and `{{host}}` legend format in the dashboard system relies on this. The Proxmox HA singleton model guarantees it: each VM role exists once in the cluster and migrates between nodes as needed, keeping its hostname. If two VMs on different nodes shared a hostname, queries would silently merge their metrics. The `(node, host)` pair provides additional context (which physical node is running this VM right now), but `host` alone is sufficient for identity.

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
| `$node` | query | yes | All | Proxmox node filter (currently single-node `pve1`; multi-node not planned but supported by the label contract) |
| `$vm_role` | query | yes | All | Primary grouping axis |
| `$host` | query | yes | All | VM hostname, filtered by `$node` + `$vm_role` |
| `$service` | query | yes | All | Service identity, filtered by upstream vars |
| `$env` | custom | no | `prod` | Hidden; exists for future staging support |

Not every dashboard exposes every variable. The overview may hide `$service` (too granular). But the **names must match** so links work.

### Variable Format Options (Reference)

When interpolating variables in queries or URLs, Grafana supports format suffixes. The most useful for this dashboard system:

| Format | Syntax | Output | Use case |
|--------|--------|--------|----------|
| Default (regex) | `${host}` | `(media\|core)` | PromQL/LogQL label matchers (automatic) |
| Pipe | `${host:pipe}` | `media\|core` | Manual regex contexts |
| CSV | `${host:csv}` | `media,core` | Non-regex contexts |
| Query param | `${host:queryparam}` | `var-host=media&var-host=core` | Building drilldown URLs — handles multi-value correctly |
| Raw | `${host:raw}` | `media,core` | Unescaped comma-separated; use when Grafana's auto-escaping interferes |
| Percent encode | `${host:percentencode}` | `media%2Ccore` | URL-safe encoding for external links |

> The `:queryparam` format is particularly useful for drilldown URLs when a variable might have multiple values selected. Instead of `var-host=${host}` (which produces a single regex-escaped string), `${host:queryparam}` produces proper multi-value URL parameters that Grafana interprets correctly on the target dashboard.

### Variable Queries (Pattern)

Variables cascade using **Label values** queries with optional metric filters.

> **Grafana v12+ (structured query editor):** The Prometheus data source provides a dropdown-based **Query type** selector. Use the **Label values** query type with explicit `label` and optional `metric` fields instead of the legacy `label_values()` string syntax, which is now classified as **Classic query** and deprecated.

#### Structured query editor (preferred — Grafana v12+)

| Variable | Query Type | Label | Metric (filter) |
|----------|-----------|-------|-----------------|
| `$node` | Label values | `node` | `up` |
| `$vm_role` | Label values | `vm_role` | `up{node=~"$node"}` |
| `$host` | Label values | `host` | `up{node=~"$node", vm_role=~"$vm_role"}` |
| `$service` | Label values | `service` | `up{host=~"$host"}` |

#### Classic query syntax (deprecated — avoid in new dashboards)

```
label_values(up, node)
label_values({node=~"$node"}, vm_role)
label_values({node=~"$node", vm_role=~"$vm_role"}, host)
label_values({host=~"$host"}, service)
```

If the existing dashboards still use classic `label_values()` strings, they continue to work. Migrate to the structured editor when next editing variable definitions.

---

## Drilldown Flow

### URL Contract

Drilldown links pass context via URL parameters:

```
/d/<dashboard-uid>?${__url_time_range}&var-vm_role=${vm_role}&var-host=${host}
```

Every drilldown link MUST include:

1. **Time range**: `${__url_time_range}` (expands to `from=<epoch>&to=<epoch>`) or the explicit `from=${__from}&to=${__to}` form
2. **Relevant variables**: only the ones the target dashboard uses

> **Grafana v11+:** `${__url_time_range}` is the preferred shorthand for time range propagation. It produces the same `from=...&to=...` query string but avoids manual parameter assembly. The explicit `from=${__from}&to=${__to}` form still works and is equivalent.

### Flow Map

```
D00 (Overview)
 │
 ├─ [VM status / saturation / OOM] ──→ D01a (Host Workbench)
 │     passes: time, $vm_role, $host
 │
 ├─ [Container deficit / restarts] ──→ D01b (Container Workbench)
 │     passes: time, $vm_role, $host
 │
 ├─ [Error rate spike] ──→ D02 (Log Workbench)
 │     passes: time, $vm_role, $host, level=error
 │
 └─ [Domain signal] ──→ D03+ (Exception)
       passes: time, $vm_role, $host

D01a (Host Workbench)
 │
 └─ [Container summary table row] ──→ D01b (Container Workbench)
       passes: time, $host, $service

D01b (Container Workbench)
 │
 └─ [Container with errors / "View Logs"] ──→ D02 (Log Workbench)
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

2. **Investigate (cross-dashboard):** From a focused view, a second click drills to the workbench that owns the next level of detail. From the focused container on D01b, a "View Logs" link opens D02 pre-filtered to that service at the same time window.

Every visible anomaly should have a click path to its explanation. The operator should never see something interesting without knowing where to click next.

### Implementation Patterns

**Same-dashboard focus (variable update via self-link):**

```
/d/${__dashboard.uid}?${__url_time_range}&var-service=${__data.fields.service}&var-node=${node}&var-vm_role=${vm_role}&var-host=${host}
```

Navigates to the same dashboard with one variable narrowed. All upstream variables are preserved to prevent cascade resets. The dashboard re-renders with narrowed scope.

**Cross-dashboard drilldown:**

```
/d/<target-uid>?${__url_time_range}&var-host=${__data.fields.host}&var-service=${__data.fields.service}
```

**Rules for all click links:**

- Every link includes `${__url_time_range}` (or the equivalent `from=${__from}&to=${__to}`) — the time window is the incident context
- Same-dashboard focus links preserve all upstream variables (`$node`, `$vm_role`, `$host`) to prevent cascade resets
- Cross-dashboard links include only the variables the target dashboard uses
- Use `${__data.fields.<name>}` for row/cell-specific values (tables, stat panels)
- Use `${__field.labels.<labelname>}` when you need a specific Prometheus/Loki label from the series (alternative to `${__data.fields.*}` when the field name matches a label)
- Use `${variable_name}` for current dashboard variable values (filter-wide drilldowns)

### What the Next Click Should Show

The expected next insight depends on what the operator is looking at:

| Signal type on screen | Focus action (same dashboard) | Investigate action (cross-dashboard) |
|----------------------|------------------------------|-------------------------------------|
| Multi-host resource metric | Filter to one host | → D01a (from D00) or → D02 logs (from D01a) |
| Multi-container time series | Update `$service` to one container | → D02 for that service's logs |
| Error count or rate | Update `$service` to one service | → D02 with `level=error` |
| Container restart event | — (already specific) | → D02 with time narrowed to restart |
| Probe failure | — (already specific) | → D02 for service logs; D01a for host health |
| High network throughput | Filter to one host or container | → D03 (from D01a) or → D02 logs |
| Disk/storage warning | Filter to specific mount | → D02 for I/O error logs |
| Log error pattern | Filter log stream to that pattern | Terminal — operator reads and acts |
| Physical hardware signal | — (already specific) | → D01a for the affected VM |
| Container summary row (on D01a) | — (bridge table, no focus) | → D01b with `$host` + `$service` |

### Click Depth by Dashboard

| Dashboard | Typical depth | Pattern |
|-----------|--------------|---------|
| **D00 Overview** | 1 click | Always routes outward to a workbench. D00 never focuses itself. |
| **D01a Host** | 1–2 clicks | Click host panel → focus on host (1). Click bridge table row → D01b (2). |
| **D01b Container** | 1–2 clicks | Click table row → focus on container (1). Click focused panel → D02 logs (2). |
| **D02 Logs** | 1–2 clicks | Click service line → focus (1). Read logs → act (terminal). |
| **D03 Network** | 1–2 clicks | Click VM in throughput → focus (1). Click focused signal → D02 or D01a (2). |
| **D04 Media** | 1 click | Click pipeline stage → D02 for logs or D01a for resources. |
| **D05 Hardware** | 1 click | Click VM in allocation → D01a for that VM. |

### D00 Is Always Depth 1

The overview never focuses itself. Every click on D00 routes to a workbench. This is by design — the overview's job is routing, not investigation. If the instinct is "filter D00 to just this VM," the right action is opening D01a for that VM.

Each dashboard spec includes a **Click Flow Map** table documenting every clickable element, its action type (focus or investigate), and its target.

---

## Scaling Rules

### Adding a New VM (e.g., Security)

Zero dashboard changes required if you follow this architecture:

1. Deploy the VM with Alloy sidecar using the label contract (`vm_role=security`, `host=security`, etc.)
2. Add Prometheus scrape targets for the new VM's node_exporter/cAdvisor
3. The new VM appears automatically in all dashboards via label queries

This works because:

- All variable queries use **Label values** query types — new label values appear dynamically
- All panels use variable-filtered queries (`{vm_role=~"$vm_role"}`) — new VMs are included by default
- Repeating rows/panels (repeat by `$host` or `$vm_role`) generate new visual sections automatically

### Adding a New Proxmox Node (e.g., pve2)

> **Current state:** The homelab runs on a single Proxmox node (`pve1`). There are no plans to add a second node. This section documents the scaling path so the architecture remains future-proof without requiring dashboard changes if that decision changes.

Zero dashboard changes required. The `$node` variable and label contract already handle multi-node:

1. Deploy node_exporter on the new bare-metal host with labels `host=pve2`, `vm_role=hypervisor`, `node=pve2`
2. Deploy VMs on the new node using the standard label contract (each VM gets a globally unique `host` name)
3. Add Prometheus scrape targets for the new node's exporters and its VMs' node_exporter/cAdvisor
4. The `$node` dropdown auto-populates with `pve2` via the Label values query. D05 filters to the new node. D00 aggregates across both nodes. D01a/D01b show the new VMs.

This works because every query already filters by `node=~"$node"` and the variable cascade `$node → $vm_role → $host → $service` scopes downstream selections automatically. No dashboards need cloning, no queries need editing.

**VM migration and time series continuity:** When Proxmox live-migrates a VM between nodes (HA failover or manual rebalancing), the VM keeps its `host` name but the `node` label changes (e.g., `node=pve1` → `node=pve2`). Prometheus treats this as a new time series. Over a time range that spans the migration, panels show two short series for that host instead of one continuous line. This is expected behavior — the break in the line marks exactly when the migration occurred, which is useful forensic context. It does not affect correctness: both series carry the same `host` label, so `by (host)` groupings still identify the VM correctly.

### Adding a New Signal Source

If you add a new exporter or metric source:

1. It goes on the **workbench** that owns that signal type (host metrics → D01a, container metrics → D01b, logs → D02)
2. Only if the signal reveals a fleet-level health condition does it get a **summary** on D00
3. D00 summaries are always aggregations (counts, rates, max), never raw series

---

## Visualization Reference

Grafana ships a broad set of visualization types. Choosing the right one matters — a gauge where a stat belongs wastes space; a time series where a state timeline belongs hides discrete events. This catalog groups every built-in visualization by the kind of data it represents, with notes on when each is the right (and wrong) choice for this dashboard system.

### Graphs & Charts (continuous data over time or categories)

| Visualization | Data shape | When to use | When NOT to use |
|--------------|------------|-------------|-----------------|
| **Time series** | Metric values over time | Default for any `rate()`, `increase()`, or raw metric over time. Supports alerts. | Discrete states (use State timeline). Single values (use Stat). |
| **Bar chart** | Categorical data (labels on one axis, values on the other) | Comparing values across categories at a point in time (e.g., CPU per VM as a snapshot). | Time-based trends (use Time series). |
| **Histogram** | Distribution of values | Showing how values are distributed across buckets (e.g., request latency distribution). | Trends over time. Small sample sizes. |
| **Heatmap** | Two-dimensional density (typically value × time) | Visualizing request latency percentiles over time, or any "how much of X falls in range Y at time T" question. | Simple time series. Categorical comparisons. |
| **XY chart** | Arbitrary x/y numeric pairs | Scatter plots, correlation analysis (CPU vs. memory). | Time series data. |
| **Candlestick** | OHLC (open/high/low/close) financial-style data | Price or stock data. Rarely used in infrastructure monitoring. | General metrics. |
| **Pie chart** | Proportional breakdown of parts to a whole | Log level distribution, storage allocation breakdown. | Comparisons over time. More than 6–7 slices (becomes unreadable). |
| **Trend** | Sequential numeric x (not time) | Datasets with a numeric sequence axis that isn't time. | Time-based data (use Time series). |

### Stats & Gauges (single values, current state)

| Visualization | Data shape | When to use | When NOT to use |
|--------------|------------|-------------|-----------------|
| **Stat** | Single value per series, optional sparkline | Fleet Pulse indicators, KPI numbers, any "big number" display. The sparkline adds trend context without a full graph. | Detailed breakdowns. Range comparisons. |
| **Gauge** | Single value relative to min/max | Showing how close a value is to a limit (CPU % toward 100%, memory toward OOM threshold). | Values without meaningful min/max bounds. |
| **Bar gauge** | Single value per series, shown as horizontal/vertical bar | Per-VM resource saturation (CPU/Memory/Disk %). Three display modes: basic, gradient, LCD. | Trend data. More than ~10 bars. |

### State & Timeline (discrete events, status changes)

| Visualization | Data shape | When to use | When NOT to use |
|--------------|------------|-------------|-----------------|
| **State timeline** | Discrete state values over time | Container lifecycle (running/stopped/restarting), service health states. Consecutive identical states merge into solid bands. Thresholds map numbers to state colors. | Continuous metrics. High-cardinality states. |
| **Status history** | Periodic state samples over time | Similar to state timeline but does NOT merge consecutive values — each sample is a distinct colored box. Better for periodic polling data. | Continuous metrics. |

### Tables & Data

| Visualization | Data shape | When to use | When NOT to use |
|--------------|------------|-------------|-----------------|
| **Table** | Tabular rows × columns | Ranked lists (Top Offenders), bridge tables, any multi-column data. Supports cell display modes: colored background, sparkline, gauge, JSON view, image. Supports row coloring via `applyToRow`. | Simple single-value displays. |

**Table cell display modes (v12):**
- **Auto** — default rendering
- **Color text** — colors the text based on thresholds
- **Color background** — fills the cell background; supports `applyToRow: true` for full-row coloring (Grafana 11+)
- **Color background (gradient)** — gradient fill based on value
- **Gauge** — inline horizontal gauge within the cell
- **Sparkline** — embedded mini time series within a table cell (requires time series data in the query)
- **JSON view** — renders JSON objects with syntax highlighting
- **Image** — renders URL values as images
- **CSS styling** — (Grafana 12.3+) apply arbitrary CSS properties via a JSON field using the "Styling from field" cell option

### Logs, Traces, Profiles

| Visualization | Data shape | When to use | When NOT to use |
|--------------|------------|-------------|-----------------|
| **Logs** | Log streams from Loki or similar | D02 Log Workbench — the primary log viewing panel. | Metrics data. |
| **Traces** | Distributed trace spans | Trace investigation. Not used in this homelab stack (no tracing). | Non-trace data. |
| **Flame graph** | Profiling data (call stacks) | CPU/memory profiling investigation. Not used in this homelab stack. | Non-profiling data. |
| **Node graph** | Directed graph (nodes + edges) | Service dependency maps, network topology. Potential use for inter-VM connectivity visualization on D03 if data source supports it. | Tabular or time series data. |

### Spatial, Freeform, Widgets

| Visualization | Data shape | When to use | When NOT to use |
|--------------|------------|-------------|-----------------|
| **Canvas** | Freeform element placement | Custom infrastructure diagrams, NOC-style status boards with positioned elements bound to data. Supports buttons with API actions, icons, shapes, server elements. One-click data links and actions are GA as of Grafana 12. | Standard metric display (use built-in panels). |
| **Geomap** | Geospatial coordinates | Location-aware data. Not relevant for this homelab. | Non-geographic data. |
| **Datagrid** | Editable tabular data | Manually creating/editing data that feeds other panels. Niche use. | Read-only data display. |
| **Text** | Markdown or HTML | Dashboard instructions, notes, section headers. | Data display. |
| **News** | RSS feeds | Showing external news/updates on a dashboard. | Internal metrics. |
| **Annotations list** | Grafana annotation events | Listing deployment markers, incident notes. | General data. |
| **Alert list** | Grafana alert states | Showing current alert states and recent alert transitions. | Non-alert data. |
| **Dashboard list** | Dashboard metadata | Navigation panels linking to other dashboards. | Data display. |

---

## Transformations & Expressions Reference

Transformations manipulate query results client-side before rendering. Expressions compute server-side and can combine data across data sources. Both are critical tools for building the cross-query panels in this dashboard system (e.g., the Top Offenders table merging Loki error counts with cAdvisor restart data).

### Transformations (client-side, per-panel)

Transformations apply in order — each step receives the output of the previous step. Dashboard variables are interpolated before transformations run.

| Transformation | What it does | Used in this system |
|---------------|-------------|---------------------|
| **Merge** | Combines frames with matching fields into one frame (UNION-style). Matches by ALL shared label fields. | D00 Top Offenders: merging Loki error counts + cAdvisor restart counts |
| **Join by field** | Joins frames on a specific field (like SQL JOIN). Use when frames share a key but have different columns. | Pairing metrics from different queries by `host` or `service` |
| **Organize fields** | Rename, reorder, hide columns. | Every table panel — hide raw labels like `env`, `__name__`, `instance` |
| **Filter by name** | Show/hide specific fields by name or regex. | Removing internal Prometheus labels from table views |
| **Filter data by values** | Keep/exclude rows matching conditions (e.g., value > 0). | D00 Top Offenders: hiding zero-error rows |
| **Group by** | Group rows by field values and apply aggregations (sum, mean, count, min, max, first, last). | Aggregating per-service metrics before display |
| **Sort by** | Sort rows by a field. | Ranking Top Offenders by error count descending |
| **Reduce** | Collapse each series to a single value (last, mean, sum, count, min, max). | Converting time series to table values for instant-query tables |
| **Concatenate fields** | Combine all fields from all frames into a single frame. | Merging independent query results into one table |
| **Add field from calculation** | Create computed columns (binary math, unary ops, cumulative functions, window functions). | Health % column = Up / Expected |
| **Config from query results** | Use one query's results to dynamically set another query's panel config (thresholds, min, max, units, value mappings). | Dynamic thresholds from a config query — e.g., different CPU warning levels per VM role |
| **Convert field type** | Change field types (string → time, number → enum). | Formatting timestamps in restart tables |
| **Extract fields** | Parse JSON, key-value pairs, or regex from a field into separate fields. | Extracting structured data from log metadata |
| **Rows to fields** | Transpose rows into field configurations. Often paired with Config from query results. | Building per-host dynamic configurations |

**Non-obvious capabilities:**
- **Config from query results** can build value mappings dynamically from a query. A config query returning `Value | Text | Color` columns creates mappings applied to the real data query — enabling data-driven color schemes without hardcoding.
- **Filter by query** — when a panel has multiple queries, a transformation can be restricted to only one query's output using the filter icon on the transformation row.
- **Debug mode** — click the bug icon on any transformation row to see its input and output data frames side-by-side. Essential when chaining multiple transformations.

### Server-Side Expressions (cross-query computation)

Expressions run on the Grafana server, not the browser. They work with any backend data source and are the foundation of Grafana Alerting rules. Useful when combining metrics from Prometheus and Loki in the same panel, or performing math that neither data source can do natively.

| Expression type | What it does | Example |
|----------------|-------------|---------|
| **Math** | Free-form arithmetic, relational, and logical operations between query results. Supports `abs()`, `log()`, `round()`, `ceil()`, `floor()`, `is_nan()`, `is_null()`, `is_inf()`. | `$A / $B * 100` to compute percentage from two separate queries |
| **Reduce** | Collapse a time series into a single number (mean, min, max, sum, count, last). Labels are preserved. | Converting a time series to an instant value for alerting thresholds |
| **Resample** | Align timestamps across series to a common interval. Required when doing math between series with different scrape intervals. Supports pad, backfill, and fillna for empty windows. | Aligning node_exporter (15s) and cAdvisor (30s) metrics before division |

**Union semantics in Math:** When combining `$A + $B`, the expression engine joins by matching labels. If `$A` has `{host=media}` and `$B` has `{host=media}`, they match. Partial label matches (subset) also join. Two single-item collections always join regardless of labels.

---

## Best Practices & Non-Obvious Insights

### Query Performance

1. **`$__rate_interval` over hardcoded windows.** Always use `[$__rate_interval]` in `rate()` and `increase()` calls. It auto-calculates to at least 4× the scrape interval, preventing "no data" gaps when zooming in. Hardcoded `[5m]` breaks on scrape intervals that aren't 15s.

2. **Max Data Points limits what Prometheus returns.** The "Max data points" panel setting (default: panel width in pixels) controls how many points Prometheus sends. For a 1200px-wide panel showing 30 days, Prometheus only sends ~1200 points — one every ~36 minutes. This is why zooming out doesn't slow queries but reduces resolution. Set explicitly only when you need to override (e.g., stat panels where you want exactly 1 point).

3. **Instant queries for tables, range queries for time series.** Table panels showing "current value" should use instant queries (toggle in the Prometheus query editor). This returns one value per series instead of a full time range, reducing data transfer significantly.

4. **Recording rules for expensive aggregations.** If a panel query takes >2s or uses complex `by()` / `without()` grouping across many series, create a Prometheus recording rule to pre-compute it. The dashboard queries the recording rule's output metric instead. Naming convention: `level:metric:operations` (e.g., `job:container_cpu_usage:rate5m`).

5. **`-- Mixed --` data source for cross-source panels.** To combine Prometheus metrics and Loki log counts in a single panel (like the Top Offenders table), select the `-- Mixed --` built-in data source. Each query row can then target a different data source. Combine the results with `merge` or `join` transformations.

### Panel Design

6. **Value mappings bypass unit formatting.** When a value mapping matches, the unit format is skipped entirely. Use this to show "All Up" instead of "0" on the Container deficit stat, or "—" instead of "0.00%" for containers without memory limits. Mapping types: exact value, numeric range, regex, and special (null/NaN/boolean).

7. **Field overrides by regex for bulk styling.** Instead of overriding each field individually, use "Fields with name matching regex" to apply overrides to all fields matching a pattern. Example: `/.*(transmit|tx).*/i` to color all transmit series blue across a network panel, regardless of the exact field name.

8. **`applyToRow: true` for table row coloring.** In table panels, the `Color background` cell display mode with `applyToRow: true` in the field override colors the entire row based on one column's value — not just that cell. Used in D00 Containers Running table to make unhealthy rows immediately visible.

9. **Annotations mark events on time series.** Annotations draw vertical lines on time series panels marking specific events (deployments, restarts, config changes). They can be added manually, via API, or from a data source query. Use annotation queries against Loki to auto-mark container restart events on D01a time series panels — `{service="alloy"} |= "container started"` as an annotation source makes restarts visible without a separate panel.

10. **State timeline for binary/discrete state tracking.** For "is this container running?" or "is this probe passing?" over time, a state timeline is more informative than a time series. It renders solid colored bands for each state, making duration and transitions immediately visible. Map numeric values to state names via value mappings (0 → "Down"/red, 1 → "Up"/green).

11. **Canvas panels for infrastructure diagrams.** Canvas panels allow free-form placement of shapes, icons, and server elements bound to data queries. Buttons can trigger API calls (start/stop containers). Consider a canvas panel on D00 as an alternative to the stat grid for VM Availability — showing a visual topology map instead of plain stat boxes.

### Variable & URL Design

12. **`:queryparam` for multi-value drilldowns.** When a drilldown link passes a multi-value variable, `${host:queryparam}` produces `var-host=media&var-host=core` — correct URL syntax that Grafana interprets as multiple selected values. Plain `var-host=${host}` produces a regex-escaped string that only works in PromQL matchers, not as URL parameters.

13. **Repeating rows vs. repeating panels.** "Repeat by variable" on a **row** creates one collapsed row per variable value — good for per-VM sections on workbenches. "Repeat by variable" on a **panel** creates duplicate panels side-by-side — good for stat grids. Choose based on density: repeating panels for 3–5 items, repeating rows for more.

14. **Config from query results for data-driven thresholds.** Rather than hardcoding CPU warning thresholds at 70%/85% for every VM, a config query can return different thresholds per `vm_role` (media VMs run hotter than core). The `Config from query results` transformation applies these dynamically, including threshold colors.

15. **The `-- Dashboard --` data source.** A panel can query data from another panel on the same dashboard using the Dashboard data source. Combined with ad hoc filters, this enables filtering data from sources that don't natively support ad hoc filtering. Niche but useful when a visualization needs post-processed data from an existing panel.

### Pitfalls

16. **Transformations break when field names change.** `Organize fields` and `Join by field` reference fields by name. If a Prometheus label or query alias changes, the transformation silently produces empty results. Use `Filter by name` with regex patterns when possible — they're more resilient to minor naming changes.

17. **`NoData` propagates through expressions.** If any query in a server-side expression chain returns no data, the entire expression returns `NoData`. Design defensive queries with `or on() vector(0)` fallbacks for metrics that might legitimately be absent (e.g., `node_vmstat_oom_kill` returns nothing if no OOM has ever occurred).

18. **Pie charts hide magnitude.** A pie chart showing 95% info / 5% error looks alarming, but if total volume is 20 log lines, it's noise. Always pair proportional visualizations with an absolute count somewhere visible.

19. **Stacking order in time series matters.** Stacked area charts render series bottom-to-top. Put the most stable/expected series at the bottom (idle CPU, used memory) and the anomalous series at the top (steal, OOM) so visual deviations are at the top edge where they're easiest to spot.

20. **Sparkline limitations in stat panels.** Stat panel sparklines are trend indicators, not interactive graphs. They cannot be zoomed, hovered for values, or clicked for drilldowns. They answer "up or down?" — never "when exactly?" Design accordingly: if precision matters, use a time series panel.

---

## Dashboard-Specific Specs

| Dashboard | File | Status |
|-----------|------|--------|
| D00 — Homelab Overview | [dashboard-00-overview.md](dashboard-00-overview.md) | Built |
| D01a — Host Workbench | [dashboard-01a-host-workbench.md](dashboard-01a-host-workbench.md) | Built |
| D01b — Container Workbench | [dashboard-01b-container-workbench.md](dashboard-01b-container-workbench.md) | Built |
| D02 — Log Workbench | [dashboard-02-log-workbench.md](dashboard-02-log-workbench.md) | Planning |
| D03 — Network/Connectivity | [dashboard-03-network.md](dashboard-03-network.md) | Planning |
| D04 — Media Pipeline | [dashboard-04-media-pipeline.md](dashboard-04-media-pipeline.md) | Planning — requires exportarr, qbittorrent-exporter, Jellyfin metrics |
| D05 — Hardware/Host | [dashboard-05-hardware-host.md](dashboard-05-hardware-host.md) | Planning — requires node_exporter on pve1, SMART collector |
