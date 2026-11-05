import asyncio
import json
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
import pyotp
from slowapi import Limiter
from slowapi.util import get_remote_address
from ..database import get_db
from ..audit.router import log_action
from ..audit.schemas import AuditAction, AuditSeverity

# Rate limiter for auth endpoints
limiter = Limiter(key_func=get_remote_address)
from .models import User, APIToken
from .schemas import (
    UserCreate, UserResponse, Token, LoginRequest, PasswordChange,
    UserUpdate, TOTPSetupResponse, TOTPVerifyRequest, TOTPEnableRequest,
    APITokenCreate, APITokenResponse, APITokenCreated,
    RecoveryCodesResponse, RecoveryCodeVerify
)
from .utils import (
    hash_password, verify_password, create_access_token, verify_token,
    require_admin, generate_api_token, generate_recovery_codes, verify_recovery_code
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


@router.post("/setup", response_model=UserResponse)
@limiter.limit("3/minute")  # Limit setup attempts
async def setup_admin(request: Request, user: UserCreate, db: AsyncSession = Depends(get_db)):
    """Create initial admin user. Only works if no users exist."""
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

        totp = pyotp.TOTP(user.totp_secret)
        if not totp.verify(login_request.totp_code):
            # Record failed 2FA attempt
            await record_login_attempt(
                db, login_request.username, False, request,
                failure_reason="Invalid 2FA code"
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid 2FA code",
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
    """Change current user password."""
    if not await asyncio.to_thread(
        verify_password, data.current_password, current_user.hashed_password
    ):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    current_user.hashed_password = await asyncio.to_thread(hash_password, data.new_password)
    await db.commit()
    return {"status": "ok", "message": "Password changed successfully"}


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

    totp = pyotp.TOTP(current_user.totp_secret)
    if not totp.verify(request.code):
        raise HTTPException(status_code=400, detail="Invalid TOTP code")

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

    totp = pyotp.TOTP(current_user.totp_secret)
    if not totp.verify(request.code):
        raise HTTPException(status_code=400, detail="Invalid TOTP code")

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
    """List all users (admin only)."""
    result = await db.execute(select(User))
    return result.scalars().all()


@router.post("/users", response_model=UserResponse)
async def create_user(
    request: Request,
    user: UserCreate,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Create a new user (admin only)."""
    result = await db.execute(select(User).where(User.username == user.username))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Username already exists")

    db_user = User(
        username=user.username,
        hashed_password=await asyncio.to_thread(hash_password, user.password),
        is_admin=False,
    )
    db.add(db_user)
    await db.commit()
    await db.refresh(db_user)

    # Audit log
    await log_action(
        action=AuditAction.USER_CREATED,
        request=request,
        user=current_user,
        resource_type="user",
        resource_id=user.username,
        details=f"Created new user: {user.username}"
    )

    return db_user


@router.put("/users/{user_id}", response_model=UserResponse)
async def update_user(
    request: Request,
    user_id: int,
    data: UserUpdate,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Update a user (admin only)."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    changes = []
    demoting_admin = data.is_admin is False and bool(user.is_admin)
    if data.is_admin is not None:
        user.is_admin = data.is_admin
        changes.append(f"is_admin={data.is_admin}")
    if data.password:
        user.hashed_password = await asyncio.to_thread(hash_password, data.password)
        changes.append("password changed")

    if demoting_admin:
        await _ensure_an_admin_remains(db)

    await db.commit()
    await db.refresh(user)

    # Audit log
    await log_action(
        action=AuditAction.USER_UPDATED,
        request=request,
        user=current_user,
        resource_type="user",
        resource_id=user.username,
        details=f"Updated user {user.username}: {', '.join(changes)}"
    )

    return user


@router.delete("/users/{user_id}")
async def delete_user(
    request: Request,
    user_id: int,
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

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    deleted_username = user.username
    was_admin = bool(user.is_admin)
    await db.delete(user)
    if was_admin:
        await _ensure_an_admin_remains(db)
    await db.commit()

    # Audit log
    await log_action(
        action=AuditAction.USER_DELETED,
        request=request,
        user=current_user,
        resource_type="user",
        resource_id=deleted_username,
        details=f"Deleted user: {deleted_username}",
        severity=AuditSeverity.WARNING
    )

    return {"status": "ok", "message": "User deleted"}


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
