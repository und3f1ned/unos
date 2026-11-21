"""Pydantic schemas for the updates module."""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..times import UtcDatetime


class UpdateStatus(str, Enum):
    """Status of the update process."""
    IDLE = "idle"
    CHECKING = "checking"
    DOWNLOADING = "downloading"
    VERIFYING = "verifying"
    BACKING_UP = "backing_up"
    INSTALLING = "installing"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLBACK = "rollback"


_RELEASE_EXAMPLE = {
    "tag_name": "v1.3.0",
    "name": "UNOS 1.3.0",
    "body": "## Changes\n- The off-device copy checks its repository after each copy.",
    "published_at": "2026-11-12T09:30:00Z",
    "html_url": "https://github.com/und3f1ned/unos/releases/tag/v1.3.0",
    "prerelease": False,
    "assets": [],
}


class ReleaseAsset(BaseModel):
    """A downloadable asset from a GitHub release."""
    name: str = Field(description="The asset's file name.")
    download_url: str = Field(description="Where GitHub serves it.")
    size: int = Field(description="Its size in bytes.")
    content_type: str = Field(description="Its media type, as GitHub gives it.")


class ReleaseInfo(BaseModel):
    """Information about a GitHub release."""
    model_config = ConfigDict(json_schema_extra={"examples": [_RELEASE_EXAMPLE]})

    tag_name: str = Field(description="The release's tag, its version, such as v1.3.0.")
    name: str = Field(description="The release's title.")
    # Changelog/description in markdown
    body: str = Field(description="Its changelog, in Markdown.")
    published_at: UtcDatetime = Field(description="When it was published, in UTC.")
    html_url: str = Field(description="Its page on GitHub.")
    prerelease: bool = Field(False, description="Whether it is a prerelease.")
    assets: list[ReleaseAsset] = Field([], description="The files attached to it.")


class CurrentVersion(BaseModel):
    """Current installed version information."""
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "version": "v1.2.0-3-g712fc3a", "commit_hash": "712fc3a", "install_date": None, "is_development": True,
    }]})

    version: str = Field(description="What git describe --tags --always gave when the image was built: a release "
                                     "tag, a tag with the commits after it, or a commit; unknown without it.")
    commit_hash: str = Field(description="The commit of a version that is not a release tag; unknown "
                                         "otherwise.")
    install_date: UtcDatetime | None = Field(None, description="When it was installed: always null, since the "
                                                               "image does not record it.")
    is_development: bool = Field(False, description="Whether it is a commit after or without a release tag.")


class UpdateCheck(BaseModel):
    """Result of checking for updates."""
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "current_version": "v1.2.0", "latest_version": "v1.3.0", "update_available": True,
        "release_info": _RELEASE_EXAMPLE, "checked_at": "2026-11-19T10:05:12.204512Z",
    }]})

    current_version: str = Field(description="The installed version, as GET /updates/current gives it.")
    # None when no release is published.
    latest_version: str | None = Field(description="The tag of the latest release; null when none is published.")
    update_available: bool = Field(description="Whether the latest release is a newer version than the installed "
                                               "one.")
    release_info: ReleaseInfo | None = Field(None, description="The latest release; null when none is published.")
    checked_at: UtcDatetime = Field(description="When GitHub was asked, in UTC.")


class UpdateProgress(BaseModel):
    """The update or rollback that runs, or the last one, and the updater's state (ADR-0443)."""
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "status": "verifying", "progress_percent": 80, "message": "Waiting for v1.3.0 to answer",
        "started_at": "2026-11-19T10:06:00.118221Z", "completed_at": None, "error": None, "can_rollback": True,
        "action": "update", "version": "v1.3.0", "rolled_back": False, "install_unavailable": None,
        "next_automatic_check": None,
    }]})

    status: UpdateStatus = Field(description="What the update or the rollback does now: idle (none ran), "
                                             "checking, downloading (the release's images), backing_up (the "
                                             "database), installing (the switch to the release), verifying "
                                             "(its health check), rollback, completed or failed.")
    progress_percent: int = Field(ge=0, le=100, default=0, description="How far it got, in percent.")
    message: str = Field("", description="What it does, or how it ended, in English.")
    started_at: UtcDatetime | None = Field(None, description="When the last update or rollback started, in UTC; "
                                                             "null when none is recorded.")
    completed_at: UtcDatetime | None = Field(None, description="When the last update or rollback ended, in UTC; "
                                                               "null while one runs or none is recorded.")
    error: str | None = Field(None, description="Why it failed; null unless it did.")
    can_rollback: bool = Field(False, description="Whether an update switched to a release after a backup of the "
                                                  "version before, which POST /updates/rollback goes back to.")
    action: Literal["update", "rollback"] | None = Field(None, description="Whether the last job is an update "
                                                                           "or a rollback; null when none ran.")
    version: str | None = Field(None, description="The release that the update installs, or the version that "
                                                  "the rollback returns to.")
    rolled_back: bool = Field(False, description="Whether a failed update was rolled back to the version before "
                                                 "it, with the database of its backup.")
    install_unavailable: str | None = Field(None, description="Why Install Update, Rollback and the automatic "
                                                              "install cannot run on this NAS; null when they "
                                                              "can. Not stored.")
    next_automatic_check: UtcDatetime | None = Field(None, description="When the automatic install looks for a "
                                                                       "new release next, in UTC; null while it "
                                                                       "is off.")


class UpdateHistoryEntry(BaseModel):
    """A single entry in the update history."""
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "id": 4, "from_version": "v1.2.0", "to_version": "v1.3.0", "status": "success",
        "started_at": "2026-11-19T10:06:00.118221Z", "completed_at": "2026-11-19T10:14:37.902114Z", "error": None,
        "changelog": "## Changes\n- The off-device copy checks its repository after each copy.",
    }]})

    id: int = Field(description="Its number; the newest has the highest.")
    from_version: str = Field(description="The version before the update.")
    to_version: str = Field(description="The release it installed.")
    status: str = Field(description="success, or failed (after a rollback when error says so).")
    started_at: UtcDatetime = Field(description="When it started, in UTC.")
    completed_at: UtcDatetime | None = Field(None, description="When it ended, in UTC.")
    error: str | None = Field(None, description="Why it failed; null after a success.")
    changelog: str | None = Field(None, description="The release's changelog, after a success; null otherwise.")


class UpdateConfig(BaseModel):
    """The automatic install's settings (ADR-0443)."""
    model_config = ConfigDict(json_schema_extra={"examples": [{"auto_install": True, "check_interval_hours": 24}]})

    auto_install: bool = Field(False, description="Whether UNOS looks for a newer stable release at the interval "
                                                  "and installs it, as Install Update does.")
    # 1 hour to 1 week
    check_interval_hours: int = Field(ge=1, le=168, default=24, description="The hours between two looks, 1 to "
                                                                            "168.")
