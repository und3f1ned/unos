---
status: superseded
date: 2026-11-06
area: shares
decided-by: agent
superseded-by: ADR-0084
---

# ADR-0083: Shares are imported from smb.conf whenever the shares table is empty

## Context

Superseded by ADR-0084: the import runs once per database, controlled by an explicit marker.

When UNOS starts to render smb.conf from its database (ADR-0074), the shares that already exist in the live file must not be lost: they have to become rows first.

## Decision

The first design of slice 2 imported the shares of the existing smb.conf whenever the shares table was empty, and then rendered smb.conf from the rows.

## Consequences

- An empty table is not proof that nothing was imported: after the last share was deleted, a restart would import shares again from a stale file, or from one that UNOS had failed to write.
- An apply that ran before the import rendered zero shares and destroyed every share definition of a hand-made file.
- Both led to the explicit marker of ADR-0084.
