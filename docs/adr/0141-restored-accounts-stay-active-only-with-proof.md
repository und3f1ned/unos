---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
supersedes: [ADR-0140]
---

# ADR-0141: After a restore an SMB account stays active only with proof

## Context

Samba keeps its passdb outside the database, so after a restore Samba holds the passwords of
the live accounts, not necessarily those of the restored rows. Logging everyone out of SMB
at every restore (ADR-0140) also hit restores made only to roll back shares, and trusting
restored "set" rows would let an archive claim active accounts.

## Decision

On the extracted database, before it replaces the live one (ADR-0139), a restored row stays
"set" only when the live database had an active account with the same username, linux name
and uid, the restored user is not an administrator, and the restored bcrypt string equals
the live one, salt included (`accounts._proven`). Every other row becomes "password needed"
with `password_set_at` NULL, a row that was "set" gets the message "SMB must be turned on
again for X", and the reconcile then removes its passdb entry.

A restored database is authoritative for SMB only when its import marker
(`smb_state.shares_imported_at`) is set. A database that is not authoritative, taken before
its import or without SMB tables, keeps the live accounts of the users it has, carried over
by username under the same proof, and never takes a uid that a live account of another name
holds (ADR-0112). `next_uid` is raised above every uid seen, restored or live (ADR-0109).

Implemented in `supervisor/src/shares/accounts.py`; tested in
`supervisor/tests/test_restore_accounts.py` (TestProof, TestCarryOver, TestNextUid) and
`supervisor/tests/test_backup_restore.py` (TestAccountStep).

## Consequences

A restore that brings back the same passwords keeps SMB working, and every account whose
password cannot be proven loses SMB until the password is entered again.

## Considered options

- Every SMB account needs its password again after a restore (ADR-0140): it logged everyone
  out of SMB at every restore.
