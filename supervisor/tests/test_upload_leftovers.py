"""Hidden upload files that a stop of the supervisor left behind (files.upload-edge-cases, ADR-0354).

Where a folder's filesystem has no unnamed files (vfat, exfat, NFS, SMB or FUSE mounted by hand
below the pools; macOS), a file manager upload arrives in a hidden .unos-upload-<16 hex digits>
file, which a stop of the supervisor in the middle leaves behind (ADR-0169). The supervisor looks
for such files in every folder below the pools directory, UPLOAD_LEFTOVER_AGE seconds after its
start and on request, lists them with their size and last change, and removes them for good only
when an administrator asks it to. It never lists or removes the file of an upload in progress:
not one of its own uploads (the set of their hidden names), and not one changed in the last
UPLOAD_LEFTOVER_AGE seconds.
"""

import asyncio
import importlib
import logging
import os
import socket
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from src.auth.utils import verify_token
from src.main import app

from .test_upload_streaming import END, MIB, SUPERVISOR, _connect, _part_head, _wait_for

files_router = importlib.import_module("src.files.router")

ADMIN = SimpleNamespace(id=1, username="admin", is_admin=True)
API = "/api/v1/files/upload-leftovers"
# The supervisor's clock in these tests, and an hour before it.
NOW = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)
HOUR_AGO = NOW.timestamp() - 3600
NOTHING_CHECKED = {"files": [], "checked": None, "checking": False, "problems": []}


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A pools directory with the pool data, a folder outside it, and an administrator's client.

    The check starts with nothing found, and the clock stands at NOW.
    """
    root = tmp_path.resolve()
    base = root / "pools"
    pool = base / "data"
    pool.mkdir(parents=True)
    outside = root / "outside"
    outside.mkdir()
    monkeypatch.setattr(files_router, "BASE_PATH", str(base))
    monkeypatch.setattr(files_router, "_now", lambda: NOW)
    monkeypatch.setattr(files_router, "_leftovers", files_router._LeftoverCheck())
    app.dependency_overrides[verify_token] = lambda: ADMIN
    try:
        yield SimpleNamespace(root=root, base=base, pool=pool, outside=outside, client=TestClient(app))
    finally:
        app.dependency_overrides.pop(verify_token, None)


def _hidden(folder: Path, digits: str = "0123456789abcdef", size: int = 3, mtime: float = HOUR_AGO) -> Path:
    """A hidden upload file in ``folder``, of ``size`` bytes, last changed at ``mtime``."""
    path = folder / f".unos-upload-{digits}"
    path.write_bytes(b"x" * size)
    os.utime(path, (mtime, mtime))
    return path


def _check(env) -> dict:
    response = env.client.post(f"{API}/check")
    assert response.status_code == 200, response.text
    return response.json()


def _paths(answer: dict) -> list[str]:
    return [file["path"] for file in answer["files"]]


def _remove(env, paths: list[Path | str]):
    return env.client.request("DELETE", API, json=[str(path) for path in paths])


# --- finding them ----------------------------------------------------------------------


def test_the_check_finds_hidden_upload_files_in_every_folder_with_size_and_age(env):
    deep = env.pool / "photos" / "2026" / "usb"
    deep.mkdir(parents=True)
    other_pool = env.base / "backup"
    other_pool.mkdir()
    top = _hidden(env.pool, "0123456789abcdef", size=5)
    nested = _hidden(deep, "fedcba9876543210", size=7, mtime=HOUR_AGO - 24 * 3600)
    empty = _hidden(other_pool, "00000000000000ff", size=0)
    # Unchanged for exactly UPLOAD_LEFTOVER_AGE seconds: no upload writes it any more.
    just_old = _hidden(env.pool, "1111111111111111", size=1, mtime=NOW.timestamp() - files_router.UPLOAD_LEFTOVER_AGE)
    assert files_router.UPLOAD_LEFTOVER_AGE == 10 * 60

    # Nothing is known before the first check.
    assert env.client.get(API).json() == NOTHING_CHECKED
    answer = _check(env)

    assert answer == {
        "files": [
            {"path": str(empty), "size": 0, "modified": "2026-11-12T11:00:00+00:00"},
            {"path": str(top), "size": 5, "modified": "2026-11-12T11:00:00+00:00"},
            {"path": str(just_old), "size": 1, "modified": "2026-11-12T11:50:00+00:00"},
            {"path": str(nested), "size": 7, "modified": "2026-11-11T11:00:00+00:00"},
        ],
        "checked": NOW.isoformat(),
        "checking": False,
        "problems": [],
    }
    # GET answers what the last check found, without walking the pools again.
    assert env.client.get(API).json() == answer
    assert sorted(os.listdir(env.pool)) == [top.name, just_old.name, "photos"]


def test_the_check_leaves_out_what_is_not_a_leftover_of_an_upload(env):
    for name in [
        ".unos-upload-0123",
        ".unos-upload-0123456789ABCDEF",
        ".unos-upload-0123456789abcdef0",
        ".unos-upload-0123456789abcdeg",
        "unos-upload-0123456789abcdef",
        "report.pdf",
    ]:
        path = env.pool / name
        path.write_bytes(b"x")
        os.utime(path, (HOUR_AGO, HOUR_AGO))
    (env.pool / ".unos-upload-1111111111111111").mkdir()
    _hidden(env.outside, "2222222222222222")
    os.symlink(env.outside / ".unos-upload-2222222222222222", env.pool / ".unos-upload-2222222222222222")
    # A symbolic link to a folder is not followed: the folder outside is not checked.
    os.symlink(env.outside, env.pool / "elsewhere")
    # The trash (its items go with it) and snapshots (read-only copies) are not checked.
    for folder in [
        env.pool / ".unos-trash" / "20261101T000000Z-0123456789abcdef",
        env.pool / ".snapshots" / "daily_20261101",
        env.pool / "photos" / ".snapshots" / "daily_20261101",
    ]:
        folder.mkdir(parents=True)
        _hidden(folder, "3333333333333333")
    # Changed less than UPLOAD_LEFTOVER_AGE seconds ago: an upload may still be writing it.
    young = _hidden(env.pool, "4444444444444444", mtime=NOW.timestamp() - files_router.UPLOAD_LEFTOVER_AGE + 1)

    answer = _check(env)

    assert answer["files"] == [] and answer["problems"] == []
    assert young.exists()


def test_a_folder_that_cannot_be_read_is_reported_and_the_others_are_checked(env):
    if os.geteuid() == 0:
        pytest.skip("root reads a folder of mode 000")
    locked = env.pool / "locked"
    locked.mkdir()
    _hidden(locked, "fedcba9876543210")
    found = _hidden(env.pool)
    locked.chmod(0)
    try:
        answer = _check(env)
    finally:
        locked.chmod(0o755)

    assert _paths(answer) == [str(found)]
    assert answer["problems"] == [f"{locked} could not be read: Permission denied"]


def test_a_file_removed_since_the_check_is_no_longer_listed(env):
    gone = _hidden(env.pool, "0123456789abcdef")
    kept = _hidden(env.pool, "fedcba9876543210")
    assert _paths(_check(env)) == [str(gone), str(kept)]

    gone.unlink()
    # Changed meanwhile (written again): no longer old enough to be a leftover.
    os.utime(kept, (NOW.timestamp(), NOW.timestamp()))

    assert env.client.get(API).json()["files"] == []


def test_the_first_check_runs_upload_leftover_age_after_the_start_and_logs_what_it_found(env, monkeypatch, caplog):
    found = _hidden(env.pool)
    monkeypatch.setattr(files_router, "UPLOAD_LEFTOVER_AGE", 0.3)

    async def run():
        task = asyncio.create_task(files_router.first_upload_leftover_check())
        await asyncio.sleep(0.1)
        assert files_router._leftovers.checked is None, "checked at once"
        await asyncio.wait_for(task, 10)

    with caplog.at_level(logging.INFO, logger="src.files.router"):
        asyncio.run(run())

    assert _paths(env.client.get(API).json()) == [str(found)]
    assert [(r.levelno, r.getMessage()) for r in caplog.records if r.name == "src.files.router"] == [
        (
            logging.WARNING,
            "Found 1 hidden file(s) that uploads left when the supervisor stopped; "
            "the File Browser offers to remove them",
        ),
    ]


def test_the_first_check_logs_nothing_when_it_finds_nothing(env, monkeypatch, caplog):
    monkeypatch.setattr(files_router, "UPLOAD_LEFTOVER_AGE", 0)
    with caplog.at_level(logging.INFO, logger="src.files.router"):
        asyncio.run(files_router.first_upload_leftover_check())
    assert files_router._leftovers.checked == NOW
    assert [r for r in caplog.records if r.name == "src.files.router"] == []


def test_a_check_asked_for_while_one_runs_waits_for_that_one(env, monkeypatch):
    calls = []
    release = threading.Event()

    def find(now):
        calls.append(threading.current_thread() is threading.main_thread())
        assert release.wait(10)
        return [], []

    monkeypatch.setattr(files_router, "_find_upload_leftovers", find)

    async def run():
        first = asyncio.create_task(files_router.check_upload_leftovers())
        await asyncio.sleep(0.05)
        assert (await files_router.list_upload_leftovers(_=ADMIN)).checking is True
        second = asyncio.create_task(files_router.check_upload_leftovers())
        await asyncio.sleep(0.05)
        release.set()
        await asyncio.wait_for(asyncio.gather(first, second), 10)
        assert (await files_router.list_upload_leftovers(_=ADMIN)).checking is False

    asyncio.run(run())
    # One walk of the pools, in a worker thread.
    assert calls == [False]


# --- uploads in progress -------------------------------------------------------------


def test_the_file_of_an_upload_in_progress_is_neither_listed_nor_removed(env, monkeypatch):
    monkeypatch.setattr(files_router, "_UNNAMED_FLAGS", None)  # the hidden file, as on vfat
    sink = files_router._FileUpload(str(env.pool))
    try:
        sink.prepare()
        fd = sink.open("movie.bin")
        try:
            os.write(fd, b"x" * 1024)
            (hidden,) = env.pool.glob(".unos-upload-*")
            # As old as a leftover, for example after NTP moved the clock forward during the upload.
            os.utime(hidden, (HOUR_AGO, HOUR_AGO))

            assert _check(env)["files"] == []
            response = _remove(env, [hidden])
            assert (response.status_code, response.json()) == (
                409,
                {"detail": f"Nothing was removed: {hidden} is the file of an upload in progress."},
            )
            assert hidden.exists()
        finally:
            os.close(fd)
        sink.finish()
    finally:
        sink.discard()
    assert sorted(os.listdir(env.pool)) == ["movie.bin"]
    assert files_router._uploads_in_progress == set()


def test_the_hidden_name_is_known_before_the_file_exists_and_until_it_is_gone(env, monkeypatch):
    """Also the hidden name that a complete unnamed file takes to replace a file (Linux)."""
    (env.pool / "movie.bin").write_bytes(b"old")
    seen = []
    real_open, real_rename, real_unlink = os.open, os.rename, os.unlink

    def watch(call, name):
        if isinstance(name, str) and name.startswith(".unos-upload-"):
            seen.append((call, name in files_router._uploads_in_progress))

    def opened(path, *args, **kwargs):
        watch("open", path)
        return real_open(path, *args, **kwargs)

    def renamed(src, dst, *args, **kwargs):
        watch("rename", src)
        return real_rename(src, dst, *args, **kwargs)

    def unlinked(path, *args, **kwargs):
        watch("unlink", path)
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", opened)
    monkeypatch.setattr(os, "rename", renamed)
    monkeypatch.setattr(os, "unlink", unlinked)
    response = env.client.post(
        "/api/v1/files/upload", params={"path": str(env.pool)}, files={"file": ("movie.bin", b"new")}
    )
    assert response.status_code == 200, response.text

    assert (env.pool / "movie.bin").read_bytes() == b"new"
    assert ("rename", True) in seen and all(known for _, known in seen), seen
    assert files_router._uploads_in_progress == set()


def test_a_failed_upload_frees_its_hidden_name(env, monkeypatch):
    monkeypatch.setattr(files_router, "_UNNAMED_FLAGS", None)
    sink = files_router._FileUpload(str(env.pool))
    sink.prepare()
    os.close(sink.open("movie.bin"))
    assert len(files_router._uploads_in_progress) == 1
    sink.discard()
    assert files_router._uploads_in_progress == set()
    assert os.listdir(env.pool) == []


# --- removing them -------------------------------------------------------------------


def test_remove_deletes_the_files_for_good_and_nothing_else(env):
    first = _hidden(env.pool, "0123456789abcdef")
    usb = env.pool / "usb"
    usb.mkdir()
    second = _hidden(usb, "fedcba9876543210")
    (usb / "photo.jpg").write_bytes(b"jpeg")
    assert _paths(_check(env)) == [str(first), str(second)]

    response = _remove(env, [first, second])

    assert (response.status_code, response.json()) == (200, {"removed": [str(first), str(second)]})
    assert not first.exists() and not second.exists()
    assert sorted(os.listdir(env.pool)) == ["usb"]
    assert os.listdir(usb) == ["photo.jpg"]
    assert env.client.get(API).json()["files"] == []


def test_remove_skips_a_file_that_is_already_gone(env):
    gone = env.pool / ".unos-upload-0123456789abcdef"
    response = _remove(env, [gone])
    assert (response.status_code, response.json()) == (200, {"removed": []})


@pytest.mark.parametrize(
    "case",
    ["another-file", "a-folder", "changed-recently", "outside-the-pools", "in-the-trash", "a-symlink"],
)
def test_remove_refuses_what_is_not_a_leftover_and_removes_nothing(env, case):
    leftover = _hidden(env.pool, "0123456789abcdef")
    name = ".unos-upload-fedcba9876543210"
    if case == "another-file":
        target = env.pool / "report.pdf"
        target.write_bytes(b"pdf")
        expected = (400, f"Not the hidden file of an upload: {target}")
    elif case == "a-folder":
        target = env.pool / name
        target.mkdir()
        expected = (409, f"Nothing was removed: {target} is not a file.")
    elif case == "changed-recently":
        target = _hidden(env.pool, name.removeprefix(".unos-upload-"), mtime=NOW.timestamp() - 60)
        expected = (
            409,
            f"Nothing was removed: {target} changed less than 10 minutes ago, so an upload may still be "
            "writing it.",
        )
    elif case == "outside-the-pools":
        target = _hidden(env.outside, name.removeprefix(".unos-upload-"))
        expected = (403, "Access denied")
    elif case == "in-the-trash":
        trash = env.pool / ".unos-trash" / "20261101T000000Z-0123456789abcdef"
        trash.mkdir(parents=True)
        target = _hidden(trash, name.removeprefix(".unos-upload-"))
        expected = (403, "The trash (.unos-trash) is reached through Trash, not as a folder")
    else:
        old = _hidden(env.outside, name.removeprefix(".unos-upload-"))
        target = env.pool / name
        os.symlink(old, target)
        expected = (409, f"Nothing was removed: {target} is not a file.")

    response = _remove(env, [leftover, target])

    assert (response.status_code, response.json()) == (expected[0], {"detail": expected[1]})
    assert leftover.exists()
    assert os.path.lexists(target)


# --- a supervisor that stops in the middle of an upload ---------------------------------

# The application in uvicorn, in a process of its own, as in test_upload_streaming.py, but with
# the hidden file of a filesystem without unnamed files (vfat, exfat) on every platform: the pools
# base is argv[1], the listening socket the descriptor argv[2].
_SERVER = """
import importlib, socket, sys
from types import SimpleNamespace
import uvicorn
from src.auth.utils import verify_token
from src.main import app
files = importlib.import_module("src.files.router")
files.BASE_PATH = sys.argv[1]
files._UNNAMED_FLAGS = None
app.dependency_overrides[verify_token] = lambda: SimpleNamespace(id=1, username="admin", is_admin=True)
config = uvicorn.Config(app, http="httptools", lifespan="off", log_level="warning")
uvicorn.Server(config).run(sockets=[socket.socket(fileno=int(sys.argv[2]))])
"""


def test_the_hidden_file_of_a_killed_supervisor_is_found_and_removed(env, tmp_path, monkeypatch):
    """SIGKILL, as Docker sends 10 s after docker stop, with 4 MiB of 16 sent."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    with (tmp_path / "server.log").open("wb") as log:
        server = subprocess.Popen(
            [sys.executable, "-B", "-c", _SERVER, str(env.base), str(listener.fileno())],
            cwd=SUPERVISOR,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            pass_fds=[listener.fileno()],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
        )
    listener.close()
    conn = None
    try:

        def accepts() -> bool:
            assert server.poll() is None, (tmp_path / "server.log").read_text()
            try:
                socket.create_connection(("127.0.0.1", port), timeout=1).close()
                return True
            except OSError:
                return False

        _wait_for(accepts, "the server accepts connections", timeout=60)
        head, tail = _part_head(b"file", b"movie.bin"), b"\r\n" + END
        url = f"/api/v1/files/upload?{urlencode({'path': str(env.pool)})}"
        conn = _connect(port, url, len(head) + 16 * MIB + len(tail))
        conn.send(head)
        conn.send(b"x" * 4 * MIB)

        def arrived() -> int:
            return sum(path.stat().st_size for path in env.pool.glob(".unos-upload-*"))

        # The parser holds back the end of what arrived, in case it is the start of the boundary.
        _wait_for(lambda: arrived() >= 3 * MIB, "the hidden file of the upload holds data", timeout=30)
        server.kill()
        server.wait(10)
    finally:
        if server.poll() is None:
            server.kill()
            server.wait(10)
        if conn is not None:
            conn.close()
    (left,) = env.pool.iterdir()
    # The next supervisor checks UPLOAD_LEFTOVER_AGE after its start, or when asked.
    monkeypatch.setattr(files_router, "_now", lambda: datetime.now(timezone.utc).replace(year=2100))

    answer = _check(env)

    assert [(file["path"], file["size"]) for file in answer["files"]] == [(str(left), left.stat().st_size)]
    assert _remove(env, [left]).json() == {"removed": [str(left)]}
    assert os.listdir(env.pool) == []
