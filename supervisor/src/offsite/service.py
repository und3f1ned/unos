"""The off-device copy: one copy repository per installation, the copies to it and the restores from it.

ADR-0337 decides it. restic keeps the copy repository, encrypted with a password that the
administrator types and that only this device keeps: a folder on a disk mounted below MOUNTS_BASE
that is not the system disk (disk), an S3-compatible store (s3), or, by ADR-0422, a restic REST
server over HTTPS (rest) or a folder of an SFTP server that the host's ssh reaches (sftp, ssh.py).

* The settings are in SETTINGS_FILE, without a secret. The repository's password, the S3 secret
  key, the REST server's password and the private key of UNOS's SSH key pair are in SECRETS_FILE,
  mode 0600, written as a new file renamed over the old one and synced with
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
import contextlib
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
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, UTC
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic.config import JsonDict, JsonValue

from .. import host, paths, state_files
from ..config import get_settings
from ..audit.changes import changed_fields
from ..backup.router import (
    MAX_ARCHIVE_BYTES,
    BackupInfo,
    BackupValidationError,
    check_archive,
    configuration_archive,
    limit_text,
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
from . import restic, ssh

logger = logging.getLogger(__name__)

SETTINGS_FILE = str(paths.OFFSITE_SETTINGS)
SECRETS_FILE = str(paths.OFFSITE_SECRETS)
STATUS_FILE = str(paths.OFFSITE_STATUS)
# The data directory (UNOS_DATA_DIR), and the folder below which the disks are mounted
# (UNOS_MOUNTS_BASE_PATH): the filesystems of both are the system disk's, where a disk target is
# refused (on_system_disk).
DATA_DIR = str(paths.DATA_DIR)
MOUNTS_BASE = get_settings().mounts_base_path
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
NO_REST_PASSWORD = ("The password of the REST server is not stored: save the settings of the off-device copy again "
                    "with it")
NO_KEY_PAIR = ("The key pair of UNOS for SFTP is not stored: make a new one, add its public key to the SFTP server "
               "and save the settings of the off-device copy again")
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
# A REST server's user (basic authentication: no ':'), a login on an SFTP server, and the parts of
# a folder there: words that neither the repository's URL nor restic's splitting of sftp.command
# (ssh.py) can take apart.
_REST_USER = re.compile(r"[\x21-\x39\x3b-\x7e]{1,128}")
_SSH_USER = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]{0,63}")
_REMOTE_PART = re.compile(r"[A-Za-z0-9_+-][A-Za-z0-9._+-]{0,254}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
MAX_REMOTE_PASSWORD_LENGTH = 1024
MAX_CERTIFICATE_LENGTH = 64 * 1024
# How a REST server's certificate is checked (rest): by the system's roots; by its own certificate,
# pinned by its SHA-256 fingerprint at a save; or by a CA certificate that the administrator gave.
TLS_MODES = ("system", "fingerprint", "ca")


# --- the models -----------------------------------------------------------------------------------


class Kind(str, Enum):
    DISK = "disk"
    S3 = "s3"
    REST = "rest"
    SFTP = "sftp"



_SOURCES_EXAMPLE: list[JsonValue] = ["/mnt/pools/data/documents", "/mnt/pools/data/photos"]
_RETENTION_EXAMPLE: JsonDict = {"keep_daily": 7, "keep_weekly": 4, "keep_monthly": 12, "check_percent": 2}


def _retention_text(period: str) -> str:
    return (f"How many {period} snapshots the retention keeps, 0 to {MAX_KEEP}; keep_daily, keep_weekly and "
            "keep_monthly may not all be 0.")


class OffsiteSettings(BaseModel):
    """The copy target and what is copied: SETTINGS_FILE, and the settings in the answers. No secret."""

    kind: Kind = Field(description=f"Where the copy repository is: disk, a folder on a disk mounted below {MOUNTS_BASE} "
                                   "that is not the system disk; s3, a bucket of an S3-compatible store; rest, a restic "
                                   "REST server over HTTPS; sftp, a folder of an SFTP server.")
    # disk: the folder of the repository, below MOUNTS_BASE; sftp: the folder on the server
    path: str | None = Field(default=None, description=f"disk: the folder of the copy repository, below {MOUNTS_BASE}, "
                                                  "made when it is missing; sftp: its folder on the server, absolute or "
                                                  "below the login's home folder; null for s3 and rest.")
    # s3: https://host[:port] or http://host[:port]; rest: https://host[:port]
    endpoint: str | None = Field(default=None, description="s3: the store's address, https:// or http://, its host and "
                                                      "an optional port; rest: the server's, https:// only; null "
                                                      "otherwise.")
    bucket: str | None = Field(default=None, description="s3: the bucket, 3 to 63 letters, digits, '.', '_' or '-'; "
                                                    "null otherwise.")
    prefix: str | None = Field(default=None, description="s3: the folders of the copy repository in the bucket, rest: on "
                                                    "the server, joined by /; null for the top.")
    region: str | None = Field(default=None, description="s3: the store's region, such as eu-central-1; null when it "
                                                    "needs none.")
    access_key_id: str | None = Field(default=None, description="s3: the access key ID; null otherwise.")
    # rest: the user of basic authentication; sftp: the login on the server
    username: str | None = Field(default=None, description="rest: the server's user; sftp: the login on the server; null "
                                                      "otherwise.")
    # sftp
    host: str | None = Field(default=None, description="sftp: the server's host name or IP address; null otherwise.")
    port: int | None = Field(default=None, description="sftp: the server's SSH port, 22 by default; null otherwise.")
    # the server's key, "<type> <base64>", pinned at the first test of the server
    host_key: str | None = Field(default=None, description="sftp: the server's host key that the first test pinned, "
                                                      "'<type> <base64>'; a request cannot set it. null otherwise.")
    # rest: system, fingerprint or ca
    tls: str | None = Field(default=None, description="rest: how the server's certificate is checked: system, by the "
                                                 "system's roots; fingerprint, by the server's own certificate that a "
                                                 "save pinned by certificate_sha256; ca, by the CA certificate of "
                                                 "certificate. null otherwise.")
    certificate: str | None = Field(default=None, description="rest: the certificate in PEM that restic trusts alone: the "
                                                         "CA's (tls ca, given) or the server's own (tls fingerprint, "
                                                         "read from the server at the save); null otherwise.")
    certificate_sha256: str | None = Field(default=None, description="rest, tls fingerprint: the SHA-256 fingerprint of "
                                                                "the server's certificate, 64 hexadecimal digits, "
                                                                "colons allowed in a request.")
    # folders of the pools
    sources: list[str] = Field(default=[], description=f"The folders of the pools that a copy takes, 1 to {MAX_SOURCES}, "
                                               "each on a mounted pool.")
    # KiB/s, 0: none; s3 only
    limit_upload_kib: int = Field(default=0, description=f"s3, rest and sftp: the most that restic sends, in KiB/s, 0 to "
                                                 f"{MAX_LIMIT_KIB}; 0: no limit.")
    limit_download_kib: int = Field(default=0, description=f"s3, rest and sftp: the most that restic reads, in KiB/s, 0 to "
                                                   f"{MAX_LIMIT_KIB}; 0: no limit.")
    keep_daily: int = Field(default=7, description=_retention_text("daily"))
    keep_weekly: int = Field(default=4, description=_retention_text("weekly"))
    keep_monthly: int = Field(default=12, description=_retention_text("monthly"))
    # the share of the data that each check reads, 0 to 100
    check_percent: int = Field(default=2, description="The share of the data that the check after each copy reads, 0 to "
                                              "100 percent; 0 checks the repository's structure only.")


class SettingsRequest(OffsiteSettings):
    """PUT /offsite/settings. The secrets are write-only: in no answer, no repr() and no settings file.
    Without them an edit of the same target keeps the stored ones."""

    model_config = ConfigDict(json_schema_extra={"examples": [
        {"kind": "disk", "path": "/mnt/pools/backup/unos-copy", "sources": _SOURCES_EXAMPLE,
         **_RETENTION_EXAMPLE, "password": "example-repository-password"},
        {"kind": "s3", "endpoint": "https://s3.example.com", "bucket": "nas-copies", "prefix": "unos",
         "region": "eu-central-1", "access_key_id": "EXAMPLEACCESSKEYID", "sources": _SOURCES_EXAMPLE,
         "limit_upload_kib": 2048, "limit_download_kib": 0, **_RETENTION_EXAMPLE,
         "password": "example-repository-password", "s3_secret_access_key": "example-secret-access-key"},
        {"kind": "rest", "endpoint": "https://nas2.example.com:8000", "prefix": "unos", "username": "unos",
         "tls": "fingerprint", "certificate_sha256": "3F:9A:0C:41:7E:22:B8:5D:90:6A:1F:C4:3B:E8:77:02:5C:9D:1A:E6:48:B0"
                                                     ":2F:73:C1:9E:05:6D:8A:B4:31:F7",
         "sources": _SOURCES_EXAMPLE, **_RETENTION_EXAMPLE, "password": "example-repository-password",
         "rest_password": "example-rest-password"},
        {"kind": "sftp", "host": "nas2.example.com", "port": 22, "username": "unos", "path": "/srv/copies/unos",
         "sources": _SOURCES_EXAMPLE, **_RETENTION_EXAMPLE, "password": "example-repository-password"},
    ]})

    password: str | None = Field(
        default=None, exclude=True, repr=False,
        description=f"The copy repository's password, {MIN_PASSWORD_LENGTH} to {MAX_PASSWORD_LENGTH} characters; "
                    "write-only. Without it, an edit of the same repository keeps the stored one.",
    )
    s3_secret_access_key: str | None = Field(
        default=None, exclude=True, repr=False,
        description="s3: the secret access key; write-only. Without it, an edit with the same endpoint and access "
                    "key ID keeps the stored one.",
    )
    rest_password: str | None = Field(
        default=None, exclude=True, repr=False,
        description="rest: the server's password; write-only. Without it, an edit with the same endpoint and user "
                    "keeps the stored one.",
    )
    trust_new_host_key: bool = Field(
        default=False, exclude=True,
        description="sftp: pin the host key that the server shows at this test, also when another one is pinned "
                    "for its host and port: for a server whose key changed on purpose.",
    )
    host_key_fingerprint: str | None = Field(
        default=None, exclude=True,
        description="sftp: the fingerprint that the server's host key must have, SHA256:<base64> as ssh-keygen -l "
                    "prints it; checked before restic makes anything. Optional.",
    )


class Secrets(BaseModel):
    """SECRETS_FILE: what restic needs to open the repository, and the private key of UNOS's SSH key
    pair (sftp)."""

    password: str | None = Field(default=None, repr=False)
    s3_secret_access_key: str | None = Field(default=None, repr=False)
    rest_password: str | None = Field(default=None, repr=False)
    sftp_private_key: str | None = Field(default=None, repr=False)


class CopySummary(BaseModel):
    """What a copy added."""

    files_new: int = Field(default=0, description="The files that the copy of the data found new.")
    files_changed: int = Field(default=0, description="The files that it found changed.")
    files_unmodified: int = Field(default=0, description="The files that it found unchanged.")
    # by both snapshots, before compression
    bytes_added: int = Field(default=0, description="The bytes that both snapshots added to the copy repository, before "
                                            "compression.")
    configuration_snapshot: str | None = Field(default=None, description="The ID of its configuration snapshot.")
    data_snapshot: str | None = Field(default=None, description="The ID of its data snapshot.")


class CopyResult(BaseModel):
    """The last copy, in STATUS_FILE. Times are ISO 8601 in UTC."""

    started: str = Field(description="When it started, in UTC with +00:00.")
    ended: str | None = Field(default=None, description="When it ended, in UTC with +00:00.")
    # task: a task of the Scheduler; manual: Run now on Backup & Restore
    trigger: str = Field(description="task: a task of the Scheduler; manual: Run now.")
    success: bool = Field(default=False, description="Whether it succeeded.")
    # the step that failed: settings, folders, unlock, configuration, data, ...
    step: str | None = Field(default=None, description="The step that failed: settings, folders, unlock, "
                                                  "configuration, data, forget, check or restore_test.")
    error: str | None = Field(default=None, description="Why it failed; null after a success.")
    summary: CopySummary | None = Field(default=None, description="What it added; null when it did not get that far.")
    # passed or failed; None when the run did not get there
    check: str | None = Field(default=None, description="The check of the copy repository: passed or failed; null when "
                                                   "the copy did not get there.")
    restore_test: str | None = Field(default=None, description="The restore test of the configuration: passed or "
                                                          "failed; null when the copy did not get there.")
    # What the copy of the data could not read that is not the error: the files that vanished while
    # restic read the folders (no failure), and those that could not be read when a later step failed.
    note: str | None = Field(default=None, description="The files that vanished while restic read them, and those it "
                                                  "could not read when a later step failed; null when none.")


class RestoreResult(BaseModel):
    """The last restore of files, in STATUS_FILE."""

    snapshot_id: str = Field(description="The full ID of the snapshot.")
    path: str | None = Field(
        default=None, description="The folder of the snapshot that it restored; null for all of it."
    )
    folder: str = Field(description="The new folder that it wrote, removed again when a failed restore wrote "
                                    "nothing into it.")
    started: str = Field(description="When it started, in UTC with +00:00.")
    ended: str | None = Field(default=None, description="When it ended, in UTC with +00:00.")
    success: bool = Field(default=False, description="Whether it succeeded.")
    error: str | None = Field(default=None, description="Why it failed; null after a success.")


class Status(BaseModel):
    last_copy: CopyResult | None = Field(default=None, description="The last copy; null before the first.")
    last_restore: RestoreResult | None = Field(default=None, description="The last restore of files; null before the "
                                                                    "first.")


_SNAPSHOT_ID_TEXT = "A snapshot's ID, or at least its first 8 hexadecimal digits"


class RestoreConfigurationRequest(BaseModel):
    """The configuration snapshot to put into the backup list."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"snapshot_id": "4f9a1c2e"}]})

    snapshot_id: str = Field(description=f"{_SNAPSHOT_ID_TEXT}, of a configuration snapshot.")


class RestoreFilesRequest(BaseModel):
    """The snapshot, or a folder of it, to restore into a new folder of a pool."""

    model_config = ConfigDict(json_schema_extra={"examples": [
        {"snapshot_id": "9b31d07f", "pool": "data", "path": "/mnt/pools/data/photos/2025"},
    ]})

    snapshot_id: str = Field(description=f"{_SNAPSHOT_ID_TEXT}.")
    pool: str = Field(description="The pool at whose top the new folder restored-<date>-<time in UTC> is made.")
    # a folder in the snapshot; none: all of it
    path: str | None = Field(default=None, description="A folder in the snapshot, as an absolute path such as "
                                                  "/mnt/pools/data/photos; none for all of it.")


# --- what runs ------------------------------------------------------------------------------------

# What runs, by its kind: a copy, a restore, or a change of the settings, which waits for neither.
_WHAT = {"copy": "copy", "restore-files": "restore", "restore-configuration": "restore",
         "save": "test of the settings", "remove": "removal of the settings", "key": "making of the key pair"}


@dataclass
class Activity:
    kind: str
    started: datetime
    step: str | None = None
    snapshot_id: str | None = None
    folder: str | None = None
    # The pools whose folders it reads or writes: the Storage page neither unmounts nor forgets them.
    pools: set[str] = field(default_factory=set)
    # The folders it reads or writes (the target, the folders it copies, a restore's folder): the
    # Storage page deletes no volume that is one of them or holds one.
    paths: set[str] = field(default_factory=set)

    def answer(self) -> dict:
        return {"kind": self.kind, "started": self.started.isoformat(), "step": self.step,
                "snapshot_id": self.snapshot_id, "folder": self.folder}


# The one copy, restore or change of the settings that runs: taken and given back on the event loop.
_activity: Activity | None = None
# The copies and restores that run in the background (Run now, Restore files): stop() ends them.
_tasks: set[asyncio.Task] = set()
# Held while a copy, a restore or Save names its folders (_use) and opens them, and by the Storage
# page while it checks that no copy uses a volume and deletes it: either the deletion sees the copy,
# or the copy finds the volume gone.
FOLDERS_LOCK = threading.Lock()


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _running(activity: Activity) -> str:
    return f"the {_WHAT[activity.kind]} started at {activity.started:%Y-%m-%d %H:%M:%S} UTC is still running"


def _busy() -> str:
    assert _activity is not None  # called while something runs
    return _running(_activity)


def _begin(kind: str) -> Activity | None:
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


def volume_user(volume_path: str) -> str | None:
    """What of the off-device copy uses the volume (or the restored copy of a snapshot) at VOLUME_PATH,
    as pool_user says it: a target, a folder that it copies or a restore's folder that is the volume
    or lies in it. A folder above it, such as the whole pool, does not count, as for the volume's
    other users (storage/pools.py)."""
    activity = _activity
    if activity is None or not any(_inside(path, volume_path) for path in activity.paths):
        return None
    return _running(activity)


def pool_user(pool: str) -> str | None:
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


def load_settings() -> OffsiteSettings | None:
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


def _stored() -> tuple[OffsiteSettings | None, Secrets]:
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
    disks = disks_of(fd)
    if not disks:
        raise UnknownDisk("its filesystem shows no disk")
    return bool(disks & system)


def _read_dev(path: str) -> int | None:
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


def _btrfs_fsid(fd: int) -> str | None:
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


def mount_source(fd: int) -> str | None:
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
    /mnt) and of the data volume DATA_DIR. UnknownDisk when those of either cannot be found."""
    found: set[str] = set()
    for path in (MOUNTS_BASE, DATA_DIR):
        try:
            fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        except OSError as e:
            raise UnknownDisk(f"{path} cannot be opened: {e.strerror or e}") from None
        try:
            disks = disks_of(fd)
        finally:
            os.close(fd)
        if not disks:
            raise UnknownDisk(f"the disks of {path} are not known")
        found |= disks
    return found


def _colons(digest: str) -> str:
    """A SHA-256 fingerprint as openssl prints it: pairs of upper-case digits joined by colons."""
    return ":".join(digest[i:i + 2] for i in range(0, len(digest), 2)).upper()


def _handshake(endpoint: str, context: ssl.SSLContext) -> bytes:
    """Connect to ENDPOINT's host and port and make the TLS handshake of CONTEXT within PROBE_TIMEOUT:
    the server's certificate in DER. Raises OSError and ssl.SSLError. Blocking."""
    parts = urlsplit(endpoint)
    host = parts.hostname or ""
    port = parts.port or 443
    deadline = time.monotonic() + PROBE_TIMEOUT
    with socket.create_connection((host, port), timeout=PROBE_TIMEOUT) as connection:
        connection.settimeout(max(deadline - time.monotonic(), 0.01))
        with context.wrap_socket(connection, server_hostname=host) as tls:
            return tls.getpeercert(binary_form=True) or b""


def _unverified() -> ssl.SSLContext:
    """A TLS context that takes any certificate: only to read a server's own, whose fingerprint is
    then compared (fetch_certificate, probe_store)."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def certificate_problem(der: bytes, endpoint: str) -> str | None:
    """Why restic, with the certificate DER as its only root (tls fingerprint), would refuse it for
    ENDPOINT, as Go checks it: its validity period and a subject alternative name for the host (an IP
    address, or a DNS name with at most one wildcard label); None when it would take it."""
    from cryptography import x509

    try:
        certificate = x509.load_der_x509_certificate(der)
    except ValueError:
        return f"The REST server at {endpoint} shows no certificate that can be read"
    now = datetime.now(UTC)
    if now < certificate.not_valid_before_utc:
        return f"The certificate of the REST server at {endpoint} is valid only from {certificate.not_valid_before_utc:%Y-%m-%d %H:%M} UTC"
    if now > certificate.not_valid_after_utc:
        return f"The certificate of the REST server at {endpoint} expired at {certificate.not_valid_after_utc:%Y-%m-%d %H:%M} UTC"
    host = (urlsplit(endpoint).hostname or "").lower()
    try:
        names = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        names = None
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if names is not None and address is not None:
        named = address in names.get_values_for_type(x509.IPAddress)
    elif names is not None:
        named = any(_dns_name_matches(name.lower(), host) for name in names.get_values_for_type(x509.DNSName))
    else:
        named = False
    if not named:
        return (f"The certificate of the REST server at {endpoint} does not name {host} as a subject alternative name, "
                "which restic needs")
    return None


def _dns_name_matches(pattern: str, host: str) -> bool:
    if pattern.startswith("*."):
        return host.count(".") >= 2 and host.split(".", 1)[1] == pattern[2:]
    return pattern == host


def fetch_certificate(endpoint: str) -> bytes:
    """The certificate that the server at ENDPOINT shows, in DER, read in a TLS handshake within
    PROBE_TIMEOUT that sends nothing else. Raises OSError and ssl.SSLError. Blocking."""
    return _handshake(endpoint, _unverified())


def probe_store(endpoint: str, *, what: str = "The S3 store", cadata: str | None = None,
                fingerprint: str | None = None) -> str | None:
    """Why the store (WHAT) at ENDPOINT cannot be reached, or None: a TCP connection to its host and
    port and, for https, a TLS handshake that verifies its certificate as restic does, within
    PROBE_TIMEOUT: with the system's roots, or only with CADATA (PEM); with FINGERPRINT, the server's
    certificate must have that SHA-256 digest (hexadecimal), and restic checks the rest with it as
    its only root. Nothing is sent but the handshake: no request, no key. Blocking."""
    parts = urlsplit(endpoint)
    try:
        if parts.scheme != "https":
            with socket.create_connection((parts.hostname or "", parts.port or 80), timeout=PROBE_TIMEOUT):
                return None
        if fingerprint:
            der = _handshake(endpoint, _unverified())
            found = hashlib.sha256(der).hexdigest()
            if found != fingerprint:
                return (f"{what} at {endpoint} shows a certificate with the SHA-256 fingerprint {_colons(found)}, not "
                        f"the pinned {_colons(fingerprint)}: if it changed on purpose, save the settings again with "
                        "its new fingerprint")
            return certificate_problem(der, endpoint)
        context = ssl.create_default_context(cadata=cadata) if cadata else ssl.create_default_context()
        if cadata:  # a certificate of the server's own, not a CA's, is a root too, as restic takes it
            context.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
        _handshake(endpoint, context)
    except ssl.SSLCertVerificationError as e:
        return f"{what} at {endpoint} has a certificate that restic refuses: {e.verify_message or e}"
    except TimeoutError:
        return f"{what} does not answer at {endpoint}: no answer within {PROBE_TIMEOUT:g} s"
    except ssl.SSLError as e:
        return f"{what} does not answer at {endpoint}: its TLS handshake failed: {e.reason or e}"
    except OSError as e:
        return f"{what} does not answer at {endpoint}: {e.strerror or e}"
    return None


def _store_name(settings: OffsiteSettings) -> str:
    return "The REST server" if settings.kind == Kind.REST else "The S3 store"


async def _unreachable(settings: OffsiteSettings) -> str | None:
    """For an S3 store or a REST server, why it cannot be reached (probe_store); None when it answers,
    and for a disk and an SFTP server, whose ssh gives up after its ConnectTimeout (ssh.py)."""
    if settings.kind not in (Kind.S3, Kind.REST):
        return None
    what = _store_name(settings)
    options: dict = {}
    if settings.kind == Kind.REST:
        options = {"what": what}
        if settings.tls == "fingerprint":
            options["fingerprint"] = settings.certificate_sha256
        elif settings.tls == "ca":
            options["cadata"] = settings.certificate
    try:
        # The name's lookup is not bounded by the socket's timeout.
        return await asyncio.wait_for(asyncio.to_thread(probe_store, settings.endpoint or "", **options),
                                      PROBE_TIMEOUT + 5)
    except TimeoutError:
        return f"{what} does not answer at {settings.endpoint}: no answer within {PROBE_TIMEOUT:g} s"


async def _pin_certificate(settings: OffsiteSettings) -> None:
    """For tls fingerprint, read the server's certificate, compare it with the fingerprint typed and
    keep it in SETTINGS as the root that restic trusts alone (--cacert); 400 when the server cannot be
    reached or shows another certificate."""
    endpoint = settings.endpoint or ""
    try:
        der = await asyncio.wait_for(asyncio.to_thread(fetch_certificate, endpoint), PROBE_TIMEOUT + 5)
    except TimeoutError:
        raise _bad(f"The REST server does not answer at {endpoint}: no answer within {PROBE_TIMEOUT:g} s") from None
    except ssl.SSLError as e:
        raise _bad(f"The REST server does not answer at {endpoint}: its TLS handshake failed: {e.reason or e}") from None
    except OSError as e:
        raise _bad(f"The REST server does not answer at {endpoint}: {e.strerror or e}") from None
    found = hashlib.sha256(der).hexdigest()
    if found != settings.certificate_sha256:
        raise _bad(f"The REST server at {endpoint} shows a certificate with the SHA-256 fingerprint {_colons(found)}, "
                   f"not {_colons(settings.certificate_sha256 or '')}: nothing was saved")
    problem = certificate_problem(der, endpoint)
    if problem:
        raise _bad(f"{problem}: nothing was saved")
    settings.certificate = ssl.DER_cert_to_PEM_cert(der)


def _close(fds: list[int]) -> None:
    """Close FDS, the folders held open while restic ran."""
    while fds:
        try:
            os.close(fds.pop())
        except OSError:
            pass


def _system_disk_problem(fd: int, path: str) -> str | None:
    """Why PATH, open at FD, cannot hold the copy as a folder on the system disk (on_system_disk), or
    None. When the disks cannot be found it cannot either, and the reason is logged too."""
    try:
        return _on_system_disk_problem(path) if on_system_disk(fd) else None
    except UnknownDisk as e:
        problem = (f"UNOS cannot tell which disk holds {path} ({e}), so it cannot make sure that the copy "
                   f"leaves the system disk: choose a folder on a disk mounted below {MOUNTS_BASE}")
        logger.warning("%s", problem)
        return problem


def _pool_of(path: str | None) -> str | None:
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
    assert isinstance(path, str)  # _parts refuses anything else
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


def _open_target(path: str, *, create: bool) -> tuple[int, str | None]:
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


def _endpoint(value: object, kind: Kind = Kind.S3) -> str:
    """An S3 store's endpoint, https:// or http://, or a REST server's, https:// only: its scheme, host
    and port, normalized; 400 otherwise."""
    rest = kind == Kind.REST
    what = "the REST server" if rest else "the S3 store"
    if not isinstance(value, str) or not value or not value.isprintable() or len(value) > 512:
        example = "https://nas2.example.com:8000" if rest else "https://s3.eu-central-1.amazonaws.com"
        raise _bad(f"Enter the address of {what}, such as {example}" if rest
                   else f"Enter the endpoint of {what}, such as {example}")
    parts = urlsplit(value)
    if rest and parts.scheme != "https":
        raise _bad("The address of the REST server must start with https://: restic sends its password with "
                   "every request")
    if parts.scheme not in ("https", "http"):
        raise _bad(f"The endpoint of {what} must start with https:// or http://")
    if parts.username or parts.password or parts.query or parts.fragment or parts.path not in ("", "/"):
        raise _bad(f"The {'address' if rest else 'endpoint'} of {what} must hold only the scheme, the host and the "
                   "port" + ("; its folders go into Folders on the server" if rest else ""))
    host = parts.hostname or ""
    try:
        port = parts.port
    except ValueError:
        raise _bad(f"The port of the endpoint of {what} is not a port number") from None
    return f"{parts.scheme}://{_url_host(_host_name(host, 'The host of the endpoint'))}" + (f":{port}" if port else "")


def _host_name(value: object, what: str) -> str:
    """VALUE as a host name, an IPv4 address or an IPv6 address without brackets; 400 otherwise."""
    host = value if isinstance(value, str) else ""
    if ":" in host:
        try:
            return str(ipaddress.IPv6Address(host.strip("[]")))
        except ValueError:
            raise _bad(f"{what}, {host}, is not a host name or an IP address") from None
    if not _HOST_NAME.fullmatch(host) or len(host) > 253:
        raise _bad(f"{what}, {host!r}, is not a host name or an IP address")
    return host


def _url_host(host: str) -> str:
    return f"[{host}]" if ":" in host else host


def _folders(value: object, what: str) -> str | None:
    """Folder names of letters, digits, '.', '_' or '-' joined by '/' (an S3 prefix, the folders on a
    REST server); None for none; 400 otherwise."""
    if not value:
        return None
    parts = [p for p in value.split("/") if p] if isinstance(value, str) else []
    if not isinstance(value, str) or not parts or len(value) > 512 or any(
            not _PREFIX_PART.fullmatch(p) or p in (".", "..") for p in parts):
        raise _bad(f"{what} must be folder names of letters, digits, '.', '_' or '-', joined by '/'")
    return "/".join(parts)


def _remote_path(value: object) -> str:
    """The folder on an SFTP server: absolute, or below the login's home folder; its parts words of
    letters, digits, '.', '_', '+' and '-', none '.' or '..'; normalized. 400 otherwise."""
    what = "The folder on the SFTP server"
    if not isinstance(value, str) or not value.strip("/"):
        raise _bad(f"Enter {what[0].lower() + what[1:]}, such as /srv/copies/unos")
    parts = [p for p in value.split("/") if p]
    if len(value) > MAX_PATH_LENGTH or any(not _REMOTE_PART.fullmatch(p) or p in (".", "..") for p in parts):
        raise _bad(f"{what} must be an absolute path or one below the login's home folder, of folder names of "
                   "letters, digits, '.', '_', '+' and '-', without '..' or '~'")
    return ("/" if value.startswith("/") else "") + "/".join(parts)


def _certificates(value: object) -> str:
    """CA certificates in PEM, as restic's --cacert reads them; 400 otherwise."""
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_CERTIFICATE_LENGTH:
        raise _bad("Paste the CA certificate of the REST server in PEM, from -----BEGIN CERTIFICATE-----")
    blocks = re.findall(r"-----BEGIN CERTIFICATE-----[A-Za-z0-9+/=\s]+?-----END CERTIFICATE-----", value)
    try:
        if not blocks:
            raise ValueError("no certificate")
        for block in blocks:
            ssl.PEM_cert_to_DER_cert(block)
        ssl.create_default_context(cadata="\n".join(blocks))
    except (ValueError, ssl.SSLError):
        raise _bad("The CA certificate of the REST server is no certificate in PEM that restic can read") from None
    return "\n".join(blocks) + "\n"


def _sha256_text(value: object) -> str:
    """A SHA-256 fingerprint of a certificate: 64 hexadecimal digits, colons allowed; 400 otherwise."""
    digest = value.replace(":", "").strip().lower() if isinstance(value, str) else ""
    if not _SHA256.fullmatch(digest):
        raise _bad("The fingerprint of the REST server's certificate must be its SHA-256 digest, 64 hexadecimal "
                   "digits, colons allowed")
    return digest


def _password(value: str) -> str:
    if len(value) < MIN_PASSWORD_LENGTH:
        raise _bad(f"The password of the copy repository must have at least {MIN_PASSWORD_LENGTH} characters")
    if len(value) > MAX_PASSWORD_LENGTH or "\x00" in value:
        raise _bad(f"The password of the copy repository must have at most {MAX_PASSWORD_LENGTH} characters, "
                   "without NUL")
    return value


def _normalized(request: SettingsRequest) -> OffsiteSettings:
    """The settings of REQUEST, checked as far as that needs no filesystem; 400 otherwise."""
    numbers: dict[str, Any] = {
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
    if request.kind in (Kind.REST, Kind.SFTP):
        target = _rest_target(request) if request.kind == Kind.REST else _sftp_target(request)
        return OffsiteSettings(**target, sources=list(request.sources), **numbers)
    bucket = request.bucket or ""
    if not _BUCKET.fullmatch(bucket):
        raise _bad("The bucket must have 3 to 63 letters, digits, '.', '_' or '-', starting and ending with a "
                   "letter or digit")
    prefix = _folders(request.prefix, "The prefix")
    region = request.region or None
    if region is not None and not _REGION.fullmatch(region):
        raise _bad("The region must have letters, digits and '-' only, such as eu-central-1")
    if not _KEY.fullmatch(request.access_key_id or ""):
        raise _bad("Enter the access key ID of the S3 store: printable characters without spaces")
    return OffsiteSettings(kind=Kind.S3, endpoint=_endpoint(request.endpoint), bucket=bucket, prefix=prefix,
                           region=region, access_key_id=request.access_key_id, sources=list(request.sources),
                           **numbers)


def _rest_target(settings: OffsiteSettings) -> dict:
    """The fields of a REST server, checked: https only, its folders, its user and how its certificate is
    checked; 400 otherwise. The certificate of tls fingerprint is read from the server at a save
    (_pin_certificate); a stored one must be a certificate."""
    endpoint = _endpoint(settings.endpoint, Kind.REST)
    prefix = _folders(settings.prefix, "The folders of the copy repository on the REST server")
    if not isinstance(settings.username, str) or not _REST_USER.fullmatch(settings.username):
        raise _bad("Enter the user of the REST server: printable characters without spaces or ':'")
    tls = settings.tls or "system"
    if tls not in TLS_MODES:
        raise _bad("How the REST server's certificate is checked must be system, fingerprint or ca")
    target = {"kind": Kind.REST, "endpoint": endpoint, "prefix": prefix, "username": settings.username, "tls": tls}
    if tls == "ca":
        target["certificate"] = _certificates(settings.certificate)
    elif tls == "fingerprint":
        target["certificate_sha256"] = _sha256_text(settings.certificate_sha256)
        if not isinstance(settings, SettingsRequest):  # stored: the certificate read at the save
            target["certificate"] = _certificates(settings.certificate).strip() + "\n"
    return target


def _sftp_target(settings: OffsiteSettings) -> dict:
    """The fields of an SFTP server, checked: its host, port, login and folder, words that change
    neither the repository's URL nor the command of ssh.py; 400 otherwise. The host key comes from
    the server at the first test (save_settings), never from a request; a stored one must be a key."""
    target = {
        "kind": Kind.SFTP,
        "host": _host_name(settings.host, "The host of the SFTP server"),
        "port": _number(22 if settings.port is None else settings.port, "The port of the SFTP server", 1, 65535),
        "username": settings.username if isinstance(settings.username, str) and _SSH_USER.fullmatch(
            settings.username) else None,
        "path": _remote_path(settings.path),
    }
    if target["username"] is None:
        raise _bad("Enter the user of the SFTP server: letters, digits, '.', '_' and '-', not starting with '-' or '.'")
    if not isinstance(settings, SettingsRequest):
        target["host_key"] = ssh.host_key(settings.host_key)
        if target["host_key"] is None:
            raise _bad("No valid host key of the SFTP server is pinned: save the settings again")
    return target


def location(settings: OffsiteSettings) -> str:
    """The repository as restic names it."""
    if settings.kind == Kind.DISK:
        return f"local:{settings.path}"
    if settings.kind == Kind.REST:
        return f"rest:{settings.endpoint}/" + (f"{settings.prefix}/" if settings.prefix else "")
    if settings.kind == Kind.SFTP:
        # restic takes the path after the first '/' of sftp://: //srv/copies is /srv/copies.
        return f"sftp://{settings.username}@{_url_host(settings.host or '')}:{settings.port}/{settings.path}"
    return f"s3:{settings.endpoint}/{settings.bucket}" + (f"/{settings.prefix}" if settings.prefix else "")


def _checked(stored: OffsiteSettings) -> OffsiteSettings:
    """STORED settings of a REST or SFTP server checked again before restic runs: a file edited by
    hand could otherwise put words into ssh's command or restic's URL. 400 otherwise."""
    if stored.kind == Kind.REST:
        target = _rest_target(stored)
    elif stored.kind == Kind.SFTP:
        target = _sftp_target(stored)
    else:
        return stored
    return stored.model_copy(update=target)


def _remote_password(value: str) -> str:
    if len(value) > MAX_REMOTE_PASSWORD_LENGTH or not value.isprintable():
        raise _bad(f"The password of the REST server must have at most {MAX_REMOTE_PASSWORD_LENGTH} printable "
                   "characters")
    return value


def _secrets_for(request: SettingsRequest, settings: OffsiteSettings, stored: OffsiteSettings | None,
                 kept: Secrets) -> Secrets:
    """The secrets of REQUEST, or the stored ones that an edit of the same target keeps; 400 otherwise.
    The key pair of SFTP is kept as it is: only POST /offsite/sftp-key makes one."""
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
    rest_password = None
    if settings.kind == Kind.REST:
        if request.rest_password:
            rest_password = _remote_password(request.rest_password)
        elif (stored is not None and stored.kind == Kind.REST and kept.rest_password
              and (stored.endpoint, stored.username) == (settings.endpoint, settings.username)):
            rest_password = kept.rest_password
        else:
            raise _bad("Enter the password of the REST server")
    if settings.kind == Kind.SFTP and not (kept.sftp_private_key and ssh.public_key(kept.sftp_private_key)):
        raise _bad("Make the key pair of UNOS first (Make a key pair), and add its public key to the authorized "
                   "keys of the login on the SFTP server")
    return Secrets(password=password, s3_secret_access_key=secret, rest_password=rest_password,
                   sftp_private_key=kept.sftp_private_key)


def _prepare_folders(settings: OffsiteSettings, activity: Activity) -> tuple[str | None, list[int]]:
    """Check the sources and a disk target on the filesystem, normalize the sources, and make the
    target's folder when it is missing: its path then, and the target held open for restic (_ready),
    which the caller closes; the target is named for ACTIVITY first, under FOLDERS_LOCK. 400 for what
    cannot be copied or hold the copy."""
    sources = []
    for path in settings.sources:
        normalized, fd = _open_source(path)
        os.close(fd)
        if normalized not in sources:
            sources.append(normalized)
    settings.sources = sources
    if settings.kind != Kind.DISK:
        return None, []
    assert settings.path is not None  # a disk target has its folder (_normalized)
    if any(_inside(source, settings.path) for source in sources):
        raise _bad(f"{settings.path} holds a folder that it copies: choose a folder outside them, or one inside "
                   "a folder that it copies")
    with FOLDERS_LOCK:
        _use(activity, _paths_used(settings, sources=False))
        fd, created = _open_target(settings.path, create=True)
    return created, [fd]


def _warnings(settings: OffsiteSettings) -> list[dict]:
    """same_disk: a disk target on a disk of a folder that it copies."""
    if settings.kind != Kind.DISK:
        return []
    target: int | None = None
    sources: list[int] = []
    assert settings.path is not None  # a disk target has its folder (_normalized)
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
    pinned = ssh.host_key(stored.host_key) if stored is not None and stored.kind == Kind.SFTP else None
    return {
        "configured": stored is not None,
        "settings": stored.model_dump(mode="json") if stored is not None else None,
        "has_password": bool(secrets.password),
        "has_s3_secret_access_key": bool(secrets.s3_secret_access_key),
        "has_rest_password": bool(secrets.rest_password),
        "sftp_public_key": ssh.public_key(secrets.sftp_private_key) if secrets.sftp_private_key else None,
        "host_key_fingerprint": ssh.fingerprint(pinned) if pinned else None,
        "pools": pools,
        "pools_base": str(pools_base()),
        "warnings": warnings,
    }


def _sftp_server(settings: OffsiteSettings) -> str:
    return f"{_url_host(settings.host or '')}:{settings.port}"


async def _open_or_create(repo: _Repository, created: str | None, expected: str | None = None) -> None:
    """Open the repository with its password, or make it when none is there; 400 with restic's message,
    or when restic does not end within SAVE_TIMEOUT. For sftp, the host key that ssh pinned at the
    first command must have the fingerprint EXPECTED when one was typed, before restic makes anything;
    a key that differs from the pinned one is refused with what to do."""
    pinned = repo.host_key
    try:
        await _restic(repo, ["cat", "config"], step="settings", timeout=SAVE_TIMEOUT)
        failure = None
    except _Failed as e:
        failure = e
    # Compared only with a key that this test pinned: a kept pin was compared before restic ran.
    if expected and pinned is None and repo.host_key and ssh.fingerprint(repo.host_key) != expected:
        raise _bad(f"The host key of the SFTP server at {_sftp_server(repo.settings)} has the fingerprint "
                   f"{ssh.fingerprint(repo.host_key)}, not {expected}: nothing was saved")
    if failure is None:
        return
    if failure.timed_out:
        raise _bad(f"The store did not answer within {SAVE_TIMEOUT:g} s")
    where = location(repo.settings)
    if repo.settings.kind == Kind.SFTP and pinned and any(
            text in failure.message for text in ("Host key verification failed", "no matching host key type found")):
        raise _bad(f"The host key of the SFTP server at {_sftp_server(repo.settings)} is not the one pinned at its first "
                   f"test ({ssh.fingerprint(pinned)}). If the server's key changed on purpose, save again with Trust "
                   f"its new host key. {failure.message}")
    if failure.code == WRONG_PASSWORD:
        raise _bad(f"The password does not open the copy repository at {where}: {failure.message}")
    if failure.code != REPOSITORY_MISSING:
        raise _bad(f"The copy repository at {where} cannot be reached: {failure.message}")
    path = repo.settings.path  # a disk target's folder
    if repo.settings.kind == Kind.DISK and created is None and path and await asyncio.to_thread(_holds_files, path):
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


def _change_made(stored: OffsiteSettings | None, kept: Secrets, settings: OffsiteSettings,
                 secrets: Secrets) -> str | None:
    """What an entry of the audit log says about the settings that were just stored, or None when they
    and the secrets are as they were (ADR-0402). The secrets, and the access key ID, come by name
    only."""
    if stored is None:
        return f"Off-device copy set up: {location(settings)}"
    changed = changed_fields(stored.model_dump(mode="json"), settings.model_dump(mode="json"),
                             shown=set(OffsiteSettings.model_fields) - {"access_key_id", "host_key", "certificate"})
    named = [name for name, new, old in (("password", secrets.password, kept.password),
                                         ("s3_secret_access_key", secrets.s3_secret_access_key,
                                          kept.s3_secret_access_key),
                                         ("rest_password", secrets.rest_password, kept.rest_password)) if new != old]
    both = ", ".join(part for part in (changed, *named) if part)
    return f"Off-device copy settings changed: {both}" if both else None


async def save_settings(request: SettingsRequest, changes: list[tuple[str, str]] | None = None) -> dict:
    """PUT /offsite/settings: check the settings, test the target with restic, then store them.

    CHANGES, when given, gets (the repository, what changed) once the settings were stored and
    differ from the stored ones: the router writes the audit entry from it (ADR-0402)."""
    activity = _begin_or_409("save")
    try:
        stored, kept = _stored()
        settings = _normalized(request)
        secrets = _secrets_for(request, settings, stored, kept)
        expected = None
        if settings.kind == Kind.SFTP:
            if request.host_key_fingerprint:
                expected = ssh.fingerprint_text(request.host_key_fingerprint)
                if expected is None:
                    raise _bad("The fingerprint of the SFTP server's host key must be SHA256: and 43 characters of "
                               "base64, as ssh-keygen -l prints it")
            if await asyncio.to_thread(host.missing_host_binaries, [ssh.ENV, ssh.SSH]):
                raise _bad(f"The host has no {ssh.SSH}, which the copy over SFTP runs: install the package "
                           "openssh-client on the host")
            # The same server keeps its pin; another server, or a trusted new key, is pinned at this test.
            kept_pin = (stored.host_key if stored is not None and stored.kind == Kind.SFTP
                        and (stored.host, stored.port) == (settings.host, settings.port) else None)
            kept_pin = ssh.host_key(kept_pin)
            if kept_pin and request.trust_new_host_key and expected is None:
                raise _bad("Enter the fingerprint of the server's new host key to trust it: a changed key is what a "
                           "server in the middle would show")
            if kept_pin and not request.trust_new_host_key:
                if expected and ssh.fingerprint(kept_pin) != expected:
                    raise _bad(f"The pinned host key of the SFTP server at {_sftp_server(settings)} has the fingerprint "
                               f"{ssh.fingerprint(kept_pin)}, not {expected}. If the server's key changed on purpose, "
                               "save again with Trust its new host key")
                settings.host_key = kept_pin
        if settings.kind == Kind.REST and settings.tls == "fingerprint":
            await _pin_certificate(settings)
        else:
            problem = await _unreachable(settings)
            if problem:
                raise _bad(problem)
        created, held = await asyncio.to_thread(_prepare_folders, settings, activity)
        repo = _repository_of(settings, secrets)
        try:
            await _open_or_create(repo, created, expected)
        except BaseException:
            if created is not None:
                await asyncio.to_thread(_remove_if_empty, created)
            raise
        finally:
            _close(held)
        if settings.kind == Kind.SFTP:
            if repo.host_key is None:
                raise _bad(f"ssh pinned no host key of the SFTP server at {_sftp_server(settings)}: nothing was saved")
            settings.host_key = repo.host_key
        made = _change_made(stored, kept, settings, secrets)
        if made:  # settings and secrets as they were are not written again (ADR-0214)
            await asyncio.to_thread(_store, settings, secrets)
            logger.info("The off-device copy goes to %s", location(settings))
            if changes is not None:
                changes.append((location(settings), made))
    finally:
        _end(activity)
    return await settings_answer()


async def make_key_pair(changes: list[tuple[str, str]] | None = None) -> dict:
    """POST /offsite/sftp-key: a new key pair of UNOS for SFTP, whose private key goes into the secrets
    file and never into an answer; the answer is GET /offsite/settings's, with the public key. It
    replaces the one before: the SFTP server must then take the new public key. CHANGES, when given,
    gets (what, the audit's text) (ADR-0402)."""
    activity = _begin_or_409("key")
    try:
        _, kept = _stored()
        private = await asyncio.to_thread(ssh.new_private_key)
        await asyncio.to_thread(write_secrets, kept.model_copy(update={"sftp_private_key": private}))
        public = ssh.public_key(private) or ""
        text = (f"Made a new SSH key pair for the off-device copy over SFTP ({ssh.fingerprint(public)})"
                + (", which replaces the one before" if kept.sftp_private_key else ""))
        logger.info("%s", text)
        if changes is not None:
            changes.append(("sftp-key", text))
    finally:
        _end(activity)
    return await settings_answer()


def _remove_files() -> bool:
    """The settings, the secrets and the state, and the copies of them kept as .bad (ADR-0324): a
    damaged secrets file holds the password too."""
    existed = False
    for name in (SETTINGS_FILE, SECRETS_FILE, STATUS_FILE):
        path = Path(name)
        existed |= path.exists() or state_files.bad_path(path).exists()
        path.unlink(missing_ok=True)
        state_files.bad_path(path).unlink(missing_ok=True)
        for staged in path.parent.glob(f".{path.name}.*.tmp"):  # of a write that a stop interrupted
            staged.unlink(missing_ok=True)
    _sync_folder(Path(SECRETS_FILE).parent)
    return existed


async def remove_settings(changes: list[tuple[str, str]] | None = None) -> dict:
    """DELETE /offsite/settings: remove the settings, the secrets and the state. The copies stay where
    they are. CHANGES, when given, gets (the repository, what was done) if there was anything to
    remove (ADR-0402)."""
    activity = _begin_or_409("remove")
    try:
        try:
            stored = await asyncio.to_thread(load_settings)
        except OSError:
            stored = None  # the files are removed all the same
        if await asyncio.to_thread(_remove_files) and changes is not None:
            where = location(stored) if stored is not None else "the settings"
            changes.append((where, f"Forgot the off-device copy target {where}; the copies stayed where they are"))
    finally:
        _end(activity)
    return {"status": "deleted"}


# --- restic ---------------------------------------------------------------------------------------


class _Failed(Exception):
    """A step failed: STEP, what went wrong, and restic's exit status when it ran. PREFIX replaces the
    step's text of STEP_TEXTS in the message of the run."""

    def __init__(self, step: str, message: str, code: int | None = None, *, prefix: str | None = None,
                 timed_out: bool = False) -> None:
        super().__init__(message)
        self.step = step
        self.message = message
        self.code = code
        self.prefix = prefix
        self.timed_out = timed_out  # restic did not end within its timeout and was killed


@dataclass
class _Repository:
    """The settings and the secrets that restic gets: its environment, its options and the files that
    they name. For sftp, HOST_KEY is the key that ssh holds to; without it (the first test of a
    server) ssh pins the one it meets, which files() then puts into HOST_KEY for the next commands."""

    settings: OffsiteSettings
    password: str = field(repr=False)
    secret_key: str | None = field(default=None, repr=False)
    rest_password: str | None = field(default=None, repr=False)
    private_key: str | None = field(default=None, repr=False)
    host_key: str | None = None

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
        if self.settings.kind == Kind.REST:  # restic 0.17 and later; never in the URL
            env["RESTIC_REST_USERNAME"] = self.settings.username or ""
            env["RESTIC_REST_PASSWORD"] = self.rest_password or ""
        return env

    @contextlib.contextmanager
    def files(self) -> Iterator[list[str]]:
        """The options that name files for one restic command, with the files in a new folder of
        TMPDIR (the tmpfs, mode 0700), removed when the command ends: for rest with a certificate,
        --cacert; for sftp, -o sftp.command with UNOS's private key (mode 0600) and the known host
        key. A key that ssh pinned at a first test is read back into host_key."""
        kind = self.settings.kind
        if not (kind == Kind.SFTP or (kind == Kind.REST and self.settings.certificate)):
            yield []
            return
        with tempfile.TemporaryDirectory(prefix="unos-offsite-") as folder:
            if kind == Kind.REST:
                root = os.path.join(folder, "root.pem")
                _write_private(root, self.settings.certificate or "")
                yield ["--cacert", root]
                return
            identity, known = os.path.join(folder, "id"), os.path.join(folder, "known_hosts")
            _write_private(identity, self.private_key or "")
            _write_private(known, ssh.known_hosts_line(self.host_key) if self.host_key else "")
            settings = self.settings
            argv = ssh.command(server=settings.host or "", port=settings.port or 22, user=settings.username or "",
                               identity=identity, known_hosts=known, pinned=self.host_key)
            yield ["-o", "sftp.command=" + " ".join(argv)]
            if self.host_key is None:
                with open(known) as f:
                    self.host_key = ssh.known_host(f.read())

    def options(self, *, cache: bool = True) -> list[str]:
        """restic's global options: for a store over the network (s3, rest, sftp) a cache in TMPDIR
        (the tmpfs, rebuilt after a restart) and the limits; no cache for a disk, which is read as
        fast as a cache, nor without CACHE (the check, which reads every file from the store)."""
        if self.settings.kind == Kind.DISK:
            return ["--no-cache"]
        options = ["--cache-dir", os.path.join(tempfile.gettempdir(), "restic-cache")] if cache else ["--no-cache"]
        if self.settings.limit_upload_kib:
            options += ["--limit-upload", str(self.settings.limit_upload_kib)]
        if self.settings.limit_download_kib:
            options += ["--limit-download", str(self.settings.limit_download_kib)]
        return options

    def redact(self, text: str) -> str:
        for secret in (self.password, self.secret_key, self.rest_password, self.private_key):
            if secret:
                text = text.replace(secret, "<redacted>")
        return text


def _write_private(path: str, text: str) -> None:
    """A new file PATH of mode 0600 with TEXT."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)


def _duration(seconds: float) -> str:
    for size, unit in ((_DAY, "day"), (_HOUR, "hour"), (_MINUTE, "minute"), (1, "second")):
        if seconds >= size and seconds % size == 0:
            count = int(seconds // size)
            return f"{count} {unit}{'' if count == 1 else 's'}"
    return f"{seconds:g} seconds"


def _message(text: str) -> str:
    """restic's standard error as one line: its end, where restic says why it failed."""
    return " ".join(text.split())[-1500:] or "no message"


async def _restic(repo: _Repository, args: list[str], *, step: str, stdin: bytes | None = None,
                  stdout_path: Path | None = None, partial: bool = False,
                  on_error_line: Callable[[bytes], None] | None = None,
                  timeout: float | None = None, cache: bool = True) -> restic.Result:
    """Run the restic command ARGS on REPO within TIMEOUT, by default its TIMEOUTS; _Failed(STEP) when
    it cannot run, times out or fails. With PARTIAL, exit status 3 (a backup that saved its snapshot
    without some files) is no failure: the caller judges it by the errors that ON_ERROR_LINE gets."""
    name = args[0]
    timeout = timeout or TIMEOUTS[name]
    waits = ["--retry-lock", RETRY_LOCK] if name in WAIT_FOR_LOCKS else []
    try:
        with repo.files() as named:
            result = await restic.run([*repo.options(cache=cache), *named, *waits, *args], repo.env(), timeout=timeout,
                                      stdin=stdin, stdout_path=stdout_path, on_error_line=on_error_line)
    except restic.Timeout:
        raise _Failed(step, f"restic {name} did not end within {_duration(timeout)}: it was stopped",
                      timed_out=True) from None
    except (OSError, ValueError) as e:  # ValueError: a NUL in its environment, from a file edited by hand
        raise _Failed(step, f"restic could not run: {repo.redact(str(e))}") from None
    if result.code != 0 and not (partial and result.code == PARTIAL):
        raise _Failed(step, f"restic {name} ended with exit status {result.code}: "
                            f"{_message(repo.redact(result.stderr))}", result.code)
    return result


def _ready(stored: OffsiteSettings | None, secrets: Secrets, *, sources: bool,
           activity: Activity | None = None) -> tuple[_Repository, list[int]]:
    """The repository of the stored settings, with its target checked (and the sources) and held open
    while restic runs, which the caller closes (_close): a folder held open keeps its filesystem busy,
    so that a plain unmount of its disk fails, and restic's local back end, which makes the folders
    that it misses, never writes onto the empty mount point, on the system disk. _Failed with step
    settings when something is not stored, folders when a folder cannot be used. The folders are
    named for ACTIVITY and opened under FOLDERS_LOCK. Blocking."""
    if stored is None:
        raise _Failed("settings", NOT_SET_UP)
    if not secrets.password:
        raise _Failed("settings", NO_PASSWORD)
    if stored.kind == Kind.S3 and not secrets.s3_secret_access_key:
        raise _Failed("settings", NO_SECRET_KEY)
    if stored.kind == Kind.REST and not secrets.rest_password:
        raise _Failed("settings", NO_REST_PASSWORD)
    if stored.kind == Kind.SFTP and not (secrets.sftp_private_key and ssh.public_key(secrets.sftp_private_key)):
        raise _Failed("settings", NO_KEY_PAIR)
    try:
        stored = _checked(stored)
    except HTTPException as e:
        message = f"The stored settings of the off-device copy are not valid: {e.detail}"
        logger.warning("%s", message)
        raise _Failed("settings", message) from None
    held: list[int] = []
    try:
        with FOLDERS_LOCK:
            if activity is not None:
                _use(activity, _paths_used(stored, sources=sources))
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
    return _repository_of(stored, secrets), held


def _repository_of(settings: OffsiteSettings, secrets: Secrets) -> _Repository:
    return _Repository(settings, secrets.password or "", secrets.s3_secret_access_key, secrets.rest_password,
                       secrets.sftp_private_key, settings.host_key if settings.kind == Kind.SFTP else None)


async def _repository(activity: Activity | None = None) -> tuple[_Repository, list[int]]:
    """The repository for the snapshots and the restores, with the target held open (_ready), which
    the caller closes: 409 when nothing is set up, 503 when the target cannot be used. ACTIVITY, the
    restore, names the target's pool."""
    stored, secrets = _stored()
    try:
        return await asyncio.to_thread(_ready, stored, secrets, sources=False, activity=activity)
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
    path = settings.path  # a disk target's folder
    if settings.kind == Kind.DISK and path and any(_inside(path, source) for source in settings.sources):
        patterns.append(path)
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
        self.unnamed: str | None = None
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

    def problem(self) -> str | None:
        """Why the copy fails: the files that could not be read; None when every file was read or vanished."""
        if self.unreadable_count:
            return (f"{_files(self.unreadable_count)} could not be read: "
                    f"{_listed(self.unreadable, self.unreadable_count)}")
        return self.unnamed

    def note(self, error: str | None) -> str | None:
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
    activity.step = "folders"
    repo, held = await asyncio.to_thread(_ready, stored, secrets, sources=True, activity=activity)
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
    # Set by the backup step above, with the IDs of both snapshots.
    assert summary is not None and summary.configuration_snapshot and summary.data_snapshot
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


async def run_copy(trigger: str, activity: Activity | None = None) -> tuple[bool, str]:
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
        return datetime.fromisoformat(snapshot["time"]).astimezone(UTC)
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=UTC)


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
        summary = item["summary"] if isinstance(item.get("summary"), dict) else {}
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
                raise _bad(f"The configuration archive of the snapshot is larger than a backup may be: the limit is {limit_text(MAX_ARCHIVE_BYTES)}")
            try:
                await asyncio.to_thread(check_archive, archive)
            except BackupValidationError as e:
                raise _bad(str(e)) from None
            when = _when(snapshot).replace(tzinfo=None)
            backup: BackupInfo = await asyncio.to_thread(store_backup, archive, "offsite", when)
    finally:
        _close(held)
        _end(activity)
    return {"status": "success", "backup": backup.model_dump(mode="json")}


def _snapshot_path(value: str | None) -> str | None:
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


async def _restore_files(activity: Activity, repo: _Repository, snapshot_id: str, path: str | None,
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
