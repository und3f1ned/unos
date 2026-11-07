---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
---

# ADR-0105: SMB accounts are real Linux host accounts plus a tdbsam entry

## Context

Samba needs a Unix identity for every user it serves: files created over SMB are owned by the account's uid. UNOS users are rows in SQLite (ADR-0059), and an SMB user must never get a shell, a home folder or a password for anything but SMB.

## Decision

- Every active SMB account is a host account plus an entry in Samba's tdbsam passdb (`passdb backend = tdbsam`, ADR-0087).
- `create_host_account` in `supervisor/src/shares/accounts.py` creates the host account with `useradd -r -l -N -M -u <uid> -g 70000 -d /nonexistent -s /usr/sbin/nologin -c "UNOS SMB user" <name>`: no home folder, a shell that refuses logins, an explicit uid from the UNOS range (ADR-0107), the primary group unos-smb, the marker GECOS `UNOS SMB user` (ADR-0108), and a locked Unix password.
- The tdbsam entry is set with `smbpasswd -s -a <name>`, the password on standard input only (ADR-0125).
- Host accounts and passdb entries are created, changed and removed only through `host.run_on_host` (ADR-0020): argv lists, no shell, no secret in argv. A host account is created at the first activation (ADR-0129).
- Tests: `supervisor/tests/test_smb_accounts.py`, `supervisor/tests/test_smb_reconcile.py`, against the fake host of `supervisor/tests/samba_fake.py`.

## Consequences

- An SMB account changes `/etc/passwd`, `/etc/shadow` and `/etc/group` on the host as well as `passdb.tdb`; `/etc` is synced after each change (ADR-0078).
- The files that a user creates over SMB belong to the user's uid, so a uid must never pass to another user (ADR-0109, ADR-0110).
- An account that is turned off keeps its host account (locked, without a passdb entry), so its uid stays with the user.
