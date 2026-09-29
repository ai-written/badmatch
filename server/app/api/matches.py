from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, Request
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from app.core.database import get_db
from app.core.security import require_user
from app.core.audit import audit, get_client_ip
from app.core.websocket import manager
from app.models.user import User
from app.models.tournament import Tournament, TournamentStatus, PlayerStats, Court, TimeSlot
from app.models.round import (
    MatchSupport,
    Round, RoundStatus, RoundPairing, Match, MatchStatus, Notification,
)
from app.schemas.match import (
    MatchOut, RoundOut, PlayerInfo, RoundPairingOut, ScoreUpdate, SupportUpdate,
    SupportResponse, SwapUpdate,
)

router = APIRouter(prefix="/api/tournaments/{tournament_id}", tags=["matches"])


async def load_writable_tournament(db: AsyncSession, tournament_id: int) -> Tournament:
    """载入赛事并确保它处于进行中。

    赛事被提前结束（end-tournament）时，往往还会有没打完的比赛。
    这些比赛必须变成只读：否则仍能认领裁判、记分、交换场地，
    在已结束的赛事上继续改动数据。所有会改数据的比赛级接口都应先过这里。
    """
    t = await db.execute(select(Tournament).where(Tournament.id == tournament_id))
    tournament = t.scalar_one_or_none()
    if not tournament:
        raise HTTPException(status_code=404, detail="赛事不存在")
    if tournament.status != TournamentStatus.ONGOING:
        raise HTTPException(status_code=400, detail="赛事已结束，无法再修改比赛")
    return tournament


# 读取接口统一要求登录：对阵/单场/应援票数都不再匿名可读
# （唯一例外的公开读取是积分榜，见 rankings.py）。写操作的边界不受影响。
@router.get("/rounds", response_model=list[RoundOut])
async def list_rounds(
    tournament_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_user),
):
    result = await db.execute(
        select(Round).where(Round.tournament_id == tournament_id).order_by(Round.round_number)
    )
    rounds = result.scalars().all()

    # 读取赛事状态：已结束时前端据此把整页置为只读（这里不抛错，只用于展示）
    t = await db.execute(select(Tournament).where(Tournament.id == tournament_id))
    tournament = t.scalar_one_or_none()
    # 赛事不存在时返回 404，而不是「200 + 空数组」：
    # 后者会让前端把「赛事已被删除/链接失效」显示成「该赛事还没有赛程」
    if tournament is None:
        raise HTTPException(status_code=404, detail="赛事不存在")
    t_status = tournament.status.value if tournament else None

    bye_map = await _get_bye_players(rounds, db)
    out = []
    for r in rounds:
        # 必须显式按 id 排序：PostgreSQL 无 ORDER BY 时按堆内物理顺序返回，
        # 而 UPDATE（如认领裁判、记分）会把行写成新版本并挪到堆尾，
        # 导致同一场比赛在刷新后位置漂移、整张对阵表顺序跳动。
        matches_result = await db.execute(
            select(Match).where(Match.round_id == r.id).order_by(Match.id)
        )
        matches = matches_result.scalars().all()
        match_outs = await _build_matches_out(matches, db, user, t_status)
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
    user: User = Depends(require_user),
):
    result = await db.execute(
        select(Match).where(Match.id == match_id, Match.tournament_id == tournament_id)
    )
    m = result.scalar_one_or_none()
    if not m:
        raise HTTPException(status_code=404, detail="比赛不存在")
    # 带上赛事状态，前端据此决定是否只读
    t = await db.execute(select(Tournament).where(Tournament.id == tournament_id))
    tournament = t.scalar_one_or_none()
    return await _build_match_out(m, db, user, tournament.status.value if tournament else None)


@router.put("/matches/{match_id}/score")
async def update_score(
    tournament_id: int,
    match_id: int,
    score: ScoreUpdate,
    request: Request,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Match).where(Match.id == match_id, Match.tournament_id == tournament_id).with_for_update()
    )
    m = result.scalar_one_or_none()
    if not m:
        raise HTTPException(status_code=404, detail="比赛不存在")
    # 赛事已结束 = 整场只读，谁都不能再改（超级管理员也不行）
    await load_writable_tournament(db, tournament_id)
    # 比赛已结束是另一回事：赛事进行中时，管理员/超级管理员仍可修正该场比分并重算胜负
    is_privileged = user.role in ("admin", "superadmin")
    # 只有在任裁判能记分：已卸任的裁判（referee_id 仍保留作历史）不再有权限
    # 超级管理员兜底：裁判临时不在、手机没电、或根本没人认领时，管理员/超管可以直接代记分
    # （记第一分就会把比赛从 pending 变 ongoing，也就是"开始比赛"）
    if not (user.role in ("admin", "superadmin") or (m.referee_id == user.id and m.has_active_referee)):
        raise HTTPException(status_code=403, detail="只有本场裁判、管理员或超级管理员可以记分")
    if m.status == MatchStatus.FINISHED and not is_privileged:
        raise HTTPException(status_code=400, detail="比赛已结束")
    if m.status == MatchStatus.FINISHED and not score.force_end:
        # 已结束的比赛只允许"连胜负一起重算"的修正，否则会出现"比分 11:3 但胜方是拿 3 分那队"
        raise HTTPException(status_code=400, detail="修正已结束的比赛必须同时提交胜负")

    # 修正已结束的比赛时，要用旧比分/旧胜负撤回上一次结算（见 _finalize_match）
    previous = (
        (m.score_a or 0, m.score_b or 0, m.winner_pairing_id)
        if m.status == MatchStatus.FINISHED else None
    )
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
        # 已结束的比赛：管理员/超管可以再结束一次，用来在修正比分后重算胜负
        # （previous 非空 → _finalize_match 只做增量调整，不重复记账）
        if m.status == MatchStatus.FINISHED and not is_privileged:
            raise HTTPException(status_code=400, detail="比赛已结束")
        sa, sb = score.score_a, score.score_b
        if sa == sb:
            raise HTTPException(status_code=400, detail="比分相同，无法结束")
        winner = m.pairing_a_id if sa > sb else m.pairing_b_id
        await _finalize_match(m, winner, db, previous=previous)
        await _maybe_finish_tournament(tournament_id, db)
        await audit(
            user=user, action="match_force_end",
            target_type="match", target_id=m.id,
            detail={"score_a": sa, "score_b": sb, "winner_pairing_id": winner},
            ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
        )
        # 先提交再广播（同 swap_sides）：广播是网络 IO，不应在持有行锁时进行；
        # 提交后订阅者立刻拉取也能读到最新比分与赛事状态。
        await db.commit()
        await _broadcast_match(m, tournament_id)
        return {"ok": True, "finished": True}

    # 高频操作：默认不记录，由 AUDIT_HIGH_FREQ_ENABLED 控制
    await audit(
        user=user, action="match_score_update",
        target_type="match", target_id=m.id,
        detail={"score_a": score.score_a, "score_b": score.score_b},
        ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
        high_freq=True,
    )
    # 同上：先提交再广播，缩短持锁时间（记分是最高频的写操作，影响最明显）
    await db.commit()
    await _broadcast_match(m, tournament_id)
    return {"ok": True}


@router.post("/matches/{match_id}/swap-sides")
async def swap_sides(
    tournament_id: int,
    match_id: int,
    request: Request,
    body: SwapUpdate = SwapUpdate(),
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """交换左右场地：仅本场裁判可操作，落库后广播给所有人，保证各端显示一致。

    此前这是纯前端行为，每个观众各自的视角只在自己浏览器里生效；
    改为服务端状态后，裁判交换一次，所有正在看这场的人一起切换。
    """
    result = await db.execute(
        select(Match)
        .where(Match.id == match_id, Match.tournament_id == tournament_id)
        .with_for_update()
    )
    m = result.scalar_one_or_none()
    if not m:
        raise HTTPException(status_code=404, detail="比赛不存在")
    # 赛事已结束则该场只读，不允许再交换场地
    await load_writable_tournament(db, tournament_id)
    # 同上：已卸任的裁判不能再交换场地
    # 同 update_score：管理员/超级管理员也可交换场地
    if not (user.role in ("admin", "superadmin") or (m.referee_id == user.id and m.has_active_referee)):
        raise HTTPException(status_code=403, detail="只有本场裁判、管理员或超级管理员可以交换场地")
    if m.status == MatchStatus.FINISHED:
        raise HTTPException(status_code=400, detail="比赛已结束")

    m.is_swapped = (not m.is_swapped) if body.swapped is None else body.swapped
    await audit(
        user=user, action="match_swap_sides",
        target_type="match", target_id=m.id,
        detail={"is_swapped": m.is_swapped},
        ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
    )
    # 先提交再广播：广播是可能阻塞的网络 IO，而此处仍持有该行的 FOR UPDATE 锁，
    # 拖长持锁时间会挡住同一场比赛的记分请求。提交后数据也已可见，
    # 订阅者收到广播再拉取时不会读到旧值。
    await db.commit()
    await manager.broadcast(tournament_id, {
        "type": "match_updated",
        "match_id": match_id,
        "is_swapped": m.is_swapped,
    })
    return {"ok": True, "is_swapped": m.is_swapped}


@router.post("/rounds/{round_id}/start-round")
async def start_round(
    tournament_id: int,
    round_id: int,
    request: Request,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """Start a round. Call after all matches are scheduled."""
    tournament = await load_writable_tournament(db, tournament_id)
    if tournament.creator_id != user.id and user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="只有赛事创建者可以开始轮次")
    r = await db.execute(select(Round).where(Round.id == round_id, Round.tournament_id == tournament_id))
    round_obj = r.scalar_one_or_none()
    if not round_obj:
        raise HTTPException(status_code=404, detail="轮次不存在")
    if round_obj.status != RoundStatus.PENDING:
        raise HTTPException(status_code=400, detail="该轮次已开始或已结束")
    round_obj.status = RoundStatus.ONGOING
    await audit(user=user, action="round_start", target_type="round", target_id=round_id,
                detail={"tournament_id": tournament_id}, ip=get_client_ip(request), user_agent=request.headers.get("user-agent"))
    # 先提交再广播，避免订阅者回查时读到旧状态
    await db.commit()
    await manager.broadcast(tournament_id, {"type": "round_started", "round_id": round_id})
    return {"ok": True}


# ---- Support / Cheer ----

@router.post("/matches/{match_id}/support")
async def support_match(
    tournament_id: int,
    match_id: int,
    body: SupportUpdate,
    request: Request,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    # validate match
    m = await db.execute(select(Match).where(Match.id == match_id, Match.tournament_id == tournament_id))
    match = m.scalar_one_or_none()
    if not match:
        raise HTTPException(status_code=404, detail="比赛不存在")
    # 赛事已结束则整场只读，连应援投票也一并停止
    await load_writable_tournament(db, tournament_id)
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
    # 只有「在任」裁判不能投票；已卸任者可以正常应援
    if match.referee_id == user.id and match.has_active_referee:
        raise HTTPException(status_code=400, detail="裁判不能投票")

    if body.side not in ("a", "b"):
        raise HTTPException(status_code=400, detail="无效的投票方")

    # upsert（处理并发首次投票时的唯一约束冲突）
    upserted_via_retry = False
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
        # 并发首次投票：另一个请求已插入同一行。整个事务必须回滚后重做，
        # 因为回滚会连带丢弃本事务里先前写入的一切。
        upserted_via_retry = True
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
    # 高频操作：默认不记录
    await audit(user=user, action="support_vote", target_type="match", target_id=match_id,
                detail={"side": body.side, "via_retry": upserted_via_retry},
                ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
                high_freq=True)
    # 先提交再广播，避免订阅者回查时票数还是旧的
    await db.commit()
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
    user: User = Depends(require_user),
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


async def _build_match_out(m: Match, db: AsyncSession, user: User | None = None,
                           tournament_status: str | None = None) -> MatchOut:
    return (await _build_matches_out([m], db, user, tournament_status))[0]


async def _build_matches_out(
    matches: list[Match],
    db: AsyncSession,
    user: User | None = None,
    tournament_status: str | None = None,
) -> list[MatchOut]:
    """批量组装比赛信息，避免 rounds 接口逐场 N+1 查询。

    tournament_status 会随每场比赛一起返回，前端据此把已结束赛事的对阵表
    和计分页整体置为只读。
    """
    if not matches:
        return []

    # 同一个响应里共用一个「服务端当前时间」，供前端换算计时基准
    now = datetime.now()
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
            # 必须按 match_id + side 分区：只按 match_id 分区时 rn<=20 取的是「该场最新
            # 20 票（两侧混排）」，票少的一侧头像会被整批丢掉（而 support_a/support_b
            # 票数照旧），出现「一边 20 个头像、另一边 0 个但显示有票」的矛盾。
            partition_by=[MatchSupport.match_id, MatchSupport.side],
            # id 兜底：同一秒内的多次投票 created_at 可能完全相同，
            # 只按时间排的话「第几个点赞」本身就不确定
            order_by=[MatchSupport.created_at.desc(), MatchSupport.id.desc()],
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
            # 必须按 rn 排：窗口函数算出了「第几个点赞」，但外层查询不排序的话
            # 返回顺序仍是任意的，头像顺序会在刷新之间跳变（rn 也就白算了）
            .order_by(av_subq.c.match_id, av_subq.c.side, av_subq.c.rn)
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

        # 执裁历史：referee_id 一旦写入即保留，卸任后仍展示「谁执裁过」
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
        # 当前在任裁判：卸任后为空，此时该场暂无裁判可记分
        active_referee = referee if m.has_active_referee else None

        can_referee = False
        # 可认领的条件：赛事进行中 + 比赛未结束 + 自己不是「当前在任裁判」。
        # 认领本身允许顶替在任裁判、也允许参赛选手（理由见 referee.py），所以这里
        # 不再限制「无人执裁过」或「非参赛者」；只把自己已经是裁判的情况排掉，
        # 免得按钮写着「申请成为裁判」而其实已经是自己的场次。
        # 这段必须与 POST /claim-referee 的实际校验等价，否则会出现「显示按钮但点了报错」。
        if (
            user
            and tournament_status == TournamentStatus.ONGOING.value
            and m.status in (MatchStatus.PENDING, MatchStatus.ONGOING)
            and not (m.referee_id == user.id and m.has_active_referee)
        ):
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
            active_referee=active_referee,
            referee_released_at=m.referee_released_at,
            status=m.status.value,
            can_referee=can_referee,
            support_a=support_a,
            support_b=support_b,
            my_support=my_supports.get(m.id),
            support_a_users=support_a_users,
            support_b_users=support_b_users,
            is_swapped=m.is_swapped,
            duration_seconds=_match_duration(m),
            started_at=m.started_at,
            ended_at=m.ended_at,
            now=now,
            tournament_status=tournament_status,
        ))
    return out


# 已结束比赛的耗时上限：超过视为异常（如忘记结束、跨天补录），不再显示耗时。
# 取 8 小时：双打打满多局也可能到两三小时，原来的 3 小时容易误伤真实比赛。
MAX_MATCH_DURATION_SECONDS = 8 * 60 * 60


def _match_duration(m: Match) -> int | None:
    """返回比赛耗时（秒）；时间缺失或异常（跨天/超上限）时为 None。"""
    if not m.started_at or not m.ended_at:
        return None
    secs = int((m.ended_at - m.started_at).total_seconds())
    if secs < 0 or secs > MAX_MATCH_DURATION_SECONDS:
        return None
    return secs


async def _finalize_match(m: Match, winner_pairing_id: int, db: AsyncSession,
                          previous: tuple[int, int, int | None] | None = None):
    """结算一场比赛。

    previous=(旧 score_a, 旧 score_b, 旧 winner_pairing_id)：**修正已结束的比赛**时传入。
    这时本场已经结算过一次，所以只按新旧差额调整 PlayerStats，绝不重复累加
    matches_played，也不覆盖 ended_at —— 否则「修正成绩」这个功能会把成绩单本身改坏
    （积分榜/个人战绩直接读 player_stats，重复累加后无法自愈）。
    """
    was_finished = previous is not None
    m.status = MatchStatus.FINISHED
    m.winner_pairing_id = winner_pairing_id
    # 只在首次结束时记录结束时间；修正不改写比赛耗时
    if m.ended_at is None:
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

    old_a, old_b, old_winner = previous if previous else (None, None, None)
    old_winner_ids: set[int] = set()
    if old_winner is not None:
        if old_winner == m.pairing_a_id:
            old_winner_ids = {pairing_a.player_a_id, pairing_a.player_b_id}
        else:
            old_winner_ids = {pairing_b.player_a_id, pairing_b.player_b_id}

    for uid in all_ids:
        stat_result = await db.execute(
            select(PlayerStats).where(
                PlayerStats.tournament_id == m.tournament_id,
                PlayerStats.user_id == uid,
            )
        )
        stat = stat_result.scalar_one_or_none()
        if stat:
            in_a = uid in (pairing_a.player_a_id, pairing_a.player_b_id)
            if not was_finished:
                stat.matches_played += 1
            elif old_a is not None and old_b is not None:
                # 撤回上一次的结算：与本轮相加后等价于"只按差值调整"
                stat.points_for -= old_a if in_a else old_b
                stat.points_against -= old_b if in_a else old_a
                if uid in old_winner_ids:
                    stat.matches_won -= 1
                else:
                    stat.matches_lost -= 1
            stat.points_for += m.score_a if in_a else m.score_b
            stat.points_against += m.score_b if in_a else m.score_a
            if uid in winner_ids:
                stat.matches_won += 1
            else:
                stat.matches_lost += 1


async def _broadcast_match(m: Match, tournament_id: int) -> None:
    await manager.broadcast(tournament_id, {
        "type": "match_updated",
        "match_id": m.id,
        "score_a": m.score_a,
        "score_b": m.score_b,
        # 胜方必须带上：观众端靠它渲染胜方高亮与 🏆。
        # 缺了它的话，比赛结束的广播里 status 已是 finished，而胜方永远是空的——
        # 且此后不会再有任何补拉（30 秒校准只在 ongoing 时执行），
        # 观众只能退出重进才能看到结果。
        "winner_pairing_id": m.winner_pairing_id,
        "status": m.status.value,
        # 必须带上真实的 started_at：客户端用 (now - started_at) 换算耗时。
        # 只给 now 的话，每次记分都被当成「刚刚开始」，计时器会归零重走。
        "started_at": m.started_at.isoformat() if m.started_at else None,
        "duration_seconds": _match_duration(m),
        "now": datetime.now().isoformat(),
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
    # 先提交再广播：订阅者收到 tournament_finished 后可能立刻回查赛事详情，
    # 若此时事务未提交，读到的是仍是 ongoing 的旧状态，这次实时更新就被吞掉了
    await db.commit()
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
            # 取最小 user_id 而不是 set.pop()：pop 返回的是「任意一个」，
            # 多人轮空时（例如 6 人赛每轮 2 人轮空）显示谁取决于 set 的内部布局，
            # 属于未定义行为，也会让同一份数据在不同环境/版本下显示不同的人。
            # 注意：多人轮空时这里**只展示其中一位**（取 id 最小者），
            # 因此结果虽稳定，但会固定偏向最早注册的账号 —— 要全部列出得改成列表字段。
            uid = min(bye_ids)
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
