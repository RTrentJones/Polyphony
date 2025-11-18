"""Configuration management for Polyphony services"""

from pydantic import field_validator, ValidationError
from pydantic_settings import BaseSettings
from typing import Optional, List
import os
import sys


class Settings(BaseSettings):
    """Application settings loaded from environment variables"""

    # Application
    APP_NAME: str = "Polyphony"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    ENVIRONMENT: str = "development"  # development, staging, production

    # Database - PostgreSQL (REQUIRED in production)
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "polyphony"
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str  # No default - must be set via environment!

    # Vector Database - Qdrant
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_API_KEY: Optional[str] = None

    # Cache/Queue - Redis
    REDIS_URL: str = "redis://localhost:6379"
    REDIS_PASSWORD: Optional[str] = None

    # LLM APIs (REQUIRED)
    GROQ_API_KEY: str  # No default - must be set via environment!
    GROQ_MODEL: str = "llama-3.1-70b-versatile"
    GROQ_MODEL_FAST: str = "llama-3.1-8b-instant"

    # Embeddings
    EMBEDDING_MODEL: str = "all-MiniLM-L6-v2"
    EMBEDDING_DIMENSION: int = 384

    # Authentication/Security (REQUIRED in production)
    SECRET_KEY: str  # No default - must be set via environment!
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    @field_validator('SECRET_KEY')
    @classmethod
    def validate_secret_key(cls, v: str) -> str:
        """Ensure SECRET_KEY is strong enough"""
        if len(v) < 32:
            raise ValueError('SECRET_KEY must be at least 32 characters long')
        if v == "your-secret-key-change-this-in-production-min-32-chars":
            raise ValueError('SECRET_KEY must be changed from default value')
        return v

    @field_validator('GROQ_API_KEY')
    @classmethod
    def validate_groq_key(cls, v: str) -> str:
        """Ensure GROQ_API_KEY is set"""
        if not v or v == "":
            raise ValueError('GROQ_API_KEY is required')
        return v

    @field_validator('POSTGRES_PASSWORD')
    @classmethod
    def validate_postgres_password(cls, v: str) -> str:
        """Ensure database password is secure"""
        if v == "password" or v == "postgres":
            raise ValueError('POSTGRES_PASSWORD must not be a default/weak password')
        if len(v) < 8:
            raise ValueError('POSTGRES_PASSWORD must be at least 8 characters long')
        return v

    # Service URLs
    API_GATEWAY_URL: str = "http://localhost:8000"
    ORCHESTRATOR_URL: str = "http://localhost:8001"
    CHARACTER_AGENT_URL: str = "http://localhost:8002"  # Single character agent service
    CHARACTER_AGENT_URLS: str = ""  # Comma-separated URLs for multi-agent deployment

    # File Storage
    UPLOAD_DIR: str = "/tmp/polyphony/uploads"
    MAX_UPLOAD_SIZE: int = 50 * 1024 * 1024  # 50MB
    ALLOWED_EXTENSIONS: List[str] = [".txt", ".docx", ".pdf", ".html", ".htm"]

    # Monitoring
    PROMETHEUS_URL: str = "http://localhost:9090"
    GRAFANA_URL: str = "http://localhost:3001"

    # Service-specific settings
    SERVICE_NAME: Optional[str] = None
    SERVICE_PORT: int = 8000
    CHARACTER_NAME: Optional[str] = None
    CHARACTER_ID: Optional[str] = None

    # Rate limiting
    RATE_LIMIT_PER_MINUTE: int = 60
    RATE_LIMIT_PER_HOUR: int = 1000

    # RAG settings
    RAG_TOP_K: int = 5
    RAG_SCORE_THRESHOLD: float = 0.5

    # Scene generation settings
    DEFAULT_TARGET_WORD_COUNT: int = 500
    MAX_SCENE_WORD_COUNT: int = 3000
    MIN_SCENE_WORD_COUNT: int = 100
    MAX_SCENE_BEATS: int = 10

    # Cache settings
    CACHE_TTL_SECONDS: int = 3600
    CACHE_DIALOGUE: bool = True

    class Config:
        env_file = ".env"
        case_sensitive = True


# Create global settings instance
settings = Settings()


def get_character_agent_urls() -> dict[str, str]:
    """Parse character agent URLs from environment variable

    Returns:
        Dictionary mapping character name to URL
        Example: {"Hermione": "http://hermione:8002"}
    """
    if not settings.CHARACTER_AGENT_URLS:
        return {}

    agent_urls = {}
    for url in settings.CHARACTER_AGENT_URLS.split(","):
        url = url.strip()
        if not url:
            continue
        try:
            # Extract character name from URL
            # Format: http://hermione:8002
            char_name = url.split("//")[1].split(":")[0].capitalize()
            agent_urls[char_name] = url
        except (IndexError, AttributeError):
            print(f"Warning: Invalid character agent URL: {url}")

    return agent_urls
