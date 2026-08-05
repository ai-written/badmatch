import os, re, uuid
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete, update
from sqlalchemy.exc import IntegrityError
from app.core.database import get_db
from app.core.security import create_access_token, get_current_user, hash_password, verify_password, require_user
from app.core.config import get_settings
from app.core.ratelimit import RateLimiter
from app.schemas.auth import (
    RegisterRequest, LoginRequest, TokenResponse, UserProfile, UserStats,
    AdminResetPassword, AdminSetRole, SelectableUser, UpdateProfile,
    ChangePasswordRequest,
)
from app.models.user import User
from app.models.tournament import PlayerStats

router = APIRouter(prefix="/api/auth", tags=["auth"])
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "static", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

_settings = get_settings()
login_limiter = RateLimiter(_settings.LOGIN_MAX_ATTEMPTS, _settings.LOGIN_WINDOW_SECONDS)
login_ip_limiter = RateLimiter(_settings.LOGIN_MAX_ATTEMPTS, _settings.LOGIN_WINDOW_SECONDS)
invite_limiter = RateLimiter(_settings.INVITE_MAX_ATTEMPTS, _settings.INVITE_WINDOW_SECONDS)


@router.post("/register", response_model=TokenResponse)
async def register(req: RegisterRequest, db: AsyncSession = Depends(get_db)):
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

    token = create_access_token({"sub": str(user.id)})
    return TokenResponse(access_token=token, user=_profile(user))


@router.post("/login", response_model=TokenResponse)
async def login(
    req: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    if not login_limiter.check(req.username):
        raise HTTPException(status_code=429, detail="登录尝试次数过多，请稍后再试")
    ip = _client_ip(request)
    if not login_ip_limiter.check(ip):
        raise HTTPException(status_code=429, detail="登录尝试次数过多，请稍后再试")
    result = await db.execute(select(User).where(User.username == req.username))
    user = result.scalar_one_or_none()
    if not user or not verify_password(req.password, user.password_hash):
        login_limiter.record_failure(req.username)
        login_ip_limiter.record_failure(ip)
        raise HTTPException(status_code=400, detail="用户名或密码错误")
    login_limiter.reset(req.username)
    token = create_access_token({"sub": str(user.id)})
    return TokenResponse(access_token=token, user=_profile(user))


@router.get("/me", response_model=UserProfile)
async def me(user: User = Depends(get_current_user)):
    if user is None:
        raise HTTPException(status_code=401)
    return _profile(user)


@router.put("/me/profile")
async def update_profile(
    body: UpdateProfile,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if user is None:
        raise HTTPException(status_code=401)
    if body.username is not None:
        username = body.username.strip()
        if not username:
            raise HTTPException(status_code=400, detail="用户名不能为空")
        exist = await db.execute(select(User).where(User.username == username, User.id != user.id))
        if exist.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="用户名已存在")
        user.username = username
    if body.avatar:
        user.avatar = body.avatar
    if body.gender is not None:
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
        user.email = email
    try:
        await db.flush()
    except IntegrityError:
        raise HTTPException(status_code=400, detail="用户名或邮箱已被使用")
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
    login_limiter.reset(user.username)
    await db.flush()
    return {"ok": True}


@router.post("/upload-avatar")
async def upload_avatar(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if user is None:
        raise HTTPException(status_code=401)
    ext = file.filename.split(".")[-1] if file.filename and "." in file.filename else "jpg"
    if ext.lower() not in ("jpg", "jpeg", "png", "gif", "webp"):
        raise HTTPException(status_code=400, detail="不支持的文件格式")
    filename = f"{uuid.uuid4().hex}.{ext}"
    filepath = os.path.join(UPLOAD_DIR, filename)
    content = await file.read()
    if not content or len(content) > 2 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="文件大小不能超过 2MB")
    if not _looks_like_image(content, ext.lower()):
        raise HTTPException(status_code=400, detail="文件内容与图片格式不匹配")
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
    return {"avatar": user.avatar}


@router.post("/generate-invite")
async def generate_invite(
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
    target_user.role = body.role
    await db.flush()
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
    await db.execute(delete(Registration).where(Registration.user_id == user.id))
    await db.execute(delete(PS).where(PS.user_id == user.id))
    await db.execute(delete(MatchSupport).where(MatchSupport.user_id == user.id))
    await db.execute(delete(Notification).where(Notification.user_id == user.id))
    await db.execute(update(Match).where(Match.referee_id == user.id).values(referee_id=None))
    await db.execute(update(User).where(User.invited_by == user.id).values(invited_by=None))
    _remove_avatar_file(user.avatar)
    await db.delete(user)
    await db.flush()
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
    login_limiter.reset(user.username)
    await db.flush()
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


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


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
