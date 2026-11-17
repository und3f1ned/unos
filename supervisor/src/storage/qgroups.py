"""The quota groups of the mounted btrfs filesystems, as the kernel exports them in sysfs (ADR-0383).

Linux (5.9 and later) has a folder ``/sys/fs/btrfs/<fsid>/qgroups`` while quotas are on, with a
folder ``<level>_<id>`` per quota group, whose files ``referenced``, ``max_referenced`` and
``limit_flags`` print the counters that the kernel keeps in memory (``fs/btrfs/sysfs.c``: a number
and a line break), and the file ``inconsistent``, 1 while the counts need a rescan (6.1 and later).
``/sys/fs/btrfs/<fsid>/generation`` is the filesystem's transaction number, which every write
moves on. The supervisor's privileged container has the host's sysfs, as ``offsite/service.py``
reads it: a read starts no process, sends no ioctl and never reads a disk, so it wakes none
(ADR-0378), however many snapshots, and so quota groups, the pool has.

A volume's quota is the limit of its quota group 0/<id> on the bytes it references (ADR-0069,
ADR-0243), and its use is those bytes, data shared with its snapshots included.
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


@dataclass(frozen=True)
class Usage:
    """A volume's use: its referenced bytes and the limit on them (None without one)."""

    used: int | None
    quota: int | None
    state: UsageState


def _folder(fsid: str) -> str:
    """The sysfs folder of the filesystem ``fsid``; ValueError for anything but a UUID."""
    if not _FSID.fullmatch(fsid):
        raise ValueError(f"Not a filesystem UUID: {fsid!r}")
    return os.path.join(SYS_FS_BTRFS, fsid)


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
    return os.path.isdir(os.path.join(_folder(fsid), "qgroups"))


def generation(fsid: str) -> int | None:
    """The transaction number of the mounted filesystem ``fsid``; None when it cannot be read."""
    return _number(os.path.join(_folder(fsid), "generation"))


def usage(fsid: str, subvol_id: int) -> Usage:
    """The use and quota of the subvolume ``subvol_id`` of the mounted filesystem ``fsid``."""
    folder = os.path.join(_folder(fsid), "qgroups")
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
