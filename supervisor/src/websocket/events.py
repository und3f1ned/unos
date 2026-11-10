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
"""
import asyncio
import functools
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any

import psutil
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from ..auth.models import User
from ..auth.utils import authenticate_token
from ..config import get_settings
from ..database import async_session

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
# Generous bound for {"type": "auth", "token": ...}; anything larger is not a token.
_MAX_AUTH_MESSAGE_CHARS = 16 * 1024

# Track previous values for rate calculation
_prev_net_io = None
_prev_disk_io = None
_prev_time = None
_prev_net_per_iface = {}



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

    # Per-disk stats
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

    # Top processes
    processes = []
    for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent", "memory_info", "status", "username"]):
        try:
            info = proc.info
            processes.append({
                "pid": info["pid"],
                "name": info["name"] or "Unknown",
                "cpu_percent": info["cpu_percent"] or 0,
                "memory_percent": info["memory_percent"] or 0,
                "memory_mb": (info["memory_info"].rss / 1024 / 1024) if info["memory_info"] else 0,
                "status": info["status"] or "unknown",
                "username": info["username"] or "unknown"
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    processes.sort(key=lambda x: x["cpu_percent"], reverse=True)
    top_processes = processes[:10]

    # Temperature sensors
    temps = {}
    try:
        sensors = psutil.sensors_temperatures()
        for name, entries in sensors.items():
            if entries:
                temps[name] = entries[0].current
    except Exception:
        pass

    timestamp = datetime.now().isoformat()

    # Store previous values for next calculation
    _prev_net_io = net_io
    _prev_disk_io = disk_io
    _prev_time = current_time

    return {
        "type": "stats",
        "data": {
            "temperatures": temps,

            # CPU stats
            "cpu": {
                "percent": cpu_percent,
                "per_core": per_core,
                "load_1": load[0],
                "load_5": load[1],
                "load_15": load[2],
                "cores": cpu_count,
                "threads": cpu_threads,
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


async def authenticate_websocket(websocket: WebSocket, *, admin_only: bool = False) -> str | None:
    """Accept ``websocket`` and run the auth handshake described above.

    Returns the accepted token (``_watch_credential`` checks it again while
    the socket is open), or ``None`` after the socket has been closed (or the
    client went away). The socket must be accepted before it can be closed
    with an application close code, so rejected clients are accepted first
    and then closed with 4401/4403.
    """
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
    return token


# Re-checks that a closed socket left running (ADR-0312): the event loop keeps only weak
# references to tasks.
_checks_left_running: set["asyncio.Task[Any]"] = set()


async def _watch_credential(token: str, *, admin_only: bool = False) -> tuple[int, str]:
    """Check ``token`` every WS_REAUTH_INTERVAL_SECONDS while a socket is open.

    Returns the close code and reason once the socket must be closed. Each
    check uses a new short-lived database session (see ``_user_for_token``).
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


@router.websocket("/ws/v1/events")
async def websocket_endpoint(websocket: WebSocket):
    token = await authenticate_websocket(websocket)
    if token is None:
        return

    # Reading the socket notices a closed client right away (not only when the
    # next send fails) and drains anything the client sends.
    disconnected = asyncio.ensure_future(_wait_for_disconnect(websocket))
    revoked = asyncio.ensure_future(_watch_credential(token))
    try:
        # Start sending stats
        while not (disconnected.done() or revoked.done()):
            stats = get_system_stats()
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


@router.websocket("/ws/v1/logs/{container_id}")
async def container_logs_endpoint(websocket: WebSocket, container_id: str):
    """Stream container logs in real-time (administrators only)."""
    token = await authenticate_websocket(websocket, admin_only=True)
    if token is None:
        return

    await _stream_container_logs(websocket, container_id, token)


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
