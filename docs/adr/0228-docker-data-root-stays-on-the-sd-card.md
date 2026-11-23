---
status: superseded
date: 2026-11-07
area: device
decided-by: agent
superseded-by: ADR-0470
---

# ADR-0228: Docker's data root stays on the dev device's SD card

## Context

Superseded by ADR-0470: Docker's data root stays on the system disk, which is the eMMC since 2026-11-23.

Image layers, the build cache and the containers' logs live in Docker's data root on the device's SD card (ADR-0214). Moving the data root to the hard disk of the pool `data` was an open owner decision (`device.docker-data-root-on-hdd`), which the owner handed to the agent on 2026-11-07. The facts of that day:

- The eMMC is not written at all (ADR-0225).
- Idle, the device writes about 16 KiB a minute.
- A supervisor deploy of a code change wrote about 19 MiB (sync 4.6 MiB, build 11.8 MiB, up 2.6 MiB), and a base image pull hundreds of MiB.

## Decision

- The data root stays on the SD card.
- The SD card is spared by the existing rules instead: builds off the device where possible and only the final stage on it (ADR-0184), no needless image pulls or containers (ADR-0183), and the base images pinned so that a build does not pull a new base (`deploy.digest-pinned-base-images`).

## Consequences

- UNOS starts without the hard disk: a failed or missing pool disk does not take the management UI down, which a data root on that disk would.
- Deploys keep writing tens of MiB to the SD card. At the measured rates the card lasts far beyond the device's use, and it can be replaced (ADR-0225).
- `device.docker-data-root-on-hdd` stays in the ledger as an option, with this ADR in its notes.
