# UNOS Installation Guide

This guide provides detailed instructions for installing UNOS on your system.

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Hardware Requirements](#hardware-requirements)
3. [Automatic Installation](#automatic-installation)
4. [Manual Installation](#manual-installation)
5. [Post-Installation Setup](#post-installation-setup)
6. [HTTPS and the Certificate Warning](#https-and-the-certificate-warning)
7. [Troubleshooting](#troubleshooting)
8. [Upgrading](#upgrading)

---

## Prerequisites

### Supported Operating Systems

| OS | Version | Status |
|----|---------|--------|
| Debian | 11 (Bullseye) | ✅ Fully Supported |
| Debian | 12 (Bookworm) | ✅ Fully Supported |
| Ubuntu | 22.04 LTS | ✅ Fully Supported |
| Ubuntu | 24.04 LTS | ✅ Fully Supported |
| Raspberry Pi OS | 64-bit | ✅ Fully Supported |
| Armbian | Latest | ⚠️ Community Supported |

### Supported Architectures

- **AMD64** (x86_64) - Intel/AMD processors
- **ARM64** (AArch64) - Raspberry Pi 4/5, ARM SBCs
- **ARMv7** - Limited support, may have performance issues

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

### Recommended Requirements

| Component | Requirement |
|-----------|-------------|
| CPU | 2 GHz quad-core or better |
| RAM | 4 GB or more |
| System Storage | 16 GB SSD |
| Network | 1 Gbps Ethernet |

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
4. Download UNOS
5. Configure services
6. Start UNOS
7. Print the HTTPS address of the web interface

### Installation with Options

```bash
# Download installer
curl -fsSL https://raw.githubusercontent.com/und3f1ned/unos/main/deploy/install.sh -o install.sh
chmod +x install.sh

# Custom installation directory
UNOS_INSTALL_DIR=/opt/mynas ./install.sh

# Use specific branch
UNOS_BRANCH=develop ./install.sh
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `UNOS_INSTALL_DIR` | `/opt/unos` | Installation directory |
| `UNOS_DATA_DIR` | `/var/lib/unos` | Data directory |
| `UNOS_BRANCH` | `main` | Git branch to install |
| `UNOS_REPO` | GitHub URL | Repository URL |

Settings of the running containers (such as `UNOS_HOSTNAME`) go into
`/etc/unos/secrets.env`, see [Step 4](#step-4-prepare-the-secrets-file).

---

## Manual Installation

If you prefer manual control, follow these steps:

### Step 1: Install System Dependencies

```bash
sudo apt update
sudo apt install -y \
    curl wget git ca-certificates gnupg lsb-release \
    btrfs-progs smartmontools lm-sensors \
    samba samba-common-bin \
    hdparm parted mdadm nvme-cli \
    ethtool net-tools iproute2
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

### Step 3: Clone UNOS

```bash
sudo git clone https://github.com/und3f1ned/unos.git /opt/unos
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

The supervisor generates its JWT signing secret on first start and keeps it in
the `unos-data` volume (`/data/jwt_secret`, mode 0600), so the same secret is
used no matter how the containers are started, and it survives updates. To pin
the secret yourself instead, put `JWT_SECRET=<value>` in that file (at least 32
random characters, e.g. from `openssl rand -hex 32`) and make sure every
`docker compose` invocation sees it. The supervisor refuses to start with a
known placeholder such as `change-me-in-production`.

### Step 5: Configure Samba

```bash
# Backup existing config
sudo cp /etc/samba/smb.conf /etc/samba/smb.conf.backup

# UNOS will manage smb.conf automatically
sudo systemctl enable --now smbd nmbd
```

### Step 6: Build and Start UNOS

```bash
cd /opt/unos/deploy
sudo docker compose build
sudo docker compose --env-file /etc/unos/secrets.env up -d
```

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
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable unos.service
```

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
who can reach the web interface can create it.

### 3. Add Users (Optional)

Administrators manage accounts on the **Users** page. New accounts are regular
users; edit an account and tick **Administrator privileges** to promote it.

| Role | Access |
|------|--------|
| Administrator | Everything |
| User | **Dashboard** and **Account** (own password, two-factor authentication and login history) |

Every other page and API is for administrators only. The last administrator
cannot be demoted or deleted.

### 4. Configure Network (Optional)

For a NAS, a static IP is recommended:

1. Go to **System** → **Network**
2. Configure your network interface
3. Set a static IP address

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

1. Go to **Shares** → **SMB**
2. Click **Add Share**
3. Select a pool and configure permissions

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

3. Check firewall:
   ```bash
   sudo ufw status
   sudo ufw allow 80/tcp
   sudo ufw allow 443/tcp
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

`update.sh` refuses to run while `/opt/unos` has local changes: commit the
change (`cd /opt/unos && sudo git commit -am "Local subnet"`), and when
`update.sh` later reports local commits, rebase them as it describes.

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
sudo testparm
```

---

## Upgrading

### Using the Update Script

```bash
sudo /opt/unos/deploy/update.sh
```

The script:

1. Stops without changing anything if `/opt/unos` has local changes or is not
   on the branch it updates (`UNOS_BRANCH`, default `main`), and says how to
   proceed. It never stashes or discards changes. It also stops if another
   `update.sh` is running (it holds `/run/unos-update.lock`); without
   util-linux `flock` it only warns.
2. Backs up the database (consistently, with SQLite's online backup API) and
   the rest of the data volume to `/var/lib/unos/backups/update-<date>/`. Only
   root can read the backups: they contain the JWT signing secret.
3. Tags the running images `:previous`, pulls the update and builds the new
   images while UNOS keeps running. If the build fails, nothing changes.
4. Restarts UNOS (`docker compose down`, then `up -d`) and waits up to five
   minutes (`UNOS_HEALTH_TIMEOUT`, in seconds) for the health check over HTTP
   and HTTPS.
5. If the new version does not start or answer, starts the previous images and
   source revision again and exits with an error. Otherwise it removes unused
   images; the previous ones stay tagged `:previous`.

An interrupted update (Ctrl+C, a closed SSH session, `kill`) is undone the same
way: during the build the checkout returns to the previous revision, and after
UNOS was stopped the previous version is started again. After a closed SSH
session the messages are in `/var/lib/unos/backups/update-interrupted.log`.
The rollback restores the program, not the data; the backup is listed in the
error message.

### Manual Upgrade

```bash
cd /opt/unos
sudo git pull --ff-only

# Build while the current version keeps running
cd deploy
sudo docker compose build --no-cache

# Stop and remove the containers and the network (recreated with the
# current settings), then start with /etc/unos/secrets.env
sudo docker compose down
sudo systemctl restart unos
```

Without the systemd unit, run
`sudo docker compose --env-file /etc/unos/secrets.env up -d` instead of the
last command.

### Updating from 1.0.0 or Earlier

The changes listed under [Unreleased] in [CHANGELOG.md](../CHANGELOG.md) need
a few steps once.

Before updating:

- `git -C /opt/unos status` must show no local changes. This update still runs
  the update script of the version you have installed, which stashes local
  changes and replaces them with the upstream code. It also stops UNOS while it
  builds the new images.
- If `/etc/unos/secrets.env` (or `/opt/unos/deploy/.env`) sets `JWT_SECRET` to a
  placeholder such as `change-me-in-production`, remove that line: the
  supervisor now refuses to start with it.
- `/etc/unos/secrets.env` must contain only `KEY=value` lines, without
  comments or spaces in values: the 1.0.0 update script loads it with
  `export $(cat /etc/unos/secrets.env | xargs)`, which fails on a `#` line
  after UNOS has already been stopped.
- Back up the data volume yourself. The 1.0.0 update script archives the
  volume `unos-data`, but the data is in `deploy_unos-data`, so its backup is
  empty:
  ```bash
  sudo install -d -m 700 /var/lib/unos/backups
  sudo docker stop unos-supervisor
  sudo docker run --rm -v deploy_unos-data:/data:ro -v /var/lib/unos/backups:/backup \
    alpine tar czf /backup/before-update.tar.gz -C /data .
  sudo docker start unos-supervisor
  ```

After updating:

- Open `https://<server address>`; `http://` only redirects. Expect the
  certificate warning described in
  [HTTPS and the Certificate Warning](#https-and-the-certificate-warning).
- Everyone signs in again once: the web interface has a new address (`https://`),
  and installations without `JWT_SECRET` now use the secret that the
  supervisor generates on first start. A `JWT_SECRET=` line in
  `/etc/unos/secrets.env` keeps being used wherever that file is loaded
  (systemd unit, `update.sh`); remove it to switch to the generated secret.
- Regular (non-admin) accounts only see **Dashboard** and **Account**. Their
  sessions and API tokens get HTTP 403 from the administrator-only APIs,
  including `/api/v1/health/prometheus`: monitoring needs an administrator's
  API token.
- Scheduled tasks, disk health (SMART and RAID) alerts and the UPS monitor run
  for the first time; earlier releases never started them. Check
  **System** → **Scheduler** before updating, especially custom commands,
  which run as root.
- Apps installed by earlier releases keep their data in `/mnt/pools/default` on
  the system disk. The App Store marks them and UNOS no longer starts them:
  uninstall such an app (its data is kept), move the folders onto a pool and
  install the app again on that pool.
- Scripts that use the WebSocket API must send
  `{"type": "auth", "token": "<token>"}` as the first message; a `?token=`
  parameter in the URL is refused.

#### Pools created before this release

Earlier releases wrote a pool's `/etc/fstab` line inside the supervisor
container, never into the host's `/etc/fstab`, so these pools are not mounted
at boot. After the update (at the latest after the next reboot), **Storage**
shows such a pool as **Not mounted**, and its directory `/mnt/pools/<name>` is
write-protected (`chattr +i`) so that nothing is written to the boot disk in
its place. Mount each of these pools once:

1. On the **Storage** page, press **Mount** on the pool. UNOS mounts it by
   filesystem UUID and adds a managed entry to the host's `/etc/fstab`, so it
   is mounted at every boot from then on.
2. If Mount reports that `/mnt/pools/<name>` is not empty, files were written
   to the boot disk while the pool was not mounted. Move them away, then press
   **Mount** again, and copy back into the mounted pool what you need:
   ```bash
   sudo chattr -i /mnt/pools/<name>
   sudo mkdir /root/<name>-leftovers
   sudo find /mnt/pools/<name> -mindepth 1 -maxdepth 1 -exec mv -t /root/<name>-leftovers {} +
   ```
3. Check the result:
   ```bash
   findmnt /mnt/pools/<name>       # btrfs, mounted
   grep -A1 unos-pool /etc/fstab   # "# unos-pool:<name> (managed by UNOS)" and its UUID= line
   ```

A pool that is still mounted from before the update is shown as mounted, with
the badge **Not mounted at boot**: press **Unmount** (stop the apps that use it
first) and then **Mount**, or reboot and then press **Mount**. If Mount reports
that `/etc/fstab` already mounts the directory through an entry UNOS does not
manage (for example a line you added by hand), remove or comment out that line
and press **Mount** again.

### Upgrade Notes

- Always backup before upgrading
- Check release notes for breaking changes
- Clear browser cache after frontend updates

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
