"""Custom tasks: a shell command that runs at its schedule and is recorded with its output and
exit status (ADR-0044, ADR-0267, ADR-0268).

The commands run for real, with /bin/sh as the user that runs the tests.
"""

import asyncio
import importlib
import json
import os
import shlex
import sys
import time
from datetime import datetime, UTC
from pathlib import Path

import pytest

from src.scheduler.schemas import ScheduledTask, TaskRun

from .test_scheduler_time_zone import BERLIN, FakeClock, run_scheduler
from .test_scheduler_validation import _REAL_RUN, _task, make_env

scheduler = importlib.import_module("src.scheduler.router")

UTC = UTC


@pytest.fixture
def env(tmp_path, monkeypatch):
    yield from make_env(tmp_path, monkeypatch)


@pytest.fixture
def shell(env):
    """env, where subprocess.run runs commands for real (env replaces it with a mock)."""
    env.run.side_effect = _REAL_RUN
    return env


def _custom(command: str, **overrides) -> dict:
    return _task(**{"task_type": "custom", "target_pool": None, "keep_count": None, "command": command, **overrides})


def _create(env, command: str, **overrides) -> dict:
    response = env.client.post("/api/v1/scheduler/tasks", json=_custom(command, **overrides))
    assert response.status_code == 200, response.text
    return response.json()


def _run_now(env, task_id: str) -> dict:
    """Run the task now and return its run: the request returns once the run has ended."""
    assert env.client.post(f"/api/v1/scheduler/tasks/{task_id}/run").status_code == 200
    return env.client.get(f"/api/v1/scheduler/runs/{task_id}").json()[-1]


def _ended(pid: int) -> bool:
    """Whether a process is gone, or a zombie that only waits for its new parent to reap it."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    if Path("/proc/self/stat").exists():  # Linux: the state follows the command's name, in parentheses
        try:
            return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[-1].split()[0] == "Z"
        except FileNotFoundError:  # it ended meanwhile
            return True
    state = _REAL_RUN(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return state == "" or state.startswith("Z")


def _assert_ended(pid: int) -> None:
    deadline = time.monotonic() + 5
    while not _ended(pid):
        assert time.monotonic() < deadline, f"process {pid} still runs"
        time.sleep(0.05)


def _in_a_group_of_its_own(pid_file: Path) -> str:
    """A shell command: a process that moves into a process group of its own, in the session of the
    task's command, as `timeout` (without --foreground) and job control (`set -m`) do; it writes its
    process id into ``pid_file`` and sleeps."""
    code = ("import os, pathlib, sys, time; os.setpgid(0, 0); "
            "pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(30)")
    return f"{shlex.quote(sys.executable)} -I -S -c {shlex.quote(code)} {shlex.quote(str(pid_file))}"


def test_a_run_records_the_output_and_the_exit_status_of_the_command(shell):
    """Both output streams, in the order the command wrote them, and its exit status.

    The run kept the standard output, or the error output when there was none, and no exit status.
    """
    env = shell
    task = _create(env, "echo to stdout; echo to stderr >&2; echo more; exit 3")

    run = _run_now(env, task["id"])

    assert (run["status"], run["exit_code"], run["error"]) == ("failed", 3, "The command exited with status 3")
    assert run["output"] == "to stdout\nto stderr\nmore\n"
    (listed,) = env.client.get("/api/v1/scheduler/tasks").json()
    assert listed["last_status"] == "failed"


def test_a_command_that_exits_with_status_0_succeeds(shell):
    env = shell
    task = _create(env, "printf 'all good\\n'")
    run = _run_now(env, task["id"])
    assert (run["status"], run["exit_code"], run["output"], run["error"]) == ("success", 0, "all good\n", None)

    quiet = _create(env, "true", name="Quiet")
    run = _run_now(env, quiet["id"])
    assert (run["status"], run["exit_code"], run["output"], run["error"]) == ("success", 0, None, None)


def test_a_run_keeps_the_last_16_kib_of_the_output(shell):
    """The runs file keeps the last 100 runs and is rewritten on the system disk at each run."""
    env = shell
    task = _create(env, "yes 'a line of the output' | head -n 5000; echo the end")

    run = _run_now(env, task["id"])

    assert run["status"] == "success"
    note = "[Only the last 16 KiB of the output are kept.]\n"
    assert run["output"].startswith(note)
    kept = run["output"][len(note):]
    assert len(kept.encode()) == 16 * 1024
    assert kept.endswith("a line of the output\na line of the output\nthe end\n")
    assert (env.tmp / "runs.json").stat().st_size < 20 * 1024


def test_the_kept_output_does_not_start_inside_a_character(shell):
    """The cut at 16 KiB can fall inside a character of two or more bytes: the kept output starts
    with the next character, not with a replacement character."""
    env = shell
    # Lines of 5 bytes: the last 16 KiB of 25,000 start with the second byte of an é.
    task = _create(env, "yes 'éé' | head -n 5000")

    run = _run_now(env, task["id"])

    assert run["status"] == "success"
    assert "\ufffd" not in run["output"]
    assert run["output"].startswith("[Only the last 16 KiB of the output are kept.]\né\néé\n")


def test_a_command_that_does_not_end_in_time_is_stopped_with_what_it_started(shell, monkeypatch):
    """Without a time limit, a command that hangs kept its run "running" for good."""
    env = shell
    monkeypatch.setattr(scheduler, "COMMAND_TIMEOUT", 2)
    pid_file = env.tmp / "sleep.pid"
    task = _create(env, f"echo started; sleep 30 & echo $! > {pid_file}; wait")

    begun = time.monotonic()
    run = _run_now(env, task["id"])

    assert time.monotonic() - begun < 20
    assert (run["status"], run["exit_code"], run["output"]) == ("failed", None, "started\n")
    assert run["error"] == "The command did not end within 2 seconds and was stopped"
    assert run["finished_at"]
    _assert_ended(int(pid_file.read_text()))


def test_processes_that_a_command_leaves_running_end_with_its_run(shell):
    """The run ends when the command does. It read the output until every process that held it
    open had ended: a process started in the background kept the run going, and went on running."""
    env = shell
    pid_file = env.tmp / "sleep.pid"
    task = _create(env, f"sleep 30 & echo $! > {pid_file}; echo done")

    begun = time.monotonic()
    run = _run_now(env, task["id"])

    assert time.monotonic() - begun < 10
    assert (run["status"], run["exit_code"], run["output"]) == ("success", 0, "done\n")
    _assert_ended(int(pid_file.read_text()))


def test_processes_in_process_groups_of_their_own_end_with_the_run(shell, monkeypatch):
    """`timeout` and job control move processes into a process group of their own, in the session of
    the command: only its own group was killed, and they went on running after the run had ended."""
    env = shell
    monkeypatch.setattr(scheduler, "COMMAND_TIMEOUT", 20)  # bounds the test if the command hangs
    pid_file = env.tmp / "own-group.pid"
    task = _create(env, f"{_in_a_group_of_its_own(pid_file)} & n=0; "
                        f"while [ ! -s {pid_file} ] && [ $n -lt 100 ]; do sleep 0.1; n=$((n + 1)); done; echo done")

    begun = time.monotonic()
    run = _run_now(env, task["id"])

    assert time.monotonic() - begun < 15
    assert (run["status"], run["exit_code"], run["output"]) == ("success", 0, "done\n")
    _assert_ended(int(pid_file.read_text()))


def test_a_command_stopped_at_its_time_limit_ends_with_its_processes_in_groups_of_their_own(shell, monkeypatch):
    """A command such as `timeout 30h job` was stopped at the time limit, but `timeout` and its job,
    in a process group of their own, went on running."""
    env = shell
    monkeypatch.setattr(scheduler, "COMMAND_TIMEOUT", 2)
    pid_file = env.tmp / "own-group.pid"
    task = _create(env, f"echo started; {_in_a_group_of_its_own(pid_file)}; echo after")

    run = _run_now(env, task["id"])

    assert (run["status"], run["exit_code"], run["output"]) == ("failed", None, "started\n")
    assert run["error"] == "The command did not end within 2 seconds and was stopped"
    _assert_ended(int(pid_file.read_text()))


def test_output_in_any_language_takes_about_its_own_size_in_the_runs_file(shell):
    """The runs file is rewritten on the system disk at each run (ADR-0214). Written with JSON's
    \\u escapes, the 16 KiB of Cyrillic output that a run keeps took about three times as much there."""
    env = shell
    letters = "".join(map(chr, range(0x430, 0x450)))  # the small Cyrillic letters, 2 bytes each in UTF-8
    task = _create(env, f"yes '{letters}' | head -n 1000; echo end")

    run = _run_now(env, task["id"])

    assert run["status"] == "success"
    assert run["output"].startswith("[Only the last 16 KiB of the output are kept.]\n")
    assert run["output"].endswith(f"{letters}\nend\n")
    assert (env.tmp / "runs.json").stat().st_size < 20 * 1024


def test_a_run_with_text_that_utf_8_cannot_hold_is_saved(env):
    """A lone surrogate, as Python gives for the bytes of a file name that are not UTF-8, cannot be
    written as UTF-8: it is kept as its JSON escape, and the run is still saved and read back."""
    run = TaskRun(id="run_1", task_id="task_1", task_name="Nightly", started_at="2026-11-08T02:00:00+00:00",
                  status="failed", error="Cannot open /mnt/pools/data/caf\udce9")

    scheduler.save_run(run)

    assert scheduler.load_runs() == [run]


@pytest.mark.parametrize("command", ["echo " + "x" * 4096, "echo a NUL \x00 byte"])
def test_a_stored_command_is_checked_again_before_it_runs(shell, command):
    """A tasks file written by hand, or before the checks existed: the command does not run."""
    env = shell
    marker = env.tmp / "ran"
    (env.tmp / "tasks.json").write_text(json.dumps([_custom(f"touch {marker}; {command}", id="task_by_hand")]))

    run = _run_now(env, "task_by_hand")

    assert (run["status"], run["exit_code"], run["output"]) == ("failed", None, None)
    assert run["error"] == "Command must be at most 4096 characters without NUL bytes"
    assert not marker.exists()


async def test_the_scheduler_runs_a_custom_task_at_its_time(env, monkeypatch):
    """The loop starts the run of a custom task in the minute of its schedule on the host's clock,
    and the run records what the command printed and its exit status."""
    env.host.links["/etc/localtime"] = BERLIN
    scheduler.save_tasks([ScheduledTask(**_custom("echo ran at its time", id="nightly", hour=3, minute=0))])
    clock = FakeClock(datetime(2026, 11, 8, 1, 58, 30, tzinfo=UTC))  # 02:58:30 in Berlin

    await run_scheduler(monkeypatch, clock, datetime(2026, 11, 8, 2, 5, tzinfo=UTC))

    (run,) = scheduler.load_runs()
    started = datetime.fromisoformat(run.started_at)
    # 03:00 in Berlin.
    assert datetime(2026, 11, 8, 2, 0, tzinfo=UTC) <= started < datetime(2026, 11, 8, 2, 1, tzinfo=UTC)
    assert (run.task_id, run.status, run.exit_code, run.output) == ("nightly", "success", 0, "ran at its time\n")
    (task,) = scheduler.load_tasks()
    assert (task.last_run, task.last_status) == (run.started_at, "success")


async def test_a_run_that_a_stop_of_the_supervisor_interrupts_is_recorded_as_failed(env, monkeypatch):
    """When the supervisor stops (stop_scheduler, at the end of its lifespan), the runs that the loop
    started end, each recorded as failed, and so do the processes of their commands. A run stayed
    "running" for good."""
    pid_file = env.tmp / "sleep.pid"
    scheduler.save_tasks([ScheduledTask(**_custom(f"sleep 30 & echo $! > {pid_file}; wait", id="long",
                                                  last_status="success"))])
    (task,) = scheduler.load_tasks()
    monkeypatch.setattr(scheduler, "_runs", set())
    monkeypatch.setattr(scheduler, "_scheduler_task", None)
    monkeypatch.setattr(scheduler, "_scheduler_running", True)
    # As scheduler_loop starts a run.
    run = asyncio.create_task(scheduler.run_task(task))
    scheduler._runs.add(run)
    run.add_done_callback(scheduler._runs.discard)
    try:
        deadline = time.monotonic() + 10
        while not (pid_file.exists() and pid_file.read_text().strip()):
            assert time.monotonic() < deadline, "the command did not start"
            await asyncio.sleep(0.05)

        await scheduler.stop_scheduler()

        (record,) = scheduler.load_runs()
        assert (record.status, record.error, record.exit_code) == ("failed", "The supervisor stopped during the run", None)
        assert record.finished_at
        (stored,) = scheduler.load_tasks()
        assert (stored.last_run, stored.last_status) == (record.started_at, "failed")
        _assert_ended(int(pid_file.read_text()))
    finally:
        run.cancel()
        await asyncio.gather(run, return_exceptions=True)


async def test_runs_that_a_stop_of_the_supervisor_left_running_are_recorded_as_failed_when_it_starts(env, monkeypatch):
    """A run is saved as "running" when it starts. Killed meanwhile (a deploy, an update, a reboot, the
    kernel's out-of-memory killer), the supervisor left it "running" for good, and its task showed the
    status of the run before. The scheduler's start records it, with one write, and only when needed."""
    earlier, interrupted = "2026-11-07T02:00:00.100000+00:00", "2026-11-08T02:00:00.100000+00:00"
    now = datetime(2026, 11, 8, 3, 30, tzinfo=UTC)
    scheduler.save_tasks([
        ScheduledTask(**_custom("rsync -a /mnt/pools/data/ /mnt/pools/copy/", id="copy", name="Copy",
                                last_run=earlier, last_status="success")),
        ScheduledTask(**_custom("true", id="other", name="Other", last_run=earlier, last_status="success")),
    ])
    for record in (
        TaskRun(id="run_1", task_id="copy", task_name="Copy", started_at=earlier, finished_at=earlier,
                status="success", exit_code=0),
        TaskRun(id="run_2", task_id="copy", task_name="Copy", started_at=interrupted, status="running"),
        TaskRun(id="run_3", task_id="other", task_name="Other", started_at=interrupted, finished_at=interrupted,
                status="success", exit_code=0),
    ):
        scheduler.save_run(record)
    monkeypatch.setattr(scheduler, "_utc_now", lambda: now)
    monkeypatch.setattr(scheduler, "_runs", set())
    monkeypatch.setattr(scheduler, "_scheduler_task", None)
    monkeypatch.setattr(scheduler, "_scheduler_running", False)

    await scheduler.start_scheduler()
    try:
        runs = {record.id: record for record in scheduler.load_runs()}
        assert (runs["run_2"].status, runs["run_2"].error, runs["run_2"].finished_at) == (
            "failed", "The supervisor stopped during the run", now.isoformat())
        assert (runs["run_1"].status, runs["run_3"].status) == ("success", "success")
        tasks = {task.id: task for task in scheduler.load_tasks()}
        assert (tasks["copy"].last_run, tasks["copy"].last_status) == (interrupted, "failed")
        assert (tasks["other"].last_run, tasks["other"].last_status) == (earlier, "success")
    finally:
        await scheduler.stop_scheduler()

    # Started again with no such run, the scheduler writes neither file.
    files = [env.tmp / "runs.json", env.tmp / "tasks.json"]
    for path in files:
        os.utime(path, ns=(1_000_000_000, 1_000_000_000))
    await scheduler.start_scheduler()
    await scheduler.stop_scheduler()
    assert [path.stat().st_mtime_ns for path in files] == [1_000_000_000, 1_000_000_000]
