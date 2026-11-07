import asyncio
import json
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query, status, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete, select, func
import pyotp
from slowapi import Limiter
from slowapi.util import get_remote_address
from ..database import get_db
from ..audit.router import log_action
from ..audit.schemas import AuditAction, AuditSeverity
from ..security.validators import validate_new_username
from ..shares import accounts, service
from ..shares.account_state import SmbAccountState, derive_smb_state
from ..shares.constants import smb_password_problem
from ..shares.models import SmbAccount, SmbShare

# Rate limiter for auth endpoints
limiter = Limiter(key_func=get_remote_address)
from .models import User, APIToken
from .schemas import (
    UserCreate, UserResponse, Token, LoginRequest, PasswordChange,
    UserUpdate, TOTPSetupResponse, TOTPVerifyRequest, TOTPEnableRequest,
    APITokenCreate, APITokenResponse, APITokenCreated,
    RecoveryCodesResponse, RecoveryCodeVerify,
    SmbEnableRequest, SmbDisableRequest, SmbSelfEnableRequest, MySmbShare, MySmbStatus, MySmbChange,
)
from .utils import (
    hash_password, verify_password, create_access_token, verify_token,
    require_admin, generate_api_token, generate_recovery_codes,
    TOTP_CODE_REUSED, TotpCheck, check_totp_code, forget_totp_steps,
)
from ..security.router import record_login_attempt

router = APIRouter(prefix="/auth", tags=["auth"])

LAST_ADMIN_DETAIL = "At least one administrator account must remain"

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


# ==================== SMB access (spec 4.1) ====================
#
# Every SMB step runs under service.smb_lock() and starts by reloading the
# target user and its account row (accounts.reload_user, populate_existing):
# objects loaded before the lock, such as the token's user, may be stale.


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
        action=AuditAction.USER_UPDATED,
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
    shares: list[MySmbShare] = []
    for share in (await db.execute(select(SmbShare))).scalars():
        if not isinstance(share.name, str):
            continue
        levels = {
            entry.level for entry in share.access
            if entry.user_id == user.id and entry.username == user.username and entry.level in ("read", "write")
        }
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


@router.post("/setup", response_model=UserResponse)
@limiter.limit("3/minute")  # Limit setup attempts
async def setup_admin(request: Request, user: UserCreate, db: AsyncSession = Depends(get_db)):
    """Create initial admin user. Only works if no users exist."""
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


@router.post("/login", response_model=Token)
@limiter.limit("5/minute")  # 5 login attempts per minute per IP
async def login(
    request: Request,
    login_request: LoginRequest,
    db: AsyncSession = Depends(get_db)
):
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
        await log_action(
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

    # Check if 2FA is enabled
    if user.totp_enabled:
        if not login_request.totp_code:
            return Token(access_token="", requires_totp=True)

        # A code is accepted once: one seen at a login cannot sign in again.
        check = check_totp_code(user.id, user.totp_secret, login_request.totp_code)
        if check is not TotpCheck.OK:
            reused = check is TotpCheck.REUSED
            # Record failed 2FA attempt
            await record_login_attempt(
                db, login_request.username, False, request,
                failure_reason="Reused 2FA code" if reused else "Invalid 2FA code"
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=TOTP_CODE_REUSED if reused else "Invalid 2FA code",
            )

    # Record successful login
    await record_login_attempt(db, login_request.username, True, request)

    # Audit log
    await log_action(
        action=AuditAction.LOGIN_SUCCESS,
        request=request,
        username=user.username,
        success=True
    )

    access_token = create_access_token(data={"sub": user.username})
    return Token(access_token=access_token, requires_totp=False)


@router.get("/me", response_model=UserResponse)
async def get_current_user(user: User = Depends(verify_token)):
    return user


@router.get("/status")
async def get_auth_status(db: AsyncSession = Depends(get_db)):
    """Check if setup is required (no users exist)."""
    result = await db.execute(select(func.count()).select_from(User))
    count = result.scalar()
    return {"setup_required": count == 0}


@router.post("/change-password")
async def change_password(
    data: PasswordChange,
    current_user: User = Depends(verify_token),
    db: AsyncSession = Depends(get_db)
):
    """Change current user password.

    For an active SMB account, Samba's copy is changed first and the
    account's open SMB sessions end after the commit (spec 3.5). A self flow:
    its warnings are fixed texts (the details are logged).
    """
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
        warnings = await accounts.store_password(db, smb_update)
    # store_password() only ends the account's sessions.
    fixed = [accounts.SELF_SESSIONS_WARNING] if warnings else []
    return {
        "status": "ok",
        "message": "Password changed successfully",
        "warnings": accounts.self_flow_warnings(token_username, warnings, fixed),
    }


# ==================== TOTP 2FA Endpoints ====================

@router.post("/totp/setup", response_model=TOTPSetupResponse)
async def setup_totp(
    current_user: User = Depends(verify_token),
    db: AsyncSession = Depends(get_db)
):
    """Generate TOTP secret for 2FA setup."""
    if current_user.totp_enabled:
        raise HTTPException(status_code=400, detail="2FA is already enabled")

    secret = pyotp.random_base32()
    current_user.totp_secret = secret
    await db.commit()

    totp = pyotp.TOTP(secret)
    uri = totp.provisioning_uri(name=current_user.username, issuer_name="UNOS")

    return TOTPSetupResponse(secret=secret, qr_uri=uri)


@router.post("/totp/enable")
async def enable_totp(
    request: TOTPEnableRequest,
    current_user: User = Depends(verify_token),
    db: AsyncSession = Depends(get_db)
):
    """Enable 2FA after verifying TOTP code."""
    if not current_user.totp_secret:
        raise HTTPException(status_code=400, detail="Run /totp/setup first")

    if current_user.totp_enabled:
        raise HTTPException(status_code=400, detail="2FA is already enabled")

    check = check_totp_code(current_user.id, current_user.totp_secret, request.code)
    if check is not TotpCheck.OK:
        raise HTTPException(
            status_code=400, detail=TOTP_CODE_REUSED if check is TotpCheck.REUSED else "Invalid TOTP code"
        )

    current_user.totp_enabled = True
    await db.commit()

    return {"status": "ok", "message": "2FA enabled successfully"}


@router.post("/totp/disable")
async def disable_totp(
    request: TOTPVerifyRequest,
    current_user: User = Depends(verify_token),
    db: AsyncSession = Depends(get_db)
):
    """Disable 2FA."""
    if not current_user.totp_enabled:
        raise HTTPException(status_code=400, detail="2FA is not enabled")

    check = check_totp_code(current_user.id, current_user.totp_secret, request.code)
    if check is not TotpCheck.OK:
        raise HTTPException(
            status_code=400, detail=TOTP_CODE_REUSED if check is TotpCheck.REUSED else "Invalid TOTP code"
        )

    current_user.totp_enabled = False
    current_user.totp_secret = None
    await db.commit()

    return {"status": "ok", "message": "2FA disabled successfully"}


@router.get("/totp/status")
async def totp_status(current_user: User = Depends(verify_token)):
    """Check if 2FA is enabled for current user."""
    return {"enabled": current_user.totp_enabled}


# ==================== User Management (admin only) ====================

@router.get("/users", response_model=list[UserResponse])
async def list_users(
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """List all users (admin only), with the SMB state of each."""
    users = (await db.execute(select(User))).scalars().all()
    rows = {row.user_id: row for row in (await db.execute(select(SmbAccount))).scalars()}
    return [_user_response(user, rows.get(user.id)) for user in users]


@router.post("/users", response_model=UserResponse)
async def create_user(
    request: Request,
    user: UserCreate,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Create a new user (admin only), optionally with SMB access (the same password).

    The insert runs under smb_lock: SQLite gives the new user the highest rowid
    plus one, so an older release (or a restored database) may have left SMB rows
    with that id, which the reconcile and the restore remove under the lock. In
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


@router.put("/users/{user_id}", response_model=UserResponse)
async def update_user(
    request: Request,
    user_id: int,
    data: UserUpdate,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Update a user (admin only).

    Promotion to administrator revokes SMB first (administrators never have an
    active SMB account) and deletes the user's share access. A new password of
    an active SMB account goes to Samba first and ends its open SMB sessions.
    """
    new_hash = await asyncio.to_thread(hash_password, data.password) if data.password else None
    admin_name = current_user.username
    warnings: list[str] = []

    async with service.smb_lock():
        user, row = await _reload_target(db, user_id)
        if data.username is not None and data.username != user.username:
            raise _user_is_now(user_id, user)
        username = user.username

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

        if demoting_admin:
            await _ensure_an_admin_remains(db)

        if promotion is not None:
            # No SMB password went to Samba: a new administrator has no active
            # account, so update_password_before_store() returned None. No
            # statement ran since revoke_for_promotion() (its writes are
            # guarded); commit_revocation() guards the commit.
            await accounts.commit_revocation(db, promotion)
            warnings += await accounts.finish_promotion(db, promotion)
        else:
            warnings += await accounts.store_password(db, smb_update)
        user, row = await _reload_target(db, user_id)

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


@router.delete("/users/{user_id}")
async def delete_user(
    request: Request,
    user_id: int,
    # The username the request was built for (ids are reused): 409 when it is not the user's.
    username: str = Query(max_length=255),
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Delete a user (admin only)."""
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
            if was_admin:
                await _ensure_an_admin_remains(db)
            await db.commit()
        forget_totp_steps(user_id)
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


@router.post("/users/{user_id}/smb/enable", response_model=UserResponse)
async def enable_user_smb(
    request: Request,
    user_id: int,
    data: SmbEnableRequest,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Allow SMB for a user (admin only). With a password, also set it: it becomes the
    user's UNOS password and SMB password, and SMB is turned on."""
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


@router.post("/users/{user_id}/smb/disable", response_model=UserResponse)
async def disable_user_smb(
    request: Request,
    user_id: int,
    data: SmbDisableRequest,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Disallow SMB for a user (admin only): Samba's password is removed and the
    user's open SMB sessions end. The host account is kept (and so its uid)."""
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


@router.get("/me/smb", response_model=MySmbStatus)
async def get_my_smb(
    current_user: User = Depends(verify_token),
    db: AsyncSession = Depends(get_db)
):
    """The signed-in user's SMB state and the shares they can use."""
    row = (
        await db.execute(select(SmbAccount).where(SmbAccount.user_id == current_user.id))
    ).scalar_one_or_none()
    return await _my_smb_status(db, current_user, row)


@router.post("/me/smb/enable", response_model=MySmbChange)
@limiter.limit("5/minute")
async def enable_my_smb(
    request: Request,
    data: SmbSelfEnableRequest,
    current_user: User = Depends(verify_token),
    db: AsyncSession = Depends(get_db)
):
    """Turn SMB on with the current UNOS password, once an administrator allowed it.

    Needs the two-factor code of users with 2FA: SMB is a password-only channel.
    The code must be a new one (not the code of the login). A self flow: its
    warnings are fixed texts (the details are logged).
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


@router.post("/me/smb/disable", response_model=MySmbChange)
async def disable_my_smb(
    request: Request,
    current_user: User = Depends(verify_token),
    db: AsyncSession = Depends(get_db)
):
    """Turn the signed-in user's SMB access off (an administrator's allowance is kept).

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

@router.get("/tokens", response_model=list[APITokenResponse])
async def list_api_tokens(
    current_user: User = Depends(verify_token),
    db: AsyncSession = Depends(get_db)
):
    """List all API tokens for current user."""
    result = await db.execute(
        select(APIToken).where(APIToken.user_id == current_user.id)
    )
    return result.scalars().all()


@router.post("/tokens", response_model=APITokenCreated)
async def create_api_token(
    request: Request,
    data: APITokenCreate,
    current_user: User = Depends(verify_token),
    db: AsyncSession = Depends(get_db)
):
    """Create a new API token. The full token is only shown once!"""
    # Generate the token
    full_token, token_hash = generate_api_token()
    token_prefix = full_token[:12]  # Show first 12 chars for identification

    # Calculate expiration if specified
    expires_at = None
    if data.expires_in_days:
        expires_at = datetime.utcnow() + timedelta(days=data.expires_in_days)

    # Create the token
    api_token = APIToken(
        name=data.name,
        token_hash=token_hash,
        token_prefix=token_prefix,
        user_id=current_user.id,
        permissions=data.permissions,
        expires_at=expires_at
    )
    db.add(api_token)
    await db.commit()
    await db.refresh(api_token)

    # Audit log
    await log_action(
        action=AuditAction.OTHER,
        request=request,
        user=current_user,
        resource_type="api_token",
        resource_id=data.name,
        details=f"Created API token: {data.name}"
    )

    return APITokenCreated(
        id=api_token.id,
        name=api_token.name,
        token=full_token,
        token_prefix=token_prefix,
        permissions=api_token.permissions,
        expires_at=expires_at
    )


@router.delete("/tokens/{token_id}")
async def delete_api_token(
    request: Request,
    token_id: int,
    current_user: User = Depends(verify_token),
    db: AsyncSession = Depends(get_db)
):
    """Delete an API token."""
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


@router.post("/tokens/{token_id}/toggle")
async def toggle_api_token(
    token_id: int,
    current_user: User = Depends(verify_token),
    db: AsyncSession = Depends(get_db)
):
    """Enable or disable an API token."""
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


# ==================== 2FA Recovery Codes ====================

@router.post("/recovery-codes/generate", response_model=RecoveryCodesResponse)
async def generate_new_recovery_codes(
    request: Request,
    current_user: User = Depends(verify_token),
    db: AsyncSession = Depends(get_db)
):
    """Generate new recovery codes. Replaces any existing codes."""
    if not current_user.totp_enabled:
        raise HTTPException(
            status_code=400,
            detail="2FA must be enabled before generating recovery codes"
        )

    # Generate new codes
    plain_codes, hashed_codes = generate_recovery_codes(10)

    # Store hashed codes
    current_user.recovery_codes = json.dumps(hashed_codes)
    await db.commit()

    # Audit log
    await log_action(
        action=AuditAction.OTHER,
        request=request,
        user=current_user,
        resource_type="recovery_codes",
        details="Generated new 2FA recovery codes",
        severity=AuditSeverity.WARNING
    )

    return RecoveryCodesResponse(codes=plain_codes)


@router.get("/recovery-codes/status")
async def recovery_codes_status(
    current_user: User = Depends(verify_token),
    db: AsyncSession = Depends(get_db)
):
    """Check how many recovery codes are remaining."""
    if not current_user.recovery_codes:
        return {"remaining": 0, "generated": False}

    try:
        codes = json.loads(current_user.recovery_codes)
        # Count non-null entries (used codes are set to null)
        remaining = len([c for c in codes if c is not None])
        return {"remaining": remaining, "generated": True}
    except json.JSONDecodeError:
        return {"remaining": 0, "generated": False}


@router.post("/recovery-codes/verify")
async def verify_recovery_code_endpoint(
    request: Request,
    data: RecoveryCodeVerify,
    db: AsyncSession = Depends(get_db)
):
    """
    Use a recovery code to log in when 2FA device is unavailable.
    This endpoint is for login, not for authenticated users.
    """
    # This is similar to login but uses recovery code instead of TOTP
    # For simplicity, we'll require username in a header or the code format includes it
    raise HTTPException(
        status_code=501,
        detail="Recovery code login should be done via /login endpoint with recovery_code field"
    )
