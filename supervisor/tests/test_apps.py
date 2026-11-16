"""App installs keep their data on a mounted storage pool.

A folder of the pools base that is no mounted pool is a plain folder on the
system disk: an install or a start that wrote there would fill it.

Nothing here touches the real host: a temporary directory is the pools base,
``os.path.ismount`` decides which of its directories count as mounted pools,
the Docker client is a MagicMock, and ``os.fchown`` only records its calls.
"""

import asyncio
import fcntl
import importlib
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import requests
import urllib3
from docker.errors import APIError, NotFound
from docker.models.containers import Container
from fastapi import HTTPException
from docker.types import HostConfig
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from src.apps.catalog import APP_CATALOG, get_app_by_id
from src.apps.schemas import (
    POOL_PLACEHOLDER,
    AppCategory,
    AppPool,
    AppSource,
    AppTemplate,
    VolumeMapping,
)
from src import host as host_module
from src.auth.utils import verify_token
from src.config import get_settings
from src.database import Base, get_db
from src.host import HostResult
from src.main import app
from src.shares.models import SmbShare

# The package re-exports its APIRouter as ``router``; import the module.
apps_router = importlib.import_module("src.apps.router")
hardware_module = importlib.import_module("src.apps.hardware")
audit_router = importlib.import_module("src.audit.router")

INSTALL = "/api/v1/apps/install"
POOLS = "/api/v1/apps/pools"
INSTALLED = "/api/v1/apps/installed"
# Transmission installs only with its web login (USER and PASS, see the catalog).
TRANSMISSION = {"template_id": "transmission", "env_overrides": {"USER": "e2e", "PASS": "k7-Unique-pass"}}


def _fd_path(fd: int) -> Path:
    """The path of an open file or directory descriptor."""
    if sys.platform == "darwin":
        return Path(fcntl.fcntl(fd, fcntl.F_GETPATH, bytes(1024)).rstrip(b"\0").decode())
    return Path(os.readlink(f"/proc/self/fd/{fd}"))


def _full_path(path, dir_fd: int | None) -> Path:
    """The path a call with ``dir_fd`` refers to."""
    return Path(path) if dir_fd is None else _fd_path(dir_fd) / path


def _open_fds() -> set[str]:
    """The descriptors this process has open."""
    return set(os.listdir("/dev/fd" if sys.platform == "darwin" else "/proc/self/fd"))


@pytest.fixture(autouse=True)
def chown_calls(monkeypatch) -> list[tuple[Path, int, int]]:
    """Record owner changes (path of the descriptor, uid, gid) instead of making them.

    Making them needs root. Owners are only ever set through a descriptor
    of the new file or folder (os.fchown): a change by name could reach
    whatever was put in its place in the meantime.
    """
    calls: list[tuple[Path, int, int]] = []

    def record(fd, uid, gid):
        calls.append((_fd_path(fd), uid, gid))

    def by_name(path, *args, **kwargs):
        raise AssertionError(f"owner changed by name: {path!r}")

    monkeypatch.setattr(os, "fchown", record)
    monkeypatch.setattr(os, "chown", by_name)
    return calls


def _as_user(is_admin: bool):
    """Override authentication for the duration of a fixture."""
    previous = app.dependency_overrides.get(verify_token)
    app.dependency_overrides[verify_token] = lambda: SimpleNamespace(
        id=1, username="admin" if is_admin else "viewer", is_admin=is_admin
    )
    try:
        yield TestClient(app)
    finally:
        if previous is None:
            app.dependency_overrides.pop(verify_token, None)
        else:
            app.dependency_overrides[verify_token] = previous


@pytest.fixture
def api():
    yield from _as_user(is_admin=True)


@pytest.fixture
def user_api():
    yield from _as_user(is_admin=False)


@pytest.fixture
def anon_api():
    previous = app.dependency_overrides.pop(verify_token, None)
    try:
        yield TestClient(app)
    finally:
        if previous is not None:
            app.dependency_overrides[verify_token] = previous


@pytest.fixture(autouse=True)
def shares(tmp_path_factory):
    """The database of the install route, with the shares a test adds: none by default.

    An install reads the folders of every share from it (ADR-0274). It lives
    outside tmp_path, whose tree some tests compare before and after.
    ``shares(name, folder)`` adds a share row as it is stored, folder as given.
    """
    path = tmp_path_factory.mktemp("apps-db") / "apps.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    maker = async_sessionmaker(
        create_async_engine(f"sqlite+aiosqlite:///{path}", poolclass=NullPool), expire_on_commit=False
    )

    async def override_get_db():
        async with maker() as session:
            yield session

    def add(name: str, folder) -> None:
        with Session(engine) as session:
            session.add(SmbShare(name=name, path=str(folder)))
            session.commit()

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield add
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()


@pytest.fixture
def pools(tmp_path, monkeypatch):
    """Pools base with mounted pools "alpha" and "beta" and an unmounted "ghost".

    "ghost" is what an unmounted pool looks like: a plain directory on the
    boot disk. Tests remove paths from ``mounted`` to unmount a pool.
    """
    base = tmp_path / "pools"
    for name in ("alpha", "beta", "ghost"):
        (base / name).mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    mounted = {str(base / "alpha"), str(base / "beta")}
    monkeypatch.setattr(os.path, "ismount", lambda path: os.fspath(path) in mounted)
    monkeypatch.setattr(get_settings(), "pools_base_path", str(base))
    return SimpleNamespace(
        root=tmp_path,
        base=base,
        alpha=base / "alpha",
        beta=base / "beta",
        ghost=base / "ghost",
        outside=outside,
        mounted=mounted,
    )


@pytest.fixture
def docker_client(monkeypatch):
    client = MagicMock(name="docker")
    client.containers.get.side_effect = NotFound("No such container")
    container = MagicMock(name="container", short_id="c0ffee", status="running")
    client.containers.create.return_value = container
    monkeypatch.setattr(apps_router, "get_client", lambda: client)
    return client


# What the host of the hardware reads answers (src/apps/hardware.py, ADR-0377). The device tree
# is read by a shell that exits 3 where the host has none (NO_DEVICE_TREE).
DEVICE_TREE = "/proc/device-tree/compatible"
NO_DEVICE_TREE = HostResult(3, "", "")


def _reads_device_tree(cmd: list[str]) -> bool:
    return cmd[:2] == ["/bin/sh", "-c"] and cmd[3:] == ["unos-device-tree", DEVICE_TREE]


# The host's listening TCP and bound UDP sockets with their programs, which every install of an
# app that publishes a port reads before the pull (src/apps/preflight.py, ADR-0381).
SS = ["ss", "-H", "-l", "-n", "-t", "-u", "-p"]


def _hardware_reads(soc) -> list[list[str]]:
    """The host commands of ``soc`` but the look for the ports in use: the reads of the hardware."""
    return [cmd for cmd in soc.commands if cmd != SS]


@pytest.fixture(autouse=True)
def soc(monkeypatch):
    """The host for the reads of its SoC and device nodes: no device tree, as on a PC, until a
    test gives it a ``compatible`` (the bytes of /proc/device-tree/compatible) and ``devices``
    (the device nodes it has). ``commands`` records every host command; one that the fake does
    not know fails the test. ``fail`` is raised by every command, ``answer`` (a HostResult) is
    every command's answer, as when nsenter cannot enter the host. ``lookup`` (a HostResult, or an
    exception to raise) answers only the look for the device nodes, after a device tree it read.
    ``sockets`` are the lines that ``ss`` prints for the host's listening sockets (none by
    default), and ``ss`` (a HostResult, or an exception to raise) answers only that read."""
    fake = SimpleNamespace(
        compatible=None, devices=set(), commands=[], fail=None, answer=None, lookup=None, sockets=[], ss=None
    )

    def run(cmd, **kwargs):
        fake.commands.append(list(cmd))
        if fake.fail is not None:
            raise fake.fail
        if fake.answer is not None:
            return fake.answer
        if cmd == SS:
            if isinstance(fake.ss, BaseException):
                raise fake.ss
            if fake.ss is not None:
                return fake.ss
            return HostResult(0, "".join(f"{line}\n" for line in fake.sockets), "")
        if _reads_device_tree(cmd):
            return NO_DEVICE_TREE if fake.compatible is None else HostResult(0, fake.compatible, "")
        if cmd[:2] == ["/bin/sh", "-c"] and cmd[3:4] == ["unos-devices"]:
            if isinstance(fake.lookup, BaseException):
                raise fake.lookup
            if fake.lookup is not None:
                return fake.lookup
            return HostResult(0, "".join(f"{path}\n" for path in cmd[4:] if path in fake.devices), "")
        raise AssertionError(f"unexpected host command: {cmd}")

    monkeypatch.setattr(host_module, "run_on_host", run)
    return fake


def _mounts(docker_client) -> dict[str, dict]:
    """Bind mounts passed to Docker, keyed by container path."""
    kwargs = docker_client.containers.create.call_args.kwargs
    return {mount["Target"]: dict(mount) for mount in kwargs["mounts"] or []}


def _tree(root: Path) -> set[Path]:
    return set(root.rglob("*"))


def _pool(path: Path) -> AppPool:
    return AppPool(name=path.name, path=str(path), free_bytes=1, total_bytes=2)


# ---------------------------------------------------------------------------
# GET /apps/pools
# ---------------------------------------------------------------------------


class TestPoolsEndpoint:
    def test_lists_only_mounted_pools_sorted_by_name(self, api, pools, monkeypatch):
        (pools.base / "notes.txt").write_text("not a pool")
        (pools.base / "link").symlink_to(pools.alpha)
        # Even if a symlink looked like a mount point, it is not a pool.
        pools.mounted.add(str(pools.base / "link"))
        monkeypatch.setattr(
            apps_router.shutil,
            "disk_usage",
            lambda path: SimpleNamespace(total=1000, used=400, free=600),
        )

        response = api.get(POOLS)

        assert response.status_code == 200
        assert response.json() == [
            {"name": "alpha", "path": str(pools.alpha), "free_bytes": 600, "total_bytes": 1000},
            {"name": "beta", "path": str(pools.beta), "free_bytes": 600, "total_bytes": 1000},
        ]

    def test_empty_when_nothing_is_mounted(self, api, pools):
        pools.mounted.clear()
        assert api.get(POOLS).json() == []

    def test_empty_when_pools_base_is_missing(self, api, tmp_path, monkeypatch):
        monkeypatch.setattr(get_settings(), "pools_base_path", str(tmp_path / "missing"))
        assert api.get(POOLS).json() == []

    def test_requires_admin(self, user_api, pools):
        assert user_api.get(POOLS).status_code == 403

    def test_requires_authentication(self, anon_api, pools):
        assert anon_api.get(POOLS).status_code in (401, 403)


# ---------------------------------------------------------------------------
# Pool selection
# ---------------------------------------------------------------------------


class TestPoolSelection:
    def test_defaults_to_first_mounted_pool_by_name(self, api, pools, docker_client):
        response = api.post(INSTALL, json={"template_id": "jellyfin"})

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["pool"] == "alpha"
        assert body["volumes"] == [
            {"host": str(pools.alpha / "apps/jellyfin/config"), "container": "/config", "readonly": False},
            {"host": str(pools.alpha / "apps/jellyfin/cache"), "container": "/cache", "readonly": False},
            {"host": str(pools.alpha / "media"), "container": "/media", "readonly": True},
        ]
        assert _mounts(docker_client) == {
            "/config": {"Target": "/config", "Source": str(pools.alpha / "apps/jellyfin/config"), "Type": "bind", "ReadOnly": False},
            "/cache": {"Target": "/cache", "Source": str(pools.alpha / "apps/jellyfin/cache"), "Type": "bind", "ReadOnly": False},
            "/media": {"Target": "/media", "Source": str(pools.alpha / "media"), "Type": "bind", "ReadOnly": True},
        }
        kwargs = docker_client.containers.create.call_args.kwargs
        # -v style binds would let Docker create missing sources on the boot
        # disk (also on every restart while the pool is unmounted).
        assert "volumes" not in kwargs
        assert kwargs["labels"]["unos.app.pool"] == "alpha"
        for sub in ("apps/jellyfin/config", "apps/jellyfin/cache", "media"):
            assert (pools.alpha / sub).is_dir()

    def test_uses_requested_pool(self, api, pools, docker_client):
        response = api.post(INSTALL, json={"template_id": "jellyfin", "pool": "beta"})

        assert response.status_code == 200, response.text
        assert response.json()["pool"] == "beta"
        assert {m["Source"] for m in _mounts(docker_client).values()} == {
            str(pools.beta / "apps/jellyfin/config"),
            str(pools.beta / "apps/jellyfin/cache"),
            str(pools.beta / "media"),
        }
        assert not (pools.alpha / "apps").exists()

    def test_no_mounted_pool_is_a_conflict(self, api, pools, docker_client):
        pools.mounted.clear()
        before = _tree(pools.root)

        response = api.post(INSTALL, json={**TRANSMISSION})

        assert response.status_code == 409
        assert response.json()["detail"] == (
            "No mounted storage pool — create or mount a pool first"
        )
        docker_client.images.pull.assert_not_called()
        assert _tree(pools.root) == before

    @pytest.mark.parametrize("pool", ["ghost", "nope", "../alpha", ""])
    def test_unknown_or_unmounted_pool_is_rejected(self, api, pools, docker_client, pool):
        before = _tree(pools.root)

        response = api.post(INSTALL, json={**TRANSMISSION, "pool": pool})

        assert response.status_code == 400
        assert "not mounted" in response.json()["detail"]
        docker_client.images.pull.assert_not_called()
        assert _tree(pools.root) == before

    def test_app_without_pool_data_installs_without_a_pool(self, api, pools, docker_client):
        pools.mounted.clear()

        response = api.post(INSTALL, json={"template_id": "dozzle"})

        assert response.status_code == 200, response.text
        assert response.json()["pool"] is None
        assert _mounts(docker_client) == {
            "/var/run/docker.sock": {
                "Target": "/var/run/docker.sock",
                "Source": "/var/run/docker.sock",
                "Type": "bind",
                "ReadOnly": True,
            }
        }
        assert "unos.app.pool" not in docker_client.containers.create.call_args.kwargs["labels"]
        # The app's log is capped as the UNOS containers' are (ADR-0330).
        assert docker_client.containers.create.call_args.kwargs["log_config"] == {
            "Type": "json-file", "Config": {"max-size": "10m", "max-file": "3"},
        }

    def test_requested_pool_is_validated_but_not_reported_without_pool_data(
        self, api, pools, docker_client
    ):
        bad = api.post(INSTALL, json={"template_id": "dozzle", "pool": "ghost"})
        assert bad.status_code == 400

        good = api.post(INSTALL, json={"template_id": "dozzle", "pool": "beta"})
        assert good.status_code == 200, good.text
        assert good.json()["pool"] is None
        assert "unos.app.pool" not in docker_client.containers.create.call_args.kwargs["labels"]

    def test_install_requires_admin(self, user_api, pools, docker_client):
        response = user_api.post(INSTALL, json={"template_id": "jellyfin"})

        assert response.status_code == 403
        docker_client.images.pull.assert_not_called()


# ---------------------------------------------------------------------------
# Catalog path substitution
# ---------------------------------------------------------------------------

FIXED = object()  # marker: a fixed host path, mounted as-is

EXPECTED_PATHS = {
    "plex": {
        "/config": "apps/plex/config",
        "/transcode": "apps/plex/transcode",
        "/data": "media",
    },
    "transmission": {
        "/config": "apps/transmission/config",
        "/downloads": "downloads",
        # Not downloads/watch: other apps can write to downloads.
        "/watch": "apps/transmission/watch",
    },
    "sabnzbd": {
        "/config": "apps/sabnzbd/config",
        "/downloads": "downloads",
        "/incomplete-downloads": "apps/sabnzbd/incomplete",
    },
    "filebrowser": {
        "/database": "apps/filebrowser/database",
        "/config": "apps/filebrowser/config",
        "/srv": "",  # the pool root itself
    },
    "portainer": {
        "/data": "apps/portainer/data",
        "/var/run/docker.sock": FIXED,
    },
    "netdata": {
        "/etc/netdata": "apps/netdata/config",
        "/var/lib/netdata": "apps/netdata/lib",
        "/var/cache/netdata": "apps/netdata/cache",
        "/host/etc/passwd": FIXED,
        "/host/etc/group": FIXED,
        "/host/proc": FIXED,
        "/host/sys": FIXED,
    },
}


class TestCatalogPaths:
    @pytest.fixture
    def pool_dir(self, tmp_path, monkeypatch) -> Path:
        """A pool directory below the pools base."""
        monkeypatch.setattr(get_settings(), "pools_base_path", str(tmp_path))
        pool_dir = tmp_path / "data"
        pool_dir.mkdir()
        return pool_dir

    @pytest.mark.parametrize("app_id", sorted(EXPECTED_PATHS))
    def test_pool_paths_are_substituted(self, pool_dir, app_id):
        pool = _pool(pool_dir)
        template = get_app_by_id(app_id)

        planned = apps_router._plan_volumes(template, {}, pool, [pool])

        sources = {m.target: m for m in planned}
        assert set(sources) == set(EXPECTED_PATHS[app_id])
        volumes = {v.container: v for v in template.volumes}
        for target, relative in EXPECTED_PATHS[app_id].items():
            if relative is FIXED:
                assert sources[target].source == Path(volumes[target].host)
                assert sources[target].pool is None
            else:
                expected = pool_dir / relative if relative else pool_dir
                assert sources[target].source == expected
                assert sources[target].pool == pool

    def test_catalog_has_no_hard_coded_pool_paths(self):
        for template in APP_CATALOG:
            targets = [v.container for v in template.volumes]
            assert len(targets) == len(set(targets)), template.id
            for volume in template.volumes:
                assert "/mnt/pools" not in volume.host, template.id
                assert volume.on_pool or volume.host.startswith("/"), template.id
                assert ".." not in volume.host.split("/"), template.id

    def test_every_template_keeps_pool_data_inside_the_pool(self, pool_dir):
        pool = _pool(pool_dir)
        for template in APP_CATALOG:
            for m in apps_router._plan_volumes(template, {}, pool, [pool]):
                if m.pool is not None:
                    assert m.source.is_relative_to(pool_dir), (template.id, m)
                else:
                    assert not str(m.source).startswith(POOL_PLACEHOLDER), template.id

    def test_catalog_ships_no_placeholder_secrets(self):
        for template in APP_CATALOG:
            for var in template.environment:
                if var.secret:
                    assert var.value.strip().lower() not in apps_router.PLACEHOLDER_SECRETS, (
                        template.id,
                        var.name,
                    )
                if var.secret and var.required:
                    assert var.value == "", (template.id, var.name)

    def test_catalog_api_marks_pool_and_fixed_volumes(self, api):
        response = api.get("/api/v1/apps/catalog/portainer")

        assert response.status_code == 200
        assert response.json()["volumes"] == [
            {"host": f"{POOL_PLACEHOLDER}/apps/portainer/data", "container": "/data",
             "readonly": False, "is_file": False, "uid": None, "gid": None, "on_pool": True},
            {"host": "/var/run/docker.sock", "container": "/var/run/docker.sock",
             "readonly": False, "is_file": False, "uid": None, "gid": None, "on_pool": False},
        ]


# ---------------------------------------------------------------------------
# Nothing is written outside the selected pool
# ---------------------------------------------------------------------------


@pytest.fixture
def mkdir_calls(monkeypatch, tmp_path_factory) -> list[Path]:
    """Record the path of every directory created with os.mkdir (os.makedirs uses it too).

    All but the folder of the audit log, which an install writes its entry to (ADR-0350): the
    log is redirected to a folder of the test, outside the pools.
    """
    calls: list[Path] = []
    real_mkdir = os.mkdir
    audit_folder = tmp_path_factory.mktemp("audit")
    monkeypatch.setattr(audit_router, "AUDIT_FILE", str(audit_folder / "audit.jsonl"))

    def spy(path, mode=0o777, *, dir_fd=None):
        if _full_path(path, dir_fd) != audit_folder:
            calls.append(_full_path(path, dir_fd))
        return real_mkdir(path, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "mkdir", spy)
    return calls


class TestNoWritesOutsidePool:
    def test_only_the_selected_pool_gets_directories(self, api, pools, docker_client, mkdir_calls):
        before = _tree(pools.root)

        # netdata mixes pool data with fixed host paths (/proc, /etc/passwd...).
        response = api.post(INSTALL, json={"template_id": "netdata"})

        assert response.status_code == 200, response.text
        assert mkdir_calls, "expected the app directories to be created"
        assert all(path.is_relative_to(pools.alpha) for path in mkdir_calls), mkdir_calls
        new_paths = _tree(pools.root) - before
        assert new_paths and all(path.is_relative_to(pools.alpha) for path in new_paths)
        mounts = _mounts(docker_client)
        assert mounts["/host/proc"]["Source"] == "/proc"
        assert mounts["/host/etc/passwd"]["Source"] == "/etc/passwd"

    def test_fixed_host_paths_are_never_created(self, api, pools, docker_client, mkdir_calls):
        response = api.post(INSTALL, json={"template_id": "portainer"})

        assert response.status_code == 200, response.text
        assert mkdir_calls == [
            pools.alpha / "apps",
            pools.alpha / "apps/portainer",
            pools.alpha / "apps/portainer/data",
        ]
        assert _mounts(docker_client)["/var/run/docker.sock"]["Source"] == "/var/run/docker.sock"

    def test_pool_unmounted_during_pull_writes_nothing(self, api, pools, docker_client):
        docker_client.images.pull.side_effect = lambda *a, **k: pools.mounted.discard(str(pools.alpha))
        before = _tree(pools.root)

        response = api.post(INSTALL, json={**TRANSMISSION})

        assert response.status_code == 409
        assert "no longer mounted" in response.json()["detail"]
        assert _tree(pools.root) == before
        docker_client.containers.create.assert_not_called()

    def test_existing_data_is_reused(self, api, pools, docker_client):
        config = pools.alpha / "apps/transmission/config"
        config.mkdir(parents=True)
        (config / "settings.json").write_text("{}")

        response = api.post(INSTALL, json={**TRANSMISSION})

        assert response.status_code == 200, response.text
        assert (config / "settings.json").read_text() == "{}"


# ---------------------------------------------------------------------------
# volume_overrides
# ---------------------------------------------------------------------------


class TestVolumeOverrides:
    @pytest.mark.parametrize(
        "make_path",
        [
            lambda p: "/",
            lambda p: "/etc",
            lambda p: "relative/downloads",
            lambda p: "",
            lambda p: str(p.base),  # the pools base, i.e. every pool and the SD card
            lambda p: str(p.alpha / ".." / ".." / "outside"),
            lambda p: str(p.ghost / "downloads"),  # unmounted pool = boot disk
            lambda p: str(p.alpha / "escape" / "downloads"),  # symlink out of the pool
        ],
        ids=["root", "etc", "relative", "empty", "pools-base", "dotdot", "unmounted-pool", "symlink"],
    )
    def test_paths_outside_mounted_pools_are_rejected(self, api, pools, docker_client, make_path):
        (pools.alpha / "escape").symlink_to(pools.outside)
        before = _tree(pools.root)

        response = api.post(
            INSTALL,
            json={**TRANSMISSION, "volume_overrides": {"/downloads": make_path(pools)}},
        )

        assert response.status_code == 400, response.text
        docker_client.images.pull.assert_not_called()
        assert _tree(pools.root) == before

    def test_unknown_volume_cannot_be_added(self, api, pools, docker_client):
        response = api.post(
            INSTALL,
            json={**TRANSMISSION, "volume_overrides": {"/host-root": str(pools.alpha)}},
        )

        assert response.status_code == 400
        assert "/host-root" in response.json()["detail"]
        docker_client.images.pull.assert_not_called()

    def test_fixed_host_mount_cannot_be_changed(self, api, pools, docker_client):
        response = api.post(
            INSTALL,
            json={
                "template_id": "portainer",
                "volume_overrides": {"/var/run/docker.sock": str(pools.alpha / "docker.sock")},
            },
        )

        assert response.status_code == 400
        assert "fixed host mount" in response.json()["detail"]
        docker_client.images.pull.assert_not_called()

    def test_path_in_selected_pool_is_created_and_normalised(self, api, pools, docker_client):
        raw = f"{pools.alpha}//torrents/./new/"

        response = api.post(
            INSTALL,
            json={**TRANSMISSION, "volume_overrides": {"/downloads": raw}},
        )

        assert response.status_code == 200, response.text
        assert (pools.alpha / "torrents/new").is_dir()
        assert _mounts(docker_client)["/downloads"]["Source"] == str(pools.alpha / "torrents/new")

    def test_dotdot_is_refused_even_inside_the_pool(self, api, pools, docker_client):
        before = _tree(pools.root)

        response = api.post(
            INSTALL,
            json={
                **TRANSMISSION,
                "volume_overrides": {"/downloads": f"{pools.alpha}/media/../torrents"},
            },
        )

        assert response.status_code == 400
        assert "traversal" in response.json()["detail"]
        docker_client.images.pull.assert_not_called()
        assert _tree(pools.root) == before

    def test_link_to_another_pools_folder_is_not_resolved(self, api, pools, docker_client):
        # Pool contents are writable by SMB users and apps. Resolving this link
        # used to bind-mount beta's private folder into the new app.
        private = pools.beta / "apps/vaultwarden/data"
        private.mkdir(parents=True)
        (pools.alpha / "shared").symlink_to(private)
        before = _tree(pools.root)

        response = api.post(
            INSTALL,
            json={**TRANSMISSION, "volume_overrides": {"/downloads": str(pools.alpha / "shared")}},
        )

        assert response.status_code == 400
        detail = response.json()["detail"]
        assert "symbolic link" in detail and str(pools.alpha / "shared") in detail
        docker_client.images.pull.assert_not_called()
        assert _tree(pools.root) == before

    @pytest.mark.parametrize("override", ["alias", "alias/torrents"])
    def test_link_inside_the_same_pool_is_not_resolved(self, api, pools, docker_client, override):
        (pools.alpha / "library").mkdir()
        (pools.alpha / "alias").symlink_to(pools.alpha / "library")
        before = _tree(pools.root)

        response = api.post(
            INSTALL,
            json={**TRANSMISSION, "volume_overrides": {"/downloads": str(pools.alpha / override)}},
        )

        assert response.status_code == 400
        assert "symbolic link" in response.json()["detail"]
        docker_client.images.pull.assert_not_called()
        assert _tree(pools.root) == before

    def test_existing_path_in_another_mounted_pool_is_used(self, api, pools, docker_client):
        library = pools.beta / "torrents"
        library.mkdir()

        response = api.post(
            INSTALL,
            json={**TRANSMISSION, "volume_overrides": {"/downloads": str(library)}},
        )

        assert response.status_code == 200, response.text
        assert response.json()["pool"] == "alpha"
        assert _mounts(docker_client)["/downloads"]["Source"] == str(library)
        assert _mounts(docker_client)["/config"]["Source"] == str(pools.alpha / "apps/transmission/config")

    def test_missing_path_in_another_pool_is_not_created(self, api, pools, docker_client):
        before = _tree(pools.root)

        response = api.post(
            INSTALL,
            json={**TRANSMISSION, "volume_overrides": {"/downloads": str(pools.beta / "torrents")}},
        )

        assert response.status_code == 400
        assert "does not exist" in response.json()["detail"]
        assert _tree(pools.root) == before
        docker_client.images.pull.assert_not_called()


# ---------------------------------------------------------------------------
# Required environment and placeholder secrets
# ---------------------------------------------------------------------------


class TestEnvironment:
    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_required_variable_must_be_set(self, api, pools, docker_client, value):
        env = {} if value is None else {"PHOTOPRISM_ADMIN_PASSWORD": value}

        response = api.post(INSTALL, json={"template_id": "photoprism", "env_overrides": env})

        assert response.status_code == 400
        assert "PHOTOPRISM_ADMIN_PASSWORD is required" in response.json()["detail"]
        docker_client.images.pull.assert_not_called()

    @pytest.mark.parametrize(
        "app_id,name,value",
        [
            ("photoprism", "PHOTOPRISM_ADMIN_PASSWORD", "changeme"),
            ("grafana", "GF_SECURITY_ADMIN_PASSWORD", "Admin"),
            ("bookstack", "DB_PASSWORD", " password "),
            ("aria2", "RPC_SECRET", "secret"),  # optional secrets are checked too
            ("transmission", "PASS", "changeme"),
        ],
    )
    def test_placeholder_secrets_are_rejected(self, api, pools, docker_client, app_id, name, value):
        response = api.post(INSTALL, json={"template_id": app_id, "env_overrides": {name: value}})

        assert response.status_code == 400
        assert name in response.json()["detail"]
        assert "placeholder" in response.json()["detail"]
        docker_client.images.pull.assert_not_called()

    def test_unique_secret_is_passed_to_the_container(self, api, pools, docker_client):
        response = api.post(
            INSTALL,
            json={
                "template_id": "grafana",
                "env_overrides": {"GF_SECURITY_ADMIN_PASSWORD": "k7-Unique-pass"},
            },
        )

        assert response.status_code == 200, response.text
        environment = docker_client.containers.create.call_args.kwargs["environment"]
        assert environment == {"GF_SECURITY_ADMIN_PASSWORD": "k7-Unique-pass"}

    @pytest.mark.parametrize("login", [{}, {"USER": "e2e"}, {"PASS": "k7-Unique-pass"}, {"USER": " ", "PASS": "k7-Unique-pass"}])
    def test_transmission_needs_a_login_and_a_password(self, api, pools, docker_client, login):
        # Without both the image turns RPC authentication off at every start.
        variables = {var.name: var for var in get_app_by_id("transmission").environment}
        assert (variables["USER"].required, variables["USER"].secret) == (True, False)
        assert (variables["PASS"].required, variables["PASS"].secret) == (True, True)

        response = api.post(INSTALL, json={"template_id": "transmission", "env_overrides": login})

        assert response.status_code == 400
        assert "is required" in response.json()["detail"]
        docker_client.images.pull.assert_not_called()

    def test_transmission_login_is_passed_to_the_container(self, api, pools, docker_client):
        response = api.post(INSTALL, json=TRANSMISSION)

        assert response.status_code == 200, response.text
        environment = docker_client.containers.create.call_args.kwargs["environment"]
        assert (environment["USER"], environment["PASS"]) == ("e2e", "k7-Unique-pass")

    def test_tailscale_installs_without_an_auth_key(self, api, pools, docker_client):
        # Without a key the container logs a login URL to follow.
        [key] = [var for var in get_app_by_id("tailscale").environment if var.name == "TS_AUTHKEY"]
        assert (key.required, key.secret) == (False, True)
        assert "logs" in key.description and "login URL" in key.description

        response = api.post(INSTALL, json={"template_id": "tailscale"})

        assert response.status_code == 200, response.text
        assert docker_client.containers.create.call_args.kwargs["environment"]["TS_AUTHKEY"] == ""

    def test_tailscale_auth_key_is_passed_and_checked(self, api, pools, docker_client):
        bad = api.post(INSTALL, json={"template_id": "tailscale", "env_overrides": {"TS_AUTHKEY": "changeme"}})
        assert bad.status_code == 400
        assert "placeholder" in bad.json()["detail"]

        good = api.post(INSTALL, json={"template_id": "tailscale", "env_overrides": {"TS_AUTHKEY": "tskey-auth-k1"}})
        assert good.status_code == 200, good.text
        assert docker_client.containers.create.call_args.kwargs["environment"]["TS_AUTHKEY"] == "tskey-auth-k1"


# ---------------------------------------------------------------------------
# File-like sources on the pool
# ---------------------------------------------------------------------------


DEMO = AppTemplate(
    id="demo",
    name="Demo",
    description="Template with a single-file volume",
    icon="x",
    category=AppCategory.OTHER,
    image="example/demo:1",
    version="1",
    volumes=[
        VolumeMapping(host=f"{POOL_PLACEHOLDER}/apps/demo/app.db", container="/app.db", is_file=True),
        VolumeMapping(host=f"{POOL_PLACEHOLDER}/apps/demo/data", container="/data"),
    ],
)


class TestFileVolumes:
    @pytest.fixture(autouse=True)
    def demo_template(self, monkeypatch):
        real = apps_router.get_app_by_id
        monkeypatch.setattr(
            apps_router, "get_app_by_id", lambda app_id: DEMO if app_id == "demo" else real(app_id)
        )

    def test_missing_file_is_created_empty(self, api, pools, docker_client):
        response = api.post(INSTALL, json={"template_id": "demo"})

        assert response.status_code == 200, response.text
        db = pools.alpha / "apps/demo/app.db"
        assert db.is_file() and db.stat().st_size == 0
        assert (pools.alpha / "apps/demo/data").is_dir()
        assert _mounts(docker_client)["/app.db"]["Source"] == str(db)

    def test_existing_file_is_kept(self, api, pools, docker_client):
        db = pools.alpha / "apps/demo/app.db"
        db.parent.mkdir(parents=True)
        db.write_bytes(b"data")

        response = api.post(INSTALL, json={"template_id": "demo"})

        assert response.status_code == 200, response.text
        assert db.read_bytes() == b"data"

    def test_directory_in_place_of_a_file_is_rejected(self, api, pools, docker_client):
        # What Docker leaves behind when it auto-creates a missing file source.
        (pools.alpha / "apps/demo/app.db").mkdir(parents=True)

        response = api.post(INSTALL, json={"template_id": "demo"})

        assert response.status_code == 400
        assert "needs a file" in response.json()["detail"]
        docker_client.images.pull.assert_not_called()

    def test_file_override_is_normalised(self, api, pools, docker_client):
        (pools.beta / "db").mkdir()
        (pools.beta / "db/app.db").write_bytes(b"data")

        response = api.post(
            INSTALL,
            json={"template_id": "demo", "volume_overrides": {"/app.db": f"{pools.beta}//db/./app.db"}},
        )

        assert response.status_code == 200, response.text
        assert _mounts(docker_client)["/app.db"]["Source"] == str(pools.beta / "db/app.db")

    def test_file_in_a_linked_folder_is_refused(self, api, pools, docker_client):
        (pools.alpha / "linked").symlink_to(pools.outside)
        before = _tree(pools.root)

        response = api.post(
            INSTALL,
            json={"template_id": "demo", "volume_overrides": {"/app.db": str(pools.alpha / "linked/app.db")}},
        )

        assert response.status_code == 400
        assert "symbolic link" in response.json()["detail"]
        docker_client.images.pull.assert_not_called()
        assert _tree(pools.root) == before

    def test_link_in_place_of_the_file_is_refused(self, api, pools, docker_client):
        (pools.alpha / "apps/demo").mkdir(parents=True)
        (pools.alpha / "apps/demo/app.db").symlink_to(pools.outside / "app.db")

        response = api.post(INSTALL, json={"template_id": "demo"})

        assert response.status_code == 409
        assert "symbolic link" in response.json()["detail"]
        docker_client.images.pull.assert_not_called()
        assert list(pools.outside.iterdir()) == []

    @pytest.mark.parametrize(
        "path", ["{alpha}/apps/demo/..", "{alpha}/apps/demo/.", "/", "{alpha}/apps/demo/app\n.db"]
    )
    def test_file_override_must_name_a_file(self, api, pools, docker_client, path):
        response = api.post(
            INSTALL,
            json={"template_id": "demo", "volume_overrides": {"/app.db": path.format(alpha=pools.alpha)}},
        )

        assert response.status_code == 400
        assert "must name a file" in response.json()["detail"]
        docker_client.images.pull.assert_not_called()


# ---------------------------------------------------------------------------
# Docker flow
# ---------------------------------------------------------------------------


class TestDockerFlow:
    def test_existing_container_is_a_conflict(self, api, pools, docker_client):
        docker_client.containers.get.side_effect = None
        docker_client.containers.get.return_value = MagicMock(name="existing")

        response = api.post(INSTALL, json={"template_id": "jellyfin"})

        assert response.status_code == 409
        assert "already exists" in response.json()["detail"]
        docker_client.images.pull.assert_not_called()
        assert not (pools.alpha / "apps").exists()

    def test_failed_start_removes_the_container(self, api, pools, docker_client):
        container = docker_client.containers.create.return_value
        container.start.side_effect = APIError("port is already allocated")

        response = api.post(INSTALL, json={"template_id": "jellyfin"})

        assert response.status_code == 500
        assert "port is already allocated" in response.json()["detail"]
        container.remove.assert_called_once_with(force=True)

    @pytest.mark.parametrize("name", ["../etc", "a b", "-x", "x"])
    def test_invalid_container_name_is_rejected(self, api, pools, docker_client, name):
        response = api.post(INSTALL, json={"template_id": "jellyfin", "name": name})

        assert response.status_code == 400
        docker_client.containers.get.assert_not_called()


# ---------------------------------------------------------------------------
# The host ports of an install, checked before the pull (ADR-0381)
# ---------------------------------------------------------------------------


def _published(port: int, protocol: str = "tcp", host_ip: str = "") -> dict[str, list[dict]]:
    """Docker's PortBindings of a container that publishes ``port`` under the same number."""
    return {f"{port}/{protocol}": [{"HostIp": host_ip, "HostPort": str(port)}]}


# Lines that `ss -H -l -n -t -u -p` prints: the type, the state, the queues, the local address
# and port, the peer and the programs that hold the socket.
EMBY_ANY = 'tcp   LISTEN 0      4096         0.0.0.0:8096       0.0.0.0:*    users:(("EmbyServer",pid=812,fd=7))'
RESOLVED = [
    'udp   UNCONN 0      0      127.0.0.53%lo:53          0.0.0.0:*    users:(("systemd-resolve",pid=520,fd=13))',
    'tcp   LISTEN 0      4096   127.0.0.53%lo:53          0.0.0.0:*    users:(("systemd-resolve",pid=520,fd=14))',
]
PIHOLE_LOGIN = {"WEBPASSWORD": "k7-Unique-pass"}


class TestPortCheck:
    """An install checks the host ports its container would publish before it pulls anything: a
    port that a container publishes, running or stopped, or that a program on the host listens
    on, answers 409 instead of a start that fails after a pull of minutes (ADR-0381)."""

    @pytest.mark.parametrize("status", ["running", "exited", "created"])
    def test_a_port_that_a_container_publishes_answers_409(self, api, pools, docker_client, installed, soc, status):
        installed(_container("emby", status=status, port_bindings=_published(8096)))
        # A running container's port is also a listener of Docker's proxy on the host.
        soc.sockets = ['tcp   LISTEN 0      4096   0.0.0.0:8096   0.0.0.0:*   users:(("docker-proxy",pid=9,fd=7))']

        response = api.post(INSTALL, json={"template_id": "jellyfin"})

        assert (response.status_code, response.json()) == (409, {
            "detail": f"Port 8096/tcp is already used by the container emby ({status}): "
            "choose another host port for 8096/tcp"
        })
        # Stopped containers too: Docker binds their ports when they start.
        docker_client.containers.list.assert_called_once_with(all=True, ignore_removed=True)
        docker_client.images.pull.assert_not_called()
        docker_client.containers.create.assert_not_called()
        assert not (pools.alpha / "apps").exists()

    def test_a_container_that_publishes_the_port_on_one_address_holds_it(self, api, pools, docker_client, installed):
        installed(_container("emby", status="running", port_bindings=_published(8096, host_ip="127.0.0.1")))

        response = api.post(INSTALL, json={"template_id": "jellyfin"})

        assert response.status_code == 409
        assert response.json()["detail"].startswith("Port 8096/tcp is already used by the container emby (running)")

    @pytest.mark.parametrize("port_bindings", [
        None,
        _published(8096, "udp"),
        _published(18096),
        # A port that Docker chooses when the container starts.
        {"8096/tcp": [{"HostIp": "", "HostPort": ""}]},
    ], ids=["none", "other-protocol", "other-port", "chosen-by-docker"])
    def test_other_ports_of_containers_do_not_count(self, api, pools, docker_client, installed, port_bindings):
        installed(_container("emby", status="running", port_bindings=port_bindings))

        response = api.post(INSTALL, json={"template_id": "jellyfin"})

        assert response.status_code == 200, response.text

    @pytest.mark.parametrize(("line", "holder"), [
        (EMBY_ANY, "EmbyServer"),
        ('tcp   LISTEN 0      4096            [::]:8096          [::]:*    users:(("EmbyServer",pid=812,fd=8))',
         "EmbyServer"),
        ('tcp   LISTEN 0      4096       127.0.0.1:8096       0.0.0.0:*    users:(("EmbyServer",pid=812,fd=9))',
         "EmbyServer"),
        ('tcp   LISTEN 0      4096      192.0.2.10:8096       0.0.0.0:*    users:(("EmbyServer",pid=812,fd=9))',
         "EmbyServer"),
        ('tcp   LISTEN 0      4096               *:8096             *:*    users:(("EmbyServer",pid=812,fd=9))',
         "EmbyServer"),
        # Without the right to read a process's descriptors, ss names no program.
        ("tcp   LISTEN 0      4096         0.0.0.0:8096       0.0.0.0:*", "a program"),
    ], ids=["any-ipv4", "any-ipv6", "loopback", "one-address", "any", "no-program"])
    def test_a_port_that_a_program_on_the_host_listens_on_answers_409(
        self, api, pools, docker_client, soc, line, holder
    ):
        soc.sockets = [line]

        response = api.post(INSTALL, json={"template_id": "jellyfin"})

        assert (response.status_code, response.json()) == (409, {
            "detail": f"Port 8096/tcp is already in use on the host by {holder}: choose another host port for 8096/tcp"
        })
        assert soc.commands[0] == SS
        docker_client.images.pull.assert_not_called()
        assert not (pools.alpha / "apps").exists()

    def test_listeners_of_other_ports_and_protocols_do_not_count(self, api, pools, docker_client, soc):
        soc.sockets = [
            'udp   UNCONN 0      0            0.0.0.0:8096       0.0.0.0:*    users:(("EmbyServer",pid=8,fd=7))',
            'tcp   LISTEN 0      4096         0.0.0.0:18096      0.0.0.0:*    users:(("EmbyServer",pid=8,fd=8))',
            'tcp   LISTEN 0      4096            [::]:80960         [::]:*    users:(("EmbyServer",pid=8,fd=9))',
            'tcp   LISTEN 0      4096         0.0.0.0:809        0.0.0.0:*    users:(("EmbyServer",pid=8,fd=9))',
        ]

        response = api.post(INSTALL, json={"template_id": "jellyfin"})

        assert response.status_code == 200, response.text

    def test_every_taken_port_is_named(self, api, pools, docker_client, soc):
        # The resolver of systemd listens on port 53 of 127.0.0.53 by TCP and UDP.
        soc.sockets = RESOLVED

        response = api.post(INSTALL, json={"template_id": "pihole", "env_overrides": PIHOLE_LOGIN})

        assert (response.status_code, response.json()) == (409, {
            "detail": "Port 53/tcp is already in use on the host by systemd-resolve: "
            "choose another host port for 53/tcp; "
            "Port 53/udp is already in use on the host by systemd-resolve: "
            "choose another host port for 53/udp"
        })
        docker_client.images.pull.assert_not_called()

    def test_a_port_is_taken_only_for_its_protocol(self, api, pools, docker_client, soc):
        # Transmission's peer port is a TCP and a UDP port of one number; one override moves both.
        soc.sockets = ['udp   UNCONN 0      0      0.0.0.0:51413      0.0.0.0:*    users:(("qbittorrent-nox",pid=4,fd=9))']

        taken = api.post(INSTALL, json=TRANSMISSION)
        moved = api.post(INSTALL, json={**TRANSMISSION, "port_overrides": {51413: 51414}})

        assert (taken.status_code, taken.json()) == (409, {
            "detail": "Port 51413/udp is already in use on the host by qbittorrent-nox: "
            "choose another host port for 51413/udp"
        })
        assert moved.status_code == 200, moved.text
        assert docker_client.containers.create.call_args.kwargs["ports"] == {
            "9091/tcp": 9091, "51413/tcp": 51414, "51413/udp": 51414,
        }
        docker_client.images.pull.assert_called_once()

    def test_a_free_host_port_given_for_a_taken_one_installs(self, api, pools, docker_client, installed, soc):
        installed(_container("emby", status="running", port_bindings=_published(8096)))
        soc.sockets = ['tcp   LISTEN 0      4096   0.0.0.0:8096   0.0.0.0:*   users:(("docker-proxy",pid=9,fd=7))']

        response = api.post(INSTALL, json={"template_id": "jellyfin", "port_overrides": {8096: 8097}})

        assert response.status_code == 200, response.text
        assert docker_client.containers.create.call_args.kwargs["ports"] == {"8096/tcp": 8097}
        assert response.json()["web_url"] == "http://localhost:8097"

    @pytest.mark.parametrize("port", [0, -1, 65536])
    def test_a_host_port_outside_the_range_answers_400(self, api, pools, docker_client, soc, port):
        response = api.post(INSTALL, json={"template_id": "jellyfin", "port_overrides": {8096: port}})

        assert (response.status_code, response.json()) == (
            400, {"detail": "Host port for 8096/tcp must be between 1 and 65535"}
        )
        docker_client.containers.get.assert_not_called()
        assert soc.commands == []

    def test_a_host_port_given_twice_answers_400(self, api, pools, docker_client, soc):
        # Pi-hole's web page (80/tcp, on 8053) moved onto its DNS port (53/tcp).
        response = api.post(
            INSTALL, json={"template_id": "pihole", "env_overrides": PIHOLE_LOGIN, "port_overrides": {80: 53}}
        )

        assert (response.status_code, response.json()) == (400, {"detail": "Host port 53/tcp is given twice"})
        docker_client.containers.get.assert_not_called()
        assert soc.commands == []

    @pytest.mark.parametrize(("answer", "message"), [
        (
            HostResult(1, "", "nsenter: reassociate to namespace 'ns/net' failed: Operation not permitted"),
            "Could not read the ports in use on the host (exit status 1): "
            "nsenter: reassociate to namespace 'ns/net' failed: Operation not permitted",
        ),
        (
            subprocess.TimeoutExpired(SS, 15),
            f"Could not read the ports in use on the host: Command '{SS}' timed out after 15 seconds",
        ),
        (
            FileNotFoundError(2, "No such file or directory", "nsenter"),
            "Could not read the ports in use on the host: [Errno 2] No such file or directory: 'nsenter'",
        ),
    ], ids=["exit-status", "timeout", "oserror"])
    def test_a_read_of_the_host_that_fails_is_logged_and_the_install_goes_on(
        self, api, pools, docker_client, soc, caplog, answer, message
    ):
        # Docker's own bind refuses a taken port when the container starts.
        soc.ss = answer

        with caplog.at_level("WARNING", logger="src.apps.preflight"):
            response = api.post(INSTALL, json={"template_id": "jellyfin"})

        assert response.status_code == 200, response.text
        assert [r.getMessage() for r in caplog.records if r.name == "src.apps.preflight"] == [message]
        docker_client.containers.create.return_value.start.assert_called_once()

    def test_an_app_that_publishes_no_port_reads_nothing_of_the_host(self, api, pools, docker_client, soc):
        # Tailscale runs in the host's network and publishes no port.
        response = api.post(INSTALL, json={"template_id": "tailscale"})

        assert response.status_code == 200, response.text
        assert soc.commands == []

    def test_the_host_is_read_off_the_event_loop(self, api, pools, docker_client, soc, monkeypatch):
        seen: list[bool] = []
        real = host_module.run_on_host

        def run(cmd, **kwargs):
            seen.append(_on_event_loop())
            return real(cmd, **kwargs)

        monkeypatch.setattr(host_module, "run_on_host", run)

        # Dozzle has no hardware profile: its install reads the host's ports only.
        assert api.post(INSTALL, json={"template_id": "dozzle"}).status_code == 200
        assert seen == [False]

    def test_a_listing_that_docker_does_not_answer_answers_503(self, api, pools, docker_client):
        error = requests.exceptions.ConnectionError("('Connection aborted.', ConnectionResetError(104, 'reset'))")
        docker_client.containers.list.side_effect = error

        response = api.post(INSTALL, json={"template_id": "jellyfin"})

        assert (response.status_code, response.json()) == (503, {"detail": f"Docker is not available: {error}"})
        docker_client.images.pull.assert_not_called()


# ---------------------------------------------------------------------------
# Installed containers (mocked Docker objects)
# ---------------------------------------------------------------------------

START = "/api/v1/apps/installed/{}/start"
RESTART = "/api/v1/apps/installed/{}/restart"
STOP = "/api/v1/apps/installed/{}/stop"
UNINSTALL = "/api/v1/apps/installed/{}"


def _bind(source, target: str, *, rw: bool = True, propagation: str = "rprivate") -> dict:
    """A bind mount as ``docker inspect`` reports it under "Mounts"."""
    return {
        "Type": "bind",
        "Source": str(source),
        "Destination": target,
        "Mode": "",
        "RW": rw,
        "Propagation": propagation,
    }


def _container(
    name: str,
    *,
    template: str | None = None,
    pool: str | None = None,
    mounts: list[dict] = (),
    status: str = "exited",
    error: str = "",
    policy: str = "unless-stopped",
    env: list[str] = (),
    devices: list[str] | None = None,
    port_bindings: dict[str, list[dict]] | None = None,
) -> MagicMock:
    """``devices``: the host's device nodes passed in, as Docker keeps them (it answers null for
    a container that has none). ``port_bindings``: the host ports it publishes, as Docker keeps
    them ("8096/tcp": [{"HostIp": "", "HostPort": "8096"}]; null for none)."""
    labels = {}
    if template is not None:
        labels["unos.app"] = template
    if pool is not None:
        labels["unos.app.pool"] = pool
    container = MagicMock(name=f"container:{name}")
    container.name = name
    container.short_id = f"{name}-id"
    container.status = status
    container.labels = labels
    container.attrs = {
        "Created": "2026-01-02T03:04:05Z",
        "State": {
            "Status": status,
            "Running": status == "running",
            "Paused": status == "paused",
            "Restarting": False,
            "Error": error,
        },
        "Config": {"Env": list(env)},
        "HostConfig": {
            "RestartPolicy": {"Name": policy},
            "Devices": None if devices is None else [
                {"PathOnHost": path, "PathInContainer": path, "CgroupPermissions": "rwm"} for path in devices
            ],
            "PortBindings": port_bindings,
        },
        "NetworkSettings": {"Ports": {}},
        "Mounts": list(mounts),
    }
    return container


@pytest.fixture
def installed(docker_client):
    """Register containers that the mocked Docker client knows by name."""
    by_name: dict[str, MagicMock] = {}

    def get(name):
        try:
            return by_name[name]
        except KeyError:
            raise NotFound(f"No such container: {name}") from None

    docker_client.containers.get.side_effect = get
    docker_client.containers.list.side_effect = lambda *args, **kwargs: list(by_name.values())

    def add(container: MagicMock) -> MagicMock:
        by_name[container.name] = container
        return container

    return add


def _jellyfin(pools, **kwargs) -> MagicMock:
    return _container(
        kwargs.pop("name", "jellyfin"),
        template="jellyfin",
        pool="alpha",
        mounts=[
            _bind(pools.alpha / "apps/jellyfin/config", "/config"),
            _bind(pools.alpha / "apps/jellyfin/cache", "/cache"),
            _bind(pools.alpha / "media", "/media", rw=False),
        ],
        **kwargs,
    )


def _make_jellyfin_folders(pools) -> None:
    for sub in ("apps/jellyfin/config", "apps/jellyfin/cache", "media"):
        (pools.alpha / sub).mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Folders for images that run as a non-root user
# ---------------------------------------------------------------------------

# Images that run as a fixed non-root user without fixing the permissions of
# their data folders as root first. Their writable pool folders must belong to
# that user: filebrowser's init script, for example, cannot copy its settings
# into a root-owned /config, exits, and Docker restarts it forever.
NON_ROOT_IMAGES = {
    "filebrowser/filebrowser": (1000, 1000),
    "grafana/grafana": (472, 0),
    "prom/prometheus": (65534, 65534),
    "nodered/node-red": (1000, 1000),
    "n8nio/n8n": (1000, 1000),
    "vikunja/vikunja": (1000, 1000),
}


def _image_name(image: str) -> str:
    name, sep, tag = image.rpartition(":")
    return name if sep and "/" not in tag else image


class TestDataFolderOwner:
    def test_catalog_gives_writable_folders_of_non_root_images_to_their_user(self):
        for template in APP_CATALOG:
            user = NON_ROOT_IMAGES.get(_image_name(template.image))
            for volume in template.volumes:
                # Never the pool root, and only folders the app writes to.
                owned = (
                    user is not None
                    and volume.on_pool
                    and not volume.readonly
                    and volume.host != POOL_PLACEHOLDER
                )
                expected = user if owned else (None, None)
                assert (volume.uid, volume.gid) == expected, (template.id, volume.container)

    def test_new_folders_belong_to_the_image_user(self, api, pools, docker_client, chown_calls):
        response = api.post(INSTALL, json={"template_id": "filebrowser"})

        assert response.status_code == 200, response.text
        assert sorted(chown_calls) == [
            (pools.alpha / "apps/filebrowser/config", 1000, 1000),
            (pools.alpha / "apps/filebrowser/database", 1000, 1000),
        ]
        # The parent folders and the pool root (/srv) stay owned by root.

    def test_existing_folders_keep_their_owner(self, api, pools, docker_client, chown_calls):
        (pools.alpha / "apps/filebrowser/config").mkdir(parents=True)

        response = api.post(INSTALL, json={"template_id": "filebrowser"})

        assert response.status_code == 200, response.text
        assert [call[0] for call in chown_calls] == [pools.alpha / "apps/filebrowser/database"]

    def test_images_that_start_as_root_get_root_owned_folders(
        self, api, pools, docker_client, chown_calls
    ):
        response = api.post(INSTALL, json={"template_id": "jellyfin"})

        assert response.status_code == 200, response.text
        assert chown_calls == []

    def test_failed_owner_change_fails_the_install(self, api, pools, docker_client, monkeypatch):
        def refuse(*args, **kwargs):
            raise PermissionError(1, "Operation not permitted")

        monkeypatch.setattr(os, "fchown", refuse)

        response = api.post(INSTALL, json={"template_id": "filebrowser"})

        assert response.status_code == 500
        assert "Operation not permitted" in response.json()["detail"]
        docker_client.containers.create.assert_not_called()


# ---------------------------------------------------------------------------
# Creating a source never follows a symbolic link
# ---------------------------------------------------------------------------


def _planned(pool_dir: Path, relative: str, *, is_file: bool = False) -> apps_router._PlannedMount:
    """A missing source on the app's own pool, owned by uid 1000 / gid 100 once created."""
    return apps_router._PlannedMount(
        source=pool_dir / relative,
        target="/data",
        readonly=False,
        is_file=is_file,
        pool=_pool(pool_dir),
        creatable=True,
        uid=1000,
        gid=100,
    )


class TestCreateSource:
    """_create_source runs after the checks; a link planted in between must not redirect it."""

    @pytest.mark.parametrize("is_file", [False, True], ids=["folder", "file"])
    def test_new_source_is_created_and_chowned_through_its_own_descriptor(
        self, pools, chown_calls, is_file
    ):
        apps_router._create_source(_planned(pools.alpha, "apps/x/config", is_file=is_file))

        created = pools.alpha / "apps/x/config"
        assert created.is_file() if is_file else created.is_dir()
        # A descriptor of the new file or folder itself (os.fchown), never a name.
        assert chown_calls == [(created, 1000, 100)]

    @pytest.mark.parametrize("is_file", [False, True], ids=["folder", "file"])
    def test_owner_change_never_reaches_what_replaced_the_new_source(self, pools, monkeypatch, is_file):
        # Whoever can write the folder (an app, an SMB share) can move the new
        # source away right after it was created and put a hard link to another
        # app's file under its name. An owner change by name would hand that
        # file to the app's user.
        folder = pools.alpha / "apps/x"
        folder.mkdir(parents=True)
        victim = pools.alpha / "apps/private.db"
        victim.write_bytes(b"another app's data")
        created: list[int] = []

        def swap() -> None:
            created.append((folder / "config").lstat().st_ino)
            (folder / "config").rename(folder / "config.moved")
            os.link(victim, folder / "config")

        real_open, real_mkdir = os.open, os.mkdir

        def open_and_swap(path, flags, mode=0o777, *, dir_fd=None):
            fd = real_open(path, flags, mode, dir_fd=dir_fd)
            if flags & os.O_CREAT:
                swap()
            return fd

        def mkdir_and_swap(path, mode=0o777, *, dir_fd=None):
            real_mkdir(path, mode, dir_fd=dir_fd)
            swap()

        changed: list[int] = []  # inodes whose owner was changed

        def chown(path, uid, gid, *, dir_fd=None, follow_symlinks=True):
            changed.append(os.stat(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks).st_ino)

        monkeypatch.setattr(os, "open", open_and_swap)
        monkeypatch.setattr(os, "mkdir", mkdir_and_swap)
        monkeypatch.setattr(os, "chown", chown)
        monkeypatch.setattr(os, "fchown", lambda fd, uid, gid: changed.append(os.fstat(fd).st_ino))

        if is_file:
            apps_router._create_source(_planned(pools.alpha, "apps/x/config", is_file=True))
            assert changed == created  # the file this call created, wherever it is now
        else:
            # The new folder is opened again to change its owner, and what is
            # there now is not a folder.
            with pytest.raises(HTTPException) as caught:
                apps_router._create_source(_planned(pools.alpha, "apps/x/config"))
            assert caught.value.status_code == 500
            assert changed == []
        assert (folder / "config").stat().st_ino == victim.stat().st_ino  # the swap happened

    def test_owner_change_never_follows_a_link_put_in_place_of_the_new_folder(
        self, pools, monkeypatch
    ):
        # Right after mkdir, the new folder is moved away and a symbolic link to
        # a folder outside the pool is put under its name. Opening the name again
        # to change the owner must not follow it: the app's user would get that folder.
        folder = pools.alpha / "apps/x"
        folder.mkdir(parents=True)
        real_mkdir = os.mkdir

        def mkdir_and_link(path, mode=0o777, *, dir_fd=None):
            real_mkdir(path, mode, dir_fd=dir_fd)
            (folder / "config").rename(folder / "config.moved")
            (folder / "config").symlink_to(pools.outside)

        changed: list[int] = []  # inodes whose owner was changed
        monkeypatch.setattr(os, "mkdir", mkdir_and_link)
        monkeypatch.setattr(os, "fchown", lambda fd, uid, gid: changed.append(os.fstat(fd).st_ino))

        with pytest.raises(HTTPException) as caught:
            apps_router._create_source(_planned(pools.alpha, "apps/x/config"))

        assert caught.value.status_code == 500
        assert changed == []
        assert (folder / "config").is_symlink()  # the swap happened

    @pytest.mark.parametrize("is_file", [False, True], ids=["folder", "file"])
    def test_existing_source_keeps_its_owner(self, pools, chown_calls, is_file):
        (pools.alpha / "apps").mkdir()
        existing = pools.alpha / "apps/config"
        if is_file:
            existing.write_bytes(b"data")
        else:
            existing.mkdir()

        apps_router._create_source(_planned(pools.alpha, "apps/config", is_file=is_file))

        assert chown_calls == []
        assert existing.read_bytes() == b"data" if is_file else existing.is_dir()

    @pytest.mark.parametrize("link", ["apps", "apps/x"])
    @pytest.mark.parametrize("is_file", [False, True], ids=["folder", "file"])
    def test_linked_parent_folder_is_not_followed(self, pools, chown_calls, link, is_file):
        (pools.alpha / link).parent.mkdir(exist_ok=True)
        (pools.alpha / link).symlink_to(pools.outside)

        with pytest.raises(HTTPException) as caught:
            apps_router._create_source(_planned(pools.alpha, "apps/x/config", is_file=is_file))

        assert caught.value.status_code == 409
        assert "symbolic link" in caught.value.detail
        assert list(pools.outside.iterdir()) == []
        assert chown_calls == []

    @pytest.mark.parametrize("case", ["created", "exists", "linked-parent", "chown-fails"])
    @pytest.mark.parametrize("is_file", [False, True], ids=["folder", "file"])
    def test_descriptors_are_closed(self, pools, monkeypatch, case, is_file):
        # Every start of an app can create sources: a leaked descriptor per
        # call would pile up in the long-running supervisor.
        if case == "exists":
            (pools.alpha / "apps/x").mkdir(parents=True)
            if is_file:
                (pools.alpha / "apps/x/config").write_bytes(b"")
            else:
                (pools.alpha / "apps/x/config").mkdir()
        elif case == "linked-parent":
            (pools.alpha / "apps").symlink_to(pools.outside)
        elif case == "chown-fails":
            def refuse(*args, **kwargs):
                raise PermissionError(1, "Operation not permitted")

            monkeypatch.setattr(os, "fchown", refuse)
        before = _open_fds()

        try:
            apps_router._create_source(_planned(pools.alpha, "apps/x/config", is_file=is_file))
        except HTTPException as e:
            assert case in ("linked-parent", "chown-fails"), e.detail
        else:
            assert case in ("created", "exists")

        assert _open_fds() - before == set()

    @pytest.mark.parametrize("is_file", [False, True], ids=["folder", "file"])
    def test_linked_source_is_not_followed(self, pools, chown_calls, is_file):
        (pools.alpha / "apps").mkdir()
        (pools.alpha / "apps/config").symlink_to(pools.outside / "config")

        apps_router._create_source(_planned(pools.alpha, "apps/config", is_file=is_file))

        assert list(pools.outside.iterdir()) == []
        assert chown_calls == []
        # The check after creating reports the link.
        with pytest.raises(HTTPException) as caught:
            apps_router._prepare_sources([_planned(pools.alpha, "apps/config", is_file=is_file)], create=True)
        assert caught.value.status_code == 409
        assert "symbolic link" in caught.value.detail


# ---------------------------------------------------------------------------
# No pool folder inside a folder an app can write to
# ---------------------------------------------------------------------------

# Templates that bind the pool root read-write. Allowed only for images that
# run as a non-root user: the pool root and the folders the supervisor creates
# are owned by root, so the app cannot rename them.
POOL_ROOT_WRITERS = {"filebrowser"}


def _pool_relative(volume: VolumeMapping) -> Path:
    return Path("/" + volume.host[len(POOL_PLACEHOLDER):].lstrip("/"))


class TestNestedFolders:
    def test_no_catalog_folder_lies_inside_a_writable_folder_of_any_app(self):
        """Docker re-resolves bind sources on every start and follows links.

        Apps share pool folders (downloads, media, ...). If transmission's
        /watch came from {pool}/downloads/watch, any app that can write to
        {pool}/downloads could replace "watch" with a link to "/", and the
        host's root directory would be mounted at /watch at the next start.
        """
        volumes = [(t, v) for t in APP_CATALOG for v in t.volumes if v.on_pool]
        for outer_template, outer in volumes:
            if outer.readonly:
                continue
            if outer.host == POOL_PLACEHOLDER:
                assert outer_template.id in POOL_ROOT_WRITERS, outer_template.id
                assert _image_name(outer_template.image) in NON_ROOT_IMAGES, outer_template.id
                continue
            outer_path = _pool_relative(outer)
            for inner_template, inner in volumes:
                inner_path = _pool_relative(inner)
                assert inner_path == outer_path or not inner_path.is_relative_to(outer_path), (
                    f"{inner_template.id} {inner.container} ({inner.host}) is inside "
                    f"{outer_template.id} {outer.container} ({outer.host})"
                )

    def test_override_inside_a_writable_folder_of_the_app_is_rejected(
        self, api, pools, docker_client
    ):
        before = _tree(pools.root)

        response = api.post(
            INSTALL,
            json={
                **TRANSMISSION,
                "volume_overrides": {"/watch": str(pools.alpha / "downloads/watch")},
            },
        )

        assert response.status_code == 400
        assert "inside the folder for /downloads" in response.json()["detail"]
        docker_client.images.pull.assert_not_called()
        assert _tree(pools.root) == before

    def test_writable_folder_cannot_be_moved_to_the_pool_root(self, api, pools, docker_client):
        # Syncthing's /data on the pool root would contain its own /config.
        response = api.post(
            INSTALL,
            json={"template_id": "syncthing", "volume_overrides": {"/data": str(pools.alpha)}},
        )

        assert response.status_code == 400
        assert "inside the folder for /data" in response.json()["detail"]
        docker_client.images.pull.assert_not_called()

    @pytest.mark.parametrize("app_id", ["filebrowser", "duplicati"])
    def test_templates_built_for_the_pool_root_install(self, api, pools, docker_client, app_id):
        response = api.post(INSTALL, json={"template_id": app_id})

        assert response.status_code == 200, response.text
        sources = {m["Source"] for m in _mounts(docker_client).values()}
        assert str(pools.alpha) in sources

    def test_symlink_planted_after_resolution_is_refused(self, api, pools, docker_client, monkeypatch):
        # A link that appears between planning and creating (a race) is caught
        # by the walk that does not follow links.
        real_plan = apps_router._plan_volumes

        def plan_then_plant(*args, **kwargs):
            planned = real_plan(*args, **kwargs)
            (pools.alpha / "apps").mkdir(exist_ok=True)
            (pools.alpha / "apps/transmission").symlink_to(pools.outside)
            return planned

        monkeypatch.setattr(apps_router, "_plan_volumes", plan_then_plant)

        response = api.post(INSTALL, json={**TRANSMISSION})

        assert response.status_code == 409
        assert "symbolic link" in response.json()["detail"]
        assert list(pools.outside.iterdir()) == []
        docker_client.images.pull.assert_not_called()


# ---------------------------------------------------------------------------
# Running in the group unos-smb, and the folders of shares (ADR-0274)
# ---------------------------------------------------------------------------

LOGIN = TRANSMISSION["env_overrides"]
IN_UNOS_SMB = {**TRANSMISSION, "run_in_unos_smb": True}
# linuxserver.io apps whose every pool folder is their own data under {pool}/apps,
# which is never a share's folder: the group would give them nothing.
ONLY_OWN_DATA = (
    "prowlarr", "overseerr", "tautulli", "nextcloud", "bookstack", "wikijs", "heimdall", "wireguard", "code-server",
)


def _set_by_unos(names: str) -> str:
    return (
        f"{names} cannot be changed: an app runs with the PUID and PGID of its template, or with "
        "PUID 80000 and PGID 70000 (unos-smb) when it is installed to run in the group unos-smb"
    )


def _environment(docker_client) -> dict[str, str]:
    """The environment Docker was given for the new container."""
    return docker_client.containers.create.call_args.kwargs["environment"]


class TestRunInUnosSmb:
    def test_linuxserver_apps_with_a_folder_besides_their_own_data_can_run_in_the_group(self):
        offered = {t.id for t in APP_CATALOG if t.can_run_in_unos_smb}
        for template in APP_CATALOG:
            if template.id in offered:
                assert template.source is AppSource.LINUXSERVER, template.id
                assert {"PUID", "PGID"} <= {var.name for var in template.environment}, template.id
                assert any(
                    volume.on_pool and not volume.host.startswith(f"{POOL_PLACEHOLDER}/apps/")
                    for volume in template.volumes
                ), template.id
        assert {"transmission", "syncthing", "duplicati", "emby", "sonarr", "calibre-web"} <= offered
        for app_id in ONLY_OWN_DATA:
            template = get_app_by_id(app_id)
            assert template.source is AppSource.LINUXSERVER, app_id
            assert not template.can_run_in_unos_smb, app_id
        # Their templates declare PGID, but jellyfin's image runs as root and
        # Plex's takes PLEX_GID: PGID would change nothing.
        for app_id in ("jellyfin", "plex"):
            template = get_app_by_id(app_id)
            assert "PGID" in {var.name for var in template.environment}
            assert not template.can_run_in_unos_smb

    def test_the_catalog_says_which_apps_can_run_in_the_group(self, api):
        assert api.get("/api/v1/apps/catalog/transmission").json()["can_run_in_unos_smb"] is True
        assert api.get("/api/v1/apps/catalog/jellyfin").json()["can_run_in_unos_smb"] is False
        listed = {t["id"]: t["can_run_in_unos_smb"] for t in api.get("/api/v1/apps/catalog").json()}
        assert listed["syncthing"] is True and listed["filebrowser"] is False and listed["prowlarr"] is False

    def test_the_option_runs_the_app_as_user_80000_in_the_group_with_umask_007(self, api, pools, docker_client):
        response = api.post(INSTALL, json=IN_UNOS_SMB)

        assert response.status_code == 200, response.text
        environment = _environment(docker_client)
        # 80000 is no other app's user: apps outside the group run as 1000,
        # and a share's folder that the image gives to its user (chown) must
        # not become theirs. 007 gives new entries 2770 and 0660, as Samba does.
        assert (environment["PUID"], environment["PGID"], environment["UMASK"]) == ("80000", "70000", "007")
        assert (environment["USER"], environment["PASS"]) == ("e2e", "k7-Unique-pass")

    def test_no_catalog_app_runs_as_the_user_of_the_group(self):
        for template in APP_CATALOG:
            assert all(volume.uid != 80000 for volume in template.volumes), template.id
            assert all(var.value.strip() != "80000" for var in template.environment), template.id

    def test_without_the_option_the_app_stays_outside_the_group(self, api, pools, docker_client):
        response = api.post(INSTALL, json=TRANSMISSION)

        assert response.status_code == 200, response.text
        environment = _environment(docker_client)
        assert (environment["PUID"], environment["PGID"]) == ("1000", "1000")
        assert "UMASK" not in environment

    @pytest.mark.parametrize("run_in_unos_smb", [False, True])
    @pytest.mark.parametrize(
        "overrides,names",
        [
            # 70000 would put the app into the group without the option.
            ({"PGID": "70000"}, "PGID"),
            ({"PGID": " 70000"}, "PGID"),
            ({"PGID": "1000"}, "PGID"),
            ({"PGID": "0"}, "PGID"),
            # 0 runs the image's user as root, 7xxxx as an SMB account, 80000
            # as the apps in the group.
            ({"PUID": "0"}, "PUID"),
            ({"PUID": "70001"}, "PUID"),
            ({"PUID": "80000"}, "PUID"),
            ({"PUID": "1000"}, "PUID"),
            ({"PUID": "0", "PGID": "70000"}, "PUID and PGID"),
        ],
    )
    def test_puid_and_pgid_are_not_environment_settings(
        self, api, pools, docker_client, run_in_unos_smb, overrides, names
    ):
        response = api.post(
            INSTALL,
            json={
                **TRANSMISSION,
                "run_in_unos_smb": run_in_unos_smb,
                "env_overrides": {**LOGIN, **overrides},
            },
        )

        assert response.status_code == 400
        assert response.json()["detail"] == _set_by_unos(names)
        docker_client.images.pull.assert_not_called()

    @pytest.mark.parametrize(
        "app_id,name", [("jellyfin", "Jellyfin"), ("plex", "Plex Media Server"), ("filebrowser", "File Browser")]
    )
    def test_other_images_cannot_run_in_the_group(self, api, pools, docker_client, app_id, name):
        before = _tree(pools.root)

        response = api.post(INSTALL, json={"template_id": app_id, "run_in_unos_smb": True})

        assert response.status_code == 400
        assert response.json()["detail"] == (
            f"{name} cannot run in the group unos-smb: only linuxserver.io images take the user and "
            "group of their processes from PUID and PGID"
        )
        docker_client.images.pull.assert_not_called()
        assert _tree(pools.root) == before

    @pytest.mark.parametrize("app_id", ONLY_OWN_DATA)
    def test_apps_with_only_their_own_data_cannot_run_in_the_group(self, api, pools, docker_client, app_id):
        template = get_app_by_id(app_id)
        before = _tree(pools.root)

        response = api.post(INSTALL, json={"template_id": app_id, "run_in_unos_smb": True})

        assert response.status_code == 400
        assert response.json()["detail"] == (
            f"{template.name} cannot run in the group unos-smb: its folders on the pool hold only its "
            "own data (apps/), and none of them can be a share's folder"
        )
        docker_client.images.pull.assert_not_called()
        assert _tree(pools.root) == before


def _needs_the_group(name: str) -> str:
    return (
        "Only an app that runs in the group unos-smb gets a share's files: install "
        f"{name} to run in it, or choose a folder that holds no share."
    )


class TestShareFolders:
    @pytest.fixture
    def family(self, pools, shares) -> Path:
        """The folder of the share family, on the pool alpha."""
        folder = pools.alpha / "family"
        folder.mkdir()
        shares("family", folder)
        return folder

    @pytest.mark.parametrize(
        "app_id,target,env,needs",
        [
            ("transmission", "/downloads", LOGIN, _needs_the_group("Transmission")),
            # Jellyfin's image runs as root: it would read the folder whatever its mode.
            (
                "jellyfin",
                "/media",
                {},
                "Jellyfin cannot run in the group unos-smb, so it gets no share's files: choose a "
                "folder that holds no share.",
            ),
        ],
    )
    def test_a_share_folder_is_refused_without_the_option(
        self, api, pools, docker_client, family, app_id, target, env, needs
    ):
        before = _tree(pools.root)

        response = api.post(
            INSTALL,
            json={"template_id": app_id, "env_overrides": env, "volume_overrides": {target: str(family)}},
        )

        assert response.status_code == 400
        assert response.json()["detail"] == (
            f"The folder for {target} ({family}) is the folder of the share family ({family}). {needs}"
        )
        docker_client.images.pull.assert_not_called()
        assert _tree(pools.root) == before

    def test_an_app_in_the_group_gets_the_share_folder(self, api, pools, docker_client, family):
        response = api.post(INSTALL, json={**IN_UNOS_SMB, "volume_overrides": {"/downloads": str(family)}})

        assert response.status_code == 200, response.text
        assert _mounts(docker_client)["/downloads"] == {
            "Target": "/downloads", "Source": str(family), "Type": "bind", "ReadOnly": False,
        }
        assert _environment(docker_client)["PGID"] == "70000"
        assert {"host": str(family), "container": "/downloads", "readonly": False} in response.json()["volumes"]

    def test_a_shared_default_folder_needs_the_option(self, api, pools, docker_client, shares):
        # Folders that apps use can be shared; an app installed on one afterwards needs the group.
        downloads = pools.alpha / "downloads"
        downloads.mkdir()
        shares("Downloads", downloads)

        refused = api.post(INSTALL, json=TRANSMISSION)

        assert refused.status_code == 400
        assert refused.json()["detail"] == (
            f"The folder for /downloads ({downloads}) is the folder of the share Downloads ({downloads}). "
            + _needs_the_group("Transmission")
        )
        docker_client.images.pull.assert_not_called()

        allowed = api.post(INSTALL, json=IN_UNOS_SMB)

        assert allowed.status_code == 200, allowed.text
        assert _mounts(docker_client)["/downloads"]["Source"] == str(downloads)

    @pytest.mark.parametrize("run_in_unos_smb", [False, True])
    def test_a_folder_inside_a_share_is_refused(self, api, pools, docker_client, family, run_in_unos_smb):
        torrents = family / "torrents"
        torrents.mkdir()
        before = _tree(pools.root)

        response = api.post(
            INSTALL,
            json={**TRANSMISSION, "run_in_unos_smb": run_in_unos_smb,
                  "volume_overrides": {"/downloads": str(torrents)}},
        )

        assert response.status_code == 400
        assert response.json()["detail"] == (
            f"The folder for /downloads ({torrents}) is inside the folder of the share family ({family}). "
            "An app gets a share's whole folder or none of it: whoever can write to the share, such as an "
            "app given its folder, can put a symbolic link in the place of a folder inside it, and Docker "
            "would follow that link at the next start."
        )
        docker_client.images.pull.assert_not_called()
        assert _tree(pools.root) == before

    @pytest.mark.parametrize("run_in_unos_smb", [False, True])
    def test_a_writable_folder_that_holds_a_share_is_refused(
        self, api, pools, docker_client, shares, run_in_unos_smb
    ):
        media = pools.alpha / "media"
        movies = media / "movies"
        movies.mkdir(parents=True)
        shares("movies", movies)

        response = api.post(
            INSTALL,
            json={**TRANSMISSION, "run_in_unos_smb": run_in_unos_smb,
                  "volume_overrides": {"/downloads": str(media)}},
        )

        assert response.status_code == 400
        where = f"The folder for /downloads ({media}) holds the folder of the share movies ({movies})"
        assert response.json()["detail"] == (
            f"{where}, and Transmission can write to it: the app could put another folder in the "
            "share's place. Give the app the share's folder itself."
            if run_in_unos_smb
            else f"{where}. " + _needs_the_group("Transmission")
        )
        docker_client.images.pull.assert_not_called()

    def test_a_read_only_folder_that_holds_shares_needs_the_option(self, api, pools, docker_client, family):
        # Duplicati backs up the whole pool, mounted read-only at /source.
        refused = api.post(INSTALL, json={"template_id": "duplicati"})

        assert refused.status_code == 400
        assert refused.json()["detail"] == (
            f"The folder for /source ({pools.alpha}) holds the folder of the share family ({family}). "
            + _needs_the_group("Duplicati")
        )
        docker_client.images.pull.assert_not_called()

        allowed = api.post(INSTALL, json={"template_id": "duplicati", "run_in_unos_smb": True})

        assert allowed.status_code == 200, allowed.text
        assert _mounts(docker_client)["/source"] == {
            "Target": "/source", "Source": str(pools.alpha), "Type": "bind", "ReadOnly": True,
        }

    def test_an_app_outside_the_group_gets_no_pool_that_holds_shares(self, api, pools, docker_client, family):
        response = api.post(INSTALL, json={"template_id": "filebrowser"})

        assert response.status_code == 400
        assert response.json()["detail"] == (
            f"The folder for /srv ({pools.alpha}) holds the folder of the share family ({family}). "
            "File Browser cannot run in the group unos-smb, so it gets no share's files: choose a "
            "folder that holds no share."
        )
        docker_client.images.pull.assert_not_called()

    def test_the_apps_own_data_never_goes_into_a_share_folder(self, api, pools, docker_client, family):
        response = api.post(INSTALL, json={**IN_UNOS_SMB, "volume_overrides": {"/config": str(family)}})

        assert response.status_code == 400
        assert response.json()["detail"] == (
            f"/config holds Transmission's own data, which must not go into the folder of the share "
            f"family ({family}): SMB users would read and change the app's settings. Choose a folder "
            "that is not shared."
        )
        docker_client.images.pull.assert_not_called()

    @pytest.mark.parametrize(
        "stored",
        [
            # A leading "//" stays in os.path.normpath and in pathlib; ".."
            # stays in pathlib. Neither names another folder.
            "/{alpha}/x/../downloads",
            "{alpha}//downloads/",
        ],
    )
    def test_a_share_path_stored_in_another_form_still_counts(self, api, pools, docker_client, shares, stored):
        # Restored databases are untrusted (ADR-0144): the path may not be in normal form.
        downloads = pools.alpha / "downloads"
        downloads.mkdir()
        shares("Downloads", stored.format(alpha=pools.alpha))

        response = api.post(INSTALL, json=TRANSMISSION)

        assert response.status_code == 400
        assert response.json()["detail"] == (
            f"The folder for /downloads ({downloads}) is the folder of the share Downloads ({downloads}). "
            + _needs_the_group("Transmission")
        )
        docker_client.images.pull.assert_not_called()

    @pytest.mark.parametrize(
        "folder,name",
        [
            # Read-only snapshots of the pool and of a volume keep the files
            # of their shares as they were.
            (".snapshots/daily/family", ".snapshots"),
            (".snapshots", ".snapshots"),
            ("volume/.snapshots/daily", ".snapshots"),
            # The trash keeps what was deleted, from shares too (ADR-0259).
            (".unos-trash", ".unos-trash"),
            ("volume/.unos-trash/20261108T000000Z-0123456789abcdef/item", ".unos-trash"),
        ],
    )
    @pytest.mark.parametrize(
        "request_body,target",
        [
            # Jellyfin's image runs as root: it reads every copy whatever its mode.
            ({"template_id": "jellyfin"}, "/media"),
            (IN_UNOS_SMB, "/downloads"),
        ],
    )
    def test_snapshots_and_the_trash_are_refused(
        self, api, pools, docker_client, family, folder, name, request_body, target
    ):
        source = pools.alpha / folder
        source.mkdir(parents=True)
        before = _tree(pools.root)

        response = api.post(INSTALL, json={**request_body, "volume_overrides": {target: str(source)}})

        assert response.status_code == 400
        what = "a snapshot folder (.snapshots)" if name == ".snapshots" else "the trash (.unos-trash)"
        assert response.json()["detail"] == (
            f"The folder for {target} ({source}) is in {what}, which keeps files of shares outside "
            f"their folders: no app gets a snapshot or the trash. Choose a folder outside {name}."
        )
        docker_client.images.pull.assert_not_called()
        assert _tree(pools.root) == before

    def test_snapshots_are_refused_also_where_no_share_is(self, api, pools, docker_client):
        # A snapshot can hold the files of a share that was deleted or moved since.
        snapshot = pools.beta / ".snapshots/daily"
        snapshot.mkdir(parents=True)

        response = api.post(INSTALL, json={"template_id": "jellyfin", "volume_overrides": {"/media": str(snapshot)}})

        assert response.status_code == 400
        assert response.json()["detail"].startswith(
            f"The folder for /media ({snapshot}) is in a snapshot folder (.snapshots)"
        )
        docker_client.images.pull.assert_not_called()

    def test_a_share_of_a_whole_pool_counts_for_nothing(self, api, pools, docker_client, shares):
        # Imported from an old smb.conf: never served (ADR-0145), and no app would install on the pool.
        shares("data", pools.alpha)
        shares("everything", pools.base)

        response = api.post(INSTALL, json=TRANSMISSION)

        assert response.status_code == 200, response.text
        assert _mounts(docker_client)["/downloads"]["Source"] == str(pools.alpha / "downloads")

    def test_folders_beside_shares_and_shares_of_other_pools_change_nothing(
        self, api, pools, docker_client, shares
    ):
        (pools.alpha / "downloads2").mkdir()
        shares("downloads2", pools.alpha / "downloads2")
        (pools.beta / "downloads").mkdir()
        shares("beta", pools.beta / "downloads")
        shares("broken", "not/a/folder")

        response = api.post(INSTALL, json=TRANSMISSION)

        assert response.status_code == 200, response.text
        assert _mounts(docker_client)["/downloads"]["Source"] == str(pools.alpha / "downloads")
        assert _environment(docker_client)["PGID"] == "1000"


# ---------------------------------------------------------------------------
# Starting an installed app re-checks its folders
# ---------------------------------------------------------------------------


class TestStartChecks:
    def test_app_with_usable_folders_starts(self, api, pools, installed):
        _make_jellyfin_folders(pools)
        container = installed(_jellyfin(pools))

        response = api.post(START.format("jellyfin"))

        assert response.status_code == 200, response.text
        container.start.assert_called_once_with()

    def test_restart_with_usable_folders(self, api, pools, installed):
        _make_jellyfin_folders(pools)
        container = installed(_jellyfin(pools, status="running"))

        response = api.post(RESTART.format("jellyfin"))

        assert response.status_code == 200, response.text
        container.restart.assert_called_once_with()

    def test_missing_folders_on_the_apps_pool_are_created_again(self, api, pools, installed):
        # Docker's old -v binds recreated a deleted folder; Mount binds do not.
        (pools.alpha / "apps/jellyfin/config").mkdir(parents=True)
        container = installed(_jellyfin(pools))

        response = api.post(START.format("jellyfin"))

        assert response.status_code == 200, response.text
        assert (pools.alpha / "apps/jellyfin/cache").is_dir()
        assert (pools.alpha / "media").is_dir()
        container.start.assert_called_once_with()

    def test_recreated_folder_belongs_to_the_image_user(self, api, pools, installed, chown_calls):
        (pools.alpha / "apps/filebrowser/database").mkdir(parents=True)
        container = installed(
            _container(
                "filebrowser",
                template="filebrowser",
                pool="alpha",
                mounts=[
                    _bind(pools.alpha / "apps/filebrowser/database", "/database"),
                    _bind(pools.alpha / "apps/filebrowser/config", "/config"),
                    _bind(pools.alpha, "/srv"),
                ],
            )
        )

        response = api.post(START.format("filebrowser"))

        assert response.status_code == 200, response.text
        assert chown_calls == [(pools.alpha / "apps/filebrowser/config", 1000, 1000)]
        container.start.assert_called_once_with()

    @pytest.mark.parametrize("link", ["media", "apps"], ids=["source", "parent-folder"])
    def test_symlink_on_the_way_to_a_bind_source_is_refused(self, api, pools, installed, link):
        # What an app that can write the parent folder could plant, e.g.
        # "ln -s / downloads/watch": Docker would mount the link's target.
        (pools.outside / "jellyfin/config").mkdir(parents=True)
        (pools.outside / "jellyfin/cache").mkdir(parents=True)
        if link == "media":
            (pools.alpha / "apps/jellyfin/config").mkdir(parents=True)
            (pools.alpha / "apps/jellyfin/cache").mkdir(parents=True)
            (pools.alpha / "media").symlink_to(pools.outside)
        else:
            (pools.alpha / "media").mkdir()
            (pools.alpha / "apps").symlink_to(pools.outside)
        container = installed(_jellyfin(pools))
        before = _tree(pools.root)

        response = api.post(START.format("jellyfin"))

        assert response.status_code == 409
        assert "symbolic link" in response.json()["detail"]
        assert str(pools.alpha / link) in response.json()["detail"]
        container.start.assert_not_called()
        assert _tree(pools.root) == before

    def test_app_on_an_unmounted_pool_is_not_started(self, api, pools, installed):
        container = installed(_jellyfin(pools))
        pools.mounted.discard(str(pools.alpha))
        before = _tree(pools.root)

        response = api.post(START.format("jellyfin"))

        assert response.status_code == 409
        detail = response.json()["detail"]
        assert "not on a mounted storage pool" in detail
        # The answer says which pool to mount.
        assert "Mount the pool ('alpha' holds the app's data)" in detail
        container.start.assert_not_called()
        assert _tree(pools.root) == before

    def test_restart_is_refused_like_a_start(self, api, pools, installed):
        container = installed(_jellyfin(pools, status="running"))
        pools.mounted.discard(str(pools.alpha))

        response = api.post(RESTART.format("jellyfin"))

        assert response.status_code == 409
        assert "Mount the pool ('alpha' holds the app's data)" in response.json()["detail"]
        container.restart.assert_not_called()

    def test_the_answer_names_the_pool_that_holds_the_folder(self, api, pools, installed):
        """A folder on another pool than the one the app was installed on, such as a share's folder
        (ADR-0274): the 409 names the pool to mount (apps.no-legacy-default-pool)."""
        (pools.alpha / "apps/jellyfin/config").mkdir(parents=True)
        (pools.beta / "media").mkdir()
        container = installed(
            _container(
                "jellyfin",
                template="jellyfin",
                pool="alpha",
                mounts=[
                    _bind(pools.alpha / "apps/jellyfin/config", "/config"),
                    _bind(pools.beta / "media", "/media", rw=False),
                ],
            )
        )
        pools.mounted.discard(str(pools.beta))

        response = api.post(START.format("jellyfin"))

        assert response.status_code == 409
        assert "Mount the pool ('beta' holds the app's data)" in response.json()["detail"]
        container.start.assert_not_called()

    def test_data_under_a_pools_base_reached_through_a_link_is_checked(self, api, pools, installed, tmp_path,
                                                                       monkeypatch):
        """An install gives Docker the pool sources under the resolved pools base (validate_pools_path):
        with the pools base set to a path through a symbolic link, they are still data, listed as not
        on a mounted pool and refused at the start while their pool is not mounted."""
        link = tmp_path / "pools-link"
        link.symlink_to(pools.base)
        monkeypatch.setattr(get_settings(), "pools_base_path", str(link))
        pools.mounted.clear()
        pools.mounted.add(str(link / "beta"))
        container = installed(_jellyfin(pools))

        [app_info] = api.get(INSTALLED).json()
        response = api.post(START.format("jellyfin"))

        assert sorted(app_info["data_outside_pools"]) == [
            str(pools.alpha / "apps/jellyfin/cache"), str(pools.alpha / "apps/jellyfin/config"), str(pools.alpha / "media"),
        ]
        assert response.status_code == 409
        assert "Mount the pool ('alpha' holds the app's data)" in response.json()["detail"]
        container.start.assert_not_called()

    def test_no_code_for_apps_of_older_versions(self):
        """apps.no-legacy-default-pool (ADR-0207): no check, message or text for apps that older
        versions installed under /mnt/pools/default without a pool label."""
        repo = Path(__file__).resolve().parents[2]
        for path in ("supervisor/src/apps/router.py", "supervisor/src/apps/schemas.py",
                     "supervisor/src/health/router.py", "webui/src/lib/api/apps.ts",
                     "webui/src/lib/components/apps/AppStorageWarning.svelte",
                     "webui/src/lib/i18n/catalog/apps.ts", "webui/tests/apps.spec.ts"):
            text = (repo / path).read_text(encoding="utf-8")
            for old in ("older version", "older install", "older UNOS version", "pools/default", "systemDisk",
                        "'default'", "системном диске"):
                assert old not in text, f"{path}: {old}"

    def test_missing_folder_on_another_pool_is_not_created(self, api, pools, installed):
        (pools.alpha / "apps/jellyfin/config").mkdir(parents=True)
        container = installed(
            _container(
                "jellyfin",
                template="jellyfin",
                pool="alpha",
                mounts=[
                    _bind(pools.alpha / "apps/jellyfin/config", "/config"),
                    _bind(pools.beta / "library", "/media", rw=False),
                ],
            )
        )
        before = _tree(pools.root)

        response = api.post(START.format("jellyfin"))

        assert response.status_code == 409
        assert "does not exist" in response.json()["detail"]
        container.start.assert_not_called()
        assert _tree(pools.root) == before

    def test_fixed_host_paths_are_not_checked(self, api, pools, installed):
        (pools.alpha / "apps/portainer/data").mkdir(parents=True)
        container = installed(
            _container(
                "portainer",
                template="portainer",
                pool="alpha",
                mounts=[
                    _bind(pools.alpha / "apps/portainer/data", "/data"),
                    _bind("/var/run/docker.sock", "/var/run/docker.sock"),
                ],
            )
        )

        response = api.post(START.format("portainer"))

        assert response.status_code == 200, response.text
        container.start.assert_called_once_with()

    def test_unknown_app_is_not_found(self, api, pools, installed):
        assert api.post(START.format("nope")).status_code == 404

    def test_docker_error_is_reported(self, api, pools, installed):
        _make_jellyfin_folders(pools)
        container = installed(_jellyfin(pools))
        container.start.side_effect = APIError("port is already allocated")

        response = api.post(START.format("jellyfin"))

        assert response.status_code == 500
        assert "port is already allocated" in response.json()["detail"]


# ---------------------------------------------------------------------------
# The same checks for every code path that starts an app
# ---------------------------------------------------------------------------


def _inspected(container: MagicMock) -> Container:
    """docker-py's Container built from raw ``docker inspect`` data.

    Code that talks to the low-level API (the restart of apps Docker could not
    start before their pool was mounted) has only this data.
    """
    details = dict(container.attrs, Name=f"/{container.name}", Config={"Labels": container.labels})
    return Container(attrs=details)


class TestPrepareAppStart:
    def test_accepts_raw_inspect_data_and_recreates_folders(self, pools):
        (pools.alpha / "apps/jellyfin/config").mkdir(parents=True)

        apps_router.prepare_app_start(_inspected(_jellyfin(pools)))

        assert (pools.alpha / "apps/jellyfin/cache").is_dir()
        assert (pools.alpha / "media").is_dir()

    def test_refuses_a_planted_link(self, pools):
        (pools.alpha / "apps/jellyfin/config").mkdir(parents=True)
        (pools.alpha / "apps/jellyfin/cache").mkdir(parents=True)
        (pools.alpha / "media").symlink_to(pools.outside)

        with pytest.raises(HTTPException) as caught:
            apps_router.prepare_app_start(_inspected(_jellyfin(pools)))

        assert caught.value.status_code == 409
        assert "symbolic link" in caught.value.detail

    def test_refuses_an_unmounted_pool(self, pools):
        pools.mounted.discard(str(pools.alpha))
        before = _tree(pools.root)

        with pytest.raises(HTTPException) as caught:
            apps_router.prepare_app_start(_inspected(_jellyfin(pools)))

        assert caught.value.status_code == 409
        assert "not on a mounted storage pool" in caught.value.detail
        assert _tree(pools.root) == before


# ---------------------------------------------------------------------------
# Installed apps report the folders they really use
# ---------------------------------------------------------------------------


class TestInstalledApps:
    def test_real_host_folders_are_reported(self, api, pools, installed):
        installed(
            _container(
                "portainer",
                template="portainer",
                pool="alpha",
                status="running",
                mounts=[
                    _bind("/var/run/docker.sock", "/var/run/docker.sock"),
                    _bind(pools.alpha / "apps/portainer/data", "/data"),
                ],
            )
        )

        response = api.get(INSTALLED)

        assert response.status_code == 200, response.text
        [app_info] = response.json()
        assert app_info["pool"] == "alpha"
        assert app_info["volumes"] == [
            {"host": str(pools.alpha / "apps/portainer/data"), "container": "/data", "readonly": False},
            {"host": "/var/run/docker.sock", "container": "/var/run/docker.sock", "readonly": False},
        ]
        assert app_info["data_outside_pools"] == []

    def test_app_on_an_unmounted_pool_is_flagged(self, api, pools, installed):
        installed(_jellyfin(pools))
        pools.mounted.discard(str(pools.alpha))

        [app_info] = api.get(INSTALLED).json()

        assert app_info["pool"] == "alpha"
        assert len(app_info["data_outside_pools"]) == 3

    def test_the_list_says_which_apps_run_in_the_group(self, api, pools, installed):
        # The page cannot show the template's PGID as the one an app runs with
        # (ADR-0274): the container's environment says it.
        installed(
            _container(
                "transmission",
                template="transmission",
                pool="alpha",
                env=["PUID=80000", "PGID=70000", "UMASK=007", *(f"{name}={LOGIN[name]}" for name in LOGIN)],
            )
        )
        installed(_container("syncthing", template="syncthing", pool="alpha", env=["PUID=1000", "PGID=1000"]))
        installed(_container("dozzle", template="dozzle"))
        no_environment = installed(_container("jellyfin", template="jellyfin", pool="alpha"))
        no_environment.attrs["Config"] = {"Env": None}

        response = api.get(INSTALLED)

        assert response.status_code == 200, response.text
        assert {app_info["name"]: app_info["runs_in_unos_smb"] for app_info in response.json()} == {
            "transmission": True,
            "syncthing": False,
            "dozzle": False,
            "jellyfin": False,
        }
        # Only that: the list shows no value of the environment.
        assert LOGIN["PASS"] not in response.text


# ---------------------------------------------------------------------------
# Stopping and uninstalling installed apps
# ---------------------------------------------------------------------------


def _on_event_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def _webui() -> MagicMock:
    """The web UI's own container: no app of the catalog (no unos.app label)."""
    return _container("unos-webui", status="running")


def _stops(container: MagicMock) -> MagicMock:
    """Let ``container.stop()`` change the state Docker reports after a reload."""
    container.stop.side_effect = lambda *args, **kwargs: setattr(container, "status", "exited")
    return container


# Every action of the Apps API on an installed app: (action, method, path template).
APP_ACTIONS = [
    ("start", "POST", START),
    ("restart", "POST", RESTART),
    ("stop", "POST", STOP),
    ("uninstall", "DELETE", UNINSTALL),
]


# ---------------------------------------------------------------------------
# A Docker that does not answer (apps.docker-down-503)
# ---------------------------------------------------------------------------

# What docker.from_env raises when the Docker socket is missing, as on the local stand-in.
NO_SOCKET = (
    "Error while fetching server API version: ('Connection aborted.', "
    "FileNotFoundError(2, 'No such file or directory'))"
)


def _no_socket(**kwargs):
    import docker

    raise docker.errors.DockerException(NO_SOCKET)


class TestADockerThatDoesNotAnswer:
    """Each route of the Apps API that asks Docker answers 503 with the reason when Docker does not
    answer; an error that Docker answers keeps its own status."""

    @pytest.mark.parametrize(
        "method,path",
        [("GET", INSTALLED), ("POST", INSTALL), *[(method, path.format("jellyfin")) for _, method, path in APP_ACTIONS]],
    )
    def test_without_a_socket(self, api, pools, monkeypatch, method, path):
        import docker

        monkeypatch.setattr(docker, "from_env", _no_socket)

        response = api.request(method, path, json={"template_id": "jellyfin"} if path == INSTALL else None)

        assert (response.status_code, response.json()) == (503, {"detail": f"Docker is not available: {NO_SOCKET}"})

    def test_a_lookup_whose_connection_breaks_answers_503(self, api, pools, installed, docker_client):
        error = requests.exceptions.ConnectionError("('Connection aborted.', ConnectionResetError(104, 'reset'))")
        docker_client.containers.get.side_effect = error

        response = api.post(STOP.format("jellyfin"))

        assert (response.status_code, response.json()) == (503, {"detail": f"Docker is not available: {error}"})

    @pytest.mark.parametrize(
        "error",
        [
            requests.exceptions.ConnectionError("('Connection aborted.', ConnectionResetError(104, 'reset'))"),
            # A pull is a stream: docker-py passes on urllib3's error of a broken one.
            urllib3.exceptions.ProtocolError("Connection broken: IncompleteRead(0 bytes read, 12 more expected)"),
        ],
        ids=["connection", "stream"],
    )
    def test_a_pull_that_breaks_off_keeps_the_500_of_the_operation(self, api, pools, docker_client, error):
        # Docker answered the name check: the operation that fails after it keeps its own answer
        # (ADR-0254), also when the connection broke during it.
        docker_client.images.pull.side_effect = error

        response = api.post(INSTALL, json={"template_id": "jellyfin"})

        assert (response.status_code, response.json()) == (500, {"detail": f"Failed to pull image: {error}"})
        docker_client.containers.create.assert_not_called()

    def test_a_pull_that_docker_refuses_keeps_its_status(self, api, pools, docker_client):
        docker_client.images.pull.side_effect = APIError("404 Client Error", explanation="manifest unknown")

        response = api.post(INSTALL, json={"template_id": "jellyfin"})

        assert response.status_code == 500
        assert response.json()["detail"].startswith("Failed to pull image: ")



class TestStopAndUninstall:
    def test_stop_stops_the_app_and_answers_its_new_state(self, api, pools, installed):
        container = installed(_stops(_jellyfin(pools, status="running")))

        response = api.post(STOP.format("jellyfin"))

        assert (response.status_code, response.json()) == (200, {"status": "exited", "id": "jellyfin"})
        container.stop.assert_called_once_with()
        container.remove.assert_not_called()

    def test_uninstall_removes_the_container_and_keeps_the_apps_data(self, api, pools, installed):
        _make_jellyfin_folders(pools)
        (pools.alpha / "apps/jellyfin/config/library.db").write_text("the app's database")
        container = installed(_stops(_jellyfin(pools, status="running")))
        before = _tree(pools.root)

        response = api.delete(UNINSTALL.format("jellyfin"))

        assert (response.status_code, response.json()) == (200, {"status": "removed", "id": "jellyfin"})
        container.stop.assert_called_once_with()
        # Without v=True: Docker keeps the container's volumes too.
        container.remove.assert_called_once_with()
        assert _tree(pools.root) == before
        assert (pools.alpha / "apps/jellyfin/config/library.db").read_text() == "the app's database"

    @pytest.mark.parametrize("action,method,path", APP_ACTIONS, ids=[a for a, _, _ in APP_ACTIONS])
    def test_unknown_app_is_not_found(self, api, pools, installed, action, method, path):
        response = api.request(method, path.format("nope"))

        assert (response.status_code, response.json()) == (404, {"detail": "App nope not found"})

    @pytest.mark.parametrize("action,method,path", APP_ACTIONS, ids=[a for a, _, _ in APP_ACTIONS])
    def test_a_container_that_is_no_app_is_left_alone(self, api, pools, installed, action, method, path):
        # The Apps page acts only on apps: stopping UNOS's own containers this way
        # would lock the administrator out.
        container = installed(_webui())

        response = api.request(method, path.format("unos-webui"))

        assert (response.status_code, response.json()) == (400, {"detail": "Not a UNOS managed app"})
        for call in (container.start, container.restart, container.stop, container.remove, container.kill):
            call.assert_not_called()

    def test_a_docker_error_of_the_stop_is_reported(self, api, pools, installed):
        container = installed(_jellyfin(pools, status="running"))
        container.stop.side_effect = APIError("cannot stop container: permission denied")

        response = api.post(STOP.format("jellyfin"))

        assert response.status_code == 500
        assert response.json()["detail"].startswith("Failed to stop: ")
        assert "permission denied" in response.json()["detail"]

    def test_a_docker_error_of_the_removal_is_reported(self, api, pools, installed):
        container = installed(_stops(_jellyfin(pools, status="running")))
        container.remove.side_effect = APIError(
            'driver "overlay2" failed to remove root filesystem: device or resource busy'
        )

        response = api.delete(UNINSTALL.format("jellyfin"))

        assert response.status_code == 500
        assert response.json()["detail"].startswith("Failed to remove: ")
        assert "device or resource busy" in response.json()["detail"]

    @pytest.mark.parametrize("action,method,path", APP_ACTIONS, ids=[a for a, _, _ in APP_ACTIONS])
    def test_docker_is_asked_in_a_worker_thread(self, api, pools, installed, docker_client, monkeypatch,
                                                action, method, path):
        _make_jellyfin_folders(pools)
        installed(_stops(_jellyfin(pools, status="running")))
        made: list[bool] = []

        def get_client():
            made.append(_on_event_loop())
            return docker_client

        monkeypatch.setattr(apps_router, "get_client", get_client)

        response = api.request(method, path.format("jellyfin"))

        assert response.status_code == 200, response.text
        assert made == [False]


# ---------------------------------------------------------------------------
# The hardware of the host's SoC (apps.rk3588-hardware-acceleration, ADR-0377)
# ---------------------------------------------------------------------------

# The device tree of the dev device, a Banana Pi BPI-W3 (ADR-0376), and of other boards.
RK3588 = "rockchip,rk3588-armsom-w3\0rockchip,rk3588\0"
RK3568 = "rockchip,rk3568-evb1-v10\0rockchip,rk3568\0"
SPACEMIT_K1 = "spacemit,k1-x\0"
# What Jellyfin's RKMPP needs of the RK3588 (Jellyfin's Rockchip guide): the DRM nodes, the
# DMA heaps, the video codecs (MPP), the 2D engine (RGA) and the Mali GPU (OpenCL).
RK3588_VIDEO = ["/dev/dri", "/dev/dma_heap", "/dev/mpp_service", "/dev/rga", "/dev/mali0"]
# The paths that Docker 29 masks in every container that is not privileged (moby's
# defaultLinuxMaskedPaths; the per-CPU thermal_throttle folders exist only on x86).
DOCKER_MASKED_PATHS = [
    "/proc/acpi", "/proc/asound", "/proc/interrupts", "/proc/kcore", "/proc/keys", "/proc/latency_stats",
    "/proc/sched_debug", "/proc/scsi", "/proc/timer_list", "/proc/timer_stats",
    "/sys/devices/virtual/powercap", "/sys/firmware",
]
CREATED_ID = "c0ffee0123456789"


@pytest.fixture
def low_level(docker_client):
    """docker-py's low-level API, through which a container with MaskedPaths of its own is made:
    its host config as docker-py builds it, and the new container found by its id."""
    container = docker_client.containers.create.return_value
    docker_client.api.create_host_config.side_effect = lambda **kwargs: HostConfig(version="1.47", **kwargs)
    docker_client.api.create_container.return_value = {"Id": CREATED_ID, "Warnings": []}

    def get(key):
        if key == CREATED_ID:
            return container
        raise NotFound(f"No such container: {key}")

    docker_client.containers.get.side_effect = get
    return docker_client.api


def _host_config(docker_client) -> dict:
    return docker_client.api.create_container.call_args.kwargs["host_config"]


class TestHardwareOfTheSoc:
    def test_on_an_rk3588_jellyfin_gets_the_video_hardware_without_privileges(
        self, api, pools, docker_client, low_level, soc
    ):
        soc.compatible = RK3588
        soc.devices = set(RK3588_VIDEO)

        response = api.post(INSTALL, json={"template_id": "jellyfin"})

        assert response.status_code == 200, response.text
        docker_client.containers.create.assert_not_called()
        image = docker_client.api.create_container.call_args.args
        create = docker_client.api.create_container.call_args.kwargs
        config = _host_config(docker_client)
        assert image == ("jellyfin/jellyfin:latest",)
        # Each node, or folder of nodes, as the same path in the container, read and write.
        assert config["Devices"] == [
            {"PathOnHost": path, "PathInContainer": path, "CgroupPermissions": "rwm"} for path in RK3588_VIDEO
        ]
        # Rockchip's MPP names the SoC from /proc/device-tree/compatible, a link into /sys/firmware:
        # only that mask of Docker's is lifted, and Docker's AppArmor profile, which denies reading
        # /sys/firmware, is not applied. The container stays unprivileged, without capabilities
        # beyond Docker's own.
        assert config["MaskedPaths"] == [path for path in DOCKER_MASKED_PATHS if path != "/sys/firmware"]
        assert "ReadonlyPaths" not in config
        assert config["SecurityOpt"] == ["apparmor=unconfined"]
        assert "Privileged" not in config  # docker-py sets it only when true
        assert "CapAdd" not in config
        # The rest is the container of every host.
        assert config["PortBindings"] == {"8096/tcp": [{"HostIp": "", "HostPort": "8096"}]}
        assert {m["Target"]: m["Source"] for m in config["Mounts"]} == {
            "/config": str(pools.alpha / "apps/jellyfin/config"),
            "/cache": str(pools.alpha / "apps/jellyfin/cache"),
            "/media": str(pools.alpha / "media"),
        }
        assert config["RestartPolicy"] == {"Name": "unless-stopped"}
        assert config["LogConfig"] == {"Type": "json-file", "Config": {"max-size": "10m", "max-file": "3"}}
        assert create["name"] == "jellyfin"
        assert create["ports"] == [("8096", "tcp")]
        assert create["environment"] == {"PUID": "1000", "PGID": "1000", "TZ": "UTC"}
        assert create["labels"]["unos.app"] == "jellyfin"
        assert create["labels"]["unos.app.pool"] == "alpha"
        docker_client.containers.create.return_value.start.assert_called_once()
        assert response.json()["container_id"] == "c0ffee"
        # The answer names the nodes the container got.
        assert response.json()["devices"] == RK3588_VIDEO

    def test_only_the_nodes_that_the_host_has_are_passed_in(self, api, pools, docker_client, low_level, soc):
        # Under a kernel without Rockchip's Mali driver (panthor) there is no /dev/mali0, and
        # without its RGA driver no /dev/rga.
        soc.compatible = RK3588
        soc.devices = {"/dev/mpp_service", "/dev/dri", "/dev/dma_heap"}

        response = api.post(INSTALL, json={"template_id": "jellyfin"})

        assert response.status_code == 200, response.text
        assert [d["PathOnHost"] for d in _host_config(docker_client)["Devices"]] == [
            "/dev/dri", "/dev/dma_heap", "/dev/mpp_service",
        ]
        # The host was asked about the profile's nodes, in one command.
        assert _hardware_reads(soc)[1][3:] == ["unos-devices", *RK3588_VIDEO]

    @pytest.mark.parametrize("compatible", [None, RK3568, SPACEMIT_K1], ids=["pc", "rk3568", "spacemit-k1"])
    def test_off_an_rk3588_jellyfin_is_installed_as_before(self, api, pools, docker_client, soc, compatible):
        soc.compatible = compatible
        soc.devices = set(RK3588_VIDEO)

        response = api.post(INSTALL, json={"template_id": "jellyfin"})

        assert response.status_code == 200, response.text
        docker_client.api.create_container.assert_not_called()
        kwargs = docker_client.containers.create.call_args.kwargs
        assert kwargs.get("devices") is None and kwargs.get("security_opt") is None
        assert kwargs["privileged"] is False and kwargs["cap_add"] is None
        assert response.json()["devices"] == []
        # Only the device tree was read; no node was looked for.
        reads = _hardware_reads(soc)
        assert len(reads) == 1 and _reads_device_tree(reads[0])

    def test_an_rk3588_without_any_of_the_nodes_installs_jellyfin_as_elsewhere(self, api, pools, docker_client, soc):
        soc.compatible = RK3588

        response = api.post(INSTALL, json={"template_id": "jellyfin"})

        assert response.status_code == 200, response.text
        docker_client.api.create_container.assert_not_called()
        assert docker_client.containers.create.call_args.kwargs.get("devices") is None

    @pytest.mark.parametrize("devices", [
        # Under the mainline kernel: rockchip-drm and panthor make /dev/dri, the kernel the DMA heaps.
        {"/dev/dri", "/dev/dma_heap"},
        # Every node but Rockchip MPP's.
        {"/dev/dri", "/dev/dma_heap", "/dev/rga", "/dev/mali0"},
    ], ids=["mainline-kernel", "all-but-mpp"])
    def test_an_rk3588_without_rockchip_mpp_installs_jellyfin_as_elsewhere(
        self, api, pools, docker_client, soc, devices
    ):
        # Without /dev/mpp_service RKMPP cannot work: no node is passed in, no mask is lifted and
        # Docker's AppArmor profile stays.
        soc.compatible = RK3588
        soc.devices = devices

        response = api.post(INSTALL, json={"template_id": "jellyfin"})

        assert response.status_code == 200, response.text
        docker_client.api.create_container.assert_not_called()
        kwargs = docker_client.containers.create.call_args.kwargs
        assert kwargs.get("devices") is None and kwargs.get("security_opt") is None
        assert response.json()["devices"] == []

    @pytest.mark.parametrize(
        "error", [subprocess.TimeoutExpired(["nsenter"], 15), FileNotFoundError(2, "nsenter")], ids=["timeout", "oserror"]
    )
    def test_a_device_tree_that_cannot_be_read_installs_jellyfin_as_elsewhere(
        self, api, pools, docker_client, soc, error
    ):
        soc.fail = error

        response = api.post(INSTALL, json={"template_id": "jellyfin"})

        assert response.status_code == 200, response.text
        docker_client.api.create_container.assert_not_called()
        assert docker_client.containers.create.call_args.kwargs.get("devices") is None
        assert response.json()["devices"] == []

    def test_a_device_tree_read_that_fails_is_logged(self, api, pools, docker_client, soc, caplog):
        # nsenter exits non-zero when it cannot enter the host: the board counts as one without a
        # device tree, and the log says why.
        soc.answer = HostResult(1, "", "nsenter: reassociate to namespace 'ns/mnt' failed: Operation not permitted")

        with caplog.at_level("WARNING", logger="src.apps.hardware"):
            response = api.post(INSTALL, json={"template_id": "jellyfin"})

        assert response.status_code == 200, response.text
        assert docker_client.containers.create.call_args.kwargs.get("devices") is None
        assert [r.getMessage() for r in caplog.records if r.name == "src.apps.hardware"] == [
            "Could not read the host's device tree (exit status 1): "
            "nsenter: reassociate to namespace 'ns/mnt' failed: Operation not permitted"
        ]

    # The look for the device nodes, the second host command, failing after a device tree that
    # names the RK3588: nsenter exits non-zero, or the look runs past its timeout. With the
    # message that each logs.
    NODE_LOOKUP_FAILURES = [
        pytest.param(
            HostResult(1, "", "nsenter: reassociate to namespace 'ns/mnt' failed: Operation not permitted"),
            "Could not look for the device nodes of the host's SoC: "
            "nsenter: reassociate to namespace 'ns/mnt' failed: Operation not permitted",
            id="exit-status",
        ),
        pytest.param(
            subprocess.TimeoutExpired(["nsenter"], 15),
            "Could not look for the device nodes of the host's SoC: "
            "Command '['nsenter']' timed out after 15 seconds",
            id="timeout",
        ),
    ]

    @pytest.mark.parametrize(("lookup", "message"), NODE_LOOKUP_FAILURES)
    def test_an_rk3588_whose_nodes_cannot_be_looked_for_installs_jellyfin_as_elsewhere(
        self, api, pools, docker_client, low_level, soc, caplog, lookup, message
    ):
        # The host cannot say which nodes it has: none counts as present, so no profile applies,
        # and the log says why.
        soc.compatible = RK3588
        soc.devices = set(RK3588_VIDEO)
        soc.lookup = lookup

        with caplog.at_level("WARNING", logger="src.apps.hardware"):
            response = api.post(INSTALL, json={"template_id": "jellyfin"})

        assert response.status_code == 200, response.text
        docker_client.api.create_container.assert_not_called()
        kwargs = docker_client.containers.create.call_args.kwargs
        assert kwargs.get("devices") is None and kwargs.get("security_opt") is None
        assert response.json()["devices"] == []
        assert _hardware_reads(soc)[1][3:] == ["unos-devices", *RK3588_VIDEO]
        assert [r.getMessage() for r in caplog.records if r.name == "src.apps.hardware"] == [message]

    def test_a_host_without_a_device_tree_is_not_logged(self, api, soc, caplog):
        with caplog.at_level("DEBUG", logger="src.apps.hardware"):
            assert api.get("/api/v1/apps/catalog/jellyfin").json()["hardware"] == []

        assert [r.getMessage() for r in caplog.records if r.name == "src.apps.hardware"] == []

    def test_the_device_tree_script_tells_a_missing_device_tree_from_one_it_reads(self, tmp_path):
        # The script as the host's sh runs it: exit 3 without the file, its bytes with it.
        compatible = tmp_path / "compatible"
        script = hardware_module.DEVICE_TREE_SCRIPT
        missing = subprocess.run(["/bin/sh", "-c", script, "unos-device-tree", str(compatible)], capture_output=True)
        compatible.write_bytes(RK3588.encode())
        found = subprocess.run(["/bin/sh", "-c", script, "unos-device-tree", str(compatible)], capture_output=True)

        assert (missing.returncode, missing.stdout, missing.stderr) == (3, b"", b"")
        assert (found.returncode, found.stdout) == (0, RK3588.encode())

    def test_an_app_without_hardware_reads_nothing_of_the_host(self, api, pools, docker_client, soc):
        soc.compatible = RK3588
        soc.devices = set(RK3588_VIDEO)

        response = api.post(INSTALL, json={"template_id": "dozzle"})

        assert response.status_code == 200, response.text
        # Only the host ports that the app would take were read (ADR-0381).
        assert soc.commands == [SS]
        assert docker_client.containers.create.call_args.kwargs.get("devices") is None

    def test_a_failed_start_on_an_rk3588_removes_the_container(self, api, pools, docker_client, low_level, soc):
        soc.compatible = RK3588
        soc.devices = set(RK3588_VIDEO)
        container = docker_client.containers.create.return_value
        container.start.side_effect = APIError("error gathering device information while adding custom device")

        response = api.post(INSTALL, json={"template_id": "jellyfin"})

        assert response.status_code == 500
        assert response.json()["detail"].startswith("Failed to start container: ")
        container.remove.assert_called_once_with(force=True)

    def test_a_create_that_docker_refuses_on_an_rk3588_answers_500(self, api, pools, docker_client, low_level, soc):
        soc.compatible = RK3588
        soc.devices = set(RK3588_VIDEO)
        low_level.create_container.side_effect = APIError("400 Client Error", explanation="invalid masked path")

        response = api.post(INSTALL, json={"template_id": "jellyfin"})

        assert response.status_code == 500
        assert response.json()["detail"].startswith("Failed to create container: ")

    def test_a_new_container_that_docker_cannot_find_is_removed(self, api, pools, docker_client, low_level, soc):
        soc.compatible = RK3588
        soc.devices = set(RK3588_VIDEO)
        docker_client.containers.get.side_effect = NotFound("No such container")

        response = api.post(INSTALL, json={"template_id": "jellyfin"})

        assert response.status_code == 500
        assert response.json()["detail"].startswith("Failed to create container: ")
        low_level.remove_container.assert_called_once_with(CREATED_ID, force=True)

    def test_the_catalog_names_the_hardware_only_on_its_soc(self, api, soc):
        soc.compatible = RK3588
        soc.devices = set(RK3588_VIDEO)

        listed = {t["id"]: [p["id"] for p in t["hardware"]] for t in api.get("/api/v1/apps/catalog").json()}
        one = api.get("/api/v1/apps/catalog/jellyfin").json()["hardware"]
        found = api.get("/api/v1/apps/catalog", params={"query": "jelly"}).json()

        assert {app_id: ids for app_id, ids in listed.items() if ids} == {"jellyfin": ["rk3588-video"]}
        assert one == [{
            "id": "rk3588-video",
            "soc": "rockchip,rk3588",
            "devices": RK3588_VIDEO,
            "requires": ["/dev/mpp_service"],
            "unmasked_paths": ["/sys/firmware"],
            "apparmor_unconfined": True,
        }]
        assert [p["id"] for p in found[0]["hardware"]] == ["rk3588-video"]

    @pytest.mark.parametrize("compatible", [None, RK3568, SPACEMIT_K1], ids=["pc", "rk3568", "spacemit-k1"])
    def test_off_its_soc_the_catalog_names_no_hardware(self, api, soc, compatible):
        soc.compatible = compatible
        soc.devices = set(RK3588_VIDEO)

        assert not [t["id"] for t in api.get("/api/v1/apps/catalog").json() if t["hardware"]]
        assert api.get("/api/v1/apps/catalog/jellyfin").json()["hardware"] == []
        assert api.get("/api/v1/apps/catalog", params={"category": "media"}).json()[0]["hardware"] == []
        # Off the SoC no node is looked for.
        assert all(_reads_device_tree(cmd) for cmd in soc.commands)

    def test_on_an_rk3588_without_rockchip_mpp_the_catalog_names_no_hardware(self, api, soc):
        # The App Store offers no hardware acceleration that RKMPP cannot give (the mainline kernel).
        soc.compatible = RK3588
        soc.devices = {"/dev/dri", "/dev/dma_heap", "/dev/rga", "/dev/mali0"}

        assert not [t["id"] for t in api.get("/api/v1/apps/catalog").json() if t["hardware"]]
        assert api.get("/api/v1/apps/catalog/jellyfin").json()["hardware"] == []

    @pytest.mark.parametrize(("lookup", "message"), NODE_LOOKUP_FAILURES)
    def test_on_an_rk3588_whose_nodes_cannot_be_looked_for_the_catalog_names_no_hardware(
        self, api, soc, caplog, lookup, message
    ):
        soc.compatible = RK3588
        soc.devices = set(RK3588_VIDEO)
        soc.lookup = lookup

        with caplog.at_level("WARNING", logger="src.apps.hardware"):
            assert not [t["id"] for t in api.get("/api/v1/apps/catalog").json() if t["hardware"]]
            assert api.get("/api/v1/apps/catalog/jellyfin").json()["hardware"] == []

        # One look for the nodes, and one warning, for each request.
        assert [r.getMessage() for r in caplog.records if r.name == "src.apps.hardware"] == [message, message]

    def test_the_catalog_reads_the_host_off_the_event_loop(self, api, soc, monkeypatch):
        soc.compatible = RK3588
        soc.devices = set(RK3588_VIDEO)
        seen: list[bool] = []
        real = host_module.run_on_host

        def run(cmd, **kwargs):
            seen.append(_on_event_loop())
            return real(cmd, **kwargs)

        monkeypatch.setattr(host_module, "run_on_host", run)

        assert api.get("/api/v1/apps/catalog").status_code == 200
        assert api.get("/api/v1/apps/catalog/jellyfin").status_code == 200
        # The device tree and the nodes, for each request.
        assert seen == [False, False, False, False]

    def test_the_installed_list_names_the_nodes_each_container_got(self, api, pools, installed):
        # The App Store tells a Jellyfin installed without the hardware from one that has it.
        installed(_jellyfin(pools, devices=RK3588_VIDEO))
        installed(_jellyfin(pools, name="jellyfin-plain"))
        installed(_jellyfin(pools, name="jellyfin-none", devices=[]))

        response = api.get(INSTALLED)

        assert response.status_code == 200, response.text
        assert {app_info["name"]: app_info["devices"] for app_info in response.json()} == {
            "jellyfin": RK3588_VIDEO,
            "jellyfin-plain": [],
            "jellyfin-none": [],
        }

    def test_every_profile_names_device_nodes_and_masks_of_docker(self):
        profiles = [p for t in APP_CATALOG for p in t.hardware]
        assert profiles
        for profile in profiles:
            assert all(path.startswith("/dev/") for path in profile.devices), profile.id
            # A profile applies only with the nodes it requires, which the container gets.
            assert profile.requires and set(profile.requires) <= set(profile.devices), profile.id
            assert set(profile.unmasked_paths) <= set(DOCKER_MASKED_PATHS), profile.id
        for template in APP_CATALOG:
            if template.hardware:
                # The device cgroup and Docker's other limits stay as they are: no privileges.
                assert not template.privileged and not template.capabilities, template.id
