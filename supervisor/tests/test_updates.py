"""System > Updates: the version the supervisor image carries, and the latest release (ADR-0233).

The image is built with UNOS_VERSION, `git describe --tags --always` of the checkout that
deploy/install.sh or deploy/update.sh built (supervisor/tests/test_deploy_scripts.py checks
that they pass it). The supervisor reads it and runs nothing: the container has neither git nor
the checkout. GitHub is never asked here: the release check is replaced.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import subprocess
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.auth.utils import verify_token
from src.main import app
from src.updates import updater as updater_module
from src.updates.schemas import ReleaseInfo

updates_router = importlib.import_module("src.updates.router")

API = "/api/v1/updates"


def _forbidden(*args, **kwargs):
    raise AssertionError(f"the version must not come from a command: {args!r}")


@pytest.fixture
def no_commands(monkeypatch):
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, _forbidden)
    monkeypatch.setattr(os, "system", _forbidden)


@pytest.fixture
def client(no_commands):
    app.dependency_overrides[verify_token] = lambda: SimpleNamespace(
        id=1, username="admin", is_admin=True, is_active=True
    )
    updates_router.limiter.reset()
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(verify_token, None)
        updates_router.limiter.reset()


def _release(tag: str, *, prerelease: bool = False) -> ReleaseInfo:
    return ReleaseInfo(
        tag_name=tag,
        name=f"UNOS {tag}",
        body="Fixes and features.",
        published_at=datetime(2026, 12, 1, 10, 0, tzinfo=timezone.utc),
        html_url=f"https://github.com/und3f1ned/unos/releases/tag/{tag}",
        prerelease=prerelease,
    )


@pytest.fixture
def releases(monkeypatch):
    """Replace GitHub: `latest` is what the release check finds (None: no release published)."""
    state = SimpleNamespace(latest=None, asked=[], error=None)

    async def fetch_latest_release(self, repo="und3f1ned/unos", include_prereleases=False):
        state.asked.append(include_prereleases)
        if state.error:
            raise updater_module.UpdateError(state.error)
        return state.latest

    monkeypatch.setattr(updater_module.Updater, "fetch_latest_release", fetch_latest_release)
    return state


@pytest.mark.parametrize(
    "built_with,version,commit,development",
    [
        ("v1.2.0", "v1.2.0", "unknown", False),
        ("v1.2.0-3-g712fc3a", "v1.2.0-3-g712fc3a", "712fc3a", True),
        ("v1.2.0-rc.1", "v1.2.0-rc.1", "unknown", False),
        ("712fc3a", "712fc3a", "712fc3a", True),
        ("712fc3a1b2c3d4e5f60718293a4b5c6d7e8f9012", "712fc3a1b2c3d4e5f60718293a4b5c6d7e8f9012",
         "712fc3a1b2c3", True),
        ("unknown", "unknown", "unknown", False),
        ("", "unknown", "unknown", False),
        (None, "unknown", "unknown", False),
    ],
)
def test_the_current_version_is_the_one_the_image_was_built_with(
    client, monkeypatch, built_with, version, commit, development
):
    if built_with is None:
        monkeypatch.delenv("UNOS_VERSION", raising=False)
    else:
        monkeypatch.setenv("UNOS_VERSION", built_with)

    response = client.get(f"{API}/current")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "version": version,
        "commit_hash": commit,
        "install_date": None,
        "is_development": development,
    }


def test_the_version_needs_no_checkout(no_commands, monkeypatch, tmp_path):
    # The install directory the updater knows does not exist in the container.
    monkeypatch.setattr(updater_module, "INSTALL_DIR", tmp_path / "no-checkout")
    monkeypatch.setenv("UNOS_VERSION", "v1.2.0-3-g712fc3a")

    current = asyncio.run(updater_module.Updater().get_current_version())

    assert current.version == "v1.2.0-3-g712fc3a"


class TestReleaseCheck:
    def test_no_published_release_is_not_the_latest_version(self, client, releases, monkeypatch):
        monkeypatch.setenv("UNOS_VERSION", "712fc3a")

        response = client.get(f"{API}/check")

        assert response.status_code == 200, response.text
        answer = response.json()
        assert answer["current_version"] == "712fc3a"
        assert answer["latest_version"] is None
        assert answer["release_info"] is None
        assert answer["update_available"] is False

    def test_a_newer_release_is_an_update(self, client, releases, monkeypatch):
        monkeypatch.setenv("UNOS_VERSION", "v1.2.0-3-g712fc3a")
        releases.latest = _release("v1.3.0")

        answer = client.get(f"{API}/check").json()

        assert answer["latest_version"] == "v1.3.0"
        assert answer["update_available"] is True
        assert answer["release_info"]["html_url"] == "https://github.com/und3f1ned/unos/releases/tag/v1.3.0"

    def test_the_installed_release_is_no_update(self, client, releases, monkeypatch):
        monkeypatch.setenv("UNOS_VERSION", "v1.3.0")
        releases.latest = _release("v1.3.0")

        answer = client.get(f"{API}/check").json()

        assert answer["latest_version"] == "v1.3.0"
        assert answer["update_available"] is False

    @pytest.mark.parametrize(
        "installed,latest,available",
        [
            # Commits after the latest release are no older than it.
            ("v1.3.0-2-g712fc3a", "v1.3.0", False),
            ("v1.3.0-2-g712fc3a", "v1.3.1", True),
            ("v1.3.0-rc.1-4-g712fc3a", "v1.3.0", True),
            ("v1.3.0-rc.1", "v1.3.0", True),
            ("v1.4.0", "v1.3.0", False),
        ],
    )
    def test_a_build_after_a_tag_compares_as_that_tag(self, client, releases, monkeypatch, installed, latest,
                                                      available):
        monkeypatch.setenv("UNOS_VERSION", installed)
        releases.latest = _release(latest)

        assert client.get(f"{API}/check").json()["update_available"] is available

    def test_pre_releases_only_on_request(self, client, releases):
        client.get(f"{API}/check")
        client.get(f"{API}/check?include_prereleases=true")

        assert releases.asked == [False, True]

    def test_a_check_that_cannot_reach_github_names_the_error(self, client, releases):
        releases.error = "Failed to fetch release info: [Errno -3] Temporary failure in name resolution"

        response = client.get(f"{API}/check")

        assert response.status_code == 502
        assert response.json()["detail"] == releases.error
