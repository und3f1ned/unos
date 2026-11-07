---
status: superseded
date: 2026-11-05
area: device
decided-by: agent
superseded-by: ADR-0183
---

# ADR-0052: Device image checks run throwaway containers

## Context

Superseded by ADR-0183: device checks start no container, because every container created on the device writes to its system disk.

After the Phase 0 images were built on the device, the lead wanted a quick static check of them before the running stack was switched over.

## Decision

- Phase 0's post-build check validated the compose configuration, and it ran `docker run --rm` containers on the device to validate the Caddyfile with `caddy validate` and to check the images: the supervisor's code imports, `pip check`, the required tools and the installed package versions, and the web UI's static files.

## Consequences

- Every `docker run` or create writes about 1.9 MB to the system disk, as measured on 2026-11-06, and the checks ran several times per deploy. ADR-0183 replaced them with static checks and `docker exec` inside the running containers.
