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
"""

import asyncio
import errno
import grp
import os
import pwd
import secrets
import shutil
import stat
from collections.abc import Iterator
from datetime import datetime
from typing import BinaryIO
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from ..auth.utils import verify_token
from ..config import get_settings
from ..security.validators import open_dir_nofollow
from .schemas import CreateDirectoryRequest, DirectoryListing, FileInfo, FileType, RenameRequest

router = APIRouter(prefix="/files", tags=["files"])

settings = get_settings()

# Directory the file manager is confined to. Module level so tests can move it.
BASE_PATH = settings.pools_base_path

_CHUNK_SIZE = 1024 * 1024
_NAME_MAX_BYTES = 255
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
# O_NONBLOCK so that opening a FIFO cannot hang; it does not affect regular files.
_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
# O_EXCL with O_CREAT never follows a symlink and fails if the name exists.
_CREATE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC


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


def _store_upload(path: str, filename: str | None, source: BinaryIO) -> FileInfo:
    parts = _split(path)
    name = _upload_name(filename)
    _require_inside_pool(parts)
    dir_fd = _open_dir(parts)
    try:
        existing = _lstat_at(dir_fd, name)
        if existing is not None and stat.S_ISDIR(existing.st_mode):
            raise HTTPException(400, f"A folder named {name} already exists")
        # Stream into a new temporary file, then rename it over the target:
        # rename() replaces a symlink at the target instead of following it,
        # and a failed upload never leaves a truncated file behind.
        tmp_name = f".unos-upload-{secrets.token_hex(8)}"
        fd = os.open(tmp_name, _CREATE_FLAGS, 0o666, dir_fd=dir_fd)
        try:
            with os.fdopen(fd, "wb") as out:
                shutil.copyfileobj(source, out, _CHUNK_SIZE)
            os.rename(tmp_name, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        except BaseException:
            try:
                os.unlink(tmp_name, dir_fd=dir_fd)
            except OSError:
                pass
            raise
        st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    finally:
        os.close(dir_fd)
    return get_file_info(name, _display_path([*parts, name]), st)


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
        if _lstat_at(dir_fd, parts[-1]) is None:
            raise HTTPException(404, f"Not found: {path}")
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


def _remove_entry(dir_fd: int, name: str) -> None:
    """Delete ``name`` (recursively for directories) without following symlinks."""
    st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
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

        done = []
        for parts in plan:
            src_dir_fd = _open_parent_or_none(parts)
            if src_dir_fd is None:
                continue  # missing sources are skipped, as before
            try:
                name = parts[-1]
                if _lstat_at(src_dir_fd, name) is None:
                    continue
                if moving:
                    try:
                        os.rename(name, name, src_dir_fd=src_dir_fd, dst_dir_fd=dest_fd)
                    except OSError as exc:
                        if exc.errno != errno.EXDEV:
                            raise
                        # Different filesystem (e.g. another pool): copy, then delete.
                        _copy_entry(src_dir_fd, name, dest_fd)
                        _remove_entry(src_dir_fd, name)
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

    deleted = []
    for path, parts in targets:
        dir_fd = _open_parent_or_none(parts)
        if dir_fd is None:
            continue
        try:
            if _lstat_at(dir_fd, parts[-1]) is None:
                continue
            _remove_entry(dir_fd, parts[-1])
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


@router.post("/upload")
async def upload_file(
    path: str,
    file: UploadFile = File(...),
    _: dict = Depends(verify_token)
):
    """Upload a file into a directory. Only the base name of the upload is used."""
    return await _run(_store_upload, path, file.filename, file.file)


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
