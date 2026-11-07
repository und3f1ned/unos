"""WebSocket authentication handshake (first-message auth, 4401/4403 close codes)."""
import logging
import queue
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import bcrypt
import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool
from starlette.websockets import WebSocketDisconnect

from src.auth import utils as auth_utils
from src.auth.models import APIToken, User
from src.auth.utils import create_access_token, generate_api_token
from src.config import get_settings
from src.database import Base
from src.main import app
from src.websocket import events

from .test_api_token_last_use import FAILURES, START

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
def admin_token():
    return create_access_token({"sub": "ws_admin"})


@pytest.fixture
def user_token():
    return create_access_token({"sub": "ws_user"})


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
        0.1 s), store it again only once it is an hour old (ADR-0248)."""
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
        """A full /data refuses the commit of the token's first use (ADR-0247): the session is
        rolled back before it reads the user, and the socket opens (ADR-0248)."""
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
        /data (ADR-0247): it is rolled back, the token still passes, and the socket streams on
        instead of closing with 1011 (ADR-0248)."""
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

    def test_expired_jwt_is_disconnected_from_events(self, client, fast_recheck):
        token = jwt.encode({"sub": "ws_user", "exp": int(time.time()) + 2},
                           get_settings().jwt_secret, algorithm=get_settings().jwt_algorithm)

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
