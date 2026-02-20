# Dashboard 00 — Homelab Overview

Architecture spec for the fleet-level entry point dashboard.

---

## Purpose

Answers one question: **"Is my homelab healthy right now?"**

This is a triage tool, not an investigation tool. A glance should tell you within 5 seconds whether anything needs attention and WHERE the problem is — never WHY.

---

## Ownership

### What D00 Owns

- **Availability**: Is each VM and critical service up or down?
- **Error rate**: Are error/fatal logs spiking on any VM or service?
- **Saturation**: Is any VM approaching CPU, memory, or disk limits?
- **Freshness**: Is observability data actually flowing? (Staleness = silent failure)

### What D00 Does NOT Own

- Per-container resource breakdown (→ D01 Infra Workbench)
- Log text or log exploration (→ D02 Log Workbench)
- Service-specific metrics (queue depth, download rates, etc.)
- Historical trend analysis or capacity planning
- Alerting rules or thresholds (those live in Prometheus/Grafana alerting, not dashboard panels)

### Boundary Rule

If a signal on the overview makes you want to "zoom in" or "scroll through", it belongs on a workbench. The overview shows the **result** (a count, a status, a rate), never the **evidence** (a time series, a log line, a container list).

---

## Operational Feel

- **NOC board aesthetic.** Sparse layout. Status indicators dominate. Time series are minimal or absent.
- **Boring when healthy.** 95% of the time everything is green/nominal. The dashboard should feel empty and calm.
- **Loud when broken.** A single failing VM or error spike should be impossible to miss without scrolling.
- **No scroll for critical signals.** The top viewport (above the fold) must contain all availability and error signals. Saturation and secondary details can live below.
- **Phone-friendly.** Should be glanceable on a mobile Grafana session or a tablet mounted on a wall.

---

## Signal Types (Conceptual Only)

These are the CATEGORIES of information on the overview, not panel designs.

### Tier 1 — Above the Fold (Must See Immediately)

| Signal Category | What It Communicates | Source |
|----------------|----------------------|--------|
| **VM availability** | Is each VM's node_exporter responding? UP/DOWN per host. | Prometheus (`up` metric) |
| **Service availability** | Are critical services running? Container state summary. | Prometheus (cAdvisor) or Loki (log recency) |
| **Error rate** | Aggregate error+fatal log count per VM, recent window. | Loki (count by `host`, `level=~"error\|fatal"`) |

### Tier 2 — Below the Fold (Important, Not Urgent)

| Signal Category | What It Communicates | Source |
|----------------|----------------------|--------|
| **Resource saturation** | CPU / memory / disk usage per VM — worst-case or current. | Prometheus (node_exporter) |
| **Data freshness** | Is Alloy shipping logs? Is Prometheus scraping? Staleness indicators. | Prometheus (scrape staleness), Loki (last log timestamp per host) |
| **Container restarts** | Any containers restarting unexpectedly? Recent restart count. | Prometheus (cAdvisor restart counter) |

### What Is Explicitly Absent

- Individual container CPU/memory (too granular → D01)
- Log lines or log search (wrong tool → D02)
- Service-specific health (e.g., Plex transcoding, Sonarr queue → D03+ if needed)
- Network throughput per interface (too detailed → D01)

---

## Variables Exposed

D00 uses a subset of the global variable contract:

| Variable | Visible | Multi | Default | Notes |
|----------|---------|-------|---------|-------|
| `$datasource_prometheus` | yes | no | Prometheus | |
| `$datasource_loki` | yes | no | Loki | |
| `$node` | yes | yes | All | Proxmox node (future multi-node) |
| `$vm_role` | yes | yes | All | Primary axis. Selecting a role filters everything below. |
| `$host` | yes | yes | All | Cascades from `$vm_role`. |
| `$env` | hidden | no | `prod` | Hidden; future-proofing. |
| `$service` | **not exposed** | — | — | Too granular for fleet overview. Passed via drilldown only. |

### Why No $service on the Overview

The overview groups by VM (`$host` / `$vm_role`). If you need per-service filtering, you've already left triage territory — drilldown to a workbench.

---

## Drilldown Design

### Drilldown Targets

| From (signal on D00) | To (target dashboard) | Context Passed |
|----------------------|----------------------|----------------|
| VM availability indicator | D01 Infra Workbench | `time`, `$vm_role`, `$host` |
| Resource saturation summary | D01 Infra Workbench | `time`, `$vm_role`, `$host` |
| Error rate count | D02 Log Workbench | `time`, `$vm_role`, `$host`, `level=error` |
| Container restart count | D01 Infra Workbench | `time`, `$vm_role`, `$host` |
| Data freshness alert | D01 Infra Workbench | `time`, `$vm_role`, `$host` |

### Drilldown Link Format

```
/d/<d01-uid>?from=${__from}&to=${__to}&var-node=${node}&var-vm_role=${vm_role}&var-host=${__data.fields.host}
```

Key rules:
- Time range (`from`, `to`) is ALWAYS included — this preserves the incident window
- Use `${__data.fields.*}` to pass the specific row's host/role when clicking a table or stat
- Use `${vm_role}` (template variable value) for filter-wide drilldowns

### Incident Window Flow

1. User notices an error spike on D00 in the last 15 minutes
2. User narrows Grafana time range to that 15-minute window
3. User clicks the error count → opens D02 Log Workbench
4. D02 opens at the same 15-minute window with `$host` and `level=error` pre-selected
5. User sees the actual error log lines in context

The time range never resets during a drilldown chain. Each dashboard inherits it.

---

## Avoiding Duplication with Workbenches

### The Aggregation Rule

D00 shows **aggregated summaries**. Workbenches show **the underlying data**.

| D00 shows | Workbench shows |
|-----------|-----------------|
| CPU usage: single gauge per VM (current or worst) | CPU usage: time series per core, load averages, per-container breakdown |
| Error count: single number per VM | Error logs: filterable stream with timestamps, service, message |
| Disk: percentage used per VM | Disk: per-mount breakdown, I/O rates, inode usage |
| Container restarts: count badge | Container restarts: timeline, which containers, when |

### The Linkage Rule

Every number on D00 that invites deeper investigation MUST be a clickable link to the workbench that owns that investigation. If a signal doesn't link anywhere, either:
- It's self-contained (UP/DOWN status needs no drill) — acceptable
- Or it's missing its drilldown link — fix it

---

## Scaling Behavior

### When a New VM Appears (e.g., Security VM)

1. Alloy sidecar on the new VM ships logs with `vm_role=security`, `host=security`
2. node_exporter/cAdvisor on the new VM are added to Prometheus scrape targets
3. On D00:
   - `$vm_role` dropdown gains `security` automatically (`label_values()`)
   - VM availability section gains a new entry (query returns one more `host`)
   - Error rate section gains a new row
   - Resource saturation section gains a new row
4. Zero manual dashboard changes

### When a VM is Decommissioned

1. Remove scrape targets from Prometheus
2. VM stops appearing in `label_values()` after retention window expires
3. Dashboard self-heals — no orphan panels

### Repeating Panels

Where the overview needs per-VM sections (e.g., one row per VM), use Grafana's **repeat by variable** feature with `$host` or `$vm_role`. This ensures:
- New VMs generate new visual sections automatically
- Decommissioned VMs disappear after their data ages out
- No manual panel duplication ever

---

## Layout Philosophy

### Visual Hierarchy (Top to Bottom)

1. **Status bar** — All-green / has-issues summary (single row, full width)
2. **Availability** — UP/DOWN per VM (compact stat panels or status map)
3. **Error rates** — Error/fatal counts per VM, recent window (stat panels with thresholds)
4. **Saturation** — CPU/memory/disk per VM (gauges or single-stat with thresholds)
5. **Freshness / anomalies** — Staleness indicators, restart counts (bottom section)

### Color Contract

| State | Color | Meaning |
|-------|-------|---------|
| Healthy | Green | Nominal, no action needed |
| Warning | Yellow/Amber | Approaching threshold, investigate soon |
| Critical | Red | Requires immediate attention |
| Unknown/Stale | Gray/Purple | No data — possibly worse than red |

Stale/unknown is deliberately NOT green. Missing data is a signal, not silence.

---

## Anti-Patterns to Avoid

1. **Time series graphs on the overview.** If you need a graph, it belongs on a workbench.
2. **Hardcoded VM names in queries.** Use `{vm_role=~"$vm_role"}` patterns.
3. **Per-container panels.** The overview operates at VM granularity.
4. **Log panels or log search.** Logs live on D02.
5. **Alert rule configuration in dashboard panels.** Alert rules belong in Grafana Alerting or Prometheus recording rules, not embedded in overview panels.
6. **More than one page of content.** If you need to scroll past the saturation section, you've put too much on the overview.
