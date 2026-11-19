# Glossary

The words of the UNOS project: one name for each concept, what it means here, and the names to
avoid. General terms (Docker, btrfs, JWT, Samba) keep their usual meaning and are not listed. A
definition says what a word means; the values and rules behind it are in the ADRs it cites
([docs/adr/](docs/adr/README.md)), and the AGENTS.md files point to the code.

## The system

**Host**: The Debian or Ubuntu machine that UNOS is installed on. "On the host" means in the
namespaces of its PID 1, not inside a container (ADR-0020). Avoid: server, system.

**Supervisor**: The privileged backend container `unos-supervisor`, which keeps UNOS's database and
reads and changes the host for the web UI (ADR-0002). Avoid: backend server, daemon.

**Web UI**: The container `unos-webui`, which serves the web interface over HTTPS and passes its
API and WebSocket calls to the supervisor (ADR-0002, ADR-0025). Avoid: frontend server, dashboard
(the Dashboard is one page).

**Widget**: One card or section of the Dashboard that a user can move, hide and show: the CPU,
Memory, Network, Disk I/O and Temperature cards and, for administrators, Storage Pools, Containers,
Quick Actions and Recent Activity. Its order and hidden state are the user's dashboard layout, kept
per user on the server (ADR-0395). Avoid: tile, panel.

**Theme**: The colours of the web UI, dark or light: the values that the colour tokens take. Each
user chooses Dark, Light or System (the device's light or dark setting) on the Account page, and the
server keeps the choice per user (ADR-0405). Avoid: skin, colour mode.

**Text catalog**: The texts of one area of the web UI in English and Russian
(`webui/src/lib/i18n/catalog/<area>.ts`), which the pages show in the language chosen on the
Account page; the server's messages stay English (ADR-0287). Avoid: dictionary, locale file.

**Command palette**: The dialog that Ctrl+K (Cmd+K on macOS), or the search button of a phone's
header bar, opens over a signed-in page: typing narrows the pages and actions of the user's role,
and Enter opens the chosen one. It runs only the actions that change nothing on the NAS; the
others open their page's own dialog (ADR-0393). Avoid: quick switcher, command bar, search.

**Table frame**: The element of the class `table-frame` around a table that can be wider than a
phone's screen: the table scrolls sideways inside it, while the page never does (ADR-0302).
Avoid: scroll wrapper, responsive table.

**Bulk action**: An action that an administrator takes at once on the containers or SMB shares
selected on their page: Start, Stop or Restart of containers, each through its own endpoint, and
Delete of shares, after one question and with one apply. Each one selected gets its own result
(ADR-0389). Avoid: batch, mass action.

**Dev device**: The Banana Pi BPI-W3 (ArmSoM W3, Rockchip RK3588) that UNOS is developed and checked
on; PROGRESS.md (Dev device) has its state. It holds no data to
preserve (ADR-0220). Avoid: production NAS, the server.

**Workstation**: The owner's computer, where the repository is edited and tested, the local
stand-in runs and the web UI's static files are built (ADR-0016). Avoid: host, local server.

**System disk**: The disk that holds the host's root filesystem; it never joins a pool. On the dev
device it is an SD card whose writes are measured and kept minimal (ADR-0214). Avoid: eMMC (the
dev device's is an SD card), boot disk.

**Supervisor log**: What `unos-supervisor` writes to Docker's container log (`docker logs
unos-supervisor`): one JSON object per line with `time`, `level`, `logger` and `message`, set up in
`supervisor/src/logging_setup.py` at the level of `UNOS_LOG_LEVEL` (INFO by default) and silent
while UNOS idles (ADR-0398). Avoid: log file (the Logs tab's files are the container's `/var/log`),
audit log (the record of who changed what).

**Data directory**: The folder where the supervisor keeps its database (unless `UNOS_DB_PATH` names
another file), JWT secret, audit log, state files, stored certificates and backups: `UNOS_DATA_DIR`, `/data` in its container (the `unos-data`
volume), whose paths `supervisor/src/paths.py` alone names (ADR-0419). Host paths such as
`/etc/fstab` have settings of their own. Avoid: state directory (the local stand-in's folder, which
holds its data directory), data volume (the Docker volume).

**Write-ahead log**: `unos.db-wal` beside the database `unos.db` in `/data`, to which SQLite appends
the pages of each commit; a checkpoint, when the log reaches 1000 pages and when the supervisor
stops, copies them into `unos.db` (ADR-0306). Avoid: journal (SQLite's rollback journal, which
`unos.db` no longer uses).

**State file**: One of the supervisor's JSON files of state in `/data` besides the database: the
settings, rules, tasks and histories that `supervisor/src/state_files.py` replaces through a
rename and keeps as `<name>.bad` when it cannot read one (ADR-0324). Avoid: config file (some hold
histories), cache.

**Owner**: The person who owns the repository and the dev device: decides, triages, asks for
commits and pushes, opens pull requests and holds the credentials (ADR-0208, ADR-0024).
Avoid: user (a UNOS user), admin.

**Automatic revert**: What turns a network interface that UNOS disabled back on, with its saved
addresses and routes, or puts the host's firewall back as it was before a change: a timer on the
host that runs unless the administrator keeps the change within 60 seconds (an interface,
ADR-0244) or 120 seconds (the firewall, ADR-0416). Avoid: rollback (what `update.sh` and
`deploy.sh` do), undo.

**Firewall**: The host's ufw, which System > Security turns on and off and whose rules it lists and
changes (ADR-0416). Its rules are the host's, as ufw records them; UNOS keeps none of its own.
Avoid: iptables, the IPv6 inbound filter (the dev device's own nftables table, ADR-0301).

**Remote access**: The WireGuard tunnel on the host, the interface `unos-wg` of the unit
`wg-quick@unos-wg`, through which a peer reaches the NAS from outside the LAN: the NAS's tunnel
address only, never the rest of the LAN (ADR-0420). Avoid: VPN (it suggests the whole LAN), remote
desktop.

**Peer**: A device that may connect to remote access: its public key and its `/32` in the tunnel
network, with a name. Its configuration is shown once, at its creation; revoking it ends its
tunnel at once (ADR-0420). Avoid: client, user.

**Tunnel network**: The private IPv4 network of remote access (10.99.0.0/24 by default), apart from
every network of the host; the NAS takes its first address, each peer one more (ADR-0420).

**App name**: A local name of a published port of an installed app, `<label>-<host name>.local`,
which the host's unit `unos-app-names` announces with avahi-publish and the web UI's Caddy forwards
over HTTPS to that port (ADR-0420). Avoid: domain,
subdomain (it is one label).

**Local networks**: The private, link-local and unique-local networks of SMB's `hosts allow`
(10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 100.64.0.0/10, 169.254.0.0/16, fc00::/7, fe80::/10),
which SMB answers, NFS exports may name and UNOS's firewall rules let reach the web UI, SSH, SMB
and iSCSI (ADR-0095, ADR-0416, ADR-0418). Avoid: LAN when the networks are meant (a LAN can also use global IPv6
addresses).

**Scheduled task**: A task of System > Scheduler that runs at the times of its schedule on the
clock of the host's time zone: a snapshot, a scrub, a SMART test, a backup or a custom task. Each
run is recorded with its status in the task's history (ADR-0044, ADR-0266, ADR-0268). Avoid: job, cron job.

**Custom task**: A scheduled task that runs a shell command as root in the supervisor's container;
its runs record the command's output and exit status (ADR-0044, ADR-0267). Avoid: script.

**Scheduled shutdown**: The daily shutdown of the power settings at a time on the clock of the
host's time zone, announced 10 minutes ahead as a problem and postponed while a running job runs
(ADR-0407). Avoid: auto-shutdown (the setting's name in the API, `auto_shutdown`), the UPS's shutdown.

**Idle shutdown**: The shutdown of the power settings once the host has been idle for the set 20 to
1440 minutes: no open page of the web UI, login session on the host, SMB or NFS client, running job
or disk activity (ADR-0407). Avoid: idle timeout (a data disk's spin-down), sleep.

**Running job**: Work that a scheduled shutdown waits for and that keeps the host from idling: a run
of a scheduled task or one due within 2 minutes, a backup, a restore, a scrub, a balance, a disk
replaced or removed in a pool, an app's install, update or rollback job, the off-device copy's work, a repair of a share's
permissions, an update of UNOS (ADR-0407). Avoid: task (a scheduled task is one kind), process.

**Wake alarm**: The alarm of the host's RTC (`/sys/class/rtc/rtc0/wakealarm`) that UNOS keeps at the
next wake time of the power settings while the wake is on; whether the board powers on from it is
the board's (ADR-0407). Avoid: alert (a notification), rtcwake (a tool UNOS does not run).

**Notification channel**: Where UNOS sends notifications: e-mail through a mail server, a Telegram
chat, or the URL of a webhook, Discord or Slack, set up in System > Notifications (ADR-0326).
Avoid: notifier, integration.

**Alert**: A notification that UNOS sends on its own to every enabled notification channel: once
for each problem when it is found, for new error counts of a pool's devices, for a change of the
health of a pool that UNOS does not manage, and for a disk's rising SMART counters (ADR-0332).
Avoid: alarm.

**Problem**: A condition of the NAS that needs an administrator while it lasts: a managed pool
that is not mounted, degraded, read-only after an error or almost full, a volume at 80, 90 or 95
percent of its quota, a failing disk, a scheduled task whose last run failed or that missed a run,
a container of an app at its memory or CPU limit or out of memory (ADR-0390).
It is alerted once when it is found (a volume's again at each further level) and shown in
the health banner until it is gone; UNOS keeps it in RAM only (ADR-0332). Avoid: issue, incident,
event.

**Health banner**: The list of the current problems above every page of an administrator, in the
language of the page (ADR-0332). Avoid: alert bar, notification banner.

**Notification history**: The latest 100 notifications sent, tests and alerts included, with the
channel and the result of each: History in System > Notifications (ADR-0326). Avoid: log (the
Logs tab shows the journal and container output).

**Monitor history**: The host's CPU, network and disk I/O averaged over each 10 seconds, and its
memory in use, over the last hour, which the supervisor keeps in RAM from its start and System >
Resource Monitor shows with the live values of the stats socket (ADR-0399). Avoid: metrics,
statistics database.

## Storage

**Pool**: A btrfs filesystem on one or more disks that UNOS mounts under `/mnt/pools` (ADR-0034).
Avoid: array, volume, RAID.

**Managed pool**: A pool that UNOS mounts at every boot through its own entry in the host's fstab.
Only managed pools can hold shares and NFS exports (ADR-0034, ADR-0294). Avoid: mounted pool (a managed pool can
be unmounted).

**Pool import**: Making a btrfs filesystem found on the host's data disks a managed pool without
formatting it: Mount for the pool its label names, Import, under a name that becomes its label,
for any other (ADR-0283). Boot, virtual and system devices never hold a pool. Avoid: Import alone
(the smb.conf import), adoption.

**Pool root**: The top folder of a pool, which holds its apps, volumes and snapshots; it is never a
share's folder (ADR-0145). Avoid: pools directory (that is `/mnt/pools`).

**Mount copy**: A mount of a pool's filesystem at the pool's mountpoint, with the root of the
host's mount, in the mount namespace of another process whose root is the host's, such as a
systemd service with sandboxing. The host's umount does not reach it, so an unmount of the pool
unmounts it there too. Any other mount of the pool in another namespace keeps the pool mounted
(ADR-0288): a container's bind mount, and also the pool's mount at its mountpoint in a container,
whose root is its own (`-v /mnt/pools:/mnt/pools`). Avoid: stale mount (the copy exists while the
pool is mounted too).

**Volume**: A btrfs subvolume at the top of a pool, with an optional quota (ADR-0069).
Avoid: subvolume (snapshots are subvolumes too), dataset.

**Quota use**: The bytes that a volume's quota group references, which its quota limits, data
shared with its snapshots included; UNOS reads it from the kernel's counters in sysfs, alerts at
80, 90 and 95 percent of the quota and keeps one point per hour for 30 days (ADR-0387). Avoid:
volume size, disk usage.

**Snapshot**: A read-only btrfs snapshot of a pool or a volume. Restoring one makes a writable copy
and overwrites nothing (ADR-0011). Avoid: backup.

**Restored copy**: The writable snapshot `restore_<date>_<time>` that restoring a snapshot makes right
in the pool or the volume it was taken of; the Snapshots page lists and deletes it (ADR-0358). Avoid:
snapshot, restore.

**Trash**: The hidden folder `.unos-trash` at the top of each pool and volume that takes what Delete
in Files deletes there, until it is restored, deleted for good, or deleted for good 30 days after
its deletion (ADR-0259). Avoid: recycle bin, snapshot.

**Upload leftover**: A hidden `.unos-upload-<16 hex digits>` file that a stop of the supervisor left
in a folder in the middle of a file manager upload, where the folder's filesystem has no unnamed
files; the File Browser lists them and removes them for good when an administrator confirms
(ADR-0354). Avoid: temporary file, partial upload.

**Balance**: Rewriting chunks of a pool, the blocks of space that btrfs gives to data and
metadata, to free the space of partly empty ones: by default those less than 20% full, or every
chunk (a full balance). UNOS runs it on the host in the unit `unos-balance-<pool>`, and it can be
paused, resumed and cancelled (ADR-0379). Avoid: rebalance, defragment (rewrites files, not chunks).

**Disk removal**: Taking a disk out of a pool with `btrfs device remove`, which first moves its
data to the pool's other disks; UNOS runs it on the host in the unit `unos-disk-remove-<pool>` and
refuses one that the profile or the space of the disks left cannot survive (ADR-0411). Avoid:
disk replace (a copy to a new disk, which UNOS does not start), shrink.

**Recovery runbook**: `docs/RECOVERY.md`: how another Linux machine reads a pool's files without
writing to its disks, also with a disk missing, and how root runs the emergency reset (ADR-0285).
Avoid: disaster recovery plan, repair guide.

**Hot-plugged disk**: A disk attached while UNOS runs, after the supervisor started, such as a USB
disk. The Storage page lists it within seconds (ADR-0360); before smartctl reads it, the supervisor
makes its device node in its own container's `/dev`, which Docker filled only at the start
(ADR-0414). Avoid: hot-swap (a bay that takes a disk while powered), new disk.

**SMART history**: The sector and error counters of each disk that the SMART monitor records every
hour while the disk is awake, kept by serial in RAM and in `/data/smart_history.json`, which is
written when they change (ADR-0235, ADR-0269). Avoid: SMART log (smartctl's own self-test and error
logs).

**Replace soon**: The mark of a connected disk whose failure prediction, a score of the rises in
its SMART history, reaches 50; Disk Health and the dashboard show it with its reasons (ADR-0235).
Avoid: failing (the disk's own SMART health check), degraded (a pool's health).

**Idle timeout**: The time without access after which a data disk spins down into standby, set on
the Storage page with `hdparm -S` and kept in UNOS's udev rule by the disk's serial (ADR-0378).
Avoid: sleep timer, APM (the disk's own power management).

**Standby**: The power state of a disk that has spun down: a read spins it up, and reading its
power state with `hdparm -C` or `smartctl -n standby` (CHECK POWER MODE first) does not. Whether
CHECK POWER MODE restarts an awake disk's standby timer is the disk's: it did not on the dev
device's disk (ADR-0378). "Standby-safe" is a command that
sends a sleeping disk nothing that wakes it (ADR-0378). The user texts say a disk in standby "sleeps". Avoid: SLEEP
mode (the deeper ATA state that needs a reset).

**Automatic offline scan**: A hard disk's own surface scan every few hours (smartctl's automatic
offline data collection, `smartctl -o on`), which keeps the disk awake while it runs; UNOS turns it
off while the disk has an idle timeout and records that in its udev rule (ADR-0378). Avoid: offline
test, self-test (a SMART test that UNOS starts).

## Shares and SMB

**Share**: An SMB share: a folder inside a managed pool, or a volume, that Samba serves with
per-user access (ADR-0074, ADR-0145). Avoid: export (an NFS export), share folder (the folder
itself).

**Served share**: A share whose name and folder pass the rules, so that Samba serves it. The others
show as "Not served" and grant nothing (ADR-0145). Avoid: active share, enabled share.

**Share access**: One user's level on one share, read or write. Without it a user has no access to
the share, and it counts only while the user's SMB account is active (ADR-0075).
Avoid: permission, ACL.

**Group**: A named set of UNOS users, kept in UNOS's database only, that can be given share access:
each member whose SMB account is active gets it, and a user gets the higher of their own level and
their groups' (ADR-0385). It is no host group: smb.conf names the members. Avoid: SMB group, host
group, unos-smb (the host group of every SMB account).

**Group access**: A group's level on one share, read or write (ADR-0385). Avoid: group permission.

**Guest share**: A share that guests may read without a password; guests never write (ADR-0096,
ADR-0100). Avoid: public share, anonymous share.

**Protocol options**: A share's three SMB settings beside its access, each off by default: Require
encryption (only clients that encrypt with SMB 3 open it), Time Machine backups of Macs, and
Previous Versions from the snapshots of its pool or volume. Each is checked when stored and when
smb.conf is rendered (ADR-0380).

**SMB account**: A UNOS user's allowance to use SMB and, while it is active, the marked host account
and the Samba password behind it (ADR-0105, ADR-0118). Avoid: Samba user, SMB user.

**SMB state**: A user's SMB status, derived and never stored: active, password needed, off or not
available (ADR-0118). Avoid: smb_enabled (a request flag only).

**Marked account**: A host account that UNOS created for an SMB account and marked as its own; UNOS
changes no other host account (ADR-0107, ADR-0108). Avoid: system user, managed user.

**unos-smb**: The host group of every SMB account, which also owns the share folders (ADR-0150).
Avoid: smb group, samba group.

**smb_lock**: The one lock that every change of SMB state holds: shares, access, accounts, smb.conf
and a restore (ADR-0080). Avoid: share lock.

**Apply**: Writing smb.conf from the database and making Samba use it, checked before it replaces
the old file (ADR-0089). Avoid: sync, reload (one step of it).

**Import**: The one-time turning of the host's existing smb.conf into shares, which only ever
narrows access (ADR-0084, ADR-0380). Avoid: migration.

**Startup reconcile**: The task at the supervisor's start that brings the host accounts, Samba's
passwords and smb.conf in line with the database (ADR-0138). Avoid: sync, repair.

**Repair permissions**: An administrator's action on one share that gives the files already in it
the group unos-smb and removes other accounts' access; it runs in the background (ADR-0363). Avoid:
fix permissions, chmod, reconcile.

**Network discovery**: How computers on the LAN find the NAS without its address: Windows through
WS-Discovery (wsdd2), macOS through Bonjour (the `_smb._tcp` service that smbd registers with
avahi-daemon), both over IPv4 only; NetBIOS is off (ADR-0275). Avoid: network browsing (a share's
"Show in network browsing" lists the share among the NAS's shares), NetBIOS browsing.

**SMB interfaces**: The network interfaces of the host that SMB listens on: every one by default, or
those that an administrator chose, by name, together with the loopback. smbd binds their addresses
when it starts, so a change of them, and Restart SMB, restart smbd and end the open SMB connections
(ADR-0391). Avoid: bind addresses, listen addresses (addresses, not interfaces), SMB network.

**NFS export**: A folder that the host's NFS server offers to the computers of a network: one entry
of an export file, read as exportfs reads it. UNOS adds lines to its own export file,
`/etc/exports.d/unos.exports`, deletes only the lines it wrote there, exactly as it writes them,
and keeps every other line (ADR-0292, ADR-0293). Avoid: share (an SMB share), NFS share.

**Export file**: A file that exportfs reads: `/etc/exports`, then each `*.exports` file of
`/etc/exports.d` (ADR-0292). Avoid: exports table (the kernel's), etab.

**iSCSI target**: A disk that the host's kernel (LIO) offers over the network to the initiators an
administrator allows, with CHAP: UNOS's targets are made on Shares > iSCSI, each with one LUN, and
are named by an IQN that starts with `iqn.2003-01.org.linux-iscsi.unos:`. UNOS makes the kernel's
state that of its database at every start and change (ADR-0418). Avoid: iSCSI share, volume (the
pool's folder that holds the LUN), portal (one address and port where targets listen).

**LUN**: The disk of an iSCSI target, its logical unit 0: a sparse file
`<volume>/.unos-iscsi/<name>.lun` of the size the administrator chose, served by LIO's fileio
backstore; its folder is root's and immutable while it holds one (ADR-0418). Avoid: image, zvol.

**Initiator**: The iSCSI client of a computer, named by its own IQN (or an `eui.` or `naa.` name),
which logs in to a target. Only the initiators allowed for a target log in, each with the target's
CHAP user name and secret (ADR-0418). Avoid: client (an NFS client), iSCSI user.

**CHAP secret**: The shared secret with which an initiator proves itself to a target (and, with
mutual CHAP, a second one with which the target proves itself to the initiator): 12 to 16
characters, kept in `/data/iscsi_secrets.json` and shown only once, when it is made (ADR-0418).
Avoid: iSCSI password.

## Users and access

**UNOS user**: An account that people sign in to the web UI with: an administrator or a regular
user (ADR-0030). Avoid: account (alone), Linux user.

**Administrator**: A UNOS user who reaches every page and API and never has SMB access; first-run
setup creates the first one (ADR-0029, ADR-0030, ADR-0117). Avoid: root, superuser.

**Regular user**: A UNOS user who is not an administrator: sees Dashboard and Account only and may
get SMB access (ADR-0030). Avoid: normal user, guest (guests have no account).

**Host account**: An account of the host's operating system. The marked ones belong to SMB
accounts; UNOS leaves every other one alone (ADR-0108). Avoid: user.

**Session token**: The token of a sign-in, valid until it expires or its session ends: Logout, a
password change, an administrator (ADR-0028, ADR-0329). The web UI keeps it in the session cookie;
a program gets it in the sign-in's answer and sends it as a Bearer token (ADR-0397). Avoid: API
token.

**Session cookie**: The HttpOnly cookie `__Host-unos-session` that holds the web UI's session token:
no script of the page reads it, and the server takes it only from the web UI's own page and with
its session key; it ends with the browser or at Logout (ADR-0397). Avoid: token storage, auth
cookie.

**Session key**: The value that the sign-in answers with a session and that the web UI's page
sends with the session cookie; it lives in the tab, and the other tabs ask for it. Neither the key
nor the cookie alone is a session (ADR-0397). Avoid: CSRF token, session token.

**API token**: A long-lived token that a user creates for scripts, with that user's rights, shown
once (ADR-0239); only a session token creates, disables and deletes API tokens (ADR-0253). It is
full or limited by its scope (ADR-0392). Avoid: session token, API key.

**API documentation**: The supervisor's OpenAPI schema, `app.openapi()`, served at `/openapi.json`
on its own port, which the web UI's address does not pass on: every route's summary, description,
parameters, fields, examples and error statuses (ADR-0417). Avoid: Swagger.

**Scope**: What an API token may reach: its access, read only or read and change, and its areas of
the API, every area or a list (ADR-0392). Avoid: permissions (the column that keeps it), role.

**Area of the API**: The first segment of an API path after `/api/v1/`, one per router prefix, such
as `storage` or `backup`: what a scope lists (ADR-0392). Avoid: module, section, router.

**Limited token**: An API token that is read only or limited to areas; through the API it never
changes accounts, users, API tokens or two-factor authentication and never restores a backup. The
others are **full** and act with all of their user's rights (ADR-0392). Avoid: scoped key,
restricted token.

**Recovery code**: One of the ten codes that a user with two-factor authentication generates on the
Account page and keeps; each one signs in once in place of a code of the authenticator app
(ADR-0238). Avoid: backup code.

**Security key**: A WebAuthn authenticator (a USB or NFC key, or a passkey of a phone or computer)
that a user with two-factor authentication adds on the Account page; at the code step of the
sign-in its answer to a challenge signs in once in place of a code of the authenticator app. It
works at the web UI's address where it was added, never by IP address (ADR-0409). Avoid: passkey
(one kind of it), hardware token, U2F key.

**Relying party ID**: The host name to which WebAuthn binds a security key: the name of the web UI's
address where the key was added, without the port, kept with the key (ADR-0409). Avoid: domain,
RP name.

**Emergency reset**: Root on the host setting a new password for an administrator and turning its
two-factor authentication off, with `deploy/reset-admin.sh`, when no administrator can sign in;
the audit log records it by the user root (ADR-0284). Avoid: password recovery, factory reset.

**Two-factor requirement**: An administrator's setting that every account must have two-factor
authentication, after a grace period that starts when it is turned on or when the account is
created, whichever is later (ADR-0386). Avoid: mandatory 2FA policy, enforcement.

**Setup-only account**: An account without two-factor authentication whose grace period under the
two-factor requirement has ended: its sessions reach only the setup of two-factor authentication,
its status and the sign-out, and its API tokens and WebSockets nothing (ADR-0386). Avoid: locked
account, restricted user.

**Stored certificate**: A TLS certificate with its key, and its chain when given, that an
administrator uploaded through the API or generated (self-signed) on the Security page, kept in the supervisor's
`/data/certs` (ADR-0404). Avoid: custom certificate (one type of stored certificate).

**Served certificate**: The stored certificate that an administrator chose for the web UI: the
supervisor copies it with its chain and key into `served.pem` of the `served-certificate` volume,
and the web UI's Caddy serves it to every browser while it loads; otherwise, and when none is
chosen, Caddy serves its local CA's (ADR-0025, ADR-0404). What is served is what a TLS handshake
with Caddy shows. Avoid: active certificate, installed certificate.

## Apps and backups

**App**: An application installed from the App Store catalog, with its data on a pool (ADR-0039):
its main container and, for some apps, companion containers, which UNOS installs, starts, stops
and uninstalls together; each app of the catalog is installed once (ADR-0381). Avoid: container,
service, stack.

**App in unos-smb**: An app installed with the option to run in the group unos-smb (PUID 80000,
PGID 70000, UMASK 007), which only apps of linuxserver.io images with a folder besides their own
data have: the only kind of app that is given a share's folder (ADR-0274). Avoid: SMB app, app
in the SMB group.

**Container**: Any Docker container on the host, as the Containers page lists it; an app runs as
one or more containers. Avoid: app.

**Companion container**: A container of an app besides its main container (the one of the
template's own image, ports and overrides, named after the app): named `<app>-<name>` and
labelled `unos.app.companion=<name>`, on the app's network `unos-app-<app>`, where it answers to
its name, as Immich's `database`, `redis` and `machine-learning`, BookStack's and Linkwarden's
`database` and Paperless-ngx's `redis` (ADR-0415). It publishes no port and takes no override; the
main container starts once those it depends on are ready (ADR-0381). Avoid: sidecar, service.

**Ready command**: The command that UNOS runs in a companion container whose image has no health
check (Docker's exec), every 2 seconds while the next container waits for it, at the install and
at each start of its app: the companion is ready once it exits 0, such as MariaDB's
`healthcheck.sh --connect` and PostgreSQL's `pg_isready` (`ContainerSpec.ready_command`,
ADR-0415). Docker runs nothing between the waits. Avoid: health check (Docker's own, which writes
its state to the system disk at each probe).

**Host gateway**: The name `host.docker.internal`, which UNOS points at the NAS itself
(Docker's `host-gateway`) in the container of an app that reaches another app's port on the NAS:
Open WebUI's Ollama, Zigbee2MQTT's Mosquitto (`AppTemplate.host_gateway`, ADR-0415). Avoid: host
network (an app in the host's network shares the NAS's network itself).

**Install job**: An app's install as it runs in the background: `POST /api/v1/apps/install`
answers it at once, and it checks, pulls the images, creates and starts the containers and
reports each step, each image's download and each container's state, then its result or its
error, to the App Store over the events socket. Kept in memory only, the running one and the last
of each app; one runs at a time (ADR-0384). An app update and an app rollback run as jobs of the
same kind and share that one slot (ADR-0408). Avoid: install task (a task is the Scheduler's), pull
(one step of it).

**App update**: Recreating an installed app's containers from the catalog's template when the
catalog pins newer images for it, keeping its data folders, generated secrets, settings, ports,
group option and limits: before it runs, the App Store shows each image, what the pulls write to the
system disk and the database copy; then a job pulls, stops the app, makes the database copy, keeps
the earlier version and starts the new containers, or returns the app to its earlier version when
something fails after the stop. Only an image built after the one that runs is taken, and only apps
whose catalog pins its images are updated (ADR-0408). Avoid: upgrade, reinstall, `update.sh` (which
updates UNOS itself).

**Database copy**: The copy of a folder of an app's own data that the catalog marks
`copy_before_update` (Immich's `postgres/data`), which an app update makes beside it on the same pool
(`<folder>.previous`) after the app stopped and before the new version starts, and which an app
rollback puts back in its place (ADR-0408). Avoid: database backup, dump, snapshot.

**Earlier version** (of an app): The containers that an app update kept, stopped, as
`<name>.previous`, with their images, and its database copy, until an app rollback returns to them or
**Remove earlier version** deletes them; another update waits until then (ADR-0408). Avoid: old
version, backup.

**App rollback**: Returning an updated app to its earlier version: the newer containers are removed,
the database copy replaces the database (what the app stored since the update is lost), and the
earlier containers get their names back and start (ADR-0408). Avoid: restore (a backup's), rollback
alone (what `update.sh` and `deploy.sh` do).

**Generated secret**: A password or key file that UNOS writes once, at an app's install, beside
the data it opens or signs on the pool (64 hexadecimal digits, or the `secret_digits` of its
mapping, such as the 32 of BookStack's `APP_KEY`; root, mode 0600), binds read-only into the
containers that read it and never shows, replaces or makes at a start; an install that finds
that data without the file is refused (ADR-0381, ADR-0415). Avoid: password variable.

**Pinned image**: An image of the catalog named by a version tag and the digest of its index
(`name:tag@sha256:...`), pulled and run by that digest, with its size on the system disk
estimated for the install's space check and the `VOLUME`s it declares (`PINNED_IMAGES`,
`APP_IMAGES`, ADR-0381, ADR-0410). Every image of the catalog is one. Avoid: locked image.

**Moved tag**: The tag of a pinned image that names another index at its registry than the one
pinned, because the publisher pushed that version again or a line tag (Traefik's `v3.7`) moved on
to a newer release; the weekly `tags` audit reports it (`make catalog-tags`, ADR-0410). Avoid:
stale pin, outdated image.

**Image platforms**: The Linux platforms (`linux/amd64`, `linux/arm64`, `linux/riscv64`, `linux/arm/v7`)
that the registry's manifest list publishes for an image of the catalog, recorded in
`supervisor/src/apps/image_platforms.json` and checked weekly by `scripts/catalog-platforms.py`.
An app is available on a host when every image of its containers publishes the host's platform;
otherwise the App Store marks it not available and its install answers 409 (ADR-0403). Avoid:
architecture (the System page's `architecture` is the host's machine name).

**Hardware profile**: The hardware of a SoC that a container of an app gets when it is installed on
a host whose device tree names that SoC and that has the nodes the profile requires: the device
nodes that the host has of the profile's list, the Docker masks and AppArmor profile the
container cannot work under, and for some an image built for that hardware, as Jellyfin's and
Immich's server's `rk3588-video` on an RK3588 with Rockchip MPP's `/dev/mpp_service`, and Immich's
machine learning's `rk3588-npu` with Immich's `-rknn` image on an RK3588 with the NPU's render
node; on any other host the container is installed as elsewhere, and the App Store says nothing
of it (ADR-0382). Avoid: hardware acceleration (what the App Store calls it for the user),
passthrough.

**Container limits**: The memory limit and the CPU limit that an administrator sets for one
container of an app on the Installed tab, each optional: Docker keeps them in the container's
HostConfig (`docker inspect`: Memory and MemorySwap, CpuQuota per CpuPeriod), and UNOS keeps them
in its database for the app's next install. Each container of an app has its own (ADR-0390).
Avoid: quota (a volume's), app limits.

**Container use**: The memory and the CPUs that a running container of an app uses, which UNOS
reads every 30 seconds from the container's cgroup in the kernel: memory.current without the
inactive page cache, and the CPU time between two reads (ADR-0390). Avoid: stats (Docker's
stats stream, which UNOS does not read).

**Backup**: An archive of the UNOS database, encrypted if wanted, without the pools' files
(ADR-0040). Avoid: snapshot.

**Scheduled backup**: A backup that a backup task of the Scheduler makes,
`auto_backup_<date>_<time>.tar.gz.enc`, always encrypted with the task's passphrase, of which the
device keeps only a derived key (ADR-0325). Avoid: auto backup.

**Restore**: Replacing the live database with a backup's after the archive and the database are
checked (ADR-0040, ADR-0144). Restoring a snapshot is another action: it makes a copy.
Avoid: rollback (what `update.sh` and `deploy.sh` do).

**Off-device copy**: A copy of the configuration (the archive of Create Backup) and of the pools'
folders that restic keeps in the copy repository, encrypted with a password that only the NAS
keeps, by a task of the Scheduler or Run now; each copy is checked and the restore of its
configuration tested, and its snapshots restore the configuration into the backup list and the
files into a new folder of a pool (ADR-0337). Avoid: offsite backup, sync, cloud backup.

**Copy repository**: The one restic repository of an installation's off-device copies: a folder on
a disk mounted below `/mnt` that is not the system disk, a bucket of an S3-compatible store, a
repository of a restic REST server, or a folder of an SFTP server (ADR-0422). Saving its settings
opens it with its password, or makes it; a copy never makes one (ADR-0337).
Avoid: backup target, vault, bucket (unless it is one).

**Key pair of UNOS**: The SSH key pair (Ed25519) with which the off-device copy logs in to an SFTP
server: UNOS makes it, the section shows its public key for the server's authorized keys, and its
private key stays in the copy's secrets file, never answered (ADR-0422). Avoid: SSH key of the NAS
(the host's own keys are other ones), deploy key.

**Pinned host key**: The SFTP server's host key that the first test of the copy's settings took,
or that a save trusted anew; every command of the copy holds to it, and a server that shows another
one is refused (ADR-0422). Avoid: known host, fingerprint (the pinned key has one).

## Development

**ADR**: An architecture decision record, one for each decision (ADR-0001). Avoid: decision note,
spec.

**Feature ledger**: The user-visible features, each with its steps, the tests that verify it and
a `passes` flag: `feature-list.json` (ADR-0203). Avoid: backlog, roadmap.

**Progress journal**: Where the work stands (Now), the dev device's state and a dated Log:
`PROGRESS.md` (ADR-0204). Avoid: changelog (the release notes), diary.

**Handoff**: A git-ignored document for one named next goal of the next session, written by the
handoff skill (ADR-0204). Avoid: summary, progress report.

**Base image**: The image that a Dockerfile's `FROM` line starts from, named by tag and digest
(`python:3.11-slim@sha256:...`): the digest decides what is built, and only a commit that changes
it moves the base (ADR-0305). Avoid: parent image.

**Action pin**: The commit SHA that a workflow's `uses:` line names for a GitHub Action, with its
release's version in a comment (`actions/checkout@3d3c42e... # v7.0.1`): a tag can be moved to
other code, a commit cannot, and Dependabot proposes the next release's (ADR-0423). Avoid: action
tag.

**Audit environment**: The temporary virtual environment in which `scripts/audit-python.sh`
resolves the supervisor's runtime dependencies for pip-audit, with pip, setuptools, wheel and
packaging from `.github/audit-env-requirements.txt`, pinned with their hashes (ADR-0423).

**Quality gate**: `make check`: the same steps as CI's test, lint and frontend jobs (ADR-0216).
Avoid: CI (which also runs the audits and the Linux-only tests).

**Green run**: The state of a working tree (HEAD, the index, the changes and the untracked files)
in which `make check` or `make check-fast` last passed, recorded by `scripts/green-run.py`; the
pre-commit hook and the Stop hook do not run `make check-fast` again for it (ADR-0383).
Avoid: cache, stamp.

**Independent review**: The review of a change by an agent with a fresh context, which gets the
diff, the ledger entry and its ADRs and tries to refute the change (ADR-0017).
Avoid: self-review.

**Guard**: The hook that blocks risky agent commands before they run (ADR-0211). Avoid: git hook.

**Background command**: A command that an agent's Bash tool runs with `run_in_background`; the
session wakes when it ends, so it is one call of `timeout` with a deadline of at most 2 hours
(ADR-0335). Avoid: background job (what a shell starts with `&`).

**Local stand-in**: The real supervisor and a stand-in for Caddy, run on the workstation with all
their data in a git-ignored folder: the default target of the E2E specs (ADR-0215).
Avoid: mock server, staging, test stack.

**Test stack**: The Docker Compose project `unos-test`, for a machine that runs Docker: the other
local target of the E2E specs (ADR-0215). Avoid: stand-in.

**Fake host**: An in-memory double of the host in backend tests, which keeps state and fails on
commands it does not know (ADR-0189). Avoid: mock.

**Linux test runner**: The backend tests run in the dev device's RAM, for the tests that only run
on Linux (ADR-0187). Avoid: CI.

**Device lock**: The lock on the dev device that deploy steps, Linux test runs and scenario checks
hold, so that one agent at a time changes or loads the device (ADR-0210). Avoid: deploy lock.

**Scenario check**: A check of one area's user scenario on the dev device,
`scripts/dev/scenario.sh <area>`: through the API and on the host, with a temporary user, share,
volume, Scheduler task, folders or backups that it removes again (the smbconf check also edits smb.conf by hand
and restarts the supervisor, ADR-0309; the shares check turns guest access on for its share, ADR-0340; the scrub check scrubs the pool, ADR-0314; the offsite check
sets up an off-device copy and forgets it, ADR-0337; the restore check restores a backup it made moments before, twice,
which puts the device's database back as it was then and ends the web sessions, ADR-0341), PASS or FAIL
and the SD writes of each step,
and a comparison with how it found the device (ADR-0304). The feature-pass skill names the
scenario check of each host-level entry. Avoid: smoke test, E2E test.

**Device backup**: A copy of the dev device's fstab, smb.conf and data volume on the workstation
(`tmp/dev/backup`), which a deploy takes before it starts the containers; it holds the device's
secrets (ADR-0223). Avoid: backup alone (see Backup), snapshot.

**dev.env**: The git-ignored file of the dev device's settings and secrets, read only by the device
scripts (ADR-0212). Avoid: .env, secrets.env (`/etc/unos/secrets.env` belongs to an installation).

**Phase, slice**: The units of the severity-first plan: Phase 0 hardening, then Phase 1 in slices
(slice 1 the broken user flows, slice 2 SMB access), then Phase 2 (ADR-0014). Avoid: sprint,
milestone, release.

**Host-level change**: A change whose effect reaches the host: disks, pools, Samba and SMB
accounts, the kernel's iSCSI targets, host files such as fstab, Docker apps and containers, the
host's network, power or settings, whatever code path does it. It is done only after a check on the dev device and a
reboot check (ADR-0018). Avoid: backend change.
