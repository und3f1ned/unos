---
status: accepted
date: 2026-11-22
area: shares
decided-by: agent
---

# ADR-0193: ACL entries and sticky folders count in the parent-chain rule

## Context

Share folders are created only below folders that only root can change (ADR-0153): whoever can change a folder above a share's folder can put another folder in its place, and Samba and Repair permissions then use that one. The mode bits alone miss two cases. With a POSIX ACL the group bits show the ACL's mask, so a folder owned by root:root with a write entry for uid 1000 looks safe although uid 1000 can rename the share folder (btrfs has ACLs on by default). In a folder with the sticky bit the owner of an entry may still rename it, so a share folder owned by another account could be swapped.

## Decision

Implemented, tested and described in the CHANGELOG; accepted on 2026-11-22 by the lead, to whom the owner delegated it ("decide everything that can be decided without me; do it"), after the device check of its sticky rule before and after a reboot.

- When the group bits allow writing and the group is gid 0, so that the mode alone looks safe, the folder's access ACL (`system.posix_acl_access`, Linux only) is read from its descriptor. A named user entry other than uid 0 and the supervisor's euid, or a named group entry other than gid 0, with write permission, while the mask allows writing, makes the folder unsafe; the detail names the entries ("mode 0775, ACL entries for uid 1000"). An ACL that cannot be read is an error of the check: the status says it could not be checked, and create and Repair answer 500.
- The sticky bit excuses the other writers of the share folder's parent only when the share folder itself belongs to uid 0 or the supervisor's euid, read with `fstatat` without following a symlink. This is checked for existing folders and when a create finds that the folder was made meanwhile.
- Implemented in `_acl_writers` and `_unsafe_folder` of `supervisor/src/shares/service.py`; tested in `supervisor/tests/test_share_api.py` (the rule matrix, the parsing of the ACL bytes, a real ACL on Linux, and the 409, warning and status line of the sticky and ACL cases).

## Consequences

- Neither extension changes anything on the dev device today.
- Folders that an administrator opened up with ACL entries block new share folders below them and Repair, and get a warning and a status line otherwise, like a mode 0777 folder.
- If the owner rejects the proposal, the rule goes back to the mode bits of ADR-0153, and the code, its tests and the CHANGELOG text are removed together.
