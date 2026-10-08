"""
引擎触发 API: 开始赛事、退赛重排
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, func
from app.core.database import get_db
from app.core.security import require_user
from app.core.audit import audit, get_client_ip
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
    request: Request,
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
    if tournament.creator_id != user.id and user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="只有赛事创建者、管理员或超级管理员可以开始比赛")
    if tournament.status != TournamentStatus.OPEN:
        raise HTTPException(status_code=400, detail="赛事不是报名中状态")

    regs = await db.execute(
        select(Registration).where(
            Registration.tournament_id == tournament_id,
            Registration.is_active == True,
        )
        # 固定顺序：player_ids 会直接喂给排程算法，顺序不同排出来的对阵也不同。
        # 不写 ORDER BY 时顺序由执行计划决定，等于每次开赛的输入都不一样。
        .order_by(Registration.created_at, Registration.id)
    )
    registrations = regs.scalars().all()
    player_ids = [r.user_id for r in registrations]

    if len(player_ids) < 4:
        raise HTTPException(status_code=400, detail="至少需要 4 人参赛")

    # 总场次：显式传入的值先校验；库里的存量值也必须复检。
    # 库里可能是「创建时按旧人数钉下的场次」，报名人数变化后就不整除了，
    # 直接拿去排程会抛异常（未捕获 → 500 → 赛事永远开不起来），
    # 这里与 withdraw 的重排逻辑保持一致：不整除就按当前人数重算。
    if body and body.total_matches is not None:
        requested = body.total_matches
        if requested < 1 or requested > 100 or (4 * requested) % len(player_ids) != 0:
            raise HTTPException(status_code=400, detail="总场次需在 1-100 之间且保证每名选手场次相同")
        tournament.total_matches = requested
        M = requested
    elif tournament.total_matches and (4 * tournament.total_matches) % len(player_ids) == 0:
        M = tournament.total_matches
    else:
        # 未设置，或存量值与当前人数不匹配：按当前人数取最小可行场次
        M = compute_match_count(len(player_ids))

    # 排程理论上对「4M 能被 N 整除」的组合都可行（scheduler 内部已用随机重试
    # 消除贪婪选人的死路），但仍保留一层兜底：万一某个组合排不出来，
    # 退到可行的最小场次，保证赛事总能开起来，而不是 500 回滚后卡在 open。
    # 顺序很重要：必须先试请求值，否则会把用户明确选择的场次无故改小。
    schedule = None
    adjusted_M = M
    try:
        schedule = generate_schedule(player_ids, M)
    except ValueError:
        candidate = compute_match_count(len(player_ids))
        while candidate <= max(M, 30):
            if candidate == M:
                candidate += len(player_ids)
                continue
            try:
                schedule = generate_schedule(player_ids, candidate)
                adjusted_M = candidate
                break
            except ValueError:
                candidate += len(player_ids)   # 只试整除的组合
    if schedule is None:
        raise HTTPException(
            status_code=400,
            detail="当前人数无法排出赛程，请调整报名人数后重试",
        )
    if adjusted_M != M:
        # 写回实际使用的场次，避免详情与后续重排继续用那个排不出来的值
        tournament.total_matches = adjusted_M
        M = adjusted_M

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
    # 先提交再广播：订阅者收到 tournament_started 后会立刻拉赛程，
    # 未提交的话可能读到还没生成的轮次（表现为「已开始但赛程是空的」）；
    # 审计随后（见 auth.update_profile 的说明：避免业务回滚留下假日志，
    # 也避免 audit 遇到请求取消时把这次广播连带跳过）
    await db.commit()
    await manager.broadcast(tournament_id, {"type": "tournament_started"})
    await audit(
        user=user, action="tournament_start",
        target_type="tournament", target_id=tournament.id,
        detail={"players": len(player_ids), "matches": M, "rounds": len(rounds_data)},
        ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
    )
    return {"ok": True, "rounds": len(rounds_data), "matches": M, "total_matches": M}


class WithdrawBody(BaseModel):
    new_creator_id: int | None = None

@router.post("/withdraw/{player_id}")
async def withdraw_player(
    tournament_id: int,
    player_id: int,
    request: Request,
    body: WithdrawBody = WithdrawBody(),
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    t = await db.execute(
        select(Tournament).where(Tournament.id == tournament_id).with_for_update()
    )
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
        # 与 cancel-register 同一口径：取消/退赛都要记时间，否则「取消报名记录」里
        # 这条只能显示「时间未知」，和「老数据缺列」的兜底语义混淆
        r.cancelled_at = func.now()
        # 先提交再广播再审计（见 auth.update_profile 的说明）
        await db.commit()
        await manager.broadcast(tournament_id, {"type": "registration_updated"})
        await audit(user=user, action="tournament_withdraw", target_type="tournament", target_id=tournament.id,
                    detail={"player_id": player_id, "self": True, "phase": "open"},
                    ip=get_client_ip(request), user_agent=request.headers.get("user-agent"))
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
        # 同上：开赛后退出也要记时间（取消报名记录接口按「有没有失效的 PlayerStats」
        # 区分它是「报名阶段取消」还是「赛中退赛」）
        reg_record.cancelled_at = func.now()

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
        # 先提交再广播再审计（与本函数其它分支、以及全仓其它接口统一）：
        # 这条分支也在改状态（房主转移 + 报名记录失效），原先一条审计都不写 ——
        # 事后查「谁把房主转走了」只能看到一片空白。
        # 审计必须在广播之后：audit 遇到请求取消会向上抛，写在广播前会让这次广播被跳过。
        await db.commit()
        await manager.broadcast(tournament_id, {"type": "registration_updated"})
        await audit(user=user, action="tournament_withdraw", target_type="tournament", target_id=tournament.id,
                    detail={"player_id": player_id, "self": is_self, "phase": "already_withdrawn",
                            "creator_id": tournament.creator_id},
                    ip=get_client_ip(request), user_agent=request.headers.get("user-agent"))
        return {"ok": True, "message": "选手已退赛"}

    ps.is_active = False

    # handle creator transfer
    if tournament.creator_id == player_id and body.new_creator_id:
        # 新房主必须是本赛事「当前在场」的选手，且不能是自己。
        # 原先只校验「该用户存在」，于是可以传任意用户 id（已退赛的、
        # 从没报名的、甚至自己），把房主转给一个不在场的人 ——
        # 前端虽然限制了候选范围，但那是客户端校验，接口必须自己兜住。
        if body.new_creator_id == player_id:
            raise HTTPException(status_code=400, detail="不能把房主转让给自己")
        ok_new = await db.execute(
            select(PlayerStats).where(
                PlayerStats.tournament_id == tournament_id,
                PlayerStats.user_id == body.new_creator_id,
                PlayerStats.is_active == True,
            )
        )
        if ok_new.scalar_one_or_none() is None:
            raise HTTPException(status_code=400, detail="新房主必须是本赛事在场选手")
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
            Round.status == RoundStatus.PENDING,
        )
    )
    affected_referees = set()
    for r in unstarted_rounds.scalars().all():
        ms = await db.execute(select(Match).where(Match.round_id == r.id, Match.referee_id != None))
        for m in ms.scalars().all():
            # 只通知在任裁判：已卸任者（referee_id 保留作历史）不需要重新认领
            if m.has_active_referee:
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
        # 与 start_tournament 同一口径：remaining 会直接喂给排程算法，
        # 顺序不同重排出来的对阵也不同。PlayerStats 没有 created_at，
        # 用 id 即等于「开赛时的报名顺序」（那时就是按报名时间建的这些行）。
        .order_by(PlayerStats.id)
    )
    for s in stats.scalars().all():
        remaining.append(s.user_id)

    remaining_count = len(remaining)
    if remaining_count < 4:
        # 剩余人数不足 4 人，无法继续 2v2 比赛，自动结束赛事
        tournament.status = TournamentStatus.FINISHED
        # 先提交再广播：订阅者收到 tournament_finished 后会回查赛事状态，
        # 未提交的话读到的还是「进行中」
        await db.commit()
        await manager.broadcast(tournament_id, {"type": "tournament_finished"})
        # 审计放在提交**和广播之后**：写在提交前，业务事务一旦回滚就会留下
        # 「赛事已结束」的假日志；写在广播前，手机端提交后立刻切页导致的请求取消
        # 会让广播被直接跳过（audit 对取消是向上抛的，见 core/audit.py）。
        # 两条都记：一条「结束赛事」，一条「谁退的赛」。
        await audit(user=user, action="tournament_end", target_type="tournament", target_id=tournament.id,
                    detail={"auto": True, "reason": "players_below_4"},
                    ip=get_client_ip(request), user_agent=request.headers.get("user-agent"))
        await audit(user=user, action="tournament_withdraw", target_type="tournament", target_id=tournament.id,
                    detail={"player_id": player_id, "remaining": remaining_count, "auto_finished": True},
                    ip=get_client_ip(request), user_agent=request.headers.get("user-agent"))
        return {"ok": True, "message": "剩余选手不足 4 人，赛事已自动结束"}

    if tournament.total_matches and (4 * tournament.total_matches) % remaining_count == 0:
        new_M = tournament.total_matches
    else:
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

    # 先提交再广播再审计：这里重排了赛程、通知了原裁判，订阅者收到后若立刻拉赛程，
    # 未提交的话会读到重排前的旧轮次；审计随后（见 auth.update_profile 的说明）
    await db.commit()
    await manager.broadcast(tournament_id, {"type": "registration_updated"})
    await audit(user=user, action="tournament_withdraw", target_type="tournament", target_id=tournament.id,
                detail={"player_id": player_id, "remaining": remaining_count, "new_creator_id": tournament.creator_id},
                ip=get_client_ip(request), user_agent=request.headers.get("user-agent"))
    return {"ok": True}


@router.post("/end-tournament")
async def end_tournament(
    tournament_id: int,
    request: Request,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    # 行锁：与「比赛全部打完自动收尾」串行化。否则两条路径可能都读到 ongoing 各自收尾，
    # 结果是两条 tournament_end 审计（一条 title、一条 auto）+ 两次广播。
    # 同时也与正在记分的请求（它们持赛事的 FOR SHARE 锁）互斥，保证不会改到一半就被结束。
    t = await db.execute(
        select(Tournament).where(Tournament.id == tournament_id).with_for_update()
    )
    tournament = t.scalar_one_or_none()
    if not tournament:
        raise HTTPException(status_code=404)
    # 提前结束赛事：创建者、管理员或超级管理员（兜底：房主临时不在也能收尾）
    if tournament.creator_id != user.id and user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="只有赛事创建者、管理员或超级管理员可以结束赛事")
    if tournament.status != TournamentStatus.ONGOING:
        raise HTTPException(status_code=400, detail="只能结束进行中的赛事")
    tournament.status = TournamentStatus.FINISHED
    # 先提交再广播，避免订阅者回查时赛事状态还是「进行中」
    await db.commit()
    await manager.broadcast(tournament_id, {"type": "tournament_finished"})
    # 审计放最后（提交 + 广播之后）：写在提交前，提交失败会留下「赛事已结束」的假日志；
    # 写在广播前，手机端提交后立刻切页导致的请求取消会让广播被跳过。
    await audit(user=user, action="tournament_end", target_type="tournament", target_id=tournament.id,
                detail={"title": tournament.title}, ip=get_client_ip(request), user_agent=request.headers.get("user-agent"))
    return {"ok": True}
