"""Security tests for the file manager API (path traversal, symlinks, uploads)."""

import errno
import importlib
import io
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.auth.utils import verify_token
from src.main import app

# The package re-exports its APIRouter as ``router``; import the module itself.
files_router = importlib.import_module("src.files.router")

ADMIN = SimpleNamespace(id=1, username="admin", is_admin=True)
USER = SimpleNamespace(id=2, username="user", is_admin=False)


def _escaped(path) -> str:
    """A mount point as the kernel writes it in /proc/self/mountinfo."""
    escapes = {"\\": "\\134", " ": "\\040", "\t": "\\011", "\n": "\\012"}
    return "".join(escapes.get(c, c) for c in str(path))


class _MountTable:
    """A stand-in for /proc/self/mountinfo (files_router.MOUNTINFO).

    It lists the container's usual mounts and the pool's mount point, all at
    or above the folders the tests work on.
    """

    def __init__(self, path, pool):
        self.path = path
        path.parent.mkdir()
        path.write_text(
            "25 1 0:25 / / rw,relatime master:1 - overlay overlay rw,lowerdir=/var/lib/docker/overlay2/l/A\n"
            "26 25 0:26 / /proc rw,nosuid,nodev,noexec,relatime - proc proc rw\n"
            "27 25 0:27 / /dev rw,nosuid - tmpfs tmpfs rw,size=65536k,mode=755\n"
            "28 25 179:2 /mnt /mnt rw,relatime shared:1 - ext4 /dev/mmcblk0p2 rw\n"
            f"29 28 0:40 / {_escaped(pool)} rw,relatime shared:30 - btrfs /dev/sda rw,subvolid=5,subvol=/\n"
        )

    def mount(self, mount_point, root="/"):
        """Add a mount (a bind mount of ``root`` of the pool's filesystem, by default its top)."""
        number = len(self.path.read_text().splitlines()) + 40
        with self.path.open("a") as table:
            table.write(f"{number} 29 0:40 {_escaped(root)} {_escaped(mount_point)} rw,relatime - btrfs /dev/sda rw\n")


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A pools base with one pool, a sibling directory and an outside directory."""
    root = tmp_path.resolve()
    base = root / "pools"
    pool = base / "data"
    pool.mkdir(parents=True)
    (root / "pools-old").mkdir()
    (root / "pools-old" / "secret.txt").write_text("sibling")
    outside = root / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("host secret")
    mounts = _MountTable(root / "proc" / "mountinfo", pool)

    monkeypatch.setattr(files_router, "BASE_PATH", str(base))
    monkeypatch.setattr(files_router, "MOUNTINFO", str(mounts.path))
    app.dependency_overrides[verify_token] = lambda: ADMIN
    client = TestClient(app)
    yield SimpleNamespace(client=client, root=root, base=base, pool=pool, outside=outside, mounts=mounts)
    app.dependency_overrides.pop(verify_token, None)


def _upload(client, directory, filename, content=b"payload"):
    return client.post(
        "/api/v1/files/upload",
        params={"path": str(directory)},
        files={"file": (filename, io.BytesIO(content), "application/octet-stream")},
    )


def _tree(path):
    return sorted(p.relative_to(path).as_posix() for p in path.rglob("*"))


# --- access control ---------------------------------------------------------


def test_non_admin_is_rejected(env):
    app.dependency_overrides[verify_token] = lambda: USER
    response = env.client.get("/api/v1/files/list", params={"path": str(env.pool)})
    assert response.status_code == 403


# --- path confinement ---------------------------------------------------------


@pytest.mark.parametrize(
    "path_template",
    [
        "{root}/pools-old",  # shares the base's string prefix
        "{root}/poolsX",
        "{root}/outside",
        "/etc",
        "{base}/data/../../outside",
        "{base}/..",
        "relative/path",
    ],
)
def test_list_refuses_paths_outside_base(env, path_template):
    path = path_template.format(root=env.root, base=env.base)
    response = env.client.get("/api/v1/files/list", params={"path": path})
    assert response.status_code in (400, 403)


def test_list_base_and_pool(env):
    (env.pool / "a.txt").write_text("hello")
    (env.pool / "sub").mkdir()

    response = env.client.get("/api/v1/files/list")
    assert response.status_code == 200
    body = response.json()
    assert body["path"] == str(env.base)
    assert body["parent"] is None
    assert [item["name"] for item in body["items"]] == ["data"]

    response = env.client.get("/api/v1/files/list", params={"path": str(env.pool)})
    body = response.json()
    assert body["parent"] == str(env.base)
    assert [(i["name"], i["type"]) for i in body["items"]] == [("sub", "directory"), ("a.txt", "file")]
    assert body["total_size"] == 5


def test_list_shows_symlinks_without_following_them(env):
    os.symlink(env.outside / "secret.txt", env.pool / "link")
    response = env.client.get("/api/v1/files/list", params={"path": str(env.pool)})
    assert response.status_code == 200
    (item,) = response.json()["items"]
    assert item["type"] == "symlink"
    assert item["size"] == 0
    assert item["permissions"].startswith("l")


def test_symlinked_directory_is_not_traversed(env):
    os.symlink(env.outside, env.pool / "escape")
    for path in (env.pool / "escape", env.pool / "escape" / "secret.txt"):
        response = env.client.get("/api/v1/files/list", params={"path": str(path)})
        assert response.status_code == 403
    response = env.client.get(
        "/api/v1/files/download", params={"path": str(env.pool / "escape" / "secret.txt")}
    )
    assert response.status_code == 403


def test_info_describes_symlink_itself(env):
    os.symlink(env.outside / "secret.txt", env.pool / "link")
    response = env.client.get("/api/v1/files/info", params={"path": str(env.pool / "link")})
    assert response.status_code == 200
    assert response.json()["type"] == "symlink"


# --- download -----------------------------------------------------------------


def test_download_regular_file(env):
    (env.pool / "report.txt").write_bytes(b"x" * 3000)
    response = env.client.get("/api/v1/files/download", params={"path": str(env.pool / "report.txt")})
    assert response.status_code == 200
    assert response.content == b"x" * 3000
    assert response.headers["content-disposition"] == 'attachment; filename="report.txt"'


def test_download_does_not_follow_symlink(env):
    os.symlink(env.outside / "secret.txt", env.pool / "link")
    response = env.client.get("/api/v1/files/download", params={"path": str(env.pool / "link")})
    assert response.status_code == 403
    assert b"host secret" not in response.content


def test_download_directory_is_rejected(env):
    response = env.client.get("/api/v1/files/download", params={"path": str(env.pool)})
    assert response.status_code == 400


def test_download_quotes_unusual_names(env):
    (env.pool / 'a"b.txt').write_text("q")
    response = env.client.get("/api/v1/files/download", params={"path": str(env.pool / 'a"b.txt')})
    assert response.status_code == 200
    assert response.headers["content-disposition"] == "attachment; filename*=utf-8''a%22b.txt"


# --- upload -------------------------------------------------------------------


@pytest.mark.parametrize(
    "filename,stored",
    [
        ("../../outside/evil.txt", "evil.txt"),
        ("/etc/cron.d/evil", "evil"),
        ("..\\..\\evil.bat", "evil.bat"),
        ("C:\\Users\\me\\report.txt", "report.txt"),
        ("plain.txt", "plain.txt"),
    ],
)
def test_upload_uses_only_the_base_name(env, filename, stored):
    response = _upload(env.client, env.pool, filename)
    assert response.status_code == 200, response.text
    assert response.json()["name"] == stored
    assert (env.pool / stored).read_bytes() == b"payload"
    assert _tree(env.outside) == ["secret.txt"]
    assert (env.outside / "secret.txt").read_text() == "host secret"


@pytest.mark.parametrize("filename", ["..", ".", "a/..", "dir/"])
def test_upload_rejects_unusable_names(env, filename):
    response = _upload(env.client, env.pool, filename)
    assert response.status_code in (400, 422)
    assert _tree(env.pool) == []


@pytest.mark.parametrize("name", ["", ".", "..", "a\nb", "a\x00b", "tab\there", "x" * 256])
def test_upload_name_validation(name):
    with pytest.raises(Exception) as exc:
        files_router._upload_name(name)
    assert getattr(exc.value, "status_code", None) == 400


def test_upload_replaces_symlink_instead_of_writing_through_it(env):
    os.symlink(env.outside / "secret.txt", env.pool / "target.txt")
    response = _upload(env.client, env.pool, "target.txt", b"new content")
    assert response.status_code == 200
    assert not (env.pool / "target.txt").is_symlink()
    assert (env.pool / "target.txt").read_bytes() == b"new content"
    assert (env.outside / "secret.txt").read_text() == "host secret"


def test_upload_into_symlinked_directory_is_refused(env):
    os.symlink(env.outside, env.pool / "escape")
    response = _upload(env.client, env.pool / "escape", "evil.txt")
    assert response.status_code == 403
    assert _tree(env.outside) == ["secret.txt"]


def test_upload_outside_base_is_refused(env):
    response = _upload(env.client, env.outside, "evil.txt")
    assert response.status_code == 403
    assert _tree(env.outside) == ["secret.txt"]


def test_upload_into_base_itself_is_refused(env):
    response = _upload(env.client, env.base, "evil.txt")
    assert response.status_code == 400
    assert not (env.base / "evil.txt").exists()


def test_upload_overwrites_existing_file_and_streams_large_content(env):
    (env.pool / "big.bin").write_bytes(b"old")
    content = os.urandom(3 * 1024 * 1024 + 17)
    response = _upload(env.client, env.pool, "big.bin", content)
    assert response.status_code == 200
    assert response.json()["size"] == len(content)
    assert (env.pool / "big.bin").read_bytes() == content
    # No temporary files are left behind.
    assert _tree(env.pool) == ["big.bin"]


def test_upload_does_not_replace_directory(env):
    (env.pool / "folder").mkdir()
    response = _upload(env.client, env.pool, "folder")
    assert response.status_code == 400
    assert (env.pool / "folder").is_dir()
    assert _tree(env.pool) == ["folder"]


# --- mkdir / rename -----------------------------------------------------------


def test_mkdir(env):
    response = env.client.post("/api/v1/files/mkdir", json={"path": str(env.pool), "name": "new"})
    assert response.status_code == 200
    assert (env.pool / "new").is_dir()

    response = env.client.post("/api/v1/files/mkdir", json={"path": str(env.pool), "name": "new"})
    assert response.status_code == 400


@pytest.mark.parametrize("name", ["../evil", "..", ".", "a/b", "", "bad\nname"])
def test_mkdir_rejects_bad_names(env, name):
    response = env.client.post("/api/v1/files/mkdir", json={"path": str(env.pool), "name": name})
    assert response.status_code == 400
    assert _tree(env.pool) == []
    assert _tree(env.outside) == ["secret.txt"]


def test_mkdir_in_base_or_outside_is_refused(env):
    response = env.client.post("/api/v1/files/mkdir", json={"path": str(env.base), "name": "fake"})
    assert response.status_code == 400
    response = env.client.post("/api/v1/files/mkdir", json={"path": str(env.outside), "name": "x"})
    assert response.status_code == 403
    assert not (env.base / "fake").exists()
    assert _tree(env.outside) == ["secret.txt"]


def test_rename_refuses_base_and_pool_root(env):
    for path in (env.base, env.pool):
        response = env.client.post("/api/v1/files/rename", json={"path": str(path), "new_name": "x"})
        assert response.status_code == 403
    assert env.pool.is_dir()


@pytest.mark.parametrize("new_name", ["../../outside/x", "..", "a/b", ""])
def test_rename_rejects_bad_names(env, new_name):
    (env.pool / "file.txt").write_text("data")
    response = env.client.post(
        "/api/v1/files/rename", json={"path": str(env.pool / "file.txt"), "new_name": new_name}
    )
    assert response.status_code == 400
    assert (env.pool / "file.txt").exists()


def test_rename_symlink_renames_the_link(env):
    os.symlink(env.outside, env.pool / "link")
    response = env.client.post(
        "/api/v1/files/rename", json={"path": str(env.pool / "link"), "new_name": "renamed"}
    )
    assert response.status_code == 200
    assert (env.pool / "renamed").is_symlink()
    assert env.outside.is_dir() and _tree(env.outside) == ["secret.txt"]


def test_rename_existing_target_is_refused(env):
    (env.pool / "a").write_text("a")
    (env.pool / "b").write_text("b")
    response = env.client.post("/api/v1/files/rename", json={"path": str(env.pool / "a"), "new_name": "b"})
    assert response.status_code == 400
    assert (env.pool / "b").read_text() == "b"


# --- delete ---------------------------------------------------------------------


def test_delete_refuses_base_and_pool_root(env):
    (env.pool / "keep.txt").write_text("keep")
    for path in (env.base, env.pool, f"{env.pool}/", f"{env.base}/./data"):
        response = env.client.request("DELETE", "/api/v1/files/delete", json=[str(path)])
        assert response.status_code == 403
    assert (env.pool / "keep.txt").exists()


def test_delete_symlink_removes_only_the_link(env):
    os.symlink(env.outside, env.pool / "link")
    response = env.client.request("DELETE", "/api/v1/files/delete", json=[str(env.pool / "link")])
    assert response.status_code == 200
    assert not os.path.lexists(env.pool / "link")
    assert _tree(env.outside) == ["secret.txt"]


def test_delete_through_symlinked_parent_is_refused(env):
    os.symlink(env.outside, env.pool / "link")
    response = env.client.request(
        "DELETE", "/api/v1/files/delete", json=[str(env.pool / "link" / "secret.txt")]
    )
    assert response.status_code == 403
    assert (env.outside / "secret.txt").exists()


def test_delete_outside_base_is_refused(env):
    for path in (env.outside / "secret.txt", env.root / "pools-old" / "secret.txt"):
        response = env.client.request("DELETE", "/api/v1/files/delete", json=[str(path)])
        assert response.status_code == 403
    assert (env.outside / "secret.txt").exists()
    assert (env.root / "pools-old" / "secret.txt").exists()


def test_delete_files_and_directories(env):
    (env.pool / "dir" / "nested").mkdir(parents=True)
    (env.pool / "dir" / "nested" / "f").write_text("x")
    os.symlink(env.outside, env.pool / "dir" / "nested" / "escape")
    (env.pool / "file.txt").write_text("x")
    paths = [str(env.pool / "dir"), str(env.pool / "file.txt"), str(env.pool / "missing")]
    response = env.client.request("DELETE", "/api/v1/files/delete", json=paths)
    assert response.status_code == 200
    assert response.json()["deleted"] == paths[:2]
    assert _tree(env.pool) == []
    assert _tree(env.outside) == ["secret.txt"]


def test_delete_validates_every_path_before_deleting(env):
    (env.pool / "file.txt").write_text("x")
    response = env.client.request(
        "DELETE", "/api/v1/files/delete", json=[str(env.pool / "file.txt"), str(env.pool)]
    )
    assert response.status_code == 403
    assert (env.pool / "file.txt").exists()


# --- copy / move ------------------------------------------------------------------


def _copy(client, sources, destination):
    return client.post("/api/v1/files/copy", params={"destination": str(destination)}, json=[str(s) for s in sources])


def _move(client, sources, destination):
    return client.post("/api/v1/files/move", params={"destination": str(destination)}, json=[str(s) for s in sources])


def test_copy_keeps_symlinks_as_links(env):
    src = env.pool / "src"
    src.mkdir()
    (src / "file.txt").write_text("content")
    os.symlink(env.outside, src / "escape")
    os.symlink(env.outside / "secret.txt", src / "secret-link")
    (env.pool / "dst").mkdir()

    response = _copy(env.client, [src], env.pool / "dst")
    assert response.status_code == 200, response.text
    copied = env.pool / "dst" / "src"
    assert response.json()["copied"] == [str(copied)]
    assert (copied / "file.txt").read_text() == "content"
    assert (copied / "escape").is_symlink()
    assert os.readlink(copied / "escape") == str(env.outside)
    assert (copied / "secret-link").is_symlink()
    # The outside tree was not copied into the pool.
    assert not (copied / "escape").resolve().is_relative_to(env.base)
    assert sorted(p.name for p in copied.iterdir()) == ["escape", "file.txt", "secret-link"]


def test_copy_symlink_source_copies_the_link(env):
    os.symlink(env.outside / "secret.txt", env.pool / "link")
    (env.pool / "dst").mkdir()
    response = _copy(env.client, [env.pool / "link"], env.pool / "dst")
    assert response.status_code == 200
    assert (env.pool / "dst" / "link").is_symlink()


def test_copy_drops_setuid_bits(env):
    tool = env.pool / "tool"
    tool.write_bytes(b"#!/bin/sh\n")
    os.chmod(tool, 0o4755)
    (env.pool / "dst").mkdir()
    response = _copy(env.client, [tool], env.pool / "dst")
    assert response.status_code == 200
    mode = stat.S_IMODE(os.stat(env.pool / "dst" / "tool").st_mode)
    assert mode == 0o755


def test_copy_refuses_existing_destination_and_symlink_destination(env):
    (env.pool / "a.txt").write_text("new")
    (env.pool / "dst").mkdir()
    os.symlink(env.outside / "secret.txt", env.pool / "dst" / "a.txt")
    response = _copy(env.client, [env.pool / "a.txt"], env.pool / "dst")
    assert response.status_code == 400
    assert (env.outside / "secret.txt").read_text() == "host secret"


def test_copy_into_itself_is_refused(env):
    (env.pool / "dir" / "sub").mkdir(parents=True)
    response = _copy(env.client, [env.pool / "dir"], env.pool / "dir" / "sub")
    assert response.status_code == 400


def test_copy_from_or_to_outside_is_refused(env):
    (env.pool / "dst").mkdir()
    response = _copy(env.client, [env.outside / "secret.txt"], env.pool / "dst")
    assert response.status_code == 403
    (env.pool / "a.txt").write_text("a")
    response = _copy(env.client, [env.pool / "a.txt"], env.outside)
    assert response.status_code == 403
    assert _tree(env.pool / "dst") == []
    assert _tree(env.outside) == ["secret.txt"]


def test_copy_into_base_is_refused(env):
    (env.pool / "a.txt").write_text("a")
    response = _copy(env.client, [env.pool / "a.txt"], env.base)
    assert response.status_code == 400


def test_move(env):
    (env.pool / "a.txt").write_text("a")
    (env.pool / "dst").mkdir()
    response = _move(env.client, [env.pool / "a.txt"], env.pool / "dst")
    assert response.status_code == 200
    assert response.json()["moved"] == [str(env.pool / "dst" / "a.txt")]
    assert (env.pool / "dst" / "a.txt").read_text() == "a"
    assert not (env.pool / "a.txt").exists()


def test_move_refuses_pool_root_base_and_self(env):
    other = env.base / "other"
    other.mkdir()
    response = _move(env.client, [env.pool], other)
    assert response.status_code == 403
    response = _move(env.client, [env.base], other)
    assert response.status_code == 403
    (env.pool / "dir" / "sub").mkdir(parents=True)
    response = _move(env.client, [env.pool / "dir"], env.pool / "dir" / "sub")
    assert response.status_code == 400
    assert env.pool.is_dir() and (env.pool / "dir" / "sub").is_dir()


def test_move_across_filesystems_falls_back_to_copy(env, monkeypatch):
    (env.pool / "dir").mkdir()
    (env.pool / "dir" / "f.txt").write_text("f")
    (env.pool / "dst").mkdir()
    real_rename = os.rename

    def cross_device_rename(src, dst, *, src_dir_fd=None, dst_dir_fd=None):
        if src == "dir":
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real_rename(src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

    monkeypatch.setattr(files_router.os, "rename", cross_device_rename)
    response = _move(env.client, [env.pool / "dir"], env.pool / "dst")
    assert response.status_code == 200, response.text
    assert (env.pool / "dst" / "dir" / "f.txt").read_text() == "f"
    assert not (env.pool / "dir").exists()


# --- volumes, other subvolumes and mount points -------------------------------------


class _StatWithDevice:
    """An lstat() result with another st_dev."""

    def __init__(self, st, st_dev):
        self._st = st
        self.st_dev = st_dev

    def __getattr__(self, name):
        return getattr(self._st, name)


class _OsWithSubvolumes:
    """The files router's os module, in which the given directories are subvolume roots.

    There is no btrfs here: stat() and fstat() of such a directory, and of
    everything in it, report another st_dev than its parent's, as btrfs gives
    every subvolume its own (a mounted filesystem has its own too). The
    trees are taken when the proxy is made. Everything else is the real os
    module.
    """

    def __init__(self, roots):
        self._devices = {}
        # Outer roots first: a subvolume in another one keeps its own device.
        for number, root in enumerate(sorted(roots, key=lambda root: len(Path(root).parts)), start=1):
            for path in _entries(root):
                st = os.lstat(path)
                self._devices[(st.st_dev, st.st_ino)] = st.st_dev + number

    def __getattr__(self, name):
        return getattr(os, name)

    def _device(self, st):
        device = self._devices.get((st.st_dev, st.st_ino))
        return st if device is None else _StatWithDevice(st, device)

    def stat(self, path, *args, **kwargs):
        return self._device(os.stat(path, *args, **kwargs))

    def fstat(self, fd):
        return self._device(os.fstat(fd))


def _entries(root):
    """``root`` and everything below it; symlinks are not followed."""
    yield root
    for directory, dirs, files in os.walk(root):
        for name in dirs + files:
            yield os.path.join(directory, name)


VOLUME_TREE = ["photos", "photos/album", "photos/album/img.jpg"]


@pytest.fixture
def volume(env, monkeypatch):
    """<pool>/photos is a volume (a subvolume directly in the pool's top folder)."""
    photos = env.pool / "photos"
    (photos / "album").mkdir(parents=True)
    (photos / "album" / "img.jpg").write_bytes(b"jpeg")
    monkeypatch.setattr(files_router, "os", _OsWithSubvolumes([photos]))
    return photos


def test_delete_refuses_a_volume_and_deletes_nothing(env, volume):
    (env.pool / "notes.txt").write_text("x")

    response = env.client.request(
        "DELETE", "/api/v1/files/delete", json=[str(env.pool / "notes.txt"), str(volume)]
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "'photos' is a volume and cannot be deleted in the file manager. "
        "Volumes are deleted on the Storage page (Volumes)."
    )
    assert _tree(env.pool) == ["notes.txt", *VOLUME_TREE]


def test_rename_refuses_a_volume(env, volume):
    response = env.client.post("/api/v1/files/rename", json={"path": str(volume), "new_name": "pictures"})

    assert response.status_code == 409
    assert response.json()["detail"].startswith("'photos' is a volume and cannot be renamed in the file manager.")
    assert _tree(env.pool) == VOLUME_TREE


def test_move_refuses_a_volume_and_moves_nothing(env, volume):
    (env.pool / "a.txt").write_text("a")
    (env.pool / "archive").mkdir()

    response = _move(env.client, [env.pool / "a.txt", volume], env.pool / "archive")

    assert response.status_code == 409
    assert response.json()["detail"].startswith("'photos' is a volume and cannot be moved in the file manager.")
    assert _tree(env.pool) == ["a.txt", "archive", *VOLUME_TREE]


def test_a_volume_can_be_copied_and_its_content_changed(env, volume):
    (env.pool / "copies").mkdir()
    response = _copy(env.client, [volume], env.pool / "copies")
    assert response.status_code == 200, response.text
    assert (env.pool / "copies" / "photos" / "album" / "img.jpg").read_bytes() == b"jpeg"

    # Folders and files inside the volume are ordinary entries.
    response = env.client.post(
        "/api/v1/files/rename", json={"path": str(volume / "album"), "new_name": "2024"}
    )
    assert response.status_code == 200, response.text
    response = _move(env.client, [volume / "2024" / "img.jpg"], volume)
    assert response.status_code == 200, response.text
    response = env.client.request("DELETE", "/api/v1/files/delete", json=[str(volume / "2024")])
    assert response.status_code == 200, response.text
    assert _tree(volume) == ["img.jpg"]


def test_a_symlink_to_a_volume_is_an_ordinary_entry(env, volume):
    os.symlink(volume, env.pool / "link")
    response = env.client.request("DELETE", "/api/v1/files/delete", json=[str(env.pool / "link")])
    assert response.status_code == 200
    assert _tree(env.pool) == VOLUME_TREE


def test_deeper_subvolumes_and_mount_points_are_refused(env, monkeypatch):
    """E.g. a snapshot in .snapshots, a subvolume an app made, or a mount point below a pool."""
    nested = env.pool / "projects" / "2024"
    (nested / "plan.txt").parent.mkdir(parents=True)
    (nested / "plan.txt").write_text("plan")
    (env.pool / "archive").mkdir()
    monkeypatch.setattr(files_router, "os", _OsWithSubvolumes([nested]))
    detail = (
        "'2024' is a separate subvolume or filesystem (such as a snapshot or a mount point) "
        "and cannot be {} in the file manager."
    )

    response = env.client.request("DELETE", "/api/v1/files/delete", json=[str(nested)])
    assert (response.status_code, response.json()["detail"]) == (409, detail.format("deleted"))
    response = env.client.post("/api/v1/files/rename", json={"path": str(nested), "new_name": "2025"})
    assert (response.status_code, response.json()["detail"]) == (409, detail.format("renamed"))
    response = _move(env.client, [nested], env.pool / "archive")
    assert (response.status_code, response.json()["detail"]) == (409, detail.format("moved"))
    assert _tree(env.pool) == ["archive", "projects", "projects/2024", "projects/2024/plan.txt"]

    response = _copy(env.client, [nested], env.pool / "archive")
    assert response.status_code == 200, response.text
    assert (env.pool / "archive" / "2024" / "plan.txt").read_text() == "plan"


# --- folders that contain subvolumes or mount points ---------------------------------
#
# shutil.rmtree, and the copy and delete of a move to another filesystem, go
# into subvolumes and mounted filesystems below a folder. Deleting the folder,
# or moving it to another pool or volume, is refused instead.

CONTAINS = (
    "'{}' contains '{}', a separate subvolume or filesystem (such as a snapshot or a mount point), "
    "and cannot be {} in the file manager."
)
PROJECTS_TREE = [
    "projects",
    "projects/2024",
    "projects/2024/notes.txt",
    "projects/2024/snapshot",
    "projects/2024/snapshot/docs",
    "projects/2024/snapshot/docs/plan.txt",
]


def _delete(client, paths):
    return client.request("DELETE", "/api/v1/files/delete", json=[str(p) for p in paths])


@pytest.fixture
def projects(env, monkeypatch):
    """<pool>/projects/2024/snapshot is a subvolume (e.g. a snapshot an app made) in ordinary folders."""
    projects = env.pool / "projects"
    snapshot = projects / "2024" / "snapshot"
    (snapshot / "docs").mkdir(parents=True)
    (snapshot / "docs" / "plan.txt").write_text("plan")
    (projects / "2024" / "notes.txt").write_text("notes")
    monkeypatch.setattr(files_router, "os", _OsWithSubvolumes([snapshot]))
    return projects


def test_delete_refuses_a_folder_that_contains_a_subvolume_and_deletes_nothing(env, projects):
    (env.pool / "a.txt").write_text("a")
    (env.pool / "old" / "drafts").mkdir(parents=True)

    response = _delete(env.client, [env.pool / "a.txt", env.pool / "old", projects])
    assert response.status_code == 409
    assert response.json()["detail"] == CONTAINS.format("projects", "2024/snapshot", "deleted")

    response = _delete(env.client, [projects / "2024"])
    assert (response.status_code, response.json()["detail"]) == (409, CONTAINS.format("2024", "snapshot", "deleted"))
    assert _tree(env.pool) == ["a.txt", "old", "old/drafts", *PROJECTS_TREE]

    # The subvolume's own content is ordinary.
    response = _delete(env.client, [projects / "2024" / "snapshot" / "docs"])
    assert response.status_code == 200, response.text
    assert _tree(projects / "2024" / "snapshot") == []


def test_a_bind_mount_from_the_same_filesystem_below_a_folder_is_refused(env):
    """Same st_dev as the folder: only the mount table shows it."""
    target = env.pool / "media" / "usb disk" / "back\\slash"
    target.mkdir(parents=True)
    (target / "song.mp3").write_bytes(b"mp3")
    env.mounts.mount(target, root="/music")

    response = _delete(env.client, [env.pool / "media"])
    assert response.status_code == 409
    assert response.json()["detail"] == CONTAINS.format("media", "usb disk/back\\slash", "deleted")
    assert (target / "song.mp3").exists()


def test_a_folder_that_is_a_mount_point_itself_is_refused(env):
    (env.pool / "bound").mkdir()
    (env.pool / "bound" / "f.txt").write_text("f")
    env.mounts.mount(env.pool / "bound", root="/elsewhere")

    response = _delete(env.client, [env.pool / "bound"])
    assert response.status_code == 409
    assert response.json()["detail"] == (
        "'bound' is a separate subvolume or filesystem (such as a snapshot or a mount point) "
        "and cannot be deleted in the file manager."
    )
    assert _tree(env.pool) == ["bound", "bound/f.txt"]


def test_mount_points_above_or_beside_a_folder_do_not_count(env):
    """The pool's own mount point is above every folder (see _MountTable)."""
    (env.pool / "projects" / "a").mkdir(parents=True)
    (env.pool / "projects-old" / "mnt").mkdir(parents=True)
    env.mounts.mount(env.pool / "projects-old" / "mnt")  # shares the folder's name as a prefix
    env.mounts.mount(env.pool / "projects-old")

    response = _delete(env.client, [env.pool / "projects"])
    assert response.status_code == 200, response.text
    assert _tree(env.pool) == ["projects-old", "projects-old/mnt"]


def test_a_mount_point_that_appears_after_the_first_check_is_still_refused(env, monkeypatch):
    """Each folder is checked again right before it is deleted."""
    (env.pool / "first.txt").write_text("1")
    (env.pool / "later" / "disk").mkdir(parents=True)
    check_all = files_router._refuse_separate_filesystems

    def check_all_then_mount(*args, **kwargs):
        check_all(*args, **kwargs)
        env.mounts.mount(env.pool / "later" / "disk")

    monkeypatch.setattr(files_router, "_refuse_separate_filesystems", check_all_then_mount)
    response = _delete(env.client, [env.pool / "first.txt", env.pool / "later"])

    assert (response.status_code, response.json()["detail"]) == (409, CONTAINS.format("later", "disk", "deleted"))
    assert _tree(env.pool) == ["later", "later/disk"]


def test_ordinary_folders_are_still_deleted(env, monkeypatch):
    """Deep and wide trees; symlinks to directories (also to a mounted filesystem) are not followed."""
    folder = env.pool / "tree"
    deep = folder.joinpath(*[f"level{n}" for n in range(60)])
    deep.mkdir(parents=True)
    (deep / "leaf.txt").write_text("leaf")
    for n in range(30):
        (folder / f"wide{n:02}" / "sub").mkdir(parents=True)
    os.symlink(folder, folder / "level0" / "loop")  # a cycle if it were followed
    os.symlink(env.outside, folder / "outside")
    # A filesystem mounted outside the folder, linked from inside it.
    (env.pool / "usb").mkdir()
    (env.pool / "usb" / "photo.jpg").write_bytes(b"jpeg")
    env.mounts.mount(env.pool / "usb")
    os.symlink(env.pool / "usb", folder / "usb-link")
    monkeypatch.setattr(files_router, "os", _OsWithSubvolumes([env.pool / "usb", env.outside]))

    response = _delete(env.client, [folder])

    assert response.status_code == 200, response.text
    assert _tree(env.pool) == ["usb", "usb/photo.jpg"]
    assert _tree(env.outside) == ["secret.txt"]


class _OsCountingDescriptors(_OsWithSubvolumes):
    """Also records how many descriptors the router opened and has not closed yet."""

    def __init__(self, roots=()):
        super().__init__(roots)
        self.open_fds: set[int] = set()
        self.most_open = 0

    def open(self, *args, **kwargs):
        fd = os.open(*args, **kwargs)
        self.open_fds.add(fd)
        self.most_open = max(self.most_open, len(self.open_fds))
        return fd

    def close(self, fd):
        self.open_fds.discard(fd)
        os.close(fd)


def test_the_check_keeps_one_descriptor_open_per_level(env, monkeypatch):
    """Not one per directory: a wide folder would run out of descriptors."""
    folder = env.pool / "wide"
    for n in range(300):
        (folder / f"dir{n:03}" / "sub").mkdir(parents=True)
    chain = [f"z{n}" for n in range(20)]  # visited last
    folder.joinpath(*chain).mkdir(parents=True)
    counting = _OsCountingDescriptors([folder.joinpath(*chain)])
    monkeypatch.setattr(files_router, "os", counting)

    response = _delete(env.client, [folder])

    # The subvolume at the end of the chain is found after every other directory.
    detail = CONTAINS.format("wide", "/".join(chain), "deleted")
    assert (response.status_code, response.json()["detail"]) == (409, detail)
    assert counting.most_open <= len(chain) + 1  # the folder and each level below it
    assert counting.open_fds == set()

    folder.joinpath(*chain).rmdir()
    counting = _OsCountingDescriptors()
    monkeypatch.setattr(files_router, "os", counting)
    response = _delete(env.client, [folder])
    assert response.status_code == 200, response.text
    assert counting.most_open <= len(chain)
    assert not folder.exists()


def test_an_unreadable_mount_table_refuses(env, monkeypatch):
    """Without the table nothing is guessed: folders are not deleted, or moved to another filesystem."""
    (env.pool / "folder" / "sub").mkdir(parents=True)
    (env.pool / "file.txt").write_text("x")
    (env.pool / "dst").mkdir()
    monkeypatch.setattr(files_router, "MOUNTINFO", str(env.root / "missing"))

    response = _delete(env.client, [env.pool / "file.txt", env.pool / "folder"])
    assert response.status_code == 500
    assert response.json()["detail"] == (
        "Could not check 'folder' for mount points (the mount table cannot be read), so it was not deleted."
    )
    assert _tree(env.pool) == ["dst", "file.txt", "folder", "folder/sub"]

    _cross_device_rename(monkeypatch, "folder")
    response = _move(env.client, [env.pool / "folder"], env.pool / "dst")
    assert response.status_code == 500
    assert response.json()["detail"] == (
        "Could not check 'folder' for mount points (the mount table cannot be read), "
        "so it was not moved to another pool or volume."
    )
    assert _tree(env.pool) == ["dst", "file.txt", "folder", "folder/sub"]

    # Files, and folders moved within their filesystem, need no check.
    assert _delete(env.client, [env.pool / "file.txt"]).status_code == 200
    assert _move(env.client, [env.pool / "folder" / "sub"], env.pool / "dst").status_code == 200
    assert _tree(env.pool) == ["dst", "dst/sub", "folder"]


class _OsWithAnUnreadableDirectory(_OsWithSubvolumes):
    """Opening a directory named ``locked`` fails with an I/O error."""

    def open(self, path, *args, **kwargs):
        if path == "locked":
            raise OSError(errno.EIO, "Input/output error")
        return os.open(path, *args, **kwargs)


def test_a_directory_that_the_check_cannot_open_refuses_the_folder(env, monkeypatch):
    """Nothing is guessed: what the walk cannot open may be a mount point. Nothing is deleted."""
    folder = env.pool / "projects"
    (folder / "a" / "locked").mkdir(parents=True)
    (folder / "a" / "notes.txt").write_text("notes")
    monkeypatch.setattr(files_router, "os", _OsWithAnUnreadableDirectory([]))

    response = _delete(env.client, [folder])

    assert response.status_code == 500
    assert response.json()["detail"] == f"File operation failed: {os.strerror(errno.EIO)}"
    assert _tree(folder) == ["a", "a/locked", "a/notes.txt"]


def _cross_device_rename(monkeypatch, *names):
    """os.rename of the router fails with EXDEV for ``names``, as between two filesystems."""
    rename = files_router.os.rename

    def cross_device_rename(src, dst, *, src_dir_fd=None, dst_dir_fd=None):
        if src in names:
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return rename(src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

    monkeypatch.setattr(files_router.os, "rename", cross_device_rename)


def test_move_to_another_pool_refuses_a_folder_that_contains_a_subvolume(env, monkeypatch):
    """Another st_dev than the destination: checked with every other item, before anything is moved."""
    snapshot = env.pool / "projects" / "2024" / "snapshot"
    (snapshot / "docs").mkdir(parents=True)
    (env.pool / "a.txt").write_text("a")
    archive = env.base / "backup" / "archive"
    archive.mkdir(parents=True)
    monkeypatch.setattr(files_router, "os", _OsWithSubvolumes([snapshot, env.base / "backup"]))

    response = _move(env.client, [env.pool / "a.txt", env.pool / "projects"], archive)

    detail = CONTAINS.format("projects", "2024/snapshot", "moved to another pool or volume")
    assert (response.status_code, response.json()["detail"]) == (409, detail)
    assert _tree(env.pool) == [
        "a.txt", "projects", "projects/2024", "projects/2024/snapshot", "projects/2024/snapshot/docs"
    ]
    assert _tree(archive) == []


def test_a_move_that_fails_with_exdev_is_checked_before_anything_is_copied(env, projects, monkeypatch):
    """Same st_dev (e.g. two bind mounts of one filesystem), but rename(2) fails: copy, then delete."""
    (env.pool / "archive").mkdir()
    _cross_device_rename(monkeypatch, "projects")
    copies = []
    monkeypatch.setattr(files_router, "_copy_entry", lambda *args: copies.append(args))

    response = _move(env.client, [projects], env.pool / "archive")

    detail = CONTAINS.format("projects", "2024/snapshot", "moved to another pool or volume")
    assert (response.status_code, response.json()["detail"]) == (409, detail)
    assert copies == []
    assert _tree(env.pool) == ["archive", *PROJECTS_TREE]


def test_a_mount_point_that_appears_during_the_copy_of_a_move_is_not_deleted_into(env, monkeypatch):
    (env.pool / "folder" / "disk").mkdir(parents=True)
    (env.pool / "folder" / "disk" / "f.txt").write_text("f")
    (env.pool / "dst").mkdir()
    _cross_device_rename(monkeypatch, "folder")
    copy_entry = files_router._copy_entry

    def copy_then_mount(*args):
        copy_entry(*args)
        env.mounts.mount(env.pool / "folder" / "disk")

    monkeypatch.setattr(files_router, "_copy_entry", copy_then_mount)
    response = _move(env.client, [env.pool / "folder"], env.pool / "dst")

    detail = CONTAINS.format("folder", "disk", "moved to another pool or volume")
    assert (response.status_code, response.json()["detail"]) == (409, detail)
    # Refused right before the source would be deleted: the source is kept, and so is the copy.
    assert (env.pool / "folder" / "disk" / "f.txt").read_text() == "f"
    assert (env.pool / "dst" / "folder" / "disk" / "f.txt").read_text() == "f"


def _replace(folder, replacement):
    os.rename(folder, folder.with_name(folder.name + "-moved"))
    os.rename(replacement, folder)


def _replace_after_call(monkeypatch, function, number, folder, replacement):
    """Put ``replacement`` in the place of ``folder`` right after call ``number`` of the router's ``function``."""
    real = getattr(files_router, function)
    calls = []

    def call_then_replace(*args):
        result = real(*args)
        calls.append(args)
        if len(calls) == number:
            _replace(folder, replacement)
        return result

    monkeypatch.setattr(files_router, function, call_then_replace)


@pytest.fixture
def replacements(env, monkeypatch):
    """<pool>/elsewhere: snapshot is a subvolume, folder is an ordinary folder, nested contains a subvolume."""
    elsewhere = env.pool / "elsewhere"
    subvolumes = [elsewhere / "snapshot", elsewhere / "nested" / "snapshot"]
    for directory in [*subvolumes, elsewhere / "folder"]:
        directory.mkdir(parents=True)
        (directory / "keep.txt").write_text("keep")
    monkeypatch.setattr(files_router, "os", _OsWithSubvolumes(subvolumes))
    return elsewhere


SEPARATE = (
    "'old' is a separate subvolume or filesystem (such as a snapshot or a mount point) "
    "and cannot be {} in the file manager."
)
REPLACED = "'old' was replaced in the meantime, so it was not {}."


# A delete checks the entry and walks the folder in the check of every entry
# before anything is deleted (call 1), then again for its rmtree (call 2).
@pytest.mark.parametrize("function, replacement, detail", [
    ("_refuse_separate_filesystem", "snapshot", SEPARATE.format("deleted")),
    ("_first_other_device", "nested", REPLACED.format("deleted")),
], ids=["before the last check, by a subvolume", "during the last walk, by a folder that contains one"])
def test_a_folder_replaced_while_it_is_checked_is_not_deleted(
    env, replacements, monkeypatch, function, replacement, detail
):
    """The check right before rmtree takes a while: after it, the entry must still be the folder it checked."""
    folder = env.pool / "projects" / "old"
    folder.mkdir(parents=True)
    (folder / "a.txt").write_text("a")
    _replace_after_call(monkeypatch, function, 2, folder, replacements / replacement)

    response = _delete(env.client, [folder])

    assert (response.status_code, response.json()["detail"]) == (409, detail)
    assert len(list(env.pool.rglob("keep.txt"))) == 3
    assert (env.pool / "projects" / "old-moved" / "a.txt").read_text() == "a"


@pytest.mark.parametrize("replacement, detail", [
    ("snapshot", SEPARATE.format("moved to another pool or volume")),
    ("folder", REPLACED.format("moved to another pool or volume")),
], ids=["by a subvolume", "by another folder"])
def test_a_source_replaced_during_the_copy_of_a_move_is_not_deleted(
    env, replacements, monkeypatch, replacement, detail
):
    """E.g. someone renames the folder and makes a new one with that name while it is copied."""
    folder = env.pool / "projects" / "old"
    folder.mkdir(parents=True)
    (folder / "a.txt").write_text("a")
    (env.pool / "dst").mkdir()
    _cross_device_rename(monkeypatch, "old")
    copy_entry = files_router._copy_entry

    def copy_then_replace(src_dir_fd, name, dst_dir_fd):
        copy_entry(src_dir_fd, name, dst_dir_fd)
        if name == "old":  # the source itself, not an entry in it (_copy_entry recurses)
            _replace(folder, replacements / replacement)

    monkeypatch.setattr(files_router, "_copy_entry", copy_then_replace)
    response = _move(env.client, [folder], env.pool / "dst")

    assert (response.status_code, response.json()["detail"]) == (409, detail)
    assert (folder / "keep.txt").read_text() == "keep"
    assert (env.pool / "dst" / "old" / "a.txt").read_text() == "a"


def test_a_folder_with_a_subvolume_can_be_moved_within_its_filesystem_renamed_and_copied(env, projects):
    (env.pool / "archive").mkdir()
    (env.pool / "copies").mkdir()

    response = _copy(env.client, [projects], env.pool / "copies")
    assert response.status_code == 200, response.text
    assert (env.pool / "copies" / "projects" / "2024" / "snapshot" / "docs" / "plan.txt").read_text() == "plan"

    response = _move(env.client, [projects], env.pool / "archive")  # rename(2): the subvolume moves along
    assert response.status_code == 200, response.text
    moved = env.pool / "archive" / "projects"
    response = env.client.post("/api/v1/files/rename", json={"path": str(moved), "new_name": "done"})
    assert response.status_code == 200, response.text
    assert (env.pool / "archive" / "done" / "2024" / "snapshot" / "docs" / "plan.txt").read_text() == "plan"


def test_mount_table_escapes_are_decoded(tmp_path, monkeypatch):
    table = tmp_path / "mountinfo"
    table.write_bytes(
        b"30 25 0:40 / /mnt/pools/data rw - btrfs /dev/sda rw\n"
        b"31 30 0:40 /a /mnt/pools/data/my\\040disk/tab\\011new\\012line/back\\134slash rw - btrfs /dev/sda rw\n"
        b"32 30 8:17 / /mnt/pools/data/caf\xc3\xa9\r rw - ext4 /dev/sdb1 rw\n"
        b"33 30 8:18 / /mnt/pools/data/\\134040 rw - ext4 /dev/sdb2 rw\n"
        b"short line\n"
        b"\n"
    )
    monkeypatch.setattr(files_router, "MOUNTINFO", str(table))

    assert files_router._mount_points("x", "deleted") == [
        b"/mnt/pools/data",
        b"/mnt/pools/data/my disk/tab\tnew\nline/back\\slash",
        b"/mnt/pools/data/caf\xc3\xa9\r",
        b"/mnt/pools/data/\\040",
    ]
