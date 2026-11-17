import enum
import hashlib
import json
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import NamedTuple, Optional

import bcrypt
import jwt
import pyotp
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import delete, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..database import get_db
from . import requirement
from .models import APIToken, User, UserSession
from .schemas import MAX_PASSWORD_BYTES

logger = logging.getLogger(__name__)
security = HTTPBearer()

# API Token prefix for identification
API_TOKEN_PREFIX = "unos_"

BCRYPT_ROUNDS = 12


def hash_password(password: str) -> str:
    secret = password.encode("utf-8")
    if len(secret) > MAX_PASSWORD_BYTES:
        # The request schemas reject these with a 422 before we get here.
        raise ValueError(f"Password must not be longer than {MAX_PASSWORD_BYTES} bytes")
    return bcrypt.hashpw(secret, bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode("ascii")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    # Checked as given (ADR-0315): no password longer than bcrypt's 72 bytes is
    # ever hashed (the request schemas refuse it), so such a password matches no
    # hash, also not the one of its first 72 bytes.
    secret = plain_password.encode("utf-8")
    if len(secret) > MAX_PASSWORD_BYTES:
        return False
    try:
        return bcrypt.checkpw(secret, hashed_password.encode("ascii"))
    except (TypeError, ValueError):
        # Malformed or unsupported hash in the database.
        return False


def create_access_token(data: dict) -> str:
    """A JWT for ``data`` with an expiry, and a new jti unless ``data`` holds one. It is a session only
    with its row (start_session): tests make the row themselves."""
    settings = get_settings()
    to_encode = {"jti": secrets.token_urlsafe(24), **data}
    to_encode["exp"] = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    return jwt.encode(to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def start_session(db: AsyncSession, user: User) -> str:
    """A new session token of ``user``: a JWT with a jti, and the session's row added to ``db``,
    which the sign-in's commit stores (ADR-0329). Rows of expired sessions go in the same commit."""
    settings = get_settings()
    session_id = secrets.token_urlsafe(24)
    expires = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    db.add(UserSession(id=session_id, user_id=user.id, expires_at=expires.replace(tzinfo=None)))
    return jwt.encode(
        {"sub": user.username, "jti": session_id, "exp": expires},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


# Sessions whose row the sign-in's commit could not store, as on a full /data (ADR-0251): the
# session id and its user. Valid in this process until the token expires, the session ends or the
# supervisor restarts (ADR-0329).
_unstored_sessions: dict[str, int] = {}


async def keep_unstored_session(db: AsyncSession, token: str, user_id: int) -> None:
    """After a sign-in's commit: keep the session of ``token`` in memory when its row is not stored."""
    session_id = decode_access_token(token)[1]
    if await db.get(UserSession, session_id) is None:
        _unstored_sessions[session_id] = user_id


async def _delete_sessions(db: AsyncSession, query) -> int:
    """Mark the session rows that ``query`` selects as deleted, without flushing what ``db`` holds:
    the DELETEs run in the caller's commit, inside its error handling (ADR-0329). Their number."""
    with db.no_autoflush:
        rows = (await db.scalars(query)).all()
    for row in rows:
        await db.delete(row)
    return len(rows)


async def purge_expired_sessions(db: AsyncSession) -> None:
    """Delete the rows of the sessions whose tokens have expired, in the caller's commit."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    await _delete_sessions(db, select(UserSession).where(UserSession.expires_at < now))


async def end_sessions(db: AsyncSession, user_id: int, *, keep: Optional[str] = None) -> int:
    """End the sessions of a user, but the one ``keep`` names: their rows go in the caller's
    commit (ADR-0329). The number of stored sessions ended."""
    query = select(UserSession).where(UserSession.user_id == user_id)
    if keep is not None:
        query = query.where(UserSession.id != keep)
    for session_id in [s for s, owner in _unstored_sessions.items() if owner == user_id and s != keep]:
        del _unstored_sessions[session_id]
    return await _delete_sessions(db, query)


def forget_unstored_sessions() -> None:
    """A restore replaced the database: the sessions kept in memory end with the stored ones."""
    _unstored_sessions.clear()


async def end_session(db: AsyncSession, session_id: str) -> bool:
    """Delete one session, stored or kept in memory; the caller commits. Whether there was one
    to end: a sign-out of the same session that came first leaves none (ADR-0343)."""
    result = await db.execute(delete(UserSession).where(UserSession.id == session_id))
    kept = _unstored_sessions.pop(session_id, None) is not None
    return bool(result.rowcount) or kept


def decode_access_token(token: str) -> tuple[str, str]:
    """Return the username and the session id of a valid, unexpired access token.

    Raises ``jwt.PyJWTError`` for anything else (bad signature, expired,
    missing ``exp``/``sub``/``jti`` claim, other algorithm).
    """
    settings = get_settings()
    payload = jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[settings.jwt_algorithm],
        options={"require": ["exp", "sub", "jti"]},
    )
    username, session_id = payload["sub"], payload["jti"]
    if not isinstance(username, str) or not username:
        raise jwt.InvalidTokenError("Token subject must be a non-empty string")
    if not isinstance(session_id, str) or not session_id:
        raise jwt.InvalidTokenError("Token id must be a non-empty string")
    return username, session_id


def session_id_of(token: str) -> Optional[str]:
    """The session id of a session token that ``authenticate_token`` accepted; None for an API token.
    Its expiry is not checked again: a request that began with a valid token ends as it began."""
    if token.startswith(API_TOKEN_PREFIX):
        return None
    settings = get_settings()
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm],
                      options={"verify_exp": False})["jti"]


class CallerSession(NamedTuple):
    """The session of a request, kept across a restore of the database (ADR-0329)."""

    id: str
    user_id: int
    username: str
    expires_at: datetime  # UTC, without a zone, as user_sessions keeps it


def caller_session(request: Request, user: User) -> Optional[CallerSession]:
    """The session of the request that ``authenticate_token`` accepted for ``user``; None for an
    API token or a request without a Bearer token."""
    scheme, _, token = (request.headers.get("Authorization") or "").partition(" ")
    if scheme.lower() != "bearer" or not token or token.startswith(API_TOKEN_PREFIX):
        return None
    settings = get_settings()
    claims = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm],
                        options={"verify_exp": False})
    expires_at = datetime.fromtimestamp(claims["exp"], timezone.utc).replace(tzinfo=None)
    return CallerSession(claims["jti"], user.id, user.username, expires_at)


def client_ip(request: Request | None) -> str | None:
    """Address of the client that sent ``request``.

    Uvicorn runs with ``--proxy-headers`` and trusts the X-Forwarded-For value
    set by Caddy (see supervisor/Dockerfile), so ``request.client`` already is
    the browser's address, not the proxy's. Do not read X-Forwarded-For here:
    without the proxy in front, that header is whatever the caller wants.
    """
    if request is None or request.client is None:
        return None
    return request.client.host


# ==================== Two-factor codes (TOTP) ====================

TOTP_CODE_REUSED = "This two-factor code was already used; wait for the next one"

# The time step of the last TOTP code each user had accepted, by user id: a code is
# accepted once (RFC 6238, section 5.2), wherever it is checked. Memory only (the
# supervisor is one process): after a restart, a code of the step that was current
# before can be accepted once more.
_last_totp_steps: dict[int, int] = {}


class TotpCheck(enum.Enum):
    OK = "ok"
    INVALID = "invalid"     # wrong code, or no usable secret
    REUSED = "reused"       # right code, but its time step was already used


def _totp_time() -> datetime:
    """The time TOTP codes are checked at (tests replace it)."""
    return datetime.now(timezone.utc)


def check_totp_code(user_id: int, secret: object, code: object) -> TotpCheck:
    """Check a TOTP code of user ``user_id`` and use it up.

    Only the current time step is valid (valid_window 0), and only once: a code
    whose step is not newer than the last step this user had accepted is REUSED.
    Synchronous, without an await between the check and the record, so two
    requests on the event loop never both accept one code.
    """
    if not isinstance(secret, str) or not secret or not isinstance(code, str) or not code:
        return TotpCheck.INVALID
    now = _totp_time()
    try:
        totp = pyotp.TOTP(secret)
        valid = totp.verify(code, for_time=now)
        step = totp.timecode(now)
    except (TypeError, ValueError):  # binascii.Error (a secret that is not base32) is a ValueError
        return TotpCheck.INVALID
    if not valid:
        return TotpCheck.INVALID
    last = _last_totp_steps.get(user_id)
    if last is not None and step <= last:
        return TotpCheck.REUSED
    _last_totp_steps[user_id] = step
    return TotpCheck.OK


def forget_totp_steps(user_id: int) -> None:
    """The user was deleted: SQLite may give its id to the next new user."""
    _last_totp_steps.pop(user_id, None)


def generate_api_token() -> tuple[str, str]:
    """Generate a new API token. Returns (full_token, token_hash)."""
    random_part = secrets.token_urlsafe(32)
    full_token = f"{API_TOKEN_PREFIX}{random_part}"
    token_hash = hashlib.sha256(full_token.encode()).hexdigest()
    return full_token, token_hash


def hash_api_token(token: str) -> str:
    """Hash an API token for storage/comparison."""
    return hashlib.sha256(token.encode()).hexdigest()


# An API token's last use is committed at most once in this interval (ADR-0252): an automation
# would otherwise write to the database on the system disk with every request (ADR-0214).
LAST_USE_INTERVAL = timedelta(hours=1)


def _token_time() -> datetime:
    """The time API tokens are checked and stamped at: UTC without a zone, as their times are
    stored (tests replace it)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ==================== Recovery codes (ADR-0238) ====================

RECOVERY_CODE_COUNT = 10
RECOVERY_CODE_INVALID = "Invalid recovery code"
# 12 hex digits (48 random bits), shown as XXXX-XXXX-XXXX.
_RECOVERY_CODE_DIGITS = re.compile(r"[0-9A-F]{12}")


def recovery_code_hash(code: object) -> str | None:
    """The stored form of recovery code ``code``: the SHA-256 of its 12 hex digits in upper
    case, however it was typed (any case, with or without dashes and spaces). None for
    anything that is not a recovery code."""
    if not isinstance(code, str):
        return None
    digits = re.sub(r"[\s-]", "", code).upper()
    if not _RECOVERY_CODE_DIGITS.fullmatch(digits):
        return None
    return hashlib.sha256(digits.encode()).hexdigest()


def generate_recovery_codes(count: int = RECOVERY_CODE_COUNT) -> tuple[list[str], list[str]]:
    """``count`` distinct new recovery codes as shown (XXXX-XXXX-XXXX), and their hashes."""
    digits: list[str] = []
    while len(digits) < count:
        candidate = secrets.token_hex(6).upper()
        if candidate not in digits:  # two equal codes would make one code sign in twice
            digits.append(candidate)
    shown = [f"{d[:4]}-{d[4:8]}-{d[8:]}" for d in digits]
    return shown, [recovery_code_hash(code) for code in shown]


def recovery_code_hashes(stored: object) -> list:
    """The list that users.recovery_codes holds: a hash per code, null once the code is used.
    An empty list for anything else (the column of a restored database is not trusted)."""
    if not isinstance(stored, str):
        return []
    try:
        hashes = json.loads(stored)
    except ValueError:
        return []
    return hashes if isinstance(hashes, list) else []


def recovery_codes_left(stored: object) -> int:
    return sum(isinstance(h, str) for h in recovery_code_hashes(stored))


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def authenticate_token(token: str, db: AsyncSession, *, allow_setup_only: bool = False) -> User:
    """Resolve a bearer credential (JWT or ``unos_`` API token) to its user.

    Shared by HTTP requests (``verify_token``) and the WebSocket handshake so
    both apply the same rules. Raises ``HTTPException(401)`` when the
    credential is invalid, expired or disabled, or its user no longer exists,
    and ``HTTPException(403)`` with TWO_FACTOR_SETUP_REQUIRED for an account that
    can only set up two-factor authentication (ADR-0386): unless
    ``allow_setup_only``, and then only for a session token.
    """
    if token.startswith(API_TOKEN_PREFIX):
        user = await verify_api_token(token, db)
        await _refuse_setup_only(db, user, allowed=False)
        return user

    try:
        username, session_id = decode_access_token(token)
    except jwt.PyJWTError:
        raise _unauthorized("Invalid token") from None

    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if user is None:
        raise _unauthorized("User not found")
    # A token whose session was ended (sign-out, a password change, an administrator) is no
    # session any more (ADR-0329).
    session = await db.get(UserSession, session_id)
    owner = session.user_id if session is not None else _unstored_sessions.get(session_id)
    if owner != user.id:
        raise _unauthorized("Session ended")
    await _refuse_setup_only(db, user, allowed=allow_setup_only)
    return user


async def _refuse_setup_only(db: AsyncSession, user: User, *, allowed: bool) -> None:
    """403 for an account that can only set up two-factor authentication, unless ``allowed``. An
    account with it costs no read."""
    if user.totp_enabled or allowed:
        return
    state = await requirement.account_state(db, user)
    if state is not None and state.setup_only:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=requirement.TWO_FACTOR_SETUP_REQUIRED)


async def verify_token(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Verify JWT token or API token. A setup-only account gets 403."""
    return await authenticate_token(credentials.credentials, db)


async def verify_token_allowing_setup(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    """``verify_token`` for the routes that a setup-only account reaches with a session token: its
    own account, the setup of two-factor authentication, its status and the sign-out
    (SETUP_ONLY_ALLOWED of tests/test_authz.py). Its API tokens still get 403."""
    return await authenticate_token(credentials.credentials, db, allow_setup_only=True)


async def require_admin(user: User = Depends(verify_token)) -> User:
    """Allow the request only for administrators.

    Use as a route or router dependency: ``dependencies=[Depends(require_admin)]``.
    Anonymous requests are rejected by ``verify_token`` before this runs.
    """
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator privileges required",
        )
    return user


SESSION_NEEDED = "An API token cannot create, enable, disable or delete API tokens: sign in to do it"


async def require_session(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    user: User = Depends(verify_token),
) -> User:
    """Allow the request only with a session token, not with an API token (ADR-0253).

    For the changes that would outlast the API token that asks for them, such as a new token:
    a token's lifetime and its revocation must bound what it can do, and a session token needs
    the password and the second factor (ADR-0239). An API token that is not valid keeps the 401
    of ``verify_token``; a valid one gets 403.
    """
    if credentials.credentials.startswith(API_TOKEN_PREFIX):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=SESSION_NEEDED)
    return user


async def verify_api_token(token: str, db: AsyncSession) -> User:
    """Verify an API token and return the associated user.

    The token's last use is committed only when the stored one is empty or LAST_USE_INTERVAL
    old (ADR-0252). A commit that fails, such as on a full /data, is rolled back and logged,
    and the request is answered all the same.
    """
    token_hash = hash_api_token(token)

    result = await db.execute(
        select(APIToken).where(APIToken.token_hash == token_hash)
    )
    api_token = result.scalar_one_or_none()

    if api_token is None:
        raise _unauthorized("Invalid API token")

    if not api_token.is_active:
        raise _unauthorized("API token is disabled")

    now = _token_time()
    # Check expiration (stored as naive UTC)
    if api_token.expires_at and api_token.expires_at < now:
        raise _unauthorized("API token has expired")

    # Read before a rollback, which expires the loaded rows.
    token_id, user_id = api_token.id, api_token.user_id
    if api_token.last_used_at is None or now - api_token.last_used_at >= LAST_USE_INTERVAL:
        # Set only when it is committed here: a later commit of the request would write it too.
        api_token.last_used_at = now
        try:
            await db.commit()
        except OperationalError as exc:
            await db.rollback()
            logger.warning("Could not store the last use of API token %d: %s", token_id, exc.orig or exc)

    # Get the associated user
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if user is None:
        raise _unauthorized("User not found")

    return user
