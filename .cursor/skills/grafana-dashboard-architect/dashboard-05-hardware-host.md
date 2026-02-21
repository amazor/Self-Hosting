# Dashboard 05 — Hardware/Host

Planning reference for the Proxmox hypervisor and physical hardware health dashboard.

> **Status:** Planning / not yet built. Build after Proxmox node_exporter and a SMART collector are deployed on the bare-metal `pve1` host. Most signals here are unavailable without those additions.

---

## Purpose

Answers the question: **"Is the physical hardware healthy, and is Proxmox allocating resources sensibly?"**

This dashboard operates one level below the VM dashboards. While D01a shows what a VM is using, D05 shows what the bare-metal host actually has — and whether the VMs are approaching its physical limits. It also covers signals that VMs cannot see at all: CPU temperature, disk SMART health, and Proxmox's own view of resource allocation across all VMs.

---

## Ownership

### What D05 Owns

- Proxmox host CPU/RAM/disk utilization (the hypervisor itself, not any VM)
- VM allocation table: how many vCPUs and how much RAM is assigned to each VM vs. physical capacity
- CPU and disk temperatures via hardware monitoring
- Physical disk SMART health: reallocated sectors, pending sectors, uncorrectable errors, power-on hours
- Proxmox storage pool usage (local-lvm, ZFS if present)
- Container image currency — which running containers have newer images available (Diun integration, if deployed)

### What D05 Does NOT Own

- Per-VM application metrics (→ D01a/D01b)
- Log content (→ D02)
- Network probe results (→ D03)
- Media pipeline or application queues (→ D04)

### Boundary Rule

If the signal requires physical access to the machine or would be lost on a VM rebuild — it belongs here. CPU temperatures, SMART attributes, and Proxmox's allocation view are all signals that survive a VM wipe but would disappear if you were only monitoring from inside VMs.

---

## Main Insights This Dashboard Gives You

1. **Is the CPU overheating?** Thermal throttling is silent from inside a VM — the VM just gets slower. The physical host's temperature sensors give early warning before performance degrades or hardware fails.
2. **Are the disks dying?** SMART reallocated sectors are the clearest early warning of disk failure. A growing count means sectors are being remapped because they're failing — the disk is degrading before any I/O error surfaces.
3. **Is Proxmox overprovisioned?** VMs are often allocated more vCPUs and RAM than the host physically has. This is intentional (VMs don't all use their allocation simultaneously), but past a threshold it causes steal (visible in D01a) and memory pressure. The allocation view shows the cumulative picture.
4. **Is the Proxmox host itself healthy?** Node_exporter on `pve1` exposes the host's own CPU, memory, disk I/O — separate from what the VMs report. The host doing significant I/O for reasons unrelated to VMs (Proxmox backup, VM migration) is visible only here.
5. **How much disk does Proxmox have left?** VM disk images live in local-lvm (or equivalent). When local-lvm fills up, VM disks can't grow and new VMs can't be provisioned — a silent capacity ceiling.
6. **Are container images stale?** Running containers based on 6-month-old images have unpatched vulnerabilities and miss bug fixes. Diun or a similar tool flags available updates without enforcing auto-update.

---

## Signal Sources

**All signals here require new instrumentation on the bare-metal Proxmox host.**

| Signal | Source component | Status | Notes |
|--------|-----------------|--------|-------|
| Proxmox host CPU, RAM, disk, load | **node_exporter on pve1** | Not yet deployed | Needs to run on the bare metal host (not inside a VM) |
| CPU / disk temperatures | **node_exporter hwmon collector** | Not yet deployed | `--collector.hwmon` flag; reads `/sys/class/hwmon` — available on most x86 hardware |
| Disk SMART health | **node_exporter smartmon collector** or **smartd_exporter** | Not yet deployed | `--collector.diskstats` + `--collector.textfile` with smartmon.sh, or dedicated smartd_exporter container |
| Proxmox storage pool usage | **node_exporter on pve1** (disk metrics) | Not yet deployed | `node_filesystem_*` on pve1 will show lvm/zfs pool sizes |
| VM allocation (vCPU/RAM per VM) | **Proxmox API exporter** (pve_exporter) | Not yet deployed | Optional — pve_exporter queries the Proxmox API for VM inventory and allocation |
| Container image currency | **Diun** | Not yet deployed | Watches running containers; sends metrics or notifications when upstream images change |

### Proxmox node_exporter Deployment Consideration

`node_exporter` on `pve1` runs on bare metal (not in a VM). Two options:

1. **Native systemd service** — install `node_exporter` as a systemd service on the Proxmox Debian host. Cleanest; survives Proxmox upgrades. Follow node_exporter's official install guide.
2. **LXC container on pve1** — run node_exporter inside a privileged LXC with `/sys` bind-mounted. More isolated but complicates the deployment. Not recommended unless there's a policy reason.

Once deployed, add `pve1:9100` to Prometheus scrape targets with `host=pve1`, `vm_role=hypervisor`, `node=pve1`.

---

## Layout — Section Ideas

### Section 1 — Proxmox Host Overview (top)

**Question:** "Is the physical machine healthy right now?"

A summary row of the hypervisor host's resource state.

| Panel | Metric | Notes |
|-------|--------|-------|
| **Host CPU** | `1 - avg(rate(node_cpu_seconds_total{host="pve1", mode="idle"}[2m]))` | Gauge, 70%/85% thresholds |
| **Host Memory** | `1 - (node_memory_MemAvailable_bytes{host="pve1"} / node_memory_MemTotal_bytes{host="pve1"})` | Gauge |
| **Host Load** | `node_load1{host="pve1"}` | Stat with sparkline |
| **CPU Temp** | `node_hwmon_temp_celsius{host="pve1", sensor=~"coretemp.*"}` | Gauge, 70°C yellow / 85°C red |

---

### Section 2 — CPU Temperatures

**Question:** "Is the CPU at risk of thermal throttling?"

Time series showing CPU package and individual core temperatures over the time range. Most useful for identifying sustained thermal load vs. transient spikes.

```promql
# CPU package temperature (combined)
node_hwmon_temp_celsius{host="pve1", chip=~"coretemp.*", sensor="Package id 0"}

# Per-core temperatures (individual lines)
node_hwmon_temp_celsius{host="pve1", chip=~"coretemp.*", sensor=~"Core [0-9]+"}
```

**Design decisions:**
- Show `tmax` (thermal max) as a constant reference line if available from hwmon. Processors typically throttle at Tj_Max (often 100°C); yellow at 80°C, red at 90°C is conservative and appropriate.
- `node_hwmon_temp_celsius` labels vary by hardware. On the Beelink EQi13 (Intel i3-N305), expect `coretemp` chip with `Core 0`–`Core 7` sensors. Read the actual label values from Prometheus before writing the query.

---

### Section 3 — Disk Temperatures

**Question:** "Are the drives running hot?"

Stat panels or a table showing temperature per physical disk. Disk temperatures above 50–55°C accelerate wear.

```promql
# SMART temperature from node_exporter smartmon text collector
node_smartmon_attr_raw_value{host="pve1", attr_name="Temperature_Celsius"}

# Or from smartd_exporter
smartd_temperature_celsius{host="pve1"}
```

---

### Section 4 — SMART Health Table

**Question:** "Is any disk showing early warning signs?"

A table of physical disks × critical SMART attributes. This is the most actionable hardware health panel in the entire dashboard system.

| Column | SMART Attribute | Meaning |
|--------|----------------|---------|
| **Disk** | `disk` label | Physical device name (sda, nvme0n1, etc.) |
| **Reallocated Sectors** | `Reallocated_Sector_Ct` | Sectors remapped due to failure. Non-zero = disk is degrading. Growing count = accelerating degradation. |
| **Pending Sectors** | `Current_Pending_Sector` | Sectors waiting to be reallocated. Non-zero = imminent reallocations or read errors. |
| **Uncorrectable Errors** | `Offline_Uncorrectable` | Sectors that could not be recovered. Non-zero = data has been lost. |
| **Power-on Hours** | `Power_On_Hours` | Total lifetime. Useful for knowing when a drive is approaching MTBF age. |
| **Overall Status** | `node_smartmon_device_smart_healthy` | SMART overall assessment: 1 = healthy, 0 = failing |

**Color logic:**
- Reallocated Sectors: green at 0, red at any non-zero value
- Pending Sectors: green at 0, yellow at 1–5, red at >5
- Uncorrectable: green at 0, red at any non-zero value
- Overall Status: green at 1, red at 0

**Metric source options:**

```promql
# node_exporter with smartmon text collector (requires smartmontools + cron script)
node_smartmon_attr_raw_value{host="pve1", attr_name="Reallocated_Sector_Ct"}

# smartd_exporter (dedicated exporter, more reliable for NVMe)
smartd_attr_raw_value{disk="nvme0n1", attr_name="Media_and_Data_Integrity_Errors"}
```

**NVMe note:** NVMe drives use different SMART attributes than SATA/SAS. The equivalent of reallocated sectors is `Media_and_Data_Integrity_Errors` or `Critical_Warning`. Map these to the same table columns when the hardware is NVMe.

---

### Section 5 — Proxmox Storage Pools

**Question:** "How much space is left for VM disks and images?"

Bar gauges showing usage of each storage pool defined in Proxmox (local-lvm, local, any ZFS datasets).

```promql
# Filesystem usage on pve1 — Proxmox storage pools appear as mounted filesystems
node_filesystem_avail_bytes{host="pve1"}
node_filesystem_size_bytes{host="pve1"}
```

**Important:** `local-lvm` is typically a thin-provisioned LVM volume, which `node_filesystem_*` may not report correctly. The Proxmox API (via pve_exporter) is more reliable for storage pool reporting:

```promql
# pve_exporter storage metrics
pve_storage_shared{id=~"local.*"}
pve_storage_avail_bytes{id=~"local.*"}
pve_storage_total_bytes{id=~"local.*"}
```

---

### Section 6 — VM Allocation Table

**Question:** "How heavily is Proxmox provisioned relative to physical capacity?"

A table of all VMs with their allocated resources vs. the physical host totals. Requires pve_exporter or Proxmox API access.

| Column | Source |
|--------|--------|
| **VM Name** | pve_exporter `name` label |
| **Status** | `pve_up` (running/stopped) |
| **vCPUs Allocated** | `pve_cpu_max_cpus` |
| **RAM Allocated (GB)** | `pve_memory_max_bytes` |

Footer rows:
- **Total vCPU allocated**: sum of all VMs' allocations vs. physical core count
- **Total RAM allocated**: sum of all VMs' allocations vs. physical RAM

**Overprovisioning context:** A host with 8 physical cores and 24 vCPUs allocated across VMs is 3:1 overprovisioned. This is normal if VMs don't all peak simultaneously. But it explains steal time (D01a). This table makes the allocation visible so the operator understands why steal exists and whether it's a tuning opportunity.

---

### Section 7 — Container Image Currency (if Diun deployed)

**Question:** "Which running containers are based on outdated images?"

A table showing containers where Diun has detected a newer image upstream.

| Column | Source |
|--------|--------|
| **Service** | `service` label |
| **Host** | `host` label |
| **Current Image** | `image` label |
| **Update Available** | Diun metric flag |

This is a low-urgency awareness panel, not a triage signal. Updates don't auto-apply — the operator decides when to pull and redeploy. Color: green = current, yellow = update available (not critical), orange = update available for extended time.

**Note:** If Diun is not deployed, skip this section entirely. A panel that shows "no data" permanently is worse than having no panel. Diun is an optional quality-of-life addition.

---

## Variables Exposed

| Variable | Visible | Multi | Notes |
|----------|---------|-------|-------|
| `$datasource_prometheus` | yes | no | |
| `$node` | yes | yes | Proxmox node. This dashboard is *about* the node, so $node is primary axis |

Most panels in this dashboard are implicitly filtered to `host="pve1"` (the Proxmox host). Unlike other workbenches, the drill-by-host variable is less relevant — there is currently only one physical host. `$node` exists for future multi-node Proxmox expansion (e.g., adding a second server).

---

## Drilldown Flow

**Receives from:**
- D01a when a VM shows CPU steal time suggesting hypervisor contention
- D01a when load average is high relative to allocated cores
- D00 (manual navigation) when a hardware failure is suspected

**Drills out to:**
- D01a — when SMART errors or storage pressure might explain a VM's disk I/O problems
- D02 — SMART events may appear in syslog; check logs for disk error messages

---

## Click Flow Map

D05 is the physical layer dashboard. Most clicks are depth 1 — routing to D01a for the affected VM's detail. Some signals (temperatures, SMART) are informational endpoints where the operator acts directly (physical intervention, hardware planning) rather than drilling further into Grafana.

| Panel / Element | Click Type | Target | Context Passed |
|----------------|-----------|--------|----------------|
| Proxmox Host Overview → CPU/Memory gauge | Scroll | Section below for detail | — |
| Proxmox Host Overview → CPU Temp gauge | Scroll | Section 2 (CPU Temperatures) | — |
| CPU Temperatures → sustained high | Informational | — | Operator investigates cooling (physical) |
| Disk Temperatures → hot drive | Scroll | Section 4 (SMART Health) | Check for correlated SMART errors |
| SMART Health → disk with non-zero errors | Investigate | D02 Log Workbench | `time`, `host=pve1` (syslog for disk errors) |
| Proxmox Storage Pools → low pool | Informational | — | Operator plans disk provisioning |
| VM Allocation Table → VM row | Investigate | D01a Host Workbench | `time`, `$host` (from VM name) |
| Container Image Currency → stale image row | Informational | — | Operator decides whether to update |

**Physical layer signals are often terminal.** Unlike D01a/D01b–D04 where clicking leads deeper into Grafana, many D05 signals lead to physical actions: cleaning dust from a fan, replacing a degrading disk, or rebalancing VM allocations in Proxmox. The SMART Health table's link to D02 is the exception — checking syslog for disk error messages is a useful software investigation step before concluding hardware failure.

---

## Build Prerequisites Checklist

Before building this dashboard, confirm:

- [ ] **node_exporter** running as systemd service on `pve1` (bare metal, not in VM)
- [ ] `--collector.hwmon` flag enabled for temperature sensors
- [ ] `--collector.textfile` enabled with smartmon.sh script for SMART data (or smartd_exporter deployed)
- [ ] Prometheus scrape target added for `pve1:9100` with `host=pve1`, `vm_role=hypervisor`, `node=pve1`
- [ ] Verify temperature metrics: `node_hwmon_temp_celsius{host="pve1"}` returns results in Prometheus
- [ ] Verify SMART metrics: `node_smartmon_attr_raw_value{host="pve1"}` returns results
- [ ] (Optional) pve_exporter deployed for VM allocation and storage pool metrics
- [ ] (Optional) Diun deployed if image currency tracking is desired
