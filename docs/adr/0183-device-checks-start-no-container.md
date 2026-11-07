---
status: accepted
date: 2026-11-06
area: device
decided-by: agent
supersedes: [ADR-0052]
---

# ADR-0183: Device checks start no container

## Context

Phase 0's post-build checks ran throwaway `docker run --rm` containers on the device (ADR-0052). Measured on 2026-11-06, every `docker run` or `docker create` writes about 1.9 MB to the device's system disk, which cannot be replaced (ADR-0214), and checks run several times per deploy.

## Decision

- Device checks never start a container.
- After a build, the checks are static: they read the image's configuration (`docker image inspect`, for example that `PYTHONDONTWRITEBYTECODE=1` is set) and the compose configuration.
- After `up`, the checks run inside the running containers with `docker exec`, without bytecode and without a pip cache (pinned packages, `pip check`, the host tools in the image, Caddy's version and the web UI's content security policy).
- Check scripts keep scratch data only in RAM (`/dev/shm`) or on the data pool, and report what they wrote to the system disk.
- Implemented by `scripts/dev/check.sh` (read-only, the device's root filesystem mounted read-only for the check) and the build and up steps of `scripts/dev/deploy.sh`, covered by `supervisor/tests/test_dev_scripts.py`.

## Consequences

- A check cannot test an image that is not running; an image is checked for real only once it runs.
- Making the root filesystem of the Linux test runner needs one `docker create`, so it is an owner action (ADR-0187).
