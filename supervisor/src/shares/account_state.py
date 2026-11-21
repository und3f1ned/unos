"""SMB state of a UNOS user, derived from its users and smb_accounts rows (never stored)."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from ..times import UtcDatetime

from .constants import SMB_UID_MAX, SMB_UID_MIN, linux_name_problem

ADMIN_REASON = (
    "Administrators cannot use SMB: their password controls the whole NAS, and SMB has no "
    "two-factor authentication and no login rate limit. Use a regular account for files."
)


class SmbAccountState(str, Enum):
    ACTIVE = "active"                    # can sign in over SMB with the UNOS password
    PASSWORD_NEEDED = "password_needed"  # allowed; Samba has no password for the user yet
    OFF = "off"                          # not allowed
    UNAVAILABLE = "unavailable"          # administrator, or a username SMB cannot use


class SmbAccountStatus(BaseModel):
    """A user's SMB state, derived from the database and never stored."""

    state: SmbAccountState = Field(
        description="active: the user signs in over SMB with the UNOS password; password_needed: allowed, but "
                    "Samba has no password for the user yet; off: not allowed; unavailable: an administrator, "
                    "or a username that SMB cannot use."
    )
    allowed: bool = Field(description="Whether an administrator allowed SMB for the user.")
    linux_name: str | None = Field(
        default=None, description="The name of the user's SMB account on the host; null unless SMB is allowed."
    )
    uid: int | None = Field(
        default=None,
        description="The user id of the SMB account on the host, 70000 to 79999; null unless SMB is allowed and "
                    "the account has one.",
    )
    password_set_at: UtcDatetime | None = Field(
        default=None, description="When Samba got the account's password, in UTC; null unless the state is active."
    )
    reason: str | None = Field(default=None, description="Why SMB is unavailable; null for the other states.")


def username_smb_problem(username: str) -> str | None:
    problem = linux_name_problem(username)
    return None if problem is None else f"The username {username!r} cannot be used for SMB. {problem}."


def derive_smb_state(user, account) -> SmbAccountStatus:
    """``user``: auth.models.User; ``account``: models.SmbAccount or None."""
    if user.is_admin:
        return SmbAccountStatus(state=SmbAccountState.UNAVAILABLE, allowed=False, reason=ADMIN_REASON)
    problem = username_smb_problem(user.username)
    if problem:
        return SmbAccountStatus(state=SmbAccountState.UNAVAILABLE, allowed=False, reason=problem)
    if account is None or not account.enabled:
        return SmbAccountStatus(state=SmbAccountState.OFF, allowed=False)
    uid_ok = isinstance(account.uid, int) and SMB_UID_MIN <= account.uid <= SMB_UID_MAX
    active = account.password_state == "set" and uid_ok and account.linux_name == user.username
    return SmbAccountStatus(
        state=SmbAccountState.ACTIVE if active else SmbAccountState.PASSWORD_NEEDED,
        allowed=True,
        linux_name=user.username,
        uid=account.uid if uid_ok else None,
        password_set_at=account.password_set_at if active else None,
    )
