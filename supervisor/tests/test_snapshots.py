import contextlib
import errno
import fcntl
import importlib
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timedelta, UTC
from pathlib import Path
from types import SimpleNamespace
from collections.abc import Callable
from unittest.mock import patch, MagicMock

import pytest
from fastapi import HTTPException, Request

from src import host as host_module
from src.snapshots.schemas import SnapshotInfo, SnapshotCreate, SnapshotRestore
from src.snapshots.router import (
    AUTO_SNAPSHOT_PREFIX,
    RESTORE_PREFIX,
    SNAPSHOT_DIR_NAME,
    SNAPSHOT_PREFIX,
    run_cmd,
    list_snapshots,
    create_snapshot,
    delete_snapshot,
    restore_snapshot,
)
from src.storage import pools as storage_pools

from .test_pool_persistence import (
    DATA_UUID,
    MEDIA_UUID,
    admin_client,
    install_fake_host,
    wait_for_app_starts,
)
from .test_volumes import FakeSubvolumes

REPO = Path(__file__).resolve().parents[2]
SUBVOLUMES = "/api/v1/snapshots/subvolumes"
# What `btrfs subvolume show` prints when no other time is set (the fixture btrfs).
CREATED = "2025-01-24 12:00:00 +0000"
# The id of the top level of a btrfs filesystem.
TOP_LEVEL = 5
# The request and the administrator of the endpoints that the tests call directly: each change
# writes its audit entry with them (ADR-0350; test_audit_storage_and_apps.py).
REQUEST = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "client": ("192.0.2.1", 50000)})
ADMIN = SimpleNamespace(username="admin")


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


def _path_of_fd(fd: int) -> Path:
    """Where the folder open at ``fd`` is now: the path follows renames."""
    if hasattr(fcntl, "F_GETPATH"):  # macOS
        return Path(os.fsdecode(fcntl.fcntl(fd, fcntl.F_GETPATH, bytes(1024)).split(b"\0", 1)[0]))
    return Path(os.readlink(f"/proc/self/fd/{fd}"))


# A path that leads to an open descriptor of the command, and what follows it.
FD_PATH = re.compile(r"/proc/self/fd/([0-9]+)(?:/(.*))?", re.DOTALL)


class FakeBtrfs:
    """btrfs-progs on folders of the test, the commands of the snapshot router and the scheduler.

    Each of ``filesystems`` (the pool folders) is a btrfs filesystem whose
    top level (subvolume 5) is mounted there. Its other subvolumes are the
    folders made by ``subvolume`` and ``snapshot`` and by ``btrfs subvolume
    snapshot``; every other folder is a plain folder in the subvolume
    around it.
    Subvolumes are known by their inode, so they keep their id when they
    are renamed. Paths are resolved as btrfs-progs resolves them:
    ``/proc/self/fd/<fd>`` is the open folder itself (the command must get
    the descriptor in pass_fds), every other path is resolved again when the
    command runs, symbolic links included.

    ``calls``: the commands, with each /proc/self/fd/<fd> written as the
    path of its folder at that moment. ``deleted``: the subvolumes deleted,
    where they were then. ``before``: argv prefix -> function, called once
    right before the first command that starts with it, to change the
    folders between the router's checks and btrfs. ``state["fail"]``: every
    command fails; ``state["created"]``: snapshot name -> the creation time
    that ``btrfs subvolume show`` prints (CREATED by default). Any other
    program fails the test.
    """

    def __init__(self, *filesystems: Path) -> None:
        self.filesystems = filesystems
        self.calls: list[list[str]] = []
        self.deleted: list[Path] = []
        self.before: dict[tuple[str, ...], Callable[[], None]] = {}
        self.state = {"fail": False, "created": {}}
        self._ids: dict[tuple[int, int], int] = {}
        self._next_id = 256

    # -- building -----------------------------------------------------------

    def subvolume(self, path: Path) -> Path:
        """A new subvolume at ``path``, such as a volume; returns ``path``."""
        path.mkdir(parents=True)
        self._register(path)
        return path

    def snapshot(self, subvolume: Path, name: str) -> Path:
        """A new snapshot ``name`` in ``<subvolume>/.snapshots``; returns its path."""
        return self.subvolume(subvolume / ".snapshots" / name)

    def id_of(self, path: Path) -> int:
        return self._ids[self._key(path)]

    def _register(self, path: Path) -> None:
        self._ids[self._key(path)] = self._next_id
        self._next_id += 1

    @staticmethod
    def _key(path: Path) -> tuple[int, int]:
        st = os.stat(path, follow_symlinks=False)
        return st.st_dev, st.st_ino

    # -- resolving ----------------------------------------------------------

    def _resolve(self, arg: str, passed: set[int]) -> Path:
        """What btrfs-progs reaches through ``arg``."""
        match = FD_PATH.fullmatch(arg)
        if match is None:
            return Path(os.path.realpath(arg))
        fd = int(match[1])
        assert fd in passed, f"{arg}: the command did not get descriptor {fd} (pass_fds)"
        # The kernel follows /proc/self/fd/<fd> to the folder itself; the rest is resolved from there.
        folder = _path_of_fd(fd)
        return Path(os.path.realpath(folder / match[2])) if match[2] else folder

    def _shown(self, arg: str, passed: set[int]) -> str:
        match = FD_PATH.fullmatch(arg)
        if match is None:
            return arg
        fd = int(match[1])
        assert fd in passed, f"{arg}: the command did not get descriptor {fd} (pass_fds)"
        return str(_path_of_fd(fd)) + (f"/{match[2]}" if match[2] is not None else "")

    def _filesystem(self, path: Path) -> Path | None:
        return next((fs for fs in self.filesystems if path == fs or fs in path.parents), None)

    def _subvolume_of(self, path: Path) -> int | None:
        """``btrfs inspect-internal rootid``: the id of the subvolume that the folder ``path`` belongs to."""
        fs = self._filesystem(path)
        if fs is None:
            return None
        for folder in (path, *path.parents):
            if folder == fs:
                return TOP_LEVEL
            subvol_id = self._ids.get(self._key(folder))
            if subvol_id is not None:
                return subvol_id
        raise AssertionError(f"{path} is not below {fs}")

    def _find(self, fs: Path, subvol_id: int) -> Path | None:
        """Where subvolume ``subvol_id`` of the filesystem ``fs`` is now."""
        if subvol_id == TOP_LEVEL:
            return fs
        for folder, _, _ in os.walk(fs):
            if self._ids.get(self._key(Path(folder))) == subvol_id:
                return Path(folder)
        return None

    # -- commands -----------------------------------------------------------

    @staticmethod
    def _result(returncode: int = 0, stdout: str = "", stderr: str = "") -> SimpleNamespace:
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

    def run(self, cmd, *args, **kwargs):
        cmd = list(cmd)
        assert cmd[0] == "btrfs", f"unexpected command: {cmd}"
        for prefix, change in list(self.before.items()):
            if tuple(cmd[: len(prefix)]) == prefix:
                del self.before[prefix]
                change()
        passed = set(kwargs.get("pass_fds", ()))
        self.calls.append([self._shown(arg, passed) for arg in cmd])
        if self.state["fail"]:
            return self._result(1, stderr="btrfs error")
        args = cmd[1:]
        if args[:2] == ["inspect-internal", "rootid"]:
            subvol_id = self._subvolume_of(self._resolve(args[2], passed))
            if subvol_id is None:
                return self._result(1, stderr=f"ERROR: not a btrfs filesystem: {args[2]}")
            return self._result(stdout=f"{subvol_id}\n")
        if args[:2] == ["inspect-internal", "subvolid-resolve"]:
            fs = self._filesystem(self._resolve(args[3], passed))
            found = None if fs is None else self._find(fs, int(args[2]))
            if found is None:
                return self._result(1, stderr=f"ERROR: resolving subvolid {args[2]} error -2")
            return self._result(stdout=("" if found == fs else os.path.relpath(found, fs)) + "\n")
        if args[:2] == ["subvolume", "delete"]:
            return self._delete(args[2:], passed)
        if args[:2] == ["subvolume", "snapshot"]:
            return self._snapshot(args[2:], passed)
        if args[:2] == ["subvolume", "show"]:
            return self._result(stdout=btrfs_show(self.state["created"].get(Path(args[-1]).name, CREATED)))
        raise AssertionError(f"unexpected command: {cmd}")

    def _delete(self, args: list[str], passed: set[int]):
        if args[0] == "--subvolid":
            # Only the filesystem of the path counts.
            fs = self._filesystem(self._resolve(args[2], passed))
            found = None if fs is None else self._find(fs, int(args[1]))
            if found is None or found == fs:
                return self._result(1, stderr=f"ERROR: unable to find subvolume {args[1]}")
        else:
            # By path: btrfs-progs takes realpath() of it.
            found = self._resolve(args[0], passed)
            if not found.is_dir() or self._ids.get(self._key(found)) is None:
                return self._result(1, stderr=f"ERROR: Not a Btrfs subvolume: {args[0]}")
        del self._ids[self._key(found)]
        shutil.rmtree(found)
        self.deleted.append(found)
        return self._result(stdout=f"Delete subvolume '{found}'\n")

    def _snapshot(self, args: list[str], passed: set[int]):
        """``btrfs subvolume snapshot [-r] <source> <dest>``, as btrfs-progs 5 and 6 read it."""
        readonly = args[0] == "-r"
        source_arg, dest_arg = args[1:] if readonly else args
        source = self._resolve(source_arg, passed)
        if not source.is_dir():
            return self._result(1, stderr=f"ERROR: cannot access subvolume {source_arg}: No such file or directory")
        if source not in self.filesystems and self._ids.get(self._key(source)) is None:
            return self._result(1, stderr=f"ERROR: not a subvolume: {source_arg}")
        dest = self._resolve(dest_arg, passed)
        if dest.is_dir():
            # An existing folder (also through a symbolic link): the snapshot goes
            # into it, named like the last component of the source.
            name, folder = os.path.basename(source_arg), dest
            if name in ("", ".", ".."):
                return self._result(1, stderr=f"ERROR: invalid snapshot name '{name}'")
        elif os.path.lexists(dest):
            return self._result(1, stderr=f"ERROR: '{dest_arg}' exists and it is not a directory")
        else:
            name, folder = os.path.basename(dest_arg), self._resolve(os.path.dirname(dest_arg), passed)
        if self._filesystem(source) is None or self._filesystem(source) != self._filesystem(folder):
            return self._result(1, stderr=f"ERROR: cannot snapshot '{source_arg}': Invalid cross-device link")
        new = folder / name
        if os.path.lexists(new):
            return self._result(1, stderr=f"ERROR: cannot snapshot '{source_arg}': File exists")
        new.mkdir()
        # The files at the top of the source, enough to tell which folder was copied.
        for entry in os.scandir(source):
            if entry.is_file(follow_symlinks=False):
                shutil.copy2(entry.path, new / entry.name)
        self._register(new)
        return self._result(stdout=f"Create a {'readonly ' if readonly else ''}snapshot of '{source_arg}'\n")


@pytest.fixture
def btrfs(pools):
    """btrfs-progs faked on the two pools of ``pools`` (FakeBtrfs)."""
    fake = FakeBtrfs(pools.pool, pools.media)
    with patch("src.snapshots.router.subprocess.run", side_effect=fake.run):
        yield fake


@contextlib.contextmanager
def local_time_9_hours_ahead_of_utc():
    """The process's local time is 9 hours ahead of UTC, as in Tokyo (a POSIX TZ: no tzdata needed)."""
    old = os.environ.get("TZ")
    os.environ["TZ"] = "JST-9"
    time.tzset()
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()


@pytest.fixture
def clock_in_utc_plus_9():
    with local_time_9_hours_ahead_of_utc():
        yield


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


def _offered(pool: str, volume: str | None = None, volumes_error: str | None = None) -> dict:
    """An entry of GET /snapshots/subvolumes."""
    path = f"/mnt/pools/{pool}" + (f"/{volume}" if volume else "")
    return {"path": path, "pool": pool, "volume": volume, "volumes_error": volumes_error}


def _plain_folder(path: Path) -> Path:
    """A folder that is no subvolume (FakeBtrfs), e.g. one outside the pools."""
    path.mkdir(parents=True)
    return path


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
        """The list does not measure snapshots: du walked every file of each one (ADR-0243)."""
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
    /storage/pools and GET /storage/pools/{pool}/volumes show them (ADR-0243).
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

    @pytest.mark.parametrize(
        "command,stderr",
        [
            (("btrfs", "subvolume", "list", "/mnt/pools/media"), "ERROR: can't perform the search: Input/output error"),
            (("btrfs", "inspect-internal", "rootid", "/mnt/pools/media"), "ERROR: failed to lookup root id: Input/output error"),
        ],
    )
    def test_a_pool_whose_volumes_cannot_be_read_is_offered_alone_with_the_reason(self, pool_host, command, stderr):
        """One failing pool (a USB disk that dropped off, say) leaves the others and their volumes on the page."""
        pool_host.mount_pool("media")
        pool_host.subvols.add(MEDIA_UUID, "music")
        pool_host.subvols.add(DATA_UUID, "documents")
        pool_host.fail[command] = (1, stderr)

        with admin_client() as api:
            response = api.get(SUBVOLUMES)

        assert response.status_code == 200, response.text
        data, documents, media = response.json()
        assert (data, documents) == (_offered("data"), _offered("data", "documents"))
        assert media == _offered("media", volumes_error=media["volumes_error"])
        assert stderr in media["volumes_error"]
        assert pool_host.changes == []

    def test_a_failing_subvolume_list_leaves_every_pool_offered(self, pool_host):
        """It answered 500 before (ADR-0243), and the page then offered no pool at all."""
        stderr = "ERROR: can't perform the search: Permission denied"
        pool_host.subvols.add(DATA_UUID, "documents")
        pool_host.fail[("btrfs", "subvolume", "list")] = (1, stderr)

        with admin_client() as api:
            response = api.get(SUBVOLUMES)

        assert response.status_code == 200, response.text
        (data,) = response.json()
        assert data == _offered("data", volumes_error=f"Listing the subvolumes of /mnt/pools/data failed: {stderr}")

    def test_a_pool_that_does_not_answer_is_offered_alone(self, pool_host, monkeypatch):
        """A hung pool: btrfs is stopped after its timeout, and the pool is offered with why its volumes are missing."""
        pool_host.mount_pool("media")
        pool_host.subvols.add(DATA_UUID, "documents")
        answer = host_module.run_on_host

        def run_on_host(cmd, **kwargs):
            if cmd[:3] == ["btrfs", "subvolume", "list"] and cmd[-1] == "/mnt/pools/media":
                raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])
            return answer(cmd, **kwargs)

        monkeypatch.setattr(host_module, "run_on_host", run_on_host)

        with admin_client() as api:
            response = api.get(SUBVOLUMES)

        assert response.status_code == 200, response.text
        assert response.json() == [
            _offered("data"),
            _offered("data", "documents"),
            _offered(
                "media",
                volumes_error=f"btrfs did not answer within {storage_pools.VOLUME_TIMEOUT} seconds; "
                "the pool may not be responding.",
            ),
        ]

    def test_commands_run_on_the_host_only(self, pool_host):
        """In the host's mount namespace, where the Storage page reads the pools (ADR-0020)."""
        with admin_client() as api:
            assert api.get(SUBVOLUMES).status_code == 200
        # install_fake_host blocks subprocess.run: a command in the container fails the test.
        assert ["btrfs", "subvolume", "list", "/mnt/pools/data"] in pool_host.calls


class TestListSnapshots:
    """Tests for listing snapshots."""

    async def test_list_snapshots_parses_btrfs_show(self, pools, btrfs):
        snap_path = btrfs.snapshot(pools.pool, "snap_20250124_120000")

        (snap,) = await list_snapshots(str(pools.pool), {})

        assert snap.model_dump() == {
            "id": "snap_20250124_120000",
            "name": "snap_20250124_120000",
            "subvolume": str(pools.pool),
            "path": str(snap_path),
            # With its offset, so that the browser shows it in its own time zone (ADR-0243).
            "created": "2025-01-24T12:00:00+00:00",
        }
        # Only btrfs: du walked every file of every snapshot at each listing (ADR-0243).
        assert btrfs.calls == [["btrfs", "subvolume", "show", str(snap_path)]]

    async def test_creation_times_are_given_in_utc_newest_first(self, pools, btrfs):
        """btrfs prints its local time; a change of the offset (summer time) must not reorder the list."""
        btrfs.state["created"] = {
            "a": "2025-03-30 03:30:00 +0200",  # 01:30 UTC
            "b": "2025-03-30 01:45:00 +0000",  # 01:45 UTC
            "c": "2025-03-30 01:15:00 +0100",  # 00:15 UTC
        }
        for name in btrfs.state["created"]:
            btrfs.snapshot(pools.pool, name)

        snapshots = await list_snapshots(str(pools.pool), {})

        assert [(s.name, s.created) for s in snapshots] == [
            ("b", "2025-03-30T01:45:00+00:00"),
            ("a", "2025-03-30T01:30:00+00:00"),
            ("c", "2025-03-30T00:15:00+00:00"),
        ]

    async def test_a_creation_time_that_cannot_be_read_is_shown_as_btrfs_prints_it(self, pools, btrfs):
        btrfs.state["created"] = {"odd": "-"}
        btrfs.snapshot(pools.pool, "odd")

        (snap,) = await list_snapshots(str(pools.pool), {})

        assert snap.created == "-"

    async def test_list_snapshots_skips_symlinks(self, pools, btrfs):
        btrfs.snapshot(pools.pool, "real")
        os.symlink(pools.outside, pools.pool / ".snapshots" / "evil")
        (pools.pool / ".snapshots" / "file").write_text("x")
        # A planted .snapshots symlink in another pool is ignored entirely.
        os.symlink(pools.outside, pools.media / ".snapshots")

        assert [s.name for s in await list_snapshots(str(pools.pool), {})] == ["real"]
        assert await list_snapshots(str(pools.media), {}) == []
        assert all(str(pools.outside) not in " ".join(cmd) for cmd in btrfs.calls)

    async def test_list_snapshots_of_one_subvolume(self, pools, btrfs):
        btrfs.snapshot(pools.pool, "a")
        btrfs.snapshot(pools.media, "b")
        volume = btrfs.subvolume(pools.pool / "volume")
        btrfs.snapshot(volume, "c")

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
        assert await list_snapshots(str(pools.pool), {}) == []

    def test_the_list_is_of_one_pool_or_volume(self, pools, btrfs):
        """GET /snapshots/ without one listed only the folders right in the pools base, not a volume's snapshots."""
        btrfs.snapshot(btrfs.subvolume(pools.pool / "documents"), "before")

        with admin_client() as api:
            response = api.get("/api/v1/snapshots/")

        assert response.status_code == 422
        assert btrfs.calls == []


class TestCreateSnapshot:
    """Tests for creating snapshots."""

    async def test_create_snapshot_success(self, pools, btrfs):
        """Test creating a snapshot successfully."""
        result = await create_snapshot(SnapshotCreate(subvolume=str(pools.pool), name="test_snapshot"), REQUEST, ADMIN)

        assert result.name == "test_snapshot"
        assert result.subvolume == str(pools.pool)
        expected = pools.pool / ".snapshots" / "test_snapshot"
        assert result.path == str(expected)
        # The open folders (see the race tests below); "/." keeps btrfs from snapshotting into a folder.
        assert btrfs.calls == [["btrfs", "subvolume", "snapshot", "-r", f"{pools.pool}/.", str(expected)]]
        assert expected.is_dir()

    async def test_create_snapshot_auto_name(self, pools, btrfs):
        """Test creating a snapshot with auto-generated name."""
        result = await create_snapshot(SnapshotCreate(subvolume=str(pools.pool)), REQUEST, ADMIN)

        assert result.name.startswith("snap_")

    async def test_a_snapshot_without_a_name_is_named_after_the_time_in_utc(self, pools, btrfs, clock_in_utc_plus_9):
        """As the page and the guide say: the container's clock could be set to another time zone."""
        before = datetime.now(UTC).replace(microsecond=0)

        result = await create_snapshot(SnapshotCreate(subvolume=str(pools.pool)), REQUEST, ADMIN)

        named = datetime.strptime(result.name, "snap_%Y%m%d_%H%M%S").replace(tzinfo=UTC)
        assert before <= named <= datetime.now(UTC)

    async def test_create_snapshot_of_a_volume(self, pools, btrfs):
        """A volume (a subvolume in the pool's top folder) keeps its snapshots in its own .snapshots."""
        volume = btrfs.subvolume(pools.pool / "documents")

        result = await create_snapshot(SnapshotCreate(subvolume=str(volume), name="before"), REQUEST, ADMIN)

        expected = volume / ".snapshots" / "before"
        assert (result.subvolume, result.path) == (str(volume), str(expected))
        assert btrfs.calls == [["btrfs", "subvolume", "snapshot", "-r", f"{volume}/.", str(expected)]]
        assert not (pools.pool / ".snapshots").exists()
        (listed,) = await list_snapshots(str(volume), {})
        assert (listed.name, listed.created) == ("before", "2025-01-24T12:00:00+00:00")

    async def test_the_snapshot_goes_into_the_snapshot_folder_that_was_checked(self, pools, btrfs):
        """Whatever writes in the volume can swap .snapshots for a link right before btrfs runs (ADR-0242)."""
        documents = btrfs.subvolume(pools.pool / "documents")
        photos = btrfs.subvolume(pools.pool / "photos")
        (documents / ".snapshots").mkdir()

        def swap():
            os.rename(documents / ".snapshots", documents / "old")
            os.symlink(photos, documents / ".snapshots")

        btrfs.before[("btrfs", "subvolume", "snapshot")] = swap

        result = await create_snapshot(SnapshotCreate(subvolume=str(documents), name="before"), REQUEST, ADMIN)

        assert result.path == str(documents / ".snapshots" / "before")
        # Into the folder that was opened, wherever it is now; nothing went into the volume the link leads to.
        assert (documents / "old" / "before").is_dir()
        assert list(photos.iterdir()) == []

    async def test_no_snapshot_goes_into_a_folder_that_takes_its_name_meanwhile(self, pools, btrfs):
        """btrfs-progs makes a snapshot inside an existing folder of the given name: here the source's "." stops it."""
        documents = btrfs.subvolume(pools.pool / "documents")
        photos = btrfs.subvolume(pools.pool / "photos")
        btrfs.before[("btrfs", "subvolume", "snapshot")] = lambda: os.symlink(photos, documents / ".snapshots" / "x")

        with pytest.raises(HTTPException) as exc_info:
            await create_snapshot(SnapshotCreate(subvolume=str(documents), name="x"), REQUEST, ADMIN)

        assert exc_info.value.status_code == 500
        assert exc_info.value.detail == "Failed to create snapshot: ERROR: invalid snapshot name '.'"
        assert list(photos.iterdir()) == []

    async def test_create_snapshot_answers_its_time_with_the_offset(self, pools, btrfs):
        before = datetime.now(UTC).replace(microsecond=0)

        result = await create_snapshot(SnapshotCreate(subvolume=str(pools.pool), name="x"), REQUEST, ADMIN)

        created = datetime.fromisoformat(result.created)
        assert created.utcoffset() == timedelta(0)
        assert before <= created <= datetime.now(UTC)

    async def test_snapshot_folder_that_cannot_be_made_is_reported(self, pools, btrfs, monkeypatch):
        """A pool that btrfs made read-only after an error answered 500 without a reason."""

        def read_only(path, mode=0o777, *, dir_fd=None):
            raise OSError(errno.EROFS, "Read-only file system", str(path))

        monkeypatch.setattr(os, "mkdir", read_only)

        with pytest.raises(HTTPException) as exc_info:
            await create_snapshot(SnapshotCreate(subvolume=str(pools.pool), name="x"), REQUEST, ADMIN)

        assert exc_info.value.status_code == 500
        assert exc_info.value.detail == (
            f"Failed to create snapshot: cannot create {pools.pool / '.snapshots'}: Read-only file system"
        )
        assert btrfs.calls == []

    async def test_create_snapshot_not_found(self, pools, btrfs):
        """Test creating snapshot for non-existent subvolume."""
        with pytest.raises(HTTPException) as exc_info:
            await create_snapshot(SnapshotCreate(subvolume=str(pools.base / "nonexistent")), REQUEST, ADMIN)

        assert exc_info.value.status_code == 404

    @pytest.mark.parametrize(
        "subvolume", ["/etc", "/", "{root}/outside", "{root}/pools-old", "{base}", "{base}/datapool/../../outside",
                      "relative", "{base}/datapool\n"],
    )
    async def test_create_snapshot_rejects_bad_subvolume(self, pools, btrfs, subvolume):
        with pytest.raises(HTTPException) as exc_info:
            await create_snapshot(SnapshotCreate(subvolume=subvolume.format(root=pools.root, base=pools.base)), REQUEST, ADMIN)
        assert exc_info.value.status_code == 400
        assert btrfs.calls == []

    async def test_create_snapshot_rejects_symlink_escaping_pools(self, pools, btrfs):
        os.symlink(pools.outside, pools.pool / "escape")
        with pytest.raises(HTTPException) as exc_info:
            await create_snapshot(SnapshotCreate(subvolume=str(pools.pool / "escape")), REQUEST, ADMIN)
        assert exc_info.value.status_code == 400
        assert btrfs.calls == []

    async def test_create_snapshot_rejects_symlink_to_other_pool(self, pools, btrfs):
        """A link planted inside one pool must not redirect the snapshot to another pool."""
        os.symlink(pools.media, pools.pool / "apps")
        with pytest.raises(HTTPException) as exc_info:
            await create_snapshot(SnapshotCreate(subvolume=str(pools.pool / "apps"), name="x"), REQUEST, ADMIN)
        assert exc_info.value.status_code == 400
        assert btrfs.calls == []
        assert not (pools.media / ".snapshots").exists()

    @pytest.mark.parametrize("name", ["../../outside/x", "a/b", "..", ".", "x\n", ".hidden", "x" * 129])
    async def test_create_snapshot_rejects_bad_names(self, pools, btrfs, name):
        with pytest.raises(HTTPException) as exc_info:
            await create_snapshot(SnapshotCreate(subvolume=str(pools.pool), name=name), REQUEST, ADMIN)
        assert exc_info.value.status_code == 400
        assert btrfs.calls == []
        assert list(pools.outside.iterdir()) == []

    async def test_create_snapshot_refuses_symlinked_snapshot_dir(self, pools, btrfs):
        os.symlink(pools.outside, pools.pool / ".snapshots")
        with pytest.raises(HTTPException) as exc_info:
            await create_snapshot(SnapshotCreate(subvolume=str(pools.pool), name="x"), REQUEST, ADMIN)
        assert exc_info.value.status_code == 400
        assert btrfs.calls == []

    async def test_create_snapshot_already_exists(self, pools, btrfs):
        """Test creating snapshot that already exists."""
        btrfs.snapshot(pools.pool, "existing_snapshot")

        with pytest.raises(HTTPException) as exc_info:
            await create_snapshot(SnapshotCreate(subvolume=str(pools.pool), name="existing_snapshot"), REQUEST, ADMIN)

        assert exc_info.value.status_code == 400

    async def test_create_snapshot_btrfs_failure(self, pools, btrfs):
        """Test handling btrfs command failure."""
        btrfs.state["fail"] = True

        with pytest.raises(HTTPException) as exc_info:
            await create_snapshot(SnapshotCreate(subvolume=str(pools.pool), name="test_snapshot"), REQUEST, ADMIN)

        assert exc_info.value.status_code == 500


class TestDeleteSnapshot:
    """Tests for deleting snapshots."""

    async def test_delete_snapshot_success(self, pools, btrfs):
        """By the id of the snapshot that was opened, checked to be where it was asked for (ADR-0242)."""
        snap = btrfs.snapshot(pools.pool, "snap_test")
        snap_id = str(btrfs.id_of(snap))

        result = await delete_snapshot("snap_test", str(pools.pool), REQUEST, ADMIN)

        assert result["status"] == "deleted"
        assert result["id"] == "snap_test"
        assert btrfs.calls == [
            # The subvolume mounted at the pool's mountpoint: the top level.
            ["btrfs", "inspect-internal", "rootid", str(pools.pool)],
            # The open snapshot (/proc/self/fd/<fd>), and where its id is in the pool's filesystem.
            ["btrfs", "inspect-internal", "rootid", str(snap)],
            ["btrfs", "inspect-internal", "subvolid-resolve", snap_id, str(pools.pool)],
            ["btrfs", "subvolume", "delete", "--subvolid", snap_id, str(pools.pool)],
        ]
        assert btrfs.deleted == [snap]

    async def test_delete_a_snapshot_of_a_volume(self, pools, btrfs):
        volume = btrfs.subvolume(pools.pool / "documents")
        snap = btrfs.snapshot(volume, "before")
        snap_id = str(btrfs.id_of(snap))
        btrfs.snapshot(pools.pool, "before")  # the pool's snapshot of the same name stays

        result = await delete_snapshot("before", str(volume), REQUEST, ADMIN)

        assert result == {"status": "deleted", "id": "before"}
        # Checked to be documents/.snapshots/before below the top level; any path of the pool names the filesystem.
        assert btrfs.calls[-2:] == [
            ["btrfs", "inspect-internal", "subvolid-resolve", snap_id, str(pools.pool)],
            ["btrfs", "subvolume", "delete", "--subvolid", snap_id, str(pools.pool)],
        ]
        assert btrfs.deleted == [snap]
        assert (pools.pool / ".snapshots" / "before").is_dir()

    async def test_a_snapshot_swapped_for_a_link_before_btrfs_runs_is_deleted_and_not_the_link(self, pools, btrfs):
        """Whatever writes in the volume can swap the entry between the check and the delete (ADR-0242).

        btrfs-progs resolves a path given to it again, links included: by path,
        this deleted the volume the link leads to.
        """
        documents = btrfs.subvolume(pools.pool / "documents")
        photos = btrfs.subvolume(pools.pool / "photos")
        (photos / "holiday.jpg").write_text("x")
        snap = btrfs.snapshot(documents, "before")

        def swap():
            os.rename(snap, documents / ".snapshots" / "moved")
            os.symlink(photos, snap)

        btrfs.before[("btrfs", "subvolume", "delete")] = swap

        result = await delete_snapshot("before", str(documents), REQUEST, ADMIN)

        assert result == {"status": "deleted", "id": "before"}
        # The snapshot that was checked, wherever it is now.
        assert btrfs.deleted == [documents / ".snapshots" / "moved"]
        assert (photos / "holiday.jpg").read_text() == "x"

    async def test_a_snapshot_replaced_while_it_is_checked_is_not_deleted(self, pools, btrfs):
        """Another subvolume that takes the name meanwhile is not deleted in its place either."""
        documents = btrfs.subvolume(pools.pool / "documents")
        snap = btrfs.snapshot(documents, "before")
        other = btrfs.snapshot(documents, "other")

        def swap():
            os.rename(snap, documents / ".snapshots" / "moved")
            os.rename(other, snap)

        btrfs.before[("btrfs", "inspect-internal", "subvolid-resolve")] = swap

        with pytest.raises(HTTPException) as exc_info:
            await delete_snapshot("before", str(documents), REQUEST, ADMIN)

        assert exc_info.value.status_code == 409
        assert exc_info.value.detail == (
            "Snapshot before was not deleted: it was renamed or replaced while it was checked. Try again."
        )
        assert btrfs.deleted == []

    async def test_a_plain_folder_in_snapshots_is_not_a_snapshot(self, pools, btrfs):
        """The subvolume id of a plain folder is the volume's: the volume must not be deleted for it."""
        documents = btrfs.subvolume(pools.pool / "documents")
        (documents / "report.txt").write_text("x")
        _plain_folder(documents / ".snapshots" / "plain")

        with pytest.raises(HTTPException) as exc_info:
            await delete_snapshot("plain", str(documents), REQUEST, ADMIN)

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == f"{documents}/.snapshots/plain is not a snapshot: it is not a btrfs subvolume"
        assert btrfs.deleted == []
        assert (documents / "report.txt").read_text() == "x"
        assert all(call[:3] != ["btrfs", "subvolume", "delete"] for call in btrfs.calls)

    async def test_a_snapshot_of_a_pool_mounted_at_a_subvolume(self, pools, btrfs, monkeypatch):
        """Its place below the top level starts with the mounted subvolume's (mounted with subvol=)."""
        snap = btrfs.snapshot(pools.pool, "nightly")
        snap_id = btrfs.id_of(snap)
        answer = btrfs.run

        def mounted_at_subvolume_300(cmd, *args, **kwargs):
            if cmd == ["btrfs", "inspect-internal", "rootid", str(pools.pool)]:
                return SimpleNamespace(returncode=0, stdout="300\n", stderr="")
            if cmd == ["btrfs", "inspect-internal", "subvolid-resolve", "300", str(pools.pool)]:
                return SimpleNamespace(returncode=0, stdout="@data\n", stderr="")
            if cmd == ["btrfs", "inspect-internal", "subvolid-resolve", str(snap_id), str(pools.pool)]:
                return SimpleNamespace(returncode=0, stdout="@data/.snapshots/nightly\n", stderr="")
            return answer(cmd, *args, **kwargs)

        with patch("src.snapshots.router.subprocess.run", side_effect=mounted_at_subvolume_300):
            assert await delete_snapshot("nightly", str(pools.pool), REQUEST, ADMIN) == {"status": "deleted", "id": "nightly"}

        assert btrfs.deleted == [snap]

    async def test_delete_snapshot_not_found(self, pools, btrfs):
        """Test deleting non-existent snapshot."""
        with pytest.raises(HTTPException) as exc_info:
            await delete_snapshot("nonexistent", str(pools.pool), REQUEST, ADMIN)

        assert exc_info.value.status_code == 404

    @pytest.mark.parametrize("snapshot_id", ["../../../outside", "../x", "a/b", ".", "..", "", "a\x00b", "x" * 256])
    async def test_delete_snapshot_rejects_bad_ids(self, pools, btrfs, snapshot_id):
        btrfs.snapshot(pools.pool, "keep")
        _plain_folder(pools.outside / ".snapshots" / "x")
        with pytest.raises(HTTPException) as exc_info:
            await delete_snapshot(snapshot_id, str(pools.pool), REQUEST, ADMIN)
        assert exc_info.value.status_code == 400
        assert btrfs.calls == []

    @pytest.mark.parametrize("name", NAMES_FROM_OTHER_TOOLS)
    async def test_delete_snapshot_accepts_any_listed_name(self, pools, btrfs, name):
        """Snapshots made by other tools may have names create_snapshot refuses."""
        snap = btrfs.snapshot(pools.pool, name)
        snap_id = str(btrfs.id_of(snap))
        (listed,) = await list_snapshots(str(pools.pool), {})
        assert listed.name == name
        btrfs.calls.clear()

        result = await delete_snapshot(listed.id, listed.subvolume, REQUEST, ADMIN)

        assert result == {"status": "deleted", "id": name}
        # The path that btrfs gives the id ends with the name, also with a line break at its end.
        assert btrfs.calls[-1] == ["btrfs", "subvolume", "delete", "--subvolid", snap_id, str(pools.pool)]
        assert btrfs.deleted == [snap]

    async def test_delete_snapshot_missing_odd_name_is_not_found(self, pools, btrfs):
        btrfs.snapshot(pools.pool, "keep")
        with pytest.raises(HTTPException) as exc_info:
            await delete_snapshot("x\n", str(pools.pool), REQUEST, ADMIN)
        assert exc_info.value.status_code == 404
        assert btrfs.calls == []

    async def test_delete_snapshot_file_entry_is_refused(self, pools, btrfs):
        (pools.pool / ".snapshots").mkdir()
        (pools.pool / ".snapshots" / "notes").write_text("x")
        with pytest.raises(HTTPException) as exc_info:
            await delete_snapshot("notes", str(pools.pool), REQUEST, ADMIN)
        assert exc_info.value.status_code == 400
        assert btrfs.calls == []

    @pytest.mark.parametrize("subvolume", ["/", "{root}/outside", "{base}/datapool/../../outside", "{base}"])
    async def test_delete_snapshot_rejects_bad_subvolume(self, pools, btrfs, subvolume):
        _plain_folder(pools.outside / ".snapshots" / "victim")
        with pytest.raises(HTTPException) as exc_info:
            await delete_snapshot("victim", subvolume.format(root=pools.root, base=pools.base), REQUEST, ADMIN)
        assert exc_info.value.status_code == 400
        assert btrfs.calls == []

    async def test_delete_snapshot_entry_symlink_is_not_followed(self, pools, btrfs):
        """A planted symlink must not turn a snapshot delete into a delete elsewhere."""
        victim = btrfs.subvolume(pools.media / "important")
        (pools.pool / ".snapshots").mkdir()
        os.symlink(victim, pools.pool / ".snapshots" / "fake")

        with pytest.raises(HTTPException) as exc_info:
            await delete_snapshot("fake", str(pools.pool), REQUEST, ADMIN)

        assert exc_info.value.status_code == 400
        assert "symbolic link" in exc_info.value.detail
        assert btrfs.calls == []
        assert victim.is_dir()

    async def test_delete_snapshot_symlinked_snapshot_dir_is_refused(self, pools, btrfs):
        btrfs.subvolume(pools.media / "important")
        os.symlink(pools.media, pools.pool / ".snapshots")

        with pytest.raises(HTTPException) as exc_info:
            await delete_snapshot("important", str(pools.pool), REQUEST, ADMIN)

        assert exc_info.value.status_code == 400
        assert btrfs.calls == []

    async def test_delete_snapshot_btrfs_failure(self, pools, btrfs):
        """Test handling btrfs delete failure."""
        btrfs.snapshot(pools.pool, "snap_test")
        btrfs.state["fail"] = True

        with pytest.raises(HTTPException) as exc_info:
            await delete_snapshot("snap_test", str(pools.pool), REQUEST, ADMIN)

        assert exc_info.value.status_code == 500
        assert btrfs.deleted == []


# The name a restore gives its copy when utc_stamp is frozen (frozen_stamp).
RESTORE_NAME = "restore_20261107_103000"


@pytest.fixture
def frozen_stamp(monkeypatch):
    """The time that snapshot and restore names get: 2026-11-07 10:30:00 UTC."""
    # The package exports the APIRouter as "router", which hides the module of that name.
    monkeypatch.setattr(importlib.import_module("src.snapshots.router"), "utc_stamp", lambda: "20261107_103000")


class TestRestoreSnapshot:
    """Tests for restoring snapshots."""

    async def test_restore_snapshot_success(self, pools, btrfs):
        """Test restoring a snapshot successfully."""
        snap = btrfs.snapshot(pools.pool, "snap_test")

        result = await restore_snapshot(SnapshotRestore(snapshot_path=str(snap)), REQUEST, ADMIN)

        assert result["status"] == "restored"
        assert result["source"] == str(snap)
        target = Path(result["target"])
        assert target.parent == pools.pool
        assert target.name.startswith("restore_")
        # Writable (no -r), of the open snapshot into the open pool (see the race tests below).
        assert btrfs.calls == [["btrfs", "subvolume", "snapshot", f"{snap}/.", str(target)]]
        assert target.is_dir()

    async def test_a_restored_copy_is_named_after_the_time_in_utc(self, pools, btrfs, clock_in_utc_plus_9):
        """As the page and the guide say: the container's clock could be set to another time zone."""
        snap = btrfs.snapshot(pools.pool, "snap_test")
        before = datetime.now(UTC).replace(microsecond=0)

        result = await restore_snapshot(SnapshotRestore(snapshot_path=str(snap)), REQUEST, ADMIN)

        named = datetime.strptime(Path(result["target"]).name, "restore_%Y%m%d_%H%M%S").replace(tzinfo=UTC)
        assert before <= named <= datetime.now(UTC)

    async def test_restore_a_snapshot_of_a_volume_next_to_its_files(self, pools, btrfs):
        volume = btrfs.subvolume(pools.pool / "documents")
        snap = btrfs.snapshot(volume, "before")

        result = await restore_snapshot(SnapshotRestore(snapshot_path=str(snap)), REQUEST, ADMIN)

        target = Path(result["target"])
        assert (result["status"], target.parent) == ("restored", volume)
        assert target.name.startswith("restore_")
        # Writable: no -r.
        assert btrfs.calls == [["btrfs", "subvolume", "snapshot", f"{snap}/.", str(target)]]

    async def test_the_snapshot_that_was_checked_is_restored(self, pools, btrfs, frozen_stamp):
        """Whatever writes in the volume can swap the snapshot for a link right before btrfs runs (ADR-0242).

        By path, btrfs copied the snapshot of another volume that the link led
        to into this volume, where whatever made the link could read it.
        """
        documents = btrfs.subvolume(pools.pool / "documents")
        photos = btrfs.subvolume(pools.pool / "photos")
        snap = btrfs.snapshot(documents, "before")
        (snap / "report.txt").write_text("documents")
        secret = btrfs.snapshot(photos, "private")
        (secret / "holiday.jpg").write_text("photos")

        def swap():
            os.rename(snap, documents / ".snapshots" / "moved")
            os.symlink(secret, snap)

        btrfs.before[("btrfs", "subvolume", "snapshot")] = swap

        result = await restore_snapshot(SnapshotRestore(snapshot_path=str(snap)), REQUEST, ADMIN)

        copy = documents / RESTORE_NAME
        assert result == {"status": "restored", "source": str(snap), "target": str(copy)}
        assert sorted(p.name for p in copy.iterdir()) == ["report.txt"]

    async def test_the_copy_goes_into_the_folder_that_was_checked(self, pools, btrfs, frozen_stamp):
        """A volume swapped for a link to another one before btrfs runs does not get the copy."""
        documents = btrfs.subvolume(pools.pool / "documents")
        photos = btrfs.subvolume(pools.pool / "photos")
        snap = btrfs.snapshot(documents, "before")

        def swap():
            os.rename(documents, pools.pool / "documents.old")
            os.symlink(photos, documents)

        btrfs.before[("btrfs", "subvolume", "snapshot")] = swap

        await restore_snapshot(SnapshotRestore(snapshot_path=str(snap)), REQUEST, ADMIN)

        assert (pools.pool / "documents.old" / RESTORE_NAME).is_dir()
        assert list(photos.iterdir()) == []

    async def test_no_copy_goes_into_a_folder_that_takes_its_name_meanwhile(self, pools, btrfs, frozen_stamp):
        """btrfs-progs makes a snapshot inside an existing folder of the given name: here the source's "." stops it."""
        documents = btrfs.subvolume(pools.pool / "documents")
        photos = btrfs.subvolume(pools.pool / "photos")
        snap = btrfs.snapshot(documents, "before")
        btrfs.before[("btrfs", "subvolume", "snapshot")] = lambda: os.symlink(photos, documents / RESTORE_NAME)

        with pytest.raises(HTTPException) as exc_info:
            await restore_snapshot(SnapshotRestore(snapshot_path=str(snap)), REQUEST, ADMIN)

        assert exc_info.value.status_code == 500
        assert exc_info.value.detail == "Failed to restore: ERROR: invalid snapshot name '.'"
        assert list(photos.iterdir()) == []

    async def test_restore_snapshot_with_target(self, pools, btrfs):
        """Test restoring to specific target."""
        snap = btrfs.snapshot(pools.pool, "snap_test")
        custom = _plain_folder(pools.pool / "custom")

        result = await restore_snapshot(SnapshotRestore(snapshot_path=str(snap), target_path=str(custom)), REQUEST, ADMIN)

        assert result["status"] == "restored"
        target = Path(result["target"])
        assert target.parent == custom
        assert btrfs.calls == [["btrfs", "subvolume", "snapshot", f"{snap}/.", str(target)]]

    async def test_restore_snapshot_already_exists(self, pools, btrfs, frozen_stamp):
        snap = btrfs.snapshot(pools.pool, "snap_test")
        (pools.pool / RESTORE_NAME).write_text("x")

        with pytest.raises(HTTPException) as exc_info:
            await restore_snapshot(SnapshotRestore(snapshot_path=str(snap)), REQUEST, ADMIN)

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == f"Restore target already exists: {pools.pool / RESTORE_NAME}"
        assert btrfs.calls == []

    async def test_restore_snapshot_not_found(self, pools, btrfs):
        """Test restoring non-existent snapshot."""
        (pools.pool / ".snapshots").mkdir()
        with pytest.raises(HTTPException) as exc_info:
            await restore_snapshot(
                SnapshotRestore(snapshot_path=str(pools.pool / ".snapshots" / "nonexistent")), REQUEST, ADMIN
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
        _plain_folder(pools.outside / ".snapshots" / "x")
        with pytest.raises(HTTPException) as exc_info:
            await restore_snapshot(SnapshotRestore(snapshot_path=template.format(root=pools.root, base=pools.base)), REQUEST, ADMIN)
        assert exc_info.value.status_code == 400
        assert btrfs.calls == []

    @pytest.mark.parametrize("name", NAMES_FROM_OTHER_TOOLS)
    async def test_restore_snapshot_accepts_any_listed_name(self, pools, btrfs, name):
        """Snapshots made by other tools may have names create_snapshot refuses."""
        snap = btrfs.snapshot(pools.pool, name)
        (listed,) = await list_snapshots(str(pools.pool), {})
        assert listed.path == str(snap)
        btrfs.calls.clear()

        result = await restore_snapshot(SnapshotRestore(snapshot_path=listed.path), REQUEST, ADMIN)

        assert result["source"] == str(snap)
        target = Path(result["target"])
        assert target.parent == pools.pool
        assert btrfs.calls == [["btrfs", "subvolume", "snapshot", f"{snap}/.", str(target)]]

    @pytest.mark.parametrize(
        "name", ["../x", "a/b", ".", "..", "", "a\x00b", "x" * 256], ids=repr
    )
    async def test_restore_snapshot_rejects_bad_names(self, pools, btrfs, name):
        btrfs.snapshot(pools.pool, "x")
        btrfs.snapshot(pools.pool, "a")
        with pytest.raises(HTTPException) as exc_info:
            await restore_snapshot(SnapshotRestore(snapshot_path=f"{pools.pool}/.snapshots/{name}"), REQUEST, ADMIN)
        assert exc_info.value.status_code == 400
        assert btrfs.calls == []

    @pytest.mark.parametrize("link", ["entry", "snapshot-dir"])
    async def test_restore_snapshot_refuses_symlinks(self, pools, btrfs, link):
        """A planted symlink must not turn a restore into a copy of something else."""
        victim = btrfs.subvolume(pools.media / "important")
        if link == "entry":
            (pools.pool / ".snapshots").mkdir()
            os.symlink(victim, pools.pool / ".snapshots" / "important")
        else:
            os.symlink(pools.media, pools.pool / ".snapshots")

        with pytest.raises(HTTPException) as exc_info:
            await restore_snapshot(
                SnapshotRestore(snapshot_path=str(pools.pool / ".snapshots" / "important")), REQUEST, ADMIN
            )

        assert exc_info.value.status_code == 400
        assert "symbolic link" in exc_info.value.detail
        assert btrfs.calls == []

    @pytest.mark.parametrize("target", ["/etc", "{root}/outside", "{base}", "{base}/datapool/../../outside"])
    async def test_restore_snapshot_rejects_bad_target(self, pools, btrfs, target):
        snap = btrfs.snapshot(pools.pool, "snap_test")
        with pytest.raises(HTTPException) as exc_info:
            await restore_snapshot(
                SnapshotRestore(snapshot_path=str(snap), target_path=target.format(root=pools.root, base=pools.base)),
                REQUEST,
                ADMIN,
            )
        assert exc_info.value.status_code == 400
        assert btrfs.calls == []
        assert list(pools.outside.iterdir()) == []

    async def test_restore_snapshot_rejects_target_through_symlink(self, pools, btrfs):
        snap = btrfs.snapshot(pools.pool, "snap_test")
        os.symlink(pools.media, pools.pool / "apps")
        with pytest.raises(HTTPException) as exc_info:
            await restore_snapshot(SnapshotRestore(snapshot_path=str(snap), target_path=str(pools.pool / "apps")), REQUEST, ADMIN)
        assert exc_info.value.status_code == 400
        assert btrfs.calls == []
        assert list(pools.media.iterdir()) == []

    async def test_restore_snapshot_btrfs_failure(self, pools, btrfs):
        """Test handling btrfs restore failure."""
        snap = btrfs.snapshot(pools.pool, "snap_test")
        btrfs.state["fail"] = True

        with pytest.raises(HTTPException) as exc_info:
            await restore_snapshot(SnapshotRestore(snapshot_path=str(snap)), REQUEST, ADMIN)

        assert exc_info.value.status_code == 500


# A string literal of a catalog of the web UI, in single or in double quotes.
TS_STRING = re.compile(r"'((?:[^'\\\n]|\\.)*)'|\"((?:[^\"\\\n]|\\.)*)\"")


def catalog_strings(path: Path) -> list[str]:
    """The string literals of a catalog of the web UI, its keys and its texts, without its comments."""
    lines = path.read_text(encoding="utf-8").splitlines()
    code = "\n".join(line for line in lines if not line.lstrip().startswith("//"))
    return [single or double for single, double in TS_STRING.findall(code)]


class TestTheWebUI:
    def test_the_page_names_the_folder_and_the_copies_as_the_server_does_in_every_language(self):
        # Names on the disk are the same in every language (ADR-0296): the Snapshots page has
        # them as constants, the server's, and no text of its catalog holds one, so that no
        # translation can change what the page says a folder or a copy is called.
        # The Snapshots page's names are in its module (ADR-0427).
        page = (REPO / "webui" / "src" / "lib" / "components" / "snapshots" / "names.ts").read_text(encoding="utf-8")
        catalog = REPO / "webui" / "src" / "lib" / "i18n" / "catalog" / "snapshots.ts"
        names = {
            "SNAPSHOTS_FOLDER": SNAPSHOT_DIR_NAME,
            "SNAP_PREFIX": SNAPSHOT_PREFIX,
            "RESTORE_PREFIX": RESTORE_PREFIX,
            "AUTO_PREFIX": AUTO_SNAPSHOT_PREFIX,
        }

        for constant, name in names.items():
            assert f"export const {constant} = '{name}';" in page
        texts = catalog_strings(catalog)
        assert {"Restore snapshot", "Восстановление снимка"} <= set(texts)
        assert [text for text in texts if any(name in text for name in names.values())] == []
