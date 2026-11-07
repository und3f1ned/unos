---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
supersedes: [ADR-0120]
---

# ADR-0121: A password change updates Samba only for active accounts, and activation is explicit

## Context

A user who turned SMB off must not get it back silently with the next password change, and
sessions opened with an old password must not outlive it: Samba never authenticates an open
session again (ADR-0104).

## Decision

A self password change (`POST /auth/change-password`) or an administrator's reset
(`PUT /auth/users/{id}` with a password) updates Samba only when the account is active
(`accounts.update_password_before_store` does nothing for other accounts); after the commit,
the account's open SMB sessions end (`accounts.store_password`).

Activation is always an explicit step: the Account page, or an administrator's password set
through `POST /auth/users/{id}/smb/enable`. On the Users page, a user in the state "Password
needed" has one password section with the checkbox "Also turn SMB on with this password",
checked by default: checked, the page sends the password to the SMB endpoint; unchecked, it
sends a plain `PUT`.

Implemented in `supervisor/src/shares/accounts.py`, `supervisor/src/auth/router.py` and
`webui/src/routes/users/+page.svelte`; tested in `supervisor/tests/test_smb_accounts.py`
(TestPasswordChange) and `webui/tests/users.spec.ts` (a new password can also turn SMB on; a
new password of a user with SMB on changes the SMB password too).

## Consequences

A password change of an account that is not active touches nothing on the host. An
administrator who sets a new password for a user waiting for SMB decides in the same dialog
whether that password also turns SMB on.

## Considered options

- Set the SMB password at every creation, reset and self change (ADR-0120): it turned SMB on
  again for users who had turned it off.
