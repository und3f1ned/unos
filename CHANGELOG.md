# Changelog

All notable changes to UNOS will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Security and robustness hardening, fixes for broken user flows, and a few new
features (SMB access per user and per share, volumes, copy and move in the file
manager, encrypted backup uploads, health checks, CI checks).

### Changed
- The web UI has a light theme. Each user chooses **Dark**, **Light** or **System** (the device's
  setting, followed as it changes) on the Account page; the NAS keeps the choice per user
  (`GET`/`PUT /api/v1/auth/me/theme`, audited as `theme_changed`), and the page shows it before it
  is drawn. A user who never chose gets **System**, so the web UI turns light on a device set to
  light. The colour tokens have a value per theme, and the pairs of tokens that text and controls
  use meet WCAG AA in both themes, which made the dark theme's rows under the pointer and fields a
  little darker, its secondary and muted text and its status colours lighter, its field borders
  visible and the warning and success buttons darker (ADR-0405).
- Web UI pages use the design system everywhere: every button is `Button`, fields carry the
  `form-*` classes and colours are the tokens of `tailwind.config.js`; `make frontend` fails for a raw
  `<button>` or a raw palette colour outside `lib/components/ui` (ADR-0400). New tokens keep the old
  look, within what a token allows: `brand`, `surface-hover`, `border-strong`, `content-soft`, `content-on-brand`, `status-neutral`.

- The supervisor's `/tmp` tmpfs is 192 MiB, down from 2 GiB, so that a board with 1 GB of RAM cannot be
  run out of memory by a backup or a restore, and the backup limits follow it: a backup's database
  may be 48 MiB (was 512), an uploaded or stored backup file 64 MiB (was 256) and the decompressed
  archive 64 MiB (was 640). A database over the limit is refused before it is copied by Create
  Backup (413), by a scheduled backup task and by the off-device copy, and every refusal of a
  backup's size now names the limit (ADR-0406).

### Security
- The audit log records the firewall (turned on or off, rules added and removed, reset), clearing the
  login history, pools mounted, unmounted and imported, containers created, images pulled and removed,
  suspend (written before the host goes down), and the power, UPS, notification and off-device copy
  settings, for real changes only and never with a secret (ADR-0402). The Action filter offers the
  new actions. Two starts or stops of one container at the same moment write one entry. Saving power,
  channel or off-device settings that did not change no longer rewrites their files, and a firewall
  reset that ufw refuses answers 500.
- The web UI can serve a stored certificate: **Serve this certificate** on the
  certificate card of System > Security (or
  `POST /api/v1/security/certificates/{id}/serve`) checks it (it parses, the
  key belongs to it and is RSA or EC P-256 or P-384, which browsers take, it is
  for TLS servers and valid now, the chain is in order; one that expires within
  30 days is marked) and has Caddy load it from a volume that only the
  supervisor and the web UI mount; **Use Caddy's local CA** and deleting the
  served certificate serve the local CA's again. A certificate that Caddy does
  not load leaves the one it served, and at a restart the local CA's. The card,
  `GET .../certificates/status` and the listing name what a TLS handshake with
  Caddy shows (`served_by`, `served_id`, `chosen_id`, the SHA-256
  `fingerprint`). Certificate uploads, generations, deletes and changes of the
  served certificate write audit entries (ADR-0404).
- Regular users' live statistics (`/ws/v1/events`) no longer carry the host's
  processes or the names of its block devices: `processes` and
  `disk_io.disks` are empty for them, as the host's interfaces already were.
  The Resource Monitor and the Storage page that show them are for
  administrators (ADR-0399).
- The web UI keeps its session in an HttpOnly cookie, `__Host-unos-session`
  (`Secure`, `SameSite=Strict`, ending with the browser), instead of
  `localStorage`, so that no script of the page reads the session token
  (ADR-0397). `POST /api/v1/auth/login` with `"cookie": true` sets it and
  answers no token, only a `session_key` that the page sends with the cookie
  (`X-UNOS-Session-Key`, and `"key"` in a WebSocket's first message
  `{"type": "auth"}`), so that a server on another port of the NAS, to which
  the browser sends the cookie too, cannot use it; the cookie's token is no
  Bearer token. Without `cookie` the sign-in answers the token for a Bearer
  header, as scripts use it. The API takes the cookie only from the web UI's
  own origin: a change without the page's `Origin`, or a request that the
  browser marks as from another site, answers 403 "The session cookie is
  accepted only from the web UI's own page", and a cookie without its key 401.
  Logout deletes the cookie; when the NAS does not confirm it the login page
  says so and offers **Sign Out Again**. A reload keeps the session, and so
  does a new tab while another tab of the web UI is open; the tabs follow each
  other's sign-ins and sign-outs. The token that earlier versions kept in
  `localStorage` is removed.
- An API token can be limited to reading, or to areas of the API (ADR-0392).
  The areas are the prefixes of the API's paths after `/api/v1/` (`storage`,
  `backup`, `monitor` and 18 more); `POST /api/v1/auth/tokens` takes
  `read_only` and `areas`, and its answers and `GET /api/v1/auth/tokens` carry
  them. A read-only token is refused every request but `GET` (and two `POST`s
  that only read) with 403 "This API token can only read", and the download of
  a backup and the notification channels' settings with 403 "A read-only API
  token cannot read this: the answer holds secrets"; a token of areas is
  refused outside them with 403 "This API token is limited to other areas of
  the API", its WebSockets with 4403. Through the API a limited token never
  changes accounts, users, API tokens or two-factor authentication and never
  restores a backup; change access to apps, containers, the scheduler or
  updates runs programs as root, and to backups or the off-device copy takes
  the database, which the dialog warns of. Without a limit a token is full, as
  before, and the tokens that exist, which hold `*`, stay full. `permissions` is gone from the API: a request that sends it answers
  422. The Account page (**System** → **Security**) gets **API Tokens**: the
  list with each token's access and areas, **Disable Token**, **Enable Token**
  and **Delete Token**, and **New Token**, which starts at read only. A
  restored token whose scope cannot be read is refused everywhere, and the
  restore counts them among its warnings. The audit log names the scope of
  each new token.
- An administrator can require two-factor authentication for every account
  (**System** → **Security**, **Two-factor authentication for every
  account**), with a grace period of none, 1, 3, 7 (the default), 14 or 30
  days from the time it is turned on or the account is created, whichever is
  later. Turning it on or off takes an administrator with two-factor
  authentication and a current code of it, and a session, not an API token
  (`GET /api/v1/security/two-factor-requirement`, `POST .../enable` with
  `grace_days` and `code`, `POST .../disable` with `code`); the audit log
  records both as `settings_changed` of `two_factor_requirement`. During the
  grace period an account without two-factor authentication is asked to set
  it up after each sign-in (`/two-factor-setup`); after it, the account can
  only set it up: the server answers 403 "Two-factor authentication is
  required: set it up to continue" to every request but `GET /auth/me`,
  `GET /auth/totp/status`, `POST /auth/totp/setup`, `POST /auth/totp/enable`
  and `POST /auth/logout` of its sessions, to every request of its API
  tokens, and closes its WebSockets with 4403; open sessions follow at their
  next request. `GET /api/v1/auth/me` answers `two_factor_required` and
  `GET /api/v1/auth/totp/status` `required`. SMB still signs in with the
  password alone. Off by default. A restore that turns the requirement on or
  off says so among its warnings and in the audit log; a start ahead of the
  clock counts as now, and a stored requirement that cannot be read holds
  without a grace period, without errors, and can be turned off.
- **System** → **Security** no longer names a stored certificate as the one
  in use: its **SSL/TLS Certificates** card and
  `GET /api/v1/security/certificates/status` (now
  `{"served_by": "caddy_local_ca"}`) name Caddy's local CA, which is what
  browsers get, and list uploaded and self-signed certificates as stored,
  **NOT SERVED** (`served: false`), since nothing hands them to the web
  server yet. The listing no longer reads Caddy's certificates, which the
  supervisor cannot see, and has no `active_certificate` or `auto_renew`.
  openssl runs off the event loop with a timeout (10 s to read a certificate,
  60 s to make one; a timeout answers 500, an upload no longer "Invalid
  certificate format"). A certificate, its key and its chain are written to
  temporary files and renamed over the old ones only once all are written,
  one store or delete at a time: a failed write keeps the old files, two
  stores never mix one certificate with another's key, a certificate stored
  without a chain drops the old chain, and a symbolic link at a file's path
  is replaced, never followed (keys 0600). A self-signed certificate's
  organization is refused over 64 characters or with non-printable
  characters, and its country unless it is two letters (422); the country
  field takes two letters only, the dialog shows why a generation was
  refused, a listing that cannot be loaded shows no earlier list and has
  **Retry**, and a self-signed certificate made with an organization is
  listed as self-signed, not as custom.
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
- The audit log records pools created and removed, volumes created and
  deleted, snapshots created, deleted and restored, containers started,
  restarted, stopped and removed (also on the **Apps** page), and apps
  installed and uninstalled, with the administrator, the address, the browser
  and the resource. A refused or failed change, and one that changed nothing
  (a pool that was not there, a start of a running container, a stop of a
  stopped one), writes no entry. A change that failed part way writes its
  entry as failed, with what was done: a pool create that formatted the
  disks, a removal that unmounted the pool or dropped its fstab entry, a
  volume create that kept the volume without its quota, an uninstall that
  stopped the app. When the entry cannot be written, the
  change still answers and the supervisor's log has it.
- The audit log records backups created, uploaded, restored and deleted,
  reboots and shutdowns scheduled (as a warning, before the host goes down)
  and cancelled, scheduled tasks created, changed (naming the changed
  fields and, for a rename, the old name), enabled, disabled, deleted and run by hand (the new action
  **Task run**), network interfaces disabled, kept disabled and enabled,
  Wake-on-LAN turned on or off, and a new host name or time zone, with the
  administrator, the address and the browser. An enable that turned an
  interface on without all its saved addresses writes its entry as failed. A
  change that changes nothing writes no entry, and saving a scheduled task unchanged or deleting one that
  is not there no longer rewrites the tasks file. When the entry cannot be
  written, the change still answers and the supervisor's log has it.
- The **Action** filter of the Audit Log page offers only the actions that
  UNOS records: **Firewall rule added**, **Firewall rule deleted** and **Ssl
  cert uploaded**, which nothing recorded, are gone. An older entry whose
  action UNOS no longer has is listed with its action as recorded, and the
  `action` filter of the API finds it.
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
  to be restored, and turning SMB on failed for the second account. A restored
  account no longer gets the SMB uid of a user deleted from this NAS, whose
  files on the pools keep it: UNOS records every uid it hands out with the
  name it went to, also the uids of the host accounts that an uninstall kept
  and, at the first start, those of the accounts made before the record
  existed. A restore keeps that record, adds the uids that the backup's own
  record holds and this one lacks, and gives a recorded uid only to that name;
  another account must turn SMB on again and gets a new uid.
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
  list, checked against the filesystem first: the subvolume's path must be
  the volume's name byte for byte (409 when the list gives the name only the
  ids of other subvolumes).
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
- `GET` and `PUT /api/v1/power/ups/config` no longer answer the stored NUT
  password: they answer `has_password`. A `PUT` without a password keeps the
  stored one, and one without a username removes it. The settings file is
  readable by its owner only, and a damaged one is logged without its values.
  `ups_name`, `host` and `port` are checked before they reach `upsc`.

### Added
- The power settings act on the host (ADR-0407). System → Power Management has a **Shutdown and
  wake schedule**: a daily shutdown at a time on the host's clock, announced 10 minutes ahead in the
  health banner and to every notification channel and postponed while a job runs (a task, a backup,
  a restore, a scrub, a balance, a disk replace, an app install, update or rollback, the off-device copy, a permissions
  repair, an update), then announced again; a daily
  wake by the RTC's wake alarm, refused with the reason on a host whose RTC cannot take it; and a
  shutdown after the host has been idle for 20 to 1440 minutes, where open pages of the web
  interface, login sessions, SMB and NFS clients, jobs and disk activity count as use. The card
  shows the next shutdown and what holds it, the next wake and the RTC's alarm, and why the host is
  not idle (`GET /api/v1/power/schedule`). `PUT /api/v1/power/config` checks the hours, minutes and
  the idle time, and refuses settings that act on the host where host commands run locally.
- An installed app whose catalog pins its images updates in place to the catalog's newer images
  (**Update to** on the App Store's Installed tab): a dialog first shows each container's image,
  what the pulls download and write to the system disk (only the layers Docker lacks, read from the
  registry's manifests) and the copy of the database on the pool; then a background job (over
  `/ws/v1/events`, one install, update or rollback at a time) pulls, stops the app, copies its
  database (Immich's) beside itself on the pool, keeps the earlier containers as `<name>.previous`
  and starts the new ones with the data folders, password files, settings, ports, the group option
  and the limits kept. A failure or a cancel after the stop returns the app to its earlier version (or,
  when the copy cannot go back in place, leaves it stopped for a rollback). The Containers page does
  not start the earlier containers, nor start, restart or remove an app's containers while a change of
  that app runs.
  **Roll back** returns to the earlier containers and the copy of the database; **Remove earlier
  version** removes them. Only an image built after the one that runs is accepted. New routes
  `GET`/`POST /api/v1/apps/installed/{id}/update`, `POST .../rollback`, `DELETE .../previous`;
  `GET /api/v1/apps/installed` adds `version`, `update_to` and `previous`; install jobs add `kind`;
  containers get the label `unos.app.version`; audited as `app_updated`, `app_rolled_back` and
  `app_earlier_version_removed` (ADR-0408).
- Security keys as a second factor: a user with two-factor authentication adds a USB or NFC key,
  or a passkey, on the Account page (**Two-Factor Authentication** → **Security keys**), with the
  password and a touch of the key, and at the code step of the sign-in **Use a security key** signs
  in with a touch in place of the app's code. Up to 10 keys per account, each removed with the
  password; a key works at the address where it was added and never by IP address, and a signature
  counter that goes backwards is refused. Turning two-factor authentication off, and the emergency
  reset of an administrator, remove the keys. UNOS verifies WebAuthn itself (attestation none,
  ES256, EdDSA and RS256), with no new dependency; audited as `security_key_added`,
  `security_key_removed` and the sign-ins (ADR-0409, which supersedes ADR-0239).
- The App Store shows which apps have an image for the host's platform: every image of the
  catalog has its Linux platforms recorded from its registry (`image_platforms.json`), the
  catalog API answers `platforms`, `host_platform` and `available`, an app whose image lacks the
  host's platform shows "Not available for linux/riscv64" (or the host's platform) and no
  Install, and `POST /api/v1/apps/install` answers 409 for it before any pull (also when only
  one image of an app of several containers lacks it); before, such an install failed at the
  pull with a 500. `scripts/catalog-platforms.py check` (`make catalog-platforms`, and the
  weekly audit workflow) fails when the recorded platforms differ from the registries' (ADR-0403).
- The supervisor's log is one JSON object per line (`time`, `level`, `logger`,
  `message`, and `fields` and `exception` when there are any), uvicorn's own
  lines included, at INFO by default; before, only warnings reached
  `docker logs unos-supervisor`, unformatted. Set `UNOS_LOG_LEVEL` (`DEBUG`,
  `INFO`, `WARNING`, `ERROR`, `CRITICAL`) in `/etc/unos/secrets.env` and
  recreate the container to change it; other libraries log warnings and above
  only. An idle supervisor writes no line, and no line holds a password, token,
  session key or two-factor code (ADR-0398).
- A handler that catches an unexpected exception and answers 500 or 503 now
  logs it once with its traceback.
- **System** → **Resource Monitor** shows the history of the host's CPU,
  memory, network and disk I/O, with time range buttons **5 min**, **15 min**
  and **1 hour** and the peak of each chart in its range (ADR-0399). The
  supervisor notes every 10 seconds the averages of CPU, network and disks
  and the memory in use, keeps the last hour in memory, from its start and
  while no page is open, and writes nothing; the tab reads them once from
  `GET /api/v1/monitor/history` (administrators) and adds each live message
  of `/ws/v1/events`, whose new `sample_seconds` (0 for a first sample, whose
  rates are 0) keeps such a sample out of the charts. Its process list shows each
  process's PID and its user as the host names it, from the host's
  `/etc/passwd` (the container's named host accounts wrongly or not at all),
  and its memory in the largest fitting unit.
- Each user arranges the dashboard (ADR-0395). **Customize** shows a toolbar on
  every widget: drag its handle, or use **Move up** and **Move down** (for the
  keyboard and a phone), **Hide** it, and show it again from **Hidden widgets**;
  **Reset to default** restores the default. The five stats cards (CPU, Memory,
  Network, Disk I/O, Temperature) and, for administrators, Storage Pools,
  Containers, Quick Actions and Recent Activity are widgets; a regular user
  arranges the stats cards. The layout is kept on the server per user, so it
  follows the user to another browser: `GET`, `PUT` and `DELETE
  /api/v1/auth/me/dashboard-layout`, a new table `dashboard_layouts`, written
  only when the layout changes, and the audit action
  `dashboard_layout_changed`. The wide widgets sit two to a row from 1280 px
  (they were from 1024 px).
- The dashboard has a **Disk I/O** card: the bytes per second read from and
  written to the host's physical disks, from the live statistics; zram, loop,
  device-mapper (LUKS, LVM) and md devices do not count (ADR-0388).
- Actions on several containers or shares at once (ADR-0389). On the
  **Containers** page a checkbox selects a container, and **Select all shown**
  selects what the search and the state filter show; **Start selected**,
  **Stop selected** and **Restart selected** then act on each of them, one
  after another, through its own endpoint, with the checks and audit entries
  of a single action (Restart, as on a container's own row, for the running
  ones only; the containers' own buttons wait meanwhile), and list each
  container's result: its new state, or why it was not done (the others go
  on; what was not done stays selected). UNOS's own
  containers are not listed, so they cannot be selected. On the **Shares**
  page the SMB shares are selected the same way, and **Delete selected** asks
  once, naming each share and its folder, then deletes all of them (up to
  200) with one request, `POST /api/v1/shares/smb/bulk-delete` with each share's name and
  the version and folder the page loaded: one change of smb.conf and one
  reload of Samba under `smb_lock`, the folders kept, one `share_deleted`
  audit entry per share (one that cannot be written is a warning of the
  answer, and the others are still written). A share changed, made again in
  another folder or
  deleted elsewhere in the meantime is not deleted and is named with the
  reason; when testparm rejects the new smb.conf or the database is busy,
  none is deleted.
- Memory and CPU limits for the containers of apps (ADR-0390). On the App
  Store's **Installed** tab each app's card lists its containers with the
  memory and the CPUs each uses against its limits, and **Limits** sets a
  container's memory limit (from 64 MB to below the NAS's memory, leaving a
  running container a margin above what it uses now) and CPU limit (from 0.1 to the NAS's
  CPUs); an empty field is no limit. Docker applies them at once and keeps
  them across restarts and reboots (`docker inspect`: `Memory`,
  `MemorySwap`, `CpuQuota`, `CpuPeriod`), and UNOS keeps them for the app's
  next install, also after an uninstall. Every 30 seconds UNOS reads each
  running container's use from its cgroup in the kernel, with no command,
  no disk read and nothing written, and alerts once a container at 90% of
  its memory limit (until below 80%), one held at 90% of its CPU limit for
  5 minutes (until below 70%) and, as an error, one whose process the
  kernel ended for lack of memory (Docker's `oom` event, kept an hour). The
  API: `PUT /api/v1/apps/installed/{container}/limits`,
  `GET /api/v1/apps/limits` and `resources` of each container in
  `GET /api/v1/apps/installed`; the audit log records `app_limits_changed`.
- An administrator chooses the network interfaces that SMB listens on
  (ADR-0391): **Shares** → SMB → **Network interfaces** → **Choose
  interfaces…**. By default SMB listens on every interface, as before; with a
  choice, smb.conf gets `interfaces = lo <the chosen names>` and
  `bind interfaces only = yes`, and smbd binds only the addresses of those
  interfaces and the loopback. The dialog lists the host's interfaces with
  their addresses (without the loopback and Docker's; point-to-point ones
  cannot be ticked), refuses a choice without an enabled interface that has
  an address of the local network, keeps a chosen interface that is not on
  the host (an unplugged adapter), and says that saving restarts smbd, with
  the number of open SMB connections that it closes. The card shows the
  addresses smbd listens on and names an address of a chosen interface that
  came after smbd started, with **Restart SMB…**. The choice is in the
  database (the new table `smb_bind_interfaces`), so a backup holds it.
  wsdd2 and avahi-daemon keep announcing the NAS on every interface. The API:
  `GET` and `PUT /api/v1/shares/smb-interfaces` and
  `POST /api/v1/shares/smb-interfaces/restart`; the audit log records
  `smb_interfaces_changed` and `smb_restarted`.
- A command palette (ADR-0393): Ctrl+K, or Cmd+K on macOS, opens it over a
  signed-in page, and on a phone the search button of the bar at the top.
  Typing narrows its pages and actions by their English or Russian names,
  ↓ and ↑ choose, and Enter or a click opens the chosen one. It offers what
  the account's role may open: an administrator every page, the **Trash** and
  every tab of **System**; a regular user **Dashboard** and **Account**. It
  switches the language and logs out itself; **Add a user…**, **Reboot the
  host…**, **Shut down the host…** and **Suspend the host…** open the
  **Users** page's dialog and **Power Management**'s confirmations, which act
  only once confirmed. It does not open over a dialog, Escape closes only it,
  and it asks the server for nothing.
- Groups of users (ADR-0385). The **Users** page has a **Groups** card: make
  a group, choose its members (users who are not administrators), rename it
  or delete it. On the **Shares** page a share's access list gives each group
  **No access**, **Read only** or **Read & write**, and the share's card lists
  each group with its members. Every member whose SMB is on gets the group's
  access, and so does a user added to the group later; a user who also has a
  level of their own, or through another group, gets the higher one. Removing
  a member, lowering a group's level or deleting the group disconnects the
  open connections to its shares. A group lives in UNOS's database only:
  smb.conf names its members, and no group is made on the host, so the host's
  own groups, uninstall.sh and the restore of a backup (which brings the
  groups back with the database) have nothing more to handle. A deleted user,
  or one made an administrator, leaves every group, and a renamed user stays
  in them under the new name. The API: `GET`, `POST /api/v1/groups`, `GET`,
  `PATCH`, `DELETE /api/v1/groups/{id}`, which take the group's name as the
  page saw it (409 after a rename), and `group_access` in the share API; the
  audit log records `group_created`, `group_updated` and `group_deleted`.
- The use of each volume's quota (ADR-0387): the Volumes dialog shows how much
  of its quota each volume uses, with a bar, or its use alone without a quota,
  says when quotas are off on the pool or btrfs's counts need a rescan, and
  draws a volume's use over time (one point per hour for 30 days, the quota as
  a dashed line). `GET /api/v1/storage/pools/{pool}/volumes` adds `used`,
  `quota` and `usage` to each volume, and
  `GET /api/v1/storage/pools/{pool}/volumes/{name}/usage` answers the points.
  The use is read from the kernel's counters in sysfs, with no command and no
  disk read. At each pass of the RAID monitor (every 5 minutes), a volume that
  reaches 80, 90 or 95% of its quota is alerted once per level to every
  enabled channel and shown in the health banner (an error at 95%); a level
  counts again once the use fell 5 points below it, and a restart keeps the
  level reached. While btrfs's counts need a rescan they change no alert and
  no point. The points stay in memory and are written to the system disk only
  when the supervisor stops. Once btrfs has finished deleting a volume,
  restored copy or snapshot and its quota group counts nothing, the monitor's
  lists of the pool destroy that group, which btrfs keeps with its limit for a
  later subvolume of the same id.
- Apps of several containers (ADR-0381): an app of the catalog may have
  companion containers beside its main one, on a Docker network of its own
  (`unos-app-<app>`, where each answers to its name), which UNOS installs,
  starts (in order, the main container once those it depends on are ready),
  stops (in reverse order), restarts and uninstalls together. The App Store
  shows one card per app with its containers and its health, warns about an
  incomplete app or a stopped companion, and lists each container with its
  image, folders and settings in the details. A password that the containers
  share is generated at the install into a file on the app's pool (root,
  0600), given to them as a file and never shown; an install that finds the
  database without it is refused. An install that fails removes the containers
  and the network it made and keeps the folders, the password file and the
  images.
- Immich in four containers: its server, its database (Immich's PostgreSQL),
  Valkey and its machine learning, each image pinned by tag and digest
  (v3.4.1), with all its data on the pool and what the containers write at run
  time in memory. On a Rockchip RK3588 whose kernel has the RKNPU driver, its
  machine learning runs on the NPU with Immich's `-rknn` image and
  `/dev/dri`, and its server gets the video hardware for RKMPP (ADR-0382).
  `scripts/dev/scenario.sh hardware` also checks what the App Store offers
  Immich, the NPU's render node and the RKNPU driver's version, and
  `scripts/dev/immich-ml-probe.py` measures Immich's machine learning inside
  its container.
- Checks before an app's images are pulled (ADR-0381): its host ports (409
  for a port that a container publishes, a stopped one too, or that a program
  on the host listens on, read with `ss`; 400 for a port outside 1 to 65535,
  a host port given twice, or a moved port of an app in the host's network,
  whose port field the App Store no longer lets you change), the system disk's
  free space (409 when less than 2 GiB would stay free after the catalog's
  pinned images; `df` on the host), and an app that is already installed
  (409); a second change of an app while one runs answers 409 too.
- On a Rockchip RK3588 (such as the Banana Pi BPI-W3) whose kernel has
  Rockchip MPP's `/dev/mpp_service`, Jellyfin from the App Store is installed
  with the chip's video hardware for Rockchip MPP (RKMPP): those of `/dev/dri`,
  `/dev/dma_heap`, `/dev/mpp_service`, `/dev/rga` and `/dev/mali0` that the
  host has, Docker's masks without `/sys/firmware` (MPP reads the SoC from the
  device tree) and no AppArmor profile, without privileged mode. The App Store
  shows **Hardware acceleration** on its card and says in its details to
  choose **Rockchip MPP (RKMPP)** in Jellyfin; an installed Jellyfin whose
  container lacks the hardware shows **No hardware acceleration** on its card,
  and the notice of its install and its details say that reinstalling it gives
  it the hardware; the catalog API answers an app's `hardware` profiles that
  apply on the host only, and the install's answer and the installed apps list
  the `devices` their containers got. On other hosts, an RK3588 under the
  mainline kernel included, nothing changes. `scripts/dev/scenario.sh hardware`
  checks what the App Store offers on the dev device and the nodes the host
  has (ADR-0377).
- Idle timeouts of the data disks (`storage.disk-spindown`, ADR-0378):
  **Storage** → **Physical Disks** has a **Power** column with each disk's
  power state (Active, Standby, Unknown), read with `hdparm -C` on the host,
  which sends CHECK POWER MODE alone: it does not wake a sleeping disk, and it
  did not restart the standby timer of the dev device's disk
  (`smartctl -n standby -i`, whose IDENTIFY does restart it, reads it on a
  host without `hdparm` and for an NVMe disk; `smartctl -n idle`, which stops
  after CHECK POWER MODE too, reads the EPC power conditions that `hdparm`
  does not name: idle_a, idle_b and idle_c are Active, standby_y Standby; a
  disk whose read did not answer is Unknown, with no other `hdparm -C`
  started while that one still runs on the host), and a button that sets
  the disk's idle timeout (Off, 5, 10, 20 or 30 minutes, 1 to 5 hours; a
  disk with a minimum of its own sleeps later, as a WD6400AACS after about
  10 minutes for 5) through `PUT /api/v1/storage/disks/{name}/spindown`:
  `hdparm -S` on the host at once,
  and a line in UNOS's udev rule `/etc/udev/rules.d/90-unos-disk-spindown.rules`,
  matched by the disk's serial, at every boot and reconnection. While a disk
  has a timeout, its own automatic offline scans (a surface scan every few
  hours that keeps it awake; after a restart they kept the dev device's disk
  awake for hours with its timeout of 20 minutes, and through 18 to 35
  minutes of watching with 10 to 20 minutes) are off: the Save turns them off
  (`smartctl -o off` in the container) and the rule records it, also when
  `smartctl` gives no clear answer, and Off turns them on again for such a
  disk alone; a disk whose scans were off, or that has none (NVMe, SCSI, an
  ATA disk without SMART or with SMART off), is left as it is. When they
  cannot be turned off, or it is unknown whether they were, the timeout is
  set all the same and the answer's `warning`, shown on the page, says they
  may keep the disk awake; when the rule cannot be written, the scans are put
  back as the rule still records them. The dialog says that a scheduled
  scrub reads the pool instead. The system
  disk, boot and virtual devices, partitions, NVMe disks, disks without a
  usable serial and a host without `hdparm` (now an optional host tool) are
  refused with the reason, and so is every disk when the rule cannot be read.
  The page reads the disks before it lists the pools, which read every disk.
  The SMART reads of Storage and Disk Health leave a sleeping disk asleep
  (health status `STANDBY`, `power_state` standby, and `standby` in
  `GET /api/v1/health/overview`), and the hourly SMART monitor skips a disk
  with an idle timeout that was not used since its last read, since a SMART
  read restarts the disk's timer (its first pass after a start reads every
  awake disk, and a disk found asleep at its last pass is read once awake).
  The RAID monitor and the pool counts of `GET /api/v1/health/prometheus`
  and `/health/full` read the pools without a scan of the disks
  (`btrfs filesystem show --mounted`, whose exit status 1
  with no btrfs filesystem mounted is no pool mounted, and udev's records);
  the trash expiry asks a pool's disks whether they sleep only when its daily
  pass is due, and passes over one whose disks sleep, asking no disk of it
  again until the sleeping one had a read or write, and then that one first.
  `install.sh`, once UNOS runs, and the manual installation turn off the
  host's smartd (`systemctl disable --now smartmontools`): it would read every
  awake disk every 30 minutes, and UNOS reads SMART itself; `uninstall.sh`
  says how to turn it on again. `uninstall.sh` also undoes, on each disk
  that the rule names, what UNOS set, with the host's tools (found also
  where root's PATH lacks `/usr/sbin` and `/sbin`), each under `timeout`
  (the script goes on after 35 seconds per command at most, also when the
  kernel keeps a command past its kill, and so does whatever reads its
  output, since the command's output and errors go to temporary files, its
  errors shown once it has ended): the timeout off (`hdparm -S 0`), and
  the scans that UNOS turned off on again (`smartctl -o on`, read back with
  `-c`); then it removes the rule. It does so once UNOS has stopped and
  before it removes `/opt/unos`: a run stopped while it undoes the disks,
  also with Ctrl-C or a dropped SSH session (which end it once the command
  running has ended), leaves the rule whole and can run again, which then
  makes no backup, since the data volume is gone. It counts only the lines
  that the supervisor's `read_rule` takes as UNOS's, read as udev reads a
  rules file without NUL bytes (as `read_rule` now does too), never runs the
  file, touches no other disk, and names at its end, with the command, what it
  could not undo (no `hdparm` or `smartctl`, a disk not attached or not alone
  with its serial, SMART off on the disk, for which the command is
  `smartctl -s on -o on`, a failed command) and the lines edited by hand,
  which go with the rule. A disk whose scans stay off keeps its record in the
  rule, which then holds only such records, for a new installation's Off
  (not on a disk with SMART off); a rule it cannot read, or with a NUL byte,
  stays. `scripts/dev/scenario.sh smart` shows the system disk
  refused a timeout, gives the pool's disk the
  shortest timeout, waits for it to sleep (at most 1980 s: room for the dev
  device's disk's own minimum of about 660 s twice, as after a read of the
  hourly SMART monitor during the wait; it says whether the kernel counted
  reads or writes on a disk still awake), reads SMART while it sleeps and
  sets the timeout of UNOS's rule again, or Off; it reads the disk's
  automatic offline scans first, shows them off after the Save and as found
  at the end, also in its clean-up, which names what it left and the way
  back, and fails an Off that answers a warning; it names them when they keep
  the disk awake; it skips these steps when the disk has no timeout of
  UNOS's and `/etc/hdparm.conf` sets one, and when it has one with its scans
  on unrecorded. A disk that the Storage page shows active after the wait is
  a SKIP, not a failure, when the containers of apps (all but UNOS's `unos-*`)
  read or wrote it in the wait's last 660 s, as their cgroups count it, and
  UNOS's own did not, and so are the SMART reads of the sleeping disk then,
  and when the disk was found awake in them after such a container used it:
  the step names them with their counts (ADR-0394). An app that used the disk
  only earlier, UNOS's own use and any other failure still fail; when the
  apps used it just before the last 660 s, the failure says that a pass of the
  SMART monitor after them may have kept the disk awake.
- **Balance** on the Storage page (ADR-0379): a mounted, writable pool's
  balance rewrites the data and metadata chunks less full than a usage limit
  (20% by default, 1 to 99) or, after a confirmation, every chunk (a full
  balance). The pool card shows its progress (chunks balanced of about how
  many) and an estimate of the time left, which UNOS sends the open page as
  they change, and **Pause Balance**, **Resume Balance** and **Cancel Balance**
  act on it; afterwards it says whether the last balance finished, was
  cancelled or failed, with btrfs's error. The balance runs on the host in the
  systemd unit `unos-balance-<pool>`, so a restart of UNOS neither stops nor
  hides it, and a restart or shutdown of the host pauses it: it goes on when
  the pool is mounted again. A full balance that goes on after a pause, by
  **Resume Balance** or at a mount, rewrites only the chunks less than 90%
  full (the Linux kernel's rule): the card of a paused balance, the full
  balance's warning and the user guide say so, and that a new full balance,
  after a cancel, rewrites every chunk. A balance is refused while the pool
  is not mounted or is read-only, while a scrub of it runs and while another
  balance of it runs or is paused, and a scrub (also a scheduled one) while a
  balance of the pool runs. A cancel waits while a pause waits for the chunk
  being moved, since btrfs would pause the balance all the same, and the
  paused balance of a read-only pool is kept until it is mounted read-write.
  The audit log records each start, pause, resume and cancel
  (`GET`/`POST`/`DELETE /api/v1/storage/pools/{name}/balance`,
  `POST .../balance/pause` and `.../balance/resume`). `scripts/dev/scenario.sh
  balance` checks it on the dev device.
- An SMB share can require encryption, serve Time Machine backups of Macs and
  list the snapshots of its pool or volume as Windows' Previous Versions
  (ADR-0380): three options of the share dialog, off by default, shown on the
  share's card and stored in the new table `smb_share_options`
  (`encryption_required`, `time_machine`, `previous_versions` of the share
  API). Every share section now states `smb encrypt` and `vfs objects`, also
  with the options off; while a Time Machine share exists every share loads
  `catia fruit streams_xattr` and `[global]` has `fruit:aapl = yes` and
  `fruit:nfs_aces = no`, and a change of the Time Machine shares restarts
  avahi-daemon and waits for it, so that smbd announces them (`_adisk._tcp`);
  a restart that fails is a warning and a problem of the SMB status until one
  works. Previous Versions list the snapshots named `snap_<time>` and
  `auto_<time>` in UTC; scheduled tasks snapshot pools only, so a share in a
  volume lists the volume's snapshots of the Snapshots page. A guest share
  cannot require encryption, and a restored one with both loses guest access;
  Time Machine and Previous Versions need the package `samba-vfs-modules`,
  which `install.sh` now installs, and are refused without it; Previous
  Versions are refused for a folder in a subvolume or file system of its own
  below the pool's top level other than a volume. An option that the NAS
  cannot serve is left out of smb.conf and shown on the card as a warning
  with the reason (`not_rendered` of the share API). Turning encryption on
  and Previous Versions off disconnect the share's open connections. The
  import of an smb.conf that UNOS did not write keeps required encryption
  (of a section, or of `[global]` for every share) as the option with the
  access, where it removed the access before, and Time Machine where fruit is
  loaded. `scripts/dev/scenario.sh smb` shows the three options on a share in
  a new volume, with open sessions across the encryption switch and the
  `_adisk._tcp` service that Avahi resolves.
- `scripts/dev/check.sh` shows each container's CPU, the load average and the
  temperatures, and reports a container of UNOS above 15% of a core or a
  thermal zone within 5 C of its throttling point as a problem (ADR-0374).
- RISCV64 hosts on Debian 13 and Ubuntu 24.04, made for the Banana Pi BPI-F3
  (SpacemiT K1): `install.sh` accepts `riscv64` and installs the
  distribution's `docker.io`, `docker-buildx` and `docker-compose` (Debian) or
  `docker-compose-v2` (Ubuntu), since Docker's repository has no RISCV64
  packages. The supervisor's pip layer installs pip 26.3 (the base's 24.0
  ignores RISCV64 wheels) and, on RISCV64 only, compiles cryptography, bcrypt,
  cffi, psutil and uvloop with a Rust toolchain that it removes again; the web
  UI's builder runs Alpine 3.22's Node.js 22 on every platform, since the `node`
  image has no RISCV64 build. Node.js crashes on the K1 while it builds the web
  UI, so there `install.sh` and `update.sh` build only the supervisor and start
  the web UI from an image `deploy-webui:latest` made on another machine
  (`docker buildx build --platform linux/riscv64`); `install.sh` stops with
  those commands without one, and `update.sh` reminds to load the new one. On
  a K1, SATA disks behind an M.2-to-SATA adapter or USB SSDs are the recommended
  storage and need nothing special; NVMe is not recommended. For an NVMe drive
  used anyway, `install.sh` adds the udev rule `60-unos-k1-nvme.rules`, which
  turns off the drive's APST and its PCIe link's ASPM L1 whenever it appears,
  and a pool with an NVMe drive is made with `mkfs.btrfs --nodiscard`, mounted
  with `nodiscard` and kept out of the weekly fstrim (`X-fstrim.notrim`): the
  K1's NVMe drives hang otherwise. The dashboard shows the K1's CPU temperature
  (`cluster0_thermal`) instead of the first sensor psutil lists. `uninstall.sh`
  names the distribution's Docker packages and keeps the K1's rule (ADR-0370,
  ADR-0373).
- Notes for the Banana Pi BPI-W3 (ArmSoM W3, Rockchip RK3588), the dev device,
  in `docs/INSTALL.md`: Armbian's `armsom-w3` image on a microSD card, the eMMC
  left alone, Docker's data off the card where possible, SATA disks behind an
  AHCI controller, NVMe drives and USB 3 disks made and mounted into pools as on
  every host, and cooling, since the kernel slows the CPU from 75 C and drives
  no fan. The board needs no code of its own (ADR-0376).
- Administrators rename users in the **Users** page's edit dialog
  (`PUT /api/v1/auth/users/{id}` with `new_username` and the current
  `username`): the user signs in with the new name, the old one is refused,
  and the user's sessions end. A name that breaks the name rule, or that
  another user has in any letter case, is refused with the reason. A user with
  SMB on needs a new password: the open SMB connections end, Samba's account
  of the old name is removed, the host account takes the new name with its
  uid (`usermod -l`, a new host tool of the package passwd), Samba gets an
  account of the new name with the new password, and the share access and
  smb.conf follow the new name. A backup made before or after a rename,
  restored after the user was deleted, brings the user's SMB account back with
  its uid. A renamed user's login history starts at the rename, so a user who
  gets a name that a rename freed sees none of its earlier holder's sign-ins.
- The Snapshots page lists the copies that **Restore** made right in the pool
  or volume chosen, `restore_<date>_<time>`, apart from the snapshots, with
  their folder and time, and deletes them by their subvolume id after the
  checks of a volume's Delete (running containers, SMB shares, NFS exports,
  the off-device copy). A volume that held a copy can then be deleted; before,
  no page could delete the copy, and the volume's Delete answered 409.
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
  file uploaded under a `.tar.gz.enc` name that is gzip data to its end, such
  as an unencrypted backup, is refused (400) instead of failing only at its
  restore.
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
  (`POST /api/v1/shares/smb/{name}/repair-permissions`). It runs in the
  background, one repair at a time, until the share is done: the start
  answers at once (202), the Shares page shows its progress and its end,
  which `/ws/v1/events` sends administrators (`{"type": "repair"}`), and
  **Stop repair** (`DELETE` on the same path) ends it early; it stops by
  itself after 24 hours. `GET` on the same path answers the share's last
  finished run. Deleting a share or changing its name or folder stops its
  repair, and a stop of UNOS stops it with an audit entry; after a restart the
  page no longer shows a repair as running. It does not change
  ACL entries (`setfacl`): on a file or folder that has them, the group
  permissions it sets are the ACL's mask, so the users and groups named there
  keep their access and can get more, and `unos-smb` gets only what the ACL's
  entry for the owning group gives.
- On the Account page: the SMB status, "Shares you can use" and "How to
  connect".
- A status panel on the Shares page.

### Changed
- Every time that the API answers carries its offset, in UTC (ADR-0401,
  `architecture.times-with-time-zones`): the times that the database keeps
  without one (users, API tokens, the login history, SMB passwords, the
  two-factor requirement) and the times that the supervisor wrote without one
  (the audit log, the update history, the SMART and RAID history) are read as
  UTC, without rewriting them, so the audit log's hash chain still holds.
  `boot_time` of `GET /api/v1/system/info` is a time (ISO 8601) instead of
  seconds since the epoch, `GET /api/v1/power/status` answers `last_boot` as
  the time less the uptime, a scrub's `last_run` and a certificate's
  `valid_from` and `valid_until` are ISO 8601 instead of btrfs's and openssl's
  text, and the bounds of the audit log take an offset (`Z` or `+HH:MM`). The
  web interface shows every time in the browser's time zone and the page's
  language: the Audit Log page groups its entries by the browser's days, and
  From and To are the browser's days; the Storage page shows when a scrub
  started so in English too.
- An app's install runs in the background (ADR-0384, which supersedes the
  one-request install of ADR-0381): `POST /api/v1/apps/install` answers 202 at
  once with the install's job, and the checks of the pool, the folders, the
  ports and the free space, the pulls, the creates and the starts run in the
  job; the request itself answers only 404, 400 for a request that the
  template refuses, and 409 while another install runs (one at a time on the
  NAS) or the app is being changed. The job reports each image's pull as
  Docker's pull stream says it (its layers and the bytes downloaded), each
  container's state and the end: the install's answer, or the error with the
  status the request would have answered. `/ws/v1/events` sends administrators
  an `app_installs` message with the running install and the last of each app,
  `GET /api/v1/apps/install/jobs` lists them, `GET
  /api/v1/apps/install/jobs/{id}?wait=SECONDS` (up to 3600) answers once the
  job has ended, and `DELETE /api/v1/apps/install/jobs/{id}` forgets an ended
  one; `DELETE` on the running job cancels it (202): it stops at its next
  step, also in a download, and what it made is removed as after a failure.
  A pull that sends nothing for 10 minutes fails the install. Up to 4 such
  GETs wait at once (429 beyond). The jobs are kept in memory only; a stop of
  UNOS stops an install at its next step, leaving the containers it made
  (audited as a failed `app_installed`), and uvicorn now ends the requests
  still open 3 s after a stop (`--timeout-graceful-shutdown 3`), so that a
  waiting GET cannot hold back that stop. The App Store's details wait while
  the install checks and show a refusal there; then a card above the tabs
  shows each image's download, each container's start and the result, also
  after the page is loaded again, with **Cancel install** while it runs and
  **Dismiss** once it ended. `scripts/dev/api.py` waits for an install
  with `GET /apps/install/jobs/<id> --param wait=3600` instead of a
  `--timeout 3600 POST`.
- `GET /api/v1/apps/installed` lists an app once, with `health`, `missing`
  and its `companions` (name, container, status, health, image, devices), and
  each of its `volumes` names its `companion`; the install answers `health`
  and `companions`; the catalog's templates have `companions`, `depends_on`,
  `tmpfs`, `shm_size`, `stop_timeout`, `editable` variables,
  `generated_secret` volumes and a hardware profile's `image`. Hardware
  profiles apply per container (ADR-0382, which supersedes ADR-0377).
  Restarting an app stops and starts all its containers, and a stop waits for
  each container's stop timeout.
- No page polls the API any more. **System** → **Logs**' **Auto** follows the
  journal over the new WebSocket `/ws/v1/journal` (administrators only): the
  newest entries of the chosen source, level, search and time range, then
  each new one as the journal gets it, from a `journalctl --follow` on the
  host that ends with the socket; it no longer reads the whole range again
  every 5 seconds, and a lost connection stops it with a notice. The Storage
  page follows a running scrub, also a scheduled one, over `/ws/v1/events`,
  which sends administrators a `scrubs` message whenever the status of a
  scrub that runs changes, instead of asking for the status every 2 seconds.
  `GET /api/v1/logs/entries` answers each entry's journal `cursor`. A check
  of the web UI's code (`webui/scripts/polling-check.mjs`, run by
  `make test`) fails for a `setInterval` that reaches the API, also through a
  function of another module of the web UI, or that changes a store or a
  variable that a `$:` statement reaching the API reads. After the events
  socket connects again, for example to a supervisor that restarted, the
  Storage page asks once for each scrub it shows running or could not read.
- Every copy of an smb.conf that UNOS replaced is kept, not only the latest:
  a file with shares that UNOS did not create goes to a new
  `/etc/samba/smb.conf.unos-replaced-<UTC time>` before an apply, and a file
  whose shares UNOS imports to a new
  `/etc/samba/smb.conf.unos-import-backup-<UTC time>` (for example
  `smb.conf.unos-replaced-20261112_101530_123456`), unless one of the newest 10
  copies of its kind holds exactly the same bytes. A copy never replaces another; UNOS
  keeps the newest 10 of each kind and deletes the older ones, never the new
  one, and refuses nothing for their number. The Shares page's problem and the
  import report name the new copy and the earlier ones, newest first, and the
  import report also names an import backup that an earlier attempt of the
  same import wrote.
- Backups, by hand, by backup tasks and for off-device copies, no longer hold
  the supervisor container's `/etc/fstab` and `/etc/netplan` files, which
  describe nothing of the host, and the options `include_pools` and
  `include_network` of `POST /api/v1/backup/create`, which added them, are
  gone. A restore ignores such files in a backup that holds them, and no
  longer warns that `fstab` is not restored.
- `POST /api/v1/backup/create` takes only `encrypt` and `passphrase`: the
  options `include_users`, `include_apps`, `include_scheduler` and
  `include_notifications`, which selected nothing, are gone, and a request
  that still sends them makes the same backup. The manifest of a backup made
  by hand, through the API or for an off-device copy no longer has a `config`
  with them, only its version, date, hostname and whether it is encrypted.
  Older backups whose manifest names them still restore.
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
- The stats message of `/ws/v1/events` sends the CPU's temperature as
  `cpu.temperature`
  (degrees Celsius, or null without sensors); their `temperatures` objects,
  which held the first reading of every sensor group, are gone.
  `GET /api/v1/system/temperatures` lists every sensor.
- The logs API reads every source through `GET /api/v1/logs/entries?source=...`:
  the old `GET /api/v1/logs/dmesg` and `/logs/journal` are gone (404).
- On a narrow screen the System tabs are listed above the open tab, the file
  manager leaves out the Modified column and scrolls as a whole page, and
  System → Logs shows each entry's time, level and program above its message.
- New folders and uploads of the file manager in a share folder get 2770 and
  0660 with the group unos-smb, so the share's SMB users can change them at
  once.
- Copy, and Move from another pool or volume, give what they put into a share
  folder the modes of Repair permissions in the group unos-smb: folders 2770,
  files 0660, or 0770 for files their owner may run, through a whole copied
  tree; private entries keep theirs, and each entry gets its mode only once it
  is copied in full (ADR-0396). The share's SMB users change such items at once,
  without a Repair. A Move within a pool or volume stays a rename that keeps
  the modes, and outside share folders copies keep the modes of their source.
  In a share folder a copy goes on only in the folder that it made: another
  folder put at that name meanwhile is refused with 409 and left as it was; a
  copy's mode comes from the source that was opened.
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
  `smb.conf.unos-import-backup-<UTC time>`. Share options that UNOS does not know are not
  kept; they are listed on the Shares page. Hand-made global settings are
  replaced by the hardened defaults. Restrictions in the imported `[global]`
  (`hosts allow`, `hosts deny`, `invalid users`, a script that refuses
  connections) or an `include` remove the access of the imported shares, so
  that nothing becomes more open; `hosts allow`, `hosts deny` or such a
  script in a share's own section remove that share's access. A script that
  refuses connections is a `preexec` (or `exec`) command with
  `preexec close = yes`, or a `root preexec` command with
  `root preexec close = yes`. Required encryption (`required`, `mandatory`,
  `force`, `forced` or `enforced`), in `[global]` or in a share's section,
  keeps the access and becomes the share's option (see Added).
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
- A restore checks the backup and extracts its database in one pass over the
  archive, encrypted backups too, instead of decompressing the whole archive
  into `/tmp` first: it needs about half the room in `/tmp` (and so in RAM) it
  needed, the size of the database, and still refuses every archive it
  refused. The manifest is read in memory, and the scheduled tasks of a
  backup task's backup, which a restore does not restore, are no longer
  extracted. A file whose name in the archive is not its plain path, such as
  `unos.db/.`, is refused with 400 by the upload and by a restore, which
  answered 500 with a temporary path.
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
  hidden `.unos-upload-...` file, which such a stop leaves behind: the File
  Browser offers to remove it.
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
  listing the import backups `smb.conf.unos-import-backup-<UTC time>`, the
  smb.conf files that UNOS replaced with its own, oldest first, unless
  `install.sh` kept the file it replaced (`smb.conf.original`),
  which is restored through a temporary file: a copy that fails leaves
  smb.conf as UNOS wrote it.

### Removed
- `GET /api/v1/monitor/cpu`, `/monitor/memory`, `/monitor/processes`,
  `/monitor/history/cpu`, `/monitor/history/memory` and `/monitor/all`: no
  page called them, and their history held only what calls of `/cpu` and
  `/memory` had taken. They answer 404; `GET /api/v1/monitor/history` is the
  Resource Monitor's history (ADR-0399, ADR-0207). From each process of the
  live statistics: `status` and `memory_percent`, which no page read, and
  `memory_mb`, now `memory_bytes`.
- From the live statistics of `/ws/v1/events`: each interface's `ipv4`,
  `speed` and `is_up`, which were the container's; the network's
  `bytes_sent`, `bytes_recv`, `packets_sent` and `packets_recv`, the disks'
  `read_bytes`, `write_bytes`, `read_count` and `write_count`, and those of
  each block device in `disk_io.disks`, which no page read. Regular users get
  the network's rates without the host's interfaces (ADR-0388).
- `GET /api/v1/monitor/network`, `/monitor/disk-io`, `/monitor/history/network`,
  `/monitor/history/disk-io` and `GET /api/v1/system/stats`, and the `network`
  and `disk_io` of `/monitor/all`: no page called them, and they reported the
  supervisor container's network and every block device. They answer 404; the
  stats message of `/ws/v1/events` is the one source of live stats (ADR-0388,
  ADR-0207).
- BookStack, Paperless-ngx and Linkwarden from the App Store's catalog: each
  needs a database or a broker beside it, which they will get as apps of
  several containers (ADR-0381).
- The `name` of an app's install request: each app of the catalog is
  installed once, under its id, and a request with `name` answers 422.

### Fixed
- The CPU share of the live statistics, of the CPU and of each core, was of
  the time since the last sample taken in the same worker thread, any number
  of samples back; it is now of the time since the last sample, and 0 in the
  first one after a start or a pause, like the rates (ADR-0399).
- On a phone, the Audit Log no longer reaches past the screen when an entry
  holds a long name with no place to break, such as an API token's name of 64
  characters, a long name tried at sign-in or a full IPv6 address: the name
  breaks within its entry.
- The dashboard's **Network** card and the Resource Monitor showed the traffic
  of the supervisor's container, which has a network of its own, instead of
  the host's; **System** → **Network** showed that container's live rates on
  a host port named like the container's `eth0`, and none on a host whose
  port has another name. They now show the host's interfaces that **System**
  → **Network** lists, without the loopback, `dummy0` and Docker's. The disk
  rates no longer count zram, loop, device-mapper and md devices, and a disk
  or interface that comes or goes no longer makes a rate negative or adds all
  its counters at once. Rates no longer jump when the board sets its clock at
  boot, and the first rates after the dashboard is opened are no longer the
  average since it was last open (ADR-0388).
- The dashboard's live stats cost a slow board a large share of a core: every
  open tab took its own sample every two seconds, in the supervisor's event loop,
  and listed every process of the host each time (0.4 s of CPU on a SpacemiT
  K1). One sample now serves every tab, taken in a worker thread, and the
  process list is taken at most every 10 seconds (ADR-0374).
- The supervisor's healthcheck started a Python every 10 seconds, 0.5 s of CPU
  each on a SpacemiT K1; it now asks with bash, 0.01 s. The dashboard's process
  list reads name, memory, status and user of the ten processes it shows only
  (ADR-0375).
- The check for leftovers of uploads after a start no longer walks Docker's
  data root when a host keeps it on a pool, with its image layers and the root
  filesystems of the running containers (ADR-0374).
- Escape closes only the dialog on top when one dialog opens over another:
  turning **Allow SMB access** off in a user's edit dialog now asks over that
  dialog, which stays open, and Escape closes the question first, then the
  edit dialog. Which dialog Escape closed depended on the order of the dialogs
  in the page. A dialog that opens takes the keyboard focus, so a key no
  longer acts on the element that opened it, and the dialog under it takes
  neither the focus nor clicks until the question closes. When a dialog
  closes, the focus goes back to the element that had the focus when it
  opened.
- The UPS state was never read: `upsc` ran in the supervisor's container,
  which has no NUT client. It now runs on the host, under a time limit and
  off the event loop. While the host has no `upsc`, the UPS status, Test
  connection and the UPS monitor's status answer that the UPS is unavailable
  and name the missing package nut-client; the monitor then writes nothing,
  logs it once and shuts nothing down. Values with decimals are read, and a
  value that is no number leaves the others.
- `POST /api/v1/containers/{id}/restart` restarted UNOS's own containers,
  which stop and remove refuse; it now refuses them too (404).
- Two-factor authentication set up in two tabs at once could be turned on
  with a secret that the user had not scanned, and so lock them out: a setup
  that stored its new secret while the other tab's code was being accepted,
  or just after that code turned 2FA on. Turning 2FA on now needs the secret
  its code was checked against to be still the stored one (otherwise 400
  "Run /totp/setup first"), and a setup never replaces the secret of 2FA that
  is on (400 "2FA is already enabled").
- The dashboard's Temperature card shows the CPU's temperature (Intel's
  package; AMD's Tdie, or Tctl where the CPU has no Tdie) or the SoC's
  (`cpu_thermal`, and the RK3588's `soc_thermal` or `package_thermal`): it
  showed the first sensor that the host listed, on many hosts an ACPI zone or a
  disk. A host whose CPU sensor UNOS
  does not recognize still shows that first sensor.
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
  move). A move checks every item first, a file that is a bind mount too,
  also one moved to another pool or volume (it was copied there before the
  move failed), so such an item among several items moves none of them, and
  a move that stops part way says how many items moved before it. Any other
  file operation that the system refuses as busy answers 409 too.
- File manager: deleting a folder for good (also **Empty trash**, the trash's
  expiry, and a move to another pool or volume) went into a subvolume or a
  mount, also a bind mount, that appeared below the folder while it was
  deleted, and deleted its files. The delete now checks each folder's device
  and mount before it deletes anything in it, and stops at such a folder with
  409, which says that part of the folder was deleted before that.
- Volumes: a subvolume whose name holds a line break, which an app can make in
  a pool, made every later volume created with a quota answer 409 and stay
  without its quota, and one named like a volume followed by a line break was
  taken for that volume: listed, deleted or given the quota in its place. The
  subvolume list is now read highest id first, so that a name cannot raise
  the newest id, and each volume's id is confirmed by the subvolume's exact
  path (`btrfs inspect-internal subvolid-resolve`, compared byte for byte).
- Volumes: a subvolume that was being renamed while the quota guard of a new
  volume read the subvolume list could be missed and then given the new
  volume's name: it got the quota, or was deleted when the quota failed. The
  guard now also reads the quota groups (`btrfs qgroup show`), which a rename
  does not change.
- Volumes: when the subvolume list failed right after a create with a quota,
  the request answered 500 without saying that the volume existed; it now
  says `Volume '<name>' was created without its quota: ...`, also when btrfs
  does not answer within 120 seconds; when setting the quota does not
  answer, it says that the volume was created but may have no quota. When something
  took the name between the check and the create, the request answered 500
  `Creating volume ... failed`; it now answers 409 and names what is there (a
  folder, a file, a symbolic link or a subvolume).
- Unmounting or removing a pool, deleting a volume and importing a pool under
  an earlier pool's name did not count a container whose mount reached the
  pool through a symbolic link on the host, a relative path in a Docker
  volume's options, a folder of the pool bound elsewhere on the host, or a
  volume of a third-party driver. The folders are now resolved on the host
  (`realpath`) and compared by filesystem and folder in the host's mount
  table. `realpath` runs under the host's `timeout` and answers 500, with
  nothing changed, after 10 seconds on a network or FUSE mount that does not
  answer.
- Disks whose names have more than four letters, such as `sdaaa` (the 703rd
  SCSI disk), `xvdaa` or `dasda`, were listed on the Storage and Health pages,
  but creating a pool on one, its SMART data and self-tests and SMART tasks
  answered 400 `Invalid disk name format`.
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
- A file manager or backup upload whose client stopped sending in the
  middle, with its connection still open, kept what had arrived, and its
  space, for as long as that connection stayed open. An upload that gets no
  data for 2 minutes now fails with HTTP 408 ("The upload timed out: the
  client sent nothing for 120 seconds"), and nothing of it is kept; one
  that keeps sending, however slowly, goes on.
- The hidden `.unos-upload-...` file that an upload leaves when the
  supervisor stops in the middle of it, in a folder whose filesystem has no
  unnamed files (a vfat or exfat disk mounted there by hand, for example),
  was found only by looking for it. The supervisor now looks for such files
  in every folder below the pools, down to 64 folders deep, 10 minutes after
  each start, and when
  asked (`POST /api/v1/files/upload-leftovers/check`); the File Browser
  shows a notice with their number and size, and **Remove…** lists them and
  removes them for good once you confirm. A file that an upload is still
  writing, or that changed in the last 10 minutes, is never removed; when one
  is refused, the dialog lists the others.
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
- A disk attached while the Storage page was open, such as a USB disk, was
  not shown until the page was opened again, nor was a removed one taken off
  the list. The page now loads its lists again within a few seconds of the
  host's block devices changing, as the stats stream reports them, and not
  while one of its dialogs is open but once it closes (ADR-0360). SMART data
  of a disk attached after the supervisor started still cannot be read until
  the supervisor restarts: smartctl runs in the container, whose `/dev` lacks
  the new disk.

### Known issues
- Not working yet, each with an entry in `feature-list.json`:
  - The UPS has no page and is set only through the API, its state is read
    only where the host has NUT, which UNOS does not install, and the shutdown
    on low battery is skipped inside a container (`system.ups-monitoring`).
  - Scheduled shutdown and wake times are stored, and nothing acts on them
    (`system.scheduled-power`).
  - Alerts are sent only for pool (RAID) errors and disk health (SMART)
    warnings, and only to e-mail and Telegram channels; the events chosen for a
    channel have no effect (`system.alert-events`).
  - The host name and the time zone cannot be set from the web interface
    (`system.hostname-and-time-zone`).
  - The SMART data of a disk attached after the supervisor started, such as a
    USB disk, cannot be read, nor its self-tests run, until the supervisor
    restarts (`storage.hot-plugged-disks-smart`).

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
