---
status: accepted
date: 2026-11-08
area: files
decided-by: agent
supersedes: [ADR-0160]
---

# ADR-0273: New folders and uploads of the file manager in a share folder get 2770 and 0660

## Context

The file manager runs as root with the umask 022: a folder that New Folder made in a share's
folder was 2755 and an uploaded file 0644, both of the group unos-smb from the setgid folder, so
the share's SMB users could read them but neither change them nor add entries to them until a
Repair (ADR-0160, ADR-0155). The feature `files.group-writable-entries-in-shares` asks for
entries that they can change at once, while the file manager creates entries outside shares as
before, and group write must never reach a group other than unos-smb.

## Decision

- For the file manager, a share folder is a folder set up for the SMB group as ADR-0150 sets up
  a share's folder, read on the descriptor that the new entry is created through: setgid, the
  group unos-smb (`constants.SMB_GROUP_GID`) with read, write and search, and nothing for others
  (`constants.share_root_mode_ok`; `_share_folder` in `supervisor/src/files/router.py`). UNOS
  creates a share's folder so, Repair permissions gives these modes to every folder of a share
  that is not private, and Samba to every folder that an SMB user creates
  (`force directory mode = 2770`). The share rows are not read.
- In a share folder, New Folder (`_create_directory`) makes the folder 0700 and then gives it
  `constants.SHARE_DIR_MODE` (2770) through its descriptor, only while it is the folder that
  this mkdir made: the supervisor's, still 0700, and empty. Whoever can write the share folder
  can put another folder at that name in between, and such a folder keeps its modes. An upload
  (`_FileUpload.open`) gives its unnamed or hidden file `constants.SHARE_FILE_MODE` (0660)
  through its descriptor as soon as the file exists, before any of its data is written and
  before it has its name; an upload that replaces a file gives the new file 0660 as well. When
  the mode cannot be given, the request answers the error, and the upload leaves nothing, as
  every failed upload. Such a new folder is removed again (`_remove_new_folder`): private, the
  share's SMB users could not use it, and Repair permissions skips private folders (ADR-0156).
  It is removed through the share folder's descriptor, and only while its name still holds the
  folder that was opened (`os.path.samestat`); rmdir removes only an empty folder, so one put
  at the name in between loses nothing. A folder that cannot be removed, or not be opened at
  all, stays private, and the answer names it and says to delete it (`_left_private`). A stop
  of the supervisor between the mkdir and the chmod leaves the private folder as well, which an
  administrator deletes in Files.
- The group comes from the folder: the kernel gives a new entry in a setgid folder the folder's
  group. The file manager sets no group itself and so needs no check of the host's group
  database (ADR-0151): whoever has the folder's group can already replace every entry in it, and
  the modes give that group nothing that the folder does not.
- In every folder that is not set up so, New Folder and Upload create entries as before, with
  what the umask leaves of 0777 and 0666: folders 755 (2755 in a setgid folder) and files 644.
  Such folders are, for example, the top folder of a pool (never a share's folder, ADR-0145),
  a volume that was never repaired as a share's folder, app data, the folders of shares that
  were never repaired, and folders of other groups. A volume that is shared and was repaired is
  a share folder like any other. Copy and Move keep the modes of their source (`_copy_entry`),
  in share folders too.
- ADR-0160's rule for apps is restated unchanged: apps run with their own PUID and PGID (1000),
  outside the SMB group, and Install has no option that runs them in it (PGID 70000). They can
  no more use the new entries than the share folder itself, which gives others nothing. The
  ledger entry `apps.smb-group-option` plans such an option; the ADR that decides it supersedes
  this one and restates its rules for the file manager.
- Tests: `supervisor/tests/test_files_security.py` (new entries in share folders and outside
  them, a folder put at the name of a new one, a mode that cannot be given, and a new folder
  that is removed again or stays private), `supervisor/tests/test_upload_streaming.py` (the
  file's mode from its creation, unnamed and hidden, and a mode that cannot be given),
  `supervisor/tests/test_share_api.py` (TestFileManagerInAShareFolder: the folder of a new
  share), `webui/tests/files.spec.ts` (outside share folders the group gets no write access).

## Consequences

- SMB users with write access to a share can change the files and fill the folders that an
  administrator creates there in Files at once, without a Repair.
- A folder that keeps these modes after its share was deleted, or after it was moved out of a
  share, still gives new entries 2770 and 0660: only the SMB group can open it, and that group
  can already write it.
- Copies and moves into a share, and new entries in a share whose folder was never repaired,
  still need a Repair. The ledger entry `files.group-writable-copies-and-moves-in-shares` holds
  copies and moves.
- In a folder with a default ACL, the group bits of 2770 and 0660 become the new entry's ACL
  mask, as they do for what SMB users create.
- The stand-in cannot give a folder the group unos-smb (only root can): the backend tests give
  the SMB group the runner's own gid, and the dev device checks the modes and the writes of an
  SMB user over SMB (ADR-0018).

## Considered options

- The share rows (`shares.service`) to recognise a share folder: a database read for every new
  entry, and the folder's own modes would still have to be checked, since the folder of a share
  that was never repaired, or one of another group, must not give group write. The folder's
  modes decide alone.
- Giving the new entry the group unos-smb itself, as Samba's `force group` does: it would give
  gid 70000 where the folder does not have it, and every new entry would need the host's group
  check of ADR-0151.
- Group write on top of the umask (2775, 0664) in every setgid folder of the SMB group: others
  would read what a share's folder keeps from them.
