from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
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

    out = []
    for r in rounds:
        matches_result = await db.execute(
            select(Match).where(Match.round_id == r.id)
        )
        matches = matches_result.scalars().all()
        match_outs = []
        for m in matches:
            match_outs.append(await _build_match_out(m, db, user))
        bye_player = await _get_bye_player(r, db)
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
        select(Match).where(Match.id == match_id, Match.tournament_id == tournament_id)
    )
    m = result.scalar_one_or_none()
    if not m:
        raise HTTPException(status_code=404, detail="比赛不存在")
    if m.referee_id != user.id:
        raise HTTPException(status_code=403, detail="只有本场裁判可以记分")
    if m.status == MatchStatus.FINISHED:
        raise HTTPException(status_code=400, detail="比赛已结束")

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
        await manager.broadcast(tournament_id, {"type": "match_updated", "match_id": match_id, "score_a": m.score_a, "score_b": m.score_b, "status": m.status.value})
        return {"ok": True, "finished": True}

    await db.flush()
    # broadcast update
    await manager.broadcast(tournament_id, {
        "type": "match_updated",
        "match_id": match_id,
        "score_a": m.score_a,
        "score_b": m.score_b,
        "status": m.status.value,
    })
    return {"ok": True}


@router.post("/rounds/{round_id}/start-round")
async def start_round(
    tournament_id: int,
    round_id: int,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """Start a round. Call after all matches are scheduled."""
    r = await db.execute(select(Round).where(Round.id == round_id, Round.tournament_id == tournament_id))
    round_obj = r.scalar_one_or_none()
    if not round_obj:
        raise HTTPException(status_code=404)
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

    # upsert
    exist = await db.execute(
        select(MatchSupport).where(MatchSupport.match_id == match_id, MatchSupport.user_id == user.id)
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
    # resolve pairings
    pa = await db.execute(
        select(RoundPairing).where(RoundPairing.id == m.pairing_a_id)
    )
    pb = await db.execute(
        select(RoundPairing).where(RoundPairing.id == m.pairing_b_id)
    )
    pairing_a = pa.scalar_one()
    pairing_b = pb.scalar_one()

    # resolve player info
    users = {}
    for uid in [pairing_a.player_a_id, pairing_a.player_b_id, pairing_b.player_a_id, pairing_b.player_b_id]:
        if uid not in users:
            u = await db.execute(select(User).where(User.id == uid))
            users[uid] = u.scalar_one()

    def make_pairing(pairing: RoundPairing):
        return RoundPairingOut(
            id=pairing.id,
            player_a=PlayerInfo(id=pairing.player_a_id, username=users[pairing.player_a_id].username, avatar=users[pairing.player_a_id].avatar or ""),
            player_b=PlayerInfo(id=pairing.player_b_id, username=users[pairing.player_b_id].username, avatar=users[pairing.player_b_id].avatar or ""),
        )

    # court and time info
    court_name = None
    start_time = None
    end_time = None
    if m.court_id:
        c = await db.execute(select(Court).where(Court.id == m.court_id))
        court = c.scalar_one_or_none()
        if court:
            court_name = court.name
            ts = await db.execute(select(TimeSlot).where(TimeSlot.id == m.time_slot_id))
            slot = ts.scalar_one_or_none()
            if slot:
                start_time = slot.start_time
                end_time = slot.end_time

    # referee
    referee = None
    if m.referee_id:
        ref = await db.execute(select(User).where(User.id == m.referee_id))
        ref_user = ref.scalar_one_or_none()
        if ref_user:
            referee = PlayerInfo(id=ref_user.id, username=ref_user.username, avatar=ref_user.avatar or "", gender=ref_user.gender)

    # can_referee: current user can be referee if not in the match
    can_referee = False
    if user and m.status in (MatchStatus.PENDING, MatchStatus.ONGOING) and m.referee_id is None:
        match_player_ids = {
            pairing_a.player_a_id, pairing_a.player_b_id,
            pairing_b.player_a_id, pairing_b.player_b_id,
        }
        if user.id not in match_player_ids:
            can_referee = True

    # support counts and voter avatars
    support_a, support_b = await _count_supports(m.id, db)
    support_a_users = []
    support_b_users = []
    supporters = await db.execute(
        select(MatchSupport, User.avatar).join(User, MatchSupport.user_id == User.id)
        .where(MatchSupport.match_id == m.id).order_by(MatchSupport.created_at.desc()).limit(20)
    )
    for s, av in supporters.all():
        if s.side == 'a':
            support_a_users.append(av or '')
        else:
            support_b_users.append(av or '')
    my_support = None
    if user:
        s = await db.execute(
            select(MatchSupport.side).where(MatchSupport.match_id == m.id, MatchSupport.user_id == user.id)
        )
        row = s.scalar_one_or_none()
        if row:
            my_support = row

    return MatchOut(
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
        my_support=my_support,
        support_a_users=support_a_users,
        support_b_users=support_b_users,
    )


async def _finalize_match(m: Match, winner_pairing_id: int, db: AsyncSession):
    m.status = MatchStatus.FINISHED
    m.winner_pairing_id = winner_pairing_id

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


async def _get_bye_player(r: Round, db: AsyncSession) -> PlayerInfo | None:
    """Find player not in any pairing this round — the bye."""
    pairings = await db.execute(
        select(RoundPairing).where(RoundPairing.round_id == r.id)
    )
    paired_ids = set()
    for p in pairings.scalars().all():
        paired_ids.add(p.player_a_id)
        paired_ids.add(p.player_b_id)

    # get all active players
    stats = await db.execute(
        select(PlayerStats).where(
            PlayerStats.tournament_id == r.tournament_id, PlayerStats.is_active == True
        )
    )
    all_player_ids = {s.user_id for s in stats.scalars().all()}

    bye_ids = all_player_ids - paired_ids
    if bye_ids:
        uid = bye_ids.pop()
        u = await db.execute(select(User).where(User.id == uid))
        user = u.scalar_one_or_none()
        if user:
            return PlayerInfo(id=user.id, username=user.username, avatar=user.avatar or "", gender=user.gender)
    return None
