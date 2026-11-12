---
status: accepted
date: 2026-11-12
area: storage
decided-by: agent
supersedes: [ADR-0073]
---

# ADR-0352: Volume reads, container folders, disk names and deletes withstand renames, odd names, links and new mounts

## Context

ADR-0073 proposed fixes for storage cases of low severity that the reviews of slice 1 left open; ADR-0344 and ADR-0348 closed two of them (`storage.edge-cases`, steps 3 and 7). Working on through the features under the owner's delegation ("work on through the features, ask no questions; I allow everything", in translation), the agent built steps 1, 2, 4, 5, 6 and 8 of that entry as below, and chose another fix for step 1 than the staging folder that ADR-0073 proposed. This ADR records what was built and what stays open.

## Decision

- The quota guard of a new volume (ADR-0069) takes as the newest id before the create the higher of the first line of `btrfs subvolume list --sort=-rootid` and the first level-0 line of `btrfs qgroup show --sort=-qgroupid` (`_newest_subvolume_id`, `_newest_quota_group` in `supervisor/src/storage/pools.py`). A subvolume keeps its quota group 0/<id> while a rename takes it out of the subvolume list (step 1); with the highest id first, a line that a name with a line break adds follows its own subvolume's line and cannot raise the result (step 2).
- A volume's id: every id that the subvolume list gives the name is checked on the host, whose shell compares `btrfs inspect-internal subvolid-resolve <id> <mountpoint>` with the name byte for byte (`_VOLUME_PATHS_SCRIPT`, `_volume_ids`, `_volume_id`). Only an exact match is the volume, for the list, a delete and the quota (step 2): a subvolume named like a volume followed by a line break is not taken for it, a forged line does not hide the volume, and a name that no subvolume has exactly is not listed. 409 when the list gives the name only ids of other subvolumes; 500 when btrfs cannot resolve them.
- Create (step 6): when the list or that check fails right after the create, the answer (500) begins "Volume '<name>' was created without its quota:" and ends "Delete it and create it again."; when btrfs refuses the create and something is at the name now, 409 "'<name>' already exists in pool '<pool>': <what> took the name while the volume was being created. Nothing was created.", where `<what>` comes from `stat --format='%i %F'` on the host (`_entry_at`: a folder, a file, a symbolic link, or a subvolume for inode 256).
- Containers that use a pool or a volume, for an unmount, Remove Pool, Delete Volume and an import under an earlier pool's name (step 4): besides the lexical comparison, each Source of a container's mounts and each folder that a volume of the local driver binds is made absolute (a relative one from /, dockerd's folder), resolved by `realpath --canonicalize-missing` on the host, and located in the host's mount table as a filesystem (MAJ:MIN) and its folder (FSROOT); it counts when it lies in the folder of the pool or volume (`_containers_on`, `_host_paths`, `_folder_on_host`). That covers symbolic links, relative paths, folders of a pool bound elsewhere on the host and the mounts of third-party volume drivers. 500, before anything changes, when realpath or findmnt fails.
- Disk names (step 5): `DISK_NAME_PATTERN` in `supervisor/src/security/validators.py` also takes the names that the SCSI, virtio, Xen, IDE and s390 DASD drivers give with more letters (sdaaa, xvdaa, dasda, dasdaa) and their partitions; whether a name is a disk of the host is still lsblk's to say (ADR-0072).
- A delete for good of a folder (also Empty trash, the trash's expiry and the source of a move to another pool or volume) walks it on descriptors, one per level, and checks the device and the mount ID (`/proc/self/fdinfo`) of each folder before it deletes anything in it (`_remove_tree` in `supervisor/src/files/router.py`). A subvolume or a mount, a bind mount from the same filesystem too, that appears below the folder after the checks of ADR-0348 stops the delete with 409, which says that part of the folder was deleted before that (step 8).
- Tests: `supervisor/tests/test_volumes.py`, `test_pool_persistence.py`, `test_files_security.py`, `test_health_smart.py`, `test_disk_safety.py` and `test_scheduler_validation.py`; the notes of `storage.edge-cases` name the tests that failed on the code before.

## Consequences

- A create with a quota reads the quota groups and the subvolume list before the create, and the list and the check after it; listing and deleting volumes run the check as well, one host command for all ids.
- A quota group can outlive its subvolume on kernels that keep stale groups. When the newest subvolume was deleted and its id is given out again after a remount, the guard counts that id as existing: the create answers 409 and keeps the new volume without its quota. Removing the stale group on the host (`btrfs qgroup destroy 0/<id>`, or `btrfs qgroup clear-stale` where btrfs-progs has it) ends it.
- Volume drivers that serve a folder through FUSE or the network are not recognized as users; a pool's filesystem that stays mounted is still caught after the umount (ADR-0288).
- A delete that meets a new mount part way leaves the rest of the folder, and what it deleted is not in the trash. Where `/proc/self/fdinfo` does not exist (not Linux), the walk compares the devices only.
- Still open from ADR-0073: the restored copy of a volume's snapshot (`restore_<time>`) cannot be deleted in the web UI (`storage.restore-copies-deletable`).

## Considered options

- A root-only staging folder for new volumes, renamed into place with RENAME_NOREPLACE (ADR-0073): apps that run as root in their containers can still enter it, and the rename into place needs renameat2 on the host, which not every coreutils offers.
- The quota groups alone for the newest id: they count stale groups too, and the list adds a subvolume whose group was destroyed.
