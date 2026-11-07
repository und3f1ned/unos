import errno
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import pytest
from fastapi import HTTPException

from src.snapshots.schemas import SnapshotInfo, SnapshotCreate, SnapshotRestore
from src.snapshots.router import (
    run_cmd,
    list_snapshots,
    create_snapshot,
    delete_snapshot,
    restore_snapshot,
)

from .test_pool_persistence import (
    DATA_UUID,
    MEDIA_UUID,
    admin_client,
    install_fake_host,
    wait_for_app_starts,
)
from .test_volumes import FakeSubvolumes

SUBVOLUMES = "/api/v1/snapshots/subvolumes"
# What `btrfs subvolume show` prints when no other time is set (the fixture btrfs).
CREATED = "2025-01-24 12:00:00 +0000"


def btrfs_show(created: str) -> str:
    """`btrfs subvolume show` of a snapshot; btrfs-progs prints its local time with the offset."""
    return (
        "snap\n"
        "\tName: \t\t\tsnap\n"
        "\tUUID: \t\t\t12345-67890\n"
        f"\tCreation time: \t\t{created}\n"
        "\tFlags: \t\t\treadonly\n"
    )


@pytest.fixture
def pools(tmp_path):
    """A pools base with two pools and a directory outside of it."""
    root = tmp_path.resolve()
    base = root / "pools"
    (base / "datapool").mkdir(parents=True)
    (base / "mediapool").mkdir()
    outside = root / "outside"
    outside.mkdir()
    with patch("src.security.validators.settings", SimpleNamespace(pools_base_path=str(base))):
        yield SimpleNamespace(root=root, base=base, pool=base / "datapool", media=base / "mediapool",
                              outside=outside)


@pytest.fixture
def btrfs():
    """Fake btrfs; snapshot creation is emulated with mkdir. Records every command.

    ``state["created"]`` maps snapshot names to the creation time that
    ``btrfs subvolume show`` prints for them (default CREATED). Any other
    program fails the test.
    """
    calls = []
    state = {"fail": False, "created": {}}

    def fake_run(cmd, *args, **kwargs):
        calls.append(cmd)
        assert cmd[0] == "btrfs", f"unexpected command: {cmd}"
        result = MagicMock(stdout="", stderr="", returncode=0)
        if state["fail"]:
            result.returncode = 1
            result.stderr = "btrfs error"
        elif cmd[:3] == ["btrfs", "subvolume", "snapshot"]:
            Path(cmd[-1]).mkdir()
        elif cmd[:3] == ["btrfs", "subvolume", "show"]:
            result.stdout = btrfs_show(state["created"].get(Path(cmd[-1]).name, CREATED))
        return result

    with patch("src.snapshots.router.subprocess.run", side_effect=fake_run):
        yield SimpleNamespace(calls=calls, state=state)


@pytest.fixture
def pool_host(monkeypatch):
    """The FakeHost: pool "data" mounted at /mnt/pools/data, pool "media" not mounted.

    GET /snapshots/subvolumes reads the pools and their volumes on the host
    (storage/pools.py through src/host.py), as the Storage page does.
    """
    fake = install_fake_host(monkeypatch)
    fake.add_disk("sda", serial="WD-1")
    fake.add_btrfs("data", DATA_UUID, ["/dev/sda"])
    fake.mount_pool("data")
    fake.add_disk("sdb", serial="WD-2")
    fake.add_btrfs("media", MEDIA_UUID, ["/dev/sdb"])
    fake.dirs.add("/mnt/pools/media")
    fake.subvols = FakeSubvolumes(fake)
    yield fake
    wait_for_app_starts()


def _offered(pool: str, volume: str | None = None) -> dict:
    """An entry of GET /snapshots/subvolumes."""
    path = f"/mnt/pools/{pool}" + (f"/{volume}" if volume else "")
    return {"path": path, "pool": pool, "volume": volume}


def _make_snapshot(subvolume: Path, name: str) -> Path:
    snap = subvolume / ".snapshots" / name
    snap.mkdir(parents=True)
    return snap


# Names create_snapshot refuses but other tools (snapper, btrfs on the shell) use.
NAMES_FROM_OTHER_TOOLS = ["before upgrade (v2)", ".hidden", "-x", "x\n", "2025-01-24 12:00 ü"]


class TestSnapshotSchemas:
    """Tests for snapshot schemas validation."""

    def test_snapshot_info_schema(self):
        """Test SnapshotInfo schema."""
        snap = SnapshotInfo(
            id="snap_20250124_120000",
            name="snap_20250124_120000",
            subvolume="/mnt/pools/datapool",
            path="/mnt/pools/datapool/.snapshots/snap_20250124_120000",
            created="2025-01-24T12:00:00+00:00",
        )

        assert snap.id == "snap_20250124_120000"
        assert snap.name == "snap_20250124_120000"
        assert snap.subvolume == "/mnt/pools/datapool"
        assert snap.created == "2025-01-24T12:00:00+00:00"

    def test_snapshot_info_has_no_size(self):
        """The list does not measure snapshots: du walked every file of each one (ADR-0232)."""
        assert set(SnapshotInfo.model_fields) == {"id", "name", "subvolume", "path", "created"}

    def test_snapshot_create_schema(self):
        """Test SnapshotCreate schema."""
        create = SnapshotCreate(subvolume="/mnt/pools/datapool", name="mybackup")

        assert create.subvolume == "/mnt/pools/datapool"
        assert create.name == "mybackup"

    def test_snapshot_create_auto_name(self):
        """Test SnapshotCreate with auto-generated name."""
        create = SnapshotCreate(subvolume="/mnt/pools/datapool")

        assert create.subvolume == "/mnt/pools/datapool"
        assert create.name is None

    def test_snapshot_restore_schema(self):
        """Test SnapshotRestore schema."""
        restore = SnapshotRestore(
            snapshot_path="/mnt/pools/datapool/.snapshots/snap_20250124_120000",
            target_path="/mnt/pools/datapool/restored"
        )

        assert restore.snapshot_path == "/mnt/pools/datapool/.snapshots/snap_20250124_120000"
        assert restore.target_path == "/mnt/pools/datapool/restored"

    def test_snapshot_restore_default_target(self):
        """Test SnapshotRestore with default target."""
        restore = SnapshotRestore(snapshot_path="/mnt/pools/datapool/.snapshots/snap_20250124_120000")

        assert restore.target_path is None


class TestRunCmd:
    """Tests for the run_cmd helper function."""

    def test_run_cmd_success(self):
        """Test run_cmd returns correct output."""
        mock_result = MagicMock(stdout="output", stderr="", returncode=0)

        with patch("src.snapshots.router.subprocess.run", return_value=mock_result):
            stdout, stderr, rc = run_cmd(["echo", "test"])

        assert stdout == "output"
        assert stderr == ""
        assert rc == 0

    def test_run_cmd_failure(self):
        """Test run_cmd handles failure."""
        mock_result = MagicMock(stdout="", stderr="error", returncode=1)

        with patch("src.snapshots.router.subprocess.run", return_value=mock_result):
            stdout, stderr, rc = run_cmd(["false"])

        assert stdout == ""
        assert stderr == "error"
        assert rc == 1


class TestListSubvolumes:
    """GET /snapshots/subvolumes: the pools and volumes that the Snapshots page offers.

    Every pool mounted at its mountpoint, followed by its volumes, as GET
    /storage/pools and GET /storage/pools/{pool}/volumes show them (ADR-0232).
    """

    def test_mounted_pools_and_their_volumes(self, pool_host):
        documents = pool_host.subvols.add(DATA_UUID, "documents")
        pool_host.subvols.add(DATA_UUID, "photos")
        # Not volumes (ADR-0069): nested deeper, a snapshot, a name UNOS does not give volumes.
        pool_host.subvols.add(DATA_UUID, "documents/nested", parent=documents)
        pool_host.subvols.add(DATA_UUID, ".snapshots/auto_20250101_000000")
        pool_host.subvols.add(DATA_UUID, "Old Photos")

        with admin_client() as api:
            response = api.get(SUBVOLUMES)

        assert response.status_code == 200, response.text
        # "media" is not mounted: its mountpoint is a folder on the boot disk.
        assert response.json() == [_offered("data"), _offered("data", "documents"), _offered("data", "photos")]
        assert pool_host.changes == []

    def test_every_mounted_pool_comes_with_its_own_volumes(self, pool_host):
        pool_host.mount_pool("media")
        pool_host.subvols.add(MEDIA_UUID, "music")
        pool_host.subvols.add(DATA_UUID, "documents")

        with admin_client() as api:
            response = api.get(SUBVOLUMES)

        assert response.status_code == 200, response.text
        assert response.json() == [
            _offered("data"), _offered("data", "documents"), _offered("media"), _offered("media", "music"),
        ]

    # Path-wise: "/mnt/pools-old/media" is not inside "/mnt/pools".
    @pytest.mark.parametrize("target", ["/srv/media", "/mnt/pools-old/media", "/mnt/pools", "/mnt/pools/data/media"])
    def test_a_pool_mounted_elsewhere_only_is_not_offered(self, pool_host, target):
        pool_host.mount_pool("media", target)
        pool_host.subvols.add(MEDIA_UUID, "music")

        with admin_client() as api:
            response = api.get(SUBVOLUMES)

        assert response.status_code == 200, response.text
        assert response.json() == [_offered("data")]

    def test_a_pool_mounted_at_another_subvolume_is_offered_without_volumes(self, pool_host):
        """Its volumes are not reachable below its mountpoint (ADR-0069): the mounted subvolume is offered."""
        nested = pool_host.subvols.add(DATA_UUID, "documents")
        pool_host.subvols.add(DATA_UUID, "photos")
        pool_host.subvols.mounted_subvolume["/mnt/pools/data"] = nested

        with admin_client() as api:
            response = api.get(SUBVOLUMES)

        assert response.status_code == 200, response.text
        assert response.json() == [_offered("data")]

    def test_a_read_only_pool_is_offered_with_its_volumes(self, pool_host):
        """Its snapshots are listed; btrfs refuses new ones, and the page shows why."""
        pool_host.mounts[:] = [m for m in pool_host.mounts if m["target"] != "/mnt/pools/data"]
        pool_host.mount_pool("data", vfs_options="ro,noatime", fs_options="ro")
        pool_host.subvols.add(DATA_UUID, "documents")

        with admin_client() as api:
            response = api.get(SUBVOLUMES)

        assert response.json() == [_offered("data"), _offered("data", "documents")]

    def test_no_mounted_pool(self, pool_host):
        pool_host.mounts[:] = [m for m in pool_host.mounts if m["target"] != "/mnt/pools/data"]

        with admin_client() as api:
            response = api.get(SUBVOLUMES)

        assert response.status_code == 200, response.text
        assert response.json() == []

    @pytest.mark.parametrize(
        "command,stderr",
        [
            # list_pools alone would take an unreadable mount table for no mounts.
            (("findmnt",), "findmnt: cannot open /proc/self/mountinfo: Permission denied"),
            (("btrfs", "filesystem", "show"), "ERROR: cannot open /dev/btrfs-control: Permission denied"),
            (("btrfs", "subvolume", "list"), "ERROR: can't perform the search: Permission denied"),
        ],
    )
    def test_what_cannot_be_read_on_the_host_is_reported(self, pool_host, command, stderr):
        """Not an empty list: the page would say that no pool is mounted."""
        pool_host.fail[command] = (1, stderr)

        with admin_client() as api:
            response = api.get(SUBVOLUMES)

        assert response.status_code == 500
        assert stderr in response.json()["detail"]
        assert pool_host.changes == []

    def test_commands_run_on_the_host_only(self, pool_host):
        """In the host's mount namespace, where the Storage page reads the pools (ADR-0020)."""
        with admin_client() as api:
            assert api.get(SUBVOLUMES).status_code == 200
        # install_fake_host blocks subprocess.run: a command in the container fails the test.
        assert ["btrfs", "subvolume", "list", "/mnt/pools/data"] in pool_host.calls


class TestListSnapshots:
    """Tests for listing snapshots."""

    async def test_list_snapshots_parses_btrfs_show(self, pools, btrfs):
        snap_path = _make_snapshot(pools.pool, "snap_20250124_120000")

        (snap,) = await list_snapshots(None, {})

        assert snap.model_dump() == {
            "id": "snap_20250124_120000",
            "name": "snap_20250124_120000",
            "subvolume": str(pools.pool),
            "path": str(snap_path),
            # With its offset, so that the browser shows it in its own time zone (ADR-0232).
            "created": "2025-01-24T12:00:00+00:00",
        }
        # Only btrfs: du walked every file of every snapshot at each listing (ADR-0232).
        assert btrfs.calls == [["btrfs", "subvolume", "show", str(snap_path)]]

    async def test_creation_times_are_given_in_utc_newest_first(self, pools, btrfs):
        """btrfs prints its local time; a change of the offset (summer time) must not reorder the list."""
        btrfs.state["created"] = {
            "a": "2025-03-30 03:30:00 +0200",  # 01:30 UTC
            "b": "2025-03-30 01:45:00 +0000",  # 01:45 UTC
            "c": "2025-03-30 01:15:00 +0100",  # 00:15 UTC
        }
        for name in btrfs.state["created"]:
            _make_snapshot(pools.pool, name)

        snapshots = await list_snapshots(str(pools.pool), {})

        assert [(s.name, s.created) for s in snapshots] == [
            ("b", "2025-03-30T01:45:00+00:00"),
            ("a", "2025-03-30T01:30:00+00:00"),
            ("c", "2025-03-30T00:15:00+00:00"),
        ]

    async def test_a_creation_time_that_cannot_be_read_is_shown_as_btrfs_prints_it(self, pools, btrfs):
        btrfs.state["created"] = {"odd": "-"}
        _make_snapshot(pools.pool, "odd")

        (snap,) = await list_snapshots(str(pools.pool), {})

        assert snap.created == "-"

    async def test_list_snapshots_skips_symlinks(self, pools, btrfs):
        _make_snapshot(pools.pool, "real")
        os.symlink(pools.outside, pools.pool / ".snapshots" / "evil")
        (pools.pool / ".snapshots" / "file").write_text("x")
        # A planted .snapshots symlink in another pool is ignored entirely.
        os.symlink(pools.outside, pools.media / ".snapshots")

        snapshots = await list_snapshots(None, {})

        assert [s.name for s in snapshots] == ["real"]
        assert all(str(pools.outside) not in " ".join(cmd) for cmd in btrfs.calls)

    async def test_list_snapshots_of_one_subvolume(self, pools, btrfs):
        _make_snapshot(pools.pool, "a")
        _make_snapshot(pools.media, "b")
        volume = pools.pool / "volume"
        volume.mkdir()
        _make_snapshot(volume, "c")

        assert [s.name for s in await list_snapshots(str(pools.media), {})] == ["b"]
        assert [s.name for s in await list_snapshots(str(volume), {})] == ["c"]

    @pytest.mark.parametrize("subvolume", ["/etc", "{root}/outside", "{base}/datapool/../../outside", "{base}"])
    async def test_list_snapshots_rejects_outside_subvolume(self, pools, btrfs, subvolume):
        with pytest.raises(HTTPException) as exc:
            await list_snapshots(subvolume.format(root=pools.root, base=pools.base), {})
        assert exc.value.status_code == 400
        assert btrfs.calls == []

    async def test_list_snapshots_empty_directory(self, pools, btrfs):
        """Test listing when no snapshots directory."""
        assert await list_snapshots(None, {}) == []


class TestCreateSnapshot:
    """Tests for creating snapshots."""

    async def test_create_snapshot_success(self, pools, btrfs):
        """Test creating a snapshot successfully."""
        result = await create_snapshot(SnapshotCreate(subvolume=str(pools.pool), name="test_snapshot"), {})

        assert result.name == "test_snapshot"
        assert result.subvolume == str(pools.pool)
        expected = pools.pool / ".snapshots" / "test_snapshot"
        assert result.path == str(expected)
        assert btrfs.calls == [["btrfs", "subvolume", "snapshot", "-r", str(pools.pool), str(expected)]]

    async def test_create_snapshot_auto_name(self, pools, btrfs):
        """Test creating a snapshot with auto-generated name."""
        result = await create_snapshot(SnapshotCreate(subvolume=str(pools.pool)), {})

        assert result.name.startswith("snap_")

    async def test_create_snapshot_of_a_volume(self, pools, btrfs):
        """A volume (a subvolume in the pool's top folder) keeps its snapshots in its own .snapshots."""
        volume = pools.pool / "documents"
        volume.mkdir()

        result = await create_snapshot(SnapshotCreate(subvolume=str(volume), name="before"), {})

        expected = volume / ".snapshots" / "before"
        assert (result.subvolume, result.path) == (str(volume), str(expected))
        assert btrfs.calls == [["btrfs", "subvolume", "snapshot", "-r", str(volume), str(expected)]]
        assert not (pools.pool / ".snapshots").exists()
        (listed,) = await list_snapshots(str(volume), {})
        assert (listed.name, listed.created) == ("before", "2025-01-24T12:00:00+00:00")

    async def test_create_snapshot_answers_its_time_with_the_offset(self, pools, btrfs):
        before = datetime.now(timezone.utc).replace(microsecond=0)

        result = await create_snapshot(SnapshotCreate(subvolume=str(pools.pool), name="x"), {})

        created = datetime.fromisoformat(result.created)
        assert created.utcoffset() == timedelta(0)
        assert before <= created <= datetime.now(timezone.utc)

    async def test_snapshot_folder_that_cannot_be_made_is_reported(self, pools, btrfs, monkeypatch):
        """A pool that btrfs made read-only after an error answered 500 without a reason."""

        def read_only(path, mode=0o777):
            raise OSError(errno.EROFS, "Read-only file system", str(path))

        monkeypatch.setattr(os, "mkdir", read_only)

        with pytest.raises(HTTPException) as exc_info:
            await create_snapshot(SnapshotCreate(subvolume=str(pools.pool), name="x"), {})

        assert exc_info.value.status_code == 500
        assert exc_info.value.detail == (
            f"Failed to create snapshot: cannot create {pools.pool / '.snapshots'}: Read-only file system"
        )
        assert btrfs.calls == []

    async def test_create_snapshot_not_found(self, pools, btrfs):
        """Test creating snapshot for non-existent subvolume."""
        with pytest.raises(HTTPException) as exc_info:
            await create_snapshot(SnapshotCreate(subvolume=str(pools.base / "nonexistent")), {})

        assert exc_info.value.status_code == 404

    @pytest.mark.parametrize(
        "subvolume", ["/etc", "/", "{root}/outside", "{root}/pools-old", "{base}", "{base}/datapool/../../outside",
                      "relative", "{base}/datapool\n"],
    )
    async def test_create_snapshot_rejects_bad_subvolume(self, pools, btrfs, subvolume):
        with pytest.raises(HTTPException) as exc_info:
            await create_snapshot(SnapshotCreate(subvolume=subvolume.format(root=pools.root, base=pools.base)), {})
        assert exc_info.value.status_code == 400
        assert btrfs.calls == []

    async def test_create_snapshot_rejects_symlink_escaping_pools(self, pools, btrfs):
        os.symlink(pools.outside, pools.pool / "escape")
        with pytest.raises(HTTPException) as exc_info:
            await create_snapshot(SnapshotCreate(subvolume=str(pools.pool / "escape")), {})
        assert exc_info.value.status_code == 400
        assert btrfs.calls == []

    async def test_create_snapshot_rejects_symlink_to_other_pool(self, pools, btrfs):
        """A link planted inside one pool must not redirect the snapshot to another pool."""
        os.symlink(pools.media, pools.pool / "apps")
        with pytest.raises(HTTPException) as exc_info:
            await create_snapshot(SnapshotCreate(subvolume=str(pools.pool / "apps"), name="x"), {})
        assert exc_info.value.status_code == 400
        assert btrfs.calls == []
        assert not (pools.media / ".snapshots").exists()

    @pytest.mark.parametrize("name", ["../../outside/x", "a/b", "..", ".", "x\n", ".hidden", "x" * 129])
    async def test_create_snapshot_rejects_bad_names(self, pools, btrfs, name):
        with pytest.raises(HTTPException) as exc_info:
            await create_snapshot(SnapshotCreate(subvolume=str(pools.pool), name=name), {})
        assert exc_info.value.status_code == 400
        assert btrfs.calls == []
        assert list(pools.outside.iterdir()) == []

    async def test_create_snapshot_refuses_symlinked_snapshot_dir(self, pools, btrfs):
        os.symlink(pools.outside, pools.pool / ".snapshots")
        with pytest.raises(HTTPException) as exc_info:
            await create_snapshot(SnapshotCreate(subvolume=str(pools.pool), name="x"), {})
        assert exc_info.value.status_code == 400
        assert btrfs.calls == []

    async def test_create_snapshot_already_exists(self, pools, btrfs):
        """Test creating snapshot that already exists."""
        _make_snapshot(pools.pool, "existing_snapshot")

        with pytest.raises(HTTPException) as exc_info:
            await create_snapshot(SnapshotCreate(subvolume=str(pools.pool), name="existing_snapshot"), {})

        assert exc_info.value.status_code == 400

    async def test_create_snapshot_btrfs_failure(self, pools, btrfs):
        """Test handling btrfs command failure."""
        btrfs.state["fail"] = True

        with pytest.raises(HTTPException) as exc_info:
            await create_snapshot(SnapshotCreate(subvolume=str(pools.pool), name="test_snapshot"), {})

        assert exc_info.value.status_code == 500


class TestDeleteSnapshot:
    """Tests for deleting snapshots."""

    async def test_delete_snapshot_success(self, pools, btrfs):
        """Test deleting a snapshot successfully."""
        snap = _make_snapshot(pools.pool, "snap_test")

        result = await delete_snapshot("snap_test", str(pools.pool), {})

        assert result["status"] == "deleted"
        assert result["id"] == "snap_test"
        assert btrfs.calls == [["btrfs", "subvolume", "delete", str(snap)]]

    async def test_delete_a_snapshot_of_a_volume(self, pools, btrfs):
        volume = pools.pool / "documents"
        volume.mkdir()
        snap = _make_snapshot(volume, "before")
        _make_snapshot(pools.pool, "before")  # the pool's snapshot of the same name stays

        result = await delete_snapshot("before", str(volume), {})

        assert result == {"status": "deleted", "id": "before"}
        assert btrfs.calls == [["btrfs", "subvolume", "delete", str(snap)]]

    async def test_delete_snapshot_not_found(self, pools, btrfs):
        """Test deleting non-existent snapshot."""
        with pytest.raises(HTTPException) as exc_info:
            await delete_snapshot("nonexistent", str(pools.pool), {})

        assert exc_info.value.status_code == 404

    @pytest.mark.parametrize("snapshot_id", ["../../../outside", "../x", "a/b", ".", "..", "", "a\x00b", "x" * 256])
    async def test_delete_snapshot_rejects_bad_ids(self, pools, btrfs, snapshot_id):
        _make_snapshot(pools.pool, "keep")
        _make_snapshot(pools.outside, "x")
        with pytest.raises(HTTPException) as exc_info:
            await delete_snapshot(snapshot_id, str(pools.pool), {})
        assert exc_info.value.status_code == 400
        assert btrfs.calls == []

    @pytest.mark.parametrize("name", NAMES_FROM_OTHER_TOOLS)
    async def test_delete_snapshot_accepts_any_listed_name(self, pools, btrfs, name):
        """Snapshots made by other tools may have names create_snapshot refuses."""
        snap = _make_snapshot(pools.pool, name)
        (listed,) = await list_snapshots(None, {})
        assert listed.name == name
        btrfs.calls.clear()

        result = await delete_snapshot(listed.id, listed.subvolume, {})

        assert result == {"status": "deleted", "id": name}
        assert btrfs.calls == [["btrfs", "subvolume", "delete", str(snap)]]

    async def test_delete_snapshot_missing_odd_name_is_not_found(self, pools, btrfs):
        _make_snapshot(pools.pool, "keep")
        with pytest.raises(HTTPException) as exc_info:
            await delete_snapshot("x\n", str(pools.pool), {})
        assert exc_info.value.status_code == 404
        assert btrfs.calls == []

    async def test_delete_snapshot_file_entry_is_refused(self, pools, btrfs):
        (pools.pool / ".snapshots").mkdir()
        (pools.pool / ".snapshots" / "notes").write_text("x")
        with pytest.raises(HTTPException) as exc_info:
            await delete_snapshot("notes", str(pools.pool), {})
        assert exc_info.value.status_code == 400
        assert btrfs.calls == []

    @pytest.mark.parametrize("subvolume", ["/", "{root}/outside", "{base}/datapool/../../outside", "{base}"])
    async def test_delete_snapshot_rejects_bad_subvolume(self, pools, btrfs, subvolume):
        _make_snapshot(pools.outside, "victim")
        with pytest.raises(HTTPException) as exc_info:
            await delete_snapshot("victim", subvolume.format(root=pools.root, base=pools.base), {})
        assert exc_info.value.status_code == 400
        assert btrfs.calls == []

    async def test_delete_snapshot_entry_symlink_is_not_followed(self, pools, btrfs):
        """A planted symlink must not turn a snapshot delete into a delete elsewhere."""
        victim = pools.media / "important"
        victim.mkdir()
        (pools.pool / ".snapshots").mkdir()
        os.symlink(victim, pools.pool / ".snapshots" / "fake")

        with pytest.raises(HTTPException) as exc_info:
            await delete_snapshot("fake", str(pools.pool), {})

        assert exc_info.value.status_code == 400
        assert "symbolic link" in exc_info.value.detail
        assert btrfs.calls == []
        assert victim.is_dir()

    async def test_delete_snapshot_symlinked_snapshot_dir_is_refused(self, pools, btrfs):
        (pools.media / "important").mkdir()
        os.symlink(pools.media, pools.pool / ".snapshots")

        with pytest.raises(HTTPException) as exc_info:
            await delete_snapshot("important", str(pools.pool), {})

        assert exc_info.value.status_code == 400
        assert btrfs.calls == []

    async def test_delete_snapshot_btrfs_failure(self, pools, btrfs):
        """Test handling btrfs delete failure."""
        _make_snapshot(pools.pool, "snap_test")
        btrfs.state["fail"] = True

        with pytest.raises(HTTPException) as exc_info:
            await delete_snapshot("snap_test", str(pools.pool), {})

        assert exc_info.value.status_code == 500


class TestRestoreSnapshot:
    """Tests for restoring snapshots."""

    async def test_restore_snapshot_success(self, pools, btrfs):
        """Test restoring a snapshot successfully."""
        snap = _make_snapshot(pools.pool, "snap_test")

        result = await restore_snapshot(SnapshotRestore(snapshot_path=str(snap)), {})

        assert result["status"] == "restored"
        assert result["source"] == str(snap)
        target = Path(result["target"])
        assert target.parent == pools.pool
        assert target.name.startswith("restore_")
        assert btrfs.calls == [["btrfs", "subvolume", "snapshot", str(snap), str(target)]]

    async def test_restore_a_snapshot_of_a_volume_next_to_its_files(self, pools, btrfs):
        volume = pools.pool / "documents"
        volume.mkdir()
        snap = _make_snapshot(volume, "before")

        result = await restore_snapshot(SnapshotRestore(snapshot_path=str(snap)), {})

        target = Path(result["target"])
        assert (result["status"], target.parent) == ("restored", volume)
        assert target.name.startswith("restore_")
        # Writable: no -r.
        assert btrfs.calls == [["btrfs", "subvolume", "snapshot", str(snap), str(target)]]

    async def test_restore_snapshot_with_target(self, pools, btrfs):
        """Test restoring to specific target."""
        snap = _make_snapshot(pools.pool, "snap_test")
        custom = pools.pool / "custom"
        custom.mkdir()

        result = await restore_snapshot(SnapshotRestore(snapshot_path=str(snap), target_path=str(custom)), {})

        assert result["status"] == "restored"
        assert Path(result["target"]).parent == custom

    async def test_restore_snapshot_not_found(self, pools, btrfs):
        """Test restoring non-existent snapshot."""
        (pools.pool / ".snapshots").mkdir()
        with pytest.raises(HTTPException) as exc_info:
            await restore_snapshot(
                SnapshotRestore(snapshot_path=str(pools.pool / ".snapshots" / "nonexistent")), {}
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.parametrize(
        "template",
        [
            "{root}/outside/.snapshots/x",
            "{base}/datapool/.snapshots/../../../outside",
            "{base}/datapool/x",  # not inside .snapshots
            "{base}/datapool/.snapshots/x/y",
            "{base}/datapool/.snapshots/..",
            "relative/.snapshots/x",
            "/.snapshots/x",
        ],
    )
    async def test_restore_snapshot_rejects_bad_paths(self, pools, btrfs, template):
        _make_snapshot(pools.outside, "x")
        with pytest.raises(HTTPException) as exc_info:
            await restore_snapshot(SnapshotRestore(snapshot_path=template.format(root=pools.root, base=pools.base)), {})
        assert exc_info.value.status_code == 400
        assert btrfs.calls == []

    @pytest.mark.parametrize("name", NAMES_FROM_OTHER_TOOLS)
    async def test_restore_snapshot_accepts_any_listed_name(self, pools, btrfs, name):
        """Snapshots made by other tools may have names create_snapshot refuses."""
        snap = _make_snapshot(pools.pool, name)
        (listed,) = await list_snapshots(None, {})
        assert listed.path == str(snap)
        btrfs.calls.clear()

        result = await restore_snapshot(SnapshotRestore(snapshot_path=listed.path), {})

        assert result["source"] == str(snap)
        target = Path(result["target"])
        assert target.parent == pools.pool
        assert btrfs.calls == [["btrfs", "subvolume", "snapshot", str(snap), str(target)]]

    @pytest.mark.parametrize(
        "name", ["../x", "a/b", ".", "..", "", "a\x00b", "x" * 256], ids=repr
    )
    async def test_restore_snapshot_rejects_bad_names(self, pools, btrfs, name):
        _make_snapshot(pools.pool, "x")
        _make_snapshot(pools.pool, "a")
        with pytest.raises(HTTPException) as exc_info:
            await restore_snapshot(SnapshotRestore(snapshot_path=f"{pools.pool}/.snapshots/{name}"), {})
        assert exc_info.value.status_code == 400
        assert btrfs.calls == []

    @pytest.mark.parametrize("link", ["entry", "snapshot-dir"])
    async def test_restore_snapshot_refuses_symlinks(self, pools, btrfs, link):
        """A planted symlink must not turn a restore into a copy of something else."""
        victim = pools.media / "important"
        victim.mkdir()
        if link == "entry":
            (pools.pool / ".snapshots").mkdir()
            os.symlink(victim, pools.pool / ".snapshots" / "important")
        else:
            os.symlink(pools.media, pools.pool / ".snapshots")

        with pytest.raises(HTTPException) as exc_info:
            await restore_snapshot(
                SnapshotRestore(snapshot_path=str(pools.pool / ".snapshots" / "important")), {}
            )

        assert exc_info.value.status_code == 400
        assert "symbolic link" in exc_info.value.detail
        assert btrfs.calls == []

    @pytest.mark.parametrize("target", ["/etc", "{root}/outside", "{base}", "{base}/datapool/../../outside"])
    async def test_restore_snapshot_rejects_bad_target(self, pools, btrfs, target):
        snap = _make_snapshot(pools.pool, "snap_test")
        with pytest.raises(HTTPException) as exc_info:
            await restore_snapshot(
                SnapshotRestore(snapshot_path=str(snap), target_path=target.format(root=pools.root, base=pools.base)),
                {},
            )
        assert exc_info.value.status_code == 400
        assert btrfs.calls == []
        assert list(pools.outside.iterdir()) == []

    async def test_restore_snapshot_rejects_target_through_symlink(self, pools, btrfs):
        snap = _make_snapshot(pools.pool, "snap_test")
        os.symlink(pools.media, pools.pool / "apps")
        with pytest.raises(HTTPException) as exc_info:
            await restore_snapshot(SnapshotRestore(snapshot_path=str(snap), target_path=str(pools.pool / "apps")), {})
        assert exc_info.value.status_code == 400
        assert btrfs.calls == []
        assert list(pools.media.iterdir()) == []

    async def test_restore_snapshot_btrfs_failure(self, pools, btrfs):
        """Test handling btrfs restore failure."""
        snap = _make_snapshot(pools.pool, "snap_test")
        btrfs.state["fail"] = True

        with pytest.raises(HTTPException) as exc_info:
            await restore_snapshot(SnapshotRestore(snapshot_path=str(snap)), {})

        assert exc_info.value.status_code == 500
