"""Uploads go straight into their destination file as they arrive (src/request_limits.py).

Starlette's request.form() spooled every uploaded file over 1 MiB to a
temporary file of the tempfile module in /tmp, which in the supervisor
container is on the system disk (an SD card), before the endpoints copied it
into the pool or the backup directory. The uploads now stream the file into
its destination (receive_upload). These tests send real multipart bodies,
through the application and through uvicorn, with every temporary file and
directory of the tempfile module refused (no_temporary_files), and check that:

* the data reaches the destination file while the body is still arriving,
  written in a worker thread at most about 1 MiB at a time, and the request
  is not read while a write is pending;
* the memory of the server does not grow with the size of the upload;
* the folder is checked before the body is read, and the file name (or the
  backup extension) before any of the file is written, with the answers of
  before;
* every other refusal and error answers as before and leaves no temporary
  file and nothing under a final name: also a client that goes away, a full
  disk, a cancelled request and a failed check.

On Linux the file of a file upload is unnamed in its folder until it is
complete (O_TMPFILE, src/files/router.py): nothing of it is in the folder,
and a supervisor that stops in the middle leaves nothing there. Where there
cannot be an unnamed file (macOS, some filesystems), it is a hidden
temporary file. The helpers see both (_temporary_files), so the tests check
the unnamed file on Linux and the named one elsewhere; the tests of the
``creation`` fixture check each where it can be. After every test of ``env``
no upload may be left unfinished: no temporary file, and no unnamed file
still open (it would keep its space until the supervisor stops).
"""

import asyncio
import errno
import http.client
import importlib
import json
import logging
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import tracemalloc
from pathlib import Path
from types import SimpleNamespace
from typing import NamedTuple
from urllib.parse import urlencode

import pytest
import starlette.formparsers
from fastapi.testclient import TestClient

from src.auth.utils import verify_token
from src.main import app
from src.shares import constants

from .test_backup_restore import MANIFEST, _file, _tar_gz
from .test_request_limits import _scope, _uvicorn

files_router = importlib.import_module("src.files.router")
backup_router = importlib.import_module("src.backup.router")
request_limits = importlib.import_module("src.request_limits")

SUPERVISOR = Path(__file__).resolve().parents[1]
MIB = 1024 * 1024
ADMIN = SimpleNamespace(id=1, username="admin", is_admin=True)
BOUNDARY = b"unos-streaming-test-0f9c2d"
CONTENT_TYPE = b"multipart/form-data; boundary=" + BOUNDARY
END = b"--" + BOUNDARY + b"--\r\n"
NO_FILE = "No file was sent: send it as multipart/form-data in the field 'file'"
INTERRUPTED = "The upload was interrupted: the client closed the connection"


@pytest.fixture(autouse=True)
def no_temporary_files(monkeypatch):
    """Refuse every temporary file and directory of the tempfile module: no upload may need one.

    starlette.formparsers imported SpooledTemporaryFile by name: it is
    replaced there too. The check of a backup upload writes no temporary
    file either (tests/test_backup_upload_check.py).
    """
    calls: list[str] = []

    def refused(name):
        def make(*args, **kwargs):
            calls.append(name)
            raise AssertionError(f"tempfile.{name}() was called during an upload")

        return make

    for name in ("SpooledTemporaryFile", "TemporaryFile", "NamedTemporaryFile", "mkstemp",
                 "TemporaryDirectory", "mkdtemp"):
        monkeypatch.setattr(tempfile, name, refused(name))
    monkeypatch.setattr(starlette.formparsers, "SpooledTemporaryFile", refused("SpooledTemporaryFile"))
    yield calls
    assert calls == []


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A pool folder and a backup directory, and an administrator's client.

    Afterwards, whatever the test did, no upload is left unfinished.
    """
    root = tmp_path.resolve()
    pool = root / "pools" / "data"
    pool.mkdir(parents=True)
    backups = root / "backups"
    backups.mkdir()
    monkeypatch.setattr(files_router, "BASE_PATH", str(root / "pools"))
    monkeypatch.setattr(backup_router, "BACKUP_DIR", backups)
    app.dependency_overrides[verify_token] = lambda: ADMIN
    env = SimpleNamespace(root=root, base=root / "pools", pool=pool, backups=backups, client=TestClient(app))
    try:
        yield env
    finally:
        app.dependency_overrides.pop(verify_token, None)
    assert _temporary_files(env) == [], "an upload left a temporary file, or an unnamed file open"


def _unnamed_files(folder: Path, pid: int | str = "self") -> dict[tuple[int, int], int]:
    """The files the process ``pid`` has open unnamed (O_TMPFILE) in ``folder``: their sizes by device and inode.

    Linux shows the descriptor of such a file as "<folder>/#<inode>
    (deleted)". An upload holds two descriptors of its file (the sink's and
    receive_upload's): each file is counted once. Without /proc (macOS):
    none.
    """
    fds = f"/proc/{pid}/fd"
    try:
        numbers = os.listdir(fds)
    except FileNotFoundError:
        return {}
    prefix = os.path.realpath(folder) + "/#"
    found = {}
    for number in numbers:
        try:
            link = os.readlink(f"{fds}/{number}")
            st = os.stat(f"{fds}/{number}")
        except OSError:  # closed meanwhile
            continue
        if stat.S_ISREG(st.st_mode) and link == f"{prefix}{st.st_ino} (deleted)":
            found[(st.st_dev, st.st_ino)] = st.st_size
    return found


class _Unfinished(NamedTuple):
    """The destination file of an upload that is not finished."""

    name: str | None  # None: unnamed, open in this process (Linux)
    size: int
    key: tuple[int, int]  # st_dev, st_ino


def _temporary_files(env) -> list[_Unfinished]:
    """The destination files of uploads that are not finished: being written, or left behind.

    The hidden temporary files in the pool and in the backup directory, and
    on Linux the files this process has open unnamed in either.
    """
    found: dict[tuple[int, int], _Unfinished] = {}
    for path in [*env.pool.glob(".unos-upload-*"), *env.backups.glob(".upload-*.tmp")]:
        try:
            st = path.stat()
        except FileNotFoundError:
            continue
        key = (st.st_dev, st.st_ino)
        found[key] = _Unfinished(path.name, st.st_size, key)
    for folder in (env.pool, env.backups):
        for key, size in _unnamed_files(folder).items():
            found.setdefault(key, _Unfinished(None, size, key))
    return sorted(found.values(), key=lambda file: (file.name or "", file.key))


def _temporary_bytes(env) -> int:
    return sum(file.size for file in _temporary_files(env))


def _can_be_unnamed(folder: Path) -> bool:
    """Whether a file can be created unnamed in ``folder`` and reached through /proc/self/fd, as an upload's is."""
    flags = getattr(os, "O_TMPFILE", None)
    if flags is None:
        return False
    try:
        fd = os.open(folder, flags | os.O_WRONLY | os.O_CLOEXEC, 0o600)
    except OSError:
        return False
    try:
        return os.path.samestat(os.stat(f"/proc/self/fd/{fd}"), os.fstat(fd))
    except OSError:
        return False
    finally:
        os.close(fd)


@pytest.fixture(params=["unnamed", "named"])
def creation(request, env, monkeypatch):
    """How the file of a file upload is created: unnamed (Linux), or as a hidden temporary file (the fallback)."""
    if request.param == "named":
        monkeypatch.setattr(files_router, "_UNNAMED_FLAGS", None)
    elif not _can_be_unnamed(env.pool):
        pytest.skip("no unnamed files here (O_TMPFILE): not Linux, or a filesystem without them")
    return request.param


class _Target:
    """One of the two upload endpoints, with a file it accepts."""

    def __init__(self, kind: str, env):
        self.kind = kind
        self.env = env
        if kind == "files":
            self.path = "/api/v1/files/upload"
            self.query = urlencode({"path": str(env.pool)})
            self.filename = b"big.bin"
            self.folder = env.pool
        else:
            self.path = "/api/v1/backup/upload"
            self.query = ""
            # Encrypted backups are only checked for their length here.
            self.filename = b"mine.tar.gz.enc"
            self.folder = env.backups

    @property
    def url(self) -> str:
        return f"{self.path}?{self.query}" if self.query else self.path

    def scope(self, content_type: bytes | None = CONTENT_TYPE, query: str | None = None) -> dict:
        headers = [] if content_type is None else [(b"content-type", content_type)]
        return _scope(self.path, headers, query=(self.query if query is None else query).encode())

    def stored(self) -> list[str]:
        """What is in the destination folder, temporary files included."""
        return sorted(path.name for path in self.folder.iterdir())

    def stored_content(self) -> bytes:
        """The content of the uploaded file, which must be the only file there."""
        (name,) = self.stored()
        if self.kind == "files":
            assert name == "big.bin"
        else:
            assert re.fullmatch(r"uploaded_\d{8}_\d{6}\.tar\.gz\.enc", name), name
        return (self.folder / name).read_bytes()


@pytest.fixture(params=["files", "backup"])
def target(request, env):
    return _Target(request.param, env)


def _part_head(name: bytes, filename: bytes | None = None, *headers: bytes) -> bytes:
    """The boundary and the headers of a part, up to its data."""
    disposition = b'Content-Disposition: form-data; name="' + name + b'"'
    if filename is not None:
        disposition += b'; filename="' + filename + b'"'
    return b"--" + BOUNDARY + b"\r\n" + b"\r\n".join([disposition, *headers]) + b"\r\n\r\n"


def _part(name: bytes, data: bytes = b"", filename: bytes | None = None, *headers: bytes) -> bytes:
    return _part_head(name, filename, *headers) + data + b"\r\n"


def _pieces(data: bytes, size: int = 256 * 1024) -> list[bytes]:
    return [data[start : start + size] for start in range(0, len(data), size)]


class _Sender:
    """An ASGI client: sends the body in the given chunks, records what the application read and answered.

    ``on_read(n)`` runs when the application asks for the chunk ``n`` (from
    0), before it gets it. After the chunks, ``then`` is "end" (the last
    chunk ends the body), "disconnect" (the client goes away instead) or
    "block" (the client sends nothing more).
    """

    def __init__(self, chunks: list[bytes], *, on_read=None, then: str = "end"):
        self.chunks = chunks
        self.on_read = on_read
        self.then = then
        self.reads = 0
        self.messages: list[dict] = []

    async def receive(self) -> dict:
        n = self.reads
        self.reads += 1
        if self.on_read is not None:
            self.on_read(n)
        if n < len(self.chunks):
            more = n + 1 < len(self.chunks) or self.then != "end"
            return {"type": "http.request", "body": self.chunks[n], "more_body": more}
        if self.then == "block":
            await asyncio.Event().wait()
        return {"type": "http.disconnect"}

    async def send(self, message: dict) -> None:
        self.messages.append(message)

    @property
    def status(self) -> int:
        return next(m["status"] for m in self.messages if m["type"] == "http.response.start")

    @property
    def json(self):
        return json.loads(b"".join(m.get("body", b"") for m in self.messages if m["type"] == "http.response.body"))


def _wait_for(condition, what: str, timeout: float = 10.0):
    deadline = time.monotonic() + timeout
    while not (result := condition()):
        assert time.monotonic() < deadline, f"timed out waiting until {what}"
        time.sleep(0.01)
    return result


def _is_destination(env, fd: int) -> bool:
    """Whether ``fd`` is the destination file of an upload (an unfinished file of the pool or the backups)."""
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode):
        return False
    return any(file.key == (st.st_dev, st.st_ino) for file in _temporary_files(env))


def _connect(port: int, url: str, length: int) -> http.client.HTTPConnection:
    """A connection with the headers of a multipart upload of ``length`` bytes sent, but none of the body."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    conn.putrequest("POST", url, skip_accept_encoding=True)
    conn.putheader("Content-Type", CONTENT_TYPE.decode())
    conn.putheader("Content-Length", str(length))
    conn.endheaders()
    return conn


# --- no temporary file of the tempfile module ------------------------------------


def test_an_upload_needs_no_temporary_file(env, target):
    content = os.urandom(3 * MIB + 17)

    response = env.client.post(target.url, files={"file": (target.filename.decode(), content)})

    assert response.status_code == 200, response.text
    assert target.stored_content() == content


def test_a_plain_backup_upload_needs_no_temporary_file(env):
    backup = _tar_gz([_file("manifest.json", MANIFEST), _file("padding", os.urandom(2 * MIB))])

    response = env.client.post("/api/v1/backup/upload", files={"file": ("mine.tar.gz", backup)})

    assert response.status_code == 200, response.text
    assert response.json()["backup"]["version"] == "1.0"
    (name,) = os.listdir(env.backups)
    assert name.endswith(".tar.gz") and (env.backups / name).read_bytes() == backup


# --- the data goes to the destination as it arrives -------------------------------


@pytest.mark.parametrize("parser", ["h11", "httptools"])
def test_the_file_reaches_its_destination_while_the_body_still_arrives(env, target, parser):
    """Half of the file is sent; it must be in the destination file before the rest is sent."""
    data = os.urandom(6 * MIB)
    head = _part_head(b"file", target.filename, b"Content-Type: application/octet-stream")
    tail = b"\r\n" + END
    with _uvicorn(app, http=parser) as port:
        conn = _connect(port, target.url, len(head) + len(data) + len(tail))
        try:
            conn.send(head + data[: 3 * MIB])
            _wait_for(lambda: _temporary_bytes(env) >= 2 * MIB, "the first half of the file is in its destination file")
            (partial,) = _temporary_files(env)
            if target.kind == "files":  # unnamed where it can be
                assert (partial.name is None) == _can_be_unnamed(env.pool), partial
            # Nothing under a final name yet; an unnamed file is not in the folder at all.
            assert target.stored() == ([] if partial.name is None else [partial.name])
            conn.send(data[3 * MIB :] + tail)
            response = conn.getresponse()
            body = response.read()
        finally:
            conn.close()

    assert response.status == 200, body
    assert target.stored_content() == data


async def test_the_file_is_written_in_a_worker_thread_and_the_request_is_not_read_meanwhile(
    env, target, monkeypatch
):
    """At most about 1 MiB is written at a time, outside the event loop, and only then is more read."""
    real_write = os.write
    writes: list[tuple[int, int]] = []  # (thread, bytes)
    writing = threading.Event()

    def write(fd, data):
        if not _is_destination(env, fd):
            return real_write(fd, data)
        writing.set()
        try:
            time.sleep(0.01)  # a slow disk: a read of the request now would be seen
            written = real_write(fd, data)
            writes.append((threading.get_ident(), written))
            return written
        finally:
            writing.clear()

    monkeypatch.setattr(os, "write", write)
    data = os.urandom(8 * MIB)
    read_while_writing = []
    sender = _Sender(
        [_part_head(b"file", target.filename), *_pieces(data, 128 * 1024), b"\r\n" + END],
        on_read=lambda n: read_while_writing.append(n) if writing.is_set() else None,
    )

    await app(target.scope(), sender.receive, sender.send)

    assert sender.status == 200, sender.json
    assert target.stored_content() == data
    assert sum(size for _, size in writes) == len(data)
    assert len(writes) >= 8 and max(size for _, size in writes) <= MIB
    assert threading.get_ident() not in {thread for thread, _ in writes}
    assert read_while_writing == []


# The longest boundary python_multipart takes, and its delimiter in the body.
LONGEST_BOUNDARY = b"b" * 256
LONGEST_DELIMITER = b"\r\n--" + LONGEST_BOUNDARY


@pytest.mark.parametrize(
    "held",
    [LONGEST_DELIMITER[:-1], LONGEST_DELIMITER + b"\r", LONGEST_DELIMITER + b"-"],
    ids=["a-partial-delimiter", "a-delimiter-and-a-cr", "a-delimiter-and-a-hyphen"],
)
async def test_the_buffer_has_room_for_what_the_parser_holds_back(env, monkeypatch, held):
    """A piece of the body can end in what may be the delimiter of the next part.

    The parser holds those bytes back and gives them with the data of the
    next piece: up to the whole delimiter of the longest boundary and the
    byte after it, 261 bytes. The buffer is written before such a piece
    could take it over UPLOAD_BUFFER_BYTES.
    """
    real_write = os.write
    sizes: list[int] = []

    def write(fd, data):
        if _is_destination(env, fd):
            sizes.append(len(data))
        return real_write(fd, data)

    monkeypatch.setattr(os, "write", write)

    def filler(size: int) -> bytes:  # without CR, nothing in it can start a delimiter
        return os.urandom(size).replace(b"\r", b"x")

    piece = 256 * 1024
    # Before the next piece, the buffer holds one byte less than what makes
    # receive_upload write it, and the parser holds ``held`` back.
    before = request_limits._WRITE_AT - 1 - (piece - len(held))
    data = [*_pieces(filler(before)), filler(piece - len(held)) + held, b"X" + filler(piece - 1)]
    head = b"--" + LONGEST_BOUNDARY + b'\r\nContent-Disposition: form-data; name="file"; filename="big.bin"\r\n\r\n'
    sender = _Sender([head, *data, LONGEST_DELIMITER + b"--\r\n"])
    target = _Target("files", env)

    await app(target.scope(b"multipart/form-data; boundary=" + LONGEST_BOUNDARY), sender.receive, sender.send)

    assert sender.status == 200, sender.json
    assert target.stored_content() == b"".join(data)
    assert sum(sizes) == len(b"".join(data))
    assert max(sizes) <= request_limits.UPLOAD_BUFFER_BYTES


def test_the_memory_of_the_server_does_not_grow_with_the_upload(env):
    """256 MiB through uvicorn (httptools, as in the image): the Python heap grows by a few MiB at most."""
    size = 256 * MIB
    chunk = os.urandom(MIB)
    head = _part_head(b"file", b"big.bin")
    tail = b"\r\n" + END
    with _uvicorn(app, http="httptools") as port:
        conn = _connect(port, f"/api/v1/files/upload?{urlencode({'path': str(env.pool)})}", len(head) + size + len(tail))
        tracemalloc.start()
        try:
            before, _ = tracemalloc.get_traced_memory()
            conn.send(head)
            for _ in range(size // MIB):
                conn.send(chunk)
            conn.send(tail)
            response = conn.getresponse()
            body = response.read()
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
            conn.close()

    assert response.status == 200, body
    stored = env.pool / "big.bin"
    assert stored.stat().st_size == size
    with open(stored, "rb") as f:
        assert f.read(MIB) == chunk
        f.seek(size - MIB)
        assert f.read() == chunk
    assert peak - before < 16 * MIB, f"the upload made the server hold {(peak - before) / MIB:.1f} MiB"


# --- what is checked before the body is read, or before the file is written ------------


@pytest.mark.parametrize(
    "folder, status, detail",
    [
        ("{root}/outside", 403, "Access denied"),
        ("{base}", 400, "Choose a folder inside a pool"),
        ("{pool}/missing", 404, "Not found"),
        ("{pool}/a-file", 400, "Not a directory"),
        ("{pool}/link", 403, "Access denied: symbolic links are not followed"),
    ],
    ids=["outside-the-pools", "the-pools-directory", "missing", "a-file", "a-symlink"],
)
async def test_the_folder_is_checked_before_the_body_is_read(env, folder, status, detail):
    (env.root / "outside").mkdir()
    (env.pool / "a-file").write_bytes(b"x")
    os.symlink(env.root / "outside", env.pool / "link")
    target = _Target("files", env)
    sender = _Sender([_part(b"file", os.urandom(MIB), b"x.bin") + END])
    query = urlencode({"path": folder.format(root=env.root, base=env.base, pool=env.pool)})

    await app(target.scope(query=query), sender.receive, sender.send)

    assert (sender.status, sender.json) == (status, {"detail": detail})
    assert sender.reads == 0
    assert target.stored() == ["a-file", "link"] and os.listdir(env.root / "outside") == []


@pytest.mark.parametrize(
    "content_type, body, status, detail",
    [
        (b"multipart/form-data", _part(b"file", b"x", b"x.bin") + END, 400, "Missing boundary in multipart."),
        (CONTENT_TYPE, b"--" + BOUNDARY + b"\r\nnot a header\r\n\r\nx\r\n" + END, 400, "Invalid multipart data."),
        (CONTENT_TYPE, _part(b"", b"x").replace(b' name=""', b""), 400,
         'The Content-Disposition header field "name" must be provided.'),
        (b"application/json", b'{"file": "x.bin"}', 422, NO_FILE),
        (b"text/plain", b"file", 422, NO_FILE),
        (None, b"", 422, NO_FILE),
    ],
    ids=["no-boundary", "malformed", "no-name", "json", "text", "no-content-type"],
)
async def test_the_form_is_read_only_for_a_folder_that_exists(env, content_type, body, status, detail):
    """For a missing folder: 404 before anything is read. Then the answers of before for the form."""
    target = _Target("files", env)
    missing = _Sender([body])
    await app(target.scope(content_type, query=urlencode({"path": str(env.pool / "x")})), missing.receive, missing.send)
    assert (missing.status, missing.json, missing.reads) == (404, {"detail": "Not found"}, 0)

    sender = _Sender([body])
    await app(target.scope(content_type), sender.receive, sender.send)

    assert (sender.status, sender.json) == (status, {"detail": detail})
    assert target.stored() == []


async def test_a_body_that_is_not_a_multipart_form_is_not_read(env, target):
    """Starlette read an urlencoded form (and refused this one: a field over 64 KiB); it never holds a file."""
    sender = _Sender([b"file=" + b"x" * (100 * 1024)])

    await app(target.scope(b"application/x-www-form-urlencoded"), sender.receive, sender.send)

    assert (sender.status, sender.json) == (422, {"detail": NO_FILE})
    assert sender.reads == 0


@pytest.mark.parametrize(
    "filename, detail",
    [
        (b"", "Missing file name"),
        (b"..", "Invalid name"),
        (b"a/..", "Invalid name"),
        (b"folder/", "Invalid name"),
        (b"tab\there", "Invalid name: '/' and control characters are not allowed"),
        (b"x" * 256, "Name too long"),
        (b"folder", "A folder named folder already exists"),
    ],
)
async def test_the_file_name_is_checked_before_any_of_the_file_is_written(env, filename, detail):
    (env.pool / "folder").mkdir()
    target = _Target("files", env)
    sender = _Sender([_part_head(b"file", filename), os.urandom(MIB), os.urandom(MIB), b"\r\n" + END])

    await app(target.scope(), sender.receive, sender.send)

    assert (sender.status, sender.json) == (400, {"detail": detail})
    assert sender.reads == 1  # the headers of the part, none of the file
    assert target.stored() == ["folder"]


@pytest.mark.parametrize("filename", [b"backup.zip", b"backup.tar.gz.enc.bak", b"backup.tar", b""])
async def test_the_backup_extension_is_checked_before_any_of_the_file_is_written(env, filename):
    target = _Target("backup", env)
    sender = _Sender([_part_head(b"file", filename), os.urandom(MIB), os.urandom(MIB), b"\r\n" + END])

    await app(target.scope(), sender.receive, sender.send)

    assert (sender.status, sender.json) == (400, {"detail": "Invalid file format. Must be .tar.gz or .tar.gz.enc"})
    assert sender.reads == 1
    assert target.stored() == []


async def test_a_backup_is_refused_as_soon_as_it_grows_over_the_limit(env, monkeypatch):
    monkeypatch.setattr(backup_router, "MAX_ARCHIVE_BYTES", 2 * MIB)
    target = _Target("backup", env)
    pieces = _pieces(os.urandom(4 * MIB))  # 16 pieces of 256 KiB
    sender = _Sender([_part_head(b"file", target.filename), *pieces, b"\r\n" + END])

    await app(target.scope(), sender.receive, sender.send)

    assert (sender.status, sender.json) == (400, {"detail": "Backup file is too large"})
    assert sender.reads == 1 + 9  # the headers, and the pieces up to the one that passes 2 MiB
    assert target.stored() == []

    exactly = os.urandom(2 * MIB)
    response = env.client.post(target.url, files={"file": (target.filename.decode(), exactly)})
    assert response.status_code == 200, response.text
    assert target.stored_content() == exactly


def test_the_headers_of_a_part_are_limited(env, target):
    """8 KiB of header names and values per part; Starlette had no limit."""

    content = os.urandom(1000)

    def upload(padding: int):
        headers = [b"X-Pad-%d: " % i + b"p" * padding for i in range(2)]
        body = _part(b"file", content, target.filename, *headers) + END
        return env.client.post(target.url, content=body, headers={"Content-Type": CONTENT_TYPE.decode()})

    refused = upload(4100)
    assert (refused.status_code, refused.json()) == (400, {"detail": "Part headers exceeded maximum size of 8KB."})
    assert target.stored() == []

    accepted = upload(3900)
    assert accepted.status_code == 200, accepted.text
    assert target.stored_content() == content


# --- refusals after the file was written in part: nothing is left -----------------------


LATER = {
    "a-second-file": (_part(b"other", b"x", b"b.bin"), 400, "Too many files. Maximum number of files is 1."),
    "a-second-file-in-the-field": (_part(b"file", b"x", b"b.bin"), 400, "Too many files. Maximum number of files is 1."),
    "too-many-fields": (
        b"".join(_part(b"field%d" % i, b"v") for i in range(17)),
        400,
        "Too many fields. Maximum number of fields is 16.",
    ),
    "a-field-over-64-KiB": (_part(b"note", b"x" * (64 * 1024 + 1)), 400, "Part exceeded maximum size of 64KB."),
    "malformed-headers": (b"--" + BOUNDARY + b"\r\nnot a header\r\n\r\nx\r\n", 400, "Invalid multipart data."),
    "no-name": (
        b"--" + BOUNDARY + b'\r\nContent-Disposition: form-data; filename="x"\r\n\r\nx\r\n',
        400,
        'The Content-Disposition header field "name" must be provided.',
    ),
    "no-content-disposition": (
        b"--" + BOUNDARY + b"\r\nContent-Type: text/plain\r\n\r\nx\r\n",
        400,
        'The Content-Disposition header field "name" must be provided.',
    ),
    "headers-over-8-KiB": (
        _part(b"note", b"v", None, *[b"X-Pad-%d: " % i + b"p" * 3000 for i in range(3)]),
        400,
        "Part headers exceeded maximum size of 8KB.",
    ),
    # Starlette's form kept the last value of a name: the file was lost.
    "a-field-named-file-after-it": (_part(b"file", b"text"), 422, NO_FILE),
}


@pytest.mark.parametrize("later", LATER.values(), ids=LATER.keys())
async def test_a_refusal_after_the_file_leaves_nothing(env, target, later):
    """2 MiB of the file are in the destination file when the offending part arrives."""
    part, status, detail = later
    data = os.urandom(2 * MIB)
    chunks = [_part_head(b"file", target.filename), *_pieces(data), b"\r\n" + part + END]
    seen = []
    sender = _Sender(chunks, on_read=lambda n: seen.append(_temporary_bytes(env)) if n == len(chunks) - 1 else None)

    await app(target.scope(), sender.receive, sender.send)

    assert (sender.status, sender.json) == (status, {"detail": detail})
    assert seen and seen[0] >= MIB  # the destination file held data when the refusal came
    assert target.stored() == []


@pytest.mark.parametrize(
    "ending",
    [b"", b"\r\n", b"\r\n--" + BOUNDARY[:-3], b"\r\n--" + BOUNDARY],
    ids=["in-the-data", "after-the-data", "in-the-boundary", "before-the-end-of-the-boundary-line"],
)
async def test_a_body_that_ends_before_the_closing_boundary_of_the_file_stores_nothing(env, target, ending):
    data = os.urandom(2 * MIB)
    chunks = [_part_head(b"file", target.filename), *_pieces(data), ending]
    seen = []
    sender = _Sender(chunks, on_read=lambda n: seen.append(_temporary_bytes(env)) if n == len(chunks) - 1 else None)

    await app(target.scope(), sender.receive, sender.send)

    assert (sender.status, sender.json) == (422, {"detail": NO_FILE})
    assert seen and seen[0] >= MIB
    assert target.stored() == []


async def test_a_body_that_ends_after_the_closing_boundary_of_the_file_stores_it(env, target):
    """As with Starlette: the file part is complete, the rest of the form is not."""
    data = os.urandom(2 * MIB)
    chunks = [_part_head(b"file", target.filename), *_pieces(data), b"\r\n--" + BOUNDARY + b"\r\nCont"]
    seen = []
    sender = _Sender(chunks, on_read=lambda n: seen.append(_temporary_bytes(env)) if n == len(chunks) - 1 else None)

    await app(target.scope(), sender.receive, sender.send)

    assert sender.status == 200, sender.json
    assert seen and seen[0] >= MIB
    assert target.stored_content() == data


@pytest.mark.parametrize(
    "name, charset",
    [(b"other", b""), (b"file", b"; charset=utf-16-le")],
    ids=["another-name", "file-in-utf-16"],  # in UTF-16, the bytes "file" are another name
)
async def test_a_file_in_another_field_is_not_stored_anywhere(env, target, name, charset):
    """Starlette spooled it to a temporary file; it is dropped as it arrives."""
    sender = _Sender([_part(name, os.urandom(2 * MIB), target.filename) + END])

    await app(target.scope(CONTENT_TYPE + charset), sender.receive, sender.send)

    assert (sender.status, sender.json) == (422, {"detail": NO_FILE})
    assert target.stored() == []


async def test_the_body_limit_of_the_backup_upload_passes_through(env):
    """257 MiB without Content-Length: a backup of 1 MiB, then data after the end of the form (ignored)."""
    target = _Target("backup", env)
    filler = b"x" * MIB
    seen = []
    sender = _Sender(
        [_part(b"file", os.urandom(MIB), target.filename) + END, *[filler] * 258],
        on_read=lambda n: seen.append(_temporary_bytes(env)),
    )

    await app(target.scope(), sender.receive, sender.send)

    assert (sender.status, sender.json) == (413, {"detail": "Request body too large: the limit is 257 MiB"})
    assert sender.reads == 1 + 256  # the form (1 MiB and a little), and the fillers up to the limit
    assert max(seen) >= MIB // 2  # the backup was in the backup directory meanwhile
    assert target.stored() == []


# --- the client goes away, the disk is full, the request is cancelled, a check fails --------


async def test_a_client_that_goes_away_is_answered_as_before_and_leaves_nothing(env, target):
    data = os.urandom(2 * MIB)
    seen = []
    chunks = [_part_head(b"file", target.filename), *_pieces(data)]
    sender = _Sender(chunks, then="disconnect", on_read=lambda n: seen.append(_temporary_bytes(env)))

    await app(target.scope(), sender.receive, sender.send)  # raises nothing

    assert (sender.status, sender.json) == (400, {"detail": INTERRUPTED})
    assert seen[-1] >= MIB  # the destination file held data when the client went away
    assert target.stored() == []


def test_a_client_that_goes_away_from_a_real_server_leaves_nothing(env, target, caplog):
    caplog.set_level(logging.INFO)
    head = _part_head(b"file", target.filename)
    with _uvicorn(app, http="httptools") as port:
        conn = _connect(port, target.url, len(head) + 8 * MIB)
        conn.send(head + os.urandom(3 * MIB))
        _wait_for(lambda: _temporary_bytes(env) >= 2 * MIB, "the destination file holds data")
        conn.close()
        _wait_for(lambda: _temporary_files(env) == [], "the temporary file is removed (an unnamed one closed)")

    assert target.stored() == []
    assert [record.getMessage() for record in caplog.records if record.levelno >= logging.ERROR] == []


async def test_a_cancelled_request_leaves_nothing(env, target):
    """Cancelled while it waits for the client.

    The temporary file is removed (an unnamed one closed) before the task ends.
    """
    chunks = [_part_head(b"file", target.filename), *_pieces(os.urandom(2 * MIB))]
    sender = _Sender(chunks, then="block")
    task = asyncio.create_task(app(target.scope(), sender.receive, sender.send))
    deadline = time.monotonic() + 10
    while _temporary_bytes(env) < MIB:
        assert time.monotonic() < deadline and not task.done(), "the destination file got no data"
        await asyncio.sleep(0.01)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert target.stored() == []
    assert _temporary_files(env) == []


async def test_a_request_cancelled_during_a_write_leaves_nothing(env, target, monkeypatch):
    """The write in the worker thread ends first; then the temporary file is removed (an unnamed one closed)."""
    real_write = os.write
    started, release = threading.Event(), threading.Event()

    def write(fd, data):
        if _is_destination(env, fd):
            started.set()
            assert release.wait(10)
        return real_write(fd, data)

    monkeypatch.setattr(os, "write", write)
    sender = _Sender([_part_head(b"file", target.filename), *_pieces(os.urandom(2 * MIB))], then="block")
    task = asyncio.create_task(app(target.scope(), sender.receive, sender.send))
    assert await asyncio.to_thread(started.wait, 10)

    task.cancel()
    await asyncio.sleep(0.1)
    assert not task.done()  # the cleanup waits for the write
    assert len(_temporary_files(env)) == 1
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert target.stored() == []
    assert _temporary_files(env) == []


EXPECTED_FULL_DISK = {
    "files": (507, "No space left on device"),
    "backup": (500, "Failed to upload backup: [Errno 28] No space left on device"),
}


def test_a_full_disk_leaves_nothing(env, target, monkeypatch):
    real_write = os.write

    def write(fd, data):
        if _is_destination(env, fd) and os.fstat(fd).st_size + len(data) > MIB:
            raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
        return real_write(fd, data)

    monkeypatch.setattr(os, "write", write)

    response = env.client.post(target.url, files={"file": (target.filename.decode(), os.urandom(3 * MIB))})

    assert (response.status_code, response.json()["detail"]) == EXPECTED_FULL_DISK[target.kind]
    assert target.stored() == []


async def test_a_backup_that_cannot_get_its_name_leaves_nothing(env, monkeypatch):
    """An error of the last step, after the whole file arrived and was checked."""
    monkeypatch.setattr(backup_router, "NAME_ATTEMPTS", 0)
    target = _Target("backup", env)
    chunks = [_part_head(b"file", target.filename), *_pieces(os.urandom(2 * MIB)), b"\r\n" + END]
    seen = []
    sender = _Sender(chunks, on_read=lambda n: seen.append(_temporary_bytes(env)) if n == len(chunks) - 1 else None)

    await app(target.scope(), sender.receive, sender.send)

    assert (sender.status, sender.json) == (
        500,
        {"detail": "Failed to upload backup: Could not choose a file name for the backup"},
    )
    assert seen and seen[0] >= MIB
    assert target.stored() == []


async def test_a_failed_check_of_the_file_name_leaves_nothing(env, monkeypatch):
    def unreadable(dir_fd, name):
        raise PermissionError(errno.EACCES, os.strerror(errno.EACCES))

    monkeypatch.setattr(files_router, "_lstat_at", unreadable)
    target = _Target("files", env)
    sender = _Sender([_part_head(b"file", b"big.bin"), *_pieces(os.urandom(2 * MIB)), b"\r\n" + END])

    await app(target.scope(), sender.receive, sender.send)

    assert (sender.status, sender.json) == (403, {"detail": "Permission denied"})
    assert sender.reads == 1
    assert target.stored() == []


async def test_a_folder_that_appears_at_the_target_name_is_not_replaced(env):
    """Created after the name was checked, while the file arrives: the rename refuses it."""
    target = _Target("files", env)
    chunks = [_part_head(b"file", b"big.bin"), *_pieces(os.urandom(2 * MIB)), b"\r\n" + END]

    def make_folder(n):
        if n == len(chunks) - 1:
            (env.pool / "big.bin").mkdir()
            (env.pool / "big.bin" / "inside").write_bytes(b"kept")

    sender = _Sender(chunks, on_read=make_folder)

    await app(target.scope(), sender.receive, sender.send)

    assert sender.status == 400
    assert sender.json == {"detail": "Is a directory"}  # rename() onto a folder: EISDIR
    assert target.stored() == ["big.bin"]
    assert (env.pool / "big.bin" / "inside").read_bytes() == b"kept"


# --- the file of a file upload: unnamed, or a hidden temporary file ---------------------------


def _file_upload(data: bytes, filename: bytes = b"big.bin", tail: bytes = b"\r\n" + END) -> list[bytes]:
    """The chunks of a file upload: the headers of the part, the data in pieces of 256 KiB, and ``tail``."""
    return [_part_head(b"file", filename), *_pieces(data), tail]


def _watched(env, chunks: list[bytes], before_last=None) -> tuple[_Sender, list]:
    """A sender of ``chunks``. When the last one is asked for, it records the unfinished files.

    Then it runs ``before_last``, if given.
    """
    seen = []

    def on_read(n):
        if n == len(chunks) - 1:
            seen.append(_temporary_files(env))
            if before_last is not None:
                before_last()

    return _Sender(chunks, on_read=on_read), seen


def _check_unfinished(unfinished: list[_Unfinished], creation: str, at_least: int = MIB) -> None:
    """One unfinished file, holding at least ``at_least`` bytes: unnamed, or a hidden temporary file."""
    (file,) = unfinished
    assert file.size >= at_least, file
    if creation == "unnamed":
        assert file.name is None, file
    else:
        assert file.name is not None and file.name.startswith(".unos-upload-"), file


async def test_the_file_is_unnamed_or_hidden_until_it_is_complete(env, creation):
    """Unnamed: nothing of it is in the folder while it arrives. Named: one hidden temporary file."""
    target = _Target("files", env)
    data = os.urandom(3 * MIB)
    listed = []
    sender, seen = _watched(env, _file_upload(data), before_last=lambda: listed.append(target.stored()))

    await app(target.scope(), sender.receive, sender.send)

    assert sender.status == 200, sender.json
    _check_unfinished(seen[0], creation, at_least=2 * MIB)
    assert listed == [[] if creation == "unnamed" else [seen[0][0].name]]
    assert target.stored_content() == data
    assert _temporary_files(env) == []  # the descriptors of an unnamed file are closed


async def test_both_ways_give_the_same_file(env, monkeypatch):
    """The answer, the mode, the owner and the links of an uploaded file do not depend on how it was created."""
    if not _can_be_unnamed(env.pool):
        pytest.skip("no unnamed files here (O_TMPFILE): not Linux, or a filesystem without them")
    content = os.urandom(2 * MIB + 7)
    target = _Target("files", env)
    unnamed, seen_unnamed = _watched(env, _file_upload(content, b"unnamed.bin"))
    await app(target.scope(), unnamed.receive, unnamed.send)
    monkeypatch.setattr(files_router, "_UNNAMED_FLAGS", None)
    named, seen_named = _watched(env, _file_upload(content, b"named.bin"))
    await app(target.scope(), named.receive, named.send)

    assert (unnamed.status, named.status) == (200, 200)
    _check_unfinished(seen_unnamed[0], "unnamed")
    _check_unfinished(seen_named[0], "named")
    first, second = unnamed.json, named.json
    assert {key: first[key] for key in ("type", "size", "permissions", "owner", "group")} == {
        key: second[key] for key in ("type", "size", "permissions", "owner", "group")
    }
    one, two = os.stat(env.pool / "unnamed.bin"), os.stat(env.pool / "named.bin")
    assert (one.st_mode, one.st_uid, one.st_gid, one.st_nlink) == (two.st_mode, two.st_uid, two.st_gid, two.st_nlink)
    assert one.st_nlink == 1
    assert (env.pool / "unnamed.bin").read_bytes() == (env.pool / "named.bin").read_bytes() == content


def _mode_of(env, file: _Unfinished) -> int:
    """The mode bits of an unfinished upload's file: by its hidden name, or (unnamed) by a descriptor of this process."""
    if file.name is not None:
        return stat.S_IMODE(os.stat(env.pool / file.name).st_mode)
    for number in os.listdir("/proc/self/fd"):
        try:
            st = os.stat(f"/proc/self/fd/{number}")
        except OSError:  # closed meanwhile
            continue
        if (st.st_dev, st.st_ino) == file.key:
            return stat.S_IMODE(st.st_mode)
    raise AssertionError(f"no descriptor of {file}")


async def test_in_a_share_folder_the_file_is_writable_for_the_smb_group_before_any_of_it_is_written(
    env, creation, monkeypatch
):
    """In a folder set up as a share's (setgid, the SMB group, 2770), the file is 0660 from its creation (ADR-0273).

    Unnamed or hidden, it has that mode before any of its data is written and
    before it gets its name, and keeps it under its name, in the folder's
    group. The SMB group is the runner's own group here.
    """
    monkeypatch.setattr(constants, "SMB_GROUP_GID", os.getgid())
    os.chown(env.pool, -1, os.getgid())
    os.chmod(env.pool, 0o2770)
    target = _Target("files", env)
    content = os.urandom(2 * MIB)
    chunks = _file_upload(content)
    created = []

    def on_read(n):
        if n == 1:  # the headers of the part have arrived, none of its data
            (file,) = _temporary_files(env)
            created.append((file.size, _mode_of(env, file)))

    sender = _Sender(chunks, on_read=on_read)

    await app(target.scope(), sender.receive, sender.send)

    assert sender.status == 200, sender.json
    assert created == [(0, 0o660)]
    assert sender.json["permissions"] == "-rw-rw----"
    assert target.stored_content() == content
    st = os.stat(env.pool / "big.bin")
    assert (stat.S_IMODE(st.st_mode), st.st_gid) == (0o660, os.getgid())


async def test_a_file_that_cannot_get_the_mode_of_shares_leaves_nothing_either_way(env, creation, monkeypatch):
    """An upload whose file cannot be given 0660 in a share folder is answered with the error; nothing of it stays."""
    monkeypatch.setattr(constants, "SMB_GROUP_GID", os.getgid())
    os.chown(env.pool, -1, os.getgid())
    os.chmod(env.pool, 0o2770)
    real_fchmod = os.fchmod
    refused = []

    def fchmod(fd, mode):
        if _is_destination(env, fd):
            refused.append(mode)
            raise OSError(errno.EIO, os.strerror(errno.EIO))
        return real_fchmod(fd, mode)

    monkeypatch.setattr(os, "fchmod", fchmod)
    target = _Target("files", env)
    sender = _Sender(_file_upload(os.urandom(2 * MIB)))
    descriptors = len(os.listdir("/dev/fd"))

    await app(target.scope(), sender.receive, sender.send)

    assert (sender.status, sender.json) == (500, {"detail": "File operation failed: Input/output error"})
    assert refused == [0o660]
    assert target.stored() == []
    assert len(os.listdir("/dev/fd")) == descriptors  # the file's descriptors are closed too


@pytest.mark.parametrize("existing", ["a-file", "a-symlink", "a-dangling-symlink", "a-symlink-to-a-folder"])
async def test_an_upload_replaces_a_file_or_a_symlink_at_its_name(env, creation, existing, monkeypatch):
    """The rename replaces the entry; a symlink is not followed, what it points to is kept.

    An unnamed file gets a hidden name first: a link never replaces an entry,
    not even a symlink that points nowhere.
    """
    links, renames = _links_and_renames(monkeypatch)
    outside = env.root / "outside.txt"
    outside.write_bytes(b"kept")
    folder = env.root / "folder"
    if existing == "a-file":
        (env.pool / "big.bin").write_bytes(b"old")
    elif existing == "a-symlink":
        os.symlink(outside, env.pool / "big.bin")
    elif existing == "a-dangling-symlink":
        os.symlink(env.root / "nowhere", env.pool / "big.bin")
    else:
        folder.mkdir()
        os.symlink(folder, env.pool / "big.bin")
    target = _Target("files", env)
    content = os.urandom(2 * MIB + 3)
    sender, seen = _watched(env, _file_upload(content))

    await app(target.scope(), sender.receive, sender.send)

    assert sender.status == 200, sender.json
    _check_unfinished(seen[0], creation)
    ((hidden, renamed),) = renames
    assert re.fullmatch(r"\.unos-upload-[0-9a-f]{16}", hidden) and renamed == "big.bin"
    assert links == (["big.bin", hidden] if creation == "unnamed" else [])
    assert not (env.pool / "big.bin").is_symlink()
    assert target.stored_content() == content
    assert (env.pool / "big.bin").stat().st_nlink == 1
    assert outside.read_bytes() == b"kept"
    assert not (env.root / "nowhere").exists()
    if folder.exists():
        assert os.listdir(folder) == []


UNFINISHED = {
    "a-refusal-after-the-data": (b"\r\n" + LATER["a-second-file"][0] + END, 400, LATER["a-second-file"][2]),
    "a-body-that-ends-early": (b"\r\n--" + BOUNDARY[:-3], 422, NO_FILE),
    "a-client-that-goes-away": (None, 400, INTERRUPTED),
    "a-full-disk": (b"\r\n" + END, 507, "No space left on device"),
}


@pytest.mark.parametrize("ending", UNFINISHED)
async def test_an_upload_that_fails_after_its_data_leaves_nothing_either_way(env, creation, monkeypatch, ending):
    """It fails after the data; afterwards nothing is in the folder, and nothing is open.

    When 2 MiB of the file have arrived, about 1.5 MiB are in its file
    (unnamed, or hidden); it fails later, with about 2.25 MiB (a full disk)
    or 3 MiB written.
    """
    tail, status, detail = UNFINISHED[ending]
    if ending == "a-full-disk":
        real_write = os.write

        def write(fd, data):
            if _is_destination(env, fd) and os.fstat(fd).st_size >= 2 * MIB:
                raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
            return real_write(fd, data)

        monkeypatch.setattr(os, "write", write)
    target = _Target("files", env)
    chunks = _file_upload(os.urandom(3 * MIB), tail=tail or b"")
    if tail is None:
        chunks.pop()
    seen = []
    sender = _Sender(
        chunks,
        then="end" if tail is not None else "disconnect",
        on_read=lambda n: seen.append(_temporary_files(env)) if n == 9 else None,  # 2 MiB sent, 1.5 written
    )

    await app(target.scope(), sender.receive, sender.send)

    assert (sender.status, sender.json) == (status, {"detail": detail})
    _check_unfinished(seen[0], creation)
    assert target.stored() == []
    assert _temporary_files(env) == []


@pytest.mark.parametrize("when", ["while-it-waits", "during-a-write"])
async def test_a_cancelled_upload_leaves_nothing_either_way(env, creation, monkeypatch, when):
    """Cancelled while it waits for the client, or while a write runs in its thread (the cleanup waits for it)."""
    real_write = os.write
    started, release = threading.Event(), threading.Event()

    def write(fd, data):
        if when == "during-a-write" and _is_destination(env, fd) and os.fstat(fd).st_size >= MIB:
            started.set()
            assert release.wait(10)
        return real_write(fd, data)

    monkeypatch.setattr(os, "write", write)
    target = _Target("files", env)
    sender = _Sender(_file_upload(os.urandom(3 * MIB))[:-1], then="block")
    task = asyncio.create_task(app(target.scope(), sender.receive, sender.send))
    if when == "during-a-write":
        assert await asyncio.to_thread(started.wait, 10)
    else:
        deadline = time.monotonic() + 10
        while _temporary_bytes(env) < 2 * MIB:
            assert time.monotonic() < deadline and not task.done(), "the destination file got no data"
            await asyncio.sleep(0.01)
    _check_unfinished(_temporary_files(env), creation)

    task.cancel()
    await asyncio.sleep(0.1)
    if when == "during-a-write":
        assert not task.done()  # the cleanup waits for the write
        _check_unfinished(_temporary_files(env), creation)
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert target.stored() == []
    assert _temporary_files(env) == []


async def test_a_folder_that_appears_at_the_target_name_is_kept_either_way(env, creation):
    """The rename refuses it; the complete file is not left under its hidden name."""
    target = _Target("files", env)

    def make_folder():
        (env.pool / "big.bin").mkdir()
        (env.pool / "big.bin" / "inside").write_bytes(b"kept")

    sender, seen = _watched(env, _file_upload(os.urandom(2 * MIB)), before_last=make_folder)

    await app(target.scope(), sender.receive, sender.send)

    _check_unfinished(seen[0], creation)
    assert sender.status == 400
    assert sender.json == {"detail": "Is a directory"}  # rename() onto a folder: EISDIR
    assert target.stored() == ["big.bin"]
    assert os.listdir(env.pool / "big.bin") == ["inside"]
    assert (env.pool / "big.bin" / "inside").read_bytes() == b"kept"


async def test_a_folder_deleted_during_the_upload_leaves_nothing_either_way(env, creation):
    """Deleted with what is in it (over SMB, say) while the file arrives: 404, and nothing stays open.

    An unnamed file is not in the folder, so the folder looks empty and can
    even be removed with rmdir; then the link fails (ENOENT). A hidden
    temporary file is deleted with the folder; then the rename fails.
    """
    target = _Target("files", env)
    sender, seen = _watched(env, _file_upload(os.urandom(2 * MIB)), before_last=lambda: shutil.rmtree(env.pool))

    await app(target.scope(), sender.receive, sender.send)

    _check_unfinished(seen[0], creation)
    assert (sender.status, sender.json) == (404, {"detail": "Not found"})
    assert not env.pool.exists()
    assert _unnamed_files(env.pool) == {}


async def test_a_folder_removed_before_the_file_arrives_is_refused_before_any_of_it_either_way(env, creation):
    """Removed after the folder was checked, before the headers of the file part: 404 at once, as before.

    The kernel refuses a new name in a removed folder (ENOENT), but still
    creates an unnamed file there. Without a check, the upload would take the
    whole file and answer 404 only when it is linked.
    """
    folder = env.pool / "folder"
    folder.mkdir()
    target = _Target("files", env)
    sender = _Sender(_file_upload(os.urandom(2 * MIB)), on_read=lambda n: folder.rmdir() if n == 0 else None)

    await app(target.scope(query=urlencode({"path": str(folder)})), sender.receive, sender.send)

    assert (sender.status, sender.json) == (404, {"detail": "Not found"})
    assert sender.reads == 1  # the headers of the part, none of the file
    assert not folder.exists() and target.stored() == []
    assert _unnamed_files(folder) == {}


@pytest.mark.skipif(sys.platform != "linux", reason="a removed folder keeps its link count here (not Linux)")
async def test_a_removed_folder_is_not_found_whatever_its_filesystem_answers_for_the_unnamed_file(env, monkeypatch):
    """ext4 refuses an unnamed file in a removed folder with EPERM, where tmpfs creates one: 404 either way.

    The folder is removed after it was checked, before the headers of the
    file part, as above; the filesystem's refusal must not read as a
    permission problem (403).
    """
    real_open = os.open

    def ext4_open(path, flags, *args, dir_fd=None, **kwargs):
        # ext4 (__ext4_new_inode): "Cannot create files in a deleted directory".
        if flags == files_router._UNNAMED_FLAGS and os.fstat(dir_fd).st_nlink == 0:
            raise PermissionError(errno.EPERM, os.strerror(errno.EPERM))
        return real_open(path, flags, *args, dir_fd=dir_fd, **kwargs)

    monkeypatch.setattr(os, "open", ext4_open)
    folder = env.pool / "folder"
    folder.mkdir()
    target = _Target("files", env)
    sender = _Sender(_file_upload(os.urandom(2 * MIB)), on_read=lambda n: folder.rmdir() if n == 0 else None)

    await app(target.scope(query=urlencode({"path": str(folder)})), sender.receive, sender.send)

    assert (sender.status, sender.json) == (404, {"detail": "Not found"})
    assert sender.reads == 1  # the headers of the part, none of the file
    assert not folder.exists() and target.stored() == []


def test_two_uploads_of_one_name_at_the_same_time(env, creation):
    """Each has a file of its own; the one that ends last gives the file its content, and nothing is left."""
    first, second = os.urandom(3 * MIB), os.urandom(3 * MIB)
    head, tail = _part_head(b"file", b"same.bin"), b"\r\n" + END
    target = _Target("files", env)
    with _uvicorn(app, http="httptools") as port:
        conns = [_connect(port, target.url, len(head) + len(data) + len(tail)) for data in (first, second)]
        try:
            for conn, data in zip(conns, (first, second)):
                conn.send(head + data[: 2 * MIB])
            _wait_for(
                lambda: [file.size >= MIB for file in _temporary_files(env)] == [True, True],
                "both uploads have their own file, with data",
            )
            for file in _temporary_files(env):
                assert (file.name is None) == (creation == "unnamed"), file
            for conn, data in zip(conns, (first, second)):
                conn.send(data[2 * MIB :] + tail)
            answers = []
            for conn in conns:
                response = conn.getresponse()
                answers.append((response.status, json.loads(response.read())["name"]))
        finally:
            for conn in conns:
                conn.close()

    assert answers == [(200, "same.bin"), (200, "same.bin")]
    assert target.stored() == ["same.bin"]
    assert (env.pool / "same.bin").read_bytes() in (first, second)


# --- where there cannot be an unnamed file ---------------------------------------------


@pytest.mark.parametrize("unusable", ["no-O_TMPFILE", "EOPNOTSUPP", "EISDIR", "EINVAL", "no-proc-self-fd"])
async def test_where_there_cannot_be_an_unnamed_file_a_hidden_one_is_used(env, monkeypatch, unusable):
    """The same upload, with a hidden temporary file in the folder while the file arrives.

    The platform has no O_TMPFILE (macOS); the filesystem refuses it
    (EOPNOTSUPP: vfat, NFS...; EISDIR: a kernel before 3.11; EINVAL); or
    /proc/self/fd, through which the file would be linked, cannot be used.
    """
    attempts = []
    if unusable == "no-O_TMPFILE":
        monkeypatch.setattr(files_router, "_UNNAMED_FLAGS", None)
    elif unusable == "no-proc-self-fd":
        if not _can_be_unnamed(env.pool):
            pytest.skip("no unnamed files here (O_TMPFILE): not Linux, or a filesystem without them")
        monkeypatch.setattr(files_router, "_PROC_FD", str(env.root / "no-proc"))
    else:
        # Without O_TMPFILE (macOS), -1 stands for its flags: the open is refused before it reaches the system.
        flags = files_router._UNNAMED_FLAGS or -1
        monkeypatch.setattr(files_router, "_UNNAMED_FLAGS", flags)
        real_open = os.open

        def refusing_open(path, open_flags, *args, **kwargs):
            if open_flags == flags:
                attempts.append(path)
                code = getattr(errno, unusable)
                raise OSError(code, os.strerror(code))
            return real_open(path, open_flags, *args, **kwargs)

        monkeypatch.setattr(os, "open", refusing_open)
    target = _Target("files", env)
    data = os.urandom(3 * MIB)
    listed = []
    sender, seen = _watched(env, _file_upload(data), before_last=lambda: listed.append(target.stored()))

    await app(target.scope(), sender.receive, sender.send)

    assert sender.status == 200, sender.json
    _check_unfinished(seen[0], "named", at_least=2 * MIB)
    assert listed == [[seen[0][0].name]]
    assert attempts == ([] if unusable.startswith("no-") else ["."])
    assert target.stored_content() == data


async def test_a_filesystem_without_unnamed_files_gets_the_hidden_one_whatever_its_folders_report(env, monkeypatch):
    """A FUSE filesystem may report no links for every folder: without O_TMPFILE that is no removed folder.

    Where the filesystem cannot create unnamed files, the hidden file is used
    without asking whether the folder was removed: creating it in a removed
    folder fails on its own (ENOENT, 404).
    """
    flags = files_router._UNNAMED_FLAGS or -1  # -1 stands for the flags where there are none (macOS)
    monkeypatch.setattr(files_router, "_UNNAMED_FLAGS", flags)
    monkeypatch.setattr(files_router, "_removed", lambda dir_fd: True)
    real_open = os.open

    def without_unnamed_files(path, open_flags, *args, **kwargs):
        if open_flags == flags:
            raise OSError(errno.EOPNOTSUPP, os.strerror(errno.EOPNOTSUPP))
        return real_open(path, open_flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", without_unnamed_files)
    target = _Target("files", env)
    data = os.urandom(3 * MIB)
    sender, seen = _watched(env, _file_upload(data))

    await app(target.scope(), sender.receive, sender.send)

    assert sender.status == 200, sender.json
    _check_unfinished(seen[0], "named", at_least=2 * MIB)
    assert target.stored_content() == data


@pytest.mark.parametrize(
    "error, status, detail",
    [(errno.EACCES, 403, "Permission denied"), (errno.ENOSPC, 507, "No space left on device")],
    ids=["EACCES", "ENOSPC"],
)
async def test_other_errors_of_the_unnamed_file_are_answered(env, monkeypatch, error, status, detail):
    """A hidden file would fail alike: the upload is refused before any of the file is written."""
    flags = files_router._UNNAMED_FLAGS or -1
    monkeypatch.setattr(files_router, "_UNNAMED_FLAGS", flags)
    real_open = os.open
    attempts = []

    def failing_open(path, open_flags, *args, **kwargs):
        if open_flags == flags:
            attempts.append(path)
            raise OSError(error, os.strerror(error))
        if isinstance(path, str) and path.startswith(".unos-upload-"):
            attempts.append(path)
        return real_open(path, open_flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", failing_open)
    target = _Target("files", env)
    sender = _Sender(_file_upload(os.urandom(2 * MIB)))

    await app(target.scope(), sender.receive, sender.send)

    assert (sender.status, sender.json) == (status, {"detail": detail})
    assert attempts == ["."]  # no hidden file was tried
    assert sender.reads == 1
    assert target.stored() == []


# --- the last step of an unnamed file: its link into the folder ------------------------


@pytest.mark.parametrize("creation", ["unnamed"], indirect=True)
@pytest.mark.parametrize(
    "error, status, detail",
    [
        (errno.EEXIST, 400, "Already exists"),
        (errno.ENOSPC, 507, "No space left on device"),
        (errno.ENOENT, 404, "Not found"),
    ],
    ids=["EEXIST", "ENOSPC", "ENOENT"],
)
async def test_a_failed_link_of_the_complete_file_leaves_nothing(env, creation, monkeypatch, error, status, detail):
    """The whole file has arrived; nothing is named in the folder, and the unnamed file is closed."""
    links = []

    def failing_link(src, dst, **kwargs):
        links.append((src, dst, kwargs))
        raise OSError(error, os.strerror(error))

    monkeypatch.setattr(os, "link", failing_link)
    target = _Target("files", env)
    chunks = _file_upload(os.urandom(2 * MIB))
    seen = []
    sender = _Sender(chunks, on_read=lambda n: seen.append(_temporary_files(env)) if n == len(chunks) - 1 else None)

    await app(target.scope(), sender.receive, sender.send)

    assert (sender.status, sender.json) == (status, {"detail": detail})
    _check_unfinished(seen[0], creation)
    # Its own name first; a hidden one only where that exists (EEXIST).
    assert [dst for _, dst, _ in links][:1] == ["big.bin"]
    assert len(links) == (2 if error == errno.EEXIST else 1)
    if error == errno.EEXIST:
        assert re.fullmatch(r"\.unos-upload-[0-9a-f]{16}", links[1][1])
    for src, _, kwargs in links:
        assert re.fullmatch(r"/proc/self/fd/\d+", src)
        assert kwargs.get("follow_symlinks", True) is True and isinstance(kwargs.get("dst_dir_fd"), int)
    assert target.stored() == []
    assert _temporary_files(env) == []


def _links_and_renames(monkeypatch) -> tuple[list[str], list[tuple[str, str]]]:
    """Records the names that os.link and os.rename give in a folder (they still do it)."""
    links: list[str] = []
    renames: list[tuple[str, str]] = []
    real_link, real_rename = os.link, os.rename

    def link(src, dst, **kwargs):
        links.append(dst)
        return real_link(src, dst, **kwargs)

    def rename(src, dst, **kwargs):
        renames.append((src, dst))
        return real_rename(src, dst, **kwargs)

    monkeypatch.setattr(os, "link", link)
    monkeypatch.setattr(os, "rename", rename)
    return links, renames


@pytest.mark.parametrize("creation", ["unnamed"], indirect=True)
async def test_a_new_name_is_given_to_the_complete_file_at_once(env, creation, monkeypatch):
    """A link never replaces an entry: a new name is linked at once, never under a hidden name first.

    Linked under a hidden name and then renamed, a supervisor that stopped
    between the two left the complete file under the hidden name.
    """
    links, renames = _links_and_renames(monkeypatch)
    target = _Target("files", env)
    content = os.urandom(2 * MIB)
    sender, seen = _watched(env, _file_upload(content))

    await app(target.scope(), sender.receive, sender.send)

    assert sender.status == 200, sender.json
    _check_unfinished(seen[0], creation)
    assert (links, renames) == (["big.bin"], [])
    assert target.stored_content() == content


@pytest.mark.parametrize("creation", ["unnamed"], indirect=True)
@pytest.mark.parametrize("then", ["removed", "a-folder", "the-folder-removed"])
async def test_the_entry_at_the_name_changes_after_the_link_found_it(env, creation, monkeypatch, then):
    """The link to the name fails (EEXIST), then the entry there changes before the hidden link and the rename.

    Removed: the rename creates the name (200). A folder in its place: the
    rename refuses it (400) and it is kept. The whole folder removed: 404.
    Nothing is left behind, and nothing stays open.
    """
    (env.pool / "big.bin").write_bytes(b"old")
    real_link = os.link

    def link(src, dst, **kwargs):
        try:
            return real_link(src, dst, **kwargs)
        except FileExistsError:
            if dst == "big.bin":
                if then == "the-folder-removed":
                    shutil.rmtree(env.pool)
                else:
                    (env.pool / "big.bin").unlink()
                    if then == "a-folder":
                        (env.pool / "big.bin").mkdir()
            raise

    monkeypatch.setattr(os, "link", link)
    target = _Target("files", env)
    content = os.urandom(2 * MIB)
    sender, seen = _watched(env, _file_upload(content))

    await app(target.scope(), sender.receive, sender.send)

    _check_unfinished(seen[0], creation)
    if then == "removed":
        assert sender.status == 200, sender.json
        assert target.stored_content() == content
    elif then == "a-folder":
        assert (sender.status, sender.json) == (400, {"detail": "Is a directory"})
        assert target.stored() == ["big.bin"] and (env.pool / "big.bin").is_dir()
        assert os.listdir(env.pool / "big.bin") == []
    else:
        assert (sender.status, sender.json) == (404, {"detail": "Not found"})
        assert not env.pool.exists()
    assert _unnamed_files(env.pool) == {}


async def test_an_entry_at_the_hidden_name_of_an_upload_is_kept(env, creation, monkeypatch):
    """Not ours: the upload is refused (400), and the entry is not removed.

    With 64 random bits in the name, only a test can choose it. A hidden file
    is refused when it is created, before any of the data. An unnamed one
    takes a hidden name only to replace a file (a new name is linked at
    once), and is refused when it is linked, after all of it.
    """
    name = ".unos-upload-0123456789abcdef"
    monkeypatch.setattr(files_router, "_upload_temp_name", lambda: name)
    (env.pool / name).write_bytes(b"not ours")
    (env.pool / "big.bin").write_bytes(b"old")
    target = _Target("files", env)
    sender, seen = _watched(env, _file_upload(os.urandom(2 * MIB)))

    await app(target.scope(), sender.receive, sender.send)

    assert (sender.status, sender.json) == (400, {"detail": "Already exists"})
    if creation == "unnamed":
        unnamed = [file for file in seen[0] if file.name is None]
        _check_unfinished(unnamed, creation)
    else:
        assert sender.reads == 1
    assert target.stored() == [name, "big.bin"]
    assert (env.pool / name).read_bytes() == b"not ours"
    assert (env.pool / "big.bin").read_bytes() == b"old"
    (env.pool / name).unlink()


# --- a supervisor that stops in the middle of an upload ---------------------------------

# The application in uvicorn as in the image (httptools), in a process of its own: the pools base
# is argv[1], the listening socket the descriptor argv[2]; the credentials are an administrator's.
_SERVER = """
import importlib, socket, sys
from types import SimpleNamespace
import uvicorn
from src.auth.utils import verify_token
from src.main import app
importlib.import_module("src.files.router").BASE_PATH = sys.argv[1]
app.dependency_overrides[verify_token] = lambda: SimpleNamespace(id=1, username="admin", is_admin=True)
config = uvicorn.Config(app, http="httptools", lifespan="off", log_level="warning")
uvicorn.Server(config).run(sockets=[socket.socket(fileno=int(sys.argv[2]))])
"""


def _arrived(folder: Path, pid: int) -> int:
    """The bytes of the uploads the process ``pid`` is writing in ``folder``: hidden files, or unnamed ones."""
    named = 0
    for path in folder.glob(".unos-upload-*"):
        try:
            named += path.stat().st_size
        except FileNotFoundError:
            pass
    return named + sum(_unnamed_files(folder, pid).values())


def test_a_supervisor_killed_in_the_middle_of_an_upload_leaves_nothing_in_the_folder(tmp_path):
    """SIGKILL, as Docker sends 10 s after docker stop, with 16 MiB of 64 arrived.

    The unnamed file goes with the process. Where there cannot be one (macOS,
    filesystems without O_TMPFILE), the hidden temporary file stays with what
    had arrived, and nothing removes it: the test checks that, and is marked
    xfail there.
    """
    base = tmp_path.resolve() / "pools"
    pool = base / "data"
    pool.mkdir(parents=True)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    with (tmp_path / "server.log").open("wb") as log:
        server = subprocess.Popen(
            [sys.executable, "-B", "-c", _SERVER, str(base), str(listener.fileno())],
            cwd=SUPERVISOR,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            pass_fds=[listener.fileno()],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
        )
    listener.close()
    unnamed = _can_be_unnamed(pool)
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
        url = f"/api/v1/files/upload?{urlencode({'path': str(pool)})}"
        conn = _connect(port, url, len(head) + 64 * MIB + len(tail))
        conn.send(head)
        chunk = os.urandom(MIB)
        for _ in range(16):
            conn.send(chunk)
        _wait_for(lambda: _arrived(pool, server.pid) >= 8 * MIB, "the file of the upload holds data", timeout=30)
        if unnamed:
            assert os.listdir(pool) == []  # nothing of it in the folder while it arrives
        server.kill()
        server.wait(10)
    finally:
        if server.poll() is None:
            server.kill()
            server.wait(10)
        if conn is not None:
            conn.close()

    left = sorted(os.listdir(pool))
    if not unnamed:
        (name,) = left
        assert name.startswith(".unos-upload-") and (pool / name).stat().st_size >= 8 * MIB
        pytest.xfail("no unnamed files here (macOS, or no O_TMPFILE): the hidden file of the upload stays")
    assert left == []


# --- names ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "charset, name, filename, stored",
    [
        (None, b"file", "Отчёт ✓.txt".encode(), "Отчёт ✓.txt"),
        (None, b"file", b"caf\xe9.txt", "café.txt"),
        (b"windows-1251", b"file", "Отчёт.txt".encode("cp1251"), "Отчёт.txt"),
        (b"no-such-charset", b"file", b"caf\xe9.txt", "café.txt"),
        (b"utf-16-le", "file".encode("utf-16-le"), "a.txt".encode("utf-16-le"), "a.txt"),
    ],
    ids=["utf-8-by-default", "latin-1-when-not-utf-8", "the-charset-parameter", "unknown-charset", "utf-16"],
)
async def test_names_are_decoded_like_starlette(env, charset, name, filename, stored):
    """The decoded name is checked, and the file written under it, as the file arrives."""
    target = _Target("files", env)
    content_type = CONTENT_TYPE if charset is None else CONTENT_TYPE + b"; charset=" + charset
    data = os.urandom(2 * MIB)
    chunks = [_part_head(name, filename), *_pieces(data), b"\r\n" + END]
    seen = []
    sender = _Sender(chunks, on_read=lambda n: seen.append(_temporary_bytes(env)) if n == len(chunks) - 1 else None)

    await app(target.scope(content_type), sender.receive, sender.send)

    assert sender.status == 200, sender.json
    assert sender.json["name"] == stored
    assert seen and seen[0] >= MIB
    assert target.stored() == [stored]
    assert (env.pool / stored).read_bytes() == data


# Odd charset parameters of the form (request_limits._decode). A name the charset does not decode is
# read as latin-1, as Starlette does; it is never an error of the server. Each case: the charset, how
# the client encodes the names, the field name, the file name without its extension, and the name the
# file gets without it (None: the field name decodes to another name there, so no file arrives: 422).
ODD_CHARSETS = {
    # These codecs raise UnicodeError, not UnicodeDecodeError: it was 400 "Invalid path" for a file
    # and 500 for a backup. "undefined" raises it for every name.
    "undefined": (b"undefined", "latin-1", "file", "café", "café"),
    "punycode": (b"punycode", "ascii", "file-", "a", "a"),  # "file-" is "file"; "a.bin" raises
    "idna": (b"idna", "ascii", "file", "xn--abc", "xn--abc"),  # "xn--abc" raises
    # Not a text encoding (LookupError), a name it cannot decode (UnicodeDecodeError), or no codec.
    "rot13": (b"rot13", "ascii", "file", "a", "a"),
    "utf-7": (b"utf-7", "ascii", "file", "a+", "a+"),
    "unicode-escape": (b"unicode_escape", "ascii", "file", "a\\x", "x"),  # the base name of "a\x"
    "utf-16": (b"utf-16", "utf-16", "file", "a", "a"),
    "a-NUL-in-the-charset": (b"utf-8\x00", "latin-1", "file", "café", "café"),
    "punycode-another-field": (b"punycode", "ascii", "file", "a", None),
    "utf-16-another-field": (b"utf-16", "ascii", "file", "a", None),
}


@pytest.mark.parametrize("case", ODD_CHARSETS.values(), ids=ODD_CHARSETS.keys())
async def test_odd_charsets_get_the_normal_answers(env, target, case):
    charset, encoding, field, stem, stored_stem = case
    extension = ".bin" if target.kind == "files" else ".tar.gz.enc"
    data = os.urandom(64 * 1024)
    part = _part(field.encode(encoding), data, (stem + extension).encode(encoding))
    sender = _Sender([part + END])

    await app(target.scope(CONTENT_TYPE + b"; charset=" + charset), sender.receive, sender.send)

    if stored_stem is None:
        assert (sender.status, sender.json) == (422, {"detail": NO_FILE})
        assert target.stored() == []
    elif target.kind == "files":
        assert sender.status == 200, sender.json
        assert sender.json["name"] == stored_stem + extension
        assert target.stored() == [stored_stem + extension]
        assert (env.pool / (stored_stem + extension)).read_bytes() == data
    else:
        assert sender.status == 200, sender.json
        assert target.stored_content() == data
