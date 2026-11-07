---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
---

# ADR-0101: SMB3 Unix extensions stay off, so app folders may be shared

## Context

Apps keep their data in pool folders through Docker bind mounts (ADR-0039), and users want to share some of them over SMB (downloads, media). If an SMB client could replace such a folder with a symbolic link, the app's container would get whatever the link points to the next time Docker mounts the folder. The alternative was a rule that a share must not contain a Docker bind source below its folder.

## Decision

- The rendered `[global]` pins `smb3 unix extensions = no` (Samba 4.22's default; `global_settings` in `supervisor/src/shares/smbconf.py`), and SMB1 is off (`server min protocol = SMB2_02`, ADR-0087).
- With these settings no SMB client can create a symbolic link on the server. This was checked in the Samba 4.22.6 source: the only symlink creation in smbd is a request of the SMB1 Unix extensions, which is unreachable without SMB1; a reparse point set over SMB2 is only stored as an extended attribute of a regular file; the SMB3 POSIX extensions are processed only with `smb3 unix extensions = yes`; and SMB2 hard links stay inside one share.
- So folders that apps use may be shared when they are folders or volumes of a managed pool (ADR-0145), and no rule against Docker bind sources inside shares is added.
- The share form (`APPS_NOTE` in `webui/src/lib/components/shares/texts.ts`) and `docs/USER_GUIDE.md` say that SMB users with write access then change what the app sees.
- Tests: `supervisor/tests/test_smbconf.py`, `supervisor/tests/test_smbconf_testparm.py`.

## Consequences

- An SMB user with write access can add, change, rename or delete files in an app's folder, but cannot swap the folder or a file for a link.
- Turning SMB3 Unix extensions on would remove the reason of this decision, so it would need a new ADR.
- Older Samba releases that do not know the line ignore it (ADR-0091).

## Considered options

- A share must not contain a Docker bind source below its folder, checked at create, PATCH and render: to be added only if the source showed that symlink creation was possible; not added.
