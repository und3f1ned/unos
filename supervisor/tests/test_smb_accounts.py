"""SMB accounts (ADR-0105, ADR-0118): allow, activation, collisions, password
changes, revocation, promotion and deletion, through the API and the FakeSambaHost.

service.apply_smb_config and service.delete_access_of_user are AsyncMocks;
the smb.conf content is asserted in test_smb_integration.py. After every test the
passwords used appear in no argv, no log record and no response body (ADR-0125).
"""
from __future__ import annotations

import ast
import asyncio
import json
import logging
import re
import subprocess
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import bcrypt
import httpx
import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from src.auth import router as auth_router
from src.auth import utils as auth_utils
from src.auth.models import User
from src.database import Base, get_db
from src.host import HostResult
from src.main import app
from src.shares import accounts, constants, interfaces, service, smb, smbconf
from src.shares.account_state import ADMIN_REASON
from src.shares.models import SmbAccount, SmbShare, SmbShareAccess, SmbState

from .samba_fake import FakeSambaHost, install_fake_samba_host
from .audit_helpers import audit_entries
from .session_tokens import session_headers

API = "/api/v1"
SRC = Path(__file__).resolve().parents[1] / "src"

ADMIN_PW = "Admin-pass-2026"
ALICE_PW = "Alice-pass-2026"
BOB_PW = "Bob-pass-2026!"
NEW_PW = "Alice-new-pass-77"
SET_PW = "Set-by-admin-99"
SHORT_PW = "short7"  # a UNOS password that SMB refuses (7 characters)
COMMON_PW = "Password123"  # on the list of common passwords that SMB refuses (ADR-0279)
COMMON_RULE = (
    "SMB passwords must not be common passwords: this one is on the list of passwords that attackers try first"
)
MALLORY_PW = "Mallory-pass-2026"
SECRETS = (ADMIN_PW, ALICE_PW, BOB_PW, NEW_PW, SET_PW, MALLORY_PW, COMMON_PW, "Carol-pass-2026", "Dave-pass-2026x")

NOTHING_CHANGED = "Nothing was changed: the database was busy; try again"
DATABASE_BUSY = "The database was busy; try again"
SMBPASSWD_TIMEOUT = "smbpasswd did not answer in time"
FOREIGN_ALICE = (
    "A host account named alice exists that UNOS did not create. Remove it on the host "
    "(pdbedit -x -u alice; userdel alice) or use another UNOS user."
)
OTHER_CASE_ALICE = (
    "A Samba account named Alice exists that UNOS did not create; Samba treats it as alice. "
    "Remove it on the host (pdbedit -x -u Alice) or use another UNOS user."
)
TOOLS_MISSING = (
    "SMB accounts are not available: the host lacks smbpasswd, pdbedit, smbstatus "
    "(install samba and samba-common-bin)"
)
# The fixed warnings of the self flows (ADR-0131).
SELF_APPLY_WARNING = (
    "SMB access changed, but Samba has not applied every change yet; "
    "an administrator can see why on the Shares page"
)
SELF_SESSIONS_WARNING = "Some of your open SMB connections could not be ended; they end when your devices disconnect"
# What the errors of the self flows say instead of a failed command's output (ADR-0131).
SELF_HOST_FAILED = (
    "a command on the NAS failed; try again later or ask an administrator (details are in the supervisor log)"
)


def _hash(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=4)).decode()


def _cheap_hash(password: str) -> str:
    """auth.router.hash_password at bcrypt cost 4 (the tests do not need cost 12)."""
    return _hash(password)


class RecordingClient:
    """TestClient that keeps every response body (for the secrets check)."""

    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.bodies: list[str] = []

    def request(self, method, url, **kwargs):
        response = self.client.request(method, url, **kwargs)
        self.bodies.append(response.text)
        return response

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)

    def put(self, url, **kwargs):
        return self.request("PUT", url, **kwargs)

    def delete(self, url, **kwargs):
        return self.request("DELETE", url, **kwargs)


class World:
    """A temporary database (admin, alice, bob), the FakeSambaHost and the API."""

    def __init__(self, tmp_path: Path, monkeypatch, caplog) -> None:
        self.path = tmp_path / "unos.db"
        self.audit_file = tmp_path / "audit.jsonl"
        self.engine = create_engine(f"sqlite:///{self.path}")
        Base.metadata.create_all(self.engine)
        self.ids: dict[str, int] = {}
        for name, password, admin in (("admin", ADMIN_PW, True), ("alice", ALICE_PW, False), ("bob", BOB_PW, False)):
            self.add_user(name, password, admin=admin)
        self.async_engine = create_async_engine(f"sqlite+aiosqlite:///{self.path}", poolclass=NullPool)
        self.maker = async_sessionmaker(self.async_engine, expire_on_commit=False)

        async def override_get_db():
            async with self.maker() as session:
                yield session

        app.dependency_overrides[get_db] = override_get_db
        self.fake: FakeSambaHost = install_fake_samba_host(monkeypatch)
        monkeypatch.setattr("src.audit.router.AUDIT_FILE", str(self.audit_file))
        # UNOS's own smb.conf is installed: activations are allowed (ADR-0130).
        self.smb_conf = tmp_path / "smb.conf"
        self.smb_conf.write_text(smbconf.render_config([]))
        monkeypatch.setattr(smb.settings, "smb_conf_path", str(self.smb_conf))
        # TOTP codes are single-use per user id; ids repeat from test to test.
        monkeypatch.setattr(auth_utils, "_last_totp_steps", {})
        self.apply = AsyncMock(return_value=interfaces.ApplyResult())
        monkeypatch.setattr(service, "apply_smb_config", self.apply)
        self.delete_access = AsyncMock(side_effect=self._delete_access)
        monkeypatch.setattr(service, "delete_access_of_user", self.delete_access)
        monkeypatch.setattr(auth_router, "hash_password", _cheap_hash)
        monkeypatch.setattr(accounts, "USERDEL_RETRY_SECONDS", 0.01)
        auth_router.limiter.reset()
        self.client = RecordingClient(TestClient(app, raise_server_exceptions=False))
        self.caplog = caplog
        caplog.set_level(logging.DEBUG)
        logging.getLogger(__name__).debug("SMB test world ready")  # the secrets check sees a captured log

    @staticmethod
    async def _delete_access(db, user_id):
        """What service.delete_access_of_user does to the rows (flushed, not committed)."""
        result = await db.execute(delete(SmbShareAccess).where(SmbShareAccess.user_id == user_id))
        return result.rowcount

    def close(self) -> None:
        app.dependency_overrides.pop(get_db, None)
        auth_router.limiter.reset()
        asyncio.run(self.async_engine.dispose())
        self.engine.dispose()

    # -- database ---------------------------------------------------------------

    def add_user(self, name: str, password: str, *, admin: bool = False, totp_secret: str | None = None) -> int:
        with Session(self.engine) as session:
            user = User(username=name, hashed_password=_hash(password), is_admin=admin,
                        totp_secret=totp_secret, totp_enabled=totp_secret is not None)
            session.add(user)
            session.commit()
            self.ids[name] = user.id
        return self.ids[name]

    def replace_user(self, old: str, new: str, password: str) -> int:
        """Delete user ``old`` (and its account row) and give its id to a new user ``new``:
        SQLite reuses the highest rowid after a deletion."""
        user_id = self.ids.pop(old)
        with Session(self.engine) as session:
            session.execute(delete(SmbAccount).where(SmbAccount.user_id == user_id))
            session.execute(delete(User).where(User.id == user_id))
            session.add(User(id=user_id, username=new, hashed_password=_hash(password), is_admin=False))
            session.commit()
        self.ids[new] = user_id
        return user_id

    def set_totp(self, name: str, secret: str) -> None:
        """Turn two-factor authentication on for user ``name``."""
        with Session(self.engine) as session:
            user = session.get(User, self.ids[name])
            user.totp_secret, user.totp_enabled = secret, True
            session.commit()

    def set_row(self, name: str, **values) -> None:
        """Insert or change the smb_accounts row of user ``name``."""
        with Session(self.engine) as session:
            row = session.get(SmbAccount, self.ids[name])
            if row is None:
                row = SmbAccount(user_id=self.ids[name], linux_name=name, enabled=False, password_state="needed")
                session.add(row)
            for key, value in values.items():
                setattr(row, key, value)
            session.commit()

    def row(self, name: str) -> SmbAccount | None:
        with Session(self.engine) as session:
            row = session.get(SmbAccount, self.ids[name])
            if row is not None:
                session.expunge(row)
            return row

    def user(self, name: str) -> User | None:
        with Session(self.engine) as session:
            user = session.get(User, self.ids[name])
            if user is not None:
                session.expunge(user)
            return user

    def next_uid(self) -> int | None:
        with Session(self.engine) as session:
            state = session.get(SmbState, 1)
            return None if state is None else state.next_uid

    def set_next_uid(self, value: int) -> None:
        with Session(self.engine) as session:
            state = session.get(SmbState, 1) or SmbState(id=1)
            state.next_uid = value
            session.add(state)
            session.commit()

    def add_share(self, name: str, *grants: tuple[str, str], guest: bool = False) -> int:
        with Session(self.engine) as session:
            share = SmbShare(name=name, path=f"/mnt/pools/data/{name}", guest_ok=guest)
            session.add(share)
            session.flush()
            for user, level in grants:
                session.add(SmbShareAccess(share_id=share.id, user_id=self.ids[user], username=user, level=level))
            session.commit()
            return share.id

    def access_rows(self, name: str) -> list[tuple[int, str]]:
        with Session(self.engine) as session:
            return [(a.share_id, a.level) for a in session.scalars(
                select(SmbShareAccess).where(SmbShareAccess.user_id == self.ids[name]))]

    # -- host -------------------------------------------------------------------

    def make_allowed(self, name: str) -> None:
        self.set_row(name, enabled=True, password_state="needed")

    def make_active(self, name: str, uid: int, password: str) -> None:
        """An active account: the row, the marked host account, the group and the passdb entry."""
        if "unos-smb" not in self.fake.groups:
            self.fake.add_smb_group()
        self.fake.add_account(name, uid)
        self.fake.add_passdb(name, password)
        self.set_row(name, enabled=True, password_state="set", uid=uid, password_set_at=datetime(2026, 11, 1, 9, 0))
        if (self.next_uid() or 0) <= uid:
            self.set_next_uid(uid + 1)

    def deactivated(self, name: str, uid: int) -> None:
        """Activated once, then turned off: the host account is kept, without passdb entry."""
        if "unos-smb" not in self.fake.groups:
            self.fake.add_smb_group()
        self.fake.add_account(name, uid)
        self.set_row(name, enabled=True, password_state="needed", uid=uid)
        if (self.next_uid() or 0) <= uid:
            self.set_next_uid(uid + 1)

    # -- API --------------------------------------------------------------------

    def headers(self, name: str) -> dict[str, str]:
        return session_headers(self.engine, name)

    def self_enable(self, name: str = "alice", password: str = ALICE_PW, **extra):
        return self.client.post(f"{API}/auth/me/smb/enable", json={"current_password": password, **extra},
                                headers=self.headers(name))

    def self_disable(self, name: str = "alice"):
        return self.client.post(f"{API}/auth/me/smb/disable", headers=self.headers(name))

    def admin_enable(self, name: str, password: str | None = None, *, username: str | None = None):
        body = {"username": username or name}
        if password is not None:
            body["password"] = password
        return self.client.post(f"{API}/auth/users/{self.ids[name]}/smb/enable", json=body, headers=self.headers("admin"))

    def admin_disable(self, name: str, *, username: str | None = None):
        return self.client.post(f"{API}/auth/users/{self.ids[name]}/smb/disable",
                                json={"username": username or name}, headers=self.headers("admin"))

    def put_user(self, name: str, **body):
        return self.client.put(f"{API}/auth/users/{self.ids[name]}", json=body, headers=self.headers("admin"))

    def delete_user(self, name: str, *, username: str | None = None):
        return self.client.delete(f"{API}/auth/users/{self.ids[name]}", params={"username": username or name},
                                  headers=self.headers("admin"))

    def change_password(self, name: str, current: str, new: str):
        return self.client.post(f"{API}/auth/change-password", json={"current_password": current, "new_password": new},
                                headers=self.headers(name))

    def audit(self) -> list[dict]:
        return audit_entries(self.audit_file)

    def smb_audit(self) -> list[tuple[str, str, str]]:
        return [(e["resource_id"], e["details"], e["severity"]) for e in self.audit() if e["resource_type"] == "smb_account"]


@pytest.fixture
def world(tmp_path, monkeypatch, caplog):
    w = World(tmp_path, monkeypatch, caplog)
    try:
        yield w
    finally:
        w.close()
    # ADR-0125: no password in argv, in a log record or in an answer (only requests carry them).
    # In a fixture's teardown caplog.records holds only the teardown's records.
    records = [r for when in ("setup", "call", "teardown") for r in caplog.get_records(when)]
    assert records, "the log was not captured"
    for secret in SECRETS:
        w.fake.assert_no_secret(secret)
        for record in records:
            message = record.getMessage()
            assert secret not in message, f"secret in the log: {message[:80]}"
        for body in w.client.bodies:
            assert secret not in body, "secret in a response body"


@pytest.fixture
def failing_commit(monkeypatch):
    """AsyncSession.commit raising (a busy database) at the n-th commit of the test
    (``fail_at``: one number, or a set of numbers)."""
    real = AsyncSession.commit
    state = SimpleNamespace(calls=0, fail_at=None, seen=[])

    async def commit(self):
        state.calls += 1
        fail_at = state.fail_at if isinstance(state.fail_at, (set, frozenset)) else {state.fail_at}
        if state.calls in fail_at:
            raise OperationalError("COMMIT", {}, Exception("database is locked"))
        return await real(self)

    monkeypatch.setattr(AsyncSession, "commit", commit)
    return state


def calls_at_commits(world: "World", monkeypatch) -> list[int]:
    """The number of host calls made before each AsyncSession.commit, in order."""
    marks: list[int] = []
    real = AsyncSession.commit

    async def commit(self):
        marks.append(len(world.fake.calls))
        return await real(self)

    monkeypatch.setattr(AsyncSession, "commit", commit)
    return marks


def smbpasswd_times_out_after_writing(world: "World", monkeypatch, times: int = 1) -> None:
    """The next ``times`` smbpasswd calls store the password, then do not answer in time
    (only nsenter is killed at the timeout: smbpasswd may finish its write)."""
    real_run = world.fake.run
    left = {"n": times}

    def run(cmd, **kwargs):
        result = real_run(cmd, **kwargs)
        if cmd[0] == "smbpasswd" and left["n"] > 0:
            left["n"] -= 1
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))
        return result

    monkeypatch.setattr("src.host.run_on_host", run)


def times_out_once(world: "World", monkeypatch, *prefix: str) -> None:
    """The next command starting with ``prefix`` does not answer in time (recorded, not run)."""
    real_run = world.fake.run
    left = {"n": 1}

    def run(cmd, **kwargs):
        if tuple(cmd[: len(prefix)]) == prefix and left["n"] > 0:
            left["n"] -= 1
            world.fake.calls.append((list(cmd), kwargs.get("input")))
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))
        return real_run(cmd, **kwargs)

    monkeypatch.setattr("src.host.run_on_host", run)


def _disk_full(statement: str) -> OperationalError:
    return OperationalError(statement, {}, Exception("database or disk is full"))


def flush_fails_after_the_revocation(world: "World", monkeypatch, name: str) -> list[list[str]]:
    """Session.flush raises (a full disk) at the first flush with pending changes after the
    revoke step removed the passdb entry of ``name``, before the commit: a full disk fails a
    statement once SQLite's page cache spills into unos.db-wal (ADR-0306), and a lock held past
    the busy timeout fails the first write. Returns the classes of the objects that the failed
    flush had pending."""
    real = Session.flush
    failed: list[list[str]] = []

    def flush(self, *args, **kwargs):
        pending = [*self.new, *self.dirty, *self.deleted]
        if not failed and pending and name.lower() not in world.fake.passdb:
            failed.append(sorted(type(obj).__name__ for obj in pending))
            raise _disk_full("FLUSH")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Session, "flush", flush)
    return failed


USERADD_70000 = [
    "useradd", "-r", "-l", "-N", "-M", "-u", "70000", "-g", "70000",
    "-d", "/nonexistent", "-s", "/usr/sbin/nologin", "-c", "UNOS SMB user", "alice",
]


# ---------------------------------------------------------------------------
# allow
# ---------------------------------------------------------------------------


class TestAllow:
    def test_allow_writes_the_database_only(self, world):
        response = world.admin_enable("alice")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["smb"] == {"state": "password_needed", "allowed": True, "linux_name": "alice", "uid": None,
                               "password_set_at": None, "reason": None}
        assert body["warnings"] == []
        assert world.fake.calls == []
        row = world.row("alice")
        assert (row.linux_name, row.uid, row.enabled, row.password_state) == ("alice", None, True, "needed")
        world.apply.assert_not_awaited()
        assert world.smb_audit() == [("alice", "SMB access allowed", "info")]

    def test_allow_again_is_a_no_op(self, world):
        assert world.admin_enable("alice").status_code == 200
        again = world.admin_enable("alice")

        assert again.status_code == 200
        assert again.json()["smb"]["state"] == "password_needed"
        assert world.fake.calls == []
        assert len(world.smb_audit()) == 1  # nothing changed the second time

    def test_allow_after_disallow_keeps_the_uid(self, world):
        world.deactivated("alice", 70003)
        world.set_row("alice", enabled=False)

        assert world.admin_enable("alice").json()["smb"] == {
            "state": "password_needed", "allowed": True, "linux_name": "alice", "uid": 70003,
            "password_set_at": None, "reason": None,
        }
        assert world.row("alice").uid == 70003
        assert world.fake.calls == []

    def test_administrators_are_refused(self, world):
        response = world.admin_enable("admin")

        assert response.status_code == 409
        assert response.json()["detail"] == ADMIN_REASON
        assert world.row("admin") is None
        assert world.fake.calls == []

    def test_an_unusable_legacy_name_is_refused(self, world):
        world.add_user("Bob.Smith", "Legacy-pass-1")

        response = world.admin_enable("Bob.Smith")

        assert response.status_code == 409
        assert response.json()["detail"].startswith("The username 'Bob.Smith' cannot be used for SMB. Use 1 to 32")
        assert world.row("Bob.Smith") is None

    def test_list_users_shows_the_smb_state_of_each(self, world):
        world.make_active("alice", 70000, ALICE_PW)
        world.add_user("Bob.Smith", "Legacy-pass-1")

        users = {u["username"]: u for u in world.client.get(f"{API}/auth/users", headers=world.headers("admin")).json()}

        assert users["admin"]["smb"]["state"] == "unavailable"
        assert users["admin"]["smb"]["reason"] == ADMIN_REASON
        assert users["alice"]["smb"]["state"] == "active"
        assert users["alice"]["smb"]["uid"] == 70000
        assert users["bob"]["smb"] == {"state": "off", "allowed": False, "linux_name": None, "uid": None,
                                       "password_set_at": None, "reason": None}
        assert users["Bob.Smith"]["smb"]["state"] == "unavailable"
        # GET /auth/me keeps smb null; the Account page uses /auth/me/smb.
        assert world.client.get(f"{API}/auth/me", headers=world.headers("alice")).json()["smb"] is None


# ---------------------------------------------------------------------------
# Self-activation
# ---------------------------------------------------------------------------


class TestSelfActivation:
    def test_success(self, world):
        world.make_allowed("alice")

        response = world.self_enable()

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["account"]["state"] == "active"
        assert body["account"]["linux_name"] == "alice" and body["account"]["uid"] == 70000
        assert body["warnings"] == []
        fake = world.fake
        assert fake.commands("groupadd") == [["groupadd", "-g", "70000", "unos-smb"]]
        assert fake.commands("useradd") == [USERADD_70000]
        assert fake.commands("smbpasswd") == [["smbpasswd", "-s", "-a", "alice"]]
        assert fake.stdin_of("smbpasswd") == [f"{ALICE_PW}\n{ALICE_PW}\n"]
        assert ["sync", "/etc"] in fake.argvs()
        assert fake.passwd["alice"].ours and fake.passwd["alice"].uid == 70000
        assert fake.passdb["alice"] == {"name": "alice", "password": ALICE_PW, "uid": 70000}
        row = world.row("alice")
        assert (row.uid, row.enabled, row.password_state) == (70000, True, "set")
        assert row.password_set_at is not None
        assert world.next_uid() == 70001
        world.apply.assert_awaited_once()
        assert world.smb_audit() == [("alice", "SMB access activated by the user", "info")]
        # The UNOS password is unchanged.
        assert bcrypt.checkpw(ALICE_PW.encode(), world.user("alice").hashed_password.encode())

    def test_the_group_is_created_once(self, world):
        world.make_allowed("alice")
        world.make_allowed("bob")

        assert world.self_enable().status_code == 200
        assert world.self_enable("bob", BOB_PW).status_code == 200

        assert world.fake.commands("groupadd") == [["groupadd", "-g", "70000", "unos-smb"]]
        assert (world.row("alice").uid, world.row("bob").uid) == (70000, 70001)

    def test_status_of_the_account_page(self, world):
        world.make_allowed("alice")
        media = world.add_share("media", ("alice", "write"))
        world.add_share("public", guest=True)
        world.add_share("bobs", ("bob", "read"))

        before = world.client.get(f"{API}/auth/me/smb", headers=world.headers("alice")).json()
        assert before["account"]["state"] == "password_needed"
        assert before["totp_required"] is False
        assert before["shares"] == [
            {"name": "media", "comment": "", "level": "write", "guest_ok": False, "usable": False},
            {"name": "public", "comment": "", "level": "read", "guest_ok": True, "usable": True},
        ]
        assert media

        after = world.self_enable().json()
        assert [s["usable"] for s in after["shares"]] == [True, True]

    def test_wrong_password(self, world):
        world.make_allowed("alice")

        response = world.self_enable(password="Not-alice-pass")

        assert response.status_code == 400
        assert response.json()["detail"] == "Current password is incorrect"
        assert world.fake.calls == []

    def test_not_allowed_by_an_administrator(self, world):
        response = world.self_enable()

        assert response.status_code == 403
        assert response.json()["detail"] == "An administrator must allow SMB access for your account first"
        assert world.fake.calls == []
        assert world.row("alice") is None

    def test_a_password_that_smb_refuses(self, world):
        world.add_user("carol", SHORT_PW)
        world.make_allowed("carol")

        response = world.self_enable("carol", SHORT_PW)

        assert response.status_code == 400
        assert response.json()["detail"] == "SMB passwords must have at least 8 characters"
        assert world.fake.calls == []

    def test_rate_limit(self, world):
        world.make_allowed("alice")
        codes = [world.self_enable(password=f"Wrong-pass-{n}").status_code for n in range(6)]

        assert codes == [400] * 5 + [429]

    def test_two_factor_code_is_required(self, world):
        secret = pyotp.random_base32()
        world.add_user("dave", "Dave-pass-2026x", totp_secret=secret)
        world.make_allowed("dave")
        status = world.client.get(f"{API}/auth/me/smb", headers=world.headers("dave")).json()
        assert status["totp_required"] is True

        missing = world.self_enable("dave", "Dave-pass-2026x")
        wrong = world.self_enable("dave", "Dave-pass-2026x", totp_code="000000"
                                  if pyotp.TOTP(secret).now() != "000000" else "111111")

        assert (missing.status_code, missing.json()["detail"]) == (400, "Two-factor code required")
        assert (wrong.status_code, wrong.json()["detail"]) == (400, "Invalid two-factor code")
        assert world.fake.calls == []

        right = world.self_enable("dave", "Dave-pass-2026x", totp_code=pyotp.TOTP(secret).now())
        assert right.status_code == 200, right.text
        assert right.json()["account"]["state"] == "active"

    def test_tools_missing(self, world):
        world.make_allowed("alice")
        world.fake.missing = {"smbpasswd", "pdbedit", "smbstatus"}

        response = world.self_enable()

        assert response.status_code == 503
        assert response.json()["detail"] == TOOLS_MISSING
        assert world.fake.mutating_calls() == []
        assert world.row("alice").password_state == "needed"
        world.apply.assert_not_awaited()

    def test_smbpasswd_failure_leaves_the_account_not_active(self, world):
        world.make_allowed("alice")
        world.fake.fail[("smbpasswd",)] = (1, "Failed to add entry for user alice.")

        response = world.self_enable()

        assert response.status_code == 503
        # A regular user's own flow: no host output (ADR-0131; TestSelfFlowErrors).
        assert response.json()["detail"] == f"SMB could not be turned on: {SELF_HOST_FAILED}"
        assert "Samba did not accept the password: Failed to add entry for user alice." in _logged(world)
        row = world.row("alice")
        # The uid stays reserved for alice; the account is not active.
        assert (row.uid, row.password_state, row.enabled) == (70000, "needed", True)
        world.apply.assert_not_awaited()
        # A retry is idempotent: the host account exists, only smbpasswd runs again.
        del world.fake.fail[("smbpasswd",)]
        assert world.self_enable().status_code == 200
        assert len(world.fake.commands("useradd")) == 1
        assert world.row("alice").uid == 70000

    def test_a_stale_token_after_the_id_was_reused(self, world):
        world.make_allowed("alice")
        headers = world.headers("alice")
        with Session(world.engine) as session:
            session.get(User, world.ids["alice"]).username = "alicia"
            session.commit()

        response = world.client.post(f"{API}/auth/me/smb/enable", json={"current_password": ALICE_PW}, headers=headers)

        assert response.status_code == 401  # the token's user is gone
        assert world.fake.calls == []


class TestAdminActivation:
    def test_admin_sets_the_password_and_turns_smb_on(self, world):
        response = world.admin_enable("alice", SET_PW)

        assert response.status_code == 200, response.text
        assert response.json()["smb"]["state"] == "active"
        assert world.fake.stdin_of("smbpasswd") == [f"{SET_PW}\n{SET_PW}\n"]
        row = world.row("alice")
        assert (row.enabled, row.password_state, row.uid) == (True, "set", 70000)
        # It is the user's UNOS password too.
        assert bcrypt.checkpw(SET_PW.encode(), world.user("alice").hashed_password.encode())
        assert world.smb_audit() == [("alice", "SMB password set by an administrator", "info")]
        world.apply.assert_awaited_once()

    def test_a_new_password_for_an_active_account_ends_its_sessions(self, world):
        world.make_active("alice", 70000, ALICE_PW)
        pid = world.fake.add_session("alice", ["media"])
        other = world.fake.add_session("bob", ["media"])

        response = world.admin_enable("alice", SET_PW)

        assert response.status_code == 200, response.text
        assert world.fake.passdb["alice"]["password"] == SET_PW
        assert ["smbcontrol", str(pid), "shutdown"] in world.fake.argvs()
        assert world.fake.session_pids() == [other]
        assert world.fake.commands("useradd") == []  # the account exists

    def test_an_administrator_cannot_get_smb(self, world):
        response = world.admin_enable("admin", SET_PW)

        assert response.status_code == 409
        assert response.json()["detail"] == ADMIN_REASON
        assert world.fake.calls == []
        assert bcrypt.checkpw(ADMIN_PW.encode(), world.user("admin").hashed_password.encode())

    def test_a_password_that_smb_refuses(self, world):
        response = world.admin_enable("alice", "tab\tpassword")

        assert response.status_code == 400
        assert "control characters" in response.json()["detail"]
        assert world.fake.calls == []


class TestCreateUserWithSmb:
    def test_create_with_smb(self, world):
        response = world.client.post(
            f"{API}/auth/users", json={"username": "carol", "password": "Carol-pass-2026", "smb_enabled": True},
            headers=world.headers("admin"),
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["smb"]["state"] == "active" and body["warnings"] == []
        assert world.fake.passdb["carol"]["password"] == "Carol-pass-2026"
        assert [e["action"] for e in world.audit()] == ["user_created", "smb_account_updated"]

    def test_create_with_smb_on_a_host_without_samba(self, world):
        world.fake.missing = {"smbpasswd", "pdbedit", "smbstatus"}

        response = world.client.post(
            f"{API}/auth/users", json={"username": "carol", "password": "Carol-pass-2026", "smb_enabled": True},
            headers=world.headers("admin"),
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["username"] == "carol"
        assert body["smb"]["state"] == "off"
        assert body["warnings"] == [f"User created, but SMB access could not be turned on: {TOOLS_MISSING}"]

    def test_a_password_that_smb_refuses_creates_nothing(self, world):
        response = world.client.post(
            f"{API}/auth/users", json={"username": "carol", "password": SHORT_PW, "smb_enabled": True},
            headers=world.headers("admin"),
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "SMB passwords must have at least 8 characters"
        with Session(world.engine) as session:
            assert session.scalars(select(User).where(User.username == "carol")).first() is None

    def test_without_smb_no_host_command(self, world):
        response = world.client.post(
            f"{API}/auth/users", json={"username": "carol", "password": SHORT_PW}, headers=world.headers("admin")
        )

        assert response.status_code == 200
        assert response.json()["smb"]["state"] == "off"
        assert world.fake.calls == []


def orphan_rows_of(world: "World", name: str) -> int:
    """A restored or hand-edited database lost the user ``name`` (the highest id) but kept its SMB
    rows, its Samba entry and its host account. Returns the id, which SQLite gives to the next user."""
    user_id = world.ids[name]
    with Session(world.engine) as session:
        session.execute(delete(User).where(User.id == user_id))
        session.commit()
    return user_id


class TestCreateUserAfterAnOrphan:
    """ADR-0132: a new user never inherits SMB rows of its id."""

    def test_the_rows_of_its_id_are_deleted(self, world):
        world.make_active("bob", 70001, BOB_PW)
        world.add_share("media", ("bob", "write"))
        bob_id = orphan_rows_of(world, "bob")

        response = world.client.post(f"{API}/auth/users", json={"username": "bob", "password": "Bob-other-2026"},
                                     headers=world.headers("admin"))

        assert response.status_code == 200, response.text
        assert response.json()["id"] == bob_id
        assert response.json()["smb"]["state"] == "off"
        assert world.row("bob") is None
        assert world.access_rows("bob") == []
        assert world.delete_access.await_args.args[1] == bob_id
        assert world.fake.calls == []  # the old passdb entry and host account are the reconcile's

    def test_with_smb_a_fresh_uid(self, world):
        world.make_active("bob", 70001, BOB_PW)
        bob_id = orphan_rows_of(world, "bob")

        response = world.client.post(
            f"{API}/auth/users", json={"username": "bob", "password": "Bob-other-2026", "smb_enabled": True},
            headers=world.headers("admin"),
        )

        assert response.status_code == 200, response.text
        assert response.json()["id"] == bob_id
        assert response.json()["smb"]["state"] == "active" and response.json()["smb"]["uid"] == 70002
        assert world.fake.passdb["bob"]["password"] == "Bob-other-2026"
        assert world.fake.passwd["bob"].uid == 70002  # the old account was stale: removed first
        assert ["userdel", "bob"] in world.fake.argvs()


# ---------------------------------------------------------------------------
# Stale identity (ADR-0081): flows read the rows after taking smb_lock
# ---------------------------------------------------------------------------


async def _wait_for_lock_waiter(timeout: float = 5.0) -> None:
    lock = service.smb_lock()
    for _ in range(int(timeout / 0.01)):
        if lock.locked() and lock._waiters:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("no request waits on smb_lock")


class TestStaleIdentity:
    async def test_a_promotion_wins_over_a_waiting_self_activation(self, world):
        world.deactivated("alice", 70000)
        reached, release = world.fake.pause_at("pdbedit", "-L")
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://unos") as client:
            promote = asyncio.create_task(client.put(
                f"{API}/auth/users/{world.ids['alice']}", json={"is_admin": True}, headers=world.headers("admin")
            ))
            assert await asyncio.to_thread(reached.wait, 5)  # the promotion holds smb_lock
            enable = asyncio.create_task(client.post(
                f"{API}/auth/me/smb/enable", json={"current_password": ALICE_PW}, headers=world.headers("alice")
            ))
            await _wait_for_lock_waiter()
            release.set()
            promoted, enabled = await promote, await enable
        world.client.bodies += [promoted.text, enabled.text]

        assert promoted.status_code == 200, promoted.text
        assert enabled.status_code == 409
        assert enabled.json()["detail"] == ADMIN_REASON
        assert world.fake.commands("smbpasswd") == []
        assert world.user("alice").is_admin is True
        assert world.row("alice").enabled is False

    async def test_a_waiting_password_reset_after_a_disallow_does_not_reach_samba(self, world):
        world.make_active("alice", 70000, ALICE_PW)
        reached, release = world.fake.pause_at("pdbedit", "-L")
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://unos") as client:
            disallow = asyncio.create_task(client.post(
                f"{API}/auth/users/{world.ids['alice']}/smb/disable", json={"username": "alice"},
                headers=world.headers("admin")
            ))
            assert await asyncio.to_thread(reached.wait, 5)
            reset = asyncio.create_task(client.put(
                f"{API}/auth/users/{world.ids['alice']}", json={"password": NEW_PW, "username": "alice"},
                headers=world.headers("admin"),
            ))
            await _wait_for_lock_waiter()
            release.set()
            disallowed, reset_done = await disallow, await reset
        world.client.bodies += [disallowed.text, reset_done.text]

        assert disallowed.status_code == 200, disallowed.text
        assert reset_done.status_code == 200, reset_done.text
        assert reset_done.json()["smb"]["state"] == "off"
        assert world.fake.commands("smbpasswd") == []
        assert world.fake.passdb == {}
        assert bcrypt.checkpw(NEW_PW.encode(), world.user("alice").hashed_password.encode())

    @pytest.mark.parametrize("flow", ["smb-disable", "smb-enable", "change-password"])
    async def test_a_self_flow_whose_user_id_was_reused_while_it_waited(self, world, flow):
        """ADR-0081: alice's request passed verify_token, then waits on smb_lock; meanwhile
        alice is deleted and SQLite gives her id to mallory. The request must not act on
        mallory (404), whatever mallory's SMB state is."""
        world.make_allowed("alice")
        world.make_active("bob", 70001, BOB_PW)
        url, body = {
            "smb-disable": (f"{API}/auth/me/smb/disable", None),
            "smb-enable": (f"{API}/auth/me/smb/enable", {"current_password": ALICE_PW}),
            "change-password": (f"{API}/auth/change-password",
                                {"current_password": ALICE_PW, "new_password": NEW_PW}),
        }[flow]
        reached, release = world.fake.pause_at("pdbedit", "-L")
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://unos") as client:
            holder = asyncio.create_task(client.post(
                f"{API}/auth/users/{world.ids['bob']}/smb/disable", json={"username": "bob"},
                headers=world.headers("admin")
            ))
            assert await asyncio.to_thread(reached.wait, 5)  # bob's disallow holds smb_lock
            waiting = asyncio.create_task(client.post(url, json=body, headers=world.headers("alice")))
            await _wait_for_lock_waiter()
            world.replace_user("alice", "mallory", MALLORY_PW)
            world.make_active("mallory", 70002, MALLORY_PW)
            release.set()
            held, answered = await holder, await waiting
        world.client.bodies += [held.text, answered.text]

        assert held.status_code == 200, held.text
        assert answered.status_code == 404, answered.text
        assert answered.json()["detail"] == "User not found"
        # Only bob's disallow reached the host; mallory's account is untouched.
        assert world.fake.commands("pdbedit", "-x") == [["pdbedit", "-x", "-u", "bob"]]
        assert world.fake.commands("smbpasswd") == []
        assert world.fake.passdb["mallory"]["password"] == MALLORY_PW
        row = world.row("mallory")
        assert (row.enabled, row.password_state, row.uid) == (True, "set", 70002)
        assert bcrypt.checkpw(MALLORY_PW.encode(), world.user("mallory").hashed_password.encode())

    async def test_a_password_change_that_waited_for_an_administrators_reset(self, world):
        """The current password is checked again under smb_lock when the stored hash changed
        while the request waited: an old (perhaps leaked) password cannot overwrite the
        administrator's reset, in UNOS or in Samba."""
        world.make_active("alice", 70000, ALICE_PW)
        reached, release = world.fake.pause_at("smbpasswd")
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://unos") as client:
            reset = asyncio.create_task(client.put(
                f"{API}/auth/users/{world.ids['alice']}", json={"password": SET_PW, "username": "alice"},
                headers=world.headers("admin"),
            ))
            assert await asyncio.to_thread(reached.wait, 5)  # the reset holds smb_lock
            change = asyncio.create_task(client.post(
                f"{API}/auth/change-password", json={"current_password": ALICE_PW, "new_password": NEW_PW},
                headers=world.headers("alice"),
            ))
            await _wait_for_lock_waiter()
            release.set()
            reset_done, changed = await reset, await change
        world.client.bodies += [reset_done.text, changed.text]

        assert reset_done.status_code == 200, reset_done.text
        assert changed.status_code == 400, changed.text
        assert changed.json()["detail"] == "Current password is incorrect"
        assert world.fake.stdin_of("smbpasswd") == [f"{SET_PW}\n{SET_PW}\n"]
        assert world.fake.passdb["alice"]["password"] == SET_PW
        assert bcrypt.checkpw(SET_PW.encode(), world.user("alice").hashed_password.encode())

    async def test_two_factor_turned_on_while_a_self_activation_waits(self, world):
        """The two-factor rule is checked on the user reloaded under smb_lock."""
        world.make_allowed("alice")
        world.make_active("bob", 70001, BOB_PW)
        reached, release = world.fake.pause_at("pdbedit", "-L")
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://unos") as client:
            holder = asyncio.create_task(client.post(
                f"{API}/auth/users/{world.ids['bob']}/smb/disable", json={"username": "bob"},
                headers=world.headers("admin")
            ))
            assert await asyncio.to_thread(reached.wait, 5)  # bob's disallow holds smb_lock
            enable = asyncio.create_task(client.post(
                f"{API}/auth/me/smb/enable", json={"current_password": ALICE_PW}, headers=world.headers("alice")
            ))
            await _wait_for_lock_waiter()
            world.set_totp("alice", pyotp.random_base32())
            release.set()
            held, enabled = await holder, await enable
        world.client.bodies += [held.text, enabled.text]

        assert held.status_code == 200, held.text
        assert enabled.status_code == 400, enabled.text
        assert enabled.json()["detail"] == "Two-factor code required"
        assert world.fake.commands("smbpasswd") == []
        assert world.fake.commands("useradd") == []
        assert world.row("alice").password_state == "needed"


async def _sent_while_locked(world: "World", method: str, url: str, **kwargs):
    """Send a request while the test holds smb_lock, through httpx.ASGITransport in the test's
    own event loop (a TestClient runs the app in another loop, which has its own lock).
    Returns the host calls made and whether the request was answered while the lock was
    held, then the response."""
    lock = service.smb_lock()
    await lock.acquire()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://unos") as client:
            task = asyncio.create_task(client.request(method, url, **kwargs))
            await _wait_for_lock_waiter()
            held = (list(world.fake.argvs()), task.done())
            lock.release()
            response = await asyncio.wait_for(task, timeout=10)
    finally:
        if lock.locked():
            lock.release()
    world.client.bodies.append(response.text)
    return held, response


class TestAccountFlowsWaitForTheLock:
    """ADR-0080: no host command and no write before smb_lock."""

    async def test_delete_user(self, world):
        world.make_active("alice", 70000, ALICE_PW)

        (calls, done), response = await _sent_while_locked(
            world, "DELETE", f"{API}/auth/users/{world.ids['alice']}", params={"username": "alice"},
            headers=world.headers("admin"))

        assert (calls, done) == ([], False)
        assert response.status_code == 200, response.text
        assert world.user("alice") is None and "alice" not in world.fake.passwd

    async def test_admin_enable(self, world):
        (calls, done), response = await _sent_while_locked(
            world, "POST", f"{API}/auth/users/{world.ids['alice']}/smb/enable",
            json={"username": "alice", "password": SET_PW}, headers=world.headers("admin"))

        assert (calls, done) == ([], False)
        assert response.status_code == 200, response.text
        assert response.json()["smb"]["state"] == "active"

    async def test_create_user_with_smb(self, world):
        """With ADR-0132 the insert itself waits: nothing is written before the lock."""
        lock = service.smb_lock()
        await lock.acquire()
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://unos") as client:
                task = asyncio.create_task(client.post(
                    f"{API}/auth/users", json={"username": "carol", "password": "Carol-pass-2026", "smb_enabled": True},
                    headers=world.headers("admin")))
                await _wait_for_lock_waiter()
                with Session(world.engine) as session:
                    inserted = session.scalars(select(User).where(User.username == "carol")).first()
                calls, done = list(world.fake.argvs()), task.done()
                lock.release()
                response = await asyncio.wait_for(task, timeout=10)
        finally:
            if lock.locked():
                lock.release()
        world.client.bodies.append(response.text)

        assert (inserted, calls, done) == (None, [], False)
        assert response.status_code == 200, response.text
        assert response.json()["smb"]["state"] == "active"

    async def test_create_user_runs_its_smb_step_under_the_lock(self, world, monkeypatch):
        """The test above shows only that the insert waits. The SMB step
        of POST /auth/users (smb_enabled) runs under the same smb_lock too: every host command
        of the activation, the smb.conf apply and the reloads of the user see the lock held."""
        lock = service.smb_lock()
        seen: list[tuple[str, bool]] = []
        fake_run = world.fake.run

        def run(cmd, **kwargs):
            seen.append((cmd[0], lock.locked()))  # in a worker thread: only reads the flag
            return fake_run(cmd, **kwargs)

        async def apply(db):
            seen.append(("apply", lock.locked()))
            return interfaces.ApplyResult()

        reload_user = accounts.reload_user

        async def reload(db, user_id):
            seen.append(("reload", lock.locked()))
            return await reload_user(db, user_id)

        monkeypatch.setattr("src.host.run_on_host", run)
        monkeypatch.setattr(accounts, "reload_user", reload)
        world.apply.side_effect = apply
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://unos") as client:
            response = await client.post(
                f"{API}/auth/users", json={"username": "carol", "password": "Carol-pass-2026", "smb_enabled": True},
                headers=world.headers("admin"))
        world.client.bodies.append(response.text)

        assert response.status_code == 200, response.text
        assert response.json()["smb"]["state"] == "active"
        assert [step for step, _ in seen if step in ("useradd", "smbpasswd", "apply")] == [
            "useradd", "smbpasswd", "apply"]
        assert [step for step, _ in seen].count("reload") >= 2  # before the activation, and for the answer
        assert [step for step, held in seen if not held] == []


# The device's smb.conf before the upgrade (no UNOS header): its [DATA] is open to every
# Samba user (write, no hosts allow).
OLD_SMB_CONF = """[global]
   workgroup = WORKGROUP
   server string = UNOS NAS
   security = user
   map to guest = Bad Password

[DATA]
   path = /mnt/pools/data
   read only = no
   guest ok = no
"""
NOT_UNOS = "SMB cannot be turned on until UNOS has installed its own smb.conf"


class TestActivationNeedsUnosSmbConf:
    """ADR-0130: while Samba still serves an smb.conf that UNOS did not write (its first
    apply failed), no Samba password is set: the new account would get
    what the old file gives every Samba user. Revocations are never blocked."""

    @pytest.fixture(autouse=True)
    def old_conf(self, world, monkeypatch):
        world.smb_conf.write_text(OLD_SMB_CONF)
        monkeypatch.setattr(service, "_last_apply", service.LastApply())
        monkeypatch.setattr(service, "_last_import_problem", None)

    # The last apply: none yet, failed, or succeeded (the file was replaced afterwards).
    @pytest.mark.parametrize("last_ok", [None, False, True])
    def test_self_activation(self, world, last_ok):
        """A fixed text in every case: the Shares page names no reason when the file was
        replaced after a successful apply, and the reasons carry host output."""
        service.last_apply().ok = last_ok
        if last_ok is False:
            service.last_apply().message = "testparm rejected the new smb.conf: Error loading services."
        world.make_allowed("alice")

        response = world.self_enable()

        assert response.status_code == 409
        assert response.json()["detail"] == f"{NOT_UNOS}; ask an administrator"
        assert world.fake.commands("smbpasswd") == []
        assert world.fake.passdb == {}
        assert world.fake.mutating_calls() == []
        row = world.row("alice")
        assert (row.uid, row.password_state) == (None, "needed")

    def test_admin_activation_names_the_failed_apply(self, world):
        service.last_apply().ok = False
        service.last_apply().message = "testparm rejected the new smb.conf: Error loading services."

        response = world.admin_enable("alice", SET_PW)

        assert response.status_code == 409
        assert response.json()["detail"] == (
            f"{NOT_UNOS}: smb.conf was not updated: testparm rejected the new smb.conf: Error loading services."
        )
        assert world.fake.mutating_calls() == [] and world.fake.passdb == {}
        assert bcrypt.checkpw(ALICE_PW.encode(), world.user("alice").hashed_password.encode())

    def test_admin_activation_names_the_failed_import(self, world, monkeypatch):
        monkeypatch.setattr(service, "_last_import_problem",
                            "Shares were not imported from smb.conf: The pool list could not be read")

        response = world.admin_enable("alice", SET_PW)

        assert response.json()["detail"] == (
            f"{NOT_UNOS}: Shares were not imported from smb.conf: The pool list could not be read"
        )

    @pytest.mark.parametrize("conf", ["old", "none", "empty"])
    def test_without_a_known_reason(self, world, conf):
        if conf == "none":
            world.smb_conf.unlink()
        elif conf == "empty":
            world.smb_conf.write_text("")

        response = world.admin_enable("alice", SET_PW)

        assert response.status_code == 409
        assert response.json()["detail"] == (
            f"{NOT_UNOS}: UNOS has not written it yet; it is written at the next change or restart"
        )
        assert world.fake.passdb == {}

    @pytest.mark.parametrize("conf", ["old", "none"])
    def test_replaced_after_unos_wrote_it(self, world, conf):
        """The last apply succeeded, then smb.conf was replaced (by hand, or by copying a
        backup back) or removed: the reason says so, not that UNOS has not written it."""
        if conf == "none":
            world.smb_conf.unlink()
        service.last_apply().ok = True

        response = world.admin_enable("alice", SET_PW)

        assert response.status_code == 409
        assert response.json()["detail"] == (
            f"{NOT_UNOS}: it was replaced or removed after UNOS wrote it; "
            "it is written again at the next change or restart"
        )
        assert world.fake.mutating_calls() == [] and world.fake.passdb == {}

    def test_create_user_with_smb(self, world):
        response = world.client.post(
            f"{API}/auth/users", json={"username": "carol", "password": "Carol-pass-2026", "smb_enabled": True},
            headers=world.headers("admin"),
        )

        assert response.status_code == 200, response.text
        assert response.json()["smb"]["state"] == "off"
        assert response.json()["warnings"] == [
            f"User created, but SMB access could not be turned on: {NOT_UNOS}: "
            "UNOS has not written it yet; it is written at the next change or restart"
        ]
        assert world.fake.passdb == {}

    def test_the_tool_check_comes_first(self, world):
        """The stand-in (no Samba) keeps its answer."""
        world.make_allowed("alice")
        world.fake.missing = {"smbpasswd", "pdbedit", "smbstatus"}

        response = world.self_enable()

        assert (response.status_code, response.json()["detail"]) == (503, TOOLS_MISSING)

    def test_allowing_is_not_blocked(self, world):
        assert world.admin_enable("alice").json()["smb"]["state"] == "password_needed"

    @pytest.mark.parametrize("flow", ["self-disable", "admin-disable", "delete", "promotion"])
    def test_revocations_are_never_blocked(self, world, flow):
        world.make_active("alice", 70000, ALICE_PW)

        response = {
            "self-disable": lambda: world.self_disable(),
            "admin-disable": lambda: world.admin_disable("alice"),
            "delete": lambda: world.delete_user("alice"),
            "promotion": lambda: world.put_user("alice", is_admin=True, username="alice"),
        }[flow]()

        assert response.status_code == 200, response.text
        assert world.fake.passdb == {}
        assert world.fake.commands("pdbedit", "-x") == [["pdbedit", "-x", "-u", "alice"]]

    def test_a_password_change_of_an_active_account_is_not_blocked(self, world):
        """An account that is active from before: a password change is not an activation (it
        grants nothing new), so it still reaches Samba."""
        world.make_active("alice", 70000, ALICE_PW)

        response = world.change_password("alice", ALICE_PW, NEW_PW)

        assert response.status_code == 200, response.text
        assert world.fake.passdb["alice"]["password"] == NEW_PW


# ---------------------------------------------------------------------------
# Collisions (ADR-0113)
# ---------------------------------------------------------------------------


class TestCollisions:
    def test_a_foreign_host_account(self, world):
        world.make_allowed("alice")
        world.fake.add_smb_group()
        foreign = world.fake.add_account("alice", 1001, gid=1001, gecos="Alice Liddell", home="/home/alice",
                                         shell="/bin/bash")
        world.fake.add_passdb("alice", "hand-made")

        response = world.self_enable()

        assert response.status_code == 409
        assert response.json()["detail"] == (
            "A host account named alice exists that UNOS did not create. Remove it on the host "
            "(pdbedit -x -u alice; userdel alice) or use another UNOS user."
        )
        assert world.fake.passwd["alice"] == foreign
        assert world.fake.passdb["alice"]["password"] == "hand-made"
        assert world.fake.mutating_calls() == []
        assert world.row("alice").uid is None

    def test_a_host_group_with_the_name(self, world):
        world.make_allowed("alice")
        world.fake.add_smb_group()
        world.fake.add_group("alice", 1500)

        response = world.self_enable()

        assert response.status_code == 409
        assert response.json()["detail"] == "A host group named alice exists; this name cannot be used for SMB"
        assert world.fake.mutating_calls() == []

    def test_a_samba_account_in_another_letter_case(self, world):
        world.make_allowed("alice")
        world.fake.add_smb_group()
        world.fake.add_passdb("Alice", "hand-made", uid=1001)

        response = world.self_enable()

        assert response.status_code == 409
        assert response.json()["detail"] == (
            "A Samba account named Alice exists that UNOS did not create; Samba treats it as alice. "
            "Remove it on the host (pdbedit -x -u Alice) or use another UNOS user."
        )
        assert world.fake.passdb["alice"] == {"name": "Alice", "password": "hand-made", "uid": 1001}
        assert world.fake.mutating_calls() == []

    def test_a_stale_marked_account_is_replaced(self, world):
        world.make_allowed("alice")
        world.set_row("alice", uid=70005)
        world.set_next_uid(70006)
        world.fake.add_smb_group()
        world.fake.add_account("alice", 70003)
        world.fake.add_passdb("alice", "old-password")
        pid = world.fake.add_session("alice", ["media"])

        response = world.self_enable()

        assert response.status_code == 200, response.text
        mutations = world.fake.mutating_calls()
        assert mutations[:4] == [
            ["pdbedit", "-x", "-u", "alice"],
            ["smbcontrol", str(pid), "shutdown"],
            ["userdel", "alice"],
            ["useradd", "-r", "-l", "-N", "-M", "-u", "70005", "-g", "70000", "-d", "/nonexistent",
             "-s", "/usr/sbin/nologin", "-c", "UNOS SMB user", "alice"],
        ]
        assert world.fake.passwd["alice"].uid == 70005
        assert world.row("alice").uid == 70005

    def test_a_busy_stale_account(self, world):
        world.make_allowed("alice")
        world.fake.add_smb_group()
        world.fake.add_account("alice", 70003)
        world.fake.busy["alice"] = 1

        response = world.self_enable()

        assert response.status_code == 409
        assert response.json()["detail"] == "The previous SMB account alice is still in use; try again later"
        assert world.fake.passwd["alice"].uid == 70003
        assert world.fake.commands("smbpasswd") == []
        assert world.row("alice").password_state == "needed"

    def test_a_deleted_users_uid_is_never_reused(self, world):
        world.make_allowed("alice")
        assert world.self_enable().json()["account"]["uid"] == 70000
        world.fake.add_session("alice", ["media"])
        world.fake.busy["alice"] = 3  # her connection keeps the account busy
        deleted = world.delete_user("alice")
        assert deleted.status_code == 200
        assert deleted.json()["warnings"] == [
            "The host account alice is still in use by an SMB connection; UNOS removes it at its next start"
        ]
        assert world.fake.passwd["alice"].uid == 70000  # the stale account remains

        # A new alice: the stale account is still busy, then it is removed.
        world.add_user("alice", ALICE_PW)
        world.make_allowed("alice")
        world.fake.busy["alice"] = 1
        busy = world.self_enable()
        assert busy.status_code == 409
        assert busy.json()["detail"] == "The previous SMB account alice is still in use; try again later"

        again = world.self_enable()
        assert again.status_code == 200, again.text
        assert again.json()["account"]["uid"] == 70001
        assert world.fake.passwd["alice"].uid == 70001

    def test_a_deleted_users_uid_is_never_reused_when_userdel_succeeded(self, world):
        world.make_allowed("alice")
        assert world.self_enable().json()["account"]["uid"] == 70000
        assert world.delete_user("alice").status_code == 200
        assert "alice" not in world.fake.passwd

        world.add_user("carol", "Carol-pass-2026")
        world.make_allowed("carol")
        assert world.self_enable("carol", "Carol-pass-2026").json()["account"]["uid"] == 70001

    def test_a_uid_taken_on_the_host_is_skipped(self, world):
        world.make_allowed("alice")
        world.fake.add_account("svc", 70000, gid=1000, gecos="service")

        response = world.self_enable()

        assert response.status_code == 200, response.text
        assert world.row("alice").uid == 70001
        assert world.next_uid() == 70002
        assert world.fake.passwd["svc"].uid == 70000

    def test_uids_exhausted(self, world):
        world.make_allowed("alice")
        world.set_next_uid(80000)

        response = world.self_enable()

        assert response.status_code == 409
        assert response.json()["detail"] == "No SMB user ids left (70000-79999)"
        assert world.row("alice").uid is None
        assert world.fake.commands("useradd") == []

    def test_the_group_with_another_gid(self, world):
        world.make_allowed("alice")
        world.fake.add_group("unos-smb", 1234)

        response = world.self_enable()

        assert response.status_code == 409
        assert response.json()["detail"] == (
            "The host group unos-smb exists with gid 1234; UNOS needs gid 70000. Remove or renumber it."
        )
        assert world.fake.mutating_calls() == []

    def test_gid_70000_taken_by_another_group(self, world):
        world.make_allowed("alice")
        world.fake.add_group("media", 70000)

        response = world.self_enable()

        assert response.status_code == 409
        assert response.json()["detail"] == "gid 70000 is used by group media"
        assert world.fake.mutating_calls() == []


# ---------------------------------------------------------------------------
# Password changes (ADR-0121, ADR-0123)
# ---------------------------------------------------------------------------


class TestPasswordChange:
    def test_self_change_of_an_active_account(self, world, monkeypatch):
        world.make_active("alice", 70000, ALICE_PW)
        pid = world.fake.add_session("alice", ["media"])
        marks = calls_at_commits(world, monkeypatch)

        response = world.change_password("alice", ALICE_PW, NEW_PW)

        assert response.status_code == 200, response.text
        assert response.json() == {"status": "ok", "message": "Password changed successfully", "warnings": []}
        assert world.fake.passdb["alice"]["password"] == NEW_PW
        assert bcrypt.checkpw(NEW_PW.encode(), world.user("alice").hashed_password.encode())
        # Samba's copy first, then the commit of the hash; the session opened with
        # the old password ends after the commit.
        [commit] = marks
        argvs = world.fake.argvs()
        assert argvs.index(["smbpasswd", "-s", "-a", "alice"]) < commit
        assert argvs[commit:] == [["smbstatus", "--json"], ["smbcontrol", str(pid), "shutdown"]]
        assert world.fake.sessions == []
        assert world.row("alice").password_set_at > datetime(2026, 11, 1, 9, 0)

    def test_admin_reset_of_an_active_account(self, world, monkeypatch):
        world.make_active("alice", 70000, ALICE_PW)
        pid = world.fake.add_session("alice", ["media"])
        marks = calls_at_commits(world, monkeypatch)

        response = world.put_user("alice", password=NEW_PW, username="alice")

        assert response.status_code == 200, response.text
        assert response.json()["smb"]["state"] == "active"
        assert world.fake.passdb["alice"]["password"] == NEW_PW
        assert bcrypt.checkpw(NEW_PW.encode(), world.user("alice").hashed_password.encode())
        # smbpasswd before the commit of the hash, the session ended after it.
        [commit] = marks
        argvs = world.fake.argvs()
        assert argvs.index(["smbpasswd", "-s", "-a", "alice"]) < commit
        assert argvs[commit:] == [["smbstatus", "--json"], ["smbcontrol", str(pid), "shutdown"]]
        # ADR-0320: the SMB password set by an administrator has its own audit entry.
        assert world.smb_audit() == [("alice", "SMB password set by an administrator", "info")]

    @pytest.mark.parametrize("via", ["self", "admin"])
    def test_a_host_account_that_is_no_longer_unos(self, world, via):
        """The host's administrator replaced alice's account after her activation (pdbedit -x;
        userdel; useradd): smbpasswd never writes a passdb entry for the foreign account."""
        world.make_active("alice", 70000, ALICE_PW)
        del world.fake.passdb["alice"]
        world.fake.add_account("alice", 1001, gid=1001, gecos="Alice", home="/home/alice", shell="/bin/bash")

        if via == "self":
            response = world.change_password("alice", ALICE_PW, NEW_PW)
        else:
            response = world.put_user("alice", password=NEW_PW)

        assert response.status_code == 409
        assert response.json()["detail"] == FOREIGN_ALICE
        assert world.fake.commands("smbpasswd") == []
        assert "alice" not in world.fake.passdb
        assert bcrypt.checkpw(ALICE_PW.encode(), world.user("alice").hashed_password.encode())

    @pytest.mark.parametrize("via", ["self", "admin"])
    def test_a_samba_account_in_another_letter_case(self, world, via):
        world.make_active("alice", 70000, ALICE_PW)
        del world.fake.passdb["alice"]
        world.fake.add_passdb("Alice", "hand-made", uid=1001)

        if via == "self":
            response = world.change_password("alice", ALICE_PW, NEW_PW)
        else:
            response = world.put_user("alice", password=NEW_PW)

        assert response.status_code == 409
        assert response.json()["detail"] == OTHER_CASE_ALICE
        assert world.fake.commands("smbpasswd") == []
        assert world.fake.passdb["alice"] == {"name": "Alice", "password": "hand-made", "uid": 1001}
        assert bcrypt.checkpw(ALICE_PW.encode(), world.user("alice").hashed_password.encode())

    @pytest.mark.parametrize("via", ["self", "admin"])
    def test_smbpasswd_failure_keeps_the_old_password(self, world, via):
        world.make_active("alice", 70000, ALICE_PW)
        world.fake.fail[("smbpasswd",)] = (1, "tdb_store failed")

        if via == "self":
            response = world.change_password("alice", ALICE_PW, NEW_PW)
        else:
            response = world.put_user("alice", password=NEW_PW)

        assert response.status_code == 503
        assert response.json()["detail"] == (
            "Samba did not accept the password: tdb_store failed" if via == "admin"
            else f"The password was not changed: {SELF_HOST_FAILED}"
        )
        assert bcrypt.checkpw(ALICE_PW.encode(), world.user("alice").hashed_password.encode())
        assert world.fake.passdb["alice"]["password"] == ALICE_PW

    @pytest.mark.parametrize("via", ["self", "admin"])
    def test_not_active_no_host_calls(self, world, via):
        world.make_allowed("alice")

        if via == "self":
            response = world.change_password("alice", ALICE_PW, NEW_PW)
        else:
            response = world.put_user("alice", password=NEW_PW)

        assert response.status_code == 200, response.text
        assert world.fake.calls == []
        assert bcrypt.checkpw(NEW_PW.encode(), world.user("alice").hashed_password.encode())
        # A password change never turns SMB on.
        assert world.row("alice").password_state == "needed"

    @pytest.mark.parametrize("via", ["self", "admin"])
    def test_a_short_password_for_an_active_account(self, world, via):
        world.make_active("alice", 70000, ALICE_PW)

        if via == "self":
            response = world.change_password("alice", ALICE_PW, SHORT_PW)
        else:
            response = world.put_user("alice", password=SHORT_PW)

        assert response.status_code == 400
        assert response.json()["detail"] == "SMB passwords must have at least 8 characters"
        assert world.fake.calls == []
        assert bcrypt.checkpw(ALICE_PW.encode(), world.user("alice").hashed_password.encode())

    def test_users_without_smb_keep_the_old_behaviour(self, world):
        response = world.change_password("bob", BOB_PW, SHORT_PW)

        assert response.status_code == 200
        assert response.json()["warnings"] == []
        assert world.fake.calls == []
        assert world.change_password("bob", "wrong-password", BOB_PW).status_code == 400

    @pytest.mark.parametrize("name", ["bob", "alice"])
    def test_the_old_password_no_longer_signs_in_and_the_new_one_does(self, world, name):
        # bob has no SMB; alice's account is active, so Samba's copy changes too.
        old = {"bob": BOB_PW, "alice": ALICE_PW}[name]
        if name == "alice":
            world.make_active("alice", 70000, ALICE_PW)

        assert world.change_password(name, old, NEW_PW).status_code == 200

        refused = world.client.post(f"{API}/auth/login", json={"username": name, "password": old})
        assert refused.status_code == 401
        assert refused.json()["detail"] == "Incorrect username or password"
        signed_in = world.client.post(f"{API}/auth/login", json={"username": name, "password": NEW_PW})
        assert signed_in.status_code == 200, signed_in.text
        me = world.client.get(f"{API}/auth/me",
                              headers={"Authorization": f"Bearer {signed_in.json()['access_token']}"})
        assert me.json()["username"] == name

    @pytest.mark.parametrize("body", [{"current_password": BOB_PW}, {"new_password": NEW_PW}, {}],
                             ids=["no-new-password", "no-current-password", "empty"])
    def test_a_missing_field_is_refused(self, world, body):
        response = world.client.post(f"{API}/auth/change-password", json=body, headers=world.headers("bob"))

        assert response.status_code == 422
        assert bcrypt.checkpw(BOB_PW.encode(), world.user("bob").hashed_password.encode())


class TestCommonPasswords:
    """shares.smb-login-protection, step 2: a password on the list of common passwords is refused with
    the reason when SMB is turned on with it, and when a user with SMB changes to it (ADR-0279). The
    answer never carries the password (ADR-0125)."""

    def _refused(self, world, response) -> None:
        assert response.status_code == 400, response.text
        assert response.json()["detail"] == COMMON_RULE
        assert COMMON_PW not in response.text and COMMON_PW.lower() not in response.text
        assert world.fake.calls == []

    def test_creating_a_user_with_smb_creates_nothing(self, world):
        response = world.client.post(
            f"{API}/auth/users", json={"username": "carol", "password": COMMON_PW, "smb_enabled": True},
            headers=world.headers("admin"),
        )

        self._refused(world, response)
        with Session(world.engine) as session:
            assert session.scalars(select(User).where(User.username == "carol")).first() is None
        assert world.audit() == []

    def test_an_administrator_turning_smb_on_with_it(self, world):
        world.make_allowed("alice")

        response = world.admin_enable("alice", COMMON_PW)

        self._refused(world, response)
        assert world.row("alice").password_state == "needed"
        assert bcrypt.checkpw(ALICE_PW.encode(), world.user("alice").hashed_password.encode())
        assert world.smb_audit() == []

    def test_a_user_turning_smb_on_with_a_unos_password_that_is_common(self, world):
        # The API takes common UNOS passwords for users without SMB (ADR-0279).
        world.add_user("carol", COMMON_PW)
        world.make_allowed("carol")

        response = world.self_enable("carol", COMMON_PW)

        self._refused(world, response)
        assert world.row("carol").password_state == "needed"
        assert "carol" not in world.fake.passdb

    @pytest.mark.parametrize("via", ["self", "admin"])
    def test_an_active_account_changing_to_it(self, world, via):
        world.make_active("alice", 70000, ALICE_PW)

        if via == "self":
            response = world.change_password("alice", ALICE_PW, COMMON_PW)
        else:
            response = world.put_user("alice", password=COMMON_PW)

        self._refused(world, response)
        assert world.fake.passdb["alice"]["password"] == ALICE_PW
        assert bcrypt.checkpw(ALICE_PW.encode(), world.user("alice").hashed_password.encode())

    @pytest.mark.parametrize("password", ["password123", "PASSWORD123", "pAsSwOrD123"])
    def test_the_list_is_compared_ignoring_case(self, world, password):
        world.make_active("alice", 70000, ALICE_PW)

        response = world.change_password("alice", ALICE_PW, password)

        assert response.status_code == 400
        assert response.json()["detail"] == COMMON_RULE
        assert world.fake.calls == []

    def test_users_without_smb_may_have_one(self, world):
        """Only SMB-bound passwords are checked: the web sign-in has its rate limit and 2FA."""
        world.make_allowed("alice")

        created = world.client.post(
            f"{API}/auth/users", json={"username": "carol", "password": COMMON_PW}, headers=world.headers("admin")
        )
        changed = world.change_password("bob", BOB_PW, COMMON_PW)
        reset = world.put_user("alice", password=COMMON_PW)

        assert [created.status_code, changed.status_code, reset.status_code] == [200, 200, 200]
        assert world.fake.calls == []
        assert bcrypt.checkpw(COMMON_PW.encode(), world.user("bob").hashed_password.encode())
        # Allowed but not active: the password reaches Samba only when SMB is turned on with it.
        assert world.row("alice").password_state == "needed"
        assert world.self_enable("alice", COMMON_PW).json()["detail"] == COMMON_RULE

    def test_the_e2e_fakes_answer_with_the_servers_text(self):
        """webui/tests/shares-fakes.ts answers with the server's text word for word (ADR-0189)."""
        fakes = (SRC.parents[1] / "webui" / "tests" / "shares-fakes.ts").read_text(encoding="utf-8")

        assert constants.COMMON_PASSWORD_RULE == COMMON_RULE
        assert f"'{COMMON_RULE}'" in fakes


class TestCommitFailureAfterSmbpasswd:
    def test_activation(self, world, failing_commit):
        world.make_allowed("alice")
        failing_commit.fail_at = 2  # 1: the uid reservation, 2: the activation

        response = world.self_enable()

        assert response.status_code == 503
        assert response.json()["detail"] == NOTHING_CHANGED
        assert world.fake.commands("pdbedit", "-x") == [["pdbedit", "-x", "-u", "alice"]]
        assert world.fake.passdb == {}
        row = world.row("alice")
        assert (row.password_state, row.uid) == ("needed", 70000)
        world.apply.assert_not_awaited()

    def test_self_change(self, world, failing_commit):
        world.make_active("alice", 70000, ALICE_PW)
        failing_commit.fail_at = 1

        response = world.change_password("alice", ALICE_PW, NEW_PW)

        assert response.status_code == 503
        assert response.json()["detail"] == NOTHING_CHANGED
        assert world.fake.stdin_of("smbpasswd") == [f"{NEW_PW}\n{NEW_PW}\n", f"{ALICE_PW}\n{ALICE_PW}\n"]
        assert world.fake.passdb["alice"]["password"] == ALICE_PW
        assert bcrypt.checkpw(ALICE_PW.encode(), world.user("alice").hashed_password.encode())
        assert world.row("alice").password_state == "set"

    def test_self_change_when_the_old_password_cannot_be_restored(self, world, failing_commit, monkeypatch):
        world.make_active("alice", 70000, ALICE_PW)
        failing_commit.fail_at = 1
        real_run = world.fake.run
        count = {"smbpasswd": 0}

        def run(cmd, **kwargs):
            if cmd[0] == "smbpasswd":
                count["smbpasswd"] += 1
                if count["smbpasswd"] == 2:
                    world.fake.calls.append((list(cmd), kwargs.get("input")))
                    return world.fake._result(1, stderr="tdb_store failed")
            return real_run(cmd, **kwargs)

        monkeypatch.setattr("src.host.run_on_host", run)

        response = world.change_password("alice", ALICE_PW, NEW_PW)

        assert response.status_code == 503
        assert response.json()["detail"] == NOTHING_CHANGED
        assert world.fake.passdb == {}
        assert world.row("alice").password_state == "needed"

    def test_self_change_puts_back_a_common_password_that_samba_held(self, world, failing_commit):
        """An account that got a common password before the list (ADR-0279) keeps SMB when a change
        from it fails: the list applies to new passwords, not to the one that goes back (ADR-0124)."""
        world.add_user("carol", COMMON_PW)
        world.make_active("carol", 70000, COMMON_PW)
        pid = world.fake.add_session("carol", ["media"])
        failing_commit.fail_at = 1

        response = world.change_password("carol", COMMON_PW, NEW_PW)

        assert response.status_code == 503
        assert response.json()["detail"] == NOTHING_CHANGED
        assert world.fake.stdin_of("smbpasswd") == [f"{NEW_PW}\n{NEW_PW}\n", f"{COMMON_PW}\n{COMMON_PW}\n"]
        assert world.fake.passdb["carol"]["password"] == COMMON_PW
        assert world.fake.commands("pdbedit", "-x") == []
        assert bcrypt.checkpw(COMMON_PW.encode(), world.user("carol").hashed_password.encode())
        assert world.row("carol").password_state == "set"
        assert world.fake.session_pids() == [pid]

    def test_admin_reset(self, world, failing_commit):
        world.make_active("alice", 70000, ALICE_PW)
        pid = world.fake.add_session("alice", ["media"])
        failing_commit.fail_at = 1

        response = world.put_user("alice", password=NEW_PW)

        assert response.status_code == 503
        assert response.json()["detail"] == NOTHING_CHANGED
        assert world.fake.commands("pdbedit", "-x") == [["pdbedit", "-x", "-u", "alice"]]
        assert world.fake.passdb == {}
        # Read back in a fresh session: the account needs a password again.
        row = world.row("alice")
        assert (row.password_state, row.password_set_at, row.enabled) == ("needed", None, True)
        assert bcrypt.checkpw(ALICE_PW.encode(), world.user("alice").hashed_password.encode())
        assert pid not in world.fake.session_pids()

    def test_admin_password_set_on_an_active_account(self, world, failing_commit):
        world.make_active("alice", 70000, ALICE_PW)
        failing_commit.fail_at = 2  # 1: the reservation (the uid is confirmed), 2: the activation

        response = world.admin_enable("alice", SET_PW)

        assert response.status_code == 503
        assert response.json()["detail"] == NOTHING_CHANGED
        assert world.fake.passdb == {}
        assert world.row("alice").password_state == "needed"

    @pytest.mark.parametrize("via", ["self", "admin"])
    def test_compensation_that_fails_too(self, world, failing_commit, via):
        world.make_allowed("alice")
        failing_commit.fail_at = 2
        world.fake.fail[("pdbedit", "-x")] = (1, "tdb locked")

        response = world.self_enable() if via == "self" else world.admin_enable("alice", SET_PW)

        assert response.status_code == 503
        # A regular user's own flow names no host output (ADR-0131; TestSelfFlowErrors).
        assert response.json()["detail"] == (
            "The database was busy and Samba could not be reset: pdbedit could not remove the Samba "
            "account alice: tdb locked; SMB access is repaired at the next start" if via == "admin"
            else "The database was busy and Samba could not be reset; SMB access is repaired at the next start"
        )

    def test_admin_reset_when_the_row_cannot_be_marked(self, world, failing_commit):
        """The compensation has two steps: when the row cannot be set to 'needed', it failed."""
        world.make_active("alice", 70000, ALICE_PW)
        pid = world.fake.add_session("alice", ["media"])
        failing_commit.fail_at = {1, 2}  # 1: the reset, 2: the row set to 'needed' in a fresh session

        response = world.put_user("alice", password=NEW_PW, username="alice")

        assert response.status_code == 503
        assert response.json()["detail"] == (
            "The database was busy and Samba could not be reset: the SMB account could not be marked as "
            "needing a password; SMB access is repaired at the next start"
        )
        assert world.fake.passdb == {}
        # The row still says 'set' (rule 3d of the next start repairs it); no session survives.
        assert world.row("alice").password_state == "set"
        assert pid not in world.fake.session_pids()
        assert bcrypt.checkpw(ALICE_PW.encode(), world.user("alice").hashed_password.encode())


class TestCommitFailureAfterRevocation:
    """The revoke step removed the passdb entry, then the commit failed: the row must not
    say active while Samba has no password for it, and the sessions end (ADR-0104)."""

    def test_delete(self, world, failing_commit):
        world.make_active("alice", 70000, ALICE_PW)
        world.add_share("media", ("alice", "write"))
        pid = world.fake.add_session("alice", ["media"])
        failing_commit.fail_at = 1

        response = world.delete_user("alice")

        assert response.status_code == 503
        assert response.json()["detail"] == DATABASE_BUSY
        assert world.user("alice") is not None
        row = world.row("alice")
        assert (row.enabled, row.password_state, row.password_set_at, row.uid) == (True, "needed", None, 70000)
        assert world.access_rows("alice") != []
        assert world.fake.passdb == {}
        assert pid not in world.fake.session_pids()
        assert world.fake.commands("userdel") == []
        world.apply.assert_not_awaited()

    def test_promotion(self, world, failing_commit):
        world.make_active("alice", 70000, ALICE_PW)
        world.add_share("media", ("alice", "write"))
        pid = world.fake.add_session("alice", ["media"])
        failing_commit.fail_at = 1

        response = world.put_user("alice", is_admin=True, username="alice")

        assert response.status_code == 503
        assert response.json()["detail"] == DATABASE_BUSY
        assert world.user("alice").is_admin is False
        row = world.row("alice")
        assert (row.enabled, row.password_state, row.password_set_at) == (True, "needed", None)
        assert world.access_rows("alice") != []
        assert world.fake.passdb == {}
        assert pid not in world.fake.session_pids()
        world.apply.assert_not_awaited()
        assert world.smb_audit() == []

    @pytest.mark.parametrize("via", ["disallow", "deactivate"])
    def test_turning_smb_off(self, world, failing_commit, via):
        world.make_active("alice", 70000, ALICE_PW)
        pid = world.fake.add_session("alice", ["media"])
        failing_commit.fail_at = 1

        response = world.admin_disable("alice") if via == "disallow" else world.self_disable()

        assert response.status_code == 503
        assert response.json()["detail"] == DATABASE_BUSY
        row = world.row("alice")
        assert (row.enabled, row.password_state, row.password_set_at) == (True, "needed", None)
        assert world.fake.passdb == {}
        assert pid not in world.fake.session_pids()
        world.apply.assert_not_awaited()

    def test_when_the_row_cannot_be_marked_either(self, world, failing_commit):
        world.make_active("alice", 70000, ALICE_PW)
        pid = world.fake.add_session("alice", ["media"])
        failing_commit.fail_at = {1, 2}

        response = world.delete_user("alice")

        assert response.status_code == 503
        assert response.json()["detail"] == DATABASE_BUSY
        assert world.row("alice").password_state == "set"  # rule 3d of the next start repairs it
        assert world.fake.passdb == {}
        assert pid not in world.fake.session_pids()


class TestWriteFailureAfterRevocation:
    """A full disk fails the first write after the revoke step, before the commit (once
    SQLite's page cache spills into unos.db-wal, ADR-0306): every write up to the commit gets
    the compensation of a failed commit (the row 'needed', the sessions ended, 503)."""

    @pytest.mark.parametrize("failure", ["delete_access", "flush"])
    def test_promotion(self, world, monkeypatch, failure):
        world.make_active("alice", 70000, ALICE_PW)
        share = world.add_share("media", ("alice", "write"))
        pid = world.fake.add_session("alice", ["media"])
        if failure == "delete_access":
            world.delete_access.side_effect = _disk_full("DELETE FROM smb_share_access")
        else:
            failed = flush_fails_after_the_revocation(world, monkeypatch, "alice")

        response = world.put_user("alice", is_admin=True, username="alice")

        assert response.status_code == 503
        assert response.json()["detail"] == DATABASE_BUSY
        if failure == "flush":
            assert failed == [["SmbAccount"]]  # the row's UPDATE, autoflushed by delete_access_of_user
        assert world.user("alice").is_admin is False
        row = world.row("alice")
        assert (row.enabled, row.password_state, row.password_set_at) == (True, "needed", None)
        assert world.access_rows("alice") == [(share, "write")]
        assert world.fake.commands("pdbedit", "-x") == [["pdbedit", "-x", "-u", "alice"]]
        assert world.fake.passdb == {}
        assert pid not in world.fake.session_pids()
        world.apply.assert_not_awaited()
        assert world.smb_audit() == []

    def test_promotion_without_an_account_row(self, world):
        share = world.add_share("media", ("bob", "read"))
        world.delete_access.side_effect = _disk_full("DELETE FROM smb_share_access")

        response = world.put_user("bob", is_admin=True, username="bob")

        assert response.status_code == 503
        assert response.json()["detail"] == DATABASE_BUSY
        assert world.user("bob").is_admin is False
        assert world.access_rows("bob") == [(share, "read")]
        assert world.fake.calls == []  # nothing was revoked, so nothing is compensated

    @pytest.mark.parametrize("failure, access", [
        ("delete_access", True),  # the DELETE of the access rows
        ("flush", True),          # db.delete(user), while the access rows' DELETE holds the write lock
        ("flush", False),         # db.delete(user) loads the API tokens, which autoflushes the row's DELETE
    ])
    def test_delete(self, world, monkeypatch, failure, access):
        world.make_active("alice", 70000, ALICE_PW)
        share = world.add_share("media", ("alice", "write")) if access else None
        pid = world.fake.add_session("alice", ["media"])
        if failure == "delete_access":
            world.delete_access.side_effect = _disk_full("DELETE FROM smb_share_access")
        else:
            failed = flush_fails_after_the_revocation(world, monkeypatch, "alice")

        response = world.delete_user("alice")

        assert response.status_code == 503
        assert response.json()["detail"] == DATABASE_BUSY
        if failure == "flush":
            assert failed == [["SmbAccount"]]  # the account row's DELETE
        assert world.user("alice") is not None
        row = world.row("alice")
        assert (row.enabled, row.password_state, row.password_set_at, row.uid) == (True, "needed", None, 70000)
        assert world.access_rows("alice") == ([(share, "write")] if access else [])
        assert world.fake.commands("pdbedit", "-x") == [["pdbedit", "-x", "-u", "alice"]]
        assert world.fake.passdb == {}
        assert pid not in world.fake.session_pids()
        assert world.fake.commands("userdel") == []
        world.apply.assert_not_awaited()

    def test_the_last_admin_check_after_the_revocation(self, world, monkeypatch):
        """carol, an administrator, still has a UNOS passdb entry (a start without the Samba
        tools skipped its removal). While her deletion revokes it, another request takes the
        role from the deleting administrator: the last-admin 409 passes through, after the
        same compensation."""
        world.add_user("carol", "Carol-pass-2026", admin=True)
        world.fake.add_smb_group()
        world.fake.add_account("carol", 70001)
        world.fake.add_passdb("carol", "Carol-pass-2026")
        world.set_row("carol", enabled=False, password_state="needed", uid=70001)
        pid = world.fake.add_session("carol")
        real_run = world.fake.run

        def run(cmd, **kwargs):
            if cmd[:2] == ["pdbedit", "-x"]:
                with Session(world.engine) as session:
                    session.get(User, world.ids["admin"]).is_admin = False
                    session.commit()
            return real_run(cmd, **kwargs)

        monkeypatch.setattr("src.host.run_on_host", run)

        response = world.delete_user("carol")

        assert response.status_code == 409
        assert response.json()["detail"] == auth_router.LAST_ADMIN_DETAIL
        assert world.user("carol") is not None
        assert world.row("carol").password_state == "needed"
        assert world.fake.passdb == {}
        assert pid not in world.fake.session_pids()
        assert world.fake.commands("userdel") == []


class TestSmbpasswdTimeout:
    """smbpasswd may store the password before it times out, or even after: Samba's
    password is unknown, so the flows compensate as after a failed commit (ADR-0124)."""

    def test_self_change(self, world, monkeypatch):
        world.make_active("alice", 70000, ALICE_PW)
        pid = world.fake.add_session("alice", ["media"])
        smbpasswd_times_out_after_writing(world, monkeypatch)

        response = world.change_password("alice", ALICE_PW, NEW_PW)

        assert response.status_code == 503
        assert response.json()["detail"] == SMBPASSWD_TIMEOUT
        # The current password went back to Samba; UNOS kept it too.
        assert world.fake.stdin_of("smbpasswd") == [f"{NEW_PW}\n{NEW_PW}\n", f"{ALICE_PW}\n{ALICE_PW}\n"]
        assert world.fake.passdb["alice"]["password"] == ALICE_PW
        assert bcrypt.checkpw(ALICE_PW.encode(), world.user("alice").hashed_password.encode())
        assert world.row("alice").password_state == "set"
        assert world.fake.session_pids() == [pid]  # nothing changed for the open session

    def test_self_change_puts_back_a_common_password_that_samba_held(self, world, monkeypatch):
        """As TestCommitFailureAfterSmbpasswd: the password that goes back is not checked against the list."""
        world.add_user("carol", COMMON_PW)
        world.make_active("carol", 70000, COMMON_PW)
        pid = world.fake.add_session("carol", ["media"])
        smbpasswd_times_out_after_writing(world, monkeypatch)

        response = world.change_password("carol", COMMON_PW, NEW_PW)

        assert response.status_code == 503
        assert response.json()["detail"] == SMBPASSWD_TIMEOUT
        assert world.fake.stdin_of("smbpasswd") == [f"{NEW_PW}\n{NEW_PW}\n", f"{COMMON_PW}\n{COMMON_PW}\n"]
        assert world.fake.passdb["carol"]["password"] == COMMON_PW
        assert world.fake.commands("pdbedit", "-x") == []
        assert bcrypt.checkpw(COMMON_PW.encode(), world.user("carol").hashed_password.encode())
        assert world.row("carol").password_state == "set"
        assert world.fake.session_pids() == [pid]

    def test_admin_reset(self, world, monkeypatch):
        world.make_active("alice", 70000, ALICE_PW)
        pid = world.fake.add_session("alice", ["media"])
        smbpasswd_times_out_after_writing(world, monkeypatch)

        response = world.put_user("alice", password=NEW_PW, username="alice")

        assert response.status_code == 503
        assert response.json()["detail"] == SMBPASSWD_TIMEOUT
        assert world.fake.passdb == {}
        row = world.row("alice")
        assert (row.enabled, row.password_state, row.password_set_at) == (True, "needed", None)
        assert pid not in world.fake.session_pids()
        assert bcrypt.checkpw(ALICE_PW.encode(), world.user("alice").hashed_password.encode())

    def test_admin_password_set_on_an_active_account(self, world, monkeypatch):
        world.make_active("alice", 70000, ALICE_PW)
        pid = world.fake.add_session("alice", ["media"])
        smbpasswd_times_out_after_writing(world, monkeypatch)

        response = world.admin_enable("alice", SET_PW)

        assert response.status_code == 503
        assert response.json()["detail"] == SMBPASSWD_TIMEOUT
        assert world.fake.passdb == {}
        assert world.row("alice").password_state == "needed"
        assert pid not in world.fake.session_pids()
        assert bcrypt.checkpw(ALICE_PW.encode(), world.user("alice").hashed_password.encode())
        world.apply.assert_not_awaited()

    def test_first_activation(self, world, monkeypatch):
        world.make_allowed("alice")
        smbpasswd_times_out_after_writing(world, monkeypatch)

        response = world.self_enable()

        assert response.status_code == 503
        assert response.json()["detail"] == SMBPASSWD_TIMEOUT
        assert world.fake.commands("pdbedit", "-x") == [["pdbedit", "-x", "-u", "alice"]]
        assert world.fake.passdb == {}
        assert world.row("alice").password_state == "needed"
        world.apply.assert_not_awaited()

    @pytest.mark.parametrize("via", ["self", "admin"])
    def test_compensation_that_fails_too(self, world, monkeypatch, via):
        world.make_active("alice", 70000, ALICE_PW)
        smbpasswd_times_out_after_writing(world, monkeypatch, times=2)  # the restore times out too
        world.fake.fail[("pdbedit", "-x")] = (1, "tdb locked")

        if via == "self":
            response = world.change_password("alice", ALICE_PW, NEW_PW)
        else:
            response = world.put_user("alice", password=NEW_PW, username="alice")

        assert response.status_code == 503
        # A regular user's own flow names no host output (ADR-0131; TestSelfFlowErrors).
        assert response.json()["detail"] == (
            "smbpasswd did not answer in time and Samba could not be reset: pdbedit could not remove the "
            "Samba account alice: tdb locked; SMB access is repaired at the next start" if via == "admin"
            else "smbpasswd did not answer in time and Samba could not be reset; SMB access is repaired at the "
            "next start"
        )
        assert world.row("alice").password_state == "needed"
        assert bcrypt.checkpw(ALICE_PW.encode(), world.user("alice").hashed_password.encode())
        if via == "self":
            assert "pdbedit could not remove the Samba account alice: tdb locked" in _logged(world)

    @pytest.mark.parametrize("via", ["self", "admin"])
    @pytest.mark.parametrize("command", [("getent", "passwd", "alice"), ("pdbedit", "-L")])
    def test_a_read_before_smbpasswd(self, world, monkeypatch, command, via):
        """The checks before smbpasswd (the host account, passdb) timed out: nothing was
        written to Samba, so the answer is a plain 503 and nothing is compensated."""
        world.make_active("alice", 70000, ALICE_PW)
        pid = world.fake.add_session("alice", ["media"])
        times_out_once(world, monkeypatch, *command)

        if via == "self":
            response = world.change_password("alice", ALICE_PW, NEW_PW)
        else:
            response = world.put_user("alice", password=NEW_PW, username="alice")

        assert response.status_code == 503
        assert response.json()["detail"] == f"{command[0]} did not answer in time"
        assert world.fake.commands(*command) == [list(command)]
        assert world.fake.commands("smbpasswd") == []
        assert world.fake.commands("pdbedit", "-x") == []
        assert world.fake.passdb == {"alice": {"name": "alice", "password": ALICE_PW, "uid": 70000}}
        assert bcrypt.checkpw(ALICE_PW.encode(), world.user("alice").hashed_password.encode())
        row = world.row("alice")
        assert (row.enabled, row.password_state, row.password_set_at) == (True, "set", datetime(2026, 11, 1, 9, 0))
        assert world.fake.session_pids() == [pid]


class TestApplyWarnings:
    """Only the texts of smb.SambaConfigError reach the answers of administrators; the self
    flows answer fixed texts (TestSelfFlowWarnings)."""

    @pytest.mark.parametrize("via", ["admin", "self"])
    def test_a_database_error_is_not_shown(self, world, via):
        world.make_active("alice", 70000, ALICE_PW)
        world.apply.side_effect = OperationalError(
            "SELECT smb_shares.id, smb_shares.path FROM smb_shares", {}, Exception("database is locked")
        )

        response = world.admin_disable("alice") if via == "admin" else world.self_disable()

        assert response.status_code == 200, response.text
        assert response.json()["warnings"] == [
            "SMB access changed, but smb.conf could not be updated; it is written at the next change or restart"
            if via == "admin" else SELF_APPLY_WARNING
        ]
        assert "SELECT" not in response.text and "smb_shares" not in response.text

    def test_a_samba_config_error_is_shown(self, world):
        world.make_active("alice", 70000, ALICE_PW)
        world.apply.side_effect = smb.SambaConfigError("testparm rejected the new smb.conf: Unknown parameter")

        response = world.admin_disable("alice")

        assert response.status_code == 200, response.text
        assert response.json()["warnings"] == [
            "SMB access changed, but smb.conf could not be updated: testparm rejected the new smb.conf: "
            "Unknown parameter; it is written at the next change or restart"
        ]


# What install_smb_config returns while Samba's reload fails and a close-share of another
# user's share fails: host stderr, host paths and that share's name.
HOST_APPLY_WARNINGS = [
    "Samba did not reload smb.conf (smbcontrol: Failed to send message to all: NT_STATUS_ACCESS_DENIED\n"
    "debug: lock directory /run/samba, private dir /var/lib/samba/private; systemctl: Job for smbd.service "
    "failed.\nSee \"journalctl -xeu smbd.service\".); the reload is tried again at the next change",
    "Open connections to alice_private could not be closed: smbcontrol: Can't find pid for smbd",
]


def _leaks(warnings: list[str]) -> list[str]:
    return [w for w in warnings if "alice_private" in w or "NT_STATUS" in w or "/var/lib" in w or "\n" in w
            or "smbcontrol" in w]


class TestSelfFlowWarnings:
    """ADR-0131: the answers of a regular user's own flows carry fixed warnings: no host
    output, no paths, no names of other users' shares. The details are logged; the
    administrator's flows keep the full texts."""

    def test_turning_smb_off(self, world):
        world.make_active("bob", 70001, BOB_PW)
        world.apply.return_value = interfaces.ApplyResult(changed=True, warnings=list(HOST_APPLY_WARNINGS))
        world.fake.add_session("bob", ["media"])
        world.fake.fail[("smbcontrol",)] = (1, "Can't find pid 40000 (smbd) at /run/samba/smbd.pid")

        response = world.self_disable("bob")

        assert response.status_code == 200, response.text
        assert response.json()["warnings"] == [SELF_APPLY_WARNING, SELF_SESSIONS_WARNING]
        assert _leaks(response.json()["warnings"]) == []
        logged = "\n".join(r.getMessage() for r in world.caplog.records)
        assert "alice_private could not be closed" in logged
        assert "An SMB connection of bob could not be ended" in logged

    def test_turning_smb_on(self, world):
        world.make_allowed("bob")
        world.apply.return_value = interfaces.ApplyResult(changed=True, warnings=list(HOST_APPLY_WARNINGS))

        response = world.self_enable("bob", BOB_PW)

        assert response.status_code == 200, response.text
        assert response.json()["account"]["state"] == "active"
        assert response.json()["warnings"] == [SELF_APPLY_WARNING]

    def test_turning_smb_on_again_ends_the_old_sessions(self, world):
        """An active account that is turned on again: its sessions end, and that can fail."""
        world.make_active("bob", 70001, BOB_PW)
        world.fake.add_session("bob", ["media"])
        world.fake.fail[("smbcontrol",)] = (1, "Can't find pid 40000 (smbd) at /run/samba/smbd.pid")

        response = world.self_enable("bob", BOB_PW)

        assert response.status_code == 200, response.text
        assert response.json()["warnings"] == [SELF_SESSIONS_WARNING]

    def test_a_password_change(self, world):
        world.make_active("bob", 70001, BOB_PW)
        world.fake.add_session("bob", ["media"])
        world.fake.fail[("smbcontrol",)] = (1, "Can't find pid 40000 (smbd) at /run/samba/smbd.pid")

        response = world.change_password("bob", BOB_PW, "Bob-new-pass-2026")

        assert response.status_code == 200, response.text
        assert response.json() == {"status": "ok", "message": "Password changed successfully",
                                   "warnings": [SELF_SESSIONS_WARNING]}

    def test_no_problem_no_warning(self, world):
        world.make_active("bob", 70001, BOB_PW)
        world.fake.add_session("bob", ["media"])

        assert world.self_disable("bob").json()["warnings"] == []

    def test_administrators_get_the_full_texts(self, world):
        world.make_active("bob", 70001, BOB_PW)
        world.apply.return_value = interfaces.ApplyResult(changed=True, warnings=list(HOST_APPLY_WARNINGS))
        world.fake.add_session("bob", ["media"])
        world.fake.fail[("smbcontrol",)] = (1, "Can't find pid 40000 (smbd) at /run/samba/smbd.pid")

        response = world.admin_disable("bob")

        assert response.status_code == 200, response.text
        assert response.json()["warnings"] == HOST_APPLY_WARNINGS + [
            "An SMB connection of bob could not be ended: Can't find pid 40000 (smbd) at /run/samba/smbd.pid"
        ]


def _logged(world: World) -> str:
    return "\n".join(r.getMessage() for r in world.caplog.records)


class TestSelfFlowErrors:
    """ADR-0131 for errors: when a command on the host fails in a regular user's own
    flow, the answer carries no host output and no path, only a fixed text; the full
    text is logged. Fixed texts (missing tools, timeouts, collisions) are kept, and
    administrators get the full texts."""

    def test_turning_smb_on(self, world):
        world.make_allowed("alice")
        world.fake.fail[("useradd",)] = (1, "useradd: cannot lock /etc/passwd; try again later.")

        response = world.self_enable()

        assert response.status_code == 503
        assert response.json()["detail"] == f"SMB could not be turned on: {SELF_HOST_FAILED}"
        assert "useradd: cannot lock /etc/passwd; try again later." in _logged(world)
        assert world.fake.passdb == {}
        assert world.row("alice").password_state == "needed"

    def test_turning_smb_off(self, world):
        world.make_active("alice", 70000, ALICE_PW)
        world.fake.fail[("pdbedit", "-x")] = (
            1, "Failed to delete entry for user alice in /var/lib/samba/private/passdb.tdb")

        response = world.self_disable()

        assert response.status_code == 503
        assert response.json()["detail"] == f"SMB access of alice could not be revoked: {SELF_HOST_FAILED}"
        assert "/var/lib/samba/private/passdb.tdb" in _logged(world)
        assert world.row("alice").password_state == "set"
        world.apply.assert_not_awaited()

    def test_a_password_change(self, world):
        world.make_active("alice", 70000, ALICE_PW)
        world.fake.fail[("smbpasswd",)] = (1, "tdb_store failed: /var/lib/samba/private/passdb.tdb")

        response = world.change_password("alice", ALICE_PW, NEW_PW)

        assert response.status_code == 503
        assert response.json()["detail"] == f"The password was not changed: {SELF_HOST_FAILED}"
        assert "tdb_store failed" in _logged(world)
        assert bcrypt.checkpw(ALICE_PW.encode(), world.user("alice").hashed_password.encode())
        assert world.fake.passdb["alice"]["password"] == ALICE_PW

    def test_a_reset_that_fails_too(self, world, failing_commit):
        """The commit after smbpasswd fails, and removing the new passdb entry fails too."""
        world.make_allowed("alice")
        failing_commit.fail_at = 2
        world.fake.fail[("pdbedit", "-x")] = (1, "tdb locked: /var/lib/samba/private/passdb.tdb")

        response = world.self_enable()

        assert response.status_code == 503
        assert response.json()["detail"] == (
            "The database was busy and Samba could not be reset; SMB access is repaired at the next start"
        )
        assert "pdbedit could not remove the Samba account alice: tdb locked" in _logged(world)

    def test_fixed_texts_are_kept(self, world, monkeypatch):
        world.make_allowed("alice")
        world.fake.add_account("alice", 1001, gid=1001, gecos="Alice", home="/home/alice", shell="/bin/bash")
        assert world.self_enable().json()["detail"] == FOREIGN_ALICE

        del world.fake.passwd["alice"]
        times_out_once(world, monkeypatch, "useradd")
        response = world.self_enable()
        assert (response.status_code, response.json()["detail"]) == (503, "useradd did not answer in time")

        world.fake.missing = {"smbpasswd", "pdbedit", "smbstatus"}
        response = world.self_enable()
        assert (response.status_code, response.json()["detail"]) == (503, TOOLS_MISSING)

    def test_administrators_get_the_full_texts(self, world):
        world.fake.fail[("useradd",)] = (1, "useradd: cannot lock /etc/passwd; try again later.")

        response = world.admin_enable("alice", SET_PW)

        assert response.status_code == 503
        assert response.json()["detail"] == (
            "useradd could not create the host account alice: useradd: cannot lock /etc/passwd; try again later."
        )

    def test_every_text_with_host_output_is_marked(self):
        """An error text built from a command's output (_last_line) or an exception's text
        is a SmbHostCommandFailed, which the self flows do not show (SmbConfNotUnos has a
        fixed text of its own for them)."""
        source = (SRC / "shares" / "accounts.py").read_text()
        built = re.findall(r"\b(\w+)\(\d{3}, f\"[^\"\n]*\{(?:_last_line\(|_clean\(str\(exc\)\))", source)

        # The two of Samba's account policies (ADR-0278, ADR-0334) reach only the reconcile's problems.
        assert built.count("SmbHostCommandFailed") == 11
        assert set(built) == {"SmbHostCommandFailed", "SmbConfNotUnos"}


# ---------------------------------------------------------------------------
# Revocation: disallow, deactivate, promotion, deletion
# ---------------------------------------------------------------------------


class TestDisallow:
    def test_disallow_an_active_account(self, world):
        world.make_active("alice", 70000, ALICE_PW)
        pid = world.fake.add_session("alice", ["media"])

        response = world.admin_disable("alice")

        assert response.status_code == 200, response.text
        assert response.json()["smb"]["state"] == "off"
        assert world.fake.mutating_calls() == [["pdbedit", "-x", "-u", "alice"], ["smbcontrol", str(pid), "shutdown"]]
        assert world.fake.passdb == {}
        assert world.fake.passwd["alice"].uid == 70000  # the host account (and its uid) stays
        row = world.row("alice")
        assert (row.enabled, row.password_state, row.password_set_at, row.uid) == (False, "needed", None, 70000)
        world.apply.assert_awaited_once()
        assert world.smb_audit() == [("alice", "SMB access disallowed", "info")]

    def test_pdbedit_failure(self, world):
        world.make_active("alice", 70000, ALICE_PW)
        world.fake.fail[("pdbedit", "-x")] = (1, "tdb locked")

        response = world.admin_disable("alice")

        assert response.status_code == 503
        assert response.json()["detail"] == (
            "SMB access of alice could not be revoked: pdbedit could not remove the Samba account alice: tdb locked"
        )
        row = world.row("alice")
        assert (row.enabled, row.password_state) == (True, "set")
        world.apply.assert_not_awaited()

    def test_a_foreign_account_of_the_name_is_never_touched(self, world):
        world.set_row("alice", enabled=True, password_state="needed", uid=70000)
        world.fake.add_account("alice", 1001, gid=1001, gecos="Alice", home="/home/alice", shell="/bin/bash")
        world.fake.add_passdb("alice", "hand-made")

        response = world.admin_disable("alice")

        assert response.status_code == 200, response.text
        assert world.fake.commands("pdbedit", "-x") == []
        assert world.fake.passdb["alice"]["password"] == "hand-made"

    def test_disallow_without_a_row_or_twice(self, world):
        assert world.admin_disable("bob").json()["smb"]["state"] == "off"
        assert world.fake.calls == []
        world.make_allowed("alice")
        assert world.admin_disable("alice").status_code == 200
        assert world.admin_disable("alice").status_code == 200
        assert world.fake.calls == []  # never activated: nothing on the host
        assert world.smb_audit() == [("alice", "SMB access disallowed", "info")]

    def test_tools_missing_while_set(self, world):
        world.make_active("alice", 70000, ALICE_PW)
        world.fake.missing = {"pdbedit"}

        response = world.admin_disable("alice")

        assert response.status_code == 503
        assert response.json()["detail"] == "SMB access of alice could not be revoked: pdbedit missing"
        assert world.row("alice").password_state == "set"


class TestDeactivate:
    def test_deactivate(self, world):
        world.make_active("alice", 70000, ALICE_PW)
        pid = world.fake.add_session("alice", ["media"])

        response = world.self_disable()

        assert response.status_code == 200, response.text
        assert response.json()["account"]["state"] == "password_needed"
        row = world.row("alice")
        assert (row.enabled, row.password_state, row.password_set_at) == (True, "needed", None)
        assert world.fake.passdb == {}
        assert pid not in world.fake.session_pids()
        world.apply.assert_awaited_once()
        assert world.smb_audit() == [("alice", "SMB access deactivated by the user", "info")]
        # Turning it on again needs only the password.
        assert world.self_enable().json()["account"]["state"] == "active"

    def test_deactivate_without_a_row(self, world):
        response = world.self_disable("bob")

        assert response.status_code == 200
        assert response.json()["account"]["state"] == "off"
        assert world.fake.calls == []
        assert world.smb_audit() == []


class TestPromotion:
    def test_promotion_revokes_first(self, world):
        world.make_active("alice", 70000, ALICE_PW)
        share = world.add_share("media", ("alice", "write"))
        pid = world.fake.add_session("alice", ["media"])

        response = world.put_user("alice", is_admin=True, username="alice")

        assert response.status_code == 200, response.text
        assert response.json()["is_admin"] is True
        assert response.json()["smb"]["state"] == "unavailable"
        assert world.fake.passdb == {}
        assert pid not in world.fake.session_pids()
        row = world.row("alice")
        assert (row.enabled, row.password_state) == (False, "needed")
        world.delete_access.assert_awaited_once()
        assert world.delete_access.await_args.args[1] == world.ids["alice"]
        assert world.access_rows("alice") == []
        assert share
        world.apply.assert_awaited_once()
        assert ("alice", "SMB access revoked by the promotion to administrator", "warning") in world.smb_audit()

    def test_promotion_without_an_account_row(self, world):
        world.add_share("media", ("bob", "read"))

        response = world.put_user("bob", is_admin=True)

        assert response.status_code == 200, response.text
        world.delete_access.assert_awaited_once()
        assert world.delete_access.await_args.args[1] == world.ids["bob"]
        assert world.access_rows("bob") == []
        assert world.fake.calls == []
        world.apply.assert_awaited_once()  # access rows were deleted

    def test_a_failed_revocation_refuses_the_promotion(self, world):
        world.make_active("alice", 70000, ALICE_PW)
        world.fake.fail[("pdbedit", "-x")] = (1, "tdb locked")

        response = world.put_user("alice", is_admin=True)

        assert response.status_code == 503
        assert response.json()["detail"].startswith(
            "SMB access of alice could not be revoked, so alice was not made an administrator: "
        )
        assert world.user("alice").is_admin is False
        assert world.row("alice").password_state == "set"
        world.delete_access.assert_not_awaited()

    def test_demotion_does_nothing_for_smb(self, world):
        world.add_user("admin2", "Admin2-pass-2026", admin=True)

        response = world.put_user("admin2", is_admin=False)

        assert response.status_code == 200
        assert response.json()["smb"]["state"] == "off"
        assert world.fake.calls == []
        world.delete_access.assert_not_awaited()


class TestDeleteUser:
    def test_delete_an_active_user(self, world, monkeypatch):
        world.make_active("alice", 70000, ALICE_PW)
        world.add_share("media", ("alice", "write"))
        pid = world.fake.add_session("alice", ["media"])
        at_commit = []
        real = AsyncSession.commit

        async def recording_commit(self):
            at_commit.append((dict(world.fake.passdb), [a for a in world.fake.argvs() if a[0] == "userdel"]))
            return await real(self)

        monkeypatch.setattr(AsyncSession, "commit", recording_commit)

        response = world.delete_user("alice")

        assert response.status_code == 200, response.text
        assert response.json() == {"status": "ok", "message": "User deleted", "warnings": []}
        # passdb before the commit, userdel after it.
        assert at_commit == [({}, [])]
        argvs = world.fake.argvs()
        assert argvs.index(["smbcontrol", str(pid), "shutdown"]) < argvs.index(["userdel", "alice"])
        assert world.fake.commands("userdel") == [["userdel", "alice"]]
        assert "alice" not in world.fake.passwd
        assert ["sync", "/etc"] in argvs
        assert world.user("alice") is None
        assert world.row("alice") is None
        world.delete_access.assert_awaited_once()
        assert world.access_rows("alice") == []
        world.apply.assert_awaited_once()
        deleted = [e for e in world.audit() if e["action"] == "user_deleted"]
        assert deleted[0]["details"] == "Deleted user: alice; SMB account removed"
        assert deleted[0]["severity"] == "warning"

    def test_rows_go_in_the_transaction_of_the_user(self, world, failing_commit):
        world.make_allowed("alice")
        world.set_row("alice", uid=70000)
        world.add_share("media", ("alice", "read"))
        failing_commit.fail_at = 1

        response = world.delete_user("alice")

        assert response.status_code == 503
        assert response.json()["detail"] == DATABASE_BUSY
        assert world.user("alice") is not None
        assert world.row("alice") is not None
        assert world.access_rows("alice") != []
        # Nothing was revoked on the host (no passdb entry), so no session needs to end.
        assert world.fake.commands("smbcontrol") == []
        assert world.fake.mutating_calls() == []

    def test_a_failed_revocation_keeps_the_user(self, world):
        world.make_active("alice", 70000, ALICE_PW)
        world.fake.fail[("pdbedit", "-x")] = (1, "tdb locked")

        response = world.delete_user("alice")

        assert response.status_code == 503
        assert response.json()["detail"] == (
            "SMB access of alice could not be revoked; the user was not deleted: "
            "pdbedit could not remove the Samba account alice: tdb locked"
        )
        assert world.user("alice") is not None
        assert world.row("alice").password_state == "set"
        assert world.fake.commands("userdel") == []

    def test_userdel_busy_once(self, world):
        world.make_active("alice", 70000, ALICE_PW)
        world.fake.busy["alice"] = 1

        response = world.delete_user("alice")

        assert response.status_code == 200
        assert response.json()["warnings"] == []
        assert world.fake.commands("userdel") == [["userdel", "alice"], ["userdel", "alice"]]
        assert "alice" not in world.fake.passwd

    def test_userdel_busy_three_times(self, world):
        world.make_active("alice", 70000, ALICE_PW)
        world.fake.busy["alice"] = 3

        response = world.delete_user("alice")

        assert response.status_code == 200
        assert response.json()["warnings"] == [
            "The host account alice is still in use by an SMB connection; UNOS removes it at its next start"
        ]
        assert len(world.fake.commands("userdel")) == 3
        assert world.fake.passwd["alice"].uid == 70000
        assert world.user("alice") is None

    def test_a_user_without_a_row_and_without_access(self, world):
        response = world.delete_user("bob")

        assert response.status_code == 200
        assert response.json()["warnings"] == []
        assert world.fake.calls == []
        world.delete_access.assert_not_awaited()
        world.apply.assert_not_awaited()

    def test_a_user_with_access_only(self, world):
        world.add_share("media", ("bob", "read"))

        response = world.delete_user("bob")

        assert response.status_code == 200
        assert world.fake.calls == []
        world.delete_access.assert_awaited_once()
        world.apply.assert_awaited_once()
        assert world.access_rows("bob") == []

    def test_a_foreign_account_of_the_name_is_never_removed(self, world):
        world.set_row("alice", enabled=True, password_state="needed", uid=70000)
        world.fake.add_account("alice", 1001, gid=1001, gecos="Alice", home="/home/alice", shell="/bin/bash")

        response = world.delete_user("alice")

        assert response.status_code == 200
        assert world.fake.commands("userdel") == []
        assert world.fake.passwd["alice"].uid == 1001


class TestUsernamesInRequests:
    def test_admin_enable_with_a_stale_username(self, world):
        response = world.admin_enable("alice", SET_PW, username="alicia")

        assert response.status_code == 409
        assert response.json()["detail"] == f"User {world.ids['alice']} is now 'alice'; reload the page"
        assert world.fake.calls == []
        assert world.row("alice") is None

    def test_put_with_a_stale_username(self, world):
        world.make_active("alice", 70000, ALICE_PW)

        response = world.put_user("alice", password=NEW_PW, username="carol")

        assert response.status_code == 409
        assert response.json()["detail"] == f"User {world.ids['alice']} is now 'alice'; reload the page"
        assert world.fake.calls == []
        assert bcrypt.checkpw(ALICE_PW.encode(), world.user("alice").hashed_password.encode())

    def test_a_disable_or_delete_built_for_the_previous_owner_of_the_id(self, world):
        """ADR-0133: bob (the highest id) is deleted and carol gets his id. A
        request built from a stale list of users must not act on carol."""
        bob_id = world.ids["bob"]
        assert world.delete_user("bob").status_code == 200
        created = world.client.post(
            f"{API}/auth/users", json={"username": "carol", "password": "Carol-pass-2026", "smb_enabled": True},
            headers=world.headers("admin"),
        )
        assert (created.json()["id"], created.json()["smb"]["state"]) == (bob_id, "active")
        world.ids["carol"] = bob_id
        mark = len(world.fake.calls)

        disabled = world.admin_disable("bob")
        deleted = world.delete_user("bob")

        for response in (disabled, deleted):
            assert (response.status_code, response.json()["detail"]) == (
                409, f"User {bob_id} is now 'carol'; reload the page"
            )
        assert world.fake.calls[mark:] == []
        assert world.user("carol") is not None
        assert (world.row("carol").enabled, world.row("carol").password_state) == (True, "set")
        assert world.fake.passdb["carol"]["password"] == "Carol-pass-2026"

    def test_the_username_is_required(self, world):
        world.make_active("alice", 70000, ALICE_PW)
        url = f"{API}/auth/users/{world.ids['alice']}"

        responses = [
            world.client.delete(url, headers=world.headers("admin")),
            world.client.post(f"{url}/smb/disable", headers=world.headers("admin")),
            world.client.post(f"{url}/smb/disable", json={}, headers=world.headers("admin")),
            world.client.post(f"{url}/smb/disable", json={"username": "alice", "x": 1}, headers=world.headers("admin")),
        ]

        assert [r.status_code for r in responses] == [422, 422, 422, 422]
        assert world.fake.calls == []
        assert world.row("alice").password_state == "set"

    def test_unknown_user(self, world):
        response = world.client.post(f"{API}/auth/users/999/smb/enable", json={"username": "x"},
                                     headers=world.headers("admin"))
        assert response.status_code == 404
        assert response.json()["detail"] == "User not found"

    def test_extra_fields_are_refused(self, world):
        response = world.client.post(f"{API}/auth/users/{world.ids['alice']}/smb/enable",
                                     json={"username": "alice", "enabled": True}, headers=world.headers("admin"))
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# Host layer
# ---------------------------------------------------------------------------


class TestValidation:
    @pytest.mark.parametrize("call", [
        lambda: accounts.create_host_account("-o", 70000),
        lambda: accounts.create_host_account("root", 70000),
        lambda: accounts.create_host_account("alice", 0),
        lambda: accounts.create_host_account("alice", "70000"),
        lambda: accounts.create_host_account("alice", 80000),
        lambda: accounts._remove_host_account("Alice"),
        lambda: accounts._remove_host_account("x\ny"),
        lambda: accounts.set_samba_password("-a", ALICE_PW),
        lambda: accounts.delete_samba_entry("alice smith"),
        lambda: accounts.read_group("bob.smith"),
    ])
    def test_invalid_names_and_uids_raise_smb_host_error(self, monkeypatch, call):
        fake = install_fake_samba_host(monkeypatch)

        with pytest.raises(accounts.SmbHostError) as raised:
            call()

        assert raised.value.status == 500
        assert raised.value.message.startswith("Refused to run ")
        assert fake.calls == []

    def test_accounts_py_has_no_assert(self):
        tree = ast.parse((SRC / "shares" / "accounts.py").read_text())
        assert not [node for node in ast.walk(tree) if isinstance(node, ast.Assert)]

    def test_no_shell_in_account_commands(self, monkeypatch):
        fake = install_fake_samba_host(monkeypatch)
        fake.add_smb_group()
        accounts.create_host_account("alice", 70000)
        accounts.set_samba_password("alice", ALICE_PW)
        accounts.delete_samba_entry("alice")
        accounts._remove_host_account("alice", expected_uid=70000)

        for argv in fake.argvs():
            assert argv[0] in ("useradd", "smbpasswd", "pdbedit", "getent", "userdel", "sync"), argv

    def test_smbpasswd_error_never_contains_the_password(self, monkeypatch):
        fake = install_fake_samba_host(monkeypatch)
        fake.add_account("alice", 70000)
        fake.fail[("smbpasswd",)] = (1, f"weird error echoing {ALICE_PW}")

        with pytest.raises(accounts.SmbHostError) as raised:
            accounts.set_samba_password("alice", ALICE_PW)

        assert ALICE_PW not in raised.value.message
        assert raised.value.message == "Samba did not accept the password: weird error echoing ***"

    @pytest.mark.parametrize("returncode, status", [(4, 409), (9, 409), (6, 503), (1, 503)])
    def test_useradd_exit_codes(self, monkeypatch, returncode, status):
        fake = install_fake_samba_host(monkeypatch)
        fake.fail[("useradd",)] = (returncode, "useradd: failed")

        with pytest.raises(accounts.SmbHostError) as raised:
            accounts.create_host_account("alice", 70000)

        assert raised.value.status == status

    def test_userdel_exit_codes(self, monkeypatch):
        fake = install_fake_samba_host(monkeypatch)
        fake.add_account("alice", 70000)
        fake.fail[("userdel",)] = (12, "userdel: cannot remove")
        with pytest.raises(accounts.SmbHostError) as raised:
            accounts._remove_host_account("alice", expected_uid=70000)
        assert raised.value.status == 503
        del fake.fail[("userdel",)]
        fake.busy["alice"] = 1
        assert accounts._remove_host_account("alice", expected_uid=70000) == "busy"
        assert accounts._remove_host_account("alice", expected_uid=70000) == "removed"
        assert accounts._remove_host_account("alice", expected_uid=70000) == "missing"  # gone: nothing to do
        assert "alice" not in fake.passwd


def hide_from_enumeration(fake: FakeSambaHost, name: str) -> None:
    """An NSS directory with enumeration off (sssd's default): ``getent passwd`` does not
    list ``name``, while ``getent passwd <name>`` answers."""
    real = fake._getent

    def getent(argv):
        result = real(argv)
        if argv == ["getent", "passwd"]:
            kept = [line for line in result.stdout.splitlines(keepends=True) if not line.startswith(f"{name}:")]
            return HostResult(result.returncode, "".join(kept), result.stderr)
        return result

    fake._getent = getent


class TestForeignAccountsFailClosed:
    """ADR-0192: pdbedit -x never removes the Samba entry of a host account that UNOS
    did not create, also when getent passwd does not list that account cleanly, and decides
    on the account and passdb as they are right before it runs."""

    @staticmethod
    def directory_alice(monkeypatch, gecos: str = "Alice") -> FakeSambaHost:
        fake = install_fake_samba_host(monkeypatch)
        fake.add_smb_group()
        fake.add_account("alice", 1001, gid=1001, gecos=gecos, home="/home/alice", shell="/bin/bash")
        fake.add_passdb("alice", "hers")
        return fake

    def test_a_revocation_and_an_account_getent_does_not_list(self, monkeypatch):
        fake = self.directory_alice(monkeypatch)
        hide_from_enumeration(fake, "alice")

        assert accounts._revoke_on_host("alice", 70000, "needed") is False

        assert fake.commands("pdbedit", "-x") == []
        assert fake.passdb["alice"]["password"] == "hers"

    @pytest.mark.parametrize("call", [
        pytest.param(lambda: accounts._revoke_on_host("alice", 70000, "needed"), id="revocation"),
        pytest.param(lambda: accounts.delete_samba_entry("alice"), id="delete_samba_entry"),
    ])
    def test_a_colon_in_the_gecos(self, monkeypatch, call):
        fake = self.directory_alice(monkeypatch, gecos="Alice: directory user")

        assert call() is False

        assert fake.commands("pdbedit", "-x") == []
        assert fake.passdb["alice"]["password"] == "hers"

    def test_an_unreadable_line_is_a_foreign_account(self, monkeypatch):
        self.directory_alice(monkeypatch, gecos="Alice: directory user")

        listed = accounts.read_host_accounts()

        assert "alice" in listed and not listed["alice"].ours
        assert listed["alice"].uid == 1001  # kept: no uid allocation picks it
        assert accounts._read_host_account("alice") == listed["alice"]
        assert accounts.is_unos_passdb_entry("alice", listed, accounts.read_passdb_names()) is False

    def test_an_unreadable_line_wins_over_a_marked_entry_of_the_name(self, monkeypatch):
        """Two NSS sources name alice; one of them cannot be read: UNOS leaves alice alone."""
        fake = install_fake_samba_host(monkeypatch)
        fake.add_account("alice", 70000)
        real = fake._getent

        def getent(argv):
            result = real(argv)
            if argv[:2] == ["getent", "passwd"]:
                return HostResult(0, result.stdout + "alice:x:1001:1001:Alice: directory:/home/alice:/bin/bash\n", "")
            return result

        fake._getent = getent

        assert not accounts.read_host_accounts()["alice"].ours
        assert not accounts._read_host_account("alice").ours

    def test_a_removal_reads_the_host_again(self, world, monkeypatch):
        """Activation step 4 decides on what step 2 read, before the commit of the uid. Here the
        stale account alice was taken over on the host in between (usermod -c): its Samba entry
        stays, and so does the account."""
        world.make_allowed("alice")
        world.set_row("alice", uid=70005)
        world.set_next_uid(70006)
        world.fake.add_smb_group()
        world.fake.add_account("alice", 70003)
        world.fake.add_passdb("alice", "old-password")
        real = AsyncSession.commit
        commits = {"n": 0}

        async def commit(self):
            commits["n"] += 1
            if commits["n"] == 1:  # the reservation of step 3
                world.fake.add_account("alice", 70003, gecos="Alice Liddell")
            return await real(self)

        monkeypatch.setattr(AsyncSession, "commit", commit)

        response = world.self_enable()

        assert response.status_code == 500
        assert response.json()["detail"] == "Refused to remove the host account alice: UNOS did not create it"
        assert world.fake.commands("pdbedit", "-x") == []
        assert world.fake.commands("userdel") == []
        assert world.fake.commands("smbpasswd") == []
        assert world.fake.passdb["alice"]["password"] == "old-password"

    def test_a_consistent_start_reads_nothing_more(self, world):
        """The re-read runs only on removal paths: nothing to remove, no extra command."""
        world.make_active("alice", 70000, ALICE_PW)
        world.make_allowed("bob")

        assert accounts._revoke_on_host("bob", None, "needed") is False
        assert accounts._revoke_on_host("bob", 70001, "needed") is False

        assert [argv[:2] for argv in world.fake.argvs()] == [
            ["/bin/sh", "-c"], ["getent", "passwd"], ["pdbedit", "-L"],
        ]


class TestRemoveHostAccount:
    """_remove_host_account reads the account again right before userdel (ADR-0192): the callers'
    facts may be seconds old (the reconcile ends the account's sessions in between)."""

    def test_a_foreign_account_with_the_expected_uid_is_never_removed(self, monkeypatch):
        fake = install_fake_samba_host(monkeypatch)
        foreign = fake.add_account("nas", 70004, gecos="NAS admin")  # the uid and gid, but not the marker

        with pytest.raises(accounts.SmbHostError) as raised:
            accounts._remove_host_account("nas", expected_uid=70004)

        assert (raised.value.status, raised.value.message) == (
            500, "Refused to remove the host account nas: UNOS did not create it"
        )
        assert fake.commands("userdel") == []
        assert fake.passwd["nas"] == foreign

    @pytest.mark.parametrize("account", [
        accounts.HostAccount("nas", 70004, 1000, "UNOS SMB user", "/nonexistent", "/usr/sbin/nologin"),  # gid
        accounts.HostAccount("nas", 1000, 70000, "UNOS SMB user", "/nonexistent", "/usr/sbin/nologin"),   # uid
    ])
    def test_an_account_without_the_whole_marker_is_never_removed(self, monkeypatch, account):
        fake = install_fake_samba_host(monkeypatch)
        fake.passwd["nas"] = account

        with pytest.raises(accounts.SmbHostError):
            accounts._remove_host_account("nas", expected_uid=account.uid)

        assert fake.commands("userdel") == []

    def test_a_marked_account_with_another_uid_is_missing(self, monkeypatch):
        fake = install_fake_samba_host(monkeypatch)
        fake.add_account("dave", 70008)

        assert accounts._remove_host_account("dave", expected_uid=70007) == "missing"
        assert fake.commands("userdel") == []
        assert fake.passwd["dave"].uid == 70008

    def test_an_unreadable_passwd_line_is_foreign(self, monkeypatch):
        """A ':' in the GECOS of a directory account: the line does not parse (fail closed)."""
        fake = install_fake_samba_host(monkeypatch)
        fake.add_account("dave", 70007, gecos="UNOS SMB user: Dave")

        with pytest.raises(accounts.SmbHostError) as raised:
            accounts._remove_host_account("dave", expected_uid=70007)

        assert raised.value.status == 500
        assert fake.commands("userdel") == []

    def test_a_timeout(self, monkeypatch):
        import subprocess

        def hanging(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

        monkeypatch.setattr("src.host.run_on_host", hanging)
        with pytest.raises(accounts.SmbHostError) as raised:
            accounts.read_host_accounts()
        assert (raised.value.status, raised.value.message) == (503, "getent did not answer in time")

    def test_a_missing_program(self, monkeypatch):
        def missing(cmd, **kwargs):
            raise FileNotFoundError(2, "No such file or directory", cmd[0])

        monkeypatch.setattr("src.host.run_on_host", missing)
        with pytest.raises(accounts.SmbHostError) as raised:
            accounts.read_passdb_names()
        assert raised.value.message == (
            "SMB accounts are not available: the host lacks pdbedit (install samba and samba-common-bin)"
        )

    def test_gid_problem(self, monkeypatch):
        fake = install_fake_samba_host(monkeypatch)
        assert accounts.gid_problem() is None  # no group at all
        fake.add_smb_group()
        assert accounts.gid_problem() is None
        del fake.groups["unos-smb"]
        fake.add_group("media", 70000)
        assert accounts.gid_problem() == (
            "gid 70000 belongs to the host group media, not unos-smb: share folders would be opened to "
            "media's members. Remove or renumber media on the host"
        )
        fake.missing = {"getent"}
        assert accounts.gid_problem() is None  # no group database (the stand-in)
        fake.missing = set()
        fake.fail[("getent",)] = (1, "nsswitch broken")
        assert accounts.gid_problem().startswith("The owner of gid 70000 on the host could not be checked:")

    def test_host_status(self, monkeypatch):
        fake = install_fake_samba_host(monkeypatch)
        fake.add_smb_group()
        fake.add_account("alice", 70000)
        fake.add_passdb("alice")
        fake.add_passdb("nas", uid=1000)
        fake.add_account("nas", 1000, gid=1000, gecos="nas")

        status = accounts.host_status()

        assert status == interfaces.AccountHostStatus(tools_missing=[], group_problem=None,
                                                      unmanaged_samba_users=["nas"])
        assert fake.mutating_calls() == []

        fake.groups["unos-smb"] = (1234, [])
        assert accounts.host_status().group_problem == (
            "The host group unos-smb exists with gid 1234; UNOS needs gid 70000. Remove or renumber it."
        )
        fake.missing = {"smbpasswd", "pdbedit", "smbstatus"}
        status = accounts.host_status()
        assert status.tools_missing == ["smbpasswd", "pdbedit", "smbstatus"]
        assert status.unmanaged_samba_users == []

    def test_host_status_when_the_accounts_cannot_be_read(self, monkeypatch):
        """A failed `getent passwd` is not a group problem (which would block every share),
        and UNOS's own passdb entries are not reported as unmanaged then."""
        fake = install_fake_samba_host(monkeypatch)
        fake.add_smb_group()
        fake.add_account("alice", 70000)
        fake.add_passdb("alice")
        fake.fail[("getent", "passwd")] = (1, "nss: ldap unreachable")

        assert accounts.host_status() == interfaces.AccountHostStatus(
            tools_missing=[], group_problem=None, unmanaged_samba_users=[]
        )

        # The other way round: the group cannot be checked, the accounts can.
        del fake.fail[("getent", "passwd")]
        fake.fail[("getent", "group")] = (1, "nss: ldap unreachable")
        fake.add_passdb("nas", uid=1000)
        status = accounts.host_status()
        assert status.group_problem == (
            "The host group unos-smb could not be checked: getent could not read the host group unos-smb: "
            "nss: ldap unreachable"
        )
        assert status.unmanaged_samba_users == ["nas"]

    def test_host_status_when_the_check_fails(self, monkeypatch):
        fake = install_fake_samba_host(monkeypatch)
        fake.fail[("/bin/sh",)] = (1, "nsenter: reassociate to namespace 'ns/mnt' failed")

        assert accounts.host_status() == interfaces.AccountHostStatus(tools_missing=None)
        assert [a[0] for a in fake.argvs()] == ["/bin/sh"]


# ---------------------------------------------------------------------------
# read_smb_sessions and end_smb_sessions (ADR-0104)
# ---------------------------------------------------------------------------

DEVICE_EMPTY = json.dumps({
    "timestamp": "2026-11-06T16:20:31.512345+0100", "version": "4.22.6-Debian-4.22.6+dfsg-0+deb13u1",
    "smb_conf": "/etc/samba/smb.conf", "sessions": {}, "tcons": {}, "open_files": {},
})

SPEC_EXAMPLE = {
    "timestamp": "2026-11-06T16:20:31.512345+0100",
    "version": "4.22.6-Debian-4.22.6+dfsg-0+deb13u1",
    "smb_conf": "/etc/samba/smb.conf",
    "sessions": {
        "3254418735": {
            "session_id": "3254418735",
            "server_id": {"pid": "48211", "task_id": "0", "vnn": "4294967295", "unique_id": "11259836127046405537"},
            "uid": 70000, "gid": 70000, "username": "alice", "groupname": "unos-smb",
            "creation_time": "2026-11-06T16:19:58.103842+01:00",
            "expiration_time": "30828-09-14T03:48:05.477581+01:00",
            "auth_time": "2026-11-06T16:19:58.111027+01:00",
            "remote_machine": "10.1.1.20", "hostname": "ipv4:10.1.1.20:50122", "session_dialect": "SMB3_11",
            "client_guid": "5e4ac1d7-2b0f-4c41-9d0e-8f2c1a7b3e55",
            "encryption": {"cipher": "-", "degree": "none"},
            "signing": {"cipher": "AES-128-GMAC", "degree": "full"}, "channels": {},
        }
    },
    "tcons": {
        "2984": {
            "service": "media",
            "server_id": {"pid": "48211", "task_id": "0", "vnn": "4294967295", "unique_id": "11259836127046405537"},
            "tcon_id": "2984", "session_id": "3254418735", "machine": "10.1.1.20",
            "connected_at": "2026-11-06T16:19:58.142337+01:00",
            "encryption": {"cipher": "-", "degree": "none"}, "signing": {"cipher": "AES-128-GMAC", "degree": "full"},
        },
        "2983": {
            "service": "IPC$",
            "server_id": {"pid": "48211", "task_id": "0", "vnn": "4294967295", "unique_id": "11259836127046405537"},
            "tcon_id": "2983", "session_id": "3254418735", "machine": "10.1.1.20",
            "connected_at": "2026-11-06T16:19:58.120511+01:00",
            "encryption": {"cipher": "-", "degree": "none"}, "signing": {"cipher": "AES-128-GMAC", "degree": "full"},
        },
    },
    "open_files": {},
}


def _smbstatus(monkeypatch, stdout="", returncode=0, exc=None):
    calls = []

    def run(cmd, **kwargs):
        calls.append(list(cmd))
        if exc is not None:
            raise exc
        if cmd[0] == "smbcontrol":
            return accounts.host.HostResult(0, "", "")
        return accounts.host.HostResult(returncode, stdout, "smbstatus: failed" if returncode else "")

    monkeypatch.setattr("src.host.run_on_host", run)
    return calls


class TestReadSmbSessions:
    def test_the_devices_empty_output(self, monkeypatch):
        calls = _smbstatus(monkeypatch, DEVICE_EMPTY)
        assert accounts.read_smb_sessions() == []
        assert calls == [["smbstatus", "--json"]]

    def test_the_example_of_the_spec(self, monkeypatch):
        _smbstatus(monkeypatch, json.dumps(SPEC_EXAMPLE))
        assert accounts.read_smb_sessions() == [
            interfaces.SmbSession(pid=48211, username="alice", shares=("IPC$", "media"))
        ]

    @pytest.mark.parametrize("stdout, returncode", [("not json", 0), ("[1, 2]", 0), (DEVICE_EMPTY, 1)])
    def test_unreadable_output(self, monkeypatch, caplog, stdout, returncode):
        _smbstatus(monkeypatch, stdout, returncode)
        with caplog.at_level(logging.WARNING, logger="src.shares.accounts"):
            assert accounts.read_smb_sessions() is None
        assert len(caplog.records) == 1

    def test_a_timeout(self, monkeypatch):
        import subprocess

        _smbstatus(monkeypatch, exc=subprocess.TimeoutExpired(["smbstatus"], 15))
        assert accounts.read_smb_sessions() is None

    def test_missing_smbstatus(self, monkeypatch):
        _smbstatus(monkeypatch, exc=FileNotFoundError(2, "No such file or directory", "smbstatus"))
        assert accounts.read_smb_sessions() == []
        _smbstatus(monkeypatch, "", 127)
        assert accounts.read_smb_sessions() == []

    def test_unreadable_entries_are_skipped(self, monkeypatch, caplog):
        data = json.loads(json.dumps(SPEC_EXAMPLE))
        good = data["sessions"]["3254418735"]
        data["sessions"] = {
            "1": {**good, "server_id": None},
            "2": {key: value for key, value in good.items() if key != "server_id"},
            "3": {**good, "server_id": {"pid": "1"}},
            "4": {**good, "server_id": {"pid": "abc"}},
            "5": {**good, "username": "(auth in progress)"},
            "6": {**good, "username": 70000},
            "7": "not an object",
            "8": {**good, "server_id": {"pid": "١٢٣"}},  # non-ASCII digits
            "9": {**good, "server_id": {"pid": 50000}, "username": "bob"},
            "10": good,
        }
        _smbstatus(monkeypatch, json.dumps(data))

        with caplog.at_level(logging.WARNING, logger="src.shares.accounts"):
            sessions = accounts.read_smb_sessions()

        assert sessions == [
            interfaces.SmbSession(pid=50000, username="bob", shares=()),
            interfaces.SmbSession(pid=48211, username="alice", shares=("IPC$", "media")),
        ]
        [record] = caplog.records
        assert "8 entries" in record.getMessage()

    def test_sections_that_are_not_objects(self, monkeypatch):
        _smbstatus(monkeypatch, json.dumps({"sessions": [], "tcons": "x"}))
        assert accounts.read_smb_sessions() == []
        _smbstatus(monkeypatch, json.dumps({"timestamp": "x"}))
        assert accounts.read_smb_sessions() == []


class TestEndSmbSessions:
    def test_names_are_compared_case_insensitively(self, monkeypatch):
        fake = install_fake_samba_host(monkeypatch)
        alice = fake.add_session("Alice", ["media"])
        alice_again = fake.add_session("alice", ["docs"])
        bob = fake.add_session("bob", ["media"])
        guest = fake.add_session("nobody", ["public"])

        assert accounts.end_smb_sessions(["ALICE"]) == []

        assert fake.commands("smbcontrol") == [
            ["smbcontrol", str(alice), "shutdown"], ["smbcontrol", str(alice_again), "shutdown"],
        ]
        assert fake.session_pids() == [bob, guest]

    def test_only_listed_pids_are_shut_down(self, monkeypatch):
        fake = install_fake_samba_host(monkeypatch)
        fake.add_session("bob", ["media"])

        assert accounts.end_smb_sessions(["alice"]) == []
        assert fake.argvs() == [["smbstatus", "--json"]]

    def test_a_failing_smbcontrol(self, monkeypatch):
        fake = install_fake_samba_host(monkeypatch)
        pid = fake.add_session("alice", ["media"])
        fake.fail[("smbcontrol", str(pid))] = (1, "Can't find pid")

        assert accounts.end_smb_sessions(["alice"]) == ["An SMB connection of alice could not be ended: Can't find pid"]

    def test_sessions_that_cannot_be_listed(self, monkeypatch):
        fake = install_fake_samba_host(monkeypatch)
        fake.fail[("smbstatus",)] = (1, "smbstatus: tdb error")

        assert accounts.end_smb_sessions(["bob", "alice"]) == [
            "Open SMB connections could not be listed; connections of alice, bob end when their clients disconnect"
        ]
        assert fake.commands("smbcontrol") == []

    def test_no_names_no_host_call(self, monkeypatch):
        fake = install_fake_samba_host(monkeypatch)
        assert accounts.end_smb_sessions([]) == []
        assert accounts.end_smb_sessions(["", ""]) == []
        assert fake.calls == []

    def test_smbstatus_missing(self, monkeypatch):
        fake = install_fake_samba_host(monkeypatch)
        fake.missing = {"smbstatus"}
        assert accounts.end_smb_sessions(["alice"]) == []
        assert fake.commands("smbcontrol") == []


def test_secrets_never_reach_a_command_line(world):
    """The whole cycle with passwords; the world fixture checks argv, logs and answers."""
    world.make_allowed("alice")
    assert world.self_enable().status_code == 200
    assert world.change_password("alice", ALICE_PW, NEW_PW).status_code == 200
    assert world.put_user("alice", password=SET_PW).status_code == 200
    assert world.admin_enable("alice", ALICE_PW).status_code == 200
    assert world.self_disable().status_code == 200
    assert world.delete_user("alice").status_code == 200
    assert [stdin for stdin in world.fake.stdin_of("smbpasswd")] == [
        f"{ALICE_PW}\n{ALICE_PW}\n", f"{NEW_PW}\n{NEW_PW}\n", f"{SET_PW}\n{SET_PW}\n", f"{ALICE_PW}\n{ALICE_PW}\n",
    ]
