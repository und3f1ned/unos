---
status: accepted
date: 2026-11-08
area: shares
decided-by: agent
supersedes: [ADR-0148]
---

# ADR-0293: Only pools that UNOS manages can hold SMB shares and NFS exports, and the UI offers only those

## Context

ADR-0148 limited SMB shares to the pools that UNOS manages and kept every location for NFS exports (ADR-0163). A pool that UNOS does not manage has no managed fstab entry and may be missing after a reboot, so an export of it would point at the system disk as a share would. NFS exports now reach the host (ADR-0291).

## Decision

A share's or an export's pool must be one of `storage.pools.managed_pool_names()`, the pools with a UNOS marker in the host's /etc/fstab (a rule of `service.share_path_problem` for shares, of `_check_folder` in `supervisor/src/shares/nfs.py` for exports). `GET /shares/paths` gives every location `managed: bool`, whether its pool is managed; a volume has its pool's value, and every location is false when the pool list cannot be read. The SMB share form and the NFS export form offer only managed locations and preselect the first. When they left some out, they say 'Pools marked "Not mounted at boot" on the Storage page are not listed: SMB shares need a pool that UNOS manages.', or for NFS 'NFS exports need a pool that UNOS manages.', and with none left they show "No pool that UNOS manages is mounted.". A stored share path is split against the managed locations only.

Implemented in `supervisor/src/shares/router.py`, `supervisor/src/shares/nfs.py`, `supervisor/src/storage/pools.py`, `webui/src/lib/components/shares/ShareFormModal.svelte` and `webui/src/lib/components/shares/NfsExportFormModal.svelte`; tested in `supervisor/tests/test_share_api.py` (TestPaths; a pool that UNOS does not manage), `supervisor/tests/test_nfs.py` (TestCreate) and `webui/tests/shares.spec.ts` (only locations of pools that UNOS manages are offered).

## Consequences

A pool without a managed fstab entry, shown as "Not mounted at boot" on the Storage page, cannot hold SMB shares or NFS exports until it is mounted again through the Storage page, which gives it one. Exports that the owner wrote by hand for such a pool stay, and UNOS lists them.
