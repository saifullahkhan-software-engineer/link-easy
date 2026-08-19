from datetime import datetime
import re
from pydantic import BaseModel, EmailStr, field_validator
from models.roles import UserRole
from schemas.user import UserBase


class UserRegister(BaseModel):
    first_name: str
    last_name: str
    email: EmailStr
    password: str

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters long.")
        if not re.search("[a-z]", v):
            raise ValueError("Password must contain at least one lowercase letter.")
        if not re.search("[A-Z]", v):
            raise ValueError("Password must contain at least one uppercase letter.")
        if not re.search("[0-9]", v):
            raise ValueError("Password must contain at least one number.")
        if not re.search(r"[\W_]", v):
            raise ValueError("Password must contain at least one special character.")
        return v


class UserResponse(UserBase):
    """Response schema for user details, inheriting from a shared base."""

    class Config:
        from_attributes = True

class VerifyEmail(BaseModel):
    email: EmailStr
    code: str


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    password: str


class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RegisterResponse(BaseModel):
    message: str


class ResendVerificationRequest(BaseModel):
    email: EmailStr


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class TokenPayload(BaseModel):
    sub: EmailStr
    role: UserRole
    # Multi-role claim. Optional so refresh tokens minted before this change
    # still validate; ``role`` remains the single highest-privilege role.
    roles: list[str] = []
    token_type: str | None = None
