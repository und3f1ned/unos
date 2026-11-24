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
from src.standby import failback, generation, service
from src.standby.hostops import BOOT_DIR, CARD_DIR
from tests.standby_fake import CARD_UUID, ROOT_UUID, mountinfo_line
from tests.test_standby import STATE, Client, armed, audit_log
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


# --- the return to the eMMC -------------------------------------------------------------------------------


EMMC_OWN = f"UUID={ROOT_UUID} / ext4 defaults,noatime,commit=600,errors=remount-ro 0 1\n"


def on_the_card(fake, *, generation_number: int = 1) -> None:
    """The host runs from the card's root of generation ``generation_number``: its fstab (the card's), the
    eMMC's own lines kept at the sync, the run recorded."""
    armed(fake, generation_number=generation_number)
    fake.on_card_root = True
    fake.files["/proc/cmdline"] = (f"root=UUID={CARD_UUID} rootflags=subvolid=256 unos.chooser=card unos.root=card "
                                   "unos.reason=marked")
    fake.files[generation.MARK] = "reason=drill\ntime=2026-11-24T10:00:00Z\n"
    card_fstab, own = generation.card_fstab(fake.files["/etc/fstab"], CARD_UUID, {f"UUID={ROOT_UUID}"})
    fake.files["/etc/fstab"] = card_fstab
    assert own == EMMC_OWN
    fake.files[f"{generation.LIB}/emmc-fstab"] = own
    fake.files[STATE] = json.dumps({**json.loads(fake.files[STATE]), "standby_ran": {
        "at": "2026-11-24T10:02:00Z", "generation": generation_number, "reason": "drill"}})
    service.refresh()


def fsck_calls(fake) -> list[list[str]]:
    return [cmd[cmd.index("fsck.ext4"):] for cmd in fake.calls if "fsck.ext4" in cmd]


class TestTheEmmcCheck:
    def test_the_return_is_offered_when_the_emmc_s_root_passes_fsck(self, fake):
        on_the_card(fake)
        run(service.check_emmc())
        with Client() as api:
            status = api.get("/api/v1/system/standby").json()
        found = status["emmc_return"]
        assert found["offered"] is True and found["device"] == "/dev/mmcblk0p1"
        assert "passes fsck.ext4 -n" in found["detail"] and found["checked_at"]
        assert fsck_calls(fake) == [["fsck.ext4", "-n", "/dev/mmcblk0p1"]]
        assert fake.changes == []

    @pytest.mark.parametrize("broken, why", [
        ("fsck", "does not pass fsck.ext4 -n (exit status 4): /dev/mmcblk0p1: UNEXPECTED INCONSISTENCY"),
        ("gone", f"The eMMC's root UUID={ROOT_UUID} is not found: the eMMC does not read"),
        ("btrfs", "The eMMC's root is btrfs, not ext4"),
        ("no lines", "The card's copy holds no fstab line of the eMMC's root"),
        ("mounted", "The eMMC's root /dev/mmcblk0p1 is mounted at /media/emmc"),
    ])
    def test_otherwise_the_card_stays_the_system_disk(self, fake, monkeypatch, broken, why):
        monkeypatch.setattr(service, "REBOOT_DELAY_SECONDS", 0)
        on_the_card(fake)
        if broken == "fsck":
            fake.fsck["-n"] = (4, "/dev/mmcblk0p1: Inode 12 has an invalid extent\n\n"
                                  "/dev/mmcblk0p1: UNEXPECTED INCONSISTENCY; RUN fsck MANUALLY.\n")
        elif broken == "gone":
            fake.tagged.clear()
        elif broken == "btrfs":
            fake.files[f"{generation.LIB}/emmc-fstab"] = EMMC_OWN.replace(" ext4 ", " btrfs ")
        elif broken == "no lines":
            del fake.files[f"{generation.LIB}/emmc-fstab"]
        else:
            fake.extra_mounts.append(mountinfo_line(50, "/media/emmc", "ext4", "/dev/mmcblk0p1"))
        run(service.check_emmc())
        with Client() as api:
            status = api.get("/api/v1/system/standby").json()
            refused = [api.post("/api/v1/system/standby/return", json=body) for body in ({}, {"discard": True})]
        assert status["emmc_return"]["offered"] is False and why in status["emmc_return"]["detail"]
        for answer in refused:
            assert answer.status_code == 409, answer.text
            assert why in answer.json()["detail"]
            assert "The card stays the system disk, and no standby is left" in answer.json()["detail"]
        assert fake.changes == [] and fake.reboots == 0
        assert generation.MARK in fake.files

    def test_on_the_emmc_there_is_no_check(self, fake):
        armed(fake)
        with Client() as api:
            status = api.get("/api/v1/system/standby").json()
        assert status["emmc_return"] is None and fsck_calls(fake) == []

    def test_the_standby_s_start_checks_the_emmc(self, fake):
        on_the_card(fake)
        run(service.on_card_start())
        assert fsck_calls(fake) == [["fsck.ext4", "-n", "/dev/mmcblk0p1"]]
        assert service._state.emmc_check["ok"] is True


# --- the return with the card's changes: the supervisor's part --------------------------------------------


RUN_DIR = "/run/unos-standby-return"
CARD_VOLUMES = {f"deploy_{name}": f"/var/lib/docker/volumes/deploy_{name}/_data"
                for name in ("unos-data", "served-certificate", "app-names", "caddy-data", "caddy-config")}
EMMC_VOLUMES = {name.removeprefix("deploy_"): f"/mnt/pools/data/.docker/volumes/{name}/_data" for name in CARD_VOLUMES}


@pytest.fixture
def data_pool(monkeypatch):
    """The data pool, which holds the eMMC's Docker data (ADR-0486), mounted and healthy on the card's root."""
    from src.storage import pools as storage_pools
    from src.storage.schemas import PoolInfo

    found = {"data": PoolInfo(name="data", devices=["/dev/sda"], total_size=4 * 1024**4, used_size=1024**4,
                              mountpoint="/mnt/pools/data", raid_level="single", mounted=True, persistent=True,
                              health="healthy")}

    def get_pool(name):
        if name not in found:
            raise storage_pools.PoolNotFoundError(name)
        return found[name]

    monkeypatch.setattr(storage_pools, "get_pool", get_pool)
    return found


def ready_to_return(fake) -> None:
    """On the card's root, whose generation kept where the eMMC's Docker keeps UNOS's volumes, the eMMC checked."""
    on_the_card(fake)
    fake.volumes = dict(CARD_VOLUMES)
    fake.files[f"{generation.LIB}/emmc-volumes"] = json.dumps(EMMC_VOLUMES)
    fake.files[STATE] = json.dumps({**json.loads(fake.files[STATE]), "apps": {"jellyfin": {
        "generation": 2, "pool": "usb", "failed_over": {"reason": "pool_failed", "pool": "usb", "copy": True}}}},
        indent=1, sort_keys=True) + "\n"
    service.refresh()


class TestTheEmmcReturnsUnit:
    def test_the_sync_keeps_where_the_emmc_s_docker_keeps_unos_s_volumes(self, fake):
        armed(fake, generation_number=None)
        generation.sync(force=True)
        kept = json.loads(fake.files[f"{CARD_DIR}/@sys-1{generation.LIB}/emmc-volumes"])
        assert kept == {name.removeprefix("deploy_"): path for name, path in fake.volumes.items()}

    def test_the_emmc_s_fstab_is_the_standby_s_with_the_emmc_s_own_lines_for_the_card_s_root(self):
        from src.standby.emmc_return import emmc_fstab

        card = (f"UUID={CARD_UUID} / btrfs defaults,noatime,compress=zstd:1 0 0\n"
                "tmpfs /tmp tmpfs defaults,nosuid 0 0\n# unos-pool:usb (managed by UNOS)\n"
                "UUID=0d1e2f30-0000-4000-8000-000000000001 /mnt/pools/usb btrfs defaults,noatime,nofail 0 0\n")
        own = f"{EMMC_OWN}UUID=6A1B-0001 /boot vfat defaults 0 2\n"
        assert emmc_fstab(card, own) == (
            f"{own}tmpfs /tmp tmpfs defaults,nosuid 0 0\n# unos-pool:usb (managed by UNOS)\n"
            "UUID=0d1e2f30-0000-4000-8000-000000000001 /mnt/pools/usb btrfs defaults,noatime,nofail 0 0\n")
        from src.standby.hostops import StandbyRefused

        with pytest.raises(StandbyRefused):
            emmc_fstab("tmpfs /tmp tmpfs defaults 0 0\n", own)
        with pytest.raises(StandbyRefused):
            emmc_fstab(card, "UUID=6A1B-0001 /boot vfat defaults 0 2\n")

    def test_the_dry_run_answers_what_the_unit_runs_and_changes_nothing(self, fake, data_pool):
        ready_to_return(fake)
        with Client() as api:
            answer = api.post("/api/v1/system/standby/return", json={"dry_run": True})
        assert answer.status_code == 200, answer.text
        plan = answer.json()["plan"]
        assert plan[0] == "fsck.ext4 -n /dev/mmcblk0p1" and plan[-1] == "systemctl reboot"
        assert any(line.startswith("rsync -aHAX --numeric-ids --one-file-system --delete") for line in plan)
        assert any("--backup-dir=/mnt/pools/data/.docker/volumes.before-failback-" in line
                   and line.endswith("/mnt/pools/data/.docker/volumes/deploy_unos-data/_data/") for line in plan)
        assert fake.changes == [] and audit_log() == []

    def test_stages_its_files_in_ram_starts_the_unit_and_writes_nothing_else(self, fake, data_pool):
        ready_to_return(fake)
        before = fake.files[STATE]
        with Client() as api:
            answer = api.post("/api/v1/system/standby/return", json={})
        assert answer.status_code == 200, answer.text
        assert "the NAS reboots to it" in answer.json()["message"]
        # What the supervisor changed: the unit's folder in RAM and the unit; the card's files stay as they are.
        assert all(cmd[0] in ("rm", "mkdir", "write", "systemd-run") for cmd in fake.changes)
        assert all(part.startswith(RUN_DIR) for cmd in fake.changes if cmd[0] == "write" for part in cmd[1:])
        assert fake.files[STATE] == before and generation.MARK in fake.files
        unit = fake.changes[-1]
        assert unit[:2] == ["systemd-run", "--unit=unos-standby-return"]
        assert unit[-3:] == ["/bin/sh", f"{RUN_DIR}/unos-standby-return.sh", RUN_DIR]
        files = {name[len(RUN_DIR) + 1:]: text for name, text in fake.files.items() if name.startswith(RUN_DIR + "/")}
        from tests.test_standby import HOST_SCRIPTS

        assert files["unos-standby-return.sh"] == (HOST_SCRIPTS / "unos-standby-return.sh").read_text()
        settings = dict(line.split("=", 1) for line in files["return.env"].splitlines())
        assert settings["EMMC"] == "/dev/mmcblk0p1" and settings["CARD"] == "/dev/mmcblk1"
        assert settings["SECTOR"] == "32767" and settings["STATE"] == STATE
        import hashlib

        assert settings["STATE_SHA"] == hashlib.sha256(before.encode()).hexdigest()
        assert settings["UNITS"] == "'docker.socket docker.service smbd.service nmbd.service'"
        excludes = files["excludes"].splitlines()
        for path in ("/var/lib/docker", "/etc/fstab", generation.LIB, "/usr/local/lib/unos-standby",
                     "/etc/docker/daemon.json", "/etc/containerd/config.toml", "/etc/systemd/system/unos.service",
                     "/proc", "/mnt/pools/data", "/mnt/.unos-standby/card", "/var/log*"):
            assert path in excludes, path
        assert files["fstab"].startswith(EMMC_OWN) and f"UUID={CARD_UUID} / " not in files["fstab"]
        assert "/mnt/.unos-standby/card" in files["fstab"] and "/mnt/pools/data" in files["fstab"]
        after = json.loads(files["state.json"])
        assert "standby_ran" not in after and after["emmc_return"]["by"] == "admin"
        # An app that runs from the card stays there until its pool's return.
        assert after["apps"]["jellyfin"]["failed_over"]["pool"] == "usb"
        volumes = [line.split("\t") for line in files["volumes"].splitlines()]
        assert ["/var/lib/docker/volumes/deploy_unos-data/_data", "/mnt/pools/data/.docker/volumes/deploy_unos-data/_data"
                ] in [row[:2] for row in volumes]
        assert all(row[2].startswith("/mnt/pools/data/.docker/volumes.before-failback-") for row in volumes)
        assert files["counter"] == "unos_tries=0\n"
        entry = audit_log()[-1]
        assert entry["action"] == "standby_returned" and "with the card's changes" in entry["details"]

    def test_the_emmc_s_other_filesystems_are_left_out_of_the_copy(self, fake, data_pool):
        ready_to_return(fake)
        fake.files[f"{generation.LIB}/emmc-fstab"] = (f"{EMMC_OWN}UUID=0b0e0f00-0000-4000-8000-000000000002 /boot ext4 "
                                                      "defaults 0 2\n/dev/mmcblk2p4 none swap sw 0 0\n")
        with Client() as api:
            assert api.post("/api/v1/system/standby/return", json={}).status_code == 200
        excludes = fake.files[f"{RUN_DIR}/excludes"].splitlines()
        assert "/boot" in excludes and "none" not in excludes
        assert "UUID=0b0e0f00-0000-4000-8000-000000000002 /boot ext4" in fake.files[f"{RUN_DIR}/fstab"]

    def test_volumes_of_the_emmc_s_docker_on_the_emmc_go_below_its_mount_without_a_backup(self, fake, data_pool):
        ready_to_return(fake)
        fake.files[f"{generation.LIB}/emmc-volumes"] = json.dumps(
            {name: path.replace("/mnt/pools/data/.docker", "/var/lib/docker") for name, path in EMMC_VOLUMES.items()})
        with Client() as api:
            assert api.post("/api/v1/system/standby/return", json={}).status_code == 200
        rows = [line.split("\t") for line in fake.files[f"{RUN_DIR}/volumes"].splitlines()]
        assert ["/var/lib/docker/volumes/deploy_unos-data/_data",
                f"{RUN_DIR}/emmc/var/lib/docker/volumes/deploy_unos-data/_data", ""] in rows

    @pytest.mark.parametrize("broken, why", [
        ("pool", "UNOS's volumes go back to the eMMC's Docker on the pool data, but the pool data is not mounted"),
        ("no volumes", "The card's copy does not say where the eMMC's Docker keeps UNOS's volumes"),
        ("a volume", "The eMMC's Docker has no volume caddy-data"),
    ])
    def test_a_copy_that_cannot_go_is_refused_and_changes_nothing(self, fake, data_pool, broken, why):
        from src.storage.schemas import PoolInfo

        ready_to_return(fake)
        if broken == "pool":
            data_pool["data"] = PoolInfo(name="data", devices=[], total_size=0, used_size=0, mountpoint=None,
                                         raid_level="single", mounted=False, persistent=True, health="unmounted")
        elif broken == "no volumes":
            del fake.files[f"{generation.LIB}/emmc-volumes"]
        else:
            fake.files[f"{generation.LIB}/emmc-volumes"] = json.dumps(
                {name: path for name, path in EMMC_VOLUMES.items() if name != "caddy-data"})
        with Client() as api:
            answer = api.post("/api/v1/system/standby/return", json={})
        assert answer.status_code == 409 and why in answer.json()["detail"]
        assert fake.changes == []

    def test_a_failed_return_is_recorded_audited_and_alerted_at_the_next_start(self, fake, alerts):
        on_the_card(fake)
        fake.files[f"{RUN_DIR}/result"] = ("failed\ncopy\nrsync: write failed: No space left on device (28); Docker and "
                                          "Samba run again\n")
        stored = json.loads(fake.files[STATE])
        fake.files[STATE] = json.dumps({**stored, "emmc_return": {"at": "x", "generation": 1, "by": "admin"}})
        service.refresh()
        run(service.on_card_start())
        with Client() as api:
            status = api.get("/api/v1/system/standby").json()
        failed = status["emmc_return"]["failed"]
        assert failed["step"] == "copy" and "No space left on device" in failed["message"]
        assert status["emmc_return"]["offered"] is True  # it can run again
        entry = audit_log()[-1]
        assert (entry["action"], entry["username"], entry["success"]) == ("standby_returned", "system", False)
        assert [(alert.type, alert.title) for alert in alerts] == [("error", "The return to the eMMC failed")]
        assert ["rm", "-rf", "--", RUN_DIR] in fake.changes
        # A record of a return that did not end goes on the card.
        assert "emmc_return" not in json.loads(fake.files[STATE])

    def test_on_the_emmc_the_return_is_alerted_once_and_its_sync_writes_a_generation_without_docker_data(
            self, fake, alerts):
        from tests.test_standby import synced_once

        synced_once(fake)
        fake.files[STATE] = json.dumps({**json.loads(fake.files[STATE]), "emmc_return": {
            "at": "2026-11-24T12:00:00Z", "generation": 1, "by": "admin"}})
        service.refresh()
        run(service.on_emmc_after_return())
        run(service.on_emmc_after_return())
        assert [(alert.type, alert.title) for alert in alerts] == [("success", "UNOS is back on the eMMC")]
        run(service._due_syncs())
        assert fake.files[f"{BOOT_DIR}/unos/current.txt"].startswith("unos_gen=2\n")
        assert ["rm", "-rf", "--", f"{CARD_DIR}/@sys-2/var/lib/docker", f"{CARD_DIR}/@sys-2/var/lib/containerd"] in fake.changes
        stored = json.loads(fake.files[STATE])
        assert "emmc_return" not in stored and stored["last_sync"]["trigger"] == "return"


# --- the return with the card's changes: the unit's script -----------------------------------------------


class ReturnHost:
    """The card's root as the unit's script sees it, with stubs of the host's programs that log their calls:
    systemctl (docker.socket, docker.service and smbd.service active), findmnt, mount, umount, fsck.ext4,
    rsync, dd, sync and timeout. ``fail`` makes a program fail when its arguments hold a text; ``left`` is
    what the dry runs of rsync find left to copy, one answer per dry run."""

    UNITS = "docker.socket docker.service smbd.service nmbd.service"

    def __init__(self, tmp_path):
        import os

        self.dir = tmp_path / "run"
        self.dir.mkdir()
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        self.log = tmp_path / "log"
        self.mount = self.dir / "emmc"
        self.card = tmp_path / "card"
        self.boot = tmp_path / "boot"
        (self.boot / "unos").mkdir(parents=True)
        self.card.mkdir()
        self.mark = self.boot / "unos" / "emmc-failed"
        self.mark.write_text("reason=drill\n")
        self.drill = self.boot / "unos" / "drill.txt"
        self.drill.write_text("unos_emmc_part=7f\n")
        self.state = self.card / "state.json"
        self.state.write_text('{"standby_ran": {"at": "x"}}\n')
        self.counter = tmp_path / "mmcblk1"
        # The pool that holds the eMMC's Docker data.
        self.pool = tmp_path / "pools" / "data"
        # The eMMC's root as mount leaves it: its own drop-ins, one that holds Docker until its pool is mounted.
        dropins = self.mount / "etc/systemd/system/docker.service.d"
        dropins.mkdir(parents=True)
        (dropins / "data-root-on-pool.conf").write_text("[Unit]\nRequiresMountsFor=/mnt/pools/data/.docker\n")
        (dropins / "override.conf").write_text("[Service]\nLimitNOFILE=1048576\n")
        for name, text in {"excludes": "/var/lib/docker\n/etc/fstab\n", "fstab": EMMC_OWN, "counter": "unos_tries=0\n",
                           "state.json": '{"emmc_return": {"at": "x"}}\n',
                           "volumes": f"/var/lib/docker/volumes/deploy_unos-data/_data\t{self.pool}/.docker/volumes/"
                                      f"deploy_unos-data/_data\t{self.pool}/.docker/volumes.before-failback-1/deploy_unos-data\n"
                           }.items():
            (self.dir / name).write_text(text)
        self.fails: dict[str, str] = {}
        self.left: list[str] = []
        import hashlib

        self.settings = {"EMMC": "/dev/mmcblk0p1", "MOUNT": str(self.mount), "CARD": str(self.counter),
                         "SECTOR": "32767", "MARK": str(self.mark), "DRILL": str(self.drill), "STATE": str(self.state),
                         "STATE_SHA": hashlib.sha256(self.state.read_bytes()).hexdigest(), "UNITS": self.UNITS,
                         "DELAY": "0"}
        self.env = {**os.environ, "PATH": f"{self.bin}:/usr/bin:/bin"}

    def stubs(self) -> None:
        from tests.test_standby import stub

        log = self.log
        fails = "".join(f'case "$*" in *"{text}"*) echo "{program}: {text} failed" >&2; exit 3 ;; esac\n'
                        for program, text in self.fails.items() if program == "*")
        stub(self.bin, "timeout", 'while [ "${1#-}" != "$1" ]; do shift; done\nshift\nexec "$@"\n')
        for program in ("systemctl", "mount", "umount", "fsck.ext4", "sync"):
            failing = self.fails.get(program)
            check = (f'case "$*" in *"{failing}"*) echo "{program}: {failing} failed" >&2; exit 4 ;; esac\n'
                     if failing else "")
            extra = ('case "$1" in is-active) case "$3" in docker.socket|docker.service|smbd.service) exit 0 ;; '
                     '*) exit 3 ;; esac ;; esac\n') if program == "systemctl" else ""
            stub(self.bin, program, f'{extra}echo "{program} $*" >> {log}\n{check}{fails}')
        stub(self.bin, "findmnt", "exit 1\n")
        answers = self.dir.parent / "left"
        answers.mkdir(exist_ok=True)
        for index, text in enumerate(self.left):
            (answers / str(index)).write_text(text)
        failing = self.fails.get("rsync")
        check = f'case "$*" in *"{failing}"*) echo "rsync: {failing} failed" >&2; exit 23 ;; esac\n' if failing else ""
        stub(self.bin, "rsync", f'echo "rsync $*" >> {log}\n{check}'
                                f'case "$*" in *--dry-run*) n=$(ls {answers} | head -n 1); '
                                f'[ -z "$n" ] || {{ cat {answers}/$n; rm {answers}/$n; }} ;; esac\n')
        stub(self.bin, "dd", f'echo "dd $*" >> {log}\nfor a; do case $a in if=*) cp "${{a#if=}}" {self.counter} ;; esac; done\n')
        stub(self.bin, "sha256sum", 'if [ -x /usr/bin/sha256sum ]; then exec /usr/bin/sha256sum "$@"; '
                                    'else exec shasum -a 256 "$@"; fi\n')

    def run(self):
        from tests.test_standby import HOST_SCRIPTS, _run

        self.stubs()
        (self.dir / "return.env").write_text("".join(f"{key}='{value}'\n" for key, value in self.settings.items()))
        return _run(["/bin/sh", str(HOST_SCRIPTS / "unos-standby-return.sh"), str(self.dir)], self.env)

    def calls(self) -> list[str]:
        return [line.rstrip() for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def result(self) -> list[str]:
        return (self.dir / "result").read_text().splitlines()


class TestTheReturnScript:
    def test_copies_writes_the_fstab_verifies_then_resets_the_counter_drops_the_mark_and_reboots(self, tmp_path):
        found = ReturnHost(tmp_path)
        done = found.run()
        assert done.returncode == 0, done.stdout + done.stderr
        calls = [call for call in found.calls() if not call.startswith("systemctl is-active")]
        mount = str(found.mount)
        assert calls[0] == "fsck.ext4 -n /dev/mmcblk0p1"
        assert calls[1:4] == ["systemctl stop docker.socket", "systemctl stop docker.service", "systemctl stop smbd.service"]
        assert calls[4] == f"mount -t ext4 -o noatime /dev/mmcblk0p1 {mount}"
        assert calls[5] == (f"rsync -aHAX --numeric-ids --one-file-system --delete --omit-dir-times --inplace "
                            f"--no-whole-file --exclude-from={found.dir}/excludes.all / {mount}/")
        assert calls[6] == (f"rsync -aHAX --numeric-ids --delete --backup --backup-dir={found.pool}/.docker/"
                            "volumes.before-failback-1/deploy_unos-data /var/lib/docker/volumes/deploy_unos-data/_data/ "
                            f"{found.pool}/.docker/volumes/deploy_unos-data/_data/")
        assert calls[7] == f"sync {mount}/etc/fstab.unos-return"
        assert "--dry-run" in calls[8] and "--dry-run" in calls[9]
        assert calls[10:] == ["sync", f"umount {mount}", "fsck.ext4 -f -n /dev/mmcblk0p1",
                             f"dd if={found.dir}/counter of={found.counter} bs=512 seek=32767 count=1 "
                             "conv=sync,notrunc,fsync status=none", f"sync {found.state}.tmp", "sync", "systemctl reboot"]
        assert (found.mount / "etc/fstab").read_text() == EMMC_OWN
        # The drop-in that holds Docker until its pool is mounted is the eMMC's: neither copied nor deleted.
        excludes = (found.dir / "excludes.all").read_text().splitlines()
        assert "/etc/systemd/system/docker.service.d/data-root-on-pool.conf" in excludes
        assert "/etc/systemd/system/docker.service.d/override.conf" not in excludes
        assert found.counter.read_text() == "unos_tries=0\n"
        assert found.state.read_text() == '{"emmc_return": {"at": "x"}}\n'
        assert not found.mark.exists() and not found.drill.exists()
        assert found.result()[0] == "returned"

    def test_a_failed_copy_unmounts_starts_docker_and_samba_again_and_keeps_the_card_s_standby(self, tmp_path):
        found = ReturnHost(tmp_path)
        found.fails["rsync"] = "--one-file-system"
        done = found.run()
        assert done.returncode == 1
        assert found.result()[:2] == ["failed", "copy"] and "failed" in found.result()[2]
        calls = found.calls()
        assert f"umount {found.mount}" in calls
        assert calls[-3:] == ["systemctl start docker.socket", "systemctl start docker.service", "systemctl start smbd.service"]
        assert found.mark.exists() and found.drill.exists() and not found.counter.exists()
        assert found.state.read_text() == '{"standby_ran": {"at": "x"}}\n'
        assert "systemctl reboot" not in calls and "systemctl start nmbd.service" not in calls

    def test_a_state_that_changed_after_the_supervisor_read_it_stops_before_the_emmc_is_mounted(self, tmp_path):
        found = ReturnHost(tmp_path)
        found.settings["STATE_SHA"] = "0" * 64
        assert found.run().returncode == 1
        assert found.result()[:2] == ["failed", "stop"]
        assert not any(call.startswith(("mount", "rsync")) for call in found.calls())
        assert found.calls()[-1] == "systemctl start smbd.service" and found.mark.exists()

    def test_a_root_that_fails_fsck_is_not_touched(self, tmp_path):
        found = ReturnHost(tmp_path)
        found.fails["fsck.ext4"] = "-n /dev"
        assert found.run().returncode == 1
        assert found.result()[:2] == ["failed", "check"]
        assert found.calls() == ["fsck.ext4 -n /dev/mmcblk0p1"]

    def test_a_copy_that_does_not_settle_fails_at_its_verification(self, tmp_path):
        found = ReturnHost(tmp_path)
        found.left = [">f.st...... etc/hostname\n", "", ">f.st...... etc/hostname\n", ""]
        assert found.run().returncode == 1
        assert found.result()[:2] == ["failed", "verify"] and "etc/hostname" in found.result()[2]
        copies = [call for call in found.calls() if call.startswith("rsync") and "--dry-run" not in call]
        assert len(copies) == 4  # the root and the volume, twice
        assert found.mark.exists()

    def test_a_copy_that_settles_at_its_second_run_goes_on(self, tmp_path):
        found = ReturnHost(tmp_path)
        found.left = [">f.st...... etc/hostname\n", "", "", ""]
        assert found.run().returncode == 0
        assert not found.mark.exists()

    def test_an_emmc_that_fails_its_check_after_the_copy_keeps_the_card_s_standby(self, tmp_path):
        found = ReturnHost(tmp_path)
        found.fails["fsck.ext4"] = "-f -n"
        assert found.run().returncode == 1
        assert found.result()[:2] == ["failed", "verify"]
        assert found.mark.exists() and not found.counter.exists()
        assert f"umount {found.mount}" in found.calls()
