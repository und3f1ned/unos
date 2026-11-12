---
status: accepted
date: 2026-11-12
area: shares
decided-by: agent
supersedes: [ADR-0116]
---

# ADR-0363: An administrator renames a user, and the user's SMB account moves to the new name with its uid

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
  account of the old name is removed. Then one commit renames the user, the account row (password
  state "needed") and the user's access rows (`service.rename_access_of_user`; rows of the id
  written for another name are deleted, ADR-0137), bumps their shares' versions and ends every
  session of the user, the administrator's own too, since tokens carry the name (ADR-0329). A
  failed host step after `usermod`, or a failed commit, renames the account back; when Samba's
  account of the old name was already gone, the row is set to "needed" (503). After the commit an
  active account is turned on again under the new name, which renders smb.conf; when that fails,
  the user stays renamed with SMB "password needed", smb.conf is rendered without the old name,
  and the answer warns "User renamed, but SMB access could not be turned on again: <reason>".
- `smb_uids` keeps the name a uid first went to (ADR-0353): a rename does not rewrite it.
- Audit: `user_updated` "Updated user <new>: renamed from <old>[, password changed ...]"; for a user
  with an account row also `smb_account_updated` "SMB account renamed from <old> to <new>", and
  "SMB password set by an administrator" when SMB was turned on again (ADR-0320).
- The Users page's edit dialog has the name field; a rename asks for the password when SMB is on,
  and a rename of oneself signs out once saved.
- Tests: `TestRename` in `supervisor/tests/test_smb_accounts.py`;
  `test_a_renamed_user_keeps_its_share_access_under_the_new_name_and_a_start_changes_nothing` in
  `supervisor/tests/test_smb_integration.py`; the rename tests of `webui/tests/users.spec.ts`; the
  rename steps of `scripts/dev/scenario.sh smb` and their tests in
  `supervisor/tests/test_dev_scenarios.py`.

## Consequences

- A rename of a user with SMB on always changes their password; the user's devices must sign in
  with the new name and password, and every UNOS session of the user ends.
- `smb_uids` names a renamed account's uid by the old name: a backup made after the rename and
  restored after the user's deletion gives the restored account a fresh uid (ADR-0353), and its
  files keep the old one. A backup from before the rename, restored while the renamed user lives,
  gives the restored row a fresh uid too (ADR-0112).
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
