"""System > Updates: the installed version, the latest release, and Install Update, Rollback and the
automatic install (ADR-0234, ADR-0439).

An update gets the release's images, backs up the database with SQLite's backup API
(``copy_database``, ADR-0306) into a folder of its own in BACKUP_DIR, switches to the release and
waits for it to answer; when the switch fails or the release does not answer in time, it puts the
copy back through the open database with the restore's backup API and starts the version before
again. The images and the switches are the release installer's (installer.py), which is not
decided yet: until then Install Update, Rollback and the automatic install answer its reason.
"""

import asyncio
import importlib
import json
import logging
import os
import re
import shutil
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from .. import paths, state_files
from ..backup.router import (
    BackupValidationError, _replace_database, _verify_database, _verify_page_size, copy_database, restore_running,
)
from ..shares import service as shares_service
from ..config import get_settings
from ..times import utc_now
from . import installer
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
# The copies of the database made before an update (UNOS_UPDATE_BACKUP_DIR): the host's
# /var/lib/unos/backups, which deploy/docker-compose.yml mounts at the same path, beside update.sh's.
BACKUP_DIR = Path(get_settings().update_backup_dir)
# The database the supervisor opens (UNOS_DB_PATH, in the data directory by default).
DB_FILE = Path(get_settings().db_path)
UPDATE_STATE_FILE = paths.UPDATE_STATE
UPDATE_HISTORY_FILE = paths.UPDATE_HISTORY
UPDATE_CONFIG_FILE = paths.UPDATE_CONFIG
# The in-app backups kept: the newest, of the version before the installed one, and two before it.
KEPT_BACKUPS = 3
BACKUP_PREFIX = "in-app-"
# How long a release has to answer after the switch, and how often it is asked: update.sh's
# UNOS_HEALTH_TIMEOUT default and its pause between two checks (ADR-0049).
HEALTH_TIMEOUT_SECONDS = 300.0
HEALTH_INTERVAL_SECONDS = 3.0
# The longest the installer may take to get a release's images, and to switch the containers: the
# supervisor may take 680 s to report healthy (deploy/docker-compose.yml), and a switch waits for it.
FETCH_TIMEOUT_SECONDS = 3600.0
SWITCH_TIMEOUT_SECONDS = 700.0
# How often the automatic install's loop looks whether a check is due.
AUTOMATIC_TICK_SECONDS = 60.0
RUNNING = (UpdateStatus.CHECKING, UpdateStatus.DOWNLOADING, UpdateStatus.VERIFYING, UpdateStatus.BACKING_UP,
           UpdateStatus.INSTALLING, UpdateStatus.ROLLBACK)
RESTORE_RUNS = "A restore of a backup runs: update when it has ended"

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


def installed_version() -> str:
    """UNOS_VERSION, the version the supervisor image was built with; "unknown" without it."""
    return os.environ.get("UNOS_VERSION", "").strip() or "unknown"


async def _within(step, seconds: float, what: str):
    """Await a step of the installer, as an UpdateError when it takes longer than ``seconds``."""
    try:
        return await asyncio.wait_for(step, seconds)
    except TimeoutError:
        raise UpdateError(f"{what} took longer than {seconds:g} seconds") from None


class UpdateConflict(UpdateError):
    """No update or rollback can start now: one runs, a restore runs, or no installer can (409)."""


class Updater:
    """Handles all update operations for UNOS."""

    def __init__(self):
        self._progress: UpdateProgress = UpdateProgress(status=UpdateStatus.IDLE)
        self._lock = asyncio.Lock()
        self._http_client: httpx.AsyncClient | None = None
        # Changes with every step of an update or a rollback that this process runs: the events
        # socket sends administrators the progress when it changed (websocket/events.py).
        self.version = 0
        # The job that runs: install_update or rollback, started by start_install or start_rollback.
        self._job: asyncio.Task | None = None

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
        version = installed_version()
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
    ) -> ReleaseInfo | None:
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

            assets = [
                ReleaseAsset(
                    name=asset.get("name", ""),
                    download_url=asset.get("browser_download_url", ""),
                    size=asset.get("size", 0),
                    content_type=asset.get("content_type", ""),
                )
                for asset in release_data.get("assets", [])
            ]

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
                checked_at=utc_now(),
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
            checked_at=utc_now(),
        )

    def _update_progress(
        self,
        status: UpdateStatus,
        progress: int = 0,
        message: str = "",
        error: str | None = None,
    ):
        """Record a step of the job: on its file once a step (ADR-0173), and for the events socket."""
        self._progress.status = status
        self._progress.progress_percent = progress
        self._progress.message = message
        self._progress.error = error

        if status == UpdateStatus.COMPLETED or status == UpdateStatus.FAILED:
            self._progress.completed_at = utc_now()

        self._save_progress_state()
        self.version += 1

    def _save_progress_state(self):
        """Save progress state to disk for persistence (replaced whole, ADR-0324)."""
        try:
            state = self._progress.model_dump(mode="json", exclude={"install_unavailable"})
            state_files.write_json(UPDATE_STATE_FILE, state, sync=False)
        except Exception as e:
            logger.warning(f"Failed to save progress state: {e}")

    def _load_progress_state(self):
        """Load progress state from disk; a file kept as .bad leaves the progress as it is (ADR-0324).
        While a job of this process runs, its progress is the one in memory: a write of the file that
        failed must not take the job's state back."""
        if self._job is not None and not self._job.done():
            return
        try:
            self._progress = state_files.load_json(
                UPDATE_STATE_FILE, _progress_of, lambda: self._progress, what="the update's progress"
            )
        except Exception as e:
            logger.warning(f"Failed to load progress state: {e}")

    def get_progress(self) -> UpdateProgress:
        """The update or rollback that runs, or the last one, with why none can run here."""
        self._load_progress_state()
        return self.current_progress()

    def current_progress(self) -> UpdateProgress:
        """get_progress without reading its file: what this process knows. can_rollback only while
        the newest backup belongs to the version that runs (update.sh may have installed another)."""
        update: dict = {"install_unavailable": installer.current.unavailable_reason()}
        if self._progress.can_rollback and not self.running():
            try:
                self._rollback_backup()
            except UpdateError:
                update["can_rollback"] = False
        return self._progress.model_copy(update=update)

    def recover_after_start(self) -> None:
        """At the start: an update or rollback that a stop of the supervisor interrupted has ended,
        failed (ADR-0173: it stayed "downloading" for good, and Install, Reset and Rollback answered
        409). Its backup and can_rollback stay."""
        self._load_progress_state()
        if self._progress.status not in RUNNING:
            return
        noun = "rollback" if self._progress.action == "rollback" else "update"
        self._progress.status = UpdateStatus.FAILED
        self._progress.error = f"The supervisor stopped while the {noun} ran"
        self._progress.message = f"{noun.capitalize()} interrupted"
        self._progress.completed_at = utc_now()
        self._save_progress_state()
        logger.warning("The %s to %s was interrupted by a stop of the supervisor", noun, self._progress.version)

    def running(self) -> bool:
        return self._progress.status in RUNNING

    # --- the backup before an update -------------------------------------------------------------

    def _backups(self) -> list[Path]:
        """The complete in-app backups, oldest first (their names sort by time)."""
        if not BACKUP_DIR.is_dir():
            return []
        return sorted(p for p in BACKUP_DIR.iterdir()
                      if p.is_dir() and p.name.startswith(BACKUP_PREFIX) and not p.name.endswith(".partial"))

    async def _create_backup(self, version: str, release: str) -> Path:
        """A copy of the database with the commits still in unos.db-wal (copy_database, ADR-0306) and
        the version it belongs to, in a folder of BACKUP_DIR that only root reads; the oldest beyond
        KEPT_BACKUPS go. The folder is renamed into place when complete."""
        def write() -> Path:
            BACKUP_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(BACKUP_DIR, 0o700)
            final = BACKUP_DIR / f"{BACKUP_PREFIX}{utc_now().strftime('%Y%m%d-%H%M%S-%f')}"
            partial = final.with_name(final.name + ".partial")
            partial.mkdir(mode=0o700)
            try:
                copy_database(DB_FILE, partial / "unos.db")
                _verify_database(partial / "unos.db")
                info = {"version": version, "release": release, "backup_date": utc_now().isoformat()}
                (partial / "backup_info.json").write_text(json.dumps(info, indent=2))
                partial.rename(final)
            except BaseException:
                shutil.rmtree(partial, ignore_errors=True)
                raise
            for old in self._backups()[:-KEPT_BACKUPS]:
                shutil.rmtree(old, ignore_errors=True)
            # What a stop in the middle of an earlier backup left.
            for left in BACKUP_DIR.glob(f"{BACKUP_PREFIX}*.partial"):
                shutil.rmtree(left, ignore_errors=True)
            return final

        try:
            backup = await asyncio.to_thread(write)
        except (OSError, sqlite3.Error, BackupValidationError) as e:
            raise UpdateError(f"The backup of the database failed: {e}") from e
        logger.info("Backed up the database of %s into %s", version, backup)
        return backup

    def _rollback_backup(self) -> tuple[Path, str]:
        """The newest backup and the version it returns to; UpdateError when there is none, or
        when it is not of the update to the version that runs."""
        backups = self._backups()
        if not backups:
            raise UpdateError(f"No backup in {BACKUP_DIR}")
        newest = backups[-1]
        try:
            info = json.loads((newest / "backup_info.json").read_text())
        except (OSError, ValueError) as e:
            raise UpdateError(f"The backup {newest.name} cannot be read: {e}") from e
        version, release = info.get("version"), info.get("release")
        if not isinstance(version, str) or not version or version == "unknown":
            raise UpdateError(f"The backup {newest.name} names no version to return to")
        running = installed_version()
        if release != running:
            raise UpdateError(f"The newest backup is of the update from {version} to {release}, and UNOS runs "
                              f"{running}: there is no backup of the version before it")
        return newest, version

    async def _restore_database(self, backup: Path) -> None:
        """Put the backup's database back through the open database, with the restore's backup API
        (_replace_database): the live file stays in the write-ahead log, and its -wal holds the
        backup's pages, where a copy over unos.db beside the log was read through the log
        (ADR-0306). The copy is checked first, so a bad one changes nothing."""
        copy = backup / "unos.db"

        def restore() -> None:
            if not copy.is_file():
                raise UpdateError(f"The backup {backup.name} has no unos.db")
            _verify_database(copy)
            _verify_page_size(copy, DB_FILE)
            _replace_database(copy, DB_FILE)

        # As a restore of a backup: under smb_lock, then its post-restore step (the tables of this
        # version, SMB and the iSCSI targets after the database, ADR-0138), so that the supervisor
        # that runs follows the database also when the version before does not start.
        async with shares_service.smb_lock():
            try:
                await asyncio.to_thread(restore)
            except (OSError, sqlite3.Error, BackupValidationError, TimeoutError) as e:
                raise UpdateError(f"The database of {backup.name} could not be put back: {e}") from e
            logger.info("Put back the database of %s", backup)
            result: dict = {"restored": ["database"], "warnings": []}
            await importlib.import_module("src.backup.router")._after_database_restore(result)
        for warning in result["warnings"]:
            logger.warning("After the database of %s: %s", backup.name, warning)

    # --- install and rollback --------------------------------------------------------------------

    def _refusal(self) -> str | None:
        """Why no job can start now; None when one can."""
        reason = installer.current.unavailable_reason()
        if reason:
            return reason
        if self.running():
            return f"Update already in progress: {self._progress.status.value}"
        if restore_running():
            return RESTORE_RUNS
        return None

    def _begin(self, action: str, version: str) -> None:
        self._progress.action = action
        self._progress.version = version
        self._progress.rolled_back = False
        self._progress.started_at = utc_now()
        self._progress.completed_at = None
        self._update_progress(UpdateStatus.CHECKING, 0, f"Starting the {action}")

    def start_install(self, release: ReleaseInfo) -> None:
        """Start install_update as the job, or raise UpdateConflict. Nothing is awaited between the
        check and the start, so two requests cannot both start one."""
        self._load_progress_state()
        refusal = self._refusal()
        if refusal:
            raise UpdateConflict(refusal)
        running = installed_version()
        if running == "unknown":
            raise UpdateError("The installed version is unknown (UNOS_VERSION): an update could not roll back to it")
        if self._compare_versions(running, release.tag_name) >= 0:
            raise UpdateError(f"Already running version {running}, which is not older than {release.tag_name}")
        self._begin("update", release.tag_name)
        self._job = asyncio.create_task(self._run(self.install_update(release, begun=True)))

    def start_rollback(self) -> None:
        """Start rollback as the job, or raise UpdateConflict, or UpdateError without a backup."""
        self._load_progress_state()
        refusal = self._refusal()
        if refusal:
            raise UpdateConflict(refusal)
        if not self._progress.can_rollback:
            raise UpdateError("Rollback not available - no recent backup exists")
        _, version = self._rollback_backup()
        self._begin("rollback", version)
        self._job = asyncio.create_task(self._run(self.rollback(begun=True)))

    async def _run(self, job) -> None:
        try:
            await job
        except UpdateError:
            pass  # recorded in the progress
        except Exception:
            logger.exception("The update job failed")

    async def _wait_until_healthy(self, version: str) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + HEALTH_TIMEOUT_SECONDS
        while True:
            try:
                if await asyncio.wait_for(installer.current.healthy(), max(deadline - loop.time(), 0.001)):
                    return
            except Exception as e:  # a check that fails or hangs answers no
                logger.debug("The health check failed: %s", e)
            if loop.time() >= deadline:
                raise UpdateError(f"{version} did not answer within {HEALTH_TIMEOUT_SECONDS:g} seconds")
            await asyncio.sleep(HEALTH_INTERVAL_SECONDS)

    async def install_update(self, release: ReleaseInfo, *, begun: bool = False) -> None:
        """Get the release's images, back up, switch to the release and wait for it to answer;
        roll back when the switch fails or it does not answer (ADR-0049, ADR-0050)."""
        async with self._lock:
            if not begun:
                refusal = self._refusal()
                if refusal:
                    raise UpdateConflict(refusal)
                self._begin("update", release.tag_name)
            tag = release.tag_name
            current = (await self.get_current_version()).version
            started_at = self._progress.started_at or utc_now()
            backup: Path | None = None
            try:
                self._update_progress(UpdateStatus.DOWNLOADING, 10, f"Getting the images of {tag}")
                await _within(installer.current.fetch_release(release), FETCH_TIMEOUT_SECONDS,
                              f"Getting the images of {tag}")
                self._update_progress(UpdateStatus.BACKING_UP, 30, "Backing up the database")
                backup = await self._create_backup(current, tag)
                self._progress.can_rollback = True
                self._update_progress(UpdateStatus.INSTALLING, 50, f"Starting {tag}")
                await _within(installer.current.switch_to(release), SWITCH_TIMEOUT_SECONDS, f"Starting {tag}")
                self._update_progress(UpdateStatus.VERIFYING, 80, f"Waiting for {tag} to answer")
                await self._wait_until_healthy(tag)
            except Exception as e:
                error = str(e) or type(e).__name__
                if backup is not None:
                    error = await self._roll_back_failed_update(backup, current, error)
                else:
                    self._update_progress(UpdateStatus.FAILED, self._progress.progress_percent,
                                          "Update failed", error=error)
                logger.error("The update to %s failed: %s", tag, error)
                await self._add_history_entry(current, tag, "failed", started_at, utc_now(), error=error)
                raise UpdateError(error) from e

            self._update_progress(UpdateStatus.COMPLETED, 100, f"Updated to {tag}")
            logger.info("Updated from %s to %s", current, tag)
            await self._add_history_entry(current, tag, "success", started_at, utc_now(), changelog=release.body)

    async def _roll_back_failed_update(self, backup: Path, version: str, error: str) -> str:
        """The automatic rollback of a failed update; returns the update's error with its outcome."""
        self._update_progress(UpdateStatus.ROLLBACK, 90, f"Rolling back to {version}", error=error)
        try:
            await self._restore_database(backup)
            await _within(installer.current.switch_back(version), SWITCH_TIMEOUT_SECONDS, f"Starting {version}")
        except Exception as e:
            error = f"{error}; the rollback to {version} failed too: {e}"
            self._update_progress(UpdateStatus.FAILED, 90, "Update and rollback failed", error=error)
            return error
        error = f"{error}; rolled back to {version}"
        self._progress.rolled_back = True
        self._progress.can_rollback = False
        self._update_progress(UpdateStatus.FAILED, 100, "Update failed, rolled back", error=error)
        return error

    async def rollback(self, *, begun: bool = False) -> None:
        """Return to the version before the last update: its database from the backup, through the
        open database, then its containers, and wait for it to answer."""
        async with self._lock:
            if not begun:
                refusal = self._refusal()
                if refusal:
                    raise UpdateConflict(refusal)
                if not self._progress.can_rollback:
                    raise UpdateError("No rollback available")
                self._begin("rollback", "")
            try:
                backup, version = self._rollback_backup()
                self._progress.version = version
                self._update_progress(UpdateStatus.ROLLBACK, 20, "Putting back the database")
                await self._restore_database(backup)
                self._update_progress(UpdateStatus.ROLLBACK, 50, f"Starting {version}")
                await _within(installer.current.switch_back(version), SWITCH_TIMEOUT_SECONDS, f"Starting {version}")
                self._update_progress(UpdateStatus.VERIFYING, 80, f"Waiting for {version} to answer")
                await self._wait_until_healthy(version)
            except Exception as e:
                error = str(e) or type(e).__name__
                logger.error("The rollback failed: %s", error)
                self._update_progress(UpdateStatus.FAILED, self._progress.progress_percent, "Rollback failed",
                                      error=error)
                raise UpdateError(error) from e
            self._progress.can_rollback = False
            self._update_progress(UpdateStatus.COMPLETED, 100, f"Rolled back to {version}")
            logger.info("Rolled back to %s", version)

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

    # --- the automatic install -------------------------------------------------------------------

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
        """Store the settings; the next automatic look is one interval from now, none while it is
        off. Turning it on is refused where no installer can install (UpdateConflict)."""
        reason = installer.current.unavailable_reason()
        if config.auto_install and reason:
            raise UpdateConflict(reason)
        before = await self.get_config()
        try:
            state_files.replace_file(UPDATE_CONFIG_FILE, config.model_dump_json(indent=2).encode(), sync=False)
        except Exception as e:
            logger.error(f"Failed to save update config: {e}")
            raise UpdateError(f"Failed to save configuration: {e}")
        self._load_progress_state()
        due = self._progress.next_automatic_check
        if not config.auto_install:
            due = None
        elif config != before or due is None:
            due = utc_now() + timedelta(hours=config.check_interval_hours)
        if due != self._progress.next_automatic_check:
            self._progress.next_automatic_check = due
            self._save_progress_state()
            self.version += 1  # the events socket sends the next look to every open tab


async def automatic_install_pass(now: datetime) -> None:
    """One pass of the automatic install: when it is on, an installer can install and its look is
    due, look for the latest stable release and install it when it is newer. The next look is one
    interval later, whatever this one found. Writes and logs nothing while no look is due (ADR-0214)."""
    updater = get_updater()
    config = await updater.get_config()
    if not config.auto_install or installer.current.unavailable_reason():
        return
    due = updater._progress.next_automatic_check
    if due is not None and now < due:
        return
    updater._load_progress_state()
    updater._progress.next_automatic_check = now + timedelta(hours=config.check_interval_hours)
    updater._save_progress_state()
    if due is None or updater.running():
        return
    try:
        check = await updater.check_for_updates()
        if check.update_available and check.release_info is not None:
            logger.info("The automatic install installs %s", check.release_info.tag_name)
            updater.start_install(check.release_info)
            # Shielded: a stop of the loop leaves the job to end as a job started by hand does.
            await asyncio.shield(updater._job)
    except UpdateError as e:
        logger.warning("The automatic install did not install: %s", e)


async def automatic_install_loop() -> None:
    while True:
        await asyncio.sleep(AUTOMATIC_TICK_SECONDS)
        try:
            await automatic_install_pass(utc_now())
        except Exception:
            logger.exception("The automatic install's pass failed")


_automatic_task: asyncio.Task | None = None


async def start_automatic_install() -> None:
    """Started by the lifespan of src/main.py (ADR-0043)."""
    global _automatic_task
    get_updater()._load_progress_state()
    _automatic_task = asyncio.create_task(automatic_install_loop(), name="unos-automatic-install")


async def stop_automatic_install() -> None:
    global _automatic_task
    if _automatic_task is not None:
        _automatic_task.cancel()
        await asyncio.gather(_automatic_task, return_exceptions=True)
        _automatic_task = None


def _progress_of(state: object) -> UpdateProgress:
    """The decoded progress file as the progress; ValueError or TypeError for anything else (ADR-0324)."""
    if not isinstance(state, dict):
        raise ValueError("it holds no progress")
    return UpdateProgress.model_validate({**state, "install_unavailable": None})


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
_updater: Updater | None = None


def get_updater() -> Updater:
    """Get or create the global updater instance."""
    global _updater
    if _updater is None:
        _updater = Updater()
    return _updater
