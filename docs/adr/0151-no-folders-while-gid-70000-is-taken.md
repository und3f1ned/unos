---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
---

# ADR-0151: No share folder is created and no Repair runs while another group has gid 70000

## Context

New share folders and repaired files get gid 70000 by number, and the container's /etc/group
is not the host's. On a host where another group has that gid, its members and apps would
get group rights on every new share folder and every repaired file.

## Decision

`accounts.gid_problem()` reports, read-only, when `getent group 70000` on the host names a
group other than unos-smb. Before a share folder is created (`service._prepare_folder`) and
before Repair (`supervisor/src/shares/repair_router.py`), such a conflict answers 409 with
the problem text, and nothing is created or changed. When gid 70000 is free, or the host has
no group database to ask (the local stand-in), creation goes on. `GET /shares/smb-status`
lists the conflict with "No share can be opened until the group problem is fixed", once when
the group check and `gid_problem()` describe the same conflict. unos-smb is created with
gid 70000 before every activation (`accounts.ensure_group`, which refuses with 409 while
unos-smb has another gid or gid 70000 belongs to another group) and at the startup and
post-restore reconcile (`accounts._reconcile_host`, which records the same conflict as a
status problem instead and creates no account).

Implemented in `supervisor/src/shares/accounts.py`, `supervisor/src/shares/service.py` and
`supervisor/src/shares/repair_router.py`; tested in `supervisor/tests/test_share_api.py` (a
gid problem creates nothing; TestGidConflictInTheStatus),
`supervisor/tests/test_share_repair.py` (a gid problem is refused) and
`supervisor/tests/test_smb_accounts.py` (TestCollisions).

## Consequences

A host with a conflicting group gets no new share folders, no repairs and no new SMB
accounts until an administrator renumbers or removes the other group.
