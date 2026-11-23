---
status: accepted
date: 2026-11-23
area: system
decided-by: agent
---

# ADR-0476: The root counts as read-only by mountinfo, the kernel's log of the boot, or a write probe after a new ext4 error

## Context

ADR-0471 tells a read-only root by the superblock options of `/proc/1/mountinfo`, and ADR-0475
implements it. The device check of 2026-11-23 on the BPI-W3 (kernel 6.1.172-vendor-rk35xx) disproved
that source. After `echo 1 > /sys/fs/ext4/mmcblk0p1/trigger_fs_error`:
- the kernel logged "EXT4-fs (mmcblk0p1): Remounting filesystem read-only" and later "Journal has
  aborted", and writes to `/` failed with EROFS;
- `/proc/1/mountinfo` kept `rw` in the mount's and in the superblock's options, `access('/', W_OK)`
  held and `statvfs` had no `ST_RDONLY`: this kernel's ext4 refuses the writes without the
  superblock's read-only flag that those report;
- so the pass answered `read_only` false and alerted `system_disk_errors`, never `system_disk_failed`.

## Decision

This amends ADR-0471's phase 1 (the read-only half of `system_disk_failed`) and ADR-0475, in
`supervisor/src/health/system_disk.py`. The root counts as read-only when any of these says so:
1. **mountinfo**: the superblock options of `/` in `/proc/1/mountinfo`, as before (a device check's
   read-only mount namespace, ADR-0221, still counts for nothing).
2. **The kernel's log of the boot** (`journalctl --dmesg`, without `--since`) has, for the root's
   partition, "EXT4-fs (<partition>): Remounting filesystem read-only" or an ext4 line of it that
   ends in "Journal has aborted". The log is read at the first pass, for a root taken read-only before
   the supervisor started, and at each pass after the first new ext4 error, since journald may store
   the line after the count rose.
3. **A write probe**, only when neither above tells: once per rise of ext4's error count since the
   start, and at the first pass when ext4's `last_error_time` is not before the boot's `btime` of
   `/proc/stat` (an error of this boot before the start whose line the log lacks). On the host, under
   `timeout 20`, `sh` makes and removes `.unos-write-probe` in `/var/lib/unos/backups` (the updater's
   folder, which `deploy/docker-compose.yml` mounts into the supervisor at the same path), after
   `stat -L -c %D` showed that the folder is on the root's device. A create refused with "Read-only
   file system", or "Input/output error" (a kernel that shuts ext4 down after an error), counts.
   Anything else (the folder elsewhere, a probe that does not end, a failure to start it) is no
   verdict, which the card shows until the next probe; so is a root whose mount (not superblock) is
   read-only, where a refused write would tell nothing of ext4, and which is not probed.

A root found read-only by 2 or 3 stays so until the supervisor restarts: ext4 does not let an
aborted filesystem be remounted read-write within the boot. Both prove an ext4 error of this boot,
so `system_disk_failed` follows from them alone, also after a restart of the supervisor that
reset the count of new errors; by mountinfo alone it still needs a new ext4 error since the start,
so a root remounted read-only by hand is still none.

## Consequences

- The probe writes the root at most once per new ext4 error and once per start after an ext4 error
  of the boot: a create and a remove in one folder (ADR-0214). A NAS without ext4 errors never
  writes for it.
- A host whose `/var/lib/unos/backups` is missing or on another filesystem gets no probe; the log of
  the boot still tells.
- One more `journalctl` at the first pass, and at each pass after the first new ext4 error.
- On the BPI-W3 the log tells, so its drill exercises the log, not the probe: the probe's refusal is
  shown on the device by running its script by hand on the read-only root.
- Tested in `supervisor/tests/test_system_disk_health.py` (`TestTheReadOnlyRoot`), with the W3's
  remount line as recorded and the other lines in the kernel's form; the script itself on Linux.
