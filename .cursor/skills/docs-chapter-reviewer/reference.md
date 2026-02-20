# Chapter Reviewer — Reference

Use this when the user wants a **detailed** pass or when you need the full checklist. The main [SKILL.md](SKILL.md) has the workflow; this file expands checklists and chapter roles.

---

## Chapter Roles (docs/ map)

| File | Purpose |
|------|---------|
| **Chapter0** | Hardware overview—why the infrastructure choices form the foundation. |
| **Chapter1** | Proxmox setup: installing, creating templates, and preparing the environment. |
| **Chapter2** | VM overview—why each VM exists. Introduce universal sidecar concept briefly. |
| **Chapter2a** | Core VM apps—why chosen. Mention any Compose tweaks that are unique. |
| **Chapter2b** | Monitoring VM apps—why chosen. Mention universal sidecar (full details later). |
| **Chapter2c** | Media VM apps—why chosen. Briefly hint at any Compose labels explained later. |
| **Chapter2d** | Accelerated VM apps—why chosen. Any special Compose hints for context. |
| **Chapter3** | Compose strategy—structure, deployment, universal sidecar details. |
| **Chapter4** | Intro to UI configurations—links to VM-specific files. |
| **Chapter4a–d** | Per-VM UI configuration steps post-deploy. |
| **Chapter5** | Automation & helper scripts. |

When suggesting new sections or moves, keep content within the intended chapter scope.

---

## Formatting and Style Checklist

- [ ] **Title:** Clear `# Chapter N: Topic — Subtitle` (or similar). Optional emoji; consistent with other chapters.
- [ ] **Intro:** States what the chapter covers and why it matters (for "future me" and readers).
- [ ] **Heading hierarchy:** `##` for major sections, `###` for subsections. No skip (e.g. no `####` after `##`).
- [ ] **Horizontal rules:** `---` between major blocks where it improves scanability; not after every subsection.
- [ ] **Philosophy blocks:** Blockquote format only; used for reasoning/design/why, not for generic notes.
- [ ] **Bold:** Key terms in tables and lists; not overused in paragraphs.
- [ ] **Code blocks:** Tagged (`bash`, `yaml`, `text`). Commands copy-paste safe (no inline placeholders that look like real tokens).
- [ ] **Cross-references:** By chapter name and file, as links (e.g. [Chapter 2A (core)](Chapter2a-core.md)).
- [ ] **Lists:** Numbered for procedures; bullets for parallel items. One idea per item.
- [ ] **Table of contents:** Present after intro, with links to all `##` (and optionally `###`) sections.
- [ ] **Intra-doc links:** Key cross-references within the file use `[text](#anchor)`.
- [ ] **Inter-doc links:** References to other chapters use relative path `ChapterN-topic.md` or `ChapterN-topic.md#section`.

---

## Table of Contents and Anchors

- **Placement:** TOC goes after the main title and introduction (and any opening philosophy block), before the first `##` section.
- **Heading levels:** Include every `##`; include `###` in the TOC only if the chapter is long (e.g. 4+ major sections with many subsections) and it improves navigation.
- **Anchor slug rules:** From a heading, derive the anchor by: lowercase, replace spaces with `-`, remove emoji and characters that aren't letters, numbers, or hyphens. Examples: `## VM Inventory (At a Glance)` → `#vm-inventory-at-a-glance`; headings with emoji may render as `#-section-name` on GitHub—prefer slug without leading hyphen when possible.
- **Format example:**
  ```markdown
  ## Table of contents
  - [Introduction](#introduction)
  - [VM Inventory](#vm-inventory-at-a-glance)
  - [What Runs Where](#what-runs-where-quick-reference)
  ```

---

## Automation and Deployment Best Practices

Use when reviewing any procedure or deployment section.

### General

- [ ] **Prerequisites** stated at the start (chapter, VM, role, credentials).
- [ ] **One recommended path** for the main audience; alternatives called out as "optional" or "advanced."
- [ ] **Verification** after critical steps (one command or UI check).
- [ ] **Recovery** mentioned or linked (e.g. snapshot before destructive step, "see Chapter X for restore").

### Security & secrets

- [ ] No real secrets in prose or committed examples (use `.env`, vault, or "set in environment").
- [ ] Exposure is explicit (what is public vs internal; reverse proxy vs direct port).

### Infrastructure

- [ ] **VM/data boundary** clear (what lives on NAS vs local disk; what survives a VM rebuild).
- [ ] **Single public VM** (core) pattern obvious where it applies.
- [ ] **Idempotency** where possible (Cloud-Init, declarative config, re-runnable scripts).

### Automation

- [ ] Scripts and snippets are **explained** (what they do, when to run them).
- [ ] **Failure modes** noted where relevant ("if this fails, check…").
- [ ] **Versions/assumptions** stated once (Proxmox/Debian/Docker version, network layout) if they affect steps.

---

## Philosophy Block Variants

Use the label that best fits:

| Label | When to use |
|-------|-------------|
| `### 🧠 Philosophy:` | Broad design principle or "why this approach." |
| `### 🧠 Design Note:` | Implementation detail that readers might wonder about (e.g. short hostnames). |
| `### 🧠 Tradeoff:` | Explicit tradeoff (e.g. "bigger blast radius, fewer failure modes"). |
| `### 🧠 Practical Constraint:` | Real-world limitation (e.g. "GPU passthrough is usually one VM"). |
| `### 🧠 Clarification:` | Preempting a misunderstanding ("But the homepage…" → explain why it’s elsewhere). |
| `### 🧠 Escape Hatch:` | How to recover or work around (e.g. admin access when core is down). |

Keep one idea per block; split if you’re explaining two separate decisions.
