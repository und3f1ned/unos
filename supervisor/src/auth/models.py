from datetime import datetime

from sqlalchemy import Integer, LargeBinary, String, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.sql import func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from ..database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String, nullable=False)
    is_admin: Mapped[bool | None] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, onupdate=func.now())
    # TOTP 2FA fields
    totp_secret: Mapped[str | None] = mapped_column(String, nullable=True)
    totp_enabled: Mapped[bool | None] = mapped_column(Boolean, default=False)
    # Recovery codes for 2FA: a JSON array of their SHA-256 hashes, null once used (ADR-0238)
    recovery_codes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationship to API tokens
    api_tokens: Mapped[list["APIToken"]] = relationship(
        "APIToken", back_populates="user", cascade="all, delete-orphan"
    )


class APIToken(Base):
    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String, nullable=False)  # User-friendly name for the token
    token_hash: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    token_prefix: Mapped[str] = mapped_column(String, nullable=False)  # First 8 chars for identification
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    is_active: Mapped[bool | None] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # Optional expiration
    # The token's scope (src/auth/scopes.py, ADR-0392): "*" full, else "read" and the areas,
    # comma-separated. Deferred: a restored value that is not UTF-8 cannot be read as text, so the
    # queries that need it select scopes.stored_columns, and the others never load it.
    permissions: Mapped[str | None] = mapped_column(String, default="*", deferred=True)

    user: Mapped[User] = relationship("User", back_populates="api_tokens")


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

    id: Mapped[str] = mapped_column(String, primary_key=True)  # the token's jti
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    # When the token expires (UTC, without a zone, as SQLite keeps it): rows of expired
    # sessions go at the next sign-in.
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class UsernameTaken(Base):
    """When a rename last gave a name to a user (ADR-0368). The login history is kept by name, so
    a regular user sees the attempts on their name from the later of their account's creation and
    this time on (ADR-0263): a name that a rename freed shows its next holder none of the earlier
    holder's sign-ins. A rename writes the row of the new name in its commit. A row names no
    user, so a deletion leaves it: whoever holds the name later took it at their creation, which
    comes after, or by a rename, which writes the row again.
    """

    __tablename__ = "usernames_taken"

    username: Mapped[str] = mapped_column(String, primary_key=True)
    # SQLite's CURRENT_TIMESTAMP, as users.created_at and login_attempts.timestamp have it.
    taken_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class TwoFactorRequirement(Base):
    """Every account must have two-factor authentication: the one row (id 1) while an
    administrator requires it, none while it is off. An account without it has ``grace_days`` from
    the later of ``enabled_at`` and its creation to set it up; after that it is setup-only
    (src/auth/requirement.py), which reads the row of a restored database as untrusted."""

    __tablename__ = "two_factor_requirement"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    grace_days: Mapped[int] = mapped_column(Integer, nullable=False)
    # When it was turned on (UTC, without a zone, as SQLite keeps it).
    enabled_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    enabled_by: Mapped[str] = mapped_column(String, nullable=False)


class DashboardLayout(Base):
    """The order of a user's dashboard widgets and which are hidden (ADR-0395).

    A new table (ADR-0319): init_db's create_all makes it in a database that lacks it. One row per
    user who arranged the dashboard; none means the default layout. ``layout`` is the JSON list
    that src/auth/dashboard.py stores, read again as untrusted at every use (ADR-0144). SQLite
    enforces no foreign keys here, so deleting a user deletes the row in the same transaction.
    """

    __tablename__ = "dashboard_layouts"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    layout: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, server_default=func.now())


class UserTheme(Base):
    """The theme of the web UI that a user chose: dark, light or system (ADR-0405).

    A new table (ADR-0319): init_db's create_all makes it in a database that lacks it. One row per
    user who chose a theme; none means system. ``theme`` is read again as untrusted at every use
    (ADR-0144). SQLite enforces no foreign keys here, so deleting a user deletes the row in the same
    transaction.
    """

    __tablename__ = "user_themes"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    theme: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, server_default=func.now())


class SecurityKey(Base):
    """A security key (WebAuthn credential) that a user with two-factor authentication registered: it
    signs in at the code step in place of the authenticator app's code (ADR-0409).

    A new table (ADR-0319): init_db's create_all makes it in a database that lacks it. A backup holds
    it with the database, and a restore brings it back; its rows are read again as untrusted at every
    use (ADR-0144, src/auth/security_keys.py). SQLite enforces no foreign keys here, so deleting a
    user, and turning two-factor authentication off or on again, deletes the user's rows in the same
    transaction. ``origin`` and ``rp_id`` are the web UI's address at the registration: the key works
    there only, whatever a later request's Host says.
    """

    __tablename__ = "security_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    # The credential id in base64url without padding: unique across every user.
    credential_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    # The credential public key as the authenticator gave it (a COSE_Key), and its COSE algorithm.
    public_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    algorithm: Mapped[int] = mapped_column(Integer, nullable=False)
    # The signature counter of the last accepted sign-in; 0 for a key that keeps none.
    sign_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rp_id: Mapped[str] = mapped_column(String, nullable=False)
    origin: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
