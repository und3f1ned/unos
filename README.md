# UNOS - Universal NAS Open Software

<p align="center">
  <strong>A modern, open-source NAS management platform for ARM and x86 Linux systems</strong>
</p>

<p align="center">
  <a href="https://github.com/und3f1ned/unos/releases"><img src="https://img.shields.io/github/v/release/und3f1ned/unos?style=flat-square&color=blue" alt="Release"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/und3f1ned/unos?style=flat-square" alt="License"></a>
  <a href="https://github.com/und3f1ned/unos/stargazers"><img src="https://img.shields.io/github/stars/und3f1ned/unos?style=flat-square" alt="Stars"></a>
  <a href="https://github.com/und3f1ned/unos/issues"><img src="https://img.shields.io/github/issues/und3f1ned/unos?style=flat-square" alt="Issues"></a>
  <a href="https://github.com/und3f1ned/unos/commits/main"><img src="https://img.shields.io/github/last-commit/und3f1ned/unos?style=flat-square" alt="Last Commit"></a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/FastAPI-009688?style=flat-square&logo=fastapi&logoColor=white" alt="FastAPI">
  <img src="https://img.shields.io/badge/SvelteKit-FF3E00?style=flat-square&logo=svelte&logoColor=white" alt="SvelteKit">
  <img src="https://img.shields.io/badge/TailwindCSS-06B6D4?style=flat-square&logo=tailwindcss&logoColor=white" alt="TailwindCSS">
  <img src="https://img.shields.io/badge/Docker-2496ED?style=flat-square&logo=docker&logoColor=white" alt="Docker">
  <img src="https://img.shields.io/badge/BTRFS-FF6600?style=flat-square&logo=linux&logoColor=white" alt="BTRFS">
  <img src="https://img.shields.io/badge/ARM64-supported-green?style=flat-square" alt="ARM64">
  <img src="https://img.shields.io/badge/AMD64-supported-green?style=flat-square" alt="AMD64">
</p>

<p align="center">
  <a href="#features">Features</a> •
  <a href="#quick-start">Quick Start</a> •
  <a href="#documentation">Documentation</a> •
  <a href="#architecture">Architecture</a> •
  <a href="#contributing">Contributing</a>
</p>

---

## Overview

UNOS turns any Debian or Ubuntu machine into a full-featured NAS server with a modern web interface. It installs on top of your existing Linux system as a set of Docker containers — no need to replace your OS or reflash your device. Built with FastAPI and SvelteKit, UNOS provides real-time monitoring, containerized app deployment, and BTRFS-based storage management out of the box.

## Features

### Storage Management
- **BTRFS Storage Pools** - Create and manage pools with single, RAID 0/1/1C3/1C4/10 profiles (no RAID 5/6: btrfs write hole)
- **Volume Management** - Create subvolumes within pools for organized storage
- **SMART Monitoring** - Real-time disk health monitoring and diagnostics
- **BTRFS Snapshots** - Create, restore, and schedule filesystem snapshots
- **Pool Scrubbing** - Data integrity verification with scheduled scrubs

### File Sharing
- **SMB/CIFS Shares** - Windows, macOS, and Linux compatible file sharing
- **NFS Exports** - Unix/Linux native network file system
- **Web File Browser** - Upload, download, copy, move, and manage files via browser

### Application Platform
- **70+ Curated Apps** - One-click installation of popular self-hosted applications
- **Docker Management** - Full container lifecycle management (create, start, stop, logs)
- **Image Management** - Pull, list, and remove Docker images
- **App Categories** - Media, Downloads, Productivity, Monitoring, Home Automation, Network, AI, Security, and more

### System Monitoring
- **Real-time Dashboard** - Live CPU, memory, network, and disk I/O statistics via WebSocket
- **Process Monitor** - View and manage running processes
- **Temperature Monitoring** - Hardware temperature sensors
- **Network Statistics** - Per-interface bandwidth monitoring with history graphs
- **Prometheus Metrics** - `/api/v1/health/prometheus` endpoint for external monitoring (administrator API token)

### User Management & Security
- **HTTPS by Default** - The web interface is served over HTTPS with certificates from a built-in local CA; plain HTTP redirects
- **Multi-user Support** - Administrators and regular users (who see only the dashboard and their own account)
- **Two-Factor Authentication** - TOTP-based 2FA with authenticator apps
- **Recovery Codes** - Backup codes for 2FA recovery
- **API Tokens** - Generate tokens for programmatic access
- **Audit Logging** - Track all user actions and system events
- **Rate Limiting** - Protection against brute-force attacks

### Notifications
- **Email** - SMTP-based email alerts
- **Telegram** - Bot notifications to chat/groups
- **Discord** - Webhook notifications with embeds
- **Slack** - Webhook notifications
- **Custom Webhooks** - Generic webhook support for any service

### Task Scheduler
- **Automated Snapshots** - Schedule periodic snapshot creation with retention
- **Scheduled Scrubs** - Automate BTRFS data integrity checks
- **SMART Tests** - Schedule disk health tests
- **Configuration Backups** - Automated system backup with cleanup
- **Custom Commands** - Run any command on a schedule

### Network Management
- **Interface Management** - View and control network interfaces
- **DNS Configuration** - View DNS servers and search domains
- **Hostname Management** - Set system hostname
- **Routing Table** - View network routes and gateway

### Power Management
- **Remote Control** - Reboot, shutdown, and suspend via web interface
- **Wake-on-LAN** - Configure WoL for network interfaces
- **UPS Monitoring** - NUT integration with auto-shutdown on low battery
- **Scheduled Power** - Configure auto-shutdown and wake times

### Backup & Recovery
- **Configuration Backup** - Back up the UNOS database (accounts, 2FA, API tokens) and the SMB share configuration
- **Encrypted Backups** - Optional AES-256 encryption with passphrase
- **Restore** - Import backups to restore configuration

### System Updates
- **Update script** - `sudo /opt/unos/deploy/update.sh` updates UNOS from its git branch, backs up the database and the data volume first, and rolls back automatically if the new version does not start

## Quick Start

### One-Line Installation

```bash
curl -fsSL https://raw.githubusercontent.com/und3f1ned/unos/main/deploy/install.sh | sudo bash
```

### Manual Installation

```bash
git clone https://github.com/und3f1ned/unos.git /opt/unos
cd /opt/unos/deploy
sudo ./install.sh
```

### Access the Web Interface

After installation, open your browser:

```
https://YOUR_SERVER_IP
```

Plain `http://` redirects to `https://`. Expect a certificate warning: UNOS
issues its certificate from its own local CA. Accept it, or trust the CA on
your devices as described in
[HTTPS and the Certificate Warning](docs/INSTALL.md#https-and-the-certificate-warning).

There are no default credentials: on the first visit UNOS asks you to create the
administrator account. Do this right after installing, because until then
anyone who can reach the page can create it.

## System Requirements

### Minimum
- **CPU:** 1 GHz dual-core (ARM64 or AMD64)
- **RAM:** 1 GB
- **Storage:** 8 GB for system
- **OS:** Debian 11+ or Ubuntu 22.04+

### Recommended
- **CPU:** 2 GHz quad-core
- **RAM:** 4 GB
- **Storage:** 16 GB SSD for system

### Supported Platforms
- x86_64 (AMD64)
- ARM64 (AArch64)

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Web Browser                                            │
└──────────────────────┬──────────────────────────────────┘
                       │ HTTPS + WebSocket (wss)
┌──────────────────────▼──────────────────────────────────┐
│  unos-webui (SvelteKit + Caddy)                         │
│  Port: 443 (HTTPS), 80 (redirects to HTTPS)             │
└──────────────────────┬──────────────────────────────────┘
                       │ /api/*, /ws/*
┌──────────────────────▼──────────────────────────────────┐
│  unos-supervisor (Python FastAPI)                       │
│  Port: 8080 (internal)                                  │
│  SQLite: /data/unos.db                                  │
└──────────────────────┬──────────────────────────────────┘
                       │ Docker socket, system commands
┌──────────────────────▼──────────────────────────────────┐
│  Host OS (Debian/Ubuntu)                                │
│  btrfs, samba, nfs, smartmontools, docker               │
└─────────────────────────────────────────────────────────┘
```

## Documentation

| Document | Description |
|----------|-------------|
| [Installation Guide](docs/INSTALL.md) | Detailed installation instructions |
| [User Guide](docs/USER_GUIDE.md) | How to use UNOS features |
| [Changelog](CHANGELOG.md) | Version history and release notes |
| [Development Guide](CLAUDE.md) | Developer instructions |

## Tech Stack

### Backend (Supervisor)
- Python 3.11+, FastAPI, Uvicorn
- SQLAlchemy + aiosqlite (async SQLite)
- PyJWT (JWT), bcrypt, pyotp (2FA)
- docker-py, psutil

### Frontend (WebUI)
- SvelteKit 2.x, TypeScript
- TailwindCSS, Lucide Icons
- Caddy (reverse proxy)

## Updating

```bash
sudo /opt/unos/deploy/update.sh
```

The script refuses to run over local changes in `/opt/unos`, builds the new
images while UNOS keeps running, and rolls back if the new version does not
start. Or manually:
```bash
cd /opt/unos
sudo git pull --ff-only
cd deploy
sudo docker compose build
sudo docker compose down
sudo systemctl restart unos   # docker compose up -d with /etc/unos/secrets.env
```

Updating from 1.0.0 or earlier needs a few steps once (HTTPS, mounting existing
pools): see [Updating from 1.0.0 or Earlier](docs/INSTALL.md#updating-from-100-or-earlier).

## Uninstalling

```bash
sudo /opt/unos/deploy/uninstall.sh
```

This removes UNOS containers and configuration but preserves your storage pools and data.

## Contributing

Contributions are welcome! See [CLAUDE.md](CLAUDE.md) for development setup and coding standards.

```bash
# Backend development
cd supervisor
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
uvicorn src.main:app --reload --port 8080

# Frontend development
cd webui
npm install && npm run dev
```

## License

UNOS is released under the [GPL-3.0 License](LICENSE).

## Support

- [Issue Tracker](https://github.com/und3f1ned/unos/issues)
- [Discussions](https://github.com/und3f1ned/unos/discussions)

---

<p align="center">
  Built for self-hosters, by self-hosters
</p>

<p align="center">
  <code>nas</code> · <code>self-hosted</code> · <code>homelab</code> · <code>storage</code> · <code>btrfs</code> · <code>docker</code> · <code>samba</code> · <code>nfs</code> · <code>fastapi</code> · <code>sveltekit</code> · <code>arm64</code> · <code>home-server</code> · <code>file-sharing</code> · <code>network-attached-storage</code> · <code>linux</code>
</p>
