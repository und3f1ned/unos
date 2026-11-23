import asyncio
import logging
from collections.abc import Callable
from typing import Annotated, Any, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Path, Request

from ..audit.changes import audit_change
from ..audit.schemas import AuditAction, AuditSeverity
from ..auth.models import User
from ..auth.utils import verify_token
from ..openapi import BODY, RATE_LIMITED, answer, errors
from ..rate_limits import client_limiter
from ..security.validators import (
    sanitize_error_message,
    validate_filesystem_uuid,
    validate_pool_name,
    validate_volume_name,
)
from ..shares import iscsi
from ..shares import service as shares_service
from ..system import docker_data
from . import balances, devices, disks, pools, scrubs, spindown, volume_usage
from .schemas import (
    BalanceStart,
    BalanceStatus,
    DiskAdd,
    DiskInfo,
    DiskRemovalStatus,
    DiskRemove,
    DiskSpindown,
    ImportableFilesystem,
    PoolCreate,
    PoolCreated,
    PoolDevices,
    PoolImport,
    PoolInfo,
    ScrubStatus,
    SmartData,
    SpindownSet,
    VolumeCreate,
    VolumeInfo,
    VolumeUsageHistory,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Rate limiter for storage endpoints
limiter = client_limiter()

router = APIRouter(prefix="/storage", tags=["storage"])

# The path parameters and the error texts that several routes share (ADR-0417).
DiskName = Annotated[str, Path(description="The disk's name, as GET /storage/disks lists it, such as sda or nvme0n1.")]
PoolName = Annotated[str, Path(description="The pool's name, such as data.")]
VolumeName = Annotated[str, Path(description="The volume's name, such as documents.")]

_POOL_400 = {400: "The pool's name is not a valid pool name."}
_POOL_404 = {404: "No pool has that name."}
_VOLUME_400 = {400: "The pool's or the volume's name is not valid."}
_VOLUME_404 = {404: "No pool, or no volume of the pool, has that name."}
_TOP_LEVEL_409 = "The pool is not mounted, or is mounted at a subvolume other than its top level."
_BTRFS_500 = "btrfs or the host's mount table cannot be read."
_DOCKER_503 = "Docker cannot be asked about the running containers and their volumes; nothing changed."


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
        error = _http_error(exc)
        if error.status_code >= 500:  # a missing pool, a conflict or a bad value is the caller's, not a failure
            logger.exception("A storage request failed")
        raise error from exc


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


@router.get(
    "/disks", response_model=list[DiskInfo], summary="List the disks",
    responses=errors({500: "lsblk cannot list the host's disks."}),
)
async def list_disks_endpoint(_=Depends(verify_token)):
    """The host's whole disks, the system disk included, with what they hold and whether they are in
    use, their power state, read without waking them, and their idle timeout. An SD card or eMMC
    that holds part of the system or is mounted or used as swap outside a pool, an eMMC's boot and
    RPMB areas, and virtual devices are left out; a free SD card or eMMC is listed with its type.
    \f
    ADR-0378, storage.free-mmc-as-data-disk.
    """
    return await _call(disks.list_disks)


@router.put(
    "/disks/{name}/spindown", response_model=DiskSpindown, summary="Set a disk's idle timeout",
    responses=errors(BODY, {
        400: "The name is no disk of this system, or a boot or virtual device or a partition.",
        409: "The disk can have no idle timeout: the system disk, an NVMe disk, an SD card or eMMC, a disk without "
             "a serial that a "
             "udev rule can match or whose serial another disk reports too, one that udev knows by another "
             "serial, or a host without hdparm.",
        500: "The disks or the udev rule cannot be read or hdparm fails, and nothing changed; or the rule cannot "
             "be written after hdparm set the timeout, which the audit log records as failed.",
    }),
)
async def set_disk_spindown(request: Request, name: DiskName, body: SpindownSet, user: User = Depends(verify_token)):
    """Give a whole data disk of GET /storage/disks an idle timeout of ``minutes``, 0 for off:
    hdparm -S on the host now, which spins a sleeping disk up; the disk's own automatic offline scans
    turned off for a timeout, and on again when UNOS turned them off; and UNOS's udev rule, which
    sets the timeout again whenever the disk appears. When the scans cannot be switched, the timeout
    is set all the same and ``warning`` says so. A change of the rule or of the scans is written to
    the audit log as settings_changed.
    \f
    ADR-0378. ``minutes`` is one of SPINDOWN_MINUTES (422 otherwise). The udev rule also records the
    automatic offline data collection that UNOS turned off.
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
        error = _http_error(exc)
        if error.status_code >= 500:
            logger.exception("Could not set the disk's spindown")
        raise error from exc
    if change.changed or change.scans:  # the rule, or the disk's own offline data collection
        await audit_change(
            AuditAction.SETTINGS_CHANGED, request, user.username, resource_type="disk",
            resource_id=change.name, details=change.what,
        )
    return DiskSpindown(name=change.name, minutes=change.minutes, warning=change.warning)


@router.get(
    "/disks/{name}/smart", response_model=SmartData, summary="Read a disk's SMART data",
    responses=errors({
        400: "The name is no whole disk of the host: a partition, a RAID array, the boot SD card or eMMC, a "
             "virtual device, or a name such as null or st0. smartctl does not run.",
        500: "The host's disks cannot be listed, and smartctl does not run; or smartctl cannot be started.",
    }),
)
async def get_disk_smart(name: DiskName, _=Depends(verify_token)):
    """SMART data of a whole disk of the host, by its name in GET /storage/disks.

    A disk in standby stays asleep: its answer has the power state standby and no data. A
    smartctl that does not answer in time gives healthy false and the error in raw_data. An SD
    card or eMMC has no SMART: healthy null, the power state active and no data, and smartctl
    does not run.
    \f
    ADR-0378, storage.free-mmc-as-data-disk.
    """
    # Both block, off the event loop: lsblk on the host, and smartctl, which
    # can take many seconds; get_smart_data bounds it with a timeout.
    device = await _call(disks.whole_disk_device, name)
    return await _call(disks.get_smart_data, device)


@router.get(
    "/pools", response_model=list[PoolInfo], summary="List the pools",
    responses=errors({500: "btrfs cannot list the host's filesystems."}),
)
async def list_pools_endpoint(_=Depends(verify_token)):
    """The pools, mounted or not: the btrfs filesystems of the host whose label is a valid pool name.

    Of several filesystems with one label, only the one mounted or managed
    under it is a pool. A pool found on the disks that is on a boot or
    virtual device or on the system disk is left out: Mount refuses it.
    \f
    ADR-0283.
    """
    return await _call(pools.list_pools)


@router.post(
    "/pools", response_model=PoolCreated, summary="Create a pool",
    responses=errors(RATE_LIMITED, BODY, {
        400: "Invalid input, such as an invalid name or device path, confirm_name other than name, too few "
             "devices for raid_level, a device that is no disk or partition of the host or a boot or virtual "
             "device, or force without a device_identities entry for every device.",
        409: "A device is a system device, in use, holds data without force, or no longer holds the disk of its "
             "device_identities entry; or another btrfs filesystem has the name as its label, or the mountpoint "
             "is in use, holds files or is in a host fstab line that UNOS does not manage.",
        500: "mkfs.btrfs or another host command fails; after mkfs, the disks stay formatted.",
    }),
)
@limiter.limit("5/minute")  # Rate limit pool creation
async def create_pool_endpoint(request: Request, pool: PoolCreate, user: User = Depends(verify_token)):
    """Format the devices as a new pool, mount it on the host at /mnt/pools/<name> and keep it in
    the host's fstab: a managed pool. The devices are checked again right before they are formatted.

    Then smb.conf is applied again: SMB shares that a forgotten pool of this name kept are served.
    The first managed pool of a host whose Docker's data is not on a pool gets it: the move starts in
    a unit of the host, which stops UNOS and the apps until Docker answers from the pool
    (docker_move). The audit log gets pool_created, which says whether the move started; as failed,
    with the formatted devices, when a step after mkfs fails.
    \f
    ADR-0248, ADR-0350, ADR-0487.
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
    move = await docker_data.after_pool_created(info.name, user.username)
    details = f"Created the pool {info.name} ({pool.raid_level}) on {', '.join(pool.devices)}"
    await audit_change(
        AuditAction.POOL_CREATED, request, user.username, resource_type="pool", resource_id=info.name,
        details=details if move is None else f"{details}; {move.audit_text()}",
    )
    return PoolCreated(
        **info.model_dump(),
        docker_move=None if move is None else "started" if move.started else "not_started",
        docker_move_error=None if move is None else move.error,
    )


@router.get(
    "/pools/{name}", response_model=PoolInfo, summary="Read a pool",
    responses=errors(_POOL_400, _POOL_404, {500: "btrfs cannot list the host's filesystems."}),
)
async def get_pool_endpoint(name: PoolName, _=Depends(verify_token)):
    """A pool's disks, size, mount and health."""
    # Validate pool name
    validated_name = validate_pool_name(name)

    return await _call(pools.get_pool, validated_name)


@router.delete(
    "/pools/{name}", summary="Forget a pool",
    responses={
        **answer({"status": "deleted"}, "Always deleted, also for a name that is no pool, which changes nothing."),
        **errors(RATE_LIMITED, _POOL_400, {
            409: "Docker keeps its data on the pool, or a move of it there runs (the detail names Docker); host "
                 "processes, running containers or the off-device copy use the pool, or it is still mounted "
                 "elsewhere after the host's umount; nothing changed, or it is mounted again (or left unmounted "
                 "when that fails).",
            500: "The host's mount table, or Docker's or containerd's configuration, cannot be read, or umount, "
                 "the fstab or another host command fails.",
            503: "The pool is mounted, and Docker cannot be asked which running containers use it; nothing "
                 "changed.",
        }),
    },
)
@limiter.limit("5/minute")  # Rate limit pool deletion
async def delete_pool_endpoint(request: Request, name: PoolName, user: User = Depends(verify_token)):
    """Forget a pool: unmount it on the host and drop its fstab entry and empty mountpoint.

    The disks are not wiped; the filesystem is listed again as an unmounted
    pool and can be mounted later. As after an unmount, its SMB shares stop
    being served. The audit log gets pool_deleted when there was something to
    remove; as failed, with what was done, when a step fails after the pool
    was unmounted or its fstab entry dropped.
    \f
    Refused as an unmount is. ADR-0248, ADR-0350.
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


@router.get(
    "/filesystems/importable", response_model=list[ImportableFilesystem],
    summary="List the filesystems that can be imported as pools",
    responses=errors({500: "btrfs, the host's mount table, its fstab or lsblk cannot be read."}),
)
async def list_importable_filesystems_endpoint(_=Depends(verify_token)):
    """The btrfs filesystems on the host's disks that can be imported as pools.

    Those that are neither the host's system filesystem nor mounted on the
    host, that are not the pool their label names (``label_problem``): they
    have none, it is not a valid pool name, or another btrfs filesystem has
    it too and the pool of that name is the other one or neither; and that
    are on no boot or virtual device and not on the system disk. The pools
    are listed by GET /storage/pools, and Mount imports them.
    \f
    ADR-0283.
    """
    return await _call(pools.importable_filesystems)


@router.post(
    "/filesystems/{uuid}/import", response_model=PoolInfo, summary="Import a filesystem as a pool",
    responses=errors(RATE_LIMITED, BODY, {
        400: "The UUID or the pool's name is not valid.",
        404: "No btrfs filesystem of the host has the UUID.",
        409: "The filesystem or the name is refused for one of the reasons in the description; nothing changed.",
        500: "A host command fails, or the host's filesystems, mounts, fstab, SMB shares or NFS exports cannot "
             "be read.",
        503: "Docker cannot be asked whether containers keep data on a pool of that name; nothing changed.",
    }),
)
@limiter.limit("10/minute")
async def import_filesystem_endpoint(
    request: Request,
    uuid: Annotated[str, Path(description="The filesystem's UUID, as GET /storage/filesystems/importable lists it.")],
    body: PoolImport,
    user: User = Depends(verify_token),
):
    """Import a filesystem of GET /storage/filesystems/importable as the managed pool ``name``.

    Its disks are not formatted: it is mounted by UUID at /mnt/pools/<name>,
    gets the name as its label through the mount and keeps a managed fstab
    entry, like every managed pool. Then smb.conf is applied again. The audit
    log gets pool_imported.

    Refused with 409, and nothing is changed, when the filesystem holds the
    host's system, is on a boot or virtual device or on the system disk, is
    mounted, misses devices, is the pool its label names or is managed under
    another name; when another filesystem of the host has the name as its
    label or UNOS manages a pool of that name; when SMB shares, NFS exports
    or containers of an earlier pool still use the name; when the mountpoint
    is in use, in a host fstab line that UNOS does not manage, or holds
    files; and when the filesystem mounts another subvolume than its top
    level by default.
    \f
    ADR-0283, ADR-0248.
    """
    validated_uuid = validate_filesystem_uuid(uuid)
    validated_name = validate_pool_name(body.name)

    info = await _change_pools(pools.import_pool, validated_uuid, validated_name)
    await audit_change(
        AuditAction.POOL_IMPORTED, request, user.username, resource_type="pool", resource_id=validated_name,
        details=f"Imported the filesystem {validated_uuid} as the pool {validated_name}",
    )
    return info


@router.post(
    "/pools/{name}/mount", response_model=PoolInfo, summary="Mount a pool",
    responses=errors(RATE_LIMITED, _POOL_400, _POOL_404, {
        409: "Several filesystems have the pool's label, another filesystem is mounted at its mountpoint, a disk "
             "is missing or is a boot, virtual or system disk, the mountpoint holds files or is in a host fstab "
             "line that UNOS does not manage, or the filesystem mounts another subvolume than its top level "
             "(it is unmounted again).",
        500: "The host's mount table or filesystems cannot be read, or the mount or another host command fails.",
    }),
)
@limiter.limit("10/minute")
async def mount_pool_endpoint(request: Request, name: PoolName, user: User = Depends(verify_token)):
    """Mount a pool on the host by UUID and keep its managed fstab entry.

    This imports a pool found on the host's disks, too. A pool that is
    mounted and kept already is left as it is. The pool's apps that could
    not start without it are started in the background; the response does
    not wait for them. Its SMB shares and iSCSI targets are served again before it
    answers.
    The audit log gets pool_mounted when it mounted the pool or wrote its
    fstab entry.
    \f
    ADR-0283, ADR-0248, ADR-0418, ADR-0402. A mountpoint that holds files is refused: they would be hidden.
    """
    validated_name = validate_pool_name(name)

    info, changed = await _change_pools(pools.mount_pool_changed, validated_name)
    if changed:
        await audit_change(
            AuditAction.POOL_MOUNTED, request, user.username, resource_type="pool", resource_id=validated_name,
            details=f"Mounted the pool {validated_name}",
        )
    # The iSCSI targets of the pool's volumes, not served while it was not mounted (ADR-0418).
    if iscsi.pool_user(validated_name):
        await iscsi.restore_targets()
    return info


@router.post(
    "/pools/{name}/unmount", response_model=PoolInfo, summary="Unmount a pool",
    responses=errors(RATE_LIMITED, _POOL_400, _POOL_404, {
        409: "Docker keeps its data on the pool, or a move of it there runs (the detail names Docker); host "
             "processes, running containers or the off-device copy use the pool, such as smbd for an SMB client "
             "with one of its shares open; or another mount namespace still has it mounted after the host's "
             "umount, and it is mounted again (or left unmounted when that fails).",
        500: "The host's mount table or filesystems, or Docker's or containerd's configuration, cannot be read, "
             "or umount, the fstab or another host command fails.",
        503: "The pool is mounted, and Docker cannot be asked which running containers use it; nothing changed.",
    }),
)
@limiter.limit("10/minute")
async def unmount_pool_endpoint(request: Request, name: PoolName, user: User = Depends(verify_token)):
    """Unmount a pool on the host and remove its managed fstab entry; its data is kept.

    The copies of its mount that services keep in mount namespaces of their
    own, whose root is the host's, are unmounted with it. Another mount
    namespace that still has the pool mounted after the host's umount, such
    as a container that binds /mnt/pools, keeps the pool mounted: it is then
    mounted again on the host. Before it answers, smb.conf no longer serves
    the pool's shares, and their open connections are closed. The audit log
    gets pool_unmounted when the pool was mounted or its fstab entry dropped;
    as failed when the host unmounted it and it could not be mounted again.
    \f
    ADR-0288, ADR-0344, ADR-0248, ADR-0402.
    """
    validated_name = validate_pool_name(name)

    try:
        info, changed = await _change_pools(pools.unmount_pool_changed, validated_name)
    except HTTPException as exc:
        if isinstance(exc.__cause__, pools.PoolLeftUnmountedError):
            await audit_change(
                AuditAction.POOL_UNMOUNTED, request, user.username, resource_type="pool",
                resource_id=validated_name, details=f"Unmounted the pool {validated_name}; then: {exc.detail}",
                severity=AuditSeverity.WARNING, success=False,
            )
        raise
    if changed:
        await audit_change(
            AuditAction.POOL_UNMOUNTED, request, user.username, resource_type="pool", resource_id=validated_name,
            details=f"Unmounted the pool {validated_name}; its data was kept", severity=AuditSeverity.WARNING,
        )
    return info


@router.get(
    "/pools/{pool_name}/volumes", response_model=list[VolumeInfo], summary="List a pool's volumes",
    responses=errors(_POOL_400, _POOL_404, {409: _TOP_LEVEL_409, 500: _BTRFS_500}),
)
async def list_volumes_endpoint(pool_name: PoolName, _=Depends(verify_token)):
    """List the volumes (the subvolumes in the top folder) of a mounted pool, each with its quota use.

    ``used``, ``quota`` and ``usage`` come from the kernel's counters of the volume's quota group.
    The volumes of a read-only pool are listed too.
    \f
    storage.qgroups.
    """
    validated_pool = validate_pool_name(pool_name)

    return await _call(pools.list_volumes, validated_pool)


@router.get(
    "/pools/{pool_name}/volumes/{volume_name}/usage", response_model=VolumeUsageHistory,
    summary="Read a volume's quota use over time",
    responses=errors(_VOLUME_400, _VOLUME_404, {
        409: "The pool is not mounted or not at its top level, or its subvolume list gives the volume's name only "
             "ids of other subvolumes.",
        500: _BTRFS_500,
    }),
)
async def volume_usage_endpoint(pool_name: PoolName, volume_name: VolumeName, _=Depends(verify_token)):
    """A volume's quota use over time: one point per hour of the last 30 days that the
    supervisor kept, oldest first, and the current reading last.
    \f
    storage.volume_usage.
    """
    validated_pool = validate_pool_name(pool_name)
    validated_name = validate_volume_name(volume_name)

    return await _call(volume_usage.usage_history, validated_pool, validated_name)


@router.post(
    "/pools/{pool_name}/volumes", response_model=VolumeInfo, summary="Create a volume",
    responses=errors(RATE_LIMITED, BODY, _VOLUME_400, _POOL_404, {
        409: "The pool is not mounted, mounted read-only or not at its top level, the name is taken, or a quota "
             "is asked for and quotas are off on the pool, and nothing was created; or the quota could not be "
             "set, and the answer says what became of the new volume.",
        500: "btrfs or the host's mount table cannot be read, btrfs fails, or the volume was kept without (or "
             "perhaps without) its quota.",
    }),
)
@limiter.limit("10/minute")  # Rate limit volume creation
async def create_volume_endpoint(
    request: Request, pool_name: PoolName, volume: VolumeCreate, user: User = Depends(verify_token)
):
    """Create a volume: a btrfs subvolume at /mnt/pools/<pool>/<name> of the pool in the URL, with an
    optional quota.

    The quota is between 1 and 2^64 - 2 bytes. When it cannot be set, the
    new volume is removed again, unless the subvolume with its name by then
    existed before it. The audit log gets volume_created; as failed when the
    answer is the 500 of a volume kept without (or perhaps without) its quota.
    A volume kept because its removal failed (500), or whose new subvolume
    cannot be told from others of its name (409), is not written to the
    audit log.
    \f
    ADR-0350. btrfs reads a quota of 2^64 - 1 as no limit; a quota outside the range answers 422.
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


@router.delete(
    "/pools/{pool_name}/volumes/{volume_name}", summary="Delete a volume",
    responses={
        **answer({"status": "deleted"}, "Always deleted: the volume is gone."),
        **errors(_VOLUME_400, _VOLUME_404, {
            409: "The pool is not mounted, mounted read-only or not at its top level, or the volume is used by a "
                 "running container, an SMB share, an NFS export or the off-device copy, is busy, or holds "
                 "snapshots; nothing was deleted.",
            500: "btrfs or the host's mount table cannot be read, btrfs fails, or the containers' folders, the SMB "
                 "shares or the NFS exports cannot be read.",
            503: _DOCKER_503,
        }),
    },
)
async def delete_volume_endpoint(
    request: Request, pool_name: PoolName, volume_name: VolumeName, user: User = Depends(verify_token)
):
    """Delete a volume with everything in it.

    A volume in use is refused: by a running container (a bind mount, or a
    Docker volume that binds a folder of it), an SMB share, an NFS export or
    the off-device copy. Nothing is deleted when the request is refused. The
    audit log gets volume_deleted.
    \f
    ADR-0350.
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
        logger.exception("A scrub request failed")
        detail = scrubs.failure(e, validated_name)
        if reads:
            scrubs.note(validated_name, detail)
        raise HTTPException(status_code=500, detail=detail)
    scrubs.note(validated_name, status)
    return status


@router.post(
    "/pools/{name}/scrub", response_model=ScrubStatus, summary="Start a pool's scrub",
    responses=errors(_POOL_400, {
        409: "The pool is not mounted, or a balance of it runs; btrfs did not run.",
        500: "btrfs does not start the scrub, with its error, or does not answer in time, or the host's mount "
             "table cannot be read.",
    }),
)
async def start_scrub_endpoint(name: PoolName, _=Depends(verify_token)):
    """Start a scrub of a pool; it answers once btrfs runs it, with its status then.

    A scrub that started is answered running when its status cannot be read yet or says idle.
    \f
    ADR-0071, ADR-0313. No btrfs runs for a pool that is not mounted (pools.scrub_start); btrfs
    has SCRUB_COMMAND_TIMEOUT to answer.
    """
    return await _scrub(pools.start_scrub, name)


@router.get(
    "/pools/{name}/scrub", response_model=ScrubStatus, summary="Read a pool's scrub",
    responses=errors(_POOL_400, {
        500: "btrfs cannot report the status, with its error, or does not answer in time.",
    }),
)
async def get_scrub_status_endpoint(name: PoolName, _=Depends(verify_token)):
    """The pool's scrub as btrfs reports it: its progress while it runs, then its result; idle when no
    scrub is recorded and none runs.
    \f
    ADR-0313. btrfs has SCRUB_COMMAND_TIMEOUT to answer.
    """
    return await _scrub(pools.get_scrub_status, name, reads=True)


@router.delete(
    "/pools/{name}/scrub", response_model=ScrubStatus, summary="Cancel a pool's scrub",
    responses=errors(_POOL_400, {
        500: "btrfs fails to cancel the scrub or to report its status, or does not answer in time.",
    }),
)
async def cancel_scrub_endpoint(name: PoolName, _=Depends(verify_token)):
    """Cancel a running scrub; the answer is its status after btrfs's cancel, aborted once it stopped.

    A scrub that no longer runs is no error: the answer is its status. A
    cancelled scrub whose status cannot be read yet or says idle is answered
    aborted.
    \f
    ADR-0313.
    """
    return await _scrub(pools.cancel_scrub, name)


async def _balance(func: Callable[..., T], name: str, *args: Any, reads: bool = False) -> T:
    """Run a balance command for a pool in a worker thread and note its status for the events
    socket (storage/balances.py, ADR-0379); for a read of the status (``reads``), also why it
    failed. 409 for a refusal, 500 with the reason when btrfs or systemd fails."""
    validated_name = validate_pool_name(name)
    try:
        answer = await asyncio.to_thread(func, validated_name, *args)
    except pools.PoolConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        logger.exception("A balance request failed")
        detail = balances.failure(e, validated_name)
        if reads:
            balances.note(validated_name, detail)
        raise HTTPException(status_code=500, detail=detail)
    # Each balance command answers its BalanceStatus, alone or first in a tuple.
    balances.note(validated_name, answer[0] if isinstance(answer, tuple) else answer)  # type: ignore[arg-type]
    return answer



async def _audit_balance(action: AuditAction, request: Request, user: User, name: str, details: str) -> None:
    await audit_change(action, request, user.username, resource_type="pool", resource_id=name, details=details)


@router.get(
    "/pools/{name}/balance", response_model=BalanceStatus, summary="Read a pool's balance",
    responses=errors(_POOL_400, {
        409: "The pool is not mounted.",
        500: "btrfs cannot report the balance or does not answer in time, or the host's mount table cannot be "
             "read.",
    }),
)
async def get_balance_status_endpoint(name: PoolName, _=Depends(verify_token)):
    """The pool's balance as btrfs reports it, with the seconds left while it runs, and how the
    last one ended when none runs or is paused.
    \f
    ADR-0379. btrfs has pools.BALANCE_COMMAND_TIMEOUT to answer.
    """
    return await _balance(balances.status, name, reads=True)


@router.post(
    "/pools/{name}/balance", response_model=BalanceStatus, summary="Start a pool's balance",
    responses=errors(RATE_LIMITED, BODY, _POOL_400, {
        409: "The pool is not mounted or is read-only, a scrub, a disk removal or a balance of it runs, or a "
             "balance of it is paused; nothing ran.",
        500: "systemd or btrfs refuses the balance, with the reason, btrfs does not answer in time, or the host's "
             "mount table cannot be read.",
    }),
)
@limiter.limit("10/minute")
async def start_balance_endpoint(request: Request, name: PoolName, body: BalanceStart,
                                 user: User = Depends(verify_token)):
    """Start a balance of a pool: of the data and metadata chunks less than ``usage`` percent full
    (20 by default), or of every chunk with ``full``. It runs on the host in the unit
    unos-balance-<pool>; the answer is its status once btrfs runs (running also before the kernel
    reports it), or how it ended when it ended at once. The audit log gets balance_started.
    \f
    ADR-0379.
    """
    status = await _balance(balances.start, name, body)
    what = "a full balance of the pool {}: every chunk" if body.full else (
        "a balance of the pool {}: the data and metadata chunks less than " + f"{body.limit}% full")
    await _audit_balance(AuditAction.BALANCE_STARTED, request, user, status.pool, "Started " + what.format(status.pool))
    return status


@router.post(
    "/pools/{name}/balance/pause", response_model=BalanceStatus, summary="Pause a pool's balance",
    responses=errors(_POOL_400, {
        409: "The pool is not mounted, or a cancel of its balance waits for the chunk being moved: the kernel "
             "would pause the balance instead.",
        500: "btrfs fails or does not answer in time, or the host's mount table cannot be read.",
    }),
)
async def pause_balance_endpoint(request: Request, name: PoolName, user: User = Depends(verify_token)):
    """Pause the pool's running balance after the chunk it moves: paused, or pausing while btrfs
    still waits for that chunk after a few seconds. A pool without a running balance, or whose
    pause waits already, answers its status, and the audit log gets balance_paused only for a pause.
    \f
    ADR-0379. The wait is balances.STOP_WAIT seconds.
    """
    status, paused = await _balance(balances.pause, name)
    if paused:
        await _audit_balance(AuditAction.BALANCE_PAUSED, request, user, status.pool,
                             f"Paused the balance of the pool {status.pool}")
    return status


@router.post(
    "/pools/{name}/balance/resume", response_model=BalanceStatus, summary="Resume a pool's balance",
    responses=errors(_POOL_400, {
        409: "No balance of the pool is paused, the pool is not mounted or is read-only, or a scrub or a disk "
             "removal of it runs.",
        500: "systemd or btrfs refuses the resume, with the reason, btrfs does not answer in time, or the host's "
             "mount table cannot be read.",
    }),
)
async def resume_balance_endpoint(request: Request, name: PoolName, user: User = Depends(verify_token)):
    """Resume the pool's paused balance in its unit on the host. The audit log gets balance_resumed.
    \f
    ADR-0379.
    """
    status = await _balance(balances.resume, name)
    await _audit_balance(AuditAction.BALANCE_RESUMED, request, user, status.pool,
                         f"Resumed the balance of the pool {status.pool}")
    return status


@router.delete(
    "/pools/{name}/balance", response_model=BalanceStatus, summary="Cancel a pool's balance",
    responses=errors(_POOL_400, {
        409: "The pool is not mounted, a pause of its balance waits for the chunk being moved (cancel it once it "
             "is paused), or the paused balance is of a read-only pool, which the kernel keeps for its next "
             "read-write mount.",
        500: "btrfs fails or does not answer in time, or the host's mount table cannot be read.",
    }),
)
async def cancel_balance_endpoint(request: Request, name: PoolName, user: User = Depends(verify_token)):
    """Cancel the pool's running or paused balance: idle with the result cancelled, or cancelling
    while btrfs still waits for the chunk being moved after a few seconds. A pool without a balance,
    or whose cancel waits already, answers its status, and so does one whose balance a pause from
    elsewhere kept; the audit log gets balance_cancelled only for a cancel.
    \f
    ADR-0379. The wait is balances.STOP_WAIT seconds.
    """
    status, cancelled = await _balance(balances.cancel, name)
    if cancelled:
        await _audit_balance(AuditAction.BALANCE_CANCELLED, request, user, status.pool,
                             f"Cancelled the balance of the pool {status.pool}")
    return status


@router.get(
    "/pools/{name}/devices", response_model=PoolDevices, summary="List a pool's disks and the disks that can join it",
    responses=errors(_POOL_400, {
        409: "The pool is not mounted.",
        500: "btrfs cannot report its disks or profiles or does not answer in time, or lsblk or the host's mount "
             "table cannot be read.",
    }),
)
async def get_pool_devices_endpoint(name: PoolName, _=Depends(verify_token)):
    """The disks of a mounted pool, each with why it cannot be removed (the only disk, the profile,
    the space), its profiles, the whole disks that can join it (none that holds the system, a btrfs
    filesystem or a mount; a sleeping one is not woken) and its disk removal.
    \f
    ADR-0411.
    """
    return await _call(devices.describe, validate_pool_name(name))


@router.post(
    "/pools/{name}/devices", response_model=PoolInfo, summary="Add a disk to a pool",
    responses=errors(RATE_LIMITED, BODY, {
        400: "The pool's name or the device path is not valid, confirm_device or identity names another path, or "
             "the device is a partition or no disk of this system.",
        409: "The pool or the disk is refused for one of the reasons in the description; nothing was written.",
        500: "btrfs device add fails or does not answer in time, which the audit log records as failed; or btrfs, "
             "lsblk or the host's mount table cannot be read.",
    }),
)
@limiter.limit("5/minute")
async def add_pool_device_endpoint(request: Request, name: PoolName, body: DiskAdd,
                                   user: User = Depends(verify_token)):
    """Add a free whole disk to a mounted pool with btrfs device add on the host: the pool grows, and
    its profile stays. The answer is the pool. The audit log gets pool_disk_added; as failed when
    btrfs device add fails.

    Refused with 409, and nothing written: a pool that is not mounted, is read-only or misses a
    disk; a scrub, a balance (also a paused one) or a disk removal of the pool; a path that now
    holds another disk than ``identity``, or other data than ``contents``; a disk that is a boot or
    virtual device, the system disk, in use, holds a btrfs filesystem or is the pool's already;
    data without ``force``.
    \f
    ADR-0411, ADR-0429 (every disk is added with TRIM).
    """
    pool = validate_pool_name(name)
    try:
        details = await _call(devices.add, pool, body)
    except HTTPException as exc:
        if isinstance(exc.__cause__, devices.DiskAddFailedError):
            await audit_change(
                AuditAction.POOL_DISK_ADDED, request, user.username, resource_type="pool", resource_id=pool,
                details=f"Tried to add {body.device} to the pool {pool}; then: {exc.detail}", success=False,
            )
        raise
    await audit_change(
        AuditAction.POOL_DISK_ADDED, request, user.username, resource_type="pool", resource_id=pool, details=details,
    )
    # Audited before: a pool that cannot be read now still has its new disk.
    return await _call(pools.get_pool, pool)


async def _removal(func: Callable[..., DiskRemovalStatus], name: str, *args: Any, reads: bool = False) -> DiskRemovalStatus:
    """Run a request of a disk removal in a worker thread and note its status for the events socket
    (storage/devices.py, ADR-0411); for a read of the status (``reads``), also why it failed."""
    pool = validate_pool_name(name)
    try:
        status = await asyncio.to_thread(func, pool, *args)
    except HTTPException:
        raise
    except Exception as exc:
        error = _http_error(exc)
        if error.status_code >= 500:
            logger.exception("A disk removal request failed")
            error = HTTPException(status_code=500, detail=devices.failure(exc, pool))
            if reads:
                devices.note(pool, error.detail)
        raise error from exc
    devices.note(pool, status)
    return status


@router.get(
    "/pools/{name}/removal", response_model=DiskRemovalStatus, summary="Read a pool's disk removal",
    responses=errors(_POOL_400, {
        409: "The pool is not mounted.",
        500: "btrfs cannot report the pool's disks or does not answer in time, or the host's mount table cannot "
             "be read.",
    }),
)
async def get_disk_removal_endpoint(name: PoolName, _=Depends(verify_token)):
    """The pool's disk removal: running while the kernel moves the data off a disk, with the bytes
    still on it and those at its start; else idle, with how the last removal that UNOS started since
    the host started ended.
    \f
    ADR-0411. btrfs has devices.READ_TIMEOUT to answer.
    """
    return await _removal(devices.status, name, reads=True)


@router.post(
    "/pools/{name}/removal", response_model=DiskRemovalStatus, summary="Start a disk removal",
    responses=errors(RATE_LIMITED, BODY, {
        400: "The pool's name or the device path is not valid, or confirm_device is not device.",
        409: "Nothing ran: the pool is not mounted, read-only or misses a disk; a scrub, a balance (also a paused "
             "one) or a disk removal of it runs; the path is no longer the devid's; or it is the pool's only "
             "disk, or the profile (or one UNOS does not know) or the space of the disks left cannot do without "
             "it.",
        500: "systemd or btrfs refuses the removal, with the reason, btrfs does not answer in time, or the host's "
             "mount table cannot be read.",
    }),
)
@limiter.limit("5/minute")
async def start_disk_removal_endpoint(request: Request, name: PoolName, body: DiskRemove,
                                      user: User = Depends(verify_token)):
    """Start the removal of a disk of a mounted pool: btrfs device remove in the unit
    unos-disk-remove-<pool> on the host moves its data to the other disks first, and the disk leaves
    the pool at the end. ``devid`` and ``device`` name the disk as the list showed it. The answer
    is its status once btrfs runs: running, or how it ended when it ended at once. The audit log
    gets pool_disk_removal_started.
    \f
    ADR-0411.
    """
    status = await _removal(devices.remove, name, body)
    await audit_change(
        AuditAction.POOL_DISK_REMOVAL_STARTED, request, user.username, resource_type="pool", resource_id=status.pool,
        details=f"Started removing {body.device} (devid {body.devid}) from the pool {status.pool}: its data moves to "
        "the other disks first",
    )
    return status
