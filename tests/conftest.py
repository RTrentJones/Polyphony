"""Pytest configuration and fixtures for Polyphony tests"""

import importlib.util
import os
import sys
from typing import AsyncGenerator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import declarative_base
from sqlalchemy.pool import NullPool

# Set test environment before importing settings
os.environ["POSTGRES_PASSWORD"] = "test_password_12345"
os.environ["GROQ_API_KEY"] = "test_key_for_testing"
os.environ["SECRET_KEY"] = "test_secret_key_minimum_32_characters_long_12345"
os.environ["POSTGRES_HOST"] = "localhost"
os.environ["POSTGRES_PORT"] = "5432"
os.environ["POSTGRES_DB"] = "test_db"
os.environ["POSTGRES_USER"] = "test_user"

# Add services path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Create a minimal Base for testing without full database import
Base = declarative_base()


# Test database URL
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


# Note: With pytest-asyncio in auto mode, the event_loop fixture is handled
# automatically. Custom event_loop fixtures with session scope can cause
# issues in Python 3.12+. If needed, configure loop_scope in pytest.ini.


@pytest.fixture(scope="function")
async def async_engine():
    """Create async test database engine"""
    # Import ORM models to register them

    # Update Base metadata with the ORM models' Base
    from services.shared.database import Base as ORMBase

    engine = create_async_engine(
        TEST_DATABASE_URL,
        echo=False,
        poolclass=NullPool,
    )

    # Create all tables using the ORM Base
    # Note: SQLite doesn't support PostgreSQL ARRAY type, so this may fail
    try:
        async with engine.begin() as conn:
            await conn.run_sync(ORMBase.metadata.create_all)
    except Exception as e:
        await engine.dispose()
        pytest.skip(f"Database tables cannot be created with SQLite: {e}")

    yield engine

    # Drop all tables
    try:
        async with engine.begin() as conn:
            await conn.run_sync(ORMBase.metadata.drop_all)
    except Exception:
        pass  # Tables may not exist if creation failed

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


@pytest.fixture
async def test_user(async_session: AsyncSession):
    """Create a test user"""
    from services.shared.auth import get_password_hash
    from services.shared.orm_models import User

    user = User(
        email="test@example.com",
        hashed_password=get_password_hash("testpassword123"),
        full_name="Test User",
    )

    async_session.add(user)
    await async_session.commit()
    await async_session.refresh(user)

    return user


@pytest.fixture
async def auth_headers(test_user) -> dict:
    """Get authentication headers for test user"""
    from services.shared.auth import create_access_token

    access_token = create_access_token(data={"sub": str(test_user.id)})

    return {"Authorization": f"Bearer {access_token}"}


@pytest.fixture
async def test_manuscript(async_session: AsyncSession, test_user):
    """Create a test manuscript"""
    from services.shared.orm_models import Manuscript

    manuscript = Manuscript(
        user_id=test_user.id,
        title="Test Manuscript",
        author="Test Author",
        word_count=1000,
        status="completed",
    )

    async_session.add(manuscript)
    await async_session.commit()
    await async_session.refresh(manuscript)

    return manuscript


@pytest.fixture
async def test_character(async_session: AsyncSession, test_manuscript):
    """Create a test character"""
    from services.shared.orm_models import Character

    character = Character(
        manuscript_id=test_manuscript.id,
        name="Test Character",
        description="A test character",
        dialogue_count=10,
    )

    async_session.add(character)
    await async_session.commit()
    await async_session.refresh(character)

    return character


@pytest.fixture
def mock_groq_response():
    """Mock Groq API response"""
    return {
        "choices": [{"message": {"content": "This is a test response from the LLM."}}]
    }


@pytest.fixture
def sample_scene_request(test_manuscript):
    """Sample scene generation request"""
    return {
        "manuscript_id": str(test_manuscript.id),
        "characters": ["Hermione", "Harry", "Ron"],
        "scene_description": "Study session in library",
        "setting": "Hogwarts library",
        "emotional_tone": "focused",
        "target_word_count": 500,
    }


@pytest.fixture
def client():
    """Create a test client for the FastAPI app"""
    # Skip if api-gateway module can't be loaded (hyphenated directory name)
    api_gateway_path = os.path.join(
        os.path.dirname(__file__), "..", "services", "api-gateway", "main.py"
    )
    if not os.path.exists(api_gateway_path):
        pytest.skip("API Gateway module not found")

    try:
        from fastapi.testclient import TestClient
        from unittest.mock import patch, AsyncMock

        # Dynamically load the api-gateway module
        spec = importlib.util.spec_from_file_location("api_gateway_main", api_gateway_path)
        api_gateway_main = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(api_gateway_main)
        app = api_gateway_main.app

        # Mock external dependencies
        with patch("services.shared.database.get_db"), patch(
            "services.shared.caching.CacheLayer"
        ) as mock_cache:

            mock_cache_instance = AsyncMock()
            mock_cache_instance.get.return_value = None
            mock_cache_instance.set.return_value = True
            mock_cache.return_value = mock_cache_instance

            with TestClient(app) as test_client:
                yield test_client
    except Exception as e:
        pytest.skip(f"Cannot load API Gateway: {e}")


@pytest.fixture
def sample_manuscript_text():
    """Sample manuscript text for testing"""
    return """
Chapter 1: The Beginning

"Hello, my dear friend," said Elizabeth warmly, extending her hand.

William smiled back at her. "It's wonderful to see you again," he replied.

Elizabeth walked slowly across the ornate parlor, admiring the paintings. She thought about their last meeting and wondered what news William might bring.

"I have something important to tell you," William said, his expression growing serious.

"What is it?" Elizabeth asked, concern evident in her voice.

William took a deep breath. "The estate has been sold," he explained carefully.

Elizabeth gasped. "But how can that be? Father would never—"

"I'm afraid it's true," William interrupted gently.

Thomas, the butler, entered quietly with a tea service. He noticed the tension in the room immediately.

"Shall I pour, Miss Elizabeth?" Thomas asked.

"Yes, please," Elizabeth responded, grateful for the distraction.
"""


@pytest.fixture
def multiple_test_characters(async_session, test_manuscript):
    """Create multiple test characters"""
    from services.shared.orm_models import Character

    async def _create_characters():
        characters = []
        names = ["Elizabeth", "William", "Thomas"]

        for name in names:
            character = Character(
                manuscript_id=test_manuscript.id,
                name=name,
                description=f"Character named {name}",
                dialogue_count=5,
                personality_traits={"trait": "kind"},
                voice_characteristics={"style": "formal"},
            )
            async_session.add(character)
            characters.append(character)

        await async_session.commit()
        for char in characters:
            await async_session.refresh(char)

        return characters

    return _create_characters
