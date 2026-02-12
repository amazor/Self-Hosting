# Full Docs Review — All Chapters

Review date: 2025-02-13. Reviewer: docs-chapter-reviewer skill.  
**Update:** Suggestions have been applied in the chapter files (TOCs, links, new sections, apps, backup/recovery).

---

## Applied suggestions (summary)

| Item | Where | Status |
|------|--------|--------|
| TOCs | All 5 chapters | Done: after intro, before first `##` |
| Inter-document links | Ch 0, 1, 2, 2A, 2C | Done: chapter refs are links; 2B/2D marked *(planned)* |
| Prerequisites | Ch 2, 2A | Done |
| Scope | Ch 0 | Done: single Beelink + Synology |
| Recovery | Ch 0, Ch 1 | Done: NAS one-liner; Cloud-Init re-clone |
| Scope (Proxmox/Debian) | Ch 1 | Done |
| When to add a new VM | Ch 2 | Done: new section + TOC |
| App selection at-a-glance | Ch 2A | Done |
| Homepage/Homarr/Dashy, Bookstack/Nextcloud | Ch 2 | Done |
| Komodo/Portainer | Ch 2 | Done |
| Lidarr/Readarr, Overseerr/Jellyseerr | Ch 2C | Done |
| Backup and rebuild | Ch 2C | Done: new section |
| TL;DR Blueprint Breakdown | Ch 1 | Done |
| Image placeholder Ch 0 | Ch 0 | Done |

**Still optional:** Ch 0 hardware as table; Ch 1 bullet split; Ch 2A emoji in title; intra-doc anchors; Proxmox vzdump doc.

---

## Summary

The docs set is in good shape: clear voice, strong philosophy blocks, and consistent separation of concerns. **Top priorities:** (1) add a **table of contents** and **inter-document links** to every chapter; (2) turn chapter mentions into real links; (3) add a few missing prerequisites and verification steps; (4) consider new sections (backup/recovery, scope/versions) and optional apps/automations where they fit. Chapters 2B and 2D are referenced but not yet written—that’s intentional; keep references but you may add a short “(planned)” where it helps.

---

## Formatting & flow

- **Chapter 0:** Minor: “Step 0” mixes with “Hardware Overview”; consider `##` for “Step 0 – Boot Media” so the TOC is clear. The `[Image of network segmentation with VLANs]` is a placeholder—either add the image or replace with “*(Diagram: optional)*” so it’s obvious.
- **Chapter 1:** Long single paragraphs in “What are we configuring?” (bullet with QEMU/Log/Swap); splitting into one line per bullet would improve scanability. Code block at line 184: use proper backticks for inline `ssh` (`` `ssh` ``) so it doesn’t look like a stray quote.
- **Chapter 2:** Double `---` after “Short Hostnames” and before “VMID” (lines 42–45)—one is enough. Clone steps: the verification command is in a fenced block; ensure it’s tagged `bash`.
- **Chapter 2A:** No emoji in title; other chapters use emoji—optional consistency. “Reasoning Note” vs “Philosophy”—skill allows “Reasoning” as variant; fine to keep.
- **Chapter 2C:** Flow is good. “Pipeline flow” Mermaid is helpful. Optional: add `bash` to any shell blocks if you add verification commands later.

---

## Structure & sections

- **Chapter 0:** Add a short **Scope** or **Assumptions** line if you want (e.g. “This chapter assumes a single Beelink + Synology; multi-node or other NAS brands may differ.”). Optional: a one-sentence **Recovery** note (e.g. “If the NAS fails, see storage strategy in later chapters”) if you want to set expectations.
- **Chapter 1:** “Step 6 – Verification” is clear. Consider a one-line **Recovery** after “Convert to Template”: e.g. “If a clone fails Cloud-Init, delete the VM, re-clone from template, and re-add SSH key before boot.”
- **Chapter 2:** “The Practical Step” is well placed. Consider a short **When to add a new VM** bullet (e.g. “When a workload’s failure domain or resource profile no longer fits an existing VM”) to match the “Outgrow this split?” FAQ.
- **Chapter 2A:** Structure is good. The “What breaks if Core disappears” section is strong. No major restructure needed.
- **Chapter 2C:** Optional **Backup/recovery** subsection: “What to back up (compose, .env, *arr configs); what lives on NAS and survives VM rebuild.” Fits the “design for rebuild” theme.
- **Missing chapters:** 2B (monitoring), 2D (accelerated) are referenced in Chapter 2; reference.md lists them. Either add “(planned)” in the intro list or leave as-is until written.

---

## Simplification

- **Chapter 1:** “Line-by-Line Blueprint Breakdown” is dense but valuable; consider adding a one-line “TL;DR” at the top (e.g. “This section explains each part of the Cloud-Init snippet.”) so readers can skip if they only want the file.
- **Chapter 2:** VM-by-VM boundary rules are clear; no over-engineering.
- **Chapter 2C:** Optional services (Buildarr, SABnzbd, etc.) are well separated into layers; the “How Optional Services Stay Optional” section is concise. No change needed.

---

## Tables & structures

- **Chapter 0:** Hardware specs are list form; could be a small table (Component | Model/Spec | Role) for the host + NAS—optional, not required.
- **Chapter 2:** VM inventory and VMID table are good. “What Runs Where” tables are excellent. No changes needed.
- **Chapter 2A:** App selection is prose + tables; good. Consider a one-row “At a glance” table at the top of “App selection”: App | Role | Key choice (Caddy / Authentik / minimal DNS / whoami).
- **Chapter 2C:** Core Pipeline / Configuration Discipline / Enhancements tables are clear. Storage directory tree is good. No changes needed.

---

## Missing details

- **Prerequisites:** Chapter 2C already states “assumes Chapter 2 and Chapter 2A.” Chapter 2 could state at the top: “Prerequisites: Chapter 1 (template created).” Chapter 2A could state: “Prerequisites: Chapter 1 (template), Chapter 2 (VMID scheme).”
- **Verification:** Chapter 2 clone steps include a verification one-liner; good. Chapter 2A has “Boot and verify” with the same command; good. Chapter 2C: if you add a “First-time deploy” procedure later, add a one-line verify (e.g. “Check Sonarr/Radarr UI and qBittorrent behind VPN”).
- **Placeholders:** Chapter 0: `[Image of network segmentation with VLANs]` — replace or label as optional. Chapter 2A: “TODO (cert strategy)” and “Open question” are fine; keep.
- **Versions/assumptions:** Chapter 1 mentions Debian 13 and Proxmox; consider one sentence in Chapter 1 intro (e.g. “This guide was written for Proxmox 8.x and Debian 13 (Trixie).”) so future readers know the baseline.

---

## Philosophy blocks

- **Chapter 0, 1, 2, 2A, 2C:** Philosophy/Design Note/Tradeoff usage is consistent and well placed. No new blocks required; optional:
  - Chapter 2C “Why This VM Is Allowed to Break” already has a Tradeoff block; good.
  - Chapter 2A “Escape Hatch” for admin access is excellent; keep.

---

## Apps / services

- **Chapter 2 (apps VM):** Homepage is “TBD.” Options: **Homepage** (gethomepage.dev), **Homarr**, or **Dashy** — one sentence each. “(more later)” is fine; you could add “e.g. Bookstack, Nextcloud, or other self-hosted apps as needed.”
- **Chapter 2 (monitoring):** Komodo is listed; alternatives for “stack management UI”: **Portainer** (very common), **Yacht** — optional one-line mention.
- **Chapter 2A (DNS):** You chose “minimal DNS”; alternatives already mentioned (Pi-hole, AdGuard). If you later add ad-blocking, **AdGuard Home** or **Pi-hole** could live in core or a dedicated VM; no change needed now.
- **Chapter 2C:** *arr + qBittorrent + FlareSolverr + optional SABnzbd/Bazarr/ntfy are well covered. Optional: **Lidarr** (music) or **Readarr** (books) as “same pattern, different media type” in Enhancements or a one-line “Other *arrs” note.
- **Automation (media):** Buildarr/Recyclarr/Cleanuparr are already in the Configuration Discipline layer; good. Optional automation: **Overseerr** or **Jellyseerr** (request management in front of Sonarr/Radarr) as a future “Enhancements” or “User-facing” line.

---

## Links & table of contents

- **TOC:** ✅ Applied. All five chapters have a TOC after the intro, before the first `##`.
- **Intra-doc:** Chapter 2C has `[Media Stack Overview](#media-stack-overview-quick-reference)`; good. Chapter 2 could link “the table above” / “clone steps” with anchors. Chapter 1 could link “Step 3” / “Step 4” from later sections.
- **Inter-doc:** Turn all chapter mentions into links:
  - **Chapter 0:** “Move to Chapter 1” → `[Chapter 1: The Proxmox Foundation](Chapter1-proxmox.md)`.
  - **Chapter 1:** “see Chapter 0” → `[Chapter 0](Chapter0-hardware.md)`; “Template Maker” could link to Step 4.
  - **Chapter 2:** “Chapter 2A” … “Chapter 2D” → links to `Chapter2a-core.md`, and `Chapter2c-media.md`; 2B/2D don’t exist yet — use `Chapter2b-monitoring.md` and `Chapter2d-accelerated.md` as targets (or “(planned)” in link text). “Chapter 1” → `Chapter1-proxmox.md`. “Chapter 3” → either `#` or a placeholder `Chapter3-compose.md` if you create a stub.
  - **Chapter 2A:** Add “Prerequisites: [Chapter 1](Chapter1-proxmox.md) (template), [Chapter 2](Chapter2-vms.md) (VMID scheme).” Link “apps VM” to Chapter 2 or future 2-apps.
  - **Chapter 2C:** “Chapter 2” and “Chapter 2A” in intro → `[Chapter 2](Chapter2-vms.md)`, `[Chapter 2A (core)](Chapter2a-core.md)`.

Suggested TOC format (example for Chapter 2):

```markdown
## Table of contents
- [Introduction](#-introduction-defining-the-shape-of-the-lab)
- [VM Inventory (At a Glance)](#-vm-inventory-at-a-glance)
- [VMID Naming & Numbering Scheme](#vmid-naming--numbering-scheme-proxmox)
- [What Runs Where (Quick Reference)](#-what-runs-where-quick-reference)
- [VM-by-VM: The Boundary Rules](#vm-by-vm-the-boundary-rules-the-important-part)
- [Universal Sidecar Pattern](#a-small-preview-the-universal-sidecar-pattern)
- [The Practical Step: Spinning Up the VMs](#the-practical-step-spinning-up-the-vms-from-the-template)
- [FAQ](#-frequently-asked-questions)
```

(Anchors: lowercase, spaces → hyphens, emoji often stripped by renderers; when in doubt use slug without leading punctuation.)

---

## Best practices

- **Secrets:** No passwords in prose; .env and “set in environment” are referenced appropriately. Good.
- **Backups:** Chapter 2 mentions “Snapshot the fresh provisioned state”; Chapter 1 could mention “take a template snapshot before major changes.” Chapter 2C could add one sentence: “Back up compose and *arr configs (and optionally database) before major upgrades or rebuilds.”
- **Recovery:** Chapter 2A “What breaks…” and “Escape Hatch” cover recovery. Chapter 1: add one line after “Convert to Template” about re-cloning if Cloud-Init fails.
- **Single public VM:** Clearly stated in Chapter 2 and 2A. Good.
- **Data vs compute:** Chapter 2C storage design and “library on NAS” make the boundary clear. Good.

---

## New sections, apps, services, automations (suggested)

- **New sections (optional):**
  - **Chapter 0:** “Recovery / failure” one-liner (e.g. “If the NAS is unavailable, core services on the host still run; data access is restored when the NAS returns.”).
  - **Chapter 1:** “Scope” sentence (Proxmox 8.x, Debian 13); “Recovery” after template finalization.
  - **Chapter 2:** “When to add a new VM” (3–4 bullets).
  - **Chapter 2C:** “Backup and rebuild” (what to back up; what survives on NAS).
- **Apps/services to consider adding (short mentions):**
  - **Homepage / Homarr / Dashy** for the apps VM (Chapter 2).
  - **Overseerr / Jellyseerr** for request management (Chapter 2C enhancements).
  - **Lidarr / Readarr** as “same pattern” (Chapter 2C).
  - **Portainer** as alternative to Komodo (Chapter 2 monitoring).
- **Automations to consider:**
  - **Proxmox:** Backup job (vzdump) for VMs to NAS; optional hook for “pause before backup.”
  - **Media:** Already have Buildarr/Recyclarr/Cleanuparr; optional: **Notifiarr** or **Discord/Telegram** for notifications (you have ntfy).
  - **Core:** Wildcard cert via DNS challenge (already in 2A TODO).
  - **Monitoring (when 2B exists):** Node exporter, Docker metrics, and a single Grafana datasource per VM.

---

## Cross-chapter consistency

- **Terminology:** “Core VM,” “media VM,” “template,” “VMID,” “snippet” are consistent. Good.
- **VMIDs:** 110 core, 120 monitoring, 210 apps, 220 media, 230 accelerated — match across Chapter 2 and 2A/2C.
- **Duplication:** “Why a dedicated Media VM” in Chapter 2 and 2C is intentional (overview vs depth). No consolidation needed.
- **Missing chapters:** 2B, 2D, 3, 4, 5 are referenced or listed in README; linking to filenames that don’t exist yet is fine (links will work when you add those files).

---

*End of review. TOCs, inter-document links, and the suggestions above have been applied in the chapter files. See "Applied suggestions (summary)" at the top for a quick checklist.*
