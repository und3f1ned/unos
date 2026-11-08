---
status: accepted
date: 2026-11-08
area: storage
decided-by: agent
---

# ADR-0262: The recovery runbook reads a pool from another Linux machine without writing to it, degraded when a disk is missing

## Context

When the NAS does not boot, after a failed system disk or board, its users still need the files on its pools. A pool is a plain btrfs filesystem labelled with the pool name and not encrypted (`supervisor/src/storage/pools.py`), so any Linux machine with btrfs-progs can read it; but the disks of a NAS that failed may be failing too, and a repair or a writable mount can lose what was still readable. This settles the runbook of the entry `system.emergency-runbook-and-cli`.

## Decision

- `docs/RECOVERY.md` is the runbook, linked from `docs/INSTALL.md` (Troubleshooting), `docs/USER_GUIDE.md` and `README.md`. It has two parts: reaching a pool's data from another Linux machine, and the emergency reset of an administrator (ADR-0261).
- The pool is found by its label with `btrfs device scan` and `btrfs filesystem show`, and mounted only read-only, with `-o ro,rescue=nologreplay` (`nologreplay` on Linux before 5.9): a plain `ro` mount still replays btrfs's log, which writes to the disks. With a disk missing, `degraded` is added. Which disks a profile can miss is listed for every profile of `PROFILE_MAP`: one for raid1 and raid10, two for raid1c3, three for raid1c4, none for single and raid0.
- It says where UNOS keeps things on a pool: volumes are the subvolumes at its top (ADR-0069); snapshots are read-only, in the `.snapshots` folder of the pool and of each volume, and the copies that Restore made are `restore_<date>_<time>`; the trash is `.unos-trash` (ADR-0259); app data is in `apps/<app>` (ADR-0039). The share folders are named by the `[name]` sections and `path =` lines of `/etc/samba/smb.conf` on the old system disk, or by the table `smb_shares` of the database `unos.db` in the Docker volume `deploy_unos-data`. Files written over SMB belong to the SMB accounts' user ids and group 70000, and share folders have mode 2770: they are copied as root.
- No step writes to the pool: no writable mount, no `btrfs check --repair`, `btrfs rescue`, scrub or balance. When the filesystem does not mount read-only, the runbook stops and sends the reader to the btrfs documentation, after copying the disks first. Data is copied to another disk with `rsync`.
- Back on a UNOS host, the Storage page lists the pool by its label, and Mount mounts it and adds it to the host's fstab (ADR-0034); users and shares come back only with a backup of the database.
- `supervisor/tests/test_emergency.py` keeps the runbook in step with the code: every mount in it is read-only without log replay, and the names it gives are those of the code (`SNAPSHOT_DIR_NAME`, the trash, `PROFILE_MAP`, the pools folder, the share table, the data volume, the reset command and its rules).

## Consequences

- The procedure itself runs only on a Linux machine with btrfs and real disks, or loop devices: a device check of the entry, not a test that runs on the workstation.
- A pool on single or raid0 cannot be read with a disk missing.
- Without the log replay, the last writes before the NAS stopped (up to the 30-second commit interval of btrfs) may be missing from the copy; a later read-write mount replays them.

## Considered options

- A plain `ro` mount: simpler, and it shows those last writes, but it writes to disks that may be failing.
- A recovery script in the repository: it would have to run on machines UNOS does not know, without its checkout; standard btrfs-progs commands in a document can be read from a phone and typed anywhere.
