"""The history of System > Resource Monitor (ADR-0398).

Every INTERVAL_SECONDS one sample (``history_loop``, started in the lifespan of main.py, ADR-0043)
reads the host's counters, in a worker thread: the CPU times of /proc/stat, the memory of
/proc/meminfo, the traffic of the host's interfaces that System > Network lists (PID 1's
/proc/net/dev) and the I/O of its physical disks (/proc/diskstats), as the stats message of
/ws/v1/events reads them (``websocket.events``, ADR-0388). Its point holds the averages since the
last sample, the share of busy CPU time and the bytes per second received and sent and read and
written, each device's growth summed over the devices that both samples have, and the memory in use
at the sample. The first sample after a start, and one more than MAX_GAP_SECONDS after the last (a
host that was suspended, a worker thread held), is only the base of the next.

The points of the last KEPT_SECONDS are kept in RAM, oldest first, and lost with the process:
nothing is written (ADR-0214), and a sample that fails is logged once until one succeeds again.
``current`` gives each point's age in seconds since the host booted, time suspended included
(CLOCK_BOOTTIME; the monotonic clock elsewhere), instead of a time of the wall clock, which a board
without a battery-backed clock sets at boot (ADR-0388).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import NamedTuple

import psutil

from ..websocket import events

logger = logging.getLogger(__name__)

INTERVAL_SECONDS = 10.0
KEPT_SECONDS = 3600.0
MAX_GAP_SECONDS = 3 * INTERVAL_SECONDS


def _clock() -> float:
    """Seconds on a clock that counts the time the host was suspended too (UNOS offers suspend),
    so that the points before a suspend keep their true ages; the monotonic clock stops then."""
    if hasattr(time, "CLOCK_BOOTTIME"):
        return time.clock_gettime(time.CLOCK_BOOTTIME)
    return time.monotonic()


class _Reading(NamedTuple):
    """The host's counters at one time of _clock."""

    at: float
    cpu: tuple[float, float]
    net: dict[str, tuple[int, int]]
    disks: dict[str, tuple[int, int]]


class _Point(NamedTuple):
    """The averages between two readings, at the time of the second."""

    at: float
    cpu_percent: float
    memory_percent: float
    net_recv_rate: float
    net_sent_rate: float
    disk_read_rate: float
    disk_write_rate: float


_points: deque[_Point] = deque(maxlen=int(KEPT_SECONDS / INTERVAL_SECONDS))
_last: _Reading | None = None
_failing = False
_loop_task: asyncio.Task | None = None


def _rates(new: dict[str, tuple[int, int]], old: dict[str, tuple[int, int]], elapsed: float) -> tuple[float, float]:
    """The bytes per second of both counters, summed over the devices that both readings have; a
    counter that went back (a driver loaded again) counts nothing (ADR-0388)."""
    first = second = 0
    for name, (a, b) in new.items():
        before = old.get(name)
        if before:
            first += max(0, a - before[0])
            second += max(0, b - before[1])
    return first / elapsed, second / elapsed


def take_sample() -> None:
    """Read the host's counters and keep the point since the last reading. Blocking: a worker
    thread calls it. Raises what a read raises."""
    global _last
    reading = _Reading(
        at=_clock(),
        cpu=events.cpu_reading(),
        net=events.interface_counters(),
        disks=events.physical_disk_counters(psutil.disk_io_counters(perdisk=True) or {}),
    )
    memory = psutil.virtual_memory().percent
    before, _last = _last, reading
    if before is None:
        return
    elapsed = reading.at - before.at
    if elapsed <= 0 or elapsed > MAX_GAP_SECONDS:
        return
    net_recv, net_sent = _rates(reading.net, before.net, elapsed)
    disk_read, disk_write = _rates(reading.disks, before.disks, elapsed)
    _points.append(_Point(
        at=reading.at,
        cpu_percent=events.cpu_busy_percent(before.cpu, reading.cpu),
        memory_percent=float(memory),
        net_recv_rate=net_recv,
        net_sent_rate=net_sent,
        disk_read_rate=disk_read,
        disk_write_rate=disk_write,
    ))


def sample() -> None:
    """One sample of the loop: a failure is logged once, until a sample succeeds again."""
    global _failing
    try:
        take_sample()
    except Exception as exc:
        if not _failing:
            _failing = True
            logger.warning("The Resource Monitor's history cannot read the host's counters: %s", exc)
        return
    _failing = False


def current() -> dict:
    """The history as GET /api/v1/monitor/history answers it: the points of the last KEPT_SECONDS,
    each with its age in seconds, oldest first."""
    now = _clock()
    # A copy first: the sampler's worker thread appends to the deque, and an append during a walk
    # of it raises; tuple() copies it without letting another thread run.
    points = tuple(_points)
    return {
        "interval_seconds": INTERVAL_SECONDS,
        "points": [
            {"age_seconds": round(now - point.at, 1), **dict(zip(_Point._fields[1:], point[1:]))}
            for point in points
            if now - point.at <= KEPT_SECONDS
        ],
    }


async def history_loop() -> None:
    """A sample every INTERVAL_SECONDS, in a worker thread, until cancelled."""
    while True:
        await asyncio.to_thread(sample)
        await asyncio.sleep(INTERVAL_SECONDS)


async def start_history() -> None:
    """Start the samples (main.py's lifespan, ADR-0043)."""
    global _loop_task
    _loop_task = asyncio.create_task(history_loop(), name="unos-monitor-history")


async def stop_history() -> None:
    """Stop them at shutdown; a sample that runs ends in its worker thread."""
    if _loop_task is not None and not _loop_task.done():
        _loop_task.cancel()
        await asyncio.gather(_loop_task, return_exceptions=True)
