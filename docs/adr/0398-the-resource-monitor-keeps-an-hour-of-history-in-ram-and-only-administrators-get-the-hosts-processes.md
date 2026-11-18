---
status: accepted
date: 2026-11-18
area: system
decided-by: agent
---

# ADR-0398: The Resource Monitor keeps an hour of history in RAM, read once when its tab opens, and only administrators get the host's processes

## Context

The entry `system.resource-monitor` asks that System > Resource Monitor show CPU, memory, network
and disk I/O with their history, that time range buttons change the history shown, and that the
process list show the running processes with their CPU and memory use. The tab kept the last 30
CPU values of the stats socket while it was open, had no range buttons, and its process list named
the users of the host's processes from the container's `/etc/passwd`. The monitor router's
`/cpu`, `/memory`, `/processes`, `/history/cpu`, `/history/memory` and `/all` had no caller, and
its history held only what calls of `/cpu` and `/memory` had taken. The CPU share of the stats
message came from psutil's `cpu_percent(interval=None)`, which keeps its last reading per thread,
while each sample runs in whichever worker thread `asyncio.to_thread` gives it. The review of
`system.dashboard` found that regular users' sockets still got the process list and the names of
the host's block devices. The owner delegated the choices, asking above all that it be safe.

## Decision

- Where the history comes from: `supervisor/src/monitor/history.py` takes a sample every
  `INTERVAL_SECONDS` (10 s) in a loop of the lifespan (ADR-0043), in a worker thread, also while no
  page is open. A point holds the averages since the last sample of the host's CPU (busy share of
  `/proc/stat`'s times), network (the interfaces that System > Network lists, from
  `/proc/1/net/dev`) and physical disks (`/proc/diskstats` with `/sys/block`), read as the stats
  message reads them (`websocket/events.py`, ADR-0388): each device's growth summed over the
  devices both samples have, never negative; and the memory in use at the sample
  (`/proc/meminfo`). The first sample after a start, and one more than `MAX_GAP_SECONDS` (30 s)
  after the last, is only the base of the next. Its clock is `CLOCK_BOOTTIME`, which counts the
  time the host was suspended (UNOS offers suspend), and the monotonic clock where there is none.
- How long it is kept: `KEPT_SECONDS`, one hour, at most 360 points in a deque in RAM, and only the
  points of the last hour are answered (after samples failed, older ones can still be in the
  deque); the API copies the deque before it walks it, as the sampler appends in its thread.
  Nothing is written (ADR-0214): a restart of the supervisor or of the host starts it empty, and a
  sample that fails is logged once until one succeeds again. The stand-in starts no background loop and has no points.
- How the tab gets it: `GET /api/v1/monitor/history` (administrators: the router is in
  `_ADMIN_ONLY`; token area `monitor`) answers `interval_seconds` and the points, oldest first, each
  with `age_seconds`, its age on the sampler's clock, instead of a time of the wall
  clock, which a board without a battery-backed clock sets at boot. `MonitorTab.svelte` reads it
  once when it opens (`$lib/api/monitor.ts`) and adds each stats message of `/ws/v1/events` (every
  2 s) at the browser's time of arrival; no timer asks again (ADR-0365). Two messages are shown in
  the cards but are no point of the charts: the one the store already held when the tab opened,
  whose time is not known, and one whose `sample_seconds` is 0. That new field of the stats message
  is the time its rates and CPU shares are over, 0 for the first sample after a start or a gap,
  whose values are 0 (ADR-0388), so that such a sample draws no false drop. A failed read is shown
  with the server's message, and the live points still are.
- The time range buttons: 5 min (the range the tab opens with), 15 min and 1 hour, toggle buttons
  of a group named Time range. Each card of CPU, Memory, Network and Disk I/O draws the points of
  the range, which ends at the newest point, with a break where two points are more than 30 s
  apart; percentages from 0 to 100, rates from 0 to the range's peak. Under each chart the peak of
  the range, and each chart's accessible name names its range ("CPU over the last hour").
- What the process list shows of the host: with `pid: host` psutil lists the host's processes,
  those of the apps' containers included. The ten that use the most CPU, listed at most every
  10 s and read in full only for the ten (ADR-0374, ADR-0375), each with its PID, its name (never
  its command line, which can hold a password), its CPU share since the listing before (of one
  core, so above 100% for a process on several cores), its resident memory (`memory_bytes`) and
  its effective user as the host names it: the uid looked up in the host's `/etc/passwd`, read
  through `/proc/1/root/etc/passwd` again only when its inode, time or size changed, or the uid
  as a number when the host has no name for it or the file cannot be read (logged once). With
  `UNOS_HOST_EXEC=local` the machine's own accounts. Gone, as no page read them: each process's
  `status` and `memory_percent`, and `memory_mb`.
- Who may see it: administrators. `_for_role` of `websocket/events.py` sends a regular user's
  socket `processes` empty and `disk_io.disks` empty (the block devices' names are the Storage
  page's, an administrator's page, ADR-0360), as it does `network.interfaces` (ADR-0388); the
  rates stay, which the dashboard shows. The sample that every socket shares is not changed.
- The CPU share of the stats message, of the CPU and of each core, is the busy share of the CPU
  time since the last sample, from psutil's `cpu_times` and the sampler's own last reading, and 0
  for the first sample and after a gap, like the rates (ADR-0388).
- Removed (ADR-0207): `GET /api/v1/monitor/cpu`, `/memory`, `/processes`, `/history/cpu`,
  `/history/memory` and `/all`.
- Tests: `supervisor/tests/test_monitor_history.py` (the averages, the host's devices only, devices
  that come, go or start again, a gap, an hour kept and nothing written, a failure logged once, the
  loop in a worker thread, the last hour only, a read while the sampler appends, the boot-time
  clock on Linux, the API by role, the removed routes), `test_ws_stats.py` (the host's
  users, a changed and an unreadable passwd, the machine's own users, the CPU share in any thread
  and as psutil counts it, `sample_seconds`, the message's shape, no client asks for a removed
  route), `test_ws_auth.py` (a regular user gets
  no processes and no block devices), `test_lifespan.py`; `webui/tests/monitor.spec.ts` (the live
  values, the ranges with the peaks and the lines drawn, the messages that extend the history read
  once, a message of no time and the one held at the opening drawn as no point, a failed read, the
  stand-in's own socket and history, Russian) and the tab in `mobile-layout.spec.ts`.

## Consequences

- The tab shows the last hour as soon as it opens, also what happened while no page was open, at a
  cost of one sample of a few small procfs files every 10 s, shared by nobody else; the process
  list, the costly part of a sample (ADR-0374), stays in the stats message and is not sampled for
  the history.
- No history survives a restart, and none is longer than an hour: a longer one needs storage that
  ADR-0214 rules out on the dev device, or more RAM and a coarser second series.
- The history's points are 10 s averages and the live points 2 s values: a short peak shows
  higher in the live part of a chart than in its history.
- A process's user is right for the host; a user namespace of a container would show its mapped
  uid. Processes that started and ended between two listings are not seen.
- The device check is by hand: the stand-in has no host behind a container, so the host's users,
  its interfaces and disks and the history on the device are checked with the entry's steps.

## Considered options

- History from the socket's messages only, kept by the page: empty at every open, and the hour's
  range would need the tab open for an hour.
- A history of the samples that the stats socket takes: samples are taken only while a socket asks
  for them (ADR-0374), so the history would have gaps exactly when nobody looked.
- Sending the history over the events socket at each connection: every dashboard would get an
  hour of points it never shows.
- The command line of each process, as `ps` shows it: it can hold passwords and tokens.
