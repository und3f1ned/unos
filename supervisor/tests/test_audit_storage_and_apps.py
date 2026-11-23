"""Changes of pools, volumes, snapshots, containers and apps are in the audit log
(security.audit-admin-actions, steps 1 and 2; ADR-0350).

Creating and deleting pools and volumes, creating, deleting and restoring snapshots, starting,
stopping, restarting and removing containers (on the Containers page and on the Apps page), and
installing and uninstalling apps each write one entry of their action, with the administrator,
the client address and browser of the request and the resource, after the change was made. A
refused or failed request, and one that changed nothing (a pool that was not there to remove, a
start of a running container, a stop of a stopped one), write none (ADR-0320: entries record
real changes). A request that made part of its change and then failed writes its action as
failed, with what it did (a pool create that formatted the disks, a removal that unmounted the
pool, an uninstall that stopped the app). An entry that cannot be written, as on a full /data,
is in the supervisor's log, and the change, already made, answers as it does otherwise
(ADR-0251, ADR-0343).
"""
from __future__ import annotations

import importlib
import json
import logging
import subprocess
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from docker.errors import APIError, NotFound
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.audit import router as audit_router
from src.auth.utils import verify_token
from src import host as host_module
from src.containers import docker as docker_module
from src.database import Base, get_db
from src.host import HostResult
from src.main import app
from src.storage import pools
from src.storage import router as storage_router

from .audit_helpers import audit_entries
from .test_apps import install, pull_answer
from .test_containers import _container, answer_docker_lists
from .test_pool_persistence import DATA_UUID
from .test_sign_in_full_disk import ENTRY_LOST, the_audit_log_is_full
from . import test_volumes
from .test_snapshots import FakeBtrfs

# The package re-exports its APIRouter as ``router``; import the modules.
apps_router = importlib.import_module("src.apps.router")
snapshots_router = importlib.import_module("src.snapshots.router")

# The fixture of test_volumes: the FakeHost with pool "data" mounted and its subvolumes, and pool
# "media" not mounted.
host = test_volumes.host

CLIENT = "192.0.2.30"  # the address the test client connects from
BROWSER = "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0"
ADMIN = "carol"
FIELDS = ("action", "username", "ip_address", "user_agent", "resource_type", "resource_id", "details",
          "severity", "success")
POOLS = "/api/v1/storage/pools"
SNAPSHOTS = "/api/v1/snapshots"
CONTAINERS = "/api/v1/containers"
APPS = "/api/v1/apps"


def entry(action: str, resource_type: str, resource_id: str, details: str, severity: str = "info",
          success: bool = True) -> dict:
    """An entry of the administrator ADMIN, from CLIENT with BROWSER, as the audit log keeps it."""
    return {"action": action, "username": ADMIN, "ip_address": CLIENT, "user_agent": BROWSER,
            "resource_type": resource_type, "resource_id": resource_id, "details": details,
            "severity": severity, "success": success}


def picked(record: dict) -> dict:
    return {field: record.get(field) for field in FIELDS}


@pytest.fixture
def audit_file(tmp_path_factory, monkeypatch) -> Path:
    """The audit log of the test, outside tmp_path, whose tree some fixtures compare."""
    path = tmp_path_factory.mktemp("audit") / "audit.jsonl"
    monkeypatch.setattr(audit_router, "AUDIT_FILE", str(path))
    return path


@pytest.fixture
def admin(audit_file) -> Iterator[TestClient]:
    """The API as the administrator ADMIN, from the address CLIENT and the browser BROWSER."""
    previous = app.dependency_overrides.get(verify_token)
    app.dependency_overrides[verify_token] = lambda: SimpleNamespace(
        id=1, username=ADMIN, is_admin=True, is_active=True
    )
    storage_router.limiter.reset()
    try:
        yield TestClient(app, client=(CLIENT, 50000), headers={"User-Agent": BROWSER})
    finally:
        storage_router.limiter.reset()
        if previous is None:
            app.dependency_overrides.pop(verify_token, None)
        else:
            app.dependency_overrides[verify_token] = previous


def entries(audit_file: Path) -> list[dict]:
    return [picked(record) for record in audit_entries(audit_file)]


def lost(caplog) -> list[dict]:
    """The entries that the supervisor's log reports in place of the audit log (ADR-0251)."""
    found = []
    for record in caplog.records:
        message = record.getMessage()
        if record.levelno >= logging.ERROR and message.startswith(ENTRY_LOST):
            found.append(picked(json.JSONDecoder().raw_decode(message, len(ENTRY_LOST))[0]))
    return found


# ---------------------------------------------------------------------------
# Pools and volumes (step 1)
# ---------------------------------------------------------------------------


class TestPools:
    def test_creating_a_pool_writes_pool_created(self, admin, audit_file, host):
        host.add_disk("sdc", serial="WD-3")

        response = admin.post(POOLS, json={"name": "tank", "devices": ["/dev/sdc"], "raid_level": "single",
                                           "confirm_name": "tank"})

        assert response.status_code == 200, response.text
        assert entries(audit_file) == [entry("pool_created", "pool", "tank", "Created the pool tank (single) on /dev/sdc")]

    def test_a_refused_pool_writes_nothing(self, admin, audit_file, host):
        # /dev/sda holds the mounted pool "data".
        response = admin.post(POOLS, json={"name": "tank", "devices": ["/dev/sda"], "raid_level": "single",
                                           "confirm_name": "tank"})

        assert response.status_code == 409, response.text
        assert entries(audit_file) == []

    def test_removing_a_pool_writes_pool_deleted(self, admin, audit_file, host):
        response = admin.delete(f"{POOLS}/data")

        assert response.status_code == 200, response.text
        assert not host.mounted("/mnt/pools/data")
        assert entries(audit_file) == [
            entry("pool_deleted", "pool", "data", "Removed the pool data; its disks and their data were kept", "warning")
        ]

    def test_a_create_that_fails_after_formatting_writes_a_failed_entry(self, admin, audit_file, host):
        # mkfs.btrfs formatted the disk, then the mount failed: it wrote no entry.
        host.add_disk("sdc", serial="WD-3")
        host.fail[("mount",)] = (32, "mount: /mnt/pools/tank: wrong fs type")

        response = admin.post(POOLS, json={"name": "tank", "devices": ["/dev/sdc"], "raid_level": "single",
                                           "confirm_name": "tank"})

        assert response.status_code == 500, response.text
        assert response.json()["detail"] == "Mounting /mnt/pools/tank failed: mount: /mnt/pools/tank: wrong fs type"
        assert entries(audit_file) == [
            entry("pool_created", "pool", "tank", "Formatted /dev/sdc for the pool tank (single); then: Mounting "
                  "/mnt/pools/tank failed: mount: /mnt/pools/tank: wrong fs type", success=False)
        ]

    def test_removing_a_pool_that_is_not_mounted_writes_pool_deleted(self, admin, audit_file, host):
        # "media" is not mounted: the removal deletes its empty mountpoint.
        response = admin.delete(f"{POOLS}/media")

        assert response.status_code == 200, response.text
        assert "/mnt/pools/media" not in host.dirs
        assert entries(audit_file) == [
            entry("pool_deleted", "pool", "media", "Removed the pool media; its disks and their data were kept",
                  "warning")
        ]

    @pytest.mark.parametrize("failure", ["fstab", "mount again"])
    def test_a_removal_that_fails_after_unmounting_writes_a_failed_entry(
        self, admin, audit_file, host, monkeypatch, failure
    ):
        # The pool was unmounted, then the removal failed: it wrote no entry.
        if failure == "fstab":
            def write_fails(new_text, old_text):
                raise RuntimeError("Writing /etc/fstab failed: No space left on device")

            monkeypatch.setattr(pools, "_write_fstab", write_fails)
            status, error = 500, "Writing /etc/fstab failed: No space left on device"
        else:
            # Still in use elsewhere after the umount, and mounting it again fails.
            host.held.add(DATA_UUID)
            host.fail[("mount",)] = (32, "mount: /mnt/pools/data: wrong fs type")
            status, error = 409, (
                "/mnt/pools/data was unmounted on the host, but the filesystem is still in use elsewhere "
                "(for example by a container's bind mount), and mounting it again failed: Mounting "
                "/mnt/pools/data failed: mount: /mnt/pools/data: wrong fs type. Stop the apps and "
                "containers that use the pool before removing its disks."
            )

        response = admin.delete(f"{POOLS}/data")

        assert (response.status_code, response.json()["detail"]) == (status, error)
        assert not host.mounted("/mnt/pools/data")
        assert entries(audit_file) == [
            entry("pool_deleted", "pool", "data", f"Unmounted the pool data; then: {error}", "warning", success=False)
        ]

    def test_removing_a_pool_that_is_not_there_writes_nothing(self, admin, audit_file, host):
        response = admin.delete(f"{POOLS}/nothing")

        assert (response.status_code, response.json()) == (200, {"status": "deleted"})
        assert host.changes == []
        assert entries(audit_file) == []

    def test_a_pool_in_use_is_not_removed_and_writes_nothing(self, admin, audit_file, host):
        host.busy.add("/mnt/pools/data")

        response = admin.delete(f"{POOLS}/data")

        assert response.status_code == 409, response.text
        assert entries(audit_file) == []

    def test_creating_and_deleting_a_volume_write_their_entries(self, admin, audit_file, host):
        created = admin.post(f"{POOLS}/data/volumes", json={"name": "documents"})
        assert created.status_code == 200, created.text
        host.dirs.add("/mnt/pools/data/documents")
        deleted = admin.delete(f"{POOLS}/data/volumes/documents")

        assert deleted.status_code == 200, deleted.text
        assert host.subvols.paths(DATA_UUID) == []
        assert entries(audit_file) == [
            entry("volume_created", "volume", "data/documents", "Created the volume documents in the pool data"),
            entry("volume_deleted", "volume", "data/documents",
                  "Deleted the volume documents of the pool data with everything in it", "warning"),
        ]

    def test_a_volume_with_a_quota_says_so(self, admin, audit_file, host):
        host.subvols.quotas.add(DATA_UUID)

        response = admin.post(f"{POOLS}/data/volumes", json={"name": "photos", "quota": 1 << 30})

        assert response.status_code == 200, response.text
        assert entries(audit_file) == [
            entry("volume_created", "volume", "data/photos",
                  "Created the volume photos in the pool data, with a quota of 1073741824 bytes")
        ]

    def test_a_volume_kept_without_its_quota_writes_a_failed_entry(self, admin, audit_file, host):
        # The volume was created, then the list after the create did not answer: it wrote no entry.
        host.subvols.quotas.add(DATA_UUID)

        def hangs(cmd):
            if cmd[1:3] == ["subvolume", "list"]:
                raise subprocess.TimeoutExpired(cmd, pools.VOLUME_TIMEOUT)

        test_volumes.TestCreateRaces.after_the_create(host, hangs)

        response = admin.post(f"{POOLS}/data/volumes", json={"name": "documents", "quota": 1 << 30})

        error = (
            f"Volume 'documents' was created without its quota: btrfs did not answer within "
            f"{pools.VOLUME_TIMEOUT} seconds. Delete it and create it again."
        )
        assert (response.status_code, response.json()["detail"]) == (500, error)
        assert host.subvols.paths(DATA_UUID) == ["documents"]
        assert entries(audit_file) == [
            entry("volume_created", "volume", "data/documents",
                  f"Created the volume documents in the pool data; then: {error}", success=False)
        ]

    @pytest.mark.parametrize(("method", "url", "status"), [
        ("post", f"{POOLS}/media/volumes", 409),  # the pool is not mounted
        ("delete", f"{POOLS}/data/volumes/nope", 404),
    ])
    def test_a_refused_volume_change_writes_nothing(self, admin, audit_file, host, method, url, status):
        kwargs = {"json": {"name": "documents"}} if method == "post" else {}

        response = getattr(admin, method)(url, **kwargs)

        assert response.status_code == status, response.text
        assert entries(audit_file) == []

    def test_a_volume_whose_entry_cannot_be_written_is_created_and_answers(
        self, admin, audit_file, host, monkeypatch, caplog
    ):
        the_audit_log_is_full(monkeypatch)

        response = admin.post(f"{POOLS}/data/volumes", json={"name": "documents"})

        assert response.status_code == 200, response.text
        assert host.changes == [["btrfs", "subvolume", "create", "/mnt/pools/data/documents"]]
        assert lost(caplog) == [
            entry("volume_created", "volume", "data/documents", "Created the volume documents in the pool data")
        ]


# ---------------------------------------------------------------------------
# Snapshots (step 1)
# ---------------------------------------------------------------------------


@pytest.fixture
def btrfs(tmp_path, monkeypatch) -> Iterator[FakeBtrfs]:
    """btrfs-progs faked (FakeBtrfs of test_snapshots) on the pool ``btrfs.pool`` of a pools base."""
    base = tmp_path.resolve() / "pools"
    pool = base / "datapool"
    pool.mkdir(parents=True)
    fake = FakeBtrfs(pool)
    fake.pool = pool
    # The time that the name of a restored copy gets.
    monkeypatch.setattr(snapshots_router, "utc_stamp", lambda: "20261112_093000")
    with patch("src.security.validators.settings", SimpleNamespace(pools_base_path=str(base))), \
            patch("src.snapshots.router.subprocess.run", side_effect=fake.run):
        yield fake


class TestSnapshots:
    def test_creating_a_snapshot_writes_snapshot_created(self, admin, audit_file, btrfs):
        response = admin.post(f"{SNAPSHOTS}/", json={"subvolume": str(btrfs.pool), "name": "before"})

        assert response.status_code == 200, response.text
        assert (btrfs.pool / ".snapshots" / "before").is_dir()
        assert entries(audit_file) == [
            entry("snapshot_created", "snapshot", f"{btrfs.pool}/.snapshots/before",
                  f"Created the read-only snapshot before of {btrfs.pool}")
        ]

    def test_deleting_a_snapshot_writes_snapshot_deleted(self, admin, audit_file, btrfs):
        snap = btrfs.snapshot(btrfs.pool, "before")

        response = admin.delete(f"{SNAPSHOTS}/before", params={"subvolume": str(btrfs.pool)})

        assert response.status_code == 200, response.text
        assert btrfs.deleted == [snap]
        assert entries(audit_file) == [
            entry("snapshot_deleted", "snapshot", str(snap), f"Deleted the snapshot before of {btrfs.pool}", "warning")
        ]

    def test_restoring_a_snapshot_writes_snapshot_restored(self, admin, audit_file, btrfs):
        snap = btrfs.snapshot(btrfs.pool, "before")

        response = admin.post(f"{SNAPSHOTS}/restore", json={"snapshot_path": str(snap)})

        assert response.status_code == 200, response.text
        copy = btrfs.pool / "restore_20261112_093000"
        assert response.json()["target"] == str(copy)
        assert entries(audit_file) == [
            entry("snapshot_restored", "snapshot", str(snap), f"Restored the snapshot as the writable copy {copy}")
        ]

    def test_refused_and_failed_snapshot_changes_write_nothing(self, admin, audit_file, btrfs):
        btrfs.snapshot(btrfs.pool, "before")

        taken = admin.post(f"{SNAPSHOTS}/", json={"subvolume": str(btrfs.pool), "name": "before"})
        missing = admin.delete(f"{SNAPSHOTS}/nope", params={"subvolume": str(btrfs.pool)})
        no_copy = admin.post(f"{SNAPSHOTS}/restore", json={"snapshot_path": f"{btrfs.pool}/.snapshots/nope"})
        btrfs.state["fail"] = True
        failed = admin.post(f"{SNAPSHOTS}/", json={"subvolume": str(btrfs.pool), "name": "after"})

        assert [r.status_code for r in (taken, missing, no_copy, failed)] == [400, 404, 404, 500]
        assert entries(audit_file) == []

    def test_a_snapshot_whose_entry_cannot_be_written_is_made_and_answers(
        self, admin, audit_file, btrfs, monkeypatch, caplog
    ):
        the_audit_log_is_full(monkeypatch)

        response = admin.post(f"{SNAPSHOTS}/", json={"subvolume": str(btrfs.pool), "name": "before"})

        assert response.status_code == 200, response.text
        assert (btrfs.pool / ".snapshots" / "before").is_dir()
        assert lost(caplog) == [
            entry("snapshot_created", "snapshot", f"{btrfs.pool}/.snapshots/before",
                  f"Created the read-only snapshot before of {btrfs.pool}")
        ]


# ---------------------------------------------------------------------------
# Containers and apps (step 2)
# ---------------------------------------------------------------------------


@pytest.fixture
def docker(monkeypatch):
    """A mocked Docker client of the Containers and the Apps pages that knows the containers passed
    to ``add``, by name; a container that ``containers.create`` makes is added too."""
    by_name: dict[str, MagicMock] = {}
    client = MagicMock(name="docker")
    client.info.return_value = {"DockerRootDir": "/var/lib/docker"}

    def get(name):
        try:
            return by_name[name]
        except KeyError:
            raise NotFound(f"No such container: {name}") from None

    def create(image, *, name, labels, **kwargs):
        container = _container(name, labels, [])
        container.short_id = "c0ffee"
        container.status = "running"
        return by_name.setdefault(name, container)

    client.containers.get.side_effect = get
    client.containers.list.side_effect = lambda all=False, **kwargs: [
        c for c in by_name.values() if all or c.status == "running"
    ]
    client.containers.create.side_effect = create
    answer_docker_lists(client, by_name)
    # A pull answers an empty stream (ADR-0384).
    client.api.post.return_value = pull_answer()
    client.add = lambda container: by_name.setdefault(container.name, container)
    monkeypatch.setattr(docker_module, "get_client", lambda: client)
    monkeypatch.setattr(apps_router, "get_client", lambda: client)
    # No pool is mounted: the apps of these tests keep no data on one.
    monkeypatch.setattr(apps_router, "list_mounted_pools", lambda: [])
    return client


@pytest.fixture
def host_ports(monkeypatch):
    """The host of an install's checks (src/apps/preflight.py, ADR-0381): no program listens on a
    port, and the system disk has 64 GiB free. Any other host command fails the test."""

    def run(cmd, **kwargs):
        if cmd == ["ss", "-H", "-l", "-n", "-t", "-u", "-p"]:
            return HostResult(0, "", "")
        if cmd[:2] == ["/bin/sh", "-c"] and cmd[3:4] == ["unos-free-space"]:
            listing = "Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/sda1 67110912 2048 67108864 1% /\n"
            return HostResult(0, listing * len(cmd[4:]), "")
        raise AssertionError(f"unexpected host command: {cmd}")

    monkeypatch.setattr(host_module, "run_on_host", run)


def running(container: MagicMock) -> MagicMock:
    container.status = "running"
    return container


def app_container(name: str = "dozzle") -> MagicMock:
    """An installed app of the catalog without data on a pool, stopped."""
    return _container(name, {"unos.app": "dozzle"}, [])


class TestContainers:
    @pytest.mark.parametrize(("status", "action", "verb", "written"), [
        ("exited", "start", "Started", "container_started"),
        ("running", "stop", "Stopped", "container_stopped"),
        ("running", "restart", "Restarted", "container_started"),
        ("exited", "restart", "Restarted", "container_started"),
    ])
    def test_a_container_that_changes_writes_its_entry(self, admin, audit_file, docker, status, action, verb, written):
        web = docker.add(_container("web", {}, []))
        web.status = status

        response = admin.post(f"{CONTAINERS}/web/{action}")

        assert response.status_code == 200, response.text
        getattr(web, action).assert_called_once()
        assert entries(audit_file) == [entry(written, "container", "web", f"{verb} the container web")]

    @pytest.mark.parametrize(("status", "action"), [("running", "start"), ("exited", "stop"), ("created", "stop")])
    def test_a_start_or_stop_that_changes_nothing_writes_nothing(self, admin, audit_file, docker, status, action):
        # Docker answers a start of a running container and a stop of one that does not run
        # without an error, and changes nothing.
        web = docker.add(_container("web", {}, []))
        web.status = status

        response = admin.post(f"{CONTAINERS}/web/{action}")

        assert response.status_code == 200, response.text
        assert entries(audit_file) == []

    @pytest.mark.parametrize(("force", "details"), [
        (False, "Removed the container web"),
        (True, "Removed the container web (forced)"),
    ])
    def test_removing_a_container_writes_container_removed(self, admin, audit_file, docker, force, details):
        web = docker.add(_container("web", {}, []))

        response = admin.delete(f"{CONTAINERS}/web", params={"force": force})

        assert response.status_code == 200, response.text
        web.remove.assert_called_once_with(force=force)
        assert entries(audit_file) == [entry("container_removed", "container", "web", details, "warning")]

    def test_refused_and_failed_container_changes_write_nothing(self, admin, audit_file, docker):
        web = docker.add(_container("web", {}, []))
        web.start.side_effect = APIError("port is already allocated")
        running(docker.add(_container("unos-webui", {}, [])))

        failed = admin.post(f"{CONTAINERS}/web/start")
        missing = admin.delete(f"{CONTAINERS}/nope")
        system = admin.post(f"{CONTAINERS}/unos-webui/stop")

        assert [r.status_code for r in (failed, missing, system)] == [500, 404, 404]
        assert entries(audit_file) == []

    def test_a_system_container_is_not_restarted(self, admin, audit_file, docker):
        # Stop and remove refused UNOS's own containers; restart restarted them.
        supervisor = running(docker.add(_container("unos-supervisor", {}, [])))

        response = admin.post(f"{CONTAINERS}/unos-supervisor/restart")

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "Cannot restart system container"
        supervisor.restart.assert_not_called()
        assert entries(audit_file) == []

    def test_a_stop_whose_entry_cannot_be_written_stops_and_answers(
        self, admin, audit_file, docker, monkeypatch, caplog
    ):
        web = running(docker.add(_container("web", {}, [])))
        the_audit_log_is_full(monkeypatch)

        response = admin.post(f"{CONTAINERS}/web/stop")

        assert response.status_code == 200, response.text
        web.stop.assert_called_once()
        assert lost(caplog) == [entry("container_stopped", "container", "web", "Stopped the container web")]


@pytest.fixture
def database(tmp_path_factory) -> Iterator[None]:
    """The database that an install reads the shares from (ADR-0274): without shares."""
    path = tmp_path_factory.mktemp("apps-db") / "apps.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    maker = async_sessionmaker(
        create_async_engine(f"sqlite+aiosqlite:///{path}", poolclass=NullPool), expire_on_commit=False
    )

    async def override_get_db():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()


class TestApps:
    def test_installing_an_app_writes_app_installed(self, admin, audit_file, docker, database, host_ports):
        # The install runs as a job, which writes the entry before its end (ADR-0384).
        response = install(admin, {"template_id": "dozzle"})

        assert response.status_code == 200, response.text
        assert entries(audit_file) == [entry("app_installed", "app", "dozzle", "Installed Dozzle from the catalog as dozzle")]

    def test_a_refused_or_failed_install_writes_nothing(self, admin, audit_file, docker, database, host_ports):
        docker.add(_container("dozzle", {}, []))
        taken = install(admin, {"template_id": "dozzle"})
        named = install(admin, {"template_id": "dozzle", "name": "logs"})
        docker.api.post.side_effect = APIError("pull access denied")
        # Docker lacks Glances' pinned image, so the install pulls it (ADR-0410).
        docker.images.get.side_effect = NotFound("No such image")
        failed = install(admin, {"template_id": "glances"})

        assert [r.status_code for r in (taken, named, failed)] == [409, 422, 500]
        assert entries(audit_file) == []

    def test_uninstalling_an_app_writes_app_uninstalled(self, admin, audit_file, docker):
        logs = running(docker.add(app_container("logs")))

        response = admin.delete(f"{APPS}/installed/logs")

        assert response.status_code == 200, response.text
        logs.remove.assert_called_once_with()
        assert entries(audit_file) == [
            entry("app_uninstalled", "app", "logs", "Uninstalled the app logs; its data folders were kept", "warning")
        ]

    @pytest.mark.parametrize(("status", "action", "verb", "written", "calls"), [
        ("exited", "start", "Started", "container_started", ["start"]),
        ("running", "stop", "Stopped", "container_stopped", ["stop"]),
        # A restart is a stop, then a start (ADR-0381).
        ("running", "restart", "Restarted", "container_started", ["stop", "start"]),
    ])
    def test_starting_and_stopping_an_app_write_their_entries(
        self, admin, audit_file, docker, status, action, verb, written, calls
    ):
        logs = docker.add(app_container("logs"))
        logs.status = status

        response = admin.post(f"{APPS}/installed/logs/{action}")

        assert response.status_code == 200, response.text
        assert [call[0] for call in logs.method_calls if call[0] in ("start", "stop", "restart")] == calls
        assert entries(audit_file) == [entry(written, "container", "logs", f"{verb} the app logs")]

    @pytest.mark.parametrize(("status", "action"), [("running", "start"), ("exited", "stop")])
    def test_an_app_start_or_stop_that_changes_nothing_writes_nothing(self, admin, audit_file, docker, status, action):
        logs = docker.add(app_container("logs"))
        logs.status = status

        response = admin.post(f"{APPS}/installed/logs/{action}")

        assert response.status_code == 200, response.text
        assert entries(audit_file) == []

    def test_refused_and_failed_app_changes_write_nothing(self, admin, audit_file, docker):
        docker.add(_container("web", {}, []))  # no app of the catalog
        logs = docker.add(app_container("logs"))  # stopped: the uninstall's stop changes nothing
        logs.remove.side_effect = APIError("removal of container logs is already in progress")

        not_an_app = admin.delete(f"{APPS}/installed/web")
        missing = admin.post(f"{APPS}/installed/nope/stop")
        failed = admin.delete(f"{APPS}/installed/logs")

        assert [r.status_code for r in (not_an_app, missing, failed)] == [400, 404, 500]
        assert entries(audit_file) == []

    def test_an_uninstall_that_stopped_the_app_and_could_not_remove_it_writes_a_failed_stop(
        self, admin, audit_file, docker
    ):
        # The app ran: the uninstall stopped it, then its removal failed, and it wrote no entry.
        logs = running(docker.add(app_container("logs")))
        logs.remove.side_effect = APIError("removal of container logs is already in progress")

        response = admin.delete(f"{APPS}/installed/logs")

        assert response.status_code == 500, response.text
        logs.stop.assert_called_once_with(timeout=10)
        assert entries(audit_file) == [
            entry("container_stopped", "container", "logs", "Stopped the app logs; then: Failed to remove: "
                  "removal of container logs is already in progress", success=False)
        ]

    def test_an_uninstall_whose_entry_cannot_be_written_removes_the_app_and_answers(
        self, admin, audit_file, docker, monkeypatch, caplog
    ):
        logs = running(docker.add(app_container("logs")))
        the_audit_log_is_full(monkeypatch)

        response = admin.delete(f"{APPS}/installed/logs")

        assert response.status_code == 200, response.text
        logs.remove.assert_called_once_with()
        assert lost(caplog) == [
            entry("app_uninstalled", "app", "logs", "Uninstalled the app logs; its data folders were kept", "warning")
        ]
