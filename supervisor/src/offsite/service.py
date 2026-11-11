"""The off-device copy: one copy repository per installation, the copies to it and the restores from it.

ADR-0337 decides it. restic keeps the copy repository, encrypted with a password that the
administrator types and that only this device keeps: a folder on a disk mounted below MOUNTS_BASE
that is not the system disk (disk), or an S3-compatible store (s3).

* The settings are in SETTINGS_FILE, without a secret. The repository's password and the S3 secret
  key are in SECRETS_FILE, mode 0600, written as a new file renamed over the old one and synced with
  its folder; one that cannot be read is kept as .bad (ADR-0324). Neither file is in the
  configuration backups, and no answer, log line, status or argument of restic holds a secret: restic
  gets them in an environment of its own (restic.py), and its messages are redacted.
* Saving the settings tests the target: ``restic cat config`` with the password opens an existing
  repository, and ``restic init`` makes one only when none is there. A copy never makes one, so that
  a disk that is not mounted never gets a repository on its empty mount point.
* A disk target is off the system disk: not on the filesystem of MOUNTS_BASE or DATA_DIR, nor on the
  whole disks under them (on_system_disk). While restic runs, the target, the folders it copies and
  the folder a restore writes are held open, so that a plain unmount of their disks fails as busy,
  and the Storage page refuses to unmount or forget a pool that a copy or a restore uses (pool_user).
* A copy (run_copy, for the Scheduler's task type offsite_copy and for Run now): unlock, the
  configuration archive of Create Backup from standard input, the pools' folders, forget with the
  retention and --prune, check with a share of the data, and the restore test, which reads the
  configuration archive back and checks it as a restore would. Every step waits up to RETRY_LOCK for
  the lock of another restic, and every snapshot names the host HOST. Files that vanished while
  restic read them are a note; files that could not be read fail the copy once every step ran. One
  copy or restore runs at a time; a copy that finds one running ends at once as a success that says
  so. Its result is the one write of STATUS_FILE per run, besides the Scheduler's record.
* The restores: the configuration of a snapshot into the backup list (restore_configuration), the
  files of a snapshot into a new folder of a pool (restore_files, in the background).
"""

from __future__ import annotations

import asyncio
import errno
import fcntl
import hashlib
import ipaddress
import json
import logging
import os
import re
import socket
import ssl
import tempfile
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

from fastapi import HTTPException
from pydantic import BaseModel, Field, ValidationError

from .. import state_files
from ..backup.router import (
    MAX_ARCHIVE_BYTES,
    BackupInfo,
    BackupValidationError,
    check_archive,
    configuration_archive,
    store_backup,
)
from ..security.validators import (
    open_dir_nofollow,
    open_subdirs_nofollow,
    pools_base,
    validate_pool_name,
    validate_pools_path,
)
from ..storage import pools as storage_pools
from . import restic

logger = logging.getLogger(__name__)

SETTINGS_FILE = "/data/offsite.json"
SECRETS_FILE = "/data/offsite_secrets.json"
STATUS_FILE = "/data/offsite_status.json"
# The data volume, and the folder below which the disks are mounted: the filesystems of both are the
# system disk's, where a disk target is refused (on_system_disk).
DATA_DIR = "/data"
MOUNTS_BASE = "/mnt"
# Where the disks under a filesystem are found: the block devices by number, the devices of each btrfs
# filesystem, and the mounts of this process (a filesystem without a device number of its own, FUSE).
SYS_DEV_BLOCK = "/sys/dev/block"
SYS_FS_BTRFS = "/sys/fs/btrfs"
PROC_SELF = "/proc/self"
# _IOR(0x94, 31, struct btrfs_ioctl_fs_info_args): the btrfs filesystem's info, its fsid at bytes 16 to 32.
BTRFS_IOC_FS_INFO = 0x8400941F
# How long a step of a copy or a restore waits for the lock of another restic (restic 0.16 and later).
RETRY_LOCK = "10m"
# The host every snapshot names, whatever the container's hostname is: forget groups by it.
HOST = "unos"
# The files that cannot be read that a result names, and that a note of vanished files names.
NAMED_FILES = 5

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 1024
MAX_SOURCES = 64
MAX_PATH_LENGTH = 4096
MAX_LIMIT_KIB = 10_000_000
MAX_KEEP = 10_000
CONFIGURATION_TAG = "unos-configuration"
DATA_TAG = "unos-data"
CONFIGURATION_NAME = "unos-configuration.tar.gz"
SNAPSHOTS = ".snapshots"
TRASH = ".unos-trash"
# restic's exit codes (0.17 and later) for a repository that does not exist and for a wrong password,
# and of a backup that saved its snapshot without the files that it could not read.
REPOSITORY_MISSING = 10
WRONG_PASSWORD = 12
PARTIAL = 3

_MINUTE, _HOUR, _DAY = 60, 3600, 86400
# Seconds that Save and a run wait for an S3 store to take a connection, with the TLS handshake for
# https, before restic runs (probe_store): restic retries a store that does not answer for minutes.
PROBE_TIMEOUT = 10
# Seconds that the restic commands of Save (cat config, init) may take: its request waits for them,
# holding the lock of the module.
SAVE_TIMEOUT = 120
# Seconds each restic command of a run or a restore may take; then it is killed with its process
# group (restic.py).
TIMEOUTS = {
    "unlock": 10 * _MINUTE,
    "snapshots": 10 * _MINUTE,
    "dump": _HOUR,
    "check": 6 * _HOUR,
    "backup": 7 * _DAY,
    "forget": 7 * _DAY,
    "restore": 7 * _DAY,
}

NOT_SET_UP = "No off-device copy is set up: set one up on System > Backup & Restore"
NO_PASSWORD = ("The password of the copy repository is not stored: save the settings of the off-device copy "
               "again with it")
NO_SECRET_KEY = ("The secret access key of the S3 store is not stored: save the settings of the off-device copy "
                 "again with it")
STOPPED_DURING_COPY = "The supervisor stopped during the copy"
STOPPED_DURING_RESTORE = "The supervisor stopped during the restore"
SAME_DISK = ("This copy is on a disk it copies: it protects against deleted and damaged files, not against the loss "
             "of that disk")
# The commands that wait up to RETRY_LOCK for the lock of another restic (a listing, a copy of another
# device or another program) instead of failing at once: every step of a copy and of the restores.
# Saving the settings does not wait (cat, init), and the listing of the snapshots takes no lock.
WAIT_FOR_LOCKS = {"unlock", "backup", "forget", "check", "dump", "restore"}
INCOMPLETE = "The copy of the data is incomplete"
# What failed, before the error of a step that runs restic, in the result of a copy.
STEP_TEXTS = {
    "unlock": "Removing stale locks of the copy repository failed",
    "configuration": "The copy of the configuration failed",
    "data": "The copy of the data failed",
    "forget": "Removing old snapshots failed",
    "check": "The check of the copy repository failed",
    "restore_test": "The restore test failed",
}
# A path that restic would read as a pattern in --exclude.
_PATTERN_CHARACTERS = re.compile(r"[*?\[\\]")
_SNAPSHOT_ID = re.compile(r"[0-9a-f]{8,64}")
_HOST_NAME = re.compile(r"[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*")
_BUCKET = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{1,61}[A-Za-z0-9]")
_PREFIX_PART = re.compile(r"[A-Za-z0-9._-]{1,128}")
_REGION = re.compile(r"[A-Za-z0-9-]{1,64}")
_KEY = re.compile(r"[\x21-\x7e]{1,256}")


# --- the models -----------------------------------------------------------------------------------


class Kind(str, Enum):
    DISK = "disk"
    S3 = "s3"


class OffsiteSettings(BaseModel):
    """The copy target and what is copied: SETTINGS_FILE, and the settings in the answers. No secret."""

    kind: Kind
    path: Optional[str] = None  # disk: the folder of the repository, below MOUNTS_BASE
    endpoint: Optional[str] = None  # s3: https://host[:port] or http://host[:port]
    bucket: Optional[str] = None
    prefix: Optional[str] = None
    region: Optional[str] = None
    access_key_id: Optional[str] = None
    sources: list[str] = []  # folders of the pools
    limit_upload_kib: int = 0  # KiB/s, 0: none; s3 only
    limit_download_kib: int = 0
    keep_daily: int = 7
    keep_weekly: int = 4
    keep_monthly: int = 12
    check_percent: int = 2  # the share of the data that each check reads, 0 to 100


class SettingsRequest(OffsiteSettings):
    """PUT /offsite/settings. The secrets are write-only: in no answer, no repr() and no settings file.
    Without them an edit of the same target keeps the stored ones."""

    password: Optional[str] = Field(default=None, exclude=True, repr=False)
    s3_secret_access_key: Optional[str] = Field(default=None, exclude=True, repr=False)


class Secrets(BaseModel):
    """SECRETS_FILE: what restic needs to open the repository."""

    password: Optional[str] = Field(default=None, repr=False)
    s3_secret_access_key: Optional[str] = Field(default=None, repr=False)


class CopySummary(BaseModel):
    files_new: int = 0
    files_changed: int = 0
    files_unmodified: int = 0
    bytes_added: int = 0  # by both snapshots, before compression
    configuration_snapshot: Optional[str] = None
    data_snapshot: Optional[str] = None


class CopyResult(BaseModel):
    """The last copy, in STATUS_FILE. Times are ISO 8601 in UTC."""

    started: str
    ended: Optional[str] = None
    trigger: str  # task: a task of the Scheduler; manual: Run now on Backup & Restore
    success: bool = False
    step: Optional[str] = None  # the step that failed: settings, folders, unlock, configuration, data, ...
    error: Optional[str] = None
    summary: Optional[CopySummary] = None
    check: Optional[str] = None  # passed or failed; None when the run did not get there
    restore_test: Optional[str] = None
    # What the copy of the data could not read that is not the error: the files that vanished while
    # restic read the folders (no failure), and those that could not be read when a later step failed.
    note: Optional[str] = None


class RestoreResult(BaseModel):
    """The last restore of files, in STATUS_FILE."""

    snapshot_id: str
    path: Optional[str] = None
    folder: str
    started: str
    ended: Optional[str] = None
    success: bool = False
    error: Optional[str] = None


class Status(BaseModel):
    last_copy: Optional[CopyResult] = None
    last_restore: Optional[RestoreResult] = None


class RestoreConfigurationRequest(BaseModel):
    snapshot_id: str


class RestoreFilesRequest(BaseModel):
    snapshot_id: str
    pool: str
    path: Optional[str] = None  # a folder in the snapshot; none: all of it


# --- what runs ------------------------------------------------------------------------------------

# What runs, by its kind: a copy, a restore, or a change of the settings, which waits for neither.
_WHAT = {"copy": "copy", "restore-files": "restore", "restore-configuration": "restore",
         "save": "test of the settings", "remove": "removal of the settings"}


@dataclass
class Activity:
    kind: str
    started: datetime
    step: Optional[str] = None
    snapshot_id: Optional[str] = None
    folder: Optional[str] = None
    # The pools whose folders it reads or writes: the Storage page neither unmounts nor forgets them.
    pools: set[str] = field(default_factory=set)
    # The folders it reads or writes (the target, the folders it copies, a restore's folder): the
    # Storage page deletes no volume that is one of them or holds one.
    paths: set[str] = field(default_factory=set)

    def answer(self) -> dict:
        return {"kind": self.kind, "started": self.started.isoformat(), "step": self.step,
                "snapshot_id": self.snapshot_id, "folder": self.folder}


# The one copy, restore or change of the settings that runs: taken and given back on the event loop.
_activity: Optional[Activity] = None
# The copies and restores that run in the background (Run now, Restore files): stop() ends them.
_tasks: set[asyncio.Task] = set()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _running(activity: Activity) -> str:
    return f"the {_WHAT[activity.kind]} started at {activity.started:%Y-%m-%d %H:%M:%S} UTC is still running"


def _busy() -> str:
    return _running(_activity)


def _begin(kind: str) -> Optional[Activity]:
    """Take the lock for KIND; None when something runs."""
    global _activity
    if _activity is not None:
        return None
    _activity = Activity(kind, _utc_now())
    return _activity


def _begin_or_409(kind: str) -> Activity:
    activity = _begin(kind)
    if activity is None:
        text = _busy()
        raise HTTPException(409, text[0].upper() + text[1:])
    return activity


def _end(activity: Activity) -> None:
    global _activity
    if _activity is activity:
        _activity = None


def pools_in_use() -> set[str]:
    """The pools that the copy or restore that runs reads or writes."""
    activity = _activity
    return set(activity.pools) if activity is not None else set()


def volume_user(volume_path: str) -> Optional[str]:
    """What of the off-device copy uses the volume at VOLUME_PATH, as pool_user says it: a target, a
    folder that it copies or a restore's folder that is the volume or lies in it. A folder above it,
    such as the whole pool, does not count, as for the volume's other users (storage/pools.py)."""
    activity = _activity
    if activity is None or not any(_inside(path, volume_path) for path in activity.paths):
        return None
    return _running(activity)


def pool_user(pool: str) -> Optional[str]:
    """What of the off-device copy uses POOL, as "the copy started at ... is still running", or None.
    The Storage page refuses to unmount or forget the pool meanwhile (storage/pools.py)."""
    activity = _activity
    if activity is None or pool not in activity.pools:
        return None
    return _running(activity)


def _keep(task: asyncio.Task) -> None:
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def stop() -> None:
    """End the copies and restores that run in the background, with their restic (the lifespan's end):
    each is recorded as stopped by the supervisor."""
    tasks = list(_tasks)
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


# --- the state files ------------------------------------------------------------------------------


def _sync_folder(folder: Path) -> None:
    fd = os.open(folder, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _reader(model: type[BaseModel]):
    """MODEL's validation of a state file, whose refusal names the fields and why, never their values:
    load_json logs it, and pydantic's own message repeats the values, a password among them."""

    def read(data: object) -> BaseModel:
        try:
            return model.model_validate(data)
        except ValidationError as e:
            fields = "; ".join(f"{'.'.join(str(part) for part in error['loc']) or 'the file'}: {error['type']}"
                               for error in e.errors())
            raise ValueError(f"it holds no valid {model.__name__} ({fields})") from None

    return read


def load_settings() -> Optional[OffsiteSettings]:
    """The settings; None without a file, or for one kept as .bad. Raises OSError for one that cannot
    be opened (ADR-0324: settings are never replaced unread)."""
    return state_files.load_json(Path(SETTINGS_FILE), _reader(OffsiteSettings), lambda: None,
                                 what="the settings of the off-device copy")


def write_settings(settings: OffsiteSettings) -> None:
    """Replace SETTINGS_FILE, synced with its folder. Blocking: call it in a worker thread."""
    path = Path(SETTINGS_FILE)
    state_files.write_json(path, settings.model_dump(mode="json"), sync=True)
    _sync_folder(path.parent)


def load_secrets() -> Secrets:
    """The secrets; none without a file, or for one kept as .bad (with its mode 0600). Raises OSError
    for one that cannot be opened."""
    return state_files.load_json(Path(SECRETS_FILE), _reader(Secrets), Secrets,
                                 what="the secrets of the off-device copy")


def write_secrets(secrets: Secrets) -> None:
    """Replace SECRETS_FILE with a new file of mode 0600, synced with its folder (ADR-0325's rule)."""
    path = Path(SECRETS_FILE)
    state_files.write_json(path, secrets.model_dump(), sync=True, mode=0o600)
    _sync_folder(path.parent)


def load_status() -> Status:
    """The last copy and restore; none for a file that cannot be read, with a warning: a history."""
    try:
        return state_files.load_json(Path(STATUS_FILE), _reader(Status), Status,
                                     what="the state of the off-device copy")
    except OSError as e:
        logger.warning("Could not read the state of the off-device copy %s: %s", STATUS_FILE, e)
        return Status()


def write_status(status: Status) -> None:
    """Replace STATUS_FILE: one small write at the end of each copy and restore of files."""
    state_files.write_json(Path(STATUS_FILE), status.model_dump(mode="json"), sync=False)


def _stored() -> tuple[Optional[OffsiteSettings], Secrets]:
    """The settings and the secrets; HTTPException(500) when a file cannot be opened."""
    try:
        return load_settings(), load_secrets()
    except OSError as e:
        raise HTTPException(500, f"The settings of the off-device copy cannot be read: {e}") from e


# --- the system disk, the target and the sources --------------------------------------------------


def system_devices() -> set[int]:
    """The devices of the filesystems of MOUNTS_BASE (the root filesystem: a disk that is not mounted
    leaves its mount point there) and of DATA_DIR (the data volume)."""
    found = set()
    for path in (MOUNTS_BASE, DATA_DIR):
        try:
            found.add(os.stat(path).st_dev)
        except OSError:
            continue
    return found


def on_system_filesystem(fd: int) -> bool:
    """Whether the folder open at FD is on the filesystem of MOUNTS_BASE or DATA_DIR (system_devices):
    below /mnt, a folder of a disk that is not mounted, such as a pool's empty mount point."""
    return os.fstat(fd).st_dev in system_devices()


class UnknownDisk(Exception):
    """The whole disks under a folder, or under the system's filesystems, cannot be found."""


def on_system_disk(fd: int) -> bool:
    """Whether the folder open at FD is on the system disk: on the filesystem of MOUNTS_BASE or
    DATA_DIR, or on one of the whole disks that hold them, as another partition of the system disk is.
    UnknownDisk when the disks under the folder or under the system's filesystems cannot be found
    (sysfs that cannot be read, a network filesystem, ZFS, an overlay root): the rule cannot be kept."""
    if on_system_filesystem(fd):
        return True
    system = system_disks()
    if not system:
        raise UnknownDisk("the disks of the system's filesystems are not known")
    disks = disks_of(fd)
    if not disks:
        raise UnknownDisk("its filesystem shows no disk")
    return bool(disks & system)


def _read_dev(path: str) -> Optional[int]:
    """The device number in a "major:minor" file of sysfs, or None."""
    try:
        with open(path) as f:
            major, minor = f.read().strip().split(":")
        return os.makedev(int(major), int(minor))
    except (OSError, ValueError):
        return None


def _major_minor(device: int) -> str:
    """DEVICE as sysfs names it, major:minor. (On macOS, where the tests run too, os.makedev gives a
    large major a negative dev_t, which os.major refuses.)"""
    if device < 0:
        device &= 0xFFFFFFFF
    return f"{os.major(device)}:{os.minor(device)}"


def _whole_disks(device: int, depth: int) -> set[str]:
    path = os.path.realpath(os.path.join(SYS_DEV_BLOCK, _major_minor(device)))
    if not os.path.isdir(path):
        return set()
    if depth < 8:
        if os.path.exists(os.path.join(path, "partition")):  # sda1, nvme0n1p2, md0p1: its device's disks
            parent = _read_dev(os.path.join(os.path.dirname(path), "dev"))
            if parent is None:
                return {os.path.basename(os.path.dirname(path))}
            return _whole_disks(parent, depth + 1)
        slaves = os.path.join(path, "slaves")
        try:
            below = sorted(os.listdir(slaves))
        except OSError:
            below = []
        if below:  # device mapper (LVM, LUKS) and md: the devices below them
            found: set[str] = set()
            for name in below:
                number = _read_dev(os.path.join(slaves, name, "dev"))
                if number is not None:
                    found |= _whole_disks(number, depth + 1)
            return found
    return {os.path.basename(path)}  # a disk; a loop device counts as one of its own


def whole_disks(devices: set[int]) -> set[str]:
    """The whole disks (sda, nvme0n1, mmcblk1) under the block DEVICES: a partition's disk, and the
    disks below a device mapper or md device. None for a device that sysfs does not know."""
    found: set[str] = set()
    for device in devices:
        found |= _whole_disks(device, 0)
    return found


def _btrfs_fsid(fd: int) -> Optional[str]:
    """The UUID of the btrfs filesystem of FD, or None for another filesystem."""
    info = bytearray(1024)
    try:
        fcntl.ioctl(fd, BTRFS_IOC_FS_INFO, info)
    except OSError:
        return None
    return str(uuid.UUID(bytes=bytes(info[16:32])))


def btrfs_devices(fsid: str) -> set[int]:
    """The device numbers of the btrfs filesystem FSID, all of them."""
    folder = os.path.join(SYS_FS_BTRFS, fsid, "devices")
    try:
        names = os.listdir(folder)
    except OSError:
        return set()
    found = set()
    for name in names:
        number = _read_dev(os.path.join(folder, name, "dev"))
        if number is not None:
            found.add(number)
    return found


def mount_source(fd: int) -> Optional[str]:
    """The source of the mount that holds FD, as /proc/self/mountinfo names it, or None."""
    try:
        with open(f"{PROC_SELF}/fdinfo/{fd}") as f:
            mount_id = next((line.split(":", 1)[1].strip() for line in f if line.startswith("mnt_id:")), None)
        if mount_id is None:
            return None
        with open(f"{PROC_SELF}/mountinfo") as f:
            for line in f:
                fields = line.split()
                if fields and fields[0] == mount_id and "-" in fields:
                    after = fields[fields.index("-") + 1:]
                    return after[1] if len(after) >= 2 else None
    except OSError:
        return None
    return None


def _filesystem_devices(fd: int) -> set[int]:
    """The block devices under the filesystem of FD: its device number; for btrfs, whose every
    subvolume has a number of its own, all of the filesystem's devices; else, as for FUSE, the source
    of its mount. None for a filesystem on no block device (NFS, CIFS)."""
    device = os.fstat(fd).st_dev
    if not _major_minor(device).startswith("0:"):
        return {device}
    fsid = _btrfs_fsid(fd)
    if fsid is not None:
        return btrfs_devices(fsid)
    source = mount_source(fd)
    if source and source.startswith("/dev/"):
        try:
            return {os.stat(source).st_rdev}
        except OSError:
            return set()
    return set()


def disks_of(fd: int) -> set[str]:
    """The whole disks under the filesystem of FD."""
    return whole_disks(_filesystem_devices(fd))


def system_disks() -> set[str]:
    """The whole disks of the root filesystem (the filesystem of MOUNTS_BASE, a bind of the host's
    /mnt) and of the data volume DATA_DIR."""
    found: set[str] = set()
    for path in (MOUNTS_BASE, DATA_DIR):
        try:
            fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        except OSError:
            continue
        try:
            found |= disks_of(fd)
        finally:
            os.close(fd)
    return found


def probe_store(endpoint: str) -> Optional[str]:
    """Why the S3 store at ENDPOINT cannot be reached, or None: a TCP connection to its host and port
    and, for https, a TLS handshake that verifies its certificate with the system's roots, as restic
    does, within PROBE_TIMEOUT. Nothing is sent but the handshake: no request, no key. Blocking."""
    parts = urlsplit(endpoint)
    host = parts.hostname or ""
    port = parts.port or (443 if parts.scheme == "https" else 80)
    deadline = time.monotonic() + PROBE_TIMEOUT
    try:
        with socket.create_connection((host, port), timeout=PROBE_TIMEOUT) as connection:
            if parts.scheme == "https":
                connection.settimeout(max(deadline - time.monotonic(), 0.01))
                with ssl.create_default_context().wrap_socket(connection, server_hostname=host):
                    pass
    except ssl.SSLCertVerificationError as e:
        return f"The S3 store at {endpoint} has a certificate that restic refuses: {e.verify_message or e}"
    except TimeoutError:
        return f"The S3 store does not answer at {endpoint}: no answer within {PROBE_TIMEOUT:g} s"
    except ssl.SSLError as e:
        return f"The S3 store does not answer at {endpoint}: its TLS handshake failed: {e.reason or e}"
    except OSError as e:
        return f"The S3 store does not answer at {endpoint}: {e.strerror or e}"
    return None


async def _unreachable(settings: OffsiteSettings) -> Optional[str]:
    """For an S3 store, why it cannot be reached (probe_store); None when it answers, and for a disk."""
    if settings.kind != Kind.S3:
        return None
    try:
        # The name's lookup is not bounded by the socket's timeout.
        return await asyncio.wait_for(asyncio.to_thread(probe_store, settings.endpoint or ""), PROBE_TIMEOUT + 5)
    except asyncio.TimeoutError:
        return f"The S3 store does not answer at {settings.endpoint}: no answer within {PROBE_TIMEOUT:g} s"


def _close(fds: list[int]) -> None:
    """Close FDS, the folders held open while restic ran."""
    while fds:
        try:
            os.close(fds.pop())
        except OSError:
            pass


def _system_disk_problem(fd: int, path: str) -> Optional[str]:
    """Why PATH, open at FD, cannot hold the copy as a folder on the system disk (on_system_disk), or
    None. When the disks cannot be found it cannot either, and the reason is logged too."""
    try:
        return _on_system_disk_problem(path) if on_system_disk(fd) else None
    except UnknownDisk as e:
        problem = (f"UNOS cannot tell which disk holds {path} ({e}), so it cannot make sure that the copy "
                   f"leaves the system disk: choose a folder on a disk mounted below {MOUNTS_BASE}")
        logger.warning("%s", problem)
        return problem


def _pool_of(path: Optional[str]) -> Optional[str]:
    """The pool that PATH lies in, or None for a path outside the pools' base."""
    base = str(pools_base())
    if not path or not _inside(path, base) or path == base:
        return None
    return path[len(base) + 1:].split("/")[0] or None


def _paths_used(settings: OffsiteSettings, *, sources: bool) -> list[str]:
    """The folder of a disk target and, with SOURCES, the folders it copies."""
    found = [settings.path] if settings.kind == Kind.DISK and settings.path else []
    return found + (list(settings.sources) if sources else [])


def _use(activity: Activity, paths: list[str]) -> None:
    """Name PATHS and their pools as what ACTIVITY reads or writes (pool_user, volume_user)."""
    for path in paths:
        activity.paths.add(path)
        pool = _pool_of(path)
        if pool:
            activity.pools.add(pool)


def _bad(detail: str) -> HTTPException:
    return HTTPException(400, detail)


def _parts(path: object, what: str) -> list[str]:
    """The components of an absolute PATH without '..', .snapshots and .unos-trash; 400 otherwise."""
    if not isinstance(path, str) or not path or not path.isprintable() or len(path) > MAX_PATH_LENGTH:
        raise _bad(f"{what} must be a path of printable characters")
    if not path.startswith("/"):
        raise _bad(f"{what} must be an absolute path")
    parts = [p for p in path.split("/") if p not in ("", ".")]
    if ".." in parts:
        raise _bad(f"{what} must not contain '..'")
    if SNAPSHOTS in parts or TRASH in parts:
        raise _bad(f"{what} must not be inside a {SNAPSHOTS} or {TRASH} folder")
    return parts


def _base_parts() -> list[str]:
    return [p for p in str(MOUNTS_BASE).split("/") if p]


def _target_path(path: object) -> str:
    """The folder of a disk target, normalized; 400 for one that is not strictly below MOUNTS_BASE."""
    what = "The folder of the copy"
    parts = _parts(path, what)
    if _PATTERN_CHARACTERS.search(path):
        raise _bad(f"{what} must not contain *, ?, [ or \\")
    base = _base_parts()
    if parts[: len(base)] != base or len(parts) <= len(base):
        raise _bad(f"{what} must be below {MOUNTS_BASE}, on a disk mounted there")
    return "/" + "/".join(parts)


def _open_problem(e: OSError, path: str, what: str) -> str:
    if e.errno == errno.ENOENT:
        return f"{path} does not exist"
    if e.errno == errno.ELOOP:
        return f"{what} must not contain symbolic links ({path} has one)"
    if e.errno == errno.ENOTDIR:
        return f"{path} is not a folder"
    return f"{path} cannot be opened: {e.strerror or e}"


def _on_system_disk_problem(path: str) -> str:
    return (f"{path} is on the system disk (the filesystem of {MOUNTS_BASE} or of the data volume): a copy there "
            f"does not leave the device, and a disk that is not mounted leaves only its empty mount point. Mount "
            f"the disk below {MOUNTS_BASE} and choose a folder on it")


def _open_target(path: str, *, create: bool) -> tuple[int, Optional[str]]:
    """Open the folder of a disk target without following symbolic links, making it (mode 0700) when
    CREATE and its parent is not on the system disk: the open folder, which the caller closes, and its
    path when it was made here. 400 for a folder that cannot hold the copy or cannot be made (a run
    maps it to a failed step)."""
    what = "The folder of the copy"
    relative = path.split("/")[len(_base_parts()) + 1:]
    try:
        parent = open_dir_nofollow(MOUNTS_BASE, relative[:-1])
    except OSError as e:
        raise _bad(_open_problem(e, os.path.dirname(path), what)) from None
    created = None
    try:
        try:
            fd = open_subdirs_nofollow(parent, relative[-1:])
        except FileNotFoundError:
            if not create:
                raise _bad(f"{path} does not exist: is its disk mounted?") from None
            problem = _system_disk_problem(parent, path)
            if problem:
                raise _bad(problem) from None
            try:
                fd = open_subdirs_nofollow(parent, relative[-1:], create=True, mode=0o700)
            except OSError as e:
                raise _bad(f"{path} could not be made: {e.strerror or e}") from None
            created = path
        except OSError as e:
            raise _bad(_open_problem(e, path, what)) from None
        try:
            problem = _system_disk_problem(fd, path)
            if problem:
                raise _bad(problem)
        except BaseException:
            os.close(fd)
            if created is not None:
                _remove_empty(parent, relative[-1])
            raise
        return fd, created
    finally:
        os.close(parent)


def _open_source(path: str) -> tuple[str, int]:
    """A folder to copy: inside the pools' base, opened without following symbolic links, on a mounted
    pool (not on the filesystem of MOUNTS_BASE, where a pool that is not mounted leaves its empty mount
    point). Its normalized path and the open folder, which the caller closes; 400 otherwise."""
    what = "A folder to copy"
    normalized = validate_pools_path(path, what)
    _parts(normalized, what)
    base = pools_base()
    try:
        fd = open_dir_nofollow(base, Path(normalized).relative_to(base).parts)
    except OSError as e:
        raise _bad(_open_problem(e, normalized, what)) from None
    try:
        if on_system_filesystem(fd):
            raise _bad(f"{normalized} is not on a mounted pool: it is on the system disk")
    except BaseException:
        os.close(fd)
        raise
    return normalized, fd


def _same_disk(a: int, b: int) -> bool:
    """Whether the folders open at A and B are on one disk: their whole disks meet, or, when the disks
    of one are not known (a network filesystem), they are on one filesystem."""
    disks_a, disks_b = disks_of(a), disks_of(b)
    if disks_a and disks_b:
        return bool(disks_a & disks_b)
    return os.fstat(a).st_dev == os.fstat(b).st_dev


def _inside(path: str, folder: str) -> bool:
    return path == folder or path.startswith(folder.rstrip("/") + "/")


def _remove_if_empty(path: str) -> None:
    try:
        os.rmdir(path)
    except OSError:
        pass


def _remove_empty(parent: int, name: str) -> None:
    """Remove the folder NAME of the folder open at PARENT when it is empty."""
    try:
        os.rmdir(name, dir_fd=parent)
    except OSError:
        pass


def _holds_files(path: str) -> bool:
    relative = path.split("/")[len(_base_parts()) + 1:]
    fd = open_dir_nofollow(MOUNTS_BASE, relative)
    try:
        return bool(os.listdir(fd))
    finally:
        os.close(fd)


# --- the settings ---------------------------------------------------------------------------------


def _number(value: object, name: str, low: int, high: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        raise _bad(f"{name} must be a whole number from {low} to {high}")
    return value


def _endpoint(value: object) -> str:
    if not isinstance(value, str) or not value or not value.isprintable() or len(value) > 512:
        raise _bad("Enter the endpoint of the S3 store, such as https://s3.eu-central-1.amazonaws.com")
    parts = urlsplit(value)
    if parts.scheme not in ("https", "http"):
        raise _bad("The endpoint of the S3 store must start with https:// or http://")
    if parts.username or parts.password or parts.query or parts.fragment or parts.path not in ("", "/"):
        raise _bad("The endpoint of the S3 store must hold only the scheme, the host and the port")
    host = parts.hostname or ""
    try:
        port = parts.port
    except ValueError:
        raise _bad("The port of the endpoint of the S3 store is not a port number") from None
    if ":" in host:
        try:
            ipaddress.IPv6Address(host)
        except ValueError:
            raise _bad(f"The host of the endpoint, {host}, is not a host name or an IP address") from None
        host = f"[{host}]"
    elif not _HOST_NAME.fullmatch(host):
        raise _bad(f"The host of the endpoint, {host!r}, is not a host name or an IP address")
    return f"{parts.scheme}://{host}" + (f":{port}" if port else "")


def _password(value: str) -> str:
    if len(value) < MIN_PASSWORD_LENGTH:
        raise _bad(f"The password of the copy repository must have at least {MIN_PASSWORD_LENGTH} characters")
    if len(value) > MAX_PASSWORD_LENGTH or "\x00" in value:
        raise _bad(f"The password of the copy repository must have at most {MAX_PASSWORD_LENGTH} characters, "
                   "without NUL")
    return value


def _normalized(request: SettingsRequest) -> OffsiteSettings:
    """The settings of REQUEST, checked as far as that needs no filesystem; 400 otherwise."""
    numbers = {
        "limit_upload_kib": _number(request.limit_upload_kib, "limit_upload_kib", 0, MAX_LIMIT_KIB),
        "limit_download_kib": _number(request.limit_download_kib, "limit_download_kib", 0, MAX_LIMIT_KIB),
        "keep_daily": _number(request.keep_daily, "keep_daily", 0, MAX_KEEP),
        "keep_weekly": _number(request.keep_weekly, "keep_weekly", 0, MAX_KEEP),
        "keep_monthly": _number(request.keep_monthly, "keep_monthly", 0, MAX_KEEP),
        "check_percent": _number(request.check_percent, "check_percent", 0, 100),
    }
    if not (numbers["keep_daily"] or numbers["keep_weekly"] or numbers["keep_monthly"]):
        raise _bad("The retention must keep at least one snapshot: keep_daily, keep_weekly or keep_monthly above 0")
    if not request.sources:
        raise _bad("Choose at least one folder to copy")
    if len(request.sources) > MAX_SOURCES:
        raise _bad(f"Choose at most {MAX_SOURCES} folders to copy")
    if request.kind == Kind.DISK:
        return OffsiteSettings(kind=Kind.DISK, path=_target_path(request.path), sources=list(request.sources),
                               **numbers)
    bucket = request.bucket or ""
    if not _BUCKET.fullmatch(bucket):
        raise _bad("The bucket must have 3 to 63 letters, digits, '.', '_' or '-', starting and ending with a "
                   "letter or digit")
    prefix = None
    if request.prefix:
        parts = [p for p in request.prefix.split("/") if p]
        if not parts or len(request.prefix) > 512 or any(not _PREFIX_PART.fullmatch(p) or p in (".", "..")
                                                         for p in parts):
            raise _bad("The prefix must be folder names of letters, digits, '.', '_' or '-', joined by '/'")
        prefix = "/".join(parts)
    region = request.region or None
    if region is not None and not _REGION.fullmatch(region):
        raise _bad("The region must have letters, digits and '-' only, such as eu-central-1")
    if not _KEY.fullmatch(request.access_key_id or ""):
        raise _bad("Enter the access key ID of the S3 store: printable characters without spaces")
    return OffsiteSettings(kind=Kind.S3, endpoint=_endpoint(request.endpoint), bucket=bucket, prefix=prefix,
                           region=region, access_key_id=request.access_key_id, sources=list(request.sources),
                           **numbers)


def location(settings: OffsiteSettings) -> str:
    """The repository as restic names it."""
    if settings.kind == Kind.DISK:
        return f"local:{settings.path}"
    return f"s3:{settings.endpoint}/{settings.bucket}" + (f"/{settings.prefix}" if settings.prefix else "")


def _secrets_for(request: SettingsRequest, settings: OffsiteSettings, stored: Optional[OffsiteSettings],
                 kept: Secrets) -> Secrets:
    """The secrets of REQUEST, or the stored ones that an edit of the same target keeps; 400 otherwise."""
    if request.password:
        password = _password(request.password)
    elif stored is not None and kept.password and location(stored) == location(settings):
        password = kept.password
    else:
        raise _bad("Enter the password of the copy repository")
    secret = None
    if settings.kind == Kind.S3:
        if request.s3_secret_access_key:
            if not _KEY.fullmatch(request.s3_secret_access_key):
                raise _bad("The secret access key must have printable characters without spaces")
            secret = request.s3_secret_access_key
        elif (stored is not None and stored.kind == Kind.S3 and kept.s3_secret_access_key
              and (stored.endpoint, stored.access_key_id) == (settings.endpoint, settings.access_key_id)):
            secret = kept.s3_secret_access_key
        else:
            raise _bad("Enter the secret access key of the S3 store")
    return Secrets(password=password, s3_secret_access_key=secret)


def _prepare_folders(settings: OffsiteSettings) -> tuple[Optional[str], list[int]]:
    """Check the sources and a disk target on the filesystem, normalize the sources, and make the
    target's folder when it is missing: its path then, and the target held open for restic (_ready),
    which the caller closes. 400 for what cannot be copied or hold the copy."""
    sources = []
    for path in settings.sources:
        normalized, fd = _open_source(path)
        os.close(fd)
        if normalized not in sources:
            sources.append(normalized)
    settings.sources = sources
    if settings.kind != Kind.DISK:
        return None, []
    if any(_inside(source, settings.path) for source in sources):
        raise _bad(f"{settings.path} holds a folder that it copies: choose a folder outside them, or one inside "
                   "a folder that it copies")
    fd, created = _open_target(settings.path, create=True)
    return created, [fd]


def _warnings(settings: OffsiteSettings) -> list[dict]:
    """same_disk: a disk target on a disk of a folder that it copies."""
    if settings.kind != Kind.DISK:
        return []
    target: Optional[int] = None
    sources: list[int] = []
    try:
        target = _open_target(settings.path, create=False)[0]
        for source in settings.sources:
            sources.append(_open_source(source)[1])
        same = any(_same_disk(target, source) for source in sources)
    except HTTPException:
        return []
    finally:
        _close(sources + ([target] if target is not None else []))
    return [{"kind": "same_disk", "message": SAME_DISK}] if same else []


def _managed_pools() -> list[dict]:
    """The mount points of the managed pools, the folders that a copy takes by default."""
    try:
        names = storage_pools.managed_pool_names()
    except Exception as e:  # the host's fstab cannot be read: no defaults
        logger.warning("The managed pools could not be read for the off-device copy: %s", e)
        return []
    base = pools_base()
    return [{"name": name, "path": str(base / name)} for name in sorted(names)]


async def settings_answer() -> dict:
    """GET /offsite/settings: the settings without secrets, whether they are stored, the managed pools,
    the pools' base (the page names a source's pool by it) and the warnings."""
    stored, secrets = _stored()
    pools = await asyncio.to_thread(_managed_pools)
    warnings = await asyncio.to_thread(_warnings, stored) if stored is not None else []
    return {
        "configured": stored is not None,
        "settings": stored.model_dump(mode="json") if stored is not None else None,
        "has_password": bool(secrets.password),
        "has_s3_secret_access_key": bool(secrets.s3_secret_access_key),
        "pools": pools,
        "pools_base": str(pools_base()),
        "warnings": warnings,
    }


async def _open_or_create(repo: _Repository, created: Optional[str]) -> None:
    """Open the repository with its password, or make it when none is there; 400 with restic's message,
    or when restic does not end within SAVE_TIMEOUT."""
    try:
        await _restic(repo, ["cat", "config"], step="settings", timeout=SAVE_TIMEOUT)
        return
    except _Failed as e:
        failure = e
    if failure.timed_out:
        raise _bad(f"The store did not answer within {SAVE_TIMEOUT:g} s")
    where = location(repo.settings)
    if failure.code == WRONG_PASSWORD:
        raise _bad(f"The password does not open the copy repository at {where}: {failure.message}")
    if failure.code != REPOSITORY_MISSING:
        raise _bad(f"The copy repository at {where} cannot be reached: {failure.message}")
    if repo.settings.kind == Kind.DISK and created is None and await asyncio.to_thread(_holds_files, repo.settings.path):
        raise _bad(f"{repo.settings.path} holds files but no copy repository: choose an empty folder, or the folder "
                   "of an existing copy")
    try:
        await _restic(repo, ["init"], step="settings", timeout=SAVE_TIMEOUT)
    except _Failed as e:
        if e.timed_out:
            raise _bad(f"The store did not answer within {SAVE_TIMEOUT:g} s") from None
        raise _bad(f"The copy repository could not be made at {where}: {e.message}") from None


def _store(settings: OffsiteSettings, secrets: Secrets) -> None:
    """The secrets first, then the settings, as backup tasks write their keys first (ADR-0325)."""
    write_secrets(secrets)
    write_settings(settings)


async def save_settings(request: SettingsRequest) -> dict:
    """PUT /offsite/settings: check the settings, test the target with restic, then store them."""
    activity = _begin_or_409("save")
    try:
        stored, kept = _stored()
        settings = _normalized(request)
        secrets = _secrets_for(request, settings, stored, kept)
        problem = await _unreachable(settings)
        if problem:
            raise _bad(problem)
        _use(activity, _paths_used(settings, sources=False))
        created, held = await asyncio.to_thread(_prepare_folders, settings)
        repo = _Repository(settings, secrets.password, secrets.s3_secret_access_key)
        try:
            await _open_or_create(repo, created)
        except BaseException:
            if created is not None:
                await asyncio.to_thread(_remove_if_empty, created)
            raise
        finally:
            _close(held)
        await asyncio.to_thread(_store, settings, secrets)
        logger.info("The off-device copy goes to %s", location(settings))
    finally:
        _end(activity)
    return await settings_answer()


def _remove_files() -> None:
    """The settings, the secrets and the state, and the copies of them kept as .bad (ADR-0324): a
    damaged secrets file holds the password too."""
    for name in (SETTINGS_FILE, SECRETS_FILE, STATUS_FILE):
        path = Path(name)
        path.unlink(missing_ok=True)
        state_files.bad_path(path).unlink(missing_ok=True)
        for staged in path.parent.glob(f".{path.name}.*.tmp"):  # of a write that a stop interrupted
            staged.unlink(missing_ok=True)
    _sync_folder(Path(SECRETS_FILE).parent)


async def remove_settings() -> dict:
    """DELETE /offsite/settings: remove the settings, the secrets and the state. The copies stay where
    they are."""
    activity = _begin_or_409("remove")
    try:
        await asyncio.to_thread(_remove_files)
    finally:
        _end(activity)
    return {"status": "deleted"}


# --- restic ---------------------------------------------------------------------------------------


class _Failed(Exception):
    """A step failed: STEP, what went wrong, and restic's exit status when it ran. PREFIX replaces the
    step's text of STEP_TEXTS in the message of the run."""

    def __init__(self, step: str, message: str, code: Optional[int] = None, *, prefix: Optional[str] = None,
                 timed_out: bool = False) -> None:
        super().__init__(message)
        self.step = step
        self.message = message
        self.code = code
        self.prefix = prefix
        self.timed_out = timed_out  # restic did not end within its timeout and was killed


@dataclass
class _Repository:
    """The settings and the secrets that restic gets: its environment and its options."""

    settings: OffsiteSettings
    password: str = field(repr=False)
    secret_key: Optional[str] = field(default=None, repr=False)

    def env(self) -> dict[str, str]:
        """restic's environment, built for it: never the supervisor's own, which holds UNOS_JWT_SECRET."""
        temporary = tempfile.gettempdir()
        env = {
            "PATH": os.environ.get("PATH", os.defpath),
            "HOME": temporary,
            "TMPDIR": temporary,
            "RESTIC_REPOSITORY": location(self.settings),
            "RESTIC_PASSWORD": self.password,
        }
        if self.settings.kind == Kind.S3:
            env["AWS_ACCESS_KEY_ID"] = self.settings.access_key_id or ""
            env["AWS_SECRET_ACCESS_KEY"] = self.secret_key or ""
            if self.settings.region:
                env["AWS_DEFAULT_REGION"] = self.settings.region
        return env

    def options(self, *, cache: bool = True) -> list[str]:
        """restic's global options: for s3 a cache in TMPDIR (the tmpfs, rebuilt after a restart) and the
        limits; no cache for a disk, which is read as fast as a cache, nor without CACHE (the check,
        which reads every file from the store)."""
        if self.settings.kind == Kind.DISK:
            return ["--no-cache"]
        options = ["--cache-dir", os.path.join(tempfile.gettempdir(), "restic-cache")] if cache else ["--no-cache"]
        if self.settings.limit_upload_kib:
            options += ["--limit-upload", str(self.settings.limit_upload_kib)]
        if self.settings.limit_download_kib:
            options += ["--limit-download", str(self.settings.limit_download_kib)]
        return options

    def redact(self, text: str) -> str:
        for secret in (self.password, self.secret_key):
            if secret:
                text = text.replace(secret, "<redacted>")
        return text


def _duration(seconds: float) -> str:
    for size, unit in ((_DAY, "day"), (_HOUR, "hour"), (_MINUTE, "minute"), (1, "second")):
        if seconds >= size and seconds % size == 0:
            count = int(seconds // size)
            return f"{count} {unit}{'' if count == 1 else 's'}"
    return f"{seconds:g} seconds"


def _message(text: str) -> str:
    """restic's standard error as one line: its end, where restic says why it failed."""
    return " ".join(text.split())[-1500:] or "no message"


async def _restic(repo: _Repository, args: list[str], *, step: str, stdin: Optional[bytes] = None,
                  stdout_path: Optional[Path] = None, partial: bool = False,
                  on_error_line: Optional[Callable[[bytes], None]] = None,
                  timeout: Optional[float] = None, cache: bool = True) -> restic.Result:
    """Run the restic command ARGS on REPO within TIMEOUT, by default its TIMEOUTS; _Failed(STEP) when
    it cannot run, times out or fails. With PARTIAL, exit status 3 (a backup that saved its snapshot
    without some files) is no failure: the caller judges it by the errors that ON_ERROR_LINE gets."""
    name = args[0]
    timeout = timeout or TIMEOUTS[name]
    waits = ["--retry-lock", RETRY_LOCK] if name in WAIT_FOR_LOCKS else []
    try:
        result = await restic.run([*repo.options(cache=cache), *waits, *args], repo.env(), timeout=timeout, stdin=stdin,
                                  stdout_path=stdout_path, on_error_line=on_error_line)
    except restic.Timeout:
        raise _Failed(step, f"restic {name} did not end within {_duration(timeout)}: it was stopped",
                      timed_out=True) from None
    except (OSError, ValueError) as e:  # ValueError: a NUL in its environment, from a file edited by hand
        raise _Failed(step, f"restic could not run: {repo.redact(str(e))}") from None
    if result.code != 0 and not (partial and result.code == PARTIAL):
        raise _Failed(step, f"restic {name} ended with exit status {result.code}: "
                            f"{_message(repo.redact(result.stderr))}", result.code)
    return result


def _ready(stored: Optional[OffsiteSettings], secrets: Secrets, *,
           sources: bool) -> tuple[_Repository, list[int]]:
    """The repository of the stored settings, with its target checked (and the sources) and held open
    while restic runs, which the caller closes (_close): a folder held open keeps its filesystem busy,
    so that a plain unmount of its disk fails, and restic's local back end, which makes the folders
    that it misses, never writes onto the empty mount point, on the system disk. _Failed with step
    settings when something is not stored, folders when a folder cannot be used. Blocking."""
    if stored is None:
        raise _Failed("settings", NOT_SET_UP)
    if not secrets.password:
        raise _Failed("settings", NO_PASSWORD)
    if stored.kind == Kind.S3 and not secrets.s3_secret_access_key:
        raise _Failed("settings", NO_SECRET_KEY)
    held: list[int] = []
    try:
        if stored.kind == Kind.DISK:
            held.append(_open_target(_target_path(stored.path), create=False)[0])
        if sources:
            for path in stored.sources:
                held.append(_open_source(path)[1])
    except HTTPException as e:
        _close(held)
        raise _Failed("folders", e.detail) from None
    except BaseException:
        _close(held)
        raise
    return _Repository(stored, secrets.password, secrets.s3_secret_access_key), held


async def _repository(activity: Optional[Activity] = None) -> tuple[_Repository, list[int]]:
    """The repository for the snapshots and the restores, with the target held open (_ready), which
    the caller closes: 409 when nothing is set up, 503 when the target cannot be used. ACTIVITY, the
    restore, names the target's pool."""
    stored, secrets = _stored()
    if activity is not None and stored is not None:
        _use(activity, _paths_used(stored, sources=False))
    try:
        return await asyncio.to_thread(_ready, stored, secrets, sources=False)
    except _Failed as e:
        raise HTTPException(409 if e.step == "settings" else 503, e.message) from None


# --- a copy ---------------------------------------------------------------------------------------


def _summary(result: restic.Result, step: str) -> dict:
    """The summary that `restic backup --json` prints last."""
    for line in reversed(result.stdout.decode("utf-8", "replace").splitlines()):
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if isinstance(message, dict) and message.get("message_type") == "summary" and message.get("snapshot_id"):
            return message
    raise _Failed(step, "restic backup named no snapshot that it made")


def _excludes(settings: OffsiteSettings) -> list[str]:
    """--exclude for the snapshots and the trash at the top of the pools and their volumes, and for a
    disk target that lies inside a folder that is copied."""
    base = str(pools_base())
    patterns = [f"{base}/*/{SNAPSHOTS}", f"{base}/*/{TRASH}", f"{base}/*/*/{SNAPSHOTS}", f"{base}/*/*/{TRASH}"]
    if settings.kind == Kind.DISK and any(_inside(settings.path, source) for source in settings.sources):
        patterns.append(settings.path)
    return [arg for pattern in patterns for arg in ("--exclude", pattern)]


def _size(count: int) -> str:
    value = float(count)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TiB"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


async def _restore_test(repo: _Repository, snapshot_id: str, digest: str) -> None:
    """Read the configuration archive of SNAPSHOT_ID back into TMPDIR, compare it with the one sent and
    check it as a restore checks an archive. Nothing is restored."""
    with tempfile.TemporaryDirectory(prefix="unos-offsite-") as folder:
        archive = Path(folder) / CONFIGURATION_NAME
        await _restic(repo, ["dump", snapshot_id, "/" + CONFIGURATION_NAME], step="restore_test", stdout_path=archive)
        if await asyncio.to_thread(_sha256, archive) != digest:
            raise _Failed("restore_test", "The archive read back from the copy differs from the one sent")
        try:
            await asyncio.to_thread(check_archive, archive)
        except BackupValidationError as e:
            raise _Failed("restore_test", f"The archive read back from the copy is no backup that a restore "
                                          f"takes: {e}") from None


def _files(count: int) -> str:
    return f"{count} file{'' if count == 1 else 's'}"


def _listed(items: list[str], count: int) -> str:
    """ITEMS, the first of COUNT, and how many more there are."""
    more = count - len(items)
    return ", ".join(items) + (f", and {more} more" if more > 0 else "")


class _Unread:
    """The files that the copy of the data could not read, from the errors that ``restic backup
    --json`` prints on its standard error (message_type error, with the file as item and why): those
    that vanished while it ran (no such file or directory: deleted or moved meanwhile), which are no
    failure, and the others (permission denied, an I/O error), which fail the copy once every step
    ran. restic exits with status 3 for both and saves the snapshot without them. Counted, with the
    first NAMED_FILES of each named."""

    def __init__(self) -> None:
        self.vanished: list[str] = []
        self.vanished_count = 0
        self.unreadable: list[str] = []
        self.unreadable_count = 0
        # Exit status 3 without an error that names a file: why, from the end of standard error.
        self.unnamed: Optional[str] = None
        self.redact: Callable[[str], str] = lambda text: text

    def add(self, line: bytes) -> None:
        """One line of restic's standard error."""
        try:
            message = json.loads(line)
        except ValueError:
            return
        if not isinstance(message, dict) or message.get("message_type") != "error":
            return
        if message.get("during") == "scan":  # the scanner only counts: the archiver reports the file again
            return
        error = message.get("error")
        text = error.get("message") if isinstance(error, dict) else error
        text = self.redact(text) if isinstance(text, str) and text else "no reason given"
        item = self.redact(message["item"]) if isinstance(message.get("item"), str) else ""
        reason = text.split(f"{item}: ", 1)[1] if item and f"{item}: " in text else text
        if reason.lower().endswith("no such file or directory"):
            self.vanished_count += 1
            if len(self.vanished) < NAMED_FILES:
                self.vanished.append(item or text)
        else:
            self.unreadable_count += 1
            if len(self.unreadable) < NAMED_FILES:
                self.unreadable.append(f"{item} ({reason})" if item else reason)

    def problem(self) -> Optional[str]:
        """Why the copy fails: the files that could not be read; None when every file was read or vanished."""
        if self.unreadable_count:
            return (f"{_files(self.unreadable_count)} could not be read: "
                    f"{_listed(self.unreadable, self.unreadable_count)}")
        return self.unnamed

    def note(self, error: Optional[str]) -> Optional[str]:
        """The files that vanished, and the problem unless it is ERROR, the error of the copy."""
        parts = []
        if self.vanished_count:
            parts.append(f"{_files(self.vanished_count)} vanished during the copy: "
                         f"{_listed(self.vanished, self.vanished_count)}")
        problem = self.problem()
        if problem and problem != error:
            parts.append(problem)
        return "; ".join(parts) or None


async def _copy(activity: Activity, result: CopyResult, unread: _Unread) -> str:
    try:
        stored, secrets = load_settings(), load_secrets()
    except OSError as e:
        raise _Failed("settings", f"The settings of the off-device copy cannot be read: {e}") from None
    if stored is not None:
        _use(activity, _paths_used(stored, sources=True))
    activity.step = "folders"
    repo, held = await asyncio.to_thread(_ready, stored, secrets, sources=True)
    try:
        problem = await _unreachable(repo.settings)
        if problem:
            raise _Failed("settings", problem)
        return await _steps(activity, result, repo, unread)
    finally:
        _close(held)


async def _steps(activity: Activity, result: CopyResult, repo: _Repository, unread: _Unread) -> str:
    """The steps of a copy, while its folders are held open."""
    settings = repo.settings
    unread.redact = repo.redact

    activity.step = "unlock"
    await _restic(repo, ["unlock"], step="unlock")

    activity.step = "configuration"
    try:
        archive = await asyncio.to_thread(configuration_archive)
    except Exception as e:
        raise _Failed("configuration", f"The configuration could not be collected: {e}") from None
    digest = hashlib.sha256(archive).hexdigest()
    configuration = _summary(await _restic(
        repo, ["backup", "--json", "--quiet", "--host", HOST, "--stdin", "--stdin-filename", CONFIGURATION_NAME,
               "--tag", CONFIGURATION_TAG], step="configuration", stdin=archive), "configuration")
    del archive  # up to MAX_ARCHIVE_BYTES of RAM, not for the days that the copy of the data can take

    activity.step = "data"
    backup = await _restic(
        repo, ["backup", "--json", "--quiet", "--host", HOST, "--tag", DATA_TAG, *_excludes(settings), "--",
               *settings.sources], step="data", partial=True, on_error_line=unread.add)
    data = _summary(backup, "data")
    if backup.code == PARTIAL and not (unread.vanished_count or unread.unreadable_count):
        unread.unnamed = ("restic could not read some files and named none of them: "
                          f"{_message(repo.redact(backup.stderr))}")
    result.summary = CopySummary(
        files_new=int(data.get("files_new") or 0),
        files_changed=int(data.get("files_changed") or 0),
        files_unmodified=int(data.get("files_unmodified") or 0),
        bytes_added=int(configuration.get("data_added") or 0) + int(data.get("data_added") or 0),
        configuration_snapshot=configuration["snapshot_id"],
        data_snapshot=data["snapshot_id"],
    )

    activity.step = "forget"
    await _restic(repo, ["forget", "--tag", CONFIGURATION_TAG, "--tag", DATA_TAG,
                         "--keep-daily", str(settings.keep_daily), "--keep-weekly", str(settings.keep_weekly),
                         "--keep-monthly", str(settings.keep_monthly), "--prune"], step="forget")

    activity.step = "check"
    result.check = "failed"
    subset = [f"--read-data-subset={settings.check_percent}%"] if settings.check_percent else []
    # Without a cache: every file it checks comes from the store, none from a warm cache, which would
    # let a damaged copy pass; nothing goes into TMPDIR.
    await _restic(repo, ["check", *subset], step="check", cache=False)
    result.check = "passed"

    activity.step = "restore_test"
    result.restore_test = "failed"
    await _restore_test(repo, configuration["snapshot_id"], digest)
    result.restore_test = "passed"

    problem = unread.problem()
    if problem:  # the snapshot is saved, without these files
        raise _Failed("data", problem, PARTIAL, prefix=INCOMPLETE)
    summary = result.summary
    checked = f"the check of {settings.check_percent}% of the data" if settings.check_percent else "the check"
    return (f"Copied {summary.files_new} new, {summary.files_changed} changed and {summary.files_unmodified} "
            f"unmodified files, {_size(summary.bytes_added)} added; configuration snapshot "
            f"{summary.configuration_snapshot[:8]}, data snapshot {summary.data_snapshot[:8]}; {checked} and the "
            "restore test passed")


def _finish_copy(result: CopyResult, unread: _Unread) -> None:
    result.ended = _utc_now().isoformat()
    result.note = unread.note(result.error)
    status = load_status()
    status.last_copy = result
    try:
        write_status(status)
    except OSError as e:
        logger.warning("Could not record the off-device copy in %s: %s", STATUS_FILE, e)


async def run_copy(trigger: str, activity: Optional[Activity] = None) -> tuple[bool, str]:
    """A copy to the copy repository: whether it succeeded, and what it copied or why it failed.

    TRIGGER is task (the Scheduler) or manual (Run now, which took the lock as ACTIVITY). A copy that
    finds a copy or a restore running ends at once, as a success that says so: a first copy of a large
    pool to a cloud store takes days, and the runs of its task in that time are no failures.
    """
    if activity is None:
        activity = _begin("copy")
        if activity is None:
            return True, f"Skipped: {_busy()}"
    result = CopyResult(started=activity.started.isoformat(), trigger=trigger)
    unread = _Unread()
    try:
        message = await _copy(activity, result, unread)
        result.success = True
    except _Failed as e:
        result.step, result.error = e.step, e.message
        if e.step in ("settings", "folders"):
            message = e.message
        else:
            message = f"{e.prefix or STEP_TEXTS[e.step]}: {e.message}"
    except asyncio.CancelledError:
        result.step, result.error = activity.step, STOPPED_DURING_COPY
        _finish_copy(result, unread)
        raise
    except Exception as e:  # recorded as failed, never left running
        logger.exception("The off-device copy failed")
        result.step, result.error = activity.step, f"The copy failed: {e}"
        message = result.error
    finally:
        _end(activity)
    _finish_copy(result, unread)
    if result.note:
        message = f"{message}. {result.note}"
    if result.success:
        logger.info("Off-device copy: %s", message)
    else:
        logger.warning("Off-device copy: %s", message)
    return result.success, message


async def start_copy() -> None:
    """POST /offsite/run: start a copy in the background; 409 when nothing is set up or something runs."""
    stored, _ = _stored()
    if stored is None:
        raise HTTPException(409, NOT_SET_UP)
    activity = _begin_or_409("copy")
    _keep(asyncio.create_task(run_copy("manual", activity)))


async def status_answer() -> dict:
    """GET /offsite/status: what runs, and the last copy and restore of files."""
    status = load_status()
    return {
        "running": _activity.answer() if _activity is not None else None,
        "last_copy": status.last_copy.model_dump() if status.last_copy else None,
        "last_restore": status.last_restore.model_dump() if status.last_restore else None,
    }


# --- the snapshots and the restores ---------------------------------------------------------------


def _snapshot_kind(tags: list[str]) -> str:
    if CONFIGURATION_TAG in tags:
        return "configuration"
    return "data" if DATA_TAG in tags else "other"


def _when(snapshot: dict) -> datetime:
    try:
        return datetime.fromisoformat(snapshot["time"]).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)


async def _snapshots(repo: _Repository) -> list[dict]:
    """The snapshots of the repository, newest first, as the API answers them."""
    try:
        # No lock: a listing beside a copy, or beside the lock of another device, neither waits nor fails.
        result = await _restic(repo, ["snapshots", "--json", "--no-lock"], step="snapshots")
    except _Failed as e:
        raise HTTPException(503, f"The copy repository cannot be read: {e.message}") from None
    if result.stdout_cut:
        raise HTTPException(503, "The copy repository holds more snapshots than can be listed")
    try:
        found = json.loads(result.stdout or b"[]")
    except ValueError:
        raise HTTPException(503, "restic listed the snapshots in a form that cannot be read") from None
    snapshots = []
    for item in found if isinstance(found, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        tags = [t for t in item.get("tags") or [] if isinstance(t, str)]
        summary = item.get("summary") if isinstance(item.get("summary"), dict) else {}
        snapshots.append({
            "id": item["id"],
            "short_id": item.get("short_id") or item["id"][:8],
            "time": item.get("time"),
            "kind": _snapshot_kind(tags),
            "tags": tags,
            "paths": [p for p in item.get("paths") or [] if isinstance(p, str)],
            "hostname": item.get("hostname"),
            "size": summary.get("total_bytes_processed"),
            "files": summary.get("total_files_processed"),
        })
    return sorted(snapshots, key=_when, reverse=True)


async def list_snapshots() -> dict:
    """GET /offsite/snapshots."""
    repo, held = await _repository()
    try:
        return {"snapshots": await _snapshots(repo)}
    finally:
        _close(held)


def _snapshot_id(value: str) -> str:
    if not isinstance(value, str) or not _SNAPSHOT_ID.fullmatch(value):
        raise _bad("A snapshot ID is 8 to 64 hexadecimal digits")
    return value


async def _find_snapshot(repo: _Repository, snapshot_id: str) -> dict:
    found = [s for s in await _snapshots(repo) if s["id"].startswith(snapshot_id)]
    if not found:
        raise HTTPException(404, f"The copy repository has no snapshot {snapshot_id}")
    if len(found) > 1:
        raise _bad(f"Several snapshots start with {snapshot_id}: give more of its ID")
    return found[0]


async def restore_configuration(snapshot_id: str) -> dict:
    """POST /offsite/restore-configuration: the configuration archive of a snapshot, checked as an upload
    is, into the backup list as offsite_<the snapshot's time in UTC>.tar.gz. It is restored there, by
    the restore of Backup & Restore with its checks."""
    snapshot_id = _snapshot_id(snapshot_id)
    activity = _begin_or_409("restore-configuration")
    activity.snapshot_id = snapshot_id
    held: list[int] = []
    try:
        repo, held = await _repository(activity)
        snapshot = await _find_snapshot(repo, snapshot_id)
        if snapshot["kind"] != "configuration":
            raise _bad(f"Snapshot {snapshot['short_id']} is not a configuration snapshot")
        with tempfile.TemporaryDirectory(prefix="unos-offsite-") as folder:
            archive = Path(folder) / CONFIGURATION_NAME
            activity.step = "dump"
            try:
                await _restic(repo, ["dump", snapshot["id"], "/" + CONFIGURATION_NAME], step="dump",
                              stdout_path=archive)
            except _Failed as e:
                raise HTTPException(503, f"The configuration could not be read from the copy: {e.message}") from None
            if archive.stat().st_size > MAX_ARCHIVE_BYTES:
                raise _bad("The configuration archive of the snapshot is larger than a backup may be")
            try:
                await asyncio.to_thread(check_archive, archive)
            except BackupValidationError as e:
                raise _bad(str(e)) from None
            when = _when(snapshot).replace(tzinfo=None)
            backup: BackupInfo = await asyncio.to_thread(store_backup, archive, "offsite", when)
    finally:
        _close(held)
        _end(activity)
    return {"status": "success", "backup": backup.model_dump()}


def _snapshot_path(value: Optional[str]) -> Optional[str]:
    """A folder in a snapshot, as an absolute path; None for all of it."""
    if value is None or value == "":
        return None
    parts = [p for p in value.split("/") if p not in ("", ".")] if isinstance(value, str) else None
    if (parts is None or not value.isprintable() or len(value) > MAX_PATH_LENGTH or not value.startswith("/")
            or ".." in parts):
        raise _bad("The folder to restore must be an absolute path of the snapshot, without '..'")
    return "/" + "/".join(parts) if parts else None


def _pool_root(pool: str) -> str:
    """The mount point of POOL; 400 when there is none or the pool is not mounted. Blocking."""
    base = pools_base()
    try:
        fd = open_dir_nofollow(base, [pool])
    except OSError:
        raise _bad(f"There is no pool {pool} at {base / pool}") from None
    try:
        if on_system_filesystem(fd):
            raise _bad(f"Pool {pool} is not mounted at {base / pool}")
    finally:
        os.close(fd)
    return str(base / pool)


def _make_folder(pool: str, name: str) -> tuple[str, int]:
    """Make the new folder NAME at the top of POOL (755) and open it: its path and the open folder,
    which the caller closes. 409 when it exists, 400 when it cannot be made. Blocking."""
    base = pools_base()
    path = base / pool / name
    try:
        parent = open_dir_nofollow(base, [pool])
    except OSError as e:
        raise _bad(_open_problem(e, str(base / pool), "The pool")) from None
    try:
        try:
            os.mkdir(name, 0o755, dir_fd=parent)
        except FileExistsError:
            raise HTTPException(409, f"{path} exists: a restore never writes into a folder that exists. Try "
                                     "again in a second") from None
        except OSError as e:
            raise _bad(f"{path} could not be made: {e.strerror or e}") from None
        try:
            return str(path), open_subdirs_nofollow(parent, [name])
        except OSError as e:
            _remove_empty(parent, name)
            raise _bad(f"{path} could not be opened: {e.strerror or e}") from None
    finally:
        os.close(parent)


def _remove_restore_folder(folder: str) -> None:
    """Remove the folder of a restore that failed when nothing came into it. Blocking."""
    path = Path(folder)
    try:
        parent = open_dir_nofollow(pools_base(), [path.parent.name])
    except OSError:
        return
    try:
        _remove_empty(parent, path.name)
    finally:
        os.close(parent)


def _finish_restore(result: RestoreResult) -> None:
    result.ended = _utc_now().isoformat()
    status = load_status()
    status.last_restore = result
    try:
        write_status(status)
    except OSError as e:
        logger.warning("Could not record the restore from the off-device copy in %s: %s", STATUS_FILE, e)


async def _restore_files(activity: Activity, repo: _Repository, snapshot_id: str, path: Optional[str],
                         folder: str, held: list[int]) -> None:
    """The restore into FOLDER, with FOLDER and the target held open (HELD) while restic writes. A
    failed restore removes FOLDER when nothing came into it; one that a stop of the supervisor ends
    keeps it, as its record says."""
    result = RestoreResult(snapshot_id=snapshot_id, path=path, folder=folder, started=activity.started.isoformat())
    activity.step = "restore"
    try:
        try:
            target = f"{snapshot_id}:{path}" if path else snapshot_id
            await _restic(repo, ["restore", target, "--target", folder, "--json", "--quiet"], step="restore")
            result.success = True
            logger.info("Restored snapshot %s of the off-device copy into %s", snapshot_id[:8], folder)
        except _Failed as e:
            result.error = e.message
            logger.warning("The restore of snapshot %s into %s failed: %s", snapshot_id[:8], folder, e.message)
        except Exception as e:  # recorded as failed, never left running
            logger.exception("The restore from the off-device copy failed")
            result.error = f"The restore failed: {e}"
        finally:
            _close(held)
        if not result.success:
            await asyncio.to_thread(_remove_restore_folder, folder)
    except asyncio.CancelledError:
        result.error = result.error or STOPPED_DURING_RESTORE
        _finish_restore(result)
        raise
    finally:
        _end(activity)
    _finish_restore(result)


async def restore_files(request: RestoreFilesRequest) -> dict:
    """POST /offsite/restore-files: restore a snapshot, or a folder of it, into a new folder
    restored-<UTC time> at the top of a pool, in the background. Nothing is overwritten."""
    snapshot_id = _snapshot_id(request.snapshot_id)
    pool = validate_pool_name(request.pool)
    path = _snapshot_path(request.path)
    activity = _begin_or_409("restore-files")
    activity.pools.add(pool)
    held: list[int] = []
    try:
        repo, held = await _repository(activity)
        await asyncio.to_thread(_pool_root, pool)
        snapshot = await _find_snapshot(repo, snapshot_id)
        folder, fd = await asyncio.to_thread(_make_folder, pool, f"restored-{_utc_now():%Y%m%d-%H%M%S}")
        held.append(fd)
        _use(activity, [folder])
    except BaseException:
        _close(held)
        _end(activity)
        raise
    activity.snapshot_id, activity.folder = snapshot["id"], folder
    _keep(asyncio.create_task(_restore_files(activity, repo, snapshot["id"], path, folder, held)))
    return {"status": "started", "folder": folder}
