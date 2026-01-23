import subprocess
import json
import asyncio
from datetime import datetime
from pathlib import Path
from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from pydantic import BaseModel
from typing import Optional

from ..auth.utils import verify_token

router = APIRouter(prefix="/health", tags=["health"])

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


@router.get("/disks")
async def get_disk_health(_: dict = Depends(verify_token)):
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
async def get_disk_smart(device: str, _: dict = Depends(verify_token)):
    """Get detailed SMART data for a specific disk."""
    if not device.startswith("/dev/"):
        device = f"/dev/{device}"
    
    # Security check
    if ".." in device or not Path(device).exists():
        raise HTTPException(400, "Invalid device path")

    return parse_smart_data(device)


@router.post("/disk/{device:path}/test")
async def start_smart_test(
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
async def get_health_overview(_: dict = Depends(verify_token)):
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
