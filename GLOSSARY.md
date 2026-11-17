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

**Text catalog**: The texts of one area of the web UI in English and Russian
(`webui/src/lib/i18n/catalog/<area>.ts`), which the pages show in the language chosen on the
Account page; the server's messages stay English (ADR-0287). Avoid: dictionary, locale file.

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
addresses and routes: a timer on the host that runs unless the administrator keeps the disable
within 60 seconds (ADR-0244). Avoid: rollback (what `update.sh` and `deploy.sh` do), undo.

**Scheduled task**: A task of System > Scheduler that runs at the times of its schedule on the
clock of the host's time zone: a snapshot, a scrub, a SMART test, a backup or a custom task. Each
run is recorded with its status in the task's history (ADR-0044, ADR-0266, ADR-0268). Avoid: job, cron job.

**Custom task**: A scheduled task that runs a shell command as root in the supervisor's container;
its runs record the command's output and exit status (ADR-0044, ADR-0267). Avoid: script.

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

**Recovery runbook**: `docs/RECOVERY.md`: how another Linux machine reads a pool's files without
writing to its disks, also with a disk missing, and how root runs the emergency reset (ADR-0285).
Avoid: disaster recovery plan, repair guide.

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

## Users and access

**UNOS user**: An account that people sign in to the web UI with: an administrator or a regular
user (ADR-0030). Avoid: account (alone), Linux user.

**Administrator**: A UNOS user who reaches every page and API and never has SMB access; first-run
setup creates the first one (ADR-0029, ADR-0030, ADR-0117). Avoid: root, superuser.

**Regular user**: A UNOS user who is not an administrator: sees Dashboard and Account only and may
get SMB access (ADR-0030). Avoid: normal user, guest (guests have no account).

**Host account**: An account of the host's operating system. The marked ones belong to SMB
accounts; UNOS leaves every other one alone (ADR-0108). Avoid: user.

**Session token**: The token that signing in returns; the web UI sends it with every request until
it expires or its session ends: Logout, a password change, an administrator (ADR-0009, ADR-0028,
ADR-0329). Avoid: cookie, API token.

**API token**: A long-lived token that a user creates for scripts, with that user's rights, shown
once (ADR-0239); only a session token creates, disables and deletes API tokens (ADR-0253). Avoid:
session token, API key.

**Recovery code**: One of the ten codes that a user with two-factor authentication generates on the
Account page and keeps; each one signs in once in place of a code of the authenticator app
(ADR-0238). Avoid: backup code.

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
its name, as Immich's `database`, `redis` and `machine-learning`. It publishes no port and takes no
override; the main container starts once those it depends on are ready (ADR-0381). Avoid:
sidecar, service.

**Install job**: An app's install as it runs in the background: `POST /api/v1/apps/install`
answers it at once, and it checks, pulls the images, creates and starts the containers and
reports each step, each image's download and each container's state, then its result or its
error, to the App Store over the events socket. Kept in memory only, the running one and the last
of each app; one runs at a time (ADR-0384). Avoid: install task (a task is the Scheduler's), pull
(one step of it).

**Generated secret**: A password file that UNOS writes once, at an app's install, beside the data
it opens on the pool (64 hexadecimal digits, root, mode 0600), binds read-only into the
containers that read it and never shows, replaces or makes at a start; an install that finds
that data without the file is refused (ADR-0381). Avoid: password variable.

**Pinned image**: An image of the catalog named by its tag and the digest of its index
(`name:tag@sha256:...`), pulled and run by that digest, with its size on the system disk
estimated for the install's space check (`PINNED_IMAGES`, ADR-0381). The templates of floating
tags are listed in `UNPINNED_TEMPLATES`, which only shrinks. Avoid: locked image.

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
a disk mounted below `/mnt` that is not the system disk, or a bucket of an S3-compatible store.
Saving its settings opens it with its password, or makes it; a copy never makes one (ADR-0337).
Avoid: backup target, vault, bucket (unless it is one).

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
accounts, host files such as fstab, Docker apps and containers, the host's network, power or
settings, whatever code path does it. It is done only after a check on the dev device and a
reboot check (ADR-0018). Avoid: backend change.
