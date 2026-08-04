import os, uuid
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete, update
from app.core.database import get_db
from app.core.security import create_access_token, get_current_user, hash_password, verify_password, require_user
from app.schemas.auth import (
    RegisterRequest, LoginRequest, TokenResponse, UserProfile, UserStats,
    AdminResetPassword, AdminSetRole, SelectableUser,
)
from app.models.user import User
from app.models.tournament import PlayerStats

router = APIRouter(prefix="/api/auth", tags=["auth"])
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "static", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)


@router.post("/register", response_model=TokenResponse)
async def register(req: RegisterRequest, db: AsyncSession = Depends(get_db)):
    exist = await db.execute(select(User).where(User.username == req.username))
    if exist.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="用户名已存在")

    first_check = await db.execute(select(func.count(User.id)))
    is_first = first_check.scalar() == 0

    invited_by_id = None
    if not is_first:
        if not req.invite_code:
            raise HTTPException(status_code=400, detail="需要邀请码")
        inviter = await db.execute(select(User).where(User.invite_code == req.invite_code))
        inviter_user = inviter.scalar_one_or_none()
        if not inviter_user:
            raise HTTPException(status_code=400, detail="邀请码无效")
        invited_by_id = inviter_user.id

    user = User(
        username=req.username,
        password_hash=hash_password(req.password),
        gender=req.gender,
        invited_by=invited_by_id,
        role="superadmin" if is_first else "user",
    )
    db.add(user)
    await db.flush()

    token = create_access_token({"sub": str(user.id)})
    return TokenResponse(access_token=token, user=_profile(user))


@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.username == req.username))
    user = result.scalar_one_or_none()
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=400, detail="用户名或密码错误")
    token = create_access_token({"sub": str(user.id)})
    return TokenResponse(access_token=token, user=_profile(user))


@router.get("/me", response_model=UserProfile)
async def me(user: User = Depends(get_current_user)):
    if user is None:
        raise HTTPException(status_code=401)
    return _profile(user)


@router.put("/me/profile")
async def update_profile(
    username: str = "",
    avatar: str = "",
    gender: str = "",
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if user is None:
        raise HTTPException(status_code=401)
    if username:
        exist = await db.execute(select(User).where(User.username == username, User.id != user.id))
        if exist.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="用户名已存在")
        user.username = username
    if avatar:
        user.avatar = avatar
    if gender:
        user.gender = gender
    await db.flush()
    return {"ok": True}


@router.put("/me/password")
async def change_password(
    old_password: str = "",
    new_password: str = "",
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if user is None:
        raise HTTPException(status_code=401)
    if not verify_password(old_password, user.password_hash):
        raise HTTPException(status_code=400, detail="原密码错误")
    if len(new_password) < 6:
        raise HTTPException(status_code=400, detail="新密码至少6位")
    user.password_hash = hash_password(new_password)
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
    user.avatar = f"/static/uploads/{filename}"
    await db.flush()
    return {"avatar": user.avatar}


@router.post("/generate-invite")
async def generate_invite(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if user is None:
        raise HTTPException(status_code=401)
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
    return [_profile(u) for u in result.scalars().all()]


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
    await db.flush()
    return {"ok": True}


def _profile(u: User) -> UserProfile:
    return UserProfile(
        id=u.id, username=u.username, 
        avatar=u.avatar, gender=u.gender, role=u.role,
        invite_code=u.invite_code,
    )


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
