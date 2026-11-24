# UNOS - Universal NAS Open Software

<p align="center">
  <strong>A modern, open-source NAS management platform for ARM and x86 Linux systems</strong>
</p>

<p align="center">
  <a href="https://github.com/und3f1ned/unos/actions/workflows/test.yml"><img src="https://github.com/und3f1ned/unos/actions/workflows/test.yml/badge.svg?branch=main" alt="CI"></a>
  <a href="https://github.com/und3f1ned/unos/releases/latest"><img src="https://github.com/und3f1ned/unos/actions/workflows/release.yml/badge.svg" alt="Release"></a>
  <a href="https://github.com/und3f1ned/unos/actions/workflows/audit.yml"><img src="https://github.com/und3f1ned/unos/actions/workflows/audit.yml/badge.svg?branch=main" alt="Audit"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-GPL--3.0--or--later-blue?style=flat-square" alt="License: GPL-3.0-or-later"></a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.11">
  <img src="https://img.shields.io/badge/FastAPI-009688?style=flat-square&logo=fastapi&logoColor=white" alt="FastAPI">
  <img src="https://img.shields.io/badge/SvelteKit-3-FF3E00?style=flat-square&logo=svelte&logoColor=white" alt="SvelteKit 3">
  <img src="https://img.shields.io/badge/Tailwind_CSS-4-06B6D4?style=flat-square&logo=tailwindcss&logoColor=white" alt="Tailwind CSS 4">
  <img src="https://img.shields.io/badge/Docker-2496ED?style=flat-square&logo=docker&logoColor=white" alt="Docker">
  <img src="https://img.shields.io/badge/Btrfs-FF6600?style=flat-square&logo=linux&logoColor=white" alt="Btrfs">
</p>

<p align="center">
  <a href="https://github.com/und3f1ned/unos/pkgs/container/unos-supervisor"><img src="https://img.shields.io/badge/GHCR_images-amd64_%7C_arm64_%7C_riscv64-2496ED?style=flat-square&logo=docker&logoColor=white" alt="Images on GHCR: amd64, arm64, riscv64"></a>
  <img src="https://img.shields.io/badge/tested_on-BPI--W3_(RK3588,_arm64)-green?style=flat-square" alt="Tested on the BPI-W3 (RK3588, arm64)">
  <img src="https://img.shields.io/badge/tested_on-BPI--F3_(SpacemiT_K1,_riscv64),_partly-yellow?style=flat-square" alt="Partly tested on the BPI-F3 (SpacemiT K1, riscv64)">
</p>

<p align="center">
  <a href="#features">Features</a> •
  <a href="#quick-start">Quick Start</a> •
  <a href="#documentation">Documentation</a> •
  <a href="#development">Development</a>
</p>

---

UNOS turns a Debian or Ubuntu machine into a NAS with a web interface. It runs as two Docker
containers on top of the existing system: nothing is reflashed or replaced.

## Features

- **Storage**: btrfs pools (single, RAID 0, 1, 1C3, 1C4 and 10; no RAID 5/6, for btrfs's write
  hole), import of an existing pool, volumes, snapshots and restores, scrub and balance, SMART
  with scheduled tests, disk spin-down.
- **Sharing**: SMB shares with per-user and per-group access, listed by Windows and macOS
  (WS-Discovery, Bonjour); NFS exports; a web file browser with a 30-day trash.
- **Apps**: an App Store of 69 curated apps, apps of several containers, memory and CPU limits,
  and the containers and images of Docker.
- **Security**: HTTPS from a built-in local CA, administrators, users and groups, TOTP two-factor
  sign-in with recovery codes, API tokens, sign-in rate limits, the host's firewall and an audit
  log.
- **System**: a dashboard and resource monitor, network settings, the host's name, time zone and
  logs, reboot, shutdown, suspend, scheduled power and Wake-on-LAN, a task scheduler, alerts by
  e-mail, Telegram, webhooks, Discord and Slack, and a Prometheus endpoint.
- **Backups**: encrypted configuration backups, and off-device copies with restic to a disk,
  S3, SFTP or a REST server, each checked by a test restore.
- **Updates**: each release publishes its images for amd64, arm64 and riscv64 on GHCR;
  `update.sh` installs the newest with a backup first and rolls back if it does not start.
- The interface is in English and Russian, in a light or dark theme, and works on a phone.

Built and waiting for a check on real hardware: UPS monitoring through NUT, bonds, VLANs and
bridges, iSCSI targets, remote access over WireGuard, and installing updates from System >
Updates. [feature-list.json](feature-list.json) tracks every feature and what it still needs.

## Quick Start

```bash
curl -fsSL https://raw.githubusercontent.com/und3f1ned/unos/main/deploy/install.sh | sudo bash
```

Or clone the repository to `/opt/unos` and run `sudo ./install.sh` in `deploy/`: it checks out
the newest release and pulls its images. Then open `https://YOUR_SERVER_IP` (plain `http://`
redirects). Expect a certificate warning: UNOS issues its certificate from its own local CA
([HTTPS and the Certificate Warning](docs/INSTALL.md#https-and-the-certificate-warning)).

There are no default credentials: the first visit creates the administrator account. Do this
right after installing, because until then anyone who can reach the page can create it.

### Requirements

- Debian 11 to 13, Ubuntu 22.04 or 24.04, or Raspberry Pi OS 64-bit
  ([the details](docs/INSTALL.md#prerequisites)); Docker, which `install.sh` installs if missing.
- amd64, arm64 or riscv64 (Debian 13 or Ubuntu 24.04); 1 GB of RAM and 8 GB for the system at
  least. ARMv7 works with limits and no release images ([ARMv7](docs/INSTALL.md#armv7)).
- Tested on the Banana Pi [BPI-W3](docs/INSTALL.md#banana-pi-bpi-w3-rockchip-rk3588-arm64)
  (RK3588) and, in part, the [BPI-F3](docs/INSTALL.md#banana-pi-bpi-f3-spacemit-k1-riscv64)
  (SpacemiT K1).
- A browser no older than Chrome or Edge 111, Firefox 128 or Safari 16.4.

### Updating and uninstalling

```bash
sudo /opt/unos/deploy/update.sh      # or System > Updates
sudo /opt/unos/deploy/uninstall.sh   # keeps the pools and their data
```

[Upgrading](docs/INSTALL.md#upgrading) and [Uninstalling](docs/INSTALL.md#uninstalling)
describe both.

## Architecture

```
Browser ──HTTPS, wss──▶ unos-webui (SvelteKit behind Caddy; 443, 80 redirects)
                              │ /api/*, /ws/*
                              ▼
                        unos-supervisor (FastAPI, SQLite in /data; 8080, internal)
                              │ the Docker socket, the host's commands
                              ▼
                        Debian or Ubuntu host: btrfs, Samba, smartmontools, Docker
```

## Documentation

| Document | Description |
|----------|-------------|
| [Installation Guide](docs/INSTALL.md) | Installing, upgrading, uninstalling, troubleshooting |
| [User Guide](docs/USER_GUIDE.md) | Every page of the web interface |
| [Recovery](docs/RECOVERY.md) | Reach the pools from another Linux machine; reset an administrator who cannot sign in |
| [Changelog](CHANGELOG.md) | Release notes |
| [Decision Records](docs/adr/README.md) | Why UNOS is built the way it is |

## Development

```bash
make setup   # once per checkout: venvs, npm packages, Playwright, git hooks
make check   # the gate that CI runs: backend tests, lint, svelte-check, the build
```

[CONTRIBUTING.md](CONTRIBUTING.md) describes how work flows from an issue to a pull request,
[AGENTS.md](AGENTS.md) the commands and rules for developers and coding agents, and
[docs/TESTING.md](docs/TESTING.md) the tests, the local stand-in and CI. Problems go to the
[issue tracker](https://github.com/und3f1ned/unos/issues), vulnerabilities privately as
[SECURITY.md](SECURITY.md) describes. Everyone who takes part follows the
[Code of Conduct](CODE_OF_CONDUCT.md).

## License

Copyright (c) 2024–2026 UNOS Contributors

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.

[LICENSE](LICENSE) holds the text of the GNU General Public License, version 3: UNOS is
GPL-3.0-or-later.
