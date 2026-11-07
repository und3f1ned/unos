---
status: accepted
date: 2026-11-06
area: system
decided-by: agent
---

# ADR-0172: Scheduled tasks get unique ids, backups run concurrently, and SMART tests time out

## Context

Scheduled tasks created in the same second got the same id: deleting one deleted both, and editing, running or switching one on or off reached only the first. Two scheduled backups with a Keep Count that ran at the same time could fail with "No such file or directory" on a backup the other run had already removed. A scheduled SMART test waited for smartctl without a limit and could stay "running" for good on a hung disk or USB bridge.

## Decision

- New tasks get ids of the form `task_<YYYYmmddHHMMSS>_<8 hex digits>` (`create_task` in `supervisor/src/scheduler/router.py`); existing tasks keep their ids.
- The Keep Count cleanup of scheduled backups skips an old backup that another run removed meanwhile (`_create_scheduled_backup`), so two backup tasks in the same minute, or Run now during a scheduled run, both succeed.
- Scheduled SMART tests kill `smartctl` after 60 seconds (`SMART_TEST_TIMEOUT`) and record the run as failed with "smartctl timed out: <disk> did not answer within 60 seconds" (see also ADR-0072).
- Tests: `supervisor/tests/test_scheduler_validation.py` (ids of tasks created in the same second, a hanging SMART test recorded as timed out), `supervisor/tests/test_backup_files.py` (scheduled backups that run together).

## Consequences

- Ids of older tasks may still collide until such a task is deleted and created again.
- A smartctl blocked in the kernel by a hung disk ends, and its run with it, only once the kernel gives up on the command: the timeout kills the process but still waits for it, so its worker thread stays busy until then.
- Blocking scheduler work runs off the event loop with a time limit (ADR-0021).
