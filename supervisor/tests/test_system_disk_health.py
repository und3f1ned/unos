"""The wear and the failure signs of an eMMC system disk (system.emmc-health-monitoring, ADR-0471, ADR-0472).

``src/health/system_disk.py`` finds the disk of the host's root filesystem in /proc/1/mountinfo and
sysfs, reads the eMMC's life-time estimates A and B and its pre-EOL state with ``mmc extcsd read``
(sysfs ``life_time`` and ``pre_eol_info``, as of boot, without mmc-utils) at its first pass and once
a day, and at each pass of the RAID monitor the kernel's I/O errors of the eMMC from the journal,
ext4's errors on the root since the supervisor started and whether the root's superblock is
read-only. It reports the problems of the source ``system_disk`` (ADR-0332) and acts on nothing.
Every read goes through ``src/host.py``, here to a fake of the host that fails the test for a
command it does not know; nothing is written, the data folder of the tests being read-only.
"""

import asyncio
import importlib
import json
import logging
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src import host
from src.auth.utils import verify_token
from src.health import problems, system_disk
from src.main import app
from src.notifications.router import save_configs
from src.notifications.schemas import NotificationChannel, NotificationConfig, WebhookConfig

health_router = importlib.import_module("src.health.router")
notifications = importlib.import_module("src.notifications.router")
WEBUI = Path(__file__).resolve().parents[2] / "webui"

# The BPI-W3 of 2026-11-23: the system on its eMMC mmcblk0, ext4 mounted as Armbian mounts it.
W3_ROOT = "27 1 179:1 / / rw,noatime shared:1 - ext4 /dev/mmcblk0p1 rw,errors=remount-ro,commit=600\n"
MOUNTINFO = (
    "22 27 0:21 / /sys rw,nosuid,nodev,noexec,relatime shared:7 - sysfs sysfs rw\n"
    + W3_ROOT
    + "45 27 0:41 / /mnt/pools/data rw,noatime shared:24 - btrfs /dev/sda rw,space_cache=v2,subvolid=5,subvol=/\n"
)
W3_LINK = "../../devices/platform/fe2e0000.mmc/mmc_host/mmc0/mmc0:0001/block/mmcblk0/mmcblk0p1"
# What mmc-utils prints, the lines around the estimates shortened.
EXTCSD = """=============================================
  Extended CSD rev 1.8 (MMC 5.1)
=============================================

Card Supported Command sets [S_CMD_SET: 0x01]
HPI Features [HPI_FEATURE: 0x01]: implementation based on CMD13
Background operations support [BKOPS_SUPPORT: 0x01]
eMMC Firmware Version:
eMMC Life Time Estimation A [EXT_CSD_DEVICE_LIFE_TIME_EST_TYP_A]: 0x{a:02x}
eMMC Life Time Estimation B [EXT_CSD_DEVICE_LIFE_TIME_EST_TYP_B]: 0x{b:02x}
eMMC Pre EOL information [EXT_CSD_PRE_EOL_INFO]: 0x{eol:02x}
Secure Removal Type [SECURE_REMOVAL_TYPE]: 0x3b
"""
# What the kernel logs for a failed read of the eMMC (block layer, buffer heads), and lines that
# are not one: another disk's error, ext4's own error, an SD card's.
IO_ERROR = "I/O error, dev mmcblk0, sector 2048 op 0x0:(READ) flags 0x80700 phys_seg 1 prio class 2"
BUFFER_ERROR = "Buffer I/O error on dev mmcblk0p1, logical block 0, async page read"
OTHER_LINES = [
    "I/O error, dev sda, sector 4096 op 0x0:(READ) flags 0x0 phys_seg 1 prio class 2",
    "EXT4-fs error (device mmcblk0p1): ext4_lookup:1855: inode #2: comm ls: deleted inode referenced: 13",
    "I/O error, dev mmcblk1, sector 8 op 0x0:(READ) flags 0x0 phys_seg 1 prio class 2",
]
READ_ONLY_PROGRAMS = {"cat", "readlink", "mmc", "journalctl", "dd"}


class FakeHost:
    """The host behind ``src.host.run_on_host``: its files, links, mmc-utils, kernel log and the direct
    read of the root partition. A command it does not know fails the test."""

    def __init__(self) -> None:
        self.files = {
            "/proc/1/mountinfo": MOUNTINFO,
            "/sys/block/mmcblk0/device/type": "MMC\n",
            "/sys/block/mmcblk0/device/name": "BJTD4R\n",
            "/sys/block/mmcblk0/size": "61071360\n",
            "/sys/block/mmcblk0/device/life_time": "0x01 0x01\n",
            "/sys/block/mmcblk0/device/pre_eol_info": "0x01\n",
            "/sys/fs/ext4/mmcblk0p1/errors_count": "0\n",
        }
        self.links = {"/sys/dev/block/179:1": W3_LINK}
        self.wear = {"a": 1, "b": 1, "eol": 1}
        # None: mmc-utils is installed and answers; "missing": it is not; (status, stderr): it fails.
        self.mmc: object = None
        self.kernel_log: list[str] = []
        self.journal_fails = False
        # True: the direct read works; False: it fails with an I/O error; "hang": it never ends;
        # "cannot run": nsenter fails before dd starts.
        self.direct_read: object = True
        self.readlink_fails = False
        self.calls: list[list[str]] = []

    def run(self, cmd, *, timeout=60, check=False, input=None, errors=None):
        cmd = list(cmd)
        self.calls.append(cmd)
        result = self._answer(cmd, timeout)
        if check and not result.ok:
            raise host.HostCommandError(cmd, result.returncode, result.stderr)
        return result

    def programs(self) -> set[str]:
        """The programs that the host ran, under timeout or not."""
        return {(call[3] if call[0] == "timeout" else call[0]) for call in self.calls}

    def runs_of(self, program: str) -> list[list[str]]:
        return [call for call in self.calls if program in call[:4]]

    def _answer(self, cmd, timeout):
        if cmd[:2] == ["cat", "--"] and len(cmd) == 3:
            if cmd[2] in self.files:
                return host.HostResult(0, self.files[cmd[2]], "")
            return host.HostResult(1, "", f"cat: {cmd[2]}: No such file or directory\n")
        if cmd[0] == "readlink" and len(cmd) == 2:
            if self.readlink_fails:
                return host.HostResult(1, "", "nsenter: failed to execute readlink: Input/output error\n")
            if cmd[1] in self.links:
                return host.HostResult(0, self.links[cmd[1]] + "\n", "")
            return host.HostResult(1, "", "")
        assert cmd[:3] == ["timeout", "--kill-after=5", cmd[2]], cmd
        program, args = cmd[3], cmd[4:]
        if program == "mmc":
            assert args == ["extcsd", "read", "/dev/mmcblk0"], cmd
            if self.mmc == "missing":
                return host.HostResult(127, "", "timeout: failed to run command 'mmc': No such file or directory\n")
            if isinstance(self.mmc, tuple):
                return host.HostResult(self.mmc[0], "", self.mmc[1])
            return host.HostResult(0, EXTCSD.format(**self.wear), "")
        if program == "journalctl":
            assert "--dmesg" in args and "--output=cat" in args, cmd
            assert any(re.fullmatch(r"--since=@[0-9]+", arg) for arg in args), cmd
            if self.journal_fails:
                return host.HostResult(1, "", "Failed to open journal: Input/output error\n")
            [pattern] = [arg.removeprefix("--grep=") for arg in args if arg.startswith("--grep=")]
            found = [line for line in self.kernel_log if re.search(pattern, line)]
            # journalctl --grep exits with status 1, saying nothing, when no entry matched.
            return host.HostResult(0 if found else 1, "".join(f"{line}\n" for line in found), "")
        if program == "dd":
            assert args == ["if=/dev/mmcblk0p1", "of=/dev/null", "bs=1M", "count=1", "iflag=direct",
                            "status=none"], cmd
            if self.direct_read == "hang":
                raise subprocess.TimeoutExpired(cmd, timeout)
            if self.direct_read == "cannot run":
                return host.HostResult(1, "", "nsenter: cannot open /proc/1/ns/mnt: Cannot allocate memory\n")
            if self.direct_read:
                return host.HostResult(0, "", "")
            return host.HostResult(1, "", "dd: error reading '/dev/mmcblk0p1': Input/output error\n")
        raise AssertionError(f"a host command the fake does not know: {cmd}")


def _forbidden_subprocess(*args, **kwargs):
    raise AssertionError(f"a process outside src/host.py: {args}")


@pytest.fixture
def fake(monkeypatch):
    found = FakeHost()
    monkeypatch.setattr(host, "run_on_host", found.run)
    monkeypatch.setattr(subprocess, "run", _forbidden_subprocess)
    return found


@pytest.fixture
def alerts(monkeypatch):
    """The alerts that the problems send, recorded instead of sent (health.router._deliver_alert)."""
    sent = []

    async def deliver(event):
        sent.append(event)

    monkeypatch.setattr(health_router, "_deliver_alert", deliver)
    return sent


@pytest.fixture
def clock(monkeypatch):
    """The module's monotonic clock, moved by the test."""
    now = SimpleNamespace(value=1000.0)
    monkeypatch.setattr(system_disk, "_monotonic", lambda: now.value)
    return now


def _problems() -> dict[str, tuple[str, str]]:
    return {key: (p.kind, p.severity) for key, p in problems.of_source("system_disk").items()}


async def _health() -> dict:
    return (await system_disk.health()).model_dump()


class TestTheReadings:
    async def test_an_emmc_system_disk_answers_its_wear_from_mmc_and_its_errors(self, fake, alerts):
        await system_disk.monitor_pass()

        answer = await _health()
        emmc = answer["emmc"]
        assert answer["reason"] is None
        assert (emmc["device"], emmc["model"], emmc["size"]) == ("/dev/mmcblk0", "BJTD4R", 61071360 * 512)
        assert (emmc["root"], emmc["filesystem"]) == ("/dev/mmcblk0p1", "ext4")
        assert (emmc["life_time_a"], emmc["life_time_b"], emmc["pre_eol"]) == (1, 1, 1)
        assert (emmc["wear_source"], emmc["mmc_missing"], emmc["wear_error"]) == ("mmc", False, None)
        assert emmc["wear_read_at"] and emmc["checked_at"]
        assert (emmc["io_errors"], emmc["ext4_errors"], emmc["read_only"]) == (0, 0, False)
        assert emmc["direct_read_failed"] is False
        assert answer["problems"] == []
        assert _problems() == {} and alerts == []

    async def test_the_estimates_come_from_extcsd_not_from_the_sysfs_values_of_the_boot(self, fake):
        fake.wear = {"a": 3, "b": 2, "eol": 1}
        await system_disk.monitor_pass()
        emmc = (await _health())["emmc"]
        assert (emmc["life_time_a"], emmc["life_time_b"], emmc["pre_eol"]) == (3, 2, 1)
        assert not any("life_time" in call[-1] or "pre_eol_info" in call[-1] for call in fake.calls)

    async def test_without_mmc_utils_the_wear_is_the_kernels_of_the_boot_and_nothing_is_alerted(self, fake, alerts):
        fake.mmc = "missing"
        fake.files["/sys/block/mmcblk0/device/life_time"] = "0x02 0x01\n"
        await system_disk.monitor_pass()

        emmc = (await _health())["emmc"]
        assert (emmc["life_time_a"], emmc["life_time_b"], emmc["pre_eol"]) == (2, 1, 1)
        assert (emmc["wear_source"], emmc["mmc_missing"], emmc["wear_error"]) == ("sysfs", True, None)
        assert alerts == [] and _problems() == {}

    async def test_a_failing_mmc_falls_back_to_sysfs_and_says_why(self, fake):
        fake.mmc = (1, "Could not read EXT_CSD from /dev/mmcblk0: Operation not permitted\n")
        await system_disk.monitor_pass()
        emmc = (await _health())["emmc"]
        assert (emmc["wear_source"], emmc["mmc_missing"]) == ("sysfs", False)
        assert "Operation not permitted" in emmc["wear_error"]
        assert (emmc["life_time_a"], emmc["life_time_b"], emmc["pre_eol"]) == (1, 1, 1)

    async def test_a_failing_mmc_after_a_read_of_it_keeps_that_read_and_says_why(self, fake, clock):
        fake.wear = {"a": 3, "b": 2, "eol": 1}
        await system_disk.monitor_pass()
        read_at = (await _health())["emmc"]["wear_read_at"]
        fake.mmc = (1, "Could not read EXT_CSD from /dev/mmcblk0: Connection timed out\n")
        clock.value += 86400
        await system_disk.monitor_pass()

        emmc = (await _health())["emmc"]
        # Not the boot's sysfs values (0x01 0x01), which are older than the read before.
        assert (emmc["life_time_a"], emmc["life_time_b"], emmc["wear_source"]) == (3, 2, "mmc")
        assert emmc["wear_read_at"] == read_at
        assert "Connection timed out" in emmc["wear_error"]
        # mmc is tried again in a day, not at every pass.
        clock.value += 300
        await system_disk.monitor_pass()
        assert len(fake.runs_of("mmc")) == 2

    async def test_an_mmc_that_prints_no_estimates_falls_back_to_sysfs(self, fake):
        fake.mmc = (0, "")
        await system_disk.monitor_pass()
        emmc = (await _health())["emmc"]
        assert (emmc["wear_source"], emmc["life_time_a"]) == ("sysfs", 1)
        assert emmc["wear_error"] == "mmc extcsd read printed no life-time estimates"

    async def test_without_any_wear_it_says_why_judges_nothing_and_reads_again(self, fake, alerts, clock):
        fake.mmc = (124, "")
        del fake.files["/sys/block/mmcblk0/device/life_time"]
        await system_disk.monitor_pass()
        emmc = (await _health())["emmc"]
        assert (emmc["wear_source"], emmc["life_time_a"], emmc["pre_eol"], emmc["wear_read_at"]) == (
            None, None, None, None)
        assert "did not answer within 20 seconds" in emmc["wear_error"]
        assert "No such file" in emmc["wear_error"]
        assert alerts == [] and _problems() == {}

        fake.mmc = None
        clock.value += 300
        await system_disk.monitor_pass()
        assert (await _health())["emmc"]["wear_source"] == "mmc"
        assert len(fake.runs_of("mmc")) == 2

    async def test_the_wear_is_read_at_the_start_and_once_a_day_the_errors_at_every_pass(self, fake, clock):
        await system_disk.monitor_pass()
        clock.value += 300
        await system_disk.monitor_pass()
        clock.value += 23 * 3600
        await system_disk.monitor_pass()
        assert len(fake.runs_of("mmc")) == 1
        assert len(fake.runs_of("journalctl")) == 3

        fake.wear = {"a": 2, "b": 1, "eol": 1}
        clock.value += 3600
        await system_disk.monitor_pass()
        assert len(fake.runs_of("mmc")) == 2
        assert (await _health())["emmc"]["life_time_a"] == 2
        # The disk is found once: its model and size are not read again.
        assert [call[-1] for call in fake.calls].count("/sys/block/mmcblk0/device/name") == 1

    async def test_a_request_before_the_first_pass_reads_the_disk_then(self, fake):
        answer = await _health()
        assert answer["emmc"]["life_time_a"] == 1
        assert len(fake.runs_of("mmc")) == 1

    async def test_the_reads_write_nothing(self, fake):
        fake.kernel_log = [IO_ERROR]
        await system_disk.monitor_pass()
        assert fake.programs() <= READ_ONLY_PROGRAMS
        [dd] = fake.runs_of("dd")
        assert "of=/dev/null" in dd and "iflag=direct" in dd


class TestNoEmmc:
    async def test_a_root_on_another_disk_answers_why_and_reads_no_wear(self, fake, alerts):
        fake.files["/proc/1/mountinfo"] = "26 1 8:2 / / rw,relatime - ext4 /dev/sda2 rw,errors=remount-ro\n"
        fake.links = {"/sys/dev/block/8:2": "../../devices/pci0000:00/0000:00:17.0/ata1/host0/target0:0:0/0:0:0:0/block/sda/sda2"}
        await system_disk.monitor_pass()

        answer = await _health()
        assert (answer["emmc"], answer["reason"], answer["system_disk"]) == (None, "not_emmc", "/dev/sda")
        assert fake.programs() == {"cat", "readlink"}
        assert alerts == [] and _problems() == {}

    async def test_a_root_on_an_sd_card_is_no_emmc(self, fake):
        fake.files["/sys/block/mmcblk0/device/type"] = "SD\n"
        await system_disk.monitor_pass()
        answer = await _health()
        assert (answer["emmc"], answer["reason"], answer["system_disk"]) == (None, "not_emmc", "/dev/mmcblk0")
        assert "mmc" not in fake.programs()

    async def test_a_root_on_no_block_device_says_so(self, fake):
        fake.files["/proc/1/mountinfo"] = "120 98 0:52 / / rw,relatime - overlay overlay rw,lowerdir=/a,upperdir=/b\n"
        await system_disk.monitor_pass()
        answer = await _health()
        assert (answer["emmc"], answer["reason"]) == (None, "no_disk")
        assert "overlay" in answer["detail"]

    async def test_a_link_that_cannot_be_read_is_no_verdict(self, fake, clock):
        fake.readlink_fails = True
        await system_disk.monitor_pass()
        answer = await _health()
        assert (answer["emmc"], answer["reason"]) == (None, "unreadable")
        assert "Input/output error" in answer["detail"]

        fake.readlink_fails = False
        clock.value += 300
        await system_disk.monitor_pass()
        assert (await _health())["emmc"]["device"] == "/dev/mmcblk0"

    async def test_a_root_that_cannot_be_read_is_read_again_at_the_next_pass(self, fake, alerts):
        del fake.files["/proc/1/mountinfo"]
        await system_disk.monitor_pass()
        answer = await _health()
        assert (answer["emmc"], answer["reason"]) == (None, "unreadable")
        assert "No such file" in answer["detail"]

        fake.files["/proc/1/mountinfo"] = MOUNTINFO
        await system_disk.monitor_pass()
        assert (await _health())["emmc"]["device"] == "/dev/mmcblk0"
        assert alerts == []


class TestTheWear:
    @pytest.mark.parametrize(("wear", "found"), [
        ({"a": 7, "b": 7, "eol": 1}, {}),
        ({"a": 8, "b": 1, "eol": 1}, {"emmc_worn:/dev/mmcblk0": ("emmc_worn", "warning")}),
        ({"a": 1, "b": 9, "eol": 1}, {"emmc_worn:/dev/mmcblk0": ("emmc_worn", "warning")}),
        ({"a": 1, "b": 1, "eol": 2}, {"emmc_worn:/dev/mmcblk0": ("emmc_worn", "warning")}),
        ({"a": 10, "b": 1, "eol": 1}, {"emmc_worn:/dev/mmcblk0": ("emmc_worn", "error")}),
        ({"a": 1, "b": 11, "eol": 1}, {"emmc_worn:/dev/mmcblk0": ("emmc_worn", "error")}),
        ({"a": 1, "b": 1, "eol": 3}, {"emmc_worn:/dev/mmcblk0": ("emmc_worn", "error")}),
        # Not defined, and the reserved values: no estimate to judge.
        ({"a": 0, "b": 0, "eol": 0}, {}),
        ({"a": 12, "b": 0, "eol": 4}, {}),
    ])
    async def test_the_thresholds(self, fake, alerts, wear, found):
        fake.wear = wear
        await system_disk.monitor_pass()
        assert _problems() == found
        assert len(alerts) == len(found)

    async def test_the_warning_is_alerted_once_and_the_error_again(self, fake, alerts, clock):
        fake.wear = {"a": 8, "b": 1, "eol": 1}
        await system_disk.monitor_pass()
        clock.value += 300
        await system_disk.monitor_pass()
        [warning] = alerts
        assert (warning.type.value, warning.title) == ("warning", "The system's eMMC /dev/mmcblk0 is wearing out")
        assert "70 to 80% of its life" in warning.message

        fake.wear = {"a": 10, "b": 1, "eol": 3}
        clock.value += 86400
        await system_disk.monitor_pass()
        clock.value += 300
        await system_disk.monitor_pass()
        assert len(alerts) == 2
        error = alerts[1]
        assert (error.type.value, error.title) == ("error", "The system's eMMC /dev/mmcblk0 is nearly worn out")
        assert "90 to 100% of its life" in error.message and "urgent" in error.message
        [problem] = problems.current()
        assert (problem.kind, problem.subject, problem.severity) == ("emmc_worn", "/dev/mmcblk0", "error")

    async def test_the_card_says_the_problems(self, fake):
        fake.wear = {"a": 8, "b": 1, "eol": 1}
        await system_disk.monitor_pass()
        assert (await _health())["problems"] == [{"kind": "emmc_worn", "severity": "warning"}]


class TestTheErrors:
    async def test_io_errors_of_the_emmc_are_a_warning_while_the_direct_read_works(self, fake, alerts, clock):
        fake.kernel_log = [*OTHER_LINES]
        await system_disk.monitor_pass()
        assert _problems() == {} and fake.runs_of("dd") == []

        fake.kernel_log += [IO_ERROR, BUFFER_ERROR]
        clock.value += 300
        await system_disk.monitor_pass()
        assert _problems() == {"system_disk_errors:/dev/mmcblk0": ("system_disk_errors", "warning")}
        assert (await _health())["emmc"]["io_errors"] == 2
        assert len(fake.runs_of("dd")) == 1
        [alert] = alerts
        assert alert.title == "The system disk /dev/mmcblk0 reports errors"
        assert "2 I/O error(s)" in alert.message

    async def test_io_errors_and_a_failing_direct_read_are_a_failure(self, fake, alerts, clock):
        fake.kernel_log = [IO_ERROR]
        await system_disk.monitor_pass()
        fake.direct_read = False
        clock.value += 300
        await system_disk.monitor_pass()

        assert _problems() == {"system_disk_failed:/dev/mmcblk0": ("system_disk_failed", "error")}
        assert (await _health())["emmc"]["direct_read_failed"] is True
        assert [alert.title for alert in alerts] == [
            "The system disk /dev/mmcblk0 reports errors", "The system disk /dev/mmcblk0 is failing"]
        assert "direct read" in alerts[1].message

        # The failure stays, and the failed partition is not read again.
        fake.direct_read = True
        clock.value += 300
        await system_disk.monitor_pass()
        assert len(fake.runs_of("dd")) == 2
        assert _problems() == {"system_disk_failed:/dev/mmcblk0": ("system_disk_failed", "error")}
        assert len(alerts) == 2

    async def test_a_direct_read_that_never_ends_fails(self, fake):
        fake.kernel_log = [IO_ERROR]
        fake.direct_read = "hang"
        await system_disk.monitor_pass()
        assert _problems() == {"system_disk_failed:/dev/mmcblk0": ("system_disk_failed", "error")}

    async def test_a_direct_read_that_cannot_run_is_no_failure_and_is_tried_again(self, fake, alerts, clock):
        fake.kernel_log = [IO_ERROR]
        fake.direct_read = "cannot run"
        await system_disk.monitor_pass()
        assert _problems() == {"system_disk_errors:/dev/mmcblk0": ("system_disk_errors", "warning")}
        emmc = (await _health())["emmc"]
        assert emmc["direct_read_failed"] is False
        assert "Cannot allocate memory" in emmc["check_error"]

        fake.direct_read = False
        clock.value += 300
        await system_disk.monitor_pass()
        assert _problems() == {"system_disk_failed:/dev/mmcblk0": ("system_disk_failed", "error")}
        assert (await _health())["emmc"]["check_error"] is None

    async def test_the_count_of_io_errors_never_falls_and_the_failure_holds(self, fake, clock):
        fake.kernel_log = [IO_ERROR, BUFFER_ERROR]
        fake.direct_read = False
        await system_disk.monitor_pass()
        # The journal dropped its old entries.
        fake.kernel_log = []
        clock.value += 300
        await system_disk.monitor_pass()
        assert (await _health())["emmc"]["io_errors"] == 2
        assert _problems() == {"system_disk_failed:/dev/mmcblk0": ("system_disk_failed", "error")}

    async def test_a_failing_direct_read_without_io_errors_is_never_made(self, fake):
        fake.direct_read = False
        await system_disk.monitor_pass()
        assert fake.runs_of("dd") == [] and _problems() == {}

    async def test_errors_of_ext4_before_the_start_do_not_count(self, fake, alerts, clock):
        fake.files["/sys/fs/ext4/mmcblk0p1/errors_count"] = "3\n"
        await system_disk.monitor_pass()
        clock.value += 300
        await system_disk.monitor_pass()
        assert (await _health())["emmc"]["ext4_errors"] == 0
        assert _problems() == {}

        fake.files["/sys/fs/ext4/mmcblk0p1/errors_count"] = "4\n"
        clock.value += 300
        await system_disk.monitor_pass()
        assert (await _health())["emmc"]["ext4_errors"] == 1
        assert _problems() == {"system_disk_errors:/dev/mmcblk0": ("system_disk_errors", "warning")}
        assert "1 new error(s)" in alerts[0].message

    async def test_a_read_only_root_with_a_new_ext4_error_is_a_failure(self, fake, alerts, clock):
        await system_disk.monitor_pass()
        # ext4 counted an error and, with errors=remount-ro, made the superblock read-only; the
        # mount keeps its rw.
        fake.files["/sys/fs/ext4/mmcblk0p1/errors_count"] = "1\n"
        fake.files["/proc/1/mountinfo"] = MOUNTINFO.replace(
            "ext4 /dev/mmcblk0p1 rw,errors=remount-ro", "ext4 /dev/mmcblk0p1 ro,errors=remount-ro")
        clock.value += 300
        await system_disk.monitor_pass()

        assert _problems() == {"system_disk_failed:/dev/mmcblk0": ("system_disk_failed", "error")}
        emmc = (await _health())["emmc"]
        assert (emmc["read_only"], emmc["ext4_errors"]) == (True, 1)
        [alert] = alerts
        assert (alert.type.value, alert.title) == ("error", "The system disk /dev/mmcblk0 is failing")
        assert "read-only" in alert.message
        assert fake.runs_of("dd") == []

    async def test_a_root_remounted_read_only_by_hand_is_no_problem(self, fake, alerts, clock):
        await system_disk.monitor_pass()
        fake.files["/proc/1/mountinfo"] = MOUNTINFO.replace(
            "/ / rw,noatime shared:1 - ext4 /dev/mmcblk0p1 rw,", "/ / ro,noatime shared:1 - ext4 /dev/mmcblk0p1 ro,")
        clock.value += 300
        await system_disk.monitor_pass()
        assert (await _health())["emmc"]["read_only"] is True
        assert _problems() == {} and alerts == []

    async def test_a_read_only_mount_namespace_changes_nothing(self, fake, clock):
        # A device check's namespace (ADR-0221) remounts / read-only for its own mount only; the
        # superblock stays read-write, and only /proc/1/mountinfo is read.
        await system_disk.monitor_pass()
        fake.files["/proc/self/mountinfo"] = MOUNTINFO.replace("/ / rw,noatime", "/ / ro,noatime")
        fake.files["/proc/1/mountinfo"] = MOUNTINFO.replace("/ / rw,noatime", "/ / ro,noatime")
        fake.files["/sys/fs/ext4/mmcblk0p1/errors_count"] = "1\n"
        clock.value += 300
        await system_disk.monitor_pass()
        assert (await _health())["emmc"]["read_only"] is False
        assert _problems() == {"system_disk_errors:/dev/mmcblk0": ("system_disk_errors", "warning")}
        assert "/proc/self/mountinfo" not in [call[-1] for call in fake.calls]

    async def test_a_journal_that_cannot_be_read_keeps_what_was_found_and_says_why(self, fake, clock):
        fake.kernel_log = [IO_ERROR]
        await system_disk.monitor_pass()
        fake.journal_fails = True
        clock.value += 300
        await system_disk.monitor_pass()
        emmc = (await _health())["emmc"]
        assert emmc["io_errors"] == 1
        assert "Input/output error" in emmc["check_error"]
        assert _problems() == {"system_disk_errors:/dev/mmcblk0": ("system_disk_errors", "warning")}


def _read_only_with_a_new_ext4_error(fake: FakeHost) -> None:
    fake.files["/sys/fs/ext4/mmcblk0p1/errors_count"] = "1\n"
    fake.files["/proc/1/mountinfo"] = MOUNTINFO.replace(
        "ext4 /dev/mmcblk0p1 rw,errors=remount-ro", "ext4 /dev/mmcblk0p1 ro,errors=remount-ro")


def _io_errors_and_a_failing_direct_read(fake: FakeHost) -> None:
    fake.kernel_log = [IO_ERROR]
    fake.direct_read = False


class TestTheAlerts:
    @pytest.mark.parametrize("failure", [_read_only_with_a_new_ext4_error, _io_errors_and_a_failing_direct_read])
    async def test_the_failure_reaches_the_channels_while_nothing_can_be_written(
            self, fake, monkeypatch, tmp_path, caplog, clock, failure):
        # The channels are read from their file; the history of the alerts cannot be written, as on a
        # read-only root, and the alert goes all the same.
        save_configs([NotificationConfig(name="Hook", channel=NotificationChannel.WEBHOOK,
                                         webhook_config=WebhookConfig(url="https://hooks.example/nas"))])
        sent = []

        async def send_through(config, event):
            sent.append(event)
            return True, None

        monkeypatch.setattr(health_router, "send_through", send_through)
        await system_disk.monitor_pass()
        failure(fake)
        clock.value += 300
        tmp_path.chmod(0o555)
        try:
            with caplog.at_level(logging.WARNING):
                await system_disk.monitor_pass()
        finally:
            tmp_path.chmod(0o755)

        assert [event.title for event in sent] == ["The system disk /dev/mmcblk0 is failing"]
        assert "Could not record the notification" in caplog.text

    @staticmethod
    def _hooks(*names: str, disabled: str = "") -> list[NotificationConfig]:
        return [NotificationConfig(name=name, channel=NotificationChannel.WEBHOOK, enabled=name != disabled,
                                   webhook_config=WebhookConfig(url=f"https://hooks.example/{name}"))
                for name in names]

    @staticmethod
    def _channels_file_unreadable(tmp_path) -> None:
        """notifications.json can no longer be read, as on a failing eMMC: open() raises an OSError."""
        path = tmp_path / "notifications.json"
        path.unlink(missing_ok=True)
        path.mkdir()

    @pytest.fixture
    def sent(self, monkeypatch):
        sent = []

        async def send_through(config, event):
            sent.append((config.name, event.title))
            return True, None

        monkeypatch.setattr(health_router, "send_through", send_through)
        return sent

    async def test_a_channels_file_that_cannot_be_read_alerts_every_enabled_channel_from_ram(
            self, fake, tmp_path, caplog, clock, sent):
        # UNOS wrote the channels (System > Notifications), and keeps what it wrote in RAM.
        notifications.save_configs(self._hooks("phone", "mail", "old", disabled="old"))
        await system_disk.monitor_pass()
        self._channels_file_unreadable(tmp_path)
        _io_errors_and_a_failing_direct_read(fake)
        clock.value += 300
        with caplog.at_level(logging.WARNING):
            await system_disk.monitor_pass()
            # Another alert while the file stays unreadable logs nothing more.
            await problems.update("pools", {"pool_degraded:data": problems.Problem(
                key="pool_degraded:data", severity="error", kind="pool_degraded", subject="data",
                title="Pool 'data' is degraded", detail="1 device(s) of the pool are missing.")})

        assert sent == [("phone", "The system disk /dev/mmcblk0 is failing"),
                        ("mail", "The system disk /dev/mmcblk0 is failing"),
                        ("phone", "Pool 'data' is degraded"), ("mail", "Pool 'data' is degraded")]
        kept = [r for r in caplog.records if "kept in RAM" in r.getMessage()]
        assert len(kept) == 1 and kept[0].levelno == logging.WARNING
        # Every other reader keeps ADR-0324's rule: a file that cannot be opened reads as no channels.
        assert notifications.load_configs() == []

    async def test_the_channels_read_at_the_start_serve_when_the_file_cannot_be_read(
            self, fake, tmp_path, monkeypatch, sent):
        (tmp_path / "notifications.json").write_text(
            json.dumps([config.model_dump() for config in self._hooks("phone")]))

        async def no_loop():
            return None

        for loop in ("raid_monitor_loop", "smart_monitor_loop"):
            monkeypatch.setattr(health_router, loop, no_loop)
        monkeypatch.setattr(system_disk, "system_disk_loop", no_loop)
        await health_router.start_health_monitors()
        await health_router.stop_health_monitors()

        self._channels_file_unreadable(tmp_path)
        _io_errors_and_a_failing_direct_read(fake)
        await system_disk.monitor_pass()
        assert sent == [("phone", "The system disk /dev/mmcblk0 is failing")]

    async def test_a_read_that_works_wins_over_the_copy_in_ram(self, fake, tmp_path, sent):
        notifications.save_configs(self._hooks("phone"))
        # Changed outside UNOS: the file is read as it is.
        (tmp_path / "notifications.json").write_text(
            json.dumps([config.model_dump() for config in self._hooks("pager")]))
        _io_errors_and_a_failing_direct_read(fake)
        await system_disk.monitor_pass()
        assert sent == [("pager", "The system disk /dev/mmcblk0 is failing")]

    async def test_without_a_copy_in_ram_an_unreadable_file_alerts_nothing_and_says_why(
            self, fake, tmp_path, caplog, sent):
        self._channels_file_unreadable(tmp_path)
        _io_errors_and_a_failing_direct_read(fake)
        with caplog.at_level(logging.WARNING):
            await system_disk.monitor_pass()
        assert sent == []
        assert "the alert reaches no channel" in caplog.text and "none are kept in RAM" in caplog.text

    async def test_the_loop_reads_at_the_start_and_every_5_minutes_and_goes_on_after_an_error(
            self, fake, alerts, monkeypatch, caplog):
        waits = []
        real_check = system_disk.check
        calls = []

        def check():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("the host is gone")
            real_check()

        async def sleep(seconds):
            waits.append(seconds)
            if len(waits) == 2:
                raise asyncio.CancelledError

        monkeypatch.setattr(system_disk, "check", check)
        monkeypatch.setattr(system_disk.asyncio, "sleep", sleep)
        fake.kernel_log = [IO_ERROR]
        fake.direct_read = False
        with caplog.at_level(logging.ERROR), pytest.raises(asyncio.CancelledError):
            await system_disk.system_disk_loop()

        assert waits == [300, 300]
        assert "System disk monitor error" in caplog.text
        assert _problems() == {"system_disk_failed:/dev/mmcblk0": ("system_disk_failed", "error")}

    async def test_the_raid_monitor_does_not_wait_for_the_system_disk(self, fake, monkeypatch):
        async def no_pools():
            return []

        async def stop_after_one_round(_seconds):
            health_router._raid_monitor_running = False

        monkeypatch.setattr(health_router, "check_raid_health", no_pools)
        monkeypatch.setattr(health_router, "save_raid_status", lambda status: None)
        monkeypatch.setattr(health_router.asyncio, "sleep", stop_after_one_round)
        monkeypatch.setattr(health_router, "_raid_monitor_running", True)
        await health_router.raid_monitor_loop()
        assert fake.calls == []

    def test_the_health_banner_names_every_kind(self):
        banner = (WEBUI / "src/lib/components/ProblemsBanner.svelte").read_text(encoding="utf-8")
        for kind in ("emmc_worn", "system_disk_errors", "system_disk_failed"):
            assert f"{kind}: 'problems.kind.{kind}'" in banner, kind


class TestTheEndpoint:
    def _client(self, is_admin: bool) -> TestClient:
        user = SimpleNamespace(id=1, username="admin" if is_admin else "alice", is_admin=is_admin, is_active=True)
        app.dependency_overrides[verify_token] = lambda: user
        return TestClient(app)

    def test_an_administrator_reads_it(self, fake):
        try:
            response = self._client(True).get("/api/v1/health/system-disk")
        finally:
            app.dependency_overrides.pop(verify_token, None)
        assert response.status_code == 200
        assert response.json()["emmc"]["model"] == "BJTD4R"

    def test_a_regular_user_gets_403(self, fake):
        try:
            response = self._client(False).get("/api/v1/health/system-disk")
        finally:
            app.dependency_overrides.pop(verify_token, None)
        assert response.status_code == 403
        assert fake.calls == []


def test_mmc_utils_is_a_host_tool_that_install_sh_installs():
    assert host.HOST_TOOLS["mmc"] == "mmc-utils"
