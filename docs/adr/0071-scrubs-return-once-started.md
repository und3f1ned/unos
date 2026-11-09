---
status: superseded
date: 2026-11-06
area: storage
decided-by: agent
superseded-by: ADR-0311
---

# ADR-0071: Scrubs return once started, and scheduled scrubs need a mounted pool

## Context

Superseded by ADR-0311: every start of a scrub goes through one helper, which answers 409
without btrfs for a pool whose btrfs filesystem is not mounted.

In 1.0.0, Start Scrub blocked the whole API for the whole scrub, hours on a large pool: `btrfs scrub start` returns at once, but the process it forks keeps btrfs's output pipes open until the scrub ends. A scheduled scrub of a pool that was not mounted ran on the folder under the mountpoint, that is on the root filesystem.

## Decision

- `start_scrub` in `supervisor/src/storage/pools.py` sends btrfs's output to /dev/null and its error output to an unnamed temporary file, so it returns as soon as btrfs has started the scrub. The scrub endpoints run it in a worker thread with a timeout (`SCRUB_COMMAND_TIMEOUT`, 60 s; `_scrub` in `supervisor/src/storage/router.py`).
- A scheduled scrub (`supervisor/src/scheduler/router.py`) first checks that the pool is mounted and fails with "Pool <name> is not mounted at <path>" otherwise; it starts the scrub the same way, in a worker thread, within `SCRUB_START_TIMEOUT` (60 s).
- Resetting the device error counters (`POST /api/v1/health/raid/{pool}/reset-stats`) accepts only valid pool names and runs in a worker thread with the same timeout.
- Tests: `supervisor/tests/test_storage.py`, `supervisor/tests/test_scheduler_validation.py`, `supervisor/tests/test_health_monitor.py` (reset-stats).

## Consequences

- A scrub never holds up other requests, and its progress is read with the status endpoint.
- A btrfs stuck in the kernel on a hung filesystem is killed but still waited for, so it keeps a worker thread until the kernel lets it go.
- Still open: the scrub-start workaround exists twice (storage and scheduler) instead of once, and `POST /api/v1/storage/pools/{name}/scrub` does not check that the pool is mounted; the page hides the button for an unmounted pool, and a 409 is planned.
