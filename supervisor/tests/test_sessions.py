"""Sessions on the server (ADR-0329): a sign-in stores its session, and a session token is valid only
while that row exists. Signing out ends the session, a password change the user's other sessions,
and an administrator's End sessions, a new password set by an administrator and the deletion of
the user all of them."""
from __future__ import annotations

from datetime import datetime, timedelta, UTC
from types import SimpleNamespace

import bcrypt
import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from src.auth import router as auth_router
from src.auth.models import APIToken, User, UserSession
from src.auth.utils import generate_api_token
from src.config import get_settings
from src.database import Base, get_db
from src.main import app
from src.main import limiter as app_limiter

from .audit_helpers import audit_entries
from .session_tokens import session_headers, session_token

API = "/api/v1"
PASSWORD = "Alice-pass-2026"
NEW_PASSWORD = "Alice-new-pass-2026"


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A temporary database with the administrator owner and the user alice, the API on it, cheap bcrypt."""
    path = tmp_path / "sessions.db"
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
    monkeypatch.setattr(auth_router, "hash_password",
                        lambda pw: bcrypt.hashpw(pw.encode(), bcrypt.gensalt(rounds=4)).decode())
    audit_file = tmp_path / "audit.jsonl"
    monkeypatch.setattr("src.audit.router.AUDIT_FILE", str(audit_file))
    auth_router.limiter.reset()
    app_limiter.reset()
    try:
        yield SimpleNamespace(engine=engine, audit_file=audit_file, **vars(ids),
                              client=TestClient(app, raise_server_exceptions=False))
    finally:
        app.dependency_overrides.pop(get_db, None)
        auth_router.limiter.reset()
        app_limiter.reset()
        engine.dispose()


def sign_in(db, username: str = "alice", password: str = PASSWORD) -> dict[str, str]:
    response = db.client.post(f"{API}/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def me(db, headers) -> tuple[int, str | None]:
    response = db.client.get(f"{API}/auth/me", headers=headers)
    return response.status_code, response.json().get("detail")


def sessions_of(db, user_id: int) -> int:
    with Session(db.engine) as session:
        return session.scalar(select(func.count()).select_from(UserSession).where(UserSession.user_id == user_id))


class TestSignIn:
    def test_a_sign_in_stores_its_session(self, db):
        headers = sign_in(db)

        assert sessions_of(db, db.alice_id) == 1
        assert me(db, headers)[0] == 200

    def test_a_token_without_its_session_row_is_refused(self, db):
        """A token signed with the right secret but whose session is not stored: a token of an
        ended session, or one made before this check."""
        settings = get_settings()
        expires = datetime.now(UTC) + timedelta(minutes=5)
        no_row = jwt.encode({"sub": "alice", "jti": "never-stored", "exp": expires}, settings.jwt_secret,
                            algorithm=settings.jwt_algorithm)
        no_id = jwt.encode({"sub": "alice", "exp": expires}, settings.jwt_secret, algorithm=settings.jwt_algorithm)

        assert me(db, {"Authorization": f"Bearer {no_row}"}) == (401, "Session ended")
        assert me(db, {"Authorization": f"Bearer {no_id}"}) == (401, "Invalid token")

    def test_the_session_of_another_user_is_refused(self, db):
        owner_token = session_token(db.engine, "owner")
        owner_session = jwt.decode(owner_token, options={"verify_signature": False})["jti"]
        settings = get_settings()
        forged = jwt.encode({"sub": "alice", "jti": owner_session, "exp": datetime.now(UTC) + timedelta(minutes=5)},
                            settings.jwt_secret, algorithm=settings.jwt_algorithm)

        assert me(db, {"Authorization": f"Bearer {forged}"}) == (401, "Session ended")

    def test_a_sign_in_removes_the_rows_of_expired_sessions(self, db):
        with Session(db.engine) as session:
            session.add(UserSession(id="expired", user_id=db.alice_id, expires_at=datetime(2020, 1, 1)))
            session.commit()

        sign_in(db)

        with Session(db.engine) as session:
            assert session.get(UserSession, "expired") is None
        assert sessions_of(db, db.alice_id) == 1


class TestSignOut:
    def test_signing_out_ends_that_session_only(self, db):
        phone, laptop = sign_in(db), sign_in(db)

        response = db.client.post(f"{API}/auth/logout", headers=phone)

        assert response.status_code == 200, response.text
        assert me(db, phone) == (401, "Session ended")
        assert me(db, laptop)[0] == 200

    def test_an_api_token_is_no_session_to_end(self, db):
        full_token, token_hash = generate_api_token()
        with Session(db.engine) as session:
            session.add(APIToken(user_id=db.alice_id, name="script", token_hash=token_hash, token_prefix=full_token[:12]))
            session.commit()
        headers = {"Authorization": f"Bearer {full_token}"}

        response = db.client.post(f"{API}/auth/logout", headers=headers)

        assert response.status_code == 400
        assert me(db, headers)[0] == 200


class TestPasswords:
    def test_a_password_change_ends_the_other_sessions(self, db):
        current, other = sign_in(db), sign_in(db)

        response = db.client.post(f"{API}/auth/change-password", headers=current,
                                  json={"current_password": PASSWORD, "new_password": NEW_PASSWORD})

        assert response.status_code == 200, response.text
        assert me(db, current)[0] == 200
        assert me(db, other) == (401, "Session ended")
        assert sessions_of(db, db.alice_id) == 1

    def test_a_refused_password_change_ends_nothing(self, db):
        current, other = sign_in(db), sign_in(db)

        response = db.client.post(f"{API}/auth/change-password", headers=current,
                                  json={"current_password": "wrong-password", "new_password": NEW_PASSWORD})

        assert response.status_code == 400
        assert me(db, other)[0] == 200

    def test_a_password_set_by_an_administrator_ends_every_session(self, db):
        alice = sign_in(db)

        response = db.client.put(f"{API}/auth/users/{db.alice_id}", headers=session_headers(db.engine, "owner"),
                                 json={"password": NEW_PASSWORD, "username": "alice"})

        assert response.status_code == 200, response.text
        assert me(db, alice) == (401, "Session ended")
        assert sessions_of(db, db.alice_id) == 0


    def test_an_administrator_who_sets_their_own_password_keeps_this_session(self, db):
        current, other = session_headers(db.engine, "owner"), session_headers(db.engine, "owner")

        response = db.client.put(f"{API}/auth/users/{db.owner_id}", headers=current,
                                 json={"password": NEW_PASSWORD, "username": "owner"})

        assert response.status_code == 200, response.text
        assert me(db, current)[0] == 200
        assert me(db, other) == (401, "Session ended")


class TestAdministrators:
    def test_end_sessions_ends_every_session_of_the_user(self, db):
        first, second = sign_in(db), sign_in(db)
        owner = session_headers(db.engine, "owner")

        response = db.client.post(f"{API}/auth/users/{db.alice_id}/end-sessions", params={"username": "alice"},
                                  headers=owner)

        assert response.status_code == 200, response.text
        assert response.json() == {"status": "ok", "ended": 2}
        assert me(db, first) == (401, "Session ended")
        assert me(db, second) == (401, "Session ended")
        assert me(db, owner)[0] == 200
        entry = audit_entries(db.audit_file)[-1]
        assert (entry["action"], entry["username"], entry["resource_id"]) == ("user_updated", "owner", "alice")
        assert entry["details"] == "Ended the sessions of alice: 2"

    def test_end_sessions_of_an_unknown_user_is_404(self, db):
        response = db.client.post(f"{API}/auth/users/999/end-sessions", params={"username": "bob"},
                                  headers=session_headers(db.engine, "owner"))

        assert response.status_code == 404

    def test_end_sessions_for_a_name_the_id_no_longer_has_is_409(self, db):
        """ids are reused after a deletion: the request names the user it was built for."""
        sign_in(db)

        response = db.client.post(f"{API}/auth/users/{db.alice_id}/end-sessions", params={"username": "bob"},
                                  headers=session_headers(db.engine, "owner"))

        assert response.status_code == 409
        assert sessions_of(db, db.alice_id) == 1

    def test_a_regular_user_cannot_end_sessions(self, db):
        alice = sign_in(db)

        response = db.client.post(f"{API}/auth/users/{db.owner_id}/end-sessions", params={"username": "owner"},
                                  headers=alice)

        assert response.status_code == 403

    def test_deleting_a_user_removes_their_sessions(self, db):
        sign_in(db)

        response = db.client.delete(f"{API}/auth/users/{db.alice_id}", params={"username": "alice"},
                                    headers=session_headers(db.engine, "owner"))

        assert response.status_code == 200, response.text
        assert sessions_of(db, db.alice_id) == 0
