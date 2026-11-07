---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
supersedes: [ADR-0126]
---

# ADR-0127: An account that is not active has no passdb entry, never a disabled one

## Context

A disabled tdbsam entry keeps the account's NT hash, a password equivalent, on the system
disk, and enabling the entry again would revive the old password. Deleting the entry costs
the same one write and leaves nothing behind.

## Decision

An account that is not active has no passdb entry, and "Password needed" means that Samba
has no entry for the account. Turning SMB off, disallowing it, a promotion, a user's
deletion and the compensation of ADR-0124 remove the entry with `pdbedit -x -u <name>`, only
when it is a UNOS entry (ADR-0108). The reconcile removes the UNOS entry of every row that
is not active and turns a "set" row without an entry into "needed". Disabled entries are
never used: UNOS runs `smbpasswd` only as `smbpasswd -s -a` to set a password.

Implemented in `supervisor/src/shares/accounts.py`; tested in
`supervisor/tests/test_smb_accounts.py` (TestDisallow, TestDeactivate, TestPromotion,
TestDeleteUser) and `supervisor/tests/test_smb_reconcile.py` (the passdb entry of a row that
needs a password).

## Consequences

Turning SMB on again always needs the password, since nothing of the old one is kept. Failed
sign-ins of a revoked account write nothing to the system disk, because Samba finds no entry
to update (ADR-0159).

## Considered options

- Disabled passdb entries (ADR-0126): they keep the old password equivalent on disk.
