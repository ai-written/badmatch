"""
引擎触发 API: 开始赛事、退赛重排、追加比赛、重排赛程
"""
import random
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete
from sqlalchemy.orm import lazyload
from app.core.database import get_db
from app.core.security import require_user
from app.core.audit import audit, get_client_ip
from app.core.websocket import manager
from app.models.user import User
from app.models.tournament import Tournament, TournamentStatus, Registration, PlayerStats
from app.models.round import Round, RoundStatus, RoundPairing, Match, MatchStatus, Notification
from app.models.round import MatchSupport
from pydantic import BaseModel
from app.engine.scheduler import (
    generate_schedule, compute_match_count, compute_rounds, shuffle_sides,
    fairness_step, nearest_valid_total, _pair_key, _match_key,
)

# 总场次上限：与开赛时的校验同一个数（原先是硬编码两处，追加比赛也要用同一个上限）
MAX_TOTAL_MATCHES = 100

# 跨赛事避重：新排赛程时把「本赛事已排的」+「**最近几场赛事**的」搭档/对手/对局统计
# 一起当历史喂给排程器，让本周尽量不出现上周打过的「同 4 人 + 同分组」对局。
#
# 为什么只承诺"避开相同的对局/搭档"，不承诺"搭档完全不重复"：8 人 14 场时每人打 7 场、
# 正好要跟其余 7 人各搭档一次，而搭档组合总共只有 C(8,2)=28 种 —— 一周就用满全部搭档
# 位，跨周必然重复。这是组合数学的限制，不是算法能绕开的（9 人以上搭档池才有多余空间）。
#
# 取 3 场（不是 1 场）：只看上一场会让配对在两份赛程之间来回摆（上周 A 组、这周 B 组、
# 下周又回到 A 组）；往前看 3 场能把重复推得更远。历史只影响打分、不改变每人目标场次，
# 所以「每人场次相等」不受影响（有单测）。
RECENT_EVENT_HISTORY = 3


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
        select(Tournament).where(Tournament.id == tournament_id)
        # 只要赛事自己的列：整行会把 5 个 selectin 集合（报名/场地/轮次/比赛/战绩）一起拉回来，
        # 而这些写路径（开赛/加赛/重排/退赛/结束）全都不看它们
        .options(lazyload('*')).with_for_update()
    )
    tournament = t.scalar_one_or_none()
    if not tournament:
        raise HTTPException(status_code=404, detail="赛事不存在")
    if tournament.creator_id != user.id and user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="只有赛事创建者、管理员或超级管理员可以开始比赛")
    if tournament.status != TournamentStatus.OPEN:
        raise HTTPException(status_code=400, detail="赛事不是报名中状态")

    # 被禁用的报名者不进赛程，并且顺带把他们的报名置为取消（保留行、记取消时间），
    # 否则会出现「名单里显示 8 人、赛程里只有 7 人」，而开赛后又没有 PlayerStats
    # 可供「退赛」清理 —— 那条报名会永久卡在名单里。
    # 取消记录在「取消/退赛记录」里能查到，开赛审计里也记了条数。
    disabled_regs = await db.execute(
        select(Registration)
        .join(User, User.id == Registration.user_id)
        .where(
            Registration.tournament_id == tournament_id,
            Registration.is_active == True,
            User.is_active == False,
        )
    )
    disabled_cancelled = 0
    for r in disabled_regs.scalars().all():
        r.is_active = False
        r.cancelled_at = func.now()
        disabled_cancelled += 1

    regs = await db.execute(
        select(Registration)
        .join(User, User.id == Registration.user_id)
        .where(
            Registration.tournament_id == tournament_id,
            Registration.is_active == True,
            # 被禁用的账号登录不进来，排进赛程就是给全场安排一场打不了的比赛
            User.is_active == True,
        )
        # 固定顺序（报名先后的稳定序）：这个列表马上会被**随机洗一次**再喂给排程算法，
        # 但仍然必须先有一份确定的顺序 —— 否则不写 ORDER BY 时顺序由执行计划决定，
        # 同一个 shuffle_seed 也复现不出同一份赛程（洗牌本身是"洗这份确定的名单"）。
        .order_by(Registration.created_at, Registration.id)
    )
    registrations = regs.scalars().all()
    player_ids = [r.user_id for r in registrations]

    if len(player_ids) < 4:
        raise HTTPException(status_code=400, detail="至少需要 4 人参赛")

    # 开赛时把名单顺序**真随机**打散一次。两层目的各管一件事：
    # - 跨赛事避重（_new_match_history）负责"别跟最近几场撞同样的对局"；
    # - 这次洗牌负责"别总是报名最早的那 4 个人打第一场、每场的分组结构也别老一样"：
    #   算法本身是确定性的（同一份名单顺序 → 同一份赛程），不洗牌的话同一群体每周
    #   同样顺序报名就会拿到一模一样的第一场。
    #
    # 用种子而不是直接 random.shuffle：出问题时按审计里的 shuffle_seed 能复现同一份
    # **对阵与分组**（算法内部的重试本来就是固定种子，所以"顺序 + 历史 + 场次数"定了，
    # 谁跟谁打、怎么分组就定了；只有落库前那次左右翻边是另一层随机，不受这个种子控制）。
    # 洗过的顺序会跟着 PlayerStats 一起落库，本赛事后续的追加比赛 / 退赛重排都按它算，
    # 所以**同一赛事内仍然可复现**，只有"每次开赛"才是随机的。
    shuffle_seed = random.randrange(1, 2 ** 31)
    random.Random(shuffle_seed).shuffle(player_ids)

    # 总场次：显式传入的值先校验；库里的存量值也必须复检。
    # 库里可能是「创建时按旧人数钉下的场次」，报名人数变化后就不整除了，
    # 直接拿去排程会抛异常（未捕获 → 500 → 赛事永远开不起来），
    # 这里与 withdraw 的重排逻辑保持一致：不整除就按当前人数重算。
    if body and body.total_matches is not None:
        requested = body.total_matches
        if requested < 1 or requested > MAX_TOTAL_MATCHES or (4 * requested) % len(player_ids) != 0:
            raise HTTPException(
                status_code=400,
                detail=f"总场次需在 1-{MAX_TOTAL_MATCHES} 之间且保证每名选手场次相同",
            )
        tournament.total_matches = requested
        M = requested
    elif tournament.total_matches:
        # 存量值与当前人数不整除（报名人数变了、上限被改过、或库里的老数据）：
        # 取**离它最近**的合法档，而不是最小档 —— 用最小值会把「8 人 14 场」在
        # 人数变动后直接砍成 3 场（每人只打一场），用户看到的就是「场次没了」
        M = nearest_valid_total(len(player_ids), tournament.total_matches)
    else:
        # 从未设过总场次（直接调接口建的赛事）：按当前人数取最小可行场次
        M = compute_match_count(len(player_ids))

    # 跨赛事避重：把最近几场赛事的搭档/对手/对局统计喂给排程器（本赛事此时还没排过赛程，
    # 所以只会是别的赛事的）。同一群体每周固定打球时，这一步让"上周那场 4 人、同样分组"
    # 不会原样再来一次。
    partner_history, opponent_history, match_history = await _new_match_history(db, tournament_id)

    # 排程理论上对「4M 能被 N 整除」的组合都可行（scheduler 内部已用换序重试消除贪心死路），
    # 但仍保留一层兜底：万一某个档位排不出来，退到可行的最小场次，保证赛事总能开起来，
    # 而不是 500 回滚后卡在 open。顺序很重要：必须先试请求值，否则会把用户选的场次无故改小。
    #
    # 每一档再套一层"历史级别"（_try_schedule）：先带跨赛事历史试、不行才不带历史重试**同一档**。
    # 注意历史现在已经退出贪心打分（只在候选挑选时用，见 scheduler.generate_schedule），
    # 所以这一层在 N ≤ 48 时基本不会再触发 —— 贪心的**可行性**与历史无关，两个级别要么都成、
    # 要么都失败。留着它是为了 N > 48 那一档（那里历史仍参与贪心，可能真的会因此走死路），
    # 以及万一将来把历史重新放回打分。审计里的 history_level 因此通常就是 0。
    schedule = None
    adjusted_M = M
    history_level = None
    candidate_Ms = [M]
    next_M = compute_match_count(len(player_ids))
    while next_M <= max(M, 30):
        if next_M != M:
            candidate_Ms.append(next_M)
        next_M += len(player_ids)          # 只试整除的组合（与改动前一模一样）
    for cand_M in candidate_Ms:
        schedule, history_level = _try_schedule(
            player_ids, cand_M,
            ((partner_history, opponent_history, match_history), ({}, {}, {})),
        )
        if schedule is not None:
            adjusted_M = cand_M
            break
    if schedule is None:
        raise HTTPException(
            status_code=400,
            detail="当前人数无法排出赛程，请调整报名人数后重试",
        )
    if adjusted_M != M:
        # 排程在个别组合下排不出请求的场次，会退到可行的档位
        M = adjusted_M
    # 一律写回**实际使用**的场次：存量值对当前人数不整除时，上面已经把它就近换档了，
    # 不写回的话详情页会一直显示旧数字（「共 14 场」而实际排了 15 场）
    tournament.total_matches = M

    matches_per_round = 2
    # 左右随机：排程本身是确定性的（见 scheduler），并列时总是让名单靠前的人进 A 队，
    # 不翻边的话名单第一人会在自己每一场里都站左边。翻边只改 A/B 队归属，不动对阵。
    schedule = shuffle_sides(schedule)
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
        detail={"players": len(player_ids), "matches": M, "rounds": len(rounds_data),
                # 被禁用而自动取消的报名数：事后能解释"报名 8 人怎么只排了 7 人"
                "disabled_registrations_cancelled": disabled_cancelled,
                # 开赛时打散名单用的种子：有它才能按同一份输入复现这份赛程
                "shuffle_seed": shuffle_seed,
                # 用上了哪一级避重历史（0 = 带跨赛事历史，1 = 退到不带历史）
                "history_level": history_level},
        ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
    )
    return {"ok": True, "rounds": len(rounds_data), "matches": M, "total_matches": M}


class AddMatchesBody(BaseModel):
    matches: int


async def _active_player_ids(db: AsyncSession, tournament_id: int) -> list[int]:
    """在场选手 id（按 PlayerStats.id，即**开赛时打散后的名单顺序**）。

    顺序必须固定：这个列表会直接喂给排程算法，顺序不同排出来的对阵也不同
    （与 withdraw 重排用的是同一个口径）。开赛时会把名单真随机洗一次再落库
    （见 start_tournament 的 shuffle_seed），所以这里拿到的是那次洗牌的顺序，
    不再是报名顺序 —— 这也正是"别总是报名最早的人打第一场"的实现方式。

    「在场」= PlayerStats.is_active 且**账号未被禁用**：禁用不改 PlayerStats
    （那是"退赛"的语义、也刻意保留数据），但被禁用的人登录不进来，
    把他排进新比赛等于凭空制造一场没人能打的比赛。
    """
    rows = await db.execute(
        select(PlayerStats.user_id)
        .join(User, User.id == PlayerStats.user_id)
        .where(
            PlayerStats.tournament_id == tournament_id,
            PlayerStats.is_active == True,
            User.is_active == True,
        )
        .order_by(PlayerStats.id)
    )
    return [uid for (uid,) in rows.all()]


async def _load_history(db: AsyncSession, tournament_ids):
    """给定赛事的搭档/对手/对局统计，交给排程器做重复惩罚。

    取**全部轮次**而不只是打过的：追加比赛时，还没开打的比赛也已经排在那里了，
    跟它排成一模一样同样是"重复"。
    """
    ids = [tid for tid in tournament_ids if tid is not None]
    if not ids:
        return Counter(), Counter(), Counter()

    partner_history: Counter = Counter()
    opponent_history: Counter = Counter()
    match_history: Counter = Counter()

    pairing_rows = await db.execute(
        select(RoundPairing)
        .join(Round, Round.id == RoundPairing.round_id)
        .where(Round.tournament_id.in_(ids))
    )
    pairings: dict[int, RoundPairing] = {}
    for p in pairing_rows.scalars().all():
        key = _pair_key(p.player_a_id, p.player_b_id)
        # 库里搭档键的方向不保证（见 scheduler 的说明），两个方向各记一次
        partner_history[key] += 1
        partner_history[key[::-1]] += 1
        pairings[p.id] = p

    match_rows = await db.execute(
        select(Match).where(Match.tournament_id.in_(ids))
    )
    for m in match_rows.scalars().all():
        pa, pb = pairings.get(m.pairing_a_id), pairings.get(m.pairing_b_id)
        if not pa or not pb:
            continue
        t1 = _pair_key(pa.player_a_id, pa.player_b_id)
        t2 = _pair_key(pb.player_a_id, pb.player_b_id)
        match_history[_match_key(t1, t2)] += 1
        for u in t1:
            for v in t2:
                opponent_history[_pair_key(u, v)] += 1

    return partner_history, opponent_history, match_history


async def _recent_tournament_ids(db: AsyncSession, tournament_id: int) -> list[int]:
    """本赛事之前创建的最近几场赛事 id（新的在前）。

    只取 id 更小的：赛事按 id 递增创建，所以"下周的赛事"（提前建好、id 更大）不会
    反过来影响"本周的赛事"。还没排过赛程的赛事查不到配对，带进来也无害。
    """
    rows = await db.execute(
        select(Tournament.id)
        .where(Tournament.id < tournament_id)
        .order_by(Tournament.id.desc())
        .limit(RECENT_EVENT_HISTORY)
    )
    return [tid for (tid,) in rows.all()]


async def _new_match_history(db: AsyncSession, tournament_id: int):
    """新排比赛（开赛 / 追加）要避开的重复：**本赛事已排的 + 最近几场赛事的**。"""
    recent = await _recent_tournament_ids(db, tournament_id)
    return await _load_history(db, [tournament_id, *recent])


def _try_schedule(players, matches, history_variants):
    """按「历史逐级放宽」的顺序试排一份赛程，返回 (schedule, level)；全失败返回 (None, None)。

    - `history_variants`：从最完整的避重到最弱的顺序，例如
      开赛 = (跨赛事历史, 不带历史)；追加 = (跨赛事+本赛事, 只本赛事)；
      重排 = (旧赛程+最近几场, 只最近几场, 不带历史)。
    - `level` 就是用上的那一级的下标（写进审计，便于事后解释这份赛程的避重口径）。

    注意这两级在 N ≤ 48 时**基本等价**：历史已经退出贪心打分、只影响候选挑选
    （见 scheduler.generate_schedule），所以贪心的可行性与历史无关 —— 第一级排不出来时
    第二级同样排不出来。留着第二级是为了 N > 48（那一档历史仍参与贪心）以及将来万一
    把历史放回打分；因此审计里的 `history_level` 通常就是 0。
    """
    for level, hist in enumerate(history_variants):
        try:
            return generate_schedule(players, matches, *hist), level
        except ValueError:
            continue
    return None, None


@router.get("/extra-match-options")
async def extra_match_options(
    tournament_id: int,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """「追加比赛」的可选项。

    约束是"每人新增场次相同"：追加数只能是 fairness_step(N) 的倍数。
    可选项由服务端算、前端只负责展示，免得「界面能选、提交却被接口拒」。
    """
    t = await db.execute(
        select(Tournament).where(Tournament.id == tournament_id).options(lazyload('*'))
    )
    tournament = t.scalar_one_or_none()
    if not tournament:
        raise HTTPException(status_code=404, detail="赛事不存在")

    players = await _active_player_ids(db, tournament_id)
    n = len(players)
    # 「现有场次」按库里真实的 matches 行数算，不用 tournaments.total_matches：
    # 那个字段在退赛重排后不写回（重排只删未开打的轮次、已结束的保留），
    # 拿它当前值会让弹层显示、100 场上限和审计里的数字都偏小
    current = (await db.execute(
        select(func.count(Match.id)).where(Match.tournament_id == tournament_id)
    )).scalar() or 0
    if tournament.status != TournamentStatus.ONGOING or n < 4:
        return {"players": n, "step": 0, "current_total": current,
                "remaining": 0, "options": []}

    step = fairness_step(n)
    options = []
    added = step
    # 最多给 10 个档位：8 人时 step=2，够选到 +20 场，再多也没人点
    while current + added <= MAX_TOTAL_MATCHES and len(options) < 10:
        total = current + added
        options.append({
            "added": added,
            "per_person_added": (4 * added) // n,
            "total": total,
            # 每人总场次：已有的场次本身是均分的（开赛/重排都按同一约束生成）
            "per_person_total": (4 * total) // n,
        })
        added += step

    return {
        "players": n,
        "step": step,
        "current_total": current,
        "remaining": max(0, MAX_TOTAL_MATCHES - current),
        "options": options,
    }


@router.post("/add-matches")
async def add_matches(
    tournament_id: int,
    request: Request,
    body: AddMatchesBody,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """进行中的赛事再追加几场（开场次排少了、还有时间）。

    只追加、不动已有比赛：新比赛接在最后一个轮次之后，没开打的旧轮次原样保留。
    """
    t = await db.execute(
        select(Tournament).where(Tournament.id == tournament_id)
        # 只要赛事自己的列：整行会把 5 个 selectin 集合（报名/场地/轮次/比赛/战绩）一起拉回来，
        # 而这些写路径（开赛/加赛/重排/退赛/结束）全都不看它们
        .options(lazyload('*')).with_for_update()
    )
    tournament = t.scalar_one_or_none()
    if not tournament:
        raise HTTPException(status_code=404, detail="赛事不存在")
    if tournament.creator_id != user.id and user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="只有赛事创建者、管理员或超级管理员可以追加比赛")
    if tournament.status != TournamentStatus.ONGOING:
        raise HTTPException(status_code=400, detail="只有进行中的赛事可以追加比赛")

    players = await _active_player_ids(db, tournament_id)
    n = len(players)
    if n < 4:
        raise HTTPException(status_code=400, detail="在场选手不足 4 人，无法再排比赛")

    if body.matches < 1:
        raise HTTPException(status_code=400, detail="追加场次至少 1 场")
    step = fairness_step(n)
    if body.matches % step != 0:
        raise HTTPException(
            status_code=400,
            detail=f"当前 {n} 人在场，追加场次必须是 {step} 的倍数，否则每人场次会不一样",
        )
    # 「现有场次」= 库里真实的 matches 行数（不是 tournaments.total_matches）：
    # 退赛重排只删未开打的轮次、保留已结束的，且从不写回该字段；拿它当"现有场次"
    # 会让上限判断与界面/审计里的数字都偏小（实际可超出 100 场）
    current = (await db.execute(
        select(func.count(Match.id)).where(Match.tournament_id == tournament_id)
    )).scalar() or 0
    if current + body.matches > MAX_TOTAL_MATCHES:
        raise HTTPException(
            status_code=400,
            detail=f"总场次上限 {MAX_TOTAL_MATCHES} 场（现有 {current} 场）",
        )

    partner_history, opponent_history, match_history = await _new_match_history(db, tournament_id)
    # 只避"本赛事已排的重复"这一级历史（= 加上跨赛事避重之前的行为）：带着跨赛事历史
    # 个别组合会走死路，这一级是每一档的兜底，绝不能等到"所有档位都失败"才用 ——
    # 那会把用户请求的档位悄悄翻倍（实测 5 人局：请求 +5 变成 +10）
    own_partner, own_opponent, own_match = await _load_history(db, [tournament_id])

    # 贪心排程在个别组合下会走入死路（开赛那边也有同样的兜底）：先按用户请求的档位试，
    # 每一档都「带跨赛事历史 → 只带本赛事历史」各试一次，都排不出来才按步长往上加。
    # 顺序很重要：先退历史、再改档位 —— 否则会把用户选的追加数悄悄改大。
    schedule = None
    history_level = None
    added = body.matches
    while current + added <= MAX_TOTAL_MATCHES:
        schedule, history_level = _try_schedule(
            players, added,
            ((partner_history, opponent_history, match_history),
             (own_partner, own_opponent, own_match)),
        )
        if schedule is not None:
            break
        added += step
    if schedule is None:
        raise HTTPException(status_code=400, detail="当前人数排不出更多场次，请减少追加场次后重试")

    last_num_row = await db.execute(
        select(func.max(Round.round_number)).where(Round.tournament_id == tournament_id)
    )
    last_num = last_num_row.scalar() or 0

    rounds_data = compute_rounds(shuffle_sides(schedule), 2)
    for round_idx, round_matches in enumerate(rounds_data, start=1):
        r = Round(tournament_id=tournament_id, round_number=last_num + round_idx)
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

    total = current + added
    tournament.total_matches = total
    # 先提交再广播再审计（全仓统一）：订阅者收到 schedule_updated 后回查赛程，
    # 未提交的话读到的是追加前的旧赛程，这一次实时更新就被吞掉
    await db.commit()
    await manager.broadcast(tournament_id, {"type": "schedule_updated"})
    await audit(
        user=user, action="tournament_add_matches",
        target_type="tournament", target_id=tournament_id,
        detail={"added": added, "requested": body.matches, "total_matches": total,
                "players": n, "rounds": len(rounds_data),
                # 用上了哪一级避重历史（0 = 跨赛事+本赛事，1 = 只本赛事）
                "history_level": history_level},
        ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
    )
    return {
        "ok": True,
        "added": added,
        "requested": body.matches,
        "total_matches": total,
        "rounds": len(rounds_data),
        "per_person_added": (4 * added) // n,
    }


class WithdrawBody(BaseModel):
    new_creator_id: int | None = None


@router.post("/reschedule")
async def reschedule_schedule(
    tournament_id: int,
    request: Request,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """重排赛程（赛事创建者 / 管理员 / 超管），且**只在一场都没开打时**允许。

    用途：这份赛程不合适（比如跟上周撞得太多、或分组看着别扭）时换一份。
    一旦有比赛在打或已打完就不能重排 —— 这个入口会把**当前这份**赛程整批删掉重排，
    那等于毁掉正在进行/已经产生的比分与历史（所以界面上的按钮就叫「重排赛程」，
    不叫"重排未打完的场次"：打到一半的场次是不能重排的）。详情页也会据此不显示按钮，
    但按钮的显隐是体验层，真正的边界在这里。

    与「退赛重排」是同一套机制，只有两点不同：

    1. 人不减（名单仍是当前在场选手），所以它不需要退赛那套房主转让逻辑；
    2. **故意引入随机**（洗牌名单顺序）。开赛/追加/退赛重排必须可复现，而"重排"这个
       动作本身就是"再给我换一份"：算法是确定性的，沿用同一份输入会排出完全一样的结果，
       点了就像没反应（这正是这个按钮最容易踩的坑）。

    避重历史照旧：这份旧赛程（玩家已经看过那些对局）+ 最近 3 场赛事（见
    RECENT_EVENT_HISTORY）都算"用过的"，新的一份尽量避开它们。
    """
    t = await db.execute(
        select(Tournament).where(Tournament.id == tournament_id)
        # 只要赛事自己的列：整行会把 5 个 selectin 集合（报名/场地/轮次/比赛/战绩）一起拉回来，
        # 而这些写路径（开赛/加赛/重排/退赛/结束）全都不看它们
        .options(lazyload('*')).with_for_update()
    )
    tournament = t.scalar_one_or_none()
    if not tournament:
        raise HTTPException(status_code=404, detail="赛事不存在")
    if tournament.creator_id != user.id and user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="只有赛事创建者、管理员或超级管理员可以重排赛程")
    if tournament.status != TournamentStatus.ONGOING:
        raise HTTPException(status_code=400, detail="只有进行中的赛事可以重排赛程")

    # 一场都不能开打：在打的（有人正在记分）和已打完的（历史/比分）都必须原样留着。
    # 行锁已经拿到（FOR UPDATE 与比赛级接口的 FOR SHARE 互斥），所以这里判过之后
    # 不会再有比赛在本次重排期间被打完。
    started = (await db.execute(
        select(func.count(Match.id)).where(
            Match.tournament_id == tournament_id,
            Match.status != MatchStatus.PENDING,
        )
    )).scalar() or 0
    if started > 0:
        raise HTTPException(
            status_code=400,
            detail=f"已经有 {started} 场开打了，不能再重排赛程（重排只用于开赛前调整）",
        )

    # 先把「当前这份赛程」的重复统计记下来（含马上要删掉的未打场次）：它们是玩家已经
    # 看到过的安排，新的一份要避开，否则重排后看起来"没变化"。再并上最近几场赛事的。
    partner_history, opponent_history, match_history = await _load_history(db, [tournament_id])
    recent_partner, recent_opponent, recent_match = await _load_history(
        db, await _recent_tournament_ids(db, tournament_id))
    partner_history.update(recent_partner)
    opponent_history.update(recent_opponent)
    match_history.update(recent_match)

    players = await _active_player_ids(db, tournament_id)
    n = len(players)
    if n < 4:
        raise HTTPException(status_code=400, detail="在场选手不足 4 人，无法重排赛程")

    # 至此可以断定：库里这个赛事的比赛**全都是 PENDING**（一场没开打），所以下面把
    # **所有轮次**整批删掉重排 —— 注意不能只删 `Round.status == PENDING` 的轮次：
    # `POST /rounds/{id}/start-round` 会把轮次标成 ONGOING 而比赛仍是 PENDING，
    # 只按轮次状态过滤会漏掉它，结果就是"旧的一份 + 新的一份"叠加（每人多打一场、
    # 场次数与返回的 replaced_matches 也对不上）。这里按"比赛一场没开打"整批换。
    all_rounds = await db.execute(
        select(Round).where(Round.tournament_id == tournament_id)
    )
    released_referees: set[int] = set()
    replaced_rounds = 0
    replaced_matches = 0
    for r in all_rounds.scalars().all():
        ms = await db.execute(select(Match).where(Match.round_id == r.id, Match.referee_id.is_not(None)))
        for m in ms.scalars().all():
            # 只通知在任裁判：已卸任者（referee_id 保留作历史）不需要重新认领
            if m.has_active_referee:
                released_referees.add(m.referee_id)
        match_ids = await db.execute(select(Match.id).where(Match.round_id == r.id))
        for (mid,) in match_ids.all():
            await db.execute(delete(MatchSupport).where(MatchSupport.match_id == mid))
            replaced_matches += 1
        await db.execute(delete(Match).where(Match.round_id == r.id))
        await db.execute(delete(RoundPairing).where(RoundPairing.round_id == r.id))
        await db.execute(delete(Round).where(Round.id == r.id))
        replaced_rounds += 1

    if tournament.total_matches:
        # 与开赛同一口径：不整除时取**离原值最近**的合法档，别缩到最小档
        M = nearest_valid_total(n, tournament.total_matches)
    else:
        M = compute_match_count(n)

    # 见 docstring：显式重排洗牌，保证"再点一次会换一份"。同样记种子进审计，
    # 与开赛那次洗牌一个口径：能复现"谁跟谁打、怎么分组"（左右两边另算）。
    reschedule_shuffle_seed = random.randrange(1, 2 ** 31)
    random.Random(reschedule_shuffle_seed).shuffle(players)

    # 贪心在个别组合下会走死路（与开赛/追加同一层兜底）：逐级放宽历史 ——
    # ① 避开"这份旧赛程 + 最近几场" → ② 只避开最近几场 → ③ 不带历史。
    # 历史只影响打分、不改变每人场次，所以任何一级排出来都比给用户一个 400 好；
    # 名单顺序已经洗过牌，即使退到 ③ 也基本不会排出和刚才一样的一份。
    schedule, history_level = _try_schedule(
        players, M,
        ((partner_history, opponent_history, match_history),
         (recent_partner, recent_opponent, recent_match),
         ({}, {}, {})),
    )
    if schedule is None:
        raise HTTPException(status_code=400, detail="当前人数排不出赛程，请调整人数后重试")

    schedule = shuffle_sides(schedule)          # 与开赛/追加同口径，抹掉"名单靠前者总在左队"
    rounds_data = compute_rounds(schedule, 2)

    # 新轮次接在最大轮号之后。正常情况下待删的轮次就是全部（一场没开打），
    # 所以 last_num 会是 0、新赛程从第 1 轮开始；照查不误是为了将来规则宽松时不用改这里。
    last_num = (await db.execute(
        select(Round.round_number)
        .where(Round.tournament_id == tournament_id)
        .order_by(Round.round_number.desc())
        .limit(1)
    )).scalar() or 0

    for round_idx, round_matches in enumerate(rounds_data, start=1):
        r = Round(tournament_id=tournament_id, round_number=last_num + round_idx,
                  is_regenerated=True)
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

    for ref_id in released_referees:
        db.add(Notification(
            user_id=ref_id,
            tournament_id=tournament_id,
            type="schedule_changed",
            message="赛事赛程已重排，您之前认领的裁判场次已取消，请重新认领。",
        ))

    # 写回真实总场次（正常情况下就等于 M：一场都没开打，全部场次都是这次排出来的）。
    # 仍然按库里的真实行数写，是为了万一将来放开"允许重排已开打的赛事"时这里不用改。
    tournament.total_matches = (await db.execute(
        select(func.count(Match.id)).where(Match.tournament_id == tournament_id)
    )).scalar() or M

    # 先提交再广播再审计（全仓统一）：订阅者收到 schedule_updated 后会立刻拉赛程，
    # 未提交的话读到的还是重排前的旧轮次；审计随后
    await db.commit()
    await manager.broadcast(tournament_id, {"type": "schedule_updated"})
    await audit(
        user=user, action="tournament_reschedule",
        target_type="tournament", target_id=tournament_id,
        detail={"players": n, "matches": M, "replaced_matches": replaced_matches,
                "replaced_rounds": replaced_rounds,
                "released_referees": len(released_referees),
                "total_matches": tournament.total_matches,
                # 用上了哪一级避重历史（0 = 旧赛程+最近几场，1 = 只最近几场，2 = 不带）
                "history_level": history_level,
                # 这次重排打散名单用的种子：与开赛一样，可按它复现"谁跟谁打、怎么分组"
                "shuffle_seed": reschedule_shuffle_seed},
        ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
    )
    return {
        "ok": True,
        "players": n,
        "matches": M,
        "replaced_matches": replaced_matches,
        "replaced_rounds": replaced_rounds,
        "released_referees": len(released_referees),
        "total_matches": tournament.total_matches,
    }


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
        select(Tournament).where(Tournament.id == tournament_id)
        # 只要赛事自己的列：整行会把 5 个 selectin 集合（报名/场地/轮次/比赛/战绩）一起拉回来，
        # 而这些写路径（开赛/加赛/重排/退赛/结束）全都不看它们
        .options(lazyload('*')).with_for_update()
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
                select(PlayerStats.user_id)
                .join(User, User.id == PlayerStats.user_id)
                .where(
                    PlayerStats.tournament_id == tournament_id,
                    PlayerStats.is_active == True,
                    PlayerStats.user_id != player_id,
                    User.is_active == True,   # 不把房主传给已禁用的账号
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
            select(PlayerStats)
            .join(User, User.id == PlayerStats.user_id)
            .where(
                PlayerStats.tournament_id == tournament_id,
                PlayerStats.user_id == body.new_creator_id,
                PlayerStats.is_active == True,
                # 被禁用的账号登录不进来，不能当房主（否则赛事没人能推进流程）
                User.is_active == True,
            )
        )
        if ok_new.scalar_one_or_none() is None:
            raise HTTPException(status_code=400, detail="新房主必须是本赛事在场选手")
        tournament.creator_id = body.new_creator_id
    elif tournament.creator_id == player_id:
        remaining_players = await db.execute(
            select(PlayerStats.user_id)
            .join(User, User.id == PlayerStats.user_id)
            .where(
                PlayerStats.tournament_id == tournament_id,
                PlayerStats.is_active == True,
                PlayerStats.user_id != player_id,
                User.is_active == True,   # 同上：不把房主传给已禁用的账号
            ).limit(1)
        )
        first = remaining_players.scalar_one_or_none()
        if first:
            tournament.creator_id = first

    # 哪些轮次「已经开打了」= 该轮里**有任何一场不是 PENDING**（在打或已打完）。
    #
    # 必须按**比赛状态**判断，不能按 Round.status：全仓没有任何地方把轮次标成 FINISHED
    # （只有 start-round 会标 ONGOING），按轮次状态判断会把"已打完的轮次"当成没开打、
    # 整批删掉 —— 历史比分被抹掉而 PlayerStats 还留着分（对阵表和积分榜对不上，"第N场"
    # 也从 1 重来）。真实踩过，所以这里改成按比赛判：开打过的轮次冻结保留，
    # 一场没打的轮次才删掉重排。
    started_round_ids = {
        rid for (rid,) in (await db.execute(
            select(Match.round_id).where(
                Match.tournament_id == tournament_id,
                Match.status != MatchStatus.PENDING,
            ).distinct()
        )).all()
    }

    all_rounds = await db.execute(
        select(Round).where(Round.tournament_id == tournament_id)
    )
    affected_referees = set()
    for r in all_rounds.scalars().all():
        if r.id in started_round_ids:
            r.is_frozen = True              # 开打过的轮次：编号与比赛原样保留
            continue
        # 一场没打的轮次：整批删掉，稍后重排（先收被取消场次的在任裁判，好发通知）
        ms = await db.execute(select(Match).where(Match.round_id == r.id, Match.referee_id.is_not(None)))
        for m in ms.scalars().all():
            # 只通知在任裁判：已卸任者（referee_id 保留作历史）不需要重新认领
            if m.has_active_referee:
                affected_referees.add(m.referee_id)
        match_ids = await db.execute(select(Match.id).where(Match.round_id == r.id))
        for (mid,) in match_ids.all():
            await db.execute(delete(MatchSupport).where(MatchSupport.match_id == mid))
        await db.execute(delete(Match).where(Match.round_id == r.id))
        await db.execute(delete(RoundPairing).where(RoundPairing.round_id == r.id))
        await db.execute(delete(Round).where(Round.id == r.id))

    # 本赛事已打完（冻结）轮次的搭档统计：重排出来的未开打轮次当然不能跟它们撞
    frozen_partner: dict = {}
    frozen_rounds = await db.execute(
        select(Round).where(Round.tournament_id == tournament_id, Round.is_frozen == True)
    )
    for r in frozen_rounds.scalars().all():
        pairings = await db.execute(select(RoundPairing).where(RoundPairing.round_id == r.id))
        for p in pairings.scalars().all():
            key = (p.player_a_id, p.player_b_id)
            frozen_partner[key] = frozen_partner.get(key, 0) + 1

    remaining = []
    stats = await db.execute(
        select(PlayerStats)
        .join(User, User.id == PlayerStats.user_id)
        .where(
            PlayerStats.tournament_id == tournament_id,
            PlayerStats.is_active == True,
            # 被禁用的账号不进重排：重排本来就是为了"人变了，重新排"，
            # 把登录不进来的人排进新的未开打轮次，等于制造打不了的比赛
            User.is_active == True,
        )
        # 与 start_tournament 同一口径：remaining 会直接喂给排程算法，
        # 顺序不同重排出来的对阵也不同。PlayerStats 没有 created_at，
        # 用 id 即等于「开赛时洗过牌、落库下来的名单顺序」（那批行就是按那个顺序建的）。
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

    if tournament.total_matches:
        # 与开赛/重排同一口径：不整除时取**离原值最近**的合法档（退赛后人数变了，
        # 用最小档会把 14 场砍成 3 场）
        new_M = nearest_valid_total(remaining_count, tournament.total_matches)
    else:
        new_M = compute_match_count(remaining_count)
    # 跨赛事避重：重排出来的、一场没打的轮次也不该跟最近几场赛事撞车（见 RECENT_EVENT_HISTORY）。
    # 历史只影响打分、不改变每人目标场次；万一带着它排不出来，就退回"只带本赛事已冻结轮次"
    # 的那一级 —— 「退赛」这条路不能因为避重而失败，也不能因此给用户一个 500。
    recent_partner, recent_opponent, recent_match = await _load_history(
        db, await _recent_tournament_ids(db, tournament_id))
    partner_history: Counter = Counter(frozen_partner)
    partner_history.update(recent_partner)
    new_schedule, reschedule_history_level = _try_schedule(
        remaining, new_M,
        ((partner_history, recent_opponent, recent_match), (frozen_partner, {}, {})),
    )
    if new_schedule is None:
        raise HTTPException(status_code=400, detail="当前人数排不出赛程，请调整人数后重试")
    # 与开赛/追加同口径：落库前随机翻边。重排出来的对阵同样带着「名单靠前者总在左队」
    # 的确定性偏置（remaining 是按 PlayerStats.id 排的名单），不翻边就会把偏置带进新赛程
    new_schedule = shuffle_sides(new_schedule)
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

    # 把重排后的真实总场次写回 tournaments.total_matches：
    # 重排只删「未开打」的轮次、已结束的比赛保留着，所以它既不等于 new_M，
    # 也不再等于原来的值。不写回的话，赛事详情页的「共 N 场」会一直停在旧数字，
    # 而追加比赛/上限判断（另一处已改成直接数 matches）也会被带偏。
    tournament.total_matches = (await db.execute(
        select(func.count(Match.id)).where(Match.tournament_id == tournament_id)
    )).scalar() or new_M

    # 先提交再广播再审计：这里重排了赛程、通知了原裁判，订阅者收到后若立刻拉赛程，
    # 未提交的话会读到重排前的旧轮次；审计随后（见 auth.update_profile 的说明）
    await db.commit()
    await manager.broadcast(tournament_id, {"type": "registration_updated"})
    await audit(user=user, action="tournament_withdraw", target_type="tournament", target_id=tournament.id,
                detail={"player_id": player_id, "remaining": remaining_count,
                        "new_creator_id": tournament.creator_id,
                        # 用上了哪一级避重历史（0 = 冻结轮次+最近几场，1 = 只冻结轮次）
                        "history_level": reschedule_history_level},
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
        select(Tournament).where(Tournament.id == tournament_id)
        # 只要赛事自己的列：整行会把 5 个 selectin 集合（报名/场地/轮次/比赛/战绩）一起拉回来，
        # 而这些写路径（开赛/加赛/重排/退赛/结束）全都不看它们
        .options(lazyload('*')).with_for_update()
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
