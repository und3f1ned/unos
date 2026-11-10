---
status: accepted
date: 2026-11-10
area: shares
decided-by: agent
supersedes: [ADR-0142, ADR-0143]
---

# ADR-0319: A restore refuses backups without SMB tables, and a backup's database alone holds its shares

## Context

ADR-0142 imported the shares of a restored database that was not authoritative for SMB
(ADR-0141) from the archive's smb.conf, for backups of releases before shares were stored in
the database, and ADR-0143 kept the backup option `include_shares` to add that copy of smb.conf
to every archive. UNOS has no such releases to restore from (ADR-0207): the ledger entry
`backup.no-old-backup-import` asks to refuse their backups and to remove the import from the
archive, and to settle what a database of this release without the import marker restores from.
The agent took it up under the owner's delegation ("work on through the features, ask no
questions; I allow everything", in translation, 2026-11-09).

## Decision

- A restore checks the four SMB tables of the extracted database (`check_smb_tables` of
  `supervisor/src/shares/service.py`) and creates none: a database that lacks one of them is
  refused with 400 "unos.db in the backup lacks the SMB tables ...: it was made by an older
  UNOS, whose backups this one does not restore", and nothing changes. A table of an unknown
  layout is refused as before.
- A database with the tables but without the import marker (its import never ran or failed) is
  not authoritative, as ADR-0141 says: its share and access rows are deleted, and the import
  after the restore reads the live smb.conf (`pending_import_source` 'live'). Such an import
  never turns guest access on, with the note "Guest access was not restored".
- A backup carries no smb.conf: `BackupConfig.include_shares` and the copy are gone, and a
  restore neither reads nor installs an smb.conf that an archive holds (it is no member of a
  backup, and is ignored like any other file). `smb_state.pending_import_conf` is gone from the
  model; a database that still has the column keeps it unused.
- Tests: `supervisor/tests/test_backup_restore.py` (`TestNotAuthoritative`,
  `test_a_backup_restores_the_shares_of_its_database`), `supervisor/tests/test_share_import.py`,
  `supervisor/tests/test_smb_models.py`.

## Consequences

- A backup's shares and their access are those of its database; a backup without them restores
  the shares that are live at the restore.
- The account rules of a restore (ADR-0141) are unchanged, also for a database that is not
  authoritative.
