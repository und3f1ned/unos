---
status: accepted
date: 2026-11-17
area: testing
decided-by: agent
---

# ADR-0383: Backend tests run in parallel with paths of their own, and a green tree skips the pre-commit check

## Context

On the workstation (10 cores) `make check` took about 12.5 minutes, 719 s of it the backend tests
(8048 passed) in one pytest process, and `make check-fast` about 5.5 minutes. `supervisor/tests/conftest.py`
pointed the supervisor's smb.conf, NFS export file and pools' folder at fixed paths
(`/tmp/test_smb.conf`, `/tmp/test_exports`, `/tmp/test_pools`), so every backend run on the
machine waited for one lock, `/tmp/unos-backend-tests.lock` (ADR-0210, ADR-0216): with several
worktrees at work, runs queued behind each other for minutes, and one that waited 30 minutes gave
up. The pre-commit hook then ran `make check-fast` again for a tree that `make check` had just
passed. The ledger entries `testing.per-test-temp-paths` and `ci.pre-commit-reuses-green-run` ask
for runs that go in parallel and for a commit that reuses a green run.

## Decision

- **Paths of their own.** `conftest.py` makes a temporary folder per pytest process when pytest
  loads it (`tempfile.mkdtemp(prefix="unos-tests-")`, in `TMPDIR`), points `UNOS_SMB_CONF_PATH`,
  `UNOS_NFS_EXPORTS_PATH` and `UNOS_POOLS_BASE_PATH` into it, and removes it in
  `pytest_unconfigure`, or at the process's exit when pytest stops before (a usage error). Every
  run, and every worker of a parallel run, has its own; every other file of a test stays in its
  `tmp_path`. One folder per process, not per test: the settings are read once, at import, also
  into module constants (`src/files/router.py`'s `BASE_PATH`), and no test of the suite writes to
  those paths. Tested by `supervisor/tests/test_test_paths.py` (two pytest runs at once, each with
  its own folder, removed at its end).
- **Parallel runs.** `make test` and `make test-fast` run pytest with `-n $(TEST_WORKERS)` of
  pytest-xdist (pinned in `supervisor/requirements-dev.txt`), `auto` by default: one worker per
  CPU core, with xdist's default distribution of single tests to free workers. `TEST_WORKERS=0`
  runs in pytest's own process. `make smoke` runs in one process: its tests take a few seconds,
  less than the workers take to start. A Python without pytest-xdist runs the tests in one process
  and make says so in one line, so a checkout whose venv predates this ADR still passes until
  `make setup` installs it. The device-checks skill runs `tests/test_smbconf_testparm.py` with
  `TEST_WORKERS=0`, one testparm at a time on the device.
- **Tests fixed for parallel runs**, without weakening an assertion: the ids of a parametrized
  test that held a secret drawn at random in each process (`test_totp_once.py`: the workers
  collected different tests), and a test that sent SIGINT to its own process while the main
  thread blocked it (`test_dev_scenarios.py`): an xdist worker has threads of its own, which take
  a signal sent to the process, so the test sends it to the main thread, which gets it in
  `scripts/dev/scenario.py`, a process without other threads. `test_dev_scenarios.py`'s fixture
  without pauses now also sets the offsite check's poll (`OFFSITE_POLL`, `OFFSITE_WAIT`), as it
  did for the other checks: the fake's copies end after one request, and the 2-second polls had
  added about 150 s of sleep to one test. A test whose fake balance pauses 0.25 s after the step's
  wait ended failed once when two whole runs went at once beside other worktrees' runs: its
  margins are now a second on each side. A test of `test_dev_scripts.py` whose holder of the
  device lock worked for 2 s, which a busy machine used up before the test looked, now lets the
  holder end when it has looked. No test needed to run alone.
- **No lock.** The test targets take no machine-wide lock, and `TEST_LOCK` is gone: nothing that
  the suite uses is shared between runs any more. This ends the serialization of backend runs of
  ADR-0210 and the wait for the lock of ADR-0216; their other rules stand.
- **The green run.** `scripts/green-run.py` holds the state of a working tree that the Stop hook
  used (ADR-0216): one SHA-256 of HEAD, git status, the diff staged and not (git's own diff,
  without external drivers or textconv) and the names and contents of the untracked files,
  recorded in the git-ignored `tmp/stop-check/green`; a git command that fails gives no state.
  `make check` and `make check-fast` record the state they started in when they pass, if the tree
  is still in it at their end and the run checked everything: not a dry run or a run that ignores
  errors (`make -n`, `-t`, `-q`, `-i`, `-e`, also through `MAKEFLAGS`), no variable on the
  command line but the tools (`PYTHON`, `RUFF`, `NPM`, `PIP_AUDIT`), `TEST_WORKERS` as one word
  and `E2E_ARGS`, no `TESTS` or `PYTEST_ARGS` from the environment, no `PYTEST_ADDOPTS`, and no
  `MAKE` from the environment. When they fail they remove the record. The Stop hook and
  `scripts/git-hooks/pre-commit` skip `make check-fast` for a tree in the recorded state; the
  pre-commit hook says in one line whether it ran it or skipped it, and reads the state with
  git's own index, not the `index.lock` of `git commit -a`. A tool given on the command line is
  trusted: a stub of it, like a record written by hand, is a deliberate act.
- **Nothing else skips the checks.** The guard (ADR-0211) still refuses `--no-verify`, and its
  rule skip-hooks also refuses a green run recorded by hand: `scripts/green-run.py record` and a
  write to `tmp/stop-check/green` (a redirection, cp, mv, ln, install, rsync, tee, dd of=). Only
  make records it; the Stop hook no longer writes it.
- Tested in `supervisor/tests/test_repo_tooling.py` (real git commits through the hook, the real
  Makefile with stub tools, every case above that records nothing, the Stop hook with the real
  Makefile) and `supervisor/tests/test_agent_guard.py`.

## Consequences

- Measured on the workstation, beside other worktrees' runs: the backend tests take about
  2 minutes instead of 12 (112 s instead of 719 s), `make check` about 2.2 minutes instead of
  12.5, `make check-fast` about 1 minute instead of 5.5, `make smoke` 2 s, and a commit of a tree
  that just passed spends a fraction of a second in the pre-commit hook instead of a run of
  `make check-fast`. No run waits for another worktree's; a runner that waited for the lock took
  4 to 15 minutes more before.
- Several worktrees that run the tests at once share the CPU: each run is slower than alone, but
  none waits, and none gives up after 30 minutes.
- A new test must not write to a fixed path outside its `tmp_path`, must give every parametrized
  case the same id in every process, and must pass in a worker with threads of its own; the
  workers report a difference in what they collected as an error.
- The recorded state includes the index: `git add` after `make check` makes the next commit run
  `make check-fast`. Staging before `make check`, or `git commit -a`, reuses the green run.
- A change made and undone while `make check` runs is not seen: the tree is in the same state at
  both ends of the run.
- A pytest process that is killed (SIGKILL, or SIGTERM, which pytest does not handle) leaves its
  folder in `TMPDIR`.
- Every worktree's `supervisor/.venv` needs pytest-xdist: `make setup` installs it, as it installs
  every change of `requirements-dev.txt`; until then make runs the tests in one process.

## Considered options

- Paths per test, the settings patched in an autouse fixture: module constants copied from the
  settings at import would point elsewhere, and no test uses those paths.
- A runner of its own that splits the test files over N pytest processes: more code to keep, and
  a worse balance than xdist's single tests; xdist installed from PyPI.
- Grouping the guard's table tests in one worker (`--dist loadgroup`), so that their module
  fixture runs once: measured, it saved no time.
- Keeping `TEST_LOCK` as an option: nothing needs it.
- A state without the index, so that `git add` after `make check` keeps the green run: the hooks
  would trust a state that the Stop hook's fingerprint never covered; kept as it was.
