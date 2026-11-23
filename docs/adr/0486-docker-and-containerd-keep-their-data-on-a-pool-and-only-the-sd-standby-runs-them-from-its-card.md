---
status: accepted
date: 2026-11-23
area: device
decided-by: owner
supersedes: [ADR-0470]
---

# ADR-0486: Docker and containerd keep their data on a pool, never on the system disk, and only the SD standby runs them from its card

## Context

ADR-0470 moved the dev device, the BPI-W3, to its eMMC and kept Docker's data root there, so that
UNOS starts without the pool's hard disk. With Docker 29's containerd image store that was
`/var/lib/docker` (3.5 GiB: container layers, volumes, the supervisor's data volume) and
`/var/lib/containerd` (8.0 GiB of image layers) on a soldered chip that every pull, build and
container write wears (ADR-0214). ADR-0471 then made a data root on the system disk one of the SD
standby's conditions. On 2026-11-23 the owner said "Docker is always on the pool; only in an
emergency it is not", and refused Docker on the eMMC by default (in translation).

## Decision

- On every NAS, Docker's data root and containerd's root are on a pool, never on the system disk:
  the btrfs subvolumes `.docker` and `.containerd` of the pool (names that start with a dot, which
  the Storage page neither lists nor deletes), named by `data-root` in `/etc/docker/daemon.json`
  and by `root` in `/etc/containerd/config.toml`. The drop-ins
  `/etc/systemd/system/docker.service.d/data-root-on-pool.conf`
  (`RequiresMountsFor=` both subvolumes) and `containerd.service.d/root-on-pool.conf`
  (`RequiresMountsFor=` `.containerd`) keep them down while the pool is not mounted, and the
  pool's fstab line mounts it before Docker (`x-systemd.before=docker.service`). The BPI-F3 already
  had its data root on its pool (ADR-0372); its Docker uses the classic overlay2 store, so it has no
  `.containerd`.
- Only the SD card standby (ADR-0471) runs Docker from its card, and only while the NAS runs from
  the standby. ADR-0471's condition that Docker's data root be on the system disk no longer holds;
  the standby must take over when the pool that holds Docker fails, as it does for the eMMC
  (`system.sd-standby-boot` revises ADR-0471 with its own ADR).
- The BPI-W3 was moved on 2026-11-23: its pool `data` (btrfs on the hard disk `/dev/sda`) holds
  `.docker` and `.containerd`, copied with `rsync -aHAX` while Docker and containerd were stopped
  (88 s, 3.98 MiB written to the eMMC with the root remounted `noatime`); the old copies on the eMMC
  were deleted after a reboot check (130 MiB of ext4 metadata written, the root's use from 11 to
  3.1 GiB of 29).
- UNOS puts Docker on a pool by itself on a new installation (`system.docker-root-on-pool`); until
  that is built, `docs/INSTALL.md` tells the operator how to do it by hand.
- ADR-0470's other decisions stay, restated: the eMMC (`mmcblk0p1`, ext4, label `armbi_emmc`, from
  sector 32768) is the W3's system disk, booted by Armbian's loader at sectors 64 and 16384, with
  UGREEN's partitions wiped and no image kept; the SD card holds no system and no loader (a card
  with a loader boots before the eMMC) and is a data disk (ADR-0472) or the standby's card
  (ADR-0471); the write budget of ADR-0214 counts the eMMC (`UNOS_DEV_SYSTEM_DISK=mmcblk0`), and the
  scripts' `SD writes` lines mean the system disk's writes.

## Consequences

- Without the pool, neither Docker nor UNOS starts (as on the F3): the pool's disk is the NAS's
  weak point, which the SD standby covers once it handles that failure.
- The W3's hard disk does not spin down while Docker runs on it.
- A deploy's and an app's image layers no longer count against the eMMC; `device.docker-data-root-on-hdd`
  is done on the W3 once a deploy shows it.
- The supervisor's data volume is on the W3's only pool too, so `scripts/dev/scenario.sh offsite`
  skips there as on the F3 unless another pool exists (ADR-0485).
