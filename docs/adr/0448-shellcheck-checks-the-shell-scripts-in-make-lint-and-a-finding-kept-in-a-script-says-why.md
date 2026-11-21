---
status: accepted
date: 2026-11-21
area: ci
decided-by: agent
---

# ADR-0448: Shellcheck checks the shell scripts in make lint, with no check turned off, and a finding kept in a script says why

## Context

`make lint` checked the shell scripts of `deploy/`, `scripts/` and `.claude/hooks/` with `bash -n`
only, which finds syntax errors, not an unquoted expansion or a source that cannot be followed. The
feature-list entry `ci.shellcheck` asks for shellcheck on those scripts in `make lint` and for CI's
failure on a finding. `make lint` runs in `make check-fast`, which the pre-commit hook and the Stop
hook run, so the tool must start fast on the workstation. The owner left the install to the agent
("do it yourself").

## Decision

- shellcheck 0.11.0, as the wheel `shellcheck-py==0.11.0.1`, which carries the binary, pinned in
  `.github/lint-requirements.in` and with the sha256 of its wheels and sdist in
  `.github/lint-requirements.txt` (ADR-0423): `make setup` installs it into `.venv-lint` beside
  ruff, mypy and pip-audit, and the CI workflow's Lint job with the same hashed install, before
  `make lint`. A local binary, not shellcheck's Docker image: the pre-commit hook needs no Docker.
- `make lint` runs `lint-shellcheck`, `$(SHELLCHECK) --format=gcc <scripts>` (`SHELLCHECK` defaults
  to `.venv-lint/bin/shellcheck`), beside ruff, mypy, `bash -n` and the JSON check, each also when
  another one fails (ADR-0190). The scripts are those of `bash -n`, found by one command of the
  Makefile (`shell_scripts`): the files of `SHELL_SCRIPT_DIRS` named `*.sh` and those whose first
  line runs sh, bash, dash or ksh (23 today). Its last line names the version that ran. The CI
  runner has a shellcheck of its own on `PATH`, so the Lint job's step sets `SHELLCHECK` to the
  pinned one, `${{ env.pythonLocation }}/bin/shellcheck`, where pip installed it, and runs
  `make lint` as before.
- Every check that shellcheck runs by default runs, down to its style level, and none is turned off
  for the repository; the optional checks (`shellcheck --list-optional`) stay off. `.shellcheckrc`
  at the root sets only `external-sources=true` and `source-path=SCRIPTDIR`, so that a script's
  `. "$(dirname "${BASH_SOURCE[0]}")/env.sh"` is followed and checked with what it defines; it is
  the only rc file of the repository (shellcheck reads the first one it finds above a script, so
  one nearer the scripts would replace it).
- A finding that stays has its directive in the script, with its reason after it:
  `# shellcheck disable=SCnnnn # <why>` before the command, or before the first command for a whole
  script; a file read from the host gets `# shellcheck source=/dev/null # <why>`.
  `scripts/dev/check.sh`, `deploy.sh`, `env.sh`, `ipv6-filter.sh`, `linux-tests.sh` and
  `bin/testparm` turn SC2016 off for the whole script: they hold the scripts they send to the
  device's shell in single quotes, for that shell to expand.
- The 45 findings of 0.11.0 with these settings were fixed without a change of behaviour where a
  fix was plain: the expansions quoted in `deploy/install.sh` (the Docker key's URL),
  `deploy/uninstall.sh` (the final backup's name, the blanks of a bracket expression) and
  `scripts/test-cycle.sh` (a test of numbers), `found=''` and the unused loop variable `_` in
  `scripts/dev/env.sh`, `UPDATE_STAGE='done'` in `deploy/update.sh`, the NUL byte of
  `uninstall.sh` read into `_`, and the secrets files of `install.sh` and `update.sh` sourced on
  lines of their own, where a source directive applies. The rest carry directives: the traps that call `on_interrupt` of `update.sh` and `stop` of
  `scripts/standin/e2e.sh` (SC2329), sed's indent of a list (SC2001), ssh's comma-separated
  methods (SC2054) and the `A && B || continue` of `scripts/setup.sh` (SC2015).
- `SHELLCHECK` or `SHELLCHECK_OPTS` from the environment records no green run (ADR-0383), and the
  Stop hook skips its run with a warning when shellcheck is missing, as for ruff and mypy.
- Tests: `test_shellcheck_checks_the_shell_scripts_with_the_settings_of_shellcheckrc` and
  `test_shellcheckrc_is_the_only_one_and_a_kept_finding_says_why` of
  `supervisor/tests/test_repo_tooling.py`, with the Makefile, Stop hook and green-run tests there;
  `test_python_checks_install_hashed_tools` and the pin tests of `test_deploy_scripts.py`.

## Consequences

- A shellcheck finding in those scripts fails `make check`, `make check-fast`, the pre-commit hook
  and the Stop hook, and the Lint job once the workflow runs again; while CI is off on GitHub
  (ADR-0230) the job's failure is shown by the tests that keep it and the Makefile in step.
- shellcheck-py publishes wheels for Linux x86_64, macOS and Windows, none for Linux on ARM: with
  wheels only (ADR-0423) `make setup` stops there until it has one, and `make lint` needs
  `SHELLCHECK=` pointing at a shellcheck of the pinned version.
- A new shellcheck can find what the old one did not: its update fixes the findings in the same
  change (docs/TESTING.md, "Updating the lint tools"). Dependabot does not move it.
- `supervisor/tests/fixtures/init-test-data.sh` and the scripts that `scripts/dev/` sends to the
  device inside strings stay outside the check; `bash -n` of `test_dev_scripts.py` still parses
  the latter.

## Considered options

- shellcheck's Docker image, pinned by digest like gitleaks (ADR-0445): every pre-commit would need
  Docker and start a container.
- `disable=SC2016` in `.shellcheckrc`: it would also hide a single-quoted expansion in `deploy/`,
  whose scripts run on users' hosts.
- A severity of warning or above: it would let pass the unquoted expansions (info) that this check
  exists to find.
- The optional checks too (`enable=all`): about 1,470 more findings in these scripts, most of them
  braces around every variable (SC2250) and `[[ ]]` for `[ ]` (SC2292), but also the commands whose
  failure `set -e` does not see (SC2310, SC2312). They are left for an entry of their own.
