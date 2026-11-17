---
status: accepted
date: 2026-11-17
area: auth
decided-by: agent
---

# ADR-0383: An administrator with two-factor authentication requires it for every account, and after a grace period an account without it reaches only its setup

## Context

Two-factor authentication is each user's choice (ADR-0067, ADR-0238), so an administrator cannot
make sure that a stolen password alone opens no account. The entry `auth.mandatory-two-factor`
asks for a requirement with a grace period: accounts without two-factor authentication are asked
to set it up at their next sign-in, and after the grace period can only set it up. The owner
delegated the design, asking above all that it be safe (ADR-0013); the setup itself must stay
reachable, or the requirement would lock people out, the last administrator included.

## Decision

- **Where it lives.** In System > Security, an administrator's card of
  `webui/src/routes/system/tabs/SecurityTab.svelte`. It is stored in the database, the one row of
  the new table `two_factor_requirement` (`grace_days`, `enabled_at` in UTC, `enabled_by`), made
  by `create_all` (ADR-0319): no row is off, the default. A backup carries it and a restore brings
  it back with the accounts it applies to. `GET /api/v1/security/two-factor-requirement` answers
  it; turning it on or off writes or deletes that row and nothing else (ADR-0214). The row of a
  restored database is not trusted (ADR-0144): it is read as stored, a grace period out of range is
  held within the choices, and a start that cannot be read or lies in the future, or a table that
  cannot be read, leave no grace period. The requirement then fails closed, never with an error:
  the setup stays reachable, and an administrator with two-factor authentication turns it off.
- **Grace period.** 0, 1, 3, 7, 14 or 30 days, 7 by default; anything else is a 422. A week
  reaches the people of a household who sign in now and then, 30 days bounds how long the
  requirement waits, and 0 is for an administrator who wants it at once.
- **Turning it on.** `POST /api/v1/security/two-factor-requirement/enable` with `grace_days` and
  `code`: an administrator, with a session token (an API token gets 403 before anything else),
  whose own account has two-factor authentication (else 409 "Turn on two-factor authentication for
  your own account first") and who gives a current code of it, used up as every code is (ADR-0067;
  400 for a wrong or used one), checked last so that a refused request uses no code. Already on,
  also when another administrator turned it on meanwhile: 409. So the administrator who imposes
  it meets it, and at that moment at least one administrator keeps full access; the code shows
  that the second factor is at hand, not only a session left open in a browser. Turning it off,
  `POST /api/v1/security/two-factor-requirement/disable` with `code`, takes the same: it weakens
  every sign-in, as turning one's own two-factor authentication off does. Not on, also when
  another administrator turned it off meanwhile: 409.
- **When the grace period starts.** For each account, at the later of the requirement's
  `enabled_at` and the account's `created_at`: an account made after the requirement gets its own
  grace period. It ends `grace_days` later. An account with two-factor authentication is never
  restricted. One that turns it off later, which needs a code, gets no new grace period: after its
  grace period it can only set it up again at once.
- **During the grace period.** Everything works as before. `GET /api/v1/auth/me` answers
  `two_factor_required` with `by`, the end of the grace period, and `setup_only` false; the web UI
  shows a page after the sign-in that asks to set it up now or later (`/two-factor-setup`).
- **After the grace period, "setup only".** Enforced in the backend, in `authenticate_token` of
  `supervisor/src/auth/utils.py` (`supervisor/src/auth/requirement.py` derives the state from the
  rows and the time; nothing is stored per account). A session token of such an account reaches
  `GET /auth/me`, `GET /auth/totp/status`, `POST /auth/totp/setup`, `POST /auth/totp/enable` and
  `POST /auth/logout`, the routes that depend on `verify_token_allowing_setup`; every other route
  answers 403 "Two-factor authentication is required: set it up to continue". The sign-in with
  the password still answers a token: it is the way to the setup. Once two-factor authentication
  is on, that session has the account's rights again at its next request: turning it on proved
  the second factor. `GET /auth/me` answers `setup_only` true, and the web UI shows only
  `/two-factor-setup`, with Sign out, and opens no WebSocket.
- **API tokens are not exempt.** Every request of an API token of a setup-only account answers
  the 403, the setup routes included (a person sets it up, with a session); its tokens work again
  once the account has two-factor authentication. A token acts with its account's rights without
  a second factor: exempt, a token made during the grace period would keep password-only access
  for ever. Tokens of accounts with two-factor authentication, and all tokens during the grace
  period, work as before.
- **WebSocket.** The handshake of every socket, and its check every few minutes, close a socket
  of a setup-only account with 4403, not 4401: the session stays, for the setup. The events
  socket closes with 4403 for nothing else, so the web UI loads the account again then and leads
  to the setup, as it does after the 403 of a request.
- **Existing sessions** of an account without two-factor authentication do not end when its grace
  period ends: the check runs at each request, so they become setup-only at their next request,
  which protects as much as ending them would, writes nothing and leads the user to the setup
  instead of the sign-in.
- **SMB has no second factor.** The requirement changes nothing for SMB: an account with SMB on
  still signs in to SMB with its password alone, also while it is setup-only. The card and
  `docs/USER_GUIDE.md` say so; an administrator turns SMB off for an account on the Users page
  where that matters. Administrators never have SMB (ADR-0117).
- **Turning it off** deletes the row: accounts without two-factor authentication have their full
  rights again at their next request, and a later turning on starts new grace periods.
- **Emergency reset and recovery codes.** `reset-admin` (ADR-0284) is unchanged: it turns the
  administrator's two-factor authentication off, and with the requirement on and the grace period
  over, the next sign-in leads to the setup, as `docs/RECOVERY.md` already asks after a reset.
  The setup page needs only an authenticator app, so no one who has one is locked out. A sign-in
  with a recovery code is one of an account with two-factor authentication, never restricted;
  after the setup, `/two-factor-setup` leads to the Account page to generate recovery codes.
- **Audit entries.** After the commit, by the administrator: `settings_changed`, resource type
  `two_factor_requirement`, "Two-factor authentication required for every account, with a grace
  period of N days" (or "without a grace period"), info; "Two-factor authentication no longer
  required for every account", warning. A refused request writes none (ADR-0320); an entry that
  cannot be written is in the supervisor's log and the request answers what it did (ADR-0343). A
  sign-in of a setup-only account is audited as `login_success` "Signed in after the grace
  period: can only set up two-factor authentication", warning. Turning two-factor authentication
  on writes its `totp_enabled` as before.
- Tests: `supervisor/tests/test_two_factor_requirement.py`, the setup-only walk of the route matrix
  in `supervisor/tests/test_authz.py`, `webui/tests/mandatory-two-factor.spec.ts`.

## Consequences

- An account without two-factor authentication costs one more read by primary key per request;
  an account with it, none.
- Whoever knows the password of an account without two-factor authentication can still sign in
  and set up an authenticator of their own, as they could sign in before: the requirement gets
  accounts to two-factor authentication, it does not protect one that has none yet.
- Scripts that use an API token of an account without two-factor authentication stop working when
  its grace period ends, until its owner sets it up.
- The code that turning the requirement on or off takes is used up once, but not rate-limited,
  like the code that turns one's own two-factor authentication off: whoever holds an
  administrator's session can guess at it one request at a time.
- SMB stays a way in with the password alone.
- A regular user who lost the authenticator app and the recovery codes is still locked out, as
  before (ADR-0284): the requirement adds no way to reset another user's second factor.

## Considered options

- Ending the sessions of accounts without two-factor authentication at the end of the grace
  period: a background job and writes at the deadline, for no more protection than the per-request
  check, and the user would land on the sign-in instead of the setup.
- Refusing the sign-in after the grace period, or locking the account until an administrator acts:
  a lockout that the requirement does not need, and the last administrator could lock themselves out.
- Exempting API tokens: a token made during the grace period would bypass the requirement.
- Refusing to turn two-factor authentication off while it is required: a user could no longer move
  to a new authenticator app; turning it off needs a code anyway and leaves the account setup-only.
- A grace period per account stored in a new column: tables are never altered (ADR-0319), and the
  start is derived from `users.created_at` and `enabled_at`.
