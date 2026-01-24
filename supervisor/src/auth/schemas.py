from pydantic import BaseModel
from datetime import datetime


class UserCreate(BaseModel):
    username: str
    password: str


class UserResponse(BaseModel):
    id: int
    username: str
    is_admin: bool
    totp_enabled: bool = False
    created_at: datetime | None = None

    class Config:
        from_attributes = True


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
    new_password: str


class UserUpdate(BaseModel):
    is_admin: bool | None = None
    password: str | None = None


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
    expires_in_days: int | None = None  # Optional expiration


class APITokenResponse(BaseModel):
    id: int
    name: str
    token_prefix: str
    is_active: bool
    permissions: str
    created_at: datetime | None = None
    last_used_at: datetime | None = None
    expires_at: datetime | None = None

    class Config:
        from_attributes = True


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
