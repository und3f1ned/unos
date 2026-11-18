"""A user's own sign-out, password change and two-factor changes are in the audit log
(security.audit-admin-actions, step 4; ADR-0343).

POST /auth/logout, POST /auth/change-password, POST /auth/totp/enable and POST /auth/totp/disable
each write one entry of their action (logout, password_change, totp_enabled, totp_disabled) for a
change they made, with the user, the client address and the user as the resource, as the other
entries of the auth router do. A refused attempt (a wrong current password, a wrong or reused
code, an API token at the sign-out) and a request that found nothing to change write none
(ADR-0320: entries record real changes). An entry that cannot be written, as on a full /data, is in
the supervisor's log, and the change, already stored, answers as it does otherwise (as a sign-in
does, ADR-0251). The entry follows the commit: a change that cannot be stored writes none.
Turning 2FA on also needs the secret its code was checked against to be still the stored one, and
a setup never replaces the secret of 2FA that is on (TestTwoFactor).
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from src.audit import router as audit_router
from src.auth import router as auth_router
from src.auth import utils as auth_utils
from src.auth.models import APIToken
from src.auth.utils import decode_access_token, generate_api_token
from src.main import app

from .test_sign_in_full_disk import (
    ATTEMPT_LOST, ENTRY_LOST, every_commit_fails, fill_data, reported, sign_in, the_audit_log_is_full,
)
from .test_smb_accounts import ALICE_PW, API, NEW_PW, SECRETS, RecordingClient, World
from .test_totp_once import SECRET, Clock

CLIENT = "192.0.2.20"  # the address the test client connects from
BROWSER = "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0"
FIELDS = ("action", "username", "ip_address", "user_agent", "resource_type", "resource_id", "details",
          "severity", "success")
# The fields of an audit entry or a login attempt whose digits are their own: the microseconds of a
# timestamp are six digits, as a two-factor code is, and an entry's id and hashes may hold six in a
# row too. The teardown's scan for codes leaves them out (free_text).
NOT_FREE_TEXT = ("id", "timestamp", "prev_hash", "hash")


class RecordingClock(Clock):
    """A Clock that keeps the secrets it made codes of, and the codes, for the secrets check."""

    def __init__(self) -> None:
        super().__init__()
        self.secrets: set[str] = {SECRET}
        self.codes: set[str] = set()

    def code(self, secret: str = SECRET) -> str:
        code = super().code(secret)
        self.secrets.add(secret)
        self.codes.add(code)
        return code


def free_text(record: dict) -> str:
    """``record``, an audit entry or a login attempt, as JSON without its fields NOT_FREE_TEXT."""
    kept = {key: value for key, value in record.items() if key not in NOT_FREE_TEXT}
    return json.dumps(kept, ensure_ascii=False)  # an escape such as \u2026 would add digits


def free_message(message: str) -> str:
    """A log record's ``message``, with the audit entry or login attempt that it reports in place
    of storing it (ENTRY_LOST, ATTEMPT_LOST) as free_text."""
    for prefix in (ENTRY_LOST, ATTEMPT_LOST):
        if message.startswith(prefix):
            record, end = json.JSONDecoder().raw_decode(message, len(prefix))
            return prefix + free_text(record) + message[end:]
    return message


@pytest.fixture
def clock(monkeypatch) -> RecordingClock:
    clock = RecordingClock()
    monkeypatch.setattr(auth_utils, "_totp_time", lambda: clock.now)
    return clock


@pytest.fixture
def world(tmp_path, monkeypatch, caplog, clock):
    w = World(tmp_path, monkeypatch, caplog)
    w.client = RecordingClient(TestClient(app, raise_server_exceptions=False, client=(CLIENT, 50000)))
    w.setup_answers = []  # the answers of POST /auth/totp/setup, the one place a secret may be
    try:
        yield w
    finally:
        w.close()
    # ADR-0125: no password, two-factor secret or code in argv, in a log record, in an answer or
    # in the audit log; only requests carry them, and the setup's answer its new secret.
    # In a fixture's teardown caplog.records holds only the teardown's records.
    records = [r for when in ("setup", "call", "teardown") for r in caplog.get_records(when)]
    assert records, "the log was not captured"
    logged = [(f"the log ({r.name})", r.getMessage()) for r in records]
    answers = [("an answer", body) for body in w.client.bodies if body not in w.setup_answers]
    texts = logged + answers + ([("the audit log", w.audit_file.read_text())] if w.audit_file.exists() else [])
    for password in SECRETS:
        w.fake.assert_no_secret(password)
        assert [where for where, text in texts if password in text] == [], "a password"
    # The database driver's debug records show the parameters of its statements, and so the
    # secret that users.totp_secret stores. The supervisor never emits them: the driver's logger
    # keeps the root logger's WARNING. Passwords are never parameters, only their hashes.
    texts = [(where, text) for where, text in texts if where != "the log (aiosqlite)"]
    for secret in {*clock.secrets, *(json.loads(answer)["secret"] for answer in w.setup_answers)}:
        w.fake.assert_no_secret(secret)
        assert [where for where, text in texts if secret in text] == [], "a two-factor secret"
    # Codes are looked for in the same places, with the audit entries and login attempts, in the
    # audit log and where the log reports them, as free_text.
    texts = [(where, free_message(text)) for where, text in logged if where != "the log (aiosqlite)"]
    texts += answers + [("the audit log", free_text(entry)) for entry in w.audit()]
    for code in clock.codes:
        alone = re.compile(rf"(?<![0-9]){code}(?![0-9])")
        assert [where for where, text in texts if alone.search(text)] == [], "a two-factor code"


def headers(world: World, name: str = "alice") -> dict[str, str]:
    """A new session of ``name``, from the browser BROWSER."""
    return {**world.headers(name), "User-Agent": BROWSER}


def picked(entry: dict) -> dict:
    return {field: entry.get(field) for field in FIELDS}


def entries(world: World) -> list[dict]:
    return [picked(entry) for entry in world.audit()]


def lost_entries(world: World, action: str) -> list[dict]:
    """The entries of ``action`` that the supervisor's log reports in place of the audit log."""
    return [picked(entry) for entry, _ in reported(world, ENTRY_LOST) if entry["action"] == action]


def own(action: str, details: str | None, severity: str = "info", name: str = "alice") -> dict:
    """An entry of ``name``'s own change of their account."""
    return {"action": action, "username": name, "ip_address": CLIENT, "user_agent": BROWSER,
            "resource_type": "user", "resource_id": name, "details": details, "severity": severity,
            "success": True}


def wrong(code: str) -> str:
    return "000000" if code != "000000" else "111111"


def new_secret(world: World, auth: dict[str, str]) -> str:
    """A new two-factor secret (POST /auth/totp/setup), whose answer is the one that may hold it."""
    answer = world.client.post(f"{API}/auth/totp/setup", headers=auth)
    assert answer.status_code == 200, answer.text
    world.setup_answers.append(answer.text)
    return answer.json()["secret"]


def a_session_kept_in_memory(world: World, monkeypatch) -> dict[str, str]:
    """/data is full when alice signs in, so her session lives in the supervisor's memory
    (ADR-0251, ADR-0329): the headers of its requests, from the browser BROWSER."""
    fill_data(world, monkeypatch)
    token = sign_in(world).json()["access_token"]
    assert decode_access_token(token)[1] in auth_utils._unstored_sessions
    return {"Authorization": f"Bearer {token}", "User-Agent": BROWSER}


# Each change of one's own account -> the actions of the entries it writes.
CHANGES = {
    "logout": ["logout"],
    "change-password": ["password_change"],
    "change-password-also-for-smb": ["password_change", "smb_account_updated"],
    "totp-enable": ["totp_enabled"],
    "totp-disable": ["totp_disabled"],
}


def make_change(world: World, clock: Clock, change: str, then: Callable[[], None]):
    """Request ``change`` of CHANGES as alice, once what it needs is in place and ``then()`` ran."""
    auth = headers(world)
    if change == "change-password-also-for-smb":
        world.make_active("alice", 70000, ALICE_PW)
    if change == "totp-enable":
        secret = new_secret(world, auth)
    if change == "totp-disable":
        world.set_totp("alice", SECRET)
    then()
    if change == "logout":
        return world.client.post(f"{API}/auth/logout", headers=auth)
    if change.startswith("change-password"):
        return world.client.post(f"{API}/auth/change-password",
                                 json={"current_password": ALICE_PW, "new_password": NEW_PW}, headers=auth)
    if change == "totp-enable":
        return world.client.post(f"{API}/auth/totp/enable", json={"code": clock.code(secret)}, headers=auth)
    return world.client.post(f"{API}/auth/totp/disable", json={"code": clock.code()}, headers=auth)


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

    @pytest.mark.parametrize("kept", ["stored", "in-memory"])
    def test_a_session_that_ended_meanwhile_writes_nothing(self, world, monkeypatch, kept):
        """Two sign-outs of one session at the same moment: the one that comes second ends
        nothing, and only the first writes its entry. Also for a session that a full /data kept
        in memory."""
        auth = headers(world) if kept == "stored" else a_session_kept_in_memory(world, monkeypatch)
        end_session = auth_router.end_session

        async def ended_by_the_other_sign_out(db, session_id):
            async with world.maker() as other:
                assert await end_session(other, session_id)
                await other.commit()
            return await end_session(db, session_id)

        monkeypatch.setattr(auth_router, "end_session", ended_by_the_other_sign_out)

        response = world.client.post(f"{API}/auth/logout", headers=auth)

        assert response.status_code == 200, response.text
        assert world.audit() == []
        assert lost_entries(world, "logout") == []

    def test_a_session_kept_in_memory_ends_and_its_entry_is_in_the_log(self, world, monkeypatch):
        """A sign-in while /data was full kept its session in memory (ADR-0329). Its sign-out
        ends it and answers 200, and the logout entry, which the full /data cannot take either,
        is in the supervisor's log once. A second sign-out of it is refused and reports nothing."""
        auth = a_session_kept_in_memory(world, monkeypatch)

        first = world.client.post(f"{API}/auth/logout", headers=auth)
        second = world.client.post(f"{API}/auth/logout", headers=auth)

        assert first.status_code == 200, first.text
        assert second.status_code == 401
        assert lost_entries(world, "logout") == [own("logout", None)]


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
        secret = new_secret(world, auth)
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

    @pytest.mark.parametrize("turn", ["on", "off"])
    def test_of_two_requests_only_the_one_that_changed_it_writes(self, world, clock, monkeypatch, turn):
        """Two requests that both found 2FA off (or on) and both took a code, each of its own
        time step: the other one writes first. This one then changes nothing, answers as when
        2FA was already on (or off), and writes no entry."""
        auth = headers(world)
        if turn == "on":
            path, secret = f"{API}/auth/totp/enable", new_secret(world, auth)
        else:
            path, secret = f"{API}/auth/totp/disable", SECRET
            world.set_totp("alice", SECRET)
        real = auth_router.check_totp_code
        other = []

        def the_other_request_writes_first(user_id, totp_secret, code):
            check = real(user_id, totp_secret, code)
            if not other:  # once: the other request checks its code here too
                other.append(None)
                clock.next_step()
                # A request of its own, answered before this one goes on.
                other[0] = world.client.post(path, json={"code": clock.code(secret)}, headers=headers(world))
            return check

        monkeypatch.setattr(auth_router, "check_totp_code", the_other_request_writes_first)
        response = world.client.post(path, json={"code": clock.code(secret)}, headers=auth)

        assert other[0].status_code == 200, other[0].text
        detail = "2FA is already enabled" if turn == "on" else "2FA is not enabled"
        assert (response.status_code, response.json().get("detail")) == (400, detail)
        user = world.user("alice")
        assert (user.totp_enabled, user.totp_secret) == ((True, secret) if turn == "on" else (False, None))
        assert [entry["action"] for entry in world.audit()] == ["totp_enabled" if turn == "on" else "totp_disabled"]

    def test_a_setup_that_replaced_the_secret_meanwhile_keeps_2fa_off(self, world, clock, monkeypatch):
        """Two tabs: this one's code matched its secret, and then the other tab's setup stored a
        new secret before this one turned 2FA on. 2FA stays off with the new secret, which only
        the other tab's QR code shows, and this request answers as when there is no setup to
        confirm: turned on, it would ask for codes of a secret the user may never have scanned,
        and the recovery codes would be gone (ADR-0343)."""
        auth = headers(world)
        first = new_secret(world, auth)
        real = auth_router.check_totp_code
        newer = []

        def a_setup_meanwhile(user_id, totp_secret, code):
            check = real(user_id, totp_secret, code)
            if not newer:  # a request of its own, answered before this one goes on
                newer.append(new_secret(world, headers(world)))
            return check

        monkeypatch.setattr(auth_router, "check_totp_code", a_setup_meanwhile)
        response = world.client.post(f"{API}/auth/totp/enable", json={"code": clock.code(first)}, headers=auth)

        assert (response.status_code, response.json().get("detail")) == (400, "Run /totp/setup first")
        user = world.user("alice")
        assert (user.totp_enabled, user.totp_secret) == (False, newer[0])
        assert world.audit() == []
        # A code of the new secret turns it on.
        clock.next_step()
        enabled = world.client.post(f"{API}/auth/totp/enable", json={"code": clock.code(newer[0])}, headers=auth)
        assert enabled.status_code == 200, enabled.text
        assert (world.user("alice").totp_enabled, world.user("alice").totp_secret) == (True, newer[0])

    def test_a_setup_never_replaces_the_secret_of_2fa_turned_on_meanwhile(self, world, clock, monkeypatch):
        """This setup found 2FA off, and then the other tab's code turned it on before this setup
        stored its new secret. 2FA keeps the secret whose code was typed, and this setup stores
        and shows nothing and answers as when it finds 2FA on (ADR-0343)."""
        auth = headers(world)
        first = new_secret(world, auth)
        real = auth_router.pyotp.random_base32
        enabled = []

        def turned_on_meanwhile():
            secret = real()
            clock.secrets.add(secret)  # this setup's secret, which no answer and no log may hold
            if not enabled:  # a request of its own, answered before this one goes on
                enabled.append(world.client.post(f"{API}/auth/totp/enable", json={"code": clock.code(first)},
                                                 headers=headers(world)))
            return secret

        monkeypatch.setattr(auth_router.pyotp, "random_base32", turned_on_meanwhile)
        response = world.client.post(f"{API}/auth/totp/setup", headers=auth)

        assert enabled[0].status_code == 200, enabled[0].text
        assert (response.status_code, response.json().get("detail")) == (400, "2FA is already enabled")
        user = world.user("alice")
        assert (user.totp_enabled, user.totp_secret) == (True, first)
        assert [entry["action"] for entry in world.audit()] == ["totp_enabled"]

    @pytest.mark.parametrize("entry", ["written", "lost"])
    def test_a_timestamp_with_the_digits_of_a_code_is_not_a_code(self, world, clock, monkeypatch, entry):
        """The microseconds of an entry's timestamp are six digits, as a code is: here they are
        those of the code that turned 2FA on. The teardown's scan for codes leaves out an entry's
        timestamp, id and hashes, in the audit log and where the supervisor's log reports an entry
        that could not be written (NOT_FREE_TEXT), and finds no code."""
        auth = headers(world)
        secret = new_secret(world, auth)
        while clock.code(secret) == "000000":  # isoformat() leaves out a microsecond of 0
            clock.next_step()
        code = clock.code(secret)

        monkeypatch.setattr(audit_router, "utc_now", lambda: datetime(2026, 11, 7, 12, 0, 5, int(code), tzinfo=timezone.utc))
        if entry == "lost":
            the_audit_log_is_full(monkeypatch)

        response = world.client.post(f"{API}/auth/totp/enable", json={"code": code}, headers=auth)

        assert response.status_code == 200, response.text
        [stored] = world.audit() if entry == "written" else [lost for lost, _ in reported(world, ENTRY_LOST)]
        assert (stored["action"], stored["timestamp"]) == ("totp_enabled", f"2026-11-07T12:00:05.{code}Z")


class TestAnEntryThatCannotBeWritten:
    @pytest.mark.parametrize("change", CHANGES)
    def test_the_change_answers_and_the_log_has_the_entry(self, world, clock, monkeypatch, change):
        response = make_change(world, clock, change, lambda: the_audit_log_is_full(monkeypatch))

        assert response.status_code == 200, response.text
        lost = [entry for entry, _ in reported(world, ENTRY_LOST)]
        assert [(entry["action"], entry["username"], entry["ip_address"]) for entry in lost] \
            == [(action, "alice", CLIENT) for action in CHANGES[change]]


class TestAChangeThatCannotBeStored:
    @pytest.mark.parametrize("change", CHANGES)
    def test_it_writes_no_entry(self, world, clock, monkeypatch, change):
        """Every commit fails, as on a full /data: the change is not stored and the request does
        not answer success. The entry follows the commit, so none is written, and none is
        reported in the supervisor's log either."""
        response = make_change(world, clock, change, lambda: every_commit_fails(monkeypatch))

        # Samba's password was put back: "Nothing was changed" (ADR-0124).
        assert response.status_code == (503 if change == "change-password-also-for-smb" else 500), response.text
        assert world.audit() == []
        assert reported(world, ENTRY_LOST) == []
