---
status: superseded
date: 2026-11-05
area: auth
decided-by: agent
superseded-by: ADR-0233
---

# ADR-0032: Auth keeps bcrypt, one JWT algorithm, TOTP before any token, and hashed API tokens

## Context

Superseded by ADR-0233: the third practice is now no token before the second factor, a TOTP code or an unused recovery code (ADR-0232); the other three stay.

The November 2026 audit rated authentication and permissions 2 of 5, but found four practices of 1.0.0 done right. Recording them keeps later changes from undoing them.

## Decision

UNOS keeps:

- password hashes made with bcrypt, 12 rounds (`hash_password` in `supervisor/src/auth/utils.py`; the library change is ADR-0026);
- one fixed JWT algorithm per install: tokens are decoded only with the configured algorithm, never with the one a token names (ADR-0028);
- no token before the TOTP check: a login to an account with 2FA answers `requires_totp` without a token until a valid code comes (`login` in `supervisor/src/auth/router.py`), and each code is accepted only once (ADR-0067);
- API tokens (prefix `unos_`) stored only as SHA-256 hashes and shown once, when they are created.

## Consequences

- A copy of the database holds hashes of the passwords and API tokens, not the values themselves.
- A lost API token cannot be shown again: a new one has to be created.
