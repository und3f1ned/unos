import asyncio
import subprocess
from collections.abc import Callable
from typing import Any, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from ..auth.utils import verify_token
from ..security.validators import (
    sanitize_error_message,
    validate_pool_name,
    validate_volume_name,
)
from ..shares import service as shares_service
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
    """Map storage errors to HTTP errors (404 / 409 / 400 / 503 / 500)."""
    if isinstance(exc, pools.PoolNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, pools.PoolConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, pools.DockerUnavailableError):
        return HTTPException(status_code=503, detail=sanitize_error_message(exc))
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


async def _change_pools(func: Callable[..., T], *args: Any) -> T:
    """_call for an operation that can add or remove a managed fstab entry, then apply smb.conf.

    The managed pools decide which SMB shares are served (ADR-0248). The apply
    runs also when the operation failed, which may have changed the fstab
    before it failed; an unchanged rendering writes nothing, and the apply
    never changes the answer.
    """
    try:
        return await _call(func, *args)
    finally:
        await shares_service.apply_after_pool_change()


@router.get("/disks", response_model=list[DiskInfo])
async def list_disks_endpoint(_=Depends(verify_token)):
    """List physical disks with their host state (system, in use, data)."""
    return await _call(disks.list_disks)


@router.get("/disks/{name}/smart", response_model=SmartData)
async def get_disk_smart(name: str, _=Depends(verify_token)):
    """SMART data of a whole disk of the host, by its name in GET /storage/disks.

    400 for anything else (a partition, a RAID array, the boot SD card or
    eMMC, a virtual device, a name such as null or st0); 500 when the host's
    disks cannot be listed. smartctl is not run then.
    """
    # Both block, off the event loop: lsblk on the host, and smartctl, which
    # can take many seconds (or spin up a sleeping disk); get_smart_data
    # bounds it with a timeout.
    device = await _call(disks.whole_disk_device, name)
    return await _call(disks.get_smart_data, device)


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
    entry for every selected device. Then smb.conf is applied again: SMB
    shares that a forgotten pool of this name kept are served (ADR-0248).
    """
    validate_pool_name(pool.name)
    if pool.confirm_name != pool.name:
        raise HTTPException(status_code=400, detail="confirm_name must match the pool name")

    return await _change_pools(pools.create_pool, pool)


@router.get("/pools/{name}", response_model=PoolInfo)
async def get_pool_endpoint(name: str, _=Depends(verify_token)):
    """Get pool details."""
    # Validate pool name
    validated_name = validate_pool_name(name)

    return await _call(pools.get_pool, validated_name)


@router.delete("/pools/{name}")
@limiter.limit("5/minute")  # Rate limit pool deletion
async def delete_pool_endpoint(request: Request, name: str, _=Depends(verify_token)):
    """Forget a pool: unmount it on the host and drop its fstab entry and empty mountpoint.

    The disks are not wiped; the filesystem is listed again as an unmounted
    pool and can be mounted later. Refused with 409 as an unmount is. As
    after an unmount, its SMB shares stop being served (ADR-0248).
    """
    # Validate pool name
    validated_name = validate_pool_name(name)

    await _change_pools(pools.delete_pool, validated_name)
    return {"status": "deleted"}


@router.post("/pools/{name}/mount", response_model=PoolInfo)
@limiter.limit("10/minute")
async def mount_pool_endpoint(request: Request, name: str, _=Depends(verify_token)):
    """Mount a pool on the host by UUID and keep its managed fstab entry.

    409 when the mountpoint directory holds files (they would be hidden).
    The pool's apps that could not start without it are started in the
    background; the response does not wait for them. Its SMB shares are
    served again before it answers (ADR-0248).
    """
    validated_name = validate_pool_name(name)

    return await _change_pools(pools.mount_pool, validated_name)


@router.post("/pools/{name}/unmount", response_model=PoolInfo)
@limiter.limit("10/minute")
async def unmount_pool_endpoint(request: Request, name: str, _=Depends(verify_token)):
    """Unmount a pool on the host and remove its managed fstab entry.

    The copies of its mount that services keep in mount namespaces of their
    own are unmounted with it (ADR-0269). 409 while host processes or running
    containers use the pool, such as smbd for an SMB client that has one of
    its shares open, and when the mount namespace of another process has it
    mounted elsewhere after the host's umount; the pool is then mounted
    again. Before it answers, smb.conf no longer serves the pool's shares,
    and their open connections are closed (ADR-0248).
    """
    validated_name = validate_pool_name(name)

    return await _change_pools(pools.unmount_pool, validated_name)


@router.get("/pools/{pool_name}/volumes", response_model=list[VolumeInfo])
async def list_volumes_endpoint(pool_name: str, _=Depends(verify_token)):
    """List the volumes (subvolumes in the top directory) of a pool.

    404 for an unknown pool, 409 when it is not mounted or is mounted at a
    subvolume other than the top level. Read-only pools are listed.
    """
    validated_pool = validate_pool_name(pool_name)

    return await _call(pools.list_volumes, validated_pool)


@router.post("/pools/{pool_name}/volumes", response_model=VolumeInfo)
@limiter.limit("10/minute")  # Rate limit volume creation
async def create_volume_endpoint(request: Request, pool_name: str, volume: VolumeCreate, _=Depends(verify_token)):
    """Create a volume: a btrfs subvolume at <mountpoint>/<name> of the pool in the URL.

    The body is ``{"name": ..., "quota": bytes or null}``; the quota must be
    between 1 and 2^64 - 2 (422 otherwise; btrfs reads 2^64 - 1 as no
    limit). 404 for an unknown pool. 409, and nothing is created, when the
    pool is not mounted, mounted read-only or not at its top level, the name
    is taken, or a quota is asked for and quotas are not enabled on the
    pool. 409 as well when the quota cannot be set: the new volume is
    removed again, unless the subvolume with its name by then existed
    before it.
    """
    validated_pool = validate_pool_name(pool_name)
    validate_volume_name(volume.name)

    return await _call(pools.create_volume, validated_pool, volume)


@router.delete("/pools/{pool_name}/volumes/{volume_name}")
async def delete_volume_endpoint(pool_name: str, volume_name: str, _=Depends(verify_token)):
    """Delete a volume with everything in it.

    404 for an unknown pool or volume; 409 when the pool is not mounted,
    mounted read-only or not at its top level, or the volume is used by a
    running container (a bind mount, or a Docker volume that binds a folder
    of it), an SMB share or an NFS export, is busy, or holds snapshots; 503
    when Docker cannot be asked about the running containers and their
    volumes. Nothing is deleted then.
    """
    validated_pool = validate_pool_name(pool_name)
    validated_volume = validate_volume_name(volume_name)

    await _call(pools.delete_volume, validated_pool, validated_volume)
    return {"status": "deleted"}


async def _scrub(func: Callable[[str], ScrubStatus], name: str) -> ScrubStatus:
    """Run a btrfs scrub command for a pool in a worker thread (it can block on a busy disk)."""
    validated_name = validate_pool_name(name)
    try:
        return await asyncio.to_thread(func, validated_name)
    except subprocess.TimeoutExpired as e:
        raise HTTPException(
            status_code=500,
            detail=f"btrfs {' '.join(e.cmd[1:3])} did not answer within {pools.SCRUB_COMMAND_TIMEOUT} "
            f"seconds: pool {validated_name} may not be responding",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=sanitize_error_message(e))


@router.post("/pools/{name}/scrub", response_model=ScrubStatus)
async def start_scrub_endpoint(name: str, _=Depends(verify_token)):
    """Start a scrub on a pool."""
    return await _scrub(pools.start_scrub, name)


@router.get("/pools/{name}/scrub", response_model=ScrubStatus)
async def get_scrub_status_endpoint(name: str, _=Depends(verify_token)):
    """Get scrub status for a pool."""
    return await _scrub(pools.get_scrub_status, name)


@router.delete("/pools/{name}/scrub", response_model=ScrubStatus)
async def cancel_scrub_endpoint(name: str, _=Depends(verify_token)):
    """Cancel a running scrub on a pool."""
    return await _scrub(pools.cancel_scrub, name)
