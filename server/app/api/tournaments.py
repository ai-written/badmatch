from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete, or_
from app.core.database import get_db
from app.core.security import require_user, get_current_user
from app.models.user import User
from app.models.tournament import (
    Tournament, TournamentStatus, Registration, PlayerStats, Court, TimeSlot,
)
from app.core.websocket import manager
from app.core.mailer import send_tournament_invite
from app.core.config import get_settings
from app.schemas.tournament import (
    TournamentCreate, TournamentBrief, TournamentDetail, RegistrationOut, CourtOut, TimeSlotOut,
)

router = APIRouter(prefix="/api/tournaments", tags=["tournaments"])


@router.get("", response_model=list[TournamentBrief])
async def list_tournaments(
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    query = select(Tournament).order_by(Tournament.created_at.desc())
    if status:
        query = query.where(Tournament.status == status)
    result = await db.execute(query)
    tournaments = result.scalars().all()

    out = []
    for t in tournaments:
        cnt_result = await db.execute(
            select(func.count(Registration.id)).where(
                Registration.tournament_id == t.id, Registration.is_active == True
            )
        )
        registered_count = cnt_result.scalar() or 0
        court_result = await db.execute(select(Court).where(Court.tournament_id == t.id).order_by(Court.sort_order).limit(1))
        first_court = court_result.scalar_one_or_none()
        out.append(TournamentBrief(
            id=t.id,
            title=t.title,
            location=t.location,
            start_date=t.start_date,
            end_date=t.end_date,
            max_participants=t.max_participants,
            entry_fee=t.entry_fee,
            status=t.status.value,
            total_matches=t.total_matches,
            points_to_win=t.points_to_win,
            registered_count=registered_count,
            court_name=first_court.name if first_court else None,
            created_at=t.created_at.isoformat() if t.created_at else "",
        ))
    return out


@router.post("", response_model=TournamentDetail)
async def create_tournament(
    data: TournamentCreate,
    background_tasks: BackgroundTasks,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    t = await _create_tournament(data, user, db, background_tasks)
    return await _tournament_detail(t, db)


@router.post("/batch")
async def create_tournaments_batch(
    data: list[TournamentCreate],
    background_tasks: BackgroundTasks,
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
        entry_fee=data.entry_fee,
        max_participants=data.max_participants,
        total_matches=data.total_matches,
        points_to_win=data.points_to_win,
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
    first = d.replace(day=1)
    week = ((d.day - 1 + first.weekday()) // 7) + 1
    prefix = f"{month_names[d.month - 1]}第{week}周"
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
    user: User | None = Depends(get_current_user),
):
    result = await db.execute(
        select(Tournament).where(Tournament.id == tournament_id)
    )
    t = result.scalar_one_or_none()
    if not t:
        raise HTTPException(status_code=404, detail="赛事不存在")
    return await _tournament_detail(t, db, user)


@router.delete("/{tournament_id}")
async def delete_tournament(
    tournament_id: int,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Tournament).where(Tournament.id == tournament_id))
    t = result.scalar_one_or_none()
    if not t:
        raise HTTPException(status_code=404, detail="赛事不存在")
    if t.creator_id != user.id:
        if user.role != "admin":
            raise HTTPException(status_code=403, detail="只有创建者可以删除")
    if t.status != TournamentStatus.OPEN:
        if user.role != "admin":
            raise HTTPException(status_code=400, detail="只能删除报名中的赛事")

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

    await db.delete(t)
    await db.flush()
    return {"ok": True}


@router.post("/{tournament_id}/register")
async def register(
    tournament_id: int,
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

    exist = await db.execute(
        select(Registration).where(
            Registration.tournament_id == tournament_id,
            Registration.user_id == user.id,
        )
    )
    reg = exist.scalar_one_or_none()
    if reg:
        if reg.is_active:
            raise HTTPException(status_code=400, detail="已报名")
        else:
            reg.is_active = True
            await db.flush()
            await manager.broadcast(tournament_id, {"type": "registration_updated"})
            return {"ok": True}

    cnt_result = await db.execute(
        select(func.count(Registration.id)).where(
            Registration.tournament_id == tournament_id, Registration.is_active == True
        )
    )
    cnt = cnt_result.scalar() or 0
    if cnt >= t.max_participants:
        raise HTTPException(status_code=400, detail="报名人数已满")

    db.add(Registration(tournament_id=tournament_id, user_id=user.id))
    await db.flush()
    await manager.broadcast(tournament_id, {"type": "registration_updated"})
    return {"ok": True}


@router.post("/{tournament_id}/cancel-register")
async def cancel_register(
    tournament_id: int,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    t_result = await db.execute(
        select(Tournament).where(Tournament.id == tournament_id).with_for_update()
    )
    t = t_result.scalar_one_or_none()
    if not t:
        raise HTTPException(status_code=404, detail="赛事不存在")
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
    await db.flush()
    await manager.broadcast(tournament_id, {"type": "registration_updated"})
    return {"ok": True}


@router.get("/{tournament_id}/registrations", response_model=list[RegistrationOut])
async def list_registrations(
    tournament_id: int,
    db: AsyncSession = Depends(get_db),
):
    from app.models.user import User as UserModel
    result = await db.execute(
        select(Registration, UserModel.username, UserModel.avatar)
        .join(UserModel, Registration.user_id == UserModel.id)
        .where(Registration.tournament_id == tournament_id, Registration.is_active == True)
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


async def _tournament_detail(t: Tournament, db: AsyncSession, user: User | None = None) -> TournamentDetail:
    cnt_result = await db.execute(
        select(func.count(Registration.id)).where(
            Registration.tournament_id == t.id, Registration.is_active == True
        )
    )
    registered_count = cnt_result.scalar() or 0

    courts_result = await db.execute(
        select(Court).where(Court.tournament_id == t.id).order_by(Court.sort_order)
    )
    courts = courts_result.scalars().all()
    court_outs = []
    for c in courts:
        ts_result = await db.execute(
            select(TimeSlot).where(TimeSlot.court_id == c.id).order_by(TimeSlot.start_time)
        )
        slots = ts_result.scalars().all()
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
        entry_fee=t.entry_fee,
        max_participants=t.max_participants,
        status=t.status.value,
            total_matches=t.total_matches,
            points_to_win=t.points_to_win,
        courts=court_outs,
        registered_count=registered_count,
        is_registered=is_registered,
        created_at=t.created_at.isoformat() if t.created_at else "",
    )


@router.get("/match-options/{num_players}")
async def match_options(num_players: int):
    """Return valid match counts for given number of players."""
    import math
    if num_players < 4:
        return {"options": [], "per_person": 0}
    step = num_players // math.gcd(4, num_players)
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
