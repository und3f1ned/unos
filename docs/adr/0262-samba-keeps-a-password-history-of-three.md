---
status: accepted
date: 2026-11-08
area: shares
decided-by: agent
supersedes: [ADR-0194]
---

# ADR-0262: Samba keeps a password history of 3, so that retries with an account's previous passwords write nothing to passdb

## Context

ADR-0194 proposed two ways to cut Samba's remaining writes to the system disk, to be chosen after measuring: its log files in `/run/samba`, which ADR-0261 decides, and, for failed sign-ins, either a short password history in passdb or a rate limit for TCP port 445. Samba writes an account's passdb entry, one synced tdb transaction, at each wrong password (ADR-0159), unless the password is one of the two before the account's current one in its password history (`need_to_increment_bad_pw_count` in Samba's `source3/auth/check_samsec.c`), and Samba keeps no history by default. The routine case is a client that keeps a saved password after it was changed and tries it again at every connection.

## Decision

- The account reconcile (`_reconcile_host` and `ensure_password_history` in `supervisor/src/shares/accounts.py`, at every start and after a restore, when the host has the Samba tools) reads `pdbedit -P "password history"` and runs `pdbedit -P "password history" -C 3` only when the value is not 3 (`PASSWORD_HISTORY`). The change is reported as "Set Samba's password history to 3 (it was N)" and audited with the reconcile's other changes. A failed read or change, or an answer without the value, is a problem of the reconcile, which the SMB status shows, and the reconcile goes on.
- Samba then stores, at each password change of an account, the hashes of its current and its two previous passwords, and refuses a sign-in with either previous password, as before, without writing the entry.
- The rate limit for TCP port 445 is not taken: it is a change of the host's firewall, which UNOS does not manage. Limiting repeated failures is the open step of `shares.smb-login-protection` (ADR-0264).
- Tests: `supervisor/tests/test_smb_reconcile.py` (TestPasswordHistory, TestConsistent) against `supervisor/tests/samba_fake.py`, which emulates `pdbedit -P`.

## Consequences

- The saving has not been measured yet: on the device, `scripts/dev/sd.sh measure` around a sign-in with the previous password must show that passdb.tdb is not written (`shares.samba-runtime-writes`).
- Every other wrong password of an account with a passdb entry still writes it once, as before (ADR-0159); only a lockout, one of the options of ADR-0264, would spare the writes of the attempts on an account while it is locked. The entry's step that no failed sign-in writes passdb.tdb therefore holds only for the two previous passwords.
- An account has a history only from its next password change on; until then a retry of its older password still writes.
- passdb.tdb, readable only by root, holds the NT hashes of two more passwords per account. Samba's own password change (SAMR, ADR-0250) refuses a password of the history; UNOS sets passwords as root with `smbpasswd`, which Samba does not check against it.
- The policy holds for every passdb entry of the host, also those UNOS did not create; no entry is changed by setting it (ADR-0108). A value set by hand is set back to 3 at the next start. Setting it writes Samba's account_policy.tdb once; reading it at each start writes nothing.
