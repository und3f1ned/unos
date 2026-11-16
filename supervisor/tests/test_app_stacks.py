"""An app of several containers installs, lists, starts, stops and uninstalls as one app
(apps.compose-stacks, ADR-0381).

The app here is a synthetic one in the manner of Immich: a server (the main container) with a
generated database password, and the companions "database" (with a health check), "cache"
(without one) and "machine-learning" (with the RK3588's NPU and an image of its own where that
applies), every image pinned by tag and digest. Docker is the strict fake of docker_fake.py, the
host the fakes of test_apps.py, and the time of the waits a clock that sleeping moves on.
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
from docker.errors import APIError
from pydantic import ValidationError

from src.apps.catalog import APP_CATALOG
from src.apps.schemas import APPS_DATA_PATH, AppCategory, AppTemplate, Companion, EnvVar, PortMapping, VolumeMapping

from . import test_apps
from .docker_fake import Call, StackDocker
from .test_apps import (
    DOCKER_MASKED_PATHS,
    INSTALL,
    INSTALLED,
    NPU,
    RESTART,
    START,
    STOP,
    UNINSTALL,
    _fd_path,
    _rk3588,
    _tree,
)

# The fixtures of test_apps: the API as an administrator, the pools, the host of the SoC and port
# reads, the database of the shares and the owner changes recorded instead of made.
api = test_apps.api
pools = test_apps.pools
soc = test_apps.soc
shares = test_apps.shares
chown_calls = test_apps.chown_calls

apps_router = importlib.import_module("src.apps.router")
catalog_module = importlib.import_module("src.apps.catalog")
stacks_module = importlib.import_module("src.apps.stacks")
audit_router = importlib.import_module("src.audit.router")


def _digest(n: int) -> str:
    return "sha256:" + str(n) * 64


SERVER = f"example/photos-server:1.0@{_digest(1)}"
DATABASE = f"example/photos-db:14@{_digest(2)}"
CACHE = f"example/valkey:9@{_digest(3)}"
LEARNING = f"example/photos-ml:1.0@{_digest(4)}"
LEARNING_NPU = f"example/photos-ml:1.0-rknn@{_digest(5)}"

PASSWORD = VolumeMapping(
    host=f"{APPS_DATA_PATH}/photos/database/password",
    container="/run/secrets/db-password",
    readonly=True,
    is_file=True,
    generated_secret=True,
    secret_for=f"{APPS_DATA_PATH}/photos/database/data",
)
PHOTOS = AppTemplate(
    id="photos",
    name="Photos",
    description="Photos of four containers",
    icon="P",
    category=AppCategory.MEDIA,
    image=SERVER,
    version="1.0",
    ports=[PortMapping(host=2283, container=2283)],
    volumes=[VolumeMapping(host=f"{APPS_DATA_PATH}/photos/library", container="/data"), PASSWORD],
    environment=[
        EnvVar(name="DB_HOSTNAME", value="database", editable=False),
        EnvVar(name="DB_PASSWORD_FILE", value="/run/secrets/db-password", editable=False),
        EnvVar(name="LOG_LEVEL", value="log"),
    ],
    tmpfs={"/tmp": "size=512m"},
    depends_on=["database", "cache"],
    companions=[
        Companion(
            name="database",
            image=DATABASE,
            volumes=[
                VolumeMapping(host=f"{APPS_DATA_PATH}/photos/database/data", container="/var/lib/postgresql/data"),
                PASSWORD,
            ],
            environment=[EnvVar(name="POSTGRES_PASSWORD_FILE", value="/run/secrets/db-password")],
            tmpfs={"/var/run/postgresql": "size=1m"},
            shm_size="128m",
            stop_timeout=60,
        ),
        Companion(name="cache", image=CACHE, volumes=[VolumeMapping(host=f"{APPS_DATA_PATH}/photos/cache", container="/data")]),
        Companion(
            name="machine-learning",
            image=LEARNING,
            volumes=[VolumeMapping(host=f"{APPS_DATA_PATH}/photos/model-cache", container="/cache")],
            tmpfs={"/tmp": "size=64m"},
            hardware=[NPU.model_copy(update={"image": LEARNING_NPU})],
        ),
    ],
)
# The app's containers in their start order, and its network.
NAMES = ["photos-database", "photos-cache", "photos-machine-learning", "photos"]
NETWORK = "unos-app-photos"
CAPPED_LOG = {"Type": "json-file", "Config": {"max-size": "10m", "max-file": "3"}}
PHOTOS_INSTALL = {"template_id": "photos"}
# What the health check of an image says: Docker's State.Health.
STARTING = {"Health": {"Status": "starting", "FailingStreak": 0, "Log": []}}
HEALTHY = {"Health": {"Status": "healthy", "FailingStreak": 0, "Log": []}}


@pytest.fixture(autouse=True)
def photos(monkeypatch) -> AppTemplate:
    """The catalog that the API answers and installs, with Photos."""
    catalog = [*APP_CATALOG, PHOTOS]
    monkeypatch.setattr(catalog_module, "APP_CATALOG", catalog)
    monkeypatch.setattr(apps_router, "APP_CATALOG", catalog)
    return PHOTOS


@pytest.fixture
def docker(monkeypatch) -> StackDocker:
    """Docker, with Photos's images in the registry: the server's and the database's with health
    checks, each declaring the VOLUMEs of its upstream image."""
    fake = StackDocker()
    fake.publish(SERVER, volumes=("/data",), healthcheck=True)
    fake.publish(DATABASE, volumes=("/var/lib/postgresql/data",), healthcheck=True)
    fake.publish(CACHE, volumes=("/data",))
    fake.publish(LEARNING)
    fake.publish(LEARNING_NPU)
    monkeypatch.setattr(apps_router, "get_client", lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def clock(monkeypatch) -> SimpleNamespace:
    """The time of the waits: sleeping moves it on at once, and ``slept`` records each sleep."""
    fake = SimpleNamespace(now=1000.0, slept=[])
    fake.monotonic = lambda: fake.now

    def sleep(seconds: float) -> None:
        fake.slept.append(seconds)
        fake.now += seconds

    fake.sleep = sleep
    monkeypatch.setattr(stacks_module, "time", fake)
    return fake


def _audit() -> list[dict]:
    path = Path(audit_router.AUDIT_FILE)
    if not path.exists():
        return []
    return [
        {key: record[key] for key in ("action", "resource_type", "resource_id", "details", "severity", "success")}
        for record in (json.loads(line) for line in path.read_text().splitlines() if line.strip())
    ]


def _created(docker: StackDocker, name: str) -> Call:
    return next(c for c in docker.calls if c.name == "api.create_container" and c.subject == name)


def _installed(api, docker: StackDocker) -> None:
    """Photos installed and running; the calls of its install forgotten."""
    response = api.post(INSTALL, json=PHOTOS_INSTALL)
    assert response.status_code == 200, response.text
    docker.calls.clear()


def _password(pools) -> Path:
    return pools.alpha / "apps/photos/database/password"


def _icon(app_id: str) -> str:
    return next(t.icon for t in APP_CATALOG if t.id == app_id)


# ---------------------------------------------------------------------------
# The install
# ---------------------------------------------------------------------------


class TestInstall:
    def test_the_network_is_made_then_each_container_is_created_and_started_in_order(self, api, pools, docker):
        response = api.post(INSTALL, json=PHOTOS_INSTALL)

        assert response.status_code == 200, response.text
        assert docker.called("networks.create", "api.create_container", "container.start") == [
            ("networks.create", NETWORK),
            *[("api.create_container", name) for name in NAMES],
            *[("container.start", name) for name in NAMES],
        ]
        network = next(c for c in docker.calls if c.name == "networks.create")
        assert network.kwargs == {"driver": "bridge", "labels": {"unos.app": "photos"}, "check_duplicate": True}
        assert {name: c.status for name, c in docker.containers_.items()} == dict.fromkeys(NAMES, "running")
        # Docker created no container through its high-level create, which knows no network
        # endpoint and no stop timeout.
        assert docker.called("containers.create") == []

    def test_each_container_gets_its_labels_alias_mounts_and_settings(self, api, pools, docker):
        response = api.post(INSTALL, json=PHOTOS_INSTALL)

        assert response.status_code == 200, response.text
        apps = pools.alpha / "apps/photos"
        database = _created(docker, "photos-database")
        assert database.args == (f"example/photos-db@{_digest(2)}",)
        assert database.kwargs["labels"] == {"unos.app": "photos", "unos.app.companion": "database", "unos.app.pool": "alpha"}
        assert database.kwargs["environment"] == {"POSTGRES_PASSWORD_FILE": "/run/secrets/db-password"}
        assert database.kwargs["stop_timeout"] == 60
        assert database.kwargs["ports"] is None
        assert database.kwargs["networking_config"] == {"EndpointsConfig": {NETWORK: {"Aliases": ["database"]}}}
        assert database.kwargs["host_config"] == {
            "NetworkMode": NETWORK,
            "RestartPolicy": {"Name": "unless-stopped"},
            "LogConfig": CAPPED_LOG,
            "Mounts": [
                {"Target": "/var/lib/postgresql/data", "Source": str(apps / "database/data"), "Type": "bind",
                 "ReadOnly": False},
                {"Target": "/run/secrets/db-password", "Source": str(apps / "database/password"), "Type": "bind",
                 "ReadOnly": True},
            ],
            "Tmpfs": {"/var/run/postgresql": "size=1m"},
            "ShmSize": 128 * 1024 * 1024,
        }

        cache = _created(docker, "photos-cache")
        assert cache.args == (f"example/valkey@{_digest(3)}",)
        assert cache.kwargs["environment"] is None
        assert cache.kwargs["stop_timeout"] is None
        assert cache.kwargs["networking_config"] == {"EndpointsConfig": {NETWORK: {"Aliases": ["cache"]}}}
        assert cache.kwargs["host_config"]["Mounts"] == [
            {"Target": "/data", "Source": str(apps / "cache"), "Type": "bind", "ReadOnly": False},
        ]
        assert "Tmpfs" not in cache.kwargs["host_config"] and "ShmSize" not in cache.kwargs["host_config"]

        server = _created(docker, "photos")
        assert server.args == (f"example/photos-server@{_digest(1)}",)
        assert server.kwargs["labels"] == {
            "unos.app": "photos", "unos.app.name": "Photos", "unos.app.icon": "P", "unos.app.pool": "alpha",
        }
        assert server.kwargs["environment"] == {
            "DB_HOSTNAME": "database", "DB_PASSWORD_FILE": "/run/secrets/db-password", "LOG_LEVEL": "log",
        }
        # Only the main container publishes ports, and it has no alias.
        assert server.kwargs["ports"] == [("2283", "tcp")]
        assert server.kwargs["networking_config"] == {"EndpointsConfig": {NETWORK: {}}}
        assert server.kwargs["host_config"] == {
            "NetworkMode": NETWORK,
            "PortBindings": {"2283/tcp": [{"HostIp": "", "HostPort": "2283"}]},
            "RestartPolicy": {"Name": "unless-stopped"},
            "LogConfig": CAPPED_LOG,
            "Mounts": [
                {"Target": "/data", "Source": str(apps / "library"), "Type": "bind", "ReadOnly": False},
                {"Target": "/run/secrets/db-password", "Source": str(apps / "database/password"), "Type": "bind",
                 "ReadOnly": True},
            ],
            "Tmpfs": {"/tmp": "size=512m"},
        }
        # Off the RK3588 the machine learning runs its own image, with Docker's masks.
        learning = _created(docker, "photos-machine-learning")
        assert learning.args == (f"example/photos-ml@{_digest(4)}",)
        assert learning.kwargs["host_config"]["Tmpfs"] == {"/tmp": "size=64m"}
        assert not {"Devices", "MaskedPaths", "SecurityOpt", "Privileged", "CapAdd"} & set(learning.kwargs["host_config"])
        for name in NAMES:
            config = docker.container(name).attrs["HostConfig"]
            assert not {"Privileged", "CapAdd", "Devices", "MaskedPaths", "SecurityOpt"} & set(config), name

    def test_on_an_rk3588_the_machine_learning_gets_the_npu_and_its_image(self, api, pools, docker, soc):
        _rk3588(soc)

        response = api.post(INSTALL, json=PHOTOS_INSTALL)

        assert response.status_code == 200, response.text
        learning = _created(docker, "photos-machine-learning")
        assert learning.args == (f"example/photos-ml@{_digest(5)}",)
        config = learning.kwargs["host_config"]
        assert config["Devices"] == [{"PathOnHost": "/dev/dri", "PathInContainer": "/dev/dri", "CgroupPermissions": "rwm"}]
        assert config["MaskedPaths"] == [path for path in DOCKER_MASKED_PATHS if path != "/sys/firmware"]
        assert config["SecurityOpt"] == ["apparmor=unconfined"]
        assert config["NetworkMode"] == NETWORK
        assert learning.kwargs["networking_config"] == {"EndpointsConfig": {NETWORK: {"Aliases": ["machine-learning"]}}}
        # Its own image was never pulled.
        assert ("images.pull", f"example/photos-ml@{_digest(4)}") not in docker.called("images.pull")
        companions = {c["name"]: c for c in response.json()["companions"]}
        assert companions["machine-learning"]["devices"] == ["/dev/dri"]
        assert companions["machine-learning"]["image"] == f"example/photos-ml@{_digest(5)}"
        assert response.json()["devices"] == []

    def test_the_images_are_pulled_by_digest_and_tagged(self, api, pools, docker):
        response = api.post(INSTALL, json=PHOTOS_INSTALL)

        assert response.status_code == 200, response.text
        refs = [(DATABASE, "example/photos-db", "14", 2), (CACHE, "example/valkey", "9", 3),
                (LEARNING, "example/photos-ml", "1.0", 4), (SERVER, "example/photos-server", "1.0", 1)]
        assert [(c.name, c.subject, c.args, c.kwargs) for c in docker.calls if c.name in ("images.pull", "api.tag")] == [
            item
            for _, name, tag, n in refs
            for item in (
                ("images.pull", f"{name}@{_digest(n)}", (name,), {"tag": _digest(n)}),
                ("api.tag", f"{name}@{_digest(n)}", (name,), {"tag": tag}),
            )
        ]

    def test_an_image_that_docker_has_is_not_pulled(self, api, pools, docker):
        docker.pulled(DATABASE)

        response = api.post(INSTALL, json=PHOTOS_INSTALL)

        assert response.status_code == 200, response.text
        assert ("images.pull", f"example/photos-db@{_digest(2)}") not in docker.called("images.pull")
        assert len(docker.called("images.pull")) == 3

    def test_a_tag_that_fails_only_logs_a_warning(self, api, pools, docker, caplog):
        docker.fail("api.tag", None, APIError("500 Server Error", explanation="tag refused"))

        with caplog.at_level("WARNING", logger="src.apps.router"):
            response = api.post(INSTALL, json=PHOTOS_INSTALL)

        assert response.status_code == 200, response.text
        warnings = [r.getMessage() for r in caplog.records if r.name == "src.apps.router"]
        assert len(warnings) == 4
        assert warnings[0].startswith(f"Could not tag example/photos-db@{_digest(2)} as example/photos-db:14: ")

    def test_the_main_container_starts_only_after_the_companions_it_depends_on_are_ready(
        self, api, pools, docker, clock
    ):
        docker.script("photos-database", STARTING, STARTING, HEALTHY)

        response = api.post(INSTALL, json=PHOTOS_INSTALL)

        assert response.status_code == 200, response.text
        calls = docker.called("container.start", "container.reload")
        start = calls.index(("container.start", "photos"))
        # The database was looked at until healthy, and the cache, which has no health check,
        # until it had run for two seconds; the machine learning is waited for by nobody.
        assert calls[:start] == [
            *[("container.start", name) for name in NAMES[:3]],
            *[("container.reload", "photos-database")] * 3,
            *[("container.reload", "photos-cache")] * 2,
        ]
        assert clock.slept == [2, 2, 2]

    def test_the_answer_names_every_container_and_tags_each_volume(self, api, pools, docker):
        response = api.post(INSTALL, json=PHOTOS_INSTALL)

        assert response.status_code == 200, response.text
        apps = pools.alpha / "apps/photos"
        server = docker.container("photos")
        body = response.json()
        assert {key: body[key] for key in ("id", "name", "container_id", "status", "web_url", "pool", "health")} == {
            "id": server.short_id, "name": "photos", "container_id": server.short_id, "status": "running",
            "web_url": "http://localhost:2283", "pool": "alpha", "health": "starting",
        }
        assert body["companions"] == [
            {
                "name": name.removeprefix("photos-"),
                "container": name,
                "container_id": docker.container(name).short_id,
                "status": "running",
                "health": health,
                "image": image,
                "devices": [],
            }
            for name, health, image in [
                ("photos-database", "healthy", f"example/photos-db@{_digest(2)}"),
                ("photos-cache", None, f"example/valkey@{_digest(3)}"),
                ("photos-machine-learning", None, f"example/photos-ml@{_digest(4)}"),
            ]
        ]
        assert body["volumes"] == [
            {"host": str(apps / "library"), "container": "/data", "readonly": False, "companion": None},
            {"host": str(apps / "database/password"), "container": "/run/secrets/db-password", "readonly": True,
             "companion": None},
            {"host": str(apps / "database/data"), "container": "/var/lib/postgresql/data", "readonly": False,
             "companion": "database"},
            {"host": str(apps / "database/password"), "container": "/run/secrets/db-password", "readonly": True,
             "companion": "database"},
            {"host": str(apps / "cache"), "container": "/data", "readonly": False, "companion": "cache"},
            {"host": str(apps / "model-cache"), "container": "/cache", "readonly": False,
             "companion": "machine-learning"},
        ]
        for folder in ("library", "database/data", "cache", "model-cache"):
            assert (apps / folder).is_dir(), folder

    def test_the_audit_entry_names_the_containers_and_the_password_file(self, api, pools, docker):
        response = api.post(INSTALL, json=PHOTOS_INSTALL)

        assert response.status_code == 200, response.text
        assert _audit() == [{
            "action": "app_installed", "resource_type": "app", "resource_id": "photos",
            "details": "Installed Photos 1.0 from the catalog as photos with the containers photos-database, "
            "photos-cache, photos-machine-learning and photos, with its data on the pool alpha; generated its "
            f"password file {_password(pools)}",
            "severity": "info", "success": True,
        }]

    def test_an_app_of_one_container_takes_no_network(self, api, pools, docker, soc):
        docker.publish("amir20/dozzle:latest")

        response = api.post(INSTALL, json={"template_id": "dozzle"})

        assert response.status_code == 200, response.text
        assert docker.called("containers.list", "networks.list", "networks.create", "images.pull",
                             "containers.create", "container.start") == [
            ("containers.list", None),
            ("images.pull", "amir20/dozzle:latest"),
            ("containers.create", "dozzle"),
            ("container.start", "dozzle"),
        ]
        assert docker.container("dozzle").labels == {
            "unos.app": "dozzle", "unos.app.name": "Dozzle", "unos.app.icon": _icon("dozzle"),
        }
        assert response.json()["companions"] == [] and response.json()["health"] is None
        assert _audit()[0]["details"] == "Installed Dozzle from the catalog as dozzle"
        docker.calls.clear()

        assert api.delete(UNINSTALL.format("dozzle")).status_code == 200
        assert docker.called("networks.list", "network.remove") == []
        assert _audit()[-1]["details"] == "Uninstalled the app dozzle; its data folders were kept"

    def test_an_app_of_one_container_with_a_stop_timeout_is_made_through_the_low_level_api(
        self, api, pools, docker, monkeypatch
    ):
        # docker-py's high-level create takes no stop timeout: the fake refuses it as docker-py does.
        alone = PHOTOS.model_copy(update={
            "id": "alone", "name": "Alone", "companions": [], "depends_on": [], "stop_timeout": 30,
            "volumes": [VolumeMapping(host=f"{APPS_DATA_PATH}/alone/data", container="/data")],
        })
        catalog = [*APP_CATALOG, alone]
        monkeypatch.setattr(catalog_module, "APP_CATALOG", catalog)
        monkeypatch.setattr(apps_router, "APP_CATALOG", catalog)

        response = api.post(INSTALL, json={"template_id": "alone"})

        assert response.status_code == 200, response.text
        created = _created(docker, "alone")
        assert created.kwargs["stop_timeout"] == 30
        assert created.kwargs["networking_config"] is None
        assert created.kwargs["host_config"]["NetworkMode"] == "default"
        assert created.kwargs["host_config"]["Tmpfs"] == {"/tmp": "size=512m"}
        assert docker.called("containers.create", "networks.create") == []
        docker.calls.clear()

        assert api.post(STOP.format("alone")).status_code == 200
        assert [c.kwargs for c in docker.calls if c.name == "container.stop"] == [{"timeout": 30}]


# ---------------------------------------------------------------------------
# A failed install leaves nothing in Docker
# ---------------------------------------------------------------------------


def _left_nothing(pools, docker: StackDocker) -> None:
    """Nothing of the app is left in Docker; its folders, its password file and the images stay."""
    assert docker.containers_ == {}
    assert docker.networks_ == {}
    assert (pools.alpha / "apps/photos/database/data").is_dir()
    assert _password(pools).is_file()
    assert len(docker.images_) == 4
    # The removals, forced and in reverse order.
    assert all(c.kwargs == {"v": False, "force": True} for c in docker.calls if c.name == "container.remove")
    removed = [subject for _, subject in docker.called("container.remove")]
    assert removed == sorted(removed, key=NAMES.index, reverse=True)


class TestRollback:
    def test_a_pull_that_fails_creates_nothing(self, api, pools, docker):
        error = APIError("500 Server Error", explanation="pull access denied")
        docker.fail("images.pull", f"example/valkey@{_digest(3)}", error)

        response = api.post(INSTALL, json=PHOTOS_INSTALL)

        assert (response.status_code, response.json()) == (500, {"detail": f"Failed to pull image {CACHE}: {error}"})
        assert docker.called("networks.create", "api.create_container") == []
        assert not (pools.alpha / "apps").exists()
        assert _audit() == []

    @pytest.mark.parametrize("failing", NAMES)
    def test_a_create_that_fails_removes_what_the_install_made(self, api, pools, docker, failing):
        docker.fail("api.create_container", failing, APIError("500 Server Error", explanation="no space left on device"))

        response = api.post(INSTALL, json=PHOTOS_INSTALL)

        assert response.status_code == 500
        assert response.json()["detail"] == (
            f"Failed to create container {failing}: 500 Server Error (\"no space left on device\"); "
            "its containers were removed and its data folders kept"
        )
        assert [subject for _, subject in docker.called("container.remove")] == list(
            reversed(NAMES[: NAMES.index(failing)])
        )
        assert docker.called("network.remove") == [("network.remove", NETWORK)]
        _left_nothing(pools, docker)
        assert _audit() == []

    def test_a_network_that_cannot_be_made_creates_nothing(self, api, pools, docker):
        docker.fail("networks.create", NETWORK, APIError("500 Server Error", explanation="no address pool left"))

        response = api.post(INSTALL, json=PHOTOS_INSTALL)

        assert response.status_code == 500
        assert response.json()["detail"].startswith(f"Failed to create the network {NETWORK}: ")
        assert docker.called("api.create_container") == []
        _left_nothing(pools, docker)

    def test_a_start_that_fails_removes_every_container(self, api, pools, docker):
        docker.fail("container.start", "photos-cache", APIError("500 Server Error", explanation="port is already allocated"))

        response = api.post(INSTALL, json=PHOTOS_INSTALL)

        assert response.status_code == 500
        assert response.json()["detail"] == (
            "Failed to start container photos-cache: 500 Server Error (\"port is already allocated\"); "
            "its containers were removed and its data folders kept"
        )
        assert [subject for _, subject in docker.called("container.remove")] == list(reversed(NAMES))
        _left_nothing(pools, docker)

    @pytest.mark.parametrize(("states", "reason"), [
        ([STARTING], "photos-database did not become ready within 180 s"),
        ([STARTING, {"Health": {"Status": "unhealthy", "FailingStreak": 3, "Log": [
            {"ExitCode": 2, "Output": "/var/run/postgresql:5432 - no response\r\n"}]}}],
         "photos-database is unhealthy: /var/run/postgresql:5432 - no response"),
        ([{"Status": "exited", "Running": False, "ExitCode": 1}], "photos-database stopped (exit code 1)"),
    ], ids=["timeout", "unhealthy", "exited"])
    def test_a_companion_that_does_not_become_ready_removes_every_container(
        self, api, pools, docker, clock, states, reason
    ):
        docker.script("photos-database", *states)

        response = api.post(INSTALL, json=PHOTOS_INSTALL)

        assert (response.status_code, response.json()) == (
            500, {"detail": f"{reason}; its containers were removed and its data folders kept"}
        )
        assert ("container.start", "photos") not in docker.called("container.start")
        _left_nothing(pools, docker)
        if reason.endswith("180 s"):
            assert sum(clock.slept) == 180
        else:
            assert sum(clock.slept) <= 2

    def test_a_clean_up_that_fails_is_answered_and_audited(self, api, pools, docker):
        docker.fail("container.start", "photos-database", APIError("500 Server Error", explanation="no memory"))
        docker.fail("container.remove", "photos-cache", APIError("500 Server Error", explanation="device busy"))

        response = api.post(INSTALL, json=PHOTOS_INSTALL)

        reason = 'Failed to start container photos-database: 500 Server Error ("no memory")'
        assert (response.status_code, response.json()) == (500, {
            "detail": f'{reason}; then could not remove photos-cache: 500 Server Error ("device busy")'
        })
        assert set(docker.containers_) == {"photos-cache"} and docker.networks_ == {}
        assert _audit() == [{
            "action": "app_installed", "resource_type": "app", "resource_id": "photos",
            "details": f"Created photos-database, photos-cache, photos-machine-learning, photos and the network "
            f"{NETWORK}; then: {reason}; left behind: photos-cache",
            "severity": "info", "success": False,
        }]


# ---------------------------------------------------------------------------
# The password file
# ---------------------------------------------------------------------------


class TestPassword:
    def test_it_is_made_once_with_mode_0600_through_a_temporary_file(self, api, pools, docker, monkeypatch):
        links: list[tuple] = []
        real_link = os.link

        def link(src, dst, *, src_dir_fd=None, dst_dir_fd=None, follow_symlinks=True):
            links.append((src, dst, _fd_path(dst_dir_fd), follow_symlinks))
            return real_link(src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd, follow_symlinks=follow_symlinks)

        monkeypatch.setattr(os, "link", link)

        response = api.post(INSTALL, json=PHOTOS_INSTALL)

        assert response.status_code == 200, response.text
        password = _password(pools)
        assert stat.S_IMODE(password.stat().st_mode) == 0o600
        content = password.read_text()
        assert len(content) == 64 and all(ch in "0123456789abcdef" for ch in content)
        # Written to a temporary file of the same folder, linked to its name without following
        # links; the temporary file is gone.
        [(src, dst, folder, follow)] = links
        assert src.startswith(".password.") and src.endswith(".tmp")
        assert (dst, folder, follow) == ("password", password.parent, False)
        assert sorted(os.listdir(password.parent)) == ["data", "password"]

    def test_it_is_on_the_disk_before_any_container_that_reads_it_is_created(self, api, pools, docker, monkeypatch):
        real_fsync = os.fsync

        def fsync(fd):
            docker.calls.append(Call("os.fsync", str(_fd_path(fd)), (), {}))
            return real_fsync(fd)

        monkeypatch.setattr(os, "fsync", fsync)

        response = api.post(INSTALL, json=PHOTOS_INSTALL)

        assert response.status_code == 200, response.text
        calls = docker.called("os.fsync", "api.create_container")
        folder = str(_password(pools).parent)
        first = calls.index(("api.create_container", "photos-database"))
        # The temporary file, then its folder once the file has its name, before the first create.
        synced = [subject for name, subject in calls[:first] if name == "os.fsync"]
        assert len(synced) == 2, synced
        assert synced[0].startswith(f"{folder}/.password.") and synced[1] == folder

    def test_one_that_is_there_is_used_again(self, api, pools, docker):
        password = _password(pools)
        password.parent.mkdir(parents=True)
        password.write_text("f" * 64)
        (password.parent / "data").mkdir()
        (password.parent / "data/PG_VERSION").write_text("14\n")

        response = api.post(INSTALL, json=PHOTOS_INSTALL)

        assert response.status_code == 200, response.text
        assert password.read_text() == "f" * 64
        assert "generated" not in _audit()[0]["details"]

    def test_one_that_is_missing_beside_an_empty_data_folder_is_made(self, api, pools, docker):
        (pools.alpha / "apps/photos/database/data").mkdir(parents=True)

        response = api.post(INSTALL, json=PHOTOS_INSTALL)

        assert response.status_code == 200, response.text
        assert _password(pools).is_file()

    def test_one_that_is_missing_beside_data_is_refused_before_any_pull(self, api, pools, docker):
        data = pools.alpha / "apps/photos/database/data"
        data.mkdir(parents=True)
        (data / "PG_VERSION").write_text("14\n")

        response = api.post(INSTALL, json=PHOTOS_INSTALL)

        assert (response.status_code, response.json()) == (409, {
            "detail": f"{data} holds the data of Photos, but its password file {_password(pools)} is missing: "
            f"restore the file from a snapshot of the pool, or move {data} away to install Photos with empty data."
        })
        assert not _password(pools).exists()
        assert docker.calls == []

    @pytest.mark.parametrize("damage", ["link", "empty", "directory", "characters", "too-long"])
    def test_one_that_is_not_a_password_unos_made_is_refused_unshown_before_any_pull(
        self, api, pools, docker, damage
    ):
        password = _password(pools)
        password.parent.mkdir(parents=True)
        secret = "k7-Unique pass"
        if damage == "link":
            (pools.outside / "secret").write_text("f" * 64)
            password.symlink_to(pools.outside / "secret")
        elif damage == "empty":
            password.write_text("")
        elif damage == "directory":
            password.mkdir()
        elif damage == "characters":
            password.write_text(secret)
        else:
            password.write_text("a" * 257)
        before = _tree(pools.root)

        response = api.post(INSTALL, json=PHOTOS_INSTALL)

        data = password.parent / "data"
        assert (response.status_code, response.json()) == (409, {
            "detail": f"{password} is not a password UNOS made (a link, empty, too long, or not letters and "
            "digits). UNOS neither shows nor replaces it: restore it from a snapshot of the pool, or, if "
            f"{data} is empty, remove it and install the app again."
        })
        assert secret not in response.text and "f" * 64 not in response.text
        assert docker.calls == []
        assert _tree(pools.root) == before

    def test_it_cannot_be_moved(self, api, pools, docker):
        response = api.post(
            INSTALL, json={**PHOTOS_INSTALL, "volume_overrides": {"/run/secrets/db-password": str(pools.alpha / "pw")}}
        )

        assert (response.status_code, response.json()) == (400, {
            "detail": "/run/secrets/db-password is Photos's password file, which UNOS keeps at "
            "{pool}/apps/photos/database/password: it cannot be moved"
        })
        assert docker.calls == []

    def test_its_value_is_nowhere_but_in_the_file(self, api, pools, docker, soc, caplog):
        _rk3588(soc)
        with caplog.at_level(logging.DEBUG):
            installed = api.post(INSTALL, json=PHOTOS_INSTALL)
            attrs = repr([c.attrs for c in docker.containers_.values()])
            listed = api.get(INSTALLED)
            stopped = api.post(STOP.format("photos"))
            started = api.post(START.format("photos"))
            uninstalled = api.delete(UNINSTALL.format("photos"))

        assert [r.status_code for r in (installed, listed, stopped, started, uninstalled)] == [200] * 5
        password = _password(pools).read_text()
        for where in (
            repr(docker.calls),
            caplog.text,
            Path(audit_router.AUDIT_FILE).read_text(),
            installed.text,
            listed.text,
            attrs,
        ):
            assert password not in where

    def test_a_start_without_it_is_refused_and_does_not_make_it(self, api, pools, docker):
        _installed(api, docker)
        assert api.post(STOP.format("photos")).status_code == 200
        _password(pools).unlink()
        docker.calls.clear()

        response = api.post(START.format("photos"))

        data = pools.alpha / "apps/photos/database/data"
        assert (response.status_code, response.json()) == (409, {
            "detail": f"{_password(pools)} is missing; it holds the password that the data in {data} was set up "
            f"with. Restore it from a snapshot of the pool, or uninstall the app, move {data} away and install it "
            "again."
        })
        assert not _password(pools).exists()
        assert docker.called("container.start") == []


# ---------------------------------------------------------------------------
# What an install checks before it pulls anything
# ---------------------------------------------------------------------------


class TestChecksBeforeThePull:
    def test_an_installed_app_is_not_installed_again(self, api, pools, docker):
        _installed(api, docker)

        response = api.post(INSTALL, json=PHOTOS_INSTALL)

        assert (response.status_code, response.json()) == (409, {"detail": "Photos is already installed"})
        assert docker.called() == [("containers.list", None)]

    def test_an_incomplete_app_is_not_installed_again(self, api, pools, docker):
        _installed(api, docker)
        docker.container("photos-cache").remove(force=True)
        docker.calls.clear()

        response = api.post(INSTALL, json=PHOTOS_INSTALL)

        assert (response.status_code, response.json()) == (409, {
            "detail": "Photos is already installed, incomplete (missing: photos-cache): uninstall it first "
            "(its data folders are kept)"
        })
        assert docker.called("images.pull", "api.create_container") == []

    def test_a_container_with_one_of_its_names_is_refused(self, api, pools, docker):
        docker.pulled("example/other:1")
        docker.containers.create("example/other:1", name="photos-cache")
        docker.calls.clear()

        response = api.post(INSTALL, json=PHOTOS_INSTALL)

        assert (response.status_code, response.json()) == (
            409, {"detail": "A container named 'photos-cache' already exists"}
        )
        assert docker.called("images.pull") == []

    @pytest.mark.parametrize("labels", [{}, {"unos.app": "other"}], ids=["unlabelled", "another-apps"])
    def test_a_network_of_its_name_that_is_not_its_own_is_refused(self, api, pools, docker, labels):
        docker.network(NETWORK, labels=labels)

        response = api.post(INSTALL, json=PHOTOS_INSTALL)

        assert (response.status_code, response.json()) == (
            409, {"detail": f"A Docker network named '{NETWORK}' already exists"}
        )
        assert docker.called("images.pull") == []
        assert not (pools.alpha / "apps").exists()

    def test_a_network_whose_name_merely_begins_like_its_own_counts_for_nothing(self, api, pools, docker):
        docker.network(f"{NETWORK}-old")

        response = api.post(INSTALL, json=PHOTOS_INSTALL)

        assert response.status_code == 200, response.text
        assert docker.called("networks.create") == [("networks.create", NETWORK)]

    def test_its_own_network_left_from_before_is_used_again(self, api, pools, docker):
        docker.network(NETWORK, labels={"unos.app": "photos"})

        response = api.post(INSTALL, json=PHOTOS_INSTALL)

        assert response.status_code == 200, response.text
        assert docker.called("networks.create") == []
        assert set(docker.networks_[NETWORK].containers) == {c.id for c in docker.containers_.values()}

    def test_its_own_network_that_another_container_uses_is_refused(self, api, pools, docker):
        docker.network(NETWORK, labels={"unos.app": "photos"})
        docker.pulled("example/other:1")
        docker.containers.create("example/other:1", name="intruder", network_mode=NETWORK).start()

        response = api.post(INSTALL, json=PHOTOS_INSTALL)

        assert (response.status_code, response.json()) == (409, {
            "detail": f"The Docker network '{NETWORK}' of Photos is used by intruder: remove them first"
        })

    def test_a_port_that_a_container_publishes_is_refused(self, api, pools, docker):
        docker.pulled("example/other:1")
        docker.containers.create("example/other:1", name="other", ports={"2283/tcp": 2283})

        response = api.post(INSTALL, json=PHOTOS_INSTALL)

        assert (response.status_code, response.json()) == (409, {
            "detail": "Port 2283/tcp is already used by the container other (created): "
            "choose another host port for 2283/tcp"
        })
        assert docker.called("images.pull") == []

    @pytest.mark.parametrize("call", ["containers.list", "networks.list"])
    def test_a_docker_that_does_not_answer_answers_503(self, api, pools, docker, call):
        error = requests.exceptions.ConnectionError("('Connection aborted.', ConnectionResetError(104, 'reset'))")
        docker.fail(call, None, error)

        response = api.post(INSTALL, json=PHOTOS_INSTALL)

        assert (response.status_code, response.json()) == (503, {"detail": f"Docker is not available: {error}"})
        assert docker.called("images.pull") == []

    def test_another_change_of_the_app_under_way_is_refused(self, api, pools, docker):
        with stacks_module.app_lock("photos", "Photos"):
            response = api.post(INSTALL, json=PHOTOS_INSTALL)

        assert (response.status_code, response.json()) == (
            409, {"detail": "Another change of Photos is under way; try again when it has finished"}
        )
        assert docker.calls == []
        # The lock is free again.
        assert api.post(INSTALL, json=PHOTOS_INSTALL).status_code == 200

    def test_an_image_volume_that_is_neither_on_the_pool_nor_in_ram_is_refused_before_any_write(
        self, api, pools, docker
    ):
        docker.publish(LEARNING, volumes=("/cache", "/root/.config"))

        response = api.post(INSTALL, json=PHOTOS_INSTALL)

        assert (response.status_code, response.json()) == (500, {
            "detail": f"The image {LEARNING} declares the volume /root/.config, which the template of Photos "
            "places neither on the pool nor in RAM; nothing was installed"
        })
        assert docker.called("networks.create", "api.create_container") == []
        assert not (pools.alpha / "apps").exists()

    def test_a_name_in_the_request_is_refused(self, api, pools, docker):
        response = api.post(INSTALL, json={**PHOTOS_INSTALL, "name": "photos2"})

        assert response.status_code == 422
        assert docker.calls == []

    @pytest.mark.parametrize(("overrides", "detail"), [
        ({"DB_HOSTNAME": "elsewhere"}, "DB_HOSTNAME is set by UNOS and cannot be changed"),
        ({"DB_HOSTNAME": "x", "DB_PASSWORD_FILE": "/x", "LOG_LEVEL": "debug"},
         "DB_HOSTNAME and DB_PASSWORD_FILE are set by UNOS and cannot be changed"),
    ])
    def test_wiring_that_unos_sets_cannot_be_changed(self, api, pools, docker, overrides, detail):
        response = api.post(INSTALL, json={**PHOTOS_INSTALL, "env_overrides": overrides})

        assert (response.status_code, response.json()) == (400, {"detail": detail})
        assert docker.calls == []

    def test_a_variable_that_unos_does_not_set_can_be_changed(self, api, pools, docker):
        response = api.post(INSTALL, json={**PHOTOS_INSTALL, "env_overrides": {"LOG_LEVEL": "debug"}})

        assert response.status_code == 200, response.text
        assert _created(docker, "photos").kwargs["environment"]["LOG_LEVEL"] == "debug"
        # Companions take no override.
        assert _created(docker, "photos-database").kwargs["environment"] == {
            "POSTGRES_PASSWORD_FILE": "/run/secrets/db-password"
        }

    def test_an_app_with_companions_does_not_run_in_the_group(self, api, pools, docker):
        response = api.post(INSTALL, json={**PHOTOS_INSTALL, "run_in_unos_smb": True})

        assert (response.status_code, response.json()) == (400, {
            "detail": "Photos cannot run in the group unos-smb: it runs in several containers, and the group "
            "would reach only one of them"
        })
        assert PHOTOS.can_run_in_unos_smb is False

    def test_a_folder_of_a_companion_inside_a_writable_folder_of_another_is_refused(self, api, pools, docker):
        response = api.post(
            INSTALL, json={**PHOTOS_INSTALL, "volume_overrides": {"/data": str(pools.alpha / "apps/photos")}}
        )

        assert response.status_code == 400
        assert response.json()["detail"].startswith(
            f"The folder for /var/lib/postgresql/data of photos-database ({pools.alpha / 'apps/photos/database/data'}) "
            f"is inside the folder for /data of photos ({pools.alpha / 'apps/photos'}), which Photos can write to."
        )
        assert docker.calls == []


# ---------------------------------------------------------------------------
# The installed list
# ---------------------------------------------------------------------------


class TestInstalledList:
    def test_the_app_is_listed_once_with_its_companions_in_order(self, api, pools, docker, soc):
        _rk3588(soc)
        _installed(api, docker)

        response = api.get(INSTALLED)

        assert response.status_code == 200, response.text
        [app] = response.json()
        apps = pools.alpha / "apps/photos"
        server = docker.container("photos")
        assert {key: app[key] for key in ("id", "template_id", "name", "container_id", "status", "pool", "health",
                                           "missing", "devices", "data_outside_pools", "runs_in_unos_smb")} == {
            "id": server.short_id, "template_id": "photos", "name": "photos", "container_id": server.short_id,
            "status": "running", "pool": "alpha", "health": "starting", "missing": [], "devices": [],
            "data_outside_pools": [], "runs_in_unos_smb": False,
        }
        assert [(c["name"], c["container"], c["status"], c["devices"]) for c in app["companions"]] == [
            ("database", "photos-database", "running", []),
            ("cache", "photos-cache", "running", []),
            ("machine-learning", "photos-machine-learning", "running", ["/dev/dri"]),
        ]
        assert [(v["container"], v["companion"]) for v in app["volumes"]] == [
            ("/data", None), ("/run/secrets/db-password", None),
            ("/run/secrets/db-password", "database"), ("/var/lib/postgresql/data", "database"),
            ("/data", "cache"), ("/cache", "machine-learning"),
        ]
        assert app["volumes"][0]["host"] == str(apps / "library")
        assert app["web_port"] == 2283

    def test_an_app_without_its_main_container_is_listed_as_missing(self, api, pools, docker):
        _installed(api, docker)
        docker.container("photos").remove(force=True)
        docker.container("photos-cache").remove(force=True)

        [app] = api.get(INSTALLED).json()

        database = docker.container("photos-database")
        assert (app["name"], app["status"], app["container_id"], app["missing"]) == (
            "photos", "missing", database.short_id, ["photos-cache", "photos"]
        )
        assert [c["name"] for c in app["companions"]] == ["database", "machine-learning"]
        assert app["volumes"][0]["companion"] == "database"

    def test_apps_of_one_container_stay_apps_of_their_own(self, api, pools, docker):
        _installed(api, docker)
        docker.pulled("example/other:1")
        # Transmission, and a container that an earlier install named after itself.
        docker.containers.create("example/other:1", name="transmission", labels={"unos.app": "transmission"})
        docker.containers.create("example/other:1", name="logs", labels={"unos.app": "dozzle"})

        listed = {app["name"]: app for app in api.get(INSTALLED).json()}

        assert sorted(listed) == ["logs", "photos", "transmission"]
        for name in ("logs", "transmission"):
            assert (listed[name]["companions"], listed[name]["missing"], listed[name]["status"]) == ([], [], "created")
        assert listed["logs"]["template_id"] == "dozzle"


# ---------------------------------------------------------------------------
# Start, stop, restart and uninstall act on the whole app
# ---------------------------------------------------------------------------


class TestActions:
    def test_a_stop_stops_every_container_in_reverse_order_with_its_timeout(self, api, pools, docker):
        _installed(api, docker)

        response = api.post(STOP.format("photos"))

        assert (response.status_code, response.json()) == (200, {"status": "exited", "id": "photos"})
        assert [(c.subject, c.kwargs) for c in docker.calls if c.name == "container.stop"] == [
            ("photos", {"timeout": 10}),
            ("photos-machine-learning", {"timeout": 10}),
            ("photos-cache", {"timeout": 10}),
            ("photos-database", {"timeout": 60}),
        ]
        assert _audit()[-1] == {
            "action": "container_stopped", "resource_type": "container", "resource_id": "photos",
            "details": "Stopped the app photos (photos, photos-machine-learning, photos-cache, photos-database)",
            "severity": "info", "success": True,
        }

    def test_a_start_checks_every_container_then_starts_them_in_order(self, api, pools, docker, clock):
        _installed(api, docker)
        assert api.post(STOP.format("photos")).status_code == 200
        docker.calls.clear()
        clock.slept.clear()

        response = api.post(START.format("photos"))

        assert (response.status_code, response.json()) == (200, {"status": "running", "id": "photos"})
        assert docker.called("container.start") == [("container.start", name) for name in NAMES]
        calls = docker.called("container.start", "container.reload")
        assert calls.index(("container.reload", "photos-cache")) < calls.index(("container.start", "photos"))
        assert _audit()[-1]["details"] == (
            "Started the app photos (photos-database, photos-cache, photos-machine-learning, photos)"
        )

    def test_a_start_names_only_what_it_started(self, api, pools, docker):
        _installed(api, docker)
        docker.container("photos-machine-learning").stop()
        docker.calls.clear()

        response = api.post(START.format("photos"))

        assert response.status_code == 200, response.text
        assert docker.called("container.start") == [("container.start", "photos-machine-learning")]
        assert _audit()[-1]["details"] == "Started the app photos (photos-machine-learning)"

    def test_an_unmounted_pool_refuses_the_start_before_any_container_starts(self, api, pools, docker):
        _installed(api, docker)
        assert api.post(STOP.format("photos")).status_code == 200
        docker.calls.clear()
        pools.mounted.discard(str(pools.alpha))

        response = api.post(START.format("photos"))

        assert response.status_code == 409
        assert "Mount the pool ('alpha' holds the app's data)" in response.json()["detail"]
        assert docker.called("container.start") == []

    def test_a_restart_stops_every_container_then_starts_them_all(self, api, pools, docker):
        _installed(api, docker)

        response = api.post(RESTART.format("photos"))

        assert (response.status_code, response.json()) == (200, {"status": "running", "id": "photos"})
        assert docker.called("container.stop", "container.start") == [
            *[("container.stop", name) for name in reversed(NAMES)],
            *[("container.start", name) for name in NAMES],
        ]
        assert _audit()[-1]["details"] == (
            "Restarted the app photos (photos-database, photos-cache, photos-machine-learning, photos)"
        )

    def test_a_restart_that_cannot_start_stops_nothing(self, api, pools, docker):
        _installed(api, docker)
        pools.mounted.discard(str(pools.alpha))

        response = api.post(RESTART.format("photos"))

        assert response.status_code == 409
        assert docker.called("container.stop", "container.start") == []

    @pytest.mark.parametrize(("method", "path"), [("POST", STOP), ("POST", RESTART), ("DELETE", UNINSTALL)])
    def test_a_companion_names_the_whole_app(self, api, pools, docker, method, path):
        _installed(api, docker)

        response = api.request(method, path.format("photos-cache"))

        assert response.status_code == 200, response.text
        assert {subject for _, subject in docker.called("container.stop")} == set(NAMES)

    def test_an_incomplete_app_does_not_start(self, api, pools, docker):
        _installed(api, docker)
        assert api.post(STOP.format("photos")).status_code == 200
        docker.container("photos-cache").remove()
        docker.calls.clear()

        for path in (START, RESTART):
            response = api.post(path.format("photos"))
            assert (response.status_code, response.json()) == (409, {
                "detail": "Photos is incomplete (missing: photos-cache): uninstall it (its data folders are kept) "
                "and install it again"
            })
        assert docker.called("container.start", "container.stop") == []

    def test_an_uninstall_removes_the_containers_and_the_network_and_keeps_the_rest(self, api, pools, docker):
        _installed(api, docker)
        before = _tree(pools.root)

        response = api.delete(UNINSTALL.format("photos"))

        assert (response.status_code, response.json()) == (200, {"status": "removed", "id": "photos"})
        assert docker.containers_ == {} and docker.networks_ == {}
        assert [(c.subject, c.kwargs) for c in docker.calls if c.name == "container.remove"] == [
            (name, {"v": False, "force": False}) for name in reversed(NAMES)
        ]
        assert _tree(pools.root) == before
        assert len(docker.images_) == 4
        assert _audit()[-1] == {
            "action": "app_uninstalled", "resource_type": "app", "resource_id": "photos",
            "details": "Uninstalled the app photos: removed its containers photos, photos-machine-learning, "
            f"photos-cache, photos-database and its network {NETWORK}; its data folders were kept",
            "severity": "warning", "success": True,
        }

    def test_an_uninstall_leaves_a_network_that_another_container_uses(self, api, pools, docker):
        _installed(api, docker)
        docker.pulled("example/other:1")
        docker.containers.create("example/other:1", name="intruder", network_mode=NETWORK).start()

        response = api.delete(UNINSTALL.format("photos"))

        assert response.status_code == 200, response.text
        assert set(docker.containers_) == {"intruder"} and set(docker.networks_) == {NETWORK}
        assert _audit()[-1]["details"] == (
            "Uninstalled the app photos: removed its containers photos, photos-machine-learning, photos-cache, "
            f"photos-database; its data folders were kept, the network {NETWORK} was left"
        )

    def test_an_incomplete_app_is_uninstalled(self, api, pools, docker):
        _installed(api, docker)
        docker.container("photos").remove(force=True)

        response = api.delete(UNINSTALL.format("photos-database"))

        assert response.status_code == 200, response.text
        assert docker.containers_ == {} and docker.networks_ == {}

    def test_a_start_that_fails_part_way_is_audited_as_failed(self, api, pools, docker):
        _installed(api, docker)
        assert api.post(STOP.format("photos")).status_code == 200
        docker.fail("container.start", "photos-machine-learning", APIError("500 Server Error", explanation="no memory"))

        response = api.post(START.format("photos"))

        failure = 'Failed to start photos-machine-learning: 500 Server Error ("no memory")'
        assert (response.status_code, response.json()) == (500, {"detail": failure})
        assert docker.container("photos-database").status == "running"
        assert _audit()[-1] == {
            "action": "container_started", "resource_type": "container", "resource_id": "photos",
            "details": f"Started photos-database, photos-cache; then: {failure}", "severity": "info",
            "success": False,
        }

    def test_an_uninstall_that_fails_part_way_is_audited_as_failed(self, api, pools, docker):
        _installed(api, docker)
        docker.fail("container.remove", "photos-cache", APIError("500 Server Error", explanation="device busy"))

        response = api.delete(UNINSTALL.format("photos"))

        failure = 'Failed to remove photos-cache: 500 Server Error ("device busy")'
        assert (response.status_code, response.json()) == (500, {"detail": failure})
        assert _audit()[-1] == {
            "action": "app_uninstalled", "resource_type": "app", "resource_id": "photos",
            "details": "Stopped photos, photos-machine-learning, photos-cache, photos-database; removed photos, "
            f"photos-machine-learning; then: {failure}",
            "severity": "warning", "success": False,
        }

    def test_a_change_of_the_app_under_way_refuses_another(self, api, pools, docker):
        _installed(api, docker)

        with stacks_module.app_lock("photos", "Photos"):
            responses = [api.request(m, p.format("photos-cache")) for m, p in
                         [("POST", START), ("POST", STOP), ("POST", RESTART), ("DELETE", UNINSTALL)]]

        assert {(r.status_code, r.json()["detail"]) for r in responses} == {
            (409, "Another change of Photos is under way; try again when it has finished")
        }
        assert docker.called("container.stop", "container.start", "container.remove") == []

    def test_a_listing_of_the_app_that_docker_does_not_answer_answers_503(self, api, pools, docker):
        _installed(api, docker)
        error = requests.exceptions.ConnectionError("('Connection aborted.', ConnectionResetError(104, 'reset'))")
        docker.fail("containers.list", None, error)

        response = api.post(STOP.format("photos"))

        assert (response.status_code, response.json()) == (503, {"detail": f"Docker is not available: {error}"})
        assert docker.called("container.stop") == []


# ---------------------------------------------------------------------------
# What a template of several containers must say
# ---------------------------------------------------------------------------


class TestTemplateRules:
    @pytest.mark.parametrize("name", ["Database", "1db", "db_1", "", "-db", "db.x"])
    def test_a_companion_is_named_in_lowercase_letters_digits_and_hyphens(self, name):
        with pytest.raises(ValidationError):
            Companion(name=name, image=DATABASE)

    def test_a_companion_name_is_given_once(self):
        with pytest.raises(ValidationError, match="a companion name is given twice"):
            PHOTOS.model_validate({**PHOTOS.model_dump(), "companions": [
                {"name": "database", "image": DATABASE}, {"name": "database", "image": CACHE},
            ], "depends_on": []})

    def test_depends_on_names_companions(self):
        with pytest.raises(ValidationError, match="depends_on names no companion"):
            PHOTOS.model_validate({**PHOTOS.model_dump(), "depends_on": ["broker"]})

    def test_the_names_of_an_apps_containers_and_its_network(self):
        assert stacks_module.container_names(PHOTOS) == NAMES
        assert stacks_module.network_name("photos") == NETWORK
