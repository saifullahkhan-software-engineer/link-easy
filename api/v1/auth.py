from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Response, status
from jose import JWTError, jwt
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from api.dependencies import get_current_user, get_db
from api.rate_limit_deps import rate_limit
from core.config import settings
from core.email import (
    EmailDeliveryError,
    send_password_reset_email,
    send_verification_email,
)
from core.security import (
    create_access_token,
    create_password_reset_token,
    create_refresh_token,
    generate_5_digit_code,
    generate_token_id,
    hash_password,
    verify_password,
)
from models.roles import UserRole
from models.user import PasswordResetToken, User
from schemas.auth import (
    RefreshTokenRequest,
    ForgotPasswordRequest,
    RegisterResponse,
    ResetPasswordRequest,
    Token,
    ResendVerificationRequest,
    TokenPayload,
    UserLogin,
    UserRegister,
    VerifyEmail,
)
from services.user_roles import get_user_roles, primary_role

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

VERIFICATION_CODE_TTL_MINUTES = 15
VERIFICATION_ATTEMPT_LIMIT = 5
VERIFICATION_ATTEMPT_WINDOW_MINUTES = 15
PASSWORD_RESET_MESSAGE = (
    "If an account exists for this email, a password reset link has been sent"
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


@router.post(
    "/register",
    response_model=RegisterResponse,
    response_model_exclude_none=True,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit("auth:register"))],
)
async def register( user_in: UserRegister, response: Response, db: AsyncSession = Depends(get_db),):
    result = await db.execute(select(User).where(User.email == user_in.email))
    existing_user = result.scalars().first()
    if existing_user is not None:
        response.status_code = status.HTTP_200_OK
        return {"message": "User already exists"}

    verification_code = generate_5_digit_code()
    now = _utc_now()
    user = User(
        first_name=user_in.first_name,
        last_name=user_in.last_name,
        email=user_in.email,
        hashed_password=hash_password(user_in.password),
        is_verified=False,
        verification_code=verification_code,
        verification_code_expires_at=now
        + timedelta(minutes=VERIFICATION_CODE_TTL_MINUTES),
        verification_attempt_count=0,
        verification_attempt_window_start=now,
        role=UserRole.CUSTOMER.value,
        created_at=now,
        updated_at=None,
    )

    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        response.status_code = status.HTTP_200_OK
        return {"message": "User already exists"}

    try:
        await send_verification_email(user.email, verification_code)
    except EmailDeliveryError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Account created, but verification email could not be sent",
        )

    return {"message": "Verification code sent to email"}


@router.post("/verify-email", dependencies=[Depends(rate_limit("auth:verify-email"))])
async def verify_email(data: VerifyEmail, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == data.email))
    user = result.scalars().first()

    if user is None:
        raise HTTPException(status_code=400, detail="Invalid code or email")

    now = _utc_now()
    window_start = _ensure_aware(user.verification_attempt_window_start)
    if (
        window_start is None
        or window_start + timedelta(minutes=VERIFICATION_ATTEMPT_WINDOW_MINUTES) <= now
    ):
        user.verification_attempt_window_start = now
        user.verification_attempt_count = 0

    if user.verification_attempt_count >= VERIFICATION_ATTEMPT_LIMIT:
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many verification attempts. Please try again later.",
        )

    user.verification_attempt_count += 1

    code_expires_at = _ensure_aware(user.verification_code_expires_at)
    if code_expires_at is None or code_expires_at <= now:
        await db.commit()
        raise HTTPException(status_code=400, detail="Code expired")

    if user.verification_code != data.code:
        await db.commit()
        raise HTTPException(status_code=400, detail="Invalid code or email")

    user.is_verified = True
    user.verification_code = None
    user.verification_code_expires_at = None
    user.verification_attempt_count = 0
    user.verification_attempt_window_start = None
    await db.commit()
    return {"message": "Account verified successfully"}


@router.post(
    "/resend-verification",
    dependencies=[Depends(rate_limit("auth:resend-verification"))],
)
async def resend_verification(
    data: ResendVerificationRequest, db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(User).where(User.email == data.email))
    user = result.scalars().first()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    if user.is_verified:
        return {"message": "Account already verified"}

    verification_code = generate_5_digit_code()
    now = _utc_now()
    user.verification_code = verification_code
    user.verification_code_expires_at = now + timedelta(
        minutes=VERIFICATION_CODE_TTL_MINUTES
    )
    user.verification_attempt_count = 0
    user.verification_attempt_window_start = now

    try:
        await send_verification_email(user.email, verification_code)
    except EmailDeliveryError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Verification email could not be sent",
        )
    await db.commit()
    return {"message": "Verification code sent to email"}


@router.post(
    "/login",
    response_model=Token,
    dependencies=[Depends(rate_limit("auth:login"))],
)
async def login(credentials: UserLogin, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == credentials.email))
    user = result.scalars().first()

    if user is None or not verify_password(credentials.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not user.is_verified:
        raise HTTPException(status_code=403, detail="Please verify your email first")

    # ``role`` stays for backwards compatibility with older clients and the
    # legacy require_roles dependency; ``roles`` is the real list, so a user who
    # is both an admin and a customer keeps both in the token.
    roles = await get_user_roles(db, user.email)
    subject = {
        "sub": user.email,
        "role": primary_role(roles),
        "roles": roles,
    }
    # Both tokens receive the same absolute deadline.  Refreshing the access
    # token must never start a new two-hour session window.
    session_expires_at = _utc_now() + timedelta(minutes=settings.SESSION_EXPIRE_MINUTES)
    access_token = create_access_token(subject, session_expires_at)
    refresh_token = create_refresh_token(subject, session_expires_at)

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
    }


@router.post(
    "/forgot-password",
    dependencies=[Depends(rate_limit("auth:forgot-password"))],
)
async def forgot_password(data: ForgotPasswordRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == data.email))
    user = result.scalars().first()

    if user is None:
        return {"message": PASSWORD_RESET_MESSAGE}

    reset_token_id = generate_token_id()
    reset_token = create_password_reset_token(user.email, reset_token_id)
    db.add(
        PasswordResetToken(
            token_id=reset_token_id,
            email=user.email,
            expires_at=_utc_now()
            + timedelta(minutes=settings.PASSWORD_RESET_TOKEN_EXPIRE_MINUTES),
        )
    )
    await db.commit()
    reset_link = f"{settings.PASSWORD_RESET_URL}?token={reset_token}"

    try:
        await send_password_reset_email(user.email, reset_link)
    except EmailDeliveryError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Password reset email could not be sent",
        )

    return {"message": PASSWORD_RESET_MESSAGE}


@router.post("/resend-password-reset")
async def resend_password_reset(
    data: ForgotPasswordRequest, db: AsyncSession = Depends(get_db)
):
    return await forgot_password(data, db)

@router.post(
    "/reset-password",
    dependencies=[Depends(rate_limit("auth:reset-password"))],
)
async def reset_password(data: ResetPasswordRequest, db: AsyncSession = Depends(get_db)):
    try:
        payload = jwt.decode(
            data.token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
        )
        if payload.get("token_type") != "password_reset":
            raise JWTError("Invalid token type")
        email = payload.get("sub")
        token_id = payload.get("jti")
        if not email or not token_id:
            raise JWTError("Missing subject")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired reset token")

    result = await db.execute(
        select(PasswordResetToken).where(PasswordResetToken.token_id == token_id)
    )
    reset_token = result.scalars().first()
    if (
        reset_token is None
        or reset_token.email != email
        or _ensure_aware(reset_token.expires_at) <= _utc_now()
    ):
        raise HTTPException(status_code=401, detail="Invalid or expired reset token")

    result = await db.execute(select(User).where(User.email == email))
    user = result.scalars().first()
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid or expired reset token")

    user.hashed_password = hash_password(data.password)
    await db.delete(reset_token)
    await db.commit()
    return {"message": "Password reset successfully"}


@router.post("/refresh", response_model=Token)
async def refresh_token(data: RefreshTokenRequest, db: AsyncSession = Depends(get_db)):
    try:
        payload = jwt.decode(
            data.refresh_token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
        )
        token_data = TokenPayload(**payload)
        if token_data.token_type != "refresh":
            raise JWTError("Invalid token type")
    except (JWTError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    # A refresh token is also bounded by the original login's absolute
    # deadline.  Reject tokens without the claim so a refresh token issued by
    # an older deployment cannot silently keep a session alive for its legacy
    # seven-day lifetime.
    try:
        if token_data.session_expires_at is None:
            raise ValueError("missing session expiry")
        session_expires_at = datetime.fromtimestamp(
            float(token_data.session_expires_at), tz=timezone.utc
        )
        if session_expires_at <= _utc_now():
            raise ValueError("session expired")
    except (TypeError, ValueError, OverflowError, OSError):
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    result = await db.execute(select(User).where(User.email == token_data.sub))
    user = result.scalars().first()

    if user is None or not user.is_verified:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    # Re-read roles on refresh so a grant/revoke takes effect without forcing
    # the user to log out and back in.
    roles = await get_user_roles(db, user.email)
    subject = {
        "sub": user.email,
        "role": primary_role(roles),
        "roles": roles,
    }

    return {
        "access_token": create_access_token(subject, session_expires_at),
        "refresh_token": create_refresh_token(subject, session_expires_at),
        "token_type": "bearer",
    }
