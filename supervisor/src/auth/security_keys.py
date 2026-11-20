"""Security keys (WebAuthn) as a second factor beside the authenticator app (ADR-0409).

A user with two-factor authentication registers a key on the Account page: POST
/auth/security-keys/register/begin, with the current password, answers the options of the browser's
navigator.credentials.create, and /register/finish checks the key's answer and stores the key, at
most MAX_KEYS_PER_USER of them. GET /auth/security-keys lists them, and DELETE
/auth/security-keys/{id}, with the current password, removes one. At the code step of the sign-in
(``code_step_offer`` and ``verify_sign_in``, which POST /auth/login calls), a key's answer opens the
session in place of the app's code.

A key works at the web UI's address where it was registered, which its row keeps (``origin`` and
``rp_id``): the expected values of a sign-in come from the row, never from the request. The rows of
a restored database are not trusted (ADR-0144): every column is read as stored (``_stored_keys``),
and a value that cannot be read leaves the key listed and removable but never signing in.
"""
from __future__ import annotations

import asyncio
import hashlib
import secrets
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import LargeBinary, cast, delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit.router import log_action
from ..audit.schemas import AuditAction, AuditSeverity
from ..database import get_db
from ..openapi import BODY, RATE_LIMITED, SIGNED_IN, answer, errors
from ..times import UtcDatetime, db_now, parse_time
from . import webauthn
from .limits import limiter
from .models import SecurityKey, User
from .schemas import SecurityKeyAssertion, SecurityKeyRequest
from .utils import Credential, credential, from_own_origin, require_session, session_id_of, verify_password, verify_token

router = APIRouter(prefix="/security-keys", tags=["auth"])

MAX_KEYS_PER_USER = 10
NAME_CHARS = 64
RP_NAME = "UNOS"
USER_HANDLE_BYTES = 16
# The signature counter is 32 bits wide.
MAX_SIGN_COUNT = 2**32 - 1

WRONG_PASSWORD = "Current password is incorrect"
NEEDS_TWO_FACTOR = (
    "Turn on two-factor authentication with an authenticator app first: a security key is an alternative "
    "to its codes"
)
NEEDS_HOST_NAME = "Security keys work only when the web UI is opened by the NAS's name, not by an IP address"
FROM_OWN_PAGE = "A security key is registered only from the web UI's own page"
TOO_MANY_KEYS = (
    f"You already have {MAX_KEYS_PER_USER} security keys, the most an account can have: remove one first"
)
NAME_TAKEN = "You already have a security key with this name"
ALREADY_REGISTERED = "This security key is already registered"
REQUEST_EXPIRED = "The security key request expired or was already used: try again"
NOT_ACCEPTED = "The security key's answer was not accepted"
KEY_NOT_FOUND = "Security key not found"
# At the sign-in: the answers, and the login history's failure reasons.
SIGN_IN_INVALID = "Invalid security key"
SIGN_IN_CLONED = (
    "This security key's signature counter went backwards, so it may have been copied: sign in another way "
    "and remove it"
)


# ==================== Requests and answers ====================


def _printable(name: str) -> str:
    """A key's name is shown on the Account page and in the audit log: no control or format
    characters (line breaks, direction marks)."""
    if any(unicodedata.category(c) in ("Cc", "Cf") for c in name):
        raise ValueError("A security key's name must not contain control characters")
    return name


KeyName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=NAME_CHARS), AfterValidator(_printable)
]


class PasswordBody(BaseModel):
    """The signed-in user's current password, which a change of their security keys needs."""

    model_config = ConfigDict(
        extra="forbid", json_schema_extra={"examples": [{"current_password": "example-password"}]},
    )

    current_password: str = Field(max_length=1024, description="The user's current password.")


class RegistrationFinish(BaseModel):
    """The browser's answer to navigator.credentials.create, each binary field base64url."""

    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [{
        "name": "YubiKey 5C", "credential_id": "example-credential-id",
        "client_data_json": "example-client-data-json", "attestation_object": "example-attestation-object",
    }]})

    name: KeyName = Field(
        description="A name for the key, unique among the user's keys: 1 to 64 characters without control "
                    "characters; white space around it is dropped."
    )
    credential_id: str = Field(min_length=1, max_length=1364, description="The key's credential id, base64url.")
    client_data_json: str = Field(
        min_length=1, max_length=6000, description="The browser's client data JSON, base64url."
    )
    attestation_object: str = Field(
        min_length=1, max_length=22000, description="The key's attestation object, base64url."
    )


class SecurityKeyCreation(BaseModel):
    """The options of navigator.credentials.create, as the page builds them (base64url for bytes)."""

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "challenge": "example-challenge", "rp_id": "nas.example.com", "rp_name": "UNOS",
        "user_id": "example-user-handle", "user_name": "alice", "algorithms": [-7, -8, -257],
        "exclude_credentials": ["example-credential-id"], "timeout_ms": 60000, "attestation": "none",
        "user_verification": "discouraged", "resident_key": "discouraged",
    }]})

    challenge: str = Field(
        description="The challenge, base64url: valid once, for 2 minutes, for this user, session and address."
    )
    rp_id: str = Field(description="The relying party ID: the host name of the web UI's address, without the port.")
    rp_name: str = Field(description="The relying party's name: UNOS.")
    user_id: str = Field(description="A random user handle, base64url, that names no one.")
    user_name: str = Field(description="The user's name.")
    algorithms: list[int] = Field(
        description="The COSE algorithms that UNOS takes, by preference: -7 (ES256), -8 (EdDSA), -257 (RS256)."
    )
    exclude_credentials: list[str] = Field(
        description="The credential ids of the user's keys, base64url, which the browser does not register again."
    )
    timeout_ms: int = Field(description="How long the browser waits for the key, in milliseconds.")
    attestation: Literal["none"] = Field("none", description="Always none: UNOS asks for no attestation.")
    user_verification: Literal["discouraged"] = Field(
        "discouraged", description="Always discouraged: the key is a second factor beside the password."
    )
    resident_key: Literal["discouraged"] = Field(
        "discouraged", description="Always discouraged: the key need not keep a discoverable credential."
    )


class SecurityKeyInfo(BaseModel):
    """One of the user's security keys, without its key material."""

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "id": 1, "name": "YubiKey 5C", "origin": "https://nas.example.com",
        "created_at": "2026-11-18T10:02:41.512345Z", "last_used_at": "2026-11-19T08:15:03.004211Z",
        "usable": True,
    }]})

    id: int = Field(description="The key's id.")
    name: str = Field(description="The key's name; ? when the stored one cannot be read.")
    # The web UI's address where the key works; null when the stored one cannot be read.
    origin: str | None = Field(
        None, description="The web UI's address where the key works; null when the stored one cannot be read."
    )
    created_at: UtcDatetime | None = Field(
        None, description="When the key was added, in UTC; null when the stored time cannot be read."
    )
    last_used_at: UtcDatetime | None = Field(
        None, description="When the key last signed in, in UTC; null: never, or a stored time that cannot be read."
    )
    # False for a key whose stored row cannot be read (a restored or damaged database): it signs in
    # nowhere and can only be removed.
    usable: bool = Field(
        True,
        description="False for a key whose stored row cannot be read (a restored or damaged database): it signs "
                    "in nowhere and can only be removed.",
    )


# ==================== The stored keys ====================


@dataclass(frozen=True)
class StoredKey:
    """A row of security_keys as read; None for a value that cannot be read."""

    id: int
    name: str
    credential_id: str | None
    public_key: bytes | None
    algorithm: int | None
    sign_count: int | None
    rp_id: str | None
    origin: str | None
    created_at: datetime | None
    last_used_at: datetime | None

    @property
    def usable(self) -> bool:
        if None in (self.credential_id, self.public_key, self.algorithm, self.sign_count, self.rp_id, self.origin):
            return False
        try:
            return webauthn.public_key_algorithm(self.public_key) == self.algorithm
        except webauthn.WebAuthnError:
            return False

    def info(self) -> SecurityKeyInfo:
        return SecurityKeyInfo(id=self.id, name=self.name, origin=self.origin, created_at=self.created_at,
                               last_used_at=self.last_used_at, usable=self.usable)


def _text(raw: object) -> str | None:
    if not isinstance(raw, bytes):
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _integer(value: object, top: int) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= top else None


def _time(raw: object) -> datetime | None:
    text = _text(raw)
    try:
        return parse_time(text) if text else None
    except ValueError:
        return None


def _credential_id(raw: object) -> str | None:
    """The stored credential id when it is base64url of 1 to 1023 bytes, as the registration
    stores it."""
    text = _text(raw)
    try:
        decoded = webauthn.b64url_decode(text, webauthn.MAX_CREDENTIAL_ID)
    except webauthn.WebAuthnError:
        return None
    return text if decoded and webauthn.b64url_encode(decoded) == text else None


def _origin(raw: object) -> str | None:
    text = _text(raw)
    return text if text and text.startswith("https://") and len(text) <= 300 else None


def _rp_id(raw: object) -> str | None:
    text = _text(raw)
    try:
        return webauthn.rp_id_of_host(text) if text else None
    except webauthn.WebAuthnError:
        return None


def _stored_query():
    """Every column as stored: texts and bytes cast to BLOB, so that no value of a restored
    database makes the driver raise; the integers as SQLite holds them."""
    k = SecurityKey
    return select(
        k.id, cast(k.name, LargeBinary), cast(k.credential_id, LargeBinary), cast(k.public_key, LargeBinary),
        k.algorithm, k.sign_count, cast(k.rp_id, LargeBinary), cast(k.origin, LargeBinary),
        cast(k.created_at, LargeBinary), cast(k.last_used_at, LargeBinary),
    ).order_by(k.id)


def _stored(row) -> StoredKey:
    key_id, name, cred, public_key, alg, count, rp_id, origin, created, used = row
    algorithm = alg if isinstance(alg, int) and not isinstance(alg, bool) and alg in webauthn.ALGORITHMS else None
    return StoredKey(
        id=key_id, name=_text(name) or "?", credential_id=_credential_id(cred),
        public_key=public_key if isinstance(public_key, bytes) else None, algorithm=algorithm,
        sign_count=_integer(count, MAX_SIGN_COUNT), rp_id=_rp_id(rp_id), origin=_origin(origin),
        created_at=_time(created), last_used_at=_time(used),
    )


async def _stored_keys(db: AsyncSession, user_id: int) -> list[StoredKey]:
    rows = await db.execute(_stored_query().where(SecurityKey.user_id == user_id))
    return [_stored(row) for row in rows.all()]


async def delete_keys_of(db: AsyncSession, user_id: int) -> int:
    """Delete every key of the user in the caller's transaction; how many there were."""
    result = await db.execute(delete(SecurityKey).where(SecurityKey.user_id == user_id))
    return result.rowcount or 0


def keys_text(count: int) -> str:
    """The keys that went with two-factor authentication, for its audit entry."""
    return f"{count} security key" if count == 1 else f"{count} security keys"


async def _audit(request: Request, action: AuditAction, username: str, details: str,
                 severity: AuditSeverity = AuditSeverity.INFO) -> None:
    """After the commit, by the user about themselves (ADR-0343); an entry that cannot be written is
    in the supervisor's log, and the request still answers what it did (ADR-0251)."""
    try:
        await log_action(action=action, request=request, username=username, resource_type="security_key",
                         resource_id=username, details=details, severity=severity)
    except OSError:
        pass  # log_action reported the entry


# ==================== Registration ====================

# The 403 of the routes that change the keys (require_session), in the API documentation.
_SESSION_ONLY = (
    "an API token (only a session token adds or removes security keys), a session cookie from another page "
    "than the web UI's, or an account that must set up two-factor authentication first"
)
_REGISTRATION_ERRORS = {
    403: f"The request did not come from the web UI's own page (its Origin), or the credential does not reach "
         f"this route: {_SESSION_ONLY}.",
    409: "The web UI is open at an IP address, not by the NAS's name; the user has no two-factor authentication "
         "with an authenticator app; or they have 10 security keys already.",
}


def _registration_address(request: Request) -> tuple[str, str]:
    """The origin and the RP ID of a registration: the web UI's own page (its Origin is
    https://<Host>, as the session cookie needs it, ADR-0397) at a host name."""
    if not from_own_origin(request, origin_required=True):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=FROM_OWN_PAGE)
    host = request.headers.get("host")
    try:
        rp_id = webauthn.rp_id_of_host(host)
    except webauthn.WebAuthnError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=NEEDS_HOST_NAME) from None
    return webauthn.origin_of_host(host), rp_id


@router.get(
    "", response_model=list[SecurityKeyInfo], summary="List your security keys", responses=errors(SIGNED_IN),
)
async def list_security_keys(user: User = Depends(verify_token), db: AsyncSession = Depends(get_db)):
    """The signed-in user's security keys, oldest first; no key material. Any signed-in user."""
    return [key.info() for key in await _stored_keys(db, user.id)]


@router.post(
    "/register/begin",
    response_model=SecurityKeyCreation,
    summary="Start adding a security key",
    responses=errors(SIGNED_IN, BODY, RATE_LIMITED, _REGISTRATION_ERRORS, {
        400: "The current password is wrong.",
    }),
)
@limiter.limit("5/minute")
async def begin_registration(
    request: Request,
    body: PasswordBody,
    found: Credential = Depends(credential),
    user: User = Depends(require_session),
    db: AsyncSession = Depends(get_db),
):
    """Start adding a security key, with the current password: the options for the browser's
    navigator.credentials.create, with a challenge bound to the user, this session and the web UI's
    address, by which the key then works. Needs two-factor authentication with an authenticator app, the
    web UI open by the NAS's name, and a session token from the web UI's own page. Any signed-in user.
    """
    origin, rp_id = _registration_address(request)
    if not user.totp_enabled:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=NEEDS_TWO_FACTOR)
    keys = await _stored_keys(db, user.id)
    if len(keys) >= MAX_KEYS_PER_USER:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=TOO_MANY_KEYS)
    user_id, username, hashed = user.id, user.username, user.hashed_password
    if not await asyncio.to_thread(verify_password, body.current_password, hashed):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=WRONG_PASSWORD)
    challenge = webauthn.issue(webauthn.CREATE, user_id, username, origin, rp_id, session_id_of(found.token))
    return SecurityKeyCreation(
        challenge=webauthn.b64url_encode(challenge),
        rp_id=rp_id,
        rp_name=RP_NAME,
        # A random user handle: it names no one, and the keys are found by their credential ids.
        user_id=webauthn.b64url_encode(secrets.token_bytes(USER_HANDLE_BYTES)),
        user_name=username,
        algorithms=list(webauthn.ALGORITHMS),
        exclude_credentials=[key.credential_id for key in keys if key.credential_id is not None],
        timeout_ms=webauthn.TIMEOUT_MS,
    )


def _refused(detail: str = NOT_ACCEPTED, code: int = status.HTTP_400_BAD_REQUEST) -> HTTPException:
    return HTTPException(status_code=code, detail=detail)


@router.post(
    "/register/finish",
    response_model=SecurityKeyInfo,
    summary="Finish adding a security key",
    responses=errors(SIGNED_IN, BODY, _REGISTRATION_ERRORS, {
        400: "The key's answer was not accepted, or its challenge expired, was used already or is another "
             "session's.",
        409: "The web UI is open at an IP address; the user no longer has two-factor authentication; the key is "
             "registered already; the user has 10 security keys already; or another of their keys has this name.",
    }),
)
async def finish_registration(
    request: Request,
    body: RegistrationFinish,
    found: Credential = Depends(credential),
    user: User = Depends(require_session),
    db: AsyncSession = Depends(get_db),
):
    """Check the key's answer to the challenge of POST /auth/security-keys/register/begin and store the
    key, for the web UI's address of the registration; written to the audit log. Any signed-in user."""
    origin, rp_id = _registration_address(request)
    user_id, username = user.id, user.username
    try:
        raw_client_data = webauthn.b64url_decode(body.client_data_json, webauthn.MAX_CLIENT_DATA)
        client = webauthn.parse_client_data(raw_client_data)
    except webauthn.WebAuthnError:
        raise _refused() from None
    # Taken, and so used up, whatever follows.
    pending = webauthn.take(client.challenge)
    if (pending is None or pending.ceremony != webauthn.CREATE or pending.user_id != user_id
            or pending.username != username or pending.session_id != session_id_of(found.token)):
        raise _refused(REQUEST_EXPIRED)
    if client.type != webauthn.CREATE or client.origin != pending.origin or pending.origin != origin:
        raise _refused()
    try:
        credential_id = webauthn.b64url_decode(body.credential_id, webauthn.MAX_CREDENTIAL_ID)
        auth = webauthn.parse_attestation_object(
            webauthn.b64url_decode(body.attestation_object, webauthn.MAX_ATTESTATION_OBJECT))
        algorithm = webauthn.public_key_algorithm(auth.public_key)
    except webauthn.WebAuthnError:
        raise _refused() from None
    if (auth.rp_id_hash != webauthn.rp_id_hash(pending.rp_id) or pending.rp_id != rp_id or not auth.user_present
            or auth.credential_id != credential_id):
        raise _refused()

    stored_id = webauthn.b64url_encode(credential_id)
    row = SecurityKey(
        user_id=user_id, name=body.name, credential_id=stored_id, public_key=auth.public_key, algorithm=algorithm,
        sign_count=auth.sign_count, rp_id=pending.rp_id, origin=pending.origin, created_at=db_now(),
    )
    db.add(row)
    try:
        # The flush takes SQLite's write lock: the checks below see every other registration, and
        # turning two-factor authentication off, which deletes the keys, comes before or after.
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise _refused(ALREADY_REGISTERED, status.HTTP_409_CONFLICT) from None
    problem = None
    if not await db.scalar(select(User.totp_enabled).where(User.id == user_id, User.username == username)):
        problem = NEEDS_TWO_FACTOR
    elif await db.scalar(select(func.count()).select_from(SecurityKey).where(SecurityKey.credential_id == stored_id)) > 1:
        problem = ALREADY_REGISTERED
    elif await db.scalar(select(func.count()).select_from(SecurityKey).where(SecurityKey.user_id == user_id)) > MAX_KEYS_PER_USER:
        problem = TOO_MANY_KEYS
    elif await db.scalar(select(func.count()).select_from(SecurityKey).where(
            SecurityKey.user_id == user_id, SecurityKey.name == body.name)) > 1:
        problem = NAME_TAKEN
    if problem is not None:
        await db.rollback()
        raise _refused(problem, status.HTTP_409_CONFLICT)
    # Answered from what was stored, not read again: another request may remove it at once.
    added = SecurityKeyInfo(id=row.id, name=body.name, origin=pending.origin, created_at=row.created_at)
    await db.commit()

    await _audit(request, AuditAction.SECURITY_KEY_ADDED, username,
                 f"Security key '{body.name}' added, for {pending.origin}")
    return added


@router.delete(
    "/{key_id}",
    summary="Remove a security key",
    responses={
        **answer({"status": "ok"}, "The key is removed."),
        **errors(SIGNED_IN, BODY, RATE_LIMITED, {
            400: "The current password is wrong.",
            403: f"The credential does not reach this route: {_SESSION_ONLY}.",
            404: "The user has no security key with this id.",
        }),
    },
)
@limiter.limit("5/minute")
async def delete_security_key(
    request: Request,
    key_id: Annotated[int, Path(description="The key's id, as GET /auth/security-keys lists it.")],
    body: PasswordBody,
    user: User = Depends(require_session),
    db: AsyncSession = Depends(get_db),
):
    """Remove one of the signed-in user's security keys, with the current password; written to the audit
    log. Any signed-in user."""
    user_id, username, hashed = user.id, user.username, user.hashed_password
    if not await asyncio.to_thread(verify_password, body.current_password, hashed):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=WRONG_PASSWORD)
    found = [key for key in await _stored_keys(db, user_id) if key.id == key_id]
    if not found:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=KEY_NOT_FOUND)
    result = await db.execute(delete(SecurityKey).where(SecurityKey.id == key_id, SecurityKey.user_id == user_id))
    await db.commit()
    if result.rowcount != 1:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=KEY_NOT_FOUND)
    removed = found[0]
    details = f"Security key '{removed.name}' removed"
    await _audit(request, AuditAction.SECURITY_KEY_REMOVED, username,
                 f"{details}, for {removed.origin}" if removed.origin else details, AuditSeverity.WARNING)
    return {"status": "ok"}


# ==================== The sign-in ====================


async def code_step_offer(
    db: AsyncSession, user: User, request: Request,
) -> tuple[SecurityKeyRequest | None, list[str] | None]:
    """At the code step of an account with two-factor authentication: a challenge for its keys that
    work at the address of the request, if any, and the addresses where its keys work (None without
    keys). A challenge is issued only for keys of this address, after the password."""
    keys = [key for key in await _stored_keys(db, user.id) if key.usable]
    origins = sorted({key.origin for key in keys}) or None
    host = request.headers.get("host")
    try:
        rp_id = webauthn.rp_id_of_host(host)
    except webauthn.WebAuthnError:
        return None, origins
    origin = webauthn.origin_of_host(host)
    here = [key for key in keys if key.origin == origin and key.rp_id == rp_id]
    if not here:
        return None, origins
    challenge = webauthn.issue(webauthn.GET, user.id, user.username, origin, rp_id)
    return SecurityKeyRequest(
        challenge=webauthn.b64url_encode(challenge), rp_id=rp_id,
        credential_ids=[key.credential_id for key in here], timeout_ms=webauthn.TIMEOUT_MS,
    ), origins


class KeyRefused(Exception):
    """A key's answer at the sign-in that does not open the session."""

    def __init__(self, detail: str, reason: str, audit: str | None = None) -> None:
        super().__init__(detail)
        self.detail = detail          # the answer's
        self.reason = reason          # the login history's
        self.audit = audit            # the audit log's, for a key that may be copied; else reason


def _invalid() -> KeyRefused:
    return KeyRefused(SIGN_IN_INVALID, "Invalid security key")


async def verify_sign_in(db: AsyncSession, user: User, answer: SecurityKeyAssertion) -> str:
    """Check a key's answer at the code step of ``user``, whose password was checked, and record the
    key's signature counter and last use in the caller's transaction, which the sign-in commits.
    The key's name; KeyRefused otherwise."""
    user_id, username = user.id, user.username
    try:
        raw_client_data = webauthn.b64url_decode(answer.client_data_json, webauthn.MAX_CLIENT_DATA)
        client = webauthn.parse_client_data(raw_client_data)
    except webauthn.WebAuthnError:
        raise _invalid() from None
    pending = webauthn.take(client.challenge)
    if pending is None or pending.ceremony != webauthn.GET or pending.user_id != user_id or pending.username != username:
        raise KeyRefused(REQUEST_EXPIRED, "Expired security key request")
    try:
        wanted = webauthn.b64url_encode(webauthn.b64url_decode(answer.credential_id, webauthn.MAX_CREDENTIAL_ID))
        raw_auth = webauthn.b64url_decode(answer.authenticator_data, webauthn.MAX_AUTHENTICATOR_DATA)
        signature = webauthn.b64url_decode(answer.signature, webauthn.MAX_SIGNATURE)
        auth = webauthn.parse_authenticator_data(raw_auth)
    except webauthn.WebAuthnError:
        raise _invalid() from None
    matches = [key for key in await _stored_keys(db, user_id) if key.credential_id == wanted and key.usable]
    if len(matches) != 1:
        raise _invalid()
    [key] = matches
    if (client.type != webauthn.GET or client.origin != key.origin or pending.origin != key.origin
            or pending.rp_id != key.rp_id or auth.rp_id_hash != webauthn.rp_id_hash(key.rp_id)
            or not auth.user_present or auth.credential_id is not None):
        raise _invalid()
    signed = raw_auth + hashlib.sha256(raw_client_data).digest()
    if not webauthn.verify_signature(key.public_key, key.algorithm, signed, signature):
        raise _invalid()
    if (auth.sign_count or key.sign_count) and auth.sign_count <= key.sign_count:
        raise KeyRefused(
            SIGN_IN_CLONED, "Security key counter went backwards",
            f"Security key '{key.name}' refused: its signature counter went backwards, so it may have been copied",
        )
    result = await db.execute(
        update(SecurityKey)
        .where(SecurityKey.id == key.id, SecurityKey.user_id == user_id, SecurityKey.sign_count == key.sign_count)
        .values(sign_count=auth.sign_count, last_used_at=db_now())
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:  # another sign-in with this key at the same moment
        await db.rollback()
        raise _invalid()
    return key.name
