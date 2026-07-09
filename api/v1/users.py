from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from api.dependencies import get_current_user, get_db, require_roles
from core.security import hash_password
from models.roles import UserRole
from models.user import User
from schemas.user import UserDeleteResponse, UserResponse, UserUpdate

router = APIRouter(prefix="/api/v1/users", tags=["users"])


@router.get("/", response_model=list[UserResponse])
async def get_users(
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_roles([UserRole.ADMIN.value])),
):
    result = await db.execute(select(User))
    return result.scalars().all()


@router.get("/{email}", response_model=UserResponse)
async def get_user(
    email: str,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalars().first()

    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    return user


@router.patch("/{email}", response_model=UserResponse)
async def update_user(
    email: str,
    user_in: UserUpdate,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_roles([UserRole.ADMIN.value])),
):
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalars().first()

    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    update_data = user_in.model_dump(exclude_unset=True)
    if "first_name" in update_data:
        user.first_name = update_data["first_name"]
    if "last_name" in update_data:
        user.last_name = update_data["last_name"]
    if "password" in update_data:
        user.hashed_password = hash_password(update_data["password"])
    if "is_verified" in update_data:
        user.is_verified = update_data["is_verified"]
    if "role" in update_data:
        role = update_data["role"]
        user.role = role.value if hasattr(role, "value") else role

    await db.commit()
    await db.refresh(user)
    return user


@router.delete("/{email}", response_model=UserDeleteResponse)
async def delete_user(
    email: str,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_roles([UserRole.ADMIN.value])),
):
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalars().first()

    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    await db.delete(user)
    await db.commit()
    return {"message": "User deleted successfully"}
