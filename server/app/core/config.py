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
    # 登录失败其实有两把锁：
    #   LOGIN_*    按「用户名」——保护单个账号不被逐个试
    #   LOGIN_IP_* 按「来源 IP」——兜底，防同一来源喷洒很多账号
    # 兜底那把必须宽松得多：一个出口 IP 后面常常是一整片人（公司/学校网络、运营商 NAT），
    # 用和「单个账号」相同的 5 次会让无关的人一起被拦 10 分钟。实测复现过：同一 IP 先失败
    # 5 次（另一个用户名），随后用**正确密码**登录另一个账号也被 429 —— 用户看到的现象
    # 就是「改完密码 / 重置完密码还是登不上」。
    LOGIN_MAX_ATTEMPTS: int = 5
    LOGIN_WINDOW_SECONDS: int = 600
    LOGIN_IP_MAX_ATTEMPTS: int = 30
    LOGIN_IP_WINDOW_SECONDS: int = 600
    INVITE_MAX_ATTEMPTS: int = 3
    INVITE_WINDOW_SECONDS: int = 300
    # 找回密码：按来源 IP 限制申请次数（避免被用来给他人邮箱灌邮件）。
    # 15 分钟 10 次：给得比登录宽松，因为「同一出口 IP」后面常常是一整片人
    # （公司/学校网络、运营商 NAT；走 Cloudflare 时多个访客还可能共用同一个边缘 IP），
    # 太紧会出现「别人申请过几次，我这边就被拦」。
    FORGOT_MAX_ATTEMPTS: int = 10
    FORGOT_WINDOW_SECONDS: int = 900

    # ---- 反向代理 ----
    # 是否信任 X-Real-IP / X-Forwarded-For 等转发头。
    # 默认 False（安全）：这些头任何客户端都能自己带上，直接暴露服务时信任它们
    # 等于所有按 IP 的限流和审计 IP 都可被伪造绕过。
    # 仅当服务只允许反向代理回源（如 nginx 覆盖式写入 X-Real-IP）时才设为 true。
    TRUST_PROXY_HEADERS: bool = False
    # 该头只在 Cloudflare 回源时才是可信的，因此单独开关，不随 TRUST_PROXY_HEADERS 放开
    TRUST_CF_CONNECTING_IP: bool = False
    # 明确允许在 DEBUG 下用不安全的 SECRET_KEY（仅供本地开发）
    ALLOW_INSECURE_SECRET_KEY: bool = False

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
