---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
---

# ADR-0104: An account's SMB sessions end at every password change and revocation

## Context

Samba never authenticates an open SMB session again, and close-share (ADR-0103) ends tree connects only, not the session. After a password change or a revocation, a session opened before could therefore connect again to every share still granted to the account, with the old password or with no SMB access at all.

## Decision

- `read_smb_sessions()` in `supervisor/src/shares/accounts.py` lists the sessions with `smbstatus --json` (read-only) and parses it defensively: an unknown shape gives a warning, never an exception that blocks a flow.
- `end_smb_sessions(names)` sends `smbcontrol <pid> shutdown` (timeout 15 s) to every smbd process that serves a session of one of the names, compared ignoring case. A pid must be an integer above 1 that smbstatus lists for such a session. A failure is a warning.
- It runs after the password change of an active account (the user's own and an administrator's reset), after turning SMB off, disallowing, promotion to administrator and deletion (before `userdel`, which is then no longer busy), and for every account that the reconcile turns from active to not active, whether or not the smb.conf apply succeeded. The startup reconcile also ends the sessions of every user that is not an active account (ADR-0138).
- `smbstatus` is one of the Samba tools that the account flows check for, and the fake host of the tests emulates sessions and their shutdown (`supervisor/tests/samba_fake.py`).
- Tests: `supervisor/tests/test_smb_accounts.py` (`TestReadSmbSessions`, `TestEndSmbSessions`, `TestPasswordChange`), `supervisor/tests/test_smb_reconcile.py` (`TestSessions`).

## Consequences

- After a password change, the account's clients are disconnected and must sign in again with the new password.
- A session that cannot be listed or ended stays open until its client disconnects; the warning says so.

## Considered options

- `smbcontrol smbd kill-client-ip` as a fallback: rejected, because it ends the sessions of every user behind one address.
