"""A user's theme of the web UI: GET and PUT /auth/me/theme (webui.light-theme, ADR-0402).

Each user, administrator or not, chooses Dark, Light or System on the Account page, and the server
keeps the choice in a new table of its own (``user_themes``, ADR-0319), so that it stays across
sign-ins and follows the user to another browser. No row is System. A stored value that is none of
the three reads as System (a restored database is untrusted, ADR-0144). A write happens only for a
real change (ADR-0214), and every real change is one entry of the audit log (ADR-0320).
"""
from __future__ import annotations

from types import SimpleNamespace

import bcrypt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from src.auth import scopes
from src.auth.models import User, UserTheme
from src.auth.router import limiter as auth_limiter
from src.database import Base, get_db
from src.main import app
from .audit_helpers import audit_entries
from .session_tokens import session_token

API = "/api/v1"
URL = f"{API}/auth/me/theme"
PASSWORD_HASH = bcrypt.hashpw(b"correct-password", bcrypt.gensalt(rounds=4)).decode()


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A temporary database with an administrator and two regular users, and the audit log's file."""
    path = tmp_path / "theme.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([User(username=name, hashed_password=PASSWORD_HASH, is_admin=name == "admin")
                         for name in ("admin", "alice", "bob")])
        session.commit()
        ids = {user.username: user.id for user in session.scalars(select(User))}
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{path}", poolclass=NullPool)
    maker = async_sessionmaker(async_engine, expire_on_commit=False)
    statements: list[str] = []

    @event.listens_for(async_engine.sync_engine, "before_cursor_execute")
    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    async def override_get_db():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr("src.audit.router.AUDIT_FILE", str(tmp_path / "audit.jsonl"))
    auth_limiter.reset()
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield SimpleNamespace(
            engine=engine, client=client, ids=ids, statements=statements,
            audit=lambda: [e for e in audit_entries(tmp_path / "audit.jsonl") if e["action"] == "theme_changed"],
            headers=lambda name: {"Authorization": f"Bearer {session_token(engine, name)}"},
            stored=lambda name: stored_rows(engine, ids[name]),
        )
    finally:
        app.dependency_overrides.pop(get_db, None)
        auth_limiter.reset()
        engine.dispose()


def stored_rows(engine, user_id: int) -> list[str]:
    with Session(engine) as session:
        return list(session.scalars(select(UserTheme.theme).where(UserTheme.user_id == user_id)))


def writes(world, start: int = 0) -> list[str]:
    """The statements since ``start`` that change the table of themes."""
    return [s for s in world.statements[start:]
            if "user_themes" in s and s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]


def theme(value: str) -> dict:
    return {"theme": value}


class TestTheChoices:
    def test_a_user_who_never_chose_has_system(self, world):
        response = world.client.get(URL, headers=world.headers("alice"))

        assert (response.status_code, response.json()) == (200, theme("system"))
        assert world.stored("alice") == []

    @pytest.mark.parametrize("value", ["dark", "light", "system"])
    def test_each_of_dark_light_and_system_is_kept(self, world, value):
        headers = world.headers("alice")
        if value == "system":  # a real change to system: from another choice
            world.client.put(URL, json=theme("light"), headers=headers)

        saved = world.client.put(URL, json=theme(value), headers=headers)

        assert (saved.status_code, saved.json()) == (200, theme(value))
        assert world.client.get(URL, headers=headers).json() == theme(value)

    @pytest.mark.parametrize("body", [
        {}, {"theme": ""}, {"theme": "Light"}, {"theme": "LIGHT"}, {"theme": "blue"}, {"theme": "auto"},
        {"theme": None}, {"theme": 1}, {"theme": ["light"]}, {"theme": "light", "accent": "red"},
        {"theme": " light"}, [], "light", None,
    ])
    def test_anything_else_answers_422_and_stores_nothing(self, world, body):
        headers = world.headers("alice")
        world.client.put(URL, json=theme("light"), headers=headers)
        before = len(world.statements)

        response = world.client.put(URL, json=body, headers=headers)

        assert response.status_code == 422, response.text
        assert world.client.get(URL, headers=headers).json() == theme("light")
        assert writes(world, before) == []
        assert len(world.audit()) == 1  # the first choice only

    def test_a_body_that_is_no_json_answers_422(self, world):
        response = world.client.put(URL, content=b"{not json", headers={
            **world.headers("alice"), "Content-Type": "application/json"})

        assert response.status_code == 422
        assert world.stored("alice") == []


class TestWhoMayChoose:
    @pytest.mark.parametrize("name", ["admin", "alice"])
    def test_every_signed_in_user_keeps_a_choice_of_their_own(self, world, name):
        headers = world.headers(name)

        assert world.client.put(URL, json=theme("light"), headers=headers).status_code == 200
        assert world.client.get(URL, headers=headers).json() == theme("light")

    def test_a_choice_belongs_to_its_user_alone(self, world):
        world.client.put(URL, json=theme("light"), headers=world.headers("alice"))
        world.client.put(URL, json=theme("dark"), headers=world.headers("bob"))

        assert world.client.get(URL, headers=world.headers("admin")).json() == theme("system")
        assert world.client.get(URL, headers=world.headers("alice")).json() == theme("light")
        assert world.client.get(URL, headers=world.headers("bob")).json() == theme("dark")

    def test_it_stays_across_sign_ins(self, world):
        world.client.put(URL, json=theme("light"), headers=world.headers("alice"))

        # Every call of headers() is a new session, as a new sign-in makes it.
        assert world.client.get(URL, headers=world.headers("alice")).json() == theme("light")

    @pytest.mark.parametrize("method", ["GET", "PUT"])
    def test_nobody_signed_out_reaches_it(self, world, method):
        assert world.client.request(method, URL, json=theme("light")).status_code in (401, 403)
        assert world.stored("alice") == []

    def test_a_full_api_token_acts_as_its_user(self, world):
        headers = world.headers("alice")
        token = world.client.post(f"{API}/auth/tokens", json={"name": "theme"}, headers=headers).json()["token"]

        put = world.client.put(URL, json=theme("light"), headers={"Authorization": f"Bearer {token}"})

        assert put.status_code == 200
        assert world.client.get(URL, headers=headers).json() == theme("light")

    def test_a_limited_token_reads_it_and_never_changes_it(self, world):
        headers = world.headers("alice")
        world.client.put(URL, json=theme("light"), headers=headers)
        token = world.client.post(f"{API}/auth/tokens", json={"name": "ro", "read_only": True}, headers=headers).json()
        limited = {"Authorization": f"Bearer {token['token']}"}

        assert world.client.get(URL, headers=limited).json() == theme("light")
        refused = world.client.put(URL, json=theme("dark"), headers=limited)
        assert (refused.status_code, refused.json()["detail"]) == (403, scopes.LIMITED_TOKEN_CANNOT)
        assert world.client.get(URL, headers=headers).json() == theme("light")


class TestWritesOnlyForARealChange:
    def test_choosing_what_is_stored_writes_nothing_and_is_not_audited(self, world):
        headers = world.headers("alice")
        world.client.put(URL, json=theme("light"), headers=headers)
        before = len(world.statements)

        again = world.client.put(URL, json=theme("light"), headers=headers)

        assert (again.status_code, again.json()) == (200, theme("light"))
        assert writes(world, before) == []
        assert len(world.audit()) == 1

    def test_choosing_system_without_a_choice_writes_nothing(self, world):
        before = len(world.statements)

        response = world.client.put(URL, json=theme("system"), headers=world.headers("alice"))

        assert (response.status_code, response.json()) == (200, theme("system"))
        assert writes(world, before) == []
        assert world.stored("alice") == []
        assert world.audit() == []

    def test_reading_writes_nothing(self, world):
        headers = world.headers("alice")
        world.client.put(URL, json=theme("dark"), headers=headers)
        before = len(world.statements)

        for _ in range(3):
            world.client.get(URL, headers=headers)

        assert writes(world, before) == []

    def test_a_change_replaces_the_row_and_keeps_one_per_user(self, world):
        headers = world.headers("alice")
        for value in ("light", "dark", "system"):
            world.client.put(URL, json=theme(value), headers=headers)

        assert world.stored("alice") == ["system"]

    def test_each_real_change_is_one_audit_entry_of_the_user_about_their_own_theme(self, world):
        headers = world.headers("alice")
        world.client.put(URL, json=theme("light"), headers=headers)
        world.client.put(URL, json=theme("light"), headers=headers)
        world.client.put(URL, json=theme("dark"), headers=headers)

        entries = world.audit()

        assert [(e["username"], e["resource_type"], e["resource_id"], e["severity"], e["success"])
                for e in entries] == [("alice", "theme", "alice", "info", True)] * 2
        assert [e["details"] for e in entries] == [
            "Changed the theme from system to light", "Changed the theme from light to dark"]


class TestAStoredChoiceIsUntrusted:
    """A restored or damaged database may hold anything in the column (ADR-0144): the answer is
    System, never a 500 and never the text as it is."""

    @pytest.mark.parametrize("stored", ["", "Light", "blue", "light ", "<script>", "x" * 1000])
    def test_an_invalid_row_reads_as_system(self, world, stored):
        with Session(world.engine) as session:
            session.add(UserTheme(user_id=world.ids["alice"], theme=stored))
            session.commit()

        response = world.client.get(URL, headers=world.headers("alice"))

        assert (response.status_code, response.json()) == (200, theme("system"))

    def test_a_row_of_a_non_text_value_reads_as_system(self, world):
        with Session(world.engine) as session:
            session.execute(text("INSERT INTO user_themes (user_id, theme) VALUES (:u, X'ff00fe')"),
                            {"u": world.ids["alice"]})
            session.commit()

        response = world.client.get(URL, headers=world.headers("alice"))

        assert (response.status_code, response.json()) == (200, theme("system"))

    def test_a_choice_over_an_invalid_row_replaces_it(self, world):
        with Session(world.engine) as session:
            session.add(UserTheme(user_id=world.ids["alice"], theme="blue"))
            session.commit()

        world.client.put(URL, json=theme("light"), headers=world.headers("alice"))

        assert world.stored("alice") == ["light"]
        assert [e["details"] for e in world.audit()] == ["Changed the theme from system to light"]


class TestWhenTheUserGoes:
    def test_deleting_a_user_deletes_their_choice_and_only_theirs(self, world):
        world.client.put(URL, json=theme("light"), headers=world.headers("alice"))
        world.client.put(URL, json=theme("dark"), headers=world.headers("bob"))

        gone = world.client.delete(f"{API}/auth/users/{world.ids['alice']}", params={"username": "alice"},
                                   headers=world.headers("admin"))

        assert gone.status_code == 200, gone.text
        assert world.stored("alice") == []
        assert world.stored("bob") == ["dark"]

    def test_a_new_user_with_a_reused_id_starts_with_system(self, world):
        world.client.put(URL, json=theme("light"), headers=world.headers("bob"))  # bob has the highest id
        world.client.delete(f"{API}/auth/users/{world.ids['bob']}", params={"username": "bob"},
                            headers=world.headers("admin"))
        with Session(world.engine) as session:
            session.add(User(id=world.ids["bob"], username="carol", hashed_password=PASSWORD_HASH))
            session.commit()

        assert world.client.get(URL, headers=world.headers("carol")).json() == theme("system")

    def test_a_rename_keeps_the_choice(self, world):
        world.client.put(URL, json=theme("light"), headers=world.headers("alice"))

        renamed = world.client.put(f"{API}/auth/users/{world.ids['alice']}",
                                   json={"username": "alice", "new_username": "alice2"},
                                   headers=world.headers("admin"))

        assert renamed.status_code == 200, renamed.text
        assert world.client.get(URL, headers=world.headers("alice2")).json() == theme("light")


class TestTheTable:
    def test_it_is_a_new_table_and_the_users_table_is_not_altered(self):
        users = {column.name for column in User.__table__.columns}

        assert "theme" not in users
        assert UserTheme.__tablename__ == "user_themes"
        assert [column.name for column in UserTheme.__table__.primary_key] == ["user_id"]
