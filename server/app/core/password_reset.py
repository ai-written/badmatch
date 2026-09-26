"""邮箱找回密码的一次性令牌。

为什么存 sha256 而不是明文/bcrypt：
- 令牌是 32 字节随机值（256 位熵），不存在被猜解的风险，不需要 bcrypt 那种
  故意变慢的哈希；sha256 足够且能按值直接建索引查询
- 不存明文，是为了「数据库泄露也不能拿去重置别人密码」

约束：一次性（用掉即置 used_at）、短时效（默认 20 分钟）、
同一用户同时只有一条可用令牌。
"""
import hashlib
import secrets
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.password_reset import PasswordResetToken
from app.models.user import User

TOKEN_TTL_MINUTES = 20


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def issue_reset_token(db: AsyncSession, user_id: int, request_ip: str | None = None) -> str:
    """为用户签发重置令牌，返回明文 token（只在此刻存在于内存中）。

    会先作废该用户所有未用令牌：否则用户多次申请后，早先那些邮件里的链接
    在 TTL 内仍然可用，等于扩大了可用凭证的数量。

    先锁住 users 行再作废+插入：并发两次申请时，两个事务各自的快照都看不到
    对方刚插入的行，于是只作废「自己看得见的」旧令牌，结果是两封邮件里的
    链接同时有效。对用户行加锁可以把两次申请串行化。
    """
    now = datetime.now()
    # 锁用户行（令牌行自己防不住这件事：新插入的行对其他事务不可见）
    await db.execute(select(User.id).where(User.id == user_id).with_for_update())

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

    用「条件 UPDATE + RETURNING」一步完成占用，而不是「先 SELECT 再改对象」：
    后者两个并发请求会各自读到 used_at IS NULL 然后都成功返回 user_id，
    于是同一个令牌能改两次密码（后提交者覆盖前者设置的密码）。
    条件写进 UPDATE 的 WHERE 之后，第二个请求拿到行锁时会**重新判定条件**，
    此时 used_at 已非空 → 更新 0 行 → 返回 None → 前端拿到 400。

    过期令牌直接在 WHERE 里排除，因此不再需要额外写 used_at
    （那种写入会随 400 异常被 get_db 回滚，等于没写）。
    """
    if not token:
        return None
    result = await db.execute(
        update(PasswordResetToken)
        .where(
            PasswordResetToken.token_hash == _hash(token),
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.expires_at > datetime.now(),
        )
        .values(used_at=datetime.now())
        .returning(PasswordResetToken.user_id)
    )
    return result.scalar_one_or_none()
