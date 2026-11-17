"""An administrator requires two-factor authentication for every account, with a grace period
(auth.mandatory-two-factor, ADR-0383).

The requirement is the one row of two_factor_requirement, turned on and off in System > Security by
an administrator who has two-factor authentication, with a current code. Each account's grace
period starts at the later of the requirement's start and the account's creation. During it,
GET /auth/me says by when; after it, an account without two-factor authentication is setup-only:
its session reaches only GET /auth/me, GET /auth/totp/status, POST /auth/totp/setup,
POST /auth/totp/enable and POST /auth/logout, and everything else, API tokens and WebSockets
included, is refused with 403 (4403). The time the requirement is judged at is the tests'
(requirement._now), as the time TOTP codes are checked at is (auth.utils._totp_time).
"""
from __future__ import annotations

import io
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pyotp
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from starlette.websockets import WebSocketDisconnect

from src import emergency
from src.audit.schemas import AuditAction
from src.auth import requirement
from src.auth import utils as auth_utils
from src.auth.models import APIToken, TwoFactorRequirement, User
from src.database import Base
from src.auth.requirement import TWO_FACTOR_SETUP_REQUIRED
from src.auth.utils import TotpCheck, generate_api_token
from src.config import get_settings
from src.security import router as security_router
from src.websocket import events

from .audit_helpers import audit_entries
from .test_backup_restore import MANIFEST, _file, _make_db, _restore, _store, make_env
from .test_sign_in_full_disk import ENTRY_LOST, reported, the_audit_log_is_full
from .test_smb_accounts import ADMIN_PW, ALICE_PW, API, World, _hash
from .test_totp_once import REUSED, Clock

REPO = Path(__file__).resolve().parents[2]
REQUIREMENT = f"{API}/security/two-factor-requirement"
ADMIN_SECRET = pyotp.random_base32()
ALICE_SECRET = pyotp.random_base32()
# The requirement's start in the tests, in UTC without a zone, as the database keeps times.
START = datetime(2026, 11, 10, 9, 0, 0)
OFF = {"enabled": False, "grace_days": None, "enabled_at": None, "enabled_by": None}
RESET_PW = "Admin-reset-pass-2026"


class Now:
    """The time the requirement is judged at (requirement._now): UTC without a zone."""

    def __init__(self) -> None:
        self.value = START

    def __call__(self) -> datetime:
        return self.value


@pytest.fixture
def clock(monkeypatch) -> Clock:
    clock = Clock()
    monkeypatch.setattr(auth_utils, "_totp_time", lambda: clock.now)
    return clock


@pytest.fixture
def now(monkeypatch) -> Now:
    now = Now()
    monkeypatch.setattr(requirement, "_now", now)
    return now


@pytest.fixture
def world(tmp_path, monkeypatch, caplog, now):
    w = World(tmp_path, monkeypatch, caplog)
    # Every account was made a month before the requirement: its grace period starts with it.
    for name in ("admin", "alice", "bob"):
        created(w, name, START - timedelta(days=30))
    try:
        yield w
    finally:
        w.close()


def created(world: World, name: str, when: datetime) -> None:
    with Session(world.engine) as session:
        session.get(User, world.ids[name]).created_at = when
        session.commit()


def require(world: World, grace_days: int = 7, at: datetime = START, by: str = "admin") -> None:
    """The requirement as turning it on stores it."""
    with Session(world.engine) as session:
        session.add(TwoFactorRequirement(id=1, grace_days=grace_days, enabled_at=at, enabled_by=by))
        session.commit()


def stored(world: World) -> tuple | None:
    with Session(world.engine) as session:
        row = session.get(TwoFactorRequirement, 1)
        return None if row is None else (row.grace_days, row.enabled_at, row.enabled_by)


def turn_on(world: World, clock: Clock, grace_days: int | None = 7, *, code: str | None = None, auth=None):
    body = {"code": clock.code(ADMIN_SECRET) if code is None else code}
    if grace_days is not None:
        body["grace_days"] = grace_days
    return world.client.post(f"{REQUIREMENT}/enable", json=body, headers=auth or world.headers("admin"))


def turn_off(world: World, clock: Clock, *, code: str | None = None, auth=None):
    body = {"code": clock.code(ADMIN_SECRET) if code is None else code}
    return world.client.post(f"{REQUIREMENT}/disable", json=body, headers=auth or world.headers("admin"))


def changes(world: World) -> list[tuple]:
    return [(e["action"], e["username"], e["resource_type"], e["details"], e["severity"])
            for e in world.audit() if e["resource_type"] == "two_factor_requirement"]


def me(world: World, auth: dict[str, str]) -> dict:
    answer = world.client.get(f"{API}/auth/me", headers=auth)
    assert answer.status_code == 200, answer.text
    return answer.json()


def sign_in(world: World, name: str = "alice", password: str = ALICE_PW, **second):
    return world.client.post(f"{API}/auth/login", json={"username": name, "password": password, **second})


def refused(answer) -> bool:
    """The answer of a request that the setup-only state refused."""
    return (answer.status_code, answer.json().get("detail")) == (403, TWO_FACTOR_SETUP_REQUIRED)


def utc(value: datetime) -> str:
    """A time of the database as the API answers it: UTC, with its offset."""
    return value.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


class TestTurningItOn:
    def test_it_is_off_by_default(self, world):
        answer = world.client.get(REQUIREMENT, headers=world.headers("admin"))

        assert (answer.status_code, answer.json()) == (200, OFF)
        assert me(world, world.headers("alice"))["two_factor_required"] is None

    def test_an_administrator_with_two_factor_authentication_turns_it_on_with_a_code(self, world, clock):
        world.set_totp("admin", ADMIN_SECRET)

        answer = turn_on(world, clock, 14)

        expected = {"enabled": True, "grace_days": 14, "enabled_at": utc(START), "enabled_by": "admin"}
        assert (answer.status_code, answer.json()) == (200, expected)
        assert world.client.get(REQUIREMENT, headers=world.headers("admin")).json() == expected
        assert stored(world) == (14, START, "admin")
        assert changes(world) == [("settings_changed", "admin", "two_factor_requirement",
                                   "Two-factor authentication required for every account, with a grace period of 14 days",
                                   "info")]

    def test_the_grace_period_is_7_days_unless_another_is_chosen(self, world, clock):
        world.set_totp("admin", ADMIN_SECRET)

        assert turn_on(world, clock, None).json()["grace_days"] == 7

    def test_without_a_grace_period(self, world, clock):
        world.set_totp("admin", ADMIN_SECRET)

        assert turn_on(world, clock, 0).json()["grace_days"] == 0
        assert changes(world)[0][3] == "Two-factor authentication required for every account, without a grace period"

    def test_a_grace_period_of_one_day_is_named_so(self, world, clock):
        world.set_totp("admin", ADMIN_SECRET)

        turn_on(world, clock, 1)

        assert changes(world)[0][3] == "Two-factor authentication required for every account, with a grace period of 1 day"

    @pytest.mark.parametrize("grace_days", [-1, 2, 31, 365, "7", None])
    def test_other_grace_periods_are_refused(self, world, clock, grace_days):
        world.set_totp("admin", ADMIN_SECRET)
        body = {"code": clock.code(ADMIN_SECRET), "grace_days": grace_days}

        answer = world.client.post(f"{REQUIREMENT}/enable", json=body, headers=world.headers("admin"))

        assert answer.status_code == 422
        assert stored(world) is None

    def test_an_administrator_without_two_factor_authentication_cannot(self, world, clock):
        """No one imposes it without meeting it: at that moment an administrator keeps full access."""
        answer = turn_on(world, clock)

        assert (answer.status_code, answer.json()["detail"]) == (
            409, "Turn on two-factor authentication for your own account first")
        assert stored(world) is None
        assert changes(world) == []

    def test_a_wrong_or_used_code_is_refused(self, world, clock):
        world.set_totp("admin", ADMIN_SECRET)
        code = clock.code(ADMIN_SECRET)
        wrong = "000000" if code != "000000" else "111111"

        first = turn_on(world, clock, code=wrong)
        assert (first.status_code, first.json()["detail"]) == (400, "Invalid TOTP code")
        assert stored(world) is None
        assert turn_on(world, clock, code=code).status_code == 200

        # Each code is accepted once (ADR-0067), here and at every other place that takes one.
        again = turn_off(world, clock, code=code)

        assert (again.status_code, again.json()["detail"]) == (400, REUSED)
        assert stored(world) is not None
        assert [c[4] for c in changes(world)] == ["info"]

    def test_an_api_token_cannot(self, world, clock):
        world.set_totp("admin", ADMIN_SECRET)
        token = self.api_token(world, "admin")

        answer = turn_on(world, clock, auth={"Authorization": f"Bearer {token}"})

        assert (answer.status_code, answer.json()["detail"]) == (
            403, "An API token cannot change the two-factor requirement: sign in to do it")
        assert stored(world) is None

    def test_an_api_token_is_refused_before_anything_else(self, world, clock):
        world.set_totp("admin", ADMIN_SECRET)
        require(world)
        auth = {"Authorization": f"Bearer {self.api_token(world, 'admin')}"}

        for answer in (turn_on(world, clock, auth=auth), turn_off(world, clock, auth=auth)):
            assert (answer.status_code, answer.json()["detail"]) == (
                403, "An API token cannot change the two-factor requirement: sign in to do it")

    def test_of_two_administrators_at_once_only_one_turns_it_on(self, world, clock, monkeypatch):
        world.set_totp("admin", ADMIN_SECRET)

        def the_other_one_first(user_id, secret, code):
            require(world, 30, by="other")
            return TotpCheck.OK

        monkeypatch.setattr(security_router, "check_totp_code", the_other_one_first)

        answer = turn_on(world, clock, 7)

        assert (answer.status_code, answer.json()["detail"]) == (
            409, "Two-factor authentication is already required for every account")
        assert stored(world) == (30, START, "other")
        assert changes(world) == []

    def test_an_administrator_whose_own_two_factor_authentication_went_off_meanwhile_cannot(
        self, world, clock, monkeypatch
    ):
        """The administrator's own /totp/disable that commits while the code is checked: their
        two-factor authentication is read again in the transaction of the insert."""
        world.set_totp("admin", ADMIN_SECRET)

        def turned_off_meanwhile(user_id, secret, code):
            with Session(world.engine) as session:
                session.get(User, world.ids["admin"]).totp_enabled = False
                session.commit()
            return TotpCheck.OK

        monkeypatch.setattr(security_router, "check_totp_code", turned_off_meanwhile)

        answer = turn_on(world, clock)

        assert (answer.status_code, answer.json()["detail"]) == (
            409, "Turn on two-factor authentication for your own account first")
        assert stored(world) is None
        assert changes(world) == []

    def test_a_regular_user_cannot(self, world, clock):
        world.set_totp("alice", ALICE_SECRET)

        answer = world.client.post(f"{REQUIREMENT}/enable", json={"code": clock.code(ALICE_SECRET)},
                                   headers=world.headers("alice"))

        assert answer.status_code == 403
        assert world.client.get(REQUIREMENT, headers=world.headers("alice")).status_code == 403
        assert stored(world) is None

    def test_turning_it_on_again_is_refused_and_changes_nothing(self, world, clock):
        world.set_totp("admin", ADMIN_SECRET)
        require(world, 3)

        answer = turn_on(world, clock, 30)

        assert (answer.status_code, answer.json()["detail"]) == (
            409, "Two-factor authentication is already required for every account")
        assert stored(world) == (3, START, "admin")
        assert changes(world) == []

    def test_an_entry_that_cannot_be_written_is_in_the_log(self, world, clock, monkeypatch):
        world.set_totp("admin", ADMIN_SECRET)
        the_audit_log_is_full(monkeypatch)

        answer = turn_on(world, clock)

        assert answer.status_code == 200
        assert stored(world) is not None
        assert [entry["resource_type"] for entry, _ in reported(world, ENTRY_LOST)] == ["two_factor_requirement"]

    @staticmethod
    def api_token(world: World, name: str) -> str:
        token, token_hash = generate_api_token()
        with Session(world.engine) as session:
            session.add(APIToken(name="script", token_hash=token_hash, token_prefix=token[:8], user_id=world.ids[name]))
            session.commit()
        return token


class TestTurningItOff:
    def test_an_administrator_turns_it_off_with_a_code(self, world, clock):
        world.set_totp("admin", ADMIN_SECRET)
        require(world)

        answer = turn_off(world, clock)

        assert (answer.status_code, answer.json()) == (200, OFF)
        assert stored(world) is None
        assert changes(world) == [("settings_changed", "admin", "two_factor_requirement",
                                   "Two-factor authentication no longer required for every account", "warning")]

    def test_it_must_be_on(self, world, clock):
        world.set_totp("admin", ADMIN_SECRET)

        answer = turn_off(world, clock)

        assert (answer.status_code, answer.json()["detail"]) == (409, "Two-factor authentication is not required")
        assert changes(world) == []

    def test_it_needs_the_administrators_own_two_factor_authentication_and_a_right_code(self, world, clock, now):
        require(world)
        # Within the grace period, an administrator without two-factor authentication.
        assert turn_off(world, clock).status_code == 409
        world.set_totp("admin", ADMIN_SECRET)
        code = clock.code(ADMIN_SECRET)
        wrong = "000000" if code != "000000" else "111111"

        assert turn_off(world, clock, code=wrong).status_code == 400
        assert stored(world) is not None
        assert changes(world) == []

    def test_of_two_administrators_at_once_only_one_turns_it_off(self, world, clock, monkeypatch):
        world.set_totp("admin", ADMIN_SECRET)
        require(world)

        def the_other_one_first(user_id, secret, code):
            with Session(world.engine) as session:
                session.delete(session.get(TwoFactorRequirement, 1))
                session.commit()
            return TotpCheck.OK

        monkeypatch.setattr(security_router, "check_totp_code", the_other_one_first)

        answer = turn_off(world, clock)

        assert (answer.status_code, answer.json()["detail"]) == (409, "Two-factor authentication is not required")
        assert changes(world) == []

    def test_an_api_token_cannot(self, world, clock):
        world.set_totp("admin", ADMIN_SECRET)
        require(world)
        token = TestTurningItOn.api_token(world, "admin")

        answer = turn_off(world, clock, auth={"Authorization": f"Bearer {token}"})

        assert answer.status_code == 403
        assert stored(world) is not None

    def test_setup_only_accounts_have_their_rights_again_at_their_next_request(self, world, clock, now):
        world.set_totp("admin", ADMIN_SECRET)
        require(world, 0)
        alice = world.headers("alice")
        assert refused(world.client.get(f"{API}/auth/me/smb", headers=alice))

        turn_off(world, clock)

        assert world.client.get(f"{API}/auth/me/smb", headers=alice).status_code == 200
        assert me(world, alice)["two_factor_required"] is None


class TestGracePeriod:
    def test_during_it_everything_works_and_the_account_is_told_by_when(self, world, now):
        require(world, 7)
        now.value = START + timedelta(days=6, hours=23)
        token = sign_in(world).json()["access_token"]
        alice = {"Authorization": f"Bearer {token}"}

        assert me(world, alice)["two_factor_required"] == {"by": utc(START + timedelta(days=7)), "setup_only": False}
        assert world.client.get(f"{API}/auth/me/smb", headers=alice).status_code == 200
        assert world.client.get(f"{API}/auth/tokens", headers=alice).status_code == 200

    def test_after_it_the_same_session_can_only_set_up_two_factor_authentication(self, world, now):
        require(world, 7)
        alice = world.headers("alice")
        assert world.client.get(f"{API}/auth/me/smb", headers=alice).status_code == 200

        now.value = START + timedelta(days=7)

        assert refused(world.client.get(f"{API}/auth/me/smb", headers=alice))
        assert refused(world.client.get(f"{API}/system/info", headers=alice))
        assert me(world, alice)["two_factor_required"] == {"by": utc(START + timedelta(days=7)), "setup_only": True}

    def test_an_account_made_after_the_requirement_has_a_grace_period_of_its_own(self, world, now):
        require(world, 3)
        world.add_user("carol", "Carol-pass-2026")
        created(world, "carol", START + timedelta(days=10))
        now.value = START + timedelta(days=12)
        carol = world.headers("carol")

        assert me(world, carol)["two_factor_required"] == {"by": utc(START + timedelta(days=13)), "setup_only": False}
        assert world.client.get(f"{API}/auth/me/smb", headers=carol).status_code == 200
        now.value = START + timedelta(days=13)
        assert refused(world.client.get(f"{API}/auth/me/smb", headers=carol))

    def test_an_account_with_two_factor_authentication_is_never_restricted(self, world, now):
        require(world, 0)
        world.set_totp("alice", ALICE_SECRET)
        alice = world.headers("alice")
        now.value = START + timedelta(days=365)

        assert me(world, alice)["two_factor_required"] is None
        assert world.client.get(f"{API}/auth/me/smb", headers=alice).status_code == 200

    def test_the_administrators_are_held_to_it_too(self, world, now):
        require(world, 0)
        admin = world.headers("admin")

        assert refused(world.client.get(f"{API}/auth/users", headers=admin))
        assert refused(world.client.get(REQUIREMENT, headers=admin))
        assert me(world, admin)["two_factor_required"]["setup_only"] is True


class TestSetupOnly:
    @pytest.fixture(autouse=True)
    def past_the_grace_period(self, world, now):
        require(world, 7)
        now.value = START + timedelta(days=8)

    def test_the_password_still_signs_in_and_the_sign_in_is_audited(self, world):
        answer = sign_in(world)

        assert answer.status_code == 200 and answer.json()["access_token"]
        alice = {"Authorization": f"Bearer {answer.json()['access_token']}"}
        assert me(world, alice)["two_factor_required"]["setup_only"] is True
        sign_ins = [(e["action"], e["username"], e["details"], e["severity"])
                    for e in world.audit() if e["action"] == "login_success"]
        assert sign_ins == [("login_success", "alice",
                             "Signed in after the grace period: can only set up two-factor authentication", "warning")]

    def test_the_setup_its_status_and_the_sign_out_are_reached(self, world, clock):
        alice = world.headers("alice")

        status = world.client.get(f"{API}/auth/totp/status", headers=alice)
        assert (status.status_code, status.json()) == (200, {"enabled": False, "required": True})
        setup = world.client.post(f"{API}/auth/totp/setup", headers=alice)
        assert setup.status_code == 200, setup.text
        secret = setup.json()["secret"]

        enabled = world.client.post(f"{API}/auth/totp/enable", json={"code": clock.code(secret)}, headers=alice)

        assert enabled.status_code == 200, enabled.text
        # The same session has the account's rights at its next request.
        assert world.client.get(f"{API}/auth/me/smb", headers=alice).status_code == 200
        assert me(world, alice)["two_factor_required"] is None
        assert world.client.post(f"{API}/auth/logout", headers=alice).status_code == 200

    def test_everything_else_is_refused(self, world):
        alice = world.headers("alice")

        for method, path in [("GET", "/auth/me/smb"), ("POST", "/auth/change-password"),
                             ("POST", "/auth/recovery-codes/generate"), ("POST", "/auth/tokens"),
                             ("GET", "/security/login-history"), ("POST", "/auth/totp/disable")]:
            assert refused(world.client.request(method, f"{API}{path}", json={}, headers=alice)), path

    def test_api_tokens_of_the_account_are_refused_until_it_has_two_factor_authentication(self, world, now):
        token = TestTurningItOn.api_token(world, "alice")
        auth = {"Authorization": f"Bearer {token}"}

        for method, path in [("GET", "/auth/me"), ("GET", "/auth/totp/status"), ("POST", "/auth/totp/setup"),
                             ("GET", "/auth/tokens"), ("GET", "/system/info")]:
            assert refused(world.client.request(method, f"{API}{path}", headers=auth)), path

        world.set_totp("alice", ALICE_SECRET)

        assert world.client.get(f"{API}/auth/me", headers=auth).status_code == 200
        assert world.client.get(f"{API}/auth/tokens", headers=auth).status_code == 200

    def test_api_tokens_work_during_the_grace_period(self, world, now):
        token = TestTurningItOn.api_token(world, "alice")
        now.value = START + timedelta(days=1)

        answer = world.client.get(f"{API}/auth/tokens", headers={"Authorization": f"Bearer {token}"})

        assert answer.status_code == 200

    def test_turning_two_factor_authentication_off_gives_no_new_grace_period(self, world, clock):
        world.set_totp("alice", ALICE_SECRET)
        alice = world.headers("alice")
        assert world.client.get(f"{API}/auth/me/smb", headers=alice).status_code == 200

        off = world.client.post(f"{API}/auth/totp/disable", json={"code": clock.code(ALICE_SECRET)}, headers=alice)

        assert off.status_code == 200, off.text
        assert refused(world.client.get(f"{API}/auth/me/smb", headers=alice))
        assert me(world, alice)["two_factor_required"]["setup_only"] is True

    def test_a_sign_in_with_a_second_factor_is_not_restricted(self, world, clock):
        world.set_totp("alice", ALICE_SECRET)

        answer = sign_in(world, totp_code=clock.code(ALICE_SECRET))

        auth = {"Authorization": f"Bearer {answer.json()['access_token']}"}
        assert world.client.get(f"{API}/auth/me/smb", headers=auth).status_code == 200
        assert [e["details"] for e in world.audit() if e["action"] == "login_success"] == [None]

    def test_an_emergency_reset_leads_the_administrator_to_the_setup(self, world, clock, monkeypatch, capsys):
        """reset-admin (src/emergency.py, ADR-0284) turns two-factor authentication off and leaves
        the requirement as it is: with the grace period over, the administrator's next sign-in can
        only set it up again."""
        world.set_totp("admin", ADMIN_SECRET)
        monkeypatch.setattr(get_settings(), "db_path", str(world.path))
        monkeypatch.setattr(emergency, "hash_password", _hash)
        monkeypatch.setattr(sys, "stdin", io.StringIO(f"{RESET_PW}\n"))

        assert emergency.main(["reset-admin", "admin"]) == 0, capsys.readouterr()

        assert stored(world) == (7, START, "admin")
        token = sign_in(world, "admin", RESET_PW).json()["access_token"]

        auth = {"Authorization": f"Bearer {token}"}
        assert me(world, auth)["two_factor_required"]["setup_only"] is True
        assert refused(world.client.get(f"{API}/auth/users", headers=auth))
        assert world.client.post(f"{API}/auth/totp/setup", headers=auth).status_code == 200


class TestTheStatusOfTheAccount:
    def test_totp_status_says_whether_it_is_required(self, world):
        alice = world.headers("alice")
        assert world.client.get(f"{API}/auth/totp/status", headers=alice).json() == {"enabled": False, "required": False}

        require(world)

        assert world.client.get(f"{API}/auth/totp/status", headers=alice).json() == {"enabled": False, "required": True}

    def test_the_user_list_says_nothing_of_it(self, world):
        require(world, 0)
        world.set_totp("admin", ADMIN_SECRET)

        users = world.client.get(f"{API}/auth/users", headers=world.headers("admin")).json()

        assert {user["two_factor_required"] for user in users} == {None}

    def test_a_restored_grace_period_out_of_range_is_held_within_it(self, world, now):
        """The row of a restored database is not trusted (ADR-0144)."""
        require(world, 10**9)
        now.value = START + timedelta(days=31)

        assert me(world, world.headers("alice"))["two_factor_required"] == {
            "by": utc(START + timedelta(days=30)), "setup_only": True}


class TestARestoredRow:
    """A restored database's row can hold anything (ADR-0144): no value of it makes a request fail.
    Without a readable start, the requirement holds without a grace period (it fails closed), the
    setup stays reachable, and an administrator with two-factor authentication can turn it off."""

    @staticmethod
    def sql(world: World, *statements: str) -> None:
        with closing(sqlite3.connect(world.path)) as conn:
            for statement in statements:
                conn.execute(statement)
            conn.commit()

    def expect_setup_only_and_no_failure(self, world, clock):
        token = sign_in(world).json()["access_token"]
        alice = {"Authorization": f"Bearer {token}"}
        assert me(world, alice)["two_factor_required"]["setup_only"] is True
        assert refused(world.client.get(f"{API}/auth/me/smb", headers=alice))
        assert world.client.post(f"{API}/auth/totp/setup", headers=alice).status_code == 200
        assert world.client.get(f"{API}/auth/totp/status", headers=alice).json() == {"enabled": False, "required": True}
        world.set_totp("admin", ADMIN_SECRET)
        status = world.client.get(REQUIREMENT, headers=world.headers("admin"))
        assert status.status_code == 200 and status.json()["enabled"] is True
        assert turn_off(world, clock).status_code == 200
        assert world.client.get(f"{API}/auth/me/smb", headers=alice).status_code == 200

    # A time that cannot be read, and aware times at either end of the calendar, which have no
    # UTC (astimezone raises OverflowError).
    @pytest.mark.parametrize("enabled_at", [
        "'x'", "42", "'0001-01-01 00:00:00+01:00'", "'9999-12-31 23:59:59-01:00'",
    ])
    def test_a_start_that_cannot_be_read(self, world, clock, enabled_at):
        require(world, 30)
        self.sql(world, f"UPDATE two_factor_requirement SET enabled_at = {enabled_at}")

        self.expect_setup_only_and_no_failure(world, clock)

    @pytest.mark.parametrize("layout", [
        "id INTEGER PRIMARY KEY, other TEXT",
        "other TEXT",  # without even an id
    ])
    def test_a_table_of_another_layout_with_a_row(self, world, clock, monkeypatch, layout):
        monkeypatch.setattr(requirement, "_warned", False)
        self.sql(world, "DROP TABLE two_factor_requirement", f"CREATE TABLE two_factor_requirement ({layout})",
                 "INSERT INTO two_factor_requirement (other) VALUES ('y')")

        self.expect_setup_only_and_no_failure(world, clock)
        # Said once, not at every request.
        assert world.caplog.text.count("The two-factor requirement cannot be read") == 1

    def test_a_table_of_another_layout_without_a_row_is_off_and_takes_no_code(self, world, clock):
        self.sql(world, "DROP TABLE two_factor_requirement",
                 "CREATE TABLE two_factor_requirement (id INTEGER PRIMARY KEY, other TEXT)")
        world.set_totp("admin", ADMIN_SECRET)
        alice = world.headers("alice")

        assert me(world, alice)["two_factor_required"] is None
        assert world.client.get(f"{API}/auth/me/smb", headers=alice).status_code == 200
        answer = turn_on(world, clock)

        assert (answer.status_code, answer.json()["detail"]) == (
            500, "The two-factor requirement cannot be stored: its table in the database has another layout")
        assert world.ids["admin"] not in auth_utils._last_totp_steps  # the code was not used up

    def test_a_missing_table_is_off(self, world, clock):
        """As between the swap of a restore of an older backup and init_db, which makes the table."""
        self.sql(world, "DROP TABLE two_factor_requirement")
        world.set_totp("admin", ADMIN_SECRET)
        alice = world.headers("alice")
        admin = world.headers("admin")

        assert me(world, alice)["two_factor_required"] is None
        assert world.client.get(f"{API}/auth/me/smb", headers=alice).status_code == 200
        assert world.client.get(REQUIREMENT, headers=admin).json() == OFF
        assert turn_off(world, clock).status_code == 409
        assert turn_on(world, clock).status_code == 500
        assert world.ids["admin"] not in auth_utils._last_totp_steps
        assert "cannot be read" not in world.caplog.text

    def test_a_start_ahead_of_the_clock_starts_the_grace_period_now(self, world, now):
        """A clock that is behind, as on a board without a real-time clock before NTP sets it,
        never skips the grace period."""
        require(world, 7, at=START + timedelta(days=10))

        assert me(world, world.headers("alice"))["two_factor_required"] == {
            "by": utc(START + timedelta(days=7)), "setup_only": False}
        status = world.client.get(REQUIREMENT, headers=world.headers("admin")).json()
        assert status["enabled_at"] == utc(START + timedelta(days=10))

    def test_without_a_grace_period_a_start_ahead_of_the_clock_holds_at_once(self, world, now):
        require(world, 0, at=START + timedelta(days=10))

        assert me(world, world.headers("alice"))["two_factor_required"]["setup_only"] is True

    def test_an_account_made_after_now_counts_as_made_now(self, world, now):
        require(world, 3)
        created(world, "alice", datetime(9999, 12, 31))
        now.value = START + timedelta(days=3)

        assert me(world, world.headers("alice"))["two_factor_required"] == {
            "by": utc(START + timedelta(days=6)), "setup_only": False}


class TestARestore:
    """A restore can turn the requirement on or off with the database it brings back: its result
    says so, and the audit log records it (settings_changed of two_factor_requirement)."""

    ON = ("The restored database requires two-factor authentication for every account: accounts "
          "without it are asked to set it up, and after their grace period can only do that.")
    OFF = "The restored database does not require two-factor authentication for every account."

    @pytest.fixture
    def env(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.audit.router.AUDIT_FILE", str(tmp_path / "audit.jsonl"))
        yield from make_env(tmp_path, monkeypatch, smb_applied=True)

    @staticmethod
    def with_requirement(path: Path, on: bool) -> Path:
        engine = create_engine(f"sqlite:///{path}")
        Base.metadata.create_all(engine, tables=[TwoFactorRequirement.__table__])
        if on:
            with engine.begin() as conn:
                conn.execute(TwoFactorRequirement.__table__.insert().values(
                    id=1, grace_days=7, enabled_at=START, enabled_by="admin2"))
        engine.dispose()
        return path

    def restore(self, env, backup: Path):
        name = _store(env, [_file("manifest.json", MANIFEST), _file("unos.db", backup.read_bytes())])
        answer = _restore(env, name)
        assert answer.status_code == 200, answer.text
        return answer.json()

    def entries(self, env):
        return [(e["action"], e["resource_type"], e["details"], e["severity"])
                for e in audit_entries(env.tmp / "audit.jsonl") if e["resource_type"] == "two_factor_requirement"]

    def backup(self, env, *, table: bool, on: bool = False) -> Path:
        path = env.tmp / "restored.db"
        _make_db(path, users=[("admin2", 1)])
        return self.with_requirement(path, on) if table else path

    def test_a_restore_that_turns_it_on(self, env):
        body = self.restore(env, self.backup(env, table=True, on=True))

        assert self.ON in body["warnings"] and self.ON in body["message"]
        assert self.entries(env) == [(AuditAction.SETTINGS_CHANGED.value, "two_factor_requirement",
                                      "Two-factor authentication required for every account by the restored database",
                                      "warning")]
        assert "_two_factor_requirement" not in body

    @pytest.mark.parametrize("table", [True, False], ids=["an empty table", "an older backup without the table"])
    def test_a_restore_that_turns_it_off(self, env, table):
        self.with_requirement(env.db_path, on=True)

        body = self.restore(env, self.backup(env, table=table))

        assert self.OFF in body["warnings"]
        assert self.entries(env) == [(AuditAction.SETTINGS_CHANGED.value, "two_factor_requirement",
                                      "Two-factor authentication no longer required for every account: the restored "
                                      "database does not require it", "warning")]

    @pytest.mark.parametrize("on", [True, False])
    def test_a_restore_that_keeps_it_says_nothing_of_it(self, env, on):
        self.with_requirement(env.db_path, on=on)

        body = self.restore(env, self.backup(env, table=True, on=on))

        assert body["warnings"] == []
        assert self.entries(env) == []


class TestWebSocket:
    @pytest.fixture
    def sockets(self, world, monkeypatch):
        monkeypatch.setattr(events, "STATS_INTERVAL_SECONDS", 0.05)
        monkeypatch.setattr(events, "get_system_stats", lambda: {"type": "stats", "data": {}})
        with patch.object(events, "async_session", world.maker):
            yield world.client

    @staticmethod
    def closed_with(ws) -> tuple[int, str]:
        with pytest.raises(WebSocketDisconnect) as closed:
            for _ in range(100):
                ws.receive_json()
        return closed.value.code, closed.value.reason

    @pytest.mark.parametrize("path", ["/ws/v1/events", "/ws/v1/logs/abc", "/ws/v1/journal"])
    def test_a_setup_only_account_is_refused_with_4403(self, world, sockets, now, path):
        require(world, 0)
        token = sign_in(world, "admin", ADMIN_PW).json()["access_token"]

        with sockets.client.websocket_connect(path) as ws:
            ws.send_json({"type": "auth", "token": token})
            assert self.closed_with(ws) == (4403, "forbidden")

    def test_a_socket_closes_with_4403_when_the_grace_period_ends(self, world, sockets, now, monkeypatch):
        monkeypatch.setattr(events, "WS_REAUTH_INTERVAL_SECONDS", 0.1)
        require(world, 7)
        token = sign_in(world).json()["access_token"]

        with sockets.client.websocket_connect("/ws/v1/events") as ws:
            ws.send_json({"type": "auth", "token": token})
            assert ws.receive_json() == {"type": "auth_ok"}
            now.value = START + timedelta(days=7)
            assert self.closed_with(ws) == (4403, "forbidden")


class TestTheTable:
    def test_turning_it_on_and_off_writes_only_its_row(self, world, clock):
        world.set_totp("admin", ADMIN_SECRET)
        with Session(world.engine) as session:
            users = session.execute(select(User.id, User.updated_at)).all()

        turn_on(world, clock)
        clock.next_step()
        turn_off(world, clock)

        with Session(world.engine) as session:
            assert session.execute(select(User.id, User.updated_at)).all() == users
            assert session.scalars(select(TwoFactorRequirement)).all() == []


def test_the_web_ui_knows_the_servers_text():
    """The web UI leads a refused request to the setup by the server's text (webui/src/lib/api/client.ts)."""
    client = (REPO / "webui/src/lib/api/client.ts").read_text()

    assert f"'{TWO_FACTOR_SETUP_REQUIRED}'" in client
