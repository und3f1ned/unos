"""WebSocket authentication handshake (first-message auth, 4401/4403 close codes), the problems and
the scrubs that the events socket sends administrators only (ADR-0332, ADR-0367), and the journal
socket of System > Logs (ADR-0366); the repair of a share's permissions that the events socket sends
administrators only (ADR-0363)."""
import asyncio
import gc
import importlib
import json
import logging
import queue
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import bcrypt
import jwt
import pytest
from docker.errors import NotFound
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool
from starlette.websockets import WebSocketDisconnect
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect as ws_connect

from src.auth import utils as auth_utils
from src.auth.models import APIToken, User, UserSession
from src.auth.utils import create_access_token, generate_api_token
from src.config import get_settings
from src.database import Base
from src.health import problems
from src.host import HostResult
from src.main import app
from src.shares import repair, repair_router
from src.storage import pools as storage_pools
from src.storage import scrubs
from src.storage.schemas import ScrubStatus
from src.websocket import events

from .test_alert_events import _found, _problem
from .test_logs_sources import FIELDS, NEWEST_FIRST, PAM, install_fake_journal, journalctl
from .test_api_token_last_use import FAILURES, START
from .session_tokens import session_token
from .test_containers import served_by_uvicorn

health_router = importlib.import_module("src.health.router")

_FAST_HASH = bcrypt.hashpw(b"irrelevant", bcrypt.gensalt(rounds=4)).decode()


@pytest.fixture
def ws_db(tmp_path):
    """A throwaway database behind the WebSocket handshake's user lookup."""
    path = tmp_path / "ws.db"
    sync_engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(sync_engine)
    with Session(sync_engine) as session:
        session.add_all([
            User(username="ws_admin", hashed_password=_FAST_HASH, is_admin=True),
            User(username="ws_user", hashed_password=_FAST_HASH, is_admin=False),
        ])
        session.commit()

    async_engine = create_async_engine(f"sqlite+aiosqlite:///{path}", poolclass=NullPool)
    maker = async_sessionmaker(async_engine, expire_on_commit=False)
    with patch.object(events, "async_session", maker):
        yield SimpleNamespace(engine=sync_engine, async_engine=async_engine)
    sync_engine.dispose()


@pytest.fixture
def client(ws_db):
    return TestClient(app)


@pytest.fixture
def admin_token(ws_db):
    return session_token(ws_db.engine, "ws_admin")


@pytest.fixture
def user_token(ws_db):
    return session_token(ws_db.engine, "ws_user")


def _auth(ws, token):
    ws.send_json({"type": "auth", "token": token})


def _expect_close(ws, code):
    with pytest.raises(WebSocketDisconnect) as exc_info:
        ws.receive_json()
    assert exc_info.value.code == code
    return exc_info.value


class TestEventsSocket:
    def test_auth_ok_then_stats(self, client, user_token):
        with client.websocket_connect("/ws/v1/events") as ws:
            _auth(ws, user_token)
            assert ws.receive_json() == {"type": "auth_ok"}
            assert ws.receive_json()["type"] == "stats"

    def test_api_token_is_accepted(self, client, ws_db):
        full_token, token_hash = generate_api_token()
        with Session(ws_db.engine) as session:
            user = session.query(User).filter_by(username="ws_user").one()
            session.add(APIToken(name="t", token_hash=token_hash, token_prefix=full_token[:12], user_id=user.id))
            session.commit()

        with client.websocket_connect("/ws/v1/events") as ws:
            _auth(ws, full_token)
            assert ws.receive_json() == {"type": "auth_ok"}

    def test_disabled_api_token_is_rejected(self, client, ws_db):
        full_token, token_hash = generate_api_token()
        with Session(ws_db.engine) as session:
            user = session.query(User).filter_by(username="ws_user").one()
            session.add(APIToken(name="t", token_hash=token_hash, token_prefix=full_token[:12],
                                 user_id=user.id, is_active=False))
            session.commit()

        with client.websocket_connect("/ws/v1/events") as ws:
            _auth(ws, full_token)
            _expect_close(ws, 4401)

    def test_missing_first_message_times_out(self, client):
        with patch.object(events, "WS_AUTH_TIMEOUT_SECONDS", 0.2):
            with client.websocket_connect("/ws/v1/events") as ws:
                closed = _expect_close(ws, 4401)
        assert closed.reason == "unauthorized"

    @pytest.mark.parametrize(
        "token_factory",
        [
            lambda: "not-a-jwt",
            lambda: "unos_" + "x" * 40,  # unknown API token
            lambda: jwt.encode(  # forged with the old public default secret
                {"sub": "ws_admin", "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                "change-me-in-production", algorithm="HS256"),
            lambda: jwt.encode(  # expired
                {"sub": "ws_admin", "exp": datetime.now(timezone.utc) - timedelta(minutes=1)},
                get_settings().jwt_secret, algorithm="HS256"),
            lambda: jwt.encode(  # no expiry
                {"sub": "ws_admin"}, get_settings().jwt_secret, algorithm="HS256"),
            lambda: create_access_token({"sub": "deleted_user"}),  # user does not exist
        ],
        ids=["garbage", "unknown-api-token", "forged", "expired", "no-exp", "unknown-user"],
    )
    def test_invalid_token_is_rejected(self, client, token_factory):
        with client.websocket_connect("/ws/v1/events") as ws:
            _auth(ws, token_factory())
            closed = _expect_close(ws, 4401)
        assert closed.reason == "unauthorized"

    @pytest.mark.parametrize(
        "send",
        [
            lambda ws, token: ws.send_text("not json"),
            lambda ws, token: ws.send_json({"token": token}),  # no type
            lambda ws, token: ws.send_json({"type": "hello", "token": token}),
            lambda ws, token: ws.send_json({"type": "auth"}),
            lambda ws, token: ws.send_json({"type": "auth", "token": 42}),
            lambda ws, token: ws.send_json(["auth", token]),
            lambda ws, token: ws.send_bytes(b'{"type": "auth"}'),
            lambda ws, token: ws.send_json({"type": "auth", "token": token}, mode="binary"),
        ],
        ids=["not-json", "no-type", "wrong-type", "no-token", "token-not-str", "array", "bytes", "binary-frame"],
    )
    def test_malformed_first_message_is_rejected(self, client, user_token, send):
        with client.websocket_connect("/ws/v1/events") as ws:
            send(ws, user_token)
            _expect_close(ws, 4401)

    def test_token_in_query_string_is_refused(self, client, user_token):
        with client.websocket_connect(f"/ws/v1/events?token={user_token}") as ws:
            # Even a correct auth message afterwards does not help.
            _auth(ws, user_token)
            _expect_close(ws, 4401)

    def test_token_is_rechecked_against_the_database(self, client, ws_db, user_token):
        with Session(ws_db.engine) as session:
            session.query(User).filter_by(username="ws_user").delete()
            session.commit()

        with client.websocket_connect("/ws/v1/events") as ws:
            _auth(ws, user_token)
            _expect_close(ws, 4401)


class _BlockingLogStream:
    """docker-py CancellableStream stand-in: blocks like a quiet container."""

    def __init__(self, lines):
        self._lines = list(lines)
        self.closed = threading.Event()

    def __iter__(self):
        return self

    def __next__(self):
        if self._lines:
            return self._lines.pop(0)
        self.closed.wait(timeout=10)
        raise StopIteration

    def close(self):
        self.closed.set()


class _FakeDockerClient:
    instances: list = []

    def __init__(self, stream, *args, **kwargs):
        self.stream = stream
        self.closed = False
        self.containers = SimpleNamespace(get=self._get)
        _FakeDockerClient.instances.append(self)

    def _get(self, container_id):
        return SimpleNamespace(logs=lambda **kwargs: self.stream)

    def close(self):
        self.closed = True


class TestContainerLogsSocket:
    def test_regular_user_is_forbidden(self, client, user_token):
        with patch("docker.DockerClient", side_effect=AssertionError("docker must not be reached")):
            with client.websocket_connect("/ws/v1/logs/abc") as ws:
                _auth(ws, user_token)
                closed = _expect_close(ws, 4403)
        assert closed.reason == "forbidden"

    def test_unauthenticated_never_reaches_docker(self, client):
        with patch("docker.DockerClient", side_effect=AssertionError("docker must not be reached")):
            with client.websocket_connect("/ws/v1/logs/abc") as ws:
                _auth(ws, "not-a-jwt")
                _expect_close(ws, 4401)

    def test_token_in_query_string_is_refused(self, client, admin_token):
        with patch("docker.DockerClient", side_effect=AssertionError("docker must not be reached")):
            with client.websocket_connect(f"/ws/v1/logs/abc?token={admin_token}") as ws:
                _expect_close(ws, 4401)

    def test_admin_streams_logs_and_cleans_up_on_disconnect(self, client, admin_token):
        stream = _BlockingLogStream([b"line 1\n", b"line 2\n"])
        _FakeDockerClient.instances = []
        with patch("docker.DockerClient", lambda *a, **kw: _FakeDockerClient(stream, *a, **kw)):
            with client.websocket_connect("/ws/v1/logs/abc") as ws:
                _auth(ws, admin_token)
                assert ws.receive_json() == {"type": "auth_ok"}
                assert ws.receive_json() == {"type": "log", "container_id": "abc", "data": "line 1\n"}
                assert ws.receive_json() == {"type": "log", "container_id": "abc", "data": "line 2\n"}
                # The stream now blocks waiting for output; the server must stay
                # responsive and release the stream once the client leaves.

            deadline = time.monotonic() + 5
            while not stream.closed.is_set() and time.monotonic() < deadline:
                time.sleep(0.01)
        assert stream.closed.is_set()
        assert _FakeDockerClient.instances and _FakeDockerClient.instances[0].closed

    def test_the_stream_starts_with_the_last_lines_and_follows_the_log(self, client, admin_token):
        # The container drawer of the web UI shows what this socket sends: the
        # last LOG_STREAM_TAIL_LINES lines, then each new one, each line with the
        # time Docker received it, as GET /containers/{id}/logs has it (ADR-0255).
        stream = _BlockingLogStream([b"line 1\n"])
        asked = []

        class _AskedDockerClient(_FakeDockerClient):
            def _get(self, container_id):
                asked.append(container_id)

                def logs(**kwargs):
                    asked.append(kwargs)
                    return self.stream

                return SimpleNamespace(logs=logs)

        with patch("docker.DockerClient", lambda *a, **kw: _AskedDockerClient(stream, *a, **kw)):
            with client.websocket_connect("/ws/v1/logs/abc") as ws:
                _auth(ws, admin_token)
                assert ws.receive_json() == {"type": "auth_ok"}
                assert ws.receive_json() == {"type": "log", "container_id": "abc", "data": "line 1\n"}

        assert events.LOG_STREAM_TAIL_LINES == 200
        assert asked == ["abc", {"stream": True, "follow": True, "tail": 200, "timestamps": True}]


class _GoneDockerClient(_FakeDockerClient):
    """Docker that no longer knows the container."""

    def _get(self, container_id):
        raise NotFound(f"No such container: {container_id}")


class TestTheLogSocketClosesWhenItsLogEnds:
    """What a browser sees of the end of a log, through uvicorn as in the image.

    The web UI shows a close with 1000 as "The log stream ended" and any other end
    as a lost connection (streamContainerLogs of webui/src/lib/api/containers.ts,
    ADR-0255). An endpoint that returns without closing makes uvicorn drop the
    connection without a close frame, which a browser reports as 1006.
    """

    @pytest.mark.parametrize(
        "docker_client,last_message",
        [
            # Docker's follow stream of a stopped container ends after its last lines.
            (
                lambda stream: _FakeDockerClient(stream),
                {"type": "log", "container_id": "abc", "data": "line 1\n"},
            ),
            (
                lambda stream: _GoneDockerClient(stream),
                {"type": "error", "message": "No such container: abc"},
            ),
        ],
        ids=["the-log-ends", "the-log-cannot-be-read"],
    )
    def test_the_server_closes_the_socket_with_1000(self, ws_db, admin_token, docker_client, last_message):
        stream = _FedLogStream()
        stream.feed(b"line 1\n")
        stream.feed(None)  # the end of the log
        made = []

        def make_client(*args, **kwargs):
            made.append(docker_client(stream))
            return made[-1]

        with patch("docker.DockerClient", make_client), served_by_uvicorn() as address:
            with ws_connect(f"ws://{address}/ws/v1/logs/abc", proxy=None, open_timeout=10, close_timeout=5) as ws:
                ws.send(json.dumps({"type": "auth", "token": admin_token}))
                assert json.loads(ws.recv(timeout=10)) == {"type": "auth_ok"}
                assert json.loads(ws.recv(timeout=10)) == last_message
                with pytest.raises(ConnectionClosed) as closed:
                    ws.recv(timeout=10)
            # The end released Docker too.
            _wait_for(lambda: made and made[0].closed)

        assert closed.value.rcvd is not None, "the server dropped the connection without a close frame (1006)"
        assert closed.value.rcvd.code == 1000


# ---------------------------------------------------------------------------
# Re-authentication while a socket is open
# ---------------------------------------------------------------------------


@pytest.fixture
def fast_recheck(monkeypatch):
    """Re-check credentials every 0.1 s and send (tiny) stats every 0.05 s.

    Returns the list of tokens checked so far: the handshake, then one entry
    per re-check.
    """
    monkeypatch.setattr(events, "WS_REAUTH_INTERVAL_SECONDS", 0.1)
    monkeypatch.setattr(events, "STATS_INTERVAL_SECONDS", 0.05)
    monkeypatch.setattr(events, "get_system_stats", lambda: {"type": "stats", "data": {}})
    checks = []
    real_authenticate = events.authenticate_token

    async def counting_authenticate(token, db):
        checks.append(token)
        return await real_authenticate(token, db)

    monkeypatch.setattr(events, "authenticate_token", counting_authenticate)
    return checks


def _read_until_closed(ws, timeout=5.0):
    """Receive messages until the server closes the socket; fail if it stays open."""
    received = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            received.append(ws.receive_json())
        except WebSocketDisconnect as closed:
            return closed, received
    pytest.fail(f"socket still open after {timeout} s; received {received[-3:]}")


def _wait_for(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            pytest.fail("condition not met in time")
        time.sleep(0.01)


def _set_user(ws_db, username, **values):
    with Session(ws_db.engine) as session:
        user = session.query(User).filter_by(username=username).one()
        for key, value in values.items():
            setattr(user, key, value)
        session.commit()


def _delete_user(ws_db, username):
    with Session(ws_db.engine) as session:
        session.query(User).filter_by(username=username).delete()
        session.commit()


class _QuietLogStream:
    """A container that logs ``lines`` and then stays quiet.

    If nothing closes it within ``give_up_after`` seconds it yields a
    sentinel line, so a socket that wrongly stays open fails the test
    instead of hanging it.
    """

    SENTINEL = b"still streaming\n"

    def __init__(self, lines, give_up_after=5.0):
        self._lines = list(lines)
        self._give_up_after = give_up_after
        self.closed = threading.Event()

    def __iter__(self):
        return self

    def __next__(self):
        if self._lines:
            return self._lines.pop(0)
        if self.closed.wait(timeout=self._give_up_after):
            raise StopIteration
        return self.SENTINEL

    def close(self):
        self.closed.set()


class _FedLogStream:
    """A container whose log lines the test hands over one at a time."""

    def __init__(self):
        self._lines = queue.Queue()
        self.closed = threading.Event()

    def feed(self, line):
        self._lines.put(line)

    def __iter__(self):
        return self

    def __next__(self):
        line = self._lines.get(timeout=10)
        if line is None:
            raise StopIteration
        return line

    def close(self):
        self.closed.set()
        self._lines.put(None)


@pytest.fixture
def fake_docker():
    """Patch docker.DockerClient; returns a function that installs a log stream."""
    _FakeDockerClient.instances = []
    streams = {}

    def install(stream):
        streams["stream"] = stream
        return stream

    with patch("docker.DockerClient",
               lambda *a, **kw: _FakeDockerClient(streams["stream"], *a, **kw)):
        yield install


class TestReauthentication:
    def test_deleted_user_is_disconnected_from_events(self, client, ws_db, user_token, fast_recheck):
        with client.websocket_connect("/ws/v1/events") as ws:
            _auth(ws, user_token)
            assert ws.receive_json() == {"type": "auth_ok"}
            assert ws.receive_json()["type"] == "stats"

            _delete_user(ws_db, "ws_user")
            closed, received = _read_until_closed(ws)

        assert (closed.code, closed.reason) == (4401, "unauthorized")
        assert all(message["type"] == "stats" for message in received)

    def test_an_ended_session_is_disconnected_from_events(self, client, ws_db, user_token, fast_recheck):
        """Signing out, a password change or an administrator ends a session (ADR-0329): the socket
        closes at its next check."""
        with client.websocket_connect("/ws/v1/events") as ws:
            _auth(ws, user_token)
            assert ws.receive_json() == {"type": "auth_ok"}
            assert ws.receive_json()["type"] == "stats"

            with Session(ws_db.engine) as session:
                session.query(UserSession).delete()
                session.commit()
            closed, received = _read_until_closed(ws)

        assert (closed.code, closed.reason) == (4401, "unauthorized")
        assert all(message["type"] == "stats" for message in received)

    def test_disabled_api_token_is_disconnected_from_events(self, client, ws_db, fast_recheck):
        full_token, token_hash = generate_api_token()
        with Session(ws_db.engine) as session:
            user = session.query(User).filter_by(username="ws_user").one()
            session.add(APIToken(name="t", token_hash=token_hash, token_prefix=full_token[:12],
                                 user_id=user.id))
            session.commit()

        with client.websocket_connect("/ws/v1/events") as ws:
            _auth(ws, full_token)
            assert ws.receive_json() == {"type": "auth_ok"}
            with Session(ws_db.engine) as session:
                session.query(APIToken).filter_by(token_hash=token_hash).update({"is_active": False})
                session.commit()
            closed, _ = _read_until_closed(ws)

        assert closed.code == 4401

    def test_rechecks_of_an_api_token_do_not_commit_its_last_use_again(self, client, ws_db, fast_recheck):
        """The handshake stores the token's last use; the re-checks, every minute (here every
        0.1 s), store it again only once it is an hour old (ADR-0252)."""
        full_token, token_hash = generate_api_token()
        with Session(ws_db.engine) as session:
            user = session.query(User).filter_by(username="ws_user").one()
            session.add(APIToken(name="t", token_hash=token_hash, token_prefix=full_token[:12], user_id=user.id))
            session.commit()
        commits = []
        event.listen(ws_db.async_engine.sync_engine, "commit", lambda connection: commits.append(connection))

        with client.websocket_connect("/ws/v1/events") as ws:
            _auth(ws, full_token)
            assert ws.receive_json() == {"type": "auth_ok"}
            deadline = time.monotonic() + 5
            while len(fast_recheck) < 4:
                assert ws.receive_json()["type"] == "stats"
                assert time.monotonic() < deadline, "credentials were not re-checked"

        assert len(commits) == 1
        with Session(ws_db.engine) as session:
            assert session.query(APIToken).filter_by(token_hash=token_hash).one().last_used_at is not None

    @staticmethod
    def _token_used_at(ws_db, last_used_at) -> tuple[str, str]:
        full_token, token_hash = generate_api_token()
        with Session(ws_db.engine) as session:
            user = session.query(User).filter_by(username="ws_user").one()
            session.add(APIToken(name="t", token_hash=token_hash, token_prefix=full_token[:12], user_id=user.id,
                                 last_used_at=last_used_at))
            session.commit()
        return full_token, token_hash

    @staticmethod
    def _last_use_warnings(caplog, error: str) -> list[str]:
        warnings = [r.getMessage() for r in caplog.records
                    if r.levelno == logging.WARNING and "last use of API token" in r.getMessage()]
        assert all(error in message for message in warnings), warnings
        return warnings

    @FAILURES
    def test_a_handshake_whose_last_use_cannot_be_stored_still_authenticates(
            self, client, ws_db, monkeypatch, caplog, failure):
        """A full /data refuses the commit of the token's first use (ADR-0251): the session is
        rolled back before it reads the user, and the socket opens (ADR-0252)."""
        full_token, token_hash = self._token_used_at(ws_db, None)
        error = failure(ws_db.async_engine.sync_engine, monkeypatch)

        with client.websocket_connect("/ws/v1/events") as ws:
            _auth(ws, full_token)
            assert ws.receive_json() == {"type": "auth_ok"}
            assert ws.receive_json()["type"] == "stats"

        with Session(ws_db.engine) as session:
            assert session.query(APIToken).filter_by(token_hash=token_hash).one().last_used_at is None
        assert len(self._last_use_warnings(caplog, error)) == 1

    @FAILURES
    def test_a_recheck_whose_last_use_cannot_be_stored_keeps_the_socket_open(
            self, client, ws_db, fast_recheck, monkeypatch, caplog, failure):
        """Once the stored last use is an hour old, a re-check commits it again and meets a full
        /data (ADR-0251): it is rolled back, the token still passes, and the socket streams on
        instead of closing with 1011 (ADR-0252)."""
        clock = {"now": START}
        monkeypatch.setattr(auth_utils, "_token_time", lambda: clock["now"])
        # Stored already: the handshake commits nothing, and every check that commits fails.
        full_token, token_hash = self._token_used_at(ws_db, START)
        error = failure(ws_db.async_engine.sync_engine, monkeypatch)

        with client.websocket_connect("/ws/v1/events") as ws:
            _auth(ws, full_token)
            assert ws.receive_json() == {"type": "auth_ok"}
            clock["now"] = START + timedelta(hours=1)
            checked = len(fast_recheck)
            deadline = time.monotonic() + 5
            while len(fast_recheck) < checked + 3:
                assert ws.receive_json()["type"] == "stats"
                assert time.monotonic() < deadline, "credentials were not re-checked"
            assert ws.receive_json()["type"] == "stats"

        with Session(ws_db.engine) as session:
            assert session.query(APIToken).filter_by(token_hash=token_hash).one().last_used_at == START
        # The first two of those re-checks are over: each one tried, and was rolled back.
        assert len(self._last_use_warnings(caplog, error)) >= 2

    def test_expired_jwt_is_disconnected_from_events(self, client, ws_db, fast_recheck):
        token = session_token(ws_db.engine, "ws_user", expires=int(time.time()) + 2)

        with client.websocket_connect("/ws/v1/events") as ws:
            _auth(ws, token)
            assert ws.receive_json() == {"type": "auth_ok"}
            closed, received = _read_until_closed(ws)

        assert closed.code == 4401
        assert received  # it streamed until the token expired

    def test_valid_session_keeps_streaming(self, client, user_token, fast_recheck):
        with client.websocket_connect("/ws/v1/events") as ws:
            _auth(ws, user_token)
            assert ws.receive_json() == {"type": "auth_ok"}
            deadline = time.monotonic() + 5
            # The handshake plus at least three re-checks, all passed.
            while len(fast_recheck) < 4:
                assert ws.receive_json()["type"] == "stats"
                assert time.monotonic() < deadline, "credentials were not re-checked"
            assert ws.receive_json()["type"] == "stats"
        assert set(fast_recheck) == {user_token}

    def test_failed_recheck_closes_with_internal_error(self, client, user_token, fast_recheck,
                                                       monkeypatch):
        real_user_for_token = events._user_for_token
        calls = 0

        async def failing_after_handshake(token):
            nonlocal calls
            calls += 1
            if calls > 1:
                raise RuntimeError("database is locked")
            return await real_user_for_token(token)

        monkeypatch.setattr(events, "_user_for_token", failing_after_handshake)
        with client.websocket_connect("/ws/v1/events") as ws:
            _auth(ws, user_token)
            assert ws.receive_json() == {"type": "auth_ok"}
            closed, _ = _read_until_closed(ws)

        # Not 4401: the client reconnects instead of logging the user out.
        assert closed.code == 1011

    def test_a_socket_that_closes_lets_its_recheck_in_progress_run_to_its_end(
            self, client, user_token, fast_recheck, monkeypatch):
        """Closing the socket ends its credential watch, and a re-check that has started still
        runs to its end (ADR-0312). Cancelled in its database query, a re-check discarded the
        session's connection; a second cancellation of that connection's close, the event loop's
        shutdown at the end of a test, left aiosqlite waiting for ever, which hung make check."""
        real_user_for_token = events._user_for_token
        real_watch = events._watch_credential
        started, release, watch_ended = threading.Event(), threading.Event(), threading.Event()
        ends = []
        calls = 0

        async def held_after_handshake(token):
            nonlocal calls
            calls += 1
            if calls == 1:
                return await real_user_for_token(token)  # the handshake
            started.set()
            try:
                while not release.is_set():
                    await asyncio.sleep(0.01)
                user = await real_user_for_token(token)
            except asyncio.CancelledError:
                ends.append("cancelled")
                raise
            ends.append(user.username)
            return user

        async def watch(token, **kwargs):
            try:
                return await real_watch(token, **kwargs)
            finally:
                watch_ended.set()

        monkeypatch.setattr(events, "_user_for_token", held_after_handshake)
        monkeypatch.setattr(events, "_watch_credential", watch)
        with client.websocket_connect("/ws/v1/events") as ws:
            _auth(ws, user_token)
            assert ws.receive_json() == {"type": "auth_ok"}
            assert started.wait(5), "no re-check started"
            ws.close()
            assert watch_ended.wait(5), "closing the socket did not end its credential watch"
            release.set()
            _wait_for(lambda: ends)

        assert ends == ["ws_user"]

    def test_a_recheck_that_fails_after_its_socket_closed_is_dropped_quietly(
            self, client, user_token, fast_recheck, monkeypatch, caplog):
        """The error of a re-check that ends after its socket closed is dropped, and asyncio does
        not report it as an exception that was never retrieved (ADR-0312)."""
        real_user_for_token = events._user_for_token
        started, release, failed = threading.Event(), threading.Event(), threading.Event()
        calls = 0

        async def failing_after_the_close(token):
            nonlocal calls
            calls += 1
            if calls == 1:
                return await real_user_for_token(token)  # the handshake
            started.set()
            while not release.is_set():
                await asyncio.sleep(0.01)
            failed.set()
            raise RuntimeError("database is locked")

        monkeypatch.setattr(events, "_user_for_token", failing_after_the_close)
        caplog.set_level(logging.ERROR, logger="asyncio")
        with client.websocket_connect("/ws/v1/events") as ws:
            _auth(ws, user_token)
            assert ws.receive_json() == {"type": "auth_ok"}
            assert started.wait(5), "no re-check started"
            ws.close()
            _wait_for(lambda: events._checks_left_running)  # the watch ended, the check runs on
            release.set()
            assert failed.wait(5)
            _wait_for(lambda: not events._checks_left_running)
            gc.collect()

        assert [r.getMessage() for r in caplog.records if "never retrieved" in r.getMessage()] == []
        assert not [r for r in caplog.records if "Could not re-check" in r.getMessage()]

    def test_demoted_admin_is_disconnected_from_quiet_logs(self, client, ws_db, admin_token,
                                                           fast_recheck, fake_docker):
        stream = fake_docker(_QuietLogStream([b"line 1\n"]))
        with client.websocket_connect("/ws/v1/logs/abc") as ws:
            _auth(ws, admin_token)
            assert ws.receive_json() == {"type": "auth_ok"}
            assert ws.receive_json()["data"] == "line 1\n"

            # No more output from the container: the re-check runs on its own timer.
            _set_user(ws_db, "ws_admin", is_admin=False)
            closed, received = _read_until_closed(ws)

            assert (closed.code, closed.reason) == (4403, "forbidden")
            assert received == []
            # Closing stopped the reader thread and released Docker.
            _wait_for(stream.closed.is_set)
            _wait_for(lambda: _FakeDockerClient.instances[0].closed)

    def test_deleted_admin_is_disconnected_from_logs(self, client, ws_db, admin_token,
                                                     fast_recheck, fake_docker):
        fake_docker(_QuietLogStream([]))
        with client.websocket_connect("/ws/v1/logs/abc") as ws:
            _auth(ws, admin_token)
            assert ws.receive_json() == {"type": "auth_ok"}
            _delete_user(ws_db, "ws_admin")
            closed, _ = _read_until_closed(ws)

        assert closed.code == 4401

    def test_admin_keeps_receiving_logs_across_rechecks(self, client, admin_token, fast_recheck,
                                                        fake_docker):
        stream = fake_docker(_FedLogStream())
        with client.websocket_connect("/ws/v1/logs/abc") as ws:
            _auth(ws, admin_token)
            assert ws.receive_json() == {"type": "auth_ok"}
            stream.feed(b"before\n")
            assert ws.receive_json()["data"] == "before\n"

            _wait_for(lambda: len(fast_recheck) >= 4)  # handshake + three re-checks
            stream.feed(b"after\n")
            assert ws.receive_json() == {"type": "log", "container_id": "abc", "data": "after\n"}
        _wait_for(stream.closed.is_set)


# ---------------------------------------------------------------------------
# The problems of the NAS on the events socket (system.alert-events, ADR-0332)
# ---------------------------------------------------------------------------


@pytest.fixture
def alerts(monkeypatch):
    """The events of the alerts that the problems send, recorded instead of sent."""
    sent = []

    async def deliver(event):
        sent.append(event)

    monkeypatch.setattr(health_router, "_deliver_alert", deliver)
    return sent


def _next_problems(ws, until=lambda data: True, timeout: float = 5.0) -> dict:
    """The next problems message on ``ws`` whose list ``until`` accepts, skipping the stats."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        message = ws.receive_json()
        if message["type"] == "problems" and until(message["data"]):
            return message
    pytest.fail("no such problems message")


class TestTheProblemsOnTheEventsSocket:
    def test_an_administrator_gets_the_problems_after_auth_ok_and_whenever_they_change(
            self, client, admin_token, fast_recheck, alerts):
        asyncio.run(problems.update("pools", _found(_problem(since="2026-11-10T10:00:00+00:00"))))

        with client.websocket_connect("/ws/v1/events") as ws:
            _auth(ws, admin_token)
            assert ws.receive_json() == {"type": "auth_ok"}
            assert ws.receive_json() == {"type": "problems", "data": [{
                "key": "pool_degraded:data", "severity": "error", "kind": "pool_degraded", "subject": "data",
                "title": "Pool 'data' is degraded", "detail": "1 device(s) of the pool are missing.",
                "since": "2026-11-10T10:00:00+00:00",
            }]}
            assert [ws.receive_json()["type"] for _ in range(2)] == ["repair", "stats"]

            asyncio.run(problems.update("tasks", _found(_problem("task_failed:task_1", "warning", "Task 'a' failed"))))
            assert [item["key"] for item in _next_problems(ws)["data"]] == ["pool_degraded:data", "task_failed:task_1"]

            asyncio.run(problems.update("pools", {}))
            asyncio.run(problems.update("tasks", {}))
            # The socket may send the list between the two updates as well.
            assert _next_problems(ws, until=lambda data: data == [])["data"] == []

    def test_an_unchanged_list_is_not_sent_again(self, client, admin_token, fast_recheck, alerts):
        with client.websocket_connect("/ws/v1/events") as ws:
            _auth(ws, admin_token)
            assert ws.receive_json() == {"type": "auth_ok"}
            assert ws.receive_json() == {"type": "problems", "data": []}
            assert [ws.receive_json()["type"] for _ in range(11)] == ["repair"] + ["stats"] * 10

    def test_a_regular_user_never_gets_them(self, client, user_token, fast_recheck, alerts):
        asyncio.run(problems.update("pools", _found(_problem())))

        with client.websocket_connect("/ws/v1/events") as ws:
            _auth(ws, user_token)
            assert ws.receive_json() == {"type": "auth_ok"}
            asyncio.run(problems.update("pools", _found(_problem(detail="Another detail"))))
            assert [ws.receive_json()["type"] for _ in range(10)] == ["stats"] * 10

    def test_an_administrator_who_is_no_longer_one_gets_none_from_the_next_check_on(
            self, client, ws_db, admin_token, fast_recheck, alerts):
        asyncio.run(problems.update("pools", _found(_problem())))

        with client.websocket_connect("/ws/v1/events") as ws:
            _auth(ws, admin_token)
            assert ws.receive_json() == {"type": "auth_ok"}
            assert len(ws.receive_json()["data"]) == 1

            _set_user(ws_db, "ws_admin", is_admin=False)
            assert _next_problems(ws)["data"] == []  # the banner goes

            asyncio.run(problems.update("pools", _found(_problem(detail="Another detail"))))
            checked = len(fast_recheck)
            deadline = time.monotonic() + 5
            while len(fast_recheck) < checked + 2:
                assert ws.receive_json()["type"] == "stats"
                assert time.monotonic() < deadline, "credentials were not re-checked"


# ---------------------------------------------------------------------------
# The scrubs on the events socket (webui.no-http-polling, ADR-0367)
# ---------------------------------------------------------------------------


def _scrub(status: str = "running", **fields) -> ScrubStatus:
    return ScrubStatus(pool="data", status=status, total_bytes=4 * 1024**3, **fields)


@pytest.fixture
def btrfs(monkeypatch):
    """pools.get_scrub_status as btrfs answers it: btrfs.answer (a status, or an exception it
    raises), btrfs.reads counts the reads; READ_INTERVAL_SECONDS 0.05 s."""
    btrfs = SimpleNamespace(answer=_scrub(bytes_scrubbed=1024**3), reads=0)

    def get_scrub_status(pool):
        assert pool == "data"
        btrfs.reads += 1
        if isinstance(btrfs.answer, Exception):
            raise btrfs.answer
        return btrfs.answer

    monkeypatch.setattr(storage_pools, "get_scrub_status", get_scrub_status)
    monkeypatch.setattr(scrubs, "READ_INTERVAL_SECONDS", 0.05)
    return btrfs


def _next_scrubs(ws, until=lambda data: True, timeout: float = 5.0) -> list:
    """The data of the next scrubs message on ``ws`` that ``until`` accepts, skipping the others."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        message = ws.receive_json()
        if message["type"] == "scrubs" and until(message["data"]):
            return message["data"]
    pytest.fail("no such scrubs message")


class TestTheScrubsOnTheEventsSocket:
    def test_nothing_is_sent_or_read_before_a_scrub_was_seen_running(self, client, admin_token, fast_recheck, btrfs):
        scrubs.note("data", _scrub("finished"))  # a status read of a pool whose scrub was never seen running
        with client.websocket_connect("/ws/v1/events") as ws:
            _auth(ws, admin_token)
            assert ws.receive_json() == {"type": "auth_ok"}
            assert [ws.receive_json()["type"] for _ in range(12)] == ["problems", "repair"] + ["stats"] * 10
        assert btrfs.reads == 0

    def test_an_administrator_follows_a_running_scrub_until_it_ends(self, client, admin_token, fast_recheck, btrfs):
        scrubs.note("data", _scrub(bytes_scrubbed=0))  # Start Scrub answered running
        with client.websocket_connect("/ws/v1/events") as ws:
            _auth(ws, admin_token)
            assert ws.receive_json() == {"type": "auth_ok"}
            assert ws.receive_json()["type"] == "problems"
            # What the socket knew, then what btrfs reports while the scrub runs, as GET answers it.
            assert ws.receive_json() == {"type": "scrubs", "data": [_scrub(bytes_scrubbed=0).model_dump()]}
            assert _next_scrubs(ws) == [{
                "pool": "data", "status": "running", "duration": None, "bytes_scrubbed": 1024**3,
                "total_bytes": 4 * 1024**3, "rate": None, "errors": 0, "last_run": None,
            }]

            btrfs.answer = _scrub("finished", duration=256)
            assert _next_scrubs(ws) == [_scrub("finished", duration=256).model_dump()]
            # It no longer runs: nothing is read, and nothing more is sent.
            reads = btrfs.reads
            assert [ws.receive_json()["type"] for _ in range(10)] == ["stats"] * 10
            assert btrfs.reads == reads

        # A new socket gets the last status after auth_ok.
        with client.websocket_connect("/ws/v1/events") as ws:
            _auth(ws, admin_token)
            assert [ws.receive_json()["type"] for _ in range(2)] == ["auth_ok", "problems"]
            assert ws.receive_json() == {"type": "scrubs", "data": [_scrub("finished", duration=256).model_dump()]}

    def test_a_status_that_cannot_be_read_is_sent_with_its_reason_and_not_read_again(
            self, client, admin_token, fast_recheck, btrfs):
        scrubs.note("data", _scrub())
        btrfs.answer = subprocess.TimeoutExpired(["btrfs", "scrub", "status", "/mnt/pools/data"], 60)
        with client.websocket_connect("/ws/v1/events") as ws:
            _auth(ws, admin_token)
            assert _next_scrubs(ws, until=lambda data: "error" in data[0]) == [{
                "pool": "data",
                "error": "btrfs scrub status did not answer within 60 seconds: pool data may not be responding",
            }]
            assert [ws.receive_json()["type"] for _ in range(10)] == ["stats"] * 10
        assert btrfs.reads == 1

    def test_a_regular_user_gets_none_and_nothing_is_read_for_them(self, client, user_token, fast_recheck, btrfs):
        scrubs.note("data", _scrub())
        with client.websocket_connect("/ws/v1/events") as ws:
            _auth(ws, user_token)
            assert ws.receive_json() == {"type": "auth_ok"}
            assert [ws.receive_json()["type"] for _ in range(10)] == ["stats"] * 10
        assert btrfs.reads == 0

    def test_nothing_is_read_while_no_administrator_is_connected(self, btrfs):
        async def wait():
            scrubs.note("data", _scrub())
            await asyncio.sleep(0.3)

        asyncio.run(wait())
        assert btrfs.reads == 0

    def test_a_read_that_a_request_answered_meanwhile_is_dropped(self, monkeypatch):
        """A Cancel Scrub that answers while a read runs: the read's running status is older."""
        reading, release = threading.Event(), threading.Event()

        def get_scrub_status(pool):
            reading.set()
            release.wait(5)
            return _scrub(bytes_scrubbed=2 * 1024**3)

        monkeypatch.setattr(storage_pools, "get_scrub_status", get_scrub_status)

        async def cancel_during_a_read():
            scrubs.note("data", _scrub())
            scrubs.follow()
            await asyncio.to_thread(reading.wait, 5)
            scrubs.note("data", _scrub("aborted"))
            release.set()
            await scrubs._reading
            scrubs.follow()  # it no longer runs: no read starts

        asyncio.run(cancel_during_a_read())
        assert scrubs.current() == [_scrub("aborted").model_dump()]




# ---------------------------------------------------------------------------
# The repair of a share's permissions on the events socket (shares.repair-in-background, ADR-0363)
# ---------------------------------------------------------------------------


def _repair_job(state: str = "running", visited: int = 0) -> repair.RepairResult:
    """The job as repair_router publishes it: the running repair, then its end."""
    return repair.RepairResult(
        share="Media", state=state, started_at=datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc),
        finished_at=None if state == "running" else datetime(2026, 11, 12, 11, 0, tzinfo=timezone.utc),
        visited=visited, changed=visited // 2, skipped=dict.fromkeys(repair.SKIP_REASONS, 0), skipped_paths=[],
        errors=0, error_samples=[], message="" if state == "running" else "Permissions repaired.",
    )


@pytest.fixture
def no_repair():
    """No repair has run since the start, and none is left behind."""
    repair_router._job = None
    yield
    repair_router._job = None


def _next_repair(ws, until=lambda data: True, timeout: float = 5.0) -> dict:
    """The next repair message on ``ws`` whose job ``until`` accepts, skipping the others."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        message = ws.receive_json()
        if message["type"] == "repair" and until(message["data"]):
            return message
    pytest.fail("no such repair message")


class TestTheRepairOnTheEventsSocket:
    def test_an_administrator_gets_the_job_after_auth_ok_and_whenever_it_changes(
            self, client, admin_token, fast_recheck, alerts, no_repair):
        repair_router._publish(_repair_job())

        with client.websocket_connect("/ws/v1/events") as ws:
            _auth(ws, admin_token)
            assert ws.receive_json() == {"type": "auth_ok"}
            assert ws.receive_json() == {"type": "problems", "data": []}
            assert ws.receive_json() == {"type": "repair", "data": {
                "share": "Media", "state": "running", "started_at": "2026-11-12T10:00:00Z", "finished_at": None,
                "visited": 0, "changed": 0, "skipped": dict.fromkeys(repair.SKIP_REASONS, 0), "skipped_paths": [],
                "errors": 0, "error_samples": [], "message": "",
            }}

            repair_router._publish(_repair_job(visited=5000))
            assert _next_repair(ws)["data"]["visited"] == 5000

            repair_router._publish(_repair_job("done", visited=9000))
            end = _next_repair(ws)["data"]
            assert (end["state"], end["visited"], end["finished_at"]) == ("done", 9000, "2026-11-12T11:00:00Z")

    def test_an_administrator_gets_none_after_auth_ok_before_any_repair(
            self, client, admin_token, fast_recheck, alerts, no_repair):
        # A web UI that reconnects after a restart of the supervisor learns that no repair runs.
        with client.websocket_connect("/ws/v1/events") as ws:
            _auth(ws, admin_token)
            assert ws.receive_json() == {"type": "auth_ok"}
            assert ws.receive_json()["type"] == "problems"
            assert ws.receive_json() == {"type": "repair", "data": None}
            assert [ws.receive_json()["type"] for _ in range(10)] == ["stats"] * 10

            repair_router._publish(_repair_job())
            assert _next_repair(ws)["data"]["state"] == "running"

    def test_an_unchanged_job_is_not_sent_again(self, client, admin_token, fast_recheck, alerts, no_repair):
        repair_router._publish(_repair_job("done", visited=9))

        with client.websocket_connect("/ws/v1/events") as ws:
            _auth(ws, admin_token)
            assert ws.receive_json() == {"type": "auth_ok"}
            assert ws.receive_json()["type"] == "problems"
            assert ws.receive_json()["type"] == "repair"
            assert [ws.receive_json()["type"] for _ in range(10)] == ["stats"] * 10

    def test_a_regular_user_never_gets_it(self, client, user_token, fast_recheck, alerts, no_repair):
        repair_router._publish(_repair_job())

        with client.websocket_connect("/ws/v1/events") as ws:
            _auth(ws, user_token)
            assert ws.receive_json() == {"type": "auth_ok"}
            repair_router._publish(_repair_job(visited=5000))
            assert [ws.receive_json()["type"] for _ in range(10)] == ["stats"] * 10


class TestSocketCaps:
    """Uvicorn's --limit-concurrency never refuses a WebSocket upgrade, while every open socket
    counts toward it: the supervisor caps its sockets itself, before it accepts one (ADR-0333)."""

    @staticmethod
    def _refused(client):
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/v1/events") as ws:
                ws.receive_json()

    def test_sockets_waiting_for_their_auth_message_are_capped(self, client, user_token, monkeypatch):
        monkeypatch.setattr(events, "MAX_SOCKETS_BEFORE_AUTH", 2)
        with client.websocket_connect("/ws/v1/events") as first, client.websocket_connect("/ws/v1/events"):
            _wait_for(lambda: events._sockets_before_auth == 2)
            self._refused(client)
            # One that signs in no longer waits: there is room again.
            _auth(first, user_token)
            assert first.receive_json() == {"type": "auth_ok"}
            _wait_for(lambda: events._sockets_before_auth == 1)
            with client.websocket_connect("/ws/v1/events") as third:
                _auth(third, user_token)
                assert third.receive_json() == {"type": "auth_ok"}
        _wait_for(lambda: (events._open_sockets, events._sockets_before_auth) == (0, 0))

    def test_open_sockets_are_capped_and_counted_out_when_they_close(self, client, user_token, monkeypatch):
        monkeypatch.setattr(events, "MAX_OPEN_SOCKETS", 2)
        with client.websocket_connect("/ws/v1/events") as first:
            _auth(first, user_token)
            assert first.receive_json() == {"type": "auth_ok"}
            with client.websocket_connect("/ws/v1/events") as second:
                _auth(second, user_token)
                assert second.receive_json() == {"type": "auth_ok"}
                self._refused(client)
            _wait_for(lambda: events._open_sockets == 1)
            with client.websocket_connect("/ws/v1/events") as again:
                _auth(again, user_token)
                assert again.receive_json() == {"type": "auth_ok"}
        _wait_for(lambda: events._open_sockets == 0)

    def test_a_refused_sign_in_counts_out_at_once(self, client):
        with client.websocket_connect("/ws/v1/events") as ws:
            _auth(ws, "not-a-token")
            _expect_close(ws, 4401)
        _wait_for(lambda: (events._open_sockets, events._sockets_before_auth) == (0, 0))


# ---------------------------------------------------------------------------
# The journal socket of System > Logs' Auto (ADR-0366)
# ---------------------------------------------------------------------------
#
# The socket takes the values of GET /api/v1/logs/entries in its URL, sends the newest entries as
# that request answers them, and then each new entry as ``journalctl --follow`` prints it on the
# host, from the newest entry it sent (its cursor), or, when it sent none, from the time it read.
# The journal is FakeJournal of test_logs_sources.py, whose follow starts through a patched
# ``src.host.stream_on_host``: nothing runs on this machine.


@pytest.fixture
def journal(monkeypatch):
    return install_fake_journal(monkeypatch)


JOURNAL_URL = "/ws/v1/journal"
FOLLOW = [
    "journalctl", "--no-pager", "--quiet", "--all", "--output=json", f"--output-fields={FIELDS}",
    "--follow", "--lines=all",
]


def _journal_signed_in(ws, token) -> list[dict]:
    """Sign in; the newest entries that the socket sends first."""
    _auth(ws, token)
    assert ws.receive_json() == {"type": "auth_ok"}
    first = ws.receive_json()
    assert first["type"] == "entries", first
    return first["data"]


class TestTheJournalSocket:
    def test_it_sends_the_newest_entries_then_each_new_one(self, client, admin_token, journal):
        with client.websocket_connect(f"{JOURNAL_URL}?source=journal&limit=3") as ws:
            newest = _journal_signed_in(ws, admin_token)
            assert [entry["message"] for entry in newest] == NEWEST_FIRST[:3]
            _wait_for(lambda: journal.followers)

            journal.now = datetime(2026, 11, 12, 10, 0, 0, tzinfo=timezone.utc).timestamp()
            started = journal.add("Started Session 4 of User admin.", minutes_ago=0, identifier="systemd",
                                  unit="init.scope", facility=3)
            journal.add("Accepted publickey for admin from 192.0.2.10", minutes_ago=0, identifier="sshd",
                        unit="ssh.service", facility=4)
            first, second = ws.receive_json(), ws.receive_json()
            # As GET /entries answers an entry.
            assert first == {"type": "entry", "data": {
                "timestamp": "2026-11-12T10:00:00+00:00", "facility": "3", "priority": "info",
                "message": "Started Session 4 of User admin.", "source": "journal", "unit": "systemd",
                "cursor": started["__CURSOR"],
            }}
            assert second["type"] == "entry"
            assert second["data"]["message"] == "Accepted publickey for admin from 192.0.2.10"

        # The newest entries as GET /entries reads them, then a follow after the newest of them.
        snapshot, follow = journal.calls
        assert snapshot == journalctl("journal", lines=3)
        assert follow == [*FOLLOW, f"--after-cursor={newest[0]['cursor']}"]
        _wait_for(lambda: journal.followers[0].stopped)

    def test_the_source_level_and_search_choose_the_new_entries_too(self, client, admin_token, journal):
        with client.websocket_connect(f"{JOURNAL_URL}?source=auth&priority=notice&search=FAIL&limit=50") as ws:
            newest = _journal_signed_in(ws, admin_token)
            assert [entry["message"] for entry in newest] == [PAM]
            _wait_for(lambda: journal.followers)

            journal.now = time.time()
            journal.add("Failed password for bob from 192.0.2.20 port 4000 ssh2", minutes_ago=0,
                        priority=6, identifier="sshd", facility=4)  # info: not this level
            journal.add("Failed to start backup.service", minutes_ago=0, priority=3, identifier="systemd",
                        facility=3)  # not of the auth facilities
            journal.add("pam_unix(sudo:auth): conversation failed", minutes_ago=0, priority=5,
                        identifier="sudo", facility=10)
            message = ws.receive_json()
            assert (message["type"], message["data"]["message"]) == ("entry", "pam_unix(sudo:auth): conversation failed")

        assert journal.calls[1] == [
            *FOLLOW, f"--after-cursor={newest[0]['cursor']}", "--priority=notice", "--grep=FAIL",
            "--case-sensitive=false", "SYSLOG_FACILITY=4", "SYSLOG_FACILITY=10",
        ]

    def test_without_an_entry_to_follow_on_after_it_follows_from_when_it_read(self, client, admin_token, journal):
        before = int(time.time())
        with client.websocket_connect(f"{JOURNAL_URL}?source=cron&search=backup.sh&limit=200") as ws:
            assert _journal_signed_in(ws, admin_token) == []
            _wait_for(lambda: journal.followers)
            journal.now = time.time()
            journal.add("(root) CMD (/usr/local/bin/backup.sh)", minutes_ago=0, identifier="CRON",
                        unit="cron.service", facility=9)
            message = ws.receive_json()
            assert (message["type"], message["data"]["message"]) == ("entry", "(root) CMD (/usr/local/bin/backup.sh)")

        follow = journal.calls[1]
        since = int(follow[len(FOLLOW)].removeprefix("--since=@"))
        assert before <= since <= time.time()
        assert follow == [*FOLLOW, f"--since=@{since}", "--grep=backup\\.sh", "--case-sensitive=false",
                          "--unit=cron.service"]

    def test_the_follow_ends_when_the_client_leaves(self, client, admin_token, journal):
        with client.websocket_connect(f"{JOURNAL_URL}?limit=1") as ws:
            _journal_signed_in(ws, admin_token)
            _wait_for(lambda: journal.followers)
        _wait_for(lambda: journal.followers[0].stopped)


class TestTheJournalSocketsErrors:
    """An error closes the socket with 1000 after {"type": "error"} with its reason; the tab then
    shows the reason as it shows the error of GET /entries."""

    @pytest.mark.parametrize("query, message", [
        ("source=syslog", "Unknown log source: syslog"),
        ("since=yesterday",
         "Unknown time range: yesterday. Give it as <N> minutes ago, <N> hours ago or <N> days ago"),
        ("search=tab%09here", "The search cannot contain control characters"),
        ("search=" + "x" * 257, "search: String should have at most 256 characters"),
        ("limit=0", "limit: Input should be greater than or equal to 1"),
        ("limit=1001", "limit: Input should be less than or equal to 1000"),
        ("priority=verbose",
         "priority: Input should be 'emerg', 'alert', 'crit', 'err', 'warning', 'notice', 'info' or 'debug'"),
    ])
    def test_a_value_it_does_not_take_is_refused_before_the_host(self, client, admin_token, journal, query, message):
        with client.websocket_connect(f"{JOURNAL_URL}?{query}") as ws:
            _auth(ws, admin_token)
            closed, received = _read_until_closed(ws)
        assert received == [{"type": "auth_ok"}, {"type": "error", "message": message}]
        assert closed.code == 1000
        assert journal.calls == []

    def test_a_journal_that_cannot_be_read_says_why(self, client, admin_token, journal):
        journal.answer = HostResult(1, "", "Failed to get journal fields: Bad message")
        with client.websocket_connect(f"{JOURNAL_URL}?limit=200") as ws:
            _auth(ws, admin_token)
            closed, received = _read_until_closed(ws)
        assert received == [{"type": "auth_ok"}, {
            "type": "error", "message": "Could not read the host's journal: Failed to get journal fields: Bad message",
        }]
        assert closed.code == 1000
        assert journal.followers == []

    def test_a_follow_that_cannot_start_says_why(self, client, admin_token, journal):
        journal.cannot_follow = FileNotFoundError(2, "No such file or directory", "nsenter")
        with client.websocket_connect(f"{JOURNAL_URL}?limit=200") as ws:
            _journal_signed_in(ws, admin_token)
            closed, received = _read_until_closed(ws)
        assert received == [{
            "type": "error",
            "message": "Could not read the host's journal: [Errno 2] No such file or directory: 'nsenter'",
        }]
        assert closed.code == 1000

    def test_a_follow_that_ends_by_itself_says_why(self, client, admin_token, journal):
        with client.websocket_connect(f"{JOURNAL_URL}?limit=200") as ws:
            _journal_signed_in(ws, admin_token)
            _wait_for(lambda: journal.followers)
            journal.followers[0].end(1, "Failed to iterate through journal: Bad message\n")
            closed, received = _read_until_closed(ws)
        assert received == [{
            "type": "error", "message": "Could not read the host's journal: Failed to iterate through journal: Bad message",
        }]
        assert closed.code == 1000


class TestTheJournalSocketsRoles:
    """Only administrators read the logs (ADR-0030): the socket checks it before the host is reached,
    and again while it is open (ADR-0031)."""

    def test_a_regular_user_is_refused_before_the_host(self, client, user_token, journal):
        with client.websocket_connect(f"{JOURNAL_URL}?limit=200") as ws:
            _auth(ws, user_token)
            assert _expect_close(ws, 4403).reason == "forbidden"
        assert journal.calls == []

    def test_no_token_and_a_token_in_the_url_are_refused_before_the_host(self, client, admin_token, journal):
        with client.websocket_connect(JOURNAL_URL) as ws:
            _auth(ws, "not-a-token")
            _expect_close(ws, 4401)
        with client.websocket_connect(f"{JOURNAL_URL}?token={admin_token}") as ws:
            _expect_close(ws, 4401)
        assert journal.calls == []

    def test_an_administrator_who_is_no_longer_one_is_closed_out_and_the_follow_ends(
            self, client, ws_db, admin_token, journal, fast_recheck):
        with client.websocket_connect(f"{JOURNAL_URL}?limit=1") as ws:
            _journal_signed_in(ws, admin_token)
            _wait_for(lambda: journal.followers)
            _set_user(ws_db, "ws_admin", is_admin=False)
            closed, received = _read_until_closed(ws)
        assert received == []
        assert closed.code == 4403
        _wait_for(lambda: journal.followers[0].stopped)
