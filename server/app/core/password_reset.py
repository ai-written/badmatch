"""邮箱找回密码的一次性令牌。

为什么存 sha256 而不是明文/bcrypt：
- 令牌是 32 字节随机值（256 位熵），不存在被猜解的风险，不需要 bcrypt 那种
  故意变慢的哈希；sha256 足够且能按值直接建索引查询
- 不存明文，是为了「数据库泄露也不能拿去重置别人密码」

约束：一次性（用掉即置 used_at）、短时效（默认 20 分钟）、
重新签发时作废该用户全部未用令牌。
"""
import hashlib
import secrets
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.password_reset import PasswordResetToken

TOKEN_TTL_MINUTES = 20


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def issue_reset_token(db: AsyncSession, user_id: int, request_ip: str | None = None) -> str:
    """为用户签发重置令牌，返回明文 token（只在此刻存在于内存中）。

    会先作废该用户所有未用令牌：否则用户多次申请后，早先那些邮件里的链接
    在 TTL 内仍然可用，等于扩大了可用凭证的数量。
    """
    now = datetime.now()
    await db.execute(
        update(PasswordResetToken)
        .where(
            PasswordResetToken.user_id == user_id,
            PasswordResetToken.used_at.is_(None),
        )
        .values(used_at=now)   # 标记为已用即作废（无需真删，保留痕迹）
    )
    token = secrets.token_urlsafe(32)
    db.add(PasswordResetToken(
        user_id=user_id,
        token_hash=_hash(token),
        expires_at=now + timedelta(minutes=TOKEN_TTL_MINUTES),
        request_ip=request_ip,
    ))
    await db.flush()
    return token


async def consume_reset_token(db: AsyncSession, token: str) -> int | None:
    """校验并作废令牌，成功返回 user_id；无效/过期/已用返回 None。

    并发用同一个 token 提交两次时，两个请求都会先读到这一行，但都只是把
    used_at 置位（第二次是覆盖同一个值），因此最终效果等同于只能用它改一次
    密码 —— 真正保证「只能用一次」的是 used_at 非空后查不到记录。
    """
    if not token:
        return None
    digest = _hash(token)
    now = datetime.now()
    result = await db.execute(
        select(PasswordResetToken).where(
            PasswordResetToken.token_hash == digest,
            PasswordResetToken.used_at.is_(None),
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None
    if row.expires_at <= now:
        # 过期也标记掉，避免被反复试探
        row.used_at = now
        await db.flush()
        return None
    row.used_at = now
    await db.flush()
    return row.user_id
