---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
---

# ADR-0108: UNOS changes only its own marked host accounts and passdb entries

## Context

UNOS creates and removes host accounts and Samba passdb entries (ADR-0105) on a host that also has accounts of its own: system users, the owner's users, Samba entries made by hand. Removing or changing one of them by mistake would lock people out or give their files to someone else. Some host tools are broad: `userdel -f` also removes a group named like the user, even when it is another account's group (with `USERGROUPS_ENAB yes`), and tdbsam keys its entries by the lower-cased name, so `pdbedit -x -u alice` also removes an entry stored as `Alice`.

## Decision

- A host account is UNOS's ("marked") only with the GECOS exactly `UNOS SMB user`, a uid in 70000-79999 and the primary gid 70000 (`HostAccount.ours` in `supervisor/src/shares/accounts.py`). UNOS creates, changes or removes no other account.
- `useradd` only creates marked accounts. `userdel` only names marked accounts, never with `-f` and never with `-r`; the account is read again right before it (ADR-0192).
- `pdbedit -x` only removes UNOS passdb entries: an entry stored under exactly a row's name (UNOS names are lower case) while the host account of that name is UNOS's or missing (`is_unos_passdb_entry`). An entry that differs only in case is foreign.
- Every name passes `constants.is_valid_linux_name` and every uid the range check at the point where it is put into argv; a failed check raises `SmbHostError`, never an `assert`, which disappears under `python -O`.
- Tests: `supervisor/tests/test_smb_accounts.py` (`TestCollisions`, `TestForeignAccountsFailClosed`, `TestRemoveHostAccount`), `supervisor/tests/test_smb_reconcile.py`.

## Consequences

- A host account or Samba entry with a UNOS user's name that UNOS did not create blocks that user's SMB activation with 409 and an instruction to remove it on the host or to use another UNOS user.
- `deploy/uninstall.sh` follows the same boundary (ADR-0180).
