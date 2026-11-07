---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
supersedes: [ADR-0111]
---

# ADR-0112: A uid belongs to one account: restored rows and stale accounts lose conflicting uids

## Context

SMB uids are the owners of files on the pools, so whoever holds a uid owns those files
(ADR-0109). Two paths could hand a live account's uid to another account. A restored
database from another installation or a crafted archive allocates from the same range,
70000-79999, and the carry-over rule of ADR-0111 let such a restored row keep its uid. And a
row of another name, left by a hand edit or a restore, could hold the uid of a stale marked
host account that the reconcile then removes; the row would later get a host account with
that uid. A first version of the reconcile rule cleared the uid only in the step that
removes the host account, so a failed commit or a stop right after `userdel` lost the
clearing, and no later run could see the conflict.

## Decision

A uid belongs to one account:

- Restore (`accounts.prepare_restored_accounts`, both for an authoritative database and in
  the carry-over): a restored row whose valid uid the live snapshot gives to an account with
  another username or linux name gets uid NULL, the password state "needed" and
  `password_set_at` NULL, and its user, if it exists, gets the message "SMB must be turned
  on again for X". The live owner keeps its uid; in the carry-over it is carried over with
  it and stays active under the proof of ADR-0141.
- Reconcile (`accounts.reconcile_accounts`): for every stale marked host account H (marked,
  and no row has its name and uid), each row of another linux name that holds H's uid loses
  it in the same way, with the change "The SMB account X no longer has uid U: it belonged to
  the host account H; X must turn SMB on again". It is a database rule: decided on the host
  state read before any write, and committed with the other database rules before the host
  actions remove H.
- A row without a uid gets a fresh one at its next activation (ADR-0109).

Implemented in `supervisor/src/shares/accounts.py`; tested in
`supervisor/tests/test_restore_accounts.py` (TestLiveUids, TestCarryOver, TestWholePath) and
`supervisor/tests/test_smb_reconcile.py` (rows that hold the uid of a stale account, also
when the run stops right after the `userdel`).

## Consequences

A user whose restored or hand-edited row conflicted with another account must turn SMB on
again and gets a new uid; files on the pools keep their owner. The live snapshot knows only
the accounts of the live database, so the rule cannot protect the uid of a user deleted
before the restore; ADR-0195 keeps that open.

## Considered options

- The restored row keeps the uid and the live account is carried over without it (ADR-0111):
  superseded, because it gave the restored user another person's files.
