---
status: superseded
date: 2026-11-06
area: shares
decided-by: agent
superseded-by: ADR-0319
---

# ADR-0143: Shares are part of the database, and include_shares only adds the old smb.conf copy

## Context

Superseded by ADR-0319: backups carry no smb.conf, and include_shares is gone.
With shares and their access in SQLite (ADR-0074), every backup carries them in unos.db. The
backup option `include_shares` therefore no longer protects shares from being restored.

## Decision

`BackupConfig.include_shares` only adds a copy of the current smb.conf to the archive, for
reference. A restore takes shares and access from the database and never installs the
archived file (ADR-0040); the copy is read only by the import of a backup whose database is
not authoritative (ADR-0142). The field's API description in
`supervisor/src/backup/router.py` and the user docs say so, and the option has no restore
logic.

Tested in `supervisor/tests/test_backup_restore.py` (include_shares false still restores the
shares of the database).

## Consequences

Turning `include_shares` off does not keep a restore from changing shares; it only leaves
the smb.conf copy out of the archive.

## Considered options

- Restore logic that honours `include_shares`: not taken; the option's meaning is documented
  instead.
