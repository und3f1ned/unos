"""The off-device copy's API (ADR-0337): the copy target, Run now, the state, the snapshots and the restores.

Administrators only: main.py includes the router with the others that are (_ADMIN_ONLY), and the
router asks for an administrator itself, since a restore writes files into the pools and the
settings decide where the configuration and the data go. service.py holds the logic.
"""

from fastapi import APIRouter, Depends, Request

from ..audit.changes import audit_change
from ..audit.schemas import AuditAction, AuditSeverity
from ..auth.models import User
from ..auth.utils import require_admin
from . import service

router = APIRouter(prefix="/offsite", tags=["offsite"], dependencies=[Depends(require_admin)])


@router.get("/settings")
async def get_settings():
    """The copy target and what is copied, without secrets: has_password and has_s3_secret_access_key
    say whether they are stored. pools: the managed pools, which a copy takes by default."""
    return await service.settings_answer()


@router.put("/settings")
async def put_settings(
    request: service.SettingsRequest, http_request: Request, user: User = Depends(require_admin)
):
    """Check the settings and test the target: restic opens its repository with the password, or makes
    one when none is there. 400 with restic's message for a wrong password or a store out of reach.
    The audit log gets settings_changed when they changed, with the names of the fields and never a
    secret (ADR-0402)."""
    changes: list[tuple[str, str]] = []
    answer = await service.save_settings(request, changes)
    for where, details in changes:
        await audit_change(AuditAction.SETTINGS_CHANGED, http_request, user.username, resource_type="offsite",
                           resource_id=where, details=details)
    return answer


@router.delete("/settings")
async def delete_settings(http_request: Request, user: User = Depends(require_admin)):
    """Forget the target: the settings, the secrets and the state. The copies stay where they are.
    The audit log gets settings_changed when there was something to forget (ADR-0402)."""
    changes: list[tuple[str, str]] = []
    answer = await service.remove_settings(changes)
    for where, details in changes:
        await audit_change(AuditAction.SETTINGS_CHANGED, http_request, user.username, resource_type="offsite",
                           resource_id=where, details=details, severity=AuditSeverity.WARNING)
    return answer


@router.get("/status")
async def get_status():
    """What runs, and the last copy and restore of files."""
    return await service.status_answer()


@router.post("/run", status_code=202)
async def run_now():
    """Start a copy in the background; GET /status shows it."""
    await service.start_copy()
    return {"status": "started"}


@router.get("/snapshots")
async def get_snapshots():
    """The snapshots of the copy repository, newest first."""
    return await service.list_snapshots()


@router.post("/restore-configuration")
async def restore_configuration(request: service.RestoreConfigurationRequest):
    """Put the configuration of a snapshot into the backup list (offsite_<time>.tar.gz), where it is
    restored with the checks of a restore."""
    return await service.restore_configuration(request.snapshot_id)


@router.post("/restore-files", status_code=202)
async def restore_files(request: service.RestoreFilesRequest):
    """Restore a snapshot, or a folder of it, into a new folder restored-<UTC time> at the top of a
    pool, in the background; GET /status shows it."""
    return await service.restore_files(request)
