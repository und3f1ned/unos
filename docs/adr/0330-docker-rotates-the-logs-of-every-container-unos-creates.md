---
status: accepted
date: 2026-11-10
area: deploy
decided-by: agent
supersedes: [ADR-0178]
---

# ADR-0330: Docker rotates the logs of every container that UNOS creates at 10 MB, keeping 3 files

## Context

The containers' json-file logs were never rotated (ADR-0178 proposed to wait for disk monitoring): a container that logs much, an app in a loop above all, fills the system disk in the end. The owner asked to "reach a safe, working MVP as soon as possible", and an unbounded log is a way to lose a working NAS.

## Decision

- `deploy/docker-compose.yml` gives `unos-supervisor` and `unos-webui` `logging: {driver: json-file, options: {max-size: "10m", max-file: "3"}}`.
- The containers UNOS creates get the same options: the app installs of the App Store (`supervisor/src/apps/router.py`) and the Containers page (`supervisor/src/containers/docker.py`) pass `CAPPED_LOG`.
- The host's `daemon.json` stays as it is: UNOS does not change the settings of other containers on the host.

## Consequences

- At most about 30 MB of log per container on the system disk; `docker logs` shows the newest 30 MB. `scripts/dev/check.sh` lists every container's log options.
- The options apply to a container when it is created: Compose recreates the UNOS containers whose `logging` changed at the next `docker compose up` (install.sh, update.sh, or `deploy.sh up` on the dev device, which recreates only the service it names when given one), and an app installed before keeps the log options it was created with until it is installed again.
- The container's options override the host's defaults: on a host whose daemon.json sets another driver (`local`, `journald`) or a smaller `max-size`, these containers still get json-file logs of up to 30 MB each.
- The cap bounds the size, not the writes: each log line is still written to the system disk (`deploy.container-logs-off-the-system-disk`).
