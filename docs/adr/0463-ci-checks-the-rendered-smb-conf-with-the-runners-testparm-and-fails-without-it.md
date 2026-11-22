---
status: accepted
date: 2026-11-22
area: ci
decided-by: agent
---

# ADR-0463: CI checks the rendered smb.conf with the runner's own testparm, and fails without it

## Context

`supervisor/tests/test_smbconf_testparm.py` checks every smb.conf that UNOS renders with Samba's
real testparm (ADR-0189), and skips where none is installed. On the workstation it takes the dev
device's testparm through `scripts/dev/bin/testparm`, which someone has to remember to put on
PATH, and CI's runner had no Samba, so the check skipped there in every run. Since the CI workflow
runs on GitHub again (ADR-0462), the Unit Tests job can run it on every pull request
(`ci.samba-in-ci`). On 2026-11-22 ubuntu-latest is Ubuntu 26.04, whose samba-common-bin is Samba
4.23.6; the dev device runs Debian trixie's Samba 4.22.

## Decision

- The Unit Tests job of `.github/workflows/test.yml` has a step `Install Samba's testparm` before
  `make test`: `sudo apt-get -o Acquire::Retries=3 update`, `sudo DEBIAN_FRONTEND=noninteractive
  apt-get -o Acquire::Retries=3 install -y --no-install-recommends samba-common-bin`,
  `sudo mkdir -p -m 0755 /run/samba` (the cache directory of the rendered `[global]`, ADR-0277,
  and the lock and pid directory of Ubuntu's Samba: testparm fails without it, and the runner's
  user cannot make it) and `testparm --version`, which writes the Samba version into the log. The
  step has no condition and, like every step, no `continue-on-error` (ADR-0190): a failed install
  fails the job.
- `make test` runs also after a failed install, unless the run was cancelled or the supervisor's
  packages failed to install (`if: ${{ !cancelled() && steps.deps.outcome == 'success' }}`), so
  that the other tests still report (ADR-0190) and the testparm tests fail.
- The package comes from the runner's Ubuntu archive, which apt checks against Ubuntu's signing
  keys, at the version that release ships; it is not pinned to a version as the Python tools are
  pinned by hash (ADR-0423).
- `test_smbconf_testparm.py` uses the first testparm on PATH. Where `CI` is `true`, as GitHub sets
  it on its runners, a missing testparm fails each test with a message that names this ADR instead
  of skipping it, so that a lost install cannot turn the check into a quiet skip. Elsewhere it still
  skips; on the workstation `scripts/dev/bin/testparm` on PATH still runs the device's testparm, and
  the device check still runs it on the device (the device-checks skill).
- `make test` in the job runs with `PYTEST_ARGS=-rfEs` in the step's environment: its summary lists
  every failure and error, as pytest's default `-rfE` does (a `-r` of its own replaces the
  default), and every skip with its place and reason, so that a skip shows in the log.
- The test stays as strict as before (ADR-0091): every line UNOS writes must be known to the
  runner's Samba. CI checks against Ubuntu's Samba, newer than the device's: it finds a line that
  a newer Samba drops or renames before the device gets that Samba. That the device's own Samba
  takes the file is still checked through the device.
- Tests: `supervisor/tests/test_repo_tooling.py` (the step, its place before `make test`, no
  condition, no `continue-on-error`, no override of `CI` or of the default shell, the condition and
  the `-rfEs` of `make test`) and `supervisor/tests/test_deploy_scripts.py`
  (`TestContinuousIntegration`: the test file run without testparm fails with `CI=true` and skips
  without it).

## Consequences

- The install adds about half a minute to each run, and a failure of Ubuntu's archive fails the job.
- When ubuntu-latest moves to another Ubuntu, or an update of Ubuntu 26.04 moves Samba to a new
  release line, and that Samba refuses a line UNOS writes, CI turns red without a change of UNOS:
  it is fixed test first like any red job (ADR-0462), not by skipping the check.
- A pass in CI does not prove that an older Samba takes the file; the two lines of ADR-0091 still
  fail this test on a Samba that lacks them.

## Considered options

- Running the Unit Tests job in a Debian trixie container, for the device's Samba: every test
  would run as root in another system, unlike on the runner that ADR-0462 describes, and the
  device's testparm is already reachable through `scripts/dev/bin/testparm`.
- Pinning samba-common-bin to a version: Ubuntu's archive drops a superseded version of an update,
  so the pin would break the job at the next security update of Samba.
