from fastapi import APIRouter, Depends, HTTPException
from ..auth.utils import verify_token
from .schemas import DiskInfo, SmartData, PoolCreate, PoolInfo, VolumeCreate, VolumeInfo
from . import disks, pools

router = APIRouter(prefix="/storage", tags=["storage"])


@router.get("/disks", response_model=list[DiskInfo])
async def list_disks_endpoint(_=Depends(verify_token)):
    """List all physical disks."""
    try:
        return disks.list_disks()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/disks/{name}/smart", response_model=SmartData)
async def get_disk_smart(name: str, _=Depends(verify_token)):
    """Get SMART data for a disk."""
    try:
        return disks.get_smart_data(f"/dev/{name}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/pools", response_model=list[PoolInfo])
async def list_pools_endpoint(_=Depends(verify_token)):
    """List all storage pools."""
    try:
        return pools.list_pools()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/pools", response_model=PoolInfo)
async def create_pool_endpoint(pool: PoolCreate, _=Depends(verify_token)):
    """Create a new storage pool."""
    try:
        return pools.create_pool(pool)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/pools/{name}", response_model=PoolInfo)
async def get_pool_endpoint(name: str, _=Depends(verify_token)):
    """Get pool details."""
    try:
        return pools.get_pool(name)
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Pool not found: {e}")


@router.delete("/pools/{name}")
async def delete_pool_endpoint(name: str, _=Depends(verify_token)):
    """Delete a storage pool."""
    try:
        pools.delete_pool(name)
        return {"status": "deleted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/pools/{pool_name}/volumes", response_model=list[VolumeInfo])
async def list_volumes_endpoint(pool_name: str, _=Depends(verify_token)):
    """List volumes in a pool."""
    try:
        return pools.list_volumes(pool_name)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/pools/{pool_name}/volumes", response_model=VolumeInfo)
async def create_volume_endpoint(pool_name: str, volume: VolumeCreate, _=Depends(verify_token)):
    """Create a new volume."""
    volume.pool_name = pool_name
    try:
        return pools.create_volume(volume)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/pools/{pool_name}/volumes/{volume_name}")
async def delete_volume_endpoint(pool_name: str, volume_name: str, _=Depends(verify_token)):
    """Delete a volume."""
    try:
        pools.delete_volume(pool_name, volume_name)
        return {"status": "deleted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
