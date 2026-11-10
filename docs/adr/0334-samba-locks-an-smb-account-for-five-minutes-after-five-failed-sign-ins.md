---
status: accepted
date: 2026-11-10
area: shares
decided-by: agent
supersedes: [ADR-0280, ADR-0310]
---

# ADR-0334: Samba locks an SMB account for 5 minutes after 5 failed sign-ins, and passdb.tdb stays on the system disk

## Context

ADR-0280 left how to limit repeated failed SMB sign-ins (step 1 of `shares.smb-login-protection`)
to the owner, who delegated the choice ("do everything yourself", in translation, 2026-11-10;
ADR-0013). ADR-0310 kept passdb.tdb on the system disk because a failed sign-in writes nothing
there while no lockout counts failures. Of the options of ADR-0280, Samba's own lockout is a few
account policies that the startup reconcile can keep as it keeps the password history (ADR-0278);
a limit per client for TCP port 445 needs a host firewall that UNOS would manage, and blocking
clients found in the journal needs that firewall and a daemon that reads the journal.

## Decision

- The startup reconcile keeps Samba's account policies `lockout duration` 5 (minutes),
  `reset count minutes` 5 and `bad lockout attempt` 5 beside `password history` 3, as it keeps the
  history: `pdbedit -P "<policy>"` at every start and `pdbedit -P "<policy>" -C <value>` only for a
  value that differs, so that account_policy.tdb is written only on a change. A policy that cannot
  be read or set is a problem of the reconcile, and the others go on. The threshold is set after
  the two times, so that the lockout starts with them rather than Samba's 30 minutes. The values
  are `LOCKOUT_MINUTES`, `RESET_COUNT_MINUTES` and `BAD_LOCKOUT_ATTEMPTS` in
  `supervisor/src/shares/accounts.py` (`ACCOUNT_POLICIES`, `ensure_account_policy`); tests:
  `supervisor/tests/test_smb_reconcile.py` (TestLockout, TestPasswordHistory) against
  `supervisor/tests/samba_fake.py`.
- What Samba then does (source3/auth/check_samsec.c, source3/passdb/passdb.c and pdb_interface.c):
  a wrong password counts, but for a retry of one of the account's two previous passwords
  (ADR-0278); the failure that brings the count to 5 locks the account; while it is locked, every
  sign-in, from any client and with the right password too, is refused with
  NT_STATUS_ACCOUNT_LOCKED_OUT before the password is checked; 5 minutes after the failure that
  locked it, the next sign-in unlocks it. A count below 5 starts again after 5 minutes without a
  failure, and a successful sign-in sets it back to 0.
- passdb.tdb stays where Samba keeps it (`passdb backend = tdbsam`), as ADR-0310 decided: the
  lockout bounds its writes (below), and the password history of 3 stays, so that the retries of
  a replaced password do not count and write nothing, unless they set an expired count back to 0.
- Step 2 of `shares.samba-runtime-writes`, which ADR-0310 let hold as written, changes with its
  description: a failed sign-in of an existing account writes nothing to passdb.tdb while the
  account is locked or when it retries one of the account's two previous passwords, and every
  other one writes the account's count of failures, of which the fifth locks it.
- The device check of both entries is the smb scenario check (`scripts/dev/scenario.py`,
  ADR-0304): its step 3 reads the four policies, its step with a wrong password expects passdb.tdb
  to change, and its last step that signs in shows 5 wrong passwords in a row locking the account:
  the right password is then refused with NT_STATUS_ACCOUNT_LOCKED_OUT and passdb.tdb stays as it
  was. The check deletes the account while it is still locked and does not wait for the lock's
  end; tests: `supervisor/tests/test_dev_scenarios.py`.

## Consequences

- What changes for ADR-0310: a failed sign-in with a password the account never had now writes
  the account's passdb entry to the system disk, the count and the time of the failure. At most
  5 failed sign-ins of an account count before its lock, a locked account writes nothing more
  until the lock ends, and the sign-in that ends it writes once; so a client that keeps failing
  writes passdb.tdb a few times per account every 5 minutes. Sign-ins that do not fail write
  nothing, but for the first after a counted failure, which sets the count back to 0.
- A client on the LAN that knows an account's name can keep it locked by failing on purpose and
  keep its user away from SMB; the web interface and the other accounts stay as they are. A
  computer that keeps trying a password older than the two previous ones locks its user's account
  in the same way; System > Logs names it (ADR-0277).
- Guessing slows down to 5 passwords per account every 5 minutes, from every client together; it
  does not stop, so SMB-bound passwords keep their rules (ADR-0279) and administrators still never
  have SMB (ADR-0117).
- UNOS shows no lock: neither the Users page nor the Account page says that an SMB account is
  locked, and the web interface cannot end a lock before its 5 minutes; a password change does
  not end it either.
- The statements of ADR-0280 that SMB sign-ins are unthrottled, and of ADR-0310 that a failed
  sign-in writes nothing to passdb.tdb, no longer hold. `docs/USER_GUIDE.md` says that 5 wrong
  passwords lock the SMB account for 5 minutes.

## Considered options

- A limit per client address for TCP port 445 in the host's firewall (ADR-0280): UNOS does not
  manage the firewall, and the limit counts connections, while a client can try several passwords
  on one connection.
- Blocking for a while a client that keeps failing, found in the auth_audit lines of the journal
  (ADR-0280): a daemon and firewall rules of its own, and a client that changes its address goes
  on guessing.
