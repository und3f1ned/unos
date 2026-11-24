"""Docker's data on a pool (system.docker-root-on-pool, ADR-0486, ADR-0487).

Where Docker keeps its data: Docker's data root as Docker names it and, with the containerd image
store, containerd's root, which config.toml names; the BPI-W3 keeps both on its pool, the BPI-F3 (the
classic overlay2 store) its data root. Making the first pool on a host whose Docker's data is not on a
pool starts the move in the unit unos-docker-move, with its files in the host's RAM; the supervisor
follows the unit until its result, also after its own restart, and alerts and audits the outcome.
Storage refuses to unmount or remove the pool that Docker's configuration names, with 409 naming
Docker. The script of the unit runs here on a fake host of stub programs (tests/docker_move_fake.py):
the move, its restart of the containers and its deletion of the old copies, and the restore of each
step that fails.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import signal
import subprocess
import sys
import time
import tomllib
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src import host as host_module
from src.audit import router as audit_router
from src.auth.utils import verify_token
from src.host import HostResult
from src.main import app
from src.system import docker_data
from src.system.docker_data import RUN_DIR, UNIT, DockerDataError

from .audit_helpers import audit_entries
from . import docker_move_fake
from .docker_move_fake import FakeMoveHost, run_script
from .test_pool_persistence import DATA_UUID, admin_client, install_fake_host, wait_for_app_starts

_real_run = subprocess.run
fake_module = docker_move_fake.__file__

DOCKER_CE_CONFIG = """#   Copyright 2018-2022 Docker Inc.

#   Licensed under the Apache License, Version 2.0 (the "License");

disabled_plugins = ["cri"]

#root = "/var/lib/containerd"
#state = "/run/containerd"
#subreaper = true

#[grpc]
#  address = "/run/containerd/containerd.sock"
"""

FRESH = {"DockerRootDir": "/var/lib/docker", "Driver": "overlayfs",
         "DriverStatus": [["driver-type", "io.containerd.snapshotter.v1"]]}
W3 = {"DockerRootDir": "/mnt/pools/data/.docker", "Driver": "overlayfs",
      "DriverStatus": [["driver-type", "io.containerd.snapshotter.v1"]]}
F3 = {"DockerRootDir": "/mnt/pools/data/.docker", "Driver": "overlay2",
      "DriverStatus": [["Backing Filesystem", "btrfs"], ["Supports d_type", "true"]]}


class MoveCommands:
    """The host commands of the move on a FakeHost: systemctl show, systemd-run, and the move's folder
    in /run, whose files are FakeHost's (cat reads them, write_host_file_atomic writes them)."""

    def __init__(self, fake) -> None:
        self.fake = fake
        self.inner = fake.run
        self.containerd_loaded = True
        self.unit_state = "inactive"
        self.units: list[list[str]] = []
        self.systemd_run_fails: str | None = None

    def run(self, cmd, **kwargs):
        cmd = list(cmd)
        if cmd[:2] == ["systemctl", "show"]:
            self.fake.calls.append(cmd)
            if cmd[2] == "--property=LoadState":
                assert cmd[-1] == "containerd.service", cmd
                return HostResult(0, "loaded\n" if self.containerd_loaded else "not-found\n", "")
            assert cmd[2:] == ["--property=ActiveState", "--value", f"{UNIT}.service"], cmd
            return HostResult(0, f"{self.unit_state}\n", "")
        if cmd[0] == "systemd-run":
            self.fake.calls.append(cmd)
            self.fake.changes.append(cmd)
            if self.systemd_run_fails:
                return HostResult(1, "", self.systemd_run_fails)
            self.units.append(cmd)
            self.unit_state = "active"
            return HostResult(0, "", "")
        if cmd == ["rm", "-rf", "--", RUN_DIR]:
            self.fake.calls.append(cmd)
            self.fake.changes.append(cmd)
            for path in [path for path in self.fake.files if path.startswith(RUN_DIR + "/")]:
                del self.fake.files[path]
            return HostResult(0, "", "")
        if cmd[:4] == ["mkdir", "-p", "-m", "0700"]:
            assert cmd[4:] == [f"{RUN_DIR}/new", f"{RUN_DIR}/old"], cmd
            self.fake.calls.append(cmd)
            self.fake.changes.append(cmd)
            return HostResult(0, "", "")
        return self.inner(cmd, **kwargs)

    def staged(self) -> dict[str, str]:
        return {path.removeprefix(RUN_DIR + "/"): text for path, text in self.fake.files.items()
                if path.startswith(RUN_DIR + "/")}

    def ends(self, result: str, step: str, text: str) -> None:
        """The unit writes its result and ends."""
        self.fake.files[f"{RUN_DIR}/result"] = f"{result}\n{step}\n{text}\n"
        self.unit_state = "inactive"


@pytest.fixture
def host(monkeypatch) -> Iterator[SimpleNamespace]:
    fake = install_fake_host(monkeypatch)
    commands = MoveCommands(fake)
    monkeypatch.setattr(host_module, "run_on_host", commands.run)
    info = dict(FRESH)
    monkeypatch.setattr(docker_data, "_docker_info", lambda: info)
    monkeypatch.setattr(docker_data, "FOLLOW_INTERVAL_SECONDS", 0.01)
    yield SimpleNamespace(fake=fake, commands=commands, info=info)
    wait_for_app_starts()


@pytest.fixture
def alerts(monkeypatch) -> list:
    sent: list = []

    async def deliver(event):
        sent.append(event)

    monkeypatch.setattr(importlib.import_module("src.health.router"), "_deliver_alert", deliver)
    return sent


def audit_log() -> list[dict]:
    return audit_entries(Path(audit_router.AUDIT_FILE))


def on_the_pool(host, info=W3, containerd_root="/mnt/pools/data/.containerd") -> None:
    """A host moved by hand, as the BPI-W3 was: both roots on the pool data, which is managed."""
    host.info.clear()
    host.info.update(info)
    host.fake.files["/etc/docker/daemon.json"] = json.dumps({"data-root": "/mnt/pools/data/.docker"})
    host.fake.files["/etc/containerd/config.toml"] = f'{DOCKER_CE_CONFIG}\nroot = "{containerd_root}"\n'


async def until_recorded() -> None:
    task = docker_data._state.task
    assert task is not None
    await asyncio.wait_for(task, 5)


# ---------------------------------------------------------------------------
# Where Docker keeps its data
# ---------------------------------------------------------------------------


class TestPlacement:
    def test_a_fresh_docker_29_keeps_both_roots_on_the_system_disk(self, host):
        where = docker_data.placement()
        assert (where.docker_root, where.containerd_root, where.pool) == ("/var/lib/docker", "/var/lib/containerd", None)

    def test_the_w3_keeps_both_on_its_pool(self, host):
        on_the_pool(host)
        where = docker_data.placement()
        assert (where.docker_root, where.containerd_root, where.pool) == (
            "/mnt/pools/data/.docker", "/mnt/pools/data/.containerd", "data")

    def test_the_f3_keeps_its_data_root_on_its_pool_and_no_images_in_containerd(self, host):
        on_the_pool(host, F3, containerd_root="/var/lib/containerd")
        where = docker_data.placement()
        assert (where.docker_root, where.containerd_root, where.pool) == ("/mnt/pools/data/.docker", None, "data")

    def test_images_in_containerd_on_the_system_disk_are_not_on_a_pool(self, host):
        on_the_pool(host, containerd_root="/var/lib/containerd")
        assert docker_data.placement().pool is None

    def test_without_a_containerd_service_its_root_is_dockers_own(self, host):
        host.commands.containerd_loaded = False
        on_the_pool(host, containerd_root="/var/lib/containerd")
        assert docker_data.placement().containerd_root is None
        assert docker_data.placement().pool == "data"

    def test_without_docker_the_answer_is_503(self, host, monkeypatch):
        def no_docker():
            from src.containers.docker import DockerUnavailable
            raise DockerUnavailable("connection refused")

        monkeypatch.setattr(docker_data, "_docker_info", no_docker)
        with pytest.raises(DockerDataError) as caught:
            docker_data.placement()
        assert caught.value.status == 503

    def test_a_config_toml_that_does_not_parse_is_an_error(self, host):
        host.fake.files["/etc/containerd/config.toml"] = "root = /var/lib/containerd\n"
        with pytest.raises(DockerDataError, match="not valid TOML"):
            docker_data.placement()

    @pytest.mark.parametrize(("info", "containerd_root", "loaded", "images"), [
        # The W3: the containerd image store of the host's containerd, on the pool.
        (W3, "/mnt/pools/data/.containerd", True, "/mnt/pools/data/.containerd"),
        # The F3: Docker's classic store keeps its images below its data root.
        (F3, "/var/lib/containerd", True, "/mnt/pools/data/.docker"),
        # The containerd image store of a containerd of Docker's own, below its data root.
        (W3, "/var/lib/containerd", False, "/mnt/pools/data/.docker"),
        # A fresh Docker 29 on the system disk.
        (FRESH, "/var/lib/containerd", True, "/var/lib/containerd"),
    ], ids=["w3", "f3", "dockers-own-containerd", "fresh"])
    def test_the_folder_of_dockers_images(self, host, info, containerd_root, loaded, images):
        # ADR-0493: the folder whose free space the App Store's checks read.
        on_the_pool(host, info, containerd_root=containerd_root)
        host.commands.containerd_loaded = loaded
        assert docker_data.images_root(dict(info)) == images

    @pytest.mark.parametrize(("path", "pool"), [
        ("/mnt/pools/data/.docker", "data"), ("/mnt/pools/data", "data"), ("/mnt/pools/media/x/y", "media"),
        ("/var/lib/docker", None), ("/mnt/pools", None), ("/mnt/poolsdata/.docker", None),
        ("/mnt/pools/../../var/lib/docker", None),
    ])
    def test_the_pool_of_a_path(self, host, path, pool):
        assert docker_data.pool_of(path) == pool


class TestTheEndpoint:
    def test_general_reads_where_docker_keeps_its_data(self, host):
        on_the_pool(host)
        with admin_client() as api:
            answer = api.get("/api/v1/system/docker-data")
        assert answer.status_code == 200, answer.text
        assert answer.json() == {"data_root": "/mnt/pools/data/.docker",
                                 "containerd_root": "/mnt/pools/data/.containerd", "pool": "data", "moving_to": None}

    def test_without_docker_it_answers_503(self, host, monkeypatch):
        def no_docker():
            from src.containers.docker import DockerUnavailable
            raise DockerUnavailable("connection refused")

        monkeypatch.setattr(docker_data, "_docker_info", no_docker)
        with admin_client() as api:
            answer = api.get("/api/v1/system/docker-data")
        assert answer.status_code == 503
        assert answer.json() == {"detail": "Docker is not available: connection refused"}

    def test_a_regular_user_is_refused(self, host):
        app.dependency_overrides[verify_token] = lambda: SimpleNamespace(id=2, username="bob", is_admin=False,
                                                                         is_active=True)
        try:
            assert TestClient(app).get("/api/v1/system/docker-data").status_code == 403
        finally:
            app.dependency_overrides.pop(verify_token, None)


# ---------------------------------------------------------------------------
# The configuration the move writes
# ---------------------------------------------------------------------------


class TestTheConfiguration:
    def test_daemon_json_keeps_its_other_settings(self):
        text = docker_data.daemon_json_with_root('{"log-driver": "json-file", "data-root": "/srv/docker"}',
                                                 "/mnt/pools/data/.docker")
        assert json.loads(text) == {"log-driver": "json-file", "data-root": "/mnt/pools/data/.docker"}
        assert json.loads(docker_data.daemon_json_with_root(None, "/x/.docker")) == {"data-root": "/x/.docker"}

    def test_a_daemon_json_that_does_not_parse_is_refused(self):
        with pytest.raises(DockerDataError, match="not valid JSON"):
            docker_data.daemon_json_with_root("{", "/x/.docker")
        with pytest.raises(DockerDataError, match="no JSON object"):
            docker_data.daemon_json_with_root("[]", "/x/.docker")

    def test_dockers_config_toml_gets_a_top_level_root_and_keeps_the_rest(self):
        text = docker_data.containerd_config_with_root(DOCKER_CE_CONFIG, "/mnt/pools/data/.containerd")
        assert text == 'root = "/mnt/pools/data/.containerd"\n' + DOCKER_CE_CONFIG
        assert tomllib.loads(text) == {"root": "/mnt/pools/data/.containerd", "disabled_plugins": ["cri"]}

    def test_a_root_before_the_first_table_is_replaced(self):
        config = 'version = 2\nroot = "/var/lib/containerd"\n\n[plugins."x"]\n  root = "/kept"\n'
        text = docker_data.containerd_config_with_root(config, "/p/.containerd")
        assert text == 'version = 2\nroot = "/p/.containerd"\n\n[plugins."x"]\n  root = "/kept"\n'

    def test_a_root_only_inside_a_table_is_not_the_top_level_one(self):
        config = '[plugins."x"]\nroot = "/kept"\n'
        text = docker_data.containerd_config_with_root(config, "/p/.containerd")
        config_after = tomllib.loads(text)
        assert config_after["root"] == "/p/.containerd" and config_after["plugins"]["x"]["root"] == "/kept"

    def test_no_config_toml_becomes_a_version_2_one_with_the_root(self):
        assert docker_data.containerd_config_with_root(None, "/p/.containerd") == 'version = 2\nroot = "/p/.containerd"\n'

    def test_the_plan_of_a_fresh_docker(self, host):
        host.fake.files["/etc/containerd/config.toml"] = DOCKER_CE_CONFIG
        host.fake.files["/etc/docker/daemon.json"] = '{"log-opts": {"max-size": "10m"}}'
        plan = docker_data.plan_move("data")
        assert plan is not None
        assert (plan.pool_dir, plan.docker_old, plan.docker_new, plan.containerd_old, plan.containerd_new) == (
            "/mnt/pools/data", "/var/lib/docker", "/mnt/pools/data/.docker", "/var/lib/containerd",
            "/mnt/pools/data/.containerd")
        assert {name: target for name, (target, _) in plan.files.items()} == {
            "daemon.json": "/etc/docker/daemon.json",
            "config.toml": "/etc/containerd/config.toml",
            "containerd.conf": "/etc/systemd/system/containerd.service.d/root-on-pool.conf",
            "docker.conf": "/etc/systemd/system/docker.service.d/data-root-on-pool.conf",
        }
        assert json.loads(plan.files["daemon.json"][1]) == {"log-opts": {"max-size": "10m"},
                                                           "data-root": "/mnt/pools/data/.docker"}
        assert plan.files["docker.conf"][1].endswith(
            "[Unit]\nRequiresMountsFor=/mnt/pools/data/.docker /mnt/pools/data/.containerd\n")
        assert plan.files["containerd.conf"][1].endswith("[Unit]\nRequiresMountsFor=/mnt/pools/data/.containerd\n")
        assert docker_data.move_settings(plan) == (
            "POOL_DIR=/mnt/pools/data\nDOCKER_OLD=/var/lib/docker\nDOCKER_NEW=/mnt/pools/data/.docker\n"
            "CONTAINERD_OLD=/var/lib/containerd\nCONTAINERD_NEW=/mnt/pools/data/.containerd\n"
            "FILES='daemon.json:/etc/docker/daemon.json config.toml:/etc/containerd/config.toml "
            "containerd.conf:/etc/systemd/system/containerd.service.d/root-on-pool.conf "
            "docker.conf:/etc/systemd/system/docker.service.d/data-root-on-pool.conf'\n")

    def test_without_a_containerd_service_only_dockers_root_moves(self, host):
        host.commands.containerd_loaded = False
        plan = docker_data.plan_move("data")
        assert plan is not None
        assert (plan.containerd_old, plan.containerd_new) == ("", "")
        assert set(plan.files) == {"daemon.json", "docker.conf"}
        assert plan.files["docker.conf"][1].endswith("RequiresMountsFor=/mnt/pools/data/.docker\n")

    def test_docker_on_a_pool_has_no_plan(self, host):
        on_the_pool(host)
        assert docker_data.plan_move("tank") is None

    @pytest.mark.parametrize("driver", ["btrfs", "zfs", ""])
    def test_layers_that_are_subvolumes_are_not_moved(self, host, driver):
        host.info["Driver"] = driver
        with pytest.raises(DockerDataError, match="keeps its layers in a way that UNOS does not copy"):
            docker_data.plan_move("data")

    @pytest.mark.parametrize("root", ["/", "/docker", "/var/lib/my docker", "/var/lib/do$cker", "/var/lib/'docker'"])
    def test_a_root_at_an_odd_path_is_not_moved(self, host, root):
        host.info["DockerRootDir"] = root
        with pytest.raises(DockerDataError, match="plain paths only"):
            docker_data.plan_move("data")


# ---------------------------------------------------------------------------
# Create Pool starts the move; the supervisor follows it
# ---------------------------------------------------------------------------


TANK = {"name": "data", "devices": ["/dev/sdb"], "raid_level": "single", "confirm_name": "data"}


def create_first_pool(host) -> dict:
    host.fake.add_disk("sdb")
    with admin_client() as api:
        answer = api.post("/api/v1/storage/pools", json=TANK)
    assert answer.status_code == 200, answer.text
    return answer.json()


class TestTheStart:
    def test_the_first_pool_moves_dockers_data_onto_it(self, host, alerts):
        host.fake.files["/etc/containerd/config.toml"] = DOCKER_CE_CONFIG
        answer = create_first_pool(host)
        assert (answer["name"], answer["docker_move"], answer["docker_move_error"]) == ("data", "started", None)
        staged = host.commands.staged()
        assert staged["move.sh"] == docker_data.HOST_SCRIPT.read_text()
        assert staged["move.env"].startswith("POOL_DIR=/mnt/pools/data\n")
        assert set(staged) == {"move.sh", "move.env", "new/daemon.json", "new/config.toml", "new/containerd.conf",
                               "new/docker.conf"}
        assert host.commands.units == [[
            "systemd-run", f"--unit={UNIT}", "--description=UNOS: Docker's data moves onto the pool data",
            "--collect", "--quiet", "--property=TimeoutStopSec=1200", "--setenv=HOME=/root", "/bin/sh",
            f"{RUN_DIR}/move.sh", RUN_DIR,
        ]]
        # Nothing of the host's configuration changes before the unit runs.
        assert "/etc/docker/daemon.json" not in host.fake.files
        assert host.fake.files["/etc/containerd/config.toml"] == DOCKER_CE_CONFIG
        record = json.loads(docker_data.STATE_FILE.read_text())
        assert (record["pool"], record["by"], record["docker_old"], record["containerd_old"]) == (
            "data", "admin", "/var/lib/docker", "/var/lib/containerd")
        assert docker_data.moving_to() == "data"
        [created] = [entry for entry in audit_log() if entry["action"] == "pool_created"]
        assert created["details"] == (f"Created the pool data (single) on /dev/sdb; Docker's data moves onto it in "
                                      f"the unit {UNIT} of the host")
        assert alerts == []

    def test_a_second_pool_moves_nothing(self, host):
        host.fake.add_disk("sda")
        host.fake.add_btrfs("media", DATA_UUID, ["/dev/sda"])
        host.fake.files["/etc/fstab"] += f"# unos-pool:media (managed by UNOS)\nUUID={DATA_UUID} /mnt/pools/media btrfs defaults 0 0\n"
        answer = create_first_pool(host)
        assert (answer["docker_move"], answer["docker_move_error"]) == (None, None)
        assert host.commands.units == [] and host.commands.staged() == {}

    def test_a_host_on_a_pool_already_moves_nothing(self, host):
        on_the_pool(host)
        answer = create_first_pool(host)
        assert answer["docker_move"] is None and host.commands.units == []

    def test_without_docker_the_pool_is_made_and_the_move_is_said_not_to_start(self, host, monkeypatch):
        def no_docker():
            from src.containers.docker import DockerUnavailable
            raise DockerUnavailable("connection refused")

        monkeypatch.setattr(docker_data, "_docker_info", no_docker)
        answer = create_first_pool(host)
        assert (answer["docker_move"], answer["docker_move_error"]) == (
            "not_started", "Docker is not available: connection refused")
        assert host.commands.units == [] and not docker_data.STATE_FILE.exists()
        [created] = [entry for entry in audit_log() if entry["action"] == "pool_created"]
        assert created["details"].endswith("; Docker's data could not be moved onto it: Docker is not available: "
                                           "connection refused")

    def test_a_docker_that_answers_too_late_does_not_fail_the_create(self, host, monkeypatch):
        import requests

        def too_late():
            raise requests.exceptions.ReadTimeout("Read timed out. (read timeout=60)")

        monkeypatch.setattr(docker_data, "_docker_info", too_late)
        answer = create_first_pool(host)
        assert (answer["docker_move"], answer["docker_move_error"]) == (
            "not_started", "Docker is not available: Read timed out. (read timeout=60)")

    def test_an_unexpected_error_does_not_fail_the_create(self, host, monkeypatch, caplog):
        def broken(plan):
            raise OSError("No space left on device")

        monkeypatch.setattr(docker_data, "_stage", broken)
        answer = create_first_pool(host)
        assert (answer["docker_move"], answer["docker_move_error"]) == ("not_started", "No space left on device")
        assert "could not start" in caplog.text and not docker_data.STATE_FILE.exists()

    def test_no_move_starts_while_an_update_of_unos_runs(self, host, monkeypatch):
        from src.updates import updater

        monkeypatch.setattr(updater.Updater, "running", lambda self: True)
        answer = create_first_pool(host)
        assert answer["docker_move"] == "not_started"
        assert answer["docker_move_error"] == "an update of UNOS runs, which restarts UNOS's containers too"
        assert host.commands.units == []

    def test_a_unit_that_does_not_start_leaves_nothing_behind(self, host):
        host.commands.systemd_run_fails = "Failed to start transient service unit: Unit exists already."
        answer = create_first_pool(host)
        assert answer["docker_move"] == "not_started"
        assert answer["docker_move_error"] == (f"The unit {UNIT} did not start: Failed to start transient service "
                                               "unit: Unit exists already.")
        assert host.commands.staged() == {} and not docker_data.STATE_FILE.exists()
        assert docker_data.moving_to() is None


class TestTheOutcome:
    async def _started(self, host) -> None:
        host.fake.add_disk("sdb")
        await asyncio.to_thread(host.fake.files.__setitem__, "/etc/containerd/config.toml", DOCKER_CE_CONFIG)
        start = await docker_data.after_pool_created("data", "admin")
        assert start == docker_data.MoveStart(True)

    async def test_a_moved_outcome_is_audited_alerted_and_forgotten(self, host, alerts):
        await self._started(host)
        host.commands.ends("moved", "", "Docker's data root is /mnt/pools/data/.docker, containerd's root "
                                        "/mnt/pools/data/.containerd")
        await until_recorded()
        [moved] = [entry for entry in audit_log() if entry["action"] == "docker_data_moved"]
        assert (moved["username"], moved["resource_type"], moved["resource_id"], moved["success"]) == (
            "system", "pool", "data", True)
        assert [(event.type.value, event.title) for event in alerts] == [
            ("success", "Docker's data moved to the pool data")]
        assert alerts[0].message == ("Docker's data root is /mnt/pools/data/.docker, containerd's root "
                                     "/mnt/pools/data/.containerd.")
        assert not docker_data.STATE_FILE.exists() and docker_data.moving_to() is None
        assert host.commands.staged() == {}

    async def test_a_restored_outcome_names_the_step_that_failed(self, host, alerts):
        await self._started(host)
        host.commands.ends("restored", "copy", "rsync: write failed: No space left on device (28); Docker runs "
                                               "from /var/lib/docker as before")
        await until_recorded()
        [event] = alerts
        assert (event.type.value, event.title) == ("error", "Docker's data stays on the system disk")
        assert event.message == ("The move of Docker's data onto the pool data failed at its step copy: rsync: write "
                                 "failed: No space left on device (28); Docker runs from /var/lib/docker as before.")
        [moved] = [entry for entry in audit_log() if entry["action"] == "docker_data_moved"]
        assert (moved["success"], moved["severity"]) == (False, "warning")
        assert "at its step copy" in moved["details"] and moved["details"].startswith("Started by admin at ")

    async def test_a_unit_that_ends_without_a_result_is_recorded_as_failed(self, host, alerts):
        await self._started(host)
        host.commands.unit_state = "inactive"
        await until_recorded()
        [event] = alerts
        assert event.title == "The move of Docker's data failed"
        assert event.message.startswith(f"The move of Docker's data onto the pool data failed: The unit {UNIT} of "
                                        "the host ended without a result")
        assert ("Docker's data is not on the pool (its data root is /var/lib/docker), and the move may have left "
                "/mnt/pools/data/.docker and /mnt/pools/data/.containerd") in event.message

    async def test_a_unit_that_ends_without_a_result_after_the_switch_says_what_may_be_left(self, host, alerts):
        await self._started(host)
        on_the_pool(host)
        host.commands.unit_state = "inactive"
        await until_recorded()
        [event] = alerts
        assert (event.type.value, event.title) == ("warning", "Docker's data moved to the pool data")
        assert ("but Docker's data root is /mnt/pools/data/.docker; the old copies may still be on the system disk "
                "(/var/lib/docker and /var/lib/containerd)") in event.message
        [moved] = [entry for entry in audit_log() if entry["action"] == "docker_data_moved"]
        assert (moved["success"], moved["severity"]) == (True, "warning")

    async def test_a_move_with_a_container_that_did_not_start_is_a_warning(self, host, alerts):
        await self._started(host)
        host.commands.ends("moved", "restart", "Docker's data root is /mnt/pools/data/.docker; these did not "
                                               "start again: jellyfin: port is already allocated")
        await until_recorded()
        [event] = alerts
        assert (event.type.value, event.title) == ("warning", "Docker's data moved to the pool data")
        assert event.message.endswith("these did not start again: jellyfin: port is already allocated.")

    async def test_the_next_supervisor_follows_the_move_from_its_start(self, host, alerts):
        await self._started(host)
        # The unit stops this supervisor: its follow ends, the record stays.
        await docker_data.stop()
        docker_data._state.move = None
        assert docker_data.STATE_FILE.exists()
        # The one that starts from the pool finds the record and the unit, which still deletes the old copies.
        await docker_data.recover_after_start()
        assert docker_data.moving_to() == "data"
        await asyncio.sleep(0.05)
        assert alerts == []
        host.commands.ends("moved", "", "Docker's data root is /mnt/pools/data/.docker")
        await until_recorded()
        assert [event.title for event in alerts] == ["Docker's data moved to the pool data"]

    async def test_a_start_without_a_record_follows_nothing(self, host):
        await docker_data.recover_after_start()
        assert docker_data._state.task is None and host.fake.calls == []

    async def test_a_move_runs_once(self, host):
        await self._started(host)
        host.fake.add_disk("sdc")
        start = await docker_data.after_pool_created("data", "admin")
        assert start == docker_data.MoveStart(False, "a move onto the pool data runs")
        await docker_data.stop()


# ---------------------------------------------------------------------------
# Storage refuses to unmount or remove the pool of Docker's data
# ---------------------------------------------------------------------------


@pytest.fixture
def docker_pool(host):
    """The pool data mounted and managed, with Docker's data on it (the W3)."""
    host.fake.add_disk("sda", model="WDC WD40EFRX", serial="WD-1")
    host.fake.add_btrfs("data", DATA_UUID, ["/dev/sda"])
    host.fake.dirs.add("/mnt/pools/data")
    from src.storage import pools

    pools.mount_pool("data")
    wait_for_app_starts()
    on_the_pool(host)
    host.fake.changes.clear()
    return host


class TestThePoolOfDockersData:
    @pytest.mark.parametrize(("method", "url"), [
        ("delete", "/api/v1/storage/pools/data"), ("post", "/api/v1/storage/pools/data/unmount"),
    ])
    def test_removing_or_unmounting_it_is_refused_naming_docker(self, docker_pool, method, url):
        with admin_client() as api:
            answer = getattr(api, method)(url)
        assert answer.status_code == 409, answer.text
        assert answer.json()["detail"] == (
            "Pool 'data' holds Docker's data (Docker's data root /mnt/pools/data/.docker, containerd's root "
            "/mnt/pools/data/.containerd): Docker, and with it UNOS and the apps, runs from it, so it stays "
            "mounted and managed.")
        assert docker_pool.fake.changes == []
        assert not [entry for entry in audit_log() if entry["action"] in ("pool_deleted", "pool_unmounted")]

    def test_the_configuration_counts_also_while_docker_is_down(self, docker_pool, monkeypatch):
        def no_docker():
            from src.containers.docker import DockerUnavailable
            raise DockerUnavailable("connection refused")

        monkeypatch.setattr(docker_data, "_docker_info", no_docker)
        docker_pool.fake.files["/etc/containerd/config.toml"] = DOCKER_CE_CONFIG
        from src.storage import pools

        with pytest.raises(pools.PoolBusyError, match=r"Docker's data \(Docker's data root /mnt/pools/data/.docker\)"):
            pools.delete_pool("data")

    def test_a_pool_that_a_move_goes_to_is_refused(self, docker_pool):
        docker_pool.fake.files.pop("/etc/docker/daemon.json")
        docker_pool.fake.files["/etc/containerd/config.toml"] = DOCKER_CE_CONFIG
        docker_data._state.move = {"pool": "data", "started_at": "2026-11-23T20:00:00Z", "by": "admin",
                                   "docker_old": "/var/lib/docker", "containerd_old": "/var/lib/containerd"}
        with admin_client() as api:
            answer = api.delete("/api/v1/storage/pools/data")
        assert answer.status_code == 409
        assert answer.json()["detail"].startswith(
            "Pool 'data' holds the move of Docker's data onto it, which runs: Docker")

    def test_another_pool_is_removed_as_before(self, docker_pool):
        docker_pool.fake.files["/etc/docker/daemon.json"] = json.dumps({"data-root": "/mnt/pools/media/.docker"})
        docker_pool.fake.files["/etc/containerd/config.toml"] = DOCKER_CE_CONFIG
        with admin_client() as api:
            answer = api.delete("/api/v1/storage/pools/data")
        assert answer.status_code == 200, answer.text

    def test_a_configuration_that_cannot_be_read_changes_nothing(self, docker_pool):
        docker_pool.fake.files["/etc/docker/daemon.json"] = "{"
        with admin_client() as api:
            answer = api.delete("/api/v1/storage/pools/data")
        assert answer.status_code == 500
        assert "/etc/docker/daemon.json is not valid JSON" in answer.json()["detail"]
        assert docker_pool.fake.changes == []


# ---------------------------------------------------------------------------
# The unit's script on a fake host
# ---------------------------------------------------------------------------


@pytest.fixture
def move(tmp_path) -> SimpleNamespace:
    """A host whose Docker keeps its data on its system disk (below tmp_path/root) and a mounted pool
    data, with the move's folder staged as the supervisor stages it."""
    root = tmp_path / "root"
    pool = root / "mnt" / "pools" / "data"
    pool.mkdir(parents=True)
    docker_old, containerd_old = root / "var" / "lib" / "docker", root / "var" / "lib" / "containerd"
    (docker_old / "volumes" / "deploy_unos-data" / "_data").mkdir(parents=True)
    (docker_old / "volumes" / "deploy_unos-data" / "_data" / "unos.db").write_text("the database")
    (containerd_old / "io.containerd.content.v1.content").mkdir(parents=True)
    (containerd_old / "io.containerd.content.v1.content" / "layer").write_text("a layer")
    etc = root / "etc"
    (etc / "containerd").mkdir(parents=True)
    (etc / "containerd" / "config.toml").write_text(DOCKER_CE_CONFIG)
    (etc / "docker").mkdir()
    (etc / "docker" / "daemon.json").write_text('{"log-driver": "json-file"}\n')
    fake = FakeMoveHost(tmp_path / "host")
    fake.set(daemon_json=str(etc / "docker" / "daemon.json"), containerd_config=str(etc / "containerd" / "config.toml"),
             docker_default=str(docker_old), containerd_default=str(containerd_old), docker_root=str(docker_old),
             containerd_root=str(containerd_old))
    files = {
        "daemon.json": (str(etc / "docker" / "daemon.json"),
                        docker_data.daemon_json_with_root('{"log-driver": "json-file"}', f"{pool}/.docker")),
        "config.toml": (str(etc / "containerd" / "config.toml"),
                        docker_data.containerd_config_with_root(DOCKER_CE_CONFIG, f"{pool}/.containerd")),
        "containerd.conf": (str(etc / "systemd" / "system" / "containerd.service.d" / "root-on-pool.conf"),
                            "[Unit]\nRequiresMountsFor=x\n"),
        "docker.conf": (str(etc / "systemd" / "system" / "docker.service.d" / "data-root-on-pool.conf"),
                        "[Unit]\nRequiresMountsFor=y\n"),
    }
    plan = docker_data.Plan("data", str(pool), str(docker_old), f"{pool}/.docker", str(containerd_old),
                            f"{pool}/.containerd", files)
    folder = tmp_path / "run" / "unos-docker-move"
    (folder / "new").mkdir(parents=True)
    (folder / "old").mkdir()
    (folder / "move.env").write_text(docker_data.move_settings(plan))
    for name, (_, text) in files.items():
        (folder / "new" / name).write_text(text)
    return SimpleNamespace(fake=fake, folder=folder, plan=plan, etc=etc, pool=pool, docker_old=docker_old,
                           containerd_old=containerd_old)


def run_move(move) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    done = run_script(docker_data.HOST_SCRIPT, move.folder, move.fake)
    result = (move.folder / "result").read_text().split("\n") if (move.folder / "result").exists() else []
    return done, result


class TestTheScript:
    def test_the_move(self, move):
        done, result = run_move(move)
        assert done.returncode == 0, done.stdout + done.stderr
        assert result[:2] == ["moved", ""]
        assert result[2] == (f"Docker's data root is {move.pool}/.docker, containerd's root {move.pool}/.containerd")
        # The data on the pool, the old copies gone.
        assert (move.pool / ".docker" / "volumes" / "deploy_unos-data" / "_data" / "unos.db").read_text() == "the database"
        assert (move.pool / ".containerd" / "io.containerd.content.v1.content" / "layer").read_text() == "a layer"
        assert not move.docker_old.exists() and not move.containerd_old.exists()
        # The configuration installed.
        assert json.loads((move.etc / "docker" / "daemon.json").read_text()) == {
            "log-driver": "json-file", "data-root": f"{move.pool}/.docker"}
        assert tomllib.loads((move.etc / "containerd" / "config.toml").read_text())["root"] == f"{move.pool}/.containerd"
        assert (move.etc / "systemd" / "system" / "docker.service.d" / "data-root-on-pool.conf").exists()
        # Docker from the pool, and every container that ran runs again; the one that was stopped stays so.
        state = move.fake.load()
        assert (state["docker_root"], state["containerd_root"]) == (f"{move.pool}/.docker", f"{move.pool}/.containerd")
        assert state["containers"] == {"unos-supervisor": True, "unos-webui": True, "jellyfin": True,
                                       "stopped-app": False}
        calls = move.fake.calls()
        order = [calls.index(call) for call in (
            "systemctl stop unos.service", "docker stop jellyfin-id",
            "systemctl stop docker.socket docker.service containerd.service",
            f"rsync -aHAXx --numeric-ids --open-noatime {move.docker_old}/ {move.pool}/.docker/",
            "systemctl daemon-reload", "systemctl start docker.socket docker.service containerd.service",
            "docker info --format {{.DockerRootDir}}", "systemctl start unos.service", "docker start jellyfin-id",
            f"rm -rf --one-file-system -- {move.docker_old}",
        )]
        assert order == sorted(order), calls

    def test_rsync_without_open_noatime_copies_without_it(self, move):
        move.fake.set(open_noatime=False)
        done, result = run_move(move)
        assert result[0] == "moved", done.stdout
        assert f"rsync -aHAXx --numeric-ids {move.docker_old}/ {move.pool}/.docker/" in move.fake.calls()

    def test_a_container_that_does_not_start_again_is_said_and_the_move_stays(self, move):
        move.fake.fail("docker start jellyfin-id", "Error response from daemon: port is already allocated")
        done, result = run_move(move)
        assert done.returncode == 0
        assert result[:2] == ["moved", "restart"]
        assert result[2].endswith("; these did not start again: jellyfin: Error response from daemon: port is "
                                  "already allocated")
        assert not move.docker_old.exists()

    def test_an_old_copy_with_a_mount_below_it_is_kept(self, move):
        mountinfo = move.folder.parent / "mountinfo"
        lines = (f"29 1 0:25 / / rw - ext4 /dev/vda1 rw\\n"
                 f"95 29 0:25 /srv/x {move.docker_old}/volumes/bound/_data rw - ext4 /dev/vda1 rw\\n")
        # Something mounts a folder below the old root once the containers start again from the pool.
        stub = move.fake.bin / "docker"
        stub.write_text(stub.read_text().replace("#!/bin/sh\n", f'#!/bin/sh\n[ "$1" != start ] || printf "{lines}" '
                                                 f'> "{mountinfo}"\n', 1))
        move.fake.extra_env = {"UNOS_DOCKER_MOVE_MOUNTINFO": str(mountinfo)}
        done, result = run_move(move)
        assert result[:2] == ["moved", "delete"], done.stdout
        assert result[2].endswith(f"; the old copies that were not deleted: {move.docker_old}: something is mounted "
                                  "below it")
        assert move.docker_old.exists() and not move.containerd_old.exists()

    def test_a_root_with_a_mount_below_it_is_not_copied(self, move):
        mountinfo = move.folder.parent / "mountinfo"
        mountinfo.write_text(f"95 29 0:44 / {move.containerd_old}/io.containerd.content.v1.content rw - nfs4 nas:/x rw\n")
        move.fake.extra_env = {"UNOS_DOCKER_MOVE_MOUNTINFO": str(mountinfo)}
        done, result = run_move(move)
        assert result[:3] == ["restored", "copy", f"something is mounted below {move.containerd_old}; Docker runs from "
                                                  f"{move.docker_old} as before"], done.stdout
        assert not (move.pool / ".docker").exists()

    def test_a_stop_of_the_unit_during_the_copy_restores(self, move):
        """systemd stops the unit's processes with SIGTERM (KillMode control-group): the copy ends, and
        the script's trap restores before the unit's TimeoutStopSec."""
        stub = move.fake.bin / "rsync"
        started = move.folder.parent / "rsync-started"
        stub.write_text(f'#!/bin/sh\ncase "$*" in *--help*) exec "{sys.executable}" "{fake_module}" '
                        f'"{move.fake.state_path}" rsync "$@";; esac\ntouch "{started}"\nexec sleep 30\n')
        process = subprocess.Popen(["/bin/sh", str(docker_data.HOST_SCRIPT), str(move.folder)], env=move.fake.env(),
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
        deadline = time.monotonic() + 20
        while not started.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert started.exists()
        os.killpg(process.pid, signal.SIGTERM)
        output, _ = process.communicate(timeout=30)
        result = (move.folder / "result").read_text().split("\n")
        assert result[:3] == ["restored", "copy", f"the unit was stopped; Docker runs from {move.docker_old} as before"], output
        state = move.fake.load()
        assert (state["docker"], state["unos_active"], state["containers"]["jellyfin"]) == (True, True, True)
        assert not (move.pool / ".docker").exists()

    def test_a_pool_that_is_not_mounted_changes_nothing(self, move):
        move.fake.set(mounted=False)
        done, result = run_move(move)
        assert done.returncode == 1
        assert result[:3] == ["restored", "prepare", f"{move.pool} is not mounted; Docker runs from {move.docker_old} as before"]
        assert not [call for call in move.fake.calls() if call.startswith(("systemctl", "docker stop"))]
        assert (move.docker_old / "volumes").exists()

    def test_a_failed_copy_starts_docker_as_before_and_deletes_the_pools_copies(self, move):
        # As rsync reports a full pool (the Lima VM's, 2026-11-23): the cause first, the sender's broken pipe last.
        move.fake.fail(f"rsync -aHAXx --numeric-ids --open-noatime {move.containerd_old}/",
                       "rsync: write failed on \"layer\": No space left on device (28)\n"
                       "rsync error: error in file IO (code 11) at receiver.c(657) [receiver=3.5.0]\n"
                       "rsync: [sender] write error: Broken pipe (32)")
        done, result = run_move(move)
        assert done.returncode == 1, done.stdout
        assert result[:3] == ["restored", "copy", "rsync: write failed on \"layer\": No space left on device (28); "
                                                  f"Docker runs from {move.docker_old} as before"]
        assert not (move.pool / ".docker").exists() and not (move.pool / ".containerd").exists()
        assert (move.docker_old / "volumes" / "deploy_unos-data" / "_data" / "unos.db").exists()
        assert (move.etc / "docker" / "daemon.json").read_text() == '{"log-driver": "json-file"}\n'
        state = move.fake.load()
        assert (state["docker"], state["docker_root"], state["unos_active"]) == (True, str(move.docker_old), True)
        assert state["containers"]["jellyfin"] is True

    def test_a_docker_that_does_not_answer_from_the_pool_gets_its_old_configuration_back(self, move):
        # Docker starts with the old data root: as if daemon.json had not been read.
        move.fake.set(daemon_json=str(move.etc / "docker" / "other.json"))
        done, result = run_move(move)
        assert done.returncode == 1, done.stdout
        assert result[:2] == ["restored", "verify"]
        assert result[2].startswith(f"Docker's data root is {move.docker_old}, not {move.pool}/.docker")
        assert (move.etc / "docker" / "daemon.json").read_text() == '{"log-driver": "json-file"}\n'
        assert (move.etc / "containerd" / "config.toml").read_text() == DOCKER_CE_CONFIG
        assert not (move.etc / "systemd" / "system" / "docker.service.d" / "data-root-on-pool.conf").exists()
        assert not (move.etc / "systemd" / "system" / "containerd.service.d" / "root-on-pool.conf").exists()
        assert not (move.pool / ".docker").exists()
        assert move.docker_old.exists() and move.containerd_old.exists()
        assert move.fake.load()["containers"]["unos-supervisor"] is True

    def test_an_image_that_docker_no_longer_lists_fails_the_move(self, move, tmp_path):
        stub = move.fake.bin / "docker"
        real = stub.read_text()
        # After Docker's start from the pool it lists one image less.
        stub.write_text(real.replace("#!/bin/sh\n", f'#!/bin/sh\nif [ "$1 $2" = "image ls" ] && [ -e "{move.pool}/.docker" ] '
                                                   f'&& grep -q "systemctl start docker" "{move.fake.folder}/calls"; then '
                                                   'echo sha256:aa; exit 0; fi\n', 1))
        done, result = run_move(move)
        assert result[:3] == ["restored", "verify", "Docker no longer lists images it listed before the move: sha256:bb; "
                                                    f"Docker runs from {move.docker_old} as before"], done.stdout

    def test_an_image_that_came_meanwhile_does_not_fail_the_move(self, move):
        stub = move.fake.bin / "docker"
        # After Docker's start from the pool it lists one image more: an install pulled it before the stop.
        stub.write_text(stub.read_text().replace("#!/bin/sh\n", f'#!/bin/sh\nif [ "$1 $2" = "image ls" ] && grep -q '
                                                 f'"systemctl start docker" "{move.fake.folder}/calls"; then '
                                                 'printf "sha256:aa\\nsha256:bb\\nsha256:cc\\n"; exit 0; fi\n', 1))
        done, result = run_move(move)
        assert result[0] == "moved", done.stdout

    def test_a_restore_that_fails_too_is_failed(self, move):
        move.fake.fail(f"rsync -aHAXx --numeric-ids --open-noatime {move.docker_old}/", "rsync: connection unexpectedly closed")
        move.fake.fail("systemctl start docker.socket", "Job for docker.service failed")
        done, result = run_move(move)
        assert done.returncode == 2
        assert result[:2] == ["failed", "copy"]
        # The pool's copies are kept: a restore that did not succeed may leave Docker naming them.
        assert result[2] == ("rsync: connection unexpectedly closed; then starting Docker: Job for docker.service "
                             f"failed; the copies on the pool were kept: {move.pool}/.docker {move.pool}/.containerd")
        assert (move.pool / ".docker").exists() and (move.pool / ".containerd").exists()

    def test_an_existing_subvolume_is_never_overwritten(self, move):
        (move.pool / ".docker").mkdir()
        (move.pool / ".docker" / "from-before").write_text("kept")
        done, result = run_move(move)
        assert result[:3] == ["restored", "prepare", f"{move.pool}/.docker exists already; Docker runs from "
                                                     f"{move.docker_old} as before"]
        assert (move.pool / ".docker" / "from-before").read_text() == "kept"

    def test_a_containerd_that_stays_is_neither_stopped_nor_deleted(self, move):
        files = {name: move.plan.files[name] for name in ("daemon.json", "docker.conf")}
        plan = docker_data.Plan("data", str(move.pool), str(move.docker_old), f"{move.pool}/.docker", "", "", files)
        (move.folder / "move.env").write_text(docker_data.move_settings(plan))
        done, result = run_move(move)
        assert result[0] == "moved", done.stdout
        assert move.containerd_old.exists() and not (move.pool / ".containerd").exists()
        assert "systemctl stop docker.socket docker.service" in move.fake.calls()
        assert (move.etc / "containerd" / "config.toml").read_text() == DOCKER_CE_CONFIG
