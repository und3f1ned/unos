"""Every account must have two-factor authentication, after a grace period (ADR-0383).

An administrator turns the requirement on in System > Security (src/security/router.py), which
stores the one row of two_factor_requirement. An account without two-factor authentication then has
the grace period from the later of the requirement's start and its own creation to set it up; after
it, the account is setup-only: authenticate_token (src/auth/utils.py) lets its session reach only
the routes that depend on verify_token_allowing_setup, and refuses everything else, its API tokens
and WebSockets included. The state is derived from the rows and the time at each request: nothing
is stored per account, and nothing is written when a grace period ends.

The row of a restored database is not trusted (ADR-0144). It is read as it is stored, never as the
ORM would convert it, so that no value of it makes a request fail: a grace period out of range is
held within the choices, and a start that cannot be read, lies in the future or a table that cannot
be read leave no grace period at all. The requirement then fails closed, never with an error, and
the setup stays reachable, so it locks no one out.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import NamedTuple, Optional

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from .models import User

logger = logging.getLogger(__name__)

# The grace periods an administrator can choose, in days, and the one offered first.
GRACE_DAYS_CHOICES = (0, 1, 3, 7, 14, 30)
DEFAULT_GRACE_DAYS = 7
MAX_GRACE_DAYS = max(GRACE_DAYS_CHOICES)

# The answer to every request that a setup-only account may not make. The web UI knows this text
# (webui/src/lib/api/client.ts) and leads to the setup page when it gets it.
TWO_FACTOR_SETUP_REQUIRED = "Two-factor authentication is required: set it up to continue"

# The one row's id.
REQUIREMENT_ID = 1


def _now() -> datetime:
    """The time the requirement is judged at: UTC without a zone, as the database keeps times
    (tests replace it)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass(frozen=True)
class Requirement:
    """The requirement as read: ``enabled_at`` None when it cannot be read or lies in the future."""

    grace_days: int
    enabled_at: Optional[datetime]
    enabled_by: Optional[str]


class AccountState(NamedTuple):
    """What the requirement asks of an account without two-factor authentication."""

    by: datetime        # the end of its grace period (UTC, without a zone)
    setup_only: bool    # the grace period is over: the account can only set it up


def _grace_days(value: object) -> int:
    """A stored grace period, held within 0 to MAX_GRACE_DAYS."""
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        return 0
    return min(value, MAX_GRACE_DAYS)


def _past_time(value: object) -> Optional[datetime]:
    """A stored time (SQLite's text, or a datetime) as UTC without a zone; None when it cannot be
    read or lies after now."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value if value <= _now() else None


async def current(db: AsyncSession) -> Optional[Requirement]:
    """The requirement, or None while it is off."""
    try:
        row = (await db.execute(
            text("SELECT grace_days, enabled_at, enabled_by FROM two_factor_requirement WHERE id = :id"),
            {"id": REQUIREMENT_ID},
        )).first()
    except SQLAlchemyError as exc:
        # A table of another layout: whether its row 1 is there says whether it is on, and turning
        # it off deletes that row. Without even that, it holds.
        try:
            there = (await db.execute(
                text("SELECT 1 FROM two_factor_requirement WHERE id = :id"), {"id": REQUIREMENT_ID}
            )).first() is not None
        except SQLAlchemyError:
            there = True
        if not there:
            return None
        logger.warning("The two-factor requirement cannot be read; it holds without a grace period: %s", exc)
        return Requirement(grace_days=0, enabled_at=None, enabled_by=None)
    if row is None:
        return None
    grace_days, enabled_at, enabled_by = row
    return Requirement(
        grace_days=_grace_days(grace_days),
        enabled_at=_past_time(enabled_at),
        enabled_by=enabled_by if isinstance(enabled_by, str) else None,
    )


def deadline(requirement: Requirement, user: User) -> datetime:
    """The end of ``user``'s grace period: ``grace_days`` after the later of the requirement's start
    and the account's creation. Without a readable start of the requirement, there is none."""
    if requirement.enabled_at is None:
        return datetime.min
    created = _past_time(user.created_at)
    start = max(requirement.enabled_at, created) if created is not None else requirement.enabled_at
    return start + timedelta(days=requirement.grace_days)


async def account_state(db: AsyncSession, user: User) -> Optional[AccountState]:
    """What the requirement asks of ``user``: None while it is off or the account has two-factor
    authentication (a NULL totp_enabled counts as off, ADR-0343)."""
    if user.totp_enabled:
        return None
    requirement = await current(db)
    if requirement is None:
        return None
    by = deadline(requirement, user)
    return AccountState(by=by, setup_only=_now() >= by)
