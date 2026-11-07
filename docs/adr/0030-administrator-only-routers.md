---
status: accepted
date: 2026-11-05
area: auth
decided-by: agent
---

# ADR-0030: Whole routers are administrator-only, and regular users get Dashboard and Account

## Context

In 1.0.0 the role was checked in only 3 of 19 routers; every other endpoint needed only a valid token. Because the supervisor is privileged (ADR-0002), any account, a regular user's included, could become root on the host through several of those endpoints, and the UI showed regular users every administrator tab.

## Decision

- Whole routers are administrator-only: `supervisor/src/main.py` includes the storage, shares, Repair permissions, containers, apps, files, snapshots, notifications, scheduler, power, network, logs, health, monitor, backup and updates routers with `dependencies=[Depends(require_admin)]` (`require_admin` in `supervisor/src/auth/utils.py`).
- The routers that mix public, user and administrator endpoints check roles per endpoint: auth, system, security, audit and the WebSockets. Hostname, time zone, reboot and shutdown, the firewall, certificates, the audit log and user management require an administrator, and so do `/api/v1/health/full` and `/api/v1/health/prometheus`.
- Regular users and their API tokens get 403 everywhere else. The UI shows them only Dashboard and Account: their own password, 2FA and login history and, since slice 2, their own SMB access (ADR-0174).
- The last administrator cannot be demoted or deleted.
- Tests: the role matrix of `supervisor/tests/test_authz.py` (`TestRouteMatrix` checks every route for anonymous users, regular users and administrators and requires an explicit decision for each new route; `TestUserManagement`), and `webui/tests/roles.spec.ts` for the regular user's UI.

## Consequences

- Adding a router means choosing its access in `main.py` and in the role matrix, or the test fails.
- A regular account reads its dashboard and manages itself, nothing more; finer roles, such as scopes for API tokens, are open (ADR-0033).

## Considered options

- Checks per endpoint everywhere: 1.0.0 shows how easily one is missed.
- A finer role model: not now; API token scopes may come later (ADR-0033).
