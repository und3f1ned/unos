"""Authorization matrix for every HTTP route, and role-specific behaviour.

Every route needs an explicit decision. It is either listed in PUBLIC, listed
in USER_ALLOWED (any signed-in user), or it must depend on ``require_admin``
(directly or through its router). A new route that does none of this fails
``test_every_route_has_an_explicit_authorization_decision``.
"""
import asyncio
import ipaddress
import json
import re
import shlex
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import bcrypt
import docker
import pytest
import yaml
from fastapi.routing import APIRoute, APIWebSocketRoute
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

import src.host
from src.auth.models import APIToken, User
from src.auth.router import limiter as auth_limiter
from src.auth.utils import create_access_token, require_admin, verify_token
from src.database import Base, get_db
from src.main import app
from src.main import limiter as app_limiter
from src.security.models import LoginAttempt

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
    ("POST", f"{API}/auth/recovery-codes/verify"),  # stub, always 501
    ("GET", f"{API}/health"),
}

# Any signed-in user, administrator or not.
USER_ALLOWED = {
    # Own account and credentials
    ("GET", f"{API}/auth/me"),
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
    # Read-only system information for the dashboard
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
    monkeypatch.setattr("src.audit.router.AUDIT_FILE", str(tmp_path / "audit_log.json"))


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
def admin_headers():
    return _auth(create_access_token({"sub": "admin"}))


@pytest.fixture
def alice_headers():
    return _auth(create_access_token({"sub": "alice"}))


@pytest.fixture
def bob_headers():
    return _auth(create_access_token({"sub": "bob"}))


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
            ("PUT", f"{API}/system/hostname"),
            ("GET", f"{API}/audit/logs"),
            ("GET", f"{API}/security/firewall"),
            ("GET", f"{API}/health/full"),
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

    def test_regular_user_only_reaches_user_routes(self, client, alice_headers):
        problems = []
        for method, path, _ in _http_operations():
            if (method, path) in PUBLIC:
                continue
            response = _call(client, method, path, alice_headers)
            if (method, path) in USER_ALLOWED:
                if response.status_code in (401, 403):
                    problems.append(f"{method} {path}: user route returned {response.status_code}")
            elif response.status_code != 403:
                problems.append(f"{method} {path}: regular user got {response.status_code}, expected 403")
        assert problems == []

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
        assert client.delete(f"{API}/auth/users/{authz_db.admin_id}", headers=alice_headers).status_code == 403
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
        response = client.delete(f"{API}/auth/users/{authz_db.admin_id}", headers=admin_headers)

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

        response = client.delete(f"{API}/auth/users/{authz_db.admin_id}", headers=admin_headers)
        assert response.status_code == 400

    def test_admin_can_delete_another_admin_while_one_remains(self, client, authz_db, admin_headers):
        with Session(authz_db.engine) as session:
            second = User(username="admin2", hashed_password=_PASSWORD_HASH, is_admin=True)
            session.add(second)
            session.commit()
            second_id = second.id

        assert client.delete(f"{API}/auth/users/{second_id}", headers=admin_headers).status_code == 200

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
        audit = json.loads((tmp_path / "audit_log.json").read_text())
        assert [entry["ip_address"] for entry in audit] == ["203.0.113.7", "198.51.100.4"]

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
        # Another browser behind the same proxy is not locked out.
        assert self._login(proxied_client, "198.51.100.4").status_code == 401

    def test_direct_connections_cannot_choose_their_address(self, direct_client, deployment, authz_db, tmp_path):
        """A new X-Forwarded-For per attempt must not mean a new rate-limit bucket."""
        codes = [self._login(direct_client, f"198.51.100.{n}").status_code for n in range(6)]
        assert codes == [401] * 5 + [429]

        with Session(authz_db.engine) as session:
            ips = {a.ip_address for a in session.scalars(select(LoginAttempt))}
        assert ips == {deployment.gateway}
        audit = json.loads((tmp_path / "audit_log.json").read_text())
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
