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
"""

import asyncio
import errno
import grp
import logging
import os
import pwd
import re
import secrets
import shutil
import stat
from collections.abc import Iterator
from datetime import datetime
from typing import BinaryIO
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..auth.utils import verify_token
from ..config import get_settings
from ..request_limits import UPLOAD_OPENAPI, UploadSink, receive_upload
from ..security.validators import open_dir_nofollow
from .schemas import CreateDirectoryRequest, DirectoryListing, FileInfo, FileType, RenameRequest

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


# --- paths -----------------------------------------------------------------


def _base_dir() -> str:
    return os.path.realpath(BASE_PATH)


def _split(path: str) -> list[str]:
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


def _display_path(parts: list[str]) -> str:
    return os.path.join(_base_dir(), *parts)


def _validate_name(name: str) -> str:
    """Validate a new single-component file or directory name."""
    if not isinstance(name, str) or name in ("", ".", ".."):
        raise HTTPException(400, "Invalid name")
    if "/" in name or any(ord(c) < 0x20 or ord(c) == 0x7F for c in name):
        raise HTTPException(400, "Invalid name: '/' and control characters are not allowed")
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


def _mount_points(name: str, action: str) -> list[bytes]:
    """The mount points in MOUNTINFO, decoded.

    The mount point is the fifth field of a line. If the table cannot be
    read, ``name`` is refused (HTTP 500): nothing is deleted or moved
    without the check.
    """
    try:
        with open(MOUNTINFO, "rb") as table:
            lines = table.read().split(b"\n")
    except OSError as exc:
        logger.error("Cannot read the mount table %s: %s", MOUNTINFO, exc)
        raise HTTPException(
            500,
            f"Could not check '{_shown(name)}' for mount points (the mount table cannot be read), "
            f"so it was not {action}.",
        ) from None
    points = []
    for line in lines:
        fields = line.split(b" ")
        if len(fields) > 4:
            points.append(_MOUNTINFO_ESCAPE.sub(lambda m: bytes([int(m[1], 8)]), fields[4]))
    return points


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


def get_file_info(name: str, path: str, st: os.stat_result) -> FileInfo:
    """Describe a directory entry from its lstat() result (links are not followed)."""
    if stat.S_ISLNK(st.st_mode):
        file_type = FileType.SYMLINK
    elif stat.S_ISDIR(st.st_mode):
        file_type = FileType.DIRECTORY
    else:
        file_type = FileType.FILE

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
        if new_name != parts[-1] and _lstat_at(dir_fd, new_name) is not None:
            raise HTTPException(400, f"Already exists: {new_name}")
        os.rename(parts[-1], new_name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        st = os.stat(new_name, dir_fd=dir_fd, follow_symlinks=False)
    finally:
        os.close(dir_fd)
    return get_file_info(new_name, _display_path([*parts[:-1], new_name]), st)


def _copy_entry(src_dir_fd: int, name: str, dst_dir_fd: int) -> None:
    """Copy ``name`` between two directories without following symlinks.

    Symlinks are recreated as symlinks, directories are copied recursively,
    and devices, FIFOs and sockets are skipped. Every destination entry is
    created exclusively, so nothing that appears in the destination while the
    copy runs (e.g. a symlink planted by another user) is written through.
    Copies are owned by root, so set-user-ID and set-group-ID bits are
    dropped from files.
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
                    children = [entry.name for entry in entries]
                for child in children:
                    _copy_entry(src_fd, child, dst_fd)
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


def _remove_entry(dir_fd: int, parts: list[str], action: str, expected: os.stat_result | None = None) -> None:
    """Delete ``parts[-1]`` from ``dir_fd`` (recursively for directories) without following symlinks.

    A directory is checked for subvolumes and mount points below it right
    before (refused as not ``action``). That takes a while, so the entry is
    then checked again: it must still be the same directory, and not a
    subvolume or mount point itself. ``expected`` is an earlier lstat() of
    the entry (the source of a move, before it was copied): an entry that
    has replaced it since is not deleted either.
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
                    _copy_entry(src_dir_fd, name, dest_fd)
            finally:
                os.close(src_dir_fd)
            done.append(_display_path([*dest_parts, name]))
        return done
    finally:
        os.close(dest_fd)


def _delete(paths: list[str]) -> list[str]:
    targets = []
    for path in paths:
        parts = _split(path)
        _refuse_protected(parts)
        targets.append((path, parts))
    _refuse_separate_filesystems([parts for _, parts in targets], "deleted")

    deleted = []
    for path, parts in targets:
        dir_fd = _open_parent_or_none(parts)
        if dir_fd is None:
            continue
        try:
            st = _lstat_at(dir_fd, parts[-1])
            if st is None:
                continue
            _refuse_separate_filesystem(dir_fd, st, parts, "deleted")
            _remove_entry(dir_fd, parts, "deleted")
        finally:
            os.close(dir_fd)
        deleted.append(path)
    return deleted


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
    """Rename a file or directory (a symlink is renamed, not its target)."""
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
async def delete_items(paths: list[str], _: dict = Depends(verify_token)):
    """Delete files/directories. Symlinks are removed, never their targets."""
    return {"deleted": await _run(_delete, paths)}
