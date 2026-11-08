---
status: superseded
date: 2026-11-06
area: shares
decided-by: agent
superseded-by: ADR-0261
---

# ADR-0160: The File Manager and apps get no group access to share folders yet

## Context

Superseded by ADR-0261: new folders and uploads of the file manager in a share folder get 2770 and 0660; apps still get no group access.

Share folders use one group, unos-smb, with group write (ADR-0150). The web File Manager
runs as root and creates entries without group write (the supervisor's umask), and apps run
as PUID/PGID 1000, outside the group.

## Decision

Slice 2 neither makes the File Manager create group-writable entries inside shares nor adds
an app install option that runs an app in the SMB group (PGID 70000). Both wait for later
slices.

## Consequences

Entries that the File Manager creates in a share get the group unos-smb from the setgid
folder, but no group write: SMB users can read, rename and delete them, but cannot change
them or create entries in such a folder until a Repair (ADR-0155). Apps can neither read nor
write share folders that UNOS created or repaired, which also keeps internet-facing apps out
of users' files.
