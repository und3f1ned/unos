---
status: accepted
date: 2026-11-05
area: storage
decided-by: agent
---

# ADR-0035: Irreversible storage operations need a typed confirmation and device identity checks

## Context

In 1.0.0 `mkfs.btrfs -f` ran on any device that matched a pattern, without checking signatures, partitions or use; from inside the container the system disk looked unused; deleting a pool left a ghost whose disks stayed blocked; and the file manager deleted a whole pool with `rmtree` after one browser `confirm()`.

## Decision

- Creating a pool needs `confirm_name` equal to the pool's name (400 otherwise).
- A disk that holds data (a filesystem, a partition table or any signature that `wipefs` sees; a failed probe counts as data) needs `force: true` (409 otherwise). With `force`, every selected disk must be named in `device_identities` with its serial number, WWN and size (400 otherwise), and a disk that no longer matches is refused (409), because disks change names when they are reconnected.
- System disks and their partitions, disks with mounted partitions, active swap, members of mounted pools or of md arrays, and boot or virtual devices are refused, judged from the host's view (`findmnt`, `lsblk`, `swapon` and `wipefs` through `supervisor/src/host.py`, in `supervisor/src/storage/disks.py`).
- Unmounting or deleting a pool is refused while host processes or containers use it (409), and deleting a pool never touches its disks.
- The Storage page keeps Create disabled until the name is typed again and the erase is confirmed (`webui/src/routes/storage/+page.svelte`).
- Tests: `supervisor/tests/test_disk_safety.py`, `webui/tests/storage.spec.ts`.

## Consequences

- Erasing a disk takes deliberate steps, and a disk that was swapped or renamed between the listing and the creation is not erased by mistake.
- The file manager cannot delete, rename or move pool roots, volumes or mounts (ADR-0170).
- The audit's other safeguards are still open: a Disable of a network interface that asks for confirmation and rolls back on a timer, and a trash or snapshot before deletions.
