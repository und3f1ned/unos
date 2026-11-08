---
status: accepted
date: 2026-11-07
area: auth
decided-by: agent
---

# ADR-0251: A sign-in answers while /data is full, and the records it cannot write go to the supervisor's log

## Context

A sign-in writes a row of the login history (SQLite) and an entry of the audit log (ADR-0224) to `/data` before it answers, and neither write caught its error. With `/data` full, which is on the system disk (ADR-0214), every sign-in answered 500, also the one of an administrator who wants to free space. ADR-0023 lists this as debt; the entry is `auth.sign-in-with-full-system-disk`.

## Decision

- `record_login_attempt` in `supervisor/src/security/router.py`: a commit that raises SQLAlchemy's `OperationalError` is rolled back, the attempt is logged at ERROR as `Could not store this login attempt in the login history: <its fields as JSON> (<the error>)`, and the sign-in goes on. That error is SQLite's "database or disk is full", and also a journal that cannot be created, a read-only or failing disk, or a lock held past the busy timeout (ADR-0079). The rollback expires the rows the caller loaded, so `login` reads the user name before.
- `log_action` in `supervisor/src/audit/router.py`: an entry whose append raises `OSError` is logged at ERROR as `Could not write this audit entry to the audit log: <its JSON line> (<the error>)`, and the error goes on to the caller. `login` answers all the same (`_audit_sign_in` in `supervisor/src/auth/router.py`); every other caller handles the error as before.
- So while `/data` is full, a wrong password still answers 401, and the password with a TOTP code answers the session token: TOTP codes are used up in memory (ADR-0067), and the rate limit is kept there too (ADR-0010).
- A recovery code does not sign in while its use cannot be stored: the UPDATE that uses it up is committed before the token (ADR-0238), its failure answers 500, and the code stays unused. A code that is not used up would sign in again.
- The last use of an API token follows the same rule (ADR-0252).
- Tests: `supervisor/tests/test_sign_in_full_disk.py`. SQLite raises its own "database or disk is full" there (`PRAGMA max_page_count` keeps the file at its pages, and `login_attempts` is filled to its last page), the audit append fails with ENOSPC, and a commit fails at its end as on a journal that cannot be written. The dev device's `/data` is never filled for a check (ADR-0214).

## Consequences

- While `/data` is full, the login history and the audit log miss the sign-ins, and the supervisor's log has each of them. On a system disk that is full to the last block, the container's json-file log (ADR-0178) is on the same disk and can lose those lines too.
- An administrator can sign in and free space, for example by deleting old backups.
- Most other audited actions still answer 500 when their entry cannot be written, now with the entry in the log; on a full `/data` their own commit comes first and fails first.

## Considered options

- Refusing a sign-in whose records cannot be written: it would lock everyone out until someone frees `/data` on the host, while failed sign-ins stay limited per client (ADR-0010).
- Catching only SQLite's `SQLITE_FULL`: a full disk also shows as a journal that cannot be created or an fsync that fails, and a read-only `/data` blocks sign-ins the same way.
- Making every audit entry best effort in `log_action`: a decision about all audited actions, beyond the sign-in.
