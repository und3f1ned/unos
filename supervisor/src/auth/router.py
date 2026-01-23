from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from ..database import get_db
from .models import User
from .schemas import UserCreate, UserResponse, Token, LoginRequest, PasswordChange, UserUpdate
from .utils import hash_password, verify_password, create_access_token, verify_token

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/setup", response_model=UserResponse)
async def setup_admin(user: UserCreate, db: AsyncSession = Depends(get_db)):
    """Create initial admin user. Only works if no users exist."""
    result = await db.execute(select(func.count()).select_from(User))
    count = result.scalar()
    if count > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Admin user already exists. Use /login instead.",
        )

    db_user = User(
        username=user.username,
        hashed_password=hash_password(user.password),
        is_admin=True,
    )
    db.add(db_user)
    await db.commit()
    await db.refresh(db_user)
    return db_user


@router.post("/login", response_model=Token)
async def login(request: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.username == request.username))
    user = result.scalar_one_or_none()

    if not user or not verify_password(request.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )

    access_token = create_access_token(data={"sub": user.username})
    return Token(access_token=access_token)


@router.get("/me", response_model=UserResponse)
async def get_current_user(user: User = Depends(verify_token)):
    return user


@router.get("/status")
async def get_auth_status(db: AsyncSession = Depends(get_db)):
    """Check if setup is required (no users exist)."""
    result = await db.execute(select(func.count()).select_from(User))
    count = result.scalar()
    return {"setup_required": count == 0}


@router.post("/change-password")
async def change_password(
    data: PasswordChange,
    current_user: User = Depends(verify_token),
    db: AsyncSession = Depends(get_db)
):
    """Change current user's password."""
    if not verify_password(data.current_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    current_user.hashed_password = hash_password(data.new_password)
    await db.commit()
    return {"status": "ok", "message": "Password changed successfully"}


# User management (admin only)
@router.get("/users", response_model=list[UserResponse])
async def list_users(
    current_user: User = Depends(verify_token),
    db: AsyncSession = Depends(get_db)
):
    """List all users (admin only)."""
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")

    result = await db.execute(select(User))
    return result.scalars().all()


@router.post("/users", response_model=UserResponse)
async def create_user(
    user: UserCreate,
    current_user: User = Depends(verify_token),
    db: AsyncSession = Depends(get_db)
):
    """Create a new user (admin only)."""
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")

    # Check if username exists
    result = await db.execute(select(User).where(User.username == user.username))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Username already exists")

    db_user = User(
        username=user.username,
        hashed_password=hash_password(user.password),
        is_admin=False,
    )
    db.add(db_user)
    await db.commit()
    await db.refresh(db_user)
    return db_user


@router.put("/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: int,
    data: UserUpdate,
    current_user: User = Depends(verify_token),
    db: AsyncSession = Depends(get_db)
):
    """Update a user (admin only)."""
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if data.is_admin is not None:
        user.is_admin = data.is_admin
    if data.password:
        user.hashed_password = hash_password(data.password)

    await db.commit()
    await db.refresh(user)
    return user


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: int,
    current_user: User = Depends(verify_token),
    db: AsyncSession = Depends(get_db)
):
    """Delete a user (admin only)."""
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")

    if current_user.id == user_id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    await db.delete(user)
    await db.commit()
    return {"status": "ok", "message": "User deleted"}
