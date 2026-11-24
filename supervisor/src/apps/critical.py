"""The critical mark of an installed app: its copy on the SD card standby (ADR-0471, ADR-0498).

``GET /apps/standby`` says whether the mark is offered here and whether UNOS runs from the standby's
card; ``GET /apps/installed/{id}/critical`` answers the mark's dialog (what is copied and what is not,
with their sizes, the images, the card's space and the writes of a sync), and ``PUT`` marks or unmarks
the app and sets its sync interval. Administrators only (main.py's ``_ADMIN_ONLY``); a change is
audited as app_critical_changed.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
from typing import TYPE_CHECKING, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from ..audit.changes import audit_change
from ..audit.schemas import AuditAction, AuditSeverity
from ..auth.models import User
from ..auth.utils import require_admin
from ..openapi import BODY, errors
from ..standby import apps as standby_apps
from ..standby import service as standby_service
from ..standby.apps import CriticalPlan

if TYPE_CHECKING:
    import src.apps.router as base
else:
    base = importlib.import_module(".router", __package__)

router = APIRouter(prefix="/apps", tags=["apps"])
logger = logging.getLogger(__name__)

_REFUSED = ("Refused: the SD standby is not set up here or UNOS runs from its card, the app is no app of the "
            "catalog or keeps no data on a pool, it runs from the card, or a new mark would leave the card less "
            "than its reserve.")


class AppsStandby(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"offered": True, "reason": None,
                                                               "running_on_standby": False}]})

    offered: bool = Field(description="Whether apps can be marked critical here: the SD standby is set up and UNOS "
                                      "runs from the eMMC.")
    reason: str | None = Field(description="Why the mark is not offered; null when it is.")
    running_on_standby: bool = Field(description="Whether UNOS runs from the standby's card: only the critical apps "
                                                 "are there.")


class CriticalRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"critical": True, "interval_hours": 6}]})

    critical: bool = Field(description="Whether the app is critical: kept on the SD standby and moved to it when "
                                       "its pool fails.")
    interval_hours: Literal[1, 2, 4, 6, 12, 24] = Field(default=24, description=(
        "Hours between its syncs; 24 syncs it with the system's daily sync."))


@router.get("/standby", response_model=AppsStandby, summary="Whether apps can be marked critical here")
async def apps_standby(_: User = Depends(require_admin)) -> AppsStandby:
    """Whether the SD standby is set up and UNOS runs from the eMMC, so that the Apps page offers the
    critical mark, and whether UNOS runs from the standby's card, where only the critical apps are.
    \f
    ADR-0498.
    """
    if not standby_service._state.read:
        await asyncio.to_thread(standby_service.refresh)
    reason = standby_apps.offered()
    return AppsStandby(offered=reason is None, reason=reason, running_on_standby=standby_service.running_on_standby())


async def _app_key(container_id: str) -> str:
    _, app = await asyncio.to_thread(base._get_app, container_id)
    return app.key


@router.get("/installed/{container_id}/critical", response_model=CriticalPlan,
            summary="What the critical mark of an app copies to the SD standby",
            responses=errors(base.APP_LOOKUP, {409: _REFUSED}))
async def critical_plan(container_id: base.AppContainerId, _: User = Depends(require_admin)) -> CriticalPlan:
    """The folders under {pool}/apps/<app>/ that the standby copies and those it does not (the app's other
    pool folders, its caches, the copies an update keeps), their sizes, its images' size, the card's space
    and reserve, and the MiB of its recent syncs. Reads the pool's folders (du); changes nothing.
    \f
    ADR-0471, ADR-0498.
    """
    reason = standby_apps.offered()
    if reason:
        raise HTTPException(409, detail=reason)
    app = await _app_key(container_id)
    marks = await standby_apps.marks()
    return await asyncio.to_thread(standby_apps.plan, app, intervals_known=marks)


@router.put("/installed/{container_id}/critical", response_model=CriticalPlan,
            summary="Mark an app critical, or not, and set its sync interval",
            responses=errors(BODY, base.APP_LOOKUP, {409: _REFUSED}))
async def set_critical(container_id: base.AppContainerId, body: CriticalRequest, request: Request,
                       user: User = Depends(require_admin)) -> CriticalPlan:
    """Mark the app critical with its sync interval, change the interval, or unmark it, which deletes its
    copies on the card. A new mark that would leave the card less than its reserve (10% of the card, at
    least 2 GiB) is refused. Audited as app_critical_changed when it changed.
    \f
    ADR-0471, ADR-0498.
    """
    reason = standby_apps.offered()
    if reason:
        raise HTTPException(409, detail=reason)
    app = await _app_key(container_id)
    marks = await standby_apps.marks()
    if standby_apps.failed_over(app) is not None:
        raise HTTPException(409, detail=f"{app} runs from the SD standby's card: return it to its pool first")
    if body.critical and app not in marks:
        found = await asyncio.to_thread(standby_apps.plan, app, intervals_known=marks)
        if found.pool is None:
            raise HTTPException(409, detail=f"{app} keeps no data on a pool: it has nothing the standby could copy")
        if not found.fits:
            raise HTTPException(409, detail=(
                f"The SD standby's card cannot take {app}: its copy and its images need about "
                f"{found.needed / 1024**3:.1f} GiB, the card has {(found.card_free or 0) / 1024**3:.1f} GiB free and "
                f"keeps {(found.reserve or 0) / 1024**3:.1f} GiB in reserve"))
    changed, before = await standby_apps.set_mark(app, body.critical, body.interval_hours)
    if changed:
        forgotten = ""
        if not body.critical:
            async with standby_service._lock:
                try:
                    await asyncio.to_thread(standby_apps.forget_copy, app)
                    forgotten = ", its copies on the SD standby deleted"
                except standby_apps.StandbyError as e:
                    logger.warning("The copies of %s on the SD standby could not be deleted: %s", app, e)
                    forgotten = f", its copies on the SD standby left: {e}"
        text = (f"Marked {app} critical, synced every {body.interval_hours} h" if body.critical and before is None
                else f"Changed the sync interval of {app} from {before} h to {body.interval_hours} h" if body.critical
                else f"Unmarked {app}: no longer critical{forgotten}")
        logger.info("%s", text)
        await audit_change(AuditAction.APP_CRITICAL_CHANGED, request, user.username, resource_type="app",
                           resource_id=app, details=text,
                           severity=AuditSeverity.INFO if body.critical else AuditSeverity.WARNING)
        await standby_service.update_problems()
    return await asyncio.to_thread(standby_apps.plan, app, intervals_known=await standby_apps.marks())
