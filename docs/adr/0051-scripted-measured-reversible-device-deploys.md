---
status: superseded
date: 2026-11-05
area: device
decided-by: agent
superseded-by: ADR-0223
---

# ADR-0051: Deploys to the device are scripted, measured and reversible

## Context

Superseded by ADR-0223: deploys keep their backups and rollback copies on the workstation; `up` first backs up the device's fstab, smb.conf and `unos-data` volume, and `rollback` also restores smb.conf.

Since Phase 0 nobody edits files on the device (ADR-0016), but every change still has to reach it, and its system disk is an SD card that wears out and cannot be replaced (ADR-0214). A deploy must therefore be reproducible, measured and possible to undo.

## Decision

- Deploys to the device run from scripts, step by step, never as edits by hand. Today that is `scripts/dev/deploy.sh` with the steps `status`, `sync [--dry-run]`, `build-supervisor`, `build-webui`, `up [service]`, `rollback` and `all`, each under the device lock (ADR-0210).
- Before anything changes, the scripts keep what is needed to go back. In Phase 0 and the slices they stored the host's fstab and smb.conf, the git state, an archive of the data volume and the containers' details in a root-only directory on the device and tagged the old images; `deploy.sh` saves the device's versions of the files it replaces on the workstation and tags the previous images `:rollback`.
- The device's checkout gets only the files that differ, and the result is checked. In Phase 0 and the slices a patch was applied from RAM (`/dev/shm`) and checked against the expected git tree; `deploy.sh` streams the differing files over SSH and compares the files by sha256 (`scripts/dev/tree.py`).
- Only what must be built on the device is built there: the supervisor image, where usually only the source layer changes, and the final stage of the web UI image, whose static files are built on the workstation and whose build context lies in the device's RAM (ADR-0184). Checks start no container (ADR-0183).
- After `up`, the stack must become healthy, and the checks run: `scripts/dev/check.sh` for the device's health, and in Phase 0 and the slices also a smoke test of 18 checks and the slice's own checks. Every step prints the MiB written to the system disk (`scripts/dev/sd.sh`).
- Every deploy can be rolled back: `deploy.sh rollback` restores the files and images of before the last sync and builds. The database in the `unos-data` volume is not rolled back.

## Consequences

- A deploy is a sequence of steps that can be reviewed one by one; only `status` and `sync --dry-run` leave the device unchanged, and the other steps run when the owner asks for a deploy.
- Rolling back the database needs a backup taken separately, for example by `update.sh` (ADR-0049).
- The writes are known: the slice 1 deploy wrote 38 MiB to the SD card, and the slice 2 deploy 45 MiB (image layers) plus 4 MiB for its checks.

## Considered options

- Editing on the device and running `docker compose build` and `up -d` there, as the old CLAUDE.md said (ADR-0003): no record of what changed, no rollback, and full builds on the SD card.
