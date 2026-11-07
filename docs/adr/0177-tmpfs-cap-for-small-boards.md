---
status: proposed
date: 2026-11-06
area: deploy
decided-by: agent
---

# ADR-0177: Size the /tmp tmpfs for boards with 1 GB of RAM

## Context

The supervisor's `/tmp` is a tmpfs of up to 2 GiB (ADR-0171), sized so that the largest restore and a backup made meanwhile fit: three times `MAX_TAR_BYTES` (640 MiB) of `supervisor/src/backup/router.py`. The documented minimum is 1 GB of RAM (`README.md`, `docs/INSTALL.md`), so on such a board a large backup or restore could run the machine out of memory before the tmpfs is full.

## Decision

Proposed, waiting for the owner's choice between:

- `size=25%` of the RAM: the limit follows the board, but the tests that compare the limit with the backup limits (`supervisor/tests/test_deploy_scripts.py`) would have to assume a RAM size; or
- a lower fixed limit together with a lower `MAX_TAR_BYTES`, so that the test that the tmpfs holds the largest restore still passes.

Independently of the choice, a restore could check and extract the archive in one streaming pass instead of decompressing it into `/tmp` first, which would halve its peak use.

## Consequences

- Until then, a backup or restore of a large database can run a 1 GB board out of memory. The tmpfs takes RAM only for the files it holds, so small backups are not affected.
- A lower `MAX_TAR_BYTES` would refuse large backups that restore today.
