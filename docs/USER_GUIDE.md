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

A session lasts 24 hours, or until it is ended: **Logout** ends it on the
server too, a password change ends your other sessions, and an administrator
can end all of a user's sessions with the log-out icon in the user's row on
the **Users** page. When it has ended, the page you have open goes to the
login page once, at its next request (also during an upload or a download) or
when the server ends its live statistics. **Logout** in one browser tab signs
out the other tabs too, and signing in as another account in one tab switches
the other tabs to that account. Restoring a backup ends every session but that
of the administrator who restored it: everyone else signs in again. API tokens
are not sessions: they keep working until they are disabled, expire or are
deleted.

Signing in works also while the disk that holds the UNOS data (`/data`, on the
system disk) is full, so that an administrator can sign in and free space, for
example by deleting old backups. The login history and the audit log then miss
the sign-ins they cannot store: the supervisor's log
(`docker logs unos-supervisor`) reports each record it could not write, with
its time. On a disk that is full to the last block, Docker may not be able to
keep those log lines either. Signing out, changing your own password and
turning two-factor authentication on or off likewise answer as usual when
their audit entry cannot be written once the change is stored, and so do the
changes of pools, volumes, snapshots, containers and apps once they are made,
backups created, uploaded, restored and deleted, reboots and shutdowns
scheduled or cancelled, changes of scheduled tasks and runs started by hand,
and changes of network interfaces, Wake-on-LAN, the host name, the time
zone and the requirement of two-factor authentication for every account; the
supervisor's log reports that entry. An account that can only set up two-factor
authentication cannot set it up while the disk is full: the setup answers an
error until there is room again.
A recovery code does not sign you in while the disk is full, because UNOS
cannot mark it as used; a code of your authenticator app does.

### Interface Overview

The UNOS interface consists of:
- **Sidebar** - Navigation menu on the left, with your account and **Logout**
  at the bottom. Regular users see **Dashboard** and **Account** only (see
  [User Roles](#user-roles)).
- **Main Content** - Current page content

On a phone, or in a window narrower than 768 pixels, a bar at the top replaces
the sidebar: its menu button (☰) opens the navigation, with your account and
**Logout**, over the page. Choosing a page, going back, pressing Escape or
tapping beside the menu closes it. Every page fits a screen 375 pixels wide:
buttons move to the next line where a row has no room, **System**'s tabs are
listed above the open tab, and a wide table, such as the list of disks or of
users, scrolls sideways on its own while the page stays put. A dialog taller
than the screen, as on a phone held sideways, scrolls to its buttons. Narrower
than 640 pixels, the File Browser leaves out the **Modified** column, and each
entry of **System Logs** shows its time, level and program above the message.

A dialog takes the keyboard focus when it opens; when it closes, the focus goes
back to the element that had the focus when the dialog opened, if that element
is still there. Escape closes a dialog. A question asked from a dialog, such as
turning off SMB in a user's edit dialog, opens over that dialog, and Escape
closes only the dialog on top. Until the question closes, the dialog under it
takes neither the focus nor clicks: Tab skips its fields and buttons.

### Language

The web interface is in English or Russian. Choose the language on the **Account**
page (administrators: **System** → **Security**), under **Language**: the list
names each language in its own language. The choice applies at once and is kept
in this browser, for every account that signs in with it, and the login page
shows it too. Without a choice, UNOS is in Russian when the browser's language is
Russian, and in English otherwise. Dates and times follow the language: in the
browser's own format when the browser is set to that language, otherwise with the
day first and a 24-hour clock.

Every page is translated.
Messages from the server, such as the reason an action was refused or the
reason a share is not served, stay in English, and so do the titles and
messages of notifications, the server's records (the log entries, the details
of the audit log, a share's import notes, smartctl's names, and the risk
factors and the advice of a failure prediction) and the values that ip and ss
print in the tables of the **Network** tab, such as a connection's state
(ESTAB) or an interface's link type (ether).

---

## Dashboard

The dashboard provides an at-a-glance view of your NAS. Above it, as above
every page, administrators see the health banner while UNOS knows of a
problem: a pool not mounted, degraded or almost full, a volume near its
quota, a failing disk, a task that failed or missed its run (see
[Alerts](#alerts)).

### System Stats
The cards follow the live statistics, which the server sends every 2 seconds
while the page is open; the time of the last one is shown beside **Refresh**.
Right after the page opens, the network and disk rates can show 0 B/s until
the next statistics arrive.
- **CPU** - Current processor usage
- **Memory** - RAM usage and total
- **Network** - The bytes per second the NAS receives (↓) and sends (↑), over
  the interfaces that **System** → **Network** lists: not the loopback,
  `dummy0` or Docker's bridges and virtual interfaces. Traffic through a
  bridge, bond or VLAN that you set up on the host counts on both the port
  and the bridge, and traffic through a tunnel (WireGuard's `wg0`, a 6in4
  tunnel) on both the tunnel and the port that carries it.
- **Disk I/O** - The bytes per second read from and written to the host's
  physical disks (SATA, NVMe, USB, SD card), without their partitions. RAM
  disks (zram, where Armbian keeps its swap and `/var/log`), loop devices,
  encrypted or LVM volumes (device mapper) and software RAID (md) do not
  count, so a write through LUKS or LVM counts once, on its disk.
- **Temperature** - The temperature of the CPU (Intel, AMD) or of the SoC of
  an ARM board, such as a Raspberry Pi or an RK3588. On a host where UNOS does
  not recognize the CPU's sensor, the first sensor the host lists, which may be
  a disk's; **N/A** on a host without sensors.

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

The reason, such as "Mounted at /srv/backup", is shown below the badge, in
English in every language, like the other messages of the server. Sizes are
written with one decimal in powers of 1024, in the number format of the
interface's language as the browser writes it, such as 59.6 GB.
When the disks cannot be listed, for example because `lsblk` fails on the
host, the card says so with the reason instead of the list; the pools are
still shown, and the other way round.

A disk attached while UNOS runs, such as a USB disk, needs no restart: the
page lists it within a few seconds, also while it is open, and a disk that
is removed leaves the list the same way, together with the pools and the
filesystems found on the disks. While a dialog of the page is open, its
lists stay as they were until it closes, also when the host answers a
reload that began before it opened, and an error message at the top of the
page stays through such a reload. The SMART data of a disk attached
after the supervisor started (**System** → **Disk Health**) can be read
only once the supervisor has restarted.

### Idle Timeouts (Spin-Down)

The **Power** column shows whether each disk is **Active** (spinning, or
idle) or in **Standby** (spun down), or **Unknown** when the disk cannot say,
as behind some USB bridges, or does not answer: after a power read of a disk
that did not answer, UNOS shows it Unknown without asking again until that
read has ended on the host. UNOS asks the disk for its power mode with
`hdparm -C` on the host, which sends it only the CHECK POWER MODE command:
that does not wake a sleeping disk, and it did not start the idle timeout
again on the disk UNOS was measured with (a WD6400AACS). A disk in standby is
not read for what it holds: the **Contents** column then says "Not read while
the disk sleeps" unless the host already knows its filesystem or partitions.
Disks with the Extended Power Conditions (EPC) of many NAS drives answer with
power conditions that `hdparm` does not name; UNOS then asks with
`smartctl -n idle`, which also sends only CHECK POWER MODE and shows them
Active (idle_a, idle_b, idle_c) or Standby (standby_y). On a host without
`hdparm`, and for an NVMe disk, UNOS asks with `smartctl` instead, which also
leaves a sleeping disk asleep but starts an awake one's timer again.

Below the state, the button with the disk's idle timeout (**Off** by
default) opens **Idle Timeout**: choose **Off**, 5, 10, 20 or 30 minutes, or
1 to 5 hours, and **Save**. The disk spins down after that time without
access, or later when it has a minimum of its own: a WD6400AACS given 5
minutes spins down after about 10. UNOS sets it at once with `hdparm -S` on
the host, which spins the disk up if it sleeps, and keeps it in its udev rule
`/etc/udev/rules.d/90-unos-disk-spindown.rules`, which sets it again at every
boot and whenever the disk is connected again, under any name: the rule finds
the disk by its serial number. **Off** stops the timer and removes the disk's
line: until its next power cycle the disk then has no standby timer, not even
one that `/etc/hdparm.conf` or the disk's own default set, and after one it
keeps those. A disk that is woken often wears faster, so give a disk in daily
use a longer timeout.

Many hard disks run automatic offline scans of their own: every few hours the
disk reads its surface, for hours, and it does not sleep meanwhile, whatever
its timeout (a WD6400AACS stayed awake for hours that way). While a disk has a
timeout, UNOS turns its automatic offline scans off (`smartctl -o off`), notes
that in its udev rule, and turns them on again when you set **Off**; a disk
whose scans were off already, or that has none (also one whose SMART is
turned off), is left as it is. The disk is expected to keep the setting when
it restarts, but that is not confirmed for every disk: if a disk no longer
sleeps after a restart, save its timeout again, which turns its scans off
again. When they cannot be turned off, or UNOS cannot tell whether they were,
the timeout is saved all the same, and **Storage** says that the scans may
keep the disk awake. Without them the disk no longer reads its surface by itself: schedule
a scrub of its pool (**System** → **Scheduler**), which reads every used
block of the pool. A long SMART test reads the whole surface, but the
Scheduler's SMART tests are short ones and no page starts a long one yet: run
`sudo smartctl -t long /dev/sdX` on the host.

The button is off, with the reason below it, for the system disk, an NVMe
disk (it manages its own power), a disk that reports no serial number a udev
rule can match or the serial of another disk, and on a host without `hdparm`:
install the package `hdparm` on the host (`deploy/install.sh` does). When the
host cannot read UNOS's udev rule, every disk's button is off with that reason.

Uninstalling UNOS (`deploy/uninstall.sh`) does what **Off** does for each
disk the udev rule names, then removes the rule: the disk has no standby timer
until it restarts, and its automatic offline scans are on again (with the
host's `smartctl`). At its end it names what it could not undo, such as a disk
that is not attached or has SMART off, with the command to run. Until such a
disk's scans are on again, the rule stays with only their records, which do
nothing at boot; a new installation of UNOS reads them, and saving **Off** for
the disk turns its scans on, unless SMART is off on the disk: then only the
command named at the end does. If the uninstallation stops while it undoes the
disks (Ctrl-C or a dropped connection, which take effect once the disk command
then running has ended), the rule is still whole: run the uninstallation again
to finish. That run makes no backup: the first one offered it, then removed
UNOS's data volume.

Nothing UNOS does on its own wakes a sleeping disk or keeps an idle one
awake. Disk Health, the SMART data and the hourly SMART records leave a
sleeping disk asleep, and the hourly records skip a disk with an idle timeout
that was not used since its last record: a SMART read of an awake disk
starts its timer again. The first records after UNOS starts read every awake
disk, and a disk that was asleep at its last record is read once it is awake
again, also when only saving its idle timeout woke it. The pool health check
every 5 minutes, and the pool
counts of `/api/v1/health/prometheus` and `/api/v1/health/full` for
monitoring, read only what the kernel and udev know. The trash of a pool is
cleared out once a day, when its disks are awake. `deploy/install.sh` turns
off smartd, the host's own SMART monitor of the package `smartmontools`: it
would read every awake disk every 30 minutes, so that a disk with a timeout
of 30 minutes or more might never spin down. UNOS's hourly SMART records and
alerts replace it. On a host set up another way, turn it off with
`sudo systemctl disable --now smartmontools` (`systemctl is-active
smartmontools` says whether it runs). What an administrator
starts or schedules wakes the disks it needs: a SMART test, a scrub, a
snapshot or backup task, a file transfer. Opening a page that lists the pools
can wake a sleeping disk, since it reads every disk: the dashboard (the first
page after signing in), **Storage**, **Shares**, **Snapshots** and
**System** → **Scheduler**. **Storage** reads whether each disk sleeps
before it lists the pools, so **Power** shows how each disk was when the page
opened.

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
  pool, also through a Docker volume that binds a folder in it, a symbolic
  link on the host or a folder of the pool mounted elsewhere on the host, or while
  files on it are open, for example by a computer that has one of its SMB
  shares open; the dialog names the containers or the reason. When Docker
  does not answer, so that UNOS cannot tell which containers use the pool,
  unmounting is refused and nothing is changed: try again when Docker is
  running. System services
  that run in a sandbox of their own keep a copy of the pool's mount there:
  UNOS unmounts those copies too. When another process still has the pool
  mounted, for example a container that binds the whole `/mnt/pools` or `/mnt`
  folder, or one that Docker does not list, UNOS mounts the pool again and the
  message names the process and where it has the pool mounted. Once the pool
  is unmounted, its SMB shares are no longer served: their open connections
  are closed, and the **Shares** page marks them **Not served** and **Pool
  not mounted**. They keep their access, and mounting the pool serves them
  again
- **Add Devices** - Expand the pool
- **Scrub** - (mounted pools) Check data integrity. While the scrub runs,
  the pool shows its progress (bytes, percent, rate and duration), which UNOS
  sends the open page as it changes, about every 2 seconds, also for a
  scheduled scrub, and **Cancel Scrub** stops it. At the end it shows the
  result with the time the scrub started, as btrfs writes it (in English, such
  as `Fri Nov  6 10:00:00 2026`) or, when the interface is in another language,
  the same date and time in that language's format: **Last scrub: No errors**, the
  number of errors btrfs counted (read, super, verify and checksum errors),
  or that the scrub was cancelled or interrupted before it finished, with
  the errors found until then; start a scrub again to check the whole pool.
  Interrupted means that the scrub ended neither by finishing nor by a
  cancel, for example because the btrfs process that ran it was killed.
  When UNOS restarts, for example with the host, it forgets the result of
  the last scrub: the pool then shows none, also when the restart stopped a
  scrub. A page left open meanwhile asks again, once its connection to UNOS
  is back, for each scrub it showed running or could not read, and follows
  the ones that still run. When btrfs cannot report the status, the pool
  says so with the reason. Scheduled scrubs are set up in **System** → **Scheduler**; a
  scheduled scrub of a pool that is not mounted fails without scrubbing
  anything. No scrub starts, scheduled or not, while a balance of the pool
  runs. For a pool that does not answer, see below
- **Balance** - (mounted pools that are not read-only) Rewrite the pool's
  chunks, the blocks of space btrfs gives to data and metadata, to free the
  space of partly empty ones. By default only the chunks less full than the
  **Usage limit (%)**, 20, are rewritten, which takes minutes; enter another
  whole number from 1 to 99. **Every chunk (full balance)** rewrites all the
  data of the pool, hours on a hard disk, and starts only after you tick
  **Rewrite every chunk of <pool>**. While the balance runs, the pool shows
  how many chunks it balanced of about how many, and the time left that UNOS
  estimates from the chunks balanced so far, sent to the open page as it
  changes; **Pause Balance** pauses it after the chunk it moves, **Resume
  Balance** goes on, and **Cancel Balance** stops it. While a pause still
  waits for its chunk, **Cancel Balance** waits too (btrfs would pause the
  balance all the same): cancel it once it is paused. A paused balance also
  goes on by itself when the pool is mounted again, for example at the next
  start of the NAS: cancel it to stop it for good. A restart or shutdown of
  the host pauses a balance that runs, which then goes on after the start in
  the same way. A full balance that goes on after a pause, by
  **Resume Balance** or at a mount, rewrites only the chunks less than 90%
  full, a rule of the Linux kernel for a resumed balance without a usage
  limit: the chunks at least 90% full that it had not reached stay as they
  are. To rewrite every chunk, cancel it and start a new full balance; a
  balance with a usage limit keeps its limit when it goes on.
  **Resume Balance** waits for a scrub of the pool to end, and the paused
  balance of a read-only pool can be resumed or cancelled only once
  the pool is mounted read-write again. Afterwards the pool says
  whether the last balance finished (with the chunks it rewrote), was
  cancelled or failed (with btrfs's error, such as no space left); after a
  restart of the host it shows none. The balance runs on the host, so a
  restart of UNOS does not stop it, and the pool shows it again once the page
  is loaded. A balance does not start while a scrub of the pool runs, nor
  while another balance of it runs or is paused
- **Volumes** - (mounted pools) List, create and delete volumes: btrfs
  subvolumes in the pool's top folder (`/mnt/pools/<pool>/<name>`) that can be
  shared on their own. **Quota in GiB** is optional: leave it empty for no
  limit, or enter a size such as `10` or `0.5`. A quota needs quotas enabled
  on the pool, which UNOS does not do itself: run
  `btrfs quota enable /mnt/pools/<pool>` on the host. Without them the volume
  is not created, the dialog says so, and you can create it without a quota.
  When the quota cannot be set, the new volume is removed again. When UNOS
  cannot tell which subvolume is the new one, or btrfs does not answer, it
  keeps it and the message says that it was created without its quota, or
  that it may have none when btrfs does not answer while the quota is set:
  delete it and create it again. When an app or a user renames or replaces
  the new volume right after it is created, UNOS sets no quota and removes
  nothing: the message says that the new volume was not removed, and it
  stays, without its quota, under the name it has now.
  When an app or a user gives something else the volume's name while it is
  created, nothing is created and the message names what is there.
  Snapshots of the pool, manual or scheduled, do not include the files in its
  volumes: take snapshots of each volume as well, on the **Snapshots** page
  (see [Snapshots](#snapshots)); scheduled snapshot tasks take pools only.
  Deleting a volume deletes its files and asks you to type its name.
  Deleting is refused while a running app or container, an SMB share or an
  NFS export uses the volume or a folder in it (a container also through a
  symbolic link or another mount on the host), and while the volume holds
  snapshots or other subvolumes, which must be deleted first. When a folder
  that a running container mounts is on a network or FUSE mount that does
  not answer, the delete stops after 10 seconds and says so. A pool mounted
  read-only lists its volumes but cannot create or delete them.
  Each volume shows its use: with a quota, a bar and "8.0 GB of 10.0 GB used
  (80.0%)", yellow from 80% and red from 95%; without one, the use alone. The
  use is what btrfs counts against the quota, the data the volume shares with
  its snapshots included (a limit on exclusive data, `btrfs qgroup limit -e`,
  is not shown as a quota). With quotas off on the pool nothing is counted,
  and the dialog says how to enable them and what they cost: deleting
  snapshots and balancing get slower, the more so the more snapshots the pool
  has. When btrfs says its counts need a rescan, the dialog shows
  `btrfs quota rescan /mnt/pools/<pool>` to run on the host; until the counts
  are right again, UNOS keeps the alerts as they were and adds no point to
  the graph. Once btrfs has finished deleting a volume, snapshot or restored
  copy, the monitor deletes its quota group at a later pass, once the group
  counts nothing (usually within 10 minutes; after a restart of UNOS, after
  the next write to the pool), so that a later subvolume does not meet its
  limit; when btrfs's counts need a rescan, the group waits for it. The graph button
  of a volume shows its use over time below it: one point per hour for the
  last 30 days, with the quota as a dashed line. UNOS keeps the points in
  memory and writes them to the system disk only when the supervisor stops,
  so an update or a reboot keeps them, while a power cut loses the points
  since the last stop. A renamed volume keeps its points; a deleted one loses
  them. A volume that reaches 80, 90 or 95% of its quota is alerted (see
  [Alerts](#alerts)); a restart keeps the level it reached.
- **Balance** - Redistribute data across devices
- **Remove pool** (the trash button) - After **Continue**, type the pool's
  name to enable **Remove Pool**. UNOS unmounts the pool, removes it from
  `/etc/fstab` and deletes its empty mountpoint. The disks are not wiped: the
  data stays on them, and the pool is listed again as **Not mounted**, ready
  to be mounted later. Like Unmount, it is refused while the pool is in use,
  and the dialog says why. As after an unmount, its SMB shares are no longer
  served; NFS exports of the pool are not checked: remove them first.

UNOS waits 60 seconds for btrfs to start a scrub, report its progress or
cancel it. When btrfs does not answer in that time, for example because a
disk of the pool stopped responding, UNOS stops that btrfs command:

- **Scrub** and **Cancel Scrub** show `btrfs scrub start did not answer within
  60 seconds: pool <name> may not be responding` (`btrfs scrub cancel` for
  Cancel Scrub).
- A scrub in progress stops updating: the pool shows **Could not read the
  scrub status** with `btrfs scrub status did not answer within 60 seconds:
  pool <name> may not be responding` instead of its progress. Until UNOS
  stops btrfs, other requests to UNOS, sign-ins among them, can be slow to
  answer as well.
- Opening the page shows **Loading...** until each mounted pool has reported
  its scrub, one pool after the other: up to 60 seconds for each pool that
  does not answer, which then shows that message.

A btrfs command stuck on a disk can take until a reboot to end. Until then,
asking for the same again is answered at once with the same message. A
**BTRFS Scrub** task in **System** → **Scheduler** fails the same way, and its
history shows why.

### SMART Monitoring

**System** → **Disk Health** shows the model, size, temperature and SMART
health status of each disk that the **Storage** page lists, the system disk
included unless it is an SD card or eMMC. Opening the tab, or its refresh
button, reads the SMART data of every disk that is awake: a disk that sleeps
stays asleep and is listed as **STANDBY**, with the model, serial number and
size that the host knows, and its dialog says that its SMART data is read
once it is awake.

**SMART data** opens a disk: its overall health, temperature, power-on hours
and power cycles, serial number, firmware, capacity, the last self-test and
its SMART attributes, each with its status: **OK**, **Warning** (within 10 of
the attribute's failure threshold) or **Failing** (at or below it). NVMe disks
have no SMART attributes; the dialog lists their SMART/Health Information log
instead (available spare, percentage used, media errors and the rest). The
dialog shows the values the tab loaded.

UNOS waits at most 30 seconds for the SMART data of a disk in Disk Health,
and 60 seconds for a SMART test of a **SMART Test** task in **System** →
**Scheduler**. A disk that does not answer in that time, for example a disk
or a USB bridge that stopped responding, is listed with the health status
**UNKNOWN**, and the task's run fails with the reason; UNOS stops smartctl
and the rest of UNOS keeps working. A smartctl stuck on such a disk can take
until a reboot to end; until then, asking again answers the same at once.

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
enabled channel (see [Notifications](#notifications)):
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

Before UNOS overwrites a file with shares it did not create, it keeps a copy
in `/etc/samba`, named with the time of the copy in UTC:
`smb.conf.unos-replaced-<time>`, for example
`smb.conf.unos-replaced-20261112_101530_123456`, and, for the file whose
shares UNOS imported at its first start, `smb.conf.unos-import-backup-<time>`.
Every such file gets a copy of its own, unless one of the newest 10 copies of
its kind already holds exactly the same text; UNOS keeps the newest 10 copies
of each kind and deletes older ones. Only regular files count as copies: a
folder or a link with such a name is left alone. Until the supervisor
restarts, the status panel of the **Shares** page names the shares and the
copy that holds them ("smb.conf had shares that UNOS did not create (...);
they are kept in smb.conf.unos-replaced-...: create them again on the Shares
page"), followed by the other copies, newest first; the import report names
the import backup in the same way.

#### How Access Works

- **SMB is off for every user until an administrator allows it** on the
  **Users** page (see [SMB Access](#smb-access)). Users then open shares
  with their UNOS username and password.
- **Each share has its own access list:** for each user **No access**,
  **Read only** or **Read & write**. New users have no access to any share.
- **Groups get access too:** a share's access list also gives each
  [group](#groups) **No access**, **Read only** or **Read & write**. Every
  member of the group whose SMB is on gets that access, and so does a user
  added to the group later. A user who has access of their own and through a
  group, or through several groups, gets the higher level: **Read & write**
  wins over **Read only**, and there is no way to give a member less than the
  group has.
- **Administrators cannot use SMB.** Their password controls the whole NAS,
  and SMB has no two-factor authentication: its lock after wrong passwords
  (see [SMB Access](#smb-access)) only slows down guessing. Use a regular
  account for your files.
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
   - **Protocol options** - **Require encryption**, **Time Machine backups of
     Macs** and **Previous Versions from snapshots**, each off by default:
     see [Protocol Options](#protocol-options)
   - **Access** - For each user, and below them for each group (with its
     members under it): No access, Read only or Read & write. **Everyone:
     Read only** and **Everyone: Read & write** set the level of every user,
     not of the groups; **Clear** sets every user and every group to No
     access.
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

#### Protocol Options

Each share can turn on three options; its card shows those that are on, and
marks one that the NAS cannot serve now with the reason (see below).

- **Require encryption** - Only clients that encrypt the connection (SMB 3:
  Windows 8 and later, macOS, Linux) can open the share; older ones, such as
  SMB 2 clients, are refused. Turning it on disconnects the share's open
  connections, which reconnect encrypted. A guest share cannot require
  encryption: guest connections are never encrypted.
- **Time Machine backups of Macs** - A Mac backs up to the share: in **System
  Settings** → **General** → **Time Machine**, **Add Backup Disk** lists it
  (or connect to it in the Finder first). Give whoever backs up **Read &
  write**, ideally a share of its own; Time Machine takes the free space of
  the pool. While any share offers Time Machine, every share loads Apple's
  SMB extensions (Windows' extra file streams are then kept as well), and
  macOS cannot change the permissions of the files. The NAS announces the
  Time Machine shares over Bonjour, those that someone has access to; a
  change of them restarts avahi-daemon for that, so the NAS disappears from
  the Finder sidebar for a moment. If avahi-daemon does not start again, the
  answer and the status panel of the **Shares** page say so, and the next
  change tries again.
- **Previous Versions from snapshots** - Windows lists the snapshots of the
  share's pool, or of its volume when the share is in one, under
  **Properties** → **Previous Versions** of its files and folders, and can
  open, copy or restore them; `smbclient` reaches them as `@GMT-` paths.
  Only the snapshots that UNOS names are listed: those made on the
  [Snapshots](#snapshots) page with the default name (`snap_<time>`) and
  those of scheduled snapshot tasks (`auto_<time>`). Scheduled tasks
  snapshot pools only, and a pool's snapshot does not hold the files of its
  volumes: a share in a volume lists only the volume's snapshots made on the
  **Snapshots** page. When a pool or volume that has a `.snapshots` folder
  lists none (all its snapshots were deleted, or it holds only snapshots
  of other names), `smbclient` of Samba 4.22 prints
  `NT_STATUS_INVALID_NETWORK_RESPONSE getting shadow copy data` (smbd
  answers the empty list in 14 bytes, and that `smbclient` wants at least
  16): it means the list is empty. A pool or volume without a `.snapshots`
  folder, such as a new one before its first snapshot (the first snapshot
  makes the folder, and deleting snapshots keeps it), has no list: there
  `smbclient` prints `NT_STATUS_INVALID_DEVICE_REQUEST getting shadow copy
  data`, as the dev device showed. In each case Windows shows no previous
  versions. A folder
  in a subvolume or file system of its own
  further below the pool's top level than a volume (such as a restored copy
  inside a volume, a subvolume made by hand or another file system mounted
  there) is in no snapshot of UNOS, and the option is refused there with the
  reason; a subvolume right below the pool, such as a restored copy of a
  pool's snapshot, counts as a volume. Turning the option on applies to the
  next connections (open ones get it when they reconnect); turning it off
  disconnects the share's open connections, so that none keeps showing the
  snapshots.

Time Machine and Previous Versions need Samba's vfs modules, the package
`samba-vfs-modules` on the NAS; without it both are refused, with the
package named. If the modules or the snapshots of an option that is on go
away later, the share is served without that option: its card shows the
option as a warning with the reason, and so does the status panel of the
**Shares** page. A share restored from a backup with guest access and
encryption required loses guest access at the restore, with a note.

#### Editing a Share

- **Edit** changes the comment, network browsing, guest access, the protocol
  options and the access list of users and groups (and the folder of a share
  marked **Not served**). Removing or lowering someone's access, also a
  group's (and removing a member from a group, see [Groups](#groups)),
  turning guest access off, requiring encryption and turning Previous
  Versions off disconnect every open connection to that share; the computers
  reconnect by themselves. Adding access disconnects nobody.
- **Repair permissions** fixes the permissions of the files that are already
  in the share (see below).
- **Delete** removes the share; the folder and its files are kept. Open
  connections to the share are closed.
- To delete several shares at once, tick their checkboxes (or **Select all**)
  and click **Delete selected**, for up to 200 shares at a time. The dialog asks once and names each share
  with its folder; **Delete N shares** then deletes all of them in one change
  of smb.conf. Their folders and files are kept, and the open connections to
  them are closed. A share that someone changed or deleted in the meantime is
  not deleted: the page names it with the reason and keeps a changed share
  selected, so you can look at it and delete it again. If Samba's check
  (testparm) rejects the new smb.conf, or the database is busy, no share is
  deleted and the dialog says why.

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
  links, and files with more than one hard link.

It runs in the background until the whole share is done, which can take
hours on a large share: the dialog shows how many entries it has checked and
changed so far, and can be closed meanwhile; the share's card then says
**Repairing permissions**, and the page updates the share when the repair
ends. Open the dialog again to see the result as **Last run**; a renamed
share keeps it, until UNOS restarts. One repair runs at a time: another one
is refused until it ends. **Stop repair** ends it early, and so do deleting
the share, changing its name or folder, a restart of UNOS, or 24 hours of
running; the [Audit Log](#audit-log) says what stopped it. What it changed
stays changed, and a new run continues quickly (entries that are already
right are skipped quickly). A disk that stops answering holds the repair
until it answers again: no stop ends it sooner.

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

### NFS Exports (Linux and macOS)

An NFS export gives the computers of one network of yours a folder of a pool,
or a volume. NFS asks for no password: it trusts each computer for the user
and group ids of its own accounts. UNOS adds its exports to its own export
file on the NAS, `/etc/exports.d/unos.exports`, and the NAS's NFS server
applies them at once and at every start. Exports written by hand into
`/etc/exports` or into another file of `/etc/exports.d` are kept as they are,
and the **NFS** tab lists them too, with their file and line.

UNOS does not install the NFS server. Without it, the **NFS** tab says "The
NFS server is not installed on the host": exports can still be added, and
they apply once the server runs. To install it, run on the NAS:

```bash
sudo apt-get install nfs-kernel-server
```

#### Adding an Export

1. Go to **Shares**, open the **NFS** tab and click **Add NFS export**
2. Configure:
   - **Location** - A pool or a volume, and a **Folder** inside it: required
     for a pool; for a volume, leave it empty to export the whole volume. Only
     pools that UNOS manages are listed, as for SMB shares (see
     [Creating a Share](#creating-a-share)). The folder must exist already
     (create it in the [File Browser](#file-browser)), and its path may only
     contain letters, digits, `.`, `_`, `-` and `/`. The top level of a pool,
     `apps` and everything below it, snapshot folders and the trash cannot be
     exported, and every folder above the exported one must be changeable by
     root only (see [What Can Be Shared](#what-can-be-shared)): otherwise the
     message names the folder and the command to run on the NAS. `exportfs`
     follows symbolic links each time it runs, so if one of those folders
     later becomes changeable by other accounts, the **NFS** tab names it and
     the command too: those accounts could make the NAS export another folder.
   - **Client network** - One address or network of your local network, such
     as `192.168.1.0/24`, `192.168.1.20` or `fd00::/64`: private IPv4
     addresses (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`),
     `100.64.0.0/10`, link-local addresses and IPv6 unique-local addresses
     (`fc00::/7`). Every computer with an address in it can mount the export.
     `*`, host names and public addresses are refused.
   - **Access** - **Read only** (the default) or **Read & write**
3. Click **Create export**

UNOS writes the export with `ro` or `rw` and the options
`sync,no_subtree_check,root_squash,secure,mp=` followed by the pool's folder
(for example `mp=/mnt/pools/data`): the folder is exported only while its
pool is mounted. The pool must be mounted when the export is added. A folder
that UNOS exports already cannot be added again; delete its export first. Nor
can a folder that another export exports, to whatever computers, in another
export file or in a line written by hand into UNOS's own: the NFS server
answers each computer from one export of a folder, so change that line on
the NAS instead. UNOS also refuses to add an export while its own file ends
inside an entry, with a backslash at the end of its last line or a quote left
open.

A change applies every export file (`exportfs -ra`), as the NFS server does
when it starts: an export made with `exportfs` on the command line, which no
file holds, ends. After a change a message names anything that `exportfs`
reported, such as a missing NFS server or a folder of another export file
that does not exist: the change is saved either way.

#### What Clients Can Do

- Every computer of the client network reads and writes as the user and group
  ids of its own accounts: the folder's owner and mode decide what each of
  them can do (`ls -ln` on the NAS shows the ids). Root on a client counts as
  the user `nobody`.
- With **Read only**, nobody can change the folder's files over NFS.
- Exports with other options, such as for a backup computer whose root must
  keep file owners, are written by hand into another export file on the NAS,
  for example `/etc/exports.d/backup.exports`, followed by `sudo exportfs -ra`.

#### Deleting an Export

The trash icon of an export added in UNOS deletes it: UNOS removes its line
from its export file, and the computers lose access to the folder. The folder
and its files are kept. Exports that UNOS did not add have no trash icon:
those of other files, and lines of UNOS's own file that were written there by
hand or changed by hand from the form UNOS writes. Change them on the NAS.

If `exportfs` cannot read a line of an export file, the **NFS** tab names the
file and the line: `exportfs` ignores that line and every line after it in
the same file. Correct or remove the line on the NAS. Until then UNOS adds
and deletes no export: the `exportfs` run that applies a change would stop
exporting what follows the line, also exports written by hand.

#### Mounting an Export

Use the NAS's address on your local network and the folder's full path:

```bash
# Linux
sudo mount -t nfs YOUR_NAS_IP:/mnt/pools/data/media /mnt/media

# macOS: the NAS accepts connections from reserved ports only (resvport)
sudo mount -t nfs -o resvport YOUR_NAS_IP:/mnt/pools/data/media ~/media
```

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
  item with the same name is already there, nothing is copied or moved, and a
  move that one of the items cannot make (such as a folder that is a mount
  point) moves none of them. When a move stops at an item after others moved,
  the message says how many moved before it.
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
  replaced. An upload that gets no data for 2 minutes (the network dropped,
  or the computer went to sleep) fails, and nothing of it is kept: upload
  the file again. Until a file has arrived in full it is not in the folder,
  and if UNOS stops in the middle of the upload (a restart, an update, a
  power cut), nothing of it stays; only on a disk mounted there by hand
  whose filesystem cannot do this (vfat or exfat, for example) it stays as
  a hidden `.unos-upload-...` file (see
  [Leftovers of interrupted uploads](#leftovers-of-interrupted-uploads)).
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
subvolumes or filesystems mounted in a pool, also a folder that is a bind
mount of another folder of the pool, cannot be deleted, renamed or moved
here: delete volumes on the **Storage** page (see
[Pool Operations](#pool-operations)). Renaming a folder that a share, an NFS
export or an app uses is not refused, but they keep using the old name, which
no longer exists: rename the folder back to restore them.

A folder that contains a subvolume (such as a snapshot) or a mounted
filesystem cannot be deleted here, or moved to another pool or volume. When
one appears below a folder while the folder is deleted for good, the delete
stops there: what it deleted before stays deleted, and the message says so.

### Leftovers of interrupted uploads

A hidden `.unos-upload-` file with 16 letters and digits after the dash is
what an upload into a folder on a vfat or exfat disk (or another filesystem
mounted there by hand without unnamed files) had received when UNOS stopped
in the middle of it. It holds only part of the file, and nothing uses it.

- UNOS looks for such files in every folder of the pools and of the disks
  mounted in them, down to 64 folders deep, 10 minutes after each start; it
  leaves out the trash and the snapshots. Scripts can ask for a check at once
  with `POST /api/v1/files/upload-leftovers/check`, for example after
  mounting a disk.
- While it found any, the File Browser shows **Leftovers of interrupted
  uploads** above the list, with their number and the space they take, as
  the check found them. **Remove…** lists each file with its folder, size
  and the time it was last written, and **Remove for good** deletes them at
  once, without the trash; that cannot be undone, so Cancel has the keyboard
  focus. Nothing removes them otherwise.
- A file that an upload is still writing, or that changed in the last 10
  minutes, is never listed or removed. If one of the listed files changed
  since the check, **Remove for good** removes nothing and says why; the
  dialog then lists the other files, and **Remove for good** removes them.

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
  It passes over a pool whose disks sleep, so as not to wake them, and clears
  out its trash at the first hourly check after they woke.
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
- To act on several containers, tick their checkboxes (in the table or on the
  cards), or **Select all shown** for every container the search and the state
  filter show; a container they hide is unselected. **Start selected**, **Stop
  selected** and **Restart selected** act on each of them in turn, and the page
  shows which one it is at; meanwhile the containers' own buttons wait.
  **Restart selected** restarts the running ones only, as a container's own
  **Restart** does: one that does not run is listed as not restarted. Then the
  page lists each container with its new state, or why it failed: one
  container's failure does not stop the others, and what was not done stays
  selected for another try. Leaving the page stops the run after the container
  it is at. Removal is one container at a time.
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

The App Store provides one-click installation of popular applications. It
follows the language chosen on the **Account** page (see [Language](#language)),
except what comes from the app catalog: the names and descriptions of the apps
and of their settings, and their memory recommendations, are in English.

### Available Categories

The list of categories filters the catalog, and each app's card and details
name its category the same way. Some of them:

- **Media** - Plex, Jellyfin, Emby, Immich
- **Downloads** - qBittorrent, Transmission
- **Productivity** - Nextcloud, Syncthing
- **Documents** - Stirling PDF, Wiki.js
- **Smart Home** - Home Assistant
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
6. Click **Install** in the details: the details stay open while the install
   checks the pool, the ports and the free space, and show why it is refused
   if it is, so that you can change the settings and try again
7. Once the checks have passed, the details close, and a card at the top of
   the **App Store** follows the install: each image with its download (a bar
   of its size, and how many of its layers are there), each container as it is
   created and started, and at the end the result, or why the install failed.
   You can leave the page or reload it meanwhile: the install goes on in the
   background, and the card shows where it is when you come back. **Cancel
   install** stops an install that runs, after a question: what it made so far
   (containers, the app's network) is removed, and its data folders and the
   downloaded images are kept. **Dismiss** removes the card of an install that
   has ended

Only one app installs at a time: while an install runs, the App Store offers
no other install until it has ended.

Before it downloads anything, the install checks that:

- the app is not installed yet: each app of the catalog is installed once;
- its host ports are free. A port that another container publishes, also a
  stopped one, or that a program on the NAS listens on, is refused with a
  message that names the container or the program: choose another host port on
  the **Ports** tab. Home Assistant and Scrypted run in the NAS's own network
  and listen on its ports, which cannot be changed: stop the program or remove
  the container that the message names;
- the system disk has at least 2 GiB free, whatever the app. For an app whose
  images UNOS pins (Immich), the 2 GiB must also stay free once the images it
  lacks are downloaded, each counted with its known size; the size of any
  other app's images is not known before the download, so only the 2 GiB
  count. When there is too little space, remove unused images on
  **Containers** > **Images** first.

Downloading an app's images can take several minutes. A pinned image (one of
Immich's) that the NAS already has shows as **Already on the NAS** and is not
downloaded again; any other app's image is asked for again, and only the layers
that the NAS lacks are downloaded. A download that makes no progress at all for
10 minutes is stopped, and the install fails with a message that says so:
install the app again to try once more.
Starting or restarting an app from UNOS fails while its data folders are not on
a mounted pool.

UNOS keeps the progress of installs in memory only: when UNOS restarts during an
install, the install stops there and its card is gone (the details of an app
whose install was still being checked say so). If it had made some of
the app's containers already, the app is listed on the **Installed** tab,
stopped or incomplete: start it, or uninstall it (its data folders are kept)
and install it again. Containers that UNOS had started are left running.

### Apps of Several Containers

Some apps run in several containers, which UNOS installs, starts, stops and
uninstalls together as one app. Immich runs in four: its server `immich`,
which serves its web page on port 2283, its database `immich-database`
(PostgreSQL), `immich-redis` (Valkey, which queues its jobs) and
`immich-machine-learning` (smart search, faces and text recognition). They
reach each other on a Docker network of their own, `unos-app-immich`, and only
the server publishes a port. The details of such an app list its containers
with their images on **Overview**, and its folders and settings per container
on **Volumes** and **Environment**, of which only the main container's can be
changed.

- **Pinned images.** UNOS installs each of Immich's images by its digest, so
  that the install gets the version that UNOS was checked with (Immich
  v3.4.1). They take about 3.6 GB on the system disk.
- **Where the data is.** Everything Immich keeps is on the pool you choose: the
  photos and videos, their thumbnails and Immich's daily dumps of its database
  in `apps/immich/library`, the database in `apps/immich/postgres/data`,
  Valkey's in `apps/immich/redis`, and the machine learning's models (about
  2.6 GB, downloaded when they are first used) in `apps/immich/model-cache`.
  The system disk holds only Immich's images, its containers' records and
  their logs: what the containers write while they run is kept in memory.
- **The database's password.** UNOS generates it at the install, keeps it in
  the file `apps/immich/postgres/password` on the pool, which only root can
  read, and gives it to the server and the database as that file: it is never
  shown. Keep the file with the database. An install that finds the database
  without its password file is refused, since a new password would not open
  it: restore the file from a snapshot of the pool, or move the database folder
  away to start with an empty database.
- **The administrator.** Open Immich as soon as it is installed and create its
  administrator: the first account that registers becomes the administrator.
  The notice of the install says so.
- **The pool's disks.** While Immich runs, the hard disks of its pool do not go
  to sleep: its database and its jobs keep writing to them. Stop Immich on the
  **Installed** tab to let them sleep.
- **A failed install** stops the containers that it started, removes them and
  the network that it made, and keeps the data folders, the password file and
  the downloaded images, so that installing again uses them. When Immich's
  database failed at its first start, the message names its folder, which may
  hold a database that was not finished: move that folder away before you
  install again.
- **Updates.** UNOS does not update an installed app yet. To move Immich to a
  newer version of the catalog, take a snapshot of its pool, uninstall Immich
  and install it again: Immich changes its database at its first start and
  cannot go back to an older version, so the snapshot is the way back.

BookStack, Paperless-ngx and Linkwarden are no longer in the catalog: each needs
a database or a broker beside it, and they will come back as apps of several
containers.

### Hardware Acceleration

On a NAS with a Rockchip RK3588 (such as the Banana Pi BPI-W3) under
Rockchip's kernel (5.10 or 6.1, such as Armbian's `vendor` kernel), Jellyfin's
card shows **Hardware acceleration**, and its details say what it gets: UNOS
installs it with the chip's video decoders and encoders (Rockchip MPP), its 2D
engine (RGA) and its GPU, those of `/dev/dri`, `/dev/dma_heap`,
`/dev/mpp_service`, `/dev/rga` and `/dev/mali0` that the NAS has, in a
container without privileged mode. The container can read the NAS's device
tree, which Rockchip MPP reads to know the chip, and runs without Docker's
AppArmor profile, which forbids that. Only Rockchip's kernel has Rockchip
MPP's `/dev/mpp_service`: under another kernel, such as the mainline kernel,
the App Store says nothing about hardware and Jellyfin is installed as on any
other NAS, and transcodes in software.

UNOS does not change Jellyfin's settings: in Jellyfin, choose **Dashboard** >
**Playback** > **Transcoding** > **Hardware acceleration**: **Rockchip MPP
(RKMPP)**, and tick the codecs to decode in hardware. The details of an
installed Jellyfin say this only when its container has the hardware. One
installed without it, as when UNOS could not look for the device nodes during
the install, shows **No hardware acceleration** on its card, and the notice
of its install and its details warn instead: uninstall it (its data folders
are kept) and install it again on the same pool to give it the hardware. On
any other NAS the App Store says nothing about hardware, and apps are
installed as before.

On the same NAS Immich's card shows **Hardware acceleration** too: UNOS
installs its machine learning with the RK3588's NPU and the image of Immich's
machine learning built for it, so that smart search, face recognition and text
recognition run on the NPU. Its container gets `/dev/dri`, where the NPU's
nodes are, can read the device tree and runs without Docker's AppArmor
profile, in a container without privileged mode. Only Rockchip's kernel has
the NPU's driver (RKNPU; Immich asks for 0.9.8 or later): under another
kernel, and on any other NAS, Immich's machine learning runs on the CPU.
Immich's server gets the video hardware as Jellyfin does: to transcode videos
in hardware, choose **RKMPP** in Immich under **Administration** >
**Settings** > **Video Transcoding Settings** > **Hardware Acceleration**. An
Immich a container of which was installed without its hardware shows **No
hardware acceleration**, and reinstalling it gives it the hardware.

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
`unos-smb`, **Runs in the group unos-smb**, and to act on them. An app of
several containers has one card, which names its containers. When the app's
image checks its own health, the card shows **Starting**, **Healthy** or
**Unhealthy**.

- **Start**, **Stop** and **Restart** an app; its card shows the new state. A
  start or restart is refused while the app's data folders are not on a mounted
  pool, and the message names the pool to mount. For an app of several
  containers they act on all of them: a start starts the other containers
  first and the main one once those it needs are ready (Immich's server once
  its database and Valkey are), a stop stops them in the reverse order, and a
  restart stops them all and starts them again.
- **Uninstall** removes the app's containers, and the network of an app of
  several, after a confirmation and keeps its data folders. Installing the app
  again on the same pool uses them again.

The **Containers** page lists the containers of an app of several containers
one by one. An app one of whose containers was removed there is
**Incomplete**: it cannot start, and its card asks to uninstall it (its data
folders are kept) and install it again. The card of a running app also warns
when one of its other containers has stopped: restart the app, and read that
container's log on the **Containers** page if it stops again.

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

### Renaming Users

Edit the user and type the new name under **Name**, then click **Save**. The
new name follows the rules of a new username (see above); a name that another
user has, also in another letter case, is refused with the reason ("Username
already exists", or "Username already exists as 'Bob': names that differ only
in letter case are one account for Samba"). A name from before these rules
(such as `Erin.Old`) can be changed to one that follows them, which also lets
the user use SMB. Change the role in a save of its own.

The user signs in with the new name from then on; the old name is refused,
and every session of the user ends. Renaming yourself signs you out: sign in
again with the new name. API tokens keep working. The user's
[login history](#login-history) starts again at the rename: it lists the
sign-ins with the new name from then on, not those with the old name, nor
those of anyone who had the new name before.

A user with SMB on keeps it under the new name, but needs a new password,
which the dialog asks for: Samba keeps passwords by name, so the SMB account
of the new name is made with it, and it is the user's UNOS password too. The
user's open SMB connections end; Samba's account of the old name is removed;
the user's host account on the NAS takes the new name and keeps its user id,
so the files the user wrote stay theirs; and every share's access follows the
new name. Update the name and the password on every computer and app that
connects over SMB. When the host cannot take the new name (it has an account,
a group or a Samba account of that name), or the user's SMB connections do not
end, nothing is renamed and the dialog says why. When SMB could not be turned
on again under the new name, the user is renamed with SMB **Password needed**,
and the page shows why.

### Groups

A group gives several users the same access to shares. Under **Groups** on
the **Users** page, click **New group**, enter a name (1 to 32 lowercase
letters, digits, `_` or `-`, starting with a letter; no two groups may have
the same name in any letter case) and tick its members, then click **Create
group**. Administrators are not listed, since they cannot use SMB, and users
whose names SMB cannot use cannot be members. A user whose SMB is off or
needs a password can be a member: the group's access counts for them once
their SMB is on.

Give the group access on the **Shares** page: in a share's access list each
group has its own level (see [How Access Works](#how-access-works)). Each
group on the **Users** page lists its members and the shares it has access
to.

- **Edit** renames the group or changes its members. A member added gets the
  group's access at once; a member removed loses it, and their open
  connections to the group's shares are closed (the computers reconnect with
  the access they still have).
- **Delete** removes the group and its access to shares; its members lose
  what the group gave them, and the users themselves stay.
- A deleted user, and a user made an administrator, leave every group; a
  renamed user stays in their groups under the new name.

A group exists only in UNOS: Samba's configuration lists the members by name,
and no group is made on the NAS itself, so a group of the NAS's own (such as
`users` or `staff`) is never affected and may have the same name. Groups are
part of the database, so a backup holds them and a restore brings them back as
they were at the backup; a member counts again once their SMB account is
active after the restore.

### User Roles

| Role | Permissions |
|------|-------------|
| Admin | Full access to all features, except SMB: administrators cannot use SMB |
| User | **Dashboard** (system overview and live statistics) and **Account** (language, own password, two-factor authentication, login history and SMB access); SMB shares as an administrator allows |

Every other page and API is for administrators only; regular users do not see
them. At least one administrator must remain: the last one cannot be demoted
or deleted. Making a user an administrator removes the user's SMB access,
share permissions and groups and ends the user's open SMB connections.

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

Then give the user access to shares on the **Shares** page, or add the user
to a group that has access (see [Groups](#groups)).

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

An SMB password changes only in UNOS: the user under **Account** →
**Change Password**, or an administrator in the edit dialog of the **Users**
page. Samba refuses every password change that a computer asks for over the
network, such as **Change a password** in Windows (Ctrl+Alt+Del) aimed at the
NAS, whatever the new password is, and the old password keeps working. Samba
answers that the new password breaks a password rule, so Windows is expected
to say that the new password does not meet the length, complexity or history
requirements; this has not been tried with a Windows computer yet.

After 5 wrong SMB passwords in a row, Samba locks the user's SMB account for 5
minutes: until they are over, every computer is refused, with the right
password too, and then the right password signs in again. Wrong passwords
count together from every computer; after 5 minutes without one, or a
successful sign-in, the count starts again. The two passwords before the
current one do not count, so a computer that still tries the password of
before a change locks nothing. Anyone on the network who knows a username can
lock its SMB account in this way, again and again; the web interface and the
other accounts are not affected, and UNOS does not show the lock; a new
password does not end it either. **System** → **Logs** names the computer
whose sign-ins fail (see [Logs](#logs)). An administrator who turns the user's
SMB off and on again ends the lock at once.

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
Your other sessions end (on other browsers and devices); this one stays signed
in. A password that an administrator sets on the **Users** page ends every
session of that user.

Admins can reset any user's password via **Users** page. When no administrator
can sign in, root on the NAS resets an administrator's password (see
[Recovery](RECOVERY.md#reset-an-administrators-password-and-two-factor-authentication)).

When SMB is on for the user, the SMB password changes too, and the user's open
SMB connections are ended: the computers sign in again with the new password.
The new password must then follow the SMB rules (at least 8 characters, not a
common password; see [SMB Access](#smb-access)). Update the saved password on
every computer and app that connects over SMB (mapped drives, media players,
sync apps, `cifs` credential files). A device that keeps trying an old
password is refused each time, and each refusal adds a line to the system's
log. The two passwords before the current one do not count toward the lock of
the SMB account; an older one does, and a device that keeps trying it keeps
the account locked (see [SMB Access](#smb-access)). If
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
2FA off, and an administrator cannot reset it for you. For an administrator
who lost both the app and the recovery codes, root on the NAS sets a new
password and turns 2FA off (see
[Recovery](RECOVERY.md#reset-an-administrators-password-and-two-factor-authentication));
a regular user who lost both has no way back into the account yet.

Scripts send the code as `recovery_code` in place of `totp_code` to
`POST /api/v1/auth/login`; `POST /api/v1/auth/recovery-codes/generate`
answers new codes, and `GET /api/v1/auth/recovery-codes/status` how many are
left.

#### Two-Factor Authentication for Every Account

An administrator can require two-factor authentication for every account,
administrators included. Under **System** → **Security**, the card **Two-factor
authentication for every account** says whether it is **Required**. To turn it
on, your own account needs 2FA: choose the **Grace period** (none, 1, 3, 7, 14
or 30 days; 7 is offered first), enter a current code of your authenticator
app and click **Require for every account**. **Stop requiring** turns it off,
with a code as well. The audit log records both (`settings_changed`, resource
`two_factor_requirement`; turning it off as a **Warning**).

What it means for an account without 2FA:

- The grace period starts when the requirement is turned on, or when the
  account is created, if that is later. The card shows the name the
  administrator had when turning it on.
- During it, everything works as before, and after each sign-in a page asks
  to set up 2FA by the end of the grace period: **Set up now** shows the QR
  code and asks for a code, **Later** goes on to the dashboard.
- After it, the password still signs in, but the account can only set up 2FA:
  the same page, without **Later**, with **Sign out**. A session that was open
  when the grace period ended is led there at its next request; the server
  refuses every other request of the account (HTTP 403 "Two-factor
  authentication is required: set it up to continue"), and the account's API
  tokens stop working. Once 2FA is on, the account has its rights again at
  once: generate recovery codes next.
- An account that turns 2FA off after its grace period can only set it up
  again at once; there is no new grace period.
- SMB has no second factor: an account with SMB on still signs in to SMB with
  its password alone. Turn SMB off for a user on the **Users** page where that
  matters.

A restore whose database requires it, or no longer requires it, says so among
its warnings, and the audit log records the change. A start of the requirement
that lies ahead of the NAS's clock, as on a board without a real-time clock
before it has set its time from the network, counts as now: the grace period is
never skipped. When the database holds a requirement whose start cannot be read,
the card says so, and accounts without 2FA get no grace period.

Turning the requirement off gives every account its rights back at its next
request. Scripts read it with `GET /api/v1/security/two-factor-requirement`
and change it with `POST /api/v1/security/two-factor-requirement/enable`
(`grace_days`, `code`) and `/disable` (`code`), with a session, not an API
token; `GET /api/v1/auth/me` answers `two_factor_required` for the signed-in
account.

### Login History

Under **Login History** on the **Account** page (administrators: **System** →
**Security**), **View History** lists the latest 50 sign-ins, newest first:
those with your account's name since your account got it, when it was created
or last renamed (an account that gets the name of a deleted or renamed one
does not see that account's), and for administrators those of every account,
also of deleted accounts, of names given up by a rename and the attempts with
names that do not exist. Each entry shows **SUCCESS** or **FAILED** with the
reason (such as "Incorrect username or password" or "Invalid 2FA code"), the
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
  English page names it (`login failed`), the address, the resource as the
  English page shows it (`user carol`), the details or the browser. A Russian
  page names actions and resources in Russian, but the search finds them by
  these English words only, as the page says when a search finds nothing.
  **User** takes an exact user name, **Action** one kind of entry, and **From**
  and **To** a range of days, each day included whole. The list follows the
  filters as you change them; **Clear filters** resets them.
- The page shows the newest 100 matching entries; **Show more** adds 100 more.
- Each entry carries the hash of the one before it. **Chain broken** marks an
  entry that was changed in the log file, or the one after an entry that was
  removed, put in or damaged there. When the newest entries are removed or
  rewritten while the supervisor runs, a **Critical** entry of the user
  `system` says so. No mark is no proof: the hashes have no key, so whoever
  can write the file while the supervisor is stopped can write a new chain,
  and removing the oldest entries shows no break, as after a rotation or a
  clearing. Entries recorded before this chain have no mark.
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
  appears only in the login history), sign-outs, a user's own password
  changes (with an SMB account entry when the SMB password changed too) and
  turning two-factor authentication on or off (off as a **Warning**), each
  by that user, users created, changed (a rename with the old name) and
  deleted, pools created and
  removed, volumes created and deleted, snapshots created, deleted and
  restored, containers started, restarted, stopped and removed on the
  **Containers** page, apps started, restarted and stopped on the **Apps**
  page (a restart as **Container started**), apps installed and uninstalled
  (removals and deletions as a **Warning**), the SMB access of users, groups
  created, changed and deleted (deletions as a **Warning**), shares
  created, changed, deleted and repaired, NFS exports created and deleted,
  shares imported from smb.conf, the corrections of the SMB check at startup
  and the SMB state after a restore (by the user `system`), API tokens
  created and deleted, new recovery codes, the reset of an administrator on
  the NAS's command line (`password_change` and `totp_disabled` by the user
  `root`, see
  [Recovery](RECOVERY.md#reset-an-administrators-password-and-two-factor-authentication)),
  and the clearing of the log. Backups created, uploaded, restored (a
  **Warning**, with what it restored) and deleted; a reboot or shutdown
  (a **Warning**, recorded as soon as it is scheduled, a minute before the
  host goes down) and its cancel; scheduled tasks created, changed (with the
  names of the fields that changed and, for a rename, the old name; never
  the command or the passphrase), enabled, disabled, deleted and run with
  **Run now** (**Task run**); a network interface disabled, its
  disable kept, and enabled; Wake-on-LAN turned on or off; a new host
  name or time zone (**Settings changed**, from the old value to the new);
  and two-factor authentication required for every account or no longer
  (**Settings changed**, resource `two_factor_requirement`; no longer as a
  **Warning**), with the sign-ins of accounts that can only set it up (a
  **Warning**).
  Only a change that was made writes an entry: a refused or failed one, or
  one that changes nothing (the same host name, a task saved unchanged,
  removing a pool that is not there, starting a running container or
  stopping a stopped one), writes none. A change that failed part way
  writes its entry as **Failed**, with what was done and then the error: a
  pool create that formatted the disks, a pool removal that unmounted the
  pool or dropped its fstab entry, an uninstall that stopped the app
  (**Container stopped**), and an enable of a network interface that turned
  it on without all its saved addresses and routes. A refused change of your
  own account, such as a wrong current password or two-factor code, writes
  no entry either. Mounting, unmounting and importing pools, creating
  containers and pulling or removing images on the **Containers** page, the
  snapshots that scheduled tasks take and clean up, the apps started when a
  pool is mounted, suspend, the off-device copy, notifications, the firewall,
  the TLS certificates and clearing the login history write no entries.
- **Action** offers only the actions that the NAS records. An older entry
  whose action UNOS no longer has, such as `firewall_rule_added`, is listed
  with its action as recorded.

The same filters work in the API, for administrators: `GET /api/v1/audit/logs`
(with `limit` from 1 and `offset` from 0), `GET /api/v1/audit/logs/count` and
`GET /api/v1/audit/logs/export` (`format=csv` or `json`) take `search`,
`username`, `action`, `severity`, `start_date` and `end_date`; `action` takes
any action name, also one that only older entries hold. A bound covers
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
- **Name and time zone**: the host's name and time zone, set on the host.
  **Save hostname** sets the name (letters, digits and hyphens, at most 63);
  **Save time zone** sets one of the zones the host lists, and scheduled tasks
  then run at the times of that zone. When the host refuses a change, the page
  shows its message and keeps the old value. Samba and network discovery (the
  name Windows and macOS show) take a new hostname when they start: until the
  NAS restarts they show the old one.

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

#### UPS (API only)

The UPS settings and state are in the API only, for administrators. UNOS
reads the UPS with NUT's `upsc` on the NAS, from the NUT server (`upsd`) that
the settings name. UNOS does not install NUT; to read a UPS, install and
configure it on the NAS (`sudo apt-get install nut`, or `nut-client` for a
NUT server on another machine).

- `GET /api/v1/power/ups/config` answers the settings with `has_password`,
  which says whether a NUT password is stored: no answer contains the
  password itself. `PUT` takes the settings with `password`: a body without
  one (absent, null or empty) keeps the stored password, a body without a
  `username` removes it, and a password without a username is refused (400).
  `ups_name`, `host` (a host name, an IPv4 address, or an IPv6 address in
  brackets) and `port` are checked, since they reach `upsc`. UNOS does not
  use the username and password yet.
- `GET /api/v1/power/ups/status` reads the UPS at once. When it cannot, it
  answers `available: false` with the `reason`, for example "The host has no
  NUT client: upsc was not found. Install the package nut-client on the
  host". `POST /api/v1/power/ups/test` answers 503 with the same reason, and
  `GET /api/v1/power/ups/list` an empty `devices` list with it as `error`.
  Values with decimals are read, percentages and minutes as whole numbers; a
  value that is no number is null, and the others are still read.
- While the settings are `enabled`, the UPS monitor reads the UPS every 30
  seconds. `GET /api/v1/power/ups/monitor/status` says whether its last check
  read the UPS (`available`) and, when not, the `reason`. While the NAS has no
  `upsc`, the monitor only checks again: it writes nothing, logs the reason
  once, and shuts nothing down; it reads the UPS once NUT is installed. The
  shutdown on low battery does not run in the supervisor's container yet.

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

**Auto** follows the journal: the tab lists the newest entries again and then
adds each new entry of the chosen source, level and search at the top as the
journal gets it, keeping the newest entries of the limit, and says
**Following new entries**. A filter or source changed while Auto is on is
followed from then on; clicking Auto again stops it and keeps the list. When
the connection to UNOS is lost, Auto stops with **Auto stopped: the
connection to the server was lost** and the list stays. **Search** waits
while an answer is still coming. When the journal cannot be read, the tab
shows why instead of the list. A search through a large journal can take long: after
30 seconds UNOS stops journalctl on the host and the tab reports that it did
not answer; a shorter time range helps.

**Failed SMB sign-ins**: Samba logs each failed SMB sign-in as a line of the
level Notice, which the source **Samba** lists. Search for `Auth:` to list
only these lines.
Each one names the account the computer tried (`user [WORKGROUP]\[alice]`),
the reason (`status [NT_STATUS_WRONG_PASSWORD]` for a wrong password,
`[NT_STATUS_ACCOUNT_LOCKED_OUT]` while the account is locked after 5 of them,
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
[Scheduled Snapshots](#scheduled-snapshots) and [System Backup](#system-backup)),
off-device copies (see [Off-Device Copy](#off-device-copy)) and custom commands. Only administrators see the tab, and only they can use
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

### HTTPS Certificates

Under **SSL/TLS Certificates** on **System** → **Security** (administrators
only), the badge names what browsers get over HTTPS: **Caddy's local CA**,
the certificate that Caddy, the web server of UNOS, issues from its own
certificate authority for the name in `UNOS_HOSTNAME` (see
[HTTPS and the Certificate Warning](INSTALL.md#https-and-the-certificate-warning)).
**Manage** says so again and lists the **Stored Certificates**, each with its
type, issuer, end date, **VALID** or **EXPIRED** and **NOT SERVED**.

UNOS stores certificates but does not serve them yet: browsers keep getting
the local CA's certificate whatever is stored (feature-list entry
`security.custom-certificates`). **Generate Self-Signed** stores a certificate
for a host name or an IP address (a name of at most 64 characters), valid for
the days you choose, with an optional organization (at most 64 printable
characters) and an optional country (two letters, such as `DE`); when UNOS
refuses what you entered, the dialog says why. The API's
`POST /api/v1/security/certificates/upload` stores a certificate with its key
and chain under the name of its subject; a certificate stored under the same
name replaces the old one with its key, and its chain too, which goes when the
new certificate has none. A store that fails keeps the old files. The trash
button deletes a stored certificate. When the certificates cannot be loaded,
the card says so with the reason and **Retry**.

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
a pool snapshot is also a volume of the pool. The copy of a volume's snapshot
is a subvolume inside the volume, and the volume cannot be deleted while it
holds the copy.

#### Restored Copies

Below the snapshots, **Restored copies in** the pool or volume you chose lists
the copies that Restore made right in it, newest first, with their folder and
the time of the restore (from the name, shown in your browser's time zone).
Only such copies are listed: a writable snapshot named
`restore_<date>_<time>` right in the pool or the volume.

To delete one, click its **Delete** button and confirm; its files are deleted
with it, and this cannot be undone. As for a volume, the copy is not deleted
while a running container, an SMB share or an NFS export uses it or a folder
in it, or while an off-device copy or restore reads or writes there: the
dialog names them. Stop or remove them and try again. When Docker does not
answer, nothing is deleted either. A share or export of the volume around the
copy does not count. Once its copies and snapshots are deleted, the volume
can be deleted on the **Storage** page.

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
other names are never deleted. When btrfs does not answer within 60 seconds
while the old snapshots are looked at, none is deleted, the new snapshot
stays, and the run fails with `Snapshot <name> created, but old snapshots were
not cleaned up: btrfs did not answer within 60 seconds. The pool may not be
responding.` Until the stuck btrfs ends, which can take until a reboot, the
next runs fail the same way, and the snapshots pile up beyond the keep count.
A run on a pool that is not mounted fails with `Pool <name> is not mounted at
/mnt/pools/<name>`, which the task's history shows.

### System Backup

Go to **System** → **Backup & Restore**. A backup holds the UNOS database:
user accounts with their passwords, two-factor authentication, API tokens,
the login history, the groups of users with their members, and the SMB
shares with their access lists, those of groups included. It does not
hold the files in your pools, Samba's own password database, nor settings
kept elsewhere, such as notification channels, scheduled tasks, firewall
rules, UPS settings, the network settings and the host's `/etc/fstab`. Each
backup in the list has **Restore**, **Download** and **Delete** buttons.

- **Create Backup** - Makes an unencrypted backup (`.tar.gz`).
- **Upload Backup** - Adds a backup file of at most 256 MiB: `.tar.gz`, or
  `.tar.gz.enc` for an encrypted backup. It is stored under a new name,
  `uploaded_<date>_<time>` with the same extension. A `.tar.gz.enc` file
  that is gzip data to its end, such as an unencrypted backup, is refused.
  Any other `.tar.gz.enc` file is stored, and its content is checked only
  when it is restored with the passphrase. An upload that gets no data for
  2 minutes fails, and nothing of it is kept.
- **Restore** - Type the backup's file name to confirm; an encrypted backup
  also needs its passphrase. Restoring replaces the database with the one in
  the backup: the user accounts with their passwords, two-factor
  authentication, API tokens and the login history are then those of the
  backup, so an API token revoked after the backup was made works again.
  Nothing else is restored: the scheduled tasks that the backups of backup
  tasks also hold are named in the result as not restored.

SMB after a restore:
- The shares and their access lists, and the groups with their members, come
  back with the database, and UNOS writes smb.conf from them; a backup holds
  no smb.conf. A backup made before UNOS had groups restores without groups,
  and one whose groups break the name rule or share a name is refused. A
  backup whose database never imported its shares gets the shares of the
  current smb.conf, without guest access: smb.conf names users, not groups,
  so every user who has access in it, also through a group, gets that access
  of their own, which stays when they leave the group, and the groups come
  back without access to shares. A backup of a UNOS from before shares were
  stored in the database is refused.
- SMB keeps working for users whose password did not change since the backup
  was made. Everyone else shows **Password needed** and must turn SMB on
  again (see [SMB Access](#smb-access)), and so must a user whose SMB user id
  belongs to another account on this NAS (for example in a backup of another
  installation), or once went to a user of another name who was deleted from
  this NAS: files on the pools keep the user id of their owner, so UNOS never
  gives it to anyone else. Such a user gets a new SMB user id. A user deleted
  after the backup was made comes back with their own SMB user id, and so with
  their files. UNOS also keeps the SMB user ids that the backup's own NAS gave
  to users it deleted, so a backup restored after a reinstall still protects
  their files. A backup whose database gives one SMB user id to two
  accounts is refused, and nothing is restored.

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

### Off-Device Copy

The backups above stay on the system disk, and nothing else copies the files
of your pools: a disk that fails takes them with it. The **Off-device copy**
section of **System** → **Backup & Restore** copies both, with restic, to one
place off this device, the copy repository. A copy repository serves one
installation: give each NAS a folder or a bucket prefix of its own.

- **A disk**: a folder on a disk mounted below `/mnt`, such as a USB disk added
  as a pool (`/mnt/pools/usb/unos-copy`). **Save and test** makes the folder
  when only it is missing. A folder on the system disk, or on another
  partition of it, is refused, and so is the mount point of a disk that is not
  mounted: it is an empty folder there. A folder on a filesystem whose disk
  UNOS cannot find, such as a network filesystem or ZFS, is refused too, since
  UNOS cannot tell whether the copy leaves the system disk; so is a folder that
  holds other files but no copy repository.
- **An S3-compatible store**, such as Backblaze B2, Wasabi, Amazon S3,
  Cloudflare R2 or MinIO on a second NAS: its endpoint (`https://`, or
  `http://` in your own network, since restic encrypts everything anyway),
  bucket, an optional prefix and region, the access key ID and the secret
  access key. Upload and download limits in KiB/s (0: none) keep a copy from
  filling your connection.

To set it up:

1. Choose where the copies go, and the folders to copy: every pool, by
   default, and a folder below a pool through **Another folder to copy** and
   **Add folder**. The pools' snapshots and trash are not copied, and neither
   is the folder of the copy when it lies inside a copied folder.
2. Enter the password of the copy repository twice, at least 12 characters.
   **Keep it somewhere outside this device**: without it nobody, UNOS
   included, can read the copies, and the configuration backups do not hold
   it. The NAS keeps it, readable only by the supervisor, so that copies run
   unattended. Editing the settings of the same target with both fields empty
   keeps it. Another target needs a password; the password of a repository
   cannot be changed here.
3. **Save and test**: the NAS opens the repository with the password, or makes
   a new one when the folder or bucket has none. A wrong password, or a store
   that cannot be reached, is refused with restic's message. The NAS first
   connects to an S3 store: one that does not answer within 10 seconds is
   refused at once, and restic gets 2 minutes at most.
4. **Add a daily copy at 03:00** adds a task of the type **Off-device Copy**
   to **System** → **Scheduler**. Until there is one, copies run only on
   **Run now**.

Each copy sends the configuration (the archive that **Create Backup** makes)
and the changes of the folders since the last copy; removes the snapshots
beyond the retention (7 daily, 4 weekly and 12 monthly, kept for the
configuration and the folders each); checks the repository, reading 2% of its
data (the share you set); and reads the configuration back and checks it as a
restore would, the restore test, without restoring anything. The check reads
its share anew at each copy: on a store that bills downloads, such as Amazon
S3, 2% checked daily downloads about 60% of the repository a month. Set a
smaller share there, or 0, with which the check reads none of the data and
covers only the repository's structure. The first step that fails ends the
copy, with one exception: files that cannot be read. A file deleted or moved
after restic listed its folder and before it opened it is left out without a
word, as if it had not been there; one that vanishes after restic opened it
and before it read it is named in a note, and the copy succeeds. Files that
cannot be read for another reason, such as a missing
permission or a read error of the disk, are left out of the snapshot, the
other steps still run, and then the copy fails, naming up to 5 of them.
**State** shows the last copy: whether it succeeded, the files new, changed and
unmodified, how much it added, the check and the restore test, the note, or the
step that failed with restic's message; **Refresh** reads it again. A failed
copy of a task is alerted to every notification channel. A copy that starts
while another one or a restore runs is skipped and counts as a success: the
first copy of a large pool to a cloud store can take days. A step that finds
the repository locked by another program waits up to 10 minutes for it. A
copy that ends hard, at a power cut say, leaves the repository locked for up
to 30 minutes: a copy in that time fails, and the next one runs again.

While a copy or a restore runs, the disks it uses stay mounted: **Unmount** and
**Remove Pool** on **Storage** refuse for its pools, and so does a plain
`umount` on the host, until it ends. **Delete Volume** refuses for a volume
that holds its target, a folder it copies or the folder a restore writes.

A copy on the disk of a folder it copies, such as a folder of the same pool,
protects against deleted and damaged files, not against the loss of that
disk: the section says so.

To restore:

- **Restore configuration** on a snapshot of the configuration puts its
  archive into the backup list above, as `offsite_<date>_<time>.tar.gz` (the
  snapshot's time, in UTC), after the checks of an upload. Restore it there,
  as any backup.
- **Restore files** on a snapshot of the folders restores all of it, or one
  folder as the snapshot holds it (`/mnt/pools/data/photos`), into a new folder
  `restored-<date>-<time>` (UTC) at the top of the pool you choose. Nothing is
  overwritten: take what you need from there with the **File Browser**. A
  restore that fails removes its folder when nothing came into it.
- On a new device: install UNOS, set up the same target with its password,
  **Restore configuration**, restore that backup, then **Restore files**.

**Forget this target** removes the settings and the password from the NAS,
damaged copies of their files included; the copies stay where they are. A task of the type **Off-device Copy** then fails
at each run, which is alerted, until a target is set up again: delete it on
**System** → **Scheduler** when no copies are wanted.

---

## Notifications

UNOS sends alerts on its own, to every enabled channel of every kind (e-mail,
Telegram, webhooks, Discord and Slack): one for each problem it finds (see
[Alerts](#alerts)), one for new error counts of a pool's devices, and one for
a disk's rising SMART counters (see [Disk Health](#disk-health)).

### Alerts

UNOS watches for these problems, and sends one alert for each, through every
enabled channel, when it finds it:

| Problem | Alert | Checked |
| --- | --- | --- |
| A pool that UNOS manages is not mounted, or none of its disks is connected | warning | every 5 minutes |
| A managed pool is degraded: a disk is missing, or its disks count errors (also errors a scrub found) | error | every 5 minutes |
| btrfs made a managed pool read-only after a serious error | error | every 5 minutes |
| A mounted managed pool is 90% full: the banner shows it as an error from 95%, and keeps it until the pool is below 85% | warning | every 5 minutes |
| A volume of a mounted pool has used 80%, then 90%, then 95% of its quota: one alert per level, the last an error; a level counts again once the use fell 5 points below it, and the banner drops the volume below 75% | warning, at 95% error | every 5 minutes |
| A disk fails its SMART health self-assessment (health FAILED; for an NVMe disk not when it is only too hot) | error | every hour |
| The latest SMART self-test of a disk failed | error | every hour |
| The last run of an enabled scheduled task failed | warning | every 5 minutes |
| An enabled task missed a run: its time passed 10 minutes ago, and it did not run | warning | every 5 minutes |

A problem is alerted once, not at every check, and stays known while it
lasts: administrators see it in the health banner above every page, in the
language of the page, with errors first. When it is gone (the pool is mounted
again, the task ran), the banner drops it and UNOS sends nothing; the banner
goes once no problem is left. A problem that comes back is alerted again. UNOS
keeps the problems in memory, not on the system disk: after a restart of the
supervisor (an update, a reboot) it alerts each problem that is still there
once more. The RAID monitor also alerts each rise of a pool's device error
counts, unless the problem that the rise causes is alerted at the same check,
and once when it cannot read the state of a managed pool. For a pool that UNOS
does not manage, it alerts the changes of its health instead: a status that is
no longer healthy, a filesystem made read-only.

The use of the volumes' quotas is read from the kernel's own counters, which
reads no disk; the volumes are listed again only after something wrote to the
pool. UNOS does not watch a volume of a pool with quotas off.

A disk that sleeps is not woken for the check and keeps what UNOS knew of it,
unless another disk is at its place. Runs are not made up after the supervisor
was stopped: a task whose time came while it was stopped is reported as
missed; run it from **System** → **Scheduler**, or it runs at its next time.
A task misses no run before it was created, enabled again or given another
time, and neither before a change of the host's time zone. A recovery sends
nothing.

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

Telegram, webhook, Discord and Slack messages leave from the supervisor's
container. When it cannot connect to the service at all, as on a network that
lets the service through over IPv6 only (the containers have no IPv6), UNOS
sends the same message once more from the host's own network with the host's
`curl`, after about 5 seconds; a message that fails both ways names both tries.
E-mail always leaves from the container.

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

### Notification Events

The form's **Notify on events** choices are stored, but they have no effect
yet: every enabled channel receives every alert, and no other event
(container status, temperatures, backups) sends a notification.

---

## Tips & Best Practices

### Security

1. ✅ Create the administrator account right after installing, with a strong password
2. ✅ Enable two-factor authentication (2FA), and keep its recovery codes
   somewhere safe; an administrator can require it for every account
   (**System** → **Security**)
3. ✅ Use strong, unique passwords, above all for accounts with SMB: SMB has
   no two-factor authentication, and its lock after 5 wrong passwords still
   lets anyone on the network try 5 passwords per account every 5 minutes.
   UNOS refuses common passwords for SMB, but not every password that is easy
   to guess
4. ✅ After a password change, update the saved password on every computer
   and app that connects over SMB: a computer with the old one fails to sign
   in again and again. **System** → **Logs**, source **Samba**, shows the
   computers whose sign-ins fail (see [Logs](#logs))
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
2. Review [Troubleshooting](INSTALL.md#troubleshooting); when the NAS does not
   boot or nobody can sign in, see [Recovery](RECOVERY.md)
3. Search [GitHub Issues](https://github.com/und3f1ned/unos/issues)
4. Ask in [Discussions](https://github.com/und3f1ned/unos/discussions)

---

## Glossary

| Term | Definition |
|------|------------|
| **BTRFS** | B-tree File System, a modern copy-on-write filesystem |
| **NFS** | Network File System, the file sharing protocol of Linux and other Unix systems |
| **Pool** | A collection of disks managed as a single storage unit |
| **RAID** | Redundant Array of Independent Disks |
| **SMB** | Server Message Block, Windows file sharing protocol |
| **Snapshot** | A point-in-time copy of filesystem state |
| **Scrub** | Process to check and repair data integrity |
| **SMART** | Self-Monitoring, Analysis and Reporting Technology |
