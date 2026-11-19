"""API router for update endpoints."""

import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from ..auth.utils import verify_token
from ..openapi import BODY, RATE_LIMITED, answer, errors
from .schemas import (
    CurrentVersion,
    ReleaseInfo,
    UpdateCheck,
    UpdateConfig,
    UpdateHistoryEntry,
    UpdateProgress,
    UpdateStatus,
)
from .updater import UpdateError, get_updater

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/updates", tags=["updates"])

# Rate limiter for update checks (prevent abuse)
limiter = Limiter(key_func=get_remote_address)

# The API documentation (ADR-0417).
_Prereleases = Annotated[bool, Query(
    description="true: the newest of GitHub's releases, which may be a prerelease; false: the latest stable release."
)]
_GITHUB_FAILED = {
    500: "The request failed unexpectedly.",
    502: "GitHub could not be asked, or answered with an error.",
}
_RUNS = "An update or a rollback runs."


@router.get("/current", response_model=CurrentVersion, summary="Read the installed version")
async def get_current_version(_: dict = Depends(verify_token)):
    """Get the currently installed version: the one the supervisor's image was built with."""
    updater = get_updater()
    return await updater.get_current_version()


@router.get(
    "/check", response_model=UpdateCheck, summary="Check for a newer release",
    responses=errors(RATE_LIMITED, _GITHUB_FAILED),
)
@limiter.limit("10/minute")
async def check_for_updates(
    request: Request,
    include_prereleases: _Prereleases = False,
    _: dict = Depends(verify_token),
):
    """
    Check for available updates from GitHub releases: the latest release, and whether it is newer than
    the installed version.

    Rate limited to 10 checks per minute to prevent abuse.
    """
    updater = get_updater()
    try:
        return await updater.check_for_updates(
            include_prereleases=include_prereleases
        )
    except UpdateError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception:
        logger.exception("Update check failed")
        raise HTTPException(status_code=500, detail="Failed to check for updates")


@router.get(
    "/latest", response_model=ReleaseInfo, summary="Read the latest release",
    responses=errors(RATE_LIMITED, _GITHUB_FAILED, {404: "GitHub lists no release."}),
)
@limiter.limit("10/minute")
async def get_latest_release(
    request: Request,
    include_prereleases: _Prereleases = False,
    _: dict = Depends(verify_token),
):
    """Get detailed information about the latest available release. Rate limited to 10 requests per
    minute."""
    updater = get_updater()
    try:
        release = await updater.fetch_latest_release(
            include_prereleases=include_prereleases
        )
        if release is None:
            raise HTTPException(status_code=404, detail="No releases found")
        return release
    except UpdateError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to fetch the latest release")
        raise HTTPException(status_code=500, detail="Failed to fetch release info")


@router.post(
    "/install", summary="Install an update",
    responses={
        **answer({"status": "started", "message": "Installing update to v1.3.0", "version": "v1.3.0"},
                 "status started, a message and the release's tag; GET /updates/progress shows how it goes."),
        **errors({
            400: "That release is installed already, or GitHub could not be asked for it.",
            404: "GitHub lists no release, or version is not the tag of the latest stable release.",
            409: _RUNS,
            500: "The update could not be started.",
        }),
    },
)
async def install_update(
    background_tasks: BackgroundTasks,
    version: Annotated[str | None, Query(
        description="The tag of the latest stable release, such as v1.3.0; none for that release."
    )] = None,
    _: dict = Depends(verify_token),
):
    """
    Install an update.

    If version is not specified, installs the latest stable release; a version that is not its tag
    answers 404. The update backs up the installed version first.
    The update runs in the background - poll /updates/progress to monitor status.
    """
    updater = get_updater()

    # Check if update is already in progress
    progress = updater.get_progress()
    if progress.status not in [UpdateStatus.IDLE, UpdateStatus.COMPLETED, UpdateStatus.FAILED]:
        raise HTTPException(
            status_code=409,
            detail=f"Update already in progress: {progress.status.value}",
        )

    try:
        # Get the release to install
        if version:
            # Fetch specific version (would need to add this method)
            release = await updater.fetch_latest_release()
            if release is None or release.tag_name != version:
                raise HTTPException(status_code=404, detail=f"Version {version} not found")
        else:
            release = await updater.fetch_latest_release()
            if release is None:
                raise HTTPException(status_code=404, detail="No releases available")

        # Check if already on this version
        current = await updater.get_current_version()
        if current.version == release.tag_name:
            raise HTTPException(
                status_code=400,
                detail=f"Already running version {release.tag_name}",
            )

        # Start update in background
        async def run_update():
            try:
                await updater.install_update(release)
            except Exception as e:
                logger.error(f"Background update failed: {e}")

        background_tasks.add_task(asyncio.create_task, run_update())

        return {
            "status": "started",
            "message": f"Installing update to {release.tag_name}",
            "version": release.tag_name,
        }

    except HTTPException:
        raise
    except UpdateError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.exception("Failed to start the update")
        raise HTTPException(status_code=500, detail="Failed to start update")


@router.get("/progress", response_model=UpdateProgress, summary="Read the update's progress")
async def get_update_progress(_: dict = Depends(verify_token)):
    """Get the current update progress: of the update or rollback that runs, or of the last one."""
    updater = get_updater()
    return updater.get_progress()


@router.post(
    "/rollback", summary="Roll back the last update",
    responses={
        **answer({"status": "started", "message": "Rolling back to previous version"},
                 "status started and a message; GET /updates/progress shows how it goes."),
        **errors({
            400: "No update backed up the version before it: can_rollback of GET /updates/progress is false.",
            409: _RUNS,
        }),
    },
)
async def rollback_update(
    background_tasks: BackgroundTasks,
    _: dict = Depends(verify_token),
):
    """
    Rollback to the previous version, in the background.

    Only available once an update has backed up the version before it (can_rollback), also when
    that update failed later.
    """
    updater = get_updater()

    progress = updater.get_progress()
    if not progress.can_rollback:
        raise HTTPException(
            status_code=400,
            detail="Rollback not available - no recent backup exists",
        )

    if progress.status not in [UpdateStatus.IDLE, UpdateStatus.COMPLETED, UpdateStatus.FAILED]:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot rollback during update: {progress.status.value}",
        )

    async def run_rollback():
        try:
            await updater.rollback()
        except Exception as e:
            logger.error(f"Background rollback failed: {e}")

    background_tasks.add_task(asyncio.create_task, run_rollback())

    return {"status": "started", "message": "Rolling back to previous version"}


@router.post(
    "/reset", summary="Reset the update's state",
    responses={
        **answer({"status": "ok", "message": "Update status reset"}, "status ok and a message."),
        **errors({409: "An update downloads or installs, or a rollback runs."}),
    },
)
async def reset_update_status(_: dict = Depends(verify_token)):
    """Reset the update status to idle (useful after failed updates); can_rollback turns false."""
    updater = get_updater()
    progress = updater.get_progress()

    if progress.status in [UpdateStatus.DOWNLOADING, UpdateStatus.INSTALLING, UpdateStatus.ROLLBACK]:
        raise HTTPException(
            status_code=409,
            detail="Cannot reset while update is in progress",
        )

    updater.reset_progress()
    return {"status": "ok", "message": "Update status reset"}


@router.get("/history", response_model=list[UpdateHistoryEntry], summary="List the updates made")
async def get_update_history(_: dict = Depends(verify_token)):
    """Get the update history: the last 50 updates, newest first; rollbacks are not in it."""
    updater = get_updater()
    return await updater.get_update_history()


@router.get("/config", response_model=UpdateConfig, summary="Read the update settings")
async def get_update_config(_: dict = Depends(verify_token)):
    """Get the update configuration, or the defaults when none is stored. Nothing acts on it yet:
    updates are checked and installed only on request."""
    updater = get_updater()
    return await updater.get_config()


@router.put(
    "/config", response_model=UpdateConfig, summary="Save the update settings",
    responses=errors(BODY, {500: "The settings could not be written."}),
)
async def update_config(
    config: UpdateConfig,
    _: dict = Depends(verify_token),
):
    """Update the update configuration, and answer it. Nothing acts on it yet."""
    updater = get_updater()
    try:
        await updater.save_config(config)
        return config
    except UpdateError as e:
        raise HTTPException(status_code=500, detail=str(e))
