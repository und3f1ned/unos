import json
import subprocess
from .schemas import DiskInfo, SmartData

# System disk patterns to exclude
SYSTEM_DISK_PATTERNS = [
    "mmcblk",      # eMMC storage (system disk)
    "zram",        # Virtual RAM disks
    "loop",        # Loop devices
    "sr",          # CD/DVD drives
    "fd",          # Floppy drives
    "nvme0n1",     # Often system NVMe (can be adjusted)
]


def _is_system_disk(name: str, mountpoint: str | None, children: list | None) -> bool:
    """Check if disk is a system disk that should be hidden."""
    # Check name patterns
    for pattern in SYSTEM_DISK_PATTERNS:
        if name.startswith(pattern):
            return True
    
    # Check if disk or any partition is mounted as root
    if mountpoint == "/":
        return True
    
    # Check children for root mount
    if children:
        for child in children:
            if child.get("mountpoint") == "/":
                return True
    
    return False


def list_disks() -> list[DiskInfo]:
    """List all block devices using lsblk, excluding system disks."""
    result = subprocess.run(
        ["lsblk", "-J", "-b", "-o", "NAME,PATH,MODEL,SERIAL,SIZE,ROTA,MOUNTPOINT,TYPE"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"lsblk failed: {result.stderr}")

    data = json.loads(result.stdout)
    disks = []

    for device in data.get("blockdevices", []):
        if device.get("type") != "disk":
            continue
        
        name = device["name"]
        mountpoint = device.get("mountpoint")
        children = device.get("children", [])
        
        # Skip system disks
        if _is_system_disk(name, mountpoint, children):
            continue
        
        disks.append(
            DiskInfo(
                name=name,
                path=device.get("path", f"/dev/{name}"),
                model=device.get("model"),
                serial=device.get("serial"),
                size=int(device.get("size", 0)),
                rotational=device.get("rota", True),
                mountpoint=mountpoint,
                type=device.get("type", "disk"),
            )
        )

    return disks


def get_smart_data(device_path: str) -> SmartData:
    """Get SMART data for a device using smartctl."""
    result = subprocess.run(
        ["smartctl", "-j", "-a", device_path],
        capture_output=True,
        text=True,
    )

    # smartctl returns non-zero for various reasons, try to parse anyway
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return SmartData(device=device_path, healthy=False, raw_data={"error": result.stderr})

    smart_status = data.get("smart_status", {})
    healthy = smart_status.get("passed", False)

    temperature = None
    if "temperature" in data:
        temperature = data["temperature"].get("current")

    power_on_hours = None
    reallocated_sectors = None

    for attr in data.get("ata_smart_attributes", {}).get("table", []):
        if attr.get("id") == 9:  # Power-On Hours
            power_on_hours = attr.get("raw", {}).get("value")
        elif attr.get("id") == 5:  # Reallocated Sector Count
            reallocated_sectors = attr.get("raw", {}).get("value")

    return SmartData(
        device=device_path,
        healthy=healthy,
        temperature=temperature,
        power_on_hours=power_on_hours,
        reallocated_sectors=reallocated_sectors,
        raw_data=data,
    )
