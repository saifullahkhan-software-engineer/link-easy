from sqlalchemy import Boolean, Column, DateTime, Integer, String
from sqlalchemy.sql import func

from database import Base
from models.roles import UserRole


class User(Base):
    __tablename__ = "users"

    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    email = Column(String, primary_key=True, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    is_verified = Column(Boolean, default=False, nullable=False)
    verification_code = Column(String, nullable=True)
    verification_code_expires_at = Column(DateTime(timezone=True), nullable=True)
    verification_attempt_count = Column(Integer, default=0, nullable=False)
    verification_attempt_window_start = Column(DateTime(timezone=True), nullable=True)
    role = Column(String, default=UserRole.CUSTOMER.value, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    token_id = Column(String, primary_key=True, unique=True, index=True, nullable=False)
    email = Column(String, index=True, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
