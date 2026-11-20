"""The power settings act on the host: the scheduled shutdown, the wake by the RTC and the idle
shutdown (ADR-0407).

Each pass of ``power_schedule_loop`` runs here on a fake clock (``schedule._utc_now``,
``schedule._monotonic``) and a fake host behind ``src.host.run_on_host``: the host's time zone,
logind (its pending action and its sessions), ``shutdown``, ``ss`` and the RTC's sysfs files. A
command the fake does not know fails the test, and ``subprocess`` is blocked, so no test can shut
down the machine that runs it. The processes and the disk counters are fakes too.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import subprocess
from datetime import datetime, timedelta, UTC
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src import host as host_module
from src.auth.utils import verify_token
from src.host import HostCommandError, HostResult
from src.main import app

power = importlib.import_module("src.power.router")
schedule = importlib.import_module("src.power.schedule")
scheduler = importlib.import_module("src.scheduler.router")
health_router = importlib.import_module("src.health.router")
problems = importlib.import_module("src.health.problems")
audit_router = importlib.import_module("src.audit.router")
events = importlib.import_module("src.websocket.events")
backup_router = importlib.import_module("src.backup.router")
install_jobs = importlib.import_module("src.apps.install_jobs")
offsite = importlib.import_module("src.offsite.service")
repair_router = importlib.import_module("src.shares.repair_router")

API = "/api/v1/power"

SCHEDULED = ["shutdown", "-h", "+1", "UNOS: scheduled shutdown (power settings)"]
IDLE = ["shutdown", "-h", "+1", "UNOS: idle shutdown (power settings)"]
CANCEL = ["shutdown", "-c"]
PENDING = power._SCHEDULED_SHUTDOWN
READLINK = ["readlink", "/etc/localtime"]
SESSIONS = [
    "busctl", "--json=short", "call", "org.freedesktop.login1", "/org/freedesktop/login1",
    "org.freedesktop.login1.Manager", "ListSessions",
]
CONNECTIONS = ["ss", "-H", "-t", "-n", "state", "established"]
WAKEALARM = "/sys/class/rtc/rtc0/wakealarm"
MIB = 1024 * 1024

# A Wednesday in central European winter time (UTC+1): 23:00 in Berlin is 22:00 in UTC.
DAY = datetime(2026, 11, 18, tzinfo=UTC)


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


class Clock:
    """The supervisor's clocks: the time in UTC and the monotonic clock, which runs with it."""

    def __init__(self, now: datetime) -> None:
        self.now = now
        self.start = now
        # How far the system clock was set, which the monotonic clock does not follow.
        self.set_by = timedelta()

    def utc(self) -> datetime:
        return self.now

    def monotonic(self) -> float:
        return (self.now - self.start - self.set_by).total_seconds() + 1000.0

    def set_clock(self, by: timedelta) -> None:
        """NTP or a person sets the system clock: the monotonic clock goes on."""
        self.now += by
        self.set_by += by


class FakeHost:
    """The host behind ``src.host.run_on_host``."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.calls: list[list[str]] = []
        self.zone = "Europe/Berlin"
        self.scheduled: tuple[str, int] = ("", 0)
        # (session id, uid, user, object path, class)
        self.sessions: list[tuple[str, int, str, str, str]] = []
        # (local address:port, peer address:port) of established TCP connections
        self.connections: list[tuple[str, str]] = []
        self.rtc = "present"  # "none": no rtc0; "no-alarm": rtc0 without wakealarm
        self.rtc_off = 0  # seconds the RTC's clock is ahead of the system clock
        self.alarm: int | None = None
        self.rtc_keeps = True  # False: the RTC takes the write and keeps nothing
        self.fail: dict[tuple[str, ...], tuple[int, str]] = {}

    @property
    def changes(self) -> list[list[str]]:
        """The commands that change the host: shutdowns and writes to the RTC."""
        return [cmd for cmd in self.calls if cmd[0] == "shutdown" or (cmd[0] == "sh" and WAKEALARM in cmd)]

    @property
    def shutdowns(self) -> list[list[str]]:
        return [cmd for cmd in self.calls if cmd[0] == "shutdown"]

    def run(self, cmd, *, timeout=60, check=False, input=None, errors=None):
        cmd = list(cmd)
        self.calls.append(cmd)
        for prefix, (code, stderr) in self.fail.items():
            if tuple(cmd[: len(prefix)]) == prefix:
                result = HostResult(code, "", stderr)
                break
        else:
            result = self._dispatch(cmd)
        if check and not result.ok:
            raise HostCommandError(cmd, result.returncode, result.stderr)
        return result

    def _dispatch(self, cmd: list[str]) -> HostResult:
        now = self.clock.now.timestamp()
        if cmd == READLINK:
            return HostResult(0, f"/usr/share/zoneinfo/{self.zone}\n", "")
        if cmd == PENDING:
            return HostResult(0, json.dumps({"type": "(st)", "data": list(self.scheduled)}) + "\n", "")
        if cmd in (SCHEDULED, IDLE):
            self.scheduled = ("poweroff", int((now + 60) * 1_000_000))
            return HostResult(0, "", "")
        if cmd == CANCEL:
            self.scheduled = ("", 0)
            return HostResult(0, "", "")
        if cmd == SESSIONS:
            data = [[sid, uid, user, "", path] for sid, uid, user, path, _ in self.sessions]
            return HostResult(0, json.dumps({"type": "a(susso)", "data": [data]}) + "\n", "")
        if cmd[:4] == ["busctl", "--json=short", "get-property", "org.freedesktop.login1"] and cmd[5:] == [
            "org.freedesktop.login1.Session", "Class",
        ]:
            found = [cls for *_, path, cls in self.sessions if path == cmd[4]]
            if not found:
                return HostResult(1, "", "Failed to get property Class: Unknown object\n")
            return HostResult(0, json.dumps({"type": "s", "data": found[0]}) + "\n", "")
        if cmd == CONNECTIONS:
            lines = "".join(f"0      0      {local}      {peer}\n" for local, peer in self.connections)
            return HostResult(0, lines, "")
        if cmd[:2] == ["sh", "-c"] and cmd[3:] == ["sh", "/sys/class/rtc/rtc0"]:
            if self.rtc == "none":
                return HostResult(0, "none\n", "")
            if self.rtc == "no-alarm":
                return HostResult(0, "no-alarm\n", "")
            alarm = "" if self.alarm is None else str(self.alarm)
            return HostResult(0, f"since={int(now) + self.rtc_off}\nalarm={alarm}\n", "")
        if cmd[:2] == ["sh", "-c"] and len(cmd) == 6 and cmd[3:5] == ["sh", WAKEALARM]:
            if self.rtc != "present":
                return HostResult(2, "", f"sh: 1: cannot create {WAKEALARM}: Permission denied\n")
            value = int(cmd[5])
            if value == 0:
                self.alarm = None
            elif self.rtc_keeps:
                self.alarm = value
            return HostResult(0, "", "")
        raise AssertionError(f"the fake host does not know {cmd!r}")


def _forbidden(*args, **kwargs):
    raise AssertionError(f"a test tried to run a command outside host.run_on_host: {args!r}")


@pytest.fixture
def clock(monkeypatch):
    clock = Clock(utc("2026-11-18T21:00:30"))
    monkeypatch.setattr(schedule, "_utc_now", clock.utc)
    monkeypatch.setattr(schedule, "_monotonic", clock.monotonic)
    return clock


@pytest.fixture
def fake_host(monkeypatch, tmp_path, clock):
    fake = FakeHost(clock)
    monkeypatch.delenv("UNOS_HOST_EXEC", raising=False)
    monkeypatch.setattr(host_module, "run_on_host", fake.run)
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, _forbidden)
    monkeypatch.setattr(os, "system", _forbidden)
    return fake


@pytest.fixture
def data(tmp_path, monkeypatch):
    """The supervisor's files of the test, in a folder of its own: the power settings, the scheduled
    tasks, the audit log."""
    folder = tmp_path / "data"
    folder.mkdir()
    monkeypatch.setattr(power, "CONFIG_FILE", str(folder / "power_config.json"))
    monkeypatch.setattr(scheduler, "TASKS_FILE", str(folder / "scheduled_tasks.json"))
    monkeypatch.setattr(audit_router, "AUDIT_FILE", str(tmp_path / "audit.jsonl"))
    return folder


@pytest.fixture
def alerts(monkeypatch):
    sent = []

    async def deliver(event):
        sent.append(event)

    monkeypatch.setattr(health_router, "_deliver_alert", deliver)
    monkeypatch.setattr(problems, "_by_source", {})
    return sent


@pytest.fixture
def quiet(monkeypatch):
    """No process and no disk I/O: the processes and the disk counters of the host."""
    state = SimpleNamespace(processes=[], disks={"sda": 0, "nvme0n1": 0})
    monkeypatch.setattr(schedule, "_processes", lambda: list(state.processes))
    monkeypatch.setattr(schedule, "_disk_counters", lambda: dict(state.disks))
    return state


@pytest.fixture
def fresh(monkeypatch):
    """The loop's RAM as at the start of the supervisor."""
    schedule._reset()
    yield
    schedule._reset()


@pytest.fixture
def world(fake_host, data, alerts, quiet, fresh, clock):
    return SimpleNamespace(host=fake_host, data=data, alerts=alerts, quiet=quiet, clock=clock)


def configure(**settings) -> None:
    power.save_config(power.PowerConfig(**settings))


def run_pass(world, at: datetime | None = None) -> None:
    if at is not None:
        world.clock.now = at
    asyncio.run(schedule.run_pass())


def passes(world, start: datetime, end: datetime) -> None:
    """A pass one second after each minute begins, from the minute of START to that of END."""
    at = start.replace(second=1, microsecond=0)
    while at <= end.replace(second=59):
        run_pass(world, at)
        at += timedelta(minutes=1)


def audit_entries(world) -> list[dict]:
    path = Path(audit_router.AUDIT_FILE)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def power_problems() -> list:
    return list(problems.of_source("power").values())


@pytest.fixture
def client(fake_host, data):
    app.dependency_overrides[verify_token] = lambda: SimpleNamespace(
        id=1, username="admin", is_admin=True, is_active=True
    )
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(verify_token, None)


# ---------------------------------------------------------------------------
# Step 1: the scheduled shutdown
# ---------------------------------------------------------------------------


class TestScheduledShutdown:
    def test_the_host_shuts_down_at_the_set_time_after_an_announcement(self, world, caplog):
        configure(auto_shutdown=True, shutdown_hour=23, shutdown_minute=0)

        passes(world, utc("2026-11-18T21:40:00"), utc("2026-11-18T21:48:00"))
        assert world.host.shutdowns == []
        assert world.alerts == []

        run_pass(world, utc("2026-11-18T21:49:01"))  # 11 minutes before 23:00 in Berlin
        [announced] = power_problems()
        assert (announced.kind, announced.subject, announced.severity) == (
            "shutdown_scheduled", "23:00 (Europe/Berlin)", "warning")
        assert [event.title for event in world.alerts] == ["The host shuts down at 23:00"]

        with caplog.at_level(logging.INFO):
            passes(world, utc("2026-11-18T21:50:00"), utc("2026-11-18T21:58:00"))
        assert world.host.shutdowns == []
        assert len(world.alerts) == 1, "announced once"

        run_pass(world, utc("2026-11-18T21:59:01"))
        assert world.host.shutdowns == [SCHEDULED]
        assert world.host.scheduled[0] == "poweroff"
        [entry] = audit_entries(world)
        assert (entry["action"], entry["username"], entry["resource_type"], entry["severity"]) == (
            "system_shutdown", "system", "host", "warning")
        assert "23:00" in entry["details"]

        passes(world, utc("2026-11-18T22:00:00"), utc("2026-11-18T22:30:00"))
        assert world.host.shutdowns == [SCHEDULED], "issued once"
        assert power_problems() == []

    def test_nothing_shuts_down_while_the_settings_are_off(self, world):
        passes(world, utc("2026-11-18T21:00:00"), utc("2026-11-19T23:00:00"))  # no settings file
        configure(auto_shutdown=False, idle_shutdown=False, auto_wakeup=False)
        passes(world, utc("2026-11-19T23:00:00"), utc("2026-11-20T23:00:00"))

        assert world.host.changes == []
        assert world.alerts == []
        assert audit_entries(world) == []

    def test_a_damaged_settings_file_shuts_nothing_down(self, world):
        Path(power.CONFIG_FILE).write_text('{"auto_shutdown": tru')

        passes(world, utc("2026-11-18T21:40:00"), utc("2026-11-18T22:10:00"))

        assert world.host.changes == []

    def test_a_pending_reboot_is_left_alone(self, world):
        configure(auto_shutdown=True, shutdown_hour=23)
        world.host.scheduled = ("reboot", int(utc("2026-11-18T22:30:00").timestamp() * 1_000_000))

        passes(world, utc("2026-11-18T21:40:00"), utc("2026-11-18T22:10:00"))

        assert world.host.shutdowns == []
        assert world.host.scheduled[0] == "reboot"

    def test_a_cancelled_shutdown_is_not_issued_again_that_day(self, world):
        configure(auto_shutdown=True, shutdown_hour=23)
        passes(world, utc("2026-11-18T21:40:00"), utc("2026-11-18T21:59:00"))
        assert world.host.shutdowns == [SCHEDULED]

        world.host.scheduled = ("", 0)  # Cancel in the Power tab
        passes(world, utc("2026-11-18T22:00:00"), utc("2026-11-18T23:00:00"))
        assert world.host.shutdowns == [SCHEDULED]

        passes(world, utc("2026-11-19T21:40:00"), utc("2026-11-19T21:59:00"))
        assert world.host.shutdowns == [SCHEDULED, SCHEDULED], "the next day's"

    def test_a_time_saved_minutes_before_waits_for_its_announcement(self, world):
        passes(world, utc("2026-11-18T21:50:00"), utc("2026-11-18T21:55:00"))
        configure(auto_shutdown=True, shutdown_hour=23)

        passes(world, utc("2026-11-18T21:56:00"), utc("2026-11-18T22:05:00"))
        assert world.host.shutdowns == []
        assert [p.kind for p in power_problems()] == ["shutdown_scheduled"]

        run_pass(world, utc("2026-11-18T22:06:01"))  # 10 minutes after the announcement at 21:56:01
        assert world.host.shutdowns == [SCHEDULED]

    def test_a_supervisor_started_a_minute_before_announces_first(self, world):
        configure(auto_shutdown=True, shutdown_hour=23)

        run_pass(world, utc("2026-11-18T21:59:20"))  # the first pass after a start
        assert world.host.shutdowns == []
        assert len(world.alerts) == 1

        passes(world, utc("2026-11-18T22:00:00"), utc("2026-11-18T22:09:00"))
        assert world.host.shutdowns == [SCHEDULED]

    def test_a_supervisor_started_after_the_time_waits_for_the_next_day(self, world):
        configure(auto_shutdown=True, shutdown_hour=23)

        passes(world, utc("2026-11-18T22:00:30"), utc("2026-11-19T21:48:00"))
        assert world.host.shutdowns == []

        passes(world, utc("2026-11-19T21:49:00"), utc("2026-11-19T21:59:00"))
        assert world.host.shutdowns == [SCHEDULED]

    @pytest.mark.parametrize(
        "day,expected",
        [
            # 02:30 does not exist on 29 March 2026 in Berlin: the clock skips from 02:00 to 03:00.
            (datetime(2026, 3, 29, tzinfo=UTC), utc("2026-03-29T01:00:00")),
            # 02:30 is shown twice on 25 October 2026: the first time, in summer time.
            (datetime(2026, 10, 25, tzinfo=UTC), utc("2026-10-25T00:30:00")),
        ],
    )
    def test_the_changes_of_daylight_saving_time(self, world, day, expected):
        configure(auto_shutdown=True, shutdown_hour=2, shutdown_minute=30)

        passes(world, day - timedelta(hours=3), day + timedelta(hours=4))

        assert world.host.shutdowns == [SCHEDULED]
        assert [c for c in world.host.calls if c[0] == "shutdown"]
        issued = schedule._last_issue_at
        assert issued is not None and expected - timedelta(minutes=1) <= issued < expected

    def test_where_host_commands_run_locally_nothing_runs(self, world, monkeypatch):
        monkeypatch.setenv("UNOS_HOST_EXEC", "local")
        configure(auto_shutdown=True, shutdown_hour=23, auto_wakeup=True, wakeup_hour=7, idle_shutdown=True,
                  idle_timeout_minutes=20)

        passes(world, utc("2026-11-18T21:00:00"), utc("2026-11-18T23:00:00"))

        assert world.host.calls == []
        assert world.alerts == []


class TestWhatPostponesAScheduledShutdown:
    def _until_the_decision(self, world):
        configure(auto_shutdown=True, shutdown_hour=23)
        passes(world, utc("2026-11-18T21:40:00"), utc("2026-11-18T21:59:00"))

    def test_a_running_task_postpones_it_until_its_end(self, world):
        async def scenario():
            run = asyncio.get_running_loop().create_task(asyncio.sleep(3600), name=scheduler.RUN_PREFIX + "Nightly backup")
            scheduler._runs.add(run)
            try:
                configure(auto_shutdown=True, shutdown_hour=23)
                for minute in range(40, 60):
                    world.clock.now = utc(f"2026-11-18T21:{minute:02d}:01")
                    await schedule.run_pass()
                assert world.host.shutdowns == []
                [postponed] = [p for p in power_problems() if p.kind == "shutdown_postponed"]
                assert "Nightly backup" in postponed.detail
                state = await schedule.status()
                assert state.shutdown.state == "postponed"
                assert [(f.kind, f.subject) for f in state.shutdown.held_by] == [("task", "Nightly backup")]
                world.clock.now = utc("2026-11-18T22:20:01")
                await schedule.run_pass()
                assert world.host.shutdowns == []
            finally:
                run.cancel()
                scheduler._runs.discard(run)
            # The task ended: the shutdown is announced again, for 10 minutes later.
            for minute in range(21, 31):
                world.clock.now = utc(f"2026-11-18T22:{minute:02d}:01")
                await schedule.run_pass()
            assert world.host.shutdowns == []
            assert (await schedule.status()).shutdown.state == "announced"
            world.clock.now = utc("2026-11-18T22:31:01")
            await schedule.run_pass()

        asyncio.run(scenario())
        assert world.host.shutdowns == [SCHEDULED]
        titles = [event.title for event in world.alerts]
        assert titles == ["The host shuts down at 23:00", "The shutdown of 23:00 waits", "The host shuts down at 23:32"]
        assert [p.kind for p in power_problems()] == ["shutdown_scheduled"], "shown until the host goes down"
        run_pass(world, utc("2026-11-18T22:33:01"))  # still up a minute after its shutdown
        assert power_problems() == []

    @pytest.mark.parametrize("job", ["restore", "backup", "app_install", "offsite", "share_repair", "scrub", "balance",
                                     "balance_thread", "scrub_with_option", "replace", "device_remove",
                                     "replace_thread", "task_due", "task_due_this_minute", "update"])
    def test_each_running_job_holds_it(self, world, monkeypatch, job):
        if job == "restore":
            monkeypatch.setattr(backup_router, "_restore_running", True)
        elif job == "backup":
            monkeypatch.setattr(backup_router, "_backups_running", 1)
        elif job == "app_install":
            running = SimpleNamespace(state="running", name="Jellyfin", kind="install", started_at=world.clock.now)
            monkeypatch.setattr(install_jobs, "current", lambda: [running])
        elif job == "offsite":
            monkeypatch.setattr(offsite, "_activity", offsite.Activity("copy", world.clock.now))
        elif job == "share_repair":
            monkeypatch.setattr(repair_router, "_job", SimpleNamespace(state="running", share="media"))
        elif job == "scrub":
            world.quiet.processes = [("btrfs", ["btrfs", "scrub", "start", "-B", "/mnt/pools/tank"])]
        elif job == "balance":
            world.quiet.processes = [("btrfs", ["btrfs", "balance", "start", "-dusage=20", "-musage=20",
                                                "/mnt/pools/tank"])]
        elif job == "balance_thread":
            world.quiet.processes = [("btrfs-balance", [])]
        elif job == "scrub_with_option":
            world.quiet.processes = [("btrfs", ["btrfs", "-q", "scrub", "start", "/mnt/pools/tank"])]
        elif job == "replace":
            world.quiet.processes = [("btrfs", ["btrfs", "replace", "start", "2", "/dev/sdc", "/mnt/pools/tank"])]
        elif job == "device_remove":
            world.quiet.processes = [("btrfs", ["/usr/bin/btrfs", "device", "remove", "/dev/sdb", "/mnt/pools/tank"])]
        elif job == "replace_thread":
            world.quiet.processes = [("btrfs-devrepl", [])]
        elif job in ("task_due", "task_due_this_minute"):
            # 23:01, or 22:59: the minute of the pass that would issue the shutdown, whose task the
            # scheduler's own pass of that minute may not have started yet.
            hour, minute = (23, 1) if job == "task_due" else (22, 59)
            scheduler.save_tasks([scheduler.ScheduledTask(
                id="t1", name="Late snapshot", task_type="snapshot", frequency="daily", hour=hour, minute=minute,
                pool_name="tank")])
        elif job == "update":
            updater = importlib.import_module("src.updates.updater")
            running = SimpleNamespace(_progress=SimpleNamespace(status=SimpleNamespace(value="installing")))
            monkeypatch.setattr(updater, "_updater", running)

        self._until_the_decision(world)

        assert world.host.shutdowns == []
        [postponed] = [p for p in power_problems() if p.kind == "shutdown_postponed"]
        assert postponed.subject == "23:00 (Europe/Berlin)"

    @pytest.mark.parametrize("kind", ["install", "update", "rollback"])
    def test_an_app_job_is_named_by_its_kind(self, world, monkeypatch, kind):
        # An update or a rollback of an app (ADR-0408) runs as a job of install_jobs: it holds a
        # shutdown as an install does, and the plan names it for what it is.
        running = SimpleNamespace(state="running", name="Immich", kind=kind, started_at=world.clock.now)
        monkeypatch.setattr(install_jobs, "current", lambda: [running])

        found = [f for f in schedule._jobs(world.clock.now, UTC) if f.subject == "Immich"]

        assert [f.kind for f in found] == [f"app_{kind}"]
        assert schedule._describe(found[0]) == {
            "install": "the install of Immich", "update": "the update of Immich",
            "rollback": "the rollback of Immich"}[kind]

    def test_finished_btrfs_commands_and_others_hold_nothing(self, world):
        world.quiet.processes = [
            ("btrfs", ["btrfs", "scrub", "status", "/mnt/pools/tank"]),
            ("btrfs", ["btrfs", "balance", "status", "/mnt/pools/tank"]),
            ("btrfs-transaction", []),
            ("smbd", ["/usr/sbin/smbd", "--foreground"]),
        ]

        self._until_the_decision(world)

        assert world.host.shutdowns == [SCHEDULED]

    def test_a_shutdown_held_for_six_hours_is_dropped_for_that_day(self, world, monkeypatch):
        monkeypatch.setattr(backup_router, "_restore_running", True)
        configure(auto_shutdown=True, shutdown_hour=23)

        passes(world, utc("2026-11-18T21:40:00"), utc("2026-11-19T04:10:00"))
        assert world.host.shutdowns == []
        assert power_problems() == []

        monkeypatch.setattr(backup_router, "_restore_running", False)
        passes(world, utc("2026-11-19T04:11:00"), utc("2026-11-19T21:58:00"))
        assert world.host.shutdowns == []
        run_pass(world, utc("2026-11-19T21:59:01"))
        assert world.host.shutdowns == [SCHEDULED]

    def test_a_shutdown_postponed_past_the_wake_time_is_dropped(self, world, monkeypatch):
        configure(auto_shutdown=True, shutdown_hour=1, auto_wakeup=True, wakeup_hour=5)
        monkeypatch.setattr(backup_router, "_restore_running", True)
        passes(world, utc("2026-11-18T23:30:00"), utc("2026-11-19T04:29:00"))  # until 05:29 in Berlin
        assert world.host.shutdowns == []

        monkeypatch.setattr(backup_router, "_restore_running", False)
        passes(world, utc("2026-11-19T04:30:00"), utc("2026-11-19T06:00:00"))

        assert world.host.shutdowns == []
        assert power_problems() == []

    def test_a_clock_set_forward_starts_the_schedule_over(self, world):
        # A board without an RTC boots with the clock of its last shutdown, at 22:45, and NTP sets it.
        configure(auto_shutdown=True, shutdown_hour=23)
        passes(world, utc("2026-11-18T21:45:00"), utc("2026-11-18T21:52:00"))
        assert [p.kind for p in power_problems()] == ["shutdown_scheduled"]

        world.clock.set_clock(timedelta(hours=2, minutes=18))  # 00:10 UTC, two hours after the time
        passes(world, utc("2026-11-19T00:11:00"), utc("2026-11-19T21:58:00"))
        assert world.host.shutdowns == []

        run_pass(world, utc("2026-11-19T21:59:01"))
        assert world.host.shutdowns == [SCHEDULED]

    def test_a_wake_alarm_that_the_rtc_does_not_keep_holds_it(self, world):
        configure(auto_shutdown=True, shutdown_hour=23, auto_wakeup=True, wakeup_hour=7)
        world.host.rtc_keeps = False

        passes(world, utc("2026-11-18T21:40:00"), utc("2026-11-18T22:10:00"))

        assert world.host.shutdowns == []
        [postponed] = [p for p in power_problems() if p.kind == "shutdown_postponed"]
        assert "wake alarm" in postponed.detail

    def test_a_shutdown_close_to_the_wake_is_skipped(self, world):
        # Saved by hand: PUT refuses a wake less than 10 minutes after the shutdown.
        configure(auto_shutdown=True, shutdown_hour=23, auto_wakeup=True, wakeup_hour=23, wakeup_minute=5)

        passes(world, utc("2026-11-18T21:40:00"), utc("2026-11-18T22:30:00"))

        assert world.host.shutdowns == []


# ---------------------------------------------------------------------------
# Step 2: the wake time and the RTC
# ---------------------------------------------------------------------------


class TestWake:
    def test_turning_the_wake_on_arms_the_rtc_for_the_next_wake_time(self, client, world):
        response = client.put(f"{API}/config", json={"auto_wakeup": True, "wakeup_hour": 7, "wakeup_minute": 30})

        assert response.status_code == 200, response.text
        seven_thirty = int(utc("2026-11-19T06:30:00").timestamp())  # 07:30 in Berlin
        assert world.host.alarm == seven_thirty
        writes = [cmd for cmd in world.host.changes]
        assert writes[-1][-1] == str(seven_thirty)
        answer = client.get(f"{API}/schedule").json()
        assert answer["timezone"] == "Europe/Berlin"
        assert answer["wake"] == {
            "enabled": True, "time": "07:30", "next": "2026-11-19T06:30:00Z",
            "rtc": {"available": True, "reason": None, "alarm": "2026-11-19T06:30:00Z"},
        }

    @pytest.mark.parametrize(
        "rtc,offset,reason",
        [
            ("none", 0, "The host has no RTC: /sys/class/rtc/rtc0 does not exist"),
            ("no-alarm", 0, "The RTC rtc0 has no wake alarm: /sys/class/rtc/rtc0/wakealarm does not exist"),
            ("present", 3600, "The RTC's clock is 60 minutes off the system clock: it keeps local time or has lost "
                              "its time"),
        ],
    )
    def test_a_host_whose_rtc_cannot_wake_it_promises_no_wake(self, client, world, rtc, offset, reason):
        world.host.rtc, world.host.rtc_off = rtc, offset

        answer = client.get(f"{API}/schedule").json()
        assert answer["wake"]["rtc"] == {"available": False, "reason": reason, "alarm": None}

        response = client.put(f"{API}/config", json={"auto_wakeup": True, "wakeup_hour": 7})
        assert response.status_code == 409
        assert response.json()["detail"] == f"No wake can be promised: {reason}"
        assert world.host.changes == []
        assert not Path(power.CONFIG_FILE).exists()

    def test_an_rtc_that_keeps_no_alarm_is_an_error(self, client, world):
        world.host.rtc_keeps = False

        response = client.put(f"{API}/config", json={"auto_wakeup": True, "wakeup_hour": 7})

        assert response.status_code == 500
        assert response.json()["detail"] == (
            "The RTC did not keep the wake alarm for 2026-11-19T06:00:00Z: it holds none")
        assert not Path(power.CONFIG_FILE).exists()

    def test_the_loop_arms_the_alarm_again_after_it_fired(self, world):
        configure(auto_wakeup=True, wakeup_hour=7)
        passes(world, utc("2026-11-19T05:50:00"), utc("2026-11-19T05:55:00"))
        assert world.host.alarm == int(utc("2026-11-19T06:00:00").timestamp())
        writes = len(world.host.changes)

        passes(world, utc("2026-11-19T05:56:00"), utc("2026-11-19T05:59:00"))
        assert len(world.host.changes) == writes, "an alarm that holds the time is not written again"

        world.host.alarm = None  # it fired at 07:00
        run_pass(world, utc("2026-11-19T06:00:01"))
        assert world.host.alarm == int(utc("2026-11-20T06:00:00").timestamp())

    @pytest.mark.parametrize("rtc", ["none", "no-alarm"])
    def test_the_wake_can_be_turned_off_on_a_host_that_lost_its_rtc(self, client, world, rtc):
        assert client.put(f"{API}/config", json={"auto_wakeup": True, "wakeup_hour": 7}).status_code == 200
        world.host.rtc = rtc  # a kernel without the RTC's driver

        response = client.put(f"{API}/config", json={"auto_wakeup": False})

        assert response.status_code == 200, response.text
        assert power.load_config().auto_wakeup is False

    def test_an_alarm_for_settings_that_could_not_be_stored_is_cleared(self, client, world, monkeypatch):
        def full(config):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(power, "save_config", full)

        with pytest.raises(OSError):
            client.put(f"{API}/config", json={"auto_wakeup": True, "wakeup_hour": 7})

        assert world.host.alarm is None
        assert [cmd[-1] for cmd in world.host.changes] == [str(int(utc("2026-11-19T06:00:00").timestamp())), "0"]

    def test_turning_the_wake_off_clears_the_alarm_and_the_rtc_is_left_alone(self, client, world):
        assert client.put(f"{API}/config", json={"auto_wakeup": True, "wakeup_hour": 7}).status_code == 200
        assert world.host.alarm is not None

        assert client.put(f"{API}/config", json={"auto_wakeup": False}).status_code == 200
        assert world.host.alarm is None

        world.host.alarm = 12345  # set by hand
        writes = len(world.host.changes)
        passes(world, utc("2026-11-18T21:00:00"), utc("2026-11-19T08:00:00"))
        assert world.host.alarm == 12345
        assert len(world.host.changes) == writes

    def test_the_wake_follows_the_change_to_summer_time(self, world):
        configure(auto_wakeup=True, wakeup_hour=2, wakeup_minute=30)

        run_pass(world, utc("2026-03-28T22:00:01"))

        assert world.host.alarm == int(utc("2026-03-29T01:00:00").timestamp())  # 03:00 CEST

    @pytest.mark.parametrize("shutdown,wake", [((23, 0), (23, 5)), ((23, 55), (0, 4)), ((7, 0), (7, 0))])
    def test_a_wake_less_than_ten_minutes_after_the_shutdown_is_refused(self, client, world, shutdown, wake):
        response = client.put(f"{API}/config", json={
            "auto_shutdown": True, "shutdown_hour": shutdown[0], "shutdown_minute": shutdown[1],
            "auto_wakeup": True, "wakeup_hour": wake[0], "wakeup_minute": wake[1]})

        assert response.status_code == 400
        assert response.json()["detail"] == "The wake time must come at least 10 minutes after the shutdown time"
        assert world.host.changes == []


# ---------------------------------------------------------------------------
# Step 3: the idle shutdown
# ---------------------------------------------------------------------------


class TestIdleShutdown:
    def test_an_idle_host_shuts_down_after_the_idle_time(self, world):
        configure(idle_shutdown=True, idle_timeout_minutes=30)

        run_pass(world, utc("2026-11-18T10:00:01"))  # the first pass measures the disks
        passes(world, utc("2026-11-18T10:01:00"), utc("2026-11-18T10:20:00"))
        assert world.alerts == []

        run_pass(world, utc("2026-11-18T10:21:01"))  # idle since 10:01:01 for 20 minutes
        [announced] = power_problems()
        assert announced.kind == "shutdown_idle"
        assert world.alerts[0].title == "The host shuts down when idle"

        passes(world, utc("2026-11-18T10:22:00"), utc("2026-11-18T10:30:00"))
        assert world.host.shutdowns == []
        run_pass(world, utc("2026-11-18T10:31:01"))
        assert world.host.shutdowns == [IDLE]
        [entry] = audit_entries(world)
        assert (entry["action"], entry["username"]) == ("system_shutdown", "system")
        assert "30 idle minutes" in entry["details"]

    @pytest.mark.parametrize("finding", ["web_session", "login_session", "smb_client", "nfs_client", "disk_activity",
                                         "job"])
    def test_use_keeps_the_host_on(self, world, monkeypatch, finding):
        configure(idle_shutdown=True, idle_timeout_minutes=20)
        if finding == "web_session":
            monkeypatch.setattr(events, "_open_sockets", 1)
        elif finding == "login_session":
            world.host.sessions = [("4", 0, "root", "/org/freedesktop/login1/session/_34", "user")]
        elif finding == "smb_client":
            world.host.connections = [("192.168.1.5:445", "192.168.1.20:51234")]
        elif finding == "nfs_client":
            world.host.connections = [("[::ffff:192.168.1.5]:2049", "[::ffff:192.168.1.30]:740")]
        elif finding == "job":
            monkeypatch.setattr(backup_router, "_restore_running", True)

        for minute in range(0, 60):
            if finding == "disk_activity":
                world.quiet.disks["sda"] += 2 * MIB
            run_pass(world, utc("2026-11-18T10:00:01") + timedelta(minutes=minute))

        assert world.host.shutdowns == []
        assert power_problems() == []
        state = asyncio.run(schedule.status())
        assert state.idle.idle is False
        assert finding.split("_")[0] in {f.kind.split("_")[0] for f in state.idle.found} or (
            finding == "job" and [f.kind for f in state.idle.found] == ["restore"])

    def test_what_does_not_count(self, world):
        configure(idle_shutdown=True, idle_timeout_minutes=20)
        world.host.sessions = [("c1", 0, "root", "/org/freedesktop/login1/session/c1", "manager")]
        world.host.connections = [("192.168.1.5:443", "192.168.1.20:51234"), ("192.168.1.5:22", "192.168.1.20:5")]
        world.quiet.disks["mmcblk0"] = 0

        for minute in range(0, 40):
            world.quiet.disks["sda"] += 100 * 1024  # 100 KiB a minute
            run_pass(world, utc("2026-11-18T10:00:01") + timedelta(minutes=minute))

        assert world.host.shutdowns == [IDLE]

    def test_what_cannot_be_read_keeps_the_host_on(self, world):
        configure(idle_shutdown=True, idle_timeout_minutes=20)
        world.host.fail[("ss",)] = (1, "ss: not found")

        passes(world, utc("2026-11-18T10:00:00"), utc("2026-11-18T11:00:00"))

        assert world.host.shutdowns == []
        found = asyncio.run(schedule.status()).idle.found
        assert [(f.kind, f.detail) for f in found] == [("unreadable", "Could not read the connections: ss: not found")]

    def test_an_announcement_that_use_withdrew_comes_again_an_hour_later_at_the_earliest(self, world):
        configure(idle_shutdown=True, idle_timeout_minutes=20)
        passes(world, utc("2026-11-18T10:00:00"), utc("2026-11-18T10:11:00"))  # idle from 10:01:01
        assert len(world.alerts) == 1

        world.host.sessions = [("4", 0, "root", "/org/freedesktop/login1/session/_34", "user")]
        run_pass(world, utc("2026-11-18T10:12:01"))
        world.host.sessions = []
        passes(world, utc("2026-11-18T10:13:00"), utc("2026-11-18T11:11:00"))  # idle again from 10:13:01
        assert world.host.shutdowns == []
        assert len(world.alerts) == 1

        passes(world, utc("2026-11-18T11:12:00"), utc("2026-11-18T11:21:00"))
        assert len(world.alerts) == 2
        assert world.host.shutdowns == []
        run_pass(world, utc("2026-11-18T11:22:01"))
        assert world.host.shutdowns == [IDLE]

    def test_the_idle_time_starts_again_at_a_start_of_the_supervisor(self, world):
        configure(idle_shutdown=True, idle_timeout_minutes=30)
        passes(world, utc("2026-11-18T10:00:00"), utc("2026-11-18T10:25:00"))
        assert [p.kind for p in power_problems()] == ["shutdown_idle"]

        schedule._reset()  # a restart: the first pass after it has no disk counters to compare
        passes(world, utc("2026-11-18T10:26:00"), utc("2026-11-18T10:56:00"))
        assert world.host.shutdowns == []
        run_pass(world, utc("2026-11-18T10:57:01"))
        assert world.host.shutdowns == [IDLE]

    def test_a_wake_time_that_passes_starts_the_idle_time_again(self, world):
        configure(idle_shutdown=True, idle_timeout_minutes=20, auto_wakeup=True, wakeup_hour=7)
        passes(world, utc("2026-11-19T05:45:00"), utc("2026-11-19T06:05:00"))  # 06:45 to 07:05 in Berlin

        assert world.host.shutdowns == []
        passes(world, utc("2026-11-19T06:06:00"), utc("2026-11-19T06:20:00"))
        assert world.host.shutdowns == [], "idle again from the pass after the wake time, 07:01 in Berlin"
        run_pass(world, utc("2026-11-19T06:21:01"))
        assert world.host.shutdowns == [IDLE]

    def test_the_status_says_when_the_idle_host_shuts_down(self, client, world):
        configure(idle_shutdown=True, idle_timeout_minutes=30)
        passes(world, utc("2026-11-18T10:00:00"), utc("2026-11-18T10:05:00"))

        answer = client.get(f"{API}/schedule").json()

        assert answer["idle"] == {
            "enabled": True, "minutes": 30, "idle": True, "since": "2026-11-18T10:01:01Z",
            "shutdown_at": "2026-11-18T10:32:01Z", "found": [],
        }


# ---------------------------------------------------------------------------
# The status, the settings, and what a pass writes
# ---------------------------------------------------------------------------


class TestStatusAndSettings:
    def test_the_status_reads_the_host_and_changes_nothing(self, client, world):
        configure(auto_shutdown=True, shutdown_hour=23, auto_wakeup=True, wakeup_hour=7, idle_shutdown=True,
                  idle_timeout_minutes=1440)
        world.host.alarm = int(utc("2026-11-19T06:00:00").timestamp())
        world.host.sessions = [("4", 1000, "alice", "/org/freedesktop/login1/session/_34", "user")]
        world.host.connections = [("192.168.1.5:445", "192.168.1.20:51234")]

        response = client.get(f"{API}/schedule")

        assert response.status_code == 200, response.text
        answer = response.json()
        assert answer["shutdown"] == {"enabled": True, "time": "23:00", "next": "2026-11-18T22:00:00Z",
                                      "state": "planned", "held_by": []}
        assert answer["wake"]["next"] == "2026-11-19T06:00:00Z"
        assert answer["wake"]["rtc"]["alarm"] == "2026-11-19T06:00:00Z"
        found = {(f["kind"], f["subject"]) for f in answer["idle"]["found"]}
        assert {("login_session", "alice"), ("smb_client", "192.168.1.20")} <= found
        assert answer["idle"]["idle"] is False
        assert world.host.changes == []

    def test_the_status_without_settings(self, client, world, quiet):
        answer = client.get(f"{API}/schedule").json()

        assert answer["shutdown"] == {"enabled": False, "time": "23:00", "next": None, "state": "off", "held_by": []}
        assert answer["wake"]["enabled"] is False and answer["wake"]["next"] is None
        assert answer["idle"]["enabled"] is False and answer["idle"]["since"] is None

    def test_the_status_is_off_where_host_commands_run_locally(self, client, world, monkeypatch):
        monkeypatch.setenv("UNOS_HOST_EXEC", "local")

        response = client.get(f"{API}/schedule")

        assert response.status_code == 503
        assert response.json()["detail"] == power.POWER_OFF_DETAIL
        assert world.host.calls == []

    @pytest.mark.parametrize("setting", ["auto_shutdown", "auto_wakeup", "idle_shutdown"])
    def test_settings_that_act_are_refused_where_host_commands_run_locally(self, client, world, monkeypatch, setting):
        monkeypatch.setenv("UNOS_HOST_EXEC", "local")

        response = client.put(f"{API}/config", json={setting: True})

        assert response.status_code == 503
        assert response.json()["detail"] == power.POWER_OFF_DETAIL
        assert world.host.calls == []
        assert not Path(power.CONFIG_FILE).exists()

    def test_settings_that_act_on_nothing_are_kept_where_host_commands_run_locally(self, client, world, monkeypatch):
        monkeypatch.setenv("UNOS_HOST_EXEC", "local")

        response = client.put(f"{API}/config", json={"shutdown_hour": 22})

        assert response.status_code == 200, response.text
        assert power.load_config().shutdown_hour == 22

    @pytest.mark.parametrize("field,value", [("shutdown_hour", 24), ("shutdown_minute", 60), ("wakeup_hour", -1),
                                             ("wakeup_minute", 75), ("idle_timeout_minutes", 19),
                                             ("idle_timeout_minutes", 1441)])
    def test_times_out_of_range_are_refused(self, client, world, field, value):
        response = client.put(f"{API}/config", json={field: value})

        assert response.status_code == 422
        assert not Path(power.CONFIG_FILE).exists()

    def test_a_quiet_pass_writes_no_file_and_logs_nothing(self, world, caplog):
        configure(auto_shutdown=True, shutdown_hour=23, auto_wakeup=True, wakeup_hour=7, idle_shutdown=True,
                  idle_timeout_minutes=1440)
        run_pass(world, utc("2026-11-18T10:00:01"))
        world.host.sessions = [("4", 0, "root", "/org/freedesktop/login1/session/_34", "user")]
        before = {path: path.stat().st_mtime_ns for path in world.data.rglob("*")}
        caplog.clear()

        with caplog.at_level(logging.DEBUG, logger="src"):
            passes(world, utc("2026-11-18T10:01:00"), utc("2026-11-18T12:00:00"))

        assert {path: path.stat().st_mtime_ns for path in world.data.rglob("*")} == before
        assert [r for r in caplog.records if r.name.startswith("src.power")] == []

    def test_the_lifespan_loop_runs_passes_until_stopped(self, world, monkeypatch):
        calls = []

        async def one_pass():
            calls.append(1)

        async def no_wait(seconds):
            await asyncio.sleep(0)

        monkeypatch.setattr(schedule, "run_pass", one_pass)
        monkeypatch.setattr(schedule, "_sleep", no_wait)

        async def scenario():
            await schedule.start_power_schedule()
            while len(calls) < 3:
                await asyncio.sleep(0)
            await schedule.stop_power_schedule()

        asyncio.run(scenario())
        assert schedule._loop_task is None or schedule._loop_task.done()


class TestTheParts:
    def test_disks_of_the_boot_medium_and_virtual_devices_do_not_count(self, monkeypatch, tmp_path):
        counters = {name: SimpleNamespace(read_bytes=10, write_bytes=5)
                    for name in ("sda", "nvme0n1", "mmcblk0", "zram0", "loop0", "sda1")}
        monkeypatch.setattr(schedule.psutil, "disk_io_counters", lambda perdisk: counters)
        sys_block = tmp_path / "block"
        for name in ("sda", "nvme0n1", "mmcblk0"):
            (sys_block / name / "device").mkdir(parents=True)
        (sys_block / "zram0").mkdir()
        (sys_block / "loop0").mkdir()
        monkeypatch.setattr(events, "SYS_BLOCK", str(sys_block))

        assert schedule._disk_counters() == {"sda": 15, "nvme0n1": 15}

    def test_a_run_of_a_task_carries_its_name(self, monkeypatch):
        async def scenario():
            started = asyncio.Event()

            async def fake_run(task):
                started.set()
                await asyncio.sleep(3600)

            monkeypatch.setattr(scheduler, "run_task", fake_run)
            task = scheduler.ScheduledTask(id="t1", name="Nightly backup", task_type="snapshot", frequency="daily",
                                           pool_name="tank")
            run = scheduler.start_run(task)
            await started.wait()
            try:
                assert scheduler.running_task_names() == ["Nightly backup"]
            finally:
                run.cancel()
                await asyncio.gather(run, return_exceptions=True)
            assert scheduler.running_task_names() == []

        asyncio.run(scenario())

    def test_a_backup_made_by_hand_counts_while_it_runs(self, client, monkeypatch, tmp_path):
        seen = []

        def fake_create(config):
            seen.append(backup_router.backup_running())
            raise OSError("disk full")

        monkeypatch.setattr(backup_router, "_create_backup", fake_create)
        monkeypatch.setattr(audit_router, "AUDIT_FILE", str(tmp_path / "audit.jsonl"))

        response = client.post("/api/v1/backup/create", json={})

        assert response.status_code == 500
        assert seen == [True]
        assert backup_router.backup_running() is False

    @pytest.mark.parametrize("peer,address", [
        ("192.168.1.20:51234", "192.168.1.20"),
        ("[::ffff:192.168.1.30]:740", "192.168.1.30"),
        ("[fd12::5]:445", "fd12::5"),
        ("[fe80::2]%eth0:51234", "fe80::2"),
    ])
    def test_a_clients_address_as_ss_prints_it(self, peer, address):
        assert schedule._peer_address(peer) == address

    def test_open_signed_in_sockets_are_counted(self, monkeypatch):
        monkeypatch.setattr(events, "_open_sockets", 3)
        monkeypatch.setattr(events, "_sockets_before_auth", 1)

        assert events.signed_in_sockets() == 2


REPO = Path(__file__).resolve().parents[2]


class TestTheWebUIsFakes:
    """webui/tests/power.spec.ts answers the Power tab with route fakes and the health banner with a
    fake socket: with the server's texts, word for word (ADR-0189)."""

    def _spec(self) -> str:
        return (REPO / "webui" / "tests" / "power.spec.ts").read_text(encoding="utf-8")

    def test_the_answers_are_the_servers(self, client, world):
        world.host.rtc = "none"
        refused = client.put(f"{API}/config", json={"auto_wakeup": True}).json()["detail"]
        too_close = client.put(f"{API}/config", json={"auto_shutdown": True, "auto_wakeup": True, "wakeup_hour": 23,
                                                      "wakeup_minute": 5}).json()["detail"]
        world.host.fail[("ss",)] = (1, "ss: not found")
        unreadable = [f.detail for f in asyncio.run(schedule.status()).idle.found
                      if f.kind == "unreadable" and f.detail.startswith("Could not read the connections")]
        assert len(unreadable) == 1

        spec = self._spec()
        for text in (schedule.NO_RTC, refused, too_close, *unreadable,
                     "The RTC did not keep the wake alarm for 2026-11-19T06:00:00Z: it holds none"):
            assert f"'{text}'" in spec, text

    def test_the_announcements_are_the_servers(self, world, monkeypatch):
        configure(auto_shutdown=True, shutdown_hour=23)
        run_pass(world, utc("2026-11-18T21:49:01"))
        announced = power_problems()
        monkeypatch.setattr(backup_router, "_restore_running", True)
        run_pass(world, utc("2026-11-18T21:59:01"))
        postponed = power_problems()
        monkeypatch.setattr(backup_router, "_restore_running", False)
        configure(idle_shutdown=True, idle_timeout_minutes=30)
        schedule._reset()
        passes(world, utc("2026-11-18T10:00:00"), utc("2026-11-18T10:21:00"))
        idle = power_problems()

        spec = self._spec()
        for problem in announced + postponed + idle:
            assert f"kind: '{problem.kind}'" in spec, problem.kind
            assert f"subject: '{problem.subject}'" in spec, problem.subject
            assert f"title: '{problem.title}'" in spec, problem.title
            assert f"detail: '{problem.detail}'" in spec, problem.detail

    def test_the_health_banner_names_every_kind_of_the_power_problems(self):
        banner = (REPO / "webui" / "src" / "lib" / "components" / "ProblemsBanner.svelte").read_text(encoding="utf-8")
        for kind in ("shutdown_scheduled", "shutdown_postponed", "shutdown_idle"):
            assert f"{kind}: 'problems.kind.{kind}'" in banner, kind
