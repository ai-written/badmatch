"""
引擎触发 API: 开始赛事、退赛重排
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from app.core.database import get_db
from app.core.security import require_user
from app.core.websocket import manager
from app.models.user import User
from app.models.tournament import Tournament, TournamentStatus, Registration, PlayerStats
from app.models.round import Round, RoundStatus, RoundPairing, Match, MatchStatus, Notification
from app.models.round import MatchSupport
from pydantic import BaseModel
from app.engine.scheduler import generate_schedule, compute_match_count, compute_rounds


class StartRequest(BaseModel):
    total_matches: int | None = None

router = APIRouter(prefix="/api/tournaments/{tournament_id}", tags=["engine"])


@router.post("/start")
async def start_tournament(
    tournament_id: int,
    body: StartRequest | None = None,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    # 行级锁：并发退赛时串行化读取-修改-写入，避免房主转让被后提交的事务覆盖丢失
    t = await db.execute(
        select(Tournament).where(Tournament.id == tournament_id).with_for_update()
    )
    tournament = t.scalar_one_or_none()
    if not tournament:
        raise HTTPException(status_code=404, detail="赛事不存在")
    if tournament.creator_id != user.id:
        raise HTTPException(status_code=403, detail="只有赛事创建者可以开始比赛")
    if tournament.status != TournamentStatus.OPEN:
        raise HTTPException(status_code=400, detail="赛事不是报名中状态")

    regs = await db.execute(
        select(Registration).where(
            Registration.tournament_id == tournament_id,
            Registration.is_active == True,
        )
    )
    registrations = regs.scalars().all()
    player_ids = [r.user_id for r in registrations]

    if len(player_ids) < 4:
        raise HTTPException(status_code=400, detail="至少需要 4 人参赛")

    # update total_matches if provided
    if body and body.total_matches is not None:
        tournament.total_matches = body.total_matches
    M = compute_match_count(len(player_ids))
    if tournament.total_matches:
        M = tournament.total_matches
    schedule = generate_schedule(player_ids, M)

    matches_per_round = 2
    rounds_data = compute_rounds(schedule, matches_per_round)

    for round_idx, round_matches in enumerate(rounds_data, start=1):
        r = Round(tournament_id=tournament_id, round_number=round_idx)
        db.add(r)
        await db.flush()

        for match_data in round_matches:
            (pa_id, pb_id), (pc_id, pd_id), court_id, slot_id = match_data

            pairing_a = RoundPairing(round_id=r.id, player_a_id=pa_id, player_b_id=pb_id)
            pairing_b = RoundPairing(round_id=r.id, player_a_id=pc_id, player_b_id=pd_id)
            db.add(pairing_a)
            db.add(pairing_b)
            await db.flush()

            m = Match(
                tournament_id=tournament_id,
                round_id=r.id,
                pairing_a_id=pairing_a.id,
                pairing_b_id=pairing_b.id,
                court_id=court_id,
                time_slot_id=slot_id,
            )
            db.add(m)

    for pid in player_ids:
        stat = PlayerStats(tournament_id=tournament_id, user_id=pid)
        db.add(stat)

    tournament.status = TournamentStatus.ONGOING
    await db.flush()
    return {"ok": True, "rounds": len(rounds_data), "matches": M}


class WithdrawBody(BaseModel):
    new_creator_id: int | None = None

@router.post("/withdraw/{player_id}")
async def withdraw_player(
    tournament_id: int,
    player_id: int,
    body: WithdrawBody = WithdrawBody(),
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    t = await db.execute(select(Tournament).where(Tournament.id == tournament_id))
    tournament = t.scalar_one_or_none()
    if not tournament:
        raise HTTPException(status_code=404)
    is_self = player_id == user.id
    if not is_self and tournament.creator_id != user.id:
        raise HTTPException(status_code=403, detail="无权操作")

    # self-withdraw before tournament starts: just cancel registration
    if is_self and tournament.status == TournamentStatus.OPEN:
        reg = await db.execute(
            select(Registration).where(
                Registration.tournament_id == tournament_id,
                Registration.user_id == user.id,
                Registration.is_active == True,
            )
        )
        r = reg.scalar_one_or_none()
        if not r:
            raise HTTPException(status_code=400, detail="未报名")
        r.is_active = False
        await db.flush()
        await manager.broadcast(tournament_id, {"type": "registration_updated"})
        return {"ok": True, "message": "已取消报名"}

    if tournament.status != TournamentStatus.ONGOING:
        raise HTTPException(status_code=400, detail="赛事未在进行中")

    stat = await db.execute(
        select(PlayerStats).where(
            PlayerStats.tournament_id == tournament_id,
            PlayerStats.user_id == player_id,
        )
    )
    ps = stat.scalar_one_or_none()
    if not ps:
        raise HTTPException(status_code=400, detail="选手不存在")

    # 退赛同时取消报名记录，保证报名列表/报名人数不再显示该选手。
    # 放在最前兜底：旧版本退赛只失效 PlayerStats、漏掉了 Registration，
    # 已退赛选手再次点击退出时也能清理残留的报名记录。
    reg = await db.execute(
        select(Registration).where(
            Registration.tournament_id == tournament_id,
            Registration.user_id == player_id,
            Registration.is_active == True,
        )
    )
    reg_record = reg.scalar_one_or_none()
    if reg_record:
        reg_record.is_active = False

    if not ps.is_active:
        # 已退赛（如旧版本残留状态）：若本人仍是房主，先把房主转给剩余活跃选手，
        # 避免赛事处于"无房主"状态
        if tournament.creator_id == player_id:
            remaining_players = await db.execute(
                select(PlayerStats.user_id).where(
                    PlayerStats.tournament_id == tournament_id,
                    PlayerStats.is_active == True,
                    PlayerStats.user_id != player_id,
                ).limit(1)
            )
            first = remaining_players.scalar_one_or_none()
            if first:
                tournament.creator_id = first
        await db.flush()
        await manager.broadcast(tournament_id, {"type": "registration_updated"})
        return {"ok": True, "message": "选手已退赛"}

    ps.is_active = False

    from app.models.user import User as UserModel
    # handle creator transfer
    if tournament.creator_id == player_id and body.new_creator_id:
        new_creator = await db.execute(select(UserModel).where(UserModel.id == body.new_creator_id))
        if new_creator.scalar_one_or_none():
            tournament.creator_id = body.new_creator_id
    elif tournament.creator_id == player_id:
        remaining_players = await db.execute(
            select(PlayerStats.user_id).where(
                PlayerStats.tournament_id == tournament_id,
                PlayerStats.is_active == True,
                PlayerStats.user_id != player_id,
            ).limit(1)
        )
        first = remaining_players.scalar_one_or_none()
        if first:
            tournament.creator_id = first

    completed_rounds = await db.execute(
        select(Round).where(
            Round.tournament_id == tournament_id,
            Round.status == RoundStatus.FINISHED,
        )
    )
    for r in completed_rounds.scalars().all():
        r.is_frozen = True

    unstarted_rounds = await db.execute(
        select(Round).where(
            Round.tournament_id == tournament_id,
            Round.status != RoundStatus.FINISHED,
        )
    )
    affected_referees = set()
    for r in unstarted_rounds.scalars().all():
        ms = await db.execute(select(Match).where(Match.round_id == r.id, Match.referee_id != None))
        for m in ms.scalars().all():
            affected_referees.add(m.referee_id)
        # delete match supports first
        match_ids = await db.execute(select(Match.id).where(Match.round_id == r.id))
        for (mid,) in match_ids.all():
            await db.execute(delete(MatchSupport).where(MatchSupport.match_id == mid))
        await db.execute(delete(Match).where(Match.round_id == r.id))
        await db.execute(delete(RoundPairing).where(RoundPairing.round_id == r.id))
        await db.execute(delete(Round).where(Round.id == r.id))

    partner_history = {}
    frozen_rounds = await db.execute(
        select(Round).where(Round.tournament_id == tournament_id, Round.is_frozen == True)
    )
    for r in frozen_rounds.scalars().all():
        pairings = await db.execute(select(RoundPairing).where(RoundPairing.round_id == r.id))
        for p in pairings.scalars().all():
            key = (p.player_a_id, p.player_b_id)
            partner_history[key] = partner_history.get(key, 0) + 1

    remaining = []
    stats = await db.execute(
        select(PlayerStats).where(
            PlayerStats.tournament_id == tournament_id,
            PlayerStats.is_active == True,
        )
    )
    for s in stats.scalars().all():
        remaining.append(s.user_id)

    remaining_count = len(remaining)
    if remaining_count < 4:
        # 剩余人数不足 4 人，无法继续 2v2 比赛，自动结束赛事
        tournament.status = TournamentStatus.FINISHED
        await db.flush()
        await manager.broadcast(tournament_id, {"type": "registration_updated"})
        return {"ok": True, "message": "剩余选手不足 4 人，赛事已自动结束"}

    if tournament.total_matches:
        new_M = compute_match_count(remaining_count)
        new_schedule = generate_schedule(remaining, new_M, partner_history)
        new_rounds_data = compute_rounds(new_schedule, 2)

        last_num_result = await db.execute(
            select(Round.round_number)
            .where(Round.tournament_id == tournament_id)
            .order_by(Round.round_number.desc())
            .limit(1)
        )
        last_num = last_num_result.scalar() or 0

        for round_idx, round_matches in enumerate(new_rounds_data, start=1):
            r = Round(
                tournament_id=tournament_id,
                round_number=last_num + round_idx,
                is_regenerated=True,
            )
            db.add(r)
            await db.flush()

            for match_data in round_matches:
                (pa_id, pb_id), (pc_id, pd_id), court_id, slot_id = match_data
                pairing_a = RoundPairing(round_id=r.id, player_a_id=pa_id, player_b_id=pb_id)
                pairing_b = RoundPairing(round_id=r.id, player_a_id=pc_id, player_b_id=pd_id)
                db.add(pairing_a)
                db.add(pairing_b)
                await db.flush()
                db.add(Match(
                    tournament_id=tournament_id,
                    round_id=r.id,
                    pairing_a_id=pairing_a.id,
                    pairing_b_id=pairing_b.id,
                    court_id=court_id,
                    time_slot_id=slot_id,
                ))

        for ref_id in affected_referees:
            db.add(Notification(
                user_id=ref_id,
                tournament_id=tournament_id,
                type="schedule_changed",
                message="赛事赛程已调整，您之前认领的裁判场次已取消，请重新认领。",
            ))

    await db.flush()
    await manager.broadcast(tournament_id, {"type": "registration_updated"})
    return {"ok": True}


@router.post("/end-tournament")
async def end_tournament(
    tournament_id: int,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    t = await db.execute(select(Tournament).where(Tournament.id == tournament_id))
    tournament = t.scalar_one_or_none()
    if not tournament:
        raise HTTPException(status_code=404)
    if tournament.creator_id != user.id:
        raise HTTPException(status_code=403)
    if tournament.status != TournamentStatus.ONGOING:
        raise HTTPException(status_code=400, detail="只能结束进行中的赛事")
    tournament.status = TournamentStatus.FINISHED
    await db.flush()
    return {"ok": True}
