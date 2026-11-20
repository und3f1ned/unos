import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Path, Query, status, Request, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete, select, func, update
import pyotp
from ..database import get_db
from ..openapi import ADMIN, BODY, RATE_LIMITED, SIGNED_IN, answer, errors
from ..times import db_now
from ..audit.router import log_action
from ..audit.schemas import AuditAction, AuditSeverity
from ..security.validators import validate_new_username
from ..shares import accounts, service
from ..shares.account_state import SmbAccountState, derive_smb_state
from ..shares.constants import smb_password_problem
from ..shares.models import SmbAccount, SmbShare, UserGroup, UserGroupMember

# Rate limiter for auth endpoints
from .limits import limiter
from . import requirement, scopes
from . import dashboard, security_keys, theme, webauthn
from .models import DashboardLayout, User, UserTheme, APIToken, UsernameTaken
from .schemas import (
    UserCreate, UserResponse, TwoFactorRequired, Token, LoginRequest, PasswordChange,
    UserUpdate, TOTPSetupResponse, TOTPVerifyRequest, TOTPEnableRequest,
    APITokenCreate, APITokenResponse, APITokenCreated,
    RecoveryCodesResponse, RecoveryCodesStatus,
    SmbEnableRequest, SmbDisableRequest, SmbSelfEnableRequest, MySmbShare, MySmbStatus, MySmbChange,
)
from .utils import (
    hash_password, verify_password, start_session, purge_expired_sessions, end_sessions, end_session,
    keep_unstored_session, session_id_of, decode_access_token,
    verify_token, verify_token_allowing_setup, Credential, credential,
    CROSS_ORIGIN, from_own_origin, session_key, set_session_cookie, delete_session_cookie,
    require_admin, require_session, generate_api_token, generate_recovery_codes,
    TOTP_CODE_REUSED, TotpCheck, check_totp_code, forget_totp_steps,
    RECOVERY_CODE_INVALID, recovery_code_hash, recovery_code_hashes, recovery_codes_left,
)
from ..security.router import record_login_attempt

router = APIRouter(prefix="/auth", tags=["auth"])
router.include_router(dashboard.router)
router.include_router(theme.router)
router.include_router(security_keys.router)
logger = logging.getLogger(__name__)

LAST_ADMIN_DETAIL = "At least one administrator account must remain"

# ==================== The API documentation's texts (ADR-0417) ====================

# The 403 of the routes that a setup-only account's session reaches (verify_token_allowing_setup).
_SETUP_ALLOWED = {
    403: "The credential does not reach this route: the session cookie came from another page than the web "
         "UI's, or an API token belongs to an account that must set up two-factor authentication first or its "
         "scope does not reach this route.",
}
# The 403 of the routes that change API tokens (require_session).
_SESSION_ONLY = {
    403: "The credential does not reach this route: an API token (only a session token creates, enables, "
         "disables or deletes API tokens), a session cookie from another page than the web UI's, or an account "
         "that must set up two-factor authentication first.",
}
_SELF_GONE = "The signed-in user was deleted or renamed meanwhile."
_NO_USER = "No user has this id."
_ID_REUSED = "The id now belongs to another username: SQLite reuses the ids of deleted users."
_SMB_PASSWORD_RULES = "the rules of SMB passwords: at least 8 characters, no control characters, not a common password"
_SMB_HOST_FAILED = (
    "A step on the host failed (Samba's tools are missing, or a command failed or did not answer in time), or "
    "the database was busy."
)
_SMB_REVOKE_FAILED = (
    "Samba's password could not be removed (its tools are missing, or a command failed or did not answer in "
    "time), or the database was busy."
)
_SMB_CANNOT_TURN_ON = (
    "SMB is unavailable for the user (an administrator, or a username that SMB cannot use), or the host cannot "
    "take the account: smb.conf is not UNOS's own yet, a host account, group or Samba account of the name "
    "exists, the user id or the gid of unos-smb belongs to another host account or group, the previous account "
    "is still in use, or no SMB user ids are left"
)
_USER_ID = Annotated[int, Path(description="The user's id, as GET /auth/users lists it.")]
_EXPECTED_USERNAME = (
    "The user's current name, as the request was built for it: 409 when the id now belongs to another user."
)

# First-run setup must create exactly one administrator. The supervisor runs
# as a single uvicorn process, so this lock makes "no users yet" and the
# insert one step for concurrent setup requests.
_setup_lock = asyncio.Lock()


async def _ensure_an_admin_remains(db: AsyncSession) -> None:
    """Abort a pending demotion/deletion that would leave no administrator.

    Call after changing the session, before committing. Flushing first takes
    SQLite's write lock, so two administrators demoting or deleting each other
    at the same moment cannot both pass this check.
    """
    await db.flush()
    remaining = await db.scalar(
        select(func.count()).select_from(User).where(User.is_admin.is_(True))
    )
    if not remaining:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=LAST_ADMIN_DETAIL)


# ==================== SMB access ====================
#
# Every SMB step runs under service.smb_lock() and starts by reloading the
# target user and its account row (accounts.reload_user, populate_existing):
# objects loaded before the lock, such as the token's user, may be stale
# (ADR-0080, ADR-0081).


def _user_response(user: User, row: SmbAccount | None, warnings: list[str] | None = None) -> UserResponse:
    response = UserResponse.model_validate(user)
    response.smb = derive_smb_state(user, row)
    response.warnings = list(warnings or [])
    return response


def _user_is_now(user_id: int, user: User) -> HTTPException:
    """The request names a user id that now belongs to another username (ids are reused)."""
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=f"User {user_id} is now '{user.username}'; reload the page",
    )


async def _reload_target(db: AsyncSession, user_id: int) -> tuple[User, SmbAccount | None]:
    user, row = await accounts.reload_user(db, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail=accounts.USER_NOT_FOUND)
    return user, row


async def _reload_self(db: AsyncSession, user_id: int, token_username: str) -> tuple[User, SmbAccount | None]:
    """The signed-in user, reloaded; 404 when the id now belongs to another username."""
    user, row = await accounts.reload_user(db, user_id)
    if user is None or user.username != token_username:
        raise HTTPException(status_code=404, detail=accounts.USER_NOT_FOUND)
    return user, row


async def _smb_audit(
    request: Request,
    current_user_name: str,
    username: str,
    details: str,
    severity: AuditSeverity = AuditSeverity.INFO,
) -> None:
    await log_action(
        action=AuditAction.SMB_ACCOUNT_UPDATED,
        request=request,
        username=current_user_name,
        resource_type="smb_account",
        resource_id=username,
        details=details,
        severity=severity,
    )


async def _my_smb_status(db: AsyncSession, user: User, row: SmbAccount | None) -> MySmbStatus:
    """The SMB state of ``user`` and the shares they can use. Database only."""
    account = derive_smb_state(user, row)
    active = account.state is SmbAccountState.ACTIVE
    # The groups the user is a member of under their current name.
    groups = set((await db.execute(
        select(UserGroupMember.group_id)
        .join(UserGroup, UserGroup.id == UserGroupMember.group_id)
        .where(UserGroupMember.user_id == user.id, UserGroupMember.username == user.username)
    )).scalars())
    shares: list[MySmbShare] = []
    for share in (await db.execute(select(SmbShare))).scalars():
        if not isinstance(share.name, str):
            continue
        # The level that smb.conf gives: two rows of one grant (a restored table without its UNIQUE
        # constraint) give the lower level, as the render takes them (ADR-0144); across grants, the
        # user's own and each group's, the higher level counts.
        grants: dict[object, set[str]] = {}
        for entry in share.access:
            if entry.user_id == user.id and entry.username == user.username and entry.level in ("read", "write"):
                grants.setdefault("own", set()).add(entry.level)
        for entry in share.group_access:
            if entry.group_id in groups and entry.level in ("read", "write"):
                grants.setdefault(entry.group_id, set()).add(entry.level)
        levels = {"read" if "read" in rows else "write" for rows in grants.values()}
        guest = bool(share.guest_ok)
        if not levels and not guest:
            continue
        shares.append(MySmbShare(
            name=share.name,
            comment=share.comment if isinstance(share.comment, str) else "",
            level="write" if "write" in levels else "read",
            guest_ok=guest,
            usable=active or guest,
        ))
    shares.sort(key=lambda s: (s.name.lower(), s.name))
    return MySmbStatus(account=account, totp_required=bool(user.totp_enabled), shares=shares)


@router.post(
    "/setup",
    response_model=UserResponse,
    summary="Create the first administrator",
    responses=errors(BODY, RATE_LIMITED, {
        400: "The username breaks the rule of new users, smb_enabled is true (administrators cannot use SMB), "
             "or a user exists already.",
    }),
)
@limiter.limit("3/minute")  # Limit setup attempts
async def setup_admin(request: Request, user: UserCreate, db: AsyncSession = Depends(get_db)):
    """First-run setup: create the first administrator, only while no user exists. Public: it needs no
    credential."""
    validate_new_username(user.username)
    if user.smb_enabled:
        raise HTTPException(status_code=400, detail="Administrators cannot use SMB")
    async with _setup_lock:
        # Counted while holding the lock: a request that waited for it sees
        # the administrator the previous one committed.
        count = await db.scalar(select(func.count()).select_from(User))
        if count:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Admin user already exists. Use /login instead.",
            )

        db_user = User(
            username=user.username,
            hashed_password=await asyncio.to_thread(hash_password, user.password),
            is_admin=True,
        )
        db.add(db_user)
        await db.commit()
    await db.refresh(db_user)
    return db_user


async def _audit_sign_in(**entry) -> None:
    """The audit entry of a sign-in. One that cannot be written, such as on a full /data, is in
    the supervisor's log (log_action), and the sign-in still answers (ADR-0251)."""
    try:
        await log_action(**entry)
    except OSError:
        pass  # log_action reported the entry


async def _audit_refused_second_factor(request: Request, username: str, details: str) -> None:
    """A refused second factor is audited as a wrong password is, with the login history's reason
    and never the code: the audit log holds every failed sign-in of every account (ADR-0430)."""
    await _audit_sign_in(
        action=AuditAction.LOGIN_FAILED, request=request, username=username, details=details,
        success=False, severity=AuditSeverity.WARNING,
    )


@router.post(
    "/login",
    response_model=Token,
    response_model_exclude_none=True,
    summary="Sign in",
    responses=errors(BODY, RATE_LIMITED, {
        401: "The username or password is wrong; or the second factor is: a two-factor code that is wrong or "
             "was used already, a recovery code that is not an unused one of the account, or a security key's "
             "answer that is not accepted, whose challenge expired or whose signature counter went backwards.",
        403: "cookie is true, but the request did not come from the web UI's own page (its Origin).",
    }),
)
@limiter.limit("5/minute")  # 5 login attempts per minute per IP
async def login(
    request: Request,
    response: Response,
    login_request: LoginRequest,
    db: AsyncSession = Depends(get_db)
):
    """Sign in with the username and password. For an account with two-factor authentication, a request
    without a second factor answers requires_totp, with the challenge for its security keys when it has
    some for this address; the next request sends the password again with a two-factor code, a recovery
    code or a security key's answer. With ``cookie`` (the web UI) the session goes into the session cookie and the
    answer holds no token, only the session key that the page sends with the cookie; only the web UI's
    own page may ask for it, which is checked before the password. Without it (programs) the answer
    carries the token, for a Bearer header. A wrong password, a refused second factor and a completed
    sign-in go into the login history and the audit log; the step that asks for the second factor
    does not. Public: it needs no credential.
    \f
    ADR-0397, ADR-0238, ADR-0409, ADR-0430.
    """
    if login_request.cookie and not from_own_origin(request, origin_required=True):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=CROSS_ORIGIN)
    result = await db.execute(select(User).where(User.username == login_request.username))
    user = result.scalar_one_or_none()

    # bcrypt (cost 12) takes hundreds of milliseconds; keep it off the event loop.
    if not user or not await asyncio.to_thread(
        verify_password, login_request.password, user.hashed_password
    ):
        # Record failed login attempt
        await record_login_attempt(
            db, login_request.username, False, request,
            failure_reason="Incorrect username or password"
        )
        # Audit log
        await _audit_sign_in(
            action=AuditAction.LOGIN_FAILED,
            request=request,
            username=login_request.username,
            details="Incorrect username or password",
            success=False,
            severity=AuditSeverity.WARNING
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )

    # Check if 2FA is enabled. A recovery code takes the place of a code of the
    # authenticator app, and each one signs in once (ADR-0238); so does a security key's answer to
    # the challenge of the code step (ADR-0409).
    used_recovery_code = False
    used_key: str | None = None
    if user.totp_enabled and login_request.recovery_code:
        if not await _use_recovery_code(db, user.id, login_request.recovery_code):
            await record_login_attempt(
                db, login_request.username, False, request, failure_reason=RECOVERY_CODE_INVALID
            )
            await _audit_refused_second_factor(request, login_request.username, RECOVERY_CODE_INVALID)
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=RECOVERY_CODE_INVALID)
        used_recovery_code = True
    elif user.totp_enabled and login_request.security_key is not None:
        try:
            used_key = await security_keys.verify_sign_in(db, user, login_request.security_key)
        except security_keys.KeyRefused as refused:
            await record_login_attempt(db, login_request.username, False, request, failure_reason=refused.reason)
            await _audit_refused_second_factor(request, login_request.username, refused.audit or refused.reason)
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=refused.detail) from None
    elif user.totp_enabled:
        if not login_request.totp_code:
            offer, origins = await security_keys.code_step_offer(db, user, request)
            return Token(access_token="", requires_totp=True, security_key=offer, security_key_origins=origins)

        # A code is accepted once: one seen at a login cannot sign in again.
        check = check_totp_code(user.id, user.totp_secret, login_request.totp_code)
        if check is not TotpCheck.OK:
            reused = check is TotpCheck.REUSED
            reason = "Reused 2FA code" if reused else "Invalid 2FA code"
            await record_login_attempt(db, login_request.username, False, request, failure_reason=reason)
            await _audit_refused_second_factor(request, login_request.username, reason)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=TOTP_CODE_REUSED if reused else "Invalid 2FA code",
            )

    # A login attempt that cannot be stored rolls the session back, which expires the user.
    username, user_id = user.username, user.id
    # The password is the way to the setup of an account that can only set it up.
    two_factor = await requirement.account_state(db, user)
    setup_only = two_factor is not None and two_factor.setup_only

    # The session's row, and the purge of expired ones, go into the commit of the login history
    # (ADR-0329): a sign-in writes no more than before (ADR-0308).
    await purge_expired_sessions(db)
    access_token = start_session(db, user, cookie=login_request.cookie)

    # Record successful login
    await record_login_attempt(db, login_request.username, True, request)
    # A full /data could not store the session's row either: the session works all the same (ADR-0251).
    await keep_unstored_session(db, access_token, user_id)

    # Audit log
    details, severity = None, AuditSeverity.INFO
    if used_recovery_code:
        details, severity = "Signed in with a recovery code", AuditSeverity.WARNING
    elif setup_only:
        details = "Signed in after the grace period: can only set up two-factor authentication"
        severity = AuditSeverity.WARNING
    elif used_key is not None:
        details = f"Signed in with the security key '{used_key}'"
    await _audit_sign_in(
        action=AuditAction.LOGIN_SUCCESS,
        request=request,
        username=username,
        details=details,
        success=True,
        severity=severity,
    )

    key = session_key(decode_access_token(access_token)[1])
    if login_request.cookie:
        set_session_cookie(response, access_token)
        return Token(access_token="", token_type="cookie", requires_totp=False, session_key=key)
    return Token(access_token=access_token, requires_totp=False, session_key=key)


async def _own_account_audit(
    request: Request,
    action: AuditAction,
    username: str,
    details: str | None = None,
    severity: AuditSeverity = AuditSeverity.INFO,
    resource_type: str = "user",
) -> None:
    """The audit entry of a change that a user made to their own account, after it was stored:
    by them, from their address, about them (ADR-0343). One that cannot be written, such as on a
    full /data, is in the supervisor's log (log_action), and the request still answers what it
    did, as a sign-in does (ADR-0251)."""
    try:
        await log_action(
            action=action,
            request=request,
            username=username,
            resource_type=resource_type,
            resource_id=username,
            details=details,
            severity=severity,
        )
    except OSError:
        pass  # log_action reported the entry


@router.post(
    "/logout",
    summary="Sign out",
    responses={
        **answer({"status": "ok"}, "The session has ended."),
        **errors(SIGNED_IN, _SETUP_ALLOWED, {
            400: "The credential is an API token, which is no session: delete the token to end it.",
        }),
    },
)
async def logout(
    request: Request,
    response: Response,
    found: Credential = Depends(credential),
    current_user: User = Depends(verify_token_allowing_setup),
    db: AsyncSession = Depends(get_db),
):
    """End the session of this session token on the server, and delete the session cookie when the
    token came in it; an API token is no session. Written to the audit log. Any signed-in user, also a
    setup-only account's session.
    \f
    ADR-0329. Only the sign-out that ended the session writes the audit entry (ADR-0343). A session of
    the cookie also deletes the cookie (ADR-0397).
    """
    session_id = session_id_of(found.token)
    if session_id is None:
        raise HTTPException(status_code=400, detail="An API token is no session: delete it to end it")
    username = current_user.username
    ended = await end_session(db, session_id)
    await db.commit()
    if found.cookie:
        delete_session_cookie(response)
    if ended:
        await _own_account_audit(request, AuditAction.LOGOUT, username)
    return {"status": "ok"}


@router.get(
    "/me", response_model=UserResponse, summary="Read your account", responses=errors(SIGNED_IN, _SETUP_ALLOWED),
)
async def get_current_user(user: User = Depends(verify_token_allowing_setup), db: AsyncSession = Depends(get_db)):
    """The signed-in user, and what the two-factor requirement asks of them: by when to set up
    two-factor authentication, and whether the grace period is over. Any signed-in user, also a
    setup-only account's session.
    \f
    src/auth/requirement.py.
    """
    response = UserResponse.model_validate(user)
    state = await requirement.account_state(db, user)
    if state is not None:
        response.two_factor_required = TwoFactorRequired(by=state.by, setup_only=state.setup_only)
    return response


@router.get(
    "/status",
    summary="Check whether first-run setup is needed",
    responses=answer(
        {"setup_required": False},
        "setup_required: true while no user exists, when POST /auth/setup creates the first administrator.",
    ),
)
async def get_auth_status(db: AsyncSession = Depends(get_db)):
    """Whether first-run setup is needed: true while no user exists. Public: it needs no credential."""
    result = await db.execute(select(func.count()).select_from(User))
    count = result.scalar()
    return {"setup_required": count == 0}


@router.post(
    "/change-password",
    summary="Change your password",
    responses={
        **answer(
            {"status": "ok", "message": "Password changed successfully", "warnings": []},
            "The password is changed. warnings: that some of the user's open SMB connections could not be ended.",
        ),
        **errors(SIGNED_IN, BODY, {
            400: f"The current password is wrong, or the user's SMB account is active and the new password "
                 f"breaks {_SMB_PASSWORD_RULES}.",
            404: _SELF_GONE,
            409: "The user's SMB account meets a host account that UNOS did not create, or a Samba account of "
                 "the name in another letter case.",
            503: "Samba's copy of the password could not be changed (its tools are missing, or a command failed "
                 "or did not answer in time), or the database was busy.",
        }),
    },
)
async def change_password(
    request: Request,
    data: PasswordChange,
    found: Credential = Depends(credential),
    current_user: User = Depends(verify_token),
    db: AsyncSession = Depends(get_db)
):
    """Change the signed-in user's own password, with the current one. The user's other sessions end;
    an active SMB account gets the new password first, and its open SMB connections end. Written to the
    audit log. Any signed-in user.
    \f
    For an active SMB account, Samba's copy is changed first and the
    account's open SMB sessions end after the commit (ADR-0121, ADR-0123). A self flow:
    its warnings are fixed texts (the details are logged). A stored change writes a
    password_change entry, and a change of Samba's copy also an smb_account_updated one
    (ADR-0343, ADR-0320). Sessions: ADR-0329.
    """
    keep = session_id_of(found.token)  # read before anything changes
    verified_hash = current_user.hashed_password
    if not await asyncio.to_thread(verify_password, data.current_password, verified_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    new_hash = await asyncio.to_thread(hash_password, data.new_password)
    user_id, token_username = current_user.id, current_user.username

    async with service.smb_lock():
        user, row = await _reload_self(db, user_id, token_username)
        if user.hashed_password != verified_hash and not await asyncio.to_thread(
            verify_password, data.current_password, user.hashed_password
        ):
            raise HTTPException(status_code=400, detail="Current password is incorrect")
        smb_update = await accounts.update_password_before_store(
            db, user, row, data.new_password, current_password=data.current_password
        )
        user.hashed_password = new_hash
        # The user's other sessions end with the old password (ADR-0329), in the same commit.
        await end_sessions(db, user.id, keep=keep)
        warnings = await accounts.store_password(db, smb_update)
    details = "Password changed by the user"
    await _own_account_audit(
        request, AuditAction.PASSWORD_CHANGE, token_username,
        details if smb_update is None else f"{details}, also for SMB",
    )
    if smb_update is not None:
        await _own_account_audit(
            request, AuditAction.SMB_ACCOUNT_UPDATED, token_username, "SMB password changed by the user",
            resource_type="smb_account",
        )
    # store_password() only ends the account's sessions.
    fixed = [accounts.SELF_SESSIONS_WARNING] if warnings else []
    return {
        "status": "ok",
        "message": "Password changed successfully",
        "warnings": accounts.self_flow_warnings(token_username, warnings, fixed),
    }


# ==================== TOTP 2FA Endpoints ====================

@router.post(
    "/totp/setup",
    response_model=TOTPSetupResponse,
    summary="Start setting up two-factor authentication",
    responses=errors(SIGNED_IN, _SETUP_ALLOWED, {400: "The user has two-factor authentication already."}),
)
async def setup_totp(
    current_user: User = Depends(verify_token_allowing_setup),
    db: AsyncSession = Depends(get_db)
):
    """A new secret for the authenticator app, which POST /auth/totp/enable confirms; a later setup
    replaces it. Any signed-in user, also a setup-only account's session.
    \f
    Stored only while 2FA is still off (ADR-0343).
    """
    if current_user.totp_enabled:
        raise HTTPException(status_code=400, detail="2FA is already enabled")

    secret = pyotp.random_base32()
    # Stored only while 2FA is still off: a confirmation that turned it on after the check above
    # keeps the secret whose code it took, which this one would replace (ADR-0343).
    result = await db.execute(
        update(User)
        .where(User.id == current_user.id, User.totp_enabled.is_not(True))
        .values(totp_secret=secret)
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    if result.rowcount != 1:
        raise HTTPException(status_code=400, detail="2FA is already enabled")

    totp = pyotp.TOTP(secret)
    uri = totp.provisioning_uri(name=current_user.username, issuer_name="UNOS")

    return TOTPSetupResponse(secret=secret, qr_uri=uri)


@router.post(
    "/totp/enable",
    summary="Turn on two-factor authentication",
    responses={
        **answer({"status": "ok", "message": "2FA enabled successfully"}, "Two-factor authentication is on."),
        **errors(SIGNED_IN, _SETUP_ALLOWED, BODY, {
            400: "There is no setup to confirm (none, or another setup replaced its secret), two-factor "
                 "authentication is on already, or the code is wrong or was used already.",
        }),
    },
)
async def enable_totp(
    request: Request,
    data: TOTPEnableRequest,
    current_user: User = Depends(verify_token_allowing_setup),
    db: AsyncSession = Depends(get_db)
):
    """Confirm the setup of POST /auth/totp/setup with a code of the authenticator app: two-factor
    authentication is on, with no recovery codes or security keys yet. Written to the audit log. Any
    signed-in user, also a setup-only account's session.
    \f
    Only the request that turned it on writes a totp_enabled entry (ADR-0343).
    """
    if not current_user.totp_secret:
        raise HTTPException(status_code=400, detail="Run /totp/setup first")

    if current_user.totp_enabled:
        raise HTTPException(status_code=400, detail="2FA is already enabled")

    secret = current_user.totp_secret
    check = check_totp_code(current_user.id, secret, data.code)
    if check is not TotpCheck.OK:
        raise HTTPException(
            status_code=400, detail=TOTP_CODE_REUSED if check is TotpCheck.REUSED else "Invalid TOTP code"
        )

    user_id, username = current_user.id, current_user.username
    # Written only while 2FA is still off (NULL counts as off, as above) and the secret is still
    # the one the code was checked against (ADR-0343). Of two requests that both found it off,
    # each with a code of its own time step, the second changes nothing and answers as when 2FA
    # was already on. After a setup in another tab replaced the secret, the code confirms
    # nothing: 2FA stays off, not on with a secret the user may never have scanned, and the
    # request answers as when there is no setup to confirm. A new setup starts without recovery
    # codes: none of an earlier one comes back (ADR-0238).
    result = await db.execute(
        update(User)
        .where(User.id == user_id, User.totp_enabled.is_not(True), User.totp_secret == secret)
        .values(totp_enabled=True, recovery_codes=None)
        .execution_options(synchronize_session=False)
    )
    # Nor with security keys of an earlier setup, which a restored database may hold (ADR-0409).
    if result.rowcount == 1:
        await security_keys.delete_keys_of(db, user_id)
    await db.commit()
    if result.rowcount != 1:
        enabled = await db.scalar(select(User.totp_enabled).where(User.id == user_id))
        raise HTTPException(status_code=400, detail="2FA is already enabled" if enabled else "Run /totp/setup first")
    await _own_account_audit(
        request, AuditAction.TOTP_ENABLED, username, "Two-factor authentication turned on by the user"
    )

    return {"status": "ok", "message": "2FA enabled successfully"}


@router.post(
    "/totp/disable",
    summary="Turn off two-factor authentication",
    responses={
        **answer({"status": "ok", "message": "2FA disabled successfully"}, "Two-factor authentication is off."),
        **errors(SIGNED_IN, BODY, {
            400: "Two-factor authentication is off already, or the code is wrong or was used already.",
        }),
    },
)
async def disable_totp(
    request: Request,
    data: TOTPVerifyRequest,
    current_user: User = Depends(verify_token),
    db: AsyncSession = Depends(get_db)
):
    """Turn the signed-in user's two-factor authentication off with a code of the authenticator app;
    its recovery codes and security keys go with it. Written to the audit log as a warning. Any
    signed-in user.
    \f
    Only the request that turned it off writes a totp_disabled entry, of severity warning (ADR-0343).
    """
    if not current_user.totp_enabled:
        raise HTTPException(status_code=400, detail="2FA is not enabled")

    check = check_totp_code(current_user.id, current_user.totp_secret, data.code)
    if check is not TotpCheck.OK:
        raise HTTPException(
            status_code=400, detail=TOTP_CODE_REUSED if check is TotpCheck.REUSED else "Invalid TOTP code"
        )

    username = current_user.username
    # One statement writes all three columns. Assigning None to the loaded user would leave
    # recovery_codes out of the UPDATE when it was loaded as None, and then codes that a
    # generation stored after that load would outlive 2FA: they belong to this setup (ADR-0238).
    # Written only while 2FA is still on: of two requests that both found it on, the second
    # changes nothing and answers as when 2FA was already off (ADR-0343).
    user_id = current_user.id
    result = await db.execute(
        update(User)
        .where(User.id == user_id, User.totp_enabled.is_(True))
        .values(totp_enabled=False, totp_secret=None, recovery_codes=None)
        .execution_options(synchronize_session=False)
    )
    # The security keys are an alternative to the app's codes: they go in the same commit (ADR-0409).
    keys = await security_keys.delete_keys_of(db, user_id) if result.rowcount == 1 else 0
    await db.commit()
    if result.rowcount != 1:
        raise HTTPException(status_code=400, detail="2FA is not enabled")
    details = "Two-factor authentication turned off by the user, with its recovery codes"
    await _own_account_audit(
        request, AuditAction.TOTP_DISABLED, username,
        f"{details} and {security_keys.keys_text(keys)}" if keys else details,
        AuditSeverity.WARNING,
    )

    return {"status": "ok", "message": "2FA disabled successfully"}


@router.get(
    "/totp/status",
    summary="Read your two-factor status",
    responses={
        **answer(
            {"enabled": True, "required": False},
            "enabled: the user has two-factor authentication; required: the two-factor requirement asks it of "
            "every account.",
        ),
        **errors(SIGNED_IN, _SETUP_ALLOWED),
    },
)
async def totp_status(current_user: User = Depends(verify_token_allowing_setup), db: AsyncSession = Depends(get_db)):
    """Whether the signed-in user has two-factor authentication, and whether every account must have it.
    Any signed-in user, also a setup-only account's session."""
    return {"enabled": bool(current_user.totp_enabled), "required": await requirement.current(db) is not None}


# ==================== User Management (admin only) ====================

@router.get("/users", response_model=list[UserResponse], summary="List the users", responses=errors(ADMIN))
async def list_users(
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Every UNOS user, with their SMB state. Administrators only."""
    users = (await db.execute(select(User))).scalars().all()
    rows = {row.user_id: row for row in (await db.execute(select(SmbAccount))).scalars()}
    return [_user_response(user, rows.get(user.id)) for user in users]


@router.post(
    "/users",
    response_model=UserResponse,
    summary="Create a user",
    responses=errors(ADMIN, BODY, {
        400: f"The username breaks the rule of new users or exists already (in any letter case), or with "
             f"smb_enabled the password breaks {_SMB_PASSWORD_RULES}.",
        404: "The new user was deleted again before the answer.",
    }),
)
async def create_user(
    request: Request,
    user: UserCreate,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Create a regular user, optionally with SMB turned on with the same password: when SMB cannot be
    turned on, the user is created all the same, and the answer's warnings say why. Written to the audit
    log. Administrators only.
    \f
    The insert runs under smb_lock: SQLite gives the new user the highest rowid
    plus one, so a restored or hand-edited database may have left SMB rows with
    that id, which the reconcile and the restore remove under the lock. In
    the same transaction they are deleted: a new user never inherits SMB state.
    """
    username = validate_new_username(user.username)
    # Samba and tdbsam treat Bob and bob as one account.
    result = await db.execute(select(User).where(func.lower(User.username) == username.lower()))
    if result.scalars().first() is not None:
        raise HTTPException(status_code=400, detail="Username already exists")
    if user.smb_enabled:
        problem = smb_password_problem(user.password)
        if problem is not None:
            raise HTTPException(status_code=400, detail=problem)

    db_user = User(
        username=username,
        hashed_password=await asyncio.to_thread(hash_password, user.password),
        is_admin=False,
    )
    admin_name = current_user.username
    warnings: list[str] = []
    smb_on = False

    async with service.smb_lock():
        db.add(db_user)
        try:
            await db.flush()
            await db.execute(delete(SmbAccount).where(SmbAccount.user_id == db_user.id))
            await service.delete_access_of_user(db, db_user.id)
            await db.commit()
        except IntegrityError:
            await db.rollback()
            raise HTTPException(status_code=400, detail="Username already exists") from None
        user_id = db_user.id

        if user.smb_enabled:
            try:
                target, row = await _reload_target(db, user_id)
                if target.username != username:
                    raise _user_is_now(user_id, target)
                flow = await accounts.activate(db, target, row, user.password, by_admin=True, new_hash=None)
                warnings += flow.warnings
                smb_on = True
            except HTTPException as exc:
                warnings.append(f"User created, but SMB access could not be turned on: {exc.detail}")
        created, row = await accounts.reload_user(db, user_id)

    # Audit log
    await log_action(
        action=AuditAction.USER_CREATED,
        request=request,
        username=admin_name,
        resource_type="user",
        resource_id=username,
        details=f"Created new user: {username}"
    )
    if smb_on:
        await _smb_audit(request, admin_name, username, "SMB password set by an administrator")

    if created is None:
        raise HTTPException(status_code=404, detail=accounts.USER_NOT_FOUND)
    return _user_response(created, row, warnings)


@router.put(
    "/users/{user_id}",
    response_model=UserResponse,
    summary="Change a user",
    responses=errors(ADMIN, BODY, {
        400: f"The new name breaks the rule of new users or another user has it (in any letter case), the name "
             f"and the role change at once, or for an active SMB account the password is missing for a rename "
             f"or breaks {_SMB_PASSWORD_RULES}.",
        404: _NO_USER,
        409: "The id now belongs to another username, the change would leave no administrator, or the host "
             "cannot take the SMB account's new name or password: a host account, group or Samba account of "
             "the name exists, smb.conf is not UNOS's own yet, or the account is still in use.",
        503: _SMB_HOST_FAILED,
    }),
)
async def update_user(
    request: Request,
    user_id: _USER_ID,
    data: UserUpdate,
    found: Credential = Depends(credential),
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Change a user's role, password or name. Promotion to administrator turns SMB off first and
    removes the user's share access and group memberships: administrators never have SMB. A new password
    of an active SMB account goes to Samba first and ends its open SMB connections. A new name
    (``new_username``) renames the user and their SMB account. Written to the audit log. Administrators
    only.
    \f
    ADR-0368, _rename_user.
    """
    new_name = validate_new_username(data.new_username) if data.new_username is not None else None
    new_hash = await asyncio.to_thread(hash_password, data.password) if data.password else None
    admin_name = current_user.username
    own_session = session_id_of(found.token) if current_user.id == user_id else None
    warnings: list[str] = []
    renamed: _Renamed | None = None

    async with service.smb_lock():
        user, row = await _reload_target(db, user_id)
        if data.username is not None and data.username != user.username:
            raise _user_is_now(user_id, user)
        username = user.username

        if new_name is not None and new_name != username:
            if data.is_admin is not None and data.is_admin != bool(user.is_admin):
                raise HTTPException(status_code=400, detail="Change the name and the role one at a time")
            renamed = await _rename_user(db, user, row, new_name, data.password, new_hash)
        else:
            changes = []
            demoting_admin = data.is_admin is False and bool(user.is_admin)
            promotion = None
            if data.is_admin is True and not user.is_admin:
                promotion = await accounts.revoke_for_promotion(db, user, row)
            if data.is_admin is not None:
                user.is_admin = data.is_admin
                changes.append(f"is_admin={data.is_admin}")
            smb_update = None
            if data.password:
                smb_update = await accounts.update_password_before_store(
                    db, user, row, data.password, current_password=None
                )
                user.hashed_password = new_hash
                changes.append("password changed (also for SMB)" if smb_update else "password changed")
                # Every session of the user ends in this commit (ADR-0329), but the administrator's
                # own one when they set their own password.
                await end_sessions(db, user.id, keep=own_session)

            if demoting_admin:
                await _ensure_an_admin_remains(db)

            if promotion is not None:
                # No SMB password went to Samba: a new administrator has no active
                # account, so update_password_before_store() returned None. No
                # write ran since revoke_for_promotion() (its writes are guarded,
                # and end_sessions only reads before the commit); commit_revocation()
                # guards the commit.
                await accounts.commit_revocation(db, promotion)
                warnings += await accounts.finish_promotion(db, promotion)
            else:
                warnings += await accounts.store_password(db, smb_update)
            user, row = await _reload_target(db, user_id)

    if renamed is not None:
        changes = [f"renamed from {username}"]
        if data.password:
            changes.append("password changed (also for SMB)" if renamed.smb_on else "password changed")
        await log_action(
            action=AuditAction.USER_UPDATED,
            request=request,
            username=admin_name,
            resource_type="user",
            resource_id=new_name,
            details=f"Updated user {new_name}: {', '.join(changes)}",
        )
        if renamed.had_row:
            await _smb_audit(request, admin_name, new_name, f"SMB account renamed from {username} to {new_name}")
        if renamed.smb_on:
            await _smb_audit(request, admin_name, new_name, "SMB password set by an administrator")
        return _user_response(renamed.user, renamed.row, renamed.warnings)

    # Audit log
    await log_action(
        action=AuditAction.USER_UPDATED,
        request=request,
        username=admin_name,
        resource_type="user",
        resource_id=username,
        details=f"Updated user {username}: {', '.join(changes)}"
    )
    if smb_update is not None:
        await _smb_audit(request, admin_name, username, "SMB password set by an administrator")
    if promotion is not None and (promotion.had_row or promotion.access_deleted):
        await _smb_audit(
            request, admin_name, username,
            "SMB access revoked by the promotion to administrator",
            AuditSeverity.WARNING,
        )

    return _user_response(user, row, warnings)


@dataclass
class _Renamed:
    user: User
    row: SmbAccount | None
    had_row: bool       # the user had an SMB account row, which took the new name
    smb_on: bool        # SMB was turned on again under the new name with the rename's password
    warnings: list[str]


async def _rename_user(
    db: AsyncSession, user: User, row: SmbAccount | None, new_name: str, password: str | None, new_hash: str | None
) -> _Renamed:
    """Rename ``user`` to ``new_name`` (ADR-0368), under smb_lock, after the name rule.

    A name that another user has, in any letter case, is refused (400): Samba and tdbsam take
    such names as one account. The host follows first (accounts.rename_before_store), then the
    new name, the password and the account and access rows are committed in one transaction, in
    which every session of the user ends, the administrator's own too: tokens carry the name
    (ADR-0329), and the new name's row of usernames_taken says from when the user's login history
    shows the attempts on it (ADR-0368). An account that was active is turned on again under the
    new name with ``password`` (accounts.finish_rename).
    """
    taken = await db.scalar(
        select(User.username).where(func.lower(User.username) == new_name.lower(), User.id != user.id)
    )
    if taken is not None:
        detail = "Username already exists" if taken == new_name else (
            f"Username already exists as '{taken}': names that differ only in letter case are one account for Samba"
        )
        raise HTTPException(status_code=400, detail=detail)
    user_id, had_row = user.id, row is not None
    # Read before the host steps; written by the commit of store_rename, which undoes them when it fails.
    taken_before = await db.get(UsernameTaken, new_name)
    rename = await accounts.rename_before_store(db, user, row, new_name, password)
    user.username = new_name
    if taken_before is None:
        db.add(UsernameTaken(username=new_name))
    else:
        taken_before.taken_at = func.now()
    if new_hash is not None:
        user.hashed_password = new_hash
    await end_sessions(db, user_id)
    await accounts.store_rename(db, user, row, rename)
    user, row = await _reload_target(db, user_id)
    warnings, smb_on = await accounts.finish_rename(db, user, row, rename, password)
    user, row = await _reload_target(db, user_id)
    return _Renamed(user, row, had_row, smb_on, warnings)


@router.delete(
    "/users/{user_id}",
    summary="Delete a user",
    responses={
        **answer(
            {"status": "ok", "message": "User deleted", "warnings": []},
            "The user is deleted. warnings: what the removal of their SMB account on the host or the update of "
            "smb.conf did not finish.",
        ),
        **errors(ADMIN, {
            400: "The administrator asked to delete themselves while another administrator remains.",
            404: _NO_USER,
            409: "The id now belongs to another username, or the deletion would leave no administrator.",
            503: "The user's SMB access could not be revoked (Samba's tools are missing, or a command failed or "
                 "did not answer in time), or the database was busy: the user is kept.",
        }),
    },
)
async def delete_user(
    request: Request,
    user_id: _USER_ID,
    # The username the request was built for (ids are reused): 409 when it is not the user's.
    username: str = Query(max_length=255, description=_EXPECTED_USERNAME),
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Delete a user, with their sessions, API tokens, security keys, dashboard layout, theme, share
    access, group memberships and SMB account. Written to the audit log. Administrators only.
    \f
    SMB first (accounts.begin_user_removal), every write up to the commit in revocation_guard.
    """
    if current_user.id == user_id:
        admin_count = await db.scalar(
            select(func.count()).select_from(User).where(User.is_admin.is_(True))
        )
        if not admin_count or admin_count <= 1:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=LAST_ADMIN_DETAIL)
        raise HTTPException(status_code=400, detail="Cannot delete yourself")

    admin_name = current_user.username
    async with service.smb_lock():
        user, row = await _reload_target(db, user_id)
        if username != user.username:
            raise _user_is_now(user_id, user)
        deleted_username = user.username
        was_admin = bool(user.is_admin)
        # SMB first: the passdb entry is removed before the commit (503: the
        # user is kept); the access and account rows go in the same transaction.
        removal = await accounts.begin_user_removal(db, user, row)
        # Every write up to the commit is guarded: when one fails after the
        # passdb entry was removed, the row says 'needed' and its SMB sessions
        # end (503; the last-admin 409 passes through).
        async with accounts.revocation_guard(db, removal):
            # Loads the user's API tokens (delete cascade); that query
            # autoflushes the account row's DELETE.
            await db.delete(user)
            await end_sessions(db, user_id)  # SQLite enforces no foreign keys (ADR-0319)
            await db.execute(delete(DashboardLayout).where(DashboardLayout.user_id == user_id))
            await db.execute(delete(UserTheme).where(UserTheme.user_id == user_id))
            await security_keys.delete_keys_of(db, user_id)
            if was_admin:
                await _ensure_an_admin_remains(db)
            await db.commit()
        forget_totp_steps(user_id)
        webauthn.forget_user(user_id)
        warnings = await accounts.finish_user_removal(db, removal) if removal is not None else []

    # Audit log
    details = f"Deleted user: {deleted_username}"
    if removal is not None and removal.had_row:
        details += "; SMB account removed"
    await log_action(
        action=AuditAction.USER_DELETED,
        request=request,
        username=admin_name,
        resource_type="user",
        resource_id=deleted_username,
        details=details,
        severity=AuditSeverity.WARNING
    )

    return {"status": "ok", "message": "User deleted", "warnings": warnings}


@router.post(
    "/users/{user_id}/end-sessions",
    summary="End a user's sessions",
    responses={
        **answer({"status": "ok", "ended": 2}, "ended: how many of the user's stored sessions ended."),
        **errors(ADMIN, {404: _NO_USER, 409: _ID_REUSED}),
    },
)
async def end_user_sessions(
    request: Request,
    user_id: _USER_ID,
    # The username the request was built for (ids are reused): 409 when it is not the user's.
    username: str = Query(max_length=255, description=_EXPECTED_USERNAME),
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """End every session of a user: their session tokens answer 401 from now on, and their live-data
    socket closes at its next check. Their API tokens stay. Written to the audit log. Administrators only.
    \f
    ADR-0329.
    """
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail=accounts.USER_NOT_FOUND)
    if username != user.username:
        raise _user_is_now(user_id, user)
    username, admin_name = user.username, current_user.username
    ended = await end_sessions(db, user_id)
    await db.commit()
    await log_action(
        action=AuditAction.USER_UPDATED,
        request=request,
        username=admin_name,
        resource_type="user",
        resource_id=username,
        details=f"Ended the sessions of {username}: {ended}",
        severity=AuditSeverity.WARNING,
    )
    return {"status": "ok", "ended": ended}


@router.post(
    "/users/{user_id}/smb/enable",
    response_model=UserResponse,
    summary="Allow SMB for a user",
    responses=errors(ADMIN, BODY, {
        400: f"The password breaks {_SMB_PASSWORD_RULES}.",
        404: _NO_USER,
        409: f"The id now belongs to another username; or {_SMB_CANNOT_TURN_ON}.",
        503: _SMB_HOST_FAILED,
    }),
)
async def enable_user_smb(
    request: Request,
    user_id: _USER_ID,
    data: SmbEnableRequest,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Allow SMB for a regular user. With a password, also set it: it becomes the user's UNOS password
    and SMB password, and SMB is turned on. A change is written to the audit log. Administrators only."""
    new_hash = None
    if data.password is not None:
        new_hash = await asyncio.to_thread(hash_password, data.password)
    admin_name = current_user.username

    async with service.smb_lock():
        user, row = await _reload_target(db, user_id)
        if data.username != user.username:
            raise _user_is_now(user_id, user)
        username = user.username
        if data.password is None:
            flow = await accounts.allow(db, user, row)
            details = "SMB access allowed"
        else:
            flow = await accounts.activate(db, user, row, data.password, by_admin=True, new_hash=new_hash)
            details = "SMB password set by an administrator"
        user, row = await _reload_target(db, user_id)

    if flow.changed:
        await _smb_audit(request, admin_name, username, details)
    return _user_response(user, row, flow.warnings)


@router.post(
    "/users/{user_id}/smb/disable",
    response_model=UserResponse,
    summary="Disallow SMB for a user",
    responses=errors(ADMIN, BODY, {404: _NO_USER, 409: _ID_REUSED, 503: _SMB_REVOKE_FAILED}),
)
async def disable_user_smb(
    request: Request,
    user_id: _USER_ID,
    data: SmbDisableRequest,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Disallow SMB for a user: Samba's password is removed and the user's open SMB connections end.
    The host account is kept, and so its user id. A change is written to the audit log. Administrators
    only."""
    admin_name = current_user.username
    async with service.smb_lock():
        user, row = await _reload_target(db, user_id)
        if data.username != user.username:
            raise _user_is_now(user_id, user)
        username = user.username
        flow = await accounts.disallow(db, user, row)
        user, row = await _reload_target(db, user_id)

    if flow.changed:
        await _smb_audit(request, admin_name, username, "SMB access disallowed")
    return _user_response(user, row, flow.warnings)


@router.get("/me/smb", response_model=MySmbStatus, summary="Read your SMB access", responses=errors(SIGNED_IN))
async def get_my_smb(
    current_user: User = Depends(verify_token),
    db: AsyncSession = Depends(get_db)
):
    """The signed-in user's SMB state and the shares they can use, from the database only. Any signed-in
    user."""
    row = (
        await db.execute(select(SmbAccount).where(SmbAccount.user_id == current_user.id))
    ).scalar_one_or_none()
    return await _my_smb_status(db, current_user, row)


@router.post(
    "/me/smb/enable",
    response_model=MySmbChange,
    summary="Turn on your SMB access",
    responses=errors(SIGNED_IN, BODY, RATE_LIMITED, {
        400: f"The current password is wrong, the two-factor code is missing, wrong or used already, or the "
             f"password breaks {_SMB_PASSWORD_RULES}.",
        403: f"An administrator has not allowed SMB for the user, or {SIGNED_IN[403][:1].lower()}"
             f"{SIGNED_IN[403][1:]}",
        404: _SELF_GONE,
        409: f"{_SMB_CANNOT_TURN_ON}.",
        503: _SMB_HOST_FAILED,
    }),
)
@limiter.limit("5/minute")
async def enable_my_smb(
    request: Request,
    data: SmbSelfEnableRequest,
    current_user: User = Depends(verify_token),
    db: AsyncSession = Depends(get_db)
):
    """Turn SMB on with the current UNOS password, once an administrator allowed it: the password
    becomes the SMB password. A user with two-factor authentication also sends a new two-factor code,
    not the one of the sign-in, since SMB signs in with the password only. Written to the audit log. Any
    signed-in user.
    \f
    A self flow: its warnings and the texts of failed host commands are fixed (the details are logged,
    ADR-0131).
    """
    verified_hash = current_user.hashed_password
    if not await asyncio.to_thread(verify_password, data.current_password, verified_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    if current_user.totp_enabled and not data.totp_code:
        raise HTTPException(status_code=400, detail="Two-factor code required")
    user_id, token_username = current_user.id, current_user.username

    async with service.smb_lock():
        user, row = await _reload_self(db, user_id, token_username)
        if user.hashed_password != verified_hash and not await asyncio.to_thread(
            verify_password, data.current_password, user.hashed_password
        ):
            raise HTTPException(status_code=400, detail="Current password is incorrect")
        if user.totp_enabled:
            if not data.totp_code:
                raise HTTPException(status_code=400, detail="Two-factor code required")
            check = check_totp_code(user.id, user.totp_secret, data.totp_code)
            if check is TotpCheck.REUSED:
                raise HTTPException(status_code=400, detail=TOTP_CODE_REUSED)
            if check is not TotpCheck.OK:
                raise HTTPException(status_code=400, detail="Invalid two-factor code")
        flow = await accounts.activate(db, user, row, data.current_password, by_admin=False, new_hash=None)
        user, row = await _reload_self(db, user_id, token_username)
        result = await _my_smb_status(db, user, row)

    await _smb_audit(request, token_username, token_username, "SMB access activated by the user")
    warnings = accounts.self_flow_warnings(token_username, flow.warnings, flow.self_warnings)
    return MySmbChange(**result.model_dump(), warnings=warnings)


@router.post(
    "/me/smb/disable",
    response_model=MySmbChange,
    summary="Turn off your SMB access",
    responses=errors(SIGNED_IN, {404: _SELF_GONE, 503: _SMB_REVOKE_FAILED}),
)
async def disable_my_smb(
    request: Request,
    current_user: User = Depends(verify_token),
    db: AsyncSession = Depends(get_db)
):
    """Turn the signed-in user's SMB access off: Samba's password is removed and their open SMB
    connections end; an administrator's allowance is kept. A change is written to the audit log. Any
    signed-in user.
    \f
    A self flow: its warnings are fixed texts (the details are logged).
    """
    user_id, token_username = current_user.id, current_user.username
    async with service.smb_lock():
        user, row = await _reload_self(db, user_id, token_username)
        flow = await accounts.deactivate(db, user, row)
        user, row = await _reload_self(db, user_id, token_username)
        result = await _my_smb_status(db, user, row)

    if flow.changed:
        await _smb_audit(request, token_username, token_username, "SMB access deactivated by the user")
    warnings = accounts.self_flow_warnings(token_username, flow.warnings, flow.self_warnings)
    return MySmbChange(**result.model_dump(), warnings=warnings)


# ==================== API Token Management ====================
#
# Only a session token creates, toggles and deletes tokens (require_session): tokens made with a
# token would outlast its lifetime and its revocation. Listing them is open to API tokens too: it
# changes nothing and never shows a token (ADR-0253).

# Each token is a row of the database on the system disk (ADR-0214, ADR-0253).
MAX_API_TOKENS_PER_USER = 100
TOO_MANY_API_TOKENS = (
    f"You already have {MAX_API_TOKENS_PER_USER} API tokens, the most a user can have: delete one first"
)


def _scope_fields(kind: object, raw: object) -> dict:
    """The answer's fields of a token's scope as stored (scopes.stored_columns, ADR-0392); a value
    that cannot be read reaches nothing, and says so."""
    scope = scopes.parse(scopes.stored_text(kind, raw))
    if scope is None:
        return {"read_only": True, "areas": [], "scope_known": False}
    areas = None if scope.areas is None else sorted(scope.areas)
    return {"read_only": scope.read_only, "areas": areas, "scope_known": True}


@router.get(
    "/tokens", response_model=list[APITokenResponse], summary="List your API tokens", responses=errors(SIGNED_IN),
)
async def list_api_tokens(
    current_user: User = Depends(verify_token),
    db: AsyncSession = Depends(get_db)
):
    """The signed-in user's API tokens with their scopes, never the tokens themselves. Any signed-in
    user, also with an API token."""
    result = await db.execute(
        select(APIToken, *scopes.stored_columns(APIToken.permissions)).where(APIToken.user_id == current_user.id)
    )
    return [
        APITokenResponse(
            id=row.id, name=row.name, token_prefix=row.token_prefix, is_active=bool(row.is_active),
            created_at=row.created_at, last_used_at=row.last_used_at, expires_at=row.expires_at,
            **_scope_fields(kind, raw),
        )
        for row, kind, raw in result.all()
    ]


@router.post(
    "/tokens",
    response_model=APITokenCreated,
    summary="Create an API token",
    responses=errors(SIGNED_IN, BODY, _SESSION_ONLY, {
        409: f"The user has {MAX_API_TOKENS_PER_USER} API tokens already, the most a user can have.",
    }),
)
async def create_api_token(
    request: Request,
    data: APITokenCreate,
    current_user: User = Depends(require_session),
    db: AsyncSession = Depends(get_db)
):
    """Create an API token of the signed-in user, full or limited by its scope; the token is in this
    answer only. Only with a session token. Written to the audit log. Any signed-in user.
    \f
    ADR-0392, ADR-0253.
    """
    scope = data.scope()
    # Generate the token
    full_token, token_hash = generate_api_token()
    token_prefix = full_token[:12]  # Show first 12 chars for identification

    # Calculate expiration if specified
    expires_at = None
    if data.expires_in_days:
        expires_at = db_now() + timedelta(days=data.expires_in_days)

    # Create the token
    user_id = current_user.id
    api_token = APIToken(
        name=data.name,
        token_hash=token_hash,
        token_prefix=token_prefix,
        user_id=user_id,
        permissions=scopes.stored_value(scope),
        expires_at=expires_at
    )
    db.add(api_token)
    # Counted after the flush, which takes SQLite's write lock: two requests at the same moment
    # cannot both pass the limit.
    await db.flush()
    count = await db.scalar(select(func.count()).select_from(APIToken).where(APIToken.user_id == user_id))
    if count > MAX_API_TOKENS_PER_USER:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=TOO_MANY_API_TOKENS)
    await db.commit()
    await db.refresh(api_token)

    # Audit log
    await log_action(
        action=AuditAction.OTHER,
        request=request,
        user=current_user,
        resource_type="api_token",
        resource_id=data.name,
        details=f"Created API token: {data.name} ({scopes.describe(scope)})"
    )

    return APITokenCreated(
        id=api_token.id,
        name=api_token.name,
        token=full_token,
        token_prefix=token_prefix,
        read_only=scope.read_only,
        areas=None if scope.areas is None else sorted(scope.areas),
        expires_at=expires_at
    )


_TOKEN_ID = Annotated[int, Path(description="The token's id, as GET /auth/tokens lists it.")]


@router.delete(
    "/tokens/{token_id}",
    summary="Delete an API token",
    responses={
        **answer({"status": "ok", "message": "Token deleted"}, "The token is deleted."),
        **errors(SIGNED_IN, _SESSION_ONLY, {404: "The user has no API token with this id."}),
    },
)
async def delete_api_token(
    request: Request,
    token_id: _TOKEN_ID,
    current_user: User = Depends(require_session),
    db: AsyncSession = Depends(get_db)
):
    """Delete one of the signed-in user's API tokens: it is refused from now on. Only with a session
    token. Written to the audit log. Any signed-in user."""
    result = await db.execute(
        select(APIToken).where(
            APIToken.id == token_id,
            APIToken.user_id == current_user.id
        )
    )
    api_token = result.scalar_one_or_none()

    if not api_token:
        raise HTTPException(status_code=404, detail="Token not found")

    token_name = api_token.name
    await db.delete(api_token)
    await db.commit()

    # Audit log
    await log_action(
        action=AuditAction.OTHER,
        request=request,
        user=current_user,
        resource_type="api_token",
        resource_id=token_name,
        details=f"Deleted API token: {token_name}"
    )

    return {"status": "ok", "message": "Token deleted"}


@router.post(
    "/tokens/{token_id}/toggle",
    summary="Enable or disable an API token",
    responses={
        **answer(
            {"status": "ok", "is_active": False, "message": "Token disabled"},
            "is_active: whether the token is enabled now.",
        ),
        **errors(SIGNED_IN, _SESSION_ONLY, {404: "The user has no API token with this id."}),
    },
)
async def toggle_api_token(
    token_id: _TOKEN_ID,
    current_user: User = Depends(require_session),
    db: AsyncSession = Depends(get_db)
):
    """Disable an enabled API token of the signed-in user, or enable a disabled one. Only with a session
    token. Any signed-in user."""
    result = await db.execute(
        select(APIToken).where(
            APIToken.id == token_id,
            APIToken.user_id == current_user.id
        )
    )
    api_token = result.scalar_one_or_none()

    if not api_token:
        raise HTTPException(status_code=404, detail="Token not found")

    api_token.is_active = not api_token.is_active
    await db.commit()

    return {
        "status": "ok",
        "is_active": api_token.is_active,
        "message": f"Token {'enabled' if api_token.is_active else 'disabled'}"
    }


# ==================== 2FA Recovery Codes (ADR-0238) ====================

RECOVERY_CODES_NEED_2FA = "2FA must be enabled before generating recovery codes"

@router.post(
    "/recovery-codes/generate",
    response_model=RecoveryCodesResponse,
    summary="Generate new recovery codes",
    responses=errors(SIGNED_IN, {400: "The user has no two-factor authentication."}),
)
async def generate_new_recovery_codes(
    request: Request,
    current_user: User = Depends(verify_token),
    db: AsyncSession = Depends(get_db)
):
    """Replace the signed-in user's recovery codes with ten new ones, which this answer alone shows; the
    earlier codes no longer sign in. Written to the audit log as a warning. Any signed-in user.
    \f
    ADR-0238.
    """
    if not current_user.totp_enabled:
        raise HTTPException(status_code=400, detail=RECOVERY_CODES_NEED_2FA)

    plain_codes, hashed_codes = generate_recovery_codes()
    # Written only while 2FA is still on: codes stored after it was turned off would outlive it.
    result = await db.execute(
        update(User)
        .where(User.id == current_user.id, User.totp_enabled.is_(True))
        .values(recovery_codes=json.dumps(hashed_codes))
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    if result.rowcount != 1:
        raise HTTPException(status_code=400, detail=RECOVERY_CODES_NEED_2FA)

    # The new codes are stored and the earlier ones no longer work: an audit entry that cannot
    # be written (a full disk) is logged, and the codes, answered only now, still reach the user.
    try:
        await log_action(
            action=AuditAction.OTHER,
            request=request,
            user=current_user,
            resource_type="recovery_codes",
            details="Generated new 2FA recovery codes",
            severity=AuditSeverity.WARNING
        )
    except Exception as exc:
        logger.error(
            "Could not write the audit entry of new recovery codes for %s: %s", current_user.username, exc
        )

    return RecoveryCodesResponse(codes=plain_codes)


@router.get(
    "/recovery-codes/status",
    response_model=RecoveryCodesStatus,
    summary="Count your recovery codes",
    responses=errors(SIGNED_IN),
)
async def recovery_codes_status(current_user: User = Depends(verify_token)):
    """How many of the signed-in user's recovery codes are left: none while two-factor authentication is
    off, when no code signs in. Any signed-in user.
    \f
    Codes stored while 2FA is off come only from a restored database.
    """
    stored = current_user.recovery_codes if current_user.totp_enabled else None
    return RecoveryCodesStatus(
        remaining=recovery_codes_left(stored),
        generated=bool(recovery_code_hashes(stored)),
    )


async def _stored_recovery_codes(db: AsyncSession, user_id: int) -> str | None:
    """users.recovery_codes of user ``user_id`` as the database holds it now."""
    return await db.scalar(select(User.recovery_codes).where(User.id == user_id))


async def _use_recovery_code(db: AsyncSession, user_id: int, code: str) -> bool:
    """Use up recovery code ``code`` of user ``user_id``; False when it is not one of the
    user's unused codes.

    The used code's hash becomes null in a compare-and-swap UPDATE, committed here: it
    changes the column only while it still holds what was read. Of two sign-ins with one
    code that read the codes at the same moment, the second updates nothing and fails, and
    so does a sign-in that read them before new codes were generated (which stay as they
    are).
    """
    wanted = recovery_code_hash(code)
    if wanted is None:
        return False
    stored = await _stored_recovery_codes(db, user_id)
    hashes = recovery_code_hashes(stored)
    left = [None if h == wanted else h for h in hashes]
    if left == hashes:
        return False
    result = await db.execute(
        update(User)
        .where(User.id == user_id, User.recovery_codes == stored)
        .values(recovery_codes=json.dumps(left))
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    return result.rowcount == 1
