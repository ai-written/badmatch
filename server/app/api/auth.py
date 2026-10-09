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
    AdminResetPassword, AdminSetRole, AdminSetActive, SelectableUser, UpdateProfile,
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
# 兜底那把按 IP，阈值独立且宽松得多（理由见 config.py 里的注释）：
# 5 次/10 分钟是「单个账号」的额度，拿它卡整个出口 IP 会误伤同网络的其他人。
# 这份计数只在「管理员重置密码」时被清（reset_all），邮件链接重置刻意不动它 ——
# 否则被锁住的人只要走一次邮件重置，就能把整个出口 IP 的额度也刷掉。
# 注意：按用户名那把可以被**别人**触发（key 来自请求体、先于凭证校验），
# 知道用户名的人每窗口打 5 次错密码就能让该账号登不上；自救路径是邮件重置或管理员重置。
login_ip_limiter = RateLimiter(_settings.LOGIN_IP_MAX_ATTEMPTS, _settings.LOGIN_IP_WINDOW_SECONDS)
invite_limiter = RateLimiter(_settings.INVITE_MAX_ATTEMPTS, _settings.INVITE_WINDOW_SECONDS)
# 初始管理员注册码防爆破（按 IP 限流）
init_limiter = RateLimiter(_settings.INVITE_MAX_ATTEMPTS, _settings.INVITE_WINDOW_SECONDS)
# 头像上传限流（按 IP，宽松一些：正常用户改头像不会太频繁）
avatar_limiter = RateLimiter(20, 600)
# WebSocket 连接票据限流（按用户，宽松）：前端每次切页/重连都会申请，
# 正常用量很低（一次连接一张），60 次/分钟足够，仅用于挡住高频滥用
ws_ticket_limiter = RateLimiter(60, 60)
# 找回密码：按来源 IP 限制申请次数，避免被用来给他人邮箱灌邮件
forgot_limiter = RateLimiter(_settings.FORGOT_MAX_ATTEMPTS, _settings.FORGOT_WINDOW_SECONDS)
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
        # 已禁用的账号不能再拿自己的邀请码拉人：否则"禁用"就漏了一个还能继续产生影响
        # 的入口，而且新账号的 invited_by 会指向一个禁用账号（普通 admin 谁也管不了，
        # 只有超管能处理）。文案与"邀请码无效"保持一致，不额外暴露是谁被禁用了。
        if not inviter_user or not inviter_user.is_active:
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
        if "uq_users_email" in detail or "users_email_key" in detail:
            # 两个名字都要认：迁移加的叫 uq_users_email，而 create_all 建的新库
            # PG 自动命名成 users_email_key（模型里 unique=True 没有命名约定）。
            # 只认一个的话，新库上「邮箱重复」会被报成 500「注册失败，请稍后重试」。
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
        raise HTTPException(status_code=429, detail=f"登录尝试次数过多，请 {_settings.LOGIN_WINDOW_SECONDS // 60} 分钟后再试")
    ip = get_client_ip(request)
    if not login_ip_limiter.check(ip):
        raise HTTPException(status_code=429, detail=f"登录尝试次数过多，请 {_settings.LOGIN_IP_WINDOW_SECONDS // 60} 分钟后再试")
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
    # 凭据正确、但账号已被禁用：这时对方已经证明自己知道密码，明说不会变成
    # "探测某账号是否被禁用"的额外渠道（密码错的人拿到的仍是那句通用提示）
    if not user.is_active:
        await audit(
            user=None, action="login_failed",
            detail={"username": req.username, "reason": "disabled"},
            ip=ip, user_agent=request.headers.get("user-agent"),
        )
        raise HTTPException(status_code=403, detail="账号已被禁用，请联系管理员")
    # 刻意**不**清零该 IP 的失败计数：登录成功者的身份是攻击者可以自己持有的
    # （注册任意一个账号即可），若成功一次就清零，攻击者用自有账号登录一次
    # 就能把 IP 计数刷回 0，按 IP 的兜底限流形同虚设。
    # 同出口（办公网 NAT）被他人失败尝试牵连的代价，由窗口时间（10 分钟）自然消化。
    # 登录本身不写业务数据，这里提交只是收尾只读事务：保持与其它接口一致的
    # 「先提交、再写审计」顺序，将来若在登录里加了写入也不会退化成假日志
    await db.commit()
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
            # 先收尾这个只读事务，再写审计（审计用自己的会话，顺序统一成"先提交再审计"）
            await session.commit()
            if u is not None:
                await audit(
                    user=u, action="password_reset_request",
                    target_type="user", target_id=u.id,
                    detail={"sent": sent},
                )
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
        raise HTTPException(status_code=429, detail=f"操作过于频繁，请 {_settings.FORGOT_WINDOW_SECONDS // 60} 分钟后再试")
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
    # 先提交再写审计（同 update_profile/register）：审计用独立会话立即提交，
    # 写在提交前的话，业务事务一旦回滚就会留下一条"密码已重置"的假日志。
    await db.commit()
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
    # 先提交再写审计（同上）：token_version 的改动要真的落库，才谈得上"已登出"
    await db.commit()
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
    # 先提交再写审计：audit() 用独立会话插入 audit_logs，而这里刚更新了 users 行
    # （username/email 都带唯一索引）。写在提交之前，审计记录的是一次还没落库的改动 ——
    # 提交失败就会留下一条假日志。register / upload_avatar 都是这个顺序。
    await db.commit()
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
    # 先提交再写审计（同上）：改密的落库与"已改密"的审计不能脱节
    await db.commit()
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
    # 同 update_profile：先提交再审计，避免审计记录一次未落库的改动
    await db.commit()
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
    include_disabled: bool = False,
    admin: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """可选用户列表：创建赛事时选「默认参赛人员」用。

    默认**不含已禁用的账号**（他们不该再被选进来）。
    管理面板（普通 admin 也用这个接口看名单）要带 `include_disabled=true`，
    否则一旦禁用了自己邀请的人，就再也找不到他、也就无法恢复。
    """
    if admin.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403)
    # 过滤放在服务端而不是只靠前端：旧页面/直接调接口都会绕过前端校验
    stmt = select(User).order_by(User.id)
    if not include_disabled:
        stmt = stmt.where(User.is_active == True)
    result = await db.execute(stmt)
    return [
        SelectableUser(
            id=u.id,
            username=u.username,
            avatar=u.avatar or "",
            gender=u.gender,
            role=u.role,
            invited_by=u.invited_by,
            is_active=u.is_active,
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
    # 先提交再写审计（同上）
    await db.commit()
    await audit(
        user=admin, action="admin_set_role",
        target_type="user", target_id=target_user.id,
        detail={"username": target_user.username, "old_role": old_role, "new_role": body.role},
        ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
    )
    return {"ok": True, "user_id": target_user.id, "role": target_user.role}


async def _transfer_ownership_from(db: AsyncSession, user_id: int) -> int:
    """把 user_id 名下的「报名中 / 进行中」赛事房主转给一个未禁用的选手。

    禁用某人时用：他登录不进来，房主徽标却还挂在他名下，而「代别人退赛」只有房主能做。
    候选人规则与「房主退赛」一致（在场/已报名 + 账号可用）；一个都找不到就保持原样
    （开始比赛、追加比赛、提前结束这些管理员也能做，不至于彻底卡死）。
    """
    from app.models.tournament import (
        Registration, Tournament, TournamentStatus, PlayerStats,
    )

    rows = await db.execute(
        select(Tournament).where(
            Tournament.creator_id == user_id,
            Tournament.status.in_([TournamentStatus.OPEN, TournamentStatus.ONGOING]),
        )
    )
    moved = 0
    for t in rows.scalars().all():
        if t.status == TournamentStatus.ONGOING:
            # 进行中：优先在场选手里还没被禁用的
            candidate_stmt = (
                select(PlayerStats.user_id)
                .join(User, User.id == PlayerStats.user_id)
                .where(
                    PlayerStats.tournament_id == t.id,
                    PlayerStats.is_active == True,
                    PlayerStats.user_id != user_id,
                    User.is_active == True,
                )
                .order_by(PlayerStats.id)
                .limit(1)
            )
        else:
            # 报名中：还没有 PlayerStats，用活跃报名记录
            candidate_stmt = (
                select(Registration.user_id)
                .join(User, User.id == Registration.user_id)
                .where(
                    Registration.tournament_id == t.id,
                    Registration.is_active == True,
                    Registration.user_id != user_id,
                    User.is_active == True,
                )
                .order_by(Registration.created_at, Registration.id)
                .limit(1)
            )
        candidate = (await db.execute(candidate_stmt)).scalar_one_or_none()
        if candidate:
            t.creator_id = candidate
            moved += 1
    return moved


async def _notify_creators_about_disabled(db: AsyncSession, user_id: int, username: str) -> int:
    """给「被禁用者仍留在名单/赛程里」的赛事房主发站内消息。

    禁用刻意不动报名与 PlayerStats（那是「退赛」的语义），所以他还留在名单和已排好的
    赛程里：报名中的赛事在开赛时会自动取消他的报名，进行中的则需要房主用「退赛」
    把他移出（未开打的轮次会自动重排）。不提醒的话，房主只会看到名单里多了一个
    不来了的人，不知道该做什么。
    """
    from app.models.round import Notification
    from app.models.tournament import (
        Registration, Tournament, TournamentStatus, PlayerStats,
    )

    affected: dict[int, str] = {}
    ongoing = await db.execute(
        select(Tournament.id, Tournament.title)
        .join(PlayerStats, PlayerStats.tournament_id == Tournament.id)
        .where(
            PlayerStats.user_id == user_id,
            PlayerStats.is_active == True,
            Tournament.status == TournamentStatus.ONGOING,
        )
    )
    for tid, title in ongoing.all():
        affected[tid] = title
    open_regs = await db.execute(
        select(Tournament.id, Tournament.title)
        .join(Registration, Registration.tournament_id == Tournament.id)
        .where(
            Registration.user_id == user_id,
            Registration.is_active == True,
            Tournament.status == TournamentStatus.OPEN,
        )
    )
    for tid, title in open_regs.all():
        affected.setdefault(tid, title)
    if not affected:
        return 0

    creators = await db.execute(
        select(Tournament.id, Tournament.creator_id).where(Tournament.id.in_(list(affected)))
    )
    notified = 0
    for tid, creator_id in creators.all():
        # 房主就是他自己的情况在 _transfer_ownership_from 之后已经不存在了，
        # 这里再兜一层：不给自己发消息
        if not creator_id or creator_id == user_id:
            continue
        db.add(Notification(
            user_id=creator_id,
            tournament_id=tid,
            type="player_disabled",
            message=(f"选手「{username}」已被管理员禁用，但仍留在赛事「{affected[tid]}」的"
                     f"名单/赛程里。报名中的赛事会在开赛时自动取消他的报名；"
                     f"进行中的赛事请用「退赛」把他移出（未开打的轮次会自动重排）。"),
        ))
        notified += 1
    return notified


@router.post("/admin/set-active")
async def set_user_active(
    body: AdminSetActive,
    request: Request,
    admin: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """禁用 / 恢复一个账号（数据全部保留，只挡登录与新的参与行为）。

    与「删除用户」的关系：删除会被「有比赛记录 / 创建过赛事」两条护栏挡住，
    而真实场景里常常只是想让他别再来了 —— 那就禁用：历史比赛、战绩、审计都在，
    只是登录不进去、也不再被预选/报名/执裁。权限模型与删除保持一致
    （超管可操作任意人；普通 admin 只能操作自己邀请来的普通用户），
    另外超级管理员和「自己」不能被禁用，避免把管理入口锁死。
    """
    if admin.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403)
    # 行锁：与「状态没变就不动 token_version」的判断配合，避免两个管理员并发
    # 下发相反操作时都通过判断、终态取决于落库顺序（界面显示与实际不一致）
    u = await db.execute(
        select(User).where(User.id == body.user_id).with_for_update()
    )
    user = u.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="不能禁用自己的账号")
    if user.role == "superadmin":
        raise HTTPException(status_code=403, detail="不能禁用超级管理员")
    if admin.role != "superadmin":
        if user.invited_by != admin.id:
            raise HTTPException(status_code=403, detail="只能操作通过自己邀请码注册的用户")
        if user.role != "user":
            raise HTTPException(status_code=403, detail="只能操作普通用户")

    if user.is_active == body.is_active:
        # 状态没变就不要动 token_version：否则「重复点一次禁用」会把已恢复的人
        # 再次踢下线，行为与界面提示不符
        return {"ok": True, "changed": False, "user_id": user.id, "is_active": user.is_active}

    from datetime import datetime
    from app.models.round import Match, MatchStatus

    username = user.username
    user.is_active = body.is_active
    if not body.is_active:
        # 禁用即失效：自增版本号让所有已签发的 token 立刻作废，
        # 再断开 WebSocket（与登出/改密/删号同一套机制）
        user.token_version += 1
        # 在任的裁判场次标记卸任：referee_id 保留作执裁历史（与选手主动卸任同一形态），
        # 否则那些场次会一直挂着一个永远不可能来操作的人，看起来像"已经有裁判了"。
        # **已结束的比赛不动**：那边的裁判记录是归档，release_referee 本身也拒绝
        # 卸任已结束的比赛；标成「已卸任」会把历史改错。
        released = await db.execute(
            update(Match)
            .where(
                Match.referee_id == user.id,
                Match.referee_released_at.is_(None),
                Match.status != MatchStatus.FINISHED,
            )
            .values(referee_released_at=datetime.now())
        )
        released_count = released.rowcount or 0
        await db.flush()
        # 他可能是房主：把赛事交给一个未禁用的选手，否则房主徽标挂在一个登录不进来
        # 的人名下，而且"代别人退赛"这类只有房主能做的操作没人能做
        ownership_transferred = await _transfer_ownership_from(db, user.id)
        # 他还留在名单/赛程里 —— 提醒房主怎么处理（开赛前没有别的入口能把别人移出）
        notified = await _notify_creators_about_disabled(db, user.id, username)
        from app.core.websocket import manager
        from app.core.ws_ticket import revoke_user
        await manager.kick_user(user.id)
        revoke_user(user.id)
    else:
        released_count = 0
        ownership_transferred = 0
        notified = 0
        await db.flush()

    is_active = user.is_active
    await db.commit()
    await audit(
        user=admin,
        action="admin_disable_user" if not is_active else "admin_enable_user",
        target_type="user", target_id=user.id,
        detail={
            "username": username,
            "released_referee_matches": released_count,
            # 禁用时顺带做了两件"善后"：转走他名下的赛事房主、给受影响的房主发提醒
            "ownership_transferred": ownership_transferred,
            "notified_tournaments": notified,
        },
        ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
    )
    return {"ok": True, "changed": True, "user_id": user.id, "is_active": is_active}


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
    # 提交前留快照：审计要用，而提交之后这个对象已经是"已删除"状态
    deleted_id, deleted_username = user.id, user.username
    await db.delete(user)
    await db.flush()
    # 断开该用户已建立的 WebSocket 并作废其未用票据。
    # 原先删用户没有这一步（logout/改密/重置密码都有），被删用户在别的标签页里
    # 仍会继续收到推送，直到连接自然断开。
    from app.core.websocket import manager
    from app.core.ws_ticket import revoke_user
    await manager.kick_user(deleted_id)
    revoke_user(deleted_id)
    # 先提交再写审计（同上）
    await db.commit()
    await audit(
        user=admin, action="admin_delete_user",
        target_type="user", target_id=deleted_id,
        detail={"username": deleted_username},
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
    # 同时清掉按 IP 的登录兜底计数：管理员重置密码的目的就是「让这个人能立刻登进来」，
    # 而卡住他的往往正是 IP 那把（它不随任何自助重置清零）。
    # 这里整体清空而不是「只清他失败过的 IP」：管理员本来就能重置任意用户的密码
    # （比这强得多），而按用户名的 5 次/10 分钟那把仍独立生效，兜底临时归零不放水。
    # 注意：邮件链接重置（reset_password）刻意**不**动这把 —— 否则任何拿到有效重置令牌的
    # 人都能把按 IP 的兜底刷回零，这层防护就形同虚设。
    login_ip_limiter.reset_all()
    await db.flush()
    from app.core.websocket import manager
    from app.core.ws_ticket import revoke_user
    await manager.kick_user(user.id)
    revoke_user(user.id)
    # 先提交再写审计（同上）
    await db.commit()
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
        is_active=u.is_active,
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
