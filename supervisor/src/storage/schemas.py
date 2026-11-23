from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.config import JsonDict

from ..times import UtcDatetime

# A whole disk's power as disks.power_state reads it without waking the disk (ADR-0378): hdparm -C
# on the host (smartctl -n idle for a power condition it does not name, as an EPC disk's), or
# smartctl -n standby without hdparm and for an NVMe disk. Active (spinning, or idle), standby (spun
# down, or asleep), or unknown (the disk cannot be asked, as behind some USB bridges, names no power
# mode, did not answer, or its earlier read is still stuck).
PowerState = Literal["active", "standby", "unknown"]

# The idle timeouts that the Storage page offers, in minutes, 0 for off (ADR-0378): values that
# hdparm -S expresses (5-second steps up to 20 minutes, 30-minute steps from 30 minutes to 5.5
# hours), from the shortest that does not spin a disk down between the bursts of a quiet NAS to the
# longest whole hour.
SPINDOWN_MINUTES = (0, 5, 10, 20, 30, 60, 120, 180, 240, 300)

# The API documentation's examples (ADR-0417): made-up disks, serials and UUIDs.
_DISK_SIZE = 4000787030016
_SDA_IDENTITY: JsonDict = {"path": "/dev/sda", "serial": "EXAMPLE-SERIAL-0001", "wwn": "0x5000000000000001", "size": _DISK_SIZE}
_SDB_IDENTITY: JsonDict = {"path": "/dev/sdb", "serial": "EXAMPLE-SERIAL-0002", "wwn": "0x5000000000000002", "size": _DISK_SIZE}
_IDLE_REMOVAL: JsonDict = {
    "pool": "data", "status": "idle", "devid": None, "device": None, "left": None, "total": None,
    "result": None, "error": None,
}


class PartitionInfo(BaseModel):
    """A partition of a disk."""

    name: str = Field(description="The partition's name, such as sda1 or nvme0n1p1.")
    path: str = Field(description="Its device path, such as /dev/sda1.")
    size: int = Field(description="Its size in bytes.")
    fstype: str | None = Field(
        default=None, description="The filesystem or signature on it, such as ext4; null for none."
    )
    label: str | None = Field(default=None, description="Its filesystem's label; null for none.")
    # Host mountpoints of the partition and of anything built on it (LVM, LUKS, md).
    mountpoints: list[str] = Field(
        default_factory=list,
        description="Where the host mounts it or anything built on it (LVM, LUKS, md), swap left out.",
    )


class DiskInfo(BaseModel):
    """A whole disk of the host and what it holds, read without waking it."""

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "name": "sda", "path": "/dev/sda", "model": "WDC WD40EFRX-68N32N0", "serial": "EXAMPLE-SERIAL-0001",
        "wwn": "0x5000000000000001", "size": _DISK_SIZE, "rotational": True, "mountpoint": "/mnt/pools/data",
        "type": "disk", "fstype": "btrfs", "label": "data", "pttype": None, "partitions": [],
        "mountpoints": ["/mnt/pools/data"], "has_data": True, "is_system": False, "in_use": True,
        "in_use_reason": "Mounted at /mnt/pools/data", "pool": "data", "power_state": "active",
        "mmc_type": None, "spindown": 20, "spindown_problem": None,
    }]})

    name: str = Field(description="The disk's name, such as sda or nvme0n1.")
    path: str = Field(description="Its device path, such as /dev/sda.")
    model: str | None = Field(default=None, description="Its model, as the disk reports it; null when it reports none.")
    serial: str | None = Field(default=None, description="Its serial number; null when it reports none.")
    # World Wide Name, if the disk reports one
    wwn: str | None = Field(
        default=None, description="Its World Wide Name; null when it reports none."
    )
    size: int = Field(description="Its size in bytes.")
    rotational: bool = Field(description="Whether the kernel reports it rotational: a spinning hard disk.")
    mountpoint: str | None = Field(
        default=None, description="The first of mountpoints; null when nothing on it is mounted."
    )
    type: str = Field(description="lsblk's device type: always disk here.")
    # Everything below is gathered from the HOST view (lsblk/findmnt/swapon/wipefs
    # run in the host namespaces), not from inside the supervisor container.
    fstype: str | None = Field(
        default=None, description="The filesystem or signature on the whole disk, such as btrfs; null for none.",
    )  # signature on the whole disk, e.g. "btrfs"
    label: str | None = Field(default=None, description="The label of the filesystem on the whole disk; null for none.")
    pttype: str | None = Field(
        default=None, description="Its partition table, gpt or dos; null without one.",
    )  # partition table type ("gpt"/"dos")
    partitions: list[PartitionInfo] = Field(default_factory=list, description="Its partitions.")
    mountpoints: list[str] = Field(
        default_factory=list,
        description="Where the host mounts the disk or anything on it (partitions, LVM, LUKS, md), swap left out.",
    )
    has_data: bool = Field(
        default=False,
        description="Whether it holds a filesystem, a partition table, partitions or another signature; true for "
        "a disk in standby whose contents are unknown.",
    )  # a filesystem, partition table, partition or other signature
    is_system: bool = Field(
        default=False,
        description="Whether it holds the host's system: /, /boot, /boot/efi, active swap or the boot device.",
    )  # backs /, /boot, /boot/efi, active swap, or the boot device
    in_use: bool = Field(
        default=False,
        description="Whether it is in use: the system disk, mounted, a member of a mounted pool, or held by md, "
        "LVM or LUKS.",
    )  # is_system, mounted, member of a mounted btrfs pool, or held by md/LVM/LUKS
    in_use_reason: str | None = Field(default=None, description="Why it is in use, in English; null when it is not.")
    pool: str | None = Field(
        default=None, description="The label of the btrfs filesystem on it or on a partition of it; null for none.",
    )  # btrfs label when the disk (or a partition) belongs to a btrfs filesystem
    # Read before anything else (disks.power_state), which leaves a sleeping disk alone; a disk in
    # standby is not probed with wipefs, so has_data is true for it unless lsblk knows more.
    power_state: PowerState = Field(
        default="unknown",
        description="Its power state, read without waking it: active (spinning or idle), standby (asleep), or "
        "unknown when the disk cannot tell. An SD card or eMMC is always active.",
    )
    # storage.free-mmc-as-data-disk: an SD card or eMMC that holds no part of the running system is a data disk.
    mmc_type: str | None = Field(
        default=None,
        description="For an SD card or eMMC, the kernel's type of it: SD, MMC (an eMMC), or unknown when the host's "
        "sysfs cannot tell; such a disk has no SMART and no idle timeout, and wears with writes. Null for every "
        "other disk.",
    )
    # The idle timeout in minutes that UNOS's udev rule sets for the disk (by its serial), 0 for none.
    spindown: int = Field(
        default=0, description="Its idle timeout in minutes, as UNOS's udev rule sets it; 0 for none."
    )
    # Why the disk gets no idle timeout (the system disk, an NVMe disk, no serial a udev rule can
    # match, no hdparm on the host); None when it can have one.
    spindown_problem: str | None = Field(
        default=None,
        description="Why it can have no idle timeout (the system disk, an NVMe disk, no serial that a udev rule "
        "can match, no hdparm on the host, a rule that cannot be read); null when it can have one.",
    )


class SpindownSet(BaseModel):
    """Body of PUT /storage/disks/{name}/spindown: the idle timeout in minutes, 0 for off."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"minutes": 20}]})

    minutes: int = Field(
        description="The idle timeout in minutes: one of 0 (off), 5, 10, 20, 30, 60, 120, 180, 240 and 300.",
    )

    @field_validator("minutes", mode="before")
    @classmethod
    def _offered(cls, value: object) -> object:
        if type(value) is not int or value not in SPINDOWN_MINUTES:
            raise ValueError(f"minutes must be one of {', '.join(map(str, SPINDOWN_MINUTES))}")
        return value


class DiskSpindown(BaseModel):
    """The idle timeout that a disk has now."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"name": "sda", "minutes": 20, "warning": None}]})

    name: str = Field(description="The disk's name, such as sda.")
    minutes: int = Field(description="Its idle timeout in minutes; 0 for none.")
    # Set when the disk's own automatic offline scans could not be turned off for the timeout, or on
    # again once it was removed (ADR-0378): the timeout is set all the same.
    warning: str | None = Field(
        default=None,
        description="Set when the disk's own automatic offline scans could not be turned off for the timeout, or "
        "on again without one: the timeout is set all the same. Null otherwise.",
    )


class SmartData(BaseModel):
    """A disk's SMART data, read without waking a disk in standby."""

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "device": "/dev/sda", "healthy": True, "power_state": "active", "temperature": 34,
        "power_on_hours": 12850, "reallocated_sectors": 0,
        "raw_data": {"smart_status": {"passed": True}, "temperature": {"current": 34}},
    }]})

    device: str = Field(description="The disk's device path, such as /dev/sda.")
    # None when smartctl left the disk alone because it sleeps (power_state "standby", ADR-0378).
    healthy: bool | None = Field(
        description="Whether the disk's own SMART health check passed; false also when smartctl gave no answer; "
        "null for a disk in standby, which was not read, and for an SD card or eMMC, which has no SMART.",
    )
    power_state: PowerState = Field(
        default="unknown",
        description="active when smartctl read the disk, and for an SD card or eMMC, which it does not read; standby "
        "when it left a sleeping disk alone; unknown otherwise.",
    )
    temperature: int | None = Field(default=None, description="Its temperature in degrees Celsius; null when not read.")
    power_on_hours: int | None = Field(
        default=None, description="Its power-on hours (attribute 9); null when not read."
    )
    reallocated_sectors: int | None = Field(
        default=None, description="Its reallocated sectors (attribute 5); null when not read.",
    )
    raw_data: dict | None = Field(
        default=None,
        description="smartctl's whole JSON output, or {\"error\": ...} when smartctl gave none; null for a disk "
        "in standby.",
    )


SAFE_RAID_LEVELS = ("single", "raid0", "raid1", "raid1c3", "raid1c4", "raid10")


class DeviceIdentity(BaseModel):
    """A selected disk as GET /storage/disks listed it (``path``, ``serial``, ``wwn``, ``size``)."""

    path: str = Field(description="The disk's device path, such as /dev/sda.")
    serial: str | None = Field(default=None, description="Its serial number as listed; null when it had none.")
    wwn: str | None = Field(default=None, description="Its World Wide Name as listed; null when it had none.")
    size: int = Field(description="Its size in bytes as listed.")


class PoolCreate(BaseModel):
    """Body of POST /storage/pools: the new pool and the disks that it erases."""

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "name": "data", "devices": ["/dev/sda", "/dev/sdb"], "raid_level": "raid1", "confirm_name": "data",
        "force": False, "device_identities": [_SDA_IDENTITY, _SDB_IDENTITY],
    }]})

    name: str = Field(
        description="The pool's name: up to 63 letters, digits, - and _, starting with a letter or digit. It "
        "becomes the filesystem's label and the folder /mnt/pools/<name>.",
    )
    devices: list[str] = Field(description="The disks or partitions to format, by device path, each once.")
    raid_level: Literal["single", "raid0", "raid1", "raid1c3", "raid1c4", "raid10"] = Field(
        default="single",
        description="The profile of data and metadata: single, raid0 (at least 2 disks), raid1 (2), raid1c3 (3), "
        "raid1c4 (4) or raid10 (4).",
    )
    # Typed confirmation of the destructive operation; must equal ``name`` (HTTP 400 otherwise).
    confirm_name: str = Field(description="The pool's name typed again, the confirmation: it must equal name.")
    # Required when any selected device already contains data (HTTP 409 otherwise).
    force: bool = Field(
        default=False, description="Erase devices that hold data; without it, such a device is refused."
    )
    # What the client saw for the selected devices. Device names are not
    # stable (USB disks are re-enumerated after a reset or replug), so with
    # identities the pool is only created if every device path still holds
    # the disk with that serial, WWN and size (HTTP 409 otherwise). With
    # ``force``, every selected device needs one (HTTP 400 otherwise).
    device_identities: list[DeviceIdentity] = Field(
        default_factory=list,
        description="The selected devices as the disk list showed them: a path that now holds another disk is "
        "refused. With force, every device needs one.",
    )


PoolHealth = Literal["healthy", "degraded", "unmounted", "error", "unknown"]


class PoolInfo(BaseModel):
    """A pool: a btrfs filesystem whose label is its name."""

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "name": "data", "uuid": "0f8e6c52-3b1d-4a7e-9c2f-5d6e7f8a9b0c", "devices": ["/dev/sda", "/dev/sdb"],
        "total_size": 2 * _DISK_SIZE, "used_size": 1300280639488, "mountpoint": "/mnt/pools/data",
        "raid_level": "raid1", "mounted": True, "persistent": True, "health": "healthy", "missing_devices": 0,
        "device_errors": 0, "read_only": False, "forced_read_only": False,
    }]})

    name: str = Field(description="The pool's name, its filesystem's label.")
    uuid: str | None = Field(default=None, description="The UUID of its btrfs filesystem.")
    devices: list[str] = Field(description="The device paths of its disks that are present.")
    total_size: int = Field(
        description="The bytes of its disks: as btrfs filesystem usage reports them while it is mounted, else "
        "the sizes of the disks that are present.",
    )
    used_size: int = Field(
        description="The bytes used: on its disks, every copy counted, as btrfs filesystem usage reports them "
        "while it is mounted; else the bytes of its data, as btrfs filesystem show reports them.",
    )
    mountpoint: str | None = Field(
        default=None, description="/mnt/pools/<name> while it is mounted there; null otherwise."
    )
    raid_level: str | None = Field(
        default=None,
        description="The profile of its data, lower case, such as single or raid1; null when not mounted or not read.",
    )
    mounted: bool = Field(
        default=False, description="Whether it is mounted on the host at /mnt/pools/<name>.",
    )  # mounted on the HOST at <pools_base_path>/<name>
    persistent: bool = Field(
        default=False, description="Whether it is a managed pool: UNOS's entry in the host's fstab mounts it at boot.",
    )  # a UNOS-managed /etc/fstab entry for this filesystem exists on the HOST
    health: PoolHealth = Field(
        default="unknown",
        description="healthy; degraded while a disk is missing; error after device errors or when btrfs made it "
        "read-only; unmounted; unknown when the mount table or the error counters cannot be read.",
    )
    missing_devices: int = Field(default=0, description="How many of its disks are missing.")
    device_errors: int = Field(
        default=0, description="The sum of btrfs's device error counters; 0 when not mounted or not read.",
    )  # sum of btrfs device stats error counters (0 if unmounted/unknown)
    # Mounted, but writes fail: mounted read-only on purpose, or forced read-only.
    read_only: bool = Field(
        default=False, description="Mounted, but read-only: on purpose, or forced by btrfs after an error.",
    )
    # Mounted read-write, but btrfs made the filesystem read-only after a serious
    # error such as an aborted transaction (health "error"; read_only is set too).
    forced_read_only: bool = Field(
        default=False,
        description="Mounted read-write, but btrfs made it read-only after a serious error (health is error, "
        "read_only true).",
    )


# Why a btrfs filesystem is not the pool its label names (ADR-0283): it has none, it is
# not a valid pool name, or another btrfs filesystem of the host has it too, and the pool
# of that name is the other one (mounted or managed under it) or neither.
LabelProblem = Literal["missing", "invalid", "duplicate"]


class ImportableFilesystem(BaseModel):
    """A btrfs filesystem on the host's disks that can be imported as a pool."""
    # ADR-0283.

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "uuid": "6a1b2c3d-4e5f-4a7b-8c9d-0e1f2a3b4c5d", "label": None, "label_problem": "missing",
        "devices": ["/dev/sdc"], "total_devices": 1, "missing_devices": 0, "size": 2000398934016,
        "used": 512110190592,
    }]})

    uuid: str = Field(description="The filesystem's UUID, which the import names.")
    label: str | None = Field(
        default=None, description="Its label as btrfs prints it; null without one.",
    )  # as btrfs prints it; None without a label
    label_problem: LabelProblem = Field(
        description="Why it is not the pool its label names: missing (no label), invalid (no valid pool name), "
        "or duplicate (another btrfs filesystem has the label too, and the pool is the other or neither).",
    )
    devices: list[str] = Field(description="The device paths of its disks that are present.")  # the members that are present
    total_devices: int = Field(description="How many disks it has, the missing ones included.")
    missing_devices: int = Field(default=0, description="How many of its disks are missing.")
    size: int = Field(description="The bytes of its disks that are present.")  # bytes, of the members that are present
    used: int = Field(description="The bytes used, as btrfs filesystem show reports them.")  # bytes used, as btrfs filesystem show reports them


class PoolImport(BaseModel):
    """Body of POST /storage/filesystems/{uuid}/import: the pool's name, which becomes its label."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"name": "backup"}]})

    name: str = Field(
        description="The pool's name, which becomes the filesystem's label: up to 63 letters, digits, - and _, "
        "starting with a letter or digit.",
    )


# btrfs stores a quota limit as an unsigned 64-bit number and reads (u64)-1
# as "no limit", so the largest limit is one less.
MAX_VOLUME_QUOTA = (1 << 64) - 2


class VolumeCreate(BaseModel):
    """Body of POST /storage/pools/{pool_name}/volumes: the pool is the one in the URL."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"name": "documents", "quota": 107374182400}]})

    name: str = Field(
        description="The volume's name: up to 63 letters, digits, - and _, starting with a letter or digit.",
    )
    # Bytes; None = no limit. Needs quotas enabled on the pool (btrfs quota enable).
    quota: int | None = Field(
        default=None, gt=0, le=MAX_VOLUME_QUOTA,
        description="Its quota in bytes; null for none. Needs quotas enabled on the pool.",
    )


# A volume's quota use as the kernel counts it (storage.qgroups): counted; inconsistent
# (counted, but btrfs says a rescan is needed); quotas_off (quotas are off on the pool: nothing is
# counted); unknown (its quota group cannot be read).
VolumeUsageState = Literal["counted", "inconsistent", "quotas_off", "unknown"]

_USAGE_STATES = (
    "counted; inconsistent (counted, but btrfs says that the counts need a rescan); quotas_off (quotas are off "
    "on the pool, and nothing is counted); unknown (its quota group cannot be read)"
)


class VolumeInfo(BaseModel):
    """A volume of a pool and its quota use."""

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "name": "documents", "pool": "data", "path": "/mnt/pools/data/documents", "used": 21474836480,
        "quota": 107374182400, "usage": "counted",
    }]})

    name: str = Field(description="The volume's name.")
    pool: str = Field(description="The pool's name.")
    path: str = Field(description="Its folder, /mnt/pools/<pool>/<name>.")
    # The bytes it references, which its quota limits; None unless counted.
    used: int | None = Field(
        default=None,
        description="Its quota use: the bytes it references, data shared with its snapshots included; null when "
        "quotas are off or its quota group cannot be read, and in the answer to a create.",
    )
    # The limit of its quota group on them, in bytes; None without one.
    quota: int | None = Field(default=None, description="Its quota in bytes; null without one.")
    # How the list read them; None in the answer to a create, which reads nothing.
    usage: VolumeUsageState | None = Field(
        default=None, description=f"How used was read: {_USAGE_STATES}. Null in the answer to a create.",
    )


class VolumeUsagePoint(BaseModel):
    """A volume's use at one time."""

    time: str = Field(description="When, in UTC with its offset.")  # ISO 8601 with the UTC offset
    used: int = Field(description="Its quota use then, in bytes.")
    quota: int | None = Field(default=None, description="Its quota then, in bytes; null without one.")


class VolumeUsageHistory(BaseModel):
    """GET /storage/pools/{pool}/volumes/{name}/usage: one point per hour of the last 30 days that
    the supervisor kept, oldest first, and the current reading last (storage.volume_usage)."""

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "name": "documents", "pool": "data", "used": 21474836480, "quota": 107374182400, "usage": "counted",
        "points": [
            {"time": "2026-11-19T08:00:00+00:00", "used": 21390950400, "quota": 107374182400},
            {"time": "2026-11-19T09:12:45+00:00", "used": 21474836480, "quota": 107374182400},
        ],
    }]})

    name: str = Field(description="The volume's name.")
    pool: str = Field(description="The pool's name.")
    used: int | None = Field(
        default=None, description="Its quota use now, in bytes; null when usage is quotas_off or unknown."
    )
    quota: int | None = Field(default=None, description="Its quota now, in bytes; null without one.")
    usage: VolumeUsageState = Field(description=f"How used was read now: {_USAGE_STATES}.")
    points: list[VolumeUsagePoint] = Field(
        description="One point per hour of the last 30 days that the supervisor kept, oldest first, and the "
        "current reading last when there is one.",
    )


class RestoreCopyInfo(BaseModel):
    """A writable copy that a snapshot's restore made right in a pool or a volume."""
    # ADR-0358.

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "name": "restore_20261112_093000", "pool": "data", "volume": "documents",
        "subvolume": "/mnt/pools/data/documents", "path": "/mnt/pools/data/documents/restore_20261112_093000",
        "created": "2026-11-12T09:30:00+00:00",
    }]})

    name: str = Field(description="The restored copy's name, restore_<date>_<time> in UTC.")
    pool: str = Field(description="The pool it is in.")
    # The volume it is in; None for one in the pool's top folder.
    volume: str | None = Field(default=None, description="The volume it is in; null for one in the pool's top folder.")
    # The folder it is in: the pool's mountpoint or the volume's folder.
    subvolume: str = Field(description="The folder it is in: the pool's mountpoint or the volume's folder.")
    path: str = Field(description="Its own folder.")
    # When the restore made it, from its name: UTC, with its offset ("2026-11-12T09:30:00+00:00").
    created: str = Field(description="When the restore made it, from its name: UTC, with its offset.")


class ScrubStatus(BaseModel):
    """A pool's scrub, as btrfs scrub status reports it."""

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "pool": "data", "status": "finished", "duration": 5421.0, "bytes_scrubbed": 1300280639488,
        "total_bytes": 1300280639488, "rate": 239859712, "errors": 0, "last_run": "2026-11-18T02:00:01Z",
    }]})

    pool: str = Field(description="The pool's name.")
    # As btrfs scrub status prints it: "running", "finished", "aborted" (cancelled),
    # "interrupted" (ended neither by finishing nor by a cancel, for example because the btrfs
    # process that ran it was killed); "idle": no scrub recorded and none running, also after a
    # restart of the supervisor, which forgets the last one (ADR-0171).
    status: str = Field(
        description="running, finished, aborted (cancelled), interrupted (ended neither by finishing nor by a "
        "cancel), or idle (no scrub recorded and none running).",
    )
    # seconds
    duration: float | None = Field(
        default=None, description="How long it ran, in seconds; null when not reported."
    )
    bytes_scrubbed: int | None = Field(default=None, description="The bytes scrubbed so far; null when not reported.")
    total_bytes: int | None = Field(default=None, description="The bytes to scrub; null when not reported.")
    rate: int | None = Field(default=None, description="Bytes per second; null when not reported.")  # bytes per second
    errors: int = Field(
        default=0, description="The errors found: the sum of its read, super, verify and checksum errors.",
    )  # the sum of the error summary: read, super, verify and csum errors (ADR-0313)
    # When the scrub started (ADR-0401): btrfs prints it in the clock of the supervisor's container,
    # UTC; None when btrfs printed no start, or one of another form.
    last_run: UtcDatetime | None = Field(
        default=None, description="When the scrub started, in UTC; null when btrfs reports no start.",
    )


# The usage limit of a balance when the request names none (ADR-0379): the data and metadata
# chunks less than this many percent full are rewritten, which frees the space of partly empty
# chunks in minutes instead of rewriting the whole pool.
BALANCE_DEFAULT_USAGE = 20


class BalanceStart(BaseModel):
    """What POST /storage/pools/{name}/balance balances."""
    # ADR-0379.

    model_config = ConfigDict(json_schema_extra={"examples": [{"usage": 20, "full": False}]})

    # Rewrite only the data and metadata chunks less than this many percent full
    # (btrfs balance start -dusage=N -musage=N). 100 would rewrite every chunk: that is ``full``.
    usage: int | None = Field(
        default=None, ge=1, le=99,
        description="Rewrite only the data and metadata chunks less than this many percent full, 1 to 99; null "
        "for 20. Not with full.",
    )
    # Rewrite every chunk (--full-balance): hours on a hard disk; the page asks first.
    full: bool = Field(
        default=False, description="Rewrite every chunk (a full balance), which takes hours on a hard disk."
    )

    @model_validator(mode="after")
    def _one_kind(self) -> "BalanceStart":
        if self.full and self.usage is not None:
            raise ValueError("A full balance rewrites every chunk: it takes no usage limit")
        return self

    @property
    def limit(self) -> int:
        return BALANCE_DEFAULT_USAGE if self.usage is None else self.usage


class BalanceStatus(BaseModel):
    """A pool's balance as btrfs balance status reports it, and how the last one ended."""
    # ADR-0379.

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "pool": "data", "status": "running", "completed": 12, "expected": 40, "considered": 52,
        "seconds_left": 840, "result": None, "error": None,
    }]})

    pool: str = Field(description="The pool's name.")
    # "running"; "pausing" and "cancelling" while a pause or a cancel waits for the chunk that is
    # being moved; "paused"; "idle": none runs and none is paused.
    status: str = Field(
        description="running; pausing or cancelling while a pause or a cancel waits for the chunk being moved; "
        "paused; idle when none runs and none is paused.",
    )
    # Of a balance that runs or is paused: the chunks balanced since its start or resume, of about
    # ``expected``, and those it considered. Of a finished one: the chunks it relocated, of
    # ``considered``.
    completed: int | None = Field(
        default=None,
        description="Of a balance that runs or is paused: the chunks balanced since its start or resume. Of a "
        "finished one: the chunks it relocated. Null otherwise.",
    )
    expected: int | None = Field(
        default=None,
        description="Of a balance that runs or is paused: about how many chunks it balances; null otherwise.",
    )
    considered: int | None = Field(default=None, description="The chunks it considered; null when not reported.")
    # Of a balance that runs: the seconds it needs still, at the rate of the chunks it balanced
    # since UNOS first saw it run; None until it balanced one.
    seconds_left: int | None = Field(
        default=None,
        description="Of a balance that runs: the seconds it still needs, at the rate seen so far; null until it "
        "balanced a chunk.",
    )
    # Of an idle pool: how the last balance that UNOS started or resumed since the host started
    # ended, from its output: "finished", "cancelled" or "failed" (``error``: btrfs's error);
    # None when that is not known.
    result: str | None = Field(
        default=None,
        description="Of an idle pool: how the last balance that UNOS started or resumed since the host started "
        "ended: finished, cancelled or failed; null when not known.",
    )
    error: str | None = Field(default=None, description="btrfs's error of a failed balance; null otherwise.")


class DiskAdd(BaseModel):
    """Body of POST /storage/pools/{name}/devices: the disk that joins the pool."""
    # ADR-0411.

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "device": "/dev/sdc", "confirm_device": "/dev/sdc",
        "identity": {"path": "/dev/sdc", "serial": "EXAMPLE-SERIAL-0003", "wwn": None, "size": _DISK_SIZE},
        "force": False, "contents": None,
    }]})

    device: str = Field(description="The whole disk's device path, such as /dev/sdc.")
    # The disk's path typed again, the confirmation of the dialog (HTTP 400 otherwise).
    confirm_device: str = Field(description="The disk's path typed again, the confirmation: it must equal device.")
    # The disk as GET /storage/pools/{name}/devices offered it: the path must still hold the disk
    # with this serial, WWN and size (HTTP 409 otherwise), since disks change names when they are
    # reconnected.
    identity: DeviceIdentity = Field(
        description="The disk as GET /storage/pools/{name}/devices offered it: the path must still hold that "
        "disk, since disks change names when they are reconnected.",
    )
    # Erase what the disk holds: needed for a disk with data (HTTP 409 otherwise).
    force: bool = Field(default=False, description="Erase what the disk holds; needed for a disk with data.")
    # What the list showed the disk to hold (its data_description): an add of a disk with data is
    # refused when it now holds something else (HTTP 409), such as a sleeping disk listed with
    # unknown contents, which the add's read wakes.
    contents: str | None = Field(
        default=None,
        description="What the list showed the disk to hold (its data_description): a disk with data that now "
        "holds something else is refused.",
    )


class DiskRemove(BaseModel):
    """Body of POST /storage/pools/{name}/removal: the disk that leaves the pool."""
    # ADR-0411.

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "devid": 2, "device": "/dev/sdb", "confirm_device": "/dev/sdb",
    }]})

    # btrfs's number of the disk in the pool, which reconnecting the disk does not change.
    devid: int = Field(
        ge=1, description="btrfs's number of the disk in the pool, which reconnecting the disk does not change.",
    )
    # Its path as the list showed it: still the devid's (HTTP 409 otherwise).
    device: str = Field(description="Its device path as the list showed it: it must still be the devid's.")
    # The path typed again, the confirmation of the dialog (HTTP 400 otherwise).
    confirm_device: str = Field(description="The path typed again, the confirmation: it must equal device.")


class PoolMember(BaseModel):
    """A disk of a mounted pool, as btrfs filesystem show reports it."""
    # ADR-0411.

    devid: int = Field(description="btrfs's number of the disk in the pool.")
    path: str = Field(description="Its device path.")
    # The bytes btrfs may use on it: 0 while the kernel removes it from the pool.
    size: int = Field(description="The bytes that btrfs may use on it; 0 while the kernel removes it from the pool.")
    # The bytes of the chunks allocated on it.
    used: int = Field(description="The bytes of the chunks allocated on it.")
    model: str | None = Field(default=None, description="The disk's model; null when unknown.")
    serial: str | None = Field(default=None, description="The disk's serial number; null when unknown.")
    # Why it cannot be removed from the pool (the only disk, the profile, the space); None when it can.
    problem: str | None = Field(
        default=None,
        description="Why it cannot be removed from the pool (the only disk, the profile, the space); null when it "
        "can.",
    )


class DiskCandidate(BaseModel):
    """A whole disk that can be added to a pool: neither the system disk nor in use, and
    without a btrfs filesystem. Read without waking it: a sleeping one has unknown
    contents, which count as data."""
    # ADR-0411, ADR-0378.

    name: str = Field(description="The disk's name, such as sdc.")
    path: str = Field(description="Its device path.")
    model: str | None = Field(default=None, description="Its model; null when it reports none.")
    serial: str | None = Field(default=None, description="Its serial number; null when it reports none.")
    wwn: str | None = Field(default=None, description="Its World Wide Name; null when it reports none.")
    size: int = Field(description="Its size in bytes.")
    # What it holds, which an add erases (with ``force``).
    has_data: bool = Field(
        description="Whether it holds data, which an add erases with force; true for a sleeping disk, whose "
        "contents are unknown.",
    )
    data_description: str | None = Field(default=None, description="What it holds, in English; null when nothing.")


class DiskRemovalStatus(BaseModel):
    """A pool's disk removal: the one that runs, or how the last one ended."""
    # ADR-0411.

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "pool": "data", "status": "running", "devid": 2, "device": "/dev/sdb", "left": 429496729600,
        "total": 650140319744, "result": None, "error": None,
    }]})

    pool: str = Field(description="The pool's name.")
    # "running" while the kernel moves the data off a disk of the pool (its size is 0), else "idle".
    status: str = Field(description="running while the kernel moves the data off a disk of the pool; idle otherwise.")
    # Of a removal that runs, or of the last one when its result is known: the disk.
    devid: int | None = Field(
        default=None, description="The disk's devid, of a removal that runs or of the last one whose result is known.",
    )
    device: str | None = Field(
        default=None,
        description="The disk's device path, of a removal that runs or of the last one whose result is known.",
    )
    # Of a removal that runs: the bytes still allocated on the disk, and those at its start when known.
    left: int | None = Field(default=None, description="Of a removal that runs: the bytes still allocated on the disk.")
    total: int | None = Field(
        default=None,
        description="Of a removal that runs: the bytes allocated on the disk at its start; null when unknown.",
    )
    # Of an idle pool: how the last removal that UNOS started since the host started ended,
    # "finished" or "failed" (``error``: btrfs's error); None when that is not known.
    result: str | None = Field(
        default=None,
        description="Of an idle pool: how the last removal that UNOS started since the host started ended, "
        "finished or failed; null when not known.",
    )
    error: str | None = Field(default=None, description="btrfs's error of a failed removal; null otherwise.")


class PoolDevices(BaseModel):
    """GET /storage/pools/{name}/devices: the disks of a mounted pool and those that can join it."""

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "pool": "data", "profiles": ["raid1"],
        "members": [
            {"devid": 1, "path": "/dev/sda", "size": _DISK_SIZE, "used": 650140319744,
             "model": "WDC WD40EFRX-68N32N0", "serial": "EXAMPLE-SERIAL-0001",
             "problem": "the pool's raid1 profile needs at least 2 disks, and without /dev/sda it would keep 1"},
            {"devid": 2, "path": "/dev/sdb", "size": _DISK_SIZE, "used": 650140319744,
             "model": "WDC WD40EFRX-68N32N0", "serial": "EXAMPLE-SERIAL-0002",
             "problem": "the pool's raid1 profile needs at least 2 disks, and without /dev/sdb it would keep 1"},
        ],
        "candidates": [
            {"name": "sdc", "path": "/dev/sdc", "model": "WDC WD40EFRX-68N32N0", "serial": "EXAMPLE-SERIAL-0003",
             "wwn": None, "size": _DISK_SIZE, "has_data": False, "data_description": None},
        ],
        "removal": _IDLE_REMOVAL,
    }]})

    pool: str = Field(description="The pool's name.")
    # The profiles of its data, metadata and system chunks (btrfs filesystem df), lower case.
    profiles: list[str] = Field(
        description="The profiles of its data, metadata and system chunks, lower case, each once, such as raid1.",
    )
    members: list[PoolMember] = Field(description="Its disks that are present.")
    candidates: list[DiskCandidate] = Field(description="The whole disks that can join it.")
    removal: DiskRemovalStatus = Field(description="Its disk removal.")
