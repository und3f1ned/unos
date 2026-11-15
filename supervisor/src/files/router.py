"""File manager API.

Every path is confined to the pools base directory (``UNOS_POOLS_BASE_PATH``).
The supervisor runs as root in a privileged container that shares the host's
PID namespace, so a path that escapes the base (``..``, an absolute upload
file name, or a symlink inside a pool pointing at ``/proc/1/root/...``) would
give the caller read and write access to the whole host. Pool contents are
writable by SMB users and app containers, so symlinks below the base are
untrusted.

Paths are therefore never resolved by the kernel in one go. They are split
into components (``..`` is refused), and each component is opened relative to
its parent's descriptor with ``O_NOFOLLOW`` starting at the base directory
(see ``open_dir_nofollow``). All operations then use those directory
descriptors:

* symlinks below the base are never followed; they are listed, renamed,
  moved, copied and deleted as links;
* a directory that is replaced by a symlink after a check cannot redirect the
  operation, because the check and the operation use the same descriptor.

The pools base itself and the pool roots (``<base>/<pool>``) cannot be
renamed, moved or deleted, and nothing can be created directly in the base.
Neither can volumes and other btrfs subvolumes or the mount points of other
filesystems (``_refuse_separate_filesystem``), and a folder that contains one,
or a bind mount, cannot be deleted, or moved to another pool or volume
(``_refuse_nested_filesystems``).

Delete moves items into the trash of their pool or volume, from where they
are restored, deleted for good, or deleted for good by the daily expiry
after TRASH_RETENTION_DAYS (ADR-0259, "the trash" below).

New folders and uploads in a share folder (_share_folder: a folder set up
for the SMB group as UNOS makes a share's) get the modes that Samba gives
the entries of SMB users, 2770 and 0660, so that the share's SMB users can
change them at once (ADR-0273); elsewhere they get what the umask leaves.
"""

import asyncio
import ctypes
import errno
import grp
import logging
import os
import pwd
import re
import secrets
import shutil
import stat
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from typing import BinaryIO
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..auth.utils import verify_token
from ..config import get_settings
from ..request_limits import UPLOAD_OPENAPI, UploadSink, receive_upload
from ..security.validators import open_dir_nofollow, open_subdirs_nofollow
from ..shares import constants as smb_constants
from ..storage import spindown
from .schemas import (
    CreateDirectoryRequest,
    DirectoryListing,
    FileInfo,
    FileType,
    RenameRequest,
    TrashItem,
    TrashListing,
    UploadLeftover,
    UploadLeftovers,
)

router = APIRouter(prefix="/files", tags=["files"])
logger = logging.getLogger(__name__)

settings = get_settings()

# Directory the file manager is confined to. Module level so tests can move it.
BASE_PATH = settings.pools_base_path
# The mount table of the supervisor's mount namespace. Module level so tests can move it.
MOUNTINFO = "/proc/self/mountinfo"
# /proc/self/fdinfo/<fd> names the mount that an open descriptor is on (mnt_id).
FDINFO = "/proc/self/fdinfo"

_CHUNK_SIZE = 1024 * 1024
_NAME_MAX_BYTES = 255
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
# O_NONBLOCK so that opening a FIFO cannot hang; it does not affect regular files.
_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
# O_EXCL with O_CREAT never follows a symlink and fails if the name exists.
_CREATE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
# Linux: an upload is written to an unnamed file in its folder (O_TMPFILE;
# without O_EXCL, so that it can be linked into the folder when it is
# complete). None where the platform has no O_TMPFILE (macOS). Module level so
# tests can turn it off.
_UNNAMED_FLAGS = (os.O_TMPFILE | os.O_WRONLY | os.O_CLOEXEC) if hasattr(os, "O_TMPFILE") else None
# What opening an unnamed file answers where there cannot be one: EOPNOTSUPP
# from a filesystem without O_TMPFILE (such as vfat, exfat, NFS, SMB, and FUSE
# ones like NTFS-3G); EISDIR from a kernel before 3.11, which does not know
# the flag and sees a folder opened for writing; EINVAL where the flags are
# refused. The upload then uses a named temporary file. Pools are btrfs,
# which has O_TMPFILE since Linux 3.16 (ext4 and tmpfs since 3.11).
_NO_UNNAMED_FILE = frozenset({errno.EOPNOTSUPP, errno.ENOTSUP, errno.EISDIR, errno.EINVAL})
# The open descriptors of this process by number: an unnamed file is linked
# into its folder through them. Module level so tests can move it.
_PROC_FD = "/proc/self/fd"
# A directory that was listed and then removed, or replaced by a file or a symlink.
_GONE = (errno.ENOENT, errno.ENOTDIR, errno.ELOOP)
# The kernel writes space, tab, newline and backslash in a mount point as \040, \011, \012, \134.
_MOUNTINFO_ESCAPE = re.compile(rb"\\([0-3][0-7]{2})")
_MOVED_ELSEWHERE = "moved to another pool or volume"

# The trash (ADR-0259): the folder in the top folder of each pool and volume
# that takes what is deleted there. Its name is reserved: the file manager
# lists, opens and makes no entry of that name, and copies leave it out.
TRASH = ".unos-trash"
# Days that a deleted item stays in the trash before the expiry deletes it for good.
TRASH_RETENTION_DAYS = 30
# Seconds between two passes of the expiry over a pool (the first one runs at the start).
TRASH_EXPIRY_INTERVAL = 24 * 60 * 60
# Seconds between two checks for the pools whose pass is due: a pool whose disks sleep is passed
# over, so as not to spin them up, and its pass runs at the first check after they woke; a check
# with no pass due asks no disk anything (ADR-0378).
TRASH_EXPIRY_CHECK = 60 * 60
# In the trash, each deleted item has a folder of its own, named after the
# time of its deletion in UTC and 16 random hex digits: the item is in it as
# _TRASH_ITEM, and its path below the pool or volume, as bytes, in _TRASH_ORIGIN.
_TRASH_ENTRY = re.compile(r"\d{8}T\d{6}Z-[0-9a-f]{16}")
_TRASH_STAMP = "%Y%m%dT%H%M%SZ"
_TRASH_ITEM = "item"
_TRASH_ORIGIN = "origin"
# More than any path can be (PATH_MAX is 4096 bytes): a longer origin is damaged.
_ORIGIN_MAX_BYTES = 64 * 1024
_FOR_GOOD = "deleted for good"
_TRASH_REFUSED = f"The trash ({TRASH}) is reached through Trash, not as a folder"


# --- paths -----------------------------------------------------------------


def _base_dir() -> str:
    return os.path.realpath(BASE_PATH)


def _components(path: str) -> list[str]:
    """Return the components of a client path below the base directory.

    The path must be absolute and lie below the base (component-wise, so
    ``/mnt/pools-old`` is not inside ``/mnt/pools``). ``..`` is refused
    rather than normalized away.
    """
    if not path or "\x00" in path:
        raise HTTPException(400, "Invalid path")
    if not path.startswith("/"):
        raise HTTPException(400, "Path must be absolute")
    parts = [p for p in path.split("/") if p not in ("", ".")]
    if ".." in parts:
        raise HTTPException(403, "Access denied")
    for base in (os.path.normpath(BASE_PATH), _base_dir()):
        base_parts = [p for p in base.split("/") if p]
        if parts[: len(base_parts)] == base_parts:
            return parts[len(base_parts):]
    raise HTTPException(403, "Access denied")


def _split(path: str) -> list[str]:
    """The components (_components) of a path of the file manager, which never leads into a trash.

    The trash API alone reads and changes the trash (ADR-0259).
    """
    parts = _components(path)
    if TRASH in parts:
        raise HTTPException(403, _TRASH_REFUSED)
    return parts


def _display_path(parts: list[str]) -> str:
    return os.path.join(_base_dir(), *parts)


def _validate_name(name: str) -> str:
    """Validate a new single-component file or directory name."""
    if not isinstance(name, str) or name in ("", ".", ".."):
        raise HTTPException(400, "Invalid name")
    if "/" in name or any(ord(c) < 0x20 or ord(c) == 0x7F for c in name):
        raise HTTPException(400, "Invalid name: '/' and control characters are not allowed")
    if name == TRASH:
        raise HTTPException(400, f"Invalid name: {TRASH} is the name of the trash")
    try:
        encoded = name.encode("utf-8")
    except UnicodeEncodeError:
        raise HTTPException(400, "Invalid name") from None
    if len(encoded) > _NAME_MAX_BYTES:
        raise HTTPException(400, "Name too long")
    return name


def _upload_name(filename: str | None) -> str:
    """Reduce a client-supplied upload file name to a single safe component.

    Browsers normally send only the base name, but some send a full Windows
    path, and a hostile client can send anything, e.g. ``../../etc/cron.d/x``.
    """
    if not filename:
        raise HTTPException(400, "Missing file name")
    return _validate_name(filename.replace("\\", "/").rsplit("/", 1)[-1])


def _refuse_protected(parts: list[str]) -> None:
    """Refuse operations on the base directory itself and on pool roots."""
    if not parts:
        raise HTTPException(403, "The pools directory cannot be modified")
    if len(parts) == 1:
        raise HTTPException(403, "Pool root directories cannot be renamed, moved or deleted")


def _require_inside_pool(parts: list[str]) -> None:
    """Refuse creating entries directly in the base directory (it holds pool mount points)."""
    if not parts:
        raise HTTPException(400, "Choose a folder inside a pool")


def _shown(name: str) -> str:
    """``name`` for a message: bytes that are not UTF-8 become U+FFFD (JSON cannot carry them)."""
    try:
        return name.encode("utf-8", "surrogateescape").decode("utf-8", "replace")
    except UnicodeEncodeError:
        return name.encode("utf-8", "replace").decode("utf-8")


def _separate_detail(name: str, action: str) -> str:
    return (
        f"'{_shown(name)}' is a separate subvolume or filesystem (such as a snapshot or a "
        f"mount point) and cannot be {action} in the file manager."
    )


def _refuse_separate_filesystem(dir_fd: int, st: os.stat_result, parts: list[str], action: str) -> None:
    """Refuse to delete, move or rename a btrfs subvolume root or the mount point of another filesystem.

    ``st`` is the lstat() of the entry ``parts`` in the directory ``dir_fd``.
    Such a directory has another st_dev than the directory it is in (btrfs
    gives every subvolume its own). Deleting a volume here would remove all
    its files and then the empty subvolume itself (Linux 4.18 and later),
    also while an app, an SMB share or an NFS export uses it, without the
    checks of the Storage page; moving or renaming it breaks those users.
    This checks the entry itself; _refuse_nested_filesystems checks the tree
    below a directory before it is deleted or moved to another filesystem.
    A bind mount from the same filesystem has the same st_dev: the mount
    table shows it, to _refuse_nested_filesystems before a delete or a move
    to another filesystem, and to _refuse_separate_filesystems before any
    move; renaming it fails with EBUSY, which _rename answers with this
    refusal (_refuse_busy, ADR-0348).
    """
    if not stat.S_ISDIR(st.st_mode) or st.st_dev == os.fstat(dir_fd).st_dev:
        return
    if len(parts) == 2:  # <base>/<pool>/<volume>
        raise HTTPException(
            409,
            f"'{_shown(parts[-1])}' is a volume and cannot be {action} in the file manager. "
            "Volumes are deleted on the Storage page (Volumes).",
        )
    raise HTTPException(409, _separate_detail(parts[-1], action))


def _refuse_busy(exc: OSError, name: str, action: str) -> None:
    """Refuse (409) the rename of ``name`` that failed with ``exc``, when it is EBUSY: a mount point.

    With the text of a delete of it (ADR-0348). This answers a rename, and a
    move of a mount point that appeared after the check of every item
    (_refuse_separate_filesystems), or that the mount table could not show.
    Any other error is left to the caller.
    """
    if exc.errno == errno.EBUSY:
        raise HTTPException(409, _separate_detail(name, action)) from None


def _read_mount_points() -> list[bytes]:
    """The mount points in MOUNTINFO, decoded (the fifth field of a line); OSError if it cannot be read."""
    try:
        with open(MOUNTINFO, "rb") as table:
            lines = table.read().split(b"\n")
    except OSError as exc:
        logger.error("Cannot read the mount table %s: %s", MOUNTINFO, exc)
        raise
    points = []
    for line in lines:
        fields = line.split(b" ")
        if len(fields) > 4:
            points.append(_MOUNTINFO_ESCAPE.sub(lambda m: bytes([int(m[1], 8)]), fields[4]))
    return points


def _mount_points(name: str, action: str) -> list[bytes]:
    """The mount points in MOUNTINFO, decoded.

    If the table cannot be read, ``name`` is refused (HTTP 500): nothing is
    deleted or moved without the check.
    """
    try:
        return _read_mount_points()
    except OSError:
        raise HTTPException(
            500,
            f"Could not check '{_shown(name)}' for mount points (the mount table cannot be read), "
            f"so it was not {action}.",
        ) from None


def _subdirectories(fd: int) -> list[str]:
    """The directories in the directory ``fd`` (symlinks are not), in reverse order of their names."""
    with os.scandir(fd) as entries:
        names = [entry.name for entry in entries if entry.is_dir(follow_symlinks=False)]
    return sorted(names, reverse=True)


def _first_other_device(top_fd: int) -> str | None:
    """The first directory below ``top_fd`` with another st_dev, relative to ``top_fd``; or None.

    Walks the tree top-down without following symlinks, with one open
    descriptor per level of depth.
    """
    device = os.fstat(top_fd).st_dev
    # Per level: its descriptor, its path, the subdirectories still to check.
    stack: list[tuple[int, str, list[str]]] = [(top_fd, "", _subdirectories(top_fd))]
    try:
        while stack:
            fd, path, names = stack[-1]
            if not names:
                stack.pop()
                if fd != top_fd:
                    os.close(fd)
                continue
            name = names.pop()
            try:
                child_fd = os.open(name, _DIR_FLAGS, dir_fd=fd)
            except OSError as exc:
                if exc.errno in _GONE:
                    continue
                raise
            child = f"{path}/{name}" if path else name
            below: list[str] = []
            stack.append((child_fd, child, below))
            if os.fstat(child_fd).st_dev != device:
                return child
            below.extend(_subdirectories(child_fd))
        return None
    finally:
        for fd, _, _ in stack:
            if fd != top_fd:
                os.close(fd)


def _refuse_nested_filesystems(
    dir_fd: int, parts: list[str], action: str, mount_points: list[bytes] | None = None
) -> None:
    """Refuse when a subvolume, snapshot or mount point lies at or below the directory ``parts``.

    ``dir_fd`` is the directory that contains it. The copy of a move to
    another filesystem goes into subvolumes and mounted filesystems below a
    folder, and a delete of the folder would stop at them part way
    (_remove_tree): the folder is refused before anything is changed. A
    subvolume or another filesystem has another st_dev than
    the folder; a bind mount from the same filesystem does not, so the mount
    table is checked as well. ``mount_points`` is a table read before
    (_mount_points); by default it is read now.
    """
    name = parts[-1]
    if mount_points is None:
        mount_points = _mount_points(name, action)
    folder = os.fsencode(_display_path(parts))
    below = folder + b"/"
    mounted = sorted(point for point in mount_points if point == folder or point.startswith(below))
    if mounted and mounted[0] == folder:
        raise HTTPException(409, _separate_detail(name, action))
    if mounted:
        nested = mounted[0][len(below):].decode("utf-8", "replace")
    else:
        fd = os.open(name, _DIR_FLAGS, dir_fd=dir_fd)
        try:
            other = _first_other_device(fd)
        finally:
            os.close(fd)
        if other is None:
            return
        nested = _shown(other)
    raise HTTPException(
        409,
        f"'{_shown(name)}' contains '{nested}', a separate subvolume or filesystem (such as a "
        f"snapshot or a mount point), and cannot be {action} in the file manager.",
    )


def _refuse_separate_filesystems(plan: list[list[str]], action: str, *, moving_to: int | None = None) -> None:
    """Check every entry before any is changed; each is checked again right before its change.

    Directories are also checked for subvolumes and mount points below them:
    all of them before a delete; before a move (``moving_to`` is the st_dev
    of the destination), those on another filesystem, which are copied and
    then deleted. Before a move, every entry, a file too, is refused when it
    is a mount point itself, such as a bind mount, whatever its st_dev
    (ADR-0348): rename(2) would refuse it with EBUSY, or a move to another
    filesystem would copy it and then fail to delete it (EBUSY), only after
    the entries before it had moved. Without the mount table, that is left
    to rename(2) and the delete (_readable_mount_points, _refuse_busy).
    """
    mount_points = None
    within = _readable_mount_points() if moving_to is not None else None
    for parts in plan:
        dir_fd = _open_parent_or_none(parts)
        if dir_fd is None:
            continue
        try:
            st = _lstat_at(dir_fd, parts[-1])
            if st is None:
                continue
            _refuse_separate_filesystem(dir_fd, st, parts, action)
            if within is not None and os.fsencode(_display_path(parts)) in within:
                raise HTTPException(409, _separate_detail(parts[-1], action))
            if moving_to is not None and st.st_dev == moving_to:
                continue
            if not stat.S_ISDIR(st.st_mode):
                continue
            nested_action = action if moving_to is None else _MOVED_ELSEWHERE
            if mount_points is None:
                mount_points = _mount_points(parts[-1], nested_action)
            _refuse_nested_filesystems(dir_fd, parts, nested_action, mount_points)
        finally:
            os.close(dir_fd)


# --- errors ----------------------------------------------------------------


def _http_error(exc: OSError) -> HTTPException:
    """Map an OSError to an HTTP error without leaking host paths.

    EBUSY is a conflict (409), not a failure: the kernel refuses to change an
    entry that is in use by the system, such as a mount point (ADR-0348).
    """
    code = exc.errno
    if code == errno.ENOENT:
        return HTTPException(404, "Not found")
    if code == errno.ELOOP:
        return HTTPException(403, "Access denied: symbolic links are not followed")
    if code == errno.ENOTDIR:
        return HTTPException(400, "Not a directory")
    if code == errno.EISDIR:
        return HTTPException(400, "Is a directory")
    if code in (errno.EEXIST, errno.ENOTEMPTY):
        return HTTPException(400, "Already exists")
    if code in (errno.EACCES, errno.EPERM, errno.EROFS):
        return HTTPException(403, "Permission denied")
    if code == errno.ENAMETOOLONG:
        return HTTPException(400, "Name too long")
    if code in (errno.ENOSPC, errno.EDQUOT):
        return HTTPException(507, "No space left on device")
    if code == errno.EBUSY:
        return HTTPException(409, "In use by the system, for example as a mount point, so the file manager cannot change it")
    reason = os.strerror(code) if code else "unknown error"
    return HTTPException(500, f"File operation failed: {reason}")


async def _run(func, *args):
    """Run a blocking file operation in a worker thread and map OS errors."""
    try:
        return await asyncio.to_thread(func, *args)
    except HTTPException:
        raise
    except OSError as exc:
        raise _http_error(exc) from exc
    except ValueError as exc:  # e.g. undecodable names
        raise HTTPException(400, "Invalid path") from exc


# --- descriptors -----------------------------------------------------------


def _open_dir(parts: list[str]) -> int:
    return open_dir_nofollow(_base_dir(), parts)


def _lstat_at(dir_fd: int, name: str) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _open_parent_or_none(parts: list[str]) -> int | None:
    """Open the parent directory of ``parts``; None if it does not exist."""
    try:
        return _open_dir(parts[:-1])
    except FileNotFoundError:
        return None


def _share_folder(dir_fd: int) -> bool:
    """Whether the open folder ``dir_fd`` is set up for the SMB group as a share's folder (ADR-0273).

    It is setgid, the group unos-smb may read, write and search it, and
    others nothing (constants.share_root_mode_ok): as UNOS makes a share's
    folder, Repair permissions every folder of a share that is not private,
    and Samba every folder that an SMB user creates. The kernel gives a new
    entry in it the folder's group, and whoever is in that group can already
    replace the entry: the share's modes give the group nothing that the
    folder does not.
    """
    st = os.fstat(dir_fd)
    return smb_constants.share_root_mode_ok(st.st_mode, st.st_gid)


def _file_type(st: os.stat_result) -> FileType:
    """What an entry is, from its lstat() result."""
    if stat.S_ISLNK(st.st_mode):
        return FileType.SYMLINK
    if stat.S_ISDIR(st.st_mode):
        return FileType.DIRECTORY
    return FileType.FILE


def get_file_info(name: str, path: str, st: os.stat_result) -> FileInfo:
    """Describe a directory entry from its lstat() result (links are not followed)."""
    file_type = _file_type(st)

    try:
        owner = pwd.getpwuid(st.st_uid).pw_name
    except KeyError:
        owner = str(st.st_uid)

    try:
        group = grp.getgrgid(st.st_gid).gr_name
    except KeyError:
        group = str(st.st_gid)

    return FileInfo(
        name=name,
        path=path,
        type=file_type,
        size=st.st_size if file_type == FileType.FILE else 0,
        modified=datetime.fromtimestamp(st.st_mtime).isoformat(),
        permissions=stat.filemode(st.st_mode),
        owner=owner,
        group=group,
    )


# --- operations (blocking, run in worker threads) ----------------------------


def _list_directory(path: str | None) -> DirectoryListing:
    parts = _split(path) if path else []
    fd = _open_dir(parts)
    try:
        items = []
        with os.scandir(fd) as entries:
            for entry in entries:
                if entry.name == TRASH:  # listed with GET /files/trash (ADR-0259)
                    continue
                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                items.append(get_file_info(entry.name, _display_path([*parts, entry.name]), st))
    finally:
        os.close(fd)

    items.sort(key=lambda item: (item.type != FileType.DIRECTORY, item.name.lower()))
    return DirectoryListing(
        path=_display_path(parts),
        parent=_display_path(parts[:-1]) if parts else None,
        items=items,
        total_size=sum(item.size for item in items),
        item_count=len(items),
    )


def _file_info(path: str) -> FileInfo:
    parts = _split(path)
    if not parts:
        base = _base_dir()
        return get_file_info(os.path.basename(base), base, os.stat(base))
    fd = _open_dir(parts[:-1])
    try:
        st = os.stat(parts[-1], dir_fd=fd, follow_symlinks=False)
    finally:
        os.close(fd)
    return get_file_info(parts[-1], _display_path(parts), st)


def _open_download(path: str) -> tuple[BinaryIO, str, int]:
    parts = _split(path)
    if not parts:
        raise HTTPException(400, "Can only download files")
    dir_fd = _open_dir(parts[:-1])
    try:
        fd = os.open(parts[-1], _READ_FLAGS, dir_fd=dir_fd)
    finally:
        os.close(dir_fd)
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode):
        os.close(fd)
        raise HTTPException(400, "Can only download files")
    return os.fdopen(fd, "rb"), parts[-1], st.st_size


def _iter_file(handle: BinaryIO) -> Iterator[bytes]:
    with handle:
        while chunk := handle.read(_CHUNK_SIZE):
            yield chunk


def _content_disposition(filename: str) -> str:
    # Same format as starlette's FileResponse; quote() also neutralizes quotes and CR/LF.
    quoted = quote(filename, errors="surrogateescape")
    if quoted != filename:
        return f"attachment; filename*=utf-8''{quoted}"
    return f'attachment; filename="{filename}"'


def _upload_temp_name() -> str:
    """A new hidden name for the file of an upload in its folder, before it gets its own."""
    return f".unos-upload-{secrets.token_hex(8)}"


# The hidden names (_upload_temp_name) of this supervisor's uploads in progress, from before their
# file is created until it has its own name or is removed (_FileUpload.discard): the check of
# leftovers never lists or removes a file of such a name (ADR-0354). O_EXCL keeps a new upload
# from taking the name of a file that exists, so a leftover never becomes an upload's file.
_uploads_in_progress: set[str] = set()
_uploads_lock = threading.Lock()


def _linkable(fd: int) -> bool:
    """Whether the unnamed file ``fd`` can be linked through _PROC_FD (/proc is mounted, and is ours)."""
    try:
        return os.path.samestat(os.stat(f"{_PROC_FD}/{fd}"), os.fstat(fd))
    except OSError:
        return False


def _removed(dir_fd: int) -> bool:
    """Whether the folder ``dir_fd`` was removed: on Linux a removed folder has no links left.

    A deleted btrfs subvolume keeps one: an upload into a volume deleted
    meanwhile is refused only when its file is linked (404 then).
    """
    return os.fstat(dir_fd).st_nlink == 0


class _FileUpload(UploadSink):
    """An upload into the folder ``path``, for request_limits.receive_upload.

    On Linux the file arrives in an unnamed file in the folder (O_TMPFILE):
    nothing of it can be seen there (in the file manager, over SMB) before
    it is complete, and if the supervisor stops in the middle (a restart, an
    update, a crash), the kernel frees what had arrived. Once the file is
    complete, it is linked into the folder through /proc/self/fd (linkat with
    AT_SYMLINK_FOLLOW: no capability is needed for a file opened without
    O_EXCL). A link never replaces an entry: a new name is linked at once;
    where the name exists (a file or a symlink to replace), the file is
    linked under a new hidden temporary name and renamed over it.

    Where the platform has no O_TMPFILE (macOS), the filesystem of the
    folder cannot create unnamed files (_NO_UNNAMED_FILE), or /proc/self/fd
    cannot be used, the file arrives in a hidden temporary file instead,
    created through the folder's descriptor (O_EXCL, O_NOFOLLOW) and renamed
    over the target name once it is complete. If the supervisor stops in the
    middle, that file stays.

    Either way, rename() replaces a symlink at the target instead of
    following it, a folder there is refused, and an upload that fails before
    its file has its name leaves nothing behind: discard closes the unnamed
    file (the kernel frees it with its last descriptor), or removes the
    temporary file. Every hidden name it takes is in _uploads_in_progress
    from before it is used until discard, so that the check of leftovers
    leaves that file alone (ADR-0354).

    In a share folder (_share_folder) the file gets the mode of the files of
    SMB users, 0660, through its descriptor as soon as it exists: before any
    of it is written and before it has a name, also when it replaces a file
    (ADR-0273). Elsewhere it has what the umask leaves of 0666.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self.parts: list[str] = []
        self.dir_fd: int | None = None
        self.name = ""
        # The sink's own descriptor of an unnamed file, to link it:
        # receive_upload closes the one it writes to before finish().
        self.unnamed_fd: int | None = None
        # A temporary name in the folder, once this upload has created it.
        self.tmp_name: str | None = None
        # The hidden names this upload took, in _uploads_in_progress until discard().
        self.hidden_names: list[str] = []

    def prepare(self) -> None:
        """Before the body is read: the folder is inside a pool, and exists."""
        self.parts = _split(self.path)
        _require_inside_pool(self.parts)
        self.dir_fd = _open_dir(self.parts)

    def open(self, filename: str) -> int:
        name = _upload_name(filename)
        existing = _lstat_at(self.dir_fd, name)
        if existing is not None and stat.S_ISDIR(existing.st_mode):
            raise HTTPException(400, f"A folder named {name} already exists")
        self.name = name
        share = _share_folder(self.dir_fd)
        fd = self._open_unnamed()
        if fd is None:
            tmp_name = self._hidden_name()
            fd = os.open(tmp_name, _CREATE_FLAGS, 0o666, dir_fd=self.dir_fd)
            self.tmp_name = tmp_name
        if share:
            try:
                os.fchmod(fd, smb_constants.SHARE_FILE_MODE)
            except BaseException:
                os.close(fd)  # discard() frees the unnamed file, or removes the hidden one
                raise
        return fd

    def _hidden_name(self) -> str:
        """A new hidden name for this upload's file, known as an upload's in progress before it is used."""
        name = _upload_temp_name()
        with _uploads_lock:
            _uploads_in_progress.add(name)
        self.hidden_names.append(name)
        return name

    def _open_unnamed(self) -> int | None:
        """A new unnamed file in the folder (a descriptor for receive_upload); None where there cannot be one."""
        if _UNNAMED_FLAGS is None:
            return None
        try:
            fd = os.open(".", _UNNAMED_FLAGS, 0o666, dir_fd=self.dir_fd)
        except OSError as exc:
            # Where there cannot be one, the hidden file is used: creating it
            # in a removed folder fails on its own (ENOENT).
            if exc.errno in _NO_UNNAMED_FILE:
                return None
            # A folder removed since prepare() is not found, whatever its
            # filesystem answers for an unnamed file there: ext4 refuses one
            # with EPERM, which would otherwise read as 403.
            if _removed(self.dir_fd):
                raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT)) from exc
            raise
        try:
            # The kernel refuses a new name in a folder that was removed since
            # prepare() (ENOENT), but tmpfs still creates an unnamed file there,
            # and btrfs does too by its code: refuse it now as well, not once
            # the whole file has arrived.
            if _removed(self.dir_fd):
                raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT))
            # Checked now, before any of the file is written: it can only be
            # linked into the folder through /proc/self/fd.
            linkable = _linkable(fd)
            if linkable:
                self.unnamed_fd = os.dup(fd)
        except BaseException:
            os.close(fd)
            raise
        if not linkable:
            os.close(fd)
            return None
        return fd

    def _close_unnamed(self) -> None:
        fd, self.unnamed_fd = self.unnamed_fd, None
        if fd is not None:
            os.close(fd)

    def finish(self) -> FileInfo:
        if self.unnamed_fd is not None:
            # The link follows /proc/self/fd/N to the open file. A new name
            # is linked at once. Where the name exists, the complete file gets
            # a hidden name, then its own as a named one does (only a stop
            # between the two leaves it under the hidden name). If a link
            # fails otherwise (ENOSPC, ENOENT...), nothing was named, and
            # discard closes the file.
            source = f"{_PROC_FD}/{self.unnamed_fd}"
            try:
                os.link(source, self.name, dst_dir_fd=self.dir_fd, follow_symlinks=True)
            except FileExistsError:
                tmp_name = self._hidden_name()
                os.link(source, tmp_name, dst_dir_fd=self.dir_fd, follow_symlinks=True)
                self.tmp_name = tmp_name
            self._close_unnamed()
        if self.tmp_name is not None:
            os.rename(self.tmp_name, self.name, src_dir_fd=self.dir_fd, dst_dir_fd=self.dir_fd)
            self.tmp_name = None
        st = os.stat(self.name, dir_fd=self.dir_fd, follow_symlinks=False)
        return get_file_info(self.name, _display_path([*self.parts, self.name]), st)

    def discard(self) -> None:
        try:
            self._discard()
        finally:
            # Only now: the file has its own name, or is gone.
            with _uploads_lock:
                _uploads_in_progress.difference_update(self.hidden_names)

    def _discard(self) -> None:
        # The kernel frees an unnamed file with its last descriptor
        # (receive_upload has closed the other one).
        try:
            self._close_unnamed()
        except OSError:
            pass
        if self.dir_fd is None:
            return
        try:
            if self.tmp_name is not None:
                os.unlink(self.tmp_name, dir_fd=self.dir_fd)
        except OSError:
            pass
        finally:
            os.close(self.dir_fd)
            self.dir_fd = None


def _create_directory(path: str, name: str) -> FileInfo:
    """Create the folder ``name`` in ``path``: in a share folder with the share's modes (ADR-0273)."""
    parts = _split(path)
    name = _validate_name(name)
    _require_inside_pool(parts)
    dir_fd = _open_dir(parts)
    try:
        share = _share_folder(dir_fd)
        try:
            # In a share folder private until it has the share's modes (_give_share_mode).
            os.mkdir(name, 0o700 if share else 0o777, dir_fd=dir_fd)
        except FileExistsError:
            raise HTTPException(400, f"Already exists: {name}") from None
        if share:
            _give_share_mode(dir_fd, name)
        st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    finally:
        os.close(dir_fd)
    return get_file_info(name, _display_path([*parts, name]), st)


def _give_share_mode(dir_fd: int, name: str) -> None:
    """Give the folder ``name`` that _create_directory has just made in the share folder ``dir_fd`` 2770.

    Whoever can write the share folder can put another folder at that name
    before it is opened here. Only the folder this mkdir made is changed,
    through its descriptor: the supervisor's, still private (0700) and
    empty. Any other entry there keeps its modes.

    If it cannot get the mode, the error is answered and the folder is
    removed again (_remove_new_folder): private, the share's SMB users could
    not use it, and Repair permissions skips private folders. A folder that
    stays all the same is named in the answer (_left_private).
    """
    try:
        fd = os.open(name, _DIR_FLAGS, dir_fd=dir_fd)
    except OSError as exc:
        if exc.errno in _GONE:  # removed, or replaced by a file or a symlink
            return
        raise _left_private(exc, name) from exc
    try:
        st = os.fstat(fd)
        if st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) & 0o777 != 0o700:
            return
        with os.scandir(fd) as entries:
            if next(entries, None) is not None:
                return
        os.fchmod(fd, smb_constants.SHARE_DIR_MODE)
    except OSError as exc:
        if not _remove_new_folder(dir_fd, name, fd):
            raise _left_private(exc, name) from exc
        raise
    finally:
        os.close(fd)


def _remove_new_folder(dir_fd: int, name: str, fd: int) -> bool:
    """Remove the folder ``fd`` from its name ``name`` in ``dir_fd``; whether the name no longer holds it.

    Only while the name holds that folder (samestat): an entry that has
    taken the name meanwhile stays. rmdir removes only an empty folder, so
    one put there between the check and the rmdir loses nothing, and
    whoever could put it there can remove it as well.
    """
    try:
        if not os.path.samestat(os.stat(name, dir_fd=dir_fd, follow_symlinks=False), os.fstat(fd)):
            return True
        os.rmdir(name, dir_fd=dir_fd)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return True


def _left_private(exc: OSError, name: str) -> HTTPException:
    """The answer to New Folder when its folder ``name`` did not get the modes of shares and stays private."""
    error = _http_error(exc)
    return HTTPException(
        error.status_code,
        f"{error.detail}. The new folder {name} stays private, and Repair permissions skips it: delete it",
    )


def _c_exclusive_rename() -> Callable[[int, str, int, str], None] | None:
    """The C library's rename that fails with EEXIST instead of replacing an entry; None if it has none.

    renameat2 with RENAME_NOREPLACE on Linux (glibc 2.28 and later), and
    renameatx_np with RENAME_EXCL on macOS, where the stand-in runs. The
    function takes a directory descriptor and a name in it, then the
    directory descriptor and the name it goes to, and raises OSError.
    """
    try:
        name, flag = {"linux": ("renameat2", 0x1), "darwin": ("renameatx_np", 0x4)}[sys.platform]
        function = getattr(ctypes.CDLL(None, use_errno=True), name)
    except (KeyError, OSError, AttributeError):
        return None
    function.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    function.restype = ctypes.c_int

    def exclusive_rename(src_dir_fd: int, old: str, dst_dir_fd: int, new: str) -> None:
        if function(src_dir_fd, os.fsencode(old), dst_dir_fd, os.fsencode(new), flag) != 0:
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code))

    return exclusive_rename


# Module level so tests can replace it.
_EXCLUSIVE_RENAME = _c_exclusive_rename()
# What it answers where the filesystem cannot rename that way (EINVAL: NFS,
# FUSE filesystems without it), or the kernel has no renameat2.
_NO_EXCLUSIVE_RENAME = frozenset({errno.EINVAL, errno.ENOSYS, errno.ENOTSUP, errno.EOPNOTSUPP})


def _rename_exclusive(src_dir_fd: int, name: str, dst_dir_fd: int, new_name: str) -> None:
    """Rename ``name`` in the directory ``src_dir_fd`` to ``new_name`` in ``dst_dir_fd``.

    FileExistsError if ``new_name`` exists. rename(2) replaces an entry that
    has the new name, and between a look for it and the rename an SMB user or
    an app can create one: the C library's exclusive rename refuses it in the
    same step (ADR-0232). Where there is none, or the filesystem cannot, the
    name is looked for first.
    """
    if _EXCLUSIVE_RENAME is not None:
        try:
            _EXCLUSIVE_RENAME(src_dir_fd, name, dst_dir_fd, new_name)
            return
        except OSError as exc:
            if exc.errno not in _NO_EXCLUSIVE_RENAME:
                raise
    if _lstat_at(dst_dir_fd, new_name) is not None:
        raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST))
    os.rename(name, new_name, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)


def _rename(path: str, new_name: str) -> FileInfo:
    parts = _split(path)
    new_name = _validate_name(new_name)
    _refuse_protected(parts)
    dir_fd = _open_dir(parts[:-1])
    try:
        st = _lstat_at(dir_fd, parts[-1])
        if st is None:
            raise HTTPException(404, f"Not found: {path}")
        _refuse_separate_filesystem(dir_fd, st, parts, "renamed")
        if new_name != parts[-1]:
            try:
                _rename_exclusive(dir_fd, parts[-1], dir_fd, new_name)
            except FileExistsError:
                raise HTTPException(400, f"Already exists: {new_name}") from None
            except OSError as exc:
                _refuse_busy(exc, parts[-1], "renamed")
                raise
        st = os.stat(new_name, dir_fd=dir_fd, follow_symlinks=False)
    finally:
        os.close(dir_fd)
    return get_file_info(new_name, _display_path([*parts[:-1], new_name]), st)


def _copy_entry(src_dir_fd: int, name: str, dst_dir_fd: int, without_trash: bool = False) -> None:
    """Copy ``name`` between two directories without following symlinks.

    Symlinks are recreated as symlinks, directories are copied recursively,
    and devices, FIFOs and sockets are skipped. Every destination entry is
    created exclusively, so nothing that appears in the destination while the
    copy runs (e.g. a symlink planted by another user) is written through.
    Copies are owned by root, so set-user-ID and set-group-ID bits are
    dropped from files. ``without_trash`` leaves out every entry named TRASH:
    a copy of a pool or a volume does not take what was deleted there
    (ADR-0259). A move to another filesystem copies everything, because it
    deletes the source afterwards.
    """
    st = os.stat(name, dir_fd=src_dir_fd, follow_symlinks=False)
    times = (st.st_atime_ns, st.st_mtime_ns)

    if stat.S_ISLNK(st.st_mode):
        os.symlink(os.readlink(name, dir_fd=src_dir_fd), name, dir_fd=dst_dir_fd)
        return

    if stat.S_ISDIR(st.st_mode):
        src_fd = os.open(name, _DIR_FLAGS, dir_fd=src_dir_fd)
        try:
            os.mkdir(name, 0o700, dir_fd=dst_dir_fd)
            dst_fd = os.open(name, _DIR_FLAGS, dir_fd=dst_dir_fd)
            try:
                with os.scandir(src_fd) as entries:
                    children = [entry.name for entry in entries if not (without_trash and entry.name == TRASH)]
                for child in children:
                    _copy_entry(src_fd, child, dst_fd, without_trash)
                os.fchmod(dst_fd, stat.S_IMODE(st.st_mode))
                os.utime(dst_fd, ns=times)
            finally:
                os.close(dst_fd)
        finally:
            os.close(src_fd)
        return

    if stat.S_ISREG(st.st_mode):
        src_fd = os.open(name, _READ_FLAGS, dir_fd=src_dir_fd)
        try:
            if not stat.S_ISREG(os.fstat(src_fd).st_mode):
                return  # replaced by something else since the lstat above
            dst_fd = os.open(name, _CREATE_FLAGS, 0o600, dir_fd=dst_dir_fd)
            try:
                with os.fdopen(src_fd, "rb", closefd=False) as src, os.fdopen(
                    dst_fd, "wb", closefd=False
                ) as dst:
                    shutil.copyfileobj(src, dst, _CHUNK_SIZE)
                os.fchmod(dst_fd, stat.S_IMODE(st.st_mode) & ~(stat.S_ISUID | stat.S_ISGID))
                os.utime(dst_fd, ns=times)
            finally:
                os.close(dst_fd)
        finally:
            os.close(src_fd)


def _recheck_entry(
    dir_fd: int, parts: list[str], action: str, expected: os.stat_result | None = None
) -> os.stat_result:
    """Check ``parts[-1]`` in ``dir_fd`` right before it is deleted or moved into the trash; its lstat().

    A directory is checked for subvolumes and mount points below it (refused
    as not ``action``). That takes a while, so the entry is then checked
    again: it must still be the same directory, and not a subvolume or mount
    point itself. ``expected`` is an earlier lstat() of the entry (the source
    of a move, before it was copied): an entry that has replaced it since is
    refused too.
    """
    name = parts[-1]
    st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    if expected is None:
        expected = st
    if stat.S_ISDIR(st.st_mode):
        _refuse_nested_filesystems(dir_fd, parts, action)
        st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        _refuse_separate_filesystem(dir_fd, st, parts, action)
    if not os.path.samestat(st, expected):
        raise HTTPException(409, f"'{_shown(name)}' was replaced in the meantime, so it was not {action}.")
    return st


def _remove_entry(dir_fd: int, parts: list[str], action: str, expected: os.stat_result | None = None) -> None:
    """Delete ``parts[-1]`` from ``dir_fd`` (recursively for directories) without following symlinks.

    After the checks of _recheck_entry.
    """
    name = parts[-1]
    st = _recheck_entry(dir_fd, parts, action, expected)
    if stat.S_ISDIR(st.st_mode):
        _remove_tree(dir_fd, parts, action, st)
    else:
        os.unlink(name, dir_fd=dir_fd)


def _mount_id(fd: int) -> int | None:
    """The ID of the mount that the open descriptor ``fd`` is on: the mnt_id of FDINFO/<fd>.

    A bind mount from the same filesystem has the st_dev of the folder it is
    in; its mount ID differs. None where the kernel has no FDINFO (not
    Linux: the tests on macOS). OSError when it cannot be read otherwise.
    """
    try:
        with open(f"{FDINFO}/{fd}", "rb") as info:
            lines = info.read().split(b"\n")
    except FileNotFoundError:
        if not os.path.isdir(FDINFO):
            return None
        raise
    for line in lines:
        if line.startswith(b"mnt_id:") and line[len(b"mnt_id:"):].strip().isdigit():
            return int(line[len(b"mnt_id:"):].strip())
    raise OSError(errno.EINVAL, f"{FDINFO}/{fd} has no mount ID")


def _remove_tree(dir_fd: int, parts: list[str], action: str, expected: os.stat_result) -> None:
    """Delete the directory ``parts[-1]`` in ``dir_fd``, with everything in it, without following symlinks.

    ``expected`` is its lstat() from the checks right before (_recheck_entry).
    A walk on descriptors, one per level of depth: each directory is opened
    without following a symlink and must be on the device and the mount of
    the folder before anything in it is deleted. A subvolume or a mount, a
    bind mount from the same filesystem too, that appears below the folder
    after the checks stops the delete with 409 (storage.edge-cases, step
    8): shutil.rmtree went into it and deleted its files. What was deleted
    before stays deleted, and the answer says so.
    """
    name = parts[-1]
    # Per level: its descriptor, its name in the level above, its path below the folder, the subdirectories left.
    stack: list[tuple[int, str, str, list[str]]] = []

    def enter(fd: int, entry: str, path: str) -> None:
        """Check the directory ``fd``, put it on the stack and delete what in it is not a directory."""
        try:
            if os.fstat(fd).st_dev != expected.st_dev or _mount_id(fd) != mount:
                raise HTTPException(
                    409,
                    f"'{_shown(name)}' contains '{_shown(path)}', a separate subvolume or filesystem (such "
                    f"as a snapshot or a mount point), and cannot be {action} in the file manager. It "
                    f"appeared during the delete, which stopped there: part of '{_shown(name)}' was "
                    "deleted before that.",
                )
            with os.scandir(fd) as entries:
                listed = [(item.name, item.is_dir(follow_symlinks=False)) for item in entries]
        except BaseException:
            os.close(fd)
            raise
        stack.append((fd, entry, path, sorted((child for child, is_dir in listed if is_dir), reverse=True)))
        for child, is_dir in listed:
            if not is_dir:
                _unlink_if_there(child, fd)

    top_fd = os.open(name, _DIR_FLAGS, dir_fd=dir_fd)
    try:
        if not os.path.samestat(os.fstat(top_fd), expected):
            raise HTTPException(409, f"'{_shown(name)}' was replaced in the meantime, so it was not {action}.")
        mount = _mount_id(top_fd)
    except BaseException:
        os.close(top_fd)
        raise
    try:
        enter(top_fd, name, "")
        while stack:
            fd, entry, path, names = stack[-1]
            if not names:
                stack.pop()
                os.close(fd)
                if stack:
                    os.rmdir(entry, dir_fd=stack[-1][0])
                continue
            child = names.pop()
            try:
                child_fd = os.open(child, _DIR_FLAGS, dir_fd=fd)
            except OSError as exc:
                if exc.errno in (errno.ENOTDIR, errno.ELOOP):  # a file or a symlink took its place since
                    _unlink_if_there(child, fd)
                elif exc.errno != errno.ENOENT:
                    raise
                continue
            enter(child_fd, child, f"{path}/{child}" if path else child)
    finally:
        for fd, _, _, _ in stack:
            os.close(fd)
    os.rmdir(name, dir_fd=dir_fd)


def _unlink_if_there(name: str, dir_fd: int) -> None:
    """Delete the entry ``name`` (not a directory) in ``dir_fd``; one that is gone already is no error."""
    try:
        os.unlink(name, dir_fd=dir_fd)
    except FileNotFoundError:
        pass


def _plan_transfer(sources: list[str], destination: str, *, moving: bool) -> tuple[list[str], list[list[str]]]:
    """Validate a copy/move request before anything is changed."""
    dest_parts = _split(destination)
    _require_inside_pool(dest_parts)
    plan: list[list[str]] = []
    names: set[str] = set()
    for source in sources:
        parts = _split(source)
        if moving:
            _refuse_protected(parts)
        elif not parts:
            raise HTTPException(403, "The pools directory cannot be copied")
        if dest_parts[: len(parts)] == parts:
            raise HTTPException(400, "Cannot copy or move a folder into itself")
        if parts[-1] in names:
            raise HTTPException(400, f"More than one item is named {parts[-1]}")
        names.add(parts[-1])
        plan.append(parts)
    return dest_parts, plan


def _transfer(sources: list[str], destination: str, *, moving: bool) -> list[str]:
    """Copy or move the items ``sources`` into the folder ``destination``.

    Every item is checked before any is changed. A move that fails at an
    item after others moved is refused with how many moved before it
    (_done_before).
    """
    dest_parts, plan = _plan_transfer(sources, destination, moving=moving)
    dest_fd = _open_dir(dest_parts)
    try:
        for parts in plan:
            if _lstat_at(dest_fd, parts[-1]) is not None:
                raise HTTPException(400, f"Already exists: {parts[-1]}")
        if moving:  # copies of volumes, and of folders that contain one, are allowed
            _refuse_separate_filesystems(plan, "moved", moving_to=os.fstat(dest_fd).st_dev)

        done = []
        for parts in plan:
            try:
                went = _transfer_one(parts, dest_fd, moving)
            except (HTTPException, OSError) as exc:
                if not moving or not done:
                    raise
                raise _done_before(exc, len(done), len(plan), "moved") from exc
            if went:
                done.append(_display_path([*dest_parts, parts[-1]]))
        return done
    finally:
        os.close(dest_fd)


def _transfer_one(parts: list[str], dest_fd: int, moving: bool) -> bool:
    """Copy or move the item ``parts`` into the folder ``dest_fd``; False when it is not there (any more)."""
    src_dir_fd = _open_parent_or_none(parts)
    if src_dir_fd is None:
        return False  # missing sources are skipped, as before
    try:
        name = parts[-1]
        st = _lstat_at(src_dir_fd, name)
        if st is None:
            return False
        if moving:
            _refuse_separate_filesystem(src_dir_fd, st, parts, "moved")
            try:
                os.rename(name, name, src_dir_fd=src_dir_fd, dst_dir_fd=dest_fd)
            except OSError as exc:
                _refuse_busy(exc, name, "moved")
                if exc.errno != errno.EXDEV:
                    raise
                # Different filesystem (e.g. another pool): copy, then delete.
                if stat.S_ISDIR(st.st_mode):
                    _refuse_nested_filesystems(src_dir_fd, parts, _MOVED_ELSEWHERE)
                _copy_entry(src_dir_fd, name, dest_fd)
                _remove_entry(src_dir_fd, parts, _MOVED_ELSEWHERE, expected=st)
        else:
            _copy_entry(src_dir_fd, name, dest_fd, True)
        return True
    finally:
        os.close(src_dir_fd)


def _delete(paths: list[str], permanent: bool = False) -> list[str]:
    """Move the items ``paths`` into the trash of their pool or volume; with ``permanent``, delete them for good.

    Every item is checked before any is changed: also, unless ``permanent``,
    that one rename can take it into its trash, which must be a folder that
    is private or can be made so (_check_trash_of). An item that fails
    after others went is refused with how many went before it (_done_before).
    """
    targets = []
    for path in paths:
        parts = _split(path)
        _refuse_protected(parts)
        targets.append((path, parts))
    plan = [parts for _, parts in targets]
    _refuse_separate_filesystems(plan, "deleted")
    if not permanent:
        mount_points = _readable_mount_points()
        for parts in plan:
            _check_trash_of(parts, mount_points)

    deleted = []
    for path, parts in targets:
        try:
            done = _delete_one(parts, permanent)
        except (HTTPException, OSError) as exc:
            if not deleted:
                raise
            how = "deleted for good" if permanent else "moved to the trash"
            raise _done_before(exc, len(deleted), len(paths), how) from exc
        if done:
            deleted.append(path)
    return deleted


def _delete_one(parts: list[str], permanent: bool) -> bool:
    """Move the item ``parts`` into its trash, or delete it for good; False when it is not there (any more)."""
    dir_fd = _open_parent_or_none(parts)
    if dir_fd is None:
        return False
    try:
        st = _lstat_at(dir_fd, parts[-1])
        if st is None:
            return False
        _refuse_separate_filesystem(dir_fd, st, parts, "deleted")
        if permanent:
            _remove_entry(dir_fd, parts, "deleted")
        else:
            _move_to_trash(dir_fd, parts)
        return True
    finally:
        os.close(dir_fd)


def _done_before(exc: HTTPException | OSError, done: int, total: int, how: str) -> HTTPException:
    """The refusal ``exc`` of a request that failed after ``done`` of its ``total`` items had been ``how``.

    Its message says so: the client cannot tell otherwise which items went.
    """
    error = exc if isinstance(exc, HTTPException) else _http_error(exc)
    items = f"{done} of the {total} items {'was' if done == 1 else 'were'}"
    detail = f"{error.detail}{'' if error.detail.endswith('.') else '.'} {items} {how} before that."
    return HTTPException(error.status_code, detail)


# --- the trash (ADR-0259) ------------------------------------------------------
#
# Delete moves an item into the trash of the pool or volume that holds it,
# <pool>/.unos-trash or <pool>/<volume>/.unos-trash, with one rename on its
# filesystem: btrfs renames nothing into another subvolume, so a volume has a
# trash of its own. The trash and each entry in it are private folders (the
# supervisor's, mode 0700), which Repair permissions never enters and which
# no app or NFS client that is not root can change: a trash that someone
# else made first is made private before it is used, and only private
# folders in it count as its entries (_open_trash, _unos_entry). What a
# trash holds is still untrusted, as the rest of a volume is: apps that run
# as root can change it. So origin is checked before a restore and leads
# only below the trash's own pool or volume, the item is renamed back
# without replacing anything, and an entry is deleted for good only after
# the checks of ADR-0348.


def _now() -> datetime:
    """The current time in UTC. Module level so tests can move it."""
    return datetime.now(timezone.utc)


def _entry_time(name: str) -> datetime | None:
    """When the trash entry ``name`` was deleted; None for a name that is not a trash entry's."""
    if not _TRASH_ENTRY.fullmatch(name):
        return None
    try:
        return datetime.strptime(name[:16], _TRASH_STAMP).replace(tzinfo=timezone.utc)
    except ValueError:  # e.g. month 13
        return None


def _no_trash_detail(name: str) -> str:
    return (
        f"'{_shown(name)}' is in a separate subvolume or filesystem (such as a snapshot or a mount point) "
        "that has no trash, so it was not deleted. Delete it for good instead."
    )


def _not_a_folder_detail(scope: list[str]) -> str:
    return (
        f"{_shown(_display_path([*scope, TRASH]))} is not a folder, so nothing can be moved to the trash "
        f"of {_shown(_display_path(scope))}."
    )


def _not_private_detail(scope: list[str]) -> str:
    return (
        f"{_shown(_display_path([*scope, TRASH]))} could not be made private (only UNOS may open it), so nothing "
        f"can be moved to the trash of {_shown(_display_path(scope))}."
    )


def _unreadable(path: list[str], exc: OSError) -> str:
    return f"{_shown(_display_path(path))} could not be read: {os.strerror(exc.errno) if exc.errno else exc}"


def _private(st: os.stat_result) -> bool:
    """Whether only the supervisor can use the entry ``st``: its own (root's on the device), no group or other bits.

    Repair permissions does not enter a private folder (ADR-0155), and an
    app or an NFS client that is not root cannot change what is in it.
    """
    return st.st_uid == os.geteuid() and not st.st_mode & 0o077


def _unos_entry(st: os.stat_result | None) -> bool:
    """Whether the lstat() ``st`` of a name in a trash is one of UNOS's entries there: a private folder.

    Such a name that is not one was not made by UNOS (_open_trash makes a
    trash private before it uses it): it is never listed, restored or deleted.
    """
    return st is not None and stat.S_ISDIR(st.st_mode) and _private(st)


def _made_private(fd: int, scope: list[str]) -> bool:
    """Make the trash folder ``fd`` of ``scope`` private (_private) if it is not; whether it is now.

    E.g. an app or an NFS client that can write the top folder of a volume
    made it first, with its owner and mode: it could then rename the
    entries, to an older time for the expiry or out of the list.
    """
    st = os.fstat(fd)
    if _private(st):
        return True
    try:
        if st.st_uid != os.geteuid():
            os.fchown(fd, os.geteuid(), os.getegid())
        os.fchmod(fd, 0o700)
    except OSError:  # e.g. vfat, or NFS with root_squash
        return False
    if not _private(os.fstat(fd)):  # e.g. a filesystem that keeps its own modes
        return False
    logger.warning(
        "Made %s private (only UNOS may open it): it was uid %d's, with mode %o",
        _shown(_display_path([*scope, TRASH])), st.st_uid, stat.S_IMODE(st.st_mode),
    )
    return True


def _open_trash_scope(dir_fd: int, parts: list[str]) -> tuple[int, int]:
    """Open the pool or volume whose trash takes the entry ``parts``, which is in the folder ``dir_fd``.

    Returns its descriptor and the number of its components: 1 for the pool,
    2 for a volume, a folder directly in the pool's top folder on another
    subvolume or filesystem (as _refuse_separate_filesystem tells them). The
    entry must be on the filesystem of that folder: one in a deeper subvolume
    (a snapshot, a restored copy in a volume) or below the mount point of
    another filesystem has no trash (409). Below a bind mount of the same
    filesystem the st_dev is the same: _check_trash_of looks for those in
    the mount table.
    """
    scope_fd = _open_dir(parts[:1])
    scope_len = 1
    try:
        if len(parts) > 2:
            volume_fd = os.open(parts[1], _DIR_FLAGS, dir_fd=scope_fd)
            try:
                in_volume = os.fstat(volume_fd).st_dev != os.fstat(scope_fd).st_dev
            except BaseException:
                os.close(volume_fd)
                raise
            if in_volume:
                scope_fd, volume_fd = volume_fd, scope_fd
                scope_len = 2
            os.close(volume_fd)
        if os.fstat(dir_fd).st_dev != os.fstat(scope_fd).st_dev:
            raise HTTPException(409, _no_trash_detail(parts[-1]))
        return scope_fd, scope_len
    except BaseException:
        os.close(scope_fd)
        raise


def _open_trash(scope_fd: int, scope: list[str], *, create: bool) -> int | None:
    """Open the trash folder of the pool or volume ``scope`` (open as ``scope_fd``); None when it has none.

    With ``create`` a missing one is made (mode 0700). One that is not a
    folder (a symlink, a file) is refused (409): nothing is followed. One
    that is not private is made so first, whoever made it (_made_private);
    one that cannot be made so is refused (409), and one made just now is
    removed again.
    """
    made = False
    if create:
        try:
            os.mkdir(TRASH, 0o700, dir_fd=scope_fd)
            made = True
        except FileExistsError:
            pass
    try:
        fd = os.open(TRASH, _DIR_FLAGS, dir_fd=scope_fd)
    except FileNotFoundError:
        if create:
            raise
        return None
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise HTTPException(409, _not_a_folder_detail(scope)) from None
        raise
    try:
        if not _made_private(fd, scope):
            raise HTTPException(409, _not_private_detail(scope))
    except BaseException:
        os.close(fd)
        if made:  # on a filesystem that cannot keep it private: no trash stays
            try:
                os.rmdir(TRASH, dir_fd=scope_fd)
            except OSError:
                pass
        raise
    return fd


def _write_origin(entry_fd: int, below_scope: list[str]) -> None:
    """Write the path of a deleted item below its pool or volume into its trash entry, and flush it.

    Flushed before the item is renamed into the entry: after a power cut the
    rename may be on the disk while the content of a new file is not yet.
    """
    data = memoryview(os.fsencode("/".join(below_scope)))
    fd = os.open(_TRASH_ORIGIN, _CREATE_FLAGS, 0o600, dir_fd=entry_fd)
    try:
        while data:
            data = data[os.write(fd, data):]
        os.fsync(fd)
    finally:
        os.close(fd)


def _read_origin(entry_fd: int) -> list[str] | None:
    """The path below its pool or volume that the item of a trash entry was deleted from; None if not known.

    The file can have been changed (see above): a path that is empty, has an
    empty, '.', '..', NUL or over-long component, or leads into a trash is
    not known either.
    """
    try:
        fd = os.open(_TRASH_ORIGIN, _READ_FLAGS, dir_fd=entry_fd)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        data = b""
        while len(data) <= _ORIGIN_MAX_BYTES and (chunk := os.read(fd, _ORIGIN_MAX_BYTES + 1 - len(data))):
            data += chunk
    except OSError:
        return None
    finally:
        os.close(fd)
    if len(data) > _ORIGIN_MAX_BYTES:
        return None
    parts = os.fsdecode(data).split("/")
    for part in parts:
        if part in ("", ".", "..", TRASH) or "\x00" in part or len(os.fsencode(part)) > _NAME_MAX_BYTES:
            return None
    return parts


def _discard_trash_entry(trash_fd: int, entry: str) -> None:
    """Remove the folder of a trash entry whose item is not in it (any more): its origin, then the folder.

    Leaves what it cannot remove; the expiry or Empty trash deletes it later.
    """
    try:
        entry_fd = os.open(entry, _DIR_FLAGS, dir_fd=trash_fd)
    except OSError:
        return
    try:
        os.unlink(_TRASH_ORIGIN, dir_fd=entry_fd)
    except OSError:
        pass
    finally:
        os.close(entry_fd)
    try:
        os.rmdir(entry, dir_fd=trash_fd)
    except OSError:
        pass


def _move_to_trash(dir_fd: int, parts: list[str]) -> None:
    """Move the entry ``parts`` (in the folder ``dir_fd``) into the trash of its pool or volume.

    The entry is checked right before, as before it is deleted for good
    (_recheck_entry). It gets a new folder in the trash, which is made
    private first (_open_trash), with its origin; a move that fails leaves
    no folder behind.
    """
    _recheck_entry(dir_fd, parts, "deleted")
    scope_fd, scope_len = _open_trash_scope(dir_fd, parts)
    try:
        trash_fd = _open_trash(scope_fd, parts[:scope_len], create=True)
        try:
            entry = f"{_now().strftime(_TRASH_STAMP)}-{secrets.token_hex(8)}"
            os.mkdir(entry, 0o700, dir_fd=trash_fd)
            moved = False
            try:
                entry_fd = os.open(entry, _DIR_FLAGS, dir_fd=trash_fd)
                try:
                    _write_origin(entry_fd, parts[scope_len:])
                    _rename_exclusive(dir_fd, parts[-1], entry_fd, _TRASH_ITEM)
                    moved = True
                finally:
                    os.close(entry_fd)
            except OSError as exc:
                if exc.errno == errno.EXDEV:  # e.g. below a bind mount of the same filesystem
                    raise HTTPException(409, _no_trash_detail(parts[-1])) from None
                raise
            finally:
                if not moved:
                    _discard_trash_entry(trash_fd, entry)
        finally:
            os.close(trash_fd)
    finally:
        os.close(scope_fd)


def _readable_mount_points() -> list[bytes] | None:
    """The mount points of MOUNTINFO (_read_mount_points); None when the table cannot be read.

    For checks that only refuse earlier what a rename refuses anyway
    (_mounted_between): without the table, the rename tells.
    """
    try:
        return _read_mount_points()
    except OSError:
        return None


def _mounted_between(scope: list[str], folder: list[str], mount_points: list[bytes] | None) -> bool:
    """Whether a mount point lies below the pool or volume ``scope``, at or above its folder ``folder``.

    rename(2) takes nothing across a mount point (EXDEV), also not one of a
    bind mount of the same filesystem, which has the st_dev of the rest:
    only the mount table shows it.
    """
    if mount_points is None:
        return False
    top = os.fsencode(_display_path(scope)) + b"/"
    path = os.fsencode(_display_path(folder))
    return any(point.startswith(top) and (path == point or path.startswith(point + b"/")) for point in mount_points)


def _check_trash_of(parts: list[str], mount_points: list[bytes] | None) -> None:
    """Before a delete changes anything: whether one rename can take the entry ``parts`` into its trash.

    Refused (409): an entry in a deeper subvolume or below a mount point,
    which has no trash, and a trash that is not a folder or cannot be made
    private. A trash that is missing is made when the first item goes there.
    """
    dir_fd = _open_parent_or_none(parts)
    if dir_fd is None:
        return
    try:
        scope_fd, scope_len = _open_trash_scope(dir_fd, parts)
    finally:
        os.close(dir_fd)
    try:
        if _mounted_between(parts[:scope_len], parts[:-1], mount_points):
            raise HTTPException(409, _no_trash_detail(parts[-1]))
        trash_fd = _open_trash(scope_fd, parts[:scope_len], create=False)
        if trash_fd is not None:
            os.close(trash_fd)
    finally:
        os.close(scope_fd)


def _trash_scopes(problems: list[str], due: Callable[[str], bool] | None = None) -> list[list[str]]:
    """Every pool, each followed by its volumes: the places that can have a trash, by name.

    A pool whose top folder cannot be read is left out, with the reason in ``problems``. With
    ``due``, only the pools for which it answers True, asked before a pool is opened.
    """
    base_fd = _open_dir([])
    try:
        pools = sorted(_subdirectories(base_fd))
    finally:
        os.close(base_fd)
    scopes = []
    for pool in pools:
        if pool == TRASH or (due is not None and not due(pool)):
            continue
        try:
            pool_fd = _open_dir([pool])
        except FileNotFoundError:
            continue
        except OSError as exc:
            problems.append(_unreadable([pool], exc))
            continue
        try:
            scopes.append([pool])
            device = os.fstat(pool_fd).st_dev
            for name in sorted(_subdirectories(pool_fd)):
                st = _lstat_at(pool_fd, name)
                if name != TRASH and st is not None and stat.S_ISDIR(st.st_mode) and st.st_dev != device:
                    scopes.append([pool, name])
        except OSError as exc:
            problems.append(_unreadable([pool], exc))
        finally:
            os.close(pool_fd)
    return scopes


def _open_scope(scope: list[str]) -> int | None:
    """Open the pool or the volume ``scope``; None when it is a folder that is neither, which has no trash."""
    pool_fd = _open_dir(scope[:1])
    if len(scope) == 1:
        return pool_fd
    try:
        volume_fd = os.open(scope[1], _DIR_FLAGS, dir_fd=pool_fd)
        try:
            in_volume = os.fstat(volume_fd).st_dev != os.fstat(pool_fd).st_dev
        except BaseException:
            os.close(volume_fd)
            raise
        if not in_volume:
            os.close(volume_fd)
            return None
        return volume_fd
    finally:
        os.close(pool_fd)


def _each_trash(problems: list[str], due: Callable[[str], bool] | None = None) -> Iterator[tuple[list[str], int]]:
    """(the pool or volume, its open trash folder) for each trash; what cannot be read goes to ``problems``.

    With ``due``, of the pools for which it answers True (_trash_scopes). Close the iterator when
    done with it (contextlib.closing): it holds descriptors.
    """
    for scope in _trash_scopes(problems, due):
        try:
            scope_fd = _open_scope(scope)
        except FileNotFoundError:
            continue  # gone since _trash_scopes
        except OSError as exc:
            problems.append(_unreadable(scope, exc))
            continue
        if scope_fd is None:
            continue  # no longer a volume
        try:
            try:
                trash_fd = _open_trash(scope_fd, scope, create=False)
            except HTTPException as exc:
                problems.append(exc.detail)
                continue
            except OSError as exc:
                problems.append(_unreadable([*scope, TRASH], exc))
                continue
            if trash_fd is None:
                continue
            try:
                yield scope, trash_fd
            finally:
                os.close(trash_fd)
        finally:
            os.close(scope_fd)


def _trash_entries(trash_fd: int) -> list[str]:
    """The names of UNOS's entries in a trash folder, oldest first (_unos_entry); what else is there stays."""
    with os.scandir(trash_fd) as entries:
        names = [entry.name for entry in entries if _entry_time(entry.name) is not None]
    return sorted(name for name in names if _unos_entry(_lstat_at(trash_fd, name)))


def _trash_item(scope: list[str], trash_fd: int, entry: str) -> TrashItem | None:
    """How GET /files/trash lists the entry ``entry``; None when its item is not in it (any more)."""
    try:
        entry_fd = os.open(entry, _DIR_FLAGS, dir_fd=trash_fd)
    except OSError as exc:
        if exc.errno in _GONE:
            return None  # removed meanwhile, or not a folder: not UNOS's
        raise
    try:
        st = _lstat_at(entry_fd, _TRASH_ITEM)
        origin = _read_origin(entry_fd)
    finally:
        os.close(entry_fd)
    if st is None:
        return None
    deleted = _entry_time(entry)
    file_type = _file_type(st)
    # Shown: an origin that someone changed may hold bytes that are not UTF-8, which JSON cannot carry.
    return TrashItem(
        path=_display_path([*scope, TRASH, entry]),
        name=_shown(origin[-1]) if origin else entry,
        type=file_type,
        size=st.st_size if file_type == FileType.FILE else 0,
        original_path=_shown(_display_path([*scope, *origin])) if origin else None,
        deleted=deleted.isoformat(),
        expires=(deleted + timedelta(days=TRASH_RETENTION_DAYS)).isoformat(),
    )


def _list_trash() -> TrashListing:
    """What GET /files/trash answers: the items of every trash, newest first, and what could not be read."""
    problems: list[str] = []
    items = []
    with closing(_each_trash(problems)) as trashes:
        for scope, trash_fd in trashes:
            try:
                names = _trash_entries(trash_fd)
            except OSError as exc:
                problems.append(_unreadable([*scope, TRASH], exc))
                continue
            for entry in names:
                try:
                    item = _trash_item(scope, trash_fd, entry)
                except OSError as exc:
                    problems.append(_unreadable([*scope, TRASH, entry], exc))
                    continue
                if item is not None:
                    items.append(item)
    items.sort(key=lambda item: item.path)
    items.sort(key=lambda item: item.deleted, reverse=True)
    return TrashListing(items=items, retention_days=TRASH_RETENTION_DAYS, problems=problems)


def _trash_path(path: str) -> tuple[list[str], str]:
    """The pool or volume and the entry of a path that GET /files/trash listed; 400 for any other path."""
    parts = _components(path)
    if (
        len(parts) not in (3, 4)
        or parts[-2] != TRASH
        or TRASH in parts[:-2]
        or _entry_time(parts[-1]) is None
    ):
        raise HTTPException(400, f"Not an item in the trash: {_shown(path)}")
    return parts[:-2], parts[-1]


@contextmanager
def _opened_entry(path: str, scope: list[str], entry: str) -> Iterator[tuple[int, int, int]]:
    """The pool or volume, its trash and the entry of the path ``path`` of the trash, open.

    400 when ``scope`` is neither a pool nor a volume, 404 when the entry is
    gone or is not one of UNOS's (_unos_entry).
    """
    gone = HTTPException(404, f"Not in the trash: {_shown(path)}")
    scope_fd = _open_scope(scope)
    if scope_fd is None:
        raise HTTPException(400, f"Not an item in the trash: {_shown(path)}")
    try:
        trash_fd = _open_trash(scope_fd, scope, create=False)
        if trash_fd is None:
            raise gone
        try:
            try:
                entry_fd = os.open(entry, _DIR_FLAGS, dir_fd=trash_fd)
            except OSError as exc:
                if exc.errno in _GONE:  # also a file or a symlink: not UNOS's
                    raise gone from None
                raise
            try:
                if not _unos_entry(os.fstat(entry_fd)):
                    raise gone
                yield scope_fd, trash_fd, entry_fd
            finally:
                os.close(entry_fd)
        finally:
            os.close(trash_fd)
    finally:
        os.close(scope_fd)


def _restore_target(scope: list[str], entry: str, scope_fd: int, entry_fd: int) -> tuple[list[str], int]:
    """Where the item of a trash entry goes back to: its origin, and the folder it was in, open.

    409 when that is not known or the folder is gone; a symlink on the way
    is never followed (403).
    """
    origin = _read_origin(entry_fd)
    if origin is None:
        raise HTTPException(
            409,
            f"It is not known where {_shown(_display_path([*scope, TRASH, entry]))} was deleted from, "
            "so it cannot be restored. Delete it for good instead.",
        )
    if _lstat_at(entry_fd, _TRASH_ITEM) is None:
        raise HTTPException(404, f"Not in the trash: {_shown(_display_path([*scope, TRASH, entry]))}")
    try:
        return origin, open_subdirs_nofollow(scope_fd, origin[:-1])
    except (FileNotFoundError, NotADirectoryError):
        raise HTTPException(
            409,
            f"Could not restore '{_shown(origin[-1])}': the folder {_shown(_display_path([*scope, *origin[:-1]]))} "
            "it was deleted from is gone. Restore or create that folder first.",
        ) from None


def _elsewhere_now_detail(scope: list[str], origin: list[str]) -> str:
    return (
        f"Could not restore '{_shown(origin[-1])}': the folder {_shown(_display_path([*scope, *origin[:-1]]))} "
        "is on another subvolume or filesystem now."
    )


def _restore_from_trash(paths: list[str]) -> list[str]:
    """Rename the items of the trash entries ``paths`` back to where they were deleted from.

    Every item is checked before any is restored: its origin, its folder,
    that one rename can take it there (the folder is on the filesystem of
    the pool or volume and below no mount point in it, 409), and that its
    name is free there (400 "Already exists: <name>"). The exclusive rename
    also refuses a name that appears after that check; such a refusal after
    other items went back says how many (_done_before).
    """
    entries = [(path, *_trash_path(path)) for path in paths]
    mount_points = _readable_mount_points() if entries else None
    targets = set()
    for path, scope, entry in entries:
        with _opened_entry(path, scope, entry) as (scope_fd, _, entry_fd):
            origin, parent_fd = _restore_target(scope, entry, scope_fd, entry_fd)
            try:
                # As _open_trash_scope checks for a delete: on the filesystem of the pool or volume.
                if os.fstat(parent_fd).st_dev != os.fstat(scope_fd).st_dev or _mounted_between(
                    scope, [*scope, *origin[:-1]], mount_points
                ):
                    raise HTTPException(409, _elsewhere_now_detail(scope, origin))
                if _lstat_at(parent_fd, origin[-1]) is not None:
                    raise HTTPException(400, f"Already exists: {_shown(origin[-1])}")
            finally:
                os.close(parent_fd)
        target = _display_path([*scope, *origin])
        if target in targets:
            raise HTTPException(400, f"More than one item would go back to {_shown(target)}")
        targets.add(target)

    restored = []
    for path, scope, entry in entries:
        try:
            restored.append(_restore_one(path, scope, entry))
        except (HTTPException, OSError) as exc:  # e.g. a name or a mount that appeared since the check
            if not restored:
                raise
            raise _done_before(exc, len(restored), len(paths), "restored") from exc
    return restored


def _restore_one(path: str, scope: list[str], entry: str) -> str:
    """Rename the item of the trash entry ``path`` back to where it was deleted from; that path, as shown."""
    with _opened_entry(path, scope, entry) as (scope_fd, trash_fd, entry_fd):
        origin, parent_fd = _restore_target(scope, entry, scope_fd, entry_fd)
        try:
            _rename_exclusive(entry_fd, _TRASH_ITEM, parent_fd, origin[-1])
        except FileExistsError:
            raise HTTPException(400, f"Already exists: {_shown(origin[-1])}") from None
        except OSError as exc:
            if exc.errno == errno.EXDEV:  # a mount since the check
                raise HTTPException(409, _elsewhere_now_detail(scope, origin)) from None
            raise
        finally:
            os.close(parent_fd)
        _discard_trash_entry(trash_fd, entry)
    return _shown(_display_path([*scope, *origin]))


def _delete_trash_entry(scope: list[str], trash_fd: int, entry: str) -> bool:
    """Delete the trash entry ``entry`` for good, after the checks of ADR-0348.

    False when it is gone already, or is not one of UNOS's (_unos_entry), which stays.
    """
    if not _unos_entry(_lstat_at(trash_fd, entry)):
        return False
    _remove_entry(trash_fd, [*scope, TRASH, entry], _FOR_GOOD)
    return True


def _delete_from_trash(paths: list[str]) -> list[str]:
    """Delete the trash entries ``paths`` for good; the ones that are gone already are left out of the answer."""
    entries = [(path, *_trash_path(path)) for path in paths]
    deleted = []
    for path, scope, entry in entries:
        try:
            scope_fd = _open_scope(scope)
        except FileNotFoundError:
            continue
        if scope_fd is None:
            raise HTTPException(400, f"Not an item in the trash: {_shown(path)}")
        try:
            trash_fd = _open_trash(scope_fd, scope, create=False)
            if trash_fd is None:
                continue
            try:
                if _delete_trash_entry(scope, trash_fd, entry):
                    deleted.append(path)
            finally:
                os.close(trash_fd)
        finally:
            os.close(scope_fd)
    return deleted


def _empty_trash() -> int:
    """Delete everything in the trash of every pool and volume for good; how many items.

    Stops at the first entry that cannot be deleted. A trash that cannot be
    read answers 500 once the others are empty.
    """
    problems: list[str] = []
    count = 0
    with closing(_each_trash(problems)) as trashes:
        for scope, trash_fd in trashes:
            for entry in _trash_entries(trash_fd):
                if _delete_trash_entry(scope, trash_fd, entry):
                    count += 1
    if problems:
        raise HTTPException(500, " ".join(problems))
    return count


def _expire_trash(now: datetime, due: Callable[[str], bool] | None = None) -> tuple[int, list[str]]:
    """Delete for good what was deleted TRASH_RETENTION_DAYS or more before ``now``.

    With ``due``, in the pools for which it answers True. Returns how many
    entries it deleted and the problems: the trashes it could not read and the
    entries it could not delete, which stay.
    """
    cutoff = now - timedelta(days=TRASH_RETENTION_DAYS)
    problems: list[str] = []
    count = 0
    with closing(_each_trash(problems, due)) as trashes:
        for scope, trash_fd in trashes:
            try:
                names = _trash_entries(trash_fd)
            except OSError as exc:
                problems.append(_unreadable([*scope, TRASH], exc))
                continue
            for entry in names:
                if _entry_time(entry) > cutoff:
                    continue
                where = _shown(_display_path([*scope, TRASH, entry]))
                try:
                    if _delete_trash_entry(scope, trash_fd, entry):
                        count += 1
                except HTTPException as exc:
                    problems.append(f"{where}: {exc.detail}")
                except OSError as exc:
                    problems.append(f"{where}: {os.strerror(exc.errno) if exc.errno else exc}")
    return count, problems


def _expire_due_trash(now: datetime, done: dict[str, float], clock: float) -> tuple[int, list[str], bool]:
    """One check of the expiry (ADR-0378): the pass of each pool that is due, at the start and then
    TRASH_EXPIRY_INTERVAL after its last (``done``: pool -> ``clock`` of its last pass, which this
    updates), unless the pool's disks sleep (storage.spindown.pool_sleeps): reading its folders
    could spin them up, so it waits for a check when they are awake. Only the disks of a pool
    whose pass is due are asked whether they sleep, since asking an awake disk restarts its
    standby timer; a pool whose disks cannot be asked gets no pass. Returns how many entries it
    deleted, the problems, and whether a pass ran (or a pool's disks could not be asked).
    """
    passed: list[str] = []
    unasked: list[str] = []

    def due(pool: str) -> bool:
        if clock - done.get(pool, clock - TRASH_EXPIRY_INTERVAL) < TRASH_EXPIRY_INTERVAL:
            return False
        try:
            if spindown.pool_sleeps(pool):
                return False
        except Exception as exc:
            unasked.append(
                f"Could not tell whether the disks of pool {pool} sleep, so its trash was not cleared out: {exc}"
            )
            return False
        done[pool] = clock
        passed.append(pool)
        return True

    count, problems = _expire_trash(now, due)
    return count, unasked + problems, bool(passed or unasked)


_trash_expiry_task: asyncio.Task | None = None


async def trash_expiry_loop() -> None:
    """Delete for good what has been in the trash for TRASH_RETENTION_DAYS: each pool's at the start,
    then once a day, every TRASH_EXPIRY_CHECK seconds looking for the pools that are due and whose
    disks are awake (_expire_due_trash, ADR-0378).

    Each check reads the pools in a worker thread, without a timeout: a pool
    that hangs holds that one thread, and the next check waits for it. A pass
    logs what it deleted, and its problems when they differ from the last
    pass's; one that finds nothing to do writes nothing (ADR-0214).
    """
    reported: list[str] = []
    done: dict[str, float] = {}
    while True:
        try:
            count, problems, ran = await asyncio.to_thread(_expire_due_trash, _now(), done, time.monotonic())
        except Exception as exc:  # e.g. the pools directory cannot be read
            count, problems, ran = 0, [str(exc) or type(exc).__name__], True
        if count:
            logger.info(
                "Deleted %d item(s) for good that had been in the trash for %d days", count, TRASH_RETENTION_DAYS
            )
        if ran:
            if problems and problems != reported:
                logger.warning("The trash could not be cleared out completely: %s", "; ".join(problems))
            reported = problems
        await asyncio.sleep(TRASH_EXPIRY_CHECK)


async def start_trash_expiry() -> None:
    """Start the expiry of the trash (main.py's lifespan, ADR-0043)."""
    global _trash_expiry_task
    _trash_expiry_task = asyncio.create_task(trash_expiry_loop(), name="unos-trash-expiry")


async def stop_trash_expiry() -> None:
    """Stop the expiry of the trash at shutdown."""
    task = _trash_expiry_task
    if task is not None:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


# --- leftovers of uploads (ADR-0354) -------------------------------------------------
#
# Where the file of an upload cannot be unnamed (_FileUpload), it arrives under a hidden name,
# which a stop of the supervisor in the middle of the upload (a restart, an update, a crash, a
# power loss) leaves behind with what had arrived. The check looks for such files in every folder
# below the pools directory, UPLOAD_LEFTOVER_AGE seconds after the start and when an administrator
# asks; they are removed only when an administrator asks, never on their own.

# The hidden name of the file of an upload (_upload_temp_name).
_UPLOAD_TEMP_NAME = re.compile(r"\.unos-upload-[0-9a-f]{16}")
# Seconds that a hidden upload file must have been unchanged to count as a leftover, and after the
# start of the supervisor at which the first check runs: by then every file that the stop before
# it left is that old. The files of this supervisor's uploads are known by their names
# (_uploads_in_progress), however long ago they were written: receive_upload writes only once it
# holds about 768 KiB, so the file of a slow upload can stay unchanged for long. This rule is for
# a writer that the set cannot know, a supervisor process that has not ended yet: a file that it
# wrote in the last 10 minutes is left alone.
UPLOAD_LEFTOVER_AGE = 10 * 60
# The folder of the snapshots of a pool or a volume (ADR-0011), which the check does not enter:
# a snapshot is a read-only copy, and walking each would read the tree once per snapshot.
_SNAPSHOTS = ".snapshots"
# How many folders below the pools directory the check goes down. It keeps a descriptor open for
# each folder on its way, so a deeper tree could use up the descriptors of the whole supervisor;
# the folders below are not entered and are named among the problems, as Repair permissions does.
_LEFTOVER_MAX_DEPTH = 64


class _FoundLeftover:
    """A leftover as the check found it: GET answers this without looking at the disk again."""

    __slots__ = ("parts", "size", "mtime")

    def __init__(self, parts: list[str], st: os.stat_result) -> None:
        self.parts = parts  # its components below the pools directory
        self.size = st.st_size
        self.mtime = st.st_mtime


class _LeftoverCheck:
    """What the last check of leftovers found, and the check that runs."""

    def __init__(self) -> None:
        self.found: list[_FoundLeftover] = []  # by path
        self.problems: list[str] = []  # the folders it could not read
        self.checked: datetime | None = None  # when it ended
        self.task: asyncio.Task | None = None


_leftovers = _LeftoverCheck()


def _not_a_leftover(name: str, st: os.stat_result, now: datetime) -> str | None:
    """Why the entry ``name`` with the lstat() ``st`` is not a leftover of an upload; None when it is one."""
    if not stat.S_ISREG(st.st_mode):
        return "is not a file"
    with _uploads_lock:
        writing = name in _uploads_in_progress
    if writing:
        return "is the file of an upload in progress"
    if now.timestamp() - st.st_mtime < UPLOAD_LEFTOVER_AGE:
        return f"changed less than {UPLOAD_LEFTOVER_AGE // 60} minutes ago, so an upload may still be writing it"
    return None


def _mount_roots() -> set[tuple[int, int]] | None:
    """The st_dev and st_ino of the root of every mount at or below the pools directory; None without the mount table.

    Only such a folder can be met twice in a walk: the root of a bind mount
    is the folder it shows again (it has that folder's st_dev and st_ino),
    and a filesystem mounted twice has one root.
    """
    try:
        points = _read_mount_points()
    except OSError:
        return None
    base = os.fsencode(_base_dir())
    roots = set()
    for point in points:
        if point != base and not point.startswith(base.rstrip(b"/") + b"/"):
            continue
        try:
            st = os.stat(point, follow_symlinks=False)
        except OSError:  # gone since the table was read
            continue
        if stat.S_ISDIR(st.st_mode):
            roots.add((st.st_dev, st.st_ino))
    return roots


def _docker_data_root() -> str | None:
    """Docker's data root as Docker names it, or None when Docker does not answer. A host may keep
    it on a pool (ADR-0372); _find_upload_leftovers leaves it out (ADR-0374)."""
    from ..containers import docker as docker_ops

    try:
        return docker_ops.get_client().info().get("DockerRootDir") or None
    except Exception:
        return None


def _find_upload_leftovers(
    now: datetime, skipped: frozenset[str] = frozenset()
) -> tuple[list[_FoundLeftover], list[str]]:
    """The leftovers of uploads below the pools directory, by path, and the folders that could not be read.

    It walks every folder from the pools directory down, also on the
    filesystems mounted there (where most leftovers are), one component at a
    time without following symbolic links, at most _LEFTOVER_MAX_DEPTH
    folders down. Each folder is walked once: a bind mount that shows a
    folder again is not walked twice. For that it remembers only the roots
    of mounts that it walked (_mount_roots), so its memory does not grow
    with the number of folders; every folder only when the mount table
    cannot be read. The trash and the snapshots are left out, and so are the folders of
    ``skipped`` (absolute paths): Docker's data root, whose image layers and mounted container
    roots hold no upload and cost minutes of a slow board's CPU (ADR-0374). It only reads.
    """
    found: list[_FoundLeftover] = []
    problems: list[str] = []
    roots = _mount_roots()
    seen: set[tuple[int, int]] = set()
    # The open folders from the pools directory down to the one walked, each with the subfolders left to walk.
    stack: list[tuple[list[str], int, Iterator[str]]] = []

    def enter(parts: list[str], fd: int) -> None:
        """Note the leftovers of the open folder ``parts`` and put it on the stack; ``fd`` goes with it."""
        subfolders = []
        try:
            st = os.fstat(fd)
            folder = (st.st_dev, st.st_ino)
            if roots is None or folder in roots:
                if folder in seen:
                    os.close(fd)
                    return
                seen.add(folder)
            with os.scandir(fd) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name not in (TRASH, _SNAPSHOTS) and (
                            not skipped or os.path.join(_base_dir(), *parts, entry.name) not in skipped
                        ):
                            subfolders.append(entry.name)
                    elif _UPLOAD_TEMP_NAME.fullmatch(entry.name):
                        try:
                            entry_st = entry.stat(follow_symlinks=False)
                        except FileNotFoundError:
                            continue
                        if _not_a_leftover(entry.name, entry_st, now) is None:
                            found.append(_FoundLeftover([*parts, entry.name], entry_st))
        except OSError as exc:
            os.close(fd)
            problems.append(_unreadable(parts, exc))
            return
        if subfolders and len(parts) >= _LEFTOVER_MAX_DEPTH:
            problems.append(
                f"The folders in {_shown(_display_path(parts))} are too deep to check: "
                f"more than {_LEFTOVER_MAX_DEPTH} folders below {_shown(_base_dir())}"
            )
            subfolders = []
        stack.append((parts, fd, iter(sorted(subfolders))))

    try:
        enter([], _open_dir([]))
        while stack:
            parts, fd, names = stack[-1]
            name = next(names, None)
            if name is None:
                stack.pop()
                os.close(fd)
                continue
            try:
                child = os.open(name, _DIR_FLAGS, dir_fd=fd)
            except OSError as exc:
                if exc.errno not in _GONE:
                    problems.append(_unreadable([*parts, name], exc))
                continue
            enter([*parts, name], child)
    finally:
        for _, fd, _ in stack:
            os.close(fd)
    found.sort(key=lambda leftover: _display_path(leftover.parts))
    return found, problems


def _find_upload_leftovers_outside_docker(now: datetime) -> tuple[list[_FoundLeftover], list[str]]:
    """_find_upload_leftovers without Docker's data root (ADR-0374)."""
    root = _docker_data_root()
    if root is None:
        return _find_upload_leftovers(now)
    return _find_upload_leftovers(now, frozenset({os.path.normpath(root)}))


async def _run_upload_leftover_check() -> None:
    try:
        found, problems = await asyncio.to_thread(_find_upload_leftovers_outside_docker, _now())
    except OSError as exc:  # the pools directory cannot be read
        found, problems = [], [_unreadable([], exc)]
    _leftovers.found, _leftovers.problems, _leftovers.checked = found, problems, _now()


async def check_upload_leftovers() -> None:
    """Look for leftovers of uploads below the pools directory, in a worker thread.

    A check that runs is waited for, not started again. It has no timeout:
    a mount that hangs holds its thread, and the checks after it wait.
    """
    task = _leftovers.task
    if task is None or task.done():
        task = asyncio.create_task(_run_upload_leftover_check(), name="unos-upload-leftover-check")
        _leftovers.task = task
    await asyncio.shield(task)


def _list_upload_leftovers(checking: bool) -> UploadLeftovers:
    """What GET /files/upload-leftovers answers: what the last check found, as it found it.

    It does not look at the disks: a mount that hangs would hold a worker
    thread for each visit to the File Browser. A removal looks at each file
    again, and drops from this list the ones that are gone or changed.
    """
    files = []
    problems = list(_leftovers.problems)
    for leftover in _leftovers.found:
        path = _display_path(leftover.parts)
        if _shown(path) != path:
            # JSON cannot carry it, and a path shown otherwise would not lead back to the file.
            problems.append(f"{_shown(path)} is not listed: its path is not UTF-8")
            continue
        modified = datetime.fromtimestamp(leftover.mtime, timezone.utc).isoformat()
        files.append(UploadLeftover(path=path, size=leftover.size, modified=modified))
    checked = _leftovers.checked
    return UploadLeftovers(
        files=files, checked=checked.isoformat() if checked else None, checking=checking, problems=problems
    )


def _same_file(a: os.stat_result, b: os.stat_result) -> bool:
    """Whether the lstat() results ``a`` and ``b`` are of one file that was not written in between."""
    return (a.st_dev, a.st_ino, a.st_mtime_ns) == (b.st_dev, b.st_ino, b.st_mtime_ns)


def _remove_upload_leftovers(paths: list[str]) -> list[str]:
    """Remove the leftovers ``paths`` for good; answers the ones it removed.

    Every path is checked before any file is removed: it names a leftover
    (_not_a_leftover), or nothing any more. Each file is looked at again
    right before its unlink, through the descriptor of the folder that was
    checked: one replaced or written since is left alone. A file that is
    gone (also when a folder on its path was replaced by a file or a
    symbolic link) or is not a leftover any more leaves the list of the
    last check, also when the request is refused for it.
    """
    now = _now()
    folders: dict[tuple[str, ...], int] = {}  # the descriptors of the folders checked
    dropped: list[list[str]] = []  # what leaves the list of the last check
    try:
        targets = []
        for path in paths:
            parts = _split(path)
            if not parts or not _UPLOAD_TEMP_NAME.fullmatch(parts[-1]):
                raise HTTPException(400, f"Not the hidden file of an upload: {_shown(path)}")
            dir_fd = folders.get(tuple(parts[:-1]))
            if dir_fd is None:
                try:
                    dir_fd = _open_dir(parts[:-1])
                except OSError as exc:
                    # Gone, or replaced by a file or a symbolic link since the check: it holds
                    # no leftover now. Refusing would block every removal, since the page sends
                    # every listed path.
                    if exc.errno not in _GONE:
                        raise
                    dropped.append(parts)
                    continue
                folders[tuple(parts[:-1])] = dir_fd
            st = _lstat_at(dir_fd, parts[-1])
            if st is None:
                dropped.append(parts)
                continue
            problem = _not_a_leftover(parts[-1], st, now)
            if problem is not None:
                dropped.append(parts)
                raise HTTPException(409, f"Nothing was removed: {_shown(path)} {problem}.")
            targets.append((path, parts, dir_fd, st))

        removed = []
        for path, parts, dir_fd, st in targets:
            try:
                current = _lstat_at(dir_fd, parts[-1])
                if current is not None and _same_file(current, st):
                    os.unlink(parts[-1], dir_fd=dir_fd)
                    removed.append(path)
            except FileNotFoundError:
                pass
            except OSError as exc:
                if not removed:
                    raise
                raise _done_before(exc, len(removed), len(paths), "removed") from exc
            dropped.append(parts)
        return removed
    finally:
        for fd in folders.values():
            os.close(fd)
        if dropped:
            _leftovers.found = [leftover for leftover in _leftovers.found if leftover.parts not in dropped]


_upload_leftover_task: asyncio.Task | None = None


async def first_upload_leftover_check() -> None:
    """Look for leftovers of uploads once, UPLOAD_LEFTOVER_AGE seconds after the start.

    Leftovers come only from a stop, so the check after each start finds
    them; later ones run when an administrator asks. It logs what it found
    and the folders it could not read, and nothing when it found nothing
    (ADR-0214).
    """
    await asyncio.sleep(UPLOAD_LEFTOVER_AGE)
    await check_upload_leftovers()
    if _leftovers.found:
        logger.warning(
            "Found %d hidden file(s) that uploads left when the supervisor stopped; "
            "the File Browser offers to remove them",
            len(_leftovers.found),
        )
    if _leftovers.problems:
        logger.warning("Some folders could not be checked for leftovers of uploads: %s", "; ".join(_leftovers.problems))


async def start_upload_leftover_check() -> None:
    """Start the first check of leftovers of uploads (main.py's lifespan, ADR-0043)."""
    global _upload_leftover_task
    _upload_leftover_task = asyncio.create_task(first_upload_leftover_check(), name="unos-upload-leftovers")


async def stop_upload_leftover_check() -> None:
    """Stop it, and the check that runs, at shutdown; a walk that runs ends in its worker thread."""
    tasks = [task for task in (_upload_leftover_task, _leftovers.task) if task is not None and not task.done()]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


# --- endpoints -------------------------------------------------------------


@router.get("/list", response_model=DirectoryListing)
async def list_directory(path: str | None = None, _: dict = Depends(verify_token)):
    """List directory contents (the pools directory if no path is given)."""
    return await _run(_list_directory, path)


@router.get("/info", response_model=FileInfo)
async def get_file(path: str, _: dict = Depends(verify_token)):
    """Get file or directory info. Symlinks are described, not followed."""
    return await _run(_file_info, path)


@router.get("/download")
async def download_file(path: str, _: dict = Depends(verify_token)):
    """Download a regular file."""
    handle, name, size = await _run(_open_download, path)
    return StreamingResponse(
        _iter_file(handle),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": _content_disposition(name),
            "Content-Length": str(size),
        },
    )


@router.post("/upload", openapi_extra=UPLOAD_OPENAPI)
async def upload_file(path: str, request: Request, _: dict = Depends(verify_token)):
    """Upload a file (form field "file") into a directory. Only the base name of the upload is used.

    The form is read only here, after the credentials were checked, and only
    once the folder ``path`` was found; the file name is checked as soon as
    it arrives. The file then goes straight into that folder as it arrives,
    never through /tmp, and gets its name when it is complete
    (request_limits.receive_upload, _FileUpload). On Linux it is unnamed
    until then: nothing of it can be seen in the folder, and if the
    supervisor stops in the middle, nothing of it stays. Elsewhere, and on
    filesystems without unnamed files, it is a hidden temporary file, which
    a stop in the middle leaves behind until an administrator removes it
    (GET /files/upload-leftovers, ADR-0354). In a share folder the file gets
    the mode 0660, which lets the share's SMB users change it (ADR-0273).
    """
    try:
        return await receive_upload(request, _FileUpload(path))
    except OSError as exc:  # mapped as _run does
        raise _http_error(exc) from exc
    except ValueError as exc:  # e.g. undecodable names
        raise HTTPException(400, "Invalid path") from exc


@router.post("/mkdir", response_model=FileInfo)
async def create_directory(req: CreateDirectoryRequest, _: dict = Depends(verify_token)):
    """Create a new directory; in a share folder with the mode 2770, which lets the share's SMB users fill it."""
    return await _run(_create_directory, req.path, req.name)


@router.post("/rename", response_model=FileInfo)
async def rename_item(req: RenameRequest, _: dict = Depends(verify_token)):
    """Rename a file or directory in its folder (a symlink is renamed, not its target).

    A name that exists there is refused (400 "Already exists: <name>"), never replaced.
    """
    return await _run(_rename, req.path, req.new_name)


@router.post("/copy")
async def copy_items(
    sources: list[str],
    destination: str,
    _: dict = Depends(verify_token)
):
    """Copy files/directories. Symlinks are copied as links."""
    return {"copied": await _run(lambda: _transfer(sources, destination, moving=False))}


@router.post("/move")
async def move_items(
    sources: list[str],
    destination: str,
    _: dict = Depends(verify_token)
):
    """Move files/directories."""
    return {"moved": await _run(lambda: _transfer(sources, destination, moving=True))}


@router.delete("/delete")
async def delete_items(paths: list[str], permanent: bool = False, _: dict = Depends(verify_token)):
    """Move files/directories into the trash of their pool or volume (ADR-0259); with permanent=true, delete them
    for good. Symlinks are moved or removed, never their targets.

    An item that one rename cannot take into a trash (in a deeper subvolume,
    or below a mount point) is refused with 409 and can be deleted for good;
    so is every item while its trash is not a folder or cannot be made
    private. Every item is checked before any is changed; one that fails
    after others went answers with how many went before it.
    """
    return {"deleted": await _run(_delete, paths, permanent)}


@router.get("/trash", response_model=TrashListing)
async def list_trash(_: dict = Depends(verify_token)):
    """The items in the trash of every pool and volume, newest first, and the trashes that could not be read."""
    return await _run(_list_trash)


@router.post("/trash/restore")
async def restore_from_trash(paths: list[str], _: dict = Depends(verify_token)):
    """Put items of the trash (their paths as GET /files/trash lists them) back where they were deleted from.

    Nothing is replaced: a name that is taken there answers 400 "Already
    exists: <name>", and a folder that is gone, or on another subvolume or
    filesystem now, 409. Every item is checked before any is restored.
    """
    return {"restored": await _run(_restore_from_trash, paths)}


@router.delete("/trash")
async def delete_from_trash(paths: list[str], _: dict = Depends(verify_token)):
    """Delete items of the trash for good (their paths as GET /files/trash lists them)."""
    return {"deleted": await _run(_delete_from_trash, paths)}


@router.post("/trash/empty")
async def empty_trash(_: dict = Depends(verify_token)):
    """Delete everything in the trash of every pool and volume for good; answers how many items."""
    return {"deleted": await _run(_empty_trash)}


@router.get("/upload-leftovers", response_model=UploadLeftovers)
async def list_upload_leftovers(_: dict = Depends(verify_token)):
    """The hidden files that uploads left when the supervisor stopped, as the last check found them (ADR-0354).

    Without looking at the disks again: a removal does, and drops from the
    list the files that are gone or changed since.
    """
    task = _leftovers.task
    return _list_upload_leftovers(task is not None and not task.done())


@router.post("/upload-leftovers/check", response_model=UploadLeftovers)
async def check_for_upload_leftovers(_: dict = Depends(verify_token)):
    """Look for them in every folder below the pools directory now, then answer as GET does."""
    await check_upload_leftovers()
    return await list_upload_leftovers(_)


@router.delete("/upload-leftovers")
async def remove_upload_leftovers(paths: list[str], _: dict = Depends(verify_token)):
    """Remove such files for good (their paths as GET lists them); answers the ones that were there.

    Every path is checked before any file is removed: a path that is not of
    a hidden upload file answers 400, and a file that is not a leftover
    (not a regular file, an upload's in progress, or changed in the last
    10 minutes) 409. A file replaced or written between that check and its
    unlink is left alone, and not among the removed.
    """
    return {"removed": await _run(_remove_upload_leftovers, paths)}
