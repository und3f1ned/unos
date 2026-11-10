---
status: superseded
date: 2026-11-09
area: shares
decided-by: agent
supersedes: [ADR-0159]
superseded-by: ADR-0334
---

# ADR-0310: A failed SMB sign-in writes nothing to passdb.tdb while no lockout counts failures, so passdb stays on the system disk

## Context

Superseded by ADR-0334: Samba's lockout now counts failed sign-ins, so a failed sign-in writes the
account's passdb entry until the account is locked; passdb.tdb stays on the system disk.

ADR-0159 kept Samba's passdb.tdb on the system disk until the writes of a wrong password were
measured on the device, and stated, as ADR-0278 did after it, that every failed sign-in of an
account with a passdb entry writes passdb.tdb once, but for the two previous passwords that a
history of 3 holds. Step 2 of `shares.samba-runtime-writes` asks that a failed sign-in of an
existing account write nothing to passdb.tdb. The smb scenario check's run of 2026-11-09 showed
0.000 MiB for its step with a password the account never had, against those statements.
Working on through the features under the owner's delegation ("work on through the features,
ask no questions; I allow everything", in translation, 2026-11-09), the agent measured it.

On the dev device, on 2026-11-09 at 14:24 UTC, with Samba's account policies "bad lockout
attempt" 0 and "password history" 3, for a temporary account with SMB on: three sign-ins with
passwords it never had, one with the password a change had just replaced and one more that it
never had each failed with NT_STATUS_LOGON_FAILURE and left passdb.tdb's modification time as it
was, while the SD card took 0.000 to 0.016 MiB per sign-in (`scripts/dev/sd.sh sectors`, which
syncs first); the password change itself changed passdb.tdb. The measurement is what this ADR
rests on. Samba's source suggests why: after a wrong password it updates the account's bad
password fields and stores the entry again, but with no lockout policy those fields keep their
values, and tdb does not store a record whose bytes did not change.

## Decision

- passdb.tdb stays where Samba keeps it (`passdb backend = tdbsam`): while no lockout counts
  failures, which is how UNOS leaves Samba (ADR-0280), a failed sign-in writes nothing there, and
  moving it to RAM is not needed.
- Step 2 of `shares.samba-runtime-writes` can hold as written. Its device check is the smb
  scenario check's step with a password the account never had, once that step compares
  passdb.tdb's modification time and reads the lockout policy, before and after a reboot.
- The password history of 3 (ADR-0278) stays: once a lockout counts failures, it spares the
  writes and the count of a client's retries with a replaced password.

## Consequences

- These statements do not hold while no lockout counts failures: that every other wrong password
  writes passdb.tdb once (ADR-0159, ADR-0278), that a failed sign-in's journal entry comes beside
  a passdb write (ADR-0277), that failed sign-ins still write to the system disk (ADR-0280), and
  that a lockout would spare writes (ADR-0280): a lockout makes each counted failure write the
  account's entry, until the account is locked.
- A failed sign-in still adds its journal entry (ADR-0277), a small write where the journal is on
  the system disk; unthrottled SMB sign-ins stay an accepted risk (ADR-0280).
- `docs/USER_GUIDE.md` no longer says that wrong passwords are written to the system disk.
