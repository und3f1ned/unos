---
status: accepted
date: 2026-11-07
area: system
decided-by: agent
---

# ADR-0247: Schedules follow the clock of the host's time zone, read from its /etc/localtime link, with fixed rules for the changes of daylight saving time

## Context

The scheduler computed its times with the naive `datetime.now()` of the supervisor's container, which runs in UTC: a task for 03:00 ran at 03:00 UTC, and the Scheduler tab showed the naive `next_run` as the browser's local time (`system.scheduler-time-zone`, ADR-0015, ADR-0023). croniter cannot simply be given zone-aware times: with `zoneinfo` it repeats and misplaces runs around a change of daylight saving time. The loop also slept 60 seconds after each pass and looked back one minute, so a pass that came late missed the tasks of a minute.

## Decision

- The schedules follow the clock of the host's time zone. `host.time_zone_name()` (`supervisor/src/host.py`) reads it on the host through `host.run_on_host`: where the `/etc/localtime` link points below a `zoneinfo` folder, as `timedatectl` reads it and `timedatectl set-timezone` writes it; a zone of the `posix/` or `right/` folder counts as the zone itself. The scheduler reads it again for each pass of its loop and each answer of the task list (`schedule_zone` in `supervisor/src/scheduler/router.py`), so a change of the zone applies within a minute; the zone's rules come from the supervisor's own time zone database, Debian's tzdata of the `python:3.11-slim` base image. When the zone cannot be read (no such link, a zone the database lacks, a command that fails), the schedules follow UTC, and the supervisor logs the reason once.
- A task's hour and minute, or its cron expression, are times on that clock, which croniter computes on naive times only. Each comes at the first instant at which the clock shows it or a later time (`_first_instant`): in the hour that the clock shows twice when daylight saving time ends, a task runs once, the first time, and an hourly task skips the second showing; a time that the clock skips when daylight saving time begins comes when the clock passes it, at the end of the skip: a task for 02:30 runs at 03:00 in central Europe, once.
- The loop (`scheduler_loop`) makes a pass a second after each minute begins and starts the enabled tasks with a time since the pass before, so that a late pass misses no task and none runs twice. It looks back at most 5 minutes (`CATCH_UP`): a clock set forward by hours (a resume from suspend, NTP after a boot without a real-time clock) does not start every task of the hours it skipped, and after a clock set back the loop goes on from the new time.
- Times in the API carry their offset. `next_run`, derived for each answer (`ScheduledTaskInfo`) and no longer stored in the tasks file, is in the host's time zone, such as `2026-11-08T03:00:00+01:00`, and `null` for a schedule without a next time. A run's `started_at` and `finished_at`, and a task's `last_run`, are in UTC, with `+00:00`. `GET /api/v1/scheduler/timezone`, for administrators as the whole router, answers `{"timezone": "Europe/Berlin"}`, or `UTC` when the zone cannot be read.
- The Scheduler tab (`webui/src/routes/system/tabs/SchedulerTab.svelte`) names the zone that the schedules follow, in the tab and as the description of the form's time fields, and shows next runs and run times in the browser's time zone.
- Tests: `supervisor/tests/test_scheduler_time_zone.py` (the loop on a fake clock over the changes of 2026 in Berlin and New York, the skipped and the repeated times, late passes, a clock set forward, a zone that cannot be read) and `webui/tests/scheduler.spec.ts`.

## Consequences

- Once a year a task for a time in the skipped hour runs at the end of the skip, and one in the repeated hour runs once. An hourly task follows the clock as well: it skips the second showing of the repeated hour, and its run of the skipped hour comes at the end of the skip, for a task at minute 0 together with its run of 03:00 (one run for both).
- Each pass and each answer of the task list runs one host command, `readlink` through nsenter: it writes nothing.
- The zone's rules are those of the supervisor's image, which may lag behind the host's tzdata for a zone whose rules changed recently, until the image is rebuilt. `supervisor/Dockerfile` does not name tzdata itself: the line would rebuild the image's package layer on the dev device's SD card for a package the base image already has (ADR-0214). An image without it would not go unnoticed: the Scheduler tab would name UTC, and the log the zone that was not found.
- Setting the zone in the web interface is `system.hostname-and-time-zone`; until then it is set on the host, with `timedatectl set-timezone`.
- A task whose time passes while the host is off or suspended does not run, as before; neither do the tasks of a clock set forward, except those of its last 5 minutes.
- A schedule that cannot be followed, whatever it raises, as in a tasks file written by hand, leaves only its own task without runs and without a next run (`due_tasks`, `get_next_run`).

## Considered options

- croniter with zone-aware start times: with `zoneinfo`, croniter 6.0 gave a daily task for 01:00 an extra run at 00:00 on the day after the change of spring, and it returned times that do not exist, such as 02:00+01:00 for 03:00+02:00. Naive times avoid relying on how a croniter version handles zones.
- The host's /etc/localtime mounted into the container: the bind mount keeps the old file after `timedatectl set-timezone` replaces the link, and naive local times keep the problems of the changes of daylight saving time.
- `timedatectl show` on the host: it starts systemd-timedated, which writes to the journal at each start, a write to the system disk every minute (ADR-0214).
- A skipped time run an hour later, read with the offset before the change as Python does, or not at all: the first runs the task at an hour that no one chose, the second loses a night's backup once a year.
