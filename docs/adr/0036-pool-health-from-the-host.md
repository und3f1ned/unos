---
status: accepted
date: 2026-11-05
area: storage
decided-by: agent
---

# ADR-0036: Pool health is computed from the host and never assumed

## Context

In 1.0.0 the dashboard showed every pool as healthy (the value was hard-coded), the UI had no degraded or unmounted state, and the RAID monitor reported 0 errors when `btrfs device stats` failed and counted an unmounted pool as healthy.

## Decision

- A pool's health is computed from the most to the least specific cause (`classify_pool_health` in `supervisor/src/storage/pools.py`): `unknown` when the mount table cannot be read, `unmounted`, `error` when btrfs has forced the filesystem read-only after an error, `degraded` when devices are missing, `unknown` when the device error counters cannot be read, `error` when a counter is above zero, else `healthy`. A pool is never reported healthy on missing information.
- The API, the Storage page, the dashboard and the RAID monitor use this value. A RAID alert names the cause: nothing mounted, an unreadable state, a forced read-only filesystem, missing devices, or the error counters of each device (`supervisor/src/health/router.py`).
- Disk details come from the host's view, not the container's: system disk, in use, existing data, partitions, mountpoints, pool membership and WWN (`supervisor/src/storage/disks.py`).
- Tests: `supervisor/tests/test_pool_persistence.py` (`TestPoolHealth`, `TestRaidMonitorHealth`), `supervisor/tests/test_disk_safety.py`, `webui/tests/storage.spec.ts`.

## Consequences

- A failing or missing disk shows up as degraded or error instead of a healthy status, and the RAID monitor sends an alert when a pool leaves the healthy state, becomes read-only or its error counters grow; today such alerts reach only the e-mail and Telegram channels (ADR-0047).
