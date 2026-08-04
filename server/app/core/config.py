from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    APP_NAME: str = "BadMatch"
    DEBUG: bool = True
    SECRET_KEY: str = "change-me-in-production-use-randon-64-char-string"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 90  # 3 months

    DATABASE_URL: str = "postgresql+asyncpg://badminton:badminton@localhost:5432/badminton"

    # Scoring

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache
def get_settings() -> Settings:
    return Settings()
