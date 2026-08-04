from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from app.core.database import get_db
from app.core.security import require_user, get_current_user
from app.core.websocket import manager
from app.models.user import User
from app.models.tournament import Tournament, TournamentStatus, PlayerStats, Court, TimeSlot
from app.models.round import (
    MatchSupport,
    Round, RoundStatus, RoundPairing, Match, MatchStatus, Notification,
)
from app.schemas.match import MatchOut, RoundOut, PlayerInfo, RoundPairingOut, ScoreUpdate, SupportUpdate, SupportResponse

router = APIRouter(prefix="/api/tournaments/{tournament_id}", tags=["matches"])


@router.get("/rounds", response_model=list[RoundOut])
async def list_rounds(
    tournament_id: int,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    result = await db.execute(
        select(Round).where(Round.tournament_id == tournament_id).order_by(Round.round_number)
    )
    rounds = result.scalars().all()

    bye_map = await _get_bye_players(rounds, db)
    out = []
    for r in rounds:
        matches_result = await db.execute(
            select(Match).where(Match.round_id == r.id)
        )
        matches = matches_result.scalars().all()
        match_outs = await _build_matches_out(matches, db, user)
        for mo in match_outs:
            mo.round_number = r.round_number
        bye_player = bye_map.get(r.id)
        out.append(RoundOut(
            id=r.id,
            round_number=r.round_number,
            status=r.status.value,
            is_regenerated=r.is_regenerated,
            matches=match_outs,
            bye_player=bye_player,
        ))
    return out


@router.get("/matches/{match_id}", response_model=MatchOut)
async def get_match(
    tournament_id: int,
    match_id: int,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    result = await db.execute(
        select(Match).where(Match.id == match_id, Match.tournament_id == tournament_id)
    )
    m = result.scalar_one_or_none()
    if not m:
        raise HTTPException(status_code=404, detail="比赛不存在")
    return await _build_match_out(m, db, user)


@router.put("/matches/{match_id}/score")
async def update_score(
    tournament_id: int,
    match_id: int,
    score: ScoreUpdate,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Match).where(Match.id == match_id, Match.tournament_id == tournament_id).with_for_update()
    )
    m = result.scalar_one_or_none()
    if not m:
        raise HTTPException(status_code=404, detail="比赛不存在")
    if m.referee_id != user.id:
        raise HTTPException(status_code=403, detail="只有本场裁判可以记分")
    if m.status == MatchStatus.FINISHED:
        raise HTTPException(status_code=400, detail="比赛已结束")
    if score.score_a < 0 or score.score_b < 0:
        raise HTTPException(status_code=400, detail="比分不能为负数")

    # 首次记分视为比赛开始
    if m.status == MatchStatus.PENDING:
        m.status = MatchStatus.ONGOING
        m.started_at = datetime.now()

    m.score_a = score.score_a
    m.score_b = score.score_b

    # Only end via explicit force_end
    if score.force_end:
        if m.status == MatchStatus.FINISHED:
            raise HTTPException(status_code=400, detail="比赛已结束")
        sa, sb = score.score_a, score.score_b
        if sa == sb:
            raise HTTPException(status_code=400, detail="比分相同，无法结束")
        winner = m.pairing_a_id if sa > sb else m.pairing_b_id
        await _finalize_match(m, winner, db)
        await db.flush()
        await _broadcast_match(m, tournament_id)
        await _maybe_finish_tournament(tournament_id, db)
        return {"ok": True, "finished": True}

    await db.flush()
    await _broadcast_match(m, tournament_id)
    return {"ok": True}


@router.post("/rounds/{round_id}/start-round")
async def start_round(
    tournament_id: int,
    round_id: int,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """Start a round. Call after all matches are scheduled."""
    t = await db.execute(select(Tournament).where(Tournament.id == tournament_id))
    tournament = t.scalar_one_or_none()
    if not tournament:
        raise HTTPException(status_code=404, detail="赛事不存在")
    if tournament.creator_id != user.id and user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="只有赛事创建者可以开始轮次")
    if tournament.status != TournamentStatus.ONGOING:
        raise HTTPException(status_code=400, detail="赛事未在进行中")
    r = await db.execute(select(Round).where(Round.id == round_id, Round.tournament_id == tournament_id))
    round_obj = r.scalar_one_or_none()
    if not round_obj:
        raise HTTPException(status_code=404, detail="轮次不存在")
    if round_obj.status != RoundStatus.PENDING:
        raise HTTPException(status_code=400, detail="该轮次已开始或已结束")
    round_obj.status = RoundStatus.ONGOING
    await db.flush()
    await manager.broadcast(tournament_id, {"type": "round_started", "round_id": round_id})
    return {"ok": True}


# ---- Support / Cheer ----

@router.post("/matches/{match_id}/support")
async def support_match(
    tournament_id: int,
    match_id: int,
    body: SupportUpdate,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    # validate match
    m = await db.execute(select(Match).where(Match.id == match_id, Match.tournament_id == tournament_id))
    match = m.scalar_one_or_none()
    if not match:
        raise HTTPException(status_code=404, detail="比赛不存在")
    if match.status == MatchStatus.FINISHED:
        raise HTTPException(status_code=400, detail="比赛已结束")

    # check user not a player
    pa = await db.execute(select(RoundPairing).where(RoundPairing.id == match.pairing_a_id))
    pb = await db.execute(select(RoundPairing).where(RoundPairing.id == match.pairing_b_id))
    pairing_a = pa.scalar_one()
    pairing_b = pb.scalar_one()
    player_ids = {pairing_a.player_a_id, pairing_a.player_b_id, pairing_b.player_a_id, pairing_b.player_b_id}
    if user.id in player_ids:
        raise HTTPException(status_code=400, detail="参赛选手不能投票")
    if match.referee_id == user.id:
        raise HTTPException(status_code=400, detail="裁判不能投票")

    if body.side not in ("a", "b"):
        raise HTTPException(status_code=400, detail="无效的投票方")

    # upsert（处理并发首次投票时的唯一约束冲突）
    try:
        exist = await db.execute(
            select(MatchSupport).where(MatchSupport.match_id == match_id, MatchSupport.user_id == user.id)
        )
        support = exist.scalar_one_or_none()
        if support:
            support.side = body.side
        else:
            db.add(MatchSupport(match_id=match_id, user_id=user.id, side=body.side))
        await db.flush()
    except IntegrityError:
        await db.rollback()
        exist = await db.execute(
            select(MatchSupport).where(
                MatchSupport.match_id == match_id,
                MatchSupport.user_id == user.id,
            ).with_for_update()
        )
        support = exist.scalar_one_or_none()
        if support:
            support.side = body.side
        else:
            db.add(MatchSupport(match_id=match_id, user_id=user.id, side=body.side))
        await db.flush()

    # count
    counts = await _count_supports(match_id, db)
    await manager.broadcast(tournament_id, {
        "type": "support_updated",
        "match_id": match_id,
        "support_a": counts[0],
        "support_b": counts[1],
    })
    return {"support_a": counts[0], "support_b": counts[1], "my_side": body.side}


@router.get("/matches/{match_id}/support", response_model=SupportResponse)
async def get_support(
    tournament_id: int,
    match_id: int,
    user: User | None = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    counts = await _count_supports(match_id, db)
    my_side = None
    if user:
        s = await db.execute(
            select(MatchSupport.side).where(MatchSupport.match_id == match_id, MatchSupport.user_id == user.id)
        )
        row = s.scalar_one_or_none()
        if row:
            my_side = row
    return SupportResponse(support_a=counts[0], support_b=counts[1], my_side=my_side)


async def _count_supports(match_id: int, db: AsyncSession):
    a = await db.execute(
        select(func.count(MatchSupport.id)).where(MatchSupport.match_id == match_id, MatchSupport.side == "a")
    )
    b = await db.execute(
        select(func.count(MatchSupport.id)).where(MatchSupport.match_id == match_id, MatchSupport.side == "b")
    )
    return (a.scalar() or 0, b.scalar() or 0)


async def _build_match_out(m: Match, db: AsyncSession, user: User | None = None) -> MatchOut:
    return (await _build_matches_out([m], db, user))[0]


async def _build_matches_out(matches: list[Match], db: AsyncSession, user: User | None = None) -> list[MatchOut]:
    """批量组装比赛信息，避免 rounds 接口逐场 N+1 查询。"""
    if not matches:
        return []

    match_ids = [m.id for m in matches]
    pairing_ids = list({m.pairing_a_id for m in matches} | {m.pairing_b_id for m in matches})

    pairings_map: dict[int, RoundPairing] = {}
    if pairing_ids:
        pairings = await db.execute(select(RoundPairing).where(RoundPairing.id.in_(pairing_ids)))
        pairings_map = {p.id: p for p in pairings.scalars().all()}

    user_ids = {m.referee_id for m in matches if m.referee_id}
    for p in pairings_map.values():
        user_ids.add(p.player_a_id)
        user_ids.add(p.player_b_id)

    users_map: dict[int, User] = {}
    if user_ids:
        users = await db.execute(select(User).where(User.id.in_(user_ids)))
        users_map = {u.id: u for u in users.scalars().all()}

    courts_map: dict[int, Court] = {}
    court_ids = {m.court_id for m in matches if m.court_id}
    if court_ids:
        courts = await db.execute(select(Court).where(Court.id.in_(court_ids)))
        courts_map = {c.id: c for c in courts.scalars().all()}

    slots_map: dict[int, TimeSlot] = {}
    slot_ids = {m.time_slot_id for m in matches if m.time_slot_id}
    if slot_ids:
        slots = await db.execute(select(TimeSlot).where(TimeSlot.id.in_(slot_ids)))
        slots_map = {s.id: s for s in slots.scalars().all()}

    support_counts: dict[int, dict[str, int]] = {}
    support_avatars: dict[int, dict[str, list[str]]] = {}
    if match_ids:
        count_rows = await db.execute(
            select(MatchSupport.match_id, MatchSupport.side, func.count(MatchSupport.id))
            .where(MatchSupport.match_id.in_(match_ids))
            .group_by(MatchSupport.match_id, MatchSupport.side)
        )
        for mid, side, cnt in count_rows.all():
            support_counts.setdefault(mid, {})[side] = cnt

        rn = func.row_number().over(
            partition_by=MatchSupport.match_id,
            order_by=MatchSupport.created_at.desc(),
        ).label("rn")
        av_subq = (
            select(MatchSupport.match_id, MatchSupport.side, User.avatar, rn)
            .join(User, MatchSupport.user_id == User.id)
            .where(MatchSupport.match_id.in_(match_ids))
            .subquery()
        )
        av_rows = await db.execute(
            select(av_subq.c.match_id, av_subq.c.side, av_subq.c.avatar)
            .where(av_subq.c.rn <= 20)
        )
        for mid, side, av in av_rows.all():
            bucket = support_avatars.setdefault(mid, {"a": [], "b": []})
            if len(bucket[side]) < 20:
                bucket[side].append(av or "")

    my_supports: dict[int, str] = {}
    if user and match_ids:
        rows = await db.execute(
            select(MatchSupport.match_id, MatchSupport.side).where(
                MatchSupport.match_id.in_(match_ids),
                MatchSupport.user_id == user.id,
            )
        )
        my_supports = {mid: side for mid, side in rows.all()}

    out = []
    for m in matches:
        pairing_a = pairings_map.get(m.pairing_a_id)
        pairing_b = pairings_map.get(m.pairing_b_id)
        if not pairing_a or not pairing_b:
            raise HTTPException(status_code=500, detail="比赛数据不完整")

        def make_pairing(pairing: RoundPairing):
            ua = users_map.get(pairing.player_a_id)
            ub = users_map.get(pairing.player_b_id)
            return RoundPairingOut(
                id=pairing.id,
                player_a=PlayerInfo(
                    id=pairing.player_a_id,
                    username=ua.username if ua else "",
                    avatar=(ua.avatar or "") if ua else "",
                ),
                player_b=PlayerInfo(
                    id=pairing.player_b_id,
                    username=ub.username if ub else "",
                    avatar=(ub.avatar or "") if ub else "",
                ),
            )

        court_name = None
        start_time = None
        end_time = None
        if m.court_id:
            court = courts_map.get(m.court_id)
            if court:
                court_name = court.name
                slot = slots_map.get(m.time_slot_id)
                if slot:
                    start_time = slot.start_time
                    end_time = slot.end_time

        referee = None
        if m.referee_id:
            ref_user = users_map.get(m.referee_id)
            if ref_user:
                referee = PlayerInfo(
                    id=ref_user.id,
                    username=ref_user.username,
                    avatar=ref_user.avatar or "",
                    gender=ref_user.gender,
                )

        can_referee = False
        if user and m.status in (MatchStatus.PENDING, MatchStatus.ONGOING) and m.referee_id is None:
            match_player_ids = {
                pairing_a.player_a_id, pairing_a.player_b_id,
                pairing_b.player_a_id, pairing_b.player_b_id,
            }
            if user.id not in match_player_ids:
                can_referee = True

        support_a = support_counts.get(m.id, {}).get("a", 0)
        support_b = support_counts.get(m.id, {}).get("b", 0)
        support_a_users = support_avatars.get(m.id, {}).get("a", [])
        support_b_users = support_avatars.get(m.id, {}).get("b", [])

        out.append(MatchOut(
            id=m.id,
            round_id=m.round_id,
            round_number=0,  # filled by caller
            pairing_a=make_pairing(pairing_a),
            pairing_b=make_pairing(pairing_b),
            court_name=court_name,
            start_time=start_time,
            end_time=end_time,
            score_a=m.score_a,
            score_b=m.score_b,
            winner_pairing_id=m.winner_pairing_id,
            referee=referee,
            status=m.status.value,
            can_referee=can_referee,
            support_a=support_a,
            support_b=support_b,
            my_support=my_supports.get(m.id),
            support_a_users=support_a_users,
            support_b_users=support_b_users,
            duration_seconds=_match_duration(m),
        ))
    return out


# 超过该秒数视为异常（如忘记结束、跨天补录），不再显示耗时
MAX_MATCH_DURATION_SECONDS = 3 * 60 * 60


def _match_duration(m: Match) -> int | None:
    """返回比赛耗时（秒）；时间缺失或异常（跨天/超上限）时为 None。"""
    if not m.started_at or not m.ended_at:
        return None
    secs = int((m.ended_at - m.started_at).total_seconds())
    if secs < 0 or secs > MAX_MATCH_DURATION_SECONDS:
        return None
    return secs


async def _finalize_match(m: Match, winner_pairing_id: int, db: AsyncSession):
    m.status = MatchStatus.FINISHED
    m.winner_pairing_id = winner_pairing_id
    m.ended_at = datetime.now()

    # update PlayerStats for 4 participants
    pa = await db.execute(select(RoundPairing).where(RoundPairing.id == m.pairing_a_id))
    pb = await db.execute(select(RoundPairing).where(RoundPairing.id == m.pairing_b_id))
    pairing_a = pa.scalar_one()
    pairing_b = pb.scalar_one()

    winner_ids = set()
    if winner_pairing_id == m.pairing_a_id:
        winner_ids = {pairing_a.player_a_id, pairing_a.player_b_id}
    else:
        winner_ids = {pairing_b.player_a_id, pairing_b.player_b_id}

    all_ids = [pairing_a.player_a_id, pairing_a.player_b_id, pairing_b.player_a_id, pairing_b.player_b_id]

    for uid in all_ids:
        stat_result = await db.execute(
            select(PlayerStats).where(
                PlayerStats.tournament_id == m.tournament_id,
                PlayerStats.user_id == uid,
            )
        )
        stat = stat_result.scalar_one_or_none()
        if stat:
            stat.matches_played += 1
            if uid in winner_ids:
                stat.matches_won += 1
            else:
                stat.matches_lost += 1
            stat.points_for += m.score_a if uid in {pairing_a.player_a_id, pairing_a.player_b_id} else m.score_b
            stat.points_against += m.score_b if uid in {pairing_a.player_a_id, pairing_a.player_b_id} else m.score_a


async def _broadcast_match(m: Match, tournament_id: int) -> None:
    await manager.broadcast(tournament_id, {
        "type": "match_updated",
        "match_id": m.id,
        "score_a": m.score_a,
        "score_b": m.score_b,
        "status": m.status.value,
    })


async def _maybe_finish_tournament(tournament_id: int, db: AsyncSession) -> None:
    """比赛全部结束后自动将赛事置为 finished 并广播。"""
    remaining = await db.execute(
        select(func.count(Match.id)).where(
            Match.tournament_id == tournament_id,
            Match.status != MatchStatus.FINISHED,
        )
    )
    if (remaining.scalar() or 0) > 0:
        return
    t = await db.execute(select(Tournament).where(Tournament.id == tournament_id))
    tournament = t.scalar_one_or_none()
    if not tournament or tournament.status == TournamentStatus.FINISHED:
        return
    tournament.status = TournamentStatus.FINISHED
    await db.flush()
    await manager.broadcast(tournament_id, {"type": "tournament_finished"})


async def _get_bye_players(rounds: list[Round], db: AsyncSession) -> dict[int, PlayerInfo | None]:
    """批量计算每轮的轮空选手，避免逐轮查询。"""
    if not rounds:
        return {}

    round_ids = [r.id for r in rounds]
    paired_by_round: dict[int, set[int]] = {rid: set() for rid in round_ids}
    pairings = await db.execute(
        select(RoundPairing).where(RoundPairing.round_id.in_(round_ids))
    )
    for p in pairings.scalars().all():
        paired_by_round.setdefault(p.round_id, set()).add(p.player_a_id)
        paired_by_round.setdefault(p.round_id, set()).add(p.player_b_id)

    stats = await db.execute(
        select(PlayerStats).where(
            PlayerStats.tournament_id == rounds[0].tournament_id,
            PlayerStats.is_active == True,
        )
    )
    all_player_ids = {s.user_id for s in stats.scalars().all()}

    users_map: dict[int, User] = {}
    if all_player_ids:
        rows = await db.execute(select(User).where(User.id.in_(all_player_ids)))
        users_map = {u.id: u for u in rows.scalars().all()}

    result: dict[int, PlayerInfo | None] = {}
    for r in rounds:
        bye_ids = all_player_ids - paired_by_round.get(r.id, set())
        bye_player = None
        if bye_ids:
            uid = bye_ids.pop()
            u = users_map.get(uid)
            if u:
                bye_player = PlayerInfo(
                    id=u.id,
                    username=u.username,
                    avatar=u.avatar or "",
                    gender=u.gender,
                )
        result[r.id] = bye_player
    return result
