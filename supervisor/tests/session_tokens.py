"""Session tokens for tests: a JWT whose session row exists, as a sign-in makes it (ADR-0329)."""

import secrets
from datetime import datetime, timedelta, UTC

import jwt
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from src.auth.models import User, UserSession
from src.auth.utils import create_access_token
from src.config import get_settings


def session_token(engine, username: str, *, expires: int | None = None) -> str:
    """A token of a new session of ``username``, whose row goes into the database of the sync
    ``engine``; the user must exist. ``expires``, a Unix time, overrides the usual expiry."""
    settings = get_settings()
    session_id = secrets.token_urlsafe(24)
    if expires is None:
        expires_at = datetime.now(UTC) + timedelta(minutes=settings.jwt_expire_minutes)
    else:
        expires_at = datetime.fromtimestamp(expires, UTC)
    # A connection of its own, closed afterwards: one left in the pool of ``engine`` would keep
    # SQLite's write-ahead log from its checkpoint, and tests that copy the database file would
    # copy less than it holds.
    own = create_engine(engine.url, poolclass=NullPool)
    try:
        with Session(own) as session:
            user_id = session.scalars(select(User.id).where(User.username == username)).one()
            session.add(UserSession(id=session_id, user_id=user_id, expires_at=expires_at.replace(tzinfo=None)))
            session.commit()
    finally:
        own.dispose()
    if expires is None:
        return create_access_token({"sub": username, "jti": session_id})
    return jwt.encode({"sub": username, "jti": session_id, "exp": expires}, settings.jwt_secret,
                      algorithm=settings.jwt_algorithm)


def session_headers(engine, username: str) -> dict[str, str]:
    """The Authorization header of a new session of ``username``."""
    return {"Authorization": f"Bearer {session_token(engine, username)}"}
