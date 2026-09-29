import os, re, secrets, uuid, logging, json, asyncio
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, UploadFile, File
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
    ChangePasswordRequest, ForgotPasswordRequest, ResetPasswordRequest,
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
# 头像上传限流（按 IP，宽松一些：正常用户改头像不会太频繁）
avatar_limiter = RateLimiter(20, 600)
# WebSocket 连接票据限流（按用户，宽松）：前端每次切页/重连都会申请，
# 正常用量很低（一次连接一张），60 次/分钟足够，仅用于挡住高频滥用
ws_ticket_limiter = RateLimiter(60, 60)
# 找回密码：按来源 IP 限制申请次数，避免被用来给他人邮箱灌邮件
forgot_limiter = RateLimiter(5, 900)
# 提交新密码：按来源 IP 限制，避免拿令牌反复试探
reset_limiter = RateLimiter(10, 900)
# 用户不存在时也要跑一次 bcrypt，才能让「查无此人」与「密码错误」的耗时接近，
# 否则响应时间差可被用来枚举用户名。模块加载时算一次，避免每次请求都算。
_DUMMY_PASSWORD_HASH = hash_password("dummy-password-for-constant-time-compare")


@router.post("/register", response_model=TokenResponse)
async def register(
    req: RegisterRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _validate_password_length(req.password)
    ip = get_client_ip(request)

    # 校验顺序很重要：先校验邀请码，再做用户名/邮箱的占用检查。
    # 反过来（原先的顺序）会让未持有任何凭证的人靠错误文案枚举全站用户名与邮箱
    # —— 提供已存在的用户名会得到「用户名已存在」，不存在的则得到「需要邀请码」。
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
        # 限流 key 用来源 IP，不能用提交上来的邀请码本身：
        # 那个值是攻击者可控的，换一个码就重新计数，等于没有限流。
        if not invite_limiter.check(ip):
            raise HTTPException(status_code=429, detail="邀请码尝试次数过多，请稍后再试")
        inviter = await db.execute(select(User).where(User.invite_code == req.invite_code))
        inviter_user = inviter.scalar_one_or_none()
        if not inviter_user:
            invite_limiter.record_failure(ip)
            raise HTTPException(status_code=400, detail="邀请码无效")
        invited_by_id = inviter_user.id
    else:
        # 首个用户将成为超级管理员，必须提供初始化注册码，防止被抢先注册
        if not init_limiter.check(ip):
            raise HTTPException(status_code=429, detail="初始注册码尝试次数过多，请稍后再试")
        code = req.init_code.strip() if req.init_code else ""
        if not code or not secrets.compare_digest(code, _get_init_code()):
            init_limiter.record_failure(ip)
            raise HTTPException(status_code=400, detail="初始管理员注册码不正确")
        init_limiter.reset(ip)

    # 走到这里说明邀请码已通过校验，此时才做占用性检查
    email = (req.email or "").strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="邮箱不能为空")
    if not _is_valid_email(email):
        raise HTTPException(status_code=400, detail="邮箱格式不正确")
    exist = await db.execute(select(User).where(User.username == req.username))
    if exist.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="用户名已存在")
    # 用 lower() 比较：历史数据里可能存了带大写的邮箱（早期 update_profile
    # 只做了 strip 没做 lower），只比 == 会让 a@x.com 与 A@x.com 同时被占用
    email_exist = await db.execute(select(User).where(func.lower(User.email) == email))
    if email_exist.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="邮箱已被使用")

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
    except IntegrityError as e:
        # 以前不管什么完整性错误都报「用户名或邮箱已被使用」，排查时会被完全带偏：
        # 例如自增序列落后于 users 的最大 id 时会撞 users_pkey，报错却指向用户名/邮箱。
        detail = str(getattr(e, "orig", e))
        if "ix_users_username" in detail:
            raise HTTPException(status_code=400, detail="用户名已被使用")
        if "uq_users_email" in detail:
            raise HTTPException(status_code=400, detail="邮箱已被使用")
        logger.exception("register failed with unexpected IntegrityError")
        raise HTTPException(status_code=500, detail="注册失败，请稍后重试")

    # 必须在这里先提交，再写注册审计。
    # audit() 用的是**独立会话并立即提交**（这样异常路径的审计也能落库），
    # 而 audit_logs.user_id 有指向 users 的外键：用户行若还在本事务里没提交，
    # 那条审计会因外键不满足而写入失败，又被 audit() 的 except 静默吞掉 ——
    # 表现就是「注册在操作日志里根本查不到」，且日志里没有任何报错。
    await db.commit()

    token = create_access_token(
        {"sub": str(user.id), "username": user.username},
        token_version=user.token_version,
    )
    await audit(
        user=user, action="register",
        detail={"is_first": is_first, "role": user.role, "username": user.username},
        ip=ip, user_agent=request.headers.get("user-agent"),
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
    if not user:
        # 用户不存在时也做一次 bcrypt 校验：否则「查不到用户」会立刻返回，
        # 响应时间与「密码错误」明显不同，可用来枚举用户名。
        verify_password(req.password, _DUMMY_PASSWORD_HASH)
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
    # 刻意**不**清零该 IP 的失败计数：登录成功者的身份是攻击者可以自己持有的
    # （注册任意一个账号即可），若成功一次就清零，攻击者用自有账号登录一次
    # 就能把 IP 计数刷回 0，按 IP 的兜底限流形同虚设。
    # 同出口（办公网 NAT）被他人失败尝试牵连的代价，由窗口时间（10 分钟）自然消化。
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


async def _deliver_reset_mail(user_id: int, to_email: str, username: str, url: str, minutes: int) -> None:
    """在响应返回之后发信，并记一条审计。

    两个必须这样做的理由：
    1) smtplib 是同步阻塞调用。放在请求路径上会独占事件循环 —— 实测一次
       time.sleep(1) 就足以让心跳定时器抖动 999ms，也就是期间所有人的记分、
       WebSocket 广播全部停摆。丢到线程里执行。
    2) 放在请求路径上也构成**邮箱枚举的时间侧信道**：命中的分支要等完整一次
       SMTP 会话（未配置时也有 ~11ms 的库操作差异，配置后是几百 ms 到秒级），
       未命中的分支立刻返回。响应体一样但耗时可区分。
    所以「发信」这一步彻底移出请求路径。
    """
    import asyncio as _asyncio
    from app.core.mailer import send_password_reset
    from app.core.database import async_session_factory
    from app.models.user import User as _User

    sent = await _asyncio.to_thread(send_password_reset, to_email, username, url, minutes)
    if not sent:
        logger.warning("密码重置邮件未发出（SMTP 未配置或发送失败）: user_id=%s", user_id)
    try:
        async with async_session_factory() as session:
            u = (await session.execute(select(_User).where(_User.id == user_id))).scalar_one_or_none()
            if u is not None:
                await audit(
                    user=u, action="password_reset_request",
                    target_type="user", target_id=u.id,
                    detail={"sent": sent},
                )
            await session.commit()
    except Exception:
        # 审计失败不能影响已经发出的邮件
        logger.exception("写 password_reset_request 审计失败: user_id=%s", user_id)


@router.post("/forgot-password")
async def forgot_password(
    body: ForgotPasswordRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """申请找回密码：给该邮箱发一封带重置链接的邮件。

    无论邮箱是否存在、是否配置了 SMTP，都返回同一句话 —— 否则这个接口就变成
    「任意人可批量探测某邮箱是否注册过」的工具（注册接口已经因为同类问题
    把占用性检查挪到了邀请码之后，这里同样不能泄露）。

    注意**存在性分支与不存在分支必须走同样的库操作、同样的耗时**：
    只在响应体上一致、耗时上差一截，一样能被批量测出邮箱是否注册。
    """
    ip = get_client_ip(request)
    if not forgot_limiter.check(ip):
        raise HTTPException(status_code=429, detail="操作过于频繁，请稍后再试")
    forgot_limiter.record_failure(ip)

    email = (body.email or "").strip().lower()
    user = None
    if email:
        # 大小写不敏感匹配：库里可能存在历史遗留的带大写邮箱
        # （早期 update_profile 只 strip 不 lower），只比 == 会让这些人
        # 永远收不到重置邮件，且现象是「明明填过邮箱却提示未注册」。
        # with_for_update：与签发令牌处的用户行锁配合，把同一用户的并发申请串行化
        result = await db.execute(
            select(User).where(func.lower(User.email) == email).with_for_update()
        )
        user = result.scalar_one_or_none()

    from app.core.password_reset import TOKEN_TTL_MINUTES

    if user is not None:
        from app.core.password_reset import issue_reset_token
        token = await issue_reset_token(db, user.id, request_ip=ip)
        # token 放在 fragment（#）之后：不会随请求发给服务器，因此不进任何日志，
        # 也不会通过 Referer 泄露给第三方（邮件安全网关预取 URL 也不会带上它，
        # 因此不会误消费这张一次性令牌）
        url = f"{_settings.FRONTEND_URL.rstrip('/')}/reset-password#token={token}"
        await db.commit()
        # 发信交给后台任务：smtplib 是同步阻塞调用，放请求路径上既会卡住事件循环，
        # 也会构成「命中的分支要等完整 SMTP 会话」的时间侧信道（响应体一样但耗时
        # 差几百 ms~秒级，足以批量测出邮箱是否注册）
        background_tasks.add_task(
            _deliver_reset_mail, user.id, user.email, user.username, url, TOKEN_TTL_MINUTES
        )
    else:
        # 用户不存在：走同样的「提交」路径，把耗时可观察差异压到最小。
        # 这里**故意不生成令牌**（user_id 是个不存在的值，插入会被外键拦下），
        # 也就没有「幽灵令牌」可言 —— 而且不存在用户时根本无令牌可发。
        await db.commit()

    return {"ok": True, "message": "如果该邮箱已注册，我们已发送重置邮件，请查收"}


@router.post("/reset-password")
async def reset_password(
    body: ResetPasswordRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """用邮件里的令牌设置新密码。"""
    ip = get_client_ip(request)
    if not reset_limiter.check(ip):
        raise HTTPException(status_code=429, detail="操作过于频繁，请稍后再试")

    _validate_password_length(body.new_password)

    from app.core.password_reset import consume_reset_token
    user_id = await consume_reset_token(db, body.token)
    if user_id is None:
        reset_limiter.record_failure(ip)
        raise HTTPException(status_code=400, detail="重置链接无效或已过期，请重新申请")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        reset_limiter.record_failure(ip)
        raise HTTPException(status_code=400, detail="重置链接无效或已过期，请重新申请")

    user.password_hash = hash_password(body.new_password)
    # 改密后旧 token 必须失效：token_version 自增会让所有已签发的 access token
    # 失效，并主动断开该用户全部 WebSocket 连接
    user.token_version += 1
    login_limiter.reset(user.username)
    await db.flush()
    from app.core.websocket import manager
    from app.core.ws_ticket import revoke_user
    await manager.kick_user(user.id)
    revoke_user(user.id)
    await audit(
        user=user, action="password_reset",
        target_type="user", target_id=user.id,
        detail={"username": user.username},
        ip=ip, user_agent=request.headers.get("user-agent"),
    )
    return {"ok": True}


@router.post("/ws-ticket")
async def issue_ws_ticket(
    user: User = Depends(require_user),
):
    """签发 WebSocket 一次性连接票据。

    浏览器无法给 WebSocket 握手加自定义头，凭证只能放 URL；而 nginx 会把
    请求行写进 access log，等于长期有效的 JWT 明文落盘。改为先用这个接口
    （JWT 走 Authorization 头，不落日志）换一张一次性短时票据。

    这里不写审计：连接票据是高频、无业务含义的操作，写审计只会淹没日志
    （用户每次切页面/重连都会申请）。
    """
    from app.core.ws_ticket import issue_ticket, TICKET_TTL_SECONDS
    # 宽松限流：前端每次切页/重连都会申请，正常用量很低；
    # 加限流是为了挡住高频调用（票据池的清理是摊还 O(1)，但仍不该无限调用）
    if not ws_ticket_limiter.check(str(user.id)):
        raise HTTPException(status_code=429, detail="操作过于频繁，请稍后再试")
    ws_ticket_limiter.record_failure(str(user.id))
    try:
        ticket = issue_ticket(user.id)
    except RuntimeError:
        raise HTTPException(status_code=503, detail="连接票据繁忙，请稍后重试")
    return {"ticket": ticket, "expires_in": TICKET_TTL_SECONDS}


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
    from app.core.ws_ticket import revoke_user
    await manager.kick_user(user.id)
    # 一并作废未使用的连接票据，否则登出后 60 秒内仍可用旧票据建连
    revoke_user(user.id)
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
    if body.gender is not None:
        changes["gender"] = {"old": user.gender, "new": body.gender or None}
        user.gender = body.gender or None
    if body.email is not None:
        # 统一转小写：注册与找回流程都按小写存/查，这里之前只 strip 不 lower，
        # 于是用户填 MixedCase@Example.COM 会被原样入库，
        # 之后「忘记密码」用小写查询永远命中不了该账号
        email = body.email.strip().lower() or None
        if email:
            if not _is_valid_email(email):
                raise HTTPException(status_code=400, detail="邮箱格式不正确")
            email_exist = await db.execute(
                select(User).where(func.lower(User.email) == email, User.id != user.id)
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
    _validate_password_length(body.new_password)
    user.password_hash = hash_password(body.new_password)
    # 改密码后旧 token 全部失效，需重新登录
    user.token_version += 1
    login_limiter.reset(user.username)
    await db.flush()
    from app.core.websocket import manager
    from app.core.ws_ticket import revoke_user
    await manager.kick_user(user.id)
    revoke_user(user.id)
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
    ip = get_client_ip(request)
    if not avatar_limiter.check(ip):
        raise HTTPException(status_code=429, detail="上传过于频繁，请稍后再试")
    ext = file.filename.split(".")[-1] if file.filename and "." in file.filename else "jpg"
    if ext.lower() not in ("jpg", "jpeg", "png", "gif", "webp"):
        avatar_limiter.record_failure(ip)
        raise HTTPException(status_code=400, detail="不支持的文件格式")
    content = await file.read()
    if not content or len(content) > 2 * 1024 * 1024:
        avatar_limiter.record_failure(ip)
        raise HTTPException(status_code=400, detail="文件大小不能超过 2MB")
    if not _looks_like_image(content, ext.lower()):
        avatar_limiter.record_failure(ip)
        raise HTTPException(status_code=400, detail="文件内容与图片格式不匹配")
    # 压缩处理：缩放至 100px、白底合成、统一 JPEG 输出（低带宽友好）。
    # PIL 是同步 CPU 操作，必须丢到线程池执行：直接跑在事件循环里会阻塞
    # 整个服务（多人同时上传时其他请求一起卡住，表现为主页/记分也超时）。
    try:
        content = await asyncio.to_thread(_process_avatar, content)
    except Exception:
        avatar_limiter.record_failure(ip)
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
    """压缩头像：修正 EXIF 方向、按短边中心裁成正方形、等比缩小、透明背景白底合成、JPEG 输出。

    为什么要裁成正方形：头像在界面里一律画成圆形（object-fit 只会裁不会拉），
    非正方形存下来要么被拉变形、要么在圆形里留边。裁掉比留白好——留白是永久留在
    图像里的，任何展示都救不回来。
    """
    from io import BytesIO
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        img = Image.open(BytesIO(content))
        img = ImageOps.exif_transpose(img)  # 修正手机拍照方向
        # 先按短边中心裁成正方形（保留原始分辨率），再用 thumbnail 缩小：
        # thumbnail 不会放大，所以小图仍保持原尺寸，只是变成正方形
        side = min(img.size)
        if side <= 0:
            raise ValueError("图片尺寸非法")
        left = (img.width - side) // 2
        top = (img.height - side) // 2
        img = img.crop((left, top, left + side, top + side))
        img.thumbnail((max_size, max_size))  # 保持宽高比缩放（此时宽高已相等）
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
    # 邀请码列有唯一约束，撞码时 flush 会抛 IntegrityError（未被捕获 → 500）。
    # 虽然 32 位空间碰撞概率极低，但生成失败不该表现成服务器错误：
    # 重试几次，并把长度从 8 位提到 12 位（48 位空间）进一步降低概率。
    for _ in range(5):
        code = secrets.token_hex(6)
        clash = await db.execute(select(User).where(User.invite_code == code))
        if clash.scalar_one_or_none() is None:
            break
    else:
        raise HTTPException(status_code=500, detail="邀请码生成失败，请重试")
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
async def user_stats_by_id(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_user),
):
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

    query = select(AuditLog).order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
    if action:
        query = query.where(AuditLog.action == action)
    if username:
        # 转义 LIKE 通配符：用户名里输入 % 或 _ 会被当成通配符，
        # 搜「%」会匹配全部记录，用户会以为搜索失效
        safe = username.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.where(AuditLog.username.ilike(f"%{safe}%", escape="\\"))
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
    控制内存/耗时；更早记录在轮转文件 access.log.1~N 中）。文件不存在返回空。

    必须按二进制读再解码：文本模式下 seek 到任意字节偏移可能恰好落在
    一个多字节 UTF-8 字符中间（日志里有中文用户名/查询词），解码器会抛
    UnicodeDecodeError —— 它是 ValueError 的子类，下面只捕获 OSError 是拦不住的，
    结果管理页的访问日志接口会直接 500。errors="replace" 只影响被截断的那一行，
    而解析处的 try 本来就会跳过坏行。
    """
    if not path or not os.path.isfile(path):
        return []
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
                f.readline()  # 丢弃可能不完整的首行
            data = f.read()
        return data.decode("utf-8", errors="replace").splitlines()
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
    request: Request,
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
        ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
    )
    return {"ok": True, "user_id": target_user.id, "role": target_user.role}


@router.delete("/admin/users/{user_id}")
async def delete_user(
    user_id: int,
    request: Request,
    admin: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    # 权限模型必须与前端一致（AdminView 会给普通 admin 显示「自己邀请的用户」的删除按钮）：
    # 超管可删任意人，普通 admin 只能删自己邀请来的普通用户。
    # 原先这里是无条件「非超管即 403」，后面两条规则永远不执行 —— 是死代码，
    # 而且一旦有人为了修前端把这个条件放宽，删除权限会立刻变成
    # 「admin 可删任意用户（含其他 admin）」，因为那两条规则已经不生效了。
    if admin.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403)
    u = await db.execute(select(User).where(User.id == user_id))
    user = u.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404)
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="不能删除自己")
    if admin.role != "superadmin":
        if user.invited_by != admin.id:
            raise HTTPException(status_code=403, detail="只能删除通过自己邀请码注册的用户")
        if user.role != "user":
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
    # 用户被删除后无法再作为裁判：连带清掉卸任时间标记，
    # 否则会留下 referee_id 为空但 referee_released_at 有值的自相矛盾数据
    await db.execute(
        update(Match)
        .where(Match.referee_id == user.id)
        .values(referee_id=None, referee_released_at=None)
    )
    await db.execute(update(User).where(User.invited_by == user.id).values(invited_by=None))
    _remove_avatar_file(user.avatar)
    await db.delete(user)
    await db.flush()
    # 断开该用户已建立的 WebSocket 并作废其未用票据。
    # 原先删用户没有这一步（logout/改密/重置密码都有），被删用户在别的标签页里
    # 仍会继续收到推送，直到连接自然断开。
    from app.core.websocket import manager
    from app.core.ws_ticket import revoke_user
    await manager.kick_user(user.id)
    revoke_user(user.id)
    await audit(
        user=admin, action="admin_delete_user",
        target_type="user", target_id=user.id,
        detail={"username": user.username},
        ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
    )
    return {"ok": True}


@router.post("/admin/reset-password")
async def admin_reset_password(
    body: AdminResetPassword,
    request: Request,
    admin: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    if admin.role != "superadmin":
        raise HTTPException(status_code=403)
    u = await db.execute(select(User).where(User.id == body.user_id))
    user = u.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404)
    _validate_password_length(body.new_password)
    user.password_hash = hash_password(body.new_password)
    # 重置密码后旧 token 失效，需重新登录
    user.token_version += 1
    login_limiter.reset(user.username)
    await db.flush()
    from app.core.websocket import manager
    from app.core.ws_ticket import revoke_user
    await manager.kick_user(user.id)
    revoke_user(user.id)
    await audit(
        user=admin, action="admin_reset_password",
        target_type="user", target_id=user.id,
        detail={"username": user.username},
        ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
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


def _validate_password_length(password: str) -> None:
    """密码长度校验：至少 6 个字符；bcrypt 上限 72 字节（UTF-8），超长会被静默截断。"""
    if len(password) < 6 or len(password.encode("utf-8")) > 72:
        raise HTTPException(status_code=400, detail="密码至少 6 位，且不超过 72 字节")


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
