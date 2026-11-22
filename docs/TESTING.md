# UNOS Testing Guide

How a change to UNOS is verified, from the repository root. `make help` lists the targets; the
`Makefile` runs the same commands as the CI workflow (ADR-0216), which runs on GitHub (ADR-0462).

## The Three Verification Levels

This table is the one place that defines the levels; CONTRIBUTING.md and the pull request
template point here.

| Level | Command | Where it runs | What it proves |
|-------|---------|---------------|----------------|
| 1. The gate | `make check` | this machine, then CI on GitHub (ADR-0462) | lint, every backend test against fake hosts (the Linux-only ones skip elsewhere) with the line coverage of `supervisor/src` at or above its floor, svelte-check, the check of the web UI's texts and the production build |
| 2. E2E against the local stand-in | `make e2e-local` | this machine | the user flows of the web UI against the real supervisor and the built web UI |
| 3. The dev device | `scripts/dev/` with the deploy-device and device-checks skills | the dev device | what only a real NAS host has: disks, btrfs, Samba, Docker, a reboot |

- Level 1 must pass before a change is done; with CI off, whoever merges runs it. Two fast
  parts of it run on their own: `make check-fast` (lint and the convention tests; the pre-commit
  hook and the Stop hook of Claude Code sessions run it) and `make smoke` (in seconds: the app
  starts and serves health, the first-run setup and a login; the baseline of a session).
- Level 2 follows every change to `webui/` or to an API the web UI calls (ADR-0433): the change
  runs the specs of its area (those of the pages and APIs it changes, plus every spec citing a
  test id, label or catalog key it changed), and the full `make e2e-local` runs once per batch,
  on the batch branch after the merges, before the deploy and before the batch reaches main; a
  change to `webui/src/lib/components/ui/`, `webui/src/app.css` or
  `webui/src/routes/+layout.svelte` runs the full suite itself. E2E specs run only
  against local targets, never against the device (ADR-0215): the stand-in, which is the
  default, or the compose test stack of
  [The Container-Level Route](#the-container-level-route-linux-with-docker). They create and
  delete users, shares and backups, and restore databases. `webui/playwright.config.ts` refuses
  any other target, and so does the agents' guard (`.claude/hooks/guard.py`).
- Level 3 is for host-level changes, as [GLOSSARY.md](../GLOSSARY.md) defines them: they are done
  only after a check on the dev device and a reboot check (ADR-0018). The owner asks for the
  deploy; every step reports what it wrote to the device's SD card (ADR-0214).

On Linux with Docker there is also a container-level route, the deviceless test stack of
`deploy/docker-compose.test.yml` and `scripts/test-cycle.sh`; see
[The Container-Level Route](#the-container-level-route-linux-with-docker).

## Setting Up: make setup

`make setup` (`scripts/setup.sh`) bootstraps a checkout in one idempotent command (ADR-0217).
It first checks the prerequisites (git, make, Python 3.11, Node.js 22 or later with npm) and
stops before changing anything when one is missing, then sets up what is missing or outdated:

1. `supervisor/.venv`: Python 3.11 with `supervisor/requirements-dev.txt` (the runtime
   dependencies, pytest and the other test tools, and the type stubs of the runtime dependencies
   that publish none, which mypy reads there).
2. `.venv-lint`: ruff, mypy, shellcheck (the wheel `shellcheck-py`, which carries the binary) and
   pip-audit from `.github/lint-requirements.txt`, with their hashes
   (`pip install --require-hashes --only-binary=:all: -r .github/lint-requirements.txt`).
3. `webui/node_modules`: `npm ci` from `webui/package-lock.json` (a symbolic link there is left
   as it is).
4. Playwright's Chromium and WebKit, the versions that `webui/package-lock.json` pins: every E2E
   spec runs in Chromium, and `webui/tests/form-fields.spec.ts` in WebKit, Safari's engine, too.
   Playwright downloads them only for the systems its release knows: `@playwright/test` is pinned
   exactly, to a release that knows the Ubuntu of GitHub's ubuntu-latest (`UBUNTU_LATEST` of
   `supervisor/tests/test_repo_tooling.py`; 1.57.0 did not know 26.04, ADR-0461).
5. The git hooks: `git config core.hooksPath scripts/git-hooks`.
6. `dev.env`: a copy of `dev.env.example` with mode 600 when it is missing, except in a linked
   worktree whose main checkout has one: the scripts in `scripts/dev` use that one (ADR-0210). An
   existing `dev.env` is never replaced or shown; only its mode is set to 600.
7. The baseline: `make check-fast`, then `make smoke`. It ends with "baseline green", or names
   the target that failed before any work started.

Steps 1 and 2 take wheels only (`--only-binary=:all:`), so that pip runs the build code of no
package but nut2, which publishes only its source: step 1 lets it come as its sdist
(`--no-binary=nut2`, after `--only-binary=:all:`, which would clear it; ADR-0423), and pip builds
it with the newest setuptools from the index. The CI jobs, `supervisor/Dockerfile.test` and the
audit do the same. A machine for which a pinned package has no wheel cannot be set up: an Intel
Mac, since cryptography 50 publishes none for macOS x86_64. The supervisor's image is left to
pip's default, which with the exact pins of `supervisor/requirements.txt` takes a wheel wherever
the pinned version has one: its pip layer, about a hundred MB of writes on the dev device's SD
card (ADR-0214), stays as it is.

A step that is up to date is skipped: each environment remembers the SHA-256 of the files it was
installed from, so a rerun installs only what changed. Steps 1-4 need the network.
`scripts/setup.sh --check` only reports what is missing or outdated; it changes nothing and needs
no network. Python 3.11 is found as `python3.11` on `PATH`, or set `PYTHON311=/path/to/python`.
The `bootstrap` job of the weekly Audit workflow runs `make setup` on a fresh runner
([Scheduled Maintenance](#scheduled-maintenance)).

The make targets use these environments when they exist, and `python3`, `ruff`, `mypy`,
`shellcheck`, `pip-audit` and `npm` from `PATH` otherwise; override them with `PYTHON=`, `RUFF=`,
`MYPY=`, `SHELLCHECK=`, `PIP_AUDIT=` and `NPM=`.

## Make Targets

| Target | Runs |
|--------|------|
| `make check` | `lint`, `test` and `frontend`: what CI's Unit Tests, Lint and Frontend jobs run, without the network audits |
| `make check-fast` | `lint` and `test-fast`: the pre-commit hook and the Stop hook |
| `make test` | all backend tests (`supervisor/tests`), in parallel worker processes (below), with the checks of the web UI's code that run under node: `test_i18n.py`, `test_webui_design_system.py`, `test_webui_formats.py` (`webui/src/lib/format.ts` and `time.ts` in English and Russian), `test_webui_polling.py` (`webui/scripts/polling-check.mjs`: no `setInterval` reaches the API, ADR-0365) and `test_webui_runes.py` (`webui/scripts/runes-check.mjs`: every component in runes mode, ADR-0427); when `TESTS` names every test, the line coverage of `supervisor/src` against its floor ([Coverage](#coverage)); `TESTS=tests/test_auth.py` for one file, `TESTS=tests/test_shares.py PYTEST_ARGS='-x -k smb'` for pytest options, `TEST_WORKERS=4` for the number of workers |
| `make lint` | `ruff check supervisor scripts .claude/hooks` with the rules of `ruff.toml` (pyflakes findings, syntax errors and the rules that find bugs, ADR-0425, in the supervisor and in the repository's own tooling), `mypy --python-executable <PYTHON> supervisor/src` with the settings of `mypy.ini`, against the packages of the supervisor's environment (ADR-0447), `bash -n` and `shellcheck --format=gcc` with the settings of `.shellcheckrc` (ADR-0448) on every shell script of `deploy/`, `scripts/` and `.claude/hooks/` (the files named `*.sh` and those whose first line runs sh, bash, dash or ksh), and the JSON syntax of `feature-list.json` and `.claude/settings.json` |
| `make test-fast` | the convention tests, in parallel worker processes: `test_adrs.py`, `test_feature_list.py`, `test_agent_guard.py`, `test_repo_tooling.py` (with the secret scan below), `test_dev_scripts.py`, `test_dev_scenarios.py`, `test_standin.py` |
| `make smoke` | backend tests that start the app and serve requests, in a few seconds: the startup and `/api/v1/health` (`test_lifespan.py`), the first-run setup, a login through the proxy layer and the refusal of anonymous requests |
| `make frontend` | `npm run check` (svelte-kit sync, then svelte-check), `npm run runes` (`scripts/runes-check.mjs`: every component in runes mode, without the syntax of Svelte 4, ADR-0427), `npm run design` (`scripts/design-check.mjs`: no raw button, bare field or raw palette colour in a page, ADR-0400), `npm run i18n` (`scripts/i18n-check.mjs`: the texts of every `.svelte` file that `scripts/i18n-untranslated.txt` does not list come from the catalogs in English and Russian; texts put together at run time and `.ts` modules are for review, ADR-0287), `npm run build`, `npm run lint` (ESLint over `src/` with `eslint.config.js`: the recommended rules of ESLint, typescript-eslint and eslint-plugin-svelte, ADR-0449) and `npm run format-check` (Prettier's check of `src/` with `prettier.config.js`; `npm run format` writes it) in `webui/` |
| `make e2e-local` | the Playwright specs against the local stand-in (`scripts/standin/e2e.sh`); `E2E_ARGS='tests/auth.spec.ts'` for a part |
| `make catalog-platforms` | `scripts/catalog-platforms.py check`: the platforms recorded for the catalog's images (`supervisor/src/apps/image_platforms.json`) are those the registries publish now; `record` rewrites the file (needs the network, ADR-0403) |
| `make catalog-tags` | `scripts/catalog-platforms.py tags`: the tag of every pinned image of the catalog still names the index it is pinned to, asked with HEAD requests only (needs the network, ADR-0410) |
| `make audit` | `scripts/audit-python.sh` (pip-audit of `supervisor/requirements.txt` as resolved in the audit environment, below) and `npm audit --omit=dev --audit-level=high` in `webui/` (needs the network) |
| `make secret-scan` | `scripts/secret-scan.sh`: gitleaks, pinned by the digest of its image, over every commit of the history, with `.gitleaks.toml`, and `scripts/published-passwords.py` (needs Docker, and the network for the first pull; below, ADR-0445) |
| `make image-scan` | `scripts/image-scan.sh`: builds both images and scans each with trivy, pinned by the digest of its image, for fixable HIGH and CRITICAL vulnerabilities (needs Docker and the network; below, ADR-0446) |
| `make setup` | `scripts/setup.sh` (needs the network) |

`make check`, `make check-fast`, `make lint` and `make frontend` run every part, also after one
has failed, and fail at the end, naming the parts that failed (ADR-0190): one run reports every
problem. Each part starts with a line `== make <target>: <part>`.

`make test` and `make test-fast` run the tests in `TEST_WORKERS` processes of pytest-xdist
(`auto`, the default: one per CPU core; `0`: in pytest's own process), and `make smoke` in one
process, where workers would take longer to start than its tests (ADR-0383). Each pytest process,
a run's or a worker's, keeps the paths that the supervisor reads from its settings (smb.conf, the
NFS export file, the pools' folder) in a temporary folder of its own, which it removes at its end
(`supervisor/tests/conftest.py`); every other file of a test is in its `tmp_path`. So runs in
several worktrees go at once, and no lock serializes them. A test must not write to a fixed path
outside its `tmp_path`, and must give every parametrized case the same id in every process (the
workers compare what they collected). A Python without pytest-xdist runs the tests in one
process, with a line that says so; `make setup` installs it. On the workstation (10 cores) the
backend tests take about 2 minutes instead of 12, `make check` about 2.2 minutes and
`make check-fast` about 1 minute.

## Commits and Git Hooks

Commit messages are Conventional Commits in English, whose first line, type and scope included,
has at most 72 characters: [ADR-0209](adr/0209-conventional-commits-in-english.md) holds the
rule and [scripts/check-commit-msg.py](../scripts/check-commit-msg.py) checks it (its docstring
has the details). The hooks in `scripts/git-hooks/` (installed by `make setup`) run before every
commit:

- `pre-commit` runs `make check-fast`, unless the commit holds all of the working tree in which
  `make check` or `make check-fast` last passed: both record its state when they pass with
  `make setup`'s tools and every check (`scripts/green-run.py`, in the git-ignored
  `tmp/stop-check/green`), and the commit's index then has no change from the working tree and
  no file is untracked. It says in one line which it did (ADR-0383). The state covers HEAD, the
  index and the working tree: stage everything before `make check`, or commit with
  `git commit -a`, and the commit reuses the green run. Only make records it: the guard refuses
  `scripts/green-run.py record` and writes into `tmp/stop-check`, and the settings deny the
  Write and Edit tools that folder.
- `commit-msg` runs `scripts/check-commit-msg.py` on the message.

CI's Commit Messages job runs the same script over the commits of a pull request (ADR-0462).
To check a branch before pushing it:

```bash
python3 scripts/check-commit-msg.py --range origin/main..HEAD
```

## Checks in Claude Code Sessions

`.claude/settings.json` gives every Claude Code session that starts in the repository root two
hooks:

- The guard, `.claude/hooks/guard.py` (ADR-0211), checks each command of the Bash tool and of the
  desktop app's terminal tool before it runs, and blocks the risky ones with the rule and what to
  do instead. Its docstring lists the rules and what it cannot see;
  `supervisor/tests/test_agent_guard.py` holds the cases. Each denial adds a line (the time, the
  rule and the tool, never the command) to the git-ignored `tmp/guard/denials.log`. It guards the
  device backups of `tmp/dev/backup` as it guards `dev.env`, and from a linked worktree also the
  main checkout's `dev.env` and backups, which the worktree's scripts use. For a push without a
  refspec it asks git where the push would go (`@{push}`, the upstream, `push.default`). A
  command that the Bash tool runs in the background must be one call of `timeout` with a
  deadline of at most 2 hours, with loops and pipelines inside `timeout <seconds> bash -c '...'`
  (ADR-0335): the session wakes only when it ends.
- The same settings deny the Read tool every `dev.env` and every file below a `tmp/dev/backup`,
  by paths from the filesystem's root (`Read(//**/dev.env)`), so that a worktree's session cannot
  read the main checkout's either, and every tool of the Desktop Commander plugin.
- The Stop hook, `.claude/hooks/stop-check.sh` (ADR-0216), runs `make check-fast` when the agent
  is about to finish and the working tree has changed since the last green run of `make check` or
  `make check-fast` (`scripts/green-run.py`, ADR-0383). A failure sends
  the agent back to work with the last lines of the failing part; the whole output is in
  `tmp/stop-check/check-fast.log`. When the tools of `make setup` are missing it lets the session
  end with a warning that Claude Code shows to the user (the `systemMessage` of its answer).

## Backend Tests

```bash
make test                                        # all of supervisor/tests
make test TESTS=tests/test_auth.py               # one file
make test TESTS=tests/test_shares.py PYTEST_ARGS='-x -k reconcile'
make test-fast                                   # the convention tests
make smoke                                       # seconds: startup, health, setup, login
```

### Coverage

`make test`, when `TESTS` names every test (`tests/`, the default, as `make check` runs it),
measures the line coverage of `supervisor/src` with pytest-cov (ADR-0442): each worker of
pytest-xdist measures its tests and the run adds them up. It prints the total and fails below
the floor, also when every test passed, in this form (the floor, then the total):

```
Required test coverage of <floor>.0% reached. Total coverage: <total>%
```

`PYTEST_ARGS` do not turn it off: `make test PYTEST_ARGS='-k smb'` runs a part of the tests
against the floor and fails, so name a part in `TESTS`. A part named in `TESTS` measures nothing
and says so in one line, and neither do `make test-fast`, `make smoke` and the hooks. `make test` prints no table of files; after a run,
`cd supervisor && .venv/bin/python -m coverage report --skip-covered --sort=cover` reads the
git-ignored `supervisor/.coverage` for them. Each run writes its data under a name of its own,
`.coverage.run<pid>` (and its workers' `.coverage.run<pid>.*`), and renames it to `.coverage` at
its end, so two runs in one checkout neither erase nor add up each other's data.

The floor is `fail_under` of `supervisor/.coveragerc`, its one place, compared at two decimals; the
file sets nothing else, and no line of `supervisor/src` carries coverage's `pragma: no cover`
(`supervisor/tests/test_repo_tooling.py`): an omitted file or an excluded line would raise the
measure as a lower floor would. The floor is a whole percent and only rises: the same test file, in
`make test-fast`, refuses a floor below one committed before it, on the branch or on a branch merged
into it. A change that raises the coverage may raise the floor to the new measure, rounded down;
lowering it, or excluding code from the measure, takes a new ADR of the owner's that supersedes
ADR-0442. Measure on macOS, where `make check` runs: the Linux-only tests skip there, so the code
they run counts as not run, and Linux (CI's Unit Tests job) measures more.
`scripts/dev/linux-tests.sh` runs pytest without pytest-cov, and the coverage tests skip there.

### Database Migrations

The schema of unos.db is the Alembic revisions of `supervisor/src/migrations/versions/` (ADR-0444),
and `supervisor/AGENTS.md` says how to add one. `supervisor/tests/test_migrations.py` fails while
the models differ from a database at the head (the autogenerate diff, with types and server
defaults, and each table's clauses against create_all's, for the CHECK constraints and collations
that autogenerate leaves out) or the baseline's file changed, and runs every revision on a database
made by the one before it, from the baseline. It also starts the supervisor on a new database, on
one at the baseline and on one that create_all made before the migrations (the dev device's, also
with a table of an older layout), and checks that a failed upgrade changes nothing, that a start at
the head writes nothing and that a database of an unknown revision stops the start;
`test_backup_restore.py` checks that a restore upgrades the backup's database before the swap.
Most other tests build their databases with `Base.metadata.create_all`, which gives the head's
schema while the diff is empty.

### The Secret Scan

`supervisor/tests/test_repo_tooling.py`, part of `make test-fast`, checks the files that
`git ls-files --cached --others --exclude-standard` lists, what `git add -A` would commit:

- No env file (`dev.env`, another `*.env`, `.env.local`) is listed; such files are never read.
- No text file holds a secret-shaped literal: `sshpass -p` with a value, `--password=` (and
  `--token=`, `--secret=`, ...) with a value, `user:password@` in a URL, `curl -u user:password`,
  an `Authorization` header with a literal credential, a JWT, an assignment of a literal to a
  secret's name (`..._PASSWORD`, `..._PASS`, `..._TOKEN`, `..._SECRET`, `..._API_KEY`,
  `..._PRIVATE_KEY`, `..._ACCESS_KEY`, `..._AUTHKEY`: the guard's rule), the same names as keys
  of a YAML or JSON mapping in `*.yml`, `*.yaml` and `*.json`, and PEM or PGP private keys. A value
  counts when it is a literal of four or more characters (a quoted one may hold spaces), not a
  variable, a placeholder (`<...>`, `YOUR_...`, the word `password`) or a YAML scalar.
- No text file holds one of the two passwords that CLAUDE.md once published: the test keeps
  their SHA-256 digests, never the values, and compares the digest of every token of six or more
  characters.

A finding names the file, the line and the shape, never the value. Test values of these shapes
are in the scan's allowlist, each with the files that may hold it, and an entry that no file
needs any more fails the scan; a real secret belongs in `dev.env` (ADR-0212).

### The Secret Scan of the History

The scan above sees the files of the working tree; a secret that was committed once stays in the
history, which is never rewritten (ADR-0208). `make secret-scan` (`scripts/secret-scan.sh`) and the
Secret Scan job of the CI workflow run two checks over every commit that HEAD reaches; both run,
and either fails the scan (ADR-0445):

- gitleaks, in the image `ghcr.io/gitleaks/gitleaks:v8.30.1` pinned by the digest of its index
  (linux/amd64 and linux/arm64), without the network, capabilities or a writable root, with the
  repository's git folder (a linked worktree's common one) and the two files it reads, all
  read-only. It reads the commits only: files that are not committed are not scanned. A merge is
  read as `git log --remerge-diff` shows it, what its resolution changed against the merge that git
  would have made; git writes those merges' objects into a tmpfs, not into the repository.
- Its rules are gitleaks' defaults and the two of `.gitleaks.toml` that they miss: a password on
  sshpass's command line, as the old CLAUDE.md wrote the device's, and a literal assigned to a name
  whose last part ends in one of the guard's secret words (`..._PASSWORD`, `SSHPASS`, `TOKEN`,
  `..._API_KEY`, ...), as `dev.env` holds them. Inline `gitleaks:allow` comments do not count.
- Secrets are redacted, and a finding is printed as its rule, file, commit and line only
  (`scripts/secret-scan.tmpl`). gitleaks logs git's errors and still answers "no leaks", with no
  commit scanned for a repository it cannot read: an `ERR` line in its log, or no commit scanned,
  fails the scan, and so does a shallow clone.
- The allowlist of `.gitleaks.toml` names a commit and the paths of its findings, never a value,
  each block with its reason: the two passwords that CLAUDE.md published, rotated (ADR-0226),
  where it published them, and in 16 other commits values that are no credential: test values,
  placeholders and the alphabet of the CHAP secrets that UNOS generates. A finding of a merge
  names a file with a conflict as gitleaks reads it, with git's `b/` before the path.
- `scripts/published-passwords.py`: no commit adds a line that holds one of the two passwords that
  CLAUDE.md once published, found by their SHA-256 as in the scan of the working tree, but in the
  commits and paths of its `ALLOWED`, which added them before they were rotated. gitleaks' rules do
  not find a bare password in code or prose, as the old E2E specs held the administrator's.

A new finding is fixed in the next commit: a secret goes into `dev.env` and is rotated
(ADR-0212); a test value or a placeholder gets another value, or a block by commit and path with
its reason. The gitleaks pin moves by hand, in the monthly pass "The pinned check tools" of
[maintenance.md](maintenance.md): Dependabot does not read the script.

### Test Configuration

The `conftest.py` sets up the test environment:

```python
# Environment variables for testing
os.environ["UNOS_DB_PATH"] = ":memory:"  # In-memory database
# Random per run: secrets this repository published (such as "test-secret-key")
# are refused at startup.
os.environ["UNOS_JWT_SECRET"] = secrets.token_hex(32)
# A temporary folder of this pytest process alone, removed at its end (ADR-0383).
_RUN_DIR = tempfile.mkdtemp(prefix="unos-tests-")
os.environ["UNOS_POOLS_BASE_PATH"] = os.path.join(_RUN_DIR, "pools")
# The supervisor's data directory, read once at its first import (ADR-0419): no test reaches /data,
# and none writes into it (read-only): a test moves the paths it writes with monkeypatch.
os.environ["UNOS_DATA_DIR"] = os.path.join(_RUN_DIR, "data")
os.mkdir(os.environ["UNOS_DATA_DIR"], 0o555)
```

### Faking the Host

Commands that run on the host (lsblk, findmnt, mkfs.btrfs, mount, ...) go
through `src/host.py`. Tests answer them with a fake host (ADR-0189): the in-memory `FakeHost` of
`tests/test_pool_persistence.py`, or `FakeSambaHost` of `tests/samba_fake.py` for Samba;
`install_fake_host` also blocks real subprocesses and Docker, and only counts the apply of smb.conf
that the storage endpoints run after a pool change (`FakeHost.smb_applies`, ADR-0248), which
`tests/test_smb_integration.py` runs with both fakes (`PoolsAndSambaHost`). btrfs scrubs and
smartctl run in the container through `host.run_in_container`, which the fake hosts refuse as
well: a test answers them by replacing that function, or `subprocess.Popen` to see a command that
does not return, as `tests/test_command_timeouts.py` does (ADR-0290):

```python
import pytest

from src.storage import disks
from .test_pool_persistence import install_fake_host


@pytest.fixture
def fake_host(monkeypatch):
    return install_fake_host(monkeypatch)


def test_list_disks(fake_host):
    fake_host.add_disk("sdb", model="WDC WD40EFRX", serial="WD-1")
    assert [d.name for d in disks.list_disks()] == ["sdb"]
```

A notification that cannot connect from the container goes once more from the host's network with
the host's curl (ADR-0336): `FakeHost`'s curl reads the request from its config on standard input
as curl does, and answers what the test sets in `FakeHost.network` (`HttpAnswer` or
`CurlFailure`). `tests/test_notifications.py` also sends one such request through the real `curl`
of this machine to a local receiver (`UNOS_HOST_EXEC=local`), and skips it where curl is not
installed.

### Linux-Only Tests

`tests/test_share_repair.py` (Repair permissions) walks real temporary
folders with `os.O_PATH`, `/proc/self/fd` and `/proc/self/fdinfo`, which only
Linux has: it is skipped on macOS and runs on Linux.

A new share folder is created only through a pool root that is on the pool's
mount, which UNOS checks in `/proc/self/mountinfo` where that file exists. The
tests' pools are plain folders, not mount points, so on Linux
`tests/conftest.py` replaces that check (`service._pool_root_mount_problem`)
in every test; the tests of the check call the real function.

From a Mac, `scripts/dev/linux-tests.sh [pytest arguments]` runs the backend tests on Linux in
the dev device's RAM, without a container, and reports the SD writes of the run (ADR-0187).
CI's Unit Tests job runs them on GitHub's runner too (ADR-0462); here they run on tmpfs: a test fakes what
another filesystem answers differently, as ext4's EPERM in `tests/test_upload_streaming.py`.

### Samba's testparm

`tests/test_smbconf_testparm.py` checks the smb.conf that UNOS renders (both
`[global]` variants, a share of every kind, many users and a long comment)
with the real `testparm`, and is skipped where `testparm` is not installed.
`scripts/dev/bin/testparm` runs the dev device's testparm on a file sent over standard input,
read-only, so the test can use Samba's real parser from a Mac:

```bash
PATH="$PWD/scripts/dev/bin:$PATH" scripts/dev/sd.sh measure testparm -- make test TESTS=tests/test_smbconf_testparm.py
```

`sd.sh measure` prints what the run wrote to the device's SD card, as every check on the device
reports it (ADR-0214).

With Docker instead, the Samba of Debian 13 (trixie) in a container (this downloads a Python
image and Samba's tools):

```bash
docker run --rm -v "$PWD/supervisor:/s:ro" -w /s python:3.11-slim-trixie sh -c \
  'apt-get -qq update && apt-get -qq install -y samba-common-bin >/dev/null && \
   mkdir -p /run/samba && \
   pip -q install -r requirements.txt -r requirements-dev.txt && \
   PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider tests/test_smbconf_testparm.py'
```

`mkdir -p /run/samba` is needed: the rendered smb.conf keeps Samba's cache
there (`cache directory = /run/samba`), and testparm fails when that folder is
missing (smbd creates it when it starts).

The test refuses every parameter that testparm does not know. Older Samba
releases may not know `server smb encrypt` or `smb3 unix extensions`, two
lines of the rendered `[global]` that restate Samba's defaults: UNOS installs
its smb.conf there all the same (smbd ignores such a line, and UNOS accepts
testparm's message about exactly these two), but this test fails on them.

## E2E Tests Against the Local Stand-In

```bash
make e2e-local                                            # every spec
scripts/standin/e2e.sh tests/shares.spec.ts --grep Seeded # a part (Playwright's arguments)
scripts/standin/e2e.sh tests/first-run.spec.ts            # the first-run setup alone
scripts/standin/e2e.sh --help                             # what it does, its settings
```

`scripts/standin/e2e.sh` builds the web UI, starts the stand-in on this machine from a fresh data
directory under `.standin/` (git-ignored): the real supervisor through
`scripts/standin/run_supervisor.py`, with every data path inside `.standin/`, and
`scripts/standin/front.mjs` in place of Caddy (HTTPS with a self-signed certificate and the
security headers of `webui/Caddyfile`; the served certificate that the supervisor writes into
`.standin/data/served-certificate/` while it loads, ADR-0404). It creates the test administrator `admin` /
`unos-e2e-pass` (`E2E_ADMIN` in `webui/tests/fixtures.ts`) as the owner creates theirs: on the new
stand-in, which has no account yet, a first Playwright run with `E2E_FIRST_RUN=1` runs
`tests/first-run.spec.ts` alone (never retried), which goes through the `/setup` page; when it
fails, e2e.sh stops (log: `.standin/logs/first-run.log`, ADR-0261). The run of the specs ignores
that file, so arguments that name only it make this setup the whole run: e2e.sh prints its log
and, when it passed, `== Playwright exit code 0`. Otherwise it then runs Playwright with
`BASE_URL` and `HTTP_URL` on localhost and `E2E_STAND_IN=1`, stops everything and exits with
Playwright's exit code. Playwright also gets the stand-in's pools folder (`E2E_POOLS_BASE`), and its state directory
and Python (`E2E_STANDIN_STATE`, `E2E_STANDIN_PYTHON`), with which `tests/emergency-reset.spec.ts`
runs the supervisor's emergency command line against the stand-in's data
(`scripts/standin/emergency.py`, in place of `docker exec` into the supervisor's container); the
compose test stack does not set them, and the spec skips there. The logs are in `.standin/logs/`, the HTML report in `webui/playwright-report/`
(`npx playwright show-report` in `webui/`). The ports default to 46443 (HTTPS), 46080 (HTTP) and
46180 (the supervisor); `E2E_HTTPS_PORT`, `E2E_HTTP_PORT` and `E2E_BACKEND_PORT` change them. The
standin-e2e skill explains how to find the cause of a failure.

`webui/playwright.config.ts` has two projects: `chromium` runs every spec, and `webkit`
(Playwright's Desktop Safari) runs `tests/form-fields.spec.ts` alone, which checks in both
browsers that the fields of a few forms are one height, that a select is drawn by the design and
that no number field shows the browser's spin buttons (ADR-0460). Both run in `make e2e-local` and
whenever e2e.sh is given that spec; `--project=chromium` or `--project=webkit` picks one.

### The Target

`webui/playwright.config.ts` targets the stand-in, `https://localhost:46443`, unless `BASE_URL`
is set, and throws for a `BASE_URL` or `HTTP_URL` whose host is not `localhost`, `127.0.0.1`,
`::1` or `webui` (the web UI's name on the network of the compose test stack;
`webui/tests/target.ts`). `BASE_URL` must be the HTTPS address: plain HTTP only redirects to
`https://<host>/` on the default port. Playwright ignores certificate errors
(`ignoreHTTPSErrors`), because the certificates are self-signed or come from Caddy's local CA.
`HTTP_URL` is the plain-HTTP address of the same server, for the redirect and health checks in
`tests/https.spec.ts`. `E2E_CHROMIUM_PATH` points Playwright at another Chromium binary on a
machine that cannot download Playwright's own, best the headless shell of the build that the
lock's release expects (`./node_modules/.bin/playwright install --dry-run chromium` in `webui/`
prints its folder); such a machine lacks Playwright's WebKit too, and
`--project=chromium` among e2e.sh's arguments (or in `E2E_ARGS`) leaves the WebKit project out. The browser runs in en-US (`locale`): without a
language chosen in it, the web UI starts in the browser's language, and the specs find its
elements by their English texts; `tests/language.spec.ts` switches to Russian (ADR-0287), and so
do the tests of a translated area that check its pages in Russian, such as those of
`tests/dashboard.spec.ts` and `tests/files.spec.ts` (the language kept in the browser's
`localStorage` as `unos-locale`). Its window is Playwright's Desktop Chrome, 1280 x 720;
`tests/mobile-layout.spec.ts` sets a phone's 375 x 812 and checks that no page, tab or dialog
scrolls sideways there, in both languages, and that a dialog or a long folder taller than the
window scrolls to its last button or entry, there and on a phone held sideways, 667 x 375
(ADR-0302).

The stand-in is not a NAS host: it has no disks or pools that UNOS manages, no Docker, no Samba,
and not the host's network, journal or `/proc`. With `E2E_STAND_IN=1`, the specs that need those
skip with the reason (`test.skip(process.env.E2E_STAND_IN === '1', '<what the stand-in
lacks>' + TEST_STACK_TOO)`, from `webui/tests/target.ts`), and the tests that turn SMB on for a new user check that this fails with a clear
message (503). Both local targets run their host commands on their own machine
(`UNOS_HOST_EXEC=local`), so their power API answers every reboot, shutdown, suspend, cancel and
Wake-on-LAN request with 503 and runs nothing (ADR-0233): `tests/power.spec.ts` checks that
answer and answers the Power tab's requests with route fakes otherwise. The backend tests (with fake hosts), the testparm check above and the checks on
the dev device cover what the stand-in cannot. The compose test stack sets `E2E_STAND_IN=1` too
(`scripts/test-cycle.sh` and its `test-runner`), so it takes the same skips, also those of the
specs that need Docker, although that stack has the host's Docker socket. On the stand-in, which
has no Docker socket, the container and app routes answer 503 with the reason (ADR-0323), which
`tests/api.spec.ts` and the "Containers without Docker (stand-in)" specs of
`tests/containers.spec.ts` check; the test stack skips them too.
restic is not installed here: for the off-device copy the stand-in runs the fake restic of
`supervisor/tests/fake_restic.py`, which the backend tests use too, keeps its copy targets below
its state directory and counts none of it as the system disk (ADR-0337). The test stack has no
restic, and its pools share the filesystem of its data volume, which the copy refuses as the
system disk: it skips the main flow of `tests/offsite.spec.ts`.
The stand-in's supervisor makes self-signed certificates (`tests/security.spec.ts`) with the
`openssl` first on its PATH, which must be OpenSSL's, as in the supervisor's image, not
LibreSSL's, macOS's `/usr/bin/openssl`: LibreSSL writes the key of `-keyout -` to a file named
`-` in the supervisor's working directory instead of its standard output, and the generation
fails. Put OpenSSL first on PATH (Homebrew's `openssl`). The backend tests that run the real
openssl (`supervisor/tests/test_security.py`) skip without OpenSSL's.

The tests that restore a backup ("Restoring backups" in `tests/backup-restore.spec.ts`) replace
the database and `smb.conf` of the target. They run only when the target is on this machine
(`localhost`, `127.0.0.1`, `::1`, `*.localhost`) or `E2E_ALLOW_RESTORE=1` is set; otherwise they
are skipped before any request. The compose test stack sets `E2E_ALLOW_RESTORE=1`.

The E2E specs are not run in CI.

### Writing Tests

```typescript
// tests/fixtures.ts - Shared authentication. The specs take the password from E2E_ADMIN.
export const E2E_ADMIN = { username: 'admin', password: 'unos-e2e-pass' } as const;

export const test = base.extend<{ authenticatedPage: Page }>({
  authenticatedPage: async ({ page }, use) => {
    await page.goto('/login');
    await page.fill('input[type="text"]', E2E_ADMIN.username);
    await page.fill('input[type="password"]', E2E_ADMIN.password);
    await page.click('button[type="submit"]');
    await expect(page).toHaveURL('/');
    await use(page);
  },
});
```

```typescript
import { test, expect } from './fixtures';

test('should display dashboard', async ({ authenticatedPage }) => {
  await authenticatedPage.goto('/');
  await expect(authenticatedPage.locator('h1')).toContainText('Dashboard');
});
```

## The Dev Device

The dev device is a single shared resource whose system disk is an SD card that cannot be
replaced (ADR-0214). It is reached only through `scripts/dev/`, which reads its settings and
secrets from the git-ignored `dev.env` (ADR-0212):

- `scripts/dev/check.sh`: the device's health (containers, Samba and network discovery, the API,
  the inbound IPv6 filter, SD writes since boot, each container's CPU and the temperatures, with
  UNOS's budget and the throttling margin of ADR-0421), read-only.
- `scripts/dev/api.py METHOD PATH`: what the API on the device answers. Methods other than GET
  change the device.
- `scripts/dev/upload-speed.py [--mib N] [--runs R]`: how fast this workstation uploads to the
  device through Caddy and the upload route; it makes and deletes a folder `unoscheck<6 hex>` on
  the pool and signs in and out (about 0.1 MiB on the SD card).
- `scripts/dev/immich-ml-probe.py [--runs N] [--clip-model NAME] [--language CODE] [--text TEXT]`:
  whether Immich's machine learning answers, and how fast: run inside its container with that
  container's python3, read from standard input (`scripts/dev/ssh.sh 'docker exec -i
  immich-machine-learning python3 -I -B -' < scripts/dev/immich-ml-probe.py`, ADR-0183; arguments
  follow `python3 -I -B -`), it sends a picture it draws in memory and a text, as Immich's server
  asks for them, and prints each request's status, the time of its first call (which downloads
  the models to the pool), the median of N more and the answer's shape, never an embedding.
  CLIP's model is Immich's default, ViT-B-32__openai, unless `--clip-model` names the one that
  Immich's settings chose: the dev device's Immich uses nllb-clip-base-siglip__v1, so pass that
  name, or the probe downloads a model Immich does not use. It writes nothing; the machine learning's log says whether the models ran on the NPU
  (ADR-0382).
- `scripts/dev/restic-rest-server.py --listen ADDRESS:PORT --root FOLDER --cert CERT --key KEY --user
  NAME --password-file FILE`: a minimal restic REST server over HTTPS (restic's REST protocol,
  versions 1 and 2, with basic authentication), for the device check of the off-device copy to a
  REST server (`backup.off-device-copies-over-sftp`, the steps in its notes, ADR-0422). Copied into
  `/dev/shm` and run there in the background with the host's python3, on a folder of the pool and
  with the password in a file of mode 600 in RAM, never on a command line; it writes only the
  repositories' files and logs only refused or failed requests, on standard error
  (`supervisor/tests/test_restic_rest_server.py`).
- `scripts/dev/security-key-probe.py`: security keys through the device's API (ADR-0409), with a
  software authenticator that signs with the openssl command line. Copied into `/dev/shm` and run
  there as root with the host's python3 (`python3 -I -B security-key-probe.py`), it reads the name
  and password of a temporary regular user, which the check made through `api.py`, as one JSON
  line on standard input; it turns the user's two-factor authentication on, registers a key for the
  name `unoscheck.invalid`, signs in with it, sees the same answer refused, removes the key and its
  folder in `/dev/shm`, and prints PASS or FAIL for each step. It signs in 5 times, the login's
  limit of a minute for 127.0.0.1, which the relay of `api.py` shares: start it a minute after the
  relay's last sign-in. `supervisor/tests/test_security_keys.py` runs it against the API.
- `scripts/dev/linux-tests.sh`: the backend tests on Linux in the device's RAM.
- `scripts/dev/deploy.sh`: `status`, `sync --dry-run` and `backup` only read the device
  (ADR-0223). `backup` copies the device's fstab, smb.conf and `unos-data` volume into the
  git-ignored `tmp/dev/backup` of the main checkout with mode 600, because the copy holds the
  device's JWT secret and password hashes, and keeps the newest 3. Only `rollback` reads them:
  they are never shown or extracted (the guard and the settings' deny rules). The building steps,
  `up` and `rollback` run only when the owner asks for a deploy.
- `scripts/dev/sd.sh measure <label> -- <command>`: how many MiB a command wrote to the SD card.
- `scripts/dev/ipv6-filter.sh install|status|remove`: the device's inbound IPv6 filter
  (ADR-0301); `status` only reads, `install` and `remove` run on the owner's request.
- `scripts/dev/scenario.sh [--pool NAME] AREA...`: the scenario checks (ADR-0304) of `files`,
  `volumes`, `smart`, `backups`, `smb`, `smbconf`, `shares`, `scrub`, `balance`, `offsite`, `restore` and
  `hardware` (`all`: every area), on the owner's request. Each runs its area's user scenario through the API
  and on the host with a temporary user, share, volume, Scheduler task and folders named
  `unoscheck<6 hex digits>` and two backups (smbconf also edits smb.conf by hand and restarts the
  supervisor, ADR-0309; shares makes entries of other owners and modes on the pool over ssh, signs
  in as its user with a token of its own in the device's RAM and turns guest access on for its
  share, ADR-0340; scrub scrubs the pool, ADR-0314; balance balances chunks of the pool that it
  leaves about 12% full, and pauses, resumes and cancels the balance, ADR-0379; offsite sets up an off-device copy to a folder
  of the pool, with its restores, and forgets it, ADR-0337; restore restores a backup it made
  moments before, twice, ADR-0341; hardware only reads what the App Store offers of the host's SoC
  to each container of an app, the device nodes the host has and the RKNPU driver's version,
  ADR-0382), prints PASS or FAIL and the SD MiB of each step, removes what it made, also
  after a FAIL or Ctrl-C, and compares the
  device with how it found it. The feature-pass skill names the check of each host-level entry;
  after a reboot (ADR-0018) `scenario.sh all` runs again, with the checks by hand that its table
  lists. `supervisor/tests/test_dev_scenarios.py` runs them against a stand-in of the device in
  memory.

`deploy.sh`, `linux-tests.sh`, `ipv6-filter.sh` (install, remove) and `scenario.sh` hold the
device lock, so that one agent at a time deploys or tests there (ADR-0210), and keep their scratch
data in RAM. `deploy.sh`, `linux-tests.sh`, `ipv6-filter.sh` (install, remove), `scenario.sh`,
`check.sh` and `sd.sh` print the SD writes of their run; `ssh.sh`, `api.py` and `bin/testparm`
take no lock and print no SD writes, so a check made with them runs under `sd.sh measure`. The
deploy-device and device-checks skills (`.agents/skills/`) give the steps and what to report.
Neither runs E2E specs on the device.

## CI

The workflow CI was off on GitHub from 2026-11-07 (ADR-0230) and runs again since 2026-11-22
(ADR-0462). GitHub Actions (`.github/workflows/test.yml`) runs on every push to `main` and on every pull
request against `main`; a new push to a pull request cancels the run for the previous one.

### Workflow Jobs

1. **Unit Tests** (`test`): a checkout with the whole history (`fetch-depth: 0`), so that
   `tests/test_feature_list.py` can compare the ledger with the one at the merge base with
   `origin/main` (it skips while main has no `feature-list.json`), then
   `pip install --only-binary=:all: --no-binary=nut2 -r supervisor/requirements-dev.txt` on
   Python 3.11 and `make test`, with its coverage floor ([Coverage](#coverage)).
2. **Lint and Audit (Python)** (`lint`):
   - `pip install --require-hashes --only-binary=:all: -r .github/lint-requirements.txt`: ruff,
     mypy, shellcheck and pip-audit, and every package they depend on, with their hashes
   - `scripts/audit-python.sh`: known vulnerabilities in the runtime dependencies of the
     supervisor, and in their dependencies, resolved in the audit environment (below)
   - `python -m venv supervisor/.venv && supervisor/.venv/bin/pip install --only-binary=:all: --no-binary=nut2 -r supervisor/requirements-dev.txt`:
     the supervisor's packages and their type stubs, which mypy checks the supervisor against. They
     have no hashes, so they go into a venv of their own after the audit, not into the Python of the
     tools; `make lint` takes that venv as `PYTHON`
   - `make lint`: ruff with the rules of `ruff.toml`, mypy with the settings of `mypy.ini`,
     `bash -n`, shellcheck with the settings of `.shellcheckrc` and the JSON check, each also when
     another one failed. The step's environment sets `SHELLCHECK` to the pinned shellcheck in
     setup-python's folder (`${{ env.pythonLocation }}/bin/shellcheck`): the runner has another
     one on `PATH`
3. **Frontend Checks** (`frontend`), on Node.js 22:
   - `npm ci` in `webui/`
   - `make frontend`: `npm run check` (`svelte-kit sync`, then `svelte-check --threshold
     error`), `npm run runes` (every component in runes mode, ADR-0427), `npm run i18n` (the web UI's texts, ADR-0287), `npm run design` (the design system, ADR-0400), `npm run build`, `npm run lint` (ESLint) and `npm run format-check` (Prettier's check, ADR-0449), each also when
     another one failed
   - `npm audit --omit=dev --audit-level=high`: high and critical
     vulnerabilities in the `dependencies` of `webui/package.json`
     (lucide-svelte with its peer svelte, and qrcode) and everything they
     depend on; the `devDependencies` are left out, `@sveltejs/kit` too,
     although its client code is bundled into the web UI
   - no E2E spec: they need the stand-in or the compose test stack and Playwright's Chromium
     and WebKit (WebKit for `tests/form-fields.spec.ts`), which `make e2e-local` and
     `scripts/test-cycle.sh` bring (above and below)
4. **Commit Messages** (`commits`), on pull requests: `scripts/check-commit-msg.py --range` from
   the base to the head of the pull request.
5. **Build Check** (`build`): the Docker builds of both images (pushes to `main` only, after the
   unit tests).
6. **Secret Scan** (`secrets`): a checkout with the whole history, then `make secret-scan`
   (above, ADR-0445).

Every check fails the run when it finds something (ADR-0190). In the lint and frontend jobs the
audit runs even when the other step has failed, and inside `make lint` and `make frontend` every
part runs, so one run reports all problems. The jobs only read the repository
(`permissions: contents: read`) and check it out with `persist-credentials: false`: no step needs
the job's GitHub token afterwards, so it is not left in the git configuration of the checkout,
where the build or test code of a dependency could read it.

Every `uses:` line of both workflows names the commit of an action's release, with the release's
version in a comment, `actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1`
(ADR-0423): the owner of an action's repository can move a tag such as `v7` to other code, which
the next run would take with the job's token, but not a commit. Dependabot proposes the next
release's commit and rewrites the comment, for all actions in one pull request; an action has the
same pin in every workflow. To move a pin by hand, take the commit of the release's tag
(`gh api repos/<owner>/<repo>/git/ref/tags/<tag>`; an annotated tag's object is a tag that names
the commit) and change every line of that action. `supervisor/tests/test_deploy_scripts.py` fails
for a `uses:` line without a full 40-digit SHA or without the version in its comment.

svelte-check is a dev dependency of the web UI: `npm ci` installs
exactly the version in `webui/package-lock.json`, with every package it
depends on, so a new release cannot change the type check (to bump it, run
`npm install --save-dev --save-exact svelte-check@<version>` in `webui/`).
`npm run check` runs it from `webui/node_modules/.bin`, as `npm run` does for
every script of `webui/package.json`; `npx` would download a program that is
missing there from the registry, without asking where `CI` is set. The Node.js major version
of the frontend job, of the builder stage in `webui/Dockerfile` and of `engines` in
`webui/package.json` must be the same (`supervisor/tests/test_deploy_scripts.py` checks it, and
that this page has the commands of the lint and frontend jobs).

### Scheduled Maintenance

- `.github/workflows/audit.yml` runs `scripts/audit-python.sh` and
  `npm audit --omit=dev --audit-level=high` every Monday, and on demand (`workflow_dispatch`),
  because advisories are published without any change here (ADR-0218). The audits of the
  pull-request jobs can fail a commit that passed before for the same reason. Update the
  affected package then (`supervisor/requirements.txt`, or `webui/package-lock.json`, e.g. with
  `npm update <package>`) rather than weakening the check.
- `.github/dependabot.yml` proposes grouped updates every week for pip, npm, GitHub Actions (the
  commit of a newer release, ADR-0423) and the Docker base images (a newer digest of each pinned
  tag, ADR-0305), with a cooldown of a week and Conventional Commit prefixes. Nothing is merged
  automatically. New major npm versions and Playwright are left out (ADR-0427), and so are
  ESLint, Prettier and their plugins (ADR-0449, "Updating the lint tools").
- The `images` job of `audit.yml` (Image vulnerabilities) runs `make image-scan` every Monday
  (ADR-0446): `scripts/image-scan.sh` builds the supervisor's and the web UI's image with
  `docker compose` (project `unos-image-scan`) without the build cache, as `deploy/update.sh`
  does, or scans the images it is given, with trivy from `ghcr.io/aquasecurity/trivy:0.76.2`,
  pinned by the digest of its index. trivy downloads its vulnerability database in a run of its
  own into `TRIVY_CACHE` (default `~/.cache/trivy`), then scans each image from an archive of
  `docker image save`, without the network, the Docker socket or capabilities, as the calling
  user. The policy: `--scanners vuln --pkg-types os,library --severity HIGH,CRITICAL
  --ignore-unfixed`, so a HIGH or CRITICAL vulnerability with a fixed version fails the run
  (`--exit-code 3`; trivy's own errors exit 1 and fail it too), and its table names the package
  with the installed and the fixed version; one without a fix is not counted until it has one.
  A fresh build (apt-get and apk install the packages of the day), a newer digest of the base
  image or a newer package fixes it; an exception goes into `.trivyignore.yaml` with the package
  (`purls`), its reason (`statement`) and a date at most a year on (`expired_at`), after which
  trivy reports it again.
- The `bootstrap` job of `audit.yml` (Bootstrap (make setup)) runs `make setup` every Monday and
  on demand on a fresh ubuntu-latest runner (ADR-0451), as a new checkout is set up: a checkout with
  the whole history and without the token, Python 3.11 and Node.js 22 from setup-python and
  setup-node without their caches, then `make setup`, which installs everything from the indexes
  and ends with its baseline, `make check-fast` and then `make smoke`. A missing prerequisite, a
  failed install or a red baseline exits 1 and fails the run; its last lines name the part. The
  runner image, the Python and Node.js releases, the indexes and Playwright's browser download
  change without a change here, which is why the job is weekly and not in the CI workflow, which
  runs only on a push or a pull request (ADR-0462). Chromium and WebKit are installed but not started there. When GitHub moves
  ubuntu-latest to an Ubuntu that the pinned Playwright does not know, the browser install fails
  ("Playwright does not support chromium on ubuntu<version>-x64"), and when it knows it,
  `test_ubuntu_latest_is_the_ubuntu_of_githubs_runner` fails in the baseline: Playwright moves to a
  release that knows the new Ubuntu, with `UBUNTU_LATEST` and `FIRST_PLAYWRIGHT_FOR_UBUNTU_LATEST`
  of `supervisor/tests/test_repo_tooling.py`, whose tests check the pin against them (ADR-0461).
- The monthly local passes are listed in [maintenance.md](maintenance.md).

### Running the CI Checks Locally

```bash
make check      # Unit Tests, Lint and Frontend Checks
make audit      # the two audits (needs the network)
make secret-scan        # gitleaks over the whole history (needs Docker)
make image-scan         # both images built and scanned by trivy (needs Docker and the network)
make catalog-platforms  # the catalog images' recorded platforms against the registries (needs the network)
make catalog-tags       # the catalog images' pinned tags against the registries (needs the network)
```

Without `make setup`, the same commands run with the tools of your own environments, for
example:

```bash
pip install --only-binary=:all: --no-binary=nut2 -r supervisor/requirements-dev.txt   # Python 3.11
python3.11 -m venv ~/.venvs/unos-lint
~/.venvs/unos-lint/bin/pip install --require-hashes --only-binary=:all: -r .github/lint-requirements.txt
make lint RUFF=~/.venvs/unos-lint/bin/ruff MYPY=~/.venvs/unos-lint/bin/mypy SHELLCHECK=~/.venvs/unos-lint/bin/shellcheck
(cd webui && npm ci)
make frontend
```

- `ruff check --fix supervisor scripts .claude/hooks` applies ruff's safe fixes, such as the
  removal of unused imports. Unused variables (F841) and the other findings without a safe fix need
  a manual fix: an assignment may hold a call that must stay. A finding that stays gets
  `# noqa: <code>` with its reason on the line, or a per-file ignore with its reason in
  `ruff.toml` (ADR-0425); ruff fails for a `noqa` that suppresses nothing (RUF100).
- `make lint-mypy` runs mypy alone. An error that stays gets `# type: ignore[<code>]` with its
  reason on the line, or a section of its module with the reason in `mypy.ini` (ADR-0447); mypy
  fails for an ignore that suppresses nothing and for one without its code. A runtime dependency
  that publishes no type information gets its stubs (`types-<name>`) pinned in
  `supervisor/requirements-dev.txt`, the environment that mypy reads.
- `make lint-shellcheck` runs shellcheck alone, with its default checks and one line per finding
  (`file:line:column: level: text [SCnnnn]`; `shellcheck <file>` shows it with the code and a
  link to the check's page). `.shellcheckrc` lets it follow the files that a script sources,
  relative to the script's folder, and turns no check off; it is the only rc file of the
  repository, as shellcheck reads the first one it finds above a script. A finding that stays gets a directive
  with its reason, `# shellcheck disable=SCnnnn # <why>`, on the line before the command, or,
  for a whole script, before its first command (`test_repo_tooling.py` fails for a directive
  without its reason); a file that a script sources from the host gets
  `# shellcheck source=/dev/null # <why>` (ADR-0448). `SHELLCHECK_OPTS` from the environment
  adds to its options: a run with it records no green run.
- `scripts/audit-python.sh` resolves `requirements.txt` in the audit environment, a temporary
  virtual environment of Python 3.11 (`PYTHON`, `python3` by default) that it removes at the end:
  pip, setuptools and wheel (and packaging, which wheel needs) from
  `.github/audit-env-requirements.txt`, pinned with their hashes. That pip resolves the
  requirements without installing them, with wheels only but nut2 and every package listed (the
  flags `--dry-run --ignore-installed --only-binary=:all: --no-binary=nut2`), and builds nut2's
  sdist with the pinned setuptools (`--no-build-isolation`); then pip-audit (`PIP_AUDIT`) checks the resolved versions
  without pip (`--disable-pip --no-deps --strict`). `pip-audit -r` resolved them itself, after it had
  installed the latest pip, setuptools and wheel, unpinned (ADR-0423). `npm audit` asks the npm
  registry. Both need network access.
- `npm run check` first runs `svelte-kit sync`, which writes SvelteKit 3's
  `node_modules/$app/tsconfig.json`: `tsconfig.json` extends it as
  `$app/tsconfig` (`npm run build` writes it too, ADR-0456).
- `make frontend-lint` and `make frontend-format` run ESLint and Prettier's check alone, and
  `npm run format` in `webui/` writes Prettier's format over `src/` (ADR-0449). A finding of
  ESLint is fixed; a rule turned off in `webui/eslint.config.js`, or a line exempted with
  `// eslint-disable-next-line <rule> -- <reason>`, gives its reason. Where Prettier's line break
  would change a text of Svelte 5 (a space next to a block element), the element keeps its source
  with `<!-- prettier-ignore -->` and a comment with the reason.

### Updating the lint tools

`.github/lint-requirements.txt` is generated from
`.github/lint-requirements.in` with [uv](https://docs.astral.sh/uv/). Change
the versions in the `.in` file, then run, from the root of the repository:

```bash
uv pip compile --universal --python-version 3.11 --generate-hashes .github/lint-requirements.in -o .github/lint-requirements.txt
```

`--universal` resolves for every platform, the Linux x86_64 of the CI runners
included, and `--python-version 3.11` for Python 3.11 and later (the lint job
runs 3.11). Where it can, uv keeps the versions that the `.txt` already pins
for the packages the tools depend on; add `--upgrade` to update those
too, or `--upgrade-package <name>` for one of them. Commit both files, then run `make setup`
to update `.venv-lint`. `supervisor/tests/test_deploy_scripts.py` checks that every package has
hashes and that the tools have the versions of the `.in` file. Dependabot leaves these
two files alone. A new mypy or shellcheck can find errors that the old one did not: fix them in
the same change. shellcheck-py's version is shellcheck's with a build number of the wheel
(`0.11.0.1` carries shellcheck 0.11.0); it publishes wheels for Linux x86_64, macOS (arm64 and
x86_64) and Windows, none for Linux on ARM, where `make setup` stops for want of a wheel
(ADR-0448).

ESLint, Prettier and their plugins (`eslint`, `@eslint/js`, `typescript-eslint`,
`eslint-plugin-svelte`, `prettier`, `prettier-plugin-svelte`) are exact dev dependencies of the
web UI, which Dependabot leaves alone too (ADR-0449). Move them in `webui/` with
`npm install --save-dev --save-exact <package>@<version>`, to releases a week old, then run
`npm run format` and `make frontend`: the same change fixes what the new ESLint finds and holds
the format of the new Prettier. npm takes the newest release of each package they bring that its
range allows: check the dates of the new entries of `webui/package-lock.json`
(`npm view <package> time`) and hold one younger than a week at the release before it.

The file pins pip too (pip-audit depends on it through pip-api), so the
install replaces the pip of the environment with that version.

### Updating the audit environment

`.github/audit-env-requirements.txt` is written by hand, one line per package with the sha256 of
the release's wheel. Take the version and the `digests.sha256` of its `bdist_wheel` file from
PyPI's JSON API, `https://pypi.org/pypi/<name>/<version>/json` (`https://pypi.org/pypi/<name>/json`
for the newest release), and give a release a week before you take it, as Dependabot's cooldown
does. pip and packaging keep the versions of `.github/lint-requirements.txt`, and
`supervisor/tests/test_deploy_scripts.py` checks their hashes against those uv wrote there; pip
checks every hash when the audit installs the file. Dependabot leaves this file alone: the monthly
pass of [maintenance.md](maintenance.md) moves it.

## The Container-Level Route (Linux with Docker)

The deviceless test stack runs the supervisor and the web UI in containers on a Linux machine
with Docker, without a device. It is slower than the stand-in and needs Docker; nothing else in
this guide does.

### Run the Test Cycle

```bash
./scripts/test-cycle.sh            # backend tests, then the stack and the E2E specs
./scripts/test-cycle.sh --unit     # only the backend tests (make test; run make setup first)
./scripts/test-cycle.sh --e2e      # only the stack and the E2E specs
./scripts/test-cycle.sh --quick    # the stack and the auth and API specs
./scripts/test-cycle.sh --cleanup  # remove the test containers and volumes
```

The E2E part starts the test containers (supervisor and web UI), creates the test account, runs
Playwright against the stack's HTTPS address (`./node_modules/.bin/playwright test` in `webui/`,
the version of `webui/package-lock.json`, never through npx; with `E2E_STAND_IN=1`: the stack takes
the stand-in's skips) and removes the containers again.

Each worktree runs a stack of its own, so several worktrees run the cycle at once (ADR-0439). The
script derives the stack's compose project from the checkout's path, `unos-test-` and the eight
hex digits of its `cksum`, and takes the first of 100 slots, from the one the path gives onward,
whose subnet no Docker network has: slot n publishes HTTP on `127.0.0.1:13080+n` and HTTPS on
`127.0.0.1:13443+n` and has the network `172.31.(100+n).0/24`. It chooses the slot after the
build, right before `docker compose up` creates the network that holds it, and prints them then
(`Test stack <project>: https://localhost:<port>, ...`). Every compose call names the project
with `-p`, so its `down -v` (at the start, at the end and with `--cleanup`) removes only this
worktree's containers, network and volumes, whatever `COMPOSE_PROJECT_NAME` says. When all 100
subnets are taken it stops before `up`; a port that another program holds makes
`docker compose up` fail. `./scripts/test-cycle.sh --help` names the worktree's project.

### Test Environment

The test environment uses `docker-compose.test.yml`. It has a compose project of its own, so it
never touches the containers or images of a production stack on the same host. The project, the
two published ports and the subnet come from `UNOS_TEST_PROJECT`, `UNOS_TEST_HTTP_PORT`,
`UNOS_TEST_HTTPS_PORT` and `UNOS_TEST_SUBNET_PREFIX` (the first three numbers of the /24), which
`scripts/test-cycle.sh` sets for its worktree; without them the file has the stack of the
commands below: the project `unos-test`, the ports 3080 and 3443 and `172.31.253.0/24`. The
script's stack lives while the script runs; `docker compose -p <project> -f docker-compose.test.yml
ps` (or `logs`), with the project that it prints, reaches it meanwhile. It:

- Runs the supervisor unprivileged, with `UNOS_HOST_EXEC=local`: host commands run inside the
  container
- Runs it with the sign-in limit off, as the stand-in does: the limiter of the sign-in, the
  first-run setup, one's own SMB access and the security keys. Playwright reaches the stack through
  the published port, so Caddy forwards every spec from the network's gateway (the `test-runner`
  is one address of the network too), and the limit of 5 sign-ins a minute would count the whole
  run as one client. Its image runs `uvicorn stack_app:app`, the app of `src.main` with that
  limiter off (`supervisor/tests/fixtures/stack_app.py`), which only `supervisor/Dockerfile.test`
  copies; no setting turns a limit off on a NAS, `RATELIMIT_*` variables of slowapi included
  (ADR-0452). The client address is the gateway's, by the trust rule of production
- Keeps the SQLite database and the generated JWT secret in the `test-data`
  volume
- Serves the web UI and API over HTTPS on `https://localhost:3443` (`UNOS_TEST_HTTPS_PORT`), with
  a certificate from Caddy's local CA issued for `webui`
- Answers plain HTTP on `http://localhost:3080` (`UNOS_TEST_HTTP_PORT`) only with a redirect to
  HTTPS, except the health check `/api/v1/health`

Both ports are bound to 127.0.0.1: the test account is public and the test
supervisor has the Docker socket.

A fresh stack has no account. `scripts/test-cycle.sh` creates `admin` /
`unos-e2e-pass`, which the E2E tests sign in with, through the first-run setup API,
and so does the `test-runner` (see
[Running the Tests in a Container](#running-the-tests-in-a-container)). Do the
same when you start the stack yourself:

```bash
# Start test environment manually
cd deploy
docker compose -f docker-compose.test.yml up -d --wait-timeout 120

# Create the test account
curl -k -X POST https://localhost:3443/api/v1/auth/setup \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "unos-e2e-pass"}'

# Run the specs against it
(cd ../webui && E2E_STAND_IN=1 BASE_URL=https://localhost:3443 HTTP_URL=http://localhost:3080 npx playwright test)

# View logs
docker compose -f docker-compose.test.yml logs -f

# Stop and clean up
docker compose -f docker-compose.test.yml down -v
```

`docker compose up` starts `webui` only once the supervisor answers its health
check, and waits for that. `--wait-timeout 120` limits the wait to as long as
Docker can take to report the supervisor healthy or unhealthy, that is
`start_period` + (`retries` + 1) × (`interval` + `timeout`) of its healthcheck
in `deploy/docker-compose.test.yml`, 10 + 11 × (5 + 5) seconds (a failed check
counts only if it started after `start_period`). Docker Compose 2.30 or later
applies the option to this wait; 2.17 to 2.29 accept it without applying it,
and earlier versions reject it. `scripts/test-cycle.sh` uses the same limit
(`STACK_START_TIMEOUT`); change it with the healthcheck.

### Running the Tests in a Container

The `test` profile of `deploy/docker-compose.test.yml` adds the `test-runner`
service (`webui/Dockerfile.test`), which runs the suites against the test stack
over the compose network (`https://webui`). It needs neither Node.js nor a
browser on the host. Before the tests, it waits until the stack answers and
creates the test account `admin` / `unos-e2e-pass` through the first-run setup API;
an existing account is left as it is (the API answers 400). It sets `E2E_ALLOW_RESTORE=1`, so
the restore tests run against this throwaway stack.

```bash
cd deploy

# Build the images (again after changes to the supervisor or the web UI; the
# tests and playwright.config.ts are mounted from webui/)
docker compose -f docker-compose.test.yml --profile test build

# Start the stack and run all suites
docker compose -f docker-compose.test.yml --profile test run --rm test-runner

# Run one suite (the account is created first here too)
docker compose -f docker-compose.test.yml --profile test run --rm test-runner \
  npx playwright test auth.spec.ts --reporter=list

# Stop and clean up
docker compose -f docker-compose.test.yml --profile test down -v
```

The results are printed as a list; the HTML report is written to the
`test-reports` volume (`/app/playwright-report` in the container).

The image is `mcr.microsoft.com/playwright:v<version>-noble`, which carries the
browsers, and their system libraries, of exactly that Playwright version.
`<version>` must be the `@playwright/test` version in
`webui/package-lock.json`: when you update Playwright, change the tag and the
digest of the `FROM` line of `webui/Dockerfile.test` with it
(`supervisor/tests/test_deploy_scripts.py` checks that they match, and that the
line names a digest: ADR-0305).

### What the Test Stack Has

The stack has no fake disks, pools or containers. The supervisor sees:

- smb.conf: `supervisor/tests/fixtures/init-test-data.sh` writes `/data/smb.conf` at every start
  of the container, with the seeded share `e2e_seed` that UNOS imports at its first start (the
  "Seeded SMB share" specs of `tests/shares.spec.ts`). Samba does not run in the test image.
  Those specs expect the share's import notes, and the last of them dismisses them: a second run
  fails until `down -v` gives the stack a new database.
- Pools: the folder `/mnt/pools/testpool` of the `test-pools` volume, for the file manager and
  share specs. It is not a pool that UNOS manages, and the unprivileged supervisor cannot make one.
- Disks: those of the machine that runs Docker, as `lsblk` sees them inside the container.
- Containers: the host's real containers and images, through the mounted Docker socket. The
  container has neither Docker's root folder nor `/var/lib/containerd`, so an app's install
  checks no free space of the system disk: it logs a warning and pulls (ADR-0381).
- The JWT secret that the supervisor generated into the `test-data` volume.
- Users: the administrator `admin` / `unos-e2e-pass`, created by `scripts/test-cycle.sh` and the
  `test-runner`.

### Troubleshooting the Test Stack

```bash
# Check container status
docker compose -f docker-compose.test.yml ps

# View logs
docker compose -f docker-compose.test.yml logs supervisor

# Restart with clean state (the database is in the test-data volume)
docker compose -f docker-compose.test.yml down -v
docker compose -f docker-compose.test.yml up -d --wait-timeout 120
```

Then create the test account again (see [Test Environment](#test-environment)).

If port 3080 or 3443 is in use (`lsof -i :3080 -i :3443`), start the stack with other ports
(`UNOS_TEST_HTTP_PORT=3081 UNOS_TEST_HTTPS_PORT=3444 docker compose ...`) and point the tests at
them:

```bash
E2E_STAND_IN=1 BASE_URL=https://localhost:3444 HTTP_URL=http://localhost:3081 npx playwright test
```

`scripts/test-cycle.sh` chooses the ports of its stack itself (see
[Run the Test Cycle](#run-the-test-cycle)).
For slow machines, raise `timeout` and `expect.timeout` in `webui/playwright.config.ts`.

## Adding New Tests

- Backend: `supervisor/tests/test_*.py`, with the fixtures of `conftest.py` and a fake host for
  anything that would touch the host.
- Frontend: `webui/tests/*.spec.ts`, with the `authenticatedPage` fixture; a spec that needs what
  the stand-in lacks skips with the reason when `E2E_STAND_IN=1`.
- A fix comes with a test that fails without it (ADR-0186), and a feature's entry in
  `feature-list.json` lists the tests that verify it.
- Tests are independent of each other and of the order they run in, clean up what they create,
  and never depend on `dev.env` or the dev device.
