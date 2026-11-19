"""Two-factor codes are single-use (ADR-0067; RFC 6238, section 5.2).

Every place that checks a TOTP code (login, turning SMB on, the setup confirmation,
turning 2FA off) refuses a code whose time step is not newer than the last step that
user had accepted, with the endpoint's usual wrong-code status and the detail
"This two-factor code was already used; wait for the next one". The time the server
checks codes at is controlled by the tests (auth.utils._totp_time), so no test
depends on where the 30-second steps fall.
"""
from __future__ import annotations

from datetime import datetime, timedelta, UTC
from urllib.parse import parse_qs, unquote, urlsplit

import pyotp
import pytest
from sqlalchemy.orm import Session

from src.auth import utils as auth_utils
from src.auth.models import User

from .test_smb_accounts import ALICE_PW, API, BOB_PW, World

REUSED = "This two-factor code was already used; wait for the next one"
SECRET = pyotp.random_base32()


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 7, 12, 0, 5, tzinfo=UTC)

    def code(self, secret: str = SECRET) -> str:
        return pyotp.TOTP(secret).at(self.now)

    def next_step(self) -> None:
        self.now += timedelta(seconds=30)


@pytest.fixture
def world(tmp_path, monkeypatch, caplog):
    w = World(tmp_path, monkeypatch, caplog)
    try:
        yield w
    finally:
        w.close()


@pytest.fixture
def clock(monkeypatch) -> Clock:
    clock = Clock()
    monkeypatch.setattr(auth_utils, "_totp_time", lambda: clock.now)
    return clock


def login(world: World, name: str, password: str, code: str | None):
    body = {"username": name, "password": password}
    if code is not None:
        body["totp_code"] = code
    return world.client.post(f"{API}/auth/login", json=body)


class TestLogin:
    def test_a_code_signs_in_once(self, world, clock):
        world.set_totp("alice", SECRET)
        code = clock.code()

        first = login(world, "alice", ALICE_PW, code)
        second = login(world, "alice", ALICE_PW, code)

        assert first.status_code == 200 and first.json()["access_token"]
        assert (second.status_code, second.json()["detail"]) == (401, REUSED)

    def test_the_next_code_signs_in(self, world, clock):
        world.set_totp("alice", SECRET)
        assert login(world, "alice", ALICE_PW, clock.code()).status_code == 200

        clock.next_step()

        assert login(world, "alice", ALICE_PW, clock.code()).status_code == 200

    def test_a_wrong_code_uses_nothing_up(self, world, clock):
        world.set_totp("alice", SECRET)
        wrong = "000000" if clock.code() != "000000" else "111111"

        refused = login(world, "alice", ALICE_PW, wrong)
        right = login(world, "alice", ALICE_PW, clock.code())

        assert (refused.status_code, refused.json()["detail"]) == (401, "Invalid 2FA code")
        assert right.status_code == 200

    def test_a_code_of_an_older_step(self, world, clock):
        """After a code was used, the code of the step before it is never accepted (a clock
        that went back), and valid_window stays 0: the step after it is not valid yet."""
        world.set_totp("alice", SECRET)
        assert login(world, "alice", ALICE_PW, clock.code()).status_code == 200
        older = pyotp.TOTP(SECRET).at(clock.now - timedelta(seconds=30))
        newer = pyotp.TOTP(SECRET).at(clock.now + timedelta(seconds=30))

        assert login(world, "alice", ALICE_PW, newer).json()["detail"] == "Invalid 2FA code"
        clock.now -= timedelta(seconds=30)
        assert login(world, "alice", ALICE_PW, older).json()["detail"] == REUSED

    def test_users_do_not_share_steps(self, world, clock):
        world.set_totp("alice", SECRET)
        world.set_totp("bob", SECRET)

        assert login(world, "alice", ALICE_PW, clock.code()).status_code == 200
        assert login(world, "bob", BOB_PW, clock.code()).status_code == 200

    def test_a_reused_code_is_recorded_as_a_failed_login(self, world, clock):
        from src.security.models import LoginAttempt

        world.set_totp("alice", SECRET)
        code = clock.code()
        login(world, "alice", ALICE_PW, code)
        login(world, "alice", ALICE_PW, code)

        with Session(world.engine) as session:
            reasons = [a.failure_reason for a in session.query(LoginAttempt).order_by(LoginAttempt.id)]
        assert reasons == [None, "Reused 2FA code"]


class TestSmbSelfActivation:
    def test_the_code_of_the_login_does_not_turn_smb_on(self, world, clock):
        """ADR-0067: someone who saw the password and the code at the login cannot turn SMB on
        (a channel without 2FA) with them in the same 30 seconds."""
        world.set_totp("alice", SECRET)
        world.make_allowed("alice")
        code = clock.code()
        assert login(world, "alice", ALICE_PW, code).status_code == 200

        reused = world.self_enable(totp_code=code)

        assert (reused.status_code, reused.json()["detail"]) == (400, REUSED)
        assert world.fake.commands("smbpasswd") == []

        clock.next_step()
        enabled = world.self_enable(totp_code=clock.code())
        assert enabled.status_code == 200, enabled.text
        assert enabled.json()["account"]["state"] == "active"

    def test_one_code_turns_smb_on_once(self, world, clock):
        world.set_totp("alice", SECRET)
        world.make_allowed("alice")
        code = clock.code()

        assert world.self_enable(totp_code=code).status_code == 200
        assert world.self_disable().status_code == 200
        again = world.self_enable(totp_code=code)

        assert (again.status_code, again.json()["detail"]) == (400, REUSED)
        assert len(world.fake.commands("smbpasswd")) == 1

    def test_a_wrong_code(self, world, clock):
        world.set_totp("alice", SECRET)
        world.make_allowed("alice")
        wrong = "000000" if clock.code() != "000000" else "111111"

        response = world.self_enable(totp_code=wrong)

        assert (response.status_code, response.json()["detail"]) == (400, "Invalid two-factor code")


class TestSetupAndDisable:
    def test_the_qr_code_carries_the_secret_of_the_setup(self, world, clock):
        """The QR code of the setup holds qr_uri, the otpauth:// URI that authenticator apps read
        (Key Uri Format): its secret is the one shown for manual entry and stored, and the codes
        of an app that read it turn 2FA on."""
        headers = world.headers("alice")
        setup = world.client.post(f"{API}/auth/totp/setup", headers=headers).json()
        uri = urlsplit(setup["qr_uri"])
        query = parse_qs(uri.query, strict_parsing=True)

        assert (uri.scheme, uri.netloc, unquote(uri.path)) == ("otpauth", "totp", "/UNOS:alice")
        assert query["secret"] == [setup["secret"]]
        assert query["issuer"] == ["UNOS"]
        # What the app takes as given when they are left out: SHA-1, 6 digits, 30 seconds.
        assert set(query) == {"secret", "issuer"}
        assert world.user("alice").totp_secret == setup["secret"]
        app = pyotp.parse_uri(setup["qr_uri"])
        enabled = world.client.post(f"{API}/auth/totp/enable", json={"code": app.at(clock.now)}, headers=headers)
        assert enabled.status_code == 200, enabled.text

    def test_the_setup_code_does_not_sign_in(self, world, clock):
        headers = world.headers("alice")
        secret = world.client.post(f"{API}/auth/totp/setup", headers=headers).json()["secret"]
        code = clock.code(secret)

        enabled = world.client.post(f"{API}/auth/totp/enable", json={"code": code}, headers=headers)
        reused = login(world, "alice", ALICE_PW, code)

        assert enabled.status_code == 200, enabled.text
        assert (reused.status_code, reused.json()["detail"]) == (401, REUSED)
        clock.next_step()
        assert login(world, "alice", ALICE_PW, clock.code(secret)).status_code == 200

    def test_the_login_code_does_not_confirm_a_setup(self, world, clock):
        """The setup confirmation checks codes once too (here with the secret of a new setup)."""
        headers = world.headers("alice")
        secret = world.client.post(f"{API}/auth/totp/setup", headers=headers).json()["secret"]
        code = clock.code(secret)
        assert world.client.post(f"{API}/auth/totp/enable", json={"code": code}, headers=headers).status_code == 200
        with Session(world.engine) as session:
            user = session.get(User, world.ids["alice"])
            user.totp_enabled = False  # as if the confirmation had to be repeated
            session.commit()

        again = world.client.post(f"{API}/auth/totp/enable", json={"code": code}, headers=headers)

        assert (again.status_code, again.json()["detail"]) == (400, REUSED)

    def test_the_login_code_does_not_turn_2fa_off(self, world, clock):
        world.set_totp("alice", SECRET)
        code = clock.code()
        token = login(world, "alice", ALICE_PW, code).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        reused = world.client.post(f"{API}/auth/totp/disable", json={"code": code}, headers=headers)
        assert (reused.status_code, reused.json()["detail"]) == (400, REUSED)
        assert world.user("alice").totp_enabled is True

        clock.next_step()
        disabled = world.client.post(f"{API}/auth/totp/disable", json={"code": clock.code()}, headers=headers)
        assert disabled.status_code == 200, disabled.text
        assert world.user("alice").totp_enabled is False


class TestCheck:
    """auth.utils.check_totp_code on its own."""

    def test_results(self, clock, monkeypatch):
        monkeypatch.setattr(auth_utils, "_last_totp_steps", {})
        code = clock.code()

        assert auth_utils.check_totp_code(7, SECRET, code) is auth_utils.TotpCheck.OK
        assert auth_utils.check_totp_code(7, SECRET, code) is auth_utils.TotpCheck.REUSED
        assert auth_utils.check_totp_code(8, SECRET, code) is auth_utils.TotpCheck.OK

    # Ids of their own: SECRET is random in each process, and the workers of a parallel run must
    # collect the same tests.
    @pytest.mark.parametrize("secret, code", [
        (None, "123456"), ("", "123456"), ("not base32!", "123456"), (SECRET, ""), (SECRET, None), (SECRET, 123456),
    ], ids=["no-secret", "empty-secret", "not-base32", "empty-code", "no-code", "code-as-a-number"])
    def test_no_usable_secret_or_code(self, clock, monkeypatch, secret, code):
        monkeypatch.setattr(auth_utils, "_last_totp_steps", {})

        assert auth_utils.check_totp_code(7, secret, code) is auth_utils.TotpCheck.INVALID
        assert auth_utils._last_totp_steps == {}

    def test_a_deleted_users_steps_are_forgotten(self, world, clock):
        """SQLite gives the id of the highest deleted user to the next one."""
        world.set_totp("bob", SECRET)
        assert login(world, "bob", BOB_PW, clock.code()).status_code == 200
        assert world.ids["bob"] in auth_utils._last_totp_steps

        assert world.delete_user("bob").status_code == 200

        assert world.ids["bob"] not in auth_utils._last_totp_steps
