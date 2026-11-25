"""The critical apps of the SD card standby (system.sd-standby-critical-apps, ADR-0471, ADR-0500).

The host is the fake BPI-W3 of ``tests/standby_fake.py`` with a test pool ``usb`` beside the data pool,
Docker the fake client of ``tests/docker_fake.py``, and the database a file of the test's own. The
tests walk the mark with its dialog, sizes, refusals, roles and audit; the sync from a read-only
snapshot without caches and copies, with its images and definitions, and a sleeping pool; the move of a
failed pool's apps (its triggers and non-triggers), the last copy of a read-only pool, the binds on the
card, the tmpfs and the empty read-only folder; the move applied again at a start; the standby's own
start of the apps; the manual move, the discarding return, the planned repair and the 409s.
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import importlib
from src.apps import schemas as app_schemas
from src.apps.stacks import APP_POOL_LABEL, COMPANION_LABEL, UNOS_APP_LABEL
from src.containers import docker as docker_module
from src.database import Base, get_db
from src.main import app
from src.health import problems
from src.standby import apps as standby_apps
from src.standby import service
from src.standby.hostops import CARD_DIR
from src.storage import pools as storage_pools
from src.storage import spindown
from src.storage.schemas import PoolInfo
from src.times import utc_now, utc_text
from tests.docker_fake import StackDocker
from tests.standby_fake import CMDLINE_THROUGH_CHOOSER, mountinfo_line
from tests import test_standby
from tests.test_standby import Client, armed, audit_log

STATE = f"{CARD_DIR}/state.json"
POOL = "/mnt/pools/usb"
FOLDER = f"{POOL}/apps/jellyfin"
JELLYFIN_IMAGE = "jellyfin/jellyfin:10.11.4@sha256:" + "c" * 64
DB_IMAGE = "postgres:16@sha256:" + "d" * 64
LINK_IMAGE = "ghcr.io/linkwarden/linkwarden:v2@sha256:" + "e" * 64
GiB = 1024**3
apps_router = importlib.import_module("src.apps.router")
# The fake W3 and the alerts sent, as the standby's own tests have them.
fake = test_standby.fake
alerts = test_standby.alerts


# --- the fixtures ------------------------------------------------------------------------------------


@pytest.fixture
def db(monkeypatch, tmp_path):
    """The database of the marks, a file of this test's."""
    path = tmp_path / "marks.db"
    Base.metadata.create_all(create_engine(f"sqlite:///{path}"))
    maker = async_sessionmaker(create_async_engine(f"sqlite+aiosqlite:///{path}", poolclass=NullPool),
                               expire_on_commit=False)
    monkeypatch.setattr(standby_apps, "_session", maker)

    async def override_get_db():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    yield maker
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def docker(monkeypatch) -> StackDocker:
    found = StackDocker()
    monkeypatch.setattr(docker_module, "get_client", lambda: found)
    monkeypatch.setattr(apps_router, "get_client", lambda: found)
    return found


@pytest.fixture
def pools(monkeypatch):
    """The pools as storage.pools reads them: ``usb`` mounted, writable and healthy unless a test changes it."""
    found = {"usb": PoolInfo(name="usb", devices=["/dev/sdb"], total_size=64 * GiB, used_size=GiB, mountpoint=POOL,
                             raid_level="single", mounted=True, persistent=True, health="healthy")}

    def get_pool(name):
        if name not in found:
            raise storage_pools.PoolNotFoundError(name)
        return found[name]

    monkeypatch.setattr(storage_pools, "get_pool", get_pool)
    return found


class Disks(list):
    """The pools whose disks were asked whether they sleep, and those that sleep."""

    def __init__(self) -> None:
        super().__init__()
        self.asleep: set[str] = set()


@pytest.fixture
def awake(monkeypatch) -> Disks:
    """Whether the pools' disks sleep: the pools asked; none sleeps unless listed in ``asleep``."""
    asked = Disks()

    def sleeps(pool, cache=None):
        asked.append(pool)
        return pool in asked.asleep

    monkeypatch.setattr(spindown, "pool_sleeps", sleeps)
    return asked


@pytest.fixture
def standby(fake, db, docker, pools, awake, monkeypatch):
    """The W3 with the standby set up and generation 1 of the system, the test pool usb mounted, and
    Jellyfin installed on it, running, with its config, its cache, a copy an update kept, and its media
    on the pool's media folder."""
    monkeypatch.setattr(standby_apps, "_moving", set())
    monkeypatch.setattr(standby_apps, "_failed_passes", {})
    monkeypatch.setattr(standby_apps, "_checked_at", {})
    monkeypatch.setattr(standby_apps, "_move_failures", {})
    monkeypatch.setattr(standby_apps, "_passed", False)
    fake.extra_mounts.append(mountinfo_line(40, POOL, "btrfs", "/dev/sdb", "rw"))
    fake.subvolume_paths.add(POOL)
    fake.dirs |= {POOL, f"{POOL}/apps", FOLDER, f"{POOL}/media"}
    fake.files.update({
        f"{FOLDER}/config/system.xml": "<config/>",
        f"{FOLDER}/config/data/library.db": "rows",
        f"{FOLDER}/cache/images/a.jpg": "thumbnail",
        f"{FOLDER}/config.previous/system.xml": "<old/>",
        f"{POOL}/media/film.mkv": "film",
    })
    fake.file_sizes.update({f"{FOLDER}/config/data/library.db": 300 * 1024**2,
                            f"{FOLDER}/cache/images/a.jpg": 2 * GiB, f"{POOL}/media/film.mkv": 40 * GiB})
    armed(fake)
    docker.pulled(JELLYFIN_IMAGE)
    install(docker, "jellyfin", JELLYFIN_IMAGE, "usb", [
        (f"{FOLDER}/config", "/config", False), (f"{FOLDER}/cache", "/cache", False),
        (f"{POOL}/media", "/media", True), ("/etc/localtime", "/etc/localtime", True)])
    return fake


def install(docker: StackDocker, name: str, image: str, pool: str, mounts, *, companion: str | None = None,
            app: str | None = None, network: str | None = None, running: bool = True):
    """A container of an app as the install creates it: its binds, labels and network."""
    labels = {UNOS_APP_LABEL: app or name, APP_POOL_LABEL: pool, "unos.app.version": "1"}
    if companion:
        labels[COMPANION_LABEL] = companion
    host_config = {"Mounts": [{"Type": "bind", "Source": source, "Target": target, "ReadOnly": readonly}
                              for source, target, readonly in mounts],
                   "RestartPolicy": {"Name": "unless-stopped"}}
    networking = None
    if network:
        host_config["NetworkMode"] = network
        networking = {"EndpointsConfig": {network: {"Aliases": [companion] if companion else None}}}
    name_tag, _, digest = image.partition("@")
    docker.api._create(image=f"{name_tag.rpartition(':')[0]}@{digest}", name=name, labels=labels, host_config=host_config,
                       networking_config=networking, environment={"TZ": "UTC"})
    if running:
        docker.containers_[name].start()


def run(coroutine):
    return asyncio.run(coroutine)


def mark(app: str = "jellyfin", interval: int = 24) -> None:
    run(standby_apps.set_mark(app, True, interval))


def stored() -> dict:
    """state.json as the supervisor holds it."""
    return json.loads(json.dumps(service._state.stored))


def binds(docker: StackDocker, name: str) -> dict[str, tuple[str, bool]]:
    """The container's binds as Docker has them: target -> (source, read-only)."""
    attrs = docker.containers_[name].attrs
    return {m["Destination"]: (m["Source"], not m["RW"]) for m in attrs["Mounts"]}


def synced(fake, app: str = "jellyfin") -> None:
    """A first complete copy of the app on the card."""
    outcome = standby_apps.sync_app(app)
    assert outcome.result == "wrote"
    fake.changes.clear()


def pool_info(**changes) -> PoolInfo:
    base = dict(name="usb", devices=["/dev/sdb"], total_size=64 * GiB, used_size=GiB, mountpoint=POOL,
                raid_level="single", mounted=True, persistent=True, health="healthy")
    return PoolInfo(**{**base, **changes})


def raid_pass(pools: list[PoolInfo], managed: set[str] | None = frozenset({"usb", "data"}),
              changing: frozenset[str] = frozenset()) -> None:
    """One pass of the RAID monitor, with the moves and syncs it starts run to their end."""

    async def go():
        await standby_apps.on_pools_read(pools, set(managed) if managed is not None else None, changing)
        while standby_apps._tasks:
            await asyncio.gather(*list(standby_apps._tasks))

    run(go())


# --- the catalog's caches -------------------------------------------------------------------------------


class TestTheCatalogsCaches:
    def test_the_caches_under_the_apps_folders_are_marked(self):
        from src.apps.catalog import APP_CATALOG

        caches = {v.host for app in APP_CATALOG for spec in [app, *app.companions] for v in spec.volumes if v.cache}
        assert caches == {
            "{pool}/apps/jellyfin/cache", "{pool}/apps/immich/model-cache", "{pool}/apps/netdata/cache",
            "{pool}/apps/linkwarden/next-cache", "{pool}/apps/plex/transcode", "{pool}/apps/paperless-ngx/scratch"}

    @pytest.mark.parametrize("mapping", [
        dict(host="{pool}/media", container="/media"),
        dict(host="{pool}/apps/x/secret", container="/s", is_file=True),
        dict(host="{pool}/apps/x/cache", container="/c", readonly=True),
        dict(host="/var/cache", container="/c"),
    ])
    def test_a_cache_is_a_writable_folder_of_the_apps_own(self, mapping):
        with pytest.raises(ValueError, match="a cache is a writable folder"):
            app_schemas.VolumeMapping(cache=True, **mapping)


# --- the mark -------------------------------------------------------------------------------------------


class TestTheMark:
    def test_the_dialog_lists_what_is_copied_and_what_is_not_with_their_sizes(self, standby):
        with Client() as api:
            answer = api.get("/api/v1/apps/installed/jellyfin/critical")
        assert answer.status_code == 200, answer.text
        plan = answer.json()
        # Each folder once: a cache is not copied, nor the copy an update keeps.
        folders = [(item["path"], item["kind"], item["copied"], item["bytes"]) for item in plan["folders"]]
        assert folders == [
            (f"{FOLDER}/config", "data", True, 300 * 1024**2 + len("<config/>")),
            (f"{FOLDER}/cache", "cache", False, 2 * GiB),
            (f"{FOLDER}/config.previous", "previous", False, len("<old/>")),
            (f"{POOL}/media", "other_pool", False, None),
        ]
        assert plan["copied_bytes"] == 300 * 1024**2 + len("<config/>")
        assert plan["images_bytes"] == 4000
        assert (plan["card_size"], plan["card_free"]) == (31_000_000_000, 20_000_000_000)
        assert plan["reserve"] == 3_100_000_000
        assert plan["fits"] is True and plan["critical"] is False and plan["interval_hours"] == 24
        assert plan["intervals"] == [1, 2, 4, 6, 12, 24]
        assert plan["mib_per_sync"] is None

    def test_an_update_s_copy_is_listed_as_not_copied(self, standby, docker):
        # Linkwarden's database is copied before an update (copy_before_update, ADR-0408).
        folder = f"{POOL}/apps/linkwarden"
        standby.files.update({f"{folder}/database/data/PG_VERSION": "16", f"{folder}/database/data.previous/PG_VERSION": "15",
                              f"{folder}/data/archive.html": "page"})
        docker.pulled(DB_IMAGE)
        docker.pulled(LINK_IMAGE)
        docker.network("unos-app-linkwarden", labels={UNOS_APP_LABEL: "linkwarden"})
        install(docker, "linkwarden-database", DB_IMAGE, "usb", [(f"{folder}/database/data", "/var/lib/postgresql/data", False)],
                companion="database", app="linkwarden", network="unos-app-linkwarden")
        install(docker, "linkwarden", LINK_IMAGE, "usb", [(f"{folder}/data", "/data/data", False)],
                app="linkwarden", network="unos-app-linkwarden")
        with Client() as api:
            plan = api.get("/api/v1/apps/installed/linkwarden/critical").json()
        kinds = {item["path"]: (item["kind"], item["copied"]) for item in plan["folders"]}
        assert kinds[f"{folder}/database/data.previous"] == ("previous", False)
        assert kinds[f"{folder}/database"] == ("data", True)
        sizes = {item["path"]: item["bytes"] for item in plan["folders"]}
        assert sizes[f"{folder}/database"] == len("16")  # without the copy

    def test_marks_with_an_interval_audited_and_kept_in_the_table(self, standby, db):
        with Client() as api:
            answer = api.put("/api/v1/apps/installed/jellyfin/critical", json={"critical": True, "interval_hours": 6})
            listed = api.get("/api/v1/apps/installed").json()
        assert answer.status_code == 200, answer.text
        assert answer.json()["critical"] is True and answer.json()["interval_hours"] == 6
        assert {mark: value.interval for mark, value in run(standby_apps.marks()).items()} == {"jellyfin": 6}
        assert [(app["name"], app["critical"], app["on_standby_card"]) for app in listed] == [("jellyfin", True, False)]
        entries = [e for e in audit_log() if e["action"] == "app_critical_changed"]
        assert [(e["resource_id"], e["details"], e["username"]) for e in entries] == [
            ("jellyfin", "Marked jellyfin critical, synced every 6 h", "admin")]

    def test_an_interval_change_is_audited_and_the_same_is_not(self, standby):
        mark(interval=24)
        with Client() as api:
            api.put("/api/v1/apps/installed/jellyfin/critical", json={"critical": True, "interval_hours": 1})
            api.put("/api/v1/apps/installed/jellyfin/critical", json={"critical": True, "interval_hours": 1})
        assert [e["details"] for e in audit_log() if e["action"] == "app_critical_changed"] == [
            "Changed the sync interval of jellyfin from 24 h to 1 h"]

    def test_a_mark_that_leaves_less_than_the_reserve_is_refused(self, standby):
        # 3.1 GB of reserve: 3.3 GB free less the copy (0.3 GB) and twice the images is below it.
        standby.card_df = (31_000_000_000, 3_300_000_000)
        with Client() as api:
            answer = api.put("/api/v1/apps/installed/jellyfin/critical", json={"critical": True})
        assert answer.status_code == 409
        assert "cannot take jellyfin" in answer.json()["detail"]
        assert run(standby_apps.marks()) == {}
        assert not [e for e in audit_log() if e["action"] == "app_critical_changed"]

    def test_an_interval_that_is_not_offered_is_refused(self, standby):
        with Client() as api:
            answer = api.put("/api/v1/apps/installed/jellyfin/critical", json={"critical": True, "interval_hours": 3})
        assert answer.status_code == 422

    def test_without_a_standby_the_mark_is_not_offered(self, fake, db, docker, pools):
        docker.pulled(JELLYFIN_IMAGE)
        install(docker, "jellyfin", JELLYFIN_IMAGE, "usb", [(f"{FOLDER}/config", "/config", False)])
        service.refresh()
        with Client() as api:
            offered = api.get("/api/v1/apps/standby").json()
            put = api.put("/api/v1/apps/installed/jellyfin/critical", json={"critical": True})
            get = api.get("/api/v1/apps/installed/jellyfin/critical")
        assert offered == {"offered": False, "reason": "The SD standby is not set up: System > Standby sets it up",
                           "running_on_standby": False}
        assert (put.status_code, get.status_code) == (409, 409)

    def test_on_the_standby_s_root_the_apps_page_says_so(self, standby):
        standby.files["/proc/cmdline"] = CMDLINE_THROUGH_CHOOSER.replace("unos.root=emmc", "unos.root=card")
        service.refresh()
        with Client() as api:
            offered = api.get("/api/v1/apps/standby").json()
        assert offered["running_on_standby"] is True and offered["offered"] is False

    def test_administrators_only(self, standby):
        with Client(is_admin=False) as api:
            answers = [api.get("/api/v1/apps/standby"), api.get("/api/v1/apps/installed/jellyfin/critical"),
                       api.put("/api/v1/apps/installed/jellyfin/critical", json={"critical": True}),
                       api.post("/api/v1/system/standby/apps/move", json={"reason": "drill"}),
                       api.post("/api/v1/system/standby/apps/return", json={"model": "SC32G"})]
        assert [answer.status_code for answer in answers] == [403] * 5

    def test_an_unmark_deletes_its_copies_on_the_card(self, standby):
        mark()
        synced(standby)
        assert f"{CARD_DIR}/@app-jellyfin-1" in standby.subvolume_paths
        with Client() as api:
            answer = api.put("/api/v1/apps/installed/jellyfin/critical", json={"critical": False})
        assert answer.status_code == 200
        assert not any(path.startswith(f"{CARD_DIR}/@app-") for path in standby.subvolume_paths)
        assert "apps" not in stored()
        assert [e["details"] for e in audit_log() if e["action"] == "app_critical_changed"] == [
            "Unmarked jellyfin: no longer critical, its copies on the SD standby deleted"]


# --- the sync ---------------------------------------------------------------------------------------------


class TestTheSync:
    def test_copies_from_a_read_only_snapshot_without_caches_and_copies_into_a_new_generation(self, standby, docker):
        mark()
        outcome = standby_apps.sync_app("jellyfin")
        assert (outcome.result, outcome.generation) == ("wrote", 1)
        snapshot = f"{POOL}/.unos-standby-snapshot"
        gen = f"{CARD_DIR}/@app-jellyfin-1"
        changes = [" ".join(cmd) for cmd in standby.changes]
        assert f"btrfs subvolume snapshot -r {POOL} {snapshot}" in changes
        assert f"btrfs subvolume create {gen}" in changes
        copy = next(cmd for cmd in standby.unit_calls if cmd[0] == "rsync" and "--dry-run" not in cmd)
        assert copy[-2:] == [f"{snapshot}/apps/jellyfin/", f"{gen}/data/"]
        assert {"--exclude=*.previous", "--exclude=/cache"} <= set(copy)
        assert sorted(name[len(gen):] for name in standby.files if name.startswith(gen + "/data/")) == [
            "/data/config/data/library.db", "/data/config/system.xml"]
        image = docker.containers_["jellyfin"].attrs["Image"]
        assert standby.files[f"{gen}/images/{image.split(':', 1)[1]}.tar.gz"] == "archive"
        definitions = json.loads(standby.files[f"{gen}/definitions.json"])
        assert [item["name"] for item in definitions["containers"]] == ["jellyfin"]
        assert definitions["pool"] == "usb"
        # The snapshot goes once the copy is complete; state.json names the generation.
        assert f"btrfs subvolume delete {snapshot}" in [" ".join(cmd) for cmd in standby.calls]
        assert snapshot not in standby.subvolume_paths
        record = stored()["apps"]["jellyfin"]
        assert record["generation"] == 1 and record["pool"] == "usb"
        assert record["bytes"]["data"] == 300 * 1024**2 + len("<config/>")

    def test_a_sync_that_finds_nothing_writes_nothing(self, standby):
        mark()
        synced(standby)
        outcome = standby_apps.sync_app("jellyfin")
        assert outcome.result == "unchanged"
        assert standby.changes == []

    def test_a_change_writes_the_next_generation_and_deletes_the_last(self, standby):
        mark()
        synced(standby)
        standby.compare["app:jellyfin"] = [">f.st...... config/system.xml"]
        outcome = standby_apps.sync_app("jellyfin")
        assert outcome.generation == 2
        assert f"{CARD_DIR}/@app-jellyfin-2" in standby.subvolume_paths
        assert f"{CARD_DIR}/@app-jellyfin-1" not in standby.subvolume_paths
        assert stored()["apps"]["jellyfin"]["generation"] == 2
        assert ["btrfs", "subvolume", "snapshot", f"{CARD_DIR}/@app-jellyfin-1", f"{CARD_DIR}/@app-jellyfin-2"] in standby.changes

    def test_the_writes_a_week_count_neither_the_first_copy_nor_the_image_archives(self, standby, docker):
        # The first sync copies the whole folder and archives the images, and an update archives its new
        # image once, whatever the interval: neither is what each sync of an interval writes. On the dev
        # device the first copy of Uptime Kuma wrote 588.7 MiB, which made the dialog offer an interval of
        # 1 hour at 98902 MiB a week (ADR-0503).
        mark()
        assert standby_apps.sync_app("jellyfin").result == "wrote"
        # Before the copy and after it: the first copy is not measured, so no flush follows its rsync at once.
        assert standby.changes.count(["sync", "-f", CARD_DIR]) == 2
        standby.changes.clear()
        assert stored()["apps"]["jellyfin"]["mib"] == []
        with Client() as api:
            assert api.get("/api/v1/apps/installed/jellyfin/critical").json()["mib_per_sync"] is None
        standby.compare["app:jellyfin"] = [">f.st...... config/system.xml"]
        assert standby_apps.sync_app("jellyfin").result == "wrote"
        data_only = stored()["apps"]["jellyfin"]["mib"]
        assert len(data_only) == 1 and data_only[0] > 0
        standby.changes.clear()
        # An update: the same change of the folder and a new image, whose archive the sync writes.
        updated = "sha256:" + "e" * 64
        docker.containers_["jellyfin"].attrs["Image"] = updated
        docker.pulled(updated)
        outcome = standby_apps.sync_app("jellyfin")
        archive = f"{CARD_DIR}/@app-jellyfin-3/images/{'e' * 64}.tar.gz"
        assert outcome.result == "wrote" and standby.files.get(archive) == "archive"
        assert standby.changes.count(["sync", "-f", CARD_DIR]) == 2 + 2  # and before and after the archives
        recent = stored()["apps"]["jellyfin"]["mib"]
        assert recent == [data_only[0], data_only[0]]
        # The sync's own answer still counts everything it wrote.
        assert outcome.mib is not None and outcome.mib > data_only[0]

    def test_a_failed_copy_keeps_the_complete_generation(self, standby):
        mark()
        synced(standby)
        standby.compare["app:jellyfin"] = [">f.st...... config/system.xml"]
        standby.copy_status["app:jellyfin"] = 23
        with pytest.raises(standby_apps.StandbyFailed):
            standby_apps.sync_app("jellyfin")
        assert stored()["apps"]["jellyfin"]["generation"] == 1
        assert f"{CARD_DIR}/@app-jellyfin-2" not in standby.subvolume_paths
        assert f"{POOL}/.unos-standby-snapshot" not in standby.subvolume_paths

    def test_a_sleeping_pool_is_passed_over_and_synced_at_the_first_pass_that_finds_it_awake(self, standby, awake):
        mark(interval=1)
        awake.asleep.add("usb")
        raid_pass([pool_info()])
        assert awake == ["usb"]
        assert not [cmd for cmd in standby.calls if cmd[0] in ("rsync", "du", "ls") and any(POOL in part for part in cmd)]
        assert "apps" not in stored()
        awake.asleep.clear()
        raid_pass([pool_info()])
        assert stored()["apps"]["jellyfin"]["generation"] == 1

    def test_an_interval_of_an_hour_is_due_an_hour_after_the_last_sync(self, standby):
        now = utc_now()
        mark(interval=1)
        standby_apps.update_app_record("jellyfin", lambda r: {**r, "synced_at": utc_text(now - timedelta(minutes=50))})
        assert not standby_apps.interval_due("jellyfin", 1, now, None)
        assert standby_apps.interval_due("jellyfin", 1, now + timedelta(minutes=8), None)

    def test_a_daily_interval_is_due_after_the_system_s_daily_time(self, standby):
        now = utc_now().replace(hour=10, minute=0, second=0, microsecond=0)
        today = now.replace(hour=4)
        standby_apps.update_app_record("jellyfin", lambda r: {**r, "synced_at": utc_text(today - timedelta(hours=1))})
        assert standby_apps.interval_due("jellyfin", 24, now, today)
        standby_apps.update_app_record("jellyfin", lambda r: {**r, "synced_at": utc_text(today + timedelta(minutes=5))})
        assert not standby_apps.interval_due("jellyfin", 24, now, today)

    def test_a_copy_without_a_complete_sync_for_3_days_is_named(self, standby, awake, alerts):
        mark()
        synced(standby)
        old = utc_text(utc_now() - timedelta(days=3, hours=1))
        standby_apps.update_app_record("jellyfin", lambda r: {**r, "synced_at": old})
        # Not before the first pass of the RAID monitor since the start, which checks the copy.
        run(service.update_problems())
        assert "standby_copy_old:jellyfin" not in problems.of_source("standby")
        awake.asleep.add("usb")
        raid_pass([pool_info()])
        run(service.update_problems())
        found = {p.key: p for p in problems.of_source("standby").values()}
        assert "standby_copy_old:jellyfin" in found
        assert found["standby_copy_old:jellyfin"].severity == "warning"
        assert old in found["standby_copy_old:jellyfin"].detail


# --- the move of a failed pool's apps ---------------------------------------------------------------------


class TestTheFailover:
    def test_a_pool_forced_read_only_moves_its_apps_after_one_more_copy(self, standby, docker, alerts):
        mark()
        synced(standby)
        standby.compare["app:jellyfin"] = [">f.st...... config/system.xml"]
        raid_pass([pool_info(read_only=True, forced_read_only=True, health="error")])
        # The last copy read the pool as it is: no snapshot of a read-only pool.
        assert not [cmd for cmd in standby.changes if cmd[:4] == ["btrfs", "subvolume", "snapshot", "-r"]]
        record = stored()["apps"]["jellyfin"]
        assert record["generation"] == 2
        assert record["failed_over"]["reason"] == "pool_read_only" and record["failed_over"]["copy"] is True
        run_copy = f"{CARD_DIR}/@app-jellyfin-run"
        assert run_copy in standby.subvolume_paths
        assert binds(docker, "jellyfin") == {
            "/config": (f"{run_copy}/data/config", False),
            "/media": (standby_apps.EMPTY_DIR, True),
            "/etc/localtime": ("/etc/localtime", True),
        }
        container = docker.containers_["jellyfin"].attrs
        assert container["HostConfig"]["Tmpfs"] == {"/cache": "size=256m"}
        assert container["State"]["Running"] is True
        assert APP_POOL_LABEL not in container["Config"]["Labels"]
        assert container["Config"]["Labels"][standby_apps.CARD_LABEL] == "usb"
        assert [(e["action"], e["username"], e["success"]) for e in audit_log() if e["action"] == "standby_apps_moved"] == [
            ("standby_apps_moved", "system", True)]
        problem = problems.of_source("standby")["apps_on_standby:jellyfin"]
        assert problem.severity == "error" and "jellyfin (a copy of" in problem.detail
        assert [event.title for event in alerts] == ["Critical apps run from the SD standby"]

    def test_an_app_that_docker_could_not_start_without_its_pool_starts_on_the_card(self, standby, docker):
        # The NAS started while the pool was missing: Docker's start of the app failed (State.Error), its
        # policy would restart it, and nobody stopped it.
        mark()
        synced(standby)
        docker.containers_["jellyfin"].halt()
        docker.containers_["jellyfin"].attrs["State"]["Error"] = "error mounting /mnt/pools/usb/apps/jellyfin/config"
        raid_pass([])
        raid_pass([])
        assert stored()["apps"]["jellyfin"]["failed_over"]["running"] == ["jellyfin"]
        assert docker.containers_["jellyfin"].attrs["State"]["Running"] is True

    def test_a_move_whose_create_fails_is_recorded_and_the_start_creates_the_app_on_the_card(self, standby, docker):
        from docker.errors import APIError

        mark()
        synced(standby)
        docker.fail("api.create_container_from_config", "jellyfin", APIError("500 Server Error: no space left"))
        moved = run(standby_apps.move(["jellyfin"], "drill", request=None, username="admin"))
        assert moved[0].error and "no space left" in moved[0].error
        assert "jellyfin" not in docker.containers_
        assert stored()["apps"]["jellyfin"]["failed_over"]["error"]
        docker.clear()
        service._state = service._State()
        run(service.before_serving())
        assert binds(docker, "jellyfin")["/config"][0] == f"{CARD_DIR}/@app-jellyfin-run/data/config"
        assert [e["details"] for e in audit_log() if e["action"] == "standby_apps_moved"][-1] == (
            "jellyfin runs from the SD standby's card again at the start of UNOS")

    def test_one_lock_holds_every_write_and_read_of_state_json(self, standby, monkeypatch):
        """A move's record, a sync's result and a read of the card each hold the same lock: neither
        replaces what another wrote (ADR-0497: the lock is found held while each runs)."""
        held = []
        save = service._save_stored

        def saving(stored):
            held.append(("write", service._stored_lock.locked()))
            save(stored)

        monkeypatch.setattr(service, "_save_stored", saving)
        read = standby.run

        def reading(cmd, **kwargs):
            if cmd[:2] == ["cat", "--"] and cmd[2] == STATE:
                held.append(("read", service._stored_lock.locked()))
            return read(cmd, **kwargs)

        monkeypatch.setattr(service.host, "run_on_host", reading)
        standby_apps.update_app_record("jellyfin", lambda r: {**r, "pool": "usb"})
        service.merge_stored({"sync_time": "05:00"})
        service._refresh_card(service.StandbyHost(), service._state)
        assert held == [("write", True), ("write", True), ("read", True)]
        assert stored()["apps"]["jellyfin"]["pool"] == "usb" and stored()["sync_time"] == "05:00"

    def test_a_managed_pool_not_mounted_moves_its_apps_at_the_second_pass(self, standby, docker):
        mark()
        synced(standby)
        raid_pass([pool_info(mounted=False, health="unmounted", mountpoint=None)])
        assert "failed_over" not in stored()["apps"]["jellyfin"]
        raid_pass([pool_info(mounted=False, health="unmounted", mountpoint=None)])
        assert stored()["apps"]["jellyfin"]["failed_over"]["reason"] == "pool_failed"
        assert binds(docker, "jellyfin")["/config"][0].startswith(f"{CARD_DIR}/@app-jellyfin-run/")

    def test_a_pool_whose_disk_is_gone_is_failed_too(self, standby):
        mark()
        synced(standby)
        raid_pass([])
        raid_pass([])
        assert stored()["apps"]["jellyfin"]["failed_over"]["reason"] == "pool_failed"

    def test_a_good_pass_between_two_bad_ones_starts_the_count_again(self, standby):
        mark()
        synced(standby)
        raid_pass([])
        raid_pass([pool_info()])
        raid_pass([])
        assert "failed_over" not in stored()["apps"]["jellyfin"]

    def test_a_single_pool_missing_its_device_moves_and_a_raid1_missing_one_does_not(self, standby):
        mark()
        synced(standby)
        raid_pass([pool_info(raid_level="raid1", missing_devices=1, health="degraded")])
        raid_pass([pool_info(raid_level="raid1", missing_devices=1, health="degraded")])
        assert "failed_over" not in stored()["apps"]["jellyfin"]
        raid_pass([pool_info(missing_devices=1, health="degraded")])
        raid_pass([pool_info(missing_devices=1, health="degraded")])
        assert stored()["apps"]["jellyfin"]["failed_over"]["reason"] == "pool_failed"

    def test_a_pool_unmounted_through_unos_moves_nothing(self, standby):
        mark()
        synced(standby)
        # Its fstab entry is gone: no longer managed.
        for _ in range(3):
            raid_pass([pool_info(mounted=False, health="unmounted", persistent=False)], managed={"data"})
        # Read while Unmount changed it: not judged.
        for _ in range(3):
            raid_pass([pool_info(mounted=False, health="unmounted")], changing=frozenset({"usb"}))
        assert "failed_over" not in stored()["apps"]["jellyfin"]

    def test_an_app_without_a_copy_stays_stopped_and_is_named(self, standby, docker):
        mark()
        raid_pass([])
        raid_pass([])
        assert docker.containers_["jellyfin"].attrs["State"]["Running"] is False
        # Its binds are as they were: it is not created again on the card.
        assert binds(docker, "jellyfin")["/config"] == (f"{FOLDER}/config", False)
        assert stored()["apps"]["jellyfin"]["failed_over"]["copy"] is False
        assert "jellyfin (no copy: stopped)" in problems.of_source("standby")["apps_on_standby:jellyfin"].detail

    def test_an_app_that_runs_from_the_card_gets_no_sync_and_refuses_its_changes(self, standby, docker):
        mark(interval=1)
        synced(standby)
        run(standby_apps.move(["jellyfin"], "drill", request=None, username="admin"))
        standby.changes.clear()
        raid_pass([pool_info()])
        assert standby.changes == []
        with Client() as api:
            answers = [api.post("/api/v1/apps/installed/jellyfin/update"),
                       api.post("/api/v1/apps/installed/jellyfin/rollback"),
                       api.delete("/api/v1/apps/installed/jellyfin"),
                       api.put("/api/v1/apps/installed/jellyfin/critical", json={"critical": False})]
        assert [answer.status_code for answer in answers] == [409] * 4
        assert all("runs from the SD standby" in answer.json()["detail"] for answer in answers)
        with pytest.raises(Exception) as refused:
            standby_apps.refuse_failed_over("jellyfin", "its install")
        assert refused.value.status_code == 409

    def test_the_move_is_applied_again_before_the_supervisor_serves(self, standby, docker):
        mark()
        synced(standby)
        run(standby_apps.move(["jellyfin"], "drill", request=None, username="admin"))
        # Docker started a pool-bound container of the app at boot (a restore of the old one, say).
        docker.containers_["jellyfin"].halt()
        del docker.containers_["jellyfin"]
        install(docker, "jellyfin", JELLYFIN_IMAGE, "usb", [(f"{FOLDER}/config", "/config", False)])
        service._state = service._State()
        run(service.before_serving())
        assert binds(docker, "jellyfin")["/config"][0] == f"{CARD_DIR}/@app-jellyfin-run/data/config"
        assert docker.containers_["jellyfin"].attrs["State"]["Running"] is True


# --- the manual move, the return and the planned repair ----------------------------------------------------


class TestTheMovesOfTheStandbyTab:
    def test_move_to_the_card_then_return_discarding_the_card_s_changes(self, standby, docker):
        mark()
        synced(standby)
        with Client() as api:
            moved = api.post("/api/v1/system/standby/apps/move", json={"reason": "drill"})
            status = api.get("/api/v1/system/standby").json()
        assert moved.status_code == 200, moved.text
        assert moved.json()["apps"] == [{"app": "jellyfin", "has_copy": True, "age": "0 h", "error": None, "mib": None,
                                         "backup": None}]
        assert [(a["app"], a["moved"]["reason"], a["return_blocked"]) for a in status["apps"]] == [("jellyfin", "drill", None)]
        # What the app changed on the card is discarded by the return.
        standby.files[f"{CARD_DIR}/@app-jellyfin-run/data/config/new.xml"] = "card only"
        with Client() as api:
            wrong = api.post("/api/v1/system/standby/apps/return", json={"model": "nope", "discard": True})
            back = api.post("/api/v1/system/standby/apps/return", json={"model": "SC32G", "discard": True})
        assert wrong.status_code == 409
        assert back.status_code == 200, back.text
        assert binds(docker, "jellyfin")["/config"] == (f"{FOLDER}/config", False)
        assert binds(docker, "jellyfin")["/cache"] == (f"{FOLDER}/cache", False)
        container = docker.containers_["jellyfin"].attrs
        assert container["Config"]["Labels"][APP_POOL_LABEL] == "usb" and container["State"]["Running"] is True
        assert f"{CARD_DIR}/@app-jellyfin-run" not in standby.subvolume_paths
        assert f"{CARD_DIR}/@app-jellyfin-run/data/config/new.xml" not in standby.files
        assert "failed_over" not in stored()["apps"]["jellyfin"]
        actions = [(e["action"], e["username"]) for e in audit_log() if e["action"].startswith("standby_apps")]
        assert actions == [("standby_apps_moved", "admin"), ("standby_apps_returned", "admin")]

    def test_a_return_waits_for_the_pool(self, standby, pools):
        mark()
        synced(standby)
        run(standby_apps.move(["jellyfin"], "drill", request=None, username="admin"))
        pools["usb"] = pool_info(mounted=False, health="unmounted", mountpoint=None)
        with Client() as api:
            answer = api.post("/api/v1/system/standby/apps/return", json={"model": "SC32G", "discard": True})
            status = api.get("/api/v1/system/standby").json()
        assert answer.status_code == 409
        assert "the pool usb is not mounted" in answer.json()["detail"]
        assert status["apps"][0]["return_blocked"] == "the pool usb is not mounted"

    def test_a_return_needs_every_pool_folder_its_containers_bind(self, standby, docker):
        mark()
        synced(standby)
        run(standby_apps.move(["jellyfin"], "drill", request=None, username="admin"))
        del standby.files[f"{POOL}/media/film.mkv"]
        standby.dirs.discard(f"{POOL}/media")
        with Client() as api:
            answer = api.post("/api/v1/system/standby/apps/return", json={"model": "SC32G", "discard": True})
        assert answer.status_code == 409 and f"{POOL}/media is not there" in answer.json()["detail"]
        # Nothing changed: the app runs from the card.
        assert binds(docker, "jellyfin")["/config"][0] == f"{CARD_DIR}/@app-jellyfin-run/data/config"
        assert stored()["apps"]["jellyfin"]["failed_over"]["reason"] == "drill"

    def test_an_uninstall_unmarks_the_app_and_deletes_its_copies(self, standby):
        mark()
        synced(standby)
        with Client() as api:
            answer = api.delete("/api/v1/apps/installed/jellyfin")
        assert answer.status_code == 200, answer.text
        assert run(standby_apps.marks()) == {}
        assert not any(path.startswith(f"{CARD_DIR}/@app-") for path in standby.subvolume_paths)
        assert [e["details"] for e in audit_log() if e["action"] == "app_critical_changed"] == [
            "Unmarked jellyfin, which was uninstalled: its copies on the SD standby deleted"]

    def test_a_planned_repair_moves_the_pool_s_apps_so_the_unmount_no_longer_counts_them(self, standby, docker):
        mark()
        synced(standby)
        # The app changed since its last sync: the planned move copies it once more first.
        standby.compare["app:jellyfin"] = [">f.st...... config/system.xml"]
        with Client() as api:
            answer = api.post("/api/v1/system/standby/apps/move", json={"reason": "planned", "pool": "usb"})
            again = api.post("/api/v1/system/standby/apps/move", json={"reason": "planned", "pool": "usb"})
        assert answer.status_code == 200
        assert again.status_code == 409 and "No critical app is left to move" in again.json()["detail"]
        # The unmount's check of the containers that use the pool: neither a bind of the pool nor its label.
        labels = docker.containers_["jellyfin"].attrs["Config"]["Labels"]
        assert labels.get(APP_POOL_LABEL) is None
        assert not any(source.startswith(POOL) for source, _ in binds(docker, "jellyfin").values())
        assert stored()["apps"]["jellyfin"]["failed_over"]["reason"] == "planned"
        assert stored()["apps"]["jellyfin"]["failed_over"]["generation"] == 2

    def test_remove_the_standby_waits_for_the_apps_return(self, standby):
        mark()
        synced(standby)
        run(standby_apps.move(["jellyfin"], "drill", request=None, username="admin"))
        with Client() as api:
            answer = api.request("DELETE", "/api/v1/system/standby", json={"model": "SC32G"})
        assert answer.status_code == 409 and "return them to their pool first" in answer.json()["detail"]


# --- the standby's own start --------------------------------------------------------------------------------


class TestOnTheStandby:
    def test_creates_the_critical_apps_from_their_definitions(self, standby, docker, pools):
        mark()
        synced(standby)
        definitions = json.loads(standby.files[f"{CARD_DIR}/@app-jellyfin-1/definitions.json"])
        # The card's Docker: none of the app's containers, nor its image under its name.
        docker.containers_.clear()
        image = definitions["containers"][0]["image"]
        docker.images_ = {key: value for key, value in docker.images_.items() if value.id == image}
        for value in docker.images_.values():
            value.names = set()
        created = standby_apps.start_on_standby({"jellyfin"})
        assert created == ["jellyfin"]
        assert ["docker", "load", "-i", f"{CARD_DIR}/@app-jellyfin-1/images/{image.split(':', 1)[1]}.tar.gz"] in standby.changes
        # Its pool is mounted and healthy: its pool binds, its image by id.
        assert binds(docker, "jellyfin")["/config"] == (f"{FOLDER}/config", False)
        assert docker.containers_["jellyfin"].attrs["Config"]["Image"] == image

    def test_an_app_whose_pool_is_missing_runs_from_the_card(self, standby, docker, pools):
        mark()
        synced(standby)
        docker.containers_.clear()
        del pools["usb"]
        assert standby_apps.start_on_standby({"jellyfin"}) == ["jellyfin"]
        assert binds(docker, "jellyfin")["/config"][0] == f"{CARD_DIR}/@app-jellyfin-run/data/config"
        assert stored()["apps"]["jellyfin"]["failed_over"]["reason"] == "pool_failed"

    def test_an_app_with_companions_is_created_companions_first_on_its_network(self, standby, docker):
        folder = f"{POOL}/apps/linkwarden"
        standby.files.update({f"{folder}/database/data/PG_VERSION": "16", f"{folder}/data/archive.html": "page"})
        docker.pulled(DB_IMAGE)
        docker.pulled(LINK_IMAGE)
        docker.network("unos-app-linkwarden", labels={UNOS_APP_LABEL: "linkwarden"})
        install(docker, "linkwarden-database", DB_IMAGE, "usb", [(f"{folder}/database/data", "/var/lib/postgresql/data", False)],
                companion="database", app="linkwarden", network="unos-app-linkwarden")
        install(docker, "linkwarden", LINK_IMAGE, "usb", [(f"{folder}/data", "/data/data", False)],
                app="linkwarden", network="unos-app-linkwarden")
        mark("linkwarden")
        synced(standby, "linkwarden")
        docker.containers_.clear()
        docker.networks_.clear()
        docker.calls.clear()
        assert standby_apps.start_on_standby({"linkwarden"}) == ["linkwarden"]
        assert [(c.name, c.subject) for c in docker.calls if c.name in ("networks.create", "api.create_container_from_config")] == [
            ("networks.create", "unos-app-linkwarden"),
            ("api.create_container_from_config", "linkwarden-database"),
            ("api.create_container_from_config", "linkwarden")]
        assert docker.containers_["linkwarden-database"].attrs["NetworkSettings"]["Networks"] == {
            "unos-app-linkwarden": {"Aliases": ["database"]}}


# --- the Standby tab's status ---------------------------------------------------------------------------------


class TestTheStatus:
    def test_a_sync_that_wrote_a_generation_measures_the_card_by_part(self, standby):
        gen = f"{CARD_DIR}/@sys-1"
        standby.files.update({f"{gen}/etc/hostname": "nas", f"{gen}/var/lib/unos-standby/images/supervisor.tar.gz": "a",
                              f"{gen}/var/lib/unos-standby/volumes/unos-data/unos.db": "b",
                              f"{gen}/var/lib/docker/image/x": "c"})
        standby.file_sizes.update({f"{gen}/etc/hostname": 2_000_000_000,
                                   f"{gen}/var/lib/unos-standby/images/supervisor.tar.gz": 700_000_000,
                                   f"{gen}/var/lib/unos-standby/volumes/unos-data/unos.db": 3_000_000,
                                   f"{gen}/var/lib/docker/image/x": 500_000_000})
        assert service._measure_space(1) == {"system": 2_000_000_000, "unos_images": 700_000_000,
                                             "unos_volumes": 3_000_000, "loaded_images": 500_000_000}

    def test_the_card_s_space_by_part_and_the_apps(self, standby):
        mark(interval=6)
        synced(standby)
        service._state.stored["space"] = {"system": 2_000_000_000, "unos_images": 700_000_000,
                                          "unos_volumes": 3_000_000, "loaded_images": 0}
        with Client() as api:
            status = api.get("/api/v1/system/standby").json()
        assert status["space"] == {"size": 31_000_000_000, "free": 20_000_000_000, "reserve": 3_100_000_000,
                                   "system": 2_000_000_000, "unos_images": 700_000_000, "unos_volumes": 3_000_000,
                                   "loaded_images": 0}
        app = status["apps"][0]
        assert (app["app"], app["interval_hours"], app["generation"], app["pool"]) == ("jellyfin", 6, 1, "usb")
        assert app["data_bytes"] == 300 * 1024**2 + len("<config/>") and app["moved"] is None

