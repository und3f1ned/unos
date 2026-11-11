---
status: superseded
superseded-by: ADR-0339
date: 2026-11-06
area: shares
decided-by: agent
supersedes: [ADR-0097]
---

# ADR-0098: The guest-related globals follow whether any guest share exists

## Context

Superseded by ADR-0339: an anonymous share list names no share while a guest share exists.

A guest share needs `[global]` settings that let clients without a password in; without a guest share, nothing should. The first design (ADR-0097) set `restrict anonymous = 0` while a guest share existed, which let anyone list the account names without a password.

## Decision

`global_settings(guest_enabled)` in `supervisor/src/shares/smbconf.py` renders one of two variants of `[global]`; exactly three lines differ, in place:

| Line | No guest share | At least one guest share |
| --- | --- | --- |
| `map to guest` | `Never` | `Bad User` |
| `restrict anonymous` | `2` | `1` |
| `server signing` | `mandatory` | `default` (ADR-0100) |

- `restrict anonymous = 2` refuses anonymous IPC$ connections, so nobody can list shares or accounts without a password. With a guest share, 1 keeps the anonymous list of account names closed, while guest connections and the share list still work.
- `map to guest = Bad Password`, which the `[global]` of 1.0.0 forced, is never rendered; the reason for leaving it out was not recorded.
- `render_config` picks the variant from whether any served share has guest access (ADR-0096).
- Tests: `supervisor/tests/test_smbconf.py` (`TestGlobal`), `supervisor/tests/test_share_api.py` (`TestGuest`), `supervisor/tests/test_smbconf_testparm.py` (both variants).

## Consequences

- Turning guest access on for one share, or off for the last one, changes these three lines for the whole server.
- A wrong username is mapped to the guest account only while a guest share exists, and a wrong password never is.

## Considered options

- `restrict anonymous = 0` while a guest share exists (ADR-0097): superseded.
