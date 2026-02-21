# Dashboard 01 — Infrastructure Workbench

Planning reference for the host and container metrics investigation dashboard.

> **Status:** Planning / not yet built. This file captures intent, signal inventory, and panel ideas. Refine into a full spec before building.

---

## Purpose

Answers the question: **"Why is this VM struggling?"**

This is the primary investigation surface for resource and container health issues. When D00 shows CPU/memory saturation, a container deficit, or stale scrape data, this dashboard provides the full picture: time series, per-container breakdown, disk I/O, OOM events, and CPU steal from the hypervisor.

It is never open unprompted — it is always reached from a D00 drilldown or a specific suspicion.

---

## Ownership

### What D01 Owns

- Host CPU usage — all modes including iowait and steal (hypervisor contention)
- Host memory — RAM used/free/buffers/cache, swap usage, OOM kill events
- Host disk — per-mountpoint usage %, inodes, read/write I/O rates
- Container resource consumption — CPU, memory, and network per container
- Container lifecycle — which containers are running, which have restarted, and when
- Load average (1m, 5m, 15m)
- NFS mount health — are the NFS mountpoints reporting filesystem metrics?

### What D01 Does NOT Own

- Log text or error investigation (→ D02 Log Workbench)
- Probe results, DNS, TLS, or throughput investigation (→ D03 Network)
- Physical hardware temps, SMART disk health, or Proxmox host metrics (→ D05 Hardware)
- Application-level signals like download queue depth (→ D04 Media)

---

## Main Insights This Dashboard Gives You

1. **Where is the CPU going?** Is the VM running hot because a container is busy (user CPU), because the kernel is doing I/O (system), because disks are slow (iowait), or because the hypervisor is giving the vCPU to another VM (steal)?
2. **Is memory actually a problem?** "Memory is high" is different from "memory is exhausted." Swap usage and OOM kills tell you whether the system was ever forced to degrade.
3. **Which container is responsible?** D00 tells you a VM is saturated. D01 ranks containers by CPU and memory so you can attribute the saturation to a specific service.
4. **Is something restarting?** The restart timeline shows exactly which containers restarted, when, and how frequently — not just a count.
5. **Are the disks under pressure?** High I/O wait on the host often points to a specific mount or container doing heavy writes. Inode exhaustion hides behind a healthy disk % — both are shown here.
6. **Is the NFS mount alive?** If a VM has NFS mounts, node_exporter exports filesystem metrics for those paths. Their presence (or absence) in the metrics tells you whether the mounts are healthy.

---

## Signal Sources

All signals below are available today from the existing monitoring stack (node_exporter + cAdvisor scraped by Prometheus). No new exporters required for this dashboard.

| Signal | Source metric | Labels |
|--------|--------------|--------|
| CPU by mode | `node_cpu_seconds_total` | `mode` (idle, user, system, iowait, steal, softirq) |
| Load average | `node_load1`, `node_load5`, `node_load15` | `host`, `vm_role` |
| Memory usage | `node_memory_MemTotal_bytes`, `node_memory_MemAvailable_bytes`, `node_memory_Buffers_bytes`, `node_memory_Cached_bytes` | `host`, `vm_role` |
| Swap usage | `node_memory_SwapTotal_bytes`, `node_memory_SwapFree_bytes` | `host`, `vm_role` |
| OOM kill events | `node_vmstat_oom_kill` | `host`, `vm_role` |
| Disk usage | `node_filesystem_avail_bytes`, `node_filesystem_size_bytes` | `host`, `mountpoint`, `fstype` |
| Inode usage | `node_filesystem_files_free`, `node_filesystem_files` | `host`, `mountpoint` |
| Disk I/O | `node_disk_read_bytes_total`, `node_disk_write_bytes_total`, `node_disk_io_time_seconds_total` | `host`, `device` |
| Container CPU | `container_cpu_usage_seconds_total` | `host`, `service`, `container`, `image` |
| Container memory | `container_memory_working_set_bytes` | `host`, `service`, `container` |
| Container network | `container_network_receive_bytes_total`, `container_network_transmit_bytes_total` | `host`, `service`, `container` |
| Container restarts | `container_start_time_seconds` | `host`, `service`, `container` |
| NFS mount metrics | `node_filesystem_avail_bytes{fstype=~"nfs|nfs4"}` | `host`, `mountpoint` |

---

## Layout — Section Ideas

### Section 1 — Host CPU

**Panels:**

- **CPU Usage Time Series** — stacked area chart with modes: user, system, iowait, steal. Full width, spans the whole time range.
  ```promql
  # Each mode as a separate series
  rate(node_cpu_seconds_total{host=~"$host", mode!="idle"}[$__rate_interval])
  # Average across cores (already 0–1 fraction)
  avg by (mode) (rate(node_cpu_seconds_total{host=~"$host", mode!="idle"}[$__rate_interval]))
  ```
  - **iowait** colored orange — visible I/O pressure
  - **steal** colored red — hypervisor contention; any steal on a lightly loaded VM is significant

- **Load Average** — three stat panels (1m / 5m / 15m) next to each other. Threshold at vCPU count (yellow) and 2× vCPU count (red).
  ```promql
  node_load1{host=~"$host"}
  node_load5{host=~"$host"}
  node_load15{host=~"$host"}
  ```

- **NTP Time Offset** — stat panel showing clock drift. Green at <0.1s, yellow at 0.1–1s, red at >1s.
  ```promql
  node_timex_offset_seconds{host=~"$host"}
  ```

**Why NTP offset matters:** When investigating a VM and correlating its metrics with logs from another VM (via D02), time drift makes that correlation impossible. A 2-second drift means "14:32:05 on this VM" is "14:32:03 real time" — enough to misattribute an error spike to the wrong event. Cloud-init VMs usually configure NTP during provisioning, but `systemd-timesyncd` can lose sync after prolonged uptime, VM migrations, or if the NTP server becomes unreachable. This stat is context for the investigation workflow: if the number is green, timestamps are trustworthy. If yellow or red, every time-based correlation on this dashboard and D02 needs a mental offset.

**Why steal matters:** cAdvisor and node_exporter measure CPU from inside the VM. If the host is overprovisioned, Proxmox throttles the vCPU — the VM sees this as `steal`. A VM can show 30% overall CPU usage but 20% steal, meaning it's only getting 10% effective compute. Without the steal mode, saturation looks mild when it's actually severe.

---

### Section 2 — Host Memory

**Panels:**

- **Memory Breakdown Time Series** — stacked area: used (total - available), buffers, cached, free.
  ```promql
  # Used (without buffers/cache — the "real" pressure signal)
  node_memory_MemTotal_bytes{host=~"$host"} - node_memory_MemAvailable_bytes{host=~"$host"}
  ```

- **Swap Used** — stat panel with sparkline. Threshold: any swap use at all is yellow (memory was exhausted at some point).
  ```promql
  node_memory_SwapTotal_bytes{host=~"$host"} - node_memory_SwapFree_bytes{host=~"$host"}
  ```

- **OOM Kill Counter** — stat panel. Green at 0, red at any positive value. Counts since node_exporter start — use `increase()` over the time range for "OOM kills in this window."
  ```promql
  increase(node_vmstat_oom_kill{host=~"$host"}[$__range])
  ```

**Why separate swap panel:** Swap in the memory stacked chart is easy to miss. Swap being used at all means RAM was exhausted at some point — it deserves its own clear signal, not a sliver in a stacked chart.

---

### Section 3 — Disk

**Panels:**

- **Filesystem Usage** — bar gauge per mountpoint, showing `1 - (avail/size)`. Include both `mountpoint="/"` and `fstype=~"nfs|nfs4"` for NFS mounts.
  ```promql
  1 - (node_filesystem_avail_bytes{host=~"$host"} / node_filesystem_size_bytes{host=~"$host"})
  ```

- **Inode Usage** — bar gauge per mountpoint. Separate from disk %. Running out of inodes on a full disk % is a different failure mode.
  ```promql
  1 - (node_filesystem_files_free{host=~"$host"} / node_filesystem_files{host=~"$host"})
  ```

- **Disk I/O Rate** — time series showing read and write bytes/sec per device. Useful for finding which device is hot during high iowait.
  ```promql
  rate(node_disk_read_bytes_total{host=~"$host"}[$__rate_interval])
  rate(node_disk_write_bytes_total{host=~"$host"}[$__rate_interval])
  ```

- **I/O Utilization** — gauge per device, showing % of time the disk was busy.
  ```promql
  rate(node_disk_io_time_seconds_total{host=~"$host"}[$__rate_interval])
  ```

- **I/O Latency** — gauge or time series per device, showing average milliseconds per operation. This is the signal that throughput and utilization miss: a disk (especially an NFS mount) can show low utilization and low throughput but 200ms per operation — making every container on the VM feel sluggish.
  ```promql
  # Average read latency (seconds per operation)
  rate(node_disk_read_time_seconds_total{host=~"$host"}[$__rate_interval])
    / rate(node_disk_reads_completed_total{host=~"$host"}[$__rate_interval])

  # Average write latency (seconds per operation)
  rate(node_disk_write_time_seconds_total{host=~"$host"}[$__rate_interval])
    / rate(node_disk_writes_completed_total{host=~"$host"}[$__rate_interval])
  ```

**Why I/O latency matters for self-hosting:** NFS mounts are the primary case. When the NAS is under load (RAID rebuild, Synology package running, another VM doing heavy writes), NFS latency spikes. A Sonarr import takes 30 seconds instead of 1 second. CPU and disk utilization look normal. Only latency reveals the problem. Threshold: green <10ms, yellow 10–50ms, red >50ms. NFS mounts typically show higher baseline latency than local disks — adjust thresholds based on observed normal values.

**Note on five disk panels:** This section has five panels (filesystem usage, inodes, I/O rate, I/O utilization, I/O latency). If this feels too dense, I/O utilization is the most expendable — latency is more actionable for diagnosing "why is this slow?" while utilization is correlated with but less specific than latency.

---

### Section 4 — Container Resource Table

**Panel:** Table of all containers on the selected host(s), ranked by CPU usage.

| Column | Query / Source |
|--------|---------------|
| **Container** | `container` label |
| **Service** | `service` label |
| **CPU %** | `rate(container_cpu_usage_seconds_total[2m])` — fraction of one core |
| **Memory** | `container_memory_working_set_bytes` (bytes → human-readable) |
| **Mem Limit %** | `container_memory_working_set_bytes / container_spec_memory_limit_bytes` — percentage of Docker memory limit used |
| **Net In/Out** | `rate(container_network_receive/transmit_bytes_total[2m])` |
| **Restarts** | `container_start_time_seconds > (time() - $__range_s)` — bool: did it restart? |

This is the table that answers "which container is causing the saturation on this VM." Sorted descending by CPU.

**Mem Limit % design decisions:**
- Color: green <70%, yellow 70–90%, red >90%. A container at 90% of its Docker memory limit is about to be OOM-killed.
- Containers without explicit memory limits report `container_spec_memory_limit_bytes` as 0 or a very large sentinel value. Use `clamp_min(..., 1)` in the denominator and display "—" or "no limit" for containers where the limit exceeds physical RAM.
- This is the single most useful signal for preventing OOM kills before they happen. Arr applications, Jellyfin, and qBittorrent all have known memory leak patterns that accumulate over days/weeks. The Memory column (absolute bytes) tells you "Sonarr: 1.7 GB" — meaningless without knowing the limit is 2 GB. Mem Limit % tells you "Sonarr: 85% — it's about to be killed."

**Interactivity (Focus → Investigate):**
- **Click a row → Focus (same dashboard):** Updates `$service` to that container via a self-link: `/d/${__dashboard.uid}?var-service=${__data.fields.service}&from=${__from}&to=${__to}&var-node=${node}&var-vm_role=${vm_role}&var-host=${host}`. Section 6 (Per-Container Detail) now renders for just that container, showing its CPU, memory, and network time series.
- **From focused view → Investigate (cross-dashboard):** Each row also has a "View Logs" data link: `/d/<d02-uid>?var-host=${__data.fields.host}&var-service=${__data.fields.service}&from=${__from}&to=${__to}`. Opens D02 pre-filtered to that service's logs.
- The two-click pattern: click a row to focus on that container, then click again (or click the focused panel in Section 6) to investigate its logs on D02.

---

### Section 5 — Container Lifecycle

**Panel: Restart Timeline** — scatter/annotation overlay on a time axis showing which containers restarted and when.

```promql
# Each container's restart time as a point on the timeline
container_start_time_seconds{host=~"$host"} > (time() - $__range_s)
```

This is different from the fleet-level Containers panel on D00. D00 tells you "something is missing." D01 tells you "alloy restarted 3 times in the last hour, the last time was at 14:32."

**Why not `changes()`:** `changes()` returns 0 for a container that stopped and was recreated as a new series. `container_start_time_seconds` is only exported by cAdvisor for running containers, so a fresh `start_time` within the time window reliably indicates a recent start — whether from a crash loop or a manual restart.

**Interactivity:** Click a restart event → D02 Log Workbench with `$service` set to that container and time narrowed to the restart timestamp. The operator lands directly on the log lines around the restart to see what happened.

---

### Section 6 — Per-Container Detail (Repeat Row)

A row that repeats for each `$service` value in the variable. Each row contains:

- CPU time series for that container
- Memory time series for that container
- Container network receive/transmit

This section is optional and only renders when `$service` is filtered to a specific value. When `$service = All`, this section is too noisy — consider hiding it via a conditional or a separate "drilldown" dashboard UID.

---

## Variables Exposed

| Variable | Visible | Multi | Notes |
|----------|---------|-------|-------|
| `$datasource_prometheus` | yes | no | |
| `$node` | yes | yes | |
| `$vm_role` | yes | yes | Primary filter |
| `$host` | yes | yes | Cascades from `$vm_role` |
| `$service` | yes | yes | Container-level filter; defaults to All |

---

## Drilldown Flow

**Receives from:**
- D00 VM Availability panel — arrives with `$vm_role`, `$host` pre-set
- D00 Resource Saturation gauges — same context
- D00 Data Freshness (stale host) — same context
- D03 when high throughput is observed

**Drills out to:**
- D02 Log Workbench — from the Container Resource Table, passes `$host`, `$service`
- D03 Network — when container network I/O is high

**Link format (Container table row → D02):**
```
/d/<d02-uid>?from=${__from}&to=${__to}&var-host=${__data.fields.host}&var-service=${__data.fields.service}
```

---

## Click Flow Map

D01 supports both Focus (same-dashboard narrowing) and Investigate (cross-dashboard drilldown). The Container Resource Table is the primary interactive surface.

| Panel / Element | Click Type | Target | Context Passed |
|----------------|-----------|--------|----------------|
| CPU time series → high steal | Investigate | D05 Hardware/Host | `time` (hypervisor contention) |
| OOM Kill counter (non-zero) | Investigate | D02 Log Workbench | `time`, `$host`, `level=error` |
| Filesystem → NFS mount bar | Investigate | D03 Network/Connectivity | `time` (NAS reachability) |
| I/O Latency → high NFS latency | Investigate | D03 Network/Connectivity | `time`, `$host` |
| Container Resource Table → row | **Focus** | Same dashboard | Updates `$service`; Section 6 renders for that container |
| Container Resource Table → "View Logs" | Investigate | D02 Log Workbench | `time`, `$host`, `$service` |
| Restart Timeline → event | Investigate | D02 Log Workbench | `time` (narrowed to event), `$service` |
| Per-Container Detail (Section 6) → panel | Investigate | D02 Log Workbench | `time`, `$host`, `$service` |

**Focus behavior:** Clicking a container row in the resource table updates `$service` and the dashboard re-renders. All panels that filter by `$service` now show only that container's data. The Per-Container Detail section (Section 6) becomes the focused investigation surface. To return to the all-containers view, reset `$service` to "All" in the variable dropdown.

---

## Anti-Patterns to Avoid

1. **Showing all VMs at once by default.** The workbench is investigation context. Default `$host` to the most recently drilled VM or prompt selection. A 6-VM time series stack on every panel is unreadable.
2. **Using `container_memory_usage_bytes` instead of `container_memory_working_set_bytes`.** `usage_bytes` includes cached pages that the kernel can reclaim. `working_set_bytes` is the actual memory the container needs — this is what OOM killer uses.
3. **Averaging CPU across cores.** Show aggregate CPU as a fraction of total cores, but keep modes (iowait, steal) visible as stacked areas. Averaging hides steal entirely.
4. **Disk % only.** Always pair disk % with inode usage. A logs directory filling with small files can exhaust inodes while disk % remains at 40%.
5. **Drilldown from D01 to D01.** This dashboard is already the investigation surface. Don't add links that loop back to itself with more filters — that's dashboard sprawl.
