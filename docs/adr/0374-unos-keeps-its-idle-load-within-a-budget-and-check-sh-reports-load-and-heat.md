---
status: accepted
date: 2026-11-13
area: system
decided-by: agent
---

# ADR-0374: UNOS keeps its idle load within a budget, and check.sh reports load and heat

## Context

On the Banana Pi BPI-F3 (ADR-0372) the owner found the board at about half its CPU and memory
"at idle" and asked that "such load issues never happen again". Most of it was a test video that
Frigate decoded and searched for objects in a loop, now off, but UNOS added its own: every socket of
`/ws/v1/events` took a stats sample every two seconds, in the event loop, which held the API
meanwhile; on the K1 one sample took 0.4 s of CPU, 0.385 s of it for the list of 315 host
processes, so an open dashboard cost 25 to 64% of a core and more with every tab. Ten minutes
after each start, the check for leftovers of uploads walked the pool and with it Docker's data
root (ADR-0372), its image layers and the mounted root filesystems of the running containers. The
board ran at 71 C without a fan, 4 degrees below the point where its kernel slows the CPU down, and
no check showed it.

## Decision

- One stats sample serves every socket of `/ws/v1/events` until it is `STATS_INTERVAL_SECONDS`
  old, taken in a worker thread (`shared_system_stats` of `supervisor/src/websocket/events.py`),
  and the list of the host's processes in it is taken at most every
  `PROCESS_LIST_INTERVAL_SECONDS` (10 s).
- The check for leftovers of uploads asks Docker for its data root and leaves it out
  (`_docker_data_root`, `_find_upload_leftovers` of `supervisor/src/files/router.py`).
- `scripts/dev/check.sh` shows each running container's CPU averaged over five readings of
  `docker stats`, the load average and each thermal zone with its first passive trip point. A
  container of UNOS above 15% of a core on average, or a zone within 5 C of its first passive
  trip, is a problem: the check exits 1.
- Tests: `supervisor/tests/test_ws_stats.py`, `test_upload_leftovers.py` and the check.sh tests
  of `test_dev_scripts.py`.

## Consequences

- With a dashboard open, the supervisor spends about one sample's cost per interval whatever
  the number of tabs, and the process list on the dashboard is up to 10 seconds old; its CPU
  shares are averages over that time.
- A device check that runs on a hot or busy board says so; it takes about ten seconds more.
- The budget of 15% of a core and the margin of 5 C are numbers of this decision: a later change
  of what UNOS does at idle is measured against them.
