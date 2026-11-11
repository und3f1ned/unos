---
status: accepted
date: 2026-11-11
area: security
decided-by: agent
---

# ADR-0343: A user's own sign-out, password change and two-factor changes are audited about the user, and answer when their entry cannot be written

## Context

`AuditAction` has `logout`, `password_change`, `totp_enabled` and `totp_disabled`, but only the
emergency `reset-admin` wrote the last three, so a session in the wrong hands could turn
two-factor sign-in off without a trace (step 4 of `security.audit-admin-actions`). ADR-0320 keeps
entries to real changes; ADR-0251 lets only a sign-in answer when its entry cannot be written.

## Decision

- `POST /auth/logout`, `POST /auth/change-password`, `POST /auth/totp/enable` and
  `POST /auth/totp/disable` of `supervisor/src/auth/router.py` write their action after the
  change is committed (`_own_account_audit`): the user as `username`, the client address and
  browser of the request, resource type `user` with the user name as its id, as `reset-admin`
  writes them (`supervisor/src/emergency.py`).
- Details and severity: `logout` none and info; `password_change` "Password changed by the user"
  (", also for SMB" when Samba's copy changed too) and info; `totp_enabled` "Two-factor
  authentication turned on by the user" and info; `totp_disabled` "Two-factor authentication
  turned off by the user, with its recovery codes" and warning, since it weakens the sign-in.
- A password change that also set Samba's password writes `smb_account_updated` "SMB password
  changed by the user" (resource type `smb_account`) as well, as ADR-0320 asks for a new SMB
  password and as an administrator's reset does.
- Real changes only (ADR-0320): a refused request writes nothing (a wrong current password, a
  wrong or reused code, 2FA already on or off, an API token at the sign-out, a password Samba
  refused), and a sign-out writes its entry only when it ended the session: `end_session` of
  `supervisor/src/auth/utils.py` says whether a row or a session kept in memory was there, so
  of two sign-outs of one session only the first writes. Each code is accepted once (ADR-0067),
  so two requests cannot both turn 2FA on or off.
- An entry that cannot be written, such as on a full `/data`, is in the supervisor's log
  (`log_action`, ADR-0251) and the request answers what it did: the change is stored, and a 500
  would tell the user it was not. A sign-out of a session that a full `/data` kept in memory
  (ADR-0329) answers 200 as before.
- Tests: `supervisor/tests/test_audit_own_account.py`.

## Consequences

- The audit log shows who signed out, changed their password or turned two-factor sign-in on
  or off, from where, and the Audit Log page's **Action** filter finds these entries.
- Every sign-out appends a line of a few hundred bytes to the audit log on the system disk
  (ADR-0214, ADR-0224), as every sign-in already does.
- While the audit log cannot be written, these changes leave their trace only in the
  supervisor's log, as sign-ins do.
