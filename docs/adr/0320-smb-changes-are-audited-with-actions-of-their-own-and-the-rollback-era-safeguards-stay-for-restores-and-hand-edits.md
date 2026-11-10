---
status: accepted
date: 2026-11-10
area: shares
decided-by: agent
supersedes: [ADR-0157]
---

# ADR-0320: SMB changes are audited with actions of their own, and the safeguards of the rollback era stay for restores and hand edits

## Context

ADR-0157 let the SMB code audit only with `AuditAction` values that already existed, because
`deploy/update.sh` rolled back to the previous release when its health check failed, and that
release's loader dropped the whole audit log at an action it did not know. UNOS has no such
releases (ADR-0207): every release that update.sh can roll back to reads the audit log line by
line and skips a line it cannot read, keeping the rest (`_read_file` of
`supervisor/src/audit/router.py`). The purge of SMB rows (ADR-0137) and the
copy of foreign share sections (ADR-0092) also named such a rollback among their reasons. The
ledger entry `shares.rollback-era-safeguards` asks for the actions of SMB and for a decision on
the two safeguards; the agent took it up under the owner's delegation ("work on through the
features, ask no questions; I allow everything", in translation, 2026-11-09).

## Decision

- SMB and NFS changes are audited with actions of their own (`AuditAction` of
  `supervisor/src/audit/schemas.py`): `smb_account_updated` for a user's SMB account (allowed or
  disallowed, turned on or off, a new password, revoked at a promotion; resource type
  `smb_account`), `smb_shares_imported` for the
  import of smb.conf, `smb_reconciled` for the startup reconcile and `smb_restored` for the SMB
  state after a restore (both resource type `smb`), `share_permissions_repaired` for Repair
  permissions, and `nfs_export_created` and `nfs_export_deleted` for the NFS exports (ADR-0321,
  which supersedes ADR-0292). The share changes keep `share_created`, `share_updated` and
  `share_deleted`, and a user's deletion keeps its one `user_deleted` entry, which says when the
  SMB account was removed. The web UI names each action (`ACTION_NAMES` of
  `webui/src/routes/audit/+page.svelte`, ADR-0298).
- From ADR-0157, unchanged: the SMB code writes audit entries only for changes (an import that
  imported at least one share, purged rows, row or host writes, a new smb.conf, a restore); a
  change that changes nothing writes no entry; problems are not audited: the startup and
  post-restore reconcile keep them in memory for `GET /shares/smb-status`
  (`service.last_reconcile()`) and log one WARNING line.
- The purge of SMB rows that no longer match (ADR-0137) and the copy of foreign share sections
  to `smb.conf.unos-replaced` (ADR-0092) stay: a restored or hand-edited database can hold any
  row, SQLite reuses the ids of users, and a share section can be added to smb.conf by hand.
  Only their reason of an older release is gone, and with it the remarks on older releases in
  the comments of `supervisor/src/auth/router.py` and `supervisor/src/shares/accounts.py`.
- Tests: `supervisor/tests/test_smb_startup.py`, `test_smb_integration.py`, `test_share_import.py`,
  `test_share_api.py` (TestAudit), `test_smb_accounts.py`, `test_share_repair.py`, `test_nfs.py`,
  `test_backup_restore.py`, `test_audit_log.py` (every action has a name in the web UI).

## Consequences

- The audit log tells an SMB account's change, an import, a reconcile and a restore apart from
  other changes, and its filter by action finds them.
- A release that predates an action and runs after an update's rollback skips the entries with
  it, and keeps them when it appends to or rotates the log; a deletion of old entries by date
  (`DELETE /audit/logs?before_date=`) there rewrites a file with the lines it can read, so the
  entries with the newer actions go, also those newer than the date.
- A consistent start writes no audit entry, and problems are visible only while UNOS runs, on
  the Shares page and in the log.
