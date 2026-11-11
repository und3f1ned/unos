---
status: superseded
superseded-by: ADR-0342
date: 2026-11-06
area: storage
decided-by: agent
---

# ADR-0070: Pool unmount and delete respect Docker volumes, and odd names do not break storage

## Context

Superseded by ADR-0342: an unmount or delete is refused (503) when Docker cannot be asked, instead of going ahead.

A container that keeps data in a pool through a Docker volume of the local driver (one that binds a pool folder) keeps the filesystem mounted inside the container after the host's `umount` succeeds, so the pool looked unmounted and its disks safe to remove while they were still written. Names that are not valid UTF-8 (of subvolumes, labels or disk models) broke the storage pages.

## Decision

- Unmounting or deleting a pool is refused with 409 while a running container uses it: a mount from the pool's mountpoint or below it, a local Docker volume that binds a folder there, or the app label of the pool (`_containers_using` and `_unmount_pool_target` in `supervisor/src/storage/pools.py`). When Docker cannot be asked, the unmount goes ahead and a check afterwards mounts the pool again if the filesystem is still in use elsewhere (ADR-0035).
- Output of btrfs, lsblk and findmnt is decoded with U+FFFD for bytes that are not UTF-8. A subvolume name with U+FFFD is never a volume (ADR-0069); disk models and filesystem labels are only shown.
- Pool health is `unknown` when the device error counters cannot be read, never `healthy` (ADR-0036).
- Tests: `supervisor/tests/test_pool_persistence.py`, `supervisor/tests/test_volumes.py`, `supervisor/tests/test_storage.py`, `supervisor/tests/test_health_smart.py`.

## Consequences

- An administrator stops the containers that use a pool before unmounting or deleting it.
- A name that is not UTF-8 is shown with replacement characters and cannot be selected as a volume.
- Two cases stay open (ADR-0073): the comparison of a Docker volume's bound folder with the pool is lexical, and when Docker cannot be asked during an unmount, the host mount of such a volume can be taken for a deliberate mount of the pool elsewhere.
