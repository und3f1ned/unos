"""The install of an app as a background job (ADR-0384).

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
them. A stop of the supervisor (``stop_for_shutdown``) and an administrator's cancel (``cancel``)
ask the running job to stop: the install checks for it at each line of a pull, before it writes to
the pool, before the network and each create and start, and while it waits for a companion to be
ready (Progress.check), and a pull's read that blocks is ended at once (pulls.abort). A stop of
the supervisor leaves what the install made; a cancel removes it, as a failure does
(router._create_and_start).

An update of an app to the catalog's newer images and a rollback to the earlier version that an
update kept are jobs of the same kind (``kind``, src/apps/updates.py, ADR-0408): they take the same
slot, so that one install, update or rollback runs at a time, hold the app's lock and report the
same way. A rollback cannot be cancelled.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, UTC
from typing import Any, Literal

from docker.errors import DockerException
from fastapi import HTTPException

from ..security.validators import sanitize_error_message
from . import pulls, stacks
from .schemas import AppInstallJob, AppInstallResponse, AppTemplate, ContainerStart, ImagePull

logger = logging.getLogger(__name__)

# What a job does: AppInstallJob.kind.
JobKind = Literal["install", "update", "rollback"]

# How often, at most, the progress of a pull is published.
PROGRESS_INTERVAL_SECONDS = 1.0
# How long a stop of the supervisor waits for the running job to end.
SHUTDOWN_WAIT_SECONDS = 5.0
# The longest that GET /apps/install/jobs/{id} waits for the end of a job, and how many such GETs
# may wait at once: each holds a connection, which counts against uvicorn's --limit-concurrency
# (ADR-0333).
MAX_WAIT_SECONDS = 3600
MAX_WAITERS = 4
# How often a waiting GET looks at the job, in memory.
WAIT_STEP_SECONDS = 0.5

NO_SUCH_JOB = "No such install job"
TOO_MANY_WAITERS = "Too many requests wait for install jobs; try again in a moment"
STOPPED_BEFORE_ANY_CONTAINER = "A stop of the supervisor stopped the install of {name} before it made any container"
CANCELLED_BEFORE_ANY_CONTAINER = "Cancelled by {who} before any container of {name} was made"
STOPPED_BEFORE_ANY_CHANGE = "A stop of the supervisor stopped the {noun} of {name} before it changed anything"
CANCELLED_BEFORE_ANY_CHANGE = "Cancelled by {who} before {name} was stopped: nothing was changed"
# A kind of job: its noun with its article, its verb and its past participle.
_WORDS = {
    "install": ("An install", "install", "installed"),
    "update": ("An update", "update", "updated"),
    "rollback": ("A rollback", "roll back", "rolled back"),
}


class Stopped(Exception):
    """A stop of the supervisor, or with ``cancelled_by`` an administrator's cancel, asked the
    running install to stop (Progress.check)."""

    def __init__(self, cancelled_by: str | None = None) -> None:
        super().__init__(cancelled_by or "a stop of the supervisor")
        self.cancelled_by = cancelled_by


@dataclass
class _Running:
    """The install that runs: its job's id, its app's name, how to stop it and who cancelled it."""

    job_id: str
    name: str
    kind: JobKind = "install"
    stop: threading.Event = field(default_factory=threading.Event)
    cancelled_by: str | None = None
    # A stop of the supervisor: it wins over a cancel, whose clean-up would not fit in Docker's 10 s.
    shutting_down: bool = False
    thread: threading.Thread | None = None
    progress: Progress | None = None

    def ask_to_stop(self) -> None:
        """Set the stop and end a pull's read that blocks."""
        self.stop.set()
        if self.progress is not None:
            self.progress.abort()


# Reentrant: a job's start publishes it while holding it.
_lock = threading.RLock()
# The running job and the last one of each app since the supervisor started, by the app's id.
_jobs: dict[str, AppInstallJob] = {}
# Changes with every new state of a job, and when a job is forgotten.
_version = 0
_running: _Running | None = None
# The GETs that wait for a job's end now.
_waiters = 0


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
    """Keep ``job`` as its app's job: a new job, or a new state of the one kept. A state of a job
    that another one of the app replaced meanwhile is dropped."""
    global _version
    with _lock:
        kept = _jobs.get(job.template_id)
        if kept is not None and kept.id != job.id and kept.state == "running":
            return
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
    """The job once it has ended, or as it is after ``seconds``; 404 for one that is not kept, 429
    while MAX_WAITERS others wait. Looks at the job in memory every WAIT_STEP_SECONDS: no thread
    is held while it waits."""
    global _waiters
    if seconds <= 0:
        job = get(job_id)
        if job is None:
            raise HTTPException(404, NO_SUCH_JOB)
        return job
    with _lock:
        if _waiters >= MAX_WAITERS:
            raise HTTPException(429, TOO_MANY_WAITERS)
        _waiters += 1
    try:
        deadline = time.monotonic() + seconds
        while True:
            job = get(job_id)
            if job is None:
                raise HTTPException(404, NO_SUCH_JOB)
            if job.state != "running" or time.monotonic() >= deadline:
                return job
            await asyncio.sleep(min(WAIT_STEP_SECONDS, max(deadline - time.monotonic(), 0)))
    finally:
        with _lock:
            _waiters -= 1


def cancel(job_id: str, who: str) -> AppInstallJob:
    """Ask the running job ``job_id`` to stop as ``who``'s cancel: the job as it is now, still
    running until the install reaches its next step. 404 for a job that does not run."""
    with _lock:
        running = _running
        job = get(job_id)
        if running is None or job is None or running.job_id != job_id or job.state != "running":
            raise HTTPException(404, NO_SUCH_JOB)
        if running.kind == "rollback":
            raise HTTPException(409, f"The rollback of {job.name} cannot be cancelled: it ends on its own")
        if running.cancelled_by is None:
            running.cancelled_by = who
    running.ask_to_stop()
    return job


def dismiss_or_cancel(job_id: str, who: str) -> AppInstallJob | None:
    """DELETE of a job: the running one is cancelled (``cancel``, the job as it is), one that ended
    is forgotten (None). 404 for a job that is not kept."""
    with _lock:
        job = get(job_id)
        if job is None:
            raise HTTPException(404, NO_SUCH_JOB)
        if job.state != "running":
            forget(job_id)
            return None
    return cancel(job_id, who)


def start(template: AppTemplate, work: Callable[[Progress], AppInstallResponse], *, kind: JobKind = "install",
          containers: list[str] | None = None) -> AppInstallJob:
    """Start the job that runs ``work`` (the install, an update or a rollback: ``kind``) and answer
    it as it starts, running. ``containers``: the Docker names of the containers that it makes or
    starts, in their start order (default: those of the template).

    409 while a job runs, of this app or another, and while another change of this app is under
    way (stacks.app_lock). Call it on the event loop: nothing in it waits.
    """
    global _running, _version
    # The check of the slot, the app's lock and the job are taken in one hold of _lock: no other
    # start can come between them.
    with _lock:
        running = _running
        if running is not None:
            article, _, past = _WORDS[running.kind]
            if running.name == template.name:
                raise HTTPException(
                    409, f"{template.name} is being {past} already: wait for the end of its {_noun(running.kind)}"
                )
            raise HTTPException(
                409, f"{article} of {running.name} is under way: {_WORDS[kind][1]} {template.name} once it has ended"
            )
        lock = stacks.acquire(template.id, template.name)
        before = _jobs.get(template.id)
        try:
            names = containers if containers is not None else stacks.container_names(template)
            companions = {stacks.container_name(template, c.name): c.name for c in template.companions}
            job = AppInstallJob(
                id=uuid.uuid4().hex,
                template_id=template.id,
                name=template.name,
                kind=kind,
                started_at=datetime.now(UTC),
                containers=[ContainerStart(name=name, companion=companions.get(name)) for name in names],
            )
            _running = running = _Running(job_id=job.id, name=template.name, kind=kind)
            _publish(job)
            running.thread = threading.Thread(
                target=_run, args=(running, job, work, lock), name=f"app-install-{template.id}", daemon=True
            )
            running.thread.start()
        except BaseException as exc:  # no thread runs: free the app and the slot again
            lock.release()
            _running = None
            if before is None:
                _jobs.pop(template.id, None)
            else:
                _jobs[template.id] = before
            _version += 1
            if isinstance(exc, RuntimeError):
                logger.exception("The install could not be started")
                raise HTTPException(500, f"The install could not be started: {exc}") from exc
            raise
    return job


def _run(running: _Running, job: AppInstallJob, work: Callable[[Progress], AppInstallResponse],
         lock: threading.Lock) -> None:
    """The job's thread: the install, then its end, published once the app's lock and the slot
    are free. A cancel that comes while the install fails anyway ends it cancelled, with the
    failure's message: what the install made is removed either way."""
    global _running
    progress = Progress(job, running)
    running.progress = progress
    # Whatever ends the thread ends the job and frees the app and the slot: never a job that
    # stays running.
    end: dict[str, Any] = {"state": "failed", "error_status": 500, "error": "The install stopped unexpectedly"}
    try:
        result = work(progress)
        end = {"state": "succeeded", "result": result}
    except Stopped as exc:
        install = job.kind == "install"
        if exc.cancelled_by is not None:
            text = CANCELLED_BEFORE_ANY_CONTAINER if install else CANCELLED_BEFORE_ANY_CHANGE
            end = {"state": "cancelled", "error": text.format(who=exc.cancelled_by, name=job.name)}
        else:
            text = STOPPED_BEFORE_ANY_CONTAINER if install else STOPPED_BEFORE_ANY_CHANGE
            end = {"state": "failed", "error_status": 503, "error": text.format(name=job.name, noun=_noun(job.kind))}
    except HTTPException as exc:
        if running.cancelled_by is not None and not running.shutting_down:
            end = {"state": "cancelled", "error": str(exc.detail)}
        else:
            end = {"state": "failed", "error_status": exc.status_code, "error": str(exc.detail)}
    except Exception as exc:  # a bug in the install must still end the job
        noun = _noun(job.kind)
        logger.exception("The %s of %s stopped on an unexpected error", noun, job.name)
        end = {
            "state": "failed", "error_status": 500,
            "error": f"The {noun} stopped on an unexpected error: {sanitize_error_message(exc)}",
        }
    finally:
        try:
            lock.release()
        finally:
            with _lock:
                if _running is running:
                    _running = None
        if end["state"] == "failed":
            # The request's own answer is 202: the supervisor's log is where a failure shows.
            logger.warning("The %s of %s failed (%s): %s", _noun(job.kind), job.name, end["error_status"], end["error"])
        ended = progress.job.model_copy(update={**end, "finished_at": datetime.now(UTC)})
        _publish(ended)


def _noun(kind: str) -> str:
    return "rollback" if kind == "rollback" else kind


def stop_for_shutdown(timeout: float = SHUTDOWN_WAIT_SECONDS) -> None:
    """Ask the running install, if one runs, to stop, and wait up to ``timeout`` seconds for its
    end, so that its audit entry is written before the supervisor stops (main.py's lifespan).
    Blocking. An install blocked in a call to Docker does not end in time: the supervisor stops
    without its entry."""
    with _lock:
        running = _running
    if running is None:
        return
    running.shutting_down = True
    running.ask_to_stop()
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

    def __init__(self, job: AppInstallJob, running: _Running) -> None:
        self.job = job
        self._running = running
        self._layers: dict[str, dict[str, _Layer]] = {}
        self._published_at = 0.0
        # The response of the pull being read, which ``abort`` can end from another thread.
        self._response: Any = None

    @property
    def shutting_down(self) -> bool:
        """Whether a stop of the supervisor asked the job to stop."""
        return self._running.shutting_down

    def check(self) -> None:
        """Stopped when a stop of the supervisor or a cancel asked the install to stop."""
        if self._running.stop.is_set():
            raise Stopped(None if self._running.shutting_down else self._running.cancelled_by)

    def reading(self, response: Any) -> None:
        """The pull whose response is read now (None: no pull)."""
        self._response = response

    def abort(self) -> None:
        """End the read of the pull that is read now, if any (from another thread)."""
        response = self._response
        if response is not None:
            pulls.abort(response)

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
        counts: dict[str, int | str] = {
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
