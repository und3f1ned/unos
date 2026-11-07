---
status: accepted
date: 2026-11-08
area: system
decided-by: agent
supersedes: [ADR-0248]
---

# ADR-0250: A custom task's run records the command's output and exit status, and the command ends within 24 hours with every process of its session

## Context

ADR-0248 ran the command of a custom task in a process group of its own, which was killed when the shell exited and at the time limit (`system.custom-command-tasks`). `timeout` (without `--foreground`) and job control (`set -m`) move processes into a process group of their own in the same session, so `timeout 30h job`, or `timeout 2h job &`, went on running after its run had been recorded as stopped or finished, against what the Scheduler tab and the user guide say. The runs file, rewritten on the system disk at each run (ADR-0214), was also written with JSON's `\u` escapes: the 16 KiB of Cyrillic output that a run keeps took about three times as much there.

## Decision

- `run_command` (`supervisor/src/scheduler/router.py`) runs the command with `/bin/sh -c`, as root in the supervisor's container, with standard input from `/dev/null` and standard output and error into one pipe, which keeps the order in which they were written. The event loop waits for it (asyncio's subprocess), not a worker thread.
- A run (`TaskRun`) records `exit_code`, the command's exit status, and `output`, what it wrote: the last 16 KiB (`MAX_OUTPUT_BYTES`), after the line "[Only the last 16 KiB of the output are kept.]" when more came before, and from the first whole character on. The exit status 0 is a success; any other is a failure with the error "The command exited with status N". `exit_code` is null for the other kinds of task and for a command that did not exit by itself.
- The command runs in a session of its own (`start_new_session`), which its shell leads. When the shell exits, every process left in that session is killed (SIGKILL), also those in process groups of their own: `_end_session` finds them by their session id (`os.getsid` over `psutil.pids()`, in a worker thread; a zombie counts as ended) and kills them again until none is left, as one may start another before it is killed, or until 5 seconds (`STOP_WAIT`) passed. Only `setsid` leaves the session. A command still running after 24 hours (`COMMAND_TIMEOUT`) is killed with its session in the same way, and its run fails with "The command did not end within 24 hours and was stopped"; a command killed by a signal fails with "The command was ended by SIGTERM" (the signal's name). Then the run waits up to 5 seconds for the shell, and as long again for the rest of the output, since a process that left the session may still hold the pipe.
- The command is checked again before each run, as when it is saved (`_command_problem`: present, at most 4096 characters, no NUL character), so that a tasks file written by hand runs no command that the API would refuse.
- Run ids get a random suffix, as task ids do (ADR-0172): two runs that started in the same microsecond kept one record.
- The runs file (`RUNS_FILE`) is written as UTF-8 text, without JSON's `\u` escapes (`_save_runs`); a lone surrogate, which UTF-8 cannot hold (the bytes of a file name that are not UTF-8, in an error), is written as its JSON escape.
- The Scheduler tab (`webui/src/routes/system/tabs/SchedulerTab.svelte`) shows "Exit status N" with each run of a custom task and its output under View output, and its form says what a run records and when a command is stopped.
- Tests: `supervisor/tests/test_custom_tasks.py`, `supervisor/tests/test_scheduler_validation.py` and `webui/tests/scheduler.spec.ts`.

## Consequences

- A command that should keep running, such as a server, does not belong in a custom task: what it starts in the background is killed when it returns, unless it leaves the session (`setsid`). A container or a service of the host is the place for it.
- A copy that takes longer than a day is stopped; a next run can continue it, as with rsync.
- Runs of a task can overlap, as with cron and as backups may (ADR-0172): a run starts at its time, or with Run now, also while the one before has not ended, such as the next run of a daily task whose command hangs until its time limit. A command that must not run twice at once guards itself, for example with `flock`.
- The end of each run reads `/proc` for every process of the host (`pid: host`): a few milliseconds, in a worker thread.
- The runs file holds about 16 KiB per run for text output, about 1.6 MiB for its 100 runs, in any language. JSON escapes control characters: a newline takes 2 bytes, and output of other control characters takes up to 6 bytes for each of its bytes (`\u0000` for a NUL byte), about 9.6 MiB for 100 such runs.
- The output is read as UTF-8: bytes that are not UTF-8 show as the replacement character.

## Considered options

- The process group of the shell (ADR-0248): `timeout` and job control leave it, and their processes outlived the run.
- A cgroup of its own for each run: it would hold a process that called `setsid` too, but the supervisor does not manage the cgroups of its container, and the host's systemd (`systemd-run --scope`) would run the command outside the container that ADR-0044 runs it in.
