"""Idle timeouts of the data disks, and reads that leave a sleeping disk asleep
(storage.disk-spindown, ADR-0378).

An administrator sets a disk's idle timeout on the Storage page (PUT /storage/disks/{name}/spindown):
Off or one of SPINDOWN_MINUTES, which hdparm -S expresses. UNOS applies it at once with hdparm -S on
the host and keeps it in its udev rule, which runs hdparm -S again whenever the disk appears (at boot
and when it is plugged in again), matched by its serial. The system disk, a boot or virtual device,
an NVMe disk and a disk without a serial that a rule can match are refused with the reason; a host
without hdparm says so. GET /storage/disks reports each disk's power state with smartctl -n standby,
which leaves a sleeping disk alone, and the SMART reads of Storage and Disk Health do the same.

Nothing UNOS does periodically wakes a sleeping disk: the SMART monitor, the RAID monitor, the
monitoring endpoints, the trash expiry and the live statistics are run here with every disk asleep, on
the FakeHost of test_pool_persistence, which wakes a disk for every command that would read it or spin
it up. Nor do they keep an idle disk awake: any command restarts its standby timer, so the SMART monitor
leaves alone a disk with a timeout that was not used since its last read, and the trash expiry asks a
pool's disks whether they sleep only when the pool's pass is due.
"""
from __future__ import annotations

import asyncio
import importlib
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src import host as host_module
from src.audit import router as audit_router
from src.storage import disks, pools, spindown

from .audit_helpers import audit_entries
from .test_files_security import _MountTable
from .test_pool_persistence import (
    DATA_UUID,
    MEDIA_UUID,
    admin_client,
    entry,
    install_fake_host,
    marker,
    wait_for_app_starts,
)

# The packages re-export their APIRouter as ``router``; import the modules.
files_router = importlib.import_module("src.files.router")
health_router = importlib.import_module("src.health.router")
events = importlib.import_module("src.websocket.events")

DISKS = "/api/v1/storage/disks"
RULE = (
    'ACTION=="add", SUBSYSTEM=="block", ENV{{DEVTYPE}}=="disk", ENV{{ID_SERIAL_SHORT}}=="{serial}", '
    'RUN+="/sbin/hdparm -S {value} $devnode"'
)


@pytest.fixture
def host(monkeypatch, tmp_path):
    """The dev device (a BPI-W3, ADR-0376): the system on the microSD card mmcblk1, swap on zram0;
    the pool data on the SATA disk sda, mounted with UNOS's fstab entry; and, as a NAS has them, a
    second disk sdb with the pool spare, which is not mounted, a blank disk sdc and an NVMe drive."""
    fake = install_fake_host(monkeypatch)
    monkeypatch.setattr(audit_router, "AUDIT_FILE", str(tmp_path / "audit.jsonl"))
    fake.add_disk("sda", size=640_135_028_736, model="WDC WD6400AACS-00G8B0", serial="WD-W3")
    fake.add_btrfs("data", DATA_UUID, ["/dev/sda"])
    fake.files["/etc/fstab"] += marker("data") + entry("data", DATA_UUID)
    fake.dirs.add("/mnt/pools/data")
    fake.mount_pool("data")
    fake.add_disk("sdb", model="WDC WD40EFRX", serial="WD-SPARE")
    fake.add_btrfs("spare", MEDIA_UUID, ["/dev/sdb"])
    fake.dirs.add("/mnt/pools/spare")
    fake.add_disk("sdc", model="ST2000DM008", serial="ZFL0BLANK")
    fake.add_disk("nvme0n1", rota=False, size=512_110_190_592, model="Samsung SSD 980", serial="S64ANS0T")
    yield fake
    wait_for_app_starts()


def rules(fake) -> list[str]:
    """The lines of UNOS's udev rule that are not comments; [] without the file."""
    text = fake.files.get(spindown.RULES_PATH)
    if text is None:
        return []
    assert text.startswith("# "), text
    return [line for line in text.splitlines() if line and not line.startswith("#")]


def listed(api) -> dict[str, dict]:
    response = api.get(DISKS)
    assert response.status_code == 200, response.text
    return {disk["name"]: disk for disk in response.json()}


def entries(path: Path) -> list[tuple]:
    return [(e["action"], e["resource_type"], e["resource_id"], e["details"], e["success"])
            for e in audit_entries(path)]


# --- the offered values -----------------------------------------------------------------------


def test_the_offered_timeouts_are_values_that_hdparm_s_expresses():
    # 1 to 240: multiples of 5 seconds (up to 20 minutes); 241 to 251: multiples of 30 minutes.
    assert spindown.SPINDOWN_MINUTES == (0, 5, 10, 20, 30, 60, 120, 180, 240, 300)
    assert {m: spindown.hdparm_value(m) for m in spindown.SPINDOWN_MINUTES} == {
        0: 0, 5: 60, 10: 120, 20: 240, 30: 241, 60: 242, 120: 244, 180: 246, 240: 248, 300: 250}
    assert all(spindown.minutes_of(spindown.hdparm_value(m)) == m for m in spindown.SPINDOWN_MINUTES)
    assert [spindown.minutes_of(v) for v in (1, 12, 252, 253, 254, 255, 256)] == [None, 1, None, None, None, None, None]


def test_a_timeout_that_is_not_offered_is_refused_before_any_host_command(host):
    with admin_client() as api:
        for body in ({"minutes": 7}, {"minutes": -5}, {"minutes": 21}, {}, {"minutes": "5"}):
            response = api.put(f"{DISKS}/sda/spindown", json=body)
            assert response.status_code == 422, (body, response.text)
    assert host.changes == [] and spindown.RULES_PATH not in host.files


# --- setting a timeout --------------------------------------------------------------------------


def test_a_timeout_is_applied_with_hdparm_and_kept_in_a_udev_rule_that_matches_the_serial(host, tmp_path):
    with admin_client() as api:
        response = api.put(f"{DISKS}/sda/spindown", json={"minutes": 5})
        assert response.status_code == 200, response.text
        assert response.json() == {"name": "sda", "minutes": 5}
        assert listed(api)["sda"]["spindown"] == 5
    assert host.standby_timers == {"/dev/sda": 60}
    # Applied first, then kept, then udev reads its rules again.
    applied = host.calls.index(["timeout", "--kill-after=5", "30", "hdparm", "-S", "60", "/dev/sda"])
    written = host.calls.index(next(c for c in host.calls if c[:1] == ["/bin/sh"] and spindown.RULES_PATH in c))
    reloaded = host.calls.index(["udevadm", "control", "--reload"])
    assert applied < written < reloaded
    assert rules(host) == [RULE.format(serial="WD-W3", value=60)]
    assert entries(tmp_path / "audit.jsonl") == [
        ("settings_changed", "disk", "sda", "Set the idle timeout of /dev/sda (serial WD-W3) to 5 minutes", True)]


def test_each_disk_has_its_line_and_off_removes_it_and_stops_the_timer(host, tmp_path):
    with admin_client() as api:
        assert api.put(f"{DISKS}/sda/spindown", json={"minutes": 20}).status_code == 200
        assert api.put(f"{DISKS}/sdb/spindown", json={"minutes": 60}).status_code == 200
        assert rules(host) == [RULE.format(serial="WD-W3", value=240), RULE.format(serial="WD-SPARE", value=242)]
        disks_now = listed(api)
        assert (disks_now["sda"]["spindown"], disks_now["sdb"]["spindown"], disks_now["sdc"]["spindown"]) == (20, 60, 0)

        assert api.put(f"{DISKS}/sda/spindown", json={"minutes": 0}).json() == {"name": "sda", "minutes": 0}
        assert rules(host) == [RULE.format(serial="WD-SPARE", value=242)]
        assert host.standby_timers["/dev/sda"] == 0
        assert api.put(f"{DISKS}/sdb/spindown", json={"minutes": 0}).status_code == 200
    # No line left: no file, as before the first timeout.
    assert spindown.RULES_PATH not in host.files
    assert ["rm", spindown.RULES_PATH] in host.changes
    assert [e[3] for e in entries(tmp_path / "audit.jsonl")][2:] == [
        "Turned off the idle timeout of /dev/sda (serial WD-W3)",
        "Turned off the idle timeout of /dev/sdb (serial WD-SPARE)"]


def test_the_rule_follows_the_disk_under_another_name(host):
    with admin_client() as api:
        assert api.put(f"{DISKS}/sdb/spindown", json={"minutes": 10}).status_code == 200
        # The disk comes back as sdd: its serial finds its line.
        node = host.node("sdb")
        node.update(name="sdd", kname="sdd", path="/dev/sdd")
        host.filesystems[-1]["devices"] = ["/dev/sdd"]
        assert listed(api)["sdd"]["spindown"] == 10


def test_an_unchanged_timeout_is_applied_again_but_writes_and_audits_nothing(host, tmp_path):
    with admin_client() as api:
        assert api.put(f"{DISKS}/sda/spindown", json={"minutes": 5}).status_code == 200
        writes = [c for c in host.changes if c[:1] == ["write"]]
        assert api.put(f"{DISKS}/sda/spindown", json={"minutes": 5}).status_code == 200
        assert api.put(f"{DISKS}/sdc/spindown", json={"minutes": 0}).status_code == 200  # had none
    assert [c for c in host.changes if c[:1] == ["write"]] == writes
    assert spindown.RULES_PATH in host.files and len(rules(host)) == 1
    assert len(entries(tmp_path / "audit.jsonl")) == 1
    assert [c for c in host.changes if c[0] == "hdparm"] == [
        ["hdparm", "-S", "60", "/dev/sda"], ["hdparm", "-S", "60", "/dev/sda"], ["hdparm", "-S", "0", "/dev/sdc"]]


def test_a_line_that_unos_did_not_write_is_not_kept(host):
    host.files[spindown.RULES_PATH] = "# by hand\nACTION==\"add\", RUN+=\"/bin/true\"\n" + RULE.format(
        serial="WD-SPARE", value=120) + "\n"
    with admin_client() as api:
        assert listed(api)["sdb"]["spindown"] == 10
        assert api.put(f"{DISKS}/sda/spindown", json={"minutes": 30}).status_code == 200
    assert rules(host) == [RULE.format(serial="WD-SPARE", value=120), RULE.format(serial="WD-W3", value=241)]


def test_a_line_with_a_timeout_that_is_not_offered_is_not_kept(host):
    # Edited by hand to -S 180 (15 minutes), or written by a version that offered it: no timeout of
    # the page, kept no more than a line that UNOS did not write.
    host.files[spindown.RULES_PATH] = spindown.render({"WD-SPARE": 10}) + RULE.format(serial="WD-W3", value=180) + "\n"
    with admin_client() as api:
        assert (listed(api)["sda"]["spindown"], listed(api)["sdb"]["spindown"]) == (0, 10)
        response = api.put(f"{DISKS}/sdb/spindown", json={"minutes": 20})
        assert response.status_code == 200, response.text
    assert rules(host) == [RULE.format(serial="WD-SPARE", value=240)]


def test_timeouts_that_cannot_be_read_are_the_reason_why_none_can_be_set(host):
    # The rule sets sda's 20 minutes at every boot, but cat fails (an I/O error of the SD card):
    # the page must not show Off for every disk with a button whose change then fails.
    host.files[spindown.RULES_PATH] = spindown.render({"WD-W3": 20})
    host.fail[("cat", "--", spindown.RULES_PATH)] = (1, f"cat: {spindown.RULES_PATH}: Input/output error")
    with admin_client() as api:
        found = listed(api)
    reason = (f"The idle timeouts could not be read: Could not read {spindown.RULES_PATH}: "
              f"cat: {spindown.RULES_PATH}: Input/output error")
    assert {name: found[name]["spindown_problem"] for name in ("sda", "sdb", "sdc")} == dict.fromkeys(
        ("sda", "sdb", "sdc"), reason)
    # A disk's own reason comes first.
    assert found["nvme0n1"]["spindown_problem"].startswith("/dev/nvme0n1 is an NVMe disk")


# --- what gets no timeout ----------------------------------------------------------------------


def test_the_system_disk_is_refused_with_the_reason(host):
    host.mounts = [m for m in host.mounts if m["target"] != "/"]
    host.add_disk("sde", model="Root SSD", serial="ROOT1", rota=False, pttype="gpt")
    host.add_partition("sde", "sde1", fstype="ext4", uuid="0e0e0e0e-0000-4000-8000-000000000001")
    host.add_mount("/", "/dev/sde1", "ext4", "0e0e0e0e-0000-4000-8000-000000000001")
    with admin_client() as api:
        reason = "/dev/sde is the system disk (Mounted at /): it gets no idle timeout"
        assert listed(api)["sde"]["spindown_problem"] == reason
        response = api.put(f"{DISKS}/sde/spindown", json={"minutes": 5})
        assert (response.status_code, response.json()["detail"]) == (409, reason)
        assert listed(api)["sda"]["spindown_problem"] is None
    assert [c for c in host.calls if c[:1] in (["hdparm"], ["timeout"])] == []
    assert spindown.RULES_PATH not in host.files


def test_boot_and_virtual_devices_and_what_is_no_disk_are_refused(host):
    with admin_client() as api:
        for name, detail in (
            ("mmcblk1", "mmcblk1 is a boot or virtual device: it gets no idle timeout"),
            ("zram0", "zram0 is a boot or virtual device: it gets no idle timeout"),
            ("mmcblk1p1", "mmcblk1p1 is a boot or virtual device: it gets no idle timeout"),
            ("sdz", "sdz is not a disk of this system"),
        ):
            response = api.put(f"{DISKS}/{name}/spindown", json={"minutes": 5})
            assert (response.status_code, response.json()["detail"]) == (400, detail), name
        host.add_partition("sdc", "sdc1")
        response = api.put(f"{DISKS}/sdc1/spindown", json={"minutes": 5})
        assert (response.status_code, response.json()["detail"]) == (
            400, "sdc1 is a partition: idle timeouts are set for whole disks")
        assert api.put(f"{DISKS}/null/spindown", json={"minutes": 5}).status_code == 400
    assert host.changes == []


def test_an_nvme_disk_and_a_disk_without_a_usable_serial_are_refused(host):
    host.add_disk("sdf", model="USB bridge", serial=None)
    host.add_disk("sdg", model="Odd", serial='A"B*')
    with admin_client() as api:
        found = listed(api)
        expected = {
            "nvme0n1": "/dev/nvme0n1 is an NVMe disk, which manages its own power: hdparm sets idle timeouts of "
                       "SATA disks",
            "sdf": "/dev/sdf reports no serial number that a udev rule can match: UNOS could not set its idle "
                   "timeout again when it is connected",
            "sdg": "/dev/sdg reports no serial number that a udev rule can match: UNOS could not set its idle "
                   "timeout again when it is connected",
        }
        for name, reason in expected.items():
            assert found[name]["spindown_problem"] == reason, name
            response = api.put(f"{DISKS}/{name}/spindown", json={"minutes": 5})
            assert (response.status_code, response.json()["detail"]) == (409, reason), name
        # udev's serial is the one the rule matches: when it is not lsblk's, the rule would miss.
        host.udev_serials["/dev/sdc"] = "OTHER"
        response = api.put(f"{DISKS}/sdc/spindown", json={"minutes": 5})
        assert response.status_code == 409
        assert response.json()["detail"] == (
            "udev knows /dev/sdc by the serial OTHER, not ZFL0BLANK: a udev rule could not find it again")
        # Two disks with one serial (identical USB bridges): a rule would set both.
        host.add_disk("sdh", model="WDC WD40EFRX", serial="WD-SPARE")
        response = api.put(f"{DISKS}/sdh/spindown", json={"minutes": 5})
        assert (response.status_code, response.json()["detail"]) == (
            409, "/dev/sdb reports the serial WD-SPARE too: a udev rule could not tell /dev/sdh from it")
    assert [c for c in host.changes if c[0] == "hdparm"] == []


def test_a_host_without_hdparm_says_so_and_changes_nothing(host):
    host.missing_programs.add("hdparm")
    with admin_client() as api:
        found = listed(api)
        assert found["sda"]["spindown_problem"] == spindown.NO_HDPARM
        assert found["sda"]["power_state"] == "active"  # read with smartctl, in the container
        response = api.put(f"{DISKS}/sda/spindown", json={"minutes": 5})
        assert (response.status_code, response.json()["detail"]) == (409, spindown.NO_HDPARM)
    assert spindown.NO_HDPARM == "The host has no hdparm: install the package hdparm on the host to set idle timeouts"
    assert host.changes == []


def test_when_hdparm_fails_nothing_is_kept(host, tmp_path):
    host.fail[("timeout", "--kill-after=5", "30", "hdparm")] = (5, " HDIO_DRIVE_CMD(setidle) failed: Input/output error")
    with admin_client() as api:
        response = api.put(f"{DISKS}/sda/spindown", json={"minutes": 5})
        assert response.status_code == 500
        assert response.json()["detail"] == (
            "hdparm could not set the idle timeout of /dev/sda: HDIO_DRIVE_CMD(setidle) failed: Input/output error")
        assert listed(api)["sda"]["spindown"] == 0
    assert spindown.RULES_PATH not in host.files and entries(tmp_path / "audit.jsonl") == []


def test_when_the_rule_cannot_be_written_the_answer_says_the_disk_has_it_until_it_restarts(host, tmp_path):
    host.fail[("/bin/sh", "-c")] = (1, "mv: cannot move: Read-only file system")
    with admin_client() as api:
        response = api.put(f"{DISKS}/sda/spindown", json={"minutes": 5})
    assert response.status_code == 500
    assert response.json()["detail"].startswith(
        "/dev/sda has the idle timeout of 5 minutes until it restarts: the udev rule could not be written: ")
    assert host.standby_timers == {"/dev/sda": 60}
    [(action, _, _, details, success)] = entries(tmp_path / "audit.jsonl")
    assert (action, success) == ("settings_changed", False) and details.startswith(
        "Set the idle timeout of /dev/sda (serial WD-W3) to 5 minutes until it restarts; then: ")


# --- the power state and the SMART reads -----------------------------------------------------------


def test_storage_shows_each_disk_s_power_state_without_waking_it(host):
    host.asleep.update({"/dev/sda", "/dev/sdc"})
    with admin_client() as api:
        found = listed(api)
    assert {name: d["power_state"] for name, d in found.items()} == {
        "sda": "standby", "sdb": "active", "sdc": "standby", "nvme0n1": "active"}
    assert host.woken == [] and host.asleep == {"/dev/sda", "/dev/sdc"}
    # The blank sdc is not probed for signatures while it sleeps: it may hold data.
    assert not [c for c in host.calls if c[0] == "wipefs" and c[-1] == "/dev/sdc"]
    assert found["sdc"]["has_data"] is True
    assert {tuple(c[:-1]) for c in host.container_calls} == {("smartctl", "-n", "standby", "-i", "-j")}


def test_a_disk_whose_power_state_cannot_be_read_is_unknown(host, monkeypatch):
    answer = host.run_in_container

    def failing(cmd, **kwargs):
        if cmd[-1] == "/dev/sdb":
            raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])
        return answer(cmd, **kwargs)

    monkeypatch.setattr(host_module, "run_in_container", failing)
    with admin_client() as api:
        found = listed(api)
    assert (found["sdb"]["power_state"], found["sda"]["power_state"]) == ("unknown", "active")


def test_reading_smart_data_leaves_a_sleeping_disk_asleep(host):
    host.asleep.add("/dev/sda")
    with admin_client() as api:
        storage = api.get(f"{DISKS}/sda/smart").json()
        assert (storage["device"], storage["healthy"], storage["power_state"]) == ("/dev/sda", None, "standby")
        one = api.get("/api/v1/health/disk/sda").json()
        assert (one["device"], one["health_status"]) == ("/dev/sda", "STANDBY")
        listing = {d["device"]: d for d in api.get("/api/v1/health/disks").json()["disks"]}
        overview = api.get("/api/v1/health/overview").json()
    sleeping = listing["/dev/sda"]
    # What lsblk knows stands in for what smartctl did not read.
    assert (sleeping["health_status"], sleeping["model"], sleeping["serial"], sleeping["capacity"]) == (
        "STANDBY", "WDC WD6400AACS-00G8B0", "WD-W3", "596.17 GB")
    assert listing["/dev/sdb"]["health_status"] == "PASSED"
    assert (overview["standby"], overview["healthy"], overview["unknown"]) == (1, 3, 0)
    assert host.woken == [] and "/dev/sda" in host.asleep
    assert all(c[:3] == ["smartctl", "-n", "standby"] for c in host.container_calls)


def test_the_smart_data_of_an_awake_disk_is_read(host):
    with admin_client() as api:
        storage = api.get(f"{DISKS}/sda/smart").json()
    assert (storage["healthy"], storage["power_state"], storage["temperature"]) == (True, "active", 31)
    assert host.container_calls == [["smartctl", "-n", "standby", "-j", "-a", "/dev/sda"]]


# --- nothing periodic wakes a sleeping disk ---------------------------------------------------------


def all_asleep(fake) -> set[str]:
    fake.asleep.update(top["path"] for top in fake.tree if top["type"] == "disk")
    return set(fake.asleep)


def test_the_model_wakes_a_disk_for_a_full_scan_of_the_disks(host):
    # What the checks below rule out: btrfs filesystem show without --mounted reads every disk.
    all_asleep(host)
    pools.list_pools()
    assert {disk for disk, _ in host.woken} >= {"/dev/sda", "/dev/sdb"}


def test_the_smart_monitor_leaves_sleeping_disks_asleep(host, monkeypatch, tmp_path):
    monkeypatch.setattr(health_router, "SMART_HISTORY_FILE", str(tmp_path / "smart_history.json"))
    asleep = all_asleep(host)
    assert health_router._smart_monitor_pass() == []
    assert host.woken == [] and host.asleep == asleep
    assert {tuple(c[:-1]) for c in host.container_calls} == {("smartctl", "-n", "standby", "-j", "-a")}


def test_the_smart_monitor_reads_a_disk_with_an_idle_timeout_only_after_it_was_used(host, monkeypatch, tmp_path):
    # Reading SMART data sends an awake disk commands, which restart its standby timer: hourly reads
    # would keep a disk with a timeout of an hour or more awake for good (ADR-0378).
    monkeypatch.setattr(health_router, "SMART_HISTORY_FILE", str(tmp_path / "smart_history.json"))
    io = {"sda": (1000, 500), "sdb": (10, 5), "sdc": (1, 1), "nvme0n1": (7, 7)}
    monkeypatch.setattr(disks, "io_counts", lambda: dict(io))
    host.files[spindown.RULES_PATH] = spindown.render({"WD-W3": 120})

    def monitor_pass() -> list[str]:
        host.container_calls.clear()
        assert health_router._smart_monitor_pass() == []
        return sorted(call[-1] for call in host.container_calls)

    every = monitor_pass()
    assert "/dev/sda" in every and "/dev/sdb" in every
    others = [path for path in every if path != "/dev/sda"]
    # sda has had no read or write since: it is left alone and keeps the verdict of its last read.
    assert monitor_pass() == others
    assert monitor_pass() == others
    assert "/dev/sda" in health_router._disk_verdicts
    # Something read the pool: sda is read at the next pass, and then left alone again.
    io["sda"] = (1012, 500)
    assert monitor_pass() == every
    assert monitor_pass() == others
    # Without the timeout it is read at every pass, as a disk without one is.
    host.files.pop(spindown.RULES_PATH)
    assert monitor_pass() == every


async def test_the_raid_monitor_reads_no_disk(host):
    asleep = all_asleep(host)
    health = {info.pool: info for info in await health_router.check_raid_health()}
    assert host.woken == [] and host.asleep == asleep
    assert (health["data"].status, health["data"].total_errors) == ("healthy", 0)
    # The pool on the sleeping disk that is not mounted, as udev recorded it.
    assert health["spare"].status == "unmounted"
    assert ["btrfs", "filesystem", "show", "--mounted", "--raw"] in host.calls
    assert not [c for c in host.calls if c[:3] == ["btrfs", "filesystem", "show"] and "--mounted" not in c]


async def test_the_raid_monitor_reports_a_managed_pool_that_is_not_mounted_from_what_udev_knows(host):
    host.files["/etc/fstab"] += marker("spare") + entry("spare", MEDIA_UUID)
    all_asleep(host)
    await health_router.check_raid_health()
    managed, _ = await health_router._update_pool_problems()
    assert managed == {"data", "spare"}
    [problem] = health_router.problems.of_source(health_router.POOL_PROBLEMS).values()
    assert problem.title == "Pool 'spare' is not mounted"
    assert problem.detail.startswith("Nothing is mounted at /mnt/pools/spare")
    assert host.woken == []


def unmount_every_pool(fake) -> None:
    """No btrfs filesystem is mounted: btrfs filesystem show --mounted then exits 1 without a word."""
    fake.mounts = [mount for mount in fake.mounts if mount["fstype"] != "btrfs"]
    assert fake.run(["btrfs", "filesystem", "show", "--mounted", "--raw"]).returncode == 1


async def test_the_raid_monitor_reports_the_pools_when_none_is_mounted(host):
    # The owner unmounted data, the only mounted pool, which UNOS mounts at boot.
    unmount_every_pool(host)
    asleep = all_asleep(host)
    health = {info.pool: info.status for info in await health_router.check_raid_health()}
    assert health == {"data": "unmounted", "spare": "unmounted"}
    await health_router._update_pool_problems()
    [problem] = health_router.problems.of_source(health_router.POOL_PROBLEMS).values()
    assert problem.title == "Pool 'data' is not mounted"
    assert problem.detail.startswith("Nothing is mounted at /mnt/pools/data")
    assert host.woken == [] and host.asleep == asleep


async def test_the_raid_monitor_reports_a_managed_pool_whose_disk_is_gone_when_none_is_mounted(host):
    # data did not mount at boot: sda is gone, and no other btrfs filesystem is mounted.
    unmount_every_pool(host)
    host.tree = [top for top in host.tree if top["path"] != "/dev/sda"]
    host.filesystems = [fs for fs in host.filesystems if fs["label"] != "data"]
    health = {info.pool: info.status for info in await health_router.check_raid_health()}
    assert health == {"spare": "unmounted"}
    await health_router._update_pool_problems()
    [problem] = health_router.problems.of_source(health_router.POOL_PROBLEMS).values()
    assert problem.title == "Pool 'data' is not mounted"
    assert problem.detail.startswith("The host finds no disk of pool data")


async def test_the_raid_endpoint_answers_when_no_pool_is_mounted(host):
    unmount_every_pool(host)
    with admin_client() as api:
        response = api.get("/api/v1/health/raid")
    assert response.status_code == 200, response.text
    assert {pool["pool"]: pool["status"] for pool in response.json()} == {
        "data": "unmounted", "spare": "unmounted"}


def test_the_monitoring_endpoints_count_the_pools_without_reading_a_disk(host, monkeypatch):
    # Prometheus or an uptime monitor scrapes them every few seconds (README.md).
    monkeypatch.setattr(importlib.import_module("src.main"), "_running_containers", lambda: 0)
    asleep = all_asleep(host)
    with admin_client() as api:
        assert "unos_storage_pools 2\n" in api.get("/api/v1/health/prometheus").text
        assert api.get("/api/v1/health/full").json()["components"]["storage"] == {"status": "ok", "pools": 2}
    assert host.woken == [] and host.asleep == asleep


def test_the_live_statistics_run_no_disk_command(monkeypatch):
    def refused(*args, **kwargs):
        raise AssertionError(f"a command for the statistics: {args!r}")

    monkeypatch.setattr(host_module, "run_on_host", refused)
    monkeypatch.setattr(host_module, "run_in_container", refused)
    assert events.get_system_stats()["type"] == "stats"


@pytest.fixture
def two_pools(tmp_path, monkeypatch):
    """The pools data and media below a folder of the test, each with an entry in its trash that is
    due for the expiry; data's disks sleep."""
    root = tmp_path.resolve()
    base = root / "pools"
    for name in ("data", "media"):
        entry = base / name / files_router.TRASH / "20200101T000000Z-0123456789abcdef"
        entry.mkdir(parents=True)
        (entry / "item").write_text("old")
        (entry / "origin").write_text("old.txt")
        os.chmod(entry, 0o700)
        os.chmod(base / name / files_router.TRASH, 0o700)
    mounts = _MountTable(root / "proc" / "mountinfo", base / "data")
    mounts.mount(base / "media")
    monkeypatch.setattr(files_router, "BASE_PATH", str(base))
    monkeypatch.setattr(files_router, "MOUNTINFO", str(mounts.path))
    asleep = {"data"}
    asked = []  # the pools whose disks were asked whether they sleep, in order

    def pool_sleeps(pool):
        asked.append(pool)
        return pool in asleep

    monkeypatch.setattr(spindown, "pool_sleeps", pool_sleeps)
    yield base, asleep, asked
    for name in ("data", "media"):
        os.chmod(base / name, 0o755)


def test_the_trash_expiry_passes_over_a_pool_whose_disks_sleep_and_takes_it_when_it_wakes(two_pools):
    base, asleep, _ = two_pools
    # A pool that sleeps is not even opened: its folder cannot be read here.
    os.chmod(base / "data", 0)
    now = datetime(2026, 11, 15, tzinfo=timezone.utc)
    done: dict[str, float] = {}
    assert files_router._expire_due_trash(now, done, 0.0) == (1, [], True)
    assert sorted(done) == ["media"]
    assert list((base / "media" / files_router.TRASH).iterdir()) == []

    # An hour later data still sleeps, and media's pass is not due: nothing is opened.
    os.chmod(base / "media", 0)
    assert files_router._expire_due_trash(now, done, 3600.0) == (0, [], False)

    # data woke: its pass runs at the next check; media's runs again a day after its last.
    os.chmod(base / "data", 0o755)
    asleep.clear()
    assert files_router._expire_due_trash(now, done, 7200.0) == (1, [], True)
    assert list((base / "data" / files_router.TRASH).iterdir()) == []
    os.chmod(base / "media", 0o755)
    assert files_router._expire_due_trash(now, done, 86400.0) == (0, [], True)
    assert done == {"data": 7200.0, "media": 86400.0}


def test_the_trash_expiry_runs_no_pass_when_it_cannot_tell_which_disks_sleep(two_pools, monkeypatch):
    base, _, _ = two_pools

    def unreadable(pool):
        raise RuntimeError("lsblk failed: no udev")

    monkeypatch.setattr(spindown, "pool_sleeps", unreadable)
    now = datetime(2026, 11, 15, tzinfo=timezone.utc)
    done: dict[str, float] = {}
    assert files_router._expire_due_trash(now, done, 0.0) == (0, [
        f"Could not tell whether the disks of pool {pool} sleep, so its trash was not cleared out: lsblk failed: no udev"
        for pool in ("data", "media")], True)
    assert len(list((base / "media" / files_router.TRASH).iterdir())) == 1
    assert done == {}  # both passes are still due


def test_the_trash_expiry_asks_a_pool_s_disks_whether_they_sleep_only_when_its_pass_is_due(two_pools):
    # Asking an awake disk is a command, which restarts its standby timer (ADR-0378): the hourly
    # checks ask nothing while no pass is due, and then only the disks of the pool that is due.
    base, asleep, asked = two_pools
    now = datetime(2026, 11, 15, tzinfo=timezone.utc)
    done: dict[str, float] = {}
    files_router._expire_due_trash(now, done, 0.0)
    assert asked == ["data", "media"]
    asked.clear()
    files_router._expire_due_trash(now, done, 3600.0)
    assert asked == ["data"]  # it sleeps, and its pass is due
    asleep.clear()
    files_router._expire_due_trash(now, done, 7200.0)
    asked.clear()
    for hour in range(3, 24):
        assert files_router._expire_due_trash(now, done, hour * 3600.0) == (0, [], False)
    assert asked == []
    files_router._expire_due_trash(now, done, 86400.0)
    assert asked == ["media"]


def test_the_trash_expiry_checks_every_hour(monkeypatch):
    assert (files_router.TRASH_EXPIRY_CHECK, files_router.TRASH_EXPIRY_INTERVAL) == (3600, 86400)
    passes = []

    def expire(now, done, clock):
        passes.append(clock)
        return 0, [], False

    monkeypatch.setattr(files_router, "_expire_due_trash", expire)
    monkeypatch.setattr(files_router, "TRASH_EXPIRY_CHECK", 0)

    async def run():
        task = asyncio.create_task(files_router.trash_expiry_loop())
        while len(passes) < 3:
            await asyncio.sleep(0.001)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())
    assert passes == sorted(passes)


# --- the pools whose disks sleep -------------------------------------------------------------------


def test_a_pool_whose_disks_sleep_is_found_without_waking_a_disk(host):
    host.add_disk("sdi", model="WDC WD40EFRX", serial="WD-M1")
    host.add_disk("sdj", model="WDC WD40EFRX", serial="WD-M2")
    host.add_btrfs("mirror", "7d4f2c91-4567-4f6e-8e3d-bc6f5d4e7f80", ["/dev/sdi", "/dev/sdj"], profile="raid1")
    host.dirs.add("/mnt/pools/mirror")
    host.mount_pool("mirror")
    assert (spindown.pool_sleeps("mirror"), spindown.pool_sleeps("data")) == (False, False)
    # One disk of the mirror sleeps: a read could go to it.
    host.asleep.update({"/dev/sdj", "/dev/sdb"})
    assert (spindown.pool_sleeps("mirror"), spindown.pool_sleeps("data")) == (True, False)
    host.asleep.add("/dev/sda")
    assert spindown.pool_sleeps("data")
    # A pool that is not mounted at the pools' base (spare), or not at all, has nothing to read.
    assert (spindown.pool_sleeps("spare"), spindown.pool_sleeps("gone")) == (False, False)
    assert host.woken == []
    assert not [c for c in host.calls if c[0] in ("btrfs", "wipefs", "blkid")]


def test_only_the_disks_of_the_pool_are_asked_whether_they_sleep(host):
    host.add_disk("sdi", model="WDC WD40EFRX", serial="WD-M1")
    host.add_btrfs("media", "7d4f2c91-4567-4f6e-8e3d-bc6f5d4e7f80", ["/dev/sdi"])
    host.dirs.add("/mnt/pools/media")
    host.mount_pool("media")
    assert not spindown.pool_sleeps("data")
    assert [call[-1] for call in host.container_calls] == ["/dev/sda"]


def test_the_power_state_is_read_with_smartctl_n_standby(monkeypatch):
    seen = []

    def smartctl(cmd, **kwargs):
        seen.append((cmd, kwargs["timeout"]))
        out = {"/dev/sda": (2, '{"smartctl": {"messages": [{"string": "Device is in STANDBY mode, exit(2)"}]}}'),
               "/dev/sdb": (2, '{"smartctl": {"messages": [{"string": "Device is in SLEEP mode, exit(2)"}]}}'),
               "/dev/sdc": (0, '{"model_name": "X"}'),
               "/dev/sdd": (2, '{"smartctl": {"messages": [{"string": "Smartctl open device: /dev/sdd failed"}]}}'),
               "/dev/sde": (4, '{"model_name": "X"}')}[cmd[-1]]
        return subprocess.CompletedProcess(cmd, *out, "")

    monkeypatch.setattr(host_module, "run_in_container", smartctl)
    assert [disks.power_state(f"/dev/sd{c}") for c in "abcde"] == ["standby", "standby", "active", "unknown", "active"]
    assert seen[0] == (["smartctl", "-n", "standby", "-i", "-j", "/dev/sda"], disks.POWER_CHECK_TIMEOUT)


def test_hdparm_is_an_optional_host_tool():
    assert host_module.OPTIONAL_HOST_TOOLS["hdparm"] == "hdparm"
    assert "hdparm" not in host_module.HOST_TOOLS
