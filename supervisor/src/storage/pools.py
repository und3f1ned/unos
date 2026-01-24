import subprocess
import os
import re
from ..config import get_settings
from .schemas import PoolCreate, PoolInfo, VolumeCreate, VolumeInfo, ScrubStatus

settings = get_settings()


def create_pool(pool: PoolCreate) -> PoolInfo:
    """Create a new btrfs pool."""
    profile_map = {
        "single": ("single", "single"),
        "raid0": ("raid0", "raid0"),
        "raid1": ("raid1", "raid1"),
        "raid5": ("raid5", "raid5"),
        "raid6": ("raid6", "raid6"),
        "raid10": ("raid10", "raid10"),
    }

    if pool.raid_level not in profile_map:
        raise ValueError(f"Unsupported RAID level: {pool.raid_level}")

    data_profile, meta_profile = profile_map[pool.raid_level]

    cmd = [
        "mkfs.btrfs",
        "-L", pool.name,
        "-d", data_profile,
        "-m", meta_profile,
        "-f",
    ] + pool.devices

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"mkfs.btrfs failed: {result.stderr}")

    mountpoint = os.path.join(settings.pools_base_path, pool.name)
    os.makedirs(mountpoint, exist_ok=True)

    result = subprocess.run(
        ["mount", "-t", "btrfs", f"LABEL={pool.name}", mountpoint],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Mount failed: {result.stderr}")

    fstab_entry = f"LABEL={pool.name} {mountpoint} btrfs defaults 0 0\n"
    with open("/etc/fstab", "a") as f:
        f.write(fstab_entry)

    return get_pool(pool.name)


def list_pools() -> list[PoolInfo]:
    """List all btrfs pools."""
    result = subprocess.run(
        ["btrfs", "filesystem", "show", "--raw"],
        capture_output=True,
        text=True,
    )

    pools = []
    current_pool = None
    devices = []

    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith("Label:"):
            if current_pool:
                pools.append(current_pool)
            parts = line.split()
            label = parts[1].strip("'")
            uuid = parts[3] if len(parts) > 3 else None
            current_pool = {"name": label, "uuid": uuid, "devices": []}
            devices = []
        elif line.startswith("devid"):
            parts = line.split()
            for i, p in enumerate(parts):
                if p == "path":
                    device = parts[i + 1]
                    devices.append(device)
                    if current_pool:
                        current_pool["devices"] = devices

    if current_pool:
        pools.append(current_pool)

    enriched = []
    for p in pools:
        try:
            info = get_pool(p["name"])
            enriched.append(info)
        except Exception:
            enriched.append(
                PoolInfo(
                    name=p["name"],
                    uuid=p.get("uuid"),
                    devices=p.get("devices", []),
                    total_size=0,
                    used_size=0,
                )
            )

    return enriched


def get_pool(name: str) -> PoolInfo:
    """Get detailed info for a specific pool."""
    mountpoint = os.path.join(settings.pools_base_path, name)

    # Check if mounted by checking if path is a mountpoint
    mount_result = subprocess.run(
        ["findmnt", mountpoint, "-n", "-o", "TARGET"],
        capture_output=True,
        text=True,
    )
    is_mounted = mount_result.returncode == 0 and mount_result.stdout.strip()
    actual_mountpoint = mount_result.stdout.strip() if is_mounted else None

    total_size = 0
    used_size = 0
    raid_level = "single"

    if is_mounted:
        result = subprocess.run(
            ["btrfs", "filesystem", "usage", "-b", mountpoint],
            capture_output=True,
            text=True,
        )

        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("Device size:"):
                try:
                    total_size = int(line.split()[-1])
                except (ValueError, IndexError):
                    pass
            elif line.startswith("Used:"):
                try:
                    used_size = int(line.split()[-1])
                except (ValueError, IndexError):
                    pass
            elif line.startswith("Data,"):
                raid_part = line.split(":")[0].replace("Data,", "").strip()
                raid_level = raid_part.lower()

    # Get devices and UUID
    result = subprocess.run(
        ["btrfs", "filesystem", "show", name],
        capture_output=True,
        text=True,
    )

    devices = []
    uuid = None
    for line in result.stdout.splitlines():
        if "uuid:" in line:
            uuid = line.split("uuid:")[-1].strip()
        if "path" in line:
            parts = line.split()
            for i, p in enumerate(parts):
                if p == "path":
                    devices.append(parts[i + 1])

    return PoolInfo(
        name=name,
        uuid=uuid,
        devices=devices,
        total_size=total_size,
        used_size=used_size,
        mountpoint=actual_mountpoint,
        raid_level=raid_level,
    )


def delete_pool(name: str) -> None:
    """Delete a btrfs pool."""
    mountpoint = os.path.join(settings.pools_base_path, name)

    subprocess.run(["umount", mountpoint], capture_output=True, text=True)

    if os.path.exists(mountpoint):
        os.rmdir(mountpoint)

    with open("/etc/fstab", "r") as f:
        lines = f.readlines()
    with open("/etc/fstab", "w") as f:
        for line in lines:
            if f"LABEL={name}" not in line:
                f.write(line)


def create_volume(volume: VolumeCreate) -> VolumeInfo:
    """Create a btrfs subvolume."""
    pool_path = os.path.join(settings.pools_base_path, volume.pool_name)
    volume_path = os.path.join(pool_path, volume.name)

    result = subprocess.run(
        ["btrfs", "subvolume", "create", volume_path],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Subvolume creation failed: {result.stderr}")

    if volume.quota:
        subprocess.run(
            ["btrfs", "qgroup", "limit", str(volume.quota), volume_path],
            capture_output=True,
            text=True,
        )

    return VolumeInfo(
        name=volume.name,
        pool=volume.pool_name,
        path=volume_path,
        quota=volume.quota,
    )


def list_volumes(pool_name: str) -> list[VolumeInfo]:
    """List subvolumes in a pool."""
    pool_path = os.path.join(settings.pools_base_path, pool_name)

    result = subprocess.run(
        ["btrfs", "subvolume", "list", pool_path],
        capture_output=True,
        text=True,
    )

    volumes = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 9:
            name = parts[-1]
            volumes.append(
                VolumeInfo(
                    name=name,
                    pool=pool_name,
                    path=os.path.join(pool_path, name),
                )
            )

    return volumes


def delete_volume(pool_name: str, volume_name: str) -> None:
    """Delete a btrfs subvolume."""
    volume_path = os.path.join(settings.pools_base_path, pool_name, volume_name)

    result = subprocess.run(
        ["btrfs", "subvolume", "delete", volume_path],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Subvolume deletion failed: {result.stderr}")


def start_scrub(pool_name: str) -> ScrubStatus:
    """Start a btrfs scrub on a pool."""
    mountpoint = os.path.join(settings.pools_base_path, pool_name)

    result = subprocess.run(
        ["btrfs", "scrub", "start", mountpoint],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Scrub start failed: {result.stderr}")

    return get_scrub_status(pool_name)


def get_scrub_status(pool_name: str) -> ScrubStatus:
    """Get the scrub status for a pool."""
    mountpoint = os.path.join(settings.pools_base_path, pool_name)

    result = subprocess.run(
        ["btrfs", "scrub", "status", mountpoint],
        capture_output=True,
        text=True,
    )

    status = "idle"
    duration = None
    bytes_scrubbed = None
    total_bytes = None
    rate = None
    errors = 0
    last_run = None

    output = result.stdout

    # Parse status
    if "Status:" in output:
        status_match = re.search(r"Status:\s*(\w+)", output)
        if status_match:
            status = status_match.group(1).lower()

    # Check if no scrub has been run
    if "no stats available" in output.lower() or result.returncode != 0:
        return ScrubStatus(
            pool=pool_name,
            status="idle",
        )

    # Parse duration (format: 0:05:30 or similar)
    duration_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+)", output)
    if duration_match:
        hours, minutes, seconds = map(int, duration_match.groups())
        duration = hours * 3600 + minutes * 60 + seconds

    # Parse scrub started time
    started_match = re.search(r"Scrub started:\s*(.+?)(?:\n|$)", output)
    if started_match:
        last_run = started_match.group(1).strip()

    # Parse bytes scrubbed (various formats)
    scrubbed_match = re.search(r"Bytes scrubbed:\s*([\d.]+)\s*(\w+)", output)
    if scrubbed_match:
        value = float(scrubbed_match.group(1))
        unit = scrubbed_match.group(2).lower()
        bytes_scrubbed = _parse_size_to_bytes(value, unit)

    # Parse total to scrub
    total_match = re.search(r"Total to scrub:\s*([\d.]+)\s*(\w+)", output)
    if total_match:
        value = float(total_match.group(1))
        unit = total_match.group(2).lower()
        total_bytes = _parse_size_to_bytes(value, unit)

    # Parse rate
    rate_match = re.search(r"Rate:\s*([\d.]+)\s*(\w+)/s", output)
    if rate_match:
        value = float(rate_match.group(1))
        unit = rate_match.group(2).lower()
        rate = _parse_size_to_bytes(value, unit)

    # Parse errors
    error_match = re.search(r"Error summary:\s*(.+?)(?:\n|$)", output)
    if error_match:
        error_text = error_match.group(1).strip()
        if "no errors" not in error_text.lower():
            # Try to extract number of errors
            num_match = re.search(r"(\d+)", error_text)
            if num_match:
                errors = int(num_match.group(1))
            else:
                errors = 1  # Some error occurred

    return ScrubStatus(
        pool=pool_name,
        status=status,
        duration=duration,
        bytes_scrubbed=bytes_scrubbed,
        total_bytes=total_bytes,
        rate=rate,
        errors=errors,
        last_run=last_run,
    )


def cancel_scrub(pool_name: str) -> ScrubStatus:
    """Cancel a running btrfs scrub on a pool."""
    mountpoint = os.path.join(settings.pools_base_path, pool_name)

    result = subprocess.run(
        ["btrfs", "scrub", "cancel", mountpoint],
        capture_output=True,
        text=True,
    )
    # Note: cancel returns non-zero if no scrub is running, which is fine
    if result.returncode != 0 and "not running" not in result.stderr.lower():
        raise RuntimeError(f"Scrub cancel failed: {result.stderr}")

    return get_scrub_status(pool_name)


def _parse_size_to_bytes(value: float, unit: str) -> int:
    """Convert size with unit to bytes."""
    unit = unit.lower().rstrip("ib").rstrip("b")
    multipliers = {
        "": 1,
        "k": 1024,
        "m": 1024**2,
        "g": 1024**3,
        "t": 1024**4,
        "p": 1024**5,
    }
    return int(value * multipliers.get(unit, 1))
