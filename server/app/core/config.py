from pydantic_settings import BaseSettings
from functools import lru_cache


# 已知的不安全/示例密钥，生产环境禁止使用
INSECURE_SECRET_KEYS = {
    "change-me-in-production-use-randon-64-char-string",
    "change-me-to-a-random-string",
    "change-me-to-a-random-64-char-string",
    "docker-dev-secret-key-not-for-production",
    "",
}


class Settings(BaseSettings):
    APP_NAME: str = "BadMatch"
    DEBUG: bool = True
    SECRET_KEY: str = "change-me-in-production-use-randon-64-char-string"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 90  # 3 months

    DATABASE_URL: str = "postgresql+asyncpg://badminton:badminton@localhost:5432/badminton"

    FRONTEND_URL: str = "http://localhost:5173"

    # CORS 允许来源；多个用逗号分隔。
    # 为空时默认使用 FRONTEND_URL（生产同源部署天然不需要 CORS）。
    CORS_ORIGINS: str = ""

    # 首个用户注册为超级管理员所需的一次性注册码。
    # 为空时每次启动随机生成并打印到日志（重启后失效，建议配置固定值）。
    SUPERADMIN_INIT_CODE: str = ""

    # ---- 审计/访问日志 ----
    # HTTP 访问日志文件路径（JSONL 格式，RotatingFileHandler 轮转）
    AUDIT_LOG_PATH: str = "logs/access.log"
    AUDIT_LOG_MAX_MB: int = 50
    AUDIT_LOG_BACKUPS: int = 5
    # 业务操作审计（audit_logs 表）总开关
    AUDIT_DB_ENABLED: bool = True
    # 是否记录高频操作（记分/投票等），默认关闭
    AUDIT_HIGH_FREQ_ENABLED: bool = False
    # 审计记录保留天数，过期记录启动时清理
    AUDIT_RETENTION_DAYS: int = 90

    # Email notifications
    SMTP_HOST: str = ""
    SMTP_PORT: int = 465
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = ""

    # Login / invite-code brute-force protection
    LOGIN_MAX_ATTEMPTS: int = 5
    LOGIN_WINDOW_SECONDS: int = 600
    INVITE_MAX_ATTEMPTS: int = 3
    INVITE_WINDOW_SECONDS: int = 300

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

    @property
    def cors_origins_list(self) -> list[str]:
        """CORS 允许来源列表；未配置时回退到 FRONTEND_URL。"""
        origins = [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]
        if not origins:
            origins = [self.FRONTEND_URL]
        return [o for o in origins if o]

    def secret_key_is_default(self) -> bool:
        """当前 SECRET_KEY 是否为已知默认值/示例值（不安全）。"""
        return self.SECRET_KEY in INSECURE_SECRET_KEYS


@lru_cache
def get_settings() -> Settings:
    return Settings()
