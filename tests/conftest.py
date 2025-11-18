"""Pytest configuration and fixtures for Polyphony tests"""

import pytest
import asyncio
from typing import AsyncGenerator, Generator
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool
from fastapi.testclient import TestClient
import os

# Set test environment before importing settings
os.environ["POSTGRES_PASSWORD"] = "test_password_12345"
os.environ["GROQ_API_KEY"] = "test_key_for_testing"
os.environ["SECRET_KEY"] = "test_secret_key_minimum_32_characters_long_12345"

from services.shared.database import Base, get_db
from services.shared.config import settings
from services.shared.orm_models import User, Manuscript, Character, Scene
from services.api_gateway.main import app


# Test database URL
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(scope="session")
def event_loop() -> Generator:
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="function")
async def async_engine():
    """Create async test database engine"""
    engine = create_async_engine(
        TEST_DATABASE_URL,
        echo=False,
        poolclass=NullPool,
    )

    # Create all tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    # Drop all tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()


@pytest.fixture(scope="function")
async def async_session(async_engine) -> AsyncGenerator[AsyncSession, None]:
    """Create async test database session"""
    async_session_maker = async_sessionmaker(
        async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with async_session_maker() as session:
        yield session
        await session.rollback()


@pytest.fixture(scope="function")
def client(async_session):
    """Create test client with database override"""

    async def override_get_db():
        yield async_session

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


@pytest.fixture
async def test_user(async_session: AsyncSession) -> User:
    """Create a test user"""
    from services.shared.auth import get_password_hash

    user = User(
        email="test@example.com",
        hashed_password=get_password_hash("testpassword123"),
        full_name="Test User"
    )

    async_session.add(user)
    await async_session.commit()
    await async_session.refresh(user)

    return user


@pytest.fixture
async def auth_headers(test_user: User) -> dict:
    """Get authentication headers for test user"""
    from services.shared.auth import create_access_token

    access_token = create_access_token(data={"sub": str(test_user.id)})

    return {"Authorization": f"Bearer {access_token}"}


@pytest.fixture
async def test_manuscript(async_session: AsyncSession, test_user: User) -> Manuscript:
    """Create a test manuscript"""
    manuscript = Manuscript(
        user_id=test_user.id,
        title="Test Manuscript",
        author="Test Author",
        word_count=1000,
        status="completed"
    )

    async_session.add(manuscript)
    await async_session.commit()
    await async_session.refresh(manuscript)

    return manuscript


@pytest.fixture
async def test_character(async_session: AsyncSession, test_manuscript: Manuscript) -> Character:
    """Create a test character"""
    character = Character(
        manuscript_id=test_manuscript.id,
        name="Test Character",
        description="A test character",
        dialogue_count=10
    )

    async_session.add(character)
    await async_session.commit()
    await async_session.refresh(character)

    return character


@pytest.fixture
def mock_groq_response():
    """Mock Groq API response"""
    return {
        "choices": [{
            "message": {
                "content": "This is a test response from the LLM."
            }
        }]
    }


@pytest.fixture
def sample_scene_request(test_manuscript: Manuscript):
    """Sample scene generation request"""
    return {
        "manuscript_id": str(test_manuscript.id),
        "characters": ["Hermione", "Harry", "Ron"],
        "scene_description": "Study session in library",
        "setting": "Hogwarts library",
        "emotional_tone": "focused",
        "target_word_count": 500
    }
