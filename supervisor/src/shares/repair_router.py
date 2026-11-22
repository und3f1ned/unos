"""Repair permissions endpoints (ADR-0363).

POST starts a repair of a share's folder (repair.repair_tree) as a background
job in a thread of its own and answers at once (202) with the job running; one
repair runs at a time. The job, while it runs and once it ended, is
``current_job()``, which the events socket sends to administrators
(websocket/events.py). DELETE asks the running repair of a share to stop. GET
answers the last finished result of a share since UNOS started. A deletion of
the share, or a change of its name or folder, stops its repair as well
(``stop_for_share``, which shares/service.py calls), and a stop of the
supervisor stops it and waits a moment for its audit entry
(``stop_for_shutdown``, from main.py's lifespan). The job and the results are
kept in memory only: a restart of the supervisor forgets them.

main.py includes this router in the administrator-only routers; the endpoints
also require an administrator themselves.
"""

import asyncio
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, UTC
from pathlib import PurePosixPath
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from sqlalchemy import Row, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit.router import log_action
from ..audit.schemas import AuditAction
from ..auth.models import User
from ..auth.utils import require_admin
from ..database import get_db
from ..openapi import RATE_LIMITED, errors
from ..rate_limits import client_limiter
from ..security.validators import pools_base, sanitize_error_message
from . import accounts, constants, models, repair, service

logger = logging.getLogger(__name__)

# Rate limiter for the repair endpoint
limiter = client_limiter()

router = APIRouter(prefix="/shares", tags=["shares"])

ShareName = Annotated[str, Path(description="The share's name, in any letter case.")]


@dataclass
class _Running:
    """The repair that runs: its share and how to stop it."""

    share_id: int
    name: str  # the share's name when it started: a stop finds the repair by it once the share is gone
    stop: threading.Event = field(default_factory=threading.Event)
    stopped_by: str | None = None  # who or what asked it to stop, for its audit entry
    share_deleted: bool = False  # its share was deleted: its result is not kept
    thread: threading.Thread | None = None


# The repair that runs, or None: one at a time. Set and checked on the event
# loop when a repair starts; its thread clears it once the walk has ended.
_running: _Running | None = None
# The running repair, or the last one since UNOS started, of any share; None
# before the first. Each change is a new object, which the events socket sends.
_job: repair.RepairResult | None = None
# The last result of each share since UNOS started, by the share's id: a rename keeps it, and a
# deletion drops it (a new share can get the id again).
_last_results: dict[int, repair.RepairResult] = {}
# Held while a repair's end is published, and while a share's deletion drops its result: an end
# that comes during the deletion keeps no result.
_end_lock = threading.Lock()
# How long a stop of the supervisor waits for the stopped walk to write its audit entry.
SHUTDOWN_WAIT_SECONDS = 5.0

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


@router.post(
    "/smb/{name}/repair-permissions", response_model=repair.RepairResult, status_code=202,
    summary="Start a repair of a share's permissions",
    responses=errors(RATE_LIMITED, {
        404: "No share has the name, also when it was deleted while it was checked.",
        409: "Nothing was changed: the share was changed while it was checked, it is not served, its pool is not "
             "mounted or is read-only, a folder above its folder can be changed by other accounts, another host "
             "group has the gid of unos-smb, or another repair is running.",
        500: "A check of the share failed unexpectedly, or the repair could not be started.",
        503: "The pool list could not be read.",
    }),
)
@limiter.limit("6/minute")
async def repair_share_permissions(
    request: Request,
    name: ShareName,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
) -> repair.RepairResult:
    """Start a repair of the permissions of a share's folder in the background; the answer is the
    repair running, and the events socket sends its progress. One repair runs at a time.

    Every file and folder of the share gets the group unos-smb and group
    read/write (folders 2770, files 0660 or 0770); other accounts lose access.
    Symlinks, hard links, special files, private entries, .snapshots, other
    filesystems and mount points are skipped. The repair runs on until the
    share is done, it is stopped (DELETE) or 24 hours have passed (the state
    incomplete: run it again to continue).
    \f
    ADR-0363. A share that is not served breaks a rule of ADR-0145.
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
        logger.exception("Could not read the share's problem")
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
        logger.exception("Could not check the pool of the share")
        raise HTTPException(status_code=500, detail=sanitize_error_message(exc)) from exc
    # Whoever can change a folder above the share's folder can swap in another folder,
    # whose files the walk would then give the SMB group. Read-only check, nothing changed.
    try:
        unsafe = await asyncio.to_thread(service.unsafe_parent_of, share.path)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Could not check the folders above the share")
        raise HTTPException(status_code=500, detail=sanitize_error_message(exc)) from exc
    if unsafe is not None:
        raise HTTPException(status_code=409, detail=unsafe.refusal())
    try:
        problem = await asyncio.to_thread(accounts.gid_problem)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Could not check the SMB group")
        raise HTTPException(status_code=500, detail=sanitize_error_message(exc)) from exc
    if problem is not None:
        raise HTTPException(status_code=409, detail=problem)

    # No await from the check to the start: two starts cannot both pass it.
    if _running is not None:
        raise HTTPException(status_code=409, detail=ALREADY_RUNNING)
    running = _Running(share_id=share.id, name=share.name)
    started = repair.RepairResult(
        share=share.name, state="running", started_at=datetime.now(UTC), finished_at=None, visited=0,
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
        running.thread = threading.Thread(
            target=_repair_and_record,
            args=(running, started, request, username, share.path),
            name="share-repair",
            daemon=True,
        )
        running.thread.start()
    except RuntimeError as exc:  # no thread could be started: nothing runs
        _running = None
        _publish(before)
        raise HTTPException(status_code=500, detail=f"The repair could not be started: {exc}") from exc
    return started


@router.delete(
    "/smb/{name}/repair-permissions", response_model=repair.RepairResult, status_code=202,
    summary="Stop the repair of a share's permissions",
    responses=errors({
        404: "No share has the name, and no running repair started with it.",
        409: "No repair of this share is running.",
    }),
)
async def stop_repair(
    name: ShareName,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
) -> repair.RepairResult:
    """Ask the running repair of a share's permissions to stop; the answer is the repair as it is now.

    The walk stops before its next entry and ends incomplete: what it
    changed stays changed, and a new run continues quickly. The repair of a
    share that is gone (deleted while a call of the walk on its folder had
    not returned) is found by the name it started with.
    \f
    ADR-0363.
    """
    result = await db.execute(
        select(models.SmbShare.id).where(models.SmbShare.name == name).order_by(models.SmbShare.id)
    )
    share_id = result.scalar()
    running = _running
    if running is not None and (
        running.share_id == share_id
        or (running.name.lower() == name.lower() and await _share_by_id(db, running.share_id) is None)
    ):
        if running.stopped_by is None:
            running.stopped_by = admin.username
        running.stop.set()
        assert _job is not None  # _publish set it when the repair started
        return _job
    if share_id is None:
        raise HTTPException(status_code=404, detail=SHARE_NOT_FOUND)
    raise HTTPException(status_code=409, detail=NOT_RUNNING)


@router.get(
    "/smb/{name}/repair-permissions", response_model=repair.RepairResult,
    summary="Read the last repair of a share's permissions",
    responses=errors({404: "No share has the name, or no repair of it has ended since the supervisor started."}),
)
async def get_repair_result(
    name: ShareName,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> repair.RepairResult:
    """The result of the last repair of a share's permissions that ended since the supervisor started;
    the results are kept in memory only."""
    share = await _find_share(db, name)
    result = _last_results.get(share.id)
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
    if not isinstance(path, str):  # a restored row may hold anything
        return None
    try:
        parts = PurePosixPath(path).relative_to(pools_base()).parts
    except (TypeError, ValueError):
        return None
    return parts[0] if parts else None


def stop_for_share(share_id: int, stopped_by: str, *, deleted: bool = False) -> None:
    """Stop the running repair of the share ``share_id``, if one runs, naming ``stopped_by`` in its
    audit entry; with ``deleted`` (the share was deleted), also forget the share's last result.

    shares/service.py calls it once a deletion of the share, or a change of its name or folder, is
    committed: the walk would go on in a folder that is no longer the share's, under a name that
    no longer finds it.
    """
    with _end_lock:
        if deleted:
            _last_results.pop(share_id, None)
        running = _running
        if running is None or running.share_id != share_id:
            return
        if deleted:
            running.share_deleted = True
    if running.stopped_by is None:
        running.stopped_by = stopped_by
    running.stop.set()


def stop_for_shutdown(timeout: float = SHUTDOWN_WAIT_SECONDS) -> None:
    """Stop the running repair, if one runs, and wait up to ``timeout`` seconds for its end, so that
    its audit entry is written before the supervisor stops (main.py's lifespan). Blocking.

    A walk blocked in a call on the folder (a stalled disk) does not end in time: the supervisor
    stops without its entry.
    """
    running = _running
    if running is None:
        return
    if running.stopped_by is None:
        running.stopped_by = "a stop of the supervisor"
    running.stop.set()
    if running.thread is not None:
        running.thread.join(timeout)


def _repair_and_record(
    running: _Running, started: repair.RepairResult, request: Request, username: str, path: str
) -> None:
    """The job (its own thread): walk, publishing the progress, then audit, keep and publish the end.

    Progress and end carry the share's name and the time of the start, as
    the start answered them. Only once the audit entry is written does the
    end become the share's last result and the job, and can another repair
    start: all three at the same moment.
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
            "finished_at": datetime.now(UTC),
            "message": f"The repair stopped on an unexpected error: {sanitize_error_message(exc)}",
        })
    result = result.model_copy(update=mark)
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
        with _end_lock:
            if not running.share_deleted:
                _last_results[running.share_id] = result
            _publish(result)
            _running = None
