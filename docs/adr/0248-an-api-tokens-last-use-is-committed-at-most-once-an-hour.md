---
status: accepted
date: 2026-11-07
area: auth
decided-by: agent
supersedes: [ADR-0191]
---

# ADR-0248: An API token's last use is committed at most once an hour

## Context

Every request made with an API token set the token's `last_used_at` and committed it (`verify_api_token`), a write to the database on the system disk per request (ADR-0214). A sign-in's SQLite commit writes about 0.15 MiB to the dev device's SD card (ADR-0224), so a script that calls the API every 10 seconds could write on the order of 1 GiB a day, an estimate that has not been measured. ADR-0191 proposed to commit the last use only when the stored one is older than an interval; this settles it for the entry `auth.api-token-last-used-throttle`.

## Decision

- `verify_api_token` in `supervisor/src/auth/utils.py` commits `last_used_at` only when it is empty or at least `LAST_USE_INTERVAL`, one hour, older than now. Otherwise it does not set it at all: a later commit of the same request, such as one that creates another token, would write it too.
- Now is `_token_time()`, UTC without a zone as the token times are stored; the expiry check uses it too, and tests replace it.
- A commit of the last use that raises an `OperationalError`, such as on a full `/data` (ADR-0247), is rolled back and logged as a warning with the token's id, and the request is answered; the next request tries again.
- The re-check every 60 seconds of a WebSocket opened with a token (ADR-0031) goes through the same function, so it commits at most once an hour as well. Session tokens are checked by reading only, as before.
- Tests: `supervisor/tests/test_api_token_last_use.py` (100 requests within a minute commit once; a commit again once the stored use is an hour old; the token list shows a last use at most an hour old; a request's own commit does not write a skipped last use; a failed commit), `supervisor/tests/test_ws_auth.py` (the re-checks) and `webui/tests/api-tokens.spec.ts` (the last use that the API shows stays as it is).

## Consequences

- `GET /api/v1/auth/tokens` shows a last use up to an hour older than the token's latest request.
- An automation costs at most one database commit per token and hour. How little that writes to the SD card is the entry's device check (`scripts/dev/sd.sh measure`).
- A stored last use later than the clock, after the clock was set back, is replaced only once the clock is an hour past it.
