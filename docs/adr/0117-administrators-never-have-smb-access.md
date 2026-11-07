---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
---

# ADR-0117: Administrators never have SMB access

## Context

An administrator's UNOS password controls the whole NAS, while SMB has no two-factor
authentication and no login rate limit (ADR-0102). An administrator's password on SMB would
put a root-equivalent secret on an unthrottled, password-only channel. Access rows of a user
whose SMB was off also survived a promotion and came back after a demotion.

## Decision

An administrator can never have an active SMB account and is "unavailable" for SMB, with the
reason "Administrators cannot use SMB: their password controls the whole NAS, and SMB has no
two-factor authentication and no login rate limit. Use a regular account for files."
(`account_state.ADMIN_REASON`).

- First-run setup with `smb_enabled` answers 400 "Administrators cannot use SMB"; allowing
  or activating SMB for an administrator answers 409 with the reason; a share access entry
  for an administrator answers 400.
- A promotion first revokes the account: the passdb entry is removed, and when that fails
  the promotion is refused with 503. In the transaction that sets `is_admin` it then always
  deletes the user's share access rows, with or without an account row. After the commit,
  smb.conf is applied when something changed and the account's sessions end
  (`accounts.revoke_for_promotion`, `accounts.finish_promotion`).
- The reconcile turns off the rows of administrators and deletes their access rows, and
  `service.purge_orphans` deletes access rows of administrators (ADR-0137).

Implemented in `supervisor/src/shares/account_state.py`, `supervisor/src/shares/accounts.py`
and `supervisor/src/auth/router.py`; tested in `supervisor/tests/test_smb_accounts.py`
(TestPromotion, TestAllow, TestAdminActivation), `supervisor/tests/test_usernames.py`
(TestSetup), `supervisor/tests/test_smb_reconcile.py` (admin rows are cleaned) and
`webui/tests/users.spec.ts` (a promotion of a user with SMB or share access warns first).

## Consequences

Administrators use a separate regular account for their files. A demotion brings back
neither SMB nor share access: the user starts from nothing.
