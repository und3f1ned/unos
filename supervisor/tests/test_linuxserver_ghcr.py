"""LinuxServer's images of the catalog are pinned and pulled as ghcr.io/linuxserver/<name>, by the
tags and index digests that their lscr.io pins had (apps.linuxserver-images-from-ghcr, ADR-0426).

lscr.io is LinuxServer's own redirector to ghcr.io, which serves the same images by the same index
digests: on the dev device's network Docker's downloads of lscr.io's blobs failed, a pull of the
same image from ghcr.io completed. The installs and the update run against the strict fake Docker
of docker_fake.py; the registry is GHCR as it served transmission's pinned index, its linux/arm64
manifest and its configuration on 2026-11-19 (fixtures/ghcr_transmission.json), behind its
anonymous token handshake, with the configuration's blob on its CDN. No test reaches the network.
"""

from __future__ import annotations

import importlib
import itertools
import json
import urllib.parse
from pathlib import Path

import pytest

from src.apps.catalog import APP_CATALOG, get_app_by_id

from . import test_apps, test_catalog_platforms_script
from .docker_fake import StackDocker
from .test_app_stacks import clock  # noqa: F401
from .test_app_updates import _job
from .test_apps import DOCKER_ROOT, INSTALLED, TRANSMISSION, install

api = test_apps.api
pools = test_apps.pools
soc = test_apps.soc
shares = test_apps.shares
chown_calls = test_apps.chown_calls

apps_router = importlib.import_module("src.apps.router")
catalog_module = importlib.import_module("src.apps.catalog")
registry_module = importlib.import_module("src.apps.registry")
platforms_module = importlib.import_module("src.apps.platforms")
preflight_module = importlib.import_module("src.apps.preflight")

PLATFORM = "linux/arm64"

# Each LinuxServer image of the catalog with the tag and the index digest that ghcr.io named for
# that tag on 2026-11-19 (its anonymous token, then a HEAD of the tag's manifest): the digests of
# the lscr.io pins of ADR-0410 and ADR-0415, all 22 unchanged.
LINUXSERVER_PINS = {
    "bazarr": ("v1.6.2-ls373", "sha256:e0c5d45965242830c5eeddaafe9ae2b5e8ca3f6c01ecda9ee9364f550921dc50"),
    "bookstack": ("v26.09.3-ls299", "sha256:c741211356966fd38f8fea10483f341ca29c41cdd1b96b5cfcdd4312f26d7f97"),
    "calibre-web": ("0.6.27-ls410", "sha256:adb69d75b9c252e580fc691096f858833b901b5d106ae4080fb476bab104d946"),
    "code-server": ("4.145.2-ls377", "sha256:20ffca4512fcfc85dcd5612fb769233c1553192392d2ec417f0859135805f40c"),
    "deluge": ("2.2.0-ls387", "sha256:e2a537991884a514b64d7adf857e39b5e9239a1c76e52114e52bf10707dde772"),
    "duplicati": ("v2.4.1.0_stable_2026-10-29-ls318",
                  "sha256:ea137d806f2c0556468f2c620bcac963f0825bf45876de274988b42ff13b453a"),
    "emby": ("4.10.2.0-ls307", "sha256:6149cec5ced1b071e9ed6dcce1e2690b679146a982ad311f7f9604be83eb859a"),
    "heimdall": ("v2.8.4-ls373", "sha256:47f70751f900be4499017a48c01206c02ab9d18f34a580c6213a13d24cdc1a4a"),
    "lidarr": ("3.1.6.5079-ls49", "sha256:8fb40ada166dd1880cb1b166df5bcbb081107523f20ab60df05c313e2052a467"),
    "nextcloud": ("35.0.3-ls2", "sha256:cc5a5cf7385e1a958fd4645aad76d0557ed0f58c8267825329ac7f7980fe0265"),
    "nzbget": ("v26.3-ls272", "sha256:17e13511d47b1439af19b7ef17d08c8643eb2a5f009b1d3f800b704ebe590218"),
    "overseerr": ("v1.35.0-ls158", "sha256:53a1b839ffa81f139e1552840772bff8a5d13f5c78a69cce22458b9a6cd0844d"),
    "prowlarr": ("3.0.2.5780-ls169", "sha256:45117ba8439e2337a92ffe38a451a4dafc0be771754d05d88cdf102419d09646"),
    "qbittorrent": ("5.3.0_v2.1.2-ls487", "sha256:3d4c4545d51a690a0df15a2ff99038823a88a9822df8e794526447b9afbd7384"),
    "radarr": ("7.0.3.10918-ls328", "sha256:044788f5508b7f67a3e0368cdd142c77c4d4cd5aec2efaba53e1a68b6b6231e7"),
    "sabnzbd": ("5.1.3-ls281", "sha256:b356d0a9b22db584e9005364ec4e752dfc081ce501b7145e9a5c0bb38e4d1ba4"),
    "sonarr": ("4.0.20.3014-ls5", "sha256:c7d1df25e428da6c9ace05daea0d34e59522978a3843ea1944812f855af4086c"),
    "syncthing": ("v2.1.7-ls237", "sha256:c4207d8f5b66d5f67eedb636b75500b5a04898e9f33299d252f4b966c41a4128"),
    "tautulli": ("v2.19.1-ls252", "sha256:5e8f80cc18f03c606a3567c8a9b37d80812ed8017d2e2f8ff2e2a91b54a3aef1"),
    "transmission": ("4.1.4-r0-ls370", "sha256:5baf08cd73a566699f35baad3c4a84ba7d7057220566c5fb29969bf2292dc2ff"),
    "wikijs": ("v2.5.317-ls232", "sha256:cc4d796a746304d2189de7f815f86462e9762164347972f47fdd67a6b743413e"),
    "wireguard": ("1.0.20260223-r0-ls130", "sha256:10db56ad834f94541307d5dfdbe87370e26d155bb341a5bf65789f970f24ab5c"),
}

# Transmission's pin, as the update creates its container from it (name@digest), and the image that
# the dev device's transmission was created from before the catalog pinned it (ADR-0410).
GHCR_TRANSMISSION = "ghcr.io/linuxserver/transmission"
TRANSMISSION_TAG, TRANSMISSION_DIGEST = LINUXSERVER_PINS["transmission"]
TRANSMISSION_PIN = f"{GHCR_TRANSMISSION}:{TRANSMISSION_TAG}@{TRANSMISSION_DIGEST}"
TRANSMISSION_BY_DIGEST = f"{GHCR_TRANSMISSION}@{TRANSMISSION_DIGEST}"
DEVICE_IMAGE = "lscr.io/linuxserver/transmission:latest"
DEVICE_IMAGE_BUILT = "2026-10-27T05:12:30.123456789Z"

RECORDED = json.loads((Path(__file__).parent / "fixtures" / "ghcr_transmission.json").read_text())
_BLOB = "https://ghcr.io/v2/linuxserver/transmission/blobs/"
CONFIG_DIGEST = next(url.removeprefix(_BLOB) for url in RECORDED if url.startswith(_BLOB))
CONFIG = json.loads(RECORDED[_BLOB + CONFIG_DIGEST][2])
ARM64 = json.loads(next(body for url, (_, answer, body) in RECORDED.items()
                        if answer["content-type"] == "application/vnd.oci.image.manifest.v1+json"))
CDN = "https://pkg-containers.githubusercontent.com/ghcr1/blobs/"


def _all_images() -> list[str]:
    """Every image reference of the catalog: the pins, and each container's own and its hardware
    profiles' images."""
    specs = [spec for template in APP_CATALOG for spec in (template, *template.companions)]
    return [*catalog_module.PINNED_IMAGES, *(image for spec in specs for image in test_apps._images(spec))]


class Ghcr:
    """GHCR as an update reads it (src/apps/registry.py): 401 with its Bearer challenge for a request
    without a token, an anonymous token for linuxserver/transmission's pull, the recorded manifests,
    and the configuration's blob redirected to its CDN, which takes no token. Any other host fails
    the test; ``requests`` logs each URL."""

    def __init__(self) -> None:
        self.requests: list[str] = []

    def __call__(self, url: str, headers: dict) -> tuple[int, dict, bytes]:
        self.requests.append(url)
        parsed = urllib.parse.urlsplit(url)
        if url.startswith(CDN):
            assert "Authorization" not in headers, "the registry's token goes to the CDN"
            status, answer, body = RECORDED[_BLOB + url.removeprefix(CDN)]
            return status, answer, body.encode()
        assert parsed.hostname == "ghcr.io", f"a read of {url}: LinuxServer's images are read from ghcr.io"
        if parsed.path == "/token":
            query = urllib.parse.parse_qs(parsed.query)
            assert query == {"scope": ["repository:linuxserver/transmission:pull"], "service": ["ghcr.io"]}, query
            return 200, {}, json.dumps({"token": "anonymous"}).encode()
        if headers.get("Authorization") != "Bearer anonymous":
            return 401, {"www-authenticate": 'Bearer realm="https://ghcr.io/token",service="ghcr.io",'
                                             'scope="repository:linuxserver/transmission:pull"'}, b""
        if url.startswith(_BLOB):
            return 307, {"location": CDN + url.removeprefix(_BLOB)}, b""
        if url not in RECORDED:
            return 404, {}, b'{"errors":[{"code":"MANIFEST_UNKNOWN"}]}'
        status, answer, body = RECORDED[url]
        return status, answer, body.encode()


@pytest.fixture
def ghcr(monkeypatch) -> Ghcr:
    fake = Ghcr()
    monkeypatch.setattr(registry_module, "transport", fake)
    return fake


@pytest.fixture
def docker(monkeypatch) -> StackDocker:
    """Docker on an arm64 host, with transmission's pin in the registry as GHCR serves it: its
    configuration's digest as its id, its build time and its layers; and the image of
    lscr.io/linuxserver/transmission:latest, built before, which the dev device's transmission runs."""
    fake = StackDocker()
    fake.create_times = (f"2026-11-19T10:{n // 60:02d}:{n % 60:02d}.000000000Z" for n in itertools.count())
    fake.publish(TRANSMISSION_PIN, volumes=("/config",), created=CONFIG["created"],
                 diff_ids=tuple(CONFIG["rootfs"]["diff_ids"]), image_id=CONFIG_DIGEST)
    fake.publish(DEVICE_IMAGE, volumes=("/config",), created=DEVICE_IMAGE_BUILT,
                 diff_ids=("sha256:" + "1" * 64, "sha256:" + "2" * 64))
    fake.pulled(DEVICE_IMAGE)
    monkeypatch.setattr(apps_router, "get_client", lambda: fake)
    monkeypatch.setattr(platforms_module, "host_platform", lambda: PLATFORM)
    return fake


def _subjects(docker: StackDocker) -> list[str]:
    return [str(call.subject) for call in docker.calls]


class TestTheCatalog:
    def test_no_image_of_the_catalog_is_named_on_lscr_io(self):
        # LinuxServer's redirector: Docker's pulls from it did not complete on the dev device's
        # network, while ghcr.io serves the same images (ADR-0426).
        assert [ref for ref in _all_images() if ref.startswith("lscr.io/")] == []

    def test_each_linuxserver_image_is_on_ghcr_with_the_digest_that_ghcr_names_for_its_tag(self):
        pins = {pin.name: (pin.tag, pin.digest) for pin in catalog_module.PINNED_IMAGES.values()
                if "linuxserver/" in pin.name}
        assert pins == {f"ghcr.io/linuxserver/{name}": pin for name, pin in LINUXSERVER_PINS.items()}
        assert catalog_module.APP_IMAGES["transmission"].ref == TRANSMISSION_PIN
        assert catalog_module.BOOKSTACK.ref == (
            "ghcr.io/linuxserver/bookstack:v26.09.3-ls299"
            "@sha256:c741211356966fd38f8fea10483f341ca29c41cdd1b96b5cfcdd4312f26d7f97"
        )
        # The templates take their images from the pins: the App Store's images are ghcr.io's.
        assert get_app_by_id("transmission").image == TRANSMISSION_PIN
        assert get_app_by_id("bookstack").image == catalog_module.BOOKSTACK.ref

    def test_the_registry_reads_a_linuxserver_pin_from_ghcr_and_knows_no_alias_of_lscr_io(self):
        assert registry_module.parse_reference(TRANSMISSION_PIN) == (
            "ghcr.io", "linuxserver/transmission", TRANSMISSION_DIGEST)
        # The registry reader and the audit read only the catalog's references, none of them on
        # lscr.io: such a reference is read where it names, never rewritten to ghcr.io (ADR-0207).
        lscr = f"lscr.io/linuxserver/transmission:{TRANSMISSION_TAG}@{TRANSMISSION_DIGEST}"
        assert registry_module.parse_reference(lscr) == ("lscr.io", "linuxserver/transmission", TRANSMISSION_DIGEST)
        assert test_catalog_platforms_script.tool.parse_reference(lscr) == (
            "lscr.io", "linuxserver/transmission", TRANSMISSION_DIGEST)

    def test_the_platforms_of_each_linuxserver_pin_are_recorded_under_its_ghcr_name(self):
        recorded = json.loads(Path(platforms_module.__file__).with_name("image_platforms.json").read_text())["images"]
        assert [ref for ref in recorded if ref.startswith("lscr.io/")] == []
        for name, (tag, digest) in LINUXSERVER_PINS.items():
            assert PLATFORM in recorded[f"ghcr.io/linuxserver/{name}:{tag}@{digest}"], name


class TestTheInstall:
    def test_a_linuxserver_app_is_pulled_from_ghcr_by_its_digest_and_created_from_it(self, api, pools, docker):
        response = install(api, TRANSMISSION)

        assert response.status_code == 200, response.text
        assert docker.called("api.pull", "api.tag") == [("api.pull", TRANSMISSION_BY_DIGEST),
                                                        ("api.tag", TRANSMISSION_BY_DIGEST)]
        assert docker.container("transmission").attrs["Config"]["Image"] == TRANSMISSION_BY_DIGEST
        assert f"{GHCR_TRANSMISSION}:{TRANSMISSION_TAG}" in docker.images_[TRANSMISSION_PIN].names
        assert not [subject for subject in _subjects(docker) if "lscr.io" in subject]

    def test_an_image_that_docker_pulled_from_ghcr_by_its_digest_takes_no_room_and_no_pull(
        self, api, pools, docker, soc
    ):
        # As on the dev device after `docker pull ghcr.io/linuxserver/transmission@sha256:...`: the
        # image has no tag, and the space check finds it by the pin's name and digest.
        image = docker.registry[TRANSMISSION_BY_DIGEST]
        image.names.add(TRANSMISSION_BY_DIGEST)
        docker.images_[image.ref] = image
        pin = catalog_module.APP_IMAGES["transmission"]
        soc.free = {DOCKER_ROOT: preflight_module.FREE_SPACE_FLOOR + pin.disk_bytes - 1}

        response = install(api, TRANSMISSION)

        assert response.status_code == 200, response.text
        assert docker.called("api.pull") == []
        assert docker.container("transmission").attrs["Config"]["Image"] == TRANSMISSION_BY_DIGEST

    def test_an_image_that_docker_lacks_counts_its_estimate(self, api, pools, docker, soc):
        pin = catalog_module.APP_IMAGES["transmission"]
        soc.free = {DOCKER_ROOT: preflight_module.FREE_SPACE_FLOOR + pin.disk_bytes - 1}

        response = install(api, TRANSMISSION)

        assert response.status_code == 409, response.text
        assert response.json()["detail"].startswith("Transmission's images need about")
        assert docker.called("api.pull") == []


@pytest.fixture
def device_transmission(api, pools, docker, ghcr) -> StackDocker:
    """Transmission as the dev device has it: created from lscr.io/linuxserver/transmission:latest
    before its catalog pinned it, so without the version label, running; and transmission's pinned
    image in Docker's store under its name and digest alone, as the diagnosis pulled it. The calls
    of its install are forgotten."""
    response = install(api, TRANSMISSION)
    assert response.status_code == 200, response.text
    live = docker.containers_["transmission"]
    live.attrs["Config"]["Image"] = DEVICE_IMAGE
    live.attrs["Image"] = docker.images_[DEVICE_IMAGE].id
    live.attrs["Config"]["Labels"].pop("unos.app.version")
    docker.images_[TRANSMISSION_PIN].names = {TRANSMISSION_BY_DIGEST}
    docker.calls.clear()
    return docker


def _transmission(api) -> dict:
    listed = api.get(INSTALLED)
    assert listed.status_code == 200, listed.text
    return next(app for app in listed.json() if app["template_id"] == "transmission")


def _mounts(docker: StackDocker, name: str) -> dict[str, str]:
    return {m["Target"]: m["Source"] for m in docker.containers_[name].attrs["HostConfig"]["Mounts"]}


class TestTheDevDevicesTransmission:
    def test_it_is_its_templates_app_and_offered_the_ghcr_pin(self, api, device_transmission, ghcr):
        listed = _transmission(api)

        assert (listed["name"], listed["status"], listed["version"], listed["update_to"], listed["previous"]) == (
            "transmission", "running", None, TRANSMISSION_TAG, None)
        # The listing reads Docker's containers alone.
        assert ghcr.requests == []

    def test_its_plan_reads_the_pin_from_ghcr_and_counts_nothing_for_the_image_that_docker_has(
        self, api, device_transmission, ghcr
    ):
        response = api.get(f"{INSTALLED}/transmission/update")

        assert response.status_code == 200, response.text
        plan = response.json()
        assert (plan["version_from"], plan["version_to"], plan["platform"]) == (None, TRANSMISSION_TAG, PLATFORM)
        assert plan["containers"] == [{
            "name": "transmission", "companion": None, "image_from": DEVICE_IMAGE, "image_to": TRANSMISSION_PIN,
            "changed": True, "built_from": "2026-10-27T05:12:30.123456Z", "built_to": "2026-11-17T19:16:46.128214Z",
        }]
        assert (plan["download_bytes"], plan["images_bytes"], plan["copies"]) == (0, 0, [])
        hosts = {urllib.parse.urlsplit(url).hostname for url in ghcr.requests}
        assert hosts == {"ghcr.io", "pkg-containers.githubusercontent.com"}
        assert device_transmission.called("api.pull", "container.stop", "api.create_container") == []

    def test_its_update_runs_the_ghcr_pin_and_its_rollback_the_image_it_ran(self, api, device_transmission, pools):
        docker = device_transmission
        old = docker.containers_["transmission"]
        mounts, env = _mounts(docker, "transmission"), list(old.attrs["Config"]["Env"])
        device_image_id = old.attrs["Image"]

        job = _job(api, api.post(f"{INSTALLED}/transmission/update"))

        assert (job["state"], job["error"]) == ("succeeded", None), job
        new, earlier = docker.containers_["transmission"], docker.containers_["transmission.previous"]
        assert (new.attrs["Config"]["Image"], new.attrs["Image"], new.status) == (
            TRANSMISSION_BY_DIGEST, CONFIG_DIGEST, "running")
        assert _mounts(docker, "transmission") == mounts
        assert {f"{name}={value}" for name, value in TRANSMISSION["env_overrides"].items()} <= set(
            new.attrs["Config"]["Env"])
        assert (earlier.attrs["Config"]["Image"], earlier.attrs["Image"], earlier.status) == (
            DEVICE_IMAGE, device_image_id, "exited")
        listed = _transmission(api)
        assert (listed["version"], listed["update_to"]) == (TRANSMISSION_TAG, None)
        assert listed["previous"]["images"] == [DEVICE_IMAGE]

        job = _job(api, api.post(f"{INSTALLED}/transmission/rollback"))

        assert (job["state"], job["error"]) == ("succeeded", None), job
        assert sorted(docker.containers_) == ["transmission"]
        back = docker.containers_["transmission"]
        assert back is old
        assert (back.attrs["Config"]["Image"], back.attrs["Image"], back.status) == (
            DEVICE_IMAGE, device_image_id, "running")
        assert back.attrs["Config"]["Env"] == env and _mounts(docker, "transmission") == mounts
        listed = _transmission(api)
        assert (listed["version"], listed["update_to"], listed["previous"]) == (None, TRANSMISSION_TAG, None)
        # The image was there: nothing was pulled, from lscr.io or anywhere, and nothing was removed.
        assert docker.called("api.pull", "images.remove") == []
        assert DEVICE_IMAGE in docker.images_ and TRANSMISSION_PIN in docker.images_

    def test_one_created_from_the_lscr_io_pin_runs_the_ghcr_pins_image_and_is_offered_nothing(
        self, api, device_transmission, ghcr
    ):
        # lscr.io's name@digest of the same index: the same image, which no update recreates under
        # GHCR's name (ADR-0426).
        live = device_transmission.containers_["transmission"]
        live.attrs["Config"]["Image"] = f"lscr.io/linuxserver/transmission@{TRANSMISSION_DIGEST}"
        live.attrs["Image"] = CONFIG_DIGEST

        assert (_transmission(api)["update_to"], _transmission(api)["previous"]) == (None, None)
        response = api.get(f"{INSTALLED}/transmission/update")
        assert (response.status_code, response.json()["detail"]) == (
            409, "Transmission runs the images that the catalog pins: there is nothing to update")
        # The job makes the plan's checks first and ends failed with its refusal.
        job = _job(api, api.post(f"{INSTALLED}/transmission/update"))
        assert (job["state"], job["error_status"], job["error"]) == (
            "failed", 409, "Transmission runs the images that the catalog pins: there is nothing to update")
        assert ghcr.requests == []
        assert device_transmission.called("api.pull", "container.stop", "container.rename", "api.create_container") == []

    def test_without_the_image_the_update_pulls_it_from_ghcr_by_its_digest(self, api, device_transmission):
        docker = device_transmission
        del docker.images_[TRANSMISSION_PIN]

        plan = api.get(f"{INSTALLED}/transmission/update")

        assert plan.status_code == 200, plan.text
        # No layer of the image that runs is the pin's: all of its arm64 layers are pulled, and its
        # whole estimate is counted.
        layers = sum(layer["size"] for layer in ARM64["layers"])
        assert (plan.json()["download_bytes"], plan.json()["images_bytes"]) == (
            layers, catalog_module.APP_IMAGES["transmission"].disk_bytes)

        job = _job(api, api.post(f"{INSTALLED}/transmission/update"))

        assert (job["state"], job["error"]) == ("succeeded", None), job
        assert docker.called("api.pull", "api.tag") == [("api.pull", TRANSMISSION_BY_DIGEST),
                                                        ("api.tag", TRANSMISSION_BY_DIGEST)]
        assert docker.containers_["transmission"].attrs["Config"]["Image"] == TRANSMISSION_BY_DIGEST
        assert not [subject for subject in _subjects(docker) if subject.startswith("lscr.io")]
