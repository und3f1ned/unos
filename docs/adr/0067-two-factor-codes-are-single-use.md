---
status: accepted
date: 2026-11-06
area: auth
decided-by: agent
---

# ADR-0067: Two-factor codes are single-use, remembered in memory

## Context

A TOTP code stays valid for the rest of its 30-second time step. Without a record of used codes, the code seen at a login could be used again within that step, for example to turn on SMB, a channel without two-factor authentication (ADR-0068). RFC 6238 (section 5.2) says a verifier must not accept the second attempt of a code that was already accepted.

## Decision

- `check_totp_code(user_id, secret, code)` in `supervisor/src/auth/utils.py` returns OK, INVALID or REUSED. It verifies the code at one instant (`valid_window` stays 0, never widened) and remembers the time step of the last accepted code per user id in memory (`_last_totp_steps`). A code whose step is not newer than the last accepted one is REUSED.
- Every TOTP check uses it: the login, turning 2FA on (the setup confirmation) and off, and `POST /api/v1/auth/me/smb/enable`. A reused code gets the endpoint's usual wrong-code status with the detail "This two-factor code was already used; wait for the next one": 401 at the login (recorded as the failed login "Reused 2FA code"), 400 elsewhere.
- A wrong code uses nothing up. Deleting a user forgets its steps, because SQLite may give its id to the next new user.
- The check and the record run on the event loop without an `await` between them, so two requests never both accept one code.
- The login page stays in its 2FA step on this detail, and the Account page shows it as the server words it.
- Tests: `supervisor/tests/test_totp_once.py`.

## Consequences

- A user who has just signed in waits for the next code before turning SMB on.
- The record is memory only: after a restart of the supervisor, a code of the step that was current before can be accepted once more.

## Considered options

- Persisting the accepted time steps: not chosen; the decision kept the record in memory, and no further reason was recorded.
