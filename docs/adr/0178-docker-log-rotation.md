---
status: superseded
superseded-by: ADR-0330
date: 2026-11-06
area: deploy
decided-by: agent
---

# ADR-0178: Rotate Docker's json-file logs when they grow

## Context

Superseded by ADR-0330: Docker rotates the logs of every container that UNOS creates at 10 MB, keeping 3 files.

Docker keeps the containers' logs in json-file logs on the system disk, without rotation: neither the compose files nor the install script set a log size. The November 2026 audit proposed rotation in the host's `/etc/docker/daemon.json` as a quick win. Measured on the device, the logs grew by about 24 KB in 12 hours.

## Decision

Proposed: no rotation for now, because the measured volume is small. Rotation (a log size and file count in the host's daemon.json, a host change rather than UNOS code) comes together with the planned monitoring of system-disk wear and fill, which shows when the logs grow.

## Consequences

- Until then, a container that logs much more than today grows its log on the system disk without a limit.
- `scripts/dev/check.sh` shows every container's log driver with its options: rotation set through `log-opts` (max-size, max-file) keeps the `json-file` driver and shows up in its options, so a change of the setting is visible on the dev device.
