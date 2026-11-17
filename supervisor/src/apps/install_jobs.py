"""The install of an app as a background job (ADR-0383).

``POST /api/v1/apps/install`` checks only what needs neither Docker, the host nor the pools, then
starts the job (``start``) and answers it at once: the job runs the install (router._install) in
a daemon thread of its own, not in the event loop's executor, which a pull of minutes would hold.
One install runs at a time on the whole NAS, and the job holds its app's lock (stacks.app_lock)
from the request to its end, so that no start, stop, restart or uninstall of the app runs
meanwhile.

The job reports its progress through ``Progress``: the step it reached, each image's pull as
Docker's pull stream reports it (its layers and the bytes of their downloads), each container's
state, then its end: the install's answer, or why it failed with the HTTP status that the request
would have answered. Every state is a new ``AppInstallJob``; ``current`` lists the running job
and the last one of each app, and ``version`` changes with each of them, so that the events
socket sends administrators the jobs whenever they change (websocket/events.py). A pull's
progress is published at most every PROGRESS_INTERVAL_SECONDS, a change of state at once.

The jobs are kept in memory only, never on the system disk: a restart of the supervisor forgets
them. A stop of the supervisor asks the running job to stop (``stop_for_shutdown``): the install
checks for it at each line of a pull, before it writes to the pool, before each create and start,
and while it waits for a companion to be ready (Progress.check), and stops there.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from docker.errors import DockerException
from fastapi import HTTPException

from ..security.validators import sanitize_error_message
from . import stacks
from .schemas import AppInstallJob, AppInstallResponse, AppTemplate, ContainerStart, ImagePull

logger = logging.getLogger(__name__)

# How often, at most, the progress of a pull is published.
PROGRESS_INTERVAL_SECONDS = 1.0
# How long a stop of the supervisor waits for the running job to end.
SHUTDOWN_WAIT_SECONDS = 5.0
# The longest that GET /apps/install/jobs/{id} waits for the end of a job.
MAX_WAIT_SECONDS = 3600
# How often a waiting GET looks at the job, in memory.
WAIT_STEP_SECONDS = 0.5

NO_SUCH_JOB = "No such install job"
STOPPED_BEFORE_ANY_CONTAINER = "A stop of the supervisor stopped the install of {name} before it made any container"


class Stopped(Exception):
    """A stop of the supervisor asked the running install to stop (Progress.check)."""


@dataclass
class _Running:
    """The install that runs: its job's id, its app's name and how to stop it."""

    job_id: str
    name: str
    stop: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None


_lock = threading.Lock()
# The running job and the last one of each app since the supervisor started, by the app's id.
_jobs: dict[str, AppInstallJob] = {}
# Changes with every new state of a job, and when a job is forgotten.
_version = 0
_running: _Running | None = None


def current() -> list[AppInstallJob]:
    """The running job and the last one of each app, the oldest first."""
    with _lock:
        return sorted(_jobs.values(), key=lambda job: job.started_at)


def version() -> int:
    with _lock:
        return _version


def get(job_id: str) -> AppInstallJob | None:
    with _lock:
        return next((job for job in _jobs.values() if job.id == job_id), None)


def _publish(job: AppInstallJob) -> None:
    global _version
    with _lock:
        _jobs[job.template_id] = job
        _version += 1


def forget(job_id: str) -> None:
    """Forget an ended job (the App Store's Dismiss): 404 for one that is not kept, 409 for the
    running one."""
    global _version
    with _lock:
        job = next((job for job in _jobs.values() if job.id == job_id), None)
        if job is None:
            raise HTTPException(404, NO_SUCH_JOB)
        if job.state == "running":
            raise HTTPException(409, f"The install of {job.name} is still running")
        del _jobs[job.template_id]
        _version += 1


async def wait_for_end(job_id: str, seconds: float) -> AppInstallJob:
    """The job once it has ended, or as it is after ``seconds``; 404 for one that is not kept.
    Looks at the job in memory every WAIT_STEP_SECONDS: no thread is held while it waits."""
    deadline = time.monotonic() + seconds
    while True:
        job = get(job_id)
        if job is None:
            raise HTTPException(404, NO_SUCH_JOB)
        if job.state != "running" or time.monotonic() >= deadline:
            return job
        await asyncio.sleep(min(WAIT_STEP_SECONDS, max(deadline - time.monotonic(), 0)))


def start(template: AppTemplate, work: Callable[[Progress], AppInstallResponse]) -> AppInstallJob:
    """Start the job that runs ``work`` (the install) and answer it as it starts, running.

    409 while an install runs, of this app or another, and while another change of this app is
    under way (stacks.app_lock). Call it on the event loop: nothing in it waits.
    """
    global _running, _version
    with _lock:
        running = _running
    if running is not None:
        if running.name == template.name:
            raise HTTPException(409, f"{template.name} is being installed already: wait for the end of its install")
        raise HTTPException(409, f"An install of {running.name} is under way: install {template.name} once it has ended")
    lock = stacks.acquire(template.id, template.name)
    job = AppInstallJob(
        id=uuid.uuid4().hex,
        template_id=template.id,
        name=template.name,
        started_at=datetime.now(timezone.utc),
        containers=[
            ContainerStart(name=stacks.container_name(template, companion.name), companion=companion.name)
            for companion in template.companions
        ] + [ContainerStart(name=stacks.container_name(template))],
    )
    with _lock:
        before = _jobs.get(template.id)
        _running = running = _Running(job_id=job.id, name=template.name)
    try:
        _publish(job)
        running.thread = threading.Thread(
            target=_run, args=(running, job, work, lock), name=f"app-install-{template.id}", daemon=True
        )
        running.thread.start()
    except BaseException as exc:  # no thread runs: free the app and the slot again
        lock.release()
        with _lock:
            _running = None
            if before is None:
                _jobs.pop(template.id, None)
            else:
                _jobs[template.id] = before
            _version += 1
        if isinstance(exc, RuntimeError):
            raise HTTPException(500, f"The install could not be started: {exc}") from exc
        raise
    return job


def _run(running: _Running, job: AppInstallJob, work: Callable[[Progress], AppInstallResponse],
         lock: threading.Lock) -> None:
    """The job's thread: the install, then its end, published once the app's lock is free."""
    global _running
    progress = Progress(job, running.stop)
    # Whatever ends the thread ends the job and frees the app and the slot: never a job that
    # stays running.
    end: dict[str, Any] = {"state": "failed", "error_status": 500, "error": "The install stopped unexpectedly"}
    try:
        result = work(progress)
        end = {"state": "succeeded", "result": result}
    except Stopped:
        end = {"state": "failed", "error_status": 503, "error": STOPPED_BEFORE_ANY_CONTAINER.format(name=job.name)}
    except HTTPException as exc:
        end = {"state": "failed", "error_status": exc.status_code, "error": str(exc.detail)}
    except Exception as exc:  # a bug in the install must still end the job
        logger.exception("The install of %s stopped on an unexpected error", job.name)
        end = {
            "state": "failed", "error_status": 500,
            "error": f"The install stopped on an unexpected error: {sanitize_error_message(exc)}",
        }
    finally:
        lock.release()
        ended = progress.job.model_copy(update={**end, "finished_at": datetime.now(timezone.utc)})
        _publish(ended)
        with _lock:
            if _running is running:
                _running = None


def stop_for_shutdown(timeout: float = SHUTDOWN_WAIT_SECONDS) -> None:
    """Ask the running install, if one runs, to stop, and wait up to ``timeout`` seconds for its
    end, so that its audit entry is written before the supervisor stops (main.py's lifespan).
    Blocking. An install blocked in a call to Docker does not end in time: the supervisor stops
    without its entry."""
    with _lock:
        running = _running
    if running is None:
        return
    running.stop.set()
    if running.thread is not None and running.thread is not threading.current_thread():
        running.thread.join(timeout)


# ---------------------------------------------------------------------------
# What the install reports
# ---------------------------------------------------------------------------


@dataclass
class _Layer:
    """One layer of a pull, as Docker's pull stream reports it."""

    total: int = 0  # its download size, once Docker reported it
    current: int = 0  # the bytes of it downloaded
    done: bool = False  # downloaded and extracted, or already there


# The lines of Docker's pull stream that are about a layer: every other line names the pull
# itself ("Pulling from library/x", the digest, the status at the end).
_NOT_A_LAYER = ("Pulling from ",)
_DOWNLOADED = ("Download complete", "Verifying Checksum", "Extracting")
_DONE = ("Pull complete", "Already exists")


class Progress:
    """What the install reports to its job, from the job's thread: each report publishes a new
    state of the job, but a pull's progress at most every PROGRESS_INTERVAL_SECONDS."""

    def __init__(self, job: AppInstallJob, stop: threading.Event) -> None:
        self.job = job
        self._stop = stop
        self._layers: dict[str, dict[str, _Layer]] = {}
        self._published_at = 0.0

    def check(self) -> None:
        """Stopped when a stop of the supervisor asked the install to stop."""
        if self._stop.is_set():
            raise Stopped()

    def _update(self, **changes: Any) -> None:
        self.job = self.job.model_copy(update=changes)
        _publish(self.job)
        self._published_at = time.monotonic()

    def step(self, step: str) -> None:
        self._update(step=step)

    def pulling(self, images: list[str]) -> None:
        """The checks passed: the install pulls ``images``, each once, in this order."""
        self._update(step="pulling", images=[ImagePull(image=ref) for ref in images])

    def image(self, ref: str, state: str) -> None:
        self._update(images=[
            image.model_copy(update={"state": state}) if image.image == ref else image for image in self.job.images
        ])

    def container(self, name: str, state: str) -> None:
        self._update(containers=[
            c.model_copy(update={"state": state}) if c.name == name else c for c in self.job.containers
        ])

    def pull_line(self, ref: str, line: dict) -> None:
        """One line of Docker's pull stream of ``ref`` (docker-py's decoded dict). An error line
        fails the pull: DockerException with Docker's message."""
        if line.get("error") or line.get("errorDetail"):
            detail = line.get("errorDetail") or {}
            raise DockerException(str(detail.get("message") or line.get("error")))
        layer_id = line.get("id")
        status = str(line.get("status") or "")
        if not layer_id or status.startswith(_NOT_A_LAYER):
            return
        layer = self._layers.setdefault(ref, {}).setdefault(str(layer_id), _Layer())
        detail = line.get("progressDetail") or {}
        if status == "Downloading":
            total, current = detail.get("total"), detail.get("current")
            if isinstance(total, int) and total > 0:
                layer.total = total
            if isinstance(current, int) and current >= 0:
                layer.current = current
        elif status in _DOWNLOADED:
            layer.current = max(layer.current, layer.total)
        elif status in _DONE:
            layer.current = max(layer.current, layer.total)
            layer.done = True
        if time.monotonic() - self._published_at >= PROGRESS_INTERVAL_SECONDS:
            self._publish_layers(ref)

    def pulled(self, ref: str, state: str) -> None:
        """The pull of ``ref`` ended (pulled or failed): its last counts, published at once."""
        self._publish_layers(ref, state=state)

    def _publish_layers(self, ref: str, state: str | None = None) -> None:
        layers = self._layers.get(ref, {}).values()
        counts = {
            "layers": len(layers),
            "layers_done": sum(layer.done for layer in layers),
            "bytes_total": sum(layer.total for layer in layers),
            "bytes_done": sum(layer.current for layer in layers),
        }
        if state is not None:
            counts["state"] = state
        self._update(images=[
            image.model_copy(update=counts) if image.image == ref else image for image in self.job.images
        ])
