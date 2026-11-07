---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
---

# ADR-0142: Old backups import their shares from smb.conf, never with guest access

## Context

Backups of releases before slice 2 have no SMB tables: their shares exist only in the
archived smb.conf. The same holds for a backup whose database never ran the import. Such a
backup must not bring guest access back.

## Decision

When the restored database is not authoritative (ADR-0141),
`service.prepare_restored_shares` deletes its leftover share and access rows, leaves the
import marker unset and records a pending import: from the archive's smb.conf when it is
valid UTF-8 (source "backup"), else from the live file (source "live", with a warning when
the archived file is not UTF-8). The import then runs on the normal path after the swap
(ADR-0084, ADR-0086). An import with a pending source never turns guest access on: a guest
share is imported without it, with the note "Guest access in the backup was not restored".

An authoritative database keeps its rows: shares whose folder breaks a path rule lose their
access (ADR-0145), and guest shares are kept with the warning "Guest access is on for: X (as
in the backup)" and an audit entry at WARNING.

Implemented in `supervisor/src/shares/service.py` (`prepare_restored_shares`, the import)
and `supervisor/src/backup/router.py`; tested in `supervisor/tests/test_backup_restore.py`
(TestNotAuthoritative, TestAuthoritative) and `supervisor/tests/test_share_import.py` (a
guest share from a restore is not guest).

## Consequences

Guest access never comes back silently from a backup; an administrator turns it on again
where it is wanted. Backups of releases before slice 2 are a compatibility case that
ADR-0207 no longer requires, while the same path also serves backups whose database has no
import marker.
