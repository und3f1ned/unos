---
status: accepted
date: 2026-11-10
area: apps
decided-by: agent
supersedes: [ADR-0039]
---

# ADR-0318: App data lives only on a mounted pool, without handling for apps of older versions

## Context

ADR-0039 put the data of every app inside a selected, mounted pool, after 1.0.0 had kept it
under `/mnt/pools/default`, a plain folder on the SD card that no pool created, and it kept a
handling of the apps that older versions had installed there: without a pool label, flagged and
not started, with a 409 and a warning of their own. UNOS has no such installations (ADR-0207),
and the entry `apps.no-legacy-default-pool` removes that handling; the agent took it up under the
owner's delegation ("work on through the features, ask no questions; I allow everything", in
translation, 2026-11-09).

## Decision

- Installing an app chooses the pool for its data: `GET /api/v1/apps/pools` lists the mounted
  pools, and the templates of `supervisor/src/apps/catalog.py` name their data folders through a
  pool placeholder that the installer replaces (`supervisor/src/apps/router.py`).
- The data must stay inside that mounted pool: no `..`, no symbolic link on the way, and no data
  folder inside another folder that the app can write to. Only the selected pool gets new
  folders, and fixed host paths are never created.
- Well-known placeholder secrets (`changeme`, `password`, `admin`, `secret` and the like,
  `PLACEHOLDER_SECRETS`) are refused for an app's secret settings, and the catalog ships none.
- An installed app's data is what lies under the pools base, as configured or with symbolic
  links resolved (`_pools_bases`), where an install puts every pool volume. Before every start,
  `prepare_app_start` checks that these folders are on a mounted pool and that no folder on the
  way to a bind source is a symbolic link, and it recreates missing folders only on the app's own
  pool; otherwise the start is refused with one 409 that names the pool that the folder lies on
  (`_pool_folder`). The list of installed apps names such folders (`data_outside_pools`), and the
  web UI warns that they are not available.
- Tests: `supervisor/tests/test_apps.py`.

## Consequences

- An app's data lives with its pool: UNOS does not start the apps of a pool that is not mounted,
  and starts them when the pool is mounted (ADR-0034).
- Docker also starts apps on its own, at boot or after a crash, without these checks; the mount
  ordering of ADR-0034 and the rule against nested data folders keep those starts on the pool.
- A container that UNOS did not install and that binds a folder outside the pools base counts
  that folder as a fixed host path; one that binds the pools base itself is told to mount the
  pools base.
