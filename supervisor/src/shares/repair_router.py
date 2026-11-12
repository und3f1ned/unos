"""Repair permissions endpoints (ADR-0363).

POST starts a repair of a share's folder (repair.repair_tree) as a background
job in a thread of its own and answers at once (202) with the job running; one
repair runs at a time. The job, while it runs and once it ended, is
``current_job()``, which the events socket sends to administrators
(websocket/events.py). DELETE asks the running repair of a share to stop. GET
answers the last finished result of a share since UNOS started. The job and
the results are kept in memory only: a restart of the supervisor ends a
running repair without a result.

main.py includes this router in the administrator-only routers; the endpoints
also require an administrator themselves.
"""

import asyncio
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
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

logger = logging.getLogger(__name__)

# Rate limiter for the repair endpoint
limiter = Limiter(key_func=get_remote_address)

router = APIRouter(prefix="/shares", tags=["shares"])


@dataclass
class _Running:
    """The repair that runs: its share and how to stop it."""

    share_id: int
    stop: threading.Event = field(default_factory=threading.Event)
    stopped_by: str | None = None  # the administrator who asked it to stop


# The repair that runs, or None: one at a time. Set and checked on the event
# loop when a repair starts; its thread clears it once the walk has ended.
_running: _Running | None = None
# The running repair, or the last one since UNOS started, of any share; None
# before the first. Each change is a new object, which the events socket sends.
_job: repair.RepairResult | None = None
# The last result of each share since UNOS started, by the share's name in lower case.
_last_results: dict[str, repair.RepairResult] = {}

SHARE_NOT_FOUND = "Share not found"
NO_RESULT = "No repair has run for this share since UNOS started"
ALREADY_RUNNING = "A permissions repair is already running"
NOT_RUNNING = "No permissions repair of this share is running"
SHARE_CHANGED = "The share was changed meanwhile; reload"


def current_job() -> repair.RepairResult | None:
    """The running repair (state "running", the counts so far), or else the last one that ended."""
    return _job


def _publish(job: repair.RepairResult | None) -> None:
    global _job
    _job = job


@router.post("/smb/{name}/repair-permissions", response_model=repair.RepairResult, status_code=202)
@limiter.limit("6/minute")
async def repair_share_permissions(
    request: Request,
    name: str,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
) -> repair.RepairResult:
    """Start a repair of the permissions of a share's folder; answers it running (ADR-0363).

    Every file and folder of the share gets the group unos-smb and group
    read/write (folders 2770, files 0660 or 0770); other accounts lose access.
    Symlinks, hard links, special files, private entries, .snapshots, other
    filesystems and mount points are skipped. 404 for an unknown share, also
    one deleted while it was checked. 409, and nothing is changed, when the
    share was changed while it was checked, the share is not served (its
    folder breaks a rule of ADR-0145), its pool is not mounted or is mounted
    read-only, a folder above the share's folder can be changed by other
    accounts, another host group has the SMB group's gid, or another repair
    is running. 503 when the pool list cannot be read. The repair runs on in
    the background until the share is done, stopped (DELETE) or 24 hours
    have passed (the state "incomplete": run it again to continue).
    """
    global _running
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

    # No await from the check to the start: two starts cannot both pass it.
    if _running is not None:
        raise HTTPException(status_code=409, detail=ALREADY_RUNNING)
    running = _Running(share_id=share.id)
    started = repair.RepairResult(
        share=share.name, state="running", started_at=datetime.now(timezone.utc), finished_at=None, visited=0,
        changed=0, skipped=dict.fromkeys(repair.SKIP_REASONS, 0), skipped_paths=[], errors=0, error_samples=[],
        message="",
    )
    before = _job
    _running = running
    _publish(started)
    # A thread of its own, not one of the event loop's pool, which a long walk
    # would hold for hours; a daemon thread, so that a stop of the supervisor
    # does not wait for the walk.
    try:
        threading.Thread(
            target=_repair_and_record,
            args=(running, started, request, username, share.path),
            name="share-repair",
            daemon=True,
        ).start()
    except RuntimeError as exc:  # no thread could be started: nothing runs
        _running = None
        _publish(before)
        raise HTTPException(status_code=500, detail=f"The repair could not be started: {exc}") from exc
    return started


@router.delete("/smb/{name}/repair-permissions", response_model=repair.RepairResult, status_code=202)
async def stop_repair(
    name: str,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
) -> repair.RepairResult:
    """Ask the running repair of a share to stop; answers the job as it is now (ADR-0363).

    The walk stops before its next entry and ends "incomplete": what it
    changed stays changed, and a new run continues quickly. 404 for an
    unknown share; 409 when no repair of this share runs.
    """
    share = await _find_share(db, name)
    running = _running
    if running is None or running.share_id != share.id:
        raise HTTPException(status_code=409, detail=NOT_RUNNING)
    if running.stopped_by is None:
        running.stopped_by = admin.username
    running.stop.set()
    return _job


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


def _repair_and_record(
    running: _Running, started: repair.RepairResult, request: Request, username: str, path: str
) -> None:
    """The job (its own thread): walk, publishing the progress, then keep, audit and publish the end.

    Progress and end carry the share's name and the time of the start, as
    the start answered them. The end is published, and another repair may
    start, only after the audit entry was written.
    """
    global _running
    mark = {"share": started.share, "started_at": started.started_at}
    try:
        result = repair.repair_tree(
            path,
            gid=constants.SMB_GROUP_GID,
            progress=lambda progress: _publish(progress.model_copy(update=mark)),
            stop=running.stop,
        )
    except Exception as exc:  # a bug in the walk must still end the job
        logger.exception("Repair permissions of share %s stopped on an unexpected error", started.share)
        result = started.model_copy(update={
            "state": "failed",
            "finished_at": datetime.now(timezone.utc),
            "message": f"The repair stopped on an unexpected error: {sanitize_error_message(exc)}",
        })
    result = result.model_copy(update=mark)
    _last_results[started.share.lower()] = result
    skipped = sum(result.skipped.values())
    details = f"Repaired permissions: {result.changed} changed, {skipped} skipped, {result.errors} errors ({result.state})"
    if running.stopped_by is not None and result.state == "incomplete":
        details += f", stopped by {running.stopped_by}"
    try:
        # log_action is a coroutine; this thread has no event loop of its own.
        asyncio.run(log_action(
            action=AuditAction.SHARE_PERMISSIONS_REPAIRED,
            request=request,
            username=username,
            resource_type="share",
            resource_id=started.share,
            details=details,
            success=result.state != "failed",
        ))
    except OSError:
        pass  # log_action has written the entry to the supervisor's log
    finally:
        _publish(result)
        _running = None
