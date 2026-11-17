"""How much of its quota each volume uses, alerts at 80, 90 and 95 percent, and its history
(storage.volume-quota-usage, ADR-0387).

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
import logging
import math
import os
import subprocess
from pathlib import Path

import pytest

from src.health import problems
from src.storage import pools, qgroups, volume_usage
from src.storage.schemas import PoolInfo

from src.host import HostResult

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

    def remove_group(self, subvol_id: int, uuid: str = DATA_UUID) -> None:
        folder = self.root / uuid / "qgroups" / f"0_{subvol_id}"
        for path in folder.iterdir():
            path.unlink()
        folder.rmdir()

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
    run_btrfs = fake.subvols._run

    def btrfs(cmd):
        # Linux 6.1.y: the destroy starts a transaction, refused or not (the generation moves), and
        # refuses a group that counts bytes or that ``busy`` names; a destroyed group leaves sysfs.
        if cmd[1:3] != ["qgroup", "destroy"]:
            return run_btrfs(cmd)
        subvol_id = int(cmd[3][2:])
        write(fake)
        group = sysfs.root / DATA_UUID / "qgroups" / f"0_{subvol_id}"
        if not group.is_dir():
            return HostResult(1, "", "ERROR: unable to destroy quota group: No such file or directory")
        counts = [int(file.read_text()) for file in group.iterdir()
                  if file.name in ("referenced", "exclusive") or file.name.startswith("rsv_")]
        if subvol_id in fake.busy or any(counts):
            return HostResult(1, "", BUSY)
        result = run_btrfs(cmd)
        if result.ok:
            sysfs.remove_group(subvol_id)
        return result

    fake.subvols._run = btrfs
    fake.busy = set()
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

    def test_a_filesystem_whose_uuid_is_no_uuid_has_volumes_of_unknown_use(self, host):
        # The kernel names its sysfs folder by the UUID: anything else is no folder of it.
        host.add_disk("sdb", serial="WD-2")
        host.add_btrfs("odd", "not-a-uuid", ["/dev/sdb"])
        host.mount_pool("odd")
        host.subvols.add("not-a-uuid", "docs")

        with admin_client() as api:
            response = api.get(f"{API}/odd/volumes")

        assert response.status_code == 200, response.text
        assert [(v["name"], v["used"], v["usage"]) for v in response.json()] == [("docs", None, "unknown")]
        assert qgroups.quotas_on("not-a-uuid") is False and qgroups.generation("not-a-uuid") is None

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

    def test_counts_that_need_a_rescan_keep_the_problem_and_add_no_point(self, host, clock, alerts):
        use(host, 92)
        check()
        # btrfs quota rescan: the kernel counts again from 0, with inconsistent 1 until it ends.
        clock[0] = T0 + HOUR
        host.sysfs.quotas(inconsistent=True)
        host.sysfs.group(host.documents, GIB, 10 * GIB)
        write(host)
        check()
        assert [p.kind for p in volume_problems()] == ["volume_quota_90"]

        # The rescan ended: the same use as before.
        clock[0] = T0 + HOUR + 300
        host.sysfs.quotas(inconsistent=False)
        use(host, 92)
        check()

        assert len(alerts) == 1
        used = math.ceil(10 * GIB * 92 / 100)
        assert points(host.documents) == [(T0, used, 10 * GIB), (T0 + HOUR + 300, used, 10 * GIB)]

    def test_a_quota_of_0_bytes_is_full(self, host, clock, alerts):
        # btrfs qgroup limit 0 by hand: no write fits.
        host.sysfs.group(host.documents, 16384, 0)
        check()

        [problem] = volume_problems()
        assert (problem.kind, problem.severity) == ("volume_quota_95", "error")
        assert problem.title == "Volume 'documents' of pool 'data' has used 100% of its quota"

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

    def test_the_volumes_are_checked_also_when_the_raid_alerts_fail(self, host, clock, alerts, monkeypatch):
        async def stop_after_one_round(_seconds):
            health_router._raid_monitor_running = False

        def status_not_written(status):
            raise OSError("No space left on device")

        monkeypatch.setattr(health_router, "load_previous_raid_status", lambda: {})
        monkeypatch.setattr(health_router, "save_raid_status", status_not_written)
        monkeypatch.setattr(health_router.asyncio, "sleep", stop_after_one_round)
        monkeypatch.setattr(health_router, "_raid_monitor_running", True)
        use(host, 91)

        asyncio.run(health_router.raid_monitor_loop())

        assert ("warning", "Volume 'documents' of pool 'data' has used 91% of its quota") in alerts


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
        host.sysfs.group(host.documents, math.ceil(9.1 * GIB), 10 * GIB)  # no transaction: the generation stays
        check()
        check()
        assert host.calls == []
        # Read all the same, from sysfs.
        assert [p.kind for p in volume_problems()] == ["volume_quota_90"]

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


BUSY = "ERROR: unable to destroy quota group: Device or resource busy"
DESTROY = ("timeout", "--signal=KILL", "60", "btrfs", "qgroup", "destroy")


def destroys(host) -> list[list[str]]:
    return [cmd for cmd in host_commands(host) if cmd[:6] == list(DESTROY)]


def delete_subvolume(host, subvol_id: int, *, dropped: bool = True) -> None:
    """``btrfs subvolume delete``: gone from the list, its quota group left in sysfs with its limit
    (Linux 6.1); once btrfs has ``dropped`` its data and committed, the group counts nothing."""
    del host.subvols.subvolumes[DATA_UUID][subvol_id]
    if dropped:
        folder = host.sysfs.root / DATA_UUID / "qgroups" / f"0_{subvol_id}"
        for name in ("referenced", "exclusive"):
            (folder / name).write_text("0\n")
    write(host)


def settled(host) -> None:
    """Passes until the lists settle, then one more pass that must run no command."""
    for _ in range(6):
        check()
    before = len(host.calls)
    check()
    assert len(host.calls) == before


class TestStaleQuotaGroups:
    """Linux 6.1 keeps the quota group 0/<id> of a deleted subvolume, with its limit, and refuses to
    destroy it as busy until btrfs has dropped the subvolume's data and committed. A list of the
    pool destroys a group that it and the list before it found without a subvolume, once its counts
    read 0 in sysfs; every destroy starts a transaction, which moves the generation (ADR-0387)."""

    def test_the_group_of_a_deleted_volume_is_destroyed_at_the_second_list(self, host, clock, caplog):
        caplog.set_level(logging.INFO, logger=volume_usage.__name__)
        check()
        delete_subvolume(host, host.documents)
        check()
        assert destroys(host) == []  # one list: the subvolume may only have been missed

        check()  # the list once more: the generation stayed the same
        assert destroys(host) == [[*DESTROY, f"0/{host.documents}", "/mnt/pools/data"]]
        assert f"Destroyed the quota group 0/{host.documents} of pool data" in caplog.text
        assert qgroups.subvolume_group_ids(DATA_UUID) == {host.photos}
        # The destroy moved the generation: two more lists, then sysfs alone.
        settled(host)
        assert len(destroys(host)) == 1

    def test_a_group_that_still_counts_bytes_gets_no_command_until_it_reads_0(self, host, clock):
        check()
        delete_subvolume(host, host.documents, dropped=False)
        settled(host)  # no destroy, so no transaction of UNOS's: the disks may sleep
        assert destroys(host) == []

        folder = host.sysfs.root / DATA_UUID / "qgroups" / f"0_{host.documents}"
        for name in ("referenced", "exclusive"):
            (folder / name).write_text("0\n")
        write(host)  # the commit of the drop
        check()
        check()

        assert len(destroys(host)) == 1
        assert qgroups.subvolume_group_ids(DATA_UUID) == {host.photos}

    def test_a_group_with_a_reservation_gets_no_command(self, host, clock):
        check()
        delete_subvolume(host, host.documents)
        host.sysfs._write(f"{DATA_UUID}/qgroups/0_{host.documents}/rsv_meta_pertrans", 16384)
        settled(host)

        assert destroys(host) == []

    def test_counts_that_need_a_rescan_are_logged_once_with_the_rescan(self, host, clock, caplog):
        host.sysfs.quotas(inconsistent=True)
        check()
        delete_subvolume(host, host.documents, dropped=False)
        for _ in range(3):
            check()
            write(host)
        settled(host)

        assert destroys(host) == []
        assert caplog.text.count(f"The quota group 0/{host.documents} of pool data") == 1
        assert "'btrfs quota rescan /mnt/pools/data' on the host" in caplog.text

    def test_a_group_refused_without_counting_bytes_is_given_up_after_3_attempts(self, host, clock, caplog):
        check()
        delete_subvolume(host, host.documents)
        host.busy.add(host.documents)
        settled(host)  # each refused destroy moved the generation, and brought two more lists

        assert len(destroys(host)) == volume_usage.DESTROY_ATTEMPTS == 3
        assert caplog.text.count(f"Gave up destroying the quota group 0/{host.documents} of pool data") == 1
        assert f"'btrfs qgroup destroy 0/{host.documents} /mnt/pools/data' on the host" in caplog.text

    @pytest.mark.parametrize("stderr", ["ERROR: unable to destroy quota group: Invalid argument"])
    def test_another_failure_is_given_up_after_3_attempts_too(self, host, clock, caplog, stderr):
        check()
        host.fail[DESTROY] = (1, stderr)
        delete_subvolume(host, host.documents)
        for _ in range(8):
            check()
            write(host)

        assert len(destroys(host)) == 3
        assert caplog.text.count("Gave up destroying the quota group") == 1
        assert stderr in caplog.text

    def test_quotas_turned_off_meanwhile_destroy_nothing_and_log_nothing(self, host, clock, caplog):
        caplog.set_level(logging.INFO, logger=volume_usage.__name__)
        check()
        host.fail[DESTROY] = (1, "ERROR: unable to destroy quota group: Transport endpoint is not connected")
        delete_subvolume(host, host.documents)
        for _ in range(8):
            check()
            write(host)

        assert len(destroys(host)) == 3
        assert "quota group" not in caplog.text

    def test_a_destroy_that_does_not_answer_ends_the_pass_s_destroys(self, host, clock, caplog):
        check()
        host.fail[DESTROY] = (-9, "")  # timeout(1)'s SIGKILL
        delete_subvolume(host, host.documents)
        delete_subvolume(host, host.photos)
        check()
        check()

        assert len(destroys(host)) == 1

    def test_a_destroy_that_raises_is_a_failure_of_its_own(self, host, clock, caplog, monkeypatch):
        def broken(target, subvol_id):
            raise OSError("nsenter: cannot open /proc/1/ns/mnt")

        monkeypatch.setattr(pools, "destroy_quota_group", broken)
        use(host, 91, host.photos)
        check()
        delete_subvolume(host, host.documents)
        for _ in range(5):
            check()
            write(host)

        assert "Gave up destroying the quota group" in caplog.text and "cannot open" in caplog.text
        assert [p.subject for p in volume_problems()] == ["data/photos"]

    def test_the_groups_of_listed_subvolumes_snapshots_included_of_the_top_level_and_of_level_1_stay(
        self, host, clock
    ):
        snapshots = host.subvols.add(DATA_UUID, ".snapshots")
        snapshot = host.subvols.add(DATA_UUID, ".snapshots/documents_1", parent=snapshots,
                                    snapshot_of=host.documents, read_only=True)
        host.sysfs.group(snapshots, 0)
        host.sysfs.group(snapshot, 0)
        host.sysfs.group(5, 0)
        host.sysfs._write(f"{DATA_UUID}/qgroups/1_1/referenced", 0)
        write(host)
        settled(host)

        assert destroys(host) == []

    def test_a_subvolume_that_one_list_missed_keeps_its_group(self, host, clock, monkeypatch):
        # Linux 6.1 makes a new subvolume's group, counting 0, before its root item: a list run
        # meanwhile misses it.
        music = host.subvols.add(DATA_UUID, "music")
        host.sysfs.group(music, 0)
        listed = pools.mounted_pool_volumes
        calls = []

        def first_misses_music(name, target):
            found, unresolved, ids = listed(name, target)
            calls.append(name)
            return found, unresolved, ids - {music} if len(calls) == 1 else ids

        monkeypatch.setattr(pools, "mounted_pool_volumes", first_misses_music)
        settled(host)

        assert destroys(host) == []

    def test_a_subvolume_made_during_the_list_keeps_its_group(self, host, clock, monkeypatch):
        # sysfs is read before the list: a group read after it could be of a subvolume that the
        # list missed.
        made = []
        listed = pools.mounted_pool_volumes

        def list_then_make(name, target):
            found = listed(name, target)
            if not made:
                made.append(host.subvols.add(DATA_UUID, "music"))
                host.sysfs.group(made[0], 0)
            return found

        monkeypatch.setattr(pools, "mounted_pool_volumes", list_then_make)
        check()
        assert volume_usage._state.stale[DATA_UUID][1] == set()  # not even a candidate
        settled(host)

        assert destroys(host) == []

    def test_an_id_given_out_again_after_a_remount_is_not_taken_for_the_stale_one(
        self, host, clock, monkeypatch
    ):
        # A list before the remount found 0/<id> stale; after the remount btrfs gives the id to a new
        # subvolume, whose group (counting 0) the first list after the remount misses.
        check()
        delete_subvolume(host, host.documents)
        check()  # a candidate
        assert volume_usage._state.stale[DATA_UUID][1] == {host.documents}
        tree = qgroups.quota_tree_id(DATA_UUID)
        monkeypatch.setattr(qgroups, "quota_tree_id", lambda fsid: tree + 1)  # the folder made anew
        host.subvols.subvolumes[DATA_UUID][host.documents] = (5, "music")
        listed = pools.mounted_pool_volumes
        calls = []

        def first_misses_music(name, target):
            found, unresolved, ids = listed(name, target)
            calls.append(name)
            return found, unresolved, ids - {host.documents} if len(calls) == 1 else ids

        monkeypatch.setattr(pools, "mounted_pool_volumes", first_misses_music)
        write(host)
        settled(host)

        assert destroys(host) == []

    def test_a_pass_with_the_pool_unmounted_starts_the_two_lists_again(self, host, clock):
        check()
        delete_subvolume(host, host.documents)
        check()  # a candidate
        check(data_pool(mounted=False, mountpoint=None, health="unmounted"))
        write(host)  # mounted again
        check()
        assert destroys(host) == []

        check()
        assert len(destroys(host)) == 1

    def test_a_start_needs_two_lists(self, host, clock, monkeypatch):
        check()
        delete_subvolume(host, host.documents)
        check()  # a candidate
        monkeypatch.setattr(volume_usage, "_state", volume_usage._State())  # a new start
        write(host)
        check()
        assert destroys(host) == []

        check()
        assert len(destroys(host)) == 1

    def test_the_groups_of_a_pool_that_is_gone_are_forgotten(self, host, clock):
        check()
        delete_subvolume(host, host.documents)
        check()
        check(data_pool(name="other", uuid="0b1e7a65-1b0f-4ec6-9d6c-6e4c3f1a2b3c"))

        assert DATA_UUID not in volume_usage._state.stale

    def test_a_group_whose_files_cannot_be_read_gets_no_command(self, host, clock):
        check()
        delete_subvolume(host, host.documents)
        (host.sysfs.root / DATA_UUID / "qgroups" / f"0_{host.documents}" / "exclusive").unlink()
        settled(host)

        assert destroys(host) == []

    def test_a_folder_that_cannot_be_read_once_does_not_start_the_attempts_again(
        self, host, clock, caplog, monkeypatch
    ):
        check()
        delete_subvolume(host, host.documents)
        host.busy.add(host.documents)
        settled(host)
        assert len(destroys(host)) == 3
        read = qgroups.subvolume_group_ids
        monkeypatch.setattr(qgroups, "subvolume_group_ids", lambda fsid: None)
        write(host)
        check()
        monkeypatch.setattr(qgroups, "subvolume_group_ids", read)
        write(host)
        settled(host)

        assert len(destroys(host)) == 3
        assert caplog.text.count("Gave up destroying the quota group") == 1

    def test_a_group_that_has_its_subvolume_again_may_be_tried_again_later(self, host, clock, caplog):
        check()
        delete_subvolume(host, host.documents)
        host.busy.add(host.documents)
        settled(host)
        assert len(destroys(host)) == 3

        host.subvols.subvolumes[DATA_UUID][host.documents] = (5, "documents")  # listed again
        write(host)
        check()
        delete_subvolume(host, host.documents)
        settled(host)

        assert len(destroys(host)) == 6

    def test_a_hung_destroy_is_one_per_pass_and_the_others_come_later(self, host, clock):
        check()
        host.fail[DESTROY] = (-9, "")
        delete_subvolume(host, host.documents)
        delete_subvolume(host, host.photos)
        counts = []
        for _ in range(10):
            before = len(destroys(host))
            check()
            counts.append(len(destroys(host)) - before)
            write(host)

        assert max(counts) == 1
        assert [cmd[6] for cmd in destroys(host)] == [f"0/{host.documents}"] * 3 + [f"0/{host.photos}"] * 3


class TestDestroyQuotaGroup:
    """``pools.destroy_quota_group``: what btrfs answered, under the host's timeout (ADR-0258)."""

    @pytest.mark.parametrize(("returncode", "stderr", "outcome"), [
        (0, "", "destroyed"),
        (1, "ERROR: unable to destroy quota group: No such file or directory", "absent"),
        (1, "ERROR: unable to destroy quota group: Transport endpoint is not connected", "absent"),
        (1, "ERROR: unable to destroy quota group: quota not enabled", "absent"),
        (1, BUSY, "busy"),
        (1, "ERROR: unable to destroy quota group: Invalid argument", "failed"),
        (124, "", "hung"),
        (137, "", "hung"),
        (-9, "", "hung"),
    ])
    def test_outcomes(self, monkeypatch, returncode, stderr, outcome):
        sent = []

        def run_on_host(cmd, **kwargs):
            sent.append((cmd, kwargs.get("timeout")))
            return HostResult(returncode, "", stderr)

        monkeypatch.setattr("src.host.run_on_host", run_on_host)

        assert pools.destroy_quota_group("/mnt/pools/data", 300)[0] == outcome
        assert sent == [([*DESTROY, "0/300", "/mnt/pools/data"], 65)]

    def test_no_answer_within_the_supervisor_s_timeout_is_hung(self, monkeypatch):
        def run_on_host(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))

        monkeypatch.setattr("src.host.run_on_host", run_on_host)

        assert pools.destroy_quota_group("/mnt/pools/data", 300) == (
            "hung", "btrfs did not answer within 60 seconds")

    def test_a_list_that_fails_destroys_nothing(self, host, clock):
        delete_subvolume(host, host.documents)
        host.fail[("btrfs", "subvolume", "list")] = (1, "ERROR: can't perform the search")
        for _ in range(3):
            check()
            write(host)

        assert destroys(host) == []


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

    def test_a_pass_with_the_clock_far_ahead_does_not_stop_the_history(self, host, clock):
        check()
        clock[0] = T0 + 365 * 86400  # a wrong clock for one pass (NTP, an RTC)
        check()
        for hours in range(1, 6):  # the clock is right again
            clock[0] = T0 + hours * HOUR
            check()

        # The points older than 30 days before the wrong time went with that pass; the wrong point
        # goes as soon as the clock is right, and the hours go on.
        assert [t for t, _, _ in points(host.documents)] == [T0 + hours * HOUR for hours in range(1, 6)]

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
            {"uuid": DATA_UUID, "id": host.photos, "pool": "data", "volume": "photos", "points": [[T0 - HOUR, GIB, None]],
             "stage": 0}]}))
        monkeypatch.setattr(volume_usage, "HISTORY_FILE", str(file))
        clock[0] = T0 + 2 * HOUR
        check()
        assert points(host.photos) == [(T0 - HOUR, GIB, None), (T0 + 2 * HOUR, 3 * GIB, None)]

    def test_a_start_keeps_the_level_a_volume_reached(self, host, clock, alerts, monkeypatch):
        use(host, 92)
        check()
        check()
        host.sysfs.group(host.documents, math.ceil(8.7 * GIB), 10 * GIB)  # still at 90: it is left below 85
        check()
        assert [p.kind for p in volume_problems()] == ["volume_quota_90"]
        volume_usage.save_unsaved()
        # A restart: RAM is empty, the problems too (ADR-0332), and the file is read again.
        monkeypatch.setattr(volume_usage, "_state", volume_usage._State())
        monkeypatch.setattr(problems, "_by_source", {})

        check()

        assert [p.kind for p in volume_problems()] == ["volume_quota_90"]
        # Alerted once more after the restart, as every problem is, at the level it had reached.
        assert [title for _, title in alerts] == [
            "Volume 'documents' of pool 'data' has used 92% of its quota",
            "Volume 'documents' of pool 'data' has used 87% of its quota",
        ]

    def test_the_list_of_a_pool_that_is_gone_is_forgotten(self, host, clock):
        check()
        check()
        check(data_pool(name="spare", uuid="4b2d0a7f-2345-4d4c-8c1b-9a4d3b2c5d6e", mounted=False,
                        mountpoint=None, health="unmounted"))
        assert DATA_UUID not in volume_usage._state.listed
        host.calls.clear()

        check()  # the pool is back: its volumes are listed again

        assert ["btrfs", "subvolume", "list", "/mnt/pools/data"] in host_commands(host)

    def test_points_of_the_file_ahead_of_the_clock_are_dropped(self, host, clock):
        Path(volume_usage.HISTORY_FILE).write_text(json.dumps({"listed": {}, "series": [
            {"uuid": DATA_UUID, "id": host.photos, "pool": "data", "volume": "photos", "stage": 0,
             "points": [[T0 - HOUR, GIB, None], [T0 + 30 * 86400, GIB, None]]}]}))

        assert points(host.photos) == [(T0 - HOUR, GIB, None)]

    @pytest.mark.parametrize("change", [
        {"points": [[10**15, GIB, None]]},  # a time that cannot be shown
        {"points": [[T0, -1, None]]},
        {"volume": "doc'uments"},
        {"pool": "../data"},
        {"uuid": "not-a-uuid"},
        {"id": 0},
        {"stage": 85},
    ])
    def test_a_file_with_what_the_api_would_refuse_is_set_aside(self, host, clock, change):
        series = {"uuid": DATA_UUID, "id": host.photos, "pool": "data", "volume": "photos", "stage": 0,
                  "points": [[T0, GIB, None]], **change}
        Path(volume_usage.HISTORY_FILE).write_text(json.dumps({"listed": {}, "series": [series]}))

        with admin_client() as api:
            response = api.get(f"{API}/data/volumes/photos/usage")

        assert response.status_code == 200, response.text
        assert response.json()["points"] == [{"time": "2026-11-17T10:00:00+00:00", "used": 3 * GIB, "quota": None}]
        assert Path(volume_usage.HISTORY_FILE + ".bad").exists()

    def test_a_file_whose_lists_hold_a_name_the_api_would_refuse_is_set_aside(self, host, clock):
        Path(volume_usage.HISTORY_FILE).write_text(json.dumps({"series": [], "listed": {DATA_UUID: {
            "pool": "data", "generation": 100, "volumes": {"doc uments": host.documents}, "settled": True,
            "failure": None}}}))

        check()

        assert Path(volume_usage.HISTORY_FILE + ".bad").exists()
        assert ["btrfs", "subvolume", "list", "/mnt/pools/data"] in host_commands(host)

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
