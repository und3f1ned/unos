# Changelog

All notable changes to UNOS will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-02-05

### Added

#### Storage Management
- BTRFS storage pools with RAID 0/1/5/6/10 profiles
- Volume (subvolume) management within pools
- SMART disk health monitoring and diagnostics
- Snapshot creation, restoration, and scheduling
- Pool scrubbing with scheduled integrity checks
- SMART trend tracking with predictive failure alerts

#### File Sharing
- SMB/CIFS shares for Windows, macOS, and Linux
- NFS exports for Unix/Linux systems
- Web-based file browser with upload, download, and management

#### Application Platform
- App Store with 70+ curated self-hosted applications
- Docker container lifecycle management
- Container log streaming via WebSocket
- Image pull, list, and removal

#### System Monitoring
- Real-time dashboard via WebSocket (CPU, memory, network, disk I/O)
- Process monitor with per-process resource usage
- Temperature monitoring from hardware sensors
- Per-interface network statistics with history graphs
- Prometheus metrics endpoint for external monitoring

#### Security
- Multi-user support with admin and regular roles
- JWT-based authentication with secure token handling
- Two-factor authentication (TOTP) with authenticator app support
- Recovery codes for 2FA account recovery
- API tokens for programmatic access
- Rate limiting on authentication endpoints (5 attempts/minute)
- Input validation and sanitization across all endpoints
- Audit logging for all administrative actions

#### Notifications
- Email notifications via SMTP
- Telegram bot notifications
- Discord webhook notifications with embeds
- Slack webhook notifications
- Custom webhook support for any service

#### Task Scheduler
- Automated snapshot creation with retention policies
- Scheduled BTRFS scrub operations
- SMART test scheduling
- Configuration backup automation
- Custom command scheduling

#### Network Management
- Network interface management
- DNS configuration viewing
- Hostname management
- Routing table display

#### Power Management
- Remote reboot, shutdown, and suspend
- Wake-on-LAN configuration
- UPS monitoring via NUT integration
- Auto-shutdown on low battery

#### Backup & Recovery
- Configuration backup and restore
- AES-256 encrypted backup support
- Scheduled backup retention

#### System Updates
- GitHub-based update checking and installation
- Rollback support for failed updates
- Update history tracking

### Security Hardening
- Command injection prevention in scheduler
- Path traversal protection in file operations
- Secure subprocess execution without shell=True
- CSRF-safe API design
- Secure default configurations

### Developer Features
- Comprehensive unit test suite with mocked device operations
- E2E test suite with Playwright
- CI/CD pipeline with GitHub Actions
- Deviceless test cycle for development without hardware

## [Unreleased]

### Planned
- Per-user share permissions (ACLs)
- Disk spindown management
- Container resource limits (CPU/memory)
- Network bonding and VLAN support
- Database migrations with Alembic

---

[1.0.0]: https://github.com/und3f1ned/unos/releases/tag/v1.0.0
[Unreleased]: https://github.com/und3f1ned/unos/compare/v1.0.0...HEAD
