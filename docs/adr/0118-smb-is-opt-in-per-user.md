---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
---

# ADR-0118: SMB is opt-in per user: an administrator allows it, and a password activates it

## Context

Samba stores an unsalted password equivalent, and SMB allows unthrottled guessing without
two-factor authentication (ADR-0102). Which users carry that exposure is a decision like
share access, so it belongs to an administrator, while the password itself can come only
from someone who knows it (ADR-0119).

## Decision

SMB is opt-in per user:

- An administrator allows SMB for a user (`POST /auth/users/{id}/smb/enable` without a
  password, the "Allow SMB access" toggle of the Users page's edit dialog): the user's
  `smb_accounts` row gets `enabled = 1`. Allowing writes only the database.
- The account becomes active only when Samba gets the user's current password: from the user
  on the Account page ("Turn on SMB" with the current password, plus the two-factor code for
  users with 2FA, ADR-0068), or from an administrator (a password sent to
  `POST /auth/users/{id}/smb/enable`, as the edit dialog's "Also turn SMB on with this
  password" does, or user creation with `smb_enabled`). A user's own activation without an
  allowance answers 403 "An administrator must allow SMB access for your account first".
- The Add user dialog's "Allow SMB (network file) access" sends `smb_enabled` with the new
  user's password, so it is an administrator's activation, not an allowance: it creates the
  host account and the passdb entry. When the activation fails, the user is still created,
  SMB stays "Off", and the answer carries the warning "User created, but SMB access could not
  be turned on: REASON".
- A user can turn their own SMB off (`POST /auth/me/smb/disable`); the allowance is kept. An
  administrator's disallow (`POST /auth/users/{id}/smb/disable`) also clears it.
- The states (`account_state.derive_smb_state`): "off" (no row, or not allowed),
  "password_needed" (allowed but not active, shown as "Password needed"), "active", and
  "unavailable" for administrators and names SMB cannot use.

Implemented in `supervisor/src/shares/accounts.py` (allow, activate, deactivate, disallow),
`supervisor/src/auth/router.py`, `webui/src/lib/components/account/SmbAccessCard.svelte` and
`webui/src/routes/users/+page.svelte`; tested in `supervisor/tests/test_smb_accounts.py`
(TestAllow, TestSelfActivation, TestAdminActivation, TestDeactivate, TestDisallow),
`webui/tests/account-smb.spec.ts` and `webui/tests/users.spec.ts`.

## Consequences

Nobody has SMB by default, and a new user also has no share access until it is granted
(ADR-0075). An allowed user whose password never reached Samba stays "Password needed" until
someone who knows the password turns SMB on.
