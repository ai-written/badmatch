"""
业务操作审计工具。

- audit(): 向 audit_logs 表写入一条操作记录。**用独立会话并立即提交**（这样登录失败、
  权限拒绝这类异常路径也能留痕），因此调用点必须满足：成功路径先提交业务事务、
  有广播的再广播完，最后才写审计 —— 否则业务回滚会留下「操作成功」的假日志。
- cleanup_expired_audit_logs(): 清理超过保留期的过期记录（启动时调用）。
"""
import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.core.config import get_settings
from app.core.database import async_session_factory
from app.models.audit import AuditLog

logger = logging.getLogger(__name__)

# 必须与 models/audit.py 里的列宽一致：PostgreSQL 对超长字符串是**报错**而不是截断，
# 一旦超长，整条 INSERT 都会失败，而审计失败是被吞掉的 —— 表现为「某些用户/某些设备的
# 操作完全没有日志」，且业务一切正常。改成裁剪后，最多损失尾巴，不会再整条丢。
UA_LIMIT = 255
IP_LIMIT = 45
# 审计失败的落盘文件（与访问日志同目录）。生产 compose 把 ./logs 挂到宿主机，
# 因此它能跨容器重建保留；只打 stdout 的话，容器一重建现场就没了。
FAILURE_LOG_NAME = "audit-failures.log"
# 失败日志的单文件上限，超过就转成 .1（只留一份备份）。
# 访问日志有 50MB×5 的轮转，这里同样不能无界增长 —— 系统性故障时它会一直写。
MAX_FAILURE_LOG_BYTES = 5 * 1024 * 1024
# 重试策略：只重试「快速失败」，且最多等这么久
RETRY_DELAY_SECONDS = 0.1
RETRY_MAX_ELAPSED_SECONDS = 1.0


def _fit(value: str | None, limit: int) -> tuple[str | None, bool]:
    """把字符串裁剪到列宽以内，返回 (值, 是否被裁剪)。None 原样返回。"""
    if value is None:
        return None, False
    if len(value) <= limit:
        return value, False
    return value[:limit], True


def _append_failure_log(record: dict) -> None:
    """把一次审计失败追加到 audit-failures.log（JSONL）。

    刻意用同步写：调用点都在异常/取消路径上，此时再 await 线程池反而可能二次失败；
    单行追加很快，且失败本身已属罕见。

    另一个必须落盘的理由：只写 logger 的话，server 容器的 stdout 一重建就没了。
    真实事故里就是因为这个，09-29 那批审计写入失败的报错彻底不可考。
    """
    try:
        settings = get_settings()
        directory = os.path.dirname(settings.AUDIT_LOG_PATH) or "."
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, FAILURE_LOG_NAME)
        # 单文件超限就转成 .1：失败可能持续很久（系统性故障），不设上限会无界增长
        try:
            if os.path.getsize(path) > MAX_FAILURE_LOG_BYTES:
                os.replace(path, path + ".1")
        except OSError:
            pass  # 文件不存在/被占用：照常追加即可
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        # 连失败日志都写不了时，至少留在 stdout 里
        logger.exception("audit failure log write failed")


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

    失败处理（原先只有一句 logger.exception，实测代价很大）：
    1. 先裁剪 ip / user_agent 到列宽，避免「一个超长请求头就让这人的日志永久缺失」
    2. 失败重试一次：连接被回收、锁等待超时这类瞬时故障重试即可救回
    3. 仍失败（或被客户端中断）就写进 audit-failures.log 并打 ERROR —— 审计丢失
       不可恢复，必须留下可事后追溯的痕迹
    """
    settings = get_settings()
    if not settings.AUDIT_DB_ENABLED:
        return
    if high_freq and not settings.AUDIT_HIGH_FREQ_ENABLED:
        return

    ip_value, ip_cut = _fit(ip, IP_LIMIT)
    ua_value, ua_cut = _fit(user_agent, UA_LIMIT)
    if ip_cut or ua_cut:
        logger.warning(
            "audit field truncated: action=%s ip_cut=%s ua_cut=%s", action, ip_cut, ua_cut
        )

    # 先把用户字段取出来：它们在失败路径（写失败日志）里还会被读一次，而那时 ORM 对象
    # 可能已随请求会话失效（回滚/关闭）。取值抛错若从 audit() 逸出，就会把业务请求打成
    # 500 —— 违背「审计失败绝不影响业务」。这里一次取好，后面只用纯数据。
    try:
        user_id = user.id if user else None
        username = user.username if user else None
    except Exception:
        logger.warning("audit: 用户对象已失效，按匿名记录 action=%s", action, exc_info=True)
        user_id, username = None, None

    def failure(error: str) -> dict:
        return {
            "time": datetime.now(timezone.utc).astimezone().isoformat(),
            "action": action,
            "user_id": user_id,
            "username": username,
            "target_type": target_type,
            "target_id": target_id,
            "ip": ip_value,
            # 把 UA 也带上：失败现场要能直接看出「是不是超长/某类设备」，
            # 否则这条日志自己也回答不了它当初想回答的问题
            "user_agent": ua_value,
            "error": error,
        }

    started = time.monotonic()
    for attempt in (1, 2):
        committed = False
        try:
            async with async_session_factory() as session:
                session.add(AuditLog(
                    user_id=user_id,
                    username=username,
                    action=action,
                    target_type=target_type,
                    target_id=target_id,
                    detail=detail,
                    ip=ip_value,
                    user_agent=ua_value,
                ))
                await session.commit()
                committed = True
            return
        except asyncio.CancelledError:
            # 提交已经成功、取消正好落在关闭会话（__aexit__）期间：这条审计其实**已经落库**，
            # 再记一条「丢失」就是假告警，反过来会误导排查。
            if committed:
                raise
            # 客户端中断（手机端切页/断网很常见）会把请求任务取消。不能吞掉它，
            # 但必须留痕：业务可能已经提交，而这条审计不会落库。
            logger.warning("audit cancelled: action=%s", action)
            _append_failure_log(failure("CancelledError: request cancelled"))
            raise
        except Exception as e:
            elapsed = time.monotonic() - started
            # 只对「快速失败」重试：超长/约束这类数据错误重试没有意义，而慢失败
            # （连接池排队、锁等待）再等一遍只会把故障放大 —— 记分接口这时往往
            # 还持有比赛行锁，多等一秒就多连累同场其他人一秒。
            if attempt == 2 or elapsed > RETRY_MAX_ELAPSED_SECONDS:
                logger.exception("audit write failed: action=%s", action)
                _append_failure_log(failure(f"{type(e).__name__}: {e}"))
                return
            # 审计失败绝不影响业务主流程，这里是唯一的等待点
            try:
                await asyncio.sleep(RETRY_DELAY_SECONDS)
            except asyncio.CancelledError:
                # 取消正好落在重试等待里：同样要留痕，否则会出现「无任何痕迹」的丢失
                logger.warning("audit cancelled while waiting to retry: action=%s", action)
                _append_failure_log(failure("CancelledError: request cancelled during retry wait"))
                raise


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
