"""The layout of a user's dashboard: GET, PUT and DELETE /auth/me/dashboard-layout
(webui.dashboard-layout, ADR-0395).

Each user, administrator or not, keeps the order of the dashboard's widgets and which of them are
hidden, in a new table of their own (``dashboard_layouts``, ADR-0319), so that the layout follows
them across browsers. The server knows no widget catalog: it keeps a list of ``{id, hidden}`` that
the web UI reads, and ignores nothing but what is not such a list (a restored database is untrusted,
ADR-0144). A write happens only for a real change (ADR-0214), and every real change, and a reset
that found a layout, is one entry of the audit log (ADR-0320).
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import bcrypt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from src.auth import scopes
from src.auth.models import DashboardLayout, User
from src.auth.router import limiter as auth_limiter
from src.database import Base, get_db
from src.main import app
from .audit_helpers import audit_entries
from .session_tokens import session_token

API = "/api/v1"
URL = f"{API}/auth/me/dashboard-layout"
PASSWORD_HASH = bcrypt.hashpw(b"correct-password", bcrypt.gensalt(rounds=4)).decode()

DEFAULT_WIDGETS = ["cpu", "memory", "network", "disk-io", "temperature", "storage", "containers"]


def layout(*items: tuple[str, bool]) -> dict:
    return {"widgets": [{"id": name, "hidden": hidden} for name, hidden in items]}


ARRANGED = layout(("disk-io", False), ("cpu", False), ("memory", True), ("storage", False))


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A temporary database with an administrator and two regular users, and the audit log's file."""
    path = tmp_path / "layout.db"
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
            audit=lambda: [e for e in audit_entries(tmp_path / "audit.jsonl") if e["action"] == "dashboard_layout_changed"],
            headers=lambda name: {"Authorization": f"Bearer {session_token(engine, name)}"},
            stored=lambda name: stored_rows(engine, ids[name]),
        )
    finally:
        app.dependency_overrides.pop(get_db, None)
        auth_limiter.reset()
        engine.dispose()


def stored_rows(engine, user_id: int) -> list[str]:
    with Session(engine) as session:
        return list(session.scalars(select(DashboardLayout.layout).where(DashboardLayout.user_id == user_id)))


def writes(world, start: int = 0) -> list[str]:
    """The statements since ``start`` that change the table of layouts."""
    return [s for s in world.statements[start:]
            if "dashboard_layouts" in s and s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]


class TestWhoMayArrange:
    @pytest.mark.parametrize("name", ["admin", "alice"])
    def test_every_signed_in_user_keeps_a_layout_of_their_own(self, world, name):
        headers = world.headers(name)

        assert world.client.get(URL, headers=headers).json() == {"widgets": []}
        saved = world.client.put(URL, json=ARRANGED, headers=headers)

        assert (saved.status_code, saved.json()) == (200, ARRANGED)
        assert world.client.get(URL, headers=headers).json() == ARRANGED

    def test_a_layout_belongs_to_its_user_alone(self, world):
        world.client.put(URL, json=ARRANGED, headers=world.headers("alice"))

        for name in ("bob", "admin"):
            assert world.client.get(URL, headers=world.headers(name)).json() == {"widgets": []}
        other = layout(("temperature", False))
        world.client.put(URL, json=other, headers=world.headers("bob"))
        assert world.client.get(URL, headers=world.headers("alice")).json() == ARRANGED
        assert world.client.get(URL, headers=world.headers("bob")).json() == other

    def test_it_follows_the_user_to_another_session(self, world):
        world.client.put(URL, json=ARRANGED, headers=world.headers("alice"))

        assert world.client.get(URL, headers=world.headers("alice")).json() == ARRANGED  # a new session's token

    @pytest.mark.parametrize("method", ["GET", "PUT", "DELETE"])
    def test_nobody_signed_out_reaches_it(self, world, method):
        assert world.client.request(method, URL, json=ARRANGED).status_code in (401, 403)
        assert world.stored("alice") == []

    def test_a_full_api_token_acts_as_its_user(self, world):
        headers = world.headers("alice")
        token = world.client.post(f"{API}/auth/tokens", json={"name": "layout"}, headers=headers).json()["token"]

        put = world.client.put(URL, json=ARRANGED, headers={"Authorization": f"Bearer {token}"})

        assert put.status_code == 200
        assert world.client.get(URL, headers=headers).json() == ARRANGED

    def test_a_limited_token_reads_it_and_never_changes_it(self, world):
        headers = world.headers("alice")
        world.client.put(URL, json=ARRANGED, headers=headers)
        token = world.client.post(f"{API}/auth/tokens", json={"name": "ro", "read_only": True}, headers=headers).json()
        limited = {"Authorization": f"Bearer {token['token']}"}

        assert world.client.get(URL, headers=limited).json() == ARRANGED
        for method in ("PUT", "DELETE"):
            refused = world.client.request(method, URL, json=layout(("cpu", False)), headers=limited)
            assert (refused.status_code, refused.json()["detail"]) == (403, scopes.LIMITED_TOKEN_CANNOT)
        assert world.client.get(URL, headers=headers).json() == ARRANGED


class TestWhatIsAccepted:
    def test_the_order_and_the_hidden_widgets_are_kept_as_sent(self, world):
        headers = world.headers("alice")
        everything = layout(*[(name, name in ("network", "containers")) for name in reversed(DEFAULT_WIDGETS)])

        assert world.client.put(URL, json=everything, headers=headers).status_code == 200
        assert world.client.get(URL, headers=headers).json() == everything

    def test_ids_the_server_has_no_widget_for_are_kept_for_the_web_ui_to_ignore(self, world):
        headers = world.headers("alice")
        later = layout(("cpu", False), ("a-widget-of-a-later-version", True))

        assert world.client.put(URL, json=later, headers=headers).status_code == 200
        assert world.client.get(URL, headers=headers).json() == later

    def test_the_biggest_layout_is_accepted(self, world):
        biggest = layout(*[(f"widget-{n}", n % 2 == 0) for n in range(64)])

        assert world.client.put(URL, json=biggest, headers=world.headers("alice")).status_code == 200

    @pytest.mark.parametrize("body", [
        {},
        {"widgets": []},
        {"widgets": "cpu"},
        {"widgets": ["cpu"]},
        {"widgets": [{"id": "cpu"}]},
        {"widgets": [{"id": "cpu", "hidden": "yes"}]},
        {"widgets": [{"id": "cpu", "hidden": 1}]},
        {"widgets": [{"id": "cpu", "hidden": None}]},
        {"widgets": [{"hidden": False}]},
        {"widgets": [{"id": "", "hidden": False}]},
        {"widgets": [{"id": "CPU", "hidden": False}]},
        {"widgets": [{"id": "cpu load", "hidden": False}]},
        {"widgets": [{"id": "../cpu", "hidden": False}]},
        {"widgets": [{"id": "-cpu", "hidden": False}]},
        {"widgets": [{"id": "x" * 33, "hidden": False}]},
        {"widgets": [{"id": 7, "hidden": False}]},
        {"widgets": [{"id": "cpu", "hidden": False, "size": "big"}]},
        {"widgets": [{"id": "cpu", "hidden": False}, {"id": "cpu", "hidden": True}]},
        {"widgets": [{"id": f"w{n}", "hidden": False} for n in range(65)]},
        {"widgets": [{"id": "cpu", "hidden": False}], "theme": "dark"},
        [],
        "cpu",
        None,
    ])
    def test_anything_else_answers_422_and_stores_nothing(self, world, body):
        headers = world.headers("alice")
        world.client.put(URL, json=ARRANGED, headers=headers)
        before = len(world.statements)

        response = world.client.put(URL, json=body, headers=headers)

        assert response.status_code == 422, response.text
        assert world.client.get(URL, headers=headers).json() == ARRANGED
        assert writes(world, before) == []
        assert len(world.audit()) == 1  # the first save only

    def test_a_body_that_is_no_json_answers_422(self, world):
        response = world.client.put(URL, content=b"{not json", headers={
            **world.headers("alice"), "Content-Type": "application/json"})

        assert response.status_code == 422
        assert world.stored("alice") == []


class TestWritesOnlyForARealChange:
    def test_saving_the_layout_that_is_stored_writes_nothing_and_is_not_audited(self, world):
        headers = world.headers("alice")
        world.client.put(URL, json=ARRANGED, headers=headers)
        before = len(world.statements)

        again = world.client.put(URL, json=ARRANGED, headers=headers)

        assert (again.status_code, again.json()) == (200, ARRANGED)
        assert writes(world, before) == []
        assert len(world.audit()) == 1

    def test_reading_writes_nothing(self, world):
        headers = world.headers("alice")
        world.client.put(URL, json=ARRANGED, headers=headers)
        before = len(world.statements)

        for _ in range(3):
            world.client.get(URL, headers=headers)

        assert writes(world, before) == []

    def test_a_change_replaces_the_row_and_keeps_one_per_user(self, world):
        headers = world.headers("alice")
        world.client.put(URL, json=ARRANGED, headers=headers)
        moved = layout(("cpu", False), ("disk-io", False), ("memory", True), ("storage", False))

        world.client.put(URL, json=moved, headers=headers)

        assert [json.loads(row) for row in world.stored("alice")] == [moved["widgets"]]

    def test_each_real_change_is_one_audit_entry_of_the_user_about_their_own_dashboard(self, world):
        headers = world.headers("alice")
        world.client.put(URL, json=ARRANGED, headers=headers, )
        world.client.put(URL, json=layout(("cpu", False)), headers=headers)

        entries = world.audit()

        assert [(e["username"], e["resource_type"], e["resource_id"], e["severity"], e["success"])
                for e in entries] == [("alice", "dashboard_layout", "alice", "info", True)] * 2
        assert "4 widgets, 1 hidden" in entries[0]["details"]
        assert "1 widget, 0 hidden" in entries[1]["details"]


class TestReset:
    def test_delete_returns_the_default_and_removes_the_row(self, world):
        headers = world.headers("alice")
        world.client.put(URL, json=ARRANGED, headers=headers)

        reset = world.client.delete(URL, headers=headers)

        assert (reset.status_code, reset.json()) == (200, {"widgets": []})
        assert world.stored("alice") == []
        assert world.client.get(URL, headers=headers).json() == {"widgets": []}
        assert [e["details"] for e in world.audit()][-1] == "Reset the dashboard to its default layout"

    def test_a_reset_of_a_default_layout_writes_nothing_and_is_not_audited(self, world):
        headers = world.headers("alice")
        before = len(world.statements)

        reset = world.client.delete(URL, headers=headers)

        assert (reset.status_code, reset.json()) == (200, {"widgets": []})
        assert writes(world, before) == []
        assert world.audit() == []

    def test_a_reset_leaves_other_users_layouts(self, world):
        world.client.put(URL, json=ARRANGED, headers=world.headers("alice"))
        world.client.put(URL, json=ARRANGED, headers=world.headers("bob"))

        world.client.delete(URL, headers=world.headers("alice"))

        assert world.client.get(URL, headers=world.headers("bob")).json() == ARRANGED


class TestAStoredLayoutIsUntrusted:
    """A restored or damaged database may hold anything in the column (ADR-0144): the answer is the
    default layout, never a 500 and never the text as it is."""

    @pytest.mark.parametrize("stored", [
        "", "not json", "{}", "null", "7", '"cpu"', '["cpu"]', '[{"id": "cpu"}]',
        '[{"id": "cpu", "hidden": "no"}]', '[{"id": "cpu", "hidden": false}, {"id": "cpu", "hidden": false}]',
        '[{"id": "<script>", "hidden": false}]', json.dumps([{"id": f"w{n}", "hidden": False} for n in range(65)]),
    ])
    def test_an_invalid_row_reads_as_the_default(self, world, stored):
        with Session(world.engine) as session:
            session.add(DashboardLayout(user_id=world.ids["alice"], layout=stored))
            session.commit()

        response = world.client.get(URL, headers=world.headers("alice"))

        assert (response.status_code, response.json()) == (200, {"widgets": []})

    def test_a_save_over_an_invalid_row_replaces_it(self, world):
        with Session(world.engine) as session:
            session.add(DashboardLayout(user_id=world.ids["alice"], layout="not json"))
            session.commit()

        world.client.put(URL, json=ARRANGED, headers=world.headers("alice"))

        assert world.client.get(URL, headers=world.headers("alice")).json() == ARRANGED

    def test_a_row_of_a_non_text_value_reads_as_the_default(self, world):
        with Session(world.engine) as session:
            session.execute(text("INSERT INTO dashboard_layouts (user_id, layout) VALUES (:u, X'ff00fe')"),
                            {"u": world.ids["alice"]})
            session.commit()

        response = world.client.get(URL, headers=world.headers("alice"))

        assert (response.status_code, response.json()) == (200, {"widgets": []})


class TestWhenTheUserGoes:
    def test_deleting_a_user_deletes_their_layout_and_only_theirs(self, world):
        world.client.put(URL, json=ARRANGED, headers=world.headers("alice"))
        world.client.put(URL, json=ARRANGED, headers=world.headers("bob"))

        gone = world.client.delete(f"{API}/auth/users/{world.ids['alice']}", params={"username": "alice"},
                                   headers=world.headers("admin"))

        assert gone.status_code == 200, gone.text
        assert world.stored("alice") == []
        assert len(world.stored("bob")) == 1

    def test_a_new_user_with_a_reused_id_starts_with_the_default(self, world):
        world.client.put(URL, json=ARRANGED, headers=world.headers("bob"))  # bob has the highest id
        world.client.delete(f"{API}/auth/users/{world.ids['bob']}", params={"username": "bob"},
                            headers=world.headers("admin"))
        with Session(world.engine) as session:
            session.add(User(id=world.ids["bob"], username="carol", hashed_password=PASSWORD_HASH))
            session.commit()

        assert world.client.get(URL, headers=world.headers("carol")).json() == {"widgets": []}

    def test_a_rename_keeps_the_layout(self, world):
        world.client.put(URL, json=ARRANGED, headers=world.headers("alice"))

        renamed = world.client.put(f"{API}/auth/users/{world.ids['alice']}", json={"username": "alice", "new_username": "alice2"},
                                   headers=world.headers("admin"))

        assert renamed.status_code == 200, renamed.text
        assert world.client.get(URL, headers=world.headers("alice2")).json() == ARRANGED


class TestTheTable:
    def test_it_is_a_new_table_and_the_users_table_is_not_altered(self):
        users = {column.name for column in User.__table__.columns}

        assert "dashboard_layout" not in users
        assert DashboardLayout.__tablename__ == "dashboard_layouts"
        assert [column.name for column in DashboardLayout.__table__.primary_key] == ["user_id"]
