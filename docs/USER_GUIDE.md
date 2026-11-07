# UNOS User Guide

Welcome to UNOS! This guide will help you get the most out of your NAS.

## Table of Contents

1. [Getting Started](#getting-started)
2. [Dashboard](#dashboard)
3. [Storage Management](#storage-management)
4. [File Sharing](#file-sharing)
5. [File Browser](#file-browser)
6. [Docker Containers](#docker-containers)
7. [App Store](#app-store)
8. [User Management](#user-management)
9. [System Settings](#system-settings)
10. [Backups & Snapshots](#backups--snapshots)
11. [Notifications](#notifications)
12. [Tips & Best Practices](#tips--best-practices)

---

## Getting Started

### First Login

1. Open your web browser
2. Navigate to `https://YOUR_NAS_IP` (plain `http://` redirects there)
3. Your browser warns about the certificate: UNOS issues it from its own
   certificate authority. Continue to the site, or make your devices trust that
   authority once (see
   [HTTPS and the Certificate Warning](INSTALL.md#https-and-the-certificate-warning)).
4. On the very first visit UNOS asks you to create the administrator account:
   choose a user name and a password (at least 8 characters). There are no
   default credentials.
5. Afterwards you sign in on the login page.

### Interface Overview

The UNOS interface consists of:
- **Sidebar** - Navigation menu on the left, with your account and **Logout**
  at the bottom. Regular users see **Dashboard** and **Account** only (see
  [User Roles](#user-roles)).
- **Main Content** - Current page content

---

## Dashboard

The dashboard provides an at-a-glance view of your NAS:

### System Stats
- **CPU** - Current processor usage
- **Memory** - RAM usage and total
- **Network** - Current upload/download speeds
- **Temperature** - System temperature (if sensors available)

### Storage Overview
Shows all your storage pools with:
- Used/total space
- Health status
- Quick access to pool details

### Containers
Lists running Docker containers with status indicators

### Quick Actions
Common tasks like creating shares, managing pools, etc.

### Recent Activity
Latest system events and notifications

---

## Storage Management

### Understanding Storage Pools

UNOS uses BTRFS for storage management. A **storage pool** combines one or more disks into a single storage space.

### Viewing Disks

Go to **Storage** to see:
- All available disks
- Disk model, size, and serial number
- Current mount status

### Creating a Storage Pool

1. Go to **Storage**
2. Click **Create Pool**
3. Enter a pool name (e.g., "data", "media")
4. Select disks to include. System disks and disks in use cannot be selected.
5. Choose a RAID profile:

| Profile | Min Disks | Redundancy | Space Efficiency |
|---------|-----------|------------|------------------|
| Single | 1 | None | 100% |
| RAID 0 | 2 | None | 100% |
| RAID 1 | 2 | 1 disk failure | 50% |
| RAID 1C3 | 3 | 2 disk failures | 33% |
| RAID 1C4 | 4 | 3 disk failures | 25% |
| RAID 10 | 4 | 1 per pair | 50% |

> **Note:** BTRFS RAID5 and RAID6 are not offered because they have a known
> "write hole" bug that can cause silent data corruption on power failure.
> UNOS prioritizes data safety. Use RAID 1C3 (3 copies) or RAID 1C4 (4 copies)
> for multi-disk redundancy beyond RAID 1.

6. Type the pool name again to confirm. If a selected disk already contains
   data, also tick **Erase the existing data on these disks**.
7. Click **Erase Disks & Create Pool**

UNOS mounts the new pool at `/mnt/pools/<name>` and adds it to the host's
`/etc/fstab`, so it is mounted again after a reboot.

### Pool Operations

For each pool, you can:
- **View Details** - Space usage, devices, status
- **Mount / Unmount** - Mount the pool (and mount it at every boot), or unmount
  it; the data on the disks is not touched. Unmounting is refused while a
  running app or container uses the pool, also through a Docker volume that
  binds a folder in it, or while files on it are open
- **Add Devices** - Expand the pool
- **Scrub** - Check data integrity
- **Volumes** - (mounted pools) List, create and delete volumes: btrfs
  subvolumes in the pool's top folder (`/mnt/pools/<pool>/<name>`) that can be
  shared on their own. Snapshots of the pool, manual or scheduled, do not
  include the files in its volumes. Deleting a volume deletes its files and
  asks you to type its name. Deleting is refused while a running app or
  container, an SMB share or an NFS export uses the volume or a folder in it,
  and while the volume holds snapshots or other subvolumes, which must be
  deleted first. A pool mounted read-only lists its volumes but cannot create
  or delete them.
- **Balance** - Redistribute data across devices
- **Delete** - Unmount the pool and remove it from `/etc/fstab`. The disks are
  not wiped: the data stays on them and the pool can be mounted again later.
  Like Unmount, it is refused while the pool is in use; SMB shares and NFS
  exports of the pool are not checked: remove them first.

### SMART Monitoring

**System** → **Disk Health** shows the model, size, temperature and SMART
health status of each disk that the **Storage** page lists, the system disk
included unless it is an SD card or eMMC.

---

## File Sharing

### SMB Shares (Windows/Mac Compatible)

SMB shares give Windows, macOS and Linux computers on your network access to
folders of your pools. UNOS writes the whole Samba configuration
(`/etc/samba/smb.conf`) from its database; changes made to that file by hand
are overwritten.

#### How Access Works

- **SMB is off for every user until an administrator allows it** on the
  **Users** page (see [SMB Access](#smb-access)). Users then open shares
  with their UNOS username and password.
- **Each share has its own access list:** for each user **No access**,
  **Read only** or **Read & write**. New users have no access to any share.
- **Administrators cannot use SMB.** Their password controls the whole NAS,
  and SMB has neither two-factor authentication nor a limit on login
  attempts. Use a regular account for your files.
- Everyone with **Read & write** on a share can create, change, rename and
  delete every file in it, and everyone with **Read only** can read all of
  it. For files that only some people may see, use separate shares.

#### What Can Be Shared

A share is a folder inside a pool, or a volume. UNOS refuses:
- the top level of a pool, which holds app data (`apps/`), the volumes and
  the snapshots;
- `apps` and everything below it, and `.snapshots`;
- folders outside the pools that UNOS manages;
- a folder inside another share's folder, or one that contains another
  share's folder: shares do not overlap.

A share marked **Not served** breaks one of these rules, for example a share
of a whole pool imported from the host's existing smb.conf. Nobody can open it
until you give it a new folder with **Edit**; it cannot be given access until
then.

A share's folder must also be inside folders that only root can change:
whoever can change a folder above it can put another folder in its place.
Below a folder that other accounts can change (mode 0777, for example), UNOS
creates no new share folder and does not run **Repair permissions**: the
message names the folder and the command to run on the NAS, for example
`sudo chmod 755 /mnt/pools/data`. A share of a folder that exists already is
created with a warning, and the status panel of the **Shares** page lists
every served share below such a folder until the command has run.

#### Creating a Share

1. Go to **Shares** and click **Add SMB share**
2. Configure:
   - **Name** - The share name that computers on the network see: up to 32
     letters, digits, `_` and `-`, not starting with `-`. `global`,
     `globals`, `homes` and `printers` are reserved, in any letter case.
   - **Location** - A pool or a volume, and a **Folder** inside it. The folder
     is required for a pool; for a volume, leave it empty to share the whole
     volume. A missing folder is created (see
     [What Can Be Shared](#what-can-be-shared) for when it is not). Only pools
     that UNOS manages are listed: a pool marked **Not mounted at boot** on
     the **Storage** page is not, until you stop the apps that use it and
     press **Unmount** and then **Mount** there (or reboot and press
     **Mount**).
   - **Comment** - Optional description
   - **Show in network browsing** - Whether the share appears in the list of
     shares (a hidden share can still be opened by its name)
   - **Guests can read this share without a password** - See
     [Guest Shares](#guest-shares)
   - **Access** - For each user: No access, Read only or Read & write
3. Click **Create**

Folders that apps use (downloads, media) can be shared. SMB users with write
access then change what the app sees: they can add, change, rename or delete
its files there.

#### Guest Shares

Anyone on your network can read a guest share without a password, and so can
every SMB account, also one made by hand on the NAS. Its name is visible to
anyone on your network. Guests can never write. While a guest share exists,
connections to the NAS are not required to be signed: guest connections
cannot be signed, and clients may refuse to connect as a guest to a NAS that
requires signing. Windows 11 refuses guest access by default, and guest
connections are neither signed nor encrypted. Use guest shares only for
files everyone on your network may read, and keep them few.

#### Editing a Share

- **Edit** changes the comment, network browsing, guest access and the
  access list (and the folder of a share marked **Not served**). Removing or
  lowering someone's access, and turning guest access off, disconnect every
  open connection to that share; the computers reconnect by themselves.
  Adding access disconnects nobody.
- **Repair permissions** fixes the permissions of the files that are already
  in the share (see below).
- **Delete** removes the share; the folder and its files are kept. Open
  connections to the share are closed.

#### Repair Permissions

UNOS creates share folders with the group `unos-smb` and the mode 2770, and
files created over SMB get that group and the mode 660. Files that did not
come in over SMB (from an app, from the File Browser, or already in the folder
when the share was created) keep their permissions until you press **Repair
permissions** on the share. A share marked **Permissions need repair** needs
it. Repairing:
- gives every file and folder of the share the group `unos-smb` and group
  read and write access (folders 2770, files 660, or 770 for files their
  owner may run);
- **removes the access that the group and other permissions gave every
  other account**, including apps that run as their own user (for example
  PUID 1000): they can no longer read the folder;
- does not change ACL entries (set with `setfacl`; `ls -l` shows a `+`): the
  users and groups named in them keep their access, and can get more (up to
  what their entry gives), because the group permissions that Repair sets
  become the ACL's mask. On such a file or folder the group `unos-smb` gets
  only what the ACL's entry for the owning group gives, and the entries of a
  folder's default ACL are also given to new files in it. Remove the entries
  you do not need (for example `sudo setfacl -R -b FOLDER` removes those of
  FOLDER and everything in it), then repair again;
- leaves private files and folders alone: those only their owner can use,
  such as 0600 keys or a 0700 folder;
- changes no owner and no file content;
- skips snapshots, volumes and other mounts inside the share, symbolic
  links, and files with more than one hard link;
- can take minutes, and stops after 4 minutes: run it again to continue
  (entries that are already right are skipped quickly).

It changes the permissions of existing files and cannot be undone
automatically. It is refused while a folder above the share's folder can be
changed by other accounts (see [What Can Be Shared](#what-can-be-shared)).

#### Accessing Shares

Sign in with your UNOS username and password, after SMB is turned on for your
account. Use the NAS's address on your local network (IPv4, or an IPv6 ULA or
link-local address): SMB refuses public addresses. **Account** → **Network
file access (SMB)** lists the shares you can use and how to connect to them.

**Windows:**
1. Open File Explorer
2. Type `\\YOUR_NAS_IP\sharename` in the address bar
3. Enter your UNOS username and password

For an IPv6 address, Windows needs the ipv6-literal.net form: the address
with `-` instead of each `:` (and `s` instead of the `%` of a zone index),
followed by `.ipv6-literal.net`, for example
`\\fd00--103.ipv6-literal.net\sharename` for `fd00::103`. The `smb://`
addresses below take the address in brackets: `smb://[fd00::103]/sharename`.

**macOS:**
1. Open Finder
2. Choose **Go** → **Connect to Server** (`Cmd+K`)
3. Enter `smb://YOUR_NAS_IP/sharename`
4. Sign in as a registered user with your UNOS username and password

**Linux:**
```bash
# Mount (asks for your UNOS password)
sudo mount -t cifs //YOUR_NAS_IP/sharename /mnt/sharename -o username=YOUR_USERNAME

# Or open in a file manager
smb://YOUR_NAS_IP/sharename
```

If a computer connected as a guest or with another account before, remove
the saved sign-in first, or it keeps using it: on macOS in **Keychain
Access** (the entry with the NAS's address), on Windows in **Credential
Manager** → **Windows Credentials**.

---

## File Browser

The built-in file browser (**Files** in the sidebar) lets you manage the files
in your storage pools from the web interface. It starts in the pools directory
(`/mnt/pools`), which has a folder for each pool, and cannot go above it.

### Features

- **Navigate** - Double-click a folder to open it. Go back with the
  breadcrumbs at the top (**Pools** is the pools directory) or with **..** at
  the top of the list. Folders are listed first, then the other items, by
  name.
- **Select** - Click a row or its checkbox to select the item, and again to
  deselect it. You can select several items. Opening another folder clears
  the selection.
- **Download** - Select one file and click **Download**, or double-click the
  file. Folders cannot be downloaded. The browser shows the download once the
  whole file has arrived; until then it holds the file in its memory, so a
  very large file needs that much free memory on your computer.
- **Copy and Move** - Select the items and click **Copy** or **Move**, open
  the destination folder, then click **Copy here** or **Move here**. If an
  item with the same name is already there, nothing is copied or moved.
- **Rename** - Select one item and click **Rename**: its name becomes a field
  with the name selected. Type the new name, then press Enter or click
  elsewhere; Escape keeps the old name. Switching to another window or tab
  keeps the field as it is. A name that is already used in the folder is
  refused, and so are `.`, `..`, names with `/` or control characters, and
  names longer than 255 bytes: the message says why, and the field stays open
  for another name. Spaces at the start and the end of the name are dropped.
  The item stays selected under its new name. Until the list shows the new
  name, the buttons for the selected items and **Copy here** or **Move here**
  wait.
- **Delete** - Select the items, click **Delete** and confirm. Folders are
  deleted with everything in them.
- **Upload** - Click the upload arrow at the top right and choose one or more
  files. They go to the folder that was open when you chose them, also if you
  open another folder while they upload. A file with the same name is
  replaced.
- **New Folder** - Click **+** at the top right, type the name, then press
  Enter or click **Create**.
- **Keyboard and screen readers** - The name of each folder and file is a
  button: Tab to it, then press Enter or Space to open the folder or download
  the file. After a folder opens, the focus is on **..**. The checkbox selects
  the item. After a rename, or Escape in the name field, the focus is on the
  item's checkbox, unless Tab or a click took it to another control. Screen
  readers announce error messages.

Uploads, new folders, copies and moves go into the folders of a pool, not
into the pools directory itself. The folder of a pool, its volumes, and other
subvolumes or filesystems mounted in a pool cannot be deleted, renamed or
moved here: delete volumes on the **Storage** page (see
[Pool Operations](#pool-operations)). Renaming a folder that a share, an NFS
export or an app uses is not refused, but they keep using the old name, which
no longer exists: rename the folder back to restore them.

A folder that contains a subvolume (such as a snapshot) or a mounted
filesystem cannot be deleted here, or moved to another pool or volume.

---

## Docker Containers

UNOS can manage Docker containers for running additional applications.

### Viewing Containers

Go to **Containers** to see:
- Container name and image
- Status (running, stopped, etc.)
- Resource usage
- Ports exposed

### Container Actions

- **Start/Stop** - Control container state
- **Restart** - Restart the container
- **Logs** - View container output
- **Remove** - Delete the container

### Creating Containers

1. Click **Create Container**
2. Enter the image name (e.g., `nginx:latest`)
3. Configure:
   - Container name
   - Port mappings
   - Environment variables
   - Volume mounts
4. Click **Create**

---

## App Store

The App Store provides one-click installation of popular applications.

### Available Categories

- **Media** - Plex, Jellyfin, Emby
- **Downloads** - qBittorrent, Transmission
- **Productivity** - Nextcloud, Paperless
- **Home Automation** - Home Assistant
- **Development** - Gitea, Code Server
- **Monitoring** - Grafana, Uptime Kuma

### Installing an App

1. Go to **App Store**
2. Browse or search for an app
3. Click **Install**
4. Choose the **Storage pool** for the app's data (apps that keep data need a
   mounted pool; create or mount one on the **Storage** page first) and
   configure settings if prompted
5. Wait for installation to complete

Starting or restarting an app from UNOS fails while its data folders are not
on a mounted pool.

### Managing Installed Apps

Go to **App Store** → **Installed** to:
- View installed apps
- Start/stop apps
- Update apps
- Remove apps

---

## User Management

### Creating Users

1. Go to **Users** (administrators only)
2. Click **Add User**
3. Enter:
   - Username: 1 to 32 lowercase letters, digits, `_` or `-`, starting with a
     letter. It must differ from every other username in more than letter
     case (`Bob` and `bob` are one name for SMB). Names that start with `s-`
     and a digit (such as `s-1-1-0`) are reserved: Samba reads them as
     security identifiers (SIDs).
   - Password (twice, at least 8 characters)
   - **Allow SMB (network file) access** (optional): turns SMB on for the new
     user with this password (see [SMB Access](#smb-access))
4. Click **Create User**

New accounts are regular users. To make one an administrator, edit it and
tick **Administrator privileges**.

### User Roles

| Role | Permissions |
|------|-------------|
| Admin | Full access to all features, except SMB: administrators cannot use SMB |
| User | **Dashboard** (system overview and live statistics) and **Account** (own password, two-factor authentication, login history and SMB access); SMB shares as an administrator allows |

Every other page and API is for administrators only; regular users do not see
them. At least one administrator must remain: the last one cannot be demoted
or deleted. Making a user an administrator removes the user's SMB access and
share permissions and ends the user's open SMB connections.

### SMB Access

SMB (network file access) is off for every user until an administrator allows
it. The **SMB** column of the **Users** page shows each user's state:

| State | Meaning |
|-------|---------|
| Active | The user can open shares over SMB with their UNOS username and password |
| Password needed | SMB is allowed, but Samba does not have the user's password yet |
| Off | SMB is not allowed for the user |
| Not available | Administrators, and usernames that SMB cannot use |

To allow SMB for a user, edit the user and turn on **Allow SMB access**. The
state is then **Password needed**, and SMB is turned on with a password in
one of two ways:
- the user goes to **Account** → **Network file access (SMB)**, enters their
  current password (and their two-factor code, if they use 2FA) and clicks
  **Turn on SMB**: the SMB password is the UNOS password;
- or you set a new password for the user in the edit dialog, with **Also turn
  SMB on with this password** ticked.

Then give the user access to shares on the **Shares** page.

SMB passwords need at least 8 characters. A user whose UNOS password is
shorter (scripts that use the API can set shorter ones) first changes it
under **Change Password**, then turns SMB on. A two-factor code works only once:
the code the user signed in with cannot turn SMB on as well, so wait until
the authenticator app shows the next one.

SMB cannot be turned on while Samba still uses an smb.conf that UNOS did not
write (HTTP 409): on a host that had an smb.conf before, UNOS imports its
shares and writes its own at its first start. When importing the shares of
the old file or writing the new one failed, the status panel of the
**Shares** page says why: "Shares were not imported from smb.conf: ..."
(UNOS writes its smb.conf only after that import) or "smb.conf was not
updated: ...". Allowing SMB and turning it off are not affected.

Turning **Allow SMB access** off (or the user's own **Turn off SMB**) ends the
user's open SMB connections. When Samba could not apply a user's own change
at once, the **Account** page shows a short warning without details; the
details are in the supervisor's log.

After a backup is restored, a user whose password changed since the backup
was made shows **Password needed** and must turn SMB on again.

### Changing Passwords

Users can change their own password:
1. Go to **Account** (administrators: **System** → **Security**)
2. Under **Change Password**, enter the current and the new password

Admins can reset any user's password via **Users** page.

When SMB is on for the user, the SMB password changes too, and the user's open
SMB connections are ended: the computers sign in again with the new password.
Update the saved password on every computer and app that connects over SMB
(mapped drives, media players, sync apps, `cifs` credential files): Samba
writes each failed sign-in of an SMB account to the system disk, and SMB has
no limit on sign-in attempts, so a device that keeps trying the old password
keeps writing. If open SMB connections could not be ended, **Change
Password** shows a warning that stays until you dismiss it.

### Two-Factor Authentication (2FA)

Protect your account with TOTP-based two-factor authentication:

1. Go to **Account** (administrators: **System** → **Security**)
2. Under **Two-Factor Authentication**, click **Enable**
3. Scan the QR code with an authenticator app (Google Authenticator, Authy,
   etc.). The QR code is drawn in your browser; the secret is never sent to
   another service.
4. Enter the verification code and click **Enable 2FA**

To disable 2FA:
1. Go to **Account** (administrators: **System** → **Security**)
2. Under **Two-Factor Authentication**, click **Disable**
3. Enter a current 2FA code and click **Disable 2FA**

Each code works once: a code that was accepted (to sign in, to turn SMB on,
or to turn 2FA on or off) is refused when it is used again, with "This
two-factor code was already used; wait for the next one". The app shows a new
code every 30 seconds.

2FA protects only the web interface, not SMB: SMB signs in with the password
alone. Turning SMB on yourself needs your two-factor code as well.

### API Tokens

Tokens give scripts access to the UNOS API with the rights of the account that
created them: a regular user's token cannot use the administrator-only APIs.
There is no page for them yet; create one with the API, using the
`access_token` that `POST /api/v1/auth/login` returns (the full API token is
shown only once):

```bash
curl --cacert unos-root-ca.crt -X POST https://unos/api/v1/auth/tokens \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "Backup Script"}'
```

Use tokens in API requests:
```bash
curl --cacert unos-root-ca.crt -H "Authorization: Bearer YOUR_TOKEN" https://unos/api/v1/system/info
```

`unos-root-ca.crt` is the UNOS root certificate (see
[Trust the UNOS Certificate Authority](INSTALL.md#trust-the-unos-certificate-authority)),
and `unos` the name on the certificate. List your tokens with
`GET /api/v1/auth/tokens` and revoke one with `DELETE /api/v1/auth/tokens/<id>`.

---

## System Settings

Access via **System** in the sidebar.

### General

- Shows the host name, the operating system, the kernel and the architecture
- Shows the UNOS version, as the Updates tab does
- The host name and the time zone cannot be changed in the web interface yet
  (feature-list entry `system.hostname-and-time-zone`)

### Updates

- **Current version**: the version UNOS was built from, as `git describe`
  named its checkout at the build: a release tag such as `v1.2.0`, or, marked
  **dev**, a tag with the commits after it such as `v1.2.0-3-g712fc3a` or only
  the commit while no tag was in its history. A release tagged later on the
  commit UNOS runs shows after a rebuild (see [Upgrading](INSTALL.md#upgrading)).
  An installation whose images were built without `install.sh` or `update.sh`
  shows `unknown`.
- **Latest release**: checked on GitHub when the tab opens, and again with
  **Check again**; **Include pre-releases** checks for pre-releases too. The
  tab says when an update is available, with the release's notes and a link
  to it, when you run the latest release, when no release is published yet,
  and why a check failed (GitHub answers 60 checks an hour per address).
- **How to update**: UNOS is updated on the host, with
  `sudo /opt/unos/deploy/update.sh` (see
  [Upgrading](INSTALL.md#upgrading)). The web interface cannot install updates
  or roll them back yet (feature-list entry `system.in-app-updater`).

### Network

- View network interfaces
- Check IP addresses
- Monitor network statistics
- View active connections

### Power Management

**Reboot** and **Shutdown** ask for confirmation, then act on the host one
minute later. Until then the tab shows the pending reboot or shutdown with its
time and a **Cancel reboot** or **Cancel shutdown** button, also after a
reload; a shutdown or reboot scheduled on the host's console shows there too.
**Suspend** asks for confirmation and suspends the host at once, if its kernel
offers suspend to RAM; the host then sleeps until its power button or a
Wake-on-LAN packet wakes it. The tab shows each answer, and the host's error
when it refused.

**Wake-on-LAN** lists the host's Ethernet interfaces with their MAC
addresses. **Turn on** lets a magic packet to the interface's address start
the host after a shutdown or wake it from suspend; **Turn off** lets nothing
wake the host through it. An interface that cannot wake the host by magic
packet shows **Not supported**. UNOS sets your choice again each time it
starts, because many network drivers forget it at a reboot. Send the magic
packet from a computer on the same network, for example with
`wakeonlan <MAC address>`.

There are no scheduled shutdown or wake times, and the UPS state has no page
yet (feature-list entries `system.scheduled-power` and
`system.ups-monitoring`).

### Disk Health

- SMART health status and temperature of each disk (see
  [SMART Monitoring](#smart-monitoring))

### Logs

- View system logs
- Filter by type and date
- Export logs for troubleshooting

---

## Backups & Snapshots

### Snapshots

BTRFS snapshots provide instant, space-efficient backups.

#### Creating a Snapshot

1. Go to **Snapshots**
2. Select a storage pool
3. Click **Create Snapshot**
4. Enter a name (optional)
5. Click **Create**

#### Restoring from Snapshot

Restoring does not overwrite the current data: it makes a writable copy of
the snapshot, `restore_<date>_<time>`, in the folder the snapshot was taken
of (for a snapshot of a pool, `/mnt/pools/<pool>/restore_<date>_<time>`).

1. Go to **Snapshots**
2. Find the snapshot to restore
3. Click **Restore** and confirm. A message shows where the copy is.

Copy what you need from it, for example in the **File Browser**. The copy of
a pool snapshot is a volume of the pool: when you no longer need it, delete
it with the pool's **Volumes** on the **Storage** page (see
[Pool Operations](#pool-operations)).

### System Backup

Go to **System** → **Backup & Restore**. A backup holds the UNOS database:
user accounts with their passwords, two-factor authentication, API tokens,
the login history, and the SMB shares with their access lists. It does not
hold the files in your pools, Samba's own password database, nor settings
kept elsewhere, such as notification channels, scheduled tasks, firewall
rules and UPS settings. Each backup in the list has **Restore**, **Download**
and **Delete** buttons.

- **Create Backup** - Makes an unencrypted backup (`.tar.gz`).
- **Upload Backup** - Adds a backup file of at most 256 MiB: `.tar.gz`, or
  `.tar.gz.enc` for an encrypted backup. It is stored under a new name,
  `uploaded_<date>_<time>` with the same extension. An unencrypted backup
  named `.tar.gz.enc` is refused.
- **Restore** - Type the backup's file name to confirm; an encrypted backup
  also needs its passphrase. Restoring replaces the database with the one in
  the backup: the user accounts with their passwords, two-factor
  authentication, API tokens and the login history are then those of the
  backup, so an API token revoked after the backup was made works again.
  Other files in a backup, such as `fstab`, are not restored; the result
  lists them.

SMB after a restore:
- The shares and their access lists come back with the database, and UNOS
  writes smb.conf from them; the copy of smb.conf that the `include_shares`
  option of the API adds to a backup is never installed.
- SMB keeps working for users whose password did not change since the backup
  was made. Everyone else shows **Password needed** and must turn SMB on
  again (see [SMB Access](#smb-access)), and so must a user whose SMB user id
  belongs to another account on this NAS (for example in a backup of another
  installation).

Encrypted backups (`.tar.gz.enc`) are made through the API, with
`POST /api/v1/backup/create` and the body
`{"encrypt": true, "passphrase": "..."}` (see [API Tokens](#api-tokens)).
Without the passphrase they cannot be restored.

For automatic backups, add a task of the type **System Backup** in
**System** → **Scheduler** (**New Task**). These backups are unencrypted,
are named `auto_backup_<date>_<time>.tar.gz` (with `_1`, `_2`, ... before
`.tar.gz` when two are made in the same second) and appear in the list;
**Keep Count** sets how many of them are kept.

---

## Notifications

UNOS sends two kinds of alerts on its own: pool (RAID) errors and disk health
(SMART) warnings. They go to every enabled e-mail and Telegram channel.

### Adding a Channel

1. Go to **System** → **Notifications**
2. Click **Add Email** or **Add Telegram**
3. Fill in the form: a channel name, and
   - for Email the SMTP host, its port (usually 587), the username and
     password, **Use TLS/STARTTLS**, the sender and the recipients;
   - for Telegram the bot token and the chat ID.
4. Leave **Enable Channel** on and click **Add Channel**
5. Send a test with the channel's **Send test notification** button (the
   lightning icon), which works while the channel is enabled

**History** lists the notifications sent, with their result.

Discord, Slack and generic webhook channels can be added only through the
API (`POST /api/v1/notifications/configs`). They receive test notifications
and what `POST /api/v1/notifications/send` sends, but no alerts yet
(feature-list entry `system.alert-events`).

### Notification Events

The form's **Notify on Events** choices are stored, but they have no effect
yet: every enabled e-mail and Telegram channel receives both kinds of alerts,
and no other event (container status, temperatures, backups) sends a
notification.

---

## Tips & Best Practices

### Security

1. ✅ Create the administrator account right after installing, with a strong password
2. ✅ Enable two-factor authentication (2FA)
3. ✅ Use strong, unique passwords, above all for accounts with SMB: SMB has
   no limit on login attempts and no two-factor authentication, and UNOS does
   not refuse common passwords
4. ✅ After a password change, update the saved password on every computer
   and app that connects over SMB: Samba writes each failed sign-in to the
   system disk
5. ✅ Use a regular account, not an administrator, for your files
6. ✅ Keep guest shares few (guests can only read)
7. ✅ Keep UNOS updated
8. ✅ Don't expose to the internet without VPN
9. ✅ Use API tokens instead of passwords for scripts
10. ✅ Review audit logs periodically

### Storage

1. ✅ Use RAID1 or higher for important data
2. ✅ Monitor SMART status regularly
3. ✅ Create regular snapshots
4. ✅ Don't fill pools beyond 80%
5. ✅ Run scrub monthly

### Performance

1. ✅ Use SSDs for frequently accessed data
2. ✅ Avoid running too many containers
3. ✅ Monitor resource usage
4. ✅ Keep system storage separate from data

### Backup Strategy

Follow the 3-2-1 rule:
- **3** copies of data
- **2** different storage types
- **1** offsite backup

---

## Getting Help

If you encounter issues:

1. Check **System** → **System Logs** for errors (administrators)
2. Review [Troubleshooting](INSTALL.md#troubleshooting)
3. Search [GitHub Issues](https://github.com/und3f1ned/unos/issues)
4. Ask in [Discussions](https://github.com/und3f1ned/unos/discussions)

---

## Glossary

| Term | Definition |
|------|------------|
| **BTRFS** | B-tree File System, a modern copy-on-write filesystem |
| **Pool** | A collection of disks managed as a single storage unit |
| **RAID** | Redundant Array of Independent Disks |
| **SMB** | Server Message Block, Windows file sharing protocol |
| **Snapshot** | A point-in-time copy of filesystem state |
| **Scrub** | Process to check and repair data integrity |
| **SMART** | Self-Monitoring, Analysis and Reporting Technology |
