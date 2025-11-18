"""Configuration management for Polyphony services"""

from pydantic_settings import BaseSettings
from typing import Optional, List
import os


class Settings(BaseSettings):
    """Application settings loaded from environment variables"""

    # Application
    APP_NAME: str = "Polyphony"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False

    # Database - PostgreSQL
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "polyphony"
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "password"

    # Vector Database - Qdrant
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_API_KEY: Optional[str] = None

    # Cache/Queue - Redis
    REDIS_URL: str = "redis://localhost:6379"
    REDIS_PASSWORD: Optional[str] = None

    # LLM APIs
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "llama-3.1-70b-versatile"
    GROQ_MODEL_FAST: str = "llama-3.1-8b-instant"

    # Embeddings
    EMBEDDING_MODEL: str = "all-MiniLM-L6-v2"
    EMBEDDING_DIMENSION: int = 384

    # Authentication/Security
    SECRET_KEY: str = "your-secret-key-change-this-in-production-min-32-chars"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    # Service URLs
    API_GATEWAY_URL: str = "http://localhost:8000"
    ORCHESTRATOR_URL: str = "http://localhost:8001"
    CHARACTER_AGENT_URLS: str = ""  # Comma-separated URLs

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
