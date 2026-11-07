---
status: accepted
date: 2026-11-07
area: system
decided-by: agent
---

# ADR-0233: The supervisor image carries its version, and System > Updates shows only what works

## Context

`get_current_version` of `supervisor/src/updates/updater.py` ran git in `/opt/unos` inside the supervisor container, which has neither, so System > Updates and General showed the version "unknown". The Updates tab offered Install Update and Rollback, which start nothing (ADR-0173), and said "You're running the latest version" when GitHub had no release to show (its 404, also for a private repository). The dev device's checkout is synced file by file and is not a git workspace (`system.updates-tab-without-dead-buttons`).

## Decision

- The version is a build argument of the supervisor image: `ARG UNOS_VERSION=unknown` and `ENV UNOS_VERSION` in `supervisor/Dockerfile`, after the code, passed by `deploy/docker-compose.yml` as `${UNOS_VERSION:-unknown}`. `deploy/install.sh` and `deploy/update.sh` set it to `git describe --tags --always` of the checkout they build, as `update.sh` already names versions in its messages. `get_current_version` reads it and runs nothing: `commit_hash` is the commit the description names, `is_development` is true when it names commits after a tag or only a commit, and without it the version is "unknown".
- `GET /api/v1/updates/check` answers `latest_version: null` and `release_info: null` when no release is published, and the tab says so. It compares a version with commits after a tag as that tag (`v1.2.0-3-g712fc3a` as `v1.2.0`): commits after a release are not older than it, where the semver pattern alone took them for a pre-release of it.
- Until `system.in-app-updater` (ADR-0173) the Updates tab shows the current version, the latest release, checked when the tab opens and again on request, with pre-releases on request, and names `sudo /opt/unos/deploy/update.sh` as the way to update (ADR-0049). Install Update, Rollback, the update progress, the history and the settings leave the tab: the in-app updater writes the progress and the history, and nothing reads the automatic-check settings. Their routes and the updater code stay as they are until that slice.
- General shows the same version.
- Tests: `supervisor/tests/test_updates.py`, `supervisor/tests/test_deploy_scripts.py` (the build argument and what the scripts pass), `webui/tests/updates.spec.ts`.

## Consequences

- An image built without the argument reports "unknown": a plain `docker compose build`, and the dev device's deploy until `scripts/dev/deploy.sh` passes `UNOS_VERSION`.
- The version names the checkout that was built, not a release: an install that follows a branch shows `<tag>-<n>-g<commit>`, or only the commit while the repository has no tag.
- Opening the tab asks GitHub's API, which allows 60 requests an hour per address without a token: beyond that the check answers an error.
