---
status: accepted
date: 2026-11-11
area: shares
decided-by: agent
supersedes: [ADR-0098]
---

# ADR-0339: The guest-related globals follow whether any guest share exists, and an anonymous share list names no share

## Context

ADR-0098 said that with a guest share, `restrict anonymous = 1` keeps the anonymous list of account names closed "while guest connections and the share list still work". On the dev device on 2026-11-11 (`scripts/dev/scenario.sh shares`, main b290ada), with a guest share served, a null session's share list (`smbclient -L -N`) succeeded but named no share: Samba refuses anonymous callers the share enumeration under `restrict anonymous = 1`. A null session to the guest share by its name listed and read it, and its write was refused. The other rules of ADR-0098 stay and are restated here.

## Decision

`global_settings(guest_enabled)` in `supervisor/src/shares/smbconf.py` renders one of two variants of `[global]`; exactly three lines differ, in place:

| Line | No guest share | At least one guest share |
| --- | --- | --- |
| `map to guest` | `Never` | `Bad User` |
| `restrict anonymous` | `2` | `1` |
| `server signing` | `mandatory` | `default` (ADR-0100) |

- `restrict anonymous = 2` refuses anonymous IPC$ connections, so nobody can list shares or accounts without a password.
- With a guest share, `restrict anonymous` stays 1, not 0 (ADR-0097): the anonymous lists of account names and of shares stay closed, and a guest reaches a guest share by its name (`\\<host>\<share>`). The share is not offered in an anonymous share list; whether a client that signs in as a guest sees it in a list is not checked.
- `map to guest = Bad Password` is never rendered.
- `render_config` picks the variant from whether any served share has guest access (ADR-0096).
- Tests: `supervisor/tests/test_smbconf.py` (`TestGlobal`), `supervisor/tests/test_share_api.py` (`TestGuest`), `supervisor/tests/test_smbconf_testparm.py` (both variants); on the dev device the `shares` area of `scripts/dev/scenario.py`, which reports what the anonymous share list names instead of expecting the guest share in it.

## Consequences

- Turning guest access on for one share, or off for the last one, changes these three lines for the whole server.
- A wrong username is mapped to the guest account only while a guest share exists, and a wrong password never is.
- A guest needs the share's name: an anonymous share list does not offer it.
- The guest warning of ADR-0096 still says that the share's name is visible to anyone on the network: a share's name is no secret (every user with SMB lists it, and names are guessed), so the warning keeps to the wider exposure.

## Considered options

- `restrict anonymous = 0` while a guest share exists, so that an anonymous share list names the guest shares: it also lists the account names without a password (ADR-0097).
