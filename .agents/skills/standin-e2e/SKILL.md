---
name: standin-e2e
description: Run the Playwright E2E specs against the local stand-in with scripts/standin/e2e.sh (make e2e-local), never against the device, and find the cause of a failure. Use after a change to webui/ or to an API the web UI calls, before marking a feature as passing, and when an E2E spec fails.
---

# E2E specs on the local stand-in

The specs create and delete users, shares and backups and restore databases,
so they run only against local targets, never against the device (ADR-0215):
this stand-in by default, or the test stack of `docs/TESTING.md`.
`webui/playwright.config.ts` throws for a `BASE_URL` or `HTTP_URL` whose host
is not localhost, 127.0.0.1, ::1 or webui (`webui/tests/target.ts`).
`scripts/standin/e2e.sh` builds the web UI, starts the real supervisor
(`scripts/standin/run_supervisor.py`, every data path in `.standin/`) behind a
Caddy stand-in (`scripts/standin/front.mjs`) from a fresh data directory,
creates the administrator the specs sign in as (`E2E_ADMIN` of
`webui/tests/fixtures.ts`) through the `/setup` page with a first Playwright
run of `webui/tests/first-run.spec.ts` (`E2E_FIRST_RUN=1`; when it fails,
e2e.sh stops: `.standin/logs/first-run.log`), runs Playwright with
`E2E_STAND_IN=1`, stops everything and exits with Playwright's code. It is the way to run the specs
here: `webui/package.json` has no test scripts, and a bare
`npx playwright test` finds no stand-in running.

## Steps

1. **Check the prerequisites.** `make setup` has made `supervisor/.venv` and
   `webui/node_modules` with Playwright's Chromium and WebKit (WebKit runs
   `tests/form-fields.spec.ts`; `--project=chromium` leaves it out on a
   machine without it). Another Python with the
   supervisor's packages: set `PYTHON`. A machine that cannot download
   Chromium: set `E2E_CHROMIUM_PATH` to an installed Chromium headless shell
   binary (for example under `~/Library/Caches/ms-playwright/`).
   Result: `test -x webui/node_modules/.bin/playwright` exits 0.

2. **Run the specs you changed.** `scripts/standin/e2e.sh tests/<name>.spec.ts`;
   Playwright's arguments pass through (`--grep <title>`, `--headed`).
   `E2E_SKIP_BUILD=1` serves the last build when only specs changed.
   `tests/first-run.spec.ts` runs first in every run; named alone, it is the
   whole run, without further arguments.
   Result: the run ends with `== Playwright exit code 0`.

3. **Run the whole suite** when it is due (ADR-0433): `make e2e-local` (about
   7 minutes), once per batch on the batch branch after the merges, before the
   deploy and before the batch reaches main, and after a change to
   `webui/src/lib/components/ui/`, `webui/src/app.css` or
   `webui/src/routes/+layout.svelte`. Any other feature runs only the specs of
   its area in step 2: those of the pages and APIs it changes, plus every spec
   citing a test id, label or catalog key it changed. Result: no `failed` and
   no `flaky` line in the summary; every skipped test shows its reason in the
   HTML report.

4. **When a test fails, find the cause; check your own change first.** Read,
   in this order: the error in `.standin/logs/playwright.log`;
   `webui/test-results/<test>/error-context.md` and its screenshot;
   `.standin/logs/supervisor.log` (tracebacks) and `.standin/logs/front.log`
   (each API request); the report (`cd webui && npx playwright show-report`).
   The data of the run stay in `.standin/data` until the next run. Result: the
   cause is named: the spec, the product code, or the stand-in.

5. **A feature the stand-in cannot provide** (real disks, btrfs, Docker, the
   host's network, the journal, Linux /proc): skip its test on the stand-in
   with what is missing, as the others do:
   `test.skip(process.env.E2E_STAND_IN === '1', 'The stand-in has no ...' + TEST_STACK_TOO)`,
   with `TEST_STACK_TOO` from `./target`: the compose test stack sets
   `E2E_STAND_IN=1` too and takes the skip, and the reason says so.
   Never keep a list of known failures, never add retries, never skip for an
   older supervisor (ADR-0207). Check the feature on the device with the
   device-checks skill instead. A skip on any other condition needs an entry,
   with its reason, in `SKIP_CONDITIONS` of `supervisor/tests/test_standin.py`.
   Result: `make test-fast` passes (`test_standin.py` checks every skip's
   condition and reason).

6. **When e2e.sh refuses to start**, read its message: another run holds
   `.standin/e2e.lock` (one run per checkout); a port is in use (a run in
   another worktree: set `E2E_HTTPS_PORT`, `E2E_HTTP_PORT` and
   `E2E_BACKEND_PORT`); a `BASE_URL` or `HTTP_URL` off this machine is refused
   (exit 2): unset it, never point it at the device. Result: e2e.sh prints
   `== starting the stand-in`.

7. **Leave the tree clean.** `.standin/` ignores itself, and `webui/build`,
   `webui/test-results` and `webui/playwright-report` are ignored. Result:
   `git status --short` lists none of them.
