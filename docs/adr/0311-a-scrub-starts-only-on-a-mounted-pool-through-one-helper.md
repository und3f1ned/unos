---
status: accepted
date: 2026-11-09
area: storage
decided-by: agent
supersedes: [ADR-0071]
---

# ADR-0311: A scrub starts only on a pool whose btrfs filesystem is mounted, through one helper

## Context

ADR-0071 made Start Scrub return once btrfs had started the scrub and made scheduled scrubs check
that the pool is mounted, and left open that the start existed twice, in the storage module and in
the Scheduler, and that `POST /api/v1/storage/pools/{name}/scrub` did not check the mount: on a
pool that is not mounted, its path is a folder on the root filesystem, and btrfs would scrub that
filesystem if it is btrfs. The Scheduler's check asked only whether anything was mounted at the
path, in the supervisor's container: a filesystem of another type mounted there by hand, or an
unmounted btrfs subvolume of a btrfs root at the path, passed it. Working on through the features
under the owner's delegation ("work on through the features, ask no questions; I allow
everything", in translation, 2026-11-09), the agent took `storage.scrub-needs-mounted-pool`.

## Decision

- `scrub_start` in `supervisor/src/storage/pools.py` is the one start of a scrub, for the Storage
  page (`start_scrub`, which reads the status after it) and the Scheduler's scrub tasks. It first
  asks the host's mount table (`findmnt`, `disks.host_mounts`) for a btrfs filesystem mounted at
  the pool's path and raises PoolConflictError, answered 409 ("Pool <name> is not mounted at
  <path>"), without any btrfs command otherwise, also for a name that nothing is mounted for. It
  does not ask which filesystem it is, as the volume operations do (`_mounted_pool_target`):
  that runs btrfs, and a scrub covers the whole filesystem whichever subvolume is mounted.
- As ADR-0071 decided: `btrfs scrub start` sends its output to /dev/null and its error output to
  an unnamed temporary file, so it returns as soon as btrfs has started the scrub; it runs in a
  worker thread, killed at its timeout and not waited for (ADR-0290): `SCRUB_COMMAND_TIMEOUT` for
  the Storage page, `SCRUB_START_TIMEOUT` for the Scheduler, 60 seconds each. A refused start is
  a 500 with btrfs's error on the page and a failed run with it in the Scheduler.
- As ADR-0071 decided: resetting the device error counters accepts only valid pool names and runs
  in a worker thread with the same timeout.
- Tests: `supervisor/tests/test_storage.py` (TestScrubNeedsAMountedPool, the scrub endpoints),
  `supervisor/tests/test_scheduler_validation.py` (both starts through `scrub_start`, a scheduled
  scrub of a pool that is not mounted) and `supervisor/tests/test_health_monitor.py`.

## Consequences

- A Start Scrub on a pool that is not mounted, which the page hides but a stale page or a script
  can send, answers 409 and runs nothing; before, it answered 500 with btrfs's refusal, or
  scrubbed a btrfs root filesystem.
- The status and the cancel of a scrub (GET and DELETE of the same path) still run btrfs on the
  path without the check; on a btrfs root they would read or cancel the root filesystem's scrub.
- The Scheduler's snapshots still check the mount in the container (`_pool_is_mounted`).
