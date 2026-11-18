---
status: accepted
date: 2026-11-18
area: deploy
decided-by: agent
supersedes: [ADR-0177]
---

# ADR-0406: The supervisor's /tmp tmpfs is 192 MiB and a backup may hold a database of 48 MiB

## Context

ADR-0171 made the supervisor's `/tmp` a tmpfs of 2 GiB, sized for three times `MAX_TAR_BYTES` (640 MiB). UNOS documents 1 GB of RAM as its minimum (`README.md`, `docs/INSTALL.md`), so on such a board a large backup or restore could exhaust the memory before the tmpfs was full (ADR-0177, which left the choice to the owner). The owner delegated it: "25 percent of RAM, or a lower fixed cap with a lower backup size limit", asking above all that it be safe.

## Decision

A lower fixed cap, with lower backup limits (`supervisor/src/backup/router.py`). It replaces the 2 GiB of ADR-0171 and the 2 GiB that ADR-0337 counts the off-device copy against.

- `/tmp` is `size=192m` in both compose files: 112 MiB for the largest legitimate use, a restore (the database, at most `MAX_DB_BYTES`, ADR-0364) and a backup made meanwhile (a copy of the database and the small files, at most `MAX_TAR_BYTES`), and 80 MiB for the pack files (about 16 MiB each, a few at a time) of an off-device copy that runs meanwhile (ADR-0337). That is 19% of 1 GB. No `install.sh` change: a fixed size needs no knowledge of the RAM.
- `MAX_DB_BYTES` is 48 MiB, `MAX_TAR_BYTES` and `MAX_ARCHIVE_BYTES` 64 MiB (were 512, 640 and 256). The archive limit leaves room for an incompressible database and the small files, so that a backup that is made also restores, and it bounds the RAM of an encrypted restore (the file and its decrypted copy) and of an encrypted or off-device backup, which is built in memory. UNOS's database holds accounts, shares, settings and the audit log, a few MiB.
- A backup over the limit is refused early, with the limit in the message, wherever a backup's size is checked: Create Backup, the scheduled backup task and the off-device copy's configuration archive refuse a database over 48 MiB before it is copied (`copy_database_for_backup`: the live file first, the copy again after it, for a database that grew meanwhile), Create Backup answering 413 "The database is 80.0 MiB, over the 48 MiB limit of a backup"; an upload over 64 MiB is refused as it arrives ("Backup file is too large: the limit is 64 MiB"), as is an archive that decompresses past 64 MiB or holds a database over 48 MiB. The restore checks of ADR-0364 say the same.
- The step of `backup.upload` that names the file size limit now says 64 MiB.
- Rejected: `size=25%`, 256 MiB on a board with 1 GB: it would not hold the old limits, so the limits would be lowered anyway, and the tests could then compare the limits only with an assumed RAM size.
- The restore keeps its streaming pass (ADR-0364), so the halving that ADR-0177 mentioned is already done.
- Tests: `supervisor/tests/test_deploy_scripts.py` (the size, at most a quarter of 1 GB, holds the largest use), `test_backup_files.py` and `test_scheduler_validation.py` (the early refusals), `test_backup_upload_check.py`, `test_upload_streaming.py`, `test_backup_restore.py` (the messages).

## Consequences

- A database over 48 MiB (an audit log that grew very large) can no longer be backed up or restored until it is smaller; the message names the limit. A backup made earlier, within the old limits but over the new, is refused by upload and restore.
- The cache that restic keeps in `/tmp` for an S3 store is not bounded: a large repository can fill the tmpfs, and its copy then fails with "No space left on device" instead of taking the board's memory. Moving or bounding that cache is a later decision.
- `update.sh`'s database copy into `/tmp` fails for a database over 192 MiB, as when the tmpfs is full (ADR-0171).
- The dev device (8 GB) is affected in the same way as a small board; its database is far below the limit, which the device check shows.
