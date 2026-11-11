"""Scheduler tasks: admin-only, validated targets at creation and at run time."""

import asyncio
import contextlib
import importlib
import io
import json
import os
import re
import signal
import sqlite3
import stat
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from cryptography.exceptions import InvalidTag
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src import host as host_module
from src.auth.utils import verify_token
from src.main import app
from src.scheduler.schemas import ScheduledTask
from src.storage import pools

from .test_pool_persistence import install_fake_host
from .test_snapshots import FakeBtrfs, local_time_9_hours_ahead_of_utc

scheduler = importlib.import_module("src.scheduler.router")
backup_router = importlib.import_module("src.backup.router")

ADMIN = SimpleNamespace(id=1, username="admin", is_admin=True)
USER = SimpleNamespace(id=2, username="user", is_admin=False)


@pytest.fixture
def env(tmp_path, monkeypatch):
    yield from make_env(tmp_path, monkeypatch)


class RecordedRuns(set):
    """scheduler._runs, which also keeps every run added to it until waiting_for_runs takes it: a run
    that ended at once has left the set by then."""

    def __init__(self) -> None:
        super().__init__()
        self.added: list[asyncio.Task] = []

    def add(self, run: asyncio.Task) -> None:
        self.added.append(run)
        super().add(run)


def waiting_for_runs(asgi_app):
    """ASGI_APP, whose requests return once the runs of tasks that they started have ended, and raise
    what a run raised, as the tests read them. Run now answers at once and its run goes on beside the
    request, on the event loop of the TestClient (scheduler.run_task_now), which a TestClient outside a
    with block ends with each request. The env puts RecordedRuns into scheduler._runs."""

    async def app_waiting_for_runs(scope, receive, send):
        await asgi_app(scope, receive, send)
        if scope["type"] != "http":
            return
        runs = scheduler._runs
        if isinstance(runs, RecordedRuns):
            added, runs.added = runs.added, []
        else:
            added = list(runs)
        loop = asyncio.get_running_loop()
        ended = await asyncio.gather(*[run for run in added if run.get_loop() is loop], return_exceptions=True)
        for result in ended:
            if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
                raise result

    return app_waiting_for_runs


def test_a_run_of_run_now_that_raises_fails_the_request_of_these_tests(env, monkeypatch):
    """waiting_for_runs keeps what a run raised: a test that reads the run would not see it."""
    async def broken(task):
        raise RuntimeError("the run broke")

    task = env.client.post("/api/v1/scheduler/tasks", json=_task(task_type="custom", target_pool=None,
                                                                 keep_count=None, command="true")).json()
    monkeypatch.setattr(scheduler, "run_task", broken)
    with pytest.raises(RuntimeError, match="the run broke"):
        env.client.post(f"/api/v1/scheduler/tasks/{task['id']}/run")


def make_env(tmp_path, monkeypatch):
    """The env fixture, also of test_custom_tasks.py and test_scheduler_time_zone.py."""
    base = tmp_path.resolve() / "pools"
    (base / "data").mkdir(parents=True)
    (tmp_path / "outside").mkdir()
    monkeypatch.setattr(scheduler, "TASKS_FILE", str(tmp_path / "tasks.json"))
    monkeypatch.setattr(scheduler, "RUNS_FILE", str(tmp_path / "runs.json"))
    monkeypatch.setattr(scheduler, "BACKUP_KEYS_FILE", str(tmp_path / "backup_keys.json"))
    monkeypatch.setattr(scheduler, "_runs", RecordedRuns())
    # schedule_zone starts afresh: no zone read from the host yet, no problem with it logged.
    monkeypatch.setattr(scheduler, "_last_zone", None)
    monkeypatch.setattr(scheduler, "_zone_problem", None)
    app.dependency_overrides[verify_token] = lambda: ADMIN
    # Temporary directories are not mount points; treat existing pool directories as mounted.
    monkeypatch.setattr(scheduler, "_pool_is_mounted", scheduler._real_dir)
    monkeypatch.setattr(pools, "_btrfs_mounted_at", scheduler._real_dir)
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
        # smartctl and btrfs run in the container: SMART tests, scrub starts and the snapshot
        # cleanup's btrfs subvolume show through host.run_in_container, the snapshot commands
        # through subprocess.run. One mock records them all; custom commands use neither
        # (run_command).
        with patch("src.scheduler.router.subprocess.run") as run, patch("src.host.run_in_container", run):
            run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            yield SimpleNamespace(client=TestClient(waiting_for_runs(app)), base=base, pool=base / "data",
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
        ("GET", f"/api/v1/scheduler/runs/{task['id']}", None),
        ("GET", "/api/v1/scheduler/timezone", None),
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


# --- editing a task that ran ------------------------------------------------------------


def test_editing_a_task_that_ran_keeps_its_last_run_and_last_status(env):
    """PUT stored the task as sent, and the Scheduler tab sends neither field: an edit cleared both (ADR-0175).

    They are the results of runs: what a client sends for them is ignored.
    """
    task = _create_custom_task(env)
    env.client.post(f"/api/v1/scheduler/tasks/{task['id']}/run")
    (ran,) = _stored(env)
    assert ran["last_status"] == "success" and ran["last_run"]
    url = f"/api/v1/scheduler/tasks/{task['id']}"

    edits = {
        5: _task(task_type="custom", target_pool=None, command="true", hour=5),  # as the Scheduler tab sends it
        6: _task(task_type="custom", target_pool=None, command="true", hour=6, last_run=None, last_status=None),
        7: _task(task_type="custom", target_pool=None, command="true", hour=7,
                 last_run="2020-01-01T00:00:00", last_status="failed"),
    }
    for hour, body in edits.items():
        response = env.client.put(url, json=body)
        assert response.status_code == 200, response.text
        assert (response.json()["last_run"], response.json()["last_status"]) == (ran["last_run"], "success")
        (stored,) = _stored(env)
        assert (stored["hour"], stored["last_run"], stored["last_status"]) == (hour, ran["last_run"], "success")
    (listed,) = env.client.get("/api/v1/scheduler/tasks").json()
    assert (listed["last_run"], listed["last_status"]) == (ran["last_run"], "success")


def test_an_edit_keeps_the_result_of_a_run_that_ends_while_the_disk_is_checked(env, monkeypatch):
    """update_task reads the tasks again after the disk check; the run's result comes from that read."""
    task = _create_smart_task(env, "/dev/sda")
    real_disk_device = scheduler.disk_device

    def a_run_ends_during_the_check(target_disk):
        tasks = scheduler.load_tasks()
        tasks[0].last_run, tasks[0].last_status = "2026-01-02T03:04:05", "failed"
        scheduler.save_tasks(tasks)
        return real_disk_device(target_disk)

    monkeypatch.setattr(scheduler, "disk_device", a_run_ends_during_the_check)
    response = env.client.put(
        f"/api/v1/scheduler/tasks/{task['id']}",
        json=_task(task_type="smart_test", target_pool=None, target_disk="/dev/nvme0n1"),
    )

    assert response.status_code == 200, response.text
    assert response.json()["last_status"] == "failed"
    (stored,) = _stored(env)
    assert (stored["target_disk"], stored["last_run"], stored["last_status"]) == (
        "/dev/nvme0n1", "2026-01-02T03:04:05", "failed"
    )


# --- backup tasks and their passphrase (ADR-0325) ---------------------------------------

PASSPHRASE = "correct horse battery"
NO_PASSPHRASE = "A backup task needs a passphrase: its backups are encrypted with it"


def _backup_task(**overrides) -> dict:
    return _task(**{"task_type": "backup", "target_pool": None, "keep_count": 3, "passphrase": PASSPHRASE,
                    **overrides})


def _keys(env) -> dict:
    """The stored keys of the backup tasks, by task id."""
    return json.loads((env.tmp / "backup_keys.json").read_text())


@pytest.fixture
def backup_env(env, monkeypatch):
    """env with a database to back up (kept open, as the running supervisor does) and a backup directory."""
    db_file = env.tmp / "unos.db"
    conn = sqlite3.connect(db_file)
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, hashed_password TEXT, is_admin BOOLEAN)")
    conn.execute("INSERT INTO users (username, hashed_password, is_admin) VALUES ('admin', 'h', 1)")
    conn.commit()
    monkeypatch.setattr(scheduler, "SCHEDULED_BACKUP_DIR", str(env.tmp / "backups"))
    monkeypatch.setattr(scheduler, "DB_FILE", str(db_file))
    env.backups = env.tmp / "backups"
    yield env
    conn.close()


def _run_now(env, task_id: str) -> dict:
    """Run the task now and return its run."""
    assert env.client.post(f"/api/v1/scheduler/tasks/{task_id}/run").status_code == 200
    return env.client.get(f"/api/v1/scheduler/runs/{task_id}").json()[-1]


def _decrypted(path, passphrase: str) -> bytes:
    return backup_router.decrypt_file(path.read_bytes(), passphrase)


@pytest.mark.parametrize("passphrase", [None, ""])
def test_a_backup_task_needs_a_passphrase(env, passphrase):
    response = env.client.post("/api/v1/scheduler/tasks", json=_backup_task(passphrase=passphrase))

    assert response.status_code == 400, response.text
    assert response.json()["detail"] == NO_PASSPHRASE
    assert _stored(env) == []
    assert not (env.tmp / "backup_keys.json").exists()


def test_a_passphrase_has_at_least_8_characters(env):
    response = env.client.post("/api/v1/scheduler/tasks", json=_backup_task(passphrase="7 chars"))
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "The passphrase must have at least 8 characters"
    assert _stored(env) == []

    task = env.client.post("/api/v1/scheduler/tasks", json=_backup_task(passphrase="8 chars!")).json()
    keys = _keys(env)
    response = env.client.put(f"/api/v1/scheduler/tasks/{task['id']}", json=_backup_task(passphrase="short"))
    assert response.status_code == 400, response.text
    assert _keys(env) == keys


def test_a_passphrase_that_is_not_unicode_text_is_refused(env):
    # A lone surrogate, which JSON can carry and UTF-8 cannot encode.
    body = json.dumps(_backup_task(passphrase="PLACEHOLDER")).replace("PLACEHOLDER", "passphrase \\ud800")
    response = env.client.post(
        "/api/v1/scheduler/tasks", content=body, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "The passphrase must be valid Unicode text"
    assert _stored(env) == []


def test_the_passphrase_is_write_only(env):
    """No answer of the API and not the tasks file (every scheduled backup holds a copy) contains it."""
    created = env.client.post("/api/v1/scheduler/tasks", json=_backup_task())
    assert created.status_code == 200, created.text
    task_id = created.json()["id"]
    listed = env.client.get("/api/v1/scheduler/tasks")
    updated = env.client.put(f"/api/v1/scheduler/tasks/{task_id}", json=_backup_task(hour=4))
    assert updated.status_code == 200, updated.text

    for answer in (created, listed, updated):
        assert PASSPHRASE not in answer.text and "passphrase" not in answer.text
    tasks_file = (env.tmp / "tasks.json").read_text()
    assert PASSPHRASE not in tasks_file and "passphrase" not in tasks_file
    # The key derived from it is kept apart, readable by root only, without the passphrase.
    keys_file = env.tmp / "backup_keys.json"
    assert stat.S_IMODE(keys_file.stat().st_mode) == 0o600
    assert list(_keys(env)) == [task_id]
    assert PASSPHRASE not in keys_file.read_text()
    # Nor what a log line or an error text with a task in it would show.
    task = ScheduledTask(**_backup_task())
    assert PASSPHRASE not in repr(task) and PASSPHRASE not in str(task)


def test_the_task_list_says_which_backup_tasks_have_a_key(env):
    """has_backup_key: whether an edit without a passphrase keeps a key, so that the edit form asks
    for one when it would not, as for a task made before ADR-0240 or one whose key cannot be used.

    Derived from the key file for each answer and never stored; None for the other kinds of task.
    """
    created = env.client.post("/api/v1/scheduler/tasks", json=_backup_task())
    other = env.client.post("/api/v1/scheduler/tasks", json=_task(has_backup_key=True))
    assert (created.json()["has_backup_key"], other.json()["has_backup_key"]) == (True, None)
    with_key, snapshot = created.json()["id"], other.json()["id"]
    # A backup task in the tasks file without an entry in the key file.
    (env.tmp / "tasks.json").write_text(
        json.dumps(_stored(env) + [_task(id="task_by_hand", task_type="backup", target_pool=None)])
    )

    def listed() -> dict:
        response = env.client.get("/api/v1/scheduler/tasks")
        assert response.status_code == 200, response.text
        return {task["id"]: task["has_backup_key"] for task in response.json()}

    assert listed() == {with_key: True, snapshot: None, "task_by_hand": False}
    keys_file = env.tmp / "backup_keys.json"
    keys = keys_file.read_bytes()
    keys_file.write_bytes(b"\xff not json")  # none of its keys can be used
    assert listed() == {with_key: False, snapshot: None, "task_by_hand": False}
    keys_file.write_bytes(keys)

    # Setting the passphrase gives the task its key; what a client sends for the flag is ignored.
    response = env.client.put("/api/v1/scheduler/tasks/task_by_hand", json=_backup_task(has_backup_key=False))
    assert response.status_code == 200, response.text
    assert response.json()["has_backup_key"] is True
    # An edit without a passphrase keeps the key; the other kinds of task have none.
    kept = env.client.put(f"/api/v1/scheduler/tasks/{with_key}", json=_backup_task(passphrase=None, hour=5))
    edited = env.client.put(f"/api/v1/scheduler/tasks/{snapshot}", json=_task(hour=5, has_backup_key=True))
    assert (kept.json()["has_backup_key"], edited.json()["has_backup_key"]) == (True, None)
    assert listed() == {with_key: True, snapshot: None, "task_by_hand": True}
    assert "has_backup_key" not in (env.tmp / "tasks.json").read_text()


def test_the_task_list_loads_when_the_key_file_cannot_be_read(env):
    """Edits and runs then fail with the reason (test_a_key_file_that_cannot_be_read_is_never_replaced),
    but the tasks are still listed, without saying whether a backup task has a key."""
    task = env.client.post("/api/v1/scheduler/tasks", json=_backup_task()).json()
    keys_file = env.tmp / "backup_keys.json"
    keys_file.unlink()
    keys_file.mkdir()  # reading it fails with an OSError (EISDIR)

    response = env.client.get("/api/v1/scheduler/tasks")

    assert response.status_code == 200, response.text
    assert [(t["id"], t["has_backup_key"]) for t in response.json()] == [(task["id"], None)]


def test_a_backup_task_stores_encrypted_backups(backup_env, monkeypatch):
    env = backup_env
    # The host's smb.conf, as the supervisor's container sees it: a scheduled backup holds none
    # either (backup.no-old-backup-import, ADR-0319).
    from .test_backup_restore import _pretend_the_hosts_smb_conf_exists

    _pretend_the_hosts_smb_conf_exists(monkeypatch)
    task = env.client.post("/api/v1/scheduler/tasks", json=_backup_task()).json()

    run = _run_now(env, task["id"])

    assert run["status"] == "success", run
    (backup,) = env.backups.iterdir()
    assert re.fullmatch(r"auto_backup_\d{8}_\d{6}\.tar\.gz\.enc", backup.name)
    assert run["output"] == f"Backup {backup.name} created."
    with pytest.raises(InvalidTag):
        _decrypted(backup, "not the passphrase")
    with tarfile.open(fileobj=io.BytesIO(_decrypted(backup, PASSPHRASE)), mode="r:gz") as tar:
        names = tar.getnames()
        archived_tasks = json.load(tar.extractfile("scheduled_tasks.json"))
    assert names[0] == "manifest.json" and "unos.db" in names
    assert set(names) <= {"manifest.json", "unos.db", "scheduled_tasks.json"}  # no fstab (ADR-0342)
    assert [t["id"] for t in archived_tasks] == [task["id"]]
    assert "passphrase" not in archived_tasks[0]
    (stored,) = env.client.get("/api/v1/scheduler/tasks").json()
    assert stored["last_status"] == "success"


def test_an_edit_without_a_passphrase_keeps_it_and_a_new_one_applies_to_later_backups(backup_env):
    env = backup_env
    task = env.client.post("/api/v1/scheduler/tasks", json=_backup_task()).json()
    url = f"/api/v1/scheduler/tasks/{task['id']}"
    keys_file = env.tmp / "backup_keys.json"
    written = (keys_file.stat().st_ino, keys_file.stat().st_mtime_ns, keys_file.read_text())

    # The Scheduler tab sends no passphrase when the field is left empty.
    for body in (_backup_task(passphrase=None, hour=5), _backup_task(passphrase="", hour=6)):
        response = env.client.put(url, json=body)
        assert response.status_code == 200, response.text
    assert (keys_file.stat().st_ino, keys_file.stat().st_mtime_ns, keys_file.read_text()) == written
    assert _run_now(env, task["id"])["status"] == "success"
    (first,) = env.backups.iterdir()
    _decrypted(first, PASSPHRASE)

    assert env.client.put(url, json=_backup_task(passphrase="a new passphrase")).status_code == 200
    assert _run_now(env, task["id"])["status"] == "success"
    (second,) = set(env.backups.iterdir()) - {first}
    _decrypted(second, "a new passphrase")
    with pytest.raises(InvalidTag):
        _decrypted(second, PASSPHRASE)
    _decrypted(first, PASSPHRASE)  # a backup keeps the passphrase it was made with


def test_a_task_changed_to_a_backup_task_needs_a_passphrase(env):
    task = env.client.post("/api/v1/scheduler/tasks", json=_task()).json()
    url = f"/api/v1/scheduler/tasks/{task['id']}"
    before = _stored(env)

    response = env.client.put(url, json=_backup_task(passphrase=None))
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == NO_PASSPHRASE
    assert _stored(env) == before
    assert not (env.tmp / "backup_keys.json").exists()

    assert env.client.put(url, json=_backup_task()).status_code == 200
    assert list(_keys(env)) == [task["id"]]


def test_the_key_goes_with_its_backup_task(env):
    """Deleting a backup task, or making it another kind of task, removes its key."""
    first, second = (env.client.post("/api/v1/scheduler/tasks", json=_backup_task(name=name)).json()
                     for name in ("A", "B"))
    assert sorted(_keys(env)) == sorted([first["id"], second["id"]])

    assert env.client.put(f"/api/v1/scheduler/tasks/{first['id']}", json=_task()).status_code == 200
    assert list(_keys(env)) == [second["id"]]
    assert env.client.delete(f"/api/v1/scheduler/tasks/{second['id']}").status_code == 200
    assert _keys(env) == {}


def test_a_key_file_that_cannot_be_read_is_never_replaced(backup_env):
    """A read error is not an empty file: writing then would drop the keys of the other tasks."""
    env = backup_env
    task = env.client.post("/api/v1/scheduler/tasks", json=_backup_task(name="A")).json()
    keys_file = env.tmp / "backup_keys.json"
    keys = keys_file.read_bytes()
    keys_file.unlink()
    keys_file.mkdir()  # reading it fails with an OSError (EISDIR)
    stored = _stored(env)

    for method, url, body in [
        ("POST", "/api/v1/scheduler/tasks", _backup_task(name="B")),
        ("PUT", f"/api/v1/scheduler/tasks/{task['id']}", _backup_task(passphrase="a new passphrase")),
        ("PUT", f"/api/v1/scheduler/tasks/{task['id']}", _task()),
        ("DELETE", f"/api/v1/scheduler/tasks/{task['id']}", None),
    ]:
        response = env.client.request(method, url, json=body)
        assert response.status_code == 500, (method, url, response.text)
        assert "The keys of the backup tasks cannot be read" in response.json()["detail"]
        # Nothing changed: neither the tasks nor the key file.
        assert _stored(env) == stored and keys_file.is_dir()
    run = _run_now(env, task["id"])
    assert run["status"] == "failed" and "The keys of the backup tasks cannot be read" in run["error"]
    assert not env.backups.exists()

    keys_file.rmdir()
    keys_file.write_bytes(keys)
    assert _run_now(env, task["id"])["status"] == "success"


def test_a_key_file_that_is_not_json_counts_as_empty_until_a_passphrase_is_set(backup_env):
    """Its keys cannot be used; the next passphrase replaces the file (written by hand, for example)."""
    env = backup_env
    task = env.client.post("/api/v1/scheduler/tasks", json=_backup_task()).json()
    keys_file = env.tmp / "backup_keys.json"
    keys_file.write_bytes(b"\xff not json")

    run = _run_now(env, task["id"])
    assert (run["status"], run["error"]) == ("failed", scheduler.NO_BACKUP_KEY)

    response = env.client.put(f"/api/v1/scheduler/tasks/{task['id']}", json=_backup_task())
    assert response.status_code == 200, response.text
    assert list(_keys(env)) == [task["id"]]
    assert _run_now(env, task["id"])["status"] == "success"


def _path_of(fd: int) -> str:
    """The path of the file that ``fd`` has open."""
    if sys.platform == "darwin":
        import fcntl

        return os.fsdecode(fcntl.fcntl(fd, fcntl.F_GETPATH, bytes(1024))).rstrip("\0")
    return os.readlink(f"/proc/self/fd/{fd}")


def test_the_key_file_is_written_off_the_event_loop_and_reaches_the_disk(env, monkeypatch):
    """fsync of the file and of its folder: after a power cut, a new passphrase must not turn into the old one."""
    import asyncio

    writes: list[bool] = []
    synced: list[str] = []
    real_save, real_fsync = scheduler.save_backup_keys, os.fsync

    def save(keys):
        try:
            asyncio.get_running_loop()
            writes.append(True)  # on the event loop
        except RuntimeError:
            writes.append(False)
        real_save(keys)

    def fsync(fd):
        # The key file and its folder; the tasks' file is synced too (ADR-0324), not counted here.
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            synced.append("folder")
        elif Path(scheduler.BACKUP_KEYS_FILE).name in _path_of(fd):
            synced.append("file")
        real_fsync(fd)

    monkeypatch.setattr(scheduler, "save_backup_keys", save)
    monkeypatch.setattr(scheduler.os, "fsync", fsync)
    task = env.client.post("/api/v1/scheduler/tasks", json=_backup_task()).json()
    env.client.put(f"/api/v1/scheduler/tasks/{task['id']}", json=_backup_task(passphrase="a new passphrase"))
    env.client.delete(f"/api/v1/scheduler/tasks/{task['id']}")

    assert writes == [False, False, False]
    assert synced == ["file", "folder"] * 3


def test_keys_set_at_the_same_time_are_all_kept(env, monkeypatch):
    """Two keys stored at once: each write reads the file after the other one wrote it."""
    import threading

    both_read = threading.Barrier(2, timeout=0.5)
    real_load = scheduler.load_backup_keys

    def load():
        keys = real_load()
        with contextlib.suppress(threading.BrokenBarrierError):
            both_read.wait()  # without the lock, both writes start from the same keys
        return keys

    monkeypatch.setattr(scheduler, "load_backup_keys", load)
    keys = {f"task_{n}": backup_router.new_backup_key(f"passphrase {n}") for n in range(2)}
    threads = [threading.Thread(target=scheduler.set_backup_key, args=item) for item in keys.items()]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(_keys(env)) == sorted(keys)


def test_a_backup_task_without_a_key_fails_and_stores_no_backup(backup_env):
    """A backup task in a tasks file written by hand, or whose key file was lost."""
    env = backup_env
    (env.tmp / "tasks.json").write_text(json.dumps([_task(id="task_by_hand", task_type="backup", target_pool=None)]))

    run = _run_now(env, "task_by_hand")

    assert (run["status"], run["error"]) == (
        "failed", "No passphrase is set for this backup task: edit the task and set one"
    )
    assert not env.backups.exists()


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


def _create_custom_task(env) -> dict:
    """A custom task running `true` (subprocess.run is mocked: it succeeds)."""
    response = env.client.post(
        "/api/v1/scheduler/tasks", json=_task(task_type="custom", target_pool=None, command="true")
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


def _fake_btrfs(env) -> FakeBtrfs:
    """btrfs-progs on the pool data of ``env`` (FakeBtrfs of test_snapshots.py)."""
    fake = FakeBtrfs(env.pool)
    env.run.side_effect = fake.run
    return fake


async def test_snapshot_task_creates_snapshot_dir_and_snapshot(env):
    """As the Snapshots page makes them: of the opened pool into the opened .snapshots (ADR-0242)."""
    btrfs = _fake_btrfs(env)

    ok, message = await scheduler.execute_task(ScheduledTask(**_task(keep_count=None)))

    assert ok, message
    (name,) = os.listdir(env.pool / ".snapshots")
    assert message == f"Snapshot {name} created"
    assert name.startswith("auto_")
    assert btrfs.calls == [["btrfs", "subvolume", "snapshot", "-r", f"{env.pool}/.", f"{env.pool}/.snapshots/{name}"]]


async def test_a_scheduled_snapshot_is_named_after_the_time_in_utc(env):
    """As the Snapshots page and the guide say: the container's clock could be set to another time zone."""
    _fake_btrfs(env)
    before = datetime.now(timezone.utc).replace(microsecond=0)

    with local_time_9_hours_ahead_of_utc():
        ok, message = await scheduler.execute_task(ScheduledTask(**_task(keep_count=None)))

    assert ok, message
    (name,) = os.listdir(env.pool / ".snapshots")
    named = datetime.strptime(name, "auto_%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)
    assert before <= named <= datetime.now(timezone.utc)


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
    monkeypatch.setattr(pools, "_btrfs_mounted_at", lambda pool: False)

    ok, message = await scheduler.execute_task(ScheduledTask(**_task(task_type="scrub")))

    assert not ok and message == f"Pool data is not mounted at {env.pool}"
    env.run.assert_not_called()


async def test_the_storage_page_and_scheduled_scrubs_start_through_one_helper(env, monkeypatch):
    """storage.scrub-needs-mounted-pool, step 2: both start a scrub through pools.scrub_start,
    which checks that the pool is mounted before btrfs runs."""
    started = []

    def scrub_start(pool, timeout):
        started.append((str(pool), timeout))
        return subprocess.CompletedProcess(["btrfs"], 0, None, "")

    monkeypatch.setattr(pools, "scrub_start", scrub_start)
    ok, message = await scheduler.execute_task(ScheduledTask(**_task(task_type="scrub")))
    assert (ok, message) == (True, "Scrub started")
    response = env.client.post("/api/v1/storage/pools/data/scrub")
    assert response.status_code == 200, response.text
    assert started == [(str(env.pool), scheduler.SCRUB_START_TIMEOUT),
                       (pools.pool_mountpoint("data"), pools.SCRUB_COMMAND_TIMEOUT)]


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


# The env fixture replaces subprocess.run and host.run_in_container with one mock; the next
# test runs a fake btrfs through the real host.run_in_container, test_custom_tasks.py runs
# commands through the real subprocess.run.
_REAL_RUN = subprocess.run
_REAL_RUN_IN_CONTAINER = host_module.run_in_container
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
    env.run.side_effect = _REAL_RUN_IN_CONTAINER
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
    btrfs = _fake_btrfs(env)
    snaps = env.pool / ".snapshots"
    for day in (1, 2, 3):
        name = f"auto_2025010{day}_000000"
        btrfs.snapshot(env.pool, name)
        btrfs.state["created"][name] = f"2025-01-0{day} 00:00:00 +0000"
    btrfs.snapshot(env.pool, "manual")
    victim = btrfs.subvolume(env.pool / "victim")
    os.symlink(victim, snaps / "auto_29990101_000000")  # newest by name, but a symlink

    message = await scheduler.cleanup_old_snapshots("data", 1)

    assert sorted(path.name for path in btrfs.deleted) == ["auto_20250101_000000", "auto_20250102_000000"]
    assert all(str(victim) not in " ".join(c) for c in btrfs.calls)
    assert victim.is_dir()
    assert "Cleaned up 2" in message


async def test_cleanup_deletes_the_snapshot_that_was_checked(env):
    """An old auto_ snapshot swapped for a link to a volume before btrfs runs: by path, the volume went (ADR-0242)."""
    btrfs = _fake_btrfs(env)
    snaps = env.pool / ".snapshots"
    for day in (1, 2):
        name = f"auto_2025010{day}_000000"
        btrfs.snapshot(env.pool, name)
        btrfs.state["created"][name] = f"2025-01-0{day} 00:00:00 +0000"
    documents = btrfs.subvolume(env.pool / "documents")
    (documents / "report.txt").write_text("x")

    def swap():
        os.rename(snaps / "auto_20250101_000000", snaps / "moved")
        os.symlink(documents, snaps / "auto_20250101_000000")

    btrfs.before[("btrfs", "subvolume", "delete")] = swap

    message = await scheduler.cleanup_old_snapshots("data", 1)

    assert btrfs.deleted == [snaps / "moved"]
    assert (documents / "report.txt").read_text() == "x"
    assert message == "Cleaned up 1 old snapshot(s)"


def test_a_snapshot_task_with_keep_count_3_keeps_the_three_newest_auto_snapshots(env):
    """Run through the API: the new snapshot and the two newest older ones stay, and nothing else goes.

    Snapshots are ordered by the creation time that btrfs reports, not by their names.
    """
    btrfs = _fake_btrfs(env)
    snaps = env.pool / ".snapshots"
    created = {
        "auto_20250101_000000": "2025-01-01 00:00:00 +0000",
        "auto_20250102_000000": "2025-01-02 00:00:00 +0000",
        # Its name sorts first, but btrfs made it last of these four: kept as the second newest.
        "auto_20240101_000000": "2025-01-04 00:00:00 +0000",
        "auto_20250103_000000": "2025-01-03 00:00:00 +0000",
    }
    for name in created:
        btrfs.snapshot(env.pool, name)
    # Not automatic snapshots: made by hand or by another tool.
    others = ["manual", "before upgrade", "autosave_20200101_000000", "snap_20200101_000000"]
    for name in others:
        btrfs.snapshot(env.pool, name)
    victim = btrfs.subvolume(env.pool / "victim")
    os.symlink(victim, snaps / "auto_29990101_000000")  # newest by name, but a symlink
    existing = set(os.listdir(snaps))
    # The new snapshot gets the time of the fake btrfs show (CREATED, 2025-01-24): the newest.
    btrfs.state["created"] = created
    task = env.client.post("/api/v1/scheduler/tasks", json=_task(keep_count=3)).json()
    assert task["keep_count"] == 3

    env.client.post(f"/api/v1/scheduler/tasks/{task['id']}/run")

    (run,) = env.client.get(f"/api/v1/scheduler/runs/{task['id']}").json()
    (new,) = set(os.listdir(snaps)) - existing
    assert (run["status"], run["output"]) == ("success", f"Snapshot {new} created. Cleaned up 2 old snapshot(s)")
    assert sorted(os.listdir(snaps)) == sorted(
        [new, "auto_20240101_000000", "auto_20250103_000000", "auto_29990101_000000", *others]
    )
    assert sorted(path.name for path in btrfs.deleted) == ["auto_20250101_000000", "auto_20250102_000000"]
    assert all(str(victim) not in " ".join(c) for c in btrfs.calls)
    assert victim.is_dir()


def test_a_snapshot_task_whose_cleanup_btrfs_does_not_answer_is_recorded_as_failed(env):
    """Its keep count was not applied, as on a pool that stopped responding: the task does not show success.

    The new snapshot stays, and no old one is deleted.
    """
    btrfs = _fake_btrfs(env)
    snaps = env.pool / ".snapshots"
    for day in (1, 2):
        btrfs.snapshot(env.pool, f"auto_2025010{day}_000000")
    existing = set(os.listdir(snaps))

    def btrfs_without_show(cmd, *args, **kwargs):
        if list(cmd[:3]) == ["btrfs", "subvolume", "show"]:
            raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])
        return btrfs.run(cmd, *args, **kwargs)

    env.run.side_effect = btrfs_without_show
    task = env.client.post("/api/v1/scheduler/tasks", json=_task(keep_count=1)).json()

    env.client.post(f"/api/v1/scheduler/tasks/{task['id']}/run")

    (run,) = env.client.get(f"/api/v1/scheduler/runs/{task['id']}").json()
    (new,) = set(os.listdir(snaps)) - existing
    assert (run["status"], run["error"], run["output"]) == (
        "failed",
        f"Snapshot {new} created, but old snapshots were not cleaned up: btrfs did not answer within 60 "
        "seconds. The pool may not be responding.",
        None,
    )
    (stored,) = env.client.get("/api/v1/scheduler/tasks").json()
    assert stored["last_status"] == "failed"
    assert btrfs.deleted == []
    assert sorted(os.listdir(snaps)) == sorted([*existing, new])


def test_scheduled_backup_passes_restore_validation(backup_env):
    """Scheduled backups copy the DB consistently and are accepted by the hardened restore."""
    env = backup_env
    task = env.client.post("/api/v1/scheduler/tasks", json=_backup_task()).json()

    assert _run_now(env, task["id"])["status"] == "success"

    (archive,) = env.backups.glob("auto_backup_*.tar.gz.enc")
    plain = env.tmp / "decrypted.tar.gz"
    plain.write_bytes(_decrypted(archive, PASSPHRASE))
    assert backup_router._inspect_archive(plain)["type"] == "scheduled"
    extracted = env.tmp / "extracted"
    extracted.mkdir()
    with backup_router._open_tar(_gunzip(plain, env.tmp / "a.tar")) as tar:
        backup_router._extract(tar, backup_router._scan_members(tar), extracted)
    backup_router._verify_database(extracted / "unos.db")


def _gunzip(src, dest):
    import gzip
    import shutil

    with gzip.open(src, "rb") as fin, open(dest, "wb") as fout:
        shutil.copyfileobj(fin, fout)
    return dest


async def test_custom_command_runs_with_a_shell_off_the_event_loop(env):
    """A shell command (here a pipe), waited for by the event loop, which goes on meanwhile (ADR-0267)."""
    import asyncio

    ticks = 0

    async def tick():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.05)
            ticks += 1

    ticker = asyncio.create_task(tick())
    try:
        result = await scheduler.run_command("sleep 1; echo hello | tr a-z A-Z")
    finally:
        ticker.cancel()
    assert (result.exit_code, result.output, result.error) == (0, "HELLO\n", None)
    assert ticks >= 5
    env.run.assert_not_called()
