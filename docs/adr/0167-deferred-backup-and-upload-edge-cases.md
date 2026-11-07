---
status: proposed
date: 2026-11-06
area: backup
decided-by: agent
---

# ADR-0167: Fix the deferred backup and upload edge cases of slice 1

## Context

The reviews of Phase 1 slice 1 left a few low-severity backup and upload cases open. They are recorded here so that a later slice fixes them instead of finding them again.

## Decision

Proposed, not implemented:

- A gzip file that is not a valid backup is still stored when it is uploaded as `.tar.gz.enc`, because an encrypted backup cannot be read without its passphrase (ADR-0166); restoring it then fails with "Invalid passphrase or corrupted backup".
- Backups copy the container's `/etc/fstab`, not the host's file with the pool entries (`_collect_backup_content` in `supervisor/src/backup/router.py`; the file is never restored). Either back up the host's file or drop it from backups.
- A client that stops sending in the middle of a file manager upload keeps its unnamed file, and its space, for as long as the connection stays open: neither uvicorn nor Caddy has a timeout for reading a request body.
- On a filesystem without O_TMPFILE (vfat, exfat, NFS, SMB or FUSE mounted by hand below the pools), a supervisor stop during an upload leaves the hidden `.unos-upload-<hex>` file behind (ADR-0169). Removing stale ones automatically means deleting files in user folders, which is an owner decision.

## Consequences

- Until these are fixed: an unusable upload is found only at restore time, the fstab in a backup is of no use for the pools, a stalled upload holds disk space until its connection ends, and leftover `.unos-upload-...` files are deleted by hand in the file manager, as the CHANGELOG says.
- Each fix needs a test that fails on the code before it (ADR-0186).
