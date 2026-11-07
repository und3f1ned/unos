---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
---

# ADR-0155: Guest-era permissions are fixed only by an explicit per-share Repair

## Context

Guest shares left folders with mode 0777 and files `nobody:nogroup` 0666. Giving them the
group model changes the owner's data and takes away the access that every other account had,
so it must not happen by itself. A walk over a large share can take long, while browsers
give up on a request after 300 s and real-time data never uses HTTP polling (ADR-0006).

## Decision

Only an administrator's Repair permissions of one share
(`POST /shares/smb/{name}/repair-permissions`) gives its files and folders the group
unos-smb and the modes 2770 for folders and 0660 for files (0770 when the owner may
execute), which removes the access that group and other bits gave other accounts; nothing
does it automatically. Owners and contents never change.

- It runs synchronously in a worker thread, one repair at a time, for at most 240 s and 5
  million entries; then its state is "incomplete", and a new run continues quickly. The last
  result of each share is kept in memory and answered by `GET` on the same path.
- It needs a served share on a pool that is mounted writable, a gid 70000 that no other
  group has (ADR-0151) and a safe parent chain (ADR-0153); otherwise it answers 409 and
  walks nothing.
- It does not change ACL entries: on a file with an ACL the group bits it sets become the
  ACL's mask, so named users and groups keep their access and can get more, and unos-smb
  gets only what the owning-group entry gives. The Repair dialog and `docs/USER_GUIDE.md`
  say so.
- How it walks the tree is ADR-0156.

Implemented in `supervisor/src/shares/repair.py`, `supervisor/src/shares/repair_router.py`
and `webui/src/lib/components/shares/RepairPermissionsModal.svelte`; tested in
`supervisor/tests/test_share_repair.py` and `webui/tests/shares.spec.ts` (repair permissions
shows the last run, a new run and its errors).

## Consequences

Other accounts, and apps that run as their own user, lose access to a repaired share
(ADR-0160). A large share may need several runs.

## Considered options

- Automatic repair: rejected, because it changes the owner's data.
- A background job with a running state: left for later; the bounded request needs no HTTP
  polling.
