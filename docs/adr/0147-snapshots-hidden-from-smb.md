---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
---

# ADR-0147: Snapshot folders are hidden from SMB and never shared or repaired

## Context

`.snapshots` holds the read-only snapshots of a pool or a volume (ADR-0011). Pool roots
cannot be shared (ADR-0145), but volumes and folders can contain `.snapshots`, and an SMB
user with write access could rename or replace it and break the Snapshots page.

## Decision

- Every share section has `veto files = /.snapshots/` (`smbconf._FIXED_SHARE_LINES`), so SMB
  clients neither see nor open it.
- A path with a `.snapshots` component cannot be shared (`service.share_path_problem`).
- Repair permissions skips `.snapshots` and the subvolumes in it
  (`supervisor/src/shares/repair.py`).

Tested in `supervisor/tests/test_smbconf.py` (the fixed lines of every share section),
`supervisor/tests/test_share_api.py` (snapshots cannot be shared) and
`supervisor/tests/test_share_repair.py` (snapshots are skipped; snapshot folders are never
walked).

## Consequences

Snapshots are handled only on the Snapshots page; SMB clients get no Previous Versions view
of them yet (ADR-0161).
