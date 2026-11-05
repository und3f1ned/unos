from typing import Literal

from pydantic import BaseModel, Field


class PartitionInfo(BaseModel):
    name: str
    path: str
    size: int
    fstype: str | None = None
    label: str | None = None
    # Host mountpoints of the partition and of anything built on it (LVM, LUKS, md).
    mountpoints: list[str] = Field(default_factory=list)


class DiskInfo(BaseModel):
    name: str
    path: str
    model: str | None = None
    serial: str | None = None
    wwn: str | None = None  # World Wide Name, if the disk reports one
    size: int
    rotational: bool
    mountpoint: str | None = None
    type: str
    # Everything below is gathered from the HOST view (lsblk/findmnt/swapon/wipefs
    # run in the host namespaces), not from inside the supervisor container.
    fstype: str | None = None  # signature on the whole disk, e.g. "btrfs"
    label: str | None = None
    pttype: str | None = None  # partition table type ("gpt"/"dos")
    partitions: list[PartitionInfo] = Field(default_factory=list)
    mountpoints: list[str] = Field(default_factory=list)
    has_data: bool = False  # a filesystem, partition table, partition or other signature
    is_system: bool = False  # backs /, /boot, /boot/efi, active swap, or the boot device
    in_use: bool = False  # is_system, mounted, member of a mounted btrfs pool, or held by md/LVM/LUKS
    in_use_reason: str | None = None
    pool: str | None = None  # btrfs label when the disk (or a partition) belongs to a btrfs filesystem


class SmartData(BaseModel):
    device: str
    healthy: bool
    temperature: int | None = None
    power_on_hours: int | None = None
    reallocated_sectors: int | None = None
    raw_data: dict | None = None


SAFE_RAID_LEVELS = ("single", "raid0", "raid1", "raid1c3", "raid1c4", "raid10")


class DeviceIdentity(BaseModel):
    """A selected disk as GET /storage/disks listed it (``path``, ``serial``, ``wwn``, ``size``)."""

    path: str
    serial: str | None = None
    wwn: str | None = None
    size: int


class PoolCreate(BaseModel):
    name: str
    devices: list[str]
    raid_level: Literal["single", "raid0", "raid1", "raid1c3", "raid1c4", "raid10"] = "single"
    # Typed confirmation of the destructive operation; must equal ``name`` (HTTP 400 otherwise).
    confirm_name: str
    # Required when any selected device already contains data (HTTP 409 otherwise).
    force: bool = False
    # What the client saw for the selected devices. Device names are not
    # stable (USB disks are re-enumerated after a reset or replug), so with
    # identities the pool is only created if every device path still holds
    # the disk with that serial, WWN and size (HTTP 409 otherwise). With
    # ``force``, every selected device needs one (HTTP 400 otherwise).
    device_identities: list[DeviceIdentity] = Field(default_factory=list)


PoolHealth = Literal["healthy", "degraded", "unmounted", "error", "unknown"]


class PoolInfo(BaseModel):
    name: str
    uuid: str | None = None
    devices: list[str]
    total_size: int
    used_size: int
    mountpoint: str | None = None
    raid_level: str | None = None
    mounted: bool = False  # mounted on the HOST at <pools_base_path>/<name>
    persistent: bool = False  # a UNOS-managed /etc/fstab entry for this filesystem exists on the HOST
    health: PoolHealth = "unknown"
    missing_devices: int = 0
    device_errors: int = 0  # sum of btrfs device stats error counters (0 if unmounted/unknown)
    # Mounted, but writes fail: mounted read-only on purpose, or forced read-only.
    read_only: bool = False
    # Mounted read-write, but btrfs made the filesystem read-only after a serious
    # error such as an aborted transaction (health "error"; read_only is set too).
    forced_read_only: bool = False


class VolumeCreate(BaseModel):
    pool_name: str
    name: str
    quota: int | None = None  # bytes, None = no quota


class VolumeInfo(BaseModel):
    name: str
    pool: str
    path: str
    used: int | None = None
    quota: int | None = None


class ScrubStatus(BaseModel):
    pool: str
    status: str  # "running", "finished", "aborted", "idle"
    duration: float | None = None  # seconds
    bytes_scrubbed: int | None = None
    total_bytes: int | None = None
    rate: int | None = None  # bytes per second
    errors: int = 0
    last_run: str | None = None  # datetime string of last scrub
