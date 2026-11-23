---
status: accepted
date: 2026-11-23
area: device
decided-by: agent
supersedes: [ADR-0473]
---

# ADR-0485: The balance check pauses its balance right after its start, and the offsite check needs a pool off the system disk

## Context

On the BPI-F3's NVMe pool `scripts/dev/scenario.sh balance` still failed now and then after
ADR-0477: the balance of ADR-0473, sized by a trial, ended before its pause ("the pause answered
idle, and the balance is idle (finished)"). Sizing cannot make a race safe: a step that needs the
balance running loses whenever the disk is faster, or the calls slower, than the trial measured.
`scenario.sh offsite` fails on the F3 by design: Docker's data root, and with it the supervisor's
data volume, is on the pool's disk (ADR-0372), and ADR-0337 refuses a copy's folder on that disk.
The owner delegated the decisions ("do as much as possible without me", 2026-11-22, in translation).

## Decision

- **The scrub check** stays as ADR-0473 had it (after ADR-0314), restated here. `scenario.sh
  scrub` (`scrub_check` of `scripts/dev/scenario.py`) works on the pool of `--pool`, or the one pool
  mounted read-write with UNOS's fstab entry:
  - No scrub of the pool runs, and the step names its last scrub; nothing of an earlier check is
    left (the Scheduler's task names and the folders of the pools' base).
  - A scrub of a folder `<base>/unoscheck<6 hex digits>` on the root filesystem, with nothing
    mounted on it, is refused with 409 "Pool <name> is not mounted at <path>", and a disabled
    scheduled scrub task of it fails its Run now with the same text (ADR-0311).
  - Start Scrub answers running, and so does the status right after it (ADR-0313). The scrub ends
    finished without errors, with a start other than the scrub's before it; its end's rate and
    duration give the bytes it scrubbed, above 0. Each answer that said running and names this
    scrub's start (`last_run`) is a counted look: its bytes scrubbed never go back or past the bytes
    to scrub, one is above 0 when there are any, two counted looks show two different values above
    0, and a scrub seen running `SCRUB_LOOKS` (5) times was counted at two looks at least.
  - A second Start Scrub, then Cancel Scrub once btrfs has recorded it, which ends aborted; a
    disabled scheduled scrub task whose Run now starts a scrub that ends finished; the task is
    deleted and its run stays in the history. Over 32 GiB of data (`SCRUB_TO_THE_END`) the scrubs
    are cancelled once recorded; btrfs is waited for 900 s at most (`SCRUB_WAIT`).
  - `REMOTE_POOL_FOLDER` makes and removes only `unoscheck` and six hex digits below the base,
    without the read-only namespace (ADR-0221), and removes only an empty folder that is no mount
    point. The clean-up cancels a scrub that still runs, deletes the check's tasks and removes the
    folder.
- **The balance check** needs no balance to run longer than one request after an answer that
  showed it running, and decides each step from the balance's own state. No trial: its chunks are
  `BALLAST_CHUNKS` (24) data chunks about 12% full (192 files of 128 MiB made with fallocate, every
  8th kept), and step 1 asks for twice their space free (48 GiB). Its steps:
  1. The pool is mounted read-write, idle, without a scrub, and has the room; 2. the baseline;
     3. a balance of a folder of the base with nothing mounted is refused with 409; 4. the chunks.
  5. Start Balance answers running, and Pause Balance is the next request: the status says
     paused, the unit `unos-balance-<pool>` has ended, and its output (`balances.LOG`,
     `/run/unos-balance-<pool>.log`, which the start empties), changed since the check read its
     time of change before the start (`REMOTE_BALANCE_OUTPUT`, to the nanosecond), says "balance
     paused by user": btrfs ran in the unit, not an earlier balance's output. A balance that ended
     before the pause fails the step, naming its end.
  6. The paused balance's status shows the chunks it balanced, whole numbers with
     0 <= completed <= expected, and a second balance is refused with 409 "A balance of pool <name>
     is paused". A paused balance does not refuse a scrub (`pools.BALANCE_RUNS`), so the scrub's
     refusal waits for the resume.
  7. Resume Balance answers running, and a scrub, the next request, is refused with 409 "A balance
     of pool <name> runs". A scrub that starts beside a balance that is still there fails the step.
  8. The status of the resumed balance, read every `BALANCE_POLL` seconds until a look finds a
     chunk balanced since the resume, shows the time left then. A balance seen running at
     `BALANCE_LOOKS` (2) looks without one, then ended, fails: its progress did not grow (the 20 or
     so chunks left cannot all be balanced between two looks on a disk that balanced none during
     the two before).
  9. Cancel Balance ends it, and the status says cancelled; a cancel that found it running and
     did not end it fails.
  10. The unit has ended, and its output, changed since step 5 (the resume empties it), says how
     btrfs ended there ("balance canceled by user", or "Done, had to relocate").
  When the balance's state shows that it had finished by the request of step 7, 8 or 9 (the scrub
  started and the look after it found the balance idle and finished, a look or the cancel found
  it so), that step is a SKIP that says so: the device did not let the check judge it. A scrub
  beside a balance still there, progress that did not grow over two looks, a pause or a cancel
  that answered running and did not act, and btrfs outside the unit fail. A scrub that a defect
  let start while the balance ran, on a disk fast enough that the balance has ended by the next
  look, is a SKIP too: the API shows no order of the two. The running balance's progress and time
  left are read once while it runs, after the resume (step 8); the paused state gives its chunks
  balanced (step 6).
- **The offsite check** first finds, after picking the pool, whether the pool is off the system
  disk, as `offsite/service.py` calls the whole disks of the root filesystem (that of `/mnt`) and
  of the supervisor's data volume (its mount of `/data`, which `docker inspect` names). It finds
  the whole disks of those and of each pool mounted read-write as that file counts them
  (`REMOTE_DISKS`: all devices of a btrfs filesystem from `/sys/fs/btrfs/<uuid>/devices`, the
  source of another mount, and the disks below each device by `lsblk --inverse`). When the pool's
  disks meet those of the root filesystem or the data volume, the step fails if another pool is
  off them (name it with `--pool`), and is otherwise a SKIP that says so, and the check ends there:
  no folder of a pool could take the copy. Elsewhere, such as on the BPI-W3 (data volume on the
  eMMC, ADR-0470; pool on the hard disk), the check runs as before.

## Consequences

- On the F3 (0.83 s a chunk, 2.4 s an API call) the pause finds about 3 of 24 chunks balanced,
  and the resumed balance runs through steps 7 to 9 with about half its chunks to spare; on the
  W3's hard disk it moves a few chunks before the cancel. Only a disk that balances 24 chunks within
  one API call fails step 5. A disk a little slower than that skips steps 7 to 9 rather than fail.
- A SKIP lets `scenario.sh all` pass without seeing that step on that run; the line names why.
  After a SKIP of step 9 the pool's last balance result is finished, not cancelled, and after one
  of step 7 the clean-up's cancel leaves the pool's last scrub result aborted. The balance moves at
  most 24 chunks of 128 MiB, and the owner's chunks below 20% as before.
- The balance check depends on the path of the unit's output: `test_dev_scenarios.py` compares
  `BALANCE_OUTPUT` with `balances.LOG`. The check reads it in the read-only namespace, in RAM.
- On the F3, `scenario.sh offsite` proves nothing of the off-device copy: a disk off the pool,
  mounted below `/mnt/pools`, would let it run there.
- The ledger entry `testing.scenario-fast-disks` states these steps in place of the trial's.
- Tests: `supervisor/tests/test_dev_scenarios.py`, with `TimedNas` (ADR-0473's fake on its own
  clock) at the F3's and the W3's times and at faster disks (0.5, 0.25, 0.15 and 0.08 s a chunk),
  the defects of each balance step (also at the F3's times), an earlier balance's output left in
  the unit's file, a board whose data volume or root filesystem is on the pool's disk, one with a
  second pool off it, and `REMOTE_DISKS` and `REMOTE_BALANCE_OUTPUT` against stand-ins.
