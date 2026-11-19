"""The iSCSI targets' API, under /api/v1/shares/iscsi (ADR-0417): administrators only (main.py).

The logic is shares/iscsi.py's. Where host commands run locally (UNOS_HOST_EXEC=local) every route
answers 503 and runs nothing. A secret is only in the answers of a create and of a change that sets
a new one; no audit entry holds one.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit.changes import audit_change
from ..audit.schemas import AuditAction, AuditSeverity
from ..auth.utils import verify_token
from ..database import get_db
from . import iscsi
from .iscsi import (
    IscsiState,
    IscsiTargetChange,
    IscsiTargetChanged,
    IscsiTargetCreate,
    IscsiTargetCreated,
    IscsiTargetDelete,
    IscsiTargetDeleted,
)

router = APIRouter(prefix="/shares/iscsi", tags=["iscsi"])

_ERRORS = {
    500: {"description": "A host command failed; the text names it, never a secret."},
    503: {"description": "UNOS runs its host commands locally (UNOS_HOST_EXEC=local): not a NAS host."},
}


@router.get(
    "",
    response_model=IscsiState,
    summary="List the iSCSI targets",
    description=(
        "Every iSCSI target with its LUN, its allowed initiators, whether the kernel serves it and the "
        "initiators logged in, and the portals on which the targets listen. No secret."
    ),
    responses=_ERRORS,
)
async def list_targets(_=Depends(verify_token), db: AsyncSession = Depends(get_db)):
    return await iscsi.list_state(db)


@router.post(
    "/targets",
    response_model=IscsiTargetCreated,
    status_code=201,
    summary="Create an iSCSI target",
    description=(
        "Make a sparse LUN file in a volume and serve it as a target with CHAP for the allowed "
        "initiators. The answer holds the CHAP secrets this once."
    ),
    responses={
        400: {"description": "A value breaks its rule."},
        404: {"description": "No such pool or volume."},
        409: {"description": "The name is taken, the pool is not mounted, or the LUN does not fit."},
        **_ERRORS,
    },
)
async def create_target(
    request: Request, spec: IscsiTargetCreate, user=Depends(verify_token), db: AsyncSession = Depends(get_db)
):
    created = await iscsi.create_target(db, spec)
    await audit_change(
        AuditAction.ISCSI_TARGET_CREATED, request, user.username, resource_type="iscsi_target",
        resource_id=created.target.name, details=f"Created the iSCSI target {iscsi.describe(created.target)}",
    )
    return created


@router.patch(
    "/targets/{name}",
    response_model=IscsiTargetChanged,
    summary="Change an iSCSI target",
    description=(
        "Change the allowed initiators, set a new CHAP secret, or turn mutual CHAP on or off. A new "
        "secret is in the answer this once."
    ),
    responses={
        400: {"description": "A value breaks its rule."},
        404: {"description": "No such target."},
        409: {"description": "No secret is stored and the change sets none."},
        **_ERRORS,
    },
)
async def change_target(
    request: Request, name: str, spec: IscsiTargetChange, user=Depends(verify_token),
    db: AsyncSession = Depends(get_db),
):
    change = await iscsi.change_target(db, name, spec)
    if change.details:
        await audit_change(
            AuditAction.ISCSI_TARGET_CHANGED, request, user.username, resource_type="iscsi_target",
            resource_id=name, details=f"Changed the iSCSI target {name}: {'; '.join(change.details)}",
        )
    return IscsiTargetChanged(target=change.target, chap_secret=change.chap_secret, mutual_secret=change.mutual_secret)


@router.post(
    "/targets/{name}/delete",
    response_model=IscsiTargetDeleted,
    summary="Delete an iSCSI target and its LUN",
    description=(
        "End the target's sessions and delete it with its LUN file, its data included. The target's "
        "name must be typed again in confirm."
    ),
    responses={
        400: {"description": "confirm is not the target's name."},
        404: {"description": "No such target."},
        409: {"description": "The pool of its volume is not mounted."},
        **_ERRORS,
    },
)
async def delete_target(
    request: Request, name: str, body: IscsiTargetDelete, user=Depends(verify_token),
    db: AsyncSession = Depends(get_db),
):
    row, sessions = await iscsi.delete_target(db, name, body.confirm)
    ended = f"; it ended the sessions of {', '.join(sessions)}" if sessions else ""
    await audit_change(
        AuditAction.ISCSI_TARGET_DELETED, request, user.username, resource_type="iscsi_target",
        resource_id=name, details=f"Deleted the iSCSI target {iscsi.describe(row)} with the data of its LUN{ended}",
        severity=AuditSeverity.WARNING,
    )
    return IscsiTargetDeleted(name=name, sessions=sessions)
