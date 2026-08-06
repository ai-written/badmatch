"""
业务操作审计工具。

- audit(): 向 audit_logs 表写入一条操作记录（与业务同事务，失败不影响业务）。
- cleanup_expired_audit_logs(): 清理超过保留期的过期记录（启动时调用）。
"""
import logging
from datetime import datetime, timedelta, timezone

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.core.config import get_settings
from app.core.database import async_session_factory
from app.models.audit import AuditLog

logger = logging.getLogger(__name__)


def get_client_ip(request: Request) -> str:
    """获取客户端 IP，优先取 X-Forwarded-For（nginx 反代场景）。"""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def audit(
    *,
    user,
    action: str,
    target_type: str | None = None,
    target_id: int | None = None,
    detail: dict | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    high_freq: bool = False,
) -> None:
    """写入一条审计记录。

    - 使用独立会话提交，不受业务事务回滚影响：
      登录失败、权限拒绝等异常路径的审计也能可靠落库。
    - 参数 user: 当前操作用户（User 对象或 None=匿名）
    - high_freq: 高频操作（记分/投票等），受 AUDIT_HIGH_FREQ_ENABLED 开关控制
    """
    settings = get_settings()
    if not settings.AUDIT_DB_ENABLED:
        return
    if high_freq and not settings.AUDIT_HIGH_FREQ_ENABLED:
        return
    try:
        async with async_session_factory() as session:
            session.add(AuditLog(
                user_id=user.id if user else None,
                username=user.username if user else None,
                action=action,
                target_type=target_type,
                target_id=target_id,
                detail=detail,
                ip=ip,
                user_agent=user_agent,
            ))
            await session.commit()
    except Exception:
        # 审计失败绝不影响业务主流程
        logger.exception("audit write failed: action=%s", action)


async def cleanup_expired_audit_logs(conn: AsyncConnection) -> None:
    """删除超过 AUDIT_RETENTION_DAYS 天的审计记录。

    使用纯 text SQL（避免 ORM 语句在 Core Connection 上的兼容性问题）。
    """
    settings = get_settings()
    if not settings.AUDIT_DB_ENABLED:
        return
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.AUDIT_RETENTION_DAYS)
    try:
        result = await conn.execute(
            text("DELETE FROM audit_logs WHERE created_at < :cutoff"),
            {"cutoff": cutoff},
        )
        if result.rowcount:
            logger.info("audit cleanup: removed %s expired records", result.rowcount)
    except Exception:
        logger.exception("audit cleanup failed")
