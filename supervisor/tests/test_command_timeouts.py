"""btrfs and smartctl commands that hang in the kernel are killed at their timeout and hold no thread.

Scrubs, error stats resets, SMART tests, the reads of SMART data and the snapshot cleanup of
scheduled snapshot tasks run btrfs or smartctl in the supervisor's container, through
host.run_in_container (ADR-0290). A process that hangs in the
kernel, on a disk or a filesystem that stopped answering, dies of SIGKILL only when the kernel lets
it go: the timeout of subprocess.run killed it and then waited for it, so the worker thread that
ran it stayed busy until then, often for good (ADR-0021).

No test can make such a process. FakeProcesses stands in for subprocess.Popen: the commands on the
pool "data" and the disk sda hang until the test releases them, whatever signal they get, and the
others answer at once. The API tests give the app one worker thread of each kind, so a thread that
still waited for a killed command would hold up the next request of its kind. TestRealProcesses
shows with real processes what the timeout kills: the command's process group, with the processes
it started (as nsenter starts the command it runs), and nothing else.
"""

import asyncio
import importlib
import json
import os
import select
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import anyio
import httpx
import pytest

from src import host
from src.auth.utils import verify_token
from src.main import app
from src.scheduler.schemas import ScheduledTask
from src.storage import disks, pools

from .test_pool_persistence import install_fake_host

health_router = importlib.import_module("src.health.router")
scheduler = importlib.import_module("src.scheduler.router")
REAL_RUN = subprocess.run
REAL_RUN_IN_CONTAINER = host.run_in_container

# The timeout the tests give every command, in seconds.
TIMEOUT = 1
# The pool and the disk whose commands hang; the commands on the others answer.
STUCK = {"data", "sda"}
SMART_JSON = json.dumps({
    "model_name": "WDC WD40EFRX",
    "serial_number": "WD-2",
    "smart_status": {"passed": True},
    "temperature": {"current": 30},
})
SCRUB_STATUS = (
    "Scrub started:    Sun Nov  8 07:00:00 2026\nStatus:           running\nDuration:         0:00:05\n"
)
# The process ids of FakeProcess: above every pid that Linux (at most 2**22) and macOS hand out.
FIRST_FAKE_PID = 5_000_000
# Seconds after which a wait without a timeout for a process that hangs fails: waiting so is
# the fault these tests look for, and the test must fail, not hang the run.
HANG_LIMIT = 20
ADMIN = SimpleNamespace(id=1, username="admin", is_admin=True, is_active=True)


class FakePipe:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeProcess:
    """A started command: the parts of subprocess.Popen that run_in_container and subprocess.run use.

    One that hangs ends only when it is released, whatever signal it got before, like a process
    stuck in the kernel: its SIGKILL takes effect when the kernel lets it go.
    """

    def __init__(self, processes: "FakeProcesses", args: list[str], **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs
        self.pid = FIRST_FAKE_PID + len(processes.started)
        self.returncode: int | None = None
        self.stdout = FakePipe() if kwargs.get("stdout") == subprocess.PIPE else None
        self.stderr = FakePipe() if kwargs.get("stderr") == subprocess.PIPE else None
        self.hangs = os.path.basename(args[-1]) in processes.stuck
        self.answer = processes.answer(args)
        self.ended = threading.Event()
        if not self.hangs:
            self.ended.set()

    def release(self) -> None:
        self.ended.set()

    def _wait_for_the_end(self, timeout: float | None) -> None:
        if self.ended.wait(HANG_LIMIT if timeout is None else timeout):
            return
        if timeout is None:
            raise AssertionError(f"{self.args} was waited for while it hangs in the kernel")
        raise subprocess.TimeoutExpired(self.args, timeout)

    def communicate(self, input=None, timeout=None):
        self._wait_for_the_end(timeout)
        code, out, err = self.answer
        self.returncode = code
        if hasattr(self.kwargs.get("stderr"), "write"):  # a file, as for btrfs scrub start
            self.kwargs["stderr"].write(err.encode())
        return (out if self.stdout else None), (err if self.stderr else None)

    def wait(self, timeout=None):
        self._wait_for_the_end(timeout)
        if self.returncode is None:
            self.returncode = -signal.SIGKILL if self.hangs else self.answer[0]
        return self.returncode

    def poll(self):
        return self.returncode

    def kill(self) -> None:
        pass  # pending until the process leaves the kernel

    def __enter__(self):
        return self

    def __exit__(self, *exc_info) -> None:
        for pipe in (self.stdout, self.stderr):
            if pipe:
                pipe.close()
        self.wait()


class FakeProcesses:
    """subprocess.Popen and os.killpg of a test: the processes it started, the signals they got."""

    def __init__(self) -> None:
        self.started: list[FakeProcess] = []
        self.killed: list[tuple[int, int]] = []
        self.stuck = set(STUCK)

    def popen(self, args, **kwargs) -> FakeProcess:
        process = FakeProcess(self, list(args), **kwargs)
        self.started.append(process)
        return process

    def killpg(self, pgid: int, sig: int) -> None:
        assert any(p.pid == pgid for p in self.started), f"killpg({pgid}) of a process the test did not start"
        self.killed.append((pgid, sig))

    @staticmethod
    def answer(cmd: list[str]) -> tuple[int, str, str]:
        if cmd[:3] == ["btrfs", "scrub", "status"]:
            return 0, SCRUB_STATUS, ""
        if cmd[:3] == ["btrfs", "balance", "status"]:
            return 0, f"No balance found on '{cmd[3]}'\n", ""
        if cmd[:2] == ["smartctl", "-t"]:
            return 0, "Testing has begun.\n", ""
        if cmd[0] == "smartctl":
            return 0, SMART_JSON, ""
        return 0, "", ""

    def runs_of(self, cmd: list[str]) -> int:
        return sum(process.args == cmd for process in self.started)

    def release(self) -> None:
        for process in self.started:
            process.release()


def _reapers() -> list[threading.Thread]:
    return [thread for thread in threading.enumerate() if thread.name.startswith("unos-reap")]


def _wait_for_reapers(timeout: float = 5) -> None:
    for thread in _reapers():
        thread.join(timeout)
        assert not thread.is_alive(), f"{thread.name} still waits for its process"


@pytest.fixture
def processes(monkeypatch):
    fake = FakeProcesses()
    monkeypatch.setattr(subprocess, "Popen", fake.popen)
    monkeypatch.setattr(os, "killpg", fake.killpg)
    yield fake
    fake.release()
    _wait_for_reapers()


async def _within(awaitable, seconds: float, what: str):
    """The result of ``awaitable`` and the seconds it took; a failure when it takes longer than ``seconds``.

    Not asyncio.wait_for: cancelling a request whose endpoint waits for an AnyIO worker thread
    waits for that thread, which a killed command would hold until the test releases it.
    """
    task = asyncio.ensure_future(awaitable)
    started = time.monotonic()
    done, _ = await asyncio.wait({task}, timeout=seconds)
    if not done:
        pytest.fail(f"{what} did not end within {seconds} seconds: a worker thread still waits")
    return task.result(), time.monotonic() - started


@pytest.fixture
async def one_worker(processes):
    """One worker thread of each kind: async endpoints run blocking work on the event loop's
    default executor (asyncio.to_thread), plain ones on AnyIO's worker threads (FastAPI).

    At the end the stuck processes end first: the event loop closes after this fixture, and it
    waits for the threads of its executor, also for one that still waits for a process.
    """
    asyncio.get_running_loop().set_default_executor(
        ThreadPoolExecutor(max_workers=1, thread_name_prefix="unos-test-worker")
    )
    anyio.to_thread.current_default_thread_limiter().total_tokens = 1
    yield
    processes.release()


def _fake_host_with_two_disks(monkeypatch):
    """The fake host with the disks sda and sdb; every process started in the container meets FakeProcesses."""
    fake = install_fake_host(monkeypatch)
    fake.add_disk("sda", serial="WD-1")
    fake.add_disk("sdb", serial="WD-2")
    # Both start their processes with the fake Popen.
    monkeypatch.setattr(host, "run_in_container", REAL_RUN_IN_CONTAINER)
    monkeypatch.setattr(subprocess, "run", REAL_RUN)
    return fake


# ---------------------------------------------------------------------------
# host.run_in_container
# ---------------------------------------------------------------------------


class TestRealProcesses:
    def test_a_command_that_ends_answers_its_status_and_output(self):
        cmd = ["sh", "-c", "printf out; printf err >&2; exit 3"]

        result = host.run_in_container(cmd, timeout=10)

        assert (result.args, result.returncode, result.stdout, result.stderr) == (cmd, 3, "out", "err")

    def test_output_that_is_not_text_is_replaced(self):
        assert host.run_in_container(["printf", "\\377ok"], timeout=10).stdout == "�ok"

    def test_the_command_has_a_session_of_its_own_and_no_input(self):
        script = "import os, sys; print(os.getsid(0), os.getpgid(0), os.getpid(), repr(sys.stdin.read()))"

        sid, pgid, pid, read = host.run_in_container([sys.executable, "-c", script], timeout=30).stdout.split()

        assert sid == pgid == pid and int(sid) != os.getsid(0)
        assert read == "''"

    def test_at_its_timeout_the_command_and_the_processes_it_started_are_killed(self):
        # sh starts sleep and waits for it, as nsenter starts the command that it runs: killing
        # sh alone would leave sleep running. Both have the pipe as their output.
        read_end, write_end = os.pipe()
        try:
            started = time.monotonic()
            with pytest.raises(subprocess.TimeoutExpired):
                host.run_in_container(["sh", "-c", "sleep 300 & wait"], timeout=TIMEOUT,
                                      stdout=write_end, stderr=subprocess.DEVNULL)
            assert time.monotonic() - started < TIMEOUT + 2
            os.close(write_end)
            write_end = None
            readable, _, _ = select.select([read_end], [], [], 5)
            assert readable and os.read(read_end, 1) == b"", "sh or the sleep it started still runs"
        finally:
            os.close(read_end)
            if write_end is not None:
                os.close(write_end)
        _wait_for_reapers()  # sh was waited for: no zombie is left

    def test_processes_that_the_command_did_not_start_are_not_killed(self):
        neighbour = subprocess.Popen(["sleep", "300"])  # in the process group of the tests
        try:
            with pytest.raises(subprocess.TimeoutExpired):
                host.run_in_container(["sleep", "300"], timeout=TIMEOUT)
            _wait_for_reapers()
            assert neighbour.poll() is None
        finally:
            neighbour.kill()
            neighbour.wait()


class TestCommandsThatDoNotReturn:
    def test_the_timeout_is_raised_at_once_and_the_process_group_is_killed(self, processes):
        cmd = ["btrfs", "scrub", "status", "/mnt/pools/data"]

        started = time.monotonic()
        with pytest.raises(subprocess.TimeoutExpired) as raised:
            host.run_in_container(cmd, timeout=TIMEOUT)
        took = time.monotonic() - started

        assert (raised.value.cmd, raised.value.timeout) == (cmd, TIMEOUT)
        assert TIMEOUT - 0.1 <= took < TIMEOUT + 1  # it waited for the timeout, not for the end
        (process,) = processes.started
        assert process.kwargs["start_new_session"] is True  # the group is the command's own
        assert processes.killed == [(process.pid, signal.SIGKILL)]
        # A daemon thread waits for it to end, which it does when the kernel lets it go.
        (reaper,) = _reapers()
        assert reaper.daemon and reaper.is_alive() and process.returncode is None
        process.release()
        reaper.join(5)
        assert not reaper.is_alive()
        assert process.returncode == -signal.SIGKILL
        assert process.stdout.closed and process.stderr.closed

    def test_the_same_command_is_not_started_again_while_it_hangs(self, processes, caplog):
        cmd = ["smartctl", "-j", "-a", "/dev/sda"]
        with pytest.raises(subprocess.TimeoutExpired):
            host.run_in_container(cmd, timeout=TIMEOUT)

        started = time.monotonic()
        with pytest.raises(subprocess.TimeoutExpired) as raised:
            host.run_in_container(cmd, timeout=TIMEOUT)
        with pytest.raises(subprocess.TimeoutExpired):
            host.run_in_container(cmd, timeout=TIMEOUT)

        assert time.monotonic() - started < 0.5
        assert (raised.value.cmd, raised.value.timeout) == (cmd, TIMEOUT)
        assert processes.runs_of(cmd) == 1
        # Logged once for the stuck run, not for every request it turns away.
        assert caplog.text.count("Not started: smartctl -j -a /dev/sda") == 1
        other = ["smartctl", "-j", "-a", "/dev/sdb"]
        assert host.run_in_container(other, timeout=TIMEOUT).stdout == SMART_JSON

        # Once the stuck process ended, the command runs again.
        processes.release()
        _wait_for_reapers()
        processes.stuck.clear()
        assert host.run_in_container(cmd, timeout=TIMEOUT).stdout == SMART_JSON
        assert [process.args for process in processes.started] == [cmd, other, cmd]

    def test_a_second_run_of_a_command_waits_for_the_first_and_starts_nothing_when_it_hangs(
        self, processes, caplog
    ):
        # As the Storage page's scrub status requests, which come every 2 seconds and overlap
        # while btrfs does not answer: a hang leaves one stuck process, not one per request.
        cmd = ["btrfs", "scrub", "status", "/mnt/pools/data"]
        timed_out: dict[str, float] = {}

        def run(which: str) -> None:
            try:
                host.run_in_container(cmd, timeout=TIMEOUT)
            except subprocess.TimeoutExpired:
                timed_out[which] = time.monotonic()

        started = time.monotonic()
        first = threading.Thread(target=run, args=("first",))
        first.start()
        _wait_for(lambda: processes.runs_of(cmd) == 1)
        second = threading.Thread(target=run, args=("second",))
        second.start()
        for thread in (first, second):
            thread.join(TIMEOUT + 5)

        assert set(timed_out) == {"first", "second"}
        # The second waited for the first, which was killed at its timeout, and then gave up
        # at once: it did not give up before the first was killed, nor wait a timeout of its own.
        assert TIMEOUT - 0.1 <= timed_out["second"] - started < TIMEOUT + 1
        assert processes.runs_of(cmd) == 1
        assert "Not started: btrfs scrub status /mnt/pools/data" in caplog.text

    def test_a_second_run_of_a_command_starts_as_soon_as_the_first_answered(self, processes):
        # Two tabs of the Storage page that ask for the scrub status of a pool that answers, at
        # the same moment: the second runs btrfs once the first answered, and both get the status.
        cmd = ["btrfs", "scrub", "status", "/mnt/pools/data"]  # answers when the test releases it
        timeout = 10  # longer than the test takes: neither run gives up
        answers: dict[str, str] = {}

        def run(which: str) -> None:
            try:
                answers[which] = host.run_in_container(cmd, timeout=timeout).stdout
            except subprocess.TimeoutExpired:
                answers[which] = "timed out"

        first = threading.Thread(target=run, args=("first",))
        second = threading.Thread(target=run, args=("second",))
        try:
            first.start()
            _wait_for(lambda: processes.runs_of(cmd) == 1)
            second.start()
            time.sleep(0.3)
            # The second waits for the first: it has neither given up nor started btrfs.
            assert second.is_alive() and answers == {}
            assert processes.runs_of(cmd) == 1

            processes.stuck.clear()  # the second process answers at once
            released = time.monotonic()
            processes.release()
            second.join(timeout / 2)

            assert answers == {"first": SCRUB_STATUS, "second": SCRUB_STATUS}
            assert processes.runs_of(cmd) == 2
            # Woken when the first ended, not at the end of its own timeout.
            assert time.monotonic() - released < 2
        finally:
            processes.release()
            for thread in (first, second):
                if thread.is_alive():
                    thread.join(HANG_LIMIT)

    def test_a_command_is_left_alone_when_its_wait_cannot_be_handed_over(self, processes, monkeypatch):
        cmd = ["smartctl", "-j", "-a", "/dev/sda"]

        def no_thread(self):
            raise RuntimeError("can't start new thread")

        with monkeypatch.context() as patched:
            patched.setattr(threading.Thread, "start", no_thread)
            with pytest.raises(RuntimeError):
                host.run_in_container(cmd, timeout=TIMEOUT)

        # Not marked as stuck for good: the command runs again.
        processes.stuck.clear()
        assert host.run_in_container(cmd, timeout=TIMEOUT).stdout == SMART_JSON


def _wait_for(condition, seconds: float = 5) -> None:
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "the condition did not come true"
        time.sleep(0.01)


# ---------------------------------------------------------------------------
# The API answers the timeout, and later requests are served
# ---------------------------------------------------------------------------


@pytest.fixture
async def api(one_worker, monkeypatch, tmp_path):
    """The app, with an administrator signed in, one worker thread of each kind and the timeouts TIMEOUT.

    The pools data and media are folders of tmp_path; the disks sda and sdb are those of the fake host.
    """
    _fake_host_with_two_disks(monkeypatch)
    monkeypatch.setattr(pools.settings, "pools_base_path", str(tmp_path))
    for pool in ("data", "media"):
        (tmp_path / pool).mkdir()
    # Temporary directories are not mount points; treat the pool directories as mounted.
    monkeypatch.setattr(pools, "_btrfs_mounted_at", os.path.isdir)
    monkeypatch.setattr(pools, "SCRUB_COMMAND_TIMEOUT", TIMEOUT)
    monkeypatch.setattr(disks, "SMARTCTL_TIMEOUT", TIMEOUT)
    monkeypatch.setattr(health_router, "SMARTCTL_TIMEOUT", TIMEOUT)
    monkeypatch.setattr(health_router, "RAID_STATUS_FILE", str(tmp_path / "raid_status.json"))
    monkeypatch.setattr(health_router, "_latest_raid_status", None)
    app.dependency_overrides[verify_token] = lambda: ADMIN
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://unos") as client:
            yield client
    finally:
        app.dependency_overrides.pop(verify_token, None)


def _not_responding(command: str, target: str) -> tuple[int, str]:
    return 500, f"{command} did not answer within {TIMEOUT} seconds: {target} may not be responding"


@pytest.mark.parametrize(
    "method, url, stuck, answering, timed_out",
    [
        # A start asks first whether a balance of the pool runs (ADR-0379): on a hung pool, that
        # command is the one that does not answer.
        ("POST", "/api/v1/storage/pools/{}/scrub", "data", "media",
         _not_responding("btrfs balance status", "pool data")),
        ("GET", "/api/v1/storage/pools/{}/scrub", "data", "media",
         _not_responding("btrfs scrub status", "pool data")),
        ("DELETE", "/api/v1/storage/pools/{}/scrub", "data", "media",
         _not_responding("btrfs scrub cancel", "pool data")),
        ("POST", "/api/v1/health/raid/{}/reset-stats", "data", "media",
         _not_responding("btrfs device stats", "pool data")),
        ("POST", "/api/v1/health/disk/{}/test?test_type=short", "sda", "sdb",
         _not_responding("smartctl", "/dev/sda")),
    ],
)
async def test_a_command_that_does_not_return_is_answered_and_frees_its_thread(
    api, processes, method, url, stuck, answering, timed_out
):
    response, took = await _within(api.request(method, url.format(stuck)), TIMEOUT + 5,
                                   f"{method} {url.format(stuck)}")

    assert (response.status_code, response.json()["detail"]) == timed_out
    assert took < TIMEOUT + 1.5
    (hanging,) = processes.started
    assert processes.killed == [(hanging.pid, signal.SIGKILL)]
    assert hanging.returncode is None  # still in the kernel: a daemon thread waits for it

    # The one worker thread is free: the same request for a pool or disk that answers is served.
    response, took = await _within(api.request(method, url.format(answering)), 5,
                                   f"{method} {url.format(answering)} after the timeout")
    assert response.status_code == 200, response.text
    assert took < TIMEOUT

    # The command that still hangs is not started again: the request is answered at once.
    response, took = await _within(api.request(method, url.format(stuck)), 5,
                                   f"{method} {url.format(stuck)} again")
    assert (response.status_code, response.json()["detail"]) == timed_out
    assert took < TIMEOUT
    assert processes.runs_of(hanging.args) == 1


@pytest.mark.parametrize(
    "url, timed_out",
    [
        # Storage: the SMART data of a disk; a smartctl without an answer is reported in the data.
        ("/api/v1/storage/disks/{}/smart",
         lambda r: r.status_code == 200 and r.json()["raw_data"] == {
             "error": f"smartctl did not answer within {TIMEOUT} seconds"}),
        # Disk Health: the SMART data of one disk.
        ("/api/v1/health/disk/{}",
         lambda r: r.status_code == 500 and r.json()["detail"].startswith("Failed to get SMART data: ")),
    ],
)
async def test_a_read_of_smart_data_that_does_not_return_is_answered_and_frees_its_thread(
    api, processes, url, timed_out
):
    response, took = await _within(api.get(url.format("sda")), TIMEOUT + 5, f"GET {url.format('sda')}")

    assert timed_out(response), response.text
    assert took < TIMEOUT + 1.5
    (hanging,) = processes.started
    assert processes.killed == [(hanging.pid, signal.SIGKILL)]

    response, took = await _within(api.get(url.format("sdb")), 5, f"GET {url.format('sdb')} after the timeout")
    assert response.status_code == 200, response.text
    assert took < TIMEOUT

    response, took = await _within(api.get(url.format("sda")), 5, f"GET {url.format('sda')} again")
    assert timed_out(response), response.text
    assert took < TIMEOUT
    assert processes.runs_of(hanging.args) == 1


async def test_disk_health_lists_a_disk_whose_smartctl_does_not_return(api, processes):
    response, took = await _within(api.get("/api/v1/health/disks"), TIMEOUT + 5, "GET /api/v1/health/disks")

    assert response.status_code == 200, response.text
    assert [(disk["device"], disk["health_status"]) for disk in response.json()["disks"]] == [
        ("/dev/sda", "UNKNOWN"), ("/dev/sdb", "PASSED")]
    assert took < TIMEOUT + 1.5

    # The next look does not wait for sda again.
    response, took = await _within(api.get("/api/v1/health/disks"), 5, "GET /api/v1/health/disks again")
    assert response.status_code == 200 and took < TIMEOUT


# ---------------------------------------------------------------------------
# Scheduled tasks and the SMART monitor
# ---------------------------------------------------------------------------


@pytest.fixture
async def scheduled(one_worker, monkeypatch, tmp_path):
    """Runs a scheduled task (scheduler.execute_task) with one worker thread and the timeouts TIMEOUT."""
    _fake_host_with_two_disks(monkeypatch)
    base = tmp_path.resolve() / "pools"
    for pool in ("data", "media"):
        (base / pool).mkdir(parents=True)
    monkeypatch.setattr("src.security.validators.settings", SimpleNamespace(pools_base_path=str(base)))
    # Temporary directories are not mount points; treat the pool directories as mounted.
    monkeypatch.setattr(scheduler, "_pool_is_mounted", scheduler._real_dir)
    monkeypatch.setattr(pools, "_btrfs_mounted_at", scheduler._real_dir)
    monkeypatch.setattr(scheduler, "SCRUB_START_TIMEOUT", TIMEOUT)
    monkeypatch.setattr(scheduler, "SMART_TEST_TIMEOUT", TIMEOUT)

    async def run(seconds: float, **target):
        task = ScheduledTask(name="Check", frequency="daily", **target)
        return await _within(scheduler.execute_task(task), seconds, f"the scheduled {task.task_type.value}")

    return SimpleNamespace(run=run, base=base)


@pytest.mark.parametrize(
    "task_type, stuck, answering, timed_out, done",
    [
        ("smart_test", {"target_disk": "/dev/sda"}, {"target_disk": "/dev/sdb"},
         f"smartctl timed out: /dev/sda did not answer within {TIMEOUT} seconds. "
         "The disk or its USB bridge may not be responding.",
         "SMART test started"),
        ("scrub", {"target_pool": "data"}, {"target_pool": "media"},
         f"btrfs scrub start timed out: data did not answer within {TIMEOUT} seconds. "
         "The pool may not be responding.",
         "Scrub started"),
    ],
)
async def test_a_scheduled_task_whose_command_does_not_return_fails_and_frees_its_thread(
    scheduled, processes, task_type, stuck, answering, timed_out, done
):
    result, took = await scheduled.run(TIMEOUT + 5, task_type=task_type, **stuck)

    assert result == (False, timed_out)
    assert took < TIMEOUT + 1.5
    (hanging,) = processes.started
    assert processes.killed == [(hanging.pid, signal.SIGKILL)]

    result, took = await scheduled.run(5, task_type=task_type, **answering)
    assert result == (True, done)
    assert took < TIMEOUT

    result, took = await scheduled.run(5, task_type=task_type, **stuck)
    assert result == (False, timed_out)
    assert took < TIMEOUT
    assert processes.runs_of(hanging.args) == 1


async def test_a_snapshot_task_whose_cleanup_does_not_return_fails_and_keeps_the_snapshots(
    scheduled, processes, monkeypatch
):
    """The keep count of a scheduled snapshot task: `btrfs subvolume show` had no timeout at all.

    The new snapshot is made; the run fails, so that the task does not show success while its
    keep count is not applied.
    """
    snapshots = scheduled.base / "data" / ".snapshots"
    names = [f"auto_2025010{day}_000000" for day in (1, 2, 3)]
    for name in names:
        (snapshots / name).mkdir(parents=True)
    processes.stuck = set(names)
    monkeypatch.setattr(scheduler, "SUBVOLUME_SHOW_TIMEOUT", TIMEOUT)

    result, took = await scheduled.run(TIMEOUT + 5, task_type="snapshot", target_pool="data", keep_count=1)

    snapshot, hanging = processes.started  # it stopped at the first snapshot that did not answer
    assert snapshot.args[:3] == ["btrfs", "subvolume", "snapshot"]
    assert result == (False, (
        f"Snapshot {os.path.basename(snapshot.args[-1])} created, but old snapshots were not cleaned up: "
        f"btrfs did not answer within {TIMEOUT} seconds. The pool may not be responding."
    ))
    assert took < TIMEOUT + 1.5
    assert sorted(os.listdir(snapshots)) == names  # (the fake process made no folder for the new one)
    assert hanging.args[:3] == ["btrfs", "subvolume", "show"]
    assert processes.killed == [(hanging.pid, signal.SIGKILL)]



async def test_the_smart_monitor_records_the_other_disks_past_one_that_does_not_answer(
    one_worker, processes, monkeypatch, tmp_path
):
    _fake_host_with_two_disks(monkeypatch)
    monkeypatch.setattr(health_router, "SMARTCTL_TIMEOUT", TIMEOUT)
    history_file = tmp_path / "smart_history.json"
    monkeypatch.setattr(health_router, "SMART_HISTORY_FILE", str(history_file))

    _, took = await _within(asyncio.to_thread(health_router._smart_monitor_pass), TIMEOUT + 5,
                            "a round of the SMART monitor")
    assert took < TIMEOUT + 1.5
    assert {serial: len(entries) for serial, entries in json.loads(history_file.read_text()).items()} == {"WD-2": 1}
    hanging, answered = processes.started
    assert (hanging.args, answered.args) == (["smartctl", "-n", "standby", "-j", "-a", "/dev/sda"],
                                             ["smartctl", "-n", "standby", "-j", "-a", "/dev/sdb"])
    assert processes.killed == [(hanging.pid, signal.SIGKILL)]

    # The next round does not wait for sda again. It records WD-2 in the history in RAM; the file is
    # written only when a round changes the history, which this one does not (ADR-0269).
    _, took = await _within(asyncio.to_thread(health_router._smart_monitor_pass), 5, "the next round")
    assert took < TIMEOUT
    assert {serial: len(entries) for serial, entries in health_router._smart_history.history.items()} == {
        "WD-2": 2}
    assert {serial: len(entries) for serial, entries in json.loads(history_file.read_text()).items()} == {"WD-2": 1}
    assert processes.runs_of(hanging.args) == 1
