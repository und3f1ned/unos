---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
---

# ADR-0131: A regular user's own SMB and password flows answer only fixed texts

## Context

The warnings of an apply and of ending sessions carry the raw output of smbcontrol and
systemctl, host paths and the names of shares that a regular user cannot see
(`access based share enum` hides them), and the error details of a failed host command
carried a line of its output with host paths. A regular user must not learn any of it from
their own requests.

## Decision

`POST /auth/me/smb/enable`, `POST /auth/me/smb/disable` and `POST /auth/change-password`
answer a regular user only with fixed texts:

- At most one apply warning, "SMB access changed, but Samba has not applied every change
  yet; an administrator can see why on the Shares page", and one session warning, "Some of
  your open SMB connections could not be ended; they end when your devices disconnect"
  (`accounts.SELF_APPLY_WARNING`, `accounts.SELF_SESSIONS_WARNING`,
  `FlowResult.self_warnings`).
- An error whose text carries a host command's output (`accounts.SmbHostCommandFailed`: a
  program that could not run, `getent`, `pdbedit`, `groupadd`, `useradd`, `userdel`,
  `smbpasswd`) keeps its status and reads "FLOW: a command on the NAS failed; try again
  later or ask an administrator (details are in the supervisor log)", where FLOW is "SMB
  could not be turned on", "SMB access of NAME could not be revoked" or "The password was
  not changed".
- A compensation that fails too reads "CAUSE and Samba could not be reset; SMB access is
  repaired at the next start".

The full texts are logged at WARNING: warnings as "SMB change of USER: TEXT", errors as
"FLOW (user USER): TEXT", and a failed compensation as "Samba could not be reset after a
failed SMB change: REASON". The administrator's flows and `GET /shares/smb-status` keep the
full texts, and the other texts of the self flows (missing tools, timeouts, refusals, every
4xx) are the same as for administrators.

Implemented in `supervisor/src/shares/accounts.py` (`_http`, `self_flow_warnings`,
`_compensation_error`) and `supervisor/src/auth/router.py`; tested in
`supervisor/tests/test_smb_accounts.py` (TestSelfFlowWarnings, TestSelfFlowErrors) and
`supervisor/tests/test_smb_integration.py` (a regular user's answers name no other share and
no host output).

## Consequences

A regular user learns that something failed and whom to ask, not why; an administrator finds
the details on the Shares page or in the supervisor log.
