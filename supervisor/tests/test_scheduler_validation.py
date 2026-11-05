"""Scheduler tasks: admin-only, validated targets at creation and at run time."""

import importlib
import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.auth.utils import verify_token
from src.main import app
from src.scheduler.schemas import ScheduledTask

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
    with patch("src.security.validators.settings", SimpleNamespace(pools_base_path=str(base))):
        with patch("src.scheduler.router.subprocess.run") as run:
            run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            yield SimpleNamespace(client=TestClient(app), base=base, pool=base / "data",
                                  outside=tmp_path / "outside", run=run, tmp=tmp_path)
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


@pytest.mark.parametrize("disk", ["/dev//dev/sda", "sda; reboot", "../sda", "/dev/../etc/passwd", "sda\n", "", None])
def test_invalid_disk_targets_rejected(env, disk):
    response = env.client.post(
        "/api/v1/scheduler/tasks", json=_task(task_type="smart_test", target_pool=None, target_disk=disk)
    )
    assert response.status_code == 400
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
    assert not ok and "not mounted" in message
    assert not (env.pool / ".snapshots").exists()
    env.run.assert_not_called()


async def test_scrub_task(env):
    ok, _ = await scheduler.execute_task(ScheduledTask(**_task(task_type="scrub")))
    assert ok
    assert _commands(env) == [["btrfs", "scrub", "start", str(env.pool)]]


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
