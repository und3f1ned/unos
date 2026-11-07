---
status: accepted
date: 2026-11-05
area: storage
decided-by: agent
---

# ADR-0034: Pools are mounted by UUID from managed fstab entries, and empty mountpoints are immutable

## Context

In 1.0.0 a pool's fstab line (`LABEL=... btrfs defaults 0 0`) was written into the container's `/etc/fstab`, which the host never reads and which is lost with the container. After a reboot the device's pool was not mounted, and the SMB share and transmission wrote to the SD card under its empty mountpoint. Even on the host, such a line without `nofail` would hang the boot when a disk is missing.

## Decision

- A pool is mounted in the host's mount namespace at `/mnt/pools/<name>`, by filesystem UUID, and persisted by one managed entry in the host's `/etc/fstab`: the marker line `# unos-pool:<name> (managed by UNOS)` followed by `UUID=<uuid> /mnt/pools/<name> btrfs defaults,noatime,nofail,x-systemd.device-timeout=15s,x-systemd.before=docker.service 0 0` (`supervisor/src/storage/pools.py`).
- UNOS adds, rewrites or removes only its own marker-and-entry pairs and keeps every other line; before its first change it copies the original file once to `/etc/fstab.unos-backup`. The fstab markers, not a database table, are the record of the pools UNOS manages.
- Mounting a pool through UNOS (`POST /api/v1/storage/pools/{name}/mount`) writes its managed entry, and unmounting it (`.../unmount`) removes the entry; the data is kept.
- At startup, before the scheduler and the apps start writing, the supervisor mounts the managed pools that the host did not mount at boot (for example on a slow disk, which `nofail` lets the boot skip), then starts their apps in the background, each app's checks bounded to 60 s (`reconcile_pools`, ADR-0043).
- The mountpoint of a pool that is not mounted is made immutable with `chattr +i`, so that even root gets EPERM there, and a pool is never mounted over a directory that holds files (409).
- Tests: `supervisor/tests/test_pool_persistence.py` (the fstab text, mounting, unmounting and deleting, the mountpoint guard, durable host writes, the startup reconcile).

## Consequences

- Nothing lands on the system disk under the mountpoint of a pool that is not mounted, and a missing disk delays the boot by at most 15 s instead of blocking it, while Docker and the apps it restarts wait for the mount.
- A pool made by hand or by an older version has no marker and is not managed until it is mounted once through UNOS; only managed pools can hold SMB shares (ADR-0148).
- An fstab line that UNOS does not manage for the same mountpoint blocks the pool's creation until it is removed.

## Considered options

- Pools stored in the database by UUID and mounted by the supervisor at startup, the audit's first option: not chosen; the host's own fstab mounts the pools at boot, before Docker starts the apps, without waiting for the supervisor.
