---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
supersedes: [ADR-0099]
---

# ADR-0100: Server signing is mandatory unless a guest share exists

## Context

Mandatory signing stops NTLM relay against the server, and every SMB2 or newer client can sign; SMB1 is off (ADR-0087). Guest connections cannot be signed, though. Samba 4.22 itself accepts unsigned guest and anonymous sessions under `server signing = mandatory` (checked in its source), but clients may refuse to connect as a guest to a server that requires signing. That client behavior was not verified with real clients.

## Decision

- With no guest share, the rendered `[global]` has `server signing = mandatory`; while at least one guest share exists, it has `server signing = default` (ADR-0098, `global_settings` in `supervisor/src/shares/smbconf.py`).
- The guest warning says that connections to the server are not required to be signed while a guest share exists (ADR-0096). `CHANGELOG.md` and `docs/USER_GUIDE.md` give client compatibility as the reason for the switch.
- Encryption stays at Samba's default (`server smb encrypt = default`).
- Tests: `supervisor/tests/test_smbconf.py` (`TestGlobal`), `supervisor/tests/test_share_api.py` (`TestGuest`).

## Consequences

- One guest share lowers signing for every connection to the server, not only for the guest share.
- Turning the last guest share off makes signing mandatory again at the next apply.

## Considered options

- Signing at Samba's default always (ADR-0099): superseded.
- Keeping mandatory signing while a guest share exists: not taken, for client compatibility, which is not verified.
