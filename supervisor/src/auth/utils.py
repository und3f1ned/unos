import secrets
import hashlib
import json
from datetime import datetime, timedelta
from jose import jwt, JWTError
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from ..config import get_settings
from ..database import get_db
from .models import User, APIToken

settings = get_settings()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer()

# API Token prefix for identification
API_TOKEN_PREFIX = "unos_"


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=settings.jwt_expire_minutes)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def generate_api_token() -> tuple[str, str]:
    """Generate a new API token. Returns (full_token, token_hash)."""
    random_part = secrets.token_urlsafe(32)
    full_token = f"{API_TOKEN_PREFIX}{random_part}"
    token_hash = hashlib.sha256(full_token.encode()).hexdigest()
    return full_token, token_hash


def hash_api_token(token: str) -> str:
    """Hash an API token for storage/comparison."""
    return hashlib.sha256(token.encode()).hexdigest()


def generate_recovery_codes(count: int = 10) -> tuple[list[str], list[str]]:
    """Generate recovery codes. Returns (plain_codes, hashed_codes)."""
    plain_codes = []
    hashed_codes = []
    for _ in range(count):
        # Format: XXXX-XXXX-XXXX
        code = "-".join(secrets.token_hex(2).upper() for _ in range(3))
        plain_codes.append(code)
        hashed_codes.append(hashlib.sha256(code.encode()).hexdigest())
    return plain_codes, hashed_codes


def verify_recovery_code(code: str, hashed_codes: list[str]) -> int | None:
    """Verify a recovery code. Returns index if valid, None otherwise."""
    code_hash = hashlib.sha256(code.upper().replace("-", "").encode()).hexdigest()
    # Also try with dashes
    code_hash_with_dashes = hashlib.sha256(code.upper().encode()).hexdigest()

    for i, hashed in enumerate(hashed_codes):
        if hashed == code_hash or hashed == code_hash_with_dashes:
            return i
    return None


async def verify_token(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Verify JWT token or API token."""
    token = credentials.credentials

    # Check if it's an API token (starts with prefix)
    if token.startswith(API_TOKEN_PREFIX):
        return await verify_api_token(token, db)

    # Otherwise, treat as JWT
    try:
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
        username: str = payload.get("sub")
        if username is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
            )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        )

    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found"
        )
    return user


async def verify_api_token(token: str, db: AsyncSession) -> User:
    """Verify an API token and return the associated user."""
    token_hash = hash_api_token(token)

    result = await db.execute(
        select(APIToken).where(APIToken.token_hash == token_hash)
    )
    api_token = result.scalar_one_or_none()

    if api_token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API token"
        )

    if not api_token.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="API token is disabled"
        )

    # Check expiration
    if api_token.expires_at and api_token.expires_at < datetime.utcnow():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="API token has expired"
        )

    # Update last used timestamp
    api_token.last_used_at = datetime.utcnow()
    await db.commit()

    # Get the associated user
    result = await db.execute(select(User).where(User.id == api_token.user_id))
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found"
        )

    return user
