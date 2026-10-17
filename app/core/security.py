"""Authentication utilities for Polyphony"""

import hashlib
import uuid
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text

from .config import settings
from .logging_config import log_error, setup_logging
from .orm_models import RefreshToken, User as UserORM
from .database import get_db

logger = setup_logging("core.security")

# Password hashing with strong configuration (P1-4 fix)
pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto",
    bcrypt__rounds=14,  # Increased from default 12 for better security
    bcrypt__ident="2b",  # Use 2b variant for better compatibility
)

# OAuth2 scheme
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash"""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Hash a password"""
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    Create a JWT access token

    Args:
        data: Data to encode in the token (typically {"sub": user_id})
        expires_delta: Token expiration time

    Returns:
        Encoded JWT token
    """
    to_encode = data.copy()

    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(
            minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
        )

    to_encode.update({"exp": expire})

    encoded_jwt = jwt.encode(
        to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM
    )

    return encoded_jwt


def decode_access_token(token: str) -> Optional[str]:
    """
    Decode a JWT token and return the user ID

    Args:
        token: JWT token string

    Returns:
        User ID if valid, None otherwise
    """
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        user_id: str = payload.get("sub")

        if user_id is None:
            return None

        return user_id

    except JWTError:
        return None


async def get_current_user(
    token: str = Depends(oauth2_scheme), db: AsyncSession = Depends(get_db)
) -> UserORM:
    """
    Dependency to get the current authenticated user

    Args:
        token: JWT token from Authorization header
        db: Database session

    Returns:
        Current user object

    Raises:
        HTTPException: If token is invalid or user not found
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    # Decode token
    user_id = decode_access_token(token)
    if user_id is None:
        raise credentials_exception

    try:
        user_uuid = uuid.UUID(user_id)
    except (ValueError, AttributeError):
        raise credentials_exception

    # Get user from database
    try:
        result = await db.execute(select(UserORM).where(UserORM.id == user_uuid))
        user = result.scalar_one_or_none()
    except HTTPException:
        raise
    except Exception:
        raise credentials_exception

    if user is None:
        raise credentials_exception

    return user


async def get_current_active_user(
    current_user: UserORM = Depends(get_current_user),
) -> UserORM:
    """
    Dependency to get the current active user

    Raises:
        HTTPException: If the account has been deactivated
    """
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated",
        )
    return current_user


async def get_current_admin_user(
    current_user: UserORM = Depends(get_current_active_user),
) -> UserORM:
    """Dependency requiring the admin role."""
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return current_user


# --- Refresh tokens -----------------------------------------------------------
# Opaque 256-bit random tokens, stored hashed. Each login starts a family;
# /refresh rotates within it. Reuse of a rotated token revokes all of the
# user's tokens (theft), except within REFRESH_REUSE_GRACE_SECONDS of its
# rotation, when it gets a fresh token in the same family (an aborted /refresh
# left the browser with the old cookie). Logout ends the family, siblings
# included; an ended token is never honoured.

REFRESH_COOKIE_NAME = "polyphony_refresh"


def _hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _utc(at: Optional[datetime]) -> Optional[datetime]:
    return at.replace(tzinfo=timezone.utc) if at and at.tzinfo is None else at


async def issue_refresh_token(
    db: AsyncSession, user: UserORM, user_agent: str = "", family_id=None
) -> str:
    """Create and persist a refresh token; returns the raw token for the cookie.
    A new login starts a family; a rotation passes its predecessor's."""
    raw = secrets.token_urlsafe(32)
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=_hash_refresh_token(raw),
            family_id=family_id or uuid.uuid4(),
            expires_at=datetime.now(timezone.utc)
            + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
            user_agent=user_agent[:500],
        )
    )
    await db.flush()
    return raw


async def _by_raw(db: AsyncSession, raw_token: str) -> Optional[RefreshToken]:
    result = await db.execute(
        select(RefreshToken).where(
            RefreshToken.token_hash == _hash_refresh_token(raw_token)
        )
    )
    return result.scalar_one_or_none()


async def _lock_user(db: AsyncSession, user_id) -> None:
    """Serialize every refresh-token change of one user (rotation, logout,
    theft revocation) until the transaction ends: a Postgres advisory lock,
    re-entrant within a transaction; a no-op on SQLite, which the unit tests use.
    One user refreshes rarely, so one lock per user costs nothing and covers
    every lineage, pre-0020 tokens included."""
    if db.get_bind().dialect.name == "postgresql":
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
            {"k": f"refresh:{user_id}"},
        )


async def _lock_and_reload(db: AsyncSession, stored: RefreshToken) -> None:
    """Take the user's lock and re-read the token under it, so a rotation or
    logout that committed meanwhile is seen."""
    await _lock_user(db, stored.user_id)
    await db.refresh(stored)


async def rotate_refresh_token(
    db: AsyncSession, raw_token: str, user_agent: str = ""
) -> Optional[tuple[UserORM, str]]:
    """Validate + rotate a refresh token.

    Returns (user, new_raw_token) or None. An ended token (logout, theft) is
    refused. A rotated token presented again within the grace window gets a
    fresh token in its family, even past its natural expiry; after the window
    it is theft and every token of the user is ended. A rotated pre-0020 token
    has no family, so it is never graced. The caller must commit even on None,
    or the theft revocation is rolled back.
    """
    stored = await _by_raw(db, raw_token)
    if stored is None:
        return None
    await _lock_and_reload(db, stored)  # a logout may have ended it meanwhile
    if stored.ended_at is not None:
        return None
    now = datetime.now(timezone.utc)
    rotated_at = _utc(stored.revoked_at)
    grace = timedelta(seconds=settings.REFRESH_REUSE_GRACE_SECONDS)
    if rotated_at is not None and (
        stored.family_id is None or now - rotated_at >= grace
    ):
        await revoke_all_refresh_tokens(db, stored.user_id)
        return None
    if rotated_at is None and _utc(stored.expires_at) <= now:
        return None

    user = (
        await db.execute(select(UserORM).where(UserORM.id == stored.user_id))
    ).scalar_one_or_none()
    if user is None or not user.is_active:
        return None

    stored.family_id = stored.family_id or uuid.uuid4()  # rows from before 0020
    stored.revoked_at = rotated_at or now
    new_raw = await issue_refresh_token(db, user, user_agent, stored.family_id)
    return user, new_raw


async def revoke_refresh_token(db: AsyncSession, raw_token: str) -> None:
    """Logout: end the presented token's whole family, siblings included."""
    stored = await _by_raw(db, raw_token)
    if stored is None:
        return
    # Re-read under the lineage lock, so a rotation that won the race has its
    # new family (and that family's lock) taken too.
    await _lock_and_reload(db, stored)
    now = datetime.now(timezone.utc)
    if stored.family_id is None:
        stored.ended_at = stored.ended_at or now
        return
    grace = timedelta(seconds=settings.REFRESH_REUSE_GRACE_SECONDS)
    family = await db.execute(  # after the lock: sees a graced sibling
        select(RefreshToken)
        .where(
            RefreshToken.family_id == stored.family_id,
            RefreshToken.ended_at.is_(None),
            RefreshToken.expires_at > now - grace,  # older rows are dead
        )
        .execution_options(populate_existing=True)
    )
    for token in family.scalars().all():
        token.ended_at = now


async def revoke_all_refresh_tokens(db: AsyncSession, user_id) -> None:
    """Theft: end every token of the user that is live or still graceable.
    Expired rows past any grace are already dead and are left alone."""
    await _lock_user(db, user_id)  # a concurrent rotation's successor is seen
    now = datetime.now(timezone.utc)
    grace = timedelta(seconds=settings.REFRESH_REUSE_GRACE_SECONDS)
    result = await db.execute(
        select(RefreshToken)
        .execution_options(populate_existing=True)
        .where(
            RefreshToken.user_id == user_id,
            RefreshToken.ended_at.is_(None),
            RefreshToken.expires_at > now - grace,
        )
    )
    for token in result.scalars().all():
        token.ended_at = now


async def authenticate_user(
    db: AsyncSession, email: str, password: str
) -> Optional[UserORM]:
    """
    Authenticate a user by email and password

    Args:
        db: Database session
        email: User email
        password: Plain text password

    Returns:
        User object if authentication successful, None otherwise
    """
    try:
        # Get user by email
        result = await db.execute(select(UserORM).where(UserORM.email == email))
        user = result.scalar_one_or_none()

        if user is None:
            return None

        # Verify password
        if not verify_password(password, user.hashed_password):
            return None

        return user

    except Exception as e:
        # Deliberately no email in the log context (PII).
        log_error(logger, e, context={"event": "authenticate_user_error"})
        return None
