from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

# bcrypt only reads the first 72 bytes of a password. New passwords longer than
# that are rejected instead of being silently truncated; see auth.utils for how
# existing hashes of longer passwords keep verifying.
MAX_PASSWORD_BYTES = 72


def _fits_bcrypt(password: str) -> str:
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise ValueError(
            f"Password must not be longer than {MAX_PASSWORD_BYTES} bytes (UTF-8 encoded)"
        )
    return password


# Use for passwords that are about to be hashed. Passwords that are only
# verified (login, current password) must not be limited, or accounts created
# before the limit existed could no longer log in.
NewPassword = Annotated[str, AfterValidator(_fits_bcrypt)]


class UserCreate(BaseModel):
    username: str
    password: NewPassword


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    is_admin: bool
    totp_enabled: bool = False
    created_at: datetime | None = None


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    requires_totp: bool = False


class LoginRequest(BaseModel):
    username: str
    password: str
    totp_code: str | None = None


class PasswordChange(BaseModel):
    current_password: str
    new_password: NewPassword


class UserUpdate(BaseModel):
    is_admin: bool | None = None
    password: NewPassword | None = None


# TOTP 2FA schemas
class TOTPSetupResponse(BaseModel):
    secret: str
    qr_uri: str


class TOTPVerifyRequest(BaseModel):
    code: str


class TOTPEnableRequest(BaseModel):
    code: str


# API Token schemas
class APITokenCreate(BaseModel):
    name: str
    permissions: str = "*"  # * for all, or comma-separated like "read,containers,storage"
    # Optional expiration: 1 day to 10 years. Anything else is a 422 (huge
    # values used to overflow the expiry date into a 500).
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)


class APITokenResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    token_prefix: str
    is_active: bool
    permissions: str
    created_at: datetime | None = None
    last_used_at: datetime | None = None
    expires_at: datetime | None = None


class APITokenCreated(BaseModel):
    """Returned only when creating a new token - includes the full token."""
    id: int
    name: str
    token: str  # Full token - only shown once!
    token_prefix: str
    permissions: str
    expires_at: datetime | None = None


# 2FA Recovery codes
class RecoveryCodesResponse(BaseModel):
    codes: list[str]  # Plain codes - only shown once when generated


class RecoveryCodeVerify(BaseModel):
    code: str
