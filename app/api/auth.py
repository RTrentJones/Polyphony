"""Authentication endpoints.

Registration is invite-gated (the deployment shares one LLM quota), sessions
are short-lived access JWTs plus rotating refresh tokens in an httpOnly
same-site cookie (the frontend is served same-origin).
"""

import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import settings
from app.core.database import get_db
from app.core.orm_models import InviteCode, User as UserORM
from app.core.security import (
    REFRESH_COOKIE_NAME,
    authenticate_user,
    create_access_token,
    get_current_active_user,
    get_current_admin_user,
    get_password_hash,
    issue_refresh_token,
    revoke_refresh_token,
    rotate_refresh_token,
)

router = APIRouter()
limiter = Limiter(key_func=get_remote_address)


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=10, max_length=128)
    full_name: Optional[str] = None
    invite_code: str = Field(..., min_length=1, max_length=64)


class InviteCreate(BaseModel):
    max_uses: int = Field(default=1, ge=1, le=100)
    expires_in_days: Optional[int] = Field(default=30, ge=1, le=365)


def _set_refresh_cookie(response: Response, raw_token: str) -> None:
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=raw_token,
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 3600,
        httponly=True,
        secure=settings.ENVIRONMENT == "production",
        samesite="strict",
        path="/api/v1/auth",
    )


async def _consume_invite(db: AsyncSession, code: str) -> InviteCode:
    """Validate an invite code; increments uses atomically with the caller's txn."""
    result = await db.execute(
        select(InviteCode).where(InviteCode.code == code).with_for_update()
    )
    invite = result.scalar_one_or_none()
    if invite is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Invalid invite code"
        )
    expires_at = invite.expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at is not None and expires_at <= datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Invite code has expired"
        )
    if invite.uses >= invite.max_uses:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invite code has been fully used",
        )
    invite.uses += 1
    return invite


@router.post("/register", response_model=dict)
@limiter.limit("3/hour")
async def register(
    request: Request,
    response: Response,
    user_data: RegisterRequest,
    db: AsyncSession = Depends(get_db),
):
    """Register with email, password, and a valid invite code."""
    existing = await db.execute(select(UserORM).where(UserORM.email == user_data.email))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered"
        )

    invite = await _consume_invite(db, user_data.invite_code)

    new_user = UserORM(
        email=user_data.email,
        hashed_password=get_password_hash(user_data.password),
        full_name=user_data.full_name,
        invite_code_id=invite.id,
        is_active=True,
    )
    db.add(new_user)
    await db.flush()

    access_token = create_access_token(
        data={"sub": str(new_user.id)},
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    refresh_raw = await issue_refresh_token(
        db, new_user, request.headers.get("user-agent", "")
    )
    await db.commit()

    _set_refresh_cookie(response, refresh_raw)
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": {
            "id": str(new_user.id),
            "email": new_user.email,
            "full_name": new_user.full_name,
        },
    }


@router.post("/login", response_model=dict)
@limiter.limit("5/minute")
async def login(
    request: Request,
    response: Response,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    """Login with email + password; sets the refresh cookie."""
    user = await authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Account is deactivated"
        )

    access_token = create_access_token(
        data={"sub": str(user.id)},
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    refresh_raw = await issue_refresh_token(
        db, user, request.headers.get("user-agent", "")
    )
    await db.commit()

    _set_refresh_cookie(response, refresh_raw)
    return {"access_token": access_token, "token_type": "bearer"}


@router.post("/refresh", response_model=dict)
async def refresh(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """Rotate the refresh token and mint a new access token."""
    raw = request.cookies.get(REFRESH_COOKIE_NAME)
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="No refresh token"
        )
    rotated = await rotate_refresh_token(db, raw, request.headers.get("user-agent", ""))
    if rotated is None:
        response.delete_cookie(REFRESH_COOKIE_NAME, path="/api/v1/auth")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token"
        )
    user, new_raw = rotated
    await db.commit()

    access_token = create_access_token(
        data={"sub": str(user.id)},
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    _set_refresh_cookie(response, new_raw)
    return {"access_token": access_token, "token_type": "bearer"}


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """Revoke the presented refresh token and clear the cookie."""
    raw = request.cookies.get(REFRESH_COOKIE_NAME)
    if raw:
        await revoke_refresh_token(db, raw)
        await db.commit()
    response.delete_cookie(REFRESH_COOKIE_NAME, path="/api/v1/auth")
    return None


@router.get("/me", response_model=dict)
async def get_current_user_info(
    current_user: UserORM = Depends(get_current_active_user),
):
    """Current user info."""
    return {
        "id": str(current_user.id),
        "email": current_user.email,
        "full_name": current_user.full_name,
        "role": current_user.role,
        "created_at": (
            current_user.created_at.isoformat() if current_user.created_at else None
        ),
    }


@router.post("/invites", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_invite(
    payload: InviteCreate,
    current_user: UserORM = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Mint an invite code (admin only)."""
    invite = InviteCode(
        code=secrets.token_urlsafe(12),
        created_by=current_user.id,
        max_uses=payload.max_uses,
        expires_at=(
            datetime.now(timezone.utc) + timedelta(days=payload.expires_in_days)
            if payload.expires_in_days
            else None
        ),
    )
    db.add(invite)
    await db.commit()
    await db.refresh(invite)
    return {
        "code": invite.code,
        "max_uses": invite.max_uses,
        "expires_at": invite.expires_at.isoformat() if invite.expires_at else None,
    }


@router.get("/invites", response_model=dict)
async def list_invites(
    current_user: UserORM = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """List invite codes (admin only)."""
    invites = (
        (await db.execute(select(InviteCode).order_by(InviteCode.created_at.desc())))
        .scalars()
        .all()
    )
    return {
        "invites": [
            {
                "code": i.code,
                "max_uses": i.max_uses,
                "uses": i.uses,
                "expires_at": i.expires_at.isoformat() if i.expires_at else None,
            }
            for i in invites
        ]
    }
