---
status: superseded
date: 2026-11-06
area: shares
decided-by: agent
superseded-by: ADR-0261
---

# ADR-0163: NFS exports stay unchanged in slice 2

## Context

Superseded by ADR-0261: UNOS keeps its NFS exports in its own export file on the host and reads every export file as exportfs reads it.

Slice 2 is about SMB (ADR-0059). NFS exports are written to /etc/exports by
`supervisor/src/shares/nfs.py`, and a parser for existing exports has been in the backlog
since before the slice.

## Decision

The NFS exports parser stays in the backlog, and the NFS schemas, endpoints and module are
unchanged by slice 2. `GET /shares/paths` gives every location the new `managed` flag, and
the NFS form still offers every location, managed or not (ADR-0148).

## Consequences

NFS keeps its earlier behavior, also on pools that UNOS does not manage. The uids and the
gid of SMB accounts are stable from now on (ADR-0107), ready for a later export with
AUTH_SYS.
