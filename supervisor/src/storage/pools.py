"""Btrfs storage pools.

A pool is a btrfs filesystem whose label is the pool name. It is mounted in
the HOST mount namespace at ``<pools_base_path>/<name>`` (``/mnt/pools/<name>``)
by filesystem UUID; the supervisor and app containers see the mount through
the rshared ``/mnt`` bind mount.

Persistence across reboots is one managed entry per pool in the HOST's
``/etc/fstab``::

    # unos-pool:<name> (managed by UNOS)
    UUID=<uuid> /mnt/pools/<name> btrfs defaults,noatime,nofail,x-systemd.device-timeout=15s,x-systemd.before=docker.service 0 0

UNOS only adds, rewrites or removes such marker + entry pairs; every other
line of the host fstab is preserved as is. The original file is copied once to
``/etc/fstab.unos-backup`` before the first modification. ``nofail`` keeps a
missing disk from blocking the boot; it also drops the ordering before
local-fs.target, so ``x-systemd.before=docker.service`` makes Docker (and the
apps it restarts) wait for the mount, for at most the 15 s device timeout.
:func:`reconcile_pools` mounts managed entries the host did not mount (e.g. a
slow disk) when the supervisor starts, starts the apps that Docker could not
start without them, and write-protects the mountpoints of pools that are not
mounted at all. :func:`mount_pool` starts those apps as well. The apps are
started in the background, after the pool lock is released
(:func:`_start_pool_apps_later`): their checks read the pool's files, which
can hang on a failing disk.

A pool is never mounted over a directory that holds files: those files would
be hidden while they keep using space on the boot disk. Unmounting is refused
while containers use the pool: a container's bind mount keeps the filesystem
mounted (and written to) even after the host's mountpoint is gone. The copies
of the pool's mount that systemd services keep in mount namespaces of their
own, whose root is the host's, are unmounted there as well; a container whose
namespace still has the pool mounted, also at its mountpoint, keeps it in use
(ADR-0288).

Deleting or unmounting a pool never touches the disks: the filesystem stays
intact and can be mounted again.

A pool is only mounted at its top level, where its volumes and share folders
are: a mount that shows another subvolume (the filesystem's default one was
changed) is undone. A btrfs filesystem found on the host's disks becomes a
pool without being formatted (ADR-0283): Mount imports the pool its label
names (_pool_choices), :func:`import_pool` any other, under a name that
becomes its label. Neither makes a pool of a boot or virtual device or of the
system disk, as creation does not (ADR-0035).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
import tempfile
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import docker
from docker.models.containers import Container
from fastapi import HTTPException

from .. import host
from ..config import get_settings
from ..security.validators import (
    POOL_NAME_PATTERN,
    VOLUME_NAME_PATTERN,
    validate_device_list,
    validate_filesystem_uuid,
    validate_pool_name,
    validate_volume_name,
)
from ..shares import nfs, smb
from . import disks
from .schemas import (
    DeviceIdentity,
    ImportableFilesystem,
    PoolCreate,
    PoolInfo,
    ScrubStatus,
    VolumeCreate,
    VolumeInfo,
)

logger = logging.getLogger(__name__)

settings = get_settings()

FSTAB_PATH = "/etc/fstab"
FSTAB_BACKUP_PATH = "/etc/fstab.unos-backup"
FSTAB_MOUNT_OPTIONS = (
    "defaults,noatime,nofail,x-systemd.device-timeout=15s,x-systemd.before=docker.service"
)
MOUNT_OPTIONS = "noatime"

MKFS_TIMEOUT = 900  # mkfs.btrfs discards (TRIMs) SSDs first, which can take minutes
MOUNT_TIMEOUT = 120
UMOUNT_TIMEOUT = 60
DOCKER_TIMEOUT = 15
# Seconds the checks before starting one app of a newly mounted pool may take
# (apps.router.prepare_app_start). They read the pool's files, which has no
# timeout of its own: a disk that mounts but then stops answering would block
# them for good.
APP_CHECK_TIMEOUT = 60
# Name (prefix) of the background threads that start the apps of a mounted pool.
_APP_THREAD_NAME = "unos-pool-apps"

# Label the app installer puts on containers that keep their data on a pool.
APP_POOL_LABEL = "unos.app.pool"
# Restart policies with which Docker starts a container when the daemon starts.
_BOOT_RESTART_POLICIES = frozenset({"always", "unless-stopped"})
# How many names of unexpected files to show in an error message.
_SAMPLE = 5

# BTRFS RAID5/6 are excluded due to the write hole bug that causes silent data
# corruption on power failure. Use RAID1C3/C4 instead.
PROFILE_MAP = {
    "single": ("single", "single"),
    "raid0": ("raid0", "raid0"),
    "raid1": ("raid1", "raid1"),
    "raid1c3": ("raid1c3", "raid1c3"),
    "raid1c4": ("raid1c4", "raid1c4"),
    "raid10": ("raid10", "raid10"),
}
MIN_DEVICES = {"single": 1, "raid0": 2, "raid1": 2, "raid1c3": 3, "raid1c4": 4, "raid10": 4}

# Use these with fullmatch(): `$` would also match before a trailing newline.
_MARKER_RE = re.compile(
    r"# unos-pool:(?P<name>[A-Za-z0-9][A-Za-z0-9_-]{0,62}) \(managed by UNOS\)"
)
_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_LABEL_RE = re.compile(r"^Label:\s+(?:'(?P<label>.*)'|none)\s+uuid:\s+(?P<uuid>\S+)$")
_TOTAL_RE = re.compile(r"^Total devices\s+(?P<total>\d+)\s+FS bytes used\s+(?P<used>\S+)")
_DEVID_RE = re.compile(
    r"^devid\s+\d+\s+size\s+(?P<size>\S+)\s+used\s+\S+\s+path\s+(?P<path>.*?)$"
)
_STAT_RE = re.compile(r"^\[[^\]]+\]\.\w+\s+(?P<value>\d+)$")

# Serialises every operation that changes pools, mounts or the host fstab.
# The operations run in worker threads (asyncio.to_thread).
_lock = threading.RLock()


class PoolNotFoundError(LookupError):
    """No pool (btrfs filesystem) with this name exists (HTTP 404)."""


class PoolConflictError(RuntimeError):
    """The request conflicts with the state of the host (HTTP 409)."""


class DeviceSafetyError(PoolConflictError):
    """A selected device is a system device, in use, or holds data (HTTP 409)."""


class PoolBusyError(PoolConflictError):
    """The pool cannot be unmounted because it is in use (HTTP 409)."""


# --------------------------------------------------------------------------
# Host /etc/fstab
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ManagedFstabEntry:
    name: str
    target: str
    has_entry: bool  # False when the marker is not followed by an entry for the target
    uuid: str | None  # UUID= source of the entry, if it uses one


def pool_mountpoint(name: str) -> str:
    return os.path.join(settings.pools_base_path, name)


def fstab_marker(name: str) -> str:
    return f"# unos-pool:{name} (managed by UNOS)"


def fstab_entry(name: str, uuid: str) -> str:
    return f"UUID={uuid} {pool_mountpoint(name)} btrfs {FSTAB_MOUNT_OPTIONS} 0 0"


def _fstab_fields(line: str) -> list[str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    return stripped.split()


def _same_target(field_value: str, target: str) -> bool:
    return field_value.rstrip("/") == target.rstrip("/")


def _marker_name(line: str) -> str | None:
    match = _MARKER_RE.fullmatch(line.rstrip())
    return match.group("name") if match else None


def _is_entry_for(line: str, name: str) -> bool:
    fields = _fstab_fields(line)
    return bool(fields and len(fields) >= 2 and _same_target(fields[1], pool_mountpoint(name)))


def parse_managed_fstab(text: str) -> dict[str, ManagedFstabEntry]:
    """Return the UNOS-managed pool entries of an fstab, keyed by pool name."""
    lines = text.splitlines()
    entries: dict[str, ManagedFstabEntry] = {}
    for index, line in enumerate(lines):
        name = _marker_name(line)
        if name is None or name in entries:
            continue
        has_entry = index + 1 < len(lines) and _is_entry_for(lines[index + 1], name)
        uuid = None
        if has_entry:
            source = lines[index + 1].split()[0]
            if source.startswith("UUID="):
                uuid = source[len("UUID="):].lower()
        entries[name] = ManagedFstabEntry(
            name=name, target=pool_mountpoint(name), has_entry=has_entry, uuid=uuid
        )
    return entries


def set_managed_fstab_entry(text: str, name: str, uuid: str) -> str:
    """Return ``text`` with exactly one managed entry for ``name`` mounting ``uuid``.

    An existing managed entry is rewritten in place; otherwise the entry is
    appended. Lines that are not ours are never changed.
    """
    marker, entry = fstab_marker(name), fstab_entry(name, uuid)
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    placed = False
    index = 0
    while index < len(lines):
        line = lines[index]
        if _marker_name(line) == name:
            owns_next = index + 1 < len(lines) and _is_entry_for(lines[index + 1], name)
            if not placed:
                out += [marker + "\n", entry + "\n"]
                placed = True
            index += 2 if owns_next else 1
            continue
        out.append(line)
        index += 1
    if not placed:
        if out and not out[-1].endswith("\n"):
            out[-1] += "\n"
        out += [marker + "\n", entry + "\n"]
    return "".join(out)


def remove_managed_fstab_entry(text: str, name: str) -> str:
    """Return ``text`` without the managed entry for ``name``; other lines are kept."""
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    index = 0
    while index < len(lines):
        if _marker_name(lines[index]) == name:
            owns_next = index + 1 < len(lines) and _is_entry_for(lines[index + 1], name)
            index += 2 if owns_next else 1
            continue
        out.append(lines[index])
        index += 1
    return "".join(out)


def unmanaged_fstab_entries(text: str, target: str) -> list[str]:
    """Active fstab lines for ``target`` that UNOS does not manage."""
    lines = text.splitlines()
    managed: set[int] = set()
    for index, line in enumerate(lines):
        name = _marker_name(line)
        if name is not None and index + 1 < len(lines) and _is_entry_for(lines[index + 1], name):
            managed.add(index + 1)
    result = []
    for index, line in enumerate(lines):
        fields = _fstab_fields(line)
        if index not in managed and fields and len(fields) >= 2 and _same_target(fields[1], target):
            result.append(line.strip())
    return result


def _read_fstab() -> str:
    return host.read_host_file(FSTAB_PATH)


def _reload_systemd() -> None:
    """Let systemd regenerate its .mount units from the changed fstab.

    Without this, systemd keeps the unit generated from the old entry. A
    stale unit that is bound to another device can make systemd unmount a
    filesystem right after we mount it, and mount(8) warns about it.
    """
    try:
        result = host.run_on_host(["systemctl", "daemon-reload"], timeout=60)
    except Exception as exc:
        logger.warning("systemctl daemon-reload failed: %s", exc)
        return
    if not result.ok:
        logger.warning("systemctl daemon-reload failed: %s", result.stderr.strip())


def _write_fstab(new_text: str, old_text: str) -> bool:
    """Replace the host fstab; returns False when there is nothing to change."""
    if new_text == old_text:
        return False
    if not host.run_on_host(["test", "-e", FSTAB_BACKUP_PATH], timeout=15).ok:
        host.write_host_file_atomic(FSTAB_BACKUP_PATH, old_text, mode="0644")
        logger.warning("Saved the original host %s as %s", FSTAB_PATH, FSTAB_BACKUP_PATH)
    host.write_host_file_atomic(FSTAB_PATH, new_text, mode="0644")
    _reload_systemd()
    return True


def _forget_fstab_entry(name: str) -> None:
    text = _read_fstab()
    new_text = remove_managed_fstab_entry(text, name)
    _write_fstab(new_text, text)
    leftovers = unmanaged_fstab_entries(new_text, pool_mountpoint(name))
    if leftovers:
        logger.warning(
            "The host /etc/fstab still mounts %s at boot through an entry UNOS does not manage: %s",
            pool_mountpoint(name),
            leftovers[0],
        )


def _managed_entries_or_none() -> dict[str, ManagedFstabEntry] | None:
    try:
        return parse_managed_fstab(_read_fstab())
    except Exception as exc:
        logger.warning("Could not read the host %s: %s", FSTAB_PATH, exc)
        return None


def managed_pool_names() -> set[str]:
    """Names of the pools UNOS manages (the fstab markers). Raises when the host's fstab cannot be read."""
    return set(parse_managed_fstab(_read_fstab()))


# --------------------------------------------------------------------------
# btrfs facts
# --------------------------------------------------------------------------


@dataclass
class BtrfsFilesystem:
    label: str | None
    uuid: str
    total_devices: int = 0
    bytes_used: int = 0
    devices: list[str] = field(default_factory=list)  # present members only
    devices_size: int = 0
    missing_devices: int = 0


def _int(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def parse_btrfs_show(output: str) -> list[BtrfsFilesystem]:
    """Parse ``btrfs filesystem show --raw``, including missing devices."""
    result: list[BtrfsFilesystem] = []
    current: BtrfsFilesystem | None = None
    flagged_missing = 0  # members printed as "... MISSING" (mounted filesystems)
    some_missing = False  # "*** Some devices missing"

    def close() -> None:
        if current is not None:
            current.missing_devices = max(
                current.total_devices - len(current.devices),
                flagged_missing,
                1 if some_missing else 0,
                0,
            )
            result.append(current)

    for raw in output.splitlines():
        line = raw.strip()
        label_match = _LABEL_RE.match(line)
        if label_match:
            close()
            current = BtrfsFilesystem(
                label=label_match.group("label"), uuid=label_match.group("uuid").lower()
            )
            flagged_missing, some_missing = 0, False
            continue
        if current is None:
            continue
        total_match = _TOTAL_RE.match(line)
        if total_match:
            current.total_devices = _int(total_match.group("total"))
            current.bytes_used = _int(total_match.group("used"))
            continue
        devid_match = _DEVID_RE.match(line)
        if devid_match:
            path = devid_match.group("path")
            if "MISSING" in path.split() or path.startswith("<missing"):
                flagged_missing += 1
            else:
                current.devices.append(path)
                current.devices_size += _int(devid_match.group("size"))
            continue
        if "Some devices missing" in line:
            some_missing = True
    close()
    return result


def btrfs_filesystems() -> list[BtrfsFilesystem]:
    """All btrfs filesystems the host knows about, mounted or not."""
    # The labels are printed as they are: one that is not UTF-8 (a filesystem
    # formatted elsewhere) must not keep the pools from being listed. It is
    # not a valid pool name either way.
    result = host.run_on_host(
        ["btrfs", "filesystem", "show", "--raw"], timeout=60, errors="replace"
    )
    if not result.ok:
        raise RuntimeError(
            f"btrfs filesystem show failed: {result.stderr.strip() or result.returncode}"
        )
    return parse_btrfs_show(result.stdout)


def _btrfs_usage(target: str) -> tuple[int, int, str | None] | None:
    result = host.run_on_host(["btrfs", "filesystem", "usage", "-b", target], timeout=60)
    if not result.ok:
        return None
    total_size = used_size = 0
    raid_level = None
    for raw in result.stdout.splitlines():
        line = raw.strip()
        if line.startswith("Device size:"):
            total_size = _int(line.split()[-1])
        elif line.startswith("Used:"):
            used_size = _int(line.split()[-1])
        elif line.startswith("Data,") and raid_level is None:
            raid_level = line.split(":")[0].replace("Data,", "").strip().lower()
    return total_size, used_size, raid_level


def btrfs_device_errors(target: str) -> int | None:
    """Sum of the ``btrfs device stats`` error counters, or None if unreadable."""
    result = host.run_on_host(["btrfs", "device", "stats", target], timeout=30)
    if not result.ok:
        return None
    total = 0
    seen = False
    for raw in result.stdout.splitlines():
        match = _STAT_RE.match(raw.strip())
        if match:
            seen = True
            total += int(match.group("value"))
    return total if seen else None


def classify_pool_health(
    *,
    mounted: bool | None,
    missing_devices: int,
    device_errors: int | None,
    forced_read_only: bool = False,
) -> str:
    """Pool health, from most to least specific cause.

    ``mounted`` is None when the mount table could not be read and
    ``device_errors`` is None when the error counters could not be read; a
    pool is never reported healthy on missing information.
    ``forced_read_only``: mounted read-write, but btrfs made the filesystem
    read-only after an error (e.g. an aborted transaction). That increments
    no error counter, and every write fails.
    """
    if mounted is None:
        return "unknown"
    if not mounted:
        return "unmounted"
    if forced_read_only:
        return "error"
    if missing_devices > 0:
        return "degraded"
    if device_errors is None:
        return "unknown"
    if device_errors > 0:
        return "error"
    return "healthy"


def _mount_matches(mount: disks.HostMount, fs: BtrfsFilesystem) -> bool:
    if mount.fstype != "btrfs":
        return False
    if mount.uuid and mount.uuid.lower() == fs.uuid:
        return True
    return bool(mount.source and mount.source in fs.devices)


def _is_system_filesystem(fs: BtrfsFilesystem, mounts: list[disks.HostMount] | None) -> bool:
    """A btrfs root/boot filesystem of the host is not a pool."""
    return any(
        m.target in disks.SYSTEM_MOUNTPOINTS and _mount_matches(m, fs) for m in mounts or []
    )


def _pool_filesystems(name: str, mounts: list[disks.HostMount] | None) -> list[BtrfsFilesystem]:
    return [
        fs
        for fs in btrfs_filesystems()
        if fs.label == name and not _is_system_filesystem(fs, mounts)
    ]


def _mounted_here(fs: BtrfsFilesystem, mounts: list[disks.HostMount] | None) -> bool:
    """Whether ``fs`` is mounted at the mountpoint of the pool its label names."""
    target = pool_mountpoint(fs.label or "")
    return any(m.target == target and _mount_matches(m, fs) for m in mounts or [])


def _managed_as_pool(fs: BtrfsFilesystem, managed: dict[str, ManagedFstabEntry] | None) -> bool:
    """Whether UNOS manages ``fs`` as the pool its label names (its fstab entry has the UUID)."""
    entry = (managed or {}).get(fs.label or "")
    return entry is not None and entry.has_entry and entry.uuid == fs.uuid


def _pool_choices(
    filesystems: list[BtrfsFilesystem],
    mounts: list[disks.HostMount] | None,
    managed: dict[str, ManagedFstabEntry] | None,
) -> dict[str, BtrfsFilesystem]:
    """The pool of each name: name -> the filesystem that is the pool (ADR-0283).

    A pool is a btrfs filesystem of the host, other than its system
    filesystem, whose label is a valid pool name. Of several with one label,
    the pool is the one mounted at the pool's mountpoint, or else the one
    that UNOS manages under the name; when neither tells them apart, the
    name has no pool, and each of them is found on the disks
    (importable_filesystems), so that no card of the Storage page acts on
    another filesystem by its name.
    """
    by_label: dict[str, list[BtrfsFilesystem]] = {}
    for fs in filesystems:
        if fs.label and POOL_NAME_PATTERN.fullmatch(fs.label) and not _is_system_filesystem(fs, mounts):
            by_label.setdefault(fs.label, []).append(fs)
    chosen: dict[str, BtrfsFilesystem] = {}
    for name, candidates in by_label.items():
        if len(candidates) == 1:
            chosen[name] = candidates[0]
            continue
        pick = next((fs for fs in candidates if _mounted_here(fs, mounts)), None) or next(
            (fs for fs in candidates if _managed_as_pool(fs, managed)), None
        )
        if pick is not None:
            chosen[name] = pick
    return chosen


def _device_problems(filesystems: list[BtrfsFilesystem]) -> dict[str, str]:
    """UUID -> why UNOS must not mount the filesystem as a pool, for those of ``filesystems``
    that have a member on a boot or virtual device or on the system disk
    (disks.pool_device_problems; ADR-0035, ADR-0225). RuntimeError when lsblk fails.
    """
    paths = sorted({device for fs in filesystems for device in fs.devices})
    if not paths:
        return {}
    problems = disks.pool_device_problems(paths)
    return {
        fs.uuid: next(problems[device] for device in fs.devices if device in problems)
        for fs in filesystems
        if any(device in problems for device in fs.devices)
    }


def _mounts_or_none() -> list[disks.HostMount] | None:
    try:
        return disks.host_mounts()
    except Exception as exc:
        logger.warning("Could not read the host mount table: %s", exc)
        return None


def _target_mounted(target: str) -> bool:
    return any(m.target == target for m in disks.host_mounts())


def _pool_info(
    fs: BtrfsFilesystem,
    mounts: list[disks.HostMount] | None,
    managed: dict[str, ManagedFstabEntry] | None,
) -> PoolInfo:
    name = fs.label or ""
    target = pool_mountpoint(name)
    here = [m for m in mounts or [] if m.target == target and _mount_matches(m, fs)]
    mounted = None if mounts is None else bool(here)
    forced_read_only = any(m.forced_read_only for m in here)
    total_size, used_size, raid_level = fs.devices_size, fs.bytes_used, None
    device_errors = None
    if mounted:
        usage = _btrfs_usage(target)
        if usage is not None:
            total_size, used_size, raid_level = usage
        device_errors = btrfs_device_errors(target)
    entry = (managed or {}).get(name)
    return PoolInfo(
        name=name,
        uuid=fs.uuid,
        devices=list(fs.devices),
        total_size=total_size,
        used_size=used_size,
        mountpoint=target if mounted else None,
        raid_level=raid_level,
        mounted=bool(mounted),
        persistent=bool(entry and entry.uuid == fs.uuid),
        health=classify_pool_health(
            mounted=mounted,
            missing_devices=fs.missing_devices,
            device_errors=device_errors,
            forced_read_only=forced_read_only,
        ),
        missing_devices=fs.missing_devices,
        device_errors=device_errors or 0,
        read_only=any(m.read_only for m in here),
        forced_read_only=forced_read_only,
    )


def list_pools() -> list[PoolInfo]:
    """List the pools: the btrfs filesystems labelled with a valid pool name (_pool_choices).

    A pool found on the disks, neither mounted at its mountpoint nor managed
    by UNOS, which Mount would import (ADR-0283), is left out when a member
    is on a boot or virtual device or on the system disk: Mount refuses it.
    When lsblk cannot tell, it is listed, and Mount checks again.
    """
    filesystems = btrfs_filesystems()
    mounts = _mounts_or_none()
    managed = _managed_entries_or_none()
    chosen = _pool_choices(filesystems, mounts, managed)
    found = [
        fs for fs in chosen.values() if not _mounted_here(fs, mounts) and not _managed_as_pool(fs, managed)
    ]
    try:
        refused = _device_problems(found)
    except Exception as exc:
        logger.warning("Could not check the devices of the pools found on the disks: %s", exc)
        refused = {}
    return [
        _pool_info(fs, mounts, managed)
        for fs in filesystems
        if fs.label and chosen.get(fs.label) is fs and fs.uuid not in refused
    ]


def get_pool(name: str) -> PoolInfo:
    """Get detailed info for a specific pool."""
    mounts = _mounts_or_none()
    candidates = _pool_filesystems(name, mounts)
    if not candidates:
        raise PoolNotFoundError(f"Pool '{name}' not found")
    managed = _managed_entries_or_none()
    target = pool_mountpoint(name)
    entry = (managed or {}).get(name)

    def rank(fs: BtrfsFilesystem) -> tuple[bool, bool]:
        mounted_here = any(m.target == target and _mount_matches(m, fs) for m in mounts or [])
        return (mounted_here, bool(entry and entry.uuid == fs.uuid))

    return _pool_info(max(candidates, key=rank), mounts, managed)


# --------------------------------------------------------------------------
# Mounting on the host
# --------------------------------------------------------------------------


def guard_mountpoint(target: str) -> bool:
    """Make an unmounted pool mountpoint immutable on the host (``chattr +i``).

    While a pool is not mounted, ``/mnt/pools/<name>`` is a plain directory on
    the host's root filesystem (the SD card on ARM boards). Shares, apps and
    containers that keep writing there would silently fill the boot disk, and
    the data would vanish under the pool once it is mounted again. With the
    immutable attribute set, creating anything in the directory fails with
    EPERM instead. Mounting the pool on top of it still works (mounting does
    not modify the mountpoint directory).

    The attribute is only set when the directory exists and nothing is
    mounted on it: on a mounted pool it would make the pool's root directory
    immutable. It is removed before the directory is deleted. It protects the
    directory itself only; files that already exist inside stay writable.
    Filesystems without support for the attribute (tmpfs, overlayfs, ...) make
    chattr fail; then a warning is logged and the directory stays unprotected.

    Returns True when the attribute was set.
    """
    try:
        if _target_mounted(target):
            return False
        if not host.run_on_host(["test", "-d", target], timeout=15).ok:
            return False
        result = host.run_on_host(["chattr", "+i", target], timeout=15)
    except Exception as exc:
        logger.warning("Could not protect the unmounted mountpoint %s: %s", target, exc)
        return False
    if not result.ok:
        logger.warning(
            "Could not protect the unmounted mountpoint %s with chattr +i (%s); "
            "writes to it will land on the host's root filesystem",
            target,
            result.stderr.strip() or result.returncode,
        )
        return False
    # The flag is metadata in the root filesystem's journal; flush it so a
    # power cut cannot silently drop the protection (ext4 commit=600).
    if not host.sync_host_path(target):
        logger.warning("Could not flush the protection of %s to disk", target)
    return True


def _ensure_mountpoint_dir(target: str) -> None:
    result = host.run_on_host(["mkdir", "-p", target], timeout=15)
    if not result.ok:
        raise RuntimeError(f"Could not create {target}: {result.stderr.strip()}")
    guard_mountpoint(target)


def _remove_mountpoint_dir(target: str) -> None:
    """Remove the guard and the empty mountpoint directory of a deleted pool."""
    if not host.run_on_host(["test", "-d", target], timeout=15).ok:
        return
    if _target_mounted(target):
        return
    host.run_on_host(["chattr", "-i", target], timeout=15)  # fails harmlessly if unset
    result = host.run_on_host(["rmdir", target], timeout=15)
    if not result.ok:
        logger.warning("Kept %s: %s", target, result.stderr.strip())
        guard_mountpoint(target)
        return
    host.sync_host_path(os.path.dirname(target))


def _mountpoint_entries(target: str) -> list[str]:
    """Names of the files in an unmounted mountpoint directory on the host.

    Returns [] when the directory does not exist. Raises RuntimeError when
    it cannot be read. Names that are not UTF-8 (SMB clients and apps
    choose them) get U+FFFD: they are only shown.
    """
    result = host.run_on_host(
        ["find", target, "-mindepth", "1", "-maxdepth", "1", "-print0"],
        timeout=60,
        errors="replace",
    )
    if result.ok:
        return sorted(os.path.basename(path) for path in result.stdout.split("\0") if path)
    if not host.run_on_host(["test", "-e", target], timeout=15).ok:
        return []
    raise RuntimeError(
        f"Could not check whether {target} is empty: "
        f"{result.stderr.strip() or f'exit status {result.returncode}'}"
    )


def _describe_entries(entries: list[str]) -> str:
    shown = ", ".join(entries[:_SAMPLE])
    if len(entries) > _SAMPLE:
        shown += f" and {len(entries) - _SAMPLE} more"
    return shown


def _refuse_hidden_files(target: str) -> None:
    """Refuse to mount a pool over a directory that holds files (PoolConflictError).

    While a pool is not mounted, its mountpoint is a plain directory on the
    host's boot disk, and shares, apps or the file manager may have written
    there. Mounting over it would hide those files while they keep using
    space on the boot disk.
    """
    entries = _mountpoint_entries(target)
    if entries:
        raise PoolConflictError(
            f"{target} is not empty: it holds {len(entries)} item(s) on the host's boot "
            f"disk ({_describe_entries(entries)}). Mounting the pool there would hide them "
            "while they keep using space on the boot disk. Move them off the host's "
            f"{target} first (it is write-protected while unmounted: run "
            f"'chattr -i {target}' before moving them), then mount the pool again."
        )


def _run_best_effort(*commands: list[str]) -> None:
    """Run host commands whose failure only gets a warning; never raises."""
    for cmd in commands:
        try:
            result = host.run_on_host(cmd, timeout=30)
        except Exception as exc:
            logger.warning("%s failed: %s", " ".join(cmd), exc)
            continue
        if not result.ok:
            logger.warning("%s failed: %s", " ".join(cmd), result.stderr.strip())


def _prepare_btrfs_devices() -> None:
    """Wait for udev and register all btrfs devices so multi-device pools can mount."""
    _run_best_effort(["udevadm", "settle", "--timeout=10"], ["btrfs", "device", "scan"])


def _mount_by_uuid(uuid: str, target: str) -> None:
    result = host.run_on_host(
        ["mount", "-t", "btrfs", "-o", MOUNT_OPTIONS, f"UUID={uuid}", target],
        timeout=MOUNT_TIMEOUT,
    )
    if not result.ok:
        raise RuntimeError(
            f"Mounting {target} failed: {result.stderr.strip() or result.returncode}"
        )


def _umount(target: str) -> None:
    # Unmount until nothing is left: the same filesystem can be stacked on
    # the target if it was mounted twice (e.g. by systemd and by UNOS).
    for _ in range(3):
        result = host.run_on_host(["umount", target], timeout=UMOUNT_TIMEOUT)
        if not result.ok:
            message = result.stderr.strip() or f"exit status {result.returncode}"
            if "busy" in message.lower():
                raise PoolBusyError(
                    f"{target} is busy ({message}). Stop the shares, apps and "
                    "containers that use the pool and try again."
                )
            raise RuntimeError(f"Unmounting {target} failed: {message}")
        if not _target_mounted(target):
            return
    raise RuntimeError(f"{target} is still mounted after unmounting it three times")


def _check_uuid(uuid: str) -> str:
    if not _UUID_RE.fullmatch(uuid or ""):
        raise RuntimeError(f"Invalid filesystem UUID: {uuid!r}")
    return uuid.lower()


# --------------------------------------------------------------------------
# Containers that use a pool
# --------------------------------------------------------------------------


@contextmanager
def _docker_api() -> Iterator[Any]:
    """Low-level Docker API client (replaced in tests)."""
    client = docker.from_env(timeout=DOCKER_TIMEOUT)
    try:
        yield client.api
    finally:
        client.close()


def _container_name(container: dict[str, Any]) -> str:
    names = container.get("Names") or []
    if names:
        return str(names[0]).lstrip("/")
    return str(container.get("Id") or "?")[:12]


def _path_within(path: str, base: str) -> bool:
    path, base = path.rstrip("/"), base.rstrip("/")
    return path == base or path.startswith(base + "/")


def _containers_using(name: str, target: str) -> list[str] | None:
    """Running containers that keep data on the pool, by name.

    A container uses the pool when one of its mounts comes from the pool's
    mountpoint (or below it), also through a Docker volume that binds a
    folder there (_mount_uses), or when the app installer labelled it with
    the pool. Returns None when Docker cannot be asked.
    """
    try:
        with _docker_api() as api:
            running, bound = _running_containers(api)
    except Exception as exc:
        logger.warning("Could not list the running containers or their volumes: %s", exc)
        return None
    users = []
    for container in running:
        labels = container.get("Labels") or {}
        if labels.get(APP_POOL_LABEL) == name or any(
            _mount_uses(mount, bound, target) for mount in container.get("Mounts") or []
        ):
            users.append(_container_name(container))
    return sorted(users)


def _finished_within(
    seconds: float, thread_name: str, func: Callable[..., object], *args: object
) -> bool:
    """Run ``func(*args)`` in a worker thread and wait for it at most ``seconds``.

    Returns False if it is still running then; it is left running in a
    daemon thread, which never keeps the supervisor from exiting, and what it
    returns or raises later is ignored. Re-raises what ``func`` raised.
    """
    raised: list[BaseException] = []

    def run() -> None:
        try:
            func(*args)
        except BaseException as exc:  # noqa: BLE001 - re-raised in the waiting thread
            raised.append(exc)

    worker = threading.Thread(target=run, name=thread_name, daemon=True)
    worker.start()
    worker.join(seconds)
    if worker.is_alive():
        return False
    if raised:
        raise raised[0]
    return True


def _start_pool_apps(name: str) -> list[str]:
    """Start the pool's apps that Docker could not start at boot; never raises.

    Docker starts containers with restart policy always/unless-stopped when
    it starts. If the pool was not mounted yet, the start fails (the bind
    source is missing), Docker records the error and never retries. Apps
    that were stopped on purpose have no such error and are left alone.
    Each app first gets the checks of a start from the Apps page
    (``apps.router.prepare_app_start``); an app that fails them stays stopped.

    The checks read the pool's files without a timeout of their own. When
    they do not finish within APP_CHECK_TIMEOUT seconds (a disk that mounted
    but stopped answering), neither that app nor the pool's remaining apps
    are started. Docker calls, the start included, are bounded by the client's
    DOCKER_TIMEOUT. Runs in the background (:func:`_start_pool_apps_later`).
    """
    started: list[str] = []
    try:
        # Imported here so that loading the storage modules does not load the apps package.
        from ..apps.router import prepare_app_start

        with _docker_api() as api:
            for summary in api.containers(all=True, filters={"label": f"{APP_POOL_LABEL}={name}"}):
                container = _container_name(summary)
                try:
                    if summary.get("State") == "running":
                        continue
                    details = api.inspect_container(summary["Id"])
                    state = details.get("State") or {}
                    policy = ((details.get("HostConfig") or {}).get("RestartPolicy") or {}).get("Name")
                    if state.get("Running") or not state.get("Error"):
                        continue
                    if policy not in _BOOT_RESTART_POLICIES:
                        continue
                    if not _finished_within(
                        APP_CHECK_TIMEOUT,
                        f"{_APP_THREAD_NAME}:{name}:{container}",
                        prepare_app_start,
                        Container(attrs=details),
                    ):
                        logger.error(
                            "The checks before starting app %s of pool '%s' did not finish "
                            "within %s seconds; the pool's disk may not be responding. Not "
                            "starting it or the pool's other apps; start them on the Apps page "
                            "once the pool responds again.",
                            container,
                            name,
                            APP_CHECK_TIMEOUT,
                        )
                        break
                    api.start(summary["Id"])
                except HTTPException as exc:
                    logger.warning("Did not start app %s of pool '%s': %s", container, name, exc.detail)
                    continue
                except Exception as exc:
                    logger.warning("Could not start app %s of pool '%s': %s", container, name, exc)
                    continue
                started.append(container)
                logger.warning(
                    "Started app %s, which Docker could not start before pool '%s' was mounted",
                    container,
                    name,
                )
    except Exception as exc:
        logger.warning("Could not start the apps of pool '%s': %s", name, exc)
    return started


def _start_pool_apps_later(name: str) -> None:
    """Start, in a background thread, the apps of a pool that was just mounted; never raises.

    Their checks read the pool's files and can hang on a failing disk, so
    they must neither hold up the caller (a mount request, the supervisor's
    startup) nor run while the pool lock is held: call this after releasing
    it. See :func:`_start_pool_apps`.
    """
    try:
        threading.Thread(
            target=_start_pool_apps, args=(name,), name=f"{_APP_THREAD_NAME}:{name}", daemon=True
        ).start()
    except RuntimeError as exc:  # no thread could be started
        logger.warning("Could not start the apps of pool '%s': %s", name, exc)


def _filesystem_active(uuid: str) -> bool:
    """Whether the kernel still has the btrfs filesystem mounted in any mount namespace.

    ``/sys/fs/btrfs/<uuid>`` exists exactly while the filesystem is mounted
    somewhere, including private mounts of other namespaces that do not
    show up in the host's mount table.
    """
    try:
        return host.run_on_host(["test", "-d", f"/sys/fs/btrfs/{uuid}"], timeout=15).ok
    except Exception as exc:
        logger.warning("Could not check whether filesystem %s is still mounted: %s", uuid, exc)
        return False


def _mounted_uuid(here: list[disks.HostMount]) -> str | None:
    """UUID of the btrfs filesystem mounted at a target (from its mount table entries)."""
    btrfs_here = [m for m in here if m.fstype == "btrfs"]
    for mount in btrfs_here:
        if mount.uuid and _UUID_RE.fullmatch(mount.uuid):
            return mount.uuid.lower()
    if not btrfs_here:
        return None
    try:
        for fs in btrfs_filesystems():
            if any(_mount_matches(mount, fs) for mount in btrfs_here):
                return fs.uuid
    except Exception as exc:
        logger.warning("Could not identify the filesystem mounted at %s: %s", here[0].target, exc)
    return None


def _root_of(table: list[disks.HostMount]) -> tuple[str, str] | None:
    """The filesystem (MAJ:MIN) and its folder (FSROOT) that a mount table shows at "/".

    None when the table has no mount at "/" (a process that a chroot into a
    folder keeps from seeing it), mounts of more than one there, or no MAJ:MIN.
    """
    roots = {(m.majmin, m.fsroot or "/") for m in table if m.target == "/"}
    if len(roots) != 1:
        return None
    [(majmin, fsroot)] = roots
    return (majmin, fsroot) if majmin else None


def _pool_mounts_in(pid: int, target: str, mounts: list[disks.HostMount]) -> tuple[int, str | None]:
    """How the mount namespace of the host's process ``pid`` has the pool mounted (ADR-0288).

    ``mounts`` is the host's mount table before its umount. A mount of the
    pool's filesystem is recognised by the device number (MAJ:MIN) that
    every mount of a btrfs filesystem shows. A copy of the host's mount is
    one of that filesystem at ``target`` with the root that the host's mount
    had, in a namespace whose own root is the host's: the same filesystem and
    folder at "/" (:func:`_root_of`), as systemd makes the namespaces of
    services. Under such a copy lies the host's mountpoint, which the unmount
    write-protects (ADR-0034).

    Returns the number of copies, and why the namespace keeps the pool
    mounted otherwise, or None: the filesystem mounted anywhere else, such as
    a container's bind mount of a folder; mounted in a namespace with a root
    of its own, even as a copy at ``target``, such as a container's that
    binds a parent folder of the pool (``-v /mnt/pools:/mnt/pools``), whose
    apps use the pool there; or another filesystem mounted at ``target`` too,
    such as the cover with which systemd hides a path from a service (an
    umount by the path would remove the topmost of them). Raises
    RuntimeError when the table cannot be read, for example because the
    process has exited.
    """
    btrfs_here = [m for m in mounts if m.target == target and m.fstype == "btrfs" and m.majmin]
    if not btrfs_here:
        return 0, None
    majmin = btrfs_here[0].majmin
    roots = {m.fsroot or "/" for m in btrfs_here}
    table = disks.namespace_mounts(pid)
    host_root = _root_of(mounts)
    shares_host_root = host_root is not None and _root_of(table) == host_root
    copies, covered = 0, False
    for mount in table:
        if mount.fstype != "btrfs" or mount.majmin != majmin:
            covered = covered or mount.target == target
            continue
        if not shares_host_root or mount.target != target or (mount.fsroot or "/") not in roots:
            return copies, f"process {pid} has it mounted at {mount.target}"
        copies += 1
    if copies and covered:
        return copies, f"process {pid} has another filesystem mounted at {target} together with its copy"
    return copies, None


def _namespace_copies(target: str, mounts: list[disks.HostMount]) -> tuple[dict[int, int], str | None]:
    """The copies of the pool's host mount that other mount namespaces keep (ADR-0288).

    systemd runs services with sandboxing (PrivateTmp=, ProtectSystem=, ...)
    in mount namespaces of their own, made as copies of the host's when they
    start. A copy of the pool's mount can sit there in a peer group of its
    own, which the host's umount does not reach; the services do not use it.
    A container's namespace has a root of its own: the pool mounted there is
    in use (:func:`_pool_mounts_in`).

    ``mounts`` is the host's mount table before its umount. Returns the
    processes whose namespaces keep copies, each with how many are stacked at
    ``target``, and why a namespace keeps the pool mounted otherwise, or
    None. A namespace whose table cannot be read is left out (its process has
    most likely exited): what it may still hold keeps the filesystem active,
    which the caller checks.
    """
    if not any(m.target == target and m.fstype == "btrfs" and m.majmin for m in mounts):
        return {}, None
    try:
        pids = disks.mount_namespace_pids()
    except Exception as exc:
        logger.warning("Could not list the host's mount namespaces: %s", exc)
        return {}, None
    copies: dict[int, int] = {}
    for pid in pids:
        try:
            count, reason = _pool_mounts_in(pid, target, mounts)
        except Exception as exc:
            logger.warning("Could not read the mount table of process %d: %s", pid, exc)
            continue
        if reason:
            return copies, reason
        if count:
            copies[pid] = count
    return copies, None


def _umount_in_namespace(pid: int, target: str) -> str | None:
    """Unmount the topmost mount at ``target`` in process ``pid``'s namespace; why not, or None.

    The host's umount enters the namespace for the unmount alone
    (``--namespace``), so the namespace needs no umount of its own. Never lazily.
    """
    try:
        result = host.run_on_host(["umount", "--namespace", str(pid), target], timeout=UMOUNT_TIMEOUT)
    except subprocess.TimeoutExpired:
        return f"umount did not finish within {UMOUNT_TIMEOUT} seconds"
    except (OSError, subprocess.SubprocessError) as exc:
        return str(exc)
    return None if result.ok else result.stderr.strip() or f"exit status {result.returncode}"


def _unmount_namespace_copies(target: str, mounts: list[disks.HostMount], copies: dict[int, int]) -> str | None:
    """Unmount the copies in their namespaces; the first one that stays, or None.

    ``mounts`` is the host's mount table before its umount. After a failure
    the other copies are still unmounted, and the namespace is read again: a
    copy that the unmount of a peer took with it fails as not mounted, and
    the namespace of a process that exited is gone. Whether the filesystem is
    still in use is for the caller's check to say.
    """
    problem = None
    unmounted = []
    for pid, count in copies.items():
        failure = None
        for _ in range(count):
            failure = _umount_in_namespace(pid, target)
            if failure:
                break
        if failure:
            try:
                left, _ = _pool_mounts_in(pid, target, mounts)
            except Exception:
                left = 0
            if left:
                logger.warning("Could not unmount %s in the mount namespace of process %d: %s",
                               target, pid, failure)
                problem = problem or (
                    f"process {pid} keeps a copy of its mount, and unmounting it there "
                    f"failed: {failure.rstrip('.')}"
                )
                continue
        unmounted.append(str(pid))
    if unmounted:
        logger.warning(
            "Unmounted %s in the mount namespaces of processes %s, which kept copies of its mount",
            target,
            ", ".join(unmounted),
        )
    return problem


def _unmount_pool_target(name: str, target: str, mounts: list[disks.HostMount]) -> None:
    """Unmount whatever is mounted at the pool's mountpoint; PoolBusyError while in use.

    ``umount`` on the host only reports users in mount namespaces that
    receive its unmount (the host, the supervisor's rshared /mnt). A
    container's bind mount of a directory on the pool is a separate, private
    mount: the host's umount succeeds, the filesystem stays mounted inside
    the container and keeps being written to, and the disk must not be
    removed. Containers that Docker reports are refused up front. After the
    umount, the copies of the mount that services keep in mount namespaces of
    their own, whose root is the host's, are unmounted there
    (:func:`_namespace_copies`), unless a namespace has the filesystem mounted
    anywhere else, or has a root of its own like a container's; any other
    user is detected, and the pool is mounted again.
    """
    here = [m for m in mounts if m.target == target]
    if not here:
        return
    users = _containers_using(name, target)
    if users:
        raise PoolBusyError(
            f"Pool '{name}' is used by running containers ({', '.join(users)}). "
            "Stop them and try again."
        )
    uuid = _mounted_uuid(here)
    _umount(target)
    if uuid is None or not _filesystem_active(uuid):
        return
    try:
        if any(m.fstype == "btrfs" and (m.uuid or "").lower() == uuid for m in disks.host_mounts()):
            return  # also mounted elsewhere on the host, deliberately
    except Exception as exc:
        logger.warning("Could not read the host mount table: %s", exc)
    copies, reason = _namespace_copies(target, mounts)
    if copies and reason is None:
        reason = _unmount_namespace_copies(target, mounts, copies)
        if not _filesystem_active(uuid):
            return
    reason = reason or "for example by a container's bind mount"
    try:
        _mount_by_uuid(uuid, target)
    except Exception as exc:
        raise PoolBusyError(
            f"{target} was unmounted on the host, but the filesystem is still in use "
            f"elsewhere ({reason}), and mounting it again failed: {exc}. Stop the apps "
            "and containers that use the pool before removing its disks."
        ) from exc
    raise PoolBusyError(
        f"Pool '{name}' is still in use outside the host ({reason}), so it was mounted "
        "again. Stop the apps and containers that use the pool and try again."
    )


def _check_top_level(name: str, target: str) -> None:
    """Refuse a pool whose mount at ``target`` shows another subvolume than the top level.

    UNOS mounts pools without subvol=, which mounts the filesystem's default
    subvolume: the top level, unless ``btrfs subvolume set-default`` changed
    it, as on some filesystems made elsewhere. A pool's volumes are the
    subvolumes below its top level, and share folders need the top level
    too (_mounted_pool_target): PoolConflictError (HTTP 409, ADR-0283).
    """
    subvolume = _mounted_subvolume_id(target)
    if subvolume != _TOP_LEVEL_SUBVOLUME:
        raise PoolConflictError(
            f"Pool '{name}' was not mounted: its filesystem mounts subvolume {subvolume} by "
            "default, not its top level, where UNOS keeps volumes and share folders. It was "
            "unmounted again, with its label and the host's /etc/fstab as they were. To use "
            "it, make the top level its default subvolume on the host ('btrfs subvolume "
            "set-default 5' on a mount of it), then try again."
        )


def _set_label(target: str, label: str) -> None:
    """Give the btrfs filesystem mounted at ``target`` the label ``label`` (an import, ADR-0283).

    Through the mount, so that the kernel changes it: given a device instead,
    btrfs-progs writes the superblocks itself, also those of a filesystem
    that a mount namespace it does not see still has mounted.
    """
    result = host.run_on_host(["btrfs", "filesystem", "label", target, label], timeout=60)
    if not result.ok:
        raise RuntimeError(
            f"Setting the label of the filesystem at {target} failed: "
            f"{result.stderr.strip() or f'exit status {result.returncode}'}"
        )


def _remove_made_mountpoint(target: str) -> None:
    """Remove again the mountpoint that a refused or failed mount made; never raises."""
    try:
        _remove_mountpoint_dir(target)
    except Exception as exc:
        logger.warning("Could not remove %s again: %s", target, exc)


def _mount_and_persist(name: str, uuid: str, *, already_mounted: bool, relabel: bool = False) -> None:
    """Mount ``uuid`` at the pool's mountpoint on the host and keep it in fstab.

    A filesystem that this call mounts must show its top level there
    (_check_top_level); with ``relabel`` (an import), it then gets the
    pool's name as its label (_set_label). When either fails, the
    filesystem is unmounted again. When the mount, the check or the label
    fails, the fstab is as it was, and a mountpoint that this call made is
    removed again (ADR-0283); only after a failed mount of a pool that
    exists (Create, Mount) does it stay, write-protected like that of every
    pool that is not mounted. An import that fails leaves nothing behind.
    """
    target = pool_mountpoint(name)
    original = text = _read_fstab()
    conflicts = unmanaged_fstab_entries(text, target)
    if conflicts:
        raise PoolConflictError(
            f"The host /etc/fstab already mounts {target} through an entry UNOS does not "
            f"manage ({conflicts[0]}). Remove or comment out that line first."
        )
    made = not already_mounted and not host.run_on_host(["test", "-d", target], timeout=15).ok
    desired = set_managed_fstab_entry(text, name, uuid)
    if desired != text and name in parse_managed_fstab(text):
        # Replace an outdated entry (e.g. of an older pool with the same name)
        # before mounting, so systemd's unit no longer points at the old device.
        _write_fstab(desired, text)
        text = desired
    if not already_mounted:
        try:
            _ensure_mountpoint_dir(target)
            _prepare_btrfs_devices()
            _mount_by_uuid(uuid, target)
        except Exception:
            _write_fstab(original, text)  # an outdated entry replaced above comes back
            if made and relabel:
                _remove_made_mountpoint(target)
            raise
        try:
            _check_top_level(name, target)
            if relabel:
                _set_label(target, name)
        except Exception as exc:
            try:
                _umount(target)
            except Exception as umount_exc:
                raise RuntimeError(
                    f"{exc} Unmounting {target} again failed as well: {umount_exc}"
                ) from exc
            _write_fstab(original, text)
            if made:
                _remove_made_mountpoint(target)
            raise
    _write_fstab(desired, text)


def mount_pool(name: str) -> PoolInfo:
    """Mount a pool on the host by UUID and keep its managed fstab entry.

    This imports a pool found on the disks, too (ADR-0283), so a pool with a
    member on a boot or virtual device or on the system disk is refused
    (PoolConflictError, HTTP 409), as Create refuses such devices
    (ADR-0035, ADR-0225). If this call mounted the pool, the pool's apps
    that Docker could not start without it are started in the background;
    that part never fails or delays the mount.
    """
    name = validate_pool_name(name)
    target = pool_mountpoint(name)
    with _lock:
        mounts = disks.host_mounts()
        candidates = _pool_filesystems(name, mounts)
        if not candidates:
            raise PoolNotFoundError(f"Pool '{name}' not found")
        mounted_here = [
            fs for fs in candidates if any(m.target == target and _mount_matches(m, fs) for m in mounts)
        ]
        if mounted_here:
            fs = mounted_here[0]
        else:
            if len(candidates) > 1:
                raise PoolConflictError(
                    f"{len(candidates)} btrfs filesystems are labelled '{name}' "
                    f"({', '.join(c.uuid for c in candidates)}). Relabel or remove the duplicates first."
                )
            fs = candidates[0]
            if any(m.target == target for m in mounts):
                raise PoolConflictError(f"Another filesystem is mounted at {target}")
            if fs.missing_devices:
                raise PoolConflictError(
                    f"Pool '{name}' is missing {fs.missing_devices} device(s) and cannot be "
                    "mounted. Reconnect the missing disk(s) and try again."
                )
            problem = _device_problems([fs]).get(fs.uuid)
            if problem:
                raise PoolConflictError(f"Pool '{name}' cannot be mounted: {problem}.")
            _refuse_hidden_files(target)
        _mount_and_persist(name, _check_uuid(fs.uuid), already_mounted=bool(mounted_here))
    if not mounted_here:
        _start_pool_apps_later(name)  # after releasing the lock; never raises
    return get_pool(name)


def unmount_pool(name: str) -> PoolInfo:
    """Unmount a pool on the host and remove its managed fstab entry. Data is kept.

    PoolBusyError (HTTP 409) while host processes or containers use the pool.
    """
    name = validate_pool_name(name)
    target = pool_mountpoint(name)
    with _lock:
        mounts = disks.host_mounts()
        if not _pool_filesystems(name, mounts):
            raise PoolNotFoundError(f"Pool '{name}' not found")
        _unmount_pool_target(name, target, mounts)
        _forget_fstab_entry(name)
        guard_mountpoint(target)
        return get_pool(name)


def delete_pool(name: str) -> None:
    """Forget a pool: unmount it, drop its fstab entry and its empty mountpoint.

    The disks are not touched. The filesystem keeps its label and is listed
    again as an unmounted pool that can be mounted later. PoolBusyError
    (HTTP 409) while host processes or containers use the pool.
    """
    name = validate_pool_name(name)
    target = pool_mountpoint(name)
    with _lock:
        _unmount_pool_target(name, target, disks.host_mounts())
        _forget_fstab_entry(name)
        _remove_mountpoint_dir(target)


# --------------------------------------------------------------------------
# Pool creation
# --------------------------------------------------------------------------


def _check_identity(device: str, fact: disks.DeviceFacts, expected: DeviceIdentity) -> None:
    """Refuse a device path that no longer holds the disk the client selected."""
    seen = (disks.disk_id(expected.serial), disks.disk_id(expected.wwn), expected.size)
    found = (fact.serial, fact.wwn, fact.size)
    if seen != found:
        raise DeviceSafetyError(
            f"{device} is no longer the disk that was selected (selected: serial "
            f"{seen[0] or 'none'}, WWN {seen[1] or 'none'}, {seen[2]} bytes; now: serial "
            f"{found[0] or 'none'}, WWN {found[1] or 'none'}, {found[2]} bytes). Disks can "
            "change names when they are reconnected. Reload the disk list and select the "
            "disks again."
        )


def _check_pool_devices(
    devices: list[str], force: bool, identities: dict[str, DeviceIdentity] | None = None
) -> dict[str, disks.DeviceFacts]:
    """Refuse devices that must not be formatted. Returns fresh host facts."""
    facts = disks.get_device_facts(devices)
    for device in devices:
        fact = facts[device]
        if identities and device in identities:
            _check_identity(device, fact, identities[device])
        if fact.hidden:
            raise ValueError(f"{device} is a boot or virtual device and cannot be used for a pool")
        if fact.is_system:
            raise DeviceSafetyError(
                f"Refusing to use {device}: it is a system device ({fact.in_use_reason})"
            )
        if fact.in_use:
            raise DeviceSafetyError(f"Refusing to use {device}: {fact.in_use_reason}")
        if fact.has_data and not force:
            raise DeviceSafetyError(
                f"{device} contains data ({fact.data_description}). "
                "Set force to erase it and create the pool anyway."
            )
    return facts


def _check_name_available(name: str, facts: dict[str, disks.DeviceFacts]) -> None:
    target = pool_mountpoint(name)
    replaced = set().union(*(fact.member_paths for fact in facts.values()))
    for fs in btrfs_filesystems():
        if fs.label != name:
            continue
        # A filesystem with this label is fine only if it lives entirely on
        # the selected devices: mkfs is about to overwrite it.
        if fs.missing_devices or not set(fs.devices) <= replaced:
            raise PoolConflictError(
                f"A btrfs filesystem labelled '{name}' already exists "
                f"(on {', '.join(fs.devices) or 'missing devices'}). Choose another name."
            )
    if _target_mounted(target):
        raise PoolConflictError(f"Something is already mounted at {target}")
    _refuse_hidden_files(target)
    conflicts = unmanaged_fstab_entries(_read_fstab(), target)
    if conflicts:
        raise PoolConflictError(
            f"The host /etc/fstab already mounts {target} through an entry UNOS does not "
            f"manage ({conflicts[0]}). Remove or comment out that line first."
        )


def _new_filesystem_uuid(mkfs_output: str, device: str) -> str:
    match = re.search(r"^UUID:\s+(\S+)\s*$", mkfs_output, re.MULTILINE)
    if match and _UUID_RE.fullmatch(match.group(1)):
        return match.group(1).lower()
    result = host.run_on_host(["blkid", "-p", "-s", "UUID", "-o", "value", device], timeout=30)
    return _check_uuid(result.stdout.strip())


def create_pool(pool: PoolCreate) -> PoolInfo:
    """Create a btrfs pool, mount it on the host and make the mount persistent.

    Devices are checked against the host's current state (system disk,
    mounted, member of a mounted pool, existing data) and checked again right
    before mkfs runs.
    """
    name = validate_pool_name(pool.name)
    if pool.confirm_name != name:
        raise ValueError("confirm_name must match the pool name")
    if pool.raid_level not in PROFILE_MAP:
        raise ValueError(f"Unsupported RAID level: {pool.raid_level}")

    # Validate device paths to prevent injection
    validated_devices = validate_device_list(pool.devices)
    if len(set(validated_devices)) != len(validated_devices):
        raise ValueError("Each device can only be selected once")
    identities: dict[str, DeviceIdentity] = {}
    for identity in pool.device_identities:
        if identity.path not in validated_devices:
            raise ValueError(f"device_identities names {identity.path}, which is not selected")
        if identity.path in identities:
            raise ValueError(f"device_identities names {identity.path} more than once")
        identities[identity.path] = identity
    # Erasing data needs proof that every path still names the disk the client
    # chose: device names change when disks are reconnected. The web interface
    # always sends them; this refuses scripts and stale clients that do not.
    if pool.force and any(device not in identities for device in validated_devices):
        raise ValueError("device_identities must name every selected device when force is set")

    # Validate minimum device count for the selected RAID level
    required = MIN_DEVICES.get(pool.raid_level, 1)
    if len(validated_devices) < required:
        raise ValueError(
            f"RAID level '{pool.raid_level}' requires at least {required} devices, "
            f"but only {len(validated_devices)} provided"
        )

    target = pool_mountpoint(name)
    if any(char.isspace() for char in target):
        raise ValueError(f"Invalid pool mountpoint: {target!r}")
    data_profile, meta_profile = PROFILE_MAP[pool.raid_level]

    with _lock:
        facts = _check_pool_devices(validated_devices, pool.force, identities)
        _check_name_available(name, facts)
        # Re-check right before mkfs: the host may have changed meanwhile.
        facts = _check_pool_devices(validated_devices, pool.force, identities)
        overwrite = pool.force and any(fact.has_data for fact in facts.values())

        cmd = ["mkfs.btrfs", "-L", name, "-d", data_profile, "-m", meta_profile]
        if overwrite:
            # Without -f mkfs.btrfs refuses devices with existing signatures,
            # which is a last line of defence for disks that looked blank.
            cmd.append("-f")
        cmd += validated_devices
        result = host.run_on_host(cmd, timeout=MKFS_TIMEOUT)
        if not result.ok:
            raise RuntimeError(f"mkfs.btrfs failed: {result.stderr.strip()}")
        logger.info("Created btrfs pool '%s' on %s", name, ", ".join(validated_devices))

        uuid = _new_filesystem_uuid(result.stdout, validated_devices[0])
        _mount_and_persist(name, uuid, already_mounted=False)
        return get_pool(name)


# --------------------------------------------------------------------------
# Importing a filesystem found on the host's disks (ADR-0283)
# --------------------------------------------------------------------------


def _label_problem(fs: BtrfsFilesystem, chosen: dict[str, BtrfsFilesystem]) -> str | None:
    """Why ``fs`` is not the pool its label names, or None when it is.

    ``chosen`` is _pool_choices: of several filesystems with one label, at
    most one is the pool.
    """
    if not fs.label:
        return "missing"
    if not POOL_NAME_PATTERN.fullmatch(fs.label):
        return "invalid"
    if chosen.get(fs.label) is not fs:
        return "duplicate"
    return None


def importable_filesystems() -> list[ImportableFilesystem]:
    """The btrfs filesystems of the host that can be imported as pools (ADR-0283).

    Those that are neither the host's system filesystem nor mounted on the
    host, that are not the pool their label names (_label_problem; the
    pools are listed by list_pools, and Mount imports them), and that have
    no member on a boot or virtual device or on the system disk
    (_device_problems). RuntimeError (HTTP 500) when btrfs, the host's mount
    table, its fstab or lsblk cannot be read: without them, the host's own
    filesystems or a pool could be offered.
    """
    filesystems = btrfs_filesystems()
    mounts = disks.host_mounts()
    chosen = _pool_choices(filesystems, mounts, parse_managed_fstab(_read_fstab()))
    found: list[tuple[BtrfsFilesystem, str]] = []
    for fs in filesystems:
        problem = _label_problem(fs, chosen)
        if problem is None or _is_system_filesystem(fs, mounts):
            continue
        if any(_mount_matches(mount, fs) for mount in mounts):
            continue
        found.append((fs, problem))
    refused = _device_problems([fs for fs, _ in found])
    return [
        ImportableFilesystem(
            uuid=fs.uuid,
            label=fs.label,
            label_problem=problem,
            devices=list(fs.devices),
            total_devices=max(fs.total_devices, len(fs.devices) + fs.missing_devices),
            missing_devices=fs.missing_devices,
            size=fs.devices_size,
            used=fs.bytes_used,
        )
        for fs, problem in found
        if fs.uuid not in refused
    ]


def _containers_of_pool(name: str, target: str) -> list[str]:
    """Every container, running or not, that keeps data on a pool named ``name``, by name.

    The app installer labelled it with the pool (APP_POOL_LABEL), or one of
    its mounts comes from the pool's mountpoint or below (_mount_uses).
    Docker errors are raised.
    """
    with _docker_api() as api:
        containers, bound = _running_containers(api, stopped=True)
    return sorted(
        _container_name(container)
        for container in containers
        if (container.get("Labels") or {}).get(APP_POOL_LABEL) == name
        or any(_mount_uses(mount, bound, target) for mount in container.get("Mounts") or [])
    )


def _refuse_users_of_an_earlier_pool(name: str) -> None:
    """Refuse an import under the name of an earlier pool that shares, exports or apps still use.

    Remove Pool keeps a pool's SMB shares with their access (ADR-0248), NFS
    exports keep their folders, and apps their pool label and bind mounts:
    a filesystem imported under that name would serve them at once, and
    Docker would start the apps on it at the next boot, from folders that
    nobody decided to share (ADR-0283). Their data is on the earlier pool's
    disks. PoolConflictError (HTTP 409) that names them;
    DockerUnavailableError (HTTP 503) when Docker cannot be asked about the
    containers; RuntimeError (HTTP 500) when the shares or the exports
    cannot be read.
    """
    target = pool_mountpoint(name)
    try:
        shares = sorted({s.name for s in smb.list_shares() if _points_into(s.path, target)})
        exports = sorted({e.path for e in nfs.list_exports() if _points_into(e.path, target)})
    except Exception as exc:
        raise RuntimeError(
            f"Pool '{name}' was not imported: the SMB shares and NFS exports could not be read ({exc})"
        ) from exc
    try:
        containers = _containers_of_pool(name, target)
    except Exception as exc:
        logger.warning("Could not list the containers or their volumes: %s", exc)
        raise DockerUnavailableError(
            f"Pool '{name}' was not imported: Docker did not answer, so it is not known whether "
            f"apps keep data on a pool named '{name}'. Try again when Docker is running ({exc})."
        ) from exc
    users = [
        f"{kind} ({', '.join(names)})"
        for kind, names in (("SMB shares", shares), ("NFS exports", exports), ("containers", containers))
        if names
    ]
    if users:
        listed = users[-1] if len(users) == 1 else f"{', '.join(users[:-1])} and {users[-1]}"
        raise PoolConflictError(
            f"The name '{name}' still belongs to an earlier pool: {listed} use it, and their "
            "data is on that pool's disks. Imported under this name, this filesystem would take "
            "that pool's place for them. Choose another name, or remove them first."
        )


def _check_import(uuid: str, name: str) -> BtrfsFilesystem:
    """The filesystem ``uuid``, once it may become the pool ``name``; changes nothing.

    PoolNotFoundError (HTTP 404) when no btrfs filesystem of the host has the
    UUID. PoolConflictError (HTTP 409) for a filesystem that holds the host's
    system, has a member on a boot or virtual device or on the system disk,
    is mounted on the host or in another mount namespace, misses devices, is
    the pool its label names, or that UNOS manages under another name; and
    for a name that another filesystem of the host has as its label (with
    two, LABEL= in the host's fstab, or root= of its kernel, could name
    either), that UNOS manages for another filesystem, that SMB shares, NFS
    exports or containers of an earlier pool still use
    (_refuse_users_of_an_earlier_pool), or whose mountpoint is in use, in an
    fstab line UNOS does not manage, or holds files.
    """
    mounts = disks.host_mounts()
    filesystems = btrfs_filesystems()
    fs = next((candidate for candidate in filesystems if candidate.uuid == uuid), None)
    if fs is None:
        raise PoolNotFoundError(
            f"No btrfs filesystem with UUID {uuid} is on the host's disks. Attach them and "
            "reload the page."
        )
    if _is_system_filesystem(fs, mounts):
        raise PoolConflictError(f"Filesystem {uuid} holds the host's system and cannot be a pool.")
    problem = _device_problems([fs]).get(fs.uuid)
    if problem:
        raise PoolConflictError(f"Filesystem {uuid} cannot be a pool: {problem}.")
    mounted_at = sorted({mount.target for mount in mounts if _mount_matches(mount, fs)})
    if mounted_at:
        raise PoolConflictError(
            f"Filesystem {uuid} is mounted at {', '.join(mounted_at)}. Unmount it on the host first."
        )
    if host.run_on_host(["test", "-d", f"/sys/fs/btrfs/{uuid}"], timeout=15).ok:
        raise PoolConflictError(
            f"Filesystem {uuid} is still mounted in another mount namespace, for example by a "
            "container. Stop what uses it first."
        )
    if fs.missing_devices:
        raise PoolConflictError(
            f"Filesystem {uuid} is missing {fs.missing_devices} device(s) and cannot be imported. "
            "Reconnect the missing disk(s) and try again."
        )
    text = _read_fstab()
    entries = parse_managed_fstab(text)
    if _label_problem(fs, _pool_choices(filesystems, mounts, entries)) is None:
        raise PoolConflictError(f"Filesystem {uuid} is the pool '{fs.label}'. Mount it on the Storage page.")
    managed_as = next((e.name for e in entries.values() if e.uuid == uuid and e.name != name), None)
    if managed_as is not None:
        raise PoolConflictError(
            f"UNOS manages filesystem {uuid} as the pool '{managed_as}' (its /etc/fstab entry). "
            f"Import it as '{managed_as}'."
        )
    labelled = next((other for other in filesystems if other.uuid != uuid and other.label == name), None)
    if labelled is not None:
        raise PoolConflictError(
            f"A btrfs filesystem labelled '{name}' already exists "
            f"(on {', '.join(labelled.devices) or 'missing devices'}). Choose another name."
        )
    devices = [path for path in disks.labelled_devices(name) if path not in fs.devices]
    if devices:
        raise PoolConflictError(
            f"The host already has a filesystem labelled '{name}' (on {', '.join(devices)}). "
            "Choose another name."
        )
    entry = entries.get(name)
    if entry is not None and entry.has_entry and entry.uuid != uuid:
        raise PoolConflictError(
            f"UNOS manages the pool '{name}' on another filesystem ({entry.uuid or 'unknown'}). "
            "Choose another name."
        )
    if entry is None or entry.uuid != uuid:
        # Not the pool that UNOS manages under this name: what the name's earlier pool
        # left behind does not belong to this filesystem.
        _refuse_users_of_an_earlier_pool(name)
    target = pool_mountpoint(name)
    if any(mount.target == target for mount in mounts):
        raise PoolConflictError(f"Something is already mounted at {target}")
    conflicts = unmanaged_fstab_entries(text, target)
    if conflicts:
        raise PoolConflictError(
            f"The host /etc/fstab already mounts {target} through an entry UNOS does not "
            f"manage ({conflicts[0]}). Remove or comment out that line first."
        )
    _refuse_hidden_files(target)
    return fs


def import_pool(uuid: str, name: str) -> PoolInfo:
    """Import the btrfs filesystem ``uuid`` found on the host's disks as the pool ``name``.

    Nothing is formatted (ADR-0283). The filesystem is mounted by UUID at
    the pool's mountpoint, must show its top level there, gets ``name`` as
    its label through the mount and is kept in the host's fstab like every
    managed pool (ADR-0034); then udev reads its devices again, so that
    lsblk shows the label. PoolNotFoundError (HTTP 404) and
    PoolConflictError (HTTP 409), with nothing changed, as _check_import
    and _check_top_level say. Unlike a mount, the import starts no apps:
    under a name that apps of an earlier pool still use, it is refused, and
    those of the pool that UNOS manages under the name start from the Apps
    page or at the next boot.
    """
    uuid = validate_filesystem_uuid(uuid)
    name = validate_pool_name(name)
    with _lock:
        fs = _check_import(uuid, name)
        _mount_and_persist(name, uuid, already_mounted=False, relabel=True)
        # The kernel wrote the label: udev reads a device again on its own only
        # after a program that opened it for writing closes it.
        devices = [device for device in fs.devices if device.startswith("/dev/")]
        if devices:
            _run_best_effort(["udevadm", "trigger", "--action=change", *devices],
                             ["udevadm", "settle", "--timeout=10"])
    return get_pool(name)


# --------------------------------------------------------------------------
# Startup reconciliation
# --------------------------------------------------------------------------


def reconcile_pools() -> list[dict[str, str]]:
    """Bring the host's pool mounts into the state UNOS expects. Never raises.

    * Every managed fstab entry the host has not mounted is mounted. The host
      normally mounts them at boot, but ``nofail`` lets it give up on a disk
      that is slow or missing. Apps of such a pool that Docker could not
      start without it are started afterwards, in the background
      (:func:`_start_pool_apps_later`); this returns without waiting for them.
    * Every other pool (a labelled btrfs filesystem without a managed entry)
      that is not mounted gets its mountpoint write-protected, so shares and
      apps that still point there cannot fill the boot disk; it stays
      unmounted until an administrator mounts it.

    Returns one result per pool (``pool``, ``target``, ``status``:
    already_mounted / mounted / failed / unmanaged, ``error`` for failures,
    ``hidden_files`` when a pool was mounted over files).
    """
    results: list[dict[str, str]] = []
    with _lock:
        try:
            entries = parse_managed_fstab(_read_fstab())
            mounted_targets = {m.target for m in disks.host_mounts()}
        except Exception as exc:
            logger.error("Pool mount check skipped: %s", exc)
            return results
        results += _mount_managed_entries(entries, mounted_targets)
        results += _guard_unmanaged_pools(entries)
    # Once every pool is mounted: an app may keep data on more than one pool.
    for outcome in results:
        if outcome.get("status") == "mounted":
            _start_pool_apps_later(outcome["pool"])
    return results


def _mount_managed_entries(
    entries: dict[str, ManagedFstabEntry], mounted_targets: set[str]
) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    scanned = False
    for entry in entries.values():
        if not entry.has_entry:
            continue  # marker without an entry line
        outcome = {"pool": entry.name, "target": entry.target}
        results.append(outcome)
        if entry.target in mounted_targets:
            outcome["status"] = "already_mounted"
            logger.info("Pool '%s' is mounted at %s", entry.name, entry.target)
            continue
        try:
            if not scanned:
                _prepare_btrfs_devices()
                scanned = True
            _ensure_mountpoint_dir(entry.target)
            try:
                hidden = _mountpoint_entries(entry.target)
            except Exception as exc:
                logger.warning("%s", exc)
                hidden = []
            result = host.run_on_host(["mount", entry.target], timeout=MOUNT_TIMEOUT)
        except Exception as exc:
            outcome.update(status="failed", error=str(exc))
            logger.error("Could not mount pool '%s' at %s: %s", entry.name, entry.target, exc)
            continue
        if not result.ok:
            error = result.stderr.strip() or f"exit status {result.returncode}"
            outcome.update(status="failed", error=error)
            logger.error("Could not mount pool '%s' at %s: %s", entry.name, entry.target, error)
            continue
        outcome["status"] = "mounted"
        logger.warning(
            "Pool '%s' was not mounted at startup; mounted it at %s", entry.name, entry.target
        )
        if hidden:
            # Mounted anyway: the host mounts the entry at every boot regardless.
            outcome["hidden_files"] = _describe_entries(hidden)
            logger.error(
                "Pool '%s' was mounted over %d item(s) in %s on the host's boot disk (%s). "
                "They are hidden under the pool and still use space on the boot disk; "
                "unmount the pool and move them away.",
                entry.name,
                len(hidden),
                entry.target,
                _describe_entries(hidden),
            )
    return results


def _guard_unmanaged_pools(entries: dict[str, ManagedFstabEntry]) -> list[dict[str, str]]:
    """Write-protect the mountpoints of unmounted pools that have no managed entry."""
    try:
        filesystems = btrfs_filesystems()
        mounts = disks.host_mounts()
    except Exception as exc:
        logger.error("Could not check for pools that are not mounted: %s", exc)
        return []
    results: list[dict[str, str]] = []
    seen: set[str] = set()
    for fs in filesystems:
        name = fs.label
        if not name or not POOL_NAME_PATTERN.fullmatch(name) or name in seen:
            continue
        if _is_system_filesystem(fs, mounts):
            continue
        entry = entries.get(name)
        if entry is not None and entry.has_entry:
            continue  # managed: mounted (or reported) above
        target = pool_mountpoint(name)
        if any(m.target == target for m in mounts):
            continue  # mounted outside UNOS's control; nothing lands on the boot disk
        seen.add(name)
        guarded = guard_mountpoint(target)
        results.append({"pool": name, "target": target, "status": "unmanaged"})
        logger.warning(
            "Pool '%s' (filesystem %s) is not mounted and has no UNOS-managed entry in the "
            "host's /etc/fstab, so it is not mounted at boot either.%s Mount it on the "
            "Storage page to use it.",
            name,
            fs.uuid,
            f" {target} was write-protected so that nothing is written to the boot disk."
            if guarded
            else "",
        )
    return results


async def reconcile_pools_on_startup() -> list[dict[str, str]]:
    """Async entry point for the app lifespan; never raises.

    Returns once the pools are mounted (every host command it runs has a
    timeout); their apps are started in the background.
    """
    try:
        return await asyncio.to_thread(reconcile_pools)
    except Exception as exc:  # reconcile_pools already handles its errors
        logger.error("Pool mount check failed: %s", exc)
        return []


# --------------------------------------------------------------------------
# Volumes and scrub
# --------------------------------------------------------------------------


# Seconds a btrfs subvolume or quota group command may take.
VOLUME_TIMEOUT = 120
# Id of the top-level subvolume of every btrfs filesystem (BTRFS_FS_TREE_OBJECTID).
_TOP_LEVEL_SUBVOLUME = 5
# A line of `btrfs subvolume list`; the path is relative to the top level
# (empty when the name starts with a line break).
_SUBVOLUME_LINE_RE = re.compile(
    r"ID (?P<id>\d+) gen \d+ top level (?P<parent>\d+) path (?P<path>.*)"
)
# Lines of `btrfs subvolume show` ("\tSubvolume ID: \t\t257").
_SHOW_FIELD_RE = re.compile(r"\s*(?P<key>Subvolume ID|Parent ID):\s*(?P<value>\d+)\s*")


class VolumeNotFoundError(PoolNotFoundError):
    """The pool has no volume with this name (HTTP 404)."""


class DockerUnavailableError(RuntimeError):
    """Docker could not be asked which containers are running (HTTP 503)."""


def _mounted_subvolume_id(target: str) -> int:
    """Id of the subvolume mounted at ``target``, from ``btrfs inspect-internal rootid``.

    UNOS mounts pools without ``subvol=``, which mounts the filesystem's
    default subvolume: the top level, unless the default was changed (e.g.
    on a filesystem made elsewhere). A pool can also be mounted by hand with
    ``subvol=``. RuntimeError (HTTP 500) when the id cannot be read.
    """
    result = host.run_on_host(
        ["btrfs", "inspect-internal", "rootid", target], timeout=VOLUME_TIMEOUT
    )
    output = result.stdout.strip()
    if not result.ok or not re.fullmatch(r"[0-9]+", output):
        raise RuntimeError(
            f"Could not find out which subvolume of the pool is mounted at {target}: "
            f"{result.stderr.strip() or output or f'exit status {result.returncode}'}"
        )
    return int(output)


def _mounted_pool_target(name: str, *, write: bool = False) -> str:
    """The mountpoint of a pool, checked to be mounted there on the host.

    Volumes are created, listed and deleted below it, by commands that run
    on the host, where the mount was checked. PoolNotFoundError (HTTP 404)
    for an unknown pool. PoolConflictError (HTTP 409):

    * when the pool's filesystem is not mounted at its mountpoint: the
      directory is then on the host's boot disk, and a volume made there
      would not be on the pool;
    * with ``write`` (create, delete), when the pool is mounted read-only,
      on purpose or by btrfs after an error: btrfs refuses to change it;
    * when the mounted subvolume is not the top level: volumes are the
      subvolumes directly below the top level, so they would be listed
      wrongly, and a new one would not be listed at all.
    """
    target = pool_mountpoint(name)
    mounts = disks.host_mounts()
    candidates = _pool_filesystems(name, mounts)
    if not candidates:
        raise PoolNotFoundError(f"Pool '{name}' not found")
    here = [m for m in mounts if m.target == target and any(_mount_matches(m, fs) for fs in candidates)]
    if not here:
        raise PoolConflictError(
            f"Pool '{name}' is not mounted at {target}. Mount it on the Storage page first."
        )
    # Like PoolInfo.read_only, which the Storage page uses to disable these operations.
    if write and any(m.read_only for m in here):
        message = f"Pool '{name}' is mounted read-only; volumes cannot be created or deleted."
        if any(m.forced_read_only for m in here):
            message += " btrfs made it read-only after an error; check the kernel log (dmesg)."
        raise PoolConflictError(message)
    subvolume = _mounted_subvolume_id(target)
    if subvolume != _TOP_LEVEL_SUBVOLUME:
        raise PoolConflictError(
            f"Pool '{name}' is mounted at subvolume {subvolume}, not the top level; "
            "volumes are not available for this pool."
        )
    return target


def _list_subvolumes(target: str) -> list[tuple[int, int, str]]:
    """Every subvolume of the filesystem mounted at ``target``: (id, parent id, path).

    The paths are relative to the top level. Whatever can write in the pool
    (an app's container, an SMB user) chooses names, and they are bytes:
    bytes that are not UTF-8 become U+FFFD, which no volume name contains.
    A name with a line break can add lines, but it cannot hide its own
    (_newest_subvolume_id relies on that): lines are split at "\\n" only,
    where btrfs-progs ends them (str.splitlines() also splits at "\\v",
    "\\x85", "\\u2028" and others), and a line whose name starts with a line
    break is kept, with an empty path.
    """
    result = host.run_on_host(
        ["btrfs", "subvolume", "list", target], timeout=VOLUME_TIMEOUT, errors="replace"
    )
    if not result.ok:
        raise RuntimeError(
            f"Listing the subvolumes of {target} failed: "
            f"{result.stderr.strip() or f'exit status {result.returncode}'}"
        )
    subvolumes = []
    # subprocess turns a "\r" in a name into "\n" as well.
    for line in result.stdout.split("\n"):
        match = _SUBVOLUME_LINE_RE.fullmatch(line)
        if match:
            subvolumes.append(
                (int(match.group("id")), int(match.group("parent")), match.group("path"))
            )
    return subvolumes


def _pool_volumes(target: str) -> dict[str, int]:
    """The volumes of the pool mounted at ``target``: name -> subvolume id.

    A volume is a subvolume in the pool's top directory whose name is a
    valid volume name. Only pools whose top level is mounted get here (see
    _mounted_pool_target), so these are the subvolumes whose parent is the
    top level and whose path is a single component. Snapshots (below
    ``.snapshots``) and subvolumes nested deeper are not volumes.
    """
    return {
        path: subvol_id
        for subvol_id, parent, path in _list_subvolumes(target)
        if parent == _TOP_LEVEL_SUBVOLUME and VOLUME_NAME_PATTERN.fullmatch(path)
    }


def _newest_subvolume_id(target: str) -> int:
    """The highest subvolume id of the filesystem mounted at ``target`` (5 if it has none).

    btrfs gives a new subvolume a higher id than every existing one. A line
    forged in a name can only raise the result: create_volume then sets no
    quota and keeps the new volume, instead of limiting or removing one that
    existed before.
    """
    return max(
        (subvol_id for subvol_id, _, _ in _list_subvolumes(target)),
        default=_TOP_LEVEL_SUBVOLUME,
    )


def _check_volume_id(
    pool: str, target: str, name: str, subvol_id: int, *, outcome: str = "was not deleted"
) -> None:
    """Make sure that subvolume ``subvol_id`` is the volume ``name`` itself.

    ``btrfs subvolume list`` prints names as they are. Whatever can write in
    the pool (an app's container, for one) can create a subvolume whose name
    holds a line break and a forged list line, which gives a volume name (or
    a phantom one) the id of any other subvolume. ``btrfs subvolume show -r``
    prints what the filesystem has for the id: first its path from the top
    level, which must be the volume name, then its id and parent.
    PoolConflictError (HTTP 409) when they differ; its message starts with
    "Volume '<name>' <outcome>".
    """
    result = host.run_on_host(
        ["btrfs", "subvolume", "show", "-r", str(subvol_id), target],
        timeout=VOLUME_TIMEOUT,
        # Its path and the paths of its snapshots: names anyone can choose (_list_subvolumes).
        errors="replace",
    )
    if not result.ok:
        raise RuntimeError(
            f"Checking subvolume {subvol_id} of {target} failed: "
            f"{result.stderr.strip() or f'exit status {result.returncode}'}"
        )
    path, *lines = result.stdout.split("\n")
    facts: dict[str, str] = {}
    for line in lines:
        match = _SHOW_FIELD_RE.fullmatch(line)
        if match:
            facts.setdefault(match.group("key"), match.group("value"))
    if (path, facts.get("Subvolume ID"), facts.get("Parent ID")) != (
        name,
        str(subvol_id),
        str(_TOP_LEVEL_SUBVOLUME),
    ):
        raise PoolConflictError(
            f"Volume '{name}' {outcome}: the subvolume list of pool '{pool}' gives it "
            f"the id {subvol_id}, which belongs to another subvolume. A subvolume whose "
            "name contains a line break can cause this."
        )


def _points_into(path: object, volume_path: str) -> bool:
    """Whether the absolute ``path`` is the volume at ``volume_path`` or inside it."""
    if not isinstance(path, str) or not path.startswith("/"):
        return False
    # normpath keeps a leading "//" (POSIX leaves its meaning open); Linux reads it as "/".
    return _path_within("/" + os.path.normpath(path).lstrip("/"), volume_path)


def _local_volume(mount: dict[str, Any]) -> str | None:
    """The name of the Docker volume of the local driver that a container mount uses, or None."""
    if mount.get("Type") == "volume" and mount.get("Driver") == "local" and mount.get("Name"):
        return str(mount["Name"])
    return None


def _bound_folder(volume: dict[str, Any]) -> object:
    """The host folder that a volume of the local driver binds, or None.

    Such a volume, made with the options type=none, o=bind and device=<folder>
    (driver_opts in a compose file), mounts the folder; ``docker volume
    inspect`` shows the options.
    """
    options = volume.get("Options") or {}
    if {"bind", "rbind"} & set(str(options.get("o") or "").split(",")):
        return options.get("device")
    return None


def _running_containers(api: Any, *, stopped: bool = False) -> tuple[list[dict[str, Any]], dict[str, object]]:
    """The running containers (with ``stopped``, every container), and the host
    folder that each Docker volume of the local driver they use binds (volume
    name -> folder or None).

    Docker errors are raised.
    """
    running = api.containers(all=stopped)
    bound: dict[str, object] = {}
    for container in running:
        for mount in container.get("Mounts") or []:
            volume = _local_volume(mount)
            if volume is not None and volume not in bound:
                bound[volume] = _bound_folder(api.inspect_volume(volume))
    return running, bound


def _mount_uses(mount: dict[str, Any], bound: dict[str, object], path: str) -> bool:
    """Whether a container's mount is ``path`` or a folder in it.

    Docker reports the host path of a bind mount as the mount's Source; for
    a Docker volume of the local driver that binds a folder, the Source is
    the volume's own folder below /var/lib/docker/volumes, and the folder
    comes from ``bound`` (_running_containers).
    """
    local = _local_volume(mount)
    return _points_into(mount.get("Source"), path) or (
        local is not None and _points_into(bound.get(local), path)
    )


def _refuse_used_volume(name: str, volume_path: str) -> None:
    """Refuse to delete the volume at ``volume_path`` while something uses it.

    Users are running containers with a mount from the volume or from a
    folder in it, also through a Docker volume that binds such a folder
    (_mount_uses), and SMB shares and NFS exports of the volume or of a
    folder in it. Deleting the subvolume would leave them with a dead
    directory. A container, share or export of a parent folder (the whole
    pool, say) does not count: it keeps working without the volume. The app
    label (APP_POOL_LABEL) is not used either: it names a pool, so it would
    match every app of the pool.

    PoolConflictError (HTTP 409) that names the users. DockerUnavailableError
    (HTTP 503) when Docker cannot be asked about the containers or their
    volumes: an unmount goes ahead without Docker because a check afterwards
    finds remaining users and mounts the pool again (_unmount_pool_target),
    but a deleted subvolume cannot be brought back. RuntimeError (HTTP 500)
    when the shares cannot be read.
    """
    try:
        with _docker_api() as api:
            running, bound = _running_containers(api)
    except Exception as exc:
        logger.warning("Could not list the running containers or their volumes: %s", exc)
        raise DockerUnavailableError(
            f"Volume '{name}' was not deleted: Docker did not answer, so it is not known whether "
            f"a running container uses it. Try again when Docker is running ({exc})."
        ) from exc
    containers = sorted(
        _container_name(container)
        for container in running
        if any(_mount_uses(mount, bound, volume_path) for mount in container.get("Mounts") or [])
    )
    try:
        shares = sorted(s.name for s in smb.list_shares() if _points_into(s.path, volume_path))
        exports = sorted({e.path for e in nfs.list_exports() if _points_into(e.path, volume_path)})
    except Exception as exc:
        raise RuntimeError(
            f"Volume '{name}' was not deleted: the SMB shares and NFS exports could not be "
            f"read ({exc})"
        ) from exc
    users = [
        f"{kind} ({', '.join(names)})"
        for kind, names in (
            ("running containers", containers),
            ("SMB shares", shares),
            ("NFS exports", exports),
        )
        if names
    ]
    if users:
        listed = users[-1] if len(users) == 1 else f"{', '.join(users[:-1])} and {users[-1]}"
        raise PoolConflictError(
            f"Volume '{name}' is used by {listed}. Stop or remove them and try again."
        )


class NewVolumeReplacedError(PoolConflictError):
    """The subvolume with a new volume's name existed before the volume (HTTP 409)."""


def _delete_volume(
    pool: str,
    target: str,
    name: str,
    *,
    refuse_if_used: bool = True,
    created_after: int | None = None,
) -> None:
    """Delete the volume ``name`` of the pool mounted at ``target`` by its subvolume id.

    btrfs-progs resolves a path argument (realpath), so deleting by path
    would follow a symbolic link that SMB users or apps planted in the pool
    under the volume's name to any other subvolume. The id comes from the
    pool's own subvolume list instead, checked against the filesystem
    (:func:`_check_volume_id`). Unless ``refuse_if_used`` is False, the
    volume's users are checked right before the delete
    (:func:`_refuse_used_volume`). With ``created_after`` (a subvolume id
    from before a new volume was created), only a subvolume with a higher id
    is deleted: NewVolumeReplacedError otherwise.
    """
    subvol_id = _pool_volumes(target).get(name)
    if subvol_id is None:
        raise VolumeNotFoundError(f"Volume '{name}' not found in pool '{pool}'")
    # btrfs gives a new subvolume a higher id than every existing one.
    if created_after is not None and subvol_id <= created_after:
        raise NewVolumeReplacedError(
            f"The new volume was not removed: the subvolume named '{name}' now (id "
            f"{subvol_id}) existed before it was created."
        )
    _check_volume_id(pool, target, name, subvol_id)
    if refuse_if_used:
        _refuse_used_volume(name, os.path.join(target, name))
    result = host.run_on_host(
        ["btrfs", "subvolume", "delete", "--subvolid", str(subvol_id), target],
        timeout=VOLUME_TIMEOUT,
        errors="replace",  # it prints the subvolume's path, which may have changed by then
    )
    if not result.ok:
        message = result.stderr.strip() or f"exit status {result.returncode}"
        if "not empty" in message.lower():
            raise PoolConflictError(
                f"Volume '{name}' contains snapshots or other subvolumes ({message}). "
                "Delete them first."
            )
        if "busy" in message.lower():
            raise PoolConflictError(f"Volume '{name}' is in use ({message}).")
        raise RuntimeError(f"Deleting volume '{name}' failed: {message}")
    logger.info("Deleted volume '%s' of pool '%s'", name, pool)


def _quotas_not_enabled(reason: str) -> bool:
    """Whether ``btrfs qgroup limit`` failed because quotas are not enabled.

    The kernel answers ENOTCONN; btrfs-progs prints its text ("Transport
    endpoint is not connected") or, in newer versions, "quota not enabled".
    """
    reason = reason.lower()
    return "not connected" in reason or "not enabled" in reason


def _quotas_enabled(target: str) -> bool:
    """Whether quotas are enabled on the btrfs filesystem mounted at ``target``.

    Without them, ``btrfs qgroup show`` exits with status 1 and prints
    "ERROR: can't list qgroups: quotas not enabled" (btrfs-progs 4.20 to
    6.14). RuntimeError (HTTP 500) when it fails for another reason.
    """
    result = host.run_on_host(
        ["btrfs", "qgroup", "show", target],
        timeout=VOLUME_TIMEOUT,
        # btrfs-progs 6 lists the subvolume paths: names anyone can choose (_list_subvolumes).
        errors="replace",
    )
    if result.ok:
        return True
    reason = result.stderr.strip() or f"exit status {result.returncode}"
    if result.returncode == 1 and "quotas not enabled" in reason:
        return False
    raise RuntimeError(f"Could not find out whether quotas are enabled on {target}: {reason}")


def _limit_new_volume(pool: str, target: str, name: str, quota: int, newest: int) -> str | None:
    """Set the quota of the volume ``name`` created a moment ago, by its subvolume id.

    ``btrfs qgroup limit <size> <path>`` limits whatever has the name when it
    runs, and whatever can rename entries in the pool's top directory could
    have swapped an existing subvolume in under the name since the create.
    The quota group of the new subvolume (0/<id>) is limited instead; the
    mountpoint after it only names the filesystem. btrfs gives a new
    subvolume a higher id than every existing one, and ``newest`` is the
    highest id before the create: nothing is limited when the name does not
    lead to a newer subvolume.

    Returns None when the quota is set, otherwise why not (the caller then
    removes the new volume again). PoolConflictError (HTTP 409) when the
    subvolume list gives the name the id of another subvolume.
    """
    new_id = _pool_volumes(target).get(name)
    if new_id is None or new_id <= newest:
        return "it was renamed or replaced right after it was created"
    _check_volume_id(pool, target, name, new_id, outcome="was created without its quota")
    result = host.run_on_host(
        ["btrfs", "qgroup", "limit", str(quota), f"0/{new_id}", target], timeout=VOLUME_TIMEOUT
    )
    if result.ok:
        return None
    return result.stderr.strip() or f"exit status {result.returncode}"


def create_volume(pool_name: str, volume: VolumeCreate) -> VolumeInfo:
    """Create a volume: a btrfs subvolume at ``<mountpoint>/<name>`` of a mounted pool.

    PoolNotFoundError (HTTP 404) for an unknown pool. PoolConflictError
    (HTTP 409), and nothing is created, when the pool is not mounted,
    mounted read-only or not at its top level (see _mounted_pool_target),
    something with the volume's name exists in the pool, or a quota is asked
    for and quotas are not enabled on the pool. The quota is set on the new
    subvolume by its id (_limit_new_volume). PoolConflictError as well when
    the quota cannot be set: the new subvolume is removed again then, but
    only if the subvolume with its name is still the new one, not one that
    existed before (whatever can rename entries in the pool's top directory
    could have given it that name). The new volume stays without its quota
    (409) when the subvolume list gives its name the id of another subvolume.
    """
    pool = validate_pool_name(pool_name)
    name = validate_volume_name(volume.name)
    with _lock:
        target = _mounted_pool_target(pool, write=True)
        path = os.path.join(target, name)
        # -L as well: btrfs cannot create a subvolume over a dangling symbolic link.
        if any(host.run_on_host(["test", flag, path], timeout=15).ok for flag in ("-e", "-L")):
            raise PoolConflictError(f"'{name}' already exists in pool '{pool}'")
        newest = None
        if volume.quota is not None:
            if not _quotas_enabled(target):
                raise PoolConflictError(
                    f"Volume '{name}' was not created: quotas are not enabled on pool '{pool}'. "
                    f"Enable them with 'btrfs quota enable {target}' on the host and try "
                    "again, or create the volume without a quota."
                )
            # The quota and the rollback below go only to a subvolume newer
            # than every one here now.
            newest = _newest_subvolume_id(target)
        result = host.run_on_host(["btrfs", "subvolume", "create", path], timeout=VOLUME_TIMEOUT)
        if not result.ok:
            raise RuntimeError(
                f"Creating volume '{name}' failed: "
                f"{result.stderr.strip() or f'exit status {result.returncode}'}"
            )
        logger.info("Created volume '%s' in pool '%s'", name, pool)
        if volume.quota is not None:
            reason = _limit_new_volume(pool, target, name, volume.quota, newest)
            if reason is not None:
                try:
                    # Created a moment ago: removed without the in-use check (it needs Docker).
                    _delete_volume(pool, target, name, refuse_if_used=False, created_after=newest)
                except NewVolumeReplacedError as exc:
                    raise PoolConflictError(
                        f"Could not set the quota of volume '{name}': {reason}. {exc}"
                    ) from exc
                except VolumeNotFoundError as exc:
                    raise PoolConflictError(
                        f"Could not set the quota of volume '{name}': {reason}. The new volume "
                        f"was not removed: nothing in pool '{pool}' is named '{name}' now."
                    ) from exc
                except Exception as exc:
                    raise RuntimeError(
                        f"Could not set the quota of volume '{name}' ({reason}), and removing "
                        f"the new volume again failed ({exc}). Delete it and try again."
                    ) from exc
                hint = ""
                if _quotas_not_enabled(reason):
                    hint = (
                        f" Quotas are not enabled on pool '{pool}': enable them with "
                        f"'btrfs quota enable {target}' on the host, then try again."
                    )
                raise PoolConflictError(
                    f"Could not set the quota of volume '{name}': {reason}.{hint} "
                    "The volume was removed again."
                )
    return VolumeInfo(name=name, pool=pool, path=path, quota=volume.quota)


def list_volumes(pool_name: str) -> list[VolumeInfo]:
    """List the volumes of a mounted pool, read-only ones included.

    HTTP 409 when it is not mounted or not at its top level (see _mounted_pool_target).
    """
    pool = validate_pool_name(pool_name)
    target = _mounted_pool_target(pool)
    return [
        VolumeInfo(name=name, pool=pool, path=os.path.join(target, name))
        for name in sorted(_pool_volumes(target))
    ]


def delete_volume(pool_name: str, volume_name: str) -> None:
    """Delete a volume of a mounted pool, with everything in it.

    PoolNotFoundError or VolumeNotFoundError (HTTP 404) for an unknown pool
    or volume. PoolConflictError (HTTP 409) when the pool is not mounted,
    mounted read-only or not at its top level, or the volume is used by a
    running container, an SMB share or an NFS export, is busy, or holds
    snapshots or other subvolumes. DockerUnavailableError (HTTP 503) when
    Docker cannot be asked about the running containers and their volumes.
    Nothing is deleted in these cases.
    """
    pool = validate_pool_name(pool_name)
    name = validate_volume_name(volume_name)
    with _lock:
        _delete_volume(pool, _mounted_pool_target(pool, write=True), name, refuse_if_used=True)


# btrfs scrub start returns once the scrub runs in the background; status and
# cancel return at once too, unless the filesystem hangs. A btrfs that does not
# return is killed after this; one stuck in the kernel on a hung filesystem is
# still waited for until the kernel lets it go.
SCRUB_COMMAND_TIMEOUT = 60


def start_scrub(pool_name: str) -> ScrubStatus:
    """Start a btrfs scrub on a pool; return once the scrub runs.

    btrfs forks the process that runs the scrub and returns at once, but that
    process keeps the standard output and error of btrfs until the scrub
    ends, hours later on a large pool (btrfs-progs cmds/scrub.c). Read through
    pipes, they would end only then: the error output goes to a temporary
    file (in /tmp, RAM), the output (a line saying that the scrub started)
    nowhere.
    """
    mountpoint = os.path.join(settings.pools_base_path, pool_name)

    with tempfile.TemporaryFile() as stderr:
        result = subprocess.run(
            ["btrfs", "scrub", "start", mountpoint],
            stdout=subprocess.DEVNULL,
            stderr=stderr,
            timeout=SCRUB_COMMAND_TIMEOUT,
        )
        stderr.seek(0)
        error = stderr.read().decode(errors="replace")
    if result.returncode != 0:
        raise RuntimeError(f"Scrub start failed: {error}")

    try:
        return get_scrub_status(pool_name)
    except RuntimeError as exc:
        # btrfs started it: the scrub runs, and its progress is read at the next request.
        logger.warning("Started the scrub of %s, but could not read its status: %s", pool_name, exc)
        return ScrubStatus(pool=pool_name, status="running")


def get_scrub_status(pool_name: str) -> ScrubStatus:
    """The pool's scrub as ``btrfs scrub status`` reports it (ADR-0272).

    idle when no scrub is recorded; RuntimeError when btrfs cannot report it.
    """
    mountpoint = os.path.join(settings.pools_base_path, pool_name)

    result = subprocess.run(
        ["btrfs", "scrub", "status", mountpoint],
        capture_output=True,
        text=True,
        timeout=SCRUB_COMMAND_TIMEOUT,
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

    # No scrub recorded.
    if "no stats available" in output.lower():
        return ScrubStatus(
            pool=pool_name,
            status="idle",
        )
    # Not "idle": btrfs could not report the status (ADR-0272).
    if result.returncode != 0:
        raise RuntimeError(
            f"Scrub status failed: {result.stderr.strip() or f'exit status {result.returncode}'}"
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

    # "Error summary:    read=1 csum=12", or "no errors found": every kind counts (ADR-0272).
    error_match = re.search(r"Error summary:\s*(.+?)(?:\n|$)", output)
    if error_match:
        error_text = error_match.group(1).strip()
        if "no errors" not in error_text.lower():
            counts = [int(count) for count in re.findall(r"\w+=(\d+)", error_text)]
            errors = sum(counts) if counts else 1  # some error, in a form not known here

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
        timeout=SCRUB_COMMAND_TIMEOUT,
    )
    # Note: cancel returns non-zero if no scrub is running, which is fine
    if result.returncode != 0 and "not running" not in result.stderr.lower():
        raise RuntimeError(f"Scrub cancel failed: {result.stderr}")

    try:
        return get_scrub_status(pool_name)
    except RuntimeError as exc:
        if result.returncode != 0:
            raise  # it was not running: nothing tells how it ended
        # btrfs cancelled it, and the kernel has stopped it.
        logger.warning("Cancelled the scrub of %s, but could not read its status: %s", pool_name, exc)
        return ScrubStatus(pool=pool_name, status="aborted")


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
