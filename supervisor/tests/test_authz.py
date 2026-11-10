"""Authorization matrix for every HTTP route, and role-specific behaviour.

Every route needs an explicit decision. It is either listed in PUBLIC, listed
in USER_ALLOWED (any signed-in user), or it must depend on ``require_admin``
(directly or through its router). A new route that does none of this fails
``test_every_route_has_an_explicit_authorization_decision``.
"""
import asyncio
import hashlib
import importlib
import ipaddress
import json
import logging
import re
import shlex
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import bcrypt
import docker
import httpx
import pytest
import uvicorn
import yaml
from fastapi.routing import APIRoute, APIWebSocketRoute
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool
from starlette.requests import Request

import src.host
from src.auth import utils as auth_utils
from src.auth.models import APIToken, User
from src.auth.router import MAX_API_TOKENS_PER_USER, TOO_MANY_API_TOKENS
from src.auth.router import limiter as auth_limiter
from src.auth.schemas import API_TOKEN_NAME_CHARS
from src.auth.utils import SESSION_NEEDED, require_admin, verify_token
from src.database import Base, get_db
from src.main import app
from src.main import limiter as app_limiter
from src.security.models import LoginAttempt
from .audit_helpers import audit_entries
from .session_tokens import session_token

try:
    from fastapi.routing import iter_route_contexts
except ImportError:  # older FastAPI flattened included routers into app.routes
    iter_route_contexts = None

REPO_ROOT = Path(__file__).resolve().parents[2]
API = "/api/v1"

# Reachable without credentials.
PUBLIC = {
    ("POST", f"{API}/auth/login"),
    ("POST", f"{API}/auth/setup"),  # refuses once any user exists
    ("GET", f"{API}/auth/status"),
    ("GET", f"{API}/health"),
}

# Any signed-in user, administrator or not.
USER_ALLOWED = {
    # Own account and credentials
    ("GET", f"{API}/auth/me"),
    ("POST", f"{API}/auth/logout"),  # ends the caller's own session (ADR-0329)
    ("POST", f"{API}/auth/change-password"),
    ("POST", f"{API}/auth/totp/setup"),
    ("POST", f"{API}/auth/totp/enable"),
    ("POST", f"{API}/auth/totp/disable"),
    ("GET", f"{API}/auth/totp/status"),
    ("GET", f"{API}/auth/tokens"),
    ("POST", f"{API}/auth/tokens"),
    ("DELETE", f"{API}/auth/tokens/{{token_id}}"),
    ("POST", f"{API}/auth/tokens/{{token_id}}/toggle"),
    ("POST", f"{API}/auth/recovery-codes/generate"),
    ("GET", f"{API}/auth/recovery-codes/status"),
    # Own SMB access: status, turn on with the current password (and 2FA code), turn off
    ("GET", f"{API}/auth/me/smb"),
    ("POST", f"{API}/auth/me/smb/enable"),
    ("POST", f"{API}/auth/me/smb/disable"),
    # Read-only system information, which every signed-in user may read
    ("GET", f"{API}/system/info"),
    ("GET", f"{API}/system/stats"),
    ("GET", f"{API}/system/network"),
    ("GET", f"{API}/system/timezone"),
    ("GET", f"{API}/system/timezones"),
    ("GET", f"{API}/system/pcie"),
    ("GET", f"{API}/system/temperatures"),
    # Own login history only (admins see everyone's)
    ("GET", f"{API}/security/login-history"),
}

# Authenticated by their own handshake; see test_ws_auth.py.
WEBSOCKETS = {"/ws/v1/events", "/ws/v1/logs/{container_id}"}

PASSWORD = "correct-password"
_PASSWORD_HASH = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode()


def _route_contexts():
    if iter_route_contexts is None:
        return [(route, route) for route in app.routes]
    return [(ctx.original_route, ctx) for ctx in iter_route_contexts(app.routes)]


def _http_operations():
    """(method, path, dependant) for every HTTP operation, router prefixes applied."""
    operations = []
    for original, effective in _route_contexts():
        if isinstance(original, APIRoute):
            for method in sorted(effective.methods):
                operations.append((method, effective.path, effective.dependant))
    return operations


def _dependency_calls(dependant):
    calls, stack = set(), [dependant]
    while stack:
        current = stack.pop()
        if current.call is not None:
            calls.add(current.call)
        stack.extend(current.dependencies)
    return calls


def _url(path):
    return re.sub(r"\{[^}]+\}", "1", path)


def _call(client, method, path, headers=None):
    body = {} if method in ("POST", "PUT", "PATCH") else None
    return client.request(method, _url(path), json=body, headers=headers or {})


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def no_side_effects(monkeypatch, tmp_path):
    """Handlers reached by these tests must never touch the host or Docker."""
    def forbidden(*args, **kwargs):
        raise RuntimeError("authorization tests must not run commands or reach Docker")

    for name in ("run", "Popen", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, forbidden)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", forbidden)
    monkeypatch.setattr(src.host, "run_on_host", forbidden)
    monkeypatch.setattr(docker, "from_env", forbidden)
    monkeypatch.setattr(docker, "DockerClient", forbidden)
    monkeypatch.setattr("src.audit.router.AUDIT_FILE", str(tmp_path / "audit.jsonl"))


@pytest.fixture(autouse=True)
def fresh_rate_limits():
    auth_limiter.reset()
    app_limiter.reset()
    yield
    auth_limiter.reset()
    app_limiter.reset()


@pytest.fixture
def authz_db(tmp_path):
    """Temporary database with one administrator and two regular users."""
    path = tmp_path / "authz.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        admin = User(username="admin", hashed_password=_PASSWORD_HASH, is_admin=True)
        alice = User(username="alice", hashed_password=_PASSWORD_HASH, is_admin=False)
        bob = User(username="bob", hashed_password=_PASSWORD_HASH, is_admin=False)
        session.add_all([admin, alice, bob])
        session.commit()
        ids = SimpleNamespace(admin_id=admin.id, alice_id=alice.id, bob_id=bob.id)

    maker = async_sessionmaker(
        create_async_engine(f"sqlite+aiosqlite:///{path}", poolclass=NullPool),
        expire_on_commit=False,
    )

    async def override_get_db():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield SimpleNamespace(engine=engine, **vars(ids))
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()


@pytest.fixture
def client(authz_db):
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def admin_headers(authz_db):
    return _auth(session_token(authz_db.engine, "admin"))


@pytest.fixture
def alice_headers(authz_db):
    return _auth(session_token(authz_db.engine, "alice"))


@pytest.fixture
def bob_headers(authz_db):
    return _auth(session_token(authz_db.engine, "bob"))


class TestRouteMatrix:
    def test_listed_routes_exist(self):
        operations = {(method, path) for method, path, _ in _http_operations()}

        assert PUBLIC - operations == set(), "stale PUBLIC entries"
        assert USER_ALLOWED - operations == set(), "stale USER_ALLOWED entries"
        assert not PUBLIC & USER_ALLOWED
        # Routes of included routers are enumerated too (guards against the
        # matrix silently checking nothing).
        assert {
            ("GET", f"{API}/storage/pools"),
            ("GET", f"{API}/auth/users"),
            ("PUT", f"{API}/system/timezone"),
            ("GET", f"{API}/audit/logs"),
            ("GET", f"{API}/security/firewall"),
            ("GET", f"{API}/health/full"),
            # The trash of the file manager (ADR-0259): administrator-only with the files router.
            ("GET", f"{API}/files/trash"),
            ("POST", f"{API}/files/trash/restore"),
            ("DELETE", f"{API}/files/trash"),
            ("POST", f"{API}/files/trash/empty"),
            # Importing a pool found on the disks (ADR-0283): administrator-only with the storage router.
            ("GET", f"{API}/storage/filesystems/importable"),
            ("POST", f"{API}/storage/filesystems/{{uuid}}/import"),
        } <= operations

    def test_websocket_routes_are_known(self):
        websockets = {
            effective.path
            for original, effective in _route_contexts()
            if isinstance(original, APIWebSocketRoute)
        }
        assert websockets == WEBSOCKETS

    def test_every_route_has_an_explicit_authorization_decision(self):
        problems = []
        for method, path, dependant in _http_operations():
            calls = _dependency_calls(dependant)
            key = (method, path)
            if key in PUBLIC:
                if verify_token in calls:
                    problems.append(f"{method} {path}: listed as PUBLIC but requires a login")
            elif key in USER_ALLOWED:
                if verify_token not in calls:
                    problems.append(f"{method} {path}: listed in USER_ALLOWED but requires no login")
                if require_admin in calls:
                    problems.append(f"{method} {path}: listed in USER_ALLOWED but requires an admin")
            elif require_admin not in calls:
                problems.append(
                    f"{method} {path}: neither PUBLIC nor USER_ALLOWED, so it must depend on require_admin"
                )
        assert problems == []

    def test_anonymous_requests_are_rejected(self, client):
        problems = []
        for method, path, _ in _http_operations():
            response = _call(client, method, path)
            if (method, path) in PUBLIC:
                if response.status_code in (401, 403):
                    problems.append(f"{method} {path}: public route returned {response.status_code}")
            elif response.status_code not in (401, 403):
                problems.append(f"{method} {path}: anonymous request got {response.status_code}")
        assert problems == []

    def test_regular_user_only_reaches_user_routes(self, client, authz_db):
        problems = []
        for method, path, _ in _http_operations():
            if (method, path) in PUBLIC:
                continue
            # A session of its own for each route: signing out ends the one it is called with.
            response = _call(client, method, path, _auth(session_token(authz_db.engine, "alice")))
            if (method, path) in USER_ALLOWED:
                if response.status_code in (401, 403):
                    problems.append(f"{method} {path}: user route returned {response.status_code}")
            elif response.status_code != 403:
                problems.append(f"{method} {path}: regular user got {response.status_code}, expected 403")
        assert problems == []

    def test_a_user_without_smb_reaches_the_smb_routes_without_host_commands(self, client, alice_headers):
        """alice has no SMB account row: her three SMB routes answer from the
        database only (no_side_effects makes any host command fail the request)."""
        status = client.get(f"{API}/auth/me/smb", headers=alice_headers)
        assert status.status_code == 200, status.text
        assert status.json() == {
            "account": {"state": "off", "allowed": False, "linux_name": None, "uid": None,
                        "password_set_at": None, "reason": None},
            "totp_required": False,
            "shares": [],
        }

        disabled = client.post(f"{API}/auth/me/smb/disable", headers=alice_headers)
        assert disabled.status_code == 200, disabled.text
        assert disabled.json()["account"]["state"] == "off"
        assert disabled.json()["warnings"] == []

        # Not allowed by an administrator: refused before any host command.
        refused = client.post(
            f"{API}/auth/me/smb/enable", json={"current_password": PASSWORD}, headers=alice_headers
        )
        assert refused.status_code == 403, refused.text
        assert refused.json()["detail"] == "An administrator must allow SMB access for your account first"

    def test_regular_users_cannot_manage_smb_of_others(self, client, authz_db, alice_headers, bob_headers):
        assert client.post(
            f"{API}/auth/users/{authz_db.bob_id}/smb/enable", json={"username": "bob"}, headers=alice_headers
        ).status_code == 403
        assert client.post(
            f"{API}/auth/users/{authz_db.bob_id}/smb/disable", json={"username": "bob"}, headers=alice_headers
        ).status_code == 403
        # Not even for themselves.
        assert client.post(
            f"{API}/auth/users/{authz_db.alice_id}/smb/enable", json={"username": "alice"}, headers=alice_headers
        ).status_code == 403

    @pytest.mark.parametrize(
        "method,path",
        [
            ("GET", f"{API}/auth/users"),
            ("GET", f"{API}/audit/actions"),
            ("GET", f"{API}/security/firewall"),
            ("GET", f"{API}/security/certificates/status"),
        ],
    )
    def test_admin_passes_the_admin_check(self, client, admin_headers, method, path):
        assert _call(client, method, path, admin_headers).status_code == 200

    def test_api_token_of_a_regular_user_is_not_admin(self, client, alice_headers):
        created = client.post(f"{API}/auth/tokens", json={"name": "cli"}, headers=alice_headers)
        assert created.status_code == 200
        token_headers = _auth(created.json()["token"])

        assert client.get(f"{API}/auth/me", headers=token_headers).json()["username"] == "alice"
        assert client.get(f"{API}/auth/users", headers=token_headers).status_code == 403
        assert client.get(f"{API}/storage/pools", headers=token_headers).status_code == 403


class TestUploadsReadTheirBodyAfterTheCredentialsAreChecked:
    """FastAPI reads the form of an UploadFile parameter before it runs the
    dependencies that check the credentials; the uploads read it themselves
    (src/request_limits.py), so a refused client gets nothing stored."""

    # {pool}: a folder of a pool (body_reads); the file upload checks it before it reads the body.
    UPLOADS = [f"{API}/backup/upload", f"{API}/files/upload?path={{pool}}"]

    @pytest.fixture
    def body_reads(self, monkeypatch, tmp_path):
        """Record (and refuse) every attempt to read a request body."""
        reads = []

        def refuse(name):
            def read(self, *args, **kwargs):
                reads.append(name)
                raise RuntimeError(f"request.{name}() was called")

            return read

        for name in ("body", "form", "stream"):
            monkeypatch.setattr(Request, name, refuse(name))
        # The modules, not the APIRouters that src.backup and src.files export under the same name.
        monkeypatch.setattr(importlib.import_module("src.backup.router"), "BACKUP_DIR", tmp_path / "backups")
        (tmp_path / "pools" / "data").mkdir(parents=True)
        monkeypatch.setattr(importlib.import_module("src.files.router"), "BASE_PATH", str(tmp_path / "pools"))
        return reads

    @pytest.mark.parametrize("url", UPLOADS)
    def test_anonymous_and_regular_users_are_refused_before_the_body_is_read(
        self, client, body_reads, alice_headers, tmp_path, url
    ):
        url = url.format(pool=tmp_path / "pools" / "data")
        files = {"file": ("x.tar.gz", b"x" * 4096)}

        assert client.post(url, files=files).status_code == 401
        assert client.post(url, files=files, headers=alice_headers).status_code == 403
        assert body_reads == []

    @pytest.mark.parametrize("url", UPLOADS)
    def test_an_administrator_upload_is_read(self, client, body_reads, admin_headers, tmp_path, url):
        """The control: a read of the body would be seen."""
        url = url.format(pool=tmp_path / "pools" / "data")
        response = client.post(url, files={"file": ("x.tar.gz", b"x")}, headers=admin_headers)

        assert response.status_code == 500
        assert body_reads == ["stream"]


class TestUserManagement:
    def test_regular_user_cannot_manage_users(self, client, authz_db, alice_headers):
        assert client.get(f"{API}/auth/users", headers=alice_headers).status_code == 403
        assert client.post(
            f"{API}/auth/users", json={"username": "mallory", "password": "pw"}, headers=alice_headers
        ).status_code == 403
        # No self-promotion.
        assert client.put(
            f"{API}/auth/users/{authz_db.alice_id}", json={"is_admin": True}, headers=alice_headers
        ).status_code == 403
        assert client.delete(
            f"{API}/auth/users/{authz_db.admin_id}", params={"username": "admin"}, headers=alice_headers
        ).status_code == 403
        with Session(authz_db.engine) as session:
            assert session.get(User, authz_db.alice_id).is_admin is False
            assert session.get(User, authz_db.admin_id) is not None

    def test_last_admin_cannot_demote_themselves(self, client, authz_db, admin_headers):
        response = client.put(
            f"{API}/auth/users/{authz_db.admin_id}", json={"is_admin": False}, headers=admin_headers
        )

        assert response.status_code == 409
        with Session(authz_db.engine) as session:
            assert session.get(User, authz_db.admin_id).is_admin is True

    def test_last_admin_cannot_delete_themselves(self, client, authz_db, admin_headers):
        response = client.delete(
            f"{API}/auth/users/{authz_db.admin_id}", params={"username": "admin"}, headers=admin_headers
        )

        assert response.status_code == 409
        with Session(authz_db.engine) as session:
            assert session.get(User, authz_db.admin_id) is not None

    def test_admins_can_be_demoted_while_another_remains(self, client, authz_db, admin_headers):
        with Session(authz_db.engine) as session:
            second = User(username="admin2", hashed_password=_PASSWORD_HASH, is_admin=True)
            session.add(second)
            session.commit()
            second_id = second.id

        demoted = client.put(f"{API}/auth/users/{second_id}", json={"is_admin": False}, headers=admin_headers)
        assert demoted.status_code == 200
        assert demoted.json()["is_admin"] is False
        # Now the caller is the last administrator again.
        assert client.put(
            f"{API}/auth/users/{authz_db.admin_id}", json={"is_admin": False}, headers=admin_headers
        ).status_code == 409

    def test_admin_cannot_delete_own_account_even_with_other_admins(self, client, authz_db, admin_headers):
        with Session(authz_db.engine) as session:
            session.add(User(username="admin2", hashed_password=_PASSWORD_HASH, is_admin=True))
            session.commit()

        response = client.delete(
            f"{API}/auth/users/{authz_db.admin_id}", params={"username": "admin"}, headers=admin_headers
        )
        assert response.status_code == 400

    def test_admin_can_delete_another_admin_while_one_remains(self, client, authz_db, admin_headers):
        with Session(authz_db.engine) as session:
            second = User(username="admin2", hashed_password=_PASSWORD_HASH, is_admin=True)
            session.add(second)
            session.commit()
            second_id = second.id

        assert client.delete(
            f"{API}/auth/users/{second_id}", params={"username": "admin2"}, headers=admin_headers
        ).status_code == 200

    def test_a_delete_names_the_user(self, client, authz_db, admin_headers):
        """users.id is a rowid that SQLite reuses: DELETE carries the username it was built for."""
        url = f"{API}/auth/users/{authz_db.bob_id}"

        missing = client.delete(url, headers=admin_headers)
        stale = client.delete(url, params={"username": "carol"}, headers=admin_headers)

        assert missing.status_code == 422
        assert (stale.status_code, stale.json()["detail"]) == (
            409, f"User {authz_db.bob_id} is now 'bob'; reload the page"
        )
        with Session(authz_db.engine) as session:
            assert session.get(User, authz_db.bob_id) is not None
        assert client.delete(url, params={"username": "bob"}, headers=admin_headers).status_code == 200
        with Session(authz_db.engine) as session:
            assert session.get(User, authz_db.bob_id) is None

    @pytest.mark.parametrize("body, status", [
        pytest.param(None, 422, id="no-body"),
        pytest.param({}, 422, id="no-username"),
        pytest.param({"username": "bob", "enabled": False}, 422, id="extra-field"),
        pytest.param({"username": "carol"}, 409, id="stale-username"),
    ])
    def test_an_smb_disable_names_the_user(self, client, authz_db, admin_headers, body, status):
        response = client.post(f"{API}/auth/users/{authz_db.bob_id}/smb/disable", json=body, headers=admin_headers)

        assert response.status_code == status, response.text
        if status == 409:
            assert response.json()["detail"] == f"User {authz_db.bob_id} is now 'bob'; reload the page"

    def test_new_passwords_longer_than_bcrypt_limit_are_rejected(self, client, admin_headers, alice_headers):
        too_long = "a" * 73
        assert client.post(
            f"{API}/auth/users", json={"username": "carol", "password": too_long}, headers=admin_headers
        ).status_code == 422
        assert client.post(
            f"{API}/auth/change-password",
            json={"current_password": PASSWORD, "new_password": too_long},
            headers=alice_headers,
        ).status_code == 422


class TestApiTokenOwnership:
    def test_only_the_owner_can_see_and_change_a_token(self, client, alice_headers, bob_headers):
        created = client.post(f"{API}/auth/tokens", json={"name": "backup-script"}, headers=alice_headers)
        assert created.status_code == 200
        token_id = created.json()["id"]

        assert client.get(f"{API}/auth/tokens", headers=bob_headers).json() == []
        assert client.post(f"{API}/auth/tokens/{token_id}/toggle", headers=bob_headers).status_code == 404
        assert client.delete(f"{API}/auth/tokens/{token_id}", headers=bob_headers).status_code == 404

        assert [t["id"] for t in client.get(f"{API}/auth/tokens", headers=alice_headers).json()] == [token_id]
        toggled = client.post(f"{API}/auth/tokens/{token_id}/toggle", headers=alice_headers)
        assert toggled.status_code == 200 and toggled.json()["is_active"] is False
        assert client.delete(f"{API}/auth/tokens/{token_id}", headers=alice_headers).status_code == 200

    def test_admin_cannot_touch_other_users_tokens_either(self, client, authz_db, admin_headers, alice_headers):
        token_id = client.post(f"{API}/auth/tokens", json={"name": "t"}, headers=alice_headers).json()["id"]

        assert client.delete(f"{API}/auth/tokens/{token_id}", headers=admin_headers).status_code == 404
        with Session(authz_db.engine) as session:
            assert session.get(APIToken, token_id) is not None


class TestApiTokenUse:
    """A token is answered once and acts with its owner's rights until it is disabled or expires
    (feature auth.api-tokens; ADR-0239)."""

    @staticmethod
    def _create(client, headers, **body) -> dict:
        response = client.post(f"{API}/auth/tokens", json={"name": "backup-script", **body}, headers=headers)
        assert response.status_code == 200, response.text
        return response.json()

    def test_the_token_is_answered_once_and_kept_as_its_hash(self, client, authz_db, alice_headers):
        created = self._create(client, alice_headers, expires_in_days=30)
        token = created["token"]
        token_hash = hashlib.sha256(token.encode()).hexdigest()

        assert token.startswith("unos_") and created["token_prefix"] == token[:12]
        listed = client.get(f"{API}/auth/tokens", headers=alice_headers)
        [entry] = listed.json()
        assert entry["id"] == created["id"] and "token" not in entry
        assert token not in listed.text and token_hash not in listed.text
        with Session(authz_db.engine) as session:
            [stored] = session.scalars(select(APIToken)).all()
            assert stored.token_hash == token_hash
            assert token not in (stored.name, stored.token_prefix, stored.permissions)

    def test_an_administrators_token_has_the_administrators_rights(self, client, admin_headers):
        token_headers = _auth(self._create(client, admin_headers)["token"])

        assert client.get(f"{API}/auth/me", headers=token_headers).json()["username"] == "admin"
        assert client.get(f"{API}/auth/users", headers=token_headers).status_code == 200

    def test_a_disabled_token_answers_401_until_it_is_enabled_again(self, client, alice_headers):
        created = self._create(client, alice_headers)
        token_headers = _auth(created["token"])
        toggle = f"{API}/auth/tokens/{created['id']}/toggle"
        assert client.get(f"{API}/auth/me", headers=token_headers).status_code == 200

        disabled = client.post(toggle, headers=alice_headers)

        assert (disabled.status_code, disabled.json()["is_active"]) == (200, False)
        for method, path in (("GET", f"{API}/auth/me"), ("GET", f"{API}/auth/tokens"), ("POST", toggle)):
            refused = client.request(method, path, headers=token_headers)
            assert (refused.status_code, refused.json()["detail"]) == (401, "API token is disabled"), path
        enabled = client.post(toggle, headers=alice_headers)
        assert (enabled.status_code, enabled.json()["is_active"]) == (200, True)
        assert client.get(f"{API}/auth/me", headers=token_headers).status_code == 200

    def test_an_expired_token_answers_401(self, client, authz_db, alice_headers):
        created = self._create(client, alice_headers, expires_in_days=1)
        token_headers = _auth(created["token"])
        assert client.get(f"{API}/auth/me", headers=token_headers).status_code == 200
        with Session(authz_db.engine) as session:
            session.get(APIToken, created["id"]).expires_at = datetime.utcnow() - timedelta(seconds=1)
            session.commit()

        refused = client.get(f"{API}/auth/me", headers=token_headers)

        assert (refused.status_code, refused.json()["detail"]) == (401, "API token has expired")

    def test_a_token_works_until_its_lifetime_is_over(self, client, alice_headers, monkeypatch):
        created = self._create(client, alice_headers, expires_in_days=1)
        expires_at = datetime.fromisoformat(created["expires_at"])
        token_headers = _auth(created["token"])

        monkeypatch.setattr(auth_utils, "_token_time", lambda: expires_at - timedelta(seconds=1))
        assert client.get(f"{API}/auth/me", headers=token_headers).status_code == 200
        monkeypatch.setattr(auth_utils, "_token_time", lambda: expires_at + timedelta(seconds=1))
        refused = client.get(f"{API}/auth/me", headers=token_headers)

        assert (refused.status_code, refused.json()["detail"]) == (401, "API token has expired")

    def test_others_reach_only_their_own_tokens(self, client, authz_db, alice_headers, bob_headers, admin_headers):
        alices = self._create(client, alice_headers)
        bobs_token = _auth(self._create(client, bob_headers)["token"])

        for others in (bob_headers, bobs_token, admin_headers):
            listed = client.get(f"{API}/auth/tokens", headers=others)
            assert listed.status_code == 200
            assert alices["id"] not in [t["id"] for t in listed.json()]
            # A session gets 404, as for a token that does not exist; an API token is refused
            # before that (ADR-0253).
            refused = 403 if others is bobs_token else 404
            assert client.post(f"{API}/auth/tokens/{alices['id']}/toggle", headers=others).status_code == refused
            assert client.delete(f"{API}/auth/tokens/{alices['id']}", headers=others).status_code == refused
        with Session(authz_db.engine) as session:
            assert session.get(APIToken, alices["id"]).is_active is True
        assert client.get(f"{API}/auth/me", headers=_auth(alices["token"])).status_code == 200


class TestApiTokensAreManagedWithASession:
    """Only a session token creates, toggles and deletes API tokens: tokens made with a token
    would outlast its lifetime and its revocation (ADR-0253)."""

    def test_a_token_cannot_create_a_token(self, client, authz_db, alice_headers):
        """A token of one day that leaked cannot make one that never expires."""
        leaked = TestApiTokenUse._create(client, alice_headers, expires_in_days=1)

        refused = client.post(f"{API}/auth/tokens", json={"name": "script"}, headers=_auth(leaked["token"]))

        assert (refused.status_code, refused.json()["detail"]) == (403, SESSION_NEEDED)
        assert "unos_" not in refused.text
        with Session(authz_db.engine) as session:
            assert [t.id for t in session.scalars(select(APIToken))] == [leaked["id"]]

    def test_a_token_cannot_toggle_or_delete_a_token(self, client, authz_db, alice_headers):
        own = TestApiTokenUse._create(client, alice_headers)
        other = TestApiTokenUse._create(client, alice_headers)
        token_headers = _auth(own["token"])

        for target in (other, own):
            for method, path in (("POST", f"{API}/auth/tokens/{target['id']}/toggle"),
                                 ("DELETE", f"{API}/auth/tokens/{target['id']}")):
                refused = client.request(method, path, headers=token_headers)
                assert (refused.status_code, refused.json()["detail"]) == (403, SESSION_NEEDED), (method, path)

        with Session(authz_db.engine) as session:
            stored = session.scalars(select(APIToken).order_by(APIToken.id)).all()
            assert [(t.id, t.is_active) for t in stored] == [(own["id"], True), (other["id"], True)]
        # The token still lists them, and still acts with its owner's rights elsewhere.
        listed = client.get(f"{API}/auth/tokens", headers=token_headers)
        assert sorted(t["id"] for t in listed.json()) == [own["id"], other["id"]]
        assert client.get(f"{API}/auth/me", headers=token_headers).json()["username"] == "alice"

    def test_an_administrators_token_cannot_create_one_either(self, client, admin_headers):
        token_headers = _auth(TestApiTokenUse._create(client, admin_headers)["token"])

        refused = client.post(f"{API}/auth/tokens", json={"name": "script"}, headers=token_headers)

        assert (refused.status_code, refused.json()["detail"]) == (403, SESSION_NEEDED)
        assert client.get(f"{API}/auth/users", headers=token_headers).status_code == 200

    def test_a_token_that_is_not_valid_keeps_its_401(self, client, authz_db, alice_headers):
        created = TestApiTokenUse._create(client, alice_headers)
        client.post(f"{API}/auth/tokens/{created['id']}/toggle", headers=alice_headers)

        for headers, detail in ((_auth(created["token"]), "API token is disabled"),
                                (_auth("unos_" + "x" * 43), "Invalid API token")):
            refused = client.post(f"{API}/auth/tokens", json={"name": "script"}, headers=headers)
            assert (refused.status_code, refused.json()["detail"]) == (401, detail)


class TestApiTokenBounds:
    """Each token is a row of the database on the system disk, which any signed-in user can add
    to: a name has 1 to 64 characters, the permissions are "*", and a user has at most 100
    tokens (ADR-0253)."""

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"name": ""},
            {"name": "   "},
            {"name": "x" * 65},
            {"name": "x" * 900_000},  # under the request limit of 1 MiB (ADR-0062)
            {"name": "t", "permissions": "read"},
            {"name": "t", "permissions": "x" * 900_000},
        ],
        ids=["no-name", "empty", "blank", "65-characters", "900000-characters", "read", "huge-permissions"],
    )
    def test_a_name_or_permissions_out_of_bounds_answer_422(self, client, authz_db, alice_headers, body):
        response = client.post(f"{API}/auth/tokens", json=body, headers=alice_headers)

        assert response.status_code == 422
        assert len(response.content) < 2048  # the 422 handler does not echo the values
        with Session(authz_db.engine) as session:
            assert session.scalars(select(APIToken)).all() == []

    def test_a_name_is_kept_without_the_space_around_it(self, client, alice_headers):
        name = "n" * API_TOKEN_NAME_CHARS

        created = client.post(f"{API}/auth/tokens", json={"name": f"  {name} ", "permissions": "*"}, headers=alice_headers)

        assert created.status_code == 200, created.text
        assert (created.json()["name"], created.json()["permissions"]) == (name, "*")
        assert [t["name"] for t in client.get(f"{API}/auth/tokens", headers=alice_headers).json()] == [name]

    @staticmethod
    def _tokens_of(authz_db, user_id: int) -> int:
        with Session(authz_db.engine) as session:
            return session.scalar(select(func.count()).select_from(APIToken).where(APIToken.user_id == user_id))

    def test_a_user_has_at_most_100_tokens(self, client, authz_db, alice_headers, bob_headers):
        with Session(authz_db.engine) as session:
            session.add_all(
                APIToken(name=f"t{i}", token_hash=f"hash-{i}", token_prefix="unos_t", user_id=authz_db.alice_id)
                for i in range(MAX_API_TOKENS_PER_USER - 1)
            )
            session.commit()

        last = client.post(f"{API}/auth/tokens", json={"name": "the 100th"}, headers=alice_headers)
        refused = client.post(f"{API}/auth/tokens", json={"name": "the 101st"}, headers=alice_headers)

        assert last.status_code == 200, last.text
        assert (refused.status_code, refused.json()["detail"]) == (409, TOO_MANY_API_TOKENS)
        assert self._tokens_of(authz_db, authz_db.alice_id) == MAX_API_TOKENS_PER_USER
        # The limit is each user's own.
        assert client.post(f"{API}/auth/tokens", json={"name": "bobs"}, headers=bob_headers).status_code == 200
        # Deleting one makes room for one.
        assert client.delete(f"{API}/auth/tokens/{last.json()['id']}", headers=alice_headers).status_code == 200
        assert client.post(f"{API}/auth/tokens", json={"name": "again"}, headers=alice_headers).status_code == 200
        assert self._tokens_of(authz_db, authz_db.alice_id) == MAX_API_TOKENS_PER_USER

    def test_two_requests_at_the_same_moment_cannot_both_take_the_last_place(self, authz_db, alice_headers):
        """The count follows the insert, which holds SQLite's write lock until the commit."""
        with Session(authz_db.engine) as session:
            session.add_all(
                APIToken(name=f"t{i}", token_hash=f"hash-{i}", token_prefix="unos_t", user_id=authz_db.alice_id)
                for i in range(MAX_API_TOKENS_PER_USER - 1)
            )
            session.commit()

        async def both() -> list[int]:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as async_client:
                answers = await asyncio.gather(*(
                    async_client.post(f"{API}/auth/tokens", json={"name": name}, headers=alice_headers)
                    for name in ("first", "second")
                ))
            return sorted(answer.status_code for answer in answers)

        assert asyncio.run(both()) == [200, 409]
        assert self._tokens_of(authz_db, authz_db.alice_id) == MAX_API_TOKENS_PER_USER


class TestApiTokenExpiry:
    @pytest.mark.parametrize("days", [0, -1, 3651, 10**8])
    def test_out_of_range_expiry_is_rejected(self, client, authz_db, alice_headers, days):
        response = client.post(
            f"{API}/auth/tokens", json={"name": "t", "expires_in_days": days}, headers=alice_headers
        )

        assert response.status_code == 422
        with Session(authz_db.engine) as session:
            assert session.scalars(select(APIToken)).all() == []

    @pytest.mark.parametrize("days", [1, 3650])
    def test_expiry_within_range_is_set(self, client, alice_headers, days):
        response = client.post(
            f"{API}/auth/tokens", json={"name": "t", "expires_in_days": days}, headers=alice_headers
        )

        assert response.status_code == 200, response.text
        expires_at = datetime.fromisoformat(response.json()["expires_at"])
        assert abs(expires_at - (datetime.utcnow() + timedelta(days=days))) < timedelta(minutes=1)

    @pytest.mark.parametrize("body", [{"name": "t"}, {"name": "t", "expires_in_days": None}])
    def test_no_expiry_by_default(self, client, alice_headers, body):
        response = client.post(f"{API}/auth/tokens", json=body, headers=alice_headers)

        assert response.status_code == 200
        assert response.json()["expires_at"] is None


class TestLoginHistory:
    @pytest.fixture
    def attempts(self, authz_db):
        with Session(authz_db.engine) as session:
            session.add_all([
                LoginAttempt(username="alice", ip_address="10.0.0.2", success=True),
                LoginAttempt(username="bob", ip_address="10.0.0.3", success=False),
                LoginAttempt(username="admin", ip_address="10.0.0.1", success=True),
            ])
            session.commit()

    def test_users_see_only_their_own_attempts(self, client, attempts, alice_headers):
        own = client.get(f"{API}/security/login-history", headers=alice_headers).json()
        filtered = client.get(f"{API}/security/login-history?username=bob", headers=alice_headers).json()

        assert {a["username"] for a in own["attempts"]} == {"alice"}
        assert {a["username"] for a in filtered["attempts"]} == {"alice"}
        assert own["total"] == 1

    def test_admins_see_everyone(self, client, attempts, admin_headers):
        everyone = client.get(f"{API}/security/login-history", headers=admin_headers).json()
        bob_only = client.get(f"{API}/security/login-history?username=bob", headers=admin_headers).json()

        assert {a["username"] for a in everyone["attempts"]} == {"alice", "bob", "admin"}
        assert {a["username"] for a in bob_only["attempts"]} == {"bob"}

    def test_only_admins_clear_history(self, client, attempts, alice_headers, admin_headers):
        assert client.delete(f"{API}/security/login-history", headers=alice_headers).status_code == 403
        assert client.delete(
            f"{API}/security/login-history?older_than_days=0", headers=admin_headers
        ).status_code == 200

    def test_clearing_counts_the_days_in_utc(self, client, authz_db, admin_headers):
        # The stored times are UTC; the server's own time zone must not move the cutoff (ADR-0262).
        try:
            with pytest.MonkeyPatch.context() as zone:
                zone.setenv("TZ", "Asia/Kathmandu")  # UTC+05:45
                time.tzset()
                now = datetime.now(timezone.utc).replace(tzinfo=None)
                with Session(authz_db.engine) as session:
                    session.add(LoginAttempt(username="alice", success=True,
                                             timestamp=now - timedelta(days=30, hours=-1)))
                    session.add(LoginAttempt(username="bob", success=True, timestamp=now - timedelta(days=30, hours=1)))
                    session.commit()

                response = client.delete(f"{API}/security/login-history?older_than_days=30", headers=admin_headers)
        finally:
            time.tzset()  # the zone of the test run again

        assert response.json() == {"status": "ok", "deleted_count": 1}
        with Session(authz_db.engine) as session:
            assert [a.username for a in session.scalars(select(LoginAttempt))] == ["alice"]

    def test_times_are_answered_in_utc_with_their_offset(self, client, alice_headers):
        # SQLite stores CURRENT_TIMESTAMP in UTC, to the second. Without an offset, a browser
        # reads the time as its own local time (ADR-0262).
        before = datetime.now(timezone.utc).replace(microsecond=0)
        assert client.post(f"{API}/auth/login", json={"username": "alice", "password": PASSWORD}).status_code == 200
        after = datetime.now(timezone.utc)

        [attempt] = client.get(f"{API}/security/login-history", headers=alice_headers).json()["attempts"]

        stamp = datetime.fromisoformat(attempt["timestamp"])
        assert stamp.utcoffset() == timedelta(0), attempt["timestamp"]
        assert before <= stamp <= after

    def test_newest_first_also_within_one_second(self, client, authz_db, alice_headers):
        # Times have whole seconds: a failed sign-in and the right one right after it share one.
        second = datetime(2026, 11, 7, 12, 0, 0)
        with Session(authz_db.engine) as session:
            # Her account is older than these attempts: a user's history starts with the account (ADR-0263).
            session.get(User, authz_db.alice_id).created_at = second - timedelta(days=1)
            session.add(LoginAttempt(username="alice", success=False, failure_reason="Incorrect username or password",
                                     timestamp=second))
            session.add(LoginAttempt(username="alice", success=True, timestamp=second))
            session.commit()

        attempts = client.get(f"{API}/security/login-history", headers=alice_headers).json()["attempts"]

        assert [(a["success"], a["timestamp"]) for a in attempts] == [
            (True, "2026-11-07T12:00:00Z"),
            (False, "2026-11-07T12:00:00Z"),
        ]

    def test_a_user_created_again_with_an_old_name_sees_only_their_own_attempts(
        self, client, authz_db, admin_headers
    ):
        # The history is kept by name: attempts on "bob" before his new account, a sign-in of the bob
        # deleted below two days ago and a failed attempt a second ago. The administrators still see
        # them; the new bob's history starts with his account (ADR-0263).
        now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
        with Session(authz_db.engine) as session:
            session.add_all([
                LoginAttempt(username="bob", ip_address="203.0.113.7", success=True, timestamp=now - timedelta(days=2)),
                LoginAttempt(username="bob", ip_address="203.0.113.8", success=False,
                             failure_reason="Incorrect username or password", timestamp=now - timedelta(seconds=1)),
            ])
            session.commit()
        assert client.delete(
            f"{API}/auth/users/{authz_db.bob_id}", params={"username": "bob"}, headers=admin_headers
        ).status_code == 200
        created = client.post(f"{API}/auth/users", json={"username": "bob", "password": PASSWORD}, headers=admin_headers)
        assert created.status_code == 200, created.text
        # His first sign-in, in the second of his account's creation or later.
        login = client.post(f"{API}/auth/login", json={"username": "bob", "password": PASSWORD})
        assert login.status_code == 200, login.text

        own = client.get(f"{API}/security/login-history", headers=_auth(login.json()["access_token"])).json()
        failed = client.get(
            f"{API}/security/login-history?success=false", headers=_auth(login.json()["access_token"])
        ).json()
        everyone = client.get(f"{API}/security/login-history?username=bob", headers=admin_headers).json()

        assert [(a["success"], a["ip_address"]) for a in own["attempts"]] == [(True, "testclient")]
        assert (own["total"], own["success_count"], own["failed_count"]) == (1, 1, 0)
        assert (failed["attempts"], failed["total"]) == ([], 1)
        assert [a["ip_address"] for a in everyone["attempts"]] == ["testclient", "203.0.113.8", "203.0.113.7"]
        assert (everyone["total"], everyone["failed_count"]) == (3, 1)

    @pytest.mark.parametrize("days", [-1, 36501, 800000])
    def test_clearing_refuses_a_period_out_of_range(self, client, authz_db, attempts, admin_headers, days):
        # A negative period moved the cutoff into the future and deleted every row; 800000 days
        # back is before the year 1 (a 500).
        response = client.delete(f"{API}/security/login-history?older_than_days={days}", headers=admin_headers)

        assert response.status_code == 422, response.text
        with Session(authz_db.engine) as session:
            assert len(session.scalars(select(LoginAttempt)).all()) == 3

    def test_clearing_counts_back_up_to_100_years(self, client, attempts, admin_headers):
        response = client.delete(f"{API}/security/login-history?older_than_days=36500", headers=admin_headers)

        assert response.json() == {"status": "ok", "deleted_count": 0}


def _compose(name):
    return yaml.safe_load((REPO_ROOT / "deploy" / name).read_text())


def _environment(service):
    env = service.get("environment") or {}
    if isinstance(env, list):
        env = dict(item.split("=", 1) for item in env)
    return env


def _uvicorn_args(dockerfile):
    """The uvicorn command line of a supervisor image."""
    text = (REPO_ROOT / "supervisor" / dockerfile).read_text()
    line = next(line for line in text.splitlines() if line.startswith(("CMD ", "ENTRYPOINT ")))
    args = json.loads(line.split(" ", 1)[1])
    if args[:2] == ["/bin/bash", "-c"]:  # Dockerfile.test: "setup && exec uvicorn ..."
        args = shlex.split(args[2].split("exec ", 1)[1])
    return args


def _proxy_layout(compose_file, network):
    compose = _compose(compose_file)
    supervisor, webui = compose["services"]["supervisor"], compose["services"]["webui"]
    network_config = compose["networks"][network] or {}
    assert "ipam" in network_config, f"{compose_file}: {network} needs a fixed subnet for webui's address"
    ipam = network_config["ipam"]["config"][0]
    return SimpleNamespace(
        trusted=_environment(supervisor).get("FORWARDED_ALLOW_IPS"),
        webui=webui["networks"][network]["ipv4_address"],
        supervisor=supervisor["networks"][network]["ipv4_address"],
        subnet=ipaddress.ip_network(ipam["subnet"]),
        gateway=ipam["gateway"],
    )


class TestRealClientAddress:
    """The supervisor only sees Caddy; the client address comes from X-Forwarded-For.

    Uvicorn believes that header only on connections from FORWARDED_ALLOW_IPS,
    which docker-compose.yml sets to the webui container's fixed address.
    Anything else that reaches port 8080 directly (host processes and
    host-network apps, which connect from the network's gateway address) is
    identified by its real address.
    """

    @pytest.fixture
    def deployment(self):
        return _proxy_layout("docker-compose.yml", "unos-net")

    @staticmethod
    def _connect(deployment, peer):
        from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

        # What `uvicorn --proxy-headers` wraps around the app, with the
        # FORWARDED_ALLOW_IPS of the deployment.
        return TestClient(ProxyHeadersMiddleware(app, trusted_hosts=deployment.trusted), client=(peer, 40000))

    @pytest.fixture
    def proxied_client(self, authz_db, deployment):
        return self._connect(deployment, deployment.webui)

    @pytest.fixture
    def direct_client(self, authz_db, deployment):
        return self._connect(deployment, deployment.gateway)

    def _login(self, client, ip, password="wrong-password"):
        return client.post(
            f"{API}/auth/login",
            json={"username": "alice", "password": password},
            headers={"X-Forwarded-For": ip},
        )

    def test_login_attempt_and_audit_entry_record_the_browser_address(self, proxied_client, authz_db, tmp_path):
        assert self._login(proxied_client, "203.0.113.7").status_code == 401
        assert self._login(proxied_client, "198.51.100.4", PASSWORD).status_code == 200

        with Session(authz_db.engine) as session:
            ips = [a.ip_address for a in session.scalars(select(LoginAttempt).order_by(LoginAttempt.id))]
        assert ips == ["203.0.113.7", "198.51.100.4"]
        audit = audit_entries(tmp_path / "audit.jsonl")
        assert [entry["ip_address"] for entry in audit] == ["203.0.113.7", "198.51.100.4"]

    def test_the_login_history_lists_the_browser_address_and_the_failed_attempts(
        self, proxied_client, alice_headers
    ):
        assert self._login(proxied_client, "203.0.113.7").status_code == 401
        assert self._login(proxied_client, "198.51.100.4", PASSWORD).status_code == 200

        # What System > Security > Login History shows alice, newest first.
        history = proxied_client.get(
            f"{API}/security/login-history", headers={**alice_headers, "X-Forwarded-For": "192.0.2.1"}
        ).json()

        assert [(a["ip_address"], a["success"], a["failure_reason"]) for a in history["attempts"]] == [
            ("198.51.100.4", True, None),
            ("203.0.113.7", False, "Incorrect username or password"),
        ]
        assert (history["total"], history["success_count"], history["failed_count"]) == (2, 1, 1)

    def test_forwarded_header_is_not_trusted_without_the_proxy_layer(self, client, authz_db):
        """App code must not parse X-Forwarded-For itself: only uvicorn's trusted-proxy check may."""
        response = client.post(
            f"{API}/auth/login",
            json={"username": "alice", "password": "wrong-password"},
            headers={"X-Forwarded-For": "203.0.113.99"},
        )
        assert response.status_code == 401

        with Session(authz_db.engine) as session:
            attempt = session.scalars(select(LoginAttempt)).one()
        assert attempt.ip_address == "testclient"

    def test_login_rate_limit_is_per_client_not_global(self, proxied_client):
        assert [self._login(proxied_client, "203.0.113.7").status_code for _ in range(5)] == [401] * 5
        assert self._login(proxied_client, "203.0.113.7").status_code == 429
        # Another browser behind the same proxy is not locked out: it signs in as the same user.
        assert self._login(proxied_client, "198.51.100.4", PASSWORD).status_code == 200
        assert self._login(proxied_client, "203.0.113.7", PASSWORD).status_code == 429

    def test_direct_connections_cannot_choose_their_address(self, direct_client, deployment, authz_db, tmp_path):
        """A new X-Forwarded-For per attempt must not mean a new rate-limit bucket."""
        codes = [self._login(direct_client, f"198.51.100.{n}").status_code for n in range(6)]
        assert codes == [401] * 5 + [429]

        with Session(authz_db.engine) as session:
            ips = {a.ip_address for a in session.scalars(select(LoginAttempt))}
        assert ips == {deployment.gateway}
        audit = audit_entries(tmp_path / "audit.jsonl")
        assert {entry["ip_address"] for entry in audit} == {deployment.gateway}

    @pytest.mark.parametrize("dockerfile, compose_file, network", [
        ("Dockerfile", "docker-compose.yml", "unos-net"),
        ("Dockerfile.test", "docker-compose.test.yml", "unos-test-net"),
    ])
    def test_only_the_webui_container_may_set_the_client_address(self, dockerfile, compose_file, network):
        args = _uvicorn_args(dockerfile)
        assert args[:2] == ["uvicorn", "src.main:app"]
        assert "--proxy-headers" in args
        # The image must not decide whom to trust (e.g. "*"): the compose file does.
        assert not any(arg.startswith("--forwarded-allow-ips") for arg in args)

        layout = _proxy_layout(compose_file, network)
        # A single address (not "*", a list or a network), and it is webui's.
        assert ipaddress.ip_address(layout.trusted) == ipaddress.ip_address(layout.webui)
        assert ipaddress.ip_address(layout.webui) in layout.subnet
        # Host processes connect from the gateway; it must never be trusted.
        assert layout.webui not in (layout.gateway, layout.supervisor)


class TestHealthchecks:
    """The healthchecks of the compose files have no credentials. A check that
    never passes leaves its container unhealthy, and webui only starts once the
    supervisor is healthy."""

    @pytest.mark.parametrize("compose_file", ["docker-compose.yml", "docker-compose.test.yml"])
    def test_healthchecks_call_a_public_route(self, client, compose_file):
        urls = {
            name: re.findall(r"https?://[^\s'\"]+", " ".join(service["healthcheck"]["test"][1:]))
            for name, service in _compose(compose_file)["services"].items()
            if "healthcheck" in service
        }

        assert set(urls) == {"supervisor", "webui"}
        for name, found in urls.items():
            assert len(found) == 1, name
            path = urlsplit(found[0]).path
            assert ("GET", path) in PUBLIC, name
            assert client.get(path).status_code == 200, name

    def test_successful_checks_stay_out_of_the_access_log(self):
        # The checks call the route every 10 seconds (supervisor) and every 30
        # (webui, through Caddy): without the filter of src.main, thousands of
        # lines a day in `docker logs unos-supervisor`. A real uvicorn server
        # (the pinned version, as in the image) answers through a stub app
        # with the status the request asks for, and logs the requests through
        # the "uvicorn.access" logger that src.main filters.
        async def stub(scope, receive, send):
            status = int(dict(scope["headers"]).get(b"x-status", b"200"))
            await send({"type": "http.response.start", "status": status, "headers": [(b"content-length", b"0")]})
            await send({"type": "http.response.body", "body": b""})

        lines = []
        capture = logging.Handler()
        # Without the client address, whose port changes.
        capture.emit = lambda record: lines.append(record.getMessage().split(" - ", 1)[1])
        access_log = logging.getLogger("uvicorn.access")
        level = access_log.level
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(stub, log_config=None, lifespan="off"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        access_log.addHandler(capture)
        access_log.setLevel(logging.INFO)
        thread.start()
        try:
            deadline = time.monotonic() + 30
            while not server.started:
                assert thread.is_alive() and time.monotonic() < deadline, "uvicorn did not start"
                time.sleep(0.05)
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            base = f"http://127.0.0.1:{sock.getsockname()[1]}"
            for method, path, status in [
                ("GET", "/api/v1/health", 200),
                ("GET", "/api/v1/health", 503),
                ("POST", "/api/v1/health", 200),
                ("GET", "/api/v1/health?verbose=1", 200),
                ("GET", "/api/v1/health/full", 200),
                ("GET", "/api/v1/auth/status", 200),
            ]:
                request = urllib.request.Request(base + path, method=method, headers={"X-Status": str(status)})
                try:
                    opener.open(request, timeout=10).close()
                except urllib.error.HTTPError as error:
                    error.close()
                    assert error.code == status
        finally:
            server.should_exit = True
            thread.join(10)
            access_log.removeHandler(capture)
            access_log.setLevel(level)
            sock.close()

        # A failed check is still logged, and so is every other request.
        assert lines == [
            '"GET /api/v1/health HTTP/1.1" 503',
            '"POST /api/v1/health HTTP/1.1" 200',
            '"GET /api/v1/health?verbose=1 HTTP/1.1" 200',
            '"GET /api/v1/health/full HTTP/1.1" 200',
            '"GET /api/v1/auth/status HTTP/1.1" 200',
        ]
