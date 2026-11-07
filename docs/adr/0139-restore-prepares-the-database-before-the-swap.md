---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
---

# ADR-0139: A restore prepares the extracted database before the swap, under smb_lock

## Context

A restore changes SMB state like any share or account change (ADR-0080), and an uploaded
archive can hold anything (ADR-0144). If the restored database replaced the live one first
and were checked afterwards, a crash, or a flow that started before the restore, could leave
unproven active accounts or commit into the restored database.

## Decision

The restore endpoint holds `service.smb_lock()` from before its worker thread starts until
the post-restore step returns. The locked part runs in its own task, awaited through
`asyncio.shield`, so that a cancelled request cannot release the lock while the restore
still runs (`supervisor/src/backup/router.py`). Before the extracted database, in the
restore's temporary directory in RAM, replaces the live one (`_prepare_smb`):

- `_verify_database` also refuses triggers and views, before any write;
- `service.ensure_smb_tables` creates the SMB tables that are missing and refuses tables of
  an unknown layout;
- the SMB accounts of the live database are read (`accounts.snapshot_for_restore`);
- one transaction on the extracted file decides by the import marker whether the database is
  authoritative (`service.restored_smb_authoritative`), then applies the share rules
  (`service.prepare_restored_shares`) and the account rules
  (`accounts.prepare_restored_accounts`, ADR-0141); any failure rolls it back, and the
  restore fails with the live database unchanged.

After the swap and `init_db`, `service.after_database_restore_locked` runs the startup path
(ADR-0138); an exception there becomes the warning "SMB state is repaired at the next
start". When the pool list cannot be read, the restore of a database that keeps its shares
is refused with 503 before anything changes (ADR-0149).

Tested in `supervisor/tests/test_backup_restore.py` (TestRestoredSchema, TestAccountStep,
TestCrashSafety; the restore waits for `smb_lock`, its post-restore step runs under it, and
a cancelled request keeps it until the restore ends).

## Consequences

The live database never holds unproven SMB state, and a crash at any later point is finished
by the next startup reconcile. SMB changes wait while a restore runs.

## Considered options

- A pending-restore marker file that a post-restore step finishes: not taken; the extracted
  database is prepared before the swap instead.
