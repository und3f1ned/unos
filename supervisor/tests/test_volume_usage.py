"""How much of its quota each volume uses, alerts at 80, 90 and 95 percent, and its history
(storage.volume-quota-usage, ADR-0383).

The use is read from the kernel's sysfs, which the supervisor's container shares with the host:
``/sys/fs/btrfs/<fsid>/qgroups/0_<id>/{referenced,max_referenced,limit_flags}`` and
``qgroups/inconsistent``, and ``/sys/fs/btrfs/<fsid>/generation``. ``FakeSysfs`` writes those files
into a folder of the test, as Linux 6.1 prints them (``fs/btrfs/sysfs.c``: a number and a line
break). The volumes' names and ids come from the FakeHost and the FakeSubvolumes of
test_volumes.py, through ``src.host.run_on_host``.

The RAID monitor's pass (every 5 minutes) reads the use of the volumes of every mounted pool with
quotas on: sysfs alone while the pool's generation stays the same, and a list of its volumes again
after a write. A volume that reaches 80, 90 or 95 percent of its quota is a problem whose stage is
the level, alerted once per level crossed upward; it leaves a level below the level less 5. One
point per volume per hour is kept for 30 days in RAM, and written to the system disk only at
shutdown.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import math
import os
from pathlib import Path

import pytest

from src.health import problems
from src.storage import qgroups, volume_usage
from src.storage.schemas import PoolInfo

from .test_pool_persistence import DATA_UUID, admin_client, install_fake_host, wait_for_app_starts
from .test_volumes import GIB, FakeSubvolumes

health_router = importlib.import_module("src.health.router")

API = "/api/v1/storage/pools"
HOUR = 3600
# 2026-11-17 10:00:00 UTC, the start of an hour.
T0 = 1_794_909_600


class FakeSysfs:
    """``/sys/fs/btrfs`` of the host, in a folder of the test."""

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def _write(self, path: str, value: object) -> None:
        file = self.root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(f"{value}\n")

    def quotas(self, uuid: str = DATA_UUID, *, inconsistent: bool = False) -> None:
        """Quotas on: the folder qgroups and its global attributes (Linux 6.1)."""
        self._write(f"{uuid}/qgroups/enabled", 1)
        self._write(f"{uuid}/qgroups/inconsistent", int(inconsistent))

    def quotas_off(self, uuid: str = DATA_UUID) -> None:
        for path in sorted((self.root / uuid / "qgroups").rglob("*"), reverse=True):
            path.rmdir() if path.is_dir() else path.unlink()
        (self.root / uuid / "qgroups").rmdir()

    def group(self, subvol_id: int, referenced: int, limit: int | None = None, uuid: str = DATA_UUID) -> None:
        """The quota group 0/<id>: its referenced bytes and, with ``limit``, a limit on them."""
        self._write(f"{uuid}/qgroups/0_{subvol_id}/referenced", referenced)
        self._write(f"{uuid}/qgroups/0_{subvol_id}/exclusive", referenced)
        self._write(f"{uuid}/qgroups/0_{subvol_id}/max_referenced", limit or 0)
        self._write(f"{uuid}/qgroups/0_{subvol_id}/max_exclusive", 0)
        # BTRFS_QGROUP_LIMIT_MAX_RFER is bit 0.
        self._write(f"{uuid}/qgroups/0_{subvol_id}/limit_flags", 1 if limit is not None else 0)

    def generation(self, value: int, uuid: str = DATA_UUID) -> None:
        self._write(f"{uuid}/generation", value)


@pytest.fixture
def host(monkeypatch, tmp_path):
    """Pool "data" mounted at /mnt/pools/data, with quotas on, the volumes documents (a quota of
    10 GiB, 5 GiB used) and photos (no quota, 3 GiB used), and the generation 100."""
    fake = install_fake_host(monkeypatch)
    fake.add_disk("sda", serial="WD-1")
    fake.add_btrfs("data", DATA_UUID, ["/dev/sda"])
    fake.mount_pool("data")
    fake.subvols = FakeSubvolumes(fake)
    fake.subvols.quotas.add(DATA_UUID)
    fake.documents = fake.subvols.add(DATA_UUID, "documents")
    fake.photos = fake.subvols.add(DATA_UUID, "photos")
    fake.dirs.update({"/mnt/pools/data/documents", "/mnt/pools/data/photos"})
    sysfs = FakeSysfs(tmp_path / "sys" / "fs" / "btrfs")
    monkeypatch.setattr(qgroups, "SYS_FS_BTRFS", str(sysfs.root))
    sysfs.quotas()
    sysfs.group(fake.documents, 5 * GIB, 10 * GIB)
    sysfs.group(fake.photos, 3 * GIB)
    sysfs.generation(100)
    fake.sysfs = sysfs
    yield fake
    wait_for_app_starts()


@pytest.fixture
def clock(monkeypatch):
    """The time of the monitor's passes: ``clock[0]``, seconds since the epoch."""
    now = [T0]
    monkeypatch.setattr(volume_usage, "_now", lambda: now[0])
    return now


@pytest.fixture
def alerts(monkeypatch):
    """The titles of the alerts that the problems send, recorded instead of sent."""
    sent = []

    async def deliver(event):
        sent.append((event.type.value, event.title))

    monkeypatch.setattr(health_router, "_deliver_alert", deliver)
    return sent


def data_pool(**fields) -> PoolInfo:
    """The pool data as the RAID monitor's read gives it: mounted at its mountpoint."""
    values = {"name": "data", "uuid": DATA_UUID, "devices": ["/dev/sda"], "total_size": 1000 * GIB,
              "used_size": GIB, "mountpoint": "/mnt/pools/data", "mounted": True, "persistent": True,
              "health": "healthy"}
    values.update(fields)
    return PoolInfo(**values)


def check(*infos: PoolInfo) -> None:
    """One pass of the monitor over the pools that the RAID monitor read."""
    asyncio.run(volume_usage.check(list(infos) or [data_pool()]))


def use(host, percent: float, subvol_id: int | None = None, limit: int = 10 * GIB) -> None:
    """The volume (documents by default) uses ``percent`` of its quota; a write: a new generation."""
    # Rounded up: the percentage that the alert names is rounded down.
    host.sysfs.group(subvol_id or host.documents, math.ceil(limit * percent / 100), limit)
    write(host)


def write(host) -> None:
    generation = int((host.sysfs.root / DATA_UUID / "generation").read_text()) + 1
    host.sysfs.generation(generation)


def volume_problems() -> list[problems.Problem]:
    return list(problems.of_source(volume_usage.SOURCE).values())


def host_commands(host) -> list[list[str]]:
    return [list(cmd) for cmd in host.calls]


# --- step 1: the Volumes dialog shows each volume's use against its quota -------------------------


class TestTheList:
    def test_each_volume_has_its_use_against_its_quota(self, host):
        with admin_client() as api:
            response = api.get(f"{API}/data/volumes")

        assert response.status_code == 200, response.text
        assert response.json() == [
            {"name": "documents", "pool": "data", "path": "/mnt/pools/data/documents",
             "used": 5 * GIB, "quota": 10 * GIB, "usage": "counted"},
            {"name": "photos", "pool": "data", "path": "/mnt/pools/data/photos",
             "used": 3 * GIB, "quota": None, "usage": "counted"},
        ]

    def test_the_use_comes_from_sysfs_without_a_quota_group_command(self, host):
        with admin_client() as api:
            api.get(f"{API}/data/volumes")

        assert not [cmd for cmd in host_commands(host) if cmd[:2] == ["btrfs", "qgroup"]]

    def test_quotas_off_on_the_pool_are_said_and_nothing_is_counted(self, host):
        host.sysfs.quotas_off()

        with admin_client() as api:
            listed = api.get(f"{API}/data/volumes").json()

        assert [(v["name"], v["used"], v["quota"], v["usage"]) for v in listed] == [
            ("documents", None, None, "quotas_off"), ("photos", None, None, "quotas_off")]

    def test_counts_that_need_a_rescan_are_shown_with_their_state(self, host):
        host.sysfs.quotas(inconsistent=True)

        with admin_client() as api:
            [documents, _] = api.get(f"{API}/data/volumes").json()

        assert (documents["used"], documents["quota"], documents["usage"]) == (5 * GIB, 10 * GIB, "inconsistent")

    def test_a_volume_whose_quota_group_cannot_be_read_is_unknown(self, host):
        (host.sysfs.root / DATA_UUID / "qgroups" / f"0_{host.photos}" / "referenced").unlink()

        with admin_client() as api:
            [documents, photos] = api.get(f"{API}/data/volumes").json()

        assert documents["usage"] == "counted"
        assert (photos["used"], photos["quota"], photos["usage"]) == (None, None, "unknown")

    def test_a_limit_that_was_removed_is_no_quota(self, host):
        # btrfs qgroup limit none clears the flag; the number that sysfs prints is not the limit then.
        host.sysfs.group(host.documents, 5 * GIB)
        (host.sysfs.root / DATA_UUID / "qgroups" / f"0_{host.documents}" / "max_referenced").write_text(f"{GIB}\n")

        with admin_client() as api:
            [documents, _] = api.get(f"{API}/data/volumes").json()

        assert (documents["used"], documents["quota"]) == (5 * GIB, None)


# --- step 2: crossing 80, 90 or 95 percent sends an alert -----------------------------------------


class TestTheAlerts:
    def test_crossing_80_90_and_95_percent_sends_one_alert_each(self, host, clock, alerts):
        for percent in (70, 80, 85, 90, 93, 95, 99):
            use(host, percent)
            check()

        assert alerts == [
            ("warning", "Volume 'documents' of pool 'data' has used 80% of its quota"),
            ("warning", "Volume 'documents' of pool 'data' has used 90% of its quota"),
            ("error", "Volume 'documents' of pool 'data' has used 95% of its quota"),
        ]
        [problem] = volume_problems()
        assert (problem.key, problem.kind, problem.subject, problem.severity, problem.stage) == (
            f"volume_quota:data/{host.documents}", "volume_quota_95", "data/documents", "error", 95)
        assert problem.title == "Volume 'documents' of pool 'data' has used 99% of its quota"
        assert problem.detail == (
            "9.90 GiB of the 10.00 GiB quota of volume documents in pool data are used. When it is "
            "full, writes to the volume fail: delete files in it, or raise its quota on the host "
            "(btrfs qgroup limit <size> /mnt/pools/data/documents).")

    def test_a_volume_at_a_level_is_alerted_once_and_again_only_after_it_fell_below_the_level_less_5(
            self, host, clock, alerts):
        for percent in (80, 79, 76, 80, 81):
            use(host, percent)
            check()
        assert [title for _, title in alerts] == ["Volume 'documents' of pool 'data' has used 80% of its quota"]

        use(host, 74.9)  # below 75: the level is left, and the problem is gone without an alert
        check()
        assert volume_problems() == [] and len(alerts) == 1

        use(host, 80)
        check()
        assert len(alerts) == 2

    def test_falling_back_under_a_level_sends_nothing(self, host, clock, alerts):
        use(host, 96)
        check()
        alerts.clear()

        use(host, 92)  # still at 95: it is left below 90
        check()
        assert volume_problems()[0].kind == "volume_quota_95"
        use(host, 89)
        check()
        [problem] = volume_problems()
        assert (problem.kind, problem.severity, problem.stage) == ("volume_quota_90", "warning", 90)
        use(host, 80)
        check()
        assert volume_problems()[0].kind == "volume_quota_80"
        assert alerts == []

        use(host, 91)  # 90 again, after the use fell below 85
        check()
        assert alerts == [("warning", "Volume 'documents' of pool 'data' has used 91% of its quota")]

    def test_several_levels_crossed_in_one_pass_send_one_alert_for_the_highest(self, host, clock, alerts):
        use(host, 50)
        check()
        use(host, 97)
        check()

        assert alerts == [("error", "Volume 'documents' of pool 'data' has used 97% of its quota")]

    def test_a_volume_without_a_quota_or_below_80_percent_has_no_problem(self, host, clock, alerts):
        host.sysfs.group(host.photos, 900 * GIB)
        use(host, 79.9)
        check()

        assert volume_problems() == [] and alerts == []

    def test_a_volume_whose_use_cannot_be_read_keeps_its_problem(self, host, clock, alerts):
        use(host, 85)
        check()
        (host.sysfs.root / DATA_UUID / "qgroups" / f"0_{host.documents}" / "referenced").unlink()
        check()

        assert [p.kind for p in volume_problems()] == ["volume_quota_80"]
        assert len(alerts) == 1

    def test_a_renamed_volume_keeps_its_problem_under_its_new_name_without_a_new_alert(self, host, clock, alerts):
        use(host, 91)
        check()
        host.subvols.subvolumes[DATA_UUID][host.documents] = (5, "papers")
        write(host)
        check()

        [problem] = volume_problems()
        assert (problem.subject, problem.title) == ("data/papers", "Volume 'papers' of pool 'data' has used 91% of its quota")
        assert len(alerts) == 1

    def test_a_deleted_volume_s_problem_goes(self, host, clock, alerts):
        use(host, 91)
        check()
        del host.subvols.subvolumes[DATA_UUID][host.documents]
        write(host)
        check()

        assert volume_problems() == []

    def test_quotas_turned_off_or_the_pool_unmounted_drop_the_problems(self, host, clock, alerts):
        use(host, 91)
        check()
        check(data_pool(mounted=False, mountpoint=None, health="unmounted"))
        assert volume_problems() == []

        check()
        assert len(volume_problems()) == 1
        host.sysfs.quotas_off()
        check()
        assert volume_problems() == []

    def test_a_pool_whose_mount_could_not_be_read_keeps_its_problems(self, host, clock, alerts):
        use(host, 91)
        check()
        # The host's mount table could not be read: not known to be mounted, its health unknown.
        check(data_pool(mounted=False, mountpoint=None, health="unknown"))
        assert [p.kind for p in volume_problems()] == ["volume_quota_90"]
        check()

        assert len(alerts) == 1

    def test_a_volume_whose_id_btrfs_could_not_resolve_keeps_its_problem_and_history(self, host, clock, alerts):
        use(host, 91)
        check()
        host.fail[("btrfs", "inspect-internal", "subvolid-resolve")] = (1, "ERROR: resolving subvolid failed: Input/output error")
        write(host)
        clock[0] = T0 + HOUR
        check()

        assert [p.kind for p in volume_problems()] == ["volume_quota_90"]
        assert len(points(host.documents)) == 1
        del host.fail[("btrfs", "inspect-internal", "subvolid-resolve")]
        check()
        assert len(points(host.documents)) == 2
        assert len(alerts) == 1

    def test_a_pool_whose_volumes_cannot_be_listed_keeps_its_problems(self, host, clock, alerts):
        use(host, 91)
        check()
        host.fail[("btrfs", "subvolume", "list")] = (1, "ERROR: can't perform the search: Input/output error")
        write(host)
        check()

        assert [p.kind for p in volume_problems()] == ["volume_quota_90"]
        assert len(alerts) == 1

    def test_the_raid_monitor_checks_the_volumes_at_each_pass(self, host, clock, alerts, monkeypatch):
        async def stop_after_one_round(_seconds):
            health_router._raid_monitor_running = False

        monkeypatch.setattr(health_router, "load_previous_raid_status", lambda: {})
        monkeypatch.setattr(health_router, "save_raid_status", lambda status: None)
        monkeypatch.setattr(health_router.asyncio, "sleep", stop_after_one_round)
        monkeypatch.setattr(health_router, "_raid_monitor_running", True)
        use(host, 91)

        asyncio.run(health_router.raid_monitor_loop())

        assert ("warning", "Volume 'documents' of pool 'data' has used 91% of its quota") in alerts
        assert health_router.RAID_CHECK_INTERVAL == 300


class TestNoDiskIsWoken:
    """A pass reads sysfs alone while nothing wrote to the pool (ADR-0378)."""

    def test_the_volumes_are_listed_again_only_after_a_write(self, host, clock):
        check()
        first = host_commands(host)
        assert ["btrfs", "inspect-internal", "rootid", "/mnt/pools/data"] in first
        assert ["btrfs", "subvolume", "list", "/mnt/pools/data"] in first
        # Once more at the next pass: the transaction whose number the first list read may still
        # have been running.
        host.calls.clear()
        check()
        assert ["btrfs", "subvolume", "list", "/mnt/pools/data"] in host_commands(host)

        host.calls.clear()
        host.sysfs.group(host.documents, 6 * GIB, 10 * GIB)  # no transaction: the generation stays
        check()
        check()
        assert host.calls == []
        assert volume_usage.usage_of(DATA_UUID, host.documents).used == 6 * GIB

        write(host)
        check()
        assert ["btrfs", "subvolume", "list", "/mnt/pools/data"] in host_commands(host)

    def test_a_rename_later_in_the_transaction_of_a_list_is_found_at_the_next_pass(self, host, clock, alerts):
        use(host, 91)
        check()
        # The same transaction: its number stays.
        host.subvols.subvolumes[DATA_UUID][host.documents] = (5, "papers")
        check()

        assert [p.subject for p in volume_problems()] == ["data/papers"]
        assert len(alerts) == 1

    def test_a_list_that_fails_is_tried_once_more_and_logged_once(self, host, clock, caplog):
        host.subvols.mounted_subvolume["/mnt/pools/data"] = host.photos
        for _ in range(4):
            check()

        rootids = [cmd for cmd in host_commands(host) if cmd[:3] == ["btrfs", "inspect-internal", "rootid"]]
        assert len(rootids) == 2
        assert caplog.text.count("Could not list the volumes of pool data") == 1

        write(host)  # tried again after a write
        check()
        assert len([cmd for cmd in host_commands(host) if cmd[:3] == ["btrfs", "inspect-internal", "rootid"]]) == 3

    def test_no_pass_scans_the_disks_or_asks_for_the_quota_groups(self, host, clock):
        for _ in range(3):
            write(host)
            check()

        for cmd in host_commands(host):
            assert cmd[:2] != ["btrfs", "qgroup"], cmd
            assert cmd[:3] != ["btrfs", "filesystem", "show"], cmd

    def test_a_pool_with_quotas_off_or_not_mounted_runs_no_command(self, host, clock):
        host.sysfs.quotas_off()
        check()
        check(data_pool(mounted=False, mountpoint=None, health="unmounted"))

        assert host.calls == []

    def test_without_a_generation_the_volumes_are_listed_at_every_pass(self, host, clock):
        (host.sysfs.root / DATA_UUID / "generation").unlink()
        check()
        host.calls.clear()
        check()

        assert ["btrfs", "subvolume", "list", "/mnt/pools/data"] in host_commands(host)

    def test_a_pool_mounted_at_another_subvolume_has_no_volumes(self, host, clock, alerts):
        use(host, 91)
        host.subvols.mounted_subvolume["/mnt/pools/data"] = host.photos
        check()

        assert volume_problems() == [] and alerts == []


# --- step 3: a graph shows the use over time ------------------------------------------------------


def points(subvol_id: int) -> list[tuple[int, int, int | None]]:
    return volume_usage.history_of(DATA_UUID, subvol_id)


class TestTheHistory:
    def test_one_point_per_volume_per_hour(self, host, clock):
        check()
        clock[0] = T0 + 300
        host.sysfs.group(host.documents, 6 * GIB, 10 * GIB)
        check()  # the same hour: no point
        clock[0] = T0 + HOUR + 10
        check()

        assert points(host.documents) == [(T0, 5 * GIB, 10 * GIB), (T0 + HOUR + 10, 6 * GIB, 10 * GIB)]
        assert points(host.photos) == [(T0, 3 * GIB, None), (T0 + HOUR + 10, 3 * GIB, None)]

    def test_points_older_than_30_days_go(self, host, clock):
        for hour in range(30 * 24 + 5):
            clock[0] = T0 + hour * HOUR
            check()

        kept = points(host.documents)
        assert len(kept) == 30 * 24
        assert kept[0][0] == T0 + 5 * HOUR
        assert clock[0] - kept[0][0] < 30 * 24 * HOUR

    def test_the_history_of_a_pool_no_longer_mounted_stays_until_it_is_30_days_old(self, host, clock):
        check()
        clock[0] = T0 + 10 * 24 * HOUR
        check(data_pool(mounted=False, mountpoint=None, health="unmounted"))
        assert points(host.documents) == [(T0, 5 * GIB, 10 * GIB)]

        clock[0] = T0 + 30 * 24 * HOUR
        check(data_pool(mounted=False, mountpoint=None, health="unmounted"))
        assert points(host.documents) == []

    def test_the_history_follows_a_rename_and_goes_with_a_deleted_volume(self, host, clock):
        check()
        host.subvols.subvolumes[DATA_UUID][host.documents] = (5, "papers")
        write(host)
        clock[0] = T0 + HOUR
        check()
        assert len(points(host.documents)) == 2

        del host.subvols.subvolumes[DATA_UUID][host.documents]
        write(host)
        check()
        assert points(host.documents) == []
        assert len(points(host.photos)) == 2

    def test_the_endpoint_answers_the_points_and_the_current_reading_last(self, host, clock):
        check()
        clock[0] = T0 + HOUR
        check()
        host.sysfs.group(host.documents, 7 * GIB, 10 * GIB)
        clock[0] = T0 + HOUR + 900

        with admin_client() as api:
            response = api.get(f"{API}/data/volumes/documents/usage")

        assert response.status_code == 200, response.text
        assert response.json() == {
            "name": "documents", "pool": "data", "used": 7 * GIB, "quota": 10 * GIB, "usage": "counted",
            "points": [
                {"time": "2026-11-17T10:00:00+00:00", "used": 5 * GIB, "quota": 10 * GIB},
                {"time": "2026-11-17T11:00:00+00:00", "used": 5 * GIB, "quota": 10 * GIB},
                {"time": "2026-11-17T11:15:00+00:00", "used": 7 * GIB, "quota": 10 * GIB},
            ],
        }

    def test_the_endpoint_of_a_volume_without_points_has_the_current_reading_alone(self, host, clock):
        with admin_client() as api:
            answer = api.get(f"{API}/data/volumes/photos/usage").json()

        assert answer["points"] == [{"time": "2026-11-17T10:00:00+00:00", "used": 3 * GIB, "quota": None}]

    def test_the_endpoint_with_quotas_off_has_no_points(self, host, clock):
        host.sysfs.quotas_off()

        with admin_client() as api:
            answer = api.get(f"{API}/data/volumes/photos/usage").json()

        assert (answer["usage"], answer["used"], answer["points"]) == ("quotas_off", None, [])

    def test_the_endpoint_refuses_an_unknown_volume_and_a_pool_that_is_not_mounted(self, host, clock):
        host.add_disk("sdb", serial="WD-2")
        host.add_btrfs("media", "4b2d0a7f-2345-4d4c-8c1b-9a4d3b2c5d6e", ["/dev/sdb"])
        host.dirs.add("/mnt/pools/media")

        with admin_client() as api:
            assert api.get(f"{API}/data/volumes/music/usage").status_code == 404
            assert api.get(f"{API}/media/volumes/music/usage").status_code == 409
            assert api.get(f"{API}/data/volumes/..%2Fx/usage").status_code in (400, 404, 422)

    def test_the_endpoint_is_for_administrators(self, host, clock):
        # The storage router is administrator-only (ADR-0030): without a token, 401.
        from fastapi.testclient import TestClient
        from src.main import app

        assert TestClient(app).get(f"{API}/data/volumes/documents/usage").status_code == 401


class TestTheHistoryFile:
    """In RAM, written to /data only at shutdown (ADR-0214, ADR-0269), read at the first use after a start."""

    def test_passes_write_nothing_and_a_stop_writes_the_history(self, host, clock):
        path = Path(volume_usage.HISTORY_FILE)
        check()
        clock[0] = T0 + HOUR
        check()
        assert not path.exists()

        volume_usage.save_unsaved()
        saved = json.loads(path.read_text())
        assert [entry["points"] for entry in saved["series"] if entry["id"] == host.documents] == [
            [[T0, 5 * GIB, 10 * GIB], [T0 + HOUR, 5 * GIB, 10 * GIB]]]

    def test_a_stop_without_anything_new_writes_nothing(self, host, clock):
        check()
        check()  # the list is settled
        volume_usage.save_unsaved()
        written = os.stat(volume_usage.HISTORY_FILE)

        clock[0] = T0 + 600  # the same hour, and the same generation: nothing new
        check()
        volume_usage.save_unsaved()
        assert os.stat(volume_usage.HISTORY_FILE).st_ino == written.st_ino
        assert os.stat(volume_usage.HISTORY_FILE).st_mtime_ns == written.st_mtime_ns

    def test_a_start_reads_the_history_and_lists_nothing_while_the_generation_is_the_same(
            self, host, clock, monkeypatch):
        check()
        check()  # the list is settled
        volume_usage.save_unsaved()
        monkeypatch.setattr(volume_usage, "_state", volume_usage._State())  # a new start
        host.calls.clear()

        clock[0] = T0 + HOUR
        check()

        assert host.calls == []
        assert points(host.documents) == [(T0, 5 * GIB, 10 * GIB), (T0 + HOUR, 5 * GIB, 10 * GIB)]

    def test_a_file_that_cannot_be_opened_is_not_written_over_and_is_read_once_it_can_be(
            self, host, clock, caplog, monkeypatch):
        folder = Path(volume_usage.HISTORY_FILE).parent / "unreadable"
        folder.mkdir()  # a folder in place of the file: opening it fails
        monkeypatch.setattr(volume_usage, "HISTORY_FILE", str(folder))
        check()
        clock[0] = T0 + HOUR
        check()
        volume_usage.save_unsaved()
        assert folder.is_dir()
        assert caplog.text.count("Could not read the history of the volumes' use") == 1

        file = folder.parent / "readable.json"
        file.write_text(json.dumps({"listed": {}, "series": [
            {"uuid": DATA_UUID, "id": host.photos, "pool": "data", "volume": "photos", "points": [[T0 - HOUR, GIB, None]]}]}))
        monkeypatch.setattr(volume_usage, "HISTORY_FILE", str(file))
        clock[0] = T0 + 2 * HOUR
        check()
        assert points(host.photos) == [(T0 - HOUR, GIB, None), (T0 + 2 * HOUR, 3 * GIB, None)]

    def test_a_file_that_cannot_be_read_is_set_aside_and_the_history_starts_anew(self, host, clock, caplog):
        Path(volume_usage.HISTORY_FILE).write_text("{not json")

        check()

        assert Path(volume_usage.HISTORY_FILE + ".bad").read_text() == "{not json"
        assert points(host.documents) == [(T0, 5 * GIB, 10 * GIB)]

    def test_the_lifespan_writes_the_history_at_shutdown(self, host, clock, monkeypatch):
        monkeypatch.setattr(health_router, "save_unsaved_smart_history", lambda: None)
        check()

        asyncio.run(health_router.stop_health_monitors())

        assert Path(volume_usage.HISTORY_FILE).exists()
