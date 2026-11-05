import subprocess
import json
import asyncio
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from pydantic import BaseModel
from typing import Optional

from .. import host
from ..auth.utils import verify_token
from ..security.validators import validate_pool_name
from ..storage import pools as storage_pools
from ..storage.schemas import PoolInfo
from ..notifications.router import load_configs as load_notification_configs
from ..notifications.router import send_email, send_telegram
from ..notifications.schemas import NotificationEvent, NotificationType, NotificationChannel

router = APIRouter(prefix="/health", tags=["health"])
logger = logging.getLogger(__name__)

# System disk patterns to exclude from health monitoring
SYSTEM_DISK_PATTERNS = [
    "mmcblk",      # eMMC storage (system disk)
    "zram",        # Virtual RAM disks
    "loop",        # Loop devices
    "sr",          # CD/DVD drives
    "fd",          # Floppy drives
    "nvme0n1",     # Often system NVMe
    "ram",         # RAM disks
]


def _is_system_disk(name: str, mountpoint: str | None = None, children: list | None = None) -> bool:
    """Check if disk is a system disk that should be hidden."""
    # Check against known system disk patterns
    for pattern in SYSTEM_DISK_PATTERNS:
        if name.startswith(pattern):
            return True
    
    # Check if mounted as root
    if mountpoint == "/":
        return True
    
    # Check if any partition is mounted as root
    if children:
        for child in children:
            if child.get("mountpoint") == "/":
                return True
    
    return False


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
    attributes: list[SmartAttribute]
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
SMARTCTL_TIMEOUT = 30

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
    serial: str
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


def load_smart_history() -> dict[str, list[dict]]:
    """Load SMART history from file."""
    path = Path(SMART_HISTORY_FILE)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def save_smart_history(history: dict[str, list[dict]]):
    """Save SMART history to file."""
    path = Path(SMART_HISTORY_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Keep only last 30 days of data per disk (720 entries at hourly checks)
    for serial in history:
        history[serial] = history[serial][-720:]
    path.write_text(json.dumps(history, indent=2))


def collect_smart_snapshot(device: str) -> Optional[SmartHistoryEntry]:
    """Collect current SMART data for trend tracking (blocking).

    ``-n standby`` leaves disks that sleep alone: reading SMART data would
    spin them up every hour. smartctl then exits with bit 1 set, as it does
    when the device cannot be opened; both give no snapshot. Higher bits
    (failing health, logged errors, ...) still come with data.
    """
    try:
        result = subprocess.run(
            ["smartctl", "-n", "standby", "-j", "-a", device],
            capture_output=True, text=True, timeout=SMARTCTL_TIMEOUT, check=False
        )
        if result.returncode & 0b11:  # bad command line, open failed or asleep
            return None

        data = json.loads(result.stdout)
        serial = data.get("serial_number", "unknown")

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


def analyze_smart_trends(serial: str, history: list[dict]) -> list[SmartTrendAlert]:
    """Analyze SMART history for concerning trends."""
    alerts = []

    if len(history) < 2:
        return alerts

    # Get latest and compare with oldest in window
    latest = history[-1]
    # Compare with 24 hours ago if available, otherwise oldest
    compare_idx = max(0, len(history) - 24)
    compare = history[compare_idx]

    device = latest.get("device", "unknown")

    for attr_id, attr_name in CRITICAL_ATTRIBUTES.items():
        current = _attribute_value(latest, attr_id)
        previous = _attribute_value(compare, attr_id)

        if current > previous:
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

            alerts.append(SmartTrendAlert(
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
            ))

    return alerts


def _send_email_blocking(config, event: NotificationEvent):
    """Run notifications.send_email to completion in the calling alert thread."""
    return asyncio.run(send_email(config, event))


async def _deliver_alert(event: NotificationEvent) -> None:
    """Send an alert through every enabled e-mail/Telegram channel; best effort.

    The senders report failures as (False, error) instead of raising; those are
    logged like timeouts and exceptions.
    """
    loop = asyncio.get_running_loop()
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
    """The trend alerts of ``history`` whose attribute rose since the previous snapshot.

    analyze_smart_trends compares with a snapshot up to a day old, so a single
    increase stays in its result for a day. The monitor runs every hour and
    alerts when the value rises, not again on every pass after it.
    """
    if len(history) < 2:
        return []
    previous = history[-2]
    return [
        alert
        for alert in analyze_smart_trends(serial, history)
        if alert.current_value > _attribute_value(previous, alert.attribute_id)
    ]


def _smart_monitor_pass() -> list[SmartTrendAlert]:
    """One round of the SMART monitor (blocking: lsblk and smartctl per disk).

    Records a snapshot of every data disk that is awake and returns the
    trend alerts of the attributes that rose since the disk's previous
    snapshot.
    """
    result = subprocess.run(
        ["lsblk", "-J", "-o", "NAME,TYPE,MOUNTPOINT"],
        capture_output=True, text=True, timeout=SMARTCTL_TIMEOUT, check=False
    )
    if result.returncode != 0:
        return []
    data = json.loads(result.stdout)
    history = load_smart_history()
    alerts: list[SmartTrendAlert] = []

    for dev in data.get("blockdevices", []):
        if dev.get("type") != "disk":
            continue
        name = dev.get("name", "")
        mountpoint = dev.get("mountpoint")
        children = dev.get("children", [])

        if _is_system_disk(name, mountpoint, children):
            continue

        snapshot = collect_smart_snapshot(f"/dev/{name}")
        if snapshot:
            serial = snapshot.serial
            entries = history.setdefault(serial, [])
            # JSON form (str attribute keys), like the entries read from the file.
            entries.append(snapshot.model_dump(mode="json"))
            alerts.extend(_new_trend_alerts(serial, entries))

    save_smart_history(history)
    return alerts


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
    # other directories under the pools base (e.g. app data an older version
    # put on the boot disk) are not pools.
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


def load_previous_raid_status() -> dict:
    """Load previous RAID status to detect new errors."""
    path = Path(RAID_STATUS_FILE)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def save_raid_status(status: dict):
    """Save current RAID status."""
    path = Path(RAID_STATUS_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(status, indent=2))


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


def parse_smart_data(device: str) -> SmartInfo:
    """Parse SMART data from smartctl."""
    try:
        result = subprocess.run(
            ["smartctl", "-j", "-a", device],
            capture_output=True, text=True, timeout=30
        )
        data = json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        raise HTTPException(500, f"Failed to get SMART data: {e}")

    # Extract basic info
    model = data.get("model_name", "Unknown")
    serial = data.get("serial_number", "Unknown")
    firmware = data.get("firmware_version", "Unknown")
    
    # Capacity
    capacity_bytes = data.get("user_capacity", {}).get("bytes", 0)
    if capacity_bytes:
        capacity = f"{capacity_bytes / (1024**4):.2f} TB" if capacity_bytes >= 1024**4 else f"{capacity_bytes / (1024**3):.2f} GB"
    else:
        capacity = "Unknown"

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
    if nvme_health:
        if temperature is None:
            temperature = nvme_health.get("temperature")
        if power_on_hours is None:
            power_on_hours = nvme_health.get("power_on_hours")
        if power_cycle_count is None:
            power_cycle_count = nvme_health.get("power_cycles")

    # Self-test status
    self_test_status = None
    last_test_result = None
    self_test_log = data.get("ata_smart_self_test_log", {}).get("standard", {})
    if self_test_log:
        tests = self_test_log.get("table", [])
        if tests:
            last_test = tests[0]
            last_test_result = last_test.get("status", {}).get("string", "Unknown")
            self_test_status = last_test.get("type", {}).get("string", "Unknown")

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
        self_test_status=self_test_status,
        last_test_result=last_test_result
    )


# The SMART endpoints are plain functions: FastAPI runs them in its thread
# pool, so smartctl (seconds per disk) does not block the event loop.
@router.get("/disks")
def get_disk_health(_: dict = Depends(verify_token)):
    """Get SMART health for all disks."""
    # List block devices with children to check root mounts
    result = subprocess.run(
        ["lsblk", "-J", "-o", "NAME,TYPE,SIZE,MODEL,MOUNTPOINT"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise HTTPException(500, "Failed to list disks")

    data = json.loads(result.stdout)
    devices = []

    for dev in data.get("blockdevices", []):
        if dev.get("type") != "disk":
            continue
        name = dev.get("name", "")
        mountpoint = dev.get("mountpoint")
        children = dev.get("children", [])
        
        # Skip system disks
        if _is_system_disk(name, mountpoint, children):
            continue
        
        device_path = f"/dev/{name}"
        try:
            smart_info = parse_smart_data(device_path)
            devices.append(smart_info)
        except Exception:
            # Add basic info if SMART fails
            devices.append(SmartInfo(
                device=device_path,
                model=dev.get("model", "Unknown"),
                serial="Unknown",
                firmware="Unknown",
                capacity=dev.get("size", "Unknown"),
                temperature=None,
                power_on_hours=None,
                power_cycle_count=None,
                health_status="UNKNOWN",
                attributes=[],
                self_test_status=None,
                last_test_result=None
            ))

    return {"disks": devices}


@router.get("/disk/{device:path}")
def get_disk_smart(device: str, _: dict = Depends(verify_token)):
    """Get detailed SMART data for a specific disk."""
    if not device.startswith("/dev/"):
        device = f"/dev/{device}"
    
    # Security check
    if ".." in device or not Path(device).exists():
        raise HTTPException(400, "Invalid device path")

    return parse_smart_data(device)


@router.post("/disk/{device:path}/test")
def start_smart_test(
    device: str,
    test_type: str = "short",
    background_tasks: BackgroundTasks = None,
    _: dict = Depends(verify_token)
):
    """Start a SMART self-test."""
    if not device.startswith("/dev/"):
        device = f"/dev/{device}"

    if test_type not in ["short", "long", "conveyance"]:
        raise HTTPException(400, "Invalid test type. Use: short, long, or conveyance")

    # Security check
    if ".." in device or not Path(device).exists():
        raise HTTPException(400, "Invalid device path")

    try:
        result = subprocess.run(
            ["smartctl", "-t", test_type, device],
            capture_output=True, text=True, timeout=30
        )
        
        if result.returncode not in [0, 4]:  # 4 means test started
            raise HTTPException(500, f"Failed to start test: {result.stderr}")

        return SmartTestResult(
            status="started",
            message=f"{test_type.capitalize()} test started on {device}"
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(500, "Command timed out")


@router.get("/overview")
def get_health_overview(_: dict = Depends(verify_token)):
    """Get overall health overview of all disks."""
    # List block devices with children to check root mounts
    result = subprocess.run(
        ["lsblk", "-J", "-o", "NAME,TYPE,MOUNTPOINT"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise HTTPException(500, "Failed to list disks")

    data = json.loads(result.stdout)
    
    total_disks = 0
    healthy_disks = 0
    warning_disks = 0
    failed_disks = 0
    unknown_disks = 0
    total_temp = 0
    temp_count = 0

    for dev in data.get("blockdevices", []):
        if dev.get("type") != "disk":
            continue
        name = dev.get("name", "")
        mountpoint = dev.get("mountpoint")
        children = dev.get("children", [])
        
        # Skip system disks
        if _is_system_disk(name, mountpoint, children):
            continue

        total_disks += 1
        device_path = f"/dev/{name}"

        try:
            smart_info = parse_smart_data(device_path)
            if smart_info.health_status == "PASSED":
                # Check for warning attributes
                has_warning = any(attr.status in ["warning", "critical"] for attr in smart_info.attributes)
                if has_warning:
                    warning_disks += 1
                else:
                    healthy_disks += 1
            elif smart_info.health_status == "FAILED":
                failed_disks += 1
            else:
                unknown_disks += 1

            if smart_info.temperature:
                total_temp += smart_info.temperature
                temp_count += 1
        except Exception:
            unknown_disks += 1

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
    pool_path = f"/mnt/pools/{pool_name}"
    if not Path(pool_path).exists():
        raise HTTPException(404, f"Pool not found: {pool_name}")

    result = subprocess.run(
        ["btrfs", "device", "stats", "--reset", pool_path],
        capture_output=True, text=True
    )

    if result.returncode != 0:
        raise HTTPException(500, f"Failed to reset stats: {result.stderr}")

    # Update saved status
    status = load_previous_raid_status()
    if pool_name in status:
        status[pool_name]["total_errors"] = 0
        status[pool_name]["status"] = "healthy"
        save_raid_status(status)

    return {"status": "ok", "message": f"Error stats reset for pool {pool_name}"}


@router.get("/raid-monitor/status")
async def get_raid_monitor_status(_: dict = Depends(verify_token)):
    """Get RAID monitor status."""
    return {
        "running": _raid_monitor_running,
        "check_interval_seconds": RAID_CHECK_INTERVAL,
        "last_status": load_previous_raid_status()
    }


# ==================== SMART Trend Monitoring ====================

@router.get("/smart/history/{serial}")
async def get_smart_history(serial: str, _: dict = Depends(verify_token)):
    """Get SMART history for a specific disk by serial number."""
    history = load_smart_history()
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


@router.get("/smart/trends")
async def get_smart_trends(_: dict = Depends(verify_token)):
    """Get current SMART trend analysis for all disks."""
    history = load_smart_history()
    all_alerts = []

    for serial, entries in history.items():
        alerts = analyze_smart_trends(serial, entries)
        all_alerts.extend([a.model_dump() for a in alerts])

    return {
        "disks_tracked": len(history),
        "alerts": all_alerts,
        "critical_count": len([a for a in all_alerts if a["severity"] == "critical"]),
        "warning_count": len([a for a in all_alerts if a["severity"] == "warning"])
    }


@router.get("/smart/predictions")
async def get_disk_predictions(_: dict = Depends(verify_token)):
    """Get disk failure predictions based on SMART trends."""
    history = load_smart_history()
    predictions = []

    for serial, entries in history.items():
        if len(entries) < 24:  # Need at least a day of data
            continue

        latest = entries[-1]
        device = latest.get("device", "unknown")

        # Calculate trend scores for critical attributes
        risk_score = 0
        risk_factors = []

        for attr_id, attr_name in CRITICAL_ATTRIBUTES.items():
            # Get values over time
            values = [_attribute_value(e, attr_id) for e in entries]
            if not values:
                continue

            current = values[-1]
            first = values[0]

            # Check if attribute is increasing
            if current > first:
                increase_rate = (current - first) / len(values)  # per hour

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
        if risk_score >= 50:
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

        predictions.append({
            "serial": serial,
            "device": device,
            "risk_score": min(risk_score, 100),
            "status": status,
            "risk_factors": risk_factors,
            "recommendation": recommendation,
            "data_points": len(entries)
        })

    # Sort by risk score descending
    predictions.sort(key=lambda x: x["risk_score"], reverse=True)

    return {"predictions": predictions}


@router.get("/smart-monitor/status")
async def get_smart_monitor_status(_: dict = Depends(verify_token)):
    """Get SMART trend monitor status."""
    history = load_smart_history()
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
