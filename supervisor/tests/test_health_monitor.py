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

from .test_pool_persistence import install_fake_host

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
def smart_disk(monkeypatch, tmp_path):
    """One data disk sda (FakeHost, lsblk on the host); set ``.reallocated`` per round.

    The host also has its boot SD card and zram, which the monitor leaves alone.
    """
    disk = SimpleNamespace(reallocated=0, rounds=0, stop_after=None)
    monkeypatch.setattr(health_router, "SMART_HISTORY_FILE", str(tmp_path / "smart_history.json"))
    fake = install_fake_host(monkeypatch)
    fake.add_disk("sda", serial="WD-1")
    host_run = fake.run

    def run_on_host(cmd, **kwargs):
        if cmd[0] == "lsblk":
            disk.rounds += 1
            if disk.stop_after is not None and disk.rounds >= disk.stop_after:
                health_router._smart_monitor_running = False  # this is the last round
        return host_run(cmd, **kwargs)

    def smartctl(cmd, **kwargs):
        assert cmd == ["smartctl", "-n", "standby", "-j", "-a", "/dev/sda"], cmd
        return subprocess.CompletedProcess(cmd, 0, _smartctl_json("WD-1", disk.reallocated), "")

    monkeypatch.setattr(host, "run_on_host", run_on_host)
    monkeypatch.setattr(host, "run_in_container", smartctl)
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

    def test_predictions_read_both_key_types(self):
        # The endpoint and the disks it covers: test_smart_predictions.py.
        entries = [{"timestamp": f"t{i}", "device": "/dev/sda", "attributes": {"5": 0}}
                   for i in range(23)]
        entries.append({"timestamp": "t23", "device": "/dev/sda", "attributes": {5: 40}})

        prediction = health_router.predict_disk_failure("WD-1", entries, "/dev/sda")

        assert prediction.risk_score >= 50
        assert "Reallocated_Sector_Ct: 40 (was 0)" in prediction.risk_factors


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


def _pool_health(errors: int = 0, checked: str = "2026-11-06T07:00:00") -> "health_router.RaidHealthInfo":
    return health_router.RaidHealthInfo(
        pool="data", mount_point="/mnt/pools/data", profile="single",
        status="healthy" if errors == 0 else "error", devices=[], total_errors=errors, last_checked=checked,
    )


class TestRaidStatusFile:
    """raid_status.json is on the system disk (an SD card on small boards): the RAID
    monitor writes it when the state of a pool changes, not after every check."""

    @pytest.fixture
    def raid_file(self, tmp_path, monkeypatch):
        path = tmp_path / "raid_status.json"
        monkeypatch.setattr(health_router, "RAID_STATUS_FILE", str(path))
        monkeypatch.setattr(health_router, "_latest_raid_status", None, raising=False)
        alerts = []

        async def fake_alert(*args):
            alerts.append(args)

        async def stop_after_one_round(_seconds):
            health_router._raid_monitor_running = False

        monkeypatch.setattr(health_router, "send_raid_alert", fake_alert)
        monkeypatch.setattr(health_router.asyncio, "sleep", stop_after_one_round)
        return SimpleNamespace(path=path, alerts=alerts)

    async def _round(self, monkeypatch, *pools):
        async def check():
            return list(pools)

        monkeypatch.setattr(health_router, "check_raid_health", check)
        monkeypatch.setattr(health_router, "_raid_monitor_running", True)
        await health_router.raid_monitor_loop()

    async def test_a_check_that_changes_nothing_does_not_write_the_file(self, raid_file, monkeypatch):
        await self._round(monkeypatch, _pool_health(checked="2026-11-06T07:00:00"))
        written = raid_file.path.read_text()
        stat_before = raid_file.path.stat()
        await self._round(monkeypatch, _pool_health(checked="2026-11-06T07:05:00"))
        await self._round(monkeypatch, _pool_health(checked="2026-11-06T07:10:00"))
        assert raid_file.path.read_text() == written
        assert raid_file.path.stat().st_mtime_ns == stat_before.st_mtime_ns
        assert json.loads(written)["data"]["last_checked"] == "2026-11-06T07:00:00"

    async def test_a_change_of_state_is_written(self, raid_file, monkeypatch):
        await self._round(monkeypatch, _pool_health(checked="2026-11-06T07:00:00"))
        await self._round(monkeypatch, _pool_health(errors=3, checked="2026-11-06T07:05:00"))
        stored = json.loads(raid_file.path.read_text())["data"]
        assert (stored["status"], stored["total_errors"], stored["last_checked"]) == ("error", 3, "2026-11-06T07:05:00")
        assert len(raid_file.alerts) == 1
        # The next check compares with the stored state: no second alert.
        await self._round(monkeypatch, _pool_health(errors=3, checked="2026-11-06T07:10:00"))
        assert len(raid_file.alerts) == 1

    @pytest.mark.parametrize("content", [None, "{not json", '{"data": "garbage"}', "[1, 2]"])
    async def test_a_missing_or_unreadable_file_is_written(self, raid_file, monkeypatch, content):
        if content is not None:
            raid_file.path.write_text(content)
        await self._round(monkeypatch, _pool_health())
        assert json.loads(raid_file.path.read_text())["data"]["status"] == "healthy"

    async def test_a_stats_reset_starts_from_the_latest_check(self, raid_file, monkeypatch, admin_api):
        """Not from the file, whose check times can be old: it is written only when the state changes."""
        await self._round(monkeypatch, _pool_health(errors=3, checked="2026-11-06T07:00:00"))
        await self._round(monkeypatch, _pool_health(errors=3, checked="2026-11-06T07:05:00"))

        health_router._clear_raid_errors("data")

        shown = admin_api.get("/api/v1/health/raid-monitor/status").json()["last_status"]["data"]
        stored = json.loads(raid_file.path.read_text())["data"]
        for entry in (shown, stored):
            assert (entry["status"], entry["total_errors"], entry["last_checked"]) == (
                "healthy", 0, "2026-11-06T07:05:00")
        await self._round(monkeypatch, _pool_health(errors=3, checked="2026-11-06T07:10:00"))
        assert len(raid_file.alerts) == 2  # errors after the reset are new again

    @pytest.fixture
    def btrfs_reset(self, tmp_path, monkeypatch, admin_api):
        """A pool folder for the reset endpoint, and a recording host.run_in_container (after admin_api's fake host)."""
        monkeypatch.setattr(health_router.storage_pools.settings, "pools_base_path", str(tmp_path))
        (tmp_path / "data").mkdir()
        calls = []

        def run(cmd, **kwargs):
            try:
                asyncio.get_running_loop()
                on_loop = True
            except RuntimeError:
                on_loop = False
            calls.append((cmd, on_loop, kwargs.get("timeout")))
            if calls[-1][2] is None:
                raise AssertionError("btrfs was run without a timeout")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr(health_router.host, "run_in_container", run)
        return SimpleNamespace(pool=tmp_path / "data", calls=calls)

    def test_the_stats_reset_runs_btrfs_off_the_event_loop_with_a_timeout(self, raid_file, btrfs_reset, admin_api):
        r = admin_api.post("/api/v1/health/raid/data/reset-stats")

        assert r.status_code == 200, r.text
        assert btrfs_reset.calls == [(["btrfs", "device", "stats", "--reset", str(btrfs_reset.pool)], False, 60)]

    def test_a_stats_reset_that_does_not_return_answers_500(self, raid_file, btrfs_reset, admin_api, monkeypatch):
        def hang(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

        monkeypatch.setattr(health_router.host, "run_in_container", hang)
        r = admin_api.post("/api/v1/health/raid/data/reset-stats")

        assert (r.status_code, r.json()["detail"]) == (
            500, "btrfs device stats did not answer within 60 seconds: pool data may not be responding")

    @pytest.mark.parametrize("name", ["..", "bad name", "data;id"])
    def test_a_stats_reset_needs_a_valid_pool_name(self, raid_file, btrfs_reset, admin_api, name):
        r = admin_api.post(f"/api/v1/health/raid/{name}/reset-stats")

        assert r.status_code in (400, 404, 422), r.text
        assert btrfs_reset.calls == []

    async def test_the_monitor_status_shows_the_latest_check(self, raid_file, monkeypatch, admin_api):
        await self._round(monkeypatch, _pool_health(checked="2026-11-06T07:00:00"))
        await self._round(monkeypatch, _pool_health(checked="2026-11-06T07:05:00"))
        body = admin_api.get("/api/v1/health/raid-monitor/status").json()
        assert body["last_status"]["data"]["last_checked"] == "2026-11-06T07:05:00"
        assert json.loads(raid_file.path.read_text())["data"]["last_checked"] == "2026-11-06T07:00:00"
