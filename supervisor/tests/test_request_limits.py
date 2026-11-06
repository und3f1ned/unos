"""Request bodies are limited before anything checks the credentials (src/request_limits.py).

FastAPI reads and parses a body before it runs the dependencies that check
the credentials. Uploads read their form only after that check; see also
test_authz.py (TestUploadsReadTheirBodyAfterTheCredentialsAreChecked).
WebSocket messages are limited by uvicorn itself (--ws-max-size in the
supervisor images), before the application reads them.
"""

import http.client
import importlib
import io
import json
import os
import re
import shlex
import socket
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlencode

import pytest
import uvicorn
import websockets
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool
from starlette.requests import Request as StarletteRequest
from uvicorn.main import main as uvicorn_command
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect as websocket_connect

from src.auth.models import User
from src.auth.utils import create_access_token, verify_token
from src.backup.router import MAX_ARCHIVE_BYTES
from src.database import Base
from src.main import app
from src.request_limits import (
    MAX_REQUEST_BODY,
    RequestBodyLimitMiddleware,
    UploadSink,
    receive_upload,
    too_large_detail,
)
from src.websocket import events

backup_router = importlib.import_module("src.backup.router")
files_router = importlib.import_module("src.files.router")
SUPERVISOR = Path(__file__).resolve().parents[1]

ADMIN = SimpleNamespace(id=1, username="admin", is_admin=True)
# An administrator endpoint that takes a JSON body.
VOLUMES = "/api/v1/storage/pools/data/volumes"
TOO_LARGE = {"detail": "Request body too large: the limit is 1 MiB"}


def _json_body(size: int) -> bytes:
    """A JSON object of exactly ``size`` bytes."""
    return b'{"name": "' + b"x" * (size - 12) + b'"}'


@pytest.fixture
def body_reads(monkeypatch):
    """Record (and refuse) every attempt of the application to read a request body."""
    reads: list[str] = []

    def refuse(name):
        def read(self, *args, **kwargs):
            reads.append(name)
            raise AssertionError(f"request.{name}() was called")

        return read

    for name in ("body", "form", "stream"):
        monkeypatch.setattr(StarletteRequest, name, refuse(name))
    return reads


# --- ASGI helpers --------------------------------------------------------------


def _scope(path: str, headers: list[tuple[bytes, bytes]], method: str = "POST", query: bytes = b"") -> dict:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query,
        "root_path": "",
        "headers": [(b"host", b"testserver"), *headers],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
    }


class _Client:
    """Sends the body in chunks and records what the application read and answered.

    With ``disconnect``, the client goes away after the chunks, before the end of the body.
    """

    def __init__(self, chunks: list[bytes], *, disconnect: bool = False):
        self.chunks = list(chunks)
        self.disconnect = disconnect
        self.reads = 0
        self.messages: list[dict] = []

    async def receive(self) -> dict:
        self.reads += 1
        if not self.chunks:
            return {"type": "http.disconnect"}
        chunk = self.chunks.pop(0)
        return {"type": "http.request", "body": chunk, "more_body": self.disconnect or bool(self.chunks)}

    async def send(self, message: dict) -> None:
        self.messages.append(message)

    @property
    def status(self) -> int:
        return next(m["status"] for m in self.messages if m["type"] == "http.response.start")

    @property
    def json(self):
        return json.loads(b"".join(m.get("body", b"") for m in self.messages if m["type"] == "http.response.body"))


# --- the limit of the application -------------------------------------------------


def test_the_application_limits_every_body_but_the_uploads():
    (middleware,) = [m for m in app.user_middleware if m.cls is RequestBodyLimitMiddleware]
    assert middleware.kwargs == {
        "limit": MAX_REQUEST_BODY,
        "path_limits": {
            "/api/v1/files/upload": None,
            "/api/v1/backup/upload": MAX_ARCHIVE_BYTES + MAX_REQUEST_BODY,
        },
    }
    assert MAX_REQUEST_BODY == 1024 * 1024
    assert too_large_detail(MAX_REQUEST_BODY) == TOO_LARGE["detail"]


@pytest.mark.parametrize(
    "path", [VOLUMES, "/api/v1/auth/login", "/api/v1/files/copy?destination=/x", "/api/v1/backup/restore/a.tar.gz"]
)
def test_content_length_over_the_limit_is_answered_413_without_reading(body_reads, path):
    """Also before the credentials are checked: the client sent none."""
    client = TestClient(app)

    response = client.post(path, content=_json_body(MAX_REQUEST_BODY + 1), headers={"Content-Type": "application/json"})

    assert response.status_code == 413
    assert response.json() == TOO_LARGE
    assert body_reads == []


async def test_content_length_over_the_limit_is_answered_before_anything_is_received():
    client = _Client([b"{}"])
    await app(_scope(VOLUMES, [(b"content-length", str(8 << 30).encode())]), client.receive, client.send)

    assert (client.status, client.json) == (413, TOO_LARGE)
    assert client.reads == 0


@pytest.mark.parametrize(
    "value", [b"", b"abc", b"-1", b"+5", b" 5", b"5 ", b"1e3", b"0x10", b"5.0", "٥".encode(), b"9" * 40]
)
async def test_content_length_that_is_not_plain_digits_is_answered_413(value):
    client = _Client([b"{}"])
    await app(_scope(VOLUMES, [(b"content-length", value)]), client.receive, client.send)

    assert (client.status, client.json) == (413, TOO_LARGE)
    assert client.reads == 0


async def test_every_content_length_header_is_checked():
    client = _Client([b"{}"])
    headers = [(b"content-length", b"2"), (b"content-length", str(MAX_REQUEST_BODY + 1).encode())]
    await app(_scope(VOLUMES, headers), client.receive, client.send)

    assert client.status == 413
    assert client.reads == 0


def test_chunked_body_over_the_limit_gets_413():
    """A generator body is sent with Transfer-Encoding: chunked, without Content-Length.

    FastAPI passes the HTTPException that receive() raises on: 413, not the
    400 it answers for other errors while it reads a body.
    """

    def chunks():
        yield _json_body(MAX_REQUEST_BODY + 1)

    response = TestClient(app).post(VOLUMES, content=chunks(), headers={"Content-Type": "application/json"})

    sent = response.request.headers
    assert "content-length" not in sent and sent["transfer-encoding"] == "chunked"
    assert response.status_code == 413
    assert response.json() == TOO_LARGE


async def test_chunked_body_is_counted_and_reading_stops_at_the_limit():
    chunk = b" " * (256 * 1024)
    client = _Client([b"{"] + [chunk] * 8 + [b"}"])  # 2 MiB, no Content-Length
    await app(_scope(VOLUMES, [(b"content-type", b"application/json")]), client.receive, client.send)

    assert (client.status, client.json) == (413, TOO_LARGE)
    assert client.reads == 5  # "{" and four chunks are 1 MiB + 1 byte


def test_bodies_within_the_limit_are_read():
    """Anonymous: the body is read and parsed (422 for a bad one), then the credentials are checked."""
    client = TestClient(app)
    for content in (_json_body(MAX_REQUEST_BODY), iter([_json_body(MAX_REQUEST_BODY)])):
        response = client.post(VOLUMES, content=content, headers={"Content-Type": "application/json"})
        assert response.status_code == 401, response.text
    response = client.post(VOLUMES, content=iter([b"{not json"]), headers={"Content-Type": "application/json"})
    assert response.status_code == 422


# --- the uploads ----------------------------------------------------------------


async def test_backup_upload_has_its_own_limit():
    client = _Client([b""])
    length = MAX_ARCHIVE_BYTES + MAX_REQUEST_BODY + 1
    await app(_scope("/api/v1/backup/upload", [(b"content-length", str(length).encode())]), client.receive, client.send)

    assert client.status == 413
    assert client.json == {"detail": too_large_detail(MAX_ARCHIVE_BYTES + MAX_REQUEST_BODY)}
    assert client.json["detail"] == "Request body too large: the limit is 257 MiB"
    assert client.reads == 0


@pytest.mark.parametrize("path", ["/api/v1/files/upload", "/api/v1/backup/upload"])
async def test_uploads_are_not_held_to_the_default_limit(path):
    """Anonymous: 401 from the credentials check, nothing read (see test_authz.py)."""
    client = _Client([b""])
    length = 200 << 20  # the file manager takes files of any size
    await app(_scope(path, [(b"content-length", str(length).encode())]), client.receive, client.send)

    assert client.status == 401
    assert client.reads == 0


# The file upload goes to the folder {pool} (see the pool fixture).
UPLOADS = ["/api/v1/files/upload?path={pool}", "/api/v1/backup/upload"]


@pytest.fixture
def pool(tmp_path, monkeypatch):
    """A pool folder for the file uploads: their destination is checked before the body is read."""
    folder = tmp_path.resolve() / "pools" / "data"
    folder.mkdir(parents=True)
    monkeypatch.setattr(files_router, "BASE_PATH", str(folder.parent))
    yield folder
    assert list(folder.iterdir()) == []  # nothing stored, no temporary file left


@pytest.fixture
def admin(tmp_path, monkeypatch, pool):
    (tmp_path / "backups").mkdir()
    monkeypatch.setattr(backup_router, "BACKUP_DIR", tmp_path / "backups")
    app.dependency_overrides[verify_token] = lambda: ADMIN
    yield TestClient(app)
    app.dependency_overrides.pop(verify_token, None)
    assert list((tmp_path / "backups").iterdir()) == []


@pytest.mark.parametrize("path", UPLOADS)
@pytest.mark.parametrize(
    "request_kwargs",
    [
        {"files": {"other": ("x.tar.gz", b"data")}},
        {"data": {"file": "not a file"}},
        {"json": {"file": "x.tar.gz"}},
        {},
    ],
    ids=["file-in-another-field", "file-as-text", "json", "no-body"],
)
def test_upload_without_a_file_in_the_file_field_gets_422(admin, pool, path, request_kwargs):
    response = admin.post(path.format(pool=pool), **request_kwargs)

    assert response.status_code == 422, response.text
    assert response.json() == {
        "detail": "No file was sent: send it as multipart/form-data in the field 'file'"
    }


@pytest.mark.parametrize("path", ["/api/v1/files/upload", "/api/v1/backup/upload"])
async def test_a_cancelled_upload_is_not_a_server_error(admin, pool, path):
    """The client goes away in the middle of the file (a cancelled upload, a closed tab)."""
    head = b'--b\r\nContent-Disposition: form-data; name="file"; filename="a.tar.gz"\r\n\r\n'
    client = _Client([head, b"x" * 1000], disconnect=True)
    headers = [(b"content-type", b"multipart/form-data; boundary=b")]

    query = urlencode({"path": str(pool)}).encode()
    await app(_scope(path, headers, query=query), client.receive, client.send)  # raises nothing

    assert client.status == 400
    assert client.json == {"detail": "The upload was interrupted: the client closed the connection"}


@pytest.mark.parametrize("path", UPLOADS)
def test_upload_takes_one_file(admin, pool, path):
    files = [("file", ("a.tar.gz", b"a")), ("file", ("b.tar.gz", b"b"))]
    response = admin.post(path.format(pool=pool), files=files)

    assert response.status_code == 400
    assert "Too many files" in response.json()["detail"]


# --- the middleware ---------------------------------------------------------------


class _FileIn(UploadSink):
    """Puts the upload in the file ``upload`` of a directory and answers its size."""

    def __init__(self, directory: Path):
        self.path = directory / "upload"

    def open(self, filename: str) -> int:
        return os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)

    def finish(self) -> dict:
        return {"size": self.path.stat().st_size}

    def discard(self) -> None:
        self.path.unlink(missing_ok=True)


def _upload_app(limit: int | None, directory: Path):
    """An upload endpoint like the application's, limited to ``limit`` bytes."""
    stub = FastAPI()

    @stub.post("/upload")
    async def upload(request: Request):
        return await receive_upload(request, _FileIn(directory))

    class Item(BaseModel):
        name: str

    @stub.post("/item")
    async def item(body: Item):
        return {"name": body.name}

    return RequestBodyLimitMiddleware(stub, limit=MAX_REQUEST_BODY, path_limits={"/upload": limit})


def test_an_upload_over_its_limit_gets_413_while_its_form_is_read(tmp_path):
    client = TestClient(_upload_app(limit=4096, directory=tmp_path))

    small = client.post("/upload", files={"file": ("a.bin", b"x" * 1000)})
    assert (small.status_code, small.json()) == (200, {"size": 1000})

    form = io.BytesIO()
    boundary = "b0undary"
    form.write(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="a.bin"\r\n\r\n'.encode())
    form.write(b"x" * 8192)
    form.write(f"\r\n--{boundary}--\r\n".encode())
    chunked = client.post(
        "/upload",
        content=iter([form.getvalue()]),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    assert (chunked.status_code, chunked.json()) == (413, {"detail": "Request body too large: the limit is 4096 bytes"})
    assert list(tmp_path.iterdir()) == []


def test_other_paths_of_an_application_keep_the_default_limit(tmp_path):
    client = TestClient(_upload_app(limit=None, directory=tmp_path))
    assert client.post("/upload", files={"file": ("a.bin", b"x" * (MAX_REQUEST_BODY + 10))}).json() == {
        "size": MAX_REQUEST_BODY + 10
    }
    assert client.post("/item", json={"name": "a"}).json() == {"name": "a"}
    response = client.post("/item", json={"name": "a" * MAX_REQUEST_BODY})
    assert response.status_code == 413


@pytest.mark.parametrize("scope_type", ["websocket", "lifespan"])
async def test_websocket_and_lifespan_pass_through_untouched(scope_type):
    seen = []

    async def inner(scope, receive, send):
        seen.append((scope, receive, send))

    middleware = RequestBodyLimitMiddleware(inner, limit=1)
    scope = {"type": scope_type, "path": "/ws/v1/events", "headers": [(b"content-length", b"999")]}

    async def receive():
        return {"type": "websocket.receive", "bytes": b"x" * 100}

    async def send(message):
        pass

    await middleware(scope, receive, send)

    assert seen == [(scope, receive, send)]


async def test_a_body_read_after_the_response_started_is_not_refused():
    """Starlette's FileResponse and StreamingResponse drain the body while they send.

    Raising then would turn a response that is being sent into a server error.
    """
    drained = []

    async def streaming(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        while (message := await receive())["type"] == "http.request":
            drained.append(len(message["body"]))
            if not message.get("more_body"):
                break
        await send({"type": "http.response.body", "body": b"done"})

    client = _Client([b"x" * 600, b"x" * 600])
    await RequestBodyLimitMiddleware(streaming, limit=1000)(_scope("/download", []), client.receive, client.send)

    assert drained == [600, 600]
    assert client.status == 200


# --- a real server ------------------------------------------------------------------


@contextmanager
def _uvicorn(asgi_app, **options):
    """``asgi_app`` in uvicorn (the pinned version, as in the image); yields the port."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(asgi_app, log_config=None, lifespan="off", **options))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 30
        while not server.started:
            assert thread.is_alive() and time.monotonic() < deadline, "uvicorn did not start"
            time.sleep(0.05)
        yield sock.getsockname()[1]
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()


@pytest.fixture(params=["h11", "httptools"])
def server(request):
    """The application in uvicorn, with each HTTP parser."""
    with _uvicorn(app, http=request.param) as port:
        yield port


def _post(port: int, path: str, body, headers: dict) -> tuple[int, dict]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        conn.request("POST", path, body=body, headers=headers, encode_chunked=not isinstance(body, bytes))
        response = conn.getresponse()
        return response.status, json.loads(response.read())
    finally:
        conn.close()


def test_a_real_server_answers_413_for_chunked_and_declared_bodies(server):
    chunk = b" " * (64 * 1024)
    chunked = iter([b"{"] + [chunk] * 48 + [b"}"])  # 3 MiB
    headers = {"Content-Type": "application/json", "Transfer-Encoding": "chunked"}
    assert _post(server, VOLUMES, chunked, headers) == (413, TOO_LARGE)

    declared = _json_body(3 * MAX_REQUEST_BODY)
    assert _post(server, VOLUMES, declared, {"Content-Type": "application/json"}) == (413, TOO_LARGE)

    small = iter([b'{"name": ', b'"photos"}'])
    status, _ = _post(server, VOLUMES, small, headers)
    assert status == 401  # read, then refused for the missing credentials


# --- WebSocket messages -------------------------------------------------------------
#
# Uvicorn reads a whole WebSocket message before the application gets it, also
# the first message of a socket, which src/websocket/events.py reads before the
# client is authenticated. RequestBodyLimitMiddleware cannot limit it (it sees
# only finished messages): the images pass --ws-max-size to uvicorn.
#
# Its websockets-sansio protocol (ws="auto" with websockets installed) also
# inflates and queues every message of one read from the socket before the
# application reads the first. With permessage-deflate, a message of 65,000
# bytes takes 86 on the wire, so one unauthenticated socket could make the
# supervisor hold hundreds of MiB whatever --ws-max-size is: the images pass
# --ws-per-message-deflate false as well.

DOCKERFILES = ["Dockerfile", "Dockerfile.test"]
LARGE_MESSAGE = "x" * (100 * 1024)


def _pinned(package: str) -> str:
    """The version of ``package`` in requirements.txt, which the image installs."""
    text = (SUPERVISOR / "requirements.txt").read_text()
    (version,) = re.findall(rf"^{re.escape(package)}(?:\[[^\]]*\])?==(\S+)$", text, re.MULTILINE)
    return version


def _uvicorn_options(dockerfile: str) -> dict:
    """The options of the uvicorn command of a supervisor image, parsed by uvicorn's command line."""
    text = (SUPERVISOR / dockerfile).read_text()
    (line,) = [line for line in text.splitlines() if line.startswith(("CMD ", "ENTRYPOINT "))]
    args = json.loads(line.split(" ", 1)[1])
    if args[:2] == ["/bin/bash", "-c"]:  # Dockerfile.test: "setup && exec uvicorn ..."
        args = shlex.split(args[2].split("exec ", 1)[1])
    assert args[:2] == ["uvicorn", "src.main:app"], args
    given = [arg for arg in args if arg == "--ws-max-size" or arg.startswith("--ws-max-size=")]
    assert len(given) == 1, f"{dockerfile}: uvicorn must run with --ws-max-size"
    return uvicorn_command.make_context("uvicorn", args[1:]).params


def test_both_images_limit_websocket_messages_alike():
    options = {name: _uvicorn_options(name) for name in DOCKERFILES}
    limits = {name: values["ws_max_size"] for name, values in options.items()}
    assert len(set(limits.values())) == 1, limits
    assert {values["ws"] for values in options.values()} == {"auto"}
    # Far below uvicorn's default of 16 MiB, and not below the longest first
    # message the application accepts (16 Ki characters of JSON with a JWT or
    # API token, which are ASCII). A real one is well under 1 KiB.
    assert events._MAX_AUTH_MESSAGE_CHARS <= limits["Dockerfile"] <= 64 * 1024
    auth = json.dumps({"type": "auth", "token": create_access_token({"sub": "u" * 32})})
    assert len(auth.encode()) < 1024


class _Recorder:
    """Records what the application receives on each WebSocket (the size of a message, not its text)."""

    def __init__(self, asgi_app):
        self.app = asgi_app
        self.sockets: list[list[dict]] = []
        self._finished = threading.Semaphore(0)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "websocket":
            await self.app(scope, receive, send)
            return
        received: list[dict] = []
        self.sockets.append(received)

        async def recording_receive():
            message = await receive()
            received.append({key: len(value) if key in ("text", "bytes") else value for key, value in message.items()})
            return message

        try:
            await self.app(scope, recording_receive, send)
        finally:
            self._finished.release()

    def wait(self) -> list[dict]:
        """Wait until the application is done with a socket; what it received on the last one."""
        assert self._finished.acquire(timeout=15), "the application did not finish"
        return self.sockets[-1]


@pytest.fixture
def ws_token(tmp_path):
    """A valid token, for a user in a throwaway database behind the WebSocket handshake."""
    path = tmp_path / "ws.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(User(username="ws_user", hashed_password="!", is_admin=False))
        session.commit()
    engine.dispose()
    maker = async_sessionmaker(create_async_engine(f"sqlite+aiosqlite:///{path}", poolclass=NullPool))
    with patch.object(events, "async_session", maker):
        yield create_access_token({"sub": "ws_user"})


def _first_message(port: int, message: str, compression: str | None) -> tuple[str | None, int | None]:
    """Send ``message`` first on /ws/v1/events: (the answer, None) or (None, the close code).

    The close code is None when the connection dropped without a close frame.
    """
    with websocket_connect(
        f"ws://127.0.0.1:{port}/ws/v1/events", proxy=None, compression=compression, close_timeout=2
    ) as ws:
        try:
            ws.send(message)
            return ws.recv(timeout=15), None
        except ConnectionClosed as exc:
            return None, exc.rcvd.code if exc.rcvd else None
        except OSError:  # reset while sending
            return None, None


def _image_websocket_options() -> dict:
    """The WebSocket options of uvicorn in supervisor/Dockerfile, for ``_uvicorn``."""
    options = _uvicorn_options("Dockerfile")
    return {name: options[name] for name in ("ws", "ws_max_size", "ws_per_message_deflate")}


# Browsers offer permessage-deflate. The image declines it (see
# test_uvicorn_does_not_accept_websocket_compression), so the 100 KiB of "x"
# are sent as they are, and refused while uvicorn reads them.
@pytest.mark.parametrize("compression", [None, "deflate"], ids=["plain", "compression-offered"])
def test_uvicorn_refuses_a_large_first_message_before_the_application_gets_it(ws_token, compression):
    """The pinned uvicorn and websockets, with the image's WebSocket options and implementation."""
    installed = (uvicorn.__version__, websockets.__version__)
    assert installed == (_pinned("uvicorn"), _pinned("websockets")), "install requirements-dev.txt"
    recorder = _Recorder(app)
    with _uvicorn(recorder, interface="asgi3", **_image_websocket_options()) as port:
        answer, code = _first_message(port, LARGE_MESSAGE, compression)
        received = recorder.wait()
        assert answer is None and code in (1009, None)  # "message too big", or the connection dropped
        assert [message["type"] for message in received] == ["websocket.connect", "websocket.disconnect"]
        assert received[-1]["code"] == 1009

        # The handshake of the web UI still works.
        answer, code = _first_message(port, json.dumps({"type": "auth", "token": ws_token}), compression)
        assert (json.loads(answer), code) == ({"type": "auth_ok"}, None)
        recorder.wait()


# Uvicorn's defaults accept permessage-deflate: the 100 KiB of "x" then take a
# few hundred bytes on the wire.
@pytest.mark.parametrize("compression", [None, "deflate"], ids=["plain", "compressed"])
def test_with_the_default_limit_the_application_reads_the_whole_message_first(compression):
    """Uvicorn's default --ws-max-size (16 MiB): read in full, then refused (4401) by the application."""
    recorder = _Recorder(app)
    with _uvicorn(recorder, interface="asgi3", ws="auto") as port:
        answer, code = _first_message(port, LARGE_MESSAGE, compression)
        received = recorder.wait()
    assert (answer, code) == (None, 4401)
    assert received == [
        {"type": "websocket.connect"},
        {"type": "websocket.receive", "text": len(LARGE_MESSAGE)},
    ]


def test_both_images_turn_off_websocket_compression():
    for name in DOCKERFILES:
        assert _uvicorn_options(name)["ws_per_message_deflate"] is False, name


def _image_environment(dockerfile: str) -> dict:
    """What the ENV lines of a supervisor image set before its CMD or ENTRYPOINT."""
    environment = {}
    for line in (SUPERVISOR / dockerfile).read_text().splitlines():
        if line.startswith(("CMD ", "ENTRYPOINT ")):
            break
        if line.startswith("ENV "):
            for item in shlex.split(line.split(" ", 1)[1]):
                name, _, value = item.partition("=")
                environment[name] = value
    return environment


def test_both_images_write_no_bytecode():
    """Each new container wrote about 1.8 MB of .pyc files into its layer, on
    the system disk (an SD card). docker exec (deploy/update.sh) and the
    health check get the image's environment too."""
    for name in DOCKERFILES:
        assert _image_environment(name).get("PYTHONDONTWRITEBYTECODE") == "1", name
        # The uvicorn command is still found and parsed.
        assert _uvicorn_options(name)["ws_max_size"] == 65536, name


@pytest.mark.parametrize("compression", [None, "deflate"], ids=["plain", "compression-offered"])
def test_uvicorn_does_not_accept_websocket_compression(ws_token, compression):
    """The image's options: no extension is negotiated, so a client cannot send compressed messages."""
    with _uvicorn(app, **_image_websocket_options()) as port:
        with websocket_connect(
            f"ws://127.0.0.1:{port}/ws/v1/events", proxy=None, compression=compression, close_timeout=2
        ) as ws:
            assert ws.protocol.extensions == []
            ws.send(json.dumps({"type": "auth", "token": ws_token}))
            assert json.loads(ws.recv(timeout=15)) == {"type": "auth_ok"}
