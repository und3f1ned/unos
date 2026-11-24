"""``/api/v1/system/standby``: the SD card standby (ADR-0471), for administrators only (main.py's
``_ADMIN_ONLY``). Every change is audited (ADR-0402); each action but the daily time's takes
``dry_run``, which answers the host's commands it would run and changes nothing.
"""

from __future__ import annotations

from typing import Literal

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
    ADR-0471, ADR-0481.
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
    ADR-0471, ADR-0481.
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
    ADR-0471, ADR-0481.
    """
    return await service.sync_now(dry_run=body.dry_run, request=request, username=user.username)


@router.put("/settings", status_code=204, summary="Set the daily sync's time",
            responses=errors(BODY, {400: "The time is not HH:MM.", **_REFUSED, **_FAILED}))
async def settings(body: Settings, request: Request, user=Depends(require_admin)) -> None:
    """Keep the daily sync's time in the card's state.json. Audited as standby_settings_changed when it changed.
    \f
    ADR-0471, ADR-0481.
    """
    await service.set_sync_time(body.sync_time, request=request, username=user.username)


@router.post("/drill", response_model=StandbyAction, summary="Start a drill: switch to the card and reboot",
             responses=errors(BODY, _REFUSED))
async def drill(body: Drill, request: Request, user=Depends(require_admin)) -> StandbyAction:
    """Write the drill's file on the card's FAT partition and reboot the host a moment after the answer.
    Audited as standby_drill.
    \f
    ADR-0471, ADR-0481.
    """
    return await service.drill(body.kind, dry_run=body.dry_run, request=request, username=user.username)


class EmmcReturn(DryRun):
    model_config = ConfigDict(json_schema_extra={"examples": [{"discard": False, "dry_run": True}]})

    discard: bool = Field(default=False, description=(
        "false: copy the card's changes to the eMMC, then reboot to it; true: reboot to the eMMC as it was, whose "
        "system then offers to discard the card's changes."))


@router.post("/return", response_model=StandbyAction, summary="On the standby: return to the eMMC and reboot",
             responses=errors(BODY, {409: "Refused: UNOS does not run from the card, the eMMC does not read or its "
                                          "root does not pass fsck.ext4 -n (the card stays the system disk), or a "
                                          "condition of the copy is not met."}, _FAILED))
async def return_to_emmc(body: EmmcReturn, request: Request, user=Depends(require_admin)) -> StandbyAction:
    """On the card's root, once the eMMC reads and its root passes fsck.ext4 -n: in a transient unit of the
    host, stop Docker and Samba, copy the standby's root into the eMMC's root and UNOS's volumes into the
    eMMC's Docker volumes, write the eMMC's fstab, delete the mark, the drill and the run, set the tries
    counter to 0 and reboot. With discard: delete the mark and the drill, set the counter to 0 and reboot.
    Audited as standby_returned.
    \f
    ADR-0471.
    """
    return await service.return_to_emmc(discard=body.discard, dry_run=body.dry_run, request=request,
                                        username=user.username)


@router.post("/discard", response_model=StandbyAction, summary="Discard the changes of a standby that ran",
             responses=errors(BODY, _REFUSED, {500: "The new generation failed: the standby keeps its changes."}))
async def discard(body: Confirmed, request: Request, user=Depends(require_admin)) -> StandbyAction:
    """On the eMMC's system after the standby ran: write a new generation from the eMMC, its Docker data
    emptied, and forget the run. Audited as standby_discarded.
    \f
    ADR-0471.
    """
    return await service.discard(body.model, dry_run=body.dry_run, request=request, username=user.username)


class AppsMove(DryRun):
    model_config = ConfigDict(json_schema_extra={"examples": [{"reason": "planned", "pool": "usb", "dry_run": False}]})

    reason: Literal["drill", "planned"] = Field(description=(
        "drill: Move the critical apps to the card on the Standby tab; planned: before an unmount or a removal "
        "of their pool on the Storage page."))
    pool: str | None = Field(default=None, max_length=64, description=(
        "The pool whose critical apps move; null: every critical app (a drill)."))


class AppsReturn(Confirmed):
    model_config = ConfigDict(json_schema_extra={"examples": [{"apps": ["vaultwarden"], "discard": False, "dry_run": True}]})

    apps: list[str] | None = Field(default=None, max_length=64, description=(
        "The apps that return; null: every app that runs from the card and whose pool can take it back."))
    discard: bool = Field(default=False, description=(
        "false: the pool return, which copies what the apps changed on the card into their folders on the pool and "
        "keeps every file it replaces in {pool}/apps/<app>.before-failback-<time>/; true: the return that discards "
        "the card's changes, confirmed by the card's model typed."))


class AppMoved(BaseModel):
    app: str = Field(description="The app.")
    has_copy: bool = Field(description="Whether it runs from its copy; false: it had none and stays stopped.")
    age: str | None = Field(description="Its copy's age, such as 3 h.")
    error: str | None = Field(description="Why its move failed; null when it did not.")
    mib: float | None = Field(default=None, description=(
        "The pool return: the MiB its copy writes to the pool (a dry run) or wrote; null without a copy."))
    backup: str | None = Field(default=None, description=(
        "The pool return: the folder that keeps the files that the copy replaced or deleted."))


class AppsAction(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "dry_run": False, "apps": [{"app": "vaultwarden", "has_copy": True, "age": "3 h", "error": None}],
        "message": "1 critical app(s) to the card"}]})

    dry_run: bool = Field(description="Whether nothing was changed.")
    apps: list[AppMoved] = Field(description="The apps the action took, with what happened to each.")
    message: str = Field(description="What was done, in English.")


@router.post("/apps/move", response_model=AppsAction, summary="Move the critical apps to the card",
             responses=errors(BODY, {409: "The standby is not set up, its card is missing, or no critical app is left "
                                          "to move.", 500: "Every app's move failed: the detail names why."}))
async def move_apps(body: AppsMove, request: Request, user=Depends(require_admin)) -> AppsAction:
    """Recreate the critical apps' containers on their copies on the card, as the failover of a pool does:
    the binds under {pool}/apps/<app>/ on the copy, a cache on a tmpfs, every other pool bind on an empty
    read-only folder of the card; those that ran start. Audited as standby_apps_moved, alerted
    (apps_on_standby).
    \f
    ADR-0471, ADR-0500.
    """
    moved = await service.move_apps(body.reason, body.pool, dry_run=body.dry_run, request=request,
                                    username=user.username)
    return AppsAction(dry_run=body.dry_run, apps=[
        AppMoved(app=item.app, has_copy=item.copy, age=item.age, error=item.error) for item in moved],
        message=("A dry run: " if body.dry_run else "") + f"{len(moved)} critical app(s) to the card")


@router.post("/apps/return", response_model=AppsAction, summary="Return the critical apps to their pool",
             responses=errors(BODY, {409: "The typed model of a discarding return does not match, no app runs from "
                                          "the card, an app's pool cannot take it back, or a sync runs.",
                                     500: "A return failed: the detail names the app and its step; the app stays "
                                          "stopped and its copy on the card as it was."}))
async def return_apps(body: AppsReturn, request: Request, user=Depends(require_admin)) -> AppsAction:
    """The pool return (the default): stop the apps' containers on the card, copy what the apps changed on the card
    into {pool}/apps/<app>/ (not their caches and update copies), keeping every file it replaces or deletes in
    {pool}/apps/<app>.before-failback-<time>/, create the pool-bound containers again from their definitions,
    start those that ran and delete the card's run copies. A dry run answers the MiB each copy writes. With
    discard, the card's changes are discarded instead. Each app's pool must be mounted, writable and healthy.
    Audited as standby_apps_returned and alerted.
    \f
    ADR-0471, ADR-0500.
    """
    returned = await service.return_apps(body.apps, body.model, discard=body.discard, dry_run=body.dry_run,
                                         request=request, username=user.username)
    if body.discard:
        items = [AppMoved(app=app, has_copy=True, age=None, error=None) for app in returned]
    else:
        items = [AppMoved(app=item.app, has_copy=item.copy, age=None, error=None, mib=item.mib, backup=item.backup)
                 for item in returned]
    return AppsAction(dry_run=body.dry_run, apps=items,
                      message=("A dry run: " if body.dry_run else "") + f"{len(returned)} app(s) back on their pool")
