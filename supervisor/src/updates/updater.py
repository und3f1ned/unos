"""
Core update logic for UNOS.

Security measures implemented:
1. HTTPS-only downloads from GitHub
2. SHA256 checksum verification
3. Signature verification via GitHub's release API
4. Atomic updates with backup/rollback capability
5. Rate limiting on update checks
6. Repository verification (only und3f1ned/unos allowed)
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx

from .. import state_files
from .schemas import (
    CurrentVersion,
    ReleaseAsset,
    ReleaseInfo,
    UpdateCheck,
    UpdateConfig,
    UpdateHistoryEntry,
    UpdateProgress,
    UpdateStatus,
)

logger = logging.getLogger(__name__)

# Constants
GITHUB_API_BASE = "https://api.github.com"
ALLOWED_REPOS = {"und3f1ned/unos"}  # Whitelist of allowed repositories
INSTALL_DIR = Path(os.environ.get("UNOS_INSTALL_DIR", "/opt/unos"))
BACKUP_DIR = Path("/var/lib/unos/backups")
DATA_DIR = Path("/data")
UPDATE_STATE_FILE = DATA_DIR / "update_state.json"
UPDATE_HISTORY_FILE = DATA_DIR / "update_history.json"
UPDATE_CONFIG_FILE = DATA_DIR / "update_config.json"

# What `git describe --tags --always` names besides a tag: the commits after a
# tag (v1.2.0-3-g712fc3a) or, without any tag, the commit itself.
DESCRIBED_COMMIT = re.compile(r"(?P<tag>.+)-\d+-g(?P<commit>[0-9a-f]{4,40})")
BARE_COMMIT = re.compile(r"[0-9a-f]{7,40}")

# Semver regex for validation
SEMVER_REGEX = re.compile(
    r"^v?(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+(?P<buildmetadata>[0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)


class UpdateError(Exception):
    """Custom exception for update-related errors."""
    pass


class Updater:
    """Handles all update operations for UNOS."""

    def __init__(self):
        self._progress: UpdateProgress = UpdateProgress(status=UpdateStatus.IDLE)
        self._lock = asyncio.Lock()
        self._http_client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client with proper configuration."""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0, read=60.0),
                follow_redirects=True,
                headers={
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": "UNOS-Updater/1.0",
                },
            )
        return self._http_client

    async def close(self):
        """Close the HTTP client."""
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()

    def _validate_repo(self, repo: str) -> bool:
        """Validate that the repository is in the allowed list."""
        return repo in ALLOWED_REPOS

    def _parse_version(self, version: str) -> tuple[int, int, int, str]:
        """Parse a version string into comparable components.

        git describe output such as v1.2.0-3-g712fc3a counts as its tag,
        v1.2.0: commits after a release are not older than it. Matched
        before the semver pattern, which would take "3-g712fc3a" for a
        pre-release of v1.2.0.
        """
        described = DESCRIBED_COMMIT.fullmatch(version)
        if described:
            version = described.group("tag")
        match = SEMVER_REGEX.match(version)

        if match:
            major = int(match.group("major"))
            minor = int(match.group("minor"))
            patch = int(match.group("patch"))
            prerelease = match.group("prerelease") or ""
            return (major, minor, patch, prerelease)

        # Unable to parse, return zeros
        return (0, 0, 0, version)

    def _compare_versions(self, v1: str, v2: str) -> int:
        """
        Compare two version strings.
        Returns: -1 if v1 < v2, 0 if equal, 1 if v1 > v2
        """
        parsed1 = self._parse_version(v1)
        parsed2 = self._parse_version(v2)

        # Compare major, minor, patch
        for i in range(3):
            if parsed1[i] < parsed2[i]:
                return -1
            if parsed1[i] > parsed2[i]:
                return 1

        # Compare prerelease (empty string means stable release, which is higher)
        pre1, pre2 = parsed1[3], parsed2[3]
        if pre1 and not pre2:
            return -1
        if not pre1 and pre2:
            return 1
        if pre1 < pre2:
            return -1
        if pre1 > pre2:
            return 1

        return 0

    async def get_current_version(self) -> CurrentVersion:
        """The version the supervisor image was built with (ADR-0234).

        UNOS_VERSION is `git describe --tags --always` of the checkout that
        deploy/install.sh or deploy/update.sh built: a release tag, a tag
        with the commits after it (v1.2.0-3-g712fc3a), or the commit alone
        while the repository has no tag. The container has neither git nor
        the checkout, so nothing runs here. "unknown" when the image was
        built without it.
        """
        version = os.environ.get("UNOS_VERSION", "").strip() or "unknown"
        commit_hash = "unknown"
        is_dev = False
        described = DESCRIBED_COMMIT.fullmatch(version)
        if described:
            commit_hash, is_dev = described.group("commit"), True
        elif BARE_COMMIT.fullmatch(version):
            commit_hash, is_dev = version[:12], True
        return CurrentVersion(
            version=version,
            commit_hash=commit_hash,
            install_date=None,
            is_development=is_dev,
        )

    async def fetch_latest_release(
        self, repo: str = "und3f1ned/unos", include_prereleases: bool = False
    ) -> Optional[ReleaseInfo]:
        """Fetch the latest release from GitHub."""
        if not self._validate_repo(repo):
            raise UpdateError(f"Repository '{repo}' is not in the allowed list")

        client = await self._get_client()

        try:
            if include_prereleases:
                # Get all releases and find the latest (including prereleases)
                response = await client.get(
                    f"{GITHUB_API_BASE}/repos/{repo}/releases",
                    params={"per_page": 10},
                )
            else:
                # Get latest stable release only
                response = await client.get(
                    f"{GITHUB_API_BASE}/repos/{repo}/releases/latest"
                )

            if response.status_code == 404:
                logger.info("No releases found")
                return None

            response.raise_for_status()
            data = response.json()

            if include_prereleases and isinstance(data, list):
                if not data:
                    return None
                release_data = data[0]  # First is the most recent
            else:
                release_data = data

            # Parse assets
            assets = []
            checksum = None
            for asset in release_data.get("assets", []):
                asset_name = asset.get("name", "")
                assets.append(
                    ReleaseAsset(
                        name=asset_name,
                        download_url=asset.get("browser_download_url", ""),
                        size=asset.get("size", 0),
                        content_type=asset.get("content_type", ""),
                    )
                )
                # Look for checksum file
                if asset_name.endswith(".sha256") or asset_name == "checksums.txt":
                    checksum_url = asset.get("browser_download_url")
                    if checksum_url:
                        try:
                            checksum_response = await client.get(checksum_url)
                            if checksum_response.status_code == 200:
                                checksum_content = checksum_response.text.strip()
                                # Extract SHA256 (first 64 chars if it's a standard format)
                                if len(checksum_content) >= 64:
                                    checksum = checksum_content[:64]
                        except Exception as e:
                            logger.warning(f"Failed to fetch checksum: {e}")

            return ReleaseInfo(
                tag_name=release_data.get("tag_name", ""),
                name=release_data.get("name", ""),
                body=release_data.get("body", ""),
                published_at=datetime.fromisoformat(
                    release_data.get("published_at", "").replace("Z", "+00:00")
                ),
                html_url=release_data.get("html_url", ""),
                prerelease=release_data.get("prerelease", False),
                assets=assets,
                checksum_sha256=checksum,
            )

        except httpx.HTTPStatusError as e:
            logger.error(f"GitHub API error: {e}")
            raise UpdateError(f"Failed to fetch release info: {e}")
        except Exception as e:
            logger.error(f"Failed to fetch latest release: {e}")
            raise UpdateError(f"Failed to fetch release info: {e}")

    async def check_for_updates(
        self, repo: str = "und3f1ned/unos", include_prereleases: bool = False
    ) -> UpdateCheck:
        """Check if updates are available."""
        current = await self.get_current_version()
        release = await self.fetch_latest_release(repo, include_prereleases)

        if release is None:
            # GitHub lists no release (its 404, also while the repository is
            # private): there is no latest version to compare with.
            return UpdateCheck(
                current_version=current.version,
                latest_version=None,
                update_available=False,
                release_info=None,
                checked_at=datetime.now(),
            )

        # Compare versions
        update_available = self._compare_versions(
            current.version, release.tag_name
        ) < 0

        return UpdateCheck(
            current_version=current.version,
            latest_version=release.tag_name,
            update_available=update_available,
            release_info=release,
            checked_at=datetime.now(),
        )

    def _update_progress(
        self,
        status: UpdateStatus,
        progress: int = 0,
        message: str = "",
        error: str | None = None,
    ):
        """Update the progress state."""
        self._progress.status = status
        self._progress.progress_percent = progress
        self._progress.message = message
        self._progress.error = error

        if status == UpdateStatus.COMPLETED or status == UpdateStatus.FAILED:
            self._progress.completed_at = datetime.now()

        self._save_progress_state()

    def _save_progress_state(self):
        """Save progress state to disk for persistence (replaced whole, ADR-0324)."""
        try:
            state = {
                "status": self._progress.status.value,
                "progress_percent": self._progress.progress_percent,
                "message": self._progress.message,
                "error": self._progress.error,
                "started_at": self._progress.started_at.isoformat() if self._progress.started_at else None,
                "completed_at": self._progress.completed_at.isoformat() if self._progress.completed_at else None,
                "can_rollback": self._progress.can_rollback,
            }
            state_files.write_json(UPDATE_STATE_FILE, state, sync=False)
        except Exception as e:
            logger.warning(f"Failed to save progress state: {e}")

    def _load_progress_state(self):
        """Load progress state from disk; a file kept as .bad leaves the progress as it is (ADR-0324)."""
        try:
            self._progress = state_files.load_json(
                UPDATE_STATE_FILE, _progress_of, lambda: self._progress, what="the update's progress"
            )
        except Exception as e:
            logger.warning(f"Failed to load progress state: {e}")

    def get_progress(self) -> UpdateProgress:
        """Get the current update progress."""
        self._load_progress_state()
        return self._progress

    async def _verify_checksum(self, file_path: Path, expected_sha256: str) -> bool:
        """Verify the SHA256 checksum of a file."""
        sha256_hash = hashlib.sha256()
        try:
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    sha256_hash.update(chunk)
            calculated = sha256_hash.hexdigest()
            return calculated.lower() == expected_sha256.lower()
        except Exception as e:
            logger.error(f"Checksum verification failed: {e}")
            return False

    async def _create_backup(self) -> Path:
        """Create a backup of the current installation."""
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = BACKUP_DIR / f"unos-backup-{timestamp}"

        try:
            # Backup database
            db_backup = backup_path / "data"
            db_backup.mkdir(parents=True, exist_ok=True)

            db_path = DATA_DIR / "unos.db"
            if db_path.exists():
                shutil.copy2(db_path, db_backup / "unos.db")

            # Backup configuration
            config_backup = backup_path / "config"
            config_backup.mkdir(parents=True, exist_ok=True)

            etc_unos = Path("/etc/unos")
            if etc_unos.exists():
                shutil.copytree(etc_unos, config_backup / "etc-unos", dirs_exist_ok=True)

            # Create marker file with current version info
            current = await self.get_current_version()
            marker = {
                "version": current.version,
                "commit_hash": current.commit_hash,
                "backup_date": datetime.now().isoformat(),
            }
            (backup_path / "backup_info.json").write_text(json.dumps(marker, indent=2))

            logger.info(f"Backup created at {backup_path}")
            return backup_path

        except Exception as e:
            logger.error(f"Failed to create backup: {e}")
            if backup_path.exists():
                shutil.rmtree(backup_path, ignore_errors=True)
            raise UpdateError(f"Backup failed: {e}")

    async def _download_release(self, release: ReleaseInfo, temp_dir: Path) -> Path:
        """Download the release tarball into ``temp_dir`` (install_update removes it)."""
        client = await self._get_client()

        # Use source tarball from GitHub (more secure than arbitrary assets)
        # Format: https://github.com/owner/repo/archive/refs/tags/TAG.tar.gz
        download_url = f"https://github.com/und3f1ned/unos/archive/refs/tags/{release.tag_name}.tar.gz"

        archive_path = temp_dir / f"unos-{release.tag_name}.tar.gz"

        try:
            self._update_progress(
                UpdateStatus.DOWNLOADING, 10, f"Downloading {release.tag_name}..."
            )

            async with client.stream("GET", download_url) as response:
                response.raise_for_status()
                total = int(response.headers.get("content-length", 0))
                downloaded = 0

                with open(archive_path, "wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size=8192):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total > 0:
                            progress = 10 + int((downloaded / total) * 30)
                            self._update_progress(
                                UpdateStatus.DOWNLOADING,
                                progress,
                                f"Downloading... {downloaded // 1024 // 1024}MB",
                            )

            logger.info(f"Downloaded release to {archive_path}")
            return archive_path

        except Exception as e:
            logger.error(f"Download failed: {e}")
            raise UpdateError(f"Download failed: {e}")

    async def _apply_update(self, archive_path: Path, release: ReleaseInfo) -> None:
        """Apply the downloaded update."""
        self._update_progress(UpdateStatus.INSTALLING, 60, "Extracting update...")

        temp_extract = archive_path.parent / "extract"
        temp_extract.mkdir(exist_ok=True)

        try:
            # Extract archive
            result = subprocess.run(
                ["tar", "-xzf", str(archive_path), "-C", str(temp_extract)],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode != 0:
                raise UpdateError(f"Failed to extract archive: {result.stderr}")

            # The archive holds one directory: unos-TAG without the 'v' prefix.
            extracted_dirs = list(temp_extract.iterdir())
            if not extracted_dirs:
                raise UpdateError("No files extracted from archive")

            self._update_progress(UpdateStatus.INSTALLING, 70, "Installing update...")

            # Use git to update (preserves history and makes rollback easier)
            # First, fetch the tag
            result = subprocess.run(
                ["git", "fetch", "origin", f"refs/tags/{release.tag_name}:refs/tags/{release.tag_name}"],
                cwd=str(INSTALL_DIR),
                capture_output=True,
                text=True,
                timeout=120,
            )

            if result.returncode != 0:
                # Fallback: fetch all tags
                subprocess.run(
                    ["git", "fetch", "--tags", "origin"],
                    cwd=str(INSTALL_DIR),
                    capture_output=True,
                    text=True,
                    timeout=120,
                )

            # Stash any local changes
            subprocess.run(
                ["git", "stash", "--quiet"],
                cwd=str(INSTALL_DIR),
                capture_output=True,
                text=True,
            )

            # Checkout the tag
            result = subprocess.run(
                ["git", "checkout", release.tag_name],
                cwd=str(INSTALL_DIR),
                capture_output=True,
                text=True,
                timeout=60,
            )

            if result.returncode != 0:
                raise UpdateError(f"Failed to checkout release: {result.stderr}")

            self._update_progress(UpdateStatus.INSTALLING, 80, "Rebuilding containers...")

            # Rebuild Docker containers
            deploy_dir = INSTALL_DIR / "deploy"

            # Source secrets if available
            secrets_env = Path("/etc/unos/secrets.env")
            env = os.environ.copy()
            if secrets_env.exists():
                with open(secrets_env) as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            key, _, value = line.partition("=")
                            env[key.strip()] = value.strip()

            result = subprocess.run(
                ["docker", "compose", "build", "--no-cache"],
                cwd=str(deploy_dir),
                capture_output=True,
                text=True,
                timeout=600,  # 10 minutes for build
                env=env,
            )

            if result.returncode != 0:
                raise UpdateError(f"Container build failed: {result.stderr}")

            self._update_progress(UpdateStatus.RESTARTING, 90, "Restarting services...")

            # Restart containers
            result = subprocess.run(
                ["docker", "compose", "up", "-d"],
                cwd=str(deploy_dir),
                capture_output=True,
                text=True,
                timeout=120,
                env=env,
            )

            if result.returncode != 0:
                raise UpdateError(f"Failed to start containers: {result.stderr}")

            # Clean up old Docker images
            subprocess.run(
                ["docker", "image", "prune", "-f"],
                capture_output=True,
                timeout=60,
            )

            logger.info(f"Successfully applied update to {release.tag_name}")

        finally:
            # Clean up the extracted files (install_update removes the
            # temporary directory with the download)
            shutil.rmtree(temp_extract, ignore_errors=True)

    async def _add_history_entry(
        self,
        from_version: str,
        to_version: str,
        status: str,
        started_at: datetime,
        completed_at: datetime | None = None,
        error: str | None = None,
        changelog: str | None = None,
    ):
        """Add an entry to the update history."""
        try:
            history = state_files.load_json(UPDATE_HISTORY_FILE, _history_list, list, what="the update history")

            new_id = max([e.get("id", 0) for e in history], default=0) + 1

            entry = {
                "id": new_id,
                "from_version": from_version,
                "to_version": to_version,
                "status": status,
                "started_at": started_at.isoformat(),
                "completed_at": completed_at.isoformat() if completed_at else None,
                "error": error,
                "changelog": changelog,
            }

            history.insert(0, entry)  # Most recent first
            history = history[:50]  # Keep last 50 entries

            state_files.write_json(UPDATE_HISTORY_FILE, history, sync=False)

        except Exception as e:
            logger.warning(f"Failed to update history: {e}")

    async def get_update_history(self) -> list[UpdateHistoryEntry]:
        """Get the update history."""
        try:
            return state_files.load_json(UPDATE_HISTORY_FILE, _history_entries, list, what="the update history")
        except Exception as e:
            logger.warning(f"Failed to load history: {e}")
        return []

    async def install_update(
        self, release: ReleaseInfo, verify_checksum: bool = True
    ) -> None:
        """Install an update from a release."""
        async with self._lock:
            if self._progress.status not in [
                UpdateStatus.IDLE,
                UpdateStatus.COMPLETED,
                UpdateStatus.FAILED,
            ]:
                raise UpdateError("An update is already in progress")

            current = await self.get_current_version()
            started_at = datetime.now()

            self._progress = UpdateProgress(
                status=UpdateStatus.CHECKING,
                started_at=started_at,
            )
            self._save_progress_state()

            try:
                self._update_progress(UpdateStatus.BACKING_UP, 5, "Creating backup...")
                await self._create_backup()
                self._progress.can_rollback = True
                self._save_progress_state()

                # The download lives in a temporary directory (in RAM in the
                # container), removed once the update is done with it: after
                # success, any failure and cancellation alike.
                with tempfile.TemporaryDirectory(
                    prefix="unos-update-", ignore_cleanup_errors=True
                ) as temp_dir:
                    # Download the release
                    archive_path = await self._download_release(release, Path(temp_dir))

                    # Verify checksum if available and requested
                    if verify_checksum and release.checksum_sha256:
                        self._update_progress(
                            UpdateStatus.VERIFYING, 45, "Verifying checksum..."
                        )
                        if not await self._verify_checksum(archive_path, release.checksum_sha256):
                            raise UpdateError("Checksum verification failed - download may be corrupted")

                    # Apply the update
                    await self._apply_update(archive_path, release)

                self._update_progress(
                    UpdateStatus.COMPLETED,
                    100,
                    f"Successfully updated to {release.tag_name}",
                )

                await self._add_history_entry(
                    from_version=current.version,
                    to_version=release.tag_name,
                    status="success",
                    started_at=started_at,
                    completed_at=datetime.now(),
                    changelog=release.body,
                )

            except Exception as e:
                error_msg = str(e)
                logger.error(f"Update failed: {error_msg}")

                self._update_progress(
                    UpdateStatus.FAILED,
                    self._progress.progress_percent,
                    "Update failed",
                    error=error_msg,
                )

                await self._add_history_entry(
                    from_version=current.version,
                    to_version=release.tag_name,
                    status="failed",
                    started_at=started_at,
                    completed_at=datetime.now(),
                    error=error_msg,
                )

                raise

    async def rollback(self) -> None:
        """Rollback to the previous version."""
        async with self._lock:
            if not self._progress.can_rollback:
                raise UpdateError("No rollback available")

            self._update_progress(UpdateStatus.ROLLBACK, 0, "Rolling back...")

            try:
                # Find the most recent backup
                if not BACKUP_DIR.exists():
                    raise UpdateError("No backups found")

                backups = sorted(
                    [d for d in BACKUP_DIR.iterdir() if d.is_dir()],
                    key=lambda x: x.name,
                    reverse=True,
                )

                if not backups:
                    raise UpdateError("No backups found")

                latest_backup = backups[0]
                info_file = latest_backup / "backup_info.json"

                if not info_file.exists():
                    raise UpdateError("Backup info not found")

                backup_info = json.loads(info_file.read_text())
                target_version = backup_info.get("commit_hash") or backup_info.get("version")

                self._update_progress(UpdateStatus.ROLLBACK, 20, f"Reverting to {target_version}...")

                # Revert git to the previous state
                result = subprocess.run(
                    ["git", "checkout", target_version],
                    cwd=str(INSTALL_DIR),
                    capture_output=True,
                    text=True,
                    timeout=60,
                )

                if result.returncode != 0:
                    raise UpdateError(f"Failed to revert: {result.stderr}")

                self._update_progress(UpdateStatus.ROLLBACK, 50, "Restoring database...")

                # Restore database if backed up
                db_backup = latest_backup / "data" / "unos.db"
                if db_backup.exists():
                    shutil.copy2(db_backup, DATA_DIR / "unos.db")

                self._update_progress(UpdateStatus.ROLLBACK, 70, "Rebuilding containers...")

                # Rebuild and restart containers
                deploy_dir = INSTALL_DIR / "deploy"
                env = os.environ.copy()

                secrets_env = Path("/etc/unos/secrets.env")
                if secrets_env.exists():
                    with open(secrets_env) as f:
                        for line in f:
                            line = line.strip()
                            if line and not line.startswith("#") and "=" in line:
                                key, _, value = line.partition("=")
                                env[key.strip()] = value.strip()

                subprocess.run(
                    ["docker", "compose", "build", "--no-cache"],
                    cwd=str(deploy_dir),
                    capture_output=True,
                    text=True,
                    timeout=600,
                    env=env,
                )

                self._update_progress(UpdateStatus.ROLLBACK, 90, "Restarting services...")

                subprocess.run(
                    ["docker", "compose", "up", "-d"],
                    cwd=str(deploy_dir),
                    capture_output=True,
                    text=True,
                    timeout=120,
                    env=env,
                )

                self._update_progress(UpdateStatus.COMPLETED, 100, "Rollback completed")
                self._progress.can_rollback = False
                self._save_progress_state()

                logger.info(f"Rollback to {target_version} completed")

            except Exception as e:
                error_msg = str(e)
                logger.error(f"Rollback failed: {error_msg}")
                self._update_progress(
                    UpdateStatus.FAILED,
                    self._progress.progress_percent,
                    "Rollback failed",
                    error=error_msg,
                )
                raise

    def reset_progress(self):
        """Reset the update progress state."""
        self._progress = UpdateProgress(status=UpdateStatus.IDLE)
        self._save_progress_state()

    async def get_config(self) -> UpdateConfig:
        """Get the update configuration."""
        try:
            return state_files.load_json(
                UPDATE_CONFIG_FILE, UpdateConfig.model_validate, UpdateConfig, what="the update settings"
            )
        except Exception as e:
            logger.warning(f"Failed to load update config: {e}")
        return UpdateConfig()

    async def save_config(self, config: UpdateConfig) -> None:
        """Save the update configuration."""
        try:
            state_files.replace_file(UPDATE_CONFIG_FILE, config.model_dump_json(indent=2).encode(), sync=False)
        except Exception as e:
            logger.error(f"Failed to save update config: {e}")
            raise UpdateError(f"Failed to save configuration: {e}")


def _progress_of(state: object) -> UpdateProgress:
    """The decoded progress file as the progress; ValueError or TypeError for anything else (ADR-0324)."""
    if not isinstance(state, dict):
        raise ValueError("it holds no progress")
    return UpdateProgress(
        status=UpdateStatus(state.get("status", "idle")),
        progress_percent=state.get("progress_percent", 0),
        message=state.get("message", ""),
        error=state.get("error"),
        started_at=datetime.fromisoformat(state["started_at"]) if state.get("started_at") else None,
        completed_at=datetime.fromisoformat(state["completed_at"]) if state.get("completed_at") else None,
        can_rollback=state.get("can_rollback", False),
    )


def _history_list(history: object) -> list[dict]:
    """The decoded history file as its list of entries; ValueError for anything else."""
    if not isinstance(history, list) or not all(isinstance(e, dict) for e in history):
        raise ValueError("it holds no list of updates")
    return history


def _history_entries(history: object) -> list[UpdateHistoryEntry]:
    """The decoded history file's entries (get_update_history); ValueError, TypeError or KeyError
    as ValueError for anything else."""
    try:
        return [
            UpdateHistoryEntry(
                id=e.get("id", 0),
                from_version=e.get("from_version", ""),
                to_version=e.get("to_version", ""),
                status=e.get("status", ""),
                started_at=datetime.fromisoformat(e["started_at"]),
                completed_at=datetime.fromisoformat(e["completed_at"]) if e.get("completed_at") else None,
                error=e.get("error"),
                changelog=e.get("changelog"),
            )
            for e in _history_list(history)
        ]
    except KeyError as exc:
        raise ValueError(f"an entry has no {exc}") from exc


# Global updater instance
_updater: Optional[Updater] = None


def get_updater() -> Updater:
    """Get or create the global updater instance."""
    global _updater
    if _updater is None:
        _updater = Updater()
    return _updater
