# UNOS Installation Guide

This guide provides detailed instructions for installing UNOS on your system.

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Hardware Requirements](#hardware-requirements)
3. [Automatic Installation](#automatic-installation)
4. [Manual Installation](#manual-installation)
5. [Post-Installation Setup](#post-installation-setup)
6. [Troubleshooting](#troubleshooting)
7. [Upgrading](#upgrading)

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
curl -fsSL https://raw.githubusercontent.com/your-repo/unos/main/deploy/install.sh | sudo bash
```

This will:
1. Detect your system
2. Install dependencies
3. Install Docker
4. Download UNOS
5. Configure services
6. Start UNOS

### Installation with Options

```bash
# Download installer
curl -fsSL https://raw.githubusercontent.com/your-repo/unos/main/deploy/install.sh -o install.sh
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

### Step 4: Generate Secrets

```bash
sudo mkdir -p /etc/unos
JWT_SECRET=$(openssl rand -hex 32)
echo "JWT_SECRET=$JWT_SECRET" | sudo tee /etc/unos/secrets.env
sudo chmod 600 /etc/unos/secrets.env
```

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
sudo docker compose up -d
```

### Step 7: Create Systemd Service (Optional)

```bash
sudo cat > /etc/systemd/system/unos.service << 'EOF'
[Unit]
Description=UNOS - Universal NAS Operating System
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

### 1. Access the Web Interface

Open your browser and navigate to:

```
http://YOUR_SERVER_IP
```

Find your server's IP address:
```bash
ip addr show | grep "inet " | grep -v 127.0.0.1
```

### 2. First Login

Default credentials:
- **Username:** `admin`
- **Password:** `admin`

### 3. Change Default Password

**⚠️ IMPORTANT: Change the default password immediately!**

1. Click on your username in the top-right corner
2. Select "Change Password"
3. Enter a strong password

### 4. Configure Network (Optional)

For a NAS, a static IP is recommended:

1. Go to **System** → **Network**
2. Configure your network interface
3. Set a static IP address

### 5. Add Storage

1. Go to **Storage** → **Disks** to view available disks
2. Click **Create Pool** to create a BTRFS storage pool
3. Select disks and RAID profile

### 6. Create Shares

1. Go to **Shares** → **SMB**
2. Click **Add Share**
3. Select a pool and configure permissions

---

## Troubleshooting

### UNOS Won't Start

Check container status:
```bash
docker ps -a
docker logs unos-supervisor
docker logs unos-webui
```

### Can't Access Web Interface

1. Check if containers are running:
   ```bash
   docker ps | grep unos
   ```

2. Check firewall:
   ```bash
   sudo ufw status
   sudo ufw allow 80/tcp
   sudo ufw allow 443/tcp
   ```

3. Check if port 80 is in use:
   ```bash
   sudo lsof -i :80
   ```

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

### Manual Upgrade

```bash
cd /opt/unos

# Stop services
cd deploy
sudo docker compose down

# Pull latest code
cd ..
sudo git pull

# Rebuild and restart
cd deploy
sudo docker compose build --no-cache
sudo docker compose up -d
```

### Upgrade Notes

- Always backup before upgrading
- Check release notes for breaking changes
- Clear browser cache after frontend updates

---

## Getting Help

- **Documentation:** [docs/](docs/)
- **Issues:** [GitHub Issues](https://github.com/und3f1ned/unos/issues)
- **Discussions:** [GitHub Discussions](https://github.com/und3f1ned/unos/discussions)

---

## Next Steps

After installation, check out the [User Guide](USER_GUIDE.md) to learn how to:
- Create storage pools
- Set up file shares
- Install applications
- Configure backups
