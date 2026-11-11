---
status: accepted
date: 2026-11-11
area: shares
decided-by: agent
---

# ADR-0349: A restore refuses a backup whose database gives one SMB uid to two accounts

## Context

A uid owns its files on the pools, so it belongs to one account (ADR-0109, ADR-0112). UNOS's own
`smb_accounts` table keeps `uid` unique, but a restore takes the table of the backup as it is,
and a crafted or edited backup can give one uid to two rows. Such a backup restored, and the
second of the two accounts could not turn SMB on while the first one existed: its host account
was refused with 409 "The user id <uid> is already used by another host account" (ADR-0276,
step 1 of `shares.restore-uid-guards`). ADR-0112 clears a restored row's uid only when the live
database gives that uid to another account, so two rows of the backup met no rule. Working on
through the features under the owner's delegation ("work on through the features, ask no
questions; I allow everything", in translation), the agent chose to refuse such a backup.

## Decision

- `check_smb_tables` in `supervisor/src/shares/service.py` refuses an `smb_accounts` table that
  gives one non-NULL uid to two or more rows, after its check of the four SMB tables' layout
  (ADR-0319). `_prepare_smb` of `supervisor/src/backup/router.py` runs it first on the
  extracted database, after `_verify_database` and before anything changes.
- The restore answers 400 "unos.db in the backup gives the uid <uid> to <n> SMB accounts", for
  the lowest such uid (a value that is not an integer is shown as at most 64 characters of its
  repr). Nothing changes: the live database's accounts are not read
  (`accounts.snapshot_for_restore`), the share and account rules do not run, no copy of the live
  database is kept, and the live database, smb.conf and the host stay as they were.
- Rows without a uid are not counted: any number of them restore, and each gets a fresh uid at
  its next activation (ADR-0109).
- Tests: `supervisor/tests/test_backup_restore.py` (TestRestoredSchema
  `test_two_accounts_with_one_uid_are_refused`: uid 70001 in two rows is refused and leaves the
  live database, smb.conf and the copies untouched, with neither the snapshot nor the account
  rules run; uids 70001 and 70002, or no uids, restore).

## Consequences

- A backup that UNOS made is never refused for this: its table cannot hold such rows. The
  administrator of a crafted or hand-edited backup gets the reason, and fixes the backup or
  restores another one.
- Step 2 of `shares.restore-uid-guards` stays open: a foreign archive can still give a restored
  account the uid of a user deleted before the backup, because such uids are not in the
  restore's snapshot (ADR-0112); an installation id in `smb_state` would close that (ADR-0342
  restates the item).

## Considered options

- Clear the uid of all rows but one, as ADR-0112 does for a restored row whose uid a live
  account holds: there the live owner has the better claim, here neither row has one. The
  database is crafted or edited either way, and any choice could give one restored user the
  files of another.
