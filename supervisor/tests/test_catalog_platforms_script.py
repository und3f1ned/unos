"""scripts/catalog-platforms.py, the audit's tool (ADR-0403, ADR-0410, ADR-0218), against recorded
registry answers: the manifests of five images as Docker Hub and GHCR served them
(fixtures/registry_manifests.json), behind the anonymous token handshake that both registries ask
for. No test reaches the network."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("catalog_platforms", ROOT / "scripts" / "catalog-platforms.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)

MANIFESTS = json.loads((Path(__file__).parent / "fixtures" / "registry_manifests.json").read_text())
TRAEFIK = "traefik:v3.0"
SONARR = "lscr.io/linuxserver/sonarr:latest"
READARR = "lscr.io/linuxserver/readarr:develop"
IMMICH = "ghcr.io/immich-app/immich-server:v3.4.1@sha256:7c489add298fc2f92cda4c777de6462aa206877e04087b815f62c7664af0b53b"
VALKEY = "docker.io/valkey/valkey:9@sha256:418652cfb58ef879d4978c33553735d7147016032d5aefaa14c828e611eb9dfd"


class Registries:
    """A transport that answers as the registries did: 401 with a Bearer challenge for a request
    without a token, a token for the realm, and the recorded manifests for one with it."""

    def __init__(self, answers: dict | None = None, digests: bool = True) -> None:
        self.answers = MANIFESTS if answers is None else answers
        self.requests: list[tuple[str, str, dict]] = []
        # Whether a HEAD of a manifest names its digest (Docker-Content-Digest), as both registries do.
        self.digests = digests

    def __call__(self, method, url, headers):
        self.requests.append((method, url, headers))
        if url.startswith(("https://auth.docker.io/token", "https://ghcr.io/token")):
            return 200, {}, json.dumps({"token": "anonymous"}).encode()
        if "Authorization" not in headers:
            realm = "https://ghcr.io/token" if "ghcr.io" in url else "https://auth.docker.io/token"
            challenge = f'Bearer realm="{realm}",service="x"'
            return 401, {"www-authenticate": challenge}, b'{"errors":[]}'
        if url in self.answers:
            status, answer, body = self.answers[url]
            if method == "HEAD":
                named = {"docker-content-digest": digest_of(body)} if status == 200 and self.digests else {}
                return status, {**answer, **named}, b""
            return status, answer, body.encode()
        return 404, {}, b"" if method == "HEAD" else b'{"errors":[{"code":"MANIFEST_UNKNOWN"}]}'


def digest_of(body: str) -> str:
    return "sha256:" + hashlib.sha256(body.encode()).hexdigest()


SONARR_TAG = "https://ghcr.io/v2/linuxserver/sonarr/manifests/latest"
# The index that the tag names in the recorded answers.
SONARR_NOW = digest_of(MANIFESTS[SONARR_TAG][2])
SONARR_PINNED = f"{SONARR}@{SONARR_NOW}"
EARLIER = "sha256:" + "e" * 64


class TestReferences:
    @pytest.mark.parametrize(
        ("ref", "parsed"),
        [
            ("traefik:v3.0", ("registry-1.docker.io", "library/traefik", "v3.0")),
            ("jellyfin/jellyfin:latest", ("registry-1.docker.io", "jellyfin/jellyfin", "latest")),
            ("lscr.io/linuxserver/sonarr:latest", ("ghcr.io", "linuxserver/sonarr", "latest")),
            ("ghcr.io/advplyr/audiobookshelf:latest", ("ghcr.io", "advplyr/audiobookshelf", "latest")),
            ("docker.io/valkey/valkey:9@sha256:" + "a" * 64, ("registry-1.docker.io", "valkey/valkey", "sha256:" + "a" * 64)),
        ],
    )
    def test_the_registry_the_repository_and_what_is_asked_for(self, ref, parsed):
        assert tool.parse_reference(ref) == parsed

    def test_a_pinned_image_is_asked_for_by_its_digest_not_its_tag(self):
        registries = Registries()

        tool.Registry(registries).platforms(VALKEY)

        urls = [url for _, url, _ in registries.requests if "/manifests/" in url]
        assert urls and all(url.endswith("sha256:418652cfb58ef879d4978c33553735d7147016032d5aefaa14c828e611eb9dfd") for url in urls)


class TestPlatforms:
    def test_a_manifest_list_gives_its_linux_platforms_with_variants(self):
        assert tool.Registry(Registries()).platforms(TRAEFIK) == [
            "linux/amd64", "linux/arm/v6", "linux/arm64", "linux/ppc64le", "linux/riscv64", "linux/s390x",
        ]

    def test_arm64s_variant_is_not_named_and_attestations_are_left_out(self):
        registry = tool.Registry(Registries())

        assert registry.platforms(SONARR) == ["linux/amd64", "linux/arm64"]
        assert registry.platforms(IMMICH) == ["linux/amd64", "linux/arm64"]

    def test_an_empty_index_publishes_no_platform(self):
        assert tool.Registry(Registries()).platforms(READARR) == []

    def test_a_single_manifest_gets_its_platform_from_its_config(self):
        manifest = {"mediaType": "application/vnd.oci.image.manifest.v1+json", "config": {"digest": "sha256:cc"}}
        config = {"os": "linux", "architecture": "riscv64"}
        answers = {
            "https://registry-1.docker.io/v2/example/one/manifests/1": [
                200, {"content-type": manifest["mediaType"]}, json.dumps(manifest)],
            "https://registry-1.docker.io/v2/example/one/blobs/sha256:cc": [200, {}, json.dumps(config)],
        }

        assert tool.Registry(Registries(answers)).platforms("example/one:1") == ["linux/riscv64"]

    def test_the_token_is_asked_once_per_repository_and_no_request_writes(self):
        registries = Registries()
        registry = tool.Registry(registries)

        registry.platforms(TRAEFIK)
        registry.platforms("traefik:v3.0")

        tokens = [url for _, url, _ in registries.requests if "/token" in url]
        assert len(tokens) == 1
        assert {method for method, _, _ in registries.requests} == {"GET"}

    def test_an_image_the_registry_does_not_know_is_an_error(self):
        with pytest.raises(tool.QueryError, match="HTTP 404"):
            tool.Registry(Registries()).platforms("example/none:1")

    def test_a_registry_that_asks_for_credentials_is_an_error(self):
        def refuses(method, url, headers):
            return 401, {"www-authenticate": 'Basic realm="x"'}, b""

        with pytest.raises(tool.QueryError, match="credentials"):
            tool.Registry(refuses).platforms(TRAEFIK)

    def test_an_answer_that_is_no_manifest_is_an_error(self):
        answers = {"https://registry-1.docker.io/v2/example/odd/manifests/1": [200, {"content-type": "text/plain"}, "[]"]}

        with pytest.raises(tool.QueryError):
            tool.Registry(Registries(answers)).platforms("example/odd:1")


class TestNormalize:
    @pytest.mark.parametrize(
        ("entry", "platform"),
        [
            ({"os": "linux", "architecture": "arm64", "variant": "v8"}, "linux/arm64"),
            ({"os": "linux", "architecture": "amd64", "variant": "v3"}, "linux/amd64"),
            ({"os": "linux", "architecture": "arm", "variant": "v7"}, "linux/arm/v7"),
            ({"os": "linux", "architecture": "riscv64"}, "linux/riscv64"),
            ({"os": "windows", "architecture": "amd64"}, None),
            ({"os": "unknown", "architecture": "unknown"}, None),
            ({}, None),
        ],
    )
    def test_a_platform_entry(self, entry, platform):
        assert tool.normalize(entry) == platform


class TestOnlyHttps:
    def test_a_token_realm_that_is_not_https_is_refused(self):
        def asks(method, url, headers):
            return 401, {"www-authenticate": 'Bearer realm="http://127.0.0.1/token",service="x"'}, b""

        with pytest.raises(tool.QueryError, match="credentials"):
            tool.Registry(asks).platforms(TRAEFIK)

    def test_a_redirect_to_another_scheme_is_not_followed(self):
        def redirects(method, url, headers):
            if "/token" in url:
                return 200, {}, b'{"token":"t"}'
            if "Authorization" not in headers:
                return 401, {"www-authenticate": 'Bearer realm="https://auth.docker.io/token"'}, b""
            return 307, {"location": "file:///etc/passwd"}, b""

        with pytest.raises(tool.QueryError, match="HTTP 307"):
            tool.Registry(redirects).platforms(TRAEFIK)

    def test_curl_is_told_to_speak_https_only(self, monkeypatch):
        seen = []

        def run(command, **kwargs):
            seen.append(command)
            raise OSError("stop")

        monkeypatch.setattr(tool.subprocess, "run", run)
        with pytest.raises(tool.QueryError):
            tool.curl_transport("GET", "https://ghcr.io/v2/", {})

        assert seen[0][seen[0].index("--proto") + 1] == "=https"
        assert seen[0][seen[0].index("--proto-redir") + 1] == "=https"

    def test_a_head_request_is_curls_head_which_reads_no_body(self, monkeypatch):
        seen = []

        def run(command, **kwargs):
            seen.append(command)
            raise OSError("stop")

        monkeypatch.setattr(tool.subprocess, "run", run)
        with pytest.raises(tool.QueryError):
            tool.curl_transport("HEAD", "https://ghcr.io/v2/", {})

        assert "--head" in seen[0] and "-X" not in seen[0]


class TestCurlOutput:
    def test_the_status_the_headers_and_the_body(self):
        raw = b"HTTP/2 401 \r\nWWW-Authenticate: Bearer realm=\"r\"\r\ncontent-type: application/json\r\n\r\n{}"

        assert tool.parse_response(raw) == (401, {"www-authenticate": 'Bearer realm="r"', "content-type": "application/json"}, b"{}")

    def test_an_interim_answer_is_skipped(self):
        assert tool.parse_response(b"HTTP/1.1 100 Continue\r\n\r\nHTTP/1.1 200 OK\r\nA: b\r\n\r\nx") == (200, {"a": "b"}, b"x")

    def test_output_that_is_no_http_answer_is_an_error(self):
        with pytest.raises(tool.QueryError):
            tool.parse_response(b"garbage")


class TestCommands:
    REFS = [TRAEFIK, SONARR, READARR]

    def _recorded(self, tmp_path, images) -> Path:
        path = tmp_path / "image_platforms.json"
        path.write_text(tool.render(images))
        return path

    def _now(self):
        return {
            TRAEFIK: ["linux/amd64", "linux/arm/v6", "linux/arm64", "linux/ppc64le", "linux/riscv64", "linux/s390x"],
            SONARR: ["linux/amd64", "linux/arm64"],
            READARR: [],
        }

    def test_check_passes_when_the_record_is_the_registries(self, tmp_path, capsys):
        path = self._recorded(tmp_path, self._now())

        assert tool.main(["check"], Registries(), path, self.REFS) == 0
        assert capsys.readouterr().err == ""

    def test_check_fails_and_names_an_image_whose_platforms_differ(self, tmp_path, capsys):
        recorded = self._now()
        recorded[TRAEFIK] = [p for p in recorded[TRAEFIK] if p != "linux/riscv64"]
        path = self._recorded(tmp_path, recorded)

        assert tool.main(["check"], Registries(), path, self.REFS) == 1
        assert f"DIFFERS {TRAEFIK}: recorded" in capsys.readouterr().err

    def test_check_fails_for_an_image_that_is_not_recorded_and_for_one_nothing_uses(self, tmp_path, capsys):
        recorded = self._now()
        del recorded[SONARR]
        recorded["example/gone:1"] = ["linux/amd64"]
        path = self._recorded(tmp_path, recorded)

        assert tool.main(["check"], Registries(), path, self.REFS) == 1
        err = capsys.readouterr().err
        assert f"{SONARR}: not recorded" in err and "example/gone:1: recorded, but no template" in err

    def test_check_fails_for_an_image_it_could_not_read_instead_of_passing(self, tmp_path, capsys):
        path = self._recorded(tmp_path, {**self._now(), "example/none:1": ["linux/amd64"]})

        assert tool.main(["check"], Registries(), path, [*self.REFS, "example/none:1"]) == 1
        assert "ERROR example/none:1" in capsys.readouterr().err

    def test_record_writes_the_registries_platforms_sorted(self, tmp_path):
        path = tmp_path / "image_platforms.json"

        assert tool.main(["record"], Registries(), path, self.REFS) == 0
        assert json.loads(path.read_text()) == {"images": dict(sorted(self._now().items()))}
        assert path.read_text().endswith("\n")

    def test_record_writes_nothing_when_one_image_cannot_be_read(self, tmp_path):
        path = tmp_path / "image_platforms.json"
        path.write_text("keep")

        assert tool.main(["record"], Registries(), path, [*self.REFS, "example/none:1"]) == 1
        assert path.read_text() == "keep"


class TestTheRecordedFile:
    def test_the_file_is_what_record_writes(self):
        path = ROOT / "supervisor" / "src" / "apps" / "image_platforms.json"
        images = json.loads(path.read_text())["images"]

        assert path.read_text() == tool.render(images)

    def test_the_catalog_images_are_the_script_s_list(self):
        from src.apps.catalog import APP_CATALOG

        listed = tool.catalog_images()

        assert listed == list(dict.fromkeys(listed))
        assert {t.image for t in APP_CATALOG} <= set(listed)
        assert set(listed) == set(json.loads(tool.RECORDED.read_text())["images"])


class TestMovedTags:
    """The weekly audit of the pinned tags (ADR-0410): a tag that names another index than the one
    pinned is reported with the templates that use it."""

    def test_a_tag_that_names_its_pinned_index_passes(self, capsys):
        assert tool.main(["tags"], Registries(), users={SONARR_PINNED: ["sonarr"]}) == 0
        out = capsys.readouterr()
        assert out.err == "" and "1 pinned image" in out.out

    def test_a_moved_tag_fails_and_names_its_templates_and_both_digests(self, capsys):
        pinned = f"{SONARR}@{EARLIER}"

        assert tool.main(["tags"], Registries(), users={pinned: ["sonarr", "other"]}) == 1
        err = capsys.readouterr().err
        assert f"MOVED sonarr, other: {SONARR} names {SONARR_NOW}, not the pinned {EARLIER}" in err
        # The pinned index is gone from the registry: an install of it fails.
        assert "the pinned index is gone" in err

    def test_a_moved_tag_whose_pinned_index_is_still_served_says_so(self, capsys):
        pinned = f"{SONARR}@{EARLIER}"
        answers = {**MANIFESTS, f"https://ghcr.io/v2/linuxserver/sonarr/manifests/{EARLIER}": [200, {}, "{}"]}

        assert tool.main(["tags"], Registries(answers), users={pinned: ["sonarr"]}) == 1
        err = capsys.readouterr().err
        assert f"MOVED sonarr: {SONARR} names {SONARR_NOW}" in err and "gone" not in err

    def test_a_pinned_index_the_registry_cannot_answer_for_is_an_error_not_gone(self, capsys):
        # Only a 404 says that the index is gone; a refusal or a server error says nothing of it.
        pinned = f"{SONARR}@{EARLIER}"
        answers = {**MANIFESTS, f"https://ghcr.io/v2/linuxserver/sonarr/manifests/{EARLIER}": [503, {}, ""]}

        assert tool.main(["tags"], Registries(answers), users={pinned: ["sonarr"]}) == 1
        err = capsys.readouterr().err
        assert "ERROR" in err and "HTTP 503" in err and "gone" not in err and "MOVED" not in err

    def test_a_digest_without_a_tag_is_an_error_not_a_check_of_latest(self, capsys):
        ref = f"lscr.io/linuxserver/sonarr@{SONARR_NOW}"

        assert tool.main(["tags"], Registries(), users={ref: ["sonarr"]}) == 1
        assert f"ERROR {ref}: not pinned by tag and digest" in capsys.readouterr().err

    def test_an_image_that_floats_on_a_tag_is_an_error(self, capsys):
        assert tool.main(["tags"], Registries(), users={SONARR: ["sonarr"]}) == 1
        assert f"ERROR {SONARR}: not pinned by tag and digest" in capsys.readouterr().err

    def test_a_tag_the_registry_does_not_know_is_an_error_not_a_pass(self, capsys):
        assert tool.main(["tags"], Registries(), users={f"example/none:1@{EARLIER}": ["none"]}) == 1
        err = capsys.readouterr().err
        assert "ERROR example/none:1" in err and "HTTP 404" in err

    def test_the_tags_are_asked_for_with_head_only_and_no_request_writes(self):
        registries = Registries()

        tool.main(["tags"], registries, users={SONARR_PINNED: ["sonarr"], VALKEY: ["immich"]})

        manifests = [(method, url) for method, url, _ in registries.requests if "/manifests/" in url]
        # A HEAD is no pull: Docker Hub does not count it against its anonymous limit.
        assert manifests and {method for method, _ in manifests} == {"HEAD"}
        assert {method for method, _, _ in registries.requests} <= {"GET", "HEAD"}
        assert ("HEAD", SONARR_TAG) in manifests
        assert ("HEAD", "https://registry-1.docker.io/v2/valkey/valkey/manifests/9") in manifests

    def test_a_registry_that_names_no_digest_is_read_with_a_get_and_hashed(self, capsys):
        registries = Registries(digests=False)

        assert tool.main(["tags"], registries, users={SONARR_PINNED: ["sonarr"]}) == 0
        assert ("GET", SONARR_TAG) in [(method, url) for method, url, _ in registries.requests]

    def test_the_catalog_names_the_templates_of_each_pinned_image(self):
        from src.apps.catalog import APP_IMAGES, VALKEY as CATALOG_VALKEY, get_app_by_id

        users = tool.catalog_users()

        assert users[CATALOG_VALKEY.ref] == ["immich"]
        assert users[get_app_by_id("transmission").image] == ["transmission"]
        assert set(users) == set(tool.catalog_images())
        assert all("@sha256:" in ref for ref in users)
        assert len(APP_IMAGES) + 5 == len(users)
