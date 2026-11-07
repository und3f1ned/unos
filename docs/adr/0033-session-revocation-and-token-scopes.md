---
status: proposed
date: 2026-11-05
area: auth
decided-by: agent
---

# ADR-0033: Add session revocation, safer token storage, 2FA recovery codes and token scopes

## Context

Phase 0 and slices 1 and 2 left four session and token items of the audit and of the Phase 0 follow-ups open, because each needs its own design.

## Decision

Proposed, to be designed in a later slice:

- revocable sessions, so that a logout ends the session on the server too; the audit suggested a token version stored in the database. Today logout only forgets the token in the browser.
- a safer place for the web UI's token than `localStorage` (ADR-0009);
- 2FA recovery codes that work: codes can be generated, but `POST /api/v1/auth/recovery-codes/verify` answers 501;
- scopes for API tokens, which today act with all the rights of their user.

## Consequences

- Until then a JWT stays valid until it expires (24 hours by default, `UNOS_JWT_EXPIRE_MINUTES`), whatever happens in the browser.
- Each item is a `feature-list.json` entry that names this ADR (ADR-0203): `auth.session-revocation`, `auth.token-storage`, `auth.recovery-codes` and `auth.api-token-scopes`.
