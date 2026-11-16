"""Idle timeouts of the data disks, and which pools sleep (storage.disk-spindown, ADR-0378).

An administrator gives a whole data disk an idle timeout on the Storage page: Off or one of
SPINDOWN_MINUTES, which ``hdparm -S`` expresses. UNOS applies it at once with hdparm on the host
and keeps it in its udev rule RULES_PATH, one line per disk matched by its serial, which runs
``hdparm -S`` again whenever udev adds the disk: at boot, before the supervisor runs, and when
the disk is plugged in again. The rule is the setting's only home: GET /storage/disks reads it
back, as the pools' fstab entries are theirs (ADR-0283), so nothing can disagree with what the
host does. hdparm is an optional host tool (host.OPTIONAL_HOST_TOOLS): without it the Storage
page says so (NO_HDPARM).

``hdparm -S`` sends the disk the IDLE command with the timer, which spins a sleeping disk up: it
runs only when an administrator sets a timeout, never periodically.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass

from fastapi import HTTPException

from .. import host
from ..config import get_settings
from ..security.validators import validate_disk_target
from . import disks
from .schemas import SPINDOWN_MINUTES

logger = logging.getLogger(__name__)

RULES_PATH = "/etc/udev/rules.d/90-unos-disk-spindown.rules"
# Where hdparm is on every supported host: /usr/sbin with a merged /usr (/sbin links to it), /sbin
# on a Debian 11 without one. udev runs a program of RUN by its absolute path.
HDPARM_PATH = "/sbin/hdparm"
# Seconds hdparm -S may take: it answers at once, unless the disk hangs; it runs under the host's
# timeout (ADR-0258), which kills it this many seconds after its TERM.
HDPARM_TIMEOUT = 30
KILL_AFTER = 5
UDEV_TIMEOUT = 15

NO_HDPARM = "The host has no hdparm: install the package hdparm on the host to set idle timeouts"

_HEADER = (
    "# Written by UNOS: the idle timeouts of the data disks, set on the Storage page (ADR-0378).\n"
    "# Each line sets the standby timer of one disk, found by its serial, with hdparm -S whenever\n"
    "# udev adds the disk: at boot and when it is connected again. UNOS writes this file at each\n"
    "# change of a timeout and keeps only its own lines; without a timeout it removes the file.\n"
)
_RULE = (
    'ACTION=="add", SUBSYSTEM=="block", ENV{{DEVTYPE}}=="disk", ENV{{ID_SERIAL_SHORT}}=="{serial}", '
    'RUN+="' + HDPARM_PATH + ' -S {value} $devnode"'
)
_RULE_LINE = re.compile(
    r'ACTION=="add", SUBSYSTEM=="block", ENV\{DEVTYPE\}=="disk", ENV\{ID_SERIAL_SHORT\}=="(?P<serial>[^"]+)", '
    r'RUN\+="' + re.escape(HDPARM_PATH) + r' -S (?P<value>\d+) \$devnode"'
)
# A serial that a udev rule matches as it is: udev's ID_SERIAL_SHORT keeps letters, digits and
# "#+-.:=@_" (it replaces the rest), and none of them is a pattern character of a rule's match.
_RULE_SERIAL = re.compile(r"[A-Za-z0-9#+.:=@_-]{1,64}")

# One change of the rule file at a time: each reads it, changes its line and writes it.
_lock = threading.Lock()


def hdparm_value(minutes: int) -> int:
    """The ``hdparm -S`` value of an idle timeout: 1 to 240 are multiples of 5 seconds (up to 20
    minutes), 241 to 251 multiples of 30 minutes (30 minutes to 5.5 hours); 0 is off."""
    if minutes not in SPINDOWN_MINUTES:
        raise ValueError(f"{minutes} minutes is not an offered idle timeout")
    return minutes * 12 if minutes <= 20 else 240 + minutes // 30


def minutes_of(value: int) -> int | None:
    """The idle timeout in whole minutes that the ``hdparm -S`` value sets, or None when it is
    none: less than a minute, or one of the special values from 252 on."""
    if value == 0:
        return 0
    if 1 <= value <= 240:
        return value * 5 // 60 if value * 5 % 60 == 0 else None
    if 241 <= value <= 251:
        return (value - 240) * 30
    return None


def _words(minutes: int) -> str:
    if minutes % 60:
        return f"{minutes} minutes"
    hours = minutes // 60
    return f"{hours} hour{'' if hours == 1 else 's'}"


# --- the rule file ---------------------------------------------------------------------------


def read_timeouts() -> dict[str, int]:
    """The idle timeouts of UNOS's udev rule: serial -> minutes, in the file's order; {} without
    the file. A line that UNOS did not write is no timeout, nor is one whose timeout is not offered
    (SPINDOWN_MINUTES): one edited by hand. RuntimeError when the file cannot be read."""
    result = host.run_on_host(["cat", "--", RULES_PATH], timeout=UDEV_TIMEOUT, errors="replace")
    if not result.ok:
        if "No such file or directory" in result.stderr:
            return {}
        raise RuntimeError(f"Could not read {RULES_PATH}: {result.stderr.strip() or result.returncode}")
    found: dict[str, int] = {}
    for line in result.stdout.splitlines():
        match = _RULE_LINE.fullmatch(line.strip())
        if match and _RULE_SERIAL.fullmatch(match["serial"]):
            minutes = minutes_of(int(match["value"]))
            if minutes and minutes in SPINDOWN_MINUTES:
                found[match["serial"]] = minutes
    return found


def timeouts_or_problem() -> tuple[dict[str, int], str | None]:
    """read_timeouts(), and None; or, with a warning, {} and why the rule could not be read, which
    the disk list gives as each disk's spindown_problem."""
    try:
        return read_timeouts(), None
    except Exception as exc:
        logger.warning("Could not read the idle timeouts of the disks: %s", exc)
        return {}, f"The idle timeouts could not be read: {str(exc).strip() or type(exc).__name__}"


def render(timeouts: dict[str, int]) -> str:
    """The rule file for ``timeouts`` (serial -> minutes): its header and one line per disk."""
    return _HEADER + "".join(
        _RULE.format(serial=serial, value=hdparm_value(minutes)) + "\n" for serial, minutes in timeouts.items()
    )


def hdparm_missing() -> bool:
    """Whether the host has no hdparm; False when the check itself fails (the change then says why)."""
    return bool(host.missing_host_binaries(["hdparm"]))


# --- which disk can have one -------------------------------------------------------------------


def problem(facts: disks.DeviceFacts, listed: list[tuple[str, str | None]]) -> str | None:
    """Why the whole disk of ``facts`` gets no idle timeout, or None. ``listed``: the path and the
    serial (lsblk's) of each whole disk of the Storage page, to find another with the same serial."""
    if facts.is_system:
        return f"{facts.path} is the system disk ({facts.in_use_reason}): it gets no idle timeout"
    if facts.name.startswith("nvme"):
        return (f"{facts.path} is an NVMe disk, which manages its own power: hdparm sets idle timeouts of "
                "SATA disks")
    if not facts.serial or not _RULE_SERIAL.fullmatch(facts.serial):
        return (f"{facts.path} reports no serial number that a udev rule can match: UNOS could not set its "
                "idle timeout again when it is connected")
    twin = next((path for path, serial in listed if serial == facts.serial and path != facts.path), None)
    if twin:
        return f"{twin} reports the serial {facts.serial} too: a udev rule could not tell {facts.path} from it"
    return None


def _udev_serial(device: str) -> str | None:
    """udev's ID_SERIAL_SHORT of ``device``, which the rule matches, from udev's database (no disk is read)."""
    result = host.run_on_host(["udevadm", "info", "--query=property", "--name", device], timeout=UDEV_TIMEOUT)
    if not result.ok:
        raise RuntimeError(f"udevadm info failed for {device}: {result.stderr.strip() or result.returncode}")
    for line in result.stdout.splitlines():
        key, _, value = line.partition("=")
        if key == "ID_SERIAL_SHORT":
            return value.strip() or None
    return None


# --- setting a timeout -------------------------------------------------------------------------


@dataclass(frozen=True)
class Change:
    """A timeout set: the disk's name and path, its serial, and whether the rule file changed."""

    name: str
    path: str
    serial: str
    minutes: int
    changed: bool

    @property
    def what(self) -> str:
        """The change in words, for the audit log."""
        if not self.minutes:
            return f"Turned off the idle timeout of {self.path} (serial {self.serial})"
        return f"Set the idle timeout of {self.path} (serial {self.serial}) to {_words(self.minutes)}"


class RuleNotWritten(Exception):
    """hdparm set the timeout, but the udev rule could not be changed: the disk has it until it restarts."""

    def __init__(self, change: Change, reason: str) -> None:
        super().__init__(reason)
        self.change = change
        self.reason = reason

    @property
    def detail(self) -> str:
        has = (f"the idle timeout of {_words(self.change.minutes)}" if self.change.minutes
               else "no idle timeout")
        return f"{self.change.path} has {has} until it restarts: the udev rule could not be written: {self.reason}"


def set_timeout(name: str, minutes: int) -> Change:
    """Give the whole data disk ``name`` (as GET /storage/disks lists it) the idle timeout
    ``minutes`` (0: none): ``hdparm -S`` on the host now, then UNOS's udev rule, written only
    when the disk's line changes, after which udev reads its rules again. Blocking: call it in a
    worker thread.

    HTTPException 400 for a name that is no disk of this system, a boot or virtual device
    (HIDDEN_DISK_PREFIXES) and a partition; 409 for a disk that gets no timeout (problem()), a host
    without hdparm, and a disk that udev knows by another serial than lsblk; 500 when hdparm fails
    or the rule cannot be read, and nothing is changed then. RuleNotWritten when hdparm set it but
    the rule could not be written.
    """
    shown = name.removeprefix("/dev/")
    if disks.is_hidden_disk(shown):
        raise HTTPException(status_code=400, detail=f"{shown} is a boot or virtual device: it gets no idle timeout")
    device = validate_disk_target(name)
    try:
        facts = disks.get_device_facts([device])[device]
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{shown} is not a disk of this system") from None
    if facts.hidden:
        raise HTTPException(status_code=400, detail=f"{shown} is a boot or virtual device: it gets no idle timeout")
    if facts.type != "disk":
        raise HTTPException(status_code=400, detail=f"{shown} is a partition: idle timeouts are set for whole disks")
    reason = problem(facts, [(disk.path, disk.serial) for disk in disks.host_disks()])
    if reason:
        raise HTTPException(status_code=409, detail=reason)
    if hdparm_missing():
        raise HTTPException(status_code=409, detail=NO_HDPARM)
    serial = facts.serial or ""
    udev = _udev_serial(facts.path)
    if udev != serial:
        raise HTTPException(
            status_code=409,
            detail=f"udev knows {facts.path} by the serial {udev or '(none)'}, not {serial}: a udev rule "
            "could not find it again",
        )
    value = hdparm_value(minutes)
    with _lock:
        current = read_timeouts()
        cmd = ["timeout", f"--kill-after={KILL_AFTER}", str(HDPARM_TIMEOUT), "hdparm", "-S", str(value), facts.path]
        result = host.run_on_host(cmd, timeout=HDPARM_TIMEOUT + KILL_AFTER + 5, errors="replace")
        if not result.ok:
            why = (result.stderr.strip() or result.stdout.strip() or f"exit status {result.returncode}").splitlines()
            raise HTTPException(
                status_code=500, detail=f"hdparm could not set the idle timeout of {facts.path}: {why[-1].strip()}"
            )
        wanted = dict(current)  # a disk's line keeps its place; a new one comes last
        if minutes:
            wanted[serial] = minutes
        else:
            wanted.pop(serial, None)
        change = Change(shown, facts.path, serial, minutes, changed=wanted != current)
        if change.changed:
            try:
                if wanted:
                    host.write_host_file_atomic(RULES_PATH, render(wanted))
                else:
                    host.run_on_host(["rm", "-f", "--", RULES_PATH], timeout=UDEV_TIMEOUT, check=True)
            except Exception as exc:
                raise RuleNotWritten(change, str(exc).strip() or type(exc).__name__) from exc
            _reload_rules()
    return change


def _reload_rules() -> None:
    """udev reads its rules again; it notices a changed rules folder by itself too, so a failure is
    only logged: the rule applies at the disk's next appearance either way, at the latest at boot."""
    try:
        result = host.run_on_host(["udevadm", "control", "--reload"], timeout=UDEV_TIMEOUT)
    except Exception as exc:
        logger.warning("udevadm control --reload failed: %s", exc)
        return
    if not result.ok:
        logger.warning("udevadm control --reload failed: %s", result.stderr.strip() or result.returncode)


# --- a pool that sleeps ------------------------------------------------------------------------


@dataclass(frozen=True)
class Asleep:
    """A disk of a pool that pool_sleeps found in standby: its path, and the reads and writes the
    kernel had counted on it then (disks.io_counts), None when they could not be read."""

    disk: str
    io: tuple[int, int] | None


def _io_of(disk: str) -> tuple[int, int] | None:
    """The reads and writes the kernel counted on the whole disk at the path ``disk``."""
    return disks.io_counts().get(disk.removeprefix("/dev/"))


def pool_sleeps(pool: str, asleep: dict[str, Asleep] | None = None) -> bool:
    """Whether a disk of the pool ``pool``, mounted at the pools' base, is in standby
    (disks.power_state), so that a read of the pool could spin it up; False for a pool that is not
    mounted there. Read without waking a disk: findmnt, lsblk and the power read, hdparm -C (CHECK
    POWER MODE alone, which did not restart the dev device's disk's standby timer either). Only the
    pool's own disks are asked: on a host without hdparm the power read is smartctl -n standby -i,
    whose IDENTIFY restarts it.
    RuntimeError when the host's mounts or disks cannot be listed. Blocking: call it in a worker
    thread.

    ``asleep`` (pool -> Asleep, which this updates) keeps the disk found in standby from one check
    to the next. While the kernel has counted no read or write on that disk since, it still sleeps
    and no disk is asked: otherwise the pool's awake disks, asked before it in lsblk's order, would
    be asked at every check while it sleeps. Once it counted one, that disk is asked first. A disk
    woken by a command that is no read or write (a SMART test, hdparm -S) counts as asleep until
    its next read or write.
    """
    target = f"{get_settings().pools_base_path.rstrip('/')}/{pool}"
    earlier = asleep.get(pool) if asleep is not None else None
    found: Asleep | None = None
    for mount in disks.host_mounts():
        if mount.fstype != "btrfs" or mount.target != target or not mount.uuid:
            continue
        members = disks.disks_of_filesystem(mount.uuid)
        if earlier is not None and earlier.disk in members:
            if earlier.io is not None and _io_of(earlier.disk) == earlier.io:
                return True
            members = [earlier.disk, *(disk for disk in members if disk != earlier.disk)]
        found = next(
            (Asleep(disk, _io_of(disk)) for disk in members if disks.power_state(disk) == "standby"), None
        )
        break
    if asleep is not None:
        if found is None:
            asleep.pop(pool, None)
        else:
            asleep[pool] = found
    return found is not None
