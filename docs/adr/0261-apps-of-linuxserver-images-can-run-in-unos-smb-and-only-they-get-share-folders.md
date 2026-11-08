---
status: accepted
date: 2026-11-08
area: apps
decided-by: agent
supersedes: [ADR-0160]
---

# ADR-0261: Apps of linuxserver.io images can be installed to run in the group unos-smb, and only such an app is given a share's folder

## Context

ADR-0160 left the File Manager and apps without group access to share folders. Share folders
belong to root and the group unos-smb with mode 2770 (ADR-0150), so apps that run as PUID/PGID
1000 cannot use them, and the ledger entry `apps.smb-group-option` asks for an install option
that sets PGID 70000. Nothing kept a share's folder out of an app either: a volume override
could name one, an image that runs as root inside its container reads and writes it whatever
its mode, and an administrator could type 70000 as PGID in the Environment tab.

## Decision

- `AppInstallRequest.run_in_unos_smb` (`supervisor/src/apps/schemas.py`), the checkbox "Run in
  the group unos-smb" of `webui/src/lib/components/apps/AppDetailModal.svelte`, adds
  `PGID=70000` (`SMB_GROUP_GID`) and `UMASK=002` to the app's environment
  (`UNOS_SMB_ENVIRONMENT` of `supervisor/src/apps/router.py`): the image's user gets the group
  unos-smb, and what it creates is group-writable, so SMB users can change it. Off by default;
  the form warns that the app, and whoever takes it over, gets the files of the share folders
  it is given.
- Only templates of linuxserver.io images offer it (`AppTemplate.can_run_in_unos_smb`): their
  base image takes the group of its user from PGID and the umask of its services from UMASK,
  and all of them declare PUID and PGID. Other images ignore both, also where their template
  declares PGID (jellyfin's image runs as root, Plex's takes PLEX_GID), so the option is
  refused for them with 400. Docker's `group_add` is not used: an image that switches to its
  user with that user's own group list drops it.
- PGID is not an `env_overrides` value any more: an install that sets it is refused with 400,
  so only the option puts an app into the group. The Environment tab shows PGID read-only.
- Each install compares the app's pool folders with the folders of all shares in the database,
  served or not (`_check_share_folders`), before the image is pulled:
  - without the option, a folder may not be a share's folder, lie inside one or hold one;
  - with it, a folder may be a share's folder, and a read-only one may hold share folders (a
    backup app's view of a whole pool);
  - never a folder inside a share's folder: whoever can write to the share, such as another
    app given it, can put a symbolic link in its place, which Docker follows at the next start
    (the reason of the nesting rule of ADR-0039);
  - never a share's folder for a volume of the app's own data (a catalog folder under
    `{pool}/apps`, which ADR-0145 keeps out of shares too);
  - never a writable folder that holds a share's folder: the app could put another folder in
    the share's place;
  - a share whose folder is not strictly inside a pool (a pool root imported from an old
    smb.conf) is never served (ADR-0145) and counts for nothing.
- The File Manager part of ADR-0160 stays: entries that it creates in a share get the group
  unos-smb from the setgid folder but no group write, until a Repair (ADR-0155); the ledger
  entry `files.group-writable-entries-in-shares` is for that.
- Tests: `supervisor/tests/test_apps.py` (`TestRunInUnosSmb`, `TestShareFolders`) and
  `webui/tests/apps.spec.ts` (Install in the group unos-smb).

## Consequences

- An app in the group reads and changes every file of the share folders it is given; an app
  outside it is given none, also where its default folder (downloads, media) was shared later:
  installing it again needs the option or another folder. File Browser cannot run in the group
  and is refused on a pool that holds a share until its `/srv` names a folder without one;
  Duplicati's read-only view of the pool needs the option there.
- The check runs when an install starts. A share made later on an app's folder, which the
  Shares page allows, is not refused; after a Repair an app outside the group can no longer
  read it, unless its image runs as root.
- linuxserver.io images may give their user the root of a data folder when they start (chown
  to PUID and PGID), so a share's folder given to such an app may come to belong to uid 1000
  with the group unos-smb. The device check shows whether it does.
- Apps of other images cannot use share folders; a way for them (such as `group_add` for images
  that run as a fixed user) would be its own decision.
