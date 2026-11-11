---
status: accepted
date: 2026-11-11
area: storage
decided-by: agent
supersedes: [ADR-0070]
---

# ADR-0342: Pool unmount and delete are refused when Docker cannot be asked, respect Docker volumes, and odd names do not break storage

## Context

ADR-0070 let an unmount go ahead when Docker could not be asked which containers use a pool, and left it to the check after the umount to mount the pool again while the filesystem stayed in use (ADR-0035). That check takes a host mount of the same filesystem elsewhere for the administrator's own, and a Docker volume of the local driver that binds a folder of the pool is such a mount, at Docker's folder for the volume: the pool was reported unmounted while a container could still write to it, a case that ADR-0073 left open (`storage.edge-cases`, step 3). This ADR restates ADR-0070 with that case closed.

## Decision

- Unmounting or deleting a mounted pool is refused with 409 while a running container uses it: a mount from the pool's mountpoint or below it, a local Docker volume that binds a folder there, or the app label of the pool (`_containers_using` and `_unmount_pool_target` in `supervisor/src/storage/pools.py`).
- When Docker cannot be asked about the running containers or their volumes (no answer, or a volume that cannot be inspected), the unmount and the delete are refused with 503 (`DockerUnavailableError`, the storage API's answer when it is not known whether a container uses a pool or a volume) before anything changes: no umount, no fstab write, no unmount in a mount namespace. A pool that is not mounted is still deleted without asking Docker.
- After the umount, the check of ADR-0035 and ADR-0288 still mounts the pool again when the filesystem stays in use where Docker does not tell (another runtime, a container that binds a parent folder of the pool).
- Output of btrfs, lsblk and findmnt is decoded with U+FFFD for bytes that are not UTF-8. A subvolume name with U+FFFD is never a volume (ADR-0069); disk models and filesystem labels are only shown.
- Pool health is `unknown` when the device error counters cannot be read, never `healthy` (ADR-0036).
- Tests: `supervisor/tests/test_pool_persistence.py`, where `test_without_docker_the_pool_stays_mounted_and_nothing_changes`, `test_docker_volume_that_cannot_be_inspected_leaves_the_pool_mounted`, `test_without_docker_a_container_that_binds_the_pools_mountpoint_is_refused` and `test_unmount_and_remove_without_docker_answer_503_and_change_nothing` failed on the code of ADR-0070; `supervisor/tests/test_volumes.py`, `supervisor/tests/test_storage.py`, `supervisor/tests/test_health_smart.py`.

## Consequences

- An administrator stops the containers that use a pool before unmounting or deleting it. While Docker does not answer, a mounted pool cannot be unmounted or removed in UNOS: the message says to try again when Docker is running.
- A name that is not UTF-8 is shown with replacement characters and cannot be selected as a volume.
- The comparison of a Docker volume's bound folder with the pool is still lexical (ADR-0073).

## Considered options

- Telling a Docker volume's host mount from the administrator's own by its path below `/var/lib/docker/volumes`: Docker's data root can be moved, and other volume drivers mount elsewhere, so such a mount could still be taken for a deliberate one.
