"""Idle timeouts of the data disks, and reads that leave a sleeping disk asleep
(storage.disk-spindown, ADR-0378).

An administrator sets a disk's idle timeout on the Storage page (PUT /storage/disks/{name}/spindown):
Off or one of SPINDOWN_MINUTES, which hdparm -S expresses. UNOS applies it at once with hdparm -S on
the host and keeps it in its udev rule, which runs hdparm -S again whenever the disk appears (at boot
and when it is plugged in again), matched by its serial. The system disk, a boot or virtual device,
an NVMe disk and a disk without a serial that a rule can match are refused with the reason; a host
without hdparm says so. An ATA disk's own automatic offline data collection, which keeps it awake, is
turned off while it has a timeout (smartctl -o off, recorded in the rule) and on again when UNOS
removes it, only for a disk UNOS turned it off for; a failure there leaves the timeout set and the
answer says so. GET /storage/disks reports each disk's power state with hdparm -C on the host,
which asks the disk CHECK POWER MODE alone: a sleeping disk stays asleep, and the dev device's disk's
standby timer went on (smartctl -n standby -i, the read of a host without hdparm and of an NVMe disk,
asks an awake disk IDENTIFY, which restarts it). A power condition that hdparm does not name, as an EPC
disk's, is read with smartctl -n idle, which stops after CHECK POWER MODE too, and a disk whose hdparm -C
timed out gets no other while that one still runs on the host. The SMART reads of Storage and Disk
Health run smartctl -n standby, which leaves a sleeping disk alone.

Nothing UNOS does periodically wakes a sleeping disk: the SMART monitor, the RAID monitor and its pass
over the quotas of the volumes, the monitoring endpoints, the trash expiry and the live statistics are
run here with every disk asleep, on the FakeHost of test_pool_persistence, which wakes a disk for every
command that would read it or spin it up. Nor do they keep an idle disk awake: IDENTIFY and SMART commands restart its standby timer (CHECK
POWER MODE did not on the dev device's disk), so the SMART monitor leaves alone a disk with a timeout
that was not used since its last read, and the trash expiry asks a pool's disks whether they sleep only
when the pool's pass is due, and none of them again while the disk it found asleep had no read or
write.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, UTC
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
    the pool data on the SATA disk sda, mounted with UNOS's fstab entry, whose automatic offline data
    collection is on, as the dev device's disk's is; and, as a NAS has them, a second disk sdb with the
    pool spare, which is not mounted, a blank disk sdc and an NVMe drive, none of which has it."""
    fake = install_fake_host(monkeypatch)
    monkeypatch.setattr(audit_router, "AUDIT_FILE", str(tmp_path / "audit.jsonl"))
    fake.add_disk("sda", size=640_135_028_736, model="WDC WD6400AACS-00G8B0", serial="WD-W3")
    fake.offline_collection["/dev/sda"] = True
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


def power_read(device: str) -> list[str]:
    """The power read of a SATA disk (disks.power_state): hdparm -C on the host, under its timeout."""
    return ["timeout", "--kill-after=5", "15", "hdparm", "-C", device]


def power_reads(fake) -> list[str]:
    """The disks whose power hdparm -C read on the fake host, in order."""
    return [call[-1] for call in fake.calls if call[:1] == ["timeout"] and call[3:5] == ["hdparm", "-C"]]


IDENTIFY = ["smartctl", "-n", "standby", "-i", "-j"]  # and the disk: smartctl's power read
# And the disk: the read of a power condition that hdparm -C does not name, which smartctl names and
# stops at for SLEEP, STANDBY, STANDBY_Y, IDLE and IDLE_A/B/C without IDENTIFY.
EPC_READ = ["smartctl", "-n", "idle", "-i", "-j"]


def stuck_check(device: str) -> list[str]:
    """Whether the hdparm -C of DEVICE that timed out still runs on the host: pgrep in the container,
    which shares the host's process IDs."""
    return ["pgrep", "-f", f"^hdparm -C {device}$"]


# And the disk: its automatic offline data collection (-c), and whether it has SMART and SMART is on (-i,
# which reads the identity that -c reads anyway).
SCANS_READ = ["smartctl", "-n", "standby", "-i", "-c", "-j"]
RECORD = "# UNOS turned off the automatic offline data collection of the disk with the serial {serial}"
# What the answer says when a disk's own automatic offline scans could not be turned off for its timeout,
# or on again when it is removed: {path} is the disk's, {reason} why.
SCANS_LEFT_ON = ("{path} has the idle timeout, but the disk's own automatic offline scans could not be turned off "
                 "({reason}): they may keep it awake")
SCANS_LEFT_OFF = ("{path} has no idle timeout now, but the disk's own automatic offline scans, which UNOS turned off "
                  "for its timeout, could not be turned on again ({reason}): save Off again to try once more")
# What it says when smartctl -o off gave no clear answer: the drive may have taken it, so the rule records it.
SCANS_MAYBE_ON = ("{path} has the idle timeout, but whether the disk's own automatic offline scans were turned off "
                  "is unknown ({reason}): they may keep it awake")


def scans_switch(value: str, device: str) -> list[str]:
    """smartctl -o on or -o off of DEVICE in the container, which then reads the state it left."""
    return ["smartctl", "-n", "standby", "-o", value, "-i", "-c", "-j", device]


def switched(fake) -> list[list[str]]:
    """The smartctl -o commands that the fake disks took, in order."""
    return [change for change in fake.changes if change[0] == "smartctl"]


def records(fake) -> list[str]:
    """The serials of the disks whose automatic offline data collection UNOS's udev rule records that
    UNOS turned off; [] without the file."""
    prefix = RECORD.format(serial="")
    return [line[len(prefix):] for line in (fake.files.get(spindown.RULES_PATH) or "").splitlines()
            if line.startswith(prefix)]


def capabilities(cmd: list[str], status: int, values: int, returncode: int = 0) -> subprocess.CompletedProcess:
    """What smartctl -c -j answers with the offline data collection status STATUS and the capabilities VALUES."""
    return subprocess.CompletedProcess(cmd, returncode, json.dumps({
        "device": {"name": cmd[-1], "type": "sat", "protocol": "ATA"},
        "ata_smart_data": {"offline_data_collection": {"status": {"value": status}},
                           "capabilities": {"values": [values, 3]}},
    }), "")


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
        assert response.json() == {"name": "sda", "minutes": 5, "warning": None}
        assert listed(api)["sda"]["spindown"] == 5
    assert host.standby_timers == {"/dev/sda": 60}
    # Applied first, then kept, then udev reads its rules again.
    applied = host.calls.index(["timeout", "--kill-after=5", "30", "hdparm", "-S", "60", "/dev/sda"])
    written = host.calls.index(next(c for c in host.calls if c[:1] == ["/bin/sh"] and spindown.RULES_PATH in c))
    reloaded = host.calls.index(["udevadm", "control", "--reload"])
    assert applied < written < reloaded
    assert rules(host) == [RULE.format(serial="WD-W3", value=60)]
    assert entries(tmp_path / "audit.jsonl") == [
        ("settings_changed", "disk", "sda", "Set the idle timeout of /dev/sda (serial WD-W3) to 5 minutes and turned "
         "off its automatic offline data collection", True)]


def test_each_disk_has_its_line_and_off_removes_it_and_stops_the_timer(host, tmp_path):
    with admin_client() as api:
        assert api.put(f"{DISKS}/sda/spindown", json={"minutes": 20}).status_code == 200
        assert api.put(f"{DISKS}/sdb/spindown", json={"minutes": 60}).status_code == 200
        assert rules(host) == [RULE.format(serial="WD-W3", value=240), RULE.format(serial="WD-SPARE", value=242)]
        disks_now = listed(api)
        assert (disks_now["sda"]["spindown"], disks_now["sdb"]["spindown"], disks_now["sdc"]["spindown"]) == (20, 60, 0)

        assert api.put(f"{DISKS}/sda/spindown", json={"minutes": 0}).json() == {"name": "sda", "minutes": 0,
                                                                                "warning": None}
        assert rules(host) == [RULE.format(serial="WD-SPARE", value=242)]
        assert host.standby_timers["/dev/sda"] == 0
        assert api.put(f"{DISKS}/sdb/spindown", json={"minutes": 0}).status_code == 200
    # No line left: no file, as before the first timeout.
    assert spindown.RULES_PATH not in host.files
    assert ["rm", spindown.RULES_PATH] in host.changes
    assert [e[3] for e in entries(tmp_path / "audit.jsonl")][2:] == [
        "Turned off the idle timeout of /dev/sda (serial WD-W3) and turned its automatic offline data collection on "
        "again",
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
    # No timer was set: hdparm ran only to read the disks' power (hdparm -C).
    assert [c for c in host.calls if c[:1] in (["hdparm"], ["timeout"]) and "-C" not in c] == []
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
        host.asleep.add("/dev/sdc")
        found = listed(api)
        assert found["sda"]["spindown_problem"] == spindown.NO_HDPARM
        # Read with smartctl -n standby -i in the container, which leaves a sleeping disk alone.
        assert (found["sda"]["power_state"], found["sdc"]["power_state"]) == ("active", "standby")
        assert [IDENTIFY + [f"/dev/{name}"] for name in ("sda", "sdb", "sdc", "nvme0n1")] == host.container_calls
        assert host.woken == []
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
    # Nothing was asked of the disk's automatic offline data collection either.
    assert not [call for call in host.container_calls if "-c" in call] and switched(host) == []


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
    # The drive keeps its automatic offline data collection off across power cycles, and no rule records
    # that UNOS turned it off: it is turned on again, so that nothing UNOS changed is left unrecorded.
    assert switched(host) == [["smartctl", "-o", "off", "/dev/sda"], ["smartctl", "-o", "on", "/dev/sda"]]
    assert host.offline_collection["/dev/sda"] is True


@pytest.mark.parametrize("undo_fails", [False, True])
def test_when_the_rule_cannot_be_written_after_off_turned_the_scans_on_they_are_turned_off_again(
        host, tmp_path, undo_fails):
    # sdc keeps its line, so the file is written again rather than removed.
    with admin_client() as api:
        assert api.put(f"{DISKS}/sda/spindown", json={"minutes": 5}).status_code == 200
        assert api.put(f"{DISKS}/sdc/spindown", json={"minutes": 5}).status_code == 200
        host.fail[("/bin/sh", "-c")] = (1, "mv: cannot move: Read-only file system")
        if undo_fails:
            host.container_answer = lambda cmd: _timeout(cmd) if cmd == scans_switch("off", "/dev/sda") else None
        response = api.put(f"{DISKS}/sda/spindown", json={"minutes": 0})
    assert response.status_code == 500
    assert response.json()["detail"].startswith(
        "/dev/sda has no idle timeout until it restarts: the udev rule could not be written: ")
    assert host.standby_timers["/dev/sda"] == 0
    # The file still holds sda's line and its record, which udev applies at the next boot: the scans are
    # turned off again, as it records them, so that the timeout works then.
    assert rules(host) == [RULE.format(serial="WD-W3", value=60), RULE.format(serial="ZFL0BLANK", value=60)]
    assert records(host) == ["WD-W3"]
    [*_, (action, _, _, details, success)] = entries(tmp_path / "audit.jsonl")
    # Turned off again, UNOS changed nothing of them; otherwise they are on, as the entry says.
    turned = " and turned its automatic offline data collection on again" if undo_fails else ""
    assert (action, success) == ("settings_changed", False) and details.startswith(
        f"Turned off the idle timeout of /dev/sda (serial WD-W3){turned} until it restarts; then: ")
    if undo_fails:
        assert switched(host) == [["smartctl", "-o", "off", "/dev/sda"], ["smartctl", "-o", "on", "/dev/sda"]]
        assert host.offline_collection["/dev/sda"] is True
        assert details.endswith(
            "; the disk's own automatic offline scans may stay on: smartctl did not answer within 30 seconds")
    else:
        assert switched(host) == [["smartctl", "-o", "off", "/dev/sda"], ["smartctl", "-o", "on", "/dev/sda"],
                                  ["smartctl", "-o", "off", "/dev/sda"]]
        assert host.offline_collection["/dev/sda"] is False


# --- the disk's own automatic offline scans ------------------------------------------------------
# An ATA disk with automatic offline data collection on (SFF-8035i, smartctl -o on) scans its surface
# every 4 hours for hours, suspends the scan for each command of the host and resumes it later: it does
# not sleep meanwhile, so an idle timeout does not work for most of the time (measured on the dev
# device's sda, ADR-0378). UNOS turns it off while the disk has a timeout, and on again when the timeout
# is removed, only for a disk it turned it off for, which its udev rule records.


def test_a_timeout_turns_off_the_disk_s_automatic_offline_scans_and_the_rule_records_it(host, tmp_path):
    with admin_client() as api:
        response = api.put(f"{DISKS}/sda/spindown", json={"minutes": 5})
    assert response.status_code == 200, response.text
    assert response.json() == {"name": "sda", "minutes": 5, "warning": None}
    assert host.offline_collection["/dev/sda"] is False
    # Read and turned off in the container once hdparm -S woke the disk (-n standby leaves nothing alone
    # then), and recorded with the disk's line.
    assert [call for call in host.container_calls if "-c" in call] == [
        SCANS_READ + ["/dev/sda"], scans_switch("off", "/dev/sda")]
    assert [c for c in host.changes if c[0] in ("hdparm", "smartctl", "write")] == [
        ["hdparm", "-S", "60", "/dev/sda"], ["smartctl", "-o", "off", "/dev/sda"], ["write", spindown.RULES_PATH]]
    assert host.files[spindown.RULES_PATH].splitlines()[-2:] == [
        RULE.format(serial="WD-W3", value=60), RECORD.format(serial="WD-W3")]
    assert records(host) == ["WD-W3"]
    assert [e[3] for e in entries(tmp_path / "audit.jsonl")] == [
        "Set the idle timeout of /dev/sda (serial WD-W3) to 5 minutes and turned off its automatic offline data "
        "collection"]


def test_the_record_stays_while_the_disk_has_a_timeout_and_off_turns_the_scans_on_again(host, tmp_path):
    with admin_client() as api:
        assert api.put(f"{DISKS}/sda/spindown", json={"minutes": 5}).status_code == 200
        # Another timeout: they are off already, and the record stays, as the disk's line does.
        assert api.put(f"{DISKS}/sda/spindown", json={"minutes": 60}).json() == {
            "name": "sda", "minutes": 60, "warning": None}
        assert (rules(host), records(host)) == ([RULE.format(serial="WD-W3", value=242)], ["WD-W3"])
        # Another disk's change keeps it too.
        assert api.put(f"{DISKS}/sdb/spindown", json={"minutes": 20}).status_code == 200
        assert records(host) == ["WD-W3"]
        assert switched(host) == [["smartctl", "-o", "off", "/dev/sda"]]
        # Off: on again, as UNOS found them, and no record is left.
        assert api.put(f"{DISKS}/sda/spindown", json={"minutes": 0}).json() == {
            "name": "sda", "minutes": 0, "warning": None}
        assert switched(host)[-1] == ["smartctl", "-o", "on", "/dev/sda"]
        assert host.container_calls[-1] == scans_switch("on", "/dev/sda")
        assert host.offline_collection["/dev/sda"] is True
        assert (rules(host), records(host)) == ([RULE.format(serial="WD-SPARE", value=240)], [])
        # Off again: UNOS turned nothing off, so it turns nothing on.
        assert api.put(f"{DISKS}/sda/spindown", json={"minutes": 0}).status_code == 200
        assert api.put(f"{DISKS}/sdb/spindown", json={"minutes": 0}).status_code == 200
    assert len(switched(host)) == 2 and spindown.RULES_PATH not in host.files
    assert [e[3] for e in entries(tmp_path / "audit.jsonl") if e[2] == "sda"] == [
        "Set the idle timeout of /dev/sda (serial WD-W3) to 5 minutes and turned off its automatic offline data "
        "collection",
        "Set the idle timeout of /dev/sda (serial WD-W3) to 1 hour",
        "Turned off the idle timeout of /dev/sda (serial WD-W3) and turned its automatic offline data collection on "
        "again"]


def test_scans_that_were_off_already_are_left_off_and_not_recorded(host, tmp_path):
    # Turned off by hand (smartctl -o off) before: UNOS changed nothing, so its Off changes nothing.
    host.offline_collection["/dev/sda"] = False
    with admin_client() as api:
        assert api.put(f"{DISKS}/sda/spindown", json={"minutes": 5}).json()["warning"] is None
        assert records(host) == []
        assert api.put(f"{DISKS}/sda/spindown", json={"minutes": 0}).json()["warning"] is None
    assert switched(host) == [] and host.offline_collection["/dev/sda"] is False
    assert [call for call in host.container_calls if "-c" in call] == [SCANS_READ + ["/dev/sda"]]
    assert [e[3] for e in entries(tmp_path / "audit.jsonl")] == [
        "Set the idle timeout of /dev/sda (serial WD-W3) to 5 minutes",
        "Turned off the idle timeout of /dev/sda (serial WD-W3)"]


def test_a_timeout_saved_again_turns_off_scans_that_are_on(host, tmp_path):
    # A timeout that the rule had before UNOS turned the scans off, as the dev device's sda had: saving it
    # again turns them off and records it, a change of the rule, audited.
    host.files[spindown.RULES_PATH] = spindown.render({"WD-W3": 20})
    with admin_client() as api:
        assert api.put(f"{DISKS}/sda/spindown", json={"minutes": 20}).json() == {
            "name": "sda", "minutes": 20, "warning": None}
    assert switched(host) == [["smartctl", "-o", "off", "/dev/sda"]] and records(host) == ["WD-W3"]
    # Turned on again by hand (smartctl -o on): the next Save turns them off again, keeps the record and
    # writes nothing, and the change of the disk is audited.
    host.offline_collection["/dev/sda"] = True
    rule = host.files[spindown.RULES_PATH]
    with admin_client() as api:
        assert api.put(f"{DISKS}/sda/spindown", json={"minutes": 20}).json()["warning"] is None
    assert host.offline_collection["/dev/sda"] is False and host.files[spindown.RULES_PATH] == rule
    assert [c for c in host.changes if c[0] == "write"] == [["write", spindown.RULES_PATH]]
    assert [e[3] for e in entries(tmp_path / "audit.jsonl")] == [
        "Set the idle timeout of /dev/sda (serial WD-W3) to 20 minutes and turned off its automatic offline data "
        "collection"] * 2


def test_a_disk_without_automatic_offline_scans_is_left_alone(host):
    with admin_client() as api:
        # sdc's capabilities lack "Auto Offline data collection on/off support" (0x59), and it has none.
        assert api.put(f"{DISKS}/sdc/spindown", json={"minutes": 5}).json() == {
            "name": "sdc", "minutes": 5, "warning": None}
        assert api.put(f"{DISKS}/sdc/spindown", json={"minutes": 0}).status_code == 200
        # A SCSI or SAS disk: smartctl -c reads no ATA SMART data of it, and -o is ATA's alone (smartctl.cpp).
        host.container_answer = lambda cmd: subprocess.CompletedProcess(cmd, 0, json.dumps(
            {"device": {"name": cmd[-1], "type": "scsi", "protocol": "SCSI"}}), "") if "-c" in cmd else None
        assert api.put(f"{DISKS}/sdb/spindown", json={"minutes": 5}).json() == {
            "name": "sdb", "minutes": 5, "warning": None}
        # An NVMe disk gets no idle timeout, and no smartctl -c.
        assert api.put(f"{DISKS}/nvme0n1/spindown", json={"minutes": 5}).status_code == 409
    assert [call for call in host.container_calls if "-c" in call] == [
        SCANS_READ + ["/dev/sdc"], SCANS_READ + ["/dev/sdb"]]
    assert switched(host) == [] and records(host) == []


@pytest.mark.parametrize("smart", ["disabled", "unsupported"])
def test_a_disk_whose_smart_is_off_or_missing_is_left_alone(host, tmp_path, smart):
    # SMART turned off (smartctl -s off, a BIOS or an enclosure), or none at all: no automatic offline data
    # collection runs, which is a SMART timer function that SMART DISABLE OPERATIONS stops. smartctl -c
    # alone would say nothing of it (ataprint.cpp prints "SMART Disabled" with pout, which -j drops); -i
    # writes smart_support.
    host.smart_off["/dev/sdc"] = smart
    with admin_client() as api:
        assert api.put(f"{DISKS}/sdc/spindown", json={"minutes": 5}).json() == {
            "name": "sdc", "minutes": 5, "warning": None}
        assert api.put(f"{DISKS}/sdc/spindown", json={"minutes": 0}).json() == {
            "name": "sdc", "minutes": 0, "warning": None}
    assert [call for call in host.container_calls if "-c" in call] == [SCANS_READ + ["/dev/sdc"]]
    assert switched(host) == [] and spindown.RULES_PATH not in host.files
    assert [e[3] for e in entries(tmp_path / "audit.jsonl")] == [
        "Set the idle timeout of /dev/sdc (serial ZFL0BLANK) to 5 minutes",
        "Turned off the idle timeout of /dev/sdc (serial ZFL0BLANK)"]


def timed_out(match: list[str]):
    """A container_answer under which the command MATCH does not answer within its timeout."""
    def answer(cmd):
        if cmd == match:
            raise subprocess.TimeoutExpired(cmd, 30)
        return None
    return answer


@pytest.mark.parametrize("failure, reason", [
    (timed_out(SCANS_READ + ["/dev/sda"]), "smartctl did not answer within 30 seconds"),
    (lambda cmd: subprocess.CompletedProcess(cmd, 2, json.dumps({"smartctl": {"messages": [
        {"string": "Smartctl open device: /dev/sda [SAT] failed: No such device", "severity": "error"}]}}), "")
     if cmd == SCANS_READ + ["/dev/sda"] else None,
     "smartctl could not read them: Smartctl open device: /dev/sda [SAT] failed: No such device"),
    (lambda cmd: subprocess.CompletedProcess(cmd, 0, "Read SMART Data failed", "")
     if cmd == SCANS_READ + ["/dev/sda"] else None, "smartctl could not read them: exit status 0"),
    # JSON of another shape: no failure of the Save either.
    (lambda cmd: subprocess.CompletedProcess(cmd, 0, json.dumps({"ata_smart_data": {
        "offline_data_collection": {"status": 132}, "capabilities": {"values": {}}}, "smartctl": {"messages": "x"}}), "")
     if cmd == SCANS_READ + ["/dev/sda"] else None, "smartctl could not read them: exit status 0"),
    # The drive refused the command (exit status bit 2, smartctl's FAILSMART) and still has them on.
    (lambda cmd: capabilities(cmd, 0x84, 0x7b, returncode=4) if cmd == scans_switch("off", "/dev/sda") else None,
     "smartctl -o off left them on, exit status 4"),
    # On, but the drive offers no switch for them (no "Auto Offline data collection on/off support").
    (lambda cmd: capabilities(cmd, 0x84, 0x59) if "-c" in cmd else None, "the disk cannot switch them off"),
])
def test_scans_that_cannot_be_turned_off_leave_the_timeout_set_and_the_answer_says_so(
        host, tmp_path, caplog, failure, reason):
    host.container_answer = failure
    with admin_client() as api:
        response = api.put(f"{DISKS}/sda/spindown", json={"minutes": 5})
        assert response.status_code == 200, response.text
        warning = SCANS_LEFT_ON.format(path="/dev/sda", reason=reason)
        assert response.json() == {"name": "sda", "minutes": 5, "warning": warning}
        assert listed(api)["sda"]["spindown"] == 5
    assert host.standby_timers == {"/dev/sda": 60} and rules(host) == [RULE.format(serial="WD-W3", value=60)]
    assert records(host) == [] and host.offline_collection["/dev/sda"] is True
    assert warning in caplog.text
    assert [e[3:] for e in entries(tmp_path / "audit.jsonl")] == [
        (f"Set the idle timeout of /dev/sda (serial WD-W3) to 5 minutes; {warning}", True)]


def _timeout(cmd):
    raise subprocess.TimeoutExpired(cmd, 30)


# smartctl sends -o off first and then reads the SMART data again (ataprint.cpp): an answer without that
# state does not say that the drive did not take the command.
@pytest.mark.parametrize("applied, answer, reason", [
    # Killed at its timeout, before or after the drive took it.
    (False, _timeout, "smartctl did not answer within 30 seconds"),
    (True, _timeout, "smartctl did not answer within 30 seconds"),
    # The re-read failed: smart_val_ok is false, and -c prints no ata_smart_data (FAILSMART).
    (True, lambda cmd: subprocess.CompletedProcess(cmd, 4, json.dumps({
        "device": {"name": cmd[-1], "type": "sat", "protocol": "ATA"},
        "smart_support": {"available": True, "enabled": True}, "smartctl": {"exit_status": 4}}), ""),
     "smartctl could not read them: exit status 4"),
    (True, lambda cmd: subprocess.CompletedProcess(cmd, 4, "Read SMART Data failed", ""),
     "smartctl could not read them: exit status 4"),
    # An answer of no ATA disk.
    (True, lambda cmd: subprocess.CompletedProcess(cmd, 0, json.dumps(
        {"device": {"name": cmd[-1], "type": "scsi", "protocol": "SCSI"}}), ""), "smartctl -o off read no state"),
])
def test_scans_whose_switch_gave_no_clear_answer_are_recorded_and_off_turns_them_on_again(
        host, tmp_path, caplog, applied, answer, reason):
    def respond(cmd):
        if cmd != scans_switch("off", "/dev/sda"):
            return None
        if applied:  # the drive took it
            host.offline_collection["/dev/sda"] = False
            host.changes.append(["smartctl", "-o", "off", "/dev/sda"])
        return answer(cmd)

    host.container_answer = respond
    with admin_client() as api:
        response = api.put(f"{DISKS}/sda/spindown", json={"minutes": 5})
        assert response.status_code == 200, response.text
        warning = SCANS_MAYBE_ON.format(path="/dev/sda", reason=reason)
        assert response.json() == {"name": "sda", "minutes": 5, "warning": warning}
        assert warning in caplog.text
        # The drive may have turned them off: recorded, so that Off puts back what UNOS found (on).
        assert (rules(host), records(host)) == ([RULE.format(serial="WD-W3", value=60)], ["WD-W3"])
        host.container_answer = None
        assert api.put(f"{DISKS}/sda/spindown", json={"minutes": 0}).json() == {
            "name": "sda", "minutes": 0, "warning": None}
    assert host.container_calls[-1] == scans_switch("on", "/dev/sda")
    assert host.offline_collection["/dev/sda"] is True and spindown.RULES_PATH not in host.files
    assert [e[3] for e in entries(tmp_path / "audit.jsonl")] == [
        f"Set the idle timeout of /dev/sda (serial WD-W3) to 5 minutes; {warning}",
        "Turned off the idle timeout of /dev/sda (serial WD-W3) and turned its automatic offline data collection on "
        "again"]


def test_scans_that_cannot_be_turned_on_again_stay_recorded_until_off_is_saved_again(host, tmp_path, caplog):
    with admin_client() as api:
        assert api.put(f"{DISKS}/sda/spindown", json={"minutes": 5}).status_code == 200
        host.container_answer = timed_out(scans_switch("on", "/dev/sda"))
        response = api.put(f"{DISKS}/sda/spindown", json={"minutes": 0})
        assert response.status_code == 200, response.text
        warning = SCANS_LEFT_OFF.format(path="/dev/sda", reason="smartctl did not answer within 30 seconds")
        assert response.json() == {"name": "sda", "minutes": 0, "warning": warning}
        assert warning in caplog.text
        # The timeout is off; the record stays, alone in the file, so that the next Off tries again.
        assert host.standby_timers["/dev/sda"] == 0 and host.offline_collection["/dev/sda"] is False
        assert (rules(host), records(host)) == ([], ["WD-W3"])
        assert listed(api)["sda"]["spindown"] == 0
        host.container_answer = None
        assert api.put(f"{DISKS}/sda/spindown", json={"minutes": 0}).json()["warning"] is None
    assert host.offline_collection["/dev/sda"] is True and spindown.RULES_PATH not in host.files
    assert [e[3] for e in entries(tmp_path / "audit.jsonl")][1:] == [
        f"Turned off the idle timeout of /dev/sda (serial WD-W3); {warning}",
        "Turned off the idle timeout of /dev/sda (serial WD-W3) and turned its automatic offline data collection on "
        "again"]


def test_the_rule_file_keeps_its_records_and_drops_one_edited_by_hand(host):
    text = spindown.render({"WD-W3": 5, "WD-SPARE": 60}, frozenset({"WD-W3", "GONE1"}))
    # Each record follows its disk's line; the record of a disk without a line comes last.
    assert text.splitlines()[-4:] == [RULE.format(serial="WD-W3", value=60), RECORD.format(serial="WD-W3"),
                                      RULE.format(serial="WD-SPARE", value=242), RECORD.format(serial="GONE1")]
    assert all(line.startswith("#") for line in text.splitlines()[:-4])
    host.files[spindown.RULES_PATH] = text + RECORD.format(serial='A"B') + "\n# UNOS turned it off: ZZZ\n"
    assert spindown.read_rule() == spindown.Rule({"WD-W3": 5, "WD-SPARE": 60}, frozenset({"WD-W3", "GONE1"}))
    assert spindown.read_timeouts() == {"WD-W3": 5, "WD-SPARE": 60}
    # A change of another disk keeps them, and drops what UNOS did not write.
    with admin_client() as api:
        assert api.put(f"{DISKS}/sdc/spindown", json={"minutes": 10}).status_code == 200
    assert spindown.read_rule() == spindown.Rule(
        {"WD-W3": 5, "WD-SPARE": 60, "ZFL0BLANK": 10}, frozenset({"WD-W3", "GONE1"}))
    assert host.files[spindown.RULES_PATH] == spindown.render(
        {"WD-W3": 5, "WD-SPARE": 60, "ZFL0BLANK": 10}, frozenset({"WD-W3", "GONE1"}))
    # Only spindown.py names the rule file, and only set_timeout writes it: no startup or restore path
    # rewrites it, so none could drop a record.
    src = Path(__file__).resolve().parents[1] / "src"
    naming = [path.relative_to(src).as_posix() for path in sorted(src.rglob("*.py"))
              if "RULES_PATH" in (text := path.read_text()) or "90-unos-disk-spindown" in text]
    assert naming == ["storage/spindown.py"]


def test_the_rule_is_read_as_udev_reads_a_file_without_nul_bytes(host):
    """A line ends at \\n or \\r, as udev's lines do, and only spaces and tabs around it are blanks; an
    hdparm -S value is written in ASCII digits. udev also ends a line at a NUL byte, which UNOS never
    writes, and read_rule does not. deploy/uninstall.sh reads the rule as read_rule does, and does not
    read a rule with a NUL byte at all (test_deploy_scripts.py), so it undoes no more and no less than
    the supervisor counts."""
    host.files[spindown.RULES_PATH] = (
        RULE.format(serial="WD-W3", value=60) + "\r" + RECORD.format(serial="WD-W3") + "\r\n"
        + " \t" + RULE.format(serial="WD-SPARE", value=120) + "\t \n"
        + " " + RULE.format(serial="NBSP", value=60) + "\n"
        + RULE.format(serial="IDEO", value=60) + "　\n"
        + RECORD.format(serial="UNIT") + "\x1f\n"
        + RULE.format(serial="VT", value=60) + "\v\n"
        + RULE.format(serial="FF1", value=60) + "\f" + RULE.format(serial="FF2", value=60) + "\n"
        + RULE.format(serial="ARABIC", value="٦٠") + "\n"
    )

    assert spindown.read_rule() == spindown.Rule({"WD-W3": 5, "WD-SPARE": 10}, frozenset({"WD-W3"}))


def test_the_e2e_fake_warns_with_the_server_s_text():
    """webui/tests/storage-fakes.ts answers a timeout whose disk's scans could not be turned off with the
    server's text, word for word (ADR-0189)."""
    fakes = (Path(__file__).resolve().parents[2] / "webui" / "tests" / "storage-fakes.ts").read_text(encoding="utf-8")
    assert "`" + SCANS_LEFT_ON.format(path="${path}", reason="${reason}") + "`" in fakes
    assert spindown.SCANS_LEFT_ON == SCANS_LEFT_ON and spindown.SCANS_LEFT_OFF == SCANS_LEFT_OFF


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
    # hdparm -C on the host asks each SATA disk CHECK POWER MODE alone, which did not restart the dev
    # device's disk's standby timer: never smartctl -i, whose IDENTIFY does. The NVMe disk, which hdparm
    # cannot ask and which gets no idle timeout, is read with smartctl.
    assert power_reads(host) == ["/dev/sda", "/dev/sdb", "/dev/sdc"]
    assert [power_read(f"/dev/{name}") for name in ("sda", "sdb", "sdc")] == [
        call for call in host.calls if "-C" in call]
    assert host.container_calls == [IDENTIFY + ["/dev/nvme0n1"]]


def test_a_disk_whose_power_state_cannot_be_read_is_unknown(host, monkeypatch):
    # sdb's hdparm was ended by the host's timeout (exit status 124), sdc's could not ask the disk; sda's
    # host command did not answer in time. None of them is asked again with smartctl -i.
    host.fail[tuple(power_read("/dev/sdb"))] = (124, "")
    host.fail[tuple(power_read("/dev/sdc"))] = (5, " HDIO_DRIVE_CMD(checkpowermode) failed: Input/output error")
    answer = host.run

    def slow(cmd, **kwargs):
        if cmd == power_read("/dev/sda"):
            raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])
        return answer(cmd, **kwargs)

    monkeypatch.setattr(host_module, "run_on_host", slow)
    with admin_client() as api:
        found = listed(api)
    assert {name: d["power_state"] for name, d in found.items()} == {
        "sda": "unknown", "sdb": "unknown", "sdc": "unknown", "nvme0n1": "active"}
    assert host.container_calls == [IDENTIFY + ["/dev/nvme0n1"]]


def test_storage_names_the_power_conditions_of_an_epc_disk_without_identify(host):
    # An EPC disk (ACS's Extended Power Conditions, as NAS drives with EPC timers have) answers CHECK POWER
    # MODE with idle_a, idle_b, idle_c (81h-83h) or standby_y (01h), which hdparm -C prints as "unknown"
    # with exit status 0. smartctl -n idle asks the disk CHECK POWER MODE again and names them, and stops
    # there: no IDENTIFY, which would restart the disk's standby timer.
    host.epc.update({"/dev/sda": "idle_b", "/dev/sdb": "standby_y", "/dev/sdc": "idle_a"})
    host.asleep.add("/dev/sdb")
    with admin_client() as api:
        found = listed(api)
    assert {name: d["power_state"] for name, d in found.items()} == {
        "sda": "active", "sdb": "standby", "sdc": "active", "nvme0n1": "active"}
    assert power_reads(host) == ["/dev/sda", "/dev/sdb", "/dev/sdc"]
    assert host.container_calls == [
        *(EPC_READ + [f"/dev/{name}"] for name in ("sda", "sdb", "sdc")), IDENTIFY + ["/dev/nvme0n1"]]
    assert host.identified == ["/dev/nvme0n1"]
    # The disk in standby_y sleeps: it stays asleep and is not probed for signatures.
    assert host.woken == [] and host.asleep == {"/dev/sdb"}
    assert not [c for c in host.calls if c[0] == "wipefs" and c[-1] == "/dev/sdb"]
    assert found["sdb"]["has_data"] is True


def test_a_power_condition_that_hdparm_does_not_name_is_read_with_smartctl_n_idle(monkeypatch):
    # smartctl -n idle stops after CHECK POWER MODE for SLEEP, STANDBY, STANDBY_Y, IDLE and IDLE_A/B/C, with
    # exit status bit 1 and "Device is in <MODE> mode": the message of its text output, and with -j one of
    # the JSON's smartctl.messages. IDLE* is active, STANDBY* and SLEEP standby. When it went on to read the
    # identity, CHECK POWER MODE answered a mode it does not stop for (the disk is active by now) or one it
    # does not name either: that IDENTIFY restarted the disk's timer and says nothing of its mode, so the
    # disk is unknown, as it is when smartctl cannot open it or does not answer.
    def run_on_host(cmd, **kwargs):
        assert cmd[3:5] == ["hdparm", "-C"], cmd
        return host_module.HostResult(0, f"\n{cmd[-1]}:\n drive state is:  unknown\n", "")

    def stopped(mode: str, *, as_json: bool = True) -> tuple[int, str]:
        message = f"Device is in {mode} mode, exit(2)"
        if as_json:
            return 2, json.dumps({"json_format_version": [1, 0], "smartctl": {
                "version": [7, 3], "messages": [{"string": message, "severity": "information"}], "exit_status": 2},
                "device": {"name": "/dev/sdx", "type": "sat"}})
        return 2, f"smartctl 7.3 2022-02-28 r5338 [aarch64-linux-6.1.0] (local build)\n\n{message}\n"

    answers = {
        "/dev/sda": stopped("IDLE_A"),
        "/dev/sdb": stopped("IDLE_B"),
        "/dev/sdc": stopped("IDLE_C"),
        "/dev/sdd": stopped("IDLE"),
        "/dev/sde": stopped("STANDBY_Y"),
        "/dev/sdf": stopped("STANDBY"),
        "/dev/sdg": stopped("SLEEP"),
        "/dev/sdh": stopped("IDLE_B", as_json=False),
        "/dev/sdi": stopped("STANDBY_Y", as_json=False),
        # smartctl names a SCSI disk's power conditions in several words (its scsiprint.cpp), and one
        # of them "IDLE_" for IDLE_B by command.
        "/dev/sdo": stopped("IDLE BY TIMER"),
        "/dev/sdp": stopped("IDLE_ BY COMMAND"),
        "/dev/sdq": stopped("IDLE_C BY TIMER", as_json=False),
        "/dev/sdr": stopped("STANDBY_Y BY COMMAND"),
        # It went on to read the identity (exit status 4: a SMART command failed after it).
        "/dev/sdj": (0, '{"model_name": "X"}'),
        "/dev/sdk": (4, '{"model_name": "X"}'),
        "/dev/sdl": (2, json.dumps({"smartctl": {"messages": [
            {"string": "Smartctl open device: /dev/sdl failed: No such device", "severity": "error"}]}})),
        # The message without the exit status of a stop is no stop.
        "/dev/sdm": (0, stopped("STANDBY")[1]),
    }
    seen = []

    def smartctl(cmd, **kwargs):
        seen.append((cmd, kwargs["timeout"]))
        if cmd[-1] == "/dev/sdn":  # smartctl did not answer in time
            raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])
        return subprocess.CompletedProcess(cmd, *answers[cmd[-1]], "")

    monkeypatch.setattr(host_module, "run_on_host", run_on_host)
    monkeypatch.setattr(host_module, "run_in_container", smartctl)
    assert {c: disks.power_state(f"/dev/sd{c}") for c in "abcdefghijklmnopqr"} == {
        "a": "active", "b": "active", "c": "active", "d": "active", "e": "standby", "f": "standby",
        "g": "standby", "h": "active", "i": "standby", "j": "unknown", "k": "unknown", "l": "unknown",
        "m": "unknown", "n": "unknown", "o": "active", "p": "active", "q": "active", "r": "standby"}
    assert [cmd for cmd, _ in seen] == [EPC_READ + [f"/dev/sd{c}"] for c in "abcdefghijklmnopqr"]
    assert {timeout for _, timeout in seen} == {disks.POWER_CHECK_TIMEOUT}


def test_the_smart_reads_leave_a_disk_in_standby_y_asleep(host):
    # smartctl -n standby stops for an EPC disk's standby_y too: "Device is in STANDBY_Y mode".
    host.epc["/dev/sdb"] = "standby_y"
    host.asleep.add("/dev/sdb")
    with admin_client() as api:
        storage = api.get(f"{DISKS}/sdb/smart").json()
        one = api.get("/api/v1/health/disk/sdb").json()
        listing = {d["device"]: d for d in api.get("/api/v1/health/disks").json()["disks"]}
    assert (storage["power_state"], storage["healthy"]) == ("standby", None)
    assert (one["health_status"], listing["/dev/sdb"]["health_status"]) == ("STANDBY", "STANDBY")
    assert host.woken == [] and "/dev/sdb" not in host.identified


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


async def test_the_quota_use_of_the_volumes_reads_no_disk(host, tmp_path, monkeypatch):
    # The RAID monitor's pass over the quotas of the volumes (storage.volume_usage): the kernel's
    # counters in sysfs, and the list of the volumes without a scan of the disks, after a write.
    from src.storage import qgroups, volume_usage

    from .test_volume_usage import FakeSysfs
    from .test_volumes import FakeSubvolumes

    host.subvols = FakeSubvolumes(host)
    host.subvols.quotas.add(DATA_UUID)
    documents = host.subvols.add(DATA_UUID, "documents")
    sysfs = FakeSysfs(tmp_path / "sys" / "fs" / "btrfs")
    monkeypatch.setattr(qgroups, "SYS_FS_BTRFS", str(sysfs.root))
    sysfs.quotas()
    sysfs.group(documents, 9 << 30, 10 << 30)
    sysfs.generation(7)
    asleep = all_asleep(host)

    await health_router.check_raid_health()
    pools_read = health_router._take_pools_read()
    for _ in range(3):
        await volume_usage.check(pools_read.pools)

    assert host.woken == [] and host.asleep == asleep
    assert [p.kind for p in health_router.problems.of_source(volume_usage.SOURCE).values()] == ["volume_quota_90"]
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

    def pool_sleeps(pool, found=None):
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
    now = datetime(2026, 11, 15, tzinfo=UTC)
    done: dict[str, float] = {}
    sleeping: dict[str, spindown.Asleep] = {}
    assert files_router._expire_due_trash(now, done, 0.0, sleeping) == (1, [], True)
    assert sorted(done) == ["media"]
    assert list((base / "media" / files_router.TRASH).iterdir()) == []

    # An hour later data still sleeps, and media's pass is not due: nothing is opened.
    os.chmod(base / "media", 0)
    assert files_router._expire_due_trash(now, done, 3600.0, sleeping) == (0, [], False)

    # data woke: its pass runs at the next check; media's runs again a day after its last.
    os.chmod(base / "data", 0o755)
    asleep.clear()
    assert files_router._expire_due_trash(now, done, 7200.0, sleeping) == (1, [], True)
    assert list((base / "data" / files_router.TRASH).iterdir()) == []
    os.chmod(base / "media", 0o755)
    assert files_router._expire_due_trash(now, done, 86400.0, sleeping) == (0, [], True)
    assert done == {"data": 7200.0, "media": 86400.0}


def test_the_trash_expiry_runs_no_pass_when_it_cannot_tell_which_disks_sleep(two_pools, monkeypatch):
    base, _, _ = two_pools

    def unreadable(pool, found=None):
        raise RuntimeError("lsblk failed: no udev")

    monkeypatch.setattr(spindown, "pool_sleeps", unreadable)
    now = datetime(2026, 11, 15, tzinfo=UTC)
    done: dict[str, float] = {}
    sleeping: dict[str, spindown.Asleep] = {}
    assert files_router._expire_due_trash(now, done, 0.0, sleeping) == (0, [
        f"Could not tell whether the disks of pool {pool} sleep, so its trash was not cleared out: lsblk failed: no udev"
        for pool in ("data", "media")], True)
    assert len(list((base / "media" / files_router.TRASH).iterdir())) == 1
    assert done == {}  # both passes are still due


def test_the_trash_expiry_asks_a_pool_s_disks_whether_they_sleep_only_when_its_pass_is_due(two_pools):
    # Asking an awake disk is a command, which can restart its standby timer (smartctl's IDENTIFY, on a
    # host without hdparm, ADR-0378): the hourly checks ask nothing while no pass is due, and then only
    # the disks of the pool that is due.
    base, asleep, asked = two_pools
    now = datetime(2026, 11, 15, tzinfo=UTC)
    done: dict[str, float] = {}
    sleeping: dict[str, spindown.Asleep] = {}
    files_router._expire_due_trash(now, done, 0.0, sleeping)
    assert asked == ["data", "media"]
    asked.clear()
    files_router._expire_due_trash(now, done, 3600.0, sleeping)
    assert asked == ["data"]  # it sleeps, and its pass is due
    asleep.clear()
    files_router._expire_due_trash(now, done, 7200.0, sleeping)
    asked.clear()
    for hour in range(3, 24):
        assert files_router._expire_due_trash(now, done, hour * 3600.0, sleeping) == (0, [], False)
    assert asked == []
    files_router._expire_due_trash(now, done, 86400.0, sleeping)
    assert asked == ["media"]


def test_the_trash_expiry_asks_an_awake_disk_of_a_pool_put_off_for_a_sleeping_one_once(host, tmp_path, monkeypatch):
    # A raid1 pool whose reads all go to one disk (btrfs picks the mirror by the reader's pid, and
    # noatime reads write nothing): sdi is awake and sdj sleeps, so the pass is put off. Asking sdi,
    # which lsblk lists first, at every hourly check would restart its standby timer for good
    # (ADR-0378): the check remembers sdj and asks nothing until the kernel counted a read or write
    # on it, and then asks sdj first.
    host.add_disk("sdi", model="WDC WD40EFRX", serial="WD-M1")
    host.add_disk("sdj", model="WDC WD40EFRX", serial="WD-M2")
    host.add_btrfs("mirror", "7d4f2c91-4567-4f6e-8e3d-bc6f5d4e7f80", ["/dev/sdi", "/dev/sdj"], profile="raid1")
    host.dirs.add("/mnt/pools/mirror")
    host.mount_pool("mirror")
    host.asleep.add("/dev/sdj")
    root = tmp_path.resolve()
    base = root / "pools"
    entry = base / "mirror" / files_router.TRASH / "20200101T000000Z-0123456789abcdef"
    entry.mkdir(parents=True)
    (entry / "item").write_text("old")
    (entry / "origin").write_text("old.txt")
    os.chmod(entry, 0o700)
    os.chmod(entry.parent, 0o700)
    mounts = _MountTable(root / "proc" / "mountinfo", base / "mirror")
    monkeypatch.setattr(files_router, "BASE_PATH", str(base))
    monkeypatch.setattr(files_router, "MOUNTINFO", str(mounts.path))
    io = {"sdi": (5000, 0), "sdj": (40, 3)}
    monkeypatch.setattr(disks, "io_counts", lambda: dict(io))
    now = datetime(2026, 11, 15, tzinfo=UTC)
    done: dict[str, float] = {}
    sleeping: dict[str, spindown.Asleep] = {}

    def asked() -> list[str]:
        paths = power_reads(host)
        host.calls.clear()
        return paths

    for hour in range(6):
        io["sdi"] = (5000 + 100 * hour, 0)  # sdi serves the reads
        assert files_router._expire_due_trash(now, done, hour * 3600.0, sleeping) == (0, [], False)
    assert asked() == ["/dev/sdi", "/dev/sdj"]
    # sdj was written to and is asleep again: it alone is asked.
    io["sdj"] = (40, 9)
    assert files_router._expire_due_trash(now, done, 6 * 3600.0, sleeping) == (0, [], False)
    assert asked() == ["/dev/sdj"]
    assert files_router._expire_due_trash(now, done, 7 * 3600.0, sleeping) == (0, [], False)
    assert asked() == []
    # sdj woke for a write: it is asked first, then sdi, and the pass runs.
    host.asleep.clear()
    io["sdj"] = (41, 12)
    assert files_router._expire_due_trash(now, done, 8 * 3600.0, sleeping) == (1, [], True)
    assert asked() == ["/dev/sdj", "/dev/sdi"]
    assert sleeping == {} and done == {"mirror": 8 * 3600.0}
    assert host.woken == [] and host.container_calls == []


def test_the_trash_expiry_checks_every_hour(monkeypatch):
    assert (files_router.TRASH_EXPIRY_CHECK, files_router.TRASH_EXPIRY_INTERVAL) == (3600, 86400)
    passes, kept = [], []

    def expire(now, done, clock, asleep):
        passes.append(clock)
        kept.append((done, asleep))
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
    # Each check gets the same memory: the pools' last passes and the disks found asleep.
    assert all(done is kept[0][0] and asleep is kept[0][1] for done, asleep in kept), kept


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
    assert power_reads(host) == ["/dev/sda"] and host.container_calls == []


def test_the_power_state_is_read_with_hdparm_c_on_the_host(monkeypatch):
    # hdparm -C sends the disk CHECK POWER MODE alone and prints its answer as " drive state is:  <state>":
    # it does not wake a sleeping disk, and it did not restart the dev device's disk's standby timer
    # (ADR-0378).
    answers = {
        "/dev/sda": (0, "\n/dev/sda:\n drive state is:  active/idle\n", ""),
        "/dev/sdb": (0, "\n/dev/sdb:\n drive state is:  idle\n", ""),
        "/dev/sdc": (0, "\n/dev/sdc:\n drive state is:  standby\n", ""),
        "/dev/sdd": (0, "\n/dev/sdd:\n drive state is:  sleeping\n", ""),
        "/dev/sde": (0, "\n/dev/sde:\n drive state is:  NVcache_spindown\n", ""),
        "/dev/sdf": (0, "\n/dev/sdf:\n drive state is:  NVcache_spinup\n", ""),
        # The disk gives an answer that hdparm does not name (smartctl -n idle then asks: below), or none.
        "/dev/sdg": (0, "\n/dev/sdg:\n drive state is:  unknown\n", ""),
        "/dev/sdh": (0, "\n/dev/sdh:\n", ""),
        # hdparm could not ask the disk (a USB bridge without ATA pass-through, an I/O error), the disk is
        # gone, or the host's timeout ended hdparm (124) or killed it (-9; 137 through a shell).
        "/dev/sdi": (5, "\n/dev/sdi:\n drive state is:  unknown\n",
                     " HDIO_DRIVE_CMD(checkpowermode) failed: Input/output error\n"),
        "/dev/sdj": (2, "", "/dev/sdj: No such file or directory\n"),
        "/dev/sdk": (124, "", ""),
        "/dev/sdl": (137, "", ""),
    }
    seen = []

    def run_on_host(cmd, **kwargs):
        seen.append((cmd, kwargs["timeout"]))
        if cmd[-1] == "/dev/sdm":  # nsenter did not answer in time
            raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])
        if cmd[-1] == "/dev/sdn":
            raise OSError("nsenter: cannot open /proc/1/ns/mnt: Permission denied")
        return host_module.HostResult(*answers[cmd[-1]])

    asked = []

    def smartctl(cmd, **kwargs):
        # Only a disk that answered a power mode hdparm does not name, with exit status 0, is asked again:
        # here smartctl went on to read its identity, which names no power mode either.
        assert cmd == EPC_READ + ["/dev/sdg"], f"smartctl read the power of a disk that hdparm asked: {cmd}"
        asked.append(cmd[-1])
        return subprocess.CompletedProcess(cmd, 0, '{"model_name": "X"}', "")

    monkeypatch.setattr(host_module, "run_on_host", run_on_host)
    monkeypatch.setattr(host_module, "run_in_container", smartctl)
    assert {c: disks.power_state(f"/dev/sd{c}") for c in "abcdefghijklmn"} == {
        "a": "active", "b": "active", "c": "standby", "d": "standby", "e": "standby", "f": "active",
        "g": "unknown", "h": "unknown", "i": "unknown", "j": "unknown", "k": "unknown", "l": "unknown",
        "m": "unknown", "n": "unknown"}
    assert asked == ["/dev/sdg"]
    # Bounded: the host's timeout sends TERM after 15 seconds and KILL 5 later (ADR-0258); nsenter's own
    # timeout comes after both.
    assert seen[0] == (["timeout", "--kill-after=5", "15", "hdparm", "-C", "/dev/sda"], 25)
    assert (disks.POWER_CHECK_TIMEOUT, disks.POWER_CHECK_KILL_AFTER) == (15, 5)


def test_without_hdparm_the_power_state_is_read_with_smartctl_n_standby(monkeypatch):
    # The host's timeout finds no hdparm (exit status 127): smartctl -n standby -i in the container,
    # which leaves a sleeping disk alone but asks an awake one IDENTIFY, which restarts its timer.
    def run_on_host(cmd, **kwargs):
        assert cmd[3:5] == ["hdparm", "-C"], cmd
        return host_module.HostResult(127, "", "timeout: failed to run command 'hdparm': No such file or directory\n")

    seen = []

    def smartctl(cmd, **kwargs):
        seen.append((cmd, kwargs["timeout"]))
        if cmd[-1] == "/dev/sdf":
            raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])
        out = {"/dev/sda": (2, '{"smartctl": {"messages": [{"string": "Device is in STANDBY mode, exit(2)"}]}}'),
               "/dev/sdb": (2, '{"smartctl": {"messages": [{"string": "Device is in SLEEP mode, exit(2)"}]}}'),
               "/dev/sdc": (0, '{"model_name": "X"}'),
               "/dev/sdd": (2, '{"smartctl": {"messages": [{"string": "Smartctl open device: /dev/sdd failed"}]}}'),
               "/dev/sde": (4, '{"model_name": "X"}'),
               # An EPC disk's standby_y, in the JSON and in the text output.
               "/dev/sdg": (2, '{"smartctl": {"messages": [{"string": "Device is in STANDBY_Y mode, exit(2)"}]}}'),
               "/dev/sdh": (2, "smartctl 7.3\n\nDevice is in STANDBY_Y mode, exit(2)\n"),
               # A SCSI disk's power conditions, as smartctl's scsiprint.cpp names them.
               "/dev/sdi": (2, '{"smartctl": {"messages": [{"string": "Device is in STANDBY BY TIMER mode, exit(2)"}]}}'),
               "/dev/sdj": (2, "smartctl 7.3\n\nDevice is in STANDBY_Y BY COMMAND mode, exit(2)\n"),
               "/dev/sdk": (2, '{"smartctl": {"messages": [{"string": "Device is in LOW POWER mode, exit(2)"}]}}')}[cmd[-1]]
        return subprocess.CompletedProcess(cmd, *out, "")

    monkeypatch.setattr(host_module, "run_on_host", run_on_host)
    monkeypatch.setattr(host_module, "run_in_container", smartctl)
    assert [disks.power_state(f"/dev/sd{c}") for c in "abcdefghijk"] == [
        "standby", "standby", "active", "unknown", "active", "unknown", "standby", "standby", "standby", "standby",
        "standby"]
    assert seen[0] == (IDENTIFY + ["/dev/sda"], disks.POWER_CHECK_TIMEOUT)


def test_an_nvme_disk_s_power_state_is_read_with_smartctl(host):
    # hdparm sends ATA commands, which an NVMe disk does not take: it would always be unknown. An NVMe
    # disk has no standby of ATA's and gets no idle timeout, so smartctl's IDENTIFY keeps nothing awake.
    assert disks.power_state("/dev/nvme0n1") == "active"
    assert power_reads(host) == [] and host.container_calls == [IDENTIFY + ["/dev/nvme0n1"]]
    # What hdparm -C answers for it on the fake host, as on a real one.
    result = host.run(power_read("/dev/nvme0n1"))
    assert (result.returncode, result.stdout.splitlines()[-1]) == (25, " drive state is:  unknown")


# --- a disk that does not answer its power read ---------------------------------------------------


def test_a_disk_that_does_not_answer_keeps_no_more_than_one_hdparm_on_the_host(host, caplog):
    # sdb stopped answering: its hdparm -C hangs in the kernel (SG_IO), where the host's timeout cannot end
    # it, and run_on_host's limit ends only nsenter. While that hdparm still runs on the host, no other is
    # started for sdb, as run_in_container bounds its commands (ADR-0290): sdb is unknown at once, at every
    # load of the Storage page and at the trash expiry's read, and the other disks are read as before.
    host.hung.add("/dev/sdb")
    with admin_client() as api:
        for _ in range(3):
            found = listed(api)
            assert {name: d["power_state"] for name, d in found.items()} == {
                "sda": "active", "sdb": "unknown", "sdc": "active", "nvme0n1": "active"}
    assert disks.power_state("/dev/sdb") == "unknown"
    assert power_reads(host) == ["/dev/sda", "/dev/sdb", "/dev/sdc", "/dev/sda", "/dev/sdc", "/dev/sda", "/dev/sdc"]
    assert host.stuck_hdparm == {"/dev/sdb"}
    assert [call for call in host.container_calls if call[0] == "pgrep"] == [stuck_check("/dev/sdb")] * 3
    timed_out = [r.getMessage() for r in caplog.records if r.name == disks.__name__ and "hdparm" in r.getMessage()]
    assert timed_out == ["hdparm -C /dev/sdb did not answer within 15 seconds",
                         "Not started: hdparm -C /dev/sdb: an earlier run of it has not ended"], timed_out
    # The kernel let the hdparm go: the next read runs one again.
    host.hung.clear()
    host.stuck_hdparm.clear()
    assert disks.power_state("/dev/sdb") == "active"
    assert power_reads(host).count("/dev/sdb") == 2


@pytest.mark.parametrize("status", [124, -9, 137])
def test_a_power_read_that_the_host_s_timeout_ended_runs_again_once_its_hdparm_is_gone(host, status):
    # The host's timeout ended hdparm with TERM (124), or killed it with KILL: timeout sends KILL to its
    # own process group, so it dies by it too, and nsenter passes that on (-9); 137 through a shell.
    # Checked, and gone, so read again.
    host.fail[tuple(power_read("/dev/sdb"))] = (status, "")
    assert disks.power_state("/dev/sdb") == "unknown"
    host.fail.clear()
    assert disks.power_state("/dev/sdb") == "active"
    assert power_reads(host) == ["/dev/sdb", "/dev/sdb"]
    assert host.container_calls == [stuck_check("/dev/sdb")]
    # Answered: no check before the next read.
    assert disks.power_state("/dev/sdb") == "active"
    assert host.container_calls == [stuck_check("/dev/sdb")]


def test_no_hdparm_is_started_while_it_cannot_tell_whether_the_stuck_one_ended(host, monkeypatch):
    host.hung.add("/dev/sdb")
    assert disks.power_state("/dev/sdb") == "unknown"
    host.hung.clear()
    host.stuck_hdparm.clear()
    answer = host.run_in_container
    # pgrep does not answer (or is still stuck itself: run_in_container raises TimeoutExpired at once), fails,
    # or cannot run: the hdparm may still be stuck.
    failures = [subprocess.TimeoutExpired(stuck_check("/dev/sdb"), 5),
                subprocess.CompletedProcess(stuck_check("/dev/sdb"), 3, "", "pgrep: cannot allocate memory\n"),
                OSError("pgrep: No such file or directory")]

    def check(cmd, **kwargs):
        assert cmd == stuck_check("/dev/sdb") and kwargs["timeout"] == disks.POWER_STUCK_CHECK_TIMEOUT, (cmd, kwargs)
        if failures:
            failure = failures.pop(0)
            if isinstance(failure, BaseException):
                raise failure
            return failure
        return answer(cmd, **kwargs)

    monkeypatch.setattr(host_module, "run_in_container", check)
    assert [disks.power_state("/dev/sdb") for _ in range(3)] == ["unknown"] * 3
    assert power_reads(host) == ["/dev/sdb"]
    assert disks.power_state("/dev/sdb") == "active"
    assert power_reads(host) == ["/dev/sdb", "/dev/sdb"]


def test_one_power_read_of_a_disk_runs_at_a_time(monkeypatch):
    # GET /storage/disks and the trash expiry read in worker threads: a second read of a disk waits for the
    # first, and another disk is not held up.
    release = threading.Event()
    started = threading.Event()
    lock = threading.Lock()
    running: dict[str, int] = {}
    most: dict[str, int] = {}
    calls: list[str] = []

    def run_on_host(cmd, **kwargs):
        device = cmd[-1]
        with lock:
            calls.append(device)
            running[device] = running.get(device, 0) + 1
            most[device] = max(most.get(device, 0), running[device])
            first = calls == ["/dev/sda"]
        try:
            if first:
                started.set()
                assert release.wait(10)
            return host_module.HostResult(0, f"\n{device}:\n drive state is:  active/idle\n", "")
        finally:
            with lock:
                running[device] -= 1

    def smartctl(cmd, **kwargs):
        raise AssertionError(f"no read timed out: {cmd}")

    monkeypatch.setattr(host_module, "run_on_host", run_on_host)
    monkeypatch.setattr(host_module, "run_in_container", smartctl)
    with ThreadPoolExecutor(3) as pool:
        first = pool.submit(disks.power_state, "/dev/sda")
        assert started.wait(10)
        second = pool.submit(disks.power_state, "/dev/sda")
        assert pool.submit(disks.power_state, "/dev/sdb").result(10) == "active"
        time.sleep(0.1)
        assert not second.done() and calls == ["/dev/sda", "/dev/sdb"]
        release.set()
        assert (first.result(10), second.result(10)) == ("active", "active")
    assert calls == ["/dev/sda", "/dev/sdb", "/dev/sda"]
    assert most == {"/dev/sda": 1, "/dev/sdb": 1}


def test_a_second_read_waits_for_the_first_at_most_its_limit(monkeypatch):
    release = threading.Event()
    started = threading.Event()
    calls: list[str] = []

    def run_on_host(cmd, **kwargs):
        calls.append(cmd[-1])
        started.set()
        assert release.wait(10)
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    monkeypatch.setattr(host_module, "run_on_host", run_on_host)
    monkeypatch.setattr(disks, "POWER_READ_LIMIT", 0.2)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(disks.power_state, "/dev/sda")
        assert started.wait(10)
        began = time.monotonic()
        assert disks.power_state("/dev/sda") == "unknown"
        assert 0.15 < time.monotonic() - began < 5
        release.set()
        assert first.result(10) == "unknown"
    assert calls == ["/dev/sda"]


def test_hdparm_is_an_optional_host_tool():
    assert host_module.OPTIONAL_HOST_TOOLS["hdparm"] == "hdparm"
    assert "hdparm" not in host_module.HOST_TOOLS

