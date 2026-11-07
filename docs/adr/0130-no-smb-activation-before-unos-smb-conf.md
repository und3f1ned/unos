---
status: superseded
date: 2026-11-06
area: shares
decided-by: agent
superseded-by: ADR-0249
---

# ADR-0130: SMB cannot be turned on while Samba serves an smb.conf that UNOS did not write

## Context

Superseded by ADR-0249: the same activation rules, and the status names an smb.conf replaced after UNOS wrote it.

Until UNOS's first apply succeeds (testparm refuses a line, the pool list cannot be read or
the import fails), or after the file was replaced by hand, Samba serves an smb.conf that
UNOS did not write. Such a file may give every Samba account access to its shares, for
example a share without `valid users`, so a new Samba password must not reach it.

## Decision

Every activation (the user's own, an administrator's with a password, user creation with
`smb_enabled`) checks that the live smb.conf starts with `smbconf.HEADER`
(`accounts._require_unos_smb_conf`), right after the tool check, so that a host without
Samba still answers 503 first, and before anything is written. Otherwise it answers 409:

- An administrator gets "SMB cannot be turned on until UNOS has installed its own smb.conf:
  REASON", the reason in this order: the file could not be read; "smb.conf was not updated:
  MESSAGE" with the message of the last apply; the last import problem; "it was replaced or
  removed after UNOS wrote it; it is written again at the next change or restart" (the last
  apply succeeded); "UNOS has not written it yet; it is written at the next change or
  restart".
- A regular user gets the fixed text "SMB cannot be turned on until UNOS has installed its
  own smb.conf; ask an administrator" (ADR-0131).
- User creation with `smb_enabled` then creates the user without SMB, with the warning "User
  created, but SMB access could not be turned on: DETAIL".

Allowing, revocations and password changes of accounts that are already active are never
blocked, and `GET /shares/smb-status` gets no line of its own for this case.

Implemented in `supervisor/src/shares/accounts.py`; tested in
`supervisor/tests/test_smb_accounts.py` (TestActivationNeedsUnosSmbConf),
`supervisor/tests/test_smb_integration.py` (SMB is turned on only once UNOS wrote smb.conf;
smb.conf replaced after UNOS wrote it), `webui/tests/users.spec.ts` and
`webui/tests/account-smb.spec.ts`.

## Consequences

Activations wait until UNOS has installed its own file, and an administrator learns why from
the answer. When the file was replaced after a successful apply, the status names no reason;
ADR-0195 keeps that open.

## Considered options

- A status problem whenever the live smb.conf lacks the header: not taken; reason texts in
  the activation's answer were the smaller change.
