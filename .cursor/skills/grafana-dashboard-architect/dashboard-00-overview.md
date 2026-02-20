# Dashboard 00 — Homelab Overview

Architecture spec for the fleet-level entry point dashboard.

---

## Purpose

Answers two questions: **"Is something wrong?"** and **"Where do I click?"**

This is a triage tool, not an investigation tool. A glance should tell you within 5 seconds whether anything needs attention, WHAT is responsible, and which workbench to open next — never WHY.

---

## Ownership

### What D00 Owns

- **Availability**: Is each VM up or down?
- **Error attribution**: Which VM and service is producing the most errors right now?
- **Change detection**: Are errors increasing? Did something just restart?
- **Saturation**: Is any VM approaching CPU, memory, or disk limits?
- **Freshness**: Is observability data actually flowing? (Staleness = silent failure)

### What D00 Does NOT Own

- Per-container resource breakdown (→ D01 Infra Workbench)
- Log text or log exploration (→ D02 Log Workbench)
- Service-specific metrics (queue depth, download rates, etc.)
- Historical trend analysis or capacity planning
- Warning-level log counts (investigation context, not triage signal)
- Alerting rules or thresholds (those live in Prometheus/Grafana alerting, not dashboard panels)

### Boundary Rule

If a signal on the overview makes you want to "zoom in" or "scroll through", it belongs on a workbench. The overview shows the **result** (a count, a status, a rate), never the **evidence** (a time series, a log line, a container list).

**Service attribution exception:** The overview shows service identity in the Top Offenders table — "sonarr on media has 43 errors" — but does NOT expose `$service` as a filter variable. The overview tells you WHO is noisy. Filtering to only that service is a workbench action.

---

## Operational Feel

- **NOC board aesthetic.** Sparse layout. Status indicators dominate.
- **Boring when healthy.** 95% of the time everything is green/nominal. The dashboard should feel empty and calm. The Top Offenders section is blank when nothing is wrong.
- **Loud when broken.** A single failing VM or error spike should be impossible to miss without scrolling.
- **Directional.** Every non-green signal points the operator to a specific next click. The operator never has to decide where to go — the dashboard tells them.
- **No scroll for critical signals.** The top viewport (above the fold) must contain all availability and error signals. Saturation and freshness can live below.
- **Phone-friendly.** Should be glanceable on a mobile Grafana session or a tablet mounted on a wall.

---

## Layout — Section by Section

Five sections, top to bottom. Each answers one question.

### Section 1 — Fleet Pulse (Row 0)

**Question:** "Do I need to pay attention right now?"

The 2-second glance row. Four compact stat panels, full width. Green = walk away.

| Panel | What It Shows | Sparkline | Threshold Logic |
|-------|--------------|-----------|-----------------|
| **Host Status** | "5/5 UP" — single merged panel; turns red if any host is down | No | Green at full count, red if any down |
| **Error Rate** | Fleet-wide errors/min (rate, not accumulated count) | Yes — shows direction | Green at 0, yellow at low rate, red at high rate |
| **Restarts** | Fleet-wide container restart count in window | Yes — shows if ongoing | Green at 0, yellow >=1, red >=5 |
| **Scrape Health** | Percentage of Prometheus targets currently UP | No | Red <90%, yellow <99%, green >=99% |

**Design decisions:**
- **Merged Host Status** replaces separate Hosts Up + Hosts Down panels. Two panels for a binary signal wastes status bar space.
- **Error Rate replaces Error Count.** Raw count over a range is meaningless without baseline. Rate shows magnitude; sparkline shows direction. "3/min and rising" is actionable. "47 errors" is not.
- **Sparklines are not time series graphs.** A stat panel sparkline is a trend indicator — it answers "up or down?" not "when exactly?" The operator never zooms into a sparkline. This is not an anti-pattern; it's directional context.
- **Warnings are absent.** Warning-level logs are not actionable at fleet level. A fleet with 500 warnings and zero errors is healthy. Warnings compete for attention with actual problems and win by volume. They belong on D02 Log Workbench.

**Links:** Host Status → scrolls to Section 2. Error Rate → D02 Log Workbench (fleet-wide, level=error). Restarts → D01 Infra Workbench. Scrape Health → scrolls to Section 5.

**Must NOT include:** Per-VM breakdown, service names, log text, any number that requires careful reading.

---

### Section 2 — VM Availability (Row 1)

**Question:** "Which VMs are alive?"

Per-VM stat panels showing UP or DOWN. Colored backgrounds. One panel per host, auto-populated by label query. No changes from current implementation — this section works.

**Links:** Each VM → D01 Infra Workbench with `$vm_role` and `$host` pre-set.

**Must NOT include:** Uptime duration, response times, or any metric beyond "is the exporter reachable."

---

### Section 3 — Top Offenders (Row 2)

**Question:** "Where should I click next?"

This is the primary routing surface — the reason the dashboard exists.

A single **table panel**, full width, ranked descending by error count.

| Column | Label Source | Purpose |
|--------|-------------|---------|
| **Host** | `host` | VM identification |
| **VM Role** | `vm_role` | Stack context |
| **Service** | `service` | Attribution — who is noisy |
| **Errors** | Loki count, `level=~"error\|fatal"` | Severity ranking |
| **Restarts** | cAdvisor restart counter | Instability signal alongside errors |

**Design decisions:**
- **Sorted descending by error count.** The noisiest offender is always the first row.
- **Zero-error entries are hidden.** When healthy, this section shows an empty-state message. Boring when healthy, loud when broken.
- **Service-level attribution as output, not input.** The table query groups `by (host, vm_role, service)`. The overview tells you "sonarr is the problem" but does NOT expose `$service` as a filter variable. Filtering to only sonarr is a workbench action. This distinction preserves the overview's VM-level filter contract while giving the operator the routing precision they need.
- **Restarts as a column, not a separate section.** Restarts next to error counts give the operator a two-signal view: "is it noisy?" (errors) and "is it unstable?" (restarts). A service with errors AND restarts is more urgent than one with only errors.

**Links:** Each row → D02 Log Workbench with `$host`, `$service` (via `${__data.fields.service}`), and `level=error` pre-set. This is the most specific drilldown on the overview — one click to the error stream for that exact service.

**Must NOT include:** Log lines, error messages, per-container breakdown, stack traces, trend graphs.

**Replaces:** The previous "Error Rates by VM" stat panel and the bottom "Container Restarts by VM" stat panel. Both are absorbed into this ranked table with better attribution and routing.

**Cross-datasource join:** The table uses two queries (Loki for errors, Prometheus/cAdvisor for restarts) joined on the `service` label via a `joinByField` transformation. This join is possible because `bootstrap.py` adds `service` to cAdvisor metrics via `metric_relabel_configs`, mirroring the same priority chain Alloy uses for logs.

---

### Section 4 — Resource Saturation (Row 3)

**Question:** "Is anything running out of headroom?"

Three gauge panels: CPU, Memory, Disk. Per-VM, with thresholds at 70% (yellow) and 85% (red).

**Refinements from previous design:**
- **Sorted by utilization descending.** The VM closest to its limit appears first. Previously sorted alphabetically by host — fine when everything is green, unhelpful when something is saturated.
- **Bar gauge preferred over radial gauge at scale.** Radial gauges are clean with 3-4 VMs but become cluttered at 6+. Bar gauges are more scannable. This is a visual judgment call, not a structural rule.

**Links:** Each gauge → D01 Infra Workbench with `$vm_role` and `$host`.

**Must NOT include:** Per-mount filesystem breakdown, per-core CPU, swap details, network I/O, per-container resource usage.

---

### Section 5 — Data Freshness (Row 4)

**Question:** "Can I trust what I'm seeing?"

A dashboard that silently stops receiving data is worse than one that shows red. This section catches "the observability pipeline itself is broken."

| Panel | What It Shows | Source |
|-------|--------------|--------|
| **Scrape Staleness** | Seconds since last successful Prometheus scrape, per VM | `timestamp(up{...})` |
| **Log Freshness** | Seconds since last log line received, per VM | Loki (last log timestamp per host) |

**Design decisions:**
- **Log Freshness is new.** Catches the failure mode where Prometheus scrapes fine but Alloy stops shipping logs. Without this, the error panels above silently show stale zeros — false confidence.
- **Stale/unknown is gray or purple, never green.** Missing data is a signal, not silence.
- **Container Restarts removed from this section.** Restart counts are not a data freshness signal. Fleet-wide restarts are in Section 1 (Fleet Pulse). Per-VM/service restart attribution is in Section 3 (Top Offenders). The previous placement created duplication.

**Links:** Stale host → D01 Infra Workbench (scrape target investigation).

**Must NOT include:** Prometheus internal metrics, Alloy pipeline details, log volume graphs.

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
| `$service` | **not exposed** | — | — | Too granular for fleet overview. Shown as output in Top Offenders. Passed via drilldown only. |

### Why No $service Variable

The overview groups and filters by VM (`$host` / `$vm_role`). Service identity appears as output in the Top Offenders table — the dashboard tells you which service is the problem. But filtering to only that service is a workbench action. This keeps the overview's variable cascade simple and avoids turning triage into investigation.

---

## Drilldown Design

### Drilldown Targets

| From (signal on D00) | To (target dashboard) | Context Passed |
|----------------------|----------------------|----------------|
| Fleet Pulse: Error Rate | D02 Log Workbench | `time`, `$vm_role`, `$host`, `level=error` |
| Fleet Pulse: Restarts | D01 Infra Workbench | `time`, `$vm_role`, `$host` |
| VM Availability: per-VM | D01 Infra Workbench | `time`, `$vm_role`, `$host` |
| Top Offenders: table row | D02 Log Workbench | `time`, `$vm_role`, `$host`, `$service`, `level=error` |
| Resource Saturation: gauge | D01 Infra Workbench | `time`, `$vm_role`, `$host` |
| Data Freshness: stale host | D01 Infra Workbench | `time`, `$vm_role`, `$host` |

### Primary Routing Surface

The Top Offenders table is the most important drilldown on the dashboard. It's the only panel that passes `$service` — every other drilldown routes to a workbench at VM granularity and lets the operator narrow from there.

### Drilldown Link Format

```
/d/<uid>?from=${__from}&to=${__to}&var-node=${node}&var-vm_role=${vm_role}&var-host=${__data.fields.host}
```

For Top Offenders table rows (includes service):
```
/d/<d02-uid>?from=${__from}&to=${__to}&var-vm_role=${__data.fields.vm_role}&var-host=${__data.fields.host}&var-service=${__data.fields.service}&var-level=error
```

Key rules:
- Time range (`from`, `to`) is ALWAYS included — preserves the incident window
- Use `${__data.fields.*}` to pass the specific row's values when clicking a table or stat
- Use `${vm_role}` (template variable value) for filter-wide drilldowns

### Incident Window Flow

1. Operator glances at Fleet Pulse — Error Rate is yellow, sparkline trending up
2. Operator looks at Top Offenders — "sonarr on media: 43 errors, 2 restarts"
3. Operator clicks the sonarr row → D02 Log Workbench opens
4. D02 shows sonarr's error stream at the same time window
5. Operator sees the actual log lines and begins investigation

Three steps from "something's wrong" to "here are the logs." The time range never resets during a drilldown chain.

---

## Avoiding Duplication with Workbenches

### The Aggregation Rule

D00 shows **aggregated summaries**. Workbenches show **the underlying data**.

| D00 shows | Workbench shows |
|-----------|-----------------|
| Error rate: fleet-wide errors/min with sparkline | Error logs: filterable stream with timestamps, service, message |
| Top Offenders: ranked host×service with error count | Log Workbench: full error stream filtered to that service |
| CPU usage: single gauge per VM (current) | CPU usage: time series per core, load averages, per-container breakdown |
| Disk: percentage used per VM | Disk: per-mount breakdown, I/O rates, inode usage |
| Restarts: count per host×service in table | Restarts: timeline showing which containers restarted and when |

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
   - VM Availability gains a new UP/DOWN panel
   - Top Offenders table includes the new VM if it has errors (auto via label query)
   - Saturation gauges gain a new entry
   - Data Freshness shows the new VM's staleness
4. Zero manual dashboard changes

### When a VM is Decommissioned

1. Remove scrape targets from Prometheus
2. VM stops appearing in `label_values()` after retention window expires
3. Dashboard self-heals — no orphan panels

### Repeating Panels

Where the overview needs per-VM sections (e.g., availability, saturation), use Grafana's **repeat by variable** feature with `$host` or `$vm_role`. This ensures:
- New VMs generate new visual sections automatically
- Decommissioned VMs disappear after their data ages out
- No manual panel duplication ever

The Top Offenders table scales by label query, not by repeating — new VMs appear as rows automatically.

---

## Color Contract

| State | Color | Meaning |
|-------|-------|---------|
| Healthy | Green | Nominal, no action needed |
| Warning | Yellow/Amber | Approaching threshold, investigate soon |
| Critical | Red | Requires immediate attention |
| Unknown/Stale | Gray/Purple | No data — possibly worse than red |

Stale/unknown is deliberately NOT green. Missing data is a signal, not silence.

---

## Anti-Patterns to Avoid

1. **Time series graphs on the overview.** Stat panel sparklines (trend indicators) are acceptable. Full time series panels are not. If you need to read data points from a graph, it belongs on a workbench.
2. **Hardcoded VM names in queries.** Use `{vm_role=~"$vm_role"}` patterns.
3. **Per-container panels.** The overview operates at VM granularity (with service attribution in the offenders table only).
4. **Log panels or log search.** Logs live on D02.
5. **Warning-level stats on the top row.** Warnings are investigation context, not triage signals. They belong on D02.
6. **Duplicate signals across sections.** Each signal appears in exactly one section. Restarts are in Fleet Pulse (fleet aggregate) and Top Offenders (attributed). They are NOT also in a standalone bottom panel.
7. **Alert rule configuration in dashboard panels.** Alert rules belong in Grafana Alerting or Prometheus recording rules, not embedded in overview panels.
8. **More than one page of content.** If you need to scroll past the saturation section, you've put too much on the overview.
