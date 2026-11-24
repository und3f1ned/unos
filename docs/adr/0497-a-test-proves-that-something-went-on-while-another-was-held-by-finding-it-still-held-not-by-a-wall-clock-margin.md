---
status: accepted
date: 2026-11-24
area: testing
decided-by: agent
---

# ADR-0497: A test proves that something went on while another was held by finding it still held, not by a wall-clock margin

## Context

The backend tests run in parallel workers, in several worktrees at once (ADR-0383), and the
workstation's load average reaches 100 to 200 on 10 cores while builders run side by side. Tests of
`test_command_timeouts.py` and `test_notifications.py` judged an answer by how fast it came: a request
whose command hangs answered within `TIMEOUT + 1.5` s (TIMEOUT is 1), a request turned away while the
command still hangs within `TIMEOUT`, the health check within 1 s while a mail server or a write to
the disk was held, the event loop 5 ticks of 10 ms in 0.2 s. Under that load a second stretched past
2: with three copies of the three files run at once beside 60 busy loops, each of the first 6 runs
failed, on 1 to 4 of those margins (a reset of the stats 3.1 to 3.7 s, the health check 1.0 to 1.6
s), and the same tests had failed inside `make check` runs of the batches (PROGRESS.md, 2026-11-24).
Run alone, they passed.

## Decision

A test that shows that a request, the event loop or a thread went on while something else was held
finds the held thing still held afterwards. The fakes keep that state: the held write's `ended`
event and the stalled mail server's `stalled` event of `test_notifications.py`, the hanging fake
process's `returncode` of `test_command_timeouts.py`, the Docker call still in flight of
`test_containers.py`. A hold lasts longer than every wait of the test meanwhile: 30 s (HOLD, GUARD),
90 s for the Docker call that two requests of up to 25 s each must pass, 60 s for a hanging fake
process's wait without a timeout (HANG_LIMIT), so a fault holds the test there and then fails it,
rather than its answer coming late.

That a run waited exactly one timeout, at its timeout and not until the command's end, is counted:
the fake processes list every wait with a timeout that ran out (`waits_timed_out`). That a run
answered at once, without waiting a timeout of its own, is measured against a long timeout: the run
gets LONG (10 s) and must answer within LONG / 2. Where only time can tell, as one wait of
`HOST_NAME_WAIT` against one wait per message, the two outcomes lie at least two waits apart: 5
messages, one wait of 4 s, a bound of 3 waits (12 s against 20). Waits for what comes in a moment, such as a thread's
start or a request's answer, are guards of 25 to 30 s, never the proof.

## Consequences

- Each changed check fails on the fault it is for: a turned-away run that waited its own timeout
  (13 failures), a run that waited for its process a second time (15), and the mail, the channel
  write and the alert's history write run on the event loop (5) each failed the new checks when made
  into the code by hand; they pass on the code as it is.
- A test whose fault occurs waits up to its hold (30 s) before it fails, instead of 1 to 5 s; a pass
  takes as long as before, but for the host-name wait (4 s instead of 0.5 s).
- New tests of the same kind follow this rule; `test_storage.py` and `test_scheduler_validation.py`
  still count ticks of the event loop in a fixed time and are the next to change when they fail.
