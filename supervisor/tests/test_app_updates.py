"""An installed app updates in place to the catalog's newer pinned images, keeps its data, copies its
database first and can be rolled back (apps.update-in-place, ADR-0408).

The app is test_app_stacks.py's Photos of four containers, its database folder marked
``copy_before_update``. Version 1.0 is installed through the API against the strict fake Docker of
docker_fake.py; then the catalog pins version 2.0, whose server and database images are new and
whose cache and machine learning are those of 1.0. The registries are a fake transport of
src/apps/registry.py that serves each image's index, its manifest for the host's platform and its
configuration, content-addressed, behind the anonymous token handshake; the copy of the database is
cp's, done here by shutil.copytree (cp -a on the device); the host is the fake of test_apps.py.
"""

from __future__ import annotations

import hashlib
import importlib
import itertools
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from docker.errors import APIError

from src.apps.catalog import APP_CATALOG, PinnedImage
from src.apps.schemas import AppCategory, AppTemplate, PortMapping
from src.audit.schemas import AuditAction

from . import test_apps
from .docker_fake import StackDocker
from .test_app_stacks import CACHE, DATABASE, LEARNING, LEARNING_NPU, NAMES, PHOTOS, SERVER, _audit, clock  # noqa: F401
from .test_apps import GIB, INSTALL, INSTALLED, UNINSTALL, install

api = test_apps.api
user_api = test_apps.user_api
pools = test_apps.pools
soc = test_apps.soc
shares = test_apps.shares
chown_calls = test_apps.chown_calls

apps_router = importlib.import_module("src.apps.router")
catalog_module = importlib.import_module("src.apps.catalog")
updates_module = importlib.import_module("src.apps.updates")
registry_module = importlib.import_module("src.apps.registry")
platforms_module = importlib.import_module("src.apps.platforms")
install_jobs = importlib.import_module("src.apps.install_jobs")
host_module = importlib.import_module("src.host")
preflight_module = importlib.import_module("src.apps.preflight")

PLATFORM = "linux/arm64"
DATA = "/var/lib/postgresql/data"
BUILT_1 = "2026-11-01T08:00:00.123456789Z"
BUILT_2 = "2026-11-17T08:00:00.987654321Z"


def _layer(name: str) -> str:
    return "sha256:" + hashlib.sha256(name.encode()).hexdigest()


# The unpacked layers: the base that both versions' server and database share, and each one's own.
BASE = _layer("base")
SERVER_1_LAYER, SERVER_2_LAYER = _layer("server 1"), _layer("server 2")
DB_1_LAYER, DB_2_LAYER = _layer("db 1"), _layer("db 2")


def _digest(body: bytes) -> str:
    return "sha256:" + hashlib.sha256(body).hexdigest()


class Registries:
    """The registries as an update reads them (src/apps/registry.py): 401 with a Bearer challenge for a
    request without a token, a token for the realm, and content-addressed manifests and blobs; a
    configuration's blob is redirected to a CDN, which takes no token. ``requests`` logs each URL."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[str, bytes]] = {}  # url -> (media type, body)
        self.requests: list[tuple[str, dict]] = []
        self.configs: dict[str, str] = {}  # ref -> config digest for the host's platform
        self.error: Exception | None = None

    def image(self, name: str, tag: str, *, created: str | None, layers: list[tuple[str, int]],
              platforms: tuple[str, ...] = ("linux/amd64", PLATFORM)) -> str:
        """Publish ``name:tag`` for ``platforms`` with ``layers`` (diff id, compressed size): its pinned
        reference."""
        repo = name.removeprefix("example/")
        entries = []
        platform_config = ""
        for platform in platforms:
            arch = platform.split("/")[1]
            config = {"architecture": arch, "os": "linux", "rootfs": {"type": "layers", "diff_ids": [d for d, _ in layers]}}
            if created is not None:
                config["created"] = created
            config_body = json.dumps(config).encode()
            config_digest = _digest(config_body)
            self.objects[f"https://cdn.example/{repo}/{config_digest}"] = ("application/octet-stream", config_body)
            self.objects[f"https://registry-1.docker.io/v2/example/{repo}/blobs/{config_digest}"] = ("redirect", b"")
            manifest = {
                "schemaVersion": 2, "mediaType": "application/vnd.oci.image.manifest.v1+json",
                "config": {"mediaType": "application/vnd.oci.image.config.v1+json", "digest": config_digest,
                           "size": len(config_body)},
                "layers": [{"mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
                            "digest": _layer(f"{diff} compressed"), "size": size} for diff, size in layers],
            }
            body = json.dumps(manifest).encode()
            self.objects[f"https://registry-1.docker.io/v2/example/{repo}/manifests/{_digest(body)}"] = (
                manifest["mediaType"], body)
            entries.append({"mediaType": manifest["mediaType"], "digest": _digest(body), "size": len(body),
                            "platform": {"architecture": arch, "os": "linux"}})
            if platform == PLATFORM:
                platform_config = config_digest
        index = json.dumps({"schemaVersion": 2, "mediaType": "application/vnd.oci.image.index.v1+json",
                            "manifests": entries}).encode()
        self.objects[f"https://registry-1.docker.io/v2/example/{repo}/manifests/{_digest(index)}"] = (
            "application/vnd.oci.image.index.v1+json", index)
        ref = f"{name}:{tag}@{_digest(index)}"
        self.configs[ref] = platform_config
        return ref

    def __call__(self, url: str, headers: dict) -> tuple[int, dict, bytes]:
        self.requests.append((url, dict(headers)))
        if self.error is not None:
            raise self.error
        if url.startswith("https://auth.docker.io/token"):
            return 200, {}, json.dumps({"token": "anonymous"}).encode()
        if url.startswith("https://cdn.example/"):
            assert "Authorization" not in headers, "the registry's token goes to the CDN"
            media, body = self.objects.get(url, ("", b""))
            return (200, {"content-type": media}, body) if body else (404, {}, b"")
        if "Authorization" not in headers:
            return 401, {"www-authenticate": 'Bearer realm="https://auth.docker.io/token",service="registry.docker.io"'}, b""
        media, body = self.objects.get(url, ("", b""))
        if media == "redirect":
            digest = url.rsplit("/", 1)[1]
            repo = url.split("/v2/example/")[1].split("/")[0]
            return 307, {"location": f"https://cdn.example/{repo}/{digest}"}, b""
        if not body:
            return 404, {}, b'{"errors":[{"code":"MANIFEST_UNKNOWN"}]}'
        return 200, {"content-type": media}, body


@pytest.fixture
def registries(monkeypatch) -> Registries:
    fake = Registries()
    monkeypatch.setattr(registry_module, "transport", fake)
    monkeypatch.setattr(platforms_module, "host_platform", lambda: PLATFORM)
    return fake


def _with_copy(template: AppTemplate, **images: str) -> AppTemplate:
    """``template`` with its database folder copied before an update and the images ``images`` (by
    container: main, database)."""
    database = template.companions[0]
    volumes = [v.model_copy(update={"copy_before_update": True}) if v.container == DATA else v for v in database.volumes]
    database = database.model_copy(update={"volumes": volumes, "image": images.get("database", database.image)})
    return template.model_copy(update={
        "image": images.get("main", template.image), "companions": [database, *template.companions[1:]],
        "version": images.get("version", template.version),
    })


V1 = _with_copy(PHOTOS)


@pytest.fixture
def versions(registries, monkeypatch):
    """Version 2.0 in the registries: its server and database on the base layer that 1.0's have too,
    each with a layer of its own; and its size records."""
    server = registries.image("example/photos-server", "2.0", created=BUILT_2,
                              layers=[(BASE, 1000), (SERVER_2_LAYER, 5000)])
    database = registries.image("example/photos-db", "14-2", created=BUILT_2, layers=[(BASE, 1000), (DB_2_LAYER, 7000)])
    monkeypatch.setitem(catalog_module.PINNED_IMAGES, server, PinnedImage("example/photos-server", "2.0",
                                                                          server.rsplit("@", 1)[1], 18000))
    monkeypatch.setitem(catalog_module.PINNED_IMAGES, database, PinnedImage("example/photos-db", "14-2",
                                                                            database.rsplit("@", 1)[1], 24000))
    v2 = _with_copy(PHOTOS, main=server, database=database, version="2.0")
    return v2, server, database


@pytest.fixture
def catalog(monkeypatch) -> list[AppTemplate]:
    """The catalog that the API answers, with Photos 1.0; a test replaces its last entry."""
    entries = [*APP_CATALOG, V1]
    monkeypatch.setattr(catalog_module, "APP_CATALOG", entries)
    monkeypatch.setattr(apps_router, "APP_CATALOG", entries)
    return entries


@pytest.fixture
def docker(monkeypatch, versions, catalog) -> StackDocker:
    """Docker with 1.0's images in its registry, built on BUILT_1, and 2.0's, with the ids that the
    registries name for the host's platform."""
    _, server, database = versions
    fake = StackDocker()
    # Each container is created a second after the one before, as an update's are after the install's.
    fake.create_times = (f"2026-11-18T10:{n // 60:02d}:{n % 60:02d}.000000000Z" for n in itertools.count())
    fake.publish(SERVER, volumes=("/data",), healthcheck=True, created=BUILT_1, diff_ids=(BASE, SERVER_1_LAYER))
    fake.publish(DATABASE, volumes=(DATA,), healthcheck=True, created=BUILT_1, diff_ids=(BASE, DB_1_LAYER))
    fake.publish(CACHE, volumes=("/data",), created=BUILT_1)
    fake.publish(LEARNING, created=BUILT_1)
    fake.publish(LEARNING_NPU, created=BUILT_1)
    registries = registry_module.transport
    fake.publish(server, volumes=("/data",), healthcheck=True, created=BUILT_2, diff_ids=(BASE, SERVER_2_LAYER),
                 image_id=registries.configs[server])
    fake.publish(database, volumes=(DATA,), healthcheck=True, created=BUILT_2, diff_ids=(BASE, DB_2_LAYER),
                 image_id=registries.configs[database])
    monkeypatch.setattr(apps_router, "get_client", lambda: fake)
    return fake


@pytest.fixture
def copies(monkeypatch) -> list[list[str]]:
    """cp -a of the database, as run_in_container runs it on the device: a copy of the folder here."""
    commands: list[list[str]] = []

    def run(cmd, *, timeout, **kwargs):
        commands.append(list(cmd))
        assert cmd[:4] == ["cp", "-a", "--reflink=auto", "--"] and timeout == updates_module.COPY_SECONDS, cmd
        shutil.copytree(cmd[4], cmd[5], symlinks=True)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(host_module, "run_in_container", run)
    return commands


def _database(pools) -> Path:
    return pools.alpha / "apps/photos/database/data"


@pytest.fixture
def installed(api, pools, docker, copies) -> StackDocker:
    """Photos 1.0 installed and running, with a database in its folder and a level of its log set at
    the install; the calls of its install forgotten."""
    response = install(api, {"template_id": "photos", "env_overrides": {"LOG_LEVEL": "debug"}})
    assert response.status_code == 200, response.text
    (_database(pools) / "PG_VERSION").write_text("14\n")
    docker.calls.clear()
    return docker


def _to_v2(catalog, versions) -> AppTemplate:
    catalog[-1] = versions[0]
    return versions[0]


def _job(api, response) -> dict:
    """The end of the job that ``response`` (202) started."""
    assert response.status_code == 202, response.text
    job = response.json()
    ended = api.get(f"{INSTALL}/jobs/{job['id']}", params={"wait": 30})
    assert ended.status_code == 200, ended.text
    assert ended.json()["state"] != "running", ended.json()
    return ended.json()


def _photos(api) -> dict:
    listed = api.get(INSTALLED)
    assert listed.status_code == 200, listed.text
    return next(app for app in listed.json() if app["template_id"] == "photos")


def _images(docker: StackDocker) -> dict[str, str]:
    return {name: live.attrs["Config"]["Image"] for name, live in docker.containers_.items()}


def _by_digest(ref: str) -> str:
    name_tag, _, digest = ref.partition("@")
    return f"{name_tag.rpartition(':')[0]}@{digest}"


V1_IMAGES = {
    "photos-database": _by_digest(DATABASE), "photos-cache": _by_digest(CACHE),
    "photos-machine-learning": _by_digest(LEARNING), "photos": _by_digest(SERVER),
}
CHANGES = ("api.pull", "container.stop", "container.start", "container.remove", "container.rename",
           "api.create_container", "networks.create", "network.remove", "api.tag")


class TestTheInstalledList:
    def test_an_app_whose_catalog_pins_other_images_offers_their_version(self, api, installed, catalog, versions):
        before = _photos(api)
        assert (before["version"], before["update_to"], before["previous"]) == ("1.0", None, None)

        _to_v2(catalog, versions)
        after = _photos(api)
        assert (after["version"], after["update_to"], after["previous"]) == ("1.0", "2.0", None)
        assert installed.called(*CHANGES) == [] and registry_module.transport.requests == []

    def test_an_image_that_a_hardware_profile_chose_is_no_update(self):
        stacks = importlib.import_module("src.apps.stacks")
        container = type("C", (), {})()
        container.name, container.labels = "photos-machine-learning", {"unos.app": "photos",
                                                                       "unos.app.companion": "machine-learning"}
        container.attrs = {"Config": {"Image": _by_digest(LEARNING_NPU)}}
        main = type("C", (), {})()
        main.name, main.labels, main.attrs = "photos", {"unos.app": "photos"}, {"Config": {"Image": _by_digest(SERVER)}}
        others = []
        for name in ("database", "cache"):
            c = type("C", (), {})()
            c.name, c.labels = f"photos-{name}", {"unos.app": "photos", "unos.app.companion": name}
            c.attrs = {"Config": {"Image": _by_digest(DATABASE if name == "database" else CACHE)}}
            others.append(c)
        app = stacks.group("photos", [main, container, *others], V1)
        assert stacks.update_to(app) is None


    def test_a_container_of_a_floating_tag_is_offered_the_version_that_the_catalog_now_pins(self):
        # The dev device's transmission was created from lscr.io/linuxserver/transmission:latest,
        # before its catalog pinned it (ADR-0410), and has no version label.
        stacks = importlib.import_module("src.apps.stacks")
        catalog = importlib.import_module("src.apps.catalog")
        container = type("C", (), {})()
        container.name, container.labels = "transmission", {"unos.app": "transmission"}
        container.attrs = {"Config": {"Image": "lscr.io/linuxserver/transmission:latest"}}

        app = stacks.group("transmission", [container], catalog.get_app_by_id("transmission"))

        assert stacks.update_to(app) == catalog.APP_IMAGES["transmission"].tag == "4.1.4-r0-ls370"

    @pytest.mark.parametrize("npu", [True, False])
    def test_immich_created_from_its_pinned_digests_is_offered_nothing(self, npu):
        # The dev device's Immich, whose containers name the digests of ADR-0381's pins, is never to
        # be updated: the catalog keeps those digests (ADR-0410).
        stacks = importlib.import_module("src.apps.stacks")
        catalog = importlib.import_module("src.apps.catalog")
        learning = catalog.IMMICH_ML_RKNN if npu else catalog.IMMICH_ML
        images = {"immich": catalog.IMMICH_SERVER, "database": catalog.IMMICH_POSTGRES, "redis": catalog.VALKEY,
                  "machine-learning": learning}
        containers = []
        for name, pin in images.items():
            c = type("C", (), {})()
            c.name = "immich" if name == "immich" else f"immich-{name}"
            c.labels = {"unos.app": "immich", **({} if name == "immich" else {"unos.app.companion": name})}
            c.attrs = {"Config": {"Image": f"{pin.name}@{pin.digest}"}}
            containers.append(c)

        app = stacks.group("immich", containers, catalog.get_app_by_id("immich"))

        assert app.missing == [] and stacks.update_to(app) is None


class TestThePlan:
    def test_it_shows_each_image_what_the_pulls_write_and_the_copy_and_changes_nothing(
        self, api, installed, catalog, versions, pools
    ):
        _, server, database = versions
        _to_v2(catalog, versions)
        tree = sorted(p.relative_to(pools.root) for p in pools.root.rglob("*"))

        response = api.get(f"{INSTALLED}/photos/update")
        assert response.status_code == 200, response.text
        plan = response.json()
        assert (plan["version_from"], plan["version_to"], plan["platform"]) == ("1.0", "2.0", PLATFORM)
        changes = {c["name"]: (c["image_from"], c["image_to"], c["changed"]) for c in plan["containers"]}
        assert changes == {
            "photos-database": (_by_digest(DATABASE), database, True),
            "photos-cache": (_by_digest(CACHE), CACHE, False),
            "photos-machine-learning": (_by_digest(LEARNING), LEARNING, False),
            "photos": (_by_digest(SERVER), server, True),
        }
        built = {c["name"]: (c["built_from"], c["built_to"]) for c in plan["containers"]}
        assert built["photos"] == ("2026-11-01T08:00:00.123456Z", "2026-11-17T08:00:00.987654Z")
        # The base layer is there (1.0's images have it): only each image's own layer is pulled,
        # 5000 and 7000 bytes of 6000 and 8000, and each writes that share of its size record.
        assert plan["download_bytes"] == 12000
        assert plan["system_disk_bytes"] == 18000 * 5000 // 6000 + 24000 * 7000 // 8000
        assert plan["system_disk_free_bytes"] == 64 * GIB
        [copy] = plan["copies"]
        assert copy["folder"] == str(_database(pools)) and copy["copy_folder"] == f"{_database(pools)}.previous"
        assert (copy["container"], copy["pool"]) == ("photos-database", "alpha") and copy["bytes"] > 0

        assert installed.called(*CHANGES) == []
        assert sorted(p.relative_to(pools.root) for p in pools.root.rglob("*")) == tree
        # Anonymous reads: the token handshake, then by digest, and the blob from the CDN.
        assert all(url.startswith("https://") for url, _ in registry_module.transport.requests)

    def test_the_same_image_under_another_index_is_no_change(self, api, installed, catalog, registries):
        # In the containerd image store Docker's id of an image is its index's digest, not its
        # configuration's: its layers and its time tell that it is the image that runs.
        repinned = registries.image("example/photos-server", "1.0", created=BUILT_1,
                                    layers=[(BASE, 1000), (SERVER_1_LAYER, 4000)])
        catalog[-1] = _with_copy(PHOTOS, main=repinned, version="1.0")
        response = api.get(f"{INSTALLED}/photos/update")
        assert response.status_code == 200, response.text
        main = next(c for c in response.json()["containers"] if c["name"] == "photos")
        assert (main["image_to"], main["changed"]) == (repinned, False)
        assert response.json()["download_bytes"] == 0

    def test_nothing_to_update(self, api, installed):
        response = api.get(f"{INSTALLED}/photos/update")
        assert response.status_code == 409
        assert response.json()["detail"] == "Photos runs the images that the catalog pins: there is nothing to update"
        assert registry_module.transport.requests == []

    def test_an_older_image_is_refused(self, api, installed, catalog, registries, monkeypatch):
        older = registries.image("example/photos-server", "0.9", created="2026-10-01T00:00:00Z",
                                 layers=[(BASE, 1000), (_layer("server 0.9"), 4000)])
        catalog[-1] = _with_copy(PHOTOS, main=older, version="0.9")
        response = api.get(f"{INSTALLED}/photos/update")
        assert response.status_code == 409
        assert "UNOS does not move an app to an older image" in response.json()["detail"]
        assert installed.called(*CHANGES) == []

    def test_an_image_without_a_time_is_refused(self, api, installed, catalog, registries):
        unknown = registries.image("example/photos-server", "2.1", created=None, layers=[(BASE, 1000)])
        catalog[-1] = _with_copy(PHOTOS, main=unknown, version="2.1")
        response = api.get(f"{INSTALLED}/photos/update")
        assert response.status_code == 409 and "cannot tell whether" in response.json()["detail"]

    def test_an_image_without_the_hosts_platform_is_refused(self, api, installed, catalog, registries):
        amd64 = registries.image("example/photos-server", "2.2", created=BUILT_2, layers=[(BASE, 1000)],
                                 platforms=("linux/amd64",))
        catalog[-1] = _with_copy(PHOTOS, main=amd64, version="2.2")
        response = api.get(f"{INSTALLED}/photos/update")
        assert response.status_code == 409
        assert response.json()["detail"] == (
            f"Photos cannot be updated on {PLATFORM}: {amd64} publishes no image for {PLATFORM}")

    def test_a_registry_that_does_not_answer(self, api, installed, catalog, versions, registries):
        _to_v2(catalog, versions)
        registries.error = registry_module.RegistryError("https://registry-1.docker.io/v2/x: connection refused")
        response = api.get(f"{INSTALLED}/photos/update")
        assert response.status_code == 503
        assert response.json()["detail"].endswith("; nothing was changed")

    def test_a_floating_tag_is_refused(self, api, pools, catalog, docker, soc):
        notes = AppTemplate(id="notes", name="Notes", description="d", icon="N", category=AppCategory.PRODUCTIVITY,
                            image="example/notes:latest", version="latest", ports=[PortMapping(host=8080, container=80)])
        catalog.append(notes)
        docker.publish("example/notes:latest")
        assert install(api, {"template_id": "notes"}).status_code == 200
        response = api.get(f"{INSTALLED}/notes/update")
        assert response.status_code == 409
        assert response.json()["detail"] == (
            "Notes's image floats on a tag (example/notes:latest): UNOS updates only an app whose catalog pins its "
            "images by digest"
        )
        # The update's job refuses it too, before it changes anything.
        job = _job(api, api.post(f"{INSTALLED}/notes/update"))
        assert (job["kind"], job["state"], job["error_status"]) == ("update", "failed", 409)
        assert job["error"].startswith("Notes's image floats on a tag")

    def test_a_copy_left_from_an_earlier_update_is_refused(self, api, installed, catalog, versions, pools):
        _to_v2(catalog, versions)
        Path(f"{_database(pools)}.previous").mkdir()
        response = api.get(f"{INSTALLED}/photos/update")
        assert response.status_code == 409
        assert response.json()["detail"] == (
            f"{_database(pools)}.previous is there from an earlier update of Photos: move it away first, since UNOS "
            "replaces no copy of a database"
        )

    def test_a_pool_without_room_for_the_copy_is_refused(self, api, installed, catalog, versions, monkeypatch):
        _to_v2(catalog, versions)
        real = shutil.disk_usage

        def tight(path):
            usage = real(path)
            return usage._replace(free=GIB) if str(path).endswith("database/data") else usage

        monkeypatch.setattr(updates_module.shutil, "disk_usage", tight)
        response = api.get(f"{INSTALLED}/photos/update")
        assert response.status_code == 409 and "needs room on the pool alpha" in response.json()["detail"]

    def test_a_system_disk_without_room_for_the_pulls_is_refused(self, api, installed, catalog, versions, soc):
        _to_v2(catalog, versions)
        soc.free = {key: preflight_module.FREE_SPACE_FLOOR + 1000 for key in soc.free}
        response = api.get(f"{INSTALLED}/photos/update")
        assert response.status_code == 409
        assert response.json()["detail"].startswith("The update of Photos writes about 1 MiB to the system disk")

    def test_an_incomplete_app_is_refused(self, api, installed, catalog, versions):
        _to_v2(catalog, versions)
        installed.container("photos-cache").remove(force=True)
        response = api.get(f"{INSTALLED}/photos/update")
        assert response.status_code == 409
        assert response.json()["detail"].startswith("Photos is incomplete (missing: photos-cache)")

    def test_only_administrators(self, user_api):
        for method, path in (("GET", "update"), ("POST", "update"), ("POST", "rollback"), ("DELETE", "previous")):
            assert user_api.request(method, f"{INSTALLED}/photos/{path}").status_code == 403


class TestTheUpdate:
    def test_it_keeps_the_data_settings_and_ports_copies_the_database_and_keeps_the_earlier_containers(
        self, api, installed, catalog, versions, pools, copies
    ):
        _, server, database = versions
        _to_v2(catalog, versions)
        mounts_before = {n: installed.containers_[n].attrs["Mounts"] for n in NAMES}
        ports_before = installed.containers_["photos"].attrs["HostConfig"]["PortBindings"]
        installed.container("photos-database").update(mem_limit=512 * 1024**2, memswap_limit=512 * 1024**2)

        job = _job(api, api.post(f"{INSTALLED}/photos/update"))
        assert (job["kind"], job["state"]) == ("update", "succeeded"), job
        assert [image["image"] for image in job["images"]] == [database, CACHE, LEARNING, server]
        assert {image["image"]: image["state"] for image in job["images"]}[CACHE] == "present"
        # The order: pulls, the stop in reverse, the copy, the renames, then the creates and starts.
        calls = [c for c in installed.called("api.pull", "container.stop", "container.rename", "api.create_container",
                                             "container.start")]
        assert calls[:2] == [("api.pull", _by_digest(database)), ("api.pull", _by_digest(server))]
        assert calls[2:6] == [("container.stop", n) for n in reversed(NAMES)]
        assert calls[6:10] == [("container.rename", n) for n in NAMES]
        assert calls[10:] == [("api.create_container", n) for n in NAMES] + [("container.start", n) for n in NAMES]
        assert copies == [["cp", "-a", "--reflink=auto", "--", str(_database(pools)), f"{_database(pools)}.copying"]]
        assert (Path(f"{_database(pools)}.previous") / "PG_VERSION").read_text() == "14\n"
        assert not Path(f"{_database(pools)}.copying").exists()

        new = {n: installed.containers_[n].attrs for n in NAMES}
        assert {n: a["Config"]["Image"] for n, a in new.items()} == {
            **V1_IMAGES, "photos-database": _by_digest(database), "photos": _by_digest(server)}
        assert {n: a["Mounts"] for n, a in new.items()} == mounts_before
        assert new["photos"]["HostConfig"]["PortBindings"] == ports_before
        assert "LOG_LEVEL=debug" in new["photos"]["Config"]["Env"]
        assert new["photos-database"]["HostConfig"]["Memory"] == 512 * 1024**2
        assert all(a["Config"]["Labels"]["unos.app.version"] == "2.0" for a in new.values())
        assert all(installed.containers_[n].status == "running" for n in NAMES)
        earlier = {f"{n}.previous": installed.containers_[f"{n}.previous"] for n in NAMES}
        assert {n: c.attrs["Config"]["Image"] for n, c in earlier.items()} == {
            f"{n}.previous": image for n, image in V1_IMAGES.items()}
        assert all(c.status == "exited" for c in earlier.values())

        listed = _photos(api)
        assert (listed["version"], listed["update_to"]) == ("2.0", None)
        assert listed["previous"] == {
            "version": "1.0", "containers": [f"{n}.previous" for n in NAMES], "images": list(V1_IMAGES.values()),
            "copies": [f"{_database(pools)}.previous"],
        }
        [entry] = [e for e in _audit() if e["action"] == AuditAction.APP_UPDATED.value]
        assert entry["success"] and entry["resource_id"] == "photos"
        assert entry["details"].startswith("Updated Photos from 1.0 to 2.0 (photos-database: ")
        assert f"copied {_database(pools)} to {_database(pools)}.previous before the new version started" in entry["details"]
        # A second update waits for the earlier version's rollback or removal.
        again = api.get(f"{INSTALLED}/photos/update")
        assert again.status_code == 409 and "is still there" in again.json()["detail"]

    def test_a_failure_in_the_middle_brings_the_earlier_containers_back(self, api, installed, catalog, versions, pools):
        _to_v2(catalog, versions)
        installed.fail("api.create_container", "photos", APIError("500 Server Error", explanation="no space left"))
        before = {n: installed.containers_[n].id for n in NAMES}

        job = _job(api, api.post(f"{INSTALLED}/photos/update"))
        assert (job["state"], job["step"], job["error_status"]) == ("failed", "restoring", 500), job
        assert job["error"].startswith("Failed to create container photos: ")
        assert job["error"].endswith(
            "Photos was returned to its earlier version, its database from the copy made before the update")
        assert {n: installed.containers_[n].id for n in NAMES} == before
        assert _images(installed) == V1_IMAGES
        assert all(installed.containers_[n].status == "running" for n in NAMES)
        assert (_database(pools) / "PG_VERSION").read_text() == "14\n"
        for suffix in (".previous", ".copying", ".rolled-back"):
            assert not Path(f"{_database(pools)}{suffix}").exists()
        [entry] = [e for e in _audit() if e["action"] == AuditAction.APP_UPDATED.value]
        assert not entry["success"] and "returned to its earlier version" in entry["details"]

    def test_a_new_version_that_does_not_start_is_undone_with_the_copy(self, api, installed, catalog, versions, pools):
        _, server, database = versions
        _to_v2(catalog, versions)
        installed.script_image(_by_digest(server), {"Status": "exited", "Running": False, "ExitCode": 1})

        def migrate(container):
            if container.attrs["Config"]["Image"] == _by_digest(database):
                (_database(pools) / "migrated").write_text("2.0\n")

        installed.start_hook = migrate
        job = _job(api, api.post(f"{INSTALLED}/photos/update"))
        assert job["state"] == "failed" and job["error"].startswith("photos stopped (exit code 1)"), job
        assert _images(installed) == V1_IMAGES
        assert not (_database(pools) / "migrated").exists()
        assert (_database(pools) / "PG_VERSION").read_text() == "14\n"
        assert not Path(f"{_database(pools)}.rolled-back").exists()
        assert not Path(f"{_database(pools)}.previous").exists()

    def test_a_cancel_during_a_pull_changes_nothing(self, api, installed, catalog, versions):
        _, _, database = versions
        _to_v2(catalog, versions)
        installed.stall(_by_digest(database), 2, block=True)
        started = api.post(f"{INSTALLED}/photos/update")
        assert started.status_code == 202
        job_id = started.json()["id"]
        for _ in range(200):
            job = api.get(f"{INSTALL}/jobs/{job_id}").json()
            if job["step"] == "pulling" and any(i["state"] == "pulling" for i in job["images"]):
                break
        assert api.delete(f"{INSTALL}/jobs/{job_id}").status_code == 202
        job = api.get(f"{INSTALL}/jobs/{job_id}", params={"wait": 30}).json()
        assert (job["state"], job["error"]) == ("cancelled", "Cancelled by admin before Photos was stopped: nothing was changed")
        assert installed.called("container.stop", "container.rename", "api.create_container") == []
        assert all(installed.containers_[n].status == "running" for n in NAMES)

    def test_a_stop_of_the_supervisor_leaves_the_app_as_it_is_and_says_so(
        self, api, installed, catalog, versions, pools, monkeypatch
    ):
        _to_v2(catalog, versions)
        real = host_module.run_in_container

        def copy_then_stop(cmd, *, timeout, **kwargs):
            done = real(cmd, timeout=timeout, **kwargs)
            install_jobs.stop_for_shutdown()
            return done

        monkeypatch.setattr(host_module, "run_in_container", copy_then_stop)
        job = _job(api, api.post(f"{INSTALLED}/photos/update"))
        assert (job["state"], job["error_status"]) == ("failed", 503), job
        assert job["error"] == (
            "A stop of the supervisor stopped the update of Photos after it stopped photos, photos-machine-learning, "
            f"photos-cache and photos-database; copied {_database(pools)}: start Photos on the Installed tab"
        )
        assert installed.called("container.rename", "api.create_container") == []
        assert all(installed.containers_[n].status == "exited" for n in NAMES)
        [entry] = [e for e in _audit() if e["action"] == AuditAction.APP_UPDATED.value]
        assert not entry["success"] and entry["details"].endswith("then: stopped by a stop of the supervisor")

    def test_one_job_at_a_time(self, api, installed, catalog, versions):
        _, _, database = versions
        _to_v2(catalog, versions)
        installed.stall(_by_digest(database), 2, block=True)
        started = api.post(f"{INSTALLED}/photos/update")
        try:
            assert api.post(f"{INSTALLED}/photos/update").json()["detail"] == (
                "Photos is being updated already: wait for the end of its update")
            assert api.post(INSTALL, json={"template_id": "photos"}).status_code == 409
            assert api.post(f"{INSTALLED}/photos/stop").status_code == 409
        finally:
            api.delete(f"{INSTALL}/jobs/{started.json()['id']}")
            api.get(f"{INSTALL}/jobs/{started.json()['id']}", params={"wait": 30})


@pytest.fixture
def updated(api, installed, catalog, versions, pools) -> StackDocker:
    """Photos updated to 2.0, whose database migrated its folder at its first start."""
    _, _, database = versions
    _to_v2(catalog, versions)

    def migrate(container):
        if container.attrs["Config"]["Image"] == _by_digest(database):
            (_database(pools) / "migrated").write_text("2.0\n")

    installed.start_hook = migrate
    job = _job(api, api.post(f"{INSTALLED}/photos/update"))
    assert job["state"] == "succeeded", job
    installed.start_hook = None
    installed.calls.clear()
    return installed


class TestTheRollback:
    def test_it_returns_to_the_earlier_containers_and_the_copy(self, api, updated, pools):
        job = _job(api, api.post(f"{INSTALLED}/photos/rollback"))
        assert (job["kind"], job["state"]) == ("rollback", "succeeded"), job
        assert sorted(updated.containers_) == sorted(NAMES)
        assert _images(updated) == V1_IMAGES
        assert all(updated.containers_[n].status == "running" for n in NAMES)
        assert not (_database(pools) / "migrated").exists() and (_database(pools) / "PG_VERSION").exists()
        for suffix in (".previous", ".rolled-back"):
            assert not Path(f"{_database(pools)}{suffix}").exists()
        listed = _photos(api)
        assert (listed["version"], listed["update_to"], listed["previous"]) == ("1.0", "2.0", None)
        [entry] = [e for e in _audit() if e["action"] == AuditAction.APP_ROLLED_BACK.value]
        assert entry["success"] and entry["details"].startswith("Rolled Photos back to its earlier version")

    def test_without_an_earlier_version(self, api, installed):
        response = api.post(f"{INSTALLED}/photos/rollback")
        assert response.status_code == 409 and response.json()["detail"] == "Photos has no earlier version to roll back to"

    def test_without_the_copy(self, api, updated, pools):
        shutil.rmtree(f"{_database(pools)}.previous")
        job = _job(api, api.post(f"{INSTALLED}/photos/rollback"))
        assert (job["state"], job["error_status"]) == ("failed", 409)
        assert "the copy of" in job["error"] and "is missing" in job["error"]
        assert updated.called("container.stop", "container.remove", "container.rename") == []

    def test_it_cannot_be_cancelled(self, api, updated, monkeypatch):
        blocked = __import__("threading").Event()
        release = __import__("threading").Event()
        real = apps_router._stop_all

        def slow(*args, **kwargs):
            blocked.set()
            release.wait(10)
            return real(*args, **kwargs)

        monkeypatch.setattr(apps_router, "_stop_all", slow)
        started = api.post(f"{INSTALLED}/photos/rollback")
        assert blocked.wait(10)
        refused = api.delete(f"{INSTALL}/jobs/{started.json()['id']}")
        release.set()
        assert refused.status_code == 409
        assert refused.json()["detail"] == "The rollback of Photos cannot be cancelled: it ends on its own"
        assert _job(api, started)["state"] == "succeeded"


@pytest.fixture
def containers_page(monkeypatch):
    """The Containers page's API on the same fake Docker as the apps'."""
    monkeypatch.setattr(importlib.import_module("src.containers.docker"), "get_client", lambda: apps_router.get_client())


class TestTheEarlierVersionIsGuarded:
    def test_the_containers_page_does_not_start_an_earlier_container(self, api, updated, containers_page):
        response = api.post("/api/v1/containers/photos-database.previous/start")
        assert response.status_code == 409
        assert response.json()["detail"] == (
            "photos-database.previous belongs to the earlier version of photos that its update kept, which does not "
            "start beside the newer one: roll back to it on the App Store's Installed tab"
        )
        assert updated.containers_["photos-database.previous"].status == "exited"
        assert api.post("/api/v1/containers/photos.previous/restart").status_code == 409

    def test_the_containers_page_waits_for_an_update(self, api, installed, catalog, versions, containers_page):
        _, _, database = versions
        _to_v2(catalog, versions)
        installed.container("photos-cache").stop()
        installed.stall(_by_digest(database), 2, block=True)
        started = api.post(f"{INSTALLED}/photos/update")
        try:
            response = api.post("/api/v1/containers/photos-cache/start")
            assert response.status_code == 409
            assert response.json()["detail"] == "Another change of photos is under way; try again when it has finished"
            assert api.delete("/api/v1/containers/photos-cache").status_code == 409
        finally:
            api.delete(f"{INSTALL}/jobs/{started.json()['id']}")
            api.get(f"{INSTALL}/jobs/{started.json()['id']}", params={"wait": 30})

    def test_a_cancel_after_the_stop_returns_the_app_and_deletes_the_unused_copy(
        self, api, installed, catalog, versions, pools, monkeypatch
    ):
        _to_v2(catalog, versions)
        real = host_module.run_in_container

        def copy_then_cancel(cmd, *, timeout, **kwargs):
            done = real(cmd, timeout=timeout, **kwargs)
            [job] = [j for j in install_jobs.current() if j.state == "running"]
            install_jobs.cancel(job.id, "admin")
            return done

        monkeypatch.setattr(host_module, "run_in_container", copy_then_cancel)
        job = _job(api, api.post(f"{INSTALLED}/photos/update"))
        assert job["state"] == "cancelled", job
        assert job["error"] == "Cancelled by admin; Photos was returned to its earlier version"
        assert _images(installed) == V1_IMAGES
        assert all(installed.containers_[n].status == "running" for n in NAMES)
        assert not Path(f"{_database(pools)}.previous").exists()

    def test_a_stop_after_the_renames_is_rolled_back_from_the_installed_tab(
        self, api, installed, catalog, versions, pools, monkeypatch
    ):
        _to_v2(catalog, versions)
        real = apps_router._prepare_sources

        def stop_first(planned, **kwargs):
            if kwargs.get("create"):
                install_jobs.stop_for_shutdown()
            return real(planned, **kwargs)

        monkeypatch.setattr(apps_router, "_prepare_sources", stop_first)
        job = _job(api, api.post(f"{INSTALLED}/photos/update"))
        assert (job["state"], job["error_status"]) == ("failed", 503), job
        assert job["error"].endswith(": roll Photos back on the Installed tab")
        assert sorted(installed.containers_) == sorted(f"{n}.previous" for n in NAMES)
        monkeypatch.setattr(apps_router, "_prepare_sources", real)

        listed = _photos(api)
        assert listed["status"] == "missing" and listed["previous"]["containers"] == [f"{n}.previous" for n in NAMES]
        job = _job(api, api.post(f"{INSTALLED}/{listed['container_id']}/rollback"))
        assert job["state"] == "succeeded", job
        assert _images(installed) == V1_IMAGES
        assert all(installed.containers_[n].status == "running" for n in NAMES)
        assert (_database(pools) / "PG_VERSION").read_text() == "14\n"
        assert not Path(f"{_database(pools)}.previous").exists()

    def test_a_copy_that_cannot_be_put_back_leaves_the_earlier_version_for_a_rollback(
        self, api, installed, catalog, versions, pools, monkeypatch
    ):
        _, server, database = versions
        _to_v2(catalog, versions)
        installed.script_image(_by_digest(server), {"Status": "exited", "Running": False, "ExitCode": 1})

        def migrate(container):
            if container.attrs["Config"]["Image"] == _by_digest(database):
                (_database(pools) / "migrated").write_text("2.0\n")

        installed.start_hook = migrate
        real = updates_module._rename
        broken = {"on": True}

        def rename(source, target, pool):
            if broken["on"] and source.name.endswith(".previous"):
                raise OSError(5, "Input/output error")
            return real(source, target, pool)

        monkeypatch.setattr(updates_module, "_rename", rename)
        job = _job(api, api.post(f"{INSTALLED}/photos/update"))
        assert job["state"] == "failed", job
        assert "could not put the copy" in job["error"]
        assert "The earlier containers stay stopped as photos-database.previous" in job["error"]
        # Nothing starts on the migrated folder: the earlier containers keep their names, stopped.
        assert sorted(installed.containers_) == sorted(f"{n}.previous" for n in NAMES)
        assert all(c.status == "exited" for c in installed.containers_.values())
        assert (_database(pools) / "migrated").exists() and Path(f"{_database(pools)}.previous").is_dir()
        assert not Path(f"{_database(pools)}.rolled-back").exists()

        broken["on"] = False
        installed.start_hook = None
        listed = _photos(api)
        job = _job(api, api.post(f"{INSTALLED}/{listed['container_id']}/rollback"))
        assert job["state"] == "succeeded", job
        assert not (_database(pools) / "migrated").exists() and (_database(pools) / "PG_VERSION").exists()

    def test_the_copy_is_not_deleted_while_the_database_folder_is_missing(self, api, updated, pools):
        shutil.rmtree(_database(pools))
        response = api.delete(f"{INSTALLED}/photos/previous")
        assert response.status_code == 409
        assert "may be the only copy of Photos's database" in response.json()["detail"]
        assert Path(f"{_database(pools)}.previous").is_dir()
        assert "photos.previous" in updated.containers_
        rollback = _job(api, api.post(f"{INSTALLED}/photos/rollback"))
        assert (rollback["state"], rollback["error_status"]) == ("failed", 409)
        assert updated.called("container.stop", "container.remove", "container.rename") == []


class TestRemovingTheEarlierVersion:
    def test_it_removes_the_earlier_containers_and_the_copy(self, api, updated, pools):
        response = api.delete(f"{INSTALLED}/photos/previous")
        assert response.status_code == 200, response.text
        assert sorted(updated.containers_) == sorted(NAMES)
        assert not Path(f"{_database(pools)}.previous").exists()
        assert (_database(pools) / "migrated").exists()
        assert _photos(api)["previous"] is None
        [entry] = [e for e in _audit() if e["action"] == AuditAction.APP_EARLIER_VERSION_REMOVED.value]
        assert entry["details"] == (
            "Removed the earlier version of Photos that its update kept: the containers photos.previous, "
            "photos-machine-learning.previous, photos-cache.previous and photos-database.previous; deleted the copy of "
            f"its database {_database(pools)}.previous"
        )
        assert api.delete(f"{INSTALLED}/photos/previous").status_code == 409

    def test_an_uninstall_removes_the_earlier_containers_and_keeps_the_copy(self, api, updated, pools):
        response = api.delete(UNINSTALL.format("photos"))
        assert response.status_code == 200, response.text
        assert updated.containers_ == {}
        assert Path(f"{_database(pools)}.previous").is_dir()
        [entry] = [e for e in _audit() if e["action"] == "app_uninstalled"]
        assert "photos.previous" in entry["details"]


class TestTheRegistry:
    def test_a_manifest_of_another_digest_is_refused(self, registries):
        ref = registries.image("example/photos-server", "3.0", created=BUILT_2, layers=[(BASE, 1)])
        url = next(u for u in registries.objects if u.endswith(ref.rsplit("@", 1)[1]))
        media, body = registries.objects[url]
        registries.objects[url] = (media, body + b" ")
        with pytest.raises(registry_module.RegistryError, match="an answer of another digest"):
            registry_module.read(ref, PLATFORM)

    def test_a_floating_tag_is_not_read(self):
        with pytest.raises(registry_module.RegistryError, match="is not pinned by a digest"):
            registry_module.read("example/notes:latest", PLATFORM)

    def test_the_chain_of_layers(self):
        chains = registry_module.chain_ids([BASE, DB_1_LAYER])
        assert chains[0] == BASE
        assert chains[1] == "sha256:" + hashlib.sha256(f"{BASE} {DB_1_LAYER}".encode()).hexdigest()
        # The same layer above another base is another layer of the store.
        assert registry_module.chain_ids([SERVER_1_LAYER, DB_1_LAYER])[1] != chains[1]


class TestTheCatalog:
    def test_immichs_database_is_copied_before_an_update(self):
        immich = next(app for app in APP_CATALOG if app.id == "immich")
        marked = [(c.name, v.host) for c in immich.companions for v in c.volumes if v.copy_before_update]
        assert marked == [("database", "{pool}/apps/immich/postgres/data")]

    def test_only_folders_of_an_apps_own_data_are_copied(self):
        for app in APP_CATALOG:
            for spec in (app, *app.companions):
                for volume in spec.volumes:
                    if volume.copy_before_update:
                        assert volume.host.startswith("{pool}/apps/") and not volume.is_file
                        assert not volume.generated_secret and not volume.readonly

    def test_every_app_restarts_unless_stopped(self):
        # The earlier containers that an update keeps stay stopped at a boot only so (ADR-0408).
        assert {app.restart_policy for app in APP_CATALOG} == {"unless-stopped"}
