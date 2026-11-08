---
status: accepted
date: 2026-11-07
area: apps
decided-by: agent
---

# ADR-0254: Docker calls of the containers and apps APIs run in worker threads within the Docker client's timeout, and the containers API answers Docker's conflicts with 409

## Context

ADR-0021 keeps blocking work off the event loop, with a timeout, but most routes of `supervisor/src/containers/router.py` and the stop and uninstall of the Apps API still called docker-py on the event loop: while an image was pulled or a container stopped (up to 10 s), the whole API waited, `GET /api/v1/health` and the live stats included. docker-py gives each request to Docker a timeout, except a pull, whose length depends on the image. Every Docker error answered 500, also Docker's 409 for an image that a container uses.

## Decision

- Every route of `supervisor/src/containers/router.py` runs its operation of `supervisor/src/containers/docker.py` in a worker thread (`_docker`, `asyncio.to_thread`), and so do the stop and the uninstall of `supervisor/src/apps/router.py`, as the install, start, restart and list of apps already did.
- The timeout is the Docker client's: `get_client()` makes the client with `DOCKER_TIMEOUT_SECONDS = 60` (docker-py's default, made explicit), which bounds each request to the Docker API of both routers; a stop waits the container's stop timeout (10 s) on top. A pull has no deadline: docker-py sends it without a timeout, and a large image on a slow link takes minutes. It holds its worker thread until Docker answers, as the pull of an app install does.
- In the containers API, a conflict that Docker reports (409: a name that is taken, a running container removed without force, an image that Docker refuses to delete) answers 409 with Docker's reason as the detail, and so does an image that a container uses, which the image removal refuses itself (ADR-0255). Its other Docker errors answer 500 as before; 503 while Docker does not answer is `apps.docker-down-503`. The Apps API keeps its own answers: every Docker error of a stop or an uninstall is a 500 "Failed to stop: ..." or "Failed to remove: ..." (ADR-0256).
- Tests: `supervisor/tests/test_containers.py` (`TestDockerCallsOffTheEventLoop`: every route, checked against the router's own list, makes its Docker client in a worker thread; `TestRequestsAnsweredWhileDockerBlocks`: under a real uvicorn, `GET /api/v1/health` and `GET /api/v1/apps/catalog` answer while a pull, a stop, a removal or a listing blocks in Docker; `TestImageRemoval`) and `supervisor/tests/test_apps.py` (`TestStopAndUninstall`).

## Consequences

- A slow or stuck Docker holds one worker thread per request, for up to `DOCKER_TIMEOUT_SECONDS` (a stop 10 s longer), and a pull for as long as it runs; the event loop and the other requests go on.
- The threads come from the default executor, which the other blocking work (bcrypt, host commands) shares: many pulls at once could make that work wait for a free thread. A dedicated executor for Docker would bound this; one administrator does not need it.
