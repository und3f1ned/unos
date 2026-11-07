---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
---

# ADR-0132: Creating a user clears the SMB rows left for its id

## Context

`users.id` is a plain rowid that SQLite reuses after a deletion, and SQLite does not enforce
the foreign keys of the SMB tables. A user created while the startup reconcile or a
post-restore step was still running, or after a deletion that left rows behind, inherited
the orphan rows of its id: the uid, the state "set", the previous owner's Samba password and
their share access.

## Decision

`POST /auth/users` runs its insert under `smb_lock`, in one transaction: add the user, flush
to get the id, delete the `smb_accounts` rows with that id and the user's access rows
(`service.delete_access_of_user`, which also bumps the versions of the shares concerned),
then commit. The optional activation (`smb_enabled`) follows under the same lock. A
duplicate name still answers 400. The orphan's passdb entry and marked host account are left
to the reconcile, which removes them as stale, or to the activation's stale-account step,
which removes them and gives the new user a fresh uid (ADR-0110).

Implemented in `supervisor/src/auth/router.py` (`create_user`); tested in
`supervisor/tests/test_smb_accounts.py` (TestCreateUserAfterAnOrphan,
TestAccountFlowsWaitForTheLock).

## Consequences

A new user never inherits SMB state or share access. User creation waits while a reconcile
or a restore holds the lock.
