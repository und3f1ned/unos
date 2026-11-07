---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
---

# ADR-0081: SMB flows reload the user and the account after taking smb_lock

## Context

`supervisor/src/database.py` creates sessions with `expire_on_commit=False`, so objects loaded before `smb_lock` was taken, such as the user of the request's token, keep the state they had before another flow's commit. With the pinned SQLAlchemy, even a new `select` in the waiting session returned the objects of its identity map unchanged. A promotion racing an SMB activation ran smbpasswd for a new administrator, and a disallow racing a password change re-created the passdb entry it had just removed.

## Decision

- Right after taking `smb_lock` (ADR-0080), every SMB flow reloads the user and its account row with `select(...).execution_options(populate_existing=True)` (`accounts.reload_user` in `supervisor/src/shares/accounts.py`) and derives the SMB state only from these rows. A user that is gone answers 404 "User not found"; a self flow also answers 404 when the reloaded username differs from the token's.
- `build_render_input`, the share mutations and the reconcile also read with `populate_existing=True` (`_FRESH` in `supervisor/src/shares/service.py`) or with plain SQL on the tables, never from objects loaded earlier.
- Tests: `supervisor/tests/test_smb_accounts.py` (`TestStaleIdentity`: a promotion wins over a waiting self-activation, which then answers 409 without calling smbpasswd) and `supervisor/tests/test_share_apply.py` (`TestRenderInput`).

## Consequences

- A flow that waited for the lock acts on the state that the previous flow committed.
- Requests that name a user by id also carry the username, so a reused id is detected (ADR-0133).
