"""Names of new UNOS users (ADR-0115): POST /auth/setup and POST /auth/users accept
only names that SMB accounts can use, unique without regard to case. Users created
before keep their names and keep logging in."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import bcrypt
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from src.auth import router as auth_router
from src.auth.models import User
from src.auth.utils import create_access_token
from src.database import Base, get_db
from src.main import app
from src.main import limiter as app_limiter
from src.security.validators import validate_new_username
from src.shares import smb, smbconf

API = "/api/v1"
ADMIN_PW = "Admin-pass-2026"
RULE = "Use 1 to 32 characters: lowercase letters, digits, '_' and '-', starting with a letter"
SID_RULE = "Names that start with 's-' and a digit are reserved: Samba reads them as security identifiers (SIDs)"

REFUSED = [
    pytest.param("", f"Invalid username. {RULE}", id="empty"),
    pytest.param("root", "Invalid username. 'root' is reserved for the system", id="root"),
    pytest.param("-o", f"Invalid username. {RULE}", id="option"),
    pytest.param("Bob", f"Invalid username. {RULE}", id="uppercase"),
    pytest.param("bob.smith", f"Invalid username. {RULE}", id="dot"),
    pytest.param("x\ny", f"Invalid username. {RULE}", id="newline"),
    pytest.param("machine$", f"Invalid username. {RULE}", id="machine-account"),
    pytest.param("a" * 33, f"Invalid username. {RULE}", id="33-characters"),
    pytest.param("systemd-x", "Invalid username. 'systemd-x' is reserved for the system", id="systemd"),
    pytest.param("unos-smb", "Invalid username. 'unos-smb' is reserved for the system", id="unos-smb"),
    # Samba reads these as SIDs in valid users and write list (s-1-1-0 is Everyone).
    *(
        pytest.param(name, f"Invalid username. {SID_RULE}", id=name)
        for name in ("s-1-1-0", "s-1-0x1-0", "s-1-22-2-70000", "s-1-5-32-545")
    ),
]
ACCEPTED = ["alice", "e2e_user_abc", "admin", "a" * 32, "b-2", "s1", "sam", "s-a"]


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A temporary database (empty), the API on it, cheap bcrypt."""
    path = tmp_path / "users.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    maker = async_sessionmaker(create_async_engine(f"sqlite+aiosqlite:///{path}", poolclass=NullPool),
                               expire_on_commit=False)

    async def override_get_db():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(auth_router, "_setup_lock", asyncio.Lock())
    monkeypatch.setattr(auth_router, "hash_password",
                        lambda pw: bcrypt.hashpw(pw.encode(), bcrypt.gensalt(rounds=4)).decode())
    monkeypatch.setattr("src.audit.router.AUDIT_FILE", str(tmp_path / "audit_log.json"))
    auth_router.limiter.reset()
    app_limiter.reset()
    try:
        yield SimpleNamespace(engine=engine, client=TestClient(app, raise_server_exceptions=False))
    finally:
        app.dependency_overrides.pop(get_db, None)
        auth_router.limiter.reset()
        app_limiter.reset()
        engine.dispose()


def add_user(engine, name: str, password: str, *, admin: bool = False) -> int:
    with Session(engine) as session:
        user = User(username=name, hashed_password=bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=4)).decode(),
                    is_admin=admin)
        session.add(user)
        session.commit()
        return user.id


def usernames(engine) -> list[str]:
    with Session(engine) as session:
        return sorted(session.scalars(select(User.username)))


def admin_headers(db) -> dict[str, str]:
    add_user(db.engine, "owner", ADMIN_PW, admin=True)
    return {"Authorization": f"Bearer {create_access_token({'sub': 'owner'})}"}


class TestValidator:
    @pytest.mark.parametrize("name, detail", REFUSED)
    def test_refused(self, name, detail):
        with pytest.raises(HTTPException) as raised:
            validate_new_username(name)
        assert (raised.value.status_code, raised.value.detail) == (400, detail)

    @pytest.mark.parametrize("name", ACCEPTED)
    def test_accepted(self, name):
        assert validate_new_username(name) == name


class TestSetup:
    @pytest.mark.parametrize("name, detail", REFUSED)
    def test_refused(self, db, name, detail):
        response = db.client.post(f"{API}/auth/setup", json={"username": name, "password": ADMIN_PW})

        assert response.status_code == 400
        assert response.json()["detail"] == detail
        assert usernames(db.engine) == []

    @pytest.mark.parametrize("name", ["alice", "e2e_user_abc", "admin"])
    def test_accepted(self, db, name):
        response = db.client.post(f"{API}/auth/setup", json={"username": name, "password": ADMIN_PW})

        assert response.status_code == 200, response.text
        assert response.json()["is_admin"] is True
        assert usernames(db.engine) == [name]

    def test_an_administrator_cannot_have_smb(self, db):
        response = db.client.post(f"{API}/auth/setup",
                                  json={"username": "admin", "password": ADMIN_PW, "smb_enabled": True})

        assert response.status_code == 400
        assert response.json()["detail"] == "Administrators cannot use SMB"
        assert usernames(db.engine) == []

    def test_a_name_longer_than_64_characters_is_a_validation_error(self, db):
        response = db.client.post(f"{API}/auth/setup", json={"username": "a" * 65, "password": ADMIN_PW})
        assert response.status_code == 422


class TestCreate:
    @pytest.mark.parametrize("name, detail", REFUSED)
    def test_refused(self, db, name, detail):
        headers = admin_headers(db)

        response = db.client.post(f"{API}/auth/users", json={"username": name, "password": "Some-pass-2026"},
                                  headers=headers)

        assert response.status_code == 400
        assert response.json()["detail"] == detail
        assert usernames(db.engine) == ["owner"]

    @pytest.mark.parametrize("name", ["alice", "e2e_user_abc", "admin"])
    def test_accepted(self, db, name):
        headers = admin_headers(db)

        response = db.client.post(f"{API}/auth/users", json={"username": name, "password": "Some-pass-2026"},
                                  headers=headers)

        assert response.status_code == 200, response.text
        assert response.json()["username"] == name
        assert response.json()["smb"]["state"] == "off"

    @pytest.mark.parametrize("existing", ["Bob", "BOB", "bob"])
    def test_a_case_insensitive_duplicate(self, db, existing):
        headers = admin_headers(db)
        add_user(db.engine, existing, "Legacy-pass-2026")

        response = db.client.post(f"{API}/auth/users", json={"username": "bob", "password": "Some-pass-2026"},
                                  headers=headers)

        assert response.status_code == 400
        assert response.json()["detail"] == "Username already exists"
        assert usernames(db.engine) == sorted(["owner", existing])


class TestLegacyUsers:
    @pytest.mark.parametrize("name", ["Bob.Smith", "TestUser", "root", "x y"])
    def test_legacy_names_still_log_in(self, db, name):
        add_user(db.engine, name, "Legacy-pass-2026")

        response = db.client.post(f"{API}/auth/login", json={"username": name, "password": "Legacy-pass-2026"})

        assert response.status_code == 200, response.text
        token = response.json()["access_token"]
        me = db.client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.json()["username"] == name

    def test_smb_is_unavailable_for_them(self, db):
        add_user(db.engine, "Bob.Smith", "Legacy-pass-2026")
        token = create_access_token({"sub": "Bob.Smith"})

        status = db.client.get(f"{API}/auth/me/smb", headers={"Authorization": f"Bearer {token}"}).json()

        assert status["account"]["state"] == "unavailable"
        assert status["account"]["reason"] == f"The username 'Bob.Smith' cannot be used for SMB. {RULE}."

    def test_a_sid_like_name_is_unavailable_and_never_rendered(self, db):
        """A user created before the SID rule (or restored) keeps the name, but gets no SMB:
        smb.conf never names it in valid users or write list."""
        add_user(db.engine, "s-1-1-0", "Legacy-pass-2026")
        token = create_access_token({"sub": "s-1-1-0"})

        status = db.client.get(f"{API}/auth/me/smb", headers={"Authorization": f"Bearer {token}"}).json()

        assert status["account"]["state"] == "unavailable"
        assert status["account"]["reason"] == f"The username 's-1-1-0' cannot be used for SMB. {SID_RULE}."
        with pytest.raises(smb.SambaConfigError):
            smbconf._safe_user("s-1-1-0")

    def test_they_can_change_their_password(self, db):
        add_user(db.engine, "Bob.Smith", "Legacy-pass-2026")
        token = create_access_token({"sub": "Bob.Smith"})

        response = db.client.post(f"{API}/auth/change-password",
                                  json={"current_password": "Legacy-pass-2026", "new_password": "New-pass-2026"},
                                  headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 200, response.text
