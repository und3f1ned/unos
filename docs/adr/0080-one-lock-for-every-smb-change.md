---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
---

# ADR-0080: One lock, smb_lock, serializes every change of SMB state

## Context

An SMB change runs host commands (useradd, smbpasswd, pdbedit, testparm, smbcontrol) between its database reads and commits. Two such flows at the same time interleave: a promotion and an activation, a deletion and a re-creation of a user with the same id, or a share edit during a database restore. A restore replaces every SMB row at once, so it is an SMB state change too.

## Decision

- Every change of SMB state runs under `service.smb_lock()` in `supervisor/src/shares/service.py`: share create, PATCH and delete, access changes, every account flow of `supervisor/src/auth/router.py` (allow, activate, turn off, password changes, promotion, user creation with its insert and optional activation, user deletion), the writing of smb.conf, the one-time import, and a database restore. It is one `asyncio.Lock` per event loop and not re-entrant: a request takes it once.
- The restore takes the lock before its worker thread starts and holds it until the post-restore step has returned; the locked part runs in a task of its own awaited through `asyncio.shield`, so a cancelled request cannot release the lock while the restore runs (`supervisor/src/backup/router.py`).
- The startup reconcile runs as a background task created in the lifespan (`supervisor/src/main.py`), which takes the lock before the first request is served; SMB changes and the first read of the shares before their import wait for it (ADR-0138).
- Every flow reloads its rows after taking the lock (ADR-0081).
- Tests: `supervisor/tests/test_smb_accounts.py` (`TestAccountFlowsWaitForTheLock`), `supervisor/tests/test_share_apply.py`, `supervisor/tests/test_backup_restore.py`, `supervisor/tests/test_lifespan.py`.

## Consequences

- SMB changes happen one at a time; a slow host command delays the next SMB change, not the rest of the API.
- Repair permissions changes folder modes, not SMB state, and does not take the lock: it checks instead that the share's row is still the one it read before it walks the folder.
