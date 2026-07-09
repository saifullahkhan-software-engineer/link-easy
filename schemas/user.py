from datetime import datetime
from pydantic import BaseModel, EmailStr

from models.roles import UserRole


class UserBase(BaseModel):
    """Base schema for user details, to be shared across other schemas."""
    first_name: str
    last_name: str
    email: EmailStr
    is_verified: bool
    role: UserRole
    created_at: datetime
    updated_at: datetime | None = None


class UserResponse(UserBase):
    class Config:
        from_attributes = True


class UserUpdate(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    password: str | None = None
    is_verified: bool | None = None
    role: UserRole | None = None


class UserDeleteResponse(BaseModel):
    message: str
