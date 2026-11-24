---
status: superseded
superseded-by: ADR-0488
date: 2026-11-23
area: system
decided-by: agent
---

# ADR-0481: The SD standby boots through a chooser checked against the eMMC's boot script, syncs through a unit it can kill, and dry-runs every host action

## Context

Superseded by ADR-0488: the card's partition table is the board's (GPT on the BPI-W3, whose U-Boot reads no MBR card), the syncs write nothing to the eMMC, Docker's data root is no condition and the standby also takes over when Docker's pool fails, and the BPI-F3 follows; every other decision stays as written here.

ADR-0471 designs the SD card standby and its phase 2, `system.sd-standby-boot`: the card's layout,
the chooser's four cases and its tries counter, a generation's content, the schedule of the syncs,
the switch from a failing eMMC, the drills and the return. It leaves open the board profile's
values, the partition table, what the chooser does when the eMMC's script returns or no generation
exists, how a sync is killed, how the copy avoids the live mount points' changes, where the
standby's settings and its run are kept, and how the administrator sees what an action would do.
The owner delegated these choices ("do as much as possible without me", 2026-11-22), above all safe.

## Decision

**The profile and the card** (`supervisor/src/standby/profiles.py`, `card.py`).
- The BPI-W3's profile is keyed by Armbian's `BOARD=armsom-w3`: U-Boot's `mmc 0` is the eMMC and
  `mmc 1` the card, partition 1 starts at sector 32768, and the tries counter is sector 32767, the
  last before it. The condition `loader` reads the sizes of the package's `idbloader.img` (sector 64)
  and `u-boot.itb` (sector 16384) and refuses a loader that reaches the counter's sector.
- The card gets an MBR (`label: dos`): partition 1 of type `c`, bootable, partition 2 of type `83`.
  The FAT partition is mounted with `fmask=0133,dmask=0022,quiet`, so that a chmod there (that of
  `host.write_host_file_atomic`, rsync's of a new file) never fails; both fstab lines carry
  `nofail,x-systemd.device-timeout=10s`, so a missing card delays no boot.
- The loader package's version is kept on the card (`loader-version`); a sync writes the loader
  again when `dpkg-query` reports another, after the loader condition passed again. It also writes
  the chooser `boot.scr` and the eMMC's `unos-standby-boot-ok` service and script again when they
  differ from what this UNOS renders (compared by SHA-256 and text): each without a new generation.
- Set up refuses (409) unless the eMMC's `/boot/boot.scr` uses `${prefix}` and the profile's
  `${extraboardargs}` and `/boot/armbianEnv.txt` does not set that variable: the chooser's handover
  works only then. It also reads the disks again right before the wipe and refuses a card whose
  identity or state changed. It writes systemd's watchdog drop-in only where `/dev/watchdog0`
  exists, in force from the next boot.

**The chooser** (`bootscript.py`).
- Its buffer is `${kernel_addr_r}`, which every Armbian boot script needs; the scripts it sources
  load at `${scriptaddr}`. Each `env import` names the variables it takes (`unos_gen`,
  `unos_subvolid`, `unos_bootdir`; a drill's `unos_emmc_part` and `unos_drill_args`; `unos_tries`).
  U-Boot deletes a named variable that the file lacks, so the eMMC's partition is set again after
  the drill's file when it is empty; the tests' interpreter (`supervisor/tests/uboot_fake.py`) deletes
  them the same way.
- When the eMMC's own script returns, it did not boot the eMMC: the chooser then boots the card's
  root with the reason `emmc_boot_failed` (the counter was stepped already). Without any generation
  it echoes and returns, and the loader's standard boot goes on to the next boot device.
- The counter's text is U-Boot's `env export -t` form, `unos_tries=<n>`, a newline and a NUL, the
  rest of the sector NULs; `unos-standby-boot-ok.sh` writes the same with `dd conv=sync`.
- The drill `emmc_unreadable` names partition `7f` (hex, as U-Boot reads it; refused when the eMMC
  has a partition 127), `emmc_root_missing` the root `UUID=00000000-0000-4000-8000-0000756e6f73`.

**The sync** (`generation.py`, `service.py`).
- rsync and `docker save` run in the transient unit `unos-standby-sync.service`
  (`systemd-run --wait --pipe --collect`, `RuntimeMaxSec` its deadline), one at a time by its name;
  the switch ends a running one with `systemctl kill --signal=SIGKILL` in a thread it does not
  wait for, and a stop flag ends the sync before its next step.
- The mount points on the root filesystem (from `/proc/1/mountinfo` and the fstab), the swap
  files of `/proc/swaps` and Docker's data root as `docker info` names it are left out of the copy
  like ADR-0471's exclusions, and the fix-ups make the mount points in the generation (the pools'
  and the standby's immutable) and list them in `/var/lib/unos-standby/mountpoints`; before the next
  copy of the system, an immutable mount point of that list that is one no more (a pool removed) is
  freed (`chattr -i`), so that rsync can delete it. So the times of live mounts such as `/run` and
  `/dev` never count as a change.
- The copy of the root leaves out the host's own churn too: `/var/lib/systemd/timesync`,
  `/var/lib/systemd/random-seed`, `/var/lib/systemd/timers`, `/var/lib/logrotate` and
  `/var/lib/NetworkManager/timestamps`, which the card's system makes anew, and compares no
  directory's time (`--omit-dir-times`), which the fix-ups' files change in the copy. A sync's record
  keeps the first ten changes that rsync itemized, to tell what made it write. The boot files are
  compared and copied with their times (`rsync -rLt --modify-window=1`).
- The card's fstab leaves out every line of a filesystem of the eMMC (by its UUID, PARTUUID, LABEL
  or path, from lsblk), not only `/`, `/boot` and swap, so a dead eMMC stops no boot of the card.
- Nothing after the rename of `next.txt` to `current.txt` undoes the new generation: the removal of
  the old ones, which follows, only logs a failure; a sync stopped or failed before the rename
  deletes its partial generation. The database's staged copy leaves TMPDIR after each sync.
- The scheduled syncs take the actions' lock and wait while an action holds it; Set up marks the
  start's sync done before it reads the new standby, so its own sync is the only one.
- The database is compared by a SHA-256 of its SQL dump (`iterdump`) of a fresh backup in TMPDIR
  against the digest stored beside the card's copy; the fix-ups by a digest of what they render.
- A sync also runs at every start of the supervisor; it writes only when something differs, which
  covers ADR-0471's start whose images differ. A sync that finds nothing changed is kept in RAM
  only; `state.json` is written when a sync wrote or failed, or the first sync after a failure
  found nothing.
- The 10 minutes after a change follow every audit entry but sign-ins, sign-outs, the dashboard's
  layout, the theme and the standby's own actions.
- The daily time lives in the card's `state.json`, with the card's identity, the counter's sector,
  the last sync, the last complete sync and a run of the standby.

**The switch and the run** (`switch.py`, `host.py`).
- The mark and the database's last copy are written through the supervisor's own bind of `/mnt`
  (`host.write_mounted_file`), and the reboot is `host.reboot_at_once` (`s`, then `b` 5 s later, to
  `/proc/sysrq-trigger`): no program of the host runs. These steps run in a thread of their own
  (`switch.run`), started by the eMMC monitor's pass before it alerts its problems, so that neither
  an event loop nor worker threads held up by the failing eMMC delay them. The loop sends the
  switch's alert (at most 30 s) and its audit entry, by the user system (at most 5 s), once the mark
  is written; the thread waits for them at most 40 s, then copies the database and reboots.
- The supervisor records `standby_ran` in `state.json` at its first start on the card's root, since
  `unos-standby.sh` has no JSON tool; `unos-standby.service` only `Wants` Docker, so the mark is
  written even when Docker fails.
- After the standby ran, Return through the standby is the drill `mark`. The typed confirmations of
  Set up, Discard and Remove are the card's model. Remove with the card gone drops UNOS's lines only,
  and a card that holds the standby's partitions without UNOS's lines (a setup stopped midway) is
  answered as `leftover`, which the tab offers to remove. The return writes the counter at the
  sector of the board's profile.

**Every host action dry-runs.** `hostops.StandbyHost` runs the reads and, with `dry_run`, records
each change instead of running it; `POST arm`, `sync`, `drill`, `return`, `discard` and `DELETE`
take `"dry_run": true` and answer the plan, and nothing is audited. A supervisor outside its
container (`UNOS_HOST_EXEC=local`) answers why in `unavailable_reason` and refuses every action.

**The web UI**: System > Standby (`/system/standby`), last tab of Maintenance; the standby's problems
link there.

## Consequences

- The profile's values, the free counter sector and the watchdog are checked by the device drills
  (`feature-list.json`, the entry's notes); a wrong `mmc` number shows as a boot of the card's root
  where the eMMC was expected, never as a write to the eMMC's loader.
- A start of the supervisor reads the whole root filesystem's metadata once (rsync's dry run).
- A sync killed by the switch leaves a partial generation, which the next sync deletes.
- `docker save` stages an image's export under Docker's data root with Docker's classic image store
  (not with the containerd image store): an image that changed, at Set up and after an update of
  UNOS, is then written once to the eMMC as well. The device check measures it; the move of that
  staging off the eMMC is left to the owner.
- A file that the host rewrites by itself and that no exclusion names makes every sync write a
  small generation; the record's changes name it.
- The eMMC monitor's pass runs every 300 s, so the switch follows a failure within about 5 minutes
  and its own bounded waits (up to about 2 minutes more).
- Return through the standby keeps its audit action `standby_drill`.
