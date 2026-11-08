---
status: accepted
date: 2026-11-08
area: auth
decided-by: agent
---

# ADR-0263: A regular user's login history starts with their account

## Context

The login history (`login_attempts`) is kept by the name that was signed in with, also for names that have no account, and deleting a user leaves its rows. A regular user saw every attempt on their name (entry `auth.login-history`), so an account created with the name of a deleted one showed the earlier person's sign-ins, with their times, addresses and browsers, as its own, and counted them.

## Decision

- `GET /api/v1/security/login-history` answers a regular user the attempts on their name since their account was created: `timestamp` from the second of `users.created_at` on, both compared to the second with SQLite's `datetime()` (`_visible_attempts` in `supervisor/src/security/router.py`), in the list and in the counts.
- Administrators keep seeing every attempt, also those of deleted accounts and of names without an account, and `username` filters them by name.
- Deleting a user keeps its rows: they remain the administrators' record until `DELETE /api/v1/security/login-history` clears them by age, `older_than_days` from 0 to 36500 (`MAX_HISTORY_DAYS`, 100 years; other values answer 422, since a negative one would delete every attempt and a much larger one reaches before the year 1).
- Tests: `supervisor/tests/test_authz.py` (`TestLoginHistory`): a user created again with the name of a deleted one sees only their own sign-in, an administrator all three; periods out of range are refused and delete nothing.

## Consequences

- Failed attempts on a name before its account existed are not shown to that account either.
- An attempt stamped earlier than the account's creation, because the clock was set back after it, is not shown to the user; administrators still see it.

## Considered options

- Deleting the user's rows with the user: not chosen, because the administrators would lose the history of a deleted account, and attempts on the name between the deletion and a new account would still be shown to the new one.
