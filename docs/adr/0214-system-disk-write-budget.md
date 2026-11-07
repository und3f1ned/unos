---
status: accepted
date: 2026-11-07
area: device
decided-by: owner
---

# ADR-0214: Every device operation keeps system-disk writes minimal and reports them

## Context

The dev device's system disk is `mmcblk1`, a 30 GB SD card by the audit's count, which the owner calls an eMMC; it wears out and cannot be replaced. The audit found Docker using 12 GB of it and data landing on it while the pools were not mounted. The owner said "Stop abusing the eMMC!!!!" and "Delete everything unneeded; maximum protection of the eMMC memory, it cannot be replaced later" (2026-11-06).

## Decision

- In UNOS: volatile data stays in RAM (ADR-0171), nothing is written periodically, writes happen only on real state changes, and a start that changes nothing writes nothing (for SMB, ADR-0082 and ADR-0094).
- On the device, every operation measures the sectors written to the system disk (`/sys/block/<disk>/stat`, after a `sync`) and reports them. `scripts/dev/sd.sh sectors` prints the counter and `scripts/dev/sd.sh measure <label> -- <command>` the MiB a command wrote; every step of `scripts/dev/deploy.sh` and every run of `scripts/dev/linux-tests.sh` reports its writes, and `scripts/dev/check.sh` shows the writes since boot and the card's wear data where the card reports it. The disk is `UNOS_DEV_SYSTEM_DISK` from `dev.env`, else the disk of the device's root filesystem.
- Builds happen off the device where possible: the web UI's static files are built on the workstation and only the image's final stage on the device (ADR-0184).
- Scratch data lives in RAM (`/dev/shm`) or on the data pool, never on the system disk. Read-only work on the device runs with its root filesystem read-only and without access-time updates (ADR-0221); the API relay keeps its login in RAM (ADR-0222); the backups and rollback copies of a deploy are kept on the workstation (ADR-0223).
- No container is created and no image pulled without need (ADR-0183); creating one, pulling and pruning images are owner actions (ADR-0211).
- The cost of an option is measured before it is chosen.

## Consequences

- Measured so far: the idle device wrote about 8 KiB per minute in an earlier measurement (kernel threads only) and 16 KiB per minute (32 sectors) on 2026-11-07; idle UNOS containers write nothing; the slice 1 deploy wrote 38 MiB, the slice 2 deploy 45 MiB of image layers plus 4 MiB for its checks; on 2026-11-07 a 46-minute session of device checks wrote 0.953 MiB, idle background included, and an API call with a cached login about 0.020 MiB. `PROGRESS.md` keeps the totals of the current phase (ADR-0204).
- Device work takes more steps: builds are split between the workstation and the device, checks avoid containers, and tests run in RAM (ADR-0187).
- Some writes remain outside UNOS's control, such as Samba's passdb at failed sign-ins (ADR-0194) and Docker's image layers at a deploy.
