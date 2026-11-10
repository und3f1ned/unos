"""SMART trend alerts, the SMART history and trends, and the /health/full and Prometheus pool counts."""
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
from src.notifications.schemas import (
    EmailConfig,
    NotificationChannel,
    NotificationConfig,
    TelegramConfig,
)
from src.storage import pools as storage_pools

from .test_pool_persistence import admin_client, install_fake_host
from .test_smart_predictions import PENDING, REALLOCATED, snapshots

health_router = importlib.import_module("src.health.router")
main_module = importlib.import_module("src.main")


def _smartctl_json(serial: str | None, reallocated: int | None, pending: int = 0, temperature: int = 35,
                   power_on_hours: int = 51775) -> str:
    """smartctl -j -a of an ATA disk; ``reallocated`` None: it could not read the attribute table.

    ``serial`` None: smartctl reports no serial number.
    """
    data = {"smart_status": {"passed": True}, "temperature": {"current": temperature}}
    if serial is not None:
        data["serial_number"] = serial
    if reallocated is not None:
        data["ata_smart_attributes"] = {"table": [
            {"id": 5, "name": "Reallocated_Sector_Ct", "raw": {"value": reallocated}},
            {"id": 9, "name": "Power_On_Hours", "raw": {"value": power_on_hours}},
            {"id": 197, "name": "Current_Pending_Sector", "raw": {"value": pending}},
        ]}
    return json.dumps(data)


@pytest.fixture
def smart_disk(monkeypatch, tmp_path):
    """One data disk sda (FakeHost, lsblk on the host); set ``.reallocated`` (None: no attribute
    table), ``.pending``, ``.temperature`` and ``.hours`` per round.

    The host also has its boot SD card and zram, which the monitor leaves alone.
    ``.history_file`` is the SMART history file.
    """
    disk = SimpleNamespace(reallocated=0, pending=0, temperature=35, hours=51775, rounds=0,
                           stop_after=None, history_file=tmp_path / "smart_history.json")
    monkeypatch.setattr(health_router, "SMART_HISTORY_FILE", str(disk.history_file))
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
        output = _smartctl_json("WD-1", disk.reallocated, disk.pending, disk.temperature, disk.hours)
        return subprocess.CompletedProcess(cmd, 0, output, "")

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

    def test_a_snapshot_without_the_counters_is_neither_a_drop_nor_a_rise(self, smart_disk):
        alerts = []
        # smartctl could not read the attribute table at the first and the third pass
        # (ADR-0270): the counters stayed at 8 until the last pass.
        for value in [None, 8, None, 8, 9]:
            smart_disk.reallocated = value
            alerts.append(health_router._smart_monitor_pass())

        assert [len(a) for a in alerts] == [0, 0, 0, 0, 1]
        [rise] = alerts[-1]
        assert (rise.previous_value, rise.current_value) == (8, 9)

    def test_no_alert_again_after_a_snapshot_without_the_counters(self, smart_disk):
        alerts = []
        for value in [0, 3, None, 3]:
            smart_disk.reallocated = value
            alerts.append(len(health_router._smart_monitor_pass()))

        assert alerts == [0, 1, 0, 0]

    def test_a_rise_after_a_drop_alerts_too(self, smart_disk):
        # Pending sectors that were rewritten leave the count; new ones are a new rise,
        # though the count stays below its value of a day ago.
        alerts = []
        for value in [5, 5, 0, 3, 3]:
            smart_disk.pending = value
            alerts.append(health_router._smart_monitor_pass())

        assert [len(a) for a in alerts] == [0, 0, 0, 1, 0]
        [rise] = alerts[3]
        assert (rise.attribute_name, rise.previous_value, rise.current_value, rise.severity) == (
            "Current_Pending_Sector", 0, 3, "warning")
        assert rise.message == "Current_Pending_Sector increased by 3 (from 0 to 3)"

    def test_a_rise_after_a_drop_that_the_day_shows_is_reported_over_the_day(self, smart_disk):
        # 5, 20, 10, 18: the newest value rose by 8, and by 13 over the day.
        alerts = []
        for value in [5, 20, 10, 18]:
            smart_disk.pending = value
            alerts.append(health_router._smart_monitor_pass())

        assert [len(a) for a in alerts] == [0, 1, 0, 1]
        [rise] = alerts[-1]
        assert (rise.previous_value, rise.current_value, rise.change, rise.severity) == (5, 18, 13, "critical")

    def test_the_serial_is_kept_as_smartctl_prints_it(self, monkeypatch):
        # Disk Health shows it so and marks a disk by it (ADR-0235).
        monkeypatch.setattr(host, "run_in_container", lambda cmd, **kw: subprocess.CompletedProcess(
            cmd, 0, _smartctl_json(" WD-1 ", 0), ""))

        assert health_router.collect_smart_snapshot("/dev/sda").serial == " WD-1 "

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


class TestSmartAlertChannels:
    """A rise of the reallocated or pending sectors reaches every enabled e-mail and Telegram channel."""

    @pytest.fixture
    def channels(self, monkeypatch):
        """Three channels, one of them disabled; returns what each sender got: (channel, address, event)."""
        mail = EmailConfig(smtp_host="mail.example", smtp_user="u", smtp_password="p",
                           from_address="nas@example", to_addresses=["me@example"])
        configs = [
            NotificationConfig(name="Mail", channel=NotificationChannel.EMAIL, email_config=mail),
            NotificationConfig(name="Phone", channel=NotificationChannel.TELEGRAM,
                               telegram_config=TelegramConfig(bot_token="t", chat_id="42")),
            NotificationConfig(name="Old mail", channel=NotificationChannel.EMAIL, enabled=False,
                               email_config=mail.model_copy(update={"to_addresses": ["old@example"]})),
        ]
        monkeypatch.setattr(health_router, "load_notification_configs", lambda: configs)
        sent = []

        async def send_email(config, event):
            sent.append(("email", config.to_addresses, event))
            return True, None

        async def send_telegram(config, event):
            sent.append(("telegram", config.chat_id, event))
            return True, None

        monkeypatch.setattr(health_router, "send_email", send_email)
        monkeypatch.setattr(health_router, "send_telegram", send_telegram)
        return sent

    @pytest.mark.parametrize(
        "counter, message, recommendation",
        [
            ("reallocated", "Reallocated_Sector_Ct increased by 2 (from 0 to 2)",
             "Disk is reallocating bad sectors. Consider replacing soon."),
            ("pending", "Current_Pending_Sector increased by 2 (from 0 to 2)",
             "Pending sectors detected. Run extended SMART test."),
        ],
    )
    async def test_a_rising_sector_count_alerts_every_enabled_channel(
        self, smart_disk, channels, monkeypatch, counter, message, recommendation
    ):
        monkeypatch.setattr(health_router, "SMART_CHECK_INTERVAL", 0)
        monkeypatch.setattr(health_router, "_smart_monitor_running", True)
        smart_disk.stop_after = 3
        real_pass = health_router._smart_monitor_pass

        def monitor_pass():
            alerts = real_pass()
            setattr(smart_disk, counter, 2)  # the count rises before the next pass, then stays
            return alerts

        monkeypatch.setattr(health_router, "_smart_monitor_pass", monitor_pass)
        await asyncio.wait_for(health_router.smart_monitor_loop(), 10)

        assert smart_disk.rounds == 3
        # Once each, at the pass that saw the rise; not the disabled channel.
        assert [(channel, address) for channel, address, _ in channels] == [
            ("email", ["me@example"]), ("telegram", "42")]
        for _, _, event in channels:
            assert (event.type.value, event.title) == ("warning", "Disk Health Warning: /dev/sda")
            assert event.message == f"{message}\n\nRecommendation: {recommendation}"


class TestSmartHistoryWrites:
    """The SMART history is written to the system disk only when a pass changes it (ADR-0269).

    Every pass records each awake disk's snapshot in RAM, where the SMART
    endpoints read it; the file is written when a snapshot changes the
    history, and at shutdown (ADR-0214).
    """

    @pytest.fixture
    def smart_host(self, monkeypatch, tmp_path):
        """The host's whole disks (FakeHost) and what smartctl reports for each path.

        ``smart_host.reports[path]`` is (smartctl's serial, reallocated sectors);
        a disk without a report sleeps (exit status bit 1, no snapshot).
        """
        fake = install_fake_host(monkeypatch)
        fake.history_file = tmp_path / "smart_history.json"
        monkeypatch.setattr(health_router, "SMART_HISTORY_FILE", str(fake.history_file))
        fake.reports = {}

        def smartctl(cmd, **kwargs):
            assert cmd[:-1] == ["smartctl", "-n", "standby", "-j", "-a"], cmd
            if cmd[-1] not in fake.reports:
                return subprocess.CompletedProcess(cmd, 2, "", "")
            serial, reallocated = fake.reports[cmd[-1]]
            return subprocess.CompletedProcess(cmd, 0, _smartctl_json(serial, reallocated), "")

        monkeypatch.setattr(host, "run_in_container", smartctl)
        return fake

    @staticmethod
    def stored() -> dict[str, list[dict]]:
        return health_router.load_smart_history()

    def test_every_pass_is_recorded_in_ram_where_the_endpoints_read_it(self, smart_disk):
        for temperature in (35, 36, 37):
            smart_disk.temperature = temperature
            assert health_router._smart_monitor_pass() == []

        with admin_client() as api:
            history = api.get("/api/v1/health/smart/history/WD-1").json()
            status = api.get("/api/v1/health/smart-monitor/status").json()
            [trend] = api.get("/api/v1/health/smart/trends").json()["disks"]

        assert history["entries"] == 3
        assert [(e["device"], e["attributes"], e["temperature"]) for e in history["data"]] == [
            ("/dev/sda", {"5": 0, "197": 0}, temperature) for temperature in (35, 36, 37)]
        assert (status["disks_tracked"], status["total_data_points"]) == (1, 3)
        assert trend["data_points"] == 3
        # The file holds the first snapshot only: nothing changed since.
        assert [e["temperature"] for e in self.stored()["WD-1"]] == [35]

    def test_a_pass_that_changes_nothing_writes_nothing(self, smart_disk):
        health_router._smart_monitor_pass()  # the disk's first snapshot is written
        inode = smart_disk.history_file.stat().st_ino
        # The time, the temperature and the power-on hours change at every pass.
        for temperature in (36, 37, 38):
            smart_disk.temperature, smart_disk.hours = temperature, smart_disk.hours + 1
            assert health_router._smart_monitor_pass() == []
        # smartctl could not read the attribute table: the counters did not change.
        smart_disk.reallocated = None
        assert health_router._smart_monitor_pass() == []
        smart_disk.reallocated = 0
        assert health_router._smart_monitor_pass() == []

        assert smart_disk.history_file.stat().st_ino == inode  # never replaced
        assert [e["temperature"] for e in self.stored()["WD-1"]] == [35]
        assert sorted(p.name for p in smart_disk.history_file.parent.iterdir()) == ["smart_history.json"]

    @pytest.mark.parametrize("change", ["a new disk", "a counter", "another path", "another lsblk serial"])
    def test_a_pass_that_changes_the_history_writes_it(self, smart_host, change):
        smart_host.add_disk("sda", serial="WD-1")
        smart_host.add_disk("sdb", serial="WD-2")
        smart_host.reports = {"/dev/sda": ("WD-1", 0), "/dev/sdb": ("WD-2", 0)}
        health_router._smart_monitor_pass()
        health_router._smart_monitor_pass()
        assert {serial: len(entries) for serial, entries in self.stored().items()} == {"WD-1": 1, "WD-2": 1}

        if change == "a new disk":
            smart_host.add_disk("sdc", serial="WD-3")
            smart_host.reports["/dev/sdc"] = ("WD-3", 0)
        elif change == "a counter":
            smart_host.reports["/dev/sda"] = ("WD-1", 1)
        elif change == "another path":
            # After a reboot the kernel named the two disks the other way round.
            smart_host.node("sda")["serial"], smart_host.node("sdb")["serial"] = "WD-2", "WD-1"
            smart_host.reports = {"/dev/sda": ("WD-2", 0), "/dev/sdb": ("WD-1", 0)}
        else:
            # The disk now sits behind a USB bridge that reports a serial of its own.
            smart_host.node("sda")["serial"] = "BRIDGE-1"
        health_router._smart_monitor_pass()

        # Every snapshot in RAM is written, those of the pass that changed nothing too.
        expected = {"WD-1": 3, "WD-2": 3, **({"WD-3": 1} if change == "a new disk" else {})}
        assert {serial: len(entries) for serial, entries in self.stored().items()} == expected

    def test_a_start_that_changes_nothing_writes_nothing(self, smart_disk, monkeypatch):
        health_router._smart_monitor_pass()  # the history of the supervisor's run before
        inode = smart_disk.history_file.stat().st_ino
        monkeypatch.setattr(health_router, "_smart_history", health_router._SmartHistoryState())

        # The first pass after the start reads the file and adds its snapshot in RAM.
        smart_disk.temperature = 36
        assert health_router._smart_monitor_pass() == []

        assert smart_disk.history_file.stat().st_ino == inode
        with admin_client() as api:
            history = api.get("/api/v1/health/smart/history/WD-1").json()
        assert [e["temperature"] for e in history["data"]] == [35, 36]

    def test_the_first_pass_after_a_start_compares_with_the_stored_history(self, smart_disk, monkeypatch):
        health_router._smart_monitor_pass()
        monkeypatch.setattr(health_router, "_smart_history", health_router._SmartHistoryState())

        smart_disk.reallocated = 2
        [alert] = health_router._smart_monitor_pass()

        assert (alert.previous_value, alert.current_value) == (0, 2)
        assert [e["attributes"]["5"] for e in self.stored()["WD-1"]] == [0, 2]

    async def test_the_snapshots_in_ram_are_written_at_shutdown(self, smart_disk):
        for temperature in (35, 36, 37):
            smart_disk.temperature = temperature
            health_router._smart_monitor_pass()

        await health_router.stop_health_monitors()

        assert [e["temperature"] for e in self.stored()["WD-1"]] == [35, 36, 37]
        inode = smart_disk.history_file.stat().st_ino
        await health_router.stop_health_monitors()  # nothing left to write
        assert smart_disk.history_file.stat().st_ino == inode

    async def test_a_shutdown_before_the_first_pass_writes_nothing(self, smart_disk):
        await health_router.stop_health_monitors()

        assert not smart_disk.history_file.exists()

    async def test_a_pass_that_ends_after_the_shutdown_keeps_nothing_and_alerts_at_the_next_start(
        self, smart_disk, monkeypatch
    ):
        health_router._smart_monitor_pass()
        await health_router.stop_health_monitors()
        inode = smart_disk.history_file.stat().st_ino

        # A pass that was still asking smartctl when the supervisor stopped: its alert
        # could no longer be sent, so it keeps nothing.
        smart_disk.reallocated = 2
        assert health_router._smart_monitor_pass() == []
        assert smart_disk.history_file.stat().st_ino == inode

        # The next start compares with the history written at shutdown.
        monkeypatch.setattr(health_router, "_smart_history", health_router._SmartHistoryState())
        [alert] = health_router._smart_monitor_pass()
        assert (alert.previous_value, alert.current_value) == (0, 2)

    def test_a_failed_write_is_tried_again_and_the_alert_still_goes_out(self, smart_disk, monkeypatch, caplog):
        health_router._smart_monitor_pass()
        save = health_router.save_smart_history

        def disk_full(history):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(health_router, "save_smart_history", disk_full)
        smart_disk.reallocated = 4
        [alert] = health_router._smart_monitor_pass()

        assert alert.current_value == 4
        assert f"could not write the SMART history {smart_disk.history_file}: " in caplog.text
        assert "No space left on device" in caplog.text
        assert [e["attributes"]["5"] for e in self.stored()["WD-1"]] == [0]

        # The next pass changes nothing, but writes the rise that is not on disk yet.
        monkeypatch.setattr(health_router, "save_smart_history", save)
        assert health_router._smart_monitor_pass() == []
        assert [e["attributes"]["5"] for e in self.stored()["WD-1"]] == [0, 4, 4]

    @pytest.mark.parametrize("serial", [None, "", "  "])
    def test_disks_without_a_serial_neither_alert_nor_write_at_every_pass(self, smart_host, serial):
        # smartctl reports no serial, or an empty one, for either disk: both are recorded
        # under "unknown".
        smart_host.add_disk("sdb")
        smart_host.add_disk("sdc")
        smart_host.reports = {"/dev/sdb": (serial, 0), "/dev/sdc": (serial, 8)}

        alerts = [health_router._smart_monitor_pass()]
        inode = smart_host.history_file.stat().st_ino
        alerts += [health_router._smart_monitor_pass() for _ in range(2)]

        assert alerts == [[], [], []]
        assert smart_host.history_file.stat().st_ino == inode
        assert list(self.stored()) == ["unknown"]
        # A rise of one of them alerts once.
        smart_host.reports["/dev/sdb"] = (None, 3)
        [rise] = health_router._smart_monitor_pass()
        assert (rise.device, rise.serial, rise.previous_value, rise.current_value) == ("/dev/sdb", "unknown", 0, 3)


class TestSmartTrendsEndpoint:
    """GET /health/smart/trends reports the trend of every disk the history tracks (ADR-0270)."""

    @pytest.fixture
    def history(self, monkeypatch, tmp_path):
        """Stores a SMART history as the monitor writes it; nothing on the host is asked."""
        path = tmp_path / "smart_history.json"
        monkeypatch.setattr(health_router, "SMART_HISTORY_FILE", str(path))
        monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("ran a command"))
        # smartctl and btrfs run in the container through host.run_in_container.
        monkeypatch.setattr(host, "run_in_container", lambda *args, **kwargs: pytest.fail("ran a command"))
        return lambda data: path.write_text(json.dumps(data))

    @staticmethod
    def trends() -> dict:
        with admin_client() as api:
            response = api.get("/api/v1/health/smart/trends")
        assert response.status_code == 200, response.text
        return response.json()

    def test_every_tracked_disk_is_reported_with_its_trend(self, history):
        history({
            "WD-1": snapshots({REALLOCATED: [0, 0, 3], PENDING: [0, 0, 0]}),
            "WD-2": snapshots({REALLOCATED: [0, 0, 0], PENDING: [0, 0, 20]}, serial="WD-2", device="/dev/sdb"),
            # NVMe disks: the monitor records no ATA counters.
            "S64ANS0T": snapshots({}, serial="S64ANS0T", device="/dev/nvme0n1", count=3),
            # A disk that was removed stays in the history.
            "OLD-1": snapshots({REALLOCATED: [7, 7]}, serial="OLD-1", device="/dev/sdc"),
        })

        def rise(serial, device, attr_id, name, change, severity, recommendation):
            return {
                "device": device, "serial": serial, "model": "", "attribute_name": name,
                "attribute_id": attr_id, "current_value": change, "previous_value": 0, "change": change,
                "severity": severity, "message": f"{name} increased by {change} (from 0 to {change})",
                "recommendation": recommendation,
            }

        assert self.trends() == {
            "disks": [
                {"serial": "WD-1", "device": "/dev/sda", "data_points": 3, "alerts": [
                    rise("WD-1", "/dev/sda", 5, "Reallocated_Sector_Ct", 3, "warning",
                         "Disk is reallocating bad sectors. Consider replacing soon.")]},
                # More than 10 new pending sectors: critical.
                {"serial": "WD-2", "device": "/dev/sdb", "data_points": 3, "alerts": [
                    rise("WD-2", "/dev/sdb", 197, "Current_Pending_Sector", 20, "critical",
                         "Pending sectors detected. Run extended SMART test.")]},
                {"serial": "S64ANS0T", "device": "/dev/nvme0n1", "data_points": 3, "alerts": []},
                {"serial": "OLD-1", "device": "/dev/sdc", "data_points": 2, "alerts": []},
            ],
            "critical_count": 1,
            "warning_count": 1,
        }

    def test_a_rise_is_measured_over_the_last_day(self, history):
        # 3 new sectors 30 snapshots ago, none since: steady over the last 24.
        history({"WD-1": snapshots({REALLOCATED: [0] * 10 + [3] * 30})})

        [disk] = self.trends()["disks"]

        assert (disk["data_points"], disk["alerts"]) == (40, [])

    @pytest.mark.parametrize("steady, rising", [(23, True), (24, False)])
    def test_the_last_day_is_the_last_24_values(self, history, steady, rising):
        # The first of the last 24 values is the base: a rise right after it counts.
        history({"WD-1": snapshots({REALLOCATED: [0] + [3] * steady})})

        [disk] = self.trends()["disks"]

        assert [(a["previous_value"], a["current_value"]) for a in disk["alerts"]] == (
            [(0, 3)] if rising else [])

    def test_a_snapshot_without_the_counters_is_neither_a_drop_nor_a_rise(self, history):
        history({"WD-1": snapshots({REALLOCATED: [None, 8, None, 8]})})

        [disk] = self.trends()["disks"]

        assert (disk["data_points"], disk["alerts"]) == (4, [])

    def test_the_disks_without_a_serial_are_told_apart_by_their_paths(self, history):
        quiet = snapshots({REALLOCATED: [0, 0, 0]}, serial="unknown", device="/dev/sdb", lsblk_serial=None)
        worn = snapshots({REALLOCATED: [8, 8, 8]}, serial="unknown", device="/dev/sdc", lsblk_serial=None)
        history({"unknown": [entry for pair in zip(quiet, worn) for entry in pair]})

        assert [(d["serial"], d["device"], d["data_points"], d["alerts"]) for d in self.trends()["disks"]] == [
            ("unknown", "/dev/sdb", 3, []), ("unknown", "/dev/sdc", 3, [])]

    def test_without_a_history_no_disk_is_tracked(self, history):
        assert self.trends() == {"disks": [], "critical_count": 0, "warning_count": 0}


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

    def test_a_raid_status_without_pools_gives_its_counts_of_0(self, admin_api, monkeypatch, tmp_path):
        # The RAID monitor keeps {} on a host without pools: Prometheus keeps its gauges at 0, and
        # /health/full counts 0 pools; without a file there are neither (ADR-0324).
        status = tmp_path / "raid_status.json"
        monkeypatch.setattr(storage_pools, "list_pools", lambda: [])
        monkeypatch.setattr(health_router, "RAID_STATUS_FILE", str(status))
        assert "unos_raid_pools_healthy" not in admin_api.get("/api/v1/health/prometheus").text
        assert admin_api.get("/api/v1/health/full").json()["components"]["raid"] == {
            "status": "ok", "message": "No RAID pools"}

        status.write_text("{}")

        metrics = admin_api.get("/api/v1/health/prometheus").text
        assert "unos_raid_pools_healthy 0\n" in metrics and "unos_raid_pools_degraded 0\n" in metrics
        assert admin_api.get("/api/v1/health/full").json()["components"]["raid"] == {"status": "ok", "pools_checked": 0}

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
