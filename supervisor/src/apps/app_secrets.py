"""The password files that UNOS generates on an app's pool (ADR-0381).

A password file (``VolumeMapping.generated_secret``) lies beside the data it unlocks, such as an
app's database, and moves with it in the pool's snapshots. The install creates it when it is
missing: 64 hexadecimal digits from ``secrets.token_hex(32)``, owned by root with mode 0600,
written to a temporary file of the same folder, opened without following links in a folder
reached without following links (ADR-0042), synced, linked to its name and the folder synced,
before any container that reads it is created. A file that appeared meanwhile is kept. A start
never creates it: the data it unlocks keeps its first password.

The password itself never leaves the file: it is in no exception, log line, answer, audit entry,
label or Docker configuration, which name the file's path only. A file that is there is checked
without being shown: a regular file of 1 to 256 bytes of letters and digits (and a final newline)
is one UNOS made; anything else answers 409, and UNOS neither shows nor replaces it.
"""

from __future__ import annotations

import contextlib
import errno
import os
import re
import secrets
import stat
from pathlib import Path

from fastapi import HTTPException

from ..security.validators import open_dir_nofollow

# The longest password file that UNOS takes as its own; it writes 64 bytes.
MAX_BYTES = 256
_PASSWORD = re.compile(rb"[A-Za-z0-9]+\n?")
# Errors of an open of the file's name that say something else is there: a link (O_NOFOLLOW), a
# socket or a device without a driver.
_NOT_A_FILE = {errno.ELOOP, errno.ENXIO, errno.EOPNOTSUPP, errno.EISDIR, errno.ENODEV}


def _parts(root: str, path: Path) -> tuple[list[str], str]:
    """The folders below ``root`` on the way to ``path``, and its name."""
    *folders, name = os.path.relpath(path, root).split(os.sep)
    return folders, name


def _folder_problem(path: Path, target: str, error: OSError) -> HTTPException:
    """The answer to a folder on the way to ``path`` that cannot be opened without following links."""
    if error.errno == errno.ELOOP:
        return HTTPException(
            409,
            f"A folder on the way to {path} is a symbolic link. Docker would follow it when it mounts "
            f"{path} at {target}, so the app could reach folders outside the pool. Replace the link with "
            "a real folder.",
        )
    if error.errno == errno.ENOTDIR:
        return HTTPException(409, f"A folder on the way to {path} is not a folder, so {path} cannot be used for {target}")
    return HTTPException(500, f"Could not check {path}: {error.strerror or error}")


def not_ours(path: Path, secret_for: Path | None) -> HTTPException:
    """The 409 for a file in the place of a password file that is not one UNOS made."""
    remedy = f"or, if {secret_for} is empty, remove it" if secret_for is not None else "or remove it"
    return HTTPException(
        409,
        f"{path} is not a password UNOS made (a link, empty, too long, or not letters and digits). "
        f"UNOS neither shows nor replaces it: restore it from a snapshot of the pool, {remedy} and install "
        "the app again.",
    )


def is_present(root: str, path: Path, target: str, secret_for: Path | None) -> bool:
    """Whether the password file ``path`` (below the pool root ``root``, bound at ``target``) is
    there: True for a file UNOS made, False when nothing is at its name; 409 for anything else,
    which is never shown (``secret_for``: the data folder it unlocks, named in the answer)."""
    folders, name = _parts(root, path)
    try:
        dir_fd = open_dir_nofollow(root, folders)
    except FileNotFoundError:
        return False
    except OSError as e:
        raise _folder_problem(path, target, e) from e
    try:
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=dir_fd)
        except FileNotFoundError:
            return False
        except OSError as e:
            if e.errno in _NOT_A_FILE:
                raise not_ours(path, secret_for) from None
            raise HTTPException(500, f"Could not check {path}: {e.strerror or e}") from None
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise not_ours(path, secret_for)
            data = b""
            while len(data) <= MAX_BYTES:
                chunk = os.read(fd, MAX_BYTES + 1 - len(data))
                if not chunk:
                    break
                data += chunk
            ours = 0 < len(data) <= MAX_BYTES and _PASSWORD.fullmatch(data) is not None
            del data
        except OSError as e:
            raise HTTPException(500, f"Could not check {path}: {e.strerror or e}") from None
        finally:
            os.close(fd)
    finally:
        os.close(dir_fd)
    if not ours:
        raise not_ours(path, secret_for)
    return True


def holds_data(root: str, folder: Path) -> bool:
    """Whether the folder ``folder`` below the pool root ``root`` is there and not empty; what
    cannot be opened as a folder without following links counts as data."""
    folders, name = _parts(root, folder)
    try:
        fd = open_dir_nofollow(root, [*folders, name])
    except FileNotFoundError:
        return False
    except OSError:
        return True
    try:
        return bool(os.listdir(fd))
    finally:
        os.close(fd)


def create(root: str, path: Path, target: str, *, uid: int | None = None, gid: int | None = None) -> bool:
    """Create the password file ``path`` below the pool root ``root`` (bound at ``target``):
    True when this call created it, False when a file appeared at its name meanwhile, which is
    kept. ``uid`` and ``gid`` own it when given (root otherwise); its mode is 0600."""
    folders, name = _parts(root, path)
    try:
        dir_fd = open_dir_nofollow(root, folders, create=True)
    except OSError as e:
        raise _folder_problem(path, target, e) from e
    temporary = f".{name}.{secrets.token_hex(8)}.tmp"
    try:
        fd = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=dir_fd
        )
        try:
            os.fchmod(fd, 0o600)
            if uid is not None or gid is not None:
                os.fchown(fd, uid if uid is not None else -1, gid if gid is not None else -1)
            view = memoryview(secrets.token_hex(32).encode())
            while view:
                view = view[os.write(fd, view):]
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            os.link(temporary, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd, follow_symlinks=False)
            created = True
        except FileExistsError:
            created = False
        os.unlink(temporary, dir_fd=dir_fd)
        os.fsync(dir_fd)
        return created
    except OSError as e:
        with contextlib.suppress(OSError):
            os.unlink(temporary, dir_fd=dir_fd)
        raise HTTPException(500, f"Could not create {path}: {e.strerror or e}") from None
    finally:
        os.close(dir_fd)
