import subprocess
import os
from ..config import get_settings
from .schemas import PoolCreate, PoolInfo, VolumeCreate, VolumeInfo

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
