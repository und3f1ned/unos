# UNOS Installation Guide

This guide provides detailed instructions for installing UNOS on your system.

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Hardware Requirements](#hardware-requirements)
3. [Automatic Installation](#automatic-installation)
4. [Manual Installation](#manual-installation)
5. [Post-Installation Setup](#post-installation-setup)
6. [HTTPS and the Certificate Warning](#https-and-the-certificate-warning)
7. [Network Discovery](#network-discovery)
8. [Troubleshooting](#troubleshooting)
9. [Upgrading](#upgrading)
10. [Uninstalling](#uninstalling)

---

## Prerequisites

### Supported Operating Systems

| OS | Version | Status |
|----|---------|--------|
| Debian | 11 (Bullseye) | ⚠️ Supported, but Windows does not list the NAS: no `wsdd2` package (see [Network Discovery](#network-discovery)) |
| Debian | 12 (Bookworm) | ✅ Fully Supported |
| Debian | 13 (Trixie) | ✅ Fully Supported (the OS of the development device) |
| Ubuntu | 22.04 LTS | ✅ Fully Supported |
| Ubuntu | 24.04 LTS | ✅ Fully Supported |
| Raspberry Pi OS | 64-bit | ✅ Fully Supported |
| Armbian | Latest | ⚠️ Community Supported |

Docker: `install.sh` installs Docker Engine with the Compose plugin from
Docker's repository when Docker is missing; on RISCV64, which Docker's
repository does not serve, it installs the distribution's `docker.io`,
`docker-buildx` and `docker-compose` (Debian 13) or `docker-compose-v2`
(Ubuntu 24.04). When Docker is already installed,
it needs Docker Compose v2 (`docker compose version`): the packages
`docker-compose-plugin` from Docker's repository, `docker-compose-v2` on
Ubuntu, or `docker-compose` on Debian 13. Debian 12's `docker-compose` (1.29)
is not enough; the script then stops before it changes anything.

### Supported Architectures

- **AMD64** (x86_64) - Intel/AMD processors
- **ARM64** (AArch64) - Raspberry Pi 4/5, ARM SBCs; see
  [Banana Pi BPI-W3](#banana-pi-bpi-w3-rockchip-rk3588-arm64)
- **RISCV64** - Debian 13 or Ubuntu 24.04 only; see
  [Banana Pi BPI-F3](#banana-pi-bpi-f3-spacemit-k1-riscv64)
- **ARMv7** - Limited support, may have performance issues; the web UI's image
  comes from another machine: see [ARMv7](#armv7)

Each release of UNOS, a git tag `v<x.y.z>`, publishes its images for AMD64,
ARM64 and RISCV64 on GHCR (`ghcr.io/und3f1ned/unos-supervisor` and
`unos-webui`, built by `.github/workflows/release.yml`, ADR-0455, ADR-0458):
`install.sh` and `update.sh` pull them, and nothing is built on the host.
ARMv7 has no release images (ADR-0458): there the scripts build the supervisor
from the release's checkout, and **System → Updates** cannot install releases.

### ARMv7

The web UI's build tools (Vite's Rolldown and Lightning CSS) publish no build
for ARMv7 with musl, the libc of the web UI's builder (ADR-0456), so on an
ARMv7 host `install.sh` and `update.sh` build only the supervisor and start
the web UI from the image `deploy-webui:latest`. Make it for each version on a
machine with Docker that runs linux/arm/v7 images (Docker Desktop does;
elsewhere `docker run --privileged --rm tonistiigi/binfmt --install arm`), in
the root of a checkout of the same release tag, and load it on the host (the
web UI's files are built in that machine's own platform, and only the image's
Caddy stage is ARMv7's):

```bash
docker buildx build --platform linux/arm/v7 -t deploy-webui:latest --load webui
docker save -o deploy-webui.tar deploy-webui:latest
scp deploy-webui.tar <the host>:
```

and on the host `sudo docker load -i deploy-webui.tar`. On a fresh host
Docker comes with `install.sh`: its first run sets up the host, then stops at
the build and prints these commands; load the image and run it again. Before
`update.sh`, load the image of the new version; when an update is rolled
back, load it again.

The supervisor's build on an ARMv7 host has not been checked: cffi and
greenlet, among others, publish no ARMv7 wheel, and their sources need
libffi's headers and a C++ compiler, which the supervisor's image does not
install, so the build probably stops there (ADR-0458).

### Banana Pi BPI-F3 (SpacemiT K1, RISCV64)

Checked on Armbian 26.8 (Debian 13) with its `current-spacemit` kernel 6.18 on
a BPI-F3 with 4 GB of RAM and the system on a microSD card (ADR-0370, ADR-0372,
ADR-0456).

- **Both images come from the release** (ADR-0458; not yet checked on the
  board, which waits for the first release with RISCV64 images).
  `install.sh` and `update.sh` pull the release's RISCV64 images, as on AMD64
  and ARM64, and build nothing on the board: the web UI's build tools have no
  RISCV64 build and Node.js crashes on the K1 in the middle of one
  (nodejs/node#66845), and the supervisor's Python packages that publish no
  RISCV64 wheel (cryptography, bcrypt, cffi, psutil, uvloop, PyYAML) took about
  35 minutes to compile on the board; the release's wheels are built on GitHub
  instead. **System → Updates** installs releases there too. Keep Docker's data
  on a disk other than a microSD card where you can, and make Docker wait for
  that disk: with a drop-in `/etc/systemd/system/docker.service.d/data-root.conf`
  that says `[Unit]` and `RequiresMountsFor=<the data root>`, a disk missing at
  boot keeps Docker down instead of letting it start on an empty data root on
  the card.
- **Disks for pools.** An NVMe drive in the M.2 slot, SATA disks behind an
  M.2-to-SATA adapter there (a JMB582, which the kernel drives as AHCI, ran two
  SATA SSDs with TRIM) or USB SSDs. Their pools get TRIM as on every host
  (ADR-0429): `mkfs.btrfs` trims an SSD when the pool is made, btrfs then
  discards freed space as it goes (`discard=async`, its default on an SSD from
  kernel 6.2), and the weekly `fstrim.timer` trims the pool. A Kingston A1000
  (Phison controller) on the slot's PCIe Gen2 x2 link ran an `fstrim` of all its
  free space, a 4 GiB write beside an `fstrim`, `discard=async` and a warm
  reboot without an NVMe timeout, reset or error in the kernel's log.
- **NVMe power saving stays off.** `install.sh` adds the udev rule
  `60-unos-k1-nvme.rules`, which turns off an NVMe drive's power saving (APST)
  and its PCIe link's ASPM L1 whenever it appears: without it, a Patriot P300
  (MAXIO MAP1202) hung under load ("QID 0 timeout, disable controller"). Do not
  add `pcie_aspm=off` or `nvme_core.default_ps_max_latency_us=0` to the kernel's
  command line instead: the board does not boot with them. Replace a drive that
  still hangs in the slot under load, on a TRIM or after a reboot: the same P300
  also hung on a TRIM of the whole drive, and after a warm reboot its controller
  did not come back ("Device not ready; aborting initialisation, CSTS=0x0"),
  even after 30 seconds without power, since the K1's PCIe driver does not
  switch the slot's power, while it worked in a USB enclosure.
- **Apps.** Most images of the App Store publish no RISCV64 build, and their
  installation fails when Docker pulls them.
- **Temperature.** The dashboard shows the CPU's first cluster
  (`cluster0_thermal`).

#### Home Assistant and Frigate beside UNOS on a BPI-F3

The App Store cannot install the K1 builds of Home Assistant and Frigate, whose
images [bpi-f3-frigate-ha](https://github.com/und3f1ned/bpi-f3-frigate-ha)
builds on the board. The owner's BPI-F3 runs them beside UNOS by UNOS's rules
for apps (ADR-0372):

- The pool `data`: a btrfs RAID1 that UNOS made on two SATA SSDs behind a
  JMB582 M.2-to-SATA adapter.
- Docker's data root on the pool: `{"data-root": "/mnt/pools/data/.docker"}` in
  `/etc/docker/daemon.json`, a btrfs subvolume whose name starts with a dot, so
  that the Storage page does not list it as a volume, and
  `RequiresMountsFor=/mnt/pools/data/.docker` in a drop-in of `docker.service`.
  Without the pool neither Docker nor UNOS's web UI starts; the microSD card
  holds the system only.
- The stack stays its own compose project. A `compose.override.yaml` beside its
  `compose.yaml` puts the data of each app below `apps/<app>` of the pool, as
  UNOS does for the apps it installs, and rotates the containers' logs at 10 MB,
  3 files, as UNOS does for its own:

  ```yaml
  x-logging: &logging
    driver: json-file
    options: {max-size: "10m", max-file: "3"}
  services:
    k1det:
      logging: *logging
      volumes:
        - /mnt/pools/data/apps/frigate/models:/models
        - /mnt/pools/data/apps/k1det/run:/run/k1det
    mosquitto:
      logging: *logging
      volumes:
        - /mnt/pools/data/apps/mosquitto/data:/mosquitto/data
    frigate:
      logging: *logging
      volumes:
        - /mnt/pools/data/apps/frigate/config:/config
        - /mnt/pools/data/apps/frigate/models:/config/model:ro
        - /mnt/pools/data/apps/frigate/media:/media/frigate
        - /mnt/pools/data/apps/k1det/run:/run/k1det
    homeassistant:
      logging: *logging
      volumes:
        - /mnt/pools/data/apps/homeassistant/config:/config
        - /mnt/pools/data/apps/homeassistant/cache:/root/.cache
  ```

  UNOS lists these containers on its Containers page and keeps the pool
  mounted while they use it.

### Banana Pi BPI-W3 (Rockchip RK3588, ARM64)

UNOS runs on a BPI-W3 with 8 GB of RAM, its dev device, from a microSD card
with Armbian's community image for the board (26.11.0-trunk, Debian 13, the
`vendor-rk35xx` kernel 6.1.172). The BPI-W3 and the ArmSoM W3 are one board,
and Armbian names its images `armsom-w3`. UNOS has no code of its own for the
board (ADR-0376).

- **Installation.** `install.sh` installs Docker from Docker's repository, as
  on every ARM64 host, and pulls both images of the newest release from GHCR.
  It has not run on the board yet: the dev device was set up by hand, and
  `scripts/dev/deploy.sh` builds its images (ADR-0223), so **System → Updates**
  cannot install releases there.
- **Upgrades.** `apt full-upgrade` also replaces the kernel and its device
  trees: `/boot/dtb` then points to the new kernel's directory, so copy a device
  tree overlay of your own into its `rockchip/overlay/` before the reboot.
- **The eMMC stays as it is.** Write the image to a microSD card and boot from
  it. UNOS neither lists nor formats the eMMC (`mmcblk0`), its boot partitions,
  the microSD card or zram, so whatever is on the eMMC stays there.
- **Docker's data and the card.** `update.sh` pulls both images of each
  release: about their size in writes, without the build's layers (installing
  the supervisor's Python packages alone wrote about 440 MiB to the dev
  device's card). Keep Docker's data root on another disk where you can, and make Docker
  wait for that disk with a drop-in
  `/etc/systemd/system/docker.service.d/data-root.conf` that says `[Unit]` and
  `RequiresMountsFor=<the data root>`, as on the BPI-F3. Docker, and with it
  UNOS, then does not start while that disk is missing; the dev device keeps
  its data root on the card for that reason (ADR-0228).
- **Disks for pools.** SATA disks behind an AHCI controller (the dev device has
  a JMicron JMB58x on a PCIe 2.0 x1 link), NVMe drives in the M.2 Key M slot
  (PCIe 3.0 x4) and USB 3 disks need nothing special: UNOS makes and mounts
  their pools as on every host. Btrfs turns on `discard=async` by default only
  from kernel 6.2, and UNOS mounts pools with `noatime` alone, so under the
  vendor kernel an SSD's pool is trimmed when it is made and then by the weekly
  `fstrim.timer` (`systemctl is-enabled fstrim.timer`). A SATA disk needs its own power supply. Only a hard disk has
  held a pool on the board so far; an NVMe pool is not checked.
- **Cooling.** The kernel slows the CPU down from the passive trip of
  `soc-thermal` at 75 C on, and with this image it drives no fan: none is among
  its cooling devices. Fit a heatsink, and keep the dashboard's temperature
  below 70 C. The dev device idles at about 42 C.
- **Samba's vfs modules.** A share's Time Machine and Previous Versions need
  the package `samba-vfs-modules` (ADR-0380), which `install.sh` installs and
  Debian's `samba` only recommends. On the dev device, set up by hand, check
  that `ls /usr/lib/aarch64-linux-gnu/samba/vfs/fruit.so` finds it, else install
  it with `sudo apt install samba-vfs-modules`; until then the Shares page
  refuses both options and names the package.
- **Apps.** ARM64 images of the App Store run. Jellyfin gets the RK3588's
  video decoders and encoders, its RGA and its GPU for Rockchip MPP (RKMPP),
  which needs the `vendor` kernel; choose RKMPP in Jellyfin (see the User Guide's
  Hardware Acceleration). Immich's machine learning runs on the RK3588's NPU,
  which needs the `vendor` kernel's RKNPU driver 0.9.8 or later, and its server
  gets the video hardware for RKMPP, which you choose in Immich (ADR-0382).
- **Temperature.** The dashboard shows the whole chip's sensor (`soc_thermal`).

### Network Requirements

- Ethernet connection (recommended)
- Static IP address (recommended for NAS)
- Internet access during installation
- Ports 80 and 443 free on the server
- The private subnet `172.31.254.0/24` unused on the server and your LAN: the
  UNOS Docker network uses it (see [Network subnet](#network-subnet))

---

## Hardware Requirements

### Minimum Requirements

| Component | Requirement |
|-----------|-------------|
| CPU | 1 GHz dual-core |
| RAM | 1 GB |
| System Storage | 8 GB |
| Network | 100 Mbps Ethernet |
| Browser of the web interface | Chrome or Edge 111, Firefox 128, Safari 16.4 (iOS 16.4) or newer |

### Recommended Requirements

| Component | Requirement |
|-----------|-------------|
| CPU | 2 GHz quad-core or better |
| RAM | 4 GB or more |
| System Storage | 16 GB SSD |
| Network | 1 Gbps Ethernet |

The supervisor keeps its temporary files in RAM instead of on the system disk:
at most 192 MiB, which a board with the minimum of 1 GB of RAM can spare, and
only while a backup is made or restored, or `update.sh` saves the database
before an update. A restore, a backup and `update.sh` each need about the size
of the database, which a backup may hold up to 48 MiB: a larger one is refused
with the limit in the message (the database of a NAS is a few MiB). Uploads need none:
files go straight to their folder, and an uploaded backup is checked in one
pass that writes nothing.

### Storage Disks

For data storage, UNOS supports:
- SATA HDDs and SSDs
- NVMe SSDs
- USB drives (not recommended for pools)
- Hardware RAID controllers

---

## Automatic Installation

### Quick Install (One Command)

```bash
curl -fsSL https://raw.githubusercontent.com/und3f1ned/unos/main/deploy/install.sh | sudo bash
```

This will:
1. Detect your system
2. Install dependencies
3. Install Docker
4. Download UNOS and check out its newest release, a tag `v<x.y.z>` (it stops
   while the repository has none)
5. Configure services
6. Pull the release's images from GHCR (on ARMv7: build the supervisor from
   the checkout) and start UNOS
7. Print the HTTPS address of the web interface

Run again, it checks out the newest release again.

### Installation with Options

```bash
# Download installer
curl -fsSL https://raw.githubusercontent.com/und3f1ned/unos/main/deploy/install.sh -o install.sh
chmod +x install.sh

# Custom installation directory
UNOS_INSTALL_DIR=/opt/mynas ./install.sh
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `UNOS_INSTALL_DIR` | `/opt/unos` | Installation directory (the supervisor's setting of the same name names it to the in-app updater) |
| `UNOS_REPO` | GitHub URL | Repository URL |
| `UNOS_REGISTRY` | `ghcr.io/und3f1ned` | Where the release images are pulled from, `<registry>/unos-supervisor:<x.y.z>` |

Settings of the running containers (such as `UNOS_HOSTNAME`) go into
`/etc/unos/secrets.env`, see [Step 4](#step-4-prepare-the-secrets-file).

---

## Manual Installation

If you prefer manual control, follow these steps:

### Step 1: Install System Dependencies

```bash
sudo apt update
sudo apt install -y \
    curl git ca-certificates gnupg lsb-release \
    btrfs-progs smartmontools \
    samba samba-common-bin samba-vfs-modules \
    hdparm ethtool iproute2
```

UNOS does not install `mdadm`: Debian's package assembles the md arrays of the
disks of an old NAS at the next boot. A host that already has it keeps it
(ADR-0413).

UNOS reads the disks' SMART data itself. Turn off smartd, which the package
smartmontools starts: it would read every awake disk every 30 minutes, and
each read starts the disk's standby timer again, so a disk given an idle
timeout of 30 minutes or more on the Storage page might never spin down
(ADR-0378):

```bash
sudo systemctl disable --now smartmontools
```

### Step 2: Install Docker

```bash
# Add Docker repository
curl -fsSL https://download.docker.com/linux/debian/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# Install Docker
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# Start Docker
sudo systemctl enable --now docker
```

On RISCV64, install the distribution's packages instead: Docker's repository
has none for it.

```bash
# Debian 13; on Ubuntu 24.04 docker-compose-v2 instead of docker-compose
sudo apt install -y docker.io docker-compose docker-buildx
sudo systemctl enable --now docker
```

On a SpacemiT K1 (Banana Pi BPI-F3) that uses an NVMe drive, also set up what
`configure_spacemit_k1` of `deploy/install.sh` writes: the script
`/usr/local/sbin/unos-k1-nvme` and the udev rule
`/etc/udev/rules.d/60-unos-k1-nvme.rules`, then run its `udevadm` commands
(see [Banana Pi BPI-F3](#banana-pi-bpi-f3-spacemit-k1-riscv64)).

### Step 3: Clone UNOS

```bash
sudo git clone --no-checkout https://github.com/und3f1ned/unos.git /opt/unos
# The newest release, a tag v<x.y.z>
sudo git -C /opt/unos tag --list 'v*' | sort -V | tail -n 1
sudo git -C /opt/unos checkout --detach refs/tags/<that tag>
```

### Step 4: Prepare the Secrets File

`/etc/unos/secrets.env` holds optional settings for the containers
(`KEY=value` lines; lines starting with `#` are comments, no comments after a
value: systemd would keep them as part of it). `install.sh`, `update.sh` and
the systemd unit below pass it to `docker compose`.

```bash
sudo mkdir -p /etc/unos
sudo install -m 600 /dev/null /etc/unos/secrets.env
```

| Setting | Default | Description |
|---------|---------|-------------|
| `UNOS_HOSTNAME` | `unos` | Name (or IP address) on the HTTPS certificate, see [Set the certificate name](#set-the-certificate-name-unos_hostname) |
| `JWT_SECRET` | generated | Signing secret for sessions; leave it unset |
| `UNOS_LOG_LEVEL` | `INFO` | How much the supervisor logs (`docker logs unos-supervisor`, one JSON object per line): `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL`. Applied by `sudo systemctl restart unos` |

The supervisor generates its JWT signing secret on first start and keeps it in
the `unos-data` volume (`/data/jwt_secret`, mode 0600), so the same secret is
used no matter how the containers are started, and it survives updates. To pin
the secret yourself instead, put `JWT_SECRET=<value>` in that file (at least 32
random characters, e.g. from `openssl rand -hex 32`) and make sure every
`docker compose` invocation sees it. The supervisor refuses to start with a
known placeholder such as `change-me-in-production`.

### Step 5: Configure Samba and Network Discovery

```bash
# UNOS writes /etc/samba/smb.conf itself, with NetBIOS off: nmbd does not run
sudo systemctl enable --now smbd
sudo systemctl disable --now nmbd

# avahi-daemon and wsdd2 answer over IPv4 only. Their settings come before the
# packages, whose installation starts them.
sudo mkdir -p /etc/avahi /etc/systemd/system/avahi-daemon.service.d /etc/systemd/system/wsdd2.service.d
sudo tee /etc/avahi/avahi-daemon.unos.conf > /dev/null << 'EOF'
# UNOS: avahi-daemon reads this file instead of avahi-daemon.conf (the drop-in
# /etc/systemd/system/avahi-daemon.service.d/unos.conf): Debian's settings,
# without IPv6, so that this server is announced with its IPv4 addresses only
# (docs/INSTALL.md, Network Discovery). install.sh writes it again when it runs.
[server]
use-ipv4=yes
use-ipv6=no
ratelimit-interval-usec=1000000
ratelimit-burst=1000

[wide-area]
enable-wide-area=yes

[publish]
publish-hinfo=no
publish-workstation=no
publish-aaaa-on-ipv4=no
EOF
sudo tee /etc/systemd/system/avahi-daemon.service.d/unos.conf > /dev/null << 'EOF'
# UNOS: avahi-daemon announces this server over IPv4 only (docs/INSTALL.md,
# Network Discovery).
[Service]
ExecStart=
ExecStart=/usr/sbin/avahi-daemon -s -f /etc/avahi/avahi-daemon.unos.conf
EOF
sudo tee /etc/systemd/system/wsdd2.service.d/unos.conf > /dev/null << 'EOF'
# UNOS: wsdd2 answers over IPv4 only (docs/INSTALL.md, Network Discovery).
[Service]
ExecStart=
ExecStart=/usr/sbin/wsdd2 -4
EOF
sudo systemctl daemon-reload

# Without libnss-mdns, which avahi-daemon recommends: it would change how this
# host resolves names that end in .local
sudo apt install -y --no-install-recommends avahi-daemon
sudo systemctl enable avahi-daemon
sudo systemctl restart avahi-daemon
sudo apt install -y wsdd2
sudo systemctl enable wsdd2
sudo systemctl restart wsdd2
```

These are the steps of `install.sh`; [Network Discovery](#network-discovery)
explains them. Debian 11 has no `wsdd2` package, and Ubuntu has it in
`universe`: without it, leave out the last three commands.

`install.sh` also writes an initial `/etc/samba/smb.conf`, the one UNOS
generates while there is no share; a manual installation needs nothing more
than the commands above. At its first start the supervisor imports the shares
of the existing file into its database once, and from then on writes the
whole file itself, with hardened settings: changes made to it by hand are
overwritten. When the existing file does not start with the header that UNOS
writes (`# Generated by UNOS from its database. ...`), as after a manual
installation, the supervisor keeps it as
`/etc/samba/smb.conf.unos-import-backup-<UTC time>`, for example
`smb.conf.unos-import-backup-20261112_101530_123456`; it keeps the newest 10
such copies.

### Step 6: Get the Images and Start UNOS

On AMD64, ARM64 and RISCV64, pull the release's images (its tag without the
`v`, such as `1.3.0`) and give them the names compose starts:

```bash
for service in supervisor webui; do
  sudo docker pull ghcr.io/und3f1ned/unos-$service:<x.y.z>
  sudo docker image tag ghcr.io/und3f1ned/unos-$service:<x.y.z> deploy-$service
  sudo docker image rm ghcr.io/und3f1ned/unos-$service:<x.y.z>
done
cd /opt/unos/deploy
sudo docker compose --env-file /etc/unos/secrets.env up -d
```

On ARMv7 build them instead: load the web UI's image first and build only the
supervisor (`sudo UNOS_VERSION=<the tag> docker compose build supervisor`)
before the `up`: see [ARMv7](#armv7).

smbd takes the ports and the cache directory of the smb.conf that UNOS writes
at its first start only when it restarts, not when it reloads the file. Once
`/etc/samba/smb.conf` starts with `# Generated by UNOS from its database.`,
run `sudo systemctl restart smbd` (or reboot), so that smbd no longer listens
on the NetBIOS port 139.

### Step 7: Create Systemd Service (Optional)

```bash
sudo tee /etc/systemd/system/unos.service > /dev/null << 'EOF'
[Unit]
Description=UNOS - Universal NAS Open Software
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/unos/deploy
EnvironmentFile=/etc/unos/secrets.env
ExecStart=/usr/bin/docker compose up -d --wait-timeout 680
ExecStop=/usr/bin/docker compose down
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now unos.service
```

`docker compose up` starts the web interface only once the supervisor answers
its health check. The supervisor first mounts the pools the host has not
mounted, up to two minutes each. Docker counts a failed check only if it
started after 10 minutes (`start_period` in `docker-compose.yml`), so the
check that runs then does not count, and reports the supervisor unhealthy
once three more have failed (`retries`); each check takes up to 10 seconds
(`timeout`), and Docker waits 10 seconds (`interval`) after each one.
`--wait-timeout 680`, `start_period` + (`retries` + 1) × (`interval` +
`timeout`), covers that time: `docker compose up` does not give up before
Docker has decided. Docker Compose applies the option to this wait from
version 2.30 on (`docker compose version`); versions before 2.17 reject it, so
leave it out there, as `install.sh` does.

---

## Post-Installation Setup

### 1. Open the Web Interface

Open your browser and navigate to:

```
https://YOUR_SERVER_IP
```

The web interface is only served over HTTPS. Plain `http://` answers with a
redirect to `https://` (only the health check `/api/v1/health` stays reachable
over HTTP, for monitoring). Expect a certificate warning the first time; see
[HTTPS and the Certificate Warning](#https-and-the-certificate-warning).

Find your server's IP address:
```bash
ip addr show | grep "inet " | grep -v 127.0.0.1
```

### 2. Create the Administrator Account

There are no default credentials. While no account exists, the web interface
opens the setup page ("Create your admin account to get started"): choose a
user name and a password (at least 8 characters) and press **Create Admin
Account**. You are then signed in as the first administrator; the setup page is
refused once any account exists.

**Do this right after installing:** until the first account exists, anyone
who can reach the web interface can create it. When no administrator can sign
in later, root on the server resets one (see
[The NAS Does Not Boot, or Nobody Can Sign In](#the-nas-does-not-boot-or-nobody-can-sign-in)).

### 3. Add Users (Optional)

Administrators manage accounts on the **Users** page. New accounts are regular
users; edit an account and tick **Administrator privileges** to promote it.

| Role | Access |
|------|--------|
| Administrator | Everything |
| User | **Dashboard** and **Account** (own password, two-factor authentication and login history) |

Every other page and API is for administrators only. The last administrator
cannot be demoted or deleted. Administrators cannot use SMB (network file
access): allow it for regular accounts (see the next steps).

### 4. Configure Network (Optional)

For a NAS, a fixed address is recommended. UNOS does not change addresses:
reserve one for the NAS in your router's DHCP settings, or set a static IP
address in the host's own network configuration. **System** → **Network**
shows the addresses, DNS servers, gateway and routes the host uses.

### 5. Add Storage

1. Go to **Storage** to view the disks. System disks and disks in use cannot be
   selected.
2. Click **Create Pool**, select disks and a RAID profile, and type the pool
   name to confirm. Disks that already contain data also need **Erase the
   existing data on these disks**.
3. UNOS formats the disks with btrfs, mounts the pool at `/mnt/pools/<name>`
   and adds an entry (by filesystem UUID) to the host's `/etc/fstab`, so the
   pool is mounted again after a reboot.

### 6. Create Shares

1. On the **Users** page, allow SMB for a regular account: tick **Allow SMB
   (network file) access** when you create it (SMB is then on, with that
   password), or edit an existing account, turn on **Allow SMB access**, and
   let the user turn SMB on with their password in **Account**.
2. Go to **Shares** → **SMB** and click **Add SMB share**.
3. Choose a pool or a volume and a folder inside it (the top level of a pool
   cannot be shared), and give the users **Read only** or **Read & write**
   access. Only pools that UNOS manages are offered, and a new folder is
   created only inside folders that only root can change (see
   [What Can Be Shared](USER_GUIDE.md#what-can-be-shared)).

Computers connect with the NAS's address on your local network, for example
`smb://192.168.1.10/<share>`; SMB refuses public addresses. Windows and macOS
also list the NAS in their network browsers (see
[Network Discovery](#network-discovery)). See
[SMB Shares](USER_GUIDE.md#smb-shares-windowsmac-compatible) in the User Guide.

---

## HTTPS and the Certificate Warning

Caddy, the web server in the `unos-webui` container, issues the HTTPS
certificate from its own local certificate authority (CA). Browsers do not know
that CA, so they warn about the certificate (for example "Your connection is
not private" or `NET::ERR_CERT_AUTHORITY_INVALID`). This is expected: accept the
warning, or make your devices trust the UNOS CA once. UNOS does not send HSTS,
so browsers always let you continue past the warning.

The certificate is issued for the name in `UNOS_HOSTNAME` (default `unos`).
Clients that connect by IP address or under another name get the same
certificate, so even with a trusted CA the browser reports a name mismatch
unless you open UNOS under that name.

### Trust the UNOS Certificate Authority

Copy the root certificate out of the container, on the server:

```bash
sudo docker cp unos-webui:/data/caddy/pki/authorities/local/root.crt unos-root-ca.crt
```

Copy `unos-root-ca.crt` to each device and import it as a trusted root CA:

- **Windows:** double-click the file → **Install Certificate** → **Local
  Machine** → **Place all certificates in the following store** → **Trusted
  Root Certification Authorities**.
- **macOS:** `sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain unos-root-ca.crt`
- **Debian/Ubuntu:** `sudo cp unos-root-ca.crt /usr/local/share/ca-certificates/ && sudo update-ca-certificates`.
  Firefox keeps its own list: **Settings** → **Privacy & Security** →
  **View Certificates** → **Authorities** → **Import**.
- **Android/iOS:** install the file as a CA certificate (Android: **Settings**
  → **Security** → **Install a certificate** → **CA certificate**, depending
  on the version; iOS: install the profile, then enable it under **Settings** →
  **General** → **About** → **Certificate Trust Settings**).

Then open `https://unos` (or the name you set in `UNOS_HOSTNAME`); that name
must resolve to the server, through your DNS, your router or the hosts file of
each device.

The CA is kept in the `caddy-data` Docker volume and survives updates. If that
volume is deleted (`docker compose down -v`, `uninstall.sh`), Caddy creates a
new CA and devices have to trust the new root certificate.

### Set the Certificate Name (UNOS_HOSTNAME)

Set `UNOS_HOSTNAME` to the host name or IP address that browsers use to reach
the server, then restart UNOS:

```bash
echo 'UNOS_HOSTNAME=nas.home.arpa' | sudo tee -a /etc/unos/secrets.env
sudo systemctl restart unos
```

`docker compose` takes `UNOS_HOSTNAME` from its environment: `install.sh`,
`update.sh` and the `unos` systemd unit load `/etc/unos/secrets.env` into it.
When you run `docker compose` yourself, add
`--env-file /etc/unos/secrets.env`. (`docker compose` also reads
`/opt/unos/deploy/.env` on its own; git ignores that file.) The new
certificate comes from the same CA, so devices that trust it need nothing new.

### Serve Your Own Certificate

Instead of the local CA's certificate, the web UI can serve a stored
certificate: a self-signed one made on **System** → **Security**, or one
uploaded through `POST /api/v1/security/certificates/upload`, such as one issued
by your own CA. Choose **Serve this certificate** under **SSL/TLS
Certificates** (see the User Guide's
[HTTPS Certificates](USER_GUIDE.md#https-certificates)). UNOS keeps it, with its
key, in the `served-certificate` Docker volume, which only the two UNOS
containers mount. **Use Caddy's local CA** serves the local CA's certificate
again; a certificate that no longer loads when UNOS starts leaves the local CA's.

---

## Network Discovery

Windows lists the NAS in File Explorer's **Network** view (on a network that
Windows treats as private, with network discovery on), and macOS in the
Finder sidebar (under **Locations**), under the server's name.
NetBIOS is off: smb.conf has `disable netbios = yes` and `smb ports = 445`,
and nmbd, Samba's NetBIOS name server, does not run. Two services announce the
NAS instead:

| Service | For | What it answers |
|---------|-----|-----------------|
| `wsdd2` | Windows | WS-Discovery (UDP 3702 and TCP 3702), and LLMNR for the server's name (UDP and TCP 5355), over IPv4 only |
| `avahi-daemon` | macOS | Bonjour (mDNS, UDP 5353), over IPv4 only: smbd registers its `_smb._tcp` service with it (`multicast dns register = yes`), and `_adisk._tcp` for the shares that offer Time Machine (ADR-0380), and it answers for the server's name (`<name>.local`) with the server's IPv4 addresses. smbd registers them when it or avahi-daemon starts, so UNOS restarts avahi-daemon after a change of the Time Machine shares |

Both answer over IPv4 only, as `install.sh` sets them up:

- wsdd2 runs as `wsdd2 -4` (the drop-in
  `/etc/systemd/system/wsdd2.service.d/unos.conf`): over IPv6 it would open
  its TCP ports on every interface, IPv6 tunnels included, whose global
  addresses the host may not filter.
- avahi-daemon reads `/etc/avahi/avahi-daemon.unos.conf` instead of
  `/etc/avahi/avahi-daemon.conf` (the drop-in
  `/etc/systemd/system/avahi-daemon.service.d/unos.conf`): Debian's settings
  with `use-ipv6=no` and `publish-aaaa-on-ipv4=no`. With IPv6, it would
  announce the server's global IPv6 addresses, which computers such as Macs
  prefer to IPv4; SMB refuses clients that connect from a global address, so
  the NAS would be listed but would not open. Settings made in
  `/etc/avahi/avahi-daemon.conf` no longer apply, and `install.sh` writes
  UNOS's file again when it runs.

avahi-daemon comes without libnss-mdns, which it recommends: the server
resolves names that end in `.local` through DNS, as before.

Limits, and what to check:

- **Debian 11** has no `wsdd2` package, and Ubuntu has it in `universe`:
  without it, `install.sh` warns and goes on, and Windows reaches the NAS only
  by its address (`\\192.168.1.10\<share>`). When a package exists but does
  not install (another apt holds its lock, a mirror fails), `install.sh` shows
  apt's error and stops: run it again.
- **Clients that know only NetBIOS** (some older TVs and media players) do
  not list the NAS: connect them by address. Clients that speak only SMB1,
  such as Windows XP, cannot connect at all: the NAS speaks SMB2 and later.
- **A network with IPv6 only:** neither service announces the NAS there.
- **The NAS is not listed:** check that the services run
  (`sudo systemctl status smbd wsdd2 avahi-daemon`), and that a firewall on the
  server lets these ports in from your local network.

---

## Troubleshooting

### UNOS Won't Start

Check container status:
```bash
docker ps -a
docker logs unos-supervisor
docker logs unos-webui
```

`JWT secret from UNOS_JWT_SECRET is a published placeholder value` in the
supervisor log: remove the `JWT_SECRET=` line from `/etc/unos/secrets.env`
(and from `/opt/unos/deploy/.env`, if present) and restart UNOS.

### The NAS Does Not Boot, or Nobody Can Sign In

The [Recovery](RECOVERY.md) guide covers both:

- When the server does not boot, a pool's files are still on its disks: another
  Linux machine with btrfs mounts the pool read-only, also with a disk missing,
  and copies them.
- When no administrator can sign in, because the password is forgotten or the
  authenticator app and its recovery codes are lost, root on the server sets a
  new password and turns two-factor authentication off:
  `sudo /opt/unos/deploy/reset-admin.sh`.

### Can't Access Web Interface

1. Check if containers are running:
   ```bash
   docker ps | grep unos
   ```

2. Check that UNOS answers on the server itself:
   ```bash
   curl http://localhost/api/v1/health
   curl -k https://localhost/api/v1/health
   ```

3. Check the firewall: `sudo ufw status` lists the host's rules. When System > Security turns
   ufw on, UNOS lets the local networks reach the web UI, SSH and SMB first
   ([Firewall](USER_GUIDE.md#firewall)). Over IPv4, ufw does not filter the web UI's ports, which
   Docker publishes; SSH from a client outside the local networks needs a rule of its own:
   ```bash
   sudo ufw status
   sudo ufw allow from 203.0.113.0/24 to any port 22 proto tcp
   ```

4. Check if ports 80 and 443 are in use:
   ```bash
   sudo lsof -i :80 -i :443
   ```

### Network subnet

The UNOS Docker network uses the fixed subnet `172.31.254.0/24`: the webui
container has `172.31.254.2` and the supervisor `172.31.254.3`, and the
supervisor accepts the client address forwarded by Caddy only from `.2`. If
that subnet is already used on the server or your LAN, `docker compose up`
fails (for example with "Pool overlaps with other one on this address space")
or the server cannot reach those LAN addresses. Change every `172.31.254.x` in
`deploy/docker-compose.yml` together (subnet, gateway, both `ipv4_address`
entries and `FORWARDED_ALLOW_IPS`) to another unused private `/24`, then
recreate the network:

```bash
cd /opt/unos/deploy
sudo docker compose down      # removes the containers and the old network
sudo systemctl restart unos   # docker compose up -d, with /etc/unos/secrets.env
```

Run the `down` yourself: `systemctl restart unos` only runs it when the unit
is active, which it is not between `install.sh` and the next reboot.

`update.sh` refuses to run while `/opt/unos` has local changes, and it checks
out each release's tag: set the change aside before an update
(`cd /opt/unos && sudo git stash`) and apply it again afterwards
(`sudo git stash pop`).

### Docker Permission Issues

Add your user to the docker group:
```bash
sudo usermod -aG docker $USER
# Log out and back in
```

### Database Errors

Reset the database (will lose settings):
```bash
cd /opt/unos/deploy
docker compose down -v
docker compose up -d
```

`down -v` deletes all UNOS volumes: the database, the generated JWT secret and
the HTTPS certificate authority (devices that trusted it have to trust the new
one).

### Samba Not Working

Check Samba status:
```bash
sudo systemctl status smbd
sudo mkdir -p /run/samba   # Samba's cache directory, which testparm needs; smbd creates it when it starts
sudo testparm
sudo journalctl -u smbd -u nmbd -n 50   # Samba's messages; a failed SMB sign-in is a line that starts with "Auth:"
```

Samba logs to the host's journal (**System** → **Logs**, source **Samba**).
What smbd and nmbd write to a log file of their own goes to
`/run/samba/log.samba`, in RAM, and is gone after a reboot.

UNOS writes `/etc/samba/smb.conf`; **Shares** lists the problems it found
(for example a share that is not served, Samba missing on the host, "Shares
were not imported from smb.conf: ..." or "smb.conf was not updated: ...").
While `/etc/samba/smb.conf` does not start with
`# Generated by UNOS from its database.`, SMB cannot be turned on for any
account.

---

## Upgrading

### Using the Update Script

```bash
sudo /opt/unos/deploy/update.sh             # the newest release
sudo /opt/unos/deploy/update.sh v1.3.0      # a given release, newer than the one that runs
```

The script:

1. Stops without changing anything if `/opt/unos` has local changes, and says
   how to proceed. It never stashes or discards changes. It also stops if
   another `update.sh` is running (it holds `/run/unos-update.lock`); without
   util-linux `flock` it only warns. It asks the repository for its newest
   release (a tag `v<x.y.z>`) unless you named one, and fetches that tag alone:
   a tag that was moved since it was fetched stops the update. It stops too
   when the release runs already ("UNOS runs the newest release") or is not
   newer than the one that runs (or the release it follows, for a checkout
   with commits after a tag). A tag whose images are not published yet (its
   release run takes up to three hours, and up to about eight and a half when
   it builds new RISCV64 wheels) stops it in step 2 without a change.
2. Pulls the release's two images from GHCR
   (`ghcr.io/und3f1ned/unos-supervisor:<x.y.z>` and `unos-webui`,
   `UNOS_REGISTRY` names another registry) while UNOS keeps running. If they
   cannot be had, nothing has changed. They carry the release's version, which
   **System → Updates** shows. Where Docker is neither amd64, arm64 nor
   riscv64 (a 32-bit ARM system), which has no release images, the supervisor
   is built from the release's checkout in step 4 instead.
3. Backs up the database (consistently, with SQLite's online backup API) and
   the rest of the data volume to `/var/lib/unos/backups/update-<date>/` (the
   supervisor mounts that folder and keeps its own copies of the database
   before an in-app update in `in-app-<time>/` beside them). Only
   root can read the backups: they contain the JWT signing secret, the keys
   of the backup tasks next to the scheduled backups those keys decrypt (a
   restored data volume needs both), and `offsite_secrets.json`, the password
   of the off-device copy's repository and its S3 secret key. If the backup
   fails, nothing has changed yet: the script asks whether to update without a
   backup, and stops if the answer is no or there is no terminal.
4. Tags the running images `:previous`, checks out the release's tag and gives
   the pulled images the names compose starts (`deploy-supervisor` and
   `deploy-webui`), removing their GHCR names.
5. Restarts UNOS (`docker compose up -d --remove-orphans`, which recreates
   the containers of the new images). `docker compose up`
   starts the web interface once the supervisor answers its health check. The
   supervisor first mounts the pools the host has not mounted, up to two
   minutes each. With Docker Compose 2.30 or later, `up` waits for it up to
   680 seconds, as long as Docker can take to report it healthy or unhealthy
   (see [Step 7](#step-7-create-systemd-service-optional)), or
   `UNOS_HEALTH_TIMEOUT` seconds if that is longer. The script then waits up
   to `UNOS_HEALTH_TIMEOUT` seconds (default 300) for the health check over
   HTTP and HTTPS; if UNOS needs longer to answer, run it again with a longer
   limit, such as `sudo UNOS_HEALTH_TIMEOUT=600 /opt/unos/deploy/update.sh`.
   The error message suggests twice the last limit, at least 600 seconds.
6. If the new version does not start or answer, stops it, puts the backup's
   `unos.db` back into the data volume (after deleting `unos.db-wal` and
   `unos.db-shm` there, which SQLite would apply to it), starts the previous
   images and checkout again and exits with an error. Otherwise it removes
   unused images; the previous ones stay tagged `:previous`.

An interrupted update (Ctrl+C, a closed SSH session, `kill`) is undone the same
way: before the restart the checkout and the image names go back, and once
the restart has begun the previous version is started again with the backup's
database. After a closed SSH session the messages are in
`/var/lib/unos/backups/update-interrupted.log`. The backup is listed in the
error message.

**System → Updates** runs the same script on the host when an administrator
installs a release from the web interface (see `docs/USER_GUIDE.md`): first
`update.sh --fetch <tag>`, which gets the tag and the images, then, after the
supervisor's own backup, `update.sh --in-app update` in a transient systemd
unit `unos-update-<time>`, which outlives the supervisor's container that it
replaces; its output is in `journalctl -u unos-update-<time>`.

### Manual Upgrade

```bash
cd /opt/unos
sudo git fetch origin refs/tags/<tag>:refs/tags/<tag>
sudo git checkout --detach refs/tags/<tag>
```

Then get the release's images as in [Step 6](#step-6-get-the-images-and-start-unos)
(on ARMv7: `sudo UNOS_VERSION=<tag> docker compose build --no-cache supervisor`), and
start them with /etc/unos/secrets.env: compose recreates the containers of
the new images.

```bash
sudo systemctl restart unos
```

Without the systemd unit, run
`sudo docker compose --env-file /etc/unos/secrets.env up -d` in
`/opt/unos/deploy` instead of the last command.

### Upgrade Notes

- `update.sh` backs up the database first; keep backups of your own data too
- Reload the web interface without the cache after an update (Ctrl+Shift+R)

---

## Uninstalling

```bash
sudo /opt/unos/deploy/uninstall.sh
```

This removes UNOS containers and configuration but preserves your storage pools and data.

It also removes the Samba passwords of the SMB accounts that UNOS created
and, when it removed one, restarts Samba so that open connections end. It
keeps those host accounts and the group `unos-smb`: files on the pools
belong to them, and while the accounts exist, a new installation gives its
SMB accounts other user ids. The script prints how to remove them, and names
the accounts whose Samba password it could not remove.
`/etc/samba/smb.conf` stays, and Samba still serves the shares it lists,
unless `install.sh` kept the file it replaced (`smb.conf.original`), which
is then restored, with NetBIOS (nmbd) on again. The network discovery
services avahi-daemon and wsdd2 keep running. If the supervisor replaced an
existing smb.conf with its own (after a manual installation, for example), it
kept that file, whose shares it imported, as
`/etc/samba/smb.conf.unos-import-backup-<UTC time>` (the newest 10 such
copies), and the script lists them.

It undoes what UNOS's udev rule of the disks' idle timeouts
(`/etc/udev/rules.d/90-unos-disk-spindown.rules`) set, then removes the rule.
Each disk it names gets no standby timer until it restarts (`hdparm -S 0`),
and a disk whose own automatic offline scans UNOS turned off gets them on
again (`smartctl -o on`, where the host has smartctl). What it could not undo
(a missing tool, a disk not attached, SMART off on a disk, a command that
failed) is named at the end, with the command to run. A disk whose scans stay
off keeps its record: the rule then holds only such records, which do nothing
at boot, until they are on. The rule's lines other than comments that UNOS did
not write go with it, and the end shows them. This happens before the script
removes `/opt/unos`: if the uninstallation stops while it undoes the disks
(Ctrl-C, a dropped SSH session), the rule is still whole, and running the
script again finishes it.

---

## Getting Help

- **Documentation:** [docs/](.)
- **Issues:** [GitHub Issues](https://github.com/und3f1ned/unos/issues)
- **Discussions:** [GitHub Discussions](https://github.com/und3f1ned/unos/discussions)

---

## Next Steps

After installation, check out the [User Guide](USER_GUIDE.md) to learn how to:
- Create storage pools
- Set up file shares
- Install applications
- Configure backups
