---
status: accepted
date: 2026-11-13
area: device
decided-by: owner
---

# ADR-0372: The BPI-F3 keeps its pool, Docker's data root and the data of its Home Assistant and Frigate stack on its two SSDs

## Context

The owner asked to deploy UNOS on the Banana Pi BPI-F3 (ADR-0370), then "all the custom Home
Assistant and Frigate containers, following the rules of UNOS", and decided that "the pool must
be on the 2 SSDs, with all the apps' data and the rest there", Docker's data "also on the SSD
pool". The board had run the stack (github.com/und3f1ned/bpi-f3-frigate-ha: Home Assistant,
Frigate, its detector on the K1's NPU and mosquitto, from images built on the board) from an md
RAID1 with ext4 on its two SATA SSDs, behind a JMB582 M.2-to-SATA adapter. Its microSD card has
15 GB, too little for the stack's images (about 5 GB) and its weekly rebuild of Frigate (7 to 8
GB more). ADR-0228 keeps Docker's data root on the first dev device's SD card so that UNOS starts
without the pool's disk; the App Store cannot install the stack's images
(`apps.spacemit-k1-hardware-acceleration`, `apps.compose-stacks`).

## Decision

- The pool `data` is a btrfs RAID1 that UNOS made (`create_pool` of
  `supervisor/src/storage/pools.py`) on the two SATA SSDs, in place of the md RAID1; its fstab
  entry is UNOS's, with TRIM, as for every SATA disk (ADR-0370).
- Docker's data root is the subvolume `.docker` of the pool (`/etc/docker/daemon.json`), with
  overlay2 on btrfs, and the drop-in `/etc/systemd/system/docker.service.d/data-root-on-pool.conf`
  (`RequiresMountsFor=/mnt/pools/data/.docker`) keeps Docker down while the pool is not mounted.
  A volume's name starts with a letter or a digit (`VOLUME_NAME_PATTERN`), so the Storage page
  neither lists nor deletes `.docker` as a volume.
- The stack stays its own compose project (`~/k1-stack/deploy` on the board), with a
  `compose.override.yaml` that keeps UNOS's rules for the data of apps: each app's data below
  `apps/<app>` of the pool (ADR-0318: `apps/homeassistant`, `apps/frigate`, `apps/k1det`,
  `apps/mosquitto`), and Docker's log rotation of 10 MB and 3 files (ADR-0330). The pool's
  mountpoint is immutable while it is not mounted (ADR-0034).
- ADR-0228 stays for the first dev device.

## Consequences

- When the pool does not mount at boot, neither Docker nor the web UI of UNOS starts: the
  BPI-F3 is looked at over ssh then. That is what ADR-0228 avoids on the first dev device.
- The microSD card holds the system only: from the boot to the start of every container after
  the reboot of 2026-11-13, 3.2 MiB were written to it.
- UNOS shows the stack's containers on its Containers page; it refuses to unmount the pool while
  they use it (ADR-0288), and the unmount fails while Docker runs from it.
- The stack's own `update.sh` rebuilds its images into the pool, and `docker compose` reads the
  override there too.
