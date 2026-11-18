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

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit.router import log_action
from ..audit.schemas import AuditAction, AuditSeverity
from ..auth.utils import verify_token
from ..database import get_db
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


class SharePath(BaseModel):
    path: str
    # The pool's name, or pool/volume: the web UI adds the kind in the page's language (ADR-0299).
    name: str
    type: str  # "pool" or "volume"
    # The pool is one that UNOS manages (its fstab marker): only such folders can be SMB
    # shares and NFS exports (ADR-0294). A volume has its pool's value; false for every path when
    # the pool list cannot be read.
    managed: bool


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


@router.get("/paths", response_model=list[SharePath])
async def list_available_paths(_=Depends(verify_token)):
    """List available paths for sharing (pools and volumes)."""
    return await asyncio.to_thread(_available_paths)


# SMB Endpoints
@router.get("/smb", response_model=list[SmbShareInfo])
async def list_smb_shares(_=Depends(verify_token), db: AsyncSession = Depends(get_db)):
    """List all SMB shares (the shares of smb.conf are imported first, once)."""
    return await _call(service.list_share_infos, db)


@router.post("/smb", response_model=SmbShareInfo)
@limiter.limit("10/minute")  # Rate limit share creation
async def create_smb_share(
    request: Request,
    share: SmbShareCreate,
    user=Depends(verify_token),
    db: AsyncSession = Depends(get_db),
):
    """Create an SMB share; a missing folder is created (root:unos-smb 2770)."""
    actor = user.username
    change = await _call(service.create_share, db, share)
    await _audit(request, actor, AuditAction.SHARE_CREATED, change)
    return change.info


@router.get("/smb-status", response_model=SmbServiceStatus)
async def get_smb_status(_=Depends(verify_token), db: AsyncSession = Depends(get_db)):
    """Samba on the host, problems of smb.conf and of the shares, the import report."""
    return await _call(service.service_status, db)


@router.delete("/smb-status/import-report")
async def clear_smb_import_report(_=Depends(verify_token), db: AsyncSession = Depends(get_db)):
    """Dismiss the report of the one-time smb.conf import."""
    await _call(service.clear_import_report, db)
    return {"status": "ok"}


@router.get("/smb-interfaces", response_model=SmbInterfaces)
async def get_smb_interfaces(_=Depends(verify_token), db: AsyncSession = Depends(get_db)):
    """The interfaces that SMB listens on ([]: every one), the host's interfaces, and smbd's listeners
    compared with the choice (ADR-0391)."""
    return await _call(listen.state, db)


@router.put("/smb-interfaces", response_model=SmbInterfaces)
async def put_smb_interfaces(
    request: Request,
    choice: SmbInterfacesUpdate,
    user=Depends(verify_token),
    db: AsyncSession = Depends(get_db),
):
    """Choose the interfaces that SMB listens on besides the loopback; [] for every interface. A
    change restarts smbd, which ends every open SMB connection (ADR-0391)."""
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


@router.post("/smb-interfaces/restart", response_model=SmbInterfaces)
async def restart_smb(request: Request, user=Depends(verify_token), db: AsyncSession = Depends(get_db)):
    """Restart smbd, so that it listens on what the chosen interfaces have now; every open SMB
    connection ends (ADR-0391). 500 when it fails."""
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


@router.get("/smb/{name}", response_model=SmbShareInfo)
async def get_smb_share(name: str, _=Depends(verify_token), db: AsyncSession = Depends(get_db)):
    """Get one SMB share (the name is not case-sensitive)."""
    return await _call(service.get_share_info, db, name)


@router.patch("/smb/{name}", response_model=SmbShareInfo)
async def update_smb_share(
    request: Request,
    name: str,
    changes: SmbShareUpdate,
    user=Depends(verify_token),
    db: AsyncSession = Depends(get_db),
):
    """Change an SMB share: only the fields that are sent change.

    Send expected_version (the version of the share as loaded): 409 when the
    share was changed meanwhile. ``access`` replaces the whole list.
    """
    actor = user.username
    change = await _call(service.update_share, db, name, changes)
    await _audit(request, actor, AuditAction.SHARE_UPDATED, change)
    return change.info


@router.post("/smb/bulk-delete", response_model=SmbSharesDeleted)
@limiter.limit("10/minute")  # as a single deletion: one request for all the shares it names
async def delete_smb_shares(
    request: Request,
    body: SmbSharesDelete,
    user=Depends(verify_token),
    db: AsyncSession = Depends(get_db),
):
    """Delete several SMB shares with one apply (ADR-0389); their folders and files are kept.

    Each share is sent with the version and the folder the client loaded: one changed since, and a
    name that no share has, are reported in its result and not deleted, while the others are. A
    failed apply deletes none of them and answers as a single deletion does. Each deleted share
    gets its own audit entry; one that cannot be written is a warning of the answer.
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


@router.delete("/smb/{name}", response_model=SmbShareDeleted)
@limiter.limit("10/minute")  # Rate limit share deletion
async def delete_smb_share(
    request: Request,
    name: str,
    user=Depends(verify_token),
    db: AsyncSession = Depends(get_db),
):
    """Delete an SMB share. Its folder and files are kept; open connections to it are closed."""
    actor = user.username
    change = await _call(service.delete_share, db, name)
    await _audit(request, actor, AuditAction.SHARE_DELETED, change)
    return SmbShareDeleted(warnings=change.warnings)


# NFS Endpoints
@router.get("/nfs", response_model=list[NfsExportInfo])
async def list_nfs_exports(_=Depends(verify_token)):
    """Every export of the host's export files, as exportfs reads them; managed: a line UNOS wrote."""
    return await _call(nfs.list_exports)


@router.get("/nfs-status", response_model=NfsServiceStatus)
async def get_nfs_status(_=Depends(verify_token)):
    """Whether the host has an NFS server (exportfs), the lines where exportfs stops reading, and
    the exports of UNOS below folders that other accounts can change."""
    return await _call(nfs.service_status)


@router.post("/nfs", response_model=NfsExportInfo)
async def create_nfs_export(request: Request, export: NfsExportCreate, user=Depends(verify_token)):
    """Export an existing folder to one local address or network (ADR-0293); exportfs applies it.

    What exportfs reports comes back as warnings: the export is written either way.
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


@router.delete("/nfs", response_model=NfsExportDeleted)
async def delete_nfs_export(request: Request, path: str, user=Depends(verify_token)):
    """Delete an export that UNOS wrote (404 for none; 409 for any other entry, and while an export
    file has a line that exportfs cannot read)."""
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
