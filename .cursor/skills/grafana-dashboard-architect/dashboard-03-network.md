# Dashboard 03 — Network/Connectivity

Architecture spec for the network investigation workbench.

---

## Purpose

Answers the question: **"Why can't I reach it?"**

This is the investigation surface for connectivity failures. When D00's Connectivity panel turns red, this dashboard tells you which specific probe failed. When a service is unreachable, this dashboard shows you whether the failure is DNS, routing, the proxy, a certificate, or a network path issue.

**Prerequisites:** This dashboard requires [Blackbox Exporter](https://github.com/prometheus/blackbox_exporter) running on the `monitoring` VM and scraped by Prometheus. No panels in this dashboard are buildable without it (except network throughput and packet error panels, which use existing node_exporter and cAdvisor data).

---

## Ownership

### What D03 Owns

- All Blackbox probe results (HTTP, DNS, TCP, ICMP, TLS) with history
- Per-service reverse proxy health (one HTTP probe per app behind the proxy)
- DNS resolution detail — per-hostname results and latency
- Inter-VM reachability matrix (ping/TCP from `monitoring` to each target)
- TLS certificate expiry timeline
- Network throughput per VM and per container
- Packet error and drop rates per VM and container
- NAS reachability from each VM that mounts it

### What D03 Does NOT Own

- Why a container is producing errors (→ D02 Log Workbench)
- Whether a container is running (→ D00/D01)
- Host CPU/RAM/disk utilization (→ D01 Infra Workbench)
- Application-level metrics like queue depth (→ D04 Media Pipeline)

### Boundary Rule

If the question is "can X reach Y?" — it belongs here. If the question is "what is X doing once it gets there?" — it belongs on a workbench.

---

## Operational Feel

- **Investigation tool, not a NOC board.** This dashboard is opened from D00 when the Connectivity panel turns red, or when an operator is actively debugging a "why can't I reach app X?" problem.
- **Probe status first.** The most important information is which probes are passing and which are failing. This should be visible without scrolling.
- **History matters.** A probe that failed 10 minutes ago and recovered is less urgent than one failing right now. Sparklines or time-context on probe results help distinguish transient from persistent failures.
- **Throughput panels are secondary.** Network throughput and packet stats are "while I'm here" investigation panels — they answer "is something hammering the network?" but are not the primary reason this dashboard exists.

---

## Layout — Section by Section

### Section 1 — Probe Status (top)

**Question:** "Which specific connectivity check is failing?"

A full-width **table** of all Blackbox probes, sorted by status (failing first). This is the first thing visible when the dashboard opens.

| Column | Source | Purpose |
|--------|--------|---------|
| **Probe** | Label from Blackbox config | Human-readable name (e.g., "Public HTTPS", "Internal DNS", "Sonarr") |
| **Target** | `instance` label | The actual URL/host:port being probed |
| **Status** | `probe_success == 1/0` | Pass/fail, colored green/red |
| **Latency** | `probe_duration_seconds` | How long the probe took — slow is a warning even if passing |
| **Last checked** | Prometheus scrape timestamp | Staleness guard |

**Design decisions:**
- **Sorted failing-first.** When something is wrong, the failing probe is the first row. Healthy probes are visual confirmation that the problem is isolated.
- **All probe types in one table.** HTTP, DNS, TCP, TLS, ICMP probes are all rows in the same table. Splitting by probe type fragments the view and forces cross-table scanning.
- **Latency as a warning signal.** A probe that returns success but with 8s latency is not healthy. Color the latency cell yellow at 2s, red at 5s.

---

### Section 2 — Inter-VM Reachability Matrix

**Question:** "Can the right VMs reach each other?"

A table or stat grid showing ICMP or TCP probe results for key paths:

| From | To | Port | What it validates |
|------|----|------|-------------------|
| `monitoring` | `core` | TCP 80/443 | Monitoring can reach core's reverse proxy |
| `monitoring` | `media` | TCP 9100 | Prometheus can scrape media's node_exporter |
| `monitoring` | NAS | ICMP | NAS is reachable from monitoring VM |
| `media` | NAS | TCP 2049 | NFS port reachable — if this fails, NFS mounts will fail or hang |
| `media` | `core` | TCP 80 | Media containers can reach proxy for internal routing |

**Design decisions:**
- **Probes run from `monitoring` VM only.** Blackbox Exporter runs on `monitoring`. True bidirectional matrix probing would require a Blackbox instance on every VM — overkill for a homelab. The `monitoring` VM reaching a target is a reasonable proxy for "the network path exists."
- **TCP not ICMP where possible.** ICMP can be blocked by firewalls while the application port is open. A TCP probe to the actual service port is more representative.
- **NFS reachability vs mount health are different things.** TCP reachability to port 2049 confirms the NAS is online and the NFS service is running — if this fails, all mounts are broken or about to be. But a passing TCP probe does not confirm that individual mounts are healthy (they can be stale-mounted, permission-denied, or returning I/O errors). For individual mount health, the signal is: (a) node_exporter on the mounting VM stops reporting `node_filesystem_avail_bytes` for the NFS mountpoint, or (b) containers using the mount start logging I/O errors (surfaced via D02/Top Offenders). The TCP probe is a necessary but not sufficient condition.
**Known gap — Docker-internal DNS:** Blackbox probes run from the monitoring VM and test external/inter-VM DNS resolvers. They cannot test Docker's internal DNS (127.0.0.11) on other VMs. Docker DNS failures are one of the most common silent issues in self-hosted stacks — containers fail to resolve hostnames while the host VM resolves them fine. These failures surface as error log patterns on D02 ("Name or service not known", "Temporary failure in name resolution") rather than as probe failures on D03. If D02 shows DNS-related errors from a specific VM but all D03 DNS probes pass, the problem is likely Docker DNS on that VM, not network DNS.

---

### Section 3 — TLS Certificate Expiry

**Question:** "Are my certificates about to expire?"

A table or stat panel per domain showing days remaining until expiry.

| Domain | Days Remaining | Status |
|--------|---------------|--------|
| yourdomain.com | 47 | Green |
| internal.yourdomain.com | 12 | Yellow |

Thresholds: green at >14 days, yellow at 7–14 days, red at <7 days.

**Design decisions:**
- **Days, not a date.** "Expires 2026-03-05" requires mental arithmetic. "12 days" is immediately actionable.
- **Sourced from Blackbox TLS probe.** `probe_ssl_earliest_cert_expiry - time()` gives seconds to expiry. Divide by 86400 for days.
- **Separate from the probe status table.** Cert expiry is a warning about the future, not a current failure. A cert expiring in 10 days is yellow here but the HTTPS probe is still green. Mixing them would inflate the "failing probes" count.

---

### Section 4 — Per-Service Reverse Proxy Health

**Question:** "Is each app accessible through the proxy?"

One HTTP probe per service behind the reverse proxy (Sonarr, Radarr, Grafana, Jellyfin, etc.), shown as a stat grid. Each cell shows pass/fail and response time.

**Design decisions:**
- **Internal probes, not public.** These probes hit the proxy from inside the network (monitoring → core's internal address). This tests proxy routing without depending on external DNS or public internet access. If the internal probe passes but users can't reach it externally, the problem is upstream (DNS, firewall, ISP) — which Section 1's public HTTP probe will have already caught.
- **One probe per logical service, not per container.** The proxy routes by hostname/path, not by container. The probe validates the routing rule, not the container's internal health (D01 covers container health).
- **Separate from the main probe table.** Per-service probes are numerous enough to overwhelm Section 1. They warrant their own section with a cleaner visual (stat grid, not a table row per service).

---

### Section 5 — Network Throughput

**Question:** "What is using the network, and is anything saturating it?"

Two panels:

1. **Per-VM receive/transmit rate** — time series, one line per VM. Shows the historical bandwidth picture over the selected time range.
2. **Top container bandwidth consumers** — table ranked by current combined receive+transmit rate. Answers "which container is responsible for the traffic spike?"

**Sources:** `node_network_receive_bytes_total` / `node_network_transmit_bytes_total` (node_exporter, VM-level) and `container_network_receive_bytes_total` / `container_network_transmit_bytes_total` (cAdvisor, container-level).

**Design decisions:**
- **No thresholds on throughput.** There is no meaningful fleet-wide "this is too much" threshold. 200 MB/s on media during a Plex transcode is expected. 200 MB/s on core is suspicious. Throughput is contextual — show the data, let the operator judge.
- **Rate, not total bytes.** `rate(bytes_total[2m])` gives instantaneous throughput. Cumulative totals are meaningless without a baseline.

---

### Section 6 — Packet Errors and Drops

**Question:** "Is there low-level network corruption or congestion?"

Bar gauges or stat panels per VM showing:
- `rate(node_network_receive_errors_total[5m])`
- `rate(node_network_transmit_errors_total[5m])`
- `rate(node_network_receive_drop_total[5m])`
- `rate(node_network_transmit_drop_total[5m])`

**Design decisions:**
- **Normally all zero.** Healthy networks have no packet errors or drops. Any non-zero value is worth investigating — color thresholds: green at 0, red at any non-zero rate.
- **Separate from throughput.** High throughput with zero errors is fine. Low throughput with errors is a hardware/driver problem. They are independent signals.
- **VM-level only.** cAdvisor also exposes container-level packet stats (`container_network_receive_errors_total`), but container-level errors are almost always zero — errors happen at the physical/virtual interface level. Start with VM-level; add container-level only if a specific debugging need arises.

---

## Variables Exposed

D03 uses the same variable contract as other dashboards, plus Blackbox-specific filtering:

| Variable | Visible | Purpose |
|----------|---------|---------|
| `$datasource_prometheus` | yes | Prometheus datasource |
| `$node` | yes | Proxmox node filter |
| `$vm_role` | yes | Filters throughput/packet panels |
| `$host` | yes | Filters throughput/packet panels |

Probe panels do **not** filter by `$host`/`$vm_role` — probe targets are defined in Blackbox config, not derived from VM labels. The probe table always shows all probes regardless of variable selection.

---

## Drilldown Flow

D03 receives drilldowns from:
- D00 Connectivity panel (when any probe fails) — opens at current time range
- D01 when a VM shows network-related anomalies

D03 drills out to:
- D02 Log Workbench — when a service fails its proxy probe, check its logs
- D01 Infra Workbench — when throughput is high, check which container is responsible

---

## Click Flow Map

D03 supports Focus (narrowing by host in throughput panels) and Investigate (routing to D02 for service logs or D01 for host health).

| Panel / Element | Click Type | Target | Context Passed |
|----------------|-----------|--------|----------------|
| Probe Status → failing HTTP probe row | Investigate | D02 Log Workbench | `time`, `$service` (from probe label) |
| Probe Status → failing DNS probe | Informational | Stay on D03 | Investigation within D03's context |
| Probe Status → failing TCP probe (NAS) | Investigate | D01 Infra Workbench | `time`, `$host` (VM that mounts NAS) |
| Inter-VM Reachability → failing path | Investigate | D01 Infra Workbench | `time`, `$host` (target VM) |
| TLS Cert Expiry → domain | Informational | — | Operator investigates cert renewal process |
| Per-Service Proxy Health → failing service | Investigate | D02 Log Workbench | `time`, `$service` |
| Network Throughput → VM line | **Focus** | Same dashboard | Updates `$host`; container bandwidth table filters to that VM |
| Top Container Bandwidth → row | Investigate | D02 Log Workbench (or D01) | `time`, `$host`, `$service` |
| Packet Errors → VM with non-zero errors | Investigate | D01 Infra Workbench | `time`, `$host` |

**Focus behavior:** Clicking a VM line in the Network Throughput time series updates `$host`. The Top Container Bandwidth table (same section) now shows only containers from that VM, revealing which container is responsible for the traffic spike. From that narrowed table, clicking a container row drills to D02 for that service's logs.

---

## Uptime Kuma and Blackbox Exporter

Both tools run in the monitoring stack. They probe endpoints but serve different purposes:

- **Blackbox Exporter** provides Prometheus-native probe metrics that power D03's panels and D00's Connectivity stat. It is the data source for all probe-based dashboard panels. Probe targets are defined in Blackbox's config and Prometheus scrape configuration.

- **Uptime Kuma** provides a standalone status page (shareable with household members who don't use Grafana), a notification router (Discord, email, push on failure), and a web UI for managing probes without editing config files. It does NOT feed into Grafana dashboards.

Probe targets should be defined in both tools. Their purposes do not overlap: Blackbox feeds dashboards, Uptime Kuma feeds notifications and a human-readable status page.

If Uptime Kuma proves redundant over time (e.g., Grafana Alerting handles all notifications and no one uses the status page), it can be removed without affecting any dashboard. Blackbox Exporter is the required component for D03 and D00.

---

## Blackbox Exporter Configuration Notes

When adding Blackbox Exporter to the `monitoring` compose stack:

- **HTTP probes** should use `fail_if_ssl` appropriately and follow redirects.
- **DNS probes** for the internal resolver should validate a known internal A record, not just that the resolver responds — a resolver that returns NXDOMAIN for everything "works" but is misconfigured.
- **TLS probes** are separate from HTTP probes — an HTTP probe that follows redirects to HTTPS validates TLS implicitly, but only a dedicated TLS probe exposes `probe_ssl_earliest_cert_expiry`.
- **Probe interval** should match Prometheus scrape interval (typically 15s–30s). Very short intervals (5s) generate noise; very long intervals (5m) make failures slow to surface.
- **Label all probes** with a human-readable `probe_name` or equivalent label in the Prometheus scrape config so the probe table can show readable names instead of raw URLs.
