---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
---

# ADR-0149: SMB changes fail closed when the pool list cannot be read

## Context

Which pools may hold shares comes from the host's /etc/fstab markers (ADR-0148). When that
list cannot be read, any answer would be a guess, and a guess could widen access.

## Decision

SMB changes fail closed when the pool list cannot be read:

- the apply raises `service.PoolListUnavailable` and leaves smb.conf unchanged, and the
  share endpoints answer 503;
- the import imports nothing, and it is tried again at the next start or change (ADR-0084);
- the restore of a database that keeps its shares is refused with 503 before anything
  changes; the import that a non-authoritative restore leaves pending (ADR-0142) waits for a
  readable list;
- `GET /shares/paths` marks every location as not managed;
- Repair permissions answers 503 and walks nothing.

Implemented in `supervisor/src/shares/service.py`, `supervisor/src/shares/router.py`,
`supervisor/src/shares/repair_router.py` and `supervisor/src/backup/router.py`; tested in
`supervisor/tests/test_share_apply.py` (an unreadable pool list),
`supervisor/tests/test_share_api.py` (the pool list unavailable is 503; none is managed when
the pool list cannot be read), `supervisor/tests/test_share_import.py`,
`supervisor/tests/test_backup_restore.py` (an unreadable pool list restores nothing) and
`supervisor/tests/test_share_repair.py`.

## Consequences

A host whose fstab cannot be read blocks share changes, imports and these restores until it
is fixed, and never widens access.
