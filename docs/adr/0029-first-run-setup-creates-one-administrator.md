---
status: accepted
date: 2026-11-05
area: auth
decided-by: agent
---

# ADR-0029: There are no default credentials: the first visit creates exactly one administrator

## Context

The docs promised a default `admin/admin` account and the old CLAUDE.md an initial administrator from `/etc/unos/init-user.json`, but the code had neither: the first visitor created the administrator, and two concurrent first requests could both succeed.

## Decision

- There is no default account. While no user exists, the `/setup` page (`webui/src/routes/setup`) calls `POST /api/v1/auth/setup`, which creates exactly one administrator, also under concurrent requests: a request that waited for `_setup_lock` sees the first administrator and gets 400 (`setup_admin` in `supervisor/src/auth/router.py`, limited to 3 attempts per minute).
- Once any user exists, setup is refused, and users sign in at `/login`.
- Tests: `supervisor/tests/test_auth.py` (`test_concurrent_setup_creates_one_admin`, `test_setup_is_refused_once_a_user_exists`).

## Consequences

- A fresh install belongs to whoever opens it first, so `docs/INSTALL.md` tells the owner to create the administrator right after installing.
- Test stacks create their administrator through the same endpoint, as the local stand-in does (ADR-0215).
