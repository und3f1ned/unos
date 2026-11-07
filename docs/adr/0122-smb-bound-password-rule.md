---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
---

# ADR-0122: SMB-bound passwords need at least 8 characters, at most 72 bytes, no control characters

## Context

SMB has no login rate limit and no two-factor authentication (ADR-0102), `smbpasswd` reads
the password as lines on standard input, and the same password is also the UNOS password,
whose bcrypt hash reads at most 72 bytes.

## Decision

Every password that is given to Samba must pass `constants.smb_password_problem`: at least 8
characters, at most 72 bytes in UTF-8, valid Unicode text, and no control characters such as
tabs or line breaks; otherwise the answer is 400 with the rule, for example "SMB passwords
must have at least 8 characters". It is checked by every activation, by user creation with
`smb_enabled` before anything is created, by the password change of an active account, and
by `accounts.set_samba_password` itself.

The UNOS-wide policy is unchanged: the API accepts shorter passwords for users without SMB
(`POST /auth/users` without `smb_enabled`, `PUT /auth/users/{id}`,
`POST /auth/change-password`), and the 8-character minimum for UNOS passwords exists only in
the web UI. A user whose UNOS password is shorter changes it before turning SMB on; the
Account page and `docs/USER_GUIDE.md` say so.

Implemented in `supervisor/src/shares/constants.py` and `supervisor/src/shares/accounts.py`;
tested in `supervisor/tests/test_smb_models.py` (SMB passwords accepted and refused),
`supervisor/tests/test_smb_accounts.py` (a password that SMB refuses; a short password for
an active account) and `webui/tests/account-smb.spec.ts` (a password that SMB cannot use).

## Consequences

A user with a short UNOS password cannot turn SMB on before changing it, and an active
account cannot change to a password that breaks the rule. Common passwords are not refused
(ADR-0102).
