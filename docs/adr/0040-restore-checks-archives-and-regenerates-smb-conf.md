---
status: superseded
date: 2026-11-05
area: backup
decided-by: agent
superseded-by: ADR-0319
---

# ADR-0040: Restore checks archives and regenerates smb.conf instead of installing the archived file

## Context

Superseded by ADR-0319: the same checks of the archive and the database, a backup holds no smb.conf, and none is read.

In 1.0.0 a restore ran `tar.extractall` without a filter and installed the archived `unos.db` and `smb.conf`, so a crafted archive could replace files on the host, and regular accounts could run a restore. Backups can be uploaded, so an archive is untrusted input.

## Decision

- A restore accepts only an allowlist of regular files within size limits (`_FILE_MEMBERS` and `MAX_TAR_BYTES` in `supervisor/src/backup/router.py`); links, devices, absolute paths and `..` abort it, and tarfile's `data` filter is applied as a second line of defence.
- The restored database replaces the live one only after it passes `PRAGMA integrity_check` and has a usable `users` table with an administrator and no triggers or views. The current database is copied first, and the copy into the live file uses SQLite's online backup API, so the connections open in the running supervisor stay consistent.
- An archived `smb.conf` is never installed: shares and their access are rows of the database, and `smb.conf` is rendered from them after the restore (ADR-0139, ADR-0144). An old backup whose shares were never imported gets them imported from its `smb.conf`, without guest access (ADR-0142).
- The other files of an archive, such as `fstab`, `scheduled_tasks.json` and the netplan files, are not restored.
- Tests: `supervisor/tests/test_backup_restore.py`, `supervisor/tests/test_backup_files.py`.

## Consequences

- A restore brings back the database and nothing else; scheduled tasks and network settings from a backup have to be set up again by hand.
- An uploaded backup is checked in one streaming pass before it is stored (ADR-0166).
