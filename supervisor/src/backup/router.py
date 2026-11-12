"""Configuration backup and restore.

Backup archives are untrusted when they are restored: they can be uploaded,
and a stolen administrator session must not be able to turn a crafted
archive into arbitrary file writes on the host. Restore therefore:

* reads the archive in one pass over its gzip stream with a size limit
  (``_read_archive``): every member is checked as it comes, only an allowlist
  of regular files is taken (``_FILE_MEMBERS``), and of those only unos.db is
  written to /tmp, as it comes, with tarfile's ``data`` filter as a second line
  of defence; links, devices, absolute paths and ``..`` abort the restore,
  also after unos.db, before anything is changed;
* replaces the live database only after the restored copy passes
  ``PRAGMA integrity_check``, has a usable ``users`` table with an
  administrator and no triggers or views. The current database is copied
  first, and the copy into the live file uses SQLite's online backup API, so
  connections that are open in the running supervisor stay consistent;
* never installs or reads an smb.conf: shares and their access are rows of
  the database, and smb.conf is rendered from them; a backup carries none.
  Before the swap, the extracted database is prepared for SMB
  (shares/service.py and shares/accounts.py, ADR-0139): its SMB tables are
  checked, a database without them (of an older UNOS) is refused, and a
  database whose shares were never imported gets the shares of the live
  smb.conf imported after the restore (ADR-0319). The restore runs under the
  SMB lock, which also covers the post-restore step that renders smb.conf from
  the restored database.

An uploaded backup is checked by the same pass, which then writes nothing
(``_inspect_archive``).

Backups created or uploaded here are written under a temporary name in
BACKUP_DIR and get their final name only when complete
(``publish_backup_file``), with a hard link that never replaces an existing
file: backups made in the same second get different names, and the list does
not show them half-written.
"""

import asyncio
import contextlib
import errno
import gzip
import io
import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import stat
import tarfile
import tempfile
import threading
import time
import zlib
from collections.abc import Iterable, Iterator
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, Optional

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import database
from ..audit.changes import audit_change
from ..audit.schemas import AuditAction, AuditSeverity
from ..auth.utils import CallerSession, caller_session, forget_unstored_sessions, verify_token
from ..config import get_settings
from ..database import init_db
from ..request_limits import UPLOAD_OPENAPI, UploadSink, receive_upload
from ..security.validators import sanitize_error_message
from ..shares import accounts, service

settings = get_settings()
logger = logging.getLogger(__name__)

# Encryption constants
SALT_SIZE = 16
NONCE_SIZE = 12
KEY_SIZE = 32  # AES-256
BACKUP_KEY_SIZE = SALT_SIZE + KEY_SIZE  # new_backup_key
ITERATIONS = 100000
GCM_TAG_SIZE = 16  # AES-GCM appends a 16-byte authentication tag to the ciphertext

router = APIRouter(prefix="/backup", tags=["backup"])

BACKUP_DIR = Path("/data/backups")
BACKUP_VERSION = "1.0"

# Limits for archives that are uploaded or restored.
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024  # the .tar.gz / .tar.gz.enc file
MAX_TAR_BYTES = 640 * 1024 * 1024  # the decompressed tar stream
MAX_DB_BYTES = 512 * 1024 * 1024
MAX_CONFIG_BYTES = 1024 * 1024
MAX_MEMBERS = 256
PRE_RESTORE_COPIES = 5
DB_BUSY_TIMEOUT = 60
_CHUNK_SIZE = 1024 * 1024

PLAIN_EXTENSION = ".tar.gz"
ENCRYPTED_EXTENSION = ".tar.gz.enc"
# Only files with such names are listed and can be downloaded, restored or
# deleted. Temporary files start with a dot and end in .tmp.
BACKUP_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,200}\.tar\.gz(\.enc)?")
NAME_ATTEMPTS = 100  # backups with the same prefix in one second
# Names of backup_temp_file. Such a file older than STALE_TEMP_SECONDS was
# left by a create, upload or scheduled backup that was killed (no finally
# ran); it is removed, since it is not listed and so cannot be deleted in the UI.
_TEMP_NAME_PATTERN = re.compile(r"\.[a-z]+-[0-9a-f]{16}\.tmp")
STALE_TEMP_SECONDS = 24 * 3600
# Regular files a backup may contain, with their size limits. Other regular
# files and directories are ignored, such as the fstab and netplan files that
# older backups held (ADR-0345); any other member type aborts the restore.
_FILE_MEMBERS = {
    "manifest.json": MAX_CONFIG_BYTES,
    "unos.db": MAX_DB_BYTES,
    "scheduled_tasks.json": MAX_CONFIG_BYTES,
}
# Included in backups (those of backup tasks) but not restored automatically.
_NOT_RESTORED = ("scheduled_tasks.json",)
REQUIRED_USER_COLUMNS = frozenset({"id", "username", "hashed_password", "is_admin"})
# What tarfile raises for a malformed header in Python 3.11 besides TarError:
# OverflowError or ValueError for a size or offset too large to seek to or
# read (2**63 or more), MemoryError for data that claims more than the
# memory, ValueError and IndexError for broken GNU sparse data or PAX numbers,
# RecursionError for a long chain of extended headers.
_CORRUPT_TAR_ERRORS = (
    tarfile.TarError, OverflowError, MemoryError, ValueError, IndexError, RecursionError
)
# GNU long names and PAX extended headers. tarfile reads the data of one at
# once (its size can claim 2**62 bytes or more), parses its records one by
# one and keeps them with the member. A backup needs a few small ones (long
# names, PAX times): together they may hold at most MAX_CONFIG_BYTES. A
# global PAX header, whose records tarfile copies into every member that
# follows, is never in a backup (_BackupTarInfo).
_EXTENDED_HEADERS = frozenset(
    {tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK, tarfile.XHDTYPE, tarfile.SOLARIS_XHDTYPE}
)

_restore_lock = threading.Lock()
# A restore request is running (its locked part may outlive a cancelled request).
_restore_running = False

SMB_REPAIRED_AT_START = "SMB state is repaired at the next start"


class BackupValidationError(Exception):
    """The archive is not an acceptable UNOS backup (reported as HTTP 400)."""


class RestoreSpaceError(Exception):
    """The temporary files of a restore did not fit in /tmp (reported as HTTP 507)."""

    def __init__(self) -> None:
        super().__init__(
            "Not enough room in /tmp for the temporary files of the restore: nothing was restored"
        )


class BackupInfo(BaseModel):
    filename: str
    size: int
    created: str
    version: str
    encrypted: bool = False


class BackupConfig(BaseModel):
    """The body of POST /backup/create. Every backup holds the same files (_collect_backup_content):
    there are no options of what it holds (ADR-0345, ADR-0355)."""

    encrypt: bool = False
    passphrase: Optional[str] = None  # Required if encrypt=True


class DecryptRequest(BaseModel):
    # Optional so that the UI's empty body ({}) is accepted for unencrypted backups.
    passphrase: Optional[str] = None


def derive_key(passphrase: str, salt: bytes) -> bytes:
    """Derive encryption key from passphrase using PBKDF2."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=KEY_SIZE,
        salt=salt,
        iterations=ITERATIONS,
    )
    return kdf.derive(passphrase.encode())


def new_backup_key(passphrase: str) -> bytes:
    """A new salt and the key derived from ``passphrase`` with it: salt + key (BACKUP_KEY_SIZE bytes).

    Blocking (PBKDF2). What encrypt_with_key writes with it, decrypt_file opens
    with the passphrase: the salt is stored at the start of each file. A
    scheduled backup task keeps this instead of its passphrase (ADR-0325).
    """
    salt = secrets.token_bytes(SALT_SIZE)
    return salt + derive_key(passphrase, salt)


def encrypt_with_key(data: bytes | memoryview, backup_key: bytes) -> bytes:
    """Encrypt data using AES-256-GCM with a key of new_backup_key and a new nonce."""
    salt, key = backup_key[:SALT_SIZE], backup_key[SALT_SIZE:]
    nonce = secrets.token_bytes(NONCE_SIZE)
    # Format: salt + nonce + ciphertext
    return salt + nonce + AESGCM(key).encrypt(nonce, data, None)


def encrypt_file(data: bytes | memoryview, passphrase: str) -> bytes:
    """Encrypt data using AES-256-GCM, with a key derived from the passphrase and a new salt."""
    return encrypt_with_key(data, new_backup_key(passphrase))


def decrypt_file(encrypted_data: bytes, passphrase: str) -> bytes:
    """Decrypt data using AES-256-GCM."""
    salt = encrypted_data[:SALT_SIZE]
    nonce = encrypted_data[SALT_SIZE:SALT_SIZE + NONCE_SIZE]
    ciphertext = encrypted_data[SALT_SIZE + NONCE_SIZE:]
    key = derive_key(passphrase, salt)
    aesgcm = AESGCM(key)
    return aesgcm.decrypt(nonce, ciphertext, None)


def ensure_backup_dir():
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)


def _backup_path(filename: str) -> Path:
    """Return the path of a backup file; the name must be a plain backup file name."""
    if not BACKUP_NAME_PATTERN.fullmatch(filename):
        raise HTTPException(400, "Invalid filename")
    return BACKUP_DIR / filename


def _is_regular_file(path: Path) -> bool:
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except FileNotFoundError:
        return False


# --- SQLite helpers -------------------------------------------------------------


def _live_db_path() -> Path | None:
    if not settings.db_path or settings.db_path == ":memory:":
        return None
    return Path(settings.db_path)


def copy_database(src: Path, dest: Path) -> None:
    """Write a consistent copy of a (possibly in-use) SQLite database to ``dest``.

    The live database writes ahead (ADR-0306): the backup API copies the commits still in its
    -wal as well, and the header that marks the file for the write-ahead log. The copy is
    switched back to the rollback journal, so it is one file on its own, as _verify_database
    requires of a backup's unos.db.
    """
    source = sqlite3.connect(str(src), timeout=30)
    try:
        target = sqlite3.connect(str(dest))
        try:
            source.backup(target)
            target.execute("PRAGMA journal_mode = DELETE")
        finally:
            target.close()
    finally:
        source.close()


def _open_readonly(path: Path) -> sqlite3.Connection:
    # immutable=1: no locking and no journal/-shm files next to the extracted copy.
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro&immutable=1", uri=True)


def _verify_database(path: Path) -> None:
    """Check that ``path`` is a healthy UNOS database before it replaces the live one."""
    with open(path, "rb") as f:
        header = f.read(100)
    if len(header) < 100 or not header.startswith(b"SQLite format 3\x00"):
        raise BackupValidationError("unos.db in the backup is not an SQLite database")
    if header[18] != 1 or header[19] != 1:
        raise BackupValidationError("unos.db in the backup uses an unsupported journal mode")

    try:
        conn = _open_readonly(path)
    except sqlite3.Error as e:
        raise BackupValidationError(f"unos.db in the backup cannot be opened: {e}") from e
    try:
        conn.execute("PRAGMA trusted_schema = OFF")
        if conn.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise BackupValidationError("unos.db in the backup failed the integrity check")
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if "users" not in tables:
            raise BackupValidationError("unos.db in the backup has no users table")
        columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
        missing = REQUIRED_USER_COLUMNS - columns
        if missing:
            raise BackupValidationError(
                f"The users table in the backup is missing columns: {', '.join(sorted(missing))}"
            )
        admins = conn.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1").fetchone()[0]
        if not admins:
            raise BackupValidationError("unos.db in the backup has no administrator account")
        # UNOS creates neither. A trigger would run inside the SMB preparation
        # of the extracted file (ADR-0139), before anything was checked.
        if conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type IN ('trigger', 'view')").fetchone()[0]:
            raise BackupValidationError("unos.db in the backup contains triggers or views; UNOS databases have none")
    except sqlite3.Error as e:
        raise BackupValidationError(f"unos.db in the backup cannot be read: {e}") from e
    finally:
        conn.close()


def _page_size(path: Path) -> int | None:
    """The page size in the header of the SQLite database ``path``; None for a file without one."""
    with open(path, "rb") as f:
        header = f.read(18)
    if len(header) < 18 or not header.startswith(b"SQLite format 3\x00"):
        return None
    size = int.from_bytes(header[16:18], "big")
    return 65536 if size == 1 else size


def _verify_page_size(restored: Path, db_path: Path) -> None:
    """The live database writes ahead (ADR-0306), and SQLite's backup API cannot give such a
    database another page size: a backup with pages of another size is refused before anything
    changes. A live database without a header yet takes any."""
    live = _page_size(db_path) if db_path.exists() else None
    size = _page_size(restored)
    if live is not None and size != live:
        raise BackupValidationError(
            f"unos.db in the backup has pages of {size} bytes, and the database here pages of {live} bytes"
        )


def _keep_copy_of_database(db_path: Path) -> Path | None:
    """Copy the current database next to it before a restore; keep the newest copies."""
    if not db_path.exists():
        return None
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    copy_path = db_path.with_name(f"{db_path.name}.pre-restore-{timestamp}")
    try:
        copy_database(db_path, copy_path)
    except BaseException:
        # No copy to keep: SQLite leaves an empty or partial file when the disk is full.
        copy_path.unlink(missing_ok=True)
        raise
    copies = sorted(db_path.parent.glob(f"{db_path.name}.pre-restore-*"))
    for old in copies[:-PRE_RESTORE_COPIES]:
        old.unlink(missing_ok=True)
    return copy_path


def _replace_database(restored: Path, db_path: Path) -> None:
    """Copy the verified database into the live one with SQLite's backup API.

    Unlike replacing the file, this works while the supervisor has the
    database open: the copy runs as one write transaction and the other
    connections see the new content afterwards.
    """
    deadline = time.monotonic() + DB_BUSY_TIMEOUT

    def give_up_when_busy_too_long(status: int, remaining: int, total: int) -> None:
        if time.monotonic() > deadline:
            raise TimeoutError("The database stayed busy; the restore was aborted")

    source = _open_readonly(restored)
    try:
        target = sqlite3.connect(str(db_path), timeout=30)
        try:
            source.backup(target, progress=give_up_when_busy_too_long)
        finally:
            target.close()
    finally:
        source.close()


# --- archive handling ---------------------------------------------------------


def _open_archive(backup_path: Path, passphrase: str | None) -> BinaryIO:
    """Open the gzip stream of a backup, decrypting it first if a passphrase is given."""
    if backup_path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise BackupValidationError("Backup file is too large")
    if passphrase is None:
        return open(backup_path, "rb")
    data = backup_path.read_bytes()
    if len(data) <= SALT_SIZE + NONCE_SIZE:
        raise BackupValidationError("Invalid passphrase or corrupted backup")
    try:
        return io.BytesIO(decrypt_file(data, passphrase))
    except InvalidTag:
        raise BackupValidationError("Invalid passphrase or corrupted backup") from None


def _member_name(member: tarfile.TarInfo) -> str:
    """Normalize a member name; refuse absolute paths and ``..``."""
    name = member.name
    if not name or "\x00" in name or "\\" in name or name.startswith("/"):
        raise BackupValidationError(f"Unsafe path in backup archive: {name[:100]!r}")
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if ".." in parts or (not parts and not member.isdir()):
        raise BackupValidationError(f"Unsafe path in backup archive: {name[:100]!r}")
    return "/".join(parts)


def _scan_members(tar: Iterable[tarfile.TarInfo]) -> dict[str, tarfile.TarInfo]:
    """Validate every member of a tar stream (_stream_members) and return the files of _FILE_MEMBERS, by normalized name."""
    wanted: dict[str, tarfile.TarInfo] = {}
    seen: set[str] = set()
    try:
        for count, member in enumerate(tar, start=1):
            if count > MAX_MEMBERS:
                raise BackupValidationError("Backup archive has too many entries")
            name = _member_name(member)
            if name in seen:
                raise BackupValidationError(f"Duplicate entry in backup archive: {name}")
            seen.add(name)
            if member.isdir():
                continue
            if not member.isreg():
                raise BackupValidationError(
                    f"Backup archive contains a link or special file: {name[:100]!r}"
                )
            limit = _FILE_MEMBERS.get(name)
            if limit is None:
                continue  # not part of a UNOS backup: ignored
            if member.size > limit:
                raise BackupValidationError(f"{name} in the backup archive is too large")
            if member.size < 0:  # a base-256 size field can be negative
                raise BackupValidationError("Invalid backup: corrupt archive")
            wanted[name] = member
    except _CORRUPT_TAR_ERRORS as e:
        raise BackupValidationError("Invalid backup: corrupt archive") from e
    if "manifest.json" not in wanted:
        raise BackupValidationError("Invalid backup: missing manifest")
    return wanted


def _parse_manifest(data: bytes) -> dict:
    try:
        manifest = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError) as e:
        raise BackupValidationError("Invalid backup: manifest.json is not valid JSON") from e
    if not isinstance(manifest, dict):
        raise BackupValidationError("Invalid backup: manifest.json must be a JSON object")
    return manifest


class _BackupTarInfo(tarfile.TarInfo):
    """A member of a backup archive as tarfile reads it, with the limits of _EXTENDED_HEADERS.

    tarfile calls _proc_member for every header it reads, before it reads
    the data of an extended header (tarfile's comments name it as the
    method to override).

    Sparse files are refused in every GNU format: UNOS never writes them
    (tarfile cannot), and tarfile builds the map of one before the member
    can be checked, from the extension blocks of the old format or from the
    start of the data in format 1.0, at a cost in memory and time that grows
    with the archive (124 MiB and 4 s on the device for a 166 KiB upload).
    Formats 0.0 and 0.1 keep their map in PAX records, within the 1 MiB of
    those.
    GNU.sparse records without a map are refused too: tarfile takes
    GNU.sparse.realsize as the size of the member but finds the next header
    after the data stored, so the member would reach into the ones after it.
    """

    def _proc_member(self, tar: "_BackupTarFile") -> tarfile.TarInfo:
        if self.type in (tarfile.XGLTYPE, tarfile.GNUTYPE_SPARSE):
            raise BackupValidationError("Invalid backup: corrupt archive")
        if self.type in _EXTENDED_HEADERS:
            tar.extended_bytes += max(self.size, 0)
            if tar.extended_bytes > MAX_CONFIG_BYTES:
                raise BackupValidationError("Invalid backup: corrupt archive")
        member = super()._proc_member(tar)
        # A map from PAX records (format 0.0 or 0.1), or GNU.sparse records without one.
        if member.sparse is not None or any(key.startswith("GNU.sparse.") for key in member.pax_headers):
            raise BackupValidationError("Invalid backup: corrupt archive")
        return member

    def _proc_gnusparse_10(self, *args: object, **kwargs: object) -> None:  # whatever tarfile passes
        raise BackupValidationError("Invalid backup: corrupt archive")  # before its map is read


class _BackupTarFile(tarfile.TarFile):
    """A backup archive being read: its members are _BackupTarInfo."""

    tarinfo = _BackupTarInfo
    extended_bytes = 0  # the data of the extended headers read so far


def _restore_filter(member: tarfile.TarInfo, dest_path: str) -> tarfile.TarInfo:
    """tarfile's data filter, without the modification time of the archive.

    The extracted files are temporary and their times are not needed; a time
    out of range (base-256 or PAX "nan") made os.utime raise OverflowError
    or ValueError.
    """
    return tarfile.data_filter(member, dest_path).replace(mtime=None, deep=False)


def _extract(tar: tarfile.TarFile, members: dict[str, tarfile.TarInfo], dest: Path) -> None:
    """Extract ``members`` into ``dest`` through _restore_filter, whose refusals are BackupValidationError.

    In a tar stream, only the current member can be extracted; its data cut short is
    tarfile's ReadError, for the caller (_scan_members: a corrupt archive).
    """
    if not hasattr(tarfile, "data_filter"):
        raise RuntimeError("Restoring backups requires Python 3.11.4 or newer (tarfile filters)")
    try:
        tar.extractall(dest, members=list(members.values()), filter=_restore_filter)
    except tarfile.FilterError as e:
        raise BackupValidationError(f"Invalid backup: {e}") from e


class _DecompressedStream:
    """The decompressed data of a gzip stream, as tarfile reads it in stream mode.

    read() refuses a decompression bomb (more than MAX_TAR_BYTES in all) and
    reports damaged gzip data (header, deflate data, CRC or length, a stream
    cut short, garbage after it) as a corrupt archive.
    """

    def __init__(self, gz: gzip.GzipFile) -> None:
        self._gz = gz
        self.size = 0

    def read(self, size: int) -> bytes:
        try:
            data = self._gz.read(size)
        except (gzip.BadGzipFile, EOFError, zlib.error) as e:
            raise BackupValidationError("Invalid backup: corrupt archive") from e
        self.size += len(data)
        if self.size > MAX_TAR_BYTES:
            raise BackupValidationError("Backup archive is too large when decompressed")
        return data

    def read_to_end(self) -> None:
        while self.read(_CHUNK_SIZE):
            pass


class _DataPastTheLimit(BackupValidationError):
    """A member's data would end past MAX_TAR_BYTES (_stream_members)."""

    def __init__(self) -> None:
        super().__init__("Backup archive is too large when decompressed")


def _stream_members(
    tar: tarfile.TarFile, stream: _DecompressedStream, manifest: list[bytes], dest: Path | None
) -> Iterator[tarfile.TarInfo]:
    """The members of a tar stream, for _scan_members; reads manifest.json, and extracts unos.db into ``dest``, on the way.

    A member is handed on before the next one is read, so _scan_members
    checks it first. tarfile skips the data of a member by reading it when
    the next one is asked for, up to tar.offset, where the next header
    starts: a member whose data would end past MAX_TAR_BYTES is refused
    before that (its size can be 2**63 or more).
    A member's data can only be read while it is the current member: it is
    read once _scan_members has accepted it, which it does only for a
    regular file of _FILE_MEMBERS within its limit. manifest.json is kept in
    memory, and unos.db extracted into ``dest`` when it is not None (a
    restore); scheduled_tasks.json, which a restore does not restore, is only
    noted. After the last member the stream is read to its end, so that the
    size limit and the checks of gzip cover all of it.
    """
    for member in tar:
        yield member
        # _scan_members did not refuse the member and asks for the next one.
        if tar.offset > MAX_TAR_BYTES:
            raise _DataPastTheLimit()
        if not member.isreg():
            continue
        name = _member_name(member)
        if name == "manifest.json":
            with tar.extractfile(member) as data:
                manifest.append(data.read(MAX_CONFIG_BYTES + 1))
        elif name == "unos.db" and dest is not None:
            _extract(tar, {name: member}, dest)
    stream.read_to_end()


def _read_archive(source: BinaryIO, dest: Path | None) -> tuple[dict, dict[str, tarfile.TarInfo]]:
    """Validate a backup in one pass over its gzip stream ``source``; return its manifest and its files of _FILE_MEMBERS.

    tarfile reads the decompressed data as a stream (mode "r|"),
    _scan_members checks each member as it comes, and manifest.json is kept
    in memory. The check of an upload (``dest`` None) writes nothing, in /tmp
    or anywhere. A restore (``dest`` its folder in /tmp) extracts unos.db
    there as it comes, the one file it writes (ADR-0364); a member refused
    after it refuses the backup as well, and the caller removes the folder.

    A member whose data would end past MAX_TAR_BYTES is refused by the
    upload at once, as too large (ADR-0345). A restore first reads the rest
    of the stream, and answers as it did when it decompressed the whole
    archive before reading it: too large when the stream is, otherwise a
    corrupt archive, whose data ends before the header says.
    """
    manifest: list[bytes] = []
    with gzip.GzipFile(fileobj=source, mode="rb") as gz:
        stream = _DecompressedStream(gz)
        try:
            with _BackupTarFile.open(fileobj=stream, mode="r|") as tar:
                members = _scan_members(_stream_members(tar, stream, manifest, dest))
        except _DataPastTheLimit as e:
            if dest is None:
                raise
            stream.read_to_end()
            raise BackupValidationError("Invalid backup: corrupt archive") from e
        except _CORRUPT_TAR_ERRORS as e:  # the first header is read by tarfile.open
            raise BackupValidationError("Invalid backup: corrupt archive") from e
    if not manifest:  # _scan_members refuses a backup without one
        raise BackupValidationError("Invalid backup: missing manifest")
    return _parse_manifest(manifest[0]), members


def _inspect_archive(archive: Path) -> dict:
    """Validate an unencrypted backup without writing anything (_read_archive); return its manifest."""
    with _open_archive(archive, None) as source:
        return _read_archive(source, None)[0]


def backup_temp_file(directory: Path, kind: str) -> Path:
    """Path for a backup file being written; its name is never listed (see BACKUP_NAME_PATTERN)."""
    return directory / f".{kind}-{secrets.token_hex(8)}.tmp"


def remove_stale_temp_files(directory: Path) -> None:
    """Remove the temporary files of killed creates, uploads and scheduled backups (see STALE_TEMP_SECONDS)."""
    cutoff = time.time() - STALE_TEMP_SECONDS
    for path in directory.iterdir():
        if not _TEMP_NAME_PATTERN.fullmatch(path.name):
            continue
        try:
            st = os.lstat(path)
            if stat.S_ISREG(st.st_mode) and st.st_mtime < cutoff:
                path.unlink()
        except OSError:
            continue  # gone meanwhile, or not removable: never fail the backup for it


def publish_backup_file(tmp: Path, prefix: str, extension: str, when: datetime | None = None) -> str:
    """Give the finished file ``tmp`` its backup name in the same directory; return the name.

    The name is ``<prefix>_<YYYYmmdd_HHMMSS><extension>``, of ``when`` (default:
    now), with ``_1``, ``_2``, ... before the extension when it is taken. os.link
    never replaces an existing file, and the file appears complete under the new
    name (also after a power loss: it is written to disk first). The caller
    removes ``tmp``.
    """
    fd = os.open(tmp, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    timestamp = (when or datetime.now()).strftime("%Y%m%d_%H%M%S")
    for attempt in range(NAME_ATTEMPTS):
        number = f"_{attempt}" if attempt else ""
        name = f"{prefix}_{timestamp}{number}{extension}"
        try:
            os.link(tmp, tmp.parent / name)
            return name
        except FileExistsError:
            continue
    raise RuntimeError("Could not choose a file name for the backup")


def _check_encrypted_archive(path: Path) -> None:
    """Check the length of an encrypted backup.

    The file is salt, nonce and AES-GCM ciphertext with its tag (encrypt_file):
    there is no header to recognise. _refuse_plain_archive refuses gzip data;
    restore validates the content after decrypting it.
    """
    if path.stat().st_size <= SALT_SIZE + NONCE_SIZE + GCM_TAG_SIZE:
        raise BackupValidationError("Invalid encrypted backup: the file is too short")


def _is_gzip(path: Path) -> bool:
    """Whether the file is gzip data to its end (zero padding allowed, as gzip allows it).

    Read in one pass that writes nothing; data that decompresses to more than
    MAX_TAR_BYTES counts as gzip without being read further.
    """
    total = 0
    with open(path, "rb") as source, gzip.GzipFile(fileobj=source, mode="rb") as gz:
        try:
            while chunk := gz.read(_CHUNK_SIZE):
                total += len(chunk)
                if total > MAX_TAR_BYTES:
                    return True
        except (gzip.BadGzipFile, EOFError, zlib.error):
            return False
    return True


def _refuse_plain_archive(path: Path) -> None:
    """Refuse gzip data uploaded under an encrypted name (.tar.gz.enc), ADR-0345.

    Stored as encrypted it could never be restored: restore decrypts the file
    before it reads a gzip stream. What encrypt_file writes reads as gzip data
    only by a chance too small to count: its random salt would have to start
    with gzip's header, and the rest decode as deflate data that ends with its
    CRC-32 and length. A plain backup is refused with the extension to use.
    """
    try:
        _inspect_archive(path)
    except BackupValidationError:
        if _is_gzip(path):
            raise BackupValidationError(
                "This file is not an encrypted backup: it is an unencrypted gzip file"
            ) from None
        return  # kept as encrypted, its content is checked when it is restored
    raise BackupValidationError("This backup is not encrypted: upload it as .tar.gz")


class _BackupUpload(UploadSink):
    """An uploaded backup, for request_limits.receive_upload.

    It arrives in backup_temp_file(BACKUP_DIR, "upload"), at most
    MAX_ARCHIVE_BYTES (refused as soon as it grows over that), is validated
    like a restore when it is complete, and then gets a new uploaded_* name
    (publish_backup_file). discard removes the temporary file.
    """

    def __init__(self) -> None:
        self.max_bytes = MAX_ARCHIVE_BYTES
        self.encrypted = False
        self.tmp: Path | None = None

    def too_large(self) -> Exception:
        return BackupValidationError("Backup file is too large")

    def prepare(self) -> None:
        ensure_backup_dir()

    def open(self, filename: str) -> int:
        # The client's file name only tells the format; the file is stored under a new name.
        if filename.endswith(ENCRYPTED_EXTENSION):
            self.encrypted = True
        elif filename.endswith(PLAIN_EXTENSION):
            self.encrypted = False
        else:
            raise HTTPException(400, "Invalid file format. Must be .tar.gz or .tar.gz.enc")
        remove_stale_temp_files(BACKUP_DIR)
        tmp = backup_temp_file(BACKUP_DIR, "upload")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o666)
        self.tmp = tmp
        return fd

    def finish(self) -> tuple[str, dict | None, int]:
        """Validate the backup and publish it: its name, its manifest and its size.

        Encrypted backups cannot be read without their passphrase and have no
        manifest here.
        """
        if self.encrypted:
            _check_encrypted_archive(self.tmp)
            _refuse_plain_archive(self.tmp)
            manifest = None
        else:
            manifest = _inspect_archive(self.tmp)
        extension = ENCRYPTED_EXTENSION if self.encrypted else PLAIN_EXTENSION
        name = publish_backup_file(self.tmp, "uploaded", extension)
        return name, manifest, (BACKUP_DIR / name).stat().st_size

    def discard(self) -> None:
        if self.tmp is not None:
            self.tmp.unlink(missing_ok=True)


def _prepare_smb(db_file: Path, db_path: Path) -> list[str]:
    """The SMB rules of a restore (ADR-0139) on the extracted database, before it replaces the live one.

    The SMB tables are checked (BackupValidationError), the SMB
    accounts of the live database and its record of handed-out uids are
    read (ADR-0353), and then one transaction on the
    extracted file applies the share rules (shares/service.py) and the
    account rules (shares/accounts.py). Any failure rolls it back and the
    restore fails: the live database never holds an unproven SMB state.
    Returns the messages for the restore result.
    """
    service.check_smb_tables(db_file)
    snapshot = accounts.snapshot_for_restore(db_path)
    conn = sqlite3.connect(str(db_file), isolation_level=None)
    try:
        conn.execute("PRAGMA trusted_schema = OFF")
        # The rollback journal of this temporary copy stays in memory, not beside it in /tmp
        # (ADR-0364); a crash loses the copy anyway.
        conn.execute("PRAGMA journal_mode = MEMORY")
        conn.execute("BEGIN IMMEDIATE")
        try:
            authoritative = service.restored_smb_authoritative(conn)
            guest_shares = service.prepare_restored_shares(conn, authoritative)
            messages = accounts.prepare_restored_accounts(conn, snapshot, authoritative)
            # The backup's sessions are not this installation's: a restore ends every session
            # (ADR-0329). An older backup has no such table: init_db makes it after the swap.
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'user_sessions'").fetchone():
                conn.execute("DELETE FROM user_sessions")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    except service.PoolListUnavailable as e:
        raise HTTPException(503, f"{sanitize_error_message(e)}; nothing was restored") from e
    finally:
        conn.close()
    if guest_shares:
        logger.info("The restored database keeps guest access for: %s", ", ".join(guest_shares))
    return list(messages)


class RestoreInProgress(Exception):
    """Another restore is running."""


def _restore(backup_path: Path, passphrase: str | None) -> dict:
    """Validate and apply a backup (blocking; runs in a worker thread).

    The lock is taken here, in the worker, so that it is held for as long as
    the restore really runs even if the request is cancelled.
    """
    if not _restore_lock.acquire(blocking=False):
        raise RestoreInProgress()
    try:
        return _restore_locked(backup_path, passphrase)
    finally:
        _restore_lock.release()


@contextlib.contextmanager
def _temporary_files_of_restore() -> Iterator[None]:
    """Report a full /tmp as RestoreSpaceError while a restore writes its temporary files.

    Only /tmp is written then (a tmpfs, in RAM, in the container): the backup
    is read, and nothing else is changed before all of it is checked. A
    full disk later, where the database is copied and replaced, keeps its
    own answer.
    """
    try:
        yield
    except OSError as e:
        if e.errno in (errno.ENOSPC, errno.EDQUOT):
            raise RestoreSpaceError() from e
        raise


def _restore_locked(backup_path: Path, passphrase: str | None) -> dict:
    warnings: list[str] = []
    restored: list[str] = []
    previous_db: Path | None = None

    with _temporary_files_of_restore():
        workdir = tempfile.TemporaryDirectory(prefix="unos-restore-")
    with workdir:
        extract_dir = Path(workdir.name)
        with _temporary_files_of_restore(), _open_archive(backup_path, passphrase) as source:
            manifest, members = _read_archive(source, extract_dir)

        # Validate everything before changing anything.
        db_path = _live_db_path()
        db_file = extract_dir / "unos.db" if "unos.db" in members else None
        if db_file is not None:
            if db_path is None:
                warnings.append("The database was not restored: no database file is configured.")
                db_file = None
            else:
                _verify_database(db_file)
                _verify_page_size(db_file, db_path)

        if db_file is not None:
            warnings.extend(_prepare_smb(db_file, db_path))

        if db_file is not None:
            previous_db = _keep_copy_of_database(db_path)
            _replace_database(db_file, db_path)
            forget_unstored_sessions()
            restored.append("database")

        for name in _NOT_RESTORED:
            if name in members:
                warnings.append(f"{name} is included in the backup but is not restored automatically.")

    return {
        "status": "success",
        "restored": restored,
        "manifest": manifest,
        "message": _restore_message(restored, warnings),
        "warnings": warnings,
        "previous_database": str(previous_db) if previous_db else None,
    }


def _restore_message(restored: list[str], warnings: list[str]) -> str:
    message = f"Backup restored: {', '.join(restored)}." if restored else "Nothing was restored."
    if warnings:
        message += " " + " ".join(warnings)
    return message


async def _after_database_restore(result: dict) -> None:
    """The post-restore step (ADR-0138), under smb_lock: missing tables, then the SMB reconcile.

    A failure is the warning SMB_REPAIRED_AT_START: the startup reconcile does
    the same work. When smb.conf was rendered from the restored database,
    "smb_config" is added to the restored items.
    """
    warnings: list[str] = []
    applied = False
    try:
        await init_db()  # older backups may lack tables added since
    except Exception:
        logger.exception("Could not create missing tables after the database restore")
        warnings.append(SMB_REPAIRED_AT_START)
    else:
        try:
            async with database.async_session() as db:
                warnings, applied = await service.after_database_restore_locked(db)
        except Exception:
            logger.exception("The SMB state could not be updated after the database restore")
            warnings, applied = [SMB_REPAIRED_AT_START], False
    result["warnings"].extend(warnings)
    if applied:
        result["restored"].append("smb_config")
    result["message"] = _restore_message(result["restored"], result["warnings"])


def _keep_caller_session(caller: CallerSession) -> None:
    """After a restore, which ended every session: the session of the administrator who restored
    goes on when the restored database has them as an administrator, under the same id and name
    (ADR-0329), so that they see the result. Otherwise, or when it cannot be stored, they sign in again."""
    try:
        with contextlib.closing(sqlite3.connect(_live_db_path(), timeout=DB_BUSY_TIMEOUT)) as conn:
            same = conn.execute("SELECT 1 FROM users WHERE id = ? AND username = ? AND is_admin",
                                (caller.user_id, caller.username)).fetchone()
            if same:
                conn.execute("INSERT OR IGNORE INTO user_sessions (id, user_id, expires_at) VALUES (?, ?, ?)",
                             (caller.id, caller.user_id, caller.expires_at.strftime("%Y-%m-%d %H:%M:%S.%f")))
                conn.commit()
    except sqlite3.Error:
        logger.warning("Could not keep the session of the restore's administrator", exc_info=True)


async def _restore_and_reconcile(backup_path: Path, passphrase: str | None, request: Request, username: str,
                                 caller: CallerSession | None = None) -> dict:
    """The restore and its post-restore step, holding smb_lock (a restore is an SMB state change),
    and the audit entry of a restore that restored something (ADR-0351): written here, in the
    restore's own task, so that a request cancelled meanwhile does not lose it."""
    global _restore_running
    try:
        async with service.smb_lock():
            result = await asyncio.to_thread(_restore, backup_path, passphrase)
            if "database" in result["restored"]:
                await _after_database_restore(result)
                if caller is not None:
                    await asyncio.to_thread(_keep_caller_session, caller)
        if result["restored"]:
            await audit_change(
                AuditAction.BACKUP_RESTORED, request, username,
                resource_type="backup", resource_id=backup_path.name,
                details=f"Restored {', '.join(result['restored'])}", severity=AuditSeverity.WARNING,
            )
        return result
    finally:
        _restore_running = False


# --- endpoints ----------------------------------------------------------------


def _manifest_version(path: Path) -> str:
    """The version in the manifest of an unencrypted backup, or "unknown".

    Read with the limits of a restore (_BackupTarFile, at most MAX_MEMBERS
    entries within MAX_TAR_BYTES, a manifest of at most MAX_CONFIG_BYTES):
    the list reads every backup each time it is shown, and one stored by an
    earlier version can hold what its upload check let through, such as a
    sparse file or a member of any size before the manifest. The
    manifest has the name the restore gives it (_member_name): also
    ./manifest.json, as tar -C <folder> . stores it.
    """
    try:
        with _BackupTarFile.open(path, "r:gz") as tar:
            for count, member in enumerate(tar, start=1):
                if count > MAX_MEMBERS:
                    break
                if member.isreg() and _member_name(member) == "manifest.json":
                    f = tar.extractfile(member)
                    data = f.read(MAX_CONFIG_BYTES + 1) if f else b""
                    if len(data) > MAX_CONFIG_BYTES:
                        break
                    return str(json.loads(data).get("version", "unknown"))
                if tar.offset > MAX_TAR_BYTES:  # the next header lies past what a restore decompresses
                    break
    except Exception:
        pass
    return "unknown"


def _list_backups() -> list[BackupInfo]:
    """The backups that can be downloaded, restored and deleted, newest first.

    That is every regular file with a backup name. Create and upload write
    under a temporary name (backup_temp_file), which is not listed.
    """
    ensure_backup_dir()
    found: list[tuple[float, str, os.stat_result]] = []
    for path in BACKUP_DIR.iterdir():
        if not BACKUP_NAME_PATTERN.fullmatch(path.name):
            continue
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            continue  # deleted meanwhile
        if stat.S_ISREG(st.st_mode):  # links are not used (_is_regular_file)
            found.append((st.st_mtime, path.name, st))
    found.sort(key=lambda item: (item[0], item[1]), reverse=True)

    backups = []
    for mtime, name, st in found:
        encrypted = name.endswith(ENCRYPTED_EXTENSION)
        backups.append(BackupInfo(
            filename=name,
            size=st.st_size,
            created=datetime.fromtimestamp(mtime).isoformat(),
            version="unknown" if encrypted else _manifest_version(BACKUP_DIR / name),
            encrypted=encrypted,
        ))
    return backups


@router.get("/list")
async def list_backups(_: dict = Depends(verify_token)):
    """List all available backups (both encrypted and unencrypted)."""
    return {"backups": await asyncio.to_thread(_list_backups)}


def _collect_backup_content(content: Path, *, encrypted: bool) -> None:
    """Put the files the backup contains into the directory ``content``: the manifest and the database.

    Nothing of /etc: the container's fstab and netplan files describe nothing of
    the host, whose fstab (with the pool entries) is not mounted into the
    container (ADR-0345). The manifest says whether the backup is ``encrypted``,
    and names no options (ADR-0355).
    """
    manifest = {
        "version": BACKUP_VERSION,
        "created": datetime.now().isoformat(),
        "hostname": os.uname().nodename,
        "encrypted": encrypted,
    }

    with open(content / "manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)

    # Backup database: use SQLite's backup API, a plain file copy of a
    # database in use can be torn and would then fail the restore check.
    db_path = _live_db_path()
    if db_path is not None and db_path.exists():
        copy_database(db_path, content / "unos.db")


def _write_archive(content: Path, out: BinaryIO) -> None:
    """Write the files in ``content`` to ``out`` as a .tar.gz archive."""
    with tarfile.open(fileobj=out, mode="w:gz") as tar:
        for item in sorted(content.iterdir()):
            tar.add(item, arcname=item.name)


def configuration_archive() -> bytes:
    """The archive that Create Backup makes (POST /backup/create without encryption), built in memory.

    The off-device copy sends it to its repository, which restic encrypts (ADR-0337). Blocking (the
    copy of the database, the archive): call it in a worker thread. Its files are collected in TMPDIR.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        content = Path(tmpdir) / "content"
        content.mkdir()
        _collect_backup_content(content, encrypted=False)
        out = io.BytesIO()
        _write_archive(content, out)
    return out.getvalue()


def check_archive(path: Path) -> dict:
    """Check the unencrypted backup at ``path`` as an upload is checked (_inspect_archive), writing
    nothing; return its manifest. BackupValidationError for an archive that a restore would refuse."""
    return _inspect_archive(path)


def store_backup(source: Path, prefix: str, when: datetime) -> BackupInfo:
    """Copy the checked unencrypted backup ``source`` into BACKUP_DIR as ``<prefix>_<when>.tar.gz``.

    Written under a temporary name and published complete (publish_backup_file), as an upload is.
    Blocking: call it in a worker thread.
    """
    ensure_backup_dir()
    remove_stale_temp_files(BACKUP_DIR)
    tmp = backup_temp_file(BACKUP_DIR, prefix)
    try:
        with open(source, "rb") as src, open(tmp, "xb") as out:
            shutil.copyfileobj(src, out, _CHUNK_SIZE)
        name = publish_backup_file(tmp, prefix, PLAIN_EXTENSION, when)
    finally:
        tmp.unlink(missing_ok=True)
    st = os.stat(BACKUP_DIR / name)
    return BackupInfo(
        filename=name,
        size=st.st_size,
        created=datetime.fromtimestamp(st.st_mtime).isoformat(),
        version=_manifest_version(BACKUP_DIR / name),
        encrypted=False,
    )


def _create_backup(config: BackupConfig) -> dict:
    ensure_backup_dir()
    remove_stale_temp_files(BACKUP_DIR)
    extension = ENCRYPTED_EXTENSION if config.encrypt else PLAIN_EXTENSION
    tmp = backup_temp_file(BACKUP_DIR, "create")
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            content = Path(tmpdir) / "content"
            content.mkdir()
            _collect_backup_content(content, encrypted=config.encrypt)
            with open(tmp, "xb") as out:
                if config.encrypt:
                    plain = io.BytesIO()
                    _write_archive(content, plain)
                    out.write(encrypt_file(plain.getbuffer(), config.passphrase))
                else:
                    _write_archive(content, out)
        backup_name = publish_backup_file(tmp, "unos_backup", extension)
    finally:
        tmp.unlink(missing_ok=True)

    return {
        "status": "success",
        "encrypted": config.encrypt,
        "backup": BackupInfo(
            filename=backup_name,
            size=(BACKUP_DIR / backup_name).stat().st_size,
            created=datetime.now().isoformat(),
            version=BACKUP_VERSION,
            encrypted=config.encrypt,
        )
    }


@router.post("/create")
async def create_backup(
    request: Request,
    config: BackupConfig = BackupConfig(),
    user=Depends(verify_token),
):
    """Create a new backup of UNOS configuration. Optionally encrypted with AES-256."""
    # Validate encryption config
    if config.encrypt and not config.passphrase:
        raise HTTPException(400, "Passphrase required for encrypted backup")

    try:
        result = await asyncio.to_thread(_create_backup, config)
    except Exception as e:
        raise HTTPException(500, f"Failed to create backup: {e}") from e
    await audit_change(
        AuditAction.BACKUP_CREATED, request, user.username, resource_type="backup",
        resource_id=result["backup"].filename,
        details="Encrypted backup created" if config.encrypt else "Backup created",
    )
    return result


@router.get("/download/{filename}")
async def download_backup(filename: str, _: dict = Depends(verify_token)):
    """Download a backup file."""
    ensure_backup_dir()
    backup_path = _backup_path(filename)
    if not _is_regular_file(backup_path):
        raise HTTPException(404, "Backup not found")

    return FileResponse(
        path=backup_path,
        filename=filename,
        media_type="application/gzip"
    )


@router.post("/upload", openapi_extra=UPLOAD_OPENAPI)
async def upload_backup(request: Request, user=Depends(verify_token)):
    """Upload a backup file (form field "file"). It is validated like a restore, but not applied.

    Encrypted backups (.tar.gz.enc) cannot be read without their passphrase:
    they are validated when they are restored. The form is read only here,
    after the credentials were checked; the extension is checked as soon as
    the file name arrives, and the file goes straight into a temporary file
    in the backup directory as it arrives, not through /tmp
    (request_limits.receive_upload, _BackupUpload). The check of the
    complete file (_inspect_archive: a plain backup, or a .tar.gz.enc upload,
    which is refused if it is gzip data, _refuse_plain_archive) reads it as
    a stream, and writes nothing: neither the decompressed archive nor its
    files are stored anywhere.
    """
    upload = _BackupUpload()
    try:
        safe_name, manifest, size = await receive_upload(request, upload)
    except BackupValidationError as e:
        raise HTTPException(400, str(e)) from e
    except StarletteHTTPException:
        raise  # the answers about the form and its file name, and the body limit (413)
    except Exception as e:
        raise HTTPException(500, f"Failed to upload backup: {e}") from e

    await audit_change(
        AuditAction.BACKUP_CREATED, request, user.username, resource_type="backup", resource_id=safe_name,
        details="Encrypted backup uploaded" if upload.encrypted else "Backup uploaded",
    )
    return {
        "status": "success",
        "backup": BackupInfo(
            filename=safe_name,
            size=size,
            created=datetime.now().isoformat(),
            version="unknown" if manifest is None else str(manifest.get("version", BACKUP_VERSION)),
            encrypted=upload.encrypted,
        )
    }


@router.post("/restore/{filename}")
async def restore_backup(
    request: Request,
    filename: str,
    decrypt_request: Optional[DecryptRequest] = None,
    user=Depends(verify_token),
):
    """Restore from a backup file. Provide passphrase for encrypted backups.

    The archive is checked in one pass over its gzip stream, which extracts
    its database into /tmp (a tmpfs in RAM in the container); when it does
    not fit, the answer is 507 and nothing was restored.
    """
    ensure_backup_dir()
    backup_path = _backup_path(filename)
    if not _is_regular_file(backup_path):
        raise HTTPException(404, "Backup not found")

    is_encrypted = filename.endswith(".enc")
    passphrase = decrypt_request.passphrase if decrypt_request else None
    if is_encrypted and not passphrase:
        raise HTTPException(400, "Passphrase required for encrypted backup")

    # The locked part runs in its own task, awaited through shield: a request
    # that is cancelled cannot release smb_lock while the restore thread runs.
    global _restore_running
    if _restore_running:
        raise HTTPException(409, "Another restore is already running")
    _restore_running = True
    try:
        caller = caller_session(request, user)
        task = asyncio.ensure_future(_restore_and_reconcile(
            backup_path, passphrase if is_encrypted else None, request, user.username, caller))
    except BaseException:
        _restore_running = False
        raise
    try:
        result = await asyncio.shield(task)
    except RestoreInProgress:
        raise HTTPException(409, "Another restore is already running") from None
    except BackupValidationError as e:
        raise HTTPException(400, str(e)) from e
    except RestoreSpaceError as e:
        logger.warning("Backup restore failed: %s (%s)", e, e.__cause__)
        raise HTTPException(507, str(e)) from e
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Backup restore failed")
        raise HTTPException(500, f"Failed to restore backup: {e}") from e
    return result


@router.delete("/{filename}")
async def delete_backup(request: Request, filename: str, user=Depends(verify_token)):
    """Delete a backup file."""
    ensure_backup_dir()
    backup_path = _backup_path(filename)
    if not _is_regular_file(backup_path):
        raise HTTPException(404, "Backup not found")

    try:
        backup_path.unlink()
    except Exception as e:
        raise HTTPException(500, f"Failed to delete backup: {e}") from e
    await audit_change(AuditAction.BACKUP_DELETED, request, user.username, resource_type="backup",
                       resource_id=filename)
    return {"status": "deleted", "filename": filename}
