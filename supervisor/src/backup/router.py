"""Configuration backup and restore.

Backup archives are untrusted when they are restored: they can be uploaded,
and a stolen administrator session must not be able to turn a crafted
archive into arbitrary file writes on the host. Restore therefore:

* decompresses the archive with a size limit and extracts only an allowlist
  of regular files (``_FILE_MEMBERS``); links, devices, absolute paths and
  ``..`` abort the restore, and tarfile's ``data`` filter is applied as a
  second line of defence;
* replaces the live database only after the restored copy passes
  ``PRAGMA integrity_check`` and has a usable ``users`` table with an
  administrator. The current database is copied first, and the copy into the
  live file uses SQLite's online backup API, so connections that are open
  in the running supervisor stay consistent;
* never installs the archived smb.conf as is: its shares are validated like
  shares created through the API and smb.conf is regenerated and checked
  with testparm (``shares.smb.stage_backup_config``). Problems with smb.conf
  are reported as warnings and do not stop the database restore.
"""

import asyncio
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
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, Optional

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..auth.utils import verify_token
from ..config import get_settings
from ..database import init_db
from ..shares import smb

settings = get_settings()
logger = logging.getLogger(__name__)

# Encryption constants
SALT_SIZE = 16
NONCE_SIZE = 12
KEY_SIZE = 32  # AES-256
ITERATIONS = 100000

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

BACKUP_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,200}\.tar\.gz(\.enc)?")
# Regular files a backup may contain, with their size limits. Other regular
# files and directories are ignored; any other member type aborts the restore.
_FILE_MEMBERS = {
    "manifest.json": MAX_CONFIG_BYTES,
    "unos.db": MAX_DB_BYTES,
    "smb.conf": MAX_CONFIG_BYTES,
    "fstab": MAX_CONFIG_BYTES,
    "scheduled_tasks.json": MAX_CONFIG_BYTES,
}
_NETPLAN_MEMBER = re.compile(r"netplan/[A-Za-z0-9][A-Za-z0-9._-]{0,100}\.yaml")
_DIR_MEMBERS = frozenset({"netplan"})
# Included in backups but not restored automatically.
_NOT_RESTORED = ("fstab", "netplan", "scheduled_tasks.json")
REQUIRED_USER_COLUMNS = frozenset({"id", "username", "hashed_password", "is_admin"})

_restore_lock = threading.Lock()


class BackupValidationError(Exception):
    """The archive is not an acceptable UNOS backup (reported as HTTP 400)."""


class BackupInfo(BaseModel):
    filename: str
    size: int
    created: str
    version: str
    encrypted: bool = False


class BackupConfig(BaseModel):
    include_users: bool = True
    include_shares: bool = True
    include_pools: bool = True
    include_apps: bool = True
    include_scheduler: bool = True
    include_notifications: bool = True
    include_network: bool = True
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


def encrypt_file(data: bytes, passphrase: str) -> bytes:
    """Encrypt data using AES-256-GCM."""
    salt = secrets.token_bytes(SALT_SIZE)
    key = derive_key(passphrase, salt)
    nonce = secrets.token_bytes(NONCE_SIZE)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, data, None)
    # Format: salt + nonce + ciphertext
    return salt + nonce + ciphertext


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
    """Write a consistent copy of a (possibly in-use) SQLite database to ``dest``."""
    source = sqlite3.connect(str(src), timeout=30)
    try:
        target = sqlite3.connect(str(dest))
        try:
            source.backup(target)
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
    except sqlite3.Error as e:
        raise BackupValidationError(f"unos.db in the backup cannot be read: {e}") from e
    finally:
        conn.close()


def _keep_copy_of_database(db_path: Path) -> Path | None:
    """Copy the current database next to it before a restore; keep the newest copies."""
    if not db_path.exists():
        return None
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    copy_path = db_path.with_name(f"{db_path.name}.pre-restore-{timestamp}")
    copy_database(db_path, copy_path)
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


def _copy_limited(source: BinaryIO, dest: Path, limit: int) -> None:
    total = 0
    with open(dest, "xb") as out:
        while chunk := source.read(_CHUNK_SIZE):
            total += len(chunk)
            if total > limit:
                raise BackupValidationError("Backup file is too large")
            out.write(chunk)


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


def _decompress(source: BinaryIO, tar_path: Path) -> None:
    """Decompress a gzip stream into ``tar_path``, refusing decompression bombs."""
    total = 0
    with open(tar_path, "wb") as out:
        try:
            with gzip.GzipFile(fileobj=source, mode="rb") as gz:
                while chunk := gz.read(_CHUNK_SIZE):
                    total += len(chunk)
                    if total > MAX_TAR_BYTES:
                        raise BackupValidationError("Backup archive is too large when decompressed")
                    out.write(chunk)
        except (gzip.BadGzipFile, EOFError, zlib.error) as e:
            raise BackupValidationError("Invalid backup: corrupt archive") from e


def _member_name(member: tarfile.TarInfo) -> str:
    """Normalize a member name; refuse absolute paths and ``..``."""
    name = member.name
    if not name or "\x00" in name or "\\" in name or name.startswith("/"):
        raise BackupValidationError(f"Unsafe path in backup archive: {name[:100]!r}")
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if ".." in parts or (not parts and not member.isdir()):
        raise BackupValidationError(f"Unsafe path in backup archive: {name[:100]!r}")
    return "/".join(parts)


def _scan_members(tar: tarfile.TarFile) -> dict[str, tarfile.TarInfo]:
    """Validate every member and return the ones to extract, by normalized name."""
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
                if name in _DIR_MEMBERS:
                    wanted[name] = member
                continue
            if not member.isreg():
                raise BackupValidationError(
                    f"Backup archive contains a link or special file: {name[:100]!r}"
                )
            limit = _FILE_MEMBERS.get(name)
            if limit is None and _NETPLAN_MEMBER.fullmatch(name):
                limit = MAX_CONFIG_BYTES
            if limit is None:
                continue  # not part of a UNOS backup: ignored
            if member.size > limit:
                raise BackupValidationError(f"{name} in the backup archive is too large")
            wanted[name] = member
    except tarfile.TarError as e:
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


def _open_tar(tar_path: Path) -> tarfile.TarFile:
    try:
        return tarfile.open(tar_path, "r:")
    except tarfile.TarError as e:
        raise BackupValidationError("Invalid backup: corrupt archive") from e


def _extract(tar: tarfile.TarFile, members: dict[str, tarfile.TarInfo], dest: Path) -> None:
    if not hasattr(tarfile, "data_filter"):
        raise RuntimeError("Restoring backups requires Python 3.11.4 or newer (tarfile filters)")
    try:
        tar.extractall(dest, members=list(members.values()), filter="data")
    except tarfile.TarError as e:  # includes the data filter's refusals
        raise BackupValidationError(f"Invalid backup: {e}") from e


def _inspect_archive(archive: Path) -> dict:
    """Validate an archive without extracting it; return its manifest."""
    with tempfile.TemporaryDirectory(prefix="unos-backup-") as workdir:
        tar_path = Path(workdir) / "backup.tar"
        with _open_archive(archive, None) as source:
            _decompress(source, tar_path)
        with _open_tar(tar_path) as tar:
            members = _scan_members(tar)
            try:
                manifest_file = tar.extractfile(members["manifest.json"])
                data = manifest_file.read() if manifest_file else b""
            except tarfile.TarError as e:
                raise BackupValidationError("Invalid backup: corrupt archive") from e
            return _parse_manifest(data)


def _publish(tmp: Path, prefix: str) -> str:
    """Give an uploaded file its final name without replacing an existing backup."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    for attempt in range(100):
        suffix = f"_{attempt}" if attempt else ""
        name = f"{prefix}_{timestamp}{suffix}.tar.gz"
        try:
            os.link(tmp, BACKUP_DIR / name)
            return name
        except FileExistsError:
            continue
    raise RuntimeError("Could not choose a file name for the uploaded backup")


def _store_upload(source: BinaryIO) -> tuple[str, dict]:
    tmp = BACKUP_DIR / f".upload-{secrets.token_hex(8)}.tmp"
    try:
        _copy_limited(source, tmp, MAX_ARCHIVE_BYTES)
        manifest = _inspect_archive(tmp)
        return _publish(tmp, "uploaded"), manifest
    finally:
        tmp.unlink(missing_ok=True)


def _stage_smb_conf(path: Path, warnings: list[str]) -> smb.StagedSmbConf | None:
    try:
        content = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        warnings.append("smb.conf in the backup is not valid UTF-8 and was not restored.")
        return None
    try:
        staged, smb_warnings = smb.stage_backup_config(content)
    except (smb.SambaConfigError, OSError) as e:
        warnings.append(f"smb.conf was not restored: {e}")
        return None
    warnings.extend(smb_warnings)
    return staged


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


def _restore_locked(backup_path: Path, passphrase: str | None) -> dict:
    warnings: list[str] = []
    restored: list[str] = []
    previous_db: Path | None = None

    with tempfile.TemporaryDirectory(prefix="unos-restore-") as workdir:
        work = Path(workdir)
        tar_path = work / "backup.tar"
        with _open_archive(backup_path, passphrase) as source:
            _decompress(source, tar_path)

        extract_dir = work / "extract"
        extract_dir.mkdir()
        with _open_tar(tar_path) as tar:
            members = _scan_members(tar)
            _extract(tar, members, extract_dir)
        manifest = _parse_manifest((extract_dir / "manifest.json").read_bytes())

        # Validate everything before changing anything.
        db_path = _live_db_path()
        db_file = extract_dir / "unos.db" if "unos.db" in members else None
        if db_file is not None:
            if db_path is None:
                warnings.append("The database was not restored: no database file is configured.")
                db_file = None
            else:
                _verify_database(db_file)

        staged = _stage_smb_conf(extract_dir / "smb.conf", warnings) if "smb.conf" in members else None

        try:
            if db_file is not None:
                previous_db = _keep_copy_of_database(db_path)
                _replace_database(db_file, db_path)
                restored.append("database")
            if staged is not None:
                try:
                    staged.install()
                    restored.append("smb_config")
                except OSError as e:
                    warnings.append(f"smb.conf was not restored: {e}")
        finally:
            if staged is not None:
                staged.discard()

        for name in _NOT_RESTORED:
            if name in members:
                warnings.append(f"{name} is included in the backup but is not restored automatically.")

    message = f"Backup restored: {', '.join(restored)}." if restored else "Nothing was restored."
    if warnings:
        message += " " + " ".join(warnings)
    return {
        "status": "success",
        "restored": restored,
        "manifest": manifest,
        "message": message,
        "warnings": warnings,
        "previous_database": str(previous_db) if previous_db else None,
    }


# --- endpoints ----------------------------------------------------------------


def _list_backups() -> list[BackupInfo]:
    ensure_backup_dir()
    backups = []

    # Find both encrypted (.tar.gz.enc) and unencrypted (.tar.gz) backups
    for pattern in ["*.tar.gz", "*.tar.gz.enc"]:
        for path in BACKUP_DIR.glob(pattern):
            # Skip if it's a .tar.gz that also has .enc version
            if pattern == "*.tar.gz" and path.name.endswith(".tar.gz"):
                enc_path = BACKUP_DIR / f"{path.name}.enc"
                if enc_path.exists():
                    continue

            try:
                stat_result = path.stat()
                is_encrypted = path.name.endswith(".enc")
                version = "unknown"

                # Try to read version from backup (only for unencrypted)
                if not is_encrypted:
                    try:
                        with tarfile.open(path, "r:gz") as tar:
                            for member in tar.getmembers():
                                if member.name == "manifest.json":
                                    f = tar.extractfile(member)
                                    if f:
                                        manifest = json.load(f)
                                        version = manifest.get("version", "unknown")
                                    break
                    except Exception:
                        pass

                backups.append(BackupInfo(
                    filename=path.name,
                    size=stat_result.st_size,
                    created=datetime.fromtimestamp(stat_result.st_mtime).isoformat(),
                    version=str(version),
                    encrypted=is_encrypted
                ))
            except Exception:
                pass

    # Sort by creation date, newest first
    backups.sort(key=lambda x: x.created, reverse=True)
    return backups


@router.get("/list")
async def list_backups(_: dict = Depends(verify_token)):
    """List all available backups (both encrypted and unencrypted)."""
    return {"backups": await asyncio.to_thread(_list_backups)}


def _create_backup(config: BackupConfig) -> dict:
    ensure_backup_dir()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = ".tar.gz.enc" if config.encrypt else ".tar.gz"
    backup_name = f"unos_backup_{timestamp}{suffix}"
    backup_path = BACKUP_DIR / backup_name

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        # Create manifest (don't include passphrase)
        manifest_config = config.model_dump()
        manifest_config.pop("passphrase", None)
        manifest = {
            "version": BACKUP_VERSION,
            "created": datetime.now().isoformat(),
            "hostname": os.uname().nodename,
            "encrypted": config.encrypt,
            "config": manifest_config
        }

        manifest_path = tmpdir_path / "manifest.json"
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2)

        # Backup database: use SQLite's backup API, a plain file copy of a
        # database in use can be torn and would then fail the restore check.
        db_path = _live_db_path()
        if db_path is not None and db_path.exists():
            copy_database(db_path, tmpdir_path / "unos.db")

        # Backup SMB config
        smb_conf = Path("/etc/samba/smb.conf")
        if smb_conf.exists() and config.include_shares:
            shutil.copy2(smb_conf, tmpdir_path / "smb.conf")

        # Backup fstab (pool mounts)
        fstab = Path("/etc/fstab")
        if fstab.exists() and config.include_pools:
            shutil.copy2(fstab, tmpdir_path / "fstab")

        # Backup network config
        if config.include_network:
            netplan_dir = Path("/etc/netplan")
            if netplan_dir.exists():
                net_backup = tmpdir_path / "netplan"
                net_backup.mkdir()
                for conf in netplan_dir.glob("*.yaml"):
                    shutil.copy2(conf, net_backup / conf.name)

        # Create tarball in memory for potential encryption
        tar_path = tmpdir_path / "backup.tar.gz"
        with tarfile.open(tar_path, "w:gz") as tar:
            for item in tmpdir_path.iterdir():
                if item.name != "backup.tar.gz":
                    tar.add(item, arcname=item.name)

        # Encrypt if requested
        if config.encrypt:
            with open(tar_path, "rb") as f:
                tar_data = f.read()
            encrypted_data = encrypt_file(tar_data, config.passphrase)
            with open(backup_path, "wb") as f:
                f.write(encrypted_data)
        else:
            shutil.move(tar_path, backup_path)

        stat_result = backup_path.stat()
        return {
            "status": "success",
            "encrypted": config.encrypt,
            "backup": BackupInfo(
                filename=backup_name,
                size=stat_result.st_size,
                created=datetime.now().isoformat(),
                version=BACKUP_VERSION
            )
        }


@router.post("/create")
async def create_backup(
    config: BackupConfig = BackupConfig(),
    _: dict = Depends(verify_token)
):
    """Create a new backup of UNOS configuration. Optionally encrypted with AES-256."""
    # Validate encryption config
    if config.encrypt and not config.passphrase:
        raise HTTPException(400, "Passphrase required for encrypted backup")

    try:
        return await asyncio.to_thread(_create_backup, config)
    except Exception as e:
        raise HTTPException(500, f"Failed to create backup: {e}") from e


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


@router.post("/upload")
async def upload_backup(
    file: UploadFile = File(...),
    _: dict = Depends(verify_token)
):
    """Upload a backup file. It is validated like a restore, but not applied."""
    ensure_backup_dir()

    if not file.filename or not file.filename.endswith(".tar.gz"):
        raise HTTPException(400, "Invalid file format. Must be .tar.gz")

    try:
        safe_name, manifest = await asyncio.to_thread(_store_upload, file.file)
    except BackupValidationError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        raise HTTPException(500, f"Failed to upload backup: {e}") from e

    stat_result = (BACKUP_DIR / safe_name).stat()
    return {
        "status": "success",
        "backup": BackupInfo(
            filename=safe_name,
            size=stat_result.st_size,
            created=datetime.now().isoformat(),
            version=str(manifest.get("version", BACKUP_VERSION))
        )
    }


@router.post("/restore/{filename}")
async def restore_backup(
    filename: str,
    decrypt_request: Optional[DecryptRequest] = None,
    _: dict = Depends(verify_token)
):
    """Restore from a backup file. Provide passphrase for encrypted backups."""
    ensure_backup_dir()
    backup_path = _backup_path(filename)
    if not _is_regular_file(backup_path):
        raise HTTPException(404, "Backup not found")

    is_encrypted = filename.endswith(".enc")
    passphrase = decrypt_request.passphrase if decrypt_request else None
    if is_encrypted and not passphrase:
        raise HTTPException(400, "Passphrase required for encrypted backup")

    try:
        result = await asyncio.to_thread(_restore, backup_path, passphrase if is_encrypted else None)
    except RestoreInProgress:
        raise HTTPException(409, "Another restore is already running") from None
    except BackupValidationError as e:
        raise HTTPException(400, str(e)) from e
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Backup restore failed")
        raise HTTPException(500, f"Failed to restore backup: {e}") from e

    if "database" in result["restored"]:
        # Older backups may lack tables added since; create them.
        try:
            await init_db()
        except Exception:
            logger.exception("Could not create missing tables after the database restore")
    return result


@router.delete("/{filename}")
async def delete_backup(filename: str, _: dict = Depends(verify_token)):
    """Delete a backup file."""
    ensure_backup_dir()
    backup_path = _backup_path(filename)
    if not _is_regular_file(backup_path):
        raise HTTPException(404, "Backup not found")

    try:
        backup_path.unlink()
        return {"status": "deleted", "filename": filename}
    except Exception as e:
        raise HTTPException(500, f"Failed to delete backup: {e}") from e
