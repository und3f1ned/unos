---
status: superseded
superseded-by: ADR-0473
date: 2026-11-09
area: device
decided-by: agent
---

# ADR-0314: The scrub scenario check scrubs the pool, cancels a scrub and runs a scheduled one

## Context

Superseded by ADR-0473: the step of the scrub that runs to its end proves the progress that a fast disk lets it see, its looks and the end's total.

The device checks of `storage.scrub` and `storage.scrub-needs-mounted-pool` were steps checked by
hand through the API on 2026-11-09, with a script of that session (PROGRESS.md, Log); ADR-0304
makes a check that is repeated an area of `scripts/dev/scenario.py`, and the review of that record
asked for it. A scrub changes the pool's last scrub result, which no check can put back, and runs
as long as reading the pool's data takes: on the dev device's pool data, 1.6 GiB, 18 to 19
seconds. Working on through the features under the owner's delegation ("work on through the
features, ask no questions; I allow everything", in translation, 2026-11-09), the agent made it.

## Decision

- `scripts/dev/scenario.sh scrub` (`scrub_check` of `scripts/dev/scenario.py`) works on the pool
  of `--pool`, or the one pool mounted read-write with UNOS's fstab entry, in these steps: no scrub
  of the pool runs, and the step names its last scrub (none after a restart of the supervisor,
  ADR-0171); nothing of an earlier check is left (the Scheduler's task names and the folders of
  the pools' base); a scrub of a folder `<base>/unoscheck<6 hex digits>` that the check makes on
  the root filesystem, with nothing mounted on it, is refused with 409 "Pool <name> is not
  mounted at <path>", and a disabled scheduled scrub task of that folder fails its Run now with
  the same text, the one start of ADR-0311, then the task and the folder go again; Start Scrub
  answers running, and so does the status right after it (ADR-0313); the status, its bytes
  scrubbed growing, until the scrub ends, finished without errors and with its start time; a
  second Start Scrub, then Cancel Scrub once btrfs has recorded that scrub, which ends aborted; a
  disabled scheduled scrub task of the pool whose Run now ends with "Scrub started" and a scrub
  that runs; that scrub's end, finished without errors; the task deleted, its run left in the
  Scheduler's history. On a pool with more than 32 GiB of data (`SCRUB_TO_THE_END`), the scrubs
  are cancelled, each once btrfs has recorded it, instead of waited for; the steps wait for btrfs
  900 seconds at most (`SCRUB_WAIT`).
- `REMOTE_POOL_FOLDER` makes and removes the folder without the read-only namespace (ADR-0221),
  and lists the base within it; it makes and removes only a name `unoscheck` and six hex digits,
  and removes only an empty folder that is no mount point. The clean-up cancels a scrub of the
  pool that still runs, also when the status reads idle, deletes the check's tasks and removes the
  folder; the comparison covers the Scheduler's tasks and the folders of the base. The pool's last
  scrub result and the runs of the check's tasks stay: they are the device's history.
- The feature-pass skill's table names this check for `storage.scrub` and
  `storage.scrub-needs-mounted-pool`, which leave its row of checks by hand. Tests:
  `supervisor/tests/test_dev_scenarios.py`, against the fake NAS, whose scrub btrfs records a
  moment after its start and whose cancel before that leaves no record, and `REMOTE_POOL_FOLDER`
  against a temporary base.

## Consequences

- The check reads the pool's data three times on a small pool: on the dev device about a minute,
  with the SD writes of each step; the scrub's status file is in a tmpfs (ADR-0171). A pool whose
  scrub ends before btrfs records it, with less than about 0.7 GiB of data at the dev device's
  rate, fails the cancel step; at 32 GiB the wait needs a rate of at least 37 MiB/s.
- A scrub started by hand or by an enabled scheduled task while the check runs makes one of its
  starts fail with "Scrub is already running", and the clean-up cancels such a scrub as well.
- The Scheduler keeps the last 100 runs: each run of the check adds two, and pushes the oldest
  out once the history is full.
