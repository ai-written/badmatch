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


def forwarded_client_ip(headers, peer_ip: str) -> str:
    """在「已确认前面是可信反向代理」的前提下，从转发头里取真实客户端 IP。

    取 X-Real-IP 优先（nginx 里用覆盖式写入，客户端伪造会被冲掉），
    其次取 X-Forwarded-For 的**最后一个**值——若代理是追加式写入，
    客户端伪造的值会排在前面，取第一个等于让攻击者自己指定 IP。

    值里若含逗号或空格，说明是被追加进去的伪造链而不是干净的单值，
    此时保守地回退到对端 IP。
    """
    def clean(v: str | None) -> str | None:
        if not v:
            return None
        v = v.strip()
        if not v or any(ch in v for ch in ", \t"):
            return None
        return v

    real = clean(headers.get("x-real-ip"))
    if real:
        return real
    xff = headers.get("x-forwarded-for")
    if xff:
        last = clean(xff.split(",")[-1])
        if last:
            return last
    return peer_ip


def resolve_client_ip(settings, headers: dict, peer_ip: str) -> str:
    """由「配置 + 请求头（小写键）+ 对端 IP」解出客户端 IP。

    抽出来是为了让访问日志（ASGI 中间件，拿不到 Request 对象）
    与各接口共用同一套判定，避免两处实现不一致而漏改一处。

    顺序很关键：**X-Real-IP 优先于 CF-Connecting-IP**。
    nginx 用 $remote_addr 覆盖式写入 X-Real-IP，是可信度最高的；
    若让 CF 头优先，那么在「开启了 CF 开关、但源站仍可被直连」的部署里，
    攻击者绕过 CF 直达源站、自带 CF-Connecting-IP 就能把 nginx 的覆盖覆盖掉，
    重新获得伪造来源 IP 的能力（按 IP 的限流与审计 IP 又会失效）。
    """
    # 先看代理覆盖式写入的 X-Real-IP
    if settings.TRUST_PROXY_HEADERS:
        real = (headers.get("x-real-ip") or "").strip()
        if real and not any(ch in real for ch in ", \t"):
            return real

    if settings.TRUST_CF_CONNECTING_IP:
        cf = (headers.get("cf-connecting-ip") or "").strip()
        if cf and not any(ch in cf for ch in ", \t"):
            return cf

    # 其次才轮到 X-Forwarded-For（追加式写入，取最后一跳）
    if settings.TRUST_PROXY_HEADERS:
        return forwarded_client_ip(headers, peer_ip)

    return peer_ip


def get_client_ip(request: Request) -> str:
    """获取客户端 IP，用于限流计数与审计记录。

    默认**不使用**转发头，因为它们是客户端可自由伪造的：一旦采信，
    攻击者每次换一个值就能让所有按 IP 的限流各自重新计数（等于无限流），
    同时审计里的来源 IP 也变成攻击者随手填的内容，事后无法追溯。

    只有确认服务只允许反向代理回源时，才把 TRUST_PROXY_HEADERS 设为 true；
    那样来源 IP 才由代理覆盖式写入。CF-Connecting-IP 需要单独开关，
    因为它只在 Cloudflare 回源时可信（详见 docker-compose.prod.yml 注释）。
    """
    peer = request.client.host if request.client else "unknown"
    return resolve_client_ip(get_settings(), request.headers, peer)


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


async def cleanup_expired_password_reset_tokens(conn: AsyncConnection) -> None:
    """删除已过期或已使用的密码重置令牌行。

    这类行不再有任何用途（明文令牌只存在于邮件里），但每次「申请找回」都会
    插入一行并作废旧行，不清理会无界增长、唯一索引持续膨胀。
    留 1 天缓冲是为了排查问题时还能看到最近的记录。
    """
    try:
        result = await conn.execute(
            text(
                "DELETE FROM password_reset_tokens "
                "WHERE expires_at < now() - interval '1 day'"
            )
        )
        if result.rowcount:
            logger.info("password_reset cleanup: removed %s expired tokens", result.rowcount)
    except Exception:
        logger.exception("password_reset cleanup failed")
