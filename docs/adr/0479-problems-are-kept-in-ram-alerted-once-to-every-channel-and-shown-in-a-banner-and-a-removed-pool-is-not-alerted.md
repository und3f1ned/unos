---
status: superseded
date: 2026-11-23
area: system
decided-by: agent
supersedes: [ADR-0332]
superseded-by: ADR-0490
---

# ADR-0479: The problems of the NAS are kept in RAM by source, alerted once to every enabled channel, and shown to administrators in a banner, and a pool the administrator removed is not alerted

## Context

Superseded by ADR-0490: UNOS records the filesystems it released, so that a pool no pass saw managed and a read during a removal are not alerted either.

ADR-0332 kept the RAID monitor's alerts of a change of status for the pools UNOS does not manage. A
pool that the administrator removes (Remove Pool, or Unmount: unmounted and its fstab entry dropped)
stays on its disks and becomes such a pool: on 2026-11-23 the BPI-W3 sent "RAID Pool 'e2e_app'
UNMOUNTED ... Immediate attention may be required" (healthy to unmounted) after its removal. This
ADR restates ADR-0332 with that alert left out. ADR-0332's own context follows.

ADR-0047 proposed an event bus for the alerts and a health banner. Until now the RAID monitor alerted each change of a pool's status, the SMART monitor a rising counter, nothing reported a pool low on space, a failing disk or a missed task, and the web UI had no banner. The feature system.alert-events asks for one alert per problem through every enabled channel, webhooks included, and a banner until the problem is gone.

## Decision

- `supervisor/src/health/problems.py` keeps the current problems in RAM by source. `update(source, found)` replaces the problems of a source: a key that is new is alerted once through `health.router._deliver_alert` (every enabled channel: e-mail in its thread pool, the others through `notifications.router.send_through`); a key that is gone is dropped and nothing is sent; when it comes back it is new again. A problem has a key (`<kind>:<pool, disk path or task id>`), a severity (warning, error), a kind, a subject, an English title and detail for the alert, and the time it was first found.
- "pools", at each pass of the RAID monitor (300 s), from the same read of the host as its check (`storage.pools.pools_and_managed_names`, kept on the event loop as the check returns), for the managed pools only: not mounted, or no disk of it found (only its fstab entry is left), warning `pool_unmounted`; degraded or in error (missing devices, device errors), error `pool_degraded`; forced read-only by btrfs, error `pool_read_only` instead; mounted and 90% full, warning `pool_low_space`, 95% full an error under the same key, which is not alerted again, and it stays until the pool is below 85%, so that one that hovers at 90% is alerted once. A pool whose state cannot be read keeps its problems, and all stay while the host's fstab cannot be read.
- Scrub errors: a scrub counts what it finds in the devices' error counters, which make the pool degraded and which the RAID monitor's new-errors alert reads; no `btrfs scrub status` at each pass.
- The RAID monitor still alerts new device errors of every pool, and for the pools UNOS does not manage a change of status and a filesystem forced read-only, as before. For a managed pool the problems replace those two, but for a state that cannot be read (from healthy, once), and its new-errors alert is left out in the pass whose new `pool_degraded` or `pool_read_only` alert names the errors: one alert, not two.
- The RAID status (`raid_status.json`, `GET /health/raid-monitor/status`) keeps for each pool `managed`: whether the host's fstab had UNOS's entry of it at the check, or, when the fstab could not be read, what it kept before. A pool that was managed at the previous check and whose entry the fstab, read, no longer has, is not alerted for turning to not mounted: the administrator removed or unmounted it. A managed pool that falls off keeps its entry and is alerted once by its problem, or by the RAID monitor while the problems fail; while the fstab cannot be read, every pool that turns to not mounted is alerted. A pool found on the disks that UNOS never managed is alerted as before. Tests: `TestThePoolsOfTheRaidMonitor` of `supervisor/tests/test_alert_events.py`.
- "disks", at each pass of the SMART monitor (hourly), which keeps a verdict per disk path in RAM, never in the SMART history file: overall health FAILED, error `disk_failed`, but not for an NVMe disk whose critical warning is only its temperature's bit, which goes as it cools; the newest self-test ended with an error (ATA `passed: false`, NVMe result 5 to 7), error `disk_test_failed`. A disk that sleeps keeps its last verdict while lsblk reports the same serial at its path; a disk that is gone has none.
- "tasks", from `scheduler.router.task_check_loop` every 300 s, started and stopped with the scheduler in the lifespan (ADR-0043): an enabled task whose last status is failed, warning `task_failed`; one with a time of its schedule on the host's clock (`next_run_after`, ADR-0266) at least 10 minutes past, after its last start (a run that still runs counts) and after its `schedule_since`, warning `task_missed`: runs that fall while the supervisor is stopped are not made up. `schedule_since` is a field of the task that the server sets when the task is created, enabled again or given another schedule: in the writes of the tasks file that these make anyway, and so across a restart. The checks also count from when they saw the host's time zone change, in RAM. A task with neither a start nor a `schedule_since`, or whose schedule has no next time, misses nothing.
- `/ws/v1/events` sends administrators `{"type": "problems", "data": [...]}` right after auth_ok and, at its 2-second ticks, whenever `problems.version()` changed; regular users never get it, and a socket whose user a re-check finds no longer an administrator gets an empty list once and no more. `authenticate_websocket` returns the user with the token.
- The web UI keeps the list in `webui/src/lib/stores/problems.ts`, and `ProblemsBanner.svelte` in `+layout.svelte` shows it above every page of an administrator: errors first, in the status-error and status-warning tokens, the texts of `catalog/problems.ts` by kind with the subject, never the server's English; it goes when the list is empty.
- Tests: `supervisor/tests/test_alert_events.py`, the problems of `supervisor/tests/test_ws_auth.py`, `webui/tests/health-banner.spec.ts` and the banner's view of `webui/tests/mobile-layout.spec.ts`.

## Consequences

- The list is never written to the system disk (ADR-0214), and `schedule_since` only goes into writes made anyway. So after a restart of the supervisor each problem still there is alerted once more; before, the status alerts of the managed pools were compared with raid_status.json and not repeated. The new-errors alerts still are.
- An alert that no channel took is not sent again; the notification history records each channel's failure (ADR-0326). A recovery sends nothing.
- The "Notify on events" choices of a channel still do nothing: every enabled channel gets every alert.
- The free space of the system disk, which ADR-0047 named, is not watched.
- A managed pool's problems wait for the next pass of the RAID monitor (up to 5 minutes), a disk's for the SMART monitor (up to an hour), a task's for the next check (up to 5 minutes).
- A host command that does not answer within its timeout (a dying disk under `btrfs filesystem show`) fails the RAID monitor's whole pass, as before: that pass reports no pool problems (storage.commands-time-out-cleanly).
- A disk that comes back at another path is alerted again: its verdicts go by path.
- A managed pool whose fstab entry goes another way than through UNOS (a hand edit, a restored fstab) and that is then not mounted is not alerted either: UNOS manages a pool by its entry.
- A check that reads the mount table between Remove Pool's unmount and its write of the fstab still sees a managed pool that is not mounted and alerts it: the check takes no lock of the pools, and the window is the few host commands between the two.
- `managed` changes the status file only when a pool's entry comes or goes, a real change (ADR-0214).

## Considered options

- An event bus with subscribers (ADR-0047): more machinery than one registry that three checks update and the socket reads by its version.
- A file of the problems already alerted, so that a restart repeats none: a write on the SD card for each problem that comes or goes, for a repeat that only a restart causes.
