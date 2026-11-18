"""The disks of a pool: adding one and removing one (storage.pool-add-and-remove-disks, ADR-0411).

A free disk joins a mounted pool with ``btrfs device add`` on the host, which takes seconds: the
pool grows at once, its chunks keep their profile, and new chunks take the pool's. A disk leaves it
with ``btrfs device remove``, which first moves every chunk of the disk to the other disks, hours on
a hard disk, in the process that asked for it: so it runs on the host in a transient systemd unit
of its own, unos-disk-remove-<pool> (``systemd-run``, which returns once btrfs runs), with its
output in /run/unos-disk-remove-<pool>.log (RAM), whose first line names the disk and the bytes on
it. A restart of the supervisor leaves it running; a shutdown of the host stops the unit before it
unmounts the pool, and the SIGTERM cancels the relocation: the disk stays in the pool.

The kernel sets the size of the disk being removed to 0 for the whole removal and gives its size
back when the removal fails or is cancelled (btrfs_shrink_device): a disk of the pool with the size
0 in ``btrfs filesystem show`` is the removal that runs, and the bytes still allocated on it are its
progress. ``btrfs filesystem show`` opens each disk of the pool, so it runs on the host, under the
host's timeout (ADR-0258): the container's /dev has only the disks that were there when it started
(ADR-0360). ``btrfs filesystem df`` asks the mounted filesystem only and runs in the container
(host.run_in_container, ADR-0290).

Both are refused while the pool is not mounted, is read-only or misses a disk, while a scrub of it
runs, while a balance of it runs or is paused, and while a removal of it runs; a disk that holds
the system, another pool or a mount is never offered nor added (disks.addable_problem), and a
removal that the profile or the space cannot survive is refused (removal_problem). The page follows
a removal that runs over the events socket, as it follows a balance (balances.py, ADR-0379):
``note``, ``current``, ``follow``, in RAM.
"""

import asyncio
import logging
import re
import subprocess
import time
from dataclasses import dataclass
from typing import Any

from .. import host
from ..security.validators import sanitize_error_message, validate_device_path
from . import disks, pools
from .schemas import (
    DiskAdd,
    DiskRemovalStatus,
    DiskRemove,
    PoolDevices,
    PoolMember,
)

logger = logging.getLogger(__name__)

UNIT = "unos-disk-remove-{}"
LOG = "/run/unos-disk-remove-{}.log"
# btrfs filesystem show and df of a mounted pool answer at once unless the filesystem hangs.
READ_TIMEOUT = 60
# btrfs device add discards (TRIMs) an SSD first, as mkfs.btrfs does.
ADD_TIMEOUT = pools.MKFS_TIMEOUT
# systemd-run returns once btrfs runs in its unit.
SYSTEMD_RUN_TIMEOUT = 60
# How long a stop of the unit, at a shutdown of the host, waits for btrfs to end after the SIGTERM
# that cancels the relocation.
UNIT_STOP_TIMEOUT = "10min"
GIB = 1024**3
# The room that the disks left keep for the chunk being moved: the kernel writes a chunk's new copy
# before it frees the old one.
RESERVE = GIB
# Of each profile that UNOS removes a disk from: the fewest disks a pool with it keeps
# (pools.MIN_DEVICES for those that UNOS creates; dup keeps both copies on one disk), and on how
# many disks the first chunk that a removal moves needs unallocated room: the most that a kernel
# asks for (raid0 2 and raid10 4 before Linux 5.15, 1 and 2 since).
PROFILES = {
    "single": (1, 1),
    "dup": (1, 1),
    "raid0": (2, 2),
    "raid1": (2, 2),
    "raid10": (4, 4),
    "raid1c3": (3, 3),
    "raid1c4": (4, 4),
}
# Over how many disks, in equal shares, a chunk of each profile is spread, by the number of disks:
# raid0 and raid10 over every disk with room (raid10 an even number of them), each share of the
# others on a disk of its own.
WIDTHS = {
    "single": lambda n: 1,
    "dup": lambda n: 1,
    "raid1": lambda n: 2,
    "raid1c3": lambda n: 3,
    "raid1c4": lambda n: 4,
    "raid0": lambda n: n,
    "raid10": lambda n: n - n % 2,
}
# The longest error of btrfs that a status keeps.
ERROR_LENGTH = 500
READ_INTERVAL_SECONDS = 2.0

_TOTAL = re.compile(r"^Total devices\s+(\d+)\s")
_DEVID = re.compile(r"^devid\s+(\d+)\s+size\s+(\d+)\s+used\s+(\d+)\s+path\s+(.+?)\s*$")
_PROFILE = re.compile(r"^(?:Data|Metadata|System|Data\+Metadata),\s*([A-Za-z0-9]+):\s*total=", re.MULTILINE)
# The first line of the unit's output (its ExecStartPre).
_START = re.compile(r"^UNOS removes devid (\d+) (\S+) with (\d+) bytes allocated$", re.MULTILINE)


class DiskAddFailedError(RuntimeError):
    """btrfs device add ran and failed: it may have written the disk (HTTP 500, audited as failed)."""


@dataclass(frozen=True)
class Member:
    """A disk of a mounted pool, as btrfs filesystem show reports it."""

    devid: int
    path: str
    size: int  # 0 while the kernel removes it
    used: int  # the bytes of the chunks allocated on it


def failure(exc: Exception, pool: str) -> str:
    """Why a command of a disk removal of ``pool`` failed, as the API answers it (500)."""
    if isinstance(exc, subprocess.TimeoutExpired):
        return (
            f"btrfs {' '.join(exc.cmd[1:3])} did not answer within {exc.timeout:g} seconds: "
            f"pool {pool} may not be responding"
        )
    return sanitize_error_message(exc)


def _mounted(pool: str, *, write: bool) -> str:
    """The mountpoint of ``pool``, where the host's mount table has its btrfs filesystem; no btrfs
    command runs otherwise. With ``write``, also refused while it is mounted read-only."""
    path = pools.pool_mountpoint(pool)
    here = [m for m in disks.host_mounts() if m.target == path and m.fstype == "btrfs"]
    if not here:
        raise pools.PoolConflictError(f"Pool {pool} is not mounted at {path}")
    if write and any(m.read_only for m in here):
        message = f"Pool {pool} is mounted read-only: no disk can be added or removed"
        if any(m.forced_read_only for m in here):
            message += ". btrfs made it read-only after an error; check the kernel log (dmesg)."
        raise pools.PoolConflictError(message)
    return path


def members(path: str, timeout: float = READ_TIMEOUT) -> tuple[list[Member], int]:
    """The disks of the btrfs filesystem mounted at ``path`` that are present, and how many of its
    disks are missing, as the host's btrfs reports them. RuntimeError when btrfs cannot report them,
    subprocess.TimeoutExpired when it does not answer."""
    btrfs = ["btrfs", "filesystem", "show", "--raw", path]
    try:
        result = host.run_on_host(["timeout", "--signal=KILL", str(timeout), *btrfs], timeout=timeout + 5)
    except subprocess.TimeoutExpired:
        raise subprocess.TimeoutExpired(btrfs, timeout) from None
    # Ended by timeout(1): its SIGKILL ends nsenter too (-9, or 137 through a shell).
    if result.returncode in (124, 137, -9):
        raise subprocess.TimeoutExpired(btrfs, timeout)
    if result.returncode != 0:
        raise RuntimeError(
            f"btrfs filesystem show failed: {(result.stderr or '').strip() or f'exit status {result.returncode}'}"
        )
    found: list[Member] = []
    total, some_missing = 0, False
    for raw in (result.stdout or "").splitlines():
        line = raw.strip()
        if (counted := _TOTAL.match(line)) is not None:
            total = int(counted[1])
        elif "Some devices missing" in line:
            some_missing = True
        elif (device := _DEVID.match(line)) is not None:
            if "MISSING" in device[4].split() or device[4].startswith("<missing"):
                continue
            found.append(Member(int(device[1]), device[4], int(device[2]), int(device[3])))
    if not found:
        raise RuntimeError(f"btrfs filesystem show named no disk of the pool at {path}")
    return found, max(total - len(found), 1 if some_missing else 0, 0)


def profiles(path: str, timeout: float = READ_TIMEOUT) -> list[str]:
    """The profiles of the data, metadata and system chunks of the btrfs filesystem mounted at
    ``path`` (btrfs filesystem df), lower case: two of a kind while one is converted."""
    result = host.run_in_container(["btrfs", "filesystem", "df", "-b", path], timeout=timeout)
    found = sorted({profile.lower() for profile in _PROFILE.findall(result.stdout or "")})
    if result.returncode != 0 or not found:
        raise RuntimeError(
            f"btrfs filesystem df failed: {(result.stderr or '').strip() or f'exit status {result.returncode}'}"
        )
    return found


def removal_runs(path: str, timeout: float = READ_TIMEOUT) -> bool:
    """Whether a disk of the pool mounted at ``path`` is being removed: its size is 0. False when
    btrfs cannot tell (the kernel refuses another exclusive operation all the same)."""
    try:
        found, _ = members(path, timeout)
    except RuntimeError as exc:
        logger.warning("Could not read the disks of %s: %s", path, exc)
        return False
    return any(member.size == 0 for member in found)


def _size(value: int) -> str:
    return f"{value / GIB:.1f} GiB"


def raw_capacity(width: int, sizes: list[int]) -> float:
    """The bytes that chunks spread in equal shares over ``width`` disks of their own can take of
    disks of ``sizes``: each chunk puts at most one share on a disk, so for each j below ``width``
    the disks without the j largest take the other width - j shares (the bound that btrfs's
    allocator, which gives each chunk the disks with the most room, reaches)."""
    ordered = sorted(sizes, reverse=True)
    return width * min((sum(ordered) - sum(ordered[:j])) / (width - j) for j in range(width))


def removal_problem(found: list[Member], devid: int, in_use: list[str]) -> str | None:
    """Why the disk ``devid`` must not leave the pool of the disks ``found`` with the profiles
    ``in_use``, or None when it may (ADR-0411): the only disk, a profile UNOS does not know, fewer
    disks left than a profile needs, or too little space on the disks left.

    The space is an estimate on the safe side. In the end, the bytes allocated on all the disks,
    also to partly empty chunks, plus RESERVE on each disk left for the chunk being moved, must fit
    on the disks left for each profile in use (raw_capacity with its WIDTHS). And at the start, the
    first chunk moved needs RESERVE unallocated on as many disks left as its profile asks for
    (PROFILES), since the kernel writes a chunk's new copy before it frees the old one. A removal can
    still run out of space on its way in an uneven layout: btrfs then keeps the disk in the pool."""
    leaving = next(member for member in found if member.devid == devid)
    rest = [member for member in found if member.devid != devid]
    if not rest:
        return f"{leaving.path} is the only disk of the pool"
    unknown = [profile for profile in in_use if profile not in PROFILES]
    if unknown:
        return f"UNOS removes no disk from a pool with the {', '.join(unknown)} profile"
    need, profile = max((PROFILES[profile][0], profile) for profile in in_use)
    if len(rest) < need:
        return (
            f"the pool's {profile} profile needs at least {need} disks, and without {leaving.path} it would "
            f"keep {len(rest)}"
        )
    sizes = [member.size for member in rest]
    room = min(raw_capacity(WIDTHS[profile](len(rest)), sizes) for profile in in_use)
    needed = sum(member.used for member in found) + RESERVE * len(rest)
    if needed > room:
        return (
            f"the other disks cannot hold the pool's chunks: they need {_size(needed)} of disk space with room for "
            f"the chunk being moved, and the disks left give {_size(int(room))} for its profile. A balance frees "
            "the space of partly empty chunks; or add a disk first"
        )
    first = max(PROFILES[profile][1] for profile in in_use)
    if sum(member.size - member.used >= RESERVE for member in rest) < first:
        return (
            f"the first chunk that the removal moves needs {_size(RESERVE)} unallocated on {first} of the other "
            "disks, and fewer have it. A full balance spreads the chunks over the disks; or add a disk first"
        )
    return None


def _refuse_beside_jobs(pool: str, path: str, found: list[Member], missing: int, action: str) -> None:
    """PoolConflictError (409) while the pool misses a disk, a disk of it is being removed, a scrub
    of it runs, or a balance of it runs or is paused: one job per pool."""
    if missing:
        raise pools.PoolConflictError(
            f"Pool {pool} misses {missing} disk(s): UNOS adds or removes a disk only while the pool has all its disks"
        )
    if any(member.size == 0 for member in found):
        raise pools.PoolConflictError(f"A disk of pool {pool} is being removed: wait for the end of its removal")
    if pools.get_scrub_status(pool).status == "running":
        raise pools.PoolConflictError(f"A scrub of pool {pool} runs: wait for its end or cancel it, then {action}")
    balance = pools.balance_progress(path).status
    if balance == "paused":
        raise pools.PoolConflictError(
            f"A balance of pool {pool} is paused: resume it until it ends, or cancel it, then {action}"
        )
    if balance != "idle":
        raise pools.PoolConflictError(f"A balance of pool {pool} runs: wait for its end or cancel it, then {action}")


def describe(pool: str) -> PoolDevices:
    """The disks of a mounted pool, each with why it cannot be removed, its profiles, the disks that
    can join it and its removal."""
    path = _mounted(pool, write=False)
    found, _ = members(path)
    in_use = profiles(path)
    named = disks.disk_models([member.path for member in found])
    listed = [
        PoolMember(
            devid=member.devid, path=member.path, size=member.size, used=member.used,
            model=named.get(member.path, (None, None))[0], serial=named.get(member.path, (None, None))[1],
            problem=removal_problem(found, member.devid, in_use),
        )
        for member in found
    ]
    return PoolDevices(
        pool=pool, profiles=in_use, members=listed, candidates=disks.addable_disks(), removal=_status(pool, found),
    )


def add(pool: str, request: DiskAdd) -> str:
    """Add a free disk to a mounted pool; what the audit log says of it. The checks run under the
    pools' locks right before btrfs device add (the module's docstring)."""
    device = validate_device_path(request.device)
    if request.confirm_device != device:
        raise ValueError("confirm_device must be the disk's path, typed again")
    if request.identity.path != device:
        raise ValueError("identity names another disk than device")
    with pools._lock, pools.maintenance_lock:
        path = _mounted(pool, write=True)
        found, missing = members(path)
        _refuse_beside_jobs(pool, path, found, missing, "add the disk")
        if any(member.path == device for member in found):
            raise pools.PoolConflictError(f"{device} is a disk of the pool {pool} already")
        fact = disks.get_device_facts([device])[device]
        if fact.type != "disk":
            raise ValueError(f"{device} is no whole disk: UNOS adds whole disks to a pool")
        pools._check_identity(device, fact, request.identity)
        problem = disks.addable_problem(fact)
        if problem:
            raise pools.DeviceSafetyError(f"Refusing to add {device}: {problem}")
        if fact.has_data and not request.force:
            raise pools.DeviceSafetyError(
                f"{device} contains data ({fact.data_description}). Set force to erase it and add it anyway."
            )
        if fact.has_data and request.contents != fact.data_description:
            # A sleeping disk was listed with unknown contents, which this read woke and found.
            raise pools.DeviceSafetyError(
                f"{device} holds {fact.data_description}, not what the list showed "
                f"({request.contents or 'nothing'}). Open the dialog again to see what an add would erase."
            )
        trimmed = pools.pool_is_trimmed([member.path for member in found])
        if trimmed and not pools.pool_is_trimmed([device]):
            raise pools.DeviceSafetyError(
                f"Refusing to add {device}: this board hangs on the TRIM of an NVMe drive, and the pool {pool} "
                "is mounted with TRIM. Add it to a pool that has an NVMe drive already, or make a new pool of it"
            )
        in_use = profiles(path)
        cmd = ["btrfs", "device", "add"]
        if request.force and fact.has_data:
            # Without -f btrfs-progs refuses a disk with signatures: the last line of defence.
            cmd.append("-f")
        if not trimmed:
            cmd.append("--nodiscard")
        try:
            result = host.run_on_host([*cmd, device, path], timeout=ADD_TIMEOUT)
        except subprocess.TimeoutExpired:
            raise DiskAddFailedError(
                f"btrfs device add did not answer within {ADD_TIMEOUT} seconds: it may still run on the host"
            ) from None
        if not result.ok:
            raise DiskAddFailedError(
                f"btrfs device add failed: {result.stderr.strip() or f'exit status {result.returncode}'}"
            )
        logger.info("Added %s to the pool %s", device, pool)
    details = (
        f"Added {device} (serial {fact.serial or 'none'}, {fact.size} bytes) to the pool {pool}; its profiles stay "
        f"{', '.join(in_use)}"
    )
    if request.force and fact.has_data:
        details += f"; erased what it held ({fact.data_description})"
    return details


def remove(pool: str, request: DiskRemove) -> DiskRemovalStatus:
    """Start the removal of a disk of a mounted pool in its unit on the host, after the checks; the
    status once btrfs runs. PoolConflictError (409) for a refusal, RuntimeError (500) when systemd
    or btrfs refuses it."""
    device = validate_device_path(request.device)
    if request.confirm_device != device:
        raise ValueError("confirm_device must be the disk's path, typed again")
    with pools._lock, pools.maintenance_lock:
        path = _mounted(pool, write=True)
        found, missing = members(path)
        _refuse_beside_jobs(pool, path, found, missing, "remove the disk")
        leaving = next((member for member in found if member.devid == request.devid), None)
        if leaving is None or leaving.path != device:
            raise pools.PoolConflictError(
                f"{device} is no longer devid {request.devid} of the pool {pool}: disks can change names when "
                "they are reconnected. Reload the list and select the disk again."
            )
        problem = removal_problem(found, leaving.devid, profiles(path))
        if problem:
            raise pools.PoolConflictError(f"Refusing to remove {device}: {problem}")
        log = LOG.format(pool)
        cmd = [
            "systemd-run",
            f"--unit={UNIT.format(pool)}",
            f"--description=UNOS: remove a disk from the pool {pool}",
            # Unloaded once btrfs ended, also after a failure: the next removal takes the name.
            "--collect",
            "--quiet",
            # systemd-run returns once btrfs runs, after ExecStartPre wrote the output's first line.
            "--service-type=exec",
            # A shutdown of the host stops the unit before it unmounts the pool: its SIGTERM is fatal to
            # btrfs, and the kernel cancels the relocation, which leaves the disk in the pool.
            f"--property=RequiresMountsFor={path}",
            f"--property=ExecStartPre=truncate --size=0 {log}",
            # The disk and the bytes on it, for the progress and the result, also after a restart of
            # the supervisor. The path is validated (/dev/ and a disk's name): systemd splits the line
            # at its spaces.
            f"--property=ExecStartPre=echo UNOS removes devid {leaving.devid} {device} with {leaving.used} "
            "bytes allocated",
            f"--property=StandardOutput=append:{log}",
            "--property=StandardError=inherit",
            f"--property=TimeoutStopSec={UNIT_STOP_TIMEOUT}",
            "--",
            "btrfs", "device", "remove", str(leaving.devid), path,
        ]
        try:
            result = host.run_on_host(cmd, timeout=SYSTEMD_RUN_TIMEOUT)
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"systemd-run did not answer within {SYSTEMD_RUN_TIMEOUT} seconds: the removal of {device} from the "
                f"pool {pool} may not have started"
            ) from None
        if not result.ok:
            raise RuntimeError(
                f"Disk removal failed: {result.stderr.strip() or f'exit status {result.returncode}'}"
            )
        logger.info("Started removing %s (devid %d) from the pool %s", device, leaving.devid, pool)
    starting = DiskRemovalStatus(
        pool=pool, status="running", devid=leaving.devid, device=device, left=leaving.used, total=leaving.used,
    )
    try:
        found_now = status(pool)
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        # btrfs runs in the unit: its progress is read at the next request.
        logger.warning("Started removing %s from %s, but could not read its status: %s", device, pool, exc)
        return starting
    if found_now.status == "idle" and found_now.result == "failed":
        raise RuntimeError(f"Disk removal failed: {found_now.error}")
    if found_now.status == "idle" and found_now.result is None:
        # btrfs runs and has not reached the kernel yet.
        return starting
    return found_now


def _log(pool: str) -> str:
    try:
        return host.read_host_file(LOG.format(pool))
    except host.HostCommandError:
        return ""  # none since the host started
    except (OSError, subprocess.SubprocessError, UnicodeDecodeError) as exc:
        logger.warning("Could not read how the last disk removal of %s ended: %s", pool, exc)
        return ""


def _status(pool: str, found: list[Member]) -> DiskRemovalStatus:
    """The removal of the pool whose disks are ``found``: the disk with the size 0, or how the last
    one that UNOS started in the unit ended, from its output."""
    start = _START.search(_log(pool))
    leaving = next((member for member in found if member.size == 0), None)
    if leaving is not None:
        total = int(start[3]) if start is not None and int(start[1]) == leaving.devid else None
        return DiskRemovalStatus(
            pool=pool, status="running", devid=leaving.devid, device=leaving.path, left=leaving.used, total=total,
        )
    idle = DiskRemovalStatus(pool=pool, status="idle")
    if start is None:
        return idle
    devid, device = int(start[1]), start[2]
    errors = [line.strip() for line in start.string.splitlines() if line.startswith("ERROR:")]
    if errors:
        return idle.model_copy(update={
            "devid": devid, "device": device, "result": "failed", "error": "; ".join(errors)[:ERROR_LENGTH],
        })
    if all(member.devid != devid for member in found):
        return idle.model_copy(update={"devid": devid, "device": device, "result": "finished"})
    return idle


def status(pool: str) -> DiskRemovalStatus:
    """The pool's disk removal: the one that runs, with the bytes still on its disk, or how the last
    one ended."""
    found, _ = members(_mounted(pool, write=False))
    return _status(pool, found)


# --- the events socket ----------------------------------------------------------------------

# Each pool's last status, or why it could not be read: the pools whose removal was seen running.
_removals: dict[str, DiskRemovalStatus | str] = {}
# A number that changes whenever _removals does.
_version = 0
# How many notes were made: a read keeps its answer only when no request noted the pool meanwhile.
_notes = 0
_noted_at: dict[str, int] = {}
_reading: "asyncio.Task[None] | None" = None
_read_at = float("-inf")


def _runs(found: DiskRemovalStatus | str) -> bool:
    return isinstance(found, DiskRemovalStatus) and found.status == "running"


def note(pool: str, found: DiskRemovalStatus | str) -> None:
    """Keep what was found of ``pool``'s removal: its status, or why it could not be read. A pool
    joins only when its removal runs."""
    global _version, _notes
    if pool not in _removals and not _runs(found):
        return
    _notes += 1
    _noted_at[pool] = _notes
    if _removals.get(pool) != found:
        _removals[pool] = found
        _version += 1


def version() -> int:
    """0 before the first removal was seen running; then a number that changes with ``current()``."""
    return _version


def current() -> list[dict[str, Any]]:
    """What the events socket sends, by pool: each status as the API answers it, or
    ``{"pool": ..., "error": ...}`` when it could not be read."""
    return [
        found.model_dump() if isinstance(found, DiskRemovalStatus) else {"pool": pool, "error": found}
        for pool, found in sorted(_removals.items())
    ]


def follow() -> None:
    """Start a read of the removals that run, unless one runs or the last started less than
    READ_INTERVAL_SECONDS ago; it does not wait for it."""
    global _reading, _read_at
    running = [pool for pool, found in _removals.items() if _runs(found)]
    if not running or (_reading is not None and not _reading.done()):
        return
    if time.monotonic() - _read_at < READ_INTERVAL_SECONDS:
        return
    _read_at = time.monotonic()
    _reading = asyncio.get_running_loop().create_task(_read(running), name="unos-disk-removal-status")


async def _read(running: list[str]) -> None:
    for pool in running:
        noted = _noted_at.get(pool)
        try:
            found: DiskRemovalStatus | str = await asyncio.to_thread(status, pool)
        except Exception as exc:
            found = failure(exc, pool)
        # A request answered meanwhile: its answer is newer than this read.
        if _noted_at.get(pool) == noted:
            note(pool, found)
