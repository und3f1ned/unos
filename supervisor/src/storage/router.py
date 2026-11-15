import asyncio
from collections.abc import Callable
from typing import Any, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from ..audit.changes import audit_change
from ..audit.schemas import AuditAction, AuditSeverity
from ..auth.models import User
from ..auth.utils import verify_token
from ..security.validators import (
    sanitize_error_message,
    validate_filesystem_uuid,
    validate_pool_name,
    validate_volume_name,
)
from ..shares import service as shares_service
from . import disks, pools, scrubs, spindown
from .schemas import (
    DiskInfo,
    DiskSpindown,
    ImportableFilesystem,
    PoolCreate,
    PoolImport,
    PoolInfo,
    ScrubStatus,
    SmartData,
    SpindownSet,
    VolumeCreate,
    VolumeInfo,
)

T = TypeVar("T")

# Rate limiter for storage endpoints
limiter = Limiter(key_func=get_remote_address)

router = APIRouter(prefix="/storage", tags=["storage"])


def _http_error(exc: Exception) -> HTTPException:
    """Map storage errors to HTTP errors (404 / 409 / 400 / 503 / 500)."""
    if isinstance(exc, pools.PoolPartlyRemovedError):
        return _http_error(exc.cause)
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
    """List physical disks with their host state (system, in use, data), their power state, read
    without waking them, and their idle timeout (ADR-0378)."""
    return await _call(disks.list_disks)


@router.put("/disks/{name}/spindown", response_model=DiskSpindown)
async def set_disk_spindown(request: Request, name: str, body: SpindownSet, user: User = Depends(verify_token)):
    """Give a whole data disk of GET /disks an idle timeout, ``minutes`` of SPINDOWN_MINUTES, 0 for
    off (ADR-0378): hdparm -S on the host now, which spins a sleeping disk up, and UNOS's udev
    rule, which sets it again whenever the disk appears.

    400 for a name that is no disk of this system, a boot or virtual device or a partition; 409 for
    the system disk, an NVMe disk, a disk without a serial that a udev rule can match or one that
    another disk reports too, a disk that udev knows by another serial, and a host without hdparm;
    422 for minutes that are not offered; 500 when hdparm fails or the rule cannot be read (nothing
    changed then), and when the rule cannot be written after hdparm set the timeout, which the
    audit log records as failed. A change of the rule is audited as settings_changed.
    """
    try:
        change = await asyncio.to_thread(spindown.set_timeout, name, body.minutes)
    except HTTPException:
        raise
    except spindown.RuleNotWritten as exc:
        await audit_change(
            AuditAction.SETTINGS_CHANGED, request, user.username, resource_type="disk",
            resource_id=exc.change.name, details=f"{exc.change.what} until it restarts; then: {exc.reason}",
            success=False,
        )
        raise HTTPException(status_code=500, detail=sanitize_error_message(RuntimeError(exc.detail))) from exc
    except Exception as exc:
        raise _http_error(exc) from exc
    if change.changed:
        await audit_change(
            AuditAction.SETTINGS_CHANGED, request, user.username, resource_type="disk",
            resource_id=change.name, details=change.what,
        )
    return DiskSpindown(name=change.name, minutes=change.minutes)


@router.get("/disks/{name}/smart", response_model=SmartData)
async def get_disk_smart(name: str, _=Depends(verify_token)):
    """SMART data of a whole disk of the host, by its name in GET /storage/disks.

    A disk that sleeps stays asleep: its answer has the power state standby
    and no data (ADR-0378). 400 for anything else (a partition, a RAID array,
    the boot SD card or eMMC, a virtual device, a name such as null or st0);
    500 when the host's disks cannot be listed. smartctl is not run then.
    """
    # Both block, off the event loop: lsblk on the host, and smartctl, which
    # can take many seconds; get_smart_data bounds it with a timeout.
    device = await _call(disks.whole_disk_device, name)
    return await _call(disks.get_smart_data, device)


@router.get("/pools", response_model=list[PoolInfo])
async def list_pools_endpoint(_=Depends(verify_token)):
    """List the storage pools (ADR-0283).

    Of several filesystems with one label, only the one mounted or managed
    under it is a pool. A pool found on the disks that is on a boot or
    virtual device or on the system disk is left out: Mount refuses it.
    """
    return await _call(pools.list_pools)


@router.post("/pools", response_model=PoolInfo)
@limiter.limit("5/minute")  # Rate limit pool creation
async def create_pool_endpoint(request: Request, pool: PoolCreate, user: User = Depends(verify_token)):
    """Create a new storage pool, mounted on the host and persisted in its fstab.

    409 when a selected device is a system device, in use, holds data without
    ``force``, or no longer matches its ``device_identities`` entry, or when
    the mountpoint directory holds files; 400 for invalid input, including
    ``confirm_name != name`` and ``force`` without a ``device_identities``
    entry for every selected device. Then smb.conf is applied again: SMB
    shares that a forgotten pool of this name kept are served (ADR-0248).
    The audit log gets pool_created; as failed, with the formatted devices,
    when a step after mkfs fails, which answers 500 (ADR-0350).
    """
    validate_pool_name(pool.name)
    if pool.confirm_name != pool.name:
        raise HTTPException(status_code=400, detail="confirm_name must match the pool name")

    try:
        info = await _change_pools(pools.create_pool, pool)
    except HTTPException as exc:
        if isinstance(exc.__cause__, pools.PoolFormattedError):
            await audit_change(
                AuditAction.POOL_CREATED, request, user.username, resource_type="pool", resource_id=pool.name,
                details=f"Formatted {', '.join(pool.devices)} for the pool {pool.name} ({pool.raid_level}); "
                f"then: {exc.detail}",
                success=False,
            )
        raise
    await audit_change(
        AuditAction.POOL_CREATED, request, user.username, resource_type="pool", resource_id=info.name,
        details=f"Created the pool {info.name} ({pool.raid_level}) on {', '.join(pool.devices)}",
    )
    return info


@router.get("/pools/{name}", response_model=PoolInfo)
async def get_pool_endpoint(name: str, _=Depends(verify_token)):
    """Get pool details."""
    # Validate pool name
    validated_name = validate_pool_name(name)

    return await _call(pools.get_pool, validated_name)


@router.delete("/pools/{name}")
@limiter.limit("5/minute")  # Rate limit pool deletion
async def delete_pool_endpoint(request: Request, name: str, user: User = Depends(verify_token)):
    """Forget a pool: unmount it on the host and drop its fstab entry and empty mountpoint.

    The disks are not wiped; the filesystem is listed again as an unmounted
    pool and can be mounted later. Refused with 409 or 503 as an unmount is. As
    after an unmount, its SMB shares stop being served (ADR-0248). The audit log
    gets pool_deleted when there was something to remove; as failed, with what
    was done, when a step fails after the pool was unmounted or its fstab
    entry dropped (ADR-0350).
    """
    # Validate pool name
    validated_name = validate_pool_name(name)

    try:
        changed = await _change_pools(pools.delete_pool, validated_name)
    except HTTPException as exc:
        if isinstance(exc.__cause__, pools.PoolPartlyRemovedError):
            await audit_change(
                AuditAction.POOL_DELETED, request, user.username, resource_type="pool",
                resource_id=validated_name, details=f"{exc.__cause__.done}; then: {exc.detail}",
                severity=AuditSeverity.WARNING, success=False,
            )
        raise
    if changed:
        await audit_change(
            AuditAction.POOL_DELETED, request, user.username, resource_type="pool", resource_id=validated_name,
            details=f"Removed the pool {validated_name}; its disks and their data were kept",
            severity=AuditSeverity.WARNING,
        )
    return {"status": "deleted"}


@router.get("/filesystems/importable", response_model=list[ImportableFilesystem])
async def list_importable_filesystems_endpoint(_=Depends(verify_token)):
    """The btrfs filesystems on the host's disks that can be imported as pools (ADR-0283).

    Those that are neither the host's system filesystem nor mounted on the
    host, that are not the pool their label names (``label_problem``): they
    have none, it is not a valid pool name, or another btrfs filesystem has
    it too and the pool of that name is the other one or neither; and that
    are on no boot or virtual device and not on the system disk. The pools
    are listed by GET /pools, and Mount imports them. 500 when btrfs, the
    host's mount table, its fstab or lsblk cannot be read.
    """
    return await _call(pools.importable_filesystems)


@router.post("/filesystems/{uuid}/import", response_model=PoolInfo)
@limiter.limit("10/minute")
async def import_filesystem_endpoint(
    request: Request, uuid: str, body: PoolImport, _=Depends(verify_token)
):
    """Import a filesystem of GET /filesystems/importable as the pool ``name`` (ADR-0283).

    Its disks are not formatted: it is mounted by UUID at /mnt/pools/<name>,
    gets the name as its label through the mount and keeps a managed fstab
    entry, like every managed pool. 400 for an invalid UUID or name; 404 when
    no btrfs filesystem has the UUID; 409, and nothing is changed, when the
    filesystem holds the host's system, is on a boot or virtual device or on
    the system disk, is mounted, misses devices, is the pool its label names
    or is managed under another name, when another filesystem of the host has
    the name as its label or UNOS manages a pool of that name, when SMB
    shares, NFS exports or containers of an earlier pool still use the name,
    when the mountpoint is in use or holds files, and when the filesystem
    mounts another subvolume than its top level by default; 503 when Docker
    does not answer. Then smb.conf is applied again (ADR-0248).
    """
    validated_uuid = validate_filesystem_uuid(uuid)
    validated_name = validate_pool_name(body.name)

    return await _change_pools(pools.import_pool, validated_uuid, validated_name)


@router.post("/pools/{name}/mount", response_model=PoolInfo)
@limiter.limit("10/minute")
async def mount_pool_endpoint(request: Request, name: str, _=Depends(verify_token)):
    """Mount a pool on the host by UUID and keep its managed fstab entry.

    This imports a pool found on the host's disks, too (ADR-0283). 409 when
    the mountpoint directory holds files (they would be hidden), when the
    filesystem is on a boot or virtual device or on the system disk, and when
    it mounts another subvolume than its top level by default: it is
    unmounted again. The pool's apps that could not start without it
    are started in the background; the response does not wait for them. Its
    SMB shares are served again before it answers (ADR-0248).
    """
    validated_name = validate_pool_name(name)

    return await _change_pools(pools.mount_pool, validated_name)


@router.post("/pools/{name}/unmount", response_model=PoolInfo)
@limiter.limit("10/minute")
async def unmount_pool_endpoint(request: Request, name: str, _=Depends(verify_token)):
    """Unmount a pool on the host and remove its managed fstab entry.

    The copies of its mount that services keep in mount namespaces of their
    own, whose root is the host's, are unmounted with it (ADR-0288). 409
    while host processes or running containers use the pool, such as smbd
    for an SMB client that has one of its shares open, and when the mount
    namespace of another process still has it mounted after the host's
    umount: elsewhere, or in a namespace with a root of its own, such as a
    container that binds /mnt/pools; the pool is then mounted again. 503,
    with nothing changed, when Docker cannot be asked which containers use
    the pool (ADR-0344). Before it answers, smb.conf no longer serves the
    pool's shares, and their open connections are closed (ADR-0248).
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
async def create_volume_endpoint(
    request: Request, pool_name: str, volume: VolumeCreate, user: User = Depends(verify_token)
):
    """Create a volume: a btrfs subvolume at <mountpoint>/<name> of the pool in the URL.

    The body is ``{"name": ..., "quota": bytes or null}``; the quota must be
    between 1 and 2^64 - 2 (422 otherwise; btrfs reads 2^64 - 1 as no
    limit). 404 for an unknown pool. 409, and nothing is created, when the
    pool is not mounted, mounted read-only or not at its top level, the name
    is taken, or a quota is asked for and quotas are not enabled on the
    pool. 409 as well when the quota cannot be set: the new volume is
    removed again, unless the subvolume with its name by then existed
    before it. The audit log gets volume_created (ADR-0350); as failed when
    the volume is kept without (or perhaps without) its quota, which
    answers 500.
    """
    validated_pool = validate_pool_name(pool_name)
    validated_name = validate_volume_name(volume.name)

    try:
        info = await _call(pools.create_volume, validated_pool, volume)
    except HTTPException as exc:
        if isinstance(exc.__cause__, pools.VolumeKeptWithoutQuotaError):
            await audit_change(
                AuditAction.VOLUME_CREATED, request, user.username, resource_type="volume",
                resource_id=f"{validated_pool}/{validated_name}",
                details=f"Created the volume {validated_name} in the pool {validated_pool}; then: {exc.detail}",
                success=False,
            )
        raise
    quota = "" if info.quota is None else f", with a quota of {info.quota} bytes"
    await audit_change(
        AuditAction.VOLUME_CREATED, request, user.username, resource_type="volume",
        resource_id=f"{info.pool}/{info.name}",
        details=f"Created the volume {info.name} in the pool {info.pool}{quota}",
    )
    return info


@router.delete("/pools/{pool_name}/volumes/{volume_name}")
async def delete_volume_endpoint(
    request: Request, pool_name: str, volume_name: str, user: User = Depends(verify_token)
):
    """Delete a volume with everything in it.

    404 for an unknown pool or volume; 409 when the pool is not mounted,
    mounted read-only or not at its top level, or the volume is used by a
    running container (a bind mount, or a Docker volume that binds a folder
    of it), an SMB share or an NFS export, is busy, or holds snapshots; 503
    when Docker cannot be asked about the running containers and their
    volumes. Nothing is deleted then. The audit log gets volume_deleted (ADR-0350).
    """
    validated_pool = validate_pool_name(pool_name)
    validated_volume = validate_volume_name(volume_name)

    await _call(pools.delete_volume, validated_pool, validated_volume)
    await audit_change(
        AuditAction.VOLUME_DELETED, request, user.username, resource_type="volume",
        resource_id=f"{validated_pool}/{validated_volume}",
        details=f"Deleted the volume {validated_volume} of the pool {validated_pool} with everything in it",
        severity=AuditSeverity.WARNING,
    )
    return {"status": "deleted"}


async def _scrub(func: Callable[[str], ScrubStatus], name: str, *, reads: bool = False) -> ScrubStatus:
    """Run a btrfs scrub command for a pool in a worker thread (it can block on a busy disk), and
    note its answer for the events socket (storage/scrubs.py, ADR-0367); for a read of the status
    (``reads``), also why it failed."""
    validated_name = validate_pool_name(name)
    try:
        status = await asyncio.to_thread(func, validated_name)
    except pools.PoolConflictError as e:  # a start on a pool that is not mounted
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        detail = scrubs.failure(e, validated_name)
        if reads:
            scrubs.note(validated_name, detail)
        raise HTTPException(status_code=500, detail=detail)
    scrubs.note(validated_name, status)
    return status


@router.post("/pools/{name}/scrub", response_model=ScrubStatus)
async def start_scrub_endpoint(name: str, _=Depends(verify_token)):
    """Start a scrub on a pool; answers once btrfs runs it, with its status then (ADR-0071).

    409, and no btrfs, when the pool is not mounted (pools.scrub_start). 500 with btrfs's error
    when it does not start, or when btrfs does not
    answer within SCRUB_COMMAND_TIMEOUT. A scrub that started is answered
    running when its status cannot be read yet or says idle (ADR-0313).
    """
    return await _scrub(pools.start_scrub, name)


@router.get("/pools/{name}/scrub", response_model=ScrubStatus)
async def get_scrub_status_endpoint(name: str, _=Depends(verify_token)):
    """The pool's scrub as btrfs reports it: its progress while it runs, then its result (ADR-0313).

    idle when no scrub is recorded and none runs; 500 with btrfs's error when it
    cannot report the status, or does not answer within SCRUB_COMMAND_TIMEOUT.
    """
    return await _scrub(pools.get_scrub_status, name, reads=True)


@router.delete("/pools/{name}/scrub", response_model=ScrubStatus)
async def cancel_scrub_endpoint(name: str, _=Depends(verify_token)):
    """Cancel a running scrub; answers its status after btrfs's cancel (aborted once it stopped).

    A scrub that no longer runs is no error: the answer is its status. A
    cancelled scrub whose status cannot be read yet or says idle is answered
    aborted (ADR-0313).
    """
    return await _scrub(pools.cancel_scrub, name)
