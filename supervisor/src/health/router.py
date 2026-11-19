import copy
import subprocess
import json
import asyncio
import logging
import re
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from pydantic import BaseModel, Field
from typing import Optional

from . import problems
from .. import host, state_files
from ..times import stored_time_text, utc_now, utc_text
from ..auth.utils import verify_token
from ..security.validators import validate_pool_name
from ..storage import disks as storage_disks
from ..storage import pools as storage_pools
from ..storage import spindown, volume_usage
from ..storage.schemas import PoolInfo
from ..notifications.router import load_configs as load_notification_configs
from ..notifications.router import history_entry, save_logs, send_email, send_through
from ..notifications.schemas import NotificationEvent, NotificationType, NotificationChannel

router = APIRouter(prefix="/health", tags=["health"])
logger = logging.getLogger(__name__)


class SmartAttribute(BaseModel):
    id: int
    name: str
    value: int
    worst: int
    thresh: int
    raw_value: str
    status: str  # ok, warning, critical


class SmartInfo(BaseModel):
    device: str
    model: str
    serial: str
    firmware: str
    capacity: str
    temperature: Optional[int]
    power_on_hours: Optional[int]
    power_cycle_count: Optional[int]
    # PASSED, FAILED, UNKNOWN; STANDBY: the disk sleeps, and smartctl -n standby left it alone, so
    # no SMART data was read (ADR-0378).
    health_status: str
    attributes: list[SmartAttribute]  # ATA attributes; NVMe disks have none
    # The numbers of an NVMe disk's SMART/Health Information log, by smartctl's
    # names and in its order (critical_warning, available_spare, media_errors,
    # ...); None for other disks.
    nvme_health: Optional[dict[str, int]] = None
    self_test_status: Optional[str]
    last_test_result: Optional[str]


class SmartTestResult(BaseModel):
    status: str
    message: str


class RaidDeviceStats(BaseModel):
    device: str
    write_io_errs: int
    read_io_errs: int
    flush_io_errs: int
    corruption_errs: int
    generation_errs: int
    has_errors: bool


class RaidHealthInfo(BaseModel):
    pool: str
    mount_point: str
    profile: Optional[str]
    # The pool's health as the Storage page reports it (storage.pools):
    # healthy, degraded (devices missing), error (error counters, or forced
    # read-only), unmounted (not mounted at mount_point), unknown (the mount
    # state or the error counters could not be read)
    status: str
    devices: list[RaidDeviceStats]
    total_errors: int
    last_checked: str
    missing_devices: int = 0
    read_only: bool = False
    # btrfs made the filesystem read-only after an error (read_only is also
    # true for a pool mounted read-only on purpose).
    forced_read_only: bool = False


# In-memory state for RAID monitoring
_raid_monitor_running = False
_raid_monitor_task: Optional[asyncio.Task] = None
RAID_CHECK_INTERVAL = 300  # Check every 5 minutes
RAID_STATUS_FILE = "/data/raid_status.json"
SMART_HISTORY_FILE = "/data/smart_history.json"
SMART_CHECK_INTERVAL = 3600  # Check SMART every hour
# Snapshots kept per disk: 30 days of hourly passes (ADR-0235).
SMART_HISTORY_LIMIT = 720
SMARTCTL_TIMEOUT = 30
# The history key of every disk that smartctl reports without a serial: the
# counters of different disks mix there, so it gets no prediction (ADR-0235).
_NO_SERIAL = "unknown"

# Alerts are sent from their own small thread pool and each delivery is bounded
# by ALERT_TIMEOUT. send_email itself runs smtplib on the notifications SMTP
# pool with a per-operation timeout; this pool keeps the monitors isolated from
# whatever a sender does, so a slow or unreachable mail server never freezes the
# event loop or uses up the default executor that storage operations run in.
# The HTTP channels' attempt from the host's network, when the container
# connects nowhere, ends well within it too (CONNECT_TIMEOUT of
# notifications/router.py, ADR-0336).
ALERT_TIMEOUT = 60
_ALERT_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="unos-alerts")

# Critical SMART attributes to track for trends
CRITICAL_ATTRIBUTES = {
    5: "Reallocated_Sector_Ct",
    187: "Reported_Uncorrect",
    188: "Command_Timeout",
    197: "Current_Pending_Sector",
    198: "Offline_Uncorrectable",
    199: "UDMA_CRC_Error_Count",
}

# SMART trend monitoring state
_smart_monitor_running = False
_smart_monitor_task: Optional[asyncio.Task] = None


class SmartHistoryEntry(BaseModel):
    timestamp: str
    device: str
    serial: str  # smartctl's: the key of the disk's history
    # The disk's serial as lsblk reported it with this snapshot, which tells
    # which connected disk the history belongs to (ADR-0235); None when lsblk
    # reports none (some USB bridges).
    lsblk_serial: Optional[str] = None
    attributes: dict[int, int]  # attr_id -> raw_value
    temperature: Optional[int] = None
    power_on_hours: Optional[int] = None
    # What smartctl said of the disk's health with this snapshot, which the SMART monitor keeps in
    # RAM for the disk problems (ADR-0332); never stored in the history (exclude). The overall
    # health self-assessment FAILED; the result of the newest self-test, when it failed.
    health_failed: bool = Field(default=False, exclude=True)
    failed_self_test: Optional[str] = Field(default=None, exclude=True)


class SmartTrendAlert(BaseModel):
    device: str
    serial: str
    model: str
    attribute_name: str
    attribute_id: int
    current_value: int
    previous_value: int
    change: int
    severity: str  # warning, critical
    message: str
    recommendation: str


class SmartHistoryUnreadable(Exception):
    """SMART_HISTORY_FILE exists but holds no SMART history that can be read."""


@dataclass
class _SmartHistoryState:
    """The SMART history as the monitor keeps it in RAM (ADR-0269).

    The monitor's first pass reads SMART_HISTORY_FILE; from then on the
    history lives here and the file is written only when a pass changed it,
    and at shutdown. Each pass replaces ``history`` with a new dict of new
    lists, so that the endpoints can read the one they got without a lock.
    """

    history: Optional[dict[str, list[dict]]] = None  # None until the monitor's first pass
    unsaved: bool = False  # holds snapshots that SMART_HISTORY_FILE does not
    changed: bool = False  # one of them changed the history: written at the next pass if not yet
    stopped: bool = False  # written at shutdown: a pass that ends later keeps nothing


_smart_history = _SmartHistoryState()
# Serialises the monitor's passes and the write at shutdown.
_smart_history_lock = threading.Lock()


def _history_by_serial(history: object) -> dict[str, list[dict]]:
    """The decoded SMART history file as the history; ValueError for anything else."""
    if not isinstance(history, dict) or not all(
        isinstance(entries, list) and all(isinstance(entry, dict) for entry in entries)
        for entries in history.values()
    ):
        raise ValueError("it holds no snapshots by serial")
    return history


def load_smart_history() -> dict[str, list[dict]]:
    """The SMART history: smartctl's serial -> its snapshots, oldest first.

    {} without a file. A file that cannot be read or holds no history raises
    SmartHistoryUnreadable: that is not an empty history (ADR-0235), and the
    file stays for the monitor's pass (load_smart_history_or_start_anew).
    """
    path = Path(SMART_HISTORY_FILE)
    try:
        return _history_by_serial(json.loads(path.read_bytes()))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise SmartHistoryUnreadable(f"Could not read the SMART history {path}: {exc}") from exc


def load_smart_history_or_start_anew() -> dict[str, list[dict]]:
    """The SMART history for the monitor's pass, which starts a new one instead of a damaged one.

    A file that cannot be read as a history is kept as smart_history.json.bad and named in a
    warning (ADR-0324); one that cannot be opened raises SmartHistoryUnreadable, as for the
    endpoints, and stays where it is.
    """
    path = Path(SMART_HISTORY_FILE)
    try:
        return state_files.load_json(path, _history_by_serial, dict, what="the SMART history")
    except OSError as exc:
        raise SmartHistoryUnreadable(f"Could not read the SMART history {path}: {exc}") from exc


def _smart_history_or_500() -> dict[str, list[dict]]:
    """The SMART history the endpoints report: the monitor's in RAM (ADR-0269).

    Before the monitor's first pass, the history in SMART_HISTORY_FILE; one
    that cannot be read answers 500 (ADR-0235).
    """
    history = _smart_history.history
    if history is not None:
        return history
    try:
        return load_smart_history()
    except SmartHistoryUnreadable as exc:
        raise HTTPException(500, str(exc)) from exc


def save_smart_history(history: dict[str, list[dict]]):
    """Write the SMART history to SMART_HISTORY_FILE.

    Staged next to the file, then renamed over it: a reader, or a power loss
    during the write, finds the old history or the new one, never a part
    (ADR-0235). The monitor writes only when a pass changed the history,
    and at shutdown (ADR-0269).
    """
    state_files.write_json(Path(SMART_HISTORY_FILE), history, sync=True)


def collect_smart_snapshot(device: str) -> Optional[SmartHistoryEntry]:
    """Collect current SMART data for trend tracking (blocking).

    ``-n standby`` leaves disks that sleep alone: reading SMART data would
    spin them up every hour. smartctl then exits with bit 1 set, as it does
    when the device cannot be opened; both give no snapshot. Higher bits
    (failing health, logged errors, ...) still come with data.
    """
    try:
        # Also of a disk attached after the supervisor started (ADR-0414).
        result = host.run_smartctl(["-n", "standby", "-j", "-a"], device, timeout=SMARTCTL_TIMEOUT)
        if result.returncode & 0b11:  # bad command line, open failed or asleep
            return None

        data = json.loads(result.stdout)
        # As smartctl prints it, the serial Disk Health shows; an empty or blank one is
        # none: such disks share the history "unknown" (ADR-0270).
        serial = data.get("serial_number")
        if not isinstance(serial, str) or not serial.strip():
            serial = _NO_SERIAL

        # Extract critical attributes
        attributes = {}
        ata_attrs = data.get("ata_smart_attributes", {}).get("table", [])
        for attr in ata_attrs:
            attr_id = attr.get("id", 0)
            if attr_id in CRITICAL_ATTRIBUTES:
                raw = attr.get("raw", {}).get("value", 0)
                try:
                    attributes[attr_id] = int(raw)
                except (ValueError, TypeError):
                    attributes[attr_id] = 0

        # Temperature
        temperature = None
        temp_data = data.get("temperature", {})
        if "current" in temp_data:
            temperature = temp_data["current"]

        # Power on hours
        power_on_hours = None
        for attr in ata_attrs:
            if attr.get("name") == "Power_On_Hours":
                try:
                    power_on_hours = int(attr.get("raw", {}).get("value", 0))
                except (ValueError, TypeError):
                    pass

        health_failed, failed_self_test = _health_verdicts(data)
        return SmartHistoryEntry(
            timestamp=utc_text(utc_now()),
            device=device,
            serial=serial,
            attributes=attributes,
            temperature=temperature,
            power_on_hours=power_on_hours,
            health_failed=health_failed,
            failed_self_test=failed_self_test,
        )
    except Exception:
        return None


# The results of an NVMe self-test that are failures: a fatal or unknown error, a failed segment
# that is not known, one or more failed segments (the NVMe Device Self-test log).
_NVME_SELF_TEST_FAILURES = {5, 6, 7}
# The bit of an NVMe disk's critical warning for a temperature beyond its thresholds.
_NVME_TOO_HOT = 0x02


def _health_verdicts(data: dict) -> tuple[bool, Optional[str]]:
    """Whether ``smartctl -j -a`` says the overall health self-assessment FAILED, and the result of
    the newest self-test when it failed, as smartctl names it (ADR-0332).

    smartctl calls an NVMe disk's health FAILED for any bit of its critical warning: one that is
    only too hot (_NVME_TOO_HOT) is not failing, and it cools down. The newest test comes first in
    the ATA self-test log and in the NVMe one (smartctl 7.3 and later); smartctl marks an ATA test
    that ended with an error passed: false, and a test that still runs, or that the host aborted,
    is no failure. Output of another shape says neither.
    """
    try:
        smart_status = data.get("smart_status", {})
        warning = (smart_status.get("nvme") or {}).get("value")
        health_failed = smart_status.get("passed") is False and not (
            isinstance(warning, int) and not warning & ~_NVME_TOO_HOT
        )
        ata_tests = data.get("ata_smart_self_test_log", {}).get("standard", {}).get("table") or []
        nvme_tests = data.get("nvme_self_test_log", {}).get("table") or []
        if ata_tests:
            status = ata_tests[0].get("status") or {}
            failed = status.get("passed") is False
        elif nvme_tests:
            status = nvme_tests[0].get("self_test_result") or {}
            failed = status.get("value") in _NVME_SELF_TEST_FAILURES
        else:
            status, failed = {}, False
        return health_failed, str(status.get("string") or "an error") if failed else None
    except (AttributeError, IndexError, TypeError):
        return False, None


def _attribute_value(entry: dict, attr_id: int) -> int:
    """Raw value of SMART attribute ``attr_id`` in a history entry (0 if absent).

    Entries read from SMART_HISTORY_FILE have str keys (JSON); an entry dumped
    from a SmartHistoryEntry without mode="json" has int keys. Accept both.
    """
    attributes = entry.get("attributes") or {}
    value = attributes.get(str(attr_id), attributes.get(attr_id, 0))
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _reports_attribute(entry: dict, attr_id: int) -> bool:
    attributes = entry.get("attributes") or {}
    return str(attr_id) in attributes or attr_id in attributes


def _counter_values(history: list[dict], attr_id: int) -> list[int]:
    """The values of counter ``attr_id`` in the snapshots that hold it, oldest first.

    A snapshot without it (smartctl could not read the attribute table, and
    the monitor stored none) tells nothing of it: it is no value of 0
    (ADR-0235, ADR-0270).
    """
    return [_attribute_value(entry, attr_id) for entry in history if _reports_attribute(entry, attr_id)]


def _latest_counters(entries: list[dict]) -> dict[int, int]:
    """The latest value of each counter of CRITICAL_ATTRIBUTES in a disk's snapshots.

    Read from the newest snapshot that holds the counter (ADR-0235, ADR-0270);
    a counter that none holds is missing.
    """
    latest: dict[int, int] = {}
    for entry in reversed(entries):
        for attr_id in CRITICAL_ATTRIBUTES:
            if attr_id not in latest and _reports_attribute(entry, attr_id):
                latest[attr_id] = _attribute_value(entry, attr_id)
        if len(latest) == len(CRITICAL_ATTRIBUTES):
            break
    return latest


def _snapshots_of_one_disk(serial: str, entries: list[dict], device: Optional[str]) -> list[dict]:
    """The snapshots of the disk at ``device`` in the history of ``serial`` (ADR-0270).

    A serial names one disk wherever it was: all of its snapshots. The disks
    that smartctl reports without a serial share the history "unknown", in
    which only the snapshots taken at one path are of one disk.
    """
    if serial and serial != _NO_SERIAL:
        return entries
    return [entry for entry in entries if entry.get("device") == device]


def _trend_alert(serial: str, device: str, attr_id: int, previous: int, current: int) -> SmartTrendAlert:
    """The alert of counter ``attr_id`` of CRITICAL_ATTRIBUTES that rose from ``previous`` to ``current``."""
    attr_name = CRITICAL_ATTRIBUTES[attr_id]
    change = current - previous

    # Determine severity based on attribute and change rate
    severity = "warning"
    if attr_id in [5, 197, 198]:  # Critical sector issues
        if change > 10:
            severity = "critical"
    elif change > 100:
        severity = "critical"

    # Generate recommendation
    if attr_id == 5:
        recommendation = "Disk is reallocating bad sectors. Consider replacing soon."
    elif attr_id == 197:
        recommendation = "Pending sectors detected. Run extended SMART test."
    elif attr_id == 198:
        recommendation = "Uncorrectable sectors found. Backup data immediately."
    elif attr_id == 199:
        recommendation = "Cable or connection issues detected. Check SATA cable."
    else:
        recommendation = "Monitor closely and consider replacement if trend continues."

    return SmartTrendAlert(
        device=device,
        serial=serial,
        model="",  # Filled in later
        attribute_name=attr_name,
        attribute_id=attr_id,
        current_value=current,
        previous_value=previous,
        change=change,
        severity=severity,
        message=f"{attr_name} increased by {change} (from {previous} to {current})",
        recommendation=recommendation
    )


def analyze_smart_trends(serial: str, history: list[dict]) -> list[SmartTrendAlert]:
    """The counters of CRITICAL_ATTRIBUTES that rose over one disk's last day (ADR-0270).

    Each counter's latest value is compared with the first of its last 24
    values (a day of hourly snapshots), or with its oldest one: of the
    snapshots that hold the counter.
    """
    alerts = []

    if len(history) < 2:
        return alerts

    device = history[-1].get("device", "unknown")

    for attr_id in CRITICAL_ATTRIBUTES:
        values = _counter_values(history, attr_id)
        if len(values) < 2:
            continue
        previous = values[max(0, len(values) - 24)]
        if values[-1] > previous:
            alerts.append(_trend_alert(serial, device, attr_id, previous, values[-1]))

    return alerts


def _send_email_blocking(config, event: NotificationEvent):
    """Run notifications.send_email to completion in the calling alert thread."""
    return asyncio.run(send_email(config, event))


async def _deliver_alert(event: NotificationEvent) -> None:
    """Send an alert through every enabled channel, of every kind; best effort.

    The senders report failures as (False, error) instead of raising; those are
    logged like timeouts and exceptions. The history of System > Notifications
    lists each alert sent, with its result, in one write per alert, made in a
    worker thread: it is synced to the system disk (ADR-0021, ADR-0326).
    """
    loop = asyncio.get_running_loop()
    entries = []
    for config in load_notification_configs():
        if not config.enabled:
            continue
        try:
            if config.channel == NotificationChannel.EMAIL and config.email_config:
                sending = loop.run_in_executor(
                    _ALERT_EXECUTOR, _send_email_blocking, config.email_config, event
                )
            else:
                # Telegram, webhooks, Discord and Slack; a channel without its settings is
                # recorded as failed, as its test send reports it.
                sending = send_through(config, event)
            sent, error = await asyncio.wait_for(sending, ALERT_TIMEOUT)
        except TimeoutError:
            sent, error = False, f"no answer within {ALERT_TIMEOUT} s"
        except Exception as exc:
            sent, error = False, str(exc) or type(exc).__name__
        if not sent:
            logger.warning(
                "Could not send alert '%s' by %s: %s", event.title, config.channel.value, error
            )
        entries.append(history_entry(config, event, sent, error))
    await asyncio.to_thread(save_logs, entries)


async def send_smart_alert(alert: SmartTrendAlert):
    """Send notification about SMART trend issue."""
    event = NotificationEvent(
        type=NotificationType.ERROR if alert.severity == "critical" else NotificationType.WARNING,
        title=f"Disk Health Warning: {alert.device}",
        message=f"{alert.message}\n\nRecommendation: {alert.recommendation}",
        timestamp=utc_now(),
    )
    await _deliver_alert(event)


def _new_trend_alerts(serial: str, history: list[dict]) -> list[SmartTrendAlert]:
    """The alerts of the counters that one disk's newest snapshot saw rise (ADR-0270).

    A counter rose when the newest snapshot holds a value above the one of
    the latest earlier snapshot that holds it. The monitor runs every hour and
    alerts when a value rises, not again on every pass after it. The alert
    reports the counter's rise over its last day, as analyze_smart_trends
    does; when it shows none (the counter fell below its value of a day ago
    and rose again), the rise since that earlier value.
    """
    if len(history) < 2:
        return []
    *earlier, newest = history
    latest = _latest_counters(earlier)
    rises = [
        attr_id for attr_id in CRITICAL_ATTRIBUTES
        if attr_id in latest and _reports_attribute(newest, attr_id)
        and _attribute_value(newest, attr_id) > latest[attr_id]
    ]
    if not rises:
        return []
    trend = {alert.attribute_id: alert for alert in analyze_smart_trends(serial, history)}
    device = newest.get("device", "unknown")
    return [
        trend.get(attr_id) or _trend_alert(serial, device, attr_id, latest[attr_id], _attribute_value(newest, attr_id))
        for attr_id in rises
    ]


def _changes_the_history(earlier: list[dict], snapshot: dict) -> bool:
    """Whether a new snapshot tells what the disk's earlier snapshots do not (ADR-0269).

    The disk's first snapshot, a counter of CRITICAL_ATTRIBUTES with another
    value than in the latest earlier snapshot that holds it, or another path
    or lsblk serial than in the previous snapshot. The time, the temperature
    and the power-on hours change at every pass and are no change, and a
    snapshot without the counters tells nothing of them.
    """
    if not earlier:
        return True
    previous = earlier[-1]
    if (snapshot.get("device"), snapshot.get("lsblk_serial")) != (
        previous.get("device"), previous.get("lsblk_serial")
    ):
        return True
    latest = _latest_counters(earlier)
    return any(
        _reports_attribute(snapshot, attr_id) and latest.get(attr_id) != _attribute_value(snapshot, attr_id)
        for attr_id in CRITICAL_ATTRIBUTES
    )


def _write_smart_history(state: _SmartHistoryState) -> None:
    """Write the history in RAM to SMART_HISTORY_FILE; a failure is logged and tried again later."""
    try:
        save_smart_history(state.history or {})
    except OSError as exc:
        logger.warning("SMART monitor: could not write the SMART history %s: %s", SMART_HISTORY_FILE, exc)
        return
    state.unsaved = state.changed = False


@dataclass(frozen=True)
class _DiskVerdict:
    """What the SMART monitor's last read of a disk said of its health (ADR-0332)."""

    serial: str  # smartctl's
    lsblk_serial: Optional[str]  # lsblk's, of the disk at the path then
    health_failed: bool  # the overall health self-assessment FAILED
    failed_self_test: Optional[str]  # the result of the newest self-test, when it failed


# The verdict of each disk of the host by its path, from the SMART monitor's passes: one that
# sleeps keeps the verdict of its last read while lsblk reports the same serial at its path, and
# a disk that is gone has none. In RAM only, like
# the problems that come from it; replaced whole by each pass.
_disk_verdicts: dict[str, _DiskVerdict] = {}

# The reads and writes that the kernel had counted on each whole disk, by its path, when the SMART
# monitor last read the disk's SMART data (storage.disks.io_counts). A disk given an idle timeout
# that has had none since is not read again: the read's commands would restart its standby timer,
# and an hourly read would keep it from ever sleeping with a timeout of an hour or more (ADR-0378).
# In RAM only; replaced whole by each pass.
_disk_io_at_read: dict[str, tuple[int, int]] = {}


def disk_problems(verdicts: dict[str, _DiskVerdict]) -> dict[str, problems.Problem]:
    """The problems of the disks in the SMART monitor's verdicts, by key (ADR-0332): an error for a
    disk whose overall health self-assessment FAILED, and one for a disk whose newest self-test
    failed."""
    found = {}
    for path, verdict in sorted(verdicts.items()):
        disk = path if verdict.serial == _NO_SERIAL else f"{path} (serial {verdict.serial})"
        if verdict.health_failed:
            found[f"disk_failed:{path}"] = problems.Problem(
                key=f"disk_failed:{path}", severity="error", kind="disk_failed", subject=path,
                title=f"Disk {path} is failing",
                detail=f"SMART reports that the disk {disk} fails its health self-assessment: "
                "copy its data to another disk and replace it.",
            )
        if verdict.failed_self_test:
            found[f"disk_test_failed:{path}"] = problems.Problem(
                key=f"disk_test_failed:{path}", severity="error", kind="disk_test_failed", subject=path,
                title=f"The SMART self-test of disk {path} failed",
                detail=f"The newest SMART self-test of the disk {disk} ended with: {verdict.failed_self_test}. "
                "Run an extended test, and replace the disk if it fails again.",
            )
    return found


def _smart_monitor_pass() -> list[SmartTrendAlert]:
    """One round of the SMART monitor (blocking: lsblk on the host, smartctl per disk).

    Records a snapshot of every whole disk of the host that is awake (the
    disks of GET /health/disks, storage.disks.host_disks) and returns the
    trend alerts of the counters that rose since the disk's previous
    snapshot. When the host's disks cannot be listed, the pass is logged
    and skipped. The history stays in RAM, and SMART_HISTORY_FILE is written
    only when a snapshot changed it, or one of an earlier pass whose write
    failed (ADR-0269). The first pass reads the file: one that cannot be
    read is logged and replaced by a new history, so that a damaged file
    does not stop the recording (ADR-0235). The verdicts of the disks'
    health go into _disk_verdicts (ADR-0332). A disk with an idle timeout
    (UNOS's udev rule) that the kernel counted no read or write on since this
    monitor last read it is left alone and keeps that read's verdict
    (_disk_io_at_read, ADR-0378).
    """
    global _disk_verdicts, _disk_io_at_read
    try:
        disks = storage_disks.host_disks()
    except Exception as exc:
        logger.warning("SMART monitor: pass skipped, could not list the host's disks: %s", exc)
        return []
    counts = storage_disks.io_counts()
    timeouts: dict[str, int] | None = None  # UNOS's udev rule, read once a disk had no I/O since its read
    snapshots = []
    verdicts: dict[str, _DiskVerdict] = {}
    io_at_read: dict[str, tuple[int, int]] = {}
    read: list[storage_disks.HostDisk] = []
    for disk in disks:
        earlier = _disk_verdicts.get(disk.path)
        io = counts.get(disk.name)
        if (
            io is not None
            and _disk_io_at_read.get(disk.path) == io
            and earlier is not None
            and earlier.lsblk_serial == disk.serial
        ):
            if timeouts is None:
                timeouts, _ = spindown.timeouts_or_problem()
            if timeouts.get(disk.serial or ""):
                verdicts[disk.path] = earlier
                io_at_read[disk.path] = io
                continue
        snapshot = collect_smart_snapshot(disk.path)
        if snapshot:
            read.append(disk)
            snapshot.lsblk_serial = disk.serial
            # JSON form (str attribute keys), like the entries read from the file.
            snapshots.append(snapshot.model_dump(mode="json"))
            verdicts[disk.path] = _DiskVerdict(
                snapshot.serial, disk.serial, snapshot.health_failed, snapshot.failed_self_test
            )
        elif (verdict := _disk_verdicts.get(disk.path)) is not None and verdict.lsblk_serial == disk.serial:
            # Asleep (smartctl leaves it alone), or it could not be opened: what its last read said,
            # unless another disk is at its path now.
            verdicts[disk.path] = verdict
    _disk_verdicts = verdicts
    # After the reads, so that their own commands, if the kernel counts them, are not taken for a use.
    after = storage_disks.io_counts() if read else {}
    io_at_read.update({disk.path: after[disk.name] for disk in read if disk.name in after})
    _disk_io_at_read = io_at_read

    alerts: list[SmartTrendAlert] = []
    with _smart_history_lock:
        state = _smart_history
        if state.stopped:
            # A pass that ran when the supervisor stopped: its alerts can no longer be
            # sent. Nothing of it is kept, so the next start compares with the history
            # written at shutdown and alerts a rise of this pass then (ADR-0269).
            return []
        if state.history is None:
            try:
                history = load_smart_history_or_start_anew()
            except SmartHistoryUnreadable as exc:
                logger.warning("SMART monitor: %s; starting a new history", exc)
                history = {}
        else:
            # New lists: the endpoints may be reading the ones they got.
            history = {serial: list(entries) for serial, entries in state.history.items()}
        for snapshot in snapshots:
            serial = snapshot["serial"]
            entries = history.setdefault(serial, [])
            if _changes_the_history(_snapshots_of_one_disk(serial, entries, snapshot["device"]), snapshot):
                state.changed = True
            entries.append(snapshot)
            del entries[:-SMART_HISTORY_LIMIT]
            alerts.extend(_new_trend_alerts(serial, _snapshots_of_one_disk(serial, entries, snapshot["device"])))
        state.history = history
        state.unsaved = state.unsaved or bool(snapshots)
        if state.changed:
            _write_smart_history(state)
    return alerts


def save_unsaved_smart_history() -> None:
    """At shutdown: write the snapshots that only the history in RAM holds (ADR-0269).

    A pass that ends after this keeps nothing (_SmartHistoryState.stopped).
    """
    with _smart_history_lock:
        if _smart_history.history is not None and _smart_history.unsaved:
            _write_smart_history(_smart_history)
        _smart_history.stopped = True


async def smart_monitor_loop():
    """Background loop for SMART trend monitoring, and the problems of the disks (ADR-0332)."""
    global _smart_monitor_running

    while _smart_monitor_running:
        try:
            # smartctl takes seconds per disk: never run it on the event loop.
            alerts = await asyncio.to_thread(_smart_monitor_pass)
            for alert in alerts:
                await send_smart_alert(alert)
            await problems.update(DISK_PROBLEMS, disk_problems(_disk_verdicts))
        except Exception as e:
            logger.error("SMART monitor error: %s", e)

        await asyncio.sleep(SMART_CHECK_INTERVAL)


def parse_btrfs_device_stats(pool_path: str) -> Optional[list[RaidDeviceStats]]:
    """Parse btrfs device stats output for error counts.

    Returns None when the counters cannot be read (the command failed, e.g.
    because nothing is mounted at pool_path, or printed no counters). That
    must not be treated as "no errors".
    """
    try:
        result = host.run_on_host(["btrfs", "device", "stats", pool_path], timeout=30)
    except Exception:
        return None
    if result.returncode != 0:
        return None

    devices = {}
    # Parse output like: [/dev/sda].write_io_errs    0
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        match = re.match(r'\[([^\]]+)\]\.(\w+)\s+(\d+)', line)
        if match:
            device = match.group(1)
            stat_name = match.group(2)
            value = int(match.group(3))

            if device not in devices:
                devices[device] = {
                    "device": device,
                    "write_io_errs": 0,
                    "read_io_errs": 0,
                    "flush_io_errs": 0,
                    "corruption_errs": 0,
                    "generation_errs": 0,
                }
            devices[device][stat_name] = value

    result_list = []
    for dev_data in devices.values():
        total = sum([
            dev_data["write_io_errs"],
            dev_data["read_io_errs"],
            dev_data["flush_io_errs"],
            dev_data["corruption_errs"],
            dev_data["generation_errs"],
        ])
        result_list.append(RaidDeviceStats(
            **dev_data,
            has_errors=total > 0
        ))

    return result_list or None


def _raid_health_of_pool(info: PoolInfo) -> RaidHealthInfo:
    """RAID health of one pool from its storage view (blocking: run it in a thread).

    The status is the pool health of the Storage page, so missing devices,
    error counters and a filesystem forced read-only are reported the same
    way everywhere. The per-device counters are added for the details.
    """
    mount_point = storage_pools.pool_mountpoint(info.name)
    devices = parse_btrfs_device_stats(mount_point) if info.mounted else None
    return RaidHealthInfo(
        pool=info.name,
        mount_point=mount_point,
        profile=info.raid_level,
        status=info.health,
        devices=devices or [],
        total_errors=info.device_errors,
        last_checked=utc_text(utc_now()),
        missing_devices=info.missing_devices,
        read_only=info.read_only,
        forced_read_only=info.forced_read_only,
    )


@dataclass
class _PoolsRead:
    """What a check of the RAID health read of the pools (storage.pools.pools_and_managed_names)."""

    pools: list[PoolInfo]
    managed: Optional[set[str]]  # the names in the host's fstab; None when it could not be read


# The pools that the latest check read, until the RAID monitor takes them for the problems of the
# pools (ADR-0332): its check and the pool problems come from one read of the host.
_pools_read: Optional[_PoolsRead] = None


def _raid_health_and_pools() -> tuple[list[RaidHealthInfo], _PoolsRead]:
    # Pools are btrfs filesystems labelled with a pool name, mounted or not;
    # other directories under the pools base are not pools. Read without a scan
    # of the disks, which would spin up a sleeping one every RAID_CHECK_INTERVAL
    # (ADR-0378): the mounted pools as the kernel knows them, the others as udev
    # recorded them.
    pools, managed = storage_pools.pools_and_managed_names(scan=False)
    return [_raid_health_of_pool(info) for info in pools], _PoolsRead(pools, managed)


async def check_raid_health() -> list[RaidHealthInfo]:
    """Check RAID health for all BTRFS pools.

    Keeps what it read of the pools for the RAID monitor that called it (_take_pools_read): on
    the event loop as it returns, so that the read of another check never comes between.
    """
    global _pools_read
    health, _pools_read = await asyncio.to_thread(_raid_health_and_pools)
    return health


def _take_pools_read() -> Optional[_PoolsRead]:
    global _pools_read
    read, _pools_read = _pools_read, None
    return read


# The sources of the problems that the monitors find (health.problems).
POOL_PROBLEMS = "pools"
DISK_PROBLEMS = "disks"
# A mounted managed pool this full is low on space: a warning; and this full, an error (ADR-0332).
# It stays low on space until it is less full than POOL_LOW_SPACE_CLEARED: a pool that hovers
# at POOL_LOW_SPACE is alerted once.
POOL_LOW_SPACE = 0.90
POOL_NEARLY_FULL = 0.95
POOL_LOW_SPACE_CLEARED = 0.85


def pool_problems(
    pools: list[PoolInfo], managed: Optional[set[str]], earlier: dict[str, problems.Problem]
) -> dict[str, problems.Problem]:
    """The problems of the pools that UNOS manages, by key, from one read of the pools (ADR-0332).

    ``managed`` names the pools whose entries the host's fstab has; when it could not be read
    (None), the problems found before (``earlier``) stay. A managed pool that is not mounted, or
    of which btrfs finds no disk, is a warning. One that is degraded (devices missing, or errors
    in their counters, which a scrub counts too) is an error, and so is one that btrfs made
    read-only after an error (that, rather than degraded). A mounted one is low on space at
    POOL_LOW_SPACE of its size, a warning, and at POOL_NEARLY_FULL an error, until it is below
    POOL_LOW_SPACE_CLEARED. A pool whose state could not be read (health unknown) keeps the
    problems found before. Pools that UNOS does not manage have none: the RAID monitor alerts the
    changes of their status itself.
    """
    if managed is None:
        return dict(earlier)
    found: dict[str, problems.Problem] = {}

    def add(kind: str, severity: problems.Severity, name: str, title: str, detail: str) -> None:
        key = f"{kind}:{name}"
        found[key] = problems.Problem(
            key=key, severity=severity, kind=kind, subject=name, title=title, detail=detail
        )

    listed = {info.name for info in pools if info.persistent}
    for name in sorted(managed - listed):
        add("pool_unmounted", "warning", name, f"Pool '{name}' is not mounted",
            f"The host finds no disk of pool {name}: the pool is not mounted, and its shares and apps "
            "cannot reach their data. Connect its disks.")
    for info in pools:
        name = info.name
        if not info.persistent:
            continue
        if info.health == "unknown":
            found.update({key: problem for key, problem in earlier.items() if problem.subject == name})
            continue
        if info.health == "unmounted":
            add("pool_unmounted", "warning", name, f"Pool '{name}' is not mounted",
                f"Nothing is mounted at {storage_pools.pool_mountpoint(name)}: the shares and apps of the "
                "pool cannot reach their data. Mount it on the Storage page.")
            continue
        causes = []
        if info.missing_devices:
            causes.append(f"{info.missing_devices} device(s) of the pool are missing.")
        if info.device_errors:
            causes.append(
                f"The error counters of its devices show {info.device_errors} error(s), of reads "
                "and writes or found by a scrub: btrfs device stats names the devices."
            )
        if info.forced_read_only:
            add("pool_read_only", "error", name, f"Pool '{name}' is read-only after an error",
                " ".join(["btrfs made the filesystem read-only after a serious error, and every write "
                          "to it fails: check the kernel log (dmesg).", *causes]))
        elif info.health in ("degraded", "error"):
            add("pool_degraded", "error", name, f"Pool '{name}' is degraded",
                " ".join(causes) or "Check its disks on the Storage page.")
        used = info.used_size / info.total_size if info.total_size > 0 else 0.0
        low = POOL_LOW_SPACE_CLEARED if f"pool_low_space:{name}" in earlier else POOL_LOW_SPACE
        if used >= low:
            add("pool_low_space", "error" if used >= POOL_NEARLY_FULL else "warning", name,
                f"Pool '{name}' is {info.used_size * 100 // info.total_size}% full",
                f"{_format_capacity(info.used_size)} of the {_format_capacity(info.total_size)} of pool "
                f"{name} are used: free some space, or add a disk to the pool.")
    return found


async def _update_pool_problems() -> tuple[set[str], set[str]]:
    """Report the problems of the managed pools that the RAID check just read (ADR-0332).

    Returns the names of the managed pools, and those whose pool_degraded or pool_read_only
    problem is new: its alert names their device errors. Nothing when the check read no pools
    (the tests replace it), and when the problems fail: the RAID monitor then alerts every pool as
    it alerts the others.
    """
    read = _take_pools_read()
    if read is None:
        return set(), set()
    try:
        earlier = problems.of_source(POOL_PROBLEMS)
        found = pool_problems(read.pools, read.managed, earlier)
        await problems.update(POOL_PROBLEMS, found)
    except Exception:
        logger.exception("RAID monitor: could not report the problems of the pools")
        return set(), set()
    health = {"pool_degraded", "pool_read_only"}
    alerted = {p.subject for key, p in found.items() if p.kind in health and key not in earlier}
    return {info.name for info in read.pools if info.persistent}, alerted


async def _update_volume_usage(pools: list[PoolInfo]) -> None:
    """Read the quota use of the volumes of the pools that the RAID check just read, keep the hour's
    points of their history and report the volumes at a level of their quota (storage.volume_usage).
    A failure is logged: the pass's other alerts went before."""
    try:
        await volume_usage.check(pools)
    except Exception:
        logger.exception("RAID monitor: could not read the quota use of the volumes")


async def send_raid_alert(pool: str, status: str, errors: int, details: str):
    """Send notification about RAID health issues."""
    event = NotificationEvent(
        type=NotificationType.ERROR if status == "error" else NotificationType.WARNING,
        title=f"RAID Pool '{pool}' {status.upper()}",
        message=f"Pool {pool} has detected {errors} error(s).\n\n{details}\n\nImmediate attention may be required.",
        timestamp=utc_now(),
    )
    await _deliver_alert(event)


def _raid_status(data: object) -> dict:
    """The decoded RAID status file: its well-formed pool entries; ValueError for no object."""
    if not isinstance(data, dict):
        raise ValueError("it holds no status by pool")
    return {pool: entry for pool, entry in data.items() if isinstance(entry, dict)}


def raid_status_or_none() -> Optional[dict]:
    """The RAID status the last check kept (only well-formed pool entries); None without one.

    A file that cannot be read as a status is kept as raid_status.json.bad (ADR-0324), and there
    is none then; one that cannot be opened raises.
    """
    return state_files.load_json(Path(RAID_STATUS_FILE), _raid_status, lambda: None, what="the RAID status")


def load_previous_raid_status() -> dict:
    """The RAID status the last check kept, to detect new errors; {} without one.

    A cache of the monitor's: a file that cannot be opened reads as none, with a warning, so that
    the check and its alerts go on (ADR-0324).
    """
    try:
        return raid_status_or_none() or {}
    except OSError as exc:
        logger.warning("Could not read the RAID status %s: %s", RAID_STATUS_FILE, exc)
        return {}


# The latest RAID check, with its time (GET /raid-monitor/status). The file
# keeps the state the next check compares with for its alerts; it is on the
# system disk (an SD card on small boards), so it is written only when that
# state changes, not after every check.
_latest_raid_status: Optional[dict] = None


def _raid_state(status: dict) -> dict:
    """A RAID status without the times of the checks."""
    return {
        pool: ({k: v for k, v in entry.items() if k != "last_checked"} if isinstance(entry, dict) else entry)
        for pool, entry in status.items()
    }


def save_raid_status(status: dict):
    """Keep the current RAID status, and write it to RAID_STATUS_FILE if more than its check times changed."""
    global _latest_raid_status
    _latest_raid_status = status
    path = Path(RAID_STATUS_FILE)
    if path.exists() and _raid_state(load_previous_raid_status()) == _raid_state(status):
        return
    state_files.write_json(path, status, sync=False)


async def raid_monitor_loop():
    """Background loop for RAID health monitoring.

    The pools that UNOS manages report their problems (pool_problems), which alert a pool that
    is not mounted, degraded, read-only or low on space once (ADR-0332), and so do the volumes of
    the mounted pools that reached a level of their quota (storage.volume_usage); for the pools the
    loop alerts new device errors, unless the pool's new pool_degraded or pool_read_only problem of
    the same check names them, and a state that could not be read. For the other pools it alerts new
    errors, a filesystem forced read-only, and a status that turned from healthy.
    """
    global _raid_monitor_running

    while _raid_monitor_running:
        read = None
        try:
            health_info = await check_raid_health()
            # The same read of the pools for the quotas of their volumes, after the RAID alerts.
            read = _pools_read
            managed, alerted = await _update_pool_problems()
            previous_status = load_previous_raid_status()
            current_status = {}

            for pool_health in health_info:
                pool_name = pool_health.pool
                current_status[pool_name] = {
                    "status": pool_health.status,
                    "total_errors": pool_health.total_errors,
                    "forced_read_only": pool_health.forced_read_only,
                    "last_checked": pool_health.last_checked
                }

                # Check if this is a new issue
                prev = previous_status.get(pool_name, {})
                prev_errors = prev.get("total_errors", 0)

                # Alert if new errors detected, if btrfs has just made the
                # filesystem read-only (no error counter moves for that, and
                # the pool may already be degraded or in error), or if the
                # status changed from healthy (or was never recorded) to
                # anything else. A managed pool's problems alert the last two, but for a state that
                # cannot be read: its problems stay as they were found before.
                new_errors = pool_health.total_errors > prev_errors
                if pool_name in managed:
                    alert = (new_errors and pool_name not in alerted) or (
                        pool_health.status == "unknown" and prev.get("status", "healthy") == "healthy"
                    )
                else:
                    alert = (
                        new_errors
                        or (pool_health.forced_read_only and not prev.get("forced_read_only", False))
                        or (pool_health.status != "healthy" and prev.get("status", "healthy") == "healthy")
                    )
                if alert:
                    # Build error details
                    details = []
                    if pool_health.status == "unmounted":
                        details.append(
                            f"Nothing is mounted at {pool_health.mount_point}."
                        )
                    elif pool_health.status == "unknown":
                        details.append(
                            "The mount state or the device error counters could not be read."
                        )
                    if pool_health.forced_read_only:
                        details.append(
                            "btrfs made the filesystem read-only after a serious error: "
                            "check the kernel log (dmesg)."
                        )
                    elif pool_health.read_only:
                        details.append("The filesystem is mounted read-only.")
                    if pool_health.missing_devices:
                        details.append(
                            f"{pool_health.missing_devices} device(s) of the pool are missing."
                        )
                    for dev in pool_health.devices:
                        if dev.has_errors:
                            details.append(
                                f"{dev.device}: write={dev.write_io_errs}, "
                                f"read={dev.read_io_errs}, "
                                f"corruption={dev.corruption_errs}"
                            )

                    await send_raid_alert(
                        pool_name,
                        pool_health.status,
                        pool_health.total_errors,
                        "\n".join(details) if details else "Check disk health immediately."
                    )

            save_raid_status(current_status)

        except Exception as e:
            logger.error("RAID monitor error: %s", e)

        # A step of its own: a failure of the RAID alerts or of their status file does not skip it.
        if read is not None:
            await _update_volume_usage(read.pools)

        await asyncio.sleep(RAID_CHECK_INTERVAL)


def _format_capacity(size: int) -> str:
    """A size in bytes as the Health page shows it ("3.64 TB", "476.94 GB"); "Unknown" for 0."""
    if not size:
        return "Unknown"
    if size >= 1024**4:
        return f"{size / 1024**4:.2f} TB"
    return f"{size / 1024**3:.2f} GB"


def parse_smart_data(device: str) -> SmartInfo:
    """The SMART data of a whole disk, from ``smartctl -n standby -j -a``.

    A disk that sleeps is left asleep (ADR-0378): its health status is STANDBY, without data.
    """
    try:
        result = host.run_smartctl(["-n", "standby", "-j", "-a"], device, timeout=SMARTCTL_TIMEOUT)
        if storage_disks.smartctl_found_it_asleep(result):
            return SmartInfo(
                device=device, model="Unknown", serial="Unknown", firmware="Unknown", capacity="Unknown",
                temperature=None, power_on_hours=None, power_cycle_count=None, health_status="STANDBY",
                attributes=[], self_test_status=None, last_test_result=None,
            )
        data = json.loads(result.stdout)
    except host.NoHostDisk as e:  # it left the host after it was listed (ADR-0414)
        raise HTTPException(400, e.strerror)
    except host.DiskNodeError as e:  # logged by host.container_disk_node, once per disk
        raise HTTPException(500, f"Failed to get SMART data: {e.strerror}")
    except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        raise HTTPException(500, f"Failed to get SMART data: {e}")

    # Extract basic info
    model = data.get("model_name", "Unknown")
    serial = data.get("serial_number", "Unknown")
    firmware = data.get("firmware_version", "Unknown")
    
    capacity = _format_capacity(data.get("user_capacity", {}).get("bytes", 0))

    # Health status
    health_status = "UNKNOWN"
    smart_status = data.get("smart_status", {})
    if smart_status.get("passed") is True:
        health_status = "PASSED"
    elif smart_status.get("passed") is False:
        health_status = "FAILED"

    # Temperature
    temperature = None
    temp_data = data.get("temperature", {})
    if "current" in temp_data:
        temperature = temp_data["current"]

    # Power on hours and cycle count
    power_on_hours = None
    power_cycle_count = None
    
    # Parse attributes
    attributes = []
    ata_attrs = data.get("ata_smart_attributes", {}).get("table", [])
    
    for attr in ata_attrs:
        attr_id = attr.get("id", 0)
        attr_name = attr.get("name", "Unknown")
        value = attr.get("value", 0)
        worst = attr.get("worst", 0)
        thresh = attr.get("thresh", 0)
        raw_value = str(attr.get("raw", {}).get("value", ""))
        
        # Determine status
        status = "ok"
        if thresh > 0 and value <= thresh:
            status = "critical"
        elif thresh > 0 and value <= thresh + 10:
            status = "warning"
        
        # Extract special values
        if attr_name == "Power_On_Hours":
            try:
                power_on_hours = int(raw_value)
            except ValueError:
                pass
        elif attr_name == "Power_Cycle_Count":
            try:
                power_cycle_count = int(raw_value)
            except ValueError:
                pass
        elif attr_name == "Temperature_Celsius" and temperature is None:
            try:
                temperature = int(raw_value.split()[0]) if raw_value else None
            except (ValueError, IndexError):
                pass
        
        attributes.append(SmartAttribute(
            id=attr_id,
            name=attr_name,
            value=value,
            worst=worst,
            thresh=thresh,
            raw_value=raw_value,
            status=status
        ))

    # NVMe handling
    nvme_health = data.get("nvme_smart_health_information_log", {})
    nvme_numbers = None
    if nvme_health:
        if temperature is None:
            temperature = nvme_health.get("temperature")
        if power_on_hours is None:
            power_on_hours = nvme_health.get("power_on_hours")
        if power_cycle_count is None:
            power_cycle_count = nvme_health.get("power_cycles")
        # Without lists such as temperature_sensors.
        nvme_numbers = {
            key: value
            for key, value in nvme_health.items()
            if isinstance(value, int) and not isinstance(value, bool)
        }

    # The last self-test: the first entry of the ATA self-test log, or of the
    # NVMe one (smartctl 7.3 and later), where the newest test comes first.
    self_test_status = None
    last_test_result = None
    ata_tests = data.get("ata_smart_self_test_log", {}).get("standard", {}).get("table", [])
    nvme_tests = data.get("nvme_self_test_log", {}).get("table", [])
    if ata_tests:
        last_test = ata_tests[0]
        last_test_result = last_test.get("status", {}).get("string", "Unknown")
        self_test_status = last_test.get("type", {}).get("string", "Unknown")
    elif nvme_tests:
        last_test = nvme_tests[0]
        last_test_result = last_test.get("self_test_result", {}).get("string", "Unknown")
        self_test_status = last_test.get("self_test_code", {}).get("string", "Unknown")

    return SmartInfo(
        device=device,
        model=model,
        serial=serial,
        firmware=firmware,
        capacity=capacity,
        temperature=temperature,
        power_on_hours=power_on_hours,
        power_cycle_count=power_cycle_count,
        health_status=health_status,
        attributes=attributes,
        nvme_health=nvme_numbers or None,
        self_test_status=self_test_status,
        last_test_result=last_test_result
    )


def _disk_health(disk: storage_disks.HostDisk) -> SmartInfo:
    """SMART health of a whole disk of the host (blocking: smartctl).

    When smartctl fails, the disk is still listed, with the status UNKNOWN.
    The model and the capacity that lsblk reports stand in for those that
    smartctl does not report: with -j it prints JSON without them when it
    cannot open or identify the disk, such as one behind a USB bridge it
    does not know. A disk that sleeps (STANDBY) gets lsblk's serial too.
    """
    try:
        info = parse_smart_data(disk.path)
    except Exception:
        info = SmartInfo(
            device=disk.path,
            model="Unknown",
            serial="Unknown",
            firmware="Unknown",
            capacity="Unknown",
            temperature=None,
            power_on_hours=None,
            power_cycle_count=None,
            health_status="UNKNOWN",
            attributes=[],
            self_test_status=None,
            last_test_result=None,
        )
    from_lsblk: dict[str, str] = {}
    if info.model == "Unknown" and disk.model:
        from_lsblk["model"] = disk.model
    if info.capacity == "Unknown":
        from_lsblk["capacity"] = _format_capacity(disk.size)
    if info.health_status == "STANDBY" and disk.serial:
        from_lsblk["serial"] = disk.serial
    return info.model_copy(update=from_lsblk)


# The SMART endpoints are plain functions: FastAPI runs them in its thread
# pool, so smartctl (seconds per disk) and lsblk on the host do not block the
# event loop.
@router.get("/disks")
def get_disk_health(_: dict = Depends(verify_token)):
    """SMART health of the host's whole disks, as the Storage page lists them.

    A system disk on NVMe, SATA or USB is included; the boot SD card/eMMC
    and virtual devices are not (storage.disks.host_disks). Every disk
    listed here answers on GET /health/disk/{device}. 500 when the host's
    disks cannot be listed.
    """
    return {"disks": [_disk_health(disk) for disk in storage_disks.host_disks_or_500()]}


@router.get("/disk/{device:path}")
def get_disk_smart(device: str, _: dict = Depends(verify_token)):
    """Get detailed SMART data for a whole disk of the host (``sda`` or ``/dev/sda``).

    400 for anything else (a partition, the boot SD card, /dev/null, a
    /dev/disk/by-id link, ...); 500 when the host's disks cannot be listed.
    """
    return parse_smart_data(storage_disks.whole_disk_device(device))


@router.post("/disk/{device:path}/test")
def start_smart_test(
    device: str,
    test_type: str = "short",
    background_tasks: BackgroundTasks = None,
    _: dict = Depends(verify_token)
):
    """Start a SMART self-test on a whole disk of the host (``sda`` or ``/dev/sda``).

    400 for another device or test type, also for a disk that left the host after it was listed;
    500 when the host's disks cannot be listed, smartctl fails, the container's node of the disk
    cannot be made (ADR-0414), and when smartctl does not answer within SMARTCTL_TIMEOUT: it is
    killed then (ADR-0290).
    """
    if test_type not in ["short", "long", "conveyance"]:
        raise HTTPException(400, "Invalid test type. Use: short, long, or conveyance")

    device = storage_disks.whole_disk_device(device)

    try:
        result = host.run_smartctl(["-t", test_type], device, timeout=SMARTCTL_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise HTTPException(
            500, f"smartctl did not answer within {SMARTCTL_TIMEOUT} seconds: {device} may not be responding"
        )
    except host.NoHostDisk as exc:
        raise HTTPException(400, exc.strerror)
    except host.DiskNodeError as exc:  # logged by host.container_disk_node, once per disk
        raise HTTPException(500, f"smartctl could not run on {device}: {exc.strerror}")

    if result.returncode not in [0, 4]:  # 4 means test started
        raise HTTPException(500, f"Failed to start test: {result.stderr}")

    return SmartTestResult(
        status="started",
        message=f"{test_type.capitalize()} test started on {device}"
    )


@router.get("/overview")
def get_health_overview(_: dict = Depends(verify_token)):
    """Health counts of the disks of GET /health/disks (500 when they cannot be listed); a disk that
    sleeps counts as standby (ADR-0378)."""
    disks = storage_disks.host_disks_or_500()

    total_disks = len(disks)
    healthy_disks = 0
    warning_disks = 0
    failed_disks = 0
    unknown_disks = 0
    standby_disks = 0
    total_temp = 0
    temp_count = 0

    for smart_info in map(_disk_health, disks):
        if smart_info.health_status == "PASSED":
            # Check for warning attributes
            has_warning = any(attr.status in ["warning", "critical"] for attr in smart_info.attributes)
            if has_warning:
                warning_disks += 1
            else:
                healthy_disks += 1
        elif smart_info.health_status == "FAILED":
            failed_disks += 1
        elif smart_info.health_status == "STANDBY":
            standby_disks += 1
        else:  # also when smartctl failed
            unknown_disks += 1

        if smart_info.temperature:
            total_temp += smart_info.temperature
            temp_count += 1

    avg_temp = round(total_temp / temp_count) if temp_count > 0 else None

    return {
        "total_disks": total_disks,
        "healthy": healthy_disks,
        "warning": warning_disks,
        "failed": failed_disks,
        "unknown": unknown_disks,
        "standby": standby_disks,
        "average_temperature": avg_temp
    }


# ==================== RAID Health Monitoring ====================

@router.get("/raid", response_model=list[RaidHealthInfo])
async def get_raid_health(_: dict = Depends(verify_token)):
    """Get RAID health status for all BTRFS pools."""
    try:
        return await check_raid_health()
    except Exception as exc:
        logger.exception("Could not get raid health")
        raise HTTPException(500, f"Could not check the pools: {exc}") from exc


@router.get("/raid/{pool_name}")
async def get_pool_raid_health(pool_name: str, _: dict = Depends(verify_token)):
    """Get RAID health for a specific pool."""
    name = validate_pool_name(pool_name)
    try:
        info = await asyncio.to_thread(storage_pools.get_pool, name)
    except storage_pools.PoolNotFoundError as exc:
        raise HTTPException(404, f"Pool not found: {name}") from exc
    except Exception as exc:
        logger.exception("Could not get pool raid health")
        raise HTTPException(500, f"Could not check pool {name}: {exc}") from exc

    return await asyncio.to_thread(_raid_health_of_pool, info)


@router.post("/raid/{pool_name}/reset-stats")
async def reset_device_stats(pool_name: str, _: dict = Depends(verify_token)):
    """Reset error statistics for a pool (after replacing a drive)."""
    name = validate_pool_name(pool_name)
    pool_path = str(Path(storage_pools.settings.pools_base_path) / name)
    if not Path(pool_path).exists():
        raise HTTPException(404, f"Pool not found: {name}")

    # Off the event loop, with a timeout: btrfs can block on a hung pool. It is
    # killed then and not waited for (ADR-0290).
    try:
        result = await asyncio.to_thread(
            host.run_in_container, ["btrfs", "device", "stats", "--reset", pool_path],
            timeout=storage_pools.SCRUB_COMMAND_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(
            500, f"btrfs device stats did not answer within {storage_pools.SCRUB_COMMAND_TIMEOUT} "
            f"seconds: pool {name} may not be responding",
        )

    if result.returncode != 0:
        raise HTTPException(500, f"Failed to reset stats: {result.stderr}")

    _clear_raid_errors(name)

    return {"status": "ok", "message": f"Error stats reset for pool {name}"}


def _clear_raid_errors(pool_name: str) -> None:
    """After an error stats reset: the pool has no errors in the RAID status.

    Starts from the latest check: the file is written only when the state
    changes, so its check times can be old.
    """
    if _latest_raid_status is not None:
        status = copy.deepcopy(_latest_raid_status)
    else:
        status = load_previous_raid_status()
    if pool_name in status:
        status[pool_name]["total_errors"] = 0
        status[pool_name]["status"] = "healthy"
        save_raid_status(status)


def _answered_raid_status(status: dict) -> dict:
    """A RAID status with the times of its checks as the API answers them: a time that the file kept
    without an offset is UTC (ADR-0401)."""
    return {
        pool: (
            {**entry, "last_checked": stored_time_text(entry["last_checked"])}
            if isinstance(entry, dict) and "last_checked" in entry else entry
        )
        for pool, entry in status.items()
    }


@router.get("/raid-monitor/status")
async def get_raid_monitor_status(_: dict = Depends(verify_token)):
    """Get RAID monitor status."""
    return {
        "running": _raid_monitor_running,
        "check_interval_seconds": RAID_CHECK_INTERVAL,
        "last_status": _answered_raid_status(
            _latest_raid_status if _latest_raid_status is not None else load_previous_raid_status()
        ),
    }


# ==================== SMART Trend Monitoring ====================

# Plain functions, like the trends and predictions below: a history that the
# monitor has not read yet is read from its file, in FastAPI's thread pool.
@router.get("/smart/history/{serial}")
def get_smart_history(serial: str, _: dict = Depends(verify_token)):
    """Get SMART history for a specific disk by serial number."""
    history = _smart_history_or_500()
    if serial not in history:
        raise HTTPException(404, f"No history found for serial: {serial}")

    # Times that the history kept without an offset are UTC (ADR-0401).
    entries = [{**entry, "timestamp": stored_time_text(entry.get("timestamp"))} for entry in history[serial]]
    return {
        "serial": serial,
        "entries": len(entries),
        "oldest": entries[0]["timestamp"] if entries else None,
        "newest": entries[-1]["timestamp"] if entries else None,
        "data": entries[-168:]  # Last week of hourly data
    }


class SmartDiskTrend(BaseModel):
    serial: str  # smartctl's: the key of the disk's history ("unknown" without one)
    device: str  # the path of the disk's latest snapshot
    data_points: int  # the disk's snapshots in the history
    # The counters that rose over the disk's last 24 values (analyze_smart_trends); none: steady.
    alerts: list[SmartTrendAlert]


class SmartTrends(BaseModel):
    disks: list[SmartDiskTrend]
    critical_count: int
    warning_count: int


def _disk_trends(history: dict[str, list[dict]]) -> list[SmartDiskTrend]:
    """The trend of every disk in the SMART history, in its order (ADR-0270).

    The disks of the shared history "unknown" (no serial) are told apart by
    their paths.
    """
    trends = []
    for serial, entries in history.items():
        shared = not serial or serial == _NO_SERIAL
        devices = list(dict.fromkeys(e.get("device") for e in entries)) if shared else [None]
        for device in devices:
            snapshots = _snapshots_of_one_disk(serial, entries, device)
            if not snapshots:
                continue
            trends.append(SmartDiskTrend(
                serial=serial,
                device=str(snapshots[-1].get("device", "")),
                data_points=len(snapshots),
                alerts=analyze_smart_trends(serial, snapshots),
            ))
    return trends


@router.get("/smart/trends", response_model=SmartTrends)
def get_smart_trends(_: dict = Depends(verify_token)):
    """The SMART trend of every disk in the history, steady ones included (ADR-0270).

    Removed disks stay in the history and are listed too; 500 when the
    history cannot be read.
    """
    disks = _disk_trends(_smart_history_or_500())
    severities = Counter(alert.severity for disk in disks for alert in disk.alerts)
    return SmartTrends(
        disks=disks,
        critical_count=severities["critical"],
        warning_count=severities["warning"],
    )


class DiskPrediction(BaseModel):
    serial: str  # smartctl's, as GET /health/disks reports it
    device: str  # the path of the disk now
    risk_score: int  # 0 to 100
    status: str  # healthy, watch, monitor_closely, replace_soon
    risk_factors: list[str]  # the reasons: one for each addition to the score
    recommendation: str
    data_points: int  # snapshots in the disk's history


class DiskPredictions(BaseModel):
    predictions: list[DiskPrediction]


# A day of hourly snapshots: the least history a prediction needs, and the
# window of its rate. The history keeps SMART_HISTORY_LIMIT snapshots per disk.
MIN_PREDICTION_SNAPSHOTS = 24
# The status of the replace-soon mark in Disk Health and on the dashboard (ADR-0235).
REPLACE_SOON_SCORE = 50


def predict_disk_failure(serial: str, entries: list[dict], device: str) -> Optional[DiskPrediction]:
    """The failure prediction of the disk now at ``device`` from its SMART history (ADR-0235).

    None with fewer than MIN_PREDICTION_SNAPSHOTS snapshots, or when no
    snapshot holds any of CRITICAL_ATTRIBUTES (NVMe disks: the monitor records
    ATA attributes only). Each counter is read from the snapshots that hold
    it: one without it (smartctl could not read the attribute table) is no
    value of 0. Each of the sector counts 5, 197 and 198 that rose over the
    history adds 30, and 20 more when it rose by more than 0.1 per snapshot
    over its last MIN_PREDICTION_SNAPSHOTS; each of 187, 188 and 199 that
    rose above 100 adds 10. REPLACE_SOON_SCORE or more is replace_soon.
    """
    if len(entries) < MIN_PREDICTION_SNAPSHOTS:
        return None
    if not any(_reports_attribute(e, attr_id) for e in entries for attr_id in CRITICAL_ATTRIBUTES):
        return None

    # Calculate trend scores for critical attributes
    risk_score = 0
    risk_factors = []

    for attr_id, attr_name in CRITICAL_ATTRIBUTES.items():
        # Get values over time
        values = [_attribute_value(e, attr_id) for e in entries if _reports_attribute(e, attr_id)]
        if not values:
            continue
        current = values[-1]
        first = values[0]

        # Check if attribute is increasing
        if current > first:
            # Per snapshot (hourly) over the last day, whatever the length of
            # the history: a month of quiet history does not hide a fast rise.
            recent = values[max(0, len(values) - MIN_PREDICTION_SNAPSHOTS)]
            increase_rate = (current - recent) / MIN_PREDICTION_SNAPSHOTS

            if attr_id in [5, 197, 198]:  # Critical sector issues
                if current > 0:
                    risk_score += 30
                    risk_factors.append(f"{attr_name}: {current} (was {first})")
                if increase_rate > 0.1:
                    risk_score += 20
                    risk_factors.append(f"{attr_name} increasing rapidly")
            elif current > 100:
                risk_score += 10
                risk_factors.append(f"{attr_name}: {current}")

    # Determine health status
    if risk_score >= REPLACE_SOON_SCORE:
        status = "replace_soon"
        recommendation = "Disk showing signs of failure. Replace within 30 days."
    elif risk_score >= 30:
        status = "monitor_closely"
        recommendation = "Disk health degrading. Monitor daily and prepare replacement."
    elif risk_score >= 10:
        status = "watch"
        recommendation = "Minor issues detected. Continue monitoring."
    else:
        status = "healthy"
        recommendation = "No concerning trends detected."

    return DiskPrediction(
        serial=serial,
        device=device,
        risk_score=min(risk_score, 100),
        status=status,
        risk_factors=risk_factors,
        recommendation=recommendation,
        data_points=len(entries),
    )


def _histories_of_connected_disks(
    history: dict[str, list[dict]], disks: list[storage_disks.HostDisk]
) -> dict[str, tuple[str, list[dict]]]:
    """The histories of the disks connected now: serial -> (the disk's path now, history).

    The history keeps the serials of removed and replaced disks (ADR-0235).
    Each history goes by its latest snapshot and the lsblk serial stored with
    it. A history whose lsblk serial no connected disk reports belongs to a
    disk that is gone. One whose lsblk serial one connected disk reports and
    no other history holds belongs to that disk, wherever it is now. Any
    other (a USB bridge without a serial; a bridge serial that several disks
    report or several histories hold: identical bridges, a disk replaced in
    its enclosure) belongs to the disk now at the path of that snapshot, if
    this disk reports the history's lsblk serial when it has one and no
    history claims the disk by its serial; of several such histories, the
    later latest snapshot wins. Disks that smartctl reports without a serial
    share one history and get none.
    """
    reported = Counter(disk.serial for disk in disks if disk.serial)
    latest = {serial: entries[-1] for serial, entries in history.items() if entries}
    held = Counter(entry.get("lsblk_serial") for entry in latest.values())
    by_lsblk_serial = {disk.serial: disk for disk in disks if disk.serial}
    by_path = {path: disk for disk in disks for path in disk.paths}
    by_identity: dict[str, str] = {}  # disk path -> serial
    by_place: dict[str, tuple[str, str]] = {}  # disk path -> (timestamp, serial)
    for serial, entry in latest.items():
        if not serial or serial == _NO_SERIAL:
            continue
        lsblk_serial = entry.get("lsblk_serial")
        if lsblk_serial and reported[lsblk_serial] == 0:
            continue  # the disk, or its enclosure, is gone
        if lsblk_serial and reported[lsblk_serial] == 1 and held[lsblk_serial] == 1:
            by_identity[by_lsblk_serial[lsblk_serial].path] = serial
            continue
        disk = by_path.get(entry.get("device"))
        if disk is None or (lsblk_serial and disk.serial != lsblk_serial):
            continue
        taken = str(stored_time_text(str(entry.get("timestamp", ""))))
        if disk.path not in by_place or taken > by_place[disk.path][0]:
            by_place[disk.path] = (taken, serial)
    owners = {path: serial for path, (_, serial) in by_place.items() if path not in by_identity}
    owners.update(by_identity)
    return {serial: (path, history[serial]) for path, serial in owners.items()}


# A plain function, like the SMART endpoints above: lsblk runs on the host in
# FastAPI's thread pool. It reads the stored history only and runs no smartctl.
@router.get("/smart/predictions", response_model=DiskPredictions)
def get_disk_predictions(_: dict = Depends(verify_token)):
    """Failure predictions from the SMART trends of the disks connected now (ADR-0235).

    The disks of GET /health/disks; 500 when the host's disks cannot be
    listed or the SMART history cannot be read.
    """
    disks = storage_disks.host_disks_or_500()
    histories = _histories_of_connected_disks(_smart_history_or_500(), disks)
    predictions = [
        prediction
        for serial, (device, entries) in histories.items()
        if (prediction := predict_disk_failure(serial, entries, device)) is not None
    ]
    predictions.sort(key=lambda prediction: prediction.risk_score, reverse=True)
    return DiskPredictions(predictions=predictions)


@router.get("/smart-monitor/status")
def get_smart_monitor_status(_: dict = Depends(verify_token)):
    """Get SMART trend monitor status."""
    history = _smart_history_or_500()
    return {
        "running": _smart_monitor_running,
        "check_interval_seconds": SMART_CHECK_INTERVAL,
        "disks_tracked": len(history),
        "total_data_points": sum(len(v) for v in history.values())
    }


async def start_health_monitors():
    """Start health monitors on app startup."""
    global _raid_monitor_running, _raid_monitor_task
    global _smart_monitor_running, _smart_monitor_task

    _raid_monitor_running = True
    _raid_monitor_task = asyncio.create_task(raid_monitor_loop())

    _smart_monitor_running = True
    _smart_history.stopped = False
    _smart_monitor_task = asyncio.create_task(smart_monitor_loop())


async def stop_health_monitors():
    """Stop health monitors on app shutdown."""
    global _raid_monitor_running, _raid_monitor_task
    global _smart_monitor_running, _smart_monitor_task

    _raid_monitor_running = False
    if _raid_monitor_task:
        _raid_monitor_task.cancel()

    _smart_monitor_running = False
    if _smart_monitor_task:
        _smart_monitor_task.cancel()
    # The snapshots since the history was last written (ADR-0269).
    await asyncio.to_thread(save_unsaved_smart_history)
    # The points of the volumes' use since their history was last written (storage.volume_usage).
    await asyncio.to_thread(volume_usage.save_unsaved)
