---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
---

# ADR-0125: No SMB password appears in argv, logs, errors, HTTP answers or the database

## Context

Command lines are visible to every process of the host, and logs and error texts travel
further than their request. FastAPI's default 422 answer repeats each rejected value, so a
rejected password came back in the body and ended up wherever answers are logged.

## Decision

- Passwords reach Samba only on standard input: `smbpasswd -s -a <name>` through
  `host.run_on_host(..., input=...)`. No secret is ever in argv, and no shell is used.
- No SMB password appears in a log line, an exception text, an HTTP detail or the database;
  when `smbpasswd` fails, the password is removed from the line of its output that the
  answer shows (`accounts._last_line`).
- The handler for `RequestValidationError` in `supervisor/src/main.py` removes `input` and
  `ctx` from every error of every 422 answer.
- Objects that carry a password or its hash leave it out of their repr
  (`interfaces.PrevAccount.hashed_password`, `accounts.PasswordUpdate.current_password`).
- Scripts that talk to the device keep secrets off command lines as well (ADR-0212).

Tested in `supervisor/tests/test_validation_errors.py`,
`supervisor/tests/test_smb_accounts.py` (secrets never reach a command line; the `smbpasswd`
error never contains the password) and `supervisor/tests/test_smb_models.py` (the repr hides
the password hash).

## Consequences

A failed `smbpasswd` is diagnosed from its cleaned last line and the log, never from the
password. 422 answers name the field and the reason, without the value.
