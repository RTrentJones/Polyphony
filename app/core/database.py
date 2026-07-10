"""Database utilities for Polyphony"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import declarative_base
from typing import AsyncGenerator, Optional
from contextlib import asynccontextmanager

from .config import settings

# SQLAlchemy Base
Base = declarative_base()

# Import all ORM models to register them with Base
try:
    from . import orm_models  # noqa: F401
except ImportError:
    # ORM models not yet created
    pass


def get_async_db_url() -> str:
    """Async database URL. A full DATABASE_URL (e.g. Neon DSN) wins over components."""
    if settings.DATABASE_URL:
        url = settings.DATABASE_URL
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        # asyncpg rejects libpq-style sslmode/channel_binding query params; it
        # negotiates TLS itself (Neon DSNs carry both).
        if "+asyncpg" in url and "?" in url:
            base, _, query = url.partition("?")
            kept = [
                p
                for p in query.split("&")
                if p and not p.startswith(("sslmode=", "channel_binding="))
            ]
            url = base + ("?" + "&".join(kept) if kept else "")
        return url
    return (
        f"postgresql+asyncpg://{settings.POSTGRES_USER}:{settings.POSTGRES_PASSWORD}"
        f"@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}"
    )


def get_sync_db_url() -> str:
    """Sync database URL (Alembic)."""
    return get_async_db_url().replace("+asyncpg", "")


# Lazily-created engine/session factory so importing this module never requires
# a reachable database (tests build their own sqlite engine).
_engine: Optional[AsyncEngine] = None
_session_factory: Optional[async_sessionmaker] = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        # Small pool: one long-lived container against Neon's direct (non-pooled)
        # endpoint — Neon free tier allows ~100 connections, we need few.
        _engine = create_async_engine(
            get_async_db_url(),
            echo=settings.DEBUG,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
            pool_recycle=3600,
            pool_timeout=30,
        )
    return _engine


def get_session_factory() -> async_sessionmaker:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autocommit=False,
            autoflush=False,
        )
    return _session_factory


# Dependency for FastAPI
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding a session that commits on success."""
    async with get_session_factory()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


@asynccontextmanager
async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """Context manager for manual session management outside of FastAPI."""
    async with get_session_factory()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def check_db_connection() -> bool:
    """Check if database is accessible"""
    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
