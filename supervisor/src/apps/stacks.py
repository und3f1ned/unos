"""The containers of an app as one app (ADR-0381).

An app is its main container, named after its template id, and the companion containers of its
template, each named "<app id>-<companion>" and labelled ``unos.app.companion``; every container
of an app carries ``unos.app=<app id>``. The containers of an app with companions share the bridge
network ``unos-app-<app id>``, on which each companion answers to its name. Companions start in
the catalog's order and the main container last, and each companion that the main container
depends on is ready before the next container starts (so one that fails to start later meets a
database set up); they stop in the reverse order. A container that the catalog's template has
and Docker lacks makes the app incomplete (``missing``): it does not start, and an uninstall
removes the rest.

One change of an app runs at a time: an install, a start, a stop, a restart or an uninstall of
an app that another one is changing answers 409; an install job holds the lock from its request
to its end (ADR-0384). The supervisor runs one process (no uvicorn workers), so a lock of the
process is enough.
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

from docker.errors import DockerException
from fastapi import HTTPException
from requests.exceptions import RequestException
from urllib3.exceptions import HTTPError as Urllib3Error

from ..containers.docker import NO_ANSWER, no_answer_detail
from .schemas import AppTemplate, ContainerSpec

UNOS_APP_LABEL = "unos.app"
APP_POOL_LABEL = "unos.app.pool"
# A companion's name in its app, on each of its containers but the main one.
COMPANION_LABEL = "unos.app.companion"

# docker-py raises DockerException subclasses, but connection problems can
# surface as plain requests exceptions, and those of a stream (a pull) as urllib3's.
DOCKER_ERRORS = (DockerException, RequestException, Urllib3Error)

# How long the main container waits for a companion it depends on to be ready, and how often the
# companion is looked at meanwhile.
DEPENDENCY_READY_SECONDS = 180
POLL_SECONDS = 2
# The seconds a stop gives a container whose spec names none: Docker's own default, named so
# that docker-py extends its request's timeout by them.
DEFAULT_STOP_TIMEOUT = 10
# The most of a health check's output that a message quotes.
HEALTH_OUTPUT_CHARS = 200


def docker_error(error: Exception) -> HTTPException:
    """The answer to an error of the first request of a route to Docker (the listing, the lookup
    of the app, the listing of an install and those that follow it before anything changed): 503
    with the reason when Docker did not answer (NO_ANSWER), 500 otherwise (ADR-0323). An
    operation that fails after it keeps its own 500 "Failed to ..." (ADR-0254, ADR-0256)."""
    if isinstance(error, NO_ANSWER):
        return HTTPException(503, no_answer_detail(error))
    return HTTPException(500, f"Docker failed: {error}")


def network_name(app_id: str) -> str:
    """The bridge network of an app with companions."""
    return f"unos-app-{app_id}"


def container_name(template: AppTemplate, companion: str | None = None) -> str:
    """The Docker name of the main container (``companion`` None) or of a companion."""
    return template.id if companion is None else f"{template.id}-{companion}"


def container_names(template: AppTemplate) -> list[str]:
    """The Docker names of the app's containers in their start order: the companions, then the
    main container."""
    return [*(container_name(template, c.name) for c in template.companions), template.id]


def joined(names: Iterable[str]) -> str:
    """``names`` as a list in a sentence: "a", "a and b", "a, b and c"."""
    items = list(names)
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


# ---------------------------------------------------------------------------
# One change of an app at a time
# ---------------------------------------------------------------------------

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def acquire(key: str, name: str) -> threading.Lock:
    """Take the lock of the app ``key`` (its template id, or the name of an app's container that
    no template names) and return it, for its holder to release, also from another thread (an
    install job, ADR-0384); 409 naming the app ``name`` while another holds it."""
    with _locks_guard:
        lock = _locks.setdefault(key, threading.Lock())
    if not lock.acquire(blocking=False):
        raise HTTPException(409, f"Another change of {name} is under way; try again when it has finished")
    return lock


@contextlib.contextmanager
def app_lock(key: str, name: str) -> Iterator[None]:
    """Hold the lock of the app ``key`` for one change (``acquire``)."""
    lock = acquire(key, name)
    try:
        yield
    finally:
        lock.release()


# ---------------------------------------------------------------------------
# The containers of an installed app
# ---------------------------------------------------------------------------


def app_key(container: Any) -> str:
    """The app that a container labelled ``unos.app`` belongs to: its template id for a companion
    or for the main container (named after it); otherwise its own name, so that a container that
    an earlier install named otherwise stays an app of its own."""
    labels = container.labels or {}
    app_id = labels.get(UNOS_APP_LABEL, "")
    return app_id if COMPANION_LABEL in labels or container.name == app_id else container.name


@dataclass
class InstalledContainers:
    """The containers of one installed app, as Docker has them."""

    # The app's name: its template id, or the name of a container that no template names.
    key: str
    # The catalog's template of the app (None: no template, or not under this name).
    template: AppTemplate | None
    main: Any | None
    # In the catalog's order; companions that the template no longer names after them.
    companions: list[Any] = field(default_factory=list)
    # The names of the containers that the template has and Docker lacks.
    missing: list[str] = field(default_factory=list)

    @property
    def containers(self) -> list[Any]:
        """The containers in their start order: the companions, then the main container."""
        return [*self.companions, *([self.main] if self.main is not None else [])]

    @property
    def several(self) -> bool:
        """Whether the app is of several containers: its texts name each container."""
        return bool(self.companions or (self.template is not None and self.template.companions))

    @property
    def name(self) -> str:
        return self.template.name if self.template is not None else self.key

    def spec(self, container: Any) -> ContainerSpec | None:
        """The catalog's spec of one of the app's containers (None when the catalog has none)."""
        if self.template is None:
            return None
        companion = (container.labels or {}).get(COMPANION_LABEL)
        if companion is None:
            return self.template
        return next((c for c in self.template.companions if c.name == companion), None)

    def stop_timeout(self, container: Any) -> int:
        """The seconds a stop gives ``container``: its spec's, else Docker's default."""
        spec = self.spec(container)
        return spec.stop_timeout if spec is not None and spec.stop_timeout is not None else DEFAULT_STOP_TIMEOUT


def group(key: str, members: list[Any], template: AppTemplate | None) -> InstalledContainers:
    """The app ``key`` of the containers ``members`` (all with that key); ``template`` is the
    catalog's template of its ``unos.app`` label, used only when the key is the template's id."""
    if template is not None and template.id != key:
        template = None
    main = next((c for c in members if COMPANION_LABEL not in (c.labels or {})), None)
    by_name = {c.labels[COMPANION_LABEL]: c for c in members if COMPANION_LABEL in (c.labels or {})}
    ordered = [by_name.pop(c.name) for c in (template.companions if template else []) if c.name in by_name]
    companions = [*ordered, *sorted(by_name.values(), key=lambda c: c.name)]
    present = {c.name for c in members}
    missing = [name for name in container_names(template) if name not in present] if template else []
    return InstalledContainers(key=key, template=template, main=main, companions=companions, missing=missing)


def missing_detail(app: InstalledContainers) -> str:
    return f"{app.name} is incomplete (missing: {', '.join(app.missing)})"


# ---------------------------------------------------------------------------
# Before an install
# ---------------------------------------------------------------------------


def check_not_installed(template: AppTemplate, containers: Iterable[Any]) -> None:
    """409 when the app is installed, complete or not, or when another container has the name of
    one of its containers; ``containers`` are all of Docker's."""
    names = container_names(template)
    containers = list(containers)
    labelled = [c for c in containers if (c.labels or {}).get(UNOS_APP_LABEL) == template.id]
    if labelled:
        own = [c for c in labelled if app_key(c) == template.id]
        missing = [name for name in names if name not in {c.name for c in own}] if own else []
        if missing:
            raise HTTPException(
                409,
                f"{template.name} is already installed, incomplete (missing: {', '.join(missing)}): "
                "uninstall it first (its data folders are kept)",
            )
        raise HTTPException(409, f"{template.name} is already installed")
    for container in containers:
        if container.name in names:
            raise HTTPException(409, f"A container named '{container.name}' already exists")


def reusable_network(client: Any, template: AppTemplate) -> Any | None:
    """The app's network left from an earlier install, to use again (None: there is none); 409
    for a network of its name that is not the app's, or that containers still use. Its errors
    are Docker's (DOCKER_ERRORS)."""
    name = network_name(template.id)
    # Docker's name filter matches parts of names: only the exact name counts.
    for network in client.networks.list(names=[name], greedy=True):
        if network.name != name:
            continue
        attrs = network.attrs or {}
        if (attrs.get("Labels") or {}).get(UNOS_APP_LABEL) != template.id:
            raise HTTPException(409, f"A Docker network named '{name}' already exists")
        users = sorted(str(c.get("Name", "")) for c in (attrs.get("Containers") or {}).values())
        if users:
            raise HTTPException(
                409, f"The Docker network '{name}' of {template.name} is used by {joined(users)}: remove them first"
            )
        return network
    return None


# ---------------------------------------------------------------------------
# Starting in order
# ---------------------------------------------------------------------------


class NotReady(Exception):
    """A companion that the main container depends on did not become ready."""


def _state(container: Any) -> dict:
    return (container.attrs or {}).get("State") or {}


def health(container: Any) -> str | None:
    """Docker's State.Health.Status of the container; None for one without a health check."""
    status = (_state(container).get("Health") or {}).get("Status")
    return str(status) if status else None


def _last_health_output(container: Any) -> str:
    log = (_state(container).get("Health") or {}).get("Log") or []
    output = str(log[-1].get("Output", "")) if log and isinstance(log[-1], dict) else ""
    text = " ".join("".join(ch if ch.isprintable() else " " for ch in output).split())
    return text[:HEALTH_OUTPUT_CHARS]


def wait_ready(container: Any, *, started_now: bool, check: Callable[[], None] | None = None) -> None:
    """Wait until ``container`` is ready for the containers that depend on it: healthy when it has
    a health check, otherwise running and not restarting (for POLL_SECONDS after a start that this
    change made). NotReady at once for one that exited, died or is unhealthy, and when it is not
    ready within DEPENDENCY_READY_SECONDS. Its errors are Docker's (DOCKER_ERRORS). ``check`` is
    called before each look at the container: an install job's stop (ADR-0384)."""
    began = time.monotonic()
    while True:
        if check is not None:
            check()
        container.reload()
        state = _state(container)
        status = health(container)
        if state.get("Status") in ("exited", "dead"):
            raise NotReady(f"{container.name} stopped (exit code {state.get('ExitCode')})")
        if status == "unhealthy":
            output = _last_health_output(container)
            raise NotReady(f"{container.name} is unhealthy" + (f": {output}" if output else ""))
        running = bool(state.get("Running")) and not state.get("Restarting")
        waited = time.monotonic() - began
        if status == "healthy" or (status is None and running and (not started_now or waited >= POLL_SECONDS)):
            return
        if waited >= DEPENDENCY_READY_SECONDS:
            code = state.get("ExitCode")
            raise NotReady(
                f"{container.name} did not become ready within {DEPENDENCY_READY_SECONDS} s"
                + (f" (exit code {code})" if code else "")
            )
        time.sleep(POLL_SECONDS)
