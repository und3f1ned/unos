---
status: accepted
date: 2026-11-08
area: system
decided-by: agent
supersedes: [ADR-0264]
---

# ADR-0266: Schedules follow the host's time zone as last read from its /etc/localtime link, with fixed rules for the changes of daylight saving time

## Context

ADR-0264 had the schedules follow the clock of the host's time zone, read on the host at each pass of the scheduler loop, and UTC whenever a read failed, with the reason logged once in the supervisor's life (`system.scheduler-time-zone`). One read that failed for a moment (a timeout during heavy I/O on the SD card, no new process under memory pressure, nsenter refused) checked that pass's minute on the clock of UTC: in Berlin, the pass at 03:00 missed the night's 03:00 backup and ran the task of 02:00 a second time, and a later failure with the same reason left no trace in the log.

## Decision

- The schedules follow the clock of the host's time zone. `host.time_zone_name()` (`supervisor/src/host.py`) reads it on the host through `host.run_on_host`: where the `/etc/localtime` link points below a `zoneinfo` folder, as `timedatectl` reads it and `timedatectl set-timezone` writes it; a zone of the `posix/` or `right/` folder counts as the zone itself. The scheduler reads it again for each pass of its loop and each answer of the task list (`schedule_zone` in `supervisor/src/scheduler/router.py`), so a change of the zone applies within a minute; the zone's rules come from the supervisor's own time zone database, Debian's tzdata of the `python:3.11-slim` base image.
- When the host answers but names no zone that the supervisor can use (no such link, a link elsewhere, a zone the database lacks), the schedules follow UTC. When the host cannot be asked (an OSError, a timeout, or nsenter failing with its message, which `time_zone_name` raises as `HostCommandError`, since readlink without `-v` reports nothing), they follow the zone that the host named last, kept in memory; UTC only before the host's first answer since the supervisor started. Each problem is logged once when it begins, not at every pass while it lasts, and again when it comes back after the zone was read.
- A task's hour and minute, or its cron expression, are times on that clock, which croniter computes on naive times only. Each comes at the first instant at which the clock shows it or a later time (`_first_instant`): in the hour that the clock shows twice when daylight saving time ends, a task runs once, the first time, and an hourly task skips the second showing; a time that the clock skips when daylight saving time begins comes when the clock passes it, at the end of the skip: a task for 02:30 runs at 03:00 in central Europe, once.
- The loop (`scheduler_loop`) makes a pass a second after each minute begins and starts the enabled tasks with a time since the pass before, so that a late pass misses no task and none runs twice. It looks back at most 5 minutes (`CATCH_UP`): a clock set forward by hours (a resume from suspend, NTP after a boot without a real-time clock) does not start every task of the hours it skipped, and after a clock set back the loop goes on from the new time.
- Times in the API carry their offset. `next_run`, derived for each answer (`ScheduledTaskInfo`) and not stored in the tasks file, is in the host's time zone, such as `2026-11-08T03:00:00+01:00`, and `null` for a schedule without a next time. A run's `started_at` and `finished_at`, and a task's `last_run`, are in UTC, with `+00:00`. `GET /api/v1/scheduler/timezone`, for administrators as the whole router, answers the zone that the schedules follow, such as `{"timezone": "Europe/Berlin"}`.
- The Scheduler tab (`webui/src/routes/system/tabs/SchedulerTab.svelte`) names the zone that the schedules follow, in the tab and as the description of the form's time fields, and shows next runs and run times in the browser's time zone, which it says.
- Tests: `supervisor/tests/test_scheduler_time_zone.py` (the loop on a fake clock over the changes of 2026 in Berlin and New York, the skipped and the repeated times, late passes, a clock set forward, a zone that the host does not name, a pass that cannot ask the host, the log of each failure) and `webui/tests/scheduler.spec.ts`.

## Consequences

- Once a year a task for a time in the skipped hour runs at the end of the skip, and one in the repeated hour runs once. An hourly task follows the clock as well: it skips the second showing of the repeated hour, and its run of the skipped hour comes at the end of the skip, for a task at minute 0 together with its run of 03:00 (one run for both).
- Each pass and each answer of the task list runs one host command, `readlink` through nsenter: it writes nothing.
- A change of the zone that comes while the host cannot be asked applies at the next pass that can. A failure before the host's first answer, at the supervisor's start, still checks that pass on the clock of UTC.
- The zone's rules are those of the supervisor's image, which may lag behind the host's tzdata for a zone whose rules changed recently, until the image is rebuilt. `supervisor/Dockerfile` does not name tzdata itself: the line would rebuild the image's package layer on the dev device's SD card for a package the base image already has (ADR-0214). An image without it would not go unnoticed: the Scheduler tab would name UTC, and the log the zone that was not found.
- Setting the zone in the web interface is `system.hostname-and-time-zone`; until then it is set on the host, with `timedatectl set-timezone`.
- A task whose time passes while the host is off or suspended does not run, as before; neither do the tasks of a clock set forward, except those of its last 5 minutes.
- A schedule that cannot be followed, whatever it raises, as in a tasks file written by hand, leaves only its own task without runs and without a next run (`due_tasks`, `get_next_run`).

## Considered options

- UTC for a pass whose read fails (ADR-0264): one failed read moves the runs of that minute by the zone's offset.
- A pass that cannot read the zone is skipped and its minutes are checked by the next one: the tasks run a minute late, and a failure that lasts longer than `CATCH_UP` loses their runs.
- croniter with zone-aware start times: with `zoneinfo`, croniter 6.0 gave a daily task for 01:00 an extra run at 00:00 on the day after the change of spring, and it returned times that do not exist, such as 02:00+01:00 for 03:00+02:00. Naive times avoid relying on how a croniter version handles zones.
- The host's /etc/localtime mounted into the container: the bind mount keeps the old file after `timedatectl set-timezone` replaces the link, and naive local times keep the problems of the changes of daylight saving time.
- `timedatectl show` on the host: it starts systemd-timedated, which writes to the journal at each start, a write to the system disk every minute (ADR-0214).
- A skipped time run an hour later, read with the offset before the change as Python does, or not at all: the first runs the task at an hour that no one chose, the second loses a night's backup once a year.
