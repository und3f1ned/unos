---
status: accepted
date: 2026-11-08
area: auth
decided-by: agent
---

# ADR-0284: Only root on the host resets an administrator's password and two-factor authentication, with the emergency command line

## Context

An administrator who forgot the password, or lost the authenticator app and the recovery codes, cannot sign in, and nobody can reset an administrator's two-factor authentication through UNOS (ADR-0238); with one administrator, the NAS can then no longer be managed. A way back that the web UI or the API offered would be reachable from the LAN, and a password on a command line is readable by every process of the host (ADR-0125). This settles the emergency command line of the entry `system.emergency-runbook-and-cli`.

## Decision

- `python -m src.emergency reset-admin [NAME]` (`supervisor/src/emergency.py`) runs in the supervisor container. Root on the host starts it with `deploy/reset-admin.sh [NAME]`, which refuses to run as another user and runs `docker exec -i unos-supervisor python -m src.emergency reset-admin`, with `-t` when its standard input is a terminal. No route of the API reaches it: no other module of `supervisor/src` imports it.
- NAME is an administrator; without NAME, the only administrator. With several and no NAME, or a NAME that is not an administrator, it lists the administrators and changes nothing. Regular users are not taken: an administrator sets their passwords on the Users page.
- The new password never comes from the command line or the environment: on a terminal it is asked twice without echo (`getpass`), and two different entries change nothing; otherwise it is the first line of standard input, without its line break. It needs at least 8 characters (`MIN_PASSWORD_CHARS`, as the web UI asks), at most 72 bytes in UTF-8 (`MAX_PASSWORD_BYTES` of bcrypt) and no control characters, and is hashed by `hash_password` (bcrypt, 12 rounds, ADR-0239).
- One UPDATE stores the hash and turns two-factor authentication off: `totp_enabled` false, `totp_secret` and `recovery_codes` null, as turning it off does (ADR-0238). It changes the row only while the id, the name, the administrator flag and the two-factor state are those read before the password was asked (a compare-and-swap): an account changed meanwhile is not reset, and the command says to run it again.
- The database is opened as an SQLite URI with `mode=rw`: a missing database is an error, never a new empty file on the system disk.
- After the commit, `log_action` writes the audit entries, by the user `root` (a name that no UNOS user can have, ADR-0115), for the resource `user` NAME, with severity warning: `password_change` "Password reset on the host's command line", and `totp_disabled` "Two-factor authentication turned off on the host's command line, with its recovery codes" when it was on. An entry that cannot be written (a full `/data`) is reported on the error output, which says that the reset was done, and the command exits with 1. The command is a second writer of the audit log, in a process of its own beside the running supervisor: it takes the log's lock across processes, as the supervisor does (ADR-0286).
- Exit status: 0 when the reset is done and recorded, 1 when it is not done or not recorded, 2 for a wrong command line, 130 when interrupted at the prompt.
- Nothing else changes: the account's sessions stay valid until they expire (`auth.session-revocation`), its API tokens keep working, and administrators have no SMB password to change (ADR-0117).
- Tests: `supervisor/tests/test_emergency.py` (the command in its process, `deploy/reset-admin.sh` with a fake docker, the sign-in and the audit log through the API afterwards, the command's entries waiting for a rotation of the supervisor); `webui/tests/emergency-reset.spec.ts` runs it against the local stand-in's data through `scripts/standin/emergency.py`.

## Consequences

- Whoever is root on the host, or in its docker group, can take over every administrator account. That was already so: the Docker socket is root-equivalent, and root reads and writes the database and the JWT secret.
- The audit log records that a reset happened, not who on the host ran it; sudo's entries in the host's journal do.
- An attacker's session from before the reset stays valid until it expires, 24 hours by default, and what an attacker did with the account stays. For when someone else may know the old password, `docs/RECOVERY.md` says how to end every session with a new JWT secret and to remove what may have been made with the account: its API tokens, the accounts it created, changed or made administrators (the Users page, and the user entries of the audit log), and what the audit log does not record yet. After a reset, the administrator turns two-factor authentication on again and generates new recovery codes.
- A regular user who lost the authenticator app and the recovery codes still has no way back in.

## Considered options

- A reset through the API or a link by e-mail: reachable from the LAN, and UNOS has no verified e-mail address for its users.
- The password as an argument or an environment variable: visible in `/proc` to other processes and kept in the shell's history; `docker exec -e` also shows it in `docker inspect`.
- Any user, regular ones included: wider than the entry asks for, and the decision to let root reset the second factor of a regular user's account is the owner's to make.
- Ending every session with the reset, by replacing the JWT secret: it signs every user out and needs a restart of the supervisor, which the command cannot do from inside its container; `docs/RECOVERY.md` describes the steps instead.
