---
status: accepted
date: 2026-11-08
area: system
decided-by: agent
---

# ADR-0268: A task run that a stop of the supervisor interrupts is recorded as failed

## Context

A run is saved as "running" when it starts and recorded when it ends (`run_task` in `supervisor/src/scheduler/router.py`). A run cut off by a stop of the supervisor (a deploy, an update, a reboot, the kernel's out-of-memory killer) was never recorded: the history showed it running for good, without an exit status or an error, and its task kept the status of the run before. A custom task's command may run for up to 24 hours (ADR-0267), so a stop during a run is likely (`system.custom-command-tasks`).

## Decision

- When the scheduler starts (`start_scheduler`, in the lifespan of `supervisor/src/main.py`, before the app serves requests), `finish_interrupted_runs` records each run that the runs file still holds as "running" as failed, with the error "The supervisor stopped during the run" and, as `finished_at`, the time of that start: the first moment the supervisor can tell that it no longer runs. The task of each gets it as its last run and status, the run that started last for a task with more than one. The runs file and the tasks file are written once, and not at all when there is no such run (ADR-0214).
- When the supervisor stops in an orderly way, `stop_scheduler` cancels the runs that the scheduler loop started and waits for them: `run_task` records each as failed with the same error, at the time of the stop, once `run_command` has killed the processes of a command (ADR-0267). A run of Run now runs within its request, for which uvicorn waits before the lifespan ends: when Docker kills the supervisor meanwhile, the next start records the run.
- Tests: `supervisor/tests/test_custom_tasks.py`.

## Consequences

- The duration of a run recorded at the next start runs until that start, which can be later than the stop by a boot or by the time the device was off.
- A run that was cut off is not repeated; the task runs next at its schedule, or with Run now.
- The work of a task that runs in a worker thread (a backup, a snapshot, a scrub's start) cannot be cancelled: at an orderly stop it may still complete while its run is recorded as stopped.
- It relies on one supervisor process using the runs file: the image runs uvicorn with one worker.
