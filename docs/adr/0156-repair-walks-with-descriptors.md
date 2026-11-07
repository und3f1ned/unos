---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
---

# ADR-0156: Repair walks the folder tree with descriptors and opens nothing it must not

## Context

SMB users and app containers can change a share while Repair runs. `os.fwalk` opens folders
without `O_NOFOLLOW` and `O_DIRECTORY`, an ordinary read-only open of a file can start a
device node that a container swapped in, a deep tree can exhaust the descriptors, and
private files must not be opened to the group.

## Decision

Repair (`repair.repair_tree`) walks depth-first with its own stack and never resolves a
path:

- Folders are opened relative to their parent's descriptor with `O_RDONLY`, `O_DIRECTORY`,
  `O_NOFOLLOW`, `O_NONBLOCK` and `O_CLOEXEC`, and changed with `fchown` and `fchmod` on that
  descriptor.
- Files are opened with `O_PATH` and `O_NOFOLLOW`, which never calls a driver's open; their
  `fstat` must match the examined entry (device and inode) and show a regular file with one
  link, and they are changed through `/proc/self/fd/<fd>`.
- An entry swapped since it was examined is skipped as "raced".
- The walk stops at depth 64 ("too_deep"), so at most 65 folder descriptors are open.
- It skips other filesystems (btrfs subvolumes and every mount point of
  /proc/self/mountinfo, by path and by the mount ID of each descriptor; up to 20 are
  listed), symlinks, hard-linked files, FIFOs, sockets and device nodes, `.snapshots`, and
  private entries without group or other bits; a private folder is not entered.

It is Linux-only (`O_PATH`, /proc): without `O_PATH` it changes nothing, and its tests skip
on macOS and run in the Linux test runner (ADR-0187).

Implemented in `supervisor/src/shares/repair.py`; tested in
`supervisor/tests/test_share_repair.py` (TestWalk).

## Consequences

Repair changes only entries of the share's own filesystem that it examined, never a
swapped-in device node, link or mount. Skipped entries keep their modes, and the result
counts them.

## Considered options

- `os.fwalk`: it opens folders without `O_NOFOLLOW` and `O_DIRECTORY`.
