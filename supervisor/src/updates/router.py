"""API router for update endpoints."""

import asyncio
import logging
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from ..auth.utils import verify_token
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


@router.get("/current", response_model=CurrentVersion)
async def get_current_version(_: dict = Depends(verify_token)):
    """Get the currently installed version."""
    updater = get_updater()
    return await updater.get_current_version()


@router.get("/check", response_model=UpdateCheck)
@limiter.limit("10/minute")
async def check_for_updates(
    request: Request,
    include_prereleases: bool = False,
    _: dict = Depends(verify_token),
):
    """
    Check for available updates from GitHub releases.

    Rate limited to 10 checks per minute to prevent abuse.
    """
    updater = get_updater()
    try:
        return await updater.check_for_updates(
            include_prereleases=include_prereleases
        )
    except UpdateError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        logger.error(f"Update check failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to check for updates")


@router.get("/latest", response_model=ReleaseInfo)
@limiter.limit("10/minute")
async def get_latest_release(
    request: Request,
    include_prereleases: bool = False,
    _: dict = Depends(verify_token),
):
    """Get detailed information about the latest available release."""
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
    except Exception as e:
        logger.error(f"Failed to fetch latest release: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch release info")


@router.post("/install")
async def install_update(
    background_tasks: BackgroundTasks,
    version: str | None = None,
    _: dict = Depends(verify_token),
):
    """
    Install an update.

    If version is not specified, installs the latest stable release.
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
    except Exception as e:
        logger.error(f"Failed to start update: {e}")
        raise HTTPException(status_code=500, detail="Failed to start update")


@router.get("/progress", response_model=UpdateProgress)
async def get_update_progress(_: dict = Depends(verify_token)):
    """Get the current update progress."""
    updater = get_updater()
    return updater.get_progress()


@router.post("/rollback")
async def rollback_update(
    background_tasks: BackgroundTasks,
    _: dict = Depends(verify_token),
):
    """
    Rollback to the previous version.

    Only available after a successful update with backup.
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


@router.post("/reset")
async def reset_update_status(_: dict = Depends(verify_token)):
    """Reset the update status (useful after failed updates)."""
    updater = get_updater()
    progress = updater.get_progress()

    if progress.status in [UpdateStatus.DOWNLOADING, UpdateStatus.INSTALLING, UpdateStatus.ROLLBACK]:
        raise HTTPException(
            status_code=409,
            detail="Cannot reset while update is in progress",
        )

    updater.reset_progress()
    return {"status": "ok", "message": "Update status reset"}


@router.get("/history", response_model=list[UpdateHistoryEntry])
async def get_update_history(_: dict = Depends(verify_token)):
    """Get the update history."""
    updater = get_updater()
    return await updater.get_update_history()


@router.get("/config", response_model=UpdateConfig)
async def get_update_config(_: dict = Depends(verify_token)):
    """Get the update configuration."""
    updater = get_updater()
    return await updater.get_config()


@router.put("/config", response_model=UpdateConfig)
async def update_config(
    config: UpdateConfig,
    _: dict = Depends(verify_token),
):
    """Update the update configuration."""
    updater = get_updater()
    try:
        await updater.save_config(config)
        return config
    except UpdateError as e:
        raise HTTPException(status_code=500, detail=str(e))
