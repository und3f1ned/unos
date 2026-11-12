---
status: accepted
date: 2026-11-12
area: storage
decided-by: agent
---

# ADR-0358: Restored copies are listed and deleted on the Snapshots page, by subvolume id after the checks of a volume's Delete

## Context

Restore on the Snapshots page makes a writable snapshot `restore_<date>_<time>` (UTC) right in the pool or the volume whose snapshot it restores (ADR-0242, ADR-0243). Nothing listed or deleted the copies in a volume, and one such copy made the volume's Delete answer 409; ADR-0073 proposed listing and deleting them by subvolume id after an in-use check, and ADR-0352 left it open (`storage.restore-copies-deletable`). Working on through the features under the owner's delegation ("work on through the features, ask no questions; I allow everything", in translation), the agent decided how.

## Decision

- `GET /api/v1/snapshots/copies?subvolume=<path>` lists the copies right in a pool (its mountpoint) or a volume (its folder), newest first, with their name, pool, volume (none for the pool), folder, path and time; `DELETE /api/v1/snapshots/copies/{name}?subvolume=<path>` deletes one. Both are routes of `supervisor/src/snapshots/router.py` (administrators only, as the router), and act on the host in `supervisor/src/storage/pools.py` (`list_restore_copies`, `delete_restore_copy`), as volumes do; another path answers 400.
- A restored copy is what a restore makes, and nothing else: a name `restore_<YYYYMMDD>_<HHMMSS>` with a valid time (`pools.RESTORE_COPY_NAME`, which `RESTORE_PREFIX` and `utc_stamp` of the snapshots router give); right in the pool's top folder or in a volume, its path from the top level (`<name>` or `<volume>/<name>`) confirmed byte for byte by the check of ADR-0352; and a writable snapshot, for which `btrfs subvolume show -r <id>` prints a Parent UUID and no `readonly` flag. Anything else is not listed, and its Delete answers 400 (another name, or a subvolume that is not a writable snapshot), 404 (none there, a plain folder too) or 409 (the subvolume list gives the name only ids of other subvolumes). The time listed is the one in the name, in UTC with its offset.
- Delete holds the pools' lock, needs the pool mounted read-write at its top level, and runs `btrfs subvolume delete --subvolid <id> <mountpoint>` after the checks of a volume's Delete, which are one function for both now (`_refuse_used_subvolume` and `_delete_subvolume`, under the off-device copy's `FOLDERS_LOCK`, ADR-0337): running containers, SMB shares, NFS exports and the off-device copy that use the copy or a folder in it refuse it with 409 that names them, Docker that does not answer with 503; users of the volume around it do not count. The messages begin with "Restored copy '<volume>/<name>'". A copy that holds subvolumes is refused with 409. The volume that held the copy can be deleted afterwards, once its snapshots are deleted too.
- The audit log gets `snapshot_deleted` (resource type `snapshot`, the copy's path, "Deleted the restored copy <name> in <folder>", a warning), after the change only (ADR-0350).
- The copy of a pool's snapshot is also a volume of the pool: it is listed under the pool, and either Delete removes it.
- The Snapshots page (`webui/src/routes/snapshots/+page.svelte`) lists the copies of the pool or volume chosen in a card of their own, "Restored copies in <pool or volume>", with each copy's name, path and time, apart from the snapshots; Delete asks first and shows the server's reason. The Restore dialog says where the copy will be listed.
- The volumes scenario check (`scripts/dev/scenario.py`) makes a snapshot of its volume, restores it, finds the copy listed and a subvolume on the host, has its Delete refused while a share uses the copy, deletes it, and then deletes the snapshot and the volume; its clean-up deletes the copies and snapshots of its volume before the volume.
- Tests: `supervisor/tests/test_restore_copies.py`, `webui/tests/snapshots.spec.ts`, `webui/tests/mobile-layout.spec.ts` and the volumes tests of `supervisor/tests/test_dev_scenarios.py`.

## Consequences

- Listing the copies of a pool or volume reads its subvolume list, checks the paths of the names like a copy's and runs one `btrfs subvolume show` for each of them; the page does so at each choice and after each restore.
- A copy that a restore put elsewhere through the API's `target_path` (the web interface never sends one), or that was renamed, is not listed, and must be deleted on the host. A subvolume made by hand under a copy's name is never taken for one.
- A copy renamed between its checks and the delete is still deleted, by its id, wherever it is then; another subvolume that takes its name meanwhile is not.

## Considered options

- Recording each restore in the database to recognise its copy: a write for each restore and rows to keep in step with copies deleted elsewhere (ADR-0214), while the name, the place and btrfs's Parent UUID already tell a restore's copy.
- Deleting through the folders opened in the container, as snapshots are (ADR-0242): the in-use checks and the pools' lock are those of volumes, on the host, and a volume's way of deleting by id keeps one way for both.
- An audit action of its own: the copy is a btrfs snapshot, and `snapshot_deleted` with its own text keeps the names that the Audit Log page knows.
