from typing import Literal

from pydantic import BaseModel, Field, model_validator


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


# Why a btrfs filesystem is not the pool its label names (ADR-0283): it has none, it is
# not a valid pool name, or another btrfs filesystem of the host has it too, and the pool
# of that name is the other one (mounted or managed under it) or neither.
LabelProblem = Literal["missing", "invalid", "duplicate"]


class ImportableFilesystem(BaseModel):
    """A btrfs filesystem on the host's disks that can be imported as a pool (ADR-0283)."""

    uuid: str
    label: str | None = None  # as btrfs prints it; None without a label
    label_problem: LabelProblem
    devices: list[str]  # the members that are present
    total_devices: int
    missing_devices: int = 0
    size: int  # bytes, of the members that are present
    used: int  # bytes used, as btrfs filesystem show reports them


class PoolImport(BaseModel):
    """Body of POST /storage/filesystems/{uuid}/import: the pool's name, which becomes its label."""

    name: str


# btrfs stores a quota limit as an unsigned 64-bit number and reads (u64)-1
# as "no limit", so the largest limit is one less.
MAX_VOLUME_QUOTA = (1 << 64) - 2


class VolumeCreate(BaseModel):
    """Body of POST /storage/pools/{pool_name}/volumes: the pool is the one in the URL."""

    name: str
    # Bytes; None = no limit. Needs quotas enabled on the pool (btrfs quota enable).
    quota: int | None = Field(default=None, gt=0, le=MAX_VOLUME_QUOTA)


class VolumeInfo(BaseModel):
    name: str
    pool: str
    path: str
    used: int | None = None
    quota: int | None = None


class RestoreCopyInfo(BaseModel):
    """A writable copy that a snapshot's restore made right in a pool or a volume (ADR-0358)."""

    name: str
    pool: str
    # The volume it is in; None for one in the pool's top folder.
    volume: str | None = None
    # The folder it is in: the pool's mountpoint or the volume's folder.
    subvolume: str
    path: str
    # When the restore made it, from its name: UTC, with its offset ("2026-11-12T09:30:00+00:00").
    created: str


class ScrubStatus(BaseModel):
    pool: str
    # As btrfs scrub status prints it: "running", "finished", "aborted" (cancelled),
    # "interrupted" (ended neither by finishing nor by a cancel, for example because the btrfs
    # process that ran it was killed); "idle": no scrub recorded and none running, also after a
    # restart of the supervisor, which forgets the last one (ADR-0171).
    status: str
    duration: float | None = None  # seconds
    bytes_scrubbed: int | None = None
    total_bytes: int | None = None
    rate: int | None = None  # bytes per second
    errors: int = 0  # the sum of the error summary: read, super, verify and csum errors (ADR-0313)
    last_run: str | None = None  # when the scrub started, as btrfs prints it


# The usage limit of a balance when the request names none (ADR-0377): the data and metadata
# chunks less than this many percent full are rewritten, which frees the space of partly empty
# chunks in minutes instead of rewriting the whole pool.
BALANCE_DEFAULT_USAGE = 20


class BalanceStart(BaseModel):
    """What POST /storage/pools/{name}/balance balances (ADR-0377)."""

    # Rewrite only the data and metadata chunks less than this many percent full
    # (btrfs balance start -dusage=N -musage=N). 100 would rewrite every chunk: that is ``full``.
    usage: int | None = Field(default=None, ge=1, le=99)
    # Rewrite every chunk (--full-balance): hours on a hard disk; the page asks first.
    full: bool = False

    @model_validator(mode="after")
    def _one_kind(self) -> "BalanceStart":
        if self.full and self.usage is not None:
            raise ValueError("A full balance rewrites every chunk: it takes no usage limit")
        return self

    @property
    def limit(self) -> int:
        return BALANCE_DEFAULT_USAGE if self.usage is None else self.usage


class BalanceStatus(BaseModel):
    """A pool's balance as btrfs balance status reports it, and how the last one ended (ADR-0377)."""

    pool: str
    # "running"; "pausing" and "cancelling" while a pause or a cancel waits for the chunk that is
    # being moved; "paused"; "idle": none runs and none is paused.
    status: str
    # Of a balance that runs or is paused: the chunks balanced since its start or resume, of about
    # ``expected``, and those it considered. Of a finished one: the chunks it relocated, of
    # ``considered``.
    completed: int | None = None
    expected: int | None = None
    considered: int | None = None
    # Of a balance that runs: the seconds it needs still, at the rate of the chunks it balanced
    # since UNOS first saw it run; None until it balanced one.
    seconds_left: int | None = None
    # Of an idle pool: how the last balance that UNOS started or resumed since the host started
    # ended, from its output: "finished", "cancelled" or "failed" (``error``: btrfs's error);
    # None when that is not known.
    result: str | None = None
    error: str | None = None
