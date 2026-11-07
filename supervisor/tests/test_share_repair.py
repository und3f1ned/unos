"""Repair permissions (ADR-0155, ADR-0156): shares/repair.py and shares/repair_router.py.

The walk runs on real temporary folders with the test's own group
(gid=os.getgid()), so it needs neither root nor the SMB group, and modes are
checked with os.lstat. It needs os.O_PATH and /proc: the tests are skipped
where O_PATH is missing (macOS) and run in the Linux test runner. The pool is
a folder of the test's filesystem; a fake mount table says that the mount
holding it is mounted at the pool's mountpoint.
"""
import errno
import json
import os
import re
import sqlite3
import stat
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from src.auth.utils import require_admin, verify_token
from src.database import Base, get_db
from src.security import validators
from src.shares import accounts, constants, repair, repair_router, service
from src.shares.models import SmbShare
from src.storage import pools

pytestmark = pytest.mark.skipif(
    not hasattr(os, "O_PATH"), reason="Repair permissions needs os.O_PATH (Linux): runs in the Linux test runner"
)

GID = os.getgid()
ADMIN = SimpleNamespace(id=1, username="admin", is_admin=True)
REGULAR = SimpleNamespace(id=2, username="alice", is_admin=False)
URL = "/api/v1/shares/smb/{}/repair-permissions"
NOTHING_SKIPPED = dict.fromkeys(repair.SKIP_REASONS, 0)


def _mode(path) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


def _dir(path: Path, mode: int = 0o755) -> Path:
    path.mkdir()
    os.chmod(path, mode)
    return path


def _file(path: Path, mode: int = 0o644) -> Path:
    path.write_bytes(b"data")
    os.chmod(path, mode)
    return path


def _repair(folder: Path, **limits) -> repair.RepairResult:
    return repair.repair_tree(str(folder), gid=GID, **limits)


def _open_descriptors() -> int:
    return len(os.listdir("/proc/self/fd"))


def _with_st_dev(st: os.stat_result, st_dev: int) -> os.stat_result:
    fields = list(st[:10])
    fields[2] = st_dev
    return os.stat_result(fields)


def _mount_id_of(path) -> int:
    """The ID of the mount that holds ``path``: the mnt_id of /proc/self/fdinfo, field 1 of mountinfo."""
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        info = Path(f"/proc/self/fdinfo/{fd}").read_text()
    finally:
        os.close(fd)
    return int(re.search(r"^mnt_id:\s*(\d+)$", info, re.M).group(1))


def _mount_line(mount_id: int, point, fstype: str = "btrfs") -> str:
    """A line of /proc/self/mountinfo: the mount ``mount_id`` at ``point``."""
    return f"{mount_id} 1 0:40 / {point} rw,relatime shared:2 - {fstype} /dev/sda rw\n"


def _add_mounts(tmp_path: Path, *lines: str) -> None:
    """Add lines to the fake mount table of the ``base`` fixture."""
    with open(tmp_path / "mountinfo", "a") as file:
        file.write("".join(lines))


@pytest.fixture(autouse=True)
def fresh_router_state():
    repair_router._last_results.clear()
    repair_router.limiter.reset()
    yield
    repair_router._last_results.clear()
    repair_router.limiter.reset()


@pytest.fixture
def base(tmp_path, monkeypatch) -> Path:
    """The pools base of the test, with the pool folder data, mounted as far as repair_tree can tell.

    The fake mount table tmp_path/mountinfo (which _add_mounts extends) says
    that the mount holding the test's folders is mounted at <base>/data.
    """
    monkeypatch.setattr(validators.settings, "pools_base_path", str(tmp_path / "pools"))
    resolved = validators.pools_base()
    (resolved / "data").mkdir(parents=True)
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(_mount_line(_mount_id_of(resolved / "data"), resolved / "data"))
    monkeypatch.setattr(repair, "MOUNTINFO_PATH", str(mountinfo))
    return resolved


@pytest.fixture
def share(base) -> Path:
    """A share's folder inside the pool data, as found before a repair (0755)."""
    return _dir(base / "data" / "media")


class TestWalk:
    def test_folders_get_2770_and_files_0660_or_0770_with_the_owners_execute_bit(self, share):
        docs = _dir(share / "docs", 0o755)
        guest = _dir(docs / "guest-era", 0o777)
        files = {
            _file(docs / "report.txt", 0o644): 0o660,
            _file(guest / "upload.bin", 0o666): 0o660,
            _file(share / "run.sh", 0o755): 0o770,
            _file(share / "owner-x.sh", 0o744): 0o770,
            _file(share / "group-only.txt", 0o060): 0o660,
            # setuid, setgid and sticky are cleared
            _file(share / "setuid", 0o4755): 0o770,
            _file(share / "setgid", 0o2755): 0o770,
            _file(share / "both", 0o6644): 0o660,
            _file(share / "sticky", 0o1644): 0o660,
        }

        result = _repair(share)

        assert result.state == "done"
        assert result.message == "Permissions repaired."
        for folder in (share, docs, guest):
            assert _mode(folder) == 0o2770, folder
        for path, mode in files.items():
            assert _mode(path) == mode, path
        for path in (share, docs, guest, *files):
            st = os.lstat(path)
            assert (st.st_uid, st.st_gid) == (os.getuid(), GID), path  # owners never change
        assert (result.visited, result.changed) == (12, 12)
        assert result.skipped == NOTHING_SKIPPED
        assert (result.errors, result.error_samples, result.skipped_paths) == (0, [], [])
        assert result.share == ""  # the router sets it
        assert result.started_at <= result.finished_at

    def test_symlinks_are_neither_changed_nor_followed(self, share, tmp_path):
        outside = _dir(tmp_path / "outside", 0o755)
        secret = _file(outside / "secret.txt", 0o644)
        docs = _dir(share / "docs", 0o755)
        inner = _file(docs / "a.txt", 0o644)
        links = {
            share / "to-a-file": inner,
            share / "to-a-folder": docs,
            share / "to-outside": outside,
            docs / "to-a-secret": secret,
        }
        for link, target in links.items():
            os.symlink(target, link)

        result = _repair(share)

        assert result.skipped["symlink"] == 4
        assert (_mode(outside), _mode(secret)) == (0o755, 0o644)
        for link, target in links.items():
            assert os.readlink(link) == str(target)
        # Reached on its own path, not through the links.
        assert (_mode(docs), _mode(inner)) == (0o2770, 0o660)
        assert (result.visited, result.changed) == (7, 3)

    def test_hard_linked_files_and_special_files_are_skipped(self, share, tmp_path):
        elsewhere = _file(tmp_path / "elsewhere.txt", 0o644)
        os.link(elsewhere, share / "linked.txt")
        pipe = share / "pipe"
        os.mkfifo(pipe)
        os.chmod(pipe, 0o666)

        result = _repair(share)

        assert (result.skipped["hardlink"], result.skipped["special"]) == (1, 1)
        assert (_mode(elsewhere), _mode(share / "linked.txt"), _mode(pipe)) == (0o644, 0o644, 0o666)
        assert result.changed == 1  # the share's folder

    def test_private_entries_are_left_alone_and_a_private_folder_is_not_entered(self, share):
        key = _file(share / "id_ed25519", 0o600)
        private = _dir(share / "private", 0o700)
        inside = _file(private / "notes.txt", 0o644)

        result = _repair(share)

        assert result.skipped["private"] == 2
        assert (_mode(key), _mode(private), _mode(inside)) == (0o600, 0o700, 0o644)
        assert result.visited == 3  # the share, the key and the folder; notes.txt was never reached

    def test_snapshots_are_skipped(self, share):
        snapshots = _dir(share / ".snapshots", 0o755)
        snapshot = _dir(snapshots / "2026-11-06", 0o755)
        old = _file(snapshot / "old.txt", 0o644)

        result = _repair(share)

        assert result.skipped["snapshots"] == 1
        assert (_mode(snapshots), _mode(snapshot), _mode(old)) == (0o755, 0o755, 0o644)
        assert result.skipped_paths == []

    def test_another_filesystem_is_skipped_and_listed(self, share, monkeypatch):
        volume = _dir(share / "volume", 0o755)
        data = _file(volume / "data.txt", 0o644)
        mounted_file = _file(share / "bound.txt", 0o644)
        real_lstat = os.lstat

        def lstat(path, *args, **kwargs):
            st = real_lstat(path, *args, **kwargs)
            if path in ("volume", "bound.txt") and kwargs.get("dir_fd") is not None:
                return _with_st_dev(st, st.st_dev + 1)
            return st

        monkeypatch.setattr(os, "lstat", lstat)

        result = _repair(share)

        assert result.skipped["other_filesystem"] == 2
        assert sorted(result.skipped_paths) == ["bound.txt", "volume"]
        assert (_mode(volume), _mode(data), _mode(mounted_file)) == (0o755, 0o644, 0o644)
        assert result.visited == 3  # data.txt was never reached

    def test_mount_points_from_mountinfo_are_skipped_and_listed(self, share, tmp_path):
        usb = _dir(share / "usb disk", 0o755)  # mountinfo writes the space as \040
        photo = _file(usb / "photo.jpg", 0o644)
        docs = _dir(share / "docs", 0o755)
        bind = _dir(docs / "bind", 0o755)
        bound = _file(bind / "other.txt", 0o644)
        sibling = _dir(share.parent / (share.name + "2"), 0o755)
        _add_mounts(
            tmp_path,
            "22 1 0:21 / / rw,relatime shared:1 - ext4 /dev/root rw\n",
            f"31 30 0:41 / {share}/usb\\040disk rw,relatime shared:3 - vfat /dev/sdb1 rw\n",
            # a bind mount of the same filesystem: same st_dev
            f"32 30 0:40 /elsewhere {share}/docs/bind rw,relatime shared:2 - btrfs /dev/sda rw\n",
            # a folder next to the share whose name starts with the share's
            f"33 22 0:42 / {sibling}/x rw - tmpfs tmpfs rw\n",
        )

        result = _repair(share)

        assert result.skipped["other_filesystem"] == 2
        assert sorted(result.skipped_paths) == ["docs/bind", "usb disk"]
        assert (_mode(usb), _mode(photo), _mode(bind), _mode(bound)) == (0o755, 0o644, 0o755, 0o644)
        assert (_mode(share), _mode(docs)) == (0o2770, 0o2770)

    def test_a_mount_point_is_skipped_without_being_examined(self, share, tmp_path, monkeypatch):
        # Even an lstat reaches into the mounted filesystem, whose driver can
        # block (a stalled FUSE daemon or NFS server), and no time limit stops
        # a blocked call: a mount point of the table is skipped by its path.
        cloud = _dir(share / "cloud", 0o755)
        snapshots = _dir(share / ".snapshots", 0o755)
        _add_mounts(
            tmp_path,
            f"41 30 0:51 / {cloud} rw,nosuid,nodev,relatime - fuse.rclone remote: rw,user_id=0,group_id=0\n",
            f"42 30 0:40 /@snapshots {snapshots} rw,relatime - btrfs /dev/sda rw\n",
        )
        real_lstat, real_open = os.lstat, os.open
        examined = []

        def lstat(path, *args, **kwargs):
            if path in ("cloud", ".snapshots"):
                examined.append(("lstat", path))
            return real_lstat(path, *args, **kwargs)

        def fake_open(path, flags, mode=0o777, *, dir_fd=None):
            if path in ("cloud", ".snapshots"):
                examined.append(("open", path))
            return real_open(path, flags, mode, dir_fd=dir_fd)

        monkeypatch.setattr(os, "lstat", lstat)
        monkeypatch.setattr(os, "open", fake_open)

        result = _repair(share)

        assert examined == []
        assert (result.skipped["other_filesystem"], result.skipped["snapshots"]) == (1, 1)
        assert result.skipped_paths == ["cloud"]
        assert (_mode(cloud), _mode(snapshots), _mode(share)) == (0o755, 0o755, 0o2770)
        assert result.visited == 3

    @pytest.mark.parametrize("table", ["mountinfo", "fdinfo"])
    def test_unreadable_mount_table_fails_and_changes_nothing(self, share, tmp_path, monkeypatch, table):
        data = _file(share / "a.txt", 0o644)
        attribute = {"mountinfo": "MOUNTINFO_PATH", "fdinfo": "FDINFO_DIR"}[table]
        monkeypatch.setattr(repair, attribute, str(tmp_path / "missing"))
        before = _open_descriptors()

        result = _repair(share)

        assert result.state == "failed"
        assert result.message == (
            f"The mount table could not be read: {os.strerror(errno.ENOENT)}. Nothing was changed."
        )
        assert (_mode(share), _mode(data)) == (0o755, 0o644)
        assert _open_descriptors() == before  # the share's folder was open: closed again

    @pytest.mark.parametrize("case", ["pool not mounted", "mount on the share's folder"])
    def test_a_share_folder_that_is_not_on_its_pools_mount_fails_and_changes_nothing(
        self, share, base, tmp_path, case
    ):
        data = _file(share / "a.txt", 0o644)
        own = _mount_id_of(share)
        lines = {
            # The pool was unmounted before the folder was opened: its
            # mountpoint is a plain folder of the system disk's mount.
            "pool not mounted": [_mount_line(own, base, "ext4")],
            # Another disk, or a folder of the system disk, mounted on the share's folder.
            "mount on the share's folder": [_mount_line(own + 1000, base / "data"), _mount_line(own, share, "ext4")],
        }[case]
        (tmp_path / "mountinfo").write_text("".join(lines))
        before = _open_descriptors()

        result = _repair(share)

        assert result.state == "failed"
        assert result.message == (
            "The share's folder is not on its pool's filesystem: the pool is not mounted, or another "
            "filesystem is mounted on the folder or above it. Nothing was changed."
        )
        assert (result.visited, result.changed) == (0, 0)
        assert (_mode(share), _mode(data)) == (0o755, 0o644)
        assert _open_descriptors() == before

    def test_an_entry_whose_descriptor_is_on_another_mount_is_skipped(self, share, monkeypatch):
        # A bind mount of the same filesystem (same st_dev) whose parent was
        # renamed after the mount table was read: its path is not in the
        # table, but its descriptor is on another mount. As for a folder, so
        # for a file bind-mounted onto a file.
        bound = _dir(share / "renamed", 0o755)
        app_config = _file(bound / "app.conf", 0o644)
        bound_file = _file(share / "bound.txt", 0o644)
        plain = _file(share / "plain.txt", 0o644)
        on_another_mount = {os.lstat(bound).st_ino, os.lstat(bound_file).st_ino}
        real_mount_id = repair._mount_id

        def mount_id(fd):
            return real_mount_id(fd) + (1 if os.fstat(fd).st_ino in on_another_mount else 0)

        monkeypatch.setattr(repair, "_mount_id", mount_id)

        result = _repair(share)

        assert result.skipped["other_filesystem"] == 2
        assert sorted(result.skipped_paths) == ["bound.txt", "renamed"]
        assert (_mode(bound), _mode(app_config), _mode(bound_file)) == (0o755, 0o644, 0o644)
        assert (_mode(share), _mode(plain)) == (0o2770, 0o660)
        assert result.visited == 4  # app.conf was never reached

    def test_skipped_paths_and_error_samples_keep_at_most_twenty(self, share, monkeypatch):
        for i in range(24):
            _dir(share / f"volume{i:02}", 0o755)
            _file(share / f"stuck{i:02}.txt", 0o644)
        real_lstat, real_chmod = os.lstat, os.chmod

        def lstat(path, *args, **kwargs):
            st = real_lstat(path, *args, **kwargs)
            if kwargs.get("dir_fd") is not None and str(path).startswith("volume"):
                return _with_st_dev(st, st.st_dev + 1)
            return st

        def chmod(path, mode, *args, **kwargs):
            if str(path).startswith("/proc/self/fd/"):
                raise PermissionError(errno.EPERM, os.strerror(errno.EPERM), path)
            return real_chmod(path, mode, *args, **kwargs)

        monkeypatch.setattr(os, "lstat", lstat)
        monkeypatch.setattr(os, "chmod", chmod)

        result = _repair(share)

        assert result.skipped["other_filesystem"] == 24
        assert len(result.skipped_paths) == repair.MAX_SAMPLES == 20
        assert set(result.skipped_paths) <= {f"volume{i:02}" for i in range(24)}
        assert result.errors == 24
        assert len(result.error_samples) == 20
        assert set(result.error_samples) <= {f"stuck{i:02}.txt: {os.strerror(errno.EPERM)}" for i in range(24)}
        assert result.message == "Permissions repaired, except for 24 entries that could not be changed."

    def test_names_that_are_not_utf8_are_answered_as_text(self, share, monkeypatch):
        name = os.fsdecode(b"caf\xe9")  # Latin-1, as listdir gives it
        os.mkdir(os.path.join(os.fsencode(share), b"caf\xe9"), 0o755)
        real_lstat = os.lstat

        def lstat(path, *args, **kwargs):
            st = real_lstat(path, *args, **kwargs)
            if kwargs.get("dir_fd") is not None and path == name:
                return _with_st_dev(st, st.st_dev + 1)
            return st

        monkeypatch.setattr(os, "lstat", lstat)

        result = _repair(share)

        assert result.skipped_paths == ["caf\ufffd"]
        assert json.loads(result.model_dump_json())["skipped_paths"] == ["caf\ufffd"]

    def test_deep_folders_stop_at_max_depth_with_few_descriptors_open(self, share, monkeypatch):
        chain = []
        folder = share
        for i in range(70):
            folder = _dir(folder / f"d{i + 1}", 0o755)
            chain.append(folder)
        real_lstat = os.lstat
        open_during_walk = []

        def lstat(path, *args, **kwargs):
            if kwargs.get("dir_fd") is not None:
                open_during_walk.append(_open_descriptors())
            return real_lstat(path, *args, **kwargs)

        monkeypatch.setattr(os, "lstat", lstat)
        before = _open_descriptors()

        result = _repair(share, max_depth=64)

        assert result.skipped["too_deep"] == 1
        assert _mode(chain[63]) == 0o2770  # d64, at depth 64
        assert _mode(chain[64]) == 0o755  # d65: too deep, neither changed nor entered
        assert result.visited == 66  # the share and d1 to d65
        assert max(open_during_walk) - before <= 64 + 1  # the share's folder and d1 to d64
        assert max(open_during_walk) - before < 70
        assert _open_descriptors() == before

    def test_a_folder_swapped_for_a_symlink_after_its_lstat_is_not_followed(self, share, tmp_path, monkeypatch):
        victim = _dir(share / "victim", 0o755)
        _file(victim / "a.txt", 0o644)
        outside = _dir(tmp_path / "outside", 0o755)
        secret = _file(outside / "secret.txt", 0o644)
        real_open = os.open
        swapped = []

        def fake_open(path, flags, mode=0o777, *, dir_fd=None):
            if path == "victim" and dir_fd is not None and not swapped:
                os.rename(victim, share / "victim.moved")
                os.symlink(outside, victim)
                swapped.append(flags)
            return real_open(path, flags, mode, dir_fd=dir_fd)

        monkeypatch.setattr(os, "open", fake_open)

        result = _repair(share)

        assert swapped == [os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC]
        assert result.skipped["raced"] == 1
        assert (_mode(outside), _mode(secret)) == (0o755, 0o644)
        assert os.path.islink(victim)
        assert _mode(share / "victim.moved") == 0o755  # renamed after its folder was listed: not reached

    def test_a_folder_swapped_for_another_folder_after_its_lstat_is_neither_changed_nor_entered(
        self, share, tmp_path, monkeypatch
    ):
        # The open succeeds (a real folder), but it is not the folder that was
        # examined: its (st_dev, st_ino) differ.
        victim = _dir(share / "victim", 0o755)
        other = _dir(tmp_path / "other", 0o755)
        inside = _file(other / "b.txt", 0o644)
        real_open = os.open
        swapped = []

        def fake_open(path, flags, mode=0o777, *, dir_fd=None):
            if path == "victim" and dir_fd is not None and not swapped:
                os.rename(victim, share / "victim.moved")
                os.rename(other, victim)
                swapped.append(flags)
            return real_open(path, flags, mode, dir_fd=dir_fd)

        monkeypatch.setattr(os, "open", fake_open)

        result = _repair(share)

        assert swapped == [os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC]
        assert result.skipped["raced"] == 1
        assert (_mode(victim), _mode(victim / inside.name)) == (0o755, 0o644)  # the other folder
        assert _mode(share / "victim.moved") == 0o755  # renamed after its folder was listed: not reached
        assert result.visited == 2

    def test_a_file_swapped_for_another_file_after_its_lstat_is_not_changed(self, share, tmp_path, monkeypatch):
        victim = _file(share / "victim.txt", 0o644)
        other = _file(tmp_path / "other.txt", 0o644)
        real_open = os.open
        swapped = []

        def fake_open(path, flags, mode=0o777, *, dir_fd=None):
            if path == "victim.txt" and dir_fd is not None and not swapped:
                # Renamed, not removed: the examined inode stays, so the new
                # file cannot get its number.
                os.rename(victim, share / "victim.old")
                os.rename(other, victim)
                swapped.append(flags)
            return real_open(path, flags, mode, dir_fd=dir_fd)

        monkeypatch.setattr(os, "open", fake_open)

        result = _repair(share)

        assert swapped == [os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC]
        assert result.skipped["raced"] == 1
        assert (_mode(victim), _mode(share / "victim.old")) == (0o644, 0o644)
        assert result.changed == 1  # the share's folder

    def test_a_file_hard_linked_after_its_lstat_is_not_changed(self, share, tmp_path, monkeypatch):
        # The lstat saw one link; the open finds the same inode with a second
        # link, which may be outside the share.
        victim = _file(share / "victim.txt", 0o644)
        outside = tmp_path / "outside-link"
        real_open = os.open
        linked = []

        def fake_open(path, flags, mode=0o777, *, dir_fd=None):
            if path == "victim.txt" and dir_fd is not None and not linked:
                os.link(victim, outside)
                linked.append(flags)
            return real_open(path, flags, mode, dir_fd=dir_fd)

        monkeypatch.setattr(os, "open", fake_open)

        result = _repair(share)

        assert linked == [os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC]
        assert result.skipped["raced"] == 1
        assert (_mode(victim), _mode(outside)) == (0o644, 0o644)
        assert result.changed == 1  # the share's folder

    def test_a_file_swapped_for_a_fifo_after_its_lstat_is_never_opened_for_reading(self, share, monkeypatch):
        victim = _file(share / "victim.txt", 0o644)
        real_open = os.open
        flags_used = []

        def fake_open(path, flags, mode=0o777, *, dir_fd=None):
            if path == "victim.txt" and dir_fd is not None and not flags_used:
                os.unlink(victim)
                os.mkfifo(victim)
                os.chmod(victim, 0o644)
                flags_used.append(flags)
            return real_open(path, flags, mode, dir_fd=dir_fd)

        monkeypatch.setattr(os, "open", fake_open)
        results = []
        # Opening a FIFO for reading blocks until a writer comes: run the walk
        # where a block cannot hang the test run.
        worker = threading.Thread(target=lambda: results.append(_repair(share)), daemon=True)
        worker.start()
        worker.join(30)

        assert not worker.is_alive(), "the walk blocked opening the FIFO"
        [result] = results
        assert flags_used == [os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC]
        assert result.skipped["raced"] == 1
        assert stat.S_ISFIFO(os.lstat(victim).st_mode) and _mode(victim) == 0o644

    def test_a_file_is_changed_through_its_proc_self_fd_entry(self, share, monkeypatch):
        target = _file(share / "doc.txt", 0o644)
        inode = os.lstat(target).st_ino
        other_gid = GID + 1  # this test cannot give it: chown and fchown are recorded, not run
        calls, fchowns = [], []
        real_chmod = os.chmod

        def chown(path, uid, gid, *args, **kwargs):
            calls.append(("chown", path, os.stat(path).st_ino, uid, gid))

        def chmod(path, mode, *args, **kwargs):
            calls.append(("chmod", path, os.stat(path).st_ino, mode))
            real_chmod(path, mode, *args, **kwargs)

        monkeypatch.setattr(os, "chown", chown)
        monkeypatch.setattr(os, "chmod", chmod)
        monkeypatch.setattr(os, "fchown", lambda fd, uid, gid: fchowns.append((os.fstat(fd).st_ino, uid, gid)))

        result = repair.repair_tree(str(share), gid=other_gid)

        assert [call[0] for call in calls] == ["chown", "chmod"]  # chown first: it can clear setgid
        for call in calls:
            assert call[1].startswith("/proc/self/fd/") and call[2] == inode
        assert calls[0][3:] == (-1, other_gid)
        assert calls[1][3] == 0o660
        assert fchowns == [(os.lstat(share).st_ino, -1, other_gid)]  # the share's folder, through its descriptor
        assert (_mode(target), _mode(share)) == (0o660, 0o2770)
        assert (result.changed, result.errors) == (2, 0)

    def test_a_second_run_changes_nothing(self, share):
        docs = _dir(share / "docs", 0o777)
        entries = [share, docs, _file(docs / "a.txt", 0o666), _file(share / "b.sh", 0o755)]

        first = _repair(share)
        ctimes = [os.lstat(path).st_ctime_ns for path in entries]
        second = _repair(share)

        assert (first.changed, second.changed) == (4, 0)
        assert (second.state, second.visited, second.errors) == ("done", first.visited, 0)
        assert [os.lstat(path).st_ctime_ns for path in entries] == ctimes  # nothing written

    @pytest.mark.parametrize("limits", [{"time_limit": 0}, {"max_entries": 1}])
    def test_a_limit_stops_the_walk_as_incomplete(self, share, limits):
        files = [_file(share / "a.txt", 0o644), _file(share / "b.txt", 0o644)]

        result = _repair(share, **limits)

        assert result.state == "incomplete"
        assert result.message == (
            "Stopped after 4 minutes; run it again to continue (already repaired entries are skipped quickly)"
        )
        assert result.visited == 1  # the share's folder
        assert [_mode(path) for path in files] == [0o644, 0o644]
        again = _repair(share)
        assert again.state == "done"
        assert [_mode(path) for path in files] == [0o660, 0o660]

    @pytest.mark.parametrize("code", [errno.EPERM, errno.EACCES, errno.EROFS, errno.EIO])
    def test_an_entry_that_cannot_be_changed_is_counted_and_the_walk_goes_on(self, share, monkeypatch, code):
        docs = _dir(share / "docs", 0o755)
        stuck = _file(docs / "stuck.txt", 0o644)
        fine = _file(share / "fine.txt", 0o644)
        stuck_inode = os.lstat(stuck).st_ino
        real_chmod = os.chmod

        def chmod(path, mode, *args, **kwargs):
            if os.stat(path).st_ino == stuck_inode:
                raise OSError(code, os.strerror(code), path)
            return real_chmod(path, mode, *args, **kwargs)

        monkeypatch.setattr(os, "chmod", chmod)

        result = _repair(share)

        assert result.state == "done"
        assert (result.errors, result.error_samples) == (1, [f"docs/stuck.txt: {os.strerror(code)}"])
        assert result.message == "Permissions repaired, except for 1 entry that could not be changed."
        assert (_mode(stuck), _mode(fine), _mode(docs)) == (0o644, 0o660, 0o2770)
        assert result.changed == 3

    def test_every_descriptor_is_closed(self, share, monkeypatch):
        for i in range(3):
            _file(_dir(share / f"d{i}") / "f.txt")
        before = _open_descriptors()

        assert _repair(share).state == "done"
        assert _repair(share, max_entries=3).state == "incomplete"
        assert _open_descriptors() == before

        real_lstat = os.lstat

        def lstat(path, *args, **kwargs):
            if path == "f.txt":
                raise RuntimeError("unexpected")
            return real_lstat(path, *args, **kwargs)

        monkeypatch.setattr(os, "lstat", lstat)
        with pytest.raises(RuntimeError):
            _repair(share)
        assert _open_descriptors() == before

    def test_a_share_folder_behind_a_symlink_fails_and_changes_nothing(self, share, base):
        data = _file(share / "a.txt", 0o644)
        real = base / "data" / "real"
        os.rename(share, real)
        os.symlink(real, share)
        before = _open_descriptors()

        result = _repair(share)

        assert result.state == "failed"
        assert result.message == (
            "The share's folder could not be opened: a part of its path is a symbolic link. Nothing was changed."
        )
        assert (result.visited, result.changed) == (0, 0)
        assert (_mode(real), _mode(real / "a.txt")) == (0o755, 0o644)
        assert data.is_file()  # through the link
        assert _open_descriptors() == before

    def test_a_pool_whose_folder_is_a_symlink_fails(self, share, base, tmp_path):
        data = _file(share / "a.txt", 0o644)
        moved = tmp_path / "moved-pool"
        os.rename(base / "data", moved)
        os.symlink(moved, base / "data")

        result = _repair(share)

        assert result.state == "failed"
        assert (_mode(moved / "media"), _mode(moved / "media" / "a.txt")) == (0o755, 0o644)
        assert data.is_file()

    def test_without_o_path_nothing_is_walked(self, share, monkeypatch):
        data = _file(share / "a.txt", 0o644)
        monkeypatch.delattr(os, "O_PATH")

        result = _repair(share)

        assert (result.state, result.visited) == ("failed", 0)
        assert result.message == "Repair permissions needs Linux (O_PATH). Nothing was changed."
        assert (_mode(share), _mode(data)) == (0o755, 0o644)

    @pytest.mark.parametrize("where", ["pool root", "pools base", "outside"])
    def test_a_folder_that_is_not_inside_a_pool_is_never_walked(self, base, tmp_path, where):
        folder = {"pool root": base / "data", "pools base": base, "outside": _dir(tmp_path / "outside")}[where]
        data = _file(folder / "a.txt", 0o644)
        os.chmod(folder, 0o755)

        result = _repair(folder)

        assert result.state == "failed"
        assert result.message == (
            "The share's folder could not be opened: it is not a folder inside a pool. Nothing was changed."
        )
        assert (_mode(folder), _mode(data)) == (0o755, 0o644)

    @pytest.mark.parametrize(
        "folder, reason",
        [
            ("apps", "it is app data (the apps folder of a pool), which is never repaired"),
            ("apps/sonarr", "it is app data (the apps folder of a pool), which is never repaired"),
            ("media/.snapshots", "it is in a snapshot folder (.snapshots), which is never repaired"),
            ("media/.snapshots/2026-11-06", "it is in a snapshot folder (.snapshots), which is never repaired"),
        ],
    )
    def test_app_data_and_snapshot_folders_are_never_walked(self, base, share, folder, reason):
        # service.share_problem() refuses them (rules 2 and 5 of share_path_problem);
        # repair_tree checks the path it walks again.
        target = base / "data" / folder
        target.mkdir(parents=True)
        os.chmod(target, 0o755)
        data = _file(target / "a.txt", 0o644)

        result = _repair(target)

        assert result.state == "failed"
        assert result.message == f"The share's folder could not be opened: {reason}. Nothing was changed."
        assert (result.visited, result.changed) == (0, 0)
        assert (_mode(target), _mode(data)) == (0o755, 0o644)


@pytest.fixture
def api(base, share, tmp_path, monkeypatch):
    """The repair router, as main.py includes it, on a temporary database with the share Media.

    service.share_problem, accounts.gid_problem and
    pools._mounted_pool_target are replaced by fakes that the test steers
    through ``checks``; repair_tree runs for real, with calls recorded.
    """
    path = tmp_path / "repair.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(SmbShare(name="Media", path=str(share)))
        session.commit()
    engine.dispose()
    maker = async_sessionmaker(
        create_async_engine(f"sqlite+aiosqlite:///{path}", poolclass=NullPool), expire_on_commit=False
    )

    async def override_get_db():
        async with maker() as session:
            yield session

    app = FastAPI()
    app.state.limiter = repair_router.limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(repair_router.router, prefix="/api/v1", dependencies=[Depends(require_admin)])
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[verify_token] = lambda: ADMIN

    checks = SimpleNamespace(
        problem=None, pool_error=None, gid_problem=None, share_ids=[], pool_calls=[], walks=[]
    )

    async def share_problem(db, share_id):
        checks.share_ids.append(share_id)
        if isinstance(checks.problem, Exception):
            raise checks.problem
        return checks.problem

    def mounted_pool_target(name, *, write=False):
        checks.pool_calls.append((name, write))
        if checks.pool_error is not None:
            raise checks.pool_error
        return str(base / name)

    real_repair_tree = repair.repair_tree

    def repair_tree(path, **kwargs):
        checks.walks.append((path, kwargs))
        return real_repair_tree(path, **kwargs)

    audit = tmp_path / "audit_log.json"
    monkeypatch.setattr("src.audit.router.AUDIT_FILE", str(audit))
    monkeypatch.setattr(constants, "SMB_GROUP_GID", GID)
    monkeypatch.setattr(service, "share_problem", share_problem)
    monkeypatch.setattr(accounts, "gid_problem", lambda: checks.gid_problem)
    monkeypatch.setattr(pools, "_mounted_pool_target", mounted_pool_target)
    monkeypatch.setattr(repair, "repair_tree", repair_tree)
    return SimpleNamespace(app=app, client=TestClient(app), checks=checks, audit=audit, share=share, base=base, db=path)


def _audit(api) -> list[dict]:
    return json.loads(api.audit.read_text()) if api.audit.exists() else []


def _sql(api, statement: str, *params) -> None:
    """Run and commit one statement on the test's database, as another request would."""
    conn = sqlite3.connect(api.db)
    try:
        conn.execute(statement, params)
        conn.commit()
    finally:
        conn.close()


def _nothing_walked(api, *files: Path) -> None:
    assert api.checks.walks == []
    assert _mode(api.share) == 0o755
    for path in files:
        assert _mode(path) == 0o644
    assert _audit(api) == []
    assert repair_router._last_results == {}


class TestEndpoints:
    def test_repair_answers_and_keeps_the_result_by_name_in_any_case(self, api):
        data = _file(api.share / "a.txt", 0o644)

        response = api.client.post(URL.format("media"))

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["share"] == "Media"
        assert (body["state"], body["visited"], body["changed"], body["errors"]) == ("done", 2, 2, 0)
        assert body["skipped"] == NOTHING_SKIPPED
        assert (_mode(api.share), _mode(data)) == (0o2770, 0o660)
        assert api.checks.share_ids == [1]
        assert api.checks.pool_calls == [("data", True)]
        assert api.checks.walks == [(str(api.share), {"gid": GID})]  # constants.SMB_GROUP_GID
        assert api.client.get(URL.format("MEDIA")).json() == body
        assert list(repair_router._last_results) == ["media"]

    def test_get_before_any_repair_is_404(self, api):
        response = api.client.get(URL.format("Media"))

        assert response.status_code == 404
        assert response.json()["detail"] == "No repair has run for this share since UNOS started"

    def test_an_unknown_share_is_404(self, api):
        data = _file(api.share / "a.txt", 0o644)

        for response in (api.client.post(URL.format("nosuch")), api.client.get(URL.format("nosuch"))):
            assert response.status_code == 404
            assert response.json()["detail"] == "Share not found"
        assert api.checks.share_ids == []
        _nothing_walked(api, data)

    def test_the_repair_is_audited(self, api):
        _file(api.share / "a.txt", 0o644)
        os.mkfifo(api.share / "pipe")

        api.client.post(URL.format("Media"))

        [entry] = _audit(api)
        assert entry["action"] == "share_updated"
        assert (entry["resource_type"], entry["resource_id"]) == ("share", "Media")
        assert entry["username"] == "admin"
        assert entry["details"] == "Repaired permissions: 2 changed, 1 skipped, 0 errors (done)"
        assert (entry["severity"], entry["success"]) == ("info", True)

    def test_a_share_that_is_not_served_is_refused(self, api):
        data = _file(api.share / "a.txt", 0o644)
        api.checks.problem = (
            "Share a folder inside the pool or a volume: the top level of a pool holds app data (apps/), "
            "volumes and snapshots"
        )

        response = api.client.post(URL.format("Media"))

        assert response.status_code == 409
        assert response.json()["detail"] == f"Share Media is not served: {api.checks.problem}; change its folder first"
        assert api.checks.pool_calls == []
        _nothing_walked(api, data)

    def test_an_unreadable_pool_list_is_503_and_nothing_is_walked(self, api):
        data = _file(api.share / "a.txt", 0o644)
        api.checks.problem = service.PoolListUnavailable("The pool list could not be read: /etc/fstab: Input/output error")

        response = api.client.post(URL.format("Media"))

        assert response.status_code == 503
        assert response.json()["detail"] == "The pool list could not be read: /etc/fstab: Input/output error"
        assert api.checks.pool_calls == []
        _nothing_walked(api, data)

    @pytest.mark.parametrize(
        "error",
        [
            pools.PoolConflictError("Pool 'data' is not mounted at /mnt/pools/data. Mount it on the Storage page first."),
            pools.PoolConflictError("Pool 'data' is mounted read-only; volumes cannot be created or deleted."),
            pools.PoolNotFoundError("Pool 'data' not found"),
        ],
    )
    def test_a_pool_that_is_not_mounted_writable_is_refused(self, api, error):
        data = _file(api.share / "a.txt", 0o644)
        api.checks.pool_error = error

        response = api.client.post(URL.format("Media"))

        assert response.status_code == 409
        assert response.json()["detail"] == f"The pool of share Media is not available: {error}"
        assert api.checks.pool_calls == [("data", True)]
        _nothing_walked(api, data)

    @pytest.mark.parametrize("where", ["pool", "parent"])
    def test_a_share_below_a_folder_that_others_can_change_is_refused(self, api, where):
        """Whoever can change a folder above the share's folder can swap in another folder (an
        app's data), which the walk would then give the SMB group (ADR-0153)."""
        data = _file(api.share / "a.txt", 0o644)
        folder = api.base / "data"
        if where == "parent":
            folder = _dir(folder / "team")
            nested = _dir(folder / "media")
            _sql(api, "UPDATE smb_shares SET path = ?", str(nested))
        os.chmod(folder, 0o777)

        response = api.client.post(URL.format("Media"))

        assert response.status_code == 409
        assert response.json()["detail"] == (
            f"Share folders must be inside folders that only root can change: {folder} can be changed by other "
            f"accounts (mode 0777). Run 'chmod 755 {folder}' on the NAS, then try again.")
        assert api.checks.pool_calls == [("data", True)]
        _nothing_walked(api, data)
        assert _mode(folder) == 0o777  # no mode is changed

    def test_a_gid_problem_is_refused(self, api):
        data = _file(api.share / "a.txt", 0o644)
        api.checks.gid_problem = (
            "gid 70000 belongs to the host group media, not unos-smb: share folders would be opened to "
            "media's members. Remove or renumber media on the host"
        )

        response = api.client.post(URL.format("Media"))

        assert response.status_code == 409
        assert response.json()["detail"] == api.checks.gid_problem
        _nothing_walked(api, data)

    @pytest.mark.parametrize("failing", ["share_problem", "mounted_pool_target", "gid_problem"])
    def test_a_check_that_fails_is_500_and_nothing_is_walked(self, api, monkeypatch, failing):
        data = _file(api.share / "a.txt", 0o644)

        def fail(*args, **kwargs):
            raise RuntimeError("Could not find out which subvolume of the pool is mounted at /mnt/pools/data: busy")

        async def fail_async(*args, **kwargs):
            fail()

        target = {"share_problem": (service, fail_async), "mounted_pool_target": (pools, fail), "gid_problem": (accounts, fail)}
        module, replacement = target[failing]
        monkeypatch.setattr(module, "_mounted_pool_target" if failing == "mounted_pool_target" else failing, replacement)

        response = api.client.post(URL.format("Media"))

        assert response.status_code == 500
        assert response.json()["detail"] == (
            "Could not find out which subvolume of the pool is mounted at /mnt/pools/data: busy"
        )
        _nothing_walked(api, data)

    def test_a_folder_outside_the_pools_is_never_walked_whatever_share_problem_says(self, api):
        _sql(api, "UPDATE smb_shares SET path = '/etc'")

        response = api.client.post(URL.format("Media"))

        assert response.status_code == 409
        assert response.json()["detail"] == (
            "Share Media is not served: its folder is not inside a pool; change its folder first"
        )
        assert api.checks.pool_calls == []
        _nothing_walked(api)

    @pytest.mark.parametrize(
        ("change", "found"),
        [
            pytest.param("path", None, id="path"),
            # The check read the row before the PATCH: it found the app data of the first read.
            pytest.param(
                "path", "App data (data/apps) cannot be shared: apps keep their configuration there", id="path-found"
            ),
            pytest.param("name", None, id="name"),
            pytest.param("deleted", None, id="deleted"),
            # The share was deleted before the check read the row: service.share_problem() answers this.
            pytest.param("deleted", "The share does not exist", id="deleted-found"),
        ],
    )
    def test_a_share_changed_while_it_was_checked_is_never_walked(self, api, monkeypatch, change, found):
        # service.share_problem() reads the share's row again, apart from the
        # router's own read, and a repair does not take smb_lock: a PATCH, a
        # restore or a delete can commit before or after that read. The folder
        # of the first read was never checked; for "path" it is app data of an
        # imported share that is not served. The answer does not depend on when
        # the change committed, nor on what the check found in the changed row:
        # 404 for a deleted share, 409 "changed" otherwise, never "not served".
        app = _dir(_dir(api.base / "data" / "apps") / "sonarr")
        config = _file(app / "config.xml", 0o644)
        data = _file(api.share / "a.txt", 0o644)
        if change == "path":
            _sql(api, "UPDATE smb_shares SET path = ?", str(app))
        statement = {
            "path": ("UPDATE smb_shares SET path = ?", str(api.share)),
            "name": ("UPDATE smb_shares SET name = ?", "Films"),
            "deleted": ("DELETE FROM smb_shares",),
        }[change]

        async def share_problem(db, share_id):
            api.checks.share_ids.append(share_id)
            _sql(api, *statement)  # commits right before or after the check read the row
            return found

        monkeypatch.setattr(service, "share_problem", share_problem)

        response = api.client.post(URL.format("Media"))

        if change == "deleted":
            assert response.status_code == 404
            assert response.json()["detail"] == "Share not found"
        else:
            assert response.status_code == 409
            assert response.json()["detail"] == "The share was changed meanwhile; reload"
        assert api.checks.share_ids == [1]
        assert api.checks.pool_calls == []
        _nothing_walked(api, data)
        assert (_mode(app), _mode(config)) == (0o755, 0o644)

    def test_a_second_repair_while_one_runs_is_refused(self, api, monkeypatch):
        data = _file(api.share / "a.txt", 0o644)
        started, release = threading.Event(), threading.Event()
        recording_repair_tree = repair.repair_tree

        def slow_repair_tree(path, **kwargs):
            started.set()
            assert release.wait(60)
            return recording_repair_tree(path, **kwargs)

        monkeypatch.setattr(repair, "repair_tree", slow_repair_tree)
        first = []
        worker = threading.Thread(target=lambda: first.append(TestClient(api.app).post(URL.format("Media"))))
        worker.start()
        try:
            assert started.wait(60)
            second = api.client.post(URL.format("Media"))
        finally:
            release.set()
            worker.join(60)

        assert second.status_code == 409
        assert second.json()["detail"] == "A permissions repair is already running"
        assert [response.status_code for response in first] == [200]
        assert len(api.checks.walks) == 1
        assert _mode(data) == 0o660
        assert len(_audit(api)) == 1
        # The lock is free again.
        assert api.client.post(URL.format("Media")).status_code == 200

    def test_a_folder_swapped_for_a_symlink_after_the_checks_fails_and_changes_nothing(self, api, tmp_path, monkeypatch):
        data = _file(api.share / "a.txt", 0o644)
        elsewhere = _dir(tmp_path / "elsewhere", 0o755)
        secret = _file(elsewhere / "secret.txt", 0o644)

        def gid_problem():  # the last check: the folder is swapped right after it
            os.rename(api.share, api.base / "data" / "moved")
            os.symlink(elsewhere, api.share)
            return None

        monkeypatch.setattr(accounts, "gid_problem", gid_problem)

        response = api.client.post(URL.format("Media"))

        assert response.status_code == 200
        body = response.json()
        assert (body["state"], body["visited"], body["changed"]) == ("failed", 0, 0)
        assert (_mode(elsewhere), _mode(secret)) == (0o755, 0o644)
        moved = api.base / "data" / "moved"
        assert (_mode(moved), _mode(moved / data.name)) == (0o755, 0o644)
        [entry] = _audit(api)
        assert entry["details"] == "Repaired permissions: 0 changed, 0 skipped, 0 errors (failed)"
        assert entry["success"] is False

    def test_at_most_six_repairs_a_minute(self, api):
        for _ in range(6):
            assert api.client.post(URL.format("nosuch")).status_code == 404

        assert api.client.post(URL.format("nosuch")).status_code == 429

    def test_regular_users_are_refused(self, api):
        data = _file(api.share / "a.txt", 0o644)
        api.app.dependency_overrides[verify_token] = lambda: REGULAR

        assert api.client.post(URL.format("Media")).status_code == 403
        assert api.client.get(URL.format("Media")).status_code == 403
        _nothing_walked(api, data)

    def test_the_endpoints_require_an_administrator_without_the_router_dependency(self, api):
        # Defense in depth: main.py adds require_admin to the whole router;
        # the endpoints also depend on it themselves.
        app = FastAPI()
        app.include_router(repair_router.router, prefix="/api/v1")
        app.dependency_overrides.update(api.app.dependency_overrides)
        app.dependency_overrides[verify_token] = lambda: REGULAR
        client = TestClient(app)

        assert client.post(URL.format("Media")).status_code == 403
        assert client.get(URL.format("Media")).status_code == 403
        assert api.checks.walks == []
