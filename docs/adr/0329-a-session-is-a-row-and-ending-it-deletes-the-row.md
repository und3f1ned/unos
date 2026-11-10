---
status: accepted
date: 2026-11-10
area: auth
decided-by: agent
---

# ADR-0329: A session is a row of user_sessions, and ending a session deletes its row

## Context

A JWT stayed valid until it expired (24 hours by default) whatever happened: signing out only forgot the token in the browser, and a password change or an administrator could not end a stolen session. This settles the first item of ADR-0033, revocable sessions. It replaces only these statements of other ADRs, which stay in force otherwise: ADR-0009's "Logout only forgets the token in the browser", and ADR-0284's that the sessions of an administrator stay valid after `reset-admin`.

## Decision

- A sign-in adds a row to the new table `user_sessions` (`id`, the token's `jti`; `user_id`; `expires_at`) in the commit of its login history, and deletes the rows of expired sessions in the same commit. The table is `WITHOUT ROWID` and has no index, so the row adds as few pages to that commit as it can (ADR-0308). The table comes from `create_all` (ADR-0319).
- When that commit fails, as on a full `/data`, the sign-in still answers (ADR-0251) and the supervisor keeps the session in memory: valid until the token expires, the session ends or the supervisor restarts.
- `authenticate_token` (`supervisor/src/auth/utils.py`) accepts a session token only while its row exists and names the token's user; otherwise it answers 401 "Session ended". A token without a `jti` is invalid. The WebSocket re-check uses the same function, so a socket closes with 4401 at its next check.
- Ending a session deletes its row, in the commit of the change that ends it (the rows are selected without flushing and deleted at the commit, inside its error handling):
  - `POST /api/v1/auth/logout` ends the caller's session; the web UI's Logout calls it with the token after it has forgotten it, so a failed call still signs the browser out. An API token gets 400: it is ended by deleting it.
  - A password change ends the user's other sessions, in the commit of the new hash.
  - A new password set by an administrator, the deletion of the user and the emergency `reset-admin` (ADR-0284) end every session of the user; an administrator who sets their own password keeps the session that set it.
  - A restore ends every session: the backup's rows are deleted before the swap, and the sessions kept in memory are forgotten. The administrator who restored keeps theirs when the restored database has them as an administrator under the same id and name, so that they see the result.
  - An administrator ends every session of another user with `POST /api/v1/auth/users/{id}/end-sessions?username=` (409 when the id now names someone else), the Users page's End sessions button, with an audit entry `user_updated`.
- API tokens are not sessions and stay as they are (ADR-0033's other items).

## Consequences

- Tokens issued before this change have no `jti` and answer 401: everyone signs in once more after the update, and after a restore.
- `reset-admin` runs in a process of its own: a session that a full `/data` kept in the supervisor's memory survives it until the supervisor restarts.
- Each authenticated request reads one more row by its primary key; sign-ins and the changes above write in commits they made before, and nothing else writes.
- A row stays until the next sign-in after its expiry, so the table holds at most the sessions of the last `UNOS_JWT_EXPIRE_MINUTES`.
