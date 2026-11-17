"""Every account must have two-factor authentication, after a grace period (ADR-0386).

An administrator turns the requirement on in System > Security (src/security/router.py), which
stores the one row of two_factor_requirement. An account without two-factor authentication then has
the grace period from the later of the requirement's start and its own creation to set it up; after
it, the account is setup-only: authenticate_token (src/auth/utils.py) lets its session reach only
the routes that depend on verify_token_allowing_setup, and refuses everything else, its API tokens
and WebSockets included. The state is derived from the rows and the time at each request: nothing
is stored per account, and nothing is written when a grace period ends.

The row of a restored or damaged database is not trusted (ADR-0144). It is read as stored, never as
the ORM would convert it, so that no value of it makes a request fail: a grace period out of range
is held within the choices, and a start that cannot be read leaves no grace period (it fails closed).
A start after now, as on a board whose clock is behind, counts as now. A missing table is off, as
between the swap of a restore of an older backup and init_db, which makes it; a table of another
layout is on while it has any row. The setup stays reachable, so the requirement locks no one out,
and an administrator with two-factor authentication can always turn it off.
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
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

# The id of the row that turning it on inserts.
REQUIREMENT_ID = 1

# The statements of the rules above, on the table as stored. Any row is the requirement: turning it
# on inserts row 1 and turning it off deletes every row, whatever the table's layout.
_TABLE_EXISTS = "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'two_factor_requirement'"
_READ = "SELECT grace_days, enabled_at, enabled_by FROM two_factor_requirement LIMIT 1"
_ANY_ROW = "SELECT 1 FROM two_factor_requirement LIMIT 1"
_COLUMNS = "SELECT id, grace_days, enabled_at, enabled_by FROM two_factor_requirement WHERE 0"
DELETE_ALL = "DELETE FROM two_factor_requirement"

# Whether the supervisor's log already says that the table cannot be read: once, not at every
# request of every account without two-factor authentication (ADR-0214).
_warned = False


def _now() -> datetime:
    """The time the requirement is judged at: UTC without a zone, as the database keeps times
    (tests replace it)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass(frozen=True)
class Requirement:
    """The requirement as stored: ``enabled_at`` None when it cannot be read."""

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


def _stored_time(value: object) -> Optional[datetime]:
    """A stored time (SQLite's text, or a datetime) as UTC without a zone; None when it cannot be
    read, also an aware time at an end of the calendar, which has no UTC."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is not None:
        try:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        except (OverflowError, ValueError):
            return None
    return value


async def current(db: AsyncSession) -> Optional[Requirement]:
    """The requirement, or None while it is off."""
    global _warned
    try:
        row = (await db.execute(text(_READ))).first()
    except SQLAlchemyError as exc:
        if not await _holds_unreadable(db):
            return None
        if not _warned:
            _warned = True
            logger.warning("The two-factor requirement cannot be read; it holds without a grace period: %s", exc)
        return Requirement(grace_days=0, enabled_at=None, enabled_by=None)
    _warned = False
    if row is None:
        return None
    grace_days, enabled_at, enabled_by = row
    return Requirement(
        grace_days=_grace_days(grace_days),
        enabled_at=_stored_time(enabled_at),
        enabled_by=enabled_by if isinstance(enabled_by, str) else None,
    )


async def _holds_unreadable(db: AsyncSession) -> bool:
    """For a table whose columns cannot be read: a missing table is off, and one of another layout
    holds while it has any row. One that cannot be read at all holds."""
    try:
        if (await db.execute(text(_TABLE_EXISTS))).first() is None:
            return False
        return (await db.execute(text(_ANY_ROW))).first() is not None
    except SQLAlchemyError:
        return True


async def storable(db: AsyncSession) -> bool:
    """Whether turning it on can insert its row: the table exists with its columns."""
    try:
        await db.execute(text(_COLUMNS))
    except SQLAlchemyError:
        return False
    return True


def required_in(path: Path) -> Optional[bool]:
    """Whether the database file at ``path`` requires two-factor authentication for every account,
    by the rules of ``current``; None when it cannot be read. For a restore, which compares the live
    database with the one it brings back."""
    try:
        with closing(sqlite3.connect(str(path), timeout=15)) as conn:
            if conn.execute(_TABLE_EXISTS).fetchone() is None:
                return False
            return conn.execute(_ANY_ROW).fetchone() is not None
    except sqlite3.Error:
        return None


def deadline(requirement: Requirement, user: User) -> datetime:
    """The end of ``user``'s grace period: ``grace_days`` after the later of the requirement's start
    and the account's creation, each counted as now when it lies after now (a clock that is behind
    never skips the grace period). Without a readable start of the requirement, there is none."""
    if requirement.enabled_at is None:
        return datetime.min
    now = _now()
    start = min(requirement.enabled_at, now)
    created = _stored_time(user.created_at)
    if created is not None:
        start = max(start, min(created, now))
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
