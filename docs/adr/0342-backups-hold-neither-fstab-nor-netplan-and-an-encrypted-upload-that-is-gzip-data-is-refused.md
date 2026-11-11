---
status: accepted
date: 2026-11-11
area: backup
decided-by: agent
---

# ADR-0342: Backups hold neither fstab nor netplan, and an encrypted upload that is gzip data is refused

## Context

ADR-0167 deferred two backup cases of slice 1, which the ledger entry `backup.edge-cases` takes
up. Backups copied the supervisor container's `/etc/fstab`, and those made by hand its
`/etc/netplan/*.yaml`: files of the container, which describe nothing of the host, whose fstab
with the pool entries is not mounted into it. A restore never restored them and warned "fstab is
included in the backup but is not restored automatically". And a gzip file that is no backup,
uploaded as `.tar.gz.enc`, was stored as encrypted (ADR-0166) and failed only at its restore,
with "Invalid passphrase or corrupted backup".

## Decision

- No backup holds a file of `/etc`: `_collect_backup_content` of
  `supervisor/src/backup/router.py` (Create Backup, encrypted backups through the API, the archive
  of the off-device copy of ADR-0337) writes the manifest and the database, and
  `_create_scheduled_backup` of `supervisor/src/scheduler/router.py` also the scheduled tasks.
  Not the host's fstab either: a restore restores only the database (ADR-0319), and the pools'
  entries are written by Mount and Import (ADR-0283).
- `BackupConfig` loses `include_pools` and `include_network`, which only added those files, and
  so does the web UI's type in `webui/src/lib/api/backup.ts` (ADR-0207). Its four other
  `include_` options, which never selected anything, are left to a change of their own.
- A restore takes from an archive only `manifest.json`, `unos.db` and `scheduled_tasks.json`
  (`_FILE_MEMBERS`). The `fstab` and `netplan` members of a backup that still holds them are
  ignored like any other name: not extracted, without a size limit of their own beyond the 640 MiB
  of the decompressed archive, and without a warning. No compatibility beyond what a restore of
  such a backup needs (ADR-0207). The one warning of a file not restored is that of
  `scheduled_tasks.json`.
- An upload named `.tar.gz.enc` that is gzip data is refused with 400 "This file is not an
  encrypted backup: it is an unencrypted gzip file" (`_refuse_plain_archive`); a plain backup is
  still refused with "This backup is not encrypted: upload it as .tar.gz". Gzip data means that
  the whole file decompresses as gzip (`_is_gzip`): header, deflate data, CRC-32 and length,
  with zero padding allowed, or more than `MAX_TAR_BYTES` decompressed without an error. This
  extends the check of encrypted uploads of ADR-0166, which left the case to ADR-0167.
- Tests: `supervisor/tests/test_backup_files.py` (`test_a_backup_holds_neither_fstab_nor_netplan`),
  `supervisor/tests/test_backup_restore.py` (`test_restore_database_and_shares`),
  `supervisor/tests/test_backup_upload_check.py` (`test_gzip_data_named_as_encrypted_is_refused_at_the_upload`,
  `test_an_encrypted_backup_whose_salt_starts_like_gzip_is_accepted`, the accepted case
  `fstab-and-netplan-of-an-older-backup`), `supervisor/tests/test_scheduler_validation.py`
  (`test_a_backup_task_stores_encrypted_backups`).

## Consequences

- A backup does not help to set up the host's fstab or network again: the pools are mounted or
  imported on the Storage page, and the network is set on the host.
- A genuine encrypted backup fails both gzip reads at its first bytes; a gzip file that is no
  backup is read twice, by `_inspect_archive` until it fails and by `_is_gzip` to its end.
- A gzip file cut short, or followed by other data, is not gzip data to its end: uploaded as
  `.tar.gz.enc` it is still stored, and fails at its restore. Telling it from encrypted data
  would rest on deflate data that only happens to decode, not on its CRC-32.
- ADR-0167's two cases of file manager uploads, a stalled client and the leftovers on
  filesystems without O_TMPFILE, stay open there.

## Considered options

- Back up the pool entries of the host's fstab through `host.read_host_file`: no restore would
  read them (ADR-0319).
- Refuse every file that starts with gzip's magic number: the salt of an encrypted backup is
  random, so one genuine encrypted backup in 65,536 would be refused.
- Keep `fstab` and `netplan` in the restore's allowlist without the warning: the restore would
  extract files that nothing reads.
