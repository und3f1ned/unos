---
status: accepted
date: 2026-11-06
area: system
decided-by: agent
---

# ADR-0171: The supervisor keeps volatile files in RAM

## Context

The supervisor container's writable layer and the `unos-data` volume are on the system disk, on many boards an SD card that writes wear out (ADR-0214). Several things wrote there needlessly: the temporary copies of backups, restores and `update.sh`'s database backup, the scrub status that btrfs rewrites every 5 seconds for hours while a scrub runs, about 1.8 MB of Python bytecode in every new container, the RAID monitor's status file after every check, and every upload over 1 MiB, twice.

## Decision

- `/tmp` in the supervisor container is a tmpfs of at most 2 GiB (`noexec,nosuid,nodev,size=2g`), and `TMPDIR=/tmp` is set so that Python's tempfile and SQLite put their temporary files there (`deploy/docker-compose.yml`; the application also pins the temporary directory at import). When it is full, operations fail instead of spilling to the disk: a backup fails with "No space left on device", a restore answers HTTP 507, and the database copy of `deploy/update.sh` fails, after which update.sh asks whether to update without a backup.
- `/var/lib/btrfs`, where btrfs keeps the scrub status, is a 1 MiB tmpfs: the result of the last scrub is forgotten when the supervisor restarts.
- The image sets `PYTHONDONTWRITEBYTECODE=1` (`supervisor/Dockerfile`): no `.pyc` files are written, and the supervisor starts about 0.1 to 0.2 seconds slower on an RK3588 board.
- The RAID monitor writes `/data/raid_status.json` only when the state of a pool changes (`save_raid_status` in `supervisor/src/health/router.py`); `GET /api/v1/health/raid-monitor/status` shows the latest check.
- `deploy/update.sh` makes its consistent copy of the database in the container's `/tmp`, never in the writable layer or `/var/tmp`, and stores the backup itself (`unos.db` and `data.tar.gz`) in `/var/lib/unos/backups` on the host (ADR-0049).
- Certificate uploads reach openssl on standard input, and uploads go straight into their folder (ADR-0169).
- Tests: `supervisor/tests/test_deploy_scripts.py` (the tmpfs limit against the backup limits, the scrub tmpfs, TMPDIR), `supervisor/tests/test_temp_files.py` (the pinned temporary directory).

## Consequences

- Making or restoring a backup takes RAM while it runs: about the size of the database for a backup and twice that for a restore. A test keeps the 2 GiB limit above the largest restore plus a backup made meanwhile.
- The limit is larger than the RAM of boards at the documented 1 GB minimum (ADR-0177).
- Losing the last scrub result at a restart is accepted; it was already lost when the container was recreated.
