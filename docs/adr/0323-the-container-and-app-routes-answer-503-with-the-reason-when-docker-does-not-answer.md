---
status: accepted
date: 2026-11-10
area: apps
decided-by: agent
---

# ADR-0323: The container and app routes answer 503 with the reason when Docker does not answer

## Context

While Docker could not be reached, the container API answered HTTP 500 with docker-py's error, and the Apps API answered 500 `Docker is not available: ...`, for errors that Docker answered too. ADR-0175 deferred the 503, and ADR-0254 left it to `apps.docker-down-503`. The storage API already answers 503 when Docker cannot be asked which containers use a pool or a volume (`DockerUnavailableError` of `supervisor/src/storage/pools.py`). Under the error, the Containers page showed counts of 0, its search and "No containers found" with a Create button. The agent decided this under the owner's delegation ("work on through the features, ask no questions; I allow everything", in translation).

## Decision

- `supervisor/src/containers/docker.py`:
  - `get_client` raises `DockerUnavailable`, a `DockerException`, when the request for Docker's API version that `docker.from_env` makes gets no answer: the socket is missing or refuses the connection. An error that Docker answers it with (`APIError`), or an answer without the version, is passed on as docker-py raises it.
  - `NO_ANSWER` holds the errors of a request that Docker did not answer: `DockerUnavailable`; requests' `ConnectionError`, `ConnectTimeout` included; requests' `ChunkedEncodingError`; and urllib3's `ProtocolError`, from a stream such as a pull or a log.
  - An answer that did not come within `DOCKER_TIMEOUT_SECONDS` (`ReadTimeout`) is not one of them: a stop waits for the container, which can take longer (ADR-0254).
- The containers API answers an error of `NO_ANSWER` with 503 on every route (`_docker` of `supervisor/src/containers/router.py`), as ADR-0254 left to this entry. The detail is `Docker is not available: <docker-py's reason>` (`no_answer_detail`). Its other errors keep their status: 409 for conflicts (ADR-0254, ADR-0255), 404 for what does not exist, 500 otherwise.
- The Apps API answers 503 when the first request of a route to Docker meets an error of `NO_ANSWER`: the listing of the installed apps, the lookup of an app to start, stop, restart or uninstall, and the name check of an install (`_docker_error` of `supervisor/src/apps/router.py`). Other errors of these requests answer 500 `Docker failed: ...`; they said "Docker is not available" before.
- An operation that fails after that first request keeps its own 500 `Failed to ...`, also when the connection broke during it (ADR-0254, ADR-0256). `_DOCKER_ERRORS` takes urllib3's errors too, so a pull whose stream breaks answers 500 `Failed to pull image: ...` and is no longer an unhandled 500.
- The web UI shows the server's message, as before. The Containers tab keeps the error of a list that could not be loaded apart (`loadError`) and shows it alone: no counts, search, empty list or Create button. The Images tab already showed only its error (`ImageList.svelte`).
- Tests:
  - `TestADockerThatDoesNotAnswer` of `supervisor/tests/test_containers.py` and `supervisor/tests/test_apps.py`:
    - each route without a socket;
    - a connection that broke, in each form that docker-py passes on;
    - a read timeout that keeps 500;
    - an error answer to the version query that is no 503;
    - an operation of the Apps API that keeps its 500.
  - On the stand-in, which has no Docker socket, `webui/tests/api.spec.ts` checks the 503 and the reason of GET `/containers`, `/containers/images` and `/apps/installed`. `scripts/standin/run_supervisor.py` points `DOCKER_HOST` at a socket under its data that does not exist.
  - The "Containers without Docker (stand-in)" specs of `webui/tests/containers.spec.ts` check the Containers tab, the Images tab and the Apps page there.
  - Both skip on the compose test stack, which reaches the host's Docker. The condition is `!process.env.E2E_STANDIN_STATE`, which `SKIP_CONDITIONS` of `supervisor/tests/test_standin.py` lists (ADR-0317), as the stack skips the specs that need Docker (ADR-0215).

## Consequences

- A client can tell a Docker that cannot be reached (503) from a request that Docker refused or did not finish (4xx, 500).
- A Docker that takes connections but does not answer within the timeout, such as a hung `dockerd`, gives 500 with requests' "Read timed out", not 503.
- A bad `DOCKER_HOST` or TLS setting, from which no client can be made, also answers 503 with docker-py's reason.
- Until `apps.catalog-without-docker`, the Apps page hides the catalog when the installed apps cannot be loaded. Under the error it shows "0 apps available, 0 installed", "No apps found" and "No apps installed".
- The dashboard does not show the 503. The WebSocket of a container's log (ADR-0255) and the health check's count of running containers report a Docker that does not answer in their own ways, as before.
- On the dev device, a stopped Docker stops UNOS with it. The stand-in is the test host of the 503, and the backend tests show the other cases.
