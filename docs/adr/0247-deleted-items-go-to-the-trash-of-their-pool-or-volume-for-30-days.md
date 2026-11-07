---
status: accepted
date: 2026-11-07
area: files
decided-by: agent
---

# ADR-0247: Deleted items go to the trash of their pool or volume for 30 days, unless they are restored or deleted for good

## Context

Delete in the file manager removed items at once with rmtree or unlink, after one confirmation; ADR-0035 left "a trash or snapshot before deletions" open, and the feature `files.delete-to-trash` asks for one of the two. btrfs renames nothing into another subvolume (EXDEV), so a single trash per pool cannot take the items of its volumes, and the trash must stay out of the file list and out of shares, keep the protections of volumes and mounts (ADR-0170) and write nothing to the system disk (ADR-0214).

## Decision

- Delete (`DELETE /api/v1/files/delete`, `_delete` in `supervisor/src/files/router.py`) moves each item into the trash of the pool or volume that holds it, with one rename on its filesystem, after the checks of ADR-0170: `.unos-trash` in the pool's top folder, or in the volume's, a folder directly in the pool's top folder on another subvolume or filesystem. An item that one rename cannot take there, in a deeper subvolume (a snapshot, a restored copy in a volume) or below a deeper mount point, is refused with 409 before anything is moved; `?permanent=true` deletes for good as before.
- Each deleted item gets a folder of its own in the trash, `<UTC time>-<16 hex digits>` (`20261107T120000Z-…`, the time of the deletion), which holds the item as `item` and its path below the pool or volume in `origin`, written and flushed with fsync before the rename. The trash and these folders have mode 0700, so Repair permissions never enters them (it skips private folders, ADR-0155). The metadata stays on the pool's disks: a delete writes nothing to the system disk.
- The name `.unos-trash` is reserved: the file manager lists no entry of that name, refuses every path through one (403) and every new name equal to it (400), and a copy leaves out every entry of that name. A trash that is a symlink or a file is refused (409), never followed.
- `GET /api/v1/files/trash` lists the items of every pool's and volume's trash, newest first, with where they were deleted from, when (UTC with its offset), when they expire and the size of files, and the trashes it could not read. `POST /api/v1/files/trash/restore` renames items back where they were: the path in `origin` is checked first, may lead only below the trash's own pool or volume and is opened without following symlinks; every item is checked before any is restored, a name taken there answers 400 "Already exists: <name>" (the exclusive rename of ADR-0232, now across two folders), and a folder that is gone answers 409. `DELETE /api/v1/files/trash` and `POST /api/v1/files/trash/empty` delete items, or everything, for good, each after the checks of ADR-0170. The routes are administrator-only with the files router (ADR-0030).
- `trash_expiry_loop`, started in the lifespan of `supervisor/src/main.py` (ADR-0043), runs at the start and then once a day (`TRASH_EXPIRY_INTERVAL`) and deletes for good the entries deleted `TRASH_RETENTION_DAYS` (30) days ago or more, by the time in their name. It logs what it deleted, and its problems when they change; a pass with nothing to do writes nothing.
- SMB: every share section vetoes the trash beside the snapshots (`veto files = /.snapshots/.unos-trash/`, `supervisor/src/shares/smbconf.py`, extending ADR-0147), and a path through a `.unos-trash` folder cannot be shared (`share_path_problem`).
- Web UI: Delete in Files opens a dialog with Move to trash, which has the keyboard focus, and Delete for good; a refusal stays in the dialog. Files links to the Trash page (`webui/src/routes/files/trash/+page.svelte`): the items with the folder they were deleted from, the time of the deletion and the day they are kept until; Restore and Delete for good for the selected items and Empty trash, both after a confirmation whose Cancel has the focus.
- Tests: `supervisor/tests/test_files_security.py` (the trash tests, and the delete tests in both modes), `supervisor/tests/test_lifespan.py`, `supervisor/tests/test_smbconf.py`, `supervisor/tests/test_share_api.py`, `supervisor/tests/test_authz.py`, `webui/tests/files.spec.ts`, `webui/tests/roles.spec.ts`.

## Consequences

- Deleted items take up their space until they expire or are deleted for good; a pool that is full frees space only through Delete for good or Empty trash. Snapshots of a pool or a volume keep its trash too.
- The items of deeper subvolumes and of deeper mounts can only be deleted for good. A restore needs the folder the item was in: a folder that is gone is restored, or made again with New Folder, first.
- Apps and NFS clients that use a volume see its `.unos-trash` (root's, mode 0700) and, as root, can change it: what it holds is read as untrusted, and a damaged or missing `origin` leaves an item that can only be deleted for good. An `origin` whose bytes are not UTF-8 is restored with those bytes and shown with U+FFFD, as messages show such names.
- The expiry trusts the clock: a clock set far ahead deletes items early. Each pass, and every listing, reads the top folders of all pools and their volumes and trashes; a pool that hangs holds the listing, like the file list, and a pass holds one worker thread without a timeout, so that passes never pile up threads.
- The rendered smb.conf changes once on every installation, at the next apply or startup reconcile: a host-level change, checked on the dev device.

## Considered options

- A snapshot before each delete, the other choice of the feature: it keeps everything that changes in the pool or volume afterwards, not only the deleted items, stops at subvolume boundaries, needs btrfs (the stand-in has none), brings an item back only as a copy, and the snapshot code's device check is open (`storage.snapshots`).
- One trash per pool, with copies (or reflinks) of the items of volumes: a delete of a large folder would take the time and, without reflinks, the space of a copy.
- The trash's records in SQLite: a write to the system disk for every delete, and records that no longer match once a pool moves to another host or a database is restored.
- A retention period that administrators set: a setting with a page of its own; 30 days are fixed until another value is needed.
