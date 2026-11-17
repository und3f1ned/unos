"""How much of its quota each volume uses: alerts at 80, 90 and 95 percent, and its history (ADR-0387).

At each pass of the RAID monitor (every 5 minutes), ``check`` reads the use of the volumes of the
mounted pools with quotas on, from the kernel's counters in sysfs (``storage.qgroups``), and
reports the volumes that reached a level of their quota as problems of the source "volumes"
(``health.problems``, ADR-0332): one per volume, whose stage is the highest level reached, alerted
again when the stage rises. A level is reached at ``level``% of the quota and left below
``level - LEVEL_MARGIN``%.

Which subvolume id is which volume comes from the last list of the pool's volumes
(``pools.mounted_pool_volumes``), made again only when the pool's generation, its transaction
number, changed since, and once more at the next pass (``_Listed.settled``): a pool that nothing
wrote to costs sysfs reads alone, and no disk of it is read (ADR-0378).

The history holds one point per volume per clock hour (the time, the bytes used, the quota) for
HISTORY_DAYS, by the pool's filesystem UUID and the volume's subvolume id, so that it follows a
rename, and each volume's stage, so that a start keeps the levels it had reached. It lives in RAM
and is written to HISTORY_FILE only at shutdown, when RAM holds something that the file does not
(ADR-0214, as the SMART history: ADR-0269), with the lists and their generations, so that a start
lists no pool again whose generation stayed the same.

Counts that btrfs marks inconsistent (a rescan is needed) feed neither the problems nor the history:
a volume keeps its problem and stage, and gets no point, until they are consistent again.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator

from .. import state_files
from ..health import problems
from ..security.validators import validate_pool_name, validate_volume_name
from . import pools, qgroups
from .schemas import PoolInfo, VolumeUsageHistory, VolumeUsagePoint

logger = logging.getLogger(__name__)

# The source of the problems (health.problems).
SOURCE = "volumes"
# The levels of a volume's quota that are alerted, in percent: fixed.
QUOTA_LEVELS = (80, 90, 95)
# A level is left below the level less this, in percent: a volume that hovers at one is alerted once.
LEVEL_MARGIN = 5
# The level from which a volume's problem is an error; below it a warning.
ERROR_LEVEL = 95

HISTORY_FILE = "/data/volume_usage.json"
# One point per volume per clock hour, for 30 days.
HISTORY_STEP = 3600
HISTORY_DAYS = 30
_HISTORY_SECONDS = HISTORY_DAYS * 86400
# The last second that a time of the history may be: 9999-12-31T23:59:59Z, the last that datetime shows.
_LAST_TIME = 253402300799

_GIB = 1 << 30

# Seconds since the epoch (the tests set the clock).
_now = time.time

Point = tuple[int, int, int | None]  # (seconds since the epoch, bytes used, quota in bytes or None)


@dataclass
class _Listed:
    """The volumes of a pool as its last list found them, at the pool's generation then.

    ``volumes`` (name -> subvolume id) are those of the last list that did not fail (None before
    one), and ``failure`` says why the last list failed (None when it did not). A list is
    ``settled`` once it found the generation that the list before found: the transaction
    whose number the first list read may still have been running, and a rename later in it leaves
    the number as it was, but btrfs commits a transaction within 30 seconds, long before the next
    pass.
    """

    pool: str
    generation: int | None
    volumes: dict[str, int] | None
    settled: bool = False
    failure: str | None = None


@dataclass
class _Series:
    pool: str
    volume: str
    points: list[Point] = field(default_factory=list)
    # The stage (0 or a level of QUOTA_LEVELS) that the last pass found of a consistent reading.
    stage: int = 0


class _State:
    """What the monitor keeps in RAM; ``lock`` guards it."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.loaded = False
        self.load_failure: str | None = None  # why HISTORY_FILE could not be opened, once logged
        self.unsaved = False
        self.listed: dict[str, _Listed] = {}  # by the pool's filesystem UUID
        self.series: dict[tuple[str, int], _Series] = {}  # by (filesystem UUID, subvolume id)


_state = _State()


# What the file holds is checked as the API checks it: its names reach the alerts and the web UI.
_Id = Annotated[int, Field(ge=1)]
_Bytes = Annotated[int, Field(ge=0)]
_Time = Annotated[int, Field(ge=0, le=_LAST_TIME)]


def _checked(validate: Callable[[str], str], value: str) -> str:
    """``value`` through a validator of security.validators, its HTTPException as a ValueError."""
    try:
        return validate(value)
    except HTTPException as exc:
        raise ValueError(str(exc.detail)) from None


def _fsid(value: str) -> str:
    if not qgroups.is_fsid(value):
        raise ValueError(f"Not a filesystem UUID: {value!r}")
    return value


class _SavedListed(BaseModel):
    pool: str
    generation: Annotated[int, Field(ge=0)] | None
    volumes: dict[str, _Id] | None
    settled: bool
    failure: str | None

    @field_validator("pool")
    @classmethod
    def _pool_name(cls, value: str) -> str:
        return _checked(validate_pool_name, value)

    @field_validator("volumes")
    @classmethod
    def _volume_names(cls, value: dict[str, int] | None) -> dict[str, int] | None:
        for name in value or {}:
            _checked(validate_volume_name, name)
        return value


class _SavedSeries(BaseModel):
    uuid: str
    id: _Id
    pool: str
    volume: str
    points: list[tuple[_Time, _Bytes, _Bytes | None]]
    stage: int

    @field_validator("uuid")
    @classmethod
    def _uuid(cls, value: str) -> str:
        return _fsid(value)

    @field_validator("pool")
    @classmethod
    def _pool_name(cls, value: str) -> str:
        return _checked(validate_pool_name, value)

    @field_validator("volume")
    @classmethod
    def _volume_name(cls, value: str) -> str:
        return _checked(validate_volume_name, value)

    @field_validator("stage")
    @classmethod
    def _level(cls, value: int) -> int:
        if value != 0 and value not in QUOTA_LEVELS:
            raise ValueError(f"Not a level of a quota: {value}")
        return value


class _Saved(BaseModel):
    listed: dict[str, _SavedListed]
    series: list[_SavedSeries]

    @field_validator("listed")
    @classmethod
    def _uuids(cls, value: dict[str, _SavedListed]) -> dict[str, _SavedListed]:
        for uuid in value:
            _fsid(uuid)
        return value


def _load(state: _State) -> None:
    """Read HISTORY_FILE into ``state`` at its first use after a start (``state.lock`` held).

    A file that cannot be read as the history (also one with a name the API would refuse, or a time
    that cannot be shown) is set aside as <name>.bad (state_files.load_json), and the history starts
    anew. One that cannot be opened (an I/O error) is tried again at the next use, and nothing is
    written over it meanwhile (save_unsaved); the failure is logged once. Points of a time ahead of
    the clock are dropped, as each pass drops them (_forget_points).
    """
    if state.loaded:
        return
    try:
        saved = state_files.load_json(
            Path(HISTORY_FILE), _Saved.model_validate, lambda: None, what="the history of the volumes' use"
        )
    except OSError as exc:
        if state.load_failure != str(exc):
            logger.warning("Could not read the history of the volumes' use %s: %s", HISTORY_FILE, exc)
            state.load_failure = str(exc)
        return
    state.loaded = True
    if saved is None:
        return
    state.listed = {
        uuid: _Listed(entry.pool, entry.generation, None if entry.volumes is None else dict(entry.volumes),
                      entry.settled, entry.failure)
        for uuid, entry in saved.listed.items()
    }
    state.series = {
        (entry.uuid, entry.id): _Series(entry.pool, entry.volume, [tuple(point) for point in entry.points],
                                        entry.stage)
        for entry in saved.series
    }
    _forget_points(state, int(_now()))


def save_unsaved() -> None:
    """Write the history to HISTORY_FILE when RAM holds what the file does not: at shutdown alone
    (``health.router.stop_health_monitors``). Blocking: run it in a worker thread."""
    state = _state
    with state.lock:
        if not state.unsaved or not state.loaded:
            return
        saved = {
            "listed": {
                uuid: {"pool": entry.pool, "generation": entry.generation, "volumes": entry.volumes,
                       "settled": entry.settled, "failure": entry.failure}
                for uuid, entry in state.listed.items()
            },
            "series": [
                {"uuid": uuid, "id": subvol_id, "pool": series.pool, "volume": series.volume,
                 "points": [list(point) for point in series.points], "stage": series.stage}
                for (uuid, subvol_id), series in sorted(state.series.items())
            ],
        }
        try:
            state_files.write_json(Path(HISTORY_FILE), saved, sync=True, indent=None, separators=(",", ":"))
        except OSError as exc:
            logger.error("Could not write the history of the volumes' use %s: %s", HISTORY_FILE, exc)
            return
        state.unsaved = False


def history_of(uuid: str, subvol_id: int) -> list[Point]:
    """The points kept of the volume ``subvol_id`` of the filesystem ``uuid``, oldest first."""
    state = _state
    with state.lock:
        _load(state)
        series = state.series.get((uuid, subvol_id))
        return list(series.points) if series else []


def _iso(seconds: float) -> str:
    return datetime.fromtimestamp(int(seconds), timezone.utc).isoformat()


def usage_history(pool_name: str, volume_name: str) -> VolumeUsageHistory:
    """GET /storage/pools/{pool}/volumes/{name}/usage: the points kept of the volume, and its current
    reading last. Blocking (host commands and sysfs reads): run it in a worker thread.

    The errors of ``pools.volume_quota_group``: 404 for an unknown pool or volume, 409 for a pool
    that is not mounted or not at its top level.
    """
    pool = validate_pool_name(pool_name)
    name = validate_volume_name(volume_name)
    uuid, subvol_id = pools.volume_quota_group(pool, name)
    reading = qgroups.usage(uuid, subvol_id)
    points = [VolumeUsagePoint(time=_iso(t), used=used, quota=quota) for t, used, quota in history_of(uuid, subvol_id)]
    if reading.used is not None:
        points.append(VolumeUsagePoint(time=_iso(_now()), used=reading.used, quota=reading.quota))
    return VolumeUsageHistory(
        name=name, pool=pool, used=reading.used, quota=reading.quota, usage=reading.state, points=points
    )


def _stage(used: int, quota: int, earlier: int) -> int:
    """The highest level of QUOTA_LEVELS that ``used`` bytes of ``quota`` reach, 0 for none; a level
    reached before (``earlier``, the stage found then) holds down to LEVEL_MARGIN below it. A quota
    of 0 bytes (``btrfs qgroup limit 0`` by hand) is full: every level."""
    stage = 0
    for level in QUOTA_LEVELS:
        threshold = level - LEVEL_MARGIN if earlier >= level else level
        if used * 100 >= threshold * quota:
            stage = level
    return stage


def _key(pool: str, subvol_id: int) -> str:
    return f"volume_quota:{pool}/{subvol_id}"


def _quota_problem(pool: str, volume: str, subvol_id: int, used: int, quota: int, stage: int) -> problems.Problem:
    """The problem of a volume that has reached the level ``stage`` of its quota."""
    path = f"{pools.pool_mountpoint(pool)}/{volume}"
    percent = used * 100 // quota if quota else 100
    return problems.Problem(
        key=_key(pool, subvol_id),
        severity="error" if stage >= ERROR_LEVEL else "warning",
        kind=f"volume_quota_{stage}",
        subject=f"{pool}/{volume}",
        title=f"Volume '{volume}' of pool '{pool}' has used {percent}% of its quota",
        detail=(
            f"{used / _GIB:.2f} GiB of the {quota / _GIB:.2f} GiB quota of volume {volume} in pool {pool} "
            "are used. When it is full, writes to the volume fail: delete files in it, or raise its "
            f"quota on the host (btrfs qgroup limit <size> {path})."
        ),
        stage=stage,
    )


def _volumes_of(state: _State, info: PoolInfo, uuid: str) -> dict[str, int] | None:
    """The volumes of a mounted pool with quotas on (name -> subvolume id): as the settled list of
    the pool's generation found them, else listed again; None when the list failed.

    A list that fails is tried again at the next pass, and then after the next write; a failure is
    logged once, not at every pass. A volume of the last list whose id btrfs could not resolve now
    fails the list: its history and its problem stay.
    """
    generation = qgroups.generation(uuid)
    with state.lock:
        known = state.listed.get(uuid)
    same = known is not None and generation is not None and known.generation == generation and known.pool == info.name
    if same and known.settled:
        return None if known.failure else known.volumes
    volumes = known.volumes if known is not None else None
    failure = None
    try:
        volumes, unresolved = pools.mounted_pool_volumes(info.name, pools.pool_mountpoint(info.name))
    except Exception as exc:
        failure = str(exc) or type(exc).__name__
    else:
        lost = unresolved & set((known.volumes or {}).values()) if known is not None else set()
        if lost:
            volumes, failure = known.volumes, f"btrfs could not resolve the subvolume ids {sorted(lost)}"
    if failure is not None and (known is None or known.failure != failure):
        logger.warning("Could not list the volumes of pool %s for their quotas: %s", info.name, failure)
    listed = _Listed(info.name, generation, volumes, settled=same, failure=failure)
    with state.lock:
        if listed != known:
            state.unsaved = True
        state.listed[uuid] = listed
        if failure is None and volumes is not None:
            ids = set(volumes.values())
            for key in [key for key in state.series if key[0] == uuid and key[1] not in ids]:
                del state.series[key]  # a volume that is gone
                state.unsaved = True
    return None if failure else volumes


def _series(state: _State, key: tuple[str, int], pool: str, volume: str) -> _Series:
    """The history of a volume, under its name of now (``state.lock`` held)."""
    series = state.series.get(key)
    if series is None:
        series = state.series[key] = _Series(pool, volume)
        state.unsaved = True
    if (series.pool, series.volume) != (pool, volume):
        series.pool, series.volume = pool, volume
        state.unsaved = True
    return series


def _add_point(state: _State, series: _Series, used: int, quota: int | None, now: int) -> None:
    """The volume's point of the hour of ``now``, unless it has one (``state.lock`` held)."""
    if not series.points or now // HISTORY_STEP > series.points[-1][0] // HISTORY_STEP:
        series.points.append((now, used, quota))
        state.unsaved = True


def _forget_points(state: _State, now: int) -> None:
    """Drop the points older than HISTORY_DAYS and those ahead of the clock (after a pass with a clock
    set ahead, or from a file), and the volumes left without any (``state.lock`` held)."""
    for key, series in list(state.series.items()):
        kept = [point for point in series.points if -HISTORY_STEP <= now - point[0] < _HISTORY_SECONDS]
        if len(kept) != len(series.points):
            state.unsaved = True
            if kept:
                series.points = kept
            else:
                del state.series[key]


def _sample(infos: list[PoolInfo], earlier: dict[str, problems.Problem]) -> dict[str, problems.Problem]:
    """One pass over the pools that the RAID monitor read: the history's points of this hour, and the
    problems of the volumes by key. Blocking (sysfs, and the host commands of a list): a worker thread.

    A volume whose use cannot be read or is counted inconsistently, and the volumes of a pool whose
    list fails or whose mount could not be read (health unknown), keep the problems found before
    (``earlier``); those of a pool that is not mounted or has quotas off go. The lists of the pools
    that the RAID monitor no longer finds at all go too.
    """
    state = _state
    now = int(_now())
    with state.lock:
        _load(state)
        # First, so that a point of a clock that was set ahead does not hold back this hour's.
        _forget_points(state, now)
        pools_found = {info.uuid for info in infos if info.uuid}
        for uuid in [uuid for uuid in state.listed if uuid not in pools_found]:
            del state.listed[uuid]
            state.unsaved = True
    found: dict[str, problems.Problem] = {}

    def keep(pool: str) -> None:
        prefix = f"volume_quota:{pool}/"
        found.update({key: problem for key, problem in earlier.items() if key.startswith(prefix)})

    for info in infos:
        uuid = info.uuid
        if not info.mounted or not uuid:
            if info.health == "unknown":  # the host's mount table could not be read
                keep(info.name)
            continue
        if not qgroups.quotas_on(uuid):
            continue
        volumes = _volumes_of(state, info, uuid)
        if volumes is None:
            keep(info.name)
            continue
        for volume, subvol_id in sorted(volumes.items()):
            reading = qgroups.usage(uuid, subvol_id)
            key = _key(info.name, subvol_id)
            if reading.used is None or reading.state != "counted":
                if key in earlier:
                    found[key] = earlier[key]
                continue
            with state.lock:
                series = _series(state, (uuid, subvol_id), info.name, volume)
                _add_point(state, series, reading.used, reading.quota, now)
                # The stage found before: the problem's, else the history's, which a start keeps.
                stage = 0
                if reading.quota is not None:
                    stage = _stage(reading.used, reading.quota, earlier[key].stage if key in earlier else series.stage)
                if series.stage != stage:
                    series.stage = stage
                    state.unsaved = True
            if stage:
                found[key] = _quota_problem(info.name, volume, subvol_id, reading.used, reading.quota, stage)
    return found


async def check(infos: list[PoolInfo]) -> None:
    """Read the use of the volumes of the pools that the RAID monitor's pass read (``infos``), keep
    this hour's points, and report the volumes that reached a level of their quota."""
    earlier = problems.of_source(SOURCE)
    found = await asyncio.to_thread(_sample, infos, earlier)
    await problems.update(SOURCE, found)
