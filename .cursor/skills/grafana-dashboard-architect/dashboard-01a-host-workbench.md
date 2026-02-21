# Dashboard 01a — Host Workbench

Planning reference for the VM-level resource investigation dashboard.

> **Status:** Built. Dashboard file: `docker_compose/monitoring/dashboards/d01a-host-workbench.json` (UID: `homelab-host-workbench`).

---

## Purpose

Answers the question: **"What's wrong with this VM's resources?"**

This is the primary investigation surface for host-level resource issues. When D00 shows CPU/memory saturation, disk pressure, or stale scrape data, this dashboard provides the full picture: CPU modes, memory breakdown, swap, OOM events, disk I/O, and I/O latency.

It is never open unprompted — it is always reached from a D00 drilldown or a specific suspicion. For container-level investigation ("which container is responsible?"), the bridge table at the bottom routes to D01b.

---

## Ownership

### What D01a Owns

- Host CPU usage — all modes including iowait and steal (hypervisor contention)
- Host memory — RAM used/free/buffers/cache, swap usage, OOM kill events
- Host disk — per-mountpoint usage %, inodes, read/write I/O rates, I/O latency
- Load average (1m, 5m, 15m)
- NTP time offset (clock drift)
- NFS mount health — are the NFS mountpoints reporting filesystem metrics?
- Container summary table (bridge) — compact ranking of containers by resource use, linking to D01b

### What D01a Does NOT Own

- Per-container time series, Mem Limit %, or container network breakdown (→ D01b Container Workbench)
- Container lifecycle / restart timeline (→ D01b Container Workbench)
- Log text or error investigation (→ D02 Log Workbench)
- Probe results, DNS, TLS, or throughput investigation (→ D03 Network)
- Physical hardware temps, SMART disk health, or Proxmox host metrics (→ D05 Hardware)
- Application-level signals like download queue depth (→ D04 Media)

---

## Main Insights This Dashboard Gives You

1. **Where is the CPU going?** Is the VM running hot because a container is busy (user CPU), because the kernel is doing I/O (system), because disks are slow (iowait), or because the hypervisor is giving the vCPU to another VM (steal)?
2. **Is memory actually a problem?** "Memory is high" is different from "memory is exhausted." Swap usage and OOM kills tell you whether the system was ever forced to degrade.
3. **Are the disks under pressure?** High I/O wait on the host often points to a specific mount or container doing heavy writes. Inode exhaustion hides behind a healthy disk % — both are shown here.
4. **Is the NFS mount alive?** If a VM has NFS mounts, node_exporter exports filesystem metrics for those paths. Their presence (or absence) in the metrics tells you whether the mounts are healthy.
5. **Which container should I look at next?** The bridge table at the bottom ranks containers by CPU so you can identify the suspect and click through to D01b for the full container investigation.

---

## Signal Sources

All signals below are available today from the existing monitoring stack (node_exporter scraped by Prometheus). No new exporters required. The bridge table uses cAdvisor metrics for the container summary.

| Signal | Source metric | Labels |
|--------|--------------|--------|
| CPU by mode | `node_cpu_seconds_total` | `mode` (idle, user, system, iowait, steal, softirq) |
| Load average | `node_load1`, `node_load5`, `node_load15` | `host`, `vm_role` |
| NTP offset | `node_timex_offset_seconds` | `host`, `vm_role` |
| Memory usage | `node_memory_MemTotal_bytes`, `node_memory_MemAvailable_bytes`, `node_memory_Buffers_bytes`, `node_memory_Cached_bytes` | `host`, `vm_role` |
| Swap usage | `node_memory_SwapTotal_bytes`, `node_memory_SwapFree_bytes` | `host`, `vm_role` |
| OOM kill events | `node_vmstat_oom_kill` | `host`, `vm_role` |
| Disk usage | `node_filesystem_avail_bytes`, `node_filesystem_size_bytes` | `host`, `mountpoint`, `fstype` |
| Inode usage | `node_filesystem_files_free`, `node_filesystem_files` | `host`, `mountpoint` |
| Disk I/O | `node_disk_read_bytes_total`, `node_disk_written_bytes_total`, `node_disk_io_time_seconds_total` | `host`, `device` |
| Disk latency | `node_disk_read_time_seconds_total`, `node_disk_write_time_seconds_total`, `node_disk_reads_completed_total`, `node_disk_writes_completed_total` | `host`, `device` |
| NFS mount metrics | `node_filesystem_avail_bytes{fstype=~"nfs\|nfs4"}` | `host`, `mountpoint` |
| Container CPU (bridge) | `container_cpu_usage_seconds_total` | `host`, `service`, `container` |
| Container memory (bridge) | `container_memory_working_set_bytes` | `host`, `service`, `container` |

---

## Query Convention

All queries include the full variable cascade (`node`, `vm_role`, `host`) so that variable selections are always respected. The shorthand `$filter` in this document refers to `node=~"$node", vm_role=~"$vm_role", host=~"$host"`.

---

## Layout — Section Ideas

> **Built layout note:** The built dashboard refines the section order below. It adds a **Quick Summary** row at the top (stat panels for Load 1m/5m/15m, Swap Used, OOM Kills, NTP Offset), followed by Host CPU, Host Memory (not collapsed), Disk (collapsed), and Container Summary. The queries and panel designs below remain accurate — only the section grouping changed.

### Section 1 — Host CPU

**Panels:**

- **CPU by Mode** — stacked area chart showing where CPU time goes: user, system, iowait, steal, softirq, nice. Full width.
  ```promql
  avg by (mode) (rate(node_cpu_seconds_total{node=~"$node", vm_role=~"$vm_role", host=~"$host", mode!="idle"}[$__rate_interval]))
  ```
  - **iowait** colored orange — visible I/O pressure
  - **steal** colored red — hypervisor contention; any steal on a lightly loaded VM is significant

- **CPU % per Host** — line per VM showing total busy percentage. Useful when D01a opens with multiple hosts selected — identifies which VM to focus on before the mode breakdown explains why.
  ```promql
  1 - avg by (host) (rate(node_cpu_seconds_total{node=~"$node", vm_role=~"$vm_role", host=~"$host", mode="idle"}[$__rate_interval]))
  ```

- **Load Average** — three stat panels (1m / 5m / 15m) next to each other. Uses `max()` to show worst-case across selected hosts. Threshold at vCPU count (yellow at 2, red at 4 — fleet is mixed: core/monitoring/apps have 2 vCPUs, media/accelerated have 4).
  ```promql
  max(node_load1{node=~"$node", vm_role=~"$vm_role", host=~"$host"})
  max(node_load5{node=~"$node", vm_role=~"$vm_role", host=~"$host"})
  max(node_load15{node=~"$node", vm_role=~"$vm_role", host=~"$host"})
  ```

- **NTP Time Offset** — stat panel showing worst-case clock drift. Uses `abs()` because drift can be negative. Green at <0.1s, yellow at 0.1–1s, red at >1s.
  ```promql
  max(abs(node_timex_offset_seconds{node=~"$node", vm_role=~"$vm_role", host=~"$host"}))
  ```

**Why NTP offset matters:** When investigating a VM and correlating its metrics with logs from another VM (via D02), time drift makes that correlation impossible. A 2-second drift means "14:32:05 on this VM" is "14:32:03 real time" — enough to misattribute an error spike to the wrong event. Cloud-init VMs usually configure NTP during provisioning, but `systemd-timesyncd` can lose sync after prolonged uptime, VM migrations, or if the NTP server becomes unreachable. This stat is context for the investigation workflow: if the number is green, timestamps are trustworthy. If yellow or red, every time-based correlation on this dashboard and D02 needs a mental offset.

**Why steal matters:** cAdvisor and node_exporter measure CPU from inside the VM. If the host is overprovisioned, Proxmox throttles the vCPU — the VM sees this as `steal`. A VM can show 30% overall CPU usage but 20% steal, meaning it's only getting 10% effective compute. Without the steal mode, saturation looks mild when it's actually severe.

---

### Section 2 — Host Memory

**Panels:**

- **Memory Composition** — stacked area with three series showing where RAM goes. Used = non-reclaimable (actual pressure). Cache/Buffers = reclaimable by kernel on demand. Free = immediately available.
  ```promql
  # Used (actual pressure — what can't be reclaimed)
  avg(node_memory_MemTotal_bytes{node=~"$node", vm_role=~"$vm_role", host=~"$host"} - node_memory_MemAvailable_bytes{node=~"$node", vm_role=~"$vm_role", host=~"$host"})

  # Cache/Buffers (reclaimable — the kernel will release these under pressure)
  avg(node_memory_MemAvailable_bytes{node=~"$node", vm_role=~"$vm_role", host=~"$host"} - node_memory_MemFree_bytes{node=~"$node", vm_role=~"$vm_role", host=~"$host"})

  # Free (immediately available)
  avg(node_memory_MemFree_bytes{node=~"$node", vm_role=~"$vm_role", host=~"$host"})
  ```

- **Memory Used % per Host** — line per VM showing percentage of memory in use. Based on MemAvailable (accounts for reclaimable cache/buffers). Useful for comparing hosts with different RAM sizes.
  ```promql
  1 - (node_memory_MemAvailable_bytes{node=~"$node", vm_role=~"$vm_role", host=~"$host"} / node_memory_MemTotal_bytes{node=~"$node", vm_role=~"$vm_role", host=~"$host"})
  ```

- **Swap Used** — stat panel with sparkline. Uses `max()` for worst-case across selected hosts. Threshold: any swap use at all is yellow (memory was exhausted at some point), >100 MB is red.
  ```promql
  max(node_memory_SwapTotal_bytes{node=~"$node", vm_role=~"$vm_role", host=~"$host"} - node_memory_SwapFree_bytes{node=~"$node", vm_role=~"$vm_role", host=~"$host"})
  ```

- **OOM Kill Counter** — stat panel. Green at 0, red at any positive value. Uses `sum()` to aggregate across hosts and `OR on() vector(0)` to show 0 instead of no-data when no OOM kills have occurred.
  ```promql
  sum(increase(node_vmstat_oom_kill{node=~"$node", vm_role=~"$vm_role", host=~"$host"}[$__range])) OR on() vector(0)
  ```

**Why separate swap panel:** Swap in the memory stacked chart is easy to miss. Swap being used at all means RAM was exhausted at some point — it deserves its own clear signal, not a sliver in a stacked chart.

---

### Section 3 — Disk

All filesystem queries filter to real filesystems (`fstype=~"ext4|xfs|btrfs|nfs|nfs4"`) and exclude Docker overlay mounts (`mountpoint!~"/var/lib/docker/.*"`). All block device queries filter out virtual devices (`device!~"loop.*|dm-.*"`).

**Panels:**

- **Filesystem Usage** — bar gauge per mountpoint, showing used percentage. Includes both local and NFS mounts.
  ```promql
  1 - (node_filesystem_avail_bytes{node=~"$node", vm_role=~"$vm_role", host=~"$host", fstype=~"ext4|xfs|btrfs|nfs|nfs4", mountpoint!~"/var/lib/docker/.*"} / node_filesystem_size_bytes{node=~"$node", vm_role=~"$vm_role", host=~"$host", fstype=~"ext4|xfs|btrfs|nfs|nfs4", mountpoint!~"/var/lib/docker/.*"})
  ```

- **Inode Usage** — bar gauge per mountpoint. Separate from disk %. Running out of inodes on a full disk % is a different failure mode.
  ```promql
  1 - (node_filesystem_files_free{node=~"$node", vm_role=~"$vm_role", host=~"$host", fstype=~"ext4|xfs|btrfs|nfs|nfs4", mountpoint!~"/var/lib/docker/.*"} / node_filesystem_files{node=~"$node", vm_role=~"$vm_role", host=~"$host", fstype=~"ext4|xfs|btrfs|nfs|nfs4", mountpoint!~"/var/lib/docker/.*"})
  ```

- **Disk I/O Rate** — time series showing read and write bytes/sec per device. Useful for finding which device is hot during high iowait.
  ```promql
  rate(node_disk_read_bytes_total{node=~"$node", vm_role=~"$vm_role", host=~"$host", device!~"loop.*|dm-.*"}[$__rate_interval])
  rate(node_disk_written_bytes_total{node=~"$node", vm_role=~"$vm_role", host=~"$host", device!~"loop.*|dm-.*"}[$__rate_interval])
  ```

- **I/O Utilization** — gauge per device, showing fraction of time the disk was busy.
  ```promql
  rate(node_disk_io_time_seconds_total{node=~"$node", vm_role=~"$vm_role", host=~"$host", device!~"loop.*|dm-.*"}[$__rate_interval])
  ```

- **I/O Latency** — time series per device, showing average seconds per operation. Uses `clamp_min(..., 0.001)` in the denominator to prevent NaN on idle devices with zero completed operations.
  ```promql
  rate(node_disk_read_time_seconds_total{node=~"$node", vm_role=~"$vm_role", host=~"$host", device!~"loop.*|dm-.*"}[$__rate_interval])
    / clamp_min(rate(node_disk_reads_completed_total{node=~"$node", vm_role=~"$vm_role", host=~"$host", device!~"loop.*|dm-.*"}[$__rate_interval]), 0.001)

  rate(node_disk_write_time_seconds_total{node=~"$node", vm_role=~"$vm_role", host=~"$host", device!~"loop.*|dm-.*"}[$__rate_interval])
    / clamp_min(rate(node_disk_writes_completed_total{node=~"$node", vm_role=~"$vm_role", host=~"$host", device!~"loop.*|dm-.*"}[$__rate_interval]), 0.001)
  ```

**Why I/O latency matters for self-hosting:** NFS mounts are the primary case. When the NAS is under load (RAID rebuild, Synology package running, another VM doing heavy writes), NFS latency spikes. A Sonarr import takes 30 seconds instead of 1 second. CPU and disk utilization look normal. Only latency reveals the problem. Threshold: green <10ms, yellow 10–50ms, red >50ms. NFS mounts typically show higher baseline latency than local disks — adjust thresholds based on observed normal values.

**Note on five disk panels:** This section has five panels (filesystem usage, inodes, I/O rate, I/O utilization, I/O latency). If this feels too dense, I/O utilization is the most expendable — latency is more actionable for diagnosing "why is this slow?" while utilization is correlated with but less specific than latency.

---

### Section 4 — Container Summary Table (Bridge)

**Panel:** Compact table of containers on the selected host(s), ranked by CPU usage. This is a **routing surface**, not an investigation surface — it exists to help the operator identify which container to investigate next and click through to D01b.

| Column | Query / Source |
|--------|---------------|
| **Host** | `host` label |
| **Service** | `service` label |
| **CPU %** | `sum by (host, service) (rate(container_cpu_usage_seconds_total{node=~"$node", vm_role=~"$vm_role", host=~"$host", name!="", image!=""}[$__rate_interval]))` |
| **Memory** | `sum by (host, service) (container_memory_working_set_bytes{node=~"$node", vm_role=~"$vm_role", host=~"$host", name!="", image!=""})` |

Sorted descending by CPU. No Mem Limit %, no Net I/O, no restart column — those belong on D01b where they have full context. Queries include `name!=""` and `image!=""` to exclude cAdvisor's internal housekeeping pseudo-containers.

**Interactivity:** Each row links to D01b Container Workbench with `$host` and `$service` pre-set:
```
/d/homelab-container-workbench?${__url_time_range}&var-host=${__data.fields.Host}&var-service=${__data.fields.Service}
```

**Why a bridge table instead of just links:** The operator needs attribution before deciding where to click. "CPU is high on this VM" → which container? The bridge table answers that in 2 seconds, then routes. Without it, the operator would have to click to D01b blind and then scan the full container table.

---

## Variables Exposed

| Variable | Visible | Multi | Notes |
|----------|---------|-------|-------|
| `$datasource_prometheus` | yes | no | |
| `$node` | yes | yes | |
| `$vm_role` | yes | yes | Primary filter |
| `$host` | yes | yes | Cascades from `$vm_role` |

D01a does not expose `$service` — container-level filtering is D01b's domain. The bridge table passes `$service` via drilldown links only.

---

## Drilldown Flow

**Receives from:**
- D00 VM Availability panel — arrives with `$vm_role`, `$host` pre-set
- D00 Resource Saturation gauges (CPU, Memory, Disk) — same context
- D00 OOM Kills stat — arrives with time context
- D00 Data Freshness (stale host) — same context
- D03 when high throughput is observed
- D05 when a VM shows hardware issues

**Drills out to:**
- D01b Container Workbench — from the bridge table, passes `$host`, `$service`
- D02 Log Workbench — from OOM Kill counter (errors around OOM events)
- D03 Network — from NFS mount or I/O latency panels (NAS reachability)

**Link format (Bridge table row → D01b):**
```
/d/homelab-container-workbench?${__url_time_range}&var-host=${__data.fields.Host}&var-service=${__data.fields.Service}
```

---

## Click Flow Map

D01a supports Investigate (cross-dashboard drilldown). The bridge table is the primary interactive surface for routing to D01b. Host-level panels link to D02 or D03 for specific failure modes.

| Panel / Element | Click Type | Target | Context Passed |
|----------------|-----------|--------|----------------|
| CPU time series → high steal | Investigate | D05 Hardware/Host | `time` (hypervisor contention) |
| OOM Kill counter (non-zero) | Investigate | D02 Log Workbench | `time`, `$host`, `level=error` |
| Filesystem → NFS mount bar | Investigate | D03 Network/Connectivity | `time` (NAS reachability) |
| I/O Latency → high NFS latency | Investigate | D03 Network/Connectivity | `time`, `$host` |
| Bridge table → row | Investigate | D01b Container Workbench | `time`, `$host`, `$service` |

---

## Anti-Patterns to Avoid

1. **Showing all VMs at once by default.** The workbench is investigation context. Default `$host` to the most recently drilled VM or prompt selection. A 6-VM time series stack on every panel is unreadable.
2. **Adding per-container time series or repeat rows.** Per-container detail belongs on D01b. D01a shows host-level signals and a compact container summary — nothing more.
3. **Using `container_memory_usage_bytes` in the bridge table.** Use `container_memory_working_set_bytes` — it's what the OOM killer uses.
4. **Averaging CPU across cores.** Show aggregate CPU as a fraction of total cores, but keep modes (iowait, steal) visible as stacked areas. Averaging hides steal entirely.
5. **Disk % only.** Always pair disk % with inode usage. A logs directory filling with small files can exhaust inodes while disk % remains at 40%.
6. **Adding Mem Limit % or restart counts to the bridge table.** The bridge table is a routing surface. Investigation detail belongs on D01b where it has full context.
