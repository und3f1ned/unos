"""SMART endpoints of the health and storage APIs: whole disks of the host only.

GET /health/disk/{device} reads SMART data and POST /health/disk/{device}/test
starts a self-test. Both used to check only for ".." and that the path exists,
so they ran smartctl on partitions, the boot SD card, /dev/disk/by-id links
and /dev/null. They now accept what the scheduler accepts for SMART tasks
(test_scheduler_validation.py): a whole disk of the host, as ``sda`` or
``/dev/sda``. GET /storage/disks/{name}/smart, which only checked the name's
format, accepts the same disks by name. GET /health/disks, GET /health/overview
and the SMART monitor cover the same disks. The host's block devices come from
``FakeHost`` (lsblk in the host namespaces); smartctl runs in the container
and is recorded.
"""

import asyncio
import importlib
import json
import logging
import subprocess
from pathlib import Path
from urllib.parse import quote

import pytest

from .test_pool_persistence import ROOT_UUID, admin_client, install_fake_host
from .webui_catalogs import REPO, key_map, read_catalog

health_router = importlib.import_module("src.health.router")

SMART_JSON = json.dumps({
    "model_name": "WDC WD40EFRX",
    "serial_number": "WD-1",
    "smart_status": {"passed": True},
    "temperature": {"current": 34},
})


def _on_event_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


@pytest.fixture
def host(monkeypatch):
    """Data disks sda (with partition sda1), sdc (under the RAID array md0), nvme0n1 and loop0.

    FakeHost also has the boot SD card mmcblk1 and the swap device zram0.
    ``host.smartctl`` records the smartctl commands; ``host.lsblk_on_loop``
    whether each lsblk ran on the event loop. Every path below /dev exists,
    as on the device: the old check (Path.exists) accepted all of them.
    """
    fake = install_fake_host(monkeypatch)
    fake.add_disk("sda", serial="WD-1")
    fake.add_partition("sda", "sda1", fstype="ext4")
    fake.add_disk("sdc", serial="WD-3")
    fake.add_holder("sdc", "md0", "raid1")
    fake.add_disk("nvme0n1", rota=False, serial="NV-1")
    fake.add_disk("loop0", size=1_000_000)["type"] = "loop"
    fake.smartctl = []
    fake.lsblk_on_loop = []

    def smartctl(cmd, **kwargs):
        assert cmd[0] == "smartctl", cmd
        assert kwargs.get("timeout"), cmd
        fake.smartctl.append(cmd)
        if cmd[1] == "-t":
            return subprocess.CompletedProcess(cmd, 0, "Testing has begun.\n", "")
        return subprocess.CompletedProcess(cmd, 0, SMART_JSON, "")

    host_run = fake.run

    def run_on_host(cmd, **kwargs):
        if cmd[0] == "lsblk":
            fake.lsblk_on_loop.append(_on_event_loop())
        return host_run(cmd, **kwargs)

    monkeypatch.setattr("src.host.run_in_container", smartctl)
    monkeypatch.setattr("src.host.run_on_host", run_on_host)
    real_exists = Path.exists
    monkeypatch.setattr(Path, "exists", lambda self: str(self).startswith("/dev/") or real_exists(self))
    return fake


@pytest.fixture
def api(host):
    with admin_client() as client:
        yield client


def read_smart(api, device):
    return api.get(f"/api/v1/health/disk/{device}")


def start_test(api, device):
    return api.post(f"/api/v1/health/disk/{device}/test?test_type=short")


# A read leaves a sleeping disk asleep (-n standby, ADR-0378); a self-test is asked for.
SMARTCTL = {read_smart: ["smartctl", "-n", "standby", "-j", "-a"], start_test: ["smartctl", "-t", "short"]}
ENDPOINTS = pytest.mark.parametrize("call", [read_smart, start_test], ids=["data", "test"])


# webui/src/lib/api/health.ts sends the device URL-encoded (encodeURIComponent).
@ENDPOINTS
@pytest.mark.parametrize(
    "device,path",
    [("sda", "/dev/sda"), ("/dev/sda", "/dev/sda"), ("%2Fdev%2Fsda", "/dev/sda"),
     ("nvme0n1", "/dev/nvme0n1")],
)
def test_whole_disks_of_the_host_are_accepted(api, host, call, device, path):
    response = call(api, device)

    assert response.status_code == 200, response.text
    assert host.smartctl == [SMARTCTL[call] + [path]]
    # The check runs on the host, in the endpoint's worker thread.
    assert host.lsblk_on_loop == [False]
    if call is read_smart:
        assert response.json()["device"] == path
        assert response.json()["serial"] == "WD-1"


@ENDPOINTS
@pytest.mark.parametrize(
    "device",
    [
        "sda1", "%2Fdev%2Fsda1",  # a partition
        "%2Fdev%2Fnull",  # exists, but is no disk
        "sdz",  # no such device
        "zram0", "loop0",  # virtual devices
        "md0",  # a RAID array built on sdc
        "nvme0",  # the NVMe controller, not a block device
        # The boot SD card and its root partition: a card is a disk only while it holds no part of
        # the system, which lsblk and the mount table tell (storage.free-mmc-as-data-disk).
        "mmcblk1", "mmcblk1p1",
    ],
)
def test_other_devices_are_not_a_disk_of_this_system(api, host, call, device):
    response = call(api, device)

    assert response.status_code == 400, response.text
    assert "is not a disk of this system" in response.json()["detail"]
    assert host.smartctl == []


@ENDPOINTS
@pytest.mark.parametrize(
    "device",
    [
        "mmcblk0boot0", "mmcblk0rpmb",  # an eMMC's boot and RPMB areas, never disks (storage.free-mmc-as-data-disk)
        "%2Fdev%2Fdisk%2Fby-id%2Fata-WD-1",  # a link to sda
        "%2Fdev%2F..%2Fsda", "..%2Fsda", "dev%2Fsda", "sda%3B%20reboot",
    ],
)
def test_invalid_device_names_are_refused(api, host, call, device):
    response = call(api, device)

    assert response.status_code == 400, response.text
    assert host.smartctl == []
    assert host.lsblk_on_loop == []


@ENDPOINTS
def test_nothing_runs_when_the_host_disks_cannot_be_listed(api, host, call):
    host.fail[("lsblk",)] = (1, "lsblk: failed to access sysfs directory: /sys/dev/block")

    response = call(api, "sda")

    assert response.status_code == 500, response.text
    assert "Could not list the host's disks" in response.json()["detail"]
    assert host.smartctl == []


def test_invalid_test_type_is_refused(api, host):
    response = api.post("/api/v1/health/disk/sda/test?test_type=destroy")

    assert response.status_code == 400, response.text
    assert host.smartctl == []


# ---------------------------------------------------------------------------
# GET /storage/disks/{name}/smart: a disk as GET /storage/disks lists it
# ---------------------------------------------------------------------------


def storage_smart(api, name):
    return api.get(f"/api/v1/storage/disks/{name}/smart")


def test_storage_smart_for_every_disk_the_storage_page_lists(api, host):
    # webui/src/lib/api/storage.ts getDiskSmart() sends the names of GET /storage/disks.
    names = [disk["name"] for disk in api.get("/api/v1/storage/disks").json()]
    assert names == ["sda", "sdc", "nvme0n1"]
    # The list reads each disk's power state without waking it (ADR-0378): hdparm -C on the host for
    # a SATA disk, smartctl -n standby -i for the NVMe disk, which hdparm cannot ask.
    assert [call[-1] for call in host.calls if call[3:5] == ["hdparm", "-C"]] == ["/dev/sda", "/dev/sdc"]
    assert host.smartctl == [["smartctl", "-n", "standby", "-i", "-j", "/dev/nvme0n1"]]
    host.smartctl.clear()
    host.lsblk_on_loop.clear()

    for name in names:
        response = storage_smart(api, name)
        assert response.status_code == 200, response.text
        assert response.json()["device"] == f"/dev/{name}"
        assert response.json()["healthy"] is True

    assert host.smartctl == [["smartctl", "-n", "standby", "-j", "-a", f"/dev/{name}"] for name in names]
    # The check runs on the host, in a worker thread.
    assert host.lsblk_on_loop == [False] * len(names)


# Names of the right format that are no disk of the host: smartctl used to run
# on them (a tape drive such as st0 rewinds when the device is closed).
@pytest.mark.parametrize(
    "name", ["sda1", "md0", "zram0", "loop0", "null", "port", "st0", "sdz", "nvme0", "mmcblk1", "mmcblk1p1"]
)
def test_storage_smart_of_other_devices_is_refused(api, host, name):
    response = storage_smart(api, name)

    assert response.status_code == 400, response.text
    assert response.json()["detail"] == (
        f"/dev/{name} is not a disk of this system. SMART works on whole disks as the Storage "
        "page lists them, not on partitions, RAID arrays, the boot SD card or eMMC, or "
        "virtual devices"
    )
    assert host.smartctl == []


@pytest.mark.parametrize("name", ["mmcblk0boot0", "mmcblk0rpmb", "sda%3B%20reboot", "sd..a", "SDA"])
def test_storage_smart_of_invalid_names_is_refused(api, host, name):
    response = storage_smart(api, name)

    assert response.status_code == 400, response.text
    assert host.smartctl == []
    assert host.lsblk_on_loop == []


# Whole disks whose names have more letters than four: the 703rd SCSI disk, the 27th Xen disk,
# an s390 DASD (storage.edge-cases, step 5). The Storage and Health pages list them; a request
# that named them answered 400 "Invalid disk name format".
LONG_DISK_NAMES = ["sdaaa", "xvdaa", "dasda", "dasdaa"]


@pytest.mark.parametrize("name", LONG_DISK_NAMES)
def test_whole_disks_with_longer_names_answer_wherever_they_are_listed(api, host, name):
    host.add_disk(name, serial="LONG-1")

    assert name in [disk["name"] for disk in api.get("/api/v1/storage/disks").json()]
    assert f"/dev/{name}" in [disk["device"] for disk in api.get("/api/v1/health/disks").json()["disks"]]
    host.smartctl.clear()

    for response in (storage_smart(api, name), read_smart(api, name), read_smart(api, f"%2Fdev%2F{name}"),
                     start_test(api, name)):
        assert response.status_code == 200, response.text
    assert host.smartctl == [
        ["smartctl", "-n", "standby", "-j", "-a", f"/dev/{name}"],
        ["smartctl", "-n", "standby", "-j", "-a", f"/dev/{name}"],
        ["smartctl", "-n", "standby", "-j", "-a", f"/dev/{name}"],
        ["smartctl", "-t", "short", f"/dev/{name}"],
    ]


@pytest.mark.parametrize("name", ["sdaaa1", "xvdaa1", "dasda1"])
def test_partitions_of_disks_with_longer_names_are_still_no_disk_of_this_system(api, host, name):
    host.add_disk(name[:-1])
    host.add_partition(name[:-1], name, fstype="ext4")

    response = storage_smart(api, name)

    assert response.status_code == 400, response.text
    assert "is not a disk of this system" in response.json()["detail"]
    assert host.smartctl == []


def test_storage_smart_runs_nothing_when_the_host_disks_cannot_be_listed(api, host):
    host.fail[("lsblk",)] = (1, "lsblk: failed to access sysfs directory: /sys/dev/block")

    response = storage_smart(api, "sda")

    assert response.status_code == 500, response.text
    assert "Could not list the host's disks" in response.json()["detail"]
    assert host.smartctl == []


# ---------------------------------------------------------------------------
# GET /health/disks, GET /health/overview and the SMART monitor: the same disks
# ---------------------------------------------------------------------------

LSBLK_FAILS = (1, "lsblk: failed to access sysfs directory: /sys/dev/block")
NO_DISKS = "Could not list the host's disks: lsblk failed: " + LSBLK_FAILS[1]

BOARD_SMART = {
    "/dev/sda": {
        "model_name": "WDC WD40EFRX-68N32N0",
        "serial_number": "WD-1",
        "user_capacity": {"bytes": 4_000_787_030_016},
        "smart_status": {"passed": True},
        "temperature": {"current": 34},
    },
    "/dev/nvme0n1": {
        "model_name": "Samsung SSD 980 1TB",
        "serial_number": "S64ANS0T",
        "user_capacity": {"bytes": 1_000_204_886_016},
        "smart_status": {"passed": True},
        "nvme_smart_health_information_log": {"temperature": 40, "power_on_hours": 1200},
    },
}


@pytest.fixture
def board(monkeypatch, tmp_path):
    """A host like the ArmSoM Sige7: a SATA disk sda and a data NVMe disk nvme0n1 (M.2 slot).

    It boots from its eMMC mmcblk0 (root filesystem on mmcblk0p1), keeps its
    bootloader in the SPI flash mtdblock0 and swaps to zram0. ``board.smart``
    holds what smartctl prints per device (a dict as JSON, a str as it is,
    None: no answer in time); ``board.smartctl`` records the smartctl
    commands, which run in the container; ``board.lsblk_on_loop`` whether
    each lsblk on the host ran on the event loop. lsblk in the container,
    where the health code used to list the disks, sees the same block
    devices and is recorded in ``board.container_lsblk``. The SMART history
    goes to a file of this test.
    """
    fake = install_fake_host(monkeypatch)
    fake.tree.clear()
    fake.mounts.clear()
    fake.swaps.clear()
    fake.add_disk("mmcblk0", size=62_537_072_640, rota=False, pttype="gpt")
    fake.add_partition("mmcblk0", "mmcblk0p1", size=62_000_000_000, fstype="ext4", uuid=ROOT_UUID)
    fake.add_mount("/", "/dev/mmcblk0p1", "ext4", ROOT_UUID)
    fake.add_disk("mtdblock0", size=16_777_216, rota=False)
    fake.add_disk("zram0", size=4_000_000_000, rota=False)
    fake.swaps.append("/dev/zram0")
    fake.add_disk("sda", model="WDC WD40EFRX-68N32N0", serial="WD-1")
    fake.add_disk("nvme0n1", size=1_000_204_886_016, rota=False, model="Samsung SSD 980 1TB",
                  serial="S64ANS0T")
    fake.smart = dict(BOARD_SMART)
    fake.smartctl = []
    fake.container_lsblk = []
    fake.lsblk_on_loop = []
    fake.history = tmp_path / "smart_history.json"
    monkeypatch.setattr(health_router, "SMART_HISTORY_FILE", str(fake.history))

    def run(cmd, **kwargs):
        if cmd[0] == "lsblk":
            fake.container_lsblk.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, fake._lsblk(), "")
        assert cmd[0] == "smartctl", cmd
        assert kwargs.get("timeout"), cmd
        fake.smartctl.append(cmd)
        if cmd[-1] not in fake.smart:  # cannot be opened: exit status bit 1
            return subprocess.CompletedProcess(cmd, 2, '{"smartctl": {"exit_status": 2}}', "")
        output = fake.smart[cmd[-1]]
        if output is None:
            raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])
        if not isinstance(output, str):
            output = json.dumps(output)
        return subprocess.CompletedProcess(cmd, 0, output, "")

    host_run = fake.run

    def run_on_host(cmd, **kwargs):
        if cmd[0] == "lsblk":
            fake.lsblk_on_loop.append(_on_event_loop())
        return host_run(cmd, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr("src.host.run_in_container", run)
    monkeypatch.setattr("src.host.run_on_host", run_on_host)
    return fake


@pytest.fixture
def board_api(board):
    with admin_client() as client:
        yield client


def disk_health(api) -> list[dict]:
    response = api.get("/api/v1/health/disks")
    assert response.status_code == 200, response.text
    return response.json()["disks"]


def smartctl_reads(*paths: str) -> list[list[str]]:
    return [["smartctl", "-n", "standby", "-j", "-a", path] for path in paths]


def test_disk_health_lists_the_whole_disks_of_the_host(board_api, board):
    # Before, lsblk ran in the container and a list of names was left out:
    # every nvme0n1 (here a data disk), but not the SPI flash mtdblock0.
    disks = disk_health(board_api)

    assert [(d["device"], d["model"], d["serial"], d["capacity"]) for d in disks] == [
        ("/dev/sda", "WDC WD40EFRX-68N32N0", "WD-1", "3.64 TB"),
        ("/dev/nvme0n1", "Samsung SSD 980 1TB", "S64ANS0T", "931.51 GB"),
    ]
    assert [d["health_status"] for d in disks] == ["PASSED", "PASSED"]
    assert [d["temperature"] for d in disks] == [34, 40]
    assert board.smartctl == smartctl_reads("/dev/sda", "/dev/nvme0n1")
    # On the host, from the endpoint's worker thread.
    assert board.lsblk_on_loop == [False]
    assert board.container_lsblk == []


def test_disk_health_lists_the_disks_of_the_storage_page(board_api, board):
    storage = board_api.get("/api/v1/storage/disks").json()

    assert [d["device"] for d in disk_health(board_api)] == [d["path"] for d in storage]


def test_every_listed_disk_answers_on_the_disk_endpoint(board_api, board):
    devices = [d["device"] for d in disk_health(board_api)]
    assert devices
    board.smartctl.clear()

    # webui/src/lib/api/health.ts getDiskSmart(): the device, URL-encoded; or the name.
    for device in devices:
        for given in (quote(device, safe=""), device.removeprefix("/dev/")):
            response = board_api.get(f"/api/v1/health/disk/{given}")
            assert response.status_code == 200, (given, response.text)
            assert response.json()["device"] == device

    assert board.smartctl == smartctl_reads(*[device for device in devices for _ in range(2)])


def test_the_system_disk_is_listed(board_api, board):
    # Booting from the NVMe disk (the SPI flash holds the bootloader): its health matters most.
    board.mounts.clear()
    board.node("mmcblk0p1")["uuid"] = None
    board.add_partition("nvme0n1", "nvme0n1p1", size=536_870_912, fstype="vfat")
    board.add_partition("nvme0n1", "nvme0n1p2", size=999_000_000_000, fstype="ext4", uuid=ROOT_UUID)
    board.add_mount("/", "/dev/nvme0n1p2", "ext4", ROOT_UUID)
    board.add_mount("/boot/efi", "/dev/nvme0n1p1", "vfat")
    storage = board_api.get("/api/v1/storage/disks").json()
    assert [d["name"] for d in storage if d["is_system"]] == ["nvme0n1"]

    disks = disk_health(board_api)

    # The eMMC no longer holds the system: a data disk, which has no SMART (storage.free-mmc-as-data-disk).
    assert [d["device"] for d in disks] == ["/dev/mmcblk0", "/dev/sda", "/dev/nvme0n1"]
    assert disks[0]["health_status"] == "NO_SMART"
    assert disks[2]["serial"] == "S64ANS0T"


def test_a_disk_without_smart_data_is_listed_with_what_lsblk_reports(board_api, board):
    board.smart["/dev/nvme0n1"] = None  # smartctl does not answer in time
    board.node("sda")["model"] = None  # lsblk reports no model
    board.smart["/dev/sda"] = "smartctl 7.3: no JSON output\n"

    disks = disk_health(board_api)

    assert [(d["device"], d["model"], d["capacity"], d["health_status"]) for d in disks] == [
        ("/dev/sda", "Unknown", "3.64 TB", "UNKNOWN"),
        ("/dev/nvme0n1", "Samsung SSD 980 1TB", "931.51 GB", "UNKNOWN"),
    ]
    assert disks[1] == {
        "device": "/dev/nvme0n1", "model": "Samsung SSD 980 1TB", "serial": "Unknown",
        "firmware": "Unknown", "capacity": "931.51 GB", "temperature": None,
        "power_on_hours": None, "power_cycle_count": None, "health_status": "UNKNOWN",
        "attributes": [], "nvme_health": None, "self_test_status": None,
        "last_test_result": None,
    }


def test_a_disk_smartctl_cannot_open_is_listed_with_what_lsblk_reports(board_api, board):
    # smartctl -j prints JSON without model and capacity when it cannot open
    # or identify a disk (exit status 2 here), such as one behind a USB bridge
    # it does not know. The page showed "Unknown" for both.
    del board.smart["/dev/nvme0n1"]

    disks = disk_health(board_api)

    assert [(d["device"], d["model"], d["capacity"], d["health_status"]) for d in disks] == [
        ("/dev/sda", "WDC WD40EFRX-68N32N0", "3.64 TB", "PASSED"),
        ("/dev/nvme0n1", "Samsung SSD 980 1TB", "931.51 GB", "UNKNOWN"),
    ]
    assert board.smartctl == smartctl_reads("/dev/sda", "/dev/nvme0n1")


# What a disk's dialog in Disk Health shows (webui/src/lib/components/system/health/SmartDataDialog.svelte):
# the data of GET /health/disks, so that opening it runs no smartctl.
ATA_ATTRIBUTES = [
    # Within 10 of its threshold: a warning.
    {"id": 1, "name": "Raw_Read_Error_Rate", "value": 55, "worst": 50, "thresh": 51,
     "raw": {"value": 1234, "string": "1234"}},
    # At its threshold: failing.
    {"id": 3, "name": "Spin_Up_Time", "value": 21, "worst": 21, "thresh": 21, "raw": {"value": 9000}},
    {"id": 5, "name": "Reallocated_Sector_Ct", "value": 100, "worst": 100, "thresh": 10,
     "raw": {"value": 3}},
    {"id": 9, "name": "Power_On_Hours", "value": 30, "worst": 30, "thresh": 0, "raw": {"value": 51775}},
    {"id": 12, "name": "Power_Cycle_Count", "value": 100, "worst": 100, "thresh": 0,
     "raw": {"value": 120}},
    {"id": 194, "name": "Temperature_Celsius", "value": 116, "worst": 100, "thresh": 0,
     "raw": {"value": 34, "string": "34 (Min/Max 20/45)"}},
]

NVME_LOG = {
    "critical_warning": 0, "temperature": 40, "available_spare": 100,
    "available_spare_threshold": 10, "percentage_used": 2, "data_units_read": 1_234_567,
    "data_units_written": 2_345_678, "host_reads": 34_567_890, "host_writes": 45_678_901,
    "controller_busy_time": 123, "power_cycles": 45, "power_on_hours": 1200,
    "unsafe_shutdowns": 12, "media_errors": 0, "num_err_log_entries": 0,
    "warning_temp_time": 0, "critical_comp_time": 0, "temperature_sensors": [40, 45],
}


def test_a_sata_disk_reports_its_attributes_and_last_self_test(board_api, board):
    board.smart["/dev/sda"] = {
        **BOARD_SMART["/dev/sda"],
        "firmware_version": "82.00A82",
        "ata_smart_attributes": {"revision": 16, "table": ATA_ATTRIBUTES},
        "ata_smart_self_test_log": {"standard": {"table": [
            {"type": {"string": "Short offline"},
             "status": {"string": "Completed without error", "passed": True}},
            {"type": {"string": "Extended offline"},
             "status": {"string": "Aborted by host", "passed": False}},
        ]}},
    }

    sata, _nvme = disk_health(board_api)

    assert sata["attributes"] == [
        {"id": 1, "name": "Raw_Read_Error_Rate", "value": 55, "worst": 50, "thresh": 51,
         "raw_value": "1234", "status": "warning"},
        {"id": 3, "name": "Spin_Up_Time", "value": 21, "worst": 21, "thresh": 21,
         "raw_value": "9000", "status": "critical"},
        {"id": 5, "name": "Reallocated_Sector_Ct", "value": 100, "worst": 100, "thresh": 10,
         "raw_value": "3", "status": "ok"},
        {"id": 9, "name": "Power_On_Hours", "value": 30, "worst": 30, "thresh": 0,
         "raw_value": "51775", "status": "ok"},
        {"id": 12, "name": "Power_Cycle_Count", "value": 100, "worst": 100, "thresh": 0,
         "raw_value": "120", "status": "ok"},
        {"id": 194, "name": "Temperature_Celsius", "value": 116, "worst": 100, "thresh": 0,
         "raw_value": "34", "status": "ok"},
    ]
    assert (sata["health_status"], sata["temperature"], sata["power_on_hours"],
            sata["power_cycle_count"], sata["firmware"]) == ("PASSED", 34, 51775, 120, "82.00A82")
    # The newest self-test comes first in smartctl's log.
    assert (sata["self_test_status"], sata["last_test_result"]) == (
        "Short offline", "Completed without error")
    assert sata["nvme_health"] is None
    assert board.smartctl == smartctl_reads("/dev/sda", "/dev/nvme0n1")


def test_an_nvme_disk_reports_its_health_information_log(board_api, board):
    # NVMe disks have no ATA attributes: their SMART data is the SMART/Health
    # Information log, which Disk Health showed nothing of. smartctl 7.3 and
    # later print their self-test log too, the newest test first.
    board.smart["/dev/nvme0n1"] = {
        **BOARD_SMART["/dev/nvme0n1"], "nvme_smart_health_information_log": NVME_LOG,
        "nvme_self_test_log": {
            "current_self_test_operation": {"value": 0, "string": "No self-test in progress"},
            "table": [
                {"self_test_code": {"value": 1, "string": "Short"},
                 "self_test_result": {"value": 0, "string": "Completed without error"},
                 "power_on_hours": 1190},
                {"self_test_code": {"value": 2, "string": "Extended"},
                 "self_test_result": {"value": 1, "string": "Aborted: Self-test command"},
                 "power_on_hours": 1100},
            ],
        },
    }
    numbers = {key: value for key, value in NVME_LOG.items() if key != "temperature_sensors"}

    sata, nvme = disk_health(board_api)

    assert nvme["attributes"] == []
    assert nvme["nvme_health"] == numbers
    assert list(nvme["nvme_health"]) == list(numbers)  # in smartctl's order
    assert (nvme["health_status"], nvme["temperature"], nvme["power_on_hours"],
            nvme["power_cycle_count"]) == ("PASSED", 40, 1200, 45)
    assert (nvme["self_test_status"], nvme["last_test_result"]) == (
        "Short", "Completed without error")
    assert sata["nvme_health"] is None
    assert (sata["self_test_status"], sata["last_test_result"]) == (None, None)
    single = board_api.get("/api/v1/health/disk/nvme0n1")
    assert single.status_code == 200, single.text
    assert single.json()["nvme_health"] == numbers


def test_overview_counts_the_same_disks(board_api, board):
    board.smart["/dev/sda"] = {**BOARD_SMART["/dev/sda"], "smart_status": {"passed": False}}

    response = board_api.get("/api/v1/health/overview")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "total_disks": 2, "healthy": 1, "warning": 0, "failed": 1, "unknown": 0, "standby": 0, "no_smart": 0,
        "average_temperature": 37,
    }
    assert board.smartctl == smartctl_reads("/dev/sda", "/dev/nvme0n1")
    assert board.lsblk_on_loop == [False]
    assert board.container_lsblk == []


def test_overview_counts_a_disk_without_smart_data_as_unknown(board_api, board):
    board.smart["/dev/nvme0n1"] = None

    response = board_api.get("/api/v1/health/overview")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "total_disks": 2, "healthy": 1, "warning": 0, "failed": 0, "unknown": 1, "standby": 0, "no_smart": 0,
        "average_temperature": 34,
    }
    assert board.smartctl == smartctl_reads("/dev/sda", "/dev/nvme0n1")


@pytest.mark.parametrize("url", ["/api/v1/health/disks", "/api/v1/health/overview"])
def test_disks_that_cannot_be_listed_are_an_error(board_api, board, url):
    board.fail[("lsblk",)] = LSBLK_FAILS

    response = board_api.get(url)

    # The answer of GET /health/disk/{device} and GET /storage/disks/{name}/smart.
    assert response.status_code == 500, response.text
    assert response.json()["detail"] == NO_DISKS
    assert board_api.get("/api/v1/health/disk/sda").json()["detail"] == NO_DISKS
    assert board.smartctl == []
    assert board.container_lsblk == []


def snapshots(*paths: str) -> list[list[str]]:
    return [["smartctl", "-n", "standby", "-j", "-a", path] for path in paths]


def test_the_monitor_checks_the_same_disks(board):
    assert health_router._smart_monitor_pass() == []

    assert board.smartctl == snapshots("/dev/sda", "/dev/nvme0n1")
    history = health_router.load_smart_history()
    assert {serial: [entry["device"] for entry in entries] for serial, entries in history.items()} == {
        "WD-1": ["/dev/sda"], "S64ANS0T": ["/dev/nvme0n1"],
    }
    assert board.container_lsblk == []


async def test_the_monitor_lists_the_disks_in_its_worker_thread(board, monkeypatch):
    async def stop_after_one_round(_seconds):
        health_router._smart_monitor_running = False

    monkeypatch.setattr(health_router.asyncio, "sleep", stop_after_one_round)
    monkeypatch.setattr(health_router, "_smart_monitor_running", True)
    await health_router.smart_monitor_loop()

    assert board.lsblk_on_loop == [False]
    assert board.smartctl == snapshots("/dev/sda", "/dev/nvme0n1")


def test_the_monitor_skips_a_pass_when_the_disks_cannot_be_listed(board, caplog):
    board.fail[("lsblk",)] = LSBLK_FAILS

    with caplog.at_level(logging.WARNING, logger="src.health.router"):
        assert health_router._smart_monitor_pass() == []

    assert board.smartctl == []
    assert not board.history.exists()
    assert "SMART monitor: pass skipped, could not list the host's disks" in caplog.text
    assert LSBLK_FAILS[1] in caplog.text
    assert board.container_lsblk == []


# CP850 "MÜSIC": Windows writes the label of a FAT filesystem in its code page.
NOT_UTF8_LABEL = b"M\x9aSIC".decode("utf-8", "surrogateescape")


def test_a_label_that_is_not_utf8_hides_no_disk(board_api, board):
    # A USB stick: lsblk -J prints its label as it is. Read as strict UTF-8,
    # the host's disks could not be listed while it was plugged in: Disk
    # Health and the overview answered 500, GET /storage/disks 400, and the
    # SMART monitor skipped every pass.
    board.add_disk("sdb", size=16_008_609_792, model="USB Flash Disk", serial="U1")
    board.add_partition("sdb", "sdb1", size=16_000_000_000, fstype="vfat", label=NOT_UTF8_LABEL)

    assert [d["device"] for d in disk_health(board_api)] == ["/dev/sda", "/dev/nvme0n1", "/dev/sdb"]
    assert board_api.get("/api/v1/health/overview").json()["total_disks"] == 3
    assert board_api.get("/api/v1/health/disk/sdb").status_code == 200
    storage = board_api.get("/api/v1/storage/disks")
    assert storage.status_code == 200, storage.text
    [stick] = [d for d in storage.json() if d["name"] == "sdb"]
    assert stick["partitions"][0]["label"] == "M\ufffdSIC"
    board.smartctl.clear()

    assert health_router._smart_monitor_pass() == []

    assert board.smartctl == snapshots("/dev/sda", "/dev/nvme0n1", "/dev/sdb")


# The units of a size that the web UI writes, by the names the server gives them, are in the one
# home of its numbers (webui.locale-formats).
FORMAT_TS = REPO / "webui" / "src" / "lib" / "format.ts"


@pytest.mark.parametrize("size", [1, 512 * 1024**2, 1_000_204_886_016, 1024**4 - 1, 1024**4, 4_000_787_030_016,
                                  5000 * 1024**4])
def test_disk_health_shows_each_capacity_the_server_writes_in_the_page_s_units(size):
    """Disk Health writes a disk's capacity, which the server answers as "3.64 TB" (_format_capacity),
    in the language of the page: SIZE_UNITS of webui/src/lib/format.ts maps the unit to a key of the
    catalog `format`, and in English the text of that key with the number is the server's text word
    for word (ADR-0287)."""
    capacity = health_router._format_capacity(size)
    number, unit = capacity.split(" ")
    assert number.replace(".", "", 1).isdigit()  # the number form that the tab reads
    units = key_map(FORMAT_TS, "SIZE_UNITS")
    assert unit in units, capacity

    catalog = read_catalog("format")  # skips without node
    assert catalog["en"][units[unit]].replace("{size}", number) == capacity
    assert "{size}" in catalog["ru"][units[unit]]
