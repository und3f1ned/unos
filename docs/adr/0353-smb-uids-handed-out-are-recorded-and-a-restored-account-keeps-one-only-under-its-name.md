---
status: accepted
date: 2026-11-12
area: shares
decided-by: agent
---

# ADR-0353: SMB uids handed out are recorded for good, and a restored account keeps a recorded uid only under the name it went to

## Context

A uid owns its files on the pools, also after its account is gone (ADR-0109). A restore protected
only the uids of live accounts (ADR-0112) and, through `next_uid`, the uids of later activations
(ADR-0109): a restored row could keep the uid of a user deleted from this NAS, before the backup was
made or after it, and with it that user's files (step 2 of `shares.restore-uid-guards`, the item that
ADR-0342 restates from ADR-0276, which named an installation id for it). Working on through the
features under the owner's delegation ("work on through the features, ask no questions; I allow
everything", in translation), the agent chose the mechanism.

## Decision

- The table `smb_uids` (`SmbUid` in `supervisor/src/shares/models.py`, made by `init_db`'s
  `create_all`, ADR-0319) holds every uid handed out on this NAS (primary key, in the range of
  ADR-0107) and the linux name it went to. No row is ever deleted or renamed: a uid that is there
  keeps its name.
- It is written, in `supervisor/src/shares/accounts.py`, by:
  - an activation that allocates a uid (`_allocate_uid`), in the transaction that commits the row's
    uid before `useradd` runs (ADR-0109). The new uid lies above every recorded uid and is none of
    them, whatever `next_uid` says;
  - the reconcile, when it has read the host (step 5' of `reconcile_accounts`): first for each
    stale marked host account whose uid the table lacks, then for each row with a valid name and
    uid that the table lacks, committed with the database rules before step 6 removes an account,
    also when step 6 finds it busy. So the first start of a new installation records the accounts
    that `deploy/uninstall.sh` kept (ADR-0109), and the first start after this change records the
    rows that existed before the table (the dev device); a later start writes nothing;
  - a user's deletion (`begin_user_removal`), for the deleted row's uid when the table lacks it, in
    the transaction that deletes the user (defence in depth for the same rows);
  - a restore (below).
- A restore: `snapshot_for_restore` reads the live table with the live accounts. In
  `prepare_restored_accounts`, inside the restore's one transaction on the extracted database
  (ADR-0139), a restored row whose valid uid no live account holds (those ADR-0112 decides) keeps it
  only when the table has no row for it or its row names the restored row's linux name. Otherwise
  the row gets uid NULL, the password state "needed" and `password_set_at` NULL, and its user, if
  it exists, the message "SMB must be turned on again for X", as in ADR-0112; a fresh uid comes at
  its next activation. Then the backup's `smb_uids`, whatever its layout or absence, is dropped and
  made anew from the model, with the live rows, the uids of the live accounts (the restore may
  delete them) and those of the restored rows with a valid name, the first name for a uid kept.
  Last come the rows of the backup's own `smb_uids` with a valid uid and name, read when its layout
  has the columns `uid` and `linux_name`, for the uids still missing. The floor of `next_uid`
  (ADR-0109) also lies above every recorded uid.
- Merging the backup's record, rather than dropping it, was chosen by the agent under the same
  delegation, as the safer of the two: a merged row only ever reserves a uid, never gives one, and
  a uid that this NAS recorded, or that a restored row or a live account holds, keeps that name.
  The old installation's backup, restored after a reinstall, so brings back the uids of the users
  it deleted, whose files the pools keep.
- The linux name is the identity, as in ADR-0112: users cannot be renamed (ADR-0116), and it is the
  owner name of the files on the host.
- Tests: `supervisor/tests/test_restore_accounts.py` (TestHandedOutUids; TestWholePath
  `test_the_uid_of_a_deleted_user`, `test_a_reinstall_records_the_kept_accounts`,
  `test_the_uid_of_a_user_activated_before_the_record`),
  `supervisor/tests/test_backup_restore.py` (TestAccountStep
  `test_the_uid_of_a_deleted_user_goes_to_no_restored_account`), `supervisor/tests/test_smb_accounts.py`
  (TestCollisions `test_an_activation_records_its_uid_before_useradd`,
  `test_a_deletion_records_the_uid_the_record_lacks`, `test_a_recorded_uid_is_never_handed_out_again`),
  `supervisor/tests/test_smb_reconcile.py` (TestNextUid
  `test_a_stale_marked_account_is_recorded_before_it_is_removed`, `test_the_rows_are_recorded_once`).

## Consequences

- A backup of another installation, or a crafted one, gives a uid handed out here to no account of
  another name. A backup of this NAS made before a user was deleted brings that user back with their
  uid and so with their files, and so does a backup of the old installation restored after a
  reinstall.
- An account of another installation's backup with the name and the uid of a user deleted here is
  taken for that user, as ADR-0112 does for live accounts.
- The table holds at most one row per uid of the range (10000). An activation that allocates writes
  one row with its commit; the first start after this change writes the rows' uids once, and a
  consistent start writes nothing (ADR-0082, ADR-0214).
- A backup of another installation, or a crafted one, can reserve uids that were never handed out
  here, up to the whole range, and so leave fewer for activations; its rows' uids could already do
  that through the floor of `next_uid` (ADR-0109).
- It closes the limits that ADR-0109 and ADR-0112 state for the uids of deleted users, and the item
  that ADR-0342 restates as still open.
- Limits: a uid handed out before the table existed (the dev device, ADR-0207) is recorded from its
  row at the first start that reads the host, or at its user's deletion; one whose user was deleted
  before is not known. An uninstall deletes the table with the database, so the next installation
  knows the uids of the host accounts the uninstall kept, not those of users deleted before it,
  until a backup of the old installation is restored. The reconcile records rows only when it has
  read the host: before, a row could take the uid of a stale host account of another name.

## Considered options

- An installation id in `smb_state`, a restore trusting the uids of a backup with the live id:
  `smb_state` gets no new column (ADR-0319), a crafted backup can copy the id, and a reinstall makes
  a new one, so the restore of the old installation's backup after a reinstall would have to take
  away every uid below `next_uid`, and its users' files.
- Keeping the higher of the restored and the live `next_uid`: ADR-0109 already does; it protects the
  uids that later activations hand out, not those that restored rows bring.
- Clearing every restored uid below the live `next_uid` that no live account of the same name holds:
  a backup made before a deletion could no longer bring the user back with their files.
- A state file outside the database: another write path on the system disk, outside the transaction
  that commits the row's uid.
- Dropping the backup's record and keeping only this NAS's: then a foreign record could never deny
  a later restore of this NAS's own backup a uid. Such a denial needs a uid that this NAS handed out
  and has not recorded: a row from before the table, which the first start records, or a user
  deleted before the table existed (the dev device, ADR-0207). The drop would instead make the old
  installation's backup, after a reinstall, forget the uids of its deleted users, whose files the
  pools keep.
