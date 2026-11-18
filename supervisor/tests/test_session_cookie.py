"""The web UI's session in an HttpOnly cookie (ADR-0397): a sign-in that asks for it sets the cookie and
answers no token, the cookie authenticates requests and WebSockets only from the web UI's own origin
and with the session key that only the sign-in's answer carries, signing out deletes it, and programs
keep Bearer tokens."""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import bcrypt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool
from starlette.websockets import WebSocketDisconnect

from src.auth import router as auth_router
from src.auth.models import APIToken, User, UserSession
from src.auth.utils import (
    COOKIE_SESSION_ONLY, CROSS_ORIGIN, SESSION_COOKIE, SESSION_KEY_HEADER, SESSION_KEY_MISMATCH,
    decode_access_token, generate_api_token, session_key,
)
from src.database import Base, get_db
from src.main import app
from src.main import limiter as app_limiter
from src.security.models import LoginAttempt
from src.websocket import events

from .session_tokens import session_token
from .test_ws_auth import _read_until_closed
from .test_authz import PUBLIC, _call, _http_operations, no_side_effects  # noqa: F401 (an autouse fixture)

API = "/api/v1"
PASSWORD = "Cookie-pass-2026"
# The TestClient's host: the web UI's own origin is this host over HTTPS.
OWN_ORIGIN = "https://testserver"
SAME = {"Origin": OWN_ORIGIN, "Sec-Fetch-Site": "same-origin"}


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A temporary database with the administrator owner and the user alice, the API and the
    WebSockets on it, cheap bcrypt."""
    path = tmp_path / "cookie.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    cheap = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode()
    with Session(engine) as session:
        owner = User(username="owner", hashed_password=cheap, is_admin=True)
        alice = User(username="alice", hashed_password=cheap, is_admin=False)
        session.add_all([owner, alice])
        session.commit()
        ids = SimpleNamespace(owner_id=owner.id, alice_id=alice.id)
    maker = async_sessionmaker(create_async_engine(f"sqlite+aiosqlite:///{path}", poolclass=NullPool),
                               expire_on_commit=False)

    async def override_get_db():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(events, "async_session", maker)
    monkeypatch.setattr("src.audit.router.AUDIT_FILE", str(tmp_path / "audit.jsonl"))
    auth_router.limiter.reset()
    app_limiter.reset()
    try:
        yield SimpleNamespace(engine=engine, **vars(ids), client=TestClient(app, raise_server_exceptions=False))
    finally:
        app.dependency_overrides.pop(get_db, None)
        auth_router.limiter.reset()
        app_limiter.reset()
        engine.dispose()


def bare_cookie(token: str) -> dict[str, str]:
    """The Cookie header that a browser sends with the session cookie. Sent as a header, not through
    the client's cookie jar, so that no test depends on what the jar keeps."""
    return {"Cookie": f"{SESSION_COOKIE}={token}"}


def key_of(token: str) -> str:
    return session_key(decode_access_token(token)[1])


def cookie(token: str) -> dict[str, str]:
    """What the web UI's page sends: the session cookie and its session key."""
    return {**bare_cookie(token), SESSION_KEY_HEADER: key_of(token)}


def set_cookies(response) -> list[str]:
    return response.headers.get_list("set-cookie")


def cookie_sign_in(db, username: str = "alice", headers: dict | None = None):
    return db.client.post(
        f"{API}/auth/login",
        json={"username": username, "password": PASSWORD, "cookie": True},
        headers=SAME if headers is None else headers,
    )


def session_of(response) -> str:
    """The session token that the answer's Set-Cookie carries."""
    [header] = set_cookies(response)
    name, _, rest = header.partition("=")
    assert name == SESSION_COOKIE
    return rest.split(";", 1)[0]


def sessions_of(db, user_id: int) -> int:
    with Session(db.engine) as session:
        return session.scalar(select(func.count()).select_from(UserSession).where(UserSession.user_id == user_id))


def login_attempts(db) -> int:
    with Session(db.engine) as session:
        return session.scalar(select(func.count()).select_from(LoginAttempt))


class TestSignIn:
    def test_a_cookie_sign_in_sets_an_httponly_strict_cookie_and_answers_no_token(self, db):
        response = cookie_sign_in(db)

        assert response.status_code == 200, response.text
        [header] = set_cookies(response)
        attributes = [part.strip().lower() for part in header.split(";")[1:]]
        assert set(attributes) == {"httponly", "path=/", "samesite=strict", "secure"}
        token = session_of(response)
        assert token and token not in response.text
        key = key_of(token)
        assert response.json() == {"access_token": "", "token_type": "cookie", "requires_totp": False,
                                   "session_key": key}
        assert key not in token
        assert sessions_of(db, db.alice_id) == 1
        me = db.client.get(f"{API}/auth/me", headers=cookie(token))
        assert me.status_code == 200 and me.json()["username"] == "alice"

    def test_the_cookie_is_a_host_cookie_of_the_browser_session(self):
        """__Host-: only from a secure origin, for every path, and no other host of the domain can set
        or shadow it. No Max-Age or Expires: it ends with the browser, at the latest when its session
        expires."""
        assert SESSION_COOKIE == "__Host-unos-session"

    def test_a_sign_in_without_the_cookie_answers_the_token_as_before(self, db):
        """Programs, such as scripts/dev/api.py, sign in for a Bearer token."""
        response = db.client.post(f"{API}/auth/login", json={"username": "alice", "password": PASSWORD})

        assert response.status_code == 200
        assert response.json()["access_token"] and response.json()["token_type"] == "bearer"
        assert response.json()["session_key"] == key_of(response.json()["access_token"])
        assert set_cookies(response) == []

    @pytest.mark.parametrize("headers", [
        {},  # no Origin: a POST from the web UI always has one
        {"Origin": "https://evil.example"},
        {"Origin": "https://testserver:8443"},  # another port of the same host: an app's
        {"Origin": "http://testserver"},  # the same host without TLS
        {"Origin": "null"},
        {"Origin": OWN_ORIGIN, "Sec-Fetch-Site": "same-site"},
        {"Origin": OWN_ORIGIN, "Sec-Fetch-Site": "cross-site"},
    ])
    def test_a_cookie_sign_in_from_another_origin_is_refused_before_the_password(self, db, headers):
        response = cookie_sign_in(db, headers=headers)

        assert response.status_code == 403
        assert response.json()["detail"] == CROSS_ORIGIN
        assert set_cookies(response) == []
        assert sessions_of(db, db.alice_id) == 0
        assert login_attempts(db) == 0

    def test_a_wrong_password_sets_no_cookie(self, db):
        response = db.client.post(
            f"{API}/auth/login", json={"username": "alice", "password": "wrong", "cookie": True}, headers=SAME
        )

        assert response.status_code == 401
        assert set_cookies(response) == []

    def test_the_two_factor_step_sets_no_cookie(self, db):
        with Session(db.engine) as session:
            session.get(User, db.alice_id).totp_enabled = True
            session.get(User, db.alice_id).totp_secret = "JBSWY3DPEHPK3PXP"
            session.commit()

        response = cookie_sign_in(db)

        assert response.status_code == 200
        assert response.json()["requires_totp"] is True
        assert response.json()["session_key"] == ""
        assert set_cookies(response) == []

    def test_the_cookie_field_takes_only_a_boolean(self, db):
        response = db.client.post(
            f"{API}/auth/login", json={"username": "alice", "password": PASSWORD, "cookie": "yes"}, headers=SAME
        )

        assert response.status_code == 422


class TestOrigin:
    def test_a_get_with_the_cookie_needs_no_origin(self, db):
        """A browser sends no Origin with a same-origin GET; it sends Sec-Fetch-Site same-origin."""
        token = session_of(cookie_sign_in(db))

        assert db.client.get(f"{API}/auth/me", headers=cookie(token)).status_code == 200
        assert db.client.get(
            f"{API}/auth/me", headers={**cookie(token), "Sec-Fetch-Site": "same-origin"}
        ).status_code == 200

    @pytest.mark.parametrize("headers", [
        {"Sec-Fetch-Site": "same-site"},  # a page on another port of the NAS
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "none"},  # the address typed in the browser
        {"Origin": "https://testserver:8443"},
        {"Origin": "https://evil.example"},
    ])
    def test_a_get_with_the_cookie_from_another_origin_is_refused(self, db, headers):
        token = session_of(cookie_sign_in(db))

        response = db.client.get(f"{API}/auth/me", headers={**cookie(token), **headers})

        assert response.status_code == 403
        assert response.json()["detail"] == CROSS_ORIGIN

    @pytest.mark.parametrize("headers", [
        {},
        {"Origin": "https://evil.example"},
        {"Origin": "https://testserver:8443"},
        {"Origin": "http://testserver"},
        {"Origin": "null"},
        {"Origin": OWN_ORIGIN, "Sec-Fetch-Site": "same-site"},
    ])
    def test_a_change_with_the_cookie_needs_the_own_origin(self, db, headers):
        token = session_of(cookie_sign_in(db))

        response = db.client.post(f"{API}/auth/logout", headers={**cookie(token), **headers})

        assert response.status_code == 403
        assert response.json()["detail"] == CROSS_ORIGIN
        assert sessions_of(db, db.alice_id) == 1

    def test_the_origin_is_compared_without_case_and_with_the_port(self, db):
        token = session_of(cookie_sign_in(db))

        assert db.client.get(
            f"{API}/auth/me", headers={**cookie(token), "Origin": "HTTPS://TestServer"}
        ).status_code == 200
        other_port = db.client.get(
            f"{API}/auth/me", headers={**cookie(token), "Host": "testserver:42443", "Origin": OWN_ORIGIN}
        )
        assert other_port.status_code == 403

    def test_every_route_refuses_the_cookie_without_its_session_key(self, db):
        """A server elsewhere that the browser sent the cookie to, such as an app on another port of
        the NAS, has no session key: its requests, Origin and all, are refused before anything runs."""
        token = session_token(db.engine, "owner")
        other = session_token(db.engine, "owner")
        problems = []
        for method, path, _ in _http_operations():
            if (method, path) in PUBLIC:
                continue
            for key in ({}, {SESSION_KEY_HEADER: key_of(other)}, {SESSION_KEY_HEADER: ""}):
                response = _call(db.client, method, path, {**bare_cookie(token), **SAME, **key})
                if response.status_code != 401 or response.json().get("detail") != SESSION_KEY_MISMATCH:
                    problems.append(f"{method} {path} {key}: {response.status_code}")
        assert problems == []
        assert sessions_of(db, db.owner_id) == 2

    def test_the_session_key_alone_is_no_credential(self, db):
        token = session_of(cookie_sign_in(db))

        response = db.client.get(f"{API}/auth/me", headers={SESSION_KEY_HEADER: key_of(token)})

        assert response.status_code == 401
        assert response.json()["detail"] == "Not authenticated"

    def test_every_route_refuses_a_cookie_from_another_origin(self, db):
        """Every route that takes a credential checks the cookie's origin before anything else: a change
        without the own Origin, and any request that the browser marks as from another site."""
        token = session_token(db.engine, "owner")
        problems = []
        for method, path, _ in _http_operations():
            if (method, path) in PUBLIC:
                continue
            refused = [{"Sec-Fetch-Site": "same-site"}, {"Origin": "https://testserver:8443"}]
            if method not in ("GET", "HEAD"):
                refused.append({})
            for headers in refused:
                response = _call(db.client, method, path, {**cookie(token), **headers})
                if response.status_code != 403 or response.json().get("detail") != CROSS_ORIGIN:
                    problems.append(f"{method} {path} {headers}: {response.status_code}")
        assert problems == []
        assert sessions_of(db, db.owner_id) == 1

    def test_a_bearer_token_needs_no_origin(self, db):
        """A program's request carries its token itself: no browser sends it on its own."""
        token = session_token(db.engine, "alice")

        response = db.client.post(f"{API}/auth/logout", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 200
        assert set_cookies(response) == []


class TestCredential:
    def test_the_token_of_the_cookie_is_no_bearer_token(self, db):
        """A server that got the cookie's value, as one on another port of the NAS gets it, cannot use
        it as a Bearer token: the session of the cookie counts only in the cookie, with its key."""
        token = session_of(cookie_sign_in(db))

        for headers in ({}, {SESSION_KEY_HEADER: key_of(token)}, SAME):
            response = db.client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {token}", **headers})
            assert response.status_code == 401
            assert response.json()["detail"] == COOKIE_SESSION_ONLY
        assert db.client.get(f"{API}/auth/me", headers=cookie(token)).status_code == 200

    def test_the_session_of_a_program_may_go_into_the_cookie(self, db):
        """The other way round is no risk: the cookie still needs the key."""
        signed_in = db.client.post(f"{API}/auth/login", json={"username": "alice", "password": PASSWORD}).json()
        token = signed_in["access_token"]

        response = db.client.get(f"{API}/auth/me", headers={**bare_cookie(token), SESSION_KEY_HEADER: signed_in["session_key"]})

        assert response.status_code == 200
        assert db.client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200

    def test_the_authorization_header_wins_over_the_cookie(self, db):
        alice = session_of(cookie_sign_in(db))
        owner = session_token(db.engine, "owner")

        response = db.client.get(
            f"{API}/auth/me", headers={**cookie(alice), "Authorization": f"Bearer {owner}"}
        )

        assert response.json()["username"] == "owner"

    def test_an_api_token_in_the_cookie_is_refused(self, db):
        """Only a sign-in sets the cookie, and it holds a session: an API token is no session."""
        full, token_hash = generate_api_token()
        with Session(db.engine) as session:
            session.add(APIToken(name="t", token_hash=token_hash, token_prefix=full[:12], user_id=db.alice_id))
            session.commit()

        response = db.client.get(f"{API}/auth/me", headers=bare_cookie(full))

        assert response.status_code == 401
        assert db.client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {full}"}).status_code == 200

    def test_without_a_credential_the_answer_is_401(self, db):
        response = db.client.get(f"{API}/auth/me")

        assert response.status_code == 401
        assert response.json()["detail"] == "Not authenticated"

    def test_an_ended_session_in_the_cookie_is_refused(self, db):
        token = session_of(cookie_sign_in(db))
        with Session(db.engine) as session:
            session.query(UserSession).delete()
            session.commit()

        response = db.client.get(f"{API}/auth/me", headers=cookie(token))

        assert response.status_code == 401
        assert response.json()["detail"] == "Session ended"

    def test_a_cookie_session_manages_api_tokens(self, db):
        """The cookie holds a session: what needs a session, such as a new API token, takes it (ADR-0253)."""
        token = session_of(cookie_sign_in(db))

        response = db.client.post(f"{API}/auth/tokens", json={"name": "script"}, headers={**cookie(token), **SAME})

        assert response.status_code == 200, response.text
        assert response.json()["token"].startswith("unos_")

    def test_a_password_change_keeps_the_cookie_session(self, db):
        token = session_of(cookie_sign_in(db))
        other = session_token(db.engine, "alice")

        response = db.client.post(
            f"{API}/auth/change-password",
            json={"current_password": PASSWORD, "new_password": "Cookie-new-pass-2026"},
            headers={**cookie(token), **SAME},
        )

        assert response.status_code == 200, response.text
        assert db.client.get(f"{API}/auth/me", headers=cookie(token)).status_code == 200
        assert db.client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {other}"}).status_code == 401


class TestSignOut:
    def test_signing_out_ends_the_session_and_deletes_the_cookie(self, db):
        token = session_of(cookie_sign_in(db))

        response = db.client.post(f"{API}/auth/logout", headers={**cookie(token), **SAME})

        assert response.status_code == 200
        [header] = set_cookies(response)
        parts = [part.strip().lower() for part in header.split(";")]
        assert parts[0] in (f"{SESSION_COOKIE.lower()}=", f'{SESSION_COOKIE.lower()}=""')
        assert {"max-age=0", "path=/", "secure", "httponly", "samesite=strict"} <= set(parts)
        assert sessions_of(db, db.alice_id) == 0
        assert db.client.get(f"{API}/auth/me", headers=cookie(token)).status_code == 401

    def test_signing_out_with_a_bearer_token_leaves_the_cookie(self, db):
        """A program's sign-out ends its own session; a browser's cookie is not its."""
        token = session_token(db.engine, "alice")

        response = db.client.post(f"{API}/auth/logout", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 200
        assert set_cookies(response) == []


def _ws_auth(client, headers: dict, message: dict):
    """The first answer of the events socket to ``message`` with the handshake's ``headers``: the
    message, or the close code."""
    try:
        with client.websocket_connect("/ws/v1/events", headers=headers) as ws:
            ws.send_json(message)
            return ws.receive_json()
    except WebSocketDisconnect as exc:
        return exc.code


class TestWebSocket:
    def test_an_auth_message_without_a_token_takes_the_cookie_of_the_handshake(self, db):
        token = session_of(cookie_sign_in(db))

        answer = _ws_auth(db.client, {**bare_cookie(token), "Origin": OWN_ORIGIN},
                          {"type": "auth", "key": key_of(token)})

        assert answer == {"type": "auth_ok"}

    @pytest.mark.parametrize("message", [{"type": "auth"}, {"type": "auth", "key": ""}, {"type": "auth", "key": 1}])
    def test_the_cookie_of_a_socket_without_its_session_key_is_refused(self, db, message):
        token = session_of(cookie_sign_in(db))

        assert _ws_auth(db.client, {**bare_cookie(token), "Origin": OWN_ORIGIN}, message) == 4401

    def test_the_session_key_of_another_session_is_refused(self, db):
        token = session_of(cookie_sign_in(db))
        other = session_token(db.engine, "alice")

        assert _ws_auth(
            db.client, {**bare_cookie(token), "Origin": OWN_ORIGIN}, {"type": "auth", "key": key_of(other)}
        ) == 4401

    @pytest.mark.parametrize("headers", [
        {},  # a browser always sends the Origin of a socket's page
        {"Origin": "https://evil.example"},
        {"Origin": "https://testserver:8443"},
        {"Origin": "http://testserver"},
        {"Origin": OWN_ORIGIN, "Sec-Fetch-Site": "same-site"},
    ])
    def test_the_cookie_of_a_socket_from_another_origin_is_refused(self, db, headers):
        """No CORS protects a WebSocket: its Origin alone tells the web UI's page from another."""
        token = session_of(cookie_sign_in(db))

        assert _ws_auth(db.client, {**bare_cookie(token), **headers}, {"type": "auth", "key": key_of(token)}) == 4401

    def test_an_auth_message_without_a_token_or_a_cookie_is_refused(self, db):
        assert _ws_auth(db.client, {"Origin": OWN_ORIGIN}, {"type": "auth", "key": "anything"}) == 4401

    def test_an_ended_session_in_the_cookie_is_refused(self, db):
        token = session_of(cookie_sign_in(db))
        db.client.post(f"{API}/auth/logout", headers={**cookie(token), **SAME})

        assert _ws_auth(
            db.client, {**bare_cookie(token), "Origin": OWN_ORIGIN}, {"type": "auth", "key": key_of(token)}
        ) == 4401

    def test_a_socket_of_the_cookie_closes_when_its_session_ends(self, db, monkeypatch):
        """The check every minute (here 0.1 s) takes the cookie's token as the handshake did (ADR-0031)."""
        monkeypatch.setattr(events, "WS_REAUTH_INTERVAL_SECONDS", 0.1)
        monkeypatch.setattr(events, "STATS_INTERVAL_SECONDS", 0.05)
        monkeypatch.setattr(events, "get_system_stats", lambda: {"type": "stats", "data": {}})
        token = session_of(cookie_sign_in(db))

        with db.client.websocket_connect("/ws/v1/events", headers={**bare_cookie(token), "Origin": OWN_ORIGIN}) as ws:
            ws.send_json({"type": "auth", "key": key_of(token)})
            assert ws.receive_json() == {"type": "auth_ok"}
            assert ws.receive_json()["type"] == "stats"
            with Session(db.engine) as session:
                session.query(UserSession).delete()
                session.commit()
            closed, _ = _read_until_closed(ws)

        assert closed.code == 4401

    def test_the_token_of_the_cookie_in_the_message_is_refused(self, db):
        token = session_of(cookie_sign_in(db))

        assert _ws_auth(db.client, {}, {"type": "auth", "token": token}) == 4401
        assert _ws_auth(db.client, {"Origin": OWN_ORIGIN}, {"type": "auth", "token": token, "key": key_of(token)}) == 4401

    def test_a_token_in_the_message_needs_no_origin_and_wins_over_the_cookie(self, db):
        """Programs authenticate a socket with a token in the first message, as before (ADR-0031)."""
        alice = session_of(cookie_sign_in(db))
        owner = session_token(db.engine, "owner")

        with db.client.websocket_connect("/ws/v1/journal", headers=bare_cookie(alice)) as ws:
            ws.send_json({"type": "auth", "token": owner})
            assert ws.receive_json() == {"type": "auth_ok"}  # the journal is administrators' only

    def test_an_api_token_in_the_cookie_is_refused(self, db):
        full, token_hash = generate_api_token()
        with Session(db.engine) as session:
            session.add(APIToken(name="t", token_hash=token_hash, token_prefix=full[:12], user_id=db.alice_id))
            session.commit()

        assert _ws_auth(db.client, {**bare_cookie(full), "Origin": OWN_ORIGIN}, {"type": "auth", "key": "x"}) == 4401


class TestNoTokenInWebStorage:
    """The web UI keeps no credential where its scripts read it, and no spec reads one there."""

    ROOT = Path(__file__).resolve().parents[2] / "webui"

    def test_the_web_ui_uses_web_storage_only_for_the_language(self):
        users = {}
        for path in sorted((self.ROOT / "src").rglob("*")):
            if path.suffix in (".ts", ".svelte") and re.search(r"\b(local|session)Storage\b", path.read_text()):
                users[path.relative_to(self.ROOT).as_posix()] = sorted(
                    set(re.findall(r"\b(?:local|session)Storage\.\w+\(\s*([^,)]*)", path.read_text()))
                )
        # The session key in the tab's sessionStorage; the token that earlier versions kept is removed.
        assert users == {
            "src/lib/api/client.ts": ["'token'", "SESSION_KEY_STORE"],
            "src/lib/i18n/index.ts": ["LOCALE_KEY"],
        }
        client = (self.ROOT / "src/lib/api/client.ts").read_text()
        assert re.findall(r"localStorage\.\w+\(\s*'token'", client) == ["localStorage.removeItem('token'"]

    def test_no_spec_reads_a_token_from_web_storage(self):
        found = []
        for path in sorted((self.ROOT / "tests").glob("*.ts")):
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if re.search(r"""(local|session)Storage\.getItem\(\s*['"`][^'"`]*token""", line, re.IGNORECASE):
                    found.append(f"{path.name}:{number}")
        assert found == []
