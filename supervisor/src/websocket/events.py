"""Real-time WebSocket endpoints.

Authentication handshake, used by both sockets. Tokens never travel in the
URL, because request URLs end up in the uvicorn and proxy access logs:

1. The client connects without query parameters.
2. Within 10 seconds it sends one text message:
   ``{"type": "auth", "token": "<JWT or unos_ API token>"}``. The web UI sends
   ``{"type": "auth", "key": "<session key>"}`` without a token: the server takes
   the session cookie of the handshake, which counts only from the web UI's own
   page, an Origin of https://<the handshake's Host>, and with its session key
   (ADR-0397); no CORS protects a WebSocket.
3. The token is checked exactly like an HTTP bearer token (signature, expiry,
   API token state, user still exists). The server answers
   ``{"type": "auth_ok"}`` and the regular message stream follows.

The server closes the socket with 4401 ("unauthorized") when the first
message is missing, late, malformed or carries an invalid token, and with
4403 ("forbidden") when the user may not use the socket (container logs
require an administrator), can only set up two-factor authentication, which
no socket serves (ADR-0386), or signs in with an API token whose scope does not
reach the socket (ADR-0392). A ``?token=`` query parameter is refused (4401).

While a socket is open, its token is checked again every
``WS_REAUTH_INTERVAL_SECONDS``. A token that stopped being valid (expired
JWT, disabled or expired API token, deleted user) closes the socket with
4401; a logs socket whose user is no longer an administrator, every socket
of an account whose grace period to set up two-factor authentication has ended,
and every socket of an API token whose scope, read again, no longer reaches it,
is closed with 4403. If the check itself fails (database error), the socket is closed with
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
running (``storage.balances.current``, ADR-0379), and
``{"type": "disk_removals", "data": [...]}``, each pool's disk removal that was seen running
(``storage.devices.current``, ADR-0410).

It also sends administrators ``{"type": "repair", "data": {...}}``, the running
repair of a share's permissions with its counts so far, or the last one that
ended (``shares.repair_router.current_job``), right after auth_ok, with
``"data": null`` when none has run since the start (a web UI that reconnects
after a restart learns that its repair no longer runs), and whenever it
changes; regular users never get it (ADR-0363).

It sends administrators ``{"type": "app_installs", "data": [...]}``, the running install of an
app with its progress and the last install of each app since the start, each as
``GET /api/v1/apps/install/jobs`` answers it (``apps.install_jobs.current``), after auth_ok once
there is one and whenever they change (ADR-0384); regular users never get it.
"""
import asyncio
import functools
import json
import logging
import os
import pwd
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import psutil
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from .. import host
from ..times import utc_now
from ..auth import scopes
from ..auth.models import User
from ..auth.utils import (
    API_TOKEN_PREFIX, SESSION_COOKIE, authenticate_token, check_session_key, from_own_origin, is_cookie_session,
)
from ..config import get_settings
from ..apps import install_jobs
from ..database import async_session
from ..health import problems
from ..logs import router as logs
from ..network import links
from ..storage import balances, devices, scrubs
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

# The counters of the last sample, for the rates of the next: each interface's (received bytes,
# sent bytes), each physical disk's (read bytes, written bytes), the busy and total CPU time of the
# CPU and of each core, and the time of _clock.
_prev_net: dict[str, tuple[int, int]] = {}
_prev_disks: dict[str, tuple[int, int]] = {}
_prev_cpu: tuple[tuple[float, float], list[tuple[float, float]]] | None = None
_prev_time: float | None = None

# The host's traffic counters (ADR-0388). /proc/net/dev lists the interfaces of the network
# namespace of the process that reads it, and the supervisor's container has one of its own
# (unos-net): psutil showed the container's traffic, mostly this socket's, as the host's. With
# pid: host, PID 1 is the host's init, and its /proc/1/net/dev lists the host's interfaces; reading
# it starts no process. With UNOS_HOST_EXEC=local (the stand-in, the test stack) the host is the
# machine the supervisor runs on, and psutil reads it.
HOST_NET_DEV = "/proc/1/net/dev"
_net_unreadable_logged = False

# The host's block devices: a physical disk has a `device` link in its folder, a zram, loop, device
# mapper or md device none, and a partition no folder (ADR-0388). Where there is no such folder
# (the stand-in on macOS), psutil lists whole disks only, and every one counts.
SYS_BLOCK = "/sys/block"

# Samples are taken only while a socket asks for them. The first after a longer gap would give the
# average since the last one as the current rate (a dashboard opened in the morning showed the
# night's backup for a moment): its rates are 0, like those of the first sample after a start, and
# its counters are the base of the next (ADR-0388).
RATE_MAX_GAP_SECONDS = 5 * STATS_INTERVAL_SECONDS

# The list of the host's processes is most of a sample's cost: on a SpacemiT K1 one listing of
# 315 processes took 0.4 s of CPU. A sample reuses the last list until it is this old (ADR-0374).
PROCESS_LIST_INTERVAL_SECONDS = 10.0
_processes: list[dict] = []
_processes_at: float | None = None

# The host's accounts, which name the users of its processes (ADR-0399): the container's own
# /etc/passwd has none of them. Read through PID 1's root (pid: host) when the file changed since
# the last read, by its inode, time and size; None until the first read.
HOST_PASSWD = "/proc/1/root/etc/passwd"
_host_users: tuple[tuple[int, int, int], dict[int, str]] | None = None
_passwd_unreadable_logged = False

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


def _host_user_names() -> dict[int, str] | None:
    """The host's account names by uid, from its /etc/passwd; None where the host is the machine
    itself (UNOS_HOST_EXEC=local), whose own accounts pwd reads. An empty map when the file cannot
    be read, which the log says once (ADR-0214): the processes then show their uids."""
    global _host_users, _passwd_unreadable_logged
    if host.runs_locally():
        return None
    try:
        with open(HOST_PASSWD, encoding="utf-8", errors="replace") as file:
            status = os.fstat(file.fileno())
            key = (status.st_ino, status.st_mtime_ns, status.st_size)
            if _host_users is not None and _host_users[0] == key:
                return _host_users[1]
            names: dict[int, str] = {}
            for line in file:
                fields = line.split(":")
                if len(fields) >= 3 and fields[2].isascii() and fields[2].isdigit():
                    names.setdefault(int(fields[2]), fields[0])
    except OSError as exc:
        if not _passwd_unreadable_logged:
            _passwd_unreadable_logged = True
            logger.warning("The host's user names cannot be read from %s: %s", HOST_PASSWD, exc)
        return {}
    _host_users = (key, names)
    return names


def _user_name(uid: int, host_names: dict[int, str] | None) -> str:
    """The name of ``uid`` as the host names it, or the uid as a number when it has none."""
    if host_names is not None:
        return host_names.get(uid, str(uid))
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return str(uid)


def _top_processes() -> list[dict]:
    """The ten processes of the host that use the most CPU, listed again only when the last list is
    PROCESS_LIST_INTERVAL_SECONDS old; psutil gives each process's CPU share since the listing
    before. Only the CPU share is read for every process, the rest for the ten: on a K1, name,
    memory, status and user of 311 processes took 0.41 s of CPU, their CPU shares 0.13 s
    (ADR-0375). With pid: host these are the host's processes, those of the apps' containers
    too; each has its name, its resident memory and its effective user as the host names it, and
    never its command line, which can hold a password (ADR-0399)."""
    global _processes, _processes_at
    now = _clock()
    if _processes_at is not None and now - _processes_at < PROCESS_LIST_INTERVAL_SECONDS:
        return _processes
    listed = sorted(
        psutil.process_iter(["cpu_percent"]), key=lambda proc: proc.info["cpu_percent"] or 0, reverse=True
    )
    host_names = _host_user_names()
    processes = []
    for proc in listed:
        if len(processes) == 10:
            break
        try:
            info = proc.as_dict(["name", "memory_info", "uids"], ad_value=None)
        except psutil.NoSuchProcess:
            continue
        uids = info["uids"]
        processes.append({
            "pid": proc.pid,
            "name": info["name"] or "?",
            "cpu_percent": proc.info["cpu_percent"] or 0,
            "memory_bytes": info["memory_info"].rss if info["memory_info"] else 0,
            "username": _user_name(uids.effective, host_names) if uids else "?",
        })
    _processes, _processes_at = processes, now
    return _processes


def _cpu_busy_and_total(times: Any) -> tuple[float, float]:
    """The busy and the total CPU time of psutil's ``cpu_times()``, counted as psutil's cpu_percent
    counts them: Linux counts the guest times in user and nice already, and idle and iowait are not
    busy."""
    total = sum(times) - getattr(times, "guest", 0.0) - getattr(times, "guest_nice", 0.0)
    return total - times.idle - getattr(times, "iowait", 0.0), total


def cpu_busy_percent(before: tuple[float, float], after: tuple[float, float]) -> float:
    """The share of the CPU time between two readings of _cpu_busy_and_total that was busy, in
    percent with one decimal; 0 when no time passed."""
    total = after[1] - before[1]
    if total <= 0:
        return 0.0
    return round(min(100.0, max(0.0, (after[0] - before[0]) / total * 100)), 1)


def cpu_reading() -> tuple[float, float]:
    """The host's busy and total CPU time now (/proc/stat, which no namespace hides)."""
    return _cpu_busy_and_total(psutil.cpu_times())


def interface_counters() -> dict[str, tuple[int, int]]:
    """The host's interfaces that System > Network lists (not the loopback, dummy0 or Docker's
    bridges and veths, links.is_hidden), each with its received and sent bytes. No interface when
    the host's counters cannot be read, which the log says once: no traffic rather than the
    container's (ADR-0388)."""
    global _net_unreadable_logged
    if host.runs_locally():
        counters = {
            name: (c.bytes_recv, c.bytes_sent) for name, c in psutil.net_io_counters(pernic=True).items()
        }
    else:
        try:
            with open(HOST_NET_DEV, encoding="utf-8", errors="replace") as file:
                parsed = links.parse_net_dev(file.read())
        except OSError as exc:
            if not _net_unreadable_logged:
                _net_unreadable_logged = True
                logger.warning("The host's network counters cannot be read from %s: %s", HOST_NET_DEV, exc)
            return {}
        counters = {name: (c.rx_bytes, c.tx_bytes) for name, c in parsed.items()}
    return {name: values for name, values in counters.items() if not links.is_hidden(name)}


def _is_physical_disk(name: str, sysfs: bool) -> bool:
    """Whether a device of /proc/diskstats is a physical disk: one with a ``device`` in its folder
    of /sys/block (ADR-0388). ``sysfs`` says whether there is a /sys/block to ask."""
    if not sysfs:
        return True
    return os.path.exists(os.path.join(SYS_BLOCK, name.replace("/", "!"), "device"))


def physical_disk_counters(per_device: dict[str, Any]) -> dict[str, tuple[int, int]]:
    """The read and written bytes of each physical disk among psutil's counters of the devices of
    /proc/diskstats (ADR-0388)."""
    sysfs = os.path.isdir(SYS_BLOCK)
    return {
        name: (counters.read_bytes, counters.write_bytes)
        for name, counters in per_device.items()
        if _is_physical_disk(name, sysfs)
    }


def get_system_stats() -> dict:
    """Get comprehensive system statistics for WebSocket broadcast."""
    global _prev_net, _prev_disks, _prev_cpu, _prev_time

    now = _clock()
    elapsed = now - _prev_time if _prev_time is not None else 0.0
    if elapsed > RATE_MAX_GAP_SECONDS:
        elapsed = 0.0

    def rate(new: int, old: int) -> float:
        """Bytes per second since the last sample, over the monotonic clock: the wall clock of a
        board without a battery-backed clock jumps when it is set at boot. A counter that went
        back (an interface's driver loaded again) counts nothing (ADR-0388)."""
        return max(0, new - old) / elapsed if elapsed > 0 else 0.0

    # The CPU's and each core's share of busy time since the last sample. psutil's
    # cpu_percent(interval=None) keeps its last reading per thread, and each sample runs in whichever
    # worker thread it is given: its share was of the time since that thread's last sample
    # (ADR-0399). Like the rates, none for the first sample and after a gap.
    cpu = cpu_reading()
    cores = [_cpu_busy_and_total(times) for times in psutil.cpu_times(percpu=True)]
    if elapsed > 0 and _prev_cpu is not None and len(_prev_cpu[1]) == len(cores):
        cpu_percent = cpu_busy_percent(_prev_cpu[0], cpu)
        per_core = [cpu_busy_percent(before, after) for before, after in zip(_prev_cpu[1], cores)]
    else:
        cpu_percent, per_core = 0.0, [0.0] * len(cores)
    load = psutil.getloadavg()
    cpu_count = psutil.cpu_count(logical=False) or 1
    cpu_threads = psutil.cpu_count(logical=True) or 1

    # Memory stats
    memory = psutil.virtual_memory()
    swap = psutil.swap_memory()

    # The host's network (ADR-0388): each rate is the sum of the rates of the interfaces that both
    # this sample and the last have, so that an interface that comes or goes (a USB adapter, a VPN)
    # never makes it negative or adds all its counters at once.
    net = interface_counters()
    interfaces = []
    for name, (bytes_recv, bytes_sent) in net.items():
        before = _prev_net.get(name)
        interfaces.append({
            "name": name,
            "bytes_sent": bytes_sent,
            "bytes_recv": bytes_recv,
            "bytes_sent_rate": rate(bytes_sent, before[1]) if before else 0.0,
            "bytes_recv_rate": rate(bytes_recv, before[0]) if before else 0.0,
        })

    # One read of the host's /proc/diskstats (not namespaced). The block devices: every one, disks
    # and partitions, which the kernel lists as soon as it has one, also one attached after the
    # supervisor started; the Storage page loads its lists again when these names change
    # (ADR-0360). The rates: the physical disks only, without zram, loop, device mapper and md
    # devices, which psutil's totals count (a write to LUKS or LVM twice), summed over the disks
    # that both samples have, as for the interfaces (ADR-0388).
    per_device = psutil.disk_io_counters(perdisk=True)
    disks = [{"name": name} for name in per_device if not name.startswith(("loop", "ram"))]
    physical = physical_disk_counters(per_device)
    read_rate = 0.0
    write_rate = 0.0
    for name, (read_bytes, write_bytes) in physical.items():
        before = _prev_disks.get(name)
        if before:
            read_rate += rate(read_bytes, before[0])
            write_rate += rate(write_bytes, before[1])

    # Top processes, listed at most every PROCESS_LIST_INTERVAL_SECONDS
    top_processes = _top_processes()

    timestamp = utc_now().isoformat()

    # Store previous values for next calculation
    _prev_net = net
    _prev_disks = physical
    _prev_cpu = (cpu, cores)
    _prev_time = now

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

            # The host's network: the rates of its interfaces and their sums (ADR-0388); regular
            # users get no interfaces (_for_role).
            "network": {
                "bytes_sent_rate": sum(interface["bytes_sent_rate"] for interface in interfaces),
                "bytes_recv_rate": sum(interface["bytes_recv_rate"] for interface in interfaces),
                "interfaces": interfaces,
            },

            # Disk I/O stats
            "disk_io": {
                "read_rate": read_rate,
                "write_rate": write_rate,
                "disks": disks,
            },

            # The host's processes that use the most CPU, for administrators only (_for_role).
            "processes": top_processes,

            # The seconds since the sample before, over which the rates and the CPU shares are taken:
            # 0 for the first sample and after a gap, whose rates and shares are 0 (ADR-0388). The
            # Resource Monitor draws no point of such a sample (ADR-0399).
            "sample_seconds": round(elapsed, 3),

            # Timestamp
            "timestamp": timestamp,
        },
    }


async def _close(websocket: WebSocket, code: int, reason: str) -> None:
    try:
        await websocket.close(code=code, reason=reason)
    except (RuntimeError, OSError, WebSocketDisconnect):
        pass  # the client is already gone


def _token_from_auth_message(websocket: WebSocket, message: dict[str, Any]) -> str | None:
    """The token that the first message ``message`` authenticates with: its own, or, without one, the
    session cookie of the handshake, from the web UI's own page only (ADR-0397). None when there is
    none or the message is no auth message."""
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
    if token is None:
        cookie = websocket.cookies.get(SESSION_COOKIE)
        if not cookie or cookie.startswith(API_TOKEN_PREFIX) or not from_own_origin(websocket, origin_required=True):
            return None
        try:
            check_session_key(cookie, payload.get("key"))
        except HTTPException:
            return None
        return cookie
    # The token of a session of the cookie counts only in the cookie (ADR-0397).
    if not isinstance(token, str) or not token or is_cookie_session(token):
        return None
    return token


class _SetupOnly(Exception):
    """The token is valid but may not use the socket: its account can only set up two-factor
    authentication, whose session stays, or the API token's scope does not reach the socket
    (ADR-0392). The socket closes with 4403, not 4401."""


async def _user_for_token(token: str, path: str) -> User | None:
    """The user of ``token`` for the socket at ``path``, or None when it is not valid; raises
    _SetupOnly for an account that can only set up two-factor authentication and for an API token
    whose scope does not reach the socket."""
    # Short-lived session: a socket can stay open for hours and must not hold
    # a pooled database connection that long.
    try:
        async with async_session() as db:
            return await authenticate_token(token, db, operation=scopes.Operation(scopes.WEBSOCKET, path))
    except HTTPException as exc:
        if exc.status_code == 403:
            raise _SetupOnly() from None
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


def signed_in_sockets() -> int:
    """The sockets open now whose user signed in: every page of the web UI keeps one open, so each is a
    web session that keeps the host from idling (ADR-0407)."""
    return max(0, _open_sockets - _sockets_before_auth)


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

    token = _token_from_auth_message(websocket, message)
    try:
        user = await _user_for_token(token, scopes.routed_path(websocket.scope)) if token else None
    except _SetupOnly:
        await _close(websocket, WS_CLOSE_FORBIDDEN, "forbidden")
        return None
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
    token: str, path: str, *, admin_only: bool = False, on_user: Callable[[User], None] | None = None
) -> tuple[int, str]:
    """Check ``token`` for the socket at ``path`` every WS_REAUTH_INTERVAL_SECONDS while it is open.

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
        check = asyncio.ensure_future(_user_for_token(token, path))
        try:
            user = await asyncio.shield(check)
        except asyncio.CancelledError:
            _checks_left_running.add(check)
            check.add_done_callback(_checks_left_running.discard)
            check.add_done_callback(_consume_result)
            raise
        except _SetupOnly:
            return WS_CLOSE_FORBIDDEN, "forbidden"
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


async def _send_disk_removals(websocket: WebSocket, is_admin: bool, sent: int) -> int:
    """_send_scrubs for the disk removals of the pools (ADR-0410)."""
    if not is_admin:
        return sent
    devices.follow()
    version = devices.version()
    if version != sent:
        await websocket.send_json({"type": "disk_removals", "data": devices.current()})
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


async def _send_installs(websocket: WebSocket, is_admin: bool, sent: int | None) -> int | None:
    """Send an administrator the install jobs of apps when they changed since the version ``sent``,
    and a socket that got none yet (None) only when there is a job: the web UI empties its jobs at
    auth_ok. Returns the version it is up to date with (ADR-0384)."""
    if not is_admin:
        return sent
    version = install_jobs.version()
    if version == sent:
        return sent
    jobs = install_jobs.current()
    if sent is None and not jobs:
        return version
    await websocket.send_json({"type": "app_installs", "data": [job.model_dump(mode="json") for job in jobs]})
    return version


def _for_role(message: dict, is_admin: bool) -> dict:
    """The stats message for a socket of this role. The host's interfaces with their traffic are
    System > Network's, an administrator's page (ADR-0030): a regular user's dashboard gets the
    network's rates without them (ADR-0388); likewise the disks' rates without the names of the
    host's block devices, which the Storage page reads, and no processes of the host, which are
    System > Resource Monitor's (ADR-0399). The sample that every socket shares stays as it is."""
    data = message.get("data")
    if is_admin or not isinstance(data, dict):
        return message
    data = {**data, "processes": []}
    if isinstance(data.get("network"), dict):
        data["network"] = {**data["network"], "interfaces": []}
    if isinstance(data.get("disk_io"), dict):
        data["disk_io"] = {**data["disk_io"], "disks": []}
    return {**message, "data": data}


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
    revoked = asyncio.ensure_future(
        _watch_credential(token, scopes.routed_path(websocket.scope), on_user=note_role)
    )
    sent_problems: int | None = None
    sent_scrubs = 0
    sent_balances = 0
    sent_removals = 0
    sent_repair: object = _NO_REPAIR_SENT
    sent_installs: int | None = None
    try:
        # Start sending stats
        while not (disconnected.done() or revoked.done()):
            sent_problems = await _send_problems(websocket, role["admin"], sent_problems)
            sent_scrubs = await _send_scrubs(websocket, role["admin"], sent_scrubs)
            sent_balances = await _send_balances(websocket, role["admin"], sent_balances)
            sent_removals = await _send_disk_removals(websocket, role["admin"], sent_removals)
            sent_repair = await _send_repair(websocket, role["admin"], sent_repair)
            sent_installs = await _send_installs(websocket, role["admin"], sent_installs)
            stats = await asyncio.to_thread(shared_system_stats)
            await websocket.send_json(_for_role(stats, role["admin"]))
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
    revoked = asyncio.ensure_future(_watch_credential(token, scopes.routed_path(websocket.scope), admin_only=True))
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
    revoked = asyncio.ensure_future(_watch_credential(token, scopes.routed_path(websocket.scope), admin_only=True))
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
            logger.exception("The log stream of container %s failed", container_id)
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
