"""Repair permissions endpoints (ADR-0155).

POST repairs the permissions of a share's folder (repair.repair_tree) in a
worker thread and answers when the repair has finished, after at most about
4 minutes; one repair runs at a time. GET answers the last result of a share
since UNOS started (kept in memory only).

main.py includes this router in the administrator-only routers; the endpoints
also require an administrator themselves.
"""

import asyncio
import threading
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, HTTPException, Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import Row, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit.router import log_action
from ..audit.schemas import AuditAction
from ..auth.models import User
from ..auth.utils import require_admin
from ..database import get_db
from ..security.validators import pools_base, sanitize_error_message
from . import accounts, constants, models, repair, service

# Rate limiter for the repair endpoint
limiter = Limiter(key_func=get_remote_address)

router = APIRouter(prefix="/shares", tags=["shares"])

# One repair at a time. The worker thread that walks takes it without blocking
# and releases it, so that a request that is cancelled cannot release it while
# its walk still runs.
_repair_lock = threading.Lock()
# The last result of each share since UNOS started, by the share's name in lower case.
_last_results: dict[str, repair.RepairResult] = {}
# Repairs in progress: their tasks finish (result kept, audit entry written)
# even when the request that started them has gone.
_tasks: set[asyncio.Task] = set()

SHARE_NOT_FOUND = "Share not found"
NO_RESULT = "No repair has run for this share since UNOS started"
ALREADY_RUNNING = "A permissions repair is already running"
SHARE_CHANGED = "The share was changed meanwhile; reload"


@router.post("/smb/{name}/repair-permissions", response_model=repair.RepairResult)
@limiter.limit("6/minute")
async def repair_share_permissions(
    request: Request,
    name: str,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
) -> repair.RepairResult:
    """Repair the permissions of a share's folder and answer the result (ADR-0155).

    Every file and folder of the share gets the group unos-smb and group
    read/write (folders 2770, files 0660 or 0770); other accounts lose access.
    Symlinks, hard links, special files, private entries, .snapshots, other
    filesystems and mount points are skipped. 404 for an unknown share, also
    one deleted while it was checked. 409, and nothing is changed, when the
    share was changed while it was checked, the share is not served (its
    folder breaks a rule of ADR-0145), its pool is not mounted or is mounted
    read-only, a folder above the share's folder can be changed by other
    accounts, another host group has the SMB group's gid, or another repair
    is running. 503 when the pool list cannot be read. A repair stops after
    4 minutes with the state "incomplete": run it again to continue.
    """
    username = admin.username
    share = await _find_share(db, name)
    try:
        problem = await service.share_problem(db, share.id)
    except HTTPException:
        raise
    except service.PoolListUnavailable as exc:
        raise HTTPException(status_code=503, detail=sanitize_error_message(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=sanitize_error_message(exc)) from exc
    # share_problem() read the share's row again, apart from the read above,
    # and a repair does not take smb_lock: a PATCH, a restore or a delete can
    # commit before or after its read. Its answer is used only while the row
    # is still the one read first (for a share deleted before its read, it
    # answers "The share does not exist"), so the same change gets the same
    # answer whenever it committed, and only the folder that it checked is
    # ever walked.
    current = await _share_by_id(db, share.id)
    if current is None:
        raise HTTPException(status_code=404, detail=SHARE_NOT_FOUND)
    if (current.name, current.path) != (share.name, share.path):
        raise HTTPException(status_code=409, detail=SHARE_CHANGED)
    if problem is not None:
        raise HTTPException(
            status_code=409, detail=f"Share {share.name} is not served: {problem}; change its folder first"
        )
    # Nothing more to read: no database connection is held while the walk runs.
    await db.close()

    pool = await asyncio.to_thread(_pool_of, share.path)
    if pool is None:  # share_problem() refuses such a path; checked again before anything is walked
        raise HTTPException(
            status_code=409,
            detail=f"Share {share.name} is not served: its folder is not inside a pool; change its folder first",
        )
    from ..storage import pools  # lazily: storage.pools imports shares.smb at module level

    try:
        # Never on the system disk (a pool that is not mounted) or on a read-only pool.
        await asyncio.to_thread(pools._mounted_pool_target, pool, write=True)
    except HTTPException:
        raise
    except (pools.PoolNotFoundError, pools.PoolConflictError) as exc:
        raise HTTPException(
            status_code=409, detail=f"The pool of share {share.name} is not available: {exc}"
        ) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=sanitize_error_message(exc)) from exc
    # Whoever can change a folder above the share's folder can swap in another folder,
    # whose files the walk would then give the SMB group. Read-only check, nothing changed.
    try:
        unsafe = await asyncio.to_thread(service.unsafe_parent_of, share.path)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=sanitize_error_message(exc)) from exc
    if unsafe is not None:
        raise HTTPException(status_code=409, detail=unsafe.refusal())
    try:
        problem = await asyncio.to_thread(accounts.gid_problem)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=sanitize_error_message(exc)) from exc
    if problem is not None:
        raise HTTPException(status_code=409, detail=problem)

    # Its own task, awaited through shield: a request that is cancelled while
    # the walk runs does not lose the result or the audit entry.
    task = asyncio.create_task(_repair_and_record(request, username, share.name, share.path))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    result = await asyncio.shield(task)
    if result is None:
        raise HTTPException(status_code=409, detail=ALREADY_RUNNING)
    return result


@router.get("/smb/{name}/repair-permissions", response_model=repair.RepairResult)
async def get_repair_result(
    name: str,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> repair.RepairResult:
    """The last repair result of a share since UNOS started; 404 when there is none."""
    share = await _find_share(db, name)
    result = _last_results.get(share.name.lower())
    if result is None:
        raise HTTPException(status_code=404, detail=NO_RESULT)
    return result


async def _find_share(db: AsyncSession, name: str) -> Row:
    """(id, name, path) of the share named ``name`` in any letter case, or 404.

    The name column is NOCASE, so the comparison ignores the case, like
    Samba and the UNIQUE constraint do. Only the columns are read: no share
    object enters the session that service.share_problem() uses.
    """
    result = await db.execute(
        select(models.SmbShare.id, models.SmbShare.name, models.SmbShare.path)
        .where(models.SmbShare.name == name)
        .order_by(models.SmbShare.id)
    )
    share = result.first()
    if share is None:
        raise HTTPException(status_code=404, detail=SHARE_NOT_FOUND)
    return share


async def _share_by_id(db: AsyncSession, share_id: int) -> Row | None:
    """(id, name, path) of the share with this id as committed now, or None when it is gone."""
    result = await db.execute(
        select(models.SmbShare.id, models.SmbShare.name, models.SmbShare.path).where(models.SmbShare.id == share_id)
    )
    return result.first()


def _pool_of(path: object) -> str | None:
    """The pool of a share's folder: its first component below the pools base; None when it is not below it."""
    try:
        parts = PurePosixPath(path).relative_to(pools_base()).parts
    except (TypeError, ValueError):
        return None
    return parts[0] if parts else None


async def _repair_and_record(
    request: Request, username: str, share_name: str, path: str
) -> repair.RepairResult | None:
    """Repair unless another repair is running (None); keep and audit the result."""
    result = await asyncio.to_thread(_repair_unless_running, path)
    if result is None:
        return None
    result = result.model_copy(update={"share": share_name})
    _last_results[share_name.lower()] = result
    skipped = sum(result.skipped.values())
    await log_action(
        action=AuditAction.SHARE_PERMISSIONS_REPAIRED,
        request=request,
        username=username,
        resource_type="share",
        resource_id=share_name,
        details=(
            f"Repaired permissions: {result.changed} changed, {skipped} skipped, "
            f"{result.errors} errors ({result.state})"
        ),
        success=result.state != "failed",
    )
    return result


def _repair_unless_running(path: str) -> repair.RepairResult | None:
    """The walk (worker thread); None when another repair holds the lock."""
    if not _repair_lock.acquire(blocking=False):
        return None
    try:
        return repair.repair_tree(path, gid=constants.SMB_GROUP_GID)
    finally:
        _repair_lock.release()
