# Changelog

All notable changes to UNOS will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Security and robustness hardening, fixes for broken user flows, and a few new
features (SMB access per user and per share, volumes, copy and move in the file
manager, encrypted backup uploads, health checks, CI checks).

### Security
- Samba refuses every SMB password change that a computer asks for over the
  network (Windows' **Change a password**, `smbpasswd -r`), so that SMB
  passwords change only in UNOS, under its password rules: the line
  `check password script = /bin/false` of the `[global]` section that UNOS
  writes, also in the first smb.conf of `install.sh`.
- After 5 wrong SMB passwords in a row, Samba locks the SMB account for 5
  minutes, for every computer and for the right password too; the two
  passwords before the current one do not count. The supervisor sets Samba's
  lockout policies at its start when they differ. Anyone on the network who
  knows a username can lock that SMB account in this way.
- The supervisor serves at most 128 open connections (503 beyond them) and
  64 WebSockets, 16 of them waiting for their sign-in, and Caddy refuses a
  header block over 16 KB (431).
- Sessions end on the server: **Logout** ends the session, a password change
  the user's other sessions, and a password set by an administrator, the
  deletion of the user, the emergency `reset-admin`, a restore (but for the
  administrator who restored) and the new **End sessions** button of the
  **Users** page all of them. Tokens from before the
  update are refused: everyone signs in once more.
- The audit log records a user's own sign-out, password change (with an SMB
  account entry when the SMB password changed too) and turning two-factor
  authentication on or off (off as a warning), with the user, the address
  and the browser; a refused attempt writes no entry. When the entry cannot
  be written, the change still answers and the supervisor's log has it.
- Each audit entry carries the hash of the one before it. An entry edited in
  the log file, and the entry after one that was removed, are marked **Chain
  broken** on the Audit page and in `GET /api/v1/audit/logs`; when the newest
  entries are removed or rewritten while the supervisor runs, a critical
  entry says so.
- The CSV export of the audit log puts a `'` before a value that a spreadsheet
  would read as a formula (one that starts with `=`, `+`, `-`, `@`, a tab or a
  carriage return).
- Caddy serves HTTPS from its local CA, redirects plain HTTP (308; only
  `/api/v1/health` also answers on port 80) and sends a Content Security
  Policy (plus SvelteKit's hash-based policy for the inline bootstrap script),
  `X-Frame-Options: DENY`, `X-Content-Type-Options`,
  `Referrer-Policy: no-referrer` and `Permissions-Policy`. No HSTS, so users
  can always continue past the certificate warning. Scripts and monitoring
  that call the API use `https://` and trust that CA (or skip the certificate
  check): tools such as curl fail on it instead of warning.
- The JWT signing secret is a random 256-bit value created on first start (mode
  0600) unless `JWT_SECRET` is set; published placeholders stop the startup.
  Tokens must carry `exp` and `sub` and be signed with the configured
  algorithm (HS256; `UNOS_JWT_ALGORITHM` can only be HS256, HS384 or HS512).
- Administrator-only routers (`require_admin`): regular accounts and their API
  tokens get HTTP 403 from storage, shares, containers, apps, files, snapshots,
  notifications, scheduler, power, network, logs, health, monitor, backup and
  updates, and from `/api/v1/health/full` and `/api/v1/health/prometheus`.
  Hostname, timezone, reboot and shutdown, the firewall, certificates, the
  audit log and user management also require an administrator. The last
  administrator cannot be demoted or deleted.
- WebSocket clients authenticate with their first message,
  `{"type": "auth", "token": "..."}`, within 10 seconds; a `?token=` query
  parameter is refused (close code 4401), so tokens no longer appear in URLs
  (and so in proxy and server logs). The container log stream requires an
  administrator (4403). Sockets check tokens like HTTP requests do (expiry,
  disabled API tokens, deleted users), at the handshake and every minute while
  they are open, and close when the token is no longer valid or the user lost
  the required role.
- There are no default credentials: first-run setup (the `/setup` page,
  `POST /api/v1/auth/setup` while no user exists) creates exactly one
  administrator, also when requests arrive at the same time.
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
  archived file. A database that gives one SMB uid to two accounts, which only
  a crafted backup can hold, is refused (400) before anything changes; it used
  to be restored, and turning SMB on failed for the second account.
- `smb.conf` and `/etc/exports` values are validated against directive
  injection, share and export paths must not pass through symlinks, and
  `smb.conf` is replaced atomically after `testparm` accepts it (without that
  check, and with a warning in the log, when the host has no `testparm`).
- Snapshot paths and names are validated; `.snapshots` must be a real
  directory.
- Certificate names (generated or uploaded) must be a host name or an IP
  address (the CN of an uploaded certificate may also be a wildcard such as
  `*.example.com`) before they name files or go into `openssl` arguments.
- Scheduled tasks are administrator-only and validated when saved and again
  before they run.
- App installs refuse well-known placeholder secrets and keep app data inside
  the selected mounted pool (no `..`, symlinks or nested writable folders).
- Input validators anchor their patterns with `\Z`: a trailing newline no longer
  passes.
- The log viewer no longer renders log text as HTML.
- The 2FA QR code is drawn in the browser; the TOTP secret was sent to a
  third-party QR code service before.
- `PyJWT` and `bcrypt` replace `python-jose` and `passlib`; new passwords
  longer than 72 bytes are refused (422), and a password is checked as given:
  one longer than 72 bytes never signs in.
- Python dependencies are pinned and updated (FastAPI 0.162, Starlette 1.10,
  Uvicorn 0.54, Pydantic 2.14, SQLAlchemy 2.1, cryptography 50, docker 7.2,
  requests 2.34); the web UI image installs exactly `package-lock.json`
  (`npm ci`).
- The images start from base images pinned by tag and digest
  (`python:3.11-slim`, `node:22-alpine`, `caddy:2-alpine`, and the Playwright
  image of the test stack), so a build no longer takes whatever a tag names
  that day. Newer digests come as pull requests, from Dependabot or from the
  monthly maintenance pass.
- `update.sh` no longer stashes local changes, which silently replaced a
  deployed change with the upstream code. Its backups (they contain the JWT
  secret) are readable by root only.
- The test stack no longer uses a fixed JWT secret and publishes its ports on
  127.0.0.1 only.
- Request bodies are limited before the credentials are checked. FastAPI reads
  a request body, and Starlette spools an upload to a temporary file, before
  the login and administrator checks run, so anyone who could reach the API
  could make the supervisor hold a body of any size in memory, or an upload of
  any size on disk, before it answered 401. Every request body is now limited
  to 1 MiB (HTTP 413, also for chunked bodies), except the file and backup
  uploads, which read their form only after the checks: file uploads have no
  limit, backup uploads one of 257 MiB (the 256 MiB archive limit plus the
  form around it). An upload form with more than one file gets 400.
- SMART data and self-tests run only on whole disks of the host, as the
  Storage page lists them (`sda`; the health endpoints and scheduled tests
  also take `/dev/sda`), in
  `GET /api/v1/storage/disks/{name}/smart`, `GET /api/v1/health/disk/{device}`,
  `POST /api/v1/health/disk/{device}/test` and scheduled SMART tests, which
  check the disk when they are saved and each time they run. The health
  endpoints used to run smartctl on any device under `/dev`, such as a
  partition, the boot SD card or `/dev/null`, and the others on any name of
  the right format, such as `sda1`, `md0`, `null` or `st0` (a tape drive,
  which rewinds when it is closed). Anything else now gets HTTP 400, and a
  scheduled test fails without running smartctl; HTTP 500 when the host's
  disks cannot be listed. An edit of a SMART task that keeps its disk is
  saved also while the disk is disconnected.
- Deleting a volume could follow a symbolic link that an app or an SMB user
  had put in the pool under the volume's name, and delete another subvolume.
  Volumes are now deleted by the subvolume id from the pool's own subvolume
  list, checked against the filesystem first (409 when they differ, for
  example for a subvolume whose name holds a line break).
- CI: the workflow's token can only read the repository
  (`permissions: contents: read`), and the checkouts no longer leave it in
  the git configuration (`persist-credentials: false`), where the build or
  test code of a dependency could read it.
- WebSocket messages are limited to 64 KiB (uvicorn `--ws-max-size 65536` in
  both supervisor images; uvicorn's default is 16 MiB). Uvicorn reads a whole
  message into memory before the supervisor gets it, also the first message
  of a socket, which comes before the client is authenticated. So anyone who
  could reach `/ws/*` could make the supervisor read 16 MiB into memory for
  each socket they opened, even with a compressed message of about 16 KB. A
  larger message now closes the socket (code 1009) before the supervisor
  gets it (through Caddy sometimes without a code, when it closes while the
  client is still sending); the web interface sends only its auth message,
  well under 1 KiB.
  WebSocket compression (permessage-deflate) is off as well
  (`--ws-per-message-deflate false`): uvicorn inflates every message that
  arrives in one read from the socket before the supervisor reads the
  first, so many compressed messages under the limit (86 bytes on the wire
  for 65,000) still let one socket make the supervisor hold about 190 MiB.
- Creating a volume with a quota set the quota by path
  (`btrfs qgroup limit <size> <path>`), so it went to whatever had the
  volume's name right after the create: an existing subvolume renamed to that
  name, or the subvolume that a symbolic link with that name pointed to.
  Anyone who can rename or create entries in the pool's top folder could do
  that, such as an app or a guest of a writable SMB share of the whole pool.
  The quota is now set by subvolume id
  (`btrfs qgroup limit <size> 0/<id> <mountpoint>`), and only on a subvolume
  with the volume's name that is newer than every subvolume that existed
  before the create; otherwise no quota is set, nothing that existed before
  is removed, and the request answers 409.
- Hardened Samba `[global]`: no printing, no usershares,
  `invalid users = root`, NTLMv2 only, SMB2 or newer, access-based share
  enumeration, SMB3 POSIX extensions off. SMB answers only loopback, private
  (10/8, 172.16/12, 192.168/16), CGNAT/VPN (100.64/10), link-local and IPv6
  ULA addresses (`hosts allow`): clients must use a LAN address of the server,
  and public IPv4 and global IPv6 addresses are refused. Signing is required
  while no guest share exists (every SMB2 or later client can sign); while a
  guest share exists it is not required for any connection, because guest
  connections cannot be signed and clients may refuse to connect as a guest to
  a server that requires signing. Anonymous access is refused
  (`restrict anonymous = 2`) while no guest share exists; with a guest share,
  the list of account names stays hidden (`restrict anonymous = 1`).
- Fail-safe share rendering: every access line is always written, and a share
  without access is closed with `valid users = root` and `available = no`.
- Removing or lowering access to a share (also turning its guest access off),
  changing its folder, and renaming or deleting it disconnect every open
  connection to that share; clients reconnect. Adding access disconnects
  nothing. Turning SMB off for a user, deleting or promoting the user, or
  changing the user's password ends the user's open SMB connections.
- Pool roots and app data can no longer be shared. A share must be a folder
  inside a pool that UNOS manages, or a volume: pool roots (they hold app data
  in `apps/`, the volumes and the snapshots), `<pool>/apps` and everything
  below it, `.snapshots`, folders outside the pools that UNOS manages, and
  folders that overlap another share's folder are refused. Such shares that
  exist already (imported or restored) are kept without access and are not
  served until their folder is changed on the Shares page.
- smb.conf is written atomically and durably.
- SMB passwords reach Samba on stdin only, never on a command line.
- SMB host accounts have a dedicated uid range (70000–79999) and the group
  `unos-smb` (gid 70000), outside the ranges that `useradd`, `groupadd` and
  systemd allocate.
- Samba keeps its cache in RAM (`/run/samba`, from Samba's next restart) and
  logs to syslog at level 1 instead of one log file per client. smbd and nmbd
  still append their own level-1 messages to `/var/log/samba/log.smbd` and
  `log.nmbd` (Samba always keeps that file for its daemons).
- A share folder is created only below folders that only root can change, and
  only through the pool's own mount: when the pool is unmounted or its
  mountpoint was removed meanwhile, the request answers 409 instead of
  creating the folder on the system disk, and a pool's mountpoint is never
  created again. Whoever can change a folder above a share's folder can put
  another folder in its place, and Samba and Repair permissions would then use
  that one: below such a folder UNOS creates no share folder and refuses
  Repair permissions (HTTP 409). The message names the folder and the command
  to run on the server, for example `sudo chmod 755 /mnt/pools/data` (or
  `chmod go-w` where 755 would let other accounts read the folder, and
  `chown root:root` for a folder of another owner). A share of an existing
  folder below it is accepted with a warning, and the Shares page lists every
  served share below such a folder. ACL entries that let another account
  write, and a share folder of another account in a folder with the sticky
  bit, count the same. UNOS changes the mode of no existing folder itself.
- Turning SMB on for an account (with a password: on the Account page, in the
  Users page's edit dialog, or when a user is created) is refused with HTTP
  409 until the supervisor has installed the smb.conf it generates (a new user
  is then created without SMB, with a warning): Samba would still use the old
  file, which may give every Samba account access to a share. The status panel
  of the Shares page says why: "Shares were not imported from smb.conf: ..."
  (UNOS writes no smb.conf until the import has worked) or "smb.conf was not
  updated: ...". Allowing SMB and turning it off are not refused.
- Two-factor codes work once: a code that was accepted at a login, when
  turning SMB on, or when turning two-factor authentication on or off is
  refused when it is used again ("This two-factor code was already used; wait
  for the next one"), so the code of a login cannot also turn SMB on. The
  codes used are remembered in memory only: a code used shortly before the
  supervisor restarts can be used once more until it expires (a code lasts 30
  seconds).
- Login requests are limited (a username of at most 255 characters, a password
  of at most 1024, a two-factor code of at most 16), and an audit entry keeps
  at most 128 characters of the username, 256 of the User-Agent and of the
  resource, and 1024 of the details. A failed login, which needs no account,
  could add megabytes to the audit log, which every audited action used to
  read and write in full.
- The answers of a regular user's own SMB changes (Account page) and password
  changes carry no host output, paths or names of shares the user cannot
  open: their warnings, and their errors when a command on the NAS failed, are
  fixed texts; the details go to the supervisor's log.
- A new user never inherits the SMB access of a deleted user who had the same
  id. After a restore, an account whose SMB user id belongs to another account
  on this server must turn SMB on again, and gets a new user id.

### Added
- Off-device copies: **System** → **Backup & Restore** copies the
  configuration and the pools' folders with restic, encrypted with a password
  that only the NAS keeps, to a folder on a disk mounted below `/mnt` that is
  not the system disk, or to an S3-compatible store, with upload and download
  limits. Saving tests the target and makes its repository; a task of the new
  type **Off-device Copy** (daily at 03:00 with one button) or **Run now**
  copies, removes old snapshots, checks the repository and tests a restore of
  the configuration. restic leaves out a file deleted before it opened it; one
  that vanishes after it opened it is a note; files that cannot be read fail
  the copy once its other steps have run. While a copy or a restore runs, its
  pools cannot be unmounted or removed, nor its volumes deleted. One copy
  repository serves one installation.
  Snapshots restore the configuration into the backup list
  (`offsite_<time>.tar.gz`) and the files into a new folder of a pool. The
  supervisor image contains restic.
- Alerts for the problems of the NAS, and a health banner: a managed pool not
  mounted (also when none of its disks is connected), degraded (scrub errors
  included), read-only after an error or 90% full (95%: an error; it stays so
  until below 85%), a disk that fails its SMART health self-assessment or its
  latest self-test, and a scheduled task whose last run failed or that missed
  a run since it was created, enabled or given another time. Each problem is
  alerted once, through every enabled channel, webhooks, Discord and Slack
  included, and administrators see the problems above every page until they
  are gone. UNOS keeps them in memory: after a restart it alerts those still
  there once more. A managed pool's change of status is no longer alerted by
  the RAID monitor as well, and its new device errors not twice.
- **System** → **General** sets the host's name and time zone.
- The web interface works on a phone: below 768 px a menu button in the top
  bar opens the navigation, and every page, tab and dialog fits a 375 px wide
  screen without scrolling sideways. Wide tables scroll on their own, and a
  dialog taller than the screen scrolls to its buttons.
- NFS exports reach the host: UNOS writes its own file
  `/etc/exports.d/unos.exports`, runs `exportfs -ra` on the host, lists every
  export of every export file and changes only its own lines. An export is a
  folder of a mounted pool, for one local address or network, read-only or
  read-write, and is served only while its pool is mounted.
- Network discovery: macOS (Bonjour, through avahi-daemon) and Windows
  (WS-Discovery, through wsdd2) list the NAS, and NetBIOS is off; `install.sh`
  sets them up.
- Apps of linuxserver.io images can be installed to run in the group unos-smb,
  so that they read and write the share folder they are given.
- Storage: a btrfs pool that already exists on the host's disks can be
  imported without formatting them.
- An emergency runbook (`docs/RECOVERY.md`) that reaches a pool's data from
  another Linux machine, and `deploy/reset-admin.sh`, which resets the
  administrator's password and two-factor authentication from the host.
- The web interface in Russian: Language on the Account page switches every
  page between English and Russian, with Russian dates, numbers and plural
  forms. Messages from the server stay in English.
- File manager: Delete moves items into a trash of their pool or volume
  (`.unos-trash`); the Trash page restores them, deletes them for good or
  empties the trash, and items deleted 30 days ago go by themselves. Delete for
  good stays available in the dialog.
- System → Notifications: channels for e-mail (STARTTLS or SMTPS, with the
  server's certificate checked), Telegram and webhooks (generic, Discord,
  Slack), each with a test, and the history of the latest 100 notifications.
- System → Logs reads the host's journal, kernel messages and log files, newest
  first, with filters and search.
- Containers: an Images view that lists, pulls and removes the host's images.
- Scheduler: a custom task records its command's output and exit status, and
  schedules follow the host's time zone.
- SMB: unmounting or forgetting a pool takes its shares out of smb.conf at once,
  and the SMB status names a smb.conf that someone else replaced.
- File manager: Rename. Select one item and choose Rename to edit its name in
  its row; a name that exists is refused, and nothing is ever replaced.
- System → Disk Health opens each disk's SMART data: health, temperature,
  power-on hours, the last self-test and the attributes with their status.
  Disks whose SMART trend predicts a failure are marked Replace soon, with the
  reasons, there and on the dashboard.
- An Audit Log page for administrators: the entries as a timeline, searched and
  filtered by user, action, severity and time, and exported as JSON or CSV
  with the same filters.
- Two-factor recovery codes: a user with two-factor authentication generates
  ten codes on the Account page, shown once, and signs in with one of them in
  place of an authenticator code; each code works once.
- Backup tasks in System → Scheduler take a passphrase (at least 8
  characters), and their backups are encrypted with it.
- The Snapshots page offers the volumes of the mounted pools as well as the
  pools, and the Volumes dialog takes an optional quota.
- Disabling a network interface in System → Network asks first, and the
  interface comes back on by itself, with its addresses and routes, after 60
  seconds unless the change is kept.
- System → Power reboots, shuts down, cancels a pending shutdown and turns
  Wake-on-LAN on and off on the host, and shows the host's answer.
- Mount and Unmount for pools (`POST /api/v1/storage/pools/{name}/mount`,
  `.../unmount`). Pools are mounted on the host by filesystem UUID, with a
  managed `/etc/fstab` entry (`nofail`; at boot Docker waits up to 15 seconds
  for the pool's disks); the original file is kept as `/etc/fstab.unos-backup`.
- At startup the supervisor mounts managed pools the host did not mount, starts
  the apps that could not start without them (in the background; an app whose
  checks take longer than 60 seconds stays stopped, as do the pool's remaining
  apps), and write-protects the mountpoints of pools that are not mounted, so
  nothing fills the boot disk.
- Pool health (healthy, degraded, not mounted, error, or unknown when the
  mount table or the error counters cannot be read), missing devices, device
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
  `POST /api/v1/storage/pools` requires `confirm_name` (equal to `name`,
  otherwise 400) and `force: true` for disks that already contain data
  (otherwise 409). With `force: true`, `device_identities` must name every
  selected device (otherwise 400). System disks and disks in use are refused
  (409).
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
- Storage page: a Volumes dialog for each mounted pool lists, creates and
  deletes volumes, the btrfs subvolumes in the pool's top folder
  (`/mnt/pools/<pool>/<name>`) that can be shared on their own. Deleting a
  volume asks for its name. On a pool mounted read-only the dialog lists the
  volumes and says why they cannot be created or deleted. It notes that
  snapshots of the pool do not include the files in its volumes.
- File manager: Copy and Move. Select items, choose Copy or Move, open the
  destination folder, then choose "Copy here" or "Move here". Nothing is
  copied or moved when an item with the same name is there already.
- File manager: keyboard and screen reader access. Every file and folder name
  is a button: Enter or Space opens the folder or downloads the file, and
  after a folder opens the focus is on its `..` entry. Going up from the
  keyboard to the pools directory, which has no `..`, puts the focus on the
  first item, or on the list ("Folder contents") when there is none. Upload
  works from the keyboard too, and each checkbox is named after its item
  ("Select <name>"). The mouse works as before: a click on a row selects it,
  a double-click opens it.
- Encrypted backups (`.tar.gz.enc`) can be uploaded, for example after a
  download, and are stored as `uploaded_<date>_<time>[_<n>].tar.gz.enc`.
  Without the passphrase their content cannot be checked (the format has no
  header); it is checked when the backup is restored with its passphrase. A
  file uploaded under a `.tar.gz.enc` name that is gzip data, an unencrypted
  backup or any other gzip file, is refused (400) instead of failing only at
  its restore.
- Docker health checks for both containers: `docker ps` shows whether the
  supervisor answers `/api/v1/health`, and whether the web interface reaches
  it through Caddy. Failed checks of the supervisor count only after 10
  minutes (`start_period`), because it first mounts the pools the host has
  not mounted.
- CI checks the supervisor with ruff (`--select F,E9`: pyflakes findings and
  syntax errors) and pip-audit (`supervisor/requirements.txt`), and the web UI
  on Node.js 22 with `npm ci`, `npm run build`, `npm run check`
  (`svelte-kit sync`, then svelte-check `--threshold error`) and
  `npm audit --omit=dev --audit-level=high`. Each check fails the run when it
  finds a problem. ruff 0.17.3 and pip-audit 2.10.1, and every package they
  depend on, are pinned with their hashes in `.github/lint-requirements.txt`
  (`pip install --require-hashes`); svelte-check 3.8.6 is an exact dev
  dependency of the web UI, installed from `webui/package-lock.json` and run
  from `node_modules/.bin` (not through npx). A new push to a pull request
  cancels the run for the previous one. `docs/TESTING.md` has the commands
  for a local run and says how to update ruff and pip-audit.
- E2E tests that restore a backup ("Restoring backups" in
  `webui/tests/backup-restore.spec.ts`). They replace the database and
  `smb.conf` of the target, so they run only when `BASE_URL` is a local
  address (`localhost`, `127.0.0.1`, `::1`, `*.localhost`) or
  `E2E_ALLOW_RESTORE=1` is set, as the deviceless test stack's Playwright
  runner does. Otherwise they are skipped before any request.
- SMB access per user: an administrator allows it, and the user turns it on
  with their own password (and two-factor code, if they have one) on the
  Account page, or an administrator sets a password. Administrators cannot
  use SMB.
- Per-share access per user: read only, or read and write. New users have no
  access, and neither does a share imported without a `valid users` list.
  Samba accounts created by hand on the host (for example `nas`) reach only
  guest shares, which every Samba account can read: allow SMB for a UNOS user
  instead. A host or Samba account made by hand with the same name as a UNOS
  user (in any letter case) must be removed before that user can use SMB:
  `pdbedit -x -u NAME; userdel NAME`.
- Guest shares, read-only, with a warning. Guest access is off unless a share
  is a guest share; imported guest shares that were writable become
  read-only. `map to guest` is `Never`, or `Bad User` while a guest share
  exists.
- Repair permissions (Shares page): gives the existing files and folders of a
  share the group `unos-smb` and group read and write access
  (`POST /api/v1/shares/smb/{name}/repair-permissions`). It does not change
  ACL entries (`setfacl`): on a file or folder that has them, the group
  permissions it sets are the ACL's mask, so the users and groups named there
  keep their access and can get more, and `unos-smb` gets only what the ACL's
  entry for the owning group gives.
- On the Account page: the SMB status, "Shares you can use" and "How to
  connect".
- A status panel on the Shares page.

### Changed
- Backups, by hand, by backup tasks and for off-device copies, no longer hold
  the supervisor container's `/etc/fstab` and `/etc/netplan` files, which
  describe nothing of the host, and the options `include_pools` and
  `include_network` of `POST /api/v1/backup/create`, which added them, are
  gone. A restore ignores such files in a backup that holds them, and no
  longer warns that `fstab` is not restored.
- The supervisor's access log keeps only the requests that were refused or
  failed (answers of 400 and above): an HTTP request that succeeds, a health
  check included, no longer writes a line to the system disk through
  `docker logs`. Each WebSocket connection still logs two lines when it opens,
  and the supervisor's own log lines, warnings and errors are logged as before.
- Docker rotates the logs of the UNOS containers, and of the apps and containers
  that UNOS creates, at 10 MB and keeps 3 files: a container that logs much no
  longer fills the system disk.
- The stats message of `/ws/v1/events` sends each value once, in its object
  (`cpu`, `memory`, `network`, `disk_io`): the flat `cpu_*`, `memory_*` and
  `network_*` fields are gone, and so are the `disk_*` fields of the container's
  root and the `history` of the last two minutes, which no page read.
- The logs API reads every source through `GET /api/v1/logs/entries?source=...`:
  the old `GET /api/v1/logs/dmesg` and `/logs/journal` are gone (404).
- On a narrow screen the System tabs are listed above the open tab, the file
  manager leaves out the Modified column and scrolls as a whole page, and
  System → Logs shows each entry's time, level and program above its message.
- New folders and uploads of the file manager in a share folder get 2770 and
  0660 with the group unos-smb, so the share's SMB users can change them at
  once.
- Samba keeps its own log file in RAM (`/run/samba`), logs failed sign-ins
  through auth_audit to the journal, which System → Logs shows, and SMB
  passwords on a list of common passwords are refused.
- Sign-in works while the disk that holds `/data` is full: the login history
  and the audit line are written best effort.
- An API token's last use is stored at most once an hour, so automations do not
  write to the system disk on every request.
- The database `/data/unos.db` uses SQLite's write-ahead log with synchronous
  NORMAL: a small change, such as a sign-in's login-history row, appends its
  pages to `unos.db-wal`, mostly without a sync, where it created, synced and
  deleted a rollback journal and synced the database each time. A power cut can
  roll back the changes of the last 35 seconds, or of up to about 155 seconds
  while the log grows. The supervisor copies the log into `unos.db` when it
  stops, backups stay one file (`update.sh` keeps `unos.db-wal` beside the copy
  of a supervisor that was not running), a backup whose database has pages of
  another size is refused, and `docs/RECOVERY.md` reads a copy of the
  database's files. A `unos.db` put back by hand needs the `unos.db-wal` and
  `unos.db-shm` beside it deleted first (`docs/INSTALL.md`).
- Docker calls of the containers API run off the event loop with timeouts.
- Scheduled backups are always encrypted (AES-256-GCM) with their task's
  passphrase and are named `auto_backup_<date>_<time>.tar.gz.enc`; a restore
  asks for the passphrase.
- `POST /api/v1/auth/login` takes `recovery_code` in place of `totp_code`; the
  stub `POST /api/v1/auth/recovery-codes/verify` is gone.
- The audit log API lists, counts and exports with one set of filters,
  including a text search (`search=`); an end bound includes the whole unit it
  names (a date its whole day).
- `GET /api/v1/snapshots/` needs the pool or volume (`subvolume=`).
- `GET /api/v1/system/info` names the host's boot time `boot_time`, in seconds
  since the epoch; its field `uptime`, which held that time, is gone.
- System → Network shows each interface's IPv6 addresses, link speed and
  whether it is disabled, and the IPv6 routes.
- System → Updates shows the installed version and points to
  `sudo /opt/unos/deploy/update.sh`; it has no Install or Rollback buttons.
- The audit log is append-only: each entry is one JSON line in
  `/data/audit/audit.jsonl`, appended in a worker thread without rewriting
  what is already there, so an entry costs the same however long the log is.
  The log rotates at 1 MiB and keeps 4 older files (about 5 MiB); a damaged
  line is skipped instead of emptying the log; clearing before a date
  rewrites at most one file and keeps every newer entry if it fails. The old
  `/data/audit_log.json` is not read.
- Regular users see only Dashboard and Account (own password, two-factor
  authentication, login history).
- The web interface ends an expired session once (HTTP 401 or WebSocket close
  code 4401), reconnects the stats socket with exponential backoff, and shows
  API validation errors as messages.
- The compose network has a fixed subnet (`172.31.254.0/24`) and fixed
  container addresses.
- UNOS generates the whole `/etc/samba/smb.conf` from its database, and hand
  edits are overwritten. At its first start the supervisor imports the shares
  of an existing smb.conf once into the database and keeps that file as
  `smb.conf.unos-import-backup`. Share options that UNOS does not know are not
  kept; they are listed on the Shares page. Hand-made global settings are
  replaced by the hardened defaults. Restrictions in the imported `[global]`
  (`hosts allow`, `hosts deny`, `invalid users`, required encryption, a
  script that refuses connections) or an `include` remove the access of the
  imported shares, so that nothing becomes more open; `hosts allow`,
  `hosts deny`, required encryption or such a script in a share's own section
  remove that share's access. Required encryption is `required`, `mandatory`,
  `force`, `forced` or `enforced`; a script that refuses connections is a
  `preexec` (or `exec`) command with `preexec close = yes`, or a
  `root preexec` command with `root preexec close = yes`.
- Share and user API: `PUT /api/v1/shares/smb/{name}` is replaced by `PATCH`,
  which changes only the fields it receives; an unknown share answers 404, and
  `expected_version` (from `version`) makes a PATCH fail with 409 when the
  share changed meanwhile. `readonly` and `valid_users` are replaced by
  `access` (per-user `read` or `write`, each entry with `user_id` and
  `username`) and `guest_ok` (read-only). New shares must be on a mounted
  pool, must not overlap another share's folder, and cannot be `.snapshots`, a
  pool root or `apps`; a share whose new folder would be below a folder that
  other accounts can change answers 409. `globals` (in any letter case) is a
  reserved share name, like `global`: Samba reads both as its global section.
  The items of `GET /api/v1/shares/paths` have `managed`, true for the pools
  that UNOS manages and their volumes: only those can hold SMB shares.
  `DELETE /api/v1/auth/users/{id}` needs the query parameter `username`, and
  `POST /api/v1/auth/users/{id}/smb/disable` the body
  `{"username": "<name>"}`: the user's current name, otherwise 409 ("User N is
  now 'x'; reload the page"), and 422 without it. `POST /api/v1/auth/login`
  answers 422 for a username of more than 255 characters, a password of more
  than 1024 or a two-factor code of more than 16. 422 answers no longer repeat
  the submitted values.
- New usernames must be 1–32 lowercase letters, digits, `_` or `-`, starting
  with a letter, and unique regardless of case. Names that start with `s-` and
  a digit (such as `s-1-1-0`) are reserved: Samba reads them as security
  identifiers (SIDs).
- Shares and their access are part of the UNOS database and are restored with
  it, and a backup holds no smb.conf: the option `include_shares` is gone. A
  restore refuses a backup whose database has no SMB tables (of an older
  UNOS), and a database that never imported its shares gets those of the
  current smb.conf, without guest access. Scheduled backups hold no smb.conf
  either.
- E2E specs run only against a local stand-in: Playwright's default target is
  the stand-in that `scripts/standin/e2e.sh` (`make e2e-local`) starts, and
  `webui/playwright.config.ts` refuses a `BASE_URL` or `HTTP_URL` whose host is
  not local.
- `update.sh` stops when the checkout has local changes or is on another
  branch, builds the new images while UNOS keeps running, restarts with
  `docker compose up -d --remove-orphans`, and prunes old images only after the
  health check passes. It refuses to start while another `update.sh` runs
  (flock on `/run/unos-update.lock`).
- `install.sh` no longer writes a JWT secret to `/etc/unos/secrets.env`, and
  prints the HTTPS address, the certificate warning and the first-run steps.
- Starting or restarting an app, from the App Store or the Containers page,
  first checks that its data folders are on a mounted pool.
- Blocking work (storage and share operations, scrubs, smartctl, bcrypt,
  container log streams, e-mail) runs off the event loop; e-mail
  notifications and the scrub commands (of the Storage page and of scheduled
  scrubs) time out instead of hanging, unless the command is stuck in the
  kernel on a hung filesystem.
- The deviceless test stack runs over HTTPS (`https://localhost:3443`; HTTP on
  3080 for the redirect and the health check) in its own compose project
  (`unos-test`), and `scripts/test-cycle.sh` creates its test account.
- The `test` profile's Playwright runner (the `test-runner` service of
  `deploy/docker-compose.test.yml`) is built on the image of the pinned
  Playwright version (1.57.0, `webui/Dockerfile.test`) and creates the test
  account itself.
- `docker compose up`, which `install.sh`, `update.sh` and the `unos`
  systemd unit run, starts the web interface only once the supervisor
  answers its health check (`depends_on` with `condition: service_healthy`);
  until then `https://` does not answer. With Docker Compose 2.30 or later
  it gives up after 680 seconds, `start_period` + (`retries` + 1) x
  (`interval` + `timeout`) of the supervisor's health check, or in
  `update.sh` after `UNOS_HEALTH_TIMEOUT` seconds if that is longer. Older
  Compose versions do not limit the wait, and both scripts warn about that.
  If the supervisor does not become healthy, the web interface is not
  started: once the supervisor works, run `sudo systemctl restart unos`, or
  `install.sh` again if its first run failed (it writes the `unos` unit only
  after UNOS has started).
- `update.sh`: `UNOS_HEALTH_TIMEOUT` (default 300) is the time the updated
  version gets to answer over HTTP and HTTPS once `docker compose up` has
  returned, before it is rolled back. Values of 1000000000 or more are
  refused before anything changes. After such a rollback the error message
  suggests a retry with twice that time, at least 600 seconds.
- When `docker compose up` fails, `install.sh` and `update.sh` name the
  messages that mean that the supervisor stopped or did not become healthy
  in time ("dependency failed to start", "is unhealthy", "exited", "timeout
  waiting for dependencies"; the wording depends on the Compose version).
- Volumes API (`/api/v1/storage/pools/{pool}/volumes`): a volume is a
  subvolume in the pool's top folder with a valid volume name. Snapshots and
  nested or otherwise named subvolumes are no longer listed, also not as
  folders for new shares (`GET /api/v1/shares/paths`). `POST` takes
  `{"name": ..., "quota": bytes or null}` and uses the pool in the URL
  (`pool_name` in the body is no longer needed and is ignored); `quota` must
  be between 1 and 2^64 - 2 (422 otherwise; btrfs reads 2^64 - 1 as no
  limit). The endpoints answer 404 for an unknown pool or volume, and 409
  when the pool is not mounted or is mounted at a subvolume other than the
  top level. Creating and deleting also answer 409 on a pool mounted
  read-only, creating when the name is taken, and deleting while the volume
  holds snapshots or other subvolumes.
- The web UI is built with Node.js 22 (`node:22-alpine`); Node.js 20 reached
  its end of life. `engines` in `webui/package.json` asks for Node.js 22 or
  later.
- The compose files no longer set the obsolete `version` key, which Docker
  Compose warned about on every command.
- `scripts/test-cycle.sh` and the `docker compose up` commands in
  `docs/TESTING.md` start the deviceless test stack with `--wait-timeout 120`,
  as long as Docker can take to report its supervisor healthy or unhealthy
  (`docker compose run`, which runs the test-runner, has no such option).
  That needs Docker Compose 2.17 or later; 2.30 or later applies it to this
  wait.
- The supervisor keeps its temporary files in RAM, in a tmpfs of at most
  2 GiB at `/tmp` (`TMPDIR=/tmp`), instead of on the system disk, which on
  many boards is an SD card that writes wear out. Making and restoring a
  backup wrote temporary copies there; now they use RAM, and only while they
  run, as does the copy of the database that `update.sh` takes before an
  update. When the tmpfs is full they fail instead of moving to the system
  disk, without changing anything: a backup with "No space left on device"
  (or "database or disk is full" while it copies the database), a restore
  with HTTP 507, and the database copy of `update.sh` with an error
  (`update.sh` then asks whether to update without a backup, and stops
  without a terminal).
- While a scrub runs, btrfs rewrites its progress file every 5 seconds, for
  hours on a large pool. The supervisor keeps that file (`/var/lib/btrfs`) in
  RAM instead of on the system disk; the result of the last scrub is
  forgotten when the supervisor restarts, as it already was when its
  container was recreated.
- The supervisor image no longer writes Python bytecode (`.pyc` files, about
  1.8 MB) into each new container, on the system disk. The supervisor starts
  about 0.1 to 0.2 seconds slower on an RK3588 board.
- The RAID monitor, which now runs every 5 minutes, writes
  `/data/raid_status.json` only when the state of a pool changes, not after
  every check, so the check times in the file can be old;
  `GET /api/v1/health/raid-monitor/status` shows the latest check.
- An upload into a folder that does not exist, or that is not inside a pool,
  is refused before any of the file is read, also when the folder is removed
  while the upload starts. An upload whose file name
  cannot be used (or names an existing folder), or a backup without a
  `.tar.gz` or `.tar.gz.enc` extension, is refused as soon as the name
  arrives. Before, the whole file was received first. A backup is refused as
  soon as it grows over 256 MiB; there was no limit before. A refused,
  failed or cancelled upload leaves nothing behind. A file uploaded in the
  file manager is not in its folder, not even under a hidden name, until all
  of it has arrived; if the supervisor stops in the middle of the upload (a
  restart, an update, a crash), nothing of it stays and its space is freed.
  Only in a folder on a disk mounted there by hand whose filesystem cannot
  do this (vfat or exfat, for example; the pools can), the file arrives as a
  hidden `.unos-upload-...` file, which such a stop leaves behind: delete it
  in the file manager.
- The headers of each part of an upload form are limited to 8 KiB in total
  (HTTP 400 above that); browsers send a few hundred bytes.
- `install.sh` writes the smb.conf that UNOS generates while there is no
  share (the hardened `[global]`), without the SMB1-era tuning options. A run
  over an installation keeps only an smb.conf that starts with the header UNOS
  writes (`# Generated by UNOS from its database. ...`, both lines); any other
  file, also one with `server string = UNOS NAS`, is replaced, and kept as
  `smb.conf.original` only when there is none yet, as on the first run.
- `uninstall.sh` removes the Samba passwords of the SMB accounts that UNOS
  created on the host (GECOS `UNOS SMB user`, uid 70000–79999, group 70000),
  only entries stored under exactly the account's name, and when it removed
  one, restarts smbd once, so that open connections end: when it restores
  `smb.conf.original`, the restart of smbd and nmbd with the restored file
  (which starts a stopped smbd too), otherwise a restart of smbd if it runs.
  It keeps these host accounts (without password or login shell) and the group
  `unos-smb`: files on the pools belong to them, and while the accounts exist
  a new installation of UNOS gives its SMB accounts other uids. Its last
  message says how to remove them, names the accounts whose Samba password it
  could not remove (when `pdbedit` failed), and says that
  `/etc/samba/smb.conf` stays, with the shares it lists (guest shares too),
  naming `smb.conf.unos-import-backup`, the smb.conf that UNOS replaced with
  its own, unless `install.sh` kept the file it replaced (`smb.conf.original`),
  which is restored through a temporary file: a copy that fails leaves
  smb.conf as UNOS wrote it.

### Fixed
- **Run now** of a scheduled task no longer holds a stop of the supervisor
  until the run ends: the stop ends the run and records it as stopped, as it
  does for the runs that the schedule starts.
- Telegram, webhook, Discord and Slack notifications reach their service also
  from a network that blocks it over IPv4: when the supervisor's container
  cannot connect (the containers have no IPv6), the same message goes once
  more from the host's network with the host's `curl`, its bot token and
  headers on curl's standard input only. A request that reached the service is
  never sent twice, and a failure names both attempts. A Telegram answer counts
  as delivered only with `"ok": true`.
- `GET` and `PUT /api/v1/system/timezone` and `GET /api/v1/system/timezones`
  read and set the host's time zone: they asked the supervisor container,
  which has no systemd, and answered "Unknown". `PUT /api/v1/system/hostname`,
  which did the same for the hostname, is removed: `PUT
  /api/v1/network/hostname` sets the host's name, and the hostname that the
  API shows is read on the host, so a new name shows at once.
- On a phone, the dashboard's header wraps, and a long host name breaks
  instead of reaching past the screen.
- The App Store lists its catalog also when Docker cannot be reached, and
  says that the installed apps could not be loaded, with the reason; before,
  one error hid the whole catalog.
- When Docker cannot be reached, the container and app API answers 503
  `Docker is not available: <reason>` (it answered 500), and the Containers
  tab shows that error alone, without counts, search, an empty list or the
  Create button. An error that Docker answers, and an operation that fails
  after Docker answered, keep their own status.
- After a power cut, or on a host without the `unos` service, the web
  interface also waits at boot until the supervisor answers: Docker then
  starts both containers at once, without the order of `docker compose up`,
  and until the supervisor answered, the pages loaded but every request failed
  with 502. Until then `https://` does not answer; `docker logs unos-webui`
  says what it waits for.
- The file manager no longer cuts off the last entries of a long folder: its
  list ends at the bottom of the window.
- The dialogs of System → Security for two-factor authentication, a firewall
  rule and a certificate, and the restore dialog of System → Backup, scroll
  when they are taller than the window, so that their title and buttons can be
  reached.
- On a narrow screen a long name no longer pushes an error message, an app's
  image name in the App Store or the sign-in history of System → Security
  beyond the screen, and the Add SMB share dialog no longer scrolls on past its
  end.
- A btrfs or smartctl command that hangs in the kernel no longer holds a
  worker thread after its timeout.
- Unmounting or removing a pool works on hosts whose sandboxed systemd
  services keep copies of its mount in their own mount namespaces; before, it
  was always refused and the pool was mounted again.
- Editing a scheduled task keeps its last run and last status, and every field
  of the task form is named by its label.
- Creating, restoring and deleting a snapshot act on the snapshot and the
  folders that were checked, even when another program changes them meanwhile.
- The SMART history is replaced atomically, so a power cut during its hourly
  write no longer cuts it short.
- Pools were never added to the host's `/etc/fstab` (the entry was written
  inside the supervisor container), so they were not mounted after a reboot.
- Disks were inspected from inside the container, where the host's system disk
  looked unused and could be selected for a pool.
- Mounting a pool over a directory with files hid them while they kept using
  the boot disk. Mount and pool creation now refuse this (409). A pool in the
  host's fstab is still mounted over such files at boot, or by the supervisor
  at startup if the host did not mount it; the supervisor then logs them.
- Removing a pool is refused (409) while a running container uses it or it
  is otherwise busy (for example, files are open on it); the failed unmount
  used to be ignored. SMB shares and NFS exports of the pool are not checked.
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
- The System page shows the tab named in the URL hash (such as
  `/system#logs`), also after Back and Forward. Choosing a tab replaces the
  hash in the current history entry, so Back does not return to the previous
  tab.
- File manager: downloading a file failed (HTTP 401) because the browser
  opened the API address without the session token, and Delete failed (HTTP
  422) because it sent no list of paths. Both work now: files are downloaded
  with the new Download button (one file selected) or a double-click, and
  the browser shows the download once the whole file has arrived.
- File manager: the page assumed that the pools directory is `/mnt/pools` and
  showed nothing when `UNOS_POOLS_BASE_PATH` points elsewhere. It now starts
  at the directory the server lists, and the breadcrumbs start there
  ("Pools") instead of offering the folders above it, which were refused.
  Errors are shown on the page instead of in browser dialogs or only in the
  browser console.
- File manager: when another folder was opened while several files were
  uploading, the remaining files went into that folder. All of them now go
  to the folder that was open when they were chosen.
- File manager: deleting a volume (`/mnt/pools/<pool>/<volume>`) removed all
  its files and then the volume itself, also while an app, an SMB share or an
  NFS export used it, and volumes could be moved, or renamed through the API
  (`POST /api/v1/files/rename`; the page has no Rename). Deleting, renaming
  and moving a volume, another btrfs subvolume (such as a snapshot) or the
  mount point of another filesystem is now refused there (409); volumes are
  deleted on the Storage page, which checks their users. Copying them still
  works. A folder that contains one, or that is or contains a bind mount,
  cannot be deleted there or moved to another pool or volume either (409):
  deleting it, and moving it between filesystems (a copy, then a delete),
  went into the subvolume or mount and deleted its files. A folder that
  contains one can still be copied and moved within its pool or volume, and
  renamed through the API.
- File and backup uploads and downloads showed an error when the session had
  expired. Like every other request, they now end the session and open the
  sign-in page.
- Restoring a backup on the Backup & Restore tab always failed (HTTP 422):
  the API required a passphrase also for unencrypted backups, and the page
  sent none. Unencrypted backups are restored without one; for an encrypted
  backup (`.tar.gz.enc`) the page now asks for it, and the list marks
  encrypted backups.
- `POST /api/v1/backup/create` answered `"encrypted": false` in `backup` for
  encrypted backups.
- Backups made in the same second, with Create Backup or by scheduled backup
  tasks, overwrote each other, and a backup was listed, and could be
  downloaded, while it was still being written; one that failed (for example
  on a full disk) stayed in the list half-written. Like uploaded backups,
  they are now written under a temporary name and get their final name only
  when they are complete and on disk, with `_1`, `_2`, ... before the
  extension when the name is taken. Temporary files left behind by a killed
  backup or backup upload are removed after a day by the next one.
- The backup list left out a backup when an encrypted one with the same name
  plus `.enc` existed, and showed symbolic links, folders and files with
  other names. It now shows exactly the regular files with a backup name
  (ASCII letters, digits, `.`, `_` and `-`, starting with a letter or digit,
  ending in `.tar.gz` or `.tar.gz.enc`), newest first: only those can be
  downloaded, restored or deleted.
- Backup & Restore tab: when loading the list, creating or deleting a backup
  failed, the page showed nothing (the error went to the browser console),
  and failed downloads and uploads showed a browser dialog. These failures
  are now shown above the list, in a message that screen readers announce
  and that can be dismissed.
- Scheduled backups with a Keep Count that run at the same time (two backup
  tasks in the same minute, or Run now during a scheduled run) skip an old
  backup that the other run has removed already, so neither run fails with
  "No such file or directory".
- Scheduled tasks created in the same second got the same id: deleting one
  deleted both, and editing, running or switching one on or off reached only
  the first. New tasks get ids of the form
  `task_<date><time>_<8 hex digits>`.
- Scheduled SMART tests kill `smartctl` after 60 seconds and record the run
  as failed (timed out); before, a run waited for smartctl without a limit
  and could stay "running" for good. A smartctl that is blocked in the
  kernel by a hung disk or USB bridge ends, and the run with it, only once
  the kernel gives up on the command.
- Creating a volume with a quota that btrfs could not set (UNOS does not
  enable quotas) created the volume without the quota and reported success.
  When quotas are not enabled on the pool, the request now answers 409,
  creates nothing and suggests `btrfs quota enable <mountpoint>`. When the
  quota cannot be set for another reason, it answers 409 with btrfs's reason
  and removes the new volume again, but never a subvolume that existed
  before under that name.
- Deleting a volume is refused (409, naming them) while a running container
  uses the volume or a folder in it, through a bind mount or through a
  Docker volume of the local driver that binds it, or while an SMB share or
  an NFS export points there; nothing was checked before. Containers, shares
  and exports of a parent folder, such as the whole pool, do not count. When
  Docker cannot be asked, nothing is deleted (503). The Volumes dialog's
  delete confirmation says so, and that the files of stopped apps in the
  volume are deleted with it.
- Unmounting or deleting a pool is refused (409) while a running container
  uses a Docker volume of the local driver that binds a folder of the pool.
  The pool used to be unmounted and its fstab entry removed although Docker
  kept the filesystem mounted on the host for the container, so its disks
  looked safe to remove.
- When Docker did not answer, an unmount or removal of a pool went ahead, and
  the host mount of a Docker volume that binds a folder of the pool passed as
  the administrator's own mount elsewhere: the pool was reported unmounted
  while a container could still write to it. Unmount and Remove Pool of a
  mounted pool now answer 503 then, `Pool '<name>' was left mounted: Docker
  did not answer, ...`, and change nothing.
- File manager: renaming a folder that is a bind mount of another folder of
  its pool, or moving it within the pool, answered 500 `File operation
  failed: Device or resource busy`. It is refused with 409, as a delete of
  it is: `'<name>' is a separate subvolume or filesystem (such as a snapshot
  or a mount point) and cannot be renamed in the file manager` (moved for a
  move). Any other file operation that the system refuses as busy answers
  409 too.
- Names that are not valid UTF-8, which apps and SMB users can give to
  folders and subvolumes in a pool, broke storage operations: a subvolume
  with such a name made listing volumes fail (HTTP 500). Such names are now
  read with a replacement character, and they are never volumes. While a
  mount on the host shows such a folder (for example a Docker volume
  binding a pool folder that an app renamed), the Storage page shows
  whether the pools are mounted, and creating, mounting, unmounting and
  deleting pools and mounting them at startup work; a file with such a name
  in a mountpoint folder makes Mount answer 409 with the files to move.
- `GET /api/v1/containers/images` answered 404: the
  `/containers/{container_id}` route took "images" for a container name.
- Select boxes (for example the System Logs filters) and the masked password
  fields on the Security tab were not associated with their labels, so
  screen readers did not read the labels.
- `install.sh` stops before it changes anything when Docker is installed
  without its Compose plugin, and says what to install
  (`docker-compose-plugin` from Docker's repository, `docker-compose-v2` on
  Ubuntu, or `docker-compose` on Debian 13). It used to fail only at
  `docker compose build`, after the system packages, the checkout and the
  Samba configuration.
- `docs/USER_GUIDE.md`: the File Browser section listed features the page
  does not have (drag and drop, sorting by column, Ctrl/Cmd selection), and
  System Backup said that backups are scheduled on a Backup tab. Both now
  describe the pages as they are (scheduled backups are tasks in System >
  Scheduler), and the Storage section describes volumes. Restoring from
  Snapshot warned that a restore overwrites the current data, but it makes a
  writable copy, `restore_<date>_<time>`; the guide now says where the copy
  is and how to delete it. SMART Monitoring and System Settings > Health
  described what no page shows (power-on hours and SMART attributes of a
  clicked disk, service status, resource usage history) and now describe
  System > Disk Health; Viewing Disks no longer says that the Storage page
  shows SMART health. The Keyboard Shortcuts section, for shortcuts that do
  not exist, is removed. System Backup now also says that a restore brings
  back the user accounts with their passwords, two-factor authentication and
  API tokens of the backup (an API token revoked since then works again),
  and that other files in a backup, such as the scheduled tasks, are not
  restored.
- Disk Health (`GET /api/v1/health/disks`), `GET /api/v1/health/overview`
  and the hourly SMART monitor listed disks with lsblk inside the container
  and left out a fixed list of names, among them `nvme0n1`: the first NVMe
  disk, such as a data disk in the M.2 slot of the ArmSoM Sige7, was never
  shown or monitored, while the SPI flash (`mtdblock0`) was listed. They now
  cover exactly the whole disks that the Storage page lists, including a
  system disk on NVMe, SATA or USB, and each of them opens on
  `GET /api/v1/health/disk/{device}`. A disk that smartctl cannot open or
  identify, such as one behind a USB bridge it does not know, shows the model
  and size that lsblk reports instead of "Unknown". When the host's disks
  cannot be listed, both endpoints answer 500 with the reason, and the
  monitor logs it and skips that check.
- While a disk was connected whose model is not valid UTF-8, the Storage
  page and Disk Health could not list the disks (HTTP 500), and while a
  btrfs filesystem with such a label was connected (formatted elsewhere),
  the Storage page could not list the pools either. Such bytes are now read
  as a replacement character (U+FFFD) wherever UNOS lists or checks disks
  (the Storage page, Disk Health, the SMART monitor, SMART data and
  self-tests), also in the filesystem labels that the Storage page now
  reads (Windows writes FAT labels in its code page, as on a USB stick).
- Scheduler tab: the edit form of a SMART task whose disk is not connected
  showed an empty Target Disk select, or "No disks available." when no disk
  was connected at all. Saving kept the disk only while the select was left
  alone: once another disk was chosen, the task's disk could not be chosen
  again. The disk is now listed as "<path> (not connected)" and selected
  (without "(not connected)" while the disk list is loading or cannot be
  loaded). New tasks still start with the first connected disk. The Target
  Disk select is now associated with its label.
- Backup & Restore tab: the restore dialog said that a restore overwrites
  "Database (users, settings)", but settings such as notifications,
  scheduled tasks and power are not restored. It now says "Database (user
  accounts with their passwords, two-factor authentication, API tokens and
  the login history)".
- File manager: a move to another pool or volume copies the folder or file,
  then deletes it. When it was renamed while it was copied and something
  else was put in its place (for example a new folder with the same name),
  that was deleted, although it had not been copied. It is now kept, the
  copy stays at the destination, and the move answers 409.
- SMART test tasks made in the Scheduler failed every time they ran: the web
  interface saves the disk as `/dev/sda`, and the task ran smartctl on
  `/dev//dev/sda`. They now test the disk.
- Disk Health tab: when the disks could not be listed (for example when
  lsblk fails on the host), the tab said "No disks found", and a refresh
  that failed kept showing the disks of the last one. It now shows the
  reason.
- Uploads no longer go through the system disk and memory. Every file over
  1 MiB uploaded in the file manager or as a backup was first written in full
  to a temporary file on the system disk (on a small board, its SD card),
  then read into memory in full and only then written to its folder: twice
  the writes, wear on the SD card, and an upload larger than the free space
  of the system disk or the free memory failed. The file now goes straight
  into its folder (a backup into the backup folder) as it arrives: nothing
  is copied once its last byte has arrived, and a backup is then checked in
  one pass that writes nothing.
- Backup upload and restore answered HTTP 500 for some malformed archives (a
  member size of 2^63 bytes or more, broken GNU sparse data, a long chain of
  extended headers), such as "cannot fit 'int' into an offset-sized
  integer", and a restore also for a file time out of range. Such archives
  now get HTTP 400, for example "Invalid backup: corrupt archive" or
  "Backup archive is too large when decompressed", and a restore does not
  use the file times of the archive. Both also refuse what UNOS backups
  never contain and tarfile reads in full before anything can be checked:
  sparse files, a global PAX header, and GNU long names and PAX headers of
  more than 1 MiB in all. A 200 KiB upload could make the supervisor hold
  740 MiB of memory, or work for minutes.
- An upload whose form names a charset that fails on its field or file
  names with an error other than an invalid byte (`charset=undefined` on
  every name, `idna` and `punycode` on some) was refused with HTTP 400
  ("There was an error parsing the body"). Such names are now read as
  Latin-1, as names with an invalid byte and unknown charsets already were,
  and the file is stored. Browsers do not send this parameter; other clients
  may.
- Certificate uploads no longer write a temporary file: openssl reads the
  certificate from its input. An upload that failed while that file was
  written (a full disk, or a certificate that is not valid Unicode, such as
  a lone `\ud800` escape in the JSON) left the file, empty or partly
  written, in the supervisor's `/tmp`. A certificate, private key or chain
  that is not valid Unicode now gets HTTP 400 and changes nothing. It got
  HTTP 500, a private key or chain only after the stored certificate of
  that name had been replaced: with a bad key its key file was emptied,
  with a bad chain its key replaced and its chain file emptied.
- Start Scrub on the Storage page, and Run now of a scheduled scrub, waited
  for the whole scrub, hours on a large pool, and blocked the whole API
  meanwhile: the background process of btrfs kept open the output that UNOS
  read to its end. Both now return as soon as the scrub runs.
- For about 6 seconds after Start Scrub, until btrfs had recorded the new
  scrub, the Storage page showed no scrub and stopped following it; a second
  Start Scrub then failed with "Scrub is already running". A scrub now shows
  as running from its start.
- Resetting the error counters of a pool
  (`POST /api/v1/health/raid/<pool>/reset-stats`) ran btrfs on the event loop
  without a time limit, blocking the whole API on a hung pool, and took any
  name as the pool (`..` named `/mnt`). It now runs in a worker thread with a
  timeout and refuses names that are not pool names (400).
- A scheduled scrub of a pool that is not mounted ran btrfs on the folder of
  its mount point, on the root filesystem, which btrfs scrubbed if it was
  btrfs. It now fails with "Pool <name> is not mounted", as a scheduled
  snapshot does.

### Known issues
- Not working yet, each with an entry in `feature-list.json`:
  - The UPS state is never read, because the supervisor image has no NUT client,
    and the shutdown on low battery is skipped inside a container
    (`system.ups-monitoring`).
  - Scheduled shutdown and wake times are stored, and nothing acts on them
    (`system.scheduled-power`).
  - Alerts are sent only for pool (RAID) errors and disk health (SMART)
    warnings, and only to e-mail and Telegram channels; the events chosen for a
    channel have no effect (`system.alert-events`).
  - The host name and the time zone cannot be set from the web interface
    (`system.hostname-and-time-zone`).

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
