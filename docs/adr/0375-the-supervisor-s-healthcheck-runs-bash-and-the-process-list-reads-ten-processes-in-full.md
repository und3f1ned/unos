---
status: accepted
date: 2026-11-13
area: system
decided-by: agent
---

# ADR-0375: The supervisor's healthcheck runs bash, and the process list reads ten processes in full

## Context

After ADR-0374 the supervisor of the owner's BPI-F3 still used 15.5% of a core on average with a
dashboard open, above the budget of 15%, and peaks of 40 to 46% every ten seconds with none open.
Measured in the container on the K1: the healthcheck of `deploy/docker-compose.yml`, a Python that
imported `urllib.request` to ask for `/api/v1/health`, took 0.5 s of CPU every 10 s, `http.client`
alone 0.42 s and the interpreter alone 0.09 s, while bash asking over `/dev/tcp` took 0.01 s. The
process list of the dashboard read name, memory, status and user of each of 311 processes:
0.41 s of CPU, of which the CPU shares alone were 0.13 s.

## Decision

- The supervisor's healthcheck is `bash -c`: it parses the health URL with parameter expansion,
  sends `GET` over `/dev/tcp`, waits for the status line with `read -t 5` and exits 0 for 200,
  else 1. It uses no proxy, as before. Compose reads `$$` in it as `$`.
- `_top_processes` of `supervisor/src/websocket/events.py` asks psutil for the CPU share of every
  process and for name, memory, status and user of the ten with the largest share only. The
  message keeps its shape.
- Tests: `test_supervisor_is_healthy_only_while_the_api_answers` of
  `supervisor/tests/test_deploy_scripts.py` runs the healthcheck with bash against a server that
  answers 200, 503 or refuses, and `test_only_the_listed_processes_are_read_in_full` of
  `supervisor/tests/test_ws_stats.py` checks what the listing asks for.

## Consequences

- The supervisor's image must keep bash with network redirections; Debian's bash, in
  `python:3.11-slim`, has them.
- A health request that connects but never answers fails after 5 s; a connection that hangs
  instead is ended by Docker's timeout of 10 s.
- The budget and the margin of ADR-0374 stay; a later healthcheck or listing is measured against
  them.
