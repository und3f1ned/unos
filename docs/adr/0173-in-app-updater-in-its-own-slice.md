---
status: superseded
superseded-by: ADR-0439
date: 2026-11-06
area: system
decided-by: agent
---

# ADR-0173: Redesign the in-app updater in its own slice; until then update.sh is the way

## Context

Superseded by ADR-0439: the updater's defects are fixed, and Install Update and Rollback run through a release installer, refused with its reason until the owner decides where a release's images come from and how the supervisor replaces its own container.

Install Update and Rollback on System > Updates start nothing, as in 1.0.0: the routes of `supervisor/src/updates/router.py` hand `asyncio.create_task` to FastAPI's BackgroundTasks, which runs it in a worker thread, so the update never starts. It could not work anyway: the updater (`supervisor/src/updates/updater.py`) runs git and `docker compose` in a checkout at `/opt/unos`, and the supervisor container has no such checkout, no git and no Docker CLI. Making it work is a design question, not a bug fix.

## Decision

Proposed:

- A separate updater slice redesigns updates from the web UI: either an updater that runs on the host, or Install refused with a clear message. That slice also fixes the updater's own defects: an interrupted update stays "downloading" for good (install, reset and rollback then answer 409), the download progress rewrites `/data/update_state.json` for every 8 KiB chunk, and `_create_backup` copies the database into the container's writable layer on the system disk, where it is lost when the container is recreated.
- Until then, Install and Rollback should be hidden on the Updates tab (they are still shown), and users update with `sudo /opt/unos/deploy/update.sh` (ADR-0049). The CHANGELOG lists the buttons under Known issues.

## Consequences

- The only working update path stays the script on the host, with its backup, health check and rollback (ADR-0049).
- A release process with tags and prebuilt images is proposed separately (ADR-0050).
- The updater code and its routes stay as they are until that slice.
