# Changelog

All notable changes to UNOS will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Security and robustness hardening. Updating needs a few steps once: read the
upgrade notes and [Updating from 1.0.0 or Earlier](docs/INSTALL.md#updating-from-100-or-earlier).

### Upgrade notes (breaking changes)
- **HTTPS only.** The web interface and API are served on `https://` (port 443)
  with a certificate from Caddy's local CA, so browsers warn until the CA is
  trusted. Plain HTTP on port 80 only redirects, except `/api/v1/health`. The
  certificate is issued for `UNOS_HOSTNAME` (default `unos`).
- **Everyone is signed out once.** The web interface moves to a new address
  (`https://`), and installations without `JWT_SECRET` switch to a secret that
  the supervisor generates in the data volume (`/data/jwt_secret`). The
  supervisor refuses to start when `JWT_SECRET` is a published placeholder such
  as `change-me-in-production`.
- **No default credentials.** The first visit creates the administrator
  account.
- **Administrator-only API.** Regular accounts and their API tokens get HTTP 403
  from storage, shares, containers, apps, files, snapshots, notifications,
  scheduler, power, network, logs, health, monitor, backup, updates, audit,
  firewall, certificates and user management, and from `/api/v1/health/full`
  and `/api/v1/health/prometheus`. The web interface shows them only Dashboard
  and Account.
- **Pool creation.** `POST /api/v1/storage/pools` requires `confirm_name`
  (equal to `name`, otherwise 400) and `force: true` for disks that already
  contain data (otherwise 409). With `force: true`, `device_identities` must
  name every selected device (otherwise 400). System disks and disks in use
  are refused (409).
- **WebSocket authentication.** Clients send `{"type": "auth", "token": "..."}`
  as the first message, within 10 seconds; a `?token=` query parameter is
  refused (close code 4401). The container log stream requires an administrator
  (4403).
- **Dependencies.** `python-jose` and `passlib` are replaced by `PyJWT` and
  `bcrypt`. Existing password hashes keep working; new passwords longer than
  72 bytes are rejected (422). Test tools moved to
  `supervisor/requirements-dev.txt`.
- **Pools must be mounted once.** Pools created by earlier releases were never
  added to the host's `/etc/fstab` and are not mounted at boot. After updating,
  Storage shows them as "Not mounted" and their `/mnt/pools/<name>` directory
  is write-protected (`chattr +i`); press Mount once.
- **Background tasks run.** The scheduler, the SMART and RAID monitors and the
  UPS monitor never started before. Enabled scheduled tasks, including custom
  commands (run as root), now run: review them before updating.
- **Apps on the system disk.** Apps installed by earlier releases keep their
  data in `/mnt/pools/default` on the system disk. UNOS marks them and no longer
  starts them; reinstall them on a pool.
- **Docker network.** The compose network uses the fixed subnet
  `172.31.254.0/24` and must be recreated once (`docker compose down`). If the
  subnet is in use, change every `172.31.254.x` in `deploy/docker-compose.yml`
  together.

### Security
- Caddy serves HTTPS from its local CA, redirects plain HTTP (308) and sends a
  Content Security Policy (plus SvelteKit's hash-based policy for the inline
  bootstrap script), `X-Frame-Options: DENY`, `X-Content-Type-Options`,
  `Referrer-Policy: no-referrer` and `Permissions-Policy`. No HSTS, so users
  can always continue past the certificate warning.
- The JWT signing secret is a random 256-bit value created on first start (mode
  0600) unless `JWT_SECRET` is set; published placeholders stop the startup.
  Tokens must carry `exp` and `sub`, and only HS256/HS384/HS512 are accepted.
- Administrator-only routers (`require_admin`). Hostname, timezone, reboot and
  shutdown, the firewall, certificates, the audit log and user management also
  require an administrator. The last administrator cannot be demoted or deleted.
- WebSocket tokens no longer appear in URLs (and so in proxy and server logs).
  Sockets check them like HTTP requests do (expiry, disabled API tokens,
  deleted users), at the handshake and every minute while they are open, and
  close when the token is no longer valid or the user lost the required role.
- First-run setup creates exactly one administrator, also when requests arrive
  at the same time.
- Uvicorn trusts `X-Forwarded-For` only from the webui container's fixed
  address (`FORWARDED_ALLOW_IPS`). The login rate limit, the login history and
  the audit log can no longer be given a forged client address.
- The file manager is confined to the pools directory and never follows
  symlinks (every path component is opened with `O_NOFOLLOW`); pool roots
  cannot be renamed, moved or deleted, and upload names are reduced to a single
  file name.
- Backup restore checks archives (size limits, allowed regular files only; no
  links, devices, absolute paths or `..`), verifies the restored database
  before it replaces the live one through SQLite's backup API (keeping a copy),
  and regenerates `smb.conf` from validated shares instead of installing the
  archived file.
- `smb.conf` and `/etc/exports` values are validated against directive
  injection, share and export paths must not pass through symlinks, and
  `smb.conf` is replaced atomically after `testparm` accepts it.
- Snapshot paths and names are validated; `.snapshots` must be a real
  directory.
- Certificate names (generated or uploaded) must be a host name or an IP
  address before they name files or go into `openssl` arguments.
- Scheduled tasks are administrator-only and validated when saved and again
  before they run.
- App installs refuse well-known placeholder secrets and keep app data inside
  the selected mounted pool (no `..`, symlinks or nested writable folders).
- Input validators anchor their patterns with `\Z`: a trailing newline no longer
  passes.
- The log viewer no longer renders log text as HTML.
- The 2FA QR code is drawn in the browser; the TOTP secret was sent to a
  third-party QR code service before.
- Python dependencies are pinned and updated (FastAPI 0.162, Starlette 1.10,
  Uvicorn 0.54, Pydantic 2.14, SQLAlchemy 2.1, cryptography 50, docker 7.2,
  requests 2.34); the web UI image installs exactly `package-lock.json`
  (`npm ci`).
- `update.sh` no longer stashes local changes, which silently replaced a
  deployed change with the upstream code. Its backups (they contain the JWT
  secret) are readable by root only.
- The test stack no longer uses a fixed JWT secret and publishes its ports on
  127.0.0.1 only.

### Added
- Mount and Unmount for pools (`POST /api/v1/storage/pools/{name}/mount`,
  `.../unmount`). Pools are mounted on the host by filesystem UUID, with a
  managed `/etc/fstab` entry (`nofail`, mounted before Docker starts); the
  original file is kept as `/etc/fstab.unos-backup`.
- At startup the supervisor mounts managed pools the host did not mount, starts
  the apps that could not start without them (in the background; an app whose
  checks take longer than 60 seconds stays stopped, as do the pool's remaining
  apps), and write-protects the mountpoints of pools that are not mounted, so
  nothing fills the boot disk.
- Pool health (healthy, degraded, not mounted, error), missing devices, device
  error counters, read-only state, and whether btrfs forced the filesystem
  read-only after an error (`forced_read_only`).
- RAID alerts name the cause: pool not mounted, error counters unreadable,
  missing devices, a read-only mount, or a filesystem btrfs made read-only
  after an error (check `dmesg`); the last one is also reported when the pool
  already had another problem. Disk details come from the host's view:
  system disk, in use, existing data, partitions, mountpoints, pool membership,
  WWN.
- Pool creation safety: typed confirmation, an explicit erase confirmation for
  disks with data, and refusal of system disks, disks in use and devices whose
  serial, WWN or size changed since they were selected (`device_identities`).
- App installs choose the storage pool for the app's data
  (`GET /api/v1/apps/pools`); the installed apps list shows the host folders an
  app really uses and warns about data outside mounted pools.
- `UNOS_HOSTNAME` sets the name on the HTTPS certificate.
- `supervisor/src/host.py` runs commands and writes files in the host's
  namespaces (nsenter into PID 1); `UNOS_HOST_EXEC=local` runs them directly.
  At startup the supervisor logs which of the programs it runs there (from
  btrfs-progs, samba-common-bin, util-linux, ...) the host lacks.
- `update.sh`: consistent database backup (SQLite online backup API) plus a
  copy of the data volume, `:previous` image tags, health check over HTTP and
  HTTPS, and automatic rollback, also when the update is interrupted (Ctrl+C,
  a closed SSH session).
- Backend tests for authorization, WebSocket authentication, the secret
  bootstrap, disk safety, pool persistence, file manager and backup restore
  security, scheduler validation, apps and the app lifespan; E2E tests for HTTPS
  and security headers, roles and the WebSocket handshake.
- `supervisor/requirements-dev.txt`; `.dockerignore` files for both images.

### Changed
- Regular users see only Dashboard and Account (own password, two-factor
  authentication, login history).
- The web interface ends an expired session once (HTTP 401 or WebSocket close
  code 4401), reconnects the stats socket with exponential backoff, and shows
  API validation errors as messages.
- The compose network has a fixed subnet and fixed container addresses.
- `update.sh` stops when the checkout has local changes or is on another
  branch, builds the new images while UNOS keeps running, restarts with
  `docker compose down` and `up -d`, and prunes old images only after the
  health check passes. It refuses to start while another `update.sh` runs
  (flock on `/run/unos-update.lock`).
- `install.sh` no longer writes a JWT secret to `/etc/unos/secrets.env`, and
  prints the HTTPS address, the certificate warning and the first-run steps.
- Starting or restarting an app, from the App Store or the Containers page,
  first checks that its data folders are on a mounted pool.
- Blocking work (storage and share operations, smartctl, bcrypt, container log
  streams, e-mail) runs off the event loop; e-mail and Telegram notifications
  time out instead of hanging.
- The deviceless test stack runs over HTTPS (`https://localhost:3443`; HTTP on
  3080 for the redirect and the health check) in its own compose project
  (`unos-test`), and `scripts/test-cycle.sh` creates its test account.
- The `test` profile's Playwright runner (`webui/Dockerfile.test`) uses the
  image of the pinned Playwright version (1.57.0) and creates the test account
  itself.

### Fixed
- Pools were never added to the host's `/etc/fstab` (the entry was written
  inside the supervisor container), so they were not mounted after a reboot.
- Disks were inspected from inside the container, where the host's system disk
  looked unused and could be selected for a pool.
- Mounting a pool over a directory with files hid them while they kept using
  the boot disk; this is now refused.
- Removing a pool that is in use is refused (409); the failed unmount used to be
  ignored.
- The scheduler, the health monitors and the UPS monitor never started (FastAPI
  ignores router startup hooks when the app defines a lifespan).
- SMART trend alerts could never fire. The SMART monitor no longer wakes
  sleeping disks or blocks the event loop.
- The RAID monitor and the full health and Prometheus endpoints treated every
  directory under `/mnt/pools` as a pool.
- `/api/v1/health/full` checked Docker with the `docker` CLI, which the
  supervisor image does not have; it now asks the Docker API, off the event
  loop. An unreachable Docker API is now reported as `error` and makes the
  overall status `degraded`; it was always `unavailable` before and never
  changed the status.
- `GET /api/v1/logs/sources` failed (500) when the supervisor runs without
  `systemctl` (the deviceless test image, `supervisor/Dockerfile.test`).
- A very long API token lifetime caused a server error; `expires_in_days` must
  now be between 1 and 3650 (422 otherwise).
- The container log stream froze the whole API while the container wrote no
  output.
- A failed app install no longer leaves a dead container that blocks a retry.
- The System page follows the URL hash, including Back and Forward.

### Planned
- Per-user share permissions (ACLs)
- Disk spindown management
- Container resource limits (CPU/memory)
- Network bonding and VLAN support
- Database migrations with Alembic

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

---

[Unreleased]: https://github.com/und3f1ned/unos/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/und3f1ned/unos/releases/tag/v1.0.0
