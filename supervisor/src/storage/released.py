"""The pools that UNOS released, and the releases that run (ADR-0496).

Unmount and Remove Pool unmount a pool and drop its entry from the host's fstab; its filesystem stays
on its disks, unmounted, and is listed again as a pool found there. The RAID monitor alerts a pool
found on the disks that is not mounted, unless UNOS released it: ``record`` keeps the UUID of each
filesystem that Unmount or Remove Pool unmounted at the pool's mountpoint or whose fstab entry they
dropped, in RAM and in RELEASED_FILE, until UNOS mounts it as a pool again (``forget``: Mount,
Import). Create Pool formats its disks, so a UUID it replaced is never seen again. The file is
written only when the record changes: once per release and once per mount of a released pool, real
changes (ADR-0214), never at a pass of the monitor. While the file cannot be opened, the record
changes in RAM only, and the file is not written over.

``changing`` marks a pool in RAM while Unmount, Remove Pool or Create Pool runs, whatever its
outcome. A check of the RAID monitor takes a ``mark`` before its read of the host and asks what
changed ``since`` it after the read: a pool that such an operation changed at any time between the
two is not judged by its mount state in that check, so a read between an unmount and its fstab
write, or between mkfs and the mount of a new pool, alerts nothing. The monitor never takes the
pools' lock: this module's lock is held only while its RAM state is read from the file, copied or
changed. The writers hold the pools' lock (storage.pools._lock), so the file is written by one of
them at a time.
"""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from .. import paths, state_files

logger = logging.getLogger(__name__)

RELEASED_FILE = paths.RELEASED_POOLS
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


@dataclass
class _State:
    loaded: bool = False
    released: dict[str, str] = field(default_factory=dict)  # a filesystem's UUID -> its pool's name then
    started: int = 0  # the operations marked since the supervisor started
    latest: dict[str, int] = field(default_factory=dict)  # a pool's name -> the number of its latest one
    running: set[str] = field(default_factory=set)  # the names of the pools that one changes now


_state = _State()
_guard = threading.Lock()


@dataclass(frozen=True)
class Mark:
    """What was released and changing when a check began its read of the host."""

    started: int
    running: frozenset[str]
    released: frozenset[str]


@dataclass(frozen=True)
class Seen:
    """What a check is to know of the releases after its read."""

    # The pools that Unmount, Remove Pool or Create Pool changed at some time since the mark: their
    # mount state is not judged.
    names: frozenset[str] = frozenset()
    # The filesystems released at the mark or now: not alerted as not mounted.
    uuids: frozenset[str] = frozenset()


def _read(data: object) -> dict[str, str]:
    if not isinstance(data, dict) or not all(
        isinstance(uuid, str) and _UUID_RE.fullmatch(uuid) and isinstance(name, str) for uuid, name in data.items()
    ):
        raise ValueError("it holds no pool names by filesystem UUID")
    return dict(data)


def _released() -> dict[str, str]:
    """The record, read from RELEASED_FILE at its first use; call it with _guard held. A file that
    cannot be opened reads as none, with a warning, and is tried again at the next use: until then
    ``loaded`` stays False, and the changes stay in RAM."""
    if not _state.loaded:
        try:
            _state.released = state_files.load_json(Path(RELEASED_FILE), _read, dict, what="the released pools")
        except OSError as exc:
            logger.warning("Could not read the released pools %s: %s", RELEASED_FILE, exc)
            return _state.released
        _state.loaded = True
    return _state.released


def _save(record: dict[str, str] | None) -> None:
    """Write the record (None: the file could not be read, so it is left as it is); a failure is
    logged, never raised: the release or mount it follows is done."""
    if record is None:
        return
    try:
        state_files.write_json(Path(RELEASED_FILE), record, sync=False)
    except OSError as exc:
        logger.warning("Could not write the released pools %s: %s", RELEASED_FILE, exc)


def mark() -> Mark:
    """Taken by a check right before its read of the host."""
    with _guard:
        return Mark(_state.started, frozenset(_state.running), frozenset(_released()))


def since(mark: Mark) -> Seen:
    """Taken by a check right after its read of the host, with the mark taken before it."""
    with _guard:
        started = {name for name, number in _state.latest.items() if number > mark.started}
        return Seen(mark.running | started, mark.released | frozenset(_released()))


@contextmanager
def changing(name: str) -> Iterator[None]:
    """Mark the pool ``name`` while Unmount, Remove Pool or Create Pool runs on it."""
    with _guard:
        _state.started += 1
        _state.latest[name] = _state.started
        _state.running.add(name)
    try:
        yield
    finally:
        with _guard:
            _state.running.discard(name)


def record(uuid: str, name: str) -> None:
    """UNOS unmounted the filesystem ``uuid`` of the pool ``name``, or dropped its fstab entry: it is
    released."""
    with _guard:
        released = _released()
        if released.get(uuid) == name:
            return
        released[uuid] = name
        copy = dict(released) if _state.loaded else None
    _save(copy)


def forget(uuid: str) -> None:
    """UNOS mounted the filesystem ``uuid`` as a pool again: it is no longer released."""
    with _guard:
        released = _released()
        if uuid not in released:
            return
        del released[uuid]
        copy = dict(released) if _state.loaded else None
    _save(copy)
