# 🛰️ The Homelab Journey: From Bare Metal to Production

Welcome to my Homelab documentation! This repository serves as a living "field manual" and a functional **Source of Truth** for my home infrastructure. It is designed to be part **journal**, part **technical guide**, and part **Infrastructure-as-Code** repository.



---

## 🎯 The Mission
The goal of this project is to build a robust, scalable, and automated home server environment that handles everything from media streaming and file storage to home automation and development tools.

### My Guiding Principles:
* **Cattle, Not Pets:** Everything is automated. If a VM fails, I don't "fix" it; I redeploy a fresh one in seconds.
* **Decoupled Architecture:** Keeping "Compute" (Proxmox) separate from "Data" (Synology) for maximum safety.
* **Documentation-First:** Every choice has a "Philosophy" note explaining the *why*, ensuring reproducibility.
* **Infrastructure as Code (IaC):** Version controlling my configurations so that my lab is predictable and recoverable.

---

## 🖥️ The Tech Stack
* **Hypervisor:** Proxmox VE (Type-1 Hypervisor)
* **Compute Host:** Beelink EQi13 (Intel i5-13500H, 32GB RAM, 500GB NVMe)
* **Storage (NAS):** Synology DS220+
* **OS:** Debian 13 (Trixie)
* **Automation:** Cloud-Init & Bash Scripting

---

## 🗺️ Repository Structure & Workflow

This repository is managed on a local workstation and pushed to GitHub. It is organized to separate the physical/hypervisor setup from the applications running on top.

```text
.
├── documentation/          # Chronological journey (Chapters 0, 1, 2...)
├── proxmox/                # Hypervisor-level automation
│   ├── scripts/            # Shell scripts (Template creation, post-install)
│   └── snippets/           # Cloud-Init blueprints (common-config.yaml)
├── docker/                 # Containerized applications
│   ├── infrastructure/     # Core services (Reverse Proxy, DNS, etc.)
│   ├── media/              # Entertainment stack (Plex, Arrs)
│   └── monitoring/         # Dashboard, Grafana, Prometheus
├── 3d-printing/            # STL/STEP files for custom rack mounts
└── README.md               # Project landing page

## 🎯 The Mission
The goal of this project is to build a robust, scalable, and automated home server environment that handles everything from media streaming and file storage to home automation and self-hosted development tools.

### My Guiding Principles:
* **Cattle, Not Pets:** Everything should be automated. If a VM fails, I don't fix it; I redeploy it in seconds.
* **Decoupled Architecture:** Keeping "Compute" (Proxmox) separate from "Data" (Synology) for maximum safety and portability.
* **Document Everything:** Every choice has a "Philosophy" note explaining *why* it was made, ensuring I (and others) can replicate the success later.
* **Security First:** Moving toward a Zero-Trust model with VLAN segmentation and key-based authentication.

---

## 🏗️ The Tech Stack
* **Hypervisor:** Proxmox VE (Type-1 Hypervisor)
* **Compute:** Beelink EQi13 (Intel i5-13500H, 32GB RAM)
* **Storage:** Synology DS220+ NAS
* **Automation:** Cloud-Init, Bash Scripting, and Docker Compose
* **OS:** Debian 13 (Trixie)

---

## 📖 Roadmap: The Chapters
I have organized this journey into chronological chapters. Each chapter contains the **Reasoning**, the **Configuration**, and the **Code** required to complete that phase.

### [Chapter 0: The Physical Foundation](docs/Chapter0-hardware.md)
*Hardware selection, the "Mini-PC" strategy, and preparing boot media with Ventoy.*

### [Chapter 1: The Proxmox Foundation](docs/Chapter1-proxmox.md)
*Installing the Hypervisor, post-install optimizations, and creating a "Golden Image" VM Template using Cloud-Init.*

### ⏳ Coming Soon...
* **Chapter 2:** The Docker Compose Workflow & First Services.
* **Chapter 3:** Networking & Storage (Connecting the Synology via NFS/SMB).
* **Chapter 4:** Security, VLANs, and Reverse Proxies.

---

## 🛠️ How to Use This Repo
1.  **Read the Chapters:** If you are starting your own journey, start with [Chapter 0](docs/Chapter0-hardware.md).
2.  **Use the Snippets:** The `snippets/` folder contains the actual YAML and Shell scripts used in the guides.
3.  **Check the Philosophy Notes:** Look for the 🧠 icon throughout the documentation to understand the logic behind specific technical choices.

---

## 🔮 The Future
This lab is a work in progress. Future expansions include:
* **3D Printed Rack:** Moving from the desk to a custom-printed 10-inch server rack.
* **Managed Networking:** Implementing an Omada or UniFi switch for full VLAN segmentation.
* **High Availability:** Potentially adding a second Beelink node for a Proxmox cluster.

---

> "A homelab is never finished; it just reaches a stable state before the next upgrade."
