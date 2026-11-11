---
status: superseded
superseded-by: ADR-0348
date: 2026-11-06
area: files
decided-by: agent
---

# ADR-0170: The file manager never deletes, renames or moves volumes, subvolumes or mounts

## Context

Superseded by ADR-0348: an entry that is a mount point itself, a bind mount from the same filesystem included, is refused before a rename or a move within its pool too, and any other EBUSY answers 409.

In 1.0.0, deleting a volume (`/mnt/pools/<pool>/<volume>`) in the file manager removed all its files and then the volume itself, also while an app, an SMB share or an NFS export used it, and volumes could be moved, or renamed through the API. Deleting a folder that contained a subvolume or a mount, or moving it to another filesystem (a copy, then a delete), went into that subvolume or mount and deleted its files too.

## Decision

- Deleting, renaming or moving a btrfs subvolume root (a volume, a snapshot, any other subvolume) or the mount point of another filesystem is refused in the file manager with HTTP 409, which says that it is a separate subvolume or filesystem (`_refuse_separate_filesystem` in `supervisor/src/files/router.py`).
- A folder that contains one, or that is or contains a bind mount, cannot be deleted or moved to another pool or volume either (409, `_refuse_nested_filesystems`). It can still be copied, moved within its pool or volume, and renamed through the API; copies of volumes are allowed.
- A subvolume or mount is recognized by a device number other than its parent's and by the mount points in `/proc/self/mountinfo`; when the mount table cannot be read, the operation is refused. The checks use the same directory descriptors as the operation, and a directory is checked again right before it is deleted.
- When a move to another filesystem finds that the source was replaced while it was copied, what is there now is kept, the copy stays at the destination, and the move answers 409.
- Volumes are deleted on the Storage page, which first checks whether a container, a Docker volume, an SMB share or an NFS export uses them (ADR-0069).
- Tests: `supervisor/tests/test_files_security.py` (volumes, deeper subvolumes and mount points, bind mounts, an unreadable mount table, a mount that appears during the copy of a move).

## Consequences

- An administrator cannot remove a snapshot or a volume from the file manager, only from the Snapshots and Storage pages.
- A restore of a snapshot of a volume creates a nested subvolume that no page can delete yet, and the remaining races of the delete walk are open; both are listed in ADR-0073.
- Every delete, and every move to another filesystem, walks the folder tree first, which takes a while on large trees.
