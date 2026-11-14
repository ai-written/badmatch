"""「优先场地」模式（court_first）的落库逻辑：打完一场，现场排下一场。

与「优先公平性」的根本区别：不排完整赛程。任何时候**还没打完的场次最多只保留
"场地数"那么多**（正在打 + 待打），这样现场看到的就是"这几场在打 / 马上打"，
场地空出来立刻有下一场顶上（12 人 2 片场地实测空等 0 分钟）。

补排时机（都走 `jit_fill`）：
- 开赛：生成第一批（= 场地数那么多场）；
- 每场比赛结束（force_end 之后）：补 1 场；
- 追加比赛 / 退赛之后：按新的人数与配额补。

公平性口径（与事前排程不同，写在这里以免误解）：
- 每人场次**允许差 1 场**（`pick_court_first_match` 只从"最少的一档、必要时放宽一档"里挑）；
- 搭档/对手/完全相同对局仍然惩罚（同一套权重），但它天生近视 —— 看不到后面谁空出来，
  实测 12 人 36 场搭档重复约 13 次（事前排程是 6 次 = 理论下限）。

混双（`tournaments.mixed_doubles`）：补出来的每一场都必须是"一男一女 vs 一男一女"，
所以空闲的人里没有 2 男 2 女时这一批就少排一场（场地先空着，等对面打完放人）。
性别分布一变（典型：有人退赛，男女不再相等）就自动按普通双打补排 —— 退赛不能被
"混双凑不齐人"拦住；已在打的场次不受影响。
"""
from collections import Counter

import logging
import random

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.jit import pick_court_first_match
from app.engine.scheduler import (
    _match_key, _pair_key, compute_match_count, matches_per_round,
    mixed_doubles_available,
)
from app.models.round import Match, MatchStatus, Round, RoundPairing
from app.models.tournament import Court, PlayerStats, Tournament
from app.models.user import User

logger = logging.getLogger(__name__)


async def player_genders(db: AsyncSession, player_ids) -> dict[int, str | None]:
    """这批选手的性别（混双补排要用）。没设置的账号值为 None。"""
    ids = list(player_ids)
    if not ids:
        return {}
    rows = await db.execute(select(User.id, User.gender).where(User.id.in_(ids)))
    return {uid: g for (uid, g) in rows.all()}


async def active_players(db: AsyncSession, tournament_id: int) -> list[int]:
    """在场选手（未退赛 + 账号未禁用），顺序按 PlayerStats.id（开赛时的名单顺序）。"""
    rows = await db.execute(
        select(PlayerStats.user_id)
        .join(User, User.id == PlayerStats.user_id)
        .where(PlayerStats.tournament_id == tournament_id,
               PlayerStats.is_active == True,          # noqa: E712
               User.is_active == True)                 # noqa: E712
        .order_by(PlayerStats.id)
    )
    return [uid for (uid,) in rows.all()]


async def court_ids(db: AsyncSession, tournament_id: int) -> list[int]:
    rows = await db.execute(
        select(Court.id)
        .where(Court.tournament_id == tournament_id)
        .order_by(Court.sort_order, Court.id)
    )
    return [cid for (cid,) in rows.all()]


async def jit_fill(db: AsyncSession, tournament: Tournament) -> list[int]:
    """把「还没打完的场次」补到场地数那么多。返回新建的 match id（调用方负责提交）。"""
    if (tournament.schedule_mode or "balanced") != "court_first":
        return []

    players = await active_players(db, tournament.id)
    n = len(players)
    if n < 4:
        return []
    courts = await court_ids(db, tournament.id)
    per_round = matches_per_round(n, len(courts))

    matches = (await db.execute(
        select(Match).where(Match.tournament_id == tournament.id)
    )).scalars().all()
    unfinished = [m for m in matches if m.status != MatchStatus.FINISHED]
    slots = per_round - len(unfinished)
    if slots <= 0:
        return []

    pairings = (await db.execute(
        select(RoundPairing).join(Round, Round.id == RoundPairing.round_id)
        .where(Round.tournament_id == tournament.id)
    )).scalars().all()
    pairing_map = {p.id: p for p in pairings}

    busy: set[int] = set()
    played: Counter = Counter()
    partner_c: Counter = Counter()
    opponent_c: Counter = Counter()
    match_c: Counter = Counter()
    for m in matches:
        pa, pb = pairing_map.get(m.pairing_a_id), pairing_map.get(m.pairing_b_id)
        if pa is None or pb is None:
            continue
        t1 = _pair_key(pa.player_a_id, pa.player_b_id)
        t2 = _pair_key(pb.player_a_id, pb.player_b_id)
        partner_c[t1] += 1
        partner_c[t2] += 1
        match_c[_match_key(t1, t2)] += 1
        for u in t1:
            for v in t2:
                opponent_c[_pair_key(u, v)] += 1
        if m.status == MatchStatus.FINISHED:
            for p in t1 + t2:
                played[p] += 1
        else:
            busy.update(t1 + t2)

    plan = tournament.plan_total_matches or tournament.total_matches or compute_match_count(n)
    target = max(1, (4 * plan) // n)

    # 混双（每场一男一女搭档）：性别分布一变（有人退赛）就按普通双打补排 ——
    # 退赛绝不能被"男女不再相等"拦住，但也不能把一场凑不出 2 男 2 女的比赛写进库里。
    mixed = bool(getattr(tournament, "mixed_doubles", False))
    genders: dict[int, str | None] = {}
    if mixed:
        genders = await player_genders(db, players)
        mixed = mixed_doubles_available(genders)
        if not mixed:
            # 走到这里说明库里的标记与现实不符（正常路径是 withdraw_player 先把标记降级、
            # 并给房主发消息）。这里只按普通双打补排，**不改标记**（这个函数在记分的写
            # 事务里被调用，顺手改赛事状态容易引发难以复现的问题），留一条日志便于排查。
            logger.warning(
                "jit_fill: 赛事 %s 标着混双，但当前名单男女不相等或有人未设置性别，本次按普通双打补排",
                tournament.id,
            )

    free = [p for p in players if p not in busy]
    occupied = {m.court_id for m in unfinished if m.court_id}
    last_round = (await db.execute(
        select(func.max(Round.round_number)).where(Round.tournament_id == tournament.id)
    )).scalar() or 0

    created: list[int] = []
    for _ in range(slots):
        pick = pick_court_first_match(free, played, target, partner_c, opponent_c,
                                       match_c, rng=random,
                                       mixed_doubles=mixed, genders=genders)
        if pick is None:
            break                       # 都打满了 / 凑不齐 4 个人 → 不再排（赛事自然收尾）
        t1, t2 = pick
        # 左右随机：挑出来的两队都按 (小 id, 大 id) 排好了，直接落库会让"名单靠前者总在左边"
        # （与开赛那次 shuffle_sides 要解决的是同一个偏置）。只换 A/B 归属，不动对阵。
        if random.random() < 0.5:
            t1, t2 = t2, t1
        last_round += 1
        # 每场单独一个"轮次"：JIT 没有"轮"的概念，一轮一场最直白，
        # 也不会让"第 N 场"的编号跟轮次顺序打架（编号本来就按轮次顺序 + id）
        r = Round(tournament_id=tournament.id, round_number=last_round)
        db.add(r)
        await db.flush()
        pa = RoundPairing(round_id=r.id, player_a_id=t1[0], player_b_id=t1[1])
        pb = RoundPairing(round_id=r.id, player_a_id=t2[0], player_b_id=t2[1])
        db.add(pa)
        db.add(pb)
        await db.flush()
        court_id = next((cid for cid in courts if cid not in occupied), None)
        m = Match(
            tournament_id=tournament.id,
            round_id=r.id,
            pairing_a_id=pa.id,
            pairing_b_id=pb.id,
            court_id=court_id,
        )
        db.add(m)
        await db.flush()
        if court_id:
            occupied.add(court_id)
        created.append(m.id)

        # 这一批里已经排上的人，不能在同一次补排里再出现
        for p in t1 + t2:
            if p in free:
                free.remove(p)
        partner_c[t1] += 1
        partner_c[t2] += 1
        match_c[_match_key(t1, t2)] += 1
        for u in t1:
            for v in t2:
                opponent_c[_pair_key(u, v)] += 1

    return created
