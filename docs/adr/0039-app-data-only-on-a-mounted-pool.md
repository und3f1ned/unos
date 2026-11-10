---
status: superseded
date: 2026-11-05
area: apps
decided-by: agent
superseded-by: ADR-0318
---

# ADR-0039: App data lives only inside a selected, mounted pool

## Context

Superseded by ADR-0318: the same rules, without the handling of apps that older versions installed.
In 1.0.0 every app kept its data under `/mnt/pools/default`, a plain folder on the SD card that no pool created; the device's transmission wrote its configuration and downloads there.

## Decision

- Installing an app chooses the pool for its data: `GET /api/v1/apps/pools` lists the mounted pools, and the templates of `supervisor/src/apps/catalog.py` name their data folders through a pool placeholder that the installer replaces (`supervisor/src/apps/router.py`).
- The data must stay inside that mounted pool: no `..`, no symbolic link on the way, and no data folder inside another folder that the app can write to. Only the selected pool gets new folders, and fixed host paths are never created.
- Well-known placeholder secrets (`changeme`, `password`, `admin`, `secret` and the like, `PLACEHOLDER_SECRETS`) are refused for an app's secret settings, and the catalog ships none.
- Before every start of an installed app, `prepare_app_start` checks that its data folders are on a mounted pool and that no folder on the way to a bind source is a symbolic link, and it recreates missing folders only on the app's own pool; otherwise the start is refused with 409.
- Apps installed by an older version, without a pool label and with their data in `/mnt/pools/default` on the system disk, are flagged, and UNOS does not start them; Docker still restarts them until they are stopped.
- Tests: `supervisor/tests/test_apps.py`.

## Consequences

- An app's data lives with its pool: UNOS does not start the apps of a pool that is not mounted, and starts them when the pool is mounted (ADR-0034).
- Docker also starts apps on its own, at boot or after a crash, without these checks; the mount ordering of ADR-0034 and the rule against nested data folders keep those starts on the pool.
- The handling of apps from older versions serves only installs from before Phase 0; under ADR-0207 it is legacy, and its removal is the `feature-list.json` entry `apps.no-legacy-default-pool`.
