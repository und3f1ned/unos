---
status: accepted
date: 2026-11-06
area: security
decided-by: agent
---

# ADR-0065: Login input and audit entries are bounded

## Context

A failed login is recorded before anyone is authenticated: in the audit log and in the login history. One unauthenticated login with a 1 MiB username added about 3 MB to `/data/audit_log.json`, a file that every audit write reads and rewrites as a whole on the system disk (ADR-0066). The request limit of ADR-0062 caps a body at 1 MiB, which is still far more than any username needs.

## Decision

- `LoginRequest` in `supervisor/src/auth/schemas.py` limits the username to 255 characters, the password to 1024 and the two-factor code to 16; a longer value gets 422, and the 422 handler of `supervisor/src/main.py` never echoes the values (it drops `input` and `ctx` from every error).
- `log_action` in `supervisor/src/audit/router.py` keeps at most 128 characters of the username, 256 of the User-Agent, 256 of the resource id and 1024 of the details (`MAX_*_CHARS`, plain truncation).
- `record_login_attempt` in `supervisor/src/security/router.py` keeps 128 characters of the username (`LOGIN_ATTEMPT_USERNAME_CHARS`); the User-Agent stays cut at 500.
- The SMB self-activation request bounds its password and code the same way (`SmbSelfEnableRequest`).
- Tests: `supervisor/tests/test_login_bounds.py`, `supervisor/tests/test_validation_errors.py`.

## Consequences

- An audit entry has a bounded size, whoever causes it.
- Long values are cut without a marker, so an entry can hold a shortened username or detail.
- The whole-file rewrite of the audit log stays until it becomes append-only (ADR-0066).
