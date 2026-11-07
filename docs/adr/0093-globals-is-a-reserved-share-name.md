---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
---

# ADR-0093: The share name 'globals' is reserved like 'global'

## Context

Samba reads a section named `[global]` or `[globals]`, in any letter case, as the global section (checked in the Samba 4.22.6 source). A share named globals was therefore never served, and its lines became the defaults of every later share and of IPC$. The share-name rule (ADR-0038) reserved global but not globals.

## Decision

- globals, in any letter case, is reserved like global: `RESERVED_SMB_NAMES` in `supervisor/src/security/validators.py` is global, globals, homes, printers, print$, ipc$, admin$, c$ and d$.
- Creating a share with that name, or renaming one to it, answers 400 "'Globals' is a reserved share name" (with the name as sent). The renderer's guard (`_safe_name` in `supervisor/src/shares/smbconf.py`) refuses the name too.
- A stored or restored row named globals is listed as not served with that problem; it is never renamed automatically, and an administrator renames it.
- The import parser already read `[globals]` as the global section (`is_global_name`).
- Tests: `supervisor/tests/test_shares.py`, `supervisor/tests/test_share_api.py`, `supervisor/tests/test_share_apply.py`.

## Consequences

- No share name that Samba reads as the global section can reach smb.conf: `test_shares.py` checks that no spelling `is_global_name` accepts passes `validate_share_name`.
