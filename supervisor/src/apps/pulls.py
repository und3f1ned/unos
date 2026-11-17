"""Docker's pull of an image as an install job reads it (ADR-0384).

The pull is Docker's ``POST /images/create``, sent through the session of docker-py's client
(``client.api.post``) and read as a stream of JSON lines, as docker-py's own ``pull`` does, but
with two things that its ``pull`` cannot give: a read timeout, and the response itself. Docker
sends a line for each step of each layer while it pulls; a pull that sends nothing for
PULL_STALL_SECONDS has stalled, and the read ends there (requests' read timeout) instead of holding
the one install that may run until the supervisor restarts. With the response, a cancel or a stop
of the supervisor can end a read that blocks (``abort``): urllib3 shuts the socket down, and
Docker ends a pull whose client went away. The catalog's images are public: the pull is sent
without credentials.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import requests
from docker.errors import APIError, DockerException
from urllib3.exceptions import ReadTimeoutError

from ..containers.docker import DOCKER_TIMEOUT_SECONDS

# A pull that sends no line for this long has stalled.
PULL_STALL_SECONDS = 600
STALLED = (
    f"Docker sent nothing about it for {PULL_STALL_SECONDS // 60} minutes, so the pull was stopped; "
    "install the app again to try once more"
)


def open_pull(client: Any, repository: str, tag: str) -> Any:
    """Ask Docker to pull ``repository`` at ``tag`` (a tag, or a digest sha256:...): the streamed
    response, whose lines ``lines`` reads. Docker's refusal before the stream (an unknown image)
    is an APIError with Docker's message; its errors otherwise are docker-py's and requests'."""
    api = client.api
    response = api.post(
        f"{api.base_url}/v{api.api_version}/images/create",
        params={"fromImage": repository, "tag": tag},
        stream=True,
        timeout=(DOCKER_TIMEOUT_SECONDS, PULL_STALL_SECONDS),
    )
    if response.status_code >= 400:
        try:
            message = (response.json() or {}).get("message") or response.reason
        except ValueError:
            message = response.reason
        response.close()
        raise APIError(f"{response.status_code} {response.reason}: {message}")
    return response


def lines(response: Any) -> Iterator[dict]:
    """The lines of a pull's stream, each one of Docker's JSON objects; DockerException for one
    that is not JSON."""
    for raw in response.iter_lines():
        if not raw:
            continue
        try:
            line = json.loads(raw)
        except ValueError as e:
            raise DockerException(f"Docker's pull stream could not be read: {e}") from e
        if isinstance(line, dict):
            yield line


def stalled(error: BaseException) -> bool:
    """Whether ``error``, from reading a pull, is its read timeout: Docker sent nothing for
    PULL_STALL_SECONDS. requests raises ReadTimeout before the answer's headers and, while it
    streams, a ConnectionError around urllib3's ReadTimeoutError."""
    if isinstance(error, requests.exceptions.ReadTimeout):
        return True
    return isinstance(error, requests.exceptions.ConnectionError) and any(
        isinstance(arg, ReadTimeoutError) for arg in error.args
    )


def abort(response: Any) -> None:
    """End a read of ``response`` that blocks in another thread: urllib3's shutdown of the socket,
    else a close. Never raises."""
    shutdown = getattr(getattr(response, "raw", None), "shutdown", None)
    try:
        if callable(shutdown):
            shutdown()
        else:
            response.close()
    except Exception:  # the read ends on its own, or has already
        pass
