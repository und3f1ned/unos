---
status: accepted
date: 2026-11-25
area: storage
decided-by: agent
---

# ADR-0505: A pool held by its earlier mount is freed by stopping its containers before the Mount

## Context

When a pool's disk goes away while an app's container uses the pool, the host unmounts it (systemd,
for a disk that is gone), but the container's mount namespace keeps its bind of the earlier mount, so
the kernel keeps the btrfs filesystem on the old device and the app goes on with what it cached. When
the disk comes back, under another device name, btrfs refuses to register it ("device scan failed:
File exists") and a mount of the pool fails with "fsconfig() failed: File exists". On the BPI-W3 on
2026-11-25 (`storage.mount-after-a-disk-returns`), with the test pool's USB stick deleted from the SCSI
layer and found again while Uptime Kuma ran from it, Mount on the Storage page answered 500 with that
text; stopping the app, Mount and Start brought it back. A critical app of the SD standby is moved to
the card after two RAID monitor passes (ADR-0500), which frees the filesystem; any other stays held.

## Decision

- A pool is held when the kernel still has its filesystem (`/sys/fs/btrfs/<uuid>`, `_filesystem_active`)
  while the host mounts it nowhere (`pools._held`). `PoolInfo.held_by` then names the running
  containers with a mount from its mount point or labelled with the pool (`_holders`, the paths as
  Docker reports them, not resolved through links); `[]` when none, or Docker cannot tell; null for a
  pool that is not held. The Storage page shows it on the pool's card ("Still held by its earlier
  mount"). A pool whose disk is still gone is not listed (btrfs finds no device of it): its holders are
  named once the disk is back.
- Mount of a held pool answers 409 (`PoolHeldError`) naming them, and changes nothing; the check comes
  last, after every other refusal of the Mount, so that none follows a stop. With
  `stop_holders` (`POST /storage/pools/{name}/mount?stop_holders=true`, which the Mount dialog sends
  for a pool with holders and says so) it stops them (`HOLDER_STOP_SECONDS`, 30), waits for the kernel
  to drop the filesystem (`RELEASE_WAIT`, 30 s), mounts the pool and starts them again in the background
  with the pool's apps (`_start_pool_apps`, the app checks for an app's containers, none for the
  others). A holder gone meanwhile (an app moved to the SD standby is created again there) is passed
  over. The audit entry pool_mounted says which it stopped and that they are being started again.
- A held pool that no running container holds answers 409 saying so (restart the host); one still
  held after its containers stopped answers 409, and they stay stopped and are named, as when a stop
  fails (503) or the mount after the stops fails (its own status); each such failure after a stop is
  audited as a failed pool_mounted naming them. Docker that cannot tell the holders answers 503.

## Consequences

- One `test -d` on the host for each pool that is not mounted, at each read of the pools (the Storage
  page, the RAID monitor), and a Docker read only for a held one.
- The stopped containers lose what they had kept of the old disk's files since it went away; it was
  not on any disk.
- A container stopped this way is started again even when its restart policy would not have started
  it at boot: it was running.
- The pools' lock is held while the holders stop, one after another (up to 30 s each) and the kernel
  lets go (up to 30 s): other changes of pools wait meanwhile; page reads and the RAID monitor take no
  lock.
- The holders are read again when the Mount runs: a container started since the dialog listed them is
  stopped too. A critical app that the RAID monitor moves to the card between that read and its stop
  can have its card copy stopped and started again.
