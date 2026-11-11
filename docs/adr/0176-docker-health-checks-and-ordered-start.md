---
status: superseded
superseded-by: ADR-0338
date: 2026-11-06
area: deploy
decided-by: agent
---

# ADR-0176: Both containers have health checks, and the web UI waits for a healthy supervisor

## Context

Superseded by ADR-0338: it also keeps every request that answered below 400 out of the access log.

The compose files had no health checks, so `docker ps` could not show whether UNOS worked, and the web UI could start before the API answered, when every request fails with 502. The files also still carried the obsolete `version` key, which Compose warns about on every command. Probes must not cost system-disk writes (ADR-0214).

## Decision

- The supervisor's health check runs Python in the container (the image has neither curl nor wget) and requests `http://127.0.0.1:8080/api/v1/health` without a proxy, every 10 s with a 10 s timeout and 3 retries, after a `start_period` of 10 minutes: before the API answers, the supervisor may first mount the pools the host has not mounted (`deploy/docker-compose.yml`).
- The web UI's health check fetches `/api/v1/health` from Caddy over plain HTTP (busybox wget), so it covers Caddy and the API behind it.
- The web UI starts only once the supervisor is healthy (`depends_on` with `condition: service_healthy`). For a supervisor that runs without answering, Docker decides within 600 + 4 x (10 + 10) = 680 seconds; `deploy/install.sh`, its systemd unit and `deploy/update.sh` pass that as `--wait-timeout` (`SUPERVISOR_START_TIMEOUT`) where Compose 2.30 or later applies it.
- Successful health checks (`GET /api/v1/health` answering 200) stay out of uvicorn's access log (`_SuccessfulHealthCheckFilter` in `supervisor/src/main.py`); failed checks and every other request are still logged.
- The obsolete `version` key is gone from both compose files.
- Measured first: Docker 29 does not rewrite a container's `config.v2.json` after health probes (it commits health state in memory since Engine 25), so the probes cost no system-disk writes.
- Tests: `TestHealthchecks` and the wait-timeout tests in `supervisor/tests/test_deploy_scripts.py`.

## Consequences

- A supervisor that never answers is reported unhealthy only after about 11 minutes, and update.sh then rolls back (ADR-0049).
- Changing the check's timing means changing `SUPERVISOR_START_TIMEOUT` in `deploy/install.sh` and `deploy/update.sh` with it, and keeping `UNOS_DEV_UP_TIMEOUT` of `scripts/dev/deploy.sh` (default 700 s, how long its `up` step waits for health on the dev device) above the 680 s. `TestHealthchecks` in `supervisor/tests/test_deploy_scripts.py` ties the two install scripts to the compose values, and `test_deploy_up_waits_until_docker_has_decided_the_health` in `supervisor/tests/test_dev_scripts.py` keeps the default of `deploy.sh` above them.
- With Docker Compose older than 2.30, Compose itself does not limit the wait: it lasts until Docker reports the supervisor healthy or unhealthy.
