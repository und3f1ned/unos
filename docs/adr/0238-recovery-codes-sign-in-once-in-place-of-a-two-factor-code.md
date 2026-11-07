---
status: accepted
date: 2026-11-07
area: auth
decided-by: agent
---

# ADR-0238: Ten recovery codes each sign in once in place of a two-factor code, and end with two-factor authentication

## Context

A user with two-factor authentication who lost the authenticator app could not sign in again: codes could be generated through the API, but the sign-in took none (ADR-0033 left the design open), and no administrator can reset two-factor authentication. A code that works twice, also when two sign-ins send it at the same moment, is a way around 2FA, and codes that outlive 2FA come back with its next setup. This settles the recovery codes of ADR-0033 for the entry `auth.recovery-codes`.

## Decision

- Sign-in: `LoginRequest` in `supervisor/src/auth/schemas.py` takes `recovery_code` (at most 32 characters) in place of `totp_code`, checked by `login` in `supervisor/src/auth/router.py` after the password, only for an account with 2FA; both codes in one request are a 422. `_use_recovery_code` uses a code up in one compare-and-swap UPDATE of `users.recovery_codes`: the used code's hash becomes null only where the column still holds what was read, so of two sign-ins with one code exactly one succeeds, and a sign-in that read the codes before new ones were generated fails.
- A wrong, used or malformed code answers 401 "Invalid recovery code" and is recorded as the failed login "Invalid recovery code"; a sign-in with a code is recorded as a successful login and audited as `login_success` with "Signed in with a recovery code" (severity warning).
- Codes: `POST /api/v1/auth/recovery-codes/generate` replaces the user's codes with ten distinct new ones of 12 hex digits (48 random bits from `secrets`), answered once as XXXX-XXXX-XXXX. Its UPDATE writes them only while 2FA is on, else 400. Once they are stored, a failure to write their audit entry (a full disk) is logged, and the codes are still answered: the earlier codes no longer work, and a 500 would leave the user without any. They may be typed in any case, with or without dashes and spaces. Stored: the SHA-256 of the 12 digits in upper case (`recovery_code_hash` in `supervisor/src/auth/utils.py`), as API tokens are (ADR-0239). `GET /api/v1/auth/recovery-codes/status` answers how many are left, none while 2FA is off.
- Turning 2FA off removes the codes in the one UPDATE that turns it off (`disable_totp`), so codes that a generation stored after the request read the user go too; turning it on starts without any: codes of an earlier setup never sign in.
- A refused code goes to the login history only, as a refused two-factor code does; the audit log gets the sign-ins with a code.
- `POST /api/v1/auth/recovery-codes/verify`, a stub that answered 501, is removed (ADR-0207).
- Web UI: on the Account page the Two-Factor Authentication card has a Recovery codes part while 2FA is on (`webui/src/lib/components/account/RecoveryCodes.svelte`): it generates codes (new ones after a confirmation), shows them once until Done, and says how many are left. A generation the server refuses (a 4xx answer) says "No recovery codes were generated". After a 5xx answer, an unreadable one or none, the page cannot know whether the server replaced the codes: it says "New recovery codes may have been generated" and that the saved codes may no longer work, until a generation succeeds or is refused. The code step of the sign-in page offers "Use a recovery code" and stays on that step after "Invalid recovery code".
- Tests: `supervisor/tests/test_recovery_codes.py`, `webui/tests/recovery-codes.spec.ts`.

## Consequences

- A recovery code is a second factor beside the TOTP code: the kept practice of ADR-0032, no token before the TOTP check, becomes no token before the second factor (ADR-0239).
- A user who lost the authenticator app signs in with recovery codes and can generate new ones, but turning 2FA off still needs a code of the app.
- Codes generated before this change were hashed with their dashes and no longer match: generate new ones.
- The 48 bits stand against guessing online, because the password comes first and sign-ins are limited per client (ADR-0010). Offline, a copy of the database defeats 2FA without the codes: `users.totp_secret` is stored as it is. If TOTP secrets are ever encrypted at rest, the code hashes need a key or a slow hash too.

## Considered options

- bcrypt for each code, or an HMAC keyed with the JWT secret: not chosen. With the TOTP secret in plain text beside them neither raises the bar for a copy of the database, bcrypt makes a sign-in check up to ten slow hashes, and a key that a restore on another install or a new secret changes would make every code fail: a lockout.
- A two-factor code before generating codes: not chosen. A stolen session can already create an API token that outlasts it, and right after turning 2FA on the user would wait for the app's next code.
- An in-process lock around the check instead of the compare-and-swap: it would have to cover generating codes and turning 2FA off as well, while the conditions of the two UPDATEs, and turning 2FA off in one UPDATE, need no lock.
- The audit entry of new codes written before they are stored, so that a generation that cannot be audited does not happen: not chosen. The answer of a stored generation can still be lost on the way, which the page has to handle anyway, and the entry would then also record codes whose commit failed.
- Comparing with the codes loaded with the user at the start of the sign-in, without reading them again: that value is older by a bcrypt check (hundreds of milliseconds), which widens the window in which a code is refused because another one was used at the same time.
