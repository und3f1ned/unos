---
status: superseded
date: 2026-11-06
area: shares
decided-by: agent
superseded-by: ADR-0320
---

# ADR-0157: The SMB code audits only real changes, and problems go to the status

## Context

Superseded by ADR-0320: SMB changes have audit actions of their own; the rest stays.

Every audit entry reads and rewrites the whole audit_log.json on the system disk (ADR-0066),
so a persistent problem, such as a host without Samba, would rewrite it at every start.
update.sh rolls back to the previous release when its health check fails, and that release's
loader drops the whole audit log when it meets an action it does not know.

## Decision

The SMB code writes audit entries only with AuditAction values that already existed
(SHARE_CREATED, SHARE_UPDATED, SHARE_DELETED, USER_UPDATED with the resource type
smb_account, USER_DELETED, and OTHER with the resource types smb and share), and only for
changes: an import that imported at least one share, purged rows, row or host writes, a new
smb.conf, a restore. A change that changes nothing writes no entry. Problems are not
audited: the startup and post-restore reconcile keep them in memory for
`GET /shares/smb-status` (`service.last_reconcile()`) and log one WARNING line.

Implemented in `supervisor/src/shares/service.py` and `supervisor/src/auth/router.py`;
tested in `supervisor/tests/test_smb_startup.py` (no audit entry and one warning per start),
`supervisor/tests/test_smb_integration.py` (a host without Samba writes the audit log only
for changes), `supervisor/tests/test_share_import.py` (an import of no share writes no audit
entry) and `supervisor/tests/test_share_api.py` (TestAudit).

## Consequences

A consistent start writes no audit entry, and problems are visible only while UNOS runs, on
the Shares page and in the log. The rollback reason concerns older releases, which ADR-0207
no longer supports; the system-disk reason holds on its own.
