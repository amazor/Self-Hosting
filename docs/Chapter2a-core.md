# Chapter 2A — Core VM (Purpose + App Selection)

## Introduction

**Prerequisites:** [Chapter 1](Chapter1-proxmox.md) (template created), [Chapter 2](Chapter2-vms.md) (VMID scheme).

The Core VM is the homelab’s “front door” and access foundation. It exists to make everything else reachable, consistently named, and safely gated.

When this VM is healthy, the rest of the system feels coherent. When it isn’t, the lab may still be running — but access becomes unreliable, inconsistent, or unsafe.

> ### 🧠 Reasoning Note: One Front Door, One Boundary
> The internet should see exactly one thing: the Core VM.
> Everything else stays private and is reachable only through controlled routing.
>
> This VM is intentionally designed to feel appliance-like: stable, predictable, and rarely changed.

> ### 🧠 Design Note: The Only Unauthenticated Surface
> I want exactly one service reachable without login: a simple personal homepage (links to GitHub/LinkedIn and optionally links to apps that still require authentication).
> Everything operational stays behind SSO.
> The details of what lives on that homepage belong in the `apps` VM.

---

## Table of contents
- [Provisioning the Core VM](#provisioning-the-core-vm-from-the-template)
- [What the Core VM is responsible for](#what-the-core-vm-is-responsible-for)
- [Why these services belong together](#why-these-services-belong-together)
- [Design constraints](#design-constraints)
- [App selection](#app-selection)
- [What breaks if the Core VM disappears](#what-breaks-if-the-core-vm-disappears)
- [FAQ](#faq)

---

## Provisioning the Core VM (From the Template)

The Core VM is the only VM that the internet touches. It deserves an intentional baseline.

**Full procedure:** The generic clone steps (template 9000, Cloud-Init, verify, snapshot) live in [Chapter 2 → Spinning Up the VMs](Chapter2-vms.md#the-practical-step-spinning-up-the-vms-from-the-template). Below are the values for **core** only.

**VMID + name**
- `110` → `core`

**Starting resources**
- **vCPU:** 2
- **RAM:** 4GB

### Steps (Proxmox)

1. Clone template `9000` → new VM `110 core`
2. Set CPU/RAM to 2 cores / 4GB
3. Confirm SSH key in Cloud-Init
4. Boot and verify:
  ```bash
   docker --version && systemctl status qemu-guest-agent --no-pager && free -h
  ```
5. Snapshot: core - fresh provisioned 

## What the Core VM is responsible for

The Core VM concentrates three primitives that everything else depends on:

- **Ingress + HTTPS**  
  Terminate TLS and route requests to internal services across VMs.

- **Identity / SSO**  
  Central authentication so downstream apps don’t become their own security islands.

- **Internal naming (DNS)**  
  Stable hostnames for VMs/services so the system doesn’t depend on static IPs.

---

## Why these services belong together

This VM has a deliberately large blast radius because it owns shared infrastructure:

- If **ingress** is down, most services are unreachable from the outside.
- If **identity** is down, access control becomes inconsistent or collapses into per-app logins.
- If **DNS** is down, service discovery and callback flows fail in confusing ways.

Keeping these together makes the system easier to reason about: there is one place to debug “access is broken,” and one place where security posture is defined.


> ### 🧠 Tradeoff: Bigger Blast Radius, Fewer Failure Modes
> Concentrating the access plane means `core` matters more — on purpose.
> It keeps the lab’s security posture and reachability understandable and recoverable.

---

## Design constraints

- **Only VM exposed publicly:** ports 80/443 terminate here, nowhere else.
- **Port forwarding is narrow by design:** only ports 80/443 are forwarded to `core`. The reverse proxy terminates TLS, upgrades HTTP → HTTPS, and forwards internally based on the requested hostname.
- **Stable by intent:** changes are infrequent and deliberate.
- **Recoverable:** restore should be straightforward (backup/snapshot + minimal state).
- **Security-first defaults:** consistent HTTPS and sane headers everywhere.
- **No “nice-to-have” apps:** dashboards/homepages live in `apps`, not here.

> ### 🧠 Escape Hatch: Admin Access Should Not Depend on Core
> If `core` is down, I still need a way to fix things.
> Admin access is handled out-of-band (e.g., Tailscale to the host).

---

## App selection

| App | Role | Key choice |
|-----|------|------------|
| Caddy | Reverse proxy | HTTPS + routing; first-class Let's Encrypt |
| Authentik | Identity / SSO | One login across many apps |
| Minimal DNS | Internal naming | Stable hostnames; low churn |
| whoami | Troubleshooting | Echo endpoint for access-plane validation |

---

### Reverse proxy: Caddy

**Why this belongs in `core`**  
The reverse proxy is the lab’s front door: it owns HTTPS termination and routing. Centralizing it avoids per-VM exposure and keeps access policy consistent.

**Alternatives considered**
- **Traefik:** powerful middleware ecosystem and great Docker integration, but its main advantage (label-driven auto-discovery) doesn’t apply cleanly when each VM is its own Docker island.
- **Vanilla Nginx:** extremely stable and explicit, but typically requires a separate certificate automation flow (more moving parts in a VM that should stay boring).

**Why Caddy won**
- **Fits multi-VM routing naturally:** explicit upstreams without needing Docker socket access across VMs.
- **First-class Let’s Encrypt:** public certs are automated without adding a separate certificate-management stack.
- **Simple mental model:** file-driven routing is easy to explain, back up, and restore.

**Tradeoffs accepted**
- Adding a new externally reachable service usually means touching proxy config in `core`. That friction is acceptable here because it reinforces deliberate change.

> **TODO (cert strategy):** current setup uses HTTP challenge. DNS challenge (e.g., wildcard certs) may become the preferred path later because it makes adding new subdomains/services easier and less brittle.

---

### Identity / SSO: Authentik

**Why this belongs in `core`**  
Identity is a shared primitive. Without central SSO, access control fragments and every app becomes its own credential and permission model.

**Alternatives considered**
- **Authelia:** simpler and lighter, excellent for “protect behind proxy + MFA,” but it’s less of a full identity platform if the lab grows into richer SSO flows.
- **Per-app accounts:** workable at small scale, but quickly becomes inconsistent and difficult to manage safely.

**Why Authentik won**
- **Strong fit for “login once, access many”:** an identity layer that supports consistent enforcement across many apps.
- **UI-driven administration is fine:** user and policy changes are rare events in a small lab.
- **Supports clean audience separation:** a single domain with subdomains can express different audiences (guest homepage vs authenticated apps vs admin-only endpoints).

**Initial stance on MFA**
MFA isn’t required on day one. The first win is central identity and consistent enforcement; hardening can tighten later without rewriting the access model.

> **Open question:** start with “gate in front” (proxy-level auth) for consistency and simplicity, then revisit per-app OIDC where it provides better UX or permissions. This tradeoff is worth deciding deliberately because it shapes how identity is enforced across the lab.

---

### Internal naming: minimal DNS for the homelab subnet

**Why this belongs in `core`**  
Most VMs use DHCP. DNS provides stable names without committing to static IP management, and it makes routing more durable.

**Alternatives considered**
- **“Just use Avahi/mDNS”:** convenient as a fallback, but multicast discovery is harder to reason about and less reliable as the lab grows.
- **Pi-hole / AdGuard:** useful, but heavier than necessary for a DNS role that should stay boring.

**Why “minimal DNS” won**
- **Small scope:** local records for the homelab subnet, forward everything else upstream.
- **Low churn:** DNS should be predictable and rarely touched.

> ### 🧠 Design Intent: DNS Should Stay Boring
> The goal is stable naming, not turning DNS into an ongoing project.

> ### 🧠 Practical Note: DNS + DHCP Is a Tradeoff
> Static IPs (or DHCP reservations) are still the most durable foundation for internal DNS.
> For now, the lab relies on DHCP typically reassigning the same IPs to VMs, but this is not a perfect guarantee.
>
> A more stable future improvement is having DNS/DHCP integrate so records update automatically when leases change (router/DHCP integration or a dynamic approach).

---

### Troubleshooting endpoint: whoami / echo service

**Why this belongs in `core`**  
A tiny echo endpoint is a practical way to validate the front door:
- external reachability,
- TLS behavior,
- routing correctness,
- security headers.

It’s also a clean monitoring target for “the access plane is up” without depending on any workload VM.

**Alternatives considered**
- “Just test with an app endpoint” — but that couples availability checks to whatever app happens to be up.

**Why a dedicated echo service won**
- Small, deterministic, and purpose-built for debugging and health checks.

---

## What breaks if the Core VM disappears

**Immediately affected**
- Public access to anything behind the reverse proxy
- TLS termination and certificate automation
- SSO flows and centralized access enforcement
- Internal hostname resolution (for homelab hostnames)

**Should remain intact**
- Workload data stored on other VMs/NAS
- VM operation via Proxmox
- Direct admin access paths that don’t depend on `core`

---

## FAQ

**Q: Why is `core` the only VM exposed to the internet?**  
*A:* It concentrates the attack surface into one hardened entry point and avoids accidental exposure of internal services. Everything else stays private and reachable only through controlled routing.*

**Q: Why not run a reverse proxy on each VM?**  
*A:* Multiple entry points multiply security mistakes and create drift. A single front door keeps access policy consistent and debugging simpler.*

**Q: Why Caddy instead of Traefik?**  
*A:* Traefik shines when it can auto-discover containers via labels. With each VM being its own Docker island, that advantage doesn’t apply cleanly. Caddy keeps routing centralized and explicit while still providing automated public certificates.*

**Q: Why not host the homepage in `core` if it’s the “front door”?**  
*A:* The access plane and the user experience are different concerns. Anything expected to evolve lives outside `core` so the VM that everything depends on stays stable.*

**Q: What’s the biggest risk to manage in `core`?**  
*A:* Accidental exposure. A mistake here can affect the entire lab, which is why this VM has strict scope and conservative change discipline.*

**Q: What happens if `core` breaks — how do I still access the lab?**  
*A:* I treat `core` as the “front door,” not the only way in. If the reverse proxy / DNS / SSO stack goes down, public access will be impacted — but I can still recover through out-of-band paths:*
- *Proxmox UI/console access to reach VMs directly (even if DNS/routing is broken).*
- *SSH by IP from the local LAN as a baseline fallback.*
- *Avahi/mDNS hostnames as a convenience fallback for local access when DNS is down.*
- *Tailscale to the host for admin access that does not depend on `core` being healthy.*


