"""Problems reach every enabled channel once and show in the web UI's banner (system.alert-events, ADR-0332).

``src/health/problems.py`` keeps the problems of the NAS that are still there, by source: the RAID
monitor reports those of the managed pools, the SMART monitor those of the disks, and a check of
the scheduler every 5 minutes those of the scheduled tasks. A problem is alerted once, through
every enabled channel, when it is new; one that is gone is dropped without an alert, and alerted
again when it comes back. The events socket sends administrators the list right after auth_ok and
whenever it changes (test_ws_auth.py). Nothing of it is written to disk (ADR-0214).
"""

import asyncio
import importlib
import json
import logging
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from src import host
from src.health import problems
from src.notifications.schemas import (
    DiscordConfig,
    EmailConfig,
    NotificationChannel,
    NotificationConfig,
    TelegramConfig,
    WebhookConfig,
)
from src.scheduler.schemas import ScheduledTask, TaskRun
from src.storage import pools as storage_pools
from src.storage.schemas import PoolInfo

from .test_pool_persistence import BASE_FSTAB, MEDIA_UUID, entry, install_fake_host, marker, wait_for_app_starts
from .test_scheduler_validation import make_env

health_router = importlib.import_module("src.health.router")
scheduler = importlib.import_module("src.scheduler.router")

UTC = timezone.utc


def _problem(key: str = "pool_degraded:data", severity: str = "error", title: str = "Pool 'data' is degraded",
             **fields) -> problems.Problem:
    kind, _, subject = key.partition(":")
    fields.setdefault("detail", "1 device(s) of the pool are missing.")
    return problems.Problem(key=key, severity=severity, kind=kind, subject=subject, title=title, **fields)


def _found(*found: problems.Problem) -> dict[str, problems.Problem]:
    return {problem.key: problem for problem in found}


@pytest.fixture
def alerts(monkeypatch):
    """The events of the alerts that the problems send, recorded instead of sent (health.router._deliver_alert)."""
    sent = []

    async def deliver(event):
        sent.append(event)

    monkeypatch.setattr(health_router, "_deliver_alert", deliver)
    return sent


# --- the registry ------------------------------------------------------------------------------


@pytest.fixture
def alert_threads(monkeypatch):
    """A pool of its own for the e-mail alerts (health.router._ALERT_EXECUTOR), ended with the test:
    a thread that the module's pool keeps would outlive it and take the signals that other tests
    send to the process (test_dev_scenarios.py)."""
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="test-alerts")
    monkeypatch.setattr(health_router, "_ALERT_EXECUTOR", executor)
    yield executor
    executor.shutdown(wait=True)


class TestTheRegistry:
    async def test_a_new_problem_goes_once_to_every_enabled_channel_webhooks_included(
            self, monkeypatch, alert_threads):
        mail = EmailConfig(smtp_host="mail.example", smtp_user="u", smtp_password="p",
                           from_address="nas@example", to_addresses=["me@example"])
        configs = [
            NotificationConfig(name="Mail", channel=NotificationChannel.EMAIL, email_config=mail),
            NotificationConfig(name="Phone", channel=NotificationChannel.TELEGRAM,
                               telegram_config=TelegramConfig(bot_token="t", chat_id="42")),
            NotificationConfig(name="Hook", channel=NotificationChannel.WEBHOOK,
                               webhook_config=WebhookConfig(url="https://hooks.example/nas")),
            NotificationConfig(name="Chat", channel=NotificationChannel.DISCORD,
                               discord_config=DiscordConfig(webhook_url="https://discord.example/hook")),
            NotificationConfig(name="Old hook", channel=NotificationChannel.WEBHOOK, enabled=False,
                               webhook_config=WebhookConfig(url="https://hooks.example/old")),
        ]
        monkeypatch.setattr(health_router, "load_notification_configs", lambda: configs)
        sent = []

        async def send_email(config, event):
            sent.append(("email", event))
            return True, None

        async def send_through(config, event):
            sent.append((config.channel.value, event))
            return True, None

        monkeypatch.setattr(health_router, "send_email", send_email)
        monkeypatch.setattr(health_router, "send_through", send_through)

        degraded = _problem(detail="1 device(s) of the pool are missing.")
        await problems.update("pools", _found(degraded))
        await problems.update("pools", _found(degraded))  # the next check finds it again

        assert [channel for channel, _ in sent] == ["email", "telegram", "webhook", "discord"]
        for _, event in sent:
            assert (event.type.value, event.title, event.message) == (
                "error", "Pool 'data' is degraded", "1 device(s) of the pool are missing.")

    async def test_a_warning_is_alerted_as_a_warning(self, alerts):
        await problems.update("tasks", _found(_problem("task_failed:task_1", "warning", "Task 'Nightly' failed")))

        [event] = alerts
        assert (event.type.value, event.title) == ("warning", "Task 'Nightly' failed")

    async def test_a_problem_that_stays_is_alerted_once_and_keeps_the_time_it_was_found(self, alerts):
        await problems.update("pools", _found(_problem(since="2026-11-10T10:00:00+00:00")))
        await problems.update("pools", _found(_problem(detail="2 device(s) of the pool are missing.",
                                                       since="2026-11-10T10:05:00+00:00")))

        assert [event.title for event in alerts] == ["Pool 'data' is degraded"]
        [current] = problems.current()
        assert current.detail == "2 device(s) of the pool are missing."  # what the last check found
        assert current.since == "2026-11-10T10:00:00+00:00"  # since the first

    async def test_a_problem_that_is_gone_is_dropped_without_an_alert_and_alerted_again_when_it_is_back(
            self, alerts):
        await problems.update("pools", _found(_problem()))
        await problems.update("pools", {})

        assert problems.current() == []
        assert len(alerts) == 1  # nothing is sent for the recovery

        await problems.update("pools", _found(_problem()))
        assert [event.title for event in alerts] == ["Pool 'data' is degraded"] * 2

    async def test_a_problem_whose_stage_rises_is_alerted_again_and_one_whose_stage_falls_is_not(self, alerts):
        # A volume that passes further levels of its quota (storage.volume_usage): one problem, a stage per level.
        def at(stage: int) -> dict[str, problems.Problem]:
            return _found(_problem("volume_quota:data/256", "warning", f"Volume at {stage}", stage=stage,
                                   since="2026-11-01T10:00:00+00:00"))

        for stage in (80, 90, 90, 80, 90, 95):
            await problems.update("volumes", at(stage))

        assert [event.title for event in alerts] == ["Volume at 80", "Volume at 90", "Volume at 90", "Volume at 95"]
        [current] = problems.current()
        assert current.stage == 95
        # The problem is there since its first find; an alert of a further stage is of its own time.
        assert alerts[0].timestamp == current.since
        assert all(event.timestamp > current.since for event in alerts[1:])

    def test_the_stage_stays_out_of_what_the_web_ui_gets(self):
        assert _problem(stage=90).stage == 90
        assert "stage" not in _problem(stage=90).model_dump()

    async def test_the_sources_are_kept_apart(self, alerts):
        await problems.update("disks", _found(_problem("disk_failed:/dev/sda", title="Disk /dev/sda is failing")))
        await problems.update("pools", _found(_problem()))
        await problems.update("pools", {})

        assert [problem.key for problem in problems.current()] == ["disk_failed:/dev/sda"]
        assert problems.of_source("disks") == _found(problems.current()[0])
        assert problems.of_source("pools") == {}

    async def test_the_list_holds_the_errors_first_then_goes_by_title(self, alerts):
        await problems.update("tasks", _found(
            _problem("task_missed:task_1", "warning", "Task 'b' missed its run"),
            _problem("task_failed:task_2", "warning", "Task 'a' failed"),
        ))
        await problems.update("pools", _found(_problem("pool_degraded:zeta", "error", "Pool 'zeta' is degraded")))

        assert [problem.title for problem in problems.current()] == [
            "Pool 'zeta' is degraded", "Task 'a' failed", "Task 'b' missed its run"]

    async def test_the_version_changes_with_the_list_only(self, alerts):
        start = problems.version()
        await problems.update("pools", {})
        assert problems.version() == start

        await problems.update("pools", _found(_problem()))
        found = problems.version()
        assert found != start
        # Found again by the next check: the same problem, since the same time.
        await problems.update("pools", _found(_problem(since="2030-01-01T00:00:00+00:00")))
        assert problems.version() == found

        await problems.update("pools", _found(_problem(detail="2 device(s) of the pool are missing.")))
        assert problems.version() != found

    async def test_an_alert_that_fails_neither_stops_the_others_nor_the_list(self, monkeypatch, caplog):
        sent = []

        async def deliver(event):
            if event.title.startswith("Pool 'a'"):
                raise RuntimeError("the history cannot be written")
            sent.append(event.title)

        monkeypatch.setattr(health_router, "_deliver_alert", deliver)
        await problems.update("pools", _found(
            _problem("pool_degraded:a", title="Pool 'a' is degraded"),
            _problem("pool_degraded:b", title="Pool 'b' is degraded"),
        ))

        assert sent == ["Pool 'b' is degraded"]
        assert [problem.subject for problem in problems.current()] == ["a", "b"]
        assert "the history cannot be written" in caplog.text


# --- the pools ---------------------------------------------------------------------------------


def _pool(name: str = "data", **fields) -> PoolInfo:
    """A managed pool, mounted and healthy, of 1000 bytes with 100 used; ``fields`` change it."""
    values = {"name": name, "devices": ["/dev/sda"], "total_size": 1000, "used_size": 100,
              "mountpoint": f"/mnt/pools/{name}", "mounted": True, "persistent": True, "health": "healthy"}
    values.update(fields)
    return PoolInfo(**values)


UNMOUNTED = {"mounted": False, "mountpoint": None, "health": "unmounted"}


class TestThePoolRules:
    def test_a_managed_pool_that_is_not_mounted_is_a_warning(self):
        [found] = health_router.pool_problems([_pool(**UNMOUNTED)], {"data"}, {}).values()

        assert (found.key, found.kind, found.severity, found.subject) == (
            "pool_unmounted:data", "pool_unmounted", "warning", "data")
        assert found.title == "Pool 'data' is not mounted"
        assert f"Nothing is mounted at {storage_pools.pool_mountpoint('data')}" in found.detail

    def test_a_managed_pool_whose_disks_are_all_gone_is_not_mounted(self):
        """btrfs lists no filesystem of it, so the Storage page lists no pool: its fstab entry is all."""
        [found] = health_router.pool_problems([], {"data"}, {}).values()

        assert (found.key, found.severity) == ("pool_unmounted:data", "warning")
        assert "no disk" in found.detail

    def test_a_degraded_pool_is_an_error(self):
        [found] = health_router.pool_problems(
            [_pool(health="degraded", missing_devices=1)], {"data"}, {}).values()

        assert (found.key, found.severity, found.title) == ("pool_degraded:data", "error", "Pool 'data' is degraded")
        assert "1 device(s) of the pool are missing." in found.detail

    def test_errors_of_its_devices_make_a_pool_degraded_scrub_errors_too(self):
        """A scrub counts what it finds in the devices' error counters, which make the pool's health "error"."""
        [found] = health_router.pool_problems([_pool(health="error", device_errors=3)], {"data"}, {}).values()

        assert (found.key, found.severity) == ("pool_degraded:data", "error")
        assert "3 error(s)" in found.detail

    def test_a_pool_that_btrfs_made_read_only_is_read_only_and_not_also_degraded(self):
        found = health_router.pool_problems(
            [_pool(health="error", forced_read_only=True, read_only=True, missing_devices=1)], {"data"}, {})

        assert list(found) == ["pool_read_only:data"]
        assert found["pool_read_only:data"].severity == "error"
        assert "dmesg" in found["pool_read_only:data"].detail

    @pytest.mark.parametrize("used, severity", [
        (899, None), (900, "warning"), (949, "warning"), (950, "error"), (1000, "error"),
    ])
    def test_a_mounted_pool_low_on_space(self, used, severity):
        found = health_router.pool_problems([_pool(used_size=used)], {"data"}, {})

        if severity is None:
            assert found == {}
        else:
            assert [(problem.key, problem.severity) for problem in found.values()] == [
                ("pool_low_space:data", severity)]
            assert f"{used // 10}%" in found["pool_low_space:data"].title

    def test_a_pool_low_on_space_stays_so_until_it_is_below_85_percent(self):
        """A pool that hovers at 90% (a nightly backup's temporary files) is alerted once, not every night."""
        earlier = health_router.pool_problems([_pool(used_size=910)], {"data"}, {})

        for used in (899, 850):
            assert list(health_router.pool_problems([_pool(used_size=used)], {"data"}, earlier)) == [
                "pool_low_space:data"]
        assert health_router.pool_problems([_pool(used_size=849)], {"data"}, earlier) == {}
        assert health_router.pool_problems([_pool(used_size=899)], {"data"}, {}) == {}

    def test_a_pool_that_is_not_mounted_is_not_low_on_space(self):
        found = health_router.pool_problems([_pool(**UNMOUNTED, used_size=990)], {"data"}, {})

        assert list(found) == ["pool_unmounted:data"]

    def test_the_pools_that_unos_does_not_manage_have_none(self):
        pools = [_pool("found", persistent=False, **UNMOUNTED),
                 _pool("by_hand", persistent=False, health="degraded", missing_devices=1, used_size=990)]

        assert health_router.pool_problems(pools, set(), {}) == {}

    def test_a_pool_whose_state_cannot_be_read_keeps_its_problems(self):
        earlier = _found(_problem("pool_degraded:data"),
                         _problem("pool_low_space:data", "warning", "Pool 'data' is 91% full"),
                         _problem("pool_unmounted:media", "warning", "Pool 'media' is not mounted"))

        found = health_router.pool_problems([_pool(health="unknown"), _pool("media")], {"data", "media"}, earlier)

        assert sorted(found) == ["pool_degraded:data", "pool_low_space:data"]

    def test_without_the_hosts_fstab_every_problem_stays(self):
        earlier = _found(_problem(), _problem("pool_unmounted:media", "warning", "Pool 'media' is not mounted"))

        assert health_router.pool_problems([_pool(persistent=False)], None, earlier) == earlier


@pytest.fixture
def managed_mirror(monkeypatch):
    """'mirror': raid1 on sdb + sdc, mounted at /mnt/pools/mirror, with UNOS's entry in the host's fstab."""
    fake = install_fake_host(monkeypatch)
    fake.add_disk("sdb")
    fake.add_disk("sdc")
    fake.add_btrfs("mirror", MEDIA_UUID, ["/dev/sdb", "/dev/sdc"], profile="raid1")
    fake.mount_pool("mirror")
    fake.files["/etc/fstab"] = BASE_FSTAB + marker("mirror") + entry("mirror", MEDIA_UUID)
    yield fake
    wait_for_app_starts()


@pytest.fixture
def raid_rounds(monkeypatch, alerts):
    """Run rounds of the RAID monitor; each returns (the RAID monitor's own alerts, the problems' alerts).

    The RAID status is kept between the rounds, as its file keeps it on the device.
    """
    store: dict = {}
    raid_alerts = []

    async def send_raid_alert(pool, status, errors, details):
        raid_alerts.append((pool, status, errors, details))

    async def stop_after_one_round(_seconds):
        health_router._raid_monitor_running = False

    def save(status):
        store.clear()
        store.update(status)

    monkeypatch.setattr(health_router, "send_raid_alert", send_raid_alert)
    monkeypatch.setattr(health_router, "load_previous_raid_status", lambda: dict(store))
    monkeypatch.setattr(health_router, "save_raid_status", save)
    monkeypatch.setattr(health_router.asyncio, "sleep", stop_after_one_round)

    async def one_round():
        raid_alerts.clear()
        alerts.clear()
        monkeypatch.setattr(health_router, "_raid_monitor_running", True)
        await health_router.raid_monitor_loop()
        return list(raid_alerts), [event.title for event in alerts]

    return one_round


class TestThePoolsOfTheRaidMonitor:
    async def test_a_managed_pool_that_degrades_is_alerted_once_by_its_problem(self, managed_mirror, raid_rounds):
        assert await raid_rounds() == ([], [])
        assert problems.current() == []

        fs = managed_mirror.fs_by_label("mirror")
        fs["devices"].remove("/dev/sdc")
        fs["missing"] = 1
        # One alert, from the problem: not a second one from the RAID monitor's change of status.
        assert await raid_rounds() == ([], ["Pool 'mirror' is degraded"])
        assert await raid_rounds() == ([], [])
        [degraded] = problems.current()
        assert (degraded.key, degraded.severity) == ("pool_degraded:mirror", "error")

        fs["devices"].append("/dev/sdc")
        fs["missing"] = 0
        assert await raid_rounds() == ([], [])
        assert problems.current() == []

    async def test_new_errors_come_with_the_problem_once_and_alone_when_they_rise(self, managed_mirror, raid_rounds):
        await raid_rounds()
        managed_mirror.device_errors["/mnt/pools/mirror"] = 3

        raid_alerts, problem_alerts = await raid_rounds()
        assert (raid_alerts, problem_alerts) == ([], ["Pool 'mirror' is degraded"])
        assert "3 error(s)" in problems.current()[0].detail

        managed_mirror.device_errors["/mnt/pools/mirror"] = 5
        raid_alerts, problem_alerts = await raid_rounds()
        assert [(pool, status, errors) for pool, status, errors, _ in raid_alerts] == [("mirror", "error", 5)]
        assert problem_alerts == []

    async def test_a_pool_made_read_only_with_new_errors_gets_one_alert(self, managed_mirror, raid_rounds):
        await raid_rounds()
        managed_mirror.mounts[-1]["fs-options"] = "ro,space_cache=v2,subvolid=5,subvol=/"
        managed_mirror.device_errors["/mnt/pools/mirror"] = 2

        assert await raid_rounds() == ([], ["Pool 'mirror' is read-only after an error"])
        assert "2 error(s)" in problems.current()[0].detail

    async def test_a_managed_pool_whose_state_cannot_be_read_is_alerted_by_the_raid_monitor_once(
            self, managed_mirror, raid_rounds):
        """No problem of its own: its problems found before stay, and the RAID monitor says it once."""
        await raid_rounds()
        managed_mirror.fail[("btrfs", "device", "stats")] = (1, "ERROR: ioctl failed")

        raid_alerts, problem_alerts = await raid_rounds()
        assert [(pool, status) for pool, status, _, _ in raid_alerts] == [("mirror", "unknown")]
        assert problem_alerts == []
        assert await raid_rounds() == ([], [])

    async def test_the_monitor_takes_the_pools_its_own_check_read(self, managed_mirror):
        """Not those of a check of GET /health/raid that ended meanwhile: the read is kept on the
        event loop as the check returns."""
        health_router._raid_health_and_pools()  # a check in a worker thread keeps nothing
        assert health_router._take_pools_read() is None

        [info] = await health_router.check_raid_health()
        read = health_router._take_pools_read()
        assert [pool.name for pool in read.pools] == [info.pool] == ["mirror"]
        assert read.managed == {"mirror"}
        assert health_router._take_pools_read() is None

    async def test_a_managed_pool_that_is_not_mounted_is_alerted_by_its_problem(self, managed_mirror, raid_rounds):
        managed_mirror.mounts.pop()  # the host did not mount it at boot

        assert await raid_rounds() == ([], ["Pool 'mirror' is not mounted"])
        assert await raid_rounds() == ([], [])

    async def test_a_managed_pool_whose_disks_are_gone_is_alerted(self, managed_mirror, raid_rounds):
        managed_mirror.mounts.pop()
        managed_mirror.filesystems.clear()  # btrfs finds none of its devices

        assert await raid_rounds() == ([], ["Pool 'mirror' is not mounted"])

    async def test_a_managed_pool_low_on_space(self, managed_mirror, raid_rounds):
        size = 2 * 4_000_787_030_016  # the fake's two disks, as btrfs filesystem usage reports them
        managed_mirror.fs_by_label("mirror")["used"] = int(size * 0.925)

        assert await raid_rounds() == ([], ["Pool 'mirror' is 92% full"])
        [low] = problems.current()
        assert (low.key, low.severity) == ("pool_low_space:mirror", "warning")

    async def test_the_pools_that_unos_does_not_manage_keep_the_alerts_of_the_raid_monitor(
            self, managed_mirror, raid_rounds):
        managed_mirror.files["/etc/fstab"] = BASE_FSTAB
        fs = managed_mirror.fs_by_label("mirror")
        fs["devices"].remove("/dev/sdc")
        fs["missing"] = 1

        raid_alerts, problem_alerts = await raid_rounds()
        assert [(pool, status) for pool, status, _, _ in raid_alerts] == [("mirror", "degraded")]
        assert problem_alerts == [] and problems.current() == []


# --- the disks ---------------------------------------------------------------------------------


def _smartctl_json(serial: str, passed: bool | None, self_test: dict | None = None,
                   nvme_test: dict | None = None) -> str:
    """smartctl -j -a of a disk: its overall health (None: not reported) and the newest entry of its self-test log."""
    data: dict = {"serial_number": serial, "temperature": {"current": 35},
                  "ata_smart_attributes": {"table": [{"id": 5, "name": "Reallocated_Sector_Ct", "raw": {"value": 0}}]}}
    if passed is not None:
        data["smart_status"] = {"passed": passed}
    if self_test is not None:
        data["ata_smart_self_test_log"] = {"standard": {"table": [
            {"type": {"value": 1, "string": "Short offline"}, "status": self_test, "lifetime_hours": 100},
            {"type": {"value": 1, "string": "Short offline"},
             "status": {"value": 0, "string": "Completed without error", "passed": True}, "lifetime_hours": 90},
        ]}}
    if nvme_test is not None:
        data["nvme_self_test_log"] = {"table": [
            {"self_test_code": {"value": 1, "string": "Short"}, "self_test_result": nvme_test, "power_on_hours": 100}]}
    return json.dumps(data)


READ_FAILURE = {"value": 121, "string": "Completed: read failure", "remaining_percent": 90, "passed": False}


@pytest.fixture
def smart_disks(monkeypatch, tmp_path):
    """The disks sda (WD-1) and sdb (WD-2) of the fake host; ``reports[path]`` is the JSON smartctl prints
    for it, and a disk without a report sleeps (exit status bit 1, no data)."""
    fake = install_fake_host(monkeypatch)
    fake.add_disk("sda", serial="WD-1")
    fake.add_disk("sdb", serial="WD-2")
    fake.history_file = tmp_path / "smart_history.json"
    monkeypatch.setattr(health_router, "SMART_HISTORY_FILE", str(fake.history_file))
    fake.reports = {"/dev/sda": _smartctl_json("WD-1", True), "/dev/sdb": _smartctl_json("WD-2", True)}

    def smartctl(cmd, **kwargs):
        assert cmd[:-1] == ["smartctl", "-n", "standby", "-j", "-a"], cmd
        report = fake.reports.get(cmd[-1])
        if report is None:
            return subprocess.CompletedProcess(cmd, 2, "", "")
        # Bit 3: the disk's overall health is FAILED; smartctl still prints the data.
        failing = json.loads(report).get("smart_status", {}).get("passed") is False
        return subprocess.CompletedProcess(cmd, 8 if failing else 0, report, "")

    monkeypatch.setattr(host, "run_in_container", smartctl)
    return fake


def _disk_problems() -> dict[str, problems.Problem]:
    return health_router.disk_problems(health_router._disk_verdicts)


class TestTheDiskRules:
    def test_a_disk_whose_health_self_assessment_failed_is_an_error(self, smart_disks):
        smart_disks.reports["/dev/sda"] = _smartctl_json("WD-1", False)
        health_router._smart_monitor_pass()

        [found] = _disk_problems().values()
        assert (found.key, found.kind, found.severity, found.subject) == (
            "disk_failed:/dev/sda", "disk_failed", "error", "/dev/sda")
        assert found.title == "Disk /dev/sda is failing"
        assert "WD-1" in found.detail

    @pytest.mark.parametrize("warning, failing", [(0x02, False), (0x04, True), (0x06, True), (0x08, True)])
    def test_an_nvme_disk_that_is_only_too_hot_is_not_failing(self, smart_disks, warning, failing):
        """smartctl calls an NVMe disk's health FAILED for any bit of its critical warning, the
        temperature's (0x02) too, which goes when the disk cools down."""
        report = json.loads(_smartctl_json("WD-1", False))
        report["smart_status"]["nvme"] = {"value": warning}
        smart_disks.reports["/dev/sda"] = json.dumps(report)
        health_router._smart_monitor_pass()

        assert list(_disk_problems()) == (["disk_failed:/dev/sda"] if failing else [])

    def test_a_disk_replaced_at_its_path_does_not_keep_the_verdict_while_it_sleeps(self, smart_disks):
        smart_disks.reports["/dev/sda"] = _smartctl_json("WD-1", False)
        health_router._smart_monitor_pass()
        smart_disks.node("sda")["serial"] = "WD-9"  # another disk at /dev/sda, which sleeps
        del smart_disks.reports["/dev/sda"]
        health_router._smart_monitor_pass()

        assert _disk_problems() == {}

    def test_a_disk_whose_last_self_test_failed_is_an_error(self, smart_disks):
        smart_disks.reports["/dev/sdb"] = _smartctl_json("WD-2", True, self_test=READ_FAILURE)
        health_router._smart_monitor_pass()

        [found] = _disk_problems().values()
        assert (found.key, found.severity) == ("disk_test_failed:/dev/sdb", "error")
        assert "Completed: read failure" in found.detail

    def test_a_failed_self_test_of_an_nvme_disk(self, smart_disks):
        smart_disks.reports["/dev/sdb"] = _smartctl_json(
            "WD-2", True, nvme_test={"value": 7, "string": "Completed: failed segments"})
        health_router._smart_monitor_pass()

        assert list(_disk_problems()) == ["disk_test_failed:/dev/sdb"]

    @pytest.mark.parametrize("status", [
        {"value": 0, "string": "Completed without error", "passed": True},
        {"value": 249, "string": "Self-test routine in progress", "remaining_percent": 90},
        {"value": 16, "string": "Aborted by host", "passed": True},
    ], ids=["passed", "in-progress", "aborted"])
    def test_a_self_test_that_did_not_fail_is_none(self, smart_disks, status):
        smart_disks.reports["/dev/sda"] = _smartctl_json("WD-1", True, self_test=status)
        smart_disks.reports["/dev/sdb"] = _smartctl_json("WD-2", None)  # a bridge that reports no health
        health_router._smart_monitor_pass()

        assert _disk_problems() == {}

    def test_a_disk_that_sleeps_keeps_its_last_state_and_one_that_passes_again_has_none(self, smart_disks):
        smart_disks.reports["/dev/sda"] = _smartctl_json("WD-1", False)
        health_router._smart_monitor_pass()
        del smart_disks.reports["/dev/sda"]  # asleep: smartctl leaves it alone
        health_router._smart_monitor_pass()

        assert list(_disk_problems()) == ["disk_failed:/dev/sda"]

        smart_disks.reports["/dev/sda"] = _smartctl_json("WD-1", True)
        health_router._smart_monitor_pass()
        assert _disk_problems() == {}

    def test_a_disk_that_is_gone_has_none(self, smart_disks):
        smart_disks.reports["/dev/sda"] = _smartctl_json("WD-1", False)
        health_router._smart_monitor_pass()
        smart_disks.tree[:] = [node for node in smart_disks.tree if node["name"] != "sda"]
        health_router._smart_monitor_pass()

        assert _disk_problems() == {}

    def test_the_verdicts_stay_out_of_the_smart_history(self, smart_disks):
        smart_disks.reports["/dev/sda"] = _smartctl_json("WD-1", False, self_test=READ_FAILURE)
        health_router._smart_monitor_pass()

        stored = json.loads(smart_disks.history_file.read_text())
        assert {key for entries in stored.values() for snapshot in entries for key in snapshot} == {
            "timestamp", "device", "serial", "lsblk_serial", "attributes", "temperature", "power_on_hours"}

    async def test_the_smart_monitor_alerts_a_failing_disk_once(self, smart_disks, alerts, monkeypatch):
        smart_disks.reports["/dev/sda"] = _smartctl_json("WD-1", False)
        rounds = 0

        async def sleep(_seconds):
            nonlocal rounds
            rounds += 1
            if rounds == 2:
                health_router._smart_monitor_running = False

        monkeypatch.setattr(health_router.asyncio, "sleep", sleep)
        monkeypatch.setattr(health_router, "_smart_monitor_running", True)
        await asyncio.wait_for(health_router.smart_monitor_loop(), 10)

        assert rounds == 2
        assert [(event.type.value, event.title) for event in alerts] == [("error", "Disk /dev/sda is failing")]


# --- the scheduled tasks -----------------------------------------------------------------------


NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


def _task(task_id: str = "task_1", name: str = "Nightly backup", **fields) -> ScheduledTask:
    """An enabled custom task, daily at 03:00, whose last run, today at 03:00 UTC, succeeded."""
    values = {"id": task_id, "name": name, "task_type": "custom", "command": "true", "frequency": "daily",
              "hour": 3, "minute": 0, "enabled": True, "last_run": "2026-11-10T03:00:01.104071+00:00",
              "last_status": "success", "schedule_since": "2026-11-01T09:00:00+00:00"}
    values.update(fields)
    return ScheduledTask(**values)


def _run(task_id: str = "task_1", started: str = "2026-11-10T03:00:01+00:00", status: str = "success",
         error: str | None = None) -> TaskRun:
    return TaskRun(id=f"run_{started}", task_id=task_id, task_name="Nightly backup", started_at=started,
                   status=status, error=error)


def _task_problems(tasks, runs=(), *, now=NOW, zone=UTC, zone_since=None) -> dict[str, problems.Problem]:
    return scheduler.task_problems(list(tasks), list(runs), zone, now, zone_since)


YESTERDAY = "2026-11-09T03:00:01+00:00"


class TestTheTaskRules:
    def test_an_enabled_task_whose_last_run_failed_is_a_warning(self):
        runs = [_run(status="failed", error="The command exited with status 2")]

        [failed] = _task_problems([_task(last_status="failed")], runs).values()
        assert (failed.key, failed.kind, failed.severity, failed.subject) == (
            "task_failed:task_1", "task_failed", "warning", "Nightly backup")
        assert failed.title == "Task 'Nightly backup' failed"
        assert "The command exited with status 2" in failed.detail

    def test_a_disabled_task_has_none(self):
        assert _task_problems([_task(last_status="failed", enabled=False, last_run=YESTERDAY)]) == {}

    def test_a_time_of_its_schedule_without_a_run_is_a_missed_run(self):
        """Runs are not made up after a stop of the supervisor (ADR-0266): today's 03:00 passed without one."""
        [missed] = _task_problems([_task(last_run=YESTERDAY)]).values()

        assert (missed.key, missed.kind, missed.severity, missed.subject) == (
            "task_missed:task_1", "task_missed", "warning", "Nightly backup")
        assert missed.title == "Task 'Nightly backup' missed its run"
        assert "2026-11-10 03:00 UTC" in missed.detail

    @pytest.mark.parametrize("minutes, missed", [(9, False), (11, True)])
    def test_a_run_is_missed_once_its_time_is_10_minutes_past(self, minutes, missed):
        now = datetime(2026, 11, 10, 3, minutes, tzinfo=UTC)

        assert list(_task_problems([_task(last_run=YESTERDAY)], now=now)) == (["task_missed:task_1"] if missed else [])

    def test_a_run_that_is_still_running_counts_from_its_start(self):
        """A task's last run is recorded when the run ends: a backup that runs for hours has started."""
        assert _task_problems([_task(last_run=YESTERDAY)], [_run(status="running")]) == {}

    def test_a_task_without_a_next_time_is_left_out(self):
        never = _task(frequency="custom", cron_expression="0 0 30 2 *", last_run=YESTERDAY)  # 30 February

        assert _task_problems([never]) == {}

    def test_a_task_without_a_start_or_a_schedule_since_is_left_out(self):
        assert _task_problems([_task(last_run=None, last_status=None, schedule_since=None)]) == {}

    def test_a_task_counts_from_when_it_was_enabled_again_or_given_another_schedule(self):
        """Disabled for a week and enabled again at 09:00, or moved to 08:00 at 09:00: the times before
        were no runs it missed, also after a restart of the supervisor."""
        enabled_again = _task(last_run="2026-11-03T03:00:01+00:00", schedule_since="2026-11-10T09:00:00+00:00")
        moved = _task(hour=8, schedule_since="2026-11-10T09:00:00+00:00")
        assert _task_problems([enabled_again, moved]) == {}

        # And from then on: the next day's runs did not come.
        later = _task_problems([enabled_again, moved], now=NOW + timedelta(days=1))
        assert [(problem.key, problem.kind) for problem in later.values()] == [("task_missed:task_1", "task_missed")]

    def test_a_new_task_that_never_ran_misses_its_first_time(self):
        new = _task(last_run=None, last_status=None, schedule_since="2026-11-09T15:00:00+00:00")

        assert list(_task_problems([new])) == ["task_missed:task_1"]

    def test_the_times_follow_the_clock_of_the_hosts_time_zone(self):
        berlin = ZoneInfo("Europe/Berlin")
        task = _task(last_run="2026-11-09T02:00:01+00:00")  # 03:00 in Berlin, an hour ahead of UTC

        assert _task_problems([task], now=datetime(2026, 11, 10, 2, 5, tzinfo=UTC), zone=berlin) == {}
        found = _task_problems([task], now=datetime(2026, 11, 10, 2, 11, tzinfo=UTC), zone=berlin)
        assert "2026-11-10 03:00 CET" in found["task_missed:task_1"].detail

    def test_the_times_before_a_change_of_the_zone_are_none_it_missed(self):
        """In New York, 03:00 came at 08:00 UTC, while the schedules still followed UTC."""
        new_york = ZoneInfo("America/New_York")
        task = _task()  # ran at 03:00 UTC

        assert list(_task_problems([task], zone=new_york)) == ["task_missed:task_1"]
        assert _task_problems([task], zone=new_york, zone_since=datetime(2026, 11, 10, 9, 0, tzinfo=UTC)) == {}

    async def test_the_check_reads_the_tasks_their_runs_and_the_zone_and_alerts_once(
            self, tmp_path, monkeypatch, alerts):
        monkeypatch.setattr(scheduler, "TASKS_FILE", str(tmp_path / "tasks.json"))
        monkeypatch.setattr(scheduler, "RUNS_FILE", str(tmp_path / "runs.json"))
        zone = {"now": UTC}
        monkeypatch.setattr(scheduler, "schedule_zone", lambda: zone["now"])
        monkeypatch.setattr(scheduler, "_utc_now", lambda: NOW)
        scheduler.save_tasks([_task(last_run=YESTERDAY, last_status="failed")])
        scheduler._save_runs([_run(started=YESTERDAY, status="failed", error="Pool data is not mounted")])

        seen = await scheduler.check_tasks(None)
        assert seen == scheduler.ZoneSeen("UTC", None)  # the first check: the zone may be older
        seen = await scheduler.check_tasks(seen)

        assert [event.title for event in alerts] == [
            "Task 'Nightly backup' failed", "Task 'Nightly backup' missed its run"]
        assert sorted(problem.key for problem in problems.current()) == ["task_failed:task_1", "task_missed:task_1"]

        zone["now"] = ZoneInfo("Europe/Berlin")
        assert await scheduler.check_tasks(seen) == scheduler.ZoneSeen("Europe/Berlin", NOW)

        scheduler.save_tasks([_task()])  # it ran today, and its run succeeded
        await scheduler.check_tasks(seen)
        assert problems.current() == []
        assert len(alerts) == 2


class TestTheTimeATaskCountsFrom:
    """The scheduler stores when a task was created, enabled again or given another schedule: the
    writes of the tasks file that these make anyway (ADR-0332)."""

    @pytest.fixture
    def tasks_api(self, tmp_path, monkeypatch):
        """The scheduler's API on test_scheduler_validation's env, on a clock that the test sets."""
        clock = {"now": datetime(2026, 11, 10, 9, 0, tzinfo=UTC)}
        monkeypatch.setattr(scheduler, "_utc_now", lambda: clock["now"])
        for env in make_env(tmp_path, monkeypatch):
            env.clock = clock
            yield env

    @staticmethod
    def _custom(**fields) -> dict:
        task = {"name": "Nightly backup", "task_type": "custom", "command": "true", "frequency": "daily",
                "hour": 3, "minute": 0, "enabled": True}
        task.update(fields)
        return task

    def _stored(self, env) -> dict:
        (task,) = json.loads((env.tmp / "tasks.json").read_text())
        return task

    def test_a_new_task_counts_from_its_creation_whatever_the_request_says(self, tasks_api):
        created = tasks_api.client.post(
            "/api/v1/scheduler/tasks", json=self._custom(schedule_since="2020-01-01T00:00:00+00:00"))

        assert created.status_code == 200, created.text
        assert self._stored(tasks_api)["schedule_since"] == "2026-11-10T09:00:00+00:00"

    def test_an_edit_of_its_schedule_or_of_enabled_counts_from_then_and_other_edits_keep_it(self, tasks_api):
        task_id = tasks_api.client.post("/api/v1/scheduler/tasks", json=self._custom()).json()["id"]
        url = f"/api/v1/scheduler/tasks/{task_id}"

        tasks_api.clock["now"] = datetime(2026, 11, 10, 10, 0, tzinfo=UTC)
        assert tasks_api.client.put(url, json=self._custom(name="Backup", command="date")).status_code == 200
        assert self._stored(tasks_api)["schedule_since"] == "2026-11-10T09:00:00+00:00"

        tasks_api.clock["now"] = datetime(2026, 11, 10, 11, 0, tzinfo=UTC)
        assert tasks_api.client.put(url, json=self._custom(hour=8)).status_code == 200
        assert self._stored(tasks_api)["schedule_since"] == "2026-11-10T11:00:00+00:00"

        tasks_api.clock["now"] = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
        assert tasks_api.client.put(url, json=self._custom(hour=8, enabled=False)).status_code == 200
        tasks_api.clock["now"] = datetime(2026, 11, 10, 13, 0, tzinfo=UTC)
        assert tasks_api.client.put(url, json=self._custom(hour=8)).status_code == 200
        assert self._stored(tasks_api)["schedule_since"] == "2026-11-10T13:00:00+00:00"

    def test_a_task_switched_on_counts_from_then(self, tasks_api):
        task_id = tasks_api.client.post("/api/v1/scheduler/tasks", json=self._custom()).json()["id"]
        toggle = f"/api/v1/scheduler/tasks/{task_id}/toggle"

        tasks_api.clock["now"] = datetime(2026, 11, 10, 10, 0, tzinfo=UTC)
        assert tasks_api.client.post(toggle).json()["enabled"] is False
        assert self._stored(tasks_api)["schedule_since"] == "2026-11-10T09:00:00+00:00"
        tasks_api.clock["now"] = datetime(2026, 11, 17, 10, 0, tzinfo=UTC)
        assert tasks_api.client.post(toggle).json()["enabled"] is True
        assert self._stored(tasks_api)["schedule_since"] == "2026-11-17T10:00:00+00:00"


def test_the_problems_of_the_tasks_are_checked_every_5_minutes():
    assert scheduler.TASK_CHECK_INTERVAL == 300
    assert scheduler.MISSED_AFTER == timedelta(minutes=10)
    assert (health_router.POOL_LOW_SPACE, health_router.POOL_NEARLY_FULL) == (0.90, 0.95)


def test_the_check_of_the_tasks_logs_its_failures_and_goes_on(monkeypatch, caplog, alerts):
    calls = []

    async def check_tasks(watched):
        calls.append(watched)
        if len(calls) == 1:
            raise OSError("the tasks file cannot be read")
        scheduler._scheduler_running = False
        return scheduler.ZoneSeen("UTC", None)

    slept = []

    async def sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(scheduler, "check_tasks", check_tasks)
    monkeypatch.setattr(scheduler, "_sleep", sleep)
    monkeypatch.setattr(scheduler, "_scheduler_running", True)
    with caplog.at_level(logging.ERROR):
        asyncio.run(asyncio.wait_for(scheduler.task_check_loop(), 5))

    assert calls == [None, None]  # the failed check changed nothing: still the first
    assert slept == [scheduler.TASK_CHECK_INTERVAL] * 2
    assert "the tasks file cannot be read" in caplog.text
