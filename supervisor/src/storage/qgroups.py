"""The quota groups of the mounted btrfs filesystems, as the kernel exports them in sysfs (ADR-0387).

Linux (5.9 and later) has a folder ``/sys/fs/btrfs/<fsid>/qgroups`` while quotas are on, with a
folder ``<level>_<id>`` per quota group, whose files ``referenced``, ``max_referenced`` and
``limit_flags`` print the counters that the kernel keeps in memory (``fs/btrfs/sysfs.c``: a number
and a line break), and the file ``inconsistent``, 1 while the counts need a rescan (6.1 and later).
``/sys/fs/btrfs/<fsid>/generation`` is the filesystem's transaction number, which every write
moves on. The supervisor's privileged container has the host's sysfs, as ``offsite/service.py``
reads it: a read starts no process, sends no ioctl and never reads a disk, so it wakes none
(ADR-0378), however many snapshots, and so quota groups, the pool has.

A volume's quota is the limit of its quota group 0/<id> on the bytes it references (ADR-0069,
ADR-0243), and its use is those bytes, data shared with its snapshots included. A limit on the
exclusive bytes (``btrfs qgroup limit -e``), which UNOS never sets, is no quota here.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Literal

SYS_FS_BTRFS = "/sys/fs/btrfs"
# BTRFS_QGROUP_LIMIT_MAX_RFER of limit_flags: the group has a limit on its referenced bytes.
LIMIT_MAX_REFERENCED = 1

# counted; inconsistent: counted, but btrfs says the counts need a rescan; quotas_off: quotas are off on
# the pool, and nothing is counted; unknown: the quota group's files cannot be read.
UsageState = Literal["counted", "inconsistent", "quotas_off", "unknown"]

_FSID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
# The folder of a quota group of level 0, a subvolume's: 0_<id>.
_SUBVOLUME_GROUP = re.compile(r"0_([1-9][0-9]{0,19})")
# BTRFS_FIRST_FREE_OBJECTID: the lowest id of a subvolume. The top level is 5, and its group 0/5 stays.
FIRST_SUBVOLUME_ID = 256


@dataclass(frozen=True)
class Usage:
    """A volume's use: its referenced bytes and the limit on them (None without one)."""

    used: int | None
    quota: int | None
    state: UsageState


def is_fsid(text: str) -> bool:
    """Whether ``text`` is a filesystem UUID as the kernel names its sysfs folder (lower case)."""
    return _FSID.fullmatch(text) is not None


def _folder(fsid: str) -> str | None:
    """The sysfs folder of the filesystem ``fsid``; None for anything but a UUID."""
    return os.path.join(SYS_FS_BTRFS, fsid) if is_fsid(fsid) else None


def _number(path: str) -> int | None:
    """The number in a sysfs file; None when it cannot be read or holds no number."""
    try:
        with open(path, "rb") as file:
            text = file.read(64).strip()
    except OSError:
        return None
    return int(text) if text.isdigit() else None


def quotas_on(fsid: str) -> bool:
    """Whether quotas are on in the mounted filesystem ``fsid``: the kernel then has its qgroups folder."""
    folder = _folder(fsid)
    return folder is not None and os.path.isdir(os.path.join(folder, "qgroups"))


def generation(fsid: str) -> int | None:
    """The number of the running transaction of the mounted filesystem ``fsid``; None when it cannot
    be read."""
    folder = _folder(fsid)
    return None if folder is None else _number(os.path.join(folder, "generation"))


def subvolume_group_ids(fsid: str) -> set[int] | None:
    """The subvolume ids of the quota groups 0/<id> of the mounted filesystem ``fsid``, the top
    level's 0/5 left out; None when quotas are off or the folder cannot be read. A list of the
    kernel's folders: no disk is read."""
    filesystem = _folder(fsid)
    if filesystem is None:
        return None
    try:
        names = os.listdir(os.path.join(filesystem, "qgroups"))
    except OSError:
        return None
    ids = set()
    for name in names:
        match = _SUBVOLUME_GROUP.fullmatch(name)
        if match is not None and int(match.group(1)) >= FIRST_SUBVOLUME_ID:
            ids.add(int(match.group(1)))
    return ids


# What Linux 6.1 counts in a quota group besides its bytes: its reservations. A kernel without these
# files (before 5.x) counts none of them here.
_RESERVATIONS = ("rsv_data", "rsv_meta_pertrans", "rsv_meta_prealloc")


def group_in_use(fsid: str, subvol_id: int) -> bool | None:
    """Whether the quota group 0/<id> of the mounted filesystem ``fsid`` still counts bytes or holds
    reservations, which Linux 6.1 refuses to destroy (busy); None when its files cannot be read."""
    filesystem = _folder(fsid)
    if filesystem is None:
        return None
    group = os.path.join(filesystem, "qgroups", f"0_{int(subvol_id)}")
    counts = [_number(os.path.join(group, name)) for name in ("referenced", "exclusive")]
    if None in counts:
        return None
    for name in _RESERVATIONS:
        path = os.path.join(group, name)
        if os.path.exists(path):
            counts.append(_number(path))
    if None in counts:
        return None
    return any(counts)


def inconsistent(fsid: str) -> bool:
    """Whether btrfs says that the quota counts of the mounted filesystem ``fsid`` need a rescan."""
    folder = _folder(fsid)
    return folder is not None and _number(os.path.join(folder, "qgroups", "inconsistent")) == 1


def usage(fsid: str, subvol_id: int) -> Usage:
    """The use and quota of the subvolume ``subvol_id`` of the mounted filesystem ``fsid``: unknown
    for an ``fsid`` that is no UUID, whose folder the kernel cannot have."""
    filesystem = _folder(fsid)
    if filesystem is None:
        return Usage(None, None, "unknown")
    folder = os.path.join(filesystem, "qgroups")
    if not os.path.isdir(folder):
        return Usage(None, None, "quotas_off")
    group = os.path.join(folder, f"0_{int(subvol_id)}")
    referenced = _number(os.path.join(group, "referenced"))
    flags = _number(os.path.join(group, "limit_flags"))
    limit = _number(os.path.join(group, "max_referenced")) if flags is not None and flags & LIMIT_MAX_REFERENCED else None
    if referenced is None or flags is None or (flags & LIMIT_MAX_REFERENCED and limit is None):
        return Usage(None, None, "unknown")
    state: UsageState = "inconsistent" if _number(os.path.join(folder, "inconsistent")) == 1 else "counted"
    return Usage(referenced, limit, state)
