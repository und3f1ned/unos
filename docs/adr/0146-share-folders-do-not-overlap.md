---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
---

# ADR-0146: Share folders must not overlap

## Context

Samba enforces read and write per user at the share level, while the filesystem gives the
whole group unos-smb the same rights (ADR-0150). The users of an outer share therefore reach
every file of a share inside it, and the inner share's access list would mislead. Privacy
between users needs separate shares.

## Decision

A share folder must not be inside another share's folder or contain it (the last rule of
`service.share_path_problem`). Create, and a PATCH that sends a path, answer 409 ("The folder
overlaps share X (one is inside the other)..."); a PATCH that grants access on a share whose
stored path overlaps another gets 400 with the same text, since a share that is not served
grants nothing (ADR-0136). At the import, the restore and the render both
overlapping shares are kept without access and are not served. Only shares whose own folders
pass the other path rules count, so an unserved pool-root share does not block folder shares
in its pool.

Implemented in `supervisor/src/shares/service.py`; tested in
`supervisor/tests/test_share_api.py` (overlapping folders; a folder containing a share; a
pool-root share does not block folders of its pool; a path overlap at PATCH) and
`supervisor/tests/test_share_apply.py` (the overlap rule).

## Consequences

Folders side by side are the only way to give different users different files. An existing
smb.conf with nested shares leaves both without access until an administrator moves one of
them.

## Considered options

- Keeping imported overlapping shares served, with a note (the first design of slice 2):
  their access lists would mislead.
