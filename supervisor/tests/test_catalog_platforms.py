"""The platforms of the catalog's images and what the host's platform makes of them (ADR-0403).

Every image of the catalog has its platforms recorded from the registries (image_platforms.json,
filled by scripts/catalog-platforms.py); the catalog API marks an app that lacks the host's
platform as not available, and an install of it answers 409 before any Docker request.
"""

from __future__ import annotations

import importlib
import json

import pytest
from fastapi import HTTPException

from src.apps import platforms as platforms_module
from src.apps.catalog import APP_CATALOG, get_app_by_id
from src.apps.schemas import AppCategory, AppTemplate, Companion, PortMapping

from . import test_apps
from .test_apps import (  # noqa: F401
    INSTALL,
    PHOTOS_ML_NPU,
    TRANSMISSION,
    _photos,
    _rk3588,
    docker_client,
    in_catalog,
    install,
    soc,
)

apps_router = importlib.import_module("src.apps.router")
CATALOG = "/api/v1/apps/catalog"

api = test_apps.api
pools = test_apps.pools
shares = test_apps.shares
chown_calls = test_apps.chown_calls


def _images(spec) -> list[str]:
    return [spec.image, *(profile.image for profile in spec.hardware if profile.image)]


def _every_image() -> list[str]:
    return list(dict.fromkeys(
        image for template in APP_CATALOG for spec in (template, *template.companions) for image in _images(spec)
    ))


@pytest.fixture
def host(monkeypatch):
    """``host("linux/riscv64")`` makes that the platform of the host the supervisor runs on."""

    def set_platform(platform: str | None) -> None:
        monkeypatch.setattr(platforms_module, "host_platform", lambda: platform)

    return set_platform


# The images whose registry lists no Linux platform at all, which no host can pull. The set may only
# shrink: it held Readarr's tag, an empty index, until Readarr left the catalog (ADR-0410).
PUBLISH_NOTHING: set[str] = set()


class TestRecordedPlatforms:
    def test_every_image_of_the_catalog_has_its_platforms_recorded(self):
        recorded = platforms_module.recorded()

        missing = [image for image in _every_image() if image not in recorded]
        empty = {image for image in _every_image() if image in recorded and not recorded[image]}

        assert missing == []
        assert empty == PUBLISH_NOTHING

    def test_every_template_lists_the_platforms_of_its_image(self):
        without = {t.id for t in APP_CATALOG if not t.platforms}

        assert without == set()
        assert all(p.startswith("linux/") for t in APP_CATALOG for p in t.platforms)

    def test_every_companion_lists_the_platforms_of_its_image(self):
        without = [(t.id, c.name) for t in APP_CATALOG for c in t.companions if not c.platforms]

        assert without == []

    def test_the_record_holds_no_image_that_the_catalog_has_left(self):
        assert sorted(set(platforms_module.recorded()) - set(_every_image())) == []

    def test_the_record_is_sorted_and_names_each_platform_once(self):
        for image, published in platforms_module.recorded().items():
            assert published == sorted(set(published)), image

    def test_the_catalog_api_lists_the_platforms_of_each_app(self, api, host):
        host("linux/arm64")

        listed = {t["id"]: t for t in api.get(CATALOG).json()}

        assert listed["navidrome"]["platforms"] == get_app_by_id("navidrome").platforms
        assert "linux/riscv64" in listed["navidrome"]["platforms"]
        assert "linux/riscv64" not in listed["plex"]["platforms"]

    def test_the_recorded_images_of_the_files_are_those_of_the_json(self):
        path = platforms_module.RECORDED
        assert json.loads(path.read_text())["images"] == platforms_module.recorded()


class TestHostPlatform:
    @pytest.mark.parametrize(
        ("machine", "platform"),
        [
            ("aarch64", "linux/arm64"),
            ("arm64", "linux/arm64"),
            ("x86_64", "linux/amd64"),
            ("AMD64", "linux/amd64"),
            ("riscv64", "linux/riscv64"),
            ("armv7l", "linux/arm/v7"),
            ("armv6l", "linux/arm/v6"),
            ("i686", "linux/386"),
            ("ppc64le", "linux/ppc64le"),
            ("s390x", "linux/s390x"),
        ],
    )
    def test_the_machine_names_the_platform(self, machine, platform):
        assert platforms_module.platform_of_machine(machine) == platform

    @pytest.mark.parametrize("machine", ["", "sparc64", "loongarch64"])
    def test_a_machine_it_does_not_know_names_no_platform(self, machine):
        assert platforms_module.platform_of_machine(machine) is None

    def test_the_host_platform_is_read_from_the_machine(self, monkeypatch):
        monkeypatch.setattr(platforms_module.platform, "machine", lambda: "aarch64")

        assert platforms_module.host_platform() == "linux/arm64"


class TestRunsOn:
    @pytest.mark.parametrize(
        ("published", "host", "runs"),
        [
            (["linux/amd64", "linux/arm64"], "linux/arm64", True),
            (["linux/amd64", "linux/arm64"], "linux/riscv64", False),
            (["linux/amd64", "linux/arm64"], "linux/arm/v7", False),
            (["linux/arm/v7"], "linux/arm/v7", True),
            # Docker runs an armv6 image on an armv7 host, but not the other way round.
            (["linux/arm/v6"], "linux/arm/v7", True),
            (["linux/arm/v7"], "linux/arm/v6", False),
            # A 32-bit arm variant is no arm64.
            (["linux/arm/v7"], "linux/arm64", False),
            ([], "linux/arm64", False),
        ],
    )
    def test_an_image_runs_on_a_platform_it_publishes(self, published, host, runs):
        assert platforms_module.runs_on(published, host) is runs


class TestWhatTheHostCannotRun:
    def test_an_image_nothing_is_recorded_for_is_left_to_docker(self):
        template = AppTemplate(
            id="x", name="X", description="x", icon="x", category=AppCategory.MEDIA, image="example/x:1", version="1"
        )

        assert platforms_module.lacking(template, "linux/riscv64") == []

    def test_every_container_of_an_app_must_have_the_platform(self, monkeypatch):
        monkeypatch.setattr(platforms_module, "recorded", lambda: {
            "example/server:1": ["linux/amd64", "linux/arm64", "linux/riscv64"],
            "example/db:1": ["linux/amd64", "linux/arm64"],
        })
        template = AppTemplate(
            id="x", name="X", description="x", icon="x", category=AppCategory.MEDIA, image="example/server:1",
            version="1", companions=[Companion(name="db", image="example/db:1")],
        )

        assert platforms_module.lacking(template, "linux/arm64") == []
        assert platforms_module.lacking(template, "linux/riscv64") == ["example/db:1"]

    def test_an_image_recorded_with_no_platform_runs_on_no_host(self, monkeypatch):
        monkeypatch.setattr(platforms_module, "recorded", lambda: {"example/x:1": []})
        template = AppTemplate(
            id="x", name="X", description="x", icon="x", category=AppCategory.MEDIA, image="example/x:1", version="1"
        )

        assert platforms_module.lacking(template, "linux/arm64") == ["example/x:1"]

    def test_the_apps_that_lack_riscv64_are_all_but_those_that_publish_it(self):
        lacking = [t.id for t in APP_CATALOG if platforms_module.lacking(t, "linux/riscv64")]
        publish = sorted(t.id for t in APP_CATALOG if t.id not in lacking)

        # docs/research/riscv64-support-2026-11-07.md, area 6, counted by hand: these images
        # publish a linux/riscv64 build.
        assert publish == ["gitea", "navidrome", "pihole", "prometheus", "traefik", "zigbee2mqtt"]


class TestCatalogApi:
    def test_on_a_host_whose_platform_an_image_lacks_the_app_is_not_available(self, api, host):
        host("linux/riscv64")

        listed = {t["id"]: t for t in api.get(CATALOG).json()}

        assert listed["plex"]["available"] is False
        assert listed["plex"]["host_platform"] == "linux/riscv64"
        assert listed["navidrome"]["available"] is True
        # The counts match the recorded data.
        offered = {t.id for t in APP_CATALOG if "linux/riscv64" in t.platforms}
        assert {i for i, t in listed.items() if t["available"]} == offered
        assert len(listed) - len(offered) == sum(1 for t in listed.values() if not t["available"])

    def test_the_single_app_answer_says_the_same(self, api, host):
        host("linux/riscv64")

        assert api.get(f"{CATALOG}/plex").json()["available"] is False
        assert api.get(f"{CATALOG}/traefik").json()["available"] is True

    def test_on_arm64_the_apps_that_publish_it_are_available(self, api, host):
        host("linux/arm64")

        listed = api.get(CATALOG).json()

        assert {t["host_platform"] for t in listed} == {"linux/arm64"}
        assert {t["id"] for t in listed if not t["available"]} == {
            t.id for t in APP_CATALOG if platforms_module.lacking(t, "linux/arm64")
        }

    def test_on_a_host_of_a_platform_it_cannot_name_every_app_is_offered(self, api, host):
        host(None)

        listed = api.get(CATALOG).json()

        assert all(t["available"] for t in listed)
        assert {t["host_platform"] for t in listed} == {None}

    def test_an_app_with_a_companion_lacking_the_platform_is_not_available(self, api, host, in_catalog, monkeypatch):  # noqa: F811
        monkeypatch.setattr(platforms_module, "recorded", lambda: {
            "example/server:1": ["linux/amd64", "linux/arm64", "linux/riscv64"],
            "example/db:1": ["linux/amd64", "linux/arm64"],
        })
        in_catalog(AppTemplate(
            id="stack", name="Stack", description="x", icon="x", category=AppCategory.MEDIA,
            image="example/server:1", version="1", companions=[Companion(name="db", image="example/db:1")],
        ))
        host("linux/riscv64")

        assert api.get(f"{CATALOG}/stack").json()["available"] is False


class TestInstallRefusal:
    def test_an_app_without_an_image_for_the_host_answers_409_before_any_docker_request(
        self, api, host, docker_client, pools  # noqa: F811
    ):
        host("linux/riscv64")

        response = api.post(INSTALL, json={"template_id": "plex"})

        assert response.status_code == 409
        detail = response.json()["detail"]
        assert "Plex" in detail and "linux/riscv64" in detail and get_app_by_id("plex").image in detail
        assert docker_client.mock_calls == []

    def test_a_companion_without_the_platform_refuses_the_whole_app(
        self, api, host, docker_client, pools, in_catalog, monkeypatch  # noqa: F811
    ):
        monkeypatch.setattr(platforms_module, "recorded", lambda: {
            "example/server:1": ["linux/amd64", "linux/arm64", "linux/riscv64"],
            "example/db:1": ["linux/amd64", "linux/arm64"],
        })
        in_catalog(AppTemplate(
            id="stack", name="Stack", description="x", icon="x", category=AppCategory.MEDIA,
            image="example/server:1", version="1", ports=[PortMapping(host=8081, container=80)],
            companions=[Companion(name="db", image="example/db:1")],
        ))
        host("linux/riscv64")

        response = api.post(INSTALL, json={"template_id": "stack"})

        assert response.status_code == 409
        assert "example/db:1" in response.json()["detail"]
        assert docker_client.mock_calls == []

    def test_the_refusal_starts_no_job_and_leaves_the_slot_free(self, api, host, docker_client, pools):  # noqa: F811
        host("linux/riscv64")

        api.post(INSTALL, json={"template_id": "plex"})

        assert api.get(f"{INSTALL}/jobs").json() == []

    def test_an_app_that_has_the_platform_is_not_refused(self, api, host, docker_client, pools):  # noqa: F811
        host("linux/arm64")

        response = api.post(INSTALL, json=TRANSMISSION)

        assert response.status_code == 202

    def test_a_host_of_an_unknown_platform_refuses_nothing(self, api, host, docker_client, pools):  # noqa: F811
        host(None)

        response = api.post(INSTALL, json=TRANSMISSION)

        assert response.status_code == 202

    def test_an_unknown_app_is_still_404_and_a_bad_request_400_on_a_host_that_has_the_image(
        self, api, host, docker_client, pools  # noqa: F811
    ):
        host("linux/arm64")

        assert api.post(INSTALL, json={"template_id": "nope"}).status_code == 404

    def test_the_check_refuses_only_for_a_host_that_has_a_name_and_a_recorded_image_that_lacks_it(self):
        with pytest.raises(HTTPException) as refused:
            apps_router._check_platform(get_app_by_id("plex"), "linux/riscv64")
        assert refused.value.status_code == 409
        apps_router._check_platform(get_app_by_id("plex"), "linux/arm64")
        apps_router._check_platform(get_app_by_id("plex"), None)

    def test_an_image_that_the_hardware_chose_is_checked_before_its_pull(
        self, api, host, docker_client, pools, in_catalog, soc, monkeypatch  # noqa: F811
    ):
        # The request checks the containers' own images, which all have riscv64; the job then reads
        # the hardware, whose NPU image has not, and refuses before the first pull.
        _rk3588(soc)
        monkeypatch.setattr(platforms_module, "recorded", lambda: {
            "example/photos-server:1": ["linux/arm64", "linux/riscv64"],
            "example/postgres:14": ["linux/arm64", "linux/riscv64"],
            "example/photos-ml:1": ["linux/arm64", "linux/riscv64"],
            PHOTOS_ML_NPU: ["linux/arm64"],
        })
        in_catalog(_photos())
        host("linux/riscv64")

        ended = install(api, {"template_id": "photos"})

        assert ended.status_code == 409
        assert PHOTOS_ML_NPU in ended.json()["detail"]
        docker_client.api.post.assert_not_called()
        docker_client.containers.create.assert_not_called()
