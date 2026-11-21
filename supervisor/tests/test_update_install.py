"""Install Update, Rollback and the automatic install of System > Updates (system.in-app-updater,
ADR-0439).

The updater runs the steps that the ADRs settle: the release's images first, then a copy of the
database with SQLite's backup API (copy_database, ADR-0306) in a folder of its own on the host's
disk, the switch to the release, its health check and, when the switch fails or the release does
not answer in time, the rollback: the database of the copy put back through the open database
with the restore's backup API, and the version before started again. Where a release's images
come from and how the supervisor replaces its own container are not decided (ADR-0439): the
release installer is a seam, which these tests fill with a fake, and the supervisor's own
installer refuses Install Update and Rollback with its reason.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import sqlite3
import stat
from contextlib import closing
from datetime import datetime, timedelta, UTC
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient

from src.auth.utils import verify_token
from src.main import app
from src.updates import installer as installer_module
from src.updates.schemas import ReleaseInfo, UpdateStatus
from tests.test_backup_restore import _make_db, _usernames

updater_module = importlib.import_module("src.updates.updater")
backup_router = importlib.import_module("src.backup.router")

API = "/api/v1/updates"
INSTALLED = "v1.2.0"
RELEASE = "v1.3.0"


def _release(tag: str = RELEASE) -> ReleaseInfo:
    return ReleaseInfo(
        tag_name=tag, name=f"UNOS {tag}", body="Fixes and features.",
        published_at=datetime(2026, 12, 1, 10, 0, tzinfo=UTC),
        html_url=f"https://github.com/und3f1ned/unos/releases/tag/{tag}",
    )


class FakeInstaller:
    """A release installer (src/updates/installer.py) that records its calls; each step does what
    its attribute says: None succeeds, an exception is raised, an Event is waited for first."""

    def __init__(self, db_path: Path, monkeypatch):
        self.db_path = db_path
        # The version that runs follows the switches, as a new supervisor's UNOS_VERSION would.
        self.monkeypatch = monkeypatch
        self.calls: list[tuple[str, str]] = []
        self.fetch: object = None
        self.switch: object = None
        self.back: object = None
        self.answers: list[bool] = [True]
        # A health check that never answers.
        self.hangs = False
        # What the release writes to the database once it runs: gone after a rollback.
        self.release_writes = "release-row"

    def unavailable_reason(self) -> str | None:
        return None

    async def _step(self, what: object) -> None:
        if isinstance(what, asyncio.Event):
            await what.wait()
        elif isinstance(what, BaseException):
            raise what

    async def fetch_release(self, release: ReleaseInfo) -> None:
        self.calls.append(("fetch", release.tag_name))
        await self._step(self.fetch)

    async def switch_to(self, release: ReleaseInfo) -> None:
        self.calls.append(("switch_to", release.tag_name))
        await self._step(self.switch)
        self.monkeypatch.setenv("UNOS_VERSION", release.tag_name)
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("INSERT INTO users (username, hashed_password, is_admin) VALUES (?, 'x', 0)",
                         (self.release_writes,))
            conn.commit()

    async def healthy(self) -> bool:
        self.calls.append(("healthy", ""))
        if self.hangs:
            await asyncio.Event().wait()
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]

    async def switch_back(self, version: str) -> None:
        self.calls.append(("switch_back", version))
        await self._step(self.back)
        self.monkeypatch.setenv("UNOS_VERSION", version)


@pytest.fixture
def world(tmp_path, monkeypatch):
    """The updater with its state files, its backups and a live database in the write-ahead log
    in a folder of the test, an installed version, a fake installer and a fake GitHub."""
    data = tmp_path / "data"
    data.mkdir()
    db_path = data / "unos.db"
    _make_db(db_path, users=[("admin", 1)], wal=True)
    backups = tmp_path / "host-backups"
    monkeypatch.setattr(updater_module, "UPDATE_STATE_FILE", data / "update_state.json")
    monkeypatch.setattr(updater_module, "UPDATE_HISTORY_FILE", data / "update_history.json")
    monkeypatch.setattr(updater_module, "UPDATE_CONFIG_FILE", data / "update_config.json")
    monkeypatch.setattr(updater_module, "BACKUP_DIR", backups)
    monkeypatch.setattr(updater_module, "DB_FILE", db_path)
    monkeypatch.setattr(updater_module, "HEALTH_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(updater_module, "HEALTH_INTERVAL_SECONDS", 0.01)
    monkeypatch.setenv("UNOS_VERSION", INSTALLED)
    fake = FakeInstaller(db_path, monkeypatch)
    monkeypatch.setattr(installer_module, "current", fake)
    # The restore's post-restore step (tables, SMB, iSCSI on the host), which the backup tests show:
    # here only that it follows the database.
    after_restore = []

    async def after_database_restore(result):
        after_restore.append(_usernames(db_path))

    monkeypatch.setattr(backup_router, "_after_database_restore", after_database_restore)
    updater = updater_module.Updater()
    monkeypatch.setattr(updater_module, "_updater", updater)
    github = SimpleNamespace(latest=_release(), asked=[])

    async def fetch_latest_release(self, repo="und3f1ned/unos", include_prereleases=False):
        github.asked.append(include_prereleases)
        return github.latest

    monkeypatch.setattr(updater_module.Updater, "fetch_latest_release", fetch_latest_release)
    # The app's pool: a connection that stays open, so that the last commits stay in unos.db-wal.
    pool = sqlite3.connect(db_path)
    assert pool.execute("PRAGMA journal_mode").fetchone() == ("wal",)
    try:
        yield SimpleNamespace(updater=updater, installer=fake, db=db_path, pool=pool, backups=backups,
                              data=data, github=github, after_restore=after_restore)
    finally:
        pool.close()


def _commit(world, sql: str) -> None:
    world.pool.execute(sql)
    world.pool.commit()


def _users(world) -> set[str]:
    return {row[0] for row in world.pool.execute("SELECT username FROM users")}


def _backups(world) -> list[Path]:
    return sorted(world.backups.iterdir()) if world.backups.exists() else []


@pytest.fixture
async def api(world):
    """The updates API as an administrator calls it, on the test's event loop: a job it starts runs
    there until it ends."""
    app.dependency_overrides[verify_token] = lambda: SimpleNamespace(
        id=1, username="admin", is_admin=True, is_active=True)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.pop(verify_token, None)


async def _until_done(world, timeout: float = 5.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        progress = world.updater.get_progress()
        if progress.status in (UpdateStatus.COMPLETED, UpdateStatus.FAILED):
            return progress
        await asyncio.sleep(0.01)
    pytest.fail(f"the job did not end: {world.updater.get_progress()}")


class TestTheBackupHoldsTheLastCommits:
    """The cause of the entry (ADR-0306): shutil.copy2 of unos.db left out the commits still in
    unos.db-wal, and a copy put back beside that log was read through it."""

    async def test_the_backup_is_a_copy_with_the_commits_still_in_the_log(self, world):
        _commit(world, "INSERT INTO users (username, hashed_password, is_admin) VALUES ('recent', 'x', 0)")
        assert (world.data / "unos.db-wal").stat().st_size > 0

        backup = await world.updater._create_backup(INSTALLED, RELEASE)

        copy = backup / "unos.db"
        assert _usernames(copy) == {"admin", "recent"}
        header = copy.read_bytes()[:100]
        assert (header[18], header[19]) == (1, 1)  # one file with the rollback journal
        assert sorted(os.listdir(backup)) == ["backup_info.json", "unos.db"]
        assert json.loads((backup / "backup_info.json").read_text())["version"] == INSTALLED
        # On the host's disk, root's alone: the copy holds every password hash and the TOTP secrets.
        assert backup.parent == world.backups
        assert stat.S_IMODE(world.backups.stat().st_mode) == 0o700
        assert stat.S_IMODE(backup.stat().st_mode) == 0o700

    async def test_a_rollback_puts_the_copy_back_through_the_open_database(self, world, api):
        _commit(world, "INSERT INTO users (username, hashed_password, is_admin) VALUES ('recent', 'x', 0)")
        assert (await api.post(f"{API}/install")).status_code == 200
        assert (await _until_done(world)).status == UpdateStatus.COMPLETED
        _commit(world, "INSERT INTO users (username, hashed_password, is_admin) VALUES ('later', 'x', 0)")
        assert _users(world) == {"admin", "recent", "release-row", "later"}

        assert (await api.post(f"{API}/rollback")).status_code == 200
        progress = await _until_done(world)

        assert progress.status == UpdateStatus.COMPLETED, progress
        # The open connection reads the database of the backup: what came after it is gone.
        assert _users(world) == {"admin", "recent"}
        # Then the restore's post-restore step, on the database put back.
        assert world.after_restore == [{"admin", "recent"}]
        header = world.db.read_bytes()[:100]
        assert (header[18], header[19]) == (2, 2)  # the live file stays in the write-ahead log

    async def test_old_backups_are_removed_but_the_newest(self, world):
        # What a stop in the middle of a backup left.
        (world.backups / "in-app-20261130-100000-000000.partial").mkdir(parents=True)
        for _ in range(updater_module.KEPT_BACKUPS + 2):
            await world.updater._create_backup(INSTALLED, RELEASE)
            await asyncio.sleep(0.001)

        assert len(_backups(world)) == updater_module.KEPT_BACKUPS
        assert not any(path.name.endswith(".partial") for path in _backups(world))

    async def test_a_backup_that_fails_leaves_no_folder(self, world, monkeypatch):
        def full(src, dest):
            Path(dest).write_bytes(b"part")
            raise sqlite3.OperationalError("database or disk is full")

        monkeypatch.setattr(updater_module, "copy_database", full)

        with pytest.raises(updater_module.UpdateError, match="database or disk is full"):
            await world.updater._create_backup(INSTALLED, RELEASE)

        assert _backups(world) == []


    def test_the_backups_are_on_the_hosts_disk(self):
        """Mounted from the host, so that a rollback still has them once the supervisor's container
        was replaced (ADR-0173: they were in its writable layer)."""
        compose = yaml.safe_load((Path(__file__).resolve().parents[2] / "deploy" / "docker-compose.yml").read_text())
        default = type(updater_module.get_settings()).model_fields["update_backup_dir"].default
        assert f"{default}:{default}" in compose["services"]["supervisor"]["volumes"]


class TestInstallUpdate:
    async def test_it_gets_the_release_backs_up_switches_and_checks_its_health(self, world, api):
        world.installer.answers = [False, False, True]

        response = await api.post(f"{API}/install")

        assert response.status_code == 200, response.text
        assert response.json() == {"status": "started", "message": f"Installing update to {RELEASE}",
                                   "version": RELEASE}
        progress = await _until_done(world)
        assert progress.status == UpdateStatus.COMPLETED, progress
        assert (progress.action, progress.version, progress.rolled_back) == ("update", RELEASE, False)
        assert progress.can_rollback is True
        # The images before the backup (ADR-0050), the backup right before the switch.
        assert world.installer.calls == [("fetch", RELEASE), ("switch_to", RELEASE), ("healthy", ""),
                                         ("healthy", ""), ("healthy", "")]
        [backup] = _backups(world)
        assert "release-row" not in _usernames(backup / "unos.db")
        [entry] = (await api.get(f"{API}/history")).json()
        assert (entry["from_version"], entry["to_version"], entry["status"]) == (INSTALLED, RELEASE, "success")

    async def test_images_that_cannot_be_got_change_nothing(self, world, api):
        world.installer.fetch = updater_module.UpdateError("manifest unknown")

        await api.post(f"{API}/install")
        progress = await _until_done(world)

        assert progress.status == UpdateStatus.FAILED
        assert progress.error == "manifest unknown"
        assert (progress.rolled_back, progress.can_rollback) == (False, False)
        assert world.installer.calls == [("fetch", RELEASE)]
        assert _backups(world) == []
        assert (await api.get(f"{API}/history")).json()[0]["status"] == "failed"

    async def test_a_release_that_does_not_answer_in_time_is_rolled_back(self, world, api):
        world.installer.answers = [False]

        await api.post(f"{API}/install")
        progress = await _until_done(world)

        assert progress.status == UpdateStatus.FAILED
        assert progress.rolled_back is True
        assert progress.error == f"{RELEASE} did not answer within 0.2 seconds; rolled back to {INSTALLED}"
        assert world.installer.calls[-1] == ("switch_back", INSTALLED)
        # The database of the backup again: what the release wrote is gone.
        assert _users(world) == {"admin"}
        assert progress.can_rollback is False
        [entry] = (await api.get(f"{API}/history")).json()
        assert (entry["status"], entry["error"]) == ("failed", progress.error)

    async def test_a_health_check_that_hangs_ends_at_the_deadline(self, world, api):
        world.installer.hangs = True

        await api.post(f"{API}/install")
        progress = await _until_done(world, timeout=2.0)

        assert progress.status == UpdateStatus.FAILED
        assert progress.rolled_back is True
        assert progress.error == f"{RELEASE} did not answer within 0.2 seconds; rolled back to {INSTALLED}"

    async def test_a_switch_that_takes_too_long_is_rolled_back(self, world, api, monkeypatch):
        monkeypatch.setattr(updater_module, "SWITCH_TIMEOUT_SECONDS", 0.05)
        world.installer.switch = asyncio.Event()

        await api.post(f"{API}/install")
        progress = await _until_done(world)

        assert progress.error == f"Starting {RELEASE} took longer than 0.05 seconds; rolled back to {INSTALLED}"
        assert world.installer.calls[-1] == ("switch_back", INSTALLED)

    async def test_a_switch_that_fails_is_rolled_back(self, world, api):
        world.installer.switch = updater_module.UpdateError("the supervisor's container did not start")

        await api.post(f"{API}/install")
        progress = await _until_done(world)

        assert progress.status == UpdateStatus.FAILED
        assert progress.error == f"the supervisor's container did not start; rolled back to {INSTALLED}"
        assert [call[0] for call in world.installer.calls] == ["fetch", "switch_to", "switch_back"]
        assert _users(world) == {"admin"}

    async def test_a_rollback_that_fails_too_says_so_and_can_be_tried_again(self, world, api):
        world.installer.answers = [False]
        world.installer.back = updater_module.UpdateError("no previous images")

        await api.post(f"{API}/install")
        progress = await _until_done(world)

        assert progress.status == UpdateStatus.FAILED
        assert progress.rolled_back is False
        assert progress.error == (f"{RELEASE} did not answer within 0.2 seconds; the rollback to {INSTALLED} "
                                  "failed too: no previous images")
        assert progress.can_rollback is True

    async def test_a_second_install_or_a_rollback_while_one_runs_is_refused(self, world, api):
        world.installer.fetch = asyncio.Event()
        assert (await api.post(f"{API}/install")).status_code == 200

        second = await api.post(f"{API}/install")
        rollback = await api.post(f"{API}/rollback")

        assert second.status_code == 409
        assert second.json()["detail"] == "Update already in progress: downloading"
        assert rollback.status_code == 409
        world.installer.fetch.set()
        assert (await _until_done(world)).status == UpdateStatus.COMPLETED
        assert [call for call in world.installer.calls if call[0] == "fetch"] == [("fetch", RELEASE)]

    async def test_of_two_requests_at_once_one_starts(self, world, api):
        world.installer.fetch = asyncio.Event()

        first, second = await asyncio.gather(api.post(f"{API}/install"), api.post(f"{API}/install"))

        assert sorted([first.status_code, second.status_code]) == [200, 409]
        world.installer.fetch.set()
        await _until_done(world)
        assert [call for call in world.installer.calls if call[0] == "fetch"] == [("fetch", RELEASE)]

    async def test_it_is_refused_while_a_restore_runs(self, world, api, monkeypatch):
        monkeypatch.setattr(backup_router, "_restore_running", True)

        response = await api.post(f"{API}/install")

        assert response.status_code == 409
        assert response.json()["detail"] == "A restore of a backup runs: update when it has ended"
        assert world.installer.calls == []

    async def test_a_restore_is_refused_while_it_runs(self, world, api):
        world.installer.fetch = asyncio.Event()
        await api.post(f"{API}/install")

        response = await api.post("/api/v1/backup/restore/unos_backup_20261201_100000.tar.gz", json={})

        assert response.status_code == 409
        assert response.json()["detail"] == "An update or a rollback of UNOS runs: restore when it has ended"
        world.installer.fetch.set()
        await _until_done(world)

    @pytest.mark.parametrize("running,detail", [
        (RELEASE, f"Already running version {RELEASE}, which is not older than {RELEASE}"),
        # Commits after the release's tag: the release would be a step back.
        (f"{RELEASE}-3-g712fc3a", f"Already running version {RELEASE}-3-g712fc3a, which is not older than {RELEASE}"),
        ("unknown", "The installed version is unknown (UNOS_VERSION): an update could not roll back to it"),
    ])
    async def test_a_release_that_is_not_newer_or_an_unknown_version_is_refused(self, world, api, monkeypatch,
                                                                              running, detail):
        monkeypatch.setenv("UNOS_VERSION", running)

        response = await api.post(f"{API}/install")

        assert response.status_code == 400
        assert response.json()["detail"] == detail
        assert world.installer.calls == []

    async def test_its_progress_is_written_once_a_step(self, world, api, monkeypatch):
        """ADR-0173: the download's progress rewrote update_state.json for every 8 KiB."""
        writes = []
        real = updater_module.state_files.write_json

        def counted(path, value, **kwargs):
            if Path(path) == updater_module.UPDATE_STATE_FILE:
                writes.append(value["status"])
            real(path, value, **kwargs)

        monkeypatch.setattr(updater_module.state_files, "write_json", counted)
        world.installer.answers = [False] * 5 + [True]

        await api.post(f"{API}/install")
        await _until_done(world)

        assert writes == ["checking", "downloading", "backing_up", "installing", "verifying", "completed"]


class TestRollback:
    async def test_it_returns_to_the_version_before(self, world, api):
        await api.post(f"{API}/install")
        await _until_done(world)
        world.installer.calls.clear()

        response = await api.post(f"{API}/rollback")

        assert response.status_code == 200, response.text
        progress = await _until_done(world)
        assert progress.status == UpdateStatus.COMPLETED
        assert (progress.action, progress.version) == ("rollback", INSTALLED)
        assert world.installer.calls[0] == ("switch_back", INSTALLED)
        assert progress.can_rollback is False
        assert _users(world) == {"admin"}

    async def test_without_a_backup_it_is_refused(self, world, api):
        response = await api.post(f"{API}/rollback")

        assert response.status_code == 400
        assert world.installer.calls == []

    @pytest.mark.parametrize("damage", ["gone", "garbage", "pages of 1024 bytes"])
    async def test_a_backup_that_is_gone_or_bad_fails_the_rollback_before_anything_changes(self, world, api,
                                                                                         damage):
        await api.post(f"{API}/install")
        await _until_done(world)
        [backup] = _backups(world)
        copy = backup / "unos.db"
        copy.unlink()
        if damage == "garbage":
            copy.write_bytes(b"SQLite format 3\x00" + bytes(200))
        elif damage == "pages of 1024 bytes":
            with closing(sqlite3.connect(copy)) as conn:
                conn.execute("PRAGMA page_size = 1024")
                conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, "
                             "hashed_password TEXT NOT NULL, is_admin BOOLEAN)")
                conn.execute("INSERT INTO users (username, hashed_password, is_admin) VALUES ('admin', 'h', 1)")
                conn.commit()
        world.installer.calls.clear()

        await api.post(f"{API}/rollback")
        progress = await _until_done(world)

        assert progress.status == UpdateStatus.FAILED
        assert "could not be put back" in progress.error or "has no unos.db" in progress.error
        assert world.installer.calls == []
        assert _users(world) == {"admin", "release-row"}
        assert world.after_restore == []
        assert progress.can_rollback is True

    async def test_after_another_version_was_installed_no_rollback_is_offered(self, world, api, monkeypatch):
        """update.sh installed another version since: the newest backup is not of the version before it."""
        await api.post(f"{API}/install")
        await _until_done(world)
        monkeypatch.setenv("UNOS_VERSION", "v1.4.0")

        assert (await api.get(f"{API}/progress")).json()["can_rollback"] is False
        response = await api.post(f"{API}/rollback")

        assert response.status_code == 400
        assert response.json()["detail"] == (f"The newest backup is of the update from {INSTALLED} to {RELEASE}, "
                                             "and UNOS runs v1.4.0: there is no backup of the version before it")
        assert world.installer.calls[-1][0] == "healthy"


class TestNoInstallerIsDecided:
    """The supervisor's own installer: where a release's images come from while GHCR has none, and
    how the supervisor replaces its own container, wait for the owner (ADR-0439)."""

    @pytest.fixture
    def undecided(self, world, monkeypatch):
        monkeypatch.setattr(installer_module, "current", installer_module.Undecided())

    async def test_install_rollback_and_the_automatic_install_are_refused_with_the_reason(self, world, api,
                                                                                         undecided):
        progress = (await api.get(f"{API}/progress")).json()
        reason = progress["install_unavailable"]
        assert reason.startswith("Installing a release from the web UI waits for two decisions")

        for response in (await api.post(f"{API}/install"), await api.post(f"{API}/rollback"),
                         await api.put(f"{API}/config", json={"auto_install": True, "check_interval_hours": 24})):
            assert response.status_code == 409, response.text
            assert response.json()["detail"] == reason
        assert not world.backups.exists()
        assert not updater_module.UPDATE_CONFIG_FILE.exists()

    async def test_an_installer_that_is_decided_answers_none(self, world, api):
        assert (await api.get(f"{API}/progress")).json()["install_unavailable"] is None


class TestAStopInTheMiddle:
    def test_an_update_that_a_stop_interrupted_is_failed_at_the_next_start(self, world):
        updater_module.state_files.write_json(updater_module.UPDATE_STATE_FILE, {
            "status": "downloading", "progress_percent": 30, "message": "", "error": None,
            "started_at": "2026-12-01T10:00:00+00:00", "completed_at": None, "can_rollback": True,
            "action": "update", "version": RELEASE,
        }, sync=False)
        updater = updater_module.Updater()

        updater.recover_after_start()

        progress = updater.get_progress()
        assert progress.status == UpdateStatus.FAILED
        assert progress.error == "The supervisor stopped while the update ran"
        # Recorded on its file once: the next start finds it ended; can_rollback of the update before
        # stays (offered while its backup is of the version that runs).
        stored = json.loads(updater_module.UPDATE_STATE_FILE.read_text())
        assert (stored["status"], stored["can_rollback"]) == ("failed", True)

    def test_the_supervisor_recovers_it_at_its_start(self, world, monkeypatch):
        updater_module.state_files.write_json(updater_module.UPDATE_STATE_FILE, {
            "status": "verifying", "progress_percent": 80, "can_rollback": True, "action": "update",
            "version": RELEASE,
        }, sync=False)
        monkeypatch.setattr(updater_module, "_updater", None)
        # No loops and no host commands at this start.
        monkeypatch.setenv("UNOS_DISABLE_BACKGROUND_TASKS", "1")

        with TestClient(app):
            progress = updater_module.get_updater().get_progress()

        assert progress.status == UpdateStatus.FAILED
        assert progress.error == "The supervisor stopped while the update ran"


class TestAutomaticInstall:
    NOW = datetime(2026, 12, 2, 8, 0, tzinfo=UTC)

    async def _turn_on(self, api, hours: int = 6):
        response = await api.put(f"{API}/config", json={"auto_install": True, "check_interval_hours": hours})
        assert response.status_code == 200, response.text

    async def test_a_newer_release_is_installed_at_the_interval(self, world, api, monkeypatch):
        monkeypatch.setattr(updater_module, "utc_now", lambda: self.NOW)
        await self._turn_on(api, hours=6)
        assert (await api.get(f"{API}/progress")).json()["next_automatic_check"] == "2026-12-02T14:00:00Z"

        await updater_module.automatic_install_pass(self.NOW + timedelta(hours=5, minutes=59))
        assert world.github.asked == []

        await updater_module.automatic_install_pass(self.NOW + timedelta(hours=6))

        assert world.github.asked == [False]  # stable releases only
        progress = world.updater.get_progress()
        assert progress.status == UpdateStatus.COMPLETED, progress
        assert progress.version == RELEASE
        assert progress.next_automatic_check == self.NOW + timedelta(hours=12)

    async def test_the_installed_release_is_left_as_it_is(self, world, api, monkeypatch):
        monkeypatch.setattr(updater_module, "utc_now", lambda: self.NOW)
        monkeypatch.setenv("UNOS_VERSION", RELEASE)
        await self._turn_on(api)

        await updater_module.automatic_install_pass(self.NOW + timedelta(hours=6))

        assert world.github.asked == [False]
        assert world.installer.calls == []

    async def test_off_it_asks_nothing(self, world, api, monkeypatch):
        monkeypatch.setattr(updater_module, "utc_now", lambda: self.NOW)
        await self._turn_on(api)
        response = await api.put(f"{API}/config", json={"auto_install": False, "check_interval_hours": 6})
        assert response.status_code == 200
        assert (await api.get(f"{API}/progress")).json()["next_automatic_check"] is None

        await updater_module.automatic_install_pass(self.NOW + timedelta(days=30))

        assert world.github.asked == []

    async def test_without_an_installer_it_asks_nothing(self, world, api, monkeypatch):
        monkeypatch.setattr(updater_module, "utc_now", lambda: self.NOW)
        await self._turn_on(api)
        monkeypatch.setattr(installer_module, "current", installer_module.Undecided())

        await updater_module.automatic_install_pass(self.NOW + timedelta(days=30))

        assert world.github.asked == []

    async def test_a_check_that_fails_waits_for_the_next_interval(self, world, api, monkeypatch):
        monkeypatch.setattr(updater_module, "utc_now", lambda: self.NOW)
        await self._turn_on(api, hours=1)

        async def unreachable(self, repo="und3f1ned/unos", include_prereleases=False):
            raise updater_module.UpdateError("Failed to fetch release info: timed out")

        monkeypatch.setattr(updater_module.Updater, "fetch_latest_release", unreachable)

        await updater_module.automatic_install_pass(self.NOW + timedelta(hours=1))

        assert world.installer.calls == []
        assert world.updater.get_progress().next_automatic_check == self.NOW + timedelta(hours=2)

    async def test_the_loop_runs_a_pass_at_each_tick_and_stops(self, world, monkeypatch):
        """The lifespan starts and stops it (test_lifespan.py, LOOPS)."""
        passes = []

        async def counted(now):
            passes.append(now)

        monkeypatch.setattr(updater_module, "AUTOMATIC_TICK_SECONDS", 0.01)
        monkeypatch.setattr(updater_module, "automatic_install_pass", counted)

        await updater_module.start_automatic_install()
        task = updater_module._automatic_task
        while len(passes) < 3:
            await asyncio.sleep(0.01)
        await updater_module.stop_automatic_install()

        assert task.cancelled()
        assert updater_module._automatic_task is None
