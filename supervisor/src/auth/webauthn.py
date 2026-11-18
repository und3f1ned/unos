"""WebAuthn verification of UNOS's own (ADR-0409): the client data, the authenticator data, the
credential public key (COSE_Key, read by the bounded decoder of ``cbor``) and the signature, with
cryptography; the relying party ID of a host name; and the challenges, which live in RAM only.

Nothing here touches the database or the request: ``security_keys`` uses it for the registration
and the sign-in. Every error is a ``WebAuthnError``, whose callers answer with their own texts.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import ipaddress
import json
import re
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa

from . import cbor

# COSE algorithms (RFC 9053, RFC 8812), in the order of the registration's preference.
ES256 = -7
EDDSA = -8
RS256 = -257
ALGORITHMS = (ES256, EDDSA, RS256)

# Ceremonies, as the client data's type names them.
CREATE = "webauthn.create"
GET = "webauthn.get"

# How long the browser waits for the touch, and how long a challenge lives on the server: a little
# longer, for the way there and back.
TIMEOUT_MS = 60_000
CHALLENGE_SECONDS = 120
CHALLENGE_BYTES = 32
# The pending challenges: at most a few per user and ceremony, the oldest dropped first, and at most
# MAX_PENDING in all, so that RAM holds a bounded number whatever the requests.
MAX_PENDING_PER_USER = 4
MAX_PENDING = 256

# What a browser sends is small: clientDataJSON a few hundred bytes, a credential id at most 1023
# bytes (WebAuthn Level 3), an attestation object a few KB with a certificate chain.
MAX_CLIENT_DATA = 4096
MAX_CREDENTIAL_ID = 1023
MAX_ATTESTATION_OBJECT = 16384
MAX_AUTHENTICATOR_DATA = 1024
MAX_SIGNATURE = 1024
MAX_PUBLIC_KEY = 1024

# Authenticator data flags.
_UP, _UV, _AT, _ED = 0x01, 0x04, 0x40, 0x80

_B64URL = re.compile(r"[A-Za-z0-9_-]*={0,2}")
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


class WebAuthnError(ValueError):
    """What was sent is not a valid WebAuthn answer, key or name; the text says what, for the log
    of a test, never for the user."""


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(text: object, max_bytes: int) -> bytes:
    """Base64url, with or without padding; at most ``max_bytes`` bytes."""
    if not isinstance(text, str) or not _B64URL.fullmatch(text) or len(text) > (max_bytes * 4 + 2) // 3 + 2:
        raise WebAuthnError("not base64url")
    try:
        data = base64.urlsafe_b64decode(text.rstrip("=") + "=" * (-len(text.rstrip("=")) % 4))
    except (binascii.Error, ValueError):
        raise WebAuthnError("not base64url") from None
    if len(data) > max_bytes:
        raise WebAuthnError("too long")
    return data


# ==================== The relying party ====================


def rp_id_of_host(host: object) -> str:
    """The relying party ID of a request's Host header: its host name in lower case, without the
    port. An IP address is refused, as browsers refuse it: WebAuthn needs a name. So are a Host
    that is no valid DNS name and one whose last label is a number, which a browser reads as an
    IPv4 address (the URL Standard)."""
    if not isinstance(host, str) or not host or host.startswith("["):
        raise WebAuthnError("no host name")
    name, _, port = host.rpartition(":") if ":" in host else (host, "", "")
    if port and not port.isdigit():
        raise WebAuthnError("no host name")
    name = name.lower()
    try:
        ipaddress.ip_address(name)
    except ValueError:
        pass
    else:
        raise WebAuthnError("an IP address")
    labels = name.split(".")
    if len(name) > 253 or not all(_LABEL.fullmatch(label) for label in labels):
        raise WebAuthnError("no host name")
    last = labels[-1]
    if last.isdigit() or last.startswith("0x"):
        raise WebAuthnError("an IP address")
    return name


def origin_of_host(host: object) -> Optional[str]:
    """The web UI's origin at a request's Host, as from_own_origin of utils compares it."""
    return f"https://{host.lower()}" if isinstance(host, str) and host else None


# ==================== The challenges (RAM only) ====================


def _clock() -> float:
    """The challenges' time (tests replace it)."""
    return time.monotonic()


@dataclass(frozen=True)
class Pending:
    """A challenge that the server issued and that no answer used yet."""

    ceremony: str               # CREATE or GET
    user_id: int
    username: str               # ids are reused by SQLite: the name must match too
    origin: str                 # https://<the Host it was issued at>
    rp_id: str
    session_id: Optional[str]   # the session that asked for a registration; None at a sign-in
    expires: float


class Challenges:
    """The challenges waiting for their answer, in the order they were issued. Each is taken once:
    ``take`` removes it, whether its answer is then accepted or not. A lock makes each method one
    step: the requests run on the event loop, and a restore clears the challenges from its worker
    thread."""

    def __init__(self) -> None:
        self._pending: dict[bytes, Pending] = {}
        self._lock = threading.Lock()

    def issue(self, ceremony: str, user_id: int, username: str, origin: str, rp_id: str,
              session_id: Optional[str] = None) -> bytes:
        with self._lock:
            now = _clock()
            for challenge in [c for c, p in self._pending.items() if p.expires <= now]:
                del self._pending[challenge]
            same = [c for c, p in self._pending.items() if p.user_id == user_id and p.ceremony == ceremony]
            for challenge in same[:max(0, len(same) - MAX_PENDING_PER_USER + 1)]:
                del self._pending[challenge]
            while len(self._pending) >= MAX_PENDING:
                del self._pending[next(iter(self._pending))]
            challenge = secrets.token_bytes(CHALLENGE_BYTES)
            self._pending[challenge] = Pending(ceremony, user_id, username, origin, rp_id, session_id,
                                               now + CHALLENGE_SECONDS)
            return challenge

    def take(self, challenge: bytes) -> Optional[Pending]:
        """The pending challenge, removed; None when there is none or it expired."""
        with self._lock:
            pending = self._pending.pop(challenge, None)
        if pending is None or pending.expires <= _clock():
            return None
        return pending

    def forget_user(self, user_id: int) -> None:
        with self._lock:
            for challenge in [c for c, p in self._pending.items() if p.user_id == user_id]:
                del self._pending[challenge]

    def clear(self) -> None:
        with self._lock:
            self._pending.clear()


_challenges = Challenges()


def issue(ceremony: str, user_id: int, username: str, origin: str, rp_id: str,
          session_id: Optional[str] = None) -> bytes:
    return _challenges.issue(ceremony, user_id, username, origin, rp_id, session_id)


def take(challenge: bytes) -> Optional[Pending]:
    return _challenges.take(challenge)


def forget_user(user_id: int) -> None:
    """The user was deleted: SQLite may give its id to the next new user."""
    _challenges.forget_user(user_id)


def forget_challenges() -> None:
    """A restore replaced the database, whose ids may now name other users."""
    _challenges.clear()


# ==================== What the browser sends ====================


@dataclass(frozen=True)
class ClientData:
    type: str
    challenge: bytes
    origin: str


def parse_client_data(raw: bytes) -> ClientData:
    """clientDataJSON: a JSON object with the type, the challenge (base64url) and the origin, and no
    crossOrigin true (a frame of another origin). Other members, which browsers add, are ignored."""
    if len(raw) > MAX_CLIENT_DATA:
        raise WebAuthnError("client data too long")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise WebAuthnError("client data is not JSON") from None
    if not isinstance(data, dict):
        raise WebAuthnError("client data is not an object")
    kind, challenge, origin = data.get("type"), data.get("challenge"), data.get("origin")
    if not isinstance(kind, str) or not isinstance(origin, str):
        raise WebAuthnError("client data without type or origin")
    if data.get("crossOrigin", False) is not False:
        raise WebAuthnError("a cross-origin frame")
    return ClientData(kind, b64url_decode(challenge, CHALLENGE_BYTES), origin)


@dataclass(frozen=True)
class AuthenticatorData:
    rp_id_hash: bytes
    flags: int
    sign_count: int
    credential_id: Optional[bytes]      # with attested credential data (AT): a registration
    public_key: Optional[bytes]         # its COSE_Key, as the authenticator wrote it

    @property
    def user_present(self) -> bool:
        return bool(self.flags & _UP)


def parse_authenticator_data(raw: bytes) -> AuthenticatorData:
    """The authenticator data: the RP ID hash, the flags, the signature counter, the attested
    credential data when AT says so, the extensions (a CBOR map) when ED says so, and nothing after."""
    if not isinstance(raw, bytes) or len(raw) < 37 or len(raw) > MAX_ATTESTATION_OBJECT:
        raise WebAuthnError("authenticator data of a wrong size")
    rp_id_hash, flags, sign_count = raw[:32], raw[32], int.from_bytes(raw[33:37], "big")
    pos = 37
    credential_id = public_key = None
    if flags & _AT:
        if len(raw) < pos + 18:
            raise WebAuthnError("attested credential data cut short")
        size = int.from_bytes(raw[pos + 16:pos + 18], "big")
        pos += 18
        if size < 1 or size > MAX_CREDENTIAL_ID or len(raw) < pos + size:
            raise WebAuthnError("credential id of a wrong size")
        credential_id = raw[pos:pos + size]
        pos += size
        try:
            _, end = cbor.decode_prefix(raw[pos:], max_depth=2)
        except cbor.CborError as exc:
            raise WebAuthnError(f"credential public key: {exc}") from None
        if end > MAX_PUBLIC_KEY:
            raise WebAuthnError("credential public key too long")
        public_key = raw[pos:pos + end]
        pos += end
    if flags & _ED:
        try:
            extensions, end = cbor.decode_prefix(raw[pos:], max_depth=cbor.MAX_DEPTH)
        except cbor.CborError as exc:
            raise WebAuthnError(f"extensions: {exc}") from None
        if not isinstance(extensions, dict):
            raise WebAuthnError("extensions are not a map")
        pos += end
    if pos != len(raw):
        raise WebAuthnError("bytes after the authenticator data")
    return AuthenticatorData(rp_id_hash, flags, sign_count, credential_id, public_key)


def parse_attestation_object(raw: bytes) -> AuthenticatorData:
    """The attestation object of a registration: a map of fmt (a text), attStmt (a map) and authData,
    whose attested credential data it answers. The statement is read by the bounded decoder and not
    checked: UNOS asks for attestation none and trusts no attestation (ADR-0409)."""
    if len(raw) > MAX_ATTESTATION_OBJECT:
        raise WebAuthnError("attestation object too long")
    try:
        value = cbor.decode(raw)
    except cbor.CborError as exc:
        raise WebAuthnError(f"attestation object: {exc}") from None
    if not isinstance(value, dict) or set(value) != {"fmt", "attStmt", "authData"}:
        raise WebAuthnError("attestation object without fmt, attStmt and authData")
    fmt, statement, auth = value["fmt"], value["attStmt"], value["authData"]
    if not isinstance(fmt, str) or not 0 < len(fmt) <= 32 or not isinstance(statement, dict) or not isinstance(auth, bytes):
        raise WebAuthnError("attestation object of a wrong shape")
    parsed = parse_authenticator_data(auth)
    if parsed.credential_id is None:
        raise WebAuthnError("no attested credential data")
    return parsed


# ==================== Public keys and signatures ====================


def _load(cose: bytes):
    """The algorithm and the public key of a COSE_Key: ES256 on P-256 (EC2), EdDSA on Ed25519 (OKP),
    or RS256 with a modulus of 2048 to 4096 bits (RSA). Anything else is refused."""
    if not isinstance(cose, bytes) or len(cose) > MAX_PUBLIC_KEY:
        raise WebAuthnError("public key of a wrong size")
    try:
        key = cbor.decode(cose, max_depth=1)
    except cbor.CborError as exc:
        raise WebAuthnError(f"public key: {exc}") from None
    if not isinstance(key, dict):
        raise WebAuthnError("public key is not a map")
    kty, alg = key.get(1), key.get(3)
    try:
        if kty == 2 and alg == ES256:
            x, y = key.get(-2), key.get(-3)
            if key.get(-1) != 1 or not isinstance(x, bytes) or not isinstance(y, bytes) or len(x) != 32 or len(y) != 32:
                raise WebAuthnError("not a P-256 key")
            return alg, ec.EllipticCurvePublicNumbers(
                int.from_bytes(x, "big"), int.from_bytes(y, "big"), ec.SECP256R1()).public_key()
        if kty == 1 and alg == EDDSA:
            x = key.get(-2)
            if key.get(-1) != 6 or not isinstance(x, bytes) or len(x) != 32:
                raise WebAuthnError("not an Ed25519 key")
            return alg, ed25519.Ed25519PublicKey.from_public_bytes(x)
        if kty == 3 and alg == RS256:
            n, e = key.get(-1), key.get(-2)
            if not isinstance(n, bytes) or not isinstance(e, bytes) or not 1 <= len(e) <= 4:
                raise WebAuthnError("not an RSA key")
            modulus = int.from_bytes(n, "big")
            if not 2048 <= modulus.bit_length() <= 4096:
                raise WebAuthnError("RSA key of 2048 to 4096 bits only")
            return alg, rsa.RSAPublicNumbers(int.from_bytes(e, "big"), modulus).public_key()
    except (ValueError, TypeError) as exc:  # a point off the curve, an exponent cryptography refuses
        if isinstance(exc, WebAuthnError):
            raise
        raise WebAuthnError("public key that cryptography refuses") from None
    raise WebAuthnError("key type or algorithm not offered")


def public_key_algorithm(cose: bytes) -> int:
    """The algorithm of a COSE_Key that UNOS can verify; WebAuthnError for any other."""
    return _load(cose)[0]


def verify_signature(cose: bytes, alg: int, signed: bytes, signature: bytes) -> bool:
    """Whether ``signature`` of ``signed`` is the key's, with the algorithm stored beside it. A key
    or an algorithm that cannot be read verifies nothing."""
    try:
        loaded_alg, key = _load(cose)
    except WebAuthnError:
        return False
    if loaded_alg != alg or not isinstance(signature, bytes) or len(signature) > MAX_SIGNATURE:
        return False
    try:
        if alg == ES256:
            key.verify(signature, signed, ec.ECDSA(hashes.SHA256()))
        elif alg == EDDSA:
            key.verify(signature, signed)
        else:
            key.verify(signature, signed, padding.PKCS1v15(), hashes.SHA256())
    except (InvalidSignature, ValueError):
        return False
    return True


def rp_id_hash(rp_id: str) -> bytes:
    return hashlib.sha256(rp_id.encode("utf-8")).digest()
