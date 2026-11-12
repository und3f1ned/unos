from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from ..database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    is_admin = Column(Boolean, default=False)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, onupdate=func.now())
    # TOTP 2FA fields
    totp_secret = Column(String, nullable=True)
    totp_enabled = Column(Boolean, default=False)
    # Recovery codes for 2FA: a JSON array of their SHA-256 hashes, null once used (ADR-0238)
    recovery_codes = Column(Text, nullable=True)

    # Relationship to API tokens
    api_tokens = relationship("APIToken", back_populates="user", cascade="all, delete-orphan")


class APIToken(Base):
    __tablename__ = "api_tokens"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)  # User-friendly name for the token
    token_hash = Column(String, unique=True, index=True, nullable=False)
    token_prefix = Column(String, nullable=False)  # First 8 chars for identification
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())
    last_used_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)  # Optional expiration
    # Permissions (JSON string or comma-separated)
    permissions = Column(String, default="*")  # * for all, or specific like "read,containers"

    user = relationship("User", back_populates="api_tokens")


class UserSession(Base):
    """A session that a sign-in began: the jti of its JWT (ADR-0329).

    A session token is valid while its row exists. Signing out deletes its row, a password
    change the user's other rows, and an administrator's End sessions, a new password set by an
    administrator and the deletion of the user all of them.
    """

    __tablename__ = "user_sessions"
    # The rows live in their primary key's B-tree alone, without indexes: a sign-in writes fewer
    # pages (ADR-0308), and the table holds about a day of sessions, so a scan costs nothing.
    __table_args__ = {"sqlite_with_rowid": False}

    id = Column(String, primary_key=True)  # the token's jti
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    # When the token expires (UTC, without a zone, as SQLite keeps it): rows of expired
    # sessions go at the next sign-in.
    expires_at = Column(DateTime, nullable=False)


class UsernameTaken(Base):
    """When a rename last gave a name to a user (ADR-0368). The login history is kept by name, so
    a regular user sees the attempts on their name from the later of their account's creation and
    this time on (ADR-0263): a name that a rename freed shows its next holder none of the earlier
    holder's sign-ins. A rename writes the row of the new name in its commit. A row names no
    user, so a deletion leaves it: whoever holds the name later took it at their creation, which
    comes after, or by a rename, which writes the row again.
    """

    __tablename__ = "usernames_taken"

    username = Column(String, primary_key=True)
    # SQLite's CURRENT_TIMESTAMP, as users.created_at and login_attempts.timestamp have it.
    taken_at = Column(DateTime, nullable=False, server_default=func.now())
