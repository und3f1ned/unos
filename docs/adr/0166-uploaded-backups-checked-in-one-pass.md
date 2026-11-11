---
status: superseded
superseded-by: ADR-0345
date: 2026-11-06
area: backup
decided-by: agent
---

# ADR-0166: Uploaded backups are checked in one streaming pass that writes nothing

## Context

Superseded by ADR-0345: an encrypted upload that is gzip data is refused too, and the rules of the upload check are restated there.

An uploaded backup is untrusted input that must be checked before it is listed. Python's tarfile reads some headers in full before anything can be checked: a 200 KiB upload could make the supervisor hold 740 MiB of memory or work for minutes, some malformed archives produced HTTP 500, and there was no size limit. The check must not leave temporary copies on the system disk (ADR-0214).

## Decision

- `_BackupUpload` in `supervisor/src/backup/router.py` receives the file as it arrives (ADR-0169) and refuses it as soon as it grows past 256 MiB (`MAX_ARCHIVE_BYTES`): HTTP 400 "Backup file is too large". A request that declares a longer body than that plus 1 MiB is answered with 413 before anything is read (ADR-0062).
- Once complete, `_inspect_archive` reads a plain backup in one pass over its gzip stream (tarfile mode `r|`): each member is checked as it comes, only `manifest.json` is kept, in memory, and nothing is written, in /tmp or anywhere. It applies the limits and checks of a restore: at most 640 MiB decompressed and 256 entries, 512 MiB for the database and 1 MiB for each of the other files a backup holds, no links, special files or duplicate entries, and a manifest.
- Malformed archives get HTTP 400 ("Invalid backup: corrupt archive", "Backup archive is too large when decompressed"), never 500.
- What UNOS backups never contain and tarfile would read in full first is refused: sparse files (every GNU format), global PAX headers, and more than 1 MiB of GNU long names and PAX extended headers in all.
- An encrypted upload (`.tar.gz.enc`) cannot be read without its passphrase: only its minimum length is checked, and it is refused when it is in fact a plain backup. Restore checks its content after decrypting it.
- The backup list reads the manifest of each unencrypted backup within the same limits (`_manifest_version`), because a file stored by an earlier version can hold what its upload check let through.
- Tests: `supervisor/tests/test_backup_upload_check.py`, `supervisor/tests/test_backup_files.py`.

## Consequences

- A backup larger than 256 MiB cannot be uploaded.
- An archive made by another tool with sparse files or a global PAX header is refused even when its content would restore.
- The check costs one decompression pass of CPU time per upload, and no disk writes beyond the uploaded file itself.
- A gzip file that is not a backup is still stored when it is uploaded under the encrypted name (ADR-0167).
- Restore keeps its own checks of the files it extracts (ADR-0040); the upload check applies the same limits before a backup is listed.
