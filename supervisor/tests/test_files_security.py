"""Security tests for the file manager API (path traversal, symlinks, uploads, the trash)."""

import asyncio
import errno
import importlib
import io
import logging
import os
import re
import stat
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.auth.utils import verify_token
from src.main import app
from src.shares import constants

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


# --- new folders and uploads in share folders (ADR-0273) ------------------------------


@pytest.fixture
def smb_group(monkeypatch):
    """The SMB group (constants.SMB_GROUP_GID) is the runner's own group: a test's folders can have it without root."""
    monkeypatch.setattr(constants, "SMB_GROUP_GID", os.getgid())
    return os.getgid()


def _folder(path, mode, gid):
    """``path`` made a folder of the group ``gid`` with exactly ``mode``, setgid included."""
    path.mkdir()
    os.chown(path, -1, gid)
    os.chmod(path, mode)
    return path


def _mode(path):
    """The mode bits of ``path`` (setgid included) and its group."""
    st = os.lstat(path)
    return stat.S_IMODE(st.st_mode), st.st_gid


def _mkdir(env, folder, name):
    return env.client.post("/api/v1/files/mkdir", json={"path": str(folder), "name": name})


def test_a_new_folder_and_an_upload_in_a_share_folder_are_writable_for_the_smb_group(env, smb_group):
    """In a folder as UNOS makes a share's (setgid, the SMB group, 2770): a folder 2770 and a file 0660 of that group."""
    docs = _folder(env.pool / "docs", 0o2770, smb_group)

    made = _mkdir(env, docs, "projects")
    uploaded = _upload(env.client, docs, "report.txt")

    assert (made.status_code, uploaded.status_code) == (200, 200), (made.text, uploaded.text)
    assert (made.json()["permissions"], uploaded.json()["permissions"]) == ("drwxrws---", "-rw-rw----")
    assert _mode(docs / "projects") == (0o2770, smb_group)
    assert _mode(docs / "report.txt") == (0o660, smb_group)
    assert (docs / "report.txt").read_bytes() == b"payload"
    # The new folder takes its own new entries alike.
    assert _mkdir(env, docs / "projects", "2026").status_code == 200
    assert _upload(env.client, docs / "projects", "plan.txt").status_code == 200
    assert _mode(docs / "projects" / "2026") == (0o2770, smb_group)
    assert _mode(docs / "projects" / "plan.txt") == (0o660, smb_group)


def test_an_upload_that_replaces_a_file_in_a_share_folder_leaves_it_writable_for_the_smb_group(env, smb_group):
    docs = _folder(env.pool / "docs", 0o2770, smb_group)
    (docs / "report.txt").write_bytes(b"old")
    os.chmod(docs / "report.txt", 0o660)

    response = _upload(env.client, docs, "report.txt", b"new")

    assert response.status_code == 200, response.text
    assert (docs / "report.txt").read_bytes() == b"new"
    assert _mode(docs / "report.txt") == (0o660, smb_group)
    assert _tree(docs) == ["report.txt"]


class _OsFailing:
    """The files router's os module, in which the functions ``names`` fail with the error ``code``."""

    def __init__(self, *names, code=errno.EIO):
        self._names = names
        self._code = code

    def __getattr__(self, name):
        if name in self._names:
            return self._fail
        return getattr(os, name)

    def _fail(self, *args, **kwargs):
        raise OSError(self._code, os.strerror(self._code))


# A step of New Folder in a share folder that fails after the mkdir: (the os function, its error, the answer).
FAILED_AFTER_THE_MKDIR = {
    "fchmod, an I/O error": ("fchmod", errno.EIO, (500, "File operation failed: Input/output error")),
    "fchmod, no space left": ("fchmod", errno.ENOSPC, (507, "No space left on device")),
    "the check that it is empty": ("scandir", errno.EIO, (500, "File operation failed: Input/output error")),
}


@pytest.mark.parametrize("function, code, answer", FAILED_AFTER_THE_MKDIR.values(), ids=FAILED_AFTER_THE_MKDIR.keys())
def test_a_new_folder_that_cannot_get_the_modes_of_shares_is_answered_so_and_removed_again(
    env, smb_group, monkeypatch, function, code, answer
):
    """The error is answered, and the folder that the mkdir made does not stay.

    Private, SMB users could not use it, and Repair permissions skips
    private folders: it would stay so until someone deleted it.
    """
    docs = _folder(env.pool / "docs", 0o2770, smb_group)
    monkeypatch.setattr(files_router, "os", _OsFailing(function, code=code))

    response = _mkdir(env, docs, "new")

    assert (response.status_code, response.json()["detail"]) == answer
    assert _tree(docs) == []


# New Folder in a share folder, whose new folder neither gets the modes of shares nor can be
# removed again: (the os functions that fail, their error, the answer).
LEFT_PRIVATE = {
    "neither fchmod nor rmdir": (
        ("fchmod", "rmdir"),
        errno.EIO,
        (500, "File operation failed: Input/output error. "
              "The new folder new stays private, and Repair permissions skips it: delete it"),
    ),
    "it cannot be opened": (
        ("open",),
        errno.EMFILE,
        (500, "File operation failed: Too many open files. "
              "The new folder new stays private, and Repair permissions skips it: delete it"),
    ),
}


@pytest.mark.parametrize("functions, code, answer", LEFT_PRIVATE.values(), ids=LEFT_PRIVATE.keys())
def test_a_new_folder_that_stays_private_is_answered_so(env, smb_group, monkeypatch, functions, code, answer):
    """The answer says that the folder stays private and must be deleted, since Repair permissions skips it."""
    docs = _folder(env.pool / "docs", 0o2770, smb_group)
    monkeypatch.setattr(files_router, "os", _OsFailing(*functions, code=code))

    response = _mkdir(env, docs, "new")

    assert (response.status_code, response.json()["detail"]) == answer
    mode, gid = _mode(docs / "new")
    assert (mode & 0o077, gid) == (0, smb_group)  # neither the group nor others can use it
    assert _tree(docs) == ["new"]


class _OsSwappingAtTheModes:
    """The files router's os module, in which another folder takes the name of a new folder, and then its fchmod fails.

    As an SMB user who can write the share's folder could do it in between:
    the new folder ``folder`` is renamed to ``<folder>.moved``, and
    ``replacement`` gets its name.
    """

    def __init__(self, folder, replacement):
        self._folder = folder
        self._replacement = replacement

    def __getattr__(self, name):
        return getattr(os, name)

    def fchmod(self, fd, mode):
        os.rename(self._folder, f"{self._folder}.moved")
        os.rename(self._replacement, self._folder)
        raise OSError(errno.EIO, os.strerror(errno.EIO))


def test_a_folder_put_at_the_name_of_a_new_folder_that_cannot_get_the_modes_of_shares_is_kept(
    env, smb_group, monkeypatch
):
    """Only the folder that the mkdir made is removed again: an empty folder that took its name meanwhile stays."""
    docs = _folder(env.pool / "docs", 0o2770, smb_group)
    other = _folder(docs / "other", 0o2770, smb_group)
    before = _mode(other)
    monkeypatch.setattr(files_router, "os", _OsSwappingAtTheModes(docs / "new", other))

    response = _mkdir(env, docs, "new")

    assert (response.status_code, response.json()["detail"]) == (500, "File operation failed: Input/output error")
    assert _mode(docs / "new") == before
    assert _tree(docs) == ["new", "new.moved"]


def test_an_existing_name_in_a_share_folder_is_refused_and_kept(env, smb_group):
    docs = _folder(env.pool / "docs", 0o2770, smb_group)
    _folder(docs / "archive", 0o2750, smb_group)

    response = _mkdir(env, docs, "archive")

    assert (response.status_code, response.json()["detail"]) == (400, "Already exists: archive")
    assert _mode(docs / "archive") == (0o2750, smb_group)


# Folders that are not set up as a share's: (mode, whether their group is the SMB group).
NOT_A_SHARE_FOLDER = {
    "an ordinary folder": (0o755, False),
    "of the SMB group without setgid": (0o770, True),
    "of the SMB group, which may only read": (0o2750, True),
    "of the SMB group, open to others too": (0o2775, True),
    "of another group": (0o2770, False),
}


@pytest.mark.parametrize("mode, smb", NOT_A_SHARE_FOLDER.values(), ids=NOT_A_SHARE_FOLDER.keys())
def test_outside_share_folders_new_folders_and_uploads_are_made_as_before(env, monkeypatch, mode, smb):
    """What the umask leaves of 0777 and 0666, as an mkdir and a new file get in the same folder."""
    monkeypatch.setattr(constants, "SMB_GROUP_GID", os.getgid() if smb else os.getgid() + 1)
    folder = _folder(env.pool / "folder", mode, os.getgid())
    os.mkdir(folder / "reference", 0o777)
    os.close(os.open(folder / "reference.txt", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666))
    (folder / "old.txt").write_bytes(b"old")

    made = _mkdir(env, folder, "new")
    uploaded = _upload(env.client, folder, "new.txt")
    replaced = _upload(env.client, folder, "old.txt", b"new")

    assert (made.status_code, uploaded.status_code, replaced.status_code) == (200, 200, 200)
    assert _mode(folder / "new") == _mode(folder / "reference")
    assert _mode(folder / "new.txt") == _mode(folder / "reference.txt")
    assert _mode(folder / "old.txt") == _mode(folder / "reference.txt")


class _OsSwappingNewFolders:
    """The files router's os module, in which another folder takes the place of a new folder right after its mkdir.

    As an SMB user who can write the share's folder could do it: the new
    folder is renamed to ``<name>.moved``, and ``replacement`` gets its name.
    Everything else is ``base``: the real os module, or another of these
    fakes.
    """

    def __init__(self, replacement, base=os):
        self._os = base
        self._replacement = replacement

    def __getattr__(self, name):
        return getattr(self._os, name)

    def mkdir(self, path, mode=0o777, *, dir_fd=None):
        self._os.mkdir(path, mode, dir_fd=dir_fd)
        os.rename(path, f"{path}.moved", src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        os.rename(self._replacement, path, dst_dir_fd=dir_fd)


@pytest.mark.parametrize("replacement", ["a private folder with files", "a folder the group may only read",
                                         "another user's empty private folder"])
def test_a_folder_put_at_the_name_of_a_new_folder_keeps_its_modes(env, smb_group, monkeypatch, replacement):
    """Only the folder that the mkdir made gets the modes of shares: the supervisor's, private and empty."""
    docs = _folder(env.pool / "docs", 0o2770, smb_group)
    other = docs / "other"
    if replacement == "a private folder with files":
        _folder(other, 0o700, smb_group)
        (other / "key").write_bytes(b"secret")
        base = os
    elif replacement == "a folder the group may only read":
        _folder(other, 0o2750, smb_group)
        base = os
    else:
        _folder(other, 0o700, smb_group)
        base = _OsWithOwners([other])
    before = _mode(other)
    monkeypatch.setattr(files_router, "os", _OsSwappingNewFolders(other, base))

    response = _mkdir(env, docs, "new")

    assert response.status_code == 200, response.text
    assert _mode(docs / "new") == before
    inside = ["new/key"] if replacement == "a private folder with files" else []
    assert _tree(docs) == sorted(["new", "new.moved", *inside])


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


def test_rename_a_file_and_a_folder(env):
    (env.pool / "draft.txt").write_text("draft")
    (env.pool / "photos").mkdir()
    (env.pool / "photos" / "img.jpg").write_bytes(b"jpeg")

    response = env.client.post(
        "/api/v1/files/rename", json={"path": str(env.pool / "draft.txt"), "new_name": "final.txt"}
    )
    assert response.status_code == 200, response.text
    assert {key: response.json()[key] for key in ("name", "path", "type", "size")} == {
        "name": "final.txt",
        "path": str(env.pool / "final.txt"),
        "type": "file",
        "size": 5,
    }
    response = env.client.post(
        "/api/v1/files/rename", json={"path": str(env.pool / "photos"), "new_name": "pictures"}
    )
    assert response.status_code == 200, response.text
    assert (response.json()["name"], response.json()["type"]) == ("pictures", "directory")

    listing = env.client.get("/api/v1/files/list", params={"path": str(env.pool)}).json()
    assert [item["name"] for item in listing["items"]] == ["pictures", "final.txt"]
    assert (env.pool / "final.txt").read_text() == "draft"
    assert _tree(env.pool / "pictures") == ["img.jpg"]


@pytest.mark.parametrize("existing", ["file", "empty folder"])
def test_rename_existing_target_is_refused(env, existing):
    (env.pool / "a").write_text("a")
    if existing == "file":
        (env.pool / "b").write_text("b")
    else:  # rename(2) would replace an empty folder
        (env.pool / "b").mkdir()
    response = env.client.post("/api/v1/files/rename", json={"path": str(env.pool / "a"), "new_name": "b"})
    assert (response.status_code, response.json()["detail"]) == (400, "Already exists: b")
    assert (env.pool / "a").read_text() == "a"
    if existing == "file":
        assert (env.pool / "b").read_text() == "b"
    else:
        assert (env.pool / "b").is_dir()


def test_rename_never_replaces_a_name_that_appears_after_the_check(env, monkeypatch):
    """E.g. an SMB user or an app creates the new name at the last moment: the rename is refused, and it stays."""
    (env.pool / "a").write_text("a")
    (env.pool / "b").write_text("b")
    lstat_at = files_router._lstat_at
    # As if b appeared right after the router looked for it.
    monkeypatch.setattr(
        files_router, "_lstat_at", lambda dir_fd, name: None if name == "b" else lstat_at(dir_fd, name)
    )

    response = env.client.post("/api/v1/files/rename", json={"path": str(env.pool / "a"), "new_name": "b"})

    assert (response.status_code, response.json().get("detail")) == (400, "Already exists: b")
    assert (env.pool / "a").read_text() == "a"
    assert (env.pool / "b").read_text() == "b"


def _no_exclusive_rename(src_dir_fd, name, dst_dir_fd, new_name):
    """What renameat2 answers where the filesystem cannot refuse an existing name itself (NFS, some FUSE)."""
    raise OSError(errno.EINVAL, os.strerror(errno.EINVAL))


@pytest.mark.parametrize("exclusive_rename", [None, _no_exclusive_rename], ids=["no C function", "EINVAL"])
def test_rename_looks_for_the_name_first_where_it_cannot_be_refused_in_one_step(env, monkeypatch, exclusive_rename):
    monkeypatch.setattr(files_router, "_EXCLUSIVE_RENAME", exclusive_rename)
    (env.pool / "a").write_text("a")
    (env.pool / "b").write_text("b")

    response = env.client.post("/api/v1/files/rename", json={"path": str(env.pool / "a"), "new_name": "b"})
    assert (response.status_code, response.json()["detail"]) == (400, "Already exists: b")
    response = env.client.post("/api/v1/files/rename", json={"path": str(env.pool / "a"), "new_name": "c"})
    assert (response.status_code, response.json()["name"]) == (200, "c")
    assert _tree(env.pool) == ["b", "c"]
    assert (env.pool / "c").read_text() == "a"


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


def test_delete_for_good_removes_files_and_directories(env):
    (env.pool / "dir" / "nested").mkdir(parents=True)
    (env.pool / "dir" / "nested" / "f").write_text("x")
    os.symlink(env.outside, env.pool / "dir" / "nested" / "escape")
    (env.pool / "file.txt").write_text("x")
    paths = [str(env.pool / "dir"), str(env.pool / "file.txt"), str(env.pool / "missing")]
    response = env.client.request("DELETE", "/api/v1/files/delete", params={"permanent": "true"}, json=paths)
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
    # Into the volume's own trash (ADR-0259).
    assert _untrashed_tree(volume) == ["img.jpg"]
    assert _trash(volume) == [("2024", "directory", [])]
    assert not (env.pool / TRASH).exists()


def test_a_symlink_to_a_volume_is_an_ordinary_entry(env, volume):
    os.symlink(volume, env.pool / "link")
    response = env.client.request("DELETE", "/api/v1/files/delete", json=[str(env.pool / "link")])
    assert response.status_code == 200
    assert _untrashed_tree(env.pool) == VOLUME_TREE
    assert _trash(env.pool) == [("link", "symlink", str(volume))]
    assert not (volume / TRASH).exists()


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


def _delete(client, paths, *, permanent=False):
    """Delete ``paths``: into the trash, or for good (ADR-0259)."""
    params = {"permanent": "true"} if permanent else {}
    return client.request("DELETE", "/api/v1/files/delete", params=params, json=[str(p) for p in paths])


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

    # The subvolume's own content is ordinary, but has no trash (ADR-0259): it is deleted for good.
    response = _delete(env.client, [projects / "2024" / "snapshot" / "docs"])
    assert (response.status_code, response.json()["detail"]) == (409, NO_TRASH.format("docs"))
    assert _tree(env.pool) == ["a.txt", "old", "old/drafts", *PROJECTS_TREE]
    response = _delete(env.client, [projects / "2024" / "snapshot" / "docs"], permanent=True)
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


def _busy_rename(monkeypatch, name):
    """rename(2) of the entry ``name`` fails with EBUSY, as the kernel answers for a mount point.

    A bind mount from the pool's own filesystem has the pool's st_dev, so
    the check of the entry itself lets it through to the rename.
    """
    real_rename = os.rename

    def busy(old, new, *, src_dir_fd=None, dst_dir_fd=None):
        if old == name:
            raise OSError(errno.EBUSY, os.strerror(errno.EBUSY))
        return real_rename(old, new, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

    monkeypatch.setattr(files_router.os, "rename", busy)
    monkeypatch.setattr(
        files_router, "_EXCLUSIVE_RENAME",
        lambda src_dir_fd, old, dst_dir_fd, new: busy(old, new, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd),
    )


@pytest.mark.parametrize("action", ["renamed", "moved"])
def test_a_folder_that_is_a_bind_mount_cannot_be_renamed_or_moved(env, monkeypatch, action):
    """409 with the reason, as for a delete of it, not 500 (ADR-0073)."""
    (env.pool / "bound").mkdir()
    (env.pool / "bound" / "f.txt").write_text("f")
    (env.pool / "archive").mkdir()
    env.mounts.mount(env.pool / "bound", root="/elsewhere")
    _busy_rename(monkeypatch, "bound")

    if action == "renamed":
        response = env.client.post(
            "/api/v1/files/rename", json={"path": str(env.pool / "bound"), "new_name": "other"}
        )
    else:
        response = _move(env.client, [env.pool / "bound"], env.pool / "archive")

    assert (response.status_code, response.json()["detail"]) == (
        409,
        "'bound' is a separate subvolume or filesystem (such as a snapshot or a mount point) "
        f"and cannot be {action} in the file manager.",
    )
    assert _tree(env.pool) == ["archive", "bound", "bound/f.txt"]


def test_any_other_entry_that_is_busy_is_a_conflict():
    """E.g. a file that is a bind mount, on its way into the trash."""
    error = files_router._http_error(OSError(errno.EBUSY, os.strerror(errno.EBUSY)))
    assert (error.status_code, error.detail) == (
        409,
        "In use by the system, for example as a mount point, so the file manager cannot change it",
    )


def test_mount_points_above_or_beside_a_folder_do_not_count(env):
    """The pool's own mount point is above every folder (see _MountTable)."""
    (env.pool / "projects" / "a").mkdir(parents=True)
    (env.pool / "projects-old" / "mnt").mkdir(parents=True)
    env.mounts.mount(env.pool / "projects-old" / "mnt")  # shares the folder's name as a prefix
    env.mounts.mount(env.pool / "projects-old")

    response = _delete(env.client, [env.pool / "projects"])
    assert response.status_code == 200, response.text
    assert _untrashed_tree(env.pool) == ["projects-old", "projects-old/mnt"]
    assert _trash(env.pool) == [("projects", "directory", ["a"])]


@pytest.mark.parametrize("permanent", [False, True], ids=["into the trash", "for good"])
def test_a_mount_point_that_appears_after_the_first_check_is_still_refused(env, monkeypatch, permanent):
    """Each folder is checked again right before it is deleted."""
    (env.pool / "first.txt").write_text("1")
    (env.pool / "later" / "disk").mkdir(parents=True)
    check_all = files_router._refuse_separate_filesystems

    def check_all_then_mount(*args, **kwargs):
        check_all(*args, **kwargs)
        env.mounts.mount(env.pool / "later" / "disk")

    monkeypatch.setattr(files_router, "_refuse_separate_filesystems", check_all_then_mount)
    response = _delete(env.client, [env.pool / "first.txt", env.pool / "later"], permanent=permanent)

    # With what went before it (ADR-0259): the client can tell that first.txt is gone.
    done = "deleted for good" if permanent else "moved to the trash"
    detail = f"{CONTAINS.format('later', 'disk', 'deleted')} 1 of the 2 items was {done} before that."
    assert (response.status_code, response.json()["detail"]) == (409, detail)
    assert _untrashed_tree(env.pool) == ["later", "later/disk"]
    assert _trash(env.pool) == ([] if permanent else [("first.txt", "file", b"1")])


def test_ordinary_folders_are_still_deleted_for_good(env, monkeypatch):
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

    response = _delete(env.client, [folder], permanent=True)

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
    assert _untrashed_tree(env.pool) == ["dst", "dst/sub", "folder"]
    assert _trash(env.pool) == [("file.txt", "file", b"x")]


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
# before anything is deleted (call 1), then again for its rmtree, or for its
# move into the trash (call 2).
@pytest.mark.parametrize("permanent", [False, True], ids=["into the trash", "for good"])
@pytest.mark.parametrize("function, replacement, detail", [
    ("_refuse_separate_filesystem", "snapshot", SEPARATE.format("deleted")),
    ("_first_other_device", "nested", REPLACED.format("deleted")),
], ids=["before the last check, by a subvolume", "during the last walk, by a folder that contains one"])
def test_a_folder_replaced_while_it_is_checked_is_not_deleted(
    env, replacements, monkeypatch, function, replacement, detail, permanent
):
    """The check right before rmtree takes a while: after it, the entry must still be the folder it checked."""
    folder = env.pool / "projects" / "old"
    folder.mkdir(parents=True)
    (folder / "a.txt").write_text("a")
    _replace_after_call(monkeypatch, function, 2, folder, replacements / replacement)

    response = _delete(env.client, [folder], permanent=permanent)

    assert (response.status_code, response.json()["detail"]) == (409, detail)
    assert len(list(env.pool.rglob("keep.txt"))) == 3
    assert (env.pool / "projects" / "old-moved" / "a.txt").read_text() == "a"
    assert _trash(env.pool) == []


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

    def copy_then_replace(src_dir_fd, name, dst_dir_fd, *rest):
        copy_entry(src_dir_fd, name, dst_dir_fd, *rest)
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


# --- the trash (ADR-0259) --------------------------------------------------------------
#
# Delete moves an item into the trash of the pool or volume that holds it,
# <pool>/.unos-trash or <pool>/<volume>/.unos-trash: one private folder per
# deleted item, named after the time of the deletion in UTC, with the item as
# ``item`` and its path below the pool or volume in ``origin``.

TRASH = ".unos-trash"
ENTRY = re.compile(r"\d{8}T\d{6}Z-[0-9a-f]{16}")
NO_TRASH = (
    "'{}' is in a separate subvolume or filesystem (such as a snapshot or a mount point) that has no "
    "trash, so it was not deleted. Delete it for good instead."
)
TRASH_REFUSED = "The trash (.unos-trash) is reached through Trash, not as a folder"
RESERVED = "Invalid name: .unos-trash is the name of the trash"
NOT_A_FOLDER = "{} is not a folder, so nothing can be moved to the trash of {}."
NOT_PRIVATE = "{} could not be made private (only UNOS may open it), so nothing can be moved to the trash of {}."
ELSEWHERE_NOW = "Could not restore '{}': the folder {} is on another subvolume or filesystem now."
UNKNOWN_ORIGIN = "It is not known where {} was deleted from, so it cannot be restored. Delete it for good instead."
GONE = "Could not restore '{}': the folder {} it was deleted from is gone. Restore or create that folder first."
T0 = datetime(2026, 11, 7, 12, 0, 0, tzinfo=timezone.utc)


def _untrashed_tree(path):
    """_tree without the trash folder of ``path``, whose content _trash shows."""
    return [p for p in _tree(path) if p != TRASH and not p.startswith(f"{TRASH}/")]


def _trash(scope):
    """What the trash of the pool or volume ``scope`` holds: (origin, type, content), by origin.

    The content is the bytes of a file, the target of a symlink or the tree
    of a folder. The trash and each entry in it are private folders, and an
    entry holds the item and its origin, nothing else.
    """
    trash = scope / TRASH
    if not trash.exists():
        return []
    assert stat.S_IMODE(trash.lstat().st_mode) == 0o700
    held = []
    for entry in trash.iterdir():
        assert ENTRY.fullmatch(entry.name), entry.name
        assert stat.S_IMODE(entry.lstat().st_mode) == 0o700
        assert sorted(p.name for p in entry.iterdir()) == ["item", "origin"]
        assert stat.S_IMODE((entry / "origin").lstat().st_mode) == 0o600
        item = entry / "item"
        if item.is_symlink():
            content = ("symlink", os.readlink(item))
        elif item.is_dir():
            content = ("directory", _tree(item))
        else:
            content = ("file", item.read_bytes())
        held.append(((entry / "origin").read_bytes().decode(), *content))
    return sorted(held)


def _trash_listing(client):
    response = client.get("/api/v1/files/trash")
    assert response.status_code == 200, response.text
    return response.json()


def _trashed(client):
    """The listed items of the trash: original path -> path in the trash."""
    return {item["original_path"]: item["path"] for item in _trash_listing(client)["items"]}


def _restore(client, paths):
    return client.post("/api/v1/files/trash/restore", json=[str(p) for p in paths])


def _delete_for_good(client, paths):
    return client.request("DELETE", "/api/v1/files/trash", json=[str(p) for p in paths])


def _empty(client):
    return client.post("/api/v1/files/trash/empty")


@pytest.fixture
def clock(monkeypatch):
    """The supervisor's clock (files_router._now): the tests set clock.now."""
    clock = SimpleNamespace(now=T0)
    monkeypatch.setattr(files_router, "_now", lambda: clock.now)
    return clock


# Step 1: deleted items go to the trash of their pool or volume.


def test_delete_moves_the_items_into_the_trash_of_their_pool(env):
    docs = env.pool / "docs"
    (docs / "old").mkdir(parents=True)
    (docs / "old" / "notes.txt").write_text("notes")
    (docs / "report.pdf").write_bytes(b"pdf")
    os.symlink(env.outside, docs / "link")
    paths = [docs / "old", docs / "report.pdf", docs / "link"]

    response = _delete(env.client, paths)

    assert response.status_code == 200, response.text
    assert response.json()["deleted"] == [str(p) for p in paths]
    assert _untrashed_tree(env.pool) == ["docs"]
    assert _trash(env.pool) == [
        ("docs/link", "symlink", str(env.outside)),
        ("docs/old", "directory", ["notes.txt"]),
        ("docs/report.pdf", "file", b"pdf"),
    ]
    assert _tree(env.outside) == ["secret.txt"]
    # The file manager does not list the trash.
    listing = env.client.get("/api/v1/files/list", params={"path": str(env.pool)}).json()
    assert ([item["name"] for item in listing["items"]], listing["item_count"]) == (["docs"], 1)


def test_the_trash_lists_what_was_deleted_from_where_and_when(env, clock):
    (env.pool / "docs").mkdir()
    (env.pool / "docs" / "a.txt").write_text("alpha")
    (env.pool / "photos").mkdir()
    assert _delete(env.client, [env.pool / "docs" / "a.txt"]).status_code == 200
    clock.now = T0 + timedelta(minutes=5)
    assert _delete(env.client, [env.pool / "photos"]).status_code == 200

    listing = _trash_listing(env.client)

    assert (listing["retention_days"], listing["problems"]) == (30, [])
    items = listing["items"]
    # Newest first; the times in UTC with their offset.
    assert [{key: value for key, value in item.items() if key != "path"} for item in items] == [
        {
            "name": "photos", "type": "directory", "size": 0, "original_path": str(env.pool / "photos"),
            "deleted": "2026-11-07T12:05:00+00:00", "expires": "2026-12-07T12:05:00+00:00",
        },
        {
            "name": "a.txt", "type": "file", "size": 5, "original_path": str(env.pool / "docs" / "a.txt"),
            "deleted": "2026-11-07T12:00:00+00:00", "expires": "2026-12-07T12:00:00+00:00",
        },
    ]
    for item, stamp in zip(items, ["20261107T120500Z", "20261107T120000Z"]):
        assert re.fullmatch(rf"{re.escape(str(env.pool / TRASH))}/{stamp}-[0-9a-f]{{16}}", item["path"])


def test_items_in_a_volume_go_to_the_trash_of_the_volume_and_come_back(env, volume):
    """btrfs renames nothing into another subvolume, so a volume has a trash of its own."""
    img = volume / "album" / "img.jpg"
    response = _delete(env.client, [img])
    assert response.status_code == 200, response.text
    assert not (env.pool / TRASH).exists()
    assert _untrashed_tree(volume) == ["album"]
    assert _trash(volume) == [("album/img.jpg", "file", b"jpeg")]

    trashed = _trashed(env.client)
    assert list(trashed) == [str(img)]
    assert trashed[str(img)].startswith(f"{volume / TRASH}/")
    response = _restore(env.client, [trashed[str(img)]])
    assert (response.status_code, response.json()) == (200, {"restored": [str(img)]})
    assert _untrashed_tree(volume) == ["album", "album/img.jpg"]
    assert _trash(volume) == []
    assert img.read_bytes() == b"jpeg"


def test_items_without_a_trash_are_refused_before_anything_is_moved(env, projects):
    """A deeper subvolume (a snapshot, a restored copy in a volume) has no trash: its items are deleted for good."""
    (env.pool / "a.txt").write_text("a")
    docs = projects / "2024" / "snapshot" / "docs"

    response = _delete(env.client, [env.pool / "a.txt", docs])

    assert (response.status_code, response.json()["detail"]) == (409, NO_TRASH.format("docs"))
    assert _tree(env.pool) == ["a.txt", *PROJECTS_TREE]


def test_an_item_that_one_rename_cannot_take_into_the_trash_stays(env, monkeypatch):
    """E.g. below a bind mount of the pool's own filesystem: same st_dev, but rename(2) fails with EXDEV."""
    (env.pool / "media").mkdir()
    (env.pool / "media" / "song.mp3").write_bytes(b"mp3")

    def cross_device(src_dir_fd, name, dst_dir_fd, new_name):
        raise OSError(errno.EXDEV, os.strerror(errno.EXDEV))

    monkeypatch.setattr(files_router, "_EXCLUSIVE_RENAME", cross_device)
    response = _delete(env.client, [env.pool / "media" / "song.mp3"])

    assert (response.status_code, response.json()["detail"]) == (409, NO_TRASH.format("song.mp3"))
    assert _untrashed_tree(env.pool) == ["media", "media/song.mp3"]
    assert _trash(env.pool) == []  # the entry that was made for it is gone again
    response = _delete(env.client, [env.pool / "media" / "song.mp3"], permanent=True)
    assert response.status_code == 200, response.text
    assert _untrashed_tree(env.pool) == ["media"]


def test_the_trash_is_not_a_folder_of_the_file_manager(env):
    (env.pool / "a.txt").write_text("a")
    (env.pool / "dst").mkdir()
    assert _delete(env.client, [env.pool / "a.txt"]).status_code == 200
    trash = env.pool / TRASH
    (entry,) = trash.iterdir()
    item = entry / "item"

    refused = [
        env.client.get("/api/v1/files/list", params={"path": str(trash)}),
        env.client.get("/api/v1/files/list", params={"path": str(entry)}),
        env.client.get("/api/v1/files/info", params={"path": str(item)}),
        env.client.get("/api/v1/files/download", params={"path": str(item)}),
        _upload(env.client, trash, "x.txt"),
        env.client.post("/api/v1/files/mkdir", json={"path": str(entry), "name": "x"}),
        env.client.post("/api/v1/files/rename", json={"path": str(item), "new_name": "x"}),
        env.client.post("/api/v1/files/rename", json={"path": str(trash), "new_name": "x"}),
        _copy(env.client, [item], env.pool / "dst"),
        _copy(env.client, [env.pool / "dst"], entry),
        _move(env.client, [item], env.pool / "dst"),
        _delete(env.client, [trash]),
        _delete(env.client, [item], permanent=True),
    ]
    assert [(r.status_code, r.json()["detail"]) for r in refused] == [(403, TRASH_REFUSED)] * len(refused)
    # Nothing gets its name either.
    named = [
        env.client.post("/api/v1/files/mkdir", json={"path": str(env.pool / "dst"), "name": TRASH}),
        env.client.post("/api/v1/files/rename", json={"path": str(env.pool / "dst"), "new_name": TRASH}),
        _upload(env.client, env.pool / "dst", TRASH),
    ]
    assert [(r.status_code, r.json()["detail"]) for r in named] == [(400, RESERVED)] * len(named)
    assert _untrashed_tree(env.pool) == ["dst"]
    assert _trash(env.pool) == [("a.txt", "file", b"a")]


def test_a_copy_leaves_the_trash_out(env, volume):
    (env.pool / "a.txt").write_text("a")
    assert _delete(env.client, [env.pool / "a.txt", volume / "album" / "img.jpg"]).status_code == 200
    (env.base / "backup").mkdir()

    response = _copy(env.client, [volume], env.base / "backup")
    assert response.status_code == 200, response.text
    assert _tree(env.base / "backup") == ["photos", "photos/album"]
    response = _copy(env.client, [env.pool], env.base / "backup")  # the whole pool, its volume included
    assert response.status_code == 200, response.text
    assert _tree(env.base / "backup" / "data") == ["photos", "photos/album"]
    assert _trash(env.pool) == [("a.txt", "file", b"a")]
    assert _trash(volume) == [("album/img.jpg", "file", b"jpeg")]


def test_a_trash_that_is_a_symlink_is_never_followed(env):
    os.symlink(env.outside, env.pool / TRASH)
    (env.pool / "a.txt").write_text("a")
    detail = NOT_A_FOLDER.format(env.pool / TRASH, env.pool)

    response = _delete(env.client, [env.pool / "a.txt"])

    assert (response.status_code, response.json()["detail"]) == (409, detail)
    assert (env.pool / "a.txt").read_text() == "a"
    assert _tree(env.outside) == ["secret.txt"]
    listing = _trash_listing(env.client)
    assert (listing["items"], listing["problems"]) == ([], [detail])


# A trash that someone else made first, e.g. an app or an NFS client that can
# write the top folder of a volume, is made private (the supervisor's, mode
# 0700) before it is used: otherwise they could rename the entries in it, to
# an older time for the expiry or out of the list. Only private folders in it
# are entries. The tests do not run as root: another user's folders are faked.


class _StatWithOwner:
    """An lstat() result with another st_uid."""

    def __init__(self, st, st_uid):
        self._st = st
        self.st_uid = st_uid

    def __getattr__(self, name):
        return getattr(self._st, name)


class _OsWithOwners:
    """The files router's os module, in which the given entries belong to another user until they are chowned.

    That user's uid is the test's own plus one. fchown of such an entry is
    recorded, and from then on it is the test's own, as the real one makes
    it the supervisor's. Everything else is ``base``: the real os module,
    or another of these fakes.
    """

    def __init__(self, paths, base=os):
        self._os = base
        self._inodes = {self._key(base.stat(path, follow_symlinks=False)) for path in paths}
        self._uid = os.geteuid() + 1
        self.chowned = []

    def __getattr__(self, name):
        return getattr(self._os, name)

    @staticmethod
    def _key(st):
        return st.st_dev, st.st_ino

    def _owned(self, st):
        return _StatWithOwner(st, self._uid) if self._key(st) in self._inodes else st

    def stat(self, path, *args, **kwargs):
        return self._owned(self._os.stat(path, *args, **kwargs))

    def fstat(self, fd):
        return self._owned(self._os.fstat(fd))

    def fchown(self, fd, uid, gid):
        key = self._key(self._os.fstat(fd))
        if key not in self._inodes:
            return self._os.fchown(fd, uid, gid)
        self._inodes.discard(key)
        self.chowned.append((uid, gid))


class _OsWithoutChown(_OsWithOwners):
    """Also no entry can be given to the supervisor, e.g. on vfat or over NFS with root_squash."""

    def fchown(self, fd, uid, gid):
        raise PermissionError(errno.EPERM, os.strerror(errno.EPERM))


class _OsKeepingModesOfItsOwn:
    """The files router's os module on a filesystem that keeps modes of its own (vfat): folders get 0755."""

    def __getattr__(self, name):
        return getattr(os, name)

    def mkdir(self, path, mode=0o777, *, dir_fd=None):
        os.mkdir(path, 0o755, dir_fd=dir_fd)

    def fchmod(self, fd, mode):
        raise PermissionError(errno.EPERM, os.strerror(errno.EPERM))


def test_a_trash_that_others_can_change_is_made_private_and_what_they_put_there_is_no_entry(env, clock):
    trash = env.pool / TRASH
    planted = trash / "20200101T000000Z-0000000000000000"  # an old time: due for the expiry
    planted.mkdir(parents=True)
    (planted / "item").write_text("planted")
    (planted / "origin").write_text("secret/x.txt")
    os.chmod(planted, 0o777)
    os.chmod(trash, 0o777)

    # The listing, as every use of the trash, makes it private first; the planted folder is no entry.
    assert _trash_listing(env.client) == {"items": [], "retention_days": 30, "problems": []}
    assert stat.S_IMODE(trash.lstat().st_mode) == 0o700
    os.chmod(trash, 0o777)
    (env.pool / "a.txt").write_text("a")
    assert _delete(env.client, [env.pool / "a.txt"]).status_code == 200
    assert stat.S_IMODE(trash.lstat().st_mode) == 0o700
    assert [item["original_path"] for item in _trash_listing(env.client)["items"]] == [str(env.pool / "a.txt")]

    # It is not restored, deleted for good, expired or emptied: it stays as it is.
    response = _restore(env.client, [planted])
    assert (response.status_code, response.json()["detail"]) == (404, f"Not in the trash: {planted}")
    assert _delete_for_good(env.client, [planted]).json() == {"deleted": []}
    assert files_router._expire_trash(T0 + timedelta(days=30)) == (1, [])  # a.txt
    assert _empty(env.client).json() == {"deleted": 0}
    assert sorted(p.name for p in trash.iterdir()) == [planted.name]
    assert (planted / "item").read_text() == "planted"
    assert _untrashed_tree(env.pool) == []


def test_a_trash_of_another_user_is_made_the_supervisors_own(env, monkeypatch, clock):
    trash = env.pool / TRASH
    planted = trash / "20200101T000000Z-0000000000000000"  # private, but not the supervisor's
    planted.mkdir(parents=True)
    (planted / "item").write_text("planted")
    (planted / "origin").write_text("a.txt")
    os.chmod(planted, 0o700)
    os.chmod(trash, 0o700)
    owners = _OsWithOwners([trash, planted])
    monkeypatch.setattr(files_router, "os", owners)
    (env.pool / "a.txt").write_text("a")

    assert _delete(env.client, [env.pool / "a.txt"]).status_code == 200

    assert owners.chowned == [(os.geteuid(), os.getegid())]  # the trash's, before a.txt went in
    assert [item["original_path"] for item in _trash_listing(env.client)["items"]] == [str(env.pool / "a.txt")]
    assert files_router._expire_trash(T0 + timedelta(days=30)) == (1, [])
    assert sorted(p.name for p in trash.iterdir()) == [planted.name]


def test_a_trash_that_cannot_be_made_private_is_not_used(env, volume, monkeypatch):
    trash = volume / TRASH
    trash.mkdir(mode=0o700)
    monkeypatch.setattr(files_router, "os", _OsWithoutChown([trash], base=files_router.os))
    (env.pool / "a.txt").write_text("a")
    img = volume / "album" / "img.jpg"
    detail = NOT_PRIVATE.format(trash, volume)

    # Checked with every other item before anything is moved: a.txt, whose trash is fine, stays too.
    response = _delete(env.client, [env.pool / "a.txt", img])

    assert (response.status_code, response.json()["detail"]) == (409, detail)
    assert _tree(env.pool) == ["a.txt", "photos", f"photos/{TRASH}", "photos/album", "photos/album/img.jpg"]
    listing = _trash_listing(env.client)
    assert (listing["items"], listing["problems"]) == ([], [detail])
    response = _delete(env.client, [img], permanent=True)
    assert response.status_code == 200, response.text
    assert _tree(volume) == [TRASH, "album"]


def test_no_trash_stays_where_it_cannot_be_kept_private(env, monkeypatch):
    monkeypatch.setattr(files_router, "os", _OsKeepingModesOfItsOwn())
    (env.pool / "a.txt").write_text("a")

    response = _delete(env.client, [env.pool / "a.txt"])

    assert (response.status_code, response.json()["detail"]) == (409, NOT_PRIVATE.format(env.pool / TRASH, env.pool))
    assert _tree(env.pool) == ["a.txt"]


def _cross_mount_rename(monkeypatch, *names):
    """The router's exclusive rename of an entry named in ``names`` fails with EXDEV, as across two mounts."""
    rename_exclusive = files_router._rename_exclusive

    def cross_mount(src_dir_fd, name, dst_dir_fd, new_name):
        if name in names or new_name in names:
            raise OSError(errno.EXDEV, os.strerror(errno.EXDEV))
        rename_exclusive(src_dir_fd, name, dst_dir_fd, new_name)

    monkeypatch.setattr(files_router, "_rename_exclusive", cross_mount)


def test_items_below_a_bind_mount_are_refused_before_anything_is_moved(env, monkeypatch):
    """The st_dev of the pool, so only the mount table shows it; one rename cannot take the item to the trash."""
    (env.pool / "a.txt").write_text("a")
    usb = env.pool / "media" / "usb"
    usb.mkdir(parents=True)
    (usb / "song.mp3").write_bytes(b"mp3")
    env.mounts.mount(usb, root="/music")
    _cross_mount_rename(monkeypatch, "song.mp3")

    response = _delete(env.client, [env.pool / "a.txt", usb / "song.mp3"])

    assert (response.status_code, response.json()["detail"]) == (409, NO_TRASH.format("song.mp3"))
    assert _tree(env.pool) == ["a.txt", "media", "media/usb", "media/usb/song.mp3"]


def test_a_delete_that_fails_part_way_says_how_many_items_went_before(env, monkeypatch):
    """E.g. the pool has no space left for the second item's entry: the first is in the trash, the second stays."""
    for name in ("a.txt", "b.txt"):
        (env.pool / name).write_text(name)
    write_origin = files_router._write_origin
    written = []

    def write_origin_until_the_pool_is_full(entry_fd, below_scope):
        written.append(below_scope)
        if len(written) == 2:
            raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
        write_origin(entry_fd, below_scope)

    monkeypatch.setattr(files_router, "_write_origin", write_origin_until_the_pool_is_full)
    response = _delete(env.client, [env.pool / "a.txt", env.pool / "b.txt"])

    # The web UI's fake of this answer (webui/tests/files.spec.ts) has this text, word for word.
    assert (response.status_code, response.json()["detail"]) == (
        507, "No space left on device. 1 of the 2 items was moved to the trash before that."
    )
    assert _untrashed_tree(env.pool) == ["b.txt"]
    assert _trash(env.pool) == [("a.txt", "file", b"a.txt")]


# Step 2: deleted items are restored to where they were.


def test_restore_puts_the_items_back_where_they_were(env):
    docs = env.pool / "docs"
    (docs / "old").mkdir(parents=True)
    (docs / "old" / "notes.txt").write_text("notes")
    (docs / "report.pdf").write_bytes(b"pdf")
    os.symlink(env.outside, docs / "link")
    os.chmod(docs / "old", 0o750)
    before = {name: os.lstat(docs / name) for name in ("old", "report.pdf", "link")}
    assert _delete(env.client, [docs / name for name in before]).status_code == 200
    trashed = _trashed(env.client)

    response = _restore(env.client, [trashed[str(docs / name)] for name in before])

    assert response.status_code == 200, response.text
    assert response.json() == {"restored": [str(docs / name) for name in before]}
    assert _untrashed_tree(env.pool) == ["docs", "docs/link", "docs/old", "docs/old/notes.txt", "docs/report.pdf"]
    assert _trash(env.pool) == []
    assert _trash_listing(env.client)["items"] == []
    # The items themselves, not copies.
    for name, st in before.items():
        after = os.lstat(docs / name)
        assert (after.st_ino, after.st_mode) == (st.st_ino, st.st_mode)
    assert os.lstat(docs / "report.pdf").st_mtime_ns == before["report.pdf"].st_mtime_ns
    assert os.readlink(docs / "link") == str(env.outside)
    assert (docs / "old" / "notes.txt").read_text() == "notes"


def test_restore_never_replaces_an_entry(env, monkeypatch):
    (env.pool / "a.txt").write_text("old")
    assert _delete(env.client, [env.pool / "a.txt"]).status_code == 200
    (env.pool / "a.txt").write_text("new")
    (path,) = _trashed(env.client).values()

    response = _restore(env.client, [path])
    assert (response.status_code, response.json()["detail"]) == (400, "Already exists: a.txt")

    # Also a name that appears after it was looked for: the exclusive rename refuses it (ADR-0232).
    lstat_at = files_router._lstat_at
    monkeypatch.setattr(
        files_router, "_lstat_at", lambda dir_fd, name: None if name == "a.txt" else lstat_at(dir_fd, name)
    )
    response = _restore(env.client, [path])
    assert (response.status_code, response.json()["detail"]) == (400, "Already exists: a.txt")

    assert (env.pool / "a.txt").read_text() == "new"
    assert _trash(env.pool) == [("a.txt", "file", b"old")]


def test_a_restore_of_several_items_restores_none_when_one_cannot_go_back(env):
    for name in ("x.txt", "y.txt"):
        (env.pool / name).write_text(name)
    assert _delete(env.client, [env.pool / "x.txt", env.pool / "y.txt"]).status_code == 200
    (env.pool / "y.txt").write_text("new y")
    trashed = _trashed(env.client)

    response = _restore(env.client, [trashed[str(env.pool / "x.txt")], trashed[str(env.pool / "y.txt")]])
    assert (response.status_code, response.json()["detail"]) == (400, "Already exists: y.txt")
    assert _untrashed_tree(env.pool) == ["y.txt"]

    # Two items deleted from the same place go back one at a time.
    (env.pool / "x.txt").write_text("second x")
    assert _delete(env.client, [env.pool / "x.txt"]).status_code == 200
    both = [item["path"] for item in _trash_listing(env.client)["items"] if item["name"] == "x.txt"]
    response = _restore(env.client, both)
    assert (response.status_code, response.json()["detail"]) == (
        400, f"More than one item would go back to {env.pool / 'x.txt'}"
    )
    assert _trash(env.pool) == [("x.txt", "file", b"second x"), ("x.txt", "file", b"x.txt"), ("y.txt", "file", b"y.txt")]


def test_a_restore_that_fails_part_way_says_how_many_items_went_back_before(env, monkeypatch):
    """E.g. a name that appears after every item was checked: the exclusive rename refuses it (ADR-0232)."""
    for name in ("a.txt", "b.txt"):
        (env.pool / name).write_text(name)
    assert _delete(env.client, [env.pool / "a.txt", env.pool / "b.txt"]).status_code == 200
    trashed = _trashed(env.client)
    (env.pool / "b.txt").write_text("new b")
    lstat_at = files_router._lstat_at
    monkeypatch.setattr(
        files_router, "_lstat_at", lambda dir_fd, name: None if name == "b.txt" else lstat_at(dir_fd, name)
    )

    response = _restore(env.client, [trashed[str(env.pool / "a.txt")], trashed[str(env.pool / "b.txt")]])

    assert (response.status_code, response.json()["detail"]) == (
        400, "Already exists: b.txt. 1 of the 2 items was restored before that."
    )
    assert (env.pool / "a.txt").read_text() == "a.txt"
    assert (env.pool / "b.txt").read_text() == "new b"
    assert _trash(env.pool) == [("b.txt", "file", b"b.txt")]


def test_a_restore_into_a_folder_that_is_gone_is_refused(env):
    docs = env.pool / "docs"
    docs.mkdir()
    (docs / "a.txt").write_text("a")
    assert _delete(env.client, [docs / "a.txt"]).status_code == 200
    assert _delete(env.client, [docs]).status_code == 200
    trashed = _trashed(env.client)

    response = _restore(env.client, [trashed[str(docs / "a.txt")]])
    assert (response.status_code, response.json()["detail"]) == (409, GONE.format("a.txt", docs))

    # The folder first, then the file.
    assert _restore(env.client, [trashed[str(docs)]]).status_code == 200
    assert _restore(env.client, [trashed[str(docs / "a.txt")]]).status_code == 200
    assert _untrashed_tree(env.pool) == ["docs", "docs/a.txt"]
    assert _trash(env.pool) == []


def test_a_restore_follows_no_symlinks(env):
    docs = env.pool / "docs"
    docs.mkdir()
    (docs / "a.txt").write_text("a")
    assert _delete(env.client, [docs / "a.txt"]).status_code == 200
    docs.rmdir()
    os.symlink(env.outside, docs)
    (path,) = _trashed(env.client).values()

    response = _restore(env.client, [path])

    assert (response.status_code, response.json()["detail"]) == (403, "Access denied: symbolic links are not followed")
    assert _tree(env.outside) == ["secret.txt"]
    assert _trash(env.pool) == [("docs/a.txt", "file", b"a")]


@pytest.mark.parametrize("mount", ["another filesystem", "a bind mount"])
def test_a_restore_of_several_items_restores_none_when_a_folder_is_on_another_filesystem_now(env, monkeypatch, mount):
    """E.g. a disk was mounted on usb since: one rename cannot take its item back there (EXDEV)."""
    for folder in ("docs", "usb"):
        (env.pool / folder).mkdir()
        (env.pool / folder / f"{folder}.txt").write_text(folder)
    assert _delete(env.client, [env.pool / "docs" / "docs.txt", env.pool / "usb" / "usb.txt"]).status_code == 200
    trashed = _trashed(env.client)
    if mount == "another filesystem":
        monkeypatch.setattr(files_router, "os", _OsWithSubvolumes([env.pool / "usb"]))
    else:  # the st_dev of the pool: only the mount table shows it
        env.mounts.mount(env.pool / "usb", root="/elsewhere")
    _cross_mount_rename(monkeypatch, "usb.txt")

    response = _restore(env.client, [trashed[str(env.pool / "docs" / "docs.txt")], trashed[str(env.pool / "usb" / "usb.txt")]])

    assert (response.status_code, response.json()["detail"]) == (409, ELSEWHERE_NOW.format("usb.txt", env.pool / "usb"))
    assert _untrashed_tree(env.pool) == ["docs", "usb"]
    assert _trash(env.pool) == [("docs/docs.txt", "file", b"docs"), ("usb/usb.txt", "file", b"usb")]


@pytest.mark.parametrize(
    "origin",
    [None, b"", b"../outside/x", b"/etc/passwd", b"docs//a.txt", b"docs/./a.txt", b".unos-trash/x",
     b"docs/.unos-trash", b"a\x00b", b"n" * 256, b"x" * (64 * 1024 + 1)],
    ids=["missing", "empty", "up", "absolute", "empty component", "dot", "into the trash", "a trash",
         "NUL", "name too long", "too long"],
)
def test_a_restore_takes_no_origin_that_could_lead_elsewhere(env, origin):
    """Apps and NFS clients that use a volume can change its trash: origin is checked before every restore."""
    (env.pool / "a.txt").write_text("a")
    assert _delete(env.client, [env.pool / "a.txt"]).status_code == 200
    (entry,) = (env.pool / TRASH).iterdir()
    if origin is None:
        (entry / "origin").unlink()
    else:
        (entry / "origin").write_bytes(origin)
    (item,) = _trash_listing(env.client)["items"]
    assert (item["name"], item["original_path"]) == (entry.name, None)

    response = _restore(env.client, [item["path"]])

    assert (response.status_code, response.json()["detail"]) == (409, UNKNOWN_ORIGIN.format(item["path"]))
    assert _untrashed_tree(env.pool) == []
    assert _tree(env.outside) == ["secret.txt"]
    assert (entry / "item").read_text() == "a"
    # It can still be deleted for good.
    assert _delete_for_good(env.client, [item["path"]]).json() == {"deleted": [item["path"]]}
    assert list((env.pool / TRASH).iterdir()) == []


def test_an_origin_that_is_not_utf_8_is_listed_as_json_can_carry_it(env):
    """Names on the disk need not be UTF-8: the answers show U+FFFD for such bytes, as the messages do."""
    (env.pool / "a.txt").write_text("a")
    assert _delete(env.client, [env.pool / "a.txt"]).status_code == 200
    (entry,) = (env.pool / TRASH).iterdir()
    (entry / "origin").write_bytes(b"caf\xe9.txt")

    (item,) = _trash_listing(env.client)["items"]

    assert (item["name"], item["original_path"]) == ("caf\ufffd.txt", f"{env.pool}/caf\ufffd.txt")


@pytest.mark.skipif(sys.platform == "darwin", reason="APFS takes only UTF-8 names; the Linux test runner runs this")
def test_an_item_whose_name_is_not_utf_8_is_restored_with_its_own_bytes(env):
    (env.pool / "a.txt").write_text("a")
    assert _delete(env.client, [env.pool / "a.txt"]).status_code == 200
    (entry,) = (env.pool / TRASH).iterdir()
    (entry / "origin").write_bytes(b"caf\xe9.txt")
    (item,) = _trash_listing(env.client)["items"]

    response = _restore(env.client, [item["path"]])

    assert (response.status_code, response.json()) == (200, {"restored": [f"{env.pool}/caf\ufffd.txt"]})
    assert sorted(os.listdir(os.fsencode(env.pool))) == [TRASH.encode(), b"caf\xe9.txt"]
    with open(os.fsencode(env.pool) + b"/caf\xe9.txt", "rb") as restored:
        assert restored.read() == b"a"


@pytest.mark.parametrize(
    "template",
    [
        "{pool}/a.txt",
        "{pool}/.unos-trash",
        "{pool}/.unos-trash/not-an-entry",
        "{pool}/.unos-trash/{entry}/item",
        "{pool}/.unos-trash/{entry}/origin",
        "{pool}/docs/.unos-trash/{entry}",  # docs is not a volume: UNOS keeps no trash there
        "{pool}/docs/sub/.unos-trash/{entry}",
        "{base}/.unos-trash/{entry}",
    ],
)
def test_the_trash_takes_only_paths_of_its_items(env, template):
    (env.pool / "a.txt").write_text("a")
    assert _delete(env.client, [env.pool / "a.txt"]).status_code == 200
    (entry,) = (env.pool / TRASH).iterdir()
    # A trash that UNOS did not make, in an ordinary folder: never listed.
    fake = env.pool / "docs" / TRASH / entry.name
    fake.mkdir(parents=True)
    (fake / "item").write_text("planted")
    (fake / "origin").write_text("a.txt")
    path = template.format(pool=env.pool, base=env.base, entry=entry.name)

    for response in (_restore(env.client, [path]), _delete_for_good(env.client, [path])):
        assert (response.status_code, response.json()["detail"]) == (400, f"Not an item in the trash: {path}")
    assert len(_trash_listing(env.client)["items"]) == 1
    assert _trash(env.pool) == [("a.txt", "file", b"a")]
    assert (fake / "item").read_text() == "planted"


@pytest.mark.parametrize("template", ["{root}/outside/.unos-trash/{entry}", "{pool}/../outside/.unos-trash/{entry}"])
def test_the_trash_takes_no_paths_outside_the_pools(env, template):
    path = template.format(root=env.root, pool=env.pool, entry="20261107T120000Z-0123456789abcdef")
    for response in (_restore(env.client, [path]), _delete_for_good(env.client, [path])):
        assert (response.status_code, response.json()["detail"]) == (403, "Access denied")


def test_an_item_that_is_no_longer_in_the_trash(env):
    path = env.pool / TRASH / "20261107T120000Z-0123456789abcdef"
    response = _restore(env.client, [path])
    assert (response.status_code, response.json()["detail"]) == (404, f"Not in the trash: {path}")
    response = _delete_for_good(env.client, [path])
    assert (response.status_code, response.json()) == (200, {"deleted": []})


# Step 3: they are gone for good after 30 days, or when an administrator deletes them.


def test_deleted_items_are_deleted_for_good_30_days_after_their_deletion(env, clock):
    for name in ("old.txt", "new.txt"):
        (env.pool / name).write_text(name)
    assert _delete(env.client, [env.pool / "old.txt"]).status_code == 200
    clock.now = T0 + timedelta(days=29)
    assert _delete(env.client, [env.pool / "new.txt"]).status_code == 200
    # An entry whose item never came (a stop right after UNOS made its private folder), and a folder UNOS did
    # not make.
    incomplete = env.pool / TRASH / "20261001T000000Z-0123456789abcdef"
    incomplete.mkdir(mode=0o700)
    (incomplete / "origin").write_text("gone.txt")
    (env.pool / TRASH / "keep-me").mkdir()

    assert files_router._expire_trash(T0 + timedelta(days=30, seconds=-1)) == (1, [])
    assert [item["name"] for item in _trash_listing(env.client)["items"]] == ["new.txt", "old.txt"]
    assert files_router._expire_trash(T0 + timedelta(days=30)) == (1, [])
    assert [item["name"] for item in _trash_listing(env.client)["items"]] == ["new.txt"]
    assert files_router._expire_trash(T0 + timedelta(days=59)) == (1, [])
    assert sorted(p.name for p in (env.pool / TRASH).iterdir()) == ["keep-me"]
    assert _untrashed_tree(env.pool) == []


def test_an_administrator_deletes_items_for_good_or_empties_the_trash(env, volume):
    for name in ("a.txt", "b.txt"):
        (env.pool / name).write_text(name)
    img = volume / "album" / "img.jpg"
    assert _delete(env.client, [env.pool / "a.txt", env.pool / "b.txt", img]).status_code == 200
    trashed = _trashed(env.client)

    response = _delete_for_good(env.client, [trashed[str(env.pool / "a.txt")]])
    assert (response.status_code, response.json()) == (200, {"deleted": [trashed[str(env.pool / "a.txt")]]})
    assert _trash(env.pool) == [("b.txt", "file", b"b.txt")]
    assert _trash(volume) == [("album/img.jpg", "file", b"jpeg")]

    response = _empty(env.client)
    assert (response.status_code, response.json()) == (200, {"deleted": 2})
    assert (_trash(env.pool), _trash(volume)) == ([], [])
    assert _trash_listing(env.client)["items"] == []
    assert _untrashed_tree(env.pool) == ["photos", f"photos/{TRASH}", "photos/album"]  # the volume's trash, empty


def test_deleting_for_good_keeps_the_protections_of_volumes_and_mounts(env, clock):
    """A mount point below an item in the trash (an app can make one there): nothing is deleted into it."""
    (env.pool / "folder" / "disk").mkdir(parents=True)
    (env.pool / "folder" / "disk" / "f.txt").write_text("f")
    assert _delete(env.client, [env.pool / "folder"]).status_code == 200
    (entry,) = (env.pool / TRASH).iterdir()
    env.mounts.mount(entry / "item" / "disk")
    (path,) = _trashed(env.client).values()
    detail = CONTAINS.format(entry.name, "item/disk", "deleted for good")

    for response in (_delete_for_good(env.client, [path]), _empty(env.client)):
        assert (response.status_code, response.json()["detail"]) == (409, detail)
    assert files_router._expire_trash(T0 + timedelta(days=30)) == (0, [f"{path}: {detail}"])
    assert (entry / "item" / "disk" / "f.txt").read_text() == "f"


def test_emptying_the_trash_deletes_deep_trees_without_following_symlinks(env, monkeypatch):
    folder = env.pool / "tree"
    deep = folder.joinpath(*[f"level{n}" for n in range(60)])
    deep.mkdir(parents=True)
    (deep / "leaf.txt").write_text("leaf")
    os.symlink(folder, folder / "level0" / "loop")  # a cycle if it were followed
    os.symlink(env.outside, folder / "outside")
    (env.pool / "usb").mkdir()
    (env.pool / "usb" / "photo.jpg").write_bytes(b"jpeg")
    env.mounts.mount(env.pool / "usb")
    os.symlink(env.pool / "usb", folder / "usb-link")
    monkeypatch.setattr(files_router, "os", _OsWithSubvolumes([env.pool / "usb", env.outside]))
    assert _delete(env.client, [folder]).status_code == 200

    response = _empty(env.client)

    assert (response.status_code, response.json()) == (200, {"deleted": 1})
    assert _tree(env.pool) == [TRASH, "usb", "usb/photo.jpg"]
    assert _tree(env.outside) == ["secret.txt"]


def test_the_expiry_runs_at_the_start_and_then_once_a_day(monkeypatch, clock):
    assert files_router.TRASH_EXPIRY_INTERVAL == 24 * 60 * 60
    passes = []

    def expire(now):
        passes.append((now, threading.current_thread() is threading.main_thread()))
        return 0, []

    monkeypatch.setattr(files_router, "_expire_trash", expire)

    async def run():
        task = asyncio.create_task(files_router.trash_expiry_loop())
        for _ in range(500):
            if passes:
                break
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.2)  # no second pass before a day has gone by
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())
    # At once, in a worker thread, with the supervisor's clock.
    assert passes == [(T0, False)]


def test_the_expiry_logs_what_it_deleted_and_each_new_problem(monkeypatch, caplog):
    results = [(2, []), (0, []), (0, ["P"]), (0, ["P"]), OSError(errno.EIO, "Input/output error"), (0, []), (0, [])]
    calls = []

    def expire(now):
        calls.append(now)
        result = results[min(len(calls), len(results)) - 1]
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(files_router, "_expire_trash", expire)
    monkeypatch.setattr(files_router, "TRASH_EXPIRY_INTERVAL", 0)

    async def run():
        task = asyncio.create_task(files_router.trash_expiry_loop())
        while len(calls) <= len(results):
            await asyncio.sleep(0.001)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    with caplog.at_level(logging.INFO, logger="src.files.router"):
        asyncio.run(run())

    assert [(r.levelno, r.getMessage()) for r in caplog.records if r.name == "src.files.router"] == [
        (logging.INFO, "Deleted 2 item(s) for good that had been in the trash for 30 days"),
        (logging.WARNING, "The trash could not be cleared out completely: P"),
        (logging.WARNING, "The trash could not be cleared out completely: [Errno 5] Input/output error"),
    ]
