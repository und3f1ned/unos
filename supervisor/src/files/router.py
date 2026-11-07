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
after TRASH_RETENTION_DAYS (ADR-0247, "the trash" below).
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
from .schemas import (
    CreateDirectoryRequest,
    DirectoryListing,
    FileInfo,
    FileType,
    RenameRequest,
    TrashItem,
    TrashListing,
)

router = APIRouter(prefix="/files", tags=["files"])
logger = logging.getLogger(__name__)

settings = get_settings()

# Directory the file manager is confined to. Module level so tests can move it.
BASE_PATH = settings.pools_base_path
# The mount table of the supervisor's mount namespace. Module level so tests can move it.
MOUNTINFO = "/proc/self/mountinfo"

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

# The trash (ADR-0247): the folder in the top folder of each pool and volume
# that takes what is deleted there. Its name is reserved: the file manager
# lists, opens and makes no entry of that name, and copies leave it out.
TRASH = ".unos-trash"
# Days that a deleted item stays in the trash before the expiry deletes it for good.
TRASH_RETENTION_DAYS = 30
# Seconds between two passes of the expiry (the first one runs at the start).
TRASH_EXPIRY_INTERVAL = 24 * 60 * 60
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

    The trash API alone reads and changes the trash (ADR-0247).
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
    A bind mount from the same filesystem has the same st_dev:
    _refuse_nested_filesystems finds it in the mount table before a delete or
    a move to another filesystem; renaming it fails with EBUSY.
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

    ``dir_fd`` is the directory that contains it. shutil.rmtree, and the
    copy and delete of a move to another filesystem, go into subvolumes and
    mounted filesystems below a folder: deleting it would delete their
    files too. A subvolume or another filesystem has another st_dev than
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
    then deleted.
    """
    mount_points = None
    for parts in plan:
        dir_fd = _open_parent_or_none(parts)
        if dir_fd is None:
            continue
        try:
            st = _lstat_at(dir_fd, parts[-1])
            if st is None:
                continue
            _refuse_separate_filesystem(dir_fd, st, parts, action)
            if not stat.S_ISDIR(st.st_mode) or (moving_to is not None and st.st_dev == moving_to):
                continue
            nested_action = action if moving_to is None else _MOVED_ELSEWHERE
            if mount_points is None:
                mount_points = _mount_points(parts[-1], nested_action)
            _refuse_nested_filesystems(dir_fd, parts, nested_action, mount_points)
        finally:
            os.close(dir_fd)


# --- errors ----------------------------------------------------------------


def _http_error(exc: OSError) -> HTTPException:
    """Map an OSError to an HTTP error without leaking host paths."""
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
                if entry.name == TRASH:  # listed with GET /files/trash (ADR-0247)
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
    temporary file.
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
        fd = self._open_unnamed()
        if fd is None:
            tmp_name = _upload_temp_name()
            fd = os.open(tmp_name, _CREATE_FLAGS, 0o666, dir_fd=self.dir_fd)
            self.tmp_name = tmp_name
        return fd

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
                tmp_name = _upload_temp_name()
                os.link(source, tmp_name, dst_dir_fd=self.dir_fd, follow_symlinks=True)
                self.tmp_name = tmp_name
            self._close_unnamed()
        if self.tmp_name is not None:
            os.rename(self.tmp_name, self.name, src_dir_fd=self.dir_fd, dst_dir_fd=self.dir_fd)
            self.tmp_name = None
        st = os.stat(self.name, dir_fd=self.dir_fd, follow_symlinks=False)
        return get_file_info(self.name, _display_path([*self.parts, self.name]), st)

    def discard(self) -> None:
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
    parts = _split(path)
    name = _validate_name(name)
    _require_inside_pool(parts)
    dir_fd = _open_dir(parts)
    try:
        try:
            os.mkdir(name, 0o777, dir_fd=dir_fd)
        except FileExistsError:
            raise HTTPException(400, f"Already exists: {name}") from None
        st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    finally:
        os.close(dir_fd)
    return get_file_info(name, _display_path([*parts, name]), st)


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
    (ADR-0247). A move to another filesystem copies everything, because it
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
        # rmtree with dir_fd uses the fd-based, symlink-attack-safe walk.
        shutil.rmtree(name, dir_fd=dir_fd)
    else:
        os.unlink(name, dir_fd=dir_fd)


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
            src_dir_fd = _open_parent_or_none(parts)
            if src_dir_fd is None:
                continue  # missing sources are skipped, as before
            try:
                name = parts[-1]
                st = _lstat_at(src_dir_fd, name)
                if st is None:
                    continue
                if moving:
                    _refuse_separate_filesystem(src_dir_fd, st, parts, "moved")
                    try:
                        os.rename(name, name, src_dir_fd=src_dir_fd, dst_dir_fd=dest_fd)
                    except OSError as exc:
                        if exc.errno != errno.EXDEV:
                            raise
                        # Different filesystem (e.g. another pool): copy, then delete.
                        if stat.S_ISDIR(st.st_mode):
                            _refuse_nested_filesystems(src_dir_fd, parts, _MOVED_ELSEWHERE)
                        _copy_entry(src_dir_fd, name, dest_fd)
                        _remove_entry(src_dir_fd, parts, _MOVED_ELSEWHERE, expected=st)
                else:
                    _copy_entry(src_dir_fd, name, dest_fd, True)
            finally:
                os.close(src_dir_fd)
            done.append(_display_path([*dest_parts, name]))
        return done
    finally:
        os.close(dest_fd)


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


# --- the trash (ADR-0247) ------------------------------------------------------
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
# the checks of ADR-0170.


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


def _trash_scopes(problems: list[str]) -> list[list[str]]:
    """Every pool, each followed by its volumes: the places that can have a trash, by name.

    A pool whose top folder cannot be read is left out, with the reason in ``problems``.
    """
    base_fd = _open_dir([])
    try:
        pools = sorted(_subdirectories(base_fd))
    finally:
        os.close(base_fd)
    scopes = []
    for pool in pools:
        if pool == TRASH:
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


def _each_trash(problems: list[str]) -> Iterator[tuple[list[str], int]]:
    """(the pool or volume, its open trash folder) for each trash; what cannot be read goes to ``problems``.

    Close the iterator when done with it (contextlib.closing): it holds descriptors.
    """
    for scope in _trash_scopes(problems):
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
    """Delete the trash entry ``entry`` for good, after the checks of ADR-0170.

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


def _expire_trash(now: datetime) -> tuple[int, list[str]]:
    """Delete for good what was deleted TRASH_RETENTION_DAYS or more before ``now``.

    Returns how many entries it deleted and the problems: the trashes it
    could not read and the entries it could not delete, which stay.
    """
    cutoff = now - timedelta(days=TRASH_RETENTION_DAYS)
    problems: list[str] = []
    count = 0
    with closing(_each_trash(problems)) as trashes:
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


_trash_expiry_task: asyncio.Task | None = None


async def trash_expiry_loop() -> None:
    """Delete for good what has been in the trash for TRASH_RETENTION_DAYS: at the start, then once a day.

    Each pass reads the pools in a worker thread, without a timeout: a pool
    that hangs holds that one thread, and the next pass waits for it. A pass
    logs what it deleted, and its problems when they differ from the last
    pass's; one that finds nothing to do writes nothing (ADR-0214).
    """
    reported: list[str] = []
    while True:
        try:
            count, problems = await asyncio.to_thread(_expire_trash, _now())
        except Exception as exc:  # e.g. the pools directory cannot be read
            count, problems = 0, [str(exc) or type(exc).__name__]
        if count:
            logger.info(
                "Deleted %d item(s) for good that had been in the trash for %d days", count, TRASH_RETENTION_DAYS
            )
        if problems and problems != reported:
            logger.warning("The trash could not be cleared out completely: %s", "; ".join(problems))
        reported = problems
        await asyncio.sleep(TRASH_EXPIRY_INTERVAL)


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
    a stop in the middle leaves behind.
    """
    try:
        return await receive_upload(request, _FileUpload(path))
    except OSError as exc:  # mapped as _run does
        raise _http_error(exc) from exc
    except ValueError as exc:  # e.g. undecodable names
        raise HTTPException(400, "Invalid path") from exc


@router.post("/mkdir", response_model=FileInfo)
async def create_directory(req: CreateDirectoryRequest, _: dict = Depends(verify_token)):
    """Create a new directory."""
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
    """Move files/directories into the trash of their pool or volume (ADR-0247); with permanent=true, delete them
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
