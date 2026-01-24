import os
import json
import shutil
import tarfile
import tempfile
import secrets
from datetime import datetime
from pathlib import Path
from fastapi import APIRouter, HTTPException, Depends, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import base64

from ..auth.utils import verify_token
from ..config import get_settings
settings = get_settings()

# Encryption constants
SALT_SIZE = 16
NONCE_SIZE = 12
KEY_SIZE = 32  # AES-256
ITERATIONS = 100000

router = APIRouter(prefix="/backup", tags=["backup"])


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
    passphrase: str


BACKUP_DIR = Path("/data/backups")


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
BACKUP_VERSION = "1.0"


def ensure_backup_dir():
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)


@router.get("/list")
async def list_backups(_: dict = Depends(verify_token)):
    """List all available backups (both encrypted and unencrypted)."""
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
                stat = path.stat()
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
                    size=stat.st_size,
                    created=datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    version=version,
                    encrypted=is_encrypted
                ))
            except Exception:
                pass

    # Sort by creation date, newest first
    backups.sort(key=lambda x: x.created, reverse=True)
    return {"backups": backups}


@router.post("/create")
async def create_backup(
    config: BackupConfig = BackupConfig(),
    _: dict = Depends(verify_token)
):
    """Create a new backup of UNOS configuration. Optionally encrypted with AES-256."""
    ensure_backup_dir()

    # Validate encryption config
    if config.encrypt and not config.passphrase:
        raise HTTPException(400, "Passphrase required for encrypted backup")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = ".tar.gz.enc" if config.encrypt else ".tar.gz"
    backup_name = f"unos_backup_{timestamp}{suffix}"
    backup_path = BACKUP_DIR / backup_name

    try:
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

            # Backup database
            db_path = Path(settings.db_path)
            if db_path.exists():
                shutil.copy2(db_path, tmpdir_path / "unos.db")

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

            stat = backup_path.stat()
            return {
                "status": "success",
                "encrypted": config.encrypt,
                "backup": BackupInfo(
                    filename=backup_name,
                    size=stat.st_size,
                    created=datetime.now().isoformat(),
                    version=BACKUP_VERSION
                )
            }
    except Exception as e:
        raise HTTPException(500, f"Failed to create backup: {e}")


@router.get("/download/{filename}")
async def download_backup(filename: str, _: dict = Depends(verify_token)):
    """Download a backup file."""
    ensure_backup_dir()
    
    # Security: prevent path traversal
    if ".." in filename or "/" in filename:
        raise HTTPException(400, "Invalid filename")
    
    backup_path = BACKUP_DIR / filename
    if not backup_path.exists():
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
    """Upload a backup file."""
    ensure_backup_dir()
    
    if not file.filename or not file.filename.endswith(".tar.gz"):
        raise HTTPException(400, "Invalid file format. Must be .tar.gz")
    
    # Generate safe filename
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = f"uploaded_{timestamp}.tar.gz"
    backup_path = BACKUP_DIR / safe_name
    
    try:
        with open(backup_path, "wb") as f:
            content = await file.read()
            f.write(content)
        
        # Verify it is a valid tarball
        try:
            with tarfile.open(backup_path, "r:gz") as tar:
                names = tar.getnames()
                if "manifest.json" not in names:
                    backup_path.unlink()
                    raise HTTPException(400, "Invalid backup: missing manifest")
        except tarfile.TarError:
            backup_path.unlink()
            raise HTTPException(400, "Invalid backup: corrupt archive")
        
        stat = backup_path.stat()
        return {
            "status": "success",
            "backup": BackupInfo(
                filename=safe_name,
                size=stat.st_size,
                created=datetime.now().isoformat(),
                version=BACKUP_VERSION
            )
        }
    except HTTPException:
        raise
    except Exception as e:
        if backup_path.exists():
            backup_path.unlink()
        raise HTTPException(500, f"Failed to upload backup: {e}")


@router.post("/restore/{filename}")
async def restore_backup(
    filename: str,
    decrypt_request: Optional[DecryptRequest] = None,
    _: dict = Depends(verify_token)
):
    """Restore from a backup file. Provide passphrase for encrypted backups."""
    ensure_backup_dir()

    # Security: prevent path traversal
    if ".." in filename or "/" in filename:
        raise HTTPException(400, "Invalid filename")

    backup_path = BACKUP_DIR / filename
    if not backup_path.exists():
        raise HTTPException(404, "Backup not found")

    is_encrypted = filename.endswith(".enc")
    if is_encrypted and (not decrypt_request or not decrypt_request.passphrase):
        raise HTTPException(400, "Passphrase required for encrypted backup")

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Handle encrypted backup
            if is_encrypted:
                try:
                    with open(backup_path, "rb") as f:
                        encrypted_data = f.read()
                    decrypted_data = decrypt_file(encrypted_data, decrypt_request.passphrase)

                    # Write decrypted tarball
                    tar_path = tmpdir_path / "backup.tar.gz"
                    with open(tar_path, "wb") as f:
                        f.write(decrypted_data)

                    # Extract from decrypted tarball
                    with tarfile.open(tar_path, "r:gz") as tar:
                        tar.extractall(tmpdir_path)
                except Exception as e:
                    if "InvalidTag" in str(type(e).__name__) or "tag" in str(e).lower():
                        raise HTTPException(400, "Invalid passphrase or corrupted backup")
                    raise
            else:
                # Extract unencrypted backup directly
                with tarfile.open(backup_path, "r:gz") as tar:
                    tar.extractall(tmpdir_path)
            
            # Read manifest
            manifest_path = tmpdir_path / "manifest.json"
            if not manifest_path.exists():
                raise HTTPException(400, "Invalid backup: missing manifest")
            
            with open(manifest_path) as f:
                manifest = json.load(f)
            
            restored = []
            
            # Restore database
            db_backup = tmpdir_path / "unos.db"
            if db_backup.exists():
                db_path = Path(settings.db_path)
                # Create backup of current db
                if db_path.exists():
                    shutil.copy2(db_path, str(db_path) + ".bak")
                shutil.copy2(db_backup, db_path)
                restored.append("database")
            
            # Restore SMB config
            smb_backup = tmpdir_path / "smb.conf"
            if smb_backup.exists():
                shutil.copy2(smb_backup, "/etc/samba/smb.conf")
                restored.append("smb_config")
            
            # Note: fstab and netplan require careful handling
            # We just report what could be restored
            
            return {
                "status": "success",
                "restored": restored,
                "manifest": manifest,
                "message": "Backup restored. Restart services to apply changes."
            }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Failed to restore backup: {e}")


@router.delete("/{filename}")
async def delete_backup(filename: str, _: dict = Depends(verify_token)):
    """Delete a backup file."""
    ensure_backup_dir()
    
    # Security: prevent path traversal
    if ".." in filename or "/" in filename:
        raise HTTPException(400, "Invalid filename")
    
    backup_path = BACKUP_DIR / filename
    if not backup_path.exists():
        raise HTTPException(404, "Backup not found")
    
    try:
        backup_path.unlink()
        return {"status": "deleted", "filename": filename}
    except Exception as e:
        raise HTTPException(500, f"Failed to delete backup: {e}")
