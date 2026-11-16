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

An ATA disk's own automatic offline data collection (SFF-8035i; ``smartctl -o on``) scans its surface
every 4 hours for hours, suspends the scan for each command of the host and resumes it later, and
does not sleep meanwhile: on the dev device's disk it kept an idle timeout from working for most of
the time. While a disk has a timeout, UNOS turns it off (``smartctl -o off`` in the container) and its
rule records that it did, or may have (RECORD); when the timeout is removed, UNOS turns it on again for
such a disk alone. A failure there leaves the timeout set and says so (SCANS_LEFT_ON, SCANS_MAYBE_ON,
SCANS_LEFT_OFF).
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import threading
from dataclasses import dataclass, replace

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
# Seconds smartctl -c, and -o with -c, may take in the container (host.run_in_container, ADR-0290):
# they answer at once, unless the disk hangs.
SMARTCTL_TIMEOUT = 30
# What the answer of a change says when the disk's own automatic offline scans could not be turned
# off for its timeout, or on again once it is removed: {path} is the disk's, {reason} why.
SCANS_LEFT_ON = ("{path} has the idle timeout, but the disk's own automatic offline scans could not be turned off "
                 "({reason}): they may keep it awake")
SCANS_LEFT_OFF = ("{path} has no idle timeout now, but the disk's own automatic offline scans, which UNOS turned off "
                  "for its timeout, could not be turned on again ({reason}): save Off again to try once more")
# What it says when smartctl -o off gave no clear answer: the drive may have taken the command, so the rule
# records it all the same, and Off turns the scans on again.
SCANS_MAYBE_ON = ("{path} has the idle timeout, but whether the disk's own automatic offline scans were turned off "
                  "is unknown ({reason}): they may keep it awake")
# The bits of smartctl -c's JSON (smartmontools' ataprint.cpp): ata_smart_data.offline_data_collection.
# status.value has bit 7 while the automatic offline data collection is on, and the first of
# ata_smart_data.capabilities.values bit 1 when the disk can switch it ("Auto Offline data collection
# on/off support", isSupportAutomaticTimer).
_AUTO_OFFLINE_ON = 0x80
_AUTO_OFFLINE_SWITCH = 0x02

_HEADER = (
    "# Written by UNOS: the idle timeouts of the data disks, set on the Storage page (ADR-0378).\n"
    "# Each line sets the standby timer of one disk, found by its serial, with hdparm -S whenever\n"
    "# udev adds the disk: at boot and when it is connected again. A comment \"UNOS turned off the\n"
    "# automatic offline data collection\" records a disk whose own surface scans, which keep it\n"
    "# awake, UNOS turned off for its timeout (smartctl -o off): the disk is expected to keep that\n"
    "# setting across restarts, which is not confirmed for every disk (saving the timeout again\n"
    "# turns them off again), and UNOS turns them on again when the timeout is removed. UNOS writes\n"
    "# this file at each change of a timeout and keeps only its own lines; with none left it\n"
    "# removes the file.\n"
)
_RULE = (
    'ACTION=="add", SUBSYSTEM=="block", ENV{{DEVTYPE}}=="disk", ENV{{ID_SERIAL_SHORT}}=="{serial}", '
    'RUN+="' + HDPARM_PATH + ' -S {value} $devnode"'
)
_RULE_LINE = re.compile(
    r'ACTION=="add", SUBSYSTEM=="block", ENV\{DEVTYPE\}=="disk", ENV\{ID_SERIAL_SHORT\}=="(?P<serial>[^"]+)", '
    r'RUN\+="' + re.escape(HDPARM_PATH) + r' -S (?P<value>[0-9]+) \$devnode"'
)
# The blanks that udev skips around a line of a rules file; a line ends at "\n" or "\r" (udev's
# read_line). read_rule reads the file the same way, and so does deploy/uninstall.sh.
_BLANKS = " \t"
# A serial that a udev rule matches as it is: udev's ID_SERIAL_SHORT keeps letters, digits and
# "#+-.:=@_" (it replaces the rest), and none of them is a pattern character of a rule's match.
_RULE_SERIAL = re.compile(r"[A-Za-z0-9#+.:=@_-]{1,64}")
# The record that UNOS turned off the automatic offline data collection of the disk with the serial.
RECORD = "# UNOS turned off the automatic offline data collection of the disk with the serial {serial}"
_RECORD_LINE = re.compile(re.escape(RECORD.format(serial="")) + r"(?P<serial>\S+)")

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


@dataclass(frozen=True)
class Rule:
    """What UNOS's udev rule keeps: the idle timeouts, serial -> minutes, in the file's order, and
    the serials of the disks whose automatic offline data collection UNOS turned off (RECORD)."""

    timeouts: dict[str, int]
    scans_off: frozenset[str] = frozenset()


def read_rule() -> Rule:
    """UNOS's udev rule; empty without the file. A line that UNOS did not write is no timeout, nor
    is one whose timeout is not offered (SPINDOWN_MINUTES): one edited by hand; nor is a record
    edited by hand one. Lines are read as udev reads them: one ends at "\n" or "\r", and only
    spaces and tabs around it are blanks. RuntimeError when the file cannot be read."""
    result = host.run_on_host(["cat", "--", RULES_PATH], timeout=UDEV_TIMEOUT, errors="replace")
    if not result.ok:
        if "No such file or directory" in result.stderr:
            return Rule({})
        raise RuntimeError(f"Could not read {RULES_PATH}: {result.stderr.strip() or result.returncode}")
    found: dict[str, int] = {}
    scans_off: set[str] = set()
    for line in result.stdout.replace("\r", "\n").split("\n"):
        line = line.strip(_BLANKS)
        match = _RULE_LINE.fullmatch(line)
        if match and _RULE_SERIAL.fullmatch(match["serial"]):
            minutes = minutes_of(int(match["value"]))
            if minutes and minutes in SPINDOWN_MINUTES:
                found[match["serial"]] = minutes
        record = _RECORD_LINE.fullmatch(line)
        if record and _RULE_SERIAL.fullmatch(record["serial"]):
            scans_off.add(record["serial"])
    return Rule(found, frozenset(scans_off))


def read_timeouts() -> dict[str, int]:
    """The idle timeouts of UNOS's udev rule (read_rule): serial -> minutes, in the file's order; {}
    without the file. RuntimeError when the file cannot be read."""
    return read_rule().timeouts


def timeouts_or_problem() -> tuple[dict[str, int], str | None]:
    """read_timeouts(), and None; or, with a warning, {} and why the rule could not be read, which
    the disk list gives as each disk's spindown_problem."""
    try:
        return read_timeouts(), None
    except Exception as exc:
        logger.warning("Could not read the idle timeouts of the disks: %s", exc)
        return {}, f"The idle timeouts could not be read: {str(exc).strip() or type(exc).__name__}"


def render(timeouts: dict[str, int], scans_off: frozenset[str] = frozenset()) -> str:
    """The rule file for ``timeouts`` (serial -> minutes) and ``scans_off`` (the serials whose
    automatic offline data collection UNOS turned off): its header and one line per disk, each
    followed by its record, then the records of disks without a line (one edited by hand), sorted."""
    lines = []
    for serial, minutes in timeouts.items():
        lines.append(_RULE.format(serial=serial, value=hdparm_value(minutes)))
        if serial in scans_off:
            lines.append(RECORD.format(serial=serial))
    lines += [RECORD.format(serial=serial) for serial in sorted(scans_off - timeouts.keys())]
    return _HEADER + "".join(line + "\n" for line in lines)


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


# --- the disk's own automatic offline scans ------------------------------------------------------


class ScansUnknown(RuntimeError):
    """The automatic offline data collection of a disk could not be read or switched: the reason."""


class ScansMaybeOff(ScansUnknown):
    """smartctl -o off was sent, but it read no state afterwards: the drive may have taken it."""


@dataclass(frozen=True)
class Scans:
    """A disk's automatic offline data collection as smartctl -c read it: whether it is on, whether
    the disk can switch it, and smartctl's exit status."""

    on: bool
    switchable: bool
    exit_status: int


def _scans(device: str, switch: str | None = None) -> Scans | None:
    """The automatic offline data collection of the whole disk ``device``: ``smartctl -i -c -j`` in
    the container, after ``-o on`` or ``-o off`` when ``switch`` is "on" or "off" (smartctl reads the
    SMART data again after -o, so the answer is the state it left). None for a disk that runs none:
    an NVMe or SCSI disk (smartctl -o is ATA's alone), and an ATA disk without SMART or with SMART
    turned off (the collection is a SMART timer function, and SMART DISABLE OPERATIONS stops it),
    which -i's smart_support says: smartctl -c alone prints "SMART Disabled" only as text, which -j
    drops (ataprint.cpp, smartctl.cpp). -i reads the identity that -c reads anyway. ``-n standby``
    leaves a sleeping disk alone; the Save that runs this woke it with hdparm -S first. ScansUnknown
    when smartctl does not answer within SMARTCTL_TIMEOUT (killed and not waited for, ADR-0290) or
    reads no state."""
    cmd = ["smartctl", "-n", "standby", *(["-o", switch] if switch else []), "-i", "-c", "-j", device]
    try:
        result = host.run_in_container(cmd, timeout=SMARTCTL_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise ScansUnknown(f"smartctl did not answer within {SMARTCTL_TIMEOUT} seconds") from None
    except OSError as exc:
        raise ScansUnknown(f"smartctl could not run: {exc}") from None
    if disks.smartctl_found_it_asleep(result):
        raise ScansUnknown("smartctl found the disk in standby")
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, dict):
        raise ScansUnknown(f"smartctl could not read them: exit status {result.returncode}")
    status = _member(data, "ata_smart_data", "offline_data_collection", "status", "value")
    values = _member(data, "ata_smart_data", "capabilities", "values")
    capabilities = values[0] if isinstance(values, list) and values else None
    if isinstance(status, int) and isinstance(capabilities, int):
        return Scans(bool(status & _AUTO_OFFLINE_ON), bool(capabilities & _AUTO_OFFLINE_SWITCH), result.returncode)
    if _member(data, "device", "protocol") in ("NVMe", "SCSI"):
        return None
    if _member(data, "smart_support", "available") is False or _member(data, "smart_support", "enabled") is False:
        return None
    messages = _member(data, "smartctl", "messages")
    said = [m["string"] for m in messages if isinstance(m, dict) and isinstance(m.get("string"), str) and m["string"]] \
        if isinstance(messages, list) else []
    raise ScansUnknown("smartctl could not read them: " + ("; ".join(said) or f"exit status {result.returncode}"))


def _member(data: object, *keys: str) -> object:
    """``data[keys[0]][keys[1]]...`` of smartctl's JSON, or None where a level is missing or no object."""
    for key in keys:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def _scans_off(device: str) -> bool:
    """Turn off the automatic offline data collection of ``device`` when it is on: True when this
    turned it off, False when it was off already or the disk has none. ScansUnknown with the reason
    when it could not be read, the disk cannot switch it, or it is still on afterwards; ScansMaybeOff
    when smartctl -o off read no state afterwards: it sends the command before it reads the SMART data
    again (ataprint.cpp), so the drive may have taken it although smartctl was killed at its timeout
    or that read failed."""
    found = _scans(device)
    if found is None or not found.on:
        return False
    if not found.switchable:
        raise ScansUnknown("the disk cannot switch them off")
    try:
        after = _scans(device, "off")
    except ScansUnknown as exc:
        raise ScansMaybeOff(str(exc)) from None
    if after is None:
        raise ScansMaybeOff("smartctl -o off read no state")
    if after.on:
        raise ScansUnknown(f"smartctl -o off left them on, exit status {after.exit_status}")
    return True


def _scans_on(device: str) -> None:
    """Turn the automatic offline data collection of ``device`` on again (UNOS turned it off).
    ScansUnknown with the reason when it is not on afterwards."""
    after = _scans(device, "on")
    if after is None or not after.on:
        raise ScansUnknown(f"smartctl -o on left them off, exit status {after.exit_status}" if after
                           else "smartctl -o on read no state")


# --- setting a timeout -------------------------------------------------------------------------


@dataclass(frozen=True)
class Change:
    """A timeout set: the disk's name and path, its serial, whether the rule file changed, what UNOS
    did to the disk's automatic offline data collection ("off", "on" or None), and the answer's
    warning when it could not do what it should (SCANS_LEFT_ON, SCANS_LEFT_OFF), else None."""

    name: str
    path: str
    serial: str
    minutes: int
    changed: bool
    scans: str | None = None
    warning: str | None = None

    @property
    def what(self) -> str:
        """The change in words, for the audit log."""
        if not self.minutes:
            text = f"Turned off the idle timeout of {self.path} (serial {self.serial})"
        else:
            text = f"Set the idle timeout of {self.path} (serial {self.serial}) to {_words(self.minutes)}"
        if self.scans == "off":
            text += " and turned off its automatic offline data collection"
        elif self.scans == "on":
            text += " and turned its automatic offline data collection on again"
        return f"{text}; {self.warning}" if self.warning else text


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
    ``minutes`` (0: none): ``hdparm -S`` on the host now, then the disk's own automatic offline
    data collection, then UNOS's udev rule, written only when the disk's line or record changes,
    after which udev reads its rules again. Blocking: call it in a worker thread.

    With a timeout, a collection that is on is turned off and recorded; with none, a collection
    that the rule records UNOS turned off is turned on again and its record removed. One that was
    off already, or a disk without it, is left as it is. When that fails, the timeout is still set:
    the Change's warning says so, and the record is kept as the disk may be (none for scans still on;
    the record for scans that smartctl -o off may have turned off without a clear answer, and for
    scans still off after a failed -o on, so that the next Off turns them on).

    HTTPException 400 for a name that is no disk of this system, a boot or virtual device
    (HIDDEN_DISK_PREFIXES) and a partition; 409 for a disk that gets no timeout (problem()), a host
    without hdparm, and a disk that udev knows by another serial than lsblk; 500 when hdparm fails
    or the rule cannot be read, and nothing is changed then. RuleNotWritten when hdparm set it but
    the rule could not be written; the collection is then put back as the old rule records it: turned
    on again when this added the record (the drive would keep it off with none), off again when this
    removed it (the old rule sets the timeout again at boot, and the scans would keep it from working).
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
        current = read_rule()
        cmd = ["timeout", f"--kill-after={KILL_AFTER}", str(HDPARM_TIMEOUT), "hdparm", "-S", str(value), facts.path]
        result = host.run_on_host(cmd, timeout=HDPARM_TIMEOUT + KILL_AFTER + 5, errors="replace")
        if not result.ok:
            why = (result.stderr.strip() or result.stdout.strip() or f"exit status {result.returncode}").splitlines()
            raise HTTPException(
                status_code=500, detail=f"hdparm could not set the idle timeout of {facts.path}: {why[-1].strip()}"
            )
        scans, warning, scans_off = _switch_scans(facts.path, serial, minutes, current.scans_off)
        timeouts = dict(current.timeouts)  # a disk's line keeps its place; a new one comes last
        if minutes:
            timeouts[serial] = minutes
        else:
            timeouts.pop(serial, None)
        wanted = Rule(timeouts, scans_off)
        change = Change(shown, facts.path, serial, minutes, wanted != current, scans, warning)
        if change.changed:
            try:
                if wanted.timeouts or wanted.scans_off:
                    host.write_host_file_atomic(RULES_PATH, render(wanted.timeouts, wanted.scans_off))
                else:
                    host.run_on_host(["rm", "-f", "--", RULES_PATH], timeout=UDEV_TIMEOUT, check=True)
            except Exception as exc:
                why = str(exc).strip() or type(exc).__name__
                if (serial in wanted.scans_off) != (serial in current.scans_off):  # a record added or removed
                    left = _undo_scans(facts.path, serial in current.scans_off)
                    if left:
                        why += f"; {left}"
                    else:
                        change = replace(change, scans=None, warning=None)
                raise RuleNotWritten(change, why) from exc
            _reload_rules()
    return change


def _switch_scans(path: str, serial: str, minutes: int, scans_off: frozenset[str]
                  ) -> tuple[str | None, str | None, frozenset[str]]:
    """The automatic offline data collection of the disk at ``path`` for its idle timeout
    ``minutes`` (set_timeout): what UNOS did ("off", "on" or None), the warning when it could not
    or does not know, and the serials of the rule's records afterwards. A failure is logged."""
    try:
        if minutes:
            if _scans_off(path):
                return "off", None, scans_off | {serial}
        elif serial in scans_off:
            _scans_on(path)
            return "on", None, scans_off - {serial}
    except ScansMaybeOff as exc:  # recorded all the same: Off then puts back what UNOS found (on)
        warning = SCANS_MAYBE_ON.format(path=path, reason=exc)
        logger.warning("%s", warning)
        return None, warning, scans_off | {serial}
    except ScansUnknown as exc:
        warning = (SCANS_LEFT_ON if minutes else SCANS_LEFT_OFF).format(path=path, reason=exc)
        logger.warning("%s", warning)
        return None, warning, scans_off
    return None, None, scans_off


def _undo_scans(path: str, recorded: bool) -> str | None:
    """Put the automatic offline data collection of the disk at ``path`` back as the rule that could
    not be replaced records it (set_timeout): off again when it records that UNOS turned it off, on
    again when it does not. None when that worked, else what is left, which is logged."""
    try:
        if recorded:
            _scans_off(path)
        else:
            _scans_on(path)
        return None
    except ScansUnknown as exc:
        left = f"the disk's own automatic offline scans may stay {'on' if recorded else 'off'}: {exc}"
        logger.error("The udev rule could not be written, and on %s %s", path, left)
        return left


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
