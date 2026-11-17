"""API tokens limited to reading, or to areas of the API (auth.api-token-scopes, ADR-0392).

A token's scope is its access, read only or read and change, and its areas: the first segments of
the paths after /api/v1/, one per router prefix. A token with change access to every area is full,
as every token was before; any other is limited. authenticate_token checks the scope of every API
token, HTTP and WebSocket, with the operation that the request or the socket asks for.

The walk calls every route with tokens of each scope. A regular user's limited token shows for every
route whether its scope let the request through (then require_admin answers "Administrator privileges
required", or a user route its own answer) or refused it, without running an administrator's handler;
an administrator's limited token is sent only where its scope refuses, so that no handler runs.
The classification of the operations is this file's own copy (KIND_TABLE), compared with
scopes.OPERATION_KINDS, so that a change on either side fails.
"""
from __future__ import annotations

import inspect
import re
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.routing import APIWebSocketRoute
from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool
from starlette.websockets import WebSocketDisconnect

from src.audit.schemas import AuditAction
from src.auth import scopes
from src.auth.models import APIToken, User
from src.auth.utils import authenticate_token, generate_api_token
from src.database import Base
from src.websocket import events

from .audit_helpers import audit_entries
from .session_tokens import session_token
from .test_authz import (
    API,
    PUBLIC,
    USER_ALLOWED,
    WEBSOCKETS,
    _auth,
    _call,
    _http_operations,
    _route_contexts,
    _url,
)
from . import test_authz
from .test_backup_restore import MANIFEST, _file, _make_db, _restore, _store, make_env

# The fixtures of test_authz.py, as this module's own: the database of admin, alice and bob, the app
# on it, their sessions, and the autouse ones (no host command and no Docker; fresh rate limits).
authz_db = test_authz.authz_db
client = test_authz.client
admin_headers = test_authz.admin_headers
alice_headers = test_authz.alice_headers
no_side_effects = test_authz.no_side_effects
fresh_rate_limits = test_authz.fresh_rate_limits

# The areas, one per router prefix (ADR-0392).
AREAS = {
    "apps", "audit", "auth", "backup", "containers", "files", "groups", "health", "logs", "monitor",
    "network", "notifications", "offsite", "power", "scheduler", "security", "shares", "snapshots",
    "storage", "system", "updates",
}

# The operations whose kind is not their method's, and the borderline ones kept as their method says.
SECRET = "secret"
READ = "read"
CHANGE = "change"
NEVER = "never"
KIND_TABLE = {
    # Reads whose answer holds secrets: a read-only token does not get them.
    ("GET", f"{API}/backup/download/{{filename}}"): SECRET,
    ("GET", f"{API}/notifications/configs"): SECRET,
    # POSTs that only read.
    ("POST", f"{API}/power/ups/test"): READ,
    ("POST", f"{API}/files/upload-leftovers/check"): READ,
    # Changes, although nothing that UNOS keeps changes.
    ("POST", f"{API}/notifications/test/{{config_id}}"): CHANGE,
    ("POST", f"{API}/notifications/send"): CHANGE,
    ("POST", f"{API}/health/disk/{{device:path}}/test"): CHANGE,
    # Reads, although they show much; none of them answers a password, a passphrase or a key.
    ("GET", f"{API}/files/download"): READ,
    ("GET", f"{API}/containers/{{container_id}}/logs"): READ,
    ("GET", f"{API}/audit/logs/export"): READ,
    ("GET", f"{API}/logs/file/{{filename}}"): READ,
    ("GET", f"{API}/scheduler/tasks"): READ,
    ("GET", f"{API}/power/ups/config"): READ,
    ("GET", f"{API}/offsite/settings"): READ,
    ("GET", f"{API}/security/certificates"): READ,
    # Never for a limited token, besides every change in the area auth: they replace or decide
    # who may do what.
    ("POST", f"{API}/backup/restore/{{filename}}"): NEVER,
    ("POST", f"{API}/security/two-factor-requirement/enable"): NEVER,
    ("POST", f"{API}/security/two-factor-requirement/disable"): NEVER,
}

SOCKET_AREAS = {"/ws/v1/events": None, "/ws/v1/logs/{container_id}": "containers", "/ws/v1/journal": "logs"}

ADMIN_REQUIRED = "Administrator privileges required"
SCOPE_DETAILS = {
    scopes.UNREADABLE_SCOPE, scopes.LIMITED_TOKEN_CANNOT, scopes.OUTSIDE_AREAS, scopes.READ_ONLY,
    scopes.SECRET_ANSWER,
}


def area_of(path: str) -> str:
    return path.split("/")[3]


def expected_kind(method: str, path: str) -> str:
    if (method, path) in KIND_TABLE:
        return KIND_TABLE[(method, path)]
    if method in ("GET", "HEAD"):
        return READ
    return NEVER if area_of(path) == "auth" else CHANGE


def expected_refusal(scope: dict, method: str, path: str) -> str | None:
    """What a token of ``scope`` (the body of its creation) gets for an operation: the 403's detail,
    or None when its scope lets the request through."""
    read_only, areas = scope.get("read_only", False), scope.get("areas")
    kind = expected_kind(method, path)
    if not read_only and areas is None:
        return None
    if kind == NEVER:
        return scopes.LIMITED_TOKEN_CANNOT
    if areas is not None and area_of(path) not in areas:
        return scopes.OUTSIDE_AREAS
    if read_only and kind == CHANGE:
        return scopes.READ_ONLY
    if read_only and kind == SECRET:
        return scopes.SECRET_ANSWER
    return None


def create(client, headers, **body) -> dict:
    response = client.post(f"{API}/auth/tokens", json={"name": "script", **body}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def stored_permissions(authz_db, token_id: int):
    with closing(sqlite3.connect(authz_db.engine.url.database)) as conn:
        return conn.execute("SELECT permissions FROM api_tokens WHERE id = ?", (token_id,)).fetchone()[0]


def store_permissions(authz_db, token_id: int, value) -> None:
    """Write ``value`` as it is, as a restored or damaged database can hold it."""
    with closing(sqlite3.connect(authz_db.engine.url.database)) as conn:
        conn.execute("UPDATE api_tokens SET permissions = ? WHERE id = ?", (value, token_id))
        conn.commit()


def detail(response) -> str | None:
    try:
        return response.json().get("detail")
    except ValueError:
        return None


# One scope of each shape: read only everywhere, change access to each area alone, read only in one
# area, and change access to two areas.
WALKED_SCOPES = (
    [{"read_only": True}]
    + [{"areas": [area]} for area in sorted(AREAS)]
    + [{"read_only": True, "areas": ["storage"]}, {"read_only": True, "areas": ["auth"]},
       {"areas": ["backup", "storage"]}]
)


def _scope_id(scope: dict) -> str:
    access = "read" if scope.get("read_only") else "change"
    return f"{access}-{'+'.join(scope.get('areas') or ['every-area'])}"


class TestTheTable:
    def test_the_areas_are_the_prefixes_of_the_routes(self):
        routed = {area_of(path) for _, path, _ in _http_operations()}
        assert set(scopes.AREAS) == AREAS == routed
        assert list(scopes.AREAS) == sorted(AREAS)

    def test_every_route_has_an_area(self):
        problems = [f"{method} {path}" for method, path, _ in _http_operations()
                    if not path.startswith(f"{API}/") or area_of(path) not in scopes.AREAS]
        assert problems == []

    def test_the_kinds_are_as_decided(self):
        assert {key: kind.value for key, kind in scopes.OPERATION_KINDS.items()} == KIND_TABLE

    def test_the_table_names_existing_operations(self):
        operations = {(method, path) for method, path, _ in _http_operations()}
        assert set(KIND_TABLE) - operations == set()

    def test_each_operation_is_classified_by_its_own_template(self):
        """A concrete path of one route never takes the kind of another route's template."""
        problems = []
        for method, path, _ in _http_operations():
            kind = scopes.kind_of(method, _url(path))
            if kind.value != expected_kind(method, path):
                problems.append(f"{method} {path}: {kind.value}, expected {expected_kind(method, path)}")
        assert problems == []

    def test_head_is_a_read_and_reads_what_get_reads(self):
        assert scopes.kind_of("HEAD", f"{API}/storage/pools").value == READ
        assert scopes.kind_of("HEAD", f"{API}/backup/download/a.tar.gz").value == SECRET

    def test_the_sockets_have_their_areas(self):
        sockets = {effective.path for original, effective in _route_contexts()
                   if isinstance(original, APIWebSocketRoute)}
        assert sockets == WEBSOCKETS == set(SOCKET_AREAS)
        assert scopes.SOCKET_AREAS == SOCKET_AREAS

    def test_every_credential_check_names_its_operation(self):
        """authenticate_token takes the operation as a required keyword: a new caller cannot leave
        it out and so skip the scope."""
        parameter = inspect.signature(authenticate_token).parameters["operation"]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert parameter.default is inspect.Parameter.empty


def test_the_web_ui_knows_the_same_areas():
    """webui/src/lib/api/auth.ts offers the areas to choose and warns of WARNED_AREAS."""
    source = (Path(__file__).resolve().parents[2] / "webui" / "src" / "lib" / "api" / "auth.ts").read_text()

    def listed(name: str) -> tuple[str, ...]:
        found = re.search(rf"export const {name}\b[^=]*= \[(.*?)\]", source, re.S)
        assert found is not None, name
        return tuple(re.findall(r'"([a-z]+)"', found.group(1)))

    assert listed("TOKEN_AREAS") == scopes.AREAS
    assert listed("WARNED_AREAS") == scopes.WARNED_AREAS


class TestStoredValues:
    @pytest.mark.parametrize(
        "read_only,areas,stored",
        [
            (False, None, "*"),
            (True, None, "read"),
            (True, ["storage"], "read,storage"),
            (False, ["storage", "backup"], "backup,storage"),
            (True, ["storage", "auth", "apps"], "read,apps,auth,storage"),
        ],
    )
    def test_a_scope_is_stored_in_one_way_and_read_back(self, read_only, areas, stored):
        scope = scopes.Scope(read_only=read_only, areas=None if areas is None else frozenset(areas))
        assert scopes.stored_value(scope) == stored
        assert scopes.parse(stored) == scope

    def test_a_scope_of_no_area_is_never_stored(self):
        """"read" alone is read only in every area: no scope of no area may be written as it."""
        with pytest.raises(ValueError):
            scopes.stored_value(scopes.Scope(read_only=True, areas=frozenset()))

    @pytest.mark.parametrize(
        "kind,raw,text",
        [("text", b"read,storage", "read,storage"), ("text", b"\x80\xff", None), ("blob", b"read", None),
         ("null", None, None), ("integer", b"7", None)],
    )
    def test_a_stored_value_is_read_as_text_only_when_it_is_utf_8_text(self, kind, raw, text):
        assert scopes.stored_text(kind, raw) == text

    @pytest.mark.parametrize("stored", ["storage,read", "shares,backup"])
    def test_another_order_of_known_words_is_read(self, stored):
        assert scopes.parse(stored) is not None

    @pytest.mark.parametrize(
        "stored",
        ["", ",", "read,", ",read", "foo", "read,foo", "*,read", "read,*", "**", "read,read", "storage,storage",
         "READ", "Storage", " read", "read ,storage", "read;storage", None, 5, 1.5, b"read", ["read"]],
    )
    def test_any_other_value_cannot_be_read(self, stored):
        assert scopes.parse(stored) is None


class TestTheApi:
    def test_a_token_without_a_limit_is_full_as_before(self, client, authz_db, alice_headers):
        created = create(client, alice_headers)

        assert (created["read_only"], created["areas"]) == (False, None)
        assert "permissions" not in created
        assert stored_permissions(authz_db, created["id"]) == "*"
        [listed] = client.get(f"{API}/auth/tokens", headers=alice_headers).json()
        assert (listed["read_only"], listed["areas"], listed["scope_known"]) == (False, None, True)
        assert "permissions" not in listed

    def test_a_limited_token_is_answered_and_listed_with_its_scope(self, client, authz_db, alice_headers):
        created = create(client, alice_headers, read_only=True, areas=["storage", "backup"])

        assert (created["read_only"], created["areas"]) == (True, ["backup", "storage"])
        assert stored_permissions(authz_db, created["id"]) == "read,backup,storage"
        [listed] = client.get(f"{API}/auth/tokens", headers=alice_headers).json()
        assert (listed["read_only"], listed["areas"], listed["scope_known"]) == (True, ["backup", "storage"], True)

    def test_every_area_can_be_named(self, client, alice_headers):
        created = create(client, alice_headers, areas=sorted(AREAS, reverse=True))
        assert created["areas"] == sorted(AREAS)

    @pytest.mark.parametrize(
        "body",
        [
            {"areas": []},
            {"areas": ["storage", "storage"]},
            {"areas": ["nope"]},
            {"areas": ["Storage"]},
            {"areas": "storage"},
            {"areas": [None]},
            {"read_only": "yes"},
            {"read_only": "true"},
            {"read_only": 1},
            {"read_only": None},
            {"permissions": "*"},
            {"permissions": "read"},
            {"scope": "read"},
        ],
        ids=["no-area", "an-area-twice", "unknown-area", "capitalised", "a-string", "null-area", "yes",
             "true-as-text", "one", "null-access", "permissions-star", "permissions-read", "unknown-field"],
    )
    def test_a_scope_that_is_not_one_answers_422_and_makes_no_token(self, client, authz_db, alice_headers, body):
        response = client.post(f"{API}/auth/tokens", json={"name": "t", **body}, headers=alice_headers)

        assert response.status_code == 422, response.text
        with Session(authz_db.engine) as session:
            assert session.scalars(select(APIToken)).all() == []

    def test_the_creation_is_audited_with_its_scope(self, client, tmp_path, alice_headers):
        create(client, alice_headers, name="all")
        create(client, alice_headers, name="reader", read_only=True)
        create(client, alice_headers, name="backups", read_only=True, areas=["storage", "backup"])
        create(client, alice_headers, name="shares", areas=["shares", "groups"])

        entries = [(e["action"], e["username"], e["resource_type"], e["resource_id"], e["details"])
                   for e in audit_entries(tmp_path / "audit.jsonl") if e["resource_type"] == "api_token"]
        assert entries == [
            (AuditAction.OTHER.value, "alice", "api_token", "all", "Created API token: all (full access)"),
            (AuditAction.OTHER.value, "alice", "api_token", "reader", "Created API token: reader (read only, every area)"),
            (AuditAction.OTHER.value, "alice", "api_token", "backups",
             "Created API token: backups (read only: backup, storage)"),
            (AuditAction.OTHER.value, "alice", "api_token", "shares",
             "Created API token: shares (read and change: groups, shares)"),
        ]


class TestTheSteps:
    """The entry's steps through the API, with an administrator's tokens."""

    def test_a_read_only_token_reads_and_every_change_answers_403(self, client, admin_headers):
        token = create(client, admin_headers, read_only=True)
        headers = _auth(token["token"])

        for path in ("/auth/me", "/auth/users", "/auth/tokens", "/audit/logs", "/audit/logs/count",
                     "/security/login-history", "/security/two-factor-requirement", "/audit/actions"):
            response = client.get(f"{API}{path}", headers=headers)
            assert response.status_code == 200, f"{path}: {response.text}"

        for method, path, refused in (
            ("PUT", "/system/timezone", scopes.READ_ONLY),
            ("DELETE", "/audit/logs", scopes.READ_ONLY),
            ("POST", "/storage/pools", scopes.READ_ONLY),
            ("POST", "/notifications/test/1", scopes.READ_ONLY),
            ("POST", "/auth/users", scopes.LIMITED_TOKEN_CANNOT),
            ("POST", "/auth/change-password", scopes.LIMITED_TOKEN_CANNOT),
            ("POST", "/auth/totp/disable", scopes.LIMITED_TOKEN_CANNOT),
            ("POST", "/auth/tokens", scopes.LIMITED_TOKEN_CANNOT),
            ("DELETE", f"/auth/tokens/{token['id']}", scopes.LIMITED_TOKEN_CANNOT),
            ("POST", "/backup/restore/x.tar.gz", scopes.LIMITED_TOKEN_CANNOT),
            ("GET", "/backup/download/x.tar.gz", scopes.SECRET_ANSWER),
            ("GET", "/notifications/configs", scopes.SECRET_ANSWER),
        ):
            response = client.request(method, f"{API}{path}", json={}, headers=headers)
            assert (response.status_code, detail(response)) == (403, refused), f"{method} {path}"

    def test_a_token_of_an_area_answers_403_outside_it(self, client, admin_headers):
        headers = _auth(create(client, admin_headers, areas=["audit"])["token"])

        assert client.get(f"{API}/audit/logs", headers=headers).status_code == 200
        assert client.get(f"{API}/audit/actions", headers=headers).status_code == 200
        for method, path in (("GET", "/auth/me"), ("GET", "/storage/pools"), ("GET", "/system/info"),
                             ("PUT", "/system/timezone"), ("GET", "/security/login-history")):
            response = client.request(method, f"{API}{path}", json={}, headers=headers)
            assert (response.status_code, detail(response)) == (403, scopes.OUTSIDE_AREAS), f"{method} {path}"
        # Inside it, a change goes through.
        assert client.delete(f"{API}/audit/logs", params={"before_date": "2000-01-01"}, headers=headers).status_code == 200

    def test_a_full_token_still_does_what_its_user_may(self, client, admin_headers):
        headers = _auth(create(client, admin_headers)["token"])

        assert client.get(f"{API}/auth/users", headers=headers).status_code == 200
        created = client.post(f"{API}/auth/users", json={"username": "carol", "password": "Carol-pass-2026"},
                              headers=headers)
        assert created.status_code == 200, created.text


class TestEveryRoute:
    """Every route for each scope (ADR-0392), as test_authz.py's matrix does for the roles."""

    @pytest.mark.parametrize("scope", WALKED_SCOPES, ids=[_scope_id(s) for s in WALKED_SCOPES])
    def test_a_regular_users_limited_token(self, client, alice_headers, scope):
        headers = _auth(create(client, alice_headers, **scope)["token"])
        problems = []
        for method, path, _ in _http_operations():
            if (method, path) in PUBLIC:
                continue
            response = _call(client, method, path, headers)
            refused = expected_refusal(scope, method, path)
            if refused is not None:
                if (response.status_code, detail(response)) != (403, refused):
                    problems.append(f"{method} {path}: {response.status_code} {response.text[:80]}, expected {refused}")
            elif (method, path) in USER_ALLOWED:
                if response.status_code in (401, 403):
                    problems.append(f"{method} {path}: a user route the scope allows got {response.status_code}")
            elif (response.status_code, detail(response)) != (403, ADMIN_REQUIRED):
                problems.append(f"{method} {path}: {response.status_code} {response.text[:80]}, expected the role's 403")
        assert problems == []

    @pytest.mark.parametrize("scope", WALKED_SCOPES, ids=[_scope_id(s) for s in WALKED_SCOPES])
    def test_an_administrators_limited_token_is_refused_before_any_handler(self, client, admin_headers, scope):
        headers = _auth(create(client, admin_headers, **scope)["token"])
        problems = []
        for method, path, _ in _http_operations():
            refused = expected_refusal(scope, method, path)
            if (method, path) in PUBLIC or refused is None:
                continue
            response = _call(client, method, path, headers)
            if (response.status_code, detail(response)) != (403, refused):
                problems.append(f"{method} {path}: {response.status_code} {response.text[:80]}, expected {refused}")
        assert problems == []

    def test_a_full_token_is_refused_nowhere_by_a_scope(self, client, alice_headers):
        headers = _auth(create(client, alice_headers)["token"])
        problems = []
        for method, path, _ in _http_operations():
            if (method, path) in PUBLIC:
                continue
            response = _call(client, method, path, headers)
            if response.status_code == 403 and detail(response) in SCOPE_DETAILS:
                problems.append(f"{method} {path}: {detail(response)}")
        assert problems == []


class TestValuesThatCannotBeRead:
    """A restored or damaged row is read as stored at every use (ADR-0144): its token is refused
    everywhere, and listed as such."""

    # A number is kept as the text "7" by the column's affinity; a blob stays a blob.
    @pytest.mark.parametrize("value", ["", "nonsense", "read,", "*,read", "read,read", "Storage", None, 7, b"read"],
                             ids=["empty", "unknown", "trailing-comma", "star-and-read", "repeated", "capitalised",
                                  "null", "a-number", "blob"])
    def test_the_token_is_refused_everywhere_and_listed_as_unreadable(self, client, authz_db, admin_headers, value):
        token = create(client, admin_headers)
        store_permissions(authz_db, token["id"], value)
        headers = _auth(token["token"])

        for method, path in (("GET", "/auth/me"), ("GET", "/system/info"), ("GET", "/auth/tokens"),
                             ("PUT", "/system/timezone"), ("POST", "/auth/users")):
            response = client.request(method, f"{API}{path}", json={}, headers=headers)
            assert (response.status_code, detail(response)) == (403, scopes.UNREADABLE_SCOPE), f"{method} {path}"
        [listed] = client.get(f"{API}/auth/tokens", headers=admin_headers).json()
        assert (listed["scope_known"], listed["read_only"], listed["areas"]) == (False, True, [])

    def test_text_that_is_not_utf_8_refuses_the_token_which_its_owner_still_lists_and_deletes(
            self, client, authz_db, admin_headers):
        """SQLite keeps it as text, and the driver cannot decode it: the column is read as bytes."""
        token = create(client, admin_headers)
        with closing(sqlite3.connect(authz_db.engine.url.database)) as conn:
            conn.execute("UPDATE api_tokens SET permissions = CAST(? AS TEXT) WHERE id = ?", (b"read,\xff", token["id"]))
            conn.commit()
            assert conn.execute("SELECT typeof(permissions) FROM api_tokens").fetchone() == ("text",)

        response = client.get(f"{API}/auth/me", headers=_auth(token["token"]))
        assert (response.status_code, detail(response)) == (403, scopes.UNREADABLE_SCOPE)
        [listed] = client.get(f"{API}/auth/tokens", headers=admin_headers).json()
        assert (listed["id"], listed["scope_known"]) == (token["id"], False)
        assert client.post(f"{API}/auth/tokens/{token['id']}/toggle", headers=admin_headers).status_code == 200
        assert client.delete(f"{API}/auth/tokens/{token['id']}", headers=admin_headers).status_code == 200
        assert client.get(f"{API}/auth/tokens", headers=admin_headers).json() == []

    def test_a_changed_scope_applies_at_the_next_request(self, client, authz_db, admin_headers):
        token = create(client, admin_headers)
        headers = _auth(token["token"])
        assert client.get(f"{API}/auth/users", headers=headers).status_code == 200

        store_permissions(authz_db, token["id"], "read,storage")

        response = client.get(f"{API}/auth/users", headers=headers)
        assert (response.status_code, detail(response)) == (403, scopes.OUTSIDE_AREAS)


# --- WebSockets ---------------------------------------------------------------------------------


@pytest.fixture
def sockets(authz_db, monkeypatch):
    """The WebSockets on authz_db, with a check of the credential every 0.1 s."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{authz_db.engine.url.database}", poolclass=NullPool)
    monkeypatch.setattr(events, "async_session", async_sessionmaker(engine, expire_on_commit=False))
    monkeypatch.setattr(events, "WS_REAUTH_INTERVAL_SECONDS", 0.1)
    monkeypatch.setattr(events, "STATS_INTERVAL_SECONDS", 0.05)
    monkeypatch.setattr(events, "get_system_stats", lambda: {"type": "stats", "data": {}})
    yield authz_db


def token_of(authz_db, username: str, permissions: str) -> tuple[str, int]:
    full_token, token_hash = generate_api_token()
    with Session(authz_db.engine) as session:
        user_id = session.scalars(select(User.id).where(User.username == username)).one()
        row = APIToken(name="socket", token_hash=token_hash, token_prefix=full_token[:12], user_id=user_id,
                       permissions=permissions)
        session.add(row)
        session.commit()
        return full_token, row.id


def first_answer(client, path: str, token: str):
    """The first message, or the close, of a socket that signs in with ``token``."""
    with client.websocket_connect(path) as ws:
        ws.send_json({"type": "auth", "token": token})
        try:
            return ws.receive_json()
        except WebSocketDisconnect as closed:
            return closed.code


class TestWebSockets:
    @pytest.mark.parametrize("permissions", ["*", "read"])
    def test_the_events_socket_takes_a_token_of_every_area(self, client, sockets, permissions):
        token, _ = token_of(sockets, "alice", permissions)
        assert first_answer(client, "/ws/v1/events", token) == {"type": "auth_ok"}

    @pytest.mark.parametrize("permissions", ["read,system", "monitor,system", "read,apps,audit,auth,backup,containers,"
                                             "files,groups,health,logs,monitor,network,notifications,offsite,power,"
                                             "scheduler,security,shares,snapshots,storage", "nonsense"])
    def test_the_events_socket_refuses_a_token_limited_to_areas(self, client, sockets, permissions):
        token, _ = token_of(sockets, "alice", permissions)
        assert first_answer(client, "/ws/v1/events", token) == 4403

    @pytest.mark.parametrize("path,permissions", [("/ws/v1/logs/abc", "read,logs"), ("/ws/v1/logs/abc", "storage"),
                                                  ("/ws/v1/journal", "read,containers"), ("/ws/v1/journal", "system")])
    def test_the_log_sockets_refuse_a_token_of_other_areas(self, client, sockets, path, permissions):
        token, _ = token_of(sockets, "admin", permissions)
        with patch("docker.DockerClient", side_effect=AssertionError("docker must not be reached")):
            assert first_answer(client, path, token) == 4403

    def test_the_container_log_socket_takes_a_read_only_token_of_containers(self, client, sockets):
        token, _ = token_of(sockets, "admin", "read,containers")
        with patch.object(events, "_stream_container_logs", side_effect=_after_auth_ok):
            assert first_answer(client, "/ws/v1/logs/abc", token) == {"type": "auth_ok"}

    def test_the_journal_socket_takes_a_read_only_token_of_logs(self, client, sockets):
        token, _ = token_of(sockets, "admin", "read,logs")
        with patch.object(events, "_follow_journal", side_effect=_after_auth_ok):
            assert first_answer(client, "/ws/v1/journal", token) == {"type": "auth_ok"}

    def test_a_scope_changed_while_the_socket_is_open_closes_it_at_the_next_check(self, client, sockets):
        token, token_id = token_of(sockets, "alice", "read")
        with client.websocket_connect("/ws/v1/events") as ws:
            ws.send_json({"type": "auth", "token": token})
            assert ws.receive_json() == {"type": "auth_ok"}
            with closing(sqlite3.connect(sockets.engine.url.database)) as conn:
                conn.execute("UPDATE api_tokens SET permissions = 'read,storage' WHERE id = ?", (token_id,))
                conn.commit()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                try:
                    ws.receive_json()
                except WebSocketDisconnect as closed:
                    assert closed.code == 4403
                    break
            else:
                pytest.fail("the socket stayed open")


async def _after_auth_ok(*args):
    """In place of the container's log stream or the journal: the handshake answered auth_ok."""


# --- A restore ----------------------------------------------------------------------------------


class TestARestore:
    """A backup carries the tokens with their scopes in the same rows; a restore whose database has
    tokens with a scope that cannot be read says how many among its warnings."""

    @pytest.fixture
    def env(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.audit.router.AUDIT_FILE", str(tmp_path / "audit.jsonl"))
        yield from make_env(tmp_path, monkeypatch, smb_applied=True)

    @staticmethod
    def backup(env, *values) -> bytes:
        path = env.tmp / "restored.db"
        _make_db(path, users=[("admin2", 1)])
        engine = create_engine(f"sqlite:///{path}")
        Base.metadata.create_all(engine, tables=[APIToken.__table__])
        with engine.begin() as conn:
            for i, value in enumerate(values):
                # Bytes go in as text, as a database can hold text that is not UTF-8.
                conn.execute(text("INSERT INTO api_tokens (name, token_hash, token_prefix, user_id, is_active, "
                                  "permissions) VALUES (:name, :hash, 'unos_x', 1, 1, "
                                  + ("CAST(:value AS TEXT))" if isinstance(value, bytes) else ":value)")),
                             {"name": f"t{i}", "hash": f"hash-{i}", "value": value})
        engine.dispose()
        return path.read_bytes()

    def restore(self, env, db: bytes) -> dict:
        name = _store(env, [_file("manifest.json", MANIFEST), _file("unos.db", db)])
        answer = _restore(env, name)
        assert answer.status_code == 200, answer.text
        return answer.json()

    def test_tokens_that_cannot_be_read_are_counted_in_the_warnings(self, env):
        body = self.restore(env, self.backup(env, "*", "read,storage", "nonsense", None, "read,read", b"read,\xff"))

        warning = scopes.unreadable_warning(4)
        assert warning == ("4 restored API tokens have a scope that UNOS cannot read: they are refused until "
                           "they are deleted.")
        assert warning in body["warnings"] and warning in body["message"]
        with closing(sqlite3.connect(env.db_path)) as conn:
            stored = [row[0] for row in conn.execute("SELECT CAST(permissions AS BLOB) FROM api_tokens")]
        assert sorted(map(repr, stored)) == sorted(map(repr, [b"*", b"read,storage", b"nonsense", None,
                                                              b"read,read", b"read,\xff"]))

    def test_one_token_is_named_in_the_singular(self, env):
        body = self.restore(env, self.backup(env, "storage,", "read"))
        assert ("1 restored API token has a scope that UNOS cannot read: it is refused until it is deleted."
                in body["warnings"])

    @pytest.mark.parametrize("values", [("*", "read", "backup,storage"), ()], ids=["readable", "no-tokens"])
    def test_readable_scopes_say_nothing(self, env, values):
        assert self.restore(env, self.backup(env, *values))["warnings"] == []

    def test_a_backup_without_the_tokens_table_says_nothing(self, env):
        path = env.tmp / "restored.db"
        assert self.restore(env, _make_db(path, users=[("admin2", 1)]))["warnings"] == []
        assert scopes.unreadable_in(path) == 0


def test_unreadable_in_a_file_that_is_no_database(tmp_path):
    path = tmp_path / "not.db"
    path.write_bytes(b"not a database" * 100)
    assert scopes.unreadable_in(path) == 0


def test_a_path_or_a_socket_outside_the_areas_is_refused_to_a_limited_token():
    """A path that no area names, as the routes have none, is refused, not passed."""
    limited = scopes.parse("read")
    assert scopes.refusal(limited, "GET", "/somewhere/else") == scopes.OUTSIDE_AREAS
    assert scopes.refusal(limited, "WEBSOCKET", "/ws/v1/unknown") == scopes.OUTSIDE_AREAS
    assert scopes.refusal(scopes.FULL, "GET", "/somewhere/else") is None


def test_a_websocket_reads_with_any_access():
    assert scopes.refusal(scopes.parse("read"), "WEBSOCKET", "/ws/v1/events") is None
    assert scopes.refusal(scopes.parse("read,storage"), "WEBSOCKET", "/ws/v1/events") == scopes.OUTSIDE_AREAS


def test_the_scope_of_a_session_is_never_checked(client, authz_db):
    """Session tokens have no scope: a session of alice reaches her routes, as before."""
    headers = _auth(session_token(authz_db.engine, "alice"))
    assert client.get(f"{API}/auth/me", headers=headers).status_code == 200


def test_the_answer_of_a_refusal_names_no_scope_or_token(client, alice_headers):
    token = create(client, alice_headers, read_only=True, areas=["storage"])
    response = client.get(f"{API}/auth/me", headers=_auth(token["token"]))
    assert response.status_code == 403
    assert token["token"] not in response.text and "storage" not in response.text
    assert detail(response) == scopes.OUTSIDE_AREAS
