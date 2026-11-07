---
status: accepted
date: 2026-11-07
area: testing
decided-by: agent
supersedes: [ADR-0188]
---

# ADR-0215: E2E tests run only against local targets, never against the device

## Context

The Playwright specs change their target: they create and delete pools, shares and users, a restore replaces the database and smb.conf, and the SMB specs would create host accounts. During the audit they were not run because they would change the live device, and against it they could not pass anyway (168 logins against a limit of 5 per minute). Yet the docs and both CLAUDE.md files still named the device as the default target, and the stand-in's missing features were tracked as a list of known failures (ADR-0188).

## Decision

- The E2E specs run only against a local target: the local stand-in, which is the default, or the compose test stack. Never against the dev device.
- The stand-in: `scripts/standin/e2e.sh [playwright args]` (also `make e2e-local`) builds the web UI, starts the real supervisor through `scripts/standin/run_supervisor.py` with every data path in the git-ignored `.standin/`, and `scripts/standin/front.mjs` in place of Caddy, creates the test administrator on a fresh data directory, runs Playwright with `BASE_URL` and `HTTP_URL` on localhost and `E2E_STAND_IN=1`, stops everything and exits with Playwright's code.
- The compose test stack, `deploy/docker-compose.test.yml`, run by `scripts/test-cycle.sh`: its own Compose project `unos-test`, so that its build and `down` never touch the images and containers of `deploy/docker-compose.yml`; HTTPS, with its ports bound to 127.0.0.1 only (3443 and 3080); no fixed JWT secret, which is generated as in production; host commands run inside its unprivileged supervisor container (`UNOS_HOST_EXEC=local`); and a Playwright runner image pinned to the `@playwright/test` version of `webui/package-lock.json` (`webui/Dockerfile.test`). It sets `E2E_STAND_IN=1` too, so it takes the stand-in's skips. Tested in `TestTestStack`, `TestTestRunner` and `TestTestCycle` of `supervisor/tests/test_deploy_scripts.py`.
- Playwright's default target is the stand-in, and `webui/playwright.config.ts` throws for a `BASE_URL` or `HTTP_URL` whose host is not localhost, 127.0.0.1, ::1 or `webui`, the web UI's name on the network of the compose test stack (`webui/tests/target.ts`). `scripts/standin/e2e.sh` refuses a target off this machine, `webui` included, before it starts anything. The guard blocks Playwright runs with a non-local `BASE_URL` or `HTTP_URL`, and Playwright on the device (ADR-0211).
- The specs run only through `scripts/standin/e2e.sh` (`make e2e-local`) and `scripts/test-cycle.sh`, which set the target and `E2E_STAND_IN=1`: `webui/package.json` has no script that starts Playwright.
- `TestTargets` in `supervisor/tests/test_standin.py` runs these checks: node loads `webui/playwright.config.ts` with `webui/tests/target.ts` over a table of accepted and refused targets (the device's address, other hosts, a host name that starts with localhost, user info before the device's address, values that are not URLs), skipped without Node.js 22.18 or later; a copy of `e2e.sh` alone in a temporary directory refuses a non-local `BASE_URL` or `HTTP_URL` with exit status 2 before it creates anything; and no npm script runs the specs.
- What the stand-in cannot provide (lsblk and real disks, Docker, the host's network, journal and `/proc`) is an explicit skip with the reason when `E2E_STAND_IN=1`: `test.skip(process.env.E2E_STAND_IN === '1', '<what the stand-in lacks>' + TEST_STACK_TOO)`, where `TEST_STACK_TOO` of `webui/tests/target.ts` adds that the compose test stack skips it too. There is no list of known failures.
- `supervisor/tests/test_standin.py` checks that every fixed `/data` path of `supervisor/src` is redirected by the stand-in, and that the security headers of `front.mjs` equal those of the Caddyfile it stands in for.

## Consequences

- A green stand-in run means every spec that can run there passed; the skipped ones name what they need.
- The compose test stack mounts the host's Docker socket, but since it sets `E2E_STAND_IN=1` it skips the specs that need Docker, as the stand-in does.
- Host-level behavior that the stand-in lacks is covered by backend tests, the Linux runner (ADR-0187) and the device check (ADR-0018), not by E2E.
- The device is never the target of an E2E run, so E2E costs it no system-disk writes (ADR-0214).

## Considered options

- The device as the default target, as the docs said: the specs change the device and cannot pass against its login limit.
