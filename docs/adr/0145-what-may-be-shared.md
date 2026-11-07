---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
---

# ADR-0145: A share is a folder strictly inside a managed pool, never the pool root or apps/

## Context

Apps keep their configuration in `<pool>/apps/<app>` (`supervisor/src/apps/catalog.py`),
privileged apps and Portainer with the Docker socket among them, so write access to a pool
root or to `apps/` would let an SMB user change what such an app runs at its next start,
which amounts to root on the host. A plain folder below the pools base can lie on the system
disk, as one on the device did. Rules checked on the raw path string would let spellings
such as `/mnt/pools/data/.` or `/mnt/pools/data//apps` pass.

## Decision

A share is served only when one function, `service.share_path_problem`, finds no problem
with its stored path:

- the path is a valid folder below the pools base, in the normal form of
  `validate_share_path`; create, PATCH and the import store the normalized path, and a
  stored path in another form is not served;
- no component is `.snapshots` (ADR-0147);
- it is strictly inside a pool (`<pools base>/<pool>/<something>`), never the pool root; a
  volume root (`<pool>/<volume>`) may be shared;
- the pool is one that UNOS manages (ADR-0148);
- it is not at or below `<pool>/apps`;
- it does not overlap another share (ADR-0146).

The same function runs at create and PATCH (400, an overlap 409), at the import (the share
is kept without access and with guest access off, with a note), at the restore preparation
and the post-restore reconcile (access and guest access removed, with a note) and in the
render (not served, the problem listed). A PATCH of an unserved share may change anything,
but its result must pass the rules if it grants anything, and Repair refuses such a share
with 409. The web UI requires a folder when a pool, not a volume, is selected; for a pool
root the text is "Share a folder inside the pool or a volume: the top level of a pool holds
app data (apps/), volumes and snapshots". A pool-root share found in an existing smb.conf is
imported with a note and not served until its folder changes (the device's DATA share was
one; ADR-0220 records its current state).

Implemented in `supervisor/src/shares/service.py`; tested in
`supervisor/tests/test_share_apply.py` (TestSharePathProblem),
`supervisor/tests/test_share_api.py` (TestCreate, TestNotServed),
`supervisor/tests/test_backup_restore.py` (a pool-root share loses its access) and
`webui/tests/shares.spec.ts`.

## Consequences

Pool roots and app data can never be shared, and no spelling of a path makes it servable.
Folders that apps use, such as downloads or media, can still be shared, and SMB users with
write access then change what the app sees (ADR-0101).
