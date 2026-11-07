---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
---

# ADR-0137: SMB rows whose username or role no longer matches are purged

## Context

SQLite does not enforce the foreign keys of the SMB tables and reuses user ids (ADR-0132),
so rows can outlive their user. An older release running after update.sh's automatic
rollback deleted users without their SMB rows, a restored or hand-edited database can hold
any row, and access rows of a user whose SMB was off survived a promotion and came back
after a demotion.

## Decision

- `service.purge_orphans`, run by every startup and post-restore reconcile, deletes access
  rows whose share or user no longer exists, whose stored username is not the user's current
  name, or whose user is an administrator; the shares concerned get version + 1. When
  nothing is stale it only reads.
- The account reconcile (`accounts.reconcile_accounts`) deletes the `smb_accounts` rows of
  users that no longer exist and the rows whose `linux_name` is not the username; their host
  accounts are then stale and are removed (ADR-0110).
- A promotion always deletes the user's access rows, with or without an account row
  (ADR-0117).

Implemented in `supervisor/src/shares/service.py` and `supervisor/src/shares/accounts.py`;
tested in `supervisor/tests/test_smb_startup.py` (stale access rows are purged),
`supervisor/tests/test_smb_reconcile.py` (a row whose linux name is not the username, a row
whose user is gone, admin rows are cleaned) and `supervisor/tests/test_share_api.py` (stale
rows are not listed).

## Consequences

A user never inherits rows written for an earlier holder of the same id or name. The
rollback case concerns older releases, which ADR-0207 no longer supports; the purge still
cleans what restored and hand-edited databases bring.
