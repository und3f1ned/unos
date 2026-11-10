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
from datetime import datetime
from pathlib import Path
from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from pydantic import BaseModel
from typing import Optional

from .. import host, state_files
from ..auth.utils import verify_token
from ..security.validators import validate_pool_name
from ..storage import disks as storage_disks
from ..storage import pools as storage_pools
from ..storage.schemas import PoolInfo
from ..notifications.router import load_configs as load_notification_configs
from ..notifications.router import history_entry, save_logs, send_email, send_telegram
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
    health_status: str  # PASSED, FAILED, UNKNOWN
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
        result = host.run_in_container(
            ["smartctl", "-n", "standby", "-j", "-a", device], timeout=SMARTCTL_TIMEOUT
        )
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

        return SmartHistoryEntry(
            timestamp=datetime.now().isoformat(),
            device=device,
            serial=serial,
            attributes=attributes,
            temperature=temperature,
            power_on_hours=power_on_hours
        )
    except Exception:
        return None


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
    """Send an alert through every enabled e-mail/Telegram channel; best effort.

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
            elif config.channel == NotificationChannel.TELEGRAM and config.telegram_config:
                sending = send_telegram(config.telegram_config, event)
            else:
                continue
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
        timestamp=datetime.now().isoformat()
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
    does not stop the recording (ADR-0235).
    """
    try:
        disks = storage_disks.host_disks()
    except Exception as exc:
        logger.warning("SMART monitor: pass skipped, could not list the host's disks: %s", exc)
        return []
    snapshots = []
    for disk in disks:
        snapshot = collect_smart_snapshot(disk.path)
        if snapshot:
            snapshot.lsblk_serial = disk.serial
            # JSON form (str attribute keys), like the entries read from the file.
            snapshots.append(snapshot.model_dump(mode="json"))

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
    """Background loop for SMART trend monitoring."""
    global _smart_monitor_running

    while _smart_monitor_running:
        try:
            # smartctl takes seconds per disk: never run it on the event loop.
            alerts = await asyncio.to_thread(_smart_monitor_pass)
            for alert in alerts:
                await send_smart_alert(alert)
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
        last_checked=datetime.now().isoformat(),
        missing_devices=info.missing_devices,
        read_only=info.read_only,
        forced_read_only=info.forced_read_only,
    )


def _raid_health_of_all_pools() -> list[RaidHealthInfo]:
    # Pools are btrfs filesystems labelled with a pool name, mounted or not;
    # other directories under the pools base are not pools.
    return [_raid_health_of_pool(info) for info in storage_pools.list_pools()]


async def check_raid_health() -> list[RaidHealthInfo]:
    """Check RAID health for all BTRFS pools."""
    return await asyncio.to_thread(_raid_health_of_all_pools)


async def send_raid_alert(pool: str, status: str, errors: int, details: str):
    """Send notification about RAID health issues."""
    event = NotificationEvent(
        type=NotificationType.ERROR if status == "error" else NotificationType.WARNING,
        title=f"RAID Pool '{pool}' {status.upper()}",
        message=f"Pool {pool} has detected {errors} error(s).\n\n{details}\n\nImmediate attention may be required.",
        timestamp=datetime.now().isoformat()
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
    """Background loop for RAID health monitoring."""
    global _raid_monitor_running

    while _raid_monitor_running:
        try:
            health_info = await check_raid_health()
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
                # anything else
                if (
                    pool_health.total_errors > prev_errors
                    or (pool_health.forced_read_only and not prev.get("forced_read_only", False))
                    or (pool_health.status != "healthy" and prev.get("status", "healthy") == "healthy")
                ):
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

        await asyncio.sleep(RAID_CHECK_INTERVAL)


def _format_capacity(size: int) -> str:
    """A size in bytes as the Health page shows it ("3.64 TB", "476.94 GB"); "Unknown" for 0."""
    if not size:
        return "Unknown"
    if size >= 1024**4:
        return f"{size / 1024**4:.2f} TB"
    return f"{size / 1024**3:.2f} GB"


def parse_smart_data(device: str) -> SmartInfo:
    """Parse SMART data from smartctl."""
    try:
        result = host.run_in_container(["smartctl", "-j", "-a", device], timeout=SMARTCTL_TIMEOUT)
        data = json.loads(result.stdout)
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
    does not know.
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

    400 for another device or test type; 500 when the host's disks cannot
    be listed or smartctl fails, and when it does not answer within
    SMARTCTL_TIMEOUT: it is killed then (ADR-0290).
    """
    if test_type not in ["short", "long", "conveyance"]:
        raise HTTPException(400, "Invalid test type. Use: short, long, or conveyance")

    device = storage_disks.whole_disk_device(device)

    try:
        result = host.run_in_container(["smartctl", "-t", test_type, device], timeout=SMARTCTL_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise HTTPException(
            500, f"smartctl did not answer within {SMARTCTL_TIMEOUT} seconds: {device} may not be responding"
        )

    if result.returncode not in [0, 4]:  # 4 means test started
        raise HTTPException(500, f"Failed to start test: {result.stderr}")

    return SmartTestResult(
        status="started",
        message=f"{test_type.capitalize()} test started on {device}"
    )


@router.get("/overview")
def get_health_overview(_: dict = Depends(verify_token)):
    """Health counts of the disks of GET /health/disks (500 when they cannot be listed)."""
    disks = storage_disks.host_disks_or_500()

    total_disks = len(disks)
    healthy_disks = 0
    warning_disks = 0
    failed_disks = 0
    unknown_disks = 0
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
        "average_temperature": avg_temp
    }


# ==================== RAID Health Monitoring ====================

@router.get("/raid", response_model=list[RaidHealthInfo])
async def get_raid_health(_: dict = Depends(verify_token)):
    """Get RAID health status for all BTRFS pools."""
    try:
        return await check_raid_health()
    except Exception as exc:
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


@router.get("/raid-monitor/status")
async def get_raid_monitor_status(_: dict = Depends(verify_token)):
    """Get RAID monitor status."""
    return {
        "running": _raid_monitor_running,
        "check_interval_seconds": RAID_CHECK_INTERVAL,
        "last_status": _latest_raid_status if _latest_raid_status is not None else load_previous_raid_status(),
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

    entries = history[serial]
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
        taken = str(entry.get("timestamp", ""))
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
