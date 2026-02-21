# Dashboard 02 — Log Workbench

Planning reference for the log exploration and error investigation dashboard.

> **Status:** Planning / not yet built. This file captures intent, signal inventory, and panel ideas. Refine into a full spec before building.

---

## Purpose

Answers the question: **"What is this service actually saying?"**

This is the log investigation surface. When D00's Top Offenders table routes you to a specific service with errors, this dashboard opens with that context pre-loaded. It shows the actual log lines, their volume over time, the top recurring patterns, and the rate at which errors are accumulating.

It is the terminal node of the triage chain: Overview → Host/Container Workbench → **Log Workbench**. Once here, the operator is reading evidence, not navigating further.

---

## Ownership

### What D02 Owns

- Log line streams — actual log text, filterable by host, service, and level
- Log volume rate — how many log lines per minute/second, by service and level
- Error rate time series — errors/min over time per service (trend and direction)
- Top error patterns — most frequent error messages (ranked by occurrence)
- Log level distribution — breakdown of info/warn/error/fatal for selected scope
- Log-based change detection — "service started" / "service stopped" events appearing in logs

### What D02 Does NOT Own

- Host resource metrics (→ D01a Host Workbench)
- Container lifecycle state — whether it's running (→ D00/D01b)
- Network connectivity or probe results (→ D03)
- Application-level queue depth or session counts (→ D04)

---

## Main Insights This Dashboard Gives You

1. **What are the actual errors?** Log lines tell you what the application is experiencing — connection refused, disk full, auth failure. No metric can substitute for the actual message.
2. **Is this a spike or a steady drip?** Log volume rate shows whether errors started suddenly (deployment, dependency failure) or have been accumulating for days (slow degradation).
3. **Is one error dominating, or many?** Top error patterns table shows if 95% of errors are the same message vs. a flood of different failures.
4. **When did the problem start?** The error rate time series gives a precise timestamp for the onset — more useful than relying on memory or alert timestamps.
5. **Is a restart causing log churn?** Log-based start/stop events ("started successfully", "caught signal 15") tell you whether restarts are contributing to error counts vs. a running container that is failing internally.
6. **Is this log pipeline even working?** Log volume rate near zero when services are clearly running is itself a signal: Alloy may have stopped shipping, or the label normalization pipeline has a bug.

---

## Signal Sources

All signals come from Loki. No Prometheus queries needed in this dashboard.

Logs arrive via Alloy agents on each VM, using the label contract defined in `bootstrap.py`'s `ensure_alloy_config()`.

| Available label | Meaning | Use in queries |
|----------------|---------|----------------|
| `host` | VM hostname | Primary filter |
| `vm_role` | VM's functional role | Coarse filter when investigating a whole role |
| `service` | Logical service identity | Per-service log streams |
| `container` | Container instance name | Disambiguates same service on multiple hosts |
| `level` | Normalized: `trace`, `debug`, `info`, `warn`, `error`, `fatal`, `unknown` | Error filtering |
| `stream` | `stdout` / `stderr` | Useful for separating application output from sidecar noise |
| `source` | Log origin type (`docker`) | Metadata; mostly constant |

---

## Layout — Section Ideas

### Section 1 — Log Volume Rate (top)

**Question:** "Is the log volume normal? Is there a spike?"

A time series showing log lines per minute, split by level. This is the first context panel — it answers "is something unusual happening in the log stream at all?" before the operator starts reading log lines.

```logql
# Total log rate per level for selected scope
sum by (level) (
  rate({host=~"$host", vm_role=~"$vm_role", service=~"$service"}[$__interval])
)
```

- Error/fatal colored red; warn colored yellow; info/debug colored blue/gray
- A sudden spike in error lines at a specific timestamp is the first thing to correlate with other signals
- Flat near-zero for info/debug with normal error rate is expected and healthy

**Design decisions:**
- Keep this panel narrow (top 6–8 rows). It's context, not the main event.
- Do NOT add thresholds here — log volume is highly service-dependent. 1000 lines/min from Jellyfin is different from 1000 lines/min from Alloy.

---

### Section 2 — Error Rate Time Series

**Question:** "When did errors start, and are they still increasing?"

A time series showing `errors + fatal` per minute, grouped by service. This is the ranked version of the log volume panel — it surfaces which services are generating errors and whether the rate is growing or shrinking.

```logql
# Error rate per service over time
sum by (service) (
  rate({host=~"$host", vm_role=~"$vm_role", level=~"error|fatal"}[$__interval])
)
```

This pairs with the Top Offenders table on D00: D00 tells you the current count, D02 tells you the history and trend.

**Interactivity (Focus):** Each service line in the time series is clickable. Click a service line → updates `$service` to that service via a self-link. All sections below (patterns, log stream, level distribution) now show only that service's data. This is the primary focus mechanism on D02: the operator sees "sonarr errors spiking at 14:32" and clicks the sonarr line to narrow the entire dashboard to sonarr.

---

### Section 3 — Top Error Patterns

**Question:** "What are the most common error messages?"

A table showing the most frequent error log messages in the selected time window. This answers "is one error dominating (single root cause) or many errors (widespread failure)?"

```logql
# Top 20 most frequent error lines, normalized (strip timestamps/IDs)
topk(20,
  sum by (message) (
    count_over_time({host=~"$host", service=~"$service", level=~"error|fatal"} | logfmt | __error__="" [$__range])
  )
)
```

**Practical note:** Log parsing quality determines how useful this is. If logs are unstructured (no JSON or logfmt), Loki's `pattern` parser or `regexp` extractor may be needed to normalize messages before grouping. Highly variable messages (those with request IDs, timestamps, or hex addresses embedded) will fragment into many unique rows instead of clustering.

**Alternative approach — Loki log pattern detection:** Grafana 10+ includes experimental pattern detection that clusters similar log lines automatically. Worth enabling if log parsing is messy.

**Common failure patterns in self-hosted Docker environments:** When reading error clusters in this table, certain patterns point to specific root causes outside the application. Recognizing these avoids chasing application-level fixes for infrastructure problems:

| Error pattern in logs | Actual root cause | Where to investigate |
|----------------------|-------------------|---------------------|
| `Name or service not known` / `Temporary failure in name resolution` | Docker internal DNS (127.0.0.11) failed | Container DNS config; Docker daemon health; may be transient — check if pattern is intermittent or sustained |
| `no space left on device` | Disk or inode exhaustion | D01a Disk section — check both filesystem % AND inode % (they are independent failure modes) |
| `Killed` / `signal 9` / `OOM` | Process was OOM-killed by the kernel | D01a Memory section + OOM counter; check Mem Limit % in D01b Container Resource Table |
| `permission denied` / `operation not permitted` | File permission mismatch (PUID/PGID) | Container user config vs mount ownership; common after NFS mount changes |
| `connection refused` on an internal port | Dependency container not running or wrong Docker network | D00 Container deficit; D01b Container Resource Table for the dependency's status |
| `i/o timeout` / `context deadline exceeded` | NFS mount stale or network partition | D03 NAS reachability (TCP 2049 probe); D01a NFS mount metrics and I/O latency |
| `too many open files` | File descriptor limit reached | D01a Host section; container ulimits in compose.yml |
| `certificate verify failed` / `SSL: CERTIFICATE_VERIFY_FAILED` | Expired cert or clock drift causing validation failure | D03 TLS cert expiry; D01a NTP time offset |

**Interactivity:** Click a row in the top error patterns table → filters the Log Stream panel (Section 4) to show only lines matching that pattern. Implementation: data link to same dashboard with a line filter query parameter appended to the Loki query.

---

### Section 4 — Log Stream

**Question:** "What exactly did this service log?"

The primary panel — an actual log viewer showing log lines from Loki. Full width. This is where investigation ends: reading the actual messages.

```logql
# Error and fatal lines for selected scope
{host=~"$host", vm_role=~"$vm_role", service=~"$service", level=~"$level"}
```

**Design decisions:**
- Default `$level` to `error|fatal` when arriving from a D00/D01b drilldown with `level=error`. When the operator wants to see context around an error, they can switch `$level` to `All` or `info|warn|error`.
- Show the `service` label in the log panel's displayed fields — when `$service = All`, it disambiguates which service each log line comes from.
- Enable log line wrapping — container logs can be long JSON blobs.
- Add a "Live" mode toggle for tail-following when actively debugging a running service.

---

### Section 5 — Log Level Distribution

**Question:** "What fraction of this service's logs are errors vs. noise?"

A pie chart or horizontal bar showing the distribution of log levels for the selected scope and time range.

```logql
# Count per level
sum by (level) (
  count_over_time({host=~"$host", service=~"$service"}[$__range])
)
```

This is a "while I'm here" panel rather than a primary signal. It's useful for profiling services that are unknown: a service that is 80% `warn` level is behaving differently from one that is 80% `info`. It also helps calibrate whether `$level=All` in the stream panel will be overwhelmingly noisy.

---

## Variables Exposed

| Variable | Visible | Multi | Notes |
|----------|---------|-------|-------|
| `$datasource_loki` | yes | no | |
| `$node` | yes | yes | |
| `$vm_role` | yes | yes | Coarse filter |
| `$host` | yes | yes | Cascades from `$vm_role` |
| `$service` | yes | yes | Cascades from `$host`. Primary investigation axis. |
| `$level` | yes | yes | Custom variable: `error\|fatal`, `warn\|error\|fatal`, `All`. Defaults to `error\|fatal`. |

**`$level` as a custom variable:** Unlike the other variables (which are `label_values()` queries), `$level` is a fixed custom list. Log levels are well-known; enumerating them from Loki is unnecessary and would surface `unknown` as an equal option to `error`.

**Note on `$service` default:** When arriving via drilldown from D00 Top Offenders or D01b Container Resource Table, `$service` arrives pre-set. When opened directly, it defaults to All — which is intentional for exploration but produces a high-volume log stream. The Log Volume Rate panel (Section 1) helps the operator quickly identify which service to narrow down to.

---

## Drilldown Flow

**Receives from:**
- D00 Fleet Pulse Error Rate — arrives with `$host`, `$vm_role`, `level=error`
- D00 Top Offenders table row — arrives with `$host`, `$service`, `level=error` (most specific)
- D01b Container Resource Table row — arrives with `$host`, `$service`
- D03 when a service fails its proxy probe — arrives with `$service`

**Drills out to:**
- D01a Host Workbench — when log errors suggest resource exhaustion (e.g., "no space left on device", "OOM")

**This dashboard is typically the end of the chain.** After reading log lines and identifying the root cause, the operator acts directly (SSH, restart, config change) rather than opening another dashboard.

---

## Log Pipeline Assumptions

This dashboard assumes the Alloy label contract from `bootstrap.py` is in place:

1. **`level` is normalized.** Alloy's pipeline in `ensure_alloy_config()` maps application-specific log levels (`ERROR`, `WARN`, `error`, `warning`, etc.) to the canonical set (`error`, `warn`, `info`, etc.). Queries using `level=~"error|fatal"` assume this normalization is working. If a new container emits levels in an unexpected format, errors will land in `unknown` and appear to be missing.

2. **`service` is reliable.** The `service` label is set from the container name via Alloy's `discovery.docker` component, falling back to compose project name. Containers with generic names (e.g., `app-1`) may produce unusable `service` labels. This is a labeling problem to fix at the Alloy config level, not the dashboard level.

3. **All containers are being scraped.** Alloy collects from the Docker socket on each VM. Containers that start after Alloy needs a restart, or containers in non-default Docker networks that Alloy cannot discover, will be absent from Loki entirely. The Data Freshness panel on D00 is the early warning for this failure mode.

---

## Click Flow Map

D02 is typically the terminal node of the triage chain. Most clicks are Focus actions (narrowing the view on the same dashboard). Cross-dashboard links go back to D01a when a log message reveals an infrastructure root cause.

| Panel / Element | Click Type | Target | Context Passed |
|----------------|-----------|--------|----------------|
| Error Rate per Service → service line | **Focus** | Same dashboard | Updates `$service`; all panels filter to that service |
| Log Volume Rate → level area | **Focus** | Same dashboard | Updates `$level` |
| Top Error Patterns → pattern row | **Focus** | Same dashboard | Filters Log Stream to matching lines |
| Log Stream → "no space left on device" | Investigate | D01a Host Workbench | `time`, `$host` (Disk section) |
| Log Stream → OOM/signal 9 message | Investigate | D01a Host Workbench | `time`, `$host` (Memory section) |
| Log Stream → "i/o timeout" on NFS | Investigate | D03 Network/Connectivity | `time` (NAS reachability) |
| Log Level Distribution → level segment | **Focus** | Same dashboard | Updates `$level` |

**Focus flow on D02:** The typical investigation starts broad and narrows:
1. Error Rate per Service shows multiple services → click one → `$service` narrows
2. Top Error Patterns now shows only that service's errors → click a pattern → log stream filters to matching lines
3. Log Stream shows the actual evidence → operator reads and acts

**Returning to D01a:** When a log message reveals an infrastructure problem (out of disk, OOM, I/O timeout), a data link on the Log Stream panel routes back to D01a with context. This is the only cross-dashboard drilldown on D02 and it goes backward in the triage chain — from evidence back to the infrastructure that caused it.

---

## Anti-Patterns to Avoid

1. **Showing all log lines from all services by default.** The default `$level = error|fatal` is not a restriction — it's a deliberate starting point. Showing ALL logs by default is overwhelming and makes the dashboard feel slow to load.
2. **Parsing log structure in the dashboard query instead of at ingest.** Heavy regex/pattern extraction in every LogQL query is expensive. If a service's logs benefit from parsing, add a structured extraction stage to Alloy's pipeline — then dashboards get clean fields for free.
3. **Log volume rate as a threshold panel.** There is no meaningful threshold for "normal" log volume. Different services have vastly different verbosity. Use the rate for trend detection (is this higher than usual?) not for absolute alarming.
4. **Duplicating D01a resource panels here.** If an operator wants to check memory while looking at logs, they open D01a. Don't embed memory graphs on the log workbench — it conflates investigation contexts and makes the dashboard heavier.
