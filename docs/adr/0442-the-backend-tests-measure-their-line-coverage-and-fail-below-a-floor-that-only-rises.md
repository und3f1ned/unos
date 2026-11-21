---
status: accepted
date: 2026-11-21
area: testing
decided-by: agent
---

# ADR-0442: The backend tests measure their line coverage and fail below a floor that only rises

## Context

`make check` ran every backend test but said nothing of how much of `supervisor/src` they run;
the ledger entry `testing.coverage-report` asks that `make test` print the backend's line coverage
and fail below an agreed floor. The February 2026 roadmap had aimed at 80 percent, and the floor
was left to the owner, who answered: "decide yourself". The run must keep the parallel workers of
pytest-xdist (ADR-0383) and leave `make test-fast` and the pre-commit hook as fast as they are.

## Decision

- **What is measured.** `make test`, when `TESTS` names every test (`tests/`, its default, as
  `make check` runs it), adds pytest-cov's `--cov=src --cov-report=`: the line coverage
  (statements, not branches) of `supervisor/src`, measured in each worker and added up by the run.
  It prints the total, `Required test coverage of 91.0% reached. Total coverage: 91.40%` on the day
  of this ADR, and no table of files. `PYTEST_ARGS` do not turn it off, so that no option skips the
  floor in a run of every test: a `-k` over every test fails the floor, and a part is named in
  `TESTS`. A part so named measures nothing and says so in one line, since its coverage is not the
  backend's; `make test-fast`, `make smoke` and so the pre-commit and Stop hooks measure nothing.
- **Its data.** Each run writes its data as `.coverage.run<pid>` in `supervisor/` (its workers'
  as `.coverage.run<pid>.*`), so that a second run in the same checkout neither erases nor adds
  up its data (ADR-0383 lets runs go at once), and renames it to the git-ignored
  `supervisor/.coverage` at its end, which `python -m coverage report` reads for the files.
- **The floor.** `fail_under` of `supervisor/.coveragerc`, its one place: no `--cov-fail-under`
  elsewhere, and the file sets nothing but the floor and its precision. A total below it fails
  `make test`, and so `make check`, also when every test passed. The total is compared at two
  decimals (`precision = 2`), so 85.71% does not round up to a floor of 86. The first floor is 91:
  the measure of 91.40% of 2026-11-21 on macOS, rounded down to a whole percent.
- **Measured on macOS.** The tests that run only on Linux skip on macOS, where `make check` runs
  (ADR-0230), and their code counts as not run there: the floor is the macOS measure. On Linux,
  CI's Unit Tests job measures more; `scripts/dev/linux-tests.sh` runs pytest without pytest-cov,
  and the coverage tests skip in its chroot, which has neither git nor make.
- **It only rises.** `tests/test_repo_tooling.py` refuses a floor in the working tree below one
  committed before it in HEAD's history, of this branch or of a branch merged into it, read as
  coverage reads the file. It also refuses what would raise the measure the same way: another
  setting in `.coveragerc` (an omitted file, an excluded line) and coverage's `pragma: no cover` in
  `supervisor/src`. A change that raises the coverage may raise the floor to its new measure on
  macOS, rounded down. Lowering the floor, or excluding code from the measure, takes a new ADR, of
  the owner's, that supersedes this one and changes that test.
- **Pins.** pytest-cov 7.1.0 and coverage 7.17.2 in `supervisor/requirements-dev.txt`, which
  `make setup` and CI install; `scripts/dev/linux-tests.sh` bundles neither.

Tested by `supervisor/tests/test_coverage_floor.py` (the command of `make test`, with two workers
that each run one test, on a throwaway package below and above a floor in the repository's
`.coveragerc`, beside another run's data) and `supervisor/tests/test_repo_tooling.py` (the wiring
of the targets, the floor's one place and settings, the rule that it only rises, and
`.gitignore`).

## Consequences

- The tracer of coverage runs in every worker, so `make test` and `make check` take longer; the
  ledger entry's notes give the times measured before and after.
- A change that adds code without tests can fail `make check` with every test green; the answer is
  tests, not a lower floor.
- The floor guards the total only: a module may lose coverage while another gains it.
- `make test PYTEST_ARGS='-k ...'` over every test fails by its coverage; the part goes into `TESTS`.

## Considered options

- 80 percent, the roadmap's aim: 11 points below today's measure, a floor that would let the
  coverage fall that far unnoticed.
- The floor as a variable of the Makefile: a command line could lower it for a run without a sign
  in the tree (`PYTEST_ARGS='--cov-fail-under=0'` can too, but records no green run, ADR-0383).
- Measuring only when `PYTEST_ARGS` are empty: `make check PYTEST_ARGS=-x` would pass below the
  floor.
- Coverage in `make test-fast` too: its few tests measure little of `src`, and the pre-commit hook
  would get slower.
