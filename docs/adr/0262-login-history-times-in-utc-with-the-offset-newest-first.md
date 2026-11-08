---
status: accepted
date: 2026-11-07
area: auth
decided-by: agent
---

# ADR-0262: The login history answers its times in UTC with the offset, newest first also within a second

## Context

`login_attempts.timestamp` is SQLite's `CURRENT_TIMESTAMP`, UTC to the second, and `GET /api/v1/security/login-history` answered it without an offset (`2026-11-07T12:00:00`). A browser reads such a time as its own local time, so System > Security > Login History showed every sign-in hours off outside UTC (entry `auth.login-history`). The list was ordered by that time alone, so a failed sign-in and the right one in the same second came in any order, and `DELETE /api/v1/security/login-history` compared the UTC times with the server's local time.

## Decision

- `LoginAttemptResponse.timestamp` (`supervisor/src/security/schemas.py`) takes a time without a zone as UTC and is answered with its offset, as `2026-11-07T12:00:00Z`. The stored rows stay as they are.
- The login history is ordered by time and then by id, both newest first (`get_login_history` in `supervisor/src/security/router.py`).
- `clear_login_history` counts `older_than_days` back from the current time in UTC, whatever the server's time zone.
- The web UI shows the time in the browser's time zone (`formatTimestamp` in `webui/src/routes/system/tabs/SecurityTab.svelte`) in a `<time>` element that carries the answer's value, and the attempts as a list named "Login history".
- Tests: `supervisor/tests/test_authz.py` (`TestLoginHistory`, `TestRealClientAddress`), and `webui/tests/login-history.spec.ts` in the time zone Asia/Kathmandu (UTC+05:45).

## Consequences

- Only this answer carries its zone so far; the other times of the API, such as the creation times of users and API tokens, stay as they are until `architecture.times-with-time-zones`.
- Attempts that the server stored in the same second keep the order in which they were stored.
