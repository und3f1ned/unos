---
status: superseded
date: 2026-11-06
area: deploy
decided-by: agent
superseded-by: ADR-0180
---

# ADR-0179: Uninstall removes the SMB host accounts

## Context

Superseded by ADR-0180: uninstall keeps the accounts and removes only their exact passdb entries.

UNOS creates a real host account for every SMB user, marked with the GECOS `UNOS SMB user`, a uid in 70000-79999 and the group `unos-smb` (gid 70000), plus a tdbsam entry in Samba's passdb (ADR-0105). An uninstall should not leave UNOS's accounts and passwords behind.

## Decision

The second design of the SMB slice had `deploy/uninstall.sh` run a step `remove_smb_accounts`, after the confirmation and before the Samba configuration was restored: for every marked account that `getent passwd` lists with a valid, non-reserved name, `pdbedit -x -u NAME` (errors ignored), then `userdel NAME` (never `-r` or `-f`, a warning on failure). The group `unos-smb` was kept, and the completion text said so.

## Consequences

- Once the accounts were gone, a later installation could hand their uids, and the files on the pools that belong to them, to new users.
- tdbsam finds an entry by its lower-cased name, so the unconditional `pdbedit -x` could remove a hand-made entry stored in another letter case.
- Both are why ADR-0180 replaced this rule before it was released.
