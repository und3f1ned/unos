---
status: superseded
superseded-by: ADR-0487
date: 2026-11-23
area: system
decided-by: owner
---

# ADR-0471: An SD card standby takes over by itself when the eMMC or the pool of the critical apps fails, and only an administrator returns from it

## Context

Superseded by ADR-0487: the card's partition table is the board's (GPT on the BPI-W3, whose U-Boot reads no MBR card), the syncs write nothing to the eMMC, Docker's data root is no condition and the standby also takes over when Docker's pool fails, and the BPI-F3 follows; every other decision stays as written here.

The owner asked on 2026-11-23 (in translation): "think through a service that, if the eMMC fails
or the pool goes down, moves the critical data to the SD card and runs from the card, containers
included". Asked with options, the owner decided: the switch is automatic (a failed eMMC boots the
standby, a failed pool moves the critical apps to the card by itself), every switch notifies and
shows a banner, and the way back is only at the owner's press; the critical data is UNOS's own (its
database, the secrets of `/etc/unos`, smb.conf) plus the configuration and databases of the apps
that the owner marks critical, not media or caches, within the card's space, which UNOS shows; the
card first serves the storage checks as a spare disk and then becomes the standby; the scope is ARM
boards with U-Boot whose system is on an eMMC beside an SD slot (the BPI-W3, the BPI-F3 and their
like), and elsewhere UNOS hides the feature and says why.

Measured on the dev device, a BPI-W3 (ADR-0376), on 2026-11-23: the RK3588's boot ROM took the SD
card's loader before the eMMC's (the board booted Armbian from the card while UGREEN's loader sat
on the eMMC, and Armbian's `ubootpart` named the card's partition as the one its boot script came
from); the system then moved to the eMMC (`/dev/mmcblk0p1`, ext4, with Docker's data root) and the
card was wiped (ADR-0470, which supersedes ADR-0225 and ADR-0228); the eMMC reports its wear
(`/sys/block/mmcblk0/device/life_time` 0x01 0x01, 0 to 10% of its life used, `pre_eol_info` 0x01,
normal; mmc-utils is installed); Docker's images take 6.3 GB, its build cache 2.8 GB, UNOS's data
volume 2.3 MB, the apps' folders on the pool 5.0 GB; the card holds 29.7 GB; the data pool is
btrfs on one hard disk. Measured on the owner's BPI-F3 the same day: its root is `/dev/mmcblk2p1`
on its eMMC (`mmcblk2`, type MMC, 14.6 GB, with boot0 and boot1), an SD card (`mmcblk0`, type SD,
14.8 GB) sits in its slot, and Docker's data root is `/mnt/pools/data/.docker` on its pool
(ADR-0372). Another entry, storage.free-mmc-as-data-disk (on its branch), makes free SD cards
offerable as data disks.

The first version of this ADR left four questions, settled the same day. The lead read the W3's
loader (`/usr/lib/linux-u-boot-vendor-armsom-w3/u-boot.itb`, Armbian's vendor U-Boot built on
2026-11-12, now on the eMMC). It has `mmc read`, `mmc write`, `env import`, `env export`, `test`,
`env exists`, `load`, `fatwrite` and `saveenv`, and no `setexpr`, `itest`, `bootcount`, `bootlimit`
or `wdt`, so UNOS builds no U-Boot of its own. The owner answered the other three:
- only the BPI-W3 for now, the F3 staying as it is;
- a planned repair of a pool moves the critical apps to the card first and brings them back after;
- the sync's interval is configurable: by default daily plus 10 minutes after a change, and per
  critical app down to every hour, with the writes it costs shown before it is saved.

## Decision

Four phases, each an entry of `feature-list.json`: `system.emmc-health-monitoring` (1),
`system.sd-standby-boot` (2), `system.sd-standby-critical-apps` (3), `system.sd-standby-failback`
(4). The code of phases 2 to 4 lives in a new package `supervisor/src/standby/`.

**Where it is offered.** `GET /api/v1/system/standby` answers each condition with its result, and
the System page's Standby tab shows the unmet ones as the reason and offers no action (on x86: "this
NAS does not boot through U-Boot"); the Apps page then offers no critical mark. The conditions: the
host runs Armbian (`/etc/armbian-release`) of a family that has a board profile in
`standby/profiles.py` (only the BPI-W3's RK3588 families, at the owner's word, measured in phase 2;
another only at the owner's word, after the drills passed on that board), with Armbian's loader package
(`/usr/lib/u-boot/platform_install.sh` with `write_uboot_platform`); the root filesystem is ext4 on
an eMMC (the disk of `/` is `mmcblk<N>` whose `device/type` is `MMC`), mounted with
`errors=remount-ro`; an SD card sold as 16 GB or larger (at least 14 GiB;
the F3's card has 14.8 GB) is in a slot (`device/type` `SD`) and holds no
pool and no mounted filesystem; Docker's data root is on the system disk (as ADR-0470 keeps
it on the dev device), since a data root on a pool stops UNOS itself with the pool. The BPI-F3 keeps
its data root on its pool (ADR-0372), so its Standby tab names that and its missing profile; the host has rsync and dosfstools, which `deploy/install.sh` installs. A profile names the
board's U-Boot device numbers of the eMMC and the card (from the device tree's `mmc0` and `mmc1`
aliases, which U-Boot follows), where the first partition may start, a free sector before it for
the tries counter, and the variables of Armbian's boot script that the chooser sets. Every endpoint
of the standby, its `GET` included, is for administrators only (`supervisor/tests/test_authz.py`).

**eMMC health (phase 1; every host whose system disk is an eMMC).** The wear comes from
`mmc extcsd read /dev/mmcblk<N>` on the host (`deploy/install.sh` installs mmc-utils) at the
supervisor's start and once a day: life-time estimates A and B and the pre-EOL state. Without
mmc-utils it comes from sysfs `life_time` and `pre_eol_info`, which the kernel reads only when it
initialises the eMMC, so the Health tab says "as of boot". At each pass of the RAID monitor (300 s)
the supervisor reads the kernel's I/O errors of the eMMC from the journal, ext4's `errors_count` of
the root (`/sys/fs/ext4/<partition>/`) against its value at the supervisor's start, and the root's
superblock options in `/proc/1/mountinfo`, so that the read-only mount namespaces of ADR-0221 do not
count. Problems of the source `system_disk` (ADR-0332):
- `emmc_worn`: a warning at an estimate of 0x08 (70 to 80% used) or pre-EOL 0x02, an error at 0x0A
  or pre-EOL 0x03; the error is alerted again through `Problem.stage`, as the volume quota's stages
  are (ADR-0387).
- `system_disk_errors`: a warning for I/O errors or new ext4 errors.
- `system_disk_failed`: an error when the root's superblock is read-only and ext4 counted a new
  error (Armbian mounts the root with `errors=remount-ro`), or when the kernel logged I/O errors of
  the eMMC and a direct read (`O_DIRECT`) of the root partition's first MiB fails. A root remounted
  read-only by hand, without a new ext4 error, is none.

The Health tab shows a System disk card; `GET /api/v1/health/system-disk` answers it. The alert must
not depend on a write to the failing disk. The I/O-error branch of `system_disk_failed` cannot be
caused safely on a device, so the backend tests alone show it.

**The card (phase 2).** "Set up the standby", after the administrator types the card's model,
wipes the card and writes: the board's loader, with Armbian's `write_uboot_platform` from the
host's `linux-u-boot` package (again only when that package's version changes); partition 1 from
16 MiB, 512 MiB of FAT32 labelled `UNOS-BOOT`, for what U-Boot reads; partition 2, the rest, btrfs
labelled `UNOS-STANDBY`, mounted `noatime,compress=zstd:1`. UNOS's fstab lines, with `nofail`, mount
partition 1 at `/mnt/.unos-standby/boot` and the btrfs top level at `/mnt/.unos-standby/card`,
on the eMMC's system and on the standby alike. Both mountpoints are immutable while nothing is
mounted on them, as a pool's are (ADR-0034), so nothing lands on the system disk while the card is
missing. The lists of disks, pools, data disks, off-device copy targets and Files leave out a card
with these labels and that folder. "Remove the standby" (typed confirmation) wipes the card and its
loader area, and frees it for the data-disk offering.

**What a generation holds.** The system's generation is the btrfs subvolume `@sys-<n>`. It holds a
copy of the eMMC's root filesystem: `rsync -aHAX --delete` of `/` on that filesystem only. The
copy leaves out `/var/lib/docker`, `/var/lib/containerd`, `/var/log*`, `/var/cache`, `/var/tmp`,
`/var/lib/apt/lists`, `/var/lib/unos/backups`, swap files, `/var/lib/unos-standby` and the fix-ups,
and rsync neither copies nor deletes what it leaves out. So the host's accounts, Samba's passdb,
smb.conf, the network, the firewall and `/etc/unos` come along without a list of files to keep
current. It also holds:
- UNOS's compose volumes (`unos-data`, whose database is copied by SQLite's backup API into the
  supervisor's `/tmp` first, `served-certificate`, `app-names`, `caddy-data`, `caddy-config`) under
  `/var/lib/unos-standby/volumes/`;
- `docker save` archives (gzip level 1) of the images that UNOS's containers run, saved under the
  image names of the compose file, under `/var/lib/unos-standby/images/`, rewritten only when an
  image id changes;
- the fix-ups, never copied in either direction:
  - `/etc/fstab`: the eMMC's lines (its root and any `/boot` or swap partition of it) give way to the
    card's root line, and the eMMC's own lines are kept in `/var/lib/unos-standby/emmc-fstab` for
    the return;
  - `unos-standby.service` with its script;
  - `unos.service` of `deploy/install.sh` masked, since it would run `docker compose up` and build
    the images on the card;
  - the immutable attribute on the pools' mountpoints, which rsync does not carry.

The boot folder `unos/boot-<n>/` on partition 1 holds what the eMMC's boot script loads from its
`/boot`: the kernel, the initrd, the board's device tree and overlays, and `armbianEnv.txt`. FAT
holds no links, so the folder holds the files that Armbian's links point to. Its `armbianEnv.txt`
names the card's btrfs (`rootdev`, `rootfstype=btrfs`). A generation whose `/boot` did not change
reuses the previous folder.

**A sync** first compares (an rsync dry run, the image ids) and, when nothing changed, writes
nothing, not even a snapshot. Otherwise it makes `@sys-<n+1>` a snapshot of `@sys-<n>` and writes
only the changed blocks (`rsync --inplace --no-whole-file`) and the boot folder if needed. It then
switches the generation: it writes `unos/next.txt` (`unos_gen`, `unos_subvolid`, `unos_bootdir`),
renames `unos/current.txt` to `unos/previous.txt` and `unos/next.txt` to `unos/current.txt`. Only
then does it delete `@sys-<n-1>`, so the generation that `previous.txt` names lives until the next
switch. Any rsync status other than 0, a partial transfer from a failing eMMC included, fails the
sync before the switch. A sync cut off leaves the previous complete generation in use, and the
switch from the eMMC (below) kills a sync that runs.

The system's copy is synced daily at the time that the Standby tab sets (04:00 host time by
default), 10 minutes after the last audited change (each change restarts the wait), after a start
of the supervisor whose images differ from the card's, and at "Sync now". Each critical app has its
own interval, kept in `standby_apps`: 1, 2, 4, 6, 12 or 24 hours, 24 by default, at the system's
daily sync. Before an interval is saved, the dialog shows the writes to the card a week that it
costs (ADR-0214): the app's average MiB a sync over its recent syncs times the syncs a week, or,
before its first sync, that it cannot tell yet. A part skipped for a sleeping pool is synced at the first pass of the RAID monitor
that finds the pool's disks awake. Its result is kept in RAM and in the card's `state.json` on the
btrfs top level, written only when it changes, and logged only when the sync wrote or failed. The
result holds the time, the MiB written to the card (from `/sys/block/<card>/stat` after a `sync`),
the parts that changed and an error. Problems of the source `standby`:
- `standby_sync_failed`: a warning;
- `standby_copy_old`: a warning when the system's copy or a critical app's copy had no complete sync
  for 3 days.

**The boot choice.** The card's `boot.scr` is a U-Boot script image that UNOS writes itself (the
legacy header and its CRCs, without u-boot-tools). At arming it gets the board's device numbers and
the eMMC's partition and path of its own boot script. It imports `unos/current.txt`, or
`unos/previous.txt` when that one is missing or does not import, and `unos/drill.txt` when present
(`env import`). It then takes the first case that applies, and passes its reason as
`unos.reason=<case>` on the kernel's command line:
1. `unos/emmc-failed` exists: the card's root.
2. The tries counter (below) holds 3: the card's root.
3. The eMMC's own boot script loads: it is handed over. The script first steps the tries counter,
   then sets `devtype`, `devnum`, `distro_bootpart` and `prefix` as U-Boot's standard boot
   would for the eMMC. It puts `unos.chooser=card unos.root=emmc panic=10` into the variable that
   Armbian's boot script appends to the kernel's command line (`extraboardargs` per the profile),
   and sources the eMMC's script. The eMMC then boots with its own kernel, initrd and overlays.
   `panic=10` makes an initramfs that finds no root reboot, so the next boot counts again.
4. Otherwise: the card's root. The script sources `unos/boot-<gen>/boot.scr` the same way, with
   `unos.chooser=card unos.root=card rootflags=subvolid=<id>`.

The tries counter is the text `unos_tries=<n>`, ended by a NUL byte, in the free sector before
partition 1 that the profile names. The W3's loader has no arithmetic and no `bootcount`. So the
script reads the sector into memory (`mmc read`) and imports it (`env import -t`). It steps the
counter with unrolled `test` branches: missing or 0 becomes 1, 1 becomes 2, 2 becomes 3. Then it
exports the variable (`env export -t`) and writes it back (`mmc write`). It writes neither the FAT
partition (`fatwrite`, which a cut-off write could leave unreadable) nor U-Boot's own environment
(`saveenv`). A board's profile lists the loader's commands that the chooser needs: `mmc read`,
`mmc write`, `env import`, `env export`, `test` and `load`.

The card's root makes every later boot stay on the card: its `unos-standby.service` writes
`unos/emmc-failed` at once, with the reason from `unos.reason`, unless the file exists. It also
deletes `unos/drill.txt`, so that a drill acts once. A passing eMMC fault therefore never sends the
NAS back by itself.

On the eMMC's system, `unos-standby-boot-ok.service` sets the counter to 0 once the system reached
multi-user, one write to the card per boot. Phase 2 also turns on systemd's hardware watchdog
(`RuntimeWatchdogSec=60s` in a drop-in) where the board has one, so that a hung kernel reboots and
is counted. The eMMC's own loader is never written. With the card missing or unreadable, the boot ROM
takes the eMMC's loader, which boots the eMMC as if there were no standby. The ROM order was
measured only with UGREEN's loader on the eMMC, and U-Boot's Rockchip defaults also try the SD card
before the eMMC, so nothing assumes it. Each start of the supervisor reads `/proc/cmdline` and
raises one of two errors:
- `standby_card_missing` when the card is gone;
- `standby_not_in_boot_path` when the card is there, the standby is armed and `unos.chooser=card` is
  missing.

**The switch from the eMMC (phase 2).** When the standby is armed, in this boot's path and holds a
complete generation, `system_disk_failed` makes the supervisor do the following in the same pass.
Its code is imported at its start, so nothing is read from the eMMC at the switch.
1. Kill a sync that runs.
2. Write `unos/emmc-failed` (reason and time) and `fsync` it.
3. Send the switch's alert.
4. Copy the database by SQLite's backup API into a new file of the current generation, in a thread
   of its own that may block on the eMMC. When it ends within 60 s and `PRAGMA quick_check` passes,
   the new file is renamed over the copy; otherwise the synced copy stays.
5. Reboot at once through `/proc/sysrq-trigger` (`s`, then `b` 5 s later). An orderly shutdown
   could hang on the failing eMMC or stop the supervisor before it reboots, and the pools' btrfs
   and the apps' SQLite take this reboot as a power cut.

Without a ready standby the failure is only alerted.

**Running from the card.** `unos-standby.service` first loads the archives that the card's Docker
lacks and, when this generation's volumes were not seeded yet, creates the compose project's
volumes (`docker compose create`) and fills them from the copy. Then it runs
`docker compose up -d --no-build` and records `standby_ran` (time, generation, reason) in
`state.json`. The supervisor sees `unos.root=card`:
- `running_on_standby` (error: "UNOS runs from the SD standby since <time>: <reason>") is alerted and
  stays on the banner;
- no sync runs, and app installs, updates and uninstalls answer 409;
- the pools mount from the copied fstab.

The drills measure the time from the reboot until UNOS answers; the target is 10 minutes. The first
sync after a return empties the card's Docker data root; the archives stay.

**A standby that ran.** While `standby_ran` is set, no sync runs: the card holds changes that the
eMMC lacks. The eMMC's system shows the warning `standby_has_changes` and offers "Return through
the standby", which writes the mark with the reason drill and reboots to the card, where phase 4's
return copies the changes back. It also offers "Discard the standby's changes" (typed confirmation),
after which a sync writes a new generation. Until phase 4, the standby's "Return to the eMMC" only
deletes `unos/emmc-failed` and `unos/drill.txt`, sets the tries counter to 0 and reboots. The
drills are kinds of `POST /api/v1/system/standby/drill`:
- `mark` writes `unos/emmc-failed` with the reason drill;
- `emmc_unreadable` writes `unos/drill.txt` with an eMMC partition that does not exist;
- `emmc_root_missing` writes `unos/drill.txt` with a kernel `root=` of a UUID that does not exist,
  which the kernel takes over the eMMC's own, since it uses the last one.

Each of them then reboots.

**Critical apps (phase 3).**
- **Marking.** An app's menu on the Apps page marks it critical, in a new table `standby_apps`
  (Alembic migration, ADR-0444). The dialog lists what is copied, the app's folders under
  `{pool}/apps/<app>/`, and what is not: its other pool folders (media, downloads, shares), its
  caches (a catalog `VolumeMapping` gets `cache=True`) and the `.previous` copies of ADR-0408. It
  shows their sizes, its images' size and the card's space, and sets its sync interval (above). The
  mark is refused (409) when the standby would leave less than the reserve: 10% of the card, at
  least 2 GiB.
- **The sync of an app.** It passes over a pool whose disks sleep (`spindown.pool_sleeps`, ADR-0378).
  It copies from a read-only snapshot `.unos-standby-snapshot` of the subvolume that holds
  `{pool}/apps/<app>`, consistent while the app runs, into a new generation `@app-<app>-<n>`, made
  the same way. `state.json` then names that generation, and the old generation and the snapshot are
  deleted. The generation also holds the archives of its images and each container's definition
  (Docker's inspect: image id, Config, HostConfig, networks) with its networks.
- **On the standby.** The supervisor loads the critical apps' archives and creates their networks
  and containers from the definitions, companions first (ADR-0381). An app that `state.json`
  records as failed over gets the card's copy. Any other app keeps its pool binds when its pool is
  mounted and healthy, and fails over as below otherwise. The other apps are not there, and the
  Apps page says so.
- **The pool failover** applies to a pool that holds a critical app's data. It starts when btrfs
  forced the pool read-only (at that pass), or at the second pass in a row (5 to 10 minutes) that
  finds one of two states. Either the pool is managed (its fstab entry is UNOS's, ADR-0034) and not
  mounted, or it misses more devices than its data profile tolerates (any device for single or
  raid0, as on the W3's one-disk pool). An unmount through UNOS drops the fstab entry, and it is
  refused while containers use the pool, so a deliberate unmount never fails over. For each
  critical app of the pool, UNOS:
  1. copies once more, within 5 minutes, when the pool is forced read-only but still readable;
  2. saves the definitions of its current containers anew, leaving out ADR-0408's previous
     containers;
  3. stops and removes those containers;
  4. creates them again with the binds under `{pool}/apps/<app>/` on the card's copy, a cache on a
     tmpfs of 256 MiB, and every other pool bind on an empty read-only folder of the card, so that
     nothing lands on the system disk (ADR-0381);
  5. starts those that were running.

  `state.json` records the failover, and the error `apps_on_standby` names the apps and the
  copy's age. An app with no copy yet stays stopped and is named. While failed over, these apps
  get no sync, and their update, uninstall and reinstall answer 409. A degraded pool whose profile
  still covers the missing devices, mounted read-write, does not fail over. Before it serves, the
  supervisor of either system applies the failovers that `state.json` records: a pool-bound
  container of such an app, which that system's Docker may have restarted at boot, is stopped and
  created again on the card's copy.
- **Drill.** "Move the critical apps to the card" on the Standby tab does the same with the reason
  drill. Until phase 4, "Return to the pool, discarding the card's changes" (typed confirmation)
  recreates the pool-bound containers.
- **Planned repair.** The Storage page may refuse to unmount or remove a pool because containers use
  it (409). When critical apps are among them, the dialog offers to move those apps to the card
  first. The move is the failover above with the reason planned, alerted and on the banner as
  every switch. The unmount or remove then goes on when no other container blocks it, and
  otherwise names those that still do. Once the repaired pool is mounted again under its name, the
  Standby tab offers to bring the apps back: in phase 3 by the discarding return, from phase 4 by
  the pool return below.

**Return (phase 4).**
- **Pool.** The return is offered once the pool is mounted read-write and healthy, and asked with
  the MiB that the copy back writes (a dry run). UNOS stops the card-bound containers and copies
  each app's card copy into `{pool}/apps/<app>/` with `rsync --delete --backup
  --backup-dir={pool}/apps/<app>.before-failback-<time>`. The copy leaves out what the sync left out
  (caches and the `.previous` copies), which it therefore neither copies nor deletes. Every file it
  replaces or deletes is kept in the backup folder, the pool's own changes since the last sync among
  them. It then recreates the pool-bound containers from the definitions, starts those that were
  running and clears the failover. A failure leaves the apps stopped and the card's copy untouched,
  names the step, and the return can be repeated.
- **eMMC.** On the standby, "Return to the eMMC" is offered when the eMMC reads and its root passes
  `fsck.ext4 -n`. Otherwise the tab says that the card stays the system disk and no standby is
  left. The return runs as a transient unit (`systemd-run`, a script the supervisor writes to
  `/run`):
  1. stop Docker and Samba;
  2. copy the standby's root into the eMMC's root and the compose volumes into the eMMC's Docker
     volumes. The copy is the sync's rsync the other way, with the same exclusions and without the
     fix-ups. `/etc/fstab` is the standby's with its card root line replaced by the eMMC's lines of
     `/var/lib/unos-standby/emmc-fstab`, so that a pool added on the card comes along;
  3. delete `unos/emmc-failed`, `unos/drill.txt` and `standby_ran`, and set the tries counter to 0;
  4. reboot.

  Apps still failed over stay so until the pool return.

**Throughout.** The new actions and reads are for administrators only (ADR-0030). They live under
`/api/v1/system/standby` (`GET`; `POST` arm, sync, drill, return, discard; `DELETE` remove),
`GET /api/v1/health/system-disk` and `PUT /api/v1/apps/installed/{id}/critical`. Every text is in
English and Russian (ADR-0287). Every action is audited (ADR-0402), the automatic switches by the
system, and every switch and every problem above reaches every enabled channel and the banner
(ADR-0332). The Standby tab shows:
- the card (model, size);
- the space by part: the system copy, UNOS's images and volumes, each critical app's data and
  images, the standby's loaded images, the free space and the reserve;
- the last sync (time, MiB, result), the generation, and whether the last boot passed the card's
  script.

Nothing is written to the eMMC while UNOS idles (ADR-0214): the reads above write nothing, and the
standby's state is in RAM and on the card. Measured with `scripts/dev/sd.sh`, an idle hour adds to
the eMMC no more than the host's own baseline, about 16 KiB a minute per ADR-0214, and nothing to
the card. `docs/RECOVERY.md` says how to mark the eMMC failed from any computer: create
`unos/emmc-failed` on the card's FAT partition.

**What changed from the first design.**
- The card's copy is btrfs generations with one switch, not files rewritten in place: a sync cut off
  by the failure it guards against would leave a torn copy.
- The chooser hands the eMMC over to its own boot script, and the card's root boots through a copy
  of it, so the kernel always matches its modules and overlays. A kernel argument proves at every
  boot that the card's script ran, since the measured ROM order may not hold beside a U-Boot on the
  eMMC.
- Wear comes from EXT_CSD daily: the sysfs values date from the boot.
- The failure is a read-only root with a new ext4 error, or I/O errors with a failing direct read.
  An I/O error alone, or a remount by hand, must not reboot the NAS.
- Boots of the eMMC that never come up are counted in a raw sector of the card, and the hardware
  watchdog reboots a hung kernel. The first choice of the card's root is made permanent at once.
  Without both, a hang or a passing fault would leave the NAS on the eMMC, or send it back from the
  card by itself.
- A degraded pool whose redundancy still covers its missing disks does not fail over: it still
  serves the apps, and moving them would trade it for the slower card and two diverging copies.
  A pool that lost more than its profile tolerates does fail over, even while still mounted.
- The standby's system is a copy of the eMMC's root, not a fresh install, and the critical data
  includes UNOS's compose volumes: the JWT secret, Caddy's data and the accounts behind smb.conf
  live outside the database.
- Images travel as archives, loaded on the standby: the card's Docker cannot be written while the
  eMMC's system runs without a second daemon.
- A standby that ran is never overwritten, and the pool return keeps the files it replaces.
- A sync that finds nothing writes nothing and leaves sleeping disks asleep.
- A last copy of the database is made before the switch.

## Consequences

- **Limits.** The card is slower and wears faster than the eMMC: critical apps run slower from it,
  and an A2 card is advised. Media and caches do not fit and are not copied: an app runs from the
  card without them. The standby is no backup: it sits in the same device and copies a mistake at
  the next sync, so the off-device copies stay. Changes made between the last sync and an eMMC
  failure are lost, except the database's best-effort last copy.
- **Space on the BPI-W3.** Even with every app marked critical (images 6.3 GB as archives plus 6.3 GB
  loaded on a run of the standby, apps' folders at most 5.0 GB, without the 2.8 GB build cache),
  the card's 29.7 GB less the reserve holds it all if the root's copy stays under about 8 GB.
  Phase 2 measures the root's copy and records it.
- **Gaps.** Some cases leave the owner the manual mark: creating `unos/emmc-failed` on the card's
  FAT partition from any computer (`docs/RECOVERY.md`). They are a system that hangs while its kernel
  still serves the watchdog, a board without a hardware watchdog, and a failure that takes the
  supervisor down before it acts. Docker may restart a failed-over app's old pool-bound container at boot, for
  the moments until the supervisor applies the failover; the pool's return keeps whatever that
  container changed in the backup folder.
- **Dev device.** ADR-0470 leaves the dev device's card wiped and unused; phase 2's device check
  is what puts it to use, and AGENTS.md's hard rule on the device then names the standby. The drills reboot the device, write its eMMC (the ext4 error drill, the
  return) and wipe the card, so they are owner actions (ADR-0211). Arming takes the card from the
  storage checks, which need another spare disk from then on, as the pool drills need a USB stick
  that holds a test pool, never the data pool.
- **New host packages:** rsync, dosfstools and mmc-utils in `deploy/install.sh` and
  `docs/INSTALL.md`.

## Considered options

- A second Docker daemon on the card's data root, so that images are loaded ahead: two daemons on
  one host, its containerd namespaces and storage drivers, for minutes saved at a rare switch.
- A fresh minimal system on the card (debootstrap): it needs the network at setup and a list of the
  host files that UNOS changes, which would go stale.
- The chooser in the eMMC's boot script: Armbian's packages own and rewrite that script, it means
  writes to the eMMC's boot files, and it does not run when the eMMC is dead.
- The card as the system and the eMMC as the standby: the eMMC is faster and barely worn (0 to 10%).
