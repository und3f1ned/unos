"""The scrubs that run, for the events socket: the Storage page follows them without asking (ADR-0367).

Each request of the scrub API notes what it found for its pool (``note``): a status read, a start,
a cancel; a status that could not be read notes why. So does the Scheduler's scrub task once btrfs
started its scrub. A pool joins when its scrub runs, and then keeps its last status. While an
administrator's events socket is open, it calls ``follow`` every time it sends the stats: the status
of each scrub that runs is read again, at most every READ_INTERVAL_SECONDS and one read at a time,
in a task of its own that the socket does not wait for. The socket sends administrators the statuses
(``current``) whenever ``version`` changes. Nothing is read while no administrator has a socket open.

The statuses live in RAM only, as btrfs's own record of the last scrub does (ADR-0171).
"""

import asyncio
import subprocess
import time
from typing import Any

from ..security.validators import sanitize_error_message
from . import pools
from .schemas import ScrubStatus

READ_INTERVAL_SECONDS = 2.0

# Each pool's last status, or why it could not be read: the pools whose scrub was seen running.
_scrubs: dict[str, ScrubStatus | str] = {}
# A number that changes whenever _scrubs does.
_version = 0
# How many notes were made: a read keeps its answer only when no request noted the pool meanwhile.
_notes = 0
_noted_at: dict[str, int] = {}
_reading: "asyncio.Task[None] | None" = None
_read_at = float("-inf")


def failure(exc: Exception, pool: str) -> str:
    """Why a scrub command of ``pool`` failed, as the scrub API answers it (500)."""
    if isinstance(exc, subprocess.TimeoutExpired):
        return (
            f"btrfs {' '.join(exc.cmd[1:3])} did not answer within {pools.SCRUB_COMMAND_TIMEOUT} "
            f"seconds: pool {pool} may not be responding"
        )
    return sanitize_error_message(exc)


def _runs(scrub: ScrubStatus | str) -> bool:
    return isinstance(scrub, ScrubStatus) and scrub.status == "running"


def note(pool: str, scrub: ScrubStatus | str) -> None:
    """Keep what was found of ``pool``'s scrub: its status, or why it could not be read. A pool
    joins only when its scrub runs."""
    global _version, _notes
    if pool not in _scrubs and not _runs(scrub):
        return
    _notes += 1
    _noted_at[pool] = _notes
    if _scrubs.get(pool) != scrub:
        _scrubs[pool] = scrub
        _version += 1


def version() -> int:
    """0 before the first scrub was seen running; then a number that changes with ``current()``."""
    return _version


def current() -> list[dict[str, Any]]:
    """What the events socket sends, by pool: each status as the scrub API answers it, or
    ``{"pool": ..., "error": ...}`` when it could not be read."""
    return [
        scrub.model_dump(mode="json") if isinstance(scrub, ScrubStatus) else {"pool": pool, "error": scrub}
        for pool, scrub in sorted(_scrubs.items())
    ]


def follow() -> None:
    """Start a read of the scrubs that run, unless one runs or the last started less than
    READ_INTERVAL_SECONDS ago; it does not wait for it."""
    global _reading, _read_at
    running = [pool for pool, scrub in _scrubs.items() if _runs(scrub)]
    if not running or (_reading is not None and not _reading.done()):
        return
    if time.monotonic() - _read_at < READ_INTERVAL_SECONDS:
        return
    _read_at = time.monotonic()
    _reading = asyncio.get_running_loop().create_task(_read(running), name="unos-scrub-status")


async def _read(running: list[str]) -> None:
    for pool in running:
        noted = _noted_at.get(pool)
        try:
            scrub: ScrubStatus | str = await asyncio.to_thread(pools.get_scrub_status, pool)
        except Exception as exc:
            scrub = failure(exc, pool)
        # A request answered meanwhile: its answer is newer than this read.
        if _noted_at.get(pool) == noted:
            note(pool, scrub)
