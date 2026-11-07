---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
---

# ADR-0133: Requests that name a user by id also carry the username

## Context

`users.id` is a plain rowid that SQLite reuses after a deletion, so a request built from a
stale list could grant share access to, reset the password of, delete or revoke the SMB
access of a new user who got the same id.

## Decision

Every request that names a user by id for an SMB effect, a password reset or a deletion also
carries the username it was built for:

- share access entries: `user_id` with a required `username`, and the access rows store the
  username too;
- `POST /auth/users/{id}/smb/enable` (`SmbEnableRequest.username`) and
  `POST /auth/users/{id}/smb/disable` (the body `{"username": ...}`, extra fields refused);
- `PUT /auth/users/{id}` (an optional `username`) and
  `DELETE /auth/users/{id}?username=<name>` (required).

After taking `smb_lock` and reloading the user, a mismatch answers 409 "User N is now 'x';
reload the page" before anything changes, and a missing required username answers 422. A
regular user's own flows answer 404 when the reloaded user's name is not the one of the
token.

The web UI sends the names (`webui/src/lib/api/auth.ts`, `webui/src/lib/api/shares.ts`).
After a 409 or 404 it reloads the list and closes a dialog whose id now names someone else
or nobody, with a page alert titled "The change was not made" or "User was not deleted";
otherwise the dialog stays and shows the SMB state as stored now.

Implemented in `supervisor/src/auth/router.py`, `supervisor/src/auth/schemas.py`,
`supervisor/src/shares/schemas.py` and `supervisor/src/shares/service.py`; tested in
`supervisor/tests/test_smb_accounts.py` (TestUsernamesInRequests, TestStaleIdentity),
`supervisor/tests/test_share_api.py` (a reused user id), `webui/tests/users.spec.ts`,
`webui/tests/shares.spec.ts` and `webui/tests/api.spec.ts` (requests name the user).

## Consequences

API clients must send the username with these requests; a client that does not gets 422. A
stale page never changes the wrong user: the administrator reloads and decides again.
