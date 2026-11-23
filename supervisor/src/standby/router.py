"""``/api/v1/system/standby``: the SD card standby (ADR-0471), for administrators only (main.py's
``_ADMIN_ONLY``). Every change is audited (ADR-0402); each action but the daily time's takes
``dry_run``, which answers the host's commands it would run and changes nothing.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from ..auth.utils import require_admin
from ..openapi import BODY, errors
from . import service
from .service import DrillKind, StandbyAction, StandbyStatus

router = APIRouter(prefix="/system/standby", tags=["system"])

_REFUSED = {409: "Refused: a condition is not met, the typed model does not match, the standby is not in the state "
                 "the action needs, a sync runs, or this supervisor runs outside its container."}
_FAILED = {500: "A command on the host failed: the detail names the step."}


class DryRun(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"dry_run": True}]})

    dry_run: bool = Field(default=False, description="Answer the host's commands that would run, and change nothing.")


class Confirmed(DryRun):
    model_config = ConfigDict(json_schema_extra={"examples": [{"model": "SC32G", "dry_run": False}]})

    model: str = Field(default="", max_length=128, description="The card's model, typed by the administrator to confirm.")


class Drill(DryRun):
    model_config = ConfigDict(json_schema_extra={"examples": [{"kind": "emmc_root_missing", "dry_run": False}]})

    kind: DrillKind = Field(description=(
        "mark: unos/emmc-failed with the reason drill (also Return through the standby after the standby ran); "
        "emmc_unreadable: unos/drill.txt names an eMMC partition that does not exist; emmc_root_missing: "
        "unos/drill.txt gives the eMMC's kernel a root that does not exist. Each then reboots the host."))


class Settings(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"sync_time": "04:00"}]})

    sync_time: str = Field(max_length=5, description="The daily sync's time on the host's clock, HH:MM.")


@router.get("", response_model=StandbyStatus, summary="The SD card standby: its conditions and its state")
async def get_standby(_=Depends(require_admin)) -> StandbyStatus:
    """Each condition of ADR-0471 with what this host shows, the card that is or would be the standby,
    this boot's choice, the copy's generation, the last sync and the standby's problems. Reads only.
    \f
    ADR-0471, ADR-0476.
    """
    return await service.status()


@router.post("/arm", response_model=StandbyAction, summary="Set up the standby on the free SD card",
             responses=errors(BODY, _REFUSED, {500: "A step on the host failed, or the first sync failed after the "
                                                    "card was set up: the detail says which."}))
async def arm(body: Confirmed, request: Request, user=Depends(require_admin)) -> StandbyAction:
    """Wipe the free SD card whose model was typed, write the board's loader, its partitions, UNOS's fstab
    lines, the chooser and the units of the eMMC's system, then the first generation. Audited as
    standby_armed.
    \f
    ADR-0471, ADR-0476.
    """
    return await service.arm(body.model, dry_run=body.dry_run, request=request, username=user.username)


@router.delete("", response_model=StandbyAction, summary="Remove the standby and wipe its card",
               responses=errors(BODY, _REFUSED, _FAILED))
async def remove(body: Confirmed, request: Request, user=Depends(require_admin)) -> StandbyAction:
    """Unmount the card, drop UNOS's fstab lines, units and mount points, and wipe the card with its
    loader area, so that it is a free card again. Audited as standby_removed.
    \f
    ADR-0471.
    """
    return await service.remove(body.model, dry_run=body.dry_run, request=request, username=user.username)


@router.post("/sync", response_model=StandbyAction, summary="Sync the system's copy now",
             responses=errors(BODY, _REFUSED, {500: "The sync failed: the previous generation stays current."}))
async def sync(body: DryRun, request: Request, user=Depends(require_admin)) -> StandbyAction:
    """Compare the eMMC's system with the card's copy and, when something changed, write a new generation
    and make it current. Audited as standby_synced.
    \f
    ADR-0471, ADR-0476.
    """
    return await service.sync_now(dry_run=body.dry_run, request=request, username=user.username)


@router.put("/settings", status_code=204, summary="Set the daily sync's time",
            responses=errors(BODY, {400: "The time is not HH:MM.", **_REFUSED, **_FAILED}))
async def settings(body: Settings, request: Request, user=Depends(require_admin)) -> None:
    """Keep the daily sync's time in the card's state.json. Audited as standby_settings_changed when it changed.
    \f
    ADR-0471, ADR-0476.
    """
    await service.set_sync_time(body.sync_time, request=request, username=user.username)


@router.post("/drill", response_model=StandbyAction, summary="Start a drill: switch to the card and reboot",
             responses=errors(BODY, _REFUSED))
async def drill(body: Drill, request: Request, user=Depends(require_admin)) -> StandbyAction:
    """Write the drill's file on the card's FAT partition and reboot the host a moment after the answer.
    Audited as standby_drill.
    \f
    ADR-0471, ADR-0476.
    """
    return await service.drill(body.kind, dry_run=body.dry_run, request=request, username=user.username)


@router.post("/return", response_model=StandbyAction, summary="On the standby: return to the eMMC and reboot",
             responses=errors(BODY, _REFUSED))
async def return_to_emmc(body: DryRun, request: Request, user=Depends(require_admin)) -> StandbyAction:
    """On the card's root: delete the mark and the drill, set the tries counter to 0 and reboot to the
    eMMC. Audited as standby_returned.
    \f
    ADR-0471.
    """
    return await service.return_to_emmc(dry_run=body.dry_run, request=request, username=user.username)


@router.post("/discard", response_model=StandbyAction, summary="Discard the changes of a standby that ran",
             responses=errors(BODY, _REFUSED, {500: "The new generation failed: the standby keeps its changes."}))
async def discard(body: Confirmed, request: Request, user=Depends(require_admin)) -> StandbyAction:
    """On the eMMC's system after the standby ran: write a new generation from the eMMC, its Docker data
    emptied, and forget the run. Audited as standby_discarded.
    \f
    ADR-0471.
    """
    return await service.discard(body.model, dry_run=body.dry_run, request=request, username=user.username)
