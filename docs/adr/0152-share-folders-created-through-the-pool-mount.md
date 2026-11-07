---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
---

# ADR-0152: Share folders are created only through the pool's mount, checked on descriptors

## Context

No share folder may land on the system disk, and an unmounted pool leaves its bare
mountpoint, a folder on the system disk, at the pool's path. A mount check by path followed
by a `mkdir` by path could race an unmount, and the earlier code even created a removed pool
mountpoint again on the system disk.

## Decision

A missing share folder is created only through the pool's mount
(`service._open_new_folder_parent`, `service._prepare_folder`):

- the pool must be mounted writable (`storage.pools._mounted_pool_target`), and the gid
  check of ADR-0151 must pass;
- the pool root is opened without creating it: when it is missing, 409 "Pool 'POOL' is not
  mounted at TARGET. Mount it on the Storage page first.", and the mountpoint is never made
  again;
- where /proc/self/mountinfo exists, the mount ID of the root's descriptor (`mnt_id` in
  /proc/self/fdinfo) must be a mount at `<pools base>/<pool>`, read with the helpers of
  Repair permissions, else the same 409; when procfs cannot be read, 409 "The mount of pool
  'POOL' could not be checked: REASON. Nothing was created."; where there is no mount table
  at all (macOS tests, the local stand-in), only this part is skipped;
- each folder of the chain is checked (ADR-0153), then the missing parents are created as
  0755, and the share folder as in ADR-0150, relative to the checked descriptors.

An existing folder is opened without following symlinks and never changed. Repair keeps its
own checks that the pool is mounted writable and that the share folder is on its pool's
mount. On Linux the tests replace `service._pool_root_mount_problem`
(`supervisor/tests/conftest.py`), since their pools are folders, not mounts; the tests of
the check call the real function.

Implemented in `supervisor/src/shares/service.py`; tested in
`supervisor/tests/test_share_api.py` (TestFolderCreationOnThePoolMount,
TestPoolRootMountCheck; an unmounted pool).

## Consequences

A share folder is created only on the pool's own filesystem; a pool that is not mounted, or
not where it belongs, gets a 409 instead of a folder on the system disk.

## Considered options

- A mount check by path (findmnt) before a `mkdir` by path: an unmount between the two put
  the folder on the system disk.
