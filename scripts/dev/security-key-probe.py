#!/usr/bin/env python3
"""Check security keys (ADR-0406) through the API of the device it runs on, with a software
authenticator that signs with the openssl command line: the device check of auth.security-keys.

    python3 -I -B security-key-probe.py < request.json

It runs as root on the device, from /dev/shm, with the host's python3 (standard library only) and
openssl, and calls only https://127.0.0.1/api/v1, through Caddy, with the Host and Origin of a
name that is no real address (``host``, unoscheck.invalid by default), so that its key works at no
address a browser opens. Standard input holds one JSON object: ``username`` and ``password`` of a
temporary regular user without two-factor authentication, which the check made through
scripts/dev/api.py and deletes afterwards, and optionally ``base`` and ``host``. The password never
comes from the command line (ADR-0125).

Steps, one line each, PASS or FAIL: sign in with the password and turn two-factor authentication on
(its TOTP code computed here); make an EC P-256 key with openssl in a folder of its own in
/dev/shm; register it (begin with the password, finish with the attestation of format none); sign
in with it at the code step; the same answer again is refused; remove it with the password; the
code step offers no key any more. The folder is removed at the end, whatever happened. It signs in
5 times: the login's limit of 5 a minute per client (ADR-0010) is reached, so the relay of
api.py must already hold a token. Exit status: 0 when every step passed, 1 otherwise.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import shutil
import ssl
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from typing import Optional, Tuple

EXPIRED = "The security key request expired or was already used: try again"


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def cbor(value: object) -> bytes:
    """CBOR of integers, bytes, texts and maps (in their order): what a COSE key and an
    attestation object of format none hold."""
    def head(major: int, argument: int) -> bytes:
        if argument < 24:
            return bytes([major << 5 | argument])
        for info, size in ((24, 1), (25, 2), (26, 4), (27, 8)):
            if argument < 1 << (8 * size):
                return bytes([major << 5 | info]) + argument.to_bytes(size, "big")
        raise ValueError("too large")

    if isinstance(value, bool):
        raise TypeError("no booleans needed")
    if isinstance(value, int):
        return head(0, value) if value >= 0 else head(1, -1 - value)
    if isinstance(value, bytes):
        return head(2, len(value)) + value
    if isinstance(value, str):
        raw = value.encode()
        return head(3, len(raw)) + raw
    if isinstance(value, dict):
        return head(5, len(value)) + b"".join(cbor(k) + cbor(v) for k, v in value.items())
    raise TypeError(type(value))


def totp(secret: str, now: float) -> str:
    """The 6-digit TOTP code (RFC 6238, SHA-1, 30 s) of a base32 secret."""
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", int(now // 30)), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    return "%06d" % ((struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 1000000)


class OpensslKey:
    """An EC P-256 key in ``folder``, made and used by the openssl command line."""

    def __init__(self, folder: str, openssl: str = "openssl", runner=None) -> None:
        self.folder, self.openssl = folder, openssl
        self.runner = runner or subprocess.run
        self.pem = os.path.join(folder, "key.pem")
        self.run("ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", self.pem)
        der = self.run("ec", "-in", self.pem, "-pubout", "-outform", "DER")
        point = der[-65:]  # the uncompressed point 04 || x || y ends the SubjectPublicKeyInfo
        if len(der) != 91 or point[0] != 4:
            raise RuntimeError("openssl wrote no P-256 public key")
        self.cose = cbor({1: 2, 3: -7, -1: 1, -2: point[1:33], -3: point[33:]})
        self.credential_id = secrets.token_bytes(32)
        self.counter = 0

    def run(self, *args: str, data: Optional[bytes] = None) -> bytes:
        return self.runner([self.openssl, *args], input=data, capture_output=True, check=True, timeout=30).stdout

    def sign(self, data: bytes) -> bytes:
        """An ES256 signature (DER) of ``data``, as WebAuthn takes it."""
        return self.run("dgst", "-sha256", "-sign", self.pem, data=data)

    def registration(self, challenge: str, origin: str, rp_id: str) -> dict:
        client = json.dumps({"type": "webauthn.create", "challenge": challenge, "origin": origin}).encode()
        attested = bytes(16) + struct.pack(">H", len(self.credential_id)) + self.credential_id + self.cose
        auth = hashlib.sha256(rp_id.encode()).digest() + bytes([0x41]) + struct.pack(">I", self.counter) + attested
        return {
            "credential_id": b64url(self.credential_id),
            "client_data_json": b64url(client),
            "attestation_object": b64url(cbor({"fmt": "none", "attStmt": {}, "authData": auth})),
        }

    def assertion(self, challenge: str, origin: str, rp_id: str) -> dict:
        self.counter += 1
        client = json.dumps({"type": "webauthn.get", "challenge": challenge, "origin": origin}).encode()
        auth = hashlib.sha256(rp_id.encode()).digest() + bytes([0x01]) + struct.pack(">I", self.counter)
        return {
            "credential_id": b64url(self.credential_id),
            "client_data_json": b64url(client),
            "authenticator_data": b64url(auth),
            "signature": b64url(self.sign(auth + hashlib.sha256(client).digest())),
        }


class Api:
    """The device's API through Caddy, with the Host and Origin of ``host``."""

    def __init__(self, base: str, host: str) -> None:
        self.base, self.host = base.rstrip("/"), host
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE  # Caddy's local CA; the loopback address only
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context))

    def call(self, method: str, path: str, body: Optional[dict] = None,
             token: Optional[str] = None) -> Tuple[int, object]:
        headers = {"Host": self.host, "Origin": "https://" + self.host, "Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(self.base + "/api/v1" + path, data=data, method=method, headers=headers)
        try:
            with self.opener.open(request, timeout=60) as response:
                status, raw = response.status, response.read()
        except urllib.error.HTTPError as error:
            status, raw = error.code, error.read()
        try:
            answer = json.loads(raw or b"null")
        except ValueError:
            answer = None
        # A dict or a list, as the API answers; {} for anything else, so that a step reads no None.
        return status, answer if isinstance(answer, (dict, list)) else {}


def probe(api, username: str, password: str, folder: str, say=print, openssl: str = "openssl", runner=None) -> bool:
    """The steps; whether every one passed. ``api`` has call(method, path, body, token); ``runner``
    runs openssl (subprocess.run)."""
    origin, rp_id = "https://" + api.host, api.host.split(":")[0]

    def step(name: str, ok: bool, detail: object = "") -> bool:
        say(("PASS  " if ok else "FAIL  ") + name + ("" if ok or detail == "" else ": %s" % (detail,)))
        return ok

    status, answer = api.call("POST", "/auth/login", {"username": username, "password": password})
    if not step("sign in with the password", status == 200 and not answer.get("requires_totp"), status):
        return False
    token = answer["access_token"]
    status, setup = api.call("POST", "/auth/totp/setup", {}, token)
    if status == 200:
        now = time.time()
        if 30 - now % 30 < 3:
            time.sleep(4)
            now = time.time()
        status, _ = api.call("POST", "/auth/totp/enable", {"code": totp(setup["secret"], now)}, token)
    if not step("turn two-factor authentication on", status == 200, status):
        return False
    key = OpensslKey(folder, openssl, runner)
    status, options = api.call("POST", "/auth/security-keys/register/begin", {"current_password": password}, token)
    if not step("begin a registration", status == 200 and options.get("rp_id") == rp_id, (status, options)):
        return False
    body = dict(key.registration(options["challenge"], origin, rp_id), name="unoscheck key")
    status, added = api.call("POST", "/auth/security-keys/register/finish", body, token)
    if not step("register the key", status == 200 and added.get("origin") == origin, (status, added)):
        return False
    status, code_step = api.call("POST", "/auth/login", {"username": username, "password": password})
    offer = code_step.get("security_key") if status == 200 else None
    if not step("the code step offers the key", bool(offer) and offer["credential_ids"] == [body["credential_id"]],
                (status, code_step)):
        return False
    answer = key.assertion(offer["challenge"], origin, rp_id)
    status, signed_in = api.call("POST", "/auth/login", {"username": username, "password": password,
                                                         "security_key": answer})
    me_status, me = api.call("GET", "/auth/me", None, signed_in.get("access_token") if status == 200 else None)
    if not step("sign in with the key", status == 200 and me_status == 200 and me.get("username") == username,
                (status, signed_in)):
        return False
    status, replay = api.call("POST", "/auth/login", {"username": username, "password": password,
                                                      "security_key": answer})
    ok = step("the same answer again is refused", status == 401 and replay.get("detail") == EXPIRED, (status, replay))
    status, _ = api.call("DELETE", "/auth/security-keys/%d" % added["id"], {"current_password": password}, token)
    listed_status, listed = api.call("GET", "/auth/security-keys", None, token)
    ok = step("remove the key", status == 200 and listed_status == 200 and listed == [], (status, listed)) and ok
    status, code_step = api.call("POST", "/auth/login", {"username": username, "password": password})
    return step("the code step offers no key", status == 200 and code_step.get("requires_totp") is True
                and "security_key" not in code_step, (status, code_step)) and ok


def main() -> int:
    try:
        request = json.loads(sys.stdin.readline())
        username, password = request["username"], request["password"]
    except (ValueError, KeyError, TypeError):
        print("security-key-probe: standard input must hold {\"username\": ..., \"password\": ...}", file=sys.stderr)
        return 1
    shm = "/dev/shm" if os.path.isdir("/dev/shm") else None
    folder = tempfile.mkdtemp(prefix="unoscheck-key-", dir=shm)
    try:
        api = Api(request.get("base", "https://127.0.0.1"), request.get("host", "unoscheck.invalid"))
        return 0 if probe(api, username, password, folder) else 1
    except (OSError, subprocess.SubprocessError, RuntimeError) as error:
        print("FAIL  %s" % error)
        return 1
    finally:
        shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
