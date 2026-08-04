from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    APP_NAME: str = "BadMatch"
    DEBUG: bool = True
    SECRET_KEY: str = "change-me-in-production-use-randon-64-char-string"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 90  # 3 months

    DATABASE_URL: str = "postgresql+asyncpg://badminton:badminton@localhost:5432/badminton"

    FRONTEND_URL: str = "http://localhost:5173"

    # Email notifications
    SMTP_HOST: str = ""
    SMTP_PORT: int = 465
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = ""

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache
def get_settings() -> Settings:
    return Settings()
