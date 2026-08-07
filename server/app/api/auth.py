import os, re, secrets, uuid, logging, json, asyncio
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete, update, text
from sqlalchemy.exc import IntegrityError
from app.core.database import get_db
from app.core.security import create_access_token, get_current_user, hash_password, verify_password, require_user
from app.core.config import get_settings
from app.core.ratelimit import RateLimiter
from app.core.audit import audit, get_client_ip
from app.schemas.auth import (
    RegisterRequest, LoginRequest, TokenResponse, UserProfile, UserStats,
    AdminResetPassword, AdminSetRole, SelectableUser, UpdateProfile,
    ChangePasswordRequest,
)
from app.models.user import User
from app.models.tournament import PlayerStats
from app.models.audit import AuditLog

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "static", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

_settings = get_settings()
login_limiter = RateLimiter(_settings.LOGIN_MAX_ATTEMPTS, _settings.LOGIN_WINDOW_SECONDS)
login_ip_limiter = RateLimiter(_settings.LOGIN_MAX_ATTEMPTS, _settings.LOGIN_WINDOW_SECONDS)
invite_limiter = RateLimiter(_settings.INVITE_MAX_ATTEMPTS, _settings.INVITE_WINDOW_SECONDS)
# 初始管理员注册码防爆破（按 IP 限流）
init_limiter = RateLimiter(_settings.INVITE_MAX_ATTEMPTS, _settings.INVITE_WINDOW_SECONDS)


@router.post("/register", response_model=TokenResponse)
async def register(
    req: RegisterRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    exist = await db.execute(select(User).where(User.username == req.username))
    if exist.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="用户名已存在")

    email = req.email.strip()
    if not email:
        raise HTTPException(status_code=400, detail="邮箱不能为空")
    if not _is_valid_email(email):
        raise HTTPException(status_code=400, detail="邮箱格式不正确")
    email_exist = await db.execute(select(User).where(User.email == email))
    if email_exist.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="邮箱已被使用")

    first_check = await db.execute(select(func.count(User.id)))
    is_first = first_check.scalar() == 0
    if is_first:
        # 并发保护：首个用户创建串行化，避免两个并发请求同时检测到
        # “无用户”而创建出两个 superadmin（事务级锁，随事务提交/回滚释放）
        await db.execute(text("SELECT pg_advisory_xact_lock(1)"))
        first_check = await db.execute(select(func.count(User.id)))
        is_first = first_check.scalar() == 0

    invited_by_id = None
    if not is_first:
        if not req.invite_code:
            raise HTTPException(status_code=400, detail="需要邀请码")
        if not invite_limiter.check(req.invite_code):
            raise HTTPException(status_code=429, detail="邀请码尝试次数过多，请稍后再试")
        inviter = await db.execute(select(User).where(User.invite_code == req.invite_code))
        inviter_user = inviter.scalar_one_or_none()
        if not inviter_user:
            invite_limiter.record_failure(req.invite_code)
            raise HTTPException(status_code=400, detail="邀请码无效")
        invited_by_id = inviter_user.id
    else:
        # 首个用户将成为超级管理员，必须提供初始化注册码，防止被抢先注册
        ip = get_client_ip(request)
        if not init_limiter.check(ip):
            raise HTTPException(status_code=429, detail="初始注册码尝试次数过多，请稍后再试")
        code = req.init_code.strip() if req.init_code else ""
        if not code or not secrets.compare_digest(code, _get_init_code()):
            init_limiter.record_failure(ip)
            raise HTTPException(status_code=400, detail="初始管理员注册码不正确")
        init_limiter.reset(ip)

    user = User(
        username=req.username,
        password_hash=hash_password(req.password),
        gender=req.gender,
        invited_by=invited_by_id,
        email=email,
        role="superadmin" if is_first else "user",
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError:
        raise HTTPException(status_code=400, detail="用户名或邮箱已被使用")

    token = create_access_token(
        {"sub": str(user.id), "username": user.username},
        token_version=user.token_version,
    )
    await audit(
        user=user, action="register",
        detail={"is_first": is_first, "role": user.role, "username": user.username},
        ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
    )
    return TokenResponse(access_token=token, user=_profile(user))


@router.post("/login", response_model=TokenResponse)
async def login(
    req: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    if not login_limiter.check(req.username):
        raise HTTPException(status_code=429, detail="登录尝试次数过多，请稍后再试")
    ip = get_client_ip(request)
    if not login_ip_limiter.check(ip):
        raise HTTPException(status_code=429, detail="登录尝试次数过多，请稍后再试")
    result = await db.execute(select(User).where(User.username == req.username))
    user = result.scalar_one_or_none()
    if not user or not verify_password(req.password, user.password_hash):
        login_limiter.record_failure(req.username)
        login_ip_limiter.record_failure(ip)
        await audit(
            user=None, action="login_failed",
            detail={"username": req.username, "reason": "bad_credentials"},
            ip=ip, user_agent=request.headers.get("user-agent"),
        )
        raise HTTPException(status_code=400, detail="用户名或密码错误")
    login_limiter.reset(req.username)
    await audit(
        user=user, action="login_success",
        detail={"username": user.username},
        ip=ip, user_agent=request.headers.get("user-agent"),
    )
    token = create_access_token(
        {"sub": str(user.id), "username": user.username},
        token_version=user.token_version,
    )
    return TokenResponse(access_token=token, user=_profile(user))


@router.post("/logout")
async def logout(
    request: Request,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """登出：递增 token 版本号使所有 token 失效，并断开该用户全部 WebSocket。"""
    user.token_version += 1
    await db.flush()
    from app.core.websocket import manager
    await manager.kick_user(user.id)
    await audit(
        user=user, action="logout",
        detail={"username": user.username},
        ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
    )
    return {"ok": True}


# ---- 初始管理员注册码 ----

_init_code_cache: str | None = None


def _get_init_code() -> str:
    """返回首个用户注册所需的初始化注册码。

    优先使用环境变量 SUPERADMIN_INIT_CODE；未配置时启动后首次调用生成
    随机 8 位码并打印到日志（重启后失效）。
    """
    global _init_code_cache
    if _init_code_cache is None:
        s = get_settings()
        if s.SUPERADMIN_INIT_CODE:
            _init_code_cache = s.SUPERADMIN_INIT_CODE
        else:
            _init_code_cache = secrets.token_hex(4)
            logger.warning(
                "未配置 SUPERADMIN_INIT_CODE，本次启动的初始管理员注册码为：%s "
                "（首个用户注册时使用；重启后失效，建议通过环境变量配置固定值）",
                _init_code_cache,
            )
    return _init_code_cache


@router.get("/me", response_model=UserProfile)
async def me(user: User = Depends(get_current_user)):
    if user is None:
        raise HTTPException(status_code=401)
    return _profile(user)


@router.put("/me/profile")
async def update_profile(
    body: UpdateProfile,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if user is None:
        raise HTTPException(status_code=401)
    changes: dict = {}
    if body.username is not None:
        username = body.username.strip()
        if not username:
            raise HTTPException(status_code=400, detail="用户名不能为空")
        exist = await db.execute(select(User).where(User.username == username, User.id != user.id))
        if exist.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="用户名已存在")
        changes["username"] = {"old": user.username, "new": username}
        user.username = username
    if body.avatar:
        changes["avatar"] = {"old": user.avatar, "new": body.avatar}
        user.avatar = body.avatar
    if body.gender is not None:
        changes["gender"] = {"old": user.gender, "new": body.gender or None}
        user.gender = body.gender or None
    if body.email is not None:
        email = body.email.strip() or None
        if email:
            if not _is_valid_email(email):
                raise HTTPException(status_code=400, detail="邮箱格式不正确")
            email_exist = await db.execute(
                select(User).where(User.email == email, User.id != user.id)
            )
            if email_exist.scalar_one_or_none():
                raise HTTPException(status_code=400, detail="邮箱已被使用")
        changes["email"] = {"old": user.email, "new": email}
        user.email = email
    try:
        await db.flush()
    except IntegrityError:
        raise HTTPException(status_code=400, detail="用户名或邮箱已被使用")
    await audit(
        user=user, action="update_profile",
        target_type="user", target_id=user.id,
        detail={"changes": changes},
        ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
    )
    return {"ok": True}


@router.put("/me/password")
async def change_password(
    body: ChangePasswordRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if user is None:
        raise HTTPException(status_code=401)
    if not verify_password(body.old_password, user.password_hash):
        raise HTTPException(status_code=400, detail="原密码错误")
    if len(body.new_password) < 6:
        raise HTTPException(status_code=400, detail="新密码至少6位")
    user.password_hash = hash_password(body.new_password)
    # 改密码后旧 token 全部失效，需重新登录
    user.token_version += 1
    login_limiter.reset(user.username)
    await db.flush()
    from app.core.websocket import manager
    await manager.kick_user(user.id)
    await audit(user=user, action="change_password", detail={"username": user.username})
    return {"ok": True}


@router.post("/upload-avatar")
async def upload_avatar(
    request: Request,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if user is None:
        raise HTTPException(status_code=401)
    ext = file.filename.split(".")[-1] if file.filename and "." in file.filename else "jpg"
    if ext.lower() not in ("jpg", "jpeg", "png", "gif", "webp"):
        raise HTTPException(status_code=400, detail="不支持的文件格式")
    content = await file.read()
    if not content or len(content) > 2 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="文件大小不能超过 2MB")
    if not _looks_like_image(content, ext.lower()):
        raise HTTPException(status_code=400, detail="文件内容与图片格式不匹配")
    # 压缩处理：缩放至 100px、白底合成、统一 JPEG 输出（低带宽友好）
    try:
        content = _process_avatar(content)
    except Exception:
        raise HTTPException(status_code=400, detail="图片无法处理，请更换图片")
    filename = f"{uuid.uuid4().hex}.jpg"
    filepath = os.path.join(UPLOAD_DIR, filename)
    with open(filepath, "wb") as f:
        f.write(content)
    old_avatar = user.avatar
    user.avatar = f"/static/uploads/{filename}"
    try:
        await db.flush()
        await db.commit()
    except Exception:
        await db.rollback()
        _remove_avatar_file(f"/static/uploads/{filename}")
        raise
    _remove_avatar_file(old_avatar)
    await audit(
        user=user, action="upload_avatar",
        target_type="user", target_id=user.id,
        detail={"avatar": user.avatar},
        ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
    )
    return {"avatar": user.avatar}


def _process_avatar(content: bytes, max_size: int = 100, quality: int = 75) -> bytes:
    """压缩头像：修正 EXIF 方向、等比缩放至 max_size、透明背景白底合成、JPEG 输出。"""
    from io import BytesIO
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        img = Image.open(BytesIO(content))
        img = ImageOps.exif_transpose(img)  # 修正手机拍照方向
        img.thumbnail((max_size, max_size))  # 保持宽高比缩放
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[-1])
            img = bg
        else:
            img = img.convert("RGB")
        buf = BytesIO()
        img.save(buf, "JPEG", quality=quality, optimize=True)
        return buf.getvalue()
    except (UnidentifiedImageError, OSError, ValueError):
        raise


@router.post("/generate-invite")
async def generate_invite(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if user is None:
        raise HTTPException(status_code=401)
    if user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="只有管理员可以生成邀请码")
    code = uuid.uuid4().hex[:8]
    user.invite_code = code
    await db.flush()
    await audit(
        user=user, action="generate_invite",
        target_type="user", target_id=user.id,
        detail={"invite_code": code},
        ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
    )
    return {"invite_code": code}



@router.get("/has-users")
async def has_users(db: AsyncSession = Depends(get_db)):
    cnt = await db.execute(select(func.count(User.id)))
    return {"exists": cnt.scalar() > 0}


@router.get("/stats", response_model=UserStats)
async def user_stats(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if user is None:
        return UserStats()
    result = await db.execute(select(PlayerStats).where(PlayerStats.user_id == user.id))
    all_stats = result.scalars().all()
    total_m = sum(s.matches_played for s in all_stats)
    total_w = sum(s.matches_won for s in all_stats)
    return UserStats(total_matches=total_m, total_wins=total_w,
        win_rate=round(total_w / total_m * 100, 1) if total_m > 0 else 0,
        tournaments_played=len(all_stats))


@router.get("/stats/{user_id}", response_model=UserStats)
async def user_stats_by_id(user_id: int, db: AsyncSession = Depends(get_db)):
    u = await db.execute(select(User).where(User.id == user_id))
    if not u.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="用户不存在")
    result = await db.execute(select(PlayerStats).where(PlayerStats.user_id == user_id))
    all_stats = result.scalars().all()
    total_m = sum(s.matches_played for s in all_stats)
    total_w = sum(s.matches_won for s in all_stats)
    return UserStats(total_matches=total_m, total_wins=total_w,
        win_rate=round(total_w / total_m * 100, 1) if total_m > 0 else 0,
        tournaments_played=len(all_stats))


# ---- Admin endpoints ----

@router.get("/admin/audit-logs")
async def list_audit_logs(
    action: str | None = None,
    username: str | None = None,
    created_from: str | None = None,
    created_to: str | None = None,
    page: int = 1,
    page_size: int = 20,
    admin: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """操作日志查询（仅超级管理员），按时间倒序分页。"""
    if admin.role != "superadmin":
        raise HTTPException(status_code=403)
    if page < 1 or page_size < 1 or page_size > 100:
        raise HTTPException(status_code=400, detail="分页参数不合法")

    query = select(AuditLog).order_by(AuditLog.created_at.desc())
    if action:
        query = query.where(AuditLog.action == action)
    if username:
        query = query.where(AuditLog.username.ilike(f"%{username}%"))
    if created_from:
        query = query.where(AuditLog.created_at >= _parse_audit_dt(created_from, "开始日期"))
    if created_to:
        query = query.where(AuditLog.created_at <= _parse_audit_dt(created_to, "结束日期"))

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar() or 0
    items = (await db.execute(
        query.offset((page - 1) * page_size).limit(page_size)
    )).scalars().all()
    return {
        "items": [
            {
                "id": a.id,
                "user_id": a.user_id,
                "username": a.username,
                "action": a.action,
                "target_type": a.target_type,
                "target_id": a.target_id,
                "detail": a.detail,
                "ip": a.ip,
                "created_at": a.created_at.isoformat() if a.created_at else "",
            }
            for a in items
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def _parse_audit_dt(value: str, field: str):
    """解析时间边界（兼容 'YYYY-MM-DD HH:MM:SS' 与 ISO 格式），
    无时区时按 Asia/Shanghai（与部署时区一致）解释。"""
    from datetime import datetime as dt
    from zoneinfo import ZoneInfo
    try:
        d = dt.fromisoformat(value.replace(" ", "T"))
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{field}格式不正确")
    if d.tzinfo is None:
        d = d.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    return d


@router.get("/admin/access-logs")
async def list_access_logs(
    keyword: str | None = None,
    method: str | None = None,
    path: str | None = None,
    status: int | None = None,
    page: int = 1,
    page_size: int = 20,
    admin: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """访问日志查询（仅超级管理员）：读取 access.log 文件，按时间倒序分页。

    不落库；文件过大时建议挂载卷查看或配置轮转。
    """
    if admin.role != "superadmin":
        raise HTTPException(status_code=403)
    if page < 1 or page_size < 1 or page_size > 100:
        raise HTTPException(status_code=400, detail="分页参数不合法")

    settings = get_settings()
    lines = await asyncio.to_thread(_read_access_log_lines, settings.AUDIT_LOG_PATH)
    items = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue  # 容忍写入中的半行/异常行
        if not isinstance(rec, dict):
            continue  # 容忍合法 JSON 但非对象的行
        if keyword:
            kw = keyword.lower()
            if not (
                kw in rec.get("ip", "").lower()
                or kw in rec.get("path", "").lower()
                or kw in (rec.get("username") or "").lower()
                or kw in rec.get("method", "").lower()
                or kw in str(rec.get("status", ""))
            ):
                continue
        if method and rec.get("method", "").upper() != method.upper():
            continue
        if path and path not in rec.get("path", ""):
            continue
        if status is not None and rec.get("status") != status:
            continue
        items.append(rec)

    # 文件是追加写入，按行顺序即时间序；倒序返回（最新在前）
    items.reverse()
    total = len(items)
    start = (page - 1) * page_size
    page_items = items[start:start + page_size]

    # 旧 token 记录的访问日志缺少 username（仅 user_id），批量补齐当前页
    uid_needed = {
        it["user_id"] for it in page_items
        if it.get("user_id") and not it.get("username")
    }
    if uid_needed:
        rows = await db.execute(
            select(User.id, User.username).where(User.id.in_(uid_needed))
        )
        uname_map = dict(rows.all())
        for it in page_items:
            if not it.get("username") and it.get("user_id") in uname_map:
                it["username"] = uname_map[it["user_id"]]

    return {
        "items": page_items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def _read_access_log_lines(path: str, max_bytes: int = 1024 * 1024) -> list[str]:
    """读取访问日志文件（最多读取末尾 max_bytes 字节，默认 1MB ≈ 数千条记录，
    控制内存/耗时；更早记录在轮转文件 access.log.1~N 中）。文件不存在返回空。"""
    if not path or not os.path.isfile(path):
        return []
    try:
        size = os.path.getsize(path)
        with open(path, "r", encoding="utf-8") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
                f.readline()  # 丢弃可能不完整的首行
            return f.readlines()
    except OSError:
        return []


@router.get("/admin/users", response_model=list[UserProfile])
async def list_users(
    admin: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    if admin.role != "superadmin":
        raise HTTPException(status_code=403)
    result = await db.execute(select(User).order_by(User.id))
    users = result.scalars().all()
    inviter_ids = {u.invited_by for u in users if u.invited_by}
    inviters: dict[int, str] = {}
    if inviter_ids:
        rows = await db.execute(select(User.id, User.username).where(User.id.in_(inviter_ids)))
        inviters = {uid: name for uid, name in rows.all()}
    return [
        _profile(u, invited_by_username=inviters.get(u.invited_by))
        for u in users
    ]


@router.get("/admin/selectable-users", response_model=list[SelectableUser])
async def selectable_users(
    admin: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    if admin.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403)
    result = await db.execute(select(User).order_by(User.id))
    return [
        SelectableUser(
            id=u.id,
            username=u.username,
            avatar=u.avatar or "",
            gender=u.gender,
            role=u.role,
            invited_by=u.invited_by,
        )
        for u in result.scalars().all()
    ]


@router.post("/admin/set-role")
async def set_role(
    body: AdminSetRole,
    admin: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    if admin.role != "superadmin":
        raise HTTPException(status_code=403)
    if body.role not in ("admin", "user"):
        raise HTTPException(status_code=400, detail="角色只能是 admin 或 user")
    if body.user_id == admin.id:
        raise HTTPException(status_code=400, detail="不能修改自己的角色")
    target = await db.execute(select(User).where(User.id == body.user_id))
    target_user = target.scalar_one_or_none()
    if not target_user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if target_user.role == "superadmin":
        raise HTTPException(status_code=403, detail="超级管理员角色不能通过接口修改")
    old_role = target_user.role
    target_user.role = body.role
    await db.flush()
    await audit(
        user=admin, action="admin_set_role",
        target_type="user", target_id=target_user.id,
        detail={"username": target_user.username, "old_role": old_role, "new_role": body.role},
    )
    return {"ok": True, "user_id": target_user.id, "role": target_user.role}


@router.delete("/admin/users/{user_id}")
async def delete_user(
    user_id: int,
    admin: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    if admin.role != "superadmin":
        raise HTTPException(status_code=403)
    u = await db.execute(select(User).where(User.id == user_id))
    user = u.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404)
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="不能删除自己")
    if admin.role != "superadmin" and user.invited_by != admin.id:
        raise HTTPException(status_code=403, detail="只能删除通过自己邀请码注册的用户")
    if admin.role != "superadmin" and user.role != "user":
        raise HTTPException(status_code=403, detail="只能删除普通用户")
    from app.models.tournament import Registration, Tournament, PlayerStats as PS
    from app.models.round import Match, MatchSupport, Notification, RoundPairing

    created = await db.execute(
        select(Tournament).where(Tournament.creator_id == user.id).limit(1)
    )
    if created.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="该用户创建过赛事，请先删除其赛事")
    has_pairings = await db.execute(
        select(RoundPairing.id).where(
            (RoundPairing.player_a_id == user.id) | (RoundPairing.player_b_id == user.id)
        ).limit(1)
    )
    if has_pairings.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="该用户有比赛记录，不能删除")
    # 用户有比赛记录则不能删除，先清理其审计记录（置空 user_id，保留追溯）
    from app.models.audit import AuditLog
    await db.execute(update(AuditLog).where(AuditLog.user_id == user.id).values(user_id=None))
    await db.execute(delete(Registration).where(Registration.user_id == user.id))
    await db.execute(delete(PS).where(PS.user_id == user.id))
    await db.execute(delete(MatchSupport).where(MatchSupport.user_id == user.id))
    await db.execute(delete(Notification).where(Notification.user_id == user.id))
    await db.execute(update(Match).where(Match.referee_id == user.id).values(referee_id=None))
    await db.execute(update(User).where(User.invited_by == user.id).values(invited_by=None))
    _remove_avatar_file(user.avatar)
    await db.delete(user)
    await db.flush()
    await audit(
        user=admin, action="admin_delete_user",
        target_type="user", target_id=user.id,
        detail={"username": user.username},
    )
    return {"ok": True}


@router.post("/admin/reset-password")
async def admin_reset_password(
    body: AdminResetPassword,
    admin: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    if admin.role != "superadmin":
        raise HTTPException(status_code=403)
    u = await db.execute(select(User).where(User.id == body.user_id))
    user = u.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404)
    if len(body.new_password) < 6:
        raise HTTPException(status_code=400, detail="密码至少6位")
    user.password_hash = hash_password(body.new_password)
    # 重置密码后旧 token 失效，需重新登录
    user.token_version += 1
    login_limiter.reset(user.username)
    await db.flush()
    from app.core.websocket import manager
    await manager.kick_user(user.id)
    await audit(
        user=admin, action="admin_reset_password",
        target_type="user", target_id=user.id,
        detail={"username": user.username},
    )
    return {"ok": True}


def _profile(u: User, invited_by_username: str | None = None) -> UserProfile:
    return UserProfile(
        id=u.id, username=u.username, email=u.email,
        avatar=u.avatar, gender=u.gender, role=u.role,
        invite_code=u.invite_code, invited_by=u.invited_by,
        invited_by_username=invited_by_username,
    )


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _is_valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.match(email))



def _looks_like_image(content: bytes, ext: str) -> bool:
    if ext in ("jpg", "jpeg"):
        return content[:3] == b"\xff\xd8\xff"
    if ext == "png":
        return content[:8] == b"\x89PNG\r\n\x1a\n"
    if ext == "gif":
        return content[:6] in (b"GIF87a", b"GIF89a")
    if ext == "webp":
        return content[:4] == b"RIFF" and content[8:12] == b"WEBP"
    return False


def _remove_avatar_file(avatar: str | None) -> None:
    """删除本地上传的头像文件；远程/空头像跳过。"""
    if not avatar:
        return
    if not avatar.startswith("/static/uploads/"):
        return
    filename = avatar.rsplit("/", 1)[-1]
    path = os.path.join(UPLOAD_DIR, filename)
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass
