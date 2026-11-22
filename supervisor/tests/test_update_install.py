"""Install Update, Rollback and the automatic install of System > Updates (system.in-app-updater,
ADR-0443).

The updater gets the release's images, makes a copy of the database with SQLite's backup API
(copy_database, ADR-0306) in a folder of its own on the host's disk, and hands the switch to a unit
on the host: deploy/update.sh --in-app, which starts the release, checks its health and, when it
does not answer, stops it, puts the copy back and starts the version before (test_deploy_scripts.py
runs update.sh). The switch replaces the supervisor's container, so the job passes to the next
supervisor through the progress file, and whichever runs follows the unit until its result file says
how it ended. These tests fill the release installer (src/updates/installer.py) with a fake, and run
the supervisor's own one, HostUpdate, against a fake host.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import re
import sqlite3
import stat
import subprocess
from datetime import datetime, timedelta, UTC
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient

from src import host
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
UNIT = re.compile(r"unos-(update|rollback)-\d{8}-\d{6}")


def _release(tag: str = RELEASE) -> ReleaseInfo:
    return ReleaseInfo(
        tag_name=tag, name=f"UNOS {tag}", body="Fixes and features.",
        published_at=datetime(2026, 12, 1, 10, 0, tzinfo=UTC),
        html_url=f"https://github.com/und3f1ned/unos/releases/tag/{tag}",
    )


class FakeInstaller:
    """A release installer (src/updates/installer.py) that records its calls. fetch and start do what
    their attribute says: None succeeds, an exception is raised, an Event is waited for first. The unit
    of a switch ends at once with ``outcome``, written into its result file as update.sh writes it, and
    a success leaves the version it switched to running (UNOS_VERSION, as the next supervisor's); with
    ``outcome`` None the unit runs until ``end``."""

    def __init__(self, monkeypatch):
        self.monkeypatch = monkeypatch
        self.calls: list[tuple[str, ...]] = []
        self.fetch: object = None
        self.start: object = None
        self.outcome: tuple[str, str] | None = ("success", "")
        self.switches: list[SimpleNamespace] = []
        self.running: set[str] = set()

    def unavailable_reason(self) -> str | None:
        return None

    async def _step(self, what: object) -> None:
        if isinstance(what, asyncio.Event):
            await what.wait()
        elif isinstance(what, BaseException):
            raise what

    async def fetch_release(self, tag: str) -> None:
        self.calls.append(("fetch", tag))
        await self._step(self.fetch)

    async def start_switch(self, unit, action, tag, backup, result) -> None:
        self.calls.append(("switch", action, tag))
        self.switches.append(SimpleNamespace(unit=unit, action=action, tag=tag, backup=backup, result=result))
        await self._step(self.start)
        self.running.add(unit)
        if self.outcome is not None:
            self.end(*self.outcome)

    def end(self, outcome: str, message: str = "") -> None:
        """The unit of the last switch ends as update.sh does: its result, then the unit is gone."""
        switch = self.switches[-1]
        if outcome == "success":
            self.monkeypatch.setenv("UNOS_VERSION", switch.tag)
        Path(switch.result).write_text(f"{outcome}\n{message}\n")
        self.running.discard(switch.unit)

    async def switch_running(self, unit: str) -> bool:
        return unit in self.running


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
    monkeypatch.setattr(updater_module, "FOLLOW_INTERVAL_SECONDS", 0.01)
    monkeypatch.setenv("UNOS_VERSION", INSTALLED)
    fake = FakeInstaller(monkeypatch)
    monkeypatch.setattr(installer_module, "current", fake)
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
                              data=data, github=github)
    finally:
        pool.close()


def _commit(world, sql: str) -> None:
    world.pool.execute(sql)
    world.pool.commit()


def _backups(world) -> list[Path]:
    return sorted(world.backups.iterdir()) if world.backups.exists() else []


def _stored(world) -> dict:
    return json.loads(updater_module.UPDATE_STATE_FILE.read_text())


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


async def _until(check, what: str, timeout: float = 5.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        found = check()
        if found:
            return found
        await asyncio.sleep(0.01)
    pytest.fail(f"{what} did not happen")


async def _until_done(world, timeout: float = 5.0, updater=None):
    updater = updater or world.updater
    return await _until(
        lambda: (p := updater.get_progress()).status in (UpdateStatus.COMPLETED, UpdateStatus.FAILED) and p,
        f"the end of the job ({updater.get_progress()})", timeout)


async def _handed_over(world, api, path: str = "install"):
    """Start a job whose unit keeps running, until the progress file names its unit; then the
    supervisor that started it is stopped, as compose stops it for the switch."""
    world.installer.outcome = None
    assert (await api.post(f"{API}/{path}")).status_code == 200
    await _until(lambda: _stored(world).get("handover"), "the handover")
    job = world.updater._job
    job.cancel()
    await asyncio.gather(job, return_exceptions=True)
    return world.installer.switches[-1]


async def _next_supervisor(monkeypatch, version: str):
    """The supervisor that the unit started: a new process with its version, from its start."""
    monkeypatch.setenv("UNOS_VERSION", version)
    updater = updater_module.Updater()
    monkeypatch.setattr(updater_module, "_updater", updater)
    updater.recover_after_start()
    return updater


class TestTheBackupHoldsTheLastCommits:
    """The cause of system.in-app-updater (ADR-0306): shutil.copy2 of unos.db left out the commits
    still in unos.db-wal. update.sh puts the copy back while the supervisor is stopped, after the
    live log (test_deploy_scripts.py, TestUpdateScriptInApp)."""

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
    async def test_it_gets_the_images_backs_up_and_hands_the_switch_to_the_host(self, world, api):
        response = await api.post(f"{API}/install")

        assert response.status_code == 200, response.text
        assert response.json() == {"status": "started", "message": f"Installing update to {RELEASE}",
                                   "version": RELEASE}
        progress = await _until_done(world)
        assert progress.status == UpdateStatus.COMPLETED, progress
        assert (progress.action, progress.version, progress.rolled_back) == ("update", RELEASE, False)
        assert progress.can_rollback is True
        assert progress.handover is None
        # The images before the backup (ADR-0050), the backup right before the switch.
        assert world.installer.calls == [("fetch", RELEASE), ("switch", "update", RELEASE)]
        [backup] = _backups(world)
        [switch] = world.installer.switches
        assert UNIT.fullmatch(switch.unit) and switch.unit.startswith("unos-update-")
        # update.sh rolls back to this copy, and writes how it ended beside it.
        assert (switch.backup, switch.result) == (backup, backup / "update.result")
        assert _usernames(backup / "unos.db") == {"admin"}
        [entry] = (await api.get(f"{API}/history")).json()
        assert (entry["from_version"], entry["to_version"], entry["status"]) == (INSTALLED, RELEASE, "success")
        assert entry["changelog"] == "Fixes and features."

    async def test_images_that_cannot_be_got_change_nothing(self, world, api):
        world.installer.fetch = installer_module.InstallerError("Getting the images of v1.3.0 failed: manifest unknown")

        await api.post(f"{API}/install")
        progress = await _until_done(world)

        assert progress.status == UpdateStatus.FAILED
        assert progress.error == "Getting the images of v1.3.0 failed: manifest unknown"
        assert (progress.rolled_back, progress.can_rollback) == (False, False)
        assert world.installer.calls == [("fetch", RELEASE)]
        assert _backups(world) == []
        assert (await api.get(f"{API}/history")).json()[0]["status"] == "failed"

    async def test_a_release_that_the_host_rolled_back_fails_the_update(self, world, api):
        world.installer.outcome = ("rolled-back", "UNOS did not answer within 300 seconds after the update.")

        await api.post(f"{API}/install")
        progress = await _until_done(world)

        assert progress.status == UpdateStatus.FAILED
        assert progress.rolled_back is True
        assert progress.error == ("UNOS did not answer within 300 seconds after the update.; "
                                  f"rolled back to {INSTALLED}")
        # The backup's database is back: no rollback to offer.
        assert progress.can_rollback is False
        [entry] = (await api.get(f"{API}/history")).json()
        assert (entry["status"], entry["error"]) == ("failed", progress.error)

    async def test_a_switch_that_failed_on_the_host_says_why(self, world, api):
        world.installer.outcome = ("failed", "Another update.sh is running (it holds /run/unos-update.lock). "
                                             "Nothing was changed.")

        await api.post(f"{API}/install")
        progress = await _until_done(world)

        assert progress.status == UpdateStatus.FAILED
        assert progress.rolled_back is False
        assert progress.error.startswith("Another update.sh is running")
        # The version before still runs: the backup is not of a version before it.
        assert (await api.get(f"{API}/progress")).json()["can_rollback"] is False

    async def test_a_rollback_that_failed_too_keeps_rollback(self, world, api, monkeypatch):
        world.installer.outcome = ("failed", "UNOS did not answer; UNOS did not come back after the rollback either")

        await api.post(f"{API}/install")

        # update.sh left the release's containers in place.
        monkeypatch.setenv("UNOS_VERSION", RELEASE)
        progress = await _until_done(world)
        assert progress.status == UpdateStatus.FAILED
        assert (await api.get(f"{API}/progress")).json()["can_rollback"] is True

    async def test_a_unit_that_does_not_start_fails_the_update(self, world, api):
        world.installer.start = installer_module.InstallerError("The unit unos-update-1 did not start: Access denied")

        await api.post(f"{API}/install")
        progress = await _until_done(world)

        assert progress.status == UpdateStatus.FAILED
        assert progress.error == "The unit unos-update-1 did not start: Access denied"
        assert progress.handover is None
        assert _stored(world)["handover"] is None

    async def test_a_unit_that_started_although_systemd_run_failed_is_followed(self, world, api):
        """systemd-run did not answer in time, but the unit runs and replaces the supervisor: the job
        follows it instead of saying it failed."""
        real = world.installer.start_switch

        async def start_switch(unit, action, tag, backup, result):
            await real(unit, action, tag, backup, result)
            raise installer_module.InstallerError("systemd-run did not answer within 60 seconds")

        world.installer.start_switch = start_switch
        world.installer.outcome = None

        await api.post(f"{API}/install")
        await _until(lambda: world.installer.running, "the unit's start")
        await asyncio.sleep(0.05)  # the job asks whether the unit runs, which it does
        assert world.updater.get_progress().status == UpdateStatus.INSTALLING
        world.installer.end("success")

        progress = await _until_done(world)
        assert progress.status == UpdateStatus.COMPLETED, progress

    async def test_a_unit_that_ended_although_systemd_run_failed_is_recorded(self, world, api):
        real = world.installer.start_switch

        async def start_switch(unit, action, tag, backup, result):
            await real(unit, action, tag, backup, result)  # ends at once with its result
            raise installer_module.InstallerError("systemd-run did not answer within 60 seconds")

        world.installer.start_switch = start_switch

        await api.post(f"{API}/install")

        progress = await _until_done(world)
        assert progress.status == UpdateStatus.COMPLETED, progress

    async def test_a_unit_that_ended_without_a_result_fails_the_job(self, world, api):
        """The host restarted in the middle of the switch: the unit is gone, its result never came."""
        world.installer.outcome = None
        await api.post(f"{API}/install")
        await _until(lambda: world.installer.running, "the unit's start")

        world.installer.running.clear()
        progress = await _until_done(world)

        unit = world.installer.switches[-1].unit
        assert progress.status == UpdateStatus.FAILED
        assert progress.error == (f"The unit {unit} on the host ended without a result: see journalctl -u {unit} "
                                  "on the host")

    async def test_a_unit_that_runs_too_long_fails_the_job(self, world, api, monkeypatch):
        monkeypatch.setattr(updater_module, "FOLLOW_TIMEOUT_SECONDS", 0.05)
        world.installer.outcome = None

        await api.post(f"{API}/install")
        progress = await _until_done(world)

        assert progress.status == UpdateStatus.FAILED
        assert "did not end within 0.05 seconds" in progress.error

    async def test_the_wait_for_the_unit_writes_nothing(self, world, api, monkeypatch):
        """ADR-0214: the result file is read, the progress written only once the unit ended."""
        writes = []
        real = updater_module.state_files.write_json

        def counted(path, value, **kwargs):
            if Path(path) == updater_module.UPDATE_STATE_FILE:
                writes.append(value["status"])
            real(path, value, **kwargs)

        monkeypatch.setattr(updater_module.state_files, "write_json", counted)
        world.installer.outcome = None
        await api.post(f"{API}/install")
        await _until(lambda: world.installer.running, "the unit's start")
        await asyncio.sleep(0.2)  # about 20 reads of the result file

        assert writes == ["checking", "downloading", "backing_up", "installing"]
        world.installer.end("success")
        await _until_done(world)
        assert writes[-1] == "completed"

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

    async def test_while_the_unit_runs_a_second_install_is_refused(self, world, api):
        world.installer.outcome = None
        await api.post(f"{API}/install")
        await _until(lambda: world.installer.running, "the unit's start")

        response = await api.post(f"{API}/install")

        assert response.status_code == 409
        assert response.json()["detail"] == "Update already in progress: installing"
        world.installer.end("success")
        await _until_done(world)

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

        await api.post(f"{API}/install")
        await _until_done(world)

        assert writes == ["checking", "downloading", "backing_up", "installing", "completed"]


class TestTheJobPassesToTheNextSupervisor:
    """The switch replaces the supervisor that started it (ADR-0443): the progress file names the unit,
    and the supervisor that starts then follows it, the release or the version before."""

    async def test_the_handover_is_recorded_before_the_unit_starts(self, world, api):
        seen = []
        world.installer.start = None
        real = world.installer.start_switch

        async def start_switch(unit, action, tag, backup, result):
            seen.append(_stored(world))
            await real(unit, action, tag, backup, result)

        world.installer.start_switch = start_switch

        await api.post(f"{API}/install")
        await _until_done(world)

        [stored] = seen
        assert stored["status"] == "installing"
        handover = stored["handover"]
        assert UNIT.fullmatch(handover["unit"])
        assert handover["result"].endswith("/update.result")
        assert (handover["from_version"], handover["changelog"]) == (INSTALLED, "Fixes and features.")

    async def test_the_release_completes_the_update_it_finds_handed_over(self, world, api, monkeypatch):
        switch = await _handed_over(world, api)

        updater = await _next_supervisor(monkeypatch, RELEASE)

        progress = updater.get_progress()
        assert (progress.status, progress.message) == (UpdateStatus.VERIFYING, f"Waiting for {RELEASE} to answer")
        assert updater.running()
        world.installer.end("success")
        progress = await _until_done(world, updater=updater)
        assert progress.status == UpdateStatus.COMPLETED, progress
        assert (progress.message, progress.can_rollback, progress.handover) == (f"Updated to {RELEASE}", True, None)
        [entry] = await updater.get_update_history()
        assert (entry.from_version, entry.to_version, entry.status) == (INSTALLED, RELEASE, "success")
        assert entry.changelog == "Fixes and features."
        assert switch.result.read_text() == "success\n\n"

    async def test_the_version_before_records_the_rollback_the_unit_made(self, world, api, monkeypatch):
        await _handed_over(world, api)

        updater = await _next_supervisor(monkeypatch, INSTALLED)

        progress = updater.get_progress()
        assert (progress.status, progress.message) == (UpdateStatus.ROLLBACK, f"Going back to {INSTALLED}")
        world.installer.end("rolled-back", "UNOS did not answer within 300 seconds after the update.")
        progress = await _until_done(world, updater=updater)
        assert progress.status == UpdateStatus.FAILED
        assert progress.rolled_back is True
        assert progress.error == f"UNOS did not answer within 300 seconds after the update.; rolled back to {INSTALLED}"
        assert progress.can_rollback is False
        [entry] = await updater.get_update_history()
        assert (entry.status, entry.error) == ("failed", progress.error)

    def test_the_lifespan_follows_a_handed_over_job(self, world, monkeypatch, tmp_path):
        result = tmp_path / "update.result"
        result.write_text("success\n\n")
        updater_module.state_files.write_json(updater_module.UPDATE_STATE_FILE, {
            "status": "installing", "progress_percent": 50, "can_rollback": True, "action": "update",
            "version": RELEASE, "started_at": "2026-12-01T10:00:00+00:00",
            "handover": {"unit": "unos-update-20261201-100000", "result": str(result), "from_version": INSTALLED,
                         "changelog": None, "at": "2026-12-01T10:00:05+00:00"},
        }, sync=False)
        monkeypatch.setenv("UNOS_VERSION", RELEASE)
        monkeypatch.setattr(updater_module, "_updater", None)
        # No loops and no host commands at this start.
        monkeypatch.setenv("UNOS_DISABLE_BACKGROUND_TASKS", "1")

        with TestClient(app) as client:
            for _ in range(500):
                if updater_module.get_updater().get_progress().status == UpdateStatus.COMPLETED:
                    break
                client.portal.call(asyncio.sleep, 0.01)

        progress = updater_module.get_updater().get_progress()
        assert progress.status == UpdateStatus.COMPLETED, progress
        assert _stored(world)["handover"] is None


class TestRollback:
    async def test_it_hands_the_newest_backup_and_the_version_before_to_the_host(self, world, api):
        await api.post(f"{API}/install")
        await _until_done(world)
        [backup] = _backups(world)
        world.installer.calls.clear()

        response = await api.post(f"{API}/rollback")

        assert response.status_code == 200, response.text
        progress = await _until_done(world)
        assert progress.status == UpdateStatus.COMPLETED
        assert (progress.action, progress.version, progress.message) == ("rollback", INSTALLED,
                                                                          f"Rolled back to {INSTALLED}")
        # The images of the version before first, which the unit starts with the backup's database.
        assert world.installer.calls == [("fetch", INSTALLED), ("switch", "rollback", INSTALLED)]
        switch = world.installer.switches[-1]
        assert switch.unit.startswith("unos-rollback-")
        assert (switch.backup, switch.result) == (backup, backup / "rollback.result")
        assert progress.can_rollback is False
        assert os.environ["UNOS_VERSION"] == INSTALLED

    async def test_a_rollback_the_unit_undid_can_be_tried_again(self, world, api):
        await api.post(f"{API}/install")
        await _until_done(world)
        world.installer.outcome = ("rolled-back", "UNOS did not answer within 300 seconds after the update.")

        await api.post(f"{API}/rollback")
        progress = await _until_done(world)

        assert progress.status == UpdateStatus.FAILED
        assert progress.error == f"UNOS did not answer within 300 seconds after the update.; UNOS runs {RELEASE} again"
        assert progress.can_rollback is True
        assert (await api.get(f"{API}/progress")).json()["can_rollback"] is True

    async def test_a_rollback_handed_over_is_completed_by_the_version_before(self, world, api, monkeypatch):
        await api.post(f"{API}/install")
        await _until_done(world)
        await _handed_over(world, api, "rollback")

        updater = await _next_supervisor(monkeypatch, INSTALLED)
        world.installer.end("success")

        progress = await _until_done(world, updater=updater)
        assert (progress.status, progress.action, progress.can_rollback) == (UpdateStatus.COMPLETED, "rollback", False)

    async def test_without_a_backup_it_is_refused(self, world, api):
        response = await api.post(f"{API}/rollback")

        assert response.status_code == 400
        assert world.installer.calls == []

    @pytest.mark.parametrize("damage", ["gone", "garbage"])
    async def test_a_backup_that_is_gone_or_bad_fails_the_rollback_before_anything_changes(self, world, api,
                                                                                         damage):
        await api.post(f"{API}/install")
        await _until_done(world)
        [backup] = _backups(world)
        copy = backup / "unos.db"
        copy.unlink()
        if damage == "garbage":
            copy.write_bytes(b"SQLite format 3\x00" + bytes(200))
        world.installer.calls.clear()

        await api.post(f"{API}/rollback")
        progress = await _until_done(world)

        assert progress.status == UpdateStatus.FAILED
        assert "could not be put back" in progress.error or "has no unos.db" in progress.error
        assert world.installer.calls == []
        assert progress.can_rollback is True

    async def test_after_another_version_was_installed_no_rollback_is_offered(self, world, api, monkeypatch):
        """update.sh installed another version since: the newest backup is not of the version before it."""
        await api.post(f"{API}/install")
        await _until_done(world)
        monkeypatch.setenv("UNOS_VERSION", "v1.4.0")
        world.installer.calls.clear()

        assert (await api.get(f"{API}/progress")).json()["can_rollback"] is False
        response = await api.post(f"{API}/rollback")

        assert response.status_code == 400
        assert response.json()["detail"] == (f"The newest backup is of the update from {INSTALLED} to {RELEASE}, "
                                             "and UNOS runs v1.4.0: there is no backup of the version before it")
        assert world.installer.calls == []


class FakeHost:
    """src.host.run_on_host_async as the host answers it: each command is recorded, and answered from
    ``answers`` by its program (an exception is raised)."""

    def __init__(self):
        self.commands: list[list[str]] = []
        self.answers: dict[str, object] = {}

    async def __call__(self, cmd, *, timeout=60, check=False, input=None, errors=None):
        self.commands.append(list(cmd))
        answer = self.answers.get(cmd[0], host.HostResult(0, "", ""))
        if isinstance(answer, BaseException):
            raise answer
        return answer


@pytest.fixture
def on_host(monkeypatch):
    fake = FakeHost()
    monkeypatch.setattr(host, "run_on_host_async", fake)
    monkeypatch.delenv("UNOS_HOST_EXEC", raising=False)
    return fake


class TestTheHostsInstaller:
    """HostUpdate runs deploy/update.sh of the host's checkout through src/host.py (ADR-0020)."""

    SCRIPT = "/opt/unos/deploy/update.sh"

    async def test_the_images_come_from_update_sh_under_the_hosts_timeout(self, on_host):
        await installer_module.HostUpdate().fetch_release(RELEASE)

        assert on_host.commands == [["timeout", "--kill-after=30", "3600", "env", "UNOS_INSTALL_DIR=/opt/unos",
                                     self.SCRIPT, "--fetch", RELEASE]]

    async def test_a_fetch_that_fails_says_update_shs_first_error(self, on_host):
        on_host.answers["timeout"] = host.HostResult(
            1, "\x1b[0;34m[INFO]\x1b[0m Getting the images of v1.3.0...\n"
               "\x1b[0;31m[ERROR]\x1b[0m Could not pull ghcr.io/und3f1ned/unos-supervisor:1.3.0. Nothing was changed.\n",
            "Error response from daemon: manifest unknown\n")

        with pytest.raises(installer_module.InstallerError) as raised:
            await installer_module.HostUpdate().fetch_release(RELEASE)

        assert str(raised.value) == ("Getting the images of v1.3.0 failed: Could not pull "
                                     "ghcr.io/und3f1ned/unos-supervisor:1.3.0. Nothing was changed.")

    async def test_a_fetch_past_its_hour_is_named(self, on_host):
        on_host.answers["timeout"] = host.HostResult(124, "", "")

        with pytest.raises(installer_module.InstallerError, match="took longer than 3600 seconds"):
            await installer_module.HostUpdate().fetch_release(RELEASE)

    async def test_the_switch_runs_in_a_transient_unit_of_the_host(self, on_host, tmp_path):
        backup = tmp_path / "in-app-20261201-100000-000000"

        await installer_module.HostUpdate().start_switch("unos-update-20261201-100000", "update", RELEASE, backup,
                                                         backup / "update.result")

        assert on_host.commands == [[
            "systemd-run", "--unit=unos-update-20261201-100000", f"--description=UNOS: the in-app update to {RELEASE}",
            "--collect", "--quiet", "--property=KillMode=mixed", "--property=TimeoutStopSec=2400",
            "--setenv=HOME=/root", "--setenv=UNOS_INSTALL_DIR=/opt/unos",
            self.SCRIPT, "--in-app", "update", "--backup", str(backup), "--result", str(backup / "update.result"),
            RELEASE,
        ]]

    async def test_a_unit_that_does_not_start_says_why(self, on_host, tmp_path):
        on_host.answers["systemd-run"] = host.HostResult(1, "", "Failed to start transient service unit: Access denied\n")

        with pytest.raises(installer_module.InstallerError,
                           match="The unit unos-rollback-20261201-100000 did not start: Failed to start"):
            await installer_module.HostUpdate().start_switch("unos-rollback-20261201-100000", "rollback", INSTALLED,
                                                             tmp_path, tmp_path / "rollback.result")

    @pytest.mark.parametrize("tag", ["main", "v1.3", "v1.3.0-rc.1", "v1.3.0; rm -rf /", "--help"])
    async def test_only_a_release_tag_reaches_the_host(self, on_host, tmp_path, tag):
        with pytest.raises(installer_module.InstallerError, match="is not a release"):
            await installer_module.HostUpdate().fetch_release(tag)
        with pytest.raises(installer_module.InstallerError, match="is not a release"):
            await installer_module.HostUpdate().start_switch("unos-update-1", "update", tag, tmp_path, tmp_path / "r")

        assert on_host.commands == []

    @pytest.mark.parametrize("answer,running", [
        (host.HostResult(0, "active\n", ""), True),
        (host.HostResult(0, "deactivating\n", ""), True),
        # Ended: unloaded (--collect), or failed.
        (host.HostResult(0, "inactive\n", ""), False),
        (host.HostResult(0, "failed\n", ""), False),
        # Unknown: the job waits for its result or its deadline.
        (host.HostResult(1, "", "Failed to connect to bus"), True),
        (subprocess.TimeoutExpired(["systemctl"], 30), True),
    ])
    async def test_whether_the_unit_runs_is_asked_of_systemd(self, on_host, answer, running):
        on_host.answers["systemctl"] = answer

        assert await installer_module.HostUpdate().switch_running("unos-update-20261201-100000") is running
        assert on_host.commands == [["systemctl", "show", "--property=ActiveState", "--value",
                                     "unos-update-20261201-100000"]]


class TestWhereTheHostCannotInstall:
    """unavailable_reason of HostUpdate: the web UI installs only on a NAS that runs a release's images
    from GHCR and has the update script of install.sh (ADR-0443)."""

    @pytest.fixture
    def nas(self, monkeypatch, tmp_path):
        """A NAS on arm64 that runs the images of a release and has /opt/unos/deploy/update.sh."""
        root = tmp_path / "host-root"
        (root / "opt" / "unos" / "deploy").mkdir(parents=True)
        (root / "opt" / "unos" / "deploy" / "update.sh").write_text("#!/bin/bash\n")
        monkeypatch.setattr(installer_module, "HOST_ROOT", str(root))
        monkeypatch.delenv("UNOS_HOST_EXEC", raising=False)
        monkeypatch.setattr(installer_module.platform, "machine", lambda: "aarch64")
        monkeypatch.setenv("UNOS_VERSION", RELEASE)
        monkeypatch.setenv("UNOS_RELEASE", RELEASE)
        return SimpleNamespace(root=root, installer=installer_module.HostUpdate())

    def test_a_nas_of_a_release_can_install(self, nas):
        assert nas.installer.unavailable_reason() is None

    @pytest.mark.parametrize("machine", ["x86_64", "aarch64"])
    def test_amd64_and_arm64_have_release_images(self, nas, monkeypatch, machine):
        monkeypatch.setattr(installer_module.platform, "machine", lambda: machine)

        assert nas.installer.unavailable_reason() is None

    def test_outside_its_container_it_installs_nothing(self, nas, monkeypatch):
        monkeypatch.setenv("UNOS_HOST_EXEC", "local")

        assert nas.installer.unavailable_reason().startswith("This supervisor runs outside its container")

    def test_a_32_bit_userland_on_a_64_bit_kernel_has_no_release_images(self, nas, monkeypatch):
        monkeypatch.setattr(installer_module, "USERLAND_64_BIT", False)

        assert "not for this host's aarch64" in nas.installer.unavailable_reason()

    def test_the_update_command_names_the_install_folder(self, nas, monkeypatch):
        monkeypatch.setattr(installer_module.platform, "machine", lambda: "riscv64")

        assert "sudo /srv/unos/deploy/update.sh" in installer_module.HostUpdate("/srv/unos").unavailable_reason()

    def test_a_riscv64_host_has_no_release_images(self, nas, monkeypatch):
        monkeypatch.setattr(installer_module.platform, "machine", lambda: "riscv64")

        reason = nas.installer.unavailable_reason()
        assert "not for this host's riscv64" in reason and "sudo /opt/unos/deploy/update.sh" in reason

    @pytest.mark.parametrize("version", ["v1.3.0-3-g712fc3a", "712fc3a-dirty", "unknown", ""])
    def test_a_version_that_is_not_a_release_tag_says_so(self, nas, monkeypatch, version):
        monkeypatch.setenv("UNOS_VERSION", version)

        assert nas.installer.unavailable_reason().startswith(f"UNOS runs {version or 'unknown'}, which is not a release")

    @pytest.mark.parametrize("release", [None, "", "v1.2.0"])
    def test_an_image_built_here_names_the_dev_device(self, nas, monkeypatch, release):
        """scripts/dev/deploy.sh builds the images on the dev device: UNOS_RELEASE is empty there, also
        when the tree it built is at a release tag."""
        if release is None:
            monkeypatch.delenv("UNOS_RELEASE")
        else:
            monkeypatch.setenv("UNOS_RELEASE", release)

        reason = nas.installer.unavailable_reason()
        assert reason.startswith("The supervisor's image was built on this host")
        assert "a dev device deployed by scripts/dev/deploy.sh" in reason

    def test_a_dev_device_after_a_release_names_both(self, nas, monkeypatch):
        """The usual dev device: deploy.sh builds the workstation's tree, commits after a tag."""
        monkeypatch.setenv("UNOS_VERSION", "v1.3.0-3-g712fc3a-dirty")
        monkeypatch.setenv("UNOS_RELEASE", "")

        reason = nas.installer.unavailable_reason()
        assert reason.startswith("UNOS runs v1.3.0-3-g712fc3a-dirty, which is not a release (a tag v<x.y.z>), and "
                                 "the supervisor's image was built on this host")
        assert "a dev device deployed by scripts/dev/deploy.sh" in reason

    def test_a_host_without_the_install_folder_says_so(self, nas):
        (nas.root / "opt" / "unos" / "deploy" / "update.sh").unlink()

        reason = nas.installer.unavailable_reason()
        assert reason.startswith("/opt/unos/deploy/update.sh is not on this host (UNOS_INSTALL_DIR)")

    def test_the_install_folder_is_a_setting(self, nas, tmp_path):
        (nas.root / "srv" / "unos" / "deploy").mkdir(parents=True)
        (nas.root / "srv" / "unos" / "deploy" / "update.sh").write_text("#!/bin/bash\n")
        (nas.root / "opt" / "unos" / "deploy" / "update.sh").unlink()

        assert installer_module.HostUpdate("/srv/unos").unavailable_reason() is None
        assert type(updater_module.get_settings()).model_fields["install_dir"].default == "/opt/unos"

    async def test_install_rollback_and_the_automatic_install_are_refused_with_the_reason(self, world, api,
                                                                                         monkeypatch):
        monkeypatch.setattr(installer_module, "current", installer_module.HostUpdate())
        monkeypatch.setenv("UNOS_HOST_EXEC", "local")

        progress = (await api.get(f"{API}/progress")).json()
        reason = progress["install_unavailable"]
        assert reason.startswith("This supervisor runs outside its container")

        for response in (await api.post(f"{API}/install"), await api.post(f"{API}/rollback"),
                         await api.put(f"{API}/config", json={"auto_install": True, "check_interval_hours": 24})):
            assert response.status_code == 409, response.text
            assert response.json()["detail"] == reason
        assert not world.backups.exists()
        assert not updater_module.UPDATE_CONFIG_FILE.exists()

    async def test_an_installer_that_can_install_answers_none(self, world, api):
        assert (await api.get(f"{API}/progress")).json()["install_unavailable"] is None

    def test_the_supervisors_installer_is_the_hosts(self):
        assert type(importlib.import_module("src.updates.installer").current).__name__ == "HostUpdate"


class TestAStopInTheMiddle:
    """A job that a stop interrupted before it handed its switch over has ended, failed (ADR-0443)."""

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
        monkeypatch.setattr(installer_module, "current", installer_module.HostUpdate())
        monkeypatch.setenv("UNOS_HOST_EXEC", "local")

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
