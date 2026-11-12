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
- **SMB/CIFS Shares** - SMB shares with per-user read/write access, for Windows, macOS, and Linux; Windows and macOS list the NAS in their network browsers (WS-Discovery and Bonjour, NetBIOS off)
- **NFS Exports** - Export a folder of a pool, or a volume, read-only or read-write to one network of your local network; exports written by hand on the NAS are kept and listed. Needs the host's NFS server (`nfs-kernel-server`), which the installer does not install
- **Web File Browser** - Upload, download, copy, move, rename, and manage files via browser, with a trash that keeps deleted items for 30 days

### Application Platform
- **70+ Curated Apps** - One-click installation of popular self-hosted applications
- **Docker Management** - Full container lifecycle management (create, start, stop, logs)
- **Image Management** - Pull, list, and remove Docker images
- **App Categories** - Media, Downloads, Productivity, Monitoring, Smart Home, Network, AI & LLMs, Security, and more

### System Monitoring
- **Real-time Dashboard** - Live CPU, memory, network, and disk I/O statistics via WebSocket
- **Process Monitor** - View the top processes by CPU or memory use
- **Temperature Monitoring** - Hardware temperature sensors
- **Network Statistics** - Per-interface bandwidth monitoring with history graphs
- **Prometheus Metrics** - `/api/v1/health/prometheus` endpoint for external monitoring (administrator API token)

### User Management & Security
- **HTTPS by Default** - The web interface is served over HTTPS with certificates from a built-in local CA; plain HTTP redirects
- **Multi-user Support** - Administrators and regular users (who see only the dashboard and their own account)
- **Two-Factor Authentication** - TOTP-based 2FA with authenticator apps, and ten single-use recovery codes for a lost app
- **API Tokens** - Generate tokens for scripts through the API
- **Audit Logging** - Sign-ins, the management of users and their SMB access, shares, API tokens and recovery codes are recorded, and so are changes of pools, volumes, snapshots, containers and apps, backups, reboots and shutdowns, scheduled tasks, network interfaces, the host name and the time zone (the firewall, the TLS certificate, suspend, the off-device copy and notifications not yet); administrators search, filter and export them on the Audit Log page or through the API
- **Rate Limiting** - Protection against brute-force attacks

### Notifications
- **Alerts** - Pool (RAID) errors and disk health (SMART) warnings go to every enabled e-mail and Telegram channel
- **Channels** - E-mail (over STARTTLS or SMTPS with the mail server's certificate checked, or plain SMTP), Telegram, generic webhooks, Discord and Slack in System > Notifications, with test messages that name why one was not sent
- **History** - The latest 100 notifications sent, tests and alerts included, newest first, with their result

### Task Scheduler
- **Automated Snapshots** - Schedule periodic snapshot creation with retention
- **Scheduled Scrubs** - Automate BTRFS data integrity checks
- **SMART Tests** - Schedule disk health tests
- **Configuration Backups** - Automated system backups, always encrypted with the task's passphrase, with cleanup
- **Custom Commands** - Run a shell command in the supervisor container on a schedule

### Network Management
- **Interfaces** - View network interfaces with their IPv4 and IPv6 addresses, state and link speed, and bring them up or down; a disabled interface comes back on by itself after 60 seconds unless the change is kept
- **DNS Configuration** - View DNS servers and search domains
- **Routing Table** - View the IPv4 and IPv6 routes, the gateway and active connections

### Backup & Recovery
- **Configuration Backup** - Back up the UNOS database (accounts, 2FA, API tokens, SMB shares and their access)
- **Encrypted Backups** - Optional AES-256 encryption with passphrase
- **Restore** - Import backups to restore configuration
- **Off-Device Copies** - The configuration and the pools' folders copied with restic, encrypted, to a disk of their own or an S3-compatible store, on a schedule; each copy is checked and its restore tested, and its snapshots restore the configuration and the files

### Power Management
- **Reboot, shutdown and suspend** - From System > Power after a confirmation; reboot and shutdown act on the host one minute later and can be cancelled until then
- **Wake-on-LAN** - Turned on or off per Ethernet interface, and set again after every reboot

### System Updates
- **Update script** - `sudo /opt/unos/deploy/update.sh` updates UNOS from its git branch, backs up the database and the data volume first, and rolls back automatically if the new version does not start
- **Version check** - System > Updates shows the installed version and the latest release on GitHub, and names the update script

### Planned

Not working yet; each has an entry in [feature-list.json](feature-list.json), named in parentheses:
- **UPS monitoring** (`system.ups-monitoring`) - The UPS state and the shutdown on low battery: there is no UPS page, the API reads the state only where the host has NUT, which its owner installs, and the shutdown is skipped inside a container
- **Scheduled power** (`system.scheduled-power`) - Shutdown and wake times are stored, and nothing acts on them
- **Alerts for every event and channel** (`system.alert-events`) - Discord, Slack and webhook channels get no alerts, and the events chosen for a channel have no effect
- **Host name and time zone** (`system.hostname-and-time-zone`) - Setting them from the web interface

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
│  btrfs, samba, smartmontools, docker                    │
└─────────────────────────────────────────────────────────┘
```

## Documentation

| Document | Description |
|----------|-------------|
| [Installation Guide](docs/INSTALL.md) | Detailed installation instructions |
| [User Guide](docs/USER_GUIDE.md) | How to use UNOS features |
| [Recovery](docs/RECOVERY.md) | Reach the pools from another Linux machine; reset an administrator who cannot sign in |
| [Changelog](CHANGELOG.md) | Version history and release notes |
| [Contributing](CONTRIBUTING.md) | How work flows, from an issue to a pull request |
| [AGENTS.md](AGENTS.md) | Commands, rules and conventions for developers and coding agents |
| [Testing Guide](docs/TESTING.md) | The tests, the local stand-in and CI |
| [Decision Records](docs/adr/README.md) | Why UNOS is built the way it is |

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
sudo systemctl restart unos   # docker compose up -d with /etc/unos/secrets.env
```

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
`/etc/samba/smb.conf.unos-import-backup`.

## Contributing

Contributions are welcome. [CONTRIBUTING.md](CONTRIBUTING.md) describes how work
flows from an issue or a feature entry to a pull request, the tests every change
needs, and how decisions are recorded in [docs/adr/](docs/adr/README.md).

```bash
make setup   # once per checkout: Python venv, lint tools, npm packages, Playwright, git hooks
make check   # the quality gate: the backend tests, lint, svelte-check, the web UI's texts and the build, as in CI
```

Developers and coding agents follow [AGENTS.md](AGENTS.md) for the commands and
the rules; [docs/TESTING.md](docs/TESTING.md) covers the tests, the local
stand-in and CI.

## License

UNOS is released under the [GPL-3.0-or-later](LICENSE) license: the GNU General Public
License, version 3 or (at your option) any later version.

## Support

- [Issue Tracker](https://github.com/und3f1ned/unos/issues)
- [Discussions](https://github.com/und3f1ned/unos/discussions)

---

<p align="center">
  Built for self-hosters, by self-hosters
</p>

<p align="center">
  <code>nas</code> · <code>self-hosted</code> · <code>homelab</code> · <code>storage</code> · <code>btrfs</code> · <code>docker</code> · <code>samba</code> · <code>fastapi</code> · <code>sveltekit</code> · <code>arm64</code> · <code>home-server</code> · <code>file-sharing</code> · <code>network-attached-storage</code> · <code>linux</code>
</p>
