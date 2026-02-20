# Documentation Template (Chapter Structure)

Use this structure when suggesting or drafting documentation for the homelab journal. File names align with existing docs in `docs/` (e.g. `Chapter2-vms.md`, `Chapter2a-core.md`).

| File | Purpose |
|------|---------|
| **Chapter0** | Hardware overview—why the infrastructure choices form the foundation. |
| **Chapter1** | Proxmox setup: installing, creating templates, and preparing the environment. |
| **Chapter2** | VM overview—why each VM exists. Introduce universal sidecar concept briefly. |
| **Chapter2A** | Core VM apps—why chosen. Mention any Compose tweaks that are unique (tease lightly). |
| **Chapter2B** | Monitoring VM apps—why chosen. Mention universal sidecar (full details later). |
| **Chapter2C** | Media VM apps—why chosen. Briefly hint at any Compose labels explained later. |
| **Chapter2D** | Accelerated VM apps—why chosen. Any special Compose hints for context. |
| **Chapter3** | Compose strategy—structure, deployment, universal sidecar details (labels, etc.). Sample universal snippet. |
| **Chapter4** | Intro to UI configurations—links to VM-specific files. |
| **Chapter4A** | Core VM UI configuration steps post-deploy. |
| **Chapter4B** | Monitoring VM UI configuration steps post-deploy. |
| **Chapter4C** | Media VM UI configuration steps post-deploy. |
| **Chapter4D** | Accelerated VM UI configuration steps post-deploy. |
| **Chapter5** | Automation & helper scripts—further automation or helper tools for ease of use. |

When suggesting “next chapter,” use this order and point to the corresponding file in `docs/`.
