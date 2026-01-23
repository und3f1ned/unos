# UNOS - Universal NAS Operating System

<p align="center">
  <img src="docs/images/logo.png" alt="UNOS Logo" width="200">
</p>

<p align="center">
  <strong>An open-source, architecture-agnostic NAS operating system</strong>
</p>

<p align="center">
  <a href="#features">Features</a> •
  <a href="#quick-start">Quick Start</a> •
  <a href="#documentation">Documentation</a> •
  <a href="#screenshots">Screenshots</a> •
  <a href="#contributing">Contributing</a>
</p>

---

## Overview

UNOS (Universal NAS Operating System) transforms any Linux system into a powerful, easy-to-use Network Attached Storage server. Built with modern web technologies and designed for both ARM and x86 architectures.

## Features

### Storage Management
- **BTRFS Storage Pools** - Create, manage, and monitor BTRFS storage pools
- **SMART Monitoring** - Real-time disk health monitoring
- **Snapshots** - Create and restore filesystem snapshots
- **Multiple RAID Profiles** - Support for single, RAID0, RAID1, RAID5, RAID6, RAID10

### File Sharing
- **SMB/CIFS Shares** - Windows-compatible file sharing
- **User Management** - Create users with different access levels
- **Permission Control** - Fine-grained access control per share

### Docker Integration
- **Container Management** - Start, stop, and monitor Docker containers
- **App Store** - One-click installation of popular applications
- **Resource Monitoring** - Track container CPU and memory usage

### System Management
- **Web Dashboard** - Modern, responsive web interface
- **Real-time Monitoring** - CPU, memory, network, and temperature stats
- **Network Configuration** - View and manage network interfaces
- **Notifications** - Email, Discord, and Telegram alerts
- **Scheduled Tasks** - Automate backups and maintenance

## Quick Start

### One-Line Installation

```bash
curl -fsSL https://raw.githubusercontent.com/your-repo/unos/main/deploy/install.sh | sudo bash
```

### Manual Installation

```bash
# Clone the repository
git clone https://github.com/und3f1ned/unos.git /opt/unos

# Run the installer
cd /opt/unos/deploy
sudo ./install.sh
```

### Access the Web Interface

After installation, open your browser and navigate to:

```
http://YOUR_SERVER_IP
```

Default credentials:
- **Username:** `admin`
- **Password:** `admin`

⚠️ **Important:** Change the default password immediately after first login!

## System Requirements

### Minimum Requirements
- **CPU:** 1 GHz dual-core (ARM64 or AMD64)
- **RAM:** 1 GB
- **Storage:** 8 GB for system
- **Network:** Ethernet connection

### Recommended Requirements
- **CPU:** 2 GHz quad-core or better
- **RAM:** 4 GB or more
- **Storage:** 16 GB SSD for system
- **Network:** Gigabit Ethernet

### Supported Operating Systems
- Debian 11 (Bullseye) or newer
- Ubuntu 22.04 LTS or newer
- Raspberry Pi OS (64-bit)

### Supported Architectures
- x86_64 (AMD64)
- ARM64 (AArch64)
- ARMv7 (32-bit ARM, limited support)

## Documentation

| Document | Description |
|----------|-------------|
| [Installation Guide](docs/INSTALL.md) | Detailed installation instructions |
| [User Guide](docs/USER_GUIDE.md) | How to use UNOS features |
| [API Reference](docs/API.md) | REST API documentation |
| [Development Guide](CLAUDE.md) | For developers and contributors |
| [FAQ](docs/FAQ.md) | Frequently asked questions |

## Screenshots

<details>
<summary>Click to view screenshots</summary>

### Dashboard
![Dashboard](docs/images/dashboard.png)

### Storage Management
![Storage](docs/images/storage.png)

### Docker Containers
![Containers](docs/images/containers.png)

### File Browser
![Files](docs/images/files.png)

</details>

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Web Browser                                            │
└──────────────────────┬──────────────────────────────────┘
                       │ HTTP/HTTPS
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
                       │ Docker socket, privileged
┌──────────────────────▼──────────────────────────────────┐
│  Host OS (Debian/Ubuntu)                                │
│  btrfs, samba, smartmontools, docker                    │
└─────────────────────────────────────────────────────────┘
```

## Updating UNOS

```bash
# Using the update script
sudo /opt/unos/deploy/update.sh

# Or manually
cd /opt/unos
sudo git pull
cd deploy
sudo docker compose build
sudo docker compose up -d
```

## Uninstalling UNOS

```bash
sudo /opt/unos/deploy/uninstall.sh
```

This will:
- Stop and remove UNOS containers
- Remove UNOS configuration files
- **NOT** remove your storage pools or data

## Tech Stack

### Backend
- Python 3.11+
- FastAPI
- SQLAlchemy + SQLite
- Docker SDK

### Frontend
- SvelteKit
- TypeScript
- TailwindCSS
- Lucide Icons

### Infrastructure
- Docker & Docker Compose
- Caddy (reverse proxy)

## Contributing

We welcome contributions! Please see our [Contributing Guide](CONTRIBUTING.md) for details.

### Development Setup

```bash
# Clone the repo
git clone https://github.com/und3f1ned/unos.git
cd unos

# Backend development
cd supervisor
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn src.main:app --reload --port 8080

# Frontend development
cd webui
npm install
npm run dev
```

## License

UNOS is released under the [MIT License](LICENSE).

## Support

- 📖 [Documentation](docs/)
- 🐛 [Issue Tracker](https://github.com/und3f1ned/unos/issues)
- 💬 [Discussions](https://github.com/und3f1ned/unos/discussions)
- 📧 Email: support@unos.io

## Acknowledgments

UNOS is built on top of many excellent open-source projects:
- [FastAPI](https://fastapi.tiangolo.com/)
- [SvelteKit](https://kit.svelte.dev/)
- [TailwindCSS](https://tailwindcss.com/)
- [Docker](https://www.docker.com/)
- [Caddy](https://caddyserver.com/)
- [BTRFS](https://btrfs.wiki.kernel.org/)
- [Samba](https://www.samba.org/)

---

<p align="center">
  Made with ❤️ by the UNOS Team
</p>
