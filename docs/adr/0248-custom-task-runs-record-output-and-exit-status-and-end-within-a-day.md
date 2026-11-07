---
status: superseded
date: 2026-11-07
area: system
decided-by: agent
superseded-by: ADR-0250
---

# ADR-0248: A custom task's run records the command's output and exit status, and the command ends within 24 hours with what it started

## Context

Superseded by ADR-0250: the command ends with every process of its session, also those that `timeout` and job control put in process groups of their own, and the runs file is UTF-8 text.

A custom task ran its command with `subprocess.run(shell=True)` in a worker thread and kept its standard output, or its error output only when there was no standard output, and no exit status (`system.custom-command-tasks`, ADR-0044). Without a time limit a command that hung kept its run "running" for good and held a worker thread, a process that it left in the background held the output pipe open and kept the run waiting, and an output of any size went into `/data/task_runs.json`, which is rewritten on the system disk at each run (ADR-0214).

## Decision

- `run_command` (`supervisor/src/scheduler/router.py`) runs the command with `/bin/sh -c`, as before as root in the supervisor's container, with standard input from `/dev/null` and standard output and error into one pipe, which keeps the order in which they were written. The event loop waits for it (asyncio's subprocess), not a worker thread.
- A run (`TaskRun`) records `exit_code`, the command's exit status, and `output`, what it wrote: the last 16 KiB (`MAX_OUTPUT_BYTES`), after the line "[Only the last 16 KiB of the output are kept.]" when more came before, and from the first whole character on. The exit status 0 is a success; any other is a failure with the error "The command exited with status N". `exit_code` is null for the other kinds of task and for a command that did not exit by itself.
- The command runs in a process group of its own. When its shell exits, the processes that it left running in that group are killed (SIGKILL): a task's processes end with its run, and the run ends when the command does. A command still running after 24 hours (`COMMAND_TIMEOUT`) is killed with its group, and its run fails with "The command did not end within 24 hours and was stopped"; a command killed by a signal fails with "The command was ended by SIGTERM" (the signal's name). After a kill, the run waits up to 5 seconds (`STOP_WAIT`) for the processes to end and as long again for the rest of their output, since a process that left the group may still hold the pipe.
- The command is checked again before each run, as when it is saved (`_command_problem`: present, at most 4096 characters, no NUL character), so that a tasks file written by hand runs no command that the API would refuse.
- Run ids get a random suffix, as task ids do (ADR-0172): two runs that started in the same microsecond kept one record.
- The Scheduler tab (`webui/src/routes/system/tabs/SchedulerTab.svelte`) shows "Exit status N" with each run of a custom task and its output under View output, and its form says what a run records and when a command is stopped.
- Tests: `supervisor/tests/test_custom_tasks.py`, `supervisor/tests/test_scheduler_validation.py` and `webui/tests/scheduler.spec.ts`.

## Consequences

- A command that should keep running, such as a server, does not belong in a custom task: what it starts in the background is killed when it returns, unless it leaves the process group (setsid). A container or a service of the host is the place for it.
- A copy that takes longer than a day is stopped; a next run can continue it, as with rsync.
- Runs of a task can overlap, as with cron and as backups may (ADR-0172): a run starts at its time, or with Run now, also while the one before has not ended, such as the next run of a daily task whose command hangs until its time limit. A command that must not run twice at once guards itself, for example with `flock`.
- The runs file holds at most about 16 KiB of output per run, about 1.6 MiB for its 100 runs.
- The output is read as UTF-8: bytes that are not UTF-8 show as the replacement character.
