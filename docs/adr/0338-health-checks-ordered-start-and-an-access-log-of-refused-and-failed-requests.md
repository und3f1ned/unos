---
status: accepted
date: 2026-11-11
area: deploy
decided-by: agent
supersedes: [ADR-0176]
---

# ADR-0338: Both containers have health checks, the web UI waits for a healthy supervisor, and the access log keeps only refused and failed requests

## Context

ADR-0176 kept only successful health checks out of uvicorn's access log. On 2026-11-11 the dev device wrote nothing to its SD card in 60 idle seconds, and 0.105 MiB for 100 API calls with a cached token (`scripts/dev/sd.sh measure`), above the 0.1 MiB of deploy.container-logs-off-the-system-disk. The only container log that grew with them was the supervisor's json-file log, one access line per request; Caddy logs no requests. The other rules of ADR-0176 stay and are restated here.

## Decision

- The supervisor's health check runs Python in the container (the image has neither curl nor wget) and requests `http://127.0.0.1:8080/api/v1/health` without a proxy, every 10 s with a 10 s timeout and 3 retries, after a `start_period` of 10 minutes: before the API answers, the supervisor may first mount the pools the host has not mounted (`deploy/docker-compose.yml`).
- The web UI's health check fetches `/api/v1/health` from Caddy over plain HTTP (busybox wget), so it covers Caddy and the API behind it.
- The web UI starts only once the supervisor is healthy (`depends_on` with `condition: service_healthy`). For a supervisor that runs without answering, Docker decides within 600 + 4 x (10 + 10) = 680 seconds; `deploy/install.sh`, its systemd unit and `deploy/update.sh` pass that as `--wait-timeout` (`SUPERVISOR_START_TIMEOUT`) where Compose 2.30 or later applies it.
- uvicorn's access log keeps only the requests that answered 400 or above (`_RefusedOrFailedRequestFilter` in `supervisor/src/main.py`): refused sign-ins, missing rights, rate limits, failed health checks and server errors are logged, every request that answered below 400 is not. WebSocket connections are not in the access log: uvicorn logs two lines for each one it accepts (`[accepted]` and `connection open`) through `uvicorn.error`, also for one whose sign-in is then refused with close code 4401 or 4403, and they stay. The application's own log lines, warnings and errors are unchanged, and `docker logs` shows them.
- The compose files set no `version` key.
- Measured first (ADR-0176): Docker 29 does not rewrite a container's `config.v2.json` after health probes, so the probes cost no system-disk writes.
- Tests: `TestHealthchecks` in `supervisor/tests/test_authz.py` (a real uvicorn server, requests answering 200 to 500) and the health check and wait-timeout tests in `supervisor/tests/test_deploy_scripts.py`.

## Consequences

- A normal day of the web UI and the health checks adds no access lines, only the two lines of each WebSocket that a page opens; who signed in and what an administrator changed is in the login history and the audit log, not in the access log.
- A burst of refused HTTP requests, such as wrong passwords, still writes a line each; the sign-in rate limit (ADR-0010) bounds it. A refused WebSocket sign-in leaves only the lines of its accepted connection.
- A supervisor that never answers is reported unhealthy only after about 11 minutes, and update.sh then rolls back (ADR-0049). Changing the check's timing means changing `SUPERVISOR_START_TIMEOUT` in `deploy/install.sh` and `deploy/update.sh` with it, and keeping `UNOS_DEV_UP_TIMEOUT` of `scripts/dev/deploy.sh` (default 700 s) above the 680 s; `TestHealthchecks` in `supervisor/tests/test_deploy_scripts.py` and `test_deploy_up_waits_until_docker_has_decided_the_health` in `supervisor/tests/test_dev_scripts.py` tie them.
- With Docker Compose older than 2.30, Compose itself does not limit the wait.

## Considered options

- uvicorn's `--no-access-log`: also drops the refused and failed requests, the lines worth reading.
- Container logs in RAM (a tmpfs for Docker's container folder, or journald with volatile storage): a change of the host, not of UNOS (device.docker-data-root-on-hdd, ADR-0228).
