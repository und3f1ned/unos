---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
---

# ADR-0077: The SMB tables come from create_all, and existing tables are never altered

## Context

Slice 2 adds four tables (ADR-0074) to databases that already exist: the live `unos.db` of an installation and the databases inside backups made before the slice. UNOS has no migration framework (ADR-0022), and SQLAlchemy's `create_all` creates missing tables but changes no existing one.

## Decision

- `init_db` in `supervisor/src/database.py` imports the SMB models, and its `create_all` creates `smb_shares`, `smb_share_access`, `smb_accounts` and `smb_state` wherever they are missing: at startup and after a restore.
- A restore creates them in the extracted backup database before that database replaces the live one (`ensure_smb_tables` in `supervisor/src/shares/service.py`, `create_all` limited to the four tables). An SMB table of an unknown layout there (a missing column, or an extra NOT NULL column without a default) refuses the restore.
- No existing table or column is ever altered: no `ALTER TABLE`, and no column is added to `users`; the SMB state of a user lives in `smb_accounts`. No migration framework is introduced in this slice.
- Tests: `supervisor/tests/test_smb_models.py`, `supervisor/tests/test_backup_restore.py`.

## Consequences

- Existing installations and restored older backups get the tables without a migration step.
- The tables that existed before slice 2 keep their layout.
- The first change to an existing table or column needs a decision on ADR-0022 first; without older installations (ADR-0207), resetting the dev device's database is the alternative to a migration framework.
