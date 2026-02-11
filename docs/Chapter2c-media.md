# Chapter 2C – Media VM (220)

## Introduction

If the Core VM protects identity, routing, and stability — the Media VM runs automation.

This VM is not infrastructure in the traditional sense. It is a pipeline.

Content is tracked. Indexers are queried. Files are downloaded. Media is imported. Torrents are seeded. Notifications are sent. And occasionally, something breaks.

That is expected.

This chapter is not about building the “perfect media server.” It is about building a media automation system that:

- Contains operational noise
- Preserves seeding correctly
- Exposes only what is necessary
- Can be rebuilt without drama

Two principles guide this VM:

> **Understand the complexity — then contain it.**  
> **Design for rebuild, not for perfection.**

We will not chase cleverness for its own sake.  
We will build something correct, understandable, and isolated.

---

## Why a Dedicated Media VM?

Media automation behaves differently than the rest of the homelab:

- Torrent clients maintain many active connections.
- Indexers fail unpredictably.
- Disk I/O fluctuates.
- External APIs change.
- Queues stall.

This is fundamentally different from:

- Reverse proxy
- DNS
- SSO
- Monitoring

Those services must remain stable.

The Media VM is isolated because:

- It is expected to change.
- It is allowed to be noisy.
- It may need to be rebuilt.
- It interacts with less predictable external systems.

If something here breaks, public access and authentication must remain intact.

That boundary is intentional.

---

## What Lives in the Media VM

This VM contains the automation engine.

### Core Stack (Minimum Viable Pipeline)

- **Sonarr** – TV automation  
- **Radarr** – Movie automation  
- **Prowlarr** – Indexer management  
- **VPN container** – Required for torrent routing  
- **qBittorrent** – Torrent client  

This is enough to:

1. Track wanted content  
2. Query indexers  
3. Download via torrent  
4. Import into the media library  
5. Continue seeding  

### Why qBittorrent?

I previously used Deluge and ran into reliability issues.  
qBittorrent integrates cleanly with the *arr ecosystem and is widely adopted.

This guide standardizes on qBittorrent.

---

## VPN Enforcement

Torrent traffic must not exit directly to the internet.

In this setup:

- I use **ExpressVPN**
- Any VPN provider can work
- Torrent traffic is routed through a VPN container
- The torrent container attaches via `network_mode: service:vpn` (or equivalent pattern)

Bootstrap validates that torrent traffic is routed through the VPN and warns if it is not.

The enforcement is architectural — not vendor-specific.

Torrenting without a VPN is strongly discouraged.

---

## Storage Design

Media storage is structured around a single root directory:

/mnt/media

This directory represents the entire media workspace.

Inside it, we separate responsibilities clearly:

```text
/mnt/media/
├── torrents/        # Torrent client workspace (blackhole + active torrents)
├── downloads/       # In-progress downloads (torrent or usenet)
├── library/         # Organized media library
│   ├── movies/
│   ├── tv/
│   └── anime/
```

### Directory Roles

**torrents/**  
Used by the torrent client. This can include a “blackhole” folder for manually dropped `.torrent` files and the active torrent working directory.

**downloads/**  
Temporary staging area for in-progress downloads.  
If using Usenet later, this may be subdivided (e.g., `downloads/torrent/` and `downloads/usenet/`), but the structure remains contained under the same root.

**library/**  
Final organized media.  
This is what Plex (or any media server) consumes.

Inside `library/`, content is separated by media type:

- `movies/`
- `tv/`
- `anime/`

These are the folders shared with Plex.

---

### Why Mount the Entire `media/` Directory?

The entire `/mnt/media` directory is mounted as a single filesystem (local disk or NFS).

This is important.

Downloads and library must exist on the same underlying filesystem so that:

- Files can be hardlinked instead of copied  
- Torrent seeding continues uninterrupted  
- No duplicate media files are created  
- No unnecessary write amplification occurs  

By mounting the entire `media/` directory together, we:

- Keep storage layout simple  
- Preserve seeding correctness  
- Avoid cross-filesystem copy operations  
- Contain all media-related state in one predictable location  

---

### Local vs NFS

The `.env.example` defaults to local storage for simplicity.

In my setup, `/mnt/media` is an NFS mount backed by HDD storage.

Both approaches work — as long as the entire `media/` directory is mounted together.

The structure remains the same.  
Only the backing storage changes.

## Optional Enhancements (Phased)

The Media VM evolves in stages.

### Phase 1 – Working Pipeline

Torrent-based automation only.

Learn the system:

- How indexers behave  
- How queues fill  
- How imports work  
- How seeding behaves  

Keep it simple.

---

### Phase 2 – Declarative Configuration & Hygiene

Once the system stabilizes, configuration drift appears.

At this stage:

- **Buildarr**
- **Recyclarr**
- **Cleanuparr**

Buildarr and Recyclarr are grouped because they serve the same purpose:  
reducing configuration drift.

Cleanuparr improves operational hygiene.

These tools are discipline layers — not requirements.

---

### Phase 3 – Enhancements

- **SABnzbd** (Usenet support)  
- **Bazarr** (Subtitles)  
- **ntfy** (Download-finished notifications)  

Usenet introduces additional complexity and cost.  
Bazarr improves quality of life.  
ntfy is intentionally minimal — alerting belongs in Monitoring.

---

## How Optional Services Stay Optional

The goal is simplicity without hiding structure.

- Base `compose.yml`
- Overlay files per app:
  - `compose.buildarr-recyclarr.yml`
  - `compose.cleanuparr.yml`
  - `compose.sabnzbd.yml`
  - `compose.bazarr.yml`
  - `compose.ntfy.yml`

`.env` controls which modules are enabled.

Bootstrap reads `.env` and selects overlays accordingly.

Shell functions hide repetitive Compose flags — but the structure remains visible in the repository.

The complexity is contained, not abstracted away.

---

## Access Model

Only the Core VM receives public traffic (80/443).

Media applications are reverse-proxied through Core.

Each application receives its own hostname.

---

### UI Hostnames (SSO Protected)

- `sonarr.domain`
- `radarr.domain`
- `prowlarr.domain`
- `bazarr.domain` (if enabled)
- `sabnzbd.domain` (if enabled)

These are protected by SSO.

---

### API Hostnames (No SSO)

To support nzb360 and similar mobile apps:

- `sonarr-api.domain`
- `radarr-api.domain`
- `prowlarr-api.domain`

These endpoints:

- Use HTTPS
- Require API keys
- Are rate-limited at the reverse proxy

---

### qBittorrent Exception

`qbittorrent.domain`

This endpoint is not behind SSO to support nzb360.

It is the highest-risk exposed surface in this VM.

Mitigations:

- Strong username/password
- HTTPS only
- Stricter rate limits than other APIs
- VPN-routed torrent traffic

This is a deliberate compromise.

---

## Important Security Note: Public APIs Are a Compromise

Exposing APIs publicly is not ideal.

Even with:

- HTTPS
- API keys
- Rate limiting

Risks remain.

This exposure exists for convenience.

In the future:

- API access will migrate to Tailscale for the small trusted group.
- Public API hostnames will be closed.
- qBittorrent will no longer be publicly accessible.

This is a staged security posture — not the final one.

---

## Why This VM Is Allowed to Break

Automation state is reproducible.

Libraries are external.

Configuration lives in files.

Optional tools are modular.

If the Media VM becomes unstable:

- It can be rebuilt.
- The media library remains intact.
- Seeding can resume.
- The rest of the infrastructure remains unaffected.

The Media VM is dynamic by design.

---

## FAQ

### Why use hardlinks instead of simple copy/move?

Because proper torrent seeding requires it.

If downloads and library share the same filesystem, hardlinks allow the file to be organized into the library without duplicating data, while the torrent client continues seeding.

Without this, imports would require copying or moving the file, which either duplicates storage or interrupts seeding.

---

### Can hardlinks work over NFS?

Yes — as long as downloads and library are on the same underlying filesystem within the same mount/export.

Hardlinks do not work across different filesystems.

---

### Why expose public APIs at all?

To support mobile apps like nzb360 without requiring every user to configure Tailscale.

It is a tradeoff between convenience and strict isolation.

Mitigations exist — but it remains a compromise.

---

### Why not keep the torrent client local-only?

Remote visibility and control are practical needs.

Monitoring progress, managing stalled torrents, and interacting via mobile apps are legitimate use cases.

The exposure is intentional and documented — not accidental.

---

### What happens if an API key leaks?

An attacker could control the associated application.

Mitigations:

- Rotate the key
- Disable the exposed hostname
- Audit logs
- Migrate to Tailscale access

This is another reason public API exposure is treated as temporary.

---

This concludes the Media VM design.

The next chapter implements the Compose structure and bootstrap mechanics that bring this architecture to life.
