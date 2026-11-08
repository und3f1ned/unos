---
status: accepted
date: 2026-11-07
area: testing
decided-by: agent
---

# ADR-0261: The stand-in's test administrator is created through the setup page, by a first Playwright run

## Context

The first-run setup (ADR-0029, entry `auth.first-run-setup`) had no E2E test: `scripts/standin/e2e.sh` created the administrator that the specs sign in as with a request to `POST /api/v1/auth/setup` before Playwright started, so no spec ever saw a new installation. The stand-in starts from a new data directory on every run, and an installation can be set up only once.

## Decision

- `scripts/standin/e2e.sh` still creates the test administrator on its fresh data directory before it runs the specs (ADR-0215), now as the owner does after installing: it checks that the new stand-in has no account (`GET /api/v1/auth/status` answers `setup_required: true`), then runs Playwright once with `E2E_FIRST_RUN=1`, and stops with the log `.standin/logs/first-run.log` when that run fails. It sends no setup request of its own, and it drops a caller's `E2E_FIRST_RUN`, so the run of the specs never has it.
- With `E2E_FIRST_RUN=1`, `webui/playwright.config.ts` has one project, `first-run`, which runs `webui/tests/first-run.spec.ts` alone and is never retried (`retries: 0`, also under `CI`): a retry would find the account of the first try. Without it, the project `chromium` runs every other spec and ignores that file.
- `first-run.spec.ts` does on the real supervisor what the owner does after installing: the default credentials of older docs do not sign in; the web UI leads to `/setup`, which asks for the administrator's name and password; creating `E2E_ADMIN` (`webui/tests/fixtures.ts`) opens the dashboard signed in as that administrator; a second setup through the page shows the server's refusal and creates nobody.
- The compose test stack (`scripts/test-cycle.sh` and its `test-runner`) keeps creating the account through the API and accepts one that exists already, because its suites may run again against a stack that was set up before.
- Tests: `supervisor/tests/test_standin.py` (`TestSpecsAndRunner`): the projects of the configuration with and without `E2E_FIRST_RUN=1`; `e2e.sh` checks the new stand-in, sends no setup request, runs the first-run setup with `E2E_FIRST_RUN=1` before the specs and stops when it fails; the spec creates `E2E_ADMIN` through the setup form.

## Consequences

- Every stand-in run goes through the first-run page, and a broken setup page stops the run before any spec, as a failed setup request did.
- The first-run setup's result is in its own log and the console, not in the HTML report of the specs.
- Two setups sent at the same moment stay a backend test (`supervisor/tests/test_auth.py`): a browser cannot send them at one moment reliably.

## Considered options

- The project `first-run` as a dependency of `chromium` in one run: Playwright runs dependencies also for one spec or `--grep`, but the administrator would then be created inside the run of the specs, not before it as ADR-0215 describes, and the specs' retries under `CI` would apply to the setup too.
- Route fakes for `/auth/status` and `/auth/setup` with a real sign-in afterwards: not chosen; the page would never create the account on the real server.
