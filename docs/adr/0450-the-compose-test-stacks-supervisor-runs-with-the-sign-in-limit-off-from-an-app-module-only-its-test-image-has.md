---
status: accepted
date: 2026-11-22
area: testing
decided-by: agent
---

# ADR-0450: The compose test stack's supervisor runs with the sign-in limit off, from an app module only its test image has

## Context

`scripts/test-cycle.sh` runs Playwright on the test machine against ports that Docker publishes
on 127.0.0.1, so Caddy sees every spec's connection come from the stack network's gateway (`.1`)
and forwards that address in `X-Forwarded-For`, which uvicorn believes from the web UI's address
(ADR-0048): the limiter of `supervisor/src/auth/limits.py` counts the whole run as one client and
refuses the sixth sign-in of a minute with 429 (ADR-0010), and `tests/auth.spec.ts` 'should show
error on invalid credentials' failed. The `test-runner` service is one address of the network too.
A Playwright run is one client in fact, so no better client address would help; the stand-in turns
the same limiter off in code, and no setting may turn it off on a NAS (ADR-0419). slowapi, however,
takes `RATELIMIT_ENABLED` and `RATELIMIT_STORAGE_URL` from the environment and from a `.env` file
(starlette's `Config`), for every limiter.

## Decision

- `supervisor/Dockerfile.test` copies `supervisor/tests/fixtures/stack_app.py` to `/app/stack_app.py`
  and runs `uvicorn stack_app:app`, with the other flags as before. The module takes the app of
  `src.main` as it is and sets `enabled = False` on the limiter of `src/auth/limits.py`, the switch
  that the stand-in sets (`scripts/standin/run_supervisor.py`): the limiter of the sign-in, the
  first-run setup, turning on one's own SMB access and adding or removing a security key. The other
  limiters of the supervisor stay on, as on the stand-in, and so do the trust rules of
  `X-Forwarded-For`.
- It is no setting. `supervisor/Dockerfile`, which `deploy/docker-compose.yml` builds, copies
  `requirements.txt` and `src/` only and runs `src.main:app`; no file of `supervisor/src`, of
  `deploy/` but the test stack's compose file, or the production Dockerfile names the module.
- Every limiter of `supervisor/src` comes from `client_limiter()` of `supervisor/src/rate_limits.py`,
  the one place that builds a slowapi `Limiter`: it gives the storage `memory://` and sets
  `enabled = True` after building it, so that no environment variable or `.env` file turns a limit
  off or moves its counters. No code of `supervisor/src` sets a limiter's `enabled` to anything else,
  exempts a route or adds a request filter.
- Tests in `supervisor/tests/test_authz.py`, `TestTestStackSignInLimit`: the sign-ins of a run as
  the stack's Caddy forwards them, all from the gateway, get 429 from the sixth on from the app of
  `src.main` and are all answered by the app of `stack_app.py`, whose next sign-in with the right
  password succeeds and whose login history records the gateway; only the test image runs the
  module, and the production files never name it; `Limiter` is built only in `rate_limits.py`, and
  no limiter is turned off in `supervisor/src`; `RATELIMIT_ENABLED=false` and another
  `RATELIMIT_STORAGE_URL` leave a limiter on its memory storage. `TestRealClientAddress` keeps the
  checks of the trust rules for both compose files.

## Consequences

- No E2E spec meets the real sign-in limit, on the stand-in or the test stack; the backend tests
  check it (`TestRealClientAddress`) and the specs check the web UI's answer to a 429 with route
  fakes (`tests/auth.spec.ts`).
- A change of the supervisor's command line is made in both Dockerfiles; the tests that read them
  (`test_authz.py`, `test_auth.py`, `test_request_limits.py`) expect `stack_app:app` in the test
  image.
- Root on a NAS could still run the test image by pointing `docker compose` at the test stack's
  file; that replaces UNOS with the unprivileged, loopback-only test stack and is no setting of
  UNOS.

## Considered options

- `RATELIMIT_ENABLED=false` in `deploy/docker-compose.test.yml`: production honoured the same
  variable, so it would be a switch that a real installation reaches.
- A higher limit, or a separate client per spec through a trusted `X-Forwarded-For` from the
  gateway: either a setting that production reaches or a weaker client-address rule (ADR-0048).
- Specs that wait out the limit: minutes per run, and every spec signs in.
