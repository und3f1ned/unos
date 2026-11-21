"""The power settings act on the host: the scheduled shutdown, the wake by the RTC and the idle
shutdown (ADR-0407).

``power_schedule_loop`` makes a pass one second after each minute begins. It acts only on what an
administrator turned on in the power settings (``auto_shutdown``, ``auto_wakeup``,
``idle_shutdown``, all off by default), and never where host commands run on the supervisor's own
machine (``UNOS_HOST_EXEC=local``). A shutdown is that of ``POST /power/shutdown`` (ADR-0233):
``shutdown -h +1`` on the host, which logind lists in the Power tab with its Cancel. Each one is
announced 10 minutes ahead as a problem of the source "power", which is alerted to every
notification channel and shown in the health banner (ADR-0332).

What the loop follows lives in RAM only (ADR-0214): the shutdown it announced or postponed, the
time since the host became idle, the last disk counters. The alarm is in the RTC, a pending
shutdown in logind, and the settings in /data/power_config.json, which only a PUT writes. A pass
logs only a change, and a failure once.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, time as clock_time, timedelta, tzinfo, UTC
from types import ModuleType
from typing import TYPE_CHECKING, Literal

import psutil
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .. import host
from ..audit.router import log_action
from ..audit.schemas import AuditAction, AuditSeverity
from ..health import problems
from ..times import UtcDatetime, from_epoch, utc_now, utc_text

if TYPE_CHECKING:
    from .router import PowerConfig

logger = logging.getLogger(__name__)

# The RTC whose alarm wakes the host, and the alarm: seconds since the epoch, empty while none is set.
RTC = "/sys/class/rtc/rtc0"
WAKEALARM = f"{RTC}/wakealarm"
# Reads the RTC on the host: "none" without it, "no-alarm" without its wakealarm, else its clock
# (since_epoch) and its alarm. The RTC's folder is the argument, never part of the script.
_RTC_READ = (
    'if [ ! -e "$1" ]; then echo none; exit 0; fi; '
    'if [ ! -e "$1/wakealarm" ]; then echo no-alarm; exit 0; fi; '
    'printf "since=%s\\nalarm=%s\\n" "$(cat "$1/since_epoch")" "$(cat "$1/wakealarm")"'
)
# Clears the alarm, then sets it to the second argument unless that is 0: the kernel refuses a new
# alarm while one is set.
_RTC_WRITE = 'echo 0 > "$1" && if [ "$2" != 0 ]; then echo "$2" > "$1"; fi'
RTC_READ = ["sh", "-c", _RTC_READ, "sh", RTC]
# How far the RTC's clock may be from the system clock: one in local time, or without its battery,
# would wake the host at another time.
RTC_TOLERANCE_SECONDS = 300

# logind's sessions, and the class of one: per-user service managers are no one logged in.
LIST_SESSIONS = [
    "busctl", "--json=short", "call", "org.freedesktop.login1", "/org/freedesktop/login1",
    "org.freedesktop.login1.Manager", "ListSessions",
]
_SESSION_PATH = re.compile(r"/org/freedesktop/login1/session/[A-Za-z0-9_]{1,64}")
_NOT_LOGGED_IN = frozenset({"manager", "manager-early"})
# The host's established TCP connections: "Recv-Q Send-Q Local:Port Peer:Port".
CONNECTIONS = ["ss", "-H", "-t", "-n", "state", "established"]
SMB_PORTS = frozenset({"445", "139"})
NFS_PORTS = frozenset({"2049"})

SCHEDULED_SHUTDOWN = ["shutdown", "-h", "+1", "UNOS: scheduled shutdown (power settings)"]
IDLE_SHUTDOWN = ["shutdown", "-h", "+1", "UNOS: idle shutdown (power settings)"]

# Seconds a host command of a pass may take.
HOST_TIMEOUT = 15
# A shutdown is announced this long before the pass that issues it.
WARNING = timedelta(minutes=10)
# The pass that issues a scheduled shutdown comes this long before its time: shutdown -h +1.
DECIDE_AHEAD = timedelta(minutes=1)
# A held shutdown is dropped for the day this long after its time.
POSTPONE_LIMIT = timedelta(hours=6)
# No shutdown when the next wake would come sooner after it; also the least time from the shutdown
# time to the wake time that PUT /power/config takes.
MIN_OFF = timedelta(minutes=10)
# A scheduled task due this soon is a running job.
DUE_AHEAD = timedelta(minutes=2)
# A disk that moved more than this many bytes per minute since the last pass is in use.
DISK_ACTIVE_BYTES = 1024 * 1024
# Passes come about a minute apart: an announcement out this much less than WARNING is out long enough.
SLACK_SECONDS = 30
# The system clock moved this much more or less than the monotonic clock between two passes: NTP set
# it after a boot without an RTC, or a person did. The schedule starts over from the new time.
CLOCK_JUMP_SECONDS = 120
# After an idle announcement that use withdrew, the next one comes no sooner than this: a host used
# now and then is not announced again and again.
IDLE_REANNOUNCE_SECONDS = 3600

NO_RTC = f"The host has no RTC: {RTC} does not exist"
NO_WAKEALARM = f"The RTC rtc0 has no wake alarm: {WAKEALARM} does not exist"
WAKE_TOO_CLOSE = "The wake time must come at least 10 minutes after the shutdown time"
NOT_MEASURED = "The disk activity is not measured yet: it is measured from one minute to the next"

FindingKind = Literal[
    "web_session", "login_session", "smb_client", "nfs_client", "disk_activity", "task", "task_due",
    "backup", "restore", "scrub", "balance", "disk_replace", "app_install", "app_update",
    "app_rollback", "offsite", "share_repair", "update",
    "wake_alarm", "unreadable",
]


class Finding(BaseModel):
    """One reason why the host is not idle, or why a scheduled shutdown waits."""
    kind: FindingKind = Field(description=(
        "What it is: an open page of the web UI (web_session), a login session on the host, an SMB or NFS client, "
        "activity on a disk, a running job (task, task_due, backup, restore, scrub, balance, disk_replace, "
        "app_install, app_update, app_rollback, offsite, share_repair, update), a wake alarm that the RTC does not "
        "hold (wake_alarm), or something that could not be read (unreadable)."))
    subject: str | None = Field(default=None, description=(
        "Whom or what it concerns, such as a task's, an app's or a share's name, a pool's path, a client's address, "
        "a user or a disk; null for none."))
    amount: int | None = Field(default=None, description=(
        "The pages open (web_session) or the bytes moved since the last pass (disk_activity); null for the others."))
    detail: str | None = Field(default=None, description=(
        "In English: why something could not be read (unreadable), or the wake alarm's problem (wake_alarm); null "
        "for the others."))


class RtcState(BaseModel):
    """Whether the RTC can wake the host, why not, and the alarm it holds."""
    available: bool = Field(description=(
        "Whether the RTC rtc0 can wake the host: it exists, has a wake alarm, and its clock is within 5 minutes of "
        "the system clock."))
    reason: str | None = Field(default=None, description="Why it cannot, in English; null when it can.")
    alarm: UtcDatetime | None = Field(
        default=None, description="The wake alarm it holds, in UTC; null when none is set."
    )


class ShutdownSchedule(BaseModel):
    """The scheduled shutdown."""
    enabled: bool = Field(description="Whether the scheduled shutdown is on.")
    time: str = Field(description="Its time, HH:MM on the host's clock.")
    next: UtcDatetime | None = Field(default=None, description=(
        "When the host shuts down next at the earliest, in UTC; null while the scheduled shutdown is off."))
    state: Literal["off", "planned", "announced", "postponed", "issued"] = Field(description=(
        "off; planned; announced: its problem is out, 10 minutes ahead; postponed: a running job holds it; issued: "
        "the host shuts down at next."))
    held_by: list[Finding] = Field(default=[], description="What holds it while it is postponed; none otherwise.")


class WakeSchedule(BaseModel):
    """The wake by the RTC's wake alarm."""
    enabled: bool = Field(description="Whether the wake is on.")
    time: str = Field(description="Its time, HH:MM on the host's clock.")
    next: UtcDatetime | None = Field(default=None, description=(
        "The next wake, in UTC; null while the wake is off or the RTC cannot wake the host: no wake is promised."))
    rtc: RtcState = Field(description="The RTC and the wake alarm it holds.")


class IdleState(BaseModel):
    """Whether the host is idle, and the idle shutdown."""
    enabled: bool = Field(description="Whether the idle shutdown is on.")
    minutes: int = Field(description="The idle minutes after which the idle shutdown shuts the host down.")
    idle: bool = Field(description="Whether the host is idle now: found is empty; also while the idle shutdown is off.")
    since: UtcDatetime | None = Field(default=None, description=(
        "Since when the host has been idle, in UTC; null while the idle shutdown is off or the host is not idle, "
        "and until a pass of the power schedule has seen it idle."))
    shutdown_at: UtcDatetime | None = Field(default=None, description=(
        "When the idle shutdown shuts the host down at the earliest, in UTC; null when since is null."))
    found: list[Finding] = Field(default=[], description="Why the host is not idle; none when it is.")


class PowerSchedule(BaseModel):
    """The scheduled shutdown, the wake and the idle shutdown of the power settings, as they stand now."""
    # GET /power/schedule (ADR-0407).

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "timezone": "Europe/Berlin",
        "shutdown": {"enabled": True, "time": "23:00", "next": "2026-11-19T22:00:00Z", "state": "planned",
                     "held_by": []},
        "wake": {"enabled": True, "time": "07:00", "next": "2026-11-20T06:00:00Z",
                 "rtc": {"available": True, "reason": None, "alarm": "2026-11-20T06:00:00Z"}},
        "idle": {"enabled": False, "minutes": 30, "idle": False, "since": None, "shutdown_at": None, "found": [
            {"kind": "web_session", "subject": None, "amount": 1, "detail": None},
            {"kind": "smb_client", "subject": "192.0.2.15", "amount": None, "detail": None},
        ]},
    }]})

    timezone: str = Field(description="The host's time zone, on whose clock the times are; UTC when it has no name.")
    shutdown: ShutdownSchedule = Field(description="The scheduled shutdown: its time, its next instant and its state.")
    wake: WakeSchedule = Field(description="The wake: its time, its next instant and the RTC.")
    idle: IdleState = Field(description="The idle shutdown, and whether the host is idle now and why not.")


class _Unreadable(Exception):
    """A part of the host that a pass could not read."""


@dataclass
class _Planned:
    """The scheduled shutdown that the loop follows."""
    key: tuple[int, int, str]  # hour, minute and zone of the settings it comes from
    due: datetime  # its time, in UTC
    announced: float | None = None  # monotonic time of its announcement
    announced_at: datetime | None = None  # and its time in UTC
    # How often it was announced again, after what held it ended.
    again: int = 0
    postponed: bool = False
    held_by: list[Finding] = field(default_factory=list)

    @property
    def expected(self) -> datetime:
        """When the host goes down at the earliest: at its time, or a shutdown's minute after the
        announcement has been out for WARNING."""
        if self.announced_at is None:
            return self.due
        return max(self.due, self.announced_at + WARNING + DECIDE_AHEAD)


# Replaced by the tests.
_utc_now = utc_now
_monotonic = time.monotonic
_sleep = asyncio.sleep

_running = False
_loop_task: asyncio.Task | None = None
_planned: _Planned | None = None
# The last scheduled shutdown that is over: its key, its time, and whether it was issued.
_finished: tuple[tuple[int, int, str], datetime, bool] | None = None
_last_issue_at: datetime | None = None
# Since when the host is idle: the monotonic time and the time in UTC.
_idle_since: tuple[float, datetime] | None = None
_idle_announced: float | None = None
# The monotonic time at which use withdrew the last idle announcement.
_idle_withdrawn: float | None = None
# The announcement of the idle shutdown, as made: it stays the same while it is out.
_idle_problem: problems.Problem | None = None
# The announcement of a shutdown that was issued, shown until the host goes down: until when, and it.
_going_down: tuple[datetime, dict[str, problems.Problem]] | None = None
# The last disk counters (monotonic time, bytes by disk) and what the last pass found from them.
_disk_sample: tuple[float, dict[str, int]] | None = None
_disk_found: list[Finding] | None = None
# The wake time the RTC holds, as the last pass found or set it; None when it holds no wanted one.
_armed: datetime | None = None
_alarm_problem: str | None = None
_issue_problem: str | None = None
_last_pass: datetime | None = None
_last_mono: float | None = None


def _reset() -> None:
    """Forget what the loop followed, as at a start of the supervisor."""
    global _planned, _finished, _last_issue_at, _idle_since, _idle_announced, _idle_withdrawn, _idle_problem
    global _going_down, _disk_sample, _disk_found, _armed, _alarm_problem, _issue_problem, _last_pass, _last_mono
    _planned = _finished = _last_issue_at = None
    _idle_since = _idle_announced = _idle_withdrawn = _idle_problem = _going_down = None
    _disk_sample = _disk_found = None
    _armed = _alarm_problem = _issue_problem = _last_pass = _last_mono = None


def _module(name: str) -> ModuleType:
    """A module of the supervisor, imported when used: the routers' packages export their APIRouter
    under the module's name, and the power router imports this module."""
    return importlib.import_module(name, __package__)


def _power() -> ModuleType:
    return _module(".router")


# ==================== Times ====================


def _zone() -> tzinfo:
    """The host's time zone, as the schedules of the tasks follow it (ADR-0266). Blocking."""
    return _module("..scheduler.router").schedule_zone()


def zone_name(zone: tzinfo) -> str:
    return getattr(zone, "key", None) or "UTC"


def next_instant(hour: int, minute: int, zone: tzinfo, after: datetime) -> datetime:
    """The first instant (UTC) after ``after`` at which the clock of ``zone`` shows hour:minute: a time
    that the start of daylight saving time skips comes at the end of the skip, one that its end shows
    twice comes the first time (scheduler.router._first_instant, ADR-0266)."""
    first_instant = _module("..scheduler.router")._first_instant
    day = after.astimezone(zone).date()
    for offset in range(3):
        at = first_instant(datetime.combine(day + timedelta(days=offset), clock_time(hour, minute)), zone)
        if at > after:
            return at
    raise AssertionError("a time of day comes within three days")


def _hhmm(hour: int, minute: int) -> str:
    return f"{hour:02d}:{minute:02d}"


def _on_the_clock(at: datetime, zone: tzinfo) -> str:
    """``at`` on the host's clock, as the announcements name it: "23:00 (Europe/Berlin)"."""
    return f"{at.astimezone(zone):%H:%M} ({zone_name(zone)})"


def check_times(config: PowerConfig) -> None:
    """400 for a wake time less than MIN_OFF after the shutdown time on the clock."""
    if not (config.auto_shutdown and config.auto_wakeup):
        return
    gap = ((config.wakeup_hour * 60 + config.wakeup_minute) - (config.shutdown_hour * 60 + config.shutdown_minute)) % 1440
    if gap < MIN_OFF.total_seconds() / 60:
        raise HTTPException(400, WAKE_TOO_CLOSE)


# ==================== The RTC ====================


def _why(result: host.HostResult) -> str:
    return _power()._why(result)


def read_rtc() -> RtcState:
    """Whether rtc0 can wake the host, and the alarm it holds. Blocking."""
    try:
        result = host.run_on_host(RTC_READ, timeout=HOST_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        return RtcState(available=False, reason=f"Could not read the RTC: {exc}")
    if not result.ok:
        return RtcState(available=False, reason=f"Could not read the RTC: {_why(result)}")
    text = result.stdout.strip()
    if text == "none":
        return RtcState(available=False, reason=NO_RTC)
    if text == "no-alarm":
        return RtcState(available=False, reason=NO_WAKEALARM)
    values = dict(line.partition("=")[::2] for line in text.splitlines())
    since, alarm = values.get("since", "").strip(), values.get("alarm", "").strip()
    if not since.isdigit():
        return RtcState(available=False, reason="The RTC's time cannot be read")
    off = abs(int(since) - _utc_now().timestamp())
    if off > RTC_TOLERANCE_SECONDS:
        return RtcState(available=False, reason=(
            f"The RTC's clock is {round(off / 60)} minutes off the system clock: it keeps local time or has "
            "lost its time"))
    held = from_epoch(int(alarm)) if alarm.isdigit() and int(alarm) > 0 else None
    return RtcState(available=True, alarm=held)


def _write_rtc(value: int) -> str | None:
    """Set the alarm to ``value`` seconds since the epoch, 0 to clear it; why it failed, or None. Blocking."""
    try:
        result = host.run_on_host(["sh", "-c", _RTC_WRITE, "sh", WAKEALARM, str(value)], timeout=HOST_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        return str(exc)
    return None if result.ok else _why(result)


def arm_rtc(at: datetime) -> str | None:
    """Set the alarm to ``at`` and read it back; None when the RTC holds it, else why not. Blocking."""
    error = _write_rtc(int(at.timestamp()))
    if error is not None:
        return f"Could not set the RTC's wake alarm: {error}"
    held = read_rtc()
    if held.alarm != at:
        return (f"The RTC did not keep the wake alarm for {utc_text(at)}: it holds "
                f"{utc_text(held.alarm) if held.alarm is not None else 'none'}")
    return None


async def apply_wake(saved: PowerConfig, new: PowerConfig) -> bool:
    """For PUT /power/config, before the settings are stored: a wake turned on or moved sets the
    alarm (409 when the RTC cannot wake the host, 500 when it keeps no alarm); a wake turned off clears
    it, unless the host no longer has the RTC or its alarm. Whether it set the alarm."""
    if host.runs_locally():
        return False
    moved = (saved.wakeup_hour, saved.wakeup_minute) != (new.wakeup_hour, new.wakeup_minute)
    if new.auto_wakeup and (not saved.auto_wakeup or moved):
        rtc = await asyncio.to_thread(read_rtc)
        if not rtc.available:
            raise HTTPException(409, f"No wake can be promised: {rtc.reason}")
        zone = await asyncio.to_thread(_zone)
        error = await asyncio.to_thread(
            arm_rtc, next_instant(new.wakeup_hour, new.wakeup_minute, zone, _utc_now()))
        if error is not None:
            logger.warning("%s", error)
            raise HTTPException(500, error)
        return True
    if saved.auto_wakeup and not new.auto_wakeup:
        rtc = await asyncio.to_thread(read_rtc)
        if rtc.reason in (NO_RTC, NO_WAKEALARM):
            return False  # nothing to clear
        error = await asyncio.to_thread(_write_rtc, 0)
        if error is not None:
            logger.warning("Could not clear the RTC's wake alarm: %s", error)
            raise HTTPException(500, f"Could not clear the RTC's wake alarm: {error}")
    return False


async def undo_wake() -> None:
    """Clear the alarm that apply_wake set for settings that could not be stored; logs a failure."""
    error = await asyncio.to_thread(_write_rtc, 0)
    if error is not None:
        logger.warning("Could not clear the RTC's wake alarm: %s", error)


def _report_alarm(problem: str | None) -> None:
    """Log a problem of the wake alarm when it is new, not at every pass while it lasts."""
    global _alarm_problem
    if problem != _alarm_problem and problem is not None:
        logger.warning("%s", problem)
    _alarm_problem = problem


async def _keep_alarm(want: datetime) -> None:
    """Keep the RTC's alarm at the next wake time ``want``."""
    global _armed
    rtc = await asyncio.to_thread(read_rtc)
    if not rtc.available:
        _armed = None
        _report_alarm(f"The RTC cannot wake the host: {rtc.reason}")
        return
    if rtc.alarm == want:
        _armed = want
        _report_alarm(None)
        return
    error = await asyncio.to_thread(arm_rtc, want)
    if error is not None:
        _armed = None
        _report_alarm(error)
        return
    _armed = want
    _report_alarm(None)
    logger.info("The RTC's wake alarm is set for %s", utc_text(want))


# ==================== What keeps the host from idling ====================


def _host_command(cmd: list[str], what: str) -> str:
    """The output of ``cmd`` on the host; _Unreadable naming ``what`` when it fails. Blocking."""
    try:
        result = host.run_on_host(cmd, timeout=HOST_TIMEOUT, errors="replace")
    except (OSError, subprocess.SubprocessError) as exc:
        raise _Unreadable(f"Could not read {what}: {exc}") from None
    if not result.ok:
        raise _Unreadable(f"Could not read {what}: {_why(result)}")
    return result.stdout


def _session_class(path: str) -> str | None:
    """The class of logind's session at ``path``; None when it cannot be read."""
    cmd = ["busctl", "--json=short", "get-property", "org.freedesktop.login1", path,
           "org.freedesktop.login1.Session", "Class"]
    try:
        data = json.loads(_host_command(cmd, "a session's class"))["data"]
    except (_Unreadable, ValueError, KeyError, TypeError):
        return None
    return data if isinstance(data, str) else None


def _login_sessions() -> list[Finding]:
    """logind's sessions on the host, without the per-user service managers. Blocking."""
    output = _host_command(LIST_SESSIONS, "the login sessions")
    try:
        sessions = json.loads(output)["data"][0]
        if not isinstance(sessions, list):
            raise ValueError("not a list")
    except (ValueError, KeyError, TypeError, IndexError):
        raise _Unreadable(f"Could not read the login sessions: busctl answered {output.strip()[:100]!r}") from None
    found = []
    for session in sessions:
        user = session[2] if isinstance(session, list) and len(session) >= 5 else None
        path = session[4] if isinstance(session, list) and len(session) >= 5 else None
        # A session whose class cannot be read counts: the host stays on.
        if isinstance(path, str) and _SESSION_PATH.fullmatch(path) and _session_class(path) in _NOT_LOGGED_IN:
            continue
        found.append(Finding(kind="login_session", subject=str(user) if user is not None else None))
    return found


def _peer_address(peer: str) -> str:
    """The address of ss's "address:port", without brackets, an IPv4-mapped prefix or a scope."""
    address = peer.rpartition(":")[0].partition("%")[0].strip("[]")
    return address.removeprefix("::ffff:")


def _connections() -> list[Finding]:
    """The SMB and NFS clients with an established connection to the host. Blocking."""
    smb: set[str] = set()
    nfs: set[str] = set()
    for line in _host_command(CONNECTIONS, "the connections").splitlines():
        parts = line.split()
        if parts and parts[0].upper().startswith("ESTAB"):
            parts = parts[1:]
        if len(parts) < 4:
            continue
        port = parts[2].rpartition(":")[2]
        if port in SMB_PORTS:
            smb.add(_peer_address(parts[3]))
        elif port in NFS_PORTS:
            nfs.add(_peer_address(parts[3]))
    return ([Finding(kind="smb_client", subject=a) for a in sorted(smb)]
            + [Finding(kind="nfs_client", subject=a) for a in sorted(nfs)])


def _processes() -> list[tuple[str, list[str]]]:
    """The name and command line of every process of the host (``pid: host``). Blocking."""
    found = []
    for process in psutil.process_iter(["name", "cmdline"]):
        info = process.info
        found.append((info.get("name") or "", list(info.get("cmdline") or [])))
    return found


_BTRFS_JOBS: dict[tuple[str, str], FindingKind] = {("scrub", "start"): "scrub", ("scrub", "resume"): "scrub",
               ("balance", "start"): "balance", ("balance", "resume"): "balance",
               ("replace", "start"): "disk_replace", ("device", "remove"): "disk_replace",
               ("device", "delete"): "disk_replace"}
# The kernel's threads of a balance resumed at a mount and of a replace.
_BTRFS_THREADS: dict[str, FindingKind] = {"btrfs-balance": "balance", "btrfs-devrepl": "disk_replace"}
# The jobs of apps.install_jobs.
_APP_JOBS: dict[str, FindingKind] = {"install": "app_install", "update": "app_update", "rollback": "app_rollback"}


def _btrfs_command(cmdline: list[str]) -> Finding | None:
    """What a btrfs command line runs, when it is a job: after btrfs's own options, a subcommand of
    _BTRFS_JOBS, or ``balance <path>``; its last argument names the pool when it is a path."""
    args = cmdline[1:]
    while args and args[0].startswith("-"):
        args = args[2:] if args[0] == "--format" else args[1:]
    if len(args) < 2:
        return None
    kind = _BTRFS_JOBS.get((args[0], args[1]))
    if kind is None and args[0] == "balance" and args[1].startswith("/"):
        kind = "balance"
    if kind is None:
        return None
    return Finding(kind=kind, subject=cmdline[-1] if cmdline[-1].startswith("/") else None)


def _btrfs_jobs() -> list[Finding]:
    """The scrubs, balances, replaces and device removals that run on the host, also those started
    outside UNOS: a btrfs process (_btrfs_command), or a thread of the kernel of _BTRFS_THREADS. Blocking."""
    found: list[Finding] = []
    try:
        processes = _processes()
    except Exception as exc:
        return [Finding(kind="unreadable", detail=f"Could not read the processes: {exc}")]
    for name, cmdline in processes:
        if name in _BTRFS_THREADS:
            finding: Finding | None = Finding(kind=_BTRFS_THREADS[name])
        elif name == "btrfs":
            finding = _btrfs_command(cmdline)
        else:
            continue
        if finding is not None and finding not in found:
            found.append(finding)
    return found


def _host_findings() -> list[Finding]:
    """The login sessions, the SMB and NFS clients, and the scrubs and balances; what cannot be read
    counts too. Blocking."""
    found: list[Finding] = []
    for read in (_login_sessions, _connections):
        try:
            found += read()
        except _Unreadable as exc:
            found.append(Finding(kind="unreadable", detail=str(exc)))
    return found + _btrfs_jobs()


def _jobs(now: datetime, zone: tzinfo) -> list[Finding]:
    """The jobs that run in the supervisor, and the scheduled tasks due within DUE_AHEAD. On the event loop."""
    scheduler = _module("..scheduler.router")
    found = [Finding(kind="task", subject=name) for name in scheduler.running_task_names()]
    try:
        # Also a task of the minute that began: the scheduler's pass of this minute may not have started it yet.
        due = scheduler.due_tasks(scheduler.load_tasks(), now - timedelta(minutes=1), now + DUE_AHEAD, zone)
    except Exception as exc:
        found.append(Finding(kind="unreadable", detail=f"Could not read the scheduled tasks: {exc}"))
    else:
        found += [Finding(kind="task_due", subject=task.name) for task in due]
    backup = _module("..backup.router")
    if backup.backup_running():
        found.append(Finding(kind="backup"))
    if backup.restore_running():
        found.append(Finding(kind="restore"))
    # An install, an update or a rollback of an app (ADR-0408), one job of install_jobs at a time.
    found += [Finding(kind=_APP_JOBS[job.kind], subject=job.name)
              for job in _module("..apps.install_jobs").current() if job.state == "running"]
    activity = _module("..offsite.service")._activity
    if activity is not None:
        found.append(Finding(kind="offsite", subject=activity.kind))
    repair = _module("..shares.repair_router").current_job()
    if repair is not None and repair.state == "running":
        found.append(Finding(kind="share_repair", subject=repair.share))
    # An update or a rollback of UNOS that this supervisor runs (System > Updates).
    updater = _module("..updates.updater")._updater
    if updater is not None and updater._progress.status.value not in ("idle", "completed", "failed"):
        found.append(Finding(kind="update", subject=updater._progress.status.value))
    return found


def _disk_counters() -> dict[str, int]:
    """The bytes read and written since boot by each disk of /proc/diskstats, without the boot SD card
    or eMMC, partitions and virtual devices (storage.disks.HIDDEN_DISK_PREFIXES)."""
    events = _module("..websocket.events")
    disks = _module("..storage.disks")
    physical = events.physical_disk_counters(psutil.disk_io_counters(perdisk=True) or {})
    return {name: read + written for name, (read, written) in physical.items() if not disks.is_hidden_disk(name)}


def _sample_disks(mono: float) -> None:
    """Compare the disk counters with the last pass's: a disk that moved more than DISK_ACTIVE_BYTES a
    minute is in use. The first pass after a start has nothing to compare with."""
    global _disk_sample, _disk_found
    try:
        counters = _disk_counters()
    except Exception as exc:
        _disk_sample = None
        _disk_found = [Finding(kind="unreadable", detail=f"Could not read the disk counters: {exc}")]
        return
    if _disk_sample is None:
        _disk_found = None
    else:
        then, before = _disk_sample
        minutes = max(mono - then, 1.0) / 60
        _disk_found = [
            Finding(kind="disk_activity", subject=name, amount=moved)
            for name, total in sorted(counters.items())
            if (moved := max(0, total - before.get(name, total))) / minutes > DISK_ACTIVE_BYTES
        ]
    _disk_sample = (mono, counters)


async def evaluate(now: datetime, zone: tzinfo) -> list[Finding]:
    """Why the host is not idle now; an empty list when it is."""
    found: list[Finding] = []
    sockets = _module("..websocket.events").signed_in_sockets()
    if sockets:
        found.append(Finding(kind="web_session", amount=sockets))
    found += await asyncio.to_thread(_host_findings)
    found += _jobs(now, zone)
    found += _disk_found if _disk_found is not None else [Finding(kind="unreadable", detail=NOT_MEASURED)]
    return found


def _describe(finding: Finding) -> str:
    """A finding in an English announcement."""
    subject = finding.subject or ""
    return {
        "web_session": f"{finding.amount} open page(s) of the web interface",
        "login_session": f"a login session of {subject}",
        "smb_client": f"the SMB client {subject}",
        "nfs_client": f"the NFS client {subject}",
        "disk_activity": f"activity on the disk {subject}",
        "task": f"the running task {subject}",
        "task_due": f"the task {subject}, due now",
        "backup": "a backup",
        "restore": "a restore",
        "scrub": f"a scrub of {subject}" if subject else "a scrub",
        "balance": f"a balance of {subject}" if subject else "a balance",
        "disk_replace": f"a disk replaced or removed in {subject}" if subject else "a disk replaced or removed",
        "app_install": f"the install of {subject}",
        "app_update": f"the update of {subject}",
        "app_rollback": f"the rollback of {subject}",
        "offsite": "the work of the off-device copy",
        "share_repair": f"a permissions repair of the share {subject}",
        "update": "an update of UNOS",
        "wake_alarm": f"the RTC's wake alarm ({finding.detail})",
        "unreadable": finding.detail or "something that could not be read",
    }[finding.kind]


# ==================== The shutdowns ====================


def _report_issue(problem: str | None) -> None:
    global _issue_problem
    if problem != _issue_problem and problem is not None:
        logger.warning("%s", problem)
    _issue_problem = problem


async def _audit(details: str) -> None:
    """The audit entry of a shutdown that the power settings issued (ADR-0351); never raises."""
    try:
        await log_action(AuditAction.SYSTEM_SHUTDOWN, username="system", resource_type="host", details=details,
                         severity=AuditSeverity.WARNING)
    except Exception as exc:
        logger.warning("Could not write the audit entry %r: %s", details, exc)


async def _shut_down(cmd: list[str], details: str) -> Literal["issued", "pending", "failed"]:
    """Issue ``cmd`` on the host unless logind has a reboot or shutdown pending."""
    global _last_issue_at
    power = _power()
    try:
        pending = await asyncio.to_thread(power._read_pending)
    except HTTPException as exc:
        _report_issue(f"No shutdown was issued: {exc.detail}")
        return "failed"
    if pending.action is not None:
        logger.info("%s: not issued, since a %s is pending on the host", details, pending.action)
        return "pending"
    result = await asyncio.to_thread(power._run, cmd)
    if not result.ok:
        _report_issue(f"Could not issue the shutdown of the power settings: {power._why(result)}")
        return "failed"
    _report_issue(None)
    _last_issue_at = _utc_now()
    logger.info("%s: the host shuts down in 1 minute", details)
    await _audit(details)
    return "issued"


def _finish(planned: _Planned, issued: bool) -> None:
    global _planned, _finished
    _finished = (planned.key, planned.due, issued)
    _planned = None


def _shutdown_problems(planned: _Planned, zone: tzinfo) -> dict[str, problems.Problem]:
    if planned.postponed and planned.held_by:
        at = _on_the_clock(planned.due, zone)
        problem = problems.Problem(
            key=f"shutdown_postponed:{planned.due.isoformat()}", severity="warning", kind="shutdown_postponed",
            subject=at, title=f"The shutdown of {planned.due.astimezone(zone):%H:%M} waits",
            detail=(f"The scheduled shutdown of {at} waits for: {'; '.join(_describe(f) for f in planned.held_by)}. "
                    f"It is announced again when they end, and dropped for the day {POSTPONE_LIMIT.seconds // 3600} "
                    "hours after its time."))
    elif planned.announced is not None:
        at = _on_the_clock(planned.expected, zone)
        again = f":{planned.again}" if planned.again else ""
        problem = problems.Problem(
            key=f"shutdown_scheduled:{planned.due.isoformat()}{again}", severity="warning", kind="shutdown_scheduled",
            subject=at, title=f"The host shuts down at {planned.expected.astimezone(zone):%H:%M}",
            detail=(f"The power settings shut the host down at {at}. Running jobs postpone it. To keep the host "
                    "running, turn the daily shutdown off in System > Power before then, or cancel the pending "
                    "shutdown there in its last minute."))
    else:
        return {}
    return {problem.key: problem}


def _announce(planned: _Planned, now: datetime, mono: float) -> None:
    planned.announced, planned.announced_at = mono, now


def _keep_shown(found: dict[str, problems.Problem], now: datetime) -> None:
    """Show the announcement of an issued shutdown until the host goes down, a minute later."""
    global _going_down
    _going_down = (now + DECIDE_AHEAD + timedelta(seconds=SLACK_SECONDS), found)


async def _holds(config: PowerConfig, now: datetime, zone: tzinfo, wake: datetime | None) -> list[Finding]:
    """What holds a shutdown: running jobs, tasks due, and a wake alarm the RTC does not hold."""
    found = _jobs(now, zone) + await asyncio.to_thread(_btrfs_jobs)
    if config.auto_wakeup and (_armed is None or _armed != wake):
        found.append(Finding(kind="wake_alarm", detail=_alarm_problem or "the RTC does not hold the next wake time"))
    return found


def _wake_too_close(now: datetime, wake: datetime | None) -> bool:
    return wake is not None and wake - (now + DECIDE_AHEAD) < MIN_OFF


async def _follow_shutdown(config: PowerConfig, zone: tzinfo, now: datetime, mono: float,
                           wake: datetime | None) -> dict[str, problems.Problem]:
    """Announce, postpone or issue the scheduled shutdown."""
    global _planned
    if not config.auto_shutdown:
        _planned = None
        return {}
    key = (config.shutdown_hour, config.shutdown_minute, zone_name(zone))
    if _planned is None or _planned.key != key:
        after = now
        if _finished is not None and _finished[0] == key and _finished[1] > after:
            after = _finished[1]
        _planned = _Planned(key, next_instant(config.shutdown_hour, config.shutdown_minute, zone, after))
    planned = _planned
    decide_at = planned.due - DECIDE_AHEAD
    if planned.announced is None and now >= decide_at - WARNING:
        _announce(planned, now, mono)
    if now < decide_at:
        return _shutdown_problems(planned, zone)
    at = _on_the_clock(planned.due, zone)
    if now > planned.due + POSTPONE_LIMIT:
        logger.info("The scheduled shutdown of %s is dropped for today: it waited %d hours", at,
                    POSTPONE_LIMIT.seconds // 3600)
        _finish(planned, False)
        return {}
    if config.auto_wakeup and next_instant(config.wakeup_hour, config.wakeup_minute, zone, planned.due) <= now:
        logger.info("The scheduled shutdown of %s is dropped for today: the wake time passed while it waited", at)
        _finish(planned, False)
        return {}
    if wake is not None and _wake_too_close(now, wake):
        logger.info("The scheduled shutdown of %s is skipped: the host wakes at %s", at, _on_the_clock(wake, zone))
        _finish(planned, False)
        return {}
    holds = await _holds(config, now, zone, wake)
    planned.held_by = holds
    if holds:
        if not planned.postponed:
            logger.info("The scheduled shutdown of %s waits for: %s", at, "; ".join(_describe(f) for f in holds))
        planned.postponed = True
        return _shutdown_problems(planned, zone)
    if planned.postponed:
        # What held it ended: announced again, it comes WARNING later unless something holds it again.
        planned.postponed = False
        planned.again += 1
        _announce(planned, now, mono)
        logger.info("The scheduled shutdown of %s no longer waits: it comes at %s", at,
                    _on_the_clock(planned.expected, zone))
        return _shutdown_problems(planned, zone)
    assert planned.announced is not None  # announced once its decision time came
    if mono - planned.announced < WARNING.total_seconds() - SLACK_SECONDS:
        return _shutdown_problems(planned, zone)
    announced = _shutdown_problems(planned, zone)
    outcome = await _shut_down(SCHEDULED_SHUTDOWN, f"Scheduled shutdown of {at} (the power settings)")
    if outcome == "failed":
        return announced
    _finish(planned, outcome == "issued")
    if outcome == "issued":
        _keep_shown(announced, now)
    return {}


def _wake_passed(config: PowerConfig, zone: tzinfo, now: datetime) -> bool:
    """Whether a wake time passed since the last pass."""
    if not config.auto_wakeup or _last_pass is None or _last_pass >= now:
        return False
    return next_instant(config.wakeup_hour, config.wakeup_minute, zone, _last_pass) <= now


async def _follow_idle(config: PowerConfig, zone: tzinfo, now: datetime, mono: float,
                       wake: datetime | None) -> dict[str, problems.Problem]:
    """Count the idle time, announce and issue the idle shutdown."""
    global _idle_since, _idle_announced, _idle_withdrawn, _idle_problem
    if not config.idle_shutdown:
        _idle_since = _idle_announced = _idle_withdrawn = None
        return {}
    found = await evaluate(now, zone)
    if found or _wake_passed(config, zone, now):
        if found and _idle_announced is not None:
            _idle_withdrawn = mono
        _idle_since = _idle_announced = None
        return {}
    if _idle_since is None:
        _idle_since = (mono, now)
    timeout = config.idle_timeout_minutes * 60
    idle_for = mono - _idle_since[0]
    if idle_for < timeout - WARNING.total_seconds():
        return {}
    if _idle_announced is None or _idle_problem is None:
        if _idle_withdrawn is not None and mono - _idle_withdrawn < IDLE_REANNOUNCE_SECONDS:
            return {}
        _idle_announced = mono
        # The host goes down a minute after the pass that issues the shutdown, WARNING from now.
        at = _on_the_clock(now + timedelta(seconds=max(timeout - idle_for, WARNING.total_seconds())) + DECIDE_AHEAD,
                           zone)
        _idle_problem = problems.Problem(
            key=f"shutdown_idle:{_idle_since[1].isoformat()}", severity="warning", kind="shutdown_idle", subject=at,
            title="The host shuts down when idle",
            detail=(f"The host has been idle for {int(idle_for // 60)} minutes: the power settings shut it down at "
                    f"about {at} unless it is used before."))
    announced = {_idle_problem.key: _idle_problem}
    if idle_for < timeout or mono - _idle_announced < WARNING.total_seconds() - SLACK_SECONDS:
        return announced
    if _wake_too_close(now, wake) or (config.auto_wakeup and _armed != wake):
        return announced
    outcome = await _shut_down(IDLE_SHUTDOWN, f"Idle shutdown after {config.idle_timeout_minutes} idle minutes "
                                              "(the power settings)")
    if outcome == "failed":
        return announced
    if outcome == "issued":
        _keep_shown(announced, now)
    _idle_since = _idle_announced = None
    return {}


# ==================== The loop and the status ====================


async def run_pass() -> None:
    """One pass of the loop."""
    global _armed, _last_pass, _last_mono, _idle_since, _idle_announced, _going_down
    if host.runs_locally():
        return
    now, mono = _utc_now(), _monotonic()
    if _last_pass is not None and _last_mono is not None:
        moved = (now - _last_pass).total_seconds() - (mono - _last_mono)
        if abs(moved) > CLOCK_JUMP_SECONDS:
            logger.info("The system clock moved by %d minutes: the power schedule starts over from the new time",
                        round(moved / 60))
            if _planned is not None:
                _finish(_planned, False)
            _idle_since = _idle_announced = None
    _sample_disks(mono)
    config = _power().load_config()
    found: dict[str, problems.Problem] = {}
    if config.acts:
        zone = await asyncio.to_thread(_zone)
        wake = None
        if config.auto_wakeup:
            wake = next_instant(config.wakeup_hour, config.wakeup_minute, zone, now)
            await _keep_alarm(wake)
        else:
            _armed = None
            _report_alarm(None)
        found.update(await _follow_shutdown(config, zone, now, mono, wake))
        found.update(await _follow_idle(config, zone, now, mono, wake))
    else:
        # Both are off: they forget what they followed, and read nothing.
        await _follow_shutdown(config, UTC, now, mono, None)
        await _follow_idle(config, UTC, now, mono, None)
    if _going_down is not None:
        if now < _going_down[0]:
            found.update(_going_down[1])
        else:
            _going_down = None
    _last_pass, _last_mono = now, mono
    await problems.update("power", found)


async def power_schedule_loop():
    """A pass one second after each minute begins, while the supervisor runs (ADR-0407)."""
    seconds_to_next_pass = _module("..scheduler.router")._seconds_to_next_pass
    while _running:
        await _sleep(seconds_to_next_pass(_utc_now()))
        try:
            await run_pass()
        except Exception:
            logger.exception("The power schedule could not be followed")


async def start_power_schedule() -> None:
    global _running, _loop_task
    _reset()
    _running = True
    _loop_task = asyncio.create_task(power_schedule_loop(), name="unos-power-schedule")


async def stop_power_schedule() -> None:
    global _running, _loop_task
    _running = False
    if _loop_task is not None:
        _loop_task.cancel()
        await asyncio.gather(_loop_task, return_exceptions=True)
        _loop_task = None


def _idle_shutdown_at(config: PowerConfig, now: datetime) -> datetime | None:
    """When the idle host goes down at the earliest: at the end of its idle time, and its announcement
    out for WARNING (after one that use withdrew, IDLE_REANNOUNCE_SECONDS later)."""
    if _idle_since is None:
        return None
    mono = _monotonic()
    announce = max(_idle_since[0] + config.idle_timeout_minutes * 60 - WARNING.total_seconds(), mono)
    if _idle_announced is not None:
        announce = _idle_announced
    elif _idle_withdrawn is not None:
        announce = max(announce, _idle_withdrawn + IDLE_REANNOUNCE_SECONDS)
    issue = max(_idle_since[0] + config.idle_timeout_minutes * 60, announce + WARNING.total_seconds())
    return now + timedelta(seconds=issue - mono) + DECIDE_AHEAD


async def status() -> PowerSchedule:
    """GET /power/schedule: read from the settings, the loop's RAM and the host; changes nothing."""
    config = _power().load_config()
    now = _utc_now()
    zone = await asyncio.to_thread(_zone)
    shutdown_time = _hhmm(config.shutdown_hour, config.shutdown_minute)
    if not config.auto_shutdown:
        shutdown = ShutdownSchedule(enabled=False, time=shutdown_time, state="off")
    else:
        key = (config.shutdown_hour, config.shutdown_minute, zone_name(zone))
        planned = _planned if _planned is not None and _planned.key == key else None
        if planned is not None:
            state: Literal["postponed", "announced", "planned"] = (
                "postponed" if planned.postponed and planned.held_by
                else "announced" if planned.announced is not None else "planned")
            shutdown = ShutdownSchedule(enabled=True, time=shutdown_time, next=planned.expected, state=state,
                                        held_by=planned.held_by if state == "postponed" else [])
        elif _finished is not None and _finished[0] == key and _finished[2] and _finished[1] > now:
            shutdown = ShutdownSchedule(enabled=True, time=shutdown_time, next=_finished[1], state="issued")
        else:
            after = _finished[1] if _finished is not None and _finished[0] == key and _finished[1] > now else now
            shutdown = ShutdownSchedule(enabled=True, time=shutdown_time, state="planned",
                                        next=next_instant(config.shutdown_hour, config.shutdown_minute, zone, after))
    rtc = await asyncio.to_thread(read_rtc)
    wake = WakeSchedule(
        enabled=config.auto_wakeup, time=_hhmm(config.wakeup_hour, config.wakeup_minute), rtc=rtc,
        next=(next_instant(config.wakeup_hour, config.wakeup_minute, zone, now)
              if config.auto_wakeup and rtc.available else None))
    found = await evaluate(now, zone)
    since = _idle_since[1] if config.idle_shutdown and _idle_since is not None and not found else None
    idle = IdleState(
        enabled=config.idle_shutdown, minutes=config.idle_timeout_minutes, idle=not found, since=since,
        shutdown_at=_idle_shutdown_at(config, now) if since is not None else None, found=found)
    return PowerSchedule(timezone=zone_name(zone), shutdown=shutdown, wake=wake, idle=idle)
