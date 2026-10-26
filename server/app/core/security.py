from datetime import datetime, timedelta, timezone
import bcrypt
from jose import jwt, JWTError
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db

settings = get_settings()
bearer_scheme = HTTPBearer(auto_error=False)

_auth_user_loads_cache: tuple | None = None


def _auth_user_loads() -> tuple:
    """鉴权只需要 User 自己的列（id / role / token_version / is_active …）。

    `User` 的 4 个集合关系都是 `lazy="selectin"`：不显式关掉的话，**每一个带登录态的请求**
    都会顺带把「他报名过的赛事 / 战绩 / 创建的赛事 / 执裁过的比赛」整批拉回来；
    而 `created_tournaments` 里的 Tournament 又自带 5 个 selectin 集合
    （registrations/courts/rounds/matches/player_stats），再往下是 matches→pairings→users
    —— 一层层 selectin 级联。实测一次 `/tournaments/{id}/rounds` 因此从 ~12 条 SQL
    涨到 **136 条**、本地 95ms（线上经 frp 转发被放大到几百 ms），且**随该用户的历史数据增长**。

    目前没有任何接口依赖这些集合（用到时都是自己 `id.in_(...)` 批量查）。
    必须在调用时构建：noload() 会立刻触发 mapper 配置。
    """
    global _auth_user_loads_cache
    if _auth_user_loads_cache is None:
        from sqlalchemy.orm import noload
        from app.models.user import User
        _auth_user_loads_cache = (
            noload(User.registrations), noload(User.player_stats),
            noload(User.created_tournaments), noload(User.refereed_matches),
        )
    return _auth_user_loads_cache


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def create_access_token(data: dict, token_version: int = 0) -> str:
    to_encode = data.copy()
    to_encode.update({"tv": token_version})
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_access_token(token: str) -> dict | None:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        return payload
    except JWTError:
        return None


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
):
    """Return current user or None; does NOT enforce auth."""
    if credentials is None:
        return None
    payload = decode_access_token(credentials.credentials)
    if payload is None:
        return None
    user_id = payload.get("sub")
    if user_id is None:
        return None
    try:
        user_id = int(user_id)
    except (TypeError, ValueError):
        return None
    from app.models.user import User
    from sqlalchemy import select
    result = await db.execute(
        select(User).where(User.id == user_id).options(*_auth_user_loads())
    )
    user = result.scalar_one_or_none()
    if user is None:
        return None
    # token 版本号校验：登出/改密码后旧 token 失效；
    # 旧格式 token（无 tv 字段）按版本 0 处理，保证升级/重启后登录状态平滑保留。
    if payload.get("tv", 0) != user.token_version:
        return None
    # 被禁用的账号立即失效：即使版本号还对（禁用时漏了自增、或库里被手工改过），
    # 也一律按未登录处理 —— 所有接口退化成 401，前端拦截器会清 token 回登录页。
    if not user.is_active:
        return None
    return user


async def require_user(
    user=Depends(get_current_user),
):
    """Enforce auth; raise 401 if not logged in."""
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")
    return user
