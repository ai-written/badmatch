from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import noload
from sqlalchemy import select, func, delete, or_
from app.core.database import get_db
from app.core.security import require_user
from app.core.audit import audit, get_client_ip
from app.models.user import User
from app.models.tournament import (
    Tournament, TournamentStatus, Registration, PlayerStats, Court, TimeSlot,
)
from app.core.websocket import manager
from app.core.mailer import send_tournament_invite
from app.engine.scheduler import fairness_step
from app.core.config import get_settings
from app.schemas.tournament import (
    TournamentCreate, TournamentBrief, TournamentListOut, TournamentDetail,
    RegistrationOut, CourtOut, TimeSlotOut, CancellationOut,
)

router = APIRouter(prefix="/api/tournaments", tags=["tournaments"])

_brief_loads_cache: tuple | None = None


def _brief_loads() -> tuple:
    """「只要赛事本身」的加载选项。

    Tournament 上的关联都是 lazy="selectin"，默认会把 registrations / courts /
    rounds / matches / player_stats 全部查回来（matches 下还有二级 selectin：
    round_pairings、pairing_a/b、time_slots），而这些接口只用赛事自身的字段，
    报名数和场地由各自的批量查询给出。
    实测 3 个赛事的列表：不做处理会执行 14 条 SQL（其中 10 条在拉整张
    matches / round_pairings / player_stats / time_slots 表），加上后降到 4 条。

    注意必须在调用时构建，不能写成模块级常量：noload() 会立即触发
    mapper 配置，而 import 阶段各模型尚未全部注册（User 的关系里引用了
    Match），会直接抛 InvalidRequestError 让整个模块导入失败。
    """
    global _brief_loads_cache
    if _brief_loads_cache is None:
        _brief_loads_cache = (
            noload(Tournament.registrations),
            noload(Tournament.courts),
            noload(Tournament.rounds),
            noload(Tournament.matches),
            noload(Tournament.player_stats),
        )
    return _brief_loads_cache


# 访问策略：本站是邀请制的封闭群，除积分榜（唯一对外的只读展示面）外，
# 赛事列表/详情/报名名单/战绩/比分这些读取接口都要求登录。
# 前端路由守卫只是把人引到登录页，真正的边界在这里——直接调接口的人绕不过去。
@router.get("", response_model=TournamentListOut)
async def list_tournaments(
    status: str | None = None,
    skip: int = 0,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_user),
):
    skip = max(0, skip)
    limit = max(1, min(limit, 100))
    # 列表只需要赛事本身的字段（报名数与首场地由下面两条批量查询给出）。
    # 关联的懒加载配置见 _brief_loads() 的注释。
    query = select(Tournament).options(*_brief_loads())
    if status:
        query = query.where(Tournament.status == status)
    total = (await db.execute(
        select(func.count()).select_from(query.subquery())
    )).scalar() or 0
    # id 兜底排序：上下场是同一事务创建、created_at 完全相同，
    # 只按 created_at 排序时并列行的顺序会随物理位置漂移（与 matches 同一个坑）
    result = await db.execute(
        query.order_by(Tournament.created_at.desc(), Tournament.id.desc())
        .offset(skip).limit(limit)
    )
    tournaments = result.scalars().all()
    if not tournaments:
        return TournamentListOut(items=[], total=total, has_more=False)

    tournament_ids = [t.id for t in tournaments]
    # 批量报名数（1 次查询替代 N 次）
    cnt_result = await db.execute(
        select(Registration.tournament_id, func.count(Registration.id))
        .where(
            Registration.tournament_id.in_(tournament_ids),
            Registration.is_active == True,
        )
        .group_by(Registration.tournament_id)
    )
    count_map = dict(cnt_result.all())

    # 批量首场地：一次查所有场地，Python 侧取每赛事 sort_order 最小者（1 次查询替代 N 次）
    # 只要场地名，不需要时间段
    court_result = await db.execute(
        select(Court)
        .where(Court.tournament_id.in_(tournament_ids))
        .options(noload(Court.time_slots))
        .order_by(Court.tournament_id, Court.sort_order)
    )
    first_court_map: dict[int, Court] = {}
    for c in court_result.scalars().all():
        first_court_map.setdefault(c.tournament_id, c)

    out = []
    for t in tournaments:
        first_court = first_court_map.get(t.id)
        out.append(TournamentBrief(
            id=t.id,
            title=t.title,
            location=t.location,
            start_date=t.start_date,
            end_date=t.end_date,
            max_participants=t.max_participants,
            status=t.status.value,
            total_matches=t.total_matches,
            points_to_win=t.points_to_win,
            registration_open_at=t.registration_open_at,
            registered_count=count_map.get(t.id, 0),
            court_name=first_court.name if first_court else None,
            created_at=t.created_at.isoformat() if t.created_at else "",
        ))
    return TournamentListOut(
        items=out,
        total=total,
        has_more=skip + len(tournaments) < total,
    )


@router.post("", response_model=TournamentDetail)
async def create_tournament(
    data: TournamentCreate,
    background_tasks: BackgroundTasks,
    request: Request,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    t = await _create_tournament(data, user, db, background_tasks)
    # 响应在事务内构造：构造失败就整体回滚（与原先一致），不会留下"创建成功但返回 500"、
    # 让客户端重试后建出两条赛事的中间态。
    detail = await _tournament_detail(t, db)
    # 先提交再写审计（同 auth.update_profile）：审计用独立会话立即提交，
    # 写在提交前的话，业务事务一回滚就会留下一条"赛事已创建"的假日志。
    await db.commit()
    await audit(
        user=user, action="tournament_create",
        target_type="tournament", target_id=t.id,
        detail={"title": t.title, "max_participants": t.max_participants, "total_matches": t.total_matches},
        ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
    )
    return detail


@router.post("/batch")
async def create_tournaments_batch(
    data: list[TournamentCreate],
    background_tasks: BackgroundTasks,
    request: Request,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    tournament_ids = []
    email_tasks = []
    for item in data:
        t = await _create_tournament(item, user, db, background_tasks, email_tasks=email_tasks)
        tournament_ids.append(t.id)
    sent_emails = set()
    for task in email_tasks:
        if task[0] in sent_emails:
            continue
        sent_emails.add(task[0])
        background_tasks.add_task(send_tournament_invite, *task)
    # 先提交再写审计（同上）
    await db.commit()
    await audit(
        user=user, action="tournament_create_batch",
        target_type="tournament", target_id=tournament_ids[0] if tournament_ids else None,
        detail={"count": len(tournament_ids), "ids": tournament_ids},
        ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
    )
    return {"ok": True, "tournament_ids": tournament_ids}


async def _create_tournament(
    data: TournamentCreate,
    user: User,
    db: AsyncSession,
    background_tasks: BackgroundTasks,
    email_tasks: list | None = None,
) -> Tournament:
    if data.max_participants < 4:
        raise HTTPException(status_code=400, detail="最大人数至少为 4")

    # 定时开放报名：启用时必须选择未来时间；未启用则一律立即开放
    registration_open_at = data.registration_open_at
    if registration_open_at is not None and registration_open_at.tzinfo is not None:
        # 统一转为本地 naive 时间，避免与 datetime.now()/TIMESTAMP 列比较或写入报错
        registration_open_at = registration_open_at.astimezone().replace(tzinfo=None)
    if data.enable_scheduled_registration:
        if registration_open_at is None:
            raise HTTPException(status_code=400, detail="启用定时报名必须选择开放时间")
        if registration_open_at <= datetime.now():
            raise HTTPException(status_code=400, detail="报名开放时间必须晚于当前时间")
    else:
        registration_open_at = None

    preselected = list(dict.fromkeys(data.preselect_player_ids))
    if preselected:
        if user.role not in ("admin", "superadmin"):
            raise HTTPException(status_code=403, detail="只有管理员可以预选参赛人员")
        if len(preselected) > data.max_participants:
            raise HTTPException(status_code=400, detail="预选人数超过最大人数")
        users_result = await db.execute(select(User).where(User.id.in_(preselected)))
        found_users = users_result.scalars().all()
        found_ids = {u.id for u in found_users}
        missing = [uid for uid in preselected if uid not in found_ids]
        if missing:
            raise HTTPException(status_code=400, detail="部分预选用户不存在")
        users_map = {u.id: u for u in found_users}

    t = Tournament(
        creator_id=user.id,
        title=data.title,
        description=data.description,
        location=data.location,
        start_date=data.start_date,
        end_date=data.end_date,
        max_participants=data.max_participants,
        total_matches=data.total_matches,
        points_to_win=data.points_to_win,
        registration_open_at=registration_open_at,
    )
    db.add(t)
    await db.flush()

    for c_data in data.courts:
        court = Court(tournament_id=t.id, name=c_data.name, sort_order=c_data.sort_order)
        db.add(court)
        await db.flush()
        for ts_data in c_data.time_slots:
            db.add(TimeSlot(court_id=court.id, start_time=ts_data.start_time, end_time=ts_data.end_time))

    from app.models.round import Notification

    settings = get_settings()
    start_text = data.start_date.strftime("%Y-%m-%d %H:%M")
    for uid in preselected:
        db.add(Registration(tournament_id=t.id, user_id=uid, is_active=True))
        db.add(Notification(
            user_id=uid,
            tournament_id=t.id,
            type="tournament_invited",
            message=f"您已被预选加入赛事「{data.title}」",
        ))
        target_user = users_map.get(uid)
        if target_user and target_user.email:
            invite_url = f"{settings.FRONTEND_URL}/tournament/{t.id}"
            task_args = (
                target_user.email,
                user.username,
                data.title,
                start_text,
                data.location,
                invite_url,
            )
            if email_tasks is None:
                background_tasks.add_task(send_tournament_invite, *task_args)
            else:
                email_tasks.append(task_args)

    await db.flush()
    return t


@router.get("/default-title")
async def default_title(
    date: str | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_user),
):
    from datetime import date as date_cls

    d = date_cls.today()
    if date:
        try:
            d = date_cls.fromisoformat(date)
        except ValueError:
            raise HTTPException(status_code=400, detail="日期格式不正确")

    month_names = [
        "一月", "二月", "三月", "四月", "五月", "六月",
        "七月", "八月", "九月", "十月", "十一月", "十二月",
    ]
    prefix = month_names[d.month - 1]
    count_result = await db.execute(
        select(func.count(Tournament.id)).where(
            or_(
                Tournament.title.like(f"{prefix}%友谊赛"),
                Tournament.title.like(f"{prefix}%养生局"),
            )
        )
    )
    count = (count_result.scalar() or 0) + 1
    return {"title": f"{prefix}第{count}次养生局"}


@router.get("/{tournament_id}", response_model=TournamentDetail)
async def get_tournament(
    tournament_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_user),
):
    result = await db.execute(
        select(Tournament).where(Tournament.id == tournament_id).options(*_brief_loads())
    )
    t = result.scalar_one_or_none()
    if not t:
        raise HTTPException(status_code=404, detail="赛事不存在")
    return await _tournament_detail(t, db, user)


def assert_can_delete(t: Tournament, user: User) -> None:
    """删除赛事的权限判定（抽成纯函数，便于不起库就单测）。

    - 创建者、管理员、超级管理员都可以删
    - 但默认**只有报名中（open）的赛事**能删：已开赛/已结束的赛事连同全部轮次、
      比赛、战绩都会被级联删掉，误删代价太大（与「赛事结束 = 完全只读」一致）
    - **超级管理员是例外**：可以删除任意状态的赛事 —— 建错的局、脏数据总得有人能
      收尾，这是唯一能跨状态删的角色（前端 `canDelete` 与这里保持同一套规则）
    """
    if t.creator_id != user.id and user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="只有创建者可以删除")
    if t.status != TournamentStatus.OPEN and user.role != "superadmin":
        raise HTTPException(status_code=400, detail="只能删除报名中的赛事（超级管理员可删除任意状态的赛事）")


@router.delete("/{tournament_id}")
async def delete_tournament(
    tournament_id: int,
    request: Request,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Tournament).where(Tournament.id == tournament_id).options(*_brief_loads())
    )
    t = result.scalar_one_or_none()
    if not t:
        raise HTTPException(status_code=404, detail="赛事不存在")
    assert_can_delete(t, user)

    # delete related courts and time slots
    # delete match supports, matches, pairings, rounds
    from app.models.round import Round, RoundPairing, Match, MatchSupport, Notification
    matches = await db.execute(select(Match.id).where(Match.tournament_id == tournament_id))
    for (mid,) in matches.all():
        await db.execute(delete(MatchSupport).where(MatchSupport.match_id == mid))
    rounds = await db.execute(select(Round.id).where(Round.tournament_id == tournament_id))
    for (rid,) in rounds.all():
        await db.execute(delete(Match).where(Match.round_id == rid))
        await db.execute(delete(RoundPairing).where(RoundPairing.round_id == rid))
        await db.execute(delete(Round).where(Round.id == rid))
    # delete player stats and notifications
    await db.execute(delete(PlayerStats).where(PlayerStats.tournament_id == tournament_id))
    await db.execute(delete(Notification).where(Notification.tournament_id == tournament_id))
    from app.models.tournament import Court, TimeSlot
    courts = await db.execute(select(Court).where(Court.tournament_id == tournament_id))
    for court in courts.scalars().all():
        await db.execute(delete(TimeSlot).where(TimeSlot.court_id == court.id))
        await db.delete(court)
    # delete registrations  
    await db.execute(delete(Registration).where(Registration.tournament_id == tournament_id))

    # 快照在 delete **之前**取（同 auth.delete_user）：删完再读列属性虽然现在也能用，
    # 但只要哪天给这个 select 加了 load_only/defer 就会变成"对已删除对象取属性"
    deleted_id, deleted_title, deleted_status = t.id, t.title, t.status.value
    await db.delete(t)
    await db.flush()
    # 先提交再写审计（同上）
    await db.commit()
    await audit(
        user=user, action="tournament_delete",
        target_type="tournament", target_id=deleted_id,
        detail={"title": deleted_title, "status": deleted_status},
        ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
    )
    return {"ok": True}


@router.post("/{tournament_id}/register")
async def register(
    tournament_id: int,
    request: Request,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Tournament).where(Tournament.id == tournament_id).with_for_update()
    )
    t = result.scalar_one_or_none()
    if not t:
        raise HTTPException(status_code=404, detail="赛事不存在")
    if t.status != TournamentStatus.OPEN:
        raise HTTPException(status_code=400, detail="报名已截止")
    if t.registration_open_at and datetime.now() < t.registration_open_at:
        raise HTTPException(status_code=400, detail="报名尚未开始")

    exist = await db.execute(
        select(Registration).where(
            Registration.tournament_id == tournament_id,
            Registration.user_id == user.id,
        )
    )
    reg = exist.scalar_one_or_none()
    if reg and reg.is_active:
        raise HTTPException(status_code=400, detail="已报名")

    # 容量校验必须放在「重新激活」之前：
    # 原先重新激活分支直接 return，容量检查永远走不到，
    # 于是「报名 → 取消 → 别人报满 → 自己再报名」就能突破人数上限。
    cnt_result = await db.execute(
        select(func.count(Registration.id)).where(
            Registration.tournament_id == tournament_id, Registration.is_active == True
        )
    )
    cnt = cnt_result.scalar() or 0
    if cnt >= t.max_participants:
        raise HTTPException(status_code=400, detail="报名人数已满")

    if reg:
        # 曾取消过的记录重新激活，不新增行（有 (tournament_id, user_id) 唯一约束）
        reg.is_active = True
        # 又回来了就不算「取消记录」，清掉取消时间（否则记录页会留着一条已失效的取消）
        reg.cancelled_at = None
        # created_at 当作「这一次报名的时间」：重新报名要刷新，否则名单里会显示几天前
        # 的旧时间、排序也停在第一次报名的位置，与「按报名时间排」的语义相反
        reg.created_at = func.now()
    else:
        db.add(Registration(tournament_id=tournament_id, user_id=user.id))

    # 先提交再广播再审计：审计对请求取消是向上抛的，写在广播前会让广播被跳过；
    # 写在提交前则业务回滚时会留下"已报名"的假日志
    await db.commit()
    await manager.broadcast(tournament_id, {"type": "registration_updated"})
    await audit(user=user, action="registration", target_type="tournament", target_id=t.id, ip=get_client_ip(request))
    return {"ok": True}


@router.post("/{tournament_id}/cancel-register")
async def cancel_register(
    tournament_id: int,
    request: Request,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    t_result = await db.execute(
        select(Tournament).where(Tournament.id == tournament_id).with_for_update()
    )
    t = t_result.scalar_one_or_none()
    if not t:
        raise HTTPException(status_code=404, detail="赛事不存在")
    # 只有报名中的赛事能取消报名。开赛后应走 withdraw（它会同时失效 PlayerStats），
    # 否则会出现「Registration 已失效但 PlayerStats 仍有效」的矛盾状态：
    # 该选手还在打，报名人数与报名列表却少了他。
    if t.status != TournamentStatus.OPEN:
        raise HTTPException(status_code=400, detail="报名已截止，无法取消报名")
    result = await db.execute(
        select(Registration).where(
            Registration.tournament_id == tournament_id,
            Registration.user_id == user.id,
        )
    )
    reg = result.scalar_one_or_none()
    if not reg or not reg.is_active:
        raise HTTPException(status_code=400, detail="未报名")
    reg.is_active = False
    # 时间交给数据库生成（now()），与 created_at 的 server_default=func.now() 同一时区口径
    reg.cancelled_at = func.now()
    # 先提交再广播再审计（同上）
    await db.commit()
    await manager.broadcast(tournament_id, {"type": "registration_updated"})
    await audit(user=user, action="cancel_registration", target_type="tournament", target_id=t.id, ip=get_client_ip(request))
    return {"ok": True}


@router.get("/{tournament_id}/registrations", response_model=list[RegistrationOut])
async def list_registrations(
    tournament_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_user),
):
    from app.models.user import User as UserModel
    result = await db.execute(
        select(Registration, UserModel.username, UserModel.avatar)
        .join(UserModel, Registration.user_id == UserModel.id)
        .where(Registration.tournament_id == tournament_id, Registration.is_active == True)
        # 按报名时间排序：不写 ORDER BY 时返回顺序由执行计划决定，而这两张表都很小、
        # PG 常走 hash join，输出顺序跟插入顺序和报名时间都不一致；前端又是直接渲染
        # 这个数组，于是「已报名」名单看起来是乱的。
        # id 作为同一次批量报名的次序兜底（同一事务里 now() 取值相同）。
        .order_by(Registration.created_at, Registration.id)
    )
    rows = result.all()
    return [
        RegistrationOut(
            id=row[0].id,
            user_id=row[0].user_id,
            username=row[1],
            avatar=row[2],
            created_at=row[0].created_at.isoformat() if row[0].created_at else "",
        )
        for row in rows
    ]


@router.get("/{tournament_id}/cancellations", response_model=list[CancellationOut])
async def list_cancellations(
    tournament_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_user),
):
    """谁取消过报名 / 谁开赛后退出。

    两条路径都会把 Registration.is_active 置 False（报名阶段取消 + 赛中退赛），
    所以这里用「有没有失效的 PlayerStats」区分：有 = 赛中退赛（开赛后才会建
    PlayerStats），没有 = 报名阶段取消（含 cancelled_at 为空的老数据）。
    可见性与报名名单一致（登录用户都能看）——名单本身对登录用户就是公开的，
    单独给取消记录加限制反而会造成「看得见名单、看不见谁退出」的怪状态。
    """
    from app.models.user import User as UserModel
    from app.models.tournament import PlayerStats as PlayerStatsModel
    # 与 /rounds 一致：赛事不存在给 404，否则前端会把「赛事已删除」显示成「暂无记录」
    exists = await db.execute(select(Tournament.id).where(Tournament.id == tournament_id))
    if exists.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="赛事不存在")
    result = await db.execute(
        select(Registration, UserModel.username, UserModel.avatar, PlayerStatsModel.is_active)
        .join(UserModel, Registration.user_id == UserModel.id)
        .outerjoin(
            PlayerStatsModel,
            (PlayerStatsModel.tournament_id == Registration.tournament_id)
            & (PlayerStatsModel.user_id == Registration.user_id),
        )
        .where(Registration.tournament_id == tournament_id, Registration.is_active == False)
        # 老数据的 cancelled_at 为 NULL，排到最后但不要丢掉
        .order_by(Registration.cancelled_at.desc().nulls_last(), Registration.id.desc())
    )
    rows = result.all()
    return [
        CancellationOut(
            user_id=row[0].user_id,
            username=row[1],
            avatar=row[2],
            cancelled_at=row[0].cancelled_at.isoformat() if row[0].cancelled_at else None,
            kind="withdraw" if row[3] is False else "cancel",
        )
        for row in rows
    ]


async def _tournament_detail(t: Tournament, db: AsyncSession, user: User | None = None) -> TournamentDetail:
    cnt_result = await db.execute(
        select(func.count(Registration.id)).where(
            Registration.tournament_id == t.id, Registration.is_active == True
        )
    )
    registered_count = cnt_result.scalar() or 0

    cancelled_result = await db.execute(
        select(func.count(Registration.id)).where(
            Registration.tournament_id == t.id, Registration.is_active == False
        )
    )
    cancelled_count = cancelled_result.scalar() or 0

    courts_result = await db.execute(
        select(Court).where(Court.tournament_id == t.id).order_by(Court.sort_order)
    )
    courts = courts_result.scalars().all()
    court_outs = []
    if courts:
        court_ids = [c.id for c in courts]
        # 批量查所有场地的时间段（1 次查询替代 N 次）
        ts_result = await db.execute(
            select(TimeSlot)
            .where(TimeSlot.court_id.in_(court_ids))
            .order_by(TimeSlot.court_id, TimeSlot.start_time)
        )
        slots_by_court: dict[int, list[TimeSlot]] = {}
        for s in ts_result.scalars().all():
            slots_by_court.setdefault(s.court_id, []).append(s)
        for c in courts:
            slots = slots_by_court.get(c.id, [])
            court_outs.append(CourtOut(
                id=c.id, name=c.name, sort_order=c.sort_order,
                time_slots=[TimeSlotOut(id=s.id, start_time=s.start_time, end_time=s.end_time) for s in slots],
            ))

    is_registered = False
    if user:
        reg = await db.execute(
            select(Registration).where(
                Registration.tournament_id == t.id,
                Registration.user_id == user.id,
                Registration.is_active == True,
            )
        )
        is_registered = reg.scalar_one_or_none() is not None

    return TournamentDetail(
        id=t.id,
        creator_id=t.creator_id,
        title=t.title,
        description=t.description,
        location=t.location,
        start_date=t.start_date,
        end_date=t.end_date,
        max_participants=t.max_participants,
        status=t.status.value,
            total_matches=t.total_matches,
            points_to_win=t.points_to_win,
        registration_open_at=t.registration_open_at,
        server_now=datetime.now(),
        courts=court_outs,
        registered_count=registered_count,
        cancelled_count=cancelled_count,
        is_registered=is_registered,
        created_at=t.created_at.isoformat() if t.created_at else "",
    )


@router.get("/match-options/{num_players}")
async def match_options(num_players: int, user: User = Depends(require_user)):
    """Return valid match counts for given number of players."""
    if num_players < 4:
        return {"options": [], "per_person": 0}
    # 步长与开赛/追加比赛的判据同一个来源（N/gcd(4,N)）
    step = fairness_step(num_players)
    start = step
    while start < 6:
        start += step
    options = []
    m = start
    while m <= 30:
        per = (4 * m) // num_players
        options.append({"total": m, "per_person": per})
        m += step
    return {"options": options, "per_person": (4 * start) // num_players}
