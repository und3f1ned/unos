---
status: accepted
date: 2026-11-12
area: shares
decided-by: agent
supersedes: [ADR-0116]
---

# ADR-0368: An administrator renames a user, and the user's SMB account moves to the new name with its uid

## Context

The SMB and host account name is the UNOS username (ADR-0113), Samba keeps passwords by name and
needs the plaintext to make an account, and a uid owns its files on the pools (ADR-0109), so
renaming a user means moving the host account, the Samba account and the share access, which
ADR-0116 left for later (`auth.user-rename`). Working on through the features under the owner's
delegation ("work on through the features, ask no questions; I allow everything", in translation),
the agent made the choices below.

## Decision

- `PUT /auth/users/{id}` takes `new_username` with the current `username` (422 without it,
  ADR-0133). Under `smb_lock` (`_rename_user` in `supervisor/src/auth/router.py`) a new name must
  pass the name rule of new users (400 "Invalid username. <rule>"), and no other user may have it in
  any letter case (400 "Username already exists", or "Username already exists as 'X': names that
  differ only in letter case are one account for Samba"). A rename does not change the role in the
  same request (400 "Change the name and the role one at a time"). A legacy name may take its own
  lower case.
- A user whose SMB account is active needs a new `password` in the request (400 otherwise, and 400
  for one that SMB refuses): it becomes the UNOS password and, after the commit, the SMB password
  of the new name (`accounts.activate`). Without SMB on, the password is optional, as for any edit.
- The host account is renamed, not made anew: `usermod -l <new> <old>` keeps its uid, and so the
  files it owns. Only the marked account with the row's uid, read again right before
  (`_rename_host_account` in `supervisor/src/shares/accounts.py`, ADR-0108, ADR-0192). `usermod`
  joins `SAMBA_TOOLS` and `HOST_TOOLS` (package passwd).
- Order (`rename_before_store`, `store_rename`, `finish_rename`): for a row with a uid, the host is
  checked first (409, nothing changed, when it has an account, a group or a Samba account of the
  new name in any case, or when smb.conf is not UNOS's and SMB is on); the old name's SMB sessions
  end, `usermod` runs (retried 3 times while an smbd process of the uid runs, then 409), and Samba's
  account of the old name is removed. Then one commit deletes an account row of another id that has
  the new name (left by an earlier user; it goes before the user's row changes, whose flush would
  otherwise meet it first), renames the user, the account row (password state "needed") and the
  user's access rows (`service.rename_access_of_user`; rows of the id written for another name are
  deleted, ADR-0137), bumps their shares' versions and ends every session of the user, the
  administrator's own too, since tokens carry the name (ADR-0329). A
  failed host step after `usermod`, or a failed commit, renames the account back; when Samba's
  account of the old name was already gone, the row is set to "needed" (503). After the commit an
  active account is turned on again under the new name, which renders smb.conf; when that fails,
  the user stays renamed with SMB "password needed", smb.conf is rendered without the old name,
  and the answer warns "User renamed, but SMB access could not be turned on again: <reason>".
- `smb_uids` keeps the name a uid first went to (ADR-0353): a rename does not rewrite it, and
  records the uid under the old name when it lacks it. The table `smb_uid_names` (`SmbUidName` in
  `supervisor/src/shares/models.py`, primary key (uid, linux_name), made by `create_all`, ADR-0319)
  gets the old and the new name of the uid in the rename's commit (`store_rename`), for a row whose
  uid the rename keeps; no row is ever deleted. This refines ADR-0353, accepted on main: a restored
  row keeps a recorded uid under any name of the uid in either table (`prepare_restored_accounts`,
  to which `snapshot_for_restore` passes the live table), and a uid that only `smb_uid_names` knows
  is protected too. A restore makes `smb_uid_names` anew in the extracted database: the live rows,
  then the backup's own rows with a valid uid and name for the uids that this NAS did not know
  before the restore (not in either live table nor held by a live account), so a backup adds no
  name to a uid of this NAS; every uid with a name is also recorded in `smb_uids` (the
  alphabetically first name when it lacks it), so no activation hands it out again and the floor of
  `next_uid` lies above it.
- The login history (ADR-0263, accepted on main, refined here): the table `usernames_taken`
  (`UsernameTaken` in `supervisor/src/auth/models.py`, primary key the name, `taken_at` SQLite's
  CURRENT_TIMESTAMP) gets the new name's row, or its time set again, in the rename's commit
  (`_rename_user`). A regular user sees the attempts on their name from the later of
  `users.created_at` and the name's `taken_at` on, compared to the second (`_visible_attempts` in
  `supervisor/src/security/router.py`). A creation writes no row: a row that a rename left for a
  name is older than any account created with that name later. A row names no user, so a deletion
  leaves it, and a restore brings the backup's table with its users.
- Audit: `user_updated` "Updated user <new>: renamed from <old>[, password changed ...]"; for a user
  with an account row also `smb_account_updated` "SMB account renamed from <old> to <new>", and
  "SMB password set by an administrator" when SMB was turned on again (ADR-0320).
- The Users page's edit dialog has the name field; a rename asks for the password when SMB is on,
  and a rename of oneself signs out once saved.
- Tests: `TestRename` in `supervisor/tests/test_smb_accounts.py` (also
  `test_a_row_that_another_id_left_with_the_new_name_goes_in_the_renames_commit`,
  `test_the_uid_keeps_each_name_it_went_by`,
  `test_a_rename_records_a_uid_that_smb_uids_lacks_under_the_old_name`,
  `test_a_name_that_a_rename_freed_shows_its_new_holder_none_of_the_earlier_sign_ins`);
  `TestRenamedUids` and TestWholePath `test_the_uid_of_a_renamed_user_deleted_after_the_backup` in
  `supervisor/tests/test_restore_accounts.py`;
  `test_a_renamed_user_keeps_its_share_access_under_the_new_name_and_a_start_changes_nothing` in
  `supervisor/tests/test_smb_integration.py`; the rename tests of `webui/tests/users.spec.ts`; the
  rename steps of `scripts/dev/scenario.sh smb`, which try the old name with its own password, and
  their tests in `supervisor/tests/test_dev_scenarios.py`.

## Consequences

- A rename of a user with SMB on always changes their password; the user's devices must sign in
  with the new name and password, and every UNOS session of the user ends.
- A backup made before or after a rename, restored after the user's deletion, brings the user
  back with the uid, and so with their files. A backup from before the rename, restored while the
  renamed user lives, gives the restored row a fresh uid (ADR-0112): the live account of the new
  name holds it.
- A renamed user's login history starts again at the rename: their sign-ins with the old name, and
  attempts on the new name from before, are not shown to them; administrators still see them. A
  backup from before `usernames_taken` existed, restored, bounds the history by the creation only,
  as before, until the next rename.
- If renaming the host account back fails after a failed step, the account keeps the new name
  while the database keeps the old one; the next start's reconcile then removes it and takes the
  uid from the row (ADR-0112), so the user turns SMB on again with a fresh uid. The answer names
  that failure.

## Considered options

- A new host account of the new name with a fresh uid: the user's files would stay with the old
  uid, owned by nobody.
- `userdel` and `useradd` with the same uid: two steps with a gap in which the uid has no account,
  and a failure between them leaves none.
- Rewriting `smb_uids` to the new name: it supersedes ADR-0353's rule that a recorded uid keeps
  its name, and fails the other backup instead (one from before the rename).
- Merging the backup's `smb_uid_names` rows for every uid: a backup could add a name to a uid of
  this NAS, and a later restore would give that uid, and a deleted user's files, to that name.
- A column on `users` for when the name was taken: existing tables are never altered (ADR-0319).
  Rows keyed by user id: a dependent row that each deletion must delete; keyed by name, a row
  outlives a deletion without harm.
