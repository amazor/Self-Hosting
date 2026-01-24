# 🏗️ Chapter 0: The Physical Foundation

## 🖥️ Hardware Overview: The Homelab Core
Before diving into the software, we need to look at the "Silicon" that makes it all possible. For this lab, I’ve chosen a hardware stack that balances power, efficiency, and quiet operation—essential for a setup running in a family home.

### The Host: Beelink EQi13 Mini PC
* **CPU:** Intel Core i5-13500H (12 Cores / 16 Threads)
* **RAM:** 32 GB DDR4
* **Storage:** 500 GB NVMe SSD
* **Form Factor:** Ultra-compact, low-power Mini PC

> ### 🧠 Philosophy: The "Mini PC" Strategy
> In the past, homelabs required loud, power-hungry enterprise servers. I chose the Beelink EQi13 for several reasons:
> * **Efficiency is King:** Since this runs 24/7, performance-per-watt matters. The i5-13500H provides massive multi-core power while idling at very low wattage.
> * **Silence:** Unlike rack-mount servers, this is nearly silent, making it "spouse/family approved" for shared living spaces.
> * **Density:** 32GB of RAM is the sweet spot for a Proxmox host. It allows us to run multiple Docker VMs and experimental LXCs simultaneously without hitting a memory bottleneck.



---

### The Storage: Synology DS220+ NAS
While the Beelink handles the "brain" work (Compute), the Synology handles the "memory" (Storage).
* **Model:** DS220+
* **Role:** Main storage for media (Movies/TV), backups, and heavy database files.

> ### 🧠 Philosophy: Decoupling Compute from Storage
> I follow the philosophy of **Decoupling**. By keeping the data on a dedicated NAS (Synology) and the applications on the host (Beelink), I gain two things:
> 1.  **Data Safety:** If I accidentally delete my Proxmox host or it suffers a hardware failure, my family photos and media remain safe on the RAID-protected Synology.
> 2.  **Portability:** I can easily point a new VM at the NAS via NFS or SMB and have my data back online in minutes.

---

## Step 0 – Boot Media Preparation (Ventoy)

The very first step is getting our installer onto the hardware. Instead of the traditional method of "flashing" a single ISO to a USB, I use **Ventoy**.

### 🛰️ What is Ventoy?
Ventoy is an open-source tool to create bootable USB drives. With Ventoy, you don't need to format the disk over and over; you simply copy the ISO file to the USB drive and boot it.

> ### 🧠 Philosophy: The "Swiss Army Knife" USB
> In a homelab, you will constantly be testing new OSs. 
> * **Reflash-Free:** I can have Proxmox, Debian, Windows, and a Rescue Disk (like GParted) all on one 32GB thumb drive.
> * **Experimentation:** If I decide to move away from Proxmox, I just drag a new ISO onto the drive—no extra software required.



### 🛠️ Steps to Prepare
1.  **Download:** Get the latest version from [ventoy.net](https://www.ventoy.net).
2.  **Install:** Plug in your USB drive and run the Ventoy installer. 
3.  **Copy:** Drag and drop the **Proxmox VE ISO** directly onto the USB drive.

---

## 🔮 Future Roadmap: Physical & Network Expansion
A homelab is never "finished." While we are starting with a single node and a NAS, the physical and logical structure will evolve.

### 🏠 The Mini-Rack & 3D Printing
To keep the "Spouse Approval Factor" high, I plan to move this hardware into a dedicated small server rack.
* **Custom Mounts:** I will be using **3D Printing** to create custom 1U or 10-inch rack mounts for the Beelink and the Synology. This ensures clean cable management and optimal airflow.
* **Centralization:** Moving from a "pile of tech on a desk" to a rack-mounted system marks the transition from a hobby project to a reliable home utility.

### 🌐 Advanced Networking (VLANs)
As the lab grows, security becomes paramount. My future plans include adding a **Managed Switch** to implement **VLANs (Virtual Local Area Networks)**.

> ### 🧠 Philosophy: Network Segmentation
> Currently, everything lives on one flat network. In the future, I will segment the lab into distinct zones:
> * **Management VLAN:** For Proxmox Web UI, Synology Admin, and Switch management. Only my main PC will have access here.
> * **Data/IoT VLAN:** For Docker containers and smart devices that don't need to talk to the rest of my private network.
> * **Guest/Public VLAN:** For any services exposed to the internet (via Reverse Proxy), keeping them isolated from my personal data.



[Image of network segmentation with VLANs]


---

**Next Step:** Ready to plug this into the Beelink? Move to **Chapter 1: The Proxmox Foundation**.