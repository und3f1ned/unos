import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address
from ..auth.utils import verify_token
from ..security.validators import sanitize_error_message
from .schemas import SmbShareCreate, SmbShareInfo, NfsExportCreate, NfsExportInfo
from . import smb, nfs
from ..storage import pools

# Rate limiter for shares endpoints
limiter = Limiter(key_func=get_remote_address)

router = APIRouter(prefix="/shares", tags=["shares"])


class SharePath(BaseModel):
    path: str
    label: str
    type: str  # "pool" or "volume"


async def _call(func, *args, not_found_status: int = 400):
    """Run a blocking share operation in a worker thread and map its errors.

    ValueError carries a user-facing message (unknown or duplicate share);
    validation errors arrive as HTTPException and are passed through.
    """
    try:
        return await asyncio.to_thread(func, *args)
    except HTTPException:
        raise
    except smb.SambaConfigError as e:
        raise HTTPException(status_code=500, detail=sanitize_error_message(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=not_found_status, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=sanitize_error_message(e)) from e


def _available_paths() -> list[SharePath]:
    available = []

    try:
        pool_list = pools.list_pools()
        for pool in pool_list:
            if pool.mountpoint:
                available.append(SharePath(
                    path=pool.mountpoint,
                    label=f"{pool.name} (Pool)",
                    type="pool"
                ))
                # Also list volumes in this pool
                try:
                    volumes = pools.list_volumes(pool.name)
                    for vol in volumes:
                        available.append(SharePath(
                            path=vol.path,
                            label=f"{pool.name}/{vol.name} (Volume)",
                            type="volume"
                        ))
                except Exception:
                    pass
    except Exception:
        pass

    return available


@router.get("/paths", response_model=list[SharePath])
async def list_available_paths(_=Depends(verify_token)):
    """List available paths for sharing (pools and volumes)."""
    return await asyncio.to_thread(_available_paths)


# SMB Endpoints
@router.get("/smb", response_model=list[SmbShareInfo])
async def list_smb_shares(_=Depends(verify_token)):
    """List all SMB shares."""
    return await _call(smb.list_shares)


@router.post("/smb", response_model=SmbShareInfo)
@limiter.limit("10/minute")  # Rate limit share creation
async def create_smb_share(request: Request, share: SmbShareCreate, _=Depends(verify_token)):
    """Create a new SMB share."""
    return await _call(smb.create_share, share)


@router.get("/smb/{name}", response_model=SmbShareInfo)
async def get_smb_share(name: str, _=Depends(verify_token)):
    """Get a specific SMB share."""
    share = await _call(smb.get_share, name)
    if not share:
        raise HTTPException(status_code=404, detail="Share not found")
    return share


@router.put("/smb/{name}", response_model=SmbShareInfo)
async def update_smb_share(name: str, share: SmbShareCreate, _=Depends(verify_token)):
    """Update an SMB share."""
    return await _call(smb.update_share, name, share)


@router.delete("/smb/{name}")
@limiter.limit("10/minute")  # Rate limit share deletion
async def delete_smb_share(request: Request, name: str, _=Depends(verify_token)):
    """Delete an SMB share."""
    await _call(smb.delete_share, name, not_found_status=404)
    return {"status": "deleted"}


# NFS Endpoints
@router.get("/nfs", response_model=list[NfsExportInfo])
async def list_nfs_exports(_=Depends(verify_token)):
    """List all NFS exports."""
    return await _call(nfs.list_exports)


@router.post("/nfs", response_model=NfsExportInfo)
async def create_nfs_export(export: NfsExportCreate, _=Depends(verify_token)):
    """Create a new NFS export."""
    return await _call(nfs.create_export, export)


@router.delete("/nfs")
async def delete_nfs_export(path: str, _=Depends(verify_token)):
    """Delete an NFS export."""
    await _call(nfs.delete_export, path, not_found_status=404)
    return {"status": "deleted"}
