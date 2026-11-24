"""The way back from the SD card standby with the card's changes (system.sd-standby-failback).

The pool return of the critical apps runs on the fake BPI-W3 of ``tests/standby_fake.py`` with the test
pool ``usb`` and Jellyfin of ``tests/test_standby_apps.py``, Docker the fake client of
``tests/docker_fake.py``: its dry run, its order (stopped before the copy, the copy without the caches
and update copies, every replaced or deleted file kept in the backup folder, the pool-bound containers
again, the run copy deleted last), a failure at each step and the repeat, its refusals, the discarding
return as the other choice, and the planned repair's return.
"""

from __future__ import annotations

import json

import pytest
from docker.errors import APIError

from src import host
from src.apps.stacks import APP_POOL_LABEL
from src.health import problems
from src.standby import apps as standby_apps
from src.standby import failback, service
from src.standby.hostops import CARD_DIR
from tests.test_standby import Client, audit_log
from tests.test_standby_apps import FOLDER, POOL, binds, mark, pool_info, run, stored, synced
from tests import test_standby, test_standby_apps

# The fixtures of the standby's tests and of the critical apps'.
fake = test_standby.fake
alerts = test_standby.alerts
db = test_standby_apps.db
docker = test_standby_apps.docker
pools = test_standby_apps.pools
awake = test_standby_apps.awake
standby = test_standby_apps.standby
RUN = f"{CARD_DIR}/@app-jellyfin-run"
MiB = 1024 * 1024


def moved_to_the_card(standby, reason: str = "drill") -> None:
    """Jellyfin marked, synced and moved to the card; then it changes there: its settings rewritten, its
    library deleted and a new file of 2 MiB."""
    mark()
    synced(standby)
    run(standby_apps.move(["jellyfin"], reason, request=None, username="admin"))
    standby.files[f"{RUN}/data/config/system.xml"] = "<card/>"
    del standby.files[f"{RUN}/data/config/data/library.db"]
    standby.files[f"{RUN}/data/config/new.xml"] = "card only"
    standby.file_sizes[f"{RUN}/data/config/new.xml"] = 2 * MiB
    standby.changes.clear()


def backups(standby) -> dict[str, str]:
    """The files of the backup folders, by their path below the folder."""
    found = {}
    for name, text in standby.files.items():
        if name.startswith(FOLDER + failback.BACKUP_SUFFIX):
            folder, _, relative = name[len(POOL) + len("/apps/"):].partition("/")
            found[relative] = text
            assert folder.startswith("jellyfin.before-failback-")
    return found


class TestThePoolReturn:
    def test_the_dry_run_answers_the_mib_the_copy_writes_and_changes_nothing(self, standby, docker):
        moved_to_the_card(standby)
        with Client() as api:
            answer = api.post("/api/v1/system/standby/apps/return", json={"dry_run": True})
        assert answer.status_code == 200, answer.text
        # The new file and the rewritten one; the deleted one writes nothing.
        assert answer.json()["apps"][0]["mib"] == 2.0
        assert standby.changes == []
        assert binds(docker, "jellyfin")["/config"][0] == f"{RUN}/data/config"
        assert audit_log()[-1]["action"] == "standby_apps_moved"

    def test_copies_the_card_s_changes_back_keeping_what_it_replaces_then_runs_from_the_pool(
            self, standby, docker, alerts, monkeypatch):
        moved_to_the_card(standby)
        # The pool's own change after the last sync, which the copy replaces: kept in the backup folder.
        standby.files[f"{FOLDER}/config/system.xml"] = "<pool/>"
        seen = []
        original = host.run_on_host

        def watched(cmd, **kwargs):
            if "--backup" in cmd:
                seen.append(docker.containers_["jellyfin"].attrs["State"]["Running"])
            return original(cmd, **kwargs)

        monkeypatch.setattr(host, "run_on_host", watched)
        with Client() as api:
            answer = api.post("/api/v1/system/standby/apps/return", json={})
        assert answer.status_code == 200, answer.text
        assert seen == [False]  # stopped before the copy
        item = answer.json()["apps"][0]
        assert item["mib"] == 2.0 and item["backup"].startswith(f"{FOLDER}.before-failback-")
        # The card's changes are on the pool; what they replaced or deleted is in the backup folder.
        assert standby.files[f"{FOLDER}/config/system.xml"] == "<card/>"
        assert standby.files[f"{FOLDER}/config/new.xml"] == "card only"
        assert f"{FOLDER}/config/data/library.db" not in standby.files
        assert backups(standby) == {"config/system.xml": "<pool/>", "config/data/library.db": "rows"}
        # What the sync leaves out is neither copied nor deleted.
        assert standby.files[f"{FOLDER}/cache/images/a.jpg"] == "thumbnail"
        assert standby.files[f"{FOLDER}/config.previous/system.xml"] == "<old/>"
        copy = next(cmd for cmd in standby.unit_calls if "--backup" in cmd)
        assert "--exclude=/cache" in copy and "--exclude=*.previous" in copy and "--delete" in copy
        # The pool-bound containers run again; the run copy and the move are gone.
        assert binds(docker, "jellyfin")["/config"] == (f"{FOLDER}/config", False)
        container = docker.containers_["jellyfin"].attrs
        assert container["Config"]["Labels"][APP_POOL_LABEL] == "usb" and container["State"]["Running"] is True
        assert f"{RUN}" not in standby.subvolume_paths and not any(name.startswith(RUN + "/") for name in standby.files)
        assert "failed_over" not in stored()["apps"]["jellyfin"]
        assert not [key for key in problems.of_source("standby") if key.startswith("apps_on_standby")]
        entry = [e for e in audit_log() if e["action"] == "standby_apps_returned"][-1]
        assert entry["username"] == "admin" and entry["success"] is True
        assert "with the SD standby's changes (2.0 MiB)" in entry["details"] and item["backup"] in entry["details"]
        assert [(alert.type, alert.title) for alert in alerts][-1] == ("success", "Critical apps are back on their pool")

    def test_a_failed_copy_leaves_the_app_stopped_and_the_card_s_copy_untouched_and_runs_again(
            self, standby, docker, alerts):
        moved_to_the_card(standby)
        standby.copy_status["back:jellyfin"] = 11
        with Client() as api:
            answer = api.post("/api/v1/system/standby/apps/return", json={})
            status = api.get("/api/v1/system/standby").json()
        assert answer.status_code == 500
        assert "The return of jellyfin failed at its step copy" in answer.json()["detail"]
        assert "No space left on device" in answer.json()["detail"]
        assert docker.containers_["jellyfin"].attrs["State"]["Running"] is False
        assert binds(docker, "jellyfin")["/config"][0] == f"{RUN}/data/config"
        assert standby.files[f"{RUN}/data/config/new.xml"] == "card only" and RUN in standby.subvolume_paths
        failure = status["apps"][0]["moved"]["return_failed"]
        assert failure["step"] == "copy" and "No space left on device" in failure["error"]
        assert status["apps"][0]["return_blocked"] is None  # offered again
        entry = [e for e in audit_log() if e["action"] == "standby_apps_returned"][-1]
        assert entry["success"] is False and "step copy" in entry["details"]
        assert alerts[-1].type == "error"
        backup = failure["backup"]
        # Again, once the cause is gone: the same backup folder.
        standby.copy_status.clear()
        with Client() as api:
            again = api.post("/api/v1/system/standby/apps/return", json={})
        assert again.status_code == 200, again.text
        assert again.json()["apps"][0]["backup"] == backup
        assert standby.files[f"{FOLDER}/config/new.xml"] == "card only"
        assert docker.containers_["jellyfin"].attrs["State"]["Running"] is True
        assert "failed_over" not in stored()["apps"]["jellyfin"]

    def test_a_failure_to_create_the_containers_leaves_the_app_stopped_and_runs_again(self, standby, docker):
        moved_to_the_card(standby)
        docker.fail("api.create_container_from_config", "jellyfin", APIError("no such image"))
        with Client() as api:
            answer = api.post("/api/v1/system/standby/apps/return", json={})
        assert answer.status_code == 500 and "failed at its step containers" in answer.json()["detail"]
        assert not any(c.attrs["State"]["Running"] for c in docker.containers_.values())
        assert stored()["apps"]["jellyfin"]["failed_over"]["returning"]["step"] == "containers"
        # The card's copy stays until the return is done.
        assert standby.files[f"{RUN}/data/config/new.xml"] == "card only"
        docker.clear()
        with Client() as api:
            again = api.post("/api/v1/system/standby/apps/return", json={})
        assert again.status_code == 200, again.text
        assert binds(docker, "jellyfin")["/config"] == (f"{FOLDER}/config", False)
        assert docker.containers_["jellyfin"].attrs["State"]["Running"] is True
        # The first run copied every file: the second replaced none of them again.
        assert backups(standby) == {"config/system.xml": "<config/>", "config/data/library.db": "rows"}

    def test_a_failure_to_forget_the_move_stops_the_app_again(self, standby, docker, monkeypatch):
        moved_to_the_card(standby)
        original = failback._note

        def note(app, returning):
            if returning is None:
                raise failback.StandbyFailed("The standby's state.json: an I/O error")
            original(app, returning)

        monkeypatch.setattr(failback, "_note", note)
        with Client() as api:
            answer = api.post("/api/v1/system/standby/apps/return", json={})
        assert answer.status_code == 500 and "failed at its step record" in answer.json()["detail"]
        assert docker.containers_["jellyfin"].attrs["State"]["Running"] is False
        assert RUN in standby.subvolume_paths

    @pytest.mark.parametrize("pool", [
        dict(mounted=False, health="unmounted", mountpoint=None), dict(read_only=True),
        dict(raid_level="raid1", missing_devices=1, health="degraded")])
    def test_a_pool_that_cannot_take_the_apps_back_changes_nothing(self, standby, docker, pools, pool):
        moved_to_the_card(standby)
        pools["usb"] = pool_info(**pool)
        with Client() as api:
            answer = api.post("/api/v1/system/standby/apps/return", json={"apps": ["jellyfin"]})
        assert answer.status_code == 409
        assert standby.changes == [] and docker.containers_["jellyfin"].attrs["State"]["Running"] is True

    def test_a_missing_folder_outside_the_copy_changes_nothing(self, standby, docker):
        moved_to_the_card(standby)
        del standby.files[f"{POOL}/media/film.mkv"]
        standby.dirs.discard(f"{POOL}/media")
        with Client() as api:
            answer = api.post("/api/v1/system/standby/apps/return", json={})
        assert answer.status_code == 409 and f"{POOL}/media is not there" in answer.json()["detail"]
        assert standby.changes == [] and docker.containers_["jellyfin"].attrs["State"]["Running"] is True

    def test_the_app_s_own_folder_need_not_be_on_a_pool_made_anew(self, standby, docker):
        moved_to_the_card(standby)
        for name in [name for name in standby.files if name.startswith(FOLDER + "/") and "/cache/" not in name]:
            del standby.files[name]
        with Client() as api:
            answer = api.post("/api/v1/system/standby/apps/return", json={})
        assert answer.status_code == 200, answer.text
        assert standby.files[f"{FOLDER}/config/system.xml"] == "<card/>"

    def test_waits_for_a_sync(self, standby):
        moved_to_the_card(standby)
        service._state.syncing = True
        with Client() as api:
            answer = api.post("/api/v1/system/standby/apps/return", json={})
        assert answer.status_code == 409 and "A sync runs" in answer.json()["detail"]
        assert standby.changes == []

    def test_an_app_without_a_copy_starts_again_as_it_was(self, standby, docker):
        mark()
        run(standby_apps.move(["jellyfin"], "drill", request=None, username="admin"))
        assert docker.containers_["jellyfin"].attrs["State"]["Running"] is False
        with Client() as api:
            answer = api.post("/api/v1/system/standby/apps/return", json={})
        assert answer.status_code == 200, answer.text
        assert answer.json()["apps"][0]["mib"] is None
        assert docker.containers_["jellyfin"].attrs["State"]["Running"] is True
        assert not [cmd for cmd in standby.unit_calls if cmd[0] == "rsync"]

    def test_the_discarding_return_stays_the_other_choice(self, standby, docker):
        moved_to_the_card(standby)
        with Client() as api:
            untyped = api.post("/api/v1/system/standby/apps/return", json={"discard": True})
            back = api.post("/api/v1/system/standby/apps/return", json={"discard": True, "model": "SC32G"})
        assert untyped.status_code == 409
        assert back.status_code == 200, back.text
        assert standby.files[f"{FOLDER}/config/system.xml"] == "<config/>"
        assert f"{FOLDER}/config/new.xml" not in standby.files and backups(standby) == {}
        assert RUN not in standby.subvolume_paths

    def test_for_administrators_only(self, standby):
        moved_to_the_card(standby)
        with Client(is_admin=False) as api:
            answer = api.post("/api/v1/system/standby/apps/return", json={})
        assert answer.status_code == 403
        assert standby.changes == []


class TestThePlannedRepairsReturn:
    def test_the_change_made_on_the_card_while_the_pool_was_unmounted_is_on_the_pool_after_the_return(
            self, standby, docker, pools):
        mark()
        synced(standby)
        with Client() as api:
            moved = api.post("/api/v1/system/standby/apps/move", json={"reason": "planned", "pool": "usb"})
        assert moved.status_code == 200, moved.text
        # The pool is unmounted for its repair: no return yet.
        pools["usb"] = pool_info(mounted=False, health="unmounted", mountpoint=None)
        standby.files[f"{RUN}/data/config/system.xml"] = "<made while the pool was away/>"
        with Client() as api:
            status = api.get("/api/v1/system/standby").json()
            early = api.post("/api/v1/system/standby/apps/return", json={})
        assert status["apps"][0]["return_blocked"] == "the pool usb is not mounted"
        assert early.status_code == 409
        # Mounted again under its name, read-write and healthy: the same pool return.
        pools["usb"] = pool_info()
        with Client() as api:
            status = api.get("/api/v1/system/standby").json()
            back = api.post("/api/v1/system/standby/apps/return", json={})
        assert status["apps"][0]["return_blocked"] is None and status["apps"][0]["moved"]["reason"] == "planned"
        assert back.status_code == 200, back.text
        assert standby.files[f"{FOLDER}/config/system.xml"] == "<made while the pool was away/>"
        assert backups(standby)["config/system.xml"] == "<config/>"
        assert json.loads(standby.files[f"{CARD_DIR}/state.json"])["apps"]["jellyfin"].get("failed_over") is None
