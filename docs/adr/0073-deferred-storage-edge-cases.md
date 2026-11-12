---
status: superseded
superseded-by: ADR-0352
date: 2026-11-06
area: storage
decided-by: agent
---

# ADR-0073: Fix the deferred storage and snapshot edge cases of slice 1

## Context

Superseded by ADR-0352: what was built for the cases below, and the restored copies of a volume's snapshots, which stay open.

The reviews of Phase 1 slice 1 found storage and snapshot cases of low severity, most of them theoretical races or cases that fail safe. They are recorded here so that a later slice fixes them instead of finding them again.

## Decision

Proposed, not implemented:

- Restoring a snapshot of a volume creates a nested `restore_<ts>` subvolume that no page can delete now: let the Snapshots page list and delete such copies by subvolume id after an in-use check.
- The quota rollback guard of a new volume (ADR-0069) can miss a subvolume that is being renamed; a root-only staging folder with `RENAME_NOREPLACE` would close it.
- When Docker cannot be asked during a pool unmount, the host mount of a Docker volume that binds the pool can be taken for a deliberate mount elsewhere (ADR-0070).
- The comparison of a Docker volume's bound folder with a pool or volume is lexical (symlinks, relative paths, third-party volume drivers).
- A subvolume name with a line break can make later volume creations with a quota answer a false 409 and leave the new volume without its quota (fails safe); a failing subvolume list right after a create answers 500 without saying that the volume exists without its quota.
- The wording of `create_volume`'s 409 when something else took the new name; renaming or moving a folder that is itself a bind mount answers 500 instead of 409.
- A mount or subvolume that appears below a folder during a delete walk is still deleted into: a walk on descriptors that checks the device of each folder would fix it.
- Whole disks named like `xvdaa`, `sdaaa` or `dasda` are listed, but requests that name them answer 400.

## Consequences

- Until these are fixed, the cases above behave as described. They were judged low in severity: most are theoretical races, and the false 409 fails safe.
- Each fix needs a test that fails on the code before it (ADR-0186).
