"""The wear and the failure signs of an eMMC system disk, read and alerted, never acted on (ADR-0471,
phase 1; ADR-0475).

The root filesystem's disk is found once per run of the supervisor: the mount of ``/`` in
/proc/1/mountinfo (the host's own mount namespace), its device number, and the block device's
folder in sysfs. When it is an eMMC (``mmcblk<N>`` whose ``device/type`` is ``MMC``):

- the wear, the life-time estimates A and B and the pre-EOL state, comes from ``mmc extcsd read``
  at the first pass and then once a day; without mmc-utils, or when it fails before its first
  reading, from the sysfs files ``life_time`` and ``pre_eol_info``, which the kernel read when it
  started;
- at each pass (``monitor_pass``, every 300 s in ``system_disk_loop``, beside the RAID monitor) the
  kernel's I/O errors of the eMMC since the supervisor started (``journalctl --dmesg``), ext4's
  errors on the root since then (``errors_count`` against its value at the first read) and whether
  the root's superblock is read-only (the superblock options in /proc/1/mountinfo, which a device
  check's read-only mount namespace, ADR-0221, does not change);
- while I/O errors of the eMMC were logged, a direct read (``O_DIRECT``) of the root partition's
  first MiB tells whether it still reads. Once it failed it is not read again: the failure stays
  until the supervisor restarts.

Problems of the source ``system_disk`` (ADR-0332): ``emmc_worn``, ``system_disk_errors`` and
``system_disk_failed``, the last in place of the second. Every read goes through ``src/host.py``
and writes nothing; what was read is kept in RAM only (ADR-0214), so that the alert of a failing
disk needs no write to it.
"""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from . import problems
from .. import host
from ..times import utc_now, utc_text

logger = logging.getLogger(__name__)

SOURCE = "system_disk"
# The wear is read again once a day; the errors at each pass of the RAID monitor.
WEAR_INTERVAL = 24 * 3600
MOUNTINFO = "/proc/1/mountinfo"
# The host's timeout(1) ends mmc, journalctl and dd; the supervisor waits a little longer, since a
# read stuck in the kernel on a failing eMMC may not end at all (ADR-0258).
COMMAND_TIMEOUT = 20
JOURNAL_TIMEOUT = 30
KILL_AFTER = 5
TIMED_OUT = 124
NOT_FOUND = 127
SECTOR = 512
# The estimates' values (JEDEC eMMC 5.0, EXT_CSD bytes 268 and 269): 0x01 to 0x0A each a tenth of
# the life used, 0x0B past it; 0x00 not defined, higher values reserved. The pre-EOL state (byte 267):
# 0x01 normal, 0x02 warning (80% of the reserved blocks used), 0x03 urgent.
LIFE_EXCEEDED = 0x0B
WORN_WARNING = 0x08
WORN_ERROR = 0x0A
PRE_EOL_WARNING = 0x02
PRE_EOL_URGENT = 0x03

_EMMC_DISK = re.compile(r"mmcblk[0-9]+")
_EMMC_DEVICE = re.compile(r"mmcblk[0-9]+(?:p[0-9]+)?")
_BLOCK_NAME = re.compile(r"[A-Za-z0-9._:-]+")
_EXTCSD = {
    name: re.compile(rf"\[{field_name}\]:\s*0x([0-9a-fA-F]+)")
    for name, field_name in (
        ("a", "EXT_CSD_DEVICE_LIFE_TIME_EST_TYP_A"),
        ("b", "EXT_CSD_DEVICE_LIFE_TIME_EST_TYP_B"),
        ("eol", "EXT_CSD_PRE_EOL_INFO"),
    )
}

Reason = Literal["not_emmc", "no_disk", "unreadable"]


class SystemDiskProblem(BaseModel):
    """A problem of the system disk that the last pass found."""

    kind: Literal["emmc_worn", "system_disk_errors", "system_disk_failed"] = Field(description=(
        "emmc_worn: the eMMC has used most of its life; system_disk_errors: I/O errors of the eMMC or new ext4 "
        "errors of the root; system_disk_failed: the root is read-only after a new ext4 error, or I/O errors and a "
        "failed direct read."))
    severity: problems.Severity = Field(description="error or warning.")


class EmmcHealth(BaseModel):
    """The eMMC that holds the root filesystem: what it is, its wear and its errors."""

    device: str = Field(description="The eMMC, such as /dev/mmcblk0.")
    model: str | None = Field(description="The eMMC's product name, from sysfs; null when it reports none.")
    size: int = Field(description="Its size in bytes.")
    root: str = Field(description="The device of the root filesystem, such as /dev/mmcblk0p1.")
    filesystem: str = Field(description="The root filesystem's type, such as ext4.")
    life_time_a: int | None = Field(description=(
        "The life-time estimate A (SLC area) as the eMMC reports it: 1 to 10, each a tenth of its life used "
        "(1: 0 to 10%), 11 past its rated life, 0 not defined; null when it could not be read."))
    life_time_b: int | None = Field(description="The life-time estimate B (MLC area), as life_time_a.")
    pre_eol: int | None = Field(description=(
        "The pre-EOL state of the reserved blocks: 1 normal, 2 warning, 3 urgent, 0 not defined; null when it could "
        "not be read."))
    wear_source: Literal["mmc", "sysfs"] | None = Field(description=(
        "mmc: read with mmc extcsd read at wear_read_at; sysfs: the values the kernel read at boot (no mmc-utils, or "
        "it failed); null: not read."))
    wear_read_at: str | None = Field(description="When the wear was read, in UTC; null when it was not.")
    mmc_missing: bool = Field(description="Whether the host lacks mmc-utils, so that the wear is the kernel's of the boot.")
    wear_error: str | None = Field(description="Why mmc extcsd read, or also sysfs, gave no wear; null when it did.")
    io_errors: int | None = Field(description=(
        "The kernel's I/O errors of the eMMC since the supervisor started; null until the journal was read."))
    ext4_errors: int | None = Field(description=(
        "The errors that ext4 counted on the root since the supervisor started; null for another filesystem or "
        "until they were read."))
    read_only: bool | None = Field(description="Whether the root's superblock is read-only; null until it was read.")
    direct_read_failed: bool = Field(description=(
        "Whether a direct read of the root partition's first MiB failed after I/O errors of the eMMC."))
    checked_at: str | None = Field(description="When the errors were last read, in UTC.")
    check_error: str | None = Field(description="What the last pass could not read, kept from before; null when it read all.")


class SystemDiskHealth(BaseModel):
    """The health of the system disk when it is an eMMC, or why it is not shown."""

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "emmc": {
            "device": "/dev/mmcblk0", "model": "BJTD4R", "size": 31268536320, "root": "/dev/mmcblk0p1",
            "filesystem": "ext4", "life_time_a": 1, "life_time_b": 1, "pre_eol": 1, "wear_source": "mmc",
            "wear_read_at": "2026-11-23T08:00:04.120391Z", "mmc_missing": False, "wear_error": None,
            "io_errors": 0, "ext4_errors": 0, "read_only": False, "direct_read_failed": False,
            "checked_at": "2026-11-23T10:05:04.533201Z", "check_error": None,
        },
        "reason": None, "system_disk": None, "detail": None, "problems": [],
    }]})

    emmc: EmmcHealth | None = Field(description="The eMMC of the root filesystem; null when the system disk is none.")
    reason: Reason | None = Field(description=(
        "Why emmc is null: not_emmc, the root is on another disk (system_disk); no_disk, it is on no block device; "
        "unreadable, the root's disk could not be read (detail), and is read again at the next pass."))
    system_disk: str | None = Field(description="With not_emmc: the disk of the root, such as /dev/sda.")
    detail: str | None = Field(description="With no_disk and unreadable: what was found or what failed, in English.")
    problems: list[SystemDiskProblem] = Field(description="The problems of the system disk that the last reads show.")


@dataclass(frozen=True)
class _Root:
    """The mount of / in the host's mount namespace."""

    majmin: str
    fstype: str
    source: str
    read_only: bool


@dataclass(frozen=True)
class _Emmc:
    disk: str  # mmcblk0
    root: str  # mmcblk0p1, or the disk itself
    fstype: str
    model: str | None
    size: int


@dataclass
class _State:
    """What the passes read since the supervisor started; in RAM only."""

    started: float = field(default_factory=time.time)
    emmc: _Emmc | None = None
    # Why there is no eMMC: final for not_emmc and no_disk, read again for unreadable.
    reason: Reason | None = None
    system_disk: str | None = None
    detail: str | None = None
    life_time_a: int | None = None
    life_time_b: int | None = None
    pre_eol: int | None = None
    wear_source: Literal["mmc", "sysfs"] | None = None
    wear_read_at: str | None = None
    wear_read_monotonic: float | None = None
    mmc_missing: bool = False
    wear_error: str | None = None
    ext4_at_start: int | None = None
    ext4_errors: int | None = None
    io_errors: int | None = None
    read_only: bool | None = None
    direct_read_failed: bool = False
    checked_at: str | None = None
    check_error: str | None = None


_state = _State()
_lock = threading.RLock()
# The tests move it.
_monotonic = time.monotonic


def _read(path: str) -> str:
    """A file of the host; raises what run_on_host raises, HostCommandError for a failed cat."""
    return host.read_host_file(path)


def _why(error: Exception) -> str:
    """The text of a failed read, short."""
    if isinstance(error, host.HostCommandError):
        text = error.stderr.strip() or f"exit status {error.returncode}"
    elif isinstance(error, subprocess.TimeoutExpired):
        text = "no answer in time"
    else:
        text = str(error) or type(error).__name__
    return text if len(text) <= 300 else text[:300] + "..."


def _root_mount(mountinfo: str) -> _Root | None:
    """The topmost mount of / in a mountinfo text; None without one."""
    found = None
    for line in mountinfo.splitlines():
        mount, separator, filesystem = line.partition(" - ")
        fields, rest = mount.split(), filesystem.split()
        if not separator or len(fields) < 6 or len(rest) < 3 or fields[4] != "/":
            continue
        found = _Root(majmin=fields[2], fstype=rest[0], source=rest[1], read_only="ro" in rest[2].split(","))
    return found


def _block_names(link: str) -> tuple[str, str] | None:
    """(disk, device) of a sysfs link to a block device's folder: ".../block/mmcblk0/mmcblk0p1" gives
    (mmcblk0, mmcblk0p1), ".../block/sda" (sda, sda); None for another link."""
    parts = link.strip().split("/")
    if "block" not in parts:
        return None
    names = parts[len(parts) - 1 - parts[::-1].index("block") + 1:]
    if not 1 <= len(names) <= 2 or not all(_BLOCK_NAME.fullmatch(name) for name in names):
        return None
    return names[0], names[-1]


def _identify(state: _State) -> None:
    """Find the root's disk and, for an eMMC, its model and size."""
    try:
        root = _root_mount(_read(MOUNTINFO))
    except Exception as exc:
        state.reason, state.detail = "unreadable", f"{MOUNTINFO} could not be read: {_why(exc)}"
        return
    if root is None:
        state.reason, state.detail = "unreadable", f"{MOUNTINFO} names no mount of /"
        return
    names = None
    candidates = [f"/sys/dev/block/{root.majmin}"]
    if root.source.startswith("/dev/") and _BLOCK_NAME.fullmatch(root.source[5:]):
        candidates.append(f"/sys/class/block/{root.source[5:]}")
    for link in candidates:
        try:
            result = host.run_on_host(["readlink", link], timeout=10)
        except (OSError, subprocess.SubprocessError) as exc:
            state.reason, state.detail = "unreadable", f"{link} could not be read: {_why(exc)}"
            return
        # readlink without -v exits 1 and says nothing for a path that is no link: no block device
        # there. Anything else (nsenter's own failure) is read again at the next pass.
        if not result.ok and (result.returncode != 1 or result.stderr.strip()):
            why = result.stderr.strip()[:300] or f"exit status {result.returncode}"
            state.reason, state.detail = "unreadable", f"{link} could not be read: {why}"
            return
        if result.ok and (names := _block_names(result.stdout)):
            break
    if names is None:
        state.reason = "no_disk"
        state.detail = f"The root is {root.fstype} on {root.source}, which is no block device of the host."
        return
    disk, device = names
    if not (_EMMC_DISK.fullmatch(disk) and _EMMC_DEVICE.fullmatch(device)):
        state.reason, state.system_disk = "not_emmc", f"/dev/{disk}"
        return
    try:
        kind = _read(f"/sys/block/{disk}/device/type").strip()
        size = int(_read(f"/sys/block/{disk}/size").strip()) * SECTOR
    except Exception as exc:
        state.reason, state.detail = "unreadable", f"The disk {disk} could not be read: {_why(exc)}"
        return
    if kind != "MMC":
        state.reason, state.system_disk = "not_emmc", f"/dev/{disk}"
        return
    try:
        model = _read(f"/sys/block/{disk}/device/name").strip() or None
    except Exception:
        model = None
    state.reason = state.detail = state.system_disk = None
    state.emmc = _Emmc(disk=disk, root=device, fstype=root.fstype, model=model, size=size)


def _hex(text: str | None) -> int | None:
    try:
        return int(text, 16) if text is not None else None
    except ValueError:
        return None


def _read_wear(state: _State, emmc: _Emmc) -> bool:
    """The estimates and the pre-EOL state from EXT_CSD; whether they were read now. When mmc fails,
    an earlier read of it is kept (sysfs dates from the boot, which is older), else sysfs is read (as
    of boot); wear_error says why."""
    cmd = ["timeout", f"--kill-after={KILL_AFTER}", str(COMMAND_TIMEOUT), "mmc", "extcsd", "read", f"/dev/{emmc.disk}"]
    mmc_error = None
    state.mmc_missing = False
    try:
        result = host.run_on_host(cmd, timeout=COMMAND_TIMEOUT + KILL_AFTER + 5, errors="replace")
    except (OSError, subprocess.SubprocessError) as exc:
        mmc_error = f"mmc extcsd read failed: {_why(exc)}"
    else:
        values = {name: _hex(match.group(1)) if (match := pattern.search(result.stdout)) else None
                  for name, pattern in _EXTCSD.items()}
        if result.ok and any(value is not None for value in values.values()):
            state.life_time_a, state.life_time_b, state.pre_eol = values["a"], values["b"], values["eol"]
            state.wear_source, state.wear_error = "mmc", None
            return True
        if result.ok:
            mmc_error = "mmc extcsd read printed no life-time estimates"
        elif result.returncode == NOT_FOUND:
            state.mmc_missing = True
            mmc_error = "mmc-utils is not installed on the host" if state.wear_source == "mmc" else None
        elif result.returncode == TIMED_OUT:
            mmc_error = f"mmc extcsd read did not answer within {COMMAND_TIMEOUT} seconds"
        else:
            mmc_error = "mmc extcsd read failed: " + (result.stderr.strip()[:300] or f"exit status {result.returncode}")
    if state.wear_source == "mmc":
        state.wear_error = mmc_error
        return False
    try:
        life_time = _read(f"/sys/block/{emmc.disk}/device/life_time").split()
        pre_eol = _read(f"/sys/block/{emmc.disk}/device/pre_eol_info").strip()
    except Exception as exc:
        state.life_time_a = state.life_time_b = state.pre_eol = None
        state.wear_source = None
        state.wear_error = "; ".join(filter(None, [mmc_error, f"the kernel reports no wear: {_why(exc)}"]))
        return False
    state.life_time_a = _hex(life_time[0]) if life_time else None
    state.life_time_b = _hex(life_time[1]) if len(life_time) > 1 else None
    state.pre_eol = _hex(pre_eol)
    state.wear_source, state.wear_error = "sysfs", mmc_error
    return True


def _io_error_pattern(disk: str) -> str:
    """The kernel's lines of a failed I/O of the eMMC or one of its partitions, as PCRE2 and Python
    read it: "I/O error, dev mmcblk0, sector ..." and "Buffer I/O error on dev mmcblk0p1, ..."."""
    return rf"I/O error(,| on) dev {disk}(p[0-9]+|boot[0-9]+|rpmb)?\b"


def _count_io_errors(state: _State, emmc: _Emmc) -> str | None:
    """Count the I/O errors of the eMMC that the kernel logged since the supervisor started; what failed."""
    pattern = _io_error_pattern(emmc.disk)
    cmd = [
        "timeout", f"--kill-after={KILL_AFTER}", str(JOURNAL_TIMEOUT), "journalctl", "--no-pager", "--quiet",
        "--dmesg", "--output=cat", f"--since=@{int(state.started)}", f"--grep={pattern}", "--case-sensitive=true",
    ]
    try:
        result = host.run_on_host(cmd, timeout=JOURNAL_TIMEOUT + KILL_AFTER + 5, errors="replace")
    except (OSError, subprocess.SubprocessError) as exc:
        return f"The kernel's log could not be read: {_why(exc)}"
    if result.returncode == 1 and not result.stdout.strip() and not result.stderr.strip():
        state.io_errors = state.io_errors or 0  # --grep found nothing
        return None
    if not result.ok:
        why = result.stderr.strip()[:300] or f"journalctl exited with status {result.returncode}"
        return f"The kernel's log could not be read: {why}"
    # The journal of a small RAM disk may drop old entries: the count since the start never falls.
    counted = sum(1 for line in result.stdout.split("\n") if re.search(pattern, line))
    state.io_errors = max(counted, state.io_errors or 0)
    return None


def _read_errors(state: _State, emmc: _Emmc) -> None:
    """The root's state, ext4's errors and the kernel's I/O errors; a direct read after I/O errors."""
    failures = []
    try:
        root = _root_mount(_read(MOUNTINFO))
        if root is None:
            failures.append(f"{MOUNTINFO} names no mount of /")
        else:
            state.read_only = root.read_only
    except Exception as exc:
        failures.append(f"{MOUNTINFO} could not be read: {_why(exc)}")
    if emmc.fstype == "ext4":
        try:
            count = int(_read(f"/sys/fs/ext4/{emmc.root}/errors_count").strip())
        except Exception as exc:
            failures.append(f"ext4's error count could not be read: {_why(exc)}")
        else:
            if state.ext4_at_start is None:
                state.ext4_at_start = count
            state.ext4_errors = max(0, count - state.ext4_at_start)
    if failure := _count_io_errors(state, emmc):
        failures.append(failure)
    if state.io_errors and not state.direct_read_failed:
        read = _read_directly(emmc)
        if read is False:
            state.direct_read_failed = True
        elif read is not True:
            failures.append(read)
    state.check_error = "; ".join(failures) or None
    state.checked_at = utc_text(utc_now())


def _read_directly(emmc: _Emmc) -> bool | str:
    """Whether the first MiB of the root's partition reads with O_DIRECT, past the page cache: True,
    False when dd failed on the device or did not end in time, or why the read could not be made
    (the host could not start it), which the next pass tries again."""
    cmd = [
        "timeout", f"--kill-after={KILL_AFTER}", str(COMMAND_TIMEOUT), "dd", f"if=/dev/{emmc.root}", "of=/dev/null",
        "bs=1M", "count=1", "iflag=direct", "status=none",
    ]
    try:
        result = host.run_on_host(cmd, timeout=COMMAND_TIMEOUT + KILL_AFTER + 5, errors="replace")
    except subprocess.TimeoutExpired:
        return False
    except (OSError, subprocess.SubprocessError) as exc:
        return f"The direct read of /dev/{emmc.root} could not run: {_why(exc)}"
    if result.ok:
        return True
    # timeout(1) ended dd (124), or killed it and died by the KILL too (-9, 137 through a shell); dd
    # itself says what failed on the device (exit 1, "dd: error reading ...", "dd: failed to open ...").
    if result.returncode in (TIMED_OUT, -9, 137) or (result.returncode == 1 and result.stderr.lstrip().startswith("dd:")):
        return False
    why = result.stderr.strip()[:300] or f"exit status {result.returncode}"
    return f"The direct read of /dev/{emmc.root} could not run: {why}"


def check() -> None:
    """One pass (blocking: run it in a worker thread): find the root's disk until it is known, read
    the wear when it is due, and the errors."""
    with _lock:
        state = _state
        if state.emmc is None:
            if state.reason in ("not_emmc", "no_disk"):
                return
            _identify(state)
            if state.emmc is None:
                state.checked_at = utc_text(utc_now())
                return
        emmc = state.emmc
        now = _monotonic()
        if state.wear_read_monotonic is None or now - state.wear_read_monotonic >= WEAR_INTERVAL:
            if _read_wear(state, emmc):
                state.wear_read_at = utc_text(utc_now())
            # A wear that neither mmc nor sysfs gave is read again at the next pass; a failed mmc
            # beside an earlier read of it, in a day.
            if state.wear_source is not None:
                state.wear_read_monotonic = now
        _read_errors(state, emmc)


def _life_text(value: int) -> str:
    return "more than its rated life" if value >= LIFE_EXCEEDED else f"{(value - 1) * 10} to {value * 10}% of its life"


_PRE_EOL_WORDS = {1: "normal", 2: "warning", 3: "urgent"}


def _judged(value: int | None) -> int | None:
    """An estimate that tells something: 1 to 11; None for not defined and the reserved values."""
    return value if value is not None and 1 <= value <= LIFE_EXCEEDED else None


def _found(state: _State) -> dict[str, problems.Problem]:
    """The problems that the state shows, by key."""
    emmc = state.emmc
    if emmc is None:
        return {}
    device, root = f"/dev/{emmc.disk}", f"/dev/{emmc.root}"
    found: dict[str, problems.Problem] = {}

    def add(kind: str, severity: problems.Severity, title: str, detail: str, stage: int = 0) -> None:
        key = f"{kind}:{device}"
        found[key] = problems.Problem(key=key, severity=severity, kind=kind, subject=device, title=title,
                                      detail=detail, stage=stage)

    estimates = [value for value in (_judged(state.life_time_a), _judged(state.life_time_b)) if value]
    worst = max(estimates, default=0)
    eol = state.pre_eol
    if worst >= WORN_ERROR or eol == PRE_EOL_URGENT or worst >= WORN_WARNING or eol == PRE_EOL_WARNING:
        error = worst >= WORN_ERROR or eol == PRE_EOL_URGENT
        used = f"{_life_text(worst)} is used" if worst else "its life used is not reported"
        eol_text = _PRE_EOL_WORDS.get(eol or 0, "not reported")
        detail = (
            f"The eMMC {device} that holds the system estimates that {used} (life-time estimates A "
            f"0x{state.life_time_a or 0:02x}, B 0x{state.life_time_b or 0:02x}; pre-EOL {eol_text}). "
            + ("Replace it soon: its writes may start to fail. Keep the off-device copies current."
               if error else "Plan its replacement, and keep the off-device copies current.")
        )
        if error:
            add("emmc_worn", "error", f"The system's eMMC {device} is nearly worn out", detail, stage=2)
        else:
            add("emmc_worn", "warning", f"The system's eMMC {device} is wearing out", detail, stage=1)

    io_errors, ext4_errors = state.io_errors or 0, state.ext4_errors or 0
    if state.read_only and ext4_errors:
        add("system_disk_failed", "error", f"The system disk {device} is failing",
            f"ext4 counted {ext4_errors} new error(s) on the root filesystem {root} and made it read-only: the host "
            "and UNOS can no longer save changes. Check the kernel log (System > Logs, Kernel), and replace the eMMC "
            "if the errors come back after a restart.")
    elif state.direct_read_failed:
        add("system_disk_failed", "error", f"The system disk {device} is failing",
            f"The kernel logged {io_errors} I/O error(s) of the eMMC since the supervisor started, and a direct read "
            f"of the first MiB of {root} failed. Replace the eMMC.")
    elif io_errors or ext4_errors:
        add("system_disk_errors", "warning", f"The system disk {device} reports errors",
            f"Since the supervisor started, the kernel logged {io_errors} I/O error(s) of the eMMC and ext4 counted "
            f"{ext4_errors} new error(s) on the root filesystem {root}. Check the kernel log (System > Logs, "
            "Kernel).")
    return found


async def monitor_pass() -> None:
    """One pass: read (in a worker thread), then report the problems."""
    await asyncio.to_thread(check)
    await problems.update(SOURCE, _found(_state))


# The pass runs in a loop of its own beside the RAID monitor, at its interval (ADR-0475): on a failing
# eMMC a pass, or the history write of its alert, can take long, and the pools' checks do not wait for it.
CHECK_INTERVAL = 300
_loop_task: asyncio.Task | None = None


async def system_disk_loop() -> None:
    """The system disk's wear and failure signs, every CHECK_INTERVAL seconds from the start."""
    while True:
        try:
            await monitor_pass()
        except Exception:
            logger.exception("System disk monitor error")
        await asyncio.sleep(CHECK_INTERVAL)


def start_monitor() -> None:
    """Start the loop (the lifespan, through health.router.start_health_monitors)."""
    global _loop_task
    _loop_task = asyncio.create_task(system_disk_loop())


def stop_monitor() -> None:
    """Stop the loop."""
    if _loop_task is not None:
        _loop_task.cancel()


def _first_check() -> None:
    """The pass of a request that came before any: none when one ran meanwhile."""
    with _lock:
        if _state.checked_at is None:
            check()


async def health() -> SystemDiskHealth:
    """What GET /health/system-disk answers: the last reads, made now when no pass has run yet. The
    state is read without the lock, which a pass on a failing eMMC may hold for long: a field may be a
    pass newer than another."""
    if _state.checked_at is None:
        await asyncio.to_thread(_first_check)
    state = _state
    emmc = state.emmc
    found = sorted(_found(state).values(), key=lambda problem: problem.severity != "error")
    return SystemDiskHealth(
        emmc=None if emmc is None else EmmcHealth(
            device=f"/dev/{emmc.disk}", model=emmc.model, size=emmc.size, root=f"/dev/{emmc.root}",
            filesystem=emmc.fstype, life_time_a=state.life_time_a, life_time_b=state.life_time_b,
            pre_eol=state.pre_eol, wear_source=state.wear_source, wear_read_at=state.wear_read_at,
            mmc_missing=state.mmc_missing, wear_error=state.wear_error, io_errors=state.io_errors,
            ext4_errors=state.ext4_errors, read_only=state.read_only,
            direct_read_failed=state.direct_read_failed, checked_at=state.checked_at,
            check_error=state.check_error,
        ),
        reason=None if emmc is not None else state.reason,
        system_disk=state.system_disk if emmc is None else None,
        detail=state.detail if emmc is None else None,
        problems=[SystemDiskProblem.model_validate({"kind": problem.kind, "severity": problem.severity})
                  for problem in found],
    )
