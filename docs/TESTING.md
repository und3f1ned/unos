# UNOS Testing Guide

How a change to UNOS is verified, from the repository root. `make help` lists the targets; the
`Makefile` runs the same commands as the CI workflow (ADR-0216), which is off on GitHub (ADR-0230).

## The Three Verification Levels

This table is the one place that defines the levels; CONTRIBUTING.md and the pull request
template point here.

| Level | Command | Where it runs | What it proves |
|-------|---------|---------------|----------------|
| 1. The gate | `make check` | this machine (CI is off, ADR-0230) | lint, every backend test against fake hosts (the Linux-only ones skip elsewhere), svelte-check and the production build |
| 2. E2E against the local stand-in | `make e2e-local` | this machine | the user flows of the web UI against the real supervisor and the built web UI |
| 3. The dev device | `scripts/dev/` with the deploy-device and device-checks skills | the dev device | what only a real NAS host has: disks, btrfs, Samba, Docker, a reboot |

- Level 1 must pass before a change is done; with CI off, whoever merges runs it. Two fast
  parts of it run on their own: `make check-fast` (lint and the convention tests; the pre-commit
  hook and the Stop hook of Claude Code sessions run it) and `make smoke` (in seconds: the app
  starts and serves health, the first-run setup and a login; the baseline of a session).
- Level 2 follows every change to `webui/` or to an API the web UI calls. E2E specs run only
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
   dependencies, pytest and the other test tools).
2. `.venv-lint`: ruff and pip-audit from `.github/lint-requirements.txt`, with their hashes
   (`pip install --require-hashes -r .github/lint-requirements.txt`).
3. `webui/node_modules`: `npm ci` from `webui/package-lock.json` (a symbolic link there is left
   as it is).
4. Playwright's Chromium, the version that `webui/package-lock.json` pins.
5. The git hooks: `git config core.hooksPath scripts/git-hooks`.
6. `dev.env`: a copy of `dev.env.example` with mode 600 when it is missing, except in a linked
   worktree whose main checkout has one: the scripts in `scripts/dev` use that one (ADR-0210). An
   existing `dev.env` is never replaced or shown; only its mode is set to 600.
7. The baseline: `make check-fast`, then `make smoke`. It ends with "baseline green", or names
   the target that failed before any work started.

A step that is up to date is skipped: each environment remembers the SHA-256 of the files it was
installed from, so a rerun installs only what changed. Steps 1-4 need the network.
`scripts/setup.sh --check` only reports what is missing or outdated; it changes nothing and needs
no network. Python 3.11 is found as `python3.11` on `PATH`, or set `PYTHON311=/path/to/python`.

The make targets use these environments when they exist, and `python3`, `ruff`, `pip-audit` and
`npm` from `PATH` otherwise; override them with `PYTHON=`, `RUFF=`, `PIP_AUDIT=` and `NPM=`.

## Make Targets

| Target | Runs |
|--------|------|
| `make check` | `lint`, `test` and `frontend`: what CI's Unit Tests, Lint and Frontend jobs run, without the network audits |
| `make check-fast` | `lint` and `test-fast`: the pre-commit hook and the Stop hook |
| `make lint` | `ruff check --select F,E9 supervisor scripts .claude/hooks` (pyflakes findings and syntax errors in the supervisor and in the repository's own tooling), `bash -n` on every shell script of `deploy/`, `scripts/` and `.claude/hooks/`, and the JSON syntax of `feature-list.json` and `.claude/settings.json` |
| `make test` | all backend tests (`supervisor/tests`); `TESTS=tests/test_auth.py` for one file, `PYTEST_ARGS='-x -k smb'` for pytest options |
| `make test-fast` | the convention tests: `test_adrs.py`, `test_feature_list.py`, `test_agent_guard.py`, `test_repo_tooling.py` (with the secret scan below), `test_dev_scripts.py`, `test_standin.py` |
| `make smoke` | backend tests that start the app and serve requests, in a few seconds: the startup and `/api/v1/health` (`test_lifespan.py`), the first-run setup, a login through the proxy layer and the refusal of anonymous requests |
| `make frontend` | `npm run check` (svelte-kit sync, then svelte-check) and `npm run build` in `webui/` |
| `make e2e-local` | the Playwright specs against the local stand-in (`scripts/standin/e2e.sh`); `E2E_ARGS='tests/auth.spec.ts'` for a part |
| `make audit` | `pip-audit -r supervisor/requirements.txt` and `npm audit --omit=dev --audit-level=high` in `webui/` (needs the network) |
| `make setup` | `scripts/setup.sh` (needs the network) |

`make check`, `make check-fast`, `make lint` and `make frontend` run every part, also after one
has failed, and fail at the end, naming the parts that failed (ADR-0190): one run reports every
problem. Each part starts with a line `== make <target>: <part>`.

The backend tests use fixed paths under `/tmp` (`supervisor/tests/conftest.py`), so only one run
per machine may use them at a time, also across worktrees: `make test`, `make test-fast` and
`make smoke` wait for the lock `/tmp/unos-backend-tests.lock` (`flock` on Linux, `lockf` on macOS;
`TEST_LOCK=` turns it off).

## Commits and Git Hooks

Commit messages are Conventional Commits in English, whose first line, type and scope included,
has at most 72 characters: [ADR-0209](adr/0209-conventional-commits-in-english.md) holds the
rule and [scripts/check-commit-msg.py](../scripts/check-commit-msg.py) checks it (its docstring
has the details). The hooks in `scripts/git-hooks/` (installed by `make setup`) run before every
commit:

- `pre-commit` runs `make check-fast`.
- `commit-msg` runs `scripts/check-commit-msg.py` on the message.

CI's Commit Messages job runs the same script over the commits of a pull request (CI is off,
ADR-0230). To check a branch before pushing it:

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
  refspec it asks git where the push would go (`@{push}`, the upstream, `push.default`).
- The same settings deny the Read tool every `dev.env` and every file below a `tmp/dev/backup`,
  by paths from the filesystem's root (`Read(//**/dev.env)`), so that a worktree's session cannot
  read the main checkout's either, and every tool of the Desktop Commander plugin.
- The Stop hook, `.claude/hooks/stop-check.sh` (ADR-0216), runs `make check-fast` when the agent
  is about to finish and the working tree has changed since the last green run. A failure sends
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

### Test Configuration

The `conftest.py` sets up the test environment:

```python
# Environment variables for testing
os.environ["UNOS_DB_PATH"] = ":memory:"  # In-memory database
# Random per run: secrets this repository published (such as "test-secret-key")
# are refused at startup.
os.environ["UNOS_JWT_SECRET"] = secrets.token_hex(32)
os.environ["UNOS_POOLS_BASE_PATH"] = "/tmp/test_pools"
```

### Faking the Host

Commands that run on the host (lsblk, findmnt, mkfs.btrfs, mount, ...) go
through `src/host.py`. Tests answer them with a fake host (ADR-0189): the in-memory `FakeHost` of
`tests/test_pool_persistence.py`, or `FakeSambaHost` of `tests/samba_fake.py` for Samba;
`install_fake_host` also blocks real subprocesses and Docker:

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
With CI off (ADR-0230), it is the only place where they run, on tmpfs: a test fakes what
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
scripts/standin/e2e.sh --help                             # what it does, its settings
```

`scripts/standin/e2e.sh` builds the web UI, starts the stand-in on this machine from a fresh data
directory under `.standin/` (git-ignored): the real supervisor through
`scripts/standin/run_supervisor.py`, with every data path inside `.standin/`, and
`scripts/standin/front.mjs` in place of Caddy (HTTPS with a self-signed certificate and the
security headers of `webui/Caddyfile`). It creates the test administrator `admin` /
`unos-e2e-pass` (`E2E_ADMIN` in `webui/tests/fixtures.ts`), runs Playwright with `BASE_URL` and
`HTTP_URL` on localhost and `E2E_STAND_IN=1`, stops everything and exits with Playwright's exit
code. The logs are in `.standin/logs/`, the HTML report in `webui/playwright-report/`
(`npx playwright show-report` in `webui/`). The ports default to 46443 (HTTPS), 46080 (HTTP) and
46180 (the supervisor); `E2E_HTTPS_PORT`, `E2E_HTTP_PORT` and `E2E_BACKEND_PORT` change them. The
standin-e2e skill explains how to find the cause of a failure.

### The Target

`webui/playwright.config.ts` targets the stand-in, `https://localhost:46443`, unless `BASE_URL`
is set, and throws for a `BASE_URL` or `HTTP_URL` whose host is not `localhost`, `127.0.0.1`,
`::1` or `webui` (the web UI's name on the network of the compose test stack;
`webui/tests/target.ts`). `BASE_URL` must be the HTTPS address: plain HTTP only redirects to
`https://<host>/` on the default port. Playwright ignores certificate errors
(`ignoreHTTPSErrors`), because the certificates are self-signed or come from Caddy's local CA.
`HTTP_URL` is the plain-HTTP address of the same server, for the redirect and health checks in
`tests/https.spec.ts`. `E2E_CHROMIUM_PATH` points Playwright at another Chromium binary on a
machine that cannot download Playwright's own.

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
specs that need Docker, although that stack has the host's Docker socket.

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

- `scripts/dev/check.sh`: the device's health (containers, Samba, the API, SD writes since boot),
  read-only.
- `scripts/dev/api.py METHOD PATH`: what the API on the device answers. Methods other than GET
  change the device.
- `scripts/dev/linux-tests.sh`: the backend tests on Linux in the device's RAM.
- `scripts/dev/deploy.sh`: `status`, `sync --dry-run` and `backup` only read the device
  (ADR-0223). `backup` copies the device's fstab, smb.conf and `unos-data` volume into the
  git-ignored `tmp/dev/backup` of the main checkout with mode 600, because the copy holds the
  device's JWT secret and password hashes, and keeps the newest 3. Only `rollback` reads them:
  they are never shown or extracted (the guard and the settings' deny rules). The building steps,
  `up` and `rollback` run only when the owner asks for a deploy.
- `scripts/dev/sd.sh measure <label> -- <command>`: how many MiB a command wrote to the SD card.

`deploy.sh` and `linux-tests.sh` hold the device lock, so that one agent at a time deploys or
tests there (ADR-0210), and keep their scratch data in RAM. `deploy.sh`, `linux-tests.sh`,
`check.sh` and `sd.sh` print the SD writes of their run; `ssh.sh`, `api.py` and `bin/testparm`
take no lock and print no SD writes, so a check made with them runs under `sd.sh measure`. The
deploy-device and device-checks skills (`.agents/skills/`) give the steps and what to report.
Neither runs E2E specs on the device.

## CI

The workflow CI is off on GitHub since 2026-11-07 (ADR-0230): its file stays as described here,
kept in step by the tests, and "Enable workflow" in the Actions tab turns it on again. When on,
GitHub Actions (`.github/workflows/test.yml`) runs on every push to `main` and on every pull
request against `main`; a new push to a pull request cancels the run for the previous one.

### Workflow Jobs

1. **Unit Tests** (`test`): a checkout with the whole history (`fetch-depth: 0`), so that
   `tests/test_feature_list.py` can compare the ledger with the one at the merge base with
   `origin/main` (it skips while main has no `feature-list.json`), then
   `pip install -r supervisor/requirements-dev.txt` on Python 3.11 and `make test`.
2. **Lint and Audit (Python)** (`lint`):
   - `pip install --require-hashes -r .github/lint-requirements.txt`: ruff and pip-audit, and
     every package they depend on, with their hashes
   - `make lint`: ruff, `bash -n` and the JSON check, each also when another one failed
   - `pip-audit -r supervisor/requirements.txt`: known vulnerabilities in the runtime
     dependencies of the supervisor, and in their dependencies
3. **Frontend Checks** (`frontend`), on Node.js 22:
   - `npm ci` in `webui/`
   - `make frontend`: `npm run check` (`svelte-kit sync`, then `svelte-check --threshold
     error`) and `npm run build`, the build also when the check failed
   - `npm audit --omit=dev --audit-level=high`: high and critical
     vulnerabilities in the `dependencies` of `webui/package.json`
     (lucide-svelte with its peer svelte, and qrcode) and everything they
     depend on; the `devDependencies` are left out, `@sveltejs/kit` too,
     although its client code is bundled into the web UI
4. **Commit Messages** (`commits`), on pull requests: `scripts/check-commit-msg.py --range` from
   the base to the head of the pull request.
5. **Build Check** (`build`): the Docker builds of both images (pushes to `main` only, after the
   unit tests).

Every check fails the run when it finds something (ADR-0190). In the lint and frontend jobs the
audit runs even when the other step has failed, and inside `make lint` and `make frontend` every
part runs, so one run reports all problems. The jobs only read the repository
(`permissions: contents: read`) and check it out with `persist-credentials: false`: no step needs
the job's GitHub token afterwards, so it is not left in the git configuration of the checkout,
where the build or test code of a dependency could read it.

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

- `.github/workflows/audit.yml` runs `pip-audit -r supervisor/requirements.txt` and
  `npm audit --omit=dev --audit-level=high` every Monday, and on demand (`workflow_dispatch`),
  because advisories are published without any change here (ADR-0218). The audits of the
  pull-request jobs can fail a commit that passed before for the same reason. Update the
  affected package then (`supervisor/requirements.txt`, or `webui/package-lock.json`, e.g. with
  `npm update <package>`) rather than weakening the check.
- `.github/dependabot.yml` proposes grouped updates every week for pip, npm, GitHub Actions and
  the Docker base images, with a cooldown of a week and Conventional Commit prefixes. Nothing is
  merged automatically. New major npm versions and Playwright are left out (ADR-0231).
- The monthly local passes are listed in [maintenance.md](maintenance.md).

### Running the CI Checks Locally

```bash
make check      # Unit Tests, Lint and Frontend Checks
make audit      # the two audits (needs the network)
```

Without `make setup`, the same commands run with the tools of your own environments, for
example:

```bash
pip install -r supervisor/requirements-dev.txt                   # Python 3.11
python3.11 -m venv ~/.venvs/unos-lint
~/.venvs/unos-lint/bin/pip install --require-hashes -r .github/lint-requirements.txt
make lint RUFF=~/.venvs/unos-lint/bin/ruff
(cd webui && npm ci)
make frontend
```

- `ruff check --fix --select F,E9 supervisor scripts .claude/hooks` removes unused imports. Unused
  variables (F841) need a manual fix: their assignment may hold a call that
  must stay.
- pip-audit resolves `requirements.txt` in a temporary virtual environment
  (`pip install --dry-run`), after it has installed the latest pip, setuptools
  and wheel there: those three are not pinned. `npm audit` asks the npm
  registry. Both need network access.
- `npm run check` first runs `svelte-kit sync`, which writes
  `.svelte-kit/tsconfig.json`: `tsconfig.json` extends it (`npm run build`
  writes it too).

### Updating ruff and pip-audit

`.github/lint-requirements.txt` is generated from
`.github/lint-requirements.in` with [uv](https://docs.astral.sh/uv/). Change
the versions in the `.in` file, then run, from the root of the repository:

```bash
uv pip compile --universal --python-version 3.11 --generate-hashes .github/lint-requirements.in -o .github/lint-requirements.txt
```

`--universal` resolves for every platform, the Linux x86_64 of the CI runners
included, and `--python-version 3.11` for Python 3.11 and later (the lint job
runs 3.11). Where it can, uv keeps the versions that the `.txt` already pins
for the packages ruff and pip-audit depend on; add `--upgrade` to update those
too, or `--upgrade-package <name>` for one of them. Commit both files, then run `make setup`
to update `.venv-lint`. `supervisor/tests/test_deploy_scripts.py` checks that every package has
hashes and that ruff and pip-audit have the versions of the `.in` file. Dependabot leaves these
two files alone.

The file pins pip too (pip-audit depends on it through pip-api), so the
install replaces the pip of the environment with that version.

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
Playwright against `https://localhost:3443` (`npx playwright test`, with `E2E_STAND_IN=1`: the
stack takes the stand-in's skips) and removes the containers again.

### Test Environment

The test environment uses `docker-compose.test.yml`. Its compose project is
`unos-test`, so it never touches the containers or images of a production stack
on the same host. It:

- Runs the supervisor unprivileged, with `UNOS_HOST_EXEC=local`: host commands run inside the
  container
- Keeps the SQLite database and the generated JWT secret in the `test-data`
  volume
- Serves the web UI and API over HTTPS on `https://localhost:3443`, with a
  certificate from Caddy's local CA issued for `webui`
- Answers plain HTTP on `http://localhost:3080` only with a redirect to HTTPS,
  except the health check `/api/v1/health`

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
`webui/package-lock.json`: when you update Playwright, change the `FROM` line
of `webui/Dockerfile.test` with it (`supervisor/tests/test_deploy_scripts.py`
checks that they match).

### What the Test Stack Has

The stack has no fake disks, pools or containers. The supervisor sees:

- smb.conf: `supervisor/tests/fixtures/init-test-data.sh` writes `/data/smb.conf` at every start
  of the container, with the seeded share `e2e_seed` that UNOS imports at its first start (the
  "Seeded SMB share" specs of `tests/shares.spec.ts`). Samba does not run in the test image.
- Pools: the folder `/mnt/pools/testpool` of the `test-pools` volume, for the file manager and
  share specs. It is not a pool that UNOS manages, and the unprivileged supervisor cannot make one.
- Disks: those of the machine that runs Docker, as `lsblk` sees them inside the container.
- Containers: the host's real containers and images, through the mounted Docker socket.
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

If port 3080 or 3443 is in use (`lsof -i :3080 -i :3443`), change the published ports of the
`webui` service in `deploy/docker-compose.test.yml` (for example `"127.0.0.1:3081:80"` and
`"127.0.0.1:3444:443"`) and point the tests at them:

```bash
E2E_STAND_IN=1 BASE_URL=https://localhost:3444 HTTP_URL=http://localhost:3081 npx playwright test
```

`scripts/test-cycle.sh` reads the addresses from `TEST_URL` and `TEST_HTTPS_URL` at its top.
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
