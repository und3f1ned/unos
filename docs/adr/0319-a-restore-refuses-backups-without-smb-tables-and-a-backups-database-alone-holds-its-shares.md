---
status: superseded
superseded-by: ADR-0439
date: 2026-11-10
area: shares
decided-by: agent
supersedes: [ADR-0040, ADR-0077, ADR-0139, ADR-0142, ADR-0143]
---

# ADR-0319: A restore refuses backups without SMB tables, and a backup's database alone holds its shares

## Context

Superseded by ADR-0439: only in part, the tables: they come from Alembic migrations, a change to an existing table is a revision, and the start brings the database to the head; every other rule here stays in force.

ADR-0077 and ADR-0139 created the SMB tables in the database of a backup that had none, of a
release before shares were stored in the database, ADR-0142 and ADR-0040 imported its shares
from the archive's smb.conf, and ADR-0143 kept the option `include_shares` that put that copy
into every backup. UNOS has no such releases to restore from (ADR-0207): the ledger entry
`backup.no-old-backup-import` asks to refuse their backups, to remove the import from the
archive, and to settle what a database of this release without the import marker restores from.
The agent took it up under the owner's delegation ("work on through the features, ask no
questions; I allow everything", in translation, 2026-11-09). This ADR restates the rules of
the five ADRs that stay.

## Decision

- The archive (from ADR-0040): a restore accepts only an allowlist of regular files within size
  limits (`_FILE_MEMBERS` and `MAX_TAR_BYTES` in `supervisor/src/backup/router.py`); links,
  devices, absolute paths and `..` abort it, tarfile's `data` filter is a second line of
  defence, and other names are ignored. `fstab`, `scheduled_tasks.json` and the netplan files
  are not restored. No smb.conf is a member: a backup, manual or scheduled, holds none, the
  option `include_shares` is gone, and an smb.conf in an archive is ignored, never read and never
  installed. Shares and their access are rows of the database, and smb.conf is rendered from them
  after the restore (ADR-0144).
- The database (from ADR-0040 and ADR-0139): the extracted database replaces the live one only
  after it passes `PRAGMA integrity_check` and has a usable `users` table with an administrator
  and no triggers or views (`_verify_database`). Before the swap, in the restore's temporary
  directory in RAM (`_prepare_smb`): `service.check_smb_tables` checks the four SMB tables and
  creates none: a database that lacks one of them is refused with 400 "unos.db in the backup
  lacks SMB tables (...): it was made by an older UNOS, whose backups this one does not
  restore", and one with a table of an unknown layout (a missing column, or an extra NOT NULL
  column without a default) too; the SMB accounts of the live database are read
  (`accounts.snapshot_for_restore`); one transaction on the extracted file decides by the import
  marker whether the database is authoritative (`service.restored_smb_authoritative`), then
  applies the share rules (`service.prepare_restored_shares`) and the account rules
  (`accounts.prepare_restored_accounts`, ADR-0141). Any failure rolls it back, and the restore
  fails with the live database unchanged. The current database is copied first, and the copy
  into the live file uses SQLite's online backup API.
- The share rules (from ADR-0142): an authoritative database keeps its rows; shares whose folder
  breaks a path rule lose their access (ADR-0145), and guest shares are kept with the warning
  "Guest access is on for: X (as in the backup)" and an audit entry at WARNING. A database with
  the tables but without the import marker (its import never ran or failed) is not
  authoritative: its share and access rows are deleted, and the import after the restore reads
  the live smb.conf (`pending_import_source` 'live'); it never turns guest access on, with the
  note "Guest access was not restored".
- The lock (from ADR-0139): the restore endpoint holds `service.smb_lock()` from before its
  worker thread starts until the post-restore step returns; the locked part runs in its own task,
  awaited through `asyncio.shield`. After the swap and `init_db`,
  `service.after_database_restore_locked` runs the startup path (ADR-0138); an exception there
  becomes the warning "SMB state is repaired at the next start". When the pool list cannot be
  read, the restore of a database that keeps its shares is refused with 503 before anything
  changes (ADR-0149).
- The tables (from ADR-0077): `init_db` creates the four SMB tables wherever they are missing,
  at startup. No existing table or column is ever altered: no `ALTER TABLE`, no column added to
  `users`, and no migration framework (ADR-0022). The model no longer has
  `smb_state.pending_import_conf`, and its CHECK on `pending_import_source` takes only 'live':
  both change new tables only. A database that has the column keeps it, nullable and unread,
  and its wider CHECK; no SQL of `supervisor/src` names the column or selects every column of
  `smb_state`, and `check_smb_tables` accepts the extra nullable column.
- Tests: `supervisor/tests/test_backup_restore.py` (TestRestoredSchema, TestNotAuthoritative,
  TestAccountStep, TestCrashSafety, `test_a_backup_holds_no_smb_conf`),
  `supervisor/tests/test_scheduler_validation.py` (`test_a_backup_task_stores_encrypted_backups`),
  `supervisor/tests/test_backup_files.py`, `supervisor/tests/test_share_import.py`,
  `supervisor/tests/test_smb_models.py`.

## Consequences

- A restore brings back the database and nothing else; scheduled tasks and network settings
  from a backup have to be set up again by hand. An uploaded backup is checked in one streaming
  pass before it is stored (ADR-0166).
- The live database never holds unproven SMB state, a crash at any later point is finished by
  the next startup reconcile, and SMB changes wait while a restore runs.
- A backup's shares and their access are those of its database. A backup whose database never
  imported its shares gets the shares of the live smb.conf, without guest access; guest access
  never comes back silently. A backup of a UNOS from before shares were stored in the database
  cannot be restored.

## Considered options

- A pending-restore marker file that a post-restore step finishes (ADR-0139): not taken; the
  extracted database is prepared before the swap instead.
