"""The UPS settings and the UPS monitor (system.ups-monitoring, ADR-0356).

The NUT password of the UPS settings is write-only: no answer, repr() or log line shows it, and a
PUT without one keeps the stored one. UNOS reads the UPS with NUT's upsc on the host, through
``src.host.run_on_host``; ``FakeNutHost`` answers it, with or without the package nut-client, and
fails the test for any other command. ``subprocess`` is blocked, so nothing runs on this machine.
While the host has no upsc, the monitor reports itself unavailable and does nothing else: it shuts
nothing down, writes no file and logs the reason once.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src import host as host_module
from src.auth.utils import verify_token
from src.host import HostCommandError, HostResult
from src.main import app

power = importlib.import_module("src.power.router")

API = "/api/v1/power/ups"
PASSWORD = "nut-secret-7f3a"

# upsc under the host's timeout (ADR-0258): 10 seconds, then SIGKILL 5 seconds later.
TIMEOUT = ["timeout", "--kill-after=5", "10"]
UPSC = TIMEOUT + ["upsc", "ups@localhost:3493"]
UPSC_LIST = TIMEOUT + ["upsc", "-l", "localhost:3493"]

# The answers' texts (ADR-0356).
UPSC_MISSING = "The host has no NUT client: upsc was not found. Install the package nut-client on the host"
PASSWORD_WITHOUT_USERNAME = "The NUT password needs a username"

# What upsc prints for a UPS on mains power (the variables the router reads, and some others).
ONLINE = {
    "battery.charge": "100",
    "battery.runtime": "1830",
    "battery.type": "PbAc",
    "device.mfr": "EATON",
    "input.voltage": "230.0",
    "output.voltage": "229.0",
    "ups.load": "17",
    "ups.mfr": "EATON",
    "ups.model": "Ellipse PRO 650",
    "ups.status": "OL",
}


class FakeNutHost:
    """The host behind ``src.host.run_on_host``: NUT's upsc, and upsd at localhost:3493."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        # Whether the host has the package nut-client, with upsc.
        self.nut_client = False
        # The UPSs that upsd at localhost:3493 serves, by name, with their variables.
        self.upsd: dict[str, dict[str, str]] = {"ups": dict(ONLINE)}

    def run(self, cmd, *, timeout=60, check=False, input=None, errors=None):
        cmd = list(cmd)
        self.calls.append(cmd)
        result = self._dispatch(cmd)
        if check and not result.ok:
            raise HostCommandError(cmd, result.returncode, result.stderr)
        return result

    def _dispatch(self, cmd) -> HostResult:
        if cmd[: len(TIMEOUT)] != TIMEOUT or cmd[len(TIMEOUT)] != "upsc":
            raise AssertionError(f"the fake host does not know {cmd!r}")
        if not self.nut_client:
            return HostResult(127, "", "timeout: failed to run command 'upsc': No such file or directory\n")
        args = cmd[len(TIMEOUT) + 1:]
        if args[0] == "-l":
            if args[1:] != ["localhost:3493"]:
                return HostResult(1, "", "Error: Connection failure: Connection refused\n")
            return HostResult(0, "".join(f"{name}\n" for name in sorted(self.upsd)), "")
        [ident] = args
        name, _, server = ident.partition("@")
        if server != "localhost:3493":
            return HostResult(1, "", "Error: Connection failure: Connection refused\n")
        if name not in self.upsd:
            return HostResult(1, "", "Error: Unknown UPS\n")
        return HostResult(0, "".join(f"{key}: {value}\n" for key, value in sorted(self.upsd[name].items())), "")


def _forbidden(*args, **kwargs):
    raise AssertionError(f"a test tried to run a command outside host.run_on_host: {args!r}")


@pytest.fixture
def settings_file(monkeypatch, tmp_path) -> Path:
    path = tmp_path / "ups_config.json"
    monkeypatch.setattr(power, "UPS_CONFIG_FILE", str(path))
    return path


@pytest.fixture
def fake_host(monkeypatch, settings_file):
    """Host commands reach FakeNutHost through nsenter's path; nothing runs on this machine."""
    fake = FakeNutHost()
    monkeypatch.delenv("UNOS_HOST_EXEC", raising=False)
    monkeypatch.setattr(host_module, "run_on_host", fake.run)
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, _forbidden)
    monkeypatch.setattr(os, "system", _forbidden)
    return fake


@pytest.fixture
def client(fake_host):
    """TestClient on the real app with an administrator signed in."""
    app.dependency_overrides[verify_token] = lambda: SimpleNamespace(
        id=1, username="admin", is_admin=True, is_active=True
    )
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(verify_token, None)


def _store(path: Path, **settings) -> None:
    path.write_text(json.dumps({"enabled": True, **settings}))


def _stored(path: Path) -> dict:
    return json.loads(path.read_text())


def _settings(**changes) -> dict:
    """A PUT body: the settings without a password, and CHANGES."""
    return {
        "enabled": True, "host": "localhost", "port": 3493, "ups_name": "ups", "username": "monuser",
        "auto_shutdown": True, "shutdown_battery_percent": 20, "shutdown_runtime_minutes": 5, **changes,
    }


def _files(folder: Path) -> dict[str, tuple[int, int]]:
    """Every file below FOLDER with its inode and modification time: what a write would change."""
    return {str(p): (p.stat().st_ino, p.stat().st_mtime_ns) for p in sorted(folder.rglob("*"))}


# ---------------------------------------------------------------------------
# The NUT password is write-only
# ---------------------------------------------------------------------------


class TestPasswordIsWriteOnly:
    def test_get_answers_whether_a_password_is_stored_never_the_password(self, client, settings_file):
        _store(settings_file, username="monuser", password=PASSWORD)

        response = client.get(f"{API}/config")

        assert response.status_code == 200, response.text
        assert PASSWORD not in response.text
        answer = response.json()
        assert "password" not in answer
        assert answer["has_password"] is True
        assert answer["username"] == "monuser"

    def test_get_without_settings_has_no_password(self, client):
        response = client.get(f"{API}/config")

        assert response.status_code == 200, response.text
        assert response.json()["has_password"] is False
        assert "password" not in response.json()

    @pytest.mark.parametrize("sent", [{}, {"password": None}, {"password": ""}], ids=["absent", "null", "empty"])
    def test_a_put_without_a_password_keeps_the_stored_one(self, client, settings_file, sent):
        _store(settings_file, username="monuser", password=PASSWORD)

        response = client.put(f"{API}/config", json=_settings(shutdown_battery_percent=35, **sent))

        assert response.status_code == 200, response.text
        assert PASSWORD not in response.text
        assert "password" not in response.json()
        assert response.json()["has_password"] is True
        assert response.json()["shutdown_battery_percent"] == 35
        assert _stored(settings_file)["password"] == PASSWORD
        assert _stored(settings_file)["shutdown_battery_percent"] == 35

    def test_a_put_with_a_password_replaces_it_and_answers_without_it(self, client, settings_file):
        _store(settings_file, username="monuser", password="the-old-one")

        response = client.put(f"{API}/config", json=_settings(password=PASSWORD))

        assert response.status_code == 200, response.text
        assert PASSWORD not in response.text
        assert "password" not in response.json()
        assert response.json()["has_password"] is True
        assert _stored(settings_file)["password"] == PASSWORD
        # The file holds a password: only its owner reads it.
        assert stat.S_IMODE(settings_file.stat().st_mode) == 0o600

    @pytest.mark.parametrize("username", [None, ""])
    def test_a_put_without_a_username_stores_no_password(self, client, settings_file, username):
        _store(settings_file, username="monuser", password=PASSWORD)

        response = client.put(f"{API}/config", json=_settings(username=username))

        assert response.status_code == 200, response.text
        assert response.json()["has_password"] is False
        assert response.json()["username"] is None
        assert _stored(settings_file)["password"] is None

    def test_a_password_without_a_username_is_refused(self, client, settings_file):
        _store(settings_file, username="monuser", password="the-old-one")
        before = _files(settings_file.parent)

        response = client.put(f"{API}/config", json=_settings(username=None, password=PASSWORD))

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == PASSWORD_WITHOUT_USERNAME
        assert PASSWORD not in response.text
        assert _files(settings_file.parent) == before

    def test_a_put_that_changes_nothing_writes_nothing(self, client, settings_file):
        assert client.put(f"{API}/config", json=_settings(password=PASSWORD)).status_code == 200
        before = _files(settings_file.parent)

        response = client.put(f"{API}/config", json=_settings())

        assert response.status_code == 200, response.text
        assert response.json()["has_password"] is True
        assert _files(settings_file.parent) == before

    def test_a_damaged_settings_file_is_logged_without_its_values(self, client, settings_file, caplog):
        # A password that is no string, and a port that is no number: the file is kept as .bad, and
        # the warning names the fields, never what they hold.
        _store(settings_file, username="monuser", password=[PASSWORD], port=f"port-{PASSWORD}")

        with caplog.at_level(logging.DEBUG):
            response = client.get(f"{API}/config")

        assert response.status_code == 200, response.text
        assert response.json()["has_password"] is False
        assert settings_file.with_name("ups_config.json.bad").exists()
        warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("password" in message and "port" in message for message in warnings), warnings
        assert not any(PASSWORD in r.getMessage() for r in caplog.records)

    def test_the_settings_repr_hides_the_password(self, settings_file):
        _store(settings_file, username="monuser", password=PASSWORD)

        assert PASSWORD not in repr(power.load_ups_config())


# ---------------------------------------------------------------------------
# upsc runs on the host
# ---------------------------------------------------------------------------


class TestUpscOnTheHost:
    def test_the_status_is_read_with_upsc_on_the_host(self, client, fake_host, settings_file):
        fake_host.nut_client = True
        _store(settings_file)

        response = client.get(f"{API}/status")

        assert response.status_code == 200, response.text
        answer = response.json()
        assert answer["available"] is True
        assert answer["reason"] is None
        assert (answer["status"], answer["charge_percent"], answer["runtime_minutes"]) == ("OL", 100, 30)
        assert (answer["model"], answer["manufacturer"], answer["load_percent"]) == ("Ellipse PRO 650", "EATON", 17)
        assert fake_host.calls == [UPSC]

    def test_without_upsc_the_status_is_unavailable_and_says_why(self, client, fake_host, settings_file):
        _store(settings_file)

        response = client.get(f"{API}/status")

        assert response.status_code == 200, response.text
        answer = response.json()
        assert answer["available"] is False
        assert answer["reason"] == UPSC_MISSING
        assert answer["status"] is None and answer["charge_percent"] is None
        assert fake_host.calls == [UPSC]

    @pytest.mark.parametrize(
        "nut_client,ups_name,reason",
        [
            (False, "ups", UPSC_MISSING),
            (True, "other", "upsc could not read other@localhost:3493: Error: Unknown UPS"),
        ],
        ids=["no-upsc", "unknown-ups"],
    )
    def test_test_connection_names_the_failure(self, client, fake_host, settings_file, nut_client, ups_name, reason):
        fake_host.nut_client = nut_client
        _store(settings_file, ups_name=ups_name)

        response = client.post(f"{API}/test")

        assert response.status_code == 503, response.text
        assert response.json()["detail"] == f"Unable to connect to the UPS: {reason}"

    def test_the_list_without_upsc_names_it(self, client, fake_host):
        response = client.get(f"{API}/list")

        assert response.status_code == 200, response.text
        assert response.json() == {"devices": [], "error": UPSC_MISSING}
        assert fake_host.calls == [UPSC_LIST]

    def test_the_list_names_the_upss_of_upsd(self, client, fake_host):
        fake_host.nut_client = True
        fake_host.upsd["rack"] = dict(ONLINE)

        response = client.get(f"{API}/list")

        assert response.status_code == 200, response.text
        assert response.json() == {"devices": ["rack", "ups"]}

    @pytest.mark.parametrize(
        "changes",
        [{"ups_name": "-l"}, {"ups_name": "ups@other"}, {"host": "-oops"}, {"host": "nas:1"}, {"port": 0},
         {"port": 65536}],
        ids=["name-option", "name-at", "host-option", "host-port", "port-0", "port-65536"],
    )
    def test_values_that_reach_upsc_are_checked(self, client, settings_file, changes):
        # They go into upsc's argument <ups>@<host>:<port> on the host (ADR-0293).
        response = client.put(f"{API}/config", json=_settings(**changes))

        assert response.status_code == 422, response.text
        assert not settings_file.exists()

    @pytest.mark.parametrize("host", ["nas.local", "192.168.1.20", "[fd00::20]"])
    def test_host_names_and_addresses_are_taken(self, client, settings_file, host):
        response = client.put(f"{API}/config", json=_settings(host=host))

        assert response.status_code == 200, response.text
        assert _stored(settings_file)["host"] == host


# ---------------------------------------------------------------------------
# The monitor
# ---------------------------------------------------------------------------


def _run_monitor(monkeypatch, rounds: int) -> None:
    """Run the UPS monitor's loop for ROUNDS checks, without its 30 seconds between them."""
    real_sleep = asyncio.sleep
    slept = 0

    async def sleep(seconds, *args, **kwargs):
        nonlocal slept
        if seconds == power.UPS_CHECK_INTERVAL:
            slept += 1
            if slept >= rounds:
                power._ups_monitor_running = False
        await real_sleep(0)

    monkeypatch.setattr(power, "_ups_monitor_running", True)
    monkeypatch.setattr(power.asyncio, "sleep", sleep)
    try:
        asyncio.run(asyncio.wait_for(power.ups_monitor_loop(), 30))
    finally:
        monkeypatch.setattr(power.asyncio, "sleep", real_sleep)


@pytest.fixture
def fresh_monitor(monkeypatch):
    """The monitor as it is at the start of the supervisor: no check yet."""
    monkeypatch.setattr(power, "_ups_last_check", None, raising=False)


class TestMonitor:
    def test_without_upsc_the_monitor_reports_itself_unavailable_and_does_nothing_else(
        self, client, fake_host, settings_file, fresh_monitor, monkeypatch, caplog, capsys, tmp_path
    ):
        _store(settings_file, username="monuser", password=PASSWORD)
        before = _files(tmp_path)

        with caplog.at_level(logging.DEBUG):
            _run_monitor(monkeypatch, rounds=5)

        # One upsc a round and nothing else on the host: no shutdown.
        assert fake_host.calls == [UPSC] * 5
        # One line in the log, at the first round, and none for the four after it.
        records = [r for r in caplog.records if r.name == power.__name__]
        assert [(r.levelno, r.getMessage()) for r in records] == [
            (logging.WARNING, f"The UPS monitor cannot read ups@localhost:3493: {UPSC_MISSING}")
        ]
        assert capsys.readouterr().out == ""
        # No file written, the settings' included.
        assert _files(tmp_path) == before

        response = client.get(f"{API}/monitor/status")

        assert response.status_code == 200, response.text
        answer = response.json()
        assert answer["enabled"] is True
        assert answer["available"] is False
        assert answer["reason"] == UPSC_MISSING
        assert answer["last_checked"]

    def test_the_monitor_reads_the_ups_once_upsc_is_installed(
        self, client, fake_host, settings_file, fresh_monitor, monkeypatch, caplog
    ):
        _store(settings_file)
        with caplog.at_level(logging.DEBUG):
            _run_monitor(monkeypatch, rounds=2)
            fake_host.nut_client = True
            _run_monitor(monkeypatch, rounds=3)

        assert fake_host.calls == [UPSC] * 5
        records = [r for r in caplog.records if r.name == power.__name__]
        assert [(r.levelno, r.getMessage()) for r in records] == [
            (logging.WARNING, f"The UPS monitor cannot read ups@localhost:3493: {UPSC_MISSING}"),
            (logging.INFO, "The UPS monitor reads ups@localhost:3493 again"),
        ]
        answer = client.get(f"{API}/monitor/status").json()
        assert (answer["available"], answer["reason"]) == (True, None)

    def test_a_monitor_whose_settings_are_off_runs_nothing(
        self, client, fake_host, settings_file, fresh_monitor, monkeypatch, caplog
    ):
        settings_file.write_text(json.dumps({"enabled": False}))

        with caplog.at_level(logging.DEBUG):
            _run_monitor(monkeypatch, rounds=3)

        assert fake_host.calls == []
        assert [r for r in caplog.records if r.name == power.__name__] == []
        answer = client.get(f"{API}/monitor/status").json()
        assert answer["enabled"] is False
        assert (answer["available"], answer["reason"], answer["last_checked"]) == (False, None, None)
