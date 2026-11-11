---
status: accepted
date: 2026-11-11
area: files
decided-by: agent
supersedes: [ADR-0170]
---

# ADR-0348: The file manager never deletes, renames or moves volumes, subvolumes or mounts, bind mounts included, and a busy entry is a conflict

## Context

ADR-0170 found a bind mount from the same filesystem, which has the st_dev of that filesystem, only in the mount table, and only before a delete or a move to another filesystem. A rename, or a move within the pool, of an entry that is such a bind mount reached rename(2), which refuses it with EBUSY: the file API answered 500, and a move of several items stopped at that item after the items before it had moved (`storage.edge-cases`, step 7). Working on through the features under the owner's delegation ("work on through the features, ask no questions; I allow everything", in translation), the agent decided to refuse such an entry with 409 before anything changes, and to answer any other EBUSY with 409 too. This ADR restates ADR-0170 with that changed.

## Decision

- Deleting, renaming or moving a btrfs subvolume root (a volume, a snapshot, any other subvolume) or the mount point of another filesystem is refused in the file manager with HTTP 409, which says that it is a separate subvolume or filesystem (`_refuse_separate_filesystem` in `supervisor/src/files/router.py`).
- So is renaming an entry that is a mount point itself, a bind mount from the same filesystem included, or moving it, within its pool or volume or to another one. A move checks every item, a file too and whatever its device number, in the mount table before any is moved (`_refuse_separate_filesystems`): rename(2) would refuse such an item with EBUSY, and a move to another filesystem would copy it and then fail to delete it, after the items before it had moved. A rename, and a mount point that appears after that check, are refused when rename(2) answers EBUSY, with the same text (`_refuse_busy`). Without the mount table, the move of a file, or of a folder within its filesystem, is left to rename(2), and a file that is a mount point and goes to another filesystem is copied before its delete answers 409 "In use by the system" (`_http_error`), the copy staying at the destination.
- A move that fails at an item after other items moved says how many moved before it, as a delete does (`_done_before`, ADR-0259).
- Any other EBUSY of the file API answers 409 "In use by the system, for example as a mount point, so the file manager cannot change it" (`_http_error`), not 500.
- A folder that contains a subvolume or a mount, or that is or contains a bind mount, cannot be deleted or moved to another pool or volume either (409, `_refuse_nested_filesystems`). A folder that contains one can still be copied, moved within its pool or volume, and renamed through the API; copies of volumes are allowed.
- A subvolume or mount is recognized by a device number other than its parent's and by the mount points in `/proc/self/mountinfo`; when the mount table cannot be read, a delete of a folder, or its move to another filesystem, is refused. The checks use the same directory descriptors as the operation, and a directory is checked again right before it is deleted.
- When a move to another filesystem finds that the source was replaced while it was copied, what is there now is kept, the copy stays at the destination, and the move answers 409.
- Volumes are deleted on the Storage page, which first checks whether a container, a Docker volume, an SMB share or an NFS export uses them (ADR-0069).
- Tests: `supervisor/tests/test_files_security.py` (volumes, deeper subvolumes and mount points, bind mounts, an unreadable mount table, a mount that appears during the copy of a move). `test_a_folder_that_is_a_bind_mount_cannot_be_renamed_or_moved` and `test_any_other_entry_that_is_busy_is_a_conflict` failed with 500 on the code of ADR-0170; `test_a_move_refuses_a_bind_mount_before_anything_is_moved` failed because the first item had moved, and `test_a_move_that_fails_part_way_says_how_many_items_went_before` because the answer did not say so. `test_a_move_refuses_a_file_that_is_a_bind_mount_with_another_st_dev` failed on the first version of this ADR's code, which looked an item up in the mount table only when it had the destination's device number: the first item had moved, and to another pool the file had been copied and the answer was "In use by the system".

## Consequences

- An administrator cannot remove a snapshot or a volume from the file manager, only from the Snapshots and Storage pages.
- A restore of a snapshot of a volume creates a nested subvolume that no page can delete yet, and the remaining races of the delete walk are open; both are listed in ADR-0073.
- Every delete, and every move to another filesystem, walks the folder tree first, which takes a while on large trees. Every move reads the mount table once more.
- A file that is a bind mount is refused before any move too, within its pool or to another one. A delete still finds it only when its rename into the trash answers EBUSY: 409 "In use by the system", with how many items went before it.
- A copy that fails part way does not say how many items were copied; the copies are in the destination folder.
