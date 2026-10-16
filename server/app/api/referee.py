from fastapi import APIRouter, Depends, HTTPException, Request
from datetime import datetime
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.core.database import get_db
from app.core.security import require_user
from app.core.audit import audit, get_client_ip
from app.api.matches import load_writable_tournament
from app.api.engine_api import _active_player_ids
from app.engine.referee_assign import assign_referees, can_assign
from app.models.user import User
from app.models.round import Match, MatchStatus, Notification, Round, RoundPairing

router = APIRouter(prefix="/api/tournaments/{tournament_id}", tags=["referee"])


@router.post("/matches/{match_id}/claim-referee")
async def claim_referee(
    tournament_id: int,
    match_id: int,
    request: Request,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    # 顺序：先取赛事的共享锁，再锁比赛行（见 load_writable_tournament 的说明）。
    # 反过来的话，与"退赛先锁赛事再删比赛"会形成环路而死锁。
    await load_writable_tournament(db, tournament_id)
    # 行锁：并发认领同一场比赛时串行化，否则两人都会读到「无裁判」，
    # 后提交者覆盖前者——前者收到「认领成功」却立刻失去权限。
    result = await db.execute(
        select(Match)
        .where(Match.id == match_id, Match.tournament_id == tournament_id)
        .with_for_update()
    )
    m = result.scalar_one_or_none()
    if not m:
        raise HTTPException(status_code=404, detail="比赛不存在")
    if m.status == MatchStatus.FINISHED:
        raise HTTPException(status_code=400, detail="比赛已结束")
    # 认领规则（按真实使用场景放宽，前端会先弹确认框再发请求）：
    # - 已有在任裁判时也允许认领，等于「顶替」：裁判临时有事要转让、或原裁判手机
    #   没电了让别人代为执裁，都会发生；被顶替的人会收到站内消息，不至于白跑一场。
    # - 参赛选手也允许认领：同样是为了「借别人的手机代认领」这种场景 ——
    #   接口层无法区分手机后面坐着的是谁，加限制只会挡住正常使用。
    # - 原裁判曾卸任（referee_released_at 非空）的场次同样任何人都能接手；
    #   原来「他人不可覆盖历史记录」会导致这种场次只有原裁判能收回。
    replaced_referee_id = None
    if m.referee_id is not None and m.referee_id != user.id and m.has_active_referee:
        replaced_referee_id = m.referee_id

    m.referee_id = user.id
    # 接手 / 收回都清空卸任时间，恢复在任状态
    m.referee_released_at = None
    if replaced_referee_id:
        # 被顶替的人要能知道：否则他会按约定去执裁，到场才发现已不是自己的场次
        db.add(Notification(
            user_id=replaced_referee_id,
            tournament_id=tournament_id,
            type="referee_replaced",
            message="您认领的裁判场次已由他人接手，如仍需执裁请重新认领。",
        ))

    # 先提交再广播再审计：订阅者收到 referee_claimed 后回查比赛详情时，
    # 未提交的话看到的仍是无裁判状态，这一次实时更新就被吞掉；
    # 审计随后（见 auth.update_profile 的说明：避免假日志、也避免取消时跳过广播）
    await db.commit()
    from app.core.websocket import manager
    await manager.broadcast(tournament_id, {
        "type": "referee_claimed",
        "match_id": match_id,
        "referee_id": user.id,
    })
    await audit(user=user, action="referee_claim", target_type="match", target_id=match_id,
                detail={"tournament_id": tournament_id, "replaced_referee_id": replaced_referee_id},
                ip=get_client_ip(request), user_agent=request.headers.get("user-agent"))
    return {"ok": True}


@router.post("/matches/{match_id}/release-referee")
async def release_referee(
    tournament_id: int,
    match_id: int,
    request: Request,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    # 行锁：与 claim_referee 用同一把锁串行化。否则「A 点卸任」与「B 顶替」并发时，
    # B 先提交成为新裁判、A 的事务随后仍会把 referee_released_at 写上，
    # 最终变成「referee_id=B 但已卸任」——B 收到认领成功却没有记分权限。
    result = await db.execute(
        select(Match)
        .where(Match.id == match_id, Match.tournament_id == tournament_id)
        .with_for_update()
    )
    m = result.scalar_one_or_none()
    if not m:
        raise HTTPException(status_code=404, detail="比赛不存在")
    if m.referee_id != user.id:
        raise HTTPException(status_code=403, detail="您不是本场比赛的裁判")
    if not m.has_active_referee:
        raise HTTPException(status_code=400, detail="您已卸任本场比赛裁判")
    # 比赛打完后不允许卸任：referee_id 要保留为「谁执裁过」的历史记录。
    # 除此外不受赛事状态限制——赛事提前结束后，认领了却没能执裁的人仍可退出。
    if m.status == MatchStatus.FINISHED:
        raise HTTPException(status_code=400, detail="比赛已结束，裁判记录已归档")

    # 只标记卸任时间，保留 referee_id 作为执裁历史；此后不再拥有记分等权限
    m.referee_released_at = datetime.now()
    # 先提交再广播再审计（同上）
    await db.commit()
    from app.core.websocket import manager
    await manager.broadcast(tournament_id, {
        "type": "referee_released",
        "match_id": match_id,
    })
    await audit(user=user, action="referee_release", target_type="match", target_id=match_id,
                detail={"tournament_id": tournament_id}, ip=get_client_ip(request), user_agent=request.headers.get("user-agent"))
    return {"ok": True}


class AutoAssignBody(BaseModel):
    """scope:
    - current：只分「当前要打的那一批」——对阵表筛选行那个按钮，任何在场选手可点；
    - all：把**全部**还没打完、还没裁判的场次一次分完——赛事详情页「一次性分配裁判」，
      房主/管理员/超管可点（他们可能不是参赛选手）。

    注意：不带 body / `null` / `{}` 都会当成 current（后端不区分「没传」和「传了空」）；
    这个参数只认 body，写成 `?scope=all` 是无效的（会被忽略）。
    """
    scope: str = "current"


@router.post("/auto-assign-referees")
async def auto_assign_referees(
    tournament_id: int,
    request: Request,
    body: AutoAssignBody | None = None,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """指派裁判（手动点一次）。

    规则：
    - scope=current：最早一个**还有比赛没打完**的轮次；scope=all：全部这样的轮次都分完；
    - 候选人 = 本赛事在场选手（未退赛、账号未禁用）里**不参加该场**的人 ——
      注意同一批里另一场的选手也算"不在这一场"，8 人局里两场互为对方的候选人池；
    - **均衡轮转 + 并列随机**：优先挑**本赛事已安排执裁场次最少**的人，次数并列的人之间
      等概率随机。所以它仍是"随机分配"（并列者里谁被挑中是随机的），但不再出现
      "有人吹 4 场、有人一场没吹"那种纯随机的偏斜（实测见 README）；
      计数口径 = 本赛事里 `referee_id` 是他、且**在任**（没卸任）的场次，
      本次请求里已经分出去的场次也算（跨批次继续均衡）；
    - **同一批里**一个人最多当一场的裁判（人不能分身；跨批次可以重复当，但会被上面的
      计数器拉住，所以是"轮着来"而不是"反复同一个人"）；
    - **只填空缺**：已经有在任裁判的场次不动（想换裁判请用「顶替」/认领）；
    - 在场人不够（例如在场只剩这一场的 4 人）时跳过该场，并在响应里告诉前端有几场跳过。
    """
    # body 缺省（不带 body 的 POST）就是 scope=current：对阵表那个按钮原来就没带 body，
    # 参数写成 `AutoAssignBody | None = None`（同 engine_api.start_tournament 的写法），
    # 避免 FastAPI 把 body 当必填而回 422
    scope = body.scope if body else "current"
    if scope not in ("current", "all"):
        raise HTTPException(status_code=400, detail="scope 只能是 current 或 all")
    tournament = await load_writable_tournament(db, tournament_id)   # 必须进行中；已结束=只读

    candidates = await _active_player_ids(db, tournament_id)
    if not can_assign(scope, user_id=user.id, role=user.role,
                      creator_id=tournament.creator_id, candidate_ids=candidates):
        raise HTTPException(status_code=403, detail=(
            "只有房主、管理员或超级管理员可以一次性分配裁判" if scope == "all"
            else "只有本赛事在场选手可以分配裁判"))

    r_stmt = (
        select(Round)
        .where(Round.tournament_id == tournament_id)
        .where(Round.matches.any(Match.status != MatchStatus.FINISHED))
        .order_by(Round.round_number)
    )
    if scope == "current":
        r_stmt = r_stmt.limit(1)
    rounds_to_do = (await db.execute(r_stmt)).scalars().all()
    if not rounds_to_do:
        return {"ok": True, "scope": scope, "assigned": [], "skipped": [],
                "message": "没有还没打完的场次"}

    # 「第 N 场」的编号必须与前端 withGlobalIndex 一致：轮次顺序 + 轮内 Match.id 顺序。
    # 界面上只有「第 N 场」、没有「第 N 轮」（轮次只是内部概念，对阵表并不显示），
    # 所以所有面向用户的文案一律用场次编号。
    id_rows = await db.execute(
        select(Match.id).join(Round, Round.id == Match.round_id)
        .where(Match.tournament_id == tournament_id)
        .order_by(Round.round_number, Match.id)
    )
    ordinal = {mid: i + 1 for i, (mid,) in enumerate(id_rows.all())}

    def fmt_nums(nums) -> str:
        """把场次编号拼成「3、4」（太多时省略成「3、4、5 等 6」）。"""
        out = [str(n) for n in nums]
        return "、".join(out[:3]) + (f" 等 {len(out)}" if len(out) > 3 else "")

    def no_of(match_ids) -> str:
        return fmt_nums([ordinal.get(mid, "?") for mid in match_ids])

    # 均衡轮转用的计数，口径：本赛事里"referee_id 是他、且还没卸任"的场次
    # = 他现在担着几场裁判。用"在任"而不是"历史上被安排过"：卸任等于这活不归他了
    # （那场会被别人重新认领、那个人也会记上一笔），所以他不该背着这个数被扣掉后续机会。
    served_rows = await db.execute(
        select(Match.referee_id).where(
            Match.tournament_id == tournament_id,
            Match.referee_id.is_not(None),
            Match.referee_released_at.is_(None),
        )
    )
    served: dict[int, int] = {}
    for (uid,) in served_rows.all():
        if uid is not None:
            served[uid] = served.get(uid, 0) + 1

    picks: list[tuple[int, int]] = []
    skipped: list[int] = []
    by_id: dict[int, Match] = {}
    already_ok: list[int] = []          # 本来就有裁判的场次（用于「无需再次分配」文案）
    for rnd in rounds_to_do:
        # 先赛事（load_writable_tournament 的 FOR SHARE）再比赛行，与 claim/记分同一加锁顺序
        m_res = await db.execute(
            select(Match).where(Match.round_id == rnd.id).order_by(Match.id).with_for_update()
        )
        round_matches = m_res.scalars().all()
        live = [m for m in round_matches if m.status != MatchStatus.FINISHED]
        need = [m for m in live if not m.has_active_referee]
        already_ok.extend(m.id for m in live if m.has_active_referee)
        if not need:
            continue

        # 本场 4 名选手：用来把"参加这场的人"排除出候选人
        pairing_ids = {pid for m in need for pid in (m.pairing_a_id, m.pairing_b_id)}
        p_res = await db.execute(select(RoundPairing).where(RoundPairing.id.in_(pairing_ids)))
        pairings = {p.id: p for p in p_res.scalars().all()}
        round_targets: list[tuple[int, set[int]]] = []
        for m in need:
            pa, pb = pairings.get(m.pairing_a_id), pairings.get(m.pairing_b_id)
            if pa is None or pb is None:
                continue    # 数据不完整（正常不会发生）：跳过而不是 500
            by_id[m.id] = m
            round_targets.append((m.id, {pa.player_a_id, pa.player_b_id,
                                         pb.player_a_id, pb.player_b_id}))

        # 同一批里已经在执裁的人，不能再分给别的场次；跨批次重新计算（每轮各自一份 busy）
        busy_ids = {m.referee_id for m in live
                    if m.has_active_referee and m.referee_id is not None}
        # 刚在这场上卸任的人不要再被自动分回来（他明确说了不行）。均衡计数按"在任"算，
        # 卸任会让他变成次数最少的那个 —— 不加这层就会立刻把他分回同一场。
        # 想吹回来可以手动认领（认领不看这层）。
        forbidden = {m.id: {m.referee_id} for m in need if m.referee_id is not None}
        round_picks = assign_referees(round_targets, candidates, busy_ids=busy_ids,
                                      served=served, forbidden=forbidden)
        picked_map = dict(round_picks)
        picks.extend(round_picks)
        skipped.extend(mid for mid, _players in round_targets if mid not in picked_map)
        # 本次请求里刚分出去的也要累加：scope=all 会把整局的批次一次分完，
        # 不累加的话第二批又会在"全 0"的计数里随机，均衡就白做了
        for _mid, uid in round_picks:
            served[uid] = served.get(uid, 0) + 1

    if not picks:
        if skipped:
            return {"ok": True, "scope": scope, "assigned": [], "skipped": skipped,
                    "message": f"第 {no_of(skipped)} 场没有可用的场下人员"}
        return {"ok": True, "scope": scope, "assigned": [], "skipped": [],
                "message": f"第 {no_of(already_ok)} 场已有裁判，无需再次分配"}

    u_res = await db.execute(select(User).where(User.id.in_({uid for _m, uid in picks})))
    users_map = {u.id: u for u in u_res.scalars().all()}

    assigned = []
    for mid, uid in picks:
        m = by_id[mid]
        m.referee_id = uid
        m.referee_released_at = None   # 与 claim_referee 同一形态：指派即恢复在任
        assigned.append({"match_id": mid, "match_number": ordinal.get(mid, 0),
                         "referee_id": uid,
                         "username": users_map[uid].username if uid in users_map else ""})

    # 站内消息按人合并成一条：scope=all 一次可能给同一人分到十几场，
    # 一场一条会把消息列表刷屏（而且这种通知本质是「你今天要执裁哪几场」）
    per_user: dict[int, list[int]] = {}
    for mid, uid in picks:
        per_user.setdefault(uid, []).append(ordinal.get(mid, 0))
    for uid, nums in per_user.items():
        nums.sort()
        db.add(Notification(
            user_id=uid,
            tournament_id=tournament_id,
            type="referee_assigned",
            # 只提「第 N 场」：界面上的编号就是对阵表的编号，没有「第 N 轮」这个概念
            message=f"您被分配为第 {fmt_nums(nums)} 场的裁判，请到记分页执裁。",
        ))

    # 先提交再广播再审计（全仓统一）：订阅者收到 referee_claimed 后会回查赛程，
    # 先提交再广播再审计（全仓统一）：订阅者收到广播后会回查赛程/比赛，
    # 未提交的话卡片上的「裁 X」还是空的，这次实时更新就被吞掉。
    # **只广播一条汇总事件**：scope=all 一次可能分掉上百场，逐场广播会让每个在线端
    # 把整份赛程回拉 N 次（含 pairing、应援头像，几十~上百 KB/次），手机上明显卡顿；
    # 认领/卸任这种单场操作仍走 referee_claimed（见 claim_referee / release_referee）。
    await db.commit()
    from app.core.websocket import manager
    await manager.broadcast(tournament_id, {
        "type": "referees_assigned", "count": len(picks),
    })
    await audit(
        user=user, action="referee_auto_assign",
        target_type="tournament", target_id=tournament_id,
        detail={"scope": scope, "assigned": assigned, "skipped": skipped},
        ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
    )
    return {"ok": True, "scope": scope,
            "assigned": assigned, "skipped": skipped, "message": ""}
