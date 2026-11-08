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
9. [Audit Log](#audit-log)
10. [System Settings](#system-settings)
11. [Backups & Snapshots](#backups--snapshots)
12. [Notifications](#notifications)
13. [Tips & Best Practices](#tips--best-practices)

---

## Getting Started

### First Login

1. Open your web browser
2. Navigate to `https://YOUR_NAS_IP` (plain `http://` redirects there)
3. Your browser warns about the certificate: UNOS issues it from its own
   certificate authority. Continue to the site, or make your devices trust that
   authority once (see
   [HTTPS and the Certificate Warning](INSTALL.md#https-and-the-certificate-warning)).
4. On the very first visit UNOS opens **Create your admin account to get
   started** (`/setup`): choose a user name and a password (at least 8
   characters), enter the password again and click **Create Admin Account**.
   The dashboard opens, signed in as that administrator. There are no default
   credentials: until this account exists, nobody can sign in. Whoever opens a
   new installation first creates its administrator, so do it right after
   installing.
5. Afterwards you sign in on the login page. The setup page refuses a second
   administrator with "Admin user already exists. Use /login instead." and
   creates nobody; administrators add further users on the **Users** page.

A session lasts 24 hours. When it has ended, the page you have open goes to
the login page once, at its next request (also during an upload or a
download) or when the server ends its live statistics. **Logout** in one
browser tab signs out the other tabs too, and signing in as another account
in one tab switches the other tabs to that account.

Signing in works also while the disk that holds the UNOS data (`/data`, on the
system disk) is full, so that an administrator can sign in and free space, for
example by deleting old backups. The login history and the audit log then miss
the sign-ins they cannot store: the supervisor's log
(`docker logs unos-supervisor`) reports each record it could not write, with
its time. On a disk that is full to the last block, Docker may not be able to
keep those log lines either.
A recovery code does not sign you in while the disk is full, because UNOS
cannot mark it as used; a code of your authenticator app does.

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

### Disks to Replace
When a disk is marked **Replace soon**, administrators see an alert above the
widgets that lists each such disk with its path, serial number and the
reasons, and links to **System** → **Disk Health** (see
[Replace Soon](#replace-soon)). When UNOS cannot read the predictions, a
warning says why instead.

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

**Storage** → **Physical Disks** lists every whole disk of the host, the
system disk included, but not SD cards and eMMC (the boot medium of ARM
boards), flash, RAM, loop, optical and floppy devices. For each disk it
shows the device, the model and serial number, the size, HDD or SSD, what
it holds (a filesystem with its label, a partition table and its
partitions), where it is mounted, and its status:

- **System**: it holds the host's root filesystem, `/boot` or active swap,
  or the host boots from it;
- **In use**: it is mounted, a member of a mounted pool, or used by an md
  RAID array, LVM or LUKS;
- **Pool "name"**: it belongs to a pool that is not mounted;
- **Has data**: it holds a filesystem, a partition table or another
  signature;
- **Free**: it is empty.

The reason, such as "Mounted at /srv/backup", is shown below the badge.
When the disks cannot be listed, for example because `lsblk` fails on the
host, the card says so with the reason instead of the list; the pools are
still shown, and the other way round.

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

### Importing an Existing Pool

Disks that already hold a btrfs filesystem, from an earlier UNOS installation or
from another system, become a pool without being formatted:

- A filesystem whose label is a valid pool name (letters, digits, `-` and `_`,
  up to 63 characters, starting with a letter or digit) that no other
  filesystem has is listed under **Storage Pools** as **Not mounted**. Click
  **Mount**: UNOS mounts it at `/mnt/pools/<label>` by its UUID and adds it to
  the host's `/etc/fstab`, so it is mounted again after a reboot. This is how
  the pools of an earlier installation come back.
- Any other btrfs filesystem, one without a label, with a label that is not a
  pool name, or with the label of another filesystem, is listed under **Found
  on the disks** with its UUID, disks, size and why it is not a pool yet. Of
  two filesystems with one label, the one mounted as that pool, or else the
  one that UNOS mounts at every boot under that name, stays the pool; when
  neither is, both are listed under **Found on the disks** and neither under
  **Storage Pools**. Click **Import**, enter a pool name and click
  **Import**: UNOS mounts it at `/mnt/pools/<name>` by its UUID, makes the
  name the filesystem's label and adds it to `/etc/fstab`. The name cannot be
  the name of a pool or the label of another filesystem of the host. Nor can
  it be the name of a removed pool whose SMB shares, NFS exports or apps are
  still there, because the imported disks would serve them: the message names
  them, so choose another name or delete them first.

A btrfs filesystem on the SD card or the eMMC, on a loop or RAM disk, or on
the system disk is neither listed nor mounted: it never becomes a pool, as no
pool is created on those devices.

The data stays on the disks either way; an import changes only the label, so a
system that mounted the filesystem by its old label needs the new one. Nothing
is imported, and nothing is changed, while the filesystem is mounted on the
host, while one of its disks is missing (**Found on the disks** says how many
are; reconnect them first), or when it mounts a subvolume other than its top
level by default, because its default subvolume was changed with
`btrfs subvolume set-default`. To make the top level the default again, mount
the filesystem anywhere on the host, run
`btrfs subvolume set-default 5 <that mountpoint>`, unmount it and try again.
An import starts no apps. When the host's disks cannot be searched, **Found on
the disks** says why. btrfs on top of md RAID, LVM or LUKS is found only once
the host has assembled or opened it.

### Pool Operations

For each pool, you can:
- **View Details** - Space usage, devices, status
- **Mount / Unmount** - Mount the pool (and mount it at every boot), or unmount
  it after confirming; the data on the disks is not touched. Mounting is
  refused when the pool's filesystem mounts a subvolume other than its top
  level by default (see [Importing an Existing Pool](#importing-an-existing-pool)).
  An unmounted pool shows **Not mounted**, its fstab entry is removed, and its
  empty mountpoint is write-protected, so that nothing lands on the system
  disk there. Unmounting is refused while a running app or container uses the
  pool, also through a Docker volume that binds a folder in it, or while
  files on it are open, for example by a computer that has one of its SMB
  shares open; the dialog names the containers or the reason. Once the pool
  is unmounted, its SMB shares are no longer served: their open connections
  are closed, and the **Shares** page marks them **Not served** and **Pool
  not mounted**. They keep their access, and mounting the pool serves them
  again
- **Add Devices** - Expand the pool
- **Scrub** - (mounted pools) Check data integrity. While the scrub runs,
  the pool shows its progress (bytes, percent, rate and duration), updated
  every 2 seconds, and **Cancel Scrub** stops it. At the end it shows the
  result with the time the scrub started: **Last scrub: No errors**, the
  number of errors btrfs counted (read, super, verify and checksum errors),
  or that the scrub was cancelled or interrupted before it finished, with
  the errors found until then; start a scrub again to check the whole pool.
  Interrupted means that the scrub ended neither by finishing nor by a
  cancel, for example because the btrfs process that ran it was killed.
  When UNOS restarts, for example with the host, it forgets the result of
  the last scrub: the pool then shows none, also when the restart stopped a
  scrub. When btrfs cannot report the status, the pool says so with the
  reason. Scheduled scrubs are set up in **System** → **Scheduler**; a
  scheduled scrub of a pool that is not mounted fails without scrubbing
  anything
- **Volumes** - (mounted pools) List, create and delete volumes: btrfs
  subvolumes in the pool's top folder (`/mnt/pools/<pool>/<name>`) that can be
  shared on their own. **Quota in GiB** is optional: leave it empty for no
  limit, or enter a size such as `10` or `0.5`. A quota needs quotas enabled
  on the pool, which UNOS does not do itself: run
  `btrfs quota enable /mnt/pools/<pool>` on the host. Without them the volume
  is not created, the dialog says so, and you can create it without a quota.
  Snapshots of the pool, manual or scheduled, do not include the files in its
  volumes: take snapshots of each volume as well, on the **Snapshots** page
  (see [Snapshots](#snapshots)); scheduled snapshot tasks take pools only.
  Deleting a volume deletes its files and asks you to type its name.
  Deleting is refused while a running app or container, an SMB share or an
  NFS export uses the volume or a folder in it, and while the volume holds
  snapshots or other subvolumes, which must be deleted first. A pool mounted
  read-only lists its volumes but cannot create or delete them.
- **Balance** - Redistribute data across devices
- **Remove pool** (the trash button) - After **Continue**, type the pool's
  name to enable **Remove Pool**. UNOS unmounts the pool, removes it from
  `/etc/fstab` and deletes its empty mountpoint. The disks are not wiped: the
  data stays on them, and the pool is listed again as **Not mounted**, ready
  to be mounted later. Like Unmount, it is refused while the pool is in use,
  and the dialog says why. As after an unmount, its SMB shares are no longer
  served; NFS exports of the pool are not checked: remove them first.

### SMART Monitoring

**System** → **Disk Health** shows the model, size, temperature and SMART
health status of each disk that the **Storage** page lists, the system disk
included unless it is an SD card or eMMC. Opening the tab, or its refresh
button, reads the SMART data of every disk, which wakes disks that sleep.

**SMART data** opens a disk: its overall health, temperature, power-on hours
and power cycles, serial number, firmware, capacity, the last self-test and
its SMART attributes, each with its status: **OK**, **Warning** (within 10 of
the attribute's failure threshold) or **Failing** (at or below it). NVMe disks
have no SMART attributes; the dialog lists their SMART/Health Information log
instead (available spare, percentage used, media errors and the rest). The
dialog shows the values the tab loaded.

#### Replace Soon

Every hour UNOS records, for each disk that is awake, the SMART counters of
failing sectors and errors, and keeps the last 720 records of each disk (30
days for a disk that never sleeps). The records are kept in memory and saved
to the system disk only when a disk's counters, path or serial number
change, when a disk is recorded for the first time, and when UNOS stops: an
hour in which nothing changed writes nothing. After a power cut, the records
since the last change are lost; the counters themselves are not. From 24
records on (a day), it scores how the counters rise:

- each of the reallocated, current pending and offline uncorrectable sector
  counts that rose within the kept records adds 30 points, and 20 more if it
  rose by 3 or more within the last 24 records;
- each of the reported uncorrectable errors, command timeouts and UDMA CRC
  errors that rose above 100 adds 10 points.

A record in which smartctl could not read the counters counts neither as a
rise nor as a drop.

With 50 points or more the disk is marked **Replace soon**: in **Disk Health**
with the reasons and a recommendation, and on the **Dashboard** (see
[Disks to Replace](#disks-to-replace)). Disk Health marks the disk that
reports the same serial number. When none of its disks does, because smartctl
could not read the disk this time or another disk has taken its place, an
alert above the list names the disk's path, serial number and reasons
instead. The mark goes away by itself once the score is below 50, for example
a day after a single sector count stopped rising.

A disk that is removed or replaced loses its mark, except in two cases, where
the old disk's mark stays until the hourly check has recorded the new disk:

- a disk replaced inside the same USB enclosure, whose bridge reports its own
  serial number for whichever disk it holds;
- a disk behind a USB bridge that reports no serial number, or the same one
  as another enclosure: UNOS finds such a disk by its path (such as
  `/dev/sdb`), which another disk can take.

A disk that sleeps is not recorded and keeps its last score.

The score follows rises, not levels: a disk that arrives with many
reallocated sectors gets no mark, so check the SMART data of a used disk
yourself. NVMe disks get no score, as the hourly records hold no NVMe
counters: **SMART data** shows their health log, and their health turns
**FAILED** when the disk reports a critical warning.

A counter that rose since the disk's previous record sends an alert to every
enabled e-mail and Telegram channel (see [Notifications](#notifications)):
"Disk Health Warning: <path>" with the counter, its old and new value and a
recommendation; a warning, or an error when a sector count rose by more than
10 (other counters: by more than 100). The same rise is not alerted again an
hour later, and a count that falls and rises again is alerted again. Disks
without a serial number are told apart by their path.
`GET /api/v1/health/smart/trends` lists every recorded disk, removed disks
included, with its number of records and the counters that rose over its
last 24 records.

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
- `apps` and everything below it, `.snapshots`, and the trash (`.unos-trash`,
  see [Trash](#trash));
- folders outside the pools that UNOS manages;
- a folder inside another share's folder, or one that contains another
  share's folder: shares do not overlap.

A share marked **Not served** breaks one of these rules, for example a share
of a whole pool imported from the host's existing smb.conf. Nobody can open it
until you give it a new folder with **Edit**; it cannot be given access until
then. The shares of a pool that was unmounted or removed on the **Storage**
page are marked **Not served** and **Pool not mounted** in the same way: they
keep their access, and mounting the pool serves them again.

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
its files there. Apps get a share's folder only when they run in the group
`unos-smb` (see [Share Folders for Apps](#share-folders-for-apps)).

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
files created over SMB get that group and the mode 660. In such a folder,
folders made with **New Folder** in the File Browser get the mode 2770 and
uploaded files 660 as well (see [File Browser](#file-browser)). Files that
came in another way (from an app, copied or moved in the File Browser, or
already in the folder when the share was created) keep their permissions
until you press **Repair permissions** on the share. A share marked
**Permissions need repair** needs it. Repairing:
- gives every file and folder of the share the group `unos-smb` and group
  read and write access (folders 2770, files 660, or 770 for files their
  owner may run);
- **removes the access that the group and other permissions gave every
  other account**, including apps that run as their own user (for example
  PUID 1000): they can no longer read the folder, unless they run in the
  group `unos-smb` (see [Share Folders for Apps](#share-folders-for-apps));
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

#### Finding the NAS

Computers on your local network find the NAS without its address:

- **Windows** lists it in File Explorer, under **Network**, on a network that
  Windows treats as private (network discovery on). Opening it there asks for
  your UNOS username and password and lists your shares.
- **macOS** lists it in the Finder sidebar, under **Locations**. Click it,
  then **Connect As** and sign in with your UNOS username and password.

The NAS appears under its server name, and is announced with its IPv4
address, which both connect to. NetBIOS, the old way of finding Windows
servers, is off: computers that know only NetBIOS (some older TVs and media
players) do not list the NAS; connect them by address as below. Computers
that speak only SMB1, such as Windows XP, cannot use the NAS at all: it
speaks SMB2 and later. On a NAS installed on Debian 11, Windows does not list
it either ([Network Discovery](INSTALL.md#network-discovery)).

**Show in network browsing** of a share is another matter: it decides whether
the share appears in the NAS's list of shares.

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
- **Delete** - Select the items and click **Delete**. In the dialog, **Move
  to trash** moves them into the trash of their pool or volume, from where
  you can restore them (see [Trash](#trash)); it has the keyboard focus, so
  Enter does it, and Escape keeps the items. Folders go with everything in
  them. If the items cannot go to the trash, the dialog stays open with the
  reason, about the items that are still in the folder: when some went to
  the trash before the refusal, the message says how many. When the trash
  refuses them (for example, they have no trash, or their pool has no space
  left for them), the dialog then also offers **Delete for good**, which
  deletes them at once and frees their space; that cannot be undone, so
  Cancel has the keyboard focus.
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

In a folder with the permissions of share folders, the group `unos-smb` and
the mode 2770 (a share's folder that UNOS created or repaired, and the
folders in it that SMB users, **Repair permissions** or the File Browser
made; see [Repair Permissions](#repair-permissions)), **New Folder** gives the
new folder the mode 2770 and **Upload** gives the file the mode 660, also a
file that replaces one, both in the group `unos-smb`: the users with **Read &
write** access to the share can change them, and add files to the new
folder, over SMB at once. Elsewhere new folders get the mode 755 (2755 in a
folder with the setgid bit) and uploaded files 644, which let other accounts
read them but not change them. **Copy** and **Move** keep the permissions of
the items, also into a share: run **Repair permissions** on the share
afterwards.

Uploads, new folders, copies and moves go into the folders of a pool, not
into the pools directory itself. The folder of a pool, its volumes, and other
subvolumes or filesystems mounted in a pool cannot be deleted, renamed or
moved here: delete volumes on the **Storage** page (see
[Pool Operations](#pool-operations)). Renaming a folder that a share, an NFS
export or an app uses is not refused, but they keep using the old name, which
no longer exists: rename the folder back to restore them.

A folder that contains a subvolume (such as a snapshot) or a mounted
filesystem cannot be deleted here, or moved to another pool or volume.

### Trash

**Trash** at the top right of the File Browser opens the trash: the items
deleted in the File Browser, in every pool and volume, newest first, with the
folder each was deleted from, when it was deleted, the day it is kept until,
and the size of files.

- **Restore** puts the selected items back in the folder they were deleted
  from, with their names, contents and permissions. Nothing is replaced: if an
  item with the same name is there again, or the folder is gone (or is now on
  another filesystem, such as a disk mounted there), nothing is restored and
  the message says why. Restore the folder first, or create it again with
  **New Folder**, then restore the item.
- Deleted items stay in the trash for 30 days. When UNOS starts, and then once
  a day, it deletes for good the items that were deleted 30 or more days ago.
- Until then they still take up space in their pool. **Delete for good**
  deletes the selected items at once, and **Empty trash** everything in the
  trash of every pool and volume. Both ask first, and neither can be undone.
- Each pool and each volume keeps its trash in a hidden folder,
  `.unos-trash`, at its top (for example `/mnt/pools/data/.unos-trash` and
  `/mnt/pools/data/photos/.unos-trash`), where only root can open it. The File
  Browser does not show it, SMB clients do not see it, and it cannot be
  shared; apps and NFS clients that use a volume can see it. If an app or an
  NFS client made a folder of that name first, UNOS makes it root's, with
  mode 0700, before it uses it, and ignores what they put in it. A copy of a
  pool or a volume leaves its trash out; a snapshot keeps it.
- Items inside a snapshot, inside the restored copy of a volume's snapshot, or
  in another filesystem mounted in a folder of a pool or volume have no trash,
  and neither have items on a filesystem that cannot keep the trash private
  (vfat, for example): the Delete dialog says so, and they can only be
  deleted for good.
- Screen readers read each item's checkbox with the folder it was deleted
  from and the time, so that items of the same name can be told apart.

---

## Docker Containers

UNOS can manage Docker containers for running additional applications. The
**Containers** page has two tabs, **Containers** and **Images**. UNOS's own
containers (`unos-supervisor`, `unos-webui`) and images are not listed.

### Viewing Containers

The **Containers** tab lists every container with its name, image, state
(running, exited, paused and so on) and the ports it publishes. The **Running**
and **Stopped** cards and the state menu show only the containers in that
state, and the search box finds containers by name or image.

### Container Actions

- **Start**, **Stop** and **Restart** act on the container, and the list shows
  its new state. An app from the App Store does not start while its data folders
  are not on a mounted pool: the message names the pool to mount.
- **Remove** deletes the container, also a running one, after a confirmation.
- Click a container for its details: **Overview**, **Logs** and **Network**.
  **Logs** shows the last 200 lines of its log and then each new line as the
  container writes it, each line with the time Docker received it. When the log
  ends, for example because the container stopped, or the connection is lost,
  the tab says so; **Reconnect** starts again with the last lines.

### Images

The **Images** tab lists the Docker images with their tags, ID, size and
creation time.

- **Pull**: enter an image name such as `nginx:latest` (without a tag, Docker
  pulls `latest`) and click **Pull**. A large image can take several minutes;
  the image is listed once Docker has it.
- **Remove**: click the bin of an image and confirm. The image goes with all
  its tags, and only if no container uses it, running or stopped: otherwise the
  dialog names the containers that use it, and the image keeps its tags. Remove
  those containers first. When Docker refuses for another reason, the dialog
  shows it.

### Creating Containers

1. Click **Create Container**
2. Enter the container name and the image name (e.g., `nginx:latest`); Docker
   pulls the image if it is missing
3. Choose the restart policy and add port mappings and environment variables
4. Click **Create Container**

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
5. To give the app a share's folder, tick **Run in the group unos-smb** and
   enter the share's folder on the **Volumes** tab (see
   [Share Folders for Apps](#share-folders-for-apps))
6. Wait for installation to complete

Starting or restarting an app from UNOS fails while its data folders are not
on a mounted pool.

### Share Folders for Apps

An app can use a share's folder only if it runs in the group `unos-smb`. The
apps of linuxserver.io images offer **Run in the group unos-smb** when they
are installed, unless all their folders hold their own data (such as
Prowlarr's `/config`): it sets the app's `PUID` to 80000, a user that no other
app has, its `PGID` to 70000 (`unos-smb`) and its `UMASK` to 007, so what the
app creates gets the same permissions as what SMB users create (folders 2770,
files 660). Such an app reads and changes every file in the share folders you
give it, and SMB users with write access to the share can change what the app
writes there. If the app can be reached from the internet, whoever takes it
over gets those files too. Apps of other images, such as Jellyfin, Plex and
File Browser, cannot run in the group: UNOS sets the user and the group
through `PUID` and `PGID`, which those images do not take. `PUID` and `PGID`
cannot be changed on the **Environment** tab.

An install is refused when it would give an app:

- a share's folder, a folder inside one or a folder that holds one, while the
  app does not run in the group;
- a folder inside a share's folder, also in the group: give the app the
  share's whole folder;
- a share's folder for the app's own data, the folders under `apps/` such as
  `/config`;
- a writable folder that holds a share's folder, also in the group. A
  read-only one may hold shares when the app runs in the group, as Duplicati's
  view of the whole pool does;
- a snapshot folder (`.snapshots`) or the trash (`.unos-trash`), or a folder
  in one, also in the group: they keep the files of shares as they were, and
  what was deleted from them.

A linuxserver.io app may give the share's folder to its user (80000) when it
starts; the folder keeps the group `unos-smb` and its permissions, and
**Repair permissions** does not change its owner.

A folder that an app already uses (downloads, media) can still be shared on
the **Shares** page. After **Repair permissions**, an app that runs as its own
user outside the group (PUID and PGID 1000) can no longer read it: uninstall
the app (its data is kept) and install it again in the group.

### Managing Installed Apps

Go to **App Store** → **Installed** to see the installed apps with their state,
the pool that holds their data and, for the apps that run in the group
`unos-smb`, **Runs in the group unos-smb**, and to act on them:

- **Start**, **Stop** and **Restart** an app; its card shows the new state. A
  start or restart is refused while the app's data folders are not on a mounted
  pool, and the message names the pool to mount.
- **Uninstall** removes the app's container after a confirmation and keeps its
  data folders. Installing the app again on the same pool uses them again.

These actions work only on apps installed from the App Store; other containers
are started and stopped on the **Containers** page.

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

SMB passwords need at least 8 characters, and they must not be common
passwords: UNOS refuses about 1450 passwords that attackers try first, such as
`Password123`, `qwertyuiop` or `Summer2024!`, in any letter case, with "SMB
passwords must not be common passwords: this one is on the list of passwords
that attackers try first". This holds when SMB is turned on with a password
(by the user or in the **Users** page, also when a user is created with SMB)
and when a user with SMB on changes the password; users without SMB may
keep a shorter or common UNOS password (the web UI asks only for 8
characters, and scripts that use the API can set shorter ones). A user whose
UNOS password breaks these rules first changes it under **Change Password**,
then turns SMB on. A two-factor code works only once:
the code the user signed in with cannot turn SMB on as well, so wait until
the authenticator app shows the next one.

SMB cannot be turned on while Samba still uses an smb.conf that UNOS did not
write (HTTP 409): on a host that had an smb.conf before, UNOS imports its
shares and writes its own at its first start. When importing the shares of
the old file or writing the new one failed, the status panel of the
**Shares** page says why: "Shares were not imported from smb.conf: ..."
(UNOS writes its smb.conf only after that import) or "smb.conf was not
updated: ...". When smb.conf was replaced or removed after UNOS wrote it, the
status panel says "smb.conf was replaced or removed after UNOS wrote it; ..."
until UNOS writes its own again at the next change or restart; an edit that
keeps the two comment lines at the top of UNOS's file is not noticed.
Allowing SMB and turning it off are not affected.

Turning **Allow SMB access** off (or the user's own **Turn off SMB**) ends the
user's open SMB connections. When Samba could not apply a user's own change
at once, the **Account** page shows a short warning without details; the
details are in the supervisor's log.

After a backup is restored, a user whose password changed since the backup
was made shows **Password needed** and must turn SMB on again.

### Changing Passwords

Users can change their own password:
1. Go to **Account** (administrators: **System** → **Security**)
2. Under **Change Password**, enter the **Current Password**, the **New
   Password** (at least 8 characters) and the new one again under **Confirm
   New Password**; all three are required
3. Click **Change Password**: the page confirms "Password changed
   successfully"

From then on the old password no longer signs in, and the new one does.
Sessions that were open before stay signed in until they end (see
[First Login](#first-login)).

Admins can reset any user's password via **Users** page.

When SMB is on for the user, the SMB password changes too, and the user's open
SMB connections are ended: the computers sign in again with the new password.
The new password must then follow the SMB rules (at least 8 characters, not a
common password; see [SMB Access](#smb-access)). Update the saved password on
every computer and app that connects over SMB (mapped drives, media players,
sync apps, `cifs` credential files). Samba remembers each account's two
previous passwords: a device that keeps trying one of them is refused without
a write to the system disk, but every other wrong password of an SMB account
is written to the system disk, and SMB has no limit on sign-in attempts. If
open SMB connections could not be ended, **Change Password** shows a warning
that stays until you dismiss it.

### Two-Factor Authentication (2FA)

Protect your account with TOTP-based two-factor authentication:

1. Go to **Account** (administrators: **System** → **Security**)
2. Under **Two-Factor Authentication**, click **Enable**
3. Scan the QR code with an authenticator app (Google Authenticator, Authy,
   etc.). The QR code is drawn in your browser; the secret is never sent to
   another service.
4. Enter the verification code and click **Enable 2FA**: the section shows
   **Enabled**

When the page cannot load whether 2FA is on, the section says so with the
reason and offers **Retry** instead of **Enable** or **Disable**.

To sign in with 2FA, enter your username and password as before: UNOS then
asks for the **Authentication Code**. Enter the 6-digit code that the app
shows and click **Verify**; **← Back to login** returns to the password form.

To disable 2FA:
1. Go to **Account** (administrators: **System** → **Security**)
2. Under **Two-Factor Authentication**, click **Disable**
3. Enter a current 2FA code and click **Disable 2FA**

Each code works once: a code that was accepted (to sign in, to turn SMB on,
or to turn 2FA on or off) is refused when it is used again, with "This
two-factor code was already used; wait for the next one". The app shows a new
code every 30 seconds; at the sign-in the code form stays, so you enter the
next code without your password again.

2FA protects only the web interface, not SMB: SMB signs in with the password
alone. Turning SMB on yourself needs your two-factor code as well.

#### Recovery Codes

A recovery code signs you in when you have lost your authenticator app.
Generate them as soon as 2FA is on:

1. Go to **Account** (administrators: **System** → **Security**)
2. Under **Two-Factor Authentication** → **Recovery codes**, click
   **Generate recovery codes**
3. Save the ten codes (they look like `5251-6486-2F69`) somewhere safe, away
   from the devices you sign in with, and click **Done**. They are shown only
   this once: UNOS keeps a hash of each code, not the code.

**Recovery codes** then says how many codes are left. **Generate new codes**
replaces all of them after a confirmation: the codes you had stop working.
When generating fails with a server or network error, the page cannot tell
whether UNOS replaced your codes, and says that the codes you saved may no
longer work: generate new codes again and save those.

To sign in with one, enter your username and password, click **Use a
recovery code** at the code step and enter one of your codes; letter case,
dashes and spaces do not matter. Each code works once: a used code is refused
with "Invalid recovery code", as a wrong one is. A refused code appears in the
login history as a failed sign-in, and the audit log records each sign-in with
a recovery code.

Turning 2FA off deletes the recovery codes, and after turning it on again you
generate new ones. Turning 2FA off needs a code from the authenticator app:
with recovery codes alone you can sign in and generate new codes, but not turn
2FA off, and an administrator cannot reset it for you.

Scripts send the code as `recovery_code` in place of `totp_code` to
`POST /api/v1/auth/login`; `POST /api/v1/auth/recovery-codes/generate`
answers new codes, and `GET /api/v1/auth/recovery-codes/status` how many are
left.

### Login History

Under **Login History** on the **Account** page (administrators: **System** →
**Security**), **View History** lists the latest 50 sign-ins, newest first:
those of your own account since it was created (an account created with the
name of a deleted one does not see that account's), and for administrators
those of every account, also of deleted accounts and the attempts with names
that do not exist. Each entry shows **SUCCESS** or **FAILED** with the reason
(such as "Incorrect username or password" or "Invalid 2FA code"), the
account, the time in your browser's time zone, the address of the computer
that signed in (not that of the web interface's proxy) and the browser. Above
the list are the number of attempts and how many succeeded and failed;
**Refresh** loads the list again. A sign-in with 2FA is listed once, when its
code was checked. When the history cannot be loaded, the section says so with
the reason, and **Retry** loads it again.

### API Tokens

Tokens give scripts access to the UNOS API with the rights of the account that
created them: a regular user's token cannot use the administrator-only APIs.
There is no page for them yet; create one with the API, using the
`access_token` that `POST /api/v1/auth/login` returns. `name` has 1 to 64
characters. `expires_in_days` is the token's lifetime, from 1 to 3650 days;
without it the token does not expire. A token acts with all the rights of its
user, and `permissions` takes only `*`. A user has at most 100 tokens. The
answer is the only place that shows the full token (`token`, which starts with
`unos_`): UNOS keeps only a hash of it, so save it right away.

```bash
curl --cacert unos-root-ca.crt -X POST https://unos/api/v1/auth/tokens \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "Backup Script", "expires_in_days": 365}'
```

Use tokens in API requests:
```bash
curl --cacert unos-root-ca.crt -H "Authorization: Bearer YOUR_TOKEN" https://unos/api/v1/system/info
```

`unos-root-ca.crt` is the UNOS root certificate (see
[Trust the UNOS Certificate Authority](INSTALL.md#trust-the-unos-certificate-authority)),
and `unos` the name on the certificate.

- `GET /api/v1/auth/tokens` lists your tokens with their first 12 characters
  (`token_prefix`), whether they are active, their expiry and their last use,
  never the full token. Other users, administrators too, neither see nor
  change your tokens.
- `POST /api/v1/auth/tokens/<id>/toggle` disables a token, and enables it
  again the next time. A disabled token is refused with 401 "API token is
  disabled", an expired one with 401 "API token has expired".
- `DELETE /api/v1/auth/tokens/<id>` revokes a token for good.
- Creating, disabling, enabling and deleting tokens needs the `access_token` of
  a sign-in: a request made with an API token gets 403, so that a token cannot
  make tokens that outlive its expiry or its revocation. A token can list
  them.
- The last use (`last_used_at`) is stored at most once an hour, so that
  scripts do not write to the system disk with every request: it can be up to
  an hour older than the token's latest request.

---

## Audit Log

**Audit Log** in the sidebar shows administrators what happened on the NAS;
regular users have no access to it.

- The entries are listed newest first, on a timeline grouped by day. Each one
  shows its time, its action, **Failed** when it did not succeed, **Warning**
  or **Critical** for its severity, its details, the user, the address it came
  from, the resource it concerns and the browser.
- **Search** finds a text, in any case, in the user name, the action as the
  page names it (`login failed`), the address, the resource as the page shows
  it (`user carol`), the details or the browser. **User** takes an exact user
  name, **Action** one kind of entry, and **From** and **To** a range of days,
  each day included whole. The list follows the filters as you change them;
  **Clear filters** resets them.
- The page shows the newest 100 matching entries; **Show more** adds 100 more.
- **Export CSV** and **Export JSON** download every entry that matches the
  filters, newest first. In the CSV file, a `'` stands before what a
  spreadsheet would read as a formula: a value that starts with `=`, `+`,
  `-`, `@`, a tab or a carriage return, and such a character after a
  semicolon, a tab or a line break inside a value. The JSON file holds the
  values as recorded.
- Times are the NAS's clock as the entries record it, without a time zone
  (UTC unless the supervisor's container is set otherwise), and the days of
  **From** and **To** are the NAS's days (feature-list entry
  `architecture.times-with-time-zones`).
- Recorded today: sign-ins and failed sign-ins (a wrong two-factor code
  appears only in the login history), users created, changed and deleted, the
  SMB access of users, shares created, changed, deleted and repaired, shares
  imported from smb.conf and the corrections of the SMB check at startup (by
  the user `system`), API tokens created and deleted, new recovery codes, and
  the clearing of the log. Storage, snapshots, containers, apps, backups,
  restarts and shutdowns, scheduled tasks, the network and system settings
  write no entries yet, and neither do a user's own password and two-factor
  changes, although **Action** lists such actions (feature-list entry
  `security.audit-admin-actions`).

The same filters work in the API, for administrators: `GET /api/v1/audit/logs`
(with `limit` from 1 and `offset` from 0), `GET /api/v1/audit/logs/count` and
`GET /api/v1/audit/logs/export` (`format=csv` or `json`) take `search`,
`username`, `action`, `severity`, `start_date` and `end_date`. A bound covers
the whole unit it names, as the first and as the last one: a date
(`YYYY-MM-DD`) its day, and a date and time (`YYYY-MM-DDTHH`, `THH:MM`,
`THH:MM:SS`, or `THH:MM:SS` and a fraction of 1 to 6 digits) its hour,
minute, second or fraction, so `end_date=2026-11-06T12:00` takes the entries
up to 12:00:59.999999. Bounds are in the NAS's local time, without a time
zone; another form gets 422.

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

The tab shows the host's network as the host itself sees it:

- **Interfaces**: each network interface with its IPv4 and IPv6 addresses, its
  state (UP; DOWN when it has no link; Disabled) and, while it has a link, its
  speed. Click an interface for its MAC address, MTU, type, speed and all
  its addresses. VLANs are listed by their kernel names, such as `eth0.100`.
  The loopback and Docker's own interfaces are not listed.
- **Default Gateway**, **DNS Servers** and **DNS Configuration**: the gateway,
  the DNS servers and the search domains the host uses.
- **Routes**: the host's IPv4 and IPv6 routing tables.
- **Connections**: the first 50 TCP and UDP sockets of the host.

What could not be read is named above the lists, with the host's message.
The traffic figures next to an interface come from the UNOS supervisor's
container and show only for an interface whose name matches one of the
container's (feature-list entry `system.dashboard`).

#### Disabling and enabling an interface

1. Click the interface, then **Disable**. A dialog names the interface and
   warns that your connection to UNOS may drop if it goes through this
   interface. Confirm with the dialog's button, for example **Disable eth1**,
   or click **Cancel**.
2. The interface goes off, and the tab counts down 60 seconds. Click
   **Keep eth1 disabled** (with the interface's name) before the time runs
   out to keep the change. With the keyboard: the countdowns have the focus
   when the dialog closes, the newest first, and Tab reaches its button.
3. If you do not, the interface comes back on by itself after 60 seconds,
   with the addresses and routes it had, the default route included. This
   also happens when the connection dropped: wait a minute, then reload the
   page. A few seconds after the time runs out, the tab says whether the
   interface came back on. When it did not, because the host could not turn
   it on or someone kept the change elsewhere, or when its network adapter
   is gone, the tab says so: click **Enable** if it should be on.

**Enable** turns a disabled interface back on with the addresses and routes
it had when UNOS disabled it; an Enable that fails during the countdown
leaves the automatic revert in place. Once UNOS finds such an interface
turned back on outside UNOS, it forgets those addresses and routes, so a
later Enable does not add them to a configuration that has changed since.
A disabled interface comes back on when the host restarts, as the host's
network configuration says. The host must run systemd: UNOS refuses to
disable an interface when it cannot set up the automatic revert.

UNOS does not disable an interface that carries VLANs, such as `eth0` with
`eth0.100`: the VLANs would go down with it and come back without their
routes. Disable the VLANs themselves, or the interface on the host.

The tab does not change addresses, DNS or routes: configure them on the host
or in your router.

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
`wakeonlan <MAC address>`. A magic packet starts a host that is off only where
the board keeps the network card powered then; many single-board computers
cut its power, and then only the power button starts the host.

There are no scheduled shutdown or wake times, and the UPS state has no page
yet (feature-list entries `system.scheduled-power` and
`system.ups-monitoring`).

### Disk Health

- SMART health status and temperature of each disk, its SMART data under
  **SMART data**, and the **Replace soon** mark (see
  [SMART Monitoring](#smart-monitoring))

### Logs

**System Logs** lists the entries of the host's systemd journal, the newest
first. The buttons above the filters choose what it lists:

- **System Journal**: everything in the journal
- **Kernel**: the kernel's messages since the host started
- **Docker**: the Docker daemon
- **Samba**: the SMB file server, smbd, and nmbd where NetBIOS is on (UNOS
  turns NetBIOS off, so nmbd's entries are from before)
- **Authentication**: sign-ins, sudo and the other messages of the syslog
  facilities auth and authpriv, which hosts with rsyslog also write to
  `/var/log/auth.log`
- **Cron Jobs**: the host's cron daemon

Each entry shows its time in your browser's time zone, its level, the program
that logged it and its message. The filters narrow the list:

- **Priority Level**: the entries at that level or a more severe one; for
  example, **Warning** lists warnings, errors and worse. An entry's level is
  the one its program gave it: sshd logs a failed password as Info.
- **Time Range**: the entries of the last 5 minutes, 15 minutes, hour,
  6 hours, 24 hours or 7 days, or of all time.
- **Limit**: at most 50, 100, 200 (at first), 500 or 1000 entries.
- **Search**: type a text and press Enter or click **Search**: the tab lists
  the newest entries whose message contains it, in the chosen source, level
  and time range, also when they are older than the newest entries, and
  says **No logs found** when none does. The case of the letters A to Z does
  not matter. The matches are highlighted in the messages, also while you
  type; log messages are always shown as text.

**Auto** reloads the list every 5 seconds. Auto and **Search** wait while an
answer is still coming. When the journal cannot be read, the tab shows why
instead of the list. A search through a large journal can take long: after
30 seconds UNOS stops journalctl on the host and the tab reports that it did
not answer; a shorter time range helps.

**Failed SMB sign-ins**: Samba logs each failed SMB sign-in as a line of the
level Notice, which the source **Samba** lists. Search for `Auth:` to list
only these lines.
Each one names the account the computer tried (`user [WORKGROUP]\[alice]`),
the reason (`status [NT_STATUS_WRONG_PASSWORD]` for a wrong password,
`[NT_STATUS_NO_SUCH_USER]` for a name without SMB) and the computer
(`remote host [ipv4:192.168.1.50:50122]`, its address and port). To see what
one computer keeps trying, search for its part, such as
`remote host [ipv4:192.168.1.50:`. Successful sign-ins are not logged.

The output of the UNOS containers themselves is not in the journal: run
`docker logs unos-supervisor` or `docker logs unos-webui` on the host. Log
files that the journal does not hold, such as `/var/log/dpkg.log`, are not
shown.

### Scheduler

**System** → **Scheduler** runs tasks on a schedule: snapshots and scrubs of a
pool, SMART tests of a disk, system backups (see
[Scheduled Snapshots](#scheduled-snapshots) and [System Backup](#system-backup))
and custom commands. Only administrators see the tab, and only they can use
its API.

- **New Task** asks for the task's type, its target and when it runs: every
  hour at a minute, every day, week or month at a time, or a cron expression
  (minute, hour, day of the month, month, day of the week). **Run now** runs a
  task at once. **History**, or a task's history button, lists the last 100
  runs with their status, start and duration, and the output or error of each.
- The schedules follow the clock of the NAS's time zone, which the tab names:
  the time zone set on the host (`timedatectl set-timezone`), or UTC when the
  host has none that UNOS can use; when the host cannot be asked for a moment,
  they keep the zone it named last. A task for 03:00 runs at 03:00 on that
  clock, also after a change of daylight saving time, and a change of the time
  zone applies within a minute. On the night the clocks go back, a time in the
  hour that repeats runs once, the first time, and an hourly task skips the
  repeated hour; on the night they go forward, a time that the clock skips
  runs when the clock passes it, once (a task for 02:30 runs at 03:00 in
  central Europe). Next runs and the times in the history show in your
  browser's time zone.
- A task runs while UNOS runs: a time that passes while the NAS is off or
  suspended is not made up for, and when the clock is set forward, only the
  tasks of its last 5 minutes run. A run that a restart or shutdown of UNOS
  cuts off (an update, a reboot) fails with "The supervisor stopped during
  the run"; the task runs again at its next time.

#### Custom Commands

A task of the type **Custom Command** runs a shell command with `/bin/sh`, as
root in the supervisor's container. The command sees the host's processes,
the Docker socket, `/etc/samba` and the pools under `/mnt/pools`, but the
container's own files and network: keep scripts on a pool, and reach the host
itself with `nsenter --target 1 --mount --uts --ipc --net --pid -- <command>`.

Each run records, in the history:

- the command's exit status, as **Exit status**: 0 is a success, any other
  value a failure ("The command exited with status 3");
- its output, under **View output**: standard output and error together, in
  the order they were written. A run keeps the last 16 KiB, after a line that
  says so when the output was longer.

A command still running after 24 hours is stopped, with the processes it
started, and its run fails with "The command did not end within 24 hours and
was stopped". Processes that a command leaves running in the background end
when it returns, also those of `timeout` or of job control (`set -m`); only a
process started in a session of its own (`setsid`) is not stopped. Run a
server in a container or as a service of the host. A run starts at its time
also while the task's run before has not ended: a command that must not run
twice at once can guard itself, for example with
`flock /tmp/my-task.lock my-script.sh`. The command is checked again before
each run, as when it is saved: at most 4096 characters, without NUL
characters.

---

## Backups & Snapshots

### Snapshots

A snapshot is a read-only copy of a pool or a volume at one point in time,
kept in its `.snapshots` folder (`/mnt/pools/<pool>/.snapshots`,
`/mnt/pools/<pool>/<volume>/.snapshots`). btrfs makes it at once and then
stores only what changes. A snapshot is kept on the same disks as the files
it copies. A snapshot of a pool does not include the files in the pool's
volumes: take snapshots of each volume as well.

The date and time in the names that UNOS gives, `snap_<date>_<time>`,
`restore_<date>_<time>` and `auto_<date>_<time>`, are in UTC. The list shows
when each snapshot was made in your browser's time zone.

#### Creating a Snapshot

1. Go to **Snapshots**
2. In **Pool or volume**, choose a mounted pool or one of its volumes. Its
   snapshots are listed, newest first, with the time each was made in your
   browser's time zone. When the volumes of a pool cannot be read (btrfs
   fails on the pool or does not answer, as after its disk dropped off), the
   pool is offered without them, and a warning says why.
3. Click **Create Snapshot**
4. Enter a name (optional; without one it is `snap_<date>_<time>`, in UTC)
5. Click **Create**: the new snapshot appears in the list.

The list does not show how much space the snapshots take.

#### Restoring from Snapshot

Restoring does not overwrite the current data: it makes a writable copy of
the snapshot, `restore_<date>_<time>` (in UTC), in the folder the snapshot was
taken of (for a snapshot of a pool, `/mnt/pools/<pool>/restore_<date>_<time>`).

1. Go to **Snapshots** and choose the pool or volume
2. Find the snapshot to restore
3. Click its **Restore** button and confirm. A message shows where the copy is.

Copy what you need from it, for example in the **File Browser**. The copy of
a pool snapshot is a volume of the pool: when you no longer need it, delete
it with the pool's **Volumes** on the **Storage** page (see
[Pool Operations](#pool-operations)). The copy of a volume's snapshot is a
subvolume inside the volume: the web interface cannot delete it yet, and the
volume cannot be deleted while it holds the copy.

#### Deleting a Snapshot

Click the snapshot's **Delete** button and confirm; this cannot be undone. A
volume that holds snapshots cannot be deleted: delete its snapshots here
first. When the snapshot was renamed or replaced while it was checked, for
example by an app that writes in the volume, nothing is deleted and the
dialog says so: try again.

#### Scheduled Snapshots

A task of the type **BTRFS Snapshot** in **System** → **Scheduler**
(**New Task**) takes snapshots of a pool on a schedule; volumes are not
offered there. Its snapshots are named `auto_<date>_<time>`, in UTC.
**Keep Count** sets how many of them are kept: after each new snapshot, the
oldest snapshots of the pool whose names start with `auto_` are deleted
beyond that number, ordered by the time btrfs made them. Snapshots with
other names are never deleted. A run on a pool that is not mounted fails
with `Pool <name> is not mounted at /mnt/pools/<name>`, which the task's
history shows.

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

Encrypted backups (`.tar.gz.enc`) are made by backup tasks (below), or
through the API, with `POST /api/v1/backup/create` and the body
`{"encrypt": true, "passphrase": "..."}` (see [API Tokens](#api-tokens)).
Without the passphrase they cannot be restored.

For automatic backups, add a task of the type **System Backup** in
**System** → **Scheduler** (**New Task**). Its backups are always encrypted
(AES-256-GCM) with the passphrase you enter twice, in **Passphrase** and
**Confirm Passphrase**, at least 8 characters long. They are named `auto_backup_<date>_<time>.tar.gz.enc`
(with `_1`, `_2`, ... before `.tar.gz.enc` when two are made in the same
second) and appear in the list, marked encrypted, once they are complete; **Keep
Count** sets how many of them are kept, counted over all backup tasks: after
each run, the oldest scheduled backups beyond that number are deleted, never a
backup made with **Create Backup** or uploaded. Two runs at the same time, as
**Run now** during a scheduled run, both succeed. Besides the database,
they hold a copy of the scheduled tasks, without passphrases, which a
restore does not restore.

- Keep the passphrase somewhere safe: UNOS never shows it again, and
  restoring a scheduled backup asks for it, as for any encrypted backup.
  Without it, the backup cannot be restored. So that the task can run
  unattended, the NAS keeps a key derived from the passphrase (not the
  passphrase itself), readable only by the supervisor. No backup of
  **Backup & Restore** and no scheduled backup holds that key; the copy of
  the data volume that `update.sh` makes before an update does, next to the
  scheduled backups (see [Upgrading](INSTALL.md#upgrading)).
- Editing the task shows the passphrase fields empty. Leave them empty to
  keep the passphrase, or enter a new one: it applies to the backups made
  from then on, and older backups still need the passphrase they were made
  with. The list does not show which task made a backup, so with several
  backup tasks one passphrase for all of them is easier to keep.
- A backup task without a passphrase, such as one whose key was lost, fails
  each run with "No passphrase is set for this backup task: edit the task
  and set one", and stores nothing. Editing it asks for a passphrase, as for
  a new task.

---

## Notifications

UNOS sends two kinds of alerts on its own: pool (RAID) errors and disk health
(SMART) warnings. They go to every enabled e-mail and Telegram channel.

### Adding a Channel

1. Go to **System** → **Notifications**
2. Click **Add Email**, **Add Telegram**, **Add Webhook**, **Add Discord** or
   **Add Slack**
3. Fill in the form: a channel name, and
   - for Email the SMTP host, its port (usually 587; 465 for SMTPS), the
     username and password, the sender and the recipients (separated by
     commas). Leave the username and password empty for a mail server that
     takes mail without a login, such as a relay in your network: UNOS then
     sends without logging in. **Use TLS/STARTTLS** switches the connection
     to TLS on ports other than 465, which uses TLS from the start.
     **Verify the server's certificate** is on: UNOS checks the mail server's
     certificate and host name before it sends the password, and a server
     that fails the check gets no password and no message. Turn it off only
     for a mail server with a self-signed certificate: without the check,
     anyone on the network path can read the password. With
     **Use TLS/STARTTLS** off on another port than 465, the form warns that
     the password and the messages cross the network unencrypted, with no
     certificate to check;
   - for Telegram the bot token and the chat ID;
   - for Webhook the URL, and POST or PUT: UNOS sends JSON with `title`,
     `message`, `type`, `timestamp` and `source`;
   - for Discord and Slack the webhook URL of the channel.
4. Leave **Enable channel** on and click **Add Channel**
5. Click the channel's **Send test**; it works for a disabled channel too,
   so you can check one before it receives notifications. Under the channel
   appears **Test notification sent**, or **Test
   notification not sent** with the reason, for example the mail server's
   certificate error, Telegram's answer, or `HTTP 404: ...` from a webhook.
   A webhook, Discord or Slack channel counts as sent only when the service
   answers with success (2xx); a redirect is a failure, so give the final URL.
   While a channel's test runs, its **Edit** and **Disable** (or **Enable**)
   buttons wait, so that the outcome shown is that of the settings the test
   was sent with.

The list shows a webhook's URL by its host only, since the rest of the URL
is its key. Editing a channel keeps the settings that only the API sets: a
webhook's headers, body template and content type, and the names and icons
of Discord and Slack channels.

### History

**History** lists the latest 100 notifications sent, newest first: the
tests, the alerts and what `POST /api/v1/notifications/send` sends, each with
the channel it went to, its time and **Delivered** or **Failed**; click an
entry for the error. Filter it by the kind of channel and by the result. The
summary above counts the active channels and the notifications sent,
delivered and failed. An entry that UNOS cannot read, for example one
damaged from outside, is left out, and the supervisor's log says so.

Webhook, Discord and Slack channels receive test notifications and what
`POST /api/v1/notifications/send` sends, but no alerts yet (feature-list
entry `system.alert-events`).

### Notification Events

The form's **Notify on events** choices are stored, but they have no effect
yet: every enabled e-mail and Telegram channel receives both kinds of alerts,
and no other event (container status, temperatures, backups) sends a
notification.

---

## Tips & Best Practices

### Security

1. ✅ Create the administrator account right after installing, with a strong password
2. ✅ Enable two-factor authentication (2FA), and keep its recovery codes
   somewhere safe
3. ✅ Use strong, unique passwords, above all for accounts with SMB: SMB has
   no limit on login attempts and no two-factor authentication. UNOS refuses
   common passwords for SMB, but not every password that is easy to guess
4. ✅ After a password change, update the saved password on every computer
   and app that connects over SMB: Samba writes each failed sign-in to the
   system disk, unless the password is one of the account's two previous
   ones. **System** → **Logs**, source **Samba**, shows the computers whose
   sign-ins fail (see [Logs](#logs))
5. ✅ Use a regular account, not an administrator, for your files
6. ✅ Keep guest shares few (guests can only read)
7. ✅ Keep UNOS updated
8. ✅ Don't expose to the internet without VPN
9. ✅ Use API tokens instead of passwords for scripts
10. ✅ Review the [audit log](#audit-log) periodically

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
