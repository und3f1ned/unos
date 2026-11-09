---
status: accepted
date: 2026-11-09
area: shares
decided-by: agent
supersedes: [ADR-0159]
---

# ADR-0310: A failed SMB sign-in writes nothing to passdb.tdb, so passdb stays on the system disk

## Context

ADR-0159 kept Samba's passdb.tdb on the system disk until the writes of a wrong password were
measured on the device, and stated, as ADR-0278 did after it, that every failed sign-in of an
account with a passdb entry writes passdb.tdb once, but for the two previous passwords that a
history of 3 holds. Step 2 of `shares.samba-runtime-writes` asks that a failed sign-in of an
existing account write nothing to passdb.tdb. The smb scenario check measured 0.000 MiB for a
sign-in with a password the account never had (2026-11-09), which contradicted those
statements. Working on through the features under the owner's delegation ("work on through the
features, ask no questions; I allow everything", in translation, 2026-11-09), the agent
measured it on its own.

On the dev device, on 2026-11-09, with Samba's account policies "bad lockout attempt" 0 and
"password history" 3: for a temporary account with SMB on, three sign-ins with passwords it
never had, one with the password a change had just replaced and one more that it never had each
failed with NT_STATUS_LOGON_FAILURE and left passdb.tdb's time of change as it was, while the SD
card took 0.000 to 0.016 MiB per sign-in, the idle level; the password change itself changed
passdb.tdb. With no lockout policy, Samba does not count bad passwords (its
`pdb_increment_bad_password_count` returns before it changes the entry), so nothing is stored.

## Decision

- passdb.tdb stays where Samba keeps it (`passdb backend = tdbsam`): a failed sign-in writes
  nothing there while the lockout policy is 0, which UNOS leaves as Samba sets it (ADR-0280).
  Moving it to RAM is not needed.
- Step 2 of `shares.samba-runtime-writes` holds as written; its device check is the smb scenario
  check's step with the replaced password and its step with a password the account never had,
  and the measurement above.
- The password history of 3 (ADR-0278) stays: it spares the writes and the count of retries
  with a replaced password once a lockout counts failures (`shares.smb-login-protection`).

## Consequences

- ADR-0159's and ADR-0278's statement that every other wrong password writes passdb.tdb once
  does not hold while the lockout policy is 0; with a lockout, each counted failure writes the
  account's entry.
- Unthrottled SMB sign-ins stay an accepted risk (ADR-0280), now without SD writes as their cost.
