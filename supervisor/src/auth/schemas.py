from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from ..shares.account_state import SmbAccountStatus

# bcrypt takes at most 72 bytes of a password: new passwords longer than that
# are refused (422), and a longer one that is only checked matches no hash
# (auth.utils.verify_password, ADR-0315).
MAX_PASSWORD_BYTES = 72


def _fits_bcrypt(password: str) -> str:
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise ValueError(
            f"Password must not be longer than {MAX_PASSWORD_BYTES} bytes (UTF-8 encoded)"
        )
    return password


# Use for passwords that are about to be hashed. Passwords that are only
# checked (the sign-in, the current password) get no such limit: a longer one
# fails like a wrong password, not with a 422 (ADR-0315).
NewPassword = Annotated[str, AfterValidator(_fits_bcrypt)]


class UserCreate(BaseModel):
    # The name rule of new users (security.validators.validate_new_username)
    # runs in the handlers, so that a bad name is a 400 with a plain message.
    username: str = Field(max_length=64)
    password: NewPassword
    # Also turn SMB on with this password (POST /auth/users only).
    smb_enabled: bool = False


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    is_admin: bool
    totp_enabled: bool = False
    created_at: datetime | None = None
    # Set by the user management endpoints; GET /auth/me keeps it null (the
    # Account page uses GET /auth/me/smb).
    smb: SmbAccountStatus | None = None
    warnings: list[str] = []


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    requires_totp: bool = False


class LoginRequest(BaseModel):
    # Bounded: a failed login is recorded (login_attempts, the audit log) before
    # anyone is authenticated. The limits are far above any real name, password
    # (bcrypt takes at most 72 bytes) or code; the 422 handler leaves the values out.
    username: str = Field(max_length=255)
    password: str = Field(max_length=1024)
    totp_code: str | None = Field(default=None, max_length=16)
    # In place of totp_code: XXXX-XXXX-XXXX, in any case, with or without dashes and spaces (ADR-0238).
    recovery_code: str | None = Field(default=None, max_length=32)

    @model_validator(mode="after")
    def _one_second_factor(self) -> "LoginRequest":
        if self.totp_code and self.recovery_code:
            raise ValueError("Send a two-factor code or a recovery code, not both")
        return self


class PasswordChange(BaseModel):
    current_password: str
    new_password: NewPassword


class UserUpdate(BaseModel):
    is_admin: bool | None = None
    password: NewPassword | None = None
    # The username the request was built for: users.id is a plain rowid that
    # SQLite reuses after a deletion (409 when it is not the user's name).
    username: str | None = Field(default=None, max_length=64)


# SMB access (ADR-0118)
class SmbEnableRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(max_length=64)       # must be the user's current username (409)
    password: NewPassword | None = None


class SmbDisableRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(max_length=64)       # must be the user's current username (409)


class SmbSelfEnableRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: str = Field(max_length=1024)
    totp_code: str | None = Field(default=None, max_length=16)


class MySmbShare(BaseModel):
    name: str
    comment: str
    level: Literal["read", "write"]
    guest_ok: bool
    usable: bool          # the account is active (or it is a guest share). Database only: whether
                          # the share is served (ADR-0145) is not checked, since that needs the host's pool list


class MySmbStatus(BaseModel):
    account: SmbAccountStatus
    totp_required: bool        # the user has 2FA: POST /auth/me/smb/enable needs totp_code
    shares: list[MySmbShare]   # shares with an access row for this user, plus guest shares; sorted by name; no paths


class MySmbChange(MySmbStatus):
    """Answer of POST /auth/me/smb/enable and /disable: the new status and the warnings of the change."""
    warnings: list[str] = []


# TOTP 2FA schemas
class TOTPSetupResponse(BaseModel):
    secret: str
    qr_uri: str


class TOTPVerifyRequest(BaseModel):
    code: str


class TOTPEnableRequest(BaseModel):
    code: str


# API Token schemas
# Each token is a row of the database on the system disk, which any signed-in user can add to:
# its name is bounded, and so is the number of tokens per user (ADR-0253).
API_TOKEN_NAME_CHARS = 64


class APITokenCreate(BaseModel):
    # Shown in the token list and the audit log; white space around it is dropped.
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=API_TOKEN_NAME_CHARS)]
    # Stored for the scopes of auth.api-token-scopes. Until they are checked, a token acts with
    # all of its owner's rights, and "*" is the one value that says so (ADR-0253).
    permissions: Literal["*"] = "*"
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


# 2FA recovery codes (ADR-0238)
class RecoveryCodesResponse(BaseModel):
    codes: list[str]  # the new codes, answered only this once: the database keeps their hashes


class RecoveryCodesStatus(BaseModel):
    remaining: int    # codes not used yet; 0 while 2FA is off
    generated: bool   # codes were generated for the current two-factor setup (also when all are used)
