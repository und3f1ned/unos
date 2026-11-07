---
status: accepted
date: 2026-11-06
area: storage
decided-by: agent
---

# ADR-0069: Volumes are top-level subvolumes, addressed by subvolume id

## Context

Creating a volume answered 422 in 1.0.0. btrfs-progs resolves a path argument, so a quota or a deletion by path acts on whatever the name leads to when the command runs, and SMB users and apps can create, rename and link entries in a pool's top folder. A quota or a deletion by path could therefore be redirected to another subvolume.

## Decision

- A volume is a btrfs subvolume directly in the pool's top folder whose name is a valid volume name (`_pool_volumes` in `supervisor/src/storage/pools.py`). Snapshots and deeper subvolumes are not volumes, and they are not offered as share folders.
- The Volumes dialog uses `GET`, `POST` (name and optional quota) and `DELETE /api/v1/storage/pools/{pool}/volumes[/{name}]`.
- The quota is set on the new subvolume's quota group by id (`btrfs qgroup limit <size> 0/<id>`), and only when the name leads to a subvolume newer than every subvolume that existed before the create; otherwise no quota is set, nothing that existed before is removed, and the answer is 409. When a quota is asked for and quotas are off on the pool, the answer is 409 with the hint to run `btrfs quota enable`, and nothing is created; a volume without a quota is created either way.
- A deletion finds the volume's id in the pool's own subvolume list, checks it against the filesystem, and is refused (409) while the volume or a folder in it is used by a running container (directly or through a local Docker volume that binds it), an SMB share or an NFS export; 503 when Docker cannot be asked.
- Tests: `supervisor/tests/test_volumes.py`.

## Consequences

- A rename or a symlink in the pool's top folder cannot move a quota or a deletion to another subvolume.
- Snapshots of a pool do not include its volumes (btrfs snapshots stop at subvolume boundaries).
- A deletion needs Docker to answer: without it, nothing is deleted.
- Edge cases left open are in ADR-0073.
