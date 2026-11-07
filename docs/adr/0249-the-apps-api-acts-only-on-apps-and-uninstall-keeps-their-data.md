---
status: accepted
date: 2026-11-07
area: apps
decided-by: agent
---

# ADR-0249: The Apps API starts, stops, restarts and uninstalls only the containers of apps, and uninstall keeps their data

## Context

`POST /api/v1/apps/installed/{id}/stop` stopped any container that Docker knew, `unos-supervisor` and `unos-webui` included, which locks the administrator out until someone starts them again on the host; start and restart did not check either. Uninstall turned its own 400 for a container that is no app, and Docker's "not found", into 500.

## Decision

- The four actions of the Apps API on an installed app (start, restart, stop, uninstall) take the container from `_get_app` in `supervisor/src/apps/router.py`: 404 "App <id> not found" when Docker does not know it, and 400 "Not a UNOS managed app" when it has no `unos.app` label, which every install from the catalog sets, before anything is started, stopped or removed. Other containers are left to the Containers page.
- Uninstall stops the container and removes it without its volumes (`container.remove()`, no `v=True`); the app's data folders are never touched, and an install of the app on the same pool uses them again. The Apps page's confirmation says that the data folders are kept.
- A Docker error of a stop or an uninstall answers 500 "Failed to stop: ..." or "Failed to remove: ..."; both run in a worker thread (ADR-0247). A start is still checked by `prepare_app_start` (ADR-0039), whose 409 names the pool to mount.
- Tests: `supervisor/tests/test_apps.py` (`TestStopAndUninstall`; `TestStartChecks` for the pool named in the 409) and `webui/tests/apps.spec.ts` (Installed apps).

## Consequences

- An app's data outlives its uninstall; deleting it is a file operation of its own, in the File Manager or over SMB.
- Containers that are no apps, such as one made on the Containers page, are started and stopped there, never through the Apps API.
