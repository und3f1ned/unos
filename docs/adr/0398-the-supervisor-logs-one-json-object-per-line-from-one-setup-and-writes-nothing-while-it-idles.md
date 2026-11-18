---
status: accepted
date: 2026-11-18
area: architecture
decided-by: agent
---

# ADR-0398: The supervisor logs one JSON object per line from one setup, and writes nothing while it idles

## Context

The supervisor configured no logging: the root logger sat at WARNING, so the INFO lines that the code already writes (a created host account, a restored copy, the startup reconcile) never reached `docker logs unos-supervisor`, and the warnings that did came out unformatted through Python's last-resort handler, in another shape than uvicorn's own lines. A handler that caught an exception and answered 500 often logged nothing, so the cause stayed in the answer's text only, without its traceback (ADR-0023 lists this debt); dozens of handlers did. `print()` calls were mixed in. The dev device's container log is on its SD card (ADR-0214, ADR-0178 rotates it), so a log that grows while nothing happens is a cost, and a log must never hold a secret (ADR-0125).

## Decision

- **Line format.** Every line of the supervisor's log, uvicorn's included, is one JSON object on stderr with `time` (UTC, `2026-11-18T04:34:24.909Z`), `level`, `logger`, `message`; `fields` (what a call passed as `extra=`) and `exception` (the traceback as text) only when there are any. Newlines are escaped, so a traceback is one line. `JsonFormatter` of `supervisor/src/logging_setup.py`.
- **One place.** `logging_setup.py` holds the format and the configuration of every logger (`logging_config`, `configure_logging`). `src/main.py` calls `configure_logging()` before any other module of the package is imported. uvicorn configures its own loggers first and loads the application after that and before its first line, so the call replaces uvicorn's handlers in time and every uvicorn line is JSON; the image's `uvicorn` command stays without `--log-config` and `--log-level`. `scripts/standin/run_supervisor.py` hands uvicorn `logging_config(WARNING)`, which is applied after the module is imported. The access filter of ADR-0338 stays.
- **Level.** INFO by default, so the lines of real changes of state show. An administrator sets `UNOS_LOG_LEVEL` (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`) in `/etc/unos/secrets.env`, which `deploy/docker-compose.yml` passes to the supervisor, and recreates the container; a value that is no level keeps INFO and is reported once. The level applies to UNOS's own loggers (`src`) and uvicorn's. Every other library logs warnings and above only, whatever the level: at DEBUG aiosqlite writes the bound values of each statement, password hashes and two-factor secrets among them.
- **Idle writes nothing.** No loop, sampler or request that changes nothing logs. INFO lines are written for a change (a created account, a mounted pool, a restored copy), at startup and at a stop; a loop logs only behind a change or in the handler of a failure. The access log keeps only 4xx and 5xx answers (ADR-0338); uvicorn's three lines per WebSocket stay as ADR-0338 decided. A failure that lasts must not write a line per pass either: the handler holds back a warning or error identical to one written in the last ten minutes (`RepeatLimit`) and says how many it held back (`fields.suppressed_repeats`) in the next one; INFO lines and the access log are never held back. `tests/test_logging.py` runs the image's `uvicorn src.main:app` as a subprocess with the background tasks off (they run host commands): 20 health checks, the first-run setup, a sign-in and an authenticated read write no line, a wrong password writes exactly one (the access line of its 401). The loops are covered by a static check, which finds every `*_loop` function of `supervisor/src`: outside an `except` handler a loop logs only named changes (the UPS shutdown, the trash expiry's deletions and problems, the leftovers of uploads, the missing host programs), and a loop that logs failures is listed in the test. The device check measures an idle minute on the device itself.
- **Exceptions.** A handler that catches an unexpected exception and answers with it (an HTTPException that is not a literal 4xx: 500, or 503 for a busy database) logs it once with its traceback (`logger.exception`, or `exc_info=True` at WARNING for a database that is busy) before it answers; a handler that maps an expected error to 400, 404 or 409 does not log it. A test walks `supervisor/src` and fails for a broad `except` that raises an HTTPException (not a literal 4xx) without such a call; the WebSocket that streams a container's log logs its failure the same way. Where an exception is turned into a good, degraded answer (a missing sensor), nothing is logged: the answer is not an error.
- **Secrets.** No code logs a password, a token, a session key or a TOTP code, and `tests/test_logging.py` drives a failed and a good sign-in, the two-factor setup and sign-in with a good and a wrong code, an API token's creation and use, a password change and a sign-out at DEBUG and asserts that none of them is in any line. The test reads what the code logs before any masking, and the recovery codes are in it. Two guards behind it: the database engine hides bound parameters from exception texts (`hide_parameters`), and the formatter masks a value after a secret-like name (`password=`, `token:`, `Authorization: Bearer`, a `Cookie`, a query parameter, pydantic's `input_value=`) and SQLAlchemy's `[parameters: ...]` in the message, the fields and the traceback; it also masks what only looks like one (`token: expired`). The masking is a backstop for what a library or an exception text carries, not a license to log them.
- **No print().** `supervisor/src` has no `print()` call, kept by a test. The UPS auto-shutdown notice is a warning. The emergency command line (`src/emergency.py`, ADR-0284) answers the operator at a terminal, which is its output and not a log: it writes its lines with `_say`, as plain text on stdout and stderr.

## Consequences

- `docker logs unos-supervisor` shows every start, every real change of state and every failure with its traceback, in lines that `jq` reads. A failing endpoint that a page polls writes a traceback per poll for as long as it fails, within the rotation of ADR-0178; there is no de-duplication.
- A new `logger.info` in a loop or on a request that changes nothing breaks ADR-0214 and the tests.
- Logging at INFO adds the lines of changes that were silent before; they are bounded by the changes themselves.
- The stand-in keeps its quiet level (warnings), so the E2E runs' logs stay short.

## Considered options

- `uvicorn --log-config` with a JSON file: needs a second copy of the configuration beside the Python one that the stand-in and the tests use.
- A structured-logging library: one more dependency for what a formatter of 30 lines does (ADR-0012).
- Logging the unexpected exceptions in one catch-all handler: the handlers that answer 500 themselves never reach it, and it cannot know what the failing step was.
