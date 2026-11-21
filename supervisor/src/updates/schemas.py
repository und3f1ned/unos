"""Pydantic schemas for the updates module."""

from enum import Enum
from pydantic import BaseModel, ConfigDict, Field
from pydantic.config import JsonDict

from ..times import UtcDatetime


class UpdateStatus(str, Enum):
    """Status of the update process."""
    IDLE = "idle"
    CHECKING = "checking"
    DOWNLOADING = "downloading"
    VERIFYING = "verifying"
    BACKING_UP = "backing_up"
    INSTALLING = "installing"
    RESTARTING = "restarting"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLBACK = "rollback"


_RELEASE_EXAMPLE: JsonDict = {
    "tag_name": "v1.3.0",
    "name": "UNOS 1.3.0",
    "body": "## Changes\n- The off-device copy checks its repository after each copy.",
    "published_at": "2026-11-12T09:30:00Z",
    "html_url": "https://github.com/und3f1ned/unos/releases/tag/v1.3.0",
    "prerelease": False,
    "assets": [{
        "name": "unos-v1.3.0.tar.gz.sha256",
        "download_url": "https://github.com/und3f1ned/unos/releases/download/v1.3.0/unos-v1.3.0.tar.gz.sha256",
        "size": 91, "content_type": "text/plain",
    }],
    "checksum_sha256": "3b9f6c1e8a2d4f7b0c5e9a1d3f6b8e2c4a7d0f9b1e3c5a8d2f4b6e9c1a3d5f7b",
}
_NOTHING_RUNS = "Nothing runs on its own: it is only stored."


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
    prerelease: bool = Field(default=False, description="Whether it is a prerelease.")
    assets: list[ReleaseAsset] = Field(default=[], description="The files attached to it.")
    # SHA256 of the release archive
    checksum_sha256: str | None = Field(default=None, description="The SHA-256 from its .sha256 or checksums.txt asset, "
                                                          "which an install checks the download against; null "
                                                          "without one.")


class CurrentVersion(BaseModel):
    """Current installed version information."""
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "version": "v1.2.0-3-g712fc3a", "commit_hash": "712fc3a", "install_date": None, "is_development": True,
    }]})

    version: str = Field(description="What git describe --tags --always gave when the image was built: a release "
                                     "tag, a tag with the commits after it, or a commit; unknown without it.")
    commit_hash: str = Field(description="The commit of a version that is not a release tag; unknown "
                                         "otherwise.")
    install_date: UtcDatetime | None = Field(default=None, description="When it was installed: always null, since the "
                                                               "image does not record it.")
    is_development: bool = Field(default=False, description="Whether it is a commit after or without a release tag.")


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
    release_info: ReleaseInfo | None = Field(
        default=None, description="The latest release; null when none is published."
    )
    checked_at: UtcDatetime = Field(description="When GitHub was asked, in UTC.")


class UpdateProgress(BaseModel):
    """Progress of an ongoing update operation."""
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "status": "verifying", "progress_percent": 45, "message": "Verifying checksum...",
        "started_at": "2026-11-19T10:06:00.118221Z", "completed_at": None, "error": None, "can_rollback": True,
    }]})

    status: UpdateStatus = Field(description="What the update or the rollback does now: idle, checking, "
                                             "downloading, verifying, backing_up, installing, restarting, "
                                             "completed, failed or rollback.")
    progress_percent: int = Field(ge=0, le=100, default=0, description="How far it got, in percent.")
    message: str = Field(default="", description="What it does, or how it ended.")
    started_at: UtcDatetime | None = Field(default=None, description="When the last update started, in UTC; null when "
                                                             "none is recorded.")
    completed_at: UtcDatetime | None = Field(default=None, description="When the last update or rollback ended, in UTC; "
                                                               "null while an update runs or none is recorded.")
    error: str | None = Field(default=None, description="Why it failed; null unless it did.")
    can_rollback: bool = Field(default=False, description="Whether an update backed up the version before it, which POST "
                                                  "/updates/rollback goes back to.")


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
    status: str = Field(description="success or failed.")
    started_at: UtcDatetime = Field(description="When it started, in UTC.")
    completed_at: UtcDatetime | None = Field(default=None, description="When it ended, in UTC.")
    error: str | None = Field(default=None, description="Why it failed; null after a success.")
    changelog: str | None = Field(default=None, description="The release's changelog, after a success; null otherwise.")


class UpdateConfig(BaseModel):
    """Update configuration settings."""
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "auto_check": True, "check_interval_hours": 24, "auto_install": False, "include_prereleases": False,
        "github_repo": "und3f1ned/unos",
    }]})

    auto_check: bool = Field(default=True, description=f"Whether to check for updates on a schedule. {_NOTHING_RUNS}")
    # 1 hour to 1 week
    check_interval_hours: int = Field(ge=1, le=168, default=24, description="The hours between two checks, 1 to "
                                                                            f"168. {_NOTHING_RUNS}")
    auto_install: bool = Field(default=False, description=f"Whether to install updates on their own. {_NOTHING_RUNS}")
    include_prereleases: bool = Field(default=False, description="Whether checks take prereleases. It is only stored: a "
                                                         "check takes its own include_prereleases.")
    github_repo: str = Field(default="und3f1ned/unos", description="The GitHub repository of the releases, owner/name. It "
                                                           "is only stored: the checks ask und3f1ned/unos.")
