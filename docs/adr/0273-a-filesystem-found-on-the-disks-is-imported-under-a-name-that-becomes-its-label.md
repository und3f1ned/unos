---
status: accepted
date: 2026-11-08
area: storage
decided-by: agent
---

# ADR-0273: A btrfs filesystem found on the disks is imported at its top level, under a name that becomes its label

## Context

`storage.import-existing-pool` asks that a btrfs filesystem already on the host's disks become a pool without formatting. UNOS names a pool by its filesystem's label (ADR-0034), so a filesystem without a label, with one that is not a pool name, or with one that another filesystem has too (two disks named `data` on two hosts) could not be mounted as a pool at all, and the Storage page did not show it. A filesystem made elsewhere may also mount a subvolume other than its top level by default, where volumes (ADR-0069) and share folders are refused.

## Decision

- A filesystem whose label is a valid pool name that no other btrfs filesystem of the host has is a pool already: the Storage page lists it, not mounted, and Mount imports it by UUID with its managed fstab entry (ADR-0034). The Mount dialog says that the disks are not formatted.
- Every other btrfs filesystem that is neither the host's system filesystem nor mounted on the host is listed by `GET /api/v1/storage/filesystems/importable`, with why it is no pool (`label_problem`: `missing`, `invalid` or `duplicate`), and under Found on the disks on the Storage page. `POST /api/v1/storage/filesystems/{uuid}/import` with `{"name": ...}` imports it: it mounts the filesystem by UUID at `/mnt/pools/<name>`, sets the name as its label through the mount (`btrfs filesystem label <mountpoint> <name>`, which the kernel writes), has udev read its devices again so that lsblk shows the label, and writes the managed fstab entry. Both routes are administrator-only with the storage router (ADR-0030); the import applies smb.conf afterwards like the other pool changes (ADR-0248).
- The import is refused with 409, and nothing is changed, for a filesystem that holds the host's system, is mounted on the host or still in another mount namespace, misses a device, is a pool under its label (Mount imports it), or that UNOS manages under another name; for a name that another filesystem of the host has as its label (any type, from btrfs and lsblk: with two filesystems of one label, `LABEL=` in the host's fstab or `root=` of its kernel could name either), that UNOS manages for another filesystem, or whose mountpoint is in use, in an fstab line UNOS does not manage, or holds files.
- A pool is mounted only at its top level: when a mount by Mount, Import or Create shows another subvolume (`btrfs inspect-internal rootid`, the default subvolume was changed with `btrfs subvolume set-default`), UNOS unmounts it again and answers 409, the fstab is as it was, and an import has not changed the label yet. The message says how to make the top level the default subvolume on the host.
- The import needs no typed confirmation (ADR-0035 is for irreversible operations): it formats nothing, and the label is the one thing it changes on the disks. Unlike a mount, it starts no stopped apps: those of an earlier pool with the same name keep their data on that pool's filesystem.
- Code: `importable_filesystems`, `import_pool`, `_check_import` and `_mount_and_persist` in `supervisor/src/storage/pools.py`, the routes in `supervisor/src/storage/router.py`, Found on the disks and the Import dialog in `webui/src/routes/storage/+page.svelte`. Tests: `supervisor/tests/test_pool_import.py`, `supervisor/tests/test_pool_persistence.py` (TestMountPool), `webui/tests/storage.spec.ts` (Pool import).

## Consequences

- After a reinstall, the pools UNOS made come back with Mount; disks from another system become pools under a name the administrator chooses; of two filesystems with one label, one is imported under another name and the other is then a pool under its label.
- The import changes the label of a filesystem made elsewhere: a system that mounts it by `LABEL=` needs the new label.
- A filesystem with a missing device is not imported (no degraded mount), nor one whose default subvolume is not its top level, until the administrator resets it on the host (`btrfs subvolume set-default 5` on a mount of it).
- btrfs on md, LVM or LUKS is offered only once the host has assembled or opened it: `btrfs filesystem show` reads only the devices that exist.

## Considered options

- Mount every pool's top level with `subvolid=5` in the mount and the fstab entry: it would import such filesystems as they are, but it changes the entry of every pool (ADR-0034); not chosen for this feature.
- Keep a pool's name apart from its label, in the database or the fstab marker: every part of UNOS finds pools by label; not chosen.
- Set the label by device before mounting: btrfs-progs then writes the superblocks itself, also of a filesystem that another mount namespace still has mounted, and a refused mount would leave the new label behind.
