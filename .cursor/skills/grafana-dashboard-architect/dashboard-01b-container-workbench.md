# Dashboard 01b — Container Workbench

Planning reference for the container-level resource investigation dashboard.

> **Status:** Built. Dashboard file: `docker_compose/monitoring/dashboards/d01b-container-workbench.json` (UID: `homelab-container-workbench`).

---

## Purpose

Answers the question: **"Which container is responsible and what's it doing?"**

This is the primary investigation surface for container resource consumption and lifecycle issues. When D00 shows a container deficit or restarts, or when D01a's bridge table identifies a hot container, this dashboard provides the full picture: per-container CPU, memory with limit context, network I/O, restart timeline, and focused time series.

The `$service` variable is the star of this dashboard — it controls what you see. When set to "All", the resource table ranks all containers. When narrowed to a specific service, the per-container detail section renders focused time series.

---

## Ownership

### What D01b Owns

- Container resource consumption — CPU, memory, Mem Limit %, and network per container
- Container lifecycle — which containers are running, which have restarted, and when
- Per-container detail — focused CPU, memory, and network time series for a selected service
- Restart timeline — table of containers started within the time range, with relative timestamps

### What D01b Does NOT Own

- Host CPU modes, memory breakdown, swap, OOM kills, disk I/O (→ D01a Host Workbench)
- Load average, NTP offset, inode usage (→ D01a Host Workbench)
- Log text or error investigation (→ D02 Log Workbench)
- Probe results, DNS, TLS, or throughput investigation (→ D03 Network)
- Application-level signals like download queue depth (→ D04 Media)

---

## Main Insights This Dashboard Gives You

1. **Which container is responsible?** D00 tells you a VM has a container deficit. D01a tells you the VM's CPU is high. This dashboard ranks containers by CPU and memory so you can attribute the problem to a specific service.
2. **Is it about to be OOM-killed?** Mem Limit % tells you how close a container is to its Docker memory limit. Arr applications, Jellyfin, and qBittorrent all have known memory leak patterns that accumulate over days/weeks. "Sonarr: 1.7 GB" is meaningless without knowing the limit is 2 GB. "Sonarr: 85%" tells you it's about to be killed.
3. **What's the restart pattern?** The restart timeline shows exactly which containers restarted, when, and how frequently — not just a count. A container restarting every 15 minutes is a crash loop. One restart 3 hours ago after a compose update is normal.
4. **What does this container's resource profile look like over time?** The per-container detail section shows CPU, memory, and network time series for a single service, revealing trends like slow memory leaks or periodic CPU spikes that a table snapshot misses.

---

## Signal Sources

All signals below are available today from the existing monitoring stack (cAdvisor scraped by Prometheus). No new exporters required.

| Signal | Source metric | Labels |
|--------|--------------|--------|
| Container CPU | `container_cpu_usage_seconds_total` | `host`, `service`, `container`, `image` |
| Container memory | `container_memory_working_set_bytes` | `host`, `service`, `container` |
| Container memory limit | `container_spec_memory_limit_bytes` | `host`, `service`, `container` |
| Container network | `container_network_receive_bytes_total`, `container_network_transmit_bytes_total` | `host`, `service`, `container` |
| Container start time | `container_start_time_seconds` | `host`, `service`, `container` |

---

## Query Convention

All queries include the full variable cascade (`node`, `vm_role`, `host`, `service`) and cAdvisor guards (`name!=""`, `image!=""`) so that variable selections are always respected and housekeeping pseudo-containers are excluded. The shorthand `$cfilter` in this document refers to `node=~"$node", vm_role=~"$vm_role", host=~"$host", service=~"$service", name!="", image!=""`.

---

## Layout — Section Ideas

### Section 1 — Container Resource Table

**Panel:** Table of all containers on the selected host(s), ranked by CPU usage. This is the primary interactive surface. Five instant queries joined via `merge` transformation.

| Column | Query |
|--------|-------|
| **Host** | `host` label (from any query) |
| **Service** | `service` label (from any query) |
| **CPU %** | `sum by (host, service) (rate(container_cpu_usage_seconds_total{node=~"$node", vm_role=~"$vm_role", host=~"$host", service=~"$service", name!="", image!=""}[$__rate_interval]))` |
| **Memory** | `sum by (host, service) (container_memory_working_set_bytes{node=~"$node", vm_role=~"$vm_role", host=~"$host", service=~"$service", name!="", image!=""})` |
| **Mem Limit %** | `sum by (host, service) (container_memory_working_set_bytes{node=~"$node", vm_role=~"$vm_role", host=~"$host", service=~"$service", name!="", image!=""}) / clamp_min(sum by (host, service) (container_spec_memory_limit_bytes{node=~"$node", vm_role=~"$vm_role", host=~"$host", service=~"$service", name!="", image!=""}), 1)` |
| **Net In** | `sum by (host, service) (rate(container_network_receive_bytes_total{node=~"$node", vm_role=~"$vm_role", host=~"$host", service=~"$service", name!="", image!=""}[$__rate_interval]))` |
| **Net Out** | `sum by (host, service) (rate(container_network_transmit_bytes_total{node=~"$node", vm_role=~"$vm_role", host=~"$host", service=~"$service", name!="", image!=""}[$__rate_interval]))` |

**Mem Limit % design decisions:**
- Color: green <70%, yellow 70–90%, red >90%. A container at 90% of its Docker memory limit is about to be OOM-killed.
- `clamp_min(..., 1)` in the denominator prevents division by zero. Containers without explicit memory limits report `container_spec_memory_limit_bytes` as 0 or a very large sentinel value. Map values near 0 (< 0.001) to "—" via a value mapping to display "no limit" for unconstrained containers.
- This is the single most useful signal for preventing OOM kills before they happen.

**Interactivity (Focus → Investigate):**
- **Click a row → Focus (same dashboard):** Updates `$service` to that container via a self-link: `/d/${__dashboard.uid}?${__url_time_range}&var-service=${__data.fields.Service}&var-node=${node}&var-vm_role=${vm_role}&var-host=${host}`. Section 3 (Per-Container Detail) now renders for just that container, showing its CPU, memory, and network time series.
- **From focused view → Investigate (cross-dashboard):** Each row also has a "View Logs" data link: `/d/homelab-log-workbench?${__url_time_range}&var-host=${__data.fields.Host}&var-service=${__data.fields.Service}`. Opens D02 pre-filtered to that service's logs.
- The two-click pattern: click a row to focus on that container, then click again (or click the focused panel in Section 3) to investigate its logs on D02.

---

### Section 2 — Container Lifecycle

**Panel: Recent Restarts** — a **table** showing containers that started within the current time range, with a human-readable "Started At" column formatted as relative time (`dateTimeFromNow`). Sorted most recent first.

```promql
container_start_time_seconds{node=~"$node", vm_role=~"$vm_role", host=~"$host", service=~"$service", name!="", image!=""} * 1000 > (time() - $__range_s) * 1000
```

The `* 1000` converts to milliseconds for compatibility with Grafana's `dateTimeFromNow` unit on the value column. Use an `organize` transformation to hide raw labels (`env`, `node`, `vm_role`, `compose_project`, `container`, `image`, `instance`, `job`, `name`, `__name__`) and show only Host, Service, and Started At.

This is different from the fleet-level Containers panel on D00. D00 tells you "something is missing." D01b tells you "alloy restarted 3 times in the last hour, the last time was at 14:32."

**Why a table, not a scatter panel:** Grafana has no native scatter/annotation panel that works well for this use case. A table with relative timestamps is practical, sortable, and clickable.

**Why not `changes()`:** `changes()` returns 0 for a container that stopped and was recreated as a new series. `container_start_time_seconds` is only exported by cAdvisor for running containers, so a fresh `start_time` within the time window reliably indicates a recent start — whether from a crash loop or a manual restart.

**Interactivity:** Click a service name → D02 Log Workbench with `$service` set to that container and time preserved. The operator lands on the log lines around the restart to see what happened.

---

### Section 3 — Per-Container Detail

Three time series panels filtered by `$service`. When `$service` is narrowed to a specific value (via the Focus click in Section 1 or a drilldown from D01a), these panels show the focused investigation surface. When `$service = All`, all containers appear as separate lines on each panel.

- **Container CPU** — CPU usage per container (fraction of one core).
  ```promql
  sum by (service) (rate(container_cpu_usage_seconds_total{node=~"$node", vm_role=~"$vm_role", host=~"$host", service=~"$service", name!="", image!=""}[$__rate_interval]))
  ```

- **Container Memory** — working set memory per container (what the OOM killer uses), with a **memory limit reference line** overlaid as a dashed orange line. The limit query filters `> 0` to exclude containers without explicit limits.
  ```promql
  # Working set (actual usage)
  sum by (service) (container_memory_working_set_bytes{node=~"$node", vm_role=~"$vm_role", host=~"$host", service=~"$service", name!="", image!=""})

  # Memory limit reference (dashed line — shows the OOM ceiling)
  sum by (service) (container_spec_memory_limit_bytes{node=~"$node", vm_role=~"$vm_role", host=~"$host", service=~"$service", name!="", image!=""} > 0)
  ```
  The limit reference line is styled as dashed, semi-transparent orange via a `byRegexp` override matching `/^Limit:/`. When investigating a memory leak, the operator sees the working set growing toward a visible ceiling instead of mentally cross-referencing the table's Mem Limit % column.

- **Container Network** — bidirectional throughput per container. Receive is positive, transmit is rendered negative (`* -1`) so the chart is centered at zero. This is a standard network monitoring pattern — traffic asymmetry is immediately visible (e.g., qBittorrent uploading vs downloading). Receive lines are green, transmit lines are blue.
  ```promql
  # Receive (positive)
  sum by (service) (rate(container_network_receive_bytes_total{node=~"$node", vm_role=~"$vm_role", host=~"$host", service=~"$service", name!="", image!=""}[$__rate_interval]))

  # Transmit (negative — rendered below zero)
  - sum by (service) (rate(container_network_transmit_bytes_total{node=~"$node", vm_role=~"$vm_role", host=~"$host", service=~"$service", name!="", image!=""}[$__rate_interval]))
  ```

This approach (filter by `$service` variable, not repeat rows) avoids the noise of per-container repeated rows when many services are selected.

**Interactivity:** From a focused per-container panel → D02 Log Workbench with `$host` and `$service` pre-set. This is the terminal investigation step on D01b — the next insight comes from logs.

---

## Variables Exposed

| Variable | Visible | Multi | Notes |
|----------|---------|-------|-------|
| `$datasource_prometheus` | yes | no | |
| `$node` | yes | yes | |
| `$vm_role` | yes | yes | |
| `$host` | yes | yes | Cascades from `$vm_role` |
| `$service` | yes | yes | Primary interactive variable; defaults to All |

`$service` is the defining variable of this dashboard. It controls the resource table filter and the per-container detail time series panels. When D01a's bridge table links here with a specific `$service`, the dashboard opens pre-focused on that container.

---

## Drilldown Flow

**Receives from:**
- D00 Containers deficit stat — arrives with `$vm_role`, `$host` pre-set
- D00 Containers Running table row — arrives with `$host` pre-set
- D01a bridge table row — arrives with `$host`, `$service` pre-set (most common entry point)

**Drills out to:**
- D02 Log Workbench — from the Container Resource Table "View Logs" link, passes `$host`, `$service`
- D02 Log Workbench — from a restart event, passes `$service` and narrowed time
- D01a Host Workbench — if the operator needs to check host-level context (reverse navigation)

**Link format (Container table row → D02):**
```
/d/homelab-log-workbench?${__url_time_range}&var-host=${__data.fields.Host}&var-service=${__data.fields.Service}
```

---

## Click Flow Map

D01b supports both Focus (same-dashboard narrowing) and Investigate (cross-dashboard drilldown). The Container Resource Table is the primary interactive surface.

| Panel / Element | Click Type | Target | Context Passed |
|----------------|-----------|--------|----------------|
| Container Resource Table → row | **Focus** | Same dashboard | Updates `$service`; Section 3 renders for that container |
| Container Resource Table → "View Logs" | Investigate | D02 Log Workbench | `time`, `$host`, `$service` |
| Restart Timeline → event | Investigate | D02 Log Workbench | `time` (narrowed to event), `$service` |
| Per-Container Detail (Section 3) → panel | Investigate | D02 Log Workbench | `time`, `$host`, `$service` |

**Focus behavior:** Clicking a container row in the resource table updates `$service` and the dashboard re-renders. All panels that filter by `$service` now show only that container's data. The Per-Container Detail section (Section 3) becomes the focused investigation surface. To return to the all-containers view, reset `$service` to "All" in the variable dropdown.

---

## Anti-Patterns to Avoid

1. **Showing host-level CPU modes, memory breakdown, or disk I/O.** Those signals belong on D01a. D01b is purely container-scoped.
2. **Using `container_memory_usage_bytes` instead of `container_memory_working_set_bytes`.** `usage_bytes` includes cached pages that the kernel can reclaim. `working_set_bytes` is the actual memory the container needs — this is what OOM killer uses.
3. **Drilldown from D01b to D01b.** This dashboard is already the container investigation surface. Don't add links that loop back to itself with more filters.
4. **Defaulting `$service` to a specific container.** Default to "All" so the resource table shows the full fleet. The operator narrows via click or via drilldown URL from D01a.
5. **Putting host-level signals "just in case."** If the operator needs host context, they navigate to D01a. D01b stays focused on containers.
