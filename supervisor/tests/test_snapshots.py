import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import pytest
from fastapi import HTTPException

from src.snapshots.schemas import SnapshotInfo, SnapshotCreate, SnapshotRestore
from src.snapshots.router import (
    run_cmd,
    list_subvolumes,
    list_snapshots,
    create_snapshot,
    delete_snapshot,
    restore_snapshot,
)

BTRFS_SHOW = """snap
\tName: \t\t\tsnap
\tUUID: \t\t\t12345-67890
\tCreation time: \t\t2025-01-24 12:00:00 +0000
\tFlags: \t\t\treadonly
"""


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
    """Fake btrfs/du; snapshot creation is emulated with mkdir. Records every command."""
    calls = []
    state = {"fail": False}

    def fake_run(cmd, *args, **kwargs):
        calls.append(cmd)
        result = MagicMock(stdout="", stderr="", returncode=0)
        if state["fail"] and cmd[0] == "btrfs":
            result.returncode = 1
            result.stderr = "btrfs error"
        elif cmd[:3] == ["btrfs", "subvolume", "snapshot"]:
            Path(cmd[-1]).mkdir()
        elif cmd[:3] == ["btrfs", "subvolume", "show"]:
            result.stdout = BTRFS_SHOW
        elif cmd[0] == "du":
            result.stdout = f"1.5G\t{cmd[-1]}"
        return result

    with patch("src.snapshots.router.subprocess.run", side_effect=fake_run):
        yield SimpleNamespace(calls=calls, state=state)


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
            created="2025-01-24T12:00:00",
            size="1.5G"
        )

        assert snap.id == "snap_20250124_120000"
        assert snap.name == "snap_20250124_120000"
        assert snap.subvolume == "/mnt/pools/datapool"
        assert snap.created == "2025-01-24T12:00:00"
        assert snap.size == "1.5G"

    def test_snapshot_info_optional_size(self):
        """Test SnapshotInfo with optional size."""
        snap = SnapshotInfo(
            id="test",
            name="test",
            subvolume="/mnt/pools/test",
            path="/mnt/pools/test/.snapshots/test",
            created="2025-01-24",
            size=None
        )

        assert snap.size is None

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
    """Tests for listing subvolumes."""

    async def test_list_subvolumes_success(self, pools):
        """Only mount points strictly inside the pools base are returned."""
        findmnt = {
            "filesystems": [
                {"target": str(pools.pool), "source": "/dev/sda"},
                {"target": str(pools.media), "source": "/dev/sdb"},
                {"target": str(pools.root / "pools-old" / "x"), "source": "/dev/sdc"},
                {"target": str(pools.base), "source": "/dev/sdd"},
                {"target": "/other/path", "source": "/dev/sde"},
            ]
        }
        mock_result = MagicMock(stdout=json.dumps(findmnt), stderr="", returncode=0)

        with patch("src.snapshots.router.subprocess.run", return_value=mock_result):
            result = await list_subvolumes({})

        assert [sv["path"] for sv in result] == [str(pools.pool), str(pools.media)]

    async def test_list_subvolumes_empty(self, pools):
        """Test listing when no subvolumes."""
        mock_result = MagicMock(stdout="", stderr="", returncode=1)

        with patch("src.snapshots.router.subprocess.run", return_value=mock_result):
            result = await list_subvolumes({})

        assert result == []

    async def test_list_subvolumes_invalid_json(self, pools):
        """Test handling of invalid JSON from findmnt."""
        mock_result = MagicMock(stdout="not json", stderr="", returncode=0)

        with patch("src.snapshots.router.subprocess.run", return_value=mock_result):
            result = await list_subvolumes({})

        assert result == []


class TestListSnapshots:
    """Tests for listing snapshots."""

    async def test_list_snapshots_parses_btrfs_show(self, pools, btrfs):
        _make_snapshot(pools.pool, "snap_20250124_120000")

        (snap,) = await list_snapshots(None, {})

        assert snap.name == "snap_20250124_120000"
        assert snap.subvolume == str(pools.pool)
        assert snap.created == "2025-01-24T12:00:00"
        assert snap.size == "1.5G"

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
