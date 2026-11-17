"""The use of the running containers of apps, read from the kernel's cgroups, and its problems
(ADR-0388).

Every SAMPLE_SECONDS one pass (``app_resources_loop``, started in the lifespan of main.py) asks
Docker twice, for the summary list of the running containers labelled ``unos.app`` and for its
events "oom" of those containers since the last pass (a query of the past, answered at once), and
reads a few files of each container's cgroup v2 in the host's /sys/fs/cgroup: memory.current,
memory.stat, memory.max, cpu.stat and cpu.max. The files are counters of the kernel's memory,
read without a process, a Docker stats stream or a disk; the supervisor reaches the host's cgroup
tree through its PID 1 (``pid: host``), as its own /sys/fs/cgroup is the container's. Nothing is
written: the readings and the problems are kept in RAM (ADR-0214).

- A container's memory use is memory.current without the inactive page cache (inactive_file of
  memory.stat), which the kernel drops first, as ``docker stats`` counts it; its CPU use is the CPU
  time of cpu.stat between two passes, in CPUs.
- Problems of the source "apps" (health.problems, ADR-0332), each alerted once when found:
  ``app_memory:<container>`` (a warning) from a pass that finds a container at MEMORY_WARNING of
  its memory limit, until one finds it below MEMORY_CLEAR; ``app_cpu:<container>`` (a warning)
  when its CPU use over the last CPU_WINDOW_SECONDS averaged CPU_WARNING of its CPU limit, until
  it falls below CPU_CLEAR; ``app_oom:<container>`` (an error) from Docker's event "oom", which
  containerd sends for each process that the kernel ends in the container's cgroup for lack of
  memory (also when that ends the container, whose new cgroup counts no kill), until
  OOM_HOLD_SECONDS after the last one.
- A container that no longer runs loses its readings and its memory and CPU problems; one whose
  cgroup cannot be read keeps its problems; a pass that Docker does not answer changes nothing.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..containers.docker import get_client
from ..health import problems
from ..health.problems import Problem
from . import limits
from .catalog import get_app_by_id
from .stacks import DOCKER_ERRORS, UNOS_APP_LABEL

logger = logging.getLogger(__name__)

# The host's cgroup v2 tree, through the root of its PID 1: the supervisor runs with pid: host.
CGROUP_ROOT = Path("/proc/1/root/sys/fs/cgroup")
SAMPLE_SECONDS = 30
CPU_WINDOW_SECONDS = 300
MEMORY_WARNING = 0.9
MEMORY_CLEAR = 0.8
CPU_WARNING = 0.9
CPU_CLEAR = 0.7
OOM_HOLD_SECONDS = 3600
# The margin above what a running container uses that a new memory limit must leave.
MEMORY_MARGIN = 0.1
MEMORY_MARGIN_MIN = 32 * limits.MIB
# How long a pass may wait for Docker before the loop goes on without it; one that still waits
# keeps the next passes from starting another (docker-py sends the events' request without a
# timeout).
PASS_TIMEOUT_SECONDS = 120
SOURCE = "apps"
# The events that a pass asks Docker for.
OOM_EVENTS = {"type": "container", "event": "oom", "label": UNOS_APP_LABEL}

_CONTAINER_ID = re.compile(r"[0-9a-f]{64}")


def _now() -> float:
    return time.time()


def _monotonic() -> float:
    return time.monotonic()


@dataclass(frozen=True)
class Reading:
    """What a container's cgroup says at one read."""

    memory_used: int
    # Its pages in swap (memory.swap.current; 0 without swap accounting): they count against its
    # memory as they come back.
    swap_used: int
    # The limits that the kernel holds it to: bytes, and CPUs; None for none.
    memory_limit: int | None
    cpu_limit: float | None
    # The CPU time it used since its cgroup was made, in microseconds; None where the cgroup has
    # no CPU controller.
    cpu_usage_usec: int | None
    # The inode of the cgroup's folder: a new cgroup (a restart) has another.
    identity: int


@dataclass(frozen=True)
class Use:
    """A container's use at the last pass."""

    memory_used: int
    cpu_used: float | None
    sampled_at: str


def cgroup_folder(container_id: str) -> Path | None:
    """The cgroup of a container of Docker on the host: system.slice/docker-<id>.scope with Docker's
    systemd driver, docker/<id> with its cgroupfs driver; None when neither exists (the container
    does not run, or the host has no cgroup v2)."""
    if not _CONTAINER_ID.fullmatch(container_id):
        return None
    for folder in (CGROUP_ROOT / "system.slice" / f"docker-{container_id}.scope", CGROUP_ROOT / "docker" / container_id):
        if folder.is_dir():
            return folder
    return None


def _keyed(text: str) -> dict[str, int]:
    values = {}
    for line in text.splitlines():
        key, _, value = line.partition(" ")
        if value.strip().lstrip("-").isdigit():
            values[key] = int(value)
    return values


def read(container_id: str) -> Reading | None:
    """A container's cgroup now; None when it has none. OSError or ValueError for files that cannot
    be read as the kernel writes them."""
    folder = cgroup_folder(container_id)
    if folder is None:
        return None
    identity = os.stat(folder).st_ino
    current = int((folder / "memory.current").read_text().strip())
    inactive = _keyed((folder / "memory.stat").read_text()).get("inactive_file", 0)
    memory_max = (folder / "memory.max").read_text().strip()
    swap = folder / "memory.swap.current"
    swap_used = int(swap.read_text().strip()) if swap.exists() else 0
    # A removed limit is above the host's memory (limits.py): from a MiB below it, which the
    # kernel's whole pages may round to, there is none.
    memory_limit = (
        None if memory_max == "max" or int(memory_max) >= limits.host_memory() - limits.MIB else int(memory_max)
    )
    usage_usec = cpu_limit = None
    if (folder / "cpu.stat").exists() and (folder / "cpu.max").exists():
        usage_usec = _keyed((folder / "cpu.stat").read_text())["usage_usec"]
        quota, _, period = (folder / "cpu.max").read_text().strip().partition(" ")
        cpu_limit = None if quota == "max" else round(int(quota) / int(period or limits.CPU_PERIOD), 2)
    return Reading(
        memory_used=max(0, current - inactive),
        swap_used=swap_used,
        memory_limit=memory_limit,
        cpu_limit=cpu_limit,
        cpu_usage_usec=usage_usec,
        identity=identity,
    )


def memory_floor(container_id: str) -> tuple[int, int] | None:
    """What a running container uses now (its memory and its pages in swap) and the least memory
    limit it may get: that with a margin of MEMORY_MARGIN of it, at least MEMORY_MARGIN_MIN, so
    that the kernel need not take the pages it works with (its own code among them, read again
    from the system disk) at once. None when its cgroup cannot be read: the caller refuses then."""
    try:
        reading = read(container_id)
    except (OSError, ValueError, KeyError):
        return None
    if reading is None:
        return None
    used = reading.memory_used + reading.swap_used
    return used, used + max(int(used * MEMORY_MARGIN), MEMORY_MARGIN_MIN)


@dataclass
class _Oom:
    at: float  # the time of the last kill, seconds since the epoch
    app: str
    memory_limit: int | None


@dataclass
class _State:
    # The use of each running container at the last pass, by its id.
    uses: dict[str, Use] = field(default_factory=dict)
    # Its readings of CPU time for the CPU window: (monotonic time, microseconds), and its cgroup.
    cpu: dict[str, deque] = field(default_factory=dict)
    identity: dict[str, int] = field(default_factory=dict)
    # The memory limit of each container at its last read, by name, for an OOM's text.
    memory_limits: dict[str, int | None] = field(default_factory=dict)
    # The memory and CPU problems found, by container name.
    memory: dict[str, Problem] = field(default_factory=dict)
    cpu_problems: dict[str, Problem] = field(default_factory=dict)
    ooms: dict[str, _Oom] = field(default_factory=dict)
    # The events asked for: from this second on; and those of that second seen already, which
    # the next query, which starts with it, answers again.
    since: int | None = None
    seen: set[tuple[str, int]] = field(default_factory=set)
    docker_failed: bool = False
    unreadable: set[str] = field(default_factory=set)
    failed: bool = False


_state = _State()


def latest(container_id: str) -> Use | None:
    """The use of a container at the last pass that read it; None when it did not."""
    return _state.uses.get(container_id)


def _app_name(labels: dict[str, Any]) -> str:
    app_id = str(labels.get(UNOS_APP_LABEL, ""))
    template = get_app_by_id(app_id)
    return template.name if template is not None else app_id


def _memory_problem(name: str, app: str, limit: int) -> Problem:
    return Problem(
        key=f"app_memory:{name}", severity="warning", kind="app_memory", subject=name,
        title=f"{name} is near its memory limit",
        detail=(
            f"{name}, a container of {app}, uses over {MEMORY_WARNING:.0%} of its memory limit of {limits.size(limit)}. "
            "At the limit the kernel takes memory from it and then ends its processes: raise the limit on the "
            "Installed tab of the App Store."
        ),
    )


def _cpu_problem(name: str, app: str, cpus: float) -> Problem:
    return Problem(
        key=f"app_cpu:{name}", severity="warning", kind="app_cpu", subject=name,
        title=f"{name} is held at its CPU limit",
        detail=(
            f"{name}, a container of {app}, used over {CPU_WARNING:.0%} of its CPU limit of {limits.cpus_text(cpus)} "
            f"on average for {CPU_WINDOW_SECONDS // 60} minutes, so the kernel holds it back: raise the limit on the "
            "Installed tab of the App Store if it is too slow."
        ),
    )


def _oom_problem(name: str, oom: _Oom) -> Problem:
    if oom.memory_limit is not None:
        detail = (
            f"The kernel ended a process of {name}, a container of {oom.app}, that had reached its memory limit of "
            f"{limits.size(oom.memory_limit)}: raise the limit on the Installed tab of the App Store."
        )
    else:
        detail = (
            f"The kernel ended a process of {name}, a container of {oom.app}, because the NAS ran out of memory: "
            "give the apps that use the most memory limits."
        )
    return Problem(
        key=f"app_oom:{name}", severity="error", kind="app_oom", subject=name,
        title=f"{name} ran out of memory", detail=detail,
    )


def _cpu_average(readings: deque, now: float) -> float | None:
    """The CPUs used on average since the newest reading at least CPU_WINDOW_SECONDS old; None
    before the readings cover that long."""
    newest = readings[-1]
    for at, used in reversed(readings):
        if now - at >= CPU_WINDOW_SECONDS:
            return (newest[1] - used) / 1_000_000 / (newest[0] - at)
    return None


def _ask_docker(client: Any) -> tuple[list[dict], list[dict], int]:
    """Docker's running containers of apps and its events "oom" since the last pass, and the end of
    the time asked for (whole seconds, as Docker compares them)."""
    listed = client.api.containers(all=False, filters={"label": UNOS_APP_LABEL})
    until = int(_now())
    # A clock set back asks again from its new time, never for a time span that ends before it starts.
    since = min(_state.since, until) if _state.since is not None else until
    events = list(client.events(since=since, until=until, filters=dict(OOM_EVENTS), decode=True))
    return listed, events, until


def _sample() -> dict[str, Problem] | None:
    """One pass, blocking: the problems of the source "apps" it found, or None when Docker did not
    answer (the problems stay as they are)."""
    state = _state
    try:
        listed, events, until = _ask_docker(get_client())
    except DOCKER_ERRORS as e:
        if not state.docker_failed:
            logger.warning("Could not read the use of the apps' containers: Docker did not answer: %s", e)
        state.docker_failed = True
        return None
    state.docker_failed = False
    state.since = until
    now, wall = _monotonic(), _now()
    at = datetime.fromtimestamp(wall, timezone.utc).isoformat()

    uses: dict[str, Use] = {}
    running: set[str] = set()
    for item in listed:
        container_id = str(item.get("Id") or "")
        names = item.get("Names") or []
        name = str(names[0]).lstrip("/") if names else ""
        labels = item.get("Labels") or {}
        if not name or not _CONTAINER_ID.fullmatch(container_id):
            continue
        running.add(name)
        try:
            reading = read(container_id)
        except (OSError, ValueError, KeyError) as e:
            if container_id not in state.unreadable:
                logger.warning("Could not read the cgroup of %s: %s", name, e)
            state.unreadable.add(container_id)
            continue
        if reading is None:
            if container_id not in state.unreadable:
                logger.warning("Found no cgroup of %s below %s", name, CGROUP_ROOT)
            state.unreadable.add(container_id)
            continue
        state.unreadable.discard(container_id)
        app = _app_name(labels)
        state.memory_limits[name] = reading.memory_limit

        readings = state.cpu.get(container_id)
        if (readings is None or state.identity.get(container_id) != reading.identity
                or reading.cpu_usage_usec is None
                or (readings and reading.cpu_usage_usec < readings[-1][1])):
            readings = deque()
            state.identity[container_id] = reading.identity
        cpu_used = None
        if reading.cpu_usage_usec is not None:
            if readings and now > readings[-1][0]:
                cpu_used = round((reading.cpu_usage_usec - readings[-1][1]) / 1_000_000 / (now - readings[-1][0]), 2)
            readings.append((now, reading.cpu_usage_usec))
            while now - readings[0][0] > CPU_WINDOW_SECONDS + 2 * SAMPLE_SECONDS:
                readings.popleft()
        state.cpu[container_id] = readings
        uses[container_id] = Use(memory_used=reading.memory_used, cpu_used=cpu_used, sampled_at=at)

        limit = reading.memory_limit
        over = name in state.memory and limit is not None and reading.memory_used >= MEMORY_CLEAR * limit
        if limit is not None and (over or reading.memory_used >= MEMORY_WARNING * limit):
            state.memory[name] = _memory_problem(name, app, limit)
        else:
            state.memory.pop(name, None)

        cpus = reading.cpu_limit
        average = _cpu_average(readings, now) if readings else None
        if cpus is None:
            state.cpu_problems.pop(name, None)
        elif average is not None:
            held = name in state.cpu_problems and average >= CPU_CLEAR * cpus
            if held or average >= CPU_WARNING * cpus:
                state.cpu_problems[name] = _cpu_problem(name, app, cpus)
            else:
                state.cpu_problems.pop(name, None)

    seen: set[tuple[str, int]] = set()
    for event in events:
        nano = int(event.get("timeNano") or int(event.get("time") or 0) * 1_000_000_000)
        actor = event.get("Actor") or {}
        key = (str(actor.get("ID") or event.get("id") or ""), nano)
        if nano >= until * 1_000_000_000:
            seen.add(key)  # the next query starts with this second
        if key in state.seen:
            continue
        attributes = actor.get("Attributes") or {}
        name = str(attributes.get("name") or "")
        if name:
            earlier = state.ooms.get(name)
            state.ooms[name] = _Oom(at=max(nano / 1_000_000_000, earlier.at if earlier else 0.0),
                                    app=_app_name(attributes), memory_limit=state.memory_limits.get(name))
    state.seen = seen

    # What no longer runs: its readings and its memory and CPU problems go.
    state.uses = uses
    listed_ids = {str(item.get("Id") or "") for item in listed}
    for container_id in [key for key in state.cpu if key not in listed_ids]:
        state.cpu.pop(container_id, None)
        state.identity.pop(container_id, None)
    state.unreadable &= listed_ids
    for problems_of in (state.memory, state.cpu_problems):
        for name in [key for key in problems_of if key not in running]:
            del problems_of[name]
    for name in [key for key, oom in state.ooms.items() if wall - oom.at >= OOM_HOLD_SECONDS]:
        del state.ooms[name]

    found = {problem.key: problem for problem in (*state.memory.values(), *state.cpu_problems.values())}
    found.update({f"app_oom:{name}": _oom_problem(name, oom) for name, oom in state.ooms.items()})
    return found


_pass: asyncio.Future | None = None


async def sample_once() -> None:
    """One pass: the reads in a worker thread, then the problems. While an earlier pass still waits
    for Docker, none starts; one that waits longer than PASS_TIMEOUT_SECONDS is left to end in its
    thread, logged once."""
    global _pass
    if _pass is not None and not _pass.done():
        return
    _pass = asyncio.ensure_future(asyncio.to_thread(_sample))
    try:
        found = await asyncio.wait_for(asyncio.shield(_pass), PASS_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        if not _state.docker_failed:
            logger.warning("Could not read the use of the apps' containers: Docker did not answer within %s s",
                           PASS_TIMEOUT_SECONDS)
        _state.docker_failed = True
        return
    if found is not None:
        await problems.update(SOURCE, found)


async def app_resources_loop() -> None:
    """A pass every SAMPLE_SECONDS while the supervisor runs."""
    while True:
        try:
            await sample_once()
            _state.failed = False
        except asyncio.CancelledError:
            raise
        except Exception:
            if not _state.failed:
                logger.exception("A pass over the use of the apps' containers failed")
            _state.failed = True
        await asyncio.sleep(SAMPLE_SECONDS)


_loop_task: asyncio.Task | None = None


async def start_app_resources() -> None:
    """Start the passes (main.py's lifespan, ADR-0043)."""
    global _loop_task
    _loop_task = asyncio.create_task(app_resources_loop(), name="unos-app-resources")


async def stop_app_resources() -> None:
    """Stop them at shutdown; a pass's reads end in their worker thread."""
    if _loop_task is not None and not _loop_task.done():
        _loop_task.cancel()
        await asyncio.gather(_loop_task, return_exceptions=True)
