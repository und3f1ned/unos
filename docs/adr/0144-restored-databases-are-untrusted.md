---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
---

# ADR-0144: Restored databases are untrusted input

## Context

Backup archives can be uploaded (ADR-0166), so a restored unos.db can hold any value in any
column, schema objects such as triggers that would run inside the restore's own transaction,
and DateTime values that SQLAlchemy cannot read. The SMB tables feed smb.conf and host
commands.

## Decision

Every value read from the SMB tables is validated again before it reaches smb.conf or argv,
and a restored row can at most deny access or mark a password as needed:

- the restore refuses triggers and views before any write, and SMB tables of an unknown
  layout (ADR-0139); the preparation runs with `PRAGMA trusted_schema = OFF`;
- share rows are checked against the path rules at the restore and again at every render
  (ADR-0145);
- account names and uids are checked before every host command, and values of the wrong type
  never reach argv;
- `next_uid` is replaced when it is not an integer in range, and the JSON columns
  `import_notes` and `import_report` are read defensively (`service.json_lines`);
- rows of administrators are cleaned (ADR-0137);
- DateTime values that cannot be read are replaced: `created_at` of `smb_shares`,
  `smb_share_access` and `smb_accounts`, and `smb_state.shares_imported_at` become the
  current time (so an authoritative database stays authoritative), and an unreadable
  `updated_at` or `password_set_at` becomes NULL.

Implemented in `supervisor/src/backup/router.py`, `supervisor/src/shares/service.py` and
`supervisor/src/shares/accounts.py`; tested in `supervisor/tests/test_backup_restore.py`
(TestRestoredSchema; DateTime values that cannot be read are replaced; a hostile database
never reaches smb.conf or argv), `supervisor/tests/test_restore_accounts.py` (hostile
values) and `supervisor/tests/test_smb_reconcile.py` (hostile rows never reach the host).

## Consequences

Every reader of the SMB tables pays for defensive checks. In exchange, a restored row cannot
name a foreign account, uid 0, a path outside a managed pool or an smb.conf directive, and
cannot turn guest access on through an import. Known gaps are tracked in ADR-0195.
