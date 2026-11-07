---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
---

# ADR-0119: The SMB password is the UNOS password, captured only where the plaintext is given

## Context

UNOS stores only a bcrypt hash of each user's password, while Samba needs the plaintext to
store its own copy. A user should have one password for the web UI and for SMB, and Samba
can get it only where a request carries it.

## Decision

A user's SMB password is their UNOS password. Samba gets it only from requests that carry
the plaintext for that purpose: an activation with the password (the Account page, an
administrator's password set through `POST /auth/users/{id}/smb/enable`, user creation with
`smb_enabled`) and a password change of an active account (ADR-0121). It is never captured
at a login. unos.db stores nothing of the SMB password, neither a hash nor the plaintext:
the `smb_accounts` row records only whether Samba has the password (`password_state`) and
since when (`password_set_at`).

Implemented in `supervisor/src/shares/accounts.py` (activate, update_password_before_store)
and `supervisor/src/auth/router.py`; tested in `supervisor/tests/test_smb_accounts.py`
(TestSelfActivation, TestAdminActivation, TestPasswordChange).

## Consequences

A password change of an active account must reach Samba too (ADR-0123), and the old password
then stops working on both. A login never turns SMB on: turning it on is always an explicit
step.
