---
status: accepted
date: 2026-11-17
area: files
decided-by: agent
---

# ADR-0395: Copies, and moves from another pool or volume, into a share folder get the modes of Repair permissions

## Context

Copy in Files gave each copy the mode of its source, and so did a Move to another pool or volume,
which copies and then deletes: in a share's folder such items kept 644 or 755, and the share's SMB
users could not change them until a Repair permissions (ADR-0363), while New Folder and Upload
already give 2770 and 0660 there (ADR-0273, whose rule for them ADR-0274 restates and keeps in
force). ADR-0273 left copies and moves to the ledger entry
`files.group-writable-copies-and-moves-in-shares`, which asks for copies and moves from another
pool or volume that the share's users can change at once, and for the modes as before outside
share folders. The owner delegated the choices, asking above all that they be safe.

## Decision

- Where: the destination of the request is a share folder as ADR-0273 decides it (the rule that
  ADR-0274 keeps), on its descriptor (`_share_folder` in `supervisor/src/files/router.py`:
  setgid, the group unos-smb with read, write and search, nothing for others), and so is every
  folder that the copy gives the modes of shares. The shares' rows are not read. A share's own folder and the repaired folders
  below it are share folders; a folder below a share that was never repaired is not, and neither
  is a private folder.
- What: there, every copy gets the mode that Repair permissions gives an entry of a share, from
  one function that Repair now uses too (`share_entry_mode` of
  `supervisor/src/shares/constants.py`): folders 2770, regular files 0660, or 0770 when their
  owner may run them (`SHARE_EXECUTABLE_FILE_MODE`). A private source (no group or other bits,
  such as a 0600 key or a 0700 folder) keeps its mode, as Repair leaves it, and what is in a
  private folder is copied as outside shares. Symbolic links stay links, and nothing is followed.
  The group comes from the setgid folder, as for New Folder: the copy sets no group and needs no
  check of the host's group database (ADR-0151).
- How: the copy walk is the one Copy had (`_copy_entry`): every entry is opened relative to its
  folder's descriptor without following a link and created exclusively (ADR-0042, ADR-0168), and
  gets its mode through its own descriptor only once it is complete: a file after its data, a
  folder after everything in it. Until then it is private to the supervisor (0600, 0700), so the
  share's users reach nothing of an incomplete copy.
- Races: whoever can write the share folder can rename entries there while the copy runs. A folder
  that the copy has just made (mkdir) in a share folder is used only while the folder opened at its
  name is the supervisor's, still 0700 and empty, as New Folder checks its own (`_open_new_folder`,
  like `_give_share_mode` of ADR-0273): another one put there in between is refused with 409
  ("Another folder took the place of '<name>' while it was copied, so the copy stopped there."),
  and so is a file, a symbolic link or nothing at that name, before anything is copied into it or
  its mode is changed; the copy's own empty private folder stays where it was put, which an
  administrator deletes in Files. The mode of a copy comes from the source that was opened (fstat),
  not from its lstat before: a private file or folder renamed over the source in between keeps its
  private mode in the copy. Outside share folders the new folder is not checked, as before: there
  it may be on a filesystem that keeps modes and owners of its own (vfat, exfat), whose folders
  would fail the check.
- Errors half way: the error is answered as before (507 for a full pool, 403 for a mode the
  filesystem refuses). What was copied stays: the complete entries with their modes, and the
  folder or file that the error interrupted private, as outside shares, which an administrator
  deletes in Files; Repair skips it. A move to another pool or volume deletes its source only
  after a complete copy, so the source stays whole.
- This is not the automatic repair that ADR-0363 rules out: no entry that existed changes, only
  the new entries that the administrator's Copy or Move makes, as with New Folder and Upload.
- A move within a pool or volume stays a rename (`rename(2)`) and keeps the owners and modes of
  the items, also into a share folder: they are the same files, whose owners and access may matter
  to others (apps, other accounts), and changing a whole tree in place is what Repair permissions
  does on an administrator's request (ADR-0363). Repair gives them the modes of the share.
- Elsewhere Copy and Move keep the modes of their source, as before: a copied file never keeps
  set-user-ID or set-group-ID (a copied folder keeps its mode whole), a rename keeps everything.
- ACL entries are copied neither from the source nor changed: a copy gets the entries of the
  destination folder's default ACL, as every new entry there does, and the group bits of its mode
  become its ACL's mask, as Repair permissions leaves them (ADR-0363).
- Roles are unchanged: Files is administrator-only (ADR-0030). Copy and Move write no audit entry,
  as before.
- Tests: `supervisor/tests/test_files_security.py` (a whole tree, the share's own folder and a
  folder below it, private items, a source in a share, outside share folders, a move within a pool,
  a copy that stops part way, a mode the filesystem refuses, a folder put at the name of a copy's
  new folder, a private entry renamed over the source, and the default ACL on Linux),
  `supervisor/tests/test_share_api.py` (`TestFileManagerInAShareFolder`: the folder of a new
  share), `supervisor/tests/test_smb_models.py` (`TestConstants`: `share_entry_mode`),
  `webui/tests/files.spec.ts` (outside share folders, Copy and Move keep the permissions); on the
  dev device, `scripts/dev/scenario.sh smb` (a copy and a move from a new volume into the check's
  share folder, their modes and the user's writes over SMB) and `files` (copies and moves outside
  share folders keep 644 and 755 root:root).

## Consequences

- The share's SMB users change and fill what an administrator copies into the share, or moves
  there from another pool or volume, at once, without a Repair.
- Copied items in a share lose the access that their group and other bits gave other accounts
  (apps that run as their own user, for example), as Repair takes it from the share's entries.
- A move within the same pool or volume into a share still needs Repair permissions;
  `docs/USER_GUIDE.md` says so.
- An incomplete copy stays private in the share until an administrator deletes it.
- The stand-in cannot give a folder the group unos-smb: the backend tests give the SMB group the
  runner's gid, and the dev device checks the modes and the SMB user's writes (ADR-0018).

## Considered options

- Giving each copy its mode as soon as it exists, as an upload does: the share's users could read
  a file half written or put entries into a folder that the copy is still filling.
- Removing the partial copy after an error: a folder that was complete already had the modes of
  shares, and the share's users may have put entries into it meanwhile.
- The modes of shares for a move within a pool or volume too: a walk over a tree that may be
  large, within the request, which changes entries that existed (see above).
