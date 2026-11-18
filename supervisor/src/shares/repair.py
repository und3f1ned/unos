"""Repair permissions: give a share's files and folders the group model (ADR-0155, ADR-0150).

After a repair every file and folder of the share has the group unos-smb and
group read/write: folders 2770 (setgid, so that new entries get the group),
files 0660, or 0770 when the owner may execute them. Every other account
loses access. Owners and file contents never change.

These entries are skipped, because changing them could reach outside the
share or would open something to the group that is not meant for it:
- symbolic links (never followed), hard-linked files (another link may be
  outside the share), FIFOs, sockets and device nodes;
- other filesystems: btrfs subvolumes (volumes, snapshots) and every mount
  point inside the share, also a bind mount of the same filesystem, which has
  the same st_dev. The mount points of /proc/self/mountinfo, which the walk
  reads again with each progress report, are skipped by their path, without
  even an lstat, which would reach into the mounted filesystem (a stalled
  FUSE or NFS mount blocks it); and every opened entry must be on the mount
  of the share's folder, by the mount ID of its descriptor, which a folder
  renamed during the walk cannot change;
- ``.snapshots`` folders;
- private entries, which only their owner can use (no group or other bits,
  such as a 0600 key or a 0700 folder); a private folder is not entered;
- folders deeper than ``max_depth``.

Nothing is changed (state "failed") when the share's folder is not on its
pool's mount (the pool is not mounted, or another filesystem is mounted on
the folder or above it), or when it is a pool root, app data (<pool>/apps)
or inside ``.snapshots``, which are never shared (ADR-0145).

SMB users and app containers can change the share while it is repaired, so
the walk never resolves a path. It is an explicit depth-first walk that opens
each folder relative to its parent's descriptor with O_NOFOLLOW and
O_DIRECTORY (os.fwalk opens folders without them), and each file with O_PATH,
which never calls a driver's open (a file swapped for a device node or a FIFO
is never opened), then changes the file through /proc/self/fd/<fd>. An opened
entry must be the one that was examined (st_dev, st_ino), else it is skipped
as "raced". At most ``max_depth + 1`` folder descriptors are open at a time.

Linux only (os.O_PATH, /proc). Every write goes to the share's pool, none to
the system disk; a second run writes nothing.

repair_router.py runs it as a background job (ADR-0363): it reports its
progress about once a second, stops when asked, and stops by itself only
after 24 hours. Both are checked between entries: no stop ends a call that
blocks (a disk that stalls), until that call returns.
"""

import errno
import os
import re
import stat
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel

from ..security.validators import open_dir_nofollow, pools_base
from ..times import UtcDatetime
from . import constants

# The mounts of this process's mount namespace. The supervisor container sees
# the pools and every mount below them (/mnt is bind-mounted rshared).
MOUNTINFO_PATH = "/proc/self/mountinfo"
# /proc/self/fdinfo/<fd> names the mount an open descriptor is on (mnt_id).
FDINFO_DIR = "/proc/self/fdinfo"

# The keys of RepairResult.skipped, in this order.
SKIP_REASONS = (
    "other_filesystem", "snapshots", "symlink", "special", "hardlink", "private", "too_deep", "raced",
)
# At most this many entries in skipped_paths and in error_samples.
MAX_SAMPLES = 20

# The bound of a walk (ADR-0363): far more than a share of tens of millions of entries takes, and
# still an end for a walk that never finishes, such as one in a tree that an app fills as fast as
# it is walked, which would otherwise hold the one repair that may run.
TIME_LIMIT_SECONDS = 24 * 3600
# How often the walk reports its progress; the events socket sends it every 2 seconds.
PROGRESS_INTERVAL_SECONDS = 1.0
_AGAIN = "run it again to continue (already repaired entries are skipped quickly)"
INCOMPLETE_MESSAGE = f"Stopped after 24 hours; {_AGAIN}"
STOPPED_MESSAGE = f"Stopped by an administrator; {_AGAIN}"

# A file its owner may execute keeps that as group execute: 0o770.
EXECUTABLE_FILE_MODE = constants.SHARE_EXECUTABLE_FILE_MODE

# O_NONBLOCK: opening a folder never blocks. O_DIRECTORY and O_NOFOLLOW: a
# folder swapped for anything else since its lstat fails with ENOTDIR (Linux,
# for a symlink as well) or ELOOP.
_FOLDER_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
# Opening an entry fails with these when it was removed or swapped since its lstat.
_RACED_ERRNOS = frozenset({errno.ENOENT, errno.ENOTDIR, errno.ELOOP})
# mountinfo writes space, tab, newline and backslash in paths as \040, \011, \012, \134.
_OCTAL_ESCAPE = re.compile(rb"\\([0-7]{3})")


class RepairResult(BaseModel):
    """A repair: while it runs (state "running", finished_at None, the counts so far), then its result."""

    share: str
    state: Literal["running", "done", "incomplete", "failed"]
    started_at: UtcDatetime
    finished_at: UtcDatetime | None
    visited: int
    changed: int
    skipped: dict[str, int]      # keys: other_filesystem, snapshots, symlink, special, hardlink, private, too_deep, raced
    skipped_paths: list[str]     # at most 20 relative paths of skipped subvolumes and mount points
    errors: int
    error_samples: list[str]     # at most 20, "relative/path: strerror"
    message: str


def repair_tree(
    path: str,
    *,
    gid: int,
    time_limit: float = TIME_LIMIT_SECONDS,
    max_entries: int | None = None,
    max_depth: int = 64,
    progress: Callable[[RepairResult], None] | None = None,
    stop: threading.Event | None = None,
) -> RepairResult:
    """Repair the permissions of the share folder ``path`` and of everything below it.

    ``path`` is a share's stored path: a folder inside a pool, in the normal
    form below the pools base (ADR-0145). ``gid`` is the group to give (the
    SMB group; a parameter for tests). The walk stops with the state
    "incomplete" after ``time_limit`` seconds, after ``max_entries`` visited
    entries (no limit when None; tests stop a walk at a known entry with it)
    or once ``stop`` is set; a second run gets on quickly, since entries that
    are already right are only examined. While it walks, ``progress`` gets
    the counts so far, as a result with the state "running", about every
    PROGRESS_INTERVAL_SECONDS. An entry that cannot be changed (EPERM, EACCES,
    EROFS, EIO, ...) is counted, with a sample, and the walk goes on. When the
    share's folder cannot be opened without following a symlink, is not on
    its pool's mount, or is a folder that is never shared (a pool root, app
    data, snapshots), or when the mount table cannot be read, the state is
    "failed" and nothing is changed.

    Blocking: run it in a worker thread. ``share`` of the result is empty; the
    caller sets it.
    """
    started_at = datetime.now(timezone.utc)
    deadline = time.monotonic() + time_limit
    o_path = getattr(os, "O_PATH", None)
    if o_path is None:
        return _Tally().result("failed", started_at, "Repair permissions needs Linux (O_PATH). Nothing was changed.")

    stack: list[_Folder] = []
    root_fd: int | None = None
    try:
        try:
            base = pools_base()
            parts = PurePosixPath(path).relative_to(base).parts
            refusal = _refusal(parts)
            if refusal is not None:
                raise _Refused(refusal)
            root_fd = open_dir_nofollow(base, parts)
            root = os.fstat(root_fd)
            names = os.listdir(root_fd)
        except (OSError, TypeError, ValueError) as exc:
            return _Tally().result(
                "failed", started_at, f"The share's folder could not be opened: {_reason(exc)}. Nothing was changed."
            )
        try:
            mounts = _read_mounts()
            root_mount = _mount_id(root_fd)
        except OSError as exc:
            return _Tally().result(
                "failed", started_at, f"The mount table could not be read: {_reason(exc)}. Nothing was changed."
            )
        # The share's folder must be on the mount at the pool's mountpoint. It
        # is not when the pool was unmounted before the folder was opened (the
        # folder of that name is then on the system disk), or when another
        # filesystem is mounted on the folder or on a folder above it.
        pool_mountpoint = PurePosixPath(base, parts[0]).parts
        if pool_mountpoint not in {point for mount_id, point in mounts if mount_id == root_mount}:
            return _Tally().result(
                "failed",
                started_at,
                "The share's folder is not on its pool's filesystem: the pool is not mounted, or another "
                "filesystem is mounted on the folder or above it. Nothing was changed.",
            )
        walk = _Walk(
            gid=gid,
            root_dev=root.st_dev,
            root_mount=root_mount,
            folder=PurePosixPath(path).parts,
            mount_points=_mount_points_inside(mounts, PurePosixPath(path).parts),
            file_flags=o_path | os.O_NOFOLLOW | os.O_CLOEXEC,
            deadline=deadline,
            max_entries=max_entries,
            max_depth=max_depth,
            started_at=started_at,
            progress=progress,
            stop=stop,
        )
        stack.append(_Folder(root_fd, iter(names), "", 0))
        root_fd = None  # closed with the stack from now on
        stopped = walk.run(stack, root)
    finally:
        if root_fd is not None:
            _close(root_fd)
        for folder in stack:
            _close(folder.fd)

    tally = walk.tally
    if stopped is not None:
        return tally.result("incomplete", started_at, stopped)
    if tally.errors == 1:
        return tally.result("done", started_at, "Permissions repaired, except for 1 entry that could not be changed.")
    if tally.errors:
        return tally.result(
            "done", started_at, f"Permissions repaired, except for {tally.errors} entries that could not be changed."
        )
    return tally.result("done", started_at, "Permissions repaired.")


@dataclass
class _Folder:
    """A folder on the walk's stack: its descriptor and the names still to visit."""

    fd: int
    names: Iterator[str]
    path: str   # relative to the share's folder; "" for the share's folder itself
    depth: int  # 0 for the share's folder


@dataclass
class _Tally:
    """The counters of one repair."""

    visited: int = 0
    changed: int = 0
    skipped: dict[str, int] = field(default_factory=lambda: dict.fromkeys(SKIP_REASONS, 0))
    skipped_paths: list[str] = field(default_factory=list)
    errors: int = 0
    error_samples: list[str] = field(default_factory=list)

    def skip(self, reason: str, path: str | None = None) -> None:
        self.skipped[reason] += 1
        if path is not None and len(self.skipped_paths) < MAX_SAMPLES:
            self.skipped_paths.append(_display(path))

    def error(self, path: str, exc: OSError) -> None:
        self.errors += 1
        if len(self.error_samples) < MAX_SAMPLES:
            reason = exc.strerror or (os.strerror(exc.errno) if exc.errno else str(exc))
            self.error_samples.append(f"{_display(path)}: {reason}")

    def result(self, state: str, started_at: datetime, message: str) -> RepairResult:
        return RepairResult(
            share="",
            state=state,
            started_at=started_at,
            finished_at=None if state == "running" else datetime.now(timezone.utc),
            visited=self.visited,
            changed=self.changed,
            skipped=dict(self.skipped),
            skipped_paths=list(self.skipped_paths),
            errors=self.errors,
            error_samples=list(self.error_samples),
            message=message,
        )


class _Walk:
    """The steps of one repair's walk."""

    def __init__(
        self,
        *,
        gid: int,
        root_dev: int,
        root_mount: int,
        folder: tuple[str, ...],
        mount_points: set[str],
        file_flags: int,
        deadline: float,
        max_entries: int | None,
        max_depth: int,
        started_at: datetime,
        progress: Callable[[RepairResult], None] | None,
        stop: threading.Event | None,
    ) -> None:
        self.gid = gid
        self.root_dev = root_dev
        self.root_mount = root_mount  # the mount ID of the share's folder
        self.folder = folder  # the share's folder, as path components
        self.mount_points = mount_points  # relative paths
        self.file_flags = file_flags
        self.deadline = deadline
        self.max_entries = max_entries
        self.max_depth = max_depth
        self.started_at = started_at
        self.progress = progress
        self.next_report = time.monotonic() + PROGRESS_INTERVAL_SECONDS
        self.stop = stop
        self.tally = _Tally()

    def run(self, stack: list[_Folder], root: os.stat_result) -> str | None:
        """Fix the share's folder (the only folder on ``stack``), then visit everything below it.

        Returns None when the walk is done, or the message of why it stopped
        early (a limit, or ``stop``). A folder's descriptor is closed when the
        folder is taken off the stack; the caller closes the descriptors still
        on it.
        """
        self.tally.visited += 1
        self._fix(".", root, constants.SHARE_DIR_MODE, stack[0].fd, through_proc=False)
        while stack:
            folder = stack[-1]
            name = next(folder.names, None)
            if name is None:
                stack.pop()
                _close(folder.fd)
                continue
            stopped = self._stopped()
            if stopped is not None:
                return stopped
            self.tally.visited += 1
            child = self._visit(folder, name, f"{folder.path}/{name}" if folder.path else name)
            if child is not None:
                stack.append(child)
        return None

    def _stopped(self) -> str | None:
        """Why the walk stops before its next entry, or None.

        When a report is due, it reports the progress and reads the mount
        table again: a mount made inside the share while it is walked, such
        as an app's FUSE mount, is skipped by its path from then on.
        """
        if self.stop is not None and self.stop.is_set():
            return STOPPED_MESSAGE
        now = time.monotonic()
        if now >= self.deadline or (self.max_entries is not None and self.tally.visited >= self.max_entries):
            return INCOMPLETE_MESSAGE
        if now >= self.next_report:
            self.next_report = now + PROGRESS_INTERVAL_SECONDS
            if self.progress is not None:
                self.progress(self.tally.result("running", self.started_at, ""))
            try:
                self.mount_points = _mount_points_inside(_read_mounts(), self.folder)
            except OSError:
                pass  # the last ones stay; every opened entry is still checked by its mount ID
        return None

    def _visit(self, parent: _Folder, name: str, path: str) -> _Folder | None:
        """Examine one entry; returns it when it is a folder to enter."""
        if path in self.mount_points:
            # Skipped by its path alone: an lstat would already reach into the
            # mounted filesystem, whose driver can block (a stalled FUSE daemon
            # or NFS server), and no time limit stops a blocked call.
            if name == ".snapshots":
                self.tally.skip("snapshots")
            else:
                self.tally.skip("other_filesystem", path)
            return None
        try:
            st = os.lstat(name, dir_fd=parent.fd)
        except FileNotFoundError:
            self.tally.skip("raced")  # removed since its folder was listed
            return None
        except OSError as exc:
            self.tally.error(path, exc)
            return None
        if stat.S_ISLNK(st.st_mode):
            self.tally.skip("symlink")
        elif stat.S_ISDIR(st.st_mode):
            return self._folder(parent, name, path, st)
        elif stat.S_ISREG(st.st_mode):
            self._file(parent, name, path, st)
        else:
            self.tally.skip("special")  # FIFO, socket, device node
        return None

    def _folder(self, parent: _Folder, name: str, path: str, st: os.stat_result) -> _Folder | None:
        if name == ".snapshots":
            self.tally.skip("snapshots")
            return None
        if st.st_dev != self.root_dev:  # a btrfs subvolume (volume, snapshot) or another filesystem
            self.tally.skip("other_filesystem", path)
            return None
        if _private(st.st_mode):
            self.tally.skip("private")
            return None
        if parent.depth + 1 > self.max_depth:
            self.tally.skip("too_deep")
            return None
        try:
            fd = os.open(name, _FOLDER_FLAGS, dir_fd=parent.fd)
        except OSError as exc:
            self._open_failed(path, exc)
            return None
        child = None
        try:
            current = os.fstat(fd)
            if not _same_entry(current, st):
                self.tally.skip("raced")
            elif _mount_id(fd) != self.root_mount:
                # A mount point that its path did not show: a bind mount of the
                # same filesystem whose parent was renamed after the mount
                # table was read.
                self.tally.skip("other_filesystem", path)
            elif _private(current.st_mode):  # made private since its lstat
                self.tally.skip("private")
            else:
                self._fix(path, current, constants.SHARE_DIR_MODE, fd, through_proc=False)
                child = _Folder(fd, iter(os.listdir(fd)), path, parent.depth + 1)
        except OSError as exc:
            self.tally.error(path, exc)
        finally:
            if child is None:
                _close(fd)
        return child

    def _file(self, parent: _Folder, name: str, path: str, st: os.stat_result) -> None:
        if st.st_nlink > 1:
            self.tally.skip("hardlink")
            return
        if st.st_dev != self.root_dev:
            self.tally.skip("other_filesystem", path)
            return
        if _private(st.st_mode):
            self.tally.skip("private")
            return
        try:
            fd = os.open(name, self.file_flags, dir_fd=parent.fd)
        except OSError as exc:
            self._open_failed(path, exc)
            return
        try:
            current = os.fstat(fd)
            if not (_same_entry(current, st) and stat.S_ISREG(current.st_mode) and current.st_nlink == 1):
                self.tally.skip("raced")
            elif _mount_id(fd) != self.root_mount:  # a file bind-mounted where its path did not show it
                self.tally.skip("other_filesystem", path)
            elif _private(current.st_mode):  # made private since its lstat
                self.tally.skip("private")
            else:
                # A regular file that is not private: 0660, or 0770 (ADR-0396 gives copies the same).
                mode = constants.share_entry_mode(current.st_mode)
                if mode is not None:
                    self._fix(path, current, mode, fd, through_proc=True)
        except OSError as exc:
            self.tally.error(path, exc)
        finally:
            _close(fd)

    def _fix(self, path: str, st: os.stat_result, mode: int, fd: int, *, through_proc: bool) -> None:
        """Give the entry open at ``fd`` the group and exactly ``mode``, when they differ.

        Owners never change. chown can clear the setgid bit, so chmod runs
        after it. A folder is changed through its descriptor; a file, whose
        O_PATH descriptor takes no fchown or fchmod, through its
        /proc/self/fd entry, which leads to that inode without a path lookup.
        """
        if (st.st_gid, stat.S_IMODE(st.st_mode)) == (self.gid, mode):
            return
        try:
            if through_proc:
                target = f"/proc/self/fd/{fd}"
                if st.st_gid != self.gid:
                    os.chown(target, -1, self.gid)
                os.chmod(target, mode)
            else:
                if st.st_gid != self.gid:
                    os.fchown(fd, -1, self.gid)
                os.fchmod(fd, mode)
        except OSError as exc:
            self.tally.error(path, exc)
        else:
            self.tally.changed += 1

    def _open_failed(self, path: str, exc: OSError) -> None:
        if exc.errno in _RACED_ERRNOS:
            self.tally.skip("raced")  # removed, or swapped for a symlink, since its lstat
        else:
            self.tally.error(path, exc)


def _read_mounts() -> list[tuple[int | None, tuple[str, ...]]]:
    """(mount ID, mount point) of every line of /proc/self/mountinfo, read once.

    The mount ID is field 1 (None if it is not a number), the mount point
    field 5 with its octal escapes decoded, as path components. OSError when
    the table cannot be read.
    """
    with open(MOUNTINFO_PATH, "rb") as file:
        lines = file.read().split(b"\n")
    mounts = []
    for line in lines:
        fields = line.split(b" ")
        if len(fields) < 5:
            continue
        mount_id = int(fields[0]) if fields[0].isdigit() else None
        point = PurePosixPath(os.fsdecode(_OCTAL_ESCAPE.sub(_unescape, fields[4]))).parts
        mounts.append((mount_id, point))
    return mounts


def _mount_points_inside(mounts: list[tuple[int | None, tuple[str, ...]]], folder: tuple[str, ...]) -> set[str]:
    """The mount points strictly inside ``folder`` (path components), relative to it.

    A bind mount of the same filesystem has the share's st_dev, so only the
    mount table shows it.
    """
    return {
        "/".join(point[len(folder):])
        for _, point in mounts
        if len(point) > len(folder) and point[: len(folder)] == folder
    }


def _mount_id(fd: int) -> int:
    """The ID of the mount that the open descriptor ``fd`` is on.

    It is the mnt_id line of /proc/self/fdinfo/<fd>, the ID that field 1 of
    mountinfo gives each mount. Unlike a path, a rename cannot change it.
    OSError when it cannot be read.
    """
    info = os.open(f"{FDINFO_DIR}/{fd}", os.O_RDONLY | os.O_CLOEXEC)
    try:
        data = os.read(info, 4096)
    finally:
        os.close(info)
    for line in data.split(b"\n"):
        if line.startswith(b"mnt_id:"):
            value = line[len(b"mnt_id:"):].strip()
            if value.isdigit():
                return int(value)
    raise OSError(errno.EINVAL, f"{FDINFO_DIR}/{fd} has no mount ID")


def _unescape(match: re.Match[bytes]) -> bytes:
    value = int(match.group(1), 8)
    return bytes([value]) if value < 256 else match.group(0)


class _Refused(ValueError):
    """The share's folder is one that a repair never walks; the message says why."""


def _refusal(parts: tuple[str, ...]) -> str | None:
    """Why a repair never walks the folder with the components ``parts`` below the pools base, or None.

    These folders are never shared (ADR-0145), and
    service.share_problem() refuses them before a repair. They are checked
    again on the path that is walked: app data and snapshots must never get
    the SMB group.
    """
    if len(parts) < 2:
        # A pool root holds app data, volumes and snapshots (rule 3).
        return "it is not a folder inside a pool"
    if parts[1] == "apps":
        return "it is app data (the apps folder of a pool), which is never repaired"
    if ".snapshots" in parts:
        return "it is in a snapshot folder (.snapshots), which is never repaired"
    return None


def _private(mode: int) -> bool:
    """Only the owner can use the entry: no group or other bits (0600, 0700)."""
    return mode & 0o077 == 0


def _same_entry(opened: os.stat_result, examined: os.stat_result) -> bool:
    return (opened.st_dev, opened.st_ino) == (examined.st_dev, examined.st_ino)


def _display(path: str) -> str:
    """A relative path for the answer: bytes that are not UTF-8 become U+FFFD (JSON needs text)."""
    return os.fsencode(path).decode("utf-8", "replace")


def _reason(exc: Exception) -> str:
    if isinstance(exc, _Refused):
        return str(exc)
    if isinstance(exc, OSError):
        if exc.errno == errno.ELOOP:
            return "a part of its path is a symbolic link"
        return exc.strerror or str(exc)
    return "it is not a folder inside a pool"


def _close(fd: int) -> None:
    try:
        os.close(fd)
    except OSError:
        pass
