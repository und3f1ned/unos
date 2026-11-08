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

**Dev device**: The ArmSoM Sige7 board that UNOS is developed and checked on. It holds no data to
preserve (ADR-0220). Avoid: production NAS, the server.

**Workstation**: The owner's computer, where the repository is edited and tested, the local
stand-in runs and the web UI's static files are built (ADR-0016). Avoid: host, local server.

**System disk**: The disk that holds the host's root filesystem; it never joins a pool. On the dev
device it is an SD card whose writes are measured and kept minimal (ADR-0214). Avoid: eMMC (the
dev device's is an SD card), boot disk.

**Owner**: The person who owns the repository and the dev device: decides, triages, asks for
commits and pushes, opens pull requests and holds the credentials (ADR-0208, ADR-0024).
Avoid: user (a UNOS user), admin.

**Automatic revert**: What turns a network interface that UNOS disabled back on, with its saved
addresses and routes: a timer on the host that runs unless the administrator keeps the disable
within 60 seconds (ADR-0244). Avoid: rollback (what `update.sh` and `deploy.sh` do), undo.

**Notification channel**: Where UNOS sends notifications: e-mail through a mail server, a Telegram
chat, or the URL of a webhook, Discord or Slack, set up in System > Notifications (ADR-0260).
Avoid: notifier, integration.

**Alert**: A notification that a monitor sends on its own, for a pool's RAID errors or a disk's
rising SMART counters, to every enabled e-mail and Telegram channel (ADR-0047). Avoid: alarm.

**Notification history**: The latest 100 notifications sent, tests and alerts included, with the
channel and the result of each: History in System > Notifications (ADR-0260). Avoid: log (the
Logs tab shows the journal and container output).

## Storage

**Pool**: A btrfs filesystem on one or more disks that UNOS mounts under `/mnt/pools` (ADR-0034).
Avoid: array, volume, RAID.

**Managed pool**: A pool that UNOS mounts at every boot through its own entry in the host's fstab.
Only managed pools can hold shares (ADR-0034, ADR-0148). Avoid: mounted pool (a managed pool can
be unmounted).

**Pool root**: The top folder of a pool, which holds its apps, volumes and snapshots; it is never a
share's folder (ADR-0145). Avoid: pools directory (that is `/mnt/pools`).

**Volume**: A btrfs subvolume at the top of a pool, with an optional quota (ADR-0069).
Avoid: subvolume (snapshots are subvolumes too), dataset.

**Snapshot**: A read-only btrfs snapshot of a pool or a volume. Restoring one makes a writable copy
and overwrites nothing (ADR-0011). Avoid: backup.

**Trash**: The hidden folder `.unos-trash` at the top of each pool and volume that takes what Delete
in Files deletes there, until it is restored, deleted for good, or deleted for good 30 days after
its deletion (ADR-0259). Avoid: recycle bin, snapshot.

**SMART history**: The sector and error counters of each disk that the SMART monitor records every
hour while the disk is awake, kept by serial in `/data/smart_history.json` (ADR-0235). Avoid: SMART
log (smartctl's own self-test and error logs).

**Replace soon**: The mark of a connected disk whose failure prediction, a score of the rises in
its SMART history, reaches 50; Disk Health and the dashboard show it with its reasons (ADR-0235).
Avoid: failing (the disk's own SMART health check), degraded (a pool's health).

## Shares and SMB

**Share**: An SMB share: a folder inside a managed pool, or a volume, that Samba serves with
per-user access (ADR-0074, ADR-0145). Avoid: export (an NFS export), share folder (the folder
itself).

**Served share**: A share whose name and folder pass the rules, so that Samba serves it. The others
show as "Not served" and grant nothing (ADR-0145). Avoid: active share, enabled share.

**Share access**: One user's level on one share, read or write. Without it a user has no access to
the share, and it counts only while the user's SMB account is active (ADR-0075).
Avoid: permission, ACL.

**Guest share**: A share that guests may read without a password; guests never write (ADR-0096,
ADR-0100). Avoid: public share, anonymous share.

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
narrows access (ADR-0084, ADR-0086). Avoid: migration.

**Startup reconcile**: The task at the supervisor's start that brings the host accounts, Samba's
passwords and smb.conf in line with the database (ADR-0138). Avoid: sync, repair.

**Repair permissions**: An administrator's action on one share that gives the files already in it
the group unos-smb and removes other accounts' access (ADR-0155). Avoid: fix permissions, chmod,
reconcile.

**Network discovery**: How computers on the LAN find the NAS without its address: Windows through
WS-Discovery (wsdd2), macOS through Bonjour (the `_smb._tcp` service that smbd registers with
avahi-daemon); NetBIOS is off (ADR-0261). Avoid: network browsing (a share's "Show in network
browsing" lists the share among the NAS's shares), NetBIOS browsing.

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
it expires (ADR-0009, ADR-0028). Avoid: cookie, API token.

**API token**: A long-lived token that a user creates for scripts, with that user's rights, shown
once (ADR-0239); only a session token creates, disables and deletes API tokens (ADR-0253). Avoid:
session token, API key.

**Recovery code**: One of the ten codes that a user with two-factor authentication generates on the
Account page and keeps; each one signs in once in place of a code of the authenticator app
(ADR-0238). Avoid: backup code.

## Apps and backups

**App**: An application installed from the App Store catalog, with its data on a pool (ADR-0039).
Avoid: container, service.

**Container**: Any Docker container on the host, as the Containers page lists it; an app runs as
one or more containers. Avoid: app.

**Backup**: An archive of the UNOS database, encrypted if wanted, without the pools' files
(ADR-0040). Avoid: snapshot.

**Scheduled backup**: A backup that a backup task of the Scheduler makes,
`auto_backup_<date>_<time>.tar.gz.enc`, always encrypted with the task's passphrase, of which the
device keeps only a derived key (ADR-0240). Avoid: auto backup.

**Restore**: Replacing the live database with a backup's after the archive and the database are
checked (ADR-0040, ADR-0144). Restoring a snapshot is another action: it makes a copy.
Avoid: rollback (what `update.sh` and `deploy.sh` do).

## Development

**ADR**: An architecture decision record, one for each decision (ADR-0001). Avoid: decision note,
spec.

**Feature ledger**: The user-visible features, each with its steps, the tests that verify it and
a `passes` flag: `feature-list.json` (ADR-0203). Avoid: backlog, roadmap.

**Progress journal**: Where the work stands (Now), the dev device's state and a dated Log:
`PROGRESS.md` (ADR-0204). Avoid: changelog (the release notes), diary.

**Handoff**: A git-ignored document for one named next goal of the next session, written by the
handoff skill (ADR-0204). Avoid: summary, progress report.

**Quality gate**: `make check`: the same steps as CI's test, lint and frontend jobs (ADR-0216).
Avoid: CI (which also runs the audits and the Linux-only tests).

**Independent review**: The review of a change by an agent with a fresh context, which gets the
diff, the ledger entry and its ADRs and tries to refute the change (ADR-0017).
Avoid: self-review.

**Guard**: The hook that blocks risky agent commands before they run (ADR-0211). Avoid: git hook.

**Local stand-in**: The real supervisor and a stand-in for Caddy, run on the workstation with all
their data in a git-ignored folder: the default target of the E2E specs (ADR-0215).
Avoid: mock server, staging, test stack.

**Test stack**: The Docker Compose project `unos-test`, for a machine that runs Docker: the other
local target of the E2E specs (ADR-0215). Avoid: stand-in.

**Fake host**: An in-memory double of the host in backend tests, which keeps state and fails on
commands it does not know (ADR-0189). Avoid: mock.

**Linux test runner**: The backend tests run in the dev device's RAM, for the tests that only run
on Linux (ADR-0187). Avoid: CI.

**Device lock**: The lock on the dev device that deploy steps and Linux test runs hold, so that one
agent at a time changes or loads the device (ADR-0210). Avoid: deploy lock.

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
