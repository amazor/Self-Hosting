# Dashboard 04 — Media Pipeline

Planning reference for the media automation and streaming dashboard.

> **Status:** Planning / not yet built. Build after the media stack is instrumented with Prometheus-compatible exporters. Most signals in this dashboard require new components — see Signal Sources below.

---

## Purpose

Answers the question: **"Is the media pipeline working, and what is it doing?"**

This is the domain workbench for the media VM. It covers the full lifecycle of media automation: content discovery via indexers (Prowlarr), download queue management (Sonarr/Radarr), actual downloading (qBittorrent/SABnzbd), and final delivery via streaming (Jellyfin/Plex). Each stage can fail independently — a healthy Jellyfin with a broken Sonarr queue is an operational failure that D00 and D01a/D01b won't surface without domain-specific signals.

This dashboard only makes sense when the media stack is deployed and instrumented. Don't build it before the exporters are running.

---

## Ownership

### What D04 Owns

- Sonarr/Radarr/Prowlarr queue depth and activity
- Indexer health and search success rates
- Download client activity, speeds, and ratios
- Jellyfin/Plex active sessions, transcoding type and count
- Library statistics: total items, recently added, missing content
- NAS storage available for media volumes (historical trend)

### What D04 Does NOT Own

- Infrastructure metrics for the media VM (CPU/memory/disk I/O) (→ D01a/D01b)
- Log text from arr services (→ D02)
- Network connectivity or NFS mount reachability (→ D03)
- Hardware health of the server (→ D05)

---

## Main Insights This Dashboard Gives You

1. **Is anything in the queue?** A healthy pipeline is usually idle. Queue depth spiking for a specific show or movie tells you automation is working; a queue that grows without shrinking tells you downloads are stalled.
2. **Are the indexers healthy?** Prowlarr aggregates all indexers. Indexer failures are silent from the outside — Sonarr fails to grab releases, but to the user it looks like "nothing available." Indexer health panels make this failure surface immediately.
3. **Is the download client working?** Speed near zero when queue is non-empty = stalled download. This is the most common media pipeline failure mode.
4. **Who is watching, and is the server struggling to serve them?** Jellyfin/Plex active sessions tell you how many streams are open. Transcoding sessions are more expensive than direct streams — a high transcode count is the leading indicator for CPU pressure on the media VM.
5. **Is the NAS filling up?** All media lives on the NAS. A full NAS silently stops completed downloads from moving to their final location. The media VM's NFS-mounted filesystem metrics already expose this with no new tooling.
6. **Is the pipeline blocked somewhere?** Mapping queue depth across Sonarr → qBittorrent → Jellyfin shows where in the chain content is stuck.

---

## Signal Sources

**Most signals here require new exporters.** This is the key constraint for this dashboard.

| Signal | Source component | Status | Notes |
|--------|-----------------|--------|-------|
| Sonarr/Radarr queue, library, missing | **exportarr** | Not yet deployed | One exporter instance per arr service; add to `media` compose stack |
| Prowlarr indexer health | **exportarr** (Prowlarr mode) | Not yet deployed | Exportarr supports Prowlarr since v0.6 |
| qBittorrent download speed, ratio | **qbittorrent-exporter** | Not yet deployed | Multiple community exporters available |
| Jellyfin sessions, transcoding | **Jellyfin metrics plugin** or **jellystat** | Not yet deployed | Jellyfin has a native `/metrics` endpoint if enabled; jellystat provides more detail |
| NAS storage (media mount) | `node_filesystem_avail_bytes{fstype=~"nfs\|nfs4"}` on media VM | **Available today** | No new tooling — media VM mounts NAS and node_exporter already exports this |
| Container resource usage | cAdvisor (existing) | **Available today** | CPU/memory for each arr container already scraped |

### Recommended Exporters to Add to the Media Stack

**exportarr** (`ghcr.io/onedr0p/exportarr:latest`)
- One container per arr service: Sonarr, Radarr, Prowlarr
- Exposes: queue depth, total series/movies, wanted/missing count, indexer status, calendar activity
- Scrape port: typically 9707–9710 (one per instance)
- Prometheus job label should follow the standard contract: `service=sonarr`, `service=radarr`, etc.

**qbittorrent-exporter** (e.g., `esanchezm/prometheus-qbittorrent-exporter`)
- Exposes: downloading/seeding/paused torrent counts, total download/upload speeds, free space on download client's disk
- Single instance, scrape port 8000 (configurable)

**Jellyfin native `/metrics`**
- Requires enabling the metrics plugin or the `EnablePrometheusMetrics=true` config option
- Exposes: active sessions, transcoding sessions, library counts, item plays
- Alternative: **jellystat** (external stats tracker with Postgres backend) — more historical data but heavier stack

---

## Layout — Section Ideas

### Section 1 — Pipeline Health Summary (top)

**Question:** "Is the pipeline flowing or blocked?"

A row of stat panels giving an at-a-glance status of each stage:

| Panel | Metric | Green condition |
|-------|--------|----------------|
| **Sonarr Queue** | `exportarr_queue_total{service="sonarr"}` | 0 (idle) or declining |
| **Radarr Queue** | `exportarr_queue_total{service="radarr"}` | 0 (idle) or declining |
| **Indexers Up** | `exportarr_indexer_status{status="passing"}` count | All passing |
| **Active Downloads** | qBittorrent downloading count | Declining or 0 |
| **Download Speed** | qBittorrent current DL speed | Non-zero when queue is non-empty |
| **Jellyfin Sessions** | Active session count | Low; no threshold — informational |

This row is the "is anything wrong in the pipeline?" quick check. Link each stat to the relevant section below.

---

### Section 2 — Arr Queue Depth Over Time

**Question:** "Is content flowing through the pipeline or piling up?"

Time series showing queue depth for Sonarr and Radarr over the time range. A healthy chart shows queue briefly spiking (new season/movie added) then returning to 0. A chart that plateaus or grows continuously indicates stalled processing.

```promql
exportarr_queue_total{host=~"$host", service=~"sonarr|radarr"}
```

Pair this with the download speed panel — a queue + download speed panel side by side immediately shows whether the queue is moving.

**Also useful:** Queue breakdown by status (downloading, completed, failed). Exportarr exposes queue status as label values. A growing `failed` queue means items are queued but not downloading.

---

### Section 3 — Indexer Health

**Question:** "Can Sonarr/Radarr actually find anything to download?"

A table of all configured indexers with their current status. Indexer failures are the most common silent failure in the arr stack.

```promql
# Indexer status from Prowlarr exportarr
exportarr_indexer_status{host=~"$host"}
```

| Column | Source |
|--------|--------|
| **Indexer** | `indexer` label |
| **Status** | `exportarr_indexer_status` — pass/fail |
| **Last checked** | Prometheus scrape timestamp |

Color: green = passing, red = failing, gray = no data. Even one failing indexer is worth surfacing — if it's the primary indexer for a niche genre, content for that genre silently stops being grabbed.

---

### Section 4 — Download Client Activity

**Question:** "What is being downloaded right now?"

Two panels:

1. **Download speed time series** — receive rate from qBittorrent exporter. Shows bandwidth usage over time.
   ```promql
   qbittorrent_global_download_speed_bytes{host=~"$host"}
   ```

2. **Torrent state summary** — stat panels showing counts: downloading, seeding, paused, errored.
   ```promql
   qbittorrent_torrents_count{host=~"$host", state="downloading"}
   qbittorrent_torrents_count{host=~"$host", state="seeding"}
   qbittorrent_torrents_count{host=~"$host", state="error"}
   ```

**Seeding ratio awareness:** A high seeding count with zero downloading is normal and healthy (ratio maintenance). A high `error` count means failed downloads that need manual intervention — color this red.

---

### Section 4b — Usenet (SABnzbd)

**Question:** "Is the Usenet half of the pipeline flowing, and is my provider actually delivering?"

Only populated when the media stack has `ENABLE_SABNZBD=1`. Metrics come from `sabnzbd-exporter` (onedr0p/exportarr, port 9707), which ships inside `compose.sabnzbd.yml` — **not** the exporters overlay, since Scraparr is *arr-only and never exports download clients.

Three panels:

1. **Usenet Download Speed** — Usenet is not peer-limited, so this should sit near line rate whenever the queue is non-empty.
   ```promql
   sabnzbd_speed_bps{host=~"$host"}
   sabnzbd_speed_limit_bps{host=~"$host"} > 0
   ```

2. **Usenet Queue** — queue length + warnings + remaining bytes (own axis, `bytes` unit).
   ```promql
   sabnzbd_queue_length{host=~"$host"}
   sabnzbd_queue_warnings{host=~"$host"}
   sabnzbd_remaining_bytes{host=~"$host"}
   ```

3. **Article Success Rate per Server** — the one that matters.
   ```promql
   clamp_max(
     increase(sabnzbd_server_articles_success{host=~"$host"}[$__rate_interval])
     /
     clamp_min(increase(sabnzbd_server_articles_total{host=~"$host"}[$__rate_interval]), 1),
     1
   )
   ```

**Why article success rate is the key Usenet signal (and has no torrent equivalent):** a torrent stalls because nobody is seeding — an availability problem you can see in the swarm. Usenet stalls because *articles are missing* (DMCA takedowns, incomplete posts), which is invisible from the queue alone: speed just quietly drops while par2 repair grinds. A primary server drifting below ~100% is the signal to add a fill/block provider on a different backbone (`USENET_SERVER2_*`), which by design is only asked for what the primary could not deliver. `clamp_min(..., 1)` guards the divide-by-zero when no articles were requested in the window; `clamp_max(..., 1)` keeps counter-reset artifacts from spiking above 100%.

**Diagnostic pairing:** zero speed + non-empty queue + falling article success = provider problem, *not* a "no seeders" problem. That distinction is the whole reason to run both protocols.

---

### Section 5 — Jellyfin/Plex Streaming Sessions

**Question:** "Who is watching, and is the server handling it?"

Two panels:

1. **Active sessions** — total count of active playback sessions. Stat with sparkline.
2. **Transcoding vs. Direct sessions** — split bar or two stats. "Direct Play" is free; "Transcoding" costs CPU.

```promql
# Total active sessions
jellyfin_active_sessions_total{host=~"$host"}

# Transcoding specifically
jellyfin_transcoding_sessions_total{host=~"$host"}
```

**Why transcoding matters:** Every transcoding session typically costs 1–4 CPU cores depending on resolution and codec. On a VM without GPU transcoding, 3 simultaneous 4K→1080p transcodes will saturate the media VM's CPU. This panel is the bridge between "Jellyfin works" and "D01a shows CPU saturation" — it provides the domain reason for the resource spike.

**GPU transcoding (future — D05/accelerated VM):** If hardware transcoding is enabled (Intel Quick Sync via the accelerated VM), transcoding cost drops to near-zero. A `hw_transcode` vs `sw_transcode` label split would show whether HW acceleration is actually being used for each session.

---

### Section 6 — Library Statistics

**Question:** "How big is the library and is it growing?"

Stats showing the size and completeness of the media library.

```promql
# Total items in library
exportarr_series_total{host=~"$host", service="sonarr"}
exportarr_movie_total{host=~"$host", service="radarr"}

# Wanted/missing items — content Sonarr/Radarr knows about but hasn't found yet
exportarr_wanted_missing_total{host=~"$host", service="sonarr"}
exportarr_wanted_missing_total{host=~"$host", service="radarr"}
```

A growing "missing" count with a healthy pipeline means content simply isn't available on indexers yet — normal. A growing missing count alongside indexer failures means content IS available but can't be found.

---

### Section 7 — NAS Storage Available

**Question:** "Is the NAS filling up?"

Time series showing available bytes on the NAS NFS mount over time. This is the "when will I run out?" panel.

```promql
# Available bytes on media NFS mount — no new tooling needed
node_filesystem_avail_bytes{
  host=~"$host",
  fstype=~"nfs|nfs4",
  mountpoint=~"/mnt/nas.*|/media.*"
}
```

Threshold: yellow at 20% free, red at 10% free. The time series (not just current %) is the value here — it shows the consumption trajectory. A straight declining line projected forward gives a rough "full by" date.

This panel is the historical complement to the D00 Disk bargauge, which shows only current usage.

---

## Variables Exposed

| Variable | Visible | Multi | Notes |
|----------|---------|-------|-------|
| `$datasource_prometheus` | yes | no | |
| `$node` | yes | yes | |
| `$vm_role` | yes | no | Effectively fixed to `media` for most panels; shown for completeness |
| `$host` | yes | yes | Defaults to media VM host |

`$service` is not exposed as a variable here. The arr services are fixed, not dynamically discovered. Panels are labeled by service name in their titles, not via variable filtering.

---

## Drilldown Flow

**Receives from:**
- D00 if a media-related service appears in Top Offenders (usually from D00 → D04 manually, since D00 doesn't link here by default)
- Manual navigation when investigating a "why isn't X downloading?" question

**Drills out to:**
- D01a — when media VM CPU/memory is high due to transcoding load
- D02 — when an indexer failure is suspected, check Sonarr/Radarr logs for grab failures
- D03 — when indexer reachability seems to be a network issue

---

## Click Flow Map

D04 is a domain workbench. Most clicks are depth 1 — routing to D02 for logs, D01a for host resource investigation, or D01b for container attribution. The Pipeline Health Summary stats (Section 1) also act as in-page navigation to the relevant detail section below.

| Panel / Element | Click Type | Target | Context Passed |
|----------------|-----------|--------|----------------|
| Pipeline Summary → Sonarr Queue stat | Scroll/Focus | Section 2 (Queue Depth detail) | — |
| Pipeline Summary → Radarr Queue stat | Scroll/Focus | Section 2 (Queue Depth detail) | — |
| Pipeline Summary → Indexers Up stat | Scroll/Focus | Section 3 (Indexer Health table) | — |
| Pipeline Summary → Active Downloads stat | Scroll/Focus | Section 4 (Download Client detail) | — |
| Pipeline Summary → Jellyfin Sessions stat | Scroll/Focus | Section 5 (Streaming Sessions detail) | — |
| Queue Depth time series → spike | Investigate | D02 Log Workbench | `time`, `$service` (sonarr/radarr) |
| Indexer Health → failing indexer row | Investigate | D02 Log Workbench | `time`, `service=prowlarr` |
| Download Speed → stall (speed=0, queue>0) | Investigate | D02 Log Workbench | `time`, `service=qbittorrent` |
| Jellyfin Sessions → high transcode count | Investigate | D01a Host Workbench | `time`, `$host` (media VM CPU) |
| Library Stats → high missing + failing indexers | Investigate | Section 3 (correlation) | — |
| NAS Storage → low available space | Investigate | D01a Host Workbench | `time`, `$host` (Disk section) |

**In-page navigation pattern:** Section 1's stat panels serve as both health indicators and table-of-contents for the sections below. Clicking a stat scrolls to its detail section. This makes the top row function as a routing surface within the dashboard, similar to how D00 routes across dashboards.

---

## Build Prerequisites Checklist

Before building this dashboard, confirm:

- [ ] **exportarr** deployed in media compose stack, one instance per arr service
- [ ] **exportarr scrape targets** added to Prometheus `SCRAPE_TARGETS` config, with correct `service=sonarr` etc. labels
- [ ] **qbittorrent-exporter** deployed and scraped
- [ ] **Jellyfin metrics** enabled (plugin or config) and scraped
- [ ] Verify metrics arrive in Prometheus: run `exportarr_queue_total` in the Prometheus UI

Do not attempt to build this dashboard with placeholder data — the value is entirely in the live pipeline signals.
