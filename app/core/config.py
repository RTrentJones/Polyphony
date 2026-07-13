"""Configuration management for Polyphony"""

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional, List


class Settings(BaseSettings):
    """Application settings loaded from environment variables"""

    # Application
    APP_NAME: str = "Polyphony"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    ENVIRONMENT: str = "development"  # development, staging, production

    # Database. DATABASE_URL (full DSN, e.g. a Neon postgresql:// URL) wins;
    # the POSTGRES_* components are the docker-compose fallback.
    DATABASE_URL: Optional[str] = None
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "polyphony"
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: Optional[str] = None

    # LLM backend (see app/llm/providers.py for the registry).
    # None models fall back to the selected provider's registry defaults.
    LLM_PROVIDER: str = "gemini"
    LLM_MODEL: Optional[str] = None
    LLM_MODEL_FAST: Optional[str] = None
    LLM_MAX_RPM: Optional[int] = None  # override the provider's pacing default
    LLM_MAX_CONCURRENCY: int = 2
    LLM_TIMEOUT_SECONDS: float = 60.0
    # Reproducibility knob: when set (e.g. 0.0 during evals), overrides every
    # call's temperature so generations are deterministic and score deltas
    # reflect prompt/architecture changes, not sampling jitter. None = off
    # (production sampling unchanged).
    LLM_TEMPERATURE_OVERRIDE: Optional[float] = None

    # Embeddings (fastembed / ONNX; 384 dims matches all-MiniLM-L6-v2)
    EMBEDDING_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"
    EMBEDDING_DIMENSION: int = 384

    # Authentication/Security (REQUIRED in production)
    SECRET_KEY: str  # No default - must be set via environment!
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    # First-boot admin bootstrap (created only when the users table is empty)
    ADMIN_EMAIL: Optional[str] = None
    ADMIN_PASSWORD: Optional[str] = None

    # Per-user LLM budget (tokens per rolling 24h; 0 disables the check)
    USER_DAILY_TOKEN_LIMIT: int = 200_000

    @field_validator("SECRET_KEY")
    @classmethod
    def validate_secret_key(cls, v: str) -> str:
        """Ensure SECRET_KEY is strong enough"""
        if len(v) < 32:
            raise ValueError("SECRET_KEY must be at least 32 characters long")
        if v == "your-secret-key-change-this-in-production-min-32-chars":
            raise ValueError("SECRET_KEY must be changed from default value")
        return v

    @field_validator("POSTGRES_PASSWORD")
    @classmethod
    def validate_postgres_password(cls, v: Optional[str]) -> Optional[str]:
        """Ensure database password is secure when component config is used"""
        if v is None:
            return v
        if v == "password" or v == "postgres":
            raise ValueError("POSTGRES_PASSWORD must not be a default/weak password")
        if len(v) < 8:
            raise ValueError("POSTGRES_PASSWORD must be at least 8 characters long")
        return v

    @model_validator(mode="after")
    def validate_database_config(self) -> "Settings":
        if not self.DATABASE_URL and not self.POSTGRES_PASSWORD:
            raise ValueError(
                "Database config required: set DATABASE_URL or POSTGRES_PASSWORD"
            )
        return self

    # File Storage
    UPLOAD_DIR: str = "/tmp/polyphony/uploads"  # nosec B108 - Configurable via env var
    MAX_UPLOAD_SIZE: int = 50 * 1024 * 1024  # 50MB
    ALLOWED_EXTENSIONS: List[str] = [".txt", ".docx", ".pdf", ".html", ".htm"]

    # Frontend static export (served by FastAPI when present)
    STATIC_DIR: str = "frontend/out"
    ALLOWED_ORIGINS: str = "http://localhost:3000"

    # Rate limiting
    RATE_LIMIT_PER_MINUTE: int = 60
    RATE_LIMIT_PER_HOUR: int = 1000

    # RAG settings
    RAG_TOP_K: int = 5
    # Voice retrieval embeds a beat/scene DESCRIPTION against stored voice LINES;
    # on symmetric all-MiniLM those land at cosine ~0.2-0.45 even for the best,
    # on-voice samples, so the old 0.5 floor silently dropped ALL grounding.
    # Lower floor + a never-empty fallback in retrieve_similar (the query is
    # already character-scoped, so its closest samples are always wanted).
    RAG_SCORE_THRESHOLD: float = 0.2

    # Scene generation settings
    DEFAULT_TARGET_WORD_COUNT: int = 500
    MAX_SCENE_WORD_COUNT: int = 3000
    MIN_SCENE_WORD_COUNT: int = 100
    MAX_SCENE_BEATS: int = 10

    # Cache settings (in-process TTL cache)
    CACHE_TTL_SECONDS: int = 3600
    CACHE_DIALOGUE: bool = True
    CACHE_MAX_ENTRIES: int = 2048

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True)


# Create global settings instance
settings = Settings()
