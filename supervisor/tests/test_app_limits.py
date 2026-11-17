"""Memory and CPU limits of an app's containers, their use and its alerts
(apps.container-resource-limits, ADR-0388).

The app is test_app_stacks' Photos, of four containers (photos, photos-database, photos-cache and
photos-machine-learning), on the strict fake Docker of docker_fake.py. The host has 8 GiB and 8
CPUs; the cgroups of its containers are files below a folder of the test, laid out as the host's
/sys/fs/cgroup with Docker's systemd driver (system.slice/docker-<id>.scope), their limits written
from each container's HostConfig as runc writes them.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import threading
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from sqlalchemy.exc import SQLAlchemyError

from src.apps.models import AppResourceLimit
from src.containers.docker import DockerUnavailable
from src.database import get_db
from src.main import app as main_app

from . import docker_fake, test_app_stacks, test_apps
from .test_app_stacks import NAMES, PHOTOS_INSTALL, _audit, _created
from .test_apps import INSTALLED, START, STOP, UNINSTALL, install

api = test_apps.api
user_api = test_apps.user_api
pools = test_apps.pools
soc = test_apps.soc
shares = test_apps.shares
chown_calls = test_apps.chown_calls
photos = test_app_stacks.photos
docker = test_app_stacks.docker
clock = test_app_stacks.clock

limits = importlib.import_module("src.apps.limits")
usage = importlib.import_module("src.apps.usage")
apps_router = importlib.import_module("src.apps.router")
stacks = importlib.import_module("src.apps.stacks")
health_router = importlib.import_module("src.health.router")
problems = importlib.import_module("src.health.problems")

MIB = 1024**2
GIB = 1024**3
HOST_MEMORY = 8 * GIB
HOST_CPUS = 8
BOUNDS = "/api/v1/apps/limits"
LIMITS = "/api/v1/apps/installed/{}/limits"
T0 = 1_800_000_000.0  # the wall clock of the first pass


@pytest.fixture(autouse=True)
def host(monkeypatch):
    """A host of 8 GiB and 8 CPUs."""
    monkeypatch.setattr(limits, "host_memory", lambda: HOST_MEMORY)
    monkeypatch.setattr(limits, "host_cpus", lambda: HOST_CPUS)


@pytest.fixture(autouse=True)
def docker_of_the_sampler(docker, monkeypatch):
    """The sampler asks the same Docker as the Apps API."""
    monkeypatch.setattr(usage, "get_client", lambda: docker)
    return docker


@pytest.fixture
def time_now(monkeypatch) -> list[float]:
    """The sampler's clock: ``time_now[0]`` seconds since the epoch, its monotonic clock too."""
    now = [T0]
    monkeypatch.setattr(usage, "_now", lambda: now[0])
    monkeypatch.setattr(usage, "_monotonic", lambda: now[0])
    return now


@pytest.fixture
def alerts(monkeypatch) -> list[tuple[str, str, str]]:
    """The alerts that the problems send (type, title, text), recorded instead of sent."""
    sent = []

    async def deliver(event):
        sent.append((event.type.value, event.title, event.message))

    monkeypatch.setattr(health_router, "_deliver_alert", deliver)
    return sent


class _Cgroups:
    """The host's /sys/fs/cgroup as the sampler reads it: a folder per running container."""

    def __init__(self, root: Path, docker) -> None:
        self.root = root
        self.docker = docker
        self.layout = "systemd"

    def folder(self, name: str) -> Path:
        live = self.docker.containers_[name]
        if self.layout == "systemd":
            return self.root / "system.slice" / f"docker-{live.id}.scope"
        return self.root / "docker" / live.id

    def write(self, name: str, *, current: int, inactive: int = 0, usage_usec: int = 0) -> None:
        """The container's use: memory.current with ``inactive`` bytes of it inactive page cache,
        and the CPU time it used so far; its memory.max and cpu.max as runc sets them from its
        HostConfig."""
        host = self.docker.containers_[name].attrs["HostConfig"]
        folder = self.folder(name)
        folder.mkdir(parents=True, exist_ok=True)
        memory = host.get("Memory") or 0
        quota, period = host.get("CpuQuota") or 0, host.get("CpuPeriod") or 100000
        (folder / "memory.current").write_text(f"{current}\n")
        (folder / "memory.stat").write_text(
            f"anon {current - inactive}\nfile {inactive}\nkernel 0\nactive_anon {current - inactive}\n"
            f"inactive_anon 0\nactive_file 0\ninactive_file {inactive}\nunevictable 0\n"
        )
        (folder / "memory.max").write_text(f"{memory}\n" if memory > 0 else "max\n")
        (folder / "memory.events").write_text("low 0\nhigh 0\nmax 0\noom 0\noom_kill 0\noom_group_kill 0\n")
        (folder / "cpu.max").write_text(f"{quota} {period}\n" if quota > 0 else f"max {period}\n")
        (folder / "cpu.stat").write_text(
            f"usage_usec {usage_usec}\nuser_usec {usage_usec}\nsystem_usec 0\nnr_periods 0\nnr_throttled 0\n"
            "throttled_usec 0\nnr_bursts 0\nburst_usec 0\n"
        )

    def gone(self, name: str) -> None:
        for path in sorted(self.folder(name).iterdir()):
            path.unlink()
        self.folder(name).rmdir()


_current: dict[str, _Cgroups] = {}


@pytest.fixture(autouse=True)
def cgroups(tmp_path, monkeypatch, docker) -> _Cgroups:
    """The host's cgroups, in every test: a memory limit set on a running container needs its use."""
    root = tmp_path / "cgroup"
    root.mkdir()
    monkeypatch.setattr(usage, "CGROUP_ROOT", root)
    _current["cgroups"] = _Cgroups(root, docker)
    yield _current["cgroups"]
    _current.clear()


def _installed(api, docker) -> None:
    """Photos installed and running, each container using 50 MiB; the calls of its install forgotten."""
    test_app_stacks._installed(api, docker)
    for name in NAMES:
        _current["cgroups"].write(name, current=50 * MIB)


def sample() -> None:
    """One pass of the sampler, as its loop runs it."""
    asyncio.run(usage.sample_once())


def _host(docker, name: str) -> dict:
    return docker.containers_[name].attrs["HostConfig"]


def _listed(api, name: str) -> dict:
    """The resources of the container ``name`` as GET /apps/installed lists them."""
    for app in api.get(INSTALLED).json():
        if app["name"] == name:
            return app["resources"]
        for companion in app["companions"]:
            if companion["container"] == name:
                return companion["resources"]
    raise AssertionError(f"{name} is not listed")


def _rows(shares_db) -> list[tuple]:
    with Session(create_engine(shares_db)) as session:
        return [
            (row.app, row.container, row.memory_bytes, row.cpu_quota)
            for row in session.scalars(select(AppResourceLimit).order_by(AppResourceLimit.container))
        ]


@pytest.fixture
def database(shares) -> str:
    """The URL of the database of the routes (test_apps' shares fixture), for sqlite3's driver."""
    return asyncio.run(_database_url())


async def _database_url() -> str:
    async for session in main_app.dependency_overrides[get_db]():
        return str(session.bind.url).replace("sqlite+aiosqlite", "sqlite")
    raise AssertionError("no database")


# ---------------------------------------------------------------------------
# Step 1: an administrator sets a memory limit and a CPU quota; docker inspect shows them
# ---------------------------------------------------------------------------


class TestSetLimits:
    def test_the_limits_of_one_container_go_into_its_host_config_at_once(self, api, pools, docker, database):
        _installed(api, docker)

        response = api.put(LIMITS.format("photos-database"), json={"memory_limit": 512 * MIB, "cpu_limit": 1.5})

        assert response.status_code == 200, response.text
        assert response.json() == {"container": "photos-database", "memory_limit": 512 * MIB, "cpu_limit": 1.5}
        host = _host(docker, "photos-database")
        # No swap beyond the limit, and the quota per Docker's period of 100 ms.
        assert (host["Memory"], host["MemorySwap"], host["CpuPeriod"], host["CpuQuota"]) == (
            512 * MIB, 512 * MIB, 100000, 150000,
        )
        assert [c for c in docker.calls if c.name == "container.update"] == [
            test_app_stacks.Call("container.update", "photos-database", (), {
                "mem_limit": 512 * MIB, "memswap_limit": 512 * MIB, "cpu_period": 100000, "cpu_quota": 150000,
            }),
        ]
        # The other containers of the app keep theirs: none.
        for name in ("photos", "photos-cache", "photos-machine-learning"):
            assert not _host(docker, name).get("Memory") and not _host(docker, name).get("CpuQuota"), name
        # The container keeps running: Docker's update changes a running container's cgroup.
        assert docker.containers_["photos-database"].status == "running"
        assert _listed(api, "photos-database")["memory_limit"] == 512 * MIB
        assert _listed(api, "photos-database")["cpu_limit"] == 1.5
        assert _listed(api, "photos")["memory_limit"] is None
        assert _rows(database) == [("photos", "photos-database", 512 * MIB, 150000)]

    def test_a_container_is_named_by_its_id_too(self, api, pools, docker):
        _installed(api, docker)
        container_id = docker.container("photos").id

        response = api.put(LIMITS.format(container_id), json={"memory_limit": GIB, "cpu_limit": None})

        assert response.status_code == 200, response.text
        assert response.json()["container"] == "photos"
        assert _host(docker, "photos")["Memory"] == GIB
        assert not _host(docker, "photos").get("CpuQuota")

    def test_a_change_is_audited_with_what_changed(self, api, pools, docker):
        _installed(api, docker)
        api.put(LIMITS.format("photos-database"), json={"memory_limit": 512 * MIB, "cpu_limit": 1.5})
        api.put(LIMITS.format("photos-database"), json={"memory_limit": GIB, "cpu_limit": 1.5})

        entries = [e for e in _audit() if e["action"] == "app_limits_changed"]
        assert entries == [
            {"action": "app_limits_changed", "resource_type": "container", "resource_id": "photos-database",
             "details": "Changed the limits of photos-database of the app photos: memory from none to 512 MiB; "
                        "CPU from none to 1.5 CPUs", "severity": "info", "success": True},
            {"action": "app_limits_changed", "resource_type": "container", "resource_id": "photos-database",
             "details": "Changed the limits of photos-database of the app photos: memory from 512 MiB to 1 GiB",
             "severity": "info", "success": True},
        ]

    def test_limits_as_they_are_change_nothing(self, api, pools, docker, database):
        _installed(api, docker)
        api.put(LIMITS.format("photos-cache"), json={"memory_limit": 256 * MIB, "cpu_limit": 0.5})
        docker.calls.clear()
        before = _audit()

        response = api.put(LIMITS.format("photos-cache"), json={"memory_limit": 256 * MIB, "cpu_limit": 0.5})

        assert response.status_code == 200, response.text
        assert docker.called("container.update") == []
        assert _audit() == before
        assert _rows(database) == [("photos", "photos-cache", 256 * MIB, 50000)]

    def test_a_limit_is_removed_with_null(self, api, pools, docker, database):
        """Docker removes a CPU quota with -1; a memory limit it cannot remove from a container
        (its update takes 0 as unchanged and refuses less than 6 MB), so the limit becomes twice the
        host's memory, which the container cannot reach, and the swap is unlimited again; the
        listing shows no limit, and the row goes."""
        _installed(api, docker)
        api.put(LIMITS.format("photos-cache"), json={"memory_limit": 256 * MIB, "cpu_limit": 0.5})

        response = api.put(LIMITS.format("photos-cache"), json={"memory_limit": None, "cpu_limit": None})

        assert response.status_code == 200, response.text
        assert response.json() == {"container": "photos-cache", "memory_limit": None, "cpu_limit": None}
        host = _host(docker, "photos-cache")
        assert (host["Memory"], host["MemorySwap"], host["CpuQuota"]) == (2 * HOST_MEMORY, -1, -1)
        assert _listed(api, "photos-cache")["memory_limit"] is None
        assert _listed(api, "photos-cache")["cpu_limit"] is None
        assert _rows(database) == []
        assert _audit()[-1]["details"] == (
            "Changed the limits of photos-cache of the app photos: memory from 256 MiB to none; "
            "CPU from 0.5 CPUs to none"
        )

    def test_one_limit_is_removed_and_the_other_kept(self, api, pools, docker, database):
        _installed(api, docker)
        api.put(LIMITS.format("photos-cache"), json={"memory_limit": 256 * MIB, "cpu_limit": 0.5})
        docker.calls.clear()

        api.put(LIMITS.format("photos-cache"), json={"memory_limit": 256 * MIB, "cpu_limit": None})

        assert [c.kwargs for c in docker.calls if c.name == "container.update"] == [{"cpu_quota": -1}]
        assert _host(docker, "photos-cache")["Memory"] == 256 * MIB
        assert _rows(database) == [("photos", "photos-cache", 256 * MIB, None)]

    def test_a_stopped_app_takes_its_limits_for_its_next_start(self, api, pools, docker):
        _installed(api, docker)
        assert api.post(STOP.format("photos")).status_code == 200

        response = api.put(LIMITS.format("photos-machine-learning"), json={"memory_limit": 2 * GIB, "cpu_limit": 4})

        assert response.status_code == 200, response.text
        assert _host(docker, "photos-machine-learning")["Memory"] == 2 * GIB
        assert api.post(START.format("photos")).status_code == 200
        # Docker keeps them in the container's HostConfig across stops, starts, restarts and reboots.
        assert _host(docker, "photos-machine-learning")["CpuQuota"] == 400000

    def test_the_bounds_of_this_host(self, api):
        response = api.get(BOUNDS)

        assert response.status_code == 200, response.text
        assert response.json() == {"memory_min": 64 * MIB, "memory_max": HOST_MEMORY, "cpu_min": 0.1, "cpu_max": 8.0}


class TestRefusedLimits:
    @pytest.mark.parametrize(("body", "detail"), [
        ({"memory_limit": 63 * MIB, "cpu_limit": None}, "A memory limit must be at least 64 MiB"),
        ({"memory_limit": 0, "cpu_limit": None}, "A memory limit must be at least 64 MiB"),
        ({"memory_limit": -1, "cpu_limit": None}, "A memory limit must be at least 64 MiB"),
        ({"memory_limit": HOST_MEMORY, "cpu_limit": None}, "A memory limit must be below the memory of the NAS (8 GiB)"),
        ({"memory_limit": None, "cpu_limit": 0.09}, "A CPU limit must be at least 0.1 CPUs"),
        ({"memory_limit": None, "cpu_limit": 0}, "A CPU limit must be at least 0.1 CPUs"),
        ({"memory_limit": None, "cpu_limit": 8.01}, "A CPU limit can be at most the 8 CPUs of the NAS"),
        ({"memory_limit": None, "cpu_limit": 1.234}, "A CPU limit is given in hundredths of a CPU"),
    ])
    def test_values_out_of_bounds_answer_400_and_change_nothing(self, api, pools, docker, body, detail):
        _installed(api, docker)

        response = api.put(LIMITS.format("photos-database"), json=body)

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == detail
        assert docker.called("container.update") == []

    @pytest.mark.parametrize("body", [{"memory_limit": 512 * MIB}, {"cpu_limit": 1}, {}])
    def test_a_field_left_out_answers_422_and_removes_nothing(self, api, pools, docker, body):
        """Only an explicit null removes a limit."""
        _installed(api, docker)
        api.put(LIMITS.format("photos-cache"), json={"memory_limit": 256 * MIB, "cpu_limit": 0.5})

        response = api.put(LIMITS.format("photos-cache"), json=body)

        assert response.status_code == 422
        assert (_host(docker, "photos-cache")["Memory"], _host(docker, "photos-cache")["CpuQuota"]) == (256 * MIB, 50000)

    def test_a_body_with_another_field_answers_422(self, api, pools, docker):
        _installed(api, docker)

        response = api.put(LIMITS.format("photos-database"), json={"memory": 512 * MIB})

        assert response.status_code == 422

    def test_a_limit_below_what_a_running_container_uses_answers_409(self, api, pools, docker, cgroups):
        """The kernel would take the memory from it, and then end its processes, at once."""
        _installed(api, docker)
        cgroups.write("photos-database", current=400 * MIB, inactive=100 * MIB)

        response = api.put(LIMITS.format("photos-database"), json={"memory_limit": 256 * MIB, "cpu_limit": None})

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == (
            "photos-database uses 300 MiB now: a memory limit below 332 MiB would make the kernel take memory from "
            "it or end its processes at once. Set at least 332 MiB, or stop Photos first."
        )
        assert docker.called("container.update") == []
        # What it uses without the page cache that the kernel can drop, and a margin of 10%, at
        # least 32 MiB, is the floor.
        assert api.put(LIMITS.format("photos-database"), json={"memory_limit": 331 * MIB, "cpu_limit": None}).status_code == 409
        assert api.put(LIMITS.format("photos-database"), json={"memory_limit": 332 * MIB, "cpu_limit": None}).status_code == 200

    def test_pages_in_swap_count_in_the_floor(self, api, pools, docker, cgroups):
        """They come back into its memory, which no swap beyond the limit then holds."""
        _installed(api, docker)
        cgroups.write("photos-database", current=1000 * MIB, inactive=600 * MIB)
        (cgroups.folder("photos-database") / "memory.swap.current").write_text(f"{200 * MIB}\n")

        response = api.put(LIMITS.format("photos-database"), json={"memory_limit": 600 * MIB, "cpu_limit": None})

        assert response.status_code == 409, response.text
        assert response.json()["detail"].startswith(
            "photos-database uses 600 MiB now: a memory limit below 660 MiB would make the kernel"
        )

    def test_a_running_container_whose_use_cannot_be_read_gets_no_lower_memory_limit(self, api, pools, docker, cgroups):
        """The floor is unknown: the change is refused, never sent blind. Raising or removing a limit,
        a CPU limit and a stopped container need no floor."""
        _installed(api, docker)
        assert api.put(LIMITS.format("photos-cache"), json={"memory_limit": 512 * MIB, "cpu_limit": None}).status_code == 200
        cgroups.gone("photos-cache")
        docker.calls.clear()

        lower = api.put(LIMITS.format("photos-cache"), json={"memory_limit": 256 * MIB, "cpu_limit": None})

        assert lower.status_code == 409, lower.text
        assert lower.json()["detail"] == (
            "UNOS could not read what photos-cache uses now, so a memory limit might make the kernel end its "
            "processes at once: stop Photos first, then set it."
        )
        assert docker.called("container.update") == []
        assert api.put(LIMITS.format("photos-cache"), json={"memory_limit": GIB, "cpu_limit": 1}).status_code == 200
        assert api.put(LIMITS.format("photos-cache"), json={"memory_limit": None, "cpu_limit": 1}).status_code == 200
        assert api.post(STOP.format("photos")).status_code == 200
        assert api.put(LIMITS.format("photos-cache"), json={"memory_limit": 128 * MIB, "cpu_limit": 1}).status_code == 200

    def test_a_regular_user_cannot_set_limits(self, user_api, pools, docker):
        response = user_api.put(LIMITS.format("photos-database"), json={"memory_limit": 512 * MIB, "cpu_limit": None})

        assert response.status_code == 403

    def test_a_container_that_is_no_app_answers_400(self, api, pools, docker):
        _installed(api, docker)
        docker.containers_["photos-cache"].attrs["Config"]["Labels"] = {}

        response = api.put(LIMITS.format("photos-cache"), json={"memory_limit": 512 * MIB, "cpu_limit": None})

        assert response.status_code == 400
        assert response.json()["detail"] == "Not a UNOS managed app"

    def test_an_unknown_container_answers_404(self, api, pools, docker):
        response = api.put(LIMITS.format("nothing"), json={"memory_limit": 512 * MIB, "cpu_limit": None})

        assert response.status_code == 404

    def test_docker_that_does_not_answer_answers_503(self, api, pools, docker):
        _installed(api, docker)
        docker.fail("containers.get", None, DockerUnavailable("no socket"))

        response = api.put(LIMITS.format("photos-cache"), json={"memory_limit": 512 * MIB, "cpu_limit": None})

        assert response.status_code == 503
        assert response.json()["detail"] == "Docker is not available: no socket"

    def test_a_cpu_limit_set_with_nano_cpus_is_left_alone(self, api, pools, docker, database):
        """Docker refuses a quota beside NanoCpus and cannot take them off: UNOS changes no CPU limit
        of such a container, and shows the one in force."""
        _installed(api, docker)
        _host(docker, "photos-cache")["NanoCpus"] = 2_000_000_000  # set by hand with docker update --cpus
        assert _listed(api, "photos-cache")["cpu_limit"] == 2.0

        response = api.put(LIMITS.format("photos-cache"), json={"memory_limit": None, "cpu_limit": 1})

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == (
            "The CPU limit of photos-cache was set outside UNOS (docker update --cpus), and Docker lets no other CPU "
            "limit replace it: change it with docker update --cpus, or uninstall Photos and install it again"
        )
        assert docker.called("container.update") == []
        # Its memory limit can still change.
        response = api.put(LIMITS.format("photos-cache"), json={"memory_limit": 512 * MIB, "cpu_limit": 2})
        assert response.status_code == 200, response.text
        assert _rows(database) == [("photos", "photos-cache", 512 * MIB, 200000)]

    def test_a_refusal_of_docker_answers_409_with_its_reason(self, api, pools, docker, database):
        _installed(api, docker)
        docker.fail("container.update", "photos-cache", docker_fake._refused(400, "Minimum memory limit allowed is 6MB"))

        response = api.put(LIMITS.format("photos-cache"), json={"memory_limit": 512 * MIB, "cpu_limit": None})

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == (
            "Docker refused the limits of photos-cache: Minimum memory limit allowed is 6MB"
        )
        assert _rows(database) == []
        assert [e for e in _audit() if e["action"] == "app_limits_changed"] == []

    def test_a_limit_that_docker_discards_answers_409_and_keeps_what_it_applied(self, api, pools, docker, database):
        """A kernel without the memory controller: Docker's update discards the memory limit with a
        warning and applies the CPU quota."""
        _installed(api, docker)
        docker.unsupported.add("memory")

        response = api.put(LIMITS.format("photos-cache"), json={"memory_limit": 512 * MIB, "cpu_limit": 1.5})

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == (
            "Docker did not apply all the limits of photos-cache: Your kernel does not support memory limit "
            "capabilities or the cgroup is not mounted. Limitation discarded."
        )
        assert not _host(docker, "photos-cache").get("Memory") and _host(docker, "photos-cache")["CpuQuota"] == 150000
        assert _rows(database) == [("photos", "photos-cache", None, 150000)]
        assert _audit()[-1]["details"] == (
            "Changed the limits of photos-cache of the app photos: CPU from none to 1.5 CPUs"
        )

    def test_a_database_that_cannot_keep_them_answers_500_and_audits_a_failure(self, api, pools, docker, monkeypatch):
        _installed(api, docker)

        async def broken(*args, **kwargs):
            raise SQLAlchemyError("disk I/O error")

        monkeypatch.setattr(limits, "keep", broken)

        response = api.put(LIMITS.format("photos-cache"), json={"memory_limit": 512 * MIB, "cpu_limit": None})

        assert response.status_code == 500
        assert response.json()["detail"] == (
            "The limits of photos-cache are in force, but UNOS could not keep them for its next install: disk I/O error"
        )
        assert _host(docker, "photos-cache")["Memory"] == 512 * MIB
        entry = _audit()[-1]
        assert (entry["action"], entry["success"]) == ("app_limits_changed", False)
        assert entry["details"] == (
            "Changed the limits of photos-cache of the app photos: memory from none to 512 MiB; then: The limits of "
            "photos-cache are in force, but UNOS could not keep them for its next install: disk I/O error"
        )

    def test_another_change_of_the_app_answers_409(self, api, pools, docker):
        _installed(api, docker)
        lock = stacks.acquire("photos", "Photos")
        try:
            response = api.put(LIMITS.format("photos-cache"), json={"memory_limit": 512 * MIB, "cpu_limit": None})
        finally:
            lock.release()

        assert response.status_code == 409
        assert response.json()["detail"] == "Another change of Photos is under way; try again when it has finished"
        assert docker.called("container.update") == []


class TestKeptForTheNextInstall:
    def test_a_reinstall_creates_the_containers_with_their_limits(self, api, pools, docker, database):
        """An uninstall keeps the limits, as it keeps the data folders (ADR-0256): the next install of
        the app creates each container with its own."""
        _installed(api, docker)
        api.put(LIMITS.format("photos-database"), json={"memory_limit": 512 * MIB, "cpu_limit": 1.5})
        api.put(LIMITS.format("photos-machine-learning"), json={"memory_limit": 2 * GIB, "cpu_limit": None})
        assert api.delete(UNINSTALL.format("photos")).status_code == 200
        assert _rows(database) == [
            ("photos", "photos-database", 512 * MIB, 150000),
            ("photos", "photos-machine-learning", 2 * GIB, None),
        ]
        docker.calls.clear()

        response = install(api, PHOTOS_INSTALL)

        assert response.status_code == 200, response.text
        database_config = _created(docker, "photos-database").kwargs["host_config"]
        assert (database_config["Memory"], database_config["MemorySwap"]) == (512 * MIB, 512 * MIB)
        assert (database_config["CpuPeriod"], database_config["CpuQuota"]) == (100000, 150000)
        learning = _created(docker, "photos-machine-learning").kwargs["host_config"]
        assert learning["Memory"] == 2 * GIB and "CpuQuota" not in learning
        for name in ("photos", "photos-cache"):
            assert "Memory" not in _created(docker, name).kwargs["host_config"], name
        assert _listed(api, "photos-database") == {
            "memory_limit": 512 * MIB, "cpu_limit": 1.5, "memory_used": None, "cpu_used": None, "sampled_at": None,
        }
        installed = [e for e in _audit() if e["action"] == "app_installed"][-1]
        assert installed["details"].endswith(
            "; with the limits kept for it: photos-database 512 MiB of memory and 1.5 CPUs, "
            "photos-machine-learning 2 GiB of memory"
        )

    def test_a_stored_limit_that_is_out_of_bounds_is_left_out(self, api, pools, docker, database, caplog):
        """The database may come from a restored backup of another host: what is out of bounds here is
        not given to Docker, and the log says so."""
        with Session(create_engine(database)) as session:
            session.add(AppResourceLimit(app="photos", container="photos-cache", memory_bytes=16 * GIB,
                                         cpu_quota=1_600_000))
            session.add(AppResourceLimit(app="photos", container="photos-database", memory_bytes=MIB, cpu_quota=50000))
            session.commit()

        with caplog.at_level(logging.WARNING):
            response = install(api, PHOTOS_INSTALL)

        assert response.status_code == 200, response.text
        cache = _created(docker, "photos-cache").kwargs["host_config"]
        assert "Memory" not in cache and "CpuQuota" not in cache
        db = _created(docker, "photos-database").kwargs["host_config"]
        assert "Memory" not in db and db["CpuQuota"] == 50000
        assert "The memory limit of photos-cache kept in the database (16 GiB) is out of bounds here" in caplog.text
        assert "The CPU limit of photos-cache kept in the database (16 CPUs) is out of bounds here" in caplog.text
        assert "The memory limit of photos-database kept in the database (1 MiB) is out of bounds here" in caplog.text


# ---------------------------------------------------------------------------
# Step 2: the use against the limits, read from the cgroups, and its alerts
# ---------------------------------------------------------------------------


class TestUse:
    def test_the_listing_shows_the_last_use_read(self, api, pools, docker, cgroups, time_now):
        _installed(api, docker)
        api.put(LIMITS.format("photos-database"), json={"memory_limit": 512 * MIB, "cpu_limit": 2})
        for name in NAMES:
            cgroups.write(name, current=300 * MIB, inactive=100 * MIB, usage_usec=1_000_000)
        sample()
        time_now[0] += 30
        cgroups.write("photos-database", current=310 * MIB, inactive=100 * MIB, usage_usec=1_000_000 + 15_000_000)
        sample()

        assert _listed(api, "photos-database") == {
            "memory_limit": 512 * MIB,
            "cpu_limit": 2.0,
            "memory_used": 210 * MIB,
            "cpu_used": 0.5,  # 15 s of CPU time in 30 s
            "sampled_at": "2027-01-15T08:00:30+00:00",
        }
        assert _listed(api, "photos")["cpu_used"] == 0.0
        assert _listed(api, "photos")["memory_used"] == 200 * MIB

    def test_the_first_read_has_no_cpu_use_yet(self, api, pools, docker, cgroups, time_now):
        _installed(api, docker)
        cgroups.write("photos", current=100 * MIB, usage_usec=5_000_000)

        sample()

        assert _listed(api, "photos")["memory_used"] == 100 * MIB
        assert _listed(api, "photos")["cpu_used"] is None

    def test_a_restarted_container_starts_its_cpu_count_again(self, api, pools, docker, cgroups, time_now):
        _installed(api, docker)
        cgroups.write("photos", current=100 * MIB, usage_usec=50_000_000)
        sample()
        time_now[0] += 30
        cgroups.gone("photos")
        cgroups.write("photos", current=100 * MIB, usage_usec=1_000_000)

        sample()

        assert _listed(api, "photos")["cpu_used"] is None

    def test_cgroupfs_driver_folders_are_read_too(self, api, pools, docker, cgroups, time_now):
        _installed(api, docker)
        cgroups.gone("photos")
        cgroups.layout = "cgroupfs"
        cgroups.write("photos", current=100 * MIB)

        sample()

        assert _listed(api, "photos")["memory_used"] == 100 * MIB

    def test_a_stopped_app_shows_no_use(self, api, pools, docker, cgroups, time_now):
        _installed(api, docker)
        cgroups.write("photos", current=100 * MIB)
        sample()
        api.post(STOP.format("photos"))
        cgroups.gone("photos")

        sample()

        assert _listed(api, "photos")["memory_used"] is None

    def test_a_removed_memory_limit_is_none_in_the_kernels_pages_too(self, api, pools, docker, cgroups, time_now, alerts):
        """The removal leaves the host's memory as the limit, which the kernel rounds down to its pages."""
        _installed(api, docker)
        api.put(LIMITS.format("photos"), json={"memory_limit": 512 * MIB, "cpu_limit": None})
        api.put(LIMITS.format("photos"), json={"memory_limit": None, "cpu_limit": None})
        cgroups.write("photos", current=7900 * MIB)
        (cgroups.folder("photos") / "memory.max").write_text(f"{HOST_MEMORY - 64 * 1024}\n")

        sample()

        assert problems.of_source("apps") == {}
        docker.oom("photos", time_now[0])
        time_now[0] += 1
        sample()
        assert "because the NAS ran out of memory" in alerts[-1][2]

    def test_a_pass_asks_docker_twice_and_runs_no_command(self, api, pools, docker, cgroups, time_now, soc):
        """One summary list of the running apps' containers and one query of the events of the past:
        no stats stream, no inspect per container, no host command; the cgroups are files of the
        kernel's memory."""
        _installed(api, docker)
        for name in NAMES:
            cgroups.write(name, current=100 * MIB)
        docker.calls.clear()
        commands = len(soc.commands)

        sample()

        assert docker.called() == [("api.containers", None), ("events", None)]
        list_call = docker.calls[0]
        assert list_call.kwargs == {"all": False, "filters": {"label": "unos.app"}}
        events_call = docker.calls[1]
        assert events_call.kwargs["filters"] == {"type": "container", "event": "oom", "label": "unos.app"}
        assert events_call.kwargs["until"] == int(T0)
        assert len(soc.commands) == commands


class TestPasses:
    def test_a_clock_set_back_asks_from_its_new_time(self, api, pools, docker, time_now, caplog):
        _installed(api, docker)
        sample()
        time_now[0] -= 100

        with caplog.at_level(logging.WARNING):
            sample()

        events = [c.kwargs for c in docker.calls if c.name == "events"]
        assert (events[-1]["since"], events[-1]["until"]) == (int(T0) - 100, int(T0) - 100)
        assert "Could not read" not in caplog.text

    def test_a_pass_that_waits_for_docker_keeps_the_next_from_starting(self, monkeypatch, caplog):
        started = []
        release = threading.Event()

        def stalled():
            started.append(1)
            release.wait(10)
            return {}

        monkeypatch.setattr(usage, "_sample", stalled)
        monkeypatch.setattr(usage, "PASS_TIMEOUT_SECONDS", 0.05)
        monkeypatch.setattr(usage, "_pass", None)

        async def twice():
            await usage.sample_once()
            await usage.sample_once()

        try:
            with caplog.at_level(logging.WARNING):
                asyncio.run(twice())
        finally:
            release.set()
        assert started == [1]
        assert "Docker did not answer within 0.05 s" in caplog.text


class TestAlerts:
    def _limited(self, api, docker, cgroups, *, memory: int | None = 512 * MIB, cpus: float | None = None):
        _installed(api, docker)
        api.put(LIMITS.format("photos-database"), json={"memory_limit": memory, "cpu_limit": cpus})
        for name in NAMES:
            cgroups.write(name, current=50 * MIB)

    def test_memory_over_90_percent_of_the_limit_is_a_warning_alerted_once(
        self, api, pools, docker, cgroups, time_now, alerts
    ):
        self._limited(api, docker, cgroups)
        cgroups.write("photos-database", current=600 * MIB, inactive=140 * MIB)  # 460 MiB: 89.8%
        sample()
        assert problems.of_source("apps") == {}

        cgroups.write("photos-database", current=600 * MIB, inactive=130 * MIB)  # 470 MiB: 91.8%
        sample()

        found = problems.of_source("apps")
        assert list(found) == ["app_memory:photos-database"]
        problem = found["app_memory:photos-database"]
        assert (problem.severity, problem.kind, problem.subject) == ("warning", "app_memory", "photos-database")
        assert alerts == [(
            "warning",
            "photos-database is near its memory limit",
            "photos-database, a container of Photos, uses over 90% of its memory limit of 512 MiB. At the limit "
            "the kernel takes memory from it and then ends its processes: raise the limit on the Installed tab "
            "of the App Store.",
        )]

        # It stays down to 80%, and is not alerted again.
        cgroups.write("photos-database", current=420 * MIB)  # 82%
        sample()
        assert list(problems.of_source("apps")) == ["app_memory:photos-database"]
        assert len(alerts) == 1

        cgroups.write("photos-database", current=400 * MIB)  # 78%
        sample()
        assert problems.of_source("apps") == {}

        # A new crossing is a new alert.
        cgroups.write("photos-database", current=500 * MIB)
        sample()
        assert len(alerts) == 2

    def test_memory_without_a_limit_is_no_problem(self, api, pools, docker, cgroups, time_now, alerts):
        self._limited(api, docker, cgroups, memory=None)
        cgroups.write("photos", current=7 * GIB)

        sample()

        assert problems.of_source("apps") == {}

    def test_cpu_held_at_its_limit_for_5_minutes_is_a_warning(self, api, pools, docker, cgroups, time_now, alerts):
        self._limited(api, docker, cgroups, memory=None, cpus=1)
        used = 0
        # 0.95 CPUs for 4 minutes and 30 seconds: not yet 5 minutes.
        for _ in range(10):
            cgroups.write("photos-database", current=50 * MIB, usage_usec=used)
            sample()
            time_now[0] += 30
            used += 28_500_000
        assert problems.of_source("apps") == {}

        cgroups.write("photos-database", current=50 * MIB, usage_usec=used)
        sample()

        assert list(problems.of_source("apps")) == ["app_cpu:photos-database"]
        assert alerts == [(
            "warning",
            "photos-database is held at its CPU limit",
            "photos-database, a container of Photos, used over 90% of its CPU limit of 1 CPU on average for 5 "
            "minutes, so the kernel holds it back: raise the limit on the Installed tab of the App Store if it "
            "is too slow.",
        )]

        # Down to 0.6 CPUs over the last 5 minutes: below 70%, gone.
        for _ in range(10):
            time_now[0] += 30
            used += 18_000_000
            cgroups.write("photos-database", current=50 * MIB, usage_usec=used)
            sample()
        assert problems.of_source("apps") == {}
        assert len(alerts) == 1

    def test_an_oom_kill_is_an_error_kept_for_an_hour(self, api, pools, docker, cgroups, time_now, alerts):
        """Docker's event "oom" comes also when the kernel ended the container's main process and
        Docker started the container again, whose new cgroup counts no kill."""
        self._limited(api, docker, cgroups, memory=256 * MIB)
        sample()
        time_now[0] += 10
        docker.oom("photos-database", time_now[0])
        time_now[0] += 20

        sample()

        found = problems.of_source("apps")
        assert list(found) == ["app_oom:photos-database"]
        assert (found["app_oom:photos-database"].severity, found["app_oom:photos-database"].kind) == ("error", "app_oom")
        assert alerts == [(
            "error",
            "photos-database ran out of memory",
            "The kernel ended a process of photos-database, a container of Photos, that had reached its memory "
            "limit of 256 MiB: raise the limit on the Installed tab of the App Store.",
        )]

        # Another kill within the hour is not alerted again, and keeps the problem an hour after it.
        time_now[0] += 1200
        docker.oom("photos-database", time_now[0])
        time_now[0] += 30
        sample()
        assert len(alerts) == 1
        time_now[0] += 3600 - 60
        sample()
        assert list(problems.of_source("apps")) == ["app_oom:photos-database"]
        time_now[0] += 60
        sample()
        assert problems.of_source("apps") == {}

        docker.oom("photos-database", time_now[0])
        time_now[0] += 30
        sample()
        assert len(alerts) == 2

    def test_an_oom_kill_without_a_limit_names_the_memory_of_the_nas(
        self, api, pools, docker, cgroups, time_now, alerts
    ):
        self._limited(api, docker, cgroups, memory=None)
        sample()
        docker.oom("photos-cache", time_now[0] + 1)
        time_now[0] += 30

        sample()

        assert alerts == [(
            "error",
            "photos-cache ran out of memory",
            "The kernel ended a process of photos-cache, a container of Photos, because the NAS ran out of "
            "memory: give the apps that use the most memory limits.",
        )]

    def test_an_event_is_counted_once_across_passes(self, api, pools, docker, cgroups, time_now, alerts):
        """Docker compares whole seconds: the second at the end of one query is the start of the next."""
        self._limited(api, docker, cgroups)
        sample()
        docker.oom("photos-database", time_now[0] + 30)  # in the last second of the next query
        time_now[0] += 30
        sample()
        time_now[0] += 30
        sample()

        assert [title for _, title, _ in alerts] == ["photos-database ran out of memory"]
        since = [c.kwargs["since"] for c in docker.calls if c.name == "events"]
        until = [c.kwargs["until"] for c in docker.calls if c.name == "events"]
        assert since[1:] == until[:-1]

    def test_a_stopped_container_loses_its_memory_problem(self, api, pools, docker, cgroups, time_now, alerts):
        self._limited(api, docker, cgroups)
        cgroups.write("photos-database", current=500 * MIB)
        sample()
        assert list(problems.of_source("apps")) == ["app_memory:photos-database"]

        api.post(STOP.format("photos"))
        cgroups.gone("photos-database")
        sample()

        assert problems.of_source("apps") == {}

    def test_docker_that_does_not_answer_keeps_the_problems(
        self, api, pools, docker, cgroups, time_now, alerts, caplog
    ):
        self._limited(api, docker, cgroups)
        cgroups.write("photos-database", current=500 * MIB)
        sample()
        docker.fail("api.containers", None, DockerUnavailable("no socket"))

        with caplog.at_level(logging.WARNING):
            sample()
            sample()

        assert list(problems.of_source("apps")) == ["app_memory:photos-database"]
        assert caplog.text.count("Could not read the use of the apps' containers") == 1

    def test_a_container_whose_cgroup_cannot_be_read_keeps_its_problem(
        self, api, pools, docker, cgroups, time_now, alerts
    ):
        self._limited(api, docker, cgroups)
        cgroups.write("photos-database", current=500 * MIB)
        sample()
        (cgroups.folder("photos-database") / "memory.current").unlink()

        sample()

        assert list(problems.of_source("apps")) == ["app_memory:photos-database"]
        assert _listed(api, "photos-database")["memory_used"] is None


# ---------------------------------------------------------------------------
# The web UI's fakes and texts say what the server says (ADR-0189)
# ---------------------------------------------------------------------------

immich_images = test_app_stacks.immich_images
WEBUI = test_app_stacks.WEBUI_TESTS.parent


def _spec_text() -> str:
    """apps-limits.spec.ts with its white space as single spaces and its joined strings as one."""
    text = (test_app_stacks.WEBUI_TESTS / "apps-limits.spec.ts").read_text(encoding="utf-8")
    return " ".join(text.split()).replace("' + '", "")


def test_the_e2e_spec_refuses_a_low_limit_with_the_servers_text(api, pools, docker, immich_images, cgroups):
    """apps-limits.spec.ts answers a memory limit below what Immich's database uses with the server's
    409, word for word."""
    spec = _spec_text()
    assert install(api, {"template_id": "immich"}).status_code == 200
    cgroups.write("immich-database", current=300 * MIB)

    response = api.put(LIMITS.format("immich-database"), json={"memory_limit": 256 * MIB, "cpu_limit": None})

    assert response.status_code == 409, response.text
    assert f"'{response.json()['detail']}'" in spec


def test_the_e2e_spec_words_the_alerts_as_the_server_does(api, pools, docker, immich_images, cgroups, time_now, alerts):
    """The problems that apps-limits.spec.ts sends over its fake events socket are the server's."""
    spec = _spec_text()
    assert install(api, {"template_id": "immich"}).status_code == 200
    for name in ("immich", "immich-database", "immich-redis", "immich-machine-learning"):
        cgroups.write(name, current=10 * MIB)
    api.put(LIMITS.format("immich-database"), json={"memory_limit": 512 * MIB, "cpu_limit": None})
    api.put(LIMITS.format("immich-redis"), json={"memory_limit": 64 * MIB, "cpu_limit": None})
    api.put(LIMITS.format("immich-machine-learning"), json={"memory_limit": None, "cpu_limit": 2})
    used = 0
    for _ in range(11):
        for name in ("immich", "immich-redis"):
            cgroups.write(name, current=10 * MIB)
        cgroups.write("immich-database", current=480 * MIB)
        cgroups.write("immich-machine-learning", current=10 * MIB, usage_usec=used)
        sample()
        time_now[0] += 30
        used += 57_000_000
    docker.oom("immich-redis", time_now[0])
    time_now[0] += 1
    sample()

    found = problems.of_source("apps")
    assert sorted(found) == ["app_cpu:immich-machine-learning", "app_memory:immich-database", "app_oom:immich-redis"]
    for problem in found.values():
        assert f"title: '{problem.title}'" in spec, problem.title
        assert f"detail: '{problem.detail}'" in spec, problem.detail


def test_the_health_banner_names_every_kind_of_the_apps_problems():
    banner = (WEBUI / "src/lib/components/ProblemsBanner.svelte").read_text(encoding="utf-8")
    for kind in ("app_memory", "app_cpu", "app_oom"):
        assert f"{kind}: 'problems.kind.{kind}'" in banner, kind
