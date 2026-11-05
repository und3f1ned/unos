"""Security tests for the file manager API (path traversal, symlinks, uploads)."""

import errno
import importlib
import io
import os
import stat
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.auth.utils import verify_token
from src.main import app

# The package re-exports its APIRouter as ``router``; import the module itself.
files_router = importlib.import_module("src.files.router")

ADMIN = SimpleNamespace(id=1, username="admin", is_admin=True)
USER = SimpleNamespace(id=2, username="user", is_admin=False)


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

    monkeypatch.setattr(files_router, "BASE_PATH", str(base))
    app.dependency_overrides[verify_token] = lambda: ADMIN
    client = TestClient(app)
    yield SimpleNamespace(client=client, root=root, base=base, pool=pool, outside=outside)
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
