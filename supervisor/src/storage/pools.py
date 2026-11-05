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
mounted (and written to) even after the host's mountpoint is gone.

Deleting or unmounting a pool never touches the disks: the filesystem stays
intact and can be mounted again.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
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
    validate_device_list,
    validate_pool_name,
)
from . import disks
from .schemas import (
    DeviceIdentity,
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
    result = host.run_on_host(["btrfs", "filesystem", "show", "--raw"], timeout=60)
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
    """List all btrfs pools (filesystems labelled with a valid pool name)."""
    filesystems = btrfs_filesystems()
    mounts = _mounts_or_none()
    managed = _managed_entries_or_none()
    return [
        _pool_info(fs, mounts, managed)
        for fs in filesystems
        if fs.label
        and POOL_NAME_PATTERN.fullmatch(fs.label)
        and not _is_system_filesystem(fs, mounts)
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
    it cannot be read.
    """
    result = host.run_on_host(
        ["find", target, "-mindepth", "1", "-maxdepth", "1", "-print0"], timeout=60
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


def _prepare_btrfs_devices() -> None:
    """Wait for udev and register all btrfs devices so multi-device pools can mount."""
    for cmd in (["udevadm", "settle", "--timeout=10"], ["btrfs", "device", "scan"]):
        try:
            result = host.run_on_host(cmd, timeout=30)
        except Exception as exc:
            logger.warning("%s failed: %s", " ".join(cmd), exc)
            continue
        if not result.ok:
            logger.warning("%s failed: %s", " ".join(cmd), result.stderr.strip())


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
    mountpoint (or below it) or when the app installer labelled it with the
    pool. Returns None when Docker cannot be asked.
    """
    try:
        with _docker_api() as api:
            running = api.containers()
    except Exception as exc:
        logger.warning("Could not list the running containers: %s", exc)
        return None
    users = []
    for container in running:
        labels = container.get("Labels") or {}
        sources = [m.get("Source") or "" for m in container.get("Mounts") or []]
        if labels.get(APP_POOL_LABEL) == name or any(
            source and _path_within(source, target) for source in sources
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


def _unmount_pool_target(name: str, target: str, mounts: list[disks.HostMount]) -> None:
    """Unmount whatever is mounted at the pool's mountpoint; PoolBusyError while in use.

    ``umount`` on the host only reports users in mount namespaces that
    receive its unmount (the host, the supervisor's rshared /mnt). A
    container's bind mount of a directory on the pool is a separate, private
    mount: the host's umount succeeds, the filesystem stays mounted inside
    the container and keeps being written to, and the disk must not be
    removed. Containers that Docker reports are refused up front; any other
    user is detected after the umount, and the pool is mounted again.
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
    try:
        _mount_by_uuid(uuid, target)
    except Exception as exc:
        raise PoolBusyError(
            f"{target} was unmounted on the host, but the filesystem is still in use "
            "elsewhere (for example by a container's bind mount), and mounting it again "
            f"failed: {exc}. Stop the apps and containers that use the pool before "
            "removing its disks."
        ) from exc
    raise PoolBusyError(
        f"Pool '{name}' is still in use outside the host (for example by a container's "
        "bind mount), so it was mounted again. Stop the apps and containers that use "
        "the pool and try again."
    )


def _mount_and_persist(name: str, uuid: str, *, already_mounted: bool) -> None:
    """Mount ``uuid`` at the pool's mountpoint on the host and keep it in fstab."""
    target = pool_mountpoint(name)
    text = _read_fstab()
    conflicts = unmanaged_fstab_entries(text, target)
    if conflicts:
        raise PoolConflictError(
            f"The host /etc/fstab already mounts {target} through an entry UNOS does not "
            f"manage ({conflicts[0]}). Remove or comment out that line first."
        )
    desired = set_managed_fstab_entry(text, name, uuid)
    if desired != text and name in parse_managed_fstab(text):
        # Replace an outdated entry (e.g. of an older pool with the same name)
        # before mounting, so systemd's unit no longer points at the old device.
        _write_fstab(desired, text)
        text = desired
    if not already_mounted:
        _ensure_mountpoint_dir(target)
        _prepare_btrfs_devices()
        _mount_by_uuid(uuid, target)
    _write_fstab(desired, text)


def mount_pool(name: str) -> PoolInfo:
    """Mount a pool on the host by UUID and keep its managed fstab entry.

    If this call mounted the pool, the pool's apps that Docker could not
    start without it are started in the background; that part never fails
    or delays the mount.
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
