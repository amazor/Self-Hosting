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

- Per-container resource breakdown (→ D01b Container Workbench)
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

Five sections, top to bottom. Each answers one question. Top Offenders is at the bottom — its empty state when healthy should not interrupt triage panels above.

### Section 1 — Fleet Pulse (Row 0)

**Question:** "Do I need to pay attention right now?"

The 2-second glance row. Three compact stat panels, full width. Green = walk away.

| Panel | What It Shows | Sparkline | Threshold Logic |
|-------|--------------|-----------|-----------------|
| **Host Status** | "All Hosts UP" or count of down VMs | No | Green at 0 down, red if any down |
| **Error Rate** | Fleet-wide errors/min (rate, not accumulated count) | Yes — shows direction | Green at 0, yellow at low rate, red at high rate |
| **Containers** | Container deficit: max_over_time(count, 24h) minus current_count. Shows "All Up" at 0. | Yes — restart events appear as brief spikes | Green at 0, red at >=1 |
| **OOM Kills** | Fleet-wide OOM kill events in the current time range. Shows "0" when healthy. | No | Green at 0, red at any positive value |

**Design decisions:**
- **Merged Host Status** replaces separate Hosts Up + Hosts Down panels. Two panels for a binary signal wastes status bar space.
- **Error Rate replaces Error Count.** Raw count over a range is meaningless without baseline. Rate shows magnitude; sparkline shows direction. "3/min and rising" is actionable. "47 errors" is not.
- **Containers shows the deficit, not the raw count.** `max_over_time(count(container_start_time_seconds)[24h:5m])` captures the baseline (highest count seen in 24h, sampled at 5m granularity). Subtracting the current count gives the number of missing containers. 0 = all up (green), >=1 = something down (red). The sparkline normal state is a flat line at 0; restart events appear as brief spikes that return to 0 once the container recovers. This replaces both the old raw count panel and the old Restarts panel.
- **Restarts panel removed.** The deficit approach in Containers captures restart events as transient spikes in the sparkline. The old Restarts panel used `container_start_time_seconds > (time() - 300)` — a 5-minute window that was frequently already expired by the time an operator looked, always showing 0 even after a confirmed restart.
- **Scrape Health removed from fleet pulse.** `avg(up) * 100` shows 100% when Prometheus can reach node_exporter and cAdvisor. In practice this drops only if an exporter process crashes, which is the same condition causing VM Availability to show DOWN. It is redundant with VM Availability and cannot detect container log issues. Scrape Staleness in Section 5 handles the subtler failure mode (scrape delays without full target loss).
- **OOM Kills is a change detection signal.** `increase(node_vmstat_oom_kill[$__range])` summed across all VMs. An OOM kill means the kernel terminated a process because memory was completely exhausted — the app loses all in-memory state and restarts with no error log explaining why. Without this stat, the operator sees "Sonarr restarted and has 12 errors" in Top Offenders, investigates logs (which show a clean startup, not the cause), and misses the real problem: the VM ran out of memory. The OOM stat turns "mysterious restart" into "OOM-killed — check memory on that VM." In self-hosted Docker environments, memory leaks in arr applications, Jellyfin, and qBittorrent accumulate over days/weeks and eventually trigger OOM kills.
- **Sparklines are not time series graphs.** A stat panel sparkline is a trend indicator — it answers "up or down?" not "when exactly?" The operator never zooms into a sparkline. This is not an anti-pattern; it's directional context.
- **Warnings are absent.** Warning-level logs are not actionable at fleet level. A fleet with 500 warnings and zero errors is healthy. Warnings compete for attention with actual problems and win by volume. They belong on D02 Log Workbench.

**Links:** Host Status → scrolls to Section 2. Error Rate → D02 Log Workbench (fleet-wide, level=error). Containers → D01b Container Workbench. OOM Kills → D01a Host Workbench (memory section). Connectivity → D03 Network/Connectivity.

**Planned panels — require Blackbox Exporter:**

| Panel | What It Shows | Sparkline | Threshold Logic |
||-------|--------------|-----------|-----------------|
|| **Connectivity** | Single binary: are all critical probes passing? Fails red if any probe is down. | No | Green = all pass, red = any fail |
|| **TLS Cert Expiry** | Fewest days remaining across all probed domains. | No | Green >14d, yellow 7–14d, red <7d |

The **TLS Cert Expiry** stat shows `min((probe_ssl_earliest_cert_expiry - time()) / 86400)` — the minimum days-to-expiry across all TLS-probed domains. This catches silent Let's Encrypt renewal failures: the cert works fine today, but renewal stopped 3 weeks ago and there are 5 days left. The HTTP probe still passes green because the cert is currently valid. Without this stat, the operator discovers the failure when everything behind HTTPS breaks simultaneously.

**Links:** TLS Cert Expiry → D03 Network/Connectivity (TLS section for per-domain breakdown).

The Connectivity panel combines independent probes into one fleet-level signal:

1. **HTTP probe → your public domain** — validates the entire public chain end-to-end (DNS resolves, TCP connects on 443, TLS handshakes, reverse proxy returns 200). If any layer in the public stack breaks, this fails.
2. **DNS probe → internal resolver** — completely independent from the HTTP probe. A dead internal resolver leaves all VMs and containers appearing healthy while inter-service communication silently fails. Cannot be inferred from the HTTP probe.
3. **TCP probe → NAS port 2049 (conditional — only when NFS is in use)** — the NAS going offline is a fleet-level event: multiple VMs lose their mounts simultaneously. Containers keep running but file operations silently fail or hang. Port 2049 is the NFS service port; a passing TCP probe means the NAS is online and accepting NFS connections. Not a substitute for checking whether a specific mount is healthy (that is D03/D01a detail), but sufficient as a D00 binary signal.

**Why not a separate public DNS probe:** The HTTP probe already requires DNS to resolve before TCP connects, so a passing HTTP probe implies public DNS is working. A separate public DNS probe only tells you *which layer* broke — investigation detail for D03, not triage signal for D00.

**Why not per-service probes on D00:** Per-service HTTP probes (one per app behind the proxy) are D03 territory. D00 asks “is the public stack reachable?”; D03 asks “which specific service is broken?”

**When NFS is not in use:** Omit probe 3. Do not add the NAS probe unconditionally — a probe target that doesn’t exist creates misleading failures.

**Must NOT include:** Per-VM breakdown, service names, log text, any number that requires careful reading.

---

### Section 2 — VM Availability (Row 1)

**Question:** "Which VMs are alive?"

Per-VM stat panels showing UP or DOWN. Colored backgrounds. One panel per host, auto-populated by label query. No changes from current implementation — this section works.

**Links:** Each VM → D01a Host Workbench with `$vm_role` and `$host` pre-set.

**Must NOT include:** Uptime duration, response times, or any metric beyond "is the exporter reachable."

---

### Section 3 — Top Offenders (Row 5, bottom)

**Question:** "Where should I click next?"

This is the primary routing surface — the reason the dashboard exists. It lives at the **bottom** of the dashboard so that its empty state (blank when healthy) does not interrupt the resource saturation and freshness rows above it.

A single **table panel**, full width, ranked descending by error count.

| Column | Label Source | Purpose |
|--------|-------------|---------|
| **Node** | `node` | Proxmox host (e.g. `pve1`) — physical machine context |
| **Host** | `host` | VM hostname — the thing you SSH into and the identity used in drilldowns |
| **Service** | `service` | Attribution — who is noisy |
| **Errors** | Loki count, `level=~"error\|fatal"` | Severity ranking |
| **Restarts** | cAdvisor restart counter | Instability signal alongside errors |

**Design decisions:**
- **Sorted descending by error count.** The noisiest offender is always the first row.
- **Zero-error entries are hidden.** When healthy, this section shows an empty-state message. Boring when healthy, loud when broken.
- **Service-level attribution as output, not input.** The table query groups `by (node, host, service)`. The overview tells you "sonarr is the problem" but does NOT expose `$service` as a filter variable. Filtering to only sonarr is a workbench action. This distinction preserves the overview's VM-level filter contract while giving the operator the routing precision they need.
- **Restarts as a column, not a separate section.** Restarts next to error counts give the operator a two-signal view: "is it noisy?" (errors) and "is it unstable?" (restarts). A service with errors AND restarts is more urgent than one with only errors.
- **Restarts use the recency method, not `changes()`.** Consistent with the fleet Restarts panel: `container_start_time_seconds > (time() - $__range_s)` detects containers that started within the dashboard time window. `changes()` is unreliable for the same reason documented in Section 1 — cAdvisor drops the series on container stop, and a recreated series with one data point returns `changes() == 0`. The recency method works regardless of series continuity. Trade-off: it shows 1 per restarted service (boolean "did it restart?") rather than a restart count. For triage, this is sufficient — the workbench owns restart timelines and counts.

**Links:** Each row → D02 Log Workbench with `$host`, `$service` (via `${__data.fields.service}`), and `level=error` pre-set. This is the most specific drilldown on the overview — one click to the error stream for that exact service.

**Must NOT include:** Log lines, error messages, per-container breakdown, stack traces, trend graphs.

**Replaces:** The previous "Error Rates by VM" stat panel and the bottom "Container Restarts by VM" stat panel. Both are absorbed into this ranked table with better attribution and routing.

**Cross-datasource join:** The table uses two queries (Loki for errors, Prometheus/cAdvisor for restarts) combined via a `merge` transformation. The `merge` matches rows by ALL shared label fields (`node`, `host`, `service`), not a single field — this correctly pairs error counts with restart counts even when the same service name exists on multiple hosts (e.g., `alloy` as a sidecar on every VM). A previous `joinByField` on `service` alone was ambiguous for multi-host services. The join is possible because `bootstrap.py` adds `service` to cAdvisor metrics via `metric_relabel_configs`, mirroring the same priority chain Alloy uses for logs.

---

### Section 4 — Resource Saturation (Row 3)

**Question:** "Is anything running out of headroom?"

Three bar gauge panels (**CPU**, **Memory**, **Disk**) plus a **Containers Running** table — all per-VM, full width.

| Panel | Type | Query basis | Thresholds |
|-------|------|-------------|------------|
| CPU | bar gauge | `1 - avg(rate(node_cpu_seconds_total{mode="idle"}[2m]))` | 70% yellow, 85% red |
| Memory | bar gauge | `1 - (max(MemAvailable) / max(MemTotal))` | 70% yellow, 85% red |
| Disk | bar gauge | `max(1 - (avail / size))` on `mountpoint="/"` plus optional NFS mounts (see design decisions) | 70% yellow, 85% red |
| Containers Running | table | `count(container_start_time_seconds)` (Up) vs `max_over_time(count(...)[24h:5m])` (Exp) | row color-background: entire row turns red/yellow/green based on Up/Exp ratio. <0.8 red, <1 yellow, =1 green |

**Design decisions:**
- **CPU uses `[2m]` rate window** (not `[5m]`). The 5-minute window made CPU feel slow to respond — a brief spike would stay elevated for 5 minutes after clearing. 2 minutes balances responsiveness with noise suppression.
- **Memory and Disk use `max by (host, vm_role)`** to deduplicate. Without this, node_exporter can return multiple series per host (different scrape instances or device labels), producing phantom duplicate bars in the gauge.
- **Containers Running is a compact table, not a bar gauge.** Each row shows: Host | Up | Exp | Health%. "Up" is the current running count; "Exp" is derived from `max_over_time` of the running count over 24 hours — automatically reflects the compose-defined count without hardcoding. The Health column (Up/Exp ratio, shown as %) drives `applyToRow: true` color-background: the **entire row** turns red/yellow/green based on whether all containers are up. This eliminates horizontal scrolling and makes unhealthy rows immediately obvious without reading the numbers. Column widths are tightened (Up: 35px, Exp: 40px, Health: 55px) so the table fits within the 6-column panel without a horizontal scrollbar. `clamp_min(..., 1)` in the denominator prevents division by zero on fresh deployments. Still uses `container_start_time_seconds` (not `container_tasks_state`) because cAdvisor only exports this metric for running containers.

- **NAS storage in the Disk panel (conditional — only when NFS mounts are in use).** The current Disk query filters to `mountpoint="/"` (VM root filesystems, typically 30–50 GB each). This makes the NAS — where all actual data lives: downloads, media, backups — completely invisible on D00. If a VM has an NFS mount, node_exporter on that VM automatically exports `node_filesystem_avail_bytes{fstype=~"nfs|nfs4"}` for the mounted path. A second query targeting `fstype=~"nfs|nfs4"` adds the NAS as an additional bar in the Disk bargauge, labeled by mountpoint or a human-readable alias (e.g., "NAS"). No new tooling required. NAS storage running out is often more impactful than VM root disk running out — a full NAS silently stops downloads and recordings while all containers remain healthy. Same thresholds: 70% yellow, 85% red.

**Links:** CPU, Memory, Disk gauges → D01a Host Workbench with `$vm_role` and `$host`. Containers Running table rows → D01b Container Workbench with `$host`.

**Must NOT include:** Per-mount filesystem breakdown, per-core CPU, swap details, network I/O, per-container resource breakdown.

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

**Links:** Stale host → D01a Host Workbench (scrape target investigation).

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
| Fleet Pulse: Containers (deficit) | D01b Container Workbench | `time`, `$vm_role`, `$host` |
| Fleet Pulse: OOM Kills | D01a Host Workbench | `time` (memory section) |
| VM Availability: per-VM | D01a Host Workbench | `time`, `$vm_role`, `$host` |
| Top Offenders: table row | D02 Log Workbench | `time`, `$vm_role`, `$host`, `$service`, `level=error` |
| Resource Saturation: CPU gauge | D01a Host Workbench | `time`, `$vm_role`, `$host` |
| Resource Saturation: Memory gauge | D01a Host Workbench | `time`, `$vm_role`, `$host` |
| Resource Saturation: Disk gauge | D01a Host Workbench | `time`, `$vm_role`, `$host` |
| Resource Saturation: Containers Running row | D01b Container Workbench | `time`, `$host` |
| Data Freshness: stale scrape | D01a Host Workbench | `time`, `$vm_role`, `$host` |
| Data Freshness: stale logs | D01a Host Workbench | `time`, `$vm_role`, `$host` |

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

## Click Flow Map

Every clickable element on D00, its action, and where it goes. D00 is always depth 1 — every click routes outward to a workbench.

| Panel / Element | Click Action | Target | Context Passed |
|----------------|-------------|--------|----------------|
| Fleet Pulse → Host Status | Scroll | Section 2 (VM Availability) | — |
| Fleet Pulse → Error Rate | Cross-dashboard | D02 Log Workbench | `time`, `$vm_role`, `$host`, `level=error` |
| Fleet Pulse → Containers | Cross-dashboard | D01b Container Workbench | `time`, `$vm_role`, `$host` |
| Fleet Pulse → OOM Kills | Cross-dashboard | D01a Host Workbench | `time` (memory section) |
| Fleet Pulse → Connectivity | Cross-dashboard | D03 Network/Connectivity | `time` |
| Fleet Pulse → TLS Cert Expiry | Cross-dashboard | D03 Network/Connectivity | `time` (TLS section) |
| VM Availability → VM panel | Cross-dashboard | D01a Host Workbench | `time`, `$vm_role`, `$host` |
| Top Offenders → table row | Cross-dashboard | D02 Log Workbench | `time`, `$host`, `$service`, `level=error` |
| Saturation → CPU bar | Cross-dashboard | D01a Host Workbench | `time`, `$vm_role`, `$host` |
| Saturation → Memory bar | Cross-dashboard | D01a Host Workbench | `time`, `$vm_role`, `$host` |
| Saturation → Disk bar | Cross-dashboard | D01a Host Workbench | `time`, `$vm_role`, `$host` |
| Saturation → Containers Running row | Cross-dashboard | D01b Container Workbench | `time`, `$host` |
| Freshness → Stale scrape host | Cross-dashboard | D01a Host Workbench | `time`, `$vm_role`, `$host` |
| Freshness → Stale log host | Cross-dashboard | D01a Host Workbench | `time`, `$vm_role`, `$host` |

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
