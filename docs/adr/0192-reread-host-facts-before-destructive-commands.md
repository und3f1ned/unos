---
status: accepted
date: 2026-11-07
area: shares
decided-by: agent
---

# ADR-0192: Host facts are read again right before destructive commands, and doubt fails closed

## Context

UNOS removes only its own marked host accounts and passdb entries (ADR-0108), but it decides that from facts it read earlier. An NSS directory can hide accounts from `getent passwd` (sssd with enumeration off) or return lines that do not parse (a ':' in the GECOS), so a foreign account could look missing and its hand-made Samba entry could be removed; and the activation's stale-account step decided on facts read before the uid was committed. An unused `delete_host_account` had no uid check at all.

## Decision

In `supervisor/src/shares/accounts.py`:

- A passwd line that names an account (its first field) but does not parse makes that account foreign, never UNOS's, even when another line of the name parses (`_unreadable_account`). Its uid is kept when the uid field is a number, so that no uid allocation picks it.
- When the caller's facts say that passdb holds a UNOS entry, `_delete_unos_entry` reads the account again by name (`getent passwd <name>`) and lists passdb again (`pdbedit -L`) right before `pdbedit -x`, and only those fresh facts decide; a refusal updates the caller's view. `delete_samba_entry`, which has just read both by name, skips the second read.
- The reconcile, the activation's stale-account step and the deletion of a user remove host accounts only through `_remove_host_account(name, expected_uid=...)`, which reads the account again by name right before `userdel` and answers "removed", "missing" or "busy". An account without the whole marker is refused with "Refused to remove the host account <name>: UNOS did not create it" (500), and a marked account with another uid counts as missing, so `userdel` does not run.
- The unused `delete_host_account` is removed.
- Tests: `TestForeignAccountsFailClosed` and `TestRemoveHostAccount` in `supervisor/tests/test_smb_accounts.py`; in `supervisor/tests/test_smb_reconcile.py`, the marker checked again right before `userdel` and the accounts that `getent passwd` does not list.

## Consequences

- The extra reads run only on removal paths: a consistent start still runs only the tool check, `getent` three times and `pdbedit -L` (ADR-0082).
- An account whose passwd line does not parse is never touched, even if it is in fact UNOS's: doubt keeps the account.
