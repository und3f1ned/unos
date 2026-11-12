"""Backups, power, scheduled tasks and the host's network and system settings are in the audit log
(security.audit-admin-actions, step 3; ADR-0351).

Creating, uploading, restoring and deleting a backup, scheduling or cancelling a reboot or a
shutdown, turning Wake-on-LAN on or off, creating, changing, enabling, disabling, deleting and
running a scheduled task, disabling, keeping and enabling a network interface, and setting the
host name or the time zone each write one entry of their action, with the administrator, the
client address and browser of the request and the resource. Only a change that was made writes
one, after it was made (ADR-0320): a refused or failed request and one that found nothing to change
write none. A reboot's or shutdown's entry is in the log when the answer comes, a minute before
logind acts. An entry that cannot be written, as on a full /data, is in the supervisor's log, and
the change, which is made already, answers as it does otherwise (as a sign-in does, ADR-0251).

Each area runs on the fakes of its own tests: the backup directory and database of
test_backup_restore.py, the host of test_power.py and test_network.py, the scheduler of
test_scheduler_validation.py, and timedatectl on a fake host as test_system_time_zone.py has it.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

import src.host
from src import host as host_module
from src.audit import router as audit_router
from src.auth.utils import verify_token
from src.host import HostResult
from src.main import app

from .audit_helpers import audit_entries
from .test_backup_restore import MANIFEST, _file, _good_db, _store, _tar_gz
from .test_backup_restore import make_env as make_backup_env
from .test_network import FakeNetworkHost
from .test_power import REBOOT, FakePowerHost, _forbidden
from .test_scheduler_validation import PASSPHRASE, _backup_task, _task, waiting_for_runs
from .test_scheduler_validation import make_env as make_scheduler_env
from .test_sign_in_full_disk import ENTRY_LOST, the_audit_log_is_full

backup_router = importlib.import_module("src.backup.router")
power = importlib.import_module("src.power.router")
scheduler = importlib.import_module("src.scheduler.router")

API = "/api/v1"
CLIENT = "192.0.2.30"  # the address the test client connects from
BROWSER = "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0"
ADMIN = SimpleNamespace(id=1, username="admin", is_admin=True, is_active=True)
FIELDS = ("action", "username", "ip_address", "user_agent", "resource_type", "resource_id", "details",
          "severity", "success")
ZONES = "Etc/UTC\nEurope/Berlin\nEurope/Moscow\n"


def entry(action: str, resource_type: str, resource_id: str | None = None, details: str | None = None,
          severity: str = "info") -> dict:
    """An entry of a change that the administrator made from CLIENT with BROWSER."""
    return {"action": action, "username": "admin", "ip_address": CLIENT, "user_agent": BROWSER,
            "resource_type": resource_type, "resource_id": resource_id, "details": details,
            "severity": severity, "success": True}


def picked(found: dict) -> dict:
    return {field: found.get(field) for field in FIELDS}


def client(asgi_app=app) -> TestClient:
    """A client of ``asgi_app`` at CLIENT, whose requests come from the browser BROWSER."""
    return TestClient(asgi_app, raise_server_exceptions=False, client=(CLIENT, 50000),
                      headers={"User-Agent": BROWSER})


def lost(caplog) -> list[dict]:
    """The entries that the supervisor's log reports in place of the audit log."""
    found = []
    for record in caplog.records:
        message = record.getMessage()
        if record.levelno >= logging.ERROR and message.startswith(ENTRY_LOST):
            found.append(picked(json.JSONDecoder().raw_decode(message, len(ENTRY_LOST))[0]))
    return found


@pytest.fixture
def audit_file(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(audit_router, "AUDIT_FILE", str(path))
    return path


def entries(audit_file: Path) -> list[dict]:
    return [picked(found) for found in audit_entries(audit_file)]


# ---------------------------------------------------------------------------
# Backups
# ---------------------------------------------------------------------------


@pytest.fixture
def backups(tmp_path, monkeypatch, audit_file):
    """The backup directory and live database of test_backup_restore.py; a restore applies smb.conf."""
    for env in make_backup_env(tmp_path, monkeypatch, smb_applied=True):
        env.client = client(waiting_for_runs(app))
        yield env


def _a_backup(env) -> str:
    return _store(env, [_file("manifest.json", MANIFEST), _file("unos.db", _good_db(env))])


class TestBackups:
    def test_a_backup_created_is_in_the_audit_log(self, backups, audit_file):
        response = backups.client.post(f"{API}/backup/create", json={})

        assert response.status_code == 200, response.text
        name = response.json()["backup"]["filename"]
        assert entries(audit_file) == [entry("backup_created", "backup", name, "Backup created")]

    def test_an_encrypted_backup_says_so(self, backups, audit_file):
        response = backups.client.post(f"{API}/backup/create", json={"encrypt": True, "passphrase": PASSPHRASE})

        assert response.status_code == 200, response.text
        name = response.json()["backup"]["filename"]
        assert entries(audit_file) == [entry("backup_created", "backup", name, "Encrypted backup created")]
        assert PASSPHRASE not in audit_file.read_text()

    def test_a_backup_that_could_not_be_created_writes_no_entry(self, backups, audit_file, monkeypatch):
        def no_room(config, content):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(backup_router, "_collect_backup_content", no_room)

        response = backups.client.post(f"{API}/backup/create", json={})

        assert response.status_code == 500
        assert entries(audit_file) == []

    def test_a_backup_uploaded_is_in_the_audit_log(self, backups, audit_file):
        archive = _tar_gz([_file("manifest.json", MANIFEST), _file("unos.db", _good_db(backups))])

        response = backups.client.post(f"{API}/backup/upload",
                                       files={"file": ("mine.tar.gz", archive, "application/gzip")})

        assert response.status_code == 200, response.text
        name = response.json()["backup"]["filename"]
        assert entries(audit_file) == [entry("backup_created", "backup", name, "Backup uploaded")]

    def test_a_refused_upload_writes_no_entry(self, backups, audit_file):
        response = backups.client.post(f"{API}/backup/upload",
                                       files={"file": ("mine.tar.gz", b"not gzip", "application/gzip")})

        assert response.status_code == 400
        assert entries(audit_file) == []

    def test_a_restore_is_in_the_audit_log(self, backups, audit_file):
        name = _a_backup(backups)

        response = backups.client.post(f"{API}/backup/restore/{name}", json={})

        assert response.status_code == 200, response.text
        assert response.json()["restored"] == ["database", "smb_config"]
        assert entries(audit_file) == [
            entry("backup_restored", "backup", name, "Restored database, smb_config", "warning")
        ]

    def test_a_restore_that_restored_nothing_writes_no_entry(self, backups, audit_file):
        name = _store(backups, [_file("manifest.json", MANIFEST)])

        response = backups.client.post(f"{API}/backup/restore/{name}", json={})

        assert response.status_code == 200, response.text
        assert response.json()["restored"] == []
        assert entries(audit_file) == []

    def test_a_refused_restore_writes_no_entry(self, backups, audit_file):
        name = _store(backups, [_file("unos.db", _good_db(backups))])  # no manifest

        response = backups.client.post(f"{API}/backup/restore/{name}", json={})

        assert response.status_code == 400
        assert entries(audit_file) == []

    def test_a_restore_whose_request_was_cancelled_is_in_the_audit_log(self, backups, audit_file, monkeypatch):
        """The restore runs on in its own task when its request is cancelled (restore_backup), and so
        does the writing of its entry."""
        name = _a_backup(backups)
        started, release = threading.Event(), threading.Event()
        restore = backup_router._restore

        def held_restore(*args):
            started.set()
            assert release.wait(10)
            return restore(*args)

        monkeypatch.setattr(backup_router, "_restore", held_restore)
        request = Request({"type": "http", "method": "POST", "path": f"{API}/backup/restore/{name}",
                           "headers": [(b"user-agent", BROWSER.encode())], "client": (CLIENT, 50000)})

        async def cancel_the_request_while_it_restores() -> None:
            answer = asyncio.ensure_future(backup_router.restore_backup(request, name, None, ADMIN))
            assert await asyncio.to_thread(started.wait, 10)
            answer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await answer
            release.set()
            for _ in range(1000):  # the restore's own task ends
                if not backup_router._restore_running:
                    break
                await asyncio.sleep(0.01)

        asyncio.run(cancel_the_request_while_it_restores())

        assert not backup_router._restore_running
        assert entries(audit_file) == [
            entry("backup_restored", "backup", name, "Restored database, smb_config", "warning")
        ]

    def test_a_deleted_backup_is_in_the_audit_log(self, backups, audit_file):
        name = _a_backup(backups)

        response = backups.client.delete(f"{API}/backup/{name}")

        assert response.status_code == 200, response.text
        assert not (backups.backup_dir / name).exists()
        assert entries(audit_file) == [entry("backup_deleted", "backup", name)]

    def test_a_backup_that_is_not_there_writes_no_entry(self, backups, audit_file):
        response = backups.client.delete(f"{API}/backup/unos_backup_20260101_000000.tar.gz")

        assert response.status_code == 404
        assert entries(audit_file) == []


# ---------------------------------------------------------------------------
# Reboot, shutdown and Wake-on-LAN
# ---------------------------------------------------------------------------


@pytest.fixture
def power_host(monkeypatch, tmp_path, audit_file):
    """The host of test_power.py; nothing runs on this machine, so no test reboots it."""
    fake = FakePowerHost()
    monkeypatch.delenv("UNOS_HOST_EXEC", raising=False)
    monkeypatch.setattr(host_module, "run_on_host", fake.run)
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, _forbidden)
    monkeypatch.setattr(os, "system", _forbidden)
    monkeypatch.setattr(power, "WOL_FILE", str(tmp_path / "wol_config.json"))
    app.dependency_overrides[verify_token] = lambda: ADMIN
    power.limiter.reset()
    try:
        yield fake
    finally:
        app.dependency_overrides.pop(verify_token, None)
        power.limiter.reset()


class TestPower:
    def test_a_reboot_is_in_the_audit_log_before_the_host_goes_down(self, power_host, audit_file, monkeypatch):
        """The entry follows the command that scheduled the reboot and is in the log when the answer
        comes, while logind still waits a minute."""
        in_the_log_when_scheduled = []

        def recording(cmd, **kwargs):
            if list(cmd) == REBOOT:
                in_the_log_when_scheduled.extend(audit_entries(audit_file))
            return power_host.run(cmd, **kwargs)

        monkeypatch.setattr(host_module, "run_on_host", recording)

        response = client().post(f"{API}/power/reboot")

        assert response.status_code == 200, response.text
        assert in_the_log_when_scheduled == []
        [written] = audit_entries(audit_file)
        assert picked(written) == entry("system_reboot", "host", None, "The host reboots in 1 minute", "warning")
        acts_at = datetime.fromisoformat(response.json()["pending"]["at"].replace("Z", "+00:00"))
        written_at = datetime.fromisoformat(written["timestamp"]).astimezone(timezone.utc)
        assert written_at < acts_at - timedelta(seconds=50)

    def test_a_shutdown_is_in_the_audit_log(self, power_host, audit_file):
        response = client().post(f"{API}/power/shutdown")

        assert response.status_code == 200, response.text
        assert entries(audit_file) == [
            entry("system_shutdown", "host", None, "The host shuts down in 1 minute", "warning")
        ]

    def test_a_reboot_the_host_refused_writes_no_entry(self, power_host, audit_file):
        power_host.fail[("shutdown",)] = (1, "Failed to schedule shutdown: Access denied")

        response = client().post(f"{API}/power/reboot")

        assert response.status_code == 500
        assert entries(audit_file) == []

    def test_a_cancelled_reboot_is_in_the_audit_log(self, power_host, audit_file):
        assert client().post(f"{API}/power/reboot").status_code == 200

        response = client().post(f"{API}/power/cancel")

        assert response.status_code == 200, response.text
        assert response.json()["status"] == "cancelled"
        assert entries(audit_file)[1:] == [
            entry("system_reboot", "host", None, "The pending reboot was cancelled")
        ]

    def test_a_cancelled_shutdown_is_in_the_audit_log(self, power_host, audit_file):
        assert client().post(f"{API}/power/shutdown").status_code == 200

        assert client().post(f"{API}/power/cancel").status_code == 200

        assert entries(audit_file)[1:] == [
            entry("system_shutdown", "host", None, "The pending shutdown was cancelled")
        ]

    def test_a_cancel_with_nothing_pending_writes_no_entry(self, power_host, audit_file):
        response = client().post(f"{API}/power/cancel")

        assert response.status_code == 200, response.text
        assert response.json()["status"] == "none"
        assert entries(audit_file) == []

    def test_wake_on_lan_turned_on_and_off_is_in_the_audit_log(self, power_host, audit_file):
        assert client().post(f"{API}/power/wol/eth0/enable").status_code == 200
        assert client().post(f"{API}/power/wol/eth0/disable").status_code == 200

        assert entries(audit_file) == [
            entry("network_config_changed", "network_interface", "eth0", "Wake-on-LAN turned on"),
            entry("network_config_changed", "network_interface", "eth0", "Wake-on-LAN turned off"),
        ]

    def test_wake_on_lan_that_stays_as_it_was_writes_no_entry(self, power_host, audit_file):
        assert client().post(f"{API}/power/wol/eth0/enable").status_code == 200

        response = client().post(f"{API}/power/wol/eth0/enable")

        assert response.status_code == 200, response.text
        assert len(entries(audit_file)) == 1

    def test_wake_on_lan_the_interface_does_not_support_writes_no_entry(self, power_host, audit_file):
        response = client().post(f"{API}/power/wol/eth1/enable")

        assert response.status_code == 409
        assert entries(audit_file) == []


# ---------------------------------------------------------------------------
# Scheduled tasks
# ---------------------------------------------------------------------------


@pytest.fixture
def tasks(tmp_path, monkeypatch, audit_file):
    """The scheduler of test_scheduler_validation.py, with the pool "data"."""
    for env in make_scheduler_env(tmp_path, monkeypatch):
        env.client = client(waiting_for_runs(app))
        yield env


def _created(env, **task) -> dict:
    response = env.client.post(f"{API}/scheduler/tasks", json=_task(**task))
    assert response.status_code == 200, response.text
    return response.json()


def _tasks_file(env) -> os.stat_result:
    return os.stat(scheduler.TASKS_FILE)


class TestScheduledTasks:
    def test_a_task_created_is_in_the_audit_log(self, tasks, audit_file):
        _created(tasks)

        assert entries(audit_file) == [entry("task_created", "task", "Nightly", "Created the snapshot task Nightly")]

    def test_a_refused_task_writes_no_entry(self, tasks, audit_file):
        response = tasks.client.post(f"{API}/scheduler/tasks", json=_task(hour=24))

        assert response.status_code == 400
        assert entries(audit_file) == []

    def test_a_changed_task_says_what_changed(self, tasks, audit_file):
        task = _created(tasks)

        response = tasks.client.put(f"{API}/scheduler/tasks/{task['id']}", json=_task(hour=3, keep_count=5))

        assert response.status_code == 200, response.text
        assert entries(audit_file)[1:] == [
            entry("task_updated", "task", "Nightly", "Changed the snapshot task Nightly: hour, keep_count")
        ]

    def test_a_renamed_task_says_its_old_name(self, tasks, audit_file):
        task = _created(tasks)

        response = tasks.client.put(f"{API}/scheduler/tasks/{task['id']}", json=_task(name="Daily"))

        assert response.status_code == 200, response.text
        assert entries(audit_file)[1:] == [
            entry("task_updated", "task", "Daily", "Changed the snapshot task Daily: name (was Nightly)")
        ]

    def test_a_new_passphrase_is_a_change_that_the_entry_names_without_the_passphrase(self, tasks, audit_file):
        created = tasks.client.post(f"{API}/scheduler/tasks", json=_backup_task())
        assert created.status_code == 200, created.text

        response = tasks.client.put(f"{API}/scheduler/tasks/{created.json()['id']}",
                                    json=_backup_task(passphrase="another passphrase"))

        assert response.status_code == 200, response.text
        assert entries(audit_file)[1:] == [
            entry("task_updated", "task", "Nightly", "Changed the backup task Nightly: passphrase")
        ]
        assert PASSPHRASE not in audit_file.read_text()
        assert "another passphrase" not in audit_file.read_text()

    def test_a_task_saved_unchanged_writes_no_entry_and_leaves_its_file(self, tasks, audit_file):
        task = _created(tasks)
        before = _tasks_file(tasks)

        response = tasks.client.put(f"{API}/scheduler/tasks/{task['id']}", json=_task())

        assert response.status_code == 200, response.text
        assert len(entries(audit_file)) == 1
        after = _tasks_file(tasks)
        assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)

    def test_a_task_disabled_and_enabled_is_in_the_audit_log(self, tasks, audit_file):
        task = _created(tasks)

        assert tasks.client.post(f"{API}/scheduler/tasks/{task['id']}/toggle").json()["enabled"] is False
        assert tasks.client.post(f"{API}/scheduler/tasks/{task['id']}/toggle").json()["enabled"] is True

        assert entries(audit_file)[1:] == [
            entry("task_updated", "task", "Nightly", "Disabled the snapshot task Nightly"),
            entry("task_updated", "task", "Nightly", "Enabled the snapshot task Nightly"),
        ]

    def test_a_task_deleted_is_in_the_audit_log(self, tasks, audit_file):
        task = _created(tasks)

        response = tasks.client.delete(f"{API}/scheduler/tasks/{task['id']}")

        assert response.status_code == 200, response.text
        assert entries(audit_file)[1:] == [entry("task_deleted", "task", "Nightly", "Deleted the snapshot task Nightly")]

    def test_a_task_that_is_not_there_is_not_deleted_and_its_file_stays(self, tasks, audit_file):
        _created(tasks)
        before = _tasks_file(tasks)

        response = tasks.client.delete(f"{API}/scheduler/tasks/task_20260101000000_00000000")

        assert response.status_code == 200, response.text
        assert len(entries(audit_file)) == 1
        after = _tasks_file(tasks)
        assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)

    def test_a_run_started_by_hand_is_in_the_audit_log(self, tasks, audit_file):
        task = _created(tasks)

        response = tasks.client.post(f"{API}/scheduler/tasks/{task['id']}/run")

        assert response.status_code == 200, response.text
        assert entries(audit_file)[1:] == [
            entry("task_run", "task", "Nightly", "Started a run of the snapshot task Nightly")
        ]

    def test_a_run_of_a_task_that_is_not_there_writes_no_entry(self, tasks, audit_file):
        response = tasks.client.post(f"{API}/scheduler/tasks/task_20260101000000_00000000/run")

        assert response.status_code == 404
        assert entries(audit_file) == []


# ---------------------------------------------------------------------------
# Network interfaces and the host name
# ---------------------------------------------------------------------------


@pytest.fixture
def network_host(monkeypatch, audit_file):
    """The host of test_network.py: eth0 and eth1 enabled, wlan0 disabled, the host name "unos"."""
    fake = FakeNetworkHost()
    monkeypatch.setattr(host_module, "run_on_host", fake.run)
    monkeypatch.setattr(subprocess, "run", _forbidden)
    monkeypatch.setattr(host_module, "run_in_container", _forbidden)
    app.dependency_overrides[verify_token] = lambda: ADMIN
    try:
        yield fake
    finally:
        app.dependency_overrides.pop(verify_token, None)


class TestNetwork:
    def test_a_disable_its_keep_and_an_enable_are_in_the_audit_log(self, network_host, audit_file):
        assert client().post(f"{API}/network/interfaces/eth1/down").status_code == 200
        assert client().post(f"{API}/network/interfaces/eth1/down/confirm").status_code == 200
        assert client().post(f"{API}/network/interfaces/eth1/up").status_code == 200

        assert entries(audit_file) == [
            entry("network_config_changed", "network_interface", "eth1",
                  "Disabled; it comes back on by itself in 60 seconds unless the disable is kept"),
            entry("network_config_changed", "network_interface", "eth1", "Disable kept: it stays off"),
            entry("network_config_changed", "network_interface", "eth1", "Enabled"),
        ]

    def test_an_enable_of_an_enabled_interface_writes_no_entry(self, network_host, audit_file):
        response = client().post(f"{API}/network/interfaces/eth0/up")

        assert response.status_code == 200, response.text
        assert entries(audit_file) == []

    def test_a_refused_disable_writes_no_entry(self, network_host, audit_file):
        response = client().post(f"{API}/network/interfaces/wlan0/down")

        assert response.status_code == 409
        assert entries(audit_file) == []

    def test_a_keep_without_a_disable_writes_no_entry(self, network_host, audit_file):
        response = client().post(f"{API}/network/interfaces/eth0/down/confirm")

        assert response.status_code == 409
        assert entries(audit_file) == []

    def test_a_new_host_name_is_in_the_audit_log(self, network_host, audit_file):
        response = client().put(f"{API}/network/hostname", json={"hostname": "nas"})

        assert response.status_code == 200, response.text
        assert entries(audit_file) == [entry("settings_changed", "host", None, "Host name changed from unos to nas")]

    def test_the_same_host_name_writes_no_entry(self, network_host, audit_file):
        response = client().put(f"{API}/network/hostname", json={"hostname": "unos"})

        assert response.status_code == 200, response.text
        assert entries(audit_file) == []

    def test_a_host_name_that_the_host_refused_writes_no_entry(self, network_host, audit_file):
        network_host.fail[("hostnamectl",)] = (1, "Could not set static hostname: Access denied")

        response = client().put(f"{API}/network/hostname", json={"hostname": "nas"})

        assert response.status_code == 500
        assert entries(audit_file) == []


# ---------------------------------------------------------------------------
# The time zone
# ---------------------------------------------------------------------------


@pytest.fixture
def zone_host(monkeypatch, audit_file):
    """timedatectl on a fake host whose zone is Etc/UTC, as in test_system_time_zone.py."""
    state = {"zone": "Etc/UTC", "fail_set": None}

    def run_on_host(cmd, *, timeout=60, check=False, input=None, errors=None):
        assert cmd[:2] == ["timeout", "10"], cmd
        cmd = cmd[2:]
        if cmd == ["timedatectl", "show", "--property=Timezone", "--value"]:
            return HostResult(0, state["zone"] + "\n", "")
        if cmd == ["timedatectl", "list-timezones"]:
            return HostResult(0, ZONES, "")
        if cmd[:2] == ["timedatectl", "set-timezone"] and len(cmd) == 3:
            if state["fail_set"]:
                return HostResult(1, "", state["fail_set"])
            state["zone"] = cmd[2]
            return HostResult(0, "", "")
        raise AssertionError(f"unexpected host command {cmd}")

    monkeypatch.setattr(src.host, "run_on_host", run_on_host)
    monkeypatch.setattr(subprocess, "run", _forbidden)
    app.dependency_overrides[verify_token] = lambda: ADMIN
    try:
        yield state
    finally:
        app.dependency_overrides.pop(verify_token, None)


class TestTimeZone:
    def test_a_new_time_zone_is_in_the_audit_log(self, zone_host, audit_file):
        response = client().put(f"{API}/system/timezone", json={"timezone": "Europe/Berlin"})

        assert response.status_code == 200, response.text
        assert entries(audit_file) == [
            entry("settings_changed", "host", None, "Time zone changed from Etc/UTC to Europe/Berlin")
        ]

    def test_the_same_time_zone_writes_no_entry(self, zone_host, audit_file):
        response = client().put(f"{API}/system/timezone", json={"timezone": "Etc/UTC"})

        assert response.status_code == 200, response.text
        assert entries(audit_file) == []

    def test_a_time_zone_that_the_host_refused_writes_no_entry(self, zone_host, audit_file):
        zone_host["fail_set"] = "Failed to set time zone: Access denied"

        response = client().put(f"{API}/system/timezone", json={"timezone": "Europe/Berlin"})

        assert response.status_code == 500
        assert entries(audit_file) == []


# ---------------------------------------------------------------------------
# An entry that cannot be written
# ---------------------------------------------------------------------------


class TestAnEntryThatCannotBeWritten:
    """The change is made, so it answers as it does otherwise; the entry is in the supervisor's log."""

    def test_a_backup_is_created_and_deleted(self, backups, audit_file, monkeypatch, caplog):
        the_audit_log_is_full(monkeypatch)

        created = backups.client.post(f"{API}/backup/create", json={})
        assert created.status_code == 200, created.text
        name = created.json()["backup"]["filename"]
        assert (backups.backup_dir / name).exists()
        deleted = backups.client.delete(f"{API}/backup/{name}")

        assert deleted.status_code == 200, deleted.text
        assert not (backups.backup_dir / name).exists()
        assert lost(caplog) == [entry("backup_created", "backup", name, "Backup created"),
                                entry("backup_deleted", "backup", name)]
        assert entries(audit_file) == []

    def test_a_backup_is_restored(self, backups, monkeypatch, caplog):
        name = _a_backup(backups)
        the_audit_log_is_full(monkeypatch)

        response = backups.client.post(f"{API}/backup/restore/{name}", json={})

        assert response.status_code == 200, response.text
        assert lost(caplog) == [entry("backup_restored", "backup", name, "Restored database, smb_config", "warning")]

    def test_the_reboot_is_scheduled(self, power_host, monkeypatch, caplog):
        the_audit_log_is_full(monkeypatch)

        response = client().post(f"{API}/power/reboot")

        assert response.status_code == 200, response.text
        assert response.json()["status"] == "scheduled"
        assert power_host.scheduled[0] == "reboot"
        assert lost(caplog) == [entry("system_reboot", "host", None, "The host reboots in 1 minute", "warning")]

    def test_a_task_is_created(self, tasks, monkeypatch, caplog):
        the_audit_log_is_full(monkeypatch)

        response = tasks.client.post(f"{API}/scheduler/tasks", json=_task())

        assert response.status_code == 200, response.text
        assert [t["name"] for t in tasks.client.get(f"{API}/scheduler/tasks").json()] == ["Nightly"]
        assert lost(caplog) == [entry("task_created", "task", "Nightly", "Created the snapshot task Nightly")]

    def test_an_interface_is_disabled(self, network_host, monkeypatch, caplog):
        the_audit_log_is_full(monkeypatch)

        response = client().post(f"{API}/network/interfaces/eth1/down")

        assert response.status_code == 200, response.text
        assert network_host.links["eth1"]["enabled"] is False
        assert [found["action"] for found in lost(caplog)] == ["network_config_changed"]

    def test_the_time_zone_is_set(self, zone_host, monkeypatch, caplog):
        the_audit_log_is_full(monkeypatch)

        response = client().put(f"{API}/system/timezone", json={"timezone": "Europe/Berlin"})

        assert response.status_code == 200, response.text
        assert zone_host["zone"] == "Europe/Berlin"
        assert [found["action"] for found in lost(caplog)] == ["settings_changed"]
