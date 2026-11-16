"""Disk discovery and the facts needed to use a disk safely.

Everything about block devices and mounts comes from the HOST view: lsblk,
findmnt, swapon and wipefs run in the host namespaces through
:mod:`src.host`. Inside the supervisor container these tools only see the
container's own mounts, so the host's root filesystem never shows up as
mounted there and a system disk looks unused.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import psutil
from fastapi import HTTPException

from .. import host
from ..security.validators import sanitize_error_message, validate_disk_target
from .schemas import DiskInfo, PartitionInfo, PowerState, SmartData

logger = logging.getLogger(__name__)

# Boot and virtual devices that are never offered for pools. Whether a disk is
# a *system* disk is decided from what it backs on the host (see
# _system_reason), not from its name.
HIDDEN_DISK_PREFIXES = (
    "mmcblk",  # SD card / eMMC: the boot medium of ARM boards
    "mtdblock",  # SPI/NOR flash holding the bootloader
    "zram",  # compressed RAM disks (swap, /var/log on Armbian)
    "ram",  # RAM disks
    "loop",  # loop devices
    "sr",  # optical drives
    "fd",  # floppy drives
)

# A device that backs one of these host mountpoints is a system device.
SYSTEM_MOUNTPOINTS = ("/", "/boot", "/boot/efi", "/boot/firmware")

LSBLK_COLUMNS = (
    "NAME,KNAME,PATH,PKNAME,TYPE,SIZE,ROTA,MODEL,SERIAL,WWN,"
    "FSTYPE,LABEL,UUID,PARTUUID,PTTYPE,MOUNTPOINTS,MAJ:MIN"
)
FINDMNT_COLUMNS = "TARGET,SOURCE,FSTYPE,UUID,MAJ:MIN,FSROOT,VFS-OPTIONS,FS-OPTIONS"

SMARTCTL_TIMEOUT = 60
# Seconds the read of a disk's power state may take (power_state): hdparm -C and smartctl -n standby
# answer at once, unless the disk or its USB bridge hangs. hdparm runs on the host under its timeout
# (ADR-0258), which kills it POWER_CHECK_KILL_AFTER seconds after its TERM; POWER_READ_LIMIT ends
# nsenter after both, and bounds the wait of a second read of the disk for the first.
POWER_CHECK_TIMEOUT = 15
POWER_CHECK_KILL_AFTER = 5
POWER_READ_LIMIT = POWER_CHECK_TIMEOUT + POWER_CHECK_KILL_AFTER + 5
# Seconds the check whether a timed-out hdparm -C still runs on the host may take (pgrep).
POWER_STUCK_CHECK_TIMEOUT = 5

# What smartctl -n prints, as its message and as one of the JSON's smartctl.messages, when it
# leaves a disk alone after CHECK POWER MODE, with exit status bit 1: "Device is in STANDBY mode,
# exit(2)". It names the mode as its ataprint.cpp does; -n standby stops for SLEEP, STANDBY and
# STANDBY_Y, -n idle for IDLE, IDLE_A, IDLE_B and IDLE_C too (EPC's power conditions, ADR-0378).
_STOPPED_IN = re.compile(r"Device is in ([A-Z][A-Z_]*) mode\b")
_SMARTCTL_MODES: dict[str, PowerState] = {
    "SLEEP": "standby",
    "STANDBY": "standby",
    "STANDBY_Y": "standby",
    "IDLE": "active",
    "IDLE_A": "active",
    "IDLE_B": "active",
    "IDLE_C": "active",
}

# How hdparm -C prints the disk's answer to CHECK POWER MODE: " drive state is:  standby".
_DRIVE_STATE = re.compile(r"^\s*drive state is:\s*(\S+)\s*$", re.MULTILINE)
# hdparm's names of the power modes: active/idle (FFh), idle (80h), standby (00h), sleeping (older
# versions, for a disk in SLEEP) and those of a disk with an NV cache (41h spinning, 40h spun down).
# "unknown" with exit status 0 is an answer hdparm does not name, as EPC's idle_a, idle_b, idle_c
# (81h-83h) and standby_y (01h): _epc_power_state asks smartctl.
_HDPARM_STATES: dict[str, PowerState] = {
    "active/idle": "active",
    "idle": "active",
    "NVcache_spinup": "active",
    "standby": "standby",
    "sleeping": "standby",
    "NVcache_spindown": "standby",
}
# The exit status of the host's timeout(1) when it cannot find the program: the host has no hdparm.
_NOT_FOUND = 127
# The exit statuses of the host's timeout(1) when it ended the program: with TERM, or with KILL.
_ENDED_BY_TIMEOUT = (124, 137)

# The hdparm -C reads, by disk (power_state), bounded as run_in_container bounds its commands
# (ADR-0290): the disks whose read runs, and those whose last read timed out, whose hdparm may still
# be stuck in the kernel on the host, each with whether a read not started for it was logged.
_power_reads = threading.Condition()
_power_reading: set[str] = set()
_power_stuck: dict[str, bool] = {}

_SUBVOLUME_SUFFIX = re.compile(r"\[[^\]]*\]$")


def _has_ro(options: str | None) -> bool:
    return "ro" in (options or "").split(",")


@dataclass(frozen=True)
class HostMount:
    """One entry of the host's mount table (or of another mount namespace's)."""

    target: str
    source: str | None  # device path without a btrfs "[/subvolume]" suffix
    fstype: str | None
    uuid: str | None
    majmin: str | None
    vfs_options: str | None = None  # per-mount options ("rw,noatime")
    fs_options: str | None = None  # superblock options ("ro,space_cache=v2,...")
    # The folder of the filesystem that the mount shows: "/" for its root, else a
    # btrfs subvolume or the folder of a bind mount ("/apps/x").
    fsroot: str | None = None

    @property
    def read_only(self) -> bool:
        """The filesystem cannot be written through this mount."""
        return _has_ro(self.vfs_options) or _has_ro(self.fs_options)

    @property
    def forced_read_only(self) -> bool:
        """Mounted read-write, but the filesystem itself is read-only.

        btrfs (like ext4 with errors=remount-ro) switches the superblock to
        read-only after a serious error such as an aborted transaction; the
        mount keeps its "rw" option. A deliberate ``mount -o ro`` marks the
        mount read-only too, so it is not reported here.
        """
        return _has_ro(self.fs_options) and not _has_ro(self.vfs_options)


@dataclass(frozen=True)
class DeviceFacts:
    """Safety-relevant facts about a disk or partition (host view)."""

    path: str
    name: str
    type: str  # "disk" or "part"
    hidden: bool  # boot/virtual device that is never offered for pools
    is_system: bool
    in_use: bool
    in_use_reason: str | None
    has_data: bool
    data_description: str | None
    pool: str | None
    mountpoints: tuple[str, ...]
    # The device itself and everything on it (partitions, LVM/LUKS/md holders).
    member_paths: frozenset[str] = field(default_factory=frozenset)
    # Identity, to tell whether /dev/sdX is still the disk a client selected:
    # serial and WWN of the whole disk (also for a partition), size of the device.
    serial: str | None = None
    wwn: str | None = None
    size: int = 0


@dataclass(frozen=True)
class HostDisk:
    """A whole disk of the host, as the Storage page lists it (see host_disks)."""

    name: str  # "sda"
    path: str  # "/dev/sda"
    model: str | None
    size: int  # bytes; 0 when lsblk reports none
    # As lsblk reports it. smartctl's can differ: behind a USB bridge lsblk may
    # report the bridge's serial, or none.
    serial: str | None = None

    @property
    def paths(self) -> frozenset[str]:
        """The paths SMART requests may name the disk by: its path and /dev/<name>."""
        return frozenset((self.path, f"/dev/{self.name}"))


@dataclass
class _HostState:
    tree: list[dict[str, Any]]
    mounts: list[HostMount] | None
    swaps: set[str]
    root_spec: tuple[str, str] | None
    system_paths: dict[str, str] = field(default_factory=dict)
    system_majmin: dict[str, str] = field(default_factory=dict)
    mounted_btrfs_uuids: set[str] = field(default_factory=set)


def is_hidden_disk(name: str) -> bool:
    return name.startswith(HIDDEN_DISK_PREFIXES)


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _to_bool(value: Any, default: bool = True) -> bool:
    # lsblk >= 2.33 emits JSON booleans; older versions emit "0"/"1".
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes")
    return default


def disk_id(value: Any) -> str | None:
    """Normalise a serial number or WWN as lsblk reports it ("" and blanks are None)."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _unique(values: Iterator[str] | list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for value in values:
        seen.setdefault(value, None)
    return list(seen)


def host_mounts() -> list[HostMount]:
    """Return the host's mount table. Raises RuntimeError if findmnt fails.

    findmnt prints the folder a mount shows (SOURCE "/dev/sda[/folder]") and
    the btrfs subvolume (subvol=) as they are named now, and whatever can
    write in a pool names them: bytes that are not UTF-8 become U+FFFD.
    """
    return _findmnt([])


def namespace_mounts(pid: int) -> list[HostMount]:
    """The mount table of the mount namespace of the host's process ``pid``.

    findmnt reads /proc/<pid>/mountinfo: the mountpoints are the ones that the
    process sees. Raises RuntimeError if findmnt fails, for example because
    the process has exited.
    """
    return _findmnt(["--task", str(pid)])


def mount_namespace_pids() -> list[int]:
    """One process of each mount namespace on the host other than the host's own, by PID.

    lsns names the lowest PID of each namespace, so the host's (PID 1's) has
    PID 1. Raises RuntimeError if lsns fails or does not print the expected
    JSON. Older versions of lsns print the numbers as strings.
    """
    result = host.run_on_host(["lsns", "--json", "--type", "mnt", "--output", "NS,PID"], timeout=30)
    if not result.ok:
        raise RuntimeError(f"lsns failed: {result.stderr.strip() or result.returncode}")
    try:
        rows = json.loads(result.stdout)["namespaces"]
        pids = {_to_int(row.get("pid")) for row in rows}
    except (json.JSONDecodeError, TypeError, KeyError, AttributeError) as exc:
        raise RuntimeError(f"lsns returned invalid JSON: {exc}") from exc
    return sorted(pid for pid in pids if pid > 1)


def _findmnt(options: list[str]) -> list[HostMount]:
    result = host.run_on_host(
        ["findmnt", "-J", "-l", *options, "-o", FINDMNT_COLUMNS], timeout=30, errors="replace"
    )
    if not result.ok:
        raise RuntimeError(f"findmnt failed: {result.stderr.strip() or result.returncode}")
    try:
        data = json.loads(result.stdout) if result.stdout.strip() else {}
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"findmnt returned invalid JSON: {exc}") from exc

    mounts: list[HostMount] = []

    def visit(items: list[dict[str, Any]] | None) -> None:
        for item in items or []:
            target = item.get("target")
            if target:
                source = item.get("source")
                if isinstance(source, str):
                    source = _SUBVOLUME_SUFFIX.sub("", source) or None
                mounts.append(
                    HostMount(
                        target=target,
                        source=source,
                        fstype=item.get("fstype"),
                        uuid=item.get("uuid"),
                        majmin=item.get("maj:min"),
                        vfs_options=item.get("vfs-options"),
                        fs_options=item.get("fs-options"),
                        fsroot=item.get("fsroot"),
                    )
                )
            visit(item.get("children"))

    visit(data.get("filesystems"))
    return mounts


def _lsblk_tree() -> list[dict[str, Any]]:
    # lsblk -J prints labels, models and mountpoints with their bytes as they
    # are, and a FAT label in a Windows code page (a USB stick) is not UTF-8:
    # such bytes become U+FFFD, which no device path or pool name contains.
    result = host.run_on_host(
        ["lsblk", "-J", "-b", "-o", LSBLK_COLUMNS], timeout=30, errors="replace"
    )
    if not result.ok:
        # util-linux < 2.37 has no MOUNTPOINTS column.
        legacy_columns = LSBLK_COLUMNS.replace("MOUNTPOINTS", "MOUNTPOINT")
        legacy = host.run_on_host(
            ["lsblk", "-J", "-b", "-o", legacy_columns], timeout=30, errors="replace"
        )
        if not legacy.ok:
            raise RuntimeError(f"lsblk failed: {result.stderr.strip() or result.returncode}")
        result = legacy
    try:
        return json.loads(result.stdout).get("blockdevices") or []
    except (json.JSONDecodeError, AttributeError) as exc:
        raise RuntimeError(f"lsblk failed: invalid output ({exc})") from exc


def labelled_devices(label: str) -> list[str]:
    """Paths of the host's block devices whose filesystem has the label ``label``.

    Every device lsblk lists counts, the boot and virtual devices included.
    RuntimeError when lsblk fails.
    """
    return sorted({
        node.get("path") or f"/dev/{node.get('name')}"
        for top in _lsblk_tree()
        for node in _walk(top)
        if node.get("label") == label
    })


def udev_btrfs_devices() -> list[tuple[str, str | None, str, int]]:
    """The host's block devices with a btrfs filesystem as udev recorded them when they appeared:
    (UUID in lower case, label, path, size in bytes), read from lsblk without reading a disk
    (ADR-0378). RuntimeError when lsblk fails.
    """
    return [
        ((node.get("uuid") or "").lower(), node.get("label"), node.get("path") or f"/dev/{node.get('name')}",
         _to_int(node.get("size")))
        for top in _lsblk_tree()
        for node in _walk(top)
        if node.get("fstype") == "btrfs" and node.get("uuid")
    ]


def disks_of_filesystem(uuid: str) -> list[str]:
    """The paths of the whole disks that hold a member of the filesystem ``uuid`` (lsblk, which reads
    no disk). RuntimeError when lsblk fails."""
    wanted = uuid.lower()
    return [
        top.get("path") or f"/dev/{top.get('name')}"
        for top in _lsblk_tree()
        if any((node.get("uuid") or "").lower() == wanted for node in _walk(top))
    ]


def _whole_disks(tree: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The whole disks among the top-level devices of an lsblk tree.

    Devices of TYPE "disk" without the boot SD card/eMMC and virtual devices
    (HIDDEN_DISK_PREFIXES). A system disk on NVMe, SATA or USB is included.
    Partitions, RAID arrays and other devices built on a disk are not whole
    disks.
    """
    return [
        node
        for node in tree
        if node.get("type") == "disk" and node.get("name") and not is_hidden_disk(node["name"])
    ]


def _active_swaps() -> set[str]:
    try:
        result = host.run_on_host(
            ["swapon", "--show=NAME", "--noheadings", "--raw"], timeout=15
        )
    except Exception as exc:  # lsblk still reports "[SWAP]" mountpoints
        logger.warning("Could not list active swap on the host: %s", exc)
        return set()
    if not result.ok:
        logger.warning("swapon --show failed: %s", result.stderr.strip())
        return set()
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def _boot_root_spec() -> tuple[str, str] | None:
    """Return the ``root=`` device of the running kernel as (kind, value)."""
    try:
        cmdline = host.read_host_file("/proc/cmdline")
    except Exception as exc:
        logger.warning("Could not read the kernel command line: %s", exc)
        return None
    spec = None
    for token in cmdline.split():
        if token.startswith("root="):
            spec = token[len("root="):]  # the kernel uses the last root=
    if not spec:
        return None
    for prefix, kind in (("UUID=", "uuid"), ("PARTUUID=", "partuuid"), ("LABEL=", "label")):
        if spec.startswith(prefix):
            value = spec[len(prefix):]
            return (kind, value if kind == "label" else value.lower())
    if spec.startswith("/dev/"):
        return ("path", spec)
    return None


def _walk(node: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield node
    for child in node.get("children") or []:
        yield from _walk(child)


def _mountpoints(node: dict[str, Any]) -> list[str]:
    values = node.get("mountpoints")
    if values is None:
        values = [node.get("mountpoint")]
    return [value for value in values if value]


def _node_paths(node: dict[str, Any]) -> set[str]:
    paths = set()
    if node.get("path"):
        paths.add(node["path"])
    for key in ("kname", "name"):
        if node.get(key):
            paths.add(f"/dev/{node[key]}")
    return paths


def _build_state(
    tree: list[dict[str, Any]],
    mounts: list[HostMount] | None,
    swaps: set[str],
    root_spec: tuple[str, str] | None,
) -> _HostState:
    state = _HostState(tree=tree, mounts=mounts, swaps=swaps, root_spec=root_spec)

    for mount in mounts or []:
        if mount.target not in SYSTEM_MOUNTPOINTS:
            continue
        reason = f"Mounted at {mount.target}"
        if mount.source and mount.source.startswith("/dev/"):
            state.system_paths.setdefault(mount.source, reason)
        # "0:N" is an anonymous device (btrfs, overlay, tmpfs): it names no disk.
        if mount.majmin and not mount.majmin.startswith("0:"):
            state.system_majmin.setdefault(mount.majmin, reason)
    for swap in swaps:
        if swap.startswith("/dev/"):
            state.system_paths.setdefault(swap, "Active swap")

    nodes_by_path: dict[str, dict[str, Any]] = {}
    for top in tree:
        for node in _walk(top):
            for path in _node_paths(node):
                nodes_by_path.setdefault(path, node)
            if node.get("fstype") == "btrfs" and node.get("uuid") and _mountpoints(node):
                state.mounted_btrfs_uuids.add(node["uuid"].lower())
    # A multi-device btrfs filesystem is mounted through one member only, so
    # lsblk shows the mountpoint on that member. Mark the filesystem UUID as
    # mounted so every other member counts as in use too.
    for mount in mounts or []:
        if mount.fstype != "btrfs":
            continue
        if mount.uuid:
            state.mounted_btrfs_uuids.add(mount.uuid.lower())
        elif mount.source in nodes_by_path and nodes_by_path[mount.source].get("uuid"):
            state.mounted_btrfs_uuids.add(nodes_by_path[mount.source]["uuid"].lower())
    return state


def _collect_host_state() -> _HostState:
    tree = _lsblk_tree()
    try:
        mounts: list[HostMount] | None = host_mounts()
    except Exception as exc:  # lsblk mountpoints still cover the common cases
        logger.warning("Could not read the host mount table: %s", exc)
        mounts = None
    return _build_state(tree, mounts, _active_swaps(), _boot_root_spec())


def _matches_root_spec(node: dict[str, Any], spec: tuple[str, str]) -> bool:
    kind, value = spec
    if kind == "uuid":
        return (node.get("uuid") or "").lower() == value
    if kind == "partuuid":
        return (node.get("partuuid") or "").lower() == value
    if kind == "label":
        return node.get("label") == value
    return value in _node_paths(node)


def _system_reason(subtree: list[dict[str, Any]], state: _HostState) -> str | None:
    """Why this device (or anything on it) is part of the running system, if it is.

    ``subtree`` holds the device and everything built on it, so a filesystem
    on a partition, an LVM volume or an md array resolves to the disk below.
    """
    for node in subtree:
        for mountpoint in _mountpoints(node):
            if mountpoint in SYSTEM_MOUNTPOINTS:
                return f"Mounted at {mountpoint}"
            if mountpoint == "[SWAP]":
                return "Active swap"
        for path in _node_paths(node):
            if path in state.system_paths:
                return state.system_paths[path]
        if node.get("maj:min") in state.system_majmin:
            return state.system_majmin[node["maj:min"]]
        if state.root_spec and _matches_root_spec(node, state.root_spec):
            return "Host boot device"
    return None


def _data_description(node: dict[str, Any]) -> str | None:
    """Describe what lsblk reports on the device, or None if it reports nothing."""
    fstype = node.get("fstype")
    label = node.get("label")
    if fstype:
        return f"{fstype} '{label}'" if label else fstype
    # On partitions lsblk reports the partition table of the parent disk.
    if node.get("type") != "part":
        partitions = [c for c in node.get("children") or [] if c.get("type") == "part"]
        if node.get("pttype") or partitions:
            count = len(partitions)
            plural = "" if count == 1 else "s"
            return f"{node.get('pttype') or 'unknown'} partition table with {count} partition{plural}"
    if node.get("children"):
        return "devices built on it (" + ", ".join(
            str(child.get("name")) for child in node["children"]
        ) + ")"
    return None


def _probe_signatures(path: str) -> str | None:
    """Look for signatures lsblk does not report (e.g. at the end of the disk).

    Returns a description, or None if the device is blank. When probing fails
    the device is reported as having unknown contents, so it is treated as
    not blank.
    """
    try:
        result = host.run_on_host(["wipefs", "--no-act", "--json", path], timeout=30)
    except Exception as exc:
        return f"unknown contents ({exc})"
    if not result.ok:
        return f"unknown contents (wipefs failed: {result.stderr.strip() or result.returncode})"
    if not result.stdout.strip():
        return None
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return "unrecognised signature"
    if not isinstance(data, dict):
        return "unrecognised signature"
    # The table is called "signatures"; accept any list so that a renamed
    # table can never make a disk look blank.
    signatures = [sig for value in data.values() if isinstance(value, list) for sig in value]
    if not signatures:
        return None
    types = _unique(
        str(sig.get("type") or "unknown") if isinstance(sig, dict) else "unknown"
        for sig in signatures
    )
    return f"{', '.join(types)} signature"


def io_counts() -> dict[str, tuple[int, int]]:
    """The reads and writes that the kernel completed on each block device since boot, by its name
    ("sda"): /proc/diskstats, which counts a whole disk's partitions in the disk's line and reads no
    disk. {} when it cannot be read."""
    try:
        counters = psutil.disk_io_counters(perdisk=True) or {}
    except Exception:
        return {}
    return {name: (c.read_count, c.write_count) for name, c in counters.items()}


def smartctl_stopped_in(result: subprocess.CompletedProcess[str]) -> str | None:
    """The power mode in which ``smartctl -n`` left the disk alone (ADR-0378), as smartctl names it
    ("STANDBY", "IDLE_A"); None when it did not stop there.

    smartctl first asks the disk for its power mode with CHECK POWER MODE, which does not spin it up,
    and stops for the modes that -n names: exit status bit 1, which also means that the device could
    not be opened, and its message "Device is in STANDBY mode", which -j puts in the JSON's
    smartctl.messages (the search finds it in both forms of the output).
    """
    if not result.returncode & 0b10:
        return None
    found = _STOPPED_IN.search(result.stdout or "")
    return found[1] if found else None


def smartctl_found_it_asleep(result: subprocess.CompletedProcess[str]) -> bool:
    """Whether ``smartctl -n standby`` left the disk alone because it sleeps (ADR-0378): in SLEEP,
    STANDBY, or EPC's STANDBY_Y."""
    return _SMARTCTL_MODES.get(smartctl_stopped_in(result) or "") == "standby"


def power_state(device: str) -> PowerState:
    """Whether the whole disk ``device`` is active (spinning, or idle) or in standby, read without
    waking it (ADR-0378): ``hdparm -C`` on the host sends the disk CHECK POWER MODE alone, which did
    not restart the standby timer of the dev device's disk (IDENTIFY does). "unknown" when hdparm
    fails (a disk it cannot ask, as behind some USB bridges), names no power mode, or does not answer
    within POWER_CHECK_TIMEOUT.

    A disk that answers with a power condition that hdparm does not name (it prints "unknown" and
    exits 0), as an EPC disk's idle_a, idle_b, idle_c and standby_y, is asked again with smartctl
    (_epc_power_state). A disk whose last read timed out is not asked again while that hdparm still
    runs on the host (_hdparm_power_read).

    On a host without hdparm (an optional host tool), and for an NVMe disk, which hdparm cannot ask
    and which gets no idle timeout, ``smartctl -n standby -i`` in the container reads it
    (_smartctl_power_state): it leaves a sleeping disk alone, but asks an awake one IDENTIFY DEVICE,
    which restarts the disk's standby timer. Blocking: call it in a worker thread.
    """
    if device.removeprefix("/dev/").startswith("nvme"):
        return _smartctl_power_state(device)
    result = _hdparm_power_read(device)
    if result is None:
        return "unknown"
    if result.returncode == _NOT_FOUND:
        return _smartctl_power_state(device)
    found = _DRIVE_STATE.search(result.stdout)
    if not result.ok or found is None:  # also when the host's timeout ended it (124, or 137 killed)
        return "unknown"
    state = _HDPARM_STATES.get(found[1])
    return state if state is not None else _epc_power_state(device)


def _hdparm_power_read(device: str) -> host.HostResult | None:
    """``hdparm -C <device>`` on the host under its timeout; None when it was not started, or did
    not answer (run_on_host's limit ran out, or it could not run).

    Bounded per disk as run_in_container bounds its commands (ADR-0290): the host's timeout cannot
    end an hdparm stuck in the kernel on a disk that stopped answering, and run_on_host's limit ends
    only nsenter. So one read of a disk runs at a time: a second waits for the first, at most
    POWER_READ_LIMIT. After a read that timed out (run_on_host's limit, or the host's timeout: 124,
    137), no hdparm is started for the disk until pgrep shows that the earlier one has ended
    (_hdparm_has_ended); until then it answers None at once, logged once for each such read. So a
    disk that hangs keeps at most one stuck hdparm, and its timeout, on the host.
    """
    with _power_reads:
        if not _power_reads.wait_for(lambda: device not in _power_reading, POWER_READ_LIMIT):
            return None
        _power_reading.add(device)
        stuck = device in _power_stuck
    try:
        if stuck:
            if not _hdparm_has_ended(device):
                with _power_reads:
                    if not _power_stuck.get(device):
                        logger.warning("Not started: hdparm -C %s: an earlier run of it has not ended", device)
                        _power_stuck[device] = True
                return None
            with _power_reads:
                _power_stuck.pop(device, None)
        cmd = ["timeout", f"--kill-after={POWER_CHECK_KILL_AFTER}", str(POWER_CHECK_TIMEOUT), "hdparm", "-C", device]
        try:
            result = host.run_on_host(cmd, timeout=POWER_READ_LIMIT, errors="replace")
        except subprocess.TimeoutExpired:
            result = None
        except (OSError, subprocess.SubprocessError):
            return None
        if result is None or result.returncode in _ENDED_BY_TIMEOUT:
            logger.warning("hdparm -C %s did not answer within %s seconds", device, POWER_CHECK_TIMEOUT)
            with _power_reads:
                _power_stuck[device] = False
        return result
    finally:
        with _power_reads:
            _power_reading.discard(device)
            _power_reads.notify_all()


def _hdparm_has_ended(device: str) -> bool:
    """Whether no ``hdparm -C <device>`` runs on the host any more: ``pgrep -f`` in the container,
    which has the host's process IDs (pid: host) and procps; False when pgrep finds one, fails, or
    does not answer (run_in_container, ADR-0290)."""
    try:
        found = host.run_in_container(
            ["pgrep", "-f", f"^hdparm -C {_ere_literal(device)}$"], timeout=POWER_STUCK_CHECK_TIMEOUT
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return found.returncode == 1  # 0: found one; 2 and 3: pgrep failed


def _ere_literal(text: str) -> str:
    """``text`` as a POSIX extended regular expression that matches it alone."""
    return re.sub(r"([.\[\]()*+?{}|^$\\])", r"\\\1", text)


def _epc_power_state(device: str) -> PowerState:
    """power_state of a disk whose answer to CHECK POWER MODE hdparm does not name, as an EPC disk's
    power conditions (ACS: 81h-83h idle_a, idle_b, idle_c; 01h standby_y): ``smartctl -n idle -i -j``
    in the container asks it again and stops there for SLEEP, STANDBY, STANDBY_Y, IDLE and
    IDLE_A/B/C, which it names, without IDENTIFY (smartctl_stopped_in): IDLE* is active, the others
    standby. "unknown" otherwise: smartctl went on to read the identity, so the disk answered a mode
    that smartctl does not stop for (it is active by now) or does not name either, and that IDENTIFY
    says nothing of its mode; or smartctl cannot open the disk or does not answer within
    POWER_CHECK_TIMEOUT."""
    try:
        # Killed at its timeout and not waited for (ADR-0290).
        result = host.run_in_container(["smartctl", "-n", "idle", "-i", "-j", device], timeout=POWER_CHECK_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return _SMARTCTL_MODES.get(smartctl_stopped_in(result) or "", "unknown")


def _smartctl_power_state(device: str) -> PowerState:
    """power_state with ``smartctl -n standby -i`` in the container, which asks the disk for its
    power mode and stops for a sleeping one (smartctl_found_it_asleep); an awake one, an EPC disk
    in idle_a, idle_b or idle_c too, answers its identity. "unknown" when
    smartctl cannot open the disk (behind some USB bridges) or does not answer within
    POWER_CHECK_TIMEOUT."""
    try:
        # Killed at its timeout and not waited for (ADR-0290).
        result = host.run_in_container(
            ["smartctl", "-n", "standby", "-i", "-j", device], timeout=POWER_CHECK_TIMEOUT
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    if smartctl_found_it_asleep(result):
        return "standby"
    if result.returncode & 0b11:  # a bad command line, or the device could not be opened
        return "unknown"
    return "active"


def _node_facts(
    node: dict[str, Any], disk: dict[str, Any], state: _HostState, *, probe: bool = True
) -> DeviceFacts:
    subtree = list(_walk(node))
    path = node.get("path") or f"/dev/{node.get('name')}"
    mountpoints = tuple(
        _unique(mp for n in subtree for mp in _mountpoints(n) if mp != "[SWAP]")
    )

    system_reason = _system_reason(subtree, state)
    if system_reason is None and disk is not node:
        # Partitions of the system disk are never used for pools.
        disk_reason = _system_reason(list(_walk(disk)), state)
        if disk_reason:
            system_reason = f"Partition of the system disk ({disk_reason})"

    pool = None
    mounted_pool_member: dict[str, Any] | None = None
    for member in subtree:
        if member.get("fstype") != "btrfs":
            continue
        pool = pool or member.get("label")
        uuid = (member.get("uuid") or "").lower()
        if mounted_pool_member is None and uuid in state.mounted_btrfs_uuids:
            mounted_pool_member = member
    holder = next((n for n in subtree[1:] if n.get("type") != "part"), None)

    if system_reason:
        reason: str | None = system_reason
    elif mountpoints:
        reason = "Mounted at " + ", ".join(mountpoints)
    elif mounted_pool_member is not None:
        label = mounted_pool_member.get("label")
        reason = f"Member of pool '{label}'" if label else "Member of a mounted btrfs filesystem"
    elif holder is not None:
        reason = f"Used by {holder.get('name')} ({holder.get('type')})"
    else:
        reason = None

    data = _data_description(node)
    if data is None:
        # wipefs reads the disk: one that sleeps (probe=False) would spin up, so what it holds is
        # unknown, and it counts as holding data (ADR-0378).
        data = _probe_signatures(path) if probe else "unknown contents (the disk sleeps)"

    return DeviceFacts(
        path=path,
        name=node.get("name") or "",
        type=node.get("type") or "",
        hidden=is_hidden_disk(disk.get("name") or ""),
        is_system=system_reason is not None,
        in_use=reason is not None,
        in_use_reason=reason,
        has_data=data is not None,
        data_description=data,
        pool=pool,
        mountpoints=mountpoints,
        member_paths=frozenset(p for n in subtree for p in _node_paths(n)),
        serial=disk_id(disk.get("serial")),
        wwn=disk_id(disk.get("wwn")),
        size=_to_int(node.get("size")),
    )


def list_disks() -> list[DiskInfo]:
    """List the host's disks with what is needed to decide whether they are safe to use.

    Boot and virtual devices (see HIDDEN_DISK_PREFIXES) are left out. Every
    other disk is listed, including system disks, which are flagged with
    ``is_system``/``in_use`` instead of being hidden. Each disk's power state is
    read first, without waking it, and a disk in standby is not probed with
    wipefs; its idle timeout comes from UNOS's udev rule, with the reason when
    it can have none, a rule that cannot be read included (ADR-0378).
    """
    from . import spindown  # it reads disks' facts itself

    state = _collect_host_state()
    timeouts, unread = spindown.timeouts_or_problem()
    no_hdparm = spindown.hdparm_missing()
    whole = _whole_disks(state.tree)
    listed = [(n.get("path") or f"/dev/{n['name']}", disk_id(n.get("serial"))) for n in whole]
    result: list[DiskInfo] = []
    for node in whole:
        name = node["name"]
        path = node.get("path") or f"/dev/{name}"
        # Before anything that could read the disk: the power read leaves a sleeping disk alone.
        power = power_state(path)
        facts = _node_facts(node, node, state, probe=power != "standby")
        partitions = [
            PartitionInfo(
                name=child.get("name") or "",
                path=child.get("path") or f"/dev/{child.get('name')}",
                size=_to_int(child.get("size")),
                fstype=child.get("fstype"),
                label=child.get("label"),
                mountpoints=_unique(
                    mp for n in _walk(child) for mp in _mountpoints(n) if mp != "[SWAP]"
                ),
            )
            for child in node.get("children") or []
            if child.get("type") == "part"
        ]
        result.append(
            DiskInfo(
                name=name,
                path=node.get("path") or f"/dev/{name}",
                model=node.get("model"),
                serial=node.get("serial"),
                wwn=disk_id(node.get("wwn")),
                size=_to_int(node.get("size")),
                rotational=_to_bool(node.get("rota")),
                mountpoint=facts.mountpoints[0] if facts.mountpoints else None,
                type=node.get("type") or "disk",
                fstype=node.get("fstype"),
                label=node.get("label"),
                pttype=node.get("pttype"),
                partitions=partitions,
                mountpoints=list(facts.mountpoints),
                has_data=facts.has_data,
                is_system=facts.is_system,
                in_use=facts.in_use,
                in_use_reason=facts.in_use_reason,
                pool=facts.pool,
                power_state=power,
                spindown=timeouts.get(facts.serial or "", 0),
                spindown_problem=(
                    spindown.problem(facts, listed) or unread or (spindown.NO_HDPARM if no_hdparm else None)
                ),
            )
        )
    return result


def host_disks() -> list[HostDisk]:
    """The host's whole disks, exactly as the Storage page lists them (list_disks).

    The disks lsblk reports in the host namespaces, without the boot SD
    card/eMMC and virtual devices; a system disk on NVMe, SATA or USB is
    included (_whole_disks). SMART data, self-tests, Disk Health and the
    SMART monitor are for these disks only. Blocking (lsblk runs on the
    host): call it in a worker thread. RuntimeError when lsblk fails.
    """
    return [
        HostDisk(
            name=node["name"],
            path=node.get("path") or f"/dev/{node['name']}",
            model=disk_id(node.get("model")),
            size=_to_int(node.get("size")),
            serial=disk_id(node.get("serial")),
        )
        for node in _whole_disks(_lsblk_tree())
    ]


def host_disks_or_500() -> list[HostDisk]:
    """host_disks() for an endpoint: HTTPException 500 when the host's disks cannot be listed."""
    try:
        return host_disks()
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not list the host's disks: {sanitize_error_message(exc)}",
        ) from exc


def whole_disk_device(target: str) -> str:
    """Return ``/dev/<disk>`` for a whole disk of the host given as ``sda`` or ``/dev/sda``.

    For SMART data and self-tests: anything else, such as a partition, a
    RAID array, the boot SD card or eMMC, /dev/null or a /dev/disk/by-id
    link, is refused. Blocking (lsblk runs on the host): call it in a worker
    thread. HTTPException 400 for anything but one of host_disks(), 500
    when the host's disks cannot be listed.
    """
    device = validate_disk_target(target)
    if not any(device in disk.paths for disk in host_disks_or_500()):
        raise HTTPException(
            status_code=400,
            detail=(
                f"{device} is not a disk of this system. SMART works on whole disks as the "
                "Storage page lists them, not on partitions, RAID arrays, the boot SD card or "
                "eMMC, or virtual devices"
            ),
        )
    return device


def pool_device_problems(paths: list[str]) -> dict[str, str]:
    """Why devices of a btrfs filesystem found on the disks must not be part of a pool.

    Mount and Import mount such a filesystem and keep it in the host's fstab
    (ADR-0283), so its members follow the rules of a pool's creation
    (ADR-0035, pools._check_pool_devices): nothing on a boot or virtual
    device (HIDDEN_DISK_PREFIXES: the SD card and the eMMC, which is never
    written, ADR-0225; loop and RAM disks), and nothing on the system disk,
    whose partitions and the devices built on them are never used for pools.
    A device that lsblk does not list cannot be checked. Returns path ->
    reason for each of ``paths`` that breaks a rule. RuntimeError when lsblk
    fails.
    """
    state = _collect_host_state()
    problems: dict[str, str] = {}
    for path in paths:
        # A device built on several disks (md, LVM) is below each of them.
        tops = [top for top in state.tree if any(path in _node_paths(node) for node in _walk(top))]
        if not tops:
            problems[path] = f"lsblk does not list {path}"
        for top in tops:
            top_path = top.get("path") or f"/dev/{top.get('name')}"
            on = "is" if path in _node_paths(top) else f"is on {top_path},"
            if is_hidden_disk(top.get("name") or ""):
                problems[path] = f"{path} {on} a boot or virtual device"
                break
            reason = _system_reason(list(_walk(top)), state)
            if reason:
                problems[path] = f"{path} {on} a system disk ({reason})"
                break
    return problems


def get_device_facts(paths: list[str]) -> dict[str, DeviceFacts]:
    """Return fresh host facts for the given disk or partition paths.

    Raises ValueError for paths that are not a disk or partition on the host,
    or when a disk and one of its own partitions are both selected.
    """
    state = _collect_host_state()
    index: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for disk in state.tree:
        if disk.get("type") != "disk":
            continue
        candidates = [disk] + [c for c in disk.get("children") or [] if c.get("type") == "part"]
        for node in candidates:
            for path in (node.get("path"), f"/dev/{node.get('name')}"):
                if path:
                    index.setdefault(path, (node, disk))

    facts: dict[str, DeviceFacts] = {}
    for path in paths:
        if path not in index:
            raise ValueError(f"{path} is not a disk or partition on this system")
        node, disk = index[path]
        facts[path] = _node_facts(node, disk, state)
    for first in paths:
        for second in paths:
            if first != second and second in facts[first].member_paths:
                raise ValueError(
                    f"{second} is on {first}; select either the whole disk or its partitions"
                )
    return facts


def get_smart_data(device_path: str) -> SmartData:
    """SMART data of a whole disk with smartctl (blocking: run it in a worker thread).

    ``-n standby`` leaves a sleeping disk asleep (ADR-0378): its answer has the power state
    "standby", ``healthy`` None and no data.
    """
    try:
        # Killed at the timeout and not waited for (ADR-0290).
        result = host.run_in_container(
            ["smartctl", "-n", "standby", "-j", "-a", device_path], timeout=SMARTCTL_TIMEOUT
        )
    except subprocess.TimeoutExpired:
        return SmartData(
            device=device_path,
            healthy=False,
            power_state="unknown",
            raw_data={"error": f"smartctl did not answer within {SMARTCTL_TIMEOUT} seconds"},
        )
    if smartctl_found_it_asleep(result):
        return SmartData(device=device_path, healthy=None, power_state="standby")

    # smartctl returns non-zero for various reasons, try to parse anyway
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return SmartData(device=device_path, healthy=False, power_state="unknown", raw_data={"error": result.stderr})

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
        power_state="unknown" if result.returncode & 0b11 else "active",
        temperature=temperature,
        power_on_hours=power_on_hours,
        reallocated_sectors=reallocated_sectors,
        raw_data=data,
    )
