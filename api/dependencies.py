from collections.abc import Iterable
import logging

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from core.config import settings
from database import async_session
from models.user import User
from schemas.auth import TokenPayload

from models.roles import UserRole

logger = logging.getLogger(__name__)

# Use HTTPBearer for a simple token input in the Swagger UI "Authorize" dialog.
# This separates the login flow (getting the token) from the authorization flow (using the token).
oauth2_scheme = HTTPBearer(auto_error=False)


async def get_db() -> AsyncSession:
    """Dependency to get an async database session."""
    async with async_session() as session:
        yield session


async def get_current_user(
    auth: HTTPAuthorizationCredentials | None = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Dependency to get the current authenticated user."""
    if auth is None or auth.scheme != "Bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = auth.credentials
    try:
        payload = jwt.decode(
            token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM]
        )
        token_data = TokenPayload(**payload)
        if token_data.token_type != "access":
            raise JWTError("Invalid token type")
    except (JWTError, ValidationError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    result = await db.execute(select(User).where(User.email == token_data.sub))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


def require_roles(allowed_roles: Iterable[UserRole]):
    def role_checker(current_user: User = Depends(get_current_user)) -> User:
        logger.debug(
            "Role check for user=%s role=%s allowed_roles=%s",
            current_user.email,
            current_user.role,
            allowed_roles,
        )
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to perform this action",
            )
        return current_user

    return role_checker





