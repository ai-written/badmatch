"""密码重置令牌。

安全要点：
- 只存 token 的 sha256，**不存明文**：数据库泄露时无法直接拿去重置别人的密码
- 一次性（used_at 置位即失效）+ 短时效（默认 20 分钟）
- 为用户重新签发时作废其全部未用令牌（避免旧邮件里的链接长期可用）

表由 create_all 创建（新表，不需要 startup_migration 补列）。
过期/已用行由 app.core.audit.cleanup_expired_password_reset_tokens 定期清理。
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # token 的 sha256（hex），唯一以便按值查找
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    # 申请来源（审计/排查用，不参与校验）
    request_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 刻意不声明 user relationship：调用方只用 user_id，而 User 的四个集合
    # 都是 lazy="selectin"，加一条 relationship 会让每次消费令牌多跑 5~6 条
    # 完全用不到的 SELECT（实测一次消费共 7 条语句）
