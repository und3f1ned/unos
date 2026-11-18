"""Security keys (WebAuthn) as a second factor (ADR-0406; feature auth.security-keys).

A user with two-factor authentication registers a security key on the Account page, with the
password and a touch of the key; at the code step of the sign-in, an answer of the key opens the
session in place of the authenticator app's code. The verification is UNOS's own: a bounded CBOR
decoder and cryptography, checked here against a software authenticator built on cryptography
(tests/webauthn_authenticator.py), whose answers each test bends to show one check.
"""
from __future__ import annotations

import io
import dataclasses
import importlib.util
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from contextlib import closing

import pyotp
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from src import emergency
from src.auth import cbor, security_keys, webauthn
from src.auth import router as auth_router
from src.auth.models import APIToken, SecurityKey, User
from src.auth.utils import generate_api_token, session_id_of
from src.config import get_settings
from src.database import Base
from src.security.models import LoginAttempt

from .test_backup_restore import MANIFEST, _file, _make_db, _restore, _store, make_env
from .test_smb_accounts import ADMIN_PW, ALICE_PW, API, BOB_PW, World, _hash
from .test_totp_once import Clock
from .webauthn_authenticator import AT, ED, EDDSA, ES256, RS256, UP, UV, SoftKey, b64url, cbor as encode, unb64url

RP = "nas.local"
ORIGIN = "https://nas.local"
HOST = {"Host": RP, "Origin": ORIGIN}
PASSWORDS = {"admin": ADMIN_PW, "alice": ALICE_PW, "bob": BOB_PW}
SECRET = pyotp.random_base32()
BOB_SECRET = pyotp.random_base32()

INVALID = "Invalid security key"
EXPIRED = "The security key request expired or was already used: try again"
CLONED = "This security key's signature counter went backwards, so it may have been copied: sign in another way and remove it"
NOT_ACCEPTED = "The security key's answer was not accepted"
NEEDS_TOTP = (
    "Turn on two-factor authentication with an authenticator app first: a security key is an alternative "
    "to its codes"
)
NEEDS_NAME = "Security keys work only when the web UI is opened by the NAS's name, not by an IP address"
OWN_PAGE = "A security key is registered only from the web UI's own page"
TOO_MANY = "You already have 10 security keys, the most an account can have: remove one first"
NAME_TAKEN = "You already have a security key with this name"
ALREADY = "This security key is already registered"
NOT_FOUND = "Security key not found"
WRONG_PASSWORD = "Current password is incorrect"


class Ticks:
    """The challenges' clock (webauthn._clock)."""

    def __init__(self) -> None:
        self.now = 1000.0


@pytest.fixture
def world(tmp_path, monkeypatch, caplog):
    w = World(tmp_path, monkeypatch, caplog)
    monkeypatch.setattr(webauthn, "_challenges", webauthn.Challenges())
    # The rate limits are under test only where a test turns them on (limited).
    monkeypatch.setattr(auth_router.limiter, "enabled", False)
    try:
        yield w
    finally:
        w.close()


@pytest.fixture
def ticks(monkeypatch) -> Ticks:
    ticks = Ticks()
    monkeypatch.setattr(webauthn, "_clock", lambda: ticks.now)
    return ticks


@pytest.fixture
def clock(monkeypatch) -> Clock:
    from src.auth import utils as auth_utils

    clock = Clock()
    monkeypatch.setattr(auth_utils, "_totp_time", lambda: clock.now)
    return clock


@pytest.fixture
def limited(world, monkeypatch):
    """The auth routes' rate limits, from no request on."""
    auth_router.limiter.reset()
    monkeypatch.setattr(auth_router.limiter, "enabled", True)
    return world


@pytest.fixture
def alice(world):
    """alice with two-factor authentication."""
    world.set_totp("alice", SECRET)
    return world


# -- helpers ------------------------------------------------------------------------------------


def own(world: World, name: str) -> dict[str, str]:
    """One session of ``name`` for the test: a registration's challenge is bound to its session, and
    world.headers makes a new one at each call."""
    sessions = world.__dict__.setdefault("key_sessions", {})
    if name not in sessions:
        sessions[name] = world.headers(name)
    return sessions[name]


def begin(world: World, name: str = "alice", password: str | None = None, host: dict | None = None,
          headers: dict | None = None):
    return world.client.post(
        f"{API}/auth/security-keys/register/begin",
        json={"current_password": PASSWORDS[name] if password is None else password},
        headers={**own(world, name), **(HOST if host is None else host)} if headers is None else headers,
    )


def finish(world: World, body: dict, name: str = "alice", key_name: str = "YubiKey", host: dict | None = None,
           headers: dict | None = None):
    return world.client.post(
        f"{API}/auth/security-keys/register/finish",
        json={"name": key_name, **body},
        headers={**own(world, name), **(HOST if host is None else host)} if headers is None else headers,
    )


def challenge_of(response) -> bytes:
    assert response.status_code == 200, response.text
    return unb64url(response.json()["challenge"])


def register(world: World, key: SoftKey, name: str = "alice", key_name: str = "YubiKey") -> dict:
    response = finish(world, key.register(challenge_of(begin(world, name)), ORIGIN, RP), name, key_name)
    assert response.status_code == 200, response.text
    return response.json()


def listed(world: World, name: str = "alice") -> list[dict]:
    response = world.client.get(f"{API}/auth/security-keys", headers=world.headers(name))
    assert response.status_code == 200, response.text
    return response.json()


def rows(world: World, name: str | None = None) -> list[SecurityKey]:
    with Session(world.engine) as session:
        query = select(SecurityKey).order_by(SecurityKey.id)
        if name is not None:
            query = query.where(SecurityKey.user_id == world.ids[name])
        found = list(session.scalars(query))
        for row in found:
            session.expunge(row)
        return found


def sign_in(world: World, name: str = "alice", host: dict | None = None, **extra):
    return world.client.post(
        f"{API}/auth/login", json={"username": name, "password": PASSWORDS[name], **extra},
        headers=HOST if host is None else host,
    )


def code_step(world: World, name: str = "alice", host: dict | None = None) -> dict:
    """The answer of the password step of an account with two-factor authentication."""
    response = sign_in(world, name, host)
    assert response.status_code == 200, response.text
    answer = response.json()
    assert answer["requires_totp"] is True and answer["access_token"] == ""
    return answer


def key_sign_in(world: World, key: SoftKey, name: str = "alice", **bend):
    """The password step, then the key's answer to its challenge."""
    challenge = unb64url(code_step(world, name)["security_key"]["challenge"])
    return sign_in(world, name, security_key=key.assertion(challenge, ORIGIN, RP, **bend))


def attempts(world: World) -> list[tuple[str, bool, str | None]]:
    with Session(world.engine) as session:
        found = session.scalars(select(LoginAttempt).order_by(LoginAttempt.id))
        return [(row.username, row.success, row.failure_reason) for row in found]


def entries(world: World, action: str) -> list[tuple[str, str, str]]:
    return [(e["username"], e["details"], e["severity"]) for e in world.audit() if e["action"] == action]


def bearer(response) -> dict[str, str]:
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


# -- the CBOR decoder ---------------------------------------------------------------------------


class TestCbor:
    def test_it_reads_what_webauthn_sends(self):
        value = {1: 2, 3: -7, -1: 1, "fmt": "none", "list": [b"\x00\x01", True, False, None], "big": 2**40}

        assert cbor.decode(encode(value)) == value

    def test_decode_prefix_answers_where_the_item_ends(self):
        data = encode({1: 2}) + b"\xff\xff"

        assert cbor.decode_prefix(data) == ({1: 2}, 3)

    @pytest.mark.parametrize("data", [
        pytest.param(b"", id="empty"),
        pytest.param(b"\xc0\x00", id="tag"),
        pytest.param(b"\xf9\x00\x00", id="half-float"),
        pytest.param(b"\xfb" + bytes(8), id="double"),
        pytest.param(b"\xf7", id="undefined"),
        pytest.param(b"\xf0", id="other simple value"),
        pytest.param(b"\x5f\x41\x00\xff", id="indefinite bytes"),
        pytest.param(b"\x9f\x01\xff", id="indefinite array"),
        pytest.param(b"\xbf\x01\x01\xff", id="indefinite map"),
        pytest.param(b"\x1c", id="reserved length"),
        pytest.param(b"\x5a\xff\xff\xff\xff\x00", id="bytes longer than the input"),
        pytest.param(b"\x7b" + b"\xff" * 8, id="text of 2**64 bytes"),
        pytest.param(b"\x98\x41" + b"\x01" * 65, id="65 items"),
        pytest.param(b"\x81\x81\x81\x81\x81\x01", id="nested five deep"),
        pytest.param(b"\xa2\x01\x01\x01\x02", id="key given twice"),
        pytest.param(b"\xa1\x41\x00\x01", id="bytes as key"),
        pytest.param(b"\xa1\xf5\x01", id="true as key"),
        pytest.param(b"\x62\xc3\x28", id="text not UTF-8"),
        pytest.param(b"\x82\x01", id="array cut short"),
        pytest.param(b"\x01\x02", id="bytes after the item"),
    ])
    def test_it_refuses_the_rest(self, data):
        with pytest.raises(cbor.CborError):
            cbor.decode(data)

    def test_four_levels_of_nesting_are_read(self):
        assert cbor.decode(b"\x81\x81\x81\x81\x01") == [[[[1]]]]


# -- what the verification takes apart ----------------------------------------------------------


class TestRelyingPartyId:
    @pytest.mark.parametrize("host, rp_id", [
        ("nas.local", "nas.local"),
        ("NAS.Local:8443", "nas.local"),
        ("unos", "unos"),
        ("localhost:51443", "localhost"),
        ("xn--nas-1na.example", "xn--nas-1na.example"),
    ])
    def test_a_host_name_is_the_rp_id(self, host, rp_id):
        assert webauthn.rp_id_of_host(host) == rp_id

    @pytest.mark.parametrize("host", [
        "192.168.1.10", "192.168.1.10:443", "[fe80::1]:443", "[::1]", "1234", "127.1", "0x7f.0.0.1", "nas.123",
        "", None, "bad host", "nas_local", "-nas.local", "nas..local", "a" * 64 + ".local", ".", "nas.local.:443",
    ])
    def test_an_ip_address_or_no_valid_name_is_refused(self, host):
        with pytest.raises(webauthn.WebAuthnError):
            webauthn.rp_id_of_host(host)


class TestPublicKeys:
    def ec2(self, change: dict) -> bytes:
        value = cbor.decode(SoftKey(ES256).cose())
        value.update(change)
        return encode(value)

    @pytest.mark.parametrize("alg", [ES256, EDDSA, RS256])
    def test_each_algorithm_verifies_its_signature_and_no_other(self, alg):
        key = SoftKey(alg)
        signature = key.sign(b"signed data")

        assert webauthn.verify_signature(key.cose(), alg, b"signed data", signature)
        assert not webauthn.verify_signature(key.cose(), alg, b"other data", signature)
        assert not webauthn.verify_signature(key.cose(), alg, b"signed data", b"\x30\x00")
        assert not webauthn.verify_signature(SoftKey(alg).cose(), alg, b"signed data", signature)

    @pytest.mark.parametrize("change", [
        pytest.param({-1: 2}, id="P-384 curve"),
        pytest.param({3: -35}, id="ES384"),
        pytest.param({3: -8}, id="alg of another key type"),
        pytest.param({-2: bytes(31)}, id="x of 31 bytes"),
        pytest.param({-2: bytes(32), -3: bytes(32)}, id="point not on the curve"),
        pytest.param({1: 4}, id="symmetric key type"),
    ])
    def test_a_key_of_another_shape_is_refused(self, change):
        with pytest.raises(webauthn.WebAuthnError):
            webauthn.public_key_algorithm(self.ec2(change))

    def test_an_rsa_key_under_2048_bits_is_refused(self):
        from cryptography.hazmat.primitives.asymmetric import rsa

        numbers = rsa.generate_private_key(public_exponent=65537, key_size=1024).public_key().public_numbers()
        cose = encode({1: 3, 3: RS256, -1: numbers.n.to_bytes(128, "big"), -2: b"\x01\x00\x01"})

        with pytest.raises(webauthn.WebAuthnError):
            webauthn.public_key_algorithm(cose)

    def test_an_okp_key_of_another_curve_is_refused(self):
        with pytest.raises(webauthn.WebAuthnError):
            webauthn.public_key_algorithm(encode({1: 1, 3: EDDSA, -1: 7, -2: bytes(57)}))


class TestAuthenticatorData:
    def test_extensions_are_read_and_nothing_may_follow(self):
        key = SoftKey()
        auth = key.auth_data(RP, UP | ED, 7, extensions=encode({"hmac-secret": True}))

        parsed = webauthn.parse_authenticator_data(auth)
        assert (parsed.sign_count, parsed.user_present, parsed.credential_id) == (7, True, None)
        with pytest.raises(webauthn.WebAuthnError):
            webauthn.parse_authenticator_data(auth + b"\x00")

    @pytest.mark.parametrize("data", [
        pytest.param(bytes(36), id="short"),
        pytest.param(bytes(32) + bytes([UP | AT]) + bytes(4) + bytes(16) + b"\x04\x00", id="credential id past the end"),
        pytest.param(bytes(32) + bytes([UP | ED]) + bytes(4), id="extensions flagged but missing"),
        pytest.param(bytes(32) + bytes([UP]) + bytes(4) + b"\xa0", id="bytes without the flag"),
    ])
    def test_damaged_data_is_refused(self, data):
        with pytest.raises(webauthn.WebAuthnError):
            webauthn.parse_authenticator_data(data)


# -- registration -------------------------------------------------------------------------------


class TestRegistration:
    @pytest.mark.parametrize("alg", [ES256, EDDSA, RS256])
    def test_a_key_is_registered_with_the_password_and_its_answer(self, alice, alg):
        options = begin(alice)

        assert options.status_code == 200, options.text
        body = options.json()
        assert len(unb64url(body["challenge"])) == 32
        assert (body["rp_id"], body["rp_name"], body["user_name"]) == (RP, "UNOS", "alice")
        assert body["algorithms"] == [ES256, EDDSA, RS256]
        assert (body["attestation"], body["user_verification"], body["resident_key"]) == ("none", "discouraged", "discouraged")
        assert body["exclude_credentials"] == [] and body["timeout_ms"] == 60000
        assert len(unb64url(body["user_id"])) == 16

        key = SoftKey(alg)
        response = finish(alice, key.register(unb64url(body["challenge"]), ORIGIN, RP), key_name="  Blue key  ")

        assert response.status_code == 200, response.text
        answer = response.json()
        assert (answer["name"], answer["origin"], answer["usable"], answer["last_used_at"]) == ("Blue key", ORIGIN, True, None)
        assert listed(alice) == [answer]
        [row] = rows(alice)
        assert (row.user_id, row.credential_id, row.algorithm, row.sign_count, row.rp_id, row.origin) == (
            alice.ids["alice"], key.id, alg, 0, RP, ORIGIN)
        assert row.public_key == key.cose()
        assert entries(alice, "security_key_added") == [
            ("alice", "Security key 'Blue key' added, for https://nas.local", "info")]

    def test_a_second_registration_excludes_the_keys_the_user_has(self, alice):
        key = SoftKey()
        register(alice, key)

        assert begin(alice).json()["exclude_credentials"] == [key.id]
        assert begin(alice, "admin").status_code == 409  # the administrator has no two-factor authentication

    def test_a_key_with_its_extensions_and_any_attestation_format_is_registered(self, alice):
        key = SoftKey()
        body = key.register(challenge_of(begin(alice)), ORIGIN, RP, fmt="packed", att_stmt={"alg": -7, "sig": b"x"},
                            flags=UP | AT | ED, extensions=encode({"credProtect": 2}))

        assert finish(alice, body).status_code == 200

    @pytest.mark.parametrize("password, status, detail", [
        (BOB_PW, 400, WRONG_PASSWORD),
        ("x" * 1025, 422, None),
    ])
    def test_begin_takes_the_current_password(self, alice, password, status, detail):
        response = begin(alice, password=password)

        assert response.status_code == status
        if detail:
            assert response.json()["detail"] == detail
        assert webauthn._challenges._pending == {}

    def test_begin_and_the_removal_take_at_most_5_passwords_a_minute(self, alice, limited):
        for _ in range(5):
            assert begin(alice, password=BOB_PW).status_code == 400

        assert begin(alice).status_code == 429

        def remove():
            return alice.client.request("DELETE", f"{API}/auth/security-keys/1", json={"current_password": BOB_PW},
                                        headers=own(alice, "alice")).status_code

        assert [remove() for _ in range(6)] == [400] * 5 + [429]

    def test_begin_needs_two_factor_authentication(self, world):
        response = begin(world, "bob")

        assert (response.status_code, response.json()["detail"]) == (409, NEEDS_TOTP)

    @pytest.mark.parametrize("host", ["192.168.1.10", "192.168.1.10:8443", "[fe80::1]", "[::1]:443"])
    def test_begin_refuses_an_ip_address(self, alice, host):
        response = begin(alice, host={"Host": host, "Origin": f"https://{host}"})

        assert (response.status_code, response.json()["detail"]) == (409, NEEDS_NAME)

    @pytest.mark.parametrize("host", [
        pytest.param({"Host": RP}, id="no origin"),
        pytest.param({"Host": RP, "Origin": "https://evil.example"}, id="another origin"),
        pytest.param({"Host": RP, "Origin": "http://nas.local"}, id="plain http"),
        pytest.param({"Host": RP, "Origin": ORIGIN, "Sec-Fetch-Site": "same-site"}, id="another site's page"),
    ])
    def test_begin_and_finish_only_from_the_web_uis_own_page(self, alice, host):
        response = begin(alice, host=host)
        assert (response.status_code, response.json()["detail"]) == (403, OWN_PAGE)

        key = SoftKey()
        body = key.register(challenge_of(begin(alice)), ORIGIN, RP)
        refused = finish(alice, body, host=host)
        assert (refused.status_code, refused.json()["detail"]) == (403, OWN_PAGE)
        assert rows(alice) == []

    def test_an_api_token_registers_and_removes_no_key(self, alice):
        token, token_hash = generate_api_token()
        with Session(alice.engine) as session:
            session.add(APIToken(name="t", token_hash=token_hash, token_prefix=token[:12], user_id=alice.ids["alice"]))
            session.commit()
        auth = {"Authorization": f"Bearer {token}", **HOST}

        assert begin(alice, headers=auth).status_code == 403
        assert finish(alice, {"credential_id": "AA", "client_data_json": "AA", "attestation_object": "AA"},
                      headers=auth).status_code == 403
        assert alice.client.request("DELETE", f"{API}/auth/security-keys/1", json={"current_password": ALICE_PW},
                                    headers=auth).status_code == 403
        # Listing them is a read.
        assert alice.client.get(f"{API}/auth/security-keys", headers=auth).status_code == 200

    def test_at_most_ten_keys(self, alice):
        for number in range(10):
            register(alice, SoftKey(), key_name=f"Key {number}")

        response = begin(alice)
        assert (response.status_code, response.json()["detail"]) == (409, TOO_MANY)

    def test_the_eleventh_key_is_refused_also_after_begin(self, alice):
        options = [challenge_of(begin(alice)) for _ in range(2)]
        for number in range(9):
            register(alice, SoftKey(), key_name=f"Key {number}")
        assert finish(alice, SoftKey().register(options[0], ORIGIN, RP), key_name="Tenth").status_code == 200

        response = finish(alice, SoftKey().register(options[1], ORIGIN, RP), key_name="Eleventh")

        assert (response.status_code, response.json()["detail"]) == (409, TOO_MANY)
        assert len(rows(alice)) == 10

    def test_names_are_unique_per_user(self, alice):
        alice.set_totp("bob", BOB_SECRET)
        register(alice, SoftKey(), key_name="YubiKey")
        register(alice, SoftKey(), "bob", key_name="YubiKey")

        response = finish(alice, SoftKey().register(challenge_of(begin(alice)), ORIGIN, RP), key_name="YubiKey")

        assert (response.status_code, response.json()["detail"]) == (409, NAME_TAKEN)

    @pytest.mark.parametrize("name", ["", "   ", "x" * 65, "Key\nNew", "Key‎"])
    def test_a_name_is_1_to_64_characters_without_control_characters(self, alice, name):
        body = SoftKey().register(challenge_of(begin(alice)), ORIGIN, RP)

        assert finish(alice, body, key_name=name).status_code == 422

    def test_a_credential_id_is_registered_once_across_users(self, alice):
        alice.set_totp("bob", BOB_SECRET)
        key = SoftKey()
        register(alice, key)

        mine = finish(alice, key.register(challenge_of(begin(alice)), ORIGIN, RP), key_name="Again")
        theirs = finish(alice, key.register(challenge_of(begin(alice, "bob")), ORIGIN, RP), "bob", key_name="Bob's")

        assert (mine.status_code, mine.json()["detail"]) == (409, ALREADY)
        assert (theirs.status_code, theirs.json()["detail"]) == (409, ALREADY)
        assert len(rows(alice)) == 1

    def test_turning_two_factor_authentication_off_meanwhile_registers_nothing(self, alice):
        challenge = challenge_of(begin(alice))
        with Session(alice.engine) as session:
            session.get(User, alice.ids["alice"]).totp_enabled = False
            session.commit()

        response = finish(alice, SoftKey().register(challenge, ORIGIN, RP))

        assert (response.status_code, response.json()["detail"]) == (409, NEEDS_TOTP)
        assert rows(alice) == []


class TestRegistrationChecks:
    """Each check of the answer: a refused one stores nothing, writes no audit entry, and uses the
    challenge up."""

    def refused(self, world, response, status=400, detail=NOT_ACCEPTED):
        assert (response.status_code, response.json()["detail"]) == (status, detail), response.text
        assert rows(world) == []
        assert entries(world, "security_key_added") == []

    def test_a_challenge_works_once(self, alice):
        key = SoftKey()
        body = key.register(challenge_of(begin(alice)), ORIGIN, RP)
        assert finish(alice, body).status_code == 200

        again = finish(alice, body, key_name="Again")

        assert (again.status_code, again.json()["detail"]) == (400, EXPIRED)

    def test_a_refused_answer_uses_the_challenge_up(self, alice):
        key = SoftKey()
        challenge = challenge_of(begin(alice))
        self.refused(alice, finish(alice, key.register(challenge, "https://evil.example", RP)))

        self.refused(alice, finish(alice, key.register(challenge, ORIGIN, RP)), detail=EXPIRED)

    def test_a_challenge_of_unknown_origin_is_refused(self, alice):
        self.refused(alice, finish(alice, SoftKey().register(b"\x00" * 32, ORIGIN, RP)), detail=EXPIRED)

    def test_a_challenge_expires_after_two_minutes(self, alice, ticks):
        challenge = challenge_of(begin(alice))
        ticks.now += 120

        self.refused(alice, finish(alice, SoftKey().register(challenge, ORIGIN, RP)), detail=EXPIRED)

    def test_a_challenge_is_bound_to_its_user(self, alice):
        alice.set_totp("bob", BOB_SECRET)
        challenge = challenge_of(begin(alice, "bob"))

        self.refused(alice, finish(alice, SoftKey().register(challenge, ORIGIN, RP)), detail=EXPIRED)

    def test_a_challenge_is_bound_to_its_session(self, alice):
        """world.headers makes a new session of alice at each call."""
        first, second = {**alice.headers("alice"), **HOST}, {**alice.headers("alice"), **HOST}
        challenge = challenge_of(begin(alice, headers=first))

        self.refused(alice, finish(alice, SoftKey().register(challenge, ORIGIN, RP), headers=second), detail=EXPIRED)

    def test_a_challenge_is_bound_to_its_origin(self, alice):
        """A forged Host header at the finish cannot move the key to another origin."""
        challenge = challenge_of(begin(alice))
        evil = {"Host": "evil.example", "Origin": "https://evil.example"}

        self.refused(alice, finish(alice, SoftKey().register(challenge, "https://evil.example", "evil.example"),
                                   host=evil))

    @pytest.mark.parametrize("bend", [
        pytest.param({"type_": "webauthn.get"}, id="type of a sign-in"),
        pytest.param({"client_extra": {"crossOrigin": True}}, id="cross-origin frame"),
        pytest.param({"flags": AT}, id="user not present"),
        pytest.param({"flags": UP}, id="no attested credential"),
        pytest.param({"credential_id": b"\x01" * 32}, id="another credential id"),
        pytest.param({"cose": encode({1: 2, 3: -35, -1: 2, -2: bytes(48), -3: bytes(48)})}, id="ES384 key"),
        pytest.param({"cose": b"\xbf\x01\x02\xff"}, id="indefinite-length key"),
        pytest.param({"cose": b"\xc0\x00"}, id="tagged key"),
    ])
    def test_a_bent_answer_is_refused(self, alice, bend):
        key = SoftKey()
        self.refused(alice, finish(alice, key.register(challenge_of(begin(alice)), ORIGIN, RP, **bend)))

    def test_the_finish_comes_from_the_origin_of_its_challenge(self, alice):
        """An answer that names the challenge's origin, sent from another page of the NAS."""
        challenge = challenge_of(begin(alice))
        other = {"Host": "nas.local:8443", "Origin": "https://nas.local:8443"}

        self.refused(alice, finish(alice, SoftKey().register(challenge, ORIGIN, RP), host=other))

    def test_a_challenge_is_bound_to_the_users_name(self, alice):
        session_id = session_id_of(own(alice, "alice")["Authorization"].split()[1])
        challenge = webauthn._challenges.issue(webauthn.CREATE, alice.ids["alice"], "carol", ORIGIN, RP, session_id)

        self.refused(alice, finish(alice, SoftKey().register(challenge, ORIGIN, RP)), detail=EXPIRED)

    def test_the_origin_of_the_page_is_checked(self, alice):
        key = SoftKey()
        self.refused(alice, finish(alice, key.register(challenge_of(begin(alice)), "https://nas.local:8443", RP)))

    def test_the_rp_id_hash_is_checked(self, alice):
        key = SoftKey()
        self.refused(alice, finish(alice, key.register(challenge_of(begin(alice)), ORIGIN, "evil.example")))

    def test_the_challenge_in_the_client_data_must_be_base64url(self, alice):
        begin(alice)
        self.refused(alice, finish(alice, SoftKey().register("not+base64/url==", ORIGIN, RP)))

    @pytest.mark.parametrize("field, value", [
        ("attestation_object", b64url(b"\xa0")),
        ("attestation_object", b64url(encode({"fmt": 1, "attStmt": {}, "authData": b""}))),
        ("attestation_object", "!!"),
        ("client_data_json", b64url(b"[]")),
        ("client_data_json", b64url(b"\xff")),
        ("credential_id", ""),
    ])
    def test_damaged_fields_are_refused(self, alice, field, value):
        body = SoftKey().register(challenge_of(begin(alice)), ORIGIN, RP)
        response = finish(alice, {**body, field: value})

        assert response.status_code in (400, 422), response.text
        assert rows(alice) == []

    def test_user_verification_is_not_required(self, alice):
        body = SoftKey().register(challenge_of(begin(alice)), ORIGIN, RP, flags=UP | UV | AT)

        assert finish(alice, body).status_code == 200


# -- deleting -----------------------------------------------------------------------------------


class TestDeletion:
    def delete(self, world, key_id, name="alice", password=None):
        return world.client.request(
            "DELETE", f"{API}/auth/security-keys/{key_id}",
            json={"current_password": PASSWORDS[name] if password is None else password}, headers=world.headers(name),
        )

    def test_a_key_is_removed_with_the_password(self, alice):
        kept = register(alice, SoftKey(), key_name="Kept")
        gone = register(alice, SoftKey(), key_name="Gone")

        wrong = self.delete(alice, gone["id"], password=BOB_PW)
        assert (wrong.status_code, wrong.json()["detail"]) == (400, WRONG_PASSWORD)
        assert len(rows(alice)) == 2

        response = self.delete(alice, gone["id"])

        assert response.status_code == 200, response.text
        assert [row["name"] for row in listed(alice)] == ["Kept"] and kept["id"] != gone["id"]
        assert entries(alice, "security_key_removed") == [
            ("alice", "Security key 'Gone' removed, for https://nas.local", "warning")]

    def test_another_users_key_is_not_found(self, alice):
        alice.set_totp("bob", BOB_SECRET)
        theirs = register(alice, SoftKey(), "bob")

        response = self.delete(alice, theirs["id"])

        assert (response.status_code, response.json()["detail"]) == (404, NOT_FOUND)
        assert len(rows(alice, "bob")) == 1
        assert listed(alice) == []

    def test_a_removed_key_signs_in_no_more(self, alice):
        key = SoftKey()
        self.delete(alice, register(alice, key)["id"])

        answer = code_step(alice)
        assert "security_key" not in answer and "security_key_origins" not in answer


# -- the sign-in --------------------------------------------------------------------------------


class TestSignIn:
    def test_the_code_step_offers_the_keys_of_its_origin(self, alice):
        key = SoftKey()
        register(alice, key)

        answer = code_step(alice)

        offer = answer["security_key"]
        assert (offer["rp_id"], offer["credential_ids"], offer["timeout_ms"], offer["user_verification"]) == (
            RP, [key.id], 60000, "discouraged")
        assert len(unb64url(offer["challenge"])) == 32
        assert answer["security_key_origins"] == [ORIGIN]

    @pytest.mark.parametrize("host", [
        {"Host": "192.168.1.10"}, {"Host": "nas.local:8443"}, {"Host": "evil.example"}, {},
    ])
    def test_another_address_gets_no_challenge_only_where_the_keys_work(self, alice, host):
        register(alice, SoftKey())
        webauthn._challenges.clear()

        answer = code_step(alice, host=host)

        assert "security_key" not in answer
        assert answer["security_key_origins"] == [ORIGIN]
        assert webauthn._challenges._pending == {}

    def test_an_account_without_keys_gets_no_challenge(self, alice):
        answer = code_step(alice)

        assert answer == {"access_token": "", "token_type": "bearer", "requires_totp": True, "session_key": ""}
        assert webauthn._challenges._pending == {}

    @pytest.mark.parametrize("alg", [ES256, EDDSA, RS256])
    def test_a_touch_of_the_key_opens_the_session(self, alice, alg):
        key = SoftKey(alg)
        key_id = register(alice, key, key_name="YubiKey")["id"]

        response = key_sign_in(alice, key)

        me = alice.client.get(f"{API}/auth/me", headers=bearer(response))
        assert me.json()["username"] == "alice"
        assert attempts(alice)[-1] == ("alice", True, None)
        assert entries(alice, "login_success") == [("alice", "Signed in with the security key 'YubiKey'", "info")]
        [row] = rows(alice)
        assert row.sign_count == 1 and row.last_used_at is not None
        assert [k["last_used_at"] is not None for k in listed(alice) if k["id"] == key_id] == [True]

    def test_the_session_cookie_of_the_web_ui(self, alice):
        key = SoftKey()
        register(alice, key)
        own = {**HOST, "Sec-Fetch-Site": "same-origin"}
        challenge = unb64url(code_step(alice, host=own)["security_key"]["challenge"])

        response = sign_in(alice, host=own, cookie=True, security_key=key.assertion(challenge, ORIGIN, RP))

        assert response.status_code == 200, response.text
        assert (response.json()["access_token"], response.json()["token_type"]) == ("", "cookie")
        assert response.json()["session_key"]
        assert "__Host-unos-session=" in response.headers["set-cookie"]

    def test_the_password_comes_first(self, alice):
        key = SoftKey()
        register(alice, key)
        challenge = unb64url(code_step(alice)["security_key"]["challenge"])
        assertion = key.assertion(challenge, ORIGIN, RP)

        wrong = alice.client.post(f"{API}/auth/login", headers=HOST, json={
            "username": "alice", "password": BOB_PW, "security_key": assertion})

        assert (wrong.status_code, wrong.json()["detail"]) == (401, "Incorrect username or password")
        # The challenge is still there for the right password.
        assert sign_in(alice, security_key=assertion).status_code == 200

    def test_one_second_factor_at_a_time(self, alice, clock):
        key = SoftKey()
        register(alice, key)
        challenge = unb64url(code_step(alice)["security_key"]["challenge"])

        response = sign_in(alice, totp_code=clock.code(SECRET), security_key=key.assertion(challenge, ORIGIN, RP))

        assert response.status_code == 422

    def test_the_app_code_and_recovery_codes_still_sign_in(self, alice, clock):
        register(alice, SoftKey())
        generated = alice.client.post(f"{API}/auth/recovery-codes/generate", headers=own(alice, "alice"))
        assert generated.status_code == 200, generated.text

        assert sign_in(alice, totp_code=clock.code(SECRET)).status_code == 200
        assert sign_in(alice, recovery_code=generated.json()["codes"][0]).status_code == 200

    def test_an_answer_works_once(self, alice):
        key = SoftKey()
        register(alice, key)
        challenge = unb64url(code_step(alice)["security_key"]["challenge"])
        assertion = key.assertion(challenge, ORIGIN, RP)
        assert sign_in(alice, security_key=assertion).status_code == 200

        replay = sign_in(alice, security_key=assertion)

        assert (replay.status_code, replay.json()["detail"]) == (401, EXPIRED)
        assert attempts(alice)[-1] == ("alice", False, "Expired security key request")

    def test_a_challenge_expires_after_two_minutes(self, alice, ticks):
        key = SoftKey()
        register(alice, key)
        challenge = unb64url(code_step(alice)["security_key"]["challenge"])
        ticks.now += 120

        response = sign_in(alice, security_key=key.assertion(challenge, ORIGIN, RP))

        assert (response.status_code, response.json()["detail"]) == (401, EXPIRED)

    def test_a_challenge_is_bound_to_the_user_whose_password_issued_it(self, alice):
        alice.set_totp("bob", BOB_SECRET)
        mine, theirs = SoftKey(), SoftKey()
        register(alice, mine)
        register(alice, theirs, "bob")
        bobs = unb64url(code_step(alice, "bob")["security_key"]["challenge"])

        response = sign_in(alice, security_key=mine.assertion(bobs, ORIGIN, RP))

        assert (response.status_code, response.json()["detail"]) == (401, EXPIRED)

    def test_a_challenge_is_bound_to_the_users_id_too(self, alice):
        """SQLite reuses ids, and a rename frees a name: both must match."""
        key = SoftKey()
        register(alice, key)
        challenge = webauthn._challenges.issue(webauthn.GET, alice.ids["alice"] + 100, "alice", ORIGIN, RP)

        response = sign_in(alice, security_key=key.assertion(challenge, ORIGIN, RP))

        assert (response.status_code, response.json()["detail"]) == (401, EXPIRED)

    def test_a_challenge_is_bound_to_the_users_name_too(self, alice):
        key = SoftKey()
        register(alice, key)
        challenge = webauthn._challenges.issue(webauthn.GET, alice.ids["alice"], "carol", ORIGIN, RP)

        response = sign_in(alice, security_key=key.assertion(challenge, ORIGIN, RP))

        assert (response.status_code, response.json()["detail"]) == (401, EXPIRED)

    @pytest.mark.parametrize("origin, rp_id", [
        ("https://other.example", "other.example"), (ORIGIN, "other.example"), ("https://nas.local:8443", RP),
    ])
    def test_a_challenge_of_another_origin_or_rp_id_signs_in_no_key_of_this_one(self, alice, origin, rp_id):
        """A key answers with its own origin and RP ID: a challenge that was issued for others is
        refused, whatever the answer says."""
        key = SoftKey()
        register(alice, key)
        challenge = webauthn._challenges.issue(webauthn.GET, alice.ids["alice"], "alice", origin, rp_id)

        response = sign_in(alice, security_key=key.assertion(challenge, ORIGIN, RP))

        assert (response.status_code, response.json()["detail"]) == (401, INVALID)

    def test_another_sign_in_that_moved_the_counter_meanwhile_refuses_this_one(self, alice, monkeypatch):
        """The counter and the last use are written only while the counter is still the one read."""
        key = SoftKey()
        register(alice, key)
        with Session(alice.engine) as session:
            session.execute(SecurityKey.__table__.update().values(sign_count=5))
            session.commit()
        stale = security_keys._stored_keys

        async def read_before_the_other_sign_in(db, user_id):
            return [dataclasses.replace(k, sign_count=0) for k in await stale(db, user_id)]

        monkeypatch.setattr(security_keys, "_stored_keys", read_before_the_other_sign_in)

        response = key_sign_in(alice, key, counter=1)

        assert (response.status_code, response.json()["detail"]) == (401, INVALID)
        assert rows(alice)[0].sign_count == 5 and rows(alice)[0].last_used_at is None
        assert entries(alice, "login_success") == []

    def test_a_challenge_of_the_other_ceremony_is_refused(self, alice):
        key = SoftKey()
        register(alice, key)
        registration = challenge_of(begin(alice))
        sign_in_challenge = unb64url(code_step(alice)["security_key"]["challenge"])

        response = sign_in(alice, security_key=key.assertion(registration, ORIGIN, RP, type_="webauthn.create"))
        refused = finish(alice, SoftKey().register(sign_in_challenge, ORIGIN, RP, type_="webauthn.get"), key_name="Other")

        assert (response.status_code, response.json()["detail"]) == (401, EXPIRED)
        assert (refused.status_code, refused.json()["detail"]) == (400, EXPIRED)

    def test_another_users_key_does_not_sign_in(self, alice):
        alice.set_totp("bob", BOB_SECRET)
        register(alice, SoftKey())
        bobs_key = SoftKey()
        register(alice, bobs_key, "bob")

        response = key_sign_in(alice, bobs_key)

        assert (response.status_code, response.json()["detail"]) == (401, INVALID)
        assert attempts(alice)[-1] == ("alice", False, "Invalid security key")

    def test_a_forged_host_header_cannot_make_a_key_work_for_another_origin(self, alice):
        """The key was registered at https://nas.local: an answer made for https://evil.example, with
        that Host on both steps, finds no challenge for it and is refused."""
        key = SoftKey()
        register(alice, key)
        evil = {"Host": "evil.example", "Origin": "https://evil.example"}
        assert "security_key" not in code_step(alice, host=evil)
        challenge = unb64url(code_step(alice)["security_key"]["challenge"])

        response = sign_in(alice, host=evil,
                           security_key=key.assertion(challenge, "https://evil.example", "evil.example"))

        assert (response.status_code, response.json()["detail"]) == (401, INVALID)

    @pytest.mark.parametrize("bend", [
        pytest.param({"type_": "webauthn.create"}, id="type of a registration"),
        pytest.param({"client_extra": {"crossOrigin": True}}, id="cross-origin frame"),
        pytest.param({"flags": 0}, id="user not present"),
        pytest.param({"flags": UP | AT}, id="attested credential data at a sign-in"),
        pytest.param({"signer": SoftKey()}, id="signed by another key"),
        pytest.param({"extensions": b"\xa0"}, id="bytes after the counter"),
    ])
    def test_a_bent_answer_is_refused(self, alice, bend):
        key = SoftKey()
        register(alice, key)

        response = key_sign_in(alice, key, **bend)

        assert (response.status_code, response.json()["detail"]) == (401, INVALID)
        assert attempts(alice)[-1] == ("alice", False, "Invalid security key")
        assert entries(alice, "login_success") == []

    def test_the_origin_and_the_rp_id_are_the_keys_own(self, alice):
        key = SoftKey()
        register(alice, key)
        for origin, rp_id in (("https://nas.local:8443", RP), (ORIGIN, "evil.example")):
            challenge = unb64url(code_step(alice)["security_key"]["challenge"])
            response = sign_in(alice, security_key=key.assertion(challenge, origin, rp_id))
            assert (response.status_code, response.json()["detail"]) == (401, INVALID)

    def test_a_signature_counter_that_goes_backwards_is_refused(self, alice):
        key = SoftKey(counter=10)
        register(alice, key, key_name="Old key")
        assert key_sign_in(alice, key).status_code == 200  # 11

        for counter in (11, 5):
            response = key_sign_in(alice, key, counter=counter)
            assert (response.status_code, response.json()["detail"]) == (401, CLONED)

        assert rows(alice)[0].sign_count == 11
        assert entries(alice, "login_failed") == [
            ("alice", "Security key 'Old key' refused: its signature counter went backwards, so it may have been copied",
             "warning"),
        ] * 2
        assert key_sign_in(alice, key, counter=12).status_code == 200

    def test_a_key_without_a_counter_keeps_signing_in(self, alice):
        key = SoftKey()
        register(alice, key)

        for _ in range(2):
            assert key_sign_in(alice, key, counter=0).status_code == 200
        assert rows(alice)[0].sign_count == 0

    def test_a_key_counts_only_with_two_factor_authentication_on(self, alice):
        """Keys left in a restored database while two-factor authentication is off offer nothing:
        the password alone signs in, as for every account without it."""
        register(alice, SoftKey())
        with Session(alice.engine) as session:
            session.get(User, alice.ids["alice"]).totp_enabled = False
            session.commit()

        answer = sign_in(alice).json()

        assert answer["requires_totp"] is False

    def test_the_rate_limit_of_the_sign_in_counts_the_key_step(self, alice, monkeypatch):
        """5 sign-in requests a minute per client (ADR-0010): the password step and the key's answer
        are one each."""
        key = SoftKey()
        register(alice, key)
        auth_router.limiter.reset()
        monkeypatch.setattr(auth_router.limiter, "enabled", True)
        for _ in range(2):
            assert key_sign_in(alice, key).status_code == 200
        challenge = unb64url(code_step(alice)["security_key"]["challenge"])

        response = sign_in(alice, security_key=key.assertion(challenge, ORIGIN, RP))

        assert response.status_code == 429

    def test_a_damaged_stored_key_is_refused_not_a_server_error(self, alice):
        key = SoftKey()
        register(alice, key)
        challenge = unb64url(code_step(alice)["security_key"]["challenge"])
        with Session(alice.engine) as session:
            session.execute(SecurityKey.__table__.update().values(public_key=b"\xc0\x00", sign_count="many"))
            session.commit()

        response = sign_in(alice, security_key=key.assertion(challenge, ORIGIN, RP))

        assert (response.status_code, response.json()["detail"]) == (401, INVALID)
        [shown] = listed(alice)
        assert shown["usable"] is False

    @pytest.mark.parametrize("field, value", [
        ("client_data_json", "x" * 6001),
        ("signature", "x" * 1401),
        ("credential_id", "x" * 1400),
        ("authenticator_data", "x" * 2001),
    ])
    def test_the_fields_of_an_answer_are_bounded(self, alice, field, value):
        assertion = {"credential_id": "AA", "client_data_json": "AA", "authenticator_data": "AA", "signature": "AA"}

        response = sign_in(alice, security_key={**assertion, field: value})

        assert response.status_code == 422


# -- what removes keys --------------------------------------------------------------------------


def count(world: World, name: str) -> int:
    with Session(world.engine) as session:
        return session.scalar(select(func.count()).select_from(SecurityKey).where(SecurityKey.user_id == world.ids[name]))


class TestWhatRemovesKeys:
    def test_turning_two_factor_authentication_off_removes_them(self, alice, clock):
        register(alice, SoftKey())
        register(alice, SoftKey(), key_name="Spare")

        response = alice.client.post(f"{API}/auth/totp/disable", json={"code": clock.code(SECRET)},
                                     headers=alice.headers("alice"))

        assert response.status_code == 200, response.text
        assert count(alice, "alice") == 0
        assert entries(alice, "totp_disabled") == [(
            "alice", "Two-factor authentication turned off by the user, with its recovery codes and 2 security keys",
            "warning")]

    def test_turning_it_on_again_starts_without_keys(self, world, clock):
        """Keys of an earlier setup never sign in, as recovery codes do not (ADR-0238)."""
        with Session(world.engine) as session:
            session.add(SecurityKey(user_id=world.ids["alice"], name="Old", credential_id="AAAA", public_key=b"\xa0",
                                    algorithm=ES256, sign_count=0, rp_id=RP, origin=ORIGIN))
            session.get(User, world.ids["alice"]).totp_secret = SECRET
            session.commit()

        response = world.client.post(f"{API}/auth/totp/enable", json={"code": clock.code(SECRET)},
                                     headers=world.headers("alice"))

        assert response.status_code == 200, response.text
        assert count(world, "alice") == 0

    def test_deleting_the_user_removes_them(self, alice):
        register(alice, SoftKey())

        response = alice.delete_user("alice")

        assert response.status_code == 200, response.text
        with Session(alice.engine) as session:
            assert session.scalar(select(func.count()).select_from(SecurityKey)) == 0

    def test_the_emergency_reset_removes_the_administrators_keys(self, world, monkeypatch, capsys):
        world.set_totp("admin", SECRET)
        register(world, SoftKey(), "admin")
        world.set_totp("alice", BOB_SECRET)
        register(world, SoftKey(), "alice")
        monkeypatch.setattr(get_settings(), "db_path", str(world.path))
        monkeypatch.setattr(emergency, "hash_password", _hash)
        monkeypatch.setattr(sys, "stdin", io.StringIO("Reset-pass-2026\n"))

        assert emergency.main(["reset-admin", "admin"]) == 0, capsys.readouterr()

        assert (count(world, "admin"), count(world, "alice")) == (0, 1)
        assert "security keys" in capsys.readouterr().out
        [entry] = [e for e in world.audit() if e["action"] == "totp_disabled"]
        assert entry["details"] == (
            "Two-factor authentication turned off on the host's command line, with its recovery codes and 1 "
            "security key")



class TestARestore:
    """A backup holds the database with its security_keys table, and a restore brings the keys back."""

    @pytest.fixture
    def env(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.audit.router.AUDIT_FILE", str(tmp_path / "audit.jsonl"))
        monkeypatch.setattr(webauthn, "_challenges", webauthn.Challenges())
        yield from make_env(tmp_path, monkeypatch, smb_applied=True)

    def test_the_keys_come_back_and_the_challenges_are_forgotten(self, env):
        """Challenges name users by id, and the restored database may give an id to another user."""
        path = env.tmp / "restored.db"
        _make_db(path, users=[("admin2", 1)])
        engine = create_engine(f"sqlite:///{path}")
        Base.metadata.create_all(engine, tables=[SecurityKey.__table__])
        with engine.begin() as conn:
            conn.execute(SecurityKey.__table__.insert().values(
                user_id=1, name="Restored", credential_id="AAAA", public_key=b"\xa0", algorithm=ES256,
                sign_count=3, rp_id=RP, origin=ORIGIN))
        engine.dispose()
        webauthn._challenges.issue("webauthn.get", 1, "admin", ORIGIN, RP)

        name = _store(env, [_file("manifest.json", MANIFEST), _file("unos.db", path.read_bytes())])
        answer = _restore(env, name)

        assert answer.status_code == 200, answer.text
        assert webauthn._challenges._pending == {}
        live = create_engine(f"sqlite:///{env.db_path}")
        with Session(live) as session:
            assert [(row.name, row.sign_count) for row in session.scalars(select(SecurityKey))] == [("Restored", 3)]
        live.dispose()


class TestRestoredRows:
    def test_a_key_of_a_damaged_row_is_listed_and_can_be_removed(self, alice):
        with closing(sqlite3.connect(alice.path)) as conn:
            conn.execute(
                "INSERT INTO security_keys (user_id, name, credential_id, public_key, algorithm, sign_count, rp_id,"
                " origin, created_at, last_used_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (alice.ids["alice"], b"\xff\xfe", 12, "text", "x", -1, b"\x00", b"\x00", "not a time", 7))
            conn.commit()

        [shown] = listed(alice)
        assert (shown["name"], shown["usable"], shown["created_at"], shown["origin"]) == ("?", False, None, None)

        response = alice.client.request("DELETE", f"{API}/auth/security-keys/{shown['id']}",
                                        json={"current_password": ALICE_PW}, headers=alice.headers("alice"))
        assert response.status_code == 200, response.text
        assert count(alice, "alice") == 0


# -- the device check's software authenticator --------------------------------------------------

PROBE = Path(__file__).resolve().parents[2] / "scripts" / "dev" / "security-key-probe.py"
# The real subprocess.run, which the fake hosts of World replace: the probe runs the real openssl.
_RUN = subprocess.run


def _probe_module():
    spec = importlib.util.spec_from_file_location("security_key_probe", PROBE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _ProbeApi:
    """The probe's Api over the test client: the same calls, with its Host and Origin."""

    def __init__(self, world: World, host: str) -> None:
        self.world, self.host = world, host

    def call(self, method, path, body=None, token=None):
        headers = {"Host": self.host, "Origin": "https://" + self.host}
        if token:
            headers["Authorization"] = "Bearer " + token
        response = self.world.client.request(method, f"{API}{path}", json=body, headers=headers)
        answer = response.json()
        return response.status_code, answer if isinstance(answer, (dict, list)) else {}


@pytest.mark.skipif(shutil.which("openssl") is None, reason="needs the openssl command line")
class TestTheDeviceProbe:
    """scripts/dev/security-key-probe.py, which the device check runs with the host's python3 and
    openssl (no cryptography there), registers a key of openssl and signs in with it."""

    def test_every_step_passes_against_the_api(self, world, tmp_path, monkeypatch):
        lines = []
        module = _probe_module()
        from src.auth import utils as auth_utils

        monkeypatch.setattr(auth_utils, "_totp_time", lambda: datetime.now(timezone.utc))

        assert module.probe(_ProbeApi(world, "unoscheck.invalid"), "bob", BOB_PW, str(tmp_path), lines.append,
                            runner=_RUN), lines
        assert [line.split("  ")[0] for line in lines] == ["PASS"] * 9
        assert rows(world, "bob") == []
        assert [e["details"] for e in world.audit() if e["action"] == "login_success"] == [
            None, "Signed in with the security key 'unoscheck key'"]

    def test_its_cbor_and_totp_match_the_servers(self):
        module = _probe_module()

        assert cbor.decode(module.cbor({1: 2, -1: b"\x00" * 40, "fmt": "none"})) == {1: 2, -1: b"\x00" * 40, "fmt": "none"}
        assert module.totp(SECRET, 1_795_000_000) == pyotp.TOTP(SECRET).at(1_795_000_000)
