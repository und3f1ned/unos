---
status: accepted
date: 2026-11-05
area: files
decided-by: agent
---

# ADR-0042: File paths stay inside the pools and never follow symlinks

## Context

The supervisor runs as root in a privileged container that shares the host's PID namespace, so a path that escapes the pools, through `..`, an absolute upload name or a symbolic link, reaches the whole host. In 1.0.0 `startswith` checks let `/mnt/pools-old` through, the upload's file name was joined to the folder unchecked, and symbolic links redirected deletes. Pool contents are writable by SMB users and by apps, so links below the pools are untrusted.

## Decision

- The file manager (`supervisor/src/files/router.py`) is confined to the pools base (`UNOS_POOLS_BASE_PATH`). A path is split into components (`..` is refused), and each component is opened relative to its parent's descriptor with `O_NOFOLLOW`, starting at the base (`open_dir_nofollow` in `supervisor/src/security/validators.py`). The operations then use those descriptors: a symbolic link below the base is handled as a link and never followed, and a directory that is swapped for a link after a check cannot redirect the operation.
- An upload's name is reduced to its last component and validated; nothing is created directly in the pools base, and the base and the pool roots cannot be renamed, moved or deleted.
- Snapshots, certificates and app volumes never follow symbolic links either. Snapshot paths and names, and certificate names (a host name, a wildcard host name or an IP address), are validated before they name a file or go into `openssl` arguments.
- Validator patterns are anchored with `\Z` and matched with `fullmatch`, so a trailing newline fails.
- Later slices reuse the descriptor helpers (`open_dir_nofollow`, `open_subdirs_nofollow`).
- Tests: `supervisor/tests/test_files_security.py`, `supervisor/tests/test_validators.py`, `supervisor/tests/test_snapshots.py`.

## Consequences

- Every feature that touches files below the pools must use the descriptor helpers instead of resolving a path in one go.
- Symbolic links made by users or apps are listed, renamed, moved, copied and deleted as links, never as their targets.
- Volumes, other subvolumes and mounts are protected by a rule of their own (ADR-0170).
