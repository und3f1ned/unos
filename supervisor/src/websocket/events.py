"""Real-time WebSocket endpoints.

Authentication handshake, used by both sockets. Tokens never travel in the
URL, because request URLs end up in the uvicorn and proxy access logs:

1. The client connects without query parameters.
2. Within 10 seconds it sends one text message:
   ``{"type": "auth", "token": "<JWT or unos_ API token>"}``.
3. The token is checked exactly like an HTTP bearer token (signature, expiry,
   API token state, user still exists). The server answers
   ``{"type": "auth_ok"}`` and the regular message stream follows.

The server closes the socket with 4401 ("unauthorized") when the first
message is missing, late, malformed or carries an invalid token, and with
4403 ("forbidden") when the user may not use the socket (container logs
require an administrator). A ``?token=`` query parameter is refused (4401).

While a socket is open, its token is checked again every
``WS_REAUTH_INTERVAL_SECONDS``. A token that stopped being valid (expired
JWT, disabled or expired API token, deleted user) closes the socket with
4401; a logs socket whose user is no longer an administrator is closed with
4403. If the check itself fails (database error), the socket is closed with
1011 and the client reconnects through the handshake. A check in progress
when the socket closes runs to its end, and its answer is dropped.

A logs socket is closed with 1000 once the container's log ended (the
container stopped) or could not be read, after a ``{"type": "error"}`` message
with the reason.

The journal socket ``/ws/v1/journal`` (administrators only) is System > Logs'
Auto (ADR-0366). Its URL takes the values of ``GET /api/v1/logs/entries``
(source, limit, priority, search, since). After auth_ok it sends
``{"type": "entries", "data": [...]}``, the newest entries as that request
answers them, newest first, and then ``{"type": "entry", "data": {...}}`` for
each new entry of the source, level and search, as the journal gets it. A value
it does not take, a journal that cannot be read and a journalctl that ends send
``{"type": "error", "message": ...}`` and close the socket with 1000.

The events socket sends administrators ``{"type": "problems", "data": [...]}``,
the problems of the NAS that are still there (``health.problems``), right after
auth_ok and whenever the list changes; regular users never get it, and a
socket whose user is no longer an administrator at a re-check gets an empty
list once and no more (ADR-0332). It sends administrators
``{"type": "scrubs", "data": [...]}`` too, each pool's scrub that was seen
running with its last status or why it could not be read
(``storage.scrubs.current``), after auth_ok once a scrub was seen running and
whenever they change; while it is open, the scrubs that run are read again
every 2 seconds (ADR-0367). The same way, it sends administrators
``{"type": "balances", "data": [...]}``, each pool's balance that was seen
running (``storage.balances.current``, ADR-0379).

It also sends administrators ``{"type": "repair", "data": {...}}``, the running
repair of a share's permissions with its counts so far, or the last one that
ended (``shares.repair_router.current_job``), right after auth_ok, with
``"data": null`` when none has run since the start (a web UI that reconnects
after a restart learns that its repair no longer runs), and whenever it
changes; regular users never get it (ADR-0363).
"""
import asyncio
import functools
import json
import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any

import psutil
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from ..auth.models import User
from ..auth.utils import authenticate_token
from ..config import get_settings
from ..database import async_session
from ..health import problems
from ..logs import router as logs
from ..storage import balances, scrubs
from ..shares import repair_router
from ..system.sensors import cpu_temperature

router = APIRouter()
logger = logging.getLogger(__name__)

WS_AUTH_TIMEOUT_SECONDS = 10.0
WS_REAUTH_INTERVAL_SECONDS = 60.0
STATS_INTERVAL_SECONDS = 2.0
# Lines of a container's log that its stream starts with, before the new ones
# (the container drawer of the web UI shows them, ADR-0255).
LOG_STREAM_TAIL_LINES = 200
# A logs socket closes with 1000 once the log ended or could not be read; the web
# UI shows any other end as a lost connection (ADR-0255).
WS_CLOSE_NORMAL = 1000
WS_CLOSE_UNAUTHORIZED = 4401
WS_CLOSE_FORBIDDEN = 4403
WS_CLOSE_INTERNAL_ERROR = 1011

# The sockets the supervisor holds (ADR-0333). Uvicorn's --limit-concurrency refuses plain
# requests over its limit but never a WebSocket upgrade, while every open socket counts toward
# it: a client that opened sockets without end, or left them waiting for their auth message,
# would use up the limit for every request. Past these caps a socket is refused before it is
# accepted (the client gets HTTP 403), so sockets alone never fill uvicorn's 128.
MAX_OPEN_SOCKETS = 64
MAX_SOCKETS_BEFORE_AUTH = 16
_open_sockets = 0
_sockets_before_auth = 0
# Generous bound for {"type": "auth", "token": ...}; anything larger is not a token.
_MAX_AUTH_MESSAGE_CHARS = 16 * 1024

# Track previous values for rate calculation
_prev_net_io = None
_prev_disk_io = None
_prev_time = None
_prev_net_per_iface = {}

# The list of the host's processes is most of a sample's cost: on a SpacemiT K1 one listing of
# 315 processes took 0.4 s of CPU. A sample reuses the last list until it is this old (ADR-0374).
PROCESS_LIST_INTERVAL_SECONDS = 10.0
_processes: list[dict] = []
_processes_at: float | None = None

# One sample serves every socket of /ws/v1/events until it is STATS_INTERVAL_SECONDS old, taken in
# a worker thread (shared_system_stats, ADR-0374).
_sample: dict | None = None
_sample_at: float | None = None
_sample_lock = threading.Lock()
_clock = time.monotonic


def shared_system_stats() -> dict:
    """The stats message for every open socket: the last sample while it is younger than
    STATS_INTERVAL_SECONDS, else a new one. Each socket used to take its own every interval, in the
    event loop, which on a slow board cost a share of a core per open tab and held the API while it
    ran (ADR-0374). Callers run it in a worker thread; the lock makes concurrent callers wait for
    one sample instead of taking one each."""
    global _sample, _sample_at
    with _sample_lock:
        now = _clock()
        if _sample is None or _sample_at is None or now - _sample_at >= STATS_INTERVAL_SECONDS:
            _sample = get_system_stats()
            _sample_at = now
        return _sample


def _top_processes() -> list[dict]:
    """The ten processes of the host that use the most CPU, listed again only when the last list is
    PROCESS_LIST_INTERVAL_SECONDS old; psutil gives each process's CPU share since the listing
    before. Only the CPU share is read for every process, the rest for the ten: on a K1, name,
    memory, status and user of 311 processes took 0.41 s of CPU, their CPU shares 0.13 s
    (ADR-0375)."""
    global _processes, _processes_at
    now = _clock()
    if _processes_at is not None and now - _processes_at < PROCESS_LIST_INTERVAL_SECONDS:
        return _processes
    listed = sorted(
        psutil.process_iter(["cpu_percent"]), key=lambda proc: proc.info["cpu_percent"] or 0, reverse=True
    )
    processes = []
    for proc in listed:
        if len(processes) == 10:
            break
        try:
            info = proc.as_dict(["name", "memory_percent", "memory_info", "status", "username"], ad_value=None)
        except psutil.NoSuchProcess:
            continue
        processes.append({
            "pid": proc.pid,
            "name": info["name"] or "Unknown",
            "cpu_percent": proc.info["cpu_percent"] or 0,
            "memory_percent": info["memory_percent"] or 0,
            "memory_mb": (info["memory_info"].rss / 1024 / 1024) if info["memory_info"] else 0,
            "status": info["status"] or "unknown",
            "username": info["username"] or "unknown"
        })
    _processes, _processes_at = processes, now
    return _processes


def get_system_stats() -> dict:
    """Get comprehensive system statistics for WebSocket broadcast."""
    global _prev_net_io, _prev_disk_io, _prev_time, _prev_net_per_iface

    current_time = time.time()
    time_delta = current_time - _prev_time if _prev_time else 2.0

    # CPU stats
    cpu_percent = psutil.cpu_percent(interval=None)
    per_core = psutil.cpu_percent(interval=None, percpu=True)
    load = psutil.getloadavg()
    cpu_count = psutil.cpu_count(logical=False) or 1
    cpu_threads = psutil.cpu_count(logical=True) or 1

    # Memory stats
    memory = psutil.virtual_memory()
    swap = psutil.swap_memory()

    # Network stats with rate calculation
    net_io = psutil.net_io_counters()
    bytes_sent_rate = 0.0
    bytes_recv_rate = 0.0
    if _prev_net_io and time_delta > 0:
        bytes_sent_rate = (net_io.bytes_sent - _prev_net_io.bytes_sent) / time_delta
        bytes_recv_rate = (net_io.bytes_recv - _prev_net_io.bytes_recv) / time_delta

    # Per-interface network stats with rates
    interfaces = []
    net_if_stats = psutil.net_if_stats()
    net_if_addrs = psutil.net_if_addrs()
    net_if_counters = psutil.net_io_counters(pernic=True)

    for name, stats in net_if_stats.items():
        if name == "lo" or not stats.isup:
            continue

        addrs = net_if_addrs.get(name, [])
        ipv4 = next((a.address for a in addrs if a.family.name == "AF_INET"), None)
        counters = net_if_counters.get(name)

        # Calculate per-interface rates
        iface_sent_rate = 0.0
        iface_recv_rate = 0.0
        if name in _prev_net_per_iface and counters and time_delta > 0:
            prev = _prev_net_per_iface[name]
            iface_sent_rate = (counters.bytes_sent - prev["bytes_sent"]) / time_delta
            iface_recv_rate = (counters.bytes_recv - prev["bytes_recv"]) / time_delta

        if counters:
            _prev_net_per_iface[name] = {
                "bytes_sent": counters.bytes_sent,
                "bytes_recv": counters.bytes_recv
            }

        interfaces.append({
            "name": name,
            "ipv4": ipv4,
            "speed": stats.speed,
            "is_up": stats.isup,
            "bytes_sent": counters.bytes_sent if counters else 0,
            "bytes_recv": counters.bytes_recv if counters else 0,
            "bytes_sent_rate": iface_sent_rate,
            "bytes_recv_rate": iface_recv_rate,
        })

    # Disk I/O stats with rate calculation
    disk_io = psutil.disk_io_counters()
    read_rate = 0.0
    write_rate = 0.0
    if _prev_disk_io and time_delta > 0:
        read_rate = (disk_io.read_bytes - _prev_disk_io.read_bytes) / time_delta
        write_rate = (disk_io.write_bytes - _prev_disk_io.write_bytes) / time_delta

    # Per-disk stats: every block device of /proc/diskstats, disks and partitions, which the
    # kernel lists as soon as it has one, also one attached after the supervisor started. The
    # Storage page loads its lists again when these names change (ADR-0360).
    disks = []
    disk_io_per = psutil.disk_io_counters(perdisk=True)
    for name, counters in disk_io_per.items():
        if name.startswith("loop") or name.startswith("ram"):
            continue
        disks.append({
            "name": name,
            "read_bytes": counters.read_bytes,
            "write_bytes": counters.write_bytes,
            "read_count": counters.read_count,
            "write_count": counters.write_count
        })

    # Top processes, listed at most every PROCESS_LIST_INTERVAL_SECONDS
    top_processes = _top_processes()

    timestamp = datetime.now().isoformat()

    # Store previous values for next calculation
    _prev_net_io = net_io
    _prev_disk_io = disk_io
    _prev_time = current_time

    return {
        "type": "stats",
        "data": {
            # CPU stats
            "cpu": {
                "percent": cpu_percent,
                "per_core": per_core,
                "load_1": load[0],
                "load_5": load[1],
                "load_15": load[2],
                "cores": cpu_count,
                "threads": cpu_threads,
                # The CPU's or SoC's, None without sensors (ADR-0346).
                "temperature": cpu_temperature(),
            },

            # Memory stats
            "memory": {
                "total": memory.total,
                "used": memory.used,
                "free": memory.free,
                "available": memory.available,
                "percent": memory.percent,
                "swap_total": swap.total,
                "swap_used": swap.used,
                "swap_percent": swap.percent,
            },

            # Network stats
            "network": {
                "bytes_sent": net_io.bytes_sent,
                "bytes_recv": net_io.bytes_recv,
                "bytes_sent_rate": bytes_sent_rate,
                "bytes_recv_rate": bytes_recv_rate,
                "packets_sent": net_io.packets_sent,
                "packets_recv": net_io.packets_recv,
                "interfaces": interfaces,
            },

            # Disk I/O stats
            "disk_io": {
                "read_bytes": disk_io.read_bytes,
                "write_bytes": disk_io.write_bytes,
                "read_rate": read_rate,
                "write_rate": write_rate,
                "read_count": disk_io.read_count,
                "write_count": disk_io.write_count,
                "disks": disks,
            },

            # Top processes
            "processes": top_processes,

            # Timestamp
            "timestamp": timestamp,
        },
    }


async def _close(websocket: WebSocket, code: int, reason: str) -> None:
    try:
        await websocket.close(code=code, reason=reason)
    except (RuntimeError, OSError, WebSocketDisconnect):
        pass  # the client is already gone


def _token_from_auth_message(message: dict[str, Any]) -> str | None:
    text = message.get("text")
    if not isinstance(text, str) or len(text) > _MAX_AUTH_MESSAGE_CHARS:
        return None
    try:
        payload = json.loads(text)
    except ValueError:
        return None
    if not isinstance(payload, dict) or payload.get("type") != "auth":
        return None
    token = payload.get("token")
    if not isinstance(token, str) or not token:
        return None
    return token


async def _user_for_token(token: str) -> User | None:
    # Short-lived session: a socket can stay open for hours and must not hold
    # a pooled database connection that long.
    try:
        async with async_session() as db:
            return await authenticate_token(token, db)
    except HTTPException:
        return None


async def authenticate_websocket(
    websocket: WebSocket, *, admin_only: bool = False
) -> tuple[str, User] | None:
    """Accept ``websocket`` and run the auth handshake described above.

    Returns the accepted token (``_watch_credential`` checks it again while
    the socket is open) and its user, or ``None`` after the socket has been
    closed (or the client went away). The socket must be accepted before it
    can be closed with an application close code, so rejected clients are
    accepted first and then closed with 4401/4403. Past MAX_OPEN_SOCKETS or
    MAX_SOCKETS_BEFORE_AUTH a socket is refused before it is accepted. A socket
    that signed in stays counted until its endpoint calls ``release_socket``.
    """
    global _open_sockets, _sockets_before_auth
    if _open_sockets >= MAX_OPEN_SOCKETS or _sockets_before_auth >= MAX_SOCKETS_BEFORE_AUTH:
        await websocket.close()  # not accepted yet: the client gets HTTP 403
        return None
    _open_sockets += 1
    _sockets_before_auth += 1
    signed_in = None
    try:
        signed_in = await _handshake(websocket, admin_only)
        return signed_in
    finally:
        _sockets_before_auth -= 1
        if signed_in is None:
            _open_sockets -= 1


def release_socket() -> None:
    """A socket that signed in has closed: it no longer counts (ADR-0333)."""
    global _open_sockets
    _open_sockets -= 1


async def _handshake(websocket: WebSocket, admin_only: bool) -> tuple[str, User] | None:
    await websocket.accept()

    if "token" in websocket.query_params:
        await _close(websocket, WS_CLOSE_UNAUTHORIZED, "unauthorized")
        return None

    try:
        message = await asyncio.wait_for(websocket.receive(), WS_AUTH_TIMEOUT_SECONDS)
    except TimeoutError:
        await _close(websocket, WS_CLOSE_UNAUTHORIZED, "unauthorized")
        return None
    if message["type"] == "websocket.disconnect":
        return None

    token = _token_from_auth_message(message)
    user = await _user_for_token(token) if token else None
    if user is None:
        await _close(websocket, WS_CLOSE_UNAUTHORIZED, "unauthorized")
        return None
    if admin_only and not user.is_admin:
        await _close(websocket, WS_CLOSE_FORBIDDEN, "forbidden")
        return None

    await websocket.send_json({"type": "auth_ok"})
    return token, user


# Re-checks that a closed socket left running (ADR-0312): the event loop keeps only weak
# references to tasks.
_checks_left_running: set["asyncio.Task[Any]"] = set()


async def _watch_credential(
    token: str, *, admin_only: bool = False, on_user: Callable[[User], None] | None = None
) -> tuple[int, str]:
    """Check ``token`` every WS_REAUTH_INTERVAL_SECONDS while a socket is open.

    Returns the close code and reason once the socket must be closed; a check
    that keeps it open gives ``on_user`` the user as it is now. Each check
    uses a new short-lived database session (see ``_user_for_token``).
    Cancelling the watch ends it at once, but a check in progress runs to its
    end and its answer is dropped (ADR-0312): cancelled in its database query,
    a check discards the session's connection, and when that connection's
    close is cancelled as well, aiosqlite waits for ever.
    """
    while True:
        await asyncio.sleep(WS_REAUTH_INTERVAL_SECONDS)
        check = asyncio.ensure_future(_user_for_token(token))
        try:
            user = await asyncio.shield(check)
        except asyncio.CancelledError:
            _checks_left_running.add(check)
            check.add_done_callback(_checks_left_running.discard)
            check.add_done_callback(_consume_result)
            raise
        except Exception:
            logger.exception("Could not re-check a WebSocket's credentials")
            return WS_CLOSE_INTERNAL_ERROR, "re-authentication failed"
        if user is None:
            return WS_CLOSE_UNAUTHORIZED, "unauthorized"
        if admin_only and not user.is_admin:
            return WS_CLOSE_FORBIDDEN, "forbidden"
        if on_user is not None:
            on_user(user)


async def _send_problems(websocket: WebSocket, is_admin: bool, sent: int | None) -> int | None:
    """Send an administrator the problems when they changed since the version ``sent`` (None:
    none sent yet), and someone who is no longer one an empty list once; returns what was sent."""
    if not is_admin:
        if sent is not None:
            await websocket.send_json({"type": "problems", "data": []})
        return None
    version = problems.version()
    if version != sent:
        await websocket.send_json(
            {"type": "problems", "data": [problem.model_dump() for problem in problems.current()]}
        )
    return version


async def _send_scrubs(websocket: WebSocket, is_admin: bool, sent: int) -> int:
    """Send an administrator the scrubs when they changed since the version ``sent`` (0: none seen
    yet), after asking for a read of the ones that run; returns the version sent."""
    if not is_admin:
        return sent
    scrubs.follow()
    version = scrubs.version()
    if version != sent:
        await websocket.send_json({"type": "scrubs", "data": scrubs.current()})
    return version


async def _send_balances(websocket: WebSocket, is_admin: bool, sent: int) -> int:
    """_send_scrubs for the balances of the pools (ADR-0379)."""
    if not is_admin:
        return sent
    balances.follow()
    version = balances.version()
    if version != sent:
        await websocket.send_json({"type": "balances", "data": balances.current()})
    return version


# What _send_repair has sent a socket before its first repair message.
_NO_REPAIR_SENT = object()


async def _send_repair(websocket: WebSocket, is_admin: bool, sent: object) -> object:
    """Send an administrator the repair job, None before any, when it is another than ``sent``
    (_NO_REPAIR_SENT: nothing sent yet; each change of the job is a new object); returns what was
    sent, _NO_REPAIR_SENT for anyone else."""
    if not is_admin:
        return _NO_REPAIR_SENT
    job = repair_router.current_job()
    if job is not sent:
        await websocket.send_json({"type": "repair", "data": None if job is None else job.model_dump(mode="json")})
    return job


@router.websocket("/ws/v1/events")
async def websocket_endpoint(websocket: WebSocket):
    signed_in = await authenticate_websocket(websocket)
    if signed_in is None:
        return
    token, user = signed_in
    # Whether the user is an administrator, as the last check of the token found (ADR-0332).
    role = {"admin": user.is_admin}

    def note_role(user: User) -> None:
        role["admin"] = user.is_admin

    # Reading the socket notices a closed client right away (not only when the
    # next send fails) and drains anything the client sends.
    disconnected = asyncio.ensure_future(_wait_for_disconnect(websocket))
    revoked = asyncio.ensure_future(_watch_credential(token, on_user=note_role))
    sent_problems: int | None = None
    sent_scrubs = 0
    sent_balances = 0
    sent_repair: object = _NO_REPAIR_SENT
    try:
        # Start sending stats
        while not (disconnected.done() or revoked.done()):
            sent_problems = await _send_problems(websocket, role["admin"], sent_problems)
            sent_scrubs = await _send_scrubs(websocket, role["admin"], sent_scrubs)
            sent_balances = await _send_balances(websocket, role["admin"], sent_balances)
            sent_repair = await _send_repair(websocket, role["admin"], sent_repair)
            stats = await asyncio.to_thread(shared_system_stats)
            await websocket.send_json(stats)
            await asyncio.wait(
                {disconnected, revoked},
                timeout=STATS_INTERVAL_SECONDS,
                return_when=asyncio.FIRST_COMPLETED,
            )
        if revoked.done() and not disconnected.done():
            await _close(websocket, *revoked.result())
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        _finish(disconnected)
        _finish(revoked)
        release_socket()


@router.websocket("/ws/v1/logs/{container_id}")
async def container_logs_endpoint(websocket: WebSocket, container_id: str):
    """Stream container logs in real-time (administrators only)."""
    signed_in = await authenticate_websocket(websocket, admin_only=True)
    if signed_in is None:
        return

    try:
        await _stream_container_logs(websocket, container_id, signed_in[0])
    finally:
        release_socket()


@router.websocket("/ws/v1/journal")
async def journal_endpoint(websocket: WebSocket):
    """Follow the host's journal for System > Logs (administrators only, ADR-0366)."""
    signed_in = await authenticate_websocket(websocket, admin_only=True)
    if signed_in is None:
        return

    try:
        await _follow_journal(websocket, signed_in[0])
    finally:
        release_socket()


async def _follow_journal(websocket: WebSocket, token: str) -> None:
    disconnected = asyncio.ensure_future(_wait_for_disconnect(websocket))
    # Runs on its own timer, so access is re-checked while the journal is quiet too.
    revoked = asyncio.ensure_future(_watch_credential(token, admin_only=True))
    stream = None

    async def until_done(work: "asyncio.Future[Any]") -> bool:
        """Wait for ``work``; False, after the close that a revoked access needs, when the socket
        must end first (the client left, or its access was revoked)."""
        await asyncio.wait({work, disconnected, revoked}, return_when=asyncio.FIRST_COMPLETED)
        if work.done() and not (disconnected.done() or revoked.done()):
            return True
        _finish(work)
        if revoked.done() and not disconnected.done():
            await _close(websocket, *revoked.result())
        return False

    try:
        query = logs.journal_query(websocket.query_params)
        # The follow starts after the newest entry read, or, when none is found, at this time.
        started = time.time()
        newest = asyncio.ensure_future(
            logs.read_journal(
                query.source, limit=query.limit, priority=query.priority,
                since=query.since or None, search=query.search or None,
            )
        )
        if not await until_done(newest):
            return
        entries = newest.result().entries
        await websocket.send_json({"type": "entries", "data": [entry.model_dump() for entry in entries]})
        stream = await logs.follow_journal(query, after=entries[0].cursor if entries else None, since=started)
        while True:
            next_line = asyncio.ensure_future(stream.readline())
            if not await until_done(next_line):
                return
            line = next_line.result()
            if line is None:
                ended = await stream.result()
                raise HTTPException(500, logs.journal_ended(ended))
            entry = logs.journal_entry(line, query.source)
            if entry is not None:
                await websocket.send_json({"type": "entry", "data": entry.model_dump()})
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        if isinstance(exc, HTTPException):
            message = exc.detail
        else:
            logger.exception("The journal socket failed")
            message = logs.CANNOT_READ
        if not disconnected.done():
            try:
                await websocket.send_json({"type": "error", "message": message})
            except Exception:
                pass
            await _close(websocket, WS_CLOSE_NORMAL, "journal error")
    finally:
        _finish(disconnected)
        _finish(revoked)
        if stream is not None:
            await stream.stop()


async def _wait_for_disconnect(websocket: WebSocket) -> None:
    """Return once the client disconnects; anything else it sends is ignored."""
    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return


def _consume_result(future: "asyncio.Future[Any]") -> None:
    """Done-callback that marks an abandoned future's outcome as retrieved."""
    if not future.cancelled():
        future.exception()


def _finish(task: "asyncio.Future[Any]") -> None:
    """Cancel ``task`` if still running, otherwise retrieve its outcome."""
    if task.done():
        _consume_result(task)
    else:
        task.cancel()


async def _stream_container_logs(websocket: WebSocket, container_id: str, token: str) -> None:
    import docker

    loop = asyncio.get_running_loop()
    # docker-py blocks while it waits for the next log line. Run it on a
    # private thread: iterating it on the event loop froze the whole API for
    # as long as a quiet container produced no output, and a shared pool
    # thread could be held for hours by one open log view.
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ws-container-logs")

    def run(func: Any, *args: Any, **kwargs: Any) -> "asyncio.Future[Any]":
        return loop.run_in_executor(executor, functools.partial(func, *args, **kwargs))

    disconnected = asyncio.ensure_future(_wait_for_disconnect(websocket))
    # Runs on its own timer, so access is re-checked while the container is quiet too.
    revoked = asyncio.ensure_future(_watch_credential(token, admin_only=True))
    client = None
    stream = None
    try:
        client = await run(docker.DockerClient, base_url=f"unix://{get_settings().docker_socket}")
        container = await run(client.containers.get, container_id)
        # Each line starts with the time Docker received it, as in GET /containers/{id}/logs.
        stream = await run(
            container.logs, stream=True, follow=True, tail=LOG_STREAM_TAIL_LINES, timestamps=True
        )

        while True:
            next_chunk = run(next, stream, None)
            await asyncio.wait(
                {next_chunk, disconnected, revoked}, return_when=asyncio.FIRST_COMPLETED
            )
            if revoked.done() or not next_chunk.done():
                # Access was revoked, or the client left while we waited for
                # output: send nothing more. Closing the stream (finally)
                # unblocks the reader thread.
                next_chunk.add_done_callback(_consume_result)
                if revoked.done() and not disconnected.done():
                    await _close(websocket, *revoked.result())
                return
            chunk = next_chunk.result()
            if chunk is None:
                # The log ended, e.g. the container stopped. Returning without a
                # close would make uvicorn drop the connection (1006 in a browser).
                await _close(websocket, WS_CLOSE_NORMAL, "log ended")
                return
            await websocket.send_json({
                "type": "log",
                "container_id": container_id,
                "data": chunk.decode("utf-8", errors="replace"),
            })
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        if not disconnected.done():
            try:
                await websocket.send_json({"type": "error", "message": str(exc)})
            except Exception:
                pass
            await _close(websocket, WS_CLOSE_NORMAL, "log error")
    finally:
        _finish(disconnected)
        _finish(revoked)
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
        executor.shutdown(wait=False, cancel_futures=True)
