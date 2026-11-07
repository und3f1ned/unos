---
status: accepted
date: 2026-01-24
area: auth
decided-by: owner
---

# ADR-0010: Login is limited to 5 attempts per minute per client IP

## Context

The login endpoint needs protection against password guessing. A limit per client address works only if the supervisor sees the real client: until Phase 0 every request arrived from Caddy's address, so all clients shared one limit, and one of them could lock everybody else out.

## Decision

- `POST /api/v1/auth/login` accepts 5 attempts per minute per client address (slowapi: `@limiter.limit("5/minute")` with `get_remote_address` in `supervisor/src/auth/router.py`). The first-run setup is limited to 3 attempts per minute, and turning on one's own SMB access to 5 per minute.
- The address is the real client's, because uvicorn takes it from `X-Forwarded-For` only on connections from the web UI container (ADR-0048).
- The limit was added on 2026-01-24 (commit c2ad8bf) from the codebase review that became the roadmap of `docs/IMPROVEMENTS.md`, which listed it as a P0 item, done (ADR-0008).
- Test: `supervisor/tests/test_authz.py` (`TestRealClientAddress::test_login_rate_limit_is_per_client_not_global`: five failed logins of one client, then 429 for that client only).

## Consequences

- The counters live in the supervisor's memory: a restart resets them, and counting writes nothing to disk.
- Clients behind one address, for example behind NAT, share one limit.
- UNOS does not limit SMB logins (ADR-0102).
