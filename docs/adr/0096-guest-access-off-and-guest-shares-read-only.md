---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
---

# ADR-0096: Guest access is off by default, and guest shares are read-only

## Context

SMB in 1.0.0 worked only in guest mode (ADR-0059). Slice 2's goal is guest access off by default and safe handling of the guest shares that already exist. A guest needs no password, so whatever a guest can do, anyone on the network can do.

## Decision

- A new share has guest access off (`guest_ok` defaults to false in `supervisor/src/shares/models.py` and `supervisor/src/shares/schemas.py`); an administrator turns it on per share.
- A guest share is read-only for guests: every section has `read only = yes`, and write access comes only from `write list`, which names users (ADR-0088). Guests connect as `nobody` and read through the group bits of the folder (`force group = unos-smb`), and they never write. The import keeps guest access only as read-only (ADR-0086).
- The guest warning (`GUEST_WARNING` in `supervisor/src/shares/service.py`, in the answer of a create or PATCH that turns guest access on, and in the share form of the Shares page) says that anyone on the network can read the share without a password, that its name is visible to anyone on the network, that guests can never write, that connections to the server are not required to be signed while a guest share exists (ADR-0100), and that Windows 11 refuses guest access by default.
- Guest sections have no `valid users` line (ADR-0088), so every Samba account of the host, hand-made ones too, can also read a guest share; `docs/USER_GUIDE.md` says so.
- Tests: `supervisor/tests/test_share_api.py` (`TestGuest`), `supervisor/tests/test_smbconf.py`.

## Consequences

- Turning guest access on for one share changes three lines of `[global]` for the whole server (ADR-0098).
- Files that should be written over SMB need a user with write access.
