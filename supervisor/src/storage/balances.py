"""The balance of a pool (storage.pool-balance, ADR-0379).

A balance rewrites a pool's chunks: by default the data and metadata chunks less than 20% full
(``btrfs balance start -dusage=20 -musage=20``), which frees the space of partly empty chunks for
new ones, or every chunk (``--full-balance``), hours on a hard disk. The kernel runs it in the
process that asked for it, and cancels it, deleting it from the disk, when that process gets a
fatal signal; a restart of the supervisor's container kills every process in it. So ``btrfs balance
start`` and ``resume`` run on the host, in a transient systemd unit of their own,
unos-balance-<pool> (``systemd-run``, which returns once btrfs runs), with their output in
/run/unos-balance-<pool>.log (RAM): what btrfs printed at the end tells how the balance ended, also
after a restart of the supervisor. A shutdown of the host stops the unit, whose ExecStop pauses the
balance before systemd's SIGTERM could cancel it: the kernel resumes it at the next mount. Its
status, its pause and its cancel are btrfs commands in the container (host.run_in_container,
ADR-0290): a pause or a cancel waits for the chunk being moved, which the request waits STOP_WAIT
seconds for; the status then says pausing or cancelling.

One balance per pool, and none beside a scrub of it: a start or a resume is refused while the pool
is not mounted or is read-only, while a scrub of it runs, while a disk of it is being removed
(devices.py, ADR-0410), and while a balance of it runs or (for a start) is paused; pools.scrub_start refuses a scrub while a balance runs. One pause or cancel at a
time: the kernel pauses a balance with both requested (Linux 6.1.150 and later), so a cancel is
refused while a pause waits, and a pause while a cancel waits.

The page follows a balance that runs over the events socket, as it follows a scrub (ADR-0367):
each request of the balance API notes what it found (``note``), and while an administrator's events
socket is open, ``follow`` reads the balances that run again, at most every READ_INTERVAL_SECONDS
and one read at a time. The time left is estimated from the chunks balanced since UNOS first saw
the balance run (``_estimate``): the kernel counts from 0 again at a resume. All of it lives in RAM.
"""

import asyncio
import logging
import math
import re
import subprocess
import threading
import time
from typing import Any

from .. import host
from ..security.validators import sanitize_error_message
from . import devices, disks, pools
from .schemas import BalanceStart, BalanceStatus

logger = logging.getLogger(__name__)

UNIT = "unos-balance-{}"
LOG = "/run/unos-balance-{}.log"
# Seconds a pause or a cancel waits for btrfs, which returns once the kernel stopped after the
# chunk it moves; a request that waited this long answers pausing or cancelling.
STOP_WAIT = 10
# systemd-run returns once btrfs runs in its unit.
SYSTEMD_RUN_TIMEOUT = 60
# How long a stop of the unit, at a shutdown of the host, waits for its pause of the balance: the
# chunk being moved, which can take minutes on a hard disk.
UNIT_STOP_TIMEOUT = "10min"
# What the unit's ExecStop prints when btrfs ended by itself: there is nothing to pause.
NOTHING_TO_PAUSE = "ERROR: balance pause on '{}' failed: Not running"
# The status while a pause or a cancel waits for the chunk being moved, by the request.
WAITING = {"pause": "pausing", "cancel": "cancelling"}
READ_INTERVAL_SECONDS = 2.0
# The longest error of btrfs that a status keeps.
ERROR_LENGTH = 500


def _now() -> float:
    return time.monotonic()


def failure(exc: Exception, pool: str) -> str:
    """Why a balance command of ``pool`` failed, as the balance API answers it (500)."""
    if isinstance(exc, subprocess.TimeoutExpired):
        return (
            f"btrfs {' '.join(exc.cmd[1:3])} did not answer within {exc.timeout:g} seconds: "
            f"pool {pool} may not be responding"
        )
    return sanitize_error_message(exc)


def _mounts(pool: str) -> tuple[str, list]:
    """The mountpoint of ``pool`` and its mounts in the host's mount table, which has its btrfs
    filesystem there; no btrfs command runs otherwise (as pools.scrub_start, ADR-0311)."""
    path = pools.pool_mountpoint(pool)
    here = [m for m in disks.host_mounts() if m.target == path and m.fstype == "btrfs"]
    if not here:
        raise pools.PoolConflictError(f"Pool {pool} is not mounted at {path}")
    return path, here


def _mounted(pool: str, *, write: bool) -> str:
    """The mountpoint of ``pool`` (``_mounts``). With ``write``, also refused while it is mounted
    read-only, on purpose or by btrfs after an error."""
    path, here = _mounts(pool)
    if write and any(m.read_only for m in here):
        message = f"Pool {pool} is mounted read-only: a balance cannot rewrite its chunks"
        if any(m.forced_read_only for m in here):
            message += ". btrfs made it read-only after an error; check the kernel log (dmesg)."
        raise pools.PoolConflictError(message)
    return path


def _ended(pool: str) -> dict[str, Any]:
    """How the last balance that UNOS ran in the pool's unit ended, from its output: {} when the
    output is gone (a reboot), empty (btrfs was killed) or says it was paused (since then cancelled,
    or resumed by the kernel when the pool was mounted again). btrfs prints a line for each request
    that stopped it, and a pause wins over a cancel requested meanwhile; the unit's ExecStop, which
    systemd runs also after btrfs ended by itself, adds that it found nothing to pause."""
    try:
        text = host.read_host_file(LOG.format(pool))
    except host.HostCommandError:
        return {}  # none since the host started
    except (OSError, subprocess.SubprocessError, UnicodeDecodeError) as exc:
        logger.warning("Could not read how the last balance of %s ended: %s", pool, exc)
        return {}
    done = re.search(r"Done, had to relocate (\d+) out of (\d+) chunks", text)
    if done:
        return {"result": "finished", "completed": int(done.group(1)), "considered": int(done.group(2))}
    if "balance paused by user" in text:
        return {}
    if "balance canceled by user" in text:
        return {"result": "cancelled"}
    stop = NOTHING_TO_PAUSE.format(pools.pool_mountpoint(pool))
    errors = [line.strip() for line in text.splitlines() if line.startswith("ERROR:") and line.strip() != stop]
    if errors:
        return {"result": "failed", "error": "; ".join(errors)[:ERROR_LENGTH]}
    return {}


# Of each pool whose balance runs: when UNOS first saw it run, and the chunks balanced then.
_since: dict[str, tuple[float, int]] = {}
_since_lock = threading.Lock()


def _estimate(found: BalanceStatus) -> BalanceStatus:
    """``found`` with the seconds left, at the rate of the chunks balanced since the balance was
    first seen running; the counts starting again (a resume) start the estimate again."""
    with _since_lock:
        if found.status != "running" or found.completed is None or found.expected is None:
            _since.pop(found.pool, None)
            return found
        now = _now()
        since = _since.get(found.pool)
        if since is None or found.completed < since[1]:
            _since[found.pool] = (now, found.completed)
            return found
        balanced = found.completed - since[1]
        if balanced <= 0 or now <= since[0]:
            return found
        left = max(found.expected - found.completed, 0)
        return found.model_copy(update={"seconds_left": math.ceil(left * (now - since[0]) / balanced)})


def _found(pool: str, progress: pools.BalanceProgress) -> BalanceStatus:
    if progress.status == "idle":
        return _estimate(BalanceStatus(pool=pool, status="idle", **_ended(pool)))
    return _estimate(BalanceStatus(
        pool=pool, status=progress.status, completed=progress.completed, expected=progress.expected,
        considered=progress.considered,
    ))


def _status(pool: str, path: str) -> BalanceStatus:
    return _found(pool, pools.balance_progress(path))


def status(pool: str) -> BalanceStatus:
    """The pool's balance as btrfs reports it, and how the last one ended when none runs."""
    return _status(pool, _mounted(pool, write=False))


def _run_in_unit(pool: str, path: str, btrfs: list[str], action: str) -> BalanceStatus:
    """Run ``btrfs`` (a balance's start or resume) in the pool's unit on the host, after the
    checks; the status once btrfs runs. PoolConflictError (409) for a refusal, RuntimeError (500)
    when systemd or btrfs refuses."""
    with pools.maintenance_lock:
        if pools.get_scrub_status(pool).status == "running":
            raise pools.PoolConflictError(
                f"A scrub of pool {pool} runs: wait for its end or cancel it, then {action} the balance"
            )
        if devices.removal_runs(path):
            # The kernel refuses a balance beside a removal, another exclusive operation (ADR-0410).
            raise pools.PoolConflictError(
                f"A disk of pool {pool} is being removed: wait for the end of its removal, then {action} the balance"
            )
        progress = pools.balance_progress(path).status
        if action == "start" and progress in pools.BALANCE_RUNS:
            raise pools.PoolConflictError(f"A balance of pool {pool} runs already")
        if action == "start" and progress == "paused":
            raise pools.PoolConflictError(f"A balance of pool {pool} is paused: resume or cancel it")
        if action == "resume" and progress != "paused":
            raise pools.PoolConflictError(f"No balance of pool {pool} is paused")
        log = LOG.format(pool)
        cmd = [
            "systemd-run",
            f"--unit={UNIT.format(pool)}",
            f"--description=UNOS: balance the pool {pool}",
            # Unloaded once btrfs ended, also after a failure: the next balance takes the name.
            "--collect",
            "--quiet",
            # systemd-run returns once btrfs runs, after ExecStartPre emptied the output file.
            "--service-type=exec",
            # A shutdown of the host stops the unit before it unmounts the pool. Its SIGTERM would
            # be fatal to btrfs, and the kernel cancels the balance of a process that gets a fatal
            # signal and deletes it from the disk: ExecStop pauses it first, and the kernel resumes
            # it when the pool is mounted again, as after a power cut. systemd runs ExecStop also
            # after btrfs ended by itself, when it finds nothing to pause ("-": not a failure).
            f"--property=RequiresMountsFor={path}",
            # Every command of the unit opens the output file, ExecStop too: btrfs appends to it,
            # emptied before it starts.
            f"--property=ExecStartPre=truncate --size=0 {log}",
            f"--property=StandardOutput=append:{log}",
            "--property=StandardError=inherit",
            # The mountpoint is the pools' base and a pool name (letters, digits, - and _): systemd
            # splits the line at its spaces.
            f"--property=ExecStop=-btrfs balance pause {path}",
            f"--property=TimeoutStopSec={UNIT_STOP_TIMEOUT}",
            "--",
            *btrfs,
        ]
        try:
            result = host.run_on_host(cmd, timeout=SYSTEMD_RUN_TIMEOUT)
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"systemd-run did not answer within {SYSTEMD_RUN_TIMEOUT} seconds: the balance of pool {pool} "
                "may not have started"
            ) from None
        if not result.ok:
            raise RuntimeError(
                f"Balance {action} failed: {result.stderr.strip() or f'exit status {result.returncode}'}"
            )
    try:
        found = _status(pool, path)
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        # btrfs runs in the unit: its progress is read at the next request.
        logger.warning("Balance %s of %s, but could not read its status: %s", action, pool, exc)
        return BalanceStatus(pool=pool, status="running")
    if found.status == "idle" and found.result == "failed":
        raise RuntimeError(f"Balance {action} failed: {found.error}")
    if found.status == "idle" and found.result is None:
        # btrfs runs and has not reached the kernel yet: it ends with a line in the output.
        return BalanceStatus(pool=pool, status="running")
    return found


def start(pool: str, request: BalanceStart) -> BalanceStatus:
    """Start a balance of a mounted, writable pool (the module's docstring)."""
    path = _mounted(pool, write=True)
    filters = ["--full-balance"] if request.full else [f"-dusage={request.limit}", f"-musage={request.limit}"]
    return _run_in_unit(pool, path, ["btrfs", "balance", "start", *filters, path], "start")


def resume(pool: str) -> BalanceStatus:
    """Resume the pool's paused balance in its unit."""
    path = _mounted(pool, write=True)
    return _run_in_unit(pool, path, ["btrfs", "balance", "resume", path], "resume")


# Held while a pause or a cancel reads the status and asks btrfs: the one does not start between the
# other's read and its request.
_stop_lock = threading.Lock()


def _stop(pool: str, action: str) -> tuple[BalanceStatus, bool]:
    path, here = _mounts(pool)
    with _stop_lock:
        progress = pools.balance_progress(path)
        if progress.status == WAITING[action]:
            # Asked already: the kernel acts after the chunk being moved.
            return _found(pool, progress), False
        if action == "cancel" and progress.status == "pausing":
            raise pools.PoolConflictError(
                f"A pause of the balance of pool {pool} waits for the chunk being moved: cancel the balance once "
                "it is paused"
            )
        if action == "pause" and progress.status == "cancelling":
            raise pools.PoolConflictError(f"A cancel of the balance of pool {pool} waits for the chunk being moved")
        if action == "cancel" and progress.status == "paused" and any(m.read_only for m in here):
            # The kernel keeps it for the next read-write mount, which resumes it (EROFS).
            raise pools.PoolConflictError(
                f"Pool {pool} is mounted read-only: its paused balance can be cancelled once it is mounted read-write"
            )
        try:
            result = host.run_in_container(["btrfs", "balance", action, path], timeout=STOP_WAIT)
        except subprocess.TimeoutExpired:
            acted = True  # the kernel has the request and stops after the chunk being moved
        else:
            if result.returncode == 0:
                acted = True
            elif result.returncode == 2 and re.search(r"failed: Not (running|in progress)$", result.stderr or "", re.M):
                # No balance to stop (ENOTCONN, btrfs-progs cmds/balance.c): a pause says "Not running",
                # also of a paused one, a cancel "Not in progress". The answer is the status.
                acted = False
            else:
                raise RuntimeError(
                    f"Balance {action} failed: {(result.stderr or '').strip() or f'exit status {result.returncode}'}"
                )
        found = _status(pool, path)
    if action == "cancel" and acted and found.status == "paused":
        # A pause from elsewhere (by hand on the host) won, and the cancel returned 0 all the same.
        acted = False
    if action == "cancel" and acted and found.status == "idle":
        # A paused balance has no unit whose output could say so.
        found = BalanceStatus(pool=pool, status="idle", result="cancelled")
    return found, acted


def pause(pool: str) -> tuple[BalanceStatus, bool]:
    """Pause the pool's running balance: its status then, and whether btrfs paused one.
    PoolConflictError while a cancel waits for the chunk being moved: the pause would win."""
    return _stop(pool, "pause")


def cancel(pool: str) -> tuple[BalanceStatus, bool]:
    """Cancel the pool's running or paused balance: its status then, and whether btrfs cancelled one.
    PoolConflictError while a pause waits for the chunk being moved, which the kernel would make all
    the same, and for a paused balance of a read-only pool."""
    return _stop(pool, "cancel")


# --- the events socket ----------------------------------------------------------------------

# Each pool's last status, or why it could not be read: the pools whose balance was seen running.
_balances: dict[str, BalanceStatus | str] = {}
# A number that changes whenever _balances does.
_version = 0
# How many notes were made: a read keeps its answer only when no request noted the pool meanwhile.
_notes = 0
_noted_at: dict[str, int] = {}
_reading: "asyncio.Task[None] | None" = None
_read_at = float("-inf")


def _runs(found: BalanceStatus | str) -> bool:
    return isinstance(found, BalanceStatus) and found.status in pools.BALANCE_RUNS


def note(pool: str, found: BalanceStatus | str) -> None:
    """Keep what was found of ``pool``'s balance: its status, or why it could not be read. A pool
    joins only when its balance runs."""
    global _version, _notes
    if pool not in _balances and not _runs(found):
        return
    _notes += 1
    _noted_at[pool] = _notes
    if _balances.get(pool) != found:
        _balances[pool] = found
        _version += 1


def version() -> int:
    """0 before the first balance was seen running; then a number that changes with ``current()``."""
    return _version


def current() -> list[dict[str, Any]]:
    """What the events socket sends, by pool: each status as the balance API answers it, or
    ``{"pool": ..., "error": ...}`` when it could not be read."""
    return [
        found.model_dump() if isinstance(found, BalanceStatus) else {"pool": pool, "error": found}
        for pool, found in sorted(_balances.items())
    ]


def follow() -> None:
    """Start a read of the balances that run, unless one runs or the last started less than
    READ_INTERVAL_SECONDS ago; it does not wait for it."""
    global _reading, _read_at
    running = [pool for pool, found in _balances.items() if _runs(found)]
    if not running or (_reading is not None and not _reading.done()):
        return
    if time.monotonic() - _read_at < READ_INTERVAL_SECONDS:
        return
    _read_at = time.monotonic()
    _reading = asyncio.get_running_loop().create_task(_read(running), name="unos-balance-status")


async def _read(running: list[str]) -> None:
    for pool in running:
        noted = _noted_at.get(pool)
        try:
            found: BalanceStatus | str = await asyncio.to_thread(status, pool)
        except Exception as exc:
            found = failure(exc, pool)
        # A request answered meanwhile: its answer is newer than this read.
        if _noted_at.get(pool) == noted:
            note(pool, found)
