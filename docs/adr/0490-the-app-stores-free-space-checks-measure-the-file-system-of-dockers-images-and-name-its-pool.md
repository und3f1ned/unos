---
status: accepted
date: 2026-11-24
area: apps
decided-by: agent
---

# ADR-0490: The App Store's free-space checks measure the file system of Docker's images and name its pool

## Context

ADR-0381 has an install keep 2 GiB free on "the system disk, where Docker keeps the images", read as
the lower `df` of Docker's data root and a fixed `/var/lib/containerd`; ADR-0408's update plan reads
the same, and the fields of ADR-0408 and ADR-0415 and the rows of ADR-0437 call the place of the
images the system disk. Since ADR-0486 and ADR-0487 put Docker's data root and containerd's root
on a pool (`/mnt/pools/<pool>/.docker`, `.containerd`), that fixed folder measured the system disk
wherever it stayed there, where no image lives any more, and the texts called the pool the system
disk. This ADR changes those parts of ADR-0381, ADR-0408, ADR-0415 and ADR-0437; the rest of each
stays.

## Decision

- `images_root` of `supervisor/src/system/docker_data.py` names the folder whose file system holds
  Docker's images, by the same rule as GET /system/docker-data: containerd's root (`root` of
  `/etc/containerd/config.toml`, `/var/lib/containerd` without one) when Docker's info names the
  containerd image store and the host runs `containerd.service`, else Docker's data root as Docker's
  info names it.
- `image_store` of `supervisor/src/apps/preflight.py` reads `df` of that one folder on the host and
  names the place: "the pool <name>" when the folder is below a pool's mountpoint (`pool_of`), else
  "the system disk". The install's check (`check_space`) and the update plan (`apps/updates.py`) use
  it, and their 409 texts name the place: "Jellyfin's images need about 2.8 GiB on the pool data,
  which has 1.5 GiB free; at least 2 GiB must stay free." ADR-0381's floor stays: 2 GiB free after
  the images that Docker lacks, by their PinnedImage estimates: a full pool fails the apps' writes
  as a full system disk fails the system's. A host without the folder is logged and not checked, as
  is one where containerd's service or configuration cannot be read and Docker's data root is
  missing too (`test -e`): Docker's socket without Docker's host, the compose test stack, which has
  no systemctl. Otherwise an unreadable configuration, info or `df` answers 500 with "Could not
  read the free space where Docker keeps its images".
- The API names the images, not the system disk: the catalog's `system_disk_bytes` becomes
  `images_bytes`, and the update plan's `system_disk_bytes` and `system_disk_free_bytes` become
  `images_bytes` and `images_free_bytes`, with `images_pool` (null for the system disk). The update
  dialog's row is named "Pool <name>" or "System disk", and the details' row "Disk space"
  (`apps.detail.imagesDisk`, `appUpdates.imagesPool`, in English and Russian).
- Tests: `TestImageSpace` of `supervisor/tests/test_apps.py`, `TestThePlan` of
  `test_app_updates.py`, the Immich space test of `test_app_stacks.py`; `apps-update.spec.ts` and
  `apps-companions.spec.ts`.

## Consequences

- On the BPI-W3 the check measures the pool `data` (containerd's root), on the BPI-F3 the same pool
  (Docker's data root, classic overlay2 store); the eMMC's free space no longer refuses an install.
- With the containerd image store, Docker's data root (container records, logs) is not measured: the
  images and the containers' layers are in containerd's root.
- The check reads `systemctl show containerd.service` and containerd's config.toml on the host when
  Docker uses the containerd image store: two more host commands per install and update plan.
