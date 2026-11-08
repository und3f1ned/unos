---
status: superseded
date: 2026-11-06
area: shares
decided-by: agent
superseded-by: ADR-0263
---

# ADR-0148: Only pools that UNOS manages can hold SMB shares, and the UI offers only those

## Context

Superseded by ADR-0263: NFS exports, too, need a pool that UNOS manages.

Shares must live on pools that UNOS manages, mounted by UUID from a managed fstab entry
(ADR-0034), not on plain folders of the system disk. The share form offered every location
of `GET /shares/paths`, also locations that the backend then refused.

## Decision

A share's pool must be one of `storage.pools.managed_pool_names()`, the pools with a UNOS
marker in the host's /etc/fstab (a rule of `service.share_path_problem`).
`GET /shares/paths` gives every location `managed: bool`, whether its pool is managed; a
volume has its pool's value, and every location is false when the pool list cannot be read.
The SMB share form offers only managed locations and preselects the first. When it left some
out, it says 'Pools marked "Not mounted at boot" on the Storage page are not listed: SMB
shares need a pool that UNOS manages.', and with none left it shows "No pool that UNOS
manages is mounted.". A stored path is split against the managed locations only. The NFS
form keeps every location (ADR-0163).

Implemented in `supervisor/src/shares/router.py`, `supervisor/src/storage/pools.py` and
`webui/src/lib/components/shares/ShareFormModal.svelte`; tested in
`supervisor/tests/test_share_api.py` (TestPaths; a pool that UNOS does not manage) and
`webui/tests/shares.spec.ts` (only locations of pools that UNOS manages are offered).

## Consequences

A pool without a managed fstab entry, shown as "Not mounted at boot" on the Storage page,
cannot hold SMB shares until it is mounted again through the Storage page, which gives it
one.
