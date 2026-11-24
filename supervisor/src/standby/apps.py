"""The critical apps of the SD card standby (ADR-0471, phase 3; ADR-0500).

An administrator marks an installed app critical (``standby_apps``, ``models.py``). Its copy on the
card's btrfs top level is a generation ``@app-<app>-<n>``:

- ``data/``: ``{pool}/apps/<app>/`` without its caches (the catalog's ``cache=True``) and the copies
  of ADR-0408 (``*.previous``, and the transient ``*.copying`` and ``*.rolled-back``), copied from a
  read-only snapshot ``.unos-standby-snapshot`` of the pool's subvolume that holds the folder, taken
  while the app runs, and deleted after the copy;
- ``images/<id>.tar.gz``: ``docker save`` of each image its containers run, by the image's id;
- ``definitions.json``: each container as Docker's inspect has it (Config, HostConfig, its networks,
  whether it ran) in its start order, companions first, and the app's network.

``state.json`` names a generation in ``apps.<app>`` only once it is complete; the old one and the
snapshot are deleted after. A sync first compares (rsync's dry run, the image ids, the definitions)
and writes nothing when nothing changed. It passes over a pool whose disks sleep (ADR-0378).

When the pool that holds a critical app's data fails (the RAID monitor's pass, ``on_pools_read``),
or at a manual move (the reason drill) or before a planned repair (the reason planned), ``move``
recreates the app's current containers on a writable snapshot ``@app-<app>-run`` of its generation:
the binds under ``{pool}/apps/<app>/`` on its ``data/``, a cache on a tmpfs of 256 MiB, every other
pool bind on an empty read-only folder of the card; those that ran start. ``state.json`` records it
(``apps.<app>.failed_over``), and the problem ``apps_on_standby`` names the apps. The pool return
(``failback.py``) copies the card's changes back into the pool; the discarding return (``return_app``)
recreates the pool-bound containers from the definitions saved at the move and deletes the run copy. At every start, before the supervisor serves, ``reapply`` moves again
a pool-bound container of an app that state.json records as moved; on the standby's root
``start_on_standby`` loads the critical apps' images and creates their networks and containers.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, UTC
from collections.abc import Callable
from typing import Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select

from .. import database, host
from ..config import get_settings
from ..times import parse_time, utc_now, utc_text
from .hostops import CARD_DIR, StandbyError, StandbyFailed, StandbyHost, kill_sync
from .models import DEFAULT_INTERVAL, INTERVALS, StandbyApp

logger = logging.getLogger(__name__)

APP_PREFIX = "@app-"
RUN_SUFFIX = "run"
SNAPSHOT_NAME = ".unos-standby-snapshot"
EMPTY_DIR = f"{CARD_DIR}/empty"
EMPTY_FILE = f"{CARD_DIR}/empty-file"
DEFINITIONS = "definitions.json"
# The folder of a copy that holds the app's folder, beside images/ and definitions.json.
COPY = "data"
CACHE_TMPFS = "size=256m"
# The label of a container that runs from the card, in place of unos.app.pool: the pool it left. Neither
# an unmount's check of the containers that use a pool nor a mount's start of its apps counts it.
CARD_LABEL = "unos.standby.card"
# What a copy leaves out of the app's folder: ADR-0408's copies and the folders an update or rollback
# leaves for a moment.
EXCLUDED_SUFFIXES = (".previous", ".copying", ".rolled-back")
# The card keeps this much free: 10% of its size, at least 2 GiB (ADR-0471).
RESERVE_FRACTION = 0.10
RESERVE_MIN = 2 * 1024**3
# A forced read-only pool gets one more copy within this time while it reads (ADR-0471).
LAST_COPY_SECONDS = 300
# A pool managed and not mounted, or missing more devices than its profile tolerates, moves its apps at
# the second pass in a row of the RAID monitor that finds it so.
FAILED_PASSES = 2
COPY_OLD = timedelta(days=3)
# The MiB of an app's recent syncs that wrote, for the writes a week of an interval.
RECENT_SYNCS = 8
RSYNC_TIMEOUT = 2 * 3600
SAVE_TIMEOUT = 3600
STOP_SECONDS = 30
READY_SECONDS = 180
_APP = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_PLAIN_PATH = re.compile(r"/[A-Za-z0-9._/@+-]*")
_IMAGE_ID = re.compile(r"sha256:([0-9a-f]{64})")
Reason = Literal["pool_failed", "pool_read_only", "drill", "planned"]
# The devices that a btrfs data profile may miss and still hold all its data.
TOLERATED = {"single": 0, "raid0": 0, "dup": 0, "raid1": 1, "raid10": 1, "raid5": 1, "raid1c3": 2, "raid6": 2,
             "raid1c4": 3}

# One sync of an app at a time; the move of an app stops the sync of its own.
_stop_app_sync = threading.Event()
_syncing_app: str | None = None
# The RAID monitor's passes that found a pool failed in a row, by pool; the disks found asleep.
_failed_passes: dict[str, int] = {}
_asleep: dict[str, Any] = {}
# The apps a move runs for now, and when an app's copy was last found complete without a write (RAM).
_moving: set[str] = set()
_checked_at: dict[str, str] = {}
_tasks: set[asyncio.Task] = set()
# The last failure of an automatic move, by app: a move that fails again the same way is not audited again.
_move_failures: dict[str, str] = {}
# Whether a pass of the RAID monitor ran since the start: until then a copy is not called old, since its
# check that found nothing changed is kept in RAM only.
_passed = False


class StandbyAppsError(StandbyError):
    pass


# --- the marks -----------------------------------------------------------------------------------


def _session() -> Any:
    return database.async_session()


@dataclass(frozen=True)
class Mark:
    interval: int
    marked_at: datetime | None


async def marks() -> dict[str, Mark]:
    """The critical apps: their intervals in hours and when they were marked."""
    async with _session() as session:
        rows = (await session.execute(select(StandbyApp))).scalars().all()
    return {row.app: Mark(row.interval_hours if row.interval_hours in INTERVALS else DEFAULT_INTERVAL,
                          row.marked_at.replace(tzinfo=row.marked_at.tzinfo or UTC) if row.marked_at else None)
            for row in rows}


async def set_mark(app: str, critical: bool, interval: int) -> tuple[bool, int | None]:
    """Mark or unmark ``app``; whether a row changed and the interval it had."""
    async with _session() as session:
        row = (await session.execute(select(StandbyApp).where(StandbyApp.app == app))).scalar_one_or_none()
        before = row.interval_hours if row is not None else None
        if not critical:
            if row is None:
                return False, None
            await session.execute(delete(StandbyApp).where(StandbyApp.app == app))
        elif row is None:
            session.add(StandbyApp(app=app, interval_hours=interval))
        elif row.interval_hours == interval:
            return False, before
        else:
            row.interval_hours = interval
        await session.commit()
    return True, before


# --- state.json's apps ----------------------------------------------------------------------------


def _service() -> Any:
    from . import service

    return service


def stored_apps() -> dict[str, dict[str, Any]]:
    value = _service()._state.stored.get("apps")
    if not isinstance(value, dict):
        return {}
    # The card is removable: a key that is no app's name is no app's record.
    return {key: dict(item) for key, item in value.items() if isinstance(item, dict) and _APP.fullmatch(str(key))}


def stored_app(app: str) -> dict[str, Any]:
    return stored_apps().get(app, {})


def update_app_record(app: str, change: Callable[[dict[str, Any]], dict[str, Any] | None]) -> None:
    """Change ``apps.<app>`` of state.json (None removes it) and write it when it changed (blocking)."""
    service = _service()
    with service._stored_lock:
        apps = stored_apps()
        new = change(dict(apps.get(app, {})))
        if new is None:
            apps.pop(app, None)
        else:
            apps[app] = new
        stored = {**service._state.stored, "apps": apps}
        if not apps:
            stored.pop("apps")
        service._save_stored(stored)


def failed_over(app: str) -> dict[str, Any] | None:
    value = stored_app(app).get("failed_over")
    return value if isinstance(value, dict) else None


def refuse_failed_over(app: str, what: str) -> None:
    """409 for an update, rollback, uninstall or reinstall of an app that runs from the card."""
    if _APP.fullmatch(app or "") and failed_over(app) is not None:
        raise HTTPException(409, detail=f"The app {app} runs from the SD standby's card: {what} waits for its "
                                        "return to the pool (System > Standby)")


# --- paths ---------------------------------------------------------------------------------------


def generation_path(app: str, number: int | str) -> str:
    return f"{CARD_DIR}/{APP_PREFIX}{app}-{number}"


def run_path(app: str) -> str:
    return generation_path(app, RUN_SUFFIX)


def pools_base() -> str:
    return os.path.normpath(get_settings().pools_base_path)


def pool_path(pool: str) -> str:
    return f"{pools_base()}/{pool}"


def app_folder(pool: str, app: str) -> str:
    return f"{pool_path(pool)}/apps/{app}"


def _within(path: str, root: str) -> bool:
    path, root = os.path.normpath(path), os.path.normpath(root)
    return path == root or path.startswith(root.rstrip("/") + "/")


def _bases() -> list[str]:
    listed = pools_base()
    resolved = os.path.realpath(listed)
    return [listed] if resolved == listed else [listed, resolved]


def _relative(path: str, root: str) -> str | None:
    """``path`` below ``root`` or its real path, relative to it; None outside."""
    for base in {os.path.normpath(root), os.path.realpath(root)}:
        if _within(path, base):
            return os.path.relpath(os.path.normpath(path), base)
    return None


def reserve_of(size: int) -> int:
    return max(int(size * RESERVE_FRACTION), RESERVE_MIN)


# --- the app as Docker has it ----------------------------------------------------------------------


def _client() -> Any:
    from ..containers import docker as docker_module

    return docker_module.get_client()


def _template(app: str) -> Any:
    from ..apps.catalog import get_app_by_id

    return get_app_by_id(app)


def installed(client: Any, app: str) -> Any:
    """The app's containers (stacks.InstalledContainers); None when Docker has none."""
    from ..apps import stacks

    found = [c for c in client.containers.list(all=True, filters={"label": f"{stacks.UNOS_APP_LABEL}={app}"},
                                               ignore_removed=True) if stacks.app_key(c) == app]
    return stacks.group(app, found, _template(app)) if found else None


def app_pool(group: Any) -> str | None:
    """The pool that holds the app's data: its containers' label, or the pool a move left."""
    from ..apps.stacks import APP_POOL_LABEL

    for container in group.containers:
        labels = container.labels or {}
        pool = labels.get(APP_POOL_LABEL) or labels.get(CARD_LABEL)
        if pool:
            return str(pool)
    return None


def _caches(app: str) -> dict[str, dict[str, str]]:
    """The app's caches (the catalog's cache=True): by container name, its path in the container -> the
    folder relative to {pool}/apps/<app>/."""
    template = _template(app)
    found: dict[str, dict[str, str]] = {}
    if template is None:
        return found
    prefix = f"{{pool}}/apps/{app}/"
    for spec in [template, *template.companions]:
        name = app if spec is template else f"{app}-{spec.name}"
        for volume in spec.volumes:
            if volume.cache and volume.host.startswith(prefix):
                found.setdefault(name, {})[volume.container] = volume.host[len(prefix):]
    return found


def _cache_folders(app: str) -> list[str]:
    return sorted({relative for targets in _caches(app).values() for relative in targets.values()})


def _is_file_target(app: str, container_name: str, target: str) -> bool:
    template = _template(app)
    if template is None:
        return False
    specs = [template, *template.companions]
    for spec in specs:
        name = app if spec is template else f"{app}-{spec.name}"
        if name == container_name:
            return any(volume.container == target and volume.is_file for volume in spec.volumes)
    return False


# The restart policies with which Docker starts a container at its own start (and fails to without its pool).
_RESTARTED = ("always", "unless-stopped")


def _should_run(attrs: dict[str, Any]) -> bool:
    """Whether the container runs, or would: Docker could not start it at boot (its pool missing), its
    policy restarts it and nobody stopped it."""
    state = attrs.get("State") or {}
    policy = ((attrs.get("HostConfig") or {}).get("RestartPolicy") or {}).get("Name")
    return bool(state.get("Running") or state.get("Restarting") or (state.get("Error") and policy in _RESTARTED))


def _container_definition(container: Any) -> dict[str, Any]:
    attrs = container.attrs or {}
    networks = (attrs.get("NetworkSettings") or {}).get("Networks") or {}
    return {
        "name": container.name,
        "image": attrs.get("Image"),
        "config": attrs.get("Config") or {},
        "host_config": attrs.get("HostConfig") or {},
        "networks": {name: {"Aliases": (value or {}).get("Aliases")} for name, value in networks.items()},
        "running": _should_run(attrs),
    }


def definitions(client: Any, group: Any) -> dict[str, Any]:
    """The app's current containers (not ADR-0408's earlier ones) in their start order, and its network."""
    from ..apps import stacks

    nets = []
    name = stacks.network_name(group.key)
    for network in client.networks.list(names=[name]):
        if network.name == name:
            attrs = network.attrs or {}
            nets.append({"name": name, "driver": attrs.get("Driver") or "bridge", "labels": attrs.get("Labels") or {}})
    return {"app": group.key, "pool": app_pool(group),
            "containers": [_container_definition(c) for c in group.containers], "networks": nets}


def _definitions_text(value: dict[str, Any]) -> str:
    return json.dumps(value, indent=1, sort_keys=True) + "\n"


def _same_definitions(text: str | None, value: dict[str, Any]) -> bool:
    """Whether the definitions a copy holds are ``value``, but for whether each container ran: a start or
    a stop alone writes no new copy."""
    def stable(item: dict[str, Any]) -> dict[str, Any]:
        return {**item, "containers": [{k: v for k, v in c.items() if k != "running"} for c in item.get("containers", [])]}

    try:
        return text is not None and stable(json.loads(text)) == stable(value)
    except (json.JSONDecodeError, AttributeError, TypeError):
        return False


def bound_to_card(container: Any) -> bool:
    return CARD_LABEL in (container.labels or {})


# --- the card's space --------------------------------------------------------------------------------


def card_space(ops: StandbyHost) -> tuple[int, int] | None:
    """The card's btrfs size and free bytes; None when it cannot be read."""
    result = ops.read(["df", "-B1", "--output=size,avail", CARD_DIR])
    lines = result.stdout.split("\n")[1:] if result.ok else []
    fields = lines[0].split() if lines else []
    if len(fields) == 2 and all(value.isdigit() for value in fields):
        return int(fields[0]), int(fields[1])
    return None


def sizes(ops: StandbyHost, paths: list[str], *, excludes: list[str] | None = None,
          timeout: float = 300) -> dict[str, int]:
    """``du -sb`` of each path (bytes, apparent), with du's excludes; a path that cannot be read is left out."""
    if not paths:
        return {}
    result = ops.read(["du", "-sb", *(f"--exclude={pattern}" for pattern in excludes or []), "--", *paths],
                      timeout=timeout)
    found = {}
    for line in result.stdout.splitlines():
        value, _, path = line.partition("\t")
        if value.isdigit() and path in paths:
            found[path] = int(value)
    return found


def _copy_excludes(app: str, folder: str) -> list[str]:
    """du's excludes of an app's copy: the copies of ADR-0408 and its caches (full paths)."""
    return [*(f"*{suffix}" for suffix in EXCLUDED_SUFFIXES),
            *(f"{folder}/{relative}" for relative in _cache_folders(app))]


# --- the mark's dialog ---------------------------------------------------------------------------------


class CriticalFolder(BaseModel):
    path: str = Field(description="The host folder or file.")
    bytes: int | None = Field(description="Its size in bytes; null when it is not read (a folder of another pool).")
    copied: bool = Field(description="Whether the standby copies it.")
    kind: Literal["data", "cache", "previous", "other_pool"] = Field(description=(
        "data: a folder of {pool}/apps/<app>/, copied; cache: a cache of the catalog, not copied; previous: a copy "
        "that an update keeps (ADR-0408), not copied; other_pool: a folder of the app on a pool outside its own, "
        "not copied (on the card the app sees an empty read-only folder there)."))


class CriticalPlan(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "app": "vaultwarden", "name": "Vaultwarden", "pool": "data", "critical": False, "interval_hours": 24,
        "intervals": [1, 2, 4, 6, 12, 24],
        "folders": [{"path": "/mnt/pools/data/apps/vaultwarden/data", "bytes": 52428800, "copied": True, "kind": "data"},
                    {"path": "/mnt/pools/data/apps/vaultwarden/data.previous", "bytes": 52000000, "copied": False,
                     "kind": "previous"}],
        "copied_bytes": 52428800, "images_bytes": 98000000, "card_size": 31000000000, "card_free": 20000000000,
        "reserve": 3100000000, "needed": 248428800, "fits": True, "mib_per_sync": None, "failed_over": False}]})

    app: str = Field(description="The app's id.")
    name: str = Field(description="Its name.")
    pool: str | None = Field(description="The pool that holds its data.")
    critical: bool = Field(description="Whether it is marked critical.")
    interval_hours: int = Field(description="Its sync interval: 1, 2, 4, 6, 12 or 24 hours (24 at the daily sync).")
    intervals: list[int] = Field(description="The intervals that can be chosen.")
    folders: list[CriticalFolder] = Field(description="What is copied and what is not.")
    copied_bytes: int = Field(description="The bytes its copy takes, at most (before compression).")
    images_bytes: int = Field(description="The size of its images: their archives, and on a run of the standby their "
                                          "loaded copy, take about as much each.")
    card_size: int | None = Field(description="The card's size in bytes.")
    card_free: int | None = Field(description="The card's free bytes now.")
    reserve: int | None = Field(description="What the card keeps free: 10% of its size, at least 2 GiB.")
    needed: int = Field(description="What a new mark needs on the card: its copy and twice its images.")
    fits: bool = Field(description="Whether a new mark leaves the reserve free; always true for a marked app.")
    mib_per_sync: float | None = Field(description=(
        "The MiB that its recent syncs wrote to the card, on average; null before its first sync: the writes a week "
        "of an interval cannot be told yet."))
    failed_over: bool = Field(description="Whether it runs from the card now.")


def _group_or_404(client: Any, app: str) -> Any:
    group = installed(client, app)
    if group is None:
        raise HTTPException(404, detail=f"No installed app {app}")
    if group.template is None:
        raise HTTPException(409, detail=f"{app} is no app of the catalog: only those can be marked critical")
    return group


def plan(app: str, *, interval: int | None = None, critical: bool | None = None,
         intervals_known: dict[str, Mark] | None = None) -> CriticalPlan:
    """The mark's dialog of ``app`` (blocking): its folders with their sizes, its images, the card."""
    from ..apps import stacks
    from ..apps.router import COPY_SUFFIX, _bind_mounts

    client = _client()
    group = _group_or_404(client, app)
    pool = app_pool(group)
    known = intervals_known or {}
    marked = app in known
    ops = StandbyHost()
    folders: list[CriticalFolder] = []
    copied = 0
    images = 0
    if pool is not None:
        folder = app_folder(pool, app)
        listing = ops.read(["ls", "-1A", "--", folder])
        if not listing.ok:
            raise HTTPException(409, detail=f"The folder {folder} of {app} cannot be read: is the pool {pool} mounted?")
        caches = [f"{folder}/{relative}" for relative in _cache_folders(app)]
        names = [name for name in listing.stdout.split("\n") if name and "/" not in name]
        entries = [f"{folder}/{name}" for name in names if not name.endswith(EXCLUDED_SUFFIXES)
                   and f"{folder}/{name}" not in caches]
        # The copies an update keeps beside its folders, and any other entry of a copy's suffix at the top.
        previous = [f"{folder}/{name}" for name in names if name.endswith(EXCLUDED_SUFFIXES)]
        for container in group.containers:
            spec = group.spec(container)
            marked_copies = {v.container for v in spec.volumes if v.copy_before_update} if spec is not None else set()
            for bind in _bind_mounts(container):
                if bind.target in marked_copies and _within(bind.source, folder):
                    copy = bind.source + COPY_SUFFIX
                    if copy not in previous and ops.exists(copy):
                        previous.append(copy)
        data = sizes(ops, entries, excludes=_copy_excludes(app, folder))
        others = sizes(ops, [path for path in [*caches, *previous] if ops.exists(path)])
        for path in entries:
            folders.append(CriticalFolder(path=path, bytes=data.get(path), copied=True, kind="data"))
            copied += data.get(path, 0)
        for path in caches:
            folders.append(CriticalFolder(path=path, bytes=others.get(path), copied=False, kind="cache"))
        for path in previous:
            folders.append(CriticalFolder(path=path, bytes=others.get(path), copied=False, kind="previous"))
        outside = sorted({bind.source for container in group.containers for bind in _bind_mounts(container)
                          if any(_within(bind.source, base) for base in _bases()) and _relative(bind.source, folder) is None})
        folders += [CriticalFolder(path=path, bytes=None, copied=False, kind="other_pool") for path in outside]
    for image_id in sorted({str((c.attrs or {}).get("Image") or "") for c in group.containers}):
        with contextlib.suppress(*stacks.DOCKER_ERRORS):
            images += int((client.images.get(image_id).attrs or {}).get("Size") or 0)
    space = card_space(ops)
    size, free = space if space else (None, None)
    reserve = reserve_of(size) if size else None
    needed = copied + 2 * images
    fits = marked or (free is not None and reserve is not None and free - needed >= reserve)
    recent = [value for value in stored_app(app).get("mib", []) if isinstance(value, (int, float))]
    return CriticalPlan(
        app=app, name=group.name, pool=pool, critical=marked if critical is None else critical,
        interval_hours=interval or (known[app].interval if app in known else DEFAULT_INTERVAL), intervals=list(INTERVALS), folders=folders,
        copied_bytes=copied, images_bytes=images, card_size=size, card_free=free, reserve=reserve, needed=needed,
        fits=fits, mib_per_sync=round(sum(recent) / len(recent), 1) if recent else None,
        failed_over=failed_over(app) is not None)


def offered() -> str | None:
    """Why no app can be marked critical here; None when the standby is set up and runs from the eMMC."""
    service = _service()
    state = service._state
    if service._unavailable():
        return service._unavailable()
    if state.root == "card":
        return "UNOS runs from the SD standby: the marks wait for the return to the eMMC"
    if not state.armed:
        return "The SD standby is not set up: System > Standby sets it up"
    if not state.card_present:
        return "The standby's card is not in its slot"
    return None


def forget_copy(app: str) -> None:
    """An unmarked app's copies on the card and its record (blocking); refused while it runs from the card."""
    if failed_over(app) is not None:
        raise StandbyAppsError(f"{app} runs from the card: its copies stay")
    ops = StandbyHost()
    for number in _generations(ops, app):
        ops.change(["btrfs", "subvolume", "delete", generation_path(app, number)], what=f"The copy of {app}")
    if ops.exists(run_path(app)):
        ops.change(["btrfs", "subvolume", "delete", run_path(app)], what=f"The run copy of {app}")
    if stored_app(app):
        update_app_record(app, lambda record: None)


def _generations(ops: StandbyHost, app: str) -> list[int]:
    listing = ops.read(["ls", "-1", "--", CARD_DIR])
    pattern = re.compile(rf"{re.escape(APP_PREFIX + app)}-([0-9]+)")
    return sorted(int(m.group(1)) for name in listing.stdout.split() if (m := pattern.fullmatch(name)))


# --- the sync of an app --------------------------------------------------------------------------------


@dataclass
class AppOutcome:
    result: str  # wrote, unchanged
    generation: int | None
    mib: float | None = None
    plan: list[str] = field(default_factory=list)


def _subvolume_of(ops: StandbyHost, folder: str, pool: str) -> str:
    """The pool's subvolume that holds ``folder``: the first of its parents up to the pool's mount point
    whose inode is 256, a btrfs subvolume's root."""
    root = pool_path(pool)
    path = folder
    while True:
        result = ops.read(["stat", "-c", "%i", "--", path])
        if result.ok and result.stdout.strip() == "256":
            return path
        if os.path.normpath(path) == os.path.normpath(root):
            raise StandbyFailed(f"No btrfs subvolume holds {folder}")
        path = os.path.dirname(path)


def _rsync_excludes(app: str) -> list[str]:
    return [*(f"--exclude=*{suffix}" for suffix in EXCLUDED_SUFFIXES),
            *(f"--exclude=/{relative}" for relative in _cache_folders(app))]


APP_RSYNC = ("rsync", "-aHAX", "--numeric-ids", "--delete", "--omit-dir-times")


def sync_app(app: str, *, dry_run: bool = False, last_copy: bool = False, deadline: float | None = None,
             stop: threading.Event | None = None) -> AppOutcome:
    """One sync of a critical app (blocking): a new generation when its folder, images or definitions
    changed. ``last_copy``: from a pool that btrfs forced read-only, which takes no snapshot, by
    ``deadline`` (monotonic). StandbyFailed when it failed; the previous generation stays."""
    from ..apps import stacks

    ops = StandbyHost(dry_run=dry_run, stop=stop)
    try:
        client = _client()
        lock = stacks.acquire(app, app)
    except HTTPException as e:
        raise StandbyFailed(str(e.detail)) from None
    except stacks.DOCKER_ERRORS as e:
        raise StandbyFailed(f"Docker: {e}") from None
    try:
        group = installed(client, app)
        if group is None or not group.containers:
            raise StandbyFailed(f"Docker has no container of {app}")
        if any(bound_to_card(c) for c in group.containers):
            raise StandbyFailed(f"{app} runs from the card: its copy gets no sync")
        defs = definitions(client, group)
    except stacks.DOCKER_ERRORS as e:
        raise StandbyFailed(f"Docker: {e}") from None
    finally:
        lock.release()
    pool = defs["pool"]
    if not pool:
        raise StandbyFailed(f"{app} keeps no data on a pool")
    folder = app_folder(pool, app)

    def remaining(default: float) -> float:
        if deadline is None:
            return default
        left = deadline - time.monotonic()
        if left <= 0:
            raise StandbyFailed("The last copy did not end within its time")
        return min(default, left)

    record = stored_app(app)
    current = record.get("generation") if isinstance(record.get("generation"), int) else None
    gen = generation_path(app, current) if current is not None else None
    excludes = _rsync_excludes(app)
    images = sorted({d["image"] for d in defs["containers"] if _IMAGE_ID.fullmatch(str(d["image"]))})
    text = _definitions_text(defs)
    changed = gen is None
    if gen is not None:
        found = ops.long_read([*APP_RSYNC, "--dry-run", "--itemize-changes", *excludes, f"{folder}/", f"{gen}/{COPY}/"],
                              timeout=remaining(RSYNC_TIMEOUT))
        # 24: files vanished while rsync read the live folder, as a running app's temporary files do.
        if not found.ok and found.returncode != 24:
            raise StandbyFailed(f"The comparison of {app}'s copy: rsync ended with exit status {found.returncode}")
        listing = ops.read(["ls", "-1", "--", f"{gen}/images"])
        have = {name[: -len(".tar.gz")] for name in listing.stdout.split() if name.endswith(".tar.gz")}
        changed = (bool(found.stdout.strip()) or have != {image.split(":", 1)[1] for image in images}
                   or not _same_definitions(ops.read_file(f"{gen}/{DEFINITIONS}"), defs))
    if not changed:
        return AppOutcome(result="unchanged", generation=current)
    space = card_space(ops)
    if space is not None and space[1] < reserve_of(space[0]):
        raise StandbyFailed("The standby's card has no more than its reserve free: no app is copied until there is room")
    service = _service()
    card_disk = service._state.card_device.rsplit("/", 1)[-1] if service._state.card_device else None
    before = None
    if not dry_run and card_disk:
        ops.change(["sync", "-f", CARD_DIR], what="The flush before the sync")
        from .generation import _sectors_written

        before = _sectors_written(card_disk)
    snapshot = None
    source = folder
    if not last_copy:
        subvolume = _subvolume_of(ops, folder, pool)
        snapshot = f"{subvolume}/{SNAPSHOT_NAME}"
        if ops.exists(snapshot):
            ops.change(["btrfs", "subvolume", "delete", snapshot], what="An old snapshot of the app's folder")
        ops.change(["btrfs", "subvolume", "snapshot", "-r", subvolume, snapshot], what=f"The snapshot of {app}'s folder")
        relative = os.path.relpath(folder, subvolume)
        source = snapshot if relative == "." else f"{snapshot}/{relative}"
    existing = _generations(ops, app)
    number = max([*existing, current or 0]) + 1
    new = generation_path(app, number)
    committed = False
    try:
        for stale in existing:
            if stale != current:
                ops.change(["btrfs", "subvolume", "delete", generation_path(app, stale)], what="An unfinished copy")
        if gen is not None:
            ops.change(["btrfs", "subvolume", "snapshot", gen, new], what=f"The new copy of {app}")
        else:
            ops.change(["btrfs", "subvolume", "create", new], what=f"The new copy of {app}")
        ops.change(["mkdir", "-p", f"{new}/{COPY}", f"{new}/images"], what=f"The new copy of {app}")
        ops.long_change([*APP_RSYNC, "--inplace", "--no-whole-file", *excludes, f"{source}/", f"{new}/{COPY}/"],
                        what=f"The copy of {app}'s folder", timeout=remaining(RSYNC_TIMEOUT))
        wanted = {image.split(":", 1)[1] for image in images}
        listing = ops.read(["ls", "-1", "--", f"{new}/images"])
        for name in listing.stdout.split():
            if name.endswith(".tar.gz") and name[: -len(".tar.gz")] not in wanted:
                ops.change(["rm", "-f", "--", f"{new}/images/{name}"], what="An archive of an image no longer used")
        for image in images:
            archive = f"{new}/images/{image.split(':', 1)[1]}.tar.gz"
            if ops.exists(archive) and not dry_run:
                continue
            ops.long_change(["bash", "-c", 'set -o pipefail; docker save "$1" | gzip -1 > "$2.tmp" && mv -f "$2.tmp" "$2"',
                             "unos-standby", image, archive], what=f"The archive of {app}'s image", timeout=remaining(SAVE_TIMEOUT))
        ops.write_file(f"{new}/{DEFINITIONS}", text, what=f"The definitions of {app}")
        ops.change(["sync", "-f", CARD_DIR], what="The flush of the new copy")
        measured = sizes(ops, [f"{new}/{COPY}", f"{new}/images"]) if not dry_run else {}
        mib = None
        if not dry_run and before is not None and card_disk:
            from .generation import _sectors_written

            after = _sectors_written(card_disk)
            mib = round((after - before) * 512 / 1024 / 1024, 1) if after is not None else None
        if not dry_run:
            at = utc_text(utc_now())

            def complete(record: dict[str, Any]) -> dict[str, Any]:
                recent = [v for v in record.get("mib", []) if isinstance(v, (int, float))]
                if mib is not None:
                    recent = [*recent, mib][-RECENT_SYNCS:]
                return {**{k: v for k, v in record.items() if k != "error"}, "generation": number, "synced_at": at,
                        "mib": recent, "pool": pool,
                        "bytes": {"data": measured.get(f"{new}/{COPY}"), "images": measured.get(f"{new}/images")}}

            update_app_record(app, complete)  # the switch: state.json names the complete copy
        committed = True
    except BaseException:
        if not dry_run and not committed:
            with contextlib.suppress(Exception):
                host.run_on_host(["btrfs", "subvolume", "delete", new], timeout=120)
        raise
    finally:
        if snapshot is not None and not dry_run:
            try:
                host.run_on_host(["btrfs", "subvolume", "delete", snapshot], timeout=120)
            except Exception as e:  # the next sync deletes it first
                logger.warning("The snapshot %s could not be deleted: %s", snapshot, e)
    tidy = StandbyHost(dry_run=dry_run)
    if current is not None:
        try:
            tidy.change(["btrfs", "subvolume", "delete", generation_path(app, current)], what="The old copy")
        except StandbyError as e:
            logger.warning("The old copy of %s could not be deleted: %s", app, e)
    ops.plan.extend(tidy.plan)
    return AppOutcome(result="wrote", generation=number, mib=mib, plan=ops.plan)


# --- creating the containers again -------------------------------------------------------------------------


def _card_bound(app: str, definition: dict[str, Any], pool: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """The Config and HostConfig of ``definition`` on the card: the binds of {pool}/apps/<app>/ on the run
    copy's data, a cache on a tmpfs, every other pool bind on an empty read-only folder of the card."""
    from ..apps.stacks import APP_POOL_LABEL

    folder = app_folder(pool, app)
    data = f"{run_path(app)}/{COPY}"
    caches = _caches(app).get(definition["name"], {})
    config = json.loads(json.dumps(definition["config"]))
    host_config = json.loads(json.dumps(definition["host_config"]))
    tmpfs = dict(host_config.get("Tmpfs") or {})

    def moved(source: str, target: str) -> str | None:
        """The bind's new source; None for a cache (a tmpfs)."""
        relative = _relative(source, folder)
        if relative is not None:
            if target in caches:
                return None
            return data if relative == "." else f"{data}/{relative}"
        if any(_within(source, base) for base in _bases()):
            return EMPTY_FILE if _is_file_target(app, definition["name"], target) else EMPTY_DIR
        return source

    mounts = []
    for mount in host_config.get("Mounts") or []:
        if mount.get("Type") != "bind":
            mounts.append(mount)
            continue
        source = moved(str(mount.get("Source")), str(mount.get("Target")))
        if source is None:
            tmpfs[str(mount.get("Target"))] = CACHE_TMPFS
            continue
        readonly = mount.get("ReadOnly", False) or source in (EMPTY_DIR, EMPTY_FILE)
        mounts.append({**mount, "Source": source, "ReadOnly": bool(readonly)})
    binds = []
    for bind in host_config.get("Binds") or []:
        parts = str(bind).split(":")
        if len(parts) < 2:
            binds.append(bind)
            continue
        source = moved(parts[0], parts[1])
        if source is None:
            tmpfs[parts[1]] = CACHE_TMPFS
            continue
        options = parts[2] if len(parts) > 2 else ""
        if source in (EMPTY_DIR, EMPTY_FILE) and "ro" not in options.split(","):
            options = ",".join(filter(None, [options.replace("rw", "").strip(","), "ro"]))
        binds.append(":".join(filter(None, [source, parts[1], options])))
    host_config["Mounts"] = mounts or None
    host_config["Binds"] = binds or None
    host_config["Tmpfs"] = tmpfs or None
    labels = dict(config.get("Labels") or {})
    labels.pop(APP_POOL_LABEL, None)
    labels[CARD_LABEL] = pool
    config["Labels"] = labels
    return config, host_config


def _create(client: Any, definition: dict[str, Any], config: dict[str, Any], host_config: dict[str, Any]) -> Any:
    """Create one container of a definition with this Config and HostConfig, on its networks."""
    image = str(config.get("Image") or "")
    try:
        client.images.get(image)
    except Exception:  # the reference is unknown to this Docker (the card's loads images by id)
        image = str(definition.get("image") or image)
    body = {**config, "Image": image, "HostConfig": host_config}
    networks = definition.get("networks") or {}
    mode = host_config.get("NetworkMode")
    endpoints = {name: {"Aliases": value.get("Aliases")} for name, value in networks.items()
                 if name == mode and name not in ("default", "bridge", "host", "none")}
    if endpoints:
        body["NetworkingConfig"] = {"EndpointsConfig": endpoints}
    answer = client.api.create_container_from_config(body, definition["name"])
    return client.containers.get(answer["Id"])


def _ensure_networks(client: Any, defs: dict[str, Any]) -> None:
    for network in defs.get("networks") or []:
        found = [item for item in client.networks.list(names=[network["name"]]) if item.name == network["name"]]
        if not found:
            client.networks.create(network["name"], driver=network.get("driver") or "bridge",
                                   labels=network.get("labels") or {}, check_duplicate=True)


def _remove_containers(group: Any) -> list[str]:
    """Stop and remove the app's current containers (main first); the names of those that ran."""
    running = [c.name for c in group.containers if c.status in ("running", "restarting")]
    for container in reversed(group.containers):
        with contextlib.suppress(Exception):
            container.stop(timeout=STOP_SECONDS)
        container.remove(force=True)
    return running


def _start(client: Any, app: str, created: list[Any], running: set[str]) -> None:
    """Start those that ran, in their order; a companion the main container depends on is waited for."""
    from ..apps import stacks

    template = _template(app)
    depends = set(template.depends_on) if template is not None else set()
    for container in created:
        if container.name not in running:
            continue
        container.start()
        companion = (container.labels or {}).get(stacks.COMPANION_LABEL)
        if companion in depends:
            spec = next((c for c in template.companions if c.name == companion), None) if template else None
            try:
                stacks.wait_ready(container, started_now=True, seconds=READY_SECONDS,
                                  ready_command=spec.ready_command if spec is not None else None)
            except stacks.NotReady as e:
                logger.warning("%s is not ready: %s", container.name, e)


def _recreate(client: Any, app: str, defs: dict[str, Any], *, card: bool, running: set[str]) -> list[str]:
    """Create the app's containers from ``defs`` again, on the card or on the pool, and start those that ran."""
    _ensure_networks(client, defs)
    created = []
    pool = defs["pool"]
    for definition in defs["containers"]:
        if card:
            config, host_config = _card_bound(app, definition, pool)
        else:
            config, host_config = definition["config"], definition["host_config"]
        created.append(_create(client, definition, config, host_config))
    _start(client, app, created, running)
    return [c.name for c in created]


# --- the move to the card ----------------------------------------------------------------------------


@dataclass
class Moved:
    app: str
    copy: bool
    age: str | None = None
    error: str | None = None


def _copy_age(record: dict[str, Any]) -> str | None:
    at = record.get("synced_at")
    if not isinstance(at, str):
        return None
    try:
        hours = (utc_now() - parse_time(at)).total_seconds() / 3600
    except ValueError:
        return None
    return f"{hours:.0f} h" if hours < 48 else f"{hours / 24:.0f} d"


def _load_images(ops: StandbyHost, app: str, gen: str, images: set[str]) -> None:
    """The images of ``images`` (ids) that this Docker lacks, loaded from the copy's archives."""
    for image in sorted(images):
        match = _IMAGE_ID.fullmatch(image)
        if match and not ops.read(["docker", "image", "inspect", image]).ok:
            ops.change(["docker", "load", "-i", f"{gen}/images/{match.group(1)}.tar.gz"], what=f"An image of {app}",
                       timeout=SAVE_TIMEOUT)


def _images_of(defs: dict[str, Any]) -> set[str]:
    return {str(item.get("image")) for item in defs.get("containers", [])}


def move_app(app: str, reason: Reason, *, dry_run: bool = False, defs: dict[str, Any] | None = None) -> Moved:
    """Move one critical app to the card (blocking): see the module. An app without a copy stays stopped.
    ``defs``: the definitions of an app that this Docker lacks (the standby's first start). A dry run
    reads only."""
    from ..apps import stacks

    if dry_run:
        record = stored_app(app)
        number = record.get("generation") if isinstance(record.get("generation"), int) else None
        return Moved(app=app, copy=number is not None, age=_copy_age(record))
    if _syncing_app == app:
        _stop_app_sync.set()
        kill_sync()
    try:
        client = _client()
        lock = stacks.acquire(app, app)
    except HTTPException as e:
        raise StandbyAppsError(str(e.detail)) from None
    try:
        # Read under the app's lock: a move, a return or an unmark of the app went before or waits.
        record = stored_app(app)
        number = record.get("generation") if isinstance(record.get("generation"), int) else None
        if isinstance(record.get("failed_over"), dict):
            return Moved(app=app, copy=bool(record["failed_over"].get("copy")), age=_copy_age(record))
        group = installed(client, app) if defs is None else None
        if defs is None:
            if group is None or not group.containers:
                raise StandbyAppsError(f"Docker has no container of {app}")
            if all(bound_to_card(c) for c in group.containers):
                # Its containers run from the card already (a record lost): the move is recorded again, and
                # neither its containers nor its run copy are touched.
                running = {c.name for c in group.containers if _should_run(c.attrs or {})}
                _record_move(app, reason, str(record.get("pool") or app_pool(group)), number, running)
                return Moved(app=app, copy=number is not None, age=_copy_age(record))
            defs = definitions(client, group)
        pool = defs.get("pool") or record.get("pool")
        if not pool:
            raise StandbyAppsError(f"{app} keeps no data on a pool")
        defs["pool"] = pool
        running = {d["name"] for d in defs["containers"] if d["running"]}
        if number is None:
            # No copy: the app stops and stays so until its pool is back.
            for container in reversed(group.containers if group is not None else []):
                with contextlib.suppress(*stacks.DOCKER_ERRORS):
                    container.stop(timeout=STOP_SECONDS)
        else:
            ops = StandbyHost()
            gen = generation_path(app, number)
            # The copy's own definitions when its images are not those that run now (an update after the last
            # sync): a newer version would not read the older data. Their images come from the copy.
            copied_text = ops.read_file(f"{gen}/{DEFINITIONS}")
            copied = json.loads(copied_text) if copied_text else None
            if copied and _images_of(copied) != _images_of(defs):
                defs = {**copied, "pool": pool}
                running = {d["name"] for d in defs["containers"] if d["name"] in running or d.get("running")}
                _load_images(ops, app, gen, _images_of(defs))
            run = run_path(app)
            if ops.exists(run):
                ops.change(["btrfs", "subvolume", "delete", run], what=f"An old run copy of {app}")
            ops.change(["btrfs", "subvolume", "snapshot", gen, run], what=f"The run copy of {app}")
            ops.write_file(f"{run}/{DEFINITIONS}", json.dumps(defs, indent=1, sort_keys=True) + "\n",
                           what=f"The definitions of {app}")
            ops.change(["mkdir", "-p", EMPTY_DIR], what="The card's empty folder")
            if not ops.exists(EMPTY_FILE):
                ops.write_file(EMPTY_FILE, "", what="The card's empty file", mode="0444")
            ops.change(["chmod", "0555", EMPTY_DIR], what="The card's empty folder")
            if group is not None:
                _remove_containers(group)
            try:
                _recreate(client, app, defs, card=True, running=running)
            except BaseException as e:
                # Its pool-bound containers are gone: the move is recorded, so that the start of the
                # supervisor creates them on the card again (reapply) and the alert names the app.
                _record_move(app, reason, pool, number, running, error=str(e))
                raise
        _record_move(app, reason, pool, number, running)
    except stacks.DOCKER_ERRORS as e:
        raise StandbyAppsError(f"Docker: {e}") from None
    finally:
        lock.release()
    return Moved(app=app, copy=number is not None, age=_copy_age(record))


def _record_move(app: str, reason: str, pool: str, number: int | None, running: set[str], *,
                 error: str | None = None) -> None:
    entry = {"at": utc_text(utc_now()), "reason": reason, "pool": pool, "copy": number is not None,
             "generation": number, "running": sorted(running), **({"error": error} if error else {})}
    update_app_record(app, lambda record: {**record, "pool": pool, "failed_over": entry})


def _missing_pool_sources(ops: StandbyHost, defs: dict[str, Any]) -> list[str]:
    """The bind sources below the pools' folder that the host lacks: a container with one is never made."""
    missing = []
    for definition in defs.get("containers", []):
        host_config = definition.get("host_config") or {}
        sources = [str(m.get("Source")) for m in host_config.get("Mounts") or [] if m.get("Type") == "bind"]
        sources += [str(bind).split(":", 1)[0] for bind in host_config.get("Binds") or []]
        for source in sources:
            if any(_within(source, base) for base in _bases()) and source not in missing and not ops.exists(source):
                missing.append(source)
    return missing


def return_app(app: str, *, dry_run: bool = False) -> None:
    """The discarding return of one app (blocking): its pool-bound containers again from the definitions
    saved at the move, those that ran started, and the run copy deleted. The pool must be mounted, writable
    and healthy, and every pool folder that the containers bind must be there: otherwise nothing changes."""
    from ..apps import stacks

    try:
        client = _client()
        lock = stacks.acquire(app, app)
    except HTTPException as e:
        raise StandbyAppsError(str(e.detail)) from None
    try:
        moved = failed_over(app)
        if moved is None:
            raise StandbyAppsError(f"{app} does not run from the card")
        pool = str(moved.get("pool") or "")
        why = pool_unfit(pool)
        if why:
            raise StandbyAppsError(f"{app} cannot return: {why}")
        ops = StandbyHost(dry_run=dry_run)
        run = run_path(app)
        text = ops.read_file(f"{run}/{DEFINITIONS}") if moved.get("copy") else None
        defs = None
        if text is not None:
            try:
                defs = json.loads(text)
            except json.JSONDecodeError as e:
                raise StandbyAppsError(f"The definitions of {app} cannot be read: {e}") from None
            missing = _missing_pool_sources(ops, defs)
            if missing:
                raise StandbyAppsError(f"{app} cannot return: {', '.join(missing)} is not there")
        if dry_run:
            return
        group = installed(client, app)
        if defs is not None:
            if group is not None:
                _remove_containers(group)
            _recreate(client, app, defs, card=False, running=set(moved.get("running") or []))
        elif group is not None:
            # No copy: the app stopped at the move; those that ran start again.
            names = set(moved.get("running") or [])
            for container in group.containers:
                if container.name in names:
                    container.start()
        update_app_record(app, lambda record: {k: v for k, v in record.items() if k != "failed_over"})
        # The card's changes go last: a run copy left by a failed delete is deleted by the next move.
        if ops.exists(run):
            try:
                ops.change(["btrfs", "subvolume", "delete", run], what=f"The run copy of {app}")
            except StandbyError as e:
                logger.warning("The run copy of %s could not be deleted: %s", app, e)
    except stacks.DOCKER_ERRORS as e:
        raise StandbyAppsError(f"Docker: {e}") from None
    finally:
        lock.release()


def pool_unfit(pool: str) -> str | None:
    """Why the pool cannot take its apps back: not mounted, read-only or degraded; None when it can."""
    from ..storage import pools as storage_pools

    try:
        info = storage_pools.get_pool(pool)
    except Exception as e:  # not found, or its state cannot be read
        return f"the pool {pool} cannot be read: {e}"
    if not info.mounted:
        return f"the pool {pool} is not mounted"
    if info.read_only or info.forced_read_only:
        return f"the pool {pool} is read-only"
    if info.missing_devices:
        return f"the pool {pool} misses {info.missing_devices} device(s)"
    return None


# --- the start ---------------------------------------------------------------------------------------


def reapply(critical: set[str]) -> list[str]:
    """Before the supervisor serves (blocking): an app that state.json records as moved runs from the card
    again. A pool-bound container of it, which this system's Docker may have started at boot, or a container
    that a failed move did not make, is created again on the card from the run copy's definitions; a
    card-bound one that ran and does not run (Docker came up before the card was mounted) starts. The apps
    it changed."""
    from ..apps import stacks

    done = []
    for app, record in stored_apps().items():
        moved = record.get("failed_over")
        if not isinstance(moved, dict) or not moved.get("copy") or app not in critical:
            continue
        try:
            text = StandbyHost().read_file(f"{run_path(app)}/{DEFINITIONS}")
            if text is None:
                logger.error("The run copy of %s on the SD standby has no definitions: it is not moved again", app)
                continue
            defs = json.loads(text)
            names = {d["name"] for d in defs.get("containers", [])}
            running = set(moved.get("running") or [])
            client = _client()
            with stacks.app_lock(app, app):
                group = installed(client, app)
                found = list(group.containers) if group is not None else []
                if found and {c.name for c in found} == names and all(bound_to_card(c) for c in found):
                    stopped = [c for c in found if c.name in running and c.status not in ("running", "restarting")]
                    for container in stopped:
                        container.start()
                    if stopped:
                        done.append(app)
                    continue
                if group is not None:
                    _remove_containers(group)
                _recreate(client, app, defs, card=True, running=running)
            logger.warning("%s runs from the SD standby's card again: its containers were created on the card", app)
            done.append(app)
        except Exception:
            logger.exception("The move of %s to the SD standby could not be applied again", app)
    return done


def start_on_standby(critical: set[str]) -> list[str]:
    """On the standby's root (blocking): each critical app's images loaded, its network and containers
    created from the definitions, companions first: on the card's copy when state.json records it moved,
    else on its pool when that is mounted and healthy and has every folder its containers bind, else moved
    now. The apps it created."""
    created = []
    ops = StandbyHost()
    for app in sorted(critical):
        record = stored_app(app)
        number = record.get("generation")
        moved = record.get("failed_over")
        try:
            client = _client()
            group = installed(client, app)
            if group is not None and group.containers:
                continue  # made at an earlier start of the standby; reapply and the RAID monitor take care
            if not isinstance(number, int):
                logger.error("The critical app %s has no copy on the SD standby: it is not there", app)
                continue
            gen = generation_path(app, number)
            text = ops.read_file(f"{gen}/{DEFINITIONS}")
            if text is None:
                logger.error("The copy of %s on the SD standby has no definitions", app)
                continue
            defs = json.loads(text)
            _load_images(ops, app, gen, _images_of(defs))
            if isinstance(moved, dict):
                run_text = ops.read_file(f"{run_path(app)}/{DEFINITIONS}")
                _recreate(client, app, json.loads(run_text) if run_text else defs, card=True,
                          running=set(moved.get("running") or []))
            elif defs.get("pool") and pool_unfit(defs["pool"]) is None and not _missing_pool_sources(ops, defs):
                _recreate(client, app, defs, card=False, running={d["name"] for d in defs["containers"] if d["running"]})
            else:
                move_app(app, "pool_failed", defs=defs)
            created.append(app)
        except Exception:
            logger.exception("The critical app %s could not be created on the SD standby", app)
    return created


# --- the RAID monitor's pass -------------------------------------------------------------------------


def classify(name: str, info: Any | None, managed: set[str] | None, changing: frozenset[str]) -> str:
    """A pool's state for the move of its apps: ok, read_only (btrfs forced it), failed (managed and not
    mounted, or missing more devices than its data profile tolerates), or skip (unknown, being changed by
    UNOS: the count stays)."""
    if name in changing or managed is None:
        return "skip"
    if info is None:
        return "failed" if name in managed else "ok"
    if getattr(info, "health", None) == "unknown":
        return "skip"
    if not info.mounted:
        return "failed" if name in managed else "ok"
    if info.forced_read_only:
        return "read_only"
    tolerated = TOLERATED.get(str(info.raid_level or "").lower(), 0)
    if info.missing_devices and info.missing_devices > tolerated:
        return "failed"
    return "ok"


def _last_complete(app: str) -> datetime | None:
    record = stored_app(app)
    times = [value for value in (record.get("synced_at"), _checked_at.get(app)) if isinstance(value, str)]
    try:
        return max((parse_time(value) for value in times), default=None)
    except ValueError:
        return None


def interval_due(app: str, hours: int, now: datetime, today: datetime | None) -> bool:
    """Whether the app's sync is due: 24 hours once the day's time of the system's daily sync (``today``)
    passed and no sync completed since; a shorter interval once it passed since the last complete sync
    (less a few minutes, so that the RAID monitor's passes of 5 minutes do not push it later each time)."""
    last = _last_complete(app)
    if hours >= 24:
        return today is not None and now >= today and (last is None or last < today)
    return last is None or now - last >= timedelta(hours=hours) - timedelta(minutes=3)


async def on_pools_read(pools: list[Any], managed: set[str] | None, changing: frozenset[str]) -> None:
    """At each pass of the RAID monitor: the moves of the critical apps of a failed pool, then the syncs
    that are due, of the pools whose disks are awake. Never raises."""
    try:
        await _on_pools_read(pools, managed, changing)
    except Exception:
        logger.exception("The SD standby's critical apps could not be checked")


async def _on_pools_read(pools: list[Any], managed: set[str] | None, changing: frozenset[str]) -> None:
    global _passed
    service = _service()
    if host.runs_locally() or not (service._state.armed or service._state.root == "card") \
            or not service._state.card_present:
        return
    _passed = True
    critical = await marks()
    if not critical:
        return
    by_pool: dict[str, list[str]] = {}
    for app in critical:
        pool = stored_app(app).get("pool") or await asyncio.to_thread(_pool_of, app)
        if pool:
            by_pool.setdefault(str(pool), []).append(app)
    infos = {info.name: info for info in pools}
    for pool, apps in sorted(by_pool.items()):
        state = classify(pool, infos.get(pool), managed, changing)
        if state == "skip":
            continue
        if state == "ok":
            _failed_passes.pop(pool, None)
            continue
        _failed_passes[pool] = _failed_passes.get(pool, 0) + 1
        pending = [app for app in sorted(apps) if failed_over(app) is None and app not in _moving]
        if not pending:
            continue
        if state == "read_only":
            _spawn(_move_pool(pool, pending, "pool_read_only", last_copy=True))
        elif _failed_passes[pool] >= FAILED_PASSES:
            _spawn(_move_pool(pool, pending, "pool_failed"))
    if service._state.root == "card" or service._lock.locked() or service._can_sync() is not None:
        return
    now = utc_now()
    try:
        zone = await asyncio.to_thread(service._host_zone)
        _, today, _ = service._daily_due(now, zone)
    except Exception:  # the host's zone: the daily slot stays unknown this pass
        today = None
    due = [app for app, mark in sorted(critical.items())
           if failed_over(app) is None and app not in _moving and interval_due(app, mark.interval, now, today)]
    if due:
        _spawn(_sync_due(due, infos))


def _pool_of(app: str) -> str | None:
    try:
        group = installed(_client(), app)
    except Exception:
        return None
    return app_pool(group) if group is not None else None


def _spawn(coroutine: Any) -> None:
    task = asyncio.create_task(coroutine, name="unos-standby-apps")
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _sync_due(apps: list[str], infos: dict[str, Any]) -> None:
    """The due syncs, one at a time with the system's: a pool not mounted or whose disks sleep is passed
    over, so that no disk is woken; the first pass that finds them awake syncs."""
    from ..storage import spindown

    service = _service()
    if service._lock.locked():
        return
    async with service._lock:
        for app in apps:
            if service._can_sync() is not None or failed_over(app) is not None or app in _moving:
                continue
            pool = stored_app(app).get("pool") or await asyncio.to_thread(_pool_of, app)
            info = infos.get(str(pool)) if pool else None
            if info is None or not info.mounted or info.forced_read_only:
                continue
            try:
                if await asyncio.to_thread(spindown.pool_sleeps, str(pool), _asleep):
                    continue
            except Exception as e:
                logger.warning("Whether the disks of the pool %s sleep cannot be read: %s", pool, e)
                continue
            await run_app_sync(app)


async def run_app_sync(app: str, *, last_copy: bool = False, deadline: float | None = None) -> AppOutcome | None:
    """One sync of an app in a worker thread; its failure into state.json. The caller holds service._lock."""
    global _syncing_app
    service = _service()
    _stop_app_sync.clear()
    _syncing_app = app
    service._state.syncing = True
    try:
        outcome = await asyncio.to_thread(sync_app, app, last_copy=last_copy, deadline=deadline, stop=_stop_app_sync)
    except (StandbyError, HTTPException) as e:
        error = str(e.detail) if isinstance(e, HTTPException) else str(e)
        logger.warning("The sync of the critical app %s to the SD standby failed: %s", app, error)
        with contextlib.suppress(StandbyError):
            await asyncio.to_thread(update_app_record, app, lambda record: {**record, "error": error})
        return None
    except Exception:
        logger.exception("The sync of the critical app %s to the SD standby failed", app)
        return None
    finally:
        _syncing_app = None
        service._state.syncing = False
    if outcome.result == "unchanged":
        _checked_at[app] = utc_text(utc_now())
        if stored_app(app).get("error"):
            await asyncio.to_thread(update_app_record, app, lambda record: {k: v for k, v in record.items()
                                                                              if k != "error"})
    else:
        logger.info("The critical app %s was synced to the SD standby: copy %s, %s MiB", app, outcome.generation,
                    outcome.mib)
    return outcome


async def _move_pool(pool: str, apps: list[str], reason: Reason, *, last_copy: bool = False) -> None:
    """The move of a failed pool's critical apps, audited as the system's and alerted."""
    service = _service()
    _moving.update(apps)
    try:
        if last_copy and not service._lock.locked() and service._state.root != "card":
            readable = await asyncio.to_thread(lambda: StandbyHost().read(["ls", "--", app_folder(pool, apps[0])]).ok)
            if readable:
                deadline = time.monotonic() + LAST_COPY_SECONDS
                async with service._lock:
                    for app in apps:
                        if time.monotonic() < deadline:
                            await run_app_sync(app, last_copy=True, deadline=deadline)
        await move(apps, reason, request=None, username="system")
    finally:
        _moving.difference_update(apps)


async def move(apps: list[str], reason: Reason, *, request: Any, username: str, dry_run: bool = False) -> list[Moved]:
    """Move ``apps`` to the card, each in a worker thread; audited and alerted (apps_on_standby)."""
    from ..audit.changes import audit_change
    from ..audit.schemas import AuditAction, AuditSeverity

    results: list[Moved] = []
    for app in apps:
        try:
            _moving.add(app)
            results.append(await asyncio.to_thread(move_app, app, reason, dry_run=dry_run))
        except (StandbyError, HTTPException) as e:
            error = str(e.detail) if isinstance(e, HTTPException) else str(e)
            logger.error("The move of %s to the SD standby failed: %s", app, error)
            results.append(Moved(app=app, copy=False, error=error))
        except Exception as e:
            logger.exception("The move of %s to the SD standby failed", app)
            results.append(Moved(app=app, copy=False, error=f"{type(e).__name__}: {e}"))
        finally:
            _moving.discard(app)
    if dry_run:
        return results
    done = [item for item in results if item.error is None]
    if done:
        logger.warning("Critical apps moved to the SD standby (%s): %s", reason,
                       ", ".join(f"{item.app} ({'copy of ' + (item.age or '?') if item.copy else 'no copy: stopped'})"
                                 for item in done))
    for item in results:
        if username == "system" and item.error is not None and _move_failures.get(item.app) == item.error:
            continue  # the same failure of the automatic move at each pass is audited once
        if item.error is not None:
            _move_failures[item.app] = item.error
        else:
            _move_failures.pop(item.app, None)
        await audit_change(AuditAction.STANDBY_APPS_MOVED, request, username, resource_type="app",
                           resource_id=item.app, success=item.error is None,
                           severity=AuditSeverity.CRITICAL if username == "system" else AuditSeverity.WARNING,
                           details=f"Moved {item.app} to the SD standby ({reason})"
                           + ("" if item.copy else ": no copy, the app stays stopped")
                           + (f": failed: {item.error}" if item.error else ""))
    await _service().update_problems()
    return results


async def return_apps(apps: list[str], *, request: Any, username: str, dry_run: bool = False) -> list[str]:
    from ..audit.changes import audit_change
    from ..audit.schemas import AuditAction, AuditSeverity

    returned = []
    for app in apps:
        try:
            await asyncio.to_thread(return_app, app, dry_run=dry_run)
        except StandbyError as e:
            if not dry_run:
                await audit_change(AuditAction.STANDBY_APPS_RETURNED, request, username, resource_type="app",
                                   resource_id=app, success=False, details=f"Failed: {e}")
            raise HTTPException(409, detail=str(e)) from None
        except HTTPException:
            raise
        except Exception as e:
            logger.exception("The return of %s to its pool failed", app)
            raise HTTPException(500, detail=f"The return of {app} failed: {e}") from None
        returned.append(app)
        if not dry_run:
            await audit_change(AuditAction.STANDBY_APPS_RETURNED, request, username, resource_type="app",
                               resource_id=app, severity=AuditSeverity.WARNING,
                               details=f"Returned {app} to its pool, discarding the SD standby's changes")
    if not dry_run:
        await _service().update_problems()
    return returned


# --- the problems ------------------------------------------------------------------------------------------


def problems_found(critical: dict[str, Mark]) -> list[tuple[str, str, str, str, str]]:
    """(kind, severity, subject, title, detail) of the critical apps: apps_on_standby, which names the apps
    that run from the card and their copies' age, and standby_copy_old for an app whose copy had no complete
    sync for 3 days (counted from its mark before its first)."""
    found = []
    moved = {app: record["failed_over"] for app, record in stored_apps().items()
             if isinstance(record.get("failed_over"), dict)}
    if moved:
        parts = []
        for app, entry in sorted(moved.items()):
            age = _copy_age(stored_app(app))
            parts.append(f"{app} ({'a copy of ' + age if entry.get('copy') and age else 'no copy: stopped'})")
        found.append(("apps_on_standby", "error", ", ".join(sorted(moved)), "Critical apps run from the SD standby",
                      f"These apps run from the SD standby's card since their pool failed or they were moved: "
                      f"{', '.join(parts)}. Changes made now are on the card only; System > Standby returns them to "
                      "their pool with the card's changes."))
    now = utc_now()
    for app, mark in sorted(critical.items()):
        # Until the first pass of the RAID monitor since the start, whose check of each app is kept in RAM.
        if app in moved or not _passed:
            continue
        since = _last_complete(app) or mark.marked_at
        if since is not None and now - since > COPY_OLD:
            found.append(("standby_copy_old", "warning", app, "The SD standby's copy of an app is old",
                          f"The critical app {app} had no complete sync to the SD standby since {utc_text(since)}."))
    return found


async def unmark_uninstalled(app: str, request: Any, username: str) -> None:
    """After an uninstall: the app's mark and its copies on the card go, audited as app_critical_changed.
    Never raises: the uninstall is done."""
    from ..audit.changes import audit_change
    from ..audit.schemas import AuditAction, AuditSeverity

    try:
        changed, _ = await set_mark(app, False, DEFAULT_INTERVAL)
        if not changed:
            return
        service = _service()
        async with service._lock:
            if service._state.card_present:
                await asyncio.to_thread(forget_copy, app)
        await audit_change(AuditAction.APP_CRITICAL_CHANGED, request, username, resource_type="app", resource_id=app,
                           severity=AuditSeverity.WARNING,
                           details=f"Unmarked {app}, which was uninstalled: its copies on the SD standby deleted")
        await service.update_problems()
    except Exception:
        logger.exception("The critical mark of the uninstalled app %s could not be removed", app)
