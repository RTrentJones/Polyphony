"""Authentication utilities for Polyphony"""

from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from .config import settings
from .models import User as UserModel
from .orm_models import User as UserORM
from .database import get_db


# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

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
        expire = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

    to_encode.update({"exp": expire})

    encoded_jwt = jwt.encode(
        to_encode,
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM
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
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM]
        )
        user_id: str = payload.get("sub")

        if user_id is None:
            return None

        return user_id

    except JWTError:
        return None


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db)
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

    # Get user from database
    try:
        result = await db.execute(
            select(UserORM).where(UserORM.id == user_id)
        )
        user = result.scalar_one_or_none()

        if user is None:
            raise credentials_exception

        return user

    except Exception as e:
        print(f"Error fetching user: {e}")
        raise credentials_exception


async def get_current_active_user(
    current_user: UserORM = Depends(get_current_user)
) -> UserORM:
    """
    Dependency to get the current active user

    Args:
        current_user: Current user from get_current_user

    Returns:
        Current user if active

    Raises:
        HTTPException: If user is inactive
    """
    # Future: Add user.is_active field if needed
    return current_user


async def authenticate_user(
    db: AsyncSession,
    email: str,
    password: str
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
        result = await db.execute(
            select(UserORM).where(UserORM.email == email)
        )
        user = result.scalar_one_or_none()

        if user is None:
            return None

        # Verify password
        if not verify_password(password, user.hashed_password):
            return None

        return user

    except Exception as e:
        print(f"Error authenticating user: {e}")
        return None
