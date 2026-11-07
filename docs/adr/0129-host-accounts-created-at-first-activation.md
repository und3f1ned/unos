---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
supersedes: [ADR-0128]
---

# ADR-0129: Host accounts are created at the first activation

## Context

An account without a password cannot sign in, and every host account written early is a
write of /etc on the device's system disk, which UNOS writes only on real changes
(ADR-0082).

## Decision

A host account is created at the first activation (`accounts.activate`). Its uid is reserved
and committed in the account row before `useradd` runs, so a crash after `useradd` leaves a
consistent row. The reconcile recreates a missing host account only for rows that are
active, whose password Samba still holds; rows that are allowed but not active get none.
Disallowing keeps the host account, locked and without a passdb entry, so its uid stays with
the user; a user's deletion removes it, and the uid is never handed out again (ADR-0109).

Implemented in `supervisor/src/shares/accounts.py` (`activate`, `_reconcile_host`); tested
in `supervisor/tests/test_smb_accounts.py` (TestAllow: allowing writes the database only)
and `supervisor/tests/test_smb_reconcile.py` (a missing host account of an active row).

## Consequences

Allowing SMB writes nothing on the host, and the first activation writes /etc once. A user's
uid is fixed from the first activation on, whether or not the user keeps SMB.

## Considered options

- The reconcile creates host accounts for every SMB-enabled user (ADR-0128): writes for
  accounts that cannot sign in.
