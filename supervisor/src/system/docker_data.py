"""Docker's data on a pool (ADR-0486, ADR-0487): where Docker keeps it, and its move onto the first pool.

Docker's data is Docker's data root, as Docker names it, and, when Docker keeps its images in containerd
(Docker 29's containerd image store), containerd's root, which ``root`` of /etc/containerd/config.toml
names (/var/lib/containerd without one). It is on a pool when each part is below that pool's
mountpoint: the BPI-W3 keeps both on its pool, the BPI-F3, whose Docker has the classic overlay2
store, its data root (ADR-0372).

When an administrator makes the first managed pool on a host whose Docker's data is not on a pool,
``after_pool_created`` moves it into the pool's subvolumes ``.docker`` and ``.containerd``. The move
stops every container, the supervisor's own included, so it runs whole in the transient unit
``unos-docker-move`` of the host (``host/unos-docker-move.sh``): the supervisor writes the script,
its settings and the new configuration files (daemon.json's ``data-root``, containerd's ``root`` and
the drop-ins with ``RequiresMountsFor=``) into RUN_DIR, in the host's RAM, records the move in its
state file and starts the unit. Whichever supervisor runs then follows the unit until its result file
says how the move ended, the next one from its start (``recover_after_start``), and alerts and audits
the outcome. ``pool_user`` names Docker for a pool that its configuration keeps Docker's data on, or
that a move goes to: Storage refuses to unmount or remove such a pool (pools.py).
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import re
import shlex
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .. import host, paths, state_files
from ..audit.changes import audit_change
from ..audit.schemas import AuditAction, AuditSeverity
from ..config import get_settings
from ..security.validators import POOL_NAME_PATTERN
from ..times import utc_now, utc_text

logger = logging.getLogger(__name__)

DAEMON_JSON = "/etc/docker/daemon.json"
CONTAINERD_CONFIG = "/etc/containerd/config.toml"
DOCKER_DEFAULT_ROOT = "/var/lib/docker"
CONTAINERD_DEFAULT_ROOT = "/var/lib/containerd"
# The subvolumes of the pool (ADR-0486): names with a dot, which the Storage page neither lists nor deletes.
DOCKER_SUBVOLUME = ".docker"
CONTAINERD_SUBVOLUME = ".containerd"
# The drop-ins that keep Docker and containerd down while the pool is not mounted.
DOCKER_DROPIN = "/etc/systemd/system/docker.service.d/data-root-on-pool.conf"
CONTAINERD_DROPIN = "/etc/systemd/system/containerd.service.d/root-on-pool.conf"
CONTAINERD_SERVICE = "containerd.service"
# What docker info's DriverStatus says of the containerd image store.
CONTAINERD_STORE = ("driver-type", "io.containerd.snapshotter.v1")
# The move's folder in the host's RAM (/run): its script, settings, new files, the files they replace
# and its result. Nothing of it is written to the system disk.
RUN_DIR = "/run/unos-docker-move"
RESULT = f"{RUN_DIR}/result"
UNIT = "unos-docker-move"
HOST_SCRIPT = Path(__file__).resolve().parent / "host" / "unos-docker-move.sh"
# The move in progress, from its start until its outcome is recorded: in the data directory, which
# the move copies with the supervisor's volume, so that the supervisor that starts from the pool, or
# again from the old root, finds it.
STATE_FILE = paths.DOCKER_MOVE
# What the unit's result says, and what each outcome means.
OUTCOMES = ("moved", "restored", "failed")
HOST_TIMEOUT_SECONDS = 30
SYSTEMD_RUN_TIMEOUT_SECONDS = 60
UNIT_STOP_TIMEOUT_SECONDS = 1200
# How often a supervisor reads the unit's result while the move runs; it writes nothing meanwhile.
FOLLOW_INTERVAL_SECONDS = 3.0
# The storage drivers (and snapshotters of the containerd image store) whose layers are folders that
# rsync copies as they are; btrfs's and zfs's are subvolumes and datasets, which it would not keep.
COPIED_DRIVERS = ("overlay2", "overlayfs", "fuse-overlayfs", "vfs")
# The states of a unit whose script has not ended yet.
UNIT_RUNS = {"active", "activating", "deactivating", "reloading"}
# The user of the audit entries of what the supervisor does on its own.
SYSTEM = "system"
# A path that the move's script and configuration files may take as they are: absolute, at least two
# components, without spaces, quotes or "..".
_SAFE_PATH = re.compile(r"(/[A-Za-z0-9_][A-Za-z0-9._-]*){2,}")
# A top-level key root of containerd's config.toml, and the first table, after which no key is top-level.
_ROOT_LINE = re.compile(r"^[ \t]*root[ \t]*=.*$", re.MULTILINE)
_FIRST_TABLE = re.compile(r"^[ \t]*\[", re.MULTILINE)


class DockerDataError(Exception):
    """Where Docker keeps its data cannot be told, or the move cannot start; ``status`` is the HTTP status
    of the answer: 503 while Docker does not answer, 500 for the rest."""

    def __init__(self, message: str, status: int = 500) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Placement:
    """Where Docker keeps its data now."""

    docker_root: str
    # None: Docker keeps its images itself (the classic overlay2 store), or its containerd's root is
    # inside its own.
    containerd_root: str | None
    # The pool that holds every part; None when one is not on a pool.
    pool: str | None
    # Docker's storage driver, or the snapshotter of its containerd image store.
    driver: str = ""


@dataclass(frozen=True)
class Plan:
    """A move onto a pool: the roots now and on the pool, and the configuration files to install."""

    pool: str
    pool_dir: str
    docker_old: str
    docker_new: str
    # Both empty when containerd's root stays: no containerd service, or its root is on a pool already.
    containerd_old: str
    containerd_new: str
    # The new files by their name in RUN_DIR/new: the file each replaces on the host, and its text.
    files: dict[str, tuple[str, str]]


@dataclass(frozen=True)
class Outcome:
    """How the unit ended the move: moved, restored or failed, the step that failed, and what to say."""

    result: Literal["moved", "restored", "failed"]
    step: str
    text: str


@dataclass(frozen=True)
class MoveStart:
    """What Create Pool says of the move: started, or not started and why."""

    started: bool
    error: str | None = None

    def audit_text(self) -> str:
        if self.started:
            return f"Docker's data moves onto it in the unit {UNIT} of the host"
        return f"Docker's data could not be moved onto it: {self.error}"


class _State:
    def __init__(self) -> None:
        # The move in progress (its record in STATE_FILE), or None.
        self.move: dict[str, str] | None = None
        self.task: asyncio.Task | None = None
        self.lock = asyncio.Lock()


_state = _State()


# --- where Docker keeps its data --------------------------------------------------------------------


def _docker_info() -> dict[str, Any]:
    """What Docker says of itself (replaced in tests); one of NO_ANSWER when Docker does not answer."""
    from ..containers.docker import get_client

    client = get_client()
    try:
        return client.info()
    finally:
        client.close()


def _info() -> dict[str, Any]:
    import requests

    from ..containers.docker import NO_ANSWER

    try:
        info = _docker_info()
    except (*NO_ANSWER, requests.exceptions.Timeout) as e:
        raise DockerDataError(f"Docker is not available: {e}", 503) from e
    except Exception as e:  # what docker-py raises for an answer it cannot use
        raise DockerDataError(f"Docker did not say where it keeps its data: {e}") from e
    if not isinstance(info, dict):
        raise DockerDataError("Docker did not say where it keeps its data")
    return info


def _host_file(path: str) -> str | None:
    """The text of a file of the host; None when it does not exist. DockerDataError when it cannot be read."""
    try:
        result = host.run_on_host(["cat", "--", path], timeout=HOST_TIMEOUT_SECONDS, errors="replace")
    except (subprocess.SubprocessError, OSError) as e:
        raise DockerDataError(f"{path} cannot be read: {e}") from e
    if result.ok:
        return result.stdout
    if "No such file or directory" in result.stderr:
        return None
    raise DockerDataError(f"{path} cannot be read: {result.stderr.strip() or f'exit status {result.returncode}'}")


def _daemon_settings(text: str | None) -> dict[str, Any]:
    """The settings of daemon.json; none without the file or with an empty one."""
    if text is None or not text.strip():
        return {}
    try:
        settings = json.loads(text)
    except ValueError as e:
        raise DockerDataError(f"{DAEMON_JSON} is not valid JSON: {e}") from e
    if not isinstance(settings, dict):
        raise DockerDataError(f"{DAEMON_JSON} holds no JSON object")
    return settings


def _configured_docker_root(settings: dict[str, Any]) -> str:
    root = settings.get("data-root", DOCKER_DEFAULT_ROOT)
    if not isinstance(root, str) or not root.startswith("/"):
        raise DockerDataError(f"data-root of {DAEMON_JSON} is no absolute path: {root!r}")
    return os.path.normpath(root)


def _containerd_root(text: str | None) -> str:
    """containerd's root as its config.toml names it; its default without the file or the key."""
    if text is None or not text.strip():
        return CONTAINERD_DEFAULT_ROOT
    try:
        config = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise DockerDataError(f"{CONTAINERD_CONFIG} is not valid TOML: {e}") from e
    root = config.get("root", CONTAINERD_DEFAULT_ROOT)
    if not isinstance(root, str) or not root.startswith("/"):
        raise DockerDataError(f"root of {CONTAINERD_CONFIG} is no absolute path: {root!r}")
    return os.path.normpath(root)


def configured_containerd_root() -> str:
    """containerd's root as its configuration names it now. DockerDataError when it cannot be read. Blocks."""
    return _containerd_root(_host_file(CONTAINERD_CONFIG))


def _containerd_service() -> bool:
    """Whether the host runs containerd as a service of its own (Docker's packages do), not inside Docker."""
    try:
        result = host.run_on_host(["systemctl", "show", "--property=LoadState", "--value", CONTAINERD_SERVICE],
                                  timeout=HOST_TIMEOUT_SECONDS, errors="replace")
    except (subprocess.SubprocessError, OSError) as e:
        raise DockerDataError(f"systemctl could not say whether {CONTAINERD_SERVICE} exists: {e}") from e
    if not result.ok:
        raise DockerDataError(f"systemctl could not say whether {CONTAINERD_SERVICE} exists: "
                              f"{result.stderr.strip() or f'exit status {result.returncode}'}")
    return result.stdout.strip() == "loaded"


def _uses_containerd_store(info: dict[str, Any]) -> bool:
    status = info.get("DriverStatus") or []
    return any(isinstance(pair, list) and tuple(pair) == CONTAINERD_STORE for pair in status)


def pool_of(path: str) -> str | None:
    """The pool whose mountpoint holds ``path``, by its name; None for a path that is on no pool."""
    base = os.path.normpath(get_settings().pools_base_path)
    path = os.path.normpath(path)
    if not path.startswith(base + "/"):
        return None
    name = path[len(base) + 1:].split("/", 1)[0]
    return name if POOL_NAME_PATTERN.fullmatch(name) else None


def _data_root(info: dict[str, Any]) -> str:
    """Docker's data root as Docker's info names it."""
    root = info.get("DockerRootDir")
    if not isinstance(root, str) or not root.startswith("/"):
        raise DockerDataError("Docker names no data root")
    return os.path.normpath(root)


def _store_root(info: dict[str, Any]) -> str | None:
    """containerd's root when Docker keeps its images in the containerd image store of the host's containerd
    service; None when Docker keeps them below its data root (its classic store, or a containerd of its own)."""
    if _uses_containerd_store(info) and _containerd_service():
        return configured_containerd_root()
    return None


def images_root(info: dict[str, Any]) -> str:
    """The folder whose file system holds Docker's images (ADR-0490): containerd's root when Docker keeps them
    in the containerd image store of the host's containerd service, else Docker's data root, by ``info``
    (Docker's info). DockerDataError when Docker names no data root or containerd's configuration cannot be
    read. Blocks."""
    return _store_root(info) or _data_root(info)


def placement() -> Placement:
    """Where Docker keeps its data now. DockerDataError (503) while Docker does not answer, (500) when
    containerd's configuration cannot be read. Blocks: call it in a worker thread."""
    info = _info()
    docker_root = _data_root(info)
    containerd_root = _store_root(info)
    held = {pool_of(path) for path in (docker_root, containerd_root) if path is not None}
    pool = next(iter(held)) if len(held) == 1 else None
    driver = info.get("Driver")
    return Placement(docker_root, containerd_root, pool, driver if isinstance(driver, str) else "")


def moving_to() -> str | None:
    """The pool that a move of Docker's data goes to while it runs."""
    return _state.move["pool"] if _state.move else None


def pool_user(name: str) -> str | None:
    """What of Docker keeps the pool ``name`` in use (ADR-0487): the move onto it while it runs, or
    Docker's data root and containerd's root that their configuration puts on it, also while Docker is
    down; None when nothing. The configuration, not Docker, is asked: its drop-ins need the pool at
    every start. DockerDataError when it cannot be read. Blocks."""
    if moving_to() == name:
        return "the move of Docker's data onto it, which runs"
    roots = [("Docker's data root", _configured_docker_root(_daemon_settings(_host_file(DAEMON_JSON))))]
    roots.append(("containerd's root", configured_containerd_root()))
    held = [f"{what} {path}" for what, path in roots if pool_of(path) == name]
    return f"Docker's data ({', '.join(held)})" if held else None


# --- the move's plan and files ------------------------------------------------------------------------


def _check_old_root(path: str) -> None:
    if not _SAFE_PATH.fullmatch(path) or ".." in path.split("/"):
        raise DockerDataError(f"UNOS does not move a root at {path!r}: it moves roots at plain paths only")
    if pool_of(path) is not None or path.startswith(os.path.normpath(get_settings().pools_base_path) + "/"):
        raise DockerDataError(f"{path} is below the pools' folder already")


def daemon_json_with_root(text: str | None, root: str) -> str:
    """daemon.json with ``data-root`` set to ``root``, its other settings as they are."""
    settings = _daemon_settings(text)
    return json.dumps({**settings, "data-root": root}, indent=2) + "\n"


def containerd_config_with_root(text: str | None, root: str) -> str:
    """containerd's config.toml with the top-level ``root`` set to ``root``, its other lines as they are:
    the key's line replaced, or a line put first. A new file is a version 2 configuration, which
    containerd 1.x reads and 2.x converts."""
    line = f'root = "{root}"'
    if text is None or not text.strip():
        rendered = f"version = 2\n{line}\n"
    else:
        first_table = _FIRST_TABLE.search(text)
        top = text[: first_table.start()] if first_table else text
        existing = _ROOT_LINE.search(top)
        if existing:
            rendered = text[: existing.start()] + line + text[existing.end():]
        else:
            rendered = f"{line}\n{text}"
    if _containerd_root(rendered) != root:
        raise DockerDataError(f"{CONTAINERD_CONFIG} could not be given the root {root}")
    return rendered


def _dropin(pool: str, roots: list[str], service: str) -> str:
    return (f"# Written by UNOS (ADR-0486, ADR-0487): {service} keeps its data on the pool {pool} and starts\n"
            f"# only while the pool is mounted.\n[Unit]\nRequiresMountsFor={' '.join(roots)}\n")


def plan_move(pool: str) -> Plan | None:
    """The move of Docker's data onto ``pool``; None when it is on a pool already. DockerDataError when
    where it is, or its configuration, cannot be read, or it cannot be moved. Blocks."""
    if not POOL_NAME_PATTERN.fullmatch(pool):
        raise DockerDataError(f"{pool!r} is no pool's name")
    where = placement()
    if where.pool is not None:
        return None
    if where.driver not in COPIED_DRIVERS:
        raise DockerDataError(f"Docker's storage driver {where.driver or '(none named)'} keeps its layers in a way "
                              f"that UNOS does not copy; it moves those of {', '.join(COPIED_DRIVERS)}")
    from ..storage import pools

    pool_dir = os.path.normpath(pools.pool_mountpoint(pool))
    docker_new = f"{pool_dir}/{DOCKER_SUBVOLUME}"
    docker_text = _host_file(DAEMON_JSON)
    files: dict[str, tuple[str, str]] = {"daemon.json": (DAEMON_JSON, daemon_json_with_root(docker_text, docker_new))}
    _check_old_root(where.docker_root)
    containerd_old = containerd_new = ""
    if _containerd_service():
        configured = _host_file(CONTAINERD_CONFIG)
        current = _containerd_root(configured)
        if pool_of(current) is None:
            _check_old_root(current)
            containerd_old, containerd_new = current, f"{pool_dir}/{CONTAINERD_SUBVOLUME}"
            files["config.toml"] = (CONTAINERD_CONFIG, containerd_config_with_root(configured, containerd_new))
            files["containerd.conf"] = (CONTAINERD_DROPIN, _dropin(pool, [containerd_new], "containerd"))
    for old in (where.docker_root, containerd_old):
        for new in (docker_new, containerd_new):
            if old and new and (new == old or new.startswith(old + "/")):
                raise DockerDataError(f"{new} is inside {old}")
    required = [path for path in (docker_new, containerd_new) if path]
    files["docker.conf"] = (DOCKER_DROPIN, _dropin(pool, required, "Docker"))
    return Plan(pool, pool_dir, where.docker_root, docker_new, containerd_old, containerd_new, files)


def move_settings(plan: Plan) -> str:
    """move.env, which the script sources: each value quoted for the shell."""
    values = {
        "POOL_DIR": plan.pool_dir, "DOCKER_OLD": plan.docker_old, "DOCKER_NEW": plan.docker_new,
        "CONTAINERD_OLD": plan.containerd_old, "CONTAINERD_NEW": plan.containerd_new,
        "FILES": " ".join(f"{name}:{target}" for name, (target, _) in plan.files.items()),
    }
    return "".join(f"{key}={shlex.quote(value)}\n" for key, value in values.items())


def _run(cmd: list[str], what: str, timeout: float = HOST_TIMEOUT_SECONDS) -> host.HostResult:
    try:
        result = host.run_on_host(cmd, timeout=timeout, errors="replace")
    except subprocess.TimeoutExpired:
        raise DockerDataError(f"{what}: {cmd[0]} did not answer within {timeout:g} seconds") from None
    except OSError as e:
        raise DockerDataError(f"{what}: {cmd[0]} could not run: {e}") from e
    if not result.ok:
        raise DockerDataError(f"{what}: {result.stderr.strip() or f'exit status {result.returncode}'}")
    return result


def _write(path: str, text: str, mode: str) -> None:
    try:
        host.write_host_file_atomic(path, text, mode=mode)
    except host.HostCommandError as e:
        raise DockerDataError(f"{path} cannot be written: {e.stderr.strip() or e}") from None
    except (subprocess.SubprocessError, OSError) as e:
        raise DockerDataError(f"{path} cannot be written: {e}") from e


def _stage(plan: Plan) -> None:
    """The move's folder in the host's RAM: the script, move.env and the new files. Blocks."""
    _run(["rm", "-rf", "--", RUN_DIR], f"{RUN_DIR} cannot be emptied")
    _run(["mkdir", "-p", "-m", "0700", f"{RUN_DIR}/new", f"{RUN_DIR}/old"], f"{RUN_DIR} cannot be made")
    _write(f"{RUN_DIR}/move.sh", HOST_SCRIPT.read_text(), "0600")
    _write(f"{RUN_DIR}/move.env", move_settings(plan), "0600")
    for name, (_, text) in plan.files.items():
        _write(f"{RUN_DIR}/new/{name}", text, "0600")


def _start_unit(plan: Plan) -> None:
    _run([
        "systemd-run", f"--unit={UNIT}", f"--description=UNOS: Docker's data moves onto the pool {plan.pool}",
        # Unloaded once the script ended, also after a failure: its result is in the file.
        "--collect", "--quiet",
        # A stop of the unit before Docker answers from the pool restores (the script's trap): up to 680 s
        # of unos.service's start and the containers' within this.
        f"--property=TimeoutStopSec={UNIT_STOP_TIMEOUT_SECONDS}",
        # A system unit has no HOME of its own; the docker command reads its settings there.
        "--setenv=HOME=/root",
        "/bin/sh", f"{RUN_DIR}/move.sh", RUN_DIR,
    ], f"The unit {UNIT} did not start", timeout=SYSTEMD_RUN_TIMEOUT_SECONDS)


def _unit_runs() -> bool:
    """Whether the move's unit still runs (True when that cannot be told)."""
    try:
        shown = host.run_on_host(["systemctl", "show", "--property=ActiveState", "--value", f"{UNIT}.service"],
                                 timeout=HOST_TIMEOUT_SECONDS, errors="replace")
    except (subprocess.SubprocessError, OSError):
        return True
    return not shown.ok or shown.stdout.strip() in UNIT_RUNS


def read_result() -> Outcome | None:
    """How the unit says the move ended; None while there is no result (or it cannot be read yet)."""
    try:
        text = _host_file(RESULT)
    except DockerDataError as e:
        logger.warning("The result of the move of Docker's data cannot be read yet: %s", e)
        return None
    if text is None:
        return None
    lines = text.split("\n")
    result, step, message = (lines + ["", "", ""])[:3]
    if result not in OUTCOMES:
        return Outcome("failed", "", f"The unit {UNIT} wrote an unknown result: {' '.join(text.split())[:200]}")
    return Outcome(result, step, message.strip())  # type: ignore[arg-type]  # one of OUTCOMES


def _forget_run_dir() -> None:
    try:
        host.run_on_host(["rm", "-rf", "--", RUN_DIR], timeout=HOST_TIMEOUT_SECONDS)
    except (subprocess.SubprocessError, OSError) as e:
        logger.warning("Could not remove %s: %s", RUN_DIR, e)


# --- the move's record --------------------------------------------------------------------------------


# The record of a move: its pool, when and by whom it started, and the roots it moves.
RECORD_KEYS = ("pool", "started_at", "by", "docker_old", "containerd_old")


def _check_record(data: object) -> dict[str, str]:
    if not isinstance(data, dict) or not all(isinstance(data.get(key), str) for key in RECORD_KEYS):
        raise ValueError("it holds no move of Docker's data")
    return {key: data[key] for key in RECORD_KEYS}


def _load_record() -> dict[str, str] | None:
    return state_files.load_json(STATE_FILE, _check_record, lambda: None, what="the move of Docker's data")


def _save_record(record: dict[str, str]) -> None:
    state_files.replace_file(STATE_FILE, json.dumps(record).encode(), sync=True)


def _forget_record() -> None:
    STATE_FILE.unlink(missing_ok=True)


# --- the move -----------------------------------------------------------------------------------------


async def after_pool_created(pool: str, username: str) -> MoveStart | None:
    """After an administrator made the pool ``pool``: when it is the first managed pool and Docker's data
    is not on a pool, start the move onto it (ADR-0487). None when no move is due; MoveStart otherwise,
    with why it did not start. Never raises."""
    from ..storage import pools
    from ..updates.updater import get_updater

    async with _state.lock:
        try:
            if await asyncio.to_thread(pools.managed_pool_names) - {pool}:
                return None
        except Exception as e:  # the fstab cannot be read: no move rather than a wrong one
            logger.warning("Docker's data stays where it is: the host's fstab cannot be read: %s", e)
            return MoveStart(False, f"the host's fstab cannot be read: {e}")
        if _state.move is not None:
            return MoveStart(False, f"a move onto the pool {_state.move['pool']} runs")
        if get_updater().running():
            return MoveStart(False, "an update of UNOS runs, which restarts UNOS's containers too")
        try:
            plan = await asyncio.to_thread(plan_move, pool)
            if plan is None:
                return None
            await _start(plan, username)
        except DockerDataError as e:
            logger.warning("Docker's data stays where it is: %s", e)
            return MoveStart(False, str(e))
        except Exception as e:  # the pool is made: its answer and audit entry say the move did not start
            logger.exception("The move of Docker's data onto the pool %s could not start", pool)
            return MoveStart(False, str(e) or type(e).__name__)
    return MoveStart(True)


async def _start(plan: Plan, username: str) -> None:
    record = {"pool": plan.pool, "started_at": utc_text(utc_now()), "by": username, "docker_old": plan.docker_old,
              "containerd_old": plan.containerd_old}
    await asyncio.to_thread(_stage, plan)
    try:
        await asyncio.to_thread(_save_record, record)
    except OSError as e:
        await asyncio.to_thread(_forget_run_dir)
        raise DockerDataError(f"The move's record cannot be written: {e}") from e
    _state.move = record
    try:
        await asyncio.to_thread(_start_unit, plan)
    except DockerDataError as e:
        # systemd-run may have started the unit and then not answered: one that runs or has ended is
        # followed, whatever systemd-run said.
        if await asyncio.to_thread(read_result) is None and not await asyncio.to_thread(_unit_runs):
            _state.move = None
            await asyncio.to_thread(_forget_record)
            await asyncio.to_thread(_forget_run_dir)
            raise
        logger.warning("Starting the unit %s answered %s, but it runs: following it", UNIT, e)
    logger.info("Moving Docker's data onto the pool %s in the unit %s of the host", plan.pool, UNIT)
    _follow_in_background(record)


def _follow_in_background(record: dict[str, str]) -> None:
    _state.task = asyncio.get_running_loop().create_task(_follow(record), name="unos-docker-move")


async def _follow(record: dict[str, str]) -> None:
    """Wait until the unit has ended the move, then record how: its result, read every
    FOLLOW_INTERVAL_SECONDS. A unit that ended without one (the host restarted meanwhile) is recorded
    as failed, with where Docker keeps its data now."""
    while True:
        outcome = await asyncio.to_thread(read_result)
        if outcome is None and not await asyncio.to_thread(_unit_runs):
            outcome = await asyncio.to_thread(read_result) or await _without_result(record)
        if outcome is not None:
            await _record(record, outcome)
            return
        await asyncio.sleep(FOLLOW_INTERVAL_SECONDS)


async def _without_result(record: dict[str, str]) -> Outcome:
    """The outcome of a unit that ended without a result, as when the host restarted during the move: by
    where Docker keeps its data now, with what the move may have left."""
    pool = record["pool"]
    ended = f"The unit {UNIT} of the host ended without a result, as when the host restarted during the move"
    journal = f"its journal says more: journalctl -u {UNIT}"
    try:
        where = await asyncio.to_thread(placement)
    except Exception as e:
        return Outcome("failed", "", f"{ended}, and where Docker keeps its data cannot be told: {e}; {journal}")
    if where.pool == pool:
        olds = " and ".join(path for path in (record["docker_old"], record["containerd_old"]) if path)
        return Outcome("moved", "delete", f"{ended}, but Docker's data root is {where.docker_root}; the old copies "
                                          f"may still be on the system disk ({olds}); {journal}")
    pool_dir = os.path.join(get_settings().pools_base_path, pool)
    copies = " and ".join(f"{pool_dir}/{name}" for name in (DOCKER_SUBVOLUME, CONTAINERD_SUBVOLUME))
    return Outcome("failed", "", f"{ended}: Docker's data is not on the pool (its data root is "
                                 f"{where.docker_root}), and the move may have left {copies}, which a move "
                                 f"by hand deletes first; {journal}")


def _alert_of(pool: str, outcome: Outcome) -> tuple[str, str, str]:
    """The alert's kind, title and text."""
    if outcome.result == "moved":
        # A move with a part that did not succeed (a container, an old copy) is a warning.
        return "warning" if outcome.step else "success", f"Docker's data moved to the pool {pool}", f"{outcome.text}."
    step = f" at its step {outcome.step}" if outcome.step else ""
    if outcome.result == "restored":
        return ("error", "Docker's data stays on the system disk",
                f"The move of Docker's data onto the pool {pool} failed{step}: {outcome.text}.")
    return ("error", "The move of Docker's data failed",
            f"The move of Docker's data onto the pool {pool} failed{step}: {outcome.text}.")


async def _record(record: dict[str, str], outcome: Outcome) -> None:
    """Audit and alert the outcome, then forget the move."""
    pool = record["pool"]
    kind, title, text = _alert_of(pool, outcome)
    moved = outcome.result == "moved"
    if kind == "success":
        logger.info("Docker's data moved onto the pool %s: %s", pool, outcome.text)
    elif moved:
        logger.warning("Docker's data moved onto the pool %s: %s", pool, outcome.text)
    else:
        logger.error("%s: %s", title, text)
    await audit_change(
        AuditAction.DOCKER_DATA_MOVED, None, SYSTEM, resource_type="pool", resource_id=pool,
        details=f"Started by {record['by']} at {record['started_at']}: {text}",
        severity=AuditSeverity.INFO if kind == "success" else AuditSeverity.WARNING, success=moved,
    )
    await _alert(kind, title, text)
    _state.move = None
    await asyncio.to_thread(_forget_record)
    await asyncio.to_thread(_forget_run_dir)


async def _alert(kind: str, title: str, text: str) -> None:
    from ..notifications.schemas import NotificationEvent

    event = NotificationEvent.model_validate({"type": kind, "title": title, "message": text,
                                              "timestamp": utc_text(utc_now())})
    health_router = importlib.import_module("..health.router", __package__)
    try:
        await health_router._deliver_alert(event)
    except Exception:
        logger.exception("The alert of the move of Docker's data could not be sent")


# --- the lifespan -------------------------------------------------------------------------------------


async def recover_after_start() -> None:
    """At the start: a move that a supervisor before this one started is followed until the unit's result
    says how it ended, and recorded then (ADR-0487)."""
    try:
        record = await asyncio.to_thread(_load_record)
    except OSError as e:
        logger.error("The record of a move of Docker's data cannot be read: %s", e)
        return
    if record is None:
        return
    _state.move = record
    logger.info("Following the move of Docker's data onto the pool %s in the unit %s", record["pool"], UNIT)
    _follow_in_background(record)


async def stop() -> None:
    """At the stop: the follow ends; the record stays for the next start."""
    task, _state.task = _state.task, None
    if task is not None:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
