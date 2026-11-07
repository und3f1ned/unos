---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
---

# ADR-0123: Samba's copy of a password is written before the bcrypt hash

## Context

Samba and UNOS hold two copies of one password (ADR-0119). When a password is changed
because it leaked, the old one must not stay valid over SMB while the web password has
already changed, and a failed write on either side must not leave the two different.

## Decision

Whenever the account is active, `smbpasswd` runs before the new bcrypt hash is stored:
`accounts.update_password_before_store` sets Samba's copy, then the caller stores the hash
and `accounts.store_password` commits. If `smbpasswd` fails, the answer is 503 and the UNOS
password is not changed either. If the commit fails after `smbpasswd` succeeded, the flow
compensates (ADR-0124). After the commit, `end_smb_sessions` ends the sessions opened with
the old password (ADR-0104). The bcrypt hash is computed before `smb_lock` is taken, so the
lock is not held for the slow hash.

Implemented in `supervisor/src/shares/accounts.py` and `supervisor/src/auth/router.py`;
tested in `supervisor/tests/test_smb_accounts.py` (TestPasswordChange: a failed `smbpasswd`
keeps the old password; the self change and the administrator's reset of an active account).

## Consequences

A password change of an active account fails as a whole while Samba refuses the new
password. The one remaining gap, a failed commit after a successful `smbpasswd`, needs the
compensation of ADR-0124.
