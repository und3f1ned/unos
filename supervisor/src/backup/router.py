import os
import json
import shutil
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path
from fastapi import APIRouter, HTTPException, Depends, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional

from ..auth.utils import verify_token
from ..config import get_settings
settings = get_settings()

router = APIRouter(prefix="/backup", tags=["backup"])


class BackupInfo(BaseModel):
    filename: str
    size: int
    created: str
    version: str


class BackupConfig(BaseModel):
    include_users: bool = True
    include_shares: bool = True
    include_pools: bool = True
    include_apps: bool = True
    include_scheduler: bool = True
    include_notifications: bool = True
    include_network: bool = True


BACKUP_DIR = Path("/data/backups")
BACKUP_VERSION = "1.0"


def ensure_backup_dir():
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)


@router.get("/list")
async def list_backups(_: dict = Depends(verify_token)):
    """List all available backups."""
    ensure_backup_dir()
    backups = []
    
    for path in BACKUP_DIR.glob("*.tar.gz"):
        try:
            stat = path.stat()
            # Try to read version from backup
            version = "unknown"
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
                version=version
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
    """Create a new backup of UNOS configuration."""
    ensure_backup_dir()
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"unos_backup_{timestamp}.tar.gz"
    backup_path = BACKUP_DIR / backup_name
    
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            
            # Create manifest
            manifest = {
                "version": BACKUP_VERSION,
                "created": datetime.now().isoformat(),
                "hostname": os.uname().nodename,
                "config": config.model_dump()
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
            
            # Create tarball
            with tarfile.open(backup_path, "w:gz") as tar:
                for item in tmpdir_path.iterdir():
                    tar.add(item, arcname=item.name)
            
            stat = backup_path.stat()
            return {
                "status": "success",
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
async def restore_backup(filename: str, _: dict = Depends(verify_token)):
    """Restore from a backup file."""
    ensure_backup_dir()
    
    # Security: prevent path traversal
    if ".." in filename or "/" in filename:
        raise HTTPException(400, "Invalid filename")
    
    backup_path = BACKUP_DIR / filename
    if not backup_path.exists():
        raise HTTPException(404, "Backup not found")
    
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            
            # Extract backup
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
