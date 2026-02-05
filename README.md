# UNOS - Universal NAS Operating System

<p align="center">
  <strong>A modern, open-source NAS operating system for ARM and x86 Linux systems</strong>
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

UNOS transforms any Linux system into a powerful Network Attached Storage server with a modern web interface. Built with FastAPI and SvelteKit, it provides real-time monitoring, containerized application deployment, and enterprise-grade storage management.

## Features

### Storage Management
- **BTRFS Storage Pools** - Create and manage pools with RAID 0/1/5/6/10 profiles
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
- **Prometheus Metrics** - `/api/v1/health/prometheus` endpoint for external monitoring

### User Management & Security
- **Multi-user Support** - Create users with admin or regular permissions
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
- **Configuration Backup** - Export all UNOS settings
- **Encrypted Backups** - Optional AES-256 encryption with passphrase
- **Restore** - Import backups to restore configuration

### System Updates
- **GitHub-based Updates** - Check and install updates from releases
- **Rollback Support** - Revert to previous version if issues occur
- **Update History** - Track all applied updates

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
http://YOUR_SERVER_IP
```

Default credentials:
- **Username:** `admin`
- **Password:** `admin`

**Important:** Change the default password immediately after first login.

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
                       │ HTTP/HTTPS + WebSocket
┌──────────────────────▼──────────────────────────────────┐
│  unos-webui (SvelteKit + Caddy)                         │
│  Port: 80/443                                           │
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
- python-jose (JWT), passlib (bcrypt), pyotp (2FA)
- docker-py, psutil

### Frontend (WebUI)
- SvelteKit 2.x, TypeScript
- TailwindCSS, Lucide Icons
- Caddy (reverse proxy)

## Updating

```bash
sudo /opt/unos/deploy/update.sh
```

Or manually:
```bash
cd /opt/unos
sudo git pull
cd deploy
sudo docker compose build && sudo docker compose up -d
```

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
