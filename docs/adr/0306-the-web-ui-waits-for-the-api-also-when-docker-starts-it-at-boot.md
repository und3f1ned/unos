---
status: accepted
date: 2026-11-09
area: deploy
decided-by: agent
---

# ADR-0306: The web UI waits for the supervisor's API also when Docker starts it at boot

## Context

ADR-0176 starts the web UI only once the supervisor is healthy, through `depends_on` with `condition: service_healthy`, which only `docker compose up` applies. At boot dockerd starts the containers it still has itself, by their restart policy (`unless-stopped`) and at once: after the reboot of 2026-11-08 the dev device's web UI started 34 ms before the supervisor and failed its first health check, as every API request fails with 502 until the supervisor answers (ADR-0176). On a host installed by `deploy/install.sh`, the `unos` unit runs `docker compose down` at shutdown, so after a clean shutdown its `docker compose up` starts the containers in order at the next boot; after a power cut, and on a host without that unit, such as the dev device, whose containers `scripts/dev/deploy.sh up` starts, dockerd starts them together. The owner left the choice to the agent ("do it").

## Decision

- The web UI's `command` in `deploy/docker-compose.yml` and `deploy/docker-compose.test.yml` waits for the API before it runs the image's CMD: busybox `sh -c` requests `http://supervisor:8080/api/v1/health`, the upstream of `webui/Caddyfile`, with `wget -q -Y off -T 5 -O /dev/null`, pauses 2 seconds after each failed try, and once it answers runs `exec caddy run --config /etc/caddy/Caddyfile`, the CMD of `webui/Dockerfile`. Caddy thus starts only once the API answers, whoever starts the container: compose, or dockerd at boot.
- Like the supervisor's own check, the request uses no proxy (`-Y off`). A try writes no file (`-O /dev/null`), and wget's error is printed only when it differs from the one before (`unos-webui: waiting for the supervisor's API: <error>`): the wait writes a log line per new error, not per try (ADR-0214), and a cause that does not go away shows in `docker logs unos-webui`. The shell's `$` is written `$$` in the compose files, since compose fills in a single `$` from the host's environment.
- `depends_on: service_healthy` stays, so `docker compose up` in `install.sh`, its unit, `update.sh` and `scripts/dev/deploy.sh` still fails when the supervisor does not become healthy (ADR-0176, ADR-0049).
- Test: `test_webui_waits_for_the_api_whoever_starts_it` in `supervisor/tests/test_deploy_scripts.py` runs the command of both files as compose passes it, under the host's sh, with a fake wget that fails three times with two different errors: Caddy starts only after the fourth try, as the image's CMD starts it, and each error is printed once. That the busybox wget of the pinned image takes these options and prints such errors was checked in the running web UI on the dev device (busybox 1.37, 2026-11-09).

## Consequences

- Until the API answers, the web UI's ports do not answer at all, at boot as after `docker compose up`; before, at boot they served the pages and answered 502 to every API request.
- When the wait lasts longer than the web UI's health check gives it (a `start_period` of 30 s and three more checks), Docker reports the container unhealthy until the first check after Caddy has started; at boot nothing acts on that state. A cause that never goes away, such as an option that the wget of a new base image (ADR-0305) lacks, keeps Caddy from starting: `update.sh` rolls back when the new version does not answer in time, `scripts/dev/deploy.sh up` fails when the web UI does not become healthy, and at boot the log line names the error.
- A `docker stop` during the wait takes Docker's stop timeout (10 s): the shell, the container's PID 1, ignores SIGTERM. Once Caddy runs, it gets the signal itself.
- A change of the CMD in `webui/Dockerfile` has to be made in both compose files too; the test fails until it is.

## Considered options

- The same loop as the CMD of `webui/Dockerfile`: the image would have to be rebuilt before the change takes effect (about 18 MiB of SD writes on the dev device, ADR-0214), for a rule about how the two containers start together, which the compose files hold (`depends_on`).
- Caddy answering while the supervisor starts, by holding API requests (`lb_try_duration` of `reverse_proxy`) or with a 503 page that says the supervisor is starting: the ports would answer during a boot of up to 10 minutes, but every API request would wait or get that page whenever the supervisor restarts, the image would need a rebuild, and ADR-0176 chose no answer before the API answers.
- `restart: on-failure` for the web UI, so that the unit starts it at boot: whether dockerd starts it then too depends on its last exit code, and on a host without the unit nothing would.
- A `docker compose up` unit on the dev device: it orders the dev device's boot, not that of a host after a power cut.
