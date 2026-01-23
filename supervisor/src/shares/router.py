from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from ..auth.utils import verify_token
from .schemas import SmbShareCreate, SmbShareInfo, NfsExportCreate, NfsExportInfo
from . import smb, nfs
from ..storage import pools

router = APIRouter(prefix="/shares", tags=["shares"])


class SharePath(BaseModel):
    path: str
    label: str
    type: str  # "pool" or "volume"


@router.get("/paths", response_model=list[SharePath])
async def list_available_paths(_=Depends(verify_token)):
    """List available paths for sharing (pools and volumes)."""
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


# SMB Endpoints
@router.get("/smb", response_model=list[SmbShareInfo])
async def list_smb_shares(_=Depends(verify_token)):
    """List all SMB shares."""
    try:
        return smb.list_shares()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/smb", response_model=SmbShareInfo)
async def create_smb_share(share: SmbShareCreate, _=Depends(verify_token)):
    """Create a new SMB share."""
    try:
        return smb.create_share(share)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/smb/{name}", response_model=SmbShareInfo)
async def get_smb_share(name: str, _=Depends(verify_token)):
    """Get a specific SMB share."""
    share = smb.get_share(name)
    if not share:
        raise HTTPException(status_code=404, detail="Share not found")
    return share


@router.put("/smb/{name}", response_model=SmbShareInfo)
async def update_smb_share(name: str, share: SmbShareCreate, _=Depends(verify_token)):
    """Update an SMB share."""
    try:
        return smb.update_share(name, share)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/smb/{name}")
async def delete_smb_share(name: str, _=Depends(verify_token)):
    """Delete an SMB share."""
    try:
        smb.delete_share(name)
        return {"status": "deleted"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# NFS Endpoints
@router.get("/nfs", response_model=list[NfsExportInfo])
async def list_nfs_exports(_=Depends(verify_token)):
    """List all NFS exports."""
    try:
        return nfs.list_exports()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/nfs", response_model=NfsExportInfo)
async def create_nfs_export(export: NfsExportCreate, _=Depends(verify_token)):
    """Create a new NFS export."""
    try:
        return nfs.create_export(export)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/nfs")
async def delete_nfs_export(path: str, _=Depends(verify_token)):
    """Delete an NFS export."""
    try:
        nfs.delete_export(path)
        return {"status": "deleted"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
