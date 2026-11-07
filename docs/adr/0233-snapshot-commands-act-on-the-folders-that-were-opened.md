---
status: accepted
date: 2026-11-07
area: storage
decided-by: agent
---

# ADR-0233: Snapshots are made, restored and deleted through the folders that were opened, and deleted by subvolume id

## Context

btrfs-progs resolves the paths it is given again when it runs, symbolic links included, so the snapshot commands acted on whatever a path led to at that moment. Pools are writable by apps, and a volume by the containers that bind it and the clients of its NFS export: such a process could rename `.snapshots` or a snapshot, or swap one for a link, between the router's checks and the command. A delete could then remove another subvolume, such as a volume, and a restore could copy the snapshot of another volume into this one. Volumes are deleted by subvolume id for the same reason (ADR-0069), and paths below the pools are opened without following links (ADR-0042). This settles the snapshot commands of storage.snapshots.

## Decision

- `supervisor/src/snapshots/router.py` opens the pool or volume, its `.snapshots` folder (made with mode 0755 in the opened folder when missing) and the snapshot one component at a time without following links (`open_dir_nofollow`, `open_subdirs_nofollow` of `supervisor/src/security/validators.py`). btrfs gets them as `/proc/self/fd/<fd>` paths with the descriptors in `pass_fds`; the kernel follows such a path to the open folder, not to its name.
- Create (`make_snapshot`) and restore (`_restore`) run `btrfs subvolume snapshot [-r] /proc/self/fd/<source>/. /proc/self/fd/<folder>/<name>`. The source ends in `/.`: btrfs-progs puts a snapshot inside an existing folder of the destination's name under the source's last component, and it refuses "." as a name, so a folder or link that takes the name meanwhile fails the command instead of receiving the snapshot. A restore copies the opened snapshot into the opened pool, volume or target folder.
- Delete (`delete_snapshot_by_id`) reads the id of the opened snapshot (`btrfs inspect-internal rootid`), resolves that id on the filesystem mounted at the pool's mountpoint (`btrfs inspect-internal subvolid-resolve`) and runs `btrfs subvolume delete --subvolid <id> <mountpoint>` only when the resolved path is the snapshot's place below the top level, after the mounted subvolume's own path when the pool is mounted with `subvol=`. A plain folder in `.snapshots`, whose id is that of the subvolume around it, answers 400 "<path> is not a snapshot: it is not a btrfs subvolume"; a snapshot moved or replaced before its id was read answers 409 "Snapshot <name> was not deleted: it was renamed or replaced while it was checked. Try again." A snapshot moved after the check is still the one deleted.
- Scheduled snapshot tasks (`supervisor/src/scheduler/router.py`) make their `auto_` snapshots with `make_snapshot` and delete the ones beyond the keep count with `delete_snapshot_by_id`; one that fails the checks is kept.
- Tests: `supervisor/tests/test_snapshots.py`, whose `FakeBtrfs` resolves paths as btrfs-progs does and keeps subvolume ids across renames, and whose race tests change the folders right before btrfs runs; `supervisor/tests/test_scheduler_validation.py`.

## Consequences

- A rename or a link made while a snapshot command runs no longer moves the command to another subvolume: the command fails, or it acts on the snapshot that was chosen.
- A delete runs three more btrfs commands, four for a pool mounted with `subvol=`.
- The output of btrfs is read as text with replacement characters and universal newlines, so a snapshot whose name is not UTF-8 or holds a carriage return never matches its resolved path: it answers 409 and stays.
- Listing snapshots still runs `btrfs subvolume show` by path; it changes nothing.
- The commands still run in the container (architecture.host-command-layer). `/proc/self/fd` is Linux's: on macOS the tests fake btrfs, and the device check shows the real btrfs-progs.
