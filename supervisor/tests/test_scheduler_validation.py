"""Scheduler tasks: admin-only, validated targets at creation and at run time."""

import contextlib
import importlib
import json
import os
import signal
import subprocess
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.auth.utils import verify_token
from src.main import app
from src.scheduler.schemas import ScheduledTask

from .test_pool_persistence import install_fake_host

scheduler = importlib.import_module("src.scheduler.router")

ADMIN = SimpleNamespace(id=1, username="admin", is_admin=True)
USER = SimpleNamespace(id=2, username="user", is_admin=False)


@pytest.fixture
def env(tmp_path, monkeypatch):
    base = tmp_path.resolve() / "pools"
    (base / "data").mkdir(parents=True)
    (tmp_path / "outside").mkdir()
    monkeypatch.setattr(scheduler, "TASKS_FILE", str(tmp_path / "tasks.json"))
    monkeypatch.setattr(scheduler, "RUNS_FILE", str(tmp_path / "runs.json"))
    app.dependency_overrides[verify_token] = lambda: ADMIN
    # Temporary directories are not mount points; treat existing pool directories as mounted.
    monkeypatch.setattr(scheduler, "_pool_is_mounted", scheduler._real_dir)
    # The host's block devices, as lsblk reports them in the host namespaces
    # (FakeHost also has the boot SD card mmcblk1 and the swap device zram0).
    host = install_fake_host(monkeypatch)
    host.add_disk("sda", serial="WD-1")
    host.add_partition("sda", "sda1", fstype="ext4")
    host.add_disk("sdc", serial="WD-3")
    host.add_holder("sdc", "md0", "raid1")
    host.add_disk("nvme0n1", rota=False, serial="NV-1")
    host.add_disk("loop0", size=1_000_000)["type"] = "loop"
    with patch("src.security.validators.settings", SimpleNamespace(pools_base_path=str(base))):
        # smartctl, btrfs and custom commands run in the container (subprocess.run).
        with patch("src.scheduler.router.subprocess.run") as run:
            run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            yield SimpleNamespace(client=TestClient(app), base=base, pool=base / "data",
                                  outside=tmp_path / "outside", run=run, tmp=tmp_path, host=host)
    app.dependency_overrides.pop(verify_token, None)


def _task(**overrides) -> dict:
    task = {"name": "Nightly", "task_type": "snapshot", "enabled": True, "frequency": "daily",
            "hour": 2, "minute": 0, "day_of_week": 0, "day_of_month": 1, "target_pool": "data", "keep_count": 7}
    task.update(overrides)
    return task


def _stored(env) -> list[dict]:
    path = env.tmp / "tasks.json"
    return json.loads(path.read_text()) if path.exists() else []


# --- access control --------------------------------------------------------------


def test_non_admin_cannot_use_any_scheduler_endpoint(env):
    env.client.post("/api/v1/scheduler/tasks", json=_task())
    (task,) = _stored(env)
    app.dependency_overrides[verify_token] = lambda: USER
    calls = [
        ("GET", "/api/v1/scheduler/tasks", None),
        ("POST", "/api/v1/scheduler/tasks", _task(task_type="custom", command="id", target_pool=None)),
        ("PUT", f"/api/v1/scheduler/tasks/{task['id']}", _task(task_type="custom", command="id")),
        ("DELETE", f"/api/v1/scheduler/tasks/{task['id']}", None),
        ("POST", f"/api/v1/scheduler/tasks/{task['id']}/run", None),
        ("POST", f"/api/v1/scheduler/tasks/{task['id']}/toggle", None),
        ("GET", "/api/v1/scheduler/runs", None),
    ]
    for method, url, body in calls:
        response = env.client.request(method, url, json=body)
        assert response.status_code == 403, (method, url)
    assert _stored(env) == [task]
    env.run.assert_not_called()


def test_router_enforces_admin_without_main_app(env):
    """The router itself requires an administrator, independent of main.py."""
    standalone = FastAPI()
    standalone.include_router(scheduler.router, prefix="/api/v1")
    standalone.dependency_overrides[verify_token] = lambda: USER
    response = TestClient(standalone).post(
        "/api/v1/scheduler/tasks", json=_task(task_type="custom", command="id", target_pool=None)
    )
    assert response.status_code == 403
    assert _stored(env) == []


# --- validation when saving ----------------------------------------------------------


def test_valid_snapshot_task(env):
    response = env.client.post("/api/v1/scheduler/tasks", json=_task())
    assert response.status_code == 200, response.text
    assert response.json()["target_pool"] == "data"
    assert len(_stored(env)) == 1


@pytest.mark.parametrize(
    "pool", ["../../etc", "data/../..", "data\n", "", None, "/mnt/pools/data", "data; rm -rf /", "-data", "a" * 64]
)
@pytest.mark.parametrize("task_type", ["snapshot", "scrub"])
def test_invalid_pool_targets_rejected(env, pool, task_type):
    response = env.client.post("/api/v1/scheduler/tasks", json=_task(task_type=task_type, target_pool=pool))
    assert response.status_code == 400, response.text
    assert _stored(env) == []


def test_pool_symlink_escaping_base_rejected(env):
    os.symlink(env.outside, env.base / "escape")
    response = env.client.post("/api/v1/scheduler/tasks", json=_task(target_pool="escape"))
    assert response.status_code == 400
    assert _stored(env) == []


@pytest.mark.parametrize("disk,stored", [("/dev/sda", "/dev/sda"), ("sda", "/dev/sda"), ("nvme0n1", "/dev/nvme0n1")])
def test_disk_targets_are_normalized(env, disk, stored):
    response = env.client.post(
        "/api/v1/scheduler/tasks", json=_task(task_type="smart_test", target_pool=None, target_disk=disk)
    )
    assert response.status_code == 200, response.text
    assert response.json()["target_disk"] == stored
    assert _stored(env)[0]["target_disk"] == stored


@pytest.mark.parametrize(
    "disk",
    ["/dev//dev/sda", "sda; reboot", "../sda", "/dev/../etc/passwd", "sda\n", "", None, "dev/sda",
     "/dev/./sda", "/dev/sda/", "/dev/disk/by-id/ata-WD-1", "mmcblk1"],
)
def test_invalid_disk_targets_rejected(env, disk):
    response = env.client.post(
        "/api/v1/scheduler/tasks", json=_task(task_type="smart_test", target_pool=None, target_disk=disk)
    )
    assert response.status_code == 400
    assert _stored(env) == []


@pytest.mark.parametrize(
    "disk",
    [
        "/dev/sda1", "sda1",  # a partition
        "sdz", "/dev/sdz",  # no such device
        "zram0", "loop0",  # virtual devices
        "md0",  # a RAID array built on sdc
        "nvme0",  # the NVMe controller, not a block device
    ],
)
def test_disk_target_must_be_a_whole_disk_of_the_host(env, disk):
    response = env.client.post(
        "/api/v1/scheduler/tasks", json=_task(task_type="smart_test", target_pool=None, target_disk=disk)
    )
    assert response.status_code == 400, response.text
    assert "is not a disk of this system" in response.json()["detail"]
    assert _stored(env) == []
    env.run.assert_not_called()


def test_disk_target_is_not_saved_when_the_host_disks_cannot_be_listed(env):
    env.host.fail[("lsblk",)] = (1, "lsblk: failed to access sysfs directory: /sys/dev/block")
    response = env.client.post(
        "/api/v1/scheduler/tasks", json=_task(task_type="smart_test", target_pool=None, target_disk="/dev/sda")
    )
    assert response.status_code == 500
    assert "Could not list the host's disks" in response.json()["detail"]
    assert _stored(env) == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"hour": 24}, {"hour": -1}, {"minute": 60}, {"day_of_week": 7}, {"day_of_month": 0},
        {"frequency": "custom", "cron_expression": "not a cron"},
        {"frequency": "custom", "cron_expression": None},
        {"frequency": "custom", "cron_expression": "0 2 * * *\n"},
        {"keep_count": 0}, {"keep_count": -5}, {"keep_count": 100000},
        {"name": ""}, {"name": "   "}, {"name": "x" * 101}, {"name": "bad\nname"},
    ],
)
def test_invalid_schedule_or_fields_rejected(env, overrides):
    response = env.client.post("/api/v1/scheduler/tasks", json=_task(**overrides))
    assert response.status_code == 400, response.text
    assert _stored(env) == []


def test_custom_cron_accepted(env):
    response = env.client.post("/api/v1/scheduler/tasks",
                               json=_task(frequency="custom", cron_expression="*/15 * * * *"))
    assert response.status_code == 200, response.text


def test_custom_command_task(env):
    response = env.client.post("/api/v1/scheduler/tasks",
                               json=_task(task_type="custom", target_pool=None, command="echo hello"))
    assert response.status_code == 200
    for command in ["", "   ", None, "echo \x00 nul", "x" * 5000]:
        response = env.client.post("/api/v1/scheduler/tasks",
                                   json=_task(task_type="custom", target_pool=None, command=command))
        assert response.status_code == 400, command
    assert len(_stored(env)) == 1


def test_update_is_validated(env):
    env.client.post("/api/v1/scheduler/tasks", json=_task())
    (task,) = _stored(env)
    response = env.client.put(f"/api/v1/scheduler/tasks/{task['id']}", json=_task(target_pool="../../etc"))
    assert response.status_code == 400
    assert _stored(env) == [task]


@pytest.mark.parametrize("disk", ["/dev/sda1", "md0", "sdz"])
def test_update_checks_the_disk_on_the_host(env, disk):
    env.client.post("/api/v1/scheduler/tasks", json=_task(task_type="smart_test", target_pool=None, target_disk="sda"))
    (task,) = _stored(env)
    response = env.client.put(
        f"/api/v1/scheduler/tasks/{task['id']}", json=_task(task_type="smart_test", target_pool=None, target_disk=disk)
    )
    assert response.status_code == 400, response.text
    assert "is not a disk of this system" in response.json()["detail"]
    assert _stored(env) == [task]
    env.run.assert_not_called()


def test_update_keeps_the_disk_of_a_smart_task_while_it_is_disconnected(env):
    """Changing the hour of a task for an unplugged USB disk must not need the disk."""
    task = _create_smart_task(env, "/dev/nvme0n1")
    env.host.tree = [node for node in env.host.tree if node["name"] != "nvme0n1"]
    url = f"/api/v1/scheduler/tasks/{task['id']}"

    for disk in ("/dev/nvme0n1", "nvme0n1"):
        response = env.client.put(url, json=_task(task_type="smart_test", target_pool=None, target_disk=disk, hour=5))
        assert response.status_code == 200, response.text
        assert response.json()["target_disk"] == "/dev/nvme0n1"
    (stored,) = _stored(env)
    assert (stored["hour"], stored["target_disk"]) == (5, "/dev/nvme0n1")

    # Another disk is still checked on the host.
    response = env.client.put(url, json=_task(task_type="smart_test", target_pool=None, target_disk="/dev/sdz"))
    assert response.status_code == 400, response.text
    assert "/dev/sdz is not a disk of this system" in response.json()["detail"]
    assert _stored(env) == [stored]

    # The run checks the kept disk before smartctl runs.
    env.client.post(f"{url}/run")
    env.run.assert_not_called()
    (run,) = env.client.get(f"/api/v1/scheduler/runs/{task['id']}").json()
    assert run["status"] == "failed"
    assert "/dev/nvme0n1 is not a disk of this system" in run["error"]


def test_a_disconnected_disk_is_only_kept_by_the_smart_task_that_has_it(env):
    tasks = [
        _task(id="task_snapshot"),
        _task(id="task_smart_sda", task_type="smart_test", target_pool=None, target_disk="/dev/sda"),
        _task(id="task_smart_nvme", task_type="smart_test", target_pool=None, target_disk="/dev/nvme0n1"),
    ]
    (env.tmp / "tasks.json").write_text(json.dumps(tasks))
    env.host.tree = [node for node in env.host.tree if node["name"] != "nvme0n1"]

    for task_id in ("task_snapshot", "task_smart_sda", "task_missing"):
        response = env.client.put(
            f"/api/v1/scheduler/tasks/{task_id}",
            json=_task(task_type="smart_test", target_pool=None, target_disk="/dev/nvme0n1"),
        )
        assert response.status_code == 400, (task_id, response.text)
        assert "/dev/nvme0n1 is not a disk of this system" in response.json()["detail"]
    assert _stored(env) == tasks


def test_tasks_created_in_the_same_second_get_different_ids(env, monkeypatch):
    """The id was the creation time to the second: two tasks got the same id, and
    deleting one deleted both (editing, running and toggling reached only the first)."""
    frozen = datetime(2026, 1, 2, 3, 4, 5)

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen if tz is None else frozen.replace(tzinfo=tz)

    monkeypatch.setattr(scheduler, "datetime", _FrozenDatetime)
    first, second = [env.client.post("/api/v1/scheduler/tasks", json=_task(name=name)).json() for name in ("A", "B")]

    assert first["id"] != second["id"]
    assert env.client.delete(f"/api/v1/scheduler/tasks/{first['id']}").status_code == 200
    assert [task["name"] for task in _stored(env)] == ["B"]


def test_stored_tasks_that_predate_validation_still_load(env):
    legacy = _task(id="task_1", keep_count=0, target_pool="../../etc")
    (env.tmp / "tasks.json").write_text(json.dumps([legacy, _task(id="task_2")]))
    response = env.client.get("/api/v1/scheduler/tasks")
    assert response.status_code == 200
    assert [t["id"] for t in response.json()] == ["task_1", "task_2"]


# --- validation when running ---------------------------------------------------------


def _commands(env) -> list:
    return [call.args[0] for call in env.run.call_args_list]


async def test_smart_test_runs_on_normalized_device(env):
    for target in ("/dev/sda", "sda"):
        env.run.reset_mock()
        ok, _ = await scheduler.execute_task(ScheduledTask(**_task(task_type="smart_test", target_disk=target)))
        assert ok
        assert _commands(env) == [["smartctl", "-t", "short", "/dev/sda"]]


def _create_smart_task(env, disk: str) -> dict:
    response = env.client.post(
        "/api/v1/scheduler/tasks", json=_task(task_type="smart_test", target_pool=None, target_disk=disk)
    )
    assert response.status_code == 200, response.text
    return response.json()


# The Scheduler tab sends the device path of the disk it lists (Disk.path);
# older clients and hand-written task files use the bare name.
@pytest.mark.parametrize("disk", ["/dev/sda", "sda", "/dev/nvme0n1"])
def test_smart_task_end_to_end(env, disk):
    """Save the task, run it now, read the result: smartctl gets the device path once."""
    device = disk if disk.startswith("/dev/") else f"/dev/{disk}"
    env.run.return_value = MagicMock(returncode=0, stdout="Testing has begun.\n", stderr="")
    task = _create_smart_task(env, disk)
    assert task["target_disk"] == device

    response = env.client.post(f"/api/v1/scheduler/tasks/{task['id']}/run")

    assert response.status_code == 200, response.text
    assert _commands(env) == [["smartctl", "-t", "short", device]]
    (run,) = env.client.get(f"/api/v1/scheduler/runs/{task['id']}").json()
    assert (run["status"], run["output"], run["error"]) == ("success", "SMART test started", None)
    (stored,) = env.client.get("/api/v1/scheduler/tasks").json()
    assert stored["last_status"] == "success"


def test_failed_smart_test_is_recorded(env):
    env.run.return_value = MagicMock(
        returncode=2, stdout="", stderr="Smartctl open device: /dev/sda failed: No such device"
    )
    task = _create_smart_task(env, "/dev/sda")

    env.client.post(f"/api/v1/scheduler/tasks/{task['id']}/run")

    assert _commands(env) == [["smartctl", "-t", "short", "/dev/sda"]]
    (run,) = env.client.get(f"/api/v1/scheduler/runs/{task['id']}").json()
    assert run["status"] == "failed"
    assert "No such device" in run["error"]


def test_hanging_smart_test_is_recorded_as_timed_out(env):
    """A hung disk or USB bridge must not block a worker thread and leave the run "running"."""

    def smartctl(cmd, **kwargs):
        if kwargs.get("timeout") is None:
            raise AssertionError("smartctl was started without a timeout and would never return")
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    env.run.side_effect = smartctl
    task = _create_smart_task(env, "/dev/sda")

    env.client.post(f"/api/v1/scheduler/tasks/{task['id']}/run")

    (run,) = env.client.get(f"/api/v1/scheduler/runs/{task['id']}").json()
    assert run["status"] == "failed" and run["finished_at"]
    assert run["error"] == (
        "smartctl timed out: /dev/sda did not answer within 60 seconds. "
        "The disk or its USB bridge may not be responding."
    )
    assert env.run.call_args.kwargs["timeout"] == scheduler.SMART_TEST_TIMEOUT == 60
    (stored,) = env.client.get("/api/v1/scheduler/tasks").json()
    assert stored["last_status"] == "failed"


def test_smart_task_whose_disk_is_gone_fails_without_smartctl(env):
    task = _create_smart_task(env, "/dev/nvme0n1")
    env.host.tree = [node for node in env.host.tree if node["name"] != "nvme0n1"]

    env.client.post(f"/api/v1/scheduler/tasks/{task['id']}/run")

    env.run.assert_not_called()
    (run,) = env.client.get(f"/api/v1/scheduler/runs/{task['id']}").json()
    assert run["status"] == "failed"
    assert "/dev/nvme0n1 is not a disk of this system" in run["error"]


@pytest.mark.parametrize("disk", ["/dev/sda1", "sdz", "zram0", "md0"])
async def test_stored_smart_task_on_a_device_that_is_no_whole_disk_fails(env, disk):
    ok, message = await scheduler.execute_task(ScheduledTask(**_task(task_type="smart_test", target_disk=disk)))
    assert not ok
    assert "is not a disk of this system" in message
    env.run.assert_not_called()


@pytest.mark.parametrize(
    "overrides",
    [
        {"task_type": "smart_test", "target_disk": "/dev//dev/sda"},
        {"task_type": "smart_test", "target_disk": "sda; reboot"},
        {"task_type": "smart_test", "target_disk": None},
        {"task_type": "snapshot", "target_pool": "../../etc"},
        {"task_type": "snapshot", "target_pool": "data\n"},
        {"task_type": "snapshot", "target_pool": None},
        {"task_type": "scrub", "target_pool": "../outside"},
        {"task_type": "scrub", "target_pool": "/"},
    ],
)
async def test_stored_invalid_targets_fail_at_run_time(env, overrides):
    ok, message = await scheduler.execute_task(ScheduledTask(**_task(**overrides)))
    assert not ok
    assert message
    env.run.assert_not_called()


async def test_snapshot_task_creates_snapshot_dir_and_snapshot(env):
    ok, message = await scheduler.execute_task(ScheduledTask(**_task(keep_count=None)))
    assert ok, message
    (command,) = _commands(env)
    assert command[:5] == ["btrfs", "subvolume", "snapshot", "-r", str(env.pool)]
    assert os.path.dirname(command[5]) == str(env.pool / ".snapshots")
    assert os.path.basename(command[5]).startswith("auto_")
    assert (env.pool / ".snapshots").is_dir()


async def test_snapshot_task_refuses_symlinked_snapshot_dir(env):
    os.symlink(env.outside, env.pool / ".snapshots")
    ok, message = await scheduler.execute_task(ScheduledTask(**_task()))
    assert not ok
    assert "symbolic link" in message
    env.run.assert_not_called()


async def test_snapshot_task_requires_mounted_pool(env, monkeypatch):
    ok, message = await scheduler.execute_task(ScheduledTask(**_task(target_pool="missing")))
    assert not ok
    env.run.assert_not_called()

    # An existing but unmounted mount point gets no .snapshots directory.
    monkeypatch.setattr(scheduler, "_pool_is_mounted", lambda pool: False)
    ok, message = await scheduler.execute_task(ScheduledTask(**_task()))
    assert not ok and message == f"Pool data is not mounted at {env.pool}"
    assert not (env.pool / ".snapshots").exists()
    env.run.assert_not_called()


def test_a_scheduled_snapshot_of_an_unmounted_pool_is_recorded_as_failed(env, monkeypatch):
    """What the Scheduler's history shows for the run: failed, with the reason."""
    task = env.client.post("/api/v1/scheduler/tasks", json=_task(keep_count=3)).json()
    monkeypatch.setattr(scheduler, "_pool_is_mounted", lambda pool: False)

    env.client.post(f"/api/v1/scheduler/tasks/{task['id']}/run")

    (run,) = env.client.get(f"/api/v1/scheduler/runs/{task['id']}").json()
    assert (run["status"], run["error"], run["output"]) == ("failed", f"Pool data is not mounted at {env.pool}", None)
    (stored,) = env.client.get("/api/v1/scheduler/tasks").json()
    assert stored["last_status"] == "failed"
    assert not (env.pool / ".snapshots").exists()
    env.run.assert_not_called()


async def test_scrub_task(env):
    ok, _ = await scheduler.execute_task(ScheduledTask(**_task(task_type="scrub")))
    assert ok
    assert _commands(env) == [["btrfs", "scrub", "start", str(env.pool)]]
    assert env.run.call_args.kwargs["timeout"] == scheduler.SCRUB_START_TIMEOUT == 60


async def test_scrub_of_an_unmounted_pool_fails_without_btrfs(env, monkeypatch):
    """Not mounted, the pool's path is a folder on the root filesystem: btrfs would scrub that one if it is btrfs."""
    monkeypatch.setattr(scheduler, "_pool_is_mounted", lambda pool: False)

    ok, message = await scheduler.execute_task(ScheduledTask(**_task(task_type="scrub")))

    assert not ok and message == f"Pool data is not mounted at {env.pool}"
    env.run.assert_not_called()


async def test_hanging_scrub_start_is_recorded_as_timed_out(env):
    """A hung filesystem must not block a worker thread and leave the run "running"."""

    def btrfs(cmd, **kwargs):
        if kwargs.get("timeout") is None:
            raise AssertionError("btrfs scrub start was run without a timeout and would never return")
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    env.run.side_effect = btrfs

    ok, message = await scheduler.execute_task(ScheduledTask(**_task(task_type="scrub")))

    assert not ok
    assert message == (
        "btrfs scrub start timed out: data did not answer within 60 seconds. "
        "The pool may not be responding."
    )


# The env fixture replaces subprocess.run with a mock; the next test runs a fake btrfs.
_REAL_RUN = subprocess.run
# What the fake btrfs does for "scrub start". Without -B, btrfs-progs forks the
# process that runs the scrub and returns at once; that process keeps the
# standard output and error of btrfs open (it closes none of them) until the
# scrub ends, hours later on a large pool.
FAKE_SCRUB_START = {
    "running": 'sleep 300 &\necho $! > "$0.pid"\necho "scrub started on $3, fsid 4d1c2b3a (pid=$!)"\n',
    "refused": 'echo "ERROR: not a btrfs filesystem: $3" >&2\nexit 1\n',
    "hanging": "exec sleep 300\n",
}


@pytest.mark.parametrize("btrfs", FAKE_SCRUB_START)
def test_a_scheduled_scrub_is_recorded_when_btrfs_returns(env, monkeypatch, btrfs):
    """Not when the scrub ends: the run read the output of btrfs through pipes
    up to their end, which the process of the scrub holds, and so recorded a
    scrub longer than SCRUB_START_TIMEOUT as timed out ("The pool may not be
    responding") while it ran."""
    monkeypatch.setattr(scheduler, "SCRUB_START_TIMEOUT", 5)
    bin_dir = env.tmp / "bin"
    bin_dir.mkdir()
    (bin_dir / "btrfs").write_text("#!/bin/sh\n" + FAKE_SCRUB_START[btrfs])
    (bin_dir / "btrfs").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    env.run.side_effect = _REAL_RUN
    task = env.client.post("/api/v1/scheduler/tasks", json=_task(name="Scrub", task_type="scrub")).json()
    scrub = bin_dir / "btrfs.pid"
    try:
        env.client.post(f"/api/v1/scheduler/tasks/{task['id']}/run")

        assert _commands(env) == [["btrfs", "scrub", "start", str(env.pool)]]
        (run,) = env.client.get(f"/api/v1/scheduler/runs/{task['id']}").json()
        (stored,) = env.client.get("/api/v1/scheduler/tasks").json()
        assert run["finished_at"]
        if btrfs == "running":
            os.kill(int(scrub.read_text()), 0)  # still running: the run did not wait for it
            assert (run["status"], run["output"], run["error"]) == ("success", "Scrub started", None)
        elif btrfs == "refused":
            assert (run["status"], run["error"]) == ("failed", f"ERROR: not a btrfs filesystem: {env.pool}\n")
        else:
            assert (run["status"], run["error"]) == (
                "failed",
                "btrfs scrub start timed out: data did not answer within 5 seconds. The pool may not be responding.",
            )
        assert stored["last_status"] == run["status"]
    finally:
        if scrub.exists():
            with contextlib.suppress(ProcessLookupError):
                os.kill(int(scrub.read_text()), signal.SIGKILL)


async def test_cleanup_only_deletes_real_auto_snapshots(env):
    snaps = env.pool / ".snapshots"
    snaps.mkdir()
    for day in (1, 2, 3):
        (snaps / f"auto_2025010{day}_000000").mkdir()
    (snaps / "manual").mkdir()
    victim = env.base / "victim"
    victim.mkdir()
    os.symlink(victim, snaps / "auto_29990101_000000")  # newest by name, but a symlink

    def fake_run(cmd, **kwargs):
        result = MagicMock(returncode=0, stdout="", stderr="")
        if cmd[:3] == ["btrfs", "subvolume", "show"]:
            stamp = os.path.basename(cmd[3])[5:13]
            result.stdout = f"\tCreation time: \t\t{stamp[:4]}-{stamp[4:6]}-{stamp[6:]} 00:00:00 +0000\n"
        return result

    env.run.side_effect = fake_run
    message = await scheduler.cleanup_old_snapshots("data", 1)

    deleted = [c[3] for c in _commands(env) if c[:3] == ["btrfs", "subvolume", "delete"]]
    assert sorted(os.path.basename(d) for d in deleted) == ["auto_20250101_000000", "auto_20250102_000000"]
    assert all(str(victim) not in " ".join(c) for c in _commands(env))
    assert "Cleaned up 2" in message


def test_a_snapshot_task_with_keep_count_3_keeps_the_three_newest_auto_snapshots(env):
    """Run through the API: the new snapshot and the two newest older ones stay, and nothing else goes.

    Snapshots are ordered by the creation time that btrfs reports, not by their names.
    """
    snaps = env.pool / ".snapshots"
    snaps.mkdir()
    created = {
        "auto_20250101_000000": "2025-01-01 00:00:00",
        "auto_20250102_000000": "2025-01-02 00:00:00",
        # Its name sorts first, but btrfs made it last of these four: kept as the second newest.
        "auto_20240101_000000": "2025-01-04 00:00:00",
        "auto_20250103_000000": "2025-01-03 00:00:00",
    }
    for name in created:
        (snaps / name).mkdir()
    # Not automatic snapshots: made by hand or by another tool.
    others = ["manual", "before upgrade", "autosave_20200101_000000", "snap_20200101_000000"]
    for name in others:
        (snaps / name).mkdir()
    victim = env.base / "victim"
    victim.mkdir()
    os.symlink(victim, snaps / "auto_29990101_000000")  # newest by name, but a symlink
    existing = set(created)
    task = env.client.post("/api/v1/scheduler/tasks", json=_task(keep_count=3)).json()
    assert task["keep_count"] == 3

    def btrfs(cmd, **kwargs):
        result = MagicMock(returncode=0, stdout="", stderr="")
        if cmd[:4] == ["btrfs", "subvolume", "snapshot", "-r"]:
            os.mkdir(cmd[5])
            created[os.path.basename(cmd[5])] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        elif cmd[:3] == ["btrfs", "subvolume", "show"]:
            result.stdout = f"\tCreation time: \t\t{created[os.path.basename(cmd[3])]} +0000\n"
        elif cmd[:3] == ["btrfs", "subvolume", "delete"]:
            os.rmdir(cmd[3])
        else:
            raise AssertionError(f"unexpected command: {cmd}")
        return result

    env.run.side_effect = btrfs

    env.client.post(f"/api/v1/scheduler/tasks/{task['id']}/run")

    (run,) = env.client.get(f"/api/v1/scheduler/runs/{task['id']}").json()
    (new,) = set(created) - existing
    assert (run["status"], run["output"]) == ("success", f"Snapshot {new} created. Cleaned up 2 old snapshot(s)")
    assert sorted(path.name for path in snaps.iterdir()) == sorted(
        [new, "auto_20240101_000000", "auto_20250103_000000", "auto_29990101_000000", *others]
    )
    deleted = [os.path.basename(c[3]) for c in _commands(env) if c[:3] == ["btrfs", "subvolume", "delete"]]
    assert sorted(deleted) == ["auto_20250101_000000", "auto_20250102_000000"]
    assert all(str(victim) not in " ".join(c) for c in _commands(env))
    assert victim.is_dir()


async def test_scheduled_backup_passes_restore_validation(env, monkeypatch):
    """Scheduled backups copy the DB consistently and are accepted by the hardened restore."""
    import sqlite3

    backup_router = importlib.import_module("src.backup.router")
    db_file = env.tmp / "unos.db"
    conn = sqlite3.connect(db_file)
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, hashed_password TEXT, is_admin BOOLEAN)")
    conn.execute("INSERT INTO users (username, hashed_password, is_admin) VALUES ('admin', 'h', 1)")
    conn.commit()  # keep the connection open, as the running supervisor does
    backups = env.tmp / "backups"
    monkeypatch.setattr(scheduler, "SCHEDULED_BACKUP_DIR", str(backups))
    monkeypatch.setattr(scheduler, "DB_FILE", str(db_file))

    ok, message = await scheduler.execute_task(ScheduledTask(**_task(task_type="backup", target_pool=None)))
    conn.close()

    assert ok, message
    (archive,) = backups.glob("auto_backup_*.tar.gz")
    assert backup_router._inspect_archive(archive)["type"] == "scheduled"
    extracted = env.tmp / "extracted"
    extracted.mkdir()
    with backup_router._open_tar(_gunzip(archive, env.tmp / "a.tar")) as tar:
        backup_router._extract(tar, backup_router._scan_members(tar), extracted)
    backup_router._verify_database(extracted / "unos.db")


def _gunzip(src, dest):
    import gzip
    import shutil

    with gzip.open(src, "rb") as fin, open(dest, "wb") as fout:
        shutil.copyfileobj(fin, fout)
    return dest


async def test_custom_command_runs_in_a_thread_with_shell(env):
    env.run.return_value = MagicMock(returncode=0, stdout="hello\n", stderr="")
    ok, output = await scheduler.execute_task(ScheduledTask(**_task(task_type="custom", command="echo hello")))
    assert ok and output == "hello\n"
    call = env.run.call_args
    assert call.args[0] == "echo hello" and call.kwargs["shell"] is True
