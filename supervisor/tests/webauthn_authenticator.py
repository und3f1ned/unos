"""A software authenticator for the tests of security keys (ADR-0409), built on cryptography: it
makes credentials of ES256, EdDSA or RS256, answers a registration (fmt none) and signs sign-ins as
a hardware key does, with a CBOR encoder of its own. Each of its answers can be bent by the test
(the client data's type, origin and challenge, the flags, the counter, the relying party), so that
the checks of the supervisor's verification have tests that refuse what each check is for.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import struct

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa

ES256, EDDSA, RS256 = -7, -8, -257
UP, UV, AT, ED = 0x01, 0x04, 0x40, 0x80


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def cbor(value: object) -> bytes:
    """``value`` in CBOR: integers, bytes, texts, lists, dicts (in their order), booleans, None."""
    def head(major: int, argument: int) -> bytes:
        if argument < 24:
            return bytes([major << 5 | argument])
        for info, size in ((24, 1), (25, 2), (26, 4), (27, 8)):
            if argument < 1 << (8 * size):
                return bytes([major << 5 | info]) + argument.to_bytes(size, "big")
        raise ValueError("too large")

    if value is False:
        return b"\xf4"
    if value is True:
        return b"\xf5"
    if value is None:
        return b"\xf6"
    if isinstance(value, int):
        return head(0, value) if value >= 0 else head(1, -1 - value)
    if isinstance(value, bytes):
        return head(2, len(value)) + value
    if isinstance(value, str):
        raw = value.encode()
        return head(3, len(raw)) + raw
    if isinstance(value, list):
        return head(4, len(value)) + b"".join(cbor(item) for item in value)
    if isinstance(value, dict):
        return head(5, len(value)) + b"".join(cbor(k) + cbor(v) for k, v in value.items())
    raise TypeError(type(value))


def client_data(type_: str, challenge: bytes | str, origin: str, **extra) -> bytes:
    """clientDataJSON as a browser writes it; ``challenge`` as bytes, or as the text to put in."""
    text = b64url(challenge) if isinstance(challenge, bytes) else challenge
    return json.dumps({"type": type_, "challenge": text, "origin": origin, **extra}).encode()


class SoftKey:
    """A credential of a software authenticator."""

    def __init__(self, alg: int = ES256, *, credential_id: bytes | None = None, counter: int = 0) -> None:
        self.alg = alg
        if alg == ES256:
            self.private = ec.generate_private_key(ec.SECP256R1())
        elif alg == EDDSA:
            self.private = ed25519.Ed25519PrivateKey.generate()
        elif alg == RS256:
            self.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        else:
            raise ValueError(alg)
        self.credential_id = credential_id if credential_id is not None else os.urandom(32)
        self.counter = counter

    @property
    def id(self) -> str:
        return b64url(self.credential_id)

    def cose(self) -> bytes:
        """The credential's public key as a COSE_Key."""
        public = self.private.public_key()
        if self.alg == ES256:
            numbers = public.public_numbers()
            return cbor({1: 2, 3: ES256, -1: 1, -2: numbers.x.to_bytes(32, "big"), -3: numbers.y.to_bytes(32, "big")})
        if self.alg == EDDSA:
            from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

            return cbor({1: 1, 3: EDDSA, -1: 6, -2: public.public_bytes(Encoding.Raw, PublicFormat.Raw)})
        numbers = public.public_numbers()
        return cbor({1: 3, 3: RS256, -1: numbers.n.to_bytes(256, "big"), -2: numbers.e.to_bytes(3, "big")})

    def auth_data(self, rp_id: str, flags: int, counter: int, attested: bytes = b"", extensions: bytes = b"") -> bytes:
        return hashlib.sha256(rp_id.encode()).digest() + bytes([flags]) + struct.pack(">I", counter) + attested + extensions

    def register(
        self, challenge: bytes | str, origin: str, rp_id: str, *, type_: str = "webauthn.create",
        flags: int = UP | AT, fmt: str = "none", att_stmt: dict | None = None, cose: bytes | None = None,
        credential_id: bytes | None = None, extensions: bytes = b"", client_extra: dict | None = None,
    ) -> dict:
        """The body of POST /auth/security-keys/register/finish, without the name."""
        cred = self.credential_id if credential_id is None else credential_id
        attested = bytes(16) + struct.pack(">H", len(cred)) + cred + (self.cose() if cose is None else cose)
        auth = self.auth_data(rp_id, flags, self.counter, attested if flags & AT else b"", extensions)
        attestation = cbor({"fmt": fmt, "attStmt": att_stmt or {}, "authData": auth})
        return {
            "credential_id": self.id,
            "client_data_json": b64url(client_data(type_, challenge, origin, **(client_extra or {}))),
            "attestation_object": b64url(attestation),
        }

    def sign(self, data: bytes) -> bytes:
        if self.alg == ES256:
            return self.private.sign(data, ec.ECDSA(hashes.SHA256()))
        if self.alg == EDDSA:
            return self.private.sign(data)
        return self.private.sign(data, padding.PKCS1v15(), hashes.SHA256())

    def assertion(
        self, challenge: bytes | str, origin: str, rp_id: str, *, type_: str = "webauthn.get", flags: int = UP,
        counter: int | None = None, client_extra: dict | None = None, signer: SoftKey | None = None,
        extensions: bytes = b"",
    ) -> dict:
        """The security_key of a sign-in: a touch of the key. The counter goes up by one unless given."""
        if counter is None:
            self.counter += 1
            counter = self.counter
        auth = self.auth_data(rp_id, flags, counter, extensions=extensions)
        data = client_data(type_, challenge, origin, **(client_extra or {}))
        signature = (signer or self).sign(auth + hashlib.sha256(data).digest())
        return {
            "credential_id": self.id,
            "client_data_json": b64url(data),
            "authenticator_data": b64url(auth),
            "signature": b64url(signature),
        }
