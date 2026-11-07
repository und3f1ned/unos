---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
---

# ADR-0110: A row never adopts a host account's uid, and a stale marked account is removed first

## Context

A marked host account (ADR-0108) can outlive its row: a user deleted while connected (`userdel` was busy), a rolled-back commit (ADR-0078), an uninstall that kept the accounts (ADR-0109). If a new user with the same name took over that account and its uid, the new user would own the deleted user's files. The first design did that: it adopted the uid of a marked account with the user's name.

## Decision

- A row never adopts a host account's uid. A marked account with the user's name is the row's own account only when it has the row's uid; with another uid, or any marked account of that name when the row has no uid yet, it is stale (`_activation_checks` in `supervisor/src/shares/accounts.py`).
- An activation removes a stale account first: its UNOS passdb entry (`pdbedit -x`, ADR-0108), then its sessions (ADR-0104), then the account itself (`userdel`, read again right before, ADR-0192). When `userdel` answers that the account is busy, the activation stops with 409 "The previous SMB account <name> is still in use; try again later".
- The row keeps its own uid or gets a fresh one (ADR-0109), committed before `useradd`.
- The only path that maps an existing uid to a row is the carry-over of a restore, and only with the proof of ADR-0141.
- Tests: `supervisor/tests/test_smb_accounts.py` (`TestCollisions`: a stale marked account is replaced, a busy one answers 409), `supervisor/tests/test_smb_reconcile.py`.

## Consequences

- A deleted user's files never pass to a new user of the same name: the new user's account gets another uid.
- A user whose previous account is still connected must wait until that connection ends before turning SMB on.
- The startup reconcile removes stale marked accounts the same way, without an activation (ADR-0138).

## Considered options

- Adopting a marked account's uid, as the first design did: removed, because it gave a deleted user's uid and files to a new user of the same name, and it contradicted the rule that such accounts are stale.
