---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
---

# ADR-0124: A failed commit after a successful smbpasswd is compensated

## Context

Samba's copy of a password is written first (ADR-0123), so a commit that fails afterwards,
because the database is busy (a backup copying it) or the disk is full, would leave Samba
with a password that UNOS did not store while the row says the account is active.

## Decision

When the commit after a successful `smbpasswd` fails, the flow rolls back and compensates
while it still holds `smb_lock`:

- an activation deletes the passdb entry it just wrote;
- a self password change sets the old password, which it knows, in Samba again; when that
  fails, it deletes the entry and sets the row to "needed" in a fresh session;
- an administrator's reset, or a password set on an account that was already active, deletes
  the entry, sets the row to "needed" in a fresh session and ends the account's sessions.

The answer is 503 "Nothing was changed: the database was busy; try again", or, when the
compensation fails too, a 503 that says SMB access is repaired at the next start (the
startup reconcile removes the passdb entries of rows that are not active). When `smbpasswd`
times out, the flow compensates the same way, since it may have stored the password anyway.
A share mutation discards its staged smb.conf when its commit fails.

Implemented in `supervisor/src/shares/accounts.py` (`_compensate_password`,
`_reset_account`, `_remove_entry`, `_compensation_error`) and
`supervisor/src/shares/service.py` (`_stage_commit_install`); tested in
`supervisor/tests/test_smb_accounts.py` (TestCommitFailureAfterSmbpasswd,
TestSmbpasswdTimeout) and `supervisor/tests/test_share_api.py` (a commit that fails after
staging changes nothing).

## Consequences

Samba and UNOS never keep different passwords for an account that the row calls active; at
worst the user enters the password again. A regular user's own flow gets the cause without
the host's output (ADR-0131).
