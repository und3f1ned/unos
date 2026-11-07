---
status: accepted
date: 2026-02-08
area: storage
decided-by: owner
---

# ADR-0011: Pools use btrfs RAID1C3/RAID1C4 instead of RAID5/6, with read-only snapshots

## Context

btrfs RAID5 and RAID6 have a write hole: a power failure during a write can corrupt data silently. Deleting a pool and restoring a snapshot must not destroy data either.

## Decision

- Pool creation offers the profiles single, raid0, raid1, raid1c3, raid1c4 and raid10 (`PoolCreate.raid_level` in `supervisor/src/storage/schemas.py`, `PROFILE_MAP` and `MIN_DEVICES` in `supervisor/src/storage/pools.py`). RAID5 and RAID6 are refused; RAID1C3 and RAID1C4 (three and four copies of data and metadata, stable since Linux 5.5) take their place. The Storage page marks an existing RAID5/6 pool as unsafe.
- Snapshots are read-only (`btrfs subvolume snapshot -r` in `supervisor/src/snapshots/router.py` and in the scheduled snapshots of `supervisor/src/scheduler/router.py`).
- Restoring a snapshot overwrites nothing: it creates a writable copy named `restore_<date>_<time>` inside the snapshotted subvolume, next to its `.snapshots` folder, or inside a chosen target folder on a pool, and refuses a name that exists.
- Deleting or unmounting a pool never touches its disks: the filesystem stays intact and can be mounted again.
- Merged as pull request #19 on 2026-02-08. Tests: `supervisor/tests/test_storage.py` (RAID5 and RAID6 refused, device counts per profile), `supervisor/tests/test_snapshots.py` (snapshots and restores).

## Consequences

- Redundancy costs capacity: RAID1C3 and RAID1C4 keep full copies instead of parity.
- The November 2026 audit confirmed these as the right storage choices, and the docs that still promised RAID 5/6 were corrected.
- A restored copy is a new subvolume that takes space until it is deleted; for a volume's snapshot it is nested in the volume, where no page can delete it yet (ADR-0073).
