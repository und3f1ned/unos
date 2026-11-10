"""The supervisor's JSON state files under /data: replaced whole, and kept when they cannot be read.

A state file is replaced, never rewritten in place (``replace_file``): the new content goes to a
temporary file next to it, which is then renamed over it, so that a supervisor killed during the
write leaves the old file or the new one, never a part (ADR-0324). A file whose loss on a power
cut an ADR rules out is also synced before the rename (``sync=True``: the SMART history, the
notification files and the keys of the backup tasks, all written in worker threads); the others
are written from the event loop and its loops as before, without a sync.

A state file that cannot be read as the state it holds (not UTF-8, not JSON, or refused by the
reader's check) is not taken for an empty state: ``load_json`` renames it to ``<name>.bad``,
replacing an earlier one, names it in a warning and answers the defaults, so that the next write
does not destroy what the file still holds. A file that cannot be opened (an I/O error, missing
permissions, a directory) is an error of its own: it raises, and nothing is renamed; each module
decides what its loader answers then.

The renames of one path take a lock of that path, and the rename to <name>.bad happens only while
the path still holds the file that was read: a reader never sets aside a file that a writer has
just put in place.
"""

import json
import logging
import os
import secrets
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# The name a state file that cannot be read gets: <name>.bad, next to it.
BAD_SUFFIX = ".bad"

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_of(path: Path) -> threading.Lock:
    """The lock that the renames of ``path`` take, in this process."""
    key = os.path.abspath(path)
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def bad_path(path: Path) -> Path:
    """Where ``load_json`` keeps a state file that it could not read."""
    return path.with_name(path.name + BAD_SUFFIX)


def replace_file(path: Path, data: bytes, *, sync: bool, mode: int = 0o644) -> None:
    """Replace ``path`` with ``data``.

    The data goes to ``.<name>.<random>.tmp`` next to the file, created with ``mode`` (0o600 for a
    file that holds secrets) whatever the umask, written and flushed, synced when ``sync`` is set
    (blocking: run it in a worker thread then), and renamed over the file. A failure removes the
    temporary file and leaves the file as it was.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    fd = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, mode)
    try:
        with os.fdopen(fd, "wb") as f:
            os.fchmod(f.fileno(), mode)  # not narrowed by the umask
            f.write(data)
            f.flush()
            if sync:
                os.fsync(f.fileno())
        with _lock_of(path):
            os.replace(staged, path)
    except BaseException:
        staged.unlink(missing_ok=True)
        raise


def write_json(path: Path, value: Any, *, sync: bool, mode: int = 0o644, **dumps: Any) -> None:
    """Replace ``path`` with ``value`` as JSON (``json.dumps`` with ``dumps``; indent=2 by default)."""
    dumps.setdefault("indent", 2)
    replace_file(path, json.dumps(value, **dumps).encode("utf-8", "backslashreplace"), sync=sync, mode=mode)


def load_json(path: Path, read: Callable[[Any], T], default: Callable[[], T], *, what: str) -> T:
    """The state that ``read`` makes of the JSON in ``path``; ``default()`` without the file.

    ``read`` takes the decoded JSON and raises ValueError (pydantic's ValidationError is one) or
    TypeError for a value that is no state of the file. Then, and for a file that is not UTF-8 or
    not JSON, the file is renamed to <name>.bad, a warning names ``what``, both paths and the
    reason, and ``default()`` is answered. A file that a writer put in place meanwhile is read
    instead. An OSError of the read, other than a missing file, raises: the file stays.
    """
    path = Path(path)
    for _ in range(2):
        try:
            with open(path, "rb") as f:
                inode = os.fstat(f.fileno()).st_ino
                raw = f.read()
        except FileNotFoundError:
            return default()
        try:
            return read(json.loads(raw))
        except (ValueError, TypeError) as exc:  # UnicodeDecodeError and JSONDecodeError are ValueErrors
            reason = exc
        with _lock_of(path):
            try:
                current = os.stat(path).st_ino
            except FileNotFoundError:  # set aside meanwhile by another reader
                return default()
            if current != inode:
                continue  # a writer replaced it meanwhile: read what it wrote
            bad = bad_path(path)
            try:
                os.replace(path, bad)
            except OSError as exc:
                logger.warning("Could not read %s %s (%s), nor keep it as %s (%s): using the defaults",
                               what, path, reason, bad, exc)
                return default()
        logger.warning("Could not read %s %s (%s): kept as %s, using the defaults", what, path, reason, bad)
        return default()
    return default()
