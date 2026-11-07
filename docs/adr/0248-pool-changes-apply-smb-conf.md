---
status: accepted
date: 2026-11-07
area: shares
decided-by: agent
---

# ADR-0248: The storage endpoints apply smb.conf after a pool change, so an unmounted pool's shares stop being served at once

## Context

Only pools that UNOS manages, those with a marker in the host's fstab, hold served shares (ADR-0148,
rule 4 of `share_path_problem`), and Unmount and Remove Pool drop the marker (ADR-0034). But only
share and account changes and the startup reconcile applied smb.conf, so the shares of an
unmounted pool kept their sections until the next change or restart, while the Shares page already
listed them as not served (ADR-0195, shares.reapply-on-pool-unmount).

## Decision

- The endpoints that can add or remove a managed fstab entry, `POST /api/v1/storage/pools`,
  `POST /api/v1/storage/pools/{name}/mount`, `POST /api/v1/storage/pools/{name}/unmount` and
  `DELETE /api/v1/storage/pools/{name}`, apply smb.conf after the pool operation and before they
  answer (`_change_pools` in `supervisor/src/storage/router.py`, `apply_after_pool_change` in
  `supervisor/src/shares/service.py`): under `smb_lock()`, in a database session of its own.
- The apply runs also when the operation failed, since an operation can change the fstab before it
  fails; a request refused before the operation (an invalid name, a `confirm_name` that does not
  match) applies nothing. An unchanged rendering writes nothing and runs no testparm (ADR-0082).
- The shares of an unmounted or removed pool leave smb.conf, and the shrink rule closes their open
  connections after the reload (ADR-0103). Their rows, access and folders stay: mounting the pool
  again, or creating a pool of that name, serves them again.
- The apply never changes the answer: the pool change stands. A failed apply is logged and recorded
  like any other (`last_apply()`), so `GET /api/v1/shares/smb-status` says "smb.conf was not
  updated: ..." until the next change or restart applies again. smb.conf then still has the
  sections of the unmounted pool's shares, whose folders are not there: the empty mountpoint is
  write-protected (ADR-0034).
- No audit entry of its own: the storage endpoints write none for the pool change either
  (security.audit-admin-actions).

Tested in `supervisor/tests/test_smb_integration.py` (the real pool and SMB code on the fake hosts:
unmount and mount, remove, a busy unmount and the mount of a mounted pool that write nothing, a
failed apply), `supervisor/tests/test_pool_persistence.py` (TestPoolEndpoints: which requests
apply) and `webui/tests/storage.spec.ts` (the Shares page after Unmount and Mount).

## Consequences

- The Shares page and smb.conf agree right after a pool change, and Samba no longer offers shares
  whose folders are gone.
- An SMB client with a share of the pool open keeps the pool busy (smbd holds the share's folder),
  so Unmount still answers 409 until the client disconnects. Closing those connections before the
  unmount can abort writes in progress, so it waits for the owner's decision.
- These requests take `smb_lock()`, so they may wait for a share change, a restore or the startup
  reconcile in progress.
- A pool that the host could not mount at boot keeps its marker, so its shares stay in smb.conf, as
  before; its mountpoint is empty and write-protected.

## Considered options

- Stop serving the pool's shares and close their connections before the unmount, and serve them
  again when it fails: it would let the unmount succeed with connected clients, but it disconnects
  them also when the unmount then fails and can abort their writes; not taken without the owner.
- Apply only after a successful operation: a failure after the fstab changed (for example when the
  pool's state is read again at the end) would leave smb.conf behind until the next change.
