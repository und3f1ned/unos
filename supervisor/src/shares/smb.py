"""smb.conf on the host: the file, testparm, the reload and close-share (spec 2.1, 2.4-2.7).

smb.conf is rendered from the UNOS database: shares/smbconf.py writes the text
and shares/service.py builds its input and runs the pipeline. This module owns
the file and the host commands around it. Every host command goes through the
module object ``host.run_on_host``, so that tests patching
``src.host.run_on_host`` see each testparm, reload and close-share call.

The file is replaced atomically: the new text is written to a temporary file
in the same directory (``/etc/samba`` is a bind mount of the host's
``/etc/samba``), fsynced, validated with the host's ``testparm`` and renamed
over smb.conf; the directory is fsynced after the rename. Then smbd and nmbd
on the host are told to reload it.

smb.conf has no quoting: a newline in any value starts a new directive and a
trailing backslash continues the line. Values are validated before they are
stored, validated again when the file is rendered (restored databases are
untrusted), and checked once more by the serializer guards of smbconf.py
before they are written.

Share paths are stored as given (normalized), never as the target of a
symlink: pool contents are writable by SMB users and app containers, and a
planted link used to be resolved into a path that was then shared, e.g.
another pool's private directory or a directory named ``%U`` that smbd
expands to the client's user name. Validation refuses paths that go through a
symlink, and ``path`` values must not contain ``%``, ``"``, ``\\`` or spaces
that Samba trims or collapses. smbd still resolves a share's root when a
client connects, so a share root that is replaced by a symlink later is
followed; keep share roots out of directories that apps or guests can modify.
"""

import logging
import os
import re
import sqlite3
import stat
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException

from .. import host
from ..config import get_settings
from ..security.validators import SHARE_NAME_PATTERN
from . import smbconf

logger = logging.getLogger(__name__)
settings = get_settings()

COMMENT_MAX_LENGTH = 256

# testparm exits 1 when the cache directory ("cache directory" in [global],
# Debian's lock directory, a tmpfs) is missing, e.g. after a boot before smbd ran.
CACHE_DIRECTORY = "/run/samba"
# Copies kept next to smb.conf: the file found by the one-time import, and a
# file whose share sections UNOS did not render (spec 1.4, 2.4 step 7).
IMPORT_BACKUP_SUFFIX = ".unos-import-backup"
REPLACED_SUFFIX = ".unos-replaced"
# Temporary files next to smb.conf: ".smb.conf.<random>.unos-tmp".
_TEMP_PREFIX = ".smb.conf."
_TEMP_SUFFIX = ".unos-tmp"
# What testparm prints for a parameter it does not know: 'Ignoring unknown parameter
# "x"' (Samba 4.22, source3/param/loadparm.c) or 'Unknown parameter encountered: "x"'.
_UNKNOWN_PARAMETER_MARKERS = ("Unknown parameter encountered", "Ignoring unknown parameter")
# [global] lines of smbconf.render_global that restate Samba's default and that older
# Samba releases (of the older supported systems) may not know; smbd ignores such a line,
# so testparm's message about exactly these is no reason to refuse the file.
TOLERATED_UNKNOWN_PARAMETERS = ("server smb encrypt", "smb3 unix extensions")
_TOLERATED_UNKNOWN_LINE = re.compile(
    r'(?:Unknown parameter encountered:|Ignoring unknown parameter) "(?:'
    + "|".join(re.escape(name) for name in TOLERATED_UNKNOWN_PARAMETERS)
    + r')"\Z'
)

# Serializes the renames of smb.conf.
_lock = threading.Lock()


class SambaConfigError(RuntimeError):
    """A new smb.conf could not be validated or installed."""


# --- validation ---------------------------------------------------------------


def _bad_request(message: str) -> HTTPException:
    return HTTPException(status_code=400, detail=message)


def _validate_comment(comment: str) -> str:
    if len(comment) > COMMENT_MAX_LENGTH:
        raise _bad_request(f"Share comment cannot exceed {COMMENT_MAX_LENGTH} characters")
    if not comment.isprintable():
        raise _bad_request("Share comment must not contain line breaks or control characters")
    if any(c in comment for c in "[]\\"):
        raise _bad_request("Share comment must not contain '[', ']' or '\\'")
    return comment


# --- the file -------------------------------------------------------------------


def _discard(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def _fsync_dir(directory: str) -> None:
    """Make renames in ``directory`` durable; a failure is logged (the rename itself is done)."""
    try:
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    except OSError as exc:
        logger.warning("Could not open %s to flush it to disk: %s", directory, exc)
        return
    try:
        os.fsync(fd)
    except OSError as exc:
        logger.warning("Could not flush %s to disk: %s", directory, exc)
    finally:
        os.close(fd)


def _conf_directory() -> str:
    return os.path.dirname(os.path.abspath(settings.smb_conf_path))


def read_conf_bytes() -> bytes | None:
    """The bytes of smb.conf, or None when there is no file. Other errors are raised."""
    try:
        with open(settings.smb_conf_path, "rb") as f:
            return f.read()
    except FileNotFoundError:
        return None


def import_backup_path() -> str:
    return settings.smb_conf_path + IMPORT_BACKUP_SUFFIX


def replaced_path() -> str:
    return settings.smb_conf_path + REPLACED_SUFFIX


def _stage(content: str) -> str:
    """Write ``content`` to a temporary file next to smb.conf and return its path."""
    target = settings.smb_conf_path
    directory = os.path.dirname(os.path.abspath(target))
    os.makedirs(directory, exist_ok=True)
    try:
        mode = stat.S_IMODE(os.stat(target).st_mode)
    except FileNotFoundError:
        mode = 0o644
    fd, tmp = tempfile.mkstemp(prefix=_TEMP_PREFIX, suffix=_TEMP_SUFFIX, dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", errors="surrogateescape") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
    except BaseException:
        _discard(tmp)
        raise
    return tmp


def _ensure_cache_dir() -> None:
    """Create Samba's cache directory before testparm (it is on a tmpfs: no disk write).

    A failure is logged; testparm then decides.
    """
    try:
        result = host.run_on_host(["mkdir", "-p", "-m", "0755", CACHE_DIRECTORY], timeout=10)
    except Exception as exc:
        logger.warning("Could not create %s on the host: %s", CACHE_DIRECTORY, exc)
        return
    if not result.ok:
        logger.warning(
            "Could not create %s on the host (exit %d): %s",
            CACHE_DIRECTORY,
            result.returncode,
            (result.stderr or result.stdout).strip(),
        )


def _unknown_parameter_tolerated(line: str) -> bool:
    """Whether a line of testparm about an unknown parameter names exactly one of the two
    parameters that older Samba releases lack (TOLERATED_UNKNOWN_PARAMETERS), and nothing else."""
    return sum(line.count(marker) for marker in _UNKNOWN_PARAMETER_MARKERS) == 1 and bool(
        _TOLERATED_UNKNOWN_LINE.search(line)
    )


def _testparm(path: str) -> None:
    """Validate a candidate smb.conf with testparm on the host.

    Skipped with a warning if testparm is not available. Any other failure
    raises SambaConfigError: the file must not be installed. A parameter that
    testparm does not know is a failure too: Samba would ignore the line, and
    a line that UNOS writes must never be ignored. The only exceptions are
    the two lines of [global] that restate a default and that older Samba
    releases do not know (TOLERATED_UNKNOWN_PARAMETERS): smbd ignores them.
    """
    try:
        result = host.run_on_host(["testparm", "-s", "--suppress-prompt", path], timeout=30)
    except FileNotFoundError:
        logger.warning("testparm/nsenter not found; installing smb.conf without testparm validation")
        return
    except subprocess.TimeoutExpired as exc:
        raise SambaConfigError("testparm timed out while validating smb.conf") from exc
    if result.returncode == 127:  # nsenter: command not found on the host
        logger.warning("testparm is not installed on the host; installing smb.conf without validation")
        return
    unknown: list[str] = []
    tolerated: list[str] = []
    for line in f"{result.stdout}\n{result.stderr}".splitlines():
        line = line.strip()
        if any(marker in line for marker in _UNKNOWN_PARAMETER_MARKERS):
            (tolerated if _unknown_parameter_tolerated(line) else unknown).append(line)
    if unknown:
        raise SambaConfigError(f"testparm does not know a parameter: {' | '.join(unknown[:5])}")
    if tolerated:
        logger.info("This Samba does not know a [global] line that restates a default; smbd ignores it: %s",
                    " | ".join(tolerated))
    if not result.ok:
        output = (result.stderr or result.stdout).strip().splitlines()
        detail = " | ".join(output[-5:]) or f"exit status {result.returncode}"
        raise SambaConfigError(f"testparm rejected the new smb.conf: {detail}")


def _install(tmp: str) -> None:
    """Rename the staged file over smb.conf, then fsync the directory (spec 2.4 step 9).

    ``/etc/samba`` is a bind mount of the host's directory, so this flushes the
    host's directory entry. The temporary file is removed when the rename fails.
    """
    target = settings.smb_conf_path
    with _lock:
        try:
            os.replace(tmp, target)
        except BaseException:
            _discard(tmp)
            raise
        _fsync_dir(os.path.dirname(os.path.abspath(target)))


def _write_file_durably(path: str, data: bytes) -> None:
    """Replace ``path`` with ``data``: temporary file, fsync, rename, fsync of the directory."""
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=_TEMP_PREFIX, suffix=_TEMP_SUFFIX, dir=directory)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        _discard(tmp)
        raise
    _fsync_dir(directory)


def keep_import_backup(data: bytes) -> bool:
    """Copy the original smb.conf to ``<smb.conf>.unos-import-backup`` (spec 1.4).

    An existing copy is never overwritten. Returns True when this call wrote it.
    """
    path = import_backup_path()
    if os.path.lexists(path):
        return False
    _write_file_durably(path, data)
    return True


def keep_replaced(data: bytes) -> bool:
    """Copy smb.conf to ``<smb.conf>.unos-replaced`` unless that file has exactly these bytes."""
    path = replaced_path()
    try:
        with open(path, "rb") as f:
            if f.read() == data:
                return False
    except FileNotFoundError:
        pass
    _write_file_durably(path, data)
    return True


def remove_stale_temp_files() -> list[str]:
    """Delete ``.smb.conf.*.unos-tmp`` next to smb.conf (spec 2.6); returns the names deleted.

    A supervisor stopped between mkstemp and rename leaves these behind. This
    writes only when such files exist.
    """
    directory = _conf_directory()
    try:
        names = sorted(os.listdir(directory))
    except FileNotFoundError:
        return []
    removed = []
    for name in names:
        if not (name.startswith(_TEMP_PREFIX) and name.endswith(_TEMP_SUFFIX)):
            continue
        try:
            os.unlink(os.path.join(directory, name))
        except FileNotFoundError:
            continue
        except OSError as exc:
            logger.warning("Could not remove the stale temporary file %s: %s", name, exc)
            continue
        removed.append(name)
    if removed:
        logger.info("Removed stale temporary files next to smb.conf: %s", ", ".join(removed))
    return removed


# --- Samba on the host -------------------------------------------------------------


def _reload_samba() -> tuple[bool, str | None]:
    """Ask smbd and nmbd on the host to reload smb.conf. Never raises.

    * (True, None): ``smbcontrol all reload-config`` worked. ``all`` (not
      ``smbd``) so that nmbd also rereads [global]; the main smbd passes the
      message on to the smbd processes that serve the open connections.
    * (True, note): only the fallback ``systemctl reload smbd`` worked. It
      sends SIGHUP to the main smbd alone (Debian's ExecReload): new
      connections get the new smb.conf, and closing a share makes the
      processes of its connections reload, but the other processes serving
      open connections, and nmbd, reread the file only at their periodic
      check, within a few minutes. ``note`` says so; callers keep the full
      reload pending, and close again after it the shares they close
      meanwhile (a tree connect keeps the access it was granted).
    * (False, why): neither worked.
    """
    errors = []
    for cmd in (["smbcontrol", "all", "reload-config"], ["systemctl", "reload", "smbd"]):
        try:
            result = host.run_on_host(cmd, timeout=30)
        except Exception as exc:
            logger.warning("Reloading Samba with %s failed: %s", cmd[0], exc)
            errors.append(f"{cmd[0]}: {exc}")
            continue
        if result.ok:
            if not errors:
                return True, None
            note = (
                f"Samba was reloaded with systemctl only ({'; '.join(errors)}); "
                "open connections apply the change within a few minutes"
            )
            logger.warning("%s", note)
            return True, note
        detail = (result.stderr or result.stdout).strip() or f"exit status {result.returncode}"
        logger.warning("Reloading Samba with %s failed (exit %d): %s", cmd[0], result.returncode, detail)
        errors.append(f"{cmd[0]}: {detail}")
    logger.error("Samba did not reload smb.conf; the changes apply when smbd next reloads or restarts")
    return False, "; ".join(errors)


def _close_share(name: object) -> str | None:
    """End every tree connect of share ``name`` (``smbcontrol smbd close-share``).

    Returns None, or a warning. A name that is not a valid share name (from a
    hand-edited file) is never passed to smbcontrol.
    """
    if not isinstance(name, str) or not SHARE_NAME_PATTERN.fullmatch(name):
        return f"Open connections to {smbconf.note_value(name)} were not closed: not a valid share name"
    try:
        result = host.run_on_host(["smbcontrol", "smbd", "close-share", name], timeout=15)
    except Exception as exc:
        logger.warning("Closing the connections to share %s failed: %s", name, exc)
        return f"Open connections to {name} could not be closed: {exc}"
    if not result.ok:
        detail = (result.stderr or result.stdout).strip() or f"exit status {result.returncode}"
        logger.warning("Closing the connections to share %s failed: %s", name, detail)
        return f"Open connections to {name} could not be closed: {detail}"
    return None


# --- shares and their folders (storage/pools.py) ------------------------------------


@dataclass(frozen=True)
class SharePathInfo:
    name: str
    path: str


def _database_share_paths() -> list[tuple[str, str]]:
    """(name, path) of the rows of smb_shares, served or not (read-only, stdlib sqlite3)."""
    db_path = getattr(settings, "db_path", None)
    if not isinstance(db_path, str) or db_path.strip() in ("", ":memory:"):
        return []
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(Path(os.path.abspath(db_path)).as_uri() + "?mode=ro", uri=True, timeout=5)
    try:
        try:
            rows = conn.execute("SELECT name, path FROM smb_shares ORDER BY id").fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc).lower():
                return []
            raise
    finally:
        conn.close()
    return [(name, path) for name, path in rows if isinstance(name, str) and isinstance(path, str) and path]


def _file_share_paths() -> list[tuple[str, str]]:
    """(name, path) of the sections of the live smb.conf that have a path."""
    data = read_conf_bytes()
    if data is None:
        return []
    parsed = smbconf.parse_smb_conf(data.decode("utf-8", "surrogateescape"))
    result = []
    for section in parsed.sections:
        path = smbconf.section_values(parsed, section).path
        if path:
            result.append((section.name, path))
    return result


def list_shares() -> list[SharePathInfo]:
    """Every share UNOS knows of, for storage/pools.py (spec 2.7). Synchronous.

    The union of the database rows and of the sections of the live smb.conf,
    so that neither a share that is not applied yet nor a stale file entry is
    missed: entries with the same name (case-insensitive) and the same path
    appear once, a name with two different paths twice. A missing database or
    file counts as empty; other errors are raised.
    """
    seen: set[tuple[str, str]] = set()
    shares = []
    for name, path in (*_database_share_paths(), *_file_share_paths()):
        key = (name.lower(), path)
        if key in seen:
            continue
        seen.add(key)
        shares.append(SharePathInfo(name=name, path=path))
    return shares
