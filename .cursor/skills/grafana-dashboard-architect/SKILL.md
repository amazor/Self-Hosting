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
D00: Homelab Overview          ← entry point (fleet health, triage)
 ├── D01: Infrastructure Workbench  ← host + container metrics deep dive
 ├── D02: Log Workbench             ← log exploration, error triage
 └── D03+: Domain Exceptions        ← only when generic workbenches fail
                                       (e.g., Media Pipeline)
```

### Ownership Rules

| Dashboard | Owns | Does NOT Own |
|-----------|------|--------------|
| **D00 Overview** | Fleet health: availability, error attribution (VM + service), change detection (rate direction, restarts), resource saturation summaries, data freshness | Per-container detail, log text, service-specific metrics, historical trend analysis, warning-level log counts |
| **D01 Infra Workbench** | Host metrics (CPU/RAM/disk/network), container resource consumption, restart tracking | Log content, application-level metrics |
| **D02 Log Workbench** | Log exploration, error/warn streams, log volume rates, pattern detection | Host resource metrics, container lifecycle |
| **D03+ Exceptions** | Domain-specific signals that don't fit generic workbenches | Anything the workbenches already cover |

### When to Create an Exception Dashboard

Only when ALL of these are true:

1. The domain has signals that are meaningless outside its context (e.g., queue depth, download rates, library size)
2. Those signals cannot be surfaced via a workbench variable filter
3. The target audience would otherwise need to mentally join 2+ workbenches

If in doubt, add a row to a workbench first.

---

## Source of Truth (Read Before Building)

Before designing panels or writing queries, read these files for the live state of what signals are available:

| File | What it tells you | Key functions / sections |
|------|-------------------|--------------------------|
| `docker_compose/monitoring/bootstrap.py` | Alloy label contract, Prometheus scrape targets, log normalization pipeline, Grafana datasource provisioning | `ensure_alloy_config()`, `ensure_prometheus_config()`, `ensure_grafana_provisioning()` |
| `docker_compose/monitoring/compose.yml` | Which metric/log containers exist, ports, network topology | Full file (short) |

The Alloy config in `ensure_alloy_config()` is the **canonical label contract** for logs. The Prometheus config in `ensure_prometheus_config()` mirrors the same contract for metrics via `metric_relabel_configs` on the cAdvisor job. If bootstrap.py and the tables below ever disagree, bootstrap.py wins.

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

- [Dashboard 00 — Homelab Overview](dashboard-00-overview.md)

Future specs (to be created when building each dashboard):

- Dashboard 01 — Infrastructure Workbench
- Dashboard 02 — Log Workbench
