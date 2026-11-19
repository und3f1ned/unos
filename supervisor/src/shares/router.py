"""Share endpoints: SMB shares and NFS exports. Administrator-only (main.py).

SMB shares are rows of the UNOS database; shares/service.py changes them
under smb_lock() and renders smb.conf from them. NFS exports are the lines
of the host's export files; shares/nfs.py changes only the lines that UNOS
wrote into its own file (ADR-0292). The network interfaces that SMB listens
on are shares/listen.py's (ADR-0391). Every mutation is audited (ADR-0320).
PUT /shares/smb/{name} is gone (405): PATCH changes only the fields it sends.
"""

import asyncio
import inspect
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit.router import log_action
from ..audit.schemas import AuditAction, AuditSeverity
from ..auth.utils import verify_token
from ..database import get_db
from ..openapi import BODY, RATE_LIMITED, answer, errors
from ..security.validators import sanitize_error_message
from ..storage import pools
from . import listen, nfs, service, smb
from .schemas import (
    NfsExportCreate,
    NfsExportDeleted,
    NfsExportInfo,
    NfsServiceStatus,
    ShareAccessLevel,
    SmbInterfaces,
    SmbInterfacesUpdate,
    SmbServiceStatus,
    SmbShareCreate,
    SmbShareDeleted,
    SmbShareInfo,
    SmbSharesDelete,
    SmbSharesDeleted,
    SmbShareUpdate,
)

logger = logging.getLogger(__name__)

# Rate limiter for shares endpoints
limiter = Limiter(key_func=get_remote_address)

router = APIRouter(prefix="/shares", tags=["shares"])

ShareName = Annotated[str, Path(description="The share's name, in any letter case.")]

# The errors of the share routes, from what _call maps (ADR-0417).
_IMPORT_FAILED = "The one-time import of smb.conf failed."
_POOLS_UNREADABLE = "The pool list could not be read."
_APPLY_FAILED = (
    "The one-time import of smb.conf failed, or the new smb.conf could not be written or testparm rejected it"
)
_APPLY_BUSY = "The pool list could not be read, or the database was busy"
_SHARE_400 = (
    "The name, comment or folder breaks a rule (such as a folder on no pool), a user or group of the access lists does not exist, a user is an "
    "administrator or has a username that SMB cannot use, a guest share would require encryption, or Time Machine "
    "or Previous Versions cannot work on this host or folder"
)
_NEW_FOLDER_409 = (
    "the folder's pool is not mounted at its mountpoint or its mount cannot be checked, or, for a folder to create, the pool is read-only, a folder above it can be "
    "changed by other accounts or another host group has the gid of unos-smb"
)
_SHARE_500 = (
    "The one-time import of smb.conf failed, Samba's vfs modules could not be checked, btrfs or the host's mount "
    "table could not be read, the folder could not be made, or the new smb.conf could not be written or testparm "
    "rejected it."
)
_EXPORTS_UNREADABLE = "The export files could not be read or written on the host."


class SharePath(BaseModel):
    """A pool or a volume, below which a share or an NFS export can name a folder."""

    model_config = ConfigDict(json_schema_extra={"examples": [
        {"path": "/mnt/pools/data", "name": "data", "type": "pool", "managed": True},
        {"path": "/mnt/pools/data/media", "name": "data/media", "type": "volume", "managed": True},
    ]})

    path: str = Field(description="The pool's mountpoint, or the volume's folder.")
    # The pool's name, or pool/volume: the web UI adds the kind in the page's language (ADR-0299).
    name: str = Field(description="The pool's name, or pool/volume for a volume.")
    type: str = Field(description="pool or volume.")  # "pool" or "volume"
    # The pool is one that UNOS manages (its fstab marker): only such folders can be SMB
    # shares and NFS exports (ADR-0294). A volume has its pool's value; false for every path when
    # the pool list cannot be read.
    managed: bool = Field(
        description="Whether the pool is one that UNOS manages: only its folders can be shared or exported. A "
                    "volume has its pool's value; false for every one when the pool list cannot be read."
    )


async def _call(func, *args, not_found_status: int = 400):
    """Run a share operation and map its errors.

    Coroutine functions are awaited; blocking functions run in a worker
    thread. HTTPException passes through (validation errors arrive as 400).
    """
    try:
        if inspect.iscoroutinefunction(func):
            return await func(*args)
        return await asyncio.to_thread(func, *args)
    except HTTPException:
        raise
    except service.PoolListUnavailable as e:
        raise HTTPException(status_code=503, detail=sanitize_error_message(e)) from e
    except smb.SambaConfigError as e:
        raise HTTPException(status_code=500, detail=sanitize_error_message(e)) from e
    except pools.PoolNotFoundError as e:
        raise HTTPException(status_code=400, detail=f"Share path must be on a storage pool: {e}") from e
    except pools.PoolConflictError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=not_found_status, detail=str(e)) from e
    except Exception as e:
        logger.exception("A request of this router failed")
        raise HTTPException(status_code=500, detail=sanitize_error_message(e)) from e


async def _audit(request: Request, actor: str | None, action: AuditAction, change: service.ShareChange) -> None:
    """The audit entry of a share mutation (ADR-0320); none when nothing changed."""
    if change.details is None:
        return
    await log_action(
        action,
        request=request,
        username=actor,
        resource_type="share",
        resource_id=change.name,
        details=change.details,
        severity=change.severity,
    )


def _available_paths() -> list[SharePath]:
    available = []
    try:
        managed = pools.managed_pool_names()
    except Exception:
        managed = set()  # unknown: no path counts as one of a managed pool

    try:
        pool_list = pools.list_pools()
        for pool in pool_list:
            if pool.mountpoint:
                available.append(SharePath(
                    path=pool.mountpoint,
                    name=pool.name,
                    type="pool",
                    managed=pool.name in managed,
                ))
                # Also list volumes in this pool
                try:
                    volumes = pools.list_volumes(pool.name)
                    for vol in volumes:
                        available.append(SharePath(
                            path=vol.path,
                            name=f"{pool.name}/{vol.name}",
                            type="volume",
                            managed=pool.name in managed,
                        ))
                except Exception:
                    pass
    except Exception:
        pass

    return available


@router.get("/paths", response_model=list[SharePath], summary="List the pools and volumes that can hold shares")
async def list_available_paths(_=Depends(verify_token)):
    """The mounted pools and their volumes, below which a share or an NFS export names its folder. A
    pool whose volumes cannot be read is listed without them; none is listed when the pools cannot be
    read."""
    return await asyncio.to_thread(_available_paths)


# SMB Endpoints
@router.get(
    "/smb", response_model=list[SmbShareInfo], summary="List the shares",
    responses=errors({500: _IMPORT_FAILED, 503: _POOLS_UNREADABLE}),
)
async def list_smb_shares(_=Depends(verify_token), db: AsyncSession = Depends(get_db)):
    """Every share with its access, group access and protocol options, and whether Samba serves it,
    sorted by name. The first call imports the shares of the host's smb.conf, once."""
    return await _call(service.list_share_infos, db)


@router.post(
    "/smb", response_model=SmbShareInfo, summary="Create a share",
    responses=errors(BODY, RATE_LIMITED, {
        400: f"{_SHARE_400}.",
        409: "A share already has the name, in any letter case, the folder overlaps another share's, a user or "
             f"group of the access lists now has another name, or {_NEW_FOLDER_409}.",
        500: _SHARE_500,
        503: f"{_APPLY_BUSY}.",
    }),
)
@limiter.limit("10/minute")  # Rate limit share creation
async def create_smb_share(
    request: Request,
    share: SmbShareCreate,
    user=Depends(verify_token),
    db: AsyncSession = Depends(get_db),
):
    """Create a share of a folder inside a pool that UNOS manages, and apply smb.conf. A missing folder
    is created (root, the group unos-smb, the mode 2770); an existing one is never changed. The answer's
    warnings name what needs a look, such as a guest share or a folder whose permissions are not set up
    for SMB."""
    actor = user.username
    change = await _call(service.create_share, db, share)
    await _audit(request, actor, AuditAction.SHARE_CREATED, change)
    return change.info


@router.get("/smb-status", response_model=SmbServiceStatus, summary="Read the state of Samba and the shares")
async def get_smb_status(_=Depends(verify_token), db: AsyncSession = Depends(get_db)):
    """Samba on the host, the problems of smb.conf, of the shares and of the host, the guest shares and
    the report of the one-time import of smb.conf."""
    return await _call(service.service_status, db)


@router.delete(
    "/smb-status/import-report", summary="Dismiss the report of the smb.conf import",
    responses=answer({"status": "ok"}, "status ok: the report is gone, also when there was none."),
)
async def clear_smb_import_report(_=Depends(verify_token), db: AsyncSession = Depends(get_db)):
    """Dismiss the report of the one-time import of smb.conf."""
    await _call(service.clear_import_report, db)
    return {"status": "ok"}


@router.get("/smb-interfaces", response_model=SmbInterfaces, summary="Read the SMB interfaces")
async def get_smb_interfaces(_=Depends(verify_token), db: AsyncSession = Depends(get_db)):
    """The SMB interfaces chosen (none: every interface), the host's network interfaces, and the
    addresses that smbd listens on, compared with the choice.
    \f
    ADR-0391.
    """
    return await _call(listen.state, db)


@router.put(
    "/smb-interfaces", response_model=SmbInterfaces, summary="Choose the SMB interfaces",
    responses=errors(BODY, {
        400: "A name is not an interface name, is the loopback, one of Docker's or a dummy interface, or names a "
             "point-to-point interface, or no chosen interface is on the host, enabled and with an address of "
             "the local networks.",
        500: f"{_APPLY_FAILED}.",
        503: "The host's network interfaces or the pool list could not be read, or the database was busy.",
    }),
)
async def put_smb_interfaces(
    request: Request,
    choice: SmbInterfacesUpdate,
    user=Depends(verify_token),
    db: AsyncSession = Depends(get_db),
):
    """Choose the network interfaces that SMB listens on besides the loopback; an empty list for
    every interface. A change applies smb.conf and restarts smbd, which ends every open SMB connection; a
    restart that fails is a warning of the answer. The same choice again changes nothing.
    \f
    ADR-0391.
    """
    change = await _call(listen.choose, db, choice.interfaces)
    if change.details is not None:
        await log_action(
            AuditAction.SMB_INTERFACES_CHANGED,
            request=request,
            username=user.username,
            resource_type="smb",
            details=change.details,
            success=change.failed is None,
            severity=listen.SEVERITY,
        )
    return change.info


@router.post(
    "/smb-interfaces/restart", response_model=SmbInterfaces, summary="Restart SMB",
    responses=errors({500: "smbd could not be restarted; the detail says why."}),
)
async def restart_smb(request: Request, user=Depends(verify_token), db: AsyncSession = Depends(get_db)):
    """Restart smbd, so that it listens on the addresses that the chosen interfaces have now; every
    open SMB connection ends.
    \f
    ADR-0391.
    """
    change = await _call(listen.restart, db)
    await log_action(
        AuditAction.SMB_RESTARTED,
        request=request,
        username=user.username,
        resource_type="smb",
        details=change.details,
        success=change.failed is None,
        severity=listen.SEVERITY,
    )
    if change.failed is not None:
        raise HTTPException(status_code=500, detail=f"smbd could not be restarted: {change.failed}")
    return change.info


@router.get(
    "/smb/{name}", response_model=SmbShareInfo, summary="Read a share",
    responses=errors({404: "No share has the name.", 500: _IMPORT_FAILED, 503: _POOLS_UNREADABLE}),
)
async def get_smb_share(name: ShareName, _=Depends(verify_token), db: AsyncSession = Depends(get_db)):
    """One share with its access, group access and protocol options, and whether Samba serves it."""
    return await _call(service.get_share_info, db, name)


@router.patch(
    "/smb/{name}", response_model=SmbShareInfo, summary="Change a share",
    responses=errors(BODY, {
        400: f"{_SHARE_400}, or the share would grant access while its folder breaks a rule.",
        404: "No share has the name.",
        409: "expected_version is not the share's version, another share has the new name, the new folder "
             "overlaps another share's, a user or group of the access lists now has another name, or, for a new "
             f"folder, {_NEW_FOLDER_409}.",
        500: _SHARE_500,
        503: f"{_APPLY_BUSY}.",
    }),
)
async def update_smb_share(
    request: Request,
    name: ShareName,
    changes: SmbShareUpdate,
    user=Depends(verify_token),
    db: AsyncSession = Depends(get_db),
):
    """Change a share: only the fields that are sent change, and access and group_access replace the
    whole lists. Send expected_version, the share's version as it was loaded: 409 when the share was
    changed since. A new name (not only in letter case) or a new folder stops a running repair of its
    permissions. A change applies smb.conf; one that changes nothing writes nothing.
    \f
    ADR-0134, ADR-0363.
    """
    actor = user.username
    change = await _call(service.update_share, db, name, changes)
    await _audit(request, actor, AuditAction.SHARE_UPDATED, change)
    return change.info


@router.post(
    "/smb/bulk-delete", response_model=SmbSharesDeleted, summary="Delete several shares",
    responses=errors(BODY, RATE_LIMITED, {
        500: f"{_APPLY_FAILED}; no share was deleted.",
        503: f"{_APPLY_BUSY}; no share was deleted.",
    }),
)
@limiter.limit("10/minute")  # as a single deletion: one request for all the shares it names
async def delete_smb_shares(
    request: Request,
    body: SmbSharesDelete,
    user=Depends(verify_token),
    db: AsyncSession = Depends(get_db),
):
    """Delete several shares with one apply of smb.conf; their folders and files are kept, and open
    connections to them are closed.

    Each share is sent with the version and the folder the client loaded: one changed since, and a
    name that no share has, are reported in its result and not deleted, while the others are. A
    failed apply deletes none of them and answers as a single deletion does. Each deleted share
    gets its own audit entry; one that cannot be written is a warning of the answer.
    \f
    ADR-0389.
    """
    actor = user.username
    deletion = await _call(service.delete_shares, db, body.shares)
    # Every deleted share's entry is written, also after one that could not be: log_action reports
    # such an entry in the supervisor's log. The answer still names each share's result, as the
    # shares are deleted, and says which entries are missing (ADR-0389).
    unrecorded: list[str] = []
    reasons: list[str] = []
    for change in deletion.changes:
        try:
            await _audit(request, actor, AuditAction.SHARE_DELETED, change)
        except OSError as exc:
            unrecorded.append(change.name)
            reason = exc.strerror or str(exc)
            if reason not in reasons:
                reasons.append(reason)
    warnings = list(deletion.warnings)
    if unrecorded:
        warnings.append(
            f"The audit log could not record the deletion of {', '.join(unrecorded)} "
            f"({'; '.join(reasons)}); the supervisor's log has the entries"
        )
    return SmbSharesDeleted(results=deletion.results, warnings=warnings)


@router.delete(
    "/smb/{name}", response_model=SmbShareDeleted, summary="Delete a share",
    responses=errors(RATE_LIMITED, {
        404: "No share has the name.",
        500: f"{_APPLY_FAILED}; the share is kept.",
        503: f"{_APPLY_BUSY}; the share is kept.",
    }),
)
@limiter.limit("10/minute")  # Rate limit share deletion
async def delete_smb_share(
    request: Request,
    name: ShareName,
    user=Depends(verify_token),
    db: AsyncSession = Depends(get_db),
):
    """Delete a share and apply smb.conf. Its folder and files are kept; open connections to it are
    closed, and a running repair of its permissions stops."""
    actor = user.username
    change = await _call(service.delete_share, db, name)
    await _audit(request, actor, AuditAction.SHARE_DELETED, change)
    return SmbShareDeleted(warnings=change.warnings)


# NFS Endpoints
@router.get(
    "/nfs", response_model=list[NfsExportInfo], summary="List the NFS exports",
    responses=errors({500: "The export files could not be read on the host."}),
)
async def list_nfs_exports(_=Depends(verify_token)):
    """Every NFS export of the host's export files, as exportfs reads them; managed marks a line that
    UNOS wrote, which it can delete."""
    return await _call(nfs.list_exports)


@router.get("/nfs-status", response_model=NfsServiceStatus, summary="Read the state of the NFS server")
async def get_nfs_status(_=Depends(verify_token)):
    """Whether the host has an NFS server (exportfs), the lines where exportfs stops reading, and
    the exports of UNOS below folders that other accounts can change."""
    return await _call(nfs.service_status)


@router.post(
    "/nfs", response_model=NfsExportInfo, summary="Create an NFS export",
    responses=errors(BODY, {
        400: "The client is not an IP address or network of the local networks, or the folder breaks a rule: "
             "outside a pool that UNOS manages, a pool's top level, app data, a snapshot or the trash, other "
             "characters, too long, missing, or reached through a symbolic link.",
        409: "UNOS or another export line exports the folder already, an export file has a line that exportfs "
             "cannot read or UNOS's own ends inside an entry, the pool is not mounted, or a folder above the "
             "folder can be changed by other accounts.",
        500: _EXPORTS_UNREADABLE,
        503: _POOLS_UNREADABLE,
    }),
)
async def create_nfs_export(request: Request, export: NfsExportCreate, user=Depends(verify_token)):
    """Export an existing folder of a pool that UNOS manages to one address or network of the local
    networks, read-only or read-write; exportfs applies it. What exportfs reports comes back as
    warnings: the export is written either way.
    \f
    ADR-0293.
    """
    info = await _call(nfs.create_export, export)
    access = "read and write" if export.access == ShareAccessLevel.WRITE else "read only"
    await log_action(
        AuditAction.NFS_EXPORT_CREATED,
        request=request,
        username=user.username,
        resource_type="nfs_export",
        resource_id=info.path,
        details=f"Exported {info.path} to {info.clients[0].client}, {access}",
    )
    return info


@router.delete(
    "/nfs", response_model=NfsExportDeleted, summary="Delete an NFS export",
    responses=errors({
        404: "No export file exports the folder.",
        409: "The folder's export is not a line that UNOS wrote (it is in another export file, or written by "
             "hand), or an export file has a line that exportfs cannot read.",
        500: _EXPORTS_UNREADABLE,
    }),
)
async def delete_nfs_export(
    request: Request,
    path: Annotated[str, Query(description="The exported folder, as GET /shares/nfs lists it.")],
    user=Depends(verify_token),
):
    """Delete an NFS export that UNOS wrote; exportfs applies it. Exports of the other export files,
    and lines written by hand, are never changed."""
    result = await _call(nfs.delete_export, path)
    await log_action(
        AuditAction.NFS_EXPORT_DELETED,
        request=request,
        username=user.username,
        resource_type="nfs_export",
        resource_id=path,
        details=f"Removed the NFS export of {path}",
        severity=AuditSeverity.WARNING,
    )
    return result
