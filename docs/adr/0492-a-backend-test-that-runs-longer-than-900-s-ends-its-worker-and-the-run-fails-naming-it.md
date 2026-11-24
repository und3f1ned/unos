---
status: accepted
date: 2026-11-24
area: testing
decided-by: agent
---

# ADR-0492: A backend test that runs longer than 900 s ends its worker, and the run fails naming it

## Context

The backend tests run in parallel workers, in several worktrees at once (ADR-0383). One `make check`
stood at 99% until a builder stopped it by hand: a test waited without a deadline, and nothing said
which one. pytest-timeout is not among the packages that `make setup` installs, and packages are the
owner's (ADR-0211).

## Decision

`supervisor/pytest.ini` sets pytest's own `faulthandler_timeout = 900` and
`faulthandler_exit_on_timeout = true`. A test (its setup and teardown included) that runs longer
prints the stack of every thread of its process to the run's stderr and ends that process:
pytest-xdist reports the worker as crashed while running that test, counts it as failed, starts
another worker and finishes the run, which fails. Without workers (`TEST_WORKERS=0`) the whole run
ends there. The limit stays above the longest timeout that a test gives a single command (600 s
now), so that a slow command ends at its own test's timeout first, with that test's message.
`supervisor/tests/test_repo_tooling.py` runs a test that waits forever under the suite's
`pytest.ini` (with a limit of 5 s) and checks that the run ends naming it, and that the limit is
above every literal `timeout=` of the tests.

## Consequences

- A hang costs at most 15 minutes and names its test and the line it waits on, instead of a run that
  someone has to notice and stop.
- A test that legitimately needs longer, or a new `timeout=` above the limit, raises the limit in
  `pytest.ini` (the tooling test fails until then for the latter). The check is per command: a test
  that runs several commands near their timeouts can still reach the limit, and is ended there.
- faulthandler's watchdog is a thread of the interpreter's C code: it fires also while a test is
  blocked in a C call (a lock, a pipe, a child's wait), where an alarm handled in Python would not run.
