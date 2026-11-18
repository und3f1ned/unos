"""BookStack, Paperless-ngx and Linkwarden install as apps with companions (apps.catalog-companions,
ADR-0414): BookStack with its MariaDB, Paperless-ngx with its Valkey broker and Linkwarden with its
PostgreSQL, each image pinned, each password and key a file that UNOS generates on the pool, a
database ready by the command that UNOS runs in it before the app's own container starts, and all
their data on the pool. Also the host gateway of Open WebUI and Zigbee2MQTT, and the catalog's
estimate of an app's images on the system disk.

Docker is the strict fake of docker_fake.py, the host the fakes of test_apps.py, and the time of
the waits the clock of test_app_stacks.py, which sleeping moves on.
"""

from __future__ import annotations

import importlib
import logging
import re
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest
from docker.errors import APIError
from docker.models.containers import ExecResult

from src.apps.catalog import APP_CATALOG, get_app_by_id
from src.apps.schemas import APPS_DATA_PATH, VolumeMapping

from . import test_app_stacks, test_apps
from .docker_fake import StackDocker
from .test_apps import INSTALLED, RESTART, START, STOP, UNINSTALL, install

api = test_apps.api
pools = test_apps.pools
soc = test_apps.soc
shares = test_apps.shares
chown_calls = test_apps.chown_calls
clock = test_app_stacks.clock
_created = test_app_stacks._created
_audit = test_app_stacks._audit

apps_router = importlib.import_module("src.apps.router")
catalog_module = importlib.import_module("src.apps.catalog")
platforms_module = importlib.import_module("src.apps.platforms")
stacks_module = importlib.import_module("src.apps.stacks")
audit_router = importlib.import_module("src.audit.router")

c = catalog_module
COMPANION_APPS = ("bookstack", "paperless-ngx", "linkwarden")
# The commands that tell when a database has finished its start and takes connections over TCP,
# which its first start's temporary server does not.
# Each ends within a timeout of its own: docker-py reads an exec's output without one.
MARIADB_READY = ["timeout", "10", "healthcheck.sh", "--connect", "--innodb_initialized"]
POSTGRES_READY = ["pg_isready", "-t", "5", "-h", "127.0.0.1", "-U", "linkwarden", "-d", "linkwarden"]
# What the image of each container writes at run time outside its data, kept in RAM.
S6_RUN = "exec,size=16m"


def _by_digest(pin) -> str:
    return f"{pin.name}@{pin.digest}"


def _mounts(docker: StackDocker, name: str) -> list[tuple[str, str, bool]]:
    return [(m["Source"], m["Target"], m["ReadOnly"]) for m in _created(docker, name).kwargs["host_config"]["Mounts"]]


def _secret(path: Path) -> str:
    """A generated file's value, after checking that it is root's alone."""
    assert stat.S_IMODE(path.stat().st_mode) == 0o600, path
    return path.read_text()


@pytest.fixture
def docker(monkeypatch) -> StackDocker:
    """Docker, with the pinned images of the three apps and of Open WebUI and Zigbee2MQTT in the
    registry, each declaring the VOLUMEs that its pin records; Paperless-ngx's and Linkwarden's
    images have their health checks, the databases none."""
    fake = StackDocker()
    for pin, healthcheck in [
        (c.BOOKSTACK, False), (c.BOOKSTACK_MARIADB, False), (c.PAPERLESS, True), (c.PAPERLESS_VALKEY, False),
        (c.LINKWARDEN, True), (c.LINKWARDEN_POSTGRES, False),
        (c.APP_IMAGES["open-webui"], True), (c.APP_IMAGES["zigbee2mqtt"], False),
    ]:
        fake.publish(pin.ref, volumes=pin.volumes, healthcheck=healthcheck)
    monkeypatch.setattr(apps_router, "get_client", lambda: fake)
    return fake


# ---------------------------------------------------------------------------
# The catalog
# ---------------------------------------------------------------------------


class TestCatalog:
    def test_the_three_apps_are_back_with_their_companions(self):
        bookstack, paperless, linkwarden = (get_app_by_id(app_id) for app_id in COMPANION_APPS)

        assert [(m.name, m.image) for m in bookstack.companions] == [("database", c.BOOKSTACK_MARIADB.ref)]
        assert [(m.name, m.image) for m in paperless.companions] == [("redis", c.PAPERLESS_VALKEY.ref)]
        assert [(m.name, m.image) for m in linkwarden.companions] == [("database", c.LINKWARDEN_POSTGRES.ref)]
        assert (bookstack.depends_on, paperless.depends_on, linkwarden.depends_on) == (
            ["database"], ["redis"], ["database"],
        )
        for template, pin in [(bookstack, c.BOOKSTACK), (paperless, c.PAPERLESS), (linkwarden, c.LINKWARDEN)]:
            assert template.image == pin.ref and template.version == pin.tag, template.id
        assert [p.host for t in (bookstack, paperless, linkwarden) for p in t.ports] == [6875, 8010, 3010]
        # The databases tell when they are ready by a command that UNOS runs in them; the broker,
        # which answers as soon as it runs, needs none.
        assert bookstack.companions[0].ready_command == MARIADB_READY
        assert linkwarden.companions[0].ready_command == POSTGRES_READY
        assert paperless.companions[0].ready_command is None
        # Each database keeps its data on the pool and is copied before an update (ADR-0408).
        for template, data, target in [
            (bookstack, "database/data", "/var/lib/mysql"),
            (linkwarden, "database/data", "/var/lib/postgresql/data"),
        ]:
            volume = next(v for v in template.companions[0].volumes if v.container == target)
            assert volume.host == f"{APPS_DATA_PATH}/{template.id}/{data}" and volume.copy_before_update
        assert next(v for v in paperless.volumes if v.container == "/usr/src/paperless/data").copy_before_update

    def test_each_password_and_key_is_a_file_that_unos_generates(self):
        def generated(template) -> dict[str, tuple[int, str | None, list[str]]]:
            files: dict[str, tuple[int, str | None, list[str]]] = {}
            for spec in (template, *template.companions):
                for volume in spec.volumes:
                    if volume.generated_secret:
                        name = getattr(spec, "name", "") if spec is not template else ""
                        digits, unlocks, users = files.get(volume.host, (volume.secret_digits, volume.secret_for, []))
                        files[volume.host] = (digits, unlocks, [*users, name or template.id])
            return files

        apps = f"{APPS_DATA_PATH}/bookstack"
        assert generated(get_app_by_id("bookstack")) == {
            f"{apps}/app-key": (32, f"{apps}/database/data", ["bookstack"]),
            f"{apps}/database/password": (64, f"{apps}/database/data", ["bookstack", "database"]),
            f"{apps}/database/root-password": (64, f"{apps}/database/data", ["database"]),
        }
        apps = f"{APPS_DATA_PATH}/paperless-ngx"
        assert generated(get_app_by_id("paperless-ngx")) == {
            f"{apps}/secret-key": (64, f"{apps}/data", ["paperless-ngx"]),
        }
        apps = f"{APPS_DATA_PATH}/linkwarden"
        assert generated(get_app_by_id("linkwarden")) == {
            f"{apps}/database/password": (64, f"{apps}/database/data", ["linkwarden", "database"]),
            f"{apps}/nextauth-secret": (64, f"{apps}/database/data", ["linkwarden"]),
        }
        # None of them asks the administrator for a secret, and the broker has no password.
        for app_id in COMPANION_APPS:
            template = get_app_by_id(app_id)
            assert not [v.name for spec in (template, *template.companions) for v in spec.environment if v.secret]
        assert not get_app_by_id("paperless-ngx").companions[0].environment

    def test_a_generated_secret_has_an_even_count_of_digits_from_32_to_256(self):
        for digits in (32, 64, 256):
            assert VolumeMapping(host="{pool}/apps/x/key", container="/k", secret_digits=digits).secret_digits == digits
        for digits in (16, 33, 258):
            with pytest.raises(ValueError):
                VolumeMapping(host="{pool}/apps/x/key", container="/k", secret_digits=digits)

    def test_only_open_webui_and_zigbee2mqtt_reach_the_host_through_its_gateway(self):
        assert sorted(t.id for t in APP_CATALOG if t.host_gateway) == ["open-webui", "zigbee2mqtt"]
        for template in APP_CATALOG:
            assert not (template.host_gateway and template.network_mode), template.id
        webui = {v.name: v.value for v in get_app_by_id("open-webui").environment}
        assert webui["OLLAMA_BASE_URL"] == "http://host.docker.internal:11434"
        zigbee = {v.name: v for v in get_app_by_id("zigbee2mqtt").environment}
        # The broker: the Mosquitto app of the same NAS, on the port that its template publishes.
        assert zigbee["ZIGBEE2MQTT_CONFIG_MQTT_SERVER"].value == "mqtt://host.docker.internal:1883"
        assert zigbee["ZIGBEE2MQTT_CONFIG_MQTT_SERVER"].editable
        assert 1883 in [p.host for p in get_app_by_id("mosquitto").ports]

    @pytest.mark.parametrize(("app_id", "pins"), [
        ("bookstack", ("BOOKSTACK", "BOOKSTACK_MARIADB")),
        ("paperless-ngx", ("PAPERLESS", "PAPERLESS_VALKEY")),
        ("linkwarden", ("LINKWARDEN", "LINKWARDEN_POSTGRES")),
        ("transmission", ()),
    ])
    def test_the_catalog_answers_the_size_of_an_apps_images_on_the_system_disk(self, api, monkeypatch, app_id, pins):
        monkeypatch.setattr(platforms_module, "host_platform", lambda: "linux/arm64")

        listed = {t["id"]: t for t in api.get("/api/v1/apps/catalog").json()}
        one = api.get(f"/api/v1/apps/catalog/{app_id}").json()

        need = sum(getattr(c, name).disk_bytes for name in pins) or c.APP_IMAGES.get(app_id).disk_bytes
        assert listed[app_id]["system_disk_bytes"] == one["system_disk_bytes"] == need
        assert listed[app_id]["available"] and listed[app_id]["host_platform"] == "linux/arm64"

    def test_the_size_of_immichs_images_counts_the_machine_learning_that_this_host_runs(self, api, soc):
        cpu = api.get("/api/v1/apps/catalog/immich").json()["system_disk_bytes"]
        test_apps._rk3588(soc)
        npu = api.get("/api/v1/apps/catalog/immich").json()["system_disk_bytes"]

        base = c.IMMICH_SERVER.disk_bytes + c.IMMICH_POSTGRES.disk_bytes + c.VALKEY.disk_bytes
        assert (cpu, npu) == (base + c.IMMICH_ML.disk_bytes, base + c.IMMICH_ML_RKNN.disk_bytes)


# ---------------------------------------------------------------------------
# BookStack and its MariaDB
# ---------------------------------------------------------------------------


class TestBookStack:
    URL = {"APP_URL": "http://nas.local:6875"}

    def _install(self, api):
        return install(api, {"template_id": "bookstack", "env_overrides": self.URL})

    def test_it_asks_for_the_address_it_is_opened_at(self, api, pools, docker):
        response = install(api, {"template_id": "bookstack"})

        assert (response.status_code, response.json()) == (400, {"detail": "APP_URL is required"})
        assert docker.calls == []

    def test_it_installs_its_database_and_waits_until_the_database_answers(self, api, pools, docker, clock):
        docker.exec_results["bookstack-database"] = [(1, b"Can't connect"), (1, b"Can't connect"), (0, b"")]

        response = self._install(api)

        assert response.status_code == 200, response.text
        assert docker.called("networks.create", "api.create_container", "container.start") == [
            ("networks.create", "unos-app-bookstack"),
            ("api.create_container", "bookstack-database"),
            ("api.create_container", "bookstack"),
            ("container.start", "bookstack-database"),
            ("container.start", "bookstack"),
        ]
        # The database's command ran until it answered, and only then did BookStack start, so
        # that its migrations meet a database that takes connections.
        calls = docker.called("container.start", "container.exec_run")
        assert calls == [
            ("container.start", "bookstack-database"),
            *[("container.exec_run", "bookstack-database")] * 3,
            ("container.start", "bookstack"),
        ]
        assert [call.args for call in docker.calls if call.name == "container.exec_run"] == [(MARIADB_READY,)] * 3
        assert clock.slept == [2, 2]
        assert {name: _created(docker, name).args for name in ("bookstack-database", "bookstack")} == {
            "bookstack-database": (_by_digest(c.BOOKSTACK_MARIADB),),
            "bookstack": (_by_digest(c.BOOKSTACK),),
        }

    def test_its_passwords_and_key_reach_the_containers_only_as_files(self, api, pools, docker, caplog):
        with caplog.at_level(logging.DEBUG):
            response = self._install(api)
            listed = api.get(INSTALLED)

        assert response.status_code == 200, response.text
        apps = pools.alpha / "apps/bookstack"
        assert _created(docker, "bookstack").kwargs["environment"] == {
            "PUID": "1000", "PGID": "1000", "TZ": "UTC", "APP_URL": "http://nas.local:6875",
            "FILE__APP_KEY": "/run/secrets/app-key",
            "DB_HOST": "database", "DB_PORT": "3306", "DB_DATABASE": "bookstack", "DB_USERNAME": "bookstack",
            "FILE__DB_PASSWORD": "/run/secrets/db-password",
        }
        assert _created(docker, "bookstack-database").kwargs["environment"] == {
            "MARIADB_DATABASE": "bookstack", "MARIADB_USER": "bookstack",
            "MARIADB_PASSWORD_FILE": "/run/secrets/db-password",
            "MARIADB_ROOT_PASSWORD_FILE": "/run/secrets/db-root-password",
            "MARIADB_AUTO_UPGRADE": "1",
        }
        key = _secret(apps / "app-key")
        password = _secret(apps / "database/password")
        root = _secret(apps / "database/root-password")
        # Laravel takes a key without "base64:" as its 32 bytes, as AES-256 needs them.
        assert re.fullmatch(r"[0-9a-f]{32}", key) and re.fullmatch(r"[0-9a-f]{64}", password)
        assert re.fullmatch(r"[0-9a-f]{64}", root) and len({key, password, root}) == 3
        attrs = repr([live.attrs for live in docker.containers_.values()])
        for value in (key, password, root):
            for where in (repr(docker.calls), caplog.text, Path(audit_router.AUDIT_FILE).read_text(),
                          response.text, listed.text, attrs):
                assert value not in where
        assert _audit()[0]["details"] == (
            f"Installed BookStack {c.BOOKSTACK.tag} from the catalog as bookstack with the containers "
            "bookstack-database and bookstack, with its data on the pool alpha; generated its password files "
            f"{apps / 'database/password'}, {apps / 'database/root-password'} and {apps / 'app-key'}"
        )

    def test_all_its_data_is_on_the_pool_and_what_it_writes_at_run_time_in_ram(self, api, pools, docker):
        response = self._install(api)

        assert response.status_code == 200, response.text
        apps = pools.alpha / "apps/bookstack"
        assert _mounts(docker, "bookstack") == [
            (str(apps / "config"), "/config", False),
            (str(apps / "app-key"), "/run/secrets/app-key", True),
            (str(apps / "database/password"), "/run/secrets/db-password", True),
        ]
        assert _mounts(docker, "bookstack-database") == [
            (str(apps / "database/data"), "/var/lib/mysql", False),
            (str(apps / "database/password"), "/run/secrets/db-password", True),
            (str(apps / "database/root-password"), "/run/secrets/db-root-password", True),
        ]
        # s6's files, the copies of the variables read from files among them, in RAM that may run them.
        # /tmp holds an upload twice, nginx's copy and PHP's, of up to the 100 MB that the image allows.
        assert _created(docker, "bookstack").kwargs["host_config"]["Tmpfs"] == {"/run": S6_RUN, "/tmp": "size=256m"}
        assert _created(docker, "bookstack-database").kwargs["host_config"]["Tmpfs"] == {
            "/run/mysqld": "size=1m", "/tmp": "size=64m",
        }
        assert _created(docker, "bookstack-database").kwargs["stop_timeout"] == 60
        assert response.json()["web_url"] == "http://localhost:6875"

    def test_a_database_that_never_answers_removes_every_container(self, api, pools, docker, clock):
        docker.exec_results["bookstack-database"] = [(1, b"mariadb: Can't connect to server on 'localhost'")]

        response = self._install(api)

        data = pools.alpha / "apps/bookstack/database/data"
        assert (response.status_code, response.json()) == (500, {
            "detail": "bookstack-database did not become ready within 180 s: mariadb: Can't connect to server on "
            "'localhost'; its containers were removed and its data folders kept. "
            f"{data} was empty before this install and may now hold data that bookstack-database did not finish "
            "setting up: move it away before you install BookStack again."
        })
        assert docker.called("container.start") == [("container.start", "bookstack-database")]
        assert docker.containers_ == {} and docker.networks_ == {}
        assert sum(clock.slept) == 180

    def test_a_start_of_the_installed_app_waits_for_the_database_too(self, api, pools, docker, clock):
        assert self._install(api).status_code == 200
        assert api.post(STOP.format("bookstack")).status_code == 200
        docker.calls.clear()
        clock.slept.clear()
        docker.exec_results["bookstack-database"] = [(1, b""), (0, b"")]

        response = api.post(START.format("bookstack"))

        assert response.status_code == 200, response.text
        assert docker.called("container.start", "container.exec_run") == [
            ("container.start", "bookstack-database"),
            ("container.exec_run", "bookstack-database"),
            ("container.exec_run", "bookstack-database"),
            ("container.start", "bookstack"),
        ]
        assert clock.slept == [2]

    def test_a_database_that_stops_at_its_start_is_not_asked_and_not_ready(self, api, pools, docker):
        assert self._install(api).status_code == 200
        docker.script("bookstack-database", {"Status": "exited", "Running": False, "ExitCode": 1})
        docker.calls.clear()

        response = api.post(RESTART.format("bookstack"))

        assert response.status_code == 500, response.text
        assert "bookstack-database stopped (exit code 1)" in response.json()["detail"]
        assert docker.called("container.start") == [("container.start", "bookstack-database")]
        assert docker.called("container.exec_run") == []

    def test_a_missing_key_beside_its_database_is_refused_before_any_pull(self, api, pools, docker):
        assert self._install(api).status_code == 200
        assert api.delete(UNINSTALL.format("bookstack")).status_code == 200
        apps = pools.alpha / "apps/bookstack"
        (apps / "database/data/ibdata1").write_text("data")
        (apps / "app-key").unlink()
        docker.calls.clear()

        response = self._install(api)

        assert response.status_code == 409, response.text
        assert str(apps / "app-key") in response.json()["detail"]
        assert docker.called("api.pull", "api.create_container") == []


    def test_the_wait_ends_after_180_s_when_each_run_of_the_command_is_slow(self, api, pools, docker, clock):
        # Each run takes 60 s before it fails: the wait does not start another once 180 s have
        # passed, so it ends within 180 s and one run of the command, which its timeout bounds.
        docker.exec_results["bookstack-database"] = [(124, b"")]
        docker.exec_hook = lambda container: clock.sleep(60)

        response = self._install(api)

        assert response.status_code == 500, response.text
        assert response.json()["detail"].startswith("bookstack-database did not become ready within 180 s;")
        assert docker.called("container.exec_run") == [("container.exec_run", "bookstack-database")] * 3
        assert clock.now - 1000.0 == 184

    def test_a_database_that_keeps_restarting_is_never_asked_and_not_ready(self, api, pools, docker, clock):
        docker.script("bookstack-database", {"Status": "restarting", "Running": True, "Restarting": True})

        response = self._install(api)

        assert response.status_code == 500, response.text
        assert response.json()["detail"].startswith("bookstack-database did not become ready within 180 s;")
        assert docker.called("container.exec_run") == []
        assert docker.containers_ == {}

    def test_an_exec_that_docker_fails_removes_every_container(self, api, pools, docker):
        docker.fail("container.exec_run", "bookstack-database", APIError("500 Server Error", explanation="exec failed"))

        response = self._install(api)

        assert response.status_code == 500, response.text
        assert response.json()["detail"].startswith("Failed to start container bookstack-database: 500 Server Error")
        assert docker.called("container.start") == [("container.start", "bookstack-database")]
        assert docker.containers_ == {} and docker.networks_ == {}


# ---------------------------------------------------------------------------
# Paperless-ngx and its Valkey
# ---------------------------------------------------------------------------


class TestPaperless:
    def test_it_installs_its_broker_and_keeps_its_scratch_and_data_on_the_pool(self, api, pools, docker, clock):
        response = install(api, {"template_id": "paperless-ngx"})

        assert response.status_code == 200, response.text
        apps = pools.alpha / "apps/paperless-ngx"
        assert docker.called("container.start", "container.exec_run") == [
            ("container.start", "paperless-ngx-redis"),
            ("container.start", "paperless-ngx"),
        ]
        # Valkey has no health check and no command: it is ready once it has run for 2 seconds.
        assert clock.slept == [2]
        assert _created(docker, "paperless-ngx").kwargs["environment"] == {
            "PAPERLESS_REDIS": "redis://redis:6379",
            "PAPERLESS_DBENGINE": "sqlite",
            "PAPERLESS_SECRET_KEY_FILE": "/run/secrets/secret-key",
            "PAPERLESS_SCRATCH_DIR": "/usr/src/paperless/scratch",
            "TMPDIR": "/usr/src/paperless/scratch",
            "S6_READ_ONLY_ROOT": "1",
            "PAPERLESS_OCR_LANGUAGE": "eng",
            "PAPERLESS_TIME_ZONE": "UTC",
            "PAPERLESS_URL": "",
        }
        assert _mounts(docker, "paperless-ngx") == [
            (str(apps / "data"), "/usr/src/paperless/data", False),
            (str(apps / "media"), "/usr/src/paperless/media", False),
            (str(apps / "export"), "/usr/src/paperless/export", False),
            (str(apps / "consume"), "/usr/src/paperless/consume", False),
            (str(apps / "scratch"), "/usr/src/paperless/scratch", False),
            (str(apps / "secret-key"), "/run/secrets/secret-key", True),
        ]
        assert _created(docker, "paperless-ngx").kwargs["host_config"]["Tmpfs"] == {"/run": S6_RUN, "/tmp": "size=64m"}
        assert _created(docker, "paperless-ngx-redis").kwargs["environment"] is None
        assert _mounts(docker, "paperless-ngx-redis") == [(str(apps / "redis"), "/data", False)]
        key = _secret(apps / "secret-key")
        assert re.fullmatch(r"[0-9a-f]{64}", key) and key not in repr(docker.calls) and key not in response.text
        assert response.json()["web_url"] == "http://localhost:8010"


# ---------------------------------------------------------------------------
# Linkwarden and its PostgreSQL
# ---------------------------------------------------------------------------


class TestLinkwarden:
    def test_its_command_reads_the_password_and_the_secret_from_their_files(self, api, pools, docker, clock):
        docker.exec_results["linkwarden-database"] = [(2, b"127.0.0.1:5432 - no response"), (0, b"accepting")]

        response = install(api, {"template_id": "linkwarden"})

        assert response.status_code == 200, response.text
        assert docker.called("container.start", "container.exec_run") == [
            ("container.start", "linkwarden-database"),
            ("container.exec_run", "linkwarden-database"),
            ("container.exec_run", "linkwarden-database"),
            ("container.start", "linkwarden"),
        ]
        assert [call.args for call in docker.calls if call.name == "container.exec_run"] == [(POSTGRES_READY,)] * 2
        server = _created(docker, "linkwarden")
        assert server.kwargs["environment"] == {
            "NEXTAUTH_URL": "http://localhost:3010/api/v1/auth",
            "NEXT_PUBLIC_DISABLE_REGISTRATION": "false",
            "DATABASE_PASSWORD_FILE": "/run/secrets/db-password",
            "NEXTAUTH_SECRET_FILE": "/run/secrets/nextauth-secret",
            "NEXT_TELEMETRY_DISABLED": "1",
            "CHECKPOINT_DISABLE": "1",
            "XDG_CACHE_HOME": "/tmp/cache",
        }
        # The image's own command, after the database's address with its password and the session
        # secret are read from their files into the variables that Linkwarden reads.
        command = server.kwargs["command"]
        assert command == ["sh", "-c", c.LINKWARDEN_COMMAND]
        assert docker.container("linkwarden").attrs["Config"]["Cmd"] == command
        assert command[2].startswith(
            'password="$(cat "$DATABASE_PASSWORD_FILE")" && secret="$(cat "$NEXTAUTH_SECRET_FILE")" && export '
            'DATABASE_URL="postgresql://linkwarden:${password}@database:5432/linkwarden" NEXTAUTH_SECRET="${secret}"'
        )
        assert command[2].endswith(" && " + c.LINKWARDEN_START)
        # The CMD recorded in the pin from the image's configuration, which a re-pin reads again.
        assert c.LINKWARDEN.cmd == ("sh", "-c", c.LINKWARDEN_START)
        assert _created(docker, "linkwarden-database").kwargs["environment"] == {
            "POSTGRES_USER": "linkwarden", "POSTGRES_DB": "linkwarden",
            "POSTGRES_PASSWORD_FILE": "/run/secrets/db-password",
        }
        apps = pools.alpha / "apps/linkwarden"
        password, secret = _secret(apps / "database/password"), _secret(apps / "nextauth-secret")
        for value in (password, secret):
            assert re.fullmatch(r"[0-9a-f]{64}", value)
            assert value not in repr(docker.calls) and value not in response.text
        assert "DATABASE_URL" not in server.kwargs["environment"]

    def test_all_its_data_is_on_the_pool_and_what_it_writes_at_run_time_in_ram(self, api, pools, docker):
        response = install(api, {"template_id": "linkwarden"})

        assert response.status_code == 200, response.text
        apps = pools.alpha / "apps/linkwarden"
        assert _mounts(docker, "linkwarden") == [
            (str(apps / "data"), "/data/data", False),
            (str(apps / "next-cache"), "/data/apps/web/.next/cache", False),
            (str(apps / "database/password"), "/run/secrets/db-password", True),
            (str(apps / "nextauth-secret"), "/run/secrets/nextauth-secret", True),
        ]
        assert _mounts(docker, "linkwarden-database") == [
            (str(apps / "database/data"), "/var/lib/postgresql/data", False),
            (str(apps / "database/password"), "/run/secrets/db-password", True),
        ]
        assert _created(docker, "linkwarden").kwargs["host_config"]["Tmpfs"] == {"/tmp": "size=512m"}
        assert _created(docker, "linkwarden-database").kwargs["host_config"]["Tmpfs"] == {
            "/var/run/postgresql": "size=1m",
        }
        assert _created(docker, "linkwarden-database").kwargs["stop_timeout"] == 60
        assert response.json()["web_url"] == "http://localhost:3010"


# ---------------------------------------------------------------------------
# The host gateway
# ---------------------------------------------------------------------------


class TestHostGateway:
    @pytest.mark.parametrize("app_id", ["open-webui", "zigbee2mqtt"])
    def test_the_app_resolves_host_docker_internal_to_the_host(self, api, pools, docker, app_id):
        response = install(api, {"template_id": app_id})

        assert response.status_code == 200, response.text
        config = docker.container(app_id).attrs["HostConfig"]
        assert config["ExtraHosts"] == ["host.docker.internal:host-gateway"]

    def test_an_app_without_it_gets_no_extra_host(self, api, pools, docker):
        pin = c.APP_IMAGES["dozzle"]
        docker.publish(pin.ref)

        response = install(api, {"template_id": "dozzle"})

        assert response.status_code == 200, response.text
        assert "ExtraHosts" not in docker.container("dozzle").attrs["HostConfig"]


# ---------------------------------------------------------------------------
# The readiness command
# ---------------------------------------------------------------------------


def _refusal(status: int) -> APIError:
    return APIError(f"{status} Client Error", response=SimpleNamespace(status_code=status, reason="", url=""))


class TestReadyCommand:
    def test_its_exit_code_and_the_start_of_its_output_answer(self):
        container = SimpleNamespace(exec_run=lambda cmd, stdout, stderr: ExecResult(1, b"no\x1b[0m response\n" * 40))

        answered, said = stacks_module._answers(container, ["pg_isready"])

        assert not answered
        assert said.startswith("no [0m response no [0m response") and len(said) == stacks_module.HEALTH_OUTPUT_CHARS

    def test_a_container_that_docker_says_does_not_run_has_not_answered(self):
        def refused(cmd, stdout, stderr):
            raise _refusal(409)

        # It said nothing: the last output of an earlier run stays the one that a timeout names.
        assert stacks_module._answers(SimpleNamespace(exec_run=refused), ["pg_isready"]) == (False, None)

    def test_any_other_error_of_docker_is_raised(self):
        def failed(cmd, stdout, stderr):
            raise _refusal(500)

        with pytest.raises(APIError):
            stacks_module._answers(SimpleNamespace(exec_run=failed), ["pg_isready"])

    def test_only_a_companion_names_one(self):
        for template in APP_CATALOG:
            assert template.ready_command is None, template.id


# ---------------------------------------------------------------------------
# What no generated value reaches, and what Docker runs while the apps idle
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("app_id", "env", "files"), [
    ("bookstack", {"APP_URL": "http://nas.local:6875"}, ["database/password", "database/root-password", "app-key"]),
    ("paperless-ngx", {}, ["secret-key"]),
    ("linkwarden", {}, ["database/password", "nextauth-secret"]),
])
def test_no_generated_value_is_anywhere_but_in_its_file(api, pools, docker, caplog, app_id, env, files):
    with caplog.at_level(logging.DEBUG):
        installed = install(api, {"template_id": app_id, "env_overrides": env})
        listed = api.get(INSTALLED)
        restarted = api.post(RESTART.format(app_id))
        uninstalled = api.delete(UNINSTALL.format(app_id))

    assert [r.status_code for r in (installed, listed, restarted, uninstalled)] == [200] * 4
    attrs = repr([call.kwargs for call in docker.calls])
    for name in files:
        value = _secret(pools.alpha / f"apps/{app_id}/{name}")
        for where in (repr(docker.calls), attrs, caplog.text, Path(audit_router.AUDIT_FILE).read_text(),
                      installed.text, listed.text, restarted.text, uninstalled.text):
            assert value not in where, (name, where[:80])


class TestHealthChecks:
    def test_paperless_and_linkwarden_run_without_their_images_health_checks(self, api, pools, docker):
        # Docker writes a container's state to the system disk at every probe of a health check.
        for app_id in ("paperless-ngx", "linkwarden"):
            response = install(api, {"template_id": app_id})

            assert response.status_code == 200, response.text
            assert _created(docker, app_id).kwargs["healthcheck"] == {"test": ["NONE"]}
            assert response.json()["health"] is None
            assert "Health" not in docker.container(app_id).attrs["State"]
        for name in ("paperless-ngx-redis", "linkwarden-database"):
            assert _created(docker, name).kwargs["healthcheck"] is None

    def test_an_app_keeps_its_images_health_check_unless_its_template_turns_it_off(self):
        off = sorted(spec_id for t in APP_CATALOG for spec_id, spec in [(t.id, t), *((f"{t.id}-{m.name}", m) for m in t.companions)]
                     if not spec.image_healthcheck)
        assert off == ["linkwarden", "paperless-ngx"]

    def test_a_ready_command_is_only_for_a_companion_that_is_waited_for(self):
        bookstack = get_app_by_id("bookstack")
        with pytest.raises(ValueError, match="ready command"):
            bookstack.model_validate({**bookstack.model_dump(), "depends_on": []})
        with pytest.raises(ValueError, match="ready command"):
            bookstack.model_validate({**bookstack.model_dump(), "ready_command": ["true"]})
