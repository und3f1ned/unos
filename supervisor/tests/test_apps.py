"""App installs keep their data on a mounted storage pool.

A folder of the pools base that is no mounted pool is a plain folder on the
system disk: an install or a start that wrote there would fill it.

Nothing here touches the real host: a temporary directory is the pools base,
``os.path.ismount`` decides which of its directories count as mounted pools,
the Docker client is a MagicMock, and ``os.fchown`` only records its calls.
"""

import asyncio
import fcntl
import hashlib
import importlib
import json
import os
import re
import shutil
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
    APPS_DATA_PATH,
    COMPANION_NAME,
    POOL_PLACEHOLDER,
    AppCategory,
    AppPool,
    AppSource,
    AppTemplate,
    Companion,
    HardwareProfile,
    PortMapping,
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
catalog_module = importlib.import_module("src.apps.catalog")
preflight_module = importlib.import_module("src.apps.preflight")
stacks_module = importlib.import_module("src.apps.stacks")
audit_router = importlib.import_module("src.audit.router")
install_jobs = importlib.import_module("src.apps.install_jobs")

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


class _Ended:
    """The end of an install job as the answer of its request: 200 with the install's answer, or
    the status and detail of the failure (ADR-0384)."""

    def __init__(self, status_code: int, body: dict) -> None:
        self.status_code = status_code
        self._body = body
        self.text = repr(body)

    def json(self) -> dict:
        return self._body


def install(client: TestClient, body: dict):
    """POST /apps/install and, when it starts a job (202), the job's end as an answer (_Ended):
    the checks, pulls, creates and starts run in the job (ADR-0384). An answer other than 202,
    such as a 400 for a request that the template refuses, is returned as it came."""
    response = client.post(INSTALL, json=body)
    if response.status_code != 202:
        return response
    job_id = response.json()["id"]
    ended = client.get(f"{INSTALL}/jobs/{job_id}", params={"wait": 30})
    assert ended.status_code == 200, ended.text
    job = ended.json()
    assert job["state"] != "running", job
    if job["state"] == "succeeded":
        return _Ended(200, job["result"])
    return _Ended(job["error_status"], {"detail": job["error"]})


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


def pull_answer(*lines: dict) -> SimpleNamespace:
    """What Docker answers a pull (POST /images/create, streamed): 200 and ``lines``."""
    return SimpleNamespace(
        status_code=200, reason="OK", iter_lines=lambda: iter([json.dumps(line).encode() for line in lines]),
        close=lambda: None, raw=SimpleNamespace(shutdown=lambda: None),
    )


def pulled(docker_client) -> list[str]:
    """The images that the install asked Docker to pull, as name:tag (or name@digest)."""
    refs = []
    for call in docker_client.api.post.call_args_list:
        assert call.args[0].endswith("/images/create") and call.kwargs["stream"] is True, call
        params = call.kwargs["params"]
        refs.append(f"{params['fromImage']}{'@' if params['tag'].startswith('sha256:') else ':'}{params['tag']}")
    return refs


@pytest.fixture
def docker_client(monkeypatch):
    """Docker without containers; ``containers.create`` makes a running container "c0ffee"; a pull
    answers an empty stream."""
    client = MagicMock(name="docker")
    client.api.post.return_value = pull_answer()

    def image(key):
        # Docker lacks every image until a pull (every image of the catalog is pinned and looked
        # up by digest first, ADR-0410); a pulled one declares no VOLUME.
        if not client.api.post.called:
            raise NotFound(f"No such image: {key}")
        return MagicMock(name="image", attrs={"Config": {"Volumes": None}})

    client.images.get.side_effect = image
    client.containers.get.side_effect = NotFound("No such container")
    client.containers.list.return_value = []
    client.info.return_value = {"DockerRootDir": DOCKER_ROOT}
    container = MagicMock(name="container", short_id="c0ffee", status="running")
    container.attrs = {"State": {"Status": "running", "Running": True}}
    client.containers.create.return_value = container
    monkeypatch.setattr(apps_router, "get_client", lambda: client)
    return client


# What the host of the hardware reads answers (src/apps/hardware.py, ADR-0382). The device tree
# is read by a shell that exits 3 where the host has none (NO_DEVICE_TREE).
DEVICE_TREE = "/proc/device-tree/compatible"
NO_DEVICE_TREE = HostResult(3, "", "")


def _reads_device_tree(cmd: list[str]) -> bool:
    return cmd[:2] == ["/bin/sh", "-c"] and cmd[3:] == ["unos-device-tree", DEVICE_TREE]


# The host's listening TCP and bound UDP sockets with their programs, which every install of an
# app that publishes a port reads before the pull (src/apps/preflight.py, ADR-0381).
SS = ["ss", "-H", "-l", "-n", "-t", "-u", "-p"]
# Docker's root folder, as Docker's info names it, and the containerd image store's: every install
# reads the free space of their file systems before the pull (ADR-0381).
DOCKER_ROOT = "/var/lib/docker"
CONTAINERD_ROOT = "/var/lib/containerd"
GIB = 1024**3
# The read of the free space exits with this status where the host has none of the folders.
NO_FREE_SPACE_FOLDER = 3
# src/host.py's own run_on_host, which the fixture ``soc`` replaces: tests of the scripts that
# the host runs put it back and run them on this machine (UNOS_HOST_EXEC=local).
REAL_RUN_ON_HOST = host_module.run_on_host


def _reads_free_space(cmd: list[str]) -> bool:
    return cmd[:2] == ["/bin/sh", "-c"] and cmd[3:4] == ["unos-free-space"]


def _free_space_read(root: str = DOCKER_ROOT) -> list[str]:
    """The read of the free space of the system disk, for Docker's root folder ``root``."""
    return ["/bin/sh", "-c", preflight_module.FREE_SPACE_SCRIPT, "unos-free-space", root, CONTAINERD_ROOT]


def _df(free: dict[str, int], paths: list[str]) -> str:
    """What df -P -k prints for those of ``paths`` that are in ``free`` (their free bytes)."""
    lines = []
    for path in paths:
        if path in free:
            lines += [
                "Filesystem     1024-blocks      Used Available Capacity Mounted on",
                f"/dev/mmcblk1p2   {free[path] // 1024 + 2048} 2048 {free[path] // 1024} 50% /",
            ]
    return "".join(f"{line}\n" for line in lines)


def _hardware_reads(soc) -> list[list[str]]:
    """The host commands of ``soc`` but the reads of the ports in use and of the free space: the
    reads of the hardware."""
    return [cmd for cmd in soc.commands if cmd != SS and not _reads_free_space(cmd)]


@pytest.fixture(autouse=True)
def soc(monkeypatch):
    """The host for the reads of its SoC and device nodes: no device tree, as on a PC, until a
    test gives it a ``compatible`` (the bytes of /proc/device-tree/compatible) and ``devices``
    (the device nodes it has). ``commands`` records every host command; one that the fake does
    not know fails the test. ``tree`` (a HostResult, or an exception to raise) answers only the
    read of the device tree, as when nsenter cannot enter the host. ``lookup`` (a HostResult, or
    an exception to raise) answers only the look for the device nodes, after a device tree it
    read. ``sockets`` are the lines that ``ss`` prints for the host's listening sockets (none by
    default), and ``ss`` (a HostResult, or an exception to raise) answers only that read.
    ``free`` gives the free bytes of the file systems of the folders that exist (Docker's root
    folder and the containerd image store, 64 GiB each by default); where none of them exists the
    read exits NO_FREE_SPACE_FOLDER, as its script does. ``df`` (a HostResult, or an exception to
    raise) answers only the read of the free space."""
    fake = SimpleNamespace(
        compatible=None, devices=set(), commands=[], tree=None, lookup=None, sockets=[], ss=None,
        free={DOCKER_ROOT: 64 * GIB, CONTAINERD_ROOT: 64 * GIB}, df=None,
    )

    def run(cmd, **kwargs):
        fake.commands.append(list(cmd))
        if _reads_free_space(cmd):
            if isinstance(fake.df, BaseException):
                raise fake.df
            if fake.df is not None:
                return fake.df
            if not any(path in fake.free for path in cmd[4:]):
                return HostResult(NO_FREE_SPACE_FOLDER, "", "")
            return HostResult(0, _df(fake.free, cmd[4:]), "")
        if cmd == SS:
            if isinstance(fake.ss, BaseException):
                raise fake.ss
            if fake.ss is not None:
                return fake.ss
            return HostResult(0, "".join(f"{line}\n" for line in fake.sockets), "")
        if _reads_device_tree(cmd):
            if isinstance(fake.tree, BaseException):
                raise fake.tree
            if fake.tree is not None:
                return fake.tree
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
        response = install(api, {"template_id": "jellyfin"})

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["pool"] == "alpha"
        # All of the main container's (ADR-0381).
        assert body["volumes"] == [
            {"host": str(pools.alpha / "apps/jellyfin/config"), "container": "/config", "readonly": False,
             "companion": None},
            {"host": str(pools.alpha / "apps/jellyfin/cache"), "container": "/cache", "readonly": False,
             "companion": None},
            {"host": str(pools.alpha / "media"), "container": "/media", "readonly": True, "companion": None},
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
        response = install(api, {"template_id": "jellyfin", "pool": "beta"})

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

        response = install(api, {**TRANSMISSION})

        assert response.status_code == 409
        assert response.json()["detail"] == (
            "No mounted storage pool — create or mount a pool first"
        )
        docker_client.api.post.assert_not_called()
        assert _tree(pools.root) == before

    @pytest.mark.parametrize("pool", ["ghost", "nope", "../alpha", ""])
    def test_unknown_or_unmounted_pool_is_rejected(self, api, pools, docker_client, pool):
        before = _tree(pools.root)

        response = install(api, {**TRANSMISSION, "pool": pool})

        assert response.status_code == 400
        assert "not mounted" in response.json()["detail"]
        docker_client.api.post.assert_not_called()
        assert _tree(pools.root) == before

    def test_app_without_pool_data_installs_without_a_pool(self, api, pools, docker_client):
        pools.mounted.clear()

        response = install(api, {"template_id": "dozzle"})

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
        bad = install(api, {"template_id": "dozzle", "pool": "ghost"})
        assert bad.status_code == 400

        good = install(api, {"template_id": "dozzle", "pool": "beta"})
        assert good.status_code == 200, good.text
        assert good.json()["pool"] is None
        assert "unos.app.pool" not in docker_client.containers.create.call_args.kwargs["labels"]

    def test_install_requires_admin(self, user_api, pools, docker_client):
        response = user_api.post(INSTALL, json={"template_id": "jellyfin"})

        assert response.status_code == 403
        docker_client.api.post.assert_not_called()


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
    # An app of several containers (ADR-0381): a companion's folders are "<companion>:<path>".
    # The password file is bound into the server and the database, beside the database's folder.
    "immich": {
        "/data": "apps/immich/library",
        "/etc/localtime": FIXED,
        "/run/secrets/db-password": "apps/immich/postgres/password",
        "database:/var/lib/postgresql/data": "apps/immich/postgres/data",
        "database:/run/secrets/db-password": "apps/immich/postgres/password",
        "redis:/data": "apps/immich/redis",
        "machine-learning:/cache": "apps/immich/model-cache",
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
        volumes = {v.container: v for v in template.volumes}
        for companion in template.companions:
            planned += apps_router._plan_volumes(
                template, {}, pool, [pool], spec=companion, container=f"{app_id}-{companion.name}",
                companion=companion.name,
            )
            volumes |= {f"{companion.name}:{v.container}": v for v in companion.volumes}

        sources = {m.target if m.companion is None else f"{m.companion}:{m.target}": m for m in planned}
        assert len(sources) == len(planned)
        assert set(sources) == set(EXPECTED_PATHS[app_id])
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
            for spec in (template, *template.companions):
                targets = [v.container for v in spec.volumes]
                assert len(targets) == len(set(targets)), template.id
                for volume in spec.volumes:
                    assert "/mnt/pools" not in volume.host, template.id
                    assert volume.on_pool or volume.host.startswith("/"), template.id
                    assert ".." not in volume.host.split("/"), template.id

    def test_every_template_keeps_pool_data_inside_the_pool(self, pool_dir):
        pool = _pool(pool_dir)
        for template in APP_CATALOG:
            planned = [
                m
                for spec in (template, *template.companions)
                for m in apps_router._plan_volumes(template, {}, pool, [pool], spec=spec)
            ]
            for m in planned:
                if m.pool is not None:
                    assert m.source.is_relative_to(pool_dir), (template.id, m)
                else:
                    assert not str(m.source).startswith(POOL_PLACEHOLDER), template.id

    def test_catalog_ships_no_placeholder_secrets(self):
        for template in APP_CATALOG:
            for var in (var for spec in (template, *template.companions) for var in spec.environment):
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
             "readonly": False, "is_file": False, "uid": None, "gid": None, "generated_secret": False,
             "secret_digits": 64, "secret_for": None, "copy_before_update": False, "on_pool": True},
            {"host": "/var/run/docker.sock", "container": "/var/run/docker.sock",
             "readonly": False, "is_file": False, "uid": None, "gid": None, "generated_secret": False,
             "secret_digits": 64, "secret_for": None, "copy_before_update": False, "on_pool": False},
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
        response = install(api, {"template_id": "netdata"})

        assert response.status_code == 200, response.text
        assert mkdir_calls, "expected the app directories to be created"
        assert all(path.is_relative_to(pools.alpha) for path in mkdir_calls), mkdir_calls
        new_paths = _tree(pools.root) - before
        assert new_paths and all(path.is_relative_to(pools.alpha) for path in new_paths)
        mounts = _mounts(docker_client)
        assert mounts["/host/proc"]["Source"] == "/proc"
        assert mounts["/host/etc/passwd"]["Source"] == "/etc/passwd"

    def test_fixed_host_paths_are_never_created(self, api, pools, docker_client, mkdir_calls):
        response = install(api, {"template_id": "portainer"})

        assert response.status_code == 200, response.text
        assert mkdir_calls == [
            pools.alpha / "apps",
            pools.alpha / "apps/portainer",
            pools.alpha / "apps/portainer/data",
        ]
        assert _mounts(docker_client)["/var/run/docker.sock"]["Source"] == "/var/run/docker.sock"

    def test_pool_unmounted_during_pull_writes_nothing(self, api, pools, docker_client):
        # The pool goes while Docker's pull stream is read: an empty stream once it is gone.
        docker_client.api.post.side_effect = lambda *a, **k: pools.mounted.discard(str(pools.alpha)) or pull_answer()
        before = _tree(pools.root)

        response = install(api, {**TRANSMISSION})

        assert response.status_code == 409
        assert "no longer mounted" in response.json()["detail"]
        assert _tree(pools.root) == before
        docker_client.containers.create.assert_not_called()

    def test_existing_data_is_reused(self, api, pools, docker_client):
        config = pools.alpha / "apps/transmission/config"
        config.mkdir(parents=True)
        (config / "settings.json").write_text("{}")

        response = install(api, {**TRANSMISSION})

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

        response = install(
            api,
            {**TRANSMISSION, "volume_overrides": {"/downloads": make_path(pools)}},
        )

        assert response.status_code == 400, response.text
        docker_client.api.post.assert_not_called()
        assert _tree(pools.root) == before

    def test_unknown_volume_cannot_be_added(self, api, pools, docker_client):
        response = install(
            api,
            {**TRANSMISSION, "volume_overrides": {"/host-root": str(pools.alpha)}},
        )

        assert response.status_code == 400
        assert "/host-root" in response.json()["detail"]
        docker_client.api.post.assert_not_called()

    def test_fixed_host_mount_cannot_be_changed(self, api, pools, docker_client):
        response = install(
            api,
            {
                "template_id": "portainer",
                "volume_overrides": {"/var/run/docker.sock": str(pools.alpha / "docker.sock")},
            },
        )

        assert response.status_code == 400
        assert "fixed host mount" in response.json()["detail"]
        docker_client.api.post.assert_not_called()

    def test_path_in_selected_pool_is_created_and_normalised(self, api, pools, docker_client):
        raw = f"{pools.alpha}//torrents/./new/"

        response = install(
            api,
            {**TRANSMISSION, "volume_overrides": {"/downloads": raw}},
        )

        assert response.status_code == 200, response.text
        assert (pools.alpha / "torrents/new").is_dir()
        assert _mounts(docker_client)["/downloads"]["Source"] == str(pools.alpha / "torrents/new")

    def test_dotdot_is_refused_even_inside_the_pool(self, api, pools, docker_client):
        before = _tree(pools.root)

        response = install(
            api,
            {
                **TRANSMISSION,
                "volume_overrides": {"/downloads": f"{pools.alpha}/media/../torrents"},
            },
        )

        assert response.status_code == 400
        assert "traversal" in response.json()["detail"]
        docker_client.api.post.assert_not_called()
        assert _tree(pools.root) == before

    def test_link_to_another_pools_folder_is_not_resolved(self, api, pools, docker_client):
        # Pool contents are writable by SMB users and apps. Resolving this link
        # used to bind-mount beta's private folder into the new app.
        private = pools.beta / "apps/vaultwarden/data"
        private.mkdir(parents=True)
        (pools.alpha / "shared").symlink_to(private)
        before = _tree(pools.root)

        response = install(
            api,
            {**TRANSMISSION, "volume_overrides": {"/downloads": str(pools.alpha / "shared")}},
        )

        assert response.status_code == 400
        detail = response.json()["detail"]
        assert "symbolic link" in detail and str(pools.alpha / "shared") in detail
        docker_client.api.post.assert_not_called()
        assert _tree(pools.root) == before

    @pytest.mark.parametrize("override", ["alias", "alias/torrents"])
    def test_link_inside_the_same_pool_is_not_resolved(self, api, pools, docker_client, override):
        (pools.alpha / "library").mkdir()
        (pools.alpha / "alias").symlink_to(pools.alpha / "library")
        before = _tree(pools.root)

        response = install(
            api,
            {**TRANSMISSION, "volume_overrides": {"/downloads": str(pools.alpha / override)}},
        )

        assert response.status_code == 400
        assert "symbolic link" in response.json()["detail"]
        docker_client.api.post.assert_not_called()
        assert _tree(pools.root) == before

    def test_existing_path_in_another_mounted_pool_is_used(self, api, pools, docker_client):
        library = pools.beta / "torrents"
        library.mkdir()

        response = install(
            api,
            {**TRANSMISSION, "volume_overrides": {"/downloads": str(library)}},
        )

        assert response.status_code == 200, response.text
        assert response.json()["pool"] == "alpha"
        assert _mounts(docker_client)["/downloads"]["Source"] == str(library)
        assert _mounts(docker_client)["/config"]["Source"] == str(pools.alpha / "apps/transmission/config")

    def test_missing_path_in_another_pool_is_not_created(self, api, pools, docker_client):
        before = _tree(pools.root)

        response = install(
            api,
            {**TRANSMISSION, "volume_overrides": {"/downloads": str(pools.beta / "torrents")}},
        )

        assert response.status_code == 400
        assert "does not exist" in response.json()["detail"]
        assert _tree(pools.root) == before
        docker_client.api.post.assert_not_called()


# ---------------------------------------------------------------------------
# Required environment and placeholder secrets
# ---------------------------------------------------------------------------


class TestEnvironment:
    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_required_variable_must_be_set(self, api, pools, docker_client, value):
        env = {} if value is None else {"PHOTOPRISM_ADMIN_PASSWORD": value}

        response = install(api, {"template_id": "photoprism", "env_overrides": env})

        assert response.status_code == 400
        assert "PHOTOPRISM_ADMIN_PASSWORD is required" in response.json()["detail"]
        docker_client.api.post.assert_not_called()

    @pytest.mark.parametrize(
        "app_id,name,value",
        [
            ("photoprism", "PHOTOPRISM_ADMIN_PASSWORD", "changeme"),
            ("grafana", "GF_SECURITY_ADMIN_PASSWORD", "Admin"),
            ("code-server", "PASSWORD", " password "),
            ("aria2", "RPC_SECRET", "secret"),  # optional secrets are checked too
            ("transmission", "PASS", "changeme"),
        ],
    )
    def test_placeholder_secrets_are_rejected(self, api, pools, docker_client, app_id, name, value):
        response = install(api, {"template_id": app_id, "env_overrides": {name: value}})

        assert response.status_code == 400
        assert name in response.json()["detail"]
        assert "placeholder" in response.json()["detail"]
        docker_client.api.post.assert_not_called()

    def test_unique_secret_is_passed_to_the_container(self, api, pools, docker_client):
        response = install(
            api,
            {
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

        response = install(api, {"template_id": "transmission", "env_overrides": login})

        assert response.status_code == 400
        assert "is required" in response.json()["detail"]
        docker_client.api.post.assert_not_called()

    def test_transmission_login_is_passed_to_the_container(self, api, pools, docker_client):
        response = install(api, TRANSMISSION)

        assert response.status_code == 200, response.text
        environment = docker_client.containers.create.call_args.kwargs["environment"]
        assert (environment["USER"], environment["PASS"]) == ("e2e", "k7-Unique-pass")

    def test_tailscale_installs_without_an_auth_key(self, api, pools, docker_client):
        # Without a key the container logs a login URL to follow.
        [key] = [var for var in get_app_by_id("tailscale").environment if var.name == "TS_AUTHKEY"]
        assert (key.required, key.secret) == (False, True)
        assert "logs" in key.description and "login URL" in key.description

        response = install(api, {"template_id": "tailscale"})

        assert response.status_code == 200, response.text
        assert docker_client.containers.create.call_args.kwargs["environment"]["TS_AUTHKEY"] == ""

    def test_tailscale_auth_key_is_passed_and_checked(self, api, pools, docker_client):
        bad = install(api, {"template_id": "tailscale", "env_overrides": {"TS_AUTHKEY": "changeme"}})
        assert bad.status_code == 400
        assert "placeholder" in bad.json()["detail"]

        good = install(api, {"template_id": "tailscale", "env_overrides": {"TS_AUTHKEY": "tskey-auth-k1"}})
        assert good.status_code == 200, good.text
        assert docker_client.containers.create.call_args.kwargs["environment"]["TS_AUTHKEY"] == "tskey-auth-k1"


# ---------------------------------------------------------------------------
# File-like sources on the pool
# ---------------------------------------------------------------------------


def _pin(ref: str) -> str:
    """``ref`` (name:tag) pinned by a digest that only it has: every image that an install pulls is
    (ADR-0410)."""
    return f"{ref}@sha256:{hashlib.sha256(ref.encode()).hexdigest()}"


DEMO = AppTemplate(
    id="demo",
    name="Demo",
    description="Template with a single-file volume",
    icon="x",
    category=AppCategory.OTHER,
    image=_pin("example/demo:1"),
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
        response = install(api, {"template_id": "demo"})

        assert response.status_code == 200, response.text
        db = pools.alpha / "apps/demo/app.db"
        assert db.is_file() and db.stat().st_size == 0
        assert (pools.alpha / "apps/demo/data").is_dir()
        assert _mounts(docker_client)["/app.db"]["Source"] == str(db)

    def test_existing_file_is_kept(self, api, pools, docker_client):
        db = pools.alpha / "apps/demo/app.db"
        db.parent.mkdir(parents=True)
        db.write_bytes(b"data")

        response = install(api, {"template_id": "demo"})

        assert response.status_code == 200, response.text
        assert db.read_bytes() == b"data"

    def test_directory_in_place_of_a_file_is_rejected(self, api, pools, docker_client):
        # What Docker leaves behind when it auto-creates a missing file source.
        (pools.alpha / "apps/demo/app.db").mkdir(parents=True)

        response = install(api, {"template_id": "demo"})

        assert response.status_code == 400
        assert "needs a file" in response.json()["detail"]
        docker_client.api.post.assert_not_called()

    def test_file_override_is_normalised(self, api, pools, docker_client):
        (pools.beta / "db").mkdir()
        (pools.beta / "db/app.db").write_bytes(b"data")

        response = install(
            api,
            {"template_id": "demo", "volume_overrides": {"/app.db": f"{pools.beta}//db/./app.db"}},
        )

        assert response.status_code == 200, response.text
        assert _mounts(docker_client)["/app.db"]["Source"] == str(pools.beta / "db/app.db")

    def test_file_in_a_linked_folder_is_refused(self, api, pools, docker_client):
        (pools.alpha / "linked").symlink_to(pools.outside)
        before = _tree(pools.root)

        response = install(
            api,
            {"template_id": "demo", "volume_overrides": {"/app.db": str(pools.alpha / "linked/app.db")}},
        )

        assert response.status_code == 400
        assert "symbolic link" in response.json()["detail"]
        docker_client.api.post.assert_not_called()
        assert _tree(pools.root) == before

    def test_link_in_place_of_the_file_is_refused(self, api, pools, docker_client):
        (pools.alpha / "apps/demo").mkdir(parents=True)
        (pools.alpha / "apps/demo/app.db").symlink_to(pools.outside / "app.db")

        response = install(api, {"template_id": "demo"})

        assert response.status_code == 409
        assert "symbolic link" in response.json()["detail"]
        docker_client.api.post.assert_not_called()
        assert list(pools.outside.iterdir()) == []

    @pytest.mark.parametrize(
        "path", ["{alpha}/apps/demo/..", "{alpha}/apps/demo/.", "/", "{alpha}/apps/demo/app\n.db"]
    )
    def test_file_override_must_name_a_file(self, api, pools, docker_client, path):
        response = install(
            api,
            {"template_id": "demo", "volume_overrides": {"/app.db": path.format(alpha=pools.alpha)}},
        )

        assert response.status_code == 400
        assert "must name a file" in response.json()["detail"]
        docker_client.api.post.assert_not_called()


# ---------------------------------------------------------------------------
# Docker flow
# ---------------------------------------------------------------------------


class TestDockerFlow:
    def test_existing_container_is_a_conflict(self, api, pools, docker_client, installed):
        installed(_container("jellyfin"))

        response = install(api, {"template_id": "jellyfin"})

        assert (response.status_code, response.json()) == (
            409, {"detail": "A container named 'jellyfin' already exists"}
        )
        # One listing of Docker's containers names them all (ADR-0381).
        docker_client.containers.list.assert_called_once_with(all=True, ignore_removed=True)
        docker_client.containers.get.assert_not_called()
        docker_client.api.post.assert_not_called()
        assert not (pools.alpha / "apps").exists()

    @pytest.mark.parametrize("name", ["jellyfin", "media"], ids=["its-name", "another-name"])
    def test_an_installed_app_is_not_installed_again(self, api, pools, docker_client, installed, name):
        # One instance per template: its data folders are named after the template (ADR-0381).
        installed(_jellyfin(pools, name=name))

        response = install(api, {"template_id": "jellyfin"})

        assert (response.status_code, response.json()) == (409, {"detail": "Jellyfin is already installed"})
        docker_client.api.post.assert_not_called()

    def test_failed_start_removes_the_container(self, api, pools, docker_client):
        container = docker_client.containers.create.return_value
        container.start.side_effect = APIError("port is already allocated")

        response = install(api, {"template_id": "jellyfin"})

        assert response.status_code == 500
        assert "port is already allocated" in response.json()["detail"]
        container.remove.assert_called_once_with(force=True)

    @pytest.mark.parametrize("name", ["jellyfin2", "../etc"])
    def test_a_name_in_the_request_is_refused(self, api, pools, docker_client, name):
        # The containers of an app are named after its template (ADR-0381).
        response = install(api, {"template_id": "jellyfin", "name": name})

        assert response.status_code == 422
        docker_client.containers.list.assert_not_called()
        docker_client.api.post.assert_not_called()


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
# The apps of the catalog that run in the host's network with a port: id, name and the port.
HOST_NETWORK_APPS = [("homeassistant", "Home Assistant", 8123), ("scrypted", "Scrypted", 10443)]


class TestPortCheck:
    """An install checks the host ports its container would publish before it pulls anything: a
    port that a container publishes, running or stopped, or that a program on the host listens
    on, answers 409 instead of a start that fails after a pull of minutes (ADR-0381)."""

    @pytest.mark.parametrize("status", ["running", "exited", "created"])
    def test_a_port_that_a_container_publishes_answers_409(self, api, pools, docker_client, installed, soc, status):
        installed(_container("emby", status=status, port_bindings=_published(8096)))
        # A running container's port is also a listener of Docker's proxy on the host.
        soc.sockets = ['tcp   LISTEN 0      4096   0.0.0.0:8096   0.0.0.0:*   users:(("docker-proxy",pid=9,fd=7))']

        response = install(api, {"template_id": "jellyfin"})

        assert (response.status_code, response.json()) == (409, {
            "detail": f"Port 8096/tcp is already used by the container emby ({status}): "
            "choose another host port for 8096/tcp"
        })
        # Stopped containers too: Docker binds their ports when they start.
        docker_client.containers.list.assert_called_once_with(all=True, ignore_removed=True)
        docker_client.api.post.assert_not_called()
        docker_client.containers.create.assert_not_called()
        assert not (pools.alpha / "apps").exists()

    def test_a_container_that_publishes_the_port_on_one_address_holds_it(self, api, pools, docker_client, installed):
        installed(_container("emby", status="running", port_bindings=_published(8096, host_ip="127.0.0.1")))

        response = install(api, {"template_id": "jellyfin"})

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

        response = install(api, {"template_id": "jellyfin"})

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

        response = install(api, {"template_id": "jellyfin"})

        assert (response.status_code, response.json()) == (409, {
            "detail": f"Port 8096/tcp is already in use on the host by {holder}: choose another host port for 8096/tcp"
        })
        assert soc.commands[0] == SS
        docker_client.api.post.assert_not_called()
        assert not (pools.alpha / "apps").exists()

    def test_listeners_of_other_ports_and_protocols_do_not_count(self, api, pools, docker_client, soc):
        soc.sockets = [
            'udp   UNCONN 0      0            0.0.0.0:8096       0.0.0.0:*    users:(("EmbyServer",pid=8,fd=7))',
            'tcp   LISTEN 0      4096         0.0.0.0:18096      0.0.0.0:*    users:(("EmbyServer",pid=8,fd=8))',
            'tcp   LISTEN 0      4096            [::]:80960         [::]:*    users:(("EmbyServer",pid=8,fd=9))',
            'tcp   LISTEN 0      4096         0.0.0.0:809        0.0.0.0:*    users:(("EmbyServer",pid=8,fd=9))',
        ]

        response = install(api, {"template_id": "jellyfin"})

        assert response.status_code == 200, response.text

    def test_every_taken_port_is_named(self, api, pools, docker_client, soc):
        # The resolver of systemd listens on port 53 of 127.0.0.53 by TCP and UDP.
        soc.sockets = RESOLVED

        response = install(api, {"template_id": "pihole", "env_overrides": PIHOLE_LOGIN})

        assert (response.status_code, response.json()) == (409, {
            "detail": "Port 53/tcp is already in use on the host by systemd-resolve: "
            "choose another host port for 53/tcp; "
            "Port 53/udp is already in use on the host by systemd-resolve: "
            "choose another host port for 53/udp"
        })
        docker_client.api.post.assert_not_called()

    def test_a_port_is_taken_only_for_its_protocol(self, api, pools, docker_client, soc):
        # Transmission's peer port is a TCP and a UDP port of one number; one override moves both.
        soc.sockets = ['udp   UNCONN 0      0      0.0.0.0:51413      0.0.0.0:*    users:(("qbittorrent-nox",pid=4,fd=9))']

        taken = install(api, TRANSMISSION)
        moved = install(api, {**TRANSMISSION, "port_overrides": {51413: 51414}})

        assert (taken.status_code, taken.json()) == (409, {
            "detail": "Port 51413/udp is already in use on the host by qbittorrent-nox: "
            "choose another host port for 51413/udp"
        })
        assert moved.status_code == 200, moved.text
        assert docker_client.containers.create.call_args.kwargs["ports"] == {
            "9091/tcp": 9091, "51413/tcp": 51414, "51413/udp": 51414,
        }
        docker_client.api.post.assert_called_once()

    def test_a_free_host_port_given_for_a_taken_one_installs(self, api, pools, docker_client, installed, soc):
        installed(_container("emby", status="running", port_bindings=_published(8096)))
        soc.sockets = ['tcp   LISTEN 0      4096   0.0.0.0:8096   0.0.0.0:*   users:(("docker-proxy",pid=9,fd=7))']

        response = install(api, {"template_id": "jellyfin", "port_overrides": {8096: 8097}})

        assert response.status_code == 200, response.text
        assert docker_client.containers.create.call_args.kwargs["ports"] == {"8096/tcp": 8097}
        assert response.json()["web_url"] == "http://localhost:8097"

    def test_the_catalogs_apps_in_the_hosts_network_name_the_ports_they_bind(self):
        # Docker discards the port bindings of a container in the host's network: the app binds
        # its container port on the host itself, which the catalog shows as its host port.
        in_host_network = [t for t in APP_CATALOG if t.network_mode == "host"]

        for template in in_host_network:
            assert all(port.host == port.container for port in template.ports), template.id
        assert sorted((t.id, t.name, [p.container for p in t.ports]) for t in in_host_network if t.ports) == [
            (template_id, name, [port]) for template_id, name, port in HOST_NETWORK_APPS
        ]

    @pytest.mark.parametrize(("template_id", "name", "port"), HOST_NETWORK_APPS, ids=[a[0] for a in HOST_NETWORK_APPS])
    def test_the_port_of_an_app_in_the_hosts_network_cannot_be_moved(
        self, api, pools, docker_client, soc, template_id, name, port
    ):
        response = install(api, {"template_id": template_id, "port_overrides": {port: port + 1}})

        assert (response.status_code, response.json()) == (
            400, {"detail": f"{name} runs in the host's network: its port {port}/tcp cannot be moved"}
        )
        docker_client.containers.get.assert_not_called()
        docker_client.api.post.assert_not_called()
        assert soc.commands == []
        assert not (pools.alpha / "apps").exists()

    @pytest.mark.parametrize(("template_id", "name", "port"), HOST_NETWORK_APPS, ids=[a[0] for a in HOST_NETWORK_APPS])
    def test_an_app_in_the_hosts_network_may_name_the_port_it_binds(
        self, api, pools, docker_client, soc, template_id, name, port
    ):
        response = install(api, {"template_id": template_id, "port_overrides": {port: port}})

        assert response.status_code == 200, response.text
        assert docker_client.containers.create.call_args.kwargs["ports"] == {f"{port}/tcp": port}
        assert response.json()["web_url"] == f"http://localhost:{port}"
        assert soc.commands == [SS, _free_space_read()]

    @pytest.mark.parametrize(("template_id", "name", "port"), HOST_NETWORK_APPS, ids=[a[0] for a in HOST_NETWORK_APPS])
    def test_a_taken_port_of_an_app_in_the_hosts_network_asks_to_stop_the_program(
        self, api, pools, docker_client, soc, template_id, name, port
    ):
        soc.sockets = [f'tcp   LISTEN 0      4096   0.0.0.0:{port}   0.0.0.0:*   users:(("python3",pid=7,fd=9))']

        response = install(api, {"template_id": template_id})

        assert (response.status_code, response.json()) == (409, {
            "detail": f"Port {port}/tcp is already in use on the host by python3: "
            f"{name} runs in the host's network and cannot move its port; stop that program first"
        })
        docker_client.api.post.assert_not_called()

    @pytest.mark.parametrize(("template_id", "name", "port"), HOST_NETWORK_APPS, ids=[a[0] for a in HOST_NETWORK_APPS])
    def test_a_port_of_an_app_in_the_hosts_network_that_a_container_publishes_asks_to_remove_it(
        self, api, pools, docker_client, installed, soc, template_id, name, port
    ):
        installed(_container("other", status="exited", port_bindings=_published(port)))

        response = install(api, {"template_id": template_id})

        assert (response.status_code, response.json()) == (409, {
            "detail": f"Port {port}/tcp is already used by the container other (exited): "
            f"{name} runs in the host's network and cannot move its port; remove that container first"
        })
        docker_client.api.post.assert_not_called()

    @pytest.mark.parametrize("port", [0, -1, 65536])
    def test_a_host_port_outside_the_range_answers_400(self, api, pools, docker_client, soc, port):
        response = install(api, {"template_id": "jellyfin", "port_overrides": {8096: port}})

        assert (response.status_code, response.json()) == (
            400, {"detail": "Host port for 8096/tcp must be between 1 and 65535"}
        )
        docker_client.containers.get.assert_not_called()
        assert soc.commands == []

    def test_a_host_port_given_twice_answers_400(self, api, pools, docker_client, soc):
        # Pi-hole's web page (80/tcp, on 8053) moved onto its DNS port (53/tcp).
        response = install(
            api, {"template_id": "pihole", "env_overrides": PIHOLE_LOGIN, "port_overrides": {80: 53}}
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
            response = install(api, {"template_id": "jellyfin"})

        assert response.status_code == 200, response.text
        assert [r.getMessage() for r in caplog.records if r.name == "src.apps.preflight"] == [message]
        docker_client.containers.create.return_value.start.assert_called_once()

    def test_an_app_that_publishes_no_port_reads_no_port_of_the_host(self, api, pools, docker_client, soc):
        # Tailscale runs in the host's network and publishes no port: only the free space is read.
        response = install(api, {"template_id": "tailscale"})

        assert response.status_code == 200, response.text
        assert soc.commands == [_free_space_read()]

    def test_the_host_is_read_off_the_event_loop(self, api, pools, docker_client, soc, monkeypatch):
        seen: list[bool] = []
        real = host_module.run_on_host

        def run(cmd, **kwargs):
            seen.append(_on_event_loop())
            return real(cmd, **kwargs)

        monkeypatch.setattr(host_module, "run_on_host", run)

        # Dozzle has no hardware profile: its install reads the host's ports and free space only.
        assert install(api, {"template_id": "dozzle"}).status_code == 200
        assert seen == [False, False]

    def test_a_listing_that_docker_does_not_answer_answers_503(self, api, pools, docker_client):
        error = requests.exceptions.ConnectionError("('Connection aborted.', ConnectionResetError(104, 'reset'))")
        docker_client.containers.list.side_effect = error

        response = install(api, {"template_id": "jellyfin"})

        assert (response.status_code, response.json()) == (503, {"detail": f"Docker is not available: {error}"})
        docker_client.api.post.assert_not_called()


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
    image = image.partition("@")[0]  # a pinned image: name:tag@digest (ADR-0381)
    name, sep, tag = image.rpartition(":")
    return name if sep and "/" not in tag else image


class TestDataFolderOwner:
    def test_catalog_gives_writable_folders_of_non_root_images_to_their_user(self):
        for spec in _catalog_containers():
            user = NON_ROOT_IMAGES.get(_image_name(spec.image))
            for volume in spec.volumes:
                # Never the pool root, and only folders the app writes to.
                owned = (
                    user is not None
                    and volume.on_pool
                    and not volume.readonly
                    and volume.host != POOL_PLACEHOLDER
                )
                expected = user if owned else (None, None)
                assert (volume.uid, volume.gid) == expected, (spec.image, volume.container)

    def test_new_folders_belong_to_the_image_user(self, api, pools, docker_client, chown_calls):
        response = install(api, {"template_id": "filebrowser"})

        assert response.status_code == 200, response.text
        assert sorted(chown_calls) == [
            (pools.alpha / "apps/filebrowser/config", 1000, 1000),
            (pools.alpha / "apps/filebrowser/database", 1000, 1000),
        ]
        # The parent folders and the pool root (/srv) stay owned by root.

    def test_existing_folders_keep_their_owner(self, api, pools, docker_client, chown_calls):
        (pools.alpha / "apps/filebrowser/config").mkdir(parents=True)

        response = install(api, {"template_id": "filebrowser"})

        assert response.status_code == 200, response.text
        assert [call[0] for call in chown_calls] == [pools.alpha / "apps/filebrowser/database"]

    def test_images_that_start_as_root_get_root_owned_folders(
        self, api, pools, docker_client, chown_calls
    ):
        response = install(api, {"template_id": "jellyfin"})

        assert response.status_code == 200, response.text
        assert chown_calls == []

    def test_failed_owner_change_fails_the_install(self, api, pools, docker_client, monkeypatch):
        def refuse(*args, **kwargs):
            raise PermissionError(1, "Operation not permitted")

        monkeypatch.setattr(os, "fchown", refuse)

        response = install(api, {"template_id": "filebrowser"})

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
        # Every container's folders: a companion's are its app's (ADR-0381).
        volumes = [(t, v) for t in APP_CATALOG for spec in (t, *t.companions) for v in spec.volumes if v.on_pool]
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

        response = install(
            api,
            {
                **TRANSMISSION,
                "volume_overrides": {"/watch": str(pools.alpha / "downloads/watch")},
            },
        )

        assert response.status_code == 400
        assert "inside the folder for /downloads" in response.json()["detail"]
        docker_client.api.post.assert_not_called()
        assert _tree(pools.root) == before

    def test_writable_folder_cannot_be_moved_to_the_pool_root(self, api, pools, docker_client):
        # Syncthing's /data on the pool root would contain its own /config.
        response = install(
            api,
            {"template_id": "syncthing", "volume_overrides": {"/data": str(pools.alpha)}},
        )

        assert response.status_code == 400
        assert "inside the folder for /data" in response.json()["detail"]
        docker_client.api.post.assert_not_called()

    @pytest.mark.parametrize("app_id", ["filebrowser", "duplicati"])
    def test_templates_built_for_the_pool_root_install(self, api, pools, docker_client, app_id):
        response = install(api, {"template_id": app_id})

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

        response = install(api, {**TRANSMISSION})

        assert response.status_code == 409
        assert "symbolic link" in response.json()["detail"]
        assert list(pools.outside.iterdir()) == []
        docker_client.api.post.assert_not_called()


# ---------------------------------------------------------------------------
# Running in the group unos-smb, and the folders of shares (ADR-0274)
# ---------------------------------------------------------------------------

LOGIN = TRANSMISSION["env_overrides"]
IN_UNOS_SMB = {**TRANSMISSION, "run_in_unos_smb": True}
# linuxserver.io apps whose every pool folder is their own data under {pool}/apps,
# which is never a share's folder: the group would give them nothing.
ONLY_OWN_DATA = (
    "prowlarr", "overseerr", "tautulli", "nextcloud", "wikijs", "heimdall", "wireguard", "code-server",
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
        response = install(api, IN_UNOS_SMB)

        assert response.status_code == 200, response.text
        environment = _environment(docker_client)
        # 80000 is no other app's user: apps outside the group run as 1000,
        # and a share's folder that the image gives to its user (chown) must
        # not become theirs. 007 gives new entries 2770 and 0660, as Samba does.
        assert (environment["PUID"], environment["PGID"], environment["UMASK"]) == ("80000", "70000", "007")
        assert (environment["USER"], environment["PASS"]) == ("e2e", "k7-Unique-pass")

    def test_no_catalog_app_runs_as_the_user_of_the_group(self):
        for spec in _catalog_containers():
            assert all(volume.uid != 80000 for volume in spec.volumes), spec.image
            assert all(var.value.strip() != "80000" for var in spec.environment), spec.image

    def test_without_the_option_the_app_stays_outside_the_group(self, api, pools, docker_client):
        response = install(api, TRANSMISSION)

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
        response = install(
            api,
            {
                **TRANSMISSION,
                "run_in_unos_smb": run_in_unos_smb,
                "env_overrides": {**LOGIN, **overrides},
            },
        )

        assert response.status_code == 400
        assert response.json()["detail"] == _set_by_unos(names)
        docker_client.api.post.assert_not_called()

    @pytest.mark.parametrize(
        "app_id,name", [("jellyfin", "Jellyfin"), ("plex", "Plex Media Server"), ("filebrowser", "File Browser")]
    )
    def test_other_images_cannot_run_in_the_group(self, api, pools, docker_client, app_id, name):
        before = _tree(pools.root)

        response = install(api, {"template_id": app_id, "run_in_unos_smb": True})

        assert response.status_code == 400
        assert response.json()["detail"] == (
            f"{name} cannot run in the group unos-smb: only linuxserver.io images take the user and "
            "group of their processes from PUID and PGID"
        )
        docker_client.api.post.assert_not_called()
        assert _tree(pools.root) == before

    @pytest.mark.parametrize("app_id", ONLY_OWN_DATA)
    def test_apps_with_only_their_own_data_cannot_run_in_the_group(self, api, pools, docker_client, app_id):
        template = get_app_by_id(app_id)
        before = _tree(pools.root)

        response = install(api, {"template_id": app_id, "run_in_unos_smb": True})

        assert response.status_code == 400
        assert response.json()["detail"] == (
            f"{template.name} cannot run in the group unos-smb: its folders on the pool hold only its "
            "own data (apps/), and none of them can be a share's folder"
        )
        docker_client.api.post.assert_not_called()
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

        response = install(
            api,
            {"template_id": app_id, "env_overrides": env, "volume_overrides": {target: str(family)}},
        )

        assert response.status_code == 400
        assert response.json()["detail"] == (
            f"The folder for {target} ({family}) is the folder of the share family ({family}). {needs}"
        )
        docker_client.api.post.assert_not_called()
        assert _tree(pools.root) == before

    def test_an_app_in_the_group_gets_the_share_folder(self, api, pools, docker_client, family):
        response = install(api, {**IN_UNOS_SMB, "volume_overrides": {"/downloads": str(family)}})

        assert response.status_code == 200, response.text
        assert _mounts(docker_client)["/downloads"] == {
            "Target": "/downloads", "Source": str(family), "Type": "bind", "ReadOnly": False,
        }
        assert _environment(docker_client)["PGID"] == "70000"
        assert {"host": str(family), "container": "/downloads", "readonly": False, "companion": None} in (
            response.json()["volumes"]
        )

    def test_a_shared_default_folder_needs_the_option(self, api, pools, docker_client, shares):
        # Folders that apps use can be shared; an app installed on one afterwards needs the group.
        downloads = pools.alpha / "downloads"
        downloads.mkdir()
        shares("Downloads", downloads)

        refused = install(api, TRANSMISSION)

        assert refused.status_code == 400
        assert refused.json()["detail"] == (
            f"The folder for /downloads ({downloads}) is the folder of the share Downloads ({downloads}). "
            + _needs_the_group("Transmission")
        )
        docker_client.api.post.assert_not_called()

        allowed = install(api, IN_UNOS_SMB)

        assert allowed.status_code == 200, allowed.text
        assert _mounts(docker_client)["/downloads"]["Source"] == str(downloads)

    @pytest.mark.parametrize("run_in_unos_smb", [False, True])
    def test_a_folder_inside_a_share_is_refused(self, api, pools, docker_client, family, run_in_unos_smb):
        torrents = family / "torrents"
        torrents.mkdir()
        before = _tree(pools.root)

        response = install(
            api,
            {**TRANSMISSION, "run_in_unos_smb": run_in_unos_smb,
                  "volume_overrides": {"/downloads": str(torrents)}},
        )

        assert response.status_code == 400
        assert response.json()["detail"] == (
            f"The folder for /downloads ({torrents}) is inside the folder of the share family ({family}). "
            "An app gets a share's whole folder or none of it: whoever can write to the share, such as an "
            "app given its folder, can put a symbolic link in the place of a folder inside it, and Docker "
            "would follow that link at the next start."
        )
        docker_client.api.post.assert_not_called()
        assert _tree(pools.root) == before

    @pytest.mark.parametrize("run_in_unos_smb", [False, True])
    def test_a_writable_folder_that_holds_a_share_is_refused(
        self, api, pools, docker_client, shares, run_in_unos_smb
    ):
        media = pools.alpha / "media"
        movies = media / "movies"
        movies.mkdir(parents=True)
        shares("movies", movies)

        response = install(
            api,
            {**TRANSMISSION, "run_in_unos_smb": run_in_unos_smb,
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
        docker_client.api.post.assert_not_called()

    def test_a_read_only_folder_that_holds_shares_needs_the_option(self, api, pools, docker_client, family):
        # Duplicati backs up the whole pool, mounted read-only at /source.
        refused = install(api, {"template_id": "duplicati"})

        assert refused.status_code == 400
        assert refused.json()["detail"] == (
            f"The folder for /source ({pools.alpha}) holds the folder of the share family ({family}). "
            + _needs_the_group("Duplicati")
        )
        docker_client.api.post.assert_not_called()

        allowed = install(api, {"template_id": "duplicati", "run_in_unos_smb": True})

        assert allowed.status_code == 200, allowed.text
        assert _mounts(docker_client)["/source"] == {
            "Target": "/source", "Source": str(pools.alpha), "Type": "bind", "ReadOnly": True,
        }

    def test_an_app_outside_the_group_gets_no_pool_that_holds_shares(self, api, pools, docker_client, family):
        response = install(api, {"template_id": "filebrowser"})

        assert response.status_code == 400
        assert response.json()["detail"] == (
            f"The folder for /srv ({pools.alpha}) holds the folder of the share family ({family}). "
            "File Browser cannot run in the group unos-smb, so it gets no share's files: choose a "
            "folder that holds no share."
        )
        docker_client.api.post.assert_not_called()

    def test_the_apps_own_data_never_goes_into_a_share_folder(self, api, pools, docker_client, family):
        response = install(api, {**IN_UNOS_SMB, "volume_overrides": {"/config": str(family)}})

        assert response.status_code == 400
        assert response.json()["detail"] == (
            f"/config holds Transmission's own data, which must not go into the folder of the share "
            f"family ({family}): SMB users would read and change the app's settings. Choose a folder "
            "that is not shared."
        )
        docker_client.api.post.assert_not_called()

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

        response = install(api, TRANSMISSION)

        assert response.status_code == 400
        assert response.json()["detail"] == (
            f"The folder for /downloads ({downloads}) is the folder of the share Downloads ({downloads}). "
            + _needs_the_group("Transmission")
        )
        docker_client.api.post.assert_not_called()

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

        response = install(api, {**request_body, "volume_overrides": {target: str(source)}})

        assert response.status_code == 400
        what = "a snapshot folder (.snapshots)" if name == ".snapshots" else "the trash (.unos-trash)"
        assert response.json()["detail"] == (
            f"The folder for {target} ({source}) is in {what}, which keeps files of shares outside "
            f"their folders: no app gets a snapshot or the trash. Choose a folder outside {name}."
        )
        docker_client.api.post.assert_not_called()
        assert _tree(pools.root) == before

    def test_snapshots_are_refused_also_where_no_share_is(self, api, pools, docker_client):
        # A snapshot can hold the files of a share that was deleted or moved since.
        snapshot = pools.beta / ".snapshots/daily"
        snapshot.mkdir(parents=True)

        response = install(api, {"template_id": "jellyfin", "volume_overrides": {"/media": str(snapshot)}})

        assert response.status_code == 400
        assert response.json()["detail"].startswith(
            f"The folder for /media ({snapshot}) is in a snapshot folder (.snapshots)"
        )
        docker_client.api.post.assert_not_called()

    def test_a_share_of_a_whole_pool_counts_for_nothing(self, api, pools, docker_client, shares):
        # Imported from an old smb.conf: never served (ADR-0145), and no app would install on the pool.
        shares("data", pools.alpha)
        shares("everything", pools.base)

        response = install(api, TRANSMISSION)

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

        response = install(api, TRANSMISSION)

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
        # A restart is a stop, then a start, as for an app of several containers (ADR-0381).
        assert [call[0] for call in container.method_calls if call[0] in ("stop", "start", "restart")] == [
            "stop", "start",
        ]
        container.stop.assert_called_once_with(timeout=10)
        container.start.assert_called_once_with()

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
        # Refused before the stop: the app keeps running.
        container.stop.assert_not_called()
        container.start.assert_not_called()

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
            {"host": str(pools.alpha / "apps/portainer/data"), "container": "/data", "readonly": False,
             "companion": None},
            {"host": "/var/run/docker.sock", "container": "/var/run/docker.sock", "readonly": False,
             "companion": None},
        ]
        assert app_info["data_outside_pools"] == []
        # An app of one container (ADR-0381).
        assert (app_info["companions"], app_info["missing"], app_info["health"]) == ([], [], None)

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

        # The install answers it from its job (ADR-0384).
        response = install(api, {"template_id": "jellyfin"}) if path == INSTALL else api.request(method, path)

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
        docker_client.api.post.side_effect = error

        response = install(api, {"template_id": "jellyfin"})

        assert (response.status_code, response.json()) == (500, {"detail": f"Failed to pull image: {error}"})
        docker_client.containers.create.assert_not_called()

    def test_a_pull_that_docker_refuses_keeps_its_status(self, api, pools, docker_client):
        docker_client.api.post.side_effect = APIError("404 Client Error", explanation="manifest unknown")

        response = install(api, {"template_id": "jellyfin"})

        assert response.status_code == 500
        assert response.json()["detail"].startswith("Failed to pull image: ")



class TestStopAndUninstall:
    def test_stop_stops_the_app_and_answers_its_new_state(self, api, pools, installed):
        container = installed(_stops(_jellyfin(pools, status="running")))

        response = api.post(STOP.format("jellyfin"))

        assert (response.status_code, response.json()) == (200, {"status": "exited", "id": "jellyfin"})
        # The stop's timeout is given, so that docker-py waits for it (ADR-0381).
        container.stop.assert_called_once_with(timeout=10)
        container.remove.assert_not_called()

    def test_uninstall_removes_the_container_and_keeps_the_apps_data(self, api, pools, installed):
        _make_jellyfin_folders(pools)
        (pools.alpha / "apps/jellyfin/config/library.db").write_text("the app's database")
        container = installed(_stops(_jellyfin(pools, status="running")))
        before = _tree(pools.root)

        response = api.delete(UNINSTALL.format("jellyfin"))

        assert (response.status_code, response.json()) == (200, {"status": "removed", "id": "jellyfin"})
        container.stop.assert_called_once_with(timeout=10)
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
# The hardware of the host's SoC (apps.rk3588-hardware-acceleration, ADR-0382)
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
# An image pinned by tag and digest (ADR-0381).
PINNED_IMAGE = re.compile(r"[^@\s]+:[^@\s]+@sha256:[0-9a-f]{64}")


def _catalog_containers() -> list:
    """The specs of every container of the catalog: each template's main container and its
    companions."""
    return [spec for template in APP_CATALOG for spec in (template, *template.companions)]


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

        response = install(api, {"template_id": "jellyfin"})

        assert response.status_code == 200, response.text
        docker_client.containers.create.assert_not_called()
        image = docker_client.api.create_container.call_args.args
        create = docker_client.api.create_container.call_args.kwargs
        config = _host_config(docker_client)
        pin = catalog_module.APP_IMAGES["jellyfin"]
        assert image == (f"{pin.name}@{pin.digest}",)
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

        response = install(api, {"template_id": "jellyfin"})

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

        response = install(api, {"template_id": "jellyfin"})

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

        response = install(api, {"template_id": "jellyfin"})

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

        response = install(api, {"template_id": "jellyfin"})

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
        soc.tree = error

        response = install(api, {"template_id": "jellyfin"})

        assert response.status_code == 200, response.text
        docker_client.api.create_container.assert_not_called()
        assert docker_client.containers.create.call_args.kwargs.get("devices") is None
        assert response.json()["devices"] == []

    def test_a_device_tree_read_that_fails_is_logged(self, api, pools, docker_client, soc, caplog):
        # nsenter exits non-zero when it cannot enter the host: the board counts as one without a
        # device tree, and the log says why.
        soc.tree = HostResult(1, "", "nsenter: reassociate to namespace 'ns/mnt' failed: Operation not permitted")

        with caplog.at_level("WARNING", logger="src.apps.hardware"):
            response = install(api, {"template_id": "jellyfin"})

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
            response = install(api, {"template_id": "jellyfin"})

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

    def test_an_app_without_hardware_reads_no_hardware_of_the_host(self, api, pools, docker_client, soc):
        soc.compatible = RK3588
        soc.devices = set(RK3588_VIDEO)

        response = install(api, {"template_id": "dozzle"})

        assert response.status_code == 200, response.text
        # Only the host ports that the app would take and the free space were read (ADR-0381).
        assert soc.commands == [SS, _free_space_read()]
        assert docker_client.containers.create.call_args.kwargs.get("devices") is None

    def test_a_failed_start_on_an_rk3588_removes_the_container(self, api, pools, docker_client, low_level, soc):
        soc.compatible = RK3588
        soc.devices = set(RK3588_VIDEO)
        container = docker_client.containers.create.return_value
        container.start.side_effect = APIError("error gathering device information while adding custom device")

        response = install(api, {"template_id": "jellyfin"})

        assert response.status_code == 500
        assert response.json()["detail"].startswith("Failed to start container: ")
        container.remove.assert_called_once_with(force=True)

    def test_a_create_that_docker_refuses_on_an_rk3588_answers_500(self, api, pools, docker_client, low_level, soc):
        soc.compatible = RK3588
        soc.devices = set(RK3588_VIDEO)
        low_level.create_container.side_effect = APIError("400 Client Error", explanation="invalid masked path")

        response = install(api, {"template_id": "jellyfin"})

        assert response.status_code == 500
        assert response.json()["detail"].startswith("Failed to create container: ")

    def test_a_new_container_that_docker_cannot_find_is_removed(self, api, pools, docker_client, low_level, soc):
        soc.compatible = RK3588
        soc.devices = set(RK3588_VIDEO)
        docker_client.containers.get.side_effect = NotFound("No such container")

        response = install(api, {"template_id": "jellyfin"})

        assert response.status_code == 500
        assert response.json()["detail"].startswith("Failed to create container: ")
        low_level.remove_container.assert_called_once_with(CREATED_ID, force=True)

    def test_the_catalog_names_the_hardware_only_on_its_soc(self, api, soc):
        soc.compatible = RK3588
        soc.devices = set(RK3588_VIDEO)

        listed = {t["id"]: [p["id"] for p in t["hardware"]] for t in api.get("/api/v1/apps/catalog").json()}
        one = api.get("/api/v1/apps/catalog/jellyfin").json()["hardware"]
        found = api.get("/api/v1/apps/catalog", params={"query": "jelly"}).json()

        # Immich's server too, for RKMPP (ADR-0382); its machine learning only with the NPU's node.
        assert {app_id: ids for app_id, ids in listed.items() if ids} == {
            "jellyfin": ["rk3588-video"],
            "immich": ["rk3588-video"],
        }
        assert one == [{
            "id": "rk3588-video",
            "soc": "rockchip,rk3588",
            "devices": RK3588_VIDEO,
            "requires": ["/dev/mpp_service"],
            "unmasked_paths": ["/sys/firmware"],
            "apparmor_unconfined": True,
            "image": None,
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
        profiles = [p for spec in _catalog_containers() for p in spec.hardware]
        assert profiles
        for profile in profiles:
            assert all(path.startswith("/dev/") for path in profile.devices), profile.id
            # A profile applies only with the nodes it requires, which the container gets: each
            # is one of its devices or lies in a folder of them (ADR-0382).
            assert profile.requires, profile.id
            for path in profile.requires:
                assert any(path == device or path.startswith(f"{device}/") for device in profile.devices), path
            assert set(profile.unmasked_paths) <= set(DOCKER_MASKED_PATHS), profile.id
        for template in APP_CATALOG:
            if hardware_module.all_profiles(template):
                # The device cgroup and Docker's other limits stay as they are: no privileges.
                assert not template.privileged and not template.capabilities, template.id

    def test_a_container_has_at_most_one_profile_that_chooses_its_image_and_it_is_pinned(self):
        for spec in _catalog_containers():
            images = [p.image for p in spec.hardware if p.image]
            assert len(images) <= 1, spec.image
            assert all(PINNED_IMAGE.fullmatch(image) for image in images), images

    def test_profiles_of_one_id_are_one_profile_but_for_its_image(self):
        # The host is read once for each id (hardware.all_profiles); a template gives a shared
        # profile its image with model_copy, and the shared profile names none.
        seen: dict[str, dict] = {}
        for spec in _catalog_containers():
            for profile in spec.hardware:
                rest = profile.model_dump(exclude={"image"})
                assert seen.setdefault(profile.id, rest) == rest, profile.id
        shared = [v for v in vars(catalog_module).values() if isinstance(v, HardwareProfile)]
        assert shared and not [p.id for p in shared if p.image]


# ---------------------------------------------------------------------------
# Hardware per container, and an image of a profile's own (ADR-0382)
# ---------------------------------------------------------------------------

# The NPU of the RK3588 under Rockchip's kernel, as ADR-0382's RK3588_NPU: the folder of DRM
# nodes, required by the stable name of the NPU's render node, a link inside it.
NPU_LINK = "/dev/dri/by-path/platform-fdab0000.npu-render"
NPU = HardwareProfile(
    id="rk3588-npu",
    soc="rockchip,rk3588",
    devices=["/dev/dri"],
    requires=[NPU_LINK],
    unmasked_paths=["/sys/firmware"],
    apparmor_unconfined=True,
)
VIDEO = get_app_by_id("jellyfin").hardware[0]
PHOTOS_ML = _pin("example/photos-ml:1")
PHOTOS_ML_NPU = _pin("example/photos-ml:1-rknn")
PHOTOS_SERVER = _pin("example/photos-server:1")
PHOTOS_SERVER_RK3588 = _pin("example/photos-server:1-rk3588")
PHOTOS_DB = _pin("example/postgres:14")


def _photos(**changes) -> AppTemplate:
    """An app of three containers in the manner of Immich: its server with the RK3588's video
    hardware, a database without any, and a machine learning service that gets the NPU and an
    image built for it where the NPU applies."""
    template = AppTemplate(
        id="photos",
        name="Photos",
        description="Photos of three containers",
        icon="P",
        category=AppCategory.MEDIA,
        image=PHOTOS_SERVER,
        version="1",
        ports=[PortMapping(host=2283, container=2283)],
        hardware=[VIDEO],
        companions=[
            Companion(name="database", image=PHOTOS_DB),
            Companion(
                name="machine-learning",
                image=PHOTOS_ML,
                hardware=[NPU.model_copy(update={"image": PHOTOS_ML_NPU})],
            ),
        ],
    )
    return template.model_copy(update=changes)


@pytest.fixture
def in_catalog(monkeypatch):
    """``in_catalog(template)`` adds ``template`` to the catalog that the API answers and installs."""

    def add(template: AppTemplate) -> AppTemplate:
        catalog = [*APP_CATALOG, template]
        monkeypatch.setattr(catalog_module, "APP_CATALOG", catalog)
        monkeypatch.setattr(apps_router, "APP_CATALOG", catalog)
        return template

    return add


def _rk3588(soc, *, npu: bool = True) -> None:
    """The dev device: an RK3588 under Rockchip's kernel with its video nodes, and the NPU's."""
    soc.compatible = RK3588
    soc.devices = {*RK3588_VIDEO, *([NPU_LINK] if npu else [])}


def _containers(template: dict) -> dict[str, dict]:
    """The containers of a template as the catalog API answers it, by name ("" for the main one)."""
    return {"": template, **{c["name"]: c for c in template["companions"]}}


class TestHardwarePerContainer:
    def test_a_profile_applies_to_its_own_container_and_may_choose_its_image(self, soc):
        _rk3588(soc)
        template = _photos()
        host = hardware_module.read_host_hardware(hardware_module.all_profiles(template))
        server, database, learning = template, *template.companions

        got = {
            "server": hardware_module.container_hardware(server, host),
            "database": hardware_module.container_hardware(database, host),
            "machine-learning": hardware_module.container_hardware(learning, host),
        }

        unmasked = [path for path in DOCKER_MASKED_PATHS if path != "/sys/firmware"]
        assert got["server"] == hardware_module.ContainerHardware(
            image=PHOTOS_SERVER,
            devices=RK3588_VIDEO,
            masked_paths=unmasked,
            security_opt=["apparmor=unconfined"],
        )
        assert got["database"] == hardware_module.ContainerHardware(image=PHOTOS_DB)
        # The folder is passed in; the link that the profile requires is inside it.
        assert got["machine-learning"] == hardware_module.ContainerHardware(
            image=PHOTOS_ML_NPU,
            devices=["/dev/dri"],
            masked_paths=unmasked,
            security_opt=["apparmor=unconfined"],
        )

    def test_the_host_is_read_once_for_the_profiles_of_every_container(self, soc):
        _rk3588(soc)

        hardware_module.read_host_hardware(hardware_module.all_profiles(_photos()))

        # One read of the device tree, and one look for the nodes of all profiles, each once.
        assert len(soc.commands) == 2 and _reads_device_tree(soc.commands[0])
        assert soc.commands[1][3:] == ["unos-devices", *RK3588_VIDEO, NPU_LINK]

    def test_a_profile_that_two_containers_share_is_read_once(self):
        template = _photos(companions=[Companion(name="worker", image=_pin("example/worker:1"), hardware=[VIDEO])])

        assert hardware_module.all_profiles(template) == [VIDEO]

    @pytest.mark.parametrize(
        ("compatible", "nodes"),
        [(None, set()), (RK3568, {*RK3588_VIDEO, NPU_LINK}), (RK3588, set(RK3588_VIDEO))],
        ids=["pc", "rk3568", "rk3588-without-the-npu"],
    )
    def test_where_a_profile_does_not_apply_its_container_keeps_its_own_image(self, soc, compatible, nodes):
        # Under the mainline kernel the NPU's driver (rocket, /dev/accel) makes no such link.
        soc.compatible = compatible
        soc.devices = nodes
        learning = _photos().companions[1]
        host = hardware_module.read_host_hardware(hardware_module.all_profiles(_photos()))

        assert hardware_module.container_hardware(learning, host) == hardware_module.ContainerHardware(image=PHOTOS_ML)

    def test_the_catalog_names_each_containers_hardware_and_the_image_it_installs(self, api, soc, in_catalog):
        _rk3588(soc)
        in_catalog(_photos())

        one = api.get("/api/v1/apps/catalog/photos").json()
        listed = next(t for t in api.get("/api/v1/apps/catalog").json() if t["id"] == "photos")

        for answer in (one, listed):
            containers = _containers(answer)
            assert {name: [p["id"] for p in c["hardware"]] for name, c in containers.items()} == {
                "": ["rk3588-video"],
                "database": [],
                "machine-learning": ["rk3588-npu"],
            }
            # The App Store shows the image that the install pulls.
            assert {name: c["image"] for name, c in containers.items()} == {
                "": PHOTOS_SERVER,
                "database": PHOTOS_DB,
                "machine-learning": PHOTOS_ML_NPU,
            }
            assert containers["machine-learning"]["hardware"][0]["image"] == PHOTOS_ML_NPU
        # The host was read once for each request, for every container's profiles.
        assert [cmd[3] for cmd in soc.commands] == ["unos-device-tree", "unos-devices"] * 2

    @pytest.mark.parametrize(
        ("compatible", "nodes", "server"),
        [(None, set(), []), (RK3588, set(RK3588_VIDEO), ["rk3588-video"])],
        ids=["pc", "rk3588-without-the-npu"],
    )
    def test_where_the_npu_does_not_apply_the_catalog_names_the_image_without_it(
        self, api, soc, in_catalog, compatible, nodes, server
    ):
        soc.compatible = compatible
        soc.devices = nodes
        in_catalog(_photos())

        containers = _containers(api.get("/api/v1/apps/catalog/photos").json())

        assert [p["id"] for p in containers[""]["hardware"]] == server
        assert containers["machine-learning"]["hardware"] == []
        assert containers["machine-learning"]["image"] == PHOTOS_ML

    def test_a_template_whose_only_profiles_are_its_companions_reads_the_host(self, api, soc, in_catalog):
        _rk3588(soc)
        in_catalog(_photos(hardware=[]))

        containers = _containers(api.get("/api/v1/apps/catalog/photos").json())

        assert containers["machine-learning"]["image"] == PHOTOS_ML_NPU
        assert len(soc.commands) == 2

    @pytest.mark.parametrize(
        ("compatible", "image"),
        [(RK3588, PHOTOS_SERVER_RK3588), (None, PHOTOS_SERVER)],
        ids=["rk3588", "pc"],
    )
    def test_an_install_pulls_and_creates_the_image_of_the_profile_that_applies(
        self, api, pools, docker_client, low_level, soc, in_catalog, compatible, image
    ):
        soc.compatible = compatible
        soc.devices = set(RK3588_VIDEO)
        in_catalog(_photos(hardware=[VIDEO.model_copy(update={"image": PHOTOS_SERVER_RK3588})], companions=[]))

        response = install(api, {"template_id": "photos"})

        assert response.status_code == 200, response.text
        name_tag, _, digest = image.partition("@")
        by_digest = f"{name_tag.rpartition(':')[0]}@{digest}"
        assert pulled(docker_client) == [by_digest]
        if compatible is None:
            assert docker_client.containers.create.call_args.args == (by_digest,)
        else:
            assert low_level.create_container.call_args.args == (by_digest,)
            assert response.json()["devices"] == RK3588_VIDEO


# ---------------------------------------------------------------------------
# The catalog's apps of several containers and its pinned images (ADR-0381, ADR-0382)
# ---------------------------------------------------------------------------

# A tag that names no build: latest, a channel, a branch or a bare major line (ADR-0410). A pinned
# tag names the version it was pinned at, so that it moves only when its publisher pushes that
# version again, which scripts/catalog-platforms.py tags reports.
FLOATING_TAG = re.compile(
    r"(latest|stable|main|master|develop|dev|nightly|edge|beta|lts|sts|next|rolling|unstable)([-_].*)?|v?\d{1,3}",
    re.IGNORECASE,
)
# Readarr's project is retired and LinuxServer's image of it is deprecated: its tag names an empty
# index, which no host can pull, so it has no image to pin (ADR-0410).
RETIRED_TEMPLATES = ("readarr",)
# The variables of Immich's server that UNOS sets to reach its companions and its password.
IMMICH_WIRING = {
    "DB_HOSTNAME", "DB_USERNAME", "DB_DATABASE_NAME", "DB_PASSWORD_FILE", "REDIS_HOSTNAME",
    "IMMICH_MACHINE_LEARNING_URL",
}


def _images(spec) -> list[str]:
    """The images that a container runs: its own, and that of a hardware profile that names one."""
    return [spec.image, *(profile.image for profile in spec.hardware if profile.image)]


def _companion(template: AppTemplate, name: str) -> Companion:
    return next(companion for companion in template.companions if companion.name == name)


def _with_companions() -> list[AppTemplate]:
    return [template for template in APP_CATALOG if template.companions]


class TestStackCatalog:
    def test_immich_is_the_catalogs_app_of_several_containers(self):
        immich = get_app_by_id("immich")

        # And BookStack, Paperless-ngx and Linkwarden with their database or broker (ADR-0415).
        assert sorted(template.id for template in _with_companions()) == [
            "bookstack", "immich", "linkwarden", "paperless-ngx",
        ]
        assert [companion.name for companion in immich.companions] == ["database", "redis", "machine-learning"]
        assert immich.depends_on == ["database", "redis"]
        assert stacks_module.container_names(immich) == [
            "immich-database", "immich-redis", "immich-machine-learning", "immich",
        ]

    def test_companions_are_named_for_the_network_and_publish_nothing(self):
        # A companion has no ports, privileges, capabilities or network mode to set: it runs on its
        # app's network, which the main container joins too.
        assert not {"ports", "privileged", "capabilities", "network_mode"} & set(Companion.model_fields)
        for template in _with_companions():
            names = [companion.name for companion in template.companions]
            assert len(set(names)) == len(names), template.id
            assert all(re.fullmatch(COMPANION_NAME, name) for name in names), names
            assert set(template.depends_on) <= set(names), template.id
            assert template.network_mode is None, template.id
        # Docker names: none is hidden as a container of UNOS's own (is_system_container).
        names = [name for template in APP_CATALOG for name in stacks_module.container_names(template)]
        assert not [name for name in names if name.startswith("unos-")]
        assert len(set(names)) == len(names)

    def test_a_companion_keeps_its_data_in_its_apps_folder(self):
        for template in _with_companions():
            for companion in template.companions:
                for volume in companion.volumes:
                    assert volume.on_pool, (template.id, companion.name, volume.host)
                    assert volume.host.startswith(f"{APPS_DATA_PATH}/{template.id}/"), (companion.name, volume.host)

    def test_a_generated_password_is_a_read_only_file_beside_the_data_it_unlocks(self):
        secrets = 0
        for template in APP_CATALOG:
            containers = (template, *template.companions)
            hosts = {volume.host for spec in containers for volume in spec.volumes}
            unlocks: dict[str, str | None] = {}
            for spec in containers:
                for volume in spec.volumes:
                    if not volume.generated_secret:
                        assert volume.secret_for is None, (template.id, volume.host)
                        continue
                    secrets += 1
                    assert volume.is_file and volume.readonly, (template.id, volume.host)
                    assert volume.host.startswith(f"{APPS_DATA_PATH}/{template.id}/"), volume.host
                    assert volume.secret_for in hosts and volume.secret_for != volume.host, volume.secret_for
                    # Every mapping of one file names the same data, and every container that
                    # binds it names its path in a *_FILE variable, or LinuxServer's FILE__ one
                    # (ADR-0415): the value is in no variable.
                    assert unlocks.setdefault(volume.host, volume.secret_for) == volume.secret_for, volume.host
                    assert volume.container in {
                        var.value for var in spec.environment
                        if var.name.endswith("_FILE") or var.name.startswith("FILE__")
                    }, (template.id, volume.host)
            # An app of several containers asks the administrator for no secret.
            if template.companions:
                assert not [var.name for spec in containers for var in spec.environment if var.secret], template.id
        # Immich's password file, bound into its server and its database (2); BookStack's key (1),
        # database password (2) and root password (1); Paperless-ngx's key (1); Linkwarden's
        # database password (2) and session secret (1) (ADR-0415).
        assert secrets == 10

    def test_pinned_images_are_named_by_tag_and_digest(self):
        pins = catalog_module.PINNED_IMAGES
        for ref, pin in pins.items():
            assert ref == pin.ref == f"{pin.name}:{pin.tag}@{pin.digest}"
            assert PINNED_IMAGE.fullmatch(ref), ref
            # Its size on the system disk, for the check of the free space before a pull.
            assert pin.disk_bytes > 0, ref
        # Each pin is an image that a container of the catalog runs.
        assert set(pins) <= {image for spec in _catalog_containers() for image in _images(spec)}

    def test_every_image_of_the_catalog_is_pinned(self):
        pins = catalog_module.PINNED_IMAGES
        for template in APP_CATALOG:
            images = [image for spec in (template, *template.companions) for image in _images(spec)]
            assert all(image in pins for image in images), (template.id, images)
        # No list of templates that float is left: none may (ADR-0410).
        assert not hasattr(catalog_module, "UNPINNED_TEMPLATES")

    def test_a_pinned_tag_names_a_version_not_a_channel(self):
        for ref, pin in catalog_module.PINNED_IMAGES.items():
            assert not FLOATING_TAG.fullmatch(pin.tag), ref

    @pytest.mark.parametrize("ref", ["example/notes:latest", "example/notes", "example/notes@sha256:" + "a" * 64])
    def test_the_install_has_no_path_for_an_image_that_is_not_pinned_by_tag_and_digest(self, ref):
        # The pull, the container's image and the space check handle name:tag@sha256 alone
        # (ADR-0410): a floating tag is never pulled as latest.
        with pytest.raises(ValueError, match="is not pinned"):
            apps_router._pinned(ref)
        with pytest.raises(ValueError, match="is not pinned"):
            apps_router._by_digest(ref)
        assert apps_router._pinned(_pin("example/notes:1")) == (
            "example/notes", "1", _pin("example/notes:1").partition("@")[2])

    def test_an_app_of_one_container_runs_the_image_of_its_version(self):
        images = catalog_module.APP_IMAGES
        for template in APP_CATALOG:
            if template.companions:
                continue
            pin = images[template.id]
            assert template.image == pin.ref, template.id
            # The version that the App Store shows, the label unos.app.version and an update's
            # target (ADR-0408) are the pinned tag.
            assert template.version == pin.tag, template.id
        assert set(images) == {template.id for template in APP_CATALOG if not template.companions}

    def test_every_volume_that_a_pinned_image_declares_is_on_the_pool_or_in_ram(self):
        # Docker would make any other VOLUME an anonymous volume in its data root on the system
        # disk; the install refuses such an image after its pull (ADR-0381), so a template that
        # left one out could not be installed at all.
        pins = catalog_module.PINNED_IMAGES
        for template in APP_CATALOG:
            for spec in (template, *template.companions):
                targets = [volume.container for volume in spec.volumes if volume.on_pool] + list(spec.tmpfs)
                for image in _images(spec):
                    for path in pins[image].volumes:
                        assert any(apps_router._is_within(path, target) for target in targets), (template.id, image, path)

    def test_the_volumes_an_app_declares_are_recorded_for_the_images_that_declare_them(self):
        # The registries' configurations of 2026-11-18 that the templates' binds rest on: Homarr's
        # database folder and LocalAI's backends and configuration are on the pool for them.
        declare = {t.id for t in APP_CATALOG if not t.companions and catalog_module.APP_IMAGES[t.id].volumes}
        assert {"homarr", "localai", "mealie", "plex", "jellyfin", "transmission"} <= declare
        assert catalog_module.APP_IMAGES["homarr"].volumes == ("/app/data/configs", "/data")
        assert catalog_module.APP_IMAGES["localai"].volumes == ("/backends", "/configuration", "/models")
        assert catalog_module.IMMICH_POSTGRES.volumes == ("/var/lib/postgresql/data",)
        assert catalog_module.IMMICH_SERVER.volumes == ("/data",)

    def test_immichs_pins_are_the_digests_its_install_runs(self):
        # The dev device runs Immich from these digests and is never updated (apps.update-in-place):
        # only Valkey's tag changed, to the version tag that names the same index.
        assert {pin.digest for pin in (catalog_module.IMMICH_SERVER, catalog_module.IMMICH_ML,
                                       catalog_module.IMMICH_ML_RKNN, catalog_module.IMMICH_POSTGRES,
                                       catalog_module.VALKEY)} == {
            "sha256:7c489add298fc2f92cda4c777de6462aa206877e04087b815f62c7664af0b53b",
            "sha256:8adebf414256f6e4f837bfbb66fa94f14f4da7260e3a1d65cbba49e4e35d8764",
            "sha256:b7baeb12cab0093803f09f0299b7c863bed46f36349c532096f40bfbf4f10c84",
            "sha256:bcf63357191b76a916ae5eb93464d65c07511da41e3bf7a8416db519b40b1c23",
            "sha256:418652cfb58ef879d4978c33553735d7147016032d5aefaa14c828e611eb9dfd",
        }
        assert catalog_module.VALKEY.ref == (
            "docker.io/valkey/valkey:9.1.2@sha256:418652cfb58ef879d4978c33553735d7147016032d5aefaa14c828e611eb9dfd"
        )

    def test_immichs_server_and_machine_learning_are_one_version(self):
        immich = get_app_by_id("immich")
        learning = _companion(immich, "machine-learning")

        assert catalog_module.IMMICH_SERVER.tag == catalog_module.IMMICH_ML.tag == catalog_module.IMMICH_VERSION
        assert immich.version == catalog_module.IMMICH_VERSION
        assert catalog_module.IMMICH_ML_RKNN.tag == f"{catalog_module.IMMICH_VERSION}-rknn"
        assert catalog_module.IMMICH_ML_RKNN.name == catalog_module.IMMICH_ML.name
        assert immich.image == catalog_module.IMMICH_SERVER.ref
        assert _companion(immich, "database").image == catalog_module.IMMICH_POSTGRES.ref
        assert _companion(immich, "redis").image == catalog_module.VALKEY.ref
        assert learning.image == catalog_module.IMMICH_ML.ref
        assert [profile.image for profile in learning.hardware] == [catalog_module.IMMICH_ML_RKNN.ref]

    def test_immichs_server_logs_into_the_database_that_its_install_sets_up(self):
        immich = get_app_by_id("immich")
        server = {var.name: var for var in immich.environment}
        database = {var.name: var.value for var in _companion(immich, "database").environment}

        assert server["DB_USERNAME"].value == database["POSTGRES_USER"]
        assert server["DB_DATABASE_NAME"].value == database["POSTGRES_DB"]
        assert server["DB_PASSWORD_FILE"].value == database["POSTGRES_PASSWORD_FILE"]
        # Its companions answer to their names on the app's network.
        assert (server["DB_HOSTNAME"].value, server["REDIS_HOSTNAME"].value) == ("database", "redis")
        assert server["IMMICH_MACHINE_LEARNING_URL"].value == "http://machine-learning:3003"
        assert {name for name, var in server.items() if not var.editable} == IMMICH_WIRING
        # The database's folder is on an HDD pool; the models' files at v2 include the RK3588's OCR.
        assert database["DB_STORAGE_TYPE"] == "HDD"
        learning = {var.name: var.value for var in _companion(immich, "machine-learning").environment}
        assert learning == {"MACHINE_LEARNING_MODEL_REVISION": "v2"}

    def test_immichs_machine_learning_gets_the_npu_and_its_server_the_video_hardware(self):
        immich = get_app_by_id("immich")

        assert catalog_module.RK3588_NPU == NPU
        assert immich.hardware == [catalog_module.RK3588_VIDEO]
        assert _companion(immich, "machine-learning").hardware == [
            NPU.model_copy(update={"image": catalog_module.IMMICH_ML_RKNN.ref}),
        ]
        assert _companion(immich, "database").hardware == _companion(immich, "redis").hardware == []

    @pytest.mark.parametrize(
        ("compatible", "nodes", "server", "npu"),
        [
            (RK3588, {*RK3588_VIDEO, NPU_LINK}, ["rk3588-video"], True),
            (RK3588, set(RK3588_VIDEO), ["rk3588-video"], False),
            (None, set(), [], False),
        ],
        ids=["rk3588", "rk3588-without-the-npu", "pc"],
    )
    def test_the_catalog_offers_immich_the_hardware_of_this_host(self, api, soc, compatible, nodes, server, npu):
        soc.compatible = compatible
        soc.devices = nodes

        containers = _containers(api.get("/api/v1/apps/catalog/immich").json())

        assert [p["id"] for p in containers[""]["hardware"]] == server
        assert [p["id"] for p in containers["machine-learning"]["hardware"]] == (["rk3588-npu"] if npu else [])
        assert containers["machine-learning"]["image"] == (
            catalog_module.IMMICH_ML_RKNN.ref if npu else catalog_module.IMMICH_ML.ref
        )
        assert containers["database"]["hardware"] == containers["redis"]["hardware"] == []
        assert containers[""]["image"] == catalog_module.IMMICH_SERVER.ref

    @pytest.mark.parametrize("app_id", RETIRED_TEMPLATES)
    def test_the_apps_that_lack_an_image_are_not_in_the_catalog(
        self, api, pools, docker_client, app_id
    ):
        assert get_app_by_id(app_id) is None
        assert app_id not in catalog_module.APP_IMAGES
        assert app_id not in {template["id"] for template in api.get("/api/v1/apps/catalog").json()}
        assert api.get(f"/api/v1/apps/catalog/{app_id}").status_code == 404

        response = install(api, {"template_id": app_id})

        assert response.status_code == 404
        docker_client.api.post.assert_not_called()


# ---------------------------------------------------------------------------
# The free space of the system disk before a pull (ADR-0381)
# ---------------------------------------------------------------------------

FREE_SPACE_FLOOR = 2 * GIB
NO_SPACE_REMEDY = "Remove unused images on Containers > Images first."


def _unread_free_space(reason: str) -> dict:
    return {"detail": f"Could not read the free space of the system disk: {reason}; nothing was pulled"}


class TestSystemDiskSpace:
    def test_every_install_reads_the_free_space_where_docker_keeps_images(self, api, pools, docker_client, soc):
        docker_client.info.return_value = {"DockerRootDir": "/srv/docker"}
        soc.free = {"/srv/docker": 64 * GIB, CONTAINERD_ROOT: 64 * GIB}

        response = install(api, {"template_id": "tailscale"})

        assert response.status_code == 200, response.text
        # Docker's root folder as Docker names it, and the containerd image store's folder.
        assert soc.commands == [_free_space_read("/srv/docker")]

    @pytest.mark.parametrize("lower", [DOCKER_ROOT, CONTAINERD_ROOT])
    def test_the_lower_free_space_of_the_two_counts(self, api, pools, docker_client, soc, lower):
        soc.free = {DOCKER_ROOT: 64 * GIB, CONTAINERD_ROOT: 64 * GIB, lower: FREE_SPACE_FLOOR - GIB // 2}

        response = install(api, {"template_id": "jellyfin"})

        need = preflight_module._gib(catalog_module.APP_IMAGES["jellyfin"].disk_bytes)
        assert (response.status_code, response.json()) == (409, {
            "detail": f"Jellyfin's images need about {need} on the system disk, which has 1.5 GiB free; at least "
            "2 GiB must stay free. " + NO_SPACE_REMEDY
        })
        docker_client.api.post.assert_not_called()
        docker_client.containers.create.assert_not_called()
        assert not (pools.alpha / "apps").exists()

    def test_a_pinned_image_that_docker_lacks_counts_its_estimate(self, api, pools, docker_client, soc):
        pin = catalog_module.APP_IMAGES["jellyfin"]
        soc.free = {DOCKER_ROOT: FREE_SPACE_FLOOR + pin.disk_bytes - 1, CONTAINERD_ROOT: 64 * GIB}

        response = install(api, {"template_id": "jellyfin"})

        assert response.status_code == 409, response.text
        assert "Jellyfin's images need about" in response.json()["detail"]
        docker_client.api.post.assert_not_called()

    def test_a_folder_that_the_host_lacks_is_left_out(self, api, pools, docker_client, soc):
        # Docker's own image store keeps everything in its root folder: no /var/lib/containerd.
        soc.free = {DOCKER_ROOT: 64 * GIB}

        response = install(api, {"template_id": "jellyfin"})

        assert response.status_code == 200, response.text

    def test_a_host_without_either_folder_is_logged_and_the_install_goes_on(
        self, api, pools, docker_client, soc, caplog
    ):
        # A supervisor that does not run on Docker's host, as in the compose test stack
        # (UNOS_HOST_EXEC=local in a container that has Docker's socket but not its folders):
        # there is no file system to read, and Docker's own pull is the last check.
        pools.mounted.clear()
        soc.free = {}

        with caplog.at_level("WARNING", logger="src.apps.preflight"):
            response = install(api, {"template_id": "dozzle"})

        assert response.status_code == 200, response.text
        assert [cmd for cmd in soc.commands if _reads_free_space(cmd)] == [_free_space_read()]
        assert [r.getMessage() for r in caplog.records if r.name == "src.apps.preflight"] == [
            f"Could not read the free space of the system disk: the host has none of {DOCKER_ROOT}, "
            f"{CONTAINERD_ROOT}; the install goes on"
        ]
        docker_client.api.post.assert_called_once()
        docker_client.containers.create.return_value.start.assert_called_once()

    @pytest.mark.parametrize(("df", "reason"), [
        (HostResult(1, "", "df: /var/lib/docker: Input/output error\n"), "df: /var/lib/docker: Input/output error"),
        (HostResult(0, "", ""), "df printed no free space"),
        (subprocess.TimeoutExpired(["nsenter"], 15), "Command '['nsenter']' timed out after 15 seconds"),
        (FileNotFoundError(2, "No such file or directory", "nsenter"), "[Errno 2] No such file or directory: 'nsenter'"),
    ], ids=["exit-status", "no-output", "timeout", "oserror"])
    def test_a_free_space_that_cannot_be_read_answers_500_before_any_pull(
        self, api, pools, docker_client, soc, df, reason
    ):
        soc.df = df

        response = install(api, {"template_id": "jellyfin"})

        assert (response.status_code, response.json()) == (500, _unread_free_space(reason))
        docker_client.api.post.assert_not_called()
        assert not (pools.alpha / "apps").exists()

    @pytest.mark.parametrize("info", [{}, {"DockerRootDir": ""}, {"DockerRootDir": "var/lib/docker"}],
                             ids=["none", "empty", "relative"])
    def test_a_docker_that_names_no_root_folder_answers_500(self, api, pools, docker_client, soc, info):
        docker_client.info.return_value = info

        response = install(api, {"template_id": "jellyfin"})

        assert (response.status_code, response.json()) == (500, _unread_free_space("Docker names no root folder"))
        assert not [cmd for cmd in soc.commands if _reads_free_space(cmd)]
        docker_client.api.post.assert_not_called()

    def test_a_docker_whose_info_fails_answers_500(self, api, pools, docker_client, soc):
        docker_client.info.side_effect = APIError("500 Server Error: Internal Server Error")

        response = install(api, {"template_id": "jellyfin"})

        assert (response.status_code, response.json()) == (
            500, _unread_free_space("500 Server Error: Internal Server Error")
        )
        docker_client.api.post.assert_not_called()

    def test_an_install_refused_before_reads_no_free_space(self, api, pools, docker_client, soc):
        soc.sockets = ['tcp   LISTEN 0      4096   0.0.0.0:8096   0.0.0.0:*   users:(("EmbyServer",pid=8,fd=7))']

        response = install(api, {"template_id": "jellyfin"})

        assert response.status_code == 409
        assert soc.commands == [SS]
        docker_client.info.assert_not_called()

    def test_the_script_skips_a_folder_that_is_not_there(self, tmp_path):
        # The script as the host runs it, on this machine's sh and df.
        result = subprocess.run(
            ["/bin/sh", "-c", preflight_module.FREE_SPACE_SCRIPT, "unos-free-space", str(tmp_path), str(tmp_path / "x")],
            capture_output=True, text=True, check=False,
        )

        assert (result.returncode, result.stderr) == (0, "")
        [free] = preflight_module._available(result.stdout)
        assert abs(free - shutil.disk_usage(tmp_path).free) < GIB // 4

    def test_the_script_reads_nothing_where_the_host_has_neither_folder(self, monkeypatch, tmp_path):
        # The script as the host runs it, through src/host.py on this machine's sh.
        monkeypatch.setattr(host_module, "run_on_host", REAL_RUN_ON_HOST)
        monkeypatch.setenv("UNOS_HOST_EXEC", "local")

        assert preflight_module._free_bytes([str(tmp_path / "docker"), str(tmp_path / "containerd")]) is None

    def test_a_df_that_fails_for_one_folder_answers_500_though_another_is_read(self, monkeypatch, tmp_path):
        # df fails for Docker's root folder (an I/O error on the system disk) and reads the
        # containerd image store: the lower free space is not known, so none is used.
        monkeypatch.setattr(host_module, "run_on_host", REAL_RUN_ON_HOST)
        monkeypatch.setenv("UNOS_HOST_EXEC", "local")
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "df").write_text(
            "#!/bin/sh\n"
            'case "$*" in */docker) echo "df: $4: Input/output error" >&2; exit 1;; esac\n'
            "printf 'Filesystem 1024-blocks Used Available Capacity Mounted on\\n'\n"
            "printf '/dev/sda1 67110912 2048 67108864 1%% /\\n'\n"
        )
        (bin_dir / "df").chmod(0o755)
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
        (tmp_path / "docker").mkdir()
        (tmp_path / "containerd").mkdir()
        # The stub reads the containerd store when it is the only folder.
        assert preflight_module._free_bytes([str(tmp_path / "containerd")]) == 64 * GIB

        with pytest.raises(HTTPException) as raised:
            preflight_module._free_bytes([str(tmp_path / "docker"), str(tmp_path / "containerd")])

        assert (raised.value.status_code, raised.value.detail) == (
            500, _unread_free_space(f"df: {tmp_path / 'docker'}: Input/output error")["detail"]
        )

    def test_a_file_system_whose_name_or_folder_holds_spaces_is_read(self):
        listing = (
            "Filesystem   1024-blocks      Used Available Capacity  Mounted on\n"
            "map auto_home          0         0         0   100%    /System/Volumes/Data/home\n"
            "//nas/a share        100        50        50    50%    /mnt/a 75% b\n"
        )

        assert preflight_module._available(listing) == [0, 50 * 1024]
