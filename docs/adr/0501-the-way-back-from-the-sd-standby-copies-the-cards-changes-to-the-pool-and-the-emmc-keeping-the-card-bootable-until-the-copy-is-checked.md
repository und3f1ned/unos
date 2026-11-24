---
status: accepted
date: 2026-11-24
area: system
decided-by: agent
---

# ADR-0501: The way back from the SD standby copies the card's changes to the pool and to the eMMC, and keeps the card bootable until the copy is checked

## Context

ADR-0471 (phase 4) designs the way back from the SD standby with the card's changes: the critical apps to
their pool and UNOS to the eMMC; the entry `system.sd-standby-failback` lists its steps. Until it, the
returns of ADR-0471 and ADR-0500 discarded the card's changes. The steps leave open where the backups
go, what a failure leaves, how the eMMC is checked and when the card stops being bootable, how the
volumes reach a Docker whose data is on a pool (ADR-0486), and what the boards allow. The owner delegated
these choices ("go on without me", 2026-11-24), above all safe: never a board that boots neither system,
never the card's changes deleted before they are on their target, every replaced file kept, no write to
a pool that is read-only or degraded, and no write to the eMMC beyond what the copy needs (ADR-0214).

## Decision

**The pool return is the default** (`supervisor/src/standby/failback.py`; `POST
/api/v1/system/standby/apps/return`, `discard` false). It is offered for an app whose pool is mounted,
writable and misses no device (`apps.pool_unfit`), and whose containers' pool folders outside what the
copy brings are there; its dry run answers rsync's `Total transferred file size` in MiB. Under the
standby's lock (its copy runs in the sync's unit, `StandbyHost.long_change(writable=True)`): stop the
app's containers on the card, none left running (step `stop`); `rsync -aHAX --numeric-ids --delete --omit-dir-times --backup
--backup-dir={pool}/apps/<app>.before-failback-<UTC time>` of the run copy's `data/` into
`{pool}/apps/<app>/` with the sync's excludes, so caches and ADR-0408's copies are neither copied nor
deleted (`copy`); the pool-bound containers again from the definitions saved at the move, those that ran
started (`containers`); the move's record forgotten (`record`); the run copy deleted last. A failure
stops the app's containers again, keeps the run copy, records the step and error in
`failed_over.returning` (the tab shows it), answers 500 and alerts; the return runs again with the same
backup folder (a folder from state.json is taken only when it is the app's own name and a time). Each
return is audited as `standby_apps_returned` and alerted. The discarding return stays as the other choice
(`discard` true, the card's model typed), but not once a pool return got past the stop: the pool then holds
part of the card's files, and only the pool return finishes it. The planned repair's apps return the same
way once their pool is mounted again under its name. An app that returned on the other system (moved on
the eMMC's system, returned to its pool on the standby's root, then UNOS back on the eMMC) finds this
Docker's containers on the card without a record: before the supervisor serves, they are created on the
pool again from the copy's definitions (`apps._returned_elsewhere`).

**The eMMC's check** (`supervisor/src/standby/emmc_return.py`). On the card's root the root line of the
eMMC's own fstab lines that the sync kept (`/var/lib/unos-standby/emmc-fstab`) names the root; blkid must
find it, its type must be ext4, it must not be mounted, and `fsck.ext4 -n` (under the host's `timeout`
of 900 s) must end with status 0. It runs at the supervisor's start on the card and before every return;
its result is in RAM and on the tab, and "Check the eMMC again" is the return's dry run. Otherwise both
returns answer 409 and the tab says that the card stays the system disk and that no standby is left.

**The return to the eMMC with the card's changes** (`POST /api/v1/system/standby/return`, `discard`
false) runs in the transient unit `unos-standby-return` (`host/unos-standby-return.sh`), since it stops
Docker and the supervisor with it. The supervisor writes the script, `return.env`, the exclusions, the
volumes' pairs, the eMMC's fstab, the next state.json and the counter's text into
`/run/unos-standby-return` (the host's RAM) and starts it; the unit waits 5 s, then:
1. `check`: the root not mounted, `fsck.ext4 -n` again;
2. `stop`: those of docker.socket, docker.service, smbd.service and nmbd.service that run; state.json must
   still be the one the supervisor read (its SHA-256), or the return stops here;
3. `mount`: the eMMC's root at `/run/unos-standby-return/emmc`, `noatime`;
4. `copy`: `rsync -aHAX --numeric-ids --one-file-system --delete --omit-dir-times --inplace --no-whole-file`
   of `/` with the sync's exclusions (EXCLUDES, VOLATILE, the card's mount points and swap files, Docker's
   and containerd's settings) and without the fix-ups, which those exclusions cover; also the eMMC's other
   filesystems (its own fstab lines' mount points, such as a /boot partition) and the eMMC's drop-ins of
   `docker.service.d` and `containerd.service.d` that hold `RequiresMountsFor=`, so that the eMMC keeps its
   own, its swap files, and beside a boot partition of its own the kernel's modules (`/lib/modules`,
   `/usr/lib/modules`), so that the kernel on that partition keeps its modules; a pool's mount point that
   the card removed loses its immutable flag so that `--delete` can remove it, and one that the card made
   is made on the eMMC, immutable below the pools' folder (ADR-0034); then UNOS's compose volumes into the
   eMMC's Docker volumes, which the sync's fix-ups now record
   (`/var/lib/unos-standby/emmc-volumes`). A volume on a pool (Docker's data root on the W3 and the F3,
   ADR-0486) gets `--backup --backup-dir=<volumes>.before-failback-<time>/<volume>` and needs that pool
   mounted, writable and not degraded; one on the eMMC is written in place, without a backup;
5. `fstab`: the standby's `/etc/fstab` with its root line replaced by the eMMC's own lines, so that a pool
   added on the card comes along;
6. `verify`: a dry run of the same copies finds nothing left (after at most one more copy), the root is
   unmounted, and `fsck.ext4 -f -n` passes;
7. `finish`: `unos/drill.txt` deleted, the tries counter at 0, state.json without `standby_ran` and with
   `emmc_return`, then `unos/emmc-failed` deleted last, and `systemctl reboot`.

Until the mark goes, the card's chooser boots the card's root: a return cut off at any point (a failure,
a stop of the unit, a power cut) leaves the card's standby bootable, and the eMMC is booted only once its
copy is complete and checked; the counter at 0 before the mark goes gives the eMMC its three tries. A
failure unmounts the eMMC, starts what it stopped and leaves `failed`, the step and why in
`/run/unos-standby-return/result`; the journal names each step. The supervisor follows the unit every 3 s
while it runs, so a failure before Docker stops (the check) is recorded at once; one after is recorded by
its next start on the card. Either way it is on the tab (`emmc_return.failed`), audited as the system's
`standby_returned` and alerted, the folder goes (`rm --one-file-system`, never into an eMMC left mounted),
and an `emmc_return` that a return left in state.json is dropped. The result is in RAM: a reboot during the
return loses it, and the card's root boots. While the unit runs, the tab offers nothing that changes the
card: another return, a pool return, a move and the automatic moves answer 409 or wait, since the unit
stops at a state.json that changed after the supervisor read it. On the eMMC, `emmc_return` is alerted once ("UNOS is back on the eMMC"), and the
next scheduled sync is `return`: forced, with the card's Docker data emptied (ADR-0471), which drops it once
it wrote a generation. Apps that run from the card stay there (state.json keeps their records, and the
eMMC's start applies their moves again, ADR-0500) until their pool's return. The return without the card's
changes (`discard` true) stays as the other choice: the mark and the drill go, the counter is 0, and the
eMMC's system then offers Discard or Return through the standby.

**The boards.** Nothing depends on the card's name (`mmcblk1` on the BPI-W3, `mmcblk0` on the BPI-F3): the
card's device comes from its mounts, the counter's sector from the board's profile (32767 on the W3, 960
on the F3), and the unit touches no loader, partition table or SpacemiT boot info. The F3's eMMC boot
partition, when it is a filesystem of its own, is left out of the copy.

## Consequences

- The eMMC takes only the changed blocks of the files that differ, its root mounted `noatime`; the eMMC's
  own changes since the last sync are replaced without a backup (a backup on the eMMC would double its
  writes). The pools keep every replaced file of the apps' folders and of UNOS's volumes beside them,
  until an administrator deletes those folders.
- On an eMMC with a boot partition of its own the kernel and its modules stay the eMMC's: a kernel updated
  on the standby does not reach the eMMC. Update packages on the eMMC's system only.
- The return to the eMMC stops UNOS for the copy: on the W3 a few minutes, as long as rsync's reads of the
  root and the fsck of the eMMC take. A host that loses power during it boots the card again.
- The return needs a generation whose fix-ups recorded the eMMC's volumes: the first sync after this change
  writes one, and a standby that ran on an older one returns only without its changes.
- Tests: `supervisor/tests/test_standby_failback.py` (the pool return, its dry run, backup folder, failures
  at each step and repeat; the planned repair's return; the eMMC's check and refusals; the unit's files,
  its script with stubs: its order, exclusions, fstab, verification, counter and reboot, and its failures),
  and `webui/tests/standby.spec.ts`.
