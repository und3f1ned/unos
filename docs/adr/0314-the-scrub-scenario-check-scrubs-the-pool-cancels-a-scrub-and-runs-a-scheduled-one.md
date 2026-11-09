---
status: accepted
date: 2026-11-09
area: device
decided-by: agent
---

# ADR-0314: The scrub scenario check scrubs the pool, cancels a scrub and runs a scheduled one

## Context

The device checks of `storage.scrub` and `storage.scrub-needs-mounted-pool` were steps checked by
hand through the API on 2026-11-09, with a script of that session (PROGRESS.md, Log); ADR-0304
makes a check that is repeated an area of `scripts/dev/scenario.py`, and the review of that record
asked for it. A scrub changes the pool's last scrub result, which no check can put back, and runs
as long as reading the pool's data takes: on the dev device's pool data, 1.6 GiB, 18 to 19
seconds. Working on through the features under the owner's delegation ("work on through the
features, ask no questions; I allow everything", in translation, 2026-11-09), the agent made it.

## Decision

- `scripts/dev/scenario.sh scrub` (`scrub_check` of `scripts/dev/scenario.py`) works on the pool
  of `--pool`, or the one pool mounted read-write with UNOS's fstab entry, in these steps:
  no scrub of the pool runs; nothing of an earlier check is left (the Scheduler's task names and
  the folders of the pools' base); a scrub of a folder `<base>/unoscheck<6 hex digits>` that the
  check makes on the root filesystem, with nothing mounted on it, is refused with 409 "Pool
  <name> is not mounted at <path>" (ADR-0311), and the folder goes again; Start Scrub answers
  running, and so does the status right after it (ADR-0313); the status until the scrub ends,
  finished without errors and with its start time; a second Start Scrub, then Cancel Scrub once
  btrfs has recorded that scrub, which ends aborted; a disabled scheduled scrub task of the pool
  whose Run now ends with "Scrub started" and a scrub that runs; that scrub's end, finished
  without errors; the task deleted, its run left in the Scheduler's history. On a pool with more
  than 64 GiB of data (`SCRUB_TO_THE_END`), the scrubs are cancelled instead of waited for.
- `REMOTE_POOL_FOLDER` makes and removes the folder without the read-only namespace (ADR-0221),
  only for a name `unoscheck` and six hex digits, and removes it only when it is an empty folder
  and no mount point. The clean-up cancels a scrub of the pool that still runs, deletes the
  check's task and removes the folder; the comparison covers the Scheduler's tasks and the
  folders of the base. The pool's last scrub result and the runs of the check's task stay: they
  are the device's history.
- The feature-pass skill's table names this check for `storage.scrub` and
  `storage.scrub-needs-mounted-pool`, which leave its row of checks by hand. Tests:
  `supervisor/tests/test_dev_scenarios.py`, against the fake NAS, whose scrub btrfs records a
  moment after its start, and `REMOTE_POOL_FOLDER` against a temporary base.

## Consequences

- The check reads the pool's data three times on a small pool: on the dev device about a minute,
  with the SD writes of each step; the scrub's status file is in a tmpfs (ADR-0171).
- A scrub started by hand while the check runs makes one of its starts fail with "Scrub is
  already running", and the step names it.
