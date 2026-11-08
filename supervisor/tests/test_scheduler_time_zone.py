"""Schedules follow the time zone of the host, also across a change of daylight saving time (ADR-0266).

The scheduler loop runs here on a fake clock (run_scheduler): each of its sleeps moves the
clock on, once the runs it started have ended. The host is the fake host of
test_pool_persistence.py, whose /etc/localtime link names the time zone.
"""

import asyncio
import errno
import importlib
import json
import logging
import subprocess
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from src import host as host_module
from src.host import HostResult
from src.scheduler.schemas import ScheduledTask

from .test_scheduler_validation import _task, make_env

scheduler = importlib.import_module("src.scheduler.router")

UTC = timezone.utc
# As `timedatectl set-timezone` writes the link, and as Debian's tzdata does.
BERLIN = "../usr/share/zoneinfo/Europe/Berlin"
NEW_YORK = "/usr/share/zoneinfo/America/New_York"


@pytest.fixture
def env(tmp_path, monkeypatch):
    yield from make_env(tmp_path, monkeypatch)


class FakeClock:
    """The time the scheduler reads (``_utc_now``), moved on by its sleeps."""

    def __init__(self, now: datetime):
        self.now = now

    def __call__(self) -> datetime:
        return self.now


async def run_scheduler(monkeypatch, clock: FakeClock, until: datetime, *, late: dict | None = None) -> None:
    """Run scheduler_loop on ``clock`` until it reaches ``until``.

    Each sleep waits for the runs that were started, then moves the clock on by the
    seconds asked for; ``late`` adds time to the sleep that ends after the instant
    it names (a pass that comes late, a clock that is set forward).
    """
    late = dict(late or {})

    async def sleep(seconds: float) -> None:
        while scheduler._runs:
            await asyncio.wait(set(scheduler._runs))
        clock.now += timedelta(seconds=seconds)
        for at in sorted(late):
            if clock.now >= at:
                clock.now += late.pop(at)
        if clock.now >= until:
            scheduler._scheduler_running = False

    monkeypatch.setattr(scheduler, "_utc_now", clock)
    monkeypatch.setattr(scheduler, "_sleep", sleep)
    monkeypatch.setattr(scheduler, "_scheduler_running", True)
    await scheduler.scheduler_loop()
    while scheduler._runs:
        await asyncio.wait(set(scheduler._runs))


def started_runs(monkeypatch, clock: FakeClock) -> list[tuple[str, datetime]]:
    """Record the runs the loop starts, (task id, instant), instead of running them."""
    started: list[tuple[str, datetime]] = []

    async def run_task(task: ScheduledTask) -> None:
        started.append((task.id, clock.now))

    monkeypatch.setattr(scheduler, "run_task", run_task)
    return started


def _store(*tasks: dict) -> None:
    scheduler.save_tasks([ScheduledTask(**task) for task in tasks])


def _daily(task_id: str, hour: int, minute: int = 0, **overrides) -> dict:
    return _task(id=task_id, name=task_id, task_type="custom", target_pool=None, keep_count=None,
                 command="true", frequency="daily", hour=hour, minute=minute, **overrides)


def _local(started: list[tuple[str, datetime]], zone: str) -> list[tuple[str, str]]:
    """The runs as ``(task id, local time without seconds)``, in the order they started."""
    return [(task_id, at.astimezone(ZoneInfo(zone)).strftime("%Y-%m-%d %H:%M %Z")) for task_id, at in started]


def _host_not_asked(failure: str, cmd: list[str]) -> HostResult:
    """What run_on_host does with ``cmd`` when the host cannot be asked: ``failure`` names how."""
    if failure == "timeout":  # the host is slow to answer, as during heavy I/O on its SD card
        raise subprocess.TimeoutExpired(["nsenter", *cmd], 10)
    if failure == "fork":  # no process can be started, as under memory pressure
        raise OSError(errno.EAGAIN, "Resource temporarily unavailable")
    # nsenter cannot enter the host's namespaces: it reports why, and readlink does not run.
    return HostResult(1, "", "nsenter: reassociate to namespace 'ns/mnt' failed: Operation not permitted\n")


# --- the time zone of the host and the times of the API ------------------------------------


def test_next_run_is_the_next_time_of_the_schedule_on_the_clock_of_the_host(env, monkeypatch):
    """It was the container's naive local time (UTC), which the Scheduler tab showed as the browser's."""
    env.host.links["/etc/localtime"] = BERLIN
    monkeypatch.setattr(scheduler, "_utc_now", lambda: datetime(2026, 11, 7, 12, 0, tzinfo=UTC))

    created = env.client.post("/api/v1/scheduler/tasks", json=_daily("ignored", 3)).json()

    assert created["next_run"] == "2026-11-08T03:00:00+01:00"
    (listed,) = env.client.get("/api/v1/scheduler/tasks").json()
    assert listed["next_run"] == "2026-11-08T03:00:00+01:00"
    edited = env.client.put(f"/api/v1/scheduler/tasks/{created['id']}", json=_daily("ignored", 4, 30)).json()
    assert edited["next_run"] == "2026-11-08T04:30:00+01:00"
    assert ["readlink", "/etc/localtime"] in env.host.calls
    # Derived for each answer and never stored: a stored one would be stale once it passed.
    assert "next_run" not in json.loads((env.tmp / "tasks.json").read_text())[0]

    env.host.links["/etc/localtime"] = NEW_YORK
    (listed,) = env.client.get("/api/v1/scheduler/tasks").json()
    assert listed["next_run"] == "2026-11-08T04:30:00-05:00"


def test_the_scheduler_names_the_time_zone_that_its_schedules_follow(env):
    for link, name in [(BERLIN, "Europe/Berlin"), (NEW_YORK, "America/New_York"),
                       ("/usr/share/zoneinfo/Etc/UTC", "Etc/UTC"),
                       # The same zones in the folders of POSIX time and of time with leap seconds:
                       # their clock shows the civil time of the zone.
                       ("/usr/share/zoneinfo/posix/Europe/Berlin", "Europe/Berlin"),
                       ("../usr/share/zoneinfo/right/America/New_York", "America/New_York")]:
        env.host.links["/etc/localtime"] = link
        response = env.client.get("/api/v1/scheduler/timezone")
        assert response.status_code == 200, response.text
        assert response.json() == {"timezone": name}


@pytest.mark.parametrize(
    "link",
    [
        None,  # no /etc/localtime, or not a link: readlink fails
        "/usr/share/zoneinfo/Nowhere/Land",  # a zone the supervisor does not know
        "/etc/localtime.dpkg-new",  # a link outside the zone database
        "/usr/share/zoneinfo/../../../etc/passwd",
    ],
)
def test_a_time_zone_that_cannot_be_read_leaves_the_schedules_in_utc_and_is_reported_once(
    env, monkeypatch, caplog, link
):
    if link is None:
        del env.host.links["/etc/localtime"]
    else:
        env.host.links["/etc/localtime"] = link
    monkeypatch.setattr(scheduler, "_utc_now", lambda: datetime(2026, 11, 7, 12, 0, tzinfo=UTC))

    with caplog.at_level(logging.WARNING, logger="src.scheduler.router"):
        created = env.client.post("/api/v1/scheduler/tasks", json=_daily("ignored", 3)).json()
        zone = env.client.get("/api/v1/scheduler/timezone").json()
        listed = env.client.get("/api/v1/scheduler/tasks").json()

    assert created["next_run"] == listed[0]["next_run"] == "2026-11-08T03:00:00+00:00"
    assert zone == {"timezone": "UTC"}
    warnings = [r.getMessage() for r in caplog.records if "time zone" in r.getMessage()]
    assert len(warnings) == 1, warnings
    assert "the schedules follow UTC" in warnings[0]


def test_a_zone_that_cannot_be_read_for_a_moment_is_the_one_read_before_and_each_failure_is_logged(
    env, monkeypatch, caplog
):
    """The host could not be asked (here: a timeout): the schedules followed UTC until the next read,
    and the reason was logged once in the supervisor's life, so a later failure left no trace."""
    env.host.links["/etc/localtime"] = BERLIN
    answer = host_module.run_on_host  # the fake host
    failing = False

    def run_on_host(cmd, **kwargs):
        if failing and cmd[0] == "readlink":
            return _host_not_asked("timeout", cmd)
        return answer(cmd, **kwargs)

    monkeypatch.setattr(host_module, "run_on_host", run_on_host)

    def zone() -> str:
        response = env.client.get("/api/v1/scheduler/timezone")
        assert response.status_code == 200, response.text
        return response.json()["timezone"]

    with caplog.at_level(logging.WARNING, logger="src.scheduler.router"):
        failing = True
        assert zone() == "UTC"  # no zone read yet
        failing = False
        assert zone() == "Europe/Berlin"
        failing = True
        assert [zone(), zone()] == ["Europe/Berlin", "Europe/Berlin"]
        failing = False
        assert zone() == "Europe/Berlin"
        failing = True
        assert zone() == "Europe/Berlin"

    warnings = [r.getMessage() for r in caplog.records if "time zone" in r.getMessage()]
    assert len(warnings) == 3, warnings  # each failure once, not at each read of it
    assert "timed out" in warnings[0] and "the schedules follow UTC" in warnings[0]
    assert all("timed out" in w and "the schedules follow Europe/Berlin" in w for w in warnings[1:]), warnings


def test_the_times_of_a_run_carry_their_offset(env, monkeypatch):
    """started_at, finished_at and last_run were naive: a browser read them as its own local time."""
    env.host.links["/etc/localtime"] = BERLIN
    task = env.client.post("/api/v1/scheduler/tasks", json=_daily("ignored", 3)).json()

    env.client.post(f"/api/v1/scheduler/tasks/{task['id']}/run")

    (run,) = env.client.get(f"/api/v1/scheduler/runs/{task['id']}").json()
    started, finished = datetime.fromisoformat(run["started_at"]), datetime.fromisoformat(run["finished_at"])
    assert started.utcoffset() == finished.utcoffset() == timedelta(0)
    assert started <= finished <= datetime.now(UTC)
    (listed,) = env.client.get("/api/v1/scheduler/tasks").json()
    assert listed["last_run"] == run["started_at"]


# --- the scheduler loop ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "link, zone, start, until, expected",
    [
        # Central Europe: on 29 March 2026 the clock goes from 02:00 to 03:00, on 25 October
        # from 03:00 back to 02:00.
        (BERLIN, "Europe/Berlin", datetime(2026, 3, 27, 22, tzinfo=UTC), datetime(2026, 3, 30, 4, tzinfo=UTC),
         ["2026-03-28 03:00 CET", "2026-03-29 03:00 CEST", "2026-03-30 03:00 CEST"]),
        (BERLIN, "Europe/Berlin", datetime(2026, 10, 23, 22, tzinfo=UTC), datetime(2026, 10, 26, 4, tzinfo=UTC),
         ["2026-10-24 03:00 CEST", "2026-10-25 03:00 CET", "2026-10-26 03:00 CET"]),
        # The United States: 8 March and 1 November 2026, at 02:00.
        (NEW_YORK, "America/New_York", datetime(2026, 3, 7, 4, tzinfo=UTC), datetime(2026, 3, 9, 10, tzinfo=UTC),
         ["2026-03-07 03:00 EST", "2026-03-08 03:00 EDT", "2026-03-09 03:00 EDT"]),
        (NEW_YORK, "America/New_York", datetime(2026, 10, 31, 4, tzinfo=UTC), datetime(2026, 11, 2, 11, tzinfo=UTC),
         ["2026-10-31 03:00 EDT", "2026-11-01 03:00 EST", "2026-11-02 03:00 EST"]),
    ],
)
async def test_a_task_for_03_00_runs_at_03_00_local_time_across_a_change_of_daylight_saving_time(
    env, monkeypatch, link, zone, start, until, expected
):
    env.host.links["/etc/localtime"] = link
    _store(_daily("nightly", 3))
    clock = FakeClock(start)
    started = started_runs(monkeypatch, clock)

    await run_scheduler(monkeypatch, clock, until)

    assert _local(started, zone) == [("nightly", time) for time in expected]


async def test_a_time_that_the_clock_skips_runs_when_the_clock_passes_it(env, monkeypatch):
    """02:30 does not come on 29 March in central Europe: the task runs at 03:00, once.

    Running it an hour later instead (02:30 read with the offset of winter time), or not at
    all, would also be possible; at 03:00 it runs when its time has come.
    """
    env.host.links["/etc/localtime"] = BERLIN
    _store(_daily("at-02-30", 2, 30), _daily("at-02-00", 2), _daily("at-03-00", 3),
           _task(id="every-15", name="every-15", task_type="custom", target_pool=None, keep_count=None,
                 command="true", frequency="custom", cron_expression="*/15 * * * *"))
    monkeypatch.setattr(scheduler, "_utc_now", lambda: datetime(2026, 3, 28, 12, tzinfo=UTC))
    next_runs = {task["id"]: task["next_run"] for task in env.client.get("/api/v1/scheduler/tasks").json()}
    assert next_runs["at-02-30"] == next_runs["at-02-00"] == "2026-03-29T03:00:00+02:00"
    assert next_runs["at-03-00"] == "2026-03-29T03:00:00+02:00"
    clock = FakeClock(datetime(2026, 3, 29, 0, 0, tzinfo=UTC))  # 01:00 in Berlin
    started = started_runs(monkeypatch, clock)

    await run_scheduler(monkeypatch, clock, datetime(2026, 3, 29, 1, 31, tzinfo=UTC))

    assert _local(started, "Europe/Berlin") == [
        ("every-15", "2026-03-29 01:15 CET"),
        ("every-15", "2026-03-29 01:30 CET"),
        ("every-15", "2026-03-29 01:45 CET"),
        # 02:00 to 02:45 do not come: each task runs once when the clock passes them.
        ("at-02-30", "2026-03-29 03:00 CEST"),
        ("at-02-00", "2026-03-29 03:00 CEST"),
        ("at-03-00", "2026-03-29 03:00 CEST"),
        ("every-15", "2026-03-29 03:00 CEST"),
        ("every-15", "2026-03-29 03:15 CEST"),
        ("every-15", "2026-03-29 03:30 CEST"),
    ]


async def test_a_time_that_the_clock_shows_twice_runs_once(env, monkeypatch):
    """On 25 October the clock of central Europe shows 02:00 to 02:59 twice: a task runs the first
    time, and so does an hourly one, which then waits for 03:30."""
    env.host.links["/etc/localtime"] = BERLIN
    _store(_daily("at-02-30", 2, 30),
           _task(id="hourly", name="hourly", task_type="custom", target_pool=None, keep_count=None,
                 command="true", frequency="hourly", minute=30))
    clock = FakeClock(datetime(2026, 10, 24, 23, 0, tzinfo=UTC))  # 01:00 in Berlin, summer time
    started = started_runs(monkeypatch, clock)

    await run_scheduler(monkeypatch, clock, datetime(2026, 10, 25, 3, 0, tzinfo=UTC))

    assert _local(started, "Europe/Berlin") == [
        ("hourly", "2026-10-25 01:30 CEST"),
        ("at-02-30", "2026-10-25 02:30 CEST"),
        ("hourly", "2026-10-25 02:30 CEST"),
        ("hourly", "2026-10-25 03:30 CET"),
    ]


@pytest.mark.parametrize("failure", ["timeout", "fork", "nsenter"])
async def test_a_pass_that_cannot_read_the_time_zone_follows_the_zone_read_before(env, monkeypatch, failure):
    """The pass at 03:00:01 in Berlin cannot ask the host for its zone. It checked its minute on the
    clock of UTC: the task for 02:00 ran a second time, and the one for 03:00 not that night."""
    env.host.links["/etc/localtime"] = BERLIN
    _store(_daily("at-02-00", 2), _daily("at-03-00", 3))
    clock = FakeClock(datetime(2026, 11, 8, 0, 58, 30, tzinfo=UTC))  # 01:58:30 in Berlin
    started = started_runs(monkeypatch, clock)
    answer = host_module.run_on_host  # the fake host

    def run_on_host(cmd, **kwargs):
        if cmd[0] == "readlink" and datetime(2026, 11, 8, 2, 0, tzinfo=UTC) <= clock.now < datetime(2026, 11, 8, 2, 1, tzinfo=UTC):
            return _host_not_asked(failure, cmd)
        return answer(cmd, **kwargs)

    monkeypatch.setattr(host_module, "run_on_host", run_on_host)

    await run_scheduler(monkeypatch, clock, datetime(2026, 11, 8, 2, 5, tzinfo=UTC))

    assert _local(started, "Europe/Berlin") == [("at-02-00", "2026-11-08 02:00 CET"), ("at-03-00", "2026-11-08 03:00 CET")]


async def test_a_pass_that_comes_late_starts_the_runs_of_the_minutes_it_missed_once(env, monkeypatch):
    """The loop slept 60 seconds after each pass and looked back one minute: a pass that came late,
    by the time of the passes before or by a blocked event loop, missed the tasks of a minute."""
    _store(_daily("at-03-00", 3), _daily("at-03-01", 3, 1), _daily("at-03-02", 3, 2))
    clock = FakeClock(datetime(2026, 11, 8, 2, 58, 30, tzinfo=UTC))
    started = started_runs(monkeypatch, clock)

    # The pass after 02:59 comes 150 seconds late, at about 03:02:30.
    await run_scheduler(monkeypatch, clock, datetime(2026, 11, 8, 3, 6, tzinfo=UTC),
                        late={datetime(2026, 11, 8, 3, 0, tzinfo=UTC): timedelta(seconds=150)})

    assert sorted(task_id for task_id, _ in started) == ["at-03-00", "at-03-01", "at-03-02"]


async def test_a_clock_set_forward_starts_only_the_runs_of_its_last_minutes(env, monkeypatch):
    """A clock set forward by hours (NTP after a boot without a real-time clock, a resume from
    suspend) does not start every task of the hours it skipped."""
    _store(_daily("at-03-00", 3), _daily("at-05-58", 5, 58))
    clock = FakeClock(datetime(2026, 11, 8, 2, 58, 30, tzinfo=UTC))
    started = started_runs(monkeypatch, clock)

    await run_scheduler(monkeypatch, clock, datetime(2026, 11, 8, 6, 5, tzinfo=UTC),
                        late={datetime(2026, 11, 8, 2, 59, 30, tzinfo=UTC): timedelta(hours=3)})

    ((task_id, at),) = started
    assert task_id == "at-05-58" and datetime(2026, 11, 8, 6, 0, tzinfo=UTC) <= at < datetime(2026, 11, 8, 6, 1, tzinfo=UTC)


async def test_a_disabled_task_does_not_run(env, monkeypatch):
    _store(_daily("off", 3, enabled=False), _daily("on", 3))
    clock = FakeClock(datetime(2026, 11, 8, 2, 59, tzinfo=UTC))
    started = started_runs(monkeypatch, clock)

    await run_scheduler(monkeypatch, clock, datetime(2026, 11, 8, 3, 2, tzinfo=UTC))

    assert [task_id for task_id, _ in started] == ["on"]


async def test_a_task_whose_schedule_fails_neither_stops_the_others_nor_the_task_list(env, monkeypatch):
    """Whatever a schedule raises, as one in a tasks file written by hand might: the task does not
    run and has no next run, and the others run and are listed."""
    _store(_daily("broken", 3), _daily("fine", 3))
    real_cron_expression = scheduler.get_cron_expression

    def cron_expression(task: ScheduledTask) -> str:
        if task.id == "broken":
            raise TypeError("not a schedule")
        return real_cron_expression(task)

    monkeypatch.setattr(scheduler, "get_cron_expression", cron_expression)
    clock = FakeClock(datetime(2026, 11, 8, 2, 59, tzinfo=UTC))
    started = started_runs(monkeypatch, clock)

    await run_scheduler(monkeypatch, clock, datetime(2026, 11, 8, 3, 2, tzinfo=UTC))

    assert [task_id for task_id, _ in started] == ["fine"]
    response = env.client.get("/api/v1/scheduler/tasks")
    assert response.status_code == 200, response.text
    assert {task["id"]: task["next_run"] for task in response.json()} == {
        "broken": None, "fine": "2026-11-09T03:00:00+00:00"
    }
