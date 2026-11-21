from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StrictBool, StringConstraints, model_validator
from pydantic.config import JsonDict

from ..times import UtcDatetime
from . import scopes
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
    """A new UNOS user: the first administrator at first-run setup, or a regular user."""

    model_config = ConfigDict(json_schema_extra={"examples": [
        {"username": "alice", "password": "example-password", "smb_enabled": False},
    ]})

    # The name rule of new users (security.validators.validate_new_username)
    # runs in the handlers, so that a bad name is a 400 with a plain message.
    username: str = Field(
        max_length=64,
        description="The new user's name: 1 to 32 lowercase letters, digits, _ and -, starting with a letter, "
                    "and not one reserved for the system.",
    )
    password: NewPassword = Field(description="The new user's password, at most 72 bytes in UTF-8.")
    # Also turn SMB on with this password (POST /auth/users only).
    smb_enabled: bool = Field(
        default=False,
        description="Also turn SMB on with this password (POST /auth/users only; first-run setup refuses true).",
    )


class TwoFactorRequired(BaseModel):
    """What the requirement of two-factor authentication asks of an account without it."""

    by: UtcDatetime = Field(description="The end of the account's grace period, in UTC.")
    setup_only: bool = Field(
        description="The grace period is over: the account's session reaches only the setup of two-factor "
                    "authentication, its status, its own account and the sign-out."
    )


class UserResponse(BaseModel):
    """A UNOS user."""

    model_config = ConfigDict(from_attributes=True, json_schema_extra={"examples": [{
        "id": 2,
        "username": "alice",
        "is_admin": False,
        "totp_enabled": True,
        "created_at": "2026-11-18T09:30:00Z",
        "smb": {
            "state": "password_needed", "allowed": True, "linux_name": "alice", "uid": None,
            "password_set_at": None, "reason": None,
        },
        "warnings": [],
        "two_factor_required": None,
    }]})

    id: int = Field(description="The user's id. An id is reused after a deletion: a request that changes a "
                                "user may send its current username too, and answers 409 when the id now names "
                                "another user.")
    username: str = Field(description="The user's name.")
    is_admin: bool = Field(description="Whether the user is an administrator; otherwise a regular user.")
    totp_enabled: bool = Field(default=False, description="Whether the user has two-factor authentication.")
    created_at: UtcDatetime | None = Field(default=None, description="When the user was created, in UTC.")
    # Set by the user management endpoints; GET /auth/me keeps it null (the
    # Account page uses GET /auth/me/smb).
    smb: SmbAccountStatus | None = Field(
        default=None,
        description="The user's SMB state in the answers of /auth/users and its routes; null in the others."
    )
    warnings: list[str] = Field(
        default=[], description="What the change did not finish, such as an smb.conf that could not be updated yet."
    )
    # Set by GET /auth/me only: null while every account need not have two-factor authentication,
    # or this one has it.
    two_factor_required: TwoFactorRequired | None = Field(
        default=None,
        description="In GET /auth/me's answer only: by when the account must set up two-factor authentication; "
                    "null while it need not, or has it.",
    )


class SecurityKeyRequest(BaseModel):
    """What the page passes to the browser's navigator.credentials.get at the code step:
    a challenge of the account's own, and the keys registered at the address the page is open at."""
    # ADR-0409.

    challenge: str = Field(description="The challenge for the account's keys, base64url.")  # base64url
    rp_id: str = Field(description="The relying party ID: the host name of the web UI's address.")
    credential_ids: list[str] = Field(
        description="The credential ids of the account's keys that work at this address, base64url."
    )  # base64url
    timeout_ms: int = Field(description="How long the browser waits for the key, in milliseconds.")
    user_verification: Literal["discouraged"] = Field(
        default="discouraged", description="Always discouraged: the key is a second factor beside the password."
    )


class Token(BaseModel):
    """The answer of a sign-in: the session, or the step that asks for the second factor."""

    model_config = ConfigDict(json_schema_extra={"examples": [
        {"access_token": "example-session-token", "token_type": "bearer", "requires_totp": False,
         "session_key": "example-session-key"},
        {"access_token": "", "token_type": "bearer", "requires_totp": True, "session_key": "",
         "security_key": {"challenge": "example-challenge", "rp_id": "nas.example.com",
                          "credential_ids": ["example-credential-id"], "timeout_ms": 60000,
                          "user_verification": "discouraged"},
         "security_key_origins": ["https://nas.example.com"]},
    ]})

    # Empty when the sign-in asked for the session cookie (token_type "cookie", ADR-0397) and at the
    # step that asks for the second factor (requires_totp).
    access_token: str = Field(
        description="The session token, to send as a Bearer token in the Authorization header; empty when the "
                    "session went into the session cookie and at the step that asks for the second factor."
    )
    token_type: str = Field(
        default="bearer", description="bearer, or cookie when the session went into the session cookie."
    )
    requires_totp: bool = Field(
        default=False,
        description="True at the step that asks for the second factor: sign in again with the password and a "
                    "two-factor code, a recovery code or a security key's answer.",
    )
    # The session key, which the web UI's page sends with the session cookie (ADR-0397); empty at the
    # step that asks for the second factor. A program with the token needs none.
    session_key: str = Field(
        default="",
        description="The session key, which the web UI's page sends with the session cookie in the "
                    "X-UNOS-Session-Key header; empty at the step that asks for the second factor.",
    )
    # At the step that asks for the second factor (ADR-0409): the request for a security key, when the
    # account has keys that work at this address, and the addresses where its keys work, when it has
    # any. The sign-in leaves them out of its answer otherwise (response_model_exclude_none).
    security_key: SecurityKeyRequest | None = Field(
        default=None,
        description="At the step that asks for the second factor: the challenge for the account's security "
                    "keys that work at this address; left out otherwise.",
    )
    security_key_origins: list[str] | None = Field(
        default=None,
        description="At the step that asks for the second factor: the web UI's addresses where the account's "
                    "security keys work; left out otherwise, and when it has none.",
    )


class SecurityKeyAssertion(BaseModel):
    """A security key's answer at the code step (navigator.credentials.get), each field base64url.
    Bounded: a credential id has at most 1023 bytes, a signature at most 512 (RSA-4096)."""

    model_config = ConfigDict(extra="forbid")

    credential_id: str = Field(min_length=1, max_length=1364, description="The key's credential id.")
    client_data_json: str = Field(min_length=1, max_length=6000, description="The browser's client data JSON.")
    authenticator_data: str = Field(min_length=1, max_length=2000, description="The key's authenticator data.")
    signature: str = Field(min_length=1, max_length=1400, description="The key's signature.")


class LoginRequest(BaseModel):
    """A sign-in: the password, and for an account with two-factor authentication one second factor."""

    model_config = ConfigDict(json_schema_extra={"examples": [
        {"username": "alice", "password": "example-password"},
        {"username": "alice", "password": "example-password", "totp_code": "123456"},
    ]})

    # Bounded: a failed login is recorded (login_attempts, the audit log) before
    # anyone is authenticated. The limits are far above any real name, password
    # (bcrypt takes at most 72 bytes) or code; the 422 handler leaves the values out.
    username: str = Field(max_length=255, description="The user's name.")
    password: str = Field(max_length=1024, description="The user's password.")
    totp_code: str | None = Field(
        default=None, max_length=16,
        description="A code of the authenticator app; each code signs in once.",
    )
    # In place of totp_code: XXXX-XXXX-XXXX, in any case, with or without dashes and spaces (ADR-0238).
    recovery_code: str | None = Field(
        default=None, max_length=32,
        description="In place of totp_code: an unused recovery code, XXXX-XXXX-XXXX, in any case, with or without "
                    "dashes and spaces.",
    )
    # In place of totp_code: a security key's answer to the challenge of the code step (ADR-0409).
    security_key: SecurityKeyAssertion | None = Field(
        default=None, description="In place of totp_code: a security key's answer to the challenge of the code step."
    )
    # The web UI's sign-in: the session goes into the HttpOnly session cookie, not into the answer
    # (ADR-0397). Programs leave it out and get the token for a Bearer header.
    cookie: StrictBool = Field(
        default=False,
        description="The web UI's sign-in: the session goes into the session cookie and the answer holds only "
                    "the session key; only the web UI's own page may ask for it. Programs leave it out.",
    )

    @model_validator(mode="after")
    def _one_second_factor(self) -> "LoginRequest":
        if sum(bool(factor) for factor in (self.totp_code, self.recovery_code, self.security_key)) > 1:
            raise ValueError("Send one second factor: a two-factor code, a recovery code or a security key")
        return self


class PasswordChange(BaseModel):
    """A change of the signed-in user's own password."""

    model_config = ConfigDict(json_schema_extra={"examples": [
        {"current_password": "example-old-password", "new_password": "example-new-password"},
    ]})

    current_password: str = Field(description="The user's current password.")
    new_password: NewPassword = Field(description="The new password, at most 72 bytes in UTF-8.")


class UserUpdate(BaseModel):
    """A change of a user by an administrator: the role, the password or the name. Fields left out stay."""

    model_config = ConfigDict(json_schema_extra={"examples": [
        {"username": "alice", "is_admin": False, "password": "example-new-password"},
        {"username": "alice", "new_username": "alicia"},
    ]})

    is_admin: bool | None = Field(
        default=None,
        description="Make the user an administrator (true) or a regular user (false); null keeps the role."
    )
    password: NewPassword | None = Field(
        default=None,
        description="A new password, at most 72 bytes in UTF-8: it ends the user's sessions (but the "
                    "administrator's own one), and an active SMB account gets it too. Null keeps the password.",
    )
    # The username the request was built for: users.id is a plain rowid that
    # SQLite reuses after a deletion (409 when it is not the user's name).
    username: str | None = Field(
        default=None, max_length=64,
        description="The user's current name, as the request was built for it: 409 when the id now belongs to "
                    "another user. Required with new_username.",
    )
    # A new name (ADR-0368); the name rule of new users runs in the handler, as for UserCreate.
    new_username: str | None = Field(
        default=None, max_length=64,
        description="A new name for the user, by the rule of new users: it ends every session of the user, and "
                    "with an active SMB account it needs a new password too. Null keeps the name.",
    )

    @model_validator(mode="after")
    def _a_rename_names_the_user(self) -> "UserUpdate":
        if self.new_username is not None and self.username is None:
            raise ValueError("A rename must also send the user's current username")
        return self


# SMB access (ADR-0118)
class SmbEnableRequest(BaseModel):
    """An administrator allows SMB for a user, and with a password also turns it on."""

    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [
        {"username": "alice", "password": "example-password"},
    ]})
    username: str = Field(
        max_length=64, description="The user's current name: 409 when the id now belongs to another user."
    )  # must be the user's current username (409)
    password: NewPassword | None = Field(
        default=None,
        description="A password for the user, at most 72 bytes in UTF-8: it becomes the UNOS password and the "
                    "SMB password, and SMB is turned on. Null only allows SMB.",
    )


class SmbDisableRequest(BaseModel):
    """An administrator disallows SMB for a user."""

    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [{"username": "alice"}]})
    username: str = Field(
        max_length=64, description="The user's current name: 409 when the id now belongs to another user."
    )  # must be the user's current username (409)


class SmbSelfEnableRequest(BaseModel):
    """The signed-in user turns their own SMB access on."""

    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [
        {"current_password": "example-password", "totp_code": "123456"},
    ]})
    current_password: str = Field(
        max_length=1024, description="The user's current UNOS password, which becomes the SMB password."
    )
    totp_code: str | None = Field(
        default=None, max_length=16,
        description="A new code of the authenticator app, not the one of the sign-in; required when the user has "
                    "two-factor authentication.",
    )


class MySmbShare(BaseModel):
    """A share that the user can use, without its path."""

    name: str = Field(description="The share's name.")
    comment: str = Field(description="The share's comment; empty when it has none.")
    level: Literal["read", "write"] = Field(
        description="The user's level: the higher of their own share access and their groups' (read when a "
                    "guest share gives them none)."
    )
    guest_ok: bool = Field(description="Whether it is a guest share, which guests may read without a password.")
    usable: bool = Field(
        description="Whether the user can use it now: their SMB account is active, or it is a guest share. "
                    "Whether Samba serves the share is not checked."
    )  # the account is active (or it is a guest share). Database only: whether
       # the share is served (ADR-0145) is not checked, since that needs the host's pool list


_MY_SMB_EXAMPLE: JsonDict = {
    "account": {
        "state": "password_needed", "allowed": True, "linux_name": "alice", "uid": 70000,
        "password_set_at": None, "reason": None,
    },
    "totp_required": True,
    "shares": [
        {"name": "media", "comment": "Films and music", "level": "read", "guest_ok": True, "usable": True},
        {"name": "projects", "comment": "", "level": "write", "guest_ok": False, "usable": False},
    ],
}


class MySmbStatus(BaseModel):
    """The signed-in user's SMB state and the shares they can use."""

    model_config = ConfigDict(json_schema_extra={"examples": [_MY_SMB_EXAMPLE]})

    account: SmbAccountStatus = Field(description="The user's SMB state.")
    totp_required: bool = Field(
        description="The user has two-factor authentication: turning SMB on needs a two-factor code."
    )  # the user has 2FA: POST /auth/me/smb/enable needs totp_code
    # Shares with an access row for this user or a group of theirs, plus guest shares; by name; no paths.
    shares: list[MySmbShare] = Field(
        description="The shares that give the user or a group of theirs access, and the guest shares, by name."
    )


class MySmbChange(MySmbStatus):
    """Answer of POST /auth/me/smb/enable and /disable: the new status and the warnings of the change."""

    model_config = ConfigDict(json_schema_extra={"examples": [{**_MY_SMB_EXAMPLE, "warnings": []}]})

    warnings: list[str] = Field(
        default=[], description="What the change did not finish, such as SMB connections that could not be ended."
    )


# TOTP 2FA schemas
class TOTPSetupResponse(BaseModel):
    """A new two-factor secret for the authenticator app, to confirm with POST /auth/totp/enable."""

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "secret": "EXAMPLEEXAMPLEEXAMPLEEXAMPLE2345",
        "qr_uri": "otpauth://totp/UNOS:alice?secret=EXAMPLEEXAMPLEEXAMPLEEXAMPLE2345&issuer=UNOS",
    }]})

    secret: str = Field(description="The secret, base32, for typing into the authenticator app.")
    qr_uri: str = Field(description="The otpauth:// URI of the secret, for a QR code.")


class TOTPVerifyRequest(BaseModel):
    """A code of the authenticator app."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"code": "123456"}]})

    code: str = Field(description="A code of the authenticator app; each code is accepted once.")


class TOTPEnableRequest(BaseModel):
    """A code of the authenticator app that confirms its setup."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"code": "123456"}]})

    code: str = Field(description="A code of the authenticator app for the secret of the setup.")


# API Token schemas
# Each token is a row of the database on the system disk, which any signed-in user can add to:
# its name is bounded, and so is the number of tokens per user (ADR-0253).
API_TOKEN_NAME_CHARS = 64


def _each_area_once(areas: list[str] | None) -> list[str] | None:
    if areas is not None and len(set(areas)) != len(areas):
        raise ValueError("Name each area once")
    return areas


class APITokenCreate(BaseModel):
    """A new API token of the signed-in user: full, or limited by its scope."""

    # A field the request does not know is a 422: a client that still sends `permissions`, which
    # left the API, gets no token with more rights than it asked for (ADR-0392).
    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [
        {"name": "backup script", "read_only": True, "areas": ["backup", "storage"], "expires_in_days": 365},
    ]})

    # Shown in the token list and the audit log; white space around it is dropped.
    name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=API_TOKEN_NAME_CHARS)
    ] = Field(description="The token's name, 1 to 64 characters; white space around it is dropped.")
    # The token's scope (ADR-0392): read only, or read and change; and its areas of the API, each
    # once, or None for every area. Without either, the token is full, as tokens were before.
    read_only: StrictBool = Field(default=False, description="Whether the token may only read; false: read and change.")
    areas: Annotated[
        list[scopes.Area] | None,
        Field(
            default=None, min_length=1, max_length=len(scopes.AREAS),
            description="The areas of the API that the token reaches, each once; null: every area.",
        ),
        AfterValidator(_each_area_once),
    ] = None
    # Optional expiration: 1 day to 10 years. Anything else is a 422 (huge
    # values used to overflow the expiry date into a 500).
    expires_in_days: int | None = Field(
        default=None, ge=1, le=3650,
        description="After how many days the token expires, 1 to 3650; null: it never expires.",
    )

    def scope(self) -> scopes.Scope:
        return scopes.Scope(read_only=self.read_only, areas=None if self.areas is None else frozenset(self.areas))


class APITokenResponse(BaseModel):
    """One of the user's API tokens, without the token itself."""

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "id": 3, "name": "backup script", "token_prefix": "unos_example", "is_active": True,
        "read_only": True, "areas": ["backup", "storage"], "scope_known": True,
        "created_at": "2026-11-18T09:40:00Z", "last_used_at": "2026-11-19T03:00:05Z",
        "expires_at": "2027-11-18T09:40:00Z",
    }]})

    id: int = Field(description="The token's id.")
    name: str = Field(description="The token's name.")
    token_prefix: str = Field(description="The token's first 12 characters, to tell it apart.")
    is_active: bool = Field(description="Whether the token is enabled; a disabled one is refused.")
    # The scope as stored (ADR-0392): areas None is every area, else sorted. scope_known is false
    # for a stored value that cannot be read, from a restored or damaged database: the token is
    # refused everywhere, and read_only true with no areas says so.
    read_only: bool = Field(description="Whether the token may only read; false: read and change.")
    areas: list[str] | None = Field(
        default=None, description="The areas of the API that the token reaches, sorted; null: every area."
    )
    scope_known: bool = Field(
        default=True,
        description="False for a stored scope that cannot be read (a restored or damaged database): the token "
                    "is refused everywhere, and read_only is true with no areas.",
    )
    created_at: UtcDatetime | None = Field(default=None, description="When the token was created, in UTC.")
    last_used_at: UtcDatetime | None = Field(
        default=None, description="When the token was last used, in UTC, stored at most once an hour; null: never."
    )
    expires_at: UtcDatetime | None = Field(default=None, description="When the token expires, in UTC; null: never.")


class APITokenCreated(BaseModel):
    """Returned only when creating a new token - includes the full token."""

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "id": 3, "name": "backup script", "token": "unos_example-token-shown-only-once",
        "token_prefix": "unos_example", "read_only": True, "areas": ["backup", "storage"],
        "expires_at": "2027-11-18T09:40:00Z",
    }]})

    id: int = Field(description="The token's id.")
    name: str = Field(description="The token's name.")
    token: str = Field(
        description="The token, to send as a Bearer token in the Authorization header. Shown only in this "
                    "answer: the server keeps its hash."
    )  # Full token - only shown once!
    token_prefix: str = Field(description="The token's first 12 characters, to tell it apart.")
    read_only: bool = Field(description="Whether the token may only read; false: read and change.")
    areas: list[str] | None = Field(
        default=None, description="The areas of the API that the token reaches, sorted; null: every area."
    )
    expires_at: UtcDatetime | None = Field(default=None, description="When the token expires, in UTC; null: never.")


# 2FA recovery codes (ADR-0238)
class RecoveryCodesResponse(BaseModel):
    """The user's new recovery codes."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"codes": [
        "0000-0000-0000", "1111-1111-1111", "2222-2222-2222", "3333-3333-3333", "4444-4444-4444",
        "5555-5555-5555", "6666-6666-6666", "7777-7777-7777", "8888-8888-8888", "9999-9999-9999",
    ]}]})

    codes: list[str] = Field(
        description="The ten new codes, XXXX-XXXX-XXXX, each of which signs in once. Shown only in this answer: "
                    "the server keeps their hashes."
    )  # the new codes, answered only this once: the database keeps their hashes


class RecoveryCodesStatus(BaseModel):
    """How many of the user's recovery codes are left."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"remaining": 8, "generated": True}]})

    remaining: int = Field(description="The codes not used yet; 0 while two-factor authentication is off.")
    generated: bool = Field(
        description="Whether codes were generated for the current two-factor setup, also when all are used."
    )
