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
to its end (ADR-0384), and so do an update and a rollback (ADR-0408). The supervisor runs one
process (no uvicorn workers), so a lock of the process is enough.

An update keeps the app's earlier containers, stopped, under their names with EARLIER_SUFFIX
(``immich-database.previous``), until a rollback gives them their names back or the earlier version
is removed (ADR-0408): they keep their labels, so they belong to the app (``previous``), but they
are none of its containers for a start, a stop or the listing's fields.
"""

from __future__ import annotations

import contextlib
import re
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

from docker.errors import APIError, DockerException
from fastapi import HTTPException
from requests.exceptions import RequestException
from urllib3.exceptions import HTTPError as Urllib3Error

from ..containers.docker import NO_ANSWER, no_answer_detail
from .schemas import AppTemplate, ContainerSpec

UNOS_APP_LABEL = "unos.app"
APP_POOL_LABEL = "unos.app.pool"
# A companion's name in its app, on each of its containers but the main one.
COMPANION_LABEL = "unos.app.companion"
# The catalog's version of the app that a container was created for (ADR-0408).
VERSION_LABEL = "unos.app.version"
# The end of the name of an earlier container that an update keeps (ADR-0408): a dot, which no
# app's or companion's name has.
EARLIER_SUFFIX = ".previous"

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


def is_earlier(container: Any) -> bool:
    """Whether the container is an earlier one that an update of its app keeps (ADR-0408)."""
    return str(container.name).endswith(EARLIER_SUFFIX)


def earlier_name(name: str) -> str:
    return name + EARLIER_SUFFIX


def base_name(name: str) -> str:
    """The name of an earlier container without its suffix: the name it had, and has again after
    a rollback."""
    return name[: -len(EARLIER_SUFFIX)] if name.endswith(EARLIER_SUFFIX) else name


# An image pinned by tag and digest (ADR-0381), or a container's image created by digest.
_DIGEST = re.compile(r"@(sha256:[0-9a-f]{64})$")


def pinned_digest(ref: str) -> str | None:
    """The digest of ``name:tag@sha256:...`` or ``name@sha256:...``; None for a
    container created from a tag, as before its catalog pinned the image (ADR-0410)."""
    match = _DIGEST.search(ref)
    return match.group(1) if match else None


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
    """The app that a container labelled ``unos.app`` belongs to: its template id for a companion,
    for the main container (named after it) and for an earlier container that an update kept;
    otherwise its own name, so that a container that an earlier install named otherwise stays an
    app of its own."""
    labels = container.labels or {}
    app_id = labels.get(UNOS_APP_LABEL, "")
    if COMPANION_LABEL in labels or container.name == app_id or (is_earlier(container) and app_id):
        return app_id
    return container.name


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
    # The earlier containers that the app's last update kept, stopped (ADR-0408).
    previous: list[Any] = field(default_factory=list)

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
    # The earlier containers in the start order of their app, those the template no longer names after them.
    order = {earlier_name(name): n for n, name in enumerate(container_names(template))} if template else {}
    previous = sorted((c for c in members if is_earlier(c)), key=lambda c: (order.get(c.name, len(order)), c.name))
    members = [c for c in members if not is_earlier(c)]
    main = next((c for c in members if COMPANION_LABEL not in (c.labels or {})), None)
    by_name = {c.labels[COMPANION_LABEL]: c for c in members if COMPANION_LABEL in (c.labels or {})}
    ordered = [by_name.pop(c.name) for c in (template.companions if template else []) if c.name in by_name]
    companions = [*ordered, *sorted(by_name.values(), key=lambda c: c.name)]
    present = {c.name for c in members}
    missing = [name for name in container_names(template) if name not in present] if template else []
    return InstalledContainers(
        key=key, template=template, main=main, companions=companions, missing=missing, previous=previous
    )


def app_version(app: InstalledContainers) -> str | None:
    """The catalog's version that the app's main container was created for, from its label; None
    for one created before the label (ADR-0408)."""
    container = app.main if app.main is not None else next(iter(app.companions), None)
    return (container.labels or {}).get(VERSION_LABEL) if container is not None else None


def update_to(app: InstalledContainers) -> str | None:
    """The catalog's version that the app can be updated to, or None (ADR-0408): it is complete and
    the catalog pins for one of its containers another
    image than the one it was created from (its own or that of a hardware profile; which one the
    host takes is the update's to read). Reads nothing but the containers' configuration."""
    template = app.template
    if template is None or app.missing or not app.containers:
        return None
    for container in app.containers:
        spec = app.spec(container)
        if spec is None:
            return template.version
        wanted = {pinned_digest(ref) for ref in [spec.image, *(p.image for p in spec.hardware if p.image)]}
        installed = pinned_digest(str((container.attrs.get("Config") or {}).get("Image") or ""))
        if installed not in wanted:
            return template.version
    return None


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


def _answers(container: Any, command: list[str]) -> tuple[bool, str | None]:
    """Whether ``command``, run in the running ``container`` (Docker's exec), exits 0, and the
    start of its output for a message. A container that Docker says does not run (409: it stopped,
    restarts or is paused meanwhile) has not answered and said nothing (None); the next look at it
    says why. docker-py reads the exec's output without a timeout: the command bounds its own run
    (the catalog's ready commands carry one, ADR-0415)."""
    try:
        result = container.exec_run(command, stdout=True, stderr=True)
    except APIError as e:
        if e.status_code == 409:
            return False, None
        raise
    output = (result.output or b"").decode(errors="replace")
    text = " ".join("".join(ch if ch.isprintable() else " " for ch in output).split())
    return result.exit_code == 0, text[:HEALTH_OUTPUT_CHARS]


def wait_ready(container: Any, *, started_now: bool, check: Callable[[], None] | None = None,
               seconds: float = DEPENDENCY_READY_SECONDS, unhealthy_fails: bool = True,
               ready_command: list[str] | None = None) -> None:
    """Wait until ``container`` is ready for the containers that depend on it: healthy when it has
    a health check; otherwise, with a ``ready_command`` (ContainerSpec.ready_command, ADR-0415),
    running and the command, run in it at each look, exiting 0; otherwise running and not
    restarting (for POLL_SECONDS after a start that this change made). NotReady at once for one
    that exited, died or (with ``unhealthy_fails``) is unhealthy, and when it is not ready within
    ``seconds`` (a ready command's last run may end up to its own timeout later), with the last
    output of its command. Its errors are Docker's (DOCKER_ERRORS).
    ``check`` is called before each look at the container: an install job's stop (ADR-0384). An
    update waits for its new main container with ``unhealthy_fails`` False: a version that migrates
    its database at its start fails its health check meanwhile (ADR-0408)."""
    began = time.monotonic()
    said = ""  # the last output of the ready command
    while True:
        if check is not None:
            check()
        container.reload()
        state = _state(container)
        status = health(container)
        if state.get("Status") in ("exited", "dead"):
            raise NotReady(f"{container.name} stopped (exit code {state.get('ExitCode')})")
        if status == "unhealthy" and unhealthy_fails:
            output = _last_health_output(container)
            raise NotReady(f"{container.name} is unhealthy" + (f": {output}" if output else ""))
        running = bool(state.get("Running")) and not state.get("Restarting")
        waited = time.monotonic() - began
        if status == "healthy":
            return
        if status is None and running and ready_command is not None:
            answered, answer = _answers(container, ready_command)
            if answered:
                return
            said = said if answer is None else answer
            waited = time.monotonic() - began
        elif status is None and running and (not started_now or waited >= POLL_SECONDS):
            return
        if waited >= seconds:
            code = state.get("ExitCode")
            raise NotReady(
                f"{container.name} did not become ready within {seconds:g} s"
                + (f" (exit code {code})" if code else "")
                + (f": {said}" if said else "")
            )
        time.sleep(POLL_SECONDS)
