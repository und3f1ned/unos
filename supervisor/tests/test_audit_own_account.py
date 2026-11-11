"""A user's own sign-out, password change and two-factor changes are in the audit log
(security.audit-admin-actions, step 4; ADR-0343).

POST /auth/logout, POST /auth/change-password, POST /auth/totp/enable and POST /auth/totp/disable
each write one entry of their action (logout, password_change, totp_enabled, totp_disabled) for a
change they made, with the user, the client address and the user as the resource, as the other
entries of the auth router do. A refused attempt (a wrong current password, a wrong or reused
code, an API token at the sign-out) and a request that found nothing to change write none
(ADR-0320: entries record real changes). An entry that cannot be written, as on a full /data, is in
the supervisor's log, and the change, already stored, answers as it does otherwise (as a sign-in
does, ADR-0251).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.orm import Session

from src.auth import router as auth_router
from src.auth import utils as auth_utils
from src.auth.models import APIToken, UserSession
from src.auth.utils import generate_api_token
from src.main import app

from .test_sign_in_full_disk import ENTRY_LOST, reported, the_audit_log_is_full
from .test_smb_accounts import ALICE_PW, API, NEW_PW, RecordingClient, World
from .test_totp_once import SECRET, Clock

CLIENT = "192.0.2.20"  # the address the test client connects from
BROWSER = "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0"
FIELDS = ("action", "username", "ip_address", "user_agent", "resource_type", "resource_id", "details",
          "severity", "success")


@pytest.fixture
def world(tmp_path, monkeypatch, caplog):
    w = World(tmp_path, monkeypatch, caplog)
    w.client = RecordingClient(TestClient(app, raise_server_exceptions=False, client=(CLIENT, 50000)))
    try:
        yield w
    finally:
        w.close()


@pytest.fixture
def clock(monkeypatch) -> Clock:
    clock = Clock()
    monkeypatch.setattr(auth_utils, "_totp_time", lambda: clock.now)
    return clock


def headers(world: World, name: str = "alice") -> dict[str, str]:
    """A new session of ``name``, from the browser BROWSER."""
    return {**world.headers(name), "User-Agent": BROWSER}


def entries(world: World) -> list[dict]:
    return [{field: entry.get(field) for field in FIELDS} for entry in world.audit()]


def own(action: str, details: str | None, severity: str = "info", name: str = "alice") -> dict:
    """An entry of ``name``'s own change of their account."""
    return {"action": action, "username": name, "ip_address": CLIENT, "user_agent": BROWSER,
            "resource_type": "user", "resource_id": name, "details": details, "severity": severity,
            "success": True}


def wrong(code: str) -> str:
    return "000000" if code != "000000" else "111111"


class TestSignOut:
    def test_signing_out_writes_a_logout_entry(self, world):
        response = world.client.post(f"{API}/auth/logout", headers=headers(world))

        assert response.status_code == 200, response.text
        assert entries(world) == [own("logout", None)]

    def test_an_api_token_writes_nothing(self, world):
        full_token, token_hash = generate_api_token()
        with Session(world.engine) as session:
            session.add(APIToken(name="script", token_hash=token_hash, token_prefix=full_token[:12],
                                 user_id=world.ids["alice"]))
            session.commit()

        response = world.client.post(f"{API}/auth/logout", headers={"Authorization": f"Bearer {full_token}"})

        assert response.status_code == 400
        assert world.audit() == []

    def test_a_session_that_ended_meanwhile_writes_nothing(self, world, monkeypatch):
        """Two sign-outs of one session at the same moment: the one that comes second ends
        nothing, and only the first writes its entry."""
        end_session = auth_router.end_session

        async def ended_by_the_other_sign_out(db, session_id):
            with Session(world.engine) as session:
                session.execute(delete(UserSession).where(UserSession.id == session_id))
                session.commit()
            return await end_session(db, session_id)

        monkeypatch.setattr(auth_router, "end_session", ended_by_the_other_sign_out)

        response = world.client.post(f"{API}/auth/logout", headers=headers(world))

        assert response.status_code == 200, response.text
        assert world.audit() == []


class TestPasswordChange:
    def change(self, world: World, current: str, new: str = NEW_PW):
        return world.client.post(f"{API}/auth/change-password",
                                 json={"current_password": current, "new_password": new}, headers=headers(world))

    def test_a_password_change_writes_a_password_change_entry(self, world):
        response = self.change(world, ALICE_PW)

        assert response.status_code == 200, response.text
        assert entries(world) == [own("password_change", "Password changed by the user")]

    def test_a_wrong_current_password_writes_nothing(self, world):
        response = self.change(world, "Not-alices-password-1")

        assert (response.status_code, response.json()["detail"]) == (400, "Current password is incorrect")
        assert world.audit() == []

    def test_the_smb_password_of_an_active_account_has_its_entry_too(self, world):
        """ADR-0320: a new SMB password is an smb_account_updated entry, as when an
        administrator sets it."""
        world.make_active("alice", 70000, ALICE_PW)

        response = self.change(world, ALICE_PW)

        assert response.status_code == 200, response.text
        assert entries(world) == [
            own("password_change", "Password changed by the user, also for SMB"),
            {**own("smb_account_updated", "SMB password changed by the user"), "resource_type": "smb_account"},
        ]

    def test_a_password_that_samba_refuses_writes_nothing(self, world):
        world.make_active("alice", 70000, ALICE_PW)
        world.fake.fail[("smbpasswd",)] = (1, "tdb_store failed")

        response = self.change(world, ALICE_PW)

        assert response.status_code == 503
        assert world.audit() == []


class TestTwoFactor:
    def turn_on(self, world: World, clock: Clock, code: str | None = None):
        auth = headers(world)
        secret = world.client.post(f"{API}/auth/totp/setup", headers=auth).json()["secret"]
        return world.client.post(f"{API}/auth/totp/enable", json={"code": code or clock.code(secret)},
                                 headers=auth), secret

    def turn_off(self, world: World, code: str):
        return world.client.post(f"{API}/auth/totp/disable", json={"code": code}, headers=headers(world))

    def test_turning_it_on_writes_a_totp_enabled_entry(self, world, clock):
        response, _ = self.turn_on(world, clock)

        assert response.status_code == 200, response.text
        assert entries(world) == [own("totp_enabled", "Two-factor authentication turned on by the user")]

    def test_a_wrong_code_at_turning_it_on_writes_nothing(self, world, clock):
        response, _ = self.turn_on(world, clock, code=wrong(clock.code()))

        assert (response.status_code, response.json()["detail"]) == (400, "Invalid TOTP code")
        assert world.user("alice").totp_enabled is False
        assert world.audit() == []

    def test_turning_it_on_again_writes_nothing(self, world, clock):
        first, secret = self.turn_on(world, clock)
        assert first.status_code == 200, first.text
        clock.next_step()

        again = world.client.post(f"{API}/auth/totp/enable", json={"code": clock.code(secret)},
                                  headers=headers(world))

        assert (again.status_code, again.json()["detail"]) == (400, "2FA is already enabled")
        assert [entry["action"] for entry in world.audit()] == ["totp_enabled"]

    def test_turning_it_off_writes_a_totp_disabled_warning(self, world, clock):
        world.set_totp("alice", SECRET)

        response = self.turn_off(world, clock.code())

        assert response.status_code == 200, response.text
        assert entries(world) == [
            own("totp_disabled", "Two-factor authentication turned off by the user, with its recovery codes",
                "warning"),
        ]

    @pytest.mark.parametrize("reused", [False, True], ids=["wrong", "reused"])
    def test_a_refused_code_at_turning_it_off_writes_nothing(self, world, clock, reused):
        world.set_totp("alice", SECRET)
        code = clock.code()
        if reused:
            login = world.client.post(f"{API}/auth/login",
                                      json={"username": "alice", "password": ALICE_PW, "totp_code": code})
            assert login.status_code == 200, login.text
            world.audit_file.unlink()  # the sign-in's entry is not under test

        response = self.turn_off(world, code if reused else wrong(code))

        assert response.status_code == 400
        assert world.user("alice").totp_enabled is True
        assert world.audit() == []

    def test_turning_it_off_while_it_is_off_writes_nothing(self, world, clock):
        response = self.turn_off(world, clock.code())

        assert (response.status_code, response.json()["detail"]) == (400, "2FA is not enabled")
        assert world.audit() == []


class TestAnEntryThatCannotBeWritten:
    # The flow -> the actions of the entries it reports in the supervisor's log.
    FLOWS = {
        "logout": ["logout"],
        "change-password": ["password_change"],
        "change-password-also-for-smb": ["password_change", "smb_account_updated"],
        "totp-enable": ["totp_enabled"],
        "totp-disable": ["totp_disabled"],
    }

    @pytest.mark.parametrize("flow", FLOWS)
    def test_the_change_answers_and_the_log_has_the_entry(self, world, clock, monkeypatch, flow):
        auth = headers(world)
        if flow == "change-password-also-for-smb":
            world.make_active("alice", 70000, ALICE_PW)
        if flow == "totp-enable":
            secret = world.client.post(f"{API}/auth/totp/setup", headers=auth).json()["secret"]
        if flow == "totp-disable":
            world.set_totp("alice", SECRET)
        the_audit_log_is_full(monkeypatch)

        if flow == "logout":
            response = world.client.post(f"{API}/auth/logout", headers=auth)
        elif flow.startswith("change-password"):
            response = world.client.post(f"{API}/auth/change-password",
                                         json={"current_password": ALICE_PW, "new_password": NEW_PW}, headers=auth)
        elif flow == "totp-enable":
            response = world.client.post(f"{API}/auth/totp/enable", json={"code": clock.code(secret)},
                                         headers=auth)
        else:
            response = world.client.post(f"{API}/auth/totp/disable", json={"code": clock.code()},
                                         headers=auth)

        assert response.status_code == 200, response.text
        lost = [entry for entry, _ in reported(world, ENTRY_LOST)]
        assert [(entry["action"], entry["username"], entry["ip_address"]) for entry in lost] \
            == [(action, "alice", CLIENT) for action in self.FLOWS[flow]]
