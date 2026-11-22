---
status: accepted
date: 2026-11-22
area: ci
decided-by: agent
---

# ADR-0450: The Audit workflow runs make setup weekly on a fresh runner, and its baseline fails the run

## Context

`make setup` (ADR-0217) was tested only with stub tools: no run showed that it bootstraps a new
checkout from the network. The entry `ci.bootstrap-in-ci` asks a CI workflow to run it on a fresh
ubuntu-latest runner, on a schedule and on demand, then `make check-fast` and `make smoke`, and to
fail when any of them fails. The CI workflow is off on GitHub because the owner found its checks
of every change "superfluous" and failing, and the weekly Audit workflow stays on (ADR-0230); the
repository is private on GitHub's Free plan (ADR-0229), whose Actions minutes are counted.

## Decision

- **Where.** The job `bootstrap` (Bootstrap (make setup)) of `.github/workflows/audit.yml`, every
  Monday at the workflow's 06:17 UTC and on demand (`workflow_dispatch`), with no push or
  pull-request trigger. The bootstrap mostly breaks without a change to the repository, as an
  advisory appears: a new runner image, a Python or Node.js release, a package index, Playwright's
  browser download. The weekly workflow that ADR-0230 keeps on is there for such drift, as for the
  image scan (ADR-0446) and the catalog checks (ADR-0403, ADR-0410). It checks no pull request and
  does not extend `test.yml`, so neither ADR-0230's gate (`make check` on the workstation) nor its
  rule that the `ci.*` entries extending that workflow wait for it changes. Not in `test.yml`: it
  is off and has no schedule, so the job would not run until the owner turns CI on. Not a workflow
  of its own: it would be one more workflow on GitHub as soon as it is merged, which ADR-0230 does
  not name.
- **What.** A checkout with the whole history (`fetch-depth: 0`), as a clone has it, for the
  tests of `make check-fast` that read it: the history of the coverage floor
  (`test_the_floor_only_rises`) and the ledger's merge base with `origin/main`
  (`supervisor/tests/test_feature_list.py`, which compares something only on a dispatch from a
  branch); without the token (`persist-credentials: false`). `actions/setup-python` with Python
  3.11 and `actions/setup-node` with Node.js 22 (the `engines` of `webui/package.json`), the
  prerequisites that `scripts/setup.sh` checks and the owner installs on a workstation, without
  their caches, so that every package and Chromium come from their indexes; then `make setup`.
  The actions have the pins of the other jobs (ADR-0423).
- **The baseline is make setup's own.** `make setup` ends with `make check-fast` and then
  `make smoke` (ADR-0217), and exits 1 when a prerequisite is missing, an install fails or either
  target fails, so the step and the run fail; its last lines name the part. The job does not run
  the two targets again in steps of their own: a second run in the same tree checks nothing new
  and would add the minutes of `make check-fast` to every run.
- **Nothing turns a failure into a pass.** The job has no `if`, `needs`, `continue-on-error`,
  `env`, `defaults` or `strategy`, and its steps no `if`, `continue-on-error`, `env`, `shell` or
  `working-directory`: `make setup` runs with the tools it installed. `timeout-minutes: 30`.
- Tests: `test_the_audit_workflow_runs_make_setup_on_a_fresh_runner` of
  `supervisor/tests/test_repo_tooling.py` reads the job (the trigger, the steps, the failure
  behaviour); `test_setup_bootstraps_a_checkout_and_a_rerun_changes_nothing`,
  `test_a_red_baseline_is_named`, `test_a_failed_install_fails_the_setup_before_the_baseline` and
  `test_setup_stops_before_changing_anything_when_a_prerequisite_is_missing` run
  `scripts/setup.sh` with stubs.

## Consequences

- The Audit workflow gets one more job of at most 30 minutes a run, at most about 2 hours a month
  of the Free plan's included minutes (2,000 a month for a private repository) for the weekly
  runs, beside its five other jobs; it checks no pull request. A dispatch runs all six jobs.
- A failed bootstrap fails the Audit workflow's run, which takes the owner's time like a failed
  audit (ADR-0218).
- Only GitHub can show that the job passes: the entry's `passes` stays false until it has run
  there, from a dispatch of the pushed branch (`workflow_dispatch` on its ref) or, once `main` has
  the job, the Monday schedule. `make check-fast` runs there on Linux, on the runner's filesystem
  and as its user, where no workstation runs it, the tests that skip on macOS included (ADR-0230
  left those to the dev device's RAM): a test that fails only there fails the run too.
- The runner is no bare Ubuntu: its image already has git, make, openssl, Docker and most of
  Chromium's system libraries, so a missing `python3.11-venv` or system library of a new machine
  does not show there.
- Chromium is installed but not started: the baseline starts no browser, so the system libraries
  of `playwright install-deps` are neither installed nor checked.
- ADR-0217's consequence that CI does not run `make setup` no longer holds; its decision stands.

## Considered options

- Steps of their own for `make check-fast` and `make smoke` after `make setup`: the same checks
  twice in one run. With one step, the Actions page shows a failed install and a red baseline
  alike as the one failed step; the log's last lines tell them apart.
- setup-python's and setup-node's caches for pip and npm: a shorter run, but no longer the
  bootstrap of a new machine.
- A job in `test.yml`, or a workflow of its own: see Where.
