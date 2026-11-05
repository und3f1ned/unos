"""SMART trend alerts and the /health/full and Prometheus pool counts."""
import asyncio
import importlib
import json
import subprocess
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src import host
from src.auth.utils import verify_token
from src.main import app
from src.storage import pools as storage_pools

health_router = importlib.import_module("src.health.router")
main_module = importlib.import_module("src.main")


def _smartctl_json(serial: str, reallocated: int) -> str:
    return json.dumps({
        "serial_number": serial,
        "smart_status": {"passed": True},
        "temperature": {"current": 35},
        "ata_smart_attributes": {"table": [
            {"id": 5, "name": "Reallocated_Sector_Ct", "raw": {"value": reallocated}},
            {"id": 9, "name": "Power_On_Hours", "raw": {"value": 51775}},
            {"id": 197, "name": "Current_Pending_Sector", "raw": {"value": 0}},
        ]},
    })


@pytest.fixture
def no_host(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("the SMART monitor must not run host commands")

    monkeypatch.setattr(host, "run_on_host", forbidden)


@pytest.fixture
def smart_disk(monkeypatch, tmp_path, no_host):
    """One data disk sda behind fake lsblk/smartctl; set ``.reallocated`` per round."""
    disk = SimpleNamespace(reallocated=0, rounds=0, stop_after=None)
    monkeypatch.setattr(health_router, "SMART_HISTORY_FILE", str(tmp_path / "smart_history.json"))

    def run(cmd, **kwargs):
        if cmd[0] == "lsblk":
            disk.rounds += 1
            if disk.stop_after is not None and disk.rounds >= disk.stop_after:
                health_router._smart_monitor_running = False  # this is the last round
            devices = {"blockdevices": [
                {"name": "mmcblk1", "type": "disk", "mountpoint": None,
                 "children": [{"name": "mmcblk1p1", "mountpoint": "/"}]},
                {"name": "sda", "type": "disk", "mountpoint": None},
            ]}
            return subprocess.CompletedProcess(cmd, 0, json.dumps(devices), "")
        assert cmd == ["smartctl", "-n", "standby", "-j", "-a", "/dev/sda"], cmd
        return subprocess.CompletedProcess(cmd, 0, _smartctl_json("WD-1", disk.reallocated), "")

    monkeypatch.setattr(subprocess, "run", run)
    return disk


class TestSmartTrendAlerts:
    async def test_rising_reallocated_sectors_raise_one_alert(self, smart_disk, monkeypatch):
        delivered = []

        async def deliver(event):
            delivered.append(event)

        monkeypatch.setattr(health_router, "_deliver_alert", deliver)
        monkeypatch.setattr(health_router, "SMART_CHECK_INTERVAL", 0)
        monkeypatch.setattr(health_router, "_smart_monitor_running", True)
        smart_disk.stop_after = 2

        real_pass = health_router._smart_monitor_pass

        def monitor_pass():
            alerts = real_pass()
            smart_disk.reallocated = 50  # the disk reallocates sectors before the next round
            return alerts

        monkeypatch.setattr(health_router, "_smart_monitor_pass", monitor_pass)
        await asyncio.wait_for(health_router.smart_monitor_loop(), 10)

        assert smart_disk.rounds == 2
        [event] = delivered
        assert event.title == "Disk Health Warning: /dev/sda"
        assert "Reallocated_Sector_Ct increased by 50 (from 0 to 50)" in event.message
        assert event.type.value == "error"  # more than 10 new bad sectors: critical
        history = health_router.load_smart_history()
        assert [entry["attributes"]["5"] for entry in history["WD-1"]] == [0, 50]

    def test_stable_values_raise_nothing(self, smart_disk):
        assert health_router._smart_monitor_pass() == []
        assert health_router._smart_monitor_pass() == []

    def test_second_pass_compares_with_the_stored_history(self, smart_disk):
        assert health_router._smart_monitor_pass() == []
        smart_disk.reallocated = 3

        [alert] = health_router._smart_monitor_pass()

        assert (alert.attribute_id, alert.previous_value, alert.current_value) == (5, 0, 3)
        assert alert.severity == "warning"

    def test_one_increase_alerts_once(self, smart_disk):
        alerts_per_pass = []
        for value in [0, 50, 50, 50]:
            smart_disk.reallocated = value
            alerts_per_pass.append(len(health_router._smart_monitor_pass()))

        # Hourly passes: not the same warning every hour for a day.
        assert alerts_per_pass == [0, 1, 0, 0]
        # The trend itself is still reported (Health page, /smart/trends).
        history = health_router.load_smart_history()["WD-1"]
        assert len(health_router.analyze_smart_trends("WD-1", history)) == 1

    def test_each_further_rise_alerts_again(self, smart_disk):
        alerts = []
        for value in [0, 5, 5, 9]:
            smart_disk.reallocated = value
            alerts.append(health_router._smart_monitor_pass())

        assert [len(a) for a in alerts] == [0, 1, 0, 1]
        [latest] = alerts[-1]
        # The message still reports the change over the comparison window.
        assert (latest.previous_value, latest.current_value, latest.change) == (0, 9, 9)

    def test_analysis_accepts_int_and_str_attribute_keys(self):
        stored = {"device": "/dev/sda", "attributes": {"5": 1, "197": 0}}  # from the JSON file
        in_memory = {"device": "/dev/sda", "attributes": {5: 4, 197: 2}}  # SmartHistoryEntry.model_dump()

        alerts = health_router.analyze_smart_trends("WD-1", [stored, in_memory])

        assert {(a.attribute_id, a.previous_value, a.current_value) for a in alerts} == {
            (5, 1, 4), (197, 0, 2),
        }

    async def test_predictions_read_both_key_types(self, tmp_path, monkeypatch):
        entries = [{"timestamp": f"t{i}", "device": "/dev/sda", "attributes": {"5": 0}}
                   for i in range(23)]
        entries.append({"timestamp": "t23", "device": "/dev/sda", "attributes": {5: 40}})
        monkeypatch.setattr(health_router, "load_smart_history", lambda: {"WD-1": entries})

        result = await health_router.get_disk_predictions(_={})

        [prediction] = result["predictions"]
        assert prediction["risk_score"] >= 50
        assert "Reallocated_Sector_Ct: 40 (was 0)" in prediction["risk_factors"]


def _on_event_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


@pytest.fixture
def admin_api(monkeypatch):
    admin = SimpleNamespace(id=1, username="admin", is_admin=True)
    app.dependency_overrides[verify_token] = lambda: admin

    def no_docker(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, "", "docker: not here")

    def no_docker_api():
        raise RuntimeError("docker: not here")

    monkeypatch.setattr(subprocess, "run", no_docker)
    monkeypatch.setattr(main_module, "_running_containers", no_docker_api)
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(verify_token, None)


class TestPoolCount:
    """/health/full and Prometheus count btrfs pools, not directories under /mnt/pools."""

    def test_full_health_counts_the_listed_pools(self, admin_api, monkeypatch):
        on_event_loop = []

        def list_pools():
            on_event_loop.append(_on_event_loop())
            return [SimpleNamespace(name="data"), SimpleNamespace(name="media")]

        monkeypatch.setattr(storage_pools, "list_pools", list_pools)
        response = admin_api.get("/api/v1/health/full")

        assert response.status_code == 200, response.text
        assert response.json()["components"]["storage"] == {"status": "ok", "pools": 2}
        assert on_event_loop == [False]  # host commands: listed in a worker thread

    def test_full_health_reports_a_failed_listing(self, admin_api, monkeypatch):
        def broken():
            raise RuntimeError("btrfs filesystem show failed: nsenter: failed to execute btrfs")

        monkeypatch.setattr(storage_pools, "list_pools", broken)
        response = admin_api.get("/api/v1/health/full")

        assert response.status_code == 200
        body = response.json()
        assert body["components"]["storage"]["status"] == "error"
        assert "failed to execute btrfs" in body["components"]["storage"]["message"]
        assert "storage" in body["issues"]
        assert body["status"] in ("degraded", "critical")

    def test_prometheus_counts_the_listed_pools(self, admin_api, monkeypatch):
        monkeypatch.setattr(storage_pools, "list_pools", lambda: [SimpleNamespace(name="data")])
        response = admin_api.get("/api/v1/health/prometheus")

        assert response.status_code == 200
        assert "unos_storage_pools 1\n" in response.text

    def test_prometheus_omits_the_count_when_listing_fails(self, admin_api, monkeypatch):
        def broken():
            raise RuntimeError("no btrfs")

        monkeypatch.setattr(storage_pools, "list_pools", broken)
        response = admin_api.get("/api/v1/health/prometheus")

        assert response.status_code == 200
        assert "unos_storage_pools" not in response.text
        assert "unos_up 1" in response.text


class TestDockerComponent:
    """/health/full asks the Docker API; the image has no docker CLI."""

    def test_running_containers_come_from_the_api_off_the_event_loop(self, admin_api, monkeypatch):
        on_event_loop = []

        def running():
            on_event_loop.append(_on_event_loop())
            return 3

        monkeypatch.setattr(main_module, "_running_containers", running)
        response = admin_api.get("/api/v1/health/full")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["components"]["docker"] == {"status": "ok", "containers_running": 3}
        assert "docker" not in body.get("issues", [])
        assert on_event_loop == [False]

    def test_unreachable_docker_is_an_issue(self, admin_api, monkeypatch):
        def unreachable():
            raise RuntimeError("Error while fetching server API version: Connection aborted")

        monkeypatch.setattr(main_module, "_running_containers", unreachable)
        body = admin_api.get("/api/v1/health/full").json()

        assert body["components"]["docker"]["status"] == "error"
        assert "Connection aborted" in body["components"]["docker"]["message"]
        assert "docker" in body["issues"]
        assert body["status"] in ("degraded", "critical")

    def test_a_hanging_docker_api_is_reported_not_waited_for(self, admin_api, monkeypatch):
        release = threading.Event()

        def hanging():
            release.wait(10)
            return 0

        monkeypatch.setattr(main_module, "DOCKER_CHECK_TIMEOUT", 0.1)
        monkeypatch.setattr(main_module, "_running_containers", hanging)
        # One event loop for the whole block, as under uvicorn. A client used
        # without "with" closes its loop after each request, and closing it
        # waits for the abandoned worker thread.
        monkeypatch.setenv("UNOS_DISABLE_BACKGROUND_TASKS", "1")
        try:
            with TestClient(app) as client:
                started = time.monotonic()
                body = client.get("/api/v1/health/full").json()
                elapsed = time.monotonic() - started
                release.set()
        finally:
            release.set()

        assert elapsed < 5
        assert body["components"]["docker"] == {"status": "error", "message": "TimeoutError"}
        assert "docker" in body["issues"]

    def test_the_client_uses_a_timeout_and_is_closed(self, monkeypatch):
        import docker

        made = []

        class FakeClient:
            def __init__(self):
                self.closed = False

            def info(self):
                return {"ContainersRunning": 2}

            def close(self):
                self.closed = True

        def from_env(**kwargs):
            client = FakeClient()
            made.append((kwargs, client))
            return client

        monkeypatch.setattr(docker, "from_env", from_env)

        assert main_module._running_containers() == 2
        [(kwargs, client)] = made
        assert kwargs == {"timeout": main_module.DOCKER_CHECK_TIMEOUT}
        assert client.closed
