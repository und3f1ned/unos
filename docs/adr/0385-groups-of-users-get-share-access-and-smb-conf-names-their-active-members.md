---
status: accepted
date: 2026-11-17
area: shares
decided-by: agent
supersedes: [ADR-0076]
---

# ADR-0385: Groups of users get share access in UNOS's database, and smb.conf names their active members

## Context

ADR-0076 left groups as share principals for the `feature-list.json` entry `shares.group-access`:
an administrator makes a group, adds users, and gives it read or read-write access to a share, which
its members and later members get. Samba can name a host group in a share's lists (`@name`), but
UNOS's SMB accounts are host accounts of its own (ADR-0105) and every share folder already belongs
to the one group unos-smb (ADR-0150), so a host group would only add host state. The owner delegated
the choices, asking above all that they be safe (ADR-0013); the agent made them.

## Decision

- **A group is a row of UNOS's database, never a host group.** The tables of
  `supervisor/src/shares/models.py`, made by `create_all` (ADR-0319): `user_groups` (an id that
  AUTOINCREMENT does not hand out again in a database that UNOS made, a name unique ignoring case,
  and a version),
  `user_group_members` (group, user id and the username it was written for, one row per group and
  user) and `smb_share_group_access` (share, group, `read` or `write`, one row per share and group).
  No `groupadd`, no gid, no `@group` or `+group` in smb.conf.
- **smb.conf names the members.** `_read_render_input` in `supervisor/src/shares/service.py`
  writes into a share's `valid users` and `write list` every member of a group with access whose
  SMB account is active and whose member row carries the user's current name. A user gets the
  higher of their own level and the levels of all their groups: there is no deny, so adding a grant
  never lowers access and removing one never raises it. Two rows of one group on one share (a
  restored database) give the lower level, as for users (ADR-0144). So a member added later gets the
  access when the group is saved, and a member removed loses it: smb.conf is staged, checked by
  testparm and installed (ADR-0089), and the shares whose access shrank close their open
  connections (ADR-0103); a rendering that does not change writes nothing (ADR-0082), and a
  group's rename alone, which cannot change it, is only committed. The account page of a user
  (`GET /auth/me/smb`) shows the level that smb.conf gives: the lower one of two rows of a grant,
  the higher one across grants.
- **The API**, administrators only (`_ADMIN_ONLY` of `supervisor/src/main.py`, ADR-0030):
  `supervisor/src/shares/groups.py` serves `GET /api/v1/groups`, `POST /groups` (`name`,
  `members`) and `GET`, `PATCH` (`name`, `expected_version`, `new_name`, `members`, which replaces
  the list; 409 "The group was changed meanwhile; reload") and `DELETE /groups/{id}?name=`. As
  users are (ADR-0133), a request names a group by its id with the name it saw, and an id whose
  group now has another name is refused (409 "Group <id> is now '<name>'; reload the page"), so a
  page loaded before a rename never reaches another group of the old name, also where a restored
  table hands an id out again. A rename raises the versions of the group's shares, as a user's
  does (ADR-0368): a share dialog loaded before it, which no longer finds the group under its old
  name and would send the access list without it, is refused (409). Members are named by id
  with their username; administrators and usernames that SMB cannot use are refused, as in a
  share's access list, and users whose SMB is off or needs a password are accepted. `POST` and
  `PATCH /shares/smb` take `group_access` (`group_id`, `group`, `level`; 400 "Group <id> does not
  exist", or the 409 above), and a share's answer lists each group with its members.
  Every change holds `smb_lock()` (ADR-0080) and is audited with `group_created`, `group_updated`
  or `group_deleted` (a warning; resource type `group`); a share's group access is part of its
  `share_created` and `share_updated` entries ("group access: +family=read").
- **Names** follow the pattern of usernames (`^[a-z][a-z0-9_-]{0,31}$`; 400 "Group names have 1 to
  32 characters: ..."), unique among groups whatever the case. A group's name reaches neither
  smb.conf nor the host, so a host group or a user of the same name is no clash and the reserved
  names of ADR-0113 do not apply; the pattern keeps names valid for Linux and Samba and plain in
  audit lines.
- **Users and groups go together.** A user's deletion, promotion to administrator (ADR-0117) and
  creation (rows left for its id, ADR-0132) delete the user's member rows with their access rows,
  and a rename (ADR-0368) moves them to the new name (`delete_access_of_user`,
  `rename_access_of_user`). A group's deletion deletes its member and access rows (the shares'
  versions grow); a new group deletes the rows a restored database left for its id. The startup
  reconcile and the post-restore step purge member rows whose group or user is gone, whose
  username is not the user's or whose user is an administrator, and group access rows whose share
  or group is gone (`purge_orphans`, ADR-0137).
- **Backups and restores.** Groups are part of the database, so every backup holds them and a
  restore brings them back with it. `check_smb_tables` checks the three tables when a backup has
  them (an older backup restores with no group) and refuses one that gives a name to two groups or
  has a group whose name breaks the rule, which no request could name: its access could be neither
  changed nor deleted; `prepare_restored_shares` deletes the group
  access of a database that is not authoritative for SMB (the groups and members stay) and, in one
  that is, the group access of missing shares and of shares that are not served, as it does for
  users (ADR-0319). After a restore of a database that is not authoritative, the import of the
  live smb.conf finds users, not groups, in its lists: every user who has access in it, also through
  a group, gets that access as their own, which stays when they leave the group
  (`docs/USER_GUIDE.md`, System Backup). A restored member counts only while the user's account is
  active, which after a restore needs proof (ADR-0141); the uid guards (ADR-0349, ADR-0353) are
  unchanged, since groups have no ids on the host.
- **Uninstall and import.** `deploy/uninstall.sh` has nothing of groups to remove (ADR-0322). The
  import still keeps no `@group` entry of an existing smb.conf (ADR-0086, ADR-0380): it names a host
  group, not a group of UNOS.
- The web UI: the Groups card of the Users page (`webui/src/lib/components/users/GroupsCard.svelte`),
  the groups of a share's access picker and card (`webui/src/lib/components/shares/`).
- Tests: `supervisor/tests/test_share_groups.py`; `webui/tests/users.spec.ts` and
  `webui/tests/shares.spec.ts`.

## Consequences

- smb.conf lists each member by name in every share their groups reach, and a change of the
  members rewrites it and reloads Samba, as a change of a share's access does.
- Membership gives the member's processes no supplementary group: files stay reachable through
  unos-smb (ADR-0150), and apps and NFS exports see nothing of groups.
- A user cannot be given less than a group gives: without a deny, that takes another group or
  share.

## Considered options

- A host group per UNOS group, named in smb.conf as `@name`: it needs a gid range, a marker as for
  accounts (ADR-0108), host commands for every membership change, its removal by uninstall.sh and
  its members again after a restore; a host group of the same name made by hand or by a package
  would give its members the share (`@name` reads the host's group database), and smbd takes a
  session's groups at sign-in, so a removed member would keep the access until signing in again.
- A user's own level overriding the group's: removing the user's entry would then raise access,
  and adding one could lower it.
