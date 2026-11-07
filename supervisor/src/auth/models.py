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
    # Recovery codes for 2FA: a JSON array of their SHA-256 hashes, null once used (ADR-0232)
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
