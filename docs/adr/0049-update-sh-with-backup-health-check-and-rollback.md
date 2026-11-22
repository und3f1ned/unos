---
status: superseded
superseded-by: ADR-0455
date: 2026-11-05
area: deploy
decided-by: agent
---

# ADR-0049: Updates go through update.sh, with a backup, a health check and automatic rollback

## Context

Superseded by ADR-0455: only in part, where update.sh followed its branch (`UNOS_BRANCH`) and built the images on the host: it switches to a release tag and pulls the release's images, and its rollback puts the backup's database back; every other rule here stays in force.

The `update.sh` of 1.0.0 built whatever HEAD of main it pulled, could not roll back, backed up the wrong Docker volume (`unos-data` instead of `deploy_unos-data`) and hid that error, and its `git stash` silently replaced deployed but unmerged changes with the upstream code. The in-app updater does not work either (ADR-0173).

## Decision

`deploy/update.sh` is the supported way to update an install:

- it refuses a checkout with local changes or on another branch than `UNOS_BRANCH` (default main), and never stashes;
- it builds the new images while UNOS keeps running, so a failed build changes nothing;
- before the restart it writes a consistent copy of the database (SQLite's online backup API, run in the supervisor container) and an archive of the rest of the data volume into a root-only directory under `/var/lib/unos/backups`, because they contain the JWT secret;
- it tags the running images `:previous`, restarts the stack and checks its health over HTTP and HTTPS within `UNOS_HEALTH_TIMEOUT` (default 300 s); on a failure, or when it is interrupted (Ctrl+C, a closed SSH session), it starts the previous images and source revision again;
- only one update runs at a time (a flock on `/run/unos-update.lock`), and old images are pruned only after the health check has passed;
- it passes the supervisor's start timeout to Compose as `--wait-timeout` (Compose 2.30 or later), so that Compose waits for a healthy supervisor (ADR-0176).
- Tests: `supervisor/tests/test_deploy_scripts.py`.

## Consequences

- An update needs a clean checkout on the branch it follows: local edits on an install must be committed or set aside first.
- The images are built on the host, which on the dev device writes image layers to its SD card (ADR-0214); prebuilt images would avoid that (ADR-0050).
- The update backups stay on the host's disk until someone removes them.

## Considered options

- Pulling released images from GHCR (ADR-0050): planned, but there are no releases yet.
