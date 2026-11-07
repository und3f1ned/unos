---
status: accepted
date: 2026-11-07
area: auth
decided-by: agent
supersedes: [ADR-0032]
---

# ADR-0239: Auth keeps bcrypt, one JWT algorithm, a second factor before any token, and hashed API tokens

## Context

ADR-0032 recorded four practices of 1.0.0 that the November 2026 audit found done right, so that later changes would not undo them. Its third practice let only a TOTP code open the session of an account with 2FA. Recovery codes now sign in once in place of a TOTP code (ADR-0238, entry `auth.recovery-codes`), so that practice names both second factors; the other three stay as they were.

## Decision

UNOS keeps:

- password hashes made with bcrypt, 12 rounds (`hash_password` in `supervisor/src/auth/utils.py`; the library change is ADR-0026);
- one fixed JWT algorithm per install: tokens are decoded only with the configured algorithm, never with the one a token names (ADR-0028);
- no token before the second factor: a login to an account with 2FA gets a token only with a valid TOTP code, each accepted only once (ADR-0067), or with one of the account's unused recovery codes, which that sign-in uses up (ADR-0238); a login that sends neither is answered `requires_totp` without a token, and one with a wrong code 401 (`login` in `supervisor/src/auth/router.py`; `supervisor/tests/test_totp_once.py`, `supervisor/tests/test_recovery_codes.py`);
- API tokens (prefix `unos_`) stored only as SHA-256 hashes and shown once, when they are created.

## Consequences

- A copy of the database holds hashes of the passwords, API tokens and recovery codes, not the values themselves; the TOTP secrets are stored as they are (ADR-0238).
- A lost API token cannot be shown again: a new one has to be created. Lost recovery codes cannot either: new ones replace them.
- An account with 2FA has two ways past the password, a code of the authenticator app and a recovery code. A change that adds another one supersedes this ADR.
