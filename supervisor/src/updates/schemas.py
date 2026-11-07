"""Pydantic schemas for the updates module."""

from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field


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


class ReleaseAsset(BaseModel):
    """A downloadable asset from a GitHub release."""
    name: str
    download_url: str
    size: int
    content_type: str


class ReleaseInfo(BaseModel):
    """Information about a GitHub release."""
    tag_name: str
    name: str
    body: str  # Changelog/description in markdown
    published_at: datetime
    html_url: str
    prerelease: bool = False
    assets: list[ReleaseAsset] = []
    checksum_sha256: str | None = None  # SHA256 of the release archive


class CurrentVersion(BaseModel):
    """Current installed version information."""
    version: str
    commit_hash: str
    install_date: datetime | None = None
    is_development: bool = False


class UpdateCheck(BaseModel):
    """Result of checking for updates."""
    current_version: str
    # None when no release is published.
    latest_version: str | None
    update_available: bool
    release_info: ReleaseInfo | None = None
    checked_at: datetime


class UpdateProgress(BaseModel):
    """Progress of an ongoing update operation."""
    status: UpdateStatus
    progress_percent: int = Field(ge=0, le=100, default=0)
    message: str = ""
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None
    can_rollback: bool = False


class UpdateHistoryEntry(BaseModel):
    """A single entry in the update history."""
    id: int
    from_version: str
    to_version: str
    status: str
    started_at: datetime
    completed_at: datetime | None = None
    error: str | None = None
    changelog: str | None = None


class UpdateConfig(BaseModel):
    """Update configuration settings."""
    auto_check: bool = True
    check_interval_hours: int = Field(ge=1, le=168, default=24)  # 1 hour to 1 week
    auto_install: bool = False
    include_prereleases: bool = False
    github_repo: str = "und3f1ned/unos"
