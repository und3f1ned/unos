"""Two-factor recovery codes (ADR-0232; feature auth.recovery-codes).

A user with two-factor authentication generates ten codes, which the answer shows once; at the
code step of the sign-in each code takes the place of a code of the authenticator app once, also
when two sign-ins send it at the same moment. Turning two-factor authentication off removes the
codes, and a new setup starts without any.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re

import httpx
import pyotp
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.auth import router as auth_router
from src.auth import utils as auth_utils
from src.auth.models import User
from src.main import app
from src.security.models import LoginAttempt

from .test_smb_accounts import ALICE_PW, API, BOB_PW, World
from .test_totp_once import Clock

INVALID = "Invalid recovery code"
NEEDS_2FA = "2FA must be enabled before generating recovery codes"
SECRET = pyotp.random_base32()
SHOWN = re.compile(r"[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}")


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


def login(world: World, name: str, password: str, **second):
    """POST /auth/login; ``second`` is totp_code or recovery_code."""
    return world.client.post(f"{API}/auth/login", json={"username": name, "password": password, **second})


def generate(world: World, name: str = "alice") -> list[str]:
    response = world.client.post(f"{API}/auth/recovery-codes/generate", headers=world.headers(name))
    assert response.status_code == 200, response.text
    return response.json()["codes"]


def status(world: World, name: str = "alice") -> dict:
    response = world.client.get(f"{API}/auth/recovery-codes/status", headers=world.headers(name))
    assert response.status_code == 200, response.text
    return response.json()


def stored(world: World, name: str = "alice") -> str | None:
    """users.recovery_codes of user ``name``, as the database holds it."""
    with Session(world.engine) as session:
        return session.get(User, world.ids[name]).recovery_codes


def store(world: World, name: str, value: str | None) -> None:
    with Session(world.engine) as session:
        session.get(User, world.ids[name]).recovery_codes = value
        session.commit()


def attempts(world: World) -> list[tuple[str, bool, str | None]]:
    with Session(world.engine) as session:
        rows = session.scalars(select(LoginAttempt).order_by(LoginAttempt.id))
        return [(row.username, row.success, row.failure_reason) for row in rows]


def a_code_not_in(codes: list[str]) -> str:
    for candidate in ("0000-0000-0000", "1111-1111-1111"):
        if candidate not in codes:
            return candidate
    raise AssertionError("unreachable: two fixed codes cannot both be among the generated ones")


class TestGenerate:
    def test_ten_codes_are_answered_once_and_only_their_hashes_are_kept(self, world):
        world.set_totp("alice", SECRET)

        codes = generate(world)

        assert len(codes) == 10 and len(set(codes)) == 10
        assert all(SHOWN.fullmatch(code) for code in codes), codes
        text = stored(world)
        hashes = json.loads(text)
        assert len(hashes) == 10 and all(re.fullmatch(r"[0-9a-f]{64}", h) for h in hashes)
        assert not any(code in text or code.replace("-", "") in text for code in codes)
        assert status(world) == {"remaining": 10, "generated": True}

    def test_only_with_two_factor_authentication(self, world):
        response = world.client.post(f"{API}/auth/recovery-codes/generate", headers=world.headers("bob"))

        assert (response.status_code, response.json()["detail"]) == (400, NEEDS_2FA)
        assert stored(world, "bob") is None
        assert status(world, "bob") == {"remaining": 0, "generated": False}

    def test_new_codes_replace_the_old_ones(self, world):
        world.set_totp("alice", SECRET)
        old = generate(world)

        new = generate(world)

        assert set(old).isdisjoint(new)
        refused = login(world, "alice", ALICE_PW, recovery_code=old[0])
        assert (refused.status_code, refused.json()["detail"]) == (401, INVALID)
        assert login(world, "alice", ALICE_PW, recovery_code=new[0]).status_code == 200
        assert status(world) == {"remaining": 9, "generated": True}

    def test_no_codes_are_written_once_two_factor_authentication_is_off(self, world, monkeypatch):
        """2FA turned off between the request's check and its write: no codes outlive it."""
        world.set_totp("alice", SECRET)
        real = auth_router.generate_recovery_codes

        def turned_off_meanwhile():
            with Session(world.engine) as session:
                user = session.get(User, world.ids["alice"])
                user.totp_enabled, user.totp_secret = False, None
                session.commit()
            return real()

        monkeypatch.setattr(auth_router, "generate_recovery_codes", turned_off_meanwhile)
        response = world.client.post(f"{API}/auth/recovery-codes/generate", headers=world.headers("alice"))

        assert (response.status_code, response.json()["detail"]) == (400, NEEDS_2FA)
        assert stored(world) is None

    def test_the_ten_codes_differ_even_when_the_random_digits_repeat(self, monkeypatch):
        """Two equal codes would make one code sign in twice."""
        digits = iter(["A1B2C3D4E5F6"] * 3 + [f"{n:012X}" for n in range(1, 20)])
        monkeypatch.setattr(auth_utils.secrets, "token_hex", lambda nbytes: next(digits).lower())

        shown, hashes = auth_utils.generate_recovery_codes()

        assert len(shown) == len(set(shown)) == 10
        assert len(set(hashes)) == 10
        assert shown[0] == "A1B2-C3D4-E5F6"
        assert hashes == [auth_utils.recovery_code_hash(code) for code in shown]


class TestSignIn:
    def test_without_a_code_the_sign_in_asks_for_one(self, world):
        world.set_totp("alice", SECRET)
        generate(world)

        response = login(world, "alice", ALICE_PW)

        assert response.status_code == 200
        assert response.json() == {"access_token": "", "token_type": "bearer", "requires_totp": True}
        assert status(world)["remaining"] == 10

    def test_a_code_signs_in_once(self, world):
        world.set_totp("alice", SECRET)
        codes = generate(world)

        first = login(world, "alice", ALICE_PW, recovery_code=codes[3])
        again = login(world, "alice", ALICE_PW, recovery_code=codes[3])

        assert first.status_code == 200, first.text
        assert first.json()["requires_totp"] is False
        token = first.json()["access_token"]
        me = world.client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert (me.status_code, me.json()["username"]) == (200, "alice")
        assert (again.status_code, again.json()) == (401, {"detail": INVALID})
        hashes = json.loads(stored(world))
        assert hashes[3] is None and sum(h is not None for h in hashes) == 9
        assert status(world) == {"remaining": 9, "generated": True}

    def test_typed_in_any_case_with_or_without_dashes_and_spaces(self, world):
        world.set_totp("alice", SECRET)
        codes = generate(world)
        typed = [codes[0].lower(), codes[1].replace("-", ""), codes[2].replace("-", " "), f"  {codes[3]}\t"]

        answers = [login(world, "alice", ALICE_PW, recovery_code=code).status_code for code in typed]

        assert answers == [200, 200, 200, 200]
        assert status(world)["remaining"] == 6

    def test_a_wrong_code_uses_nothing_up(self, world):
        world.set_totp("alice", SECRET)
        codes = generate(world)
        wrong = [a_code_not_in(codes), "ABCD", "GHIJ-KLMN-OPQR", codes[0] + "0"]

        answers = [login(world, "alice", ALICE_PW, recovery_code=code) for code in wrong]

        assert [(a.status_code, a.json()["detail"]) for a in answers] == [(401, INVALID)] * 4
        assert status(world)["remaining"] == 10
        auth_router.limiter.reset()
        assert login(world, "alice", ALICE_PW, recovery_code=codes[0]).status_code == 200

    def test_the_password_comes_first(self, world):
        world.set_totp("alice", SECRET)
        codes = generate(world)

        response = login(world, "alice", "Wrong-pass-2026", recovery_code=codes[0])

        assert (response.status_code, response.json()["detail"]) == (401, "Incorrect username or password")
        assert status(world)["remaining"] == 10

    def test_codes_of_another_user_do_not_sign_in(self, world):
        world.set_totp("alice", SECRET)
        world.set_totp("bob", SECRET)
        codes = generate(world, "alice")

        response = login(world, "bob", BOB_PW, recovery_code=codes[0])

        assert (response.status_code, response.json()["detail"]) == (401, INVALID)
        assert status(world, "alice")["remaining"] == 10

    def test_a_code_and_a_two_factor_code_together_are_refused(self, world, clock):
        world.set_totp("alice", SECRET)
        codes = generate(world)

        response = login(world, "alice", ALICE_PW, totp_code=clock.code(SECRET), recovery_code=codes[0])

        assert response.status_code == 422
        assert codes[0] not in response.text
        assert status(world)["remaining"] == 10
        assert attempts(world) == []

    def test_the_sign_ins_are_recorded_and_audited_without_the_codes(self, world):
        world.set_totp("alice", SECRET)
        codes = generate(world)

        login(world, "alice", ALICE_PW, recovery_code=a_code_not_in(codes))
        login(world, "alice", ALICE_PW, recovery_code=codes[0])

        assert attempts(world) == [("alice", False, INVALID), ("alice", True, None)]
        signed_in = [e for e in world.audit() if e["action"] == "login_success"]
        assert [(e["username"], e["details"], e["severity"]) for e in signed_in] == [
            ("alice", "Signed in with a recovery code", "warning")
        ]
        logged = "\n".join(record.getMessage() for record in world.caplog.records)
        for code in codes:
            for form in (code, code.replace("-", "")):
                assert form not in world.audit_file.read_text(), "a recovery code in the audit log"
                assert form not in logged, "a recovery code in the log"

    async def test_two_sign_ins_with_one_code_at_the_same_moment(self, world, monkeypatch):
        """Both requests read the codes before either uses one up: exactly one signs in."""
        world.set_totp("alice", SECRET)
        read_both = asyncio.Barrier(2)
        real = auth_router._stored_recovery_codes

        async def read_then_wait_for_the_other(db, user_id):
            value = await real(db, user_id)
            await asyncio.wait_for(read_both.wait(), 5)
            return value

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://unos") as client:
            generated = await client.post(f"{API}/auth/recovery-codes/generate", headers=world.headers("alice"))
            assert generated.status_code == 200, generated.text
            body = {"username": "alice", "password": ALICE_PW, "recovery_code": generated.json()["codes"][0]}
            monkeypatch.setattr(auth_router, "_stored_recovery_codes", read_then_wait_for_the_other)
            answers = await asyncio.gather(*(client.post(f"{API}/auth/login", json=body) for _ in range(2)))

        assert sorted(a.status_code for a in answers) == [200, 401], [a.text for a in answers]
        assert [a.json()["detail"] for a in answers if a.status_code == 401] == [INVALID]
        assert sum(h is not None for h in json.loads(stored(world))) == 9

    def test_a_sign_in_that_read_the_codes_before_new_ones_were_generated(self, world, monkeypatch):
        """New codes generated meanwhile stay as they are: the old code does not sign in, and the
        old codes do not come back."""
        world.set_totp("alice", SECRET)
        old = generate(world)
        new_codes, new_hashes = auth_utils.generate_recovery_codes()
        real = auth_router._stored_recovery_codes

        async def new_codes_generated_meanwhile(db, user_id):
            value = await real(db, user_id)
            store(world, "alice", json.dumps(new_hashes))
            return value

        monkeypatch.setattr(auth_router, "_stored_recovery_codes", new_codes_generated_meanwhile)
        response = login(world, "alice", ALICE_PW, recovery_code=old[0])

        assert (response.status_code, response.json()["detail"]) == (401, INVALID)
        assert json.loads(stored(world)) == new_hashes
        monkeypatch.setattr(auth_router, "_stored_recovery_codes", real)
        assert login(world, "alice", ALICE_PW, recovery_code=new_codes[0]).status_code == 200

    @pytest.mark.parametrize("value", ["not json", "{}", "[1, 2]", "null"])
    def test_a_damaged_list_signs_nobody_in(self, world, value):
        """users.recovery_codes from a restored database is not trusted."""
        world.set_totp("alice", SECRET)
        store(world, "alice", value)

        response = login(world, "alice", ALICE_PW, recovery_code="ABCD-EF01-2345")

        assert (response.status_code, response.json()["detail"]) == (401, INVALID)
        assert stored(world) == value
        assert status(world)["remaining"] == 0

    def test_without_two_factor_authentication_the_password_signs_in(self, world):
        response = login(world, "bob", BOB_PW, recovery_code="ABCD-EF01-2345")

        assert response.status_code == 200 and response.json()["access_token"]


class TestTwoFactorOff:
    def test_turning_two_factor_off_removes_the_codes(self, world, clock):
        world.set_totp("alice", SECRET)
        codes = generate(world)

        disabled = world.client.post(
            f"{API}/auth/totp/disable", json={"code": clock.code(SECRET)}, headers=world.headers("alice")
        )

        assert disabled.status_code == 200, disabled.text
        assert stored(world) is None
        assert status(world) == {"remaining": 0, "generated": False}
        # A new setup starts without codes, and the old ones do not sign in.
        clock.next_step()
        headers = world.headers("alice")
        secret = world.client.post(f"{API}/auth/totp/setup", headers=headers).json()["secret"]
        enabled = world.client.post(f"{API}/auth/totp/enable", json={"code": clock.code(secret)}, headers=headers)
        assert enabled.status_code == 200, enabled.text
        assert status(world) == {"remaining": 0, "generated": False}
        refused = login(world, "alice", ALICE_PW, recovery_code=codes[0])
        assert (refused.status_code, refused.json()["detail"]) == (401, INVALID)

    def test_a_new_setup_starts_without_codes(self, world, clock):
        """Codes left from before (a generation that crossed turning 2FA off, a restored
        database) never come back with a new setup."""
        _, hashes = auth_utils.generate_recovery_codes()
        store(world, "alice", json.dumps(hashes))
        headers = world.headers("alice")
        secret = world.client.post(f"{API}/auth/totp/setup", headers=headers).json()["secret"]

        enabled = world.client.post(f"{API}/auth/totp/enable", json={"code": clock.code(secret)}, headers=headers)

        assert enabled.status_code == 200, enabled.text
        assert stored(world) is None
        assert status(world) == {"remaining": 0, "generated": False}


class TestTheStub:
    def test_the_verify_stub_is_gone(self, world):
        response = world.client.post(f"{API}/auth/recovery-codes/verify", json={"code": "ABCD-EF01-2345"})

        assert response.status_code == 404


class TestHash:
    @pytest.mark.parametrize("typed", ["ABCD-EF01-2345", "abcd-ef01-2345", "ABCDEF012345", " abCD EF01 2345 "])
    def test_the_forms_of_one_code(self, typed):
        assert auth_utils.recovery_code_hash(typed) == auth_utils.recovery_code_hash("ABCDEF012345")
        assert auth_utils.recovery_code_hash(typed) == hashlib.sha256(b"ABCDEF012345").hexdigest()

    @pytest.mark.parametrize("typed", ["", "ABCD", "ABCD-EF01-23456", "GHIJ-KLMN-OPQR", "ABCD-EF01-234", 123, None])
    def test_no_code(self, typed):
        assert auth_utils.recovery_code_hash(typed) is None

