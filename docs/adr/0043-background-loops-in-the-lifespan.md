---
status: accepted
date: 2026-11-05
area: system
decided-by: agent
---

# ADR-0043: Background loops start and stop in the FastAPI lifespan

## Context

The scheduler and the RAID, SMART and UPS monitors registered themselves with `@router.on_event("startup")`, which FastAPI ignores when the app defines a lifespan, as UNOS's app does. No loop had ever started: there were no scheduled snapshots, scrubs, SMART tests or backups and no alerts, while the roadmap marked the monitors as done (ADR-0008).

## Decision

- The background loops start and stop in the lifespan of `supervisor/src/main.py`: `start_scheduler`, `start_health_monitors` (RAID and SMART) and `start_ups_monitor`, stopped in the reverse order at shutdown. Before them the startup mounts the managed pools (ADR-0034) and starts the SMB reconcile as a named task (ADR-0138).
- `UNOS_DISABLE_BACKGROUND_TASKS=1` turns them off, for tests and development.
- Tests: `supervisor/tests/test_lifespan.py` (`test_lifespan_starts_and_stops_all_background_loops`: the tasks exist and run after the start and are stopped at shutdown).

## Consequences

- Since Phase 0, enabled scheduled tasks run on their schedule, custom commands that run as root included (ADR-0044).
- Router startup hooks must not be used: a new background loop is started in the lifespan and added to the test.
