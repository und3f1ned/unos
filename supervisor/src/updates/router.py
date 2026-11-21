"""API router for update endpoints."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
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
)
from .updater import UpdateConflict, UpdateError, get_updater

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
_REFUSED = ("An update or a rollback runs, a restore of a backup runs, or this NAS cannot install releases: "
            "install_unavailable of GET /updates/progress says why (ADR-0439).")


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
                 "status started, a message and the release's tag; GET /updates/progress and the events "
                 "socket show how it goes."),
        **errors({
            400: "The installed version is that release or newer, or unknown, or GitHub could not be asked.",
            404: "GitHub lists no release, or version is not the tag of the latest stable release.",
            409: _REFUSED,
            500: "The update could not be started.",
        }),
    },
)
async def install_update(
    version: Annotated[str | None, Query(
        description="The tag of the latest stable release, such as v1.3.0; none for that release."
    )] = None,
    _: dict = Depends(verify_token),
):
    """
    Install an update in the background: the release's images, a backup of the database, the switch
    to the release and its health check; a switch that fails or a release that does not answer in
    time is rolled back (ADR-0439).

    If version is not specified, installs the latest stable release; a version that is not its tag
    answers 404.
    """
    updater = get_updater()
    refusal = updater._refusal()
    if refusal:
        raise HTTPException(status_code=409, detail=refusal)

    try:
        release = await updater.fetch_latest_release()
        if release is None:
            raise HTTPException(status_code=404, detail="No releases available")
        if version and release.tag_name != version:
            raise HTTPException(status_code=404, detail=f"Version {version} not found")
        # 400 for a version that runs already or is newer (a build after the release's tag), and for
        # an unknown one, which an update could not roll back to.
        updater.start_install(release)
    except HTTPException:
        raise
    except UpdateConflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    except UpdateError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.exception("Failed to start the update")
        raise HTTPException(status_code=500, detail="Failed to start update")
    return {
        "status": "started",
        "message": f"Installing update to {release.tag_name}",
        "version": release.tag_name,
    }


@router.get("/progress", response_model=UpdateProgress, summary="Read the update's progress")
async def get_update_progress(_: dict = Depends(verify_token)):
    """The update or rollback that runs, or the last one; whether one can roll back; why none can run
    on this NAS; and the automatic install's next look."""
    updater = get_updater()
    return updater.get_progress()


@router.post(
    "/rollback", summary="Roll back the last update",
    responses={
        **answer({"status": "started", "message": "Rolling back to previous version"},
                 "status started and a message; GET /updates/progress and the events socket show how it goes."),
        **errors({
            400: "No backup of the version before the one that runs: can_rollback of GET /updates/progress is false.",
            409: _REFUSED,
        }),
    },
)
async def rollback_update(_: dict = Depends(verify_token)):
    """
    Roll back to the version before the last update, in the background: its database from the
    backup the update made, then its containers, and its health check.

    Only available once an update has switched to a release after its backup (can_rollback), also
    when that update failed later and its own rollback failed too.
    """
    updater = get_updater()
    try:
        updater.start_rollback()
    except UpdateConflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    except UpdateError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "started", "message": "Rolling back to previous version"}


@router.get("/history", response_model=list[UpdateHistoryEntry], summary="List the updates made")
async def get_update_history(_: dict = Depends(verify_token)):
    """Get the update history: the last 50 updates, newest first; rollbacks by hand are not in it, a
    failed update's own rollback is in its error."""
    updater = get_updater()
    return await updater.get_update_history()


@router.get("/config", response_model=UpdateConfig, summary="Read the update settings")
async def get_update_config(_: dict = Depends(verify_token)):
    """The automatic install's settings, or the defaults when none are stored."""
    updater = get_updater()
    return await updater.get_config()


@router.put(
    "/config", response_model=UpdateConfig, summary="Save the update settings",
    responses=errors(BODY, {
        409: "auto_install is true where this NAS cannot install releases (install_unavailable).",
        500: "The settings could not be written.",
    }),
)
async def update_config(
    config: UpdateConfig,
    _: dict = Depends(verify_token),
):
    """Store the automatic install's settings, and answer them. The next look is one interval after
    the change (next_automatic_check of GET /updates/progress)."""
    updater = get_updater()
    try:
        await updater.save_config(config)
        return config
    except UpdateConflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    except UpdateError as e:
        raise HTTPException(status_code=500, detail=str(e))
