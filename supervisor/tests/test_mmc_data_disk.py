"""storage.free-mmc-as-data-disk: an SD card or eMMC that holds no part of the running system is a data disk.

A whole ``mmcblk<N>`` device is listed and offered for pools like a SATA or USB disk when nothing
on it is mounted (but a pool at its mountpoint), used as swap, or the disk the host booted from
(the root's, /boot's, the disk that root= or ubootpart= of the kernel's command line names); an
eMMC's boot and RPMB areas (mmcblk0boot0, mmcblk0rpmb) and general purpose partitions are never
disks. Every other SD card or eMMC stays hidden, as before. Such a disk has no SMART and no
spin-down: it is listed without SMART data, no smartctl or hdparm runs on it, and its model,
serial and type (SD or MMC) come from the host's sysfs.

All host commands go to the in-memory FakeHost of test_pool_persistence.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src import host as host_module
from src.security.validators import validate_device_path, validate_disk_target
from src.storage import disks, pools

from .test_disk_safety import bpi_w3, by_name, create, create_forced
from .test_disk_safety import fake_host  # noqa: F401 - the fixture
from .test_pool_persistence import ROOT_UUID, admin_client

health_router = importlib.import_module("src.health.router")
scheduler_router = importlib.import_module("src.scheduler.router")

# The W3's eMMC since 2026-11-23: the root filesystem on its only partition, which U-Boot (in the
# eMMC's first sectors) names in ubootpart= of the kernel's command line, as Armbian's boot script
# does. 116.5 GiB.
EMMC_SIZE = 125_069_950_976
EMMC_PARTUUID = "5f3a2c1e-8b4d-4e6f-9a7b-1c2d3e4f5a6b"
# The SanDisk SD card that was wiped and left in the slot: 29.7 GiB.
SD_SIZE = 31_914_983_424
SD_UUID = "2b6c9e41-7d3a-4f58-a1c2-3e4d5f6a7b8c"
SD_PARTUUID = "0c1d2e3f-01"


def sysfs(monkeypatch, tmp_path: Path, cards: dict[str, tuple[str, str, str]]) -> Path:
    """The host's /sys/block with ``cards`` (name -> type, name, serial of the card, as the mmc
    driver writes them in device/type, device/name and device/serial)."""
    root = tmp_path / "sys-block"
    for name, attributes in cards.items():
        device = root / name / "device"
        device.mkdir(parents=True)
        for attribute, value in zip(("type", "name", "serial"), attributes):
            (device / attribute).write_text(f"{value}\n")
    monkeypatch.setattr(host_module, "SYS_BLOCK", str(root))
    return root


def w3_now(fake, monkeypatch, tmp_path):
    """The BPI-W3 since its system moved to the eMMC: mmcblk0 holds the root filesystem and U-Boot
    (with its boot areas mmcblk0boot0 and mmcblk0boot1), the SD card mmcblk1 is blank, swap on zram0,
    /var/log on zram1, and a SATA disk sda."""
    fake.tree.clear()
    fake.mounts.clear()
    fake.swaps.clear()
    fake.add_disk("mmcblk0", size=EMMC_SIZE, rota=False, pttype="gpt")
    fake.add_partition("mmcblk0", "mmcblk0p1", size=EMMC_SIZE - 16_777_216, fstype="ext4", uuid=ROOT_UUID,
                       partuuid=EMMC_PARTUUID)
    fake.add_mount("/", "/dev/mmcblk0p1", "ext4", ROOT_UUID)
    fake.files["/proc/cmdline"] = (
        f"root=UUID={ROOT_UUID} rootwait rootfstype=ext4 console=ttyS2,1500000 consoleblank=0 "
        f"loglevel=1 ubootpart={EMMC_PARTUUID} usb-storage.quirks=0x2537:0x1066:u,0x2537:0x1068:u"
    )
    fake.add_disk("mmcblk0boot0", size=4_194_304, rota=False)
    fake.add_disk("mmcblk0boot1", size=4_194_304, rota=False)
    # lsblk names the card by udev's ID_SERIAL (<name>_<serial>) and knows no model of it.
    fake.add_disk("mmcblk1", size=SD_SIZE, rota=False, serial="SC32G_0x8f3a1b2c")
    fake.add_disk("zram0", size=4_000_000_000, rota=False)
    fake.swaps.append("/dev/zram0")
    fake.add_disk("zram1", size=52_428_800, rota=False, fstype="ext4")
    fake.add_mount("/var/log", "/dev/zram1", "ext4")
    fake.add_disk("sda", size=640_135_028_736, model="WDC WD6400AACS-00G8B0", serial="WD-W3")
    sysfs(monkeypatch, tmp_path, {
        "mmcblk0": ("MMC", "A3A444", "0x2c7e19a5"),
        "mmcblk0boot0": ("MMC", "A3A444", "0x2c7e19a5"),
        "mmcblk0boot1": ("MMC", "A3A444", "0x2c7e19a5"),
        "mmcblk1": ("SD", "SC32G", "0x8f3a1b2c"),
    })
    return fake


def w3_before(fake):
    """The BPI-W3 before 2026-11-23 (ADR-0470): the system on the fake host's microSD card mmcblk1,
    from whose U-Boot the board started; the eMMC mmcblk0 keeps UGREEN's firmware in nine
    partitions that nothing mounts, one of them a swap partition that is not in use, beside its boot
    partitions mmcblk0boot0 and mmcblk0boot1; a SATA disk sda."""
    fake.add_disk("mmcblk0", size=31_268_536_320, rota=False, pttype="gpt")
    for n, fstype in enumerate(("vfat", "squashfs", "ext4", "ext4", "swap", "ext4", None, None, "ext4"), 1):
        fake.add_partition("mmcblk0", f"mmcblk0p{n}", fstype=fstype)
    fake.add_disk("mmcblk0boot0", size=4_194_304, rota=False)
    fake.add_disk("mmcblk0boot1", size=4_194_304, rota=False)
    fake.add_disk("sda", size=640_135_028_736, model="WDC WD6400AACS-00G8B0", serial="WD-W3")
    return fake


@pytest.fixture
def w3(fake_host, monkeypatch, tmp_path):  # noqa: F811 - the fixture of test_disk_safety
    return w3_now(fake_host, monkeypatch, tmp_path)


@pytest.fixture
def api(w3):
    with admin_client() as client:
        yield client


def ran_on(fake, device: str) -> list[list[str]]:
    """The host's and the container's commands that named ``device``."""
    return [call for call in [*fake.calls, *fake.container_calls] if device in call]


def disk_names(listed) -> list[str]:
    return [disk.name for disk in listed]


# ---------------------------------------------------------------------------
# which SD cards and eMMC are disks
# ---------------------------------------------------------------------------


class TestTheW3Now:
    def test_the_free_sd_card_is_listed_and_the_system_emmc_is_not(self, w3):
        listed = by_name(disks.list_disks())

        assert list(listed) == ["mmcblk1", "sda"]
        card = listed["mmcblk1"]
        assert (card.path, card.size, card.rotational, card.mmc_type) == ("/dev/mmcblk1", SD_SIZE, False, "SD")
        # From the card's own sysfs, not lsblk's udev name.
        assert (card.model, card.serial) == ("SC32G", "0x8f3a1b2c")
        assert (card.is_system, card.in_use, card.has_data, card.pool) == (False, False, False, None)
        assert listed["sda"].mmc_type is None
        assert [d.name for d in disks.host_disks()] == ["mmcblk1", "sda"]

    def test_the_sd_card_is_offered_for_a_pool_and_the_emmc_is_not(self, w3):
        assert [d.name for d in disks.addable_disks()] == ["mmcblk1", "sda"]
        candidate = disks.addable_disks()[0]
        assert (candidate.model, candidate.serial, candidate.has_data) == ("SC32G", "0x8f3a1b2c", False)
        facts = disks.get_device_facts(["/dev/mmcblk0", "/dev/mmcblk1"])
        assert (facts["/dev/mmcblk0"].hidden, facts["/dev/mmcblk0"].is_system) == (True, True)
        assert (facts["/dev/mmcblk1"].hidden, facts["/dev/mmcblk1"].in_use) == (False, False)
        assert disks.get_device_facts(["/dev/mmcblk0p1"])["/dev/mmcblk0p1"].hidden

    def test_a_pool_is_made_on_the_sd_card_listed_with_it_and_removed(self, w3):
        listed = by_name(disks.list_disks())
        info = create(["/dev/mmcblk1"], identities=[{
            "path": "/dev/mmcblk1", "serial": listed["mmcblk1"].serial, "wwn": None, "size": SD_SIZE}])

        assert w3.changes[0] == ["mkfs.btrfs", "-L", "tank", "-d", "single", "-m", "single", "/dev/mmcblk1"]
        assert (info.devices, info.mounted) == (["/dev/mmcblk1"], True)
        # Mounted as a pool, the card is still a data disk: listed as the pool's, not hidden.
        card = by_name(disks.list_disks())["mmcblk1"]
        assert (card.pool, card.in_use, card.in_use_reason, card.is_system) == (
            "tank", True, "Mounted at /mnt/pools/tank", False)
        assert [pool.name for pool in pools.list_pools()] == ["tank"]

        assert pools.delete_pool("tank") is True

        card = by_name(disks.list_disks())["mmcblk1"]
        assert (card.pool, card.in_use, card.has_data) == ("tank", False, True)
        assert [(pool.name, pool.mounted) for pool in pools.list_pools()] == [("tank", False)]

    @pytest.mark.parametrize(
        ("device", "refusal", "message"),
        [("/dev/mmcblk0", ValueError, "boot or virtual device"),
         ("/dev/mmcblk0p1", ValueError, "boot or virtual device"),
         ("/dev/mmcblk0boot0", HTTPException, "Invalid device name format"),
         ("/dev/mmcblk0boot1", HTTPException, "Invalid device name format"),
         ("/dev/mmcblk0rpmb", HTTPException, "Invalid device name format"),
         ("/dev/mmcblk0gp0", HTTPException, "Invalid device name format")],
        ids=["emmc", "emmc-root", "boot0", "boot1", "rpmb", "general-purpose-partition"],
    )
    def test_the_system_emmc_and_its_areas_never_take_a_pool(self, w3, device, refusal, message):
        with pytest.raises(refusal) as raised:
            create([device])
        assert message in str(getattr(raised.value, "detail", raised.value))
        assert not any(call[0] == "mkfs.btrfs" for call in w3.calls)

    def test_a_btrfs_filesystem_on_the_sd_card_may_be_a_pool_and_one_on_the_emmc_may_not(self, w3):
        assert disks.pool_device_problems(["/dev/mmcblk1"]) == {}
        assert disks.pool_device_problems(["/dev/mmcblk0p1"]) == {
            "/dev/mmcblk0p1": "/dev/mmcblk0p1 is on /dev/mmcblk0, a boot or virtual device"}


class TestTheW3OfTheDiskSafetyTests:
    def test_the_wiped_sd_card_is_offered_and_the_emmc_with_the_system_is_not(self, fake_host):  # noqa: F811
        bpi_w3(fake_host)

        assert disk_names(disks.list_disks()) == ["mmcblk1", "sda"]
        assert disk_names(disks.addable_disks()) == ["mmcblk1", "sda"]
        with pytest.raises(ValueError, match="boot or virtual device"):
            create(["/dev/mmcblk0"])


class TestTheW3Before:
    def test_the_free_emmc_is_offered_and_the_system_sd_card_is_not(self, fake_host):  # noqa: F811
        # The system on the SD card mmcblk1, the eMMC mmcblk0 with UGREEN's firmware in nine
        # partitions that nothing mounts, one of them a swap partition that is not in use.
        w3_before(fake_host)

        listed = by_name(disks.list_disks())

        assert list(listed) == ["mmcblk0", "sda"]
        emmc = listed["mmcblk0"]
        assert (emmc.is_system, emmc.in_use, emmc.has_data, emmc.pttype) == (False, False, True, "gpt")
        assert len(emmc.partitions) == 9
        assert [d.name for d in disks.addable_disks()] == ["mmcblk0", "sda"]
        with pytest.raises(ValueError, match="boot or virtual device"):
            create_forced(fake_host, ["/dev/mmcblk1"])


class TestAnSdCardInUseStaysHidden:
    @pytest.fixture
    def card(self, w3):
        """The SD card with a partition table: mmcblk1p1 ext4, mmcblk1p2 swap."""
        w3.node("mmcblk1")["pttype"] = "dos"
        w3.add_partition("mmcblk1", "mmcblk1p1", size=SD_SIZE // 2, fstype="ext4", uuid=SD_UUID,
                         partuuid=SD_PARTUUID)
        w3.add_partition("mmcblk1", "mmcblk1p2", size=SD_SIZE // 4, fstype="swap")
        return w3

    def test_with_partitions_that_nothing_uses_it_is_offered(self, card):
        listed = by_name(disks.list_disks())
        assert (listed["mmcblk1"].has_data, listed["mmcblk1"].in_use) == (True, False)
        assert "mmcblk1" in disk_names(disks.addable_disks())

    @pytest.mark.parametrize("use", [
        "a partition mounted", "the card mounted", "a partition as swap", "/boot", "ubootpart", "root by PARTUUID",
        "btrfs mounted elsewhere",
    ])
    def test_a_card_in_use_is_hidden(self, card, use):
        if use == "a partition mounted":
            card.add_mount("/media/card", "/dev/mmcblk1p1", "ext4", SD_UUID)
        elif use == "the card mounted":
            card.node("mmcblk1").pop("children")
            card.node("mmcblk1").update(pttype=None, fstype="ext4", uuid=SD_UUID)
            card.add_mount("/srv/card", "/dev/mmcblk1", "ext4", SD_UUID)
        elif use == "a partition as swap":
            card.swaps.append("/dev/mmcblk1p2")
        elif use == "/boot":
            card.add_mount("/boot", "/dev/mmcblk1p1", "ext4", SD_UUID)
        elif use == "ubootpart":
            # U-Boot loaded the kernel from the card; nothing of it is mounted.
            card.files["/proc/cmdline"] = card.files["/proc/cmdline"].replace(EMMC_PARTUUID, SD_PARTUUID.upper())
        elif use == "root by PARTUUID":
            card.files["/proc/cmdline"] = f"root=PARTUUID={SD_PARTUUID} rootwait"
        else:
            card.add_btrfs("photos", "9a8b7c6d-5e4f-4a3b-2c1d-0e9f8a7b6c5d", ["/dev/mmcblk1p1"])
            card.add_mount("/srv/photos", "/dev/mmcblk1p1", "btrfs", "9a8b7c6d-5e4f-4a3b-2c1d-0e9f8a7b6c5d")

        assert disk_names(disks.list_disks()) == ["sda"]
        assert disk_names(disks.addable_disks()) == ["sda"]
        assert disk_names(disks.host_disks()) == ["sda"]
        assert disks.get_device_facts(["/dev/mmcblk1"])["/dev/mmcblk1"].hidden
        with pytest.raises(ValueError, match="boot or virtual device"):
            create(["/dev/mmcblk1"])
        assert not any(call[0] == "mkfs.btrfs" for call in card.calls)

    @pytest.mark.parametrize("unread", ["the mount table", "an empty mount table", "the command line"])
    def test_a_card_is_hidden_while_what_tells_its_use_cannot_be_read(self, card, unread):
        # The card is free, but what would tell that it is in use is not there to read.
        if unread == "the mount table":
            card.fail[("findmnt",)] = (1, "findmnt: can't read /proc/self/mountinfo")
        elif unread == "an empty mount table":  # findmnt that printed nothing: no / either
            card.mounts.clear()
        else:
            # U-Boot loaded the kernel from the card, which nothing mounts: only ubootpart= tells.
            card.files["/proc/cmdline"] = card.files["/proc/cmdline"].replace(EMMC_PARTUUID, SD_PARTUUID)
            card.fail[("cat", "--", "/proc/cmdline")] = (1, "nsenter: timed out")

        assert disk_names(disks.list_disks()) == ["sda"]
        assert disk_names(disks.host_disks()) == ["sda"]
        assert disk_names(disks.addable_disks()) == ["sda"]
        assert disks.get_device_facts(["/dev/mmcblk1"])["/dev/mmcblk1"].hidden


# ---------------------------------------------------------------------------
# what an SD card or eMMC lacks: SMART and spin-down
# ---------------------------------------------------------------------------


class TestNoSmart:
    def test_the_storage_page_reads_no_smart_data_of_the_card(self, api, w3):
        response = api.get("/api/v1/storage/disks/mmcblk1/smart")

        assert response.status_code == 200, response.text
        assert {key: response.json()[key] for key in ("device", "healthy", "power_state", "raw_data")} == {
            "device": "/dev/mmcblk1", "healthy": None, "power_state": "active", "raw_data": None}
        assert ran_on(w3, "/dev/mmcblk1") == []

    def test_disk_health_lists_the_card_without_smart_data(self, api, w3):
        response = api.get("/api/v1/health/disks")

        assert response.status_code == 200, response.text
        card = next(disk for disk in response.json()["disks"] if disk["device"] == "/dev/mmcblk1")
        assert (card["health_status"], card["model"], card["serial"], card["capacity"]) == (
            "NO_SMART", "SC32G", "0x8f3a1b2c", "29.72 GB")
        assert (card["attributes"], card["temperature"], card["nvme_health"]) == ([], None, None)
        single = api.get("/api/v1/health/disk/mmcblk1")
        assert single.status_code == 200, single.text
        assert single.json()["health_status"] == "NO_SMART"
        assert ran_on(w3, "/dev/mmcblk1") == []
        assert ["smartctl", "-n", "standby", "-j", "-a", "/dev/sda"] in w3.container_calls

    def test_the_overview_counts_the_card_as_without_smart(self, api, w3):
        response = api.get("/api/v1/health/overview")

        assert response.status_code == 200, response.text
        assert response.json() == {
            "total_disks": 2, "healthy": 1, "warning": 0, "failed": 0, "unknown": 0, "standby": 0,
            "no_smart": 1, "average_temperature": 31,
        }

    def test_no_self_test_starts_on_the_card(self, api, w3):
        response = api.post("/api/v1/health/disk/mmcblk1/test?test_type=short")

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "/dev/mmcblk1 is an SD card or eMMC, which has no SMART self-tests"
        assert w3.self_tests == []
        with pytest.raises(HTTPException) as raised:
            scheduler_router.disk_device("mmcblk1")
        assert raised.value.status_code == 400
        assert "no SMART self-tests" in raised.value.detail
        assert ran_on(w3, "/dev/mmcblk1") == []

    def test_the_smart_monitor_leaves_the_card_alone(self, w3, tmp_path, monkeypatch):
        monkeypatch.setattr(health_router, "SMART_HISTORY_FILE", str(tmp_path / "smart_history.json"))

        assert health_router._smart_monitor_pass() == []

        assert ran_on(w3, "/dev/mmcblk1") == []
        assert ["smartctl", "-n", "standby", "-j", "-a", "/dev/sda"] in w3.container_calls

    def test_the_system_emmc_is_no_disk_of_the_smart_pages(self, api, w3):
        for url in ("/api/v1/health/disk/mmcblk0", "/api/v1/storage/disks/mmcblk0/smart"):
            assert api.get(url).status_code == 400, url
        assert ran_on(w3, "/dev/mmcblk0") == []


class TestNoSpinDown:
    def test_the_card_is_awake_and_gets_no_idle_timeout(self, w3):
        card = by_name(disks.list_disks())["mmcblk1"]

        assert (card.power_state, card.spindown) == ("active", 0)
        assert card.spindown_problem == (
            "/dev/mmcblk1 is an SD card or eMMC, which has no disk that spins down: hdparm sets idle timeouts "
            "of SATA disks")
        # No hdparm -C (nor smartctl in its place) asked the card for its power mode.
        assert ran_on(w3, "/dev/mmcblk1") == [["wipefs", "--no-act", "--json", "/dev/mmcblk1"]]
        assert disks.power_state("/dev/mmcblk1") == "active"

    def test_an_idle_timeout_is_refused_without_hdparm(self, api, w3):
        response = api.put("/api/v1/storage/disks/mmcblk1/spindown", json={"minutes": 20})

        assert response.status_code == 409, response.text
        assert "SD card or eMMC" in response.json()["detail"]
        assert not any("hdparm" in call for call in w3.calls)
        assert w3.changes == []

    def test_the_system_emmc_is_a_boot_device(self, api, w3):
        response = api.put("/api/v1/storage/disks/mmcblk0/spindown", json={"minutes": 20})

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "mmcblk0 is a boot or virtual device: it gets no idle timeout"


class TestTheIdleShutdown:
    def test_a_free_card_counts_once_a_listing_found_it_free_and_the_systems_never(self, w3, monkeypatch, tmp_path):
        # power.schedule._disk_counters runs every minute without lsblk: it counts the cards that the
        # last read of the host's state found free (storage.free-mmc-as-data-disk).
        schedule = importlib.import_module("src.power.schedule")
        events = importlib.import_module("src.websocket.events")
        counters = {name: SimpleNamespace(read_bytes=10, write_bytes=5) for name in ("sda", "mmcblk0", "mmcblk1")}
        monkeypatch.setattr(schedule.psutil, "disk_io_counters", lambda perdisk: counters)
        sys_block = tmp_path / "events-block"
        for name in counters:
            (sys_block / name / "device").mkdir(parents=True)
        monkeypatch.setattr(events, "SYS_BLOCK", str(sys_block))
        monkeypatch.setattr(disks, "_free_cards", frozenset())

        assert schedule._disk_counters() == {"sda": 15}
        disks.list_disks()
        assert schedule._disk_counters() == {"sda": 15, "mmcblk1": 15}
        w3.add_mount("/media/card", "/dev/mmcblk1", "ext4")
        disks.host_disks()
        assert schedule._disk_counters() == {"sda": 15}


# ---------------------------------------------------------------------------
# names
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["mmcblk0", "mmcblk1", "mmcblk12", "mmcblk1p1", "mmcblk0p12"])
def test_the_names_of_cards_and_their_partitions_are_disk_names(name):
    assert validate_device_path(f"/dev/{name}") == f"/dev/{name}"
    assert validate_disk_target(name) == f"/dev/{name}"


@pytest.mark.parametrize("name", [
    "mmcblk0boot0", "mmcblk0boot1", "mmcblk0rpmb", "mmcblk0gp0", "mmcblk", "mmcblkp1", "mmcblk0p", "mmcblk0p1p1"])
def test_an_emmcs_boot_rpmb_and_general_purpose_areas_are_no_disk_names(name):
    with pytest.raises(HTTPException):
        validate_device_path(f"/dev/{name}")
    with pytest.raises(HTTPException):
        validate_disk_target(name)


def test_an_emmcs_areas_are_hidden_by_their_names():
    assert [disks.is_hidden_disk(name) for name in (
        "mmcblk0boot0", "mmcblk0boot1", "mmcblk0rpmb", "mmcblk0gp3", "mmcblk0", "mmcblk1", "mmcblk1p1")] == [
        True, True, True, True, False, False, False]
    assert [disks.is_mmc_disk(name) for name in ("mmcblk0", "mmcblk12", "mmcblk0p1", "mmcblk0boot0", "sda")] == [
        True, True, False, False, False]
