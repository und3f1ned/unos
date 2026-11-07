---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
---

# ADR-0150: Share folders belong to root and the group unos-smb with mode 2770

## Context

SMB accounts are separate host users, and Samba checks read and write per user at the share
level, while the files they create must stay usable by every user with access to the share.
Apps run as their own users (PUID/PGID 1000), and some of them face the internet.

## Decision

Every SMB account's primary group is unos-smb (gid 70000, ADR-0107). A share folder that
UNOS creates is `root:unos-smb` with mode 2770, setgid so that new entries get the group
(`service._prepare_folder`). Create and PATCH never change an existing folder, also when
`os.mkdir` finds the folder made meanwhile.

Every share section forces the group and the modes (`smbconf._FIXED_SHARE_LINES`):
`force group = unos-smb`, `create mask = 0660` with `force create mode = 0660`, and
`directory mask = 2770` with `force directory mode = 2770`, so files created over SMB are
`<creator>:unos-smb` 0660 and folders 2770. Guests connect as `nobody` and read through the
group bits; they never write (ADR-0096). Apps are not in unos-smb and can neither read nor
write share folders that UNOS created or repaired.

Implemented in `supervisor/src/shares/constants.py` (`SHARE_DIR_MODE`, `SHARE_FILE_MODE`),
`supervisor/src/shares/service.py` and `supervisor/src/shares/smbconf.py`; tested in
`supervisor/tests/test_share_api.py` (a missing folder is created for the SMB group; an
existing folder is never changed; a folder created concurrently is an existing folder),
`supervisor/tests/test_smbconf.py` and `supervisor/tests/test_smb_models.py` (the ids and
modes are fixed).

## Consequences

Within a share, everyone with write access can change, rename and delete every file; privacy
needs separate shares (ADR-0146). Apps that should use a share's folder need the group,
which is left for later (ADR-0160), and folders of earlier guest shares keep their old modes
until a Repair (ADR-0155).
