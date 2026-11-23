---
status: accepted
date: 2026-11-23
area: device
decided-by: agent
supersedes: [ADR-0314]
---

# ADR-0473: The scrub and balance checks prove what a fast disk lets them see, and size the balance by a trial

## Context

On the BPI-F3's NVMe pool (device checks of 2026-11-23) `scripts/dev/scenario.sh scrub` and
`balance` failed although UNOS behaved right. A scrub of 11.2 GiB took 18 s, and an API call
through the relay (ADR-0222) took about 2.4 s, so the scrub step saw the scrub running once,
where ADR-0314 asked for its bytes scrubbed growing over several looks. The balance check's 12
chunks were balanced within 13 to 14 s of its start (about 0.83 s a chunk), and the balance had
ended before the scrub or the second balance that it should refuse with 409, or before its status
showed the time left. Its chunks were sized for "some
seconds each on a hard disk". The owner delegated the decisions ("do as much as possible without
me", 2026-11-22, in translation); the checks must still prove on a hard disk what they proved.

## Decision

- **The scrub check** keeps ADR-0314's rules but one, restated here. `scripts/dev/scenario.sh
  scrub` (`scrub_check` of `scripts/dev/scenario.py`) works on the pool of `--pool`, or the one
  pool mounted read-write with UNOS's fstab entry. Its steps:
  - No scrub of the pool runs, and the step names its last scrub (none after a restart of the
    supervisor, ADR-0171). Nothing of an earlier check is left (the Scheduler's task names and the
    folders of the pools' base).
  - A scrub of a folder `<base>/unoscheck<6 hex digits>` that the check makes on the root
    filesystem, with nothing mounted on it, is refused with 409 "Pool <name> is not mounted at
    <path>". A disabled scheduled scrub task of that folder fails its Run now with the same text
    (the one start of ADR-0311); then the task and the folder go again.
  - Start Scrub answers running, and so does the status right after it (ADR-0313). The scrub runs
    to its end (the step below).
  - A second Start Scrub, then Cancel Scrub once btrfs has recorded that scrub, which ends aborted.
  - A disabled scheduled scrub task of the pool whose Run now ends with "Scrub started" and a scrub
    that runs; that scrub ends finished without errors. The task is deleted, and its run stays in
    the Scheduler's history.
  - On a pool with more than 32 GiB of data (`SCRUB_TO_THE_END`) the scrubs are cancelled, each
    once btrfs has recorded it, instead of waited for. The steps wait for btrfs 900 seconds at most
    (`SCRUB_WAIT`).
  - `REMOTE_POOL_FOLDER` makes and removes the folder without the read-only namespace (ADR-0221),
    and lists the base within it. It makes and removes only a name `unoscheck` and six hex digits,
    and removes only an empty folder that is no mount point.
  - The clean-up cancels a scrub of the pool that still runs, also when the status reads idle,
    deletes the check's tasks and removes the folder. The comparison covers the Scheduler's tasks
    and the folders of the base. The pool's last scrub result and the runs of the check's tasks
    stay: they are the device's history. The feature-pass skill's table names this check for
    `storage.scrub` and `storage.scrub-needs-mounted-pool`.

  The rule that changes is the step of the scrub that runs to its end. ADR-0314 asked for its
  bytes scrubbed growing over the looks. Each answer that said running is a look; its bytes
  scrubbed count only when it names this scrub's start (`last_run`). Until btrfs records a new
  scrub, about 5 seconds after its start (ADR-0313), a look may show no record or the earlier
  scrub's. The step now asks for the following.
  - The scrub ends finished without errors, with a start time other than the scrub's before it.
    Its end's rate and duration give the bytes it scrubbed (btrfs's Rate is those bytes over the
    Duration), which must be above 0, and it names the bytes to scrub (`total_bytes`).
  - The counted bytes never go back or past the bytes to scrub, and one of them is above 0 when
    there are any.
  - When two looks or more count, two of their values above 0 differ: the bytes moved while the
    scrub ran.
  - A scrub seen running `SCRUB_LOOKS` (5) times or more was counted at two looks at least. Each
    look is an API call and a pause of `SCRUB_POLL` seconds.

  So a hard disk's scrub, seen many times, must still move as before. A fast disk's scrub, counted
  once or not at all, shows its progress by that look and by its end's counters.
- **The balance check**:
  - It first makes 24 chunks of a trial, about 12% full (192 files of 128 MiB, every 8th kept),
    and runs a trial balance with the usage limit of 20%. Step 1 asks for twice their space free.
    The trial times its API calls and one device call (the unit's state over `ssh.sh`). It asks
    for the status until the supervisor's estimate rests on 8 chunks balanced since the trial's
    first look (`BALANCE_TRIAL_MOVED`), or the balance ends, and cancels it.
  - The seconds a chunk take are the supervisor's last estimate, less the second that it rounds
    its time left up: `(seconds_left - 1) / (expected - completed)`. Over 8 chunks it is off by an
    eighth of a chunk's time at most, and with as many chunks left its rounding by little. A trial
    that ends before the supervisor showed a time left fails: the disk is too fast for it (below
    about 0.2 s a chunk at the F3's 2.4 s a call).
  - The trial's files then make way for the check's chunks, at least 12. Their number is
    `BALANCE_MARGIN` (1.5) times the time that the steps spend while the balance runs, over the
    seconds a chunk. That time is 6 API calls (`BALANCE_RUNNING_API_CALLS`: the halves of the
    start, the pause, the resume and the cancel, the status right after the start, the two refused
    requests and the time left), 6 device calls (`BALANCE_RUNNING_DEVICE_CALLS`: the unit's state
    twice and the SD card's counter at the end of four steps) and two pauses of `BALANCE_POLL`.
    The pool needs twice the space of their files free, else that step fails and names what it
    needs.
  - The step of the refusals asks for 409s whose reasons say that the balance runs. A request that
    was not refused fails the step and names the balance's state then.

## Consequences

- On a hard disk (some seconds a chunk, about 1 s a call) the balance keeps 12 chunks. On the
  F3's NVMe drive it gets about 55, and its pool needs about 110 GiB free for a moment (fallocate
  reserves the space without writing it). A faster disk gets more chunks, a pool too small for them
  fails the step of the chunks.
- The trial adds a short balance: a few seconds on a fast disk, about half a minute on a hard disk
  (8 chunks), a few more calls with about 0.02 MiB of container logs each on the SD card
  (ADR-0222), and the audit entries of its start and its cancel. The trial and the check's balance
  also move the owner's chunks below 20%.
- The scrub step proves less on a disk that scrubs the pool within one look: only the end's
  counters. A pool whose scrub ends before btrfs records it (less than about 5 s of reading) also
  fails the step that cancels a recorded scrub, as before.
- The sizes rest on estimates. A disk much faster in the check's balance than in the trial, or calls
  much slower, can still end the balance early. The step of the refusals then names it, or the
  steps after it (the time left, the pause, the resume or the cancel) fail.
- As under ADR-0314, the check reads the pool's data three times on a small pool, with the SD
  writes of each step; the scrub's status file is in a tmpfs (ADR-0171). A scrub started by hand or
  by an enabled scheduled task while the check runs makes one of its starts fail with "Scrub is
  already running", and the clean-up cancels such a scrub as well. The Scheduler keeps the last 100
  runs: each run of the check adds two, and pushes the oldest out once the history is full.
- Tests: `supervisor/tests/test_dev_scenarios.py`. Its `TimedNas` is a fake NAS on its own clock,
  which scenario.py then uses. Its scrub and balance move with that clock, its time left is the
  supervisor's estimate, and btrfs may show the earlier scrub's record before the new one. Its
  `NVME` disk has the F3's measured scrub, chunk and call times, and a device call of 2 s
  (assumed); its `HARD_DISK` has the W3's scrub of ADR-0314 and an assumed 4 s a chunk and 1 s a
  call. The tests cover a scrub counted twice, once and never, looks at the earlier record, the
  balance's chunks on both disks, a pool too small for them, and the refusals of a balance that
  ended first. The defects of a scrub's progress that does not move or is never reported are
  tested too, against the fake that moves at each request; `REMOTE_POOL_FOLDER` against a
  temporary base.
