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
  `PUID=80000` (`SMB_APP_UID` of `supervisor/src/shares/constants.py`), `PGID=70000`
  (`SMB_GROUP_GID`) and `UMASK=007` to the app's environment (`UNOS_SMB_ENVIRONMENT` of
  `supervisor/src/apps/router.py`): the image's processes run as user 80000 in the group
  unos-smb, and what they create gets the modes that Samba gives new entries in a share
  (folders 2770 below the setgid share folder, files 0660, ADR-0150), so SMB users can change
  it and no other account can read it. Off by default; the form warns that the app, and
  whoever takes it over, gets the files of the share folders it is given.
- User 80000 is no other app's. Apps outside the group run as their template's PUID (1000) or
  as their image's fixed user, often 1000 too (File Browser, Node-RED, n8n, Vikunja), and
  linuxserver.io images may give the root of a folder they are given to their user when they
  start (chown to PUID and PGID). With PUID 1000, a share's folder given to an app in the group
  could thus come to belong to uid 1000, and so would everything the app writes there: every
  app that runs as 1000 and reaches the folder would own them. 80000 lies outside the uids of
  SMB accounts (70000-79999), above `UID_MAX` of login.defs, below `SUB_UID_MIN` and in the
  block that systemd never allocates, like the ids of ADR-0107.
- Only templates of linuxserver.io images with a pool folder outside `{pool}/apps` offer it
  (`AppTemplate.can_run_in_unos_smb`): their base image takes the user and group of its
  processes from PUID and PGID and the umask of its services from UMASK, and all of them
  declare PUID and PGID; a folder of the app's own data under `{pool}/apps` is never a share's
  folder, so the group would give an app that has no other (Prowlarr, Nextcloud, WireGuard)
  nothing. Other images ignore PUID and PGID, also where their template declares them
  (jellyfin's image runs as root, Plex's takes PLEX_GID). The option is refused with 400 for
  all these. Docker's `group_add` is not used: an image that switches to its user with that
  user's own group list drops it.
- PUID and PGID are not `env_overrides` values: an install that sets either is refused with
  400, so only the option changes them. PGID 70000 would put an app into the group without
  it, and PUID would make the app's processes root (0), an SMB account or the apps in the
  group. The Environment tab shows both read-only.
- Each install compares the app's pool folders with the folders of all shares in the database,
  served or not (`_check_share_folders`), before the image is pulled:
  - without the option, a folder may not be a share's folder, lie inside one or hold one;
  - with it, a folder may be a share's folder, and a read-only one may hold share folders (a
    backup app's view of a whole pool);
  - never a snapshot folder (`.snapshots`) or the trash (`.unos-trash`, ADR-0259), or a folder
    in one: they keep files of shares outside the shares' folders, also of shares deleted or
    moved since, protected only by their modes, which an image that runs as root ignores.
    Shares are refused there too (ADR-0145). A folder that holds them, a pool or a volume, is
    judged by the shares it holds;
  - never a folder inside a share's folder: whoever can write to the share, such as another
    app given it, can put a symbolic link in its place, which Docker follows at the next start
    (the reason of the nesting rule of ADR-0039);
  - never a share's folder for a volume of the app's own data (a catalog folder under
    `{pool}/apps`, which ADR-0145 keeps out of shares too);
  - never a writable folder that holds a share's folder: the app could put another folder in
    the share's place;
  - a share whose folder is not strictly inside a pool (a pool root imported from an old
    smb.conf) is never served (ADR-0145) and counts for nothing.
- `GET /api/v1/apps/installed` says for each app whether it runs in the group
  (`InstalledApp.runs_in_unos_smb`: its container's PGID is 70000), and nothing else of the
  container's environment. The Installed tab of `webui/src/routes/apps/+page.svelte` says
  "Runs in the group unos-smb", and the details of an installed app show the PUID and PGID it
  runs with, never the template's in their place.
- The File Manager part of ADR-0160 stays: entries that it creates in a share get the group
  unos-smb from the setgid folder but no group write, until a Repair (ADR-0155); the ledger
  entry `files.group-writable-entries-in-shares` is for that, with the modes that apps in the
  group already give their entries (2770 and 0660).
- Tests: `supervisor/tests/test_apps.py` (`TestRunInUnosSmb`, `TestShareFolders`,
  `TestInstalledApps`), `supervisor/tests/test_smb_models.py` (`TestConstants`) and
  `webui/tests/apps.spec.ts` (Install in the group unos-smb, Installed apps in the group
  unos-smb).

## Consequences

- An app in the group reads and changes every file of the share folders it is given; an app
  outside it is given none, also where its default folder (downloads, media) was shared later:
  installing it again needs the option or another folder. File Browser cannot run in the group
  and is refused on a pool that holds a share until its `/srv` names a folder without one;
  Duplicati's read-only view of the pool needs the option there.
- A share's folder given to an app in the group may come to belong to user 80000 with the
  group unos-smb and its mode 2770, and the app's entries in it belong to 80000; Repair
  permissions changes no owner (ADR-0155). The apps in the group share that user, but they
  share the group's access as well: each reaches only the share folders it is given, and a
  read-only folder that holds shares lets it change nothing.
- An app installed again with the option runs as user 80000: what it wrote before as 1000
  keeps that owner and is reached through the group after a Repair. Whether its image gives
  its own data folder (`/config`) to the new user when it starts is for the device check.
- The check runs when an install starts. A share made later on an app's folder, which the
  Shares page allows, is not refused; after a Repair an app outside the group can no longer
  read it, unless its image runs as root.
- Apps of other images cannot use share folders; a way for them (such as `group_add` for images
  that run as a fixed user) would be its own decision.

## Considered options

- PUID 1000 for apps in the group, with a share folder that comes to belong to 1000 reported
  by the device check: refused, because every other app that runs as 1000 would own it.
- A user per app in the group: refused, because they share the group's access anyway, and the
  ids would need an allocation that nothing else needs.
- UMASK 002: refused, because what the app writes would be readable by every account, where
  Samba's modes in a share (0660, 2770) give it to the group only.
