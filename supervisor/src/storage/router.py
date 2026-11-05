import asyncio
from collections.abc import Callable
from typing import Any, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from ..auth.utils import verify_token
from ..security.validators import (
    sanitize_error_message,
    validate_disk_name,
    validate_pool_name,
    validate_volume_name,
)
from . import disks, pools
from .schemas import (
    DiskInfo,
    PoolCreate,
    PoolInfo,
    ScrubStatus,
    SmartData,
    VolumeCreate,
    VolumeInfo,
)

T = TypeVar("T")

# Rate limiter for storage endpoints
limiter = Limiter(key_func=get_remote_address)

router = APIRouter(prefix="/storage", tags=["storage"])


def _http_error(exc: Exception) -> HTTPException:
    """Map storage errors to HTTP errors (404 / 409 / 400 / 500)."""
    if isinstance(exc, pools.PoolNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, pools.PoolConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=500, detail=sanitize_error_message(exc))


async def _call(func: Callable[..., T], *args: Any) -> T:
    """Run a blocking storage operation in a worker thread; map its errors to HTTP errors."""
    try:
        return await asyncio.to_thread(func, *args)
    except HTTPException:
        raise
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/disks", response_model=list[DiskInfo])
async def list_disks_endpoint(_=Depends(verify_token)):
    """List physical disks with their host state (system, in use, data)."""
    return await _call(disks.list_disks)


@router.get("/disks/{name}/smart", response_model=SmartData)
async def get_disk_smart(name: str, _=Depends(verify_token)):
    """Get SMART data for a disk."""
    # Validate disk name to prevent path traversal
    validated_name = validate_disk_name(name)

    # smartctl can take many seconds (or spin up a sleeping disk): keep it
    # off the event loop. get_smart_data bounds it with a timeout.
    return await _call(disks.get_smart_data, f"/dev/{validated_name}")


@router.get("/pools", response_model=list[PoolInfo])
async def list_pools_endpoint(_=Depends(verify_token)):
    """List all storage pools."""
    return await _call(pools.list_pools)


@router.post("/pools", response_model=PoolInfo)
@limiter.limit("5/minute")  # Rate limit pool creation
async def create_pool_endpoint(request: Request, pool: PoolCreate, _=Depends(verify_token)):
    """Create a new storage pool, mounted on the host and persisted in its fstab.

    409 when a selected device is a system device, in use, holds data without
    ``force``, or no longer matches its ``device_identities`` entry, or when
    the mountpoint directory holds files; 400 for invalid input, including
    ``confirm_name != name`` and ``force`` without a ``device_identities``
    entry for every selected device.
    """
    validate_pool_name(pool.name)
    if pool.confirm_name != pool.name:
        raise HTTPException(status_code=400, detail="confirm_name must match the pool name")

    return await _call(pools.create_pool, pool)


@router.get("/pools/{name}", response_model=PoolInfo)
async def get_pool_endpoint(name: str, _=Depends(verify_token)):
    """Get pool details."""
    # Validate pool name
    validated_name = validate_pool_name(name)

    return await _call(pools.get_pool, validated_name)


@router.delete("/pools/{name}")
@limiter.limit("5/minute")  # Rate limit pool deletion
async def delete_pool_endpoint(request: Request, name: str, _=Depends(verify_token)):
    """Forget a pool: unmount it on the host and drop its fstab entry.

    The disks are not wiped; the filesystem can be mounted again later.
    """
    # Validate pool name
    validated_name = validate_pool_name(name)

    await _call(pools.delete_pool, validated_name)
    return {"status": "deleted"}


@router.post("/pools/{name}/mount", response_model=PoolInfo)
@limiter.limit("10/minute")
async def mount_pool_endpoint(request: Request, name: str, _=Depends(verify_token)):
    """Mount a pool on the host by UUID and keep its managed fstab entry.

    409 when the mountpoint directory holds files (they would be hidden).
    The pool's apps that could not start without it are started in the
    background; the response does not wait for them.
    """
    validated_name = validate_pool_name(name)

    return await _call(pools.mount_pool, validated_name)


@router.post("/pools/{name}/unmount", response_model=PoolInfo)
@limiter.limit("10/minute")
async def unmount_pool_endpoint(request: Request, name: str, _=Depends(verify_token)):
    """Unmount a pool on the host and remove its managed fstab entry.

    409 while host processes or running containers use the pool.
    """
    validated_name = validate_pool_name(name)

    return await _call(pools.unmount_pool, validated_name)


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
