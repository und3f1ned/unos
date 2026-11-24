---
status: accepted
date: 2026-11-23
area: system
decided-by: agent
supersedes: [ADR-0471, ADR-0481]
---

# ADR-0487: The SD standby's card has the partition table its board's U-Boot reads, its syncs write nothing to the eMMC, and it also takes over when Docker's pool fails

## Context

The first device check of `system.sd-standby-boot` on the BPI-W3 (2026-11-23) found that the card's
chooser never ran: after Set up, two reboots, the second with `unos/emmc-failed` on the card, booted
the eMMC directly, without `unos.*` on the kernel's command line. It also found seven defects
(below). ADR-0486 then put Docker's and containerd's data on a pool and asked the standby to take
over when that pool fails. The owner decided the same day: "Yes, for F3 too" (in translation), the
standby is to be built for the BPI-F3 too once it works on the W3. This ADR keeps every decision of
ADR-0471 and ADR-0481 that it does not change below; their texts stay the description of those
decisions, phases 3 and 4 of ADR-0471 included.

Measured on the W3 without a serial console, by U-Boot scripts that put what they found on the
kernel's command line through Armbian's `${extraboardargs}`:
- The loader is Armbian's vendor U-Boot 2017.09 (Radxa's next-dev; package
  `linux-u-boot-armsom-w3-vendor`, folder `/usr/lib/linux-u-boot-vendor-armsom-w3`). Its built-in
  environment, read from the package's `u-boot.itb`, runs `distro_bootcmd` over
  `boot_targets=usb0 mmc1 nvme0 nvme1 mmc0 ...` with `boot_scripts=boot.scr.uimg boot.scr`; its
  device tree names the eMMC `mmc0` and the card `mmc1`, both enabled, and its SPL's
  `u-boot,spl-boot-order` takes the card first. Whichever loader the boot ROM takes, the same U-Boot
  scans the card before the eMMC: the ROM's order does not decide the boot.
- A probe at `/boot/boot.scr.uimg` on the eMMC, which distro boot runs before `/boot/boot.scr`, found
  that `mmc dev 1`, `mmc read` of the counter sector and `rkimgtest mmc 1` work, but `part list mmc 1`
  is empty, also after `mmc rescan`, so `fstype` and `load` of `mmc 1:1` fail, while `part list mmc 0`
  (the eMMC's GPT) lists 1. This U-Boot reads no partition of a card with an MBR, the
  `label: dos` of ADR-0481: the cause.
- Set up wrote 32.4 MiB to the eMMC: rsync's reads set the access times of 49,725 files of the
  relatime root.

## Decision

**The card's partition table is the profile's** (`partition_table` in
`supervisor/src/standby/profiles.py`, rendered by `card.partition_layout`): GPT on the BPI-W3, as
Armbian's own images for that board (`UBOOT_PARTITION_TYPE=gpt`), partition 1 of type Microsoft
basic data (which every computer opens, for the mark by hand of `docs/RECOVERY.md`) with the
attribute LegacyBIOSBootable, partition 2 a Linux filesystem. The BPI-F3's card needs GPT too, with
its loader in named partitions and a boot header that UNOS generates (the F3's own study): its
builder adds that layout to the profile. `dos` stays for a board whose loader needs an MBR. The
chooser stays the card's `boot.scr`, which U-Boot's own scan of the card runs before the eMMC's. A
board whose U-Boot scans the eMMC first would need a UNOS hook `/boot/boot.scr.uimg` on the eMMC
that sources the card's chooser: measured on the W3, it runs before Armbian's `/boot/boot.scr` and
falls through to it when it returns. The W3 does not need it, and nothing of UNOS is in the eMMC's
boot path.

**Board-neutral fields of the profile**, for the BPI-F3 to come (the W3's values change nothing):
- `emmc_boot`: how the eMMC's system boots and so how the chooser hands over. `boot.scr` is Armbian's
  script with `${prefix}` and `args_variable`, the W3's, and the only kind built; extlinux comes with
  the board that needs it. Set up's check of the eMMC's script applies to `boot.scr`.
- `buffer_variable`: the U-Boot variable of the address where the chooser loads its small files and
  the counter sector, `kernel_addr_r` on the W3; a board whose U-Boot holds the chooser there names
  another.
- The loader is written by UNOS from the profile's images at their sectors (those of Armbian's
  `write_uboot_platform`, which is no longer run; `card.write_loader`): the first stage's first
  sector is zeroed, the later stages are written, then the first stage without its first sector,
  and that sector last, so that a rewrite cut off at any point leaves no valid header in front of a
  broken stage and the boot ROM passes over the card. The loader package's name and version come
  from dpkg (`dpkg-query -S` on the loader's image, then `-W`), not from the folder's name.

**Docker's data on a pool (ADR-0486).**
- Docker's data root is no condition of the standby.
- The generation copies UNOS's compose volumes from where Docker keeps them, the pool's
  `.docker/volumes`, and nothing else of the pool; the root's copy stays on the root filesystem.
- The root's copy leaves out `/etc/docker/daemon.json`, `/etc/containerd/config.toml` and the drop-ins
  of `docker.service.d` and `containerd.service.d` that hold `RequiresMountsFor=`. The fix-ups write the
  card's `daemon.json` with `data-root` `/var/lib/docker` and its `config.toml` with the top-level
  `root` `/var/lib/containerd`, both on the card's root, and leave those drop-ins out, so that Docker
  and UNOS start on the card without the pool.
- On the eMMC's system, `unos-standby-watch.service` (`ConditionKernelCommandLine=unos.root=emmc`; its
  script `supervisor/src/standby/host/unos-standby-watch.sh`, written and enabled by Set up, so in
  force from the next boot, written again by a sync when it differs, stopped and removed by Remove)
  checks every 60 s the filesystem of Docker's data root when `daemon.json` puts that root below the
  pools' folder. When the root filesystem holds it (the pool is not mounted), it first mounts the
  pool from its fstab line and, when that works, starts Docker: a disk that came up after systemd
  gave up on its mount is no failure. When the pool stays unmounted or its filesystem is read-only
  (btrfs forced it so, or it lost its disk) at 5 checks in a row, and the card's btrfs is mounted
  with the current generation's subvolume and holds no mark, it writes `unos/emmc-failed` with
  `reason=docker_pool_failed` and reboots, and the chooser boots the card's root. Docker, and with it
  UNOS, is down then, so only a host unit can act. It writes nothing while the pool is fine.
- On the card's root after the mark, the boot's reason in the API and on the Standby tab is the
  mark's: `drill`, `system_disk_failed`, `docker_pool_failed`, or the chooser's of the card's first
  boot.

**The defects of the device check, as fixed.**
- The daily sync was due at the first pass after Set up and after every start past its time, its
  done-today flag only in RAM. Now it is due once its time passed today and no sync, whatever
  started it, ran since then, by the last sync's time in RAM or in `state.json` and the day's slot
  started in RAM.
- Sync now's own audit entry, in `unos-data`, made every next sync write a generation, and so did
  what the root rewrites at every boot (measured on the W3). These now follow: a change of
  `audit/` of `unos-data`, or of the root's `etc/fake-hwclock.data`, `etc/armbian-leds.conf`,
  `etc/logrotate.conf`, `etc/logrotate.d/`, `etc/systemd/journald.conf`, `etc/armbianmonitor/`,
  `var/lib/alsa/` or `var/lib/nfs/`, alone writes no generation, and they are copied with any other
  change; so do `smart_history.json` of `unos-data`, to which the SMART monitor adds a reading at every
  start of the supervisor, and Caddy's `caddy/autosave.json` in `caddy-config`, which Caddy writes at
  every start.
  Left out altogether, as runtime state that the card's system makes anew:
  `/var/lib/systemd/pstore/*`, `/var/lib/apt/daily_lock`, `/var/lock/*`, `/root/.docker/buildx` and
  `/root/.docker/.token_seed`. Folders' times inside UNOS's volumes are neither compared nor copied
  (the database's copy and its digest change the time of the copy's folder), and the eMMC's own units
  of the standby are not copied. A sync's record names the changes of the volumes too.
- Found by the drills: a discard failed with rsync's exit status 11, as its forced sync wrote a third
  boot folder beside the current and the previous one, and with all of `/boot/dtb` (every Rockchip
  board's device trees, 118 MiB) each folder took 179 MiB of the 512 MiB FAT partition. A boot folder
  now holds of `dtb/` only the board's device tree (`fdtfile` of `armbianEnv.txt`) and the overlays
  beside it, which Armbian's boot script loads (65 MB on the W3), and a forced sync (Set up, the
  discard) keeps the current boot folder when `/boot` did not change.
- Found by the drills: the card's root starts on the clock that fake-hwclock saved at the last sync,
  and the run was recorded up to 30 minutes early. The run is still recorded at once, so that the
  eMMC's system knows of it, and its time is corrected once `/run/systemd/timesync/synchronized`
  exists (within an hour): now less the time since the record.
- `standby_not_in_boot_path` was alerted right after Set up. It is found, and `in_boot_path` is
  false, only in a boot after the one in which the standby was set up (the kernel's
  `/proc/sys/kernel/random/boot_id` against `state.json`'s `armed_boot_id`: the clock, which Armbian
  restores from fake-hwclock early in a boot, could not tell). Before it, `in_boot_path` is null and
  the Standby tab says the chooser takes part from the next boot.
- The loader package's name was derived from its folder and was wrong: it comes from dpkg (above).
- Set up wrote 32.4 MiB to the eMMC. The sync's unit, which runs rsync, `docker save` and
  `dpkg-query -S` (which reads every package's file list), now runs with `ProtectSystem=strict` and
  `ReadWritePaths=-/mnt/.unos-standby`: it sees the host read-only but the card, so its reads set no
  access time on the eMMC and none of its commands can write the eMMC. Chosen over rsync's
  `--open-noatime` (the W3's rsync 3.5.0 has it), which would leave the folders' access times. The few
  files that the supervisor reads itself (fstab, armbianEnv.txt, Docker's settings, the units) still
  get at most one access time a day.
- Remove left `/mnt/.unos-standby` and `/etc/systemd/system.conf.d`. It now removes those and
  `/usr/local/lib/unos-standby` when they are empty.
- The supervisor's start logged the card's btrfs (labelled `UNOS-STANDBY`) as a pool that is not
  mounted, to be mounted on the Storage page: the labels of the standby are no pool there
  (`supervisor/src/storage/pools.py`).

**The BPI-F3** gets the standby, through these profile fields, after the W3's drills pass: the
ledger entry `system.sd-standby-bpi-f3`. This replaces ADR-0471's "only the BPI-W3 for now".

## Consequences

- Measured on the W3 (2026-11-23 to 24): Set up wrote 0.605 MiB to the eMMC (32.4 before) and
  1733 MiB to the card; every boot passed the chooser; each drill (the mark, an unreadable eMMC
  partition, an ext4 error, a missing root) booted the card's root, UNOS answered 95 to 108 s after a
  drill's call (237 s after three failed boots of a missing root; 319 s after the write of the ext4
  error, whose switch came within 4 minutes), wrote nothing to the eMMC from the card, and returned;
  a sync that finds nothing writes nothing to either disk, the start sync after a reboot finds
  nothing, and an idle hour wrote 0.355 MiB to the eMMC and nothing to the card.

- A W3 card set up before this change holds an MBR that its U-Boot cannot read: Remove and Set up
  again.
- The watch mounts again, within a minute, the pool that holds Docker when it was unmounted by hand,
  and starts Docker; one that cannot be mounted for 5 minutes reboots the NAS to the card. Stop
  `unos-standby-watch.service` before such work. The switch for a failed Docker pool sends no alert
  from the eMMC's system, since UNOS is down; the card's UNOS alerts `running_on_standby` with the
  mark's reason.
- The SysRq reboot of the switch from a failing eMMC loses what the last seconds wrote to the pools,
  as a power cut would: in the drill of an ext4 error the switch's audit entry and the supervisor's last log lines. The mark
  and the database's last copy, written and synced on the card first, are kept.
- A change of only a follower, such as a real edit of `/etc/logrotate.conf`, reaches the card with
  the next sync that has another change.
