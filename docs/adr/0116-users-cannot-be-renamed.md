---
status: superseded
date: 2026-11-06
area: shares
decided-by: agent
superseded-by: ADR-0368
---

# ADR-0116: UNOS users cannot be renamed yet

## Context

Superseded by ADR-0368: an administrator renames a user, and the user's SMB account moves to the new name with its uid.

The SMB and host account name is the UNOS username (ADR-0113), and Samba needs the plaintext
password to create the passdb entry of a new name, so renaming a user means recreating the
SMB account with the user's password.

## Decision

UNOS users cannot be renamed. `PUT /auth/users/{id}` accepts `username` only as a guard
against reused ids (ADR-0133): a value other than the user's current name answers 409 "User
N is now 'x'; reload the page", and nothing changes. Renaming stays in the backlog until it
can recreate the SMB account with the password.

Tested in `supervisor/tests/test_smb_accounts.py` (TestUsernamesInRequests).

## Consequences

A user whose name SMB cannot use needs a new account to get SMB.
