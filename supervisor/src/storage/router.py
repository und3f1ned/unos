from fastapi import APIRouter, Depends, HTTPException, Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from ..auth.utils import verify_token
from ..security.validators import validate_disk_name, validate_pool_name, validate_volume_name, sanitize_error_message
from .schemas import DiskInfo, SmartData, PoolCreate, PoolInfo, VolumeCreate, VolumeInfo, ScrubStatus
from . import disks, pools

# Rate limiter for storage endpoints
limiter = Limiter(key_func=get_remote_address)

router = APIRouter(prefix="/storage", tags=["storage"])


@router.get("/disks", response_model=list[DiskInfo])
async def list_disks_endpoint(_=Depends(verify_token)):
    """List all physical disks."""
    try:
        return disks.list_disks()
    except Exception as e:
        raise HTTPException(status_code=500, detail=sanitize_error_message(e))


@router.get("/disks/{name}/smart", response_model=SmartData)
async def get_disk_smart(name: str, _=Depends(verify_token)):
    """Get SMART data for a disk."""
    # Validate disk name to prevent path traversal
    validated_name = validate_disk_name(name)

    try:
        return disks.get_smart_data(f"/dev/{validated_name}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=sanitize_error_message(e))


@router.get("/pools", response_model=list[PoolInfo])
async def list_pools_endpoint(_=Depends(verify_token)):
    """List all storage pools."""
    try:
        return pools.list_pools()
    except Exception as e:
        raise HTTPException(status_code=500, detail=sanitize_error_message(e))


@router.post("/pools", response_model=PoolInfo)
@limiter.limit("5/minute")  # Rate limit pool creation
async def create_pool_endpoint(request: Request, pool: PoolCreate, _=Depends(verify_token)):
    """Create a new storage pool."""
    # Validate pool name
    validate_pool_name(pool.name)

    try:
        return pools.create_pool(pool)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=sanitize_error_message(e))


@router.get("/pools/{name}", response_model=PoolInfo)
async def get_pool_endpoint(name: str, _=Depends(verify_token)):
    """Get pool details."""
    # Validate pool name
    validated_name = validate_pool_name(name)

    try:
        return pools.get_pool(validated_name)
    except Exception as e:
        raise HTTPException(status_code=404, detail="Pool not found")


@router.delete("/pools/{name}")
@limiter.limit("5/minute")  # Rate limit pool deletion
async def delete_pool_endpoint(request: Request, name: str, _=Depends(verify_token)):
    """Delete a storage pool."""
    # Validate pool name
    validated_name = validate_pool_name(name)

    try:
        pools.delete_pool(validated_name)
        return {"status": "deleted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=sanitize_error_message(e))


@router.get("/pools/{pool_name}/volumes", response_model=list[VolumeInfo])
async def list_volumes_endpoint(pool_name: str, _=Depends(verify_token)):
    """List volumes in a pool."""
    # Validate pool name
    validated_pool = validate_pool_name(pool_name)

    try:
        return pools.list_volumes(validated_pool)
    except Exception as e:
        raise HTTPException(status_code=500, detail=sanitize_error_message(e))


@router.post("/pools/{pool_name}/volumes", response_model=VolumeInfo)
@limiter.limit("10/minute")  # Rate limit volume creation
async def create_volume_endpoint(request: Request, pool_name: str, volume: VolumeCreate, _=Depends(verify_token)):
    """Create a new volume."""
    # Validate pool and volume names
    validated_pool = validate_pool_name(pool_name)
    validate_volume_name(volume.name)

    volume.pool_name = validated_pool
    try:
        return pools.create_volume(volume)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=sanitize_error_message(e))


@router.delete("/pools/{pool_name}/volumes/{volume_name}")
async def delete_volume_endpoint(pool_name: str, volume_name: str, _=Depends(verify_token)):
    """Delete a volume."""
    # Validate pool and volume names
    validated_pool = validate_pool_name(pool_name)
    validated_volume = validate_volume_name(volume_name)

    try:
        pools.delete_volume(validated_pool, validated_volume)
        return {"status": "deleted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=sanitize_error_message(e))


@router.post("/pools/{name}/scrub", response_model=ScrubStatus)
async def start_scrub_endpoint(name: str, _=Depends(verify_token)):
    """Start a scrub on a pool."""
    # Validate pool name
    validated_name = validate_pool_name(name)

    try:
        return pools.start_scrub(validated_name)
    except Exception as e:
        raise HTTPException(status_code=500, detail=sanitize_error_message(e))


@router.get("/pools/{name}/scrub", response_model=ScrubStatus)
async def get_scrub_status_endpoint(name: str, _=Depends(verify_token)):
    """Get scrub status for a pool."""
    # Validate pool name
    validated_name = validate_pool_name(name)

    try:
        return pools.get_scrub_status(validated_name)
    except Exception as e:
        raise HTTPException(status_code=500, detail=sanitize_error_message(e))


@router.delete("/pools/{name}/scrub", response_model=ScrubStatus)
async def cancel_scrub_endpoint(name: str, _=Depends(verify_token)):
    """Cancel a running scrub on a pool."""
    # Validate pool name
    validated_name = validate_pool_name(name)

    try:
        return pools.cancel_scrub(validated_name)
    except Exception as e:
        raise HTTPException(status_code=500, detail=sanitize_error_message(e))
