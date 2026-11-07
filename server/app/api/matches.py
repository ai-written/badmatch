from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, Request
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import lazyload, noload
from app.core.database import get_db
from app.core.security import require_user
from app.core.audit import audit, get_client_ip
from app.core.websocket import manager
from app.core.occupancy import conflicts_for_match, ongoing_map, match_players
from app.engine.scheduler import use_round_engine, matches_per_round
# 分流判定（"这局是不是多场地同时开打"）放在 engine_api 里，与此处的拦截共用同一套口径。
# engine_api 不 import matches，所以这里不会成环。
from app.api.engine_api import is_multi_court_tournament
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


_user_brief_cache: tuple | None = None
_pairing_brief_cache: tuple | None = None
_court_brief_cache: tuple | None = None

# 开打闸门的咨询锁命名空间（任意固定常数，只要和别的 advisory lock 不撞即可；
# auth 里首用户创建用的是单参数形式 pg_advisory_xact_lock(1)，与此不冲突）
START_GATE_LOCK_NS = 20261106


def _current_round_ids(live_round_ids: list[int],
                       ongoing_round_ids: set[int]) -> set[int]:
    """「当前这一批」是哪些轮次 = 正在进行中的场次所在轮次 ∪ 最早一个还有未打完场次的轮次。

    `live_round_ids` 必须已按轮次升序。

    为什么不能只取"最早一个还没打完的轮次"：开启跳场后（某场 4 人里有人还在打，
    就先打后面能打的），正在打的可能是后面的轮次，而最早那个轮次还一场没开打 ——
    只按"最早未完成"给裁判，就会出现"按钮给第 7 场派裁判、裁判跑去了第 8 场"。

    没有进行中的场次时，退化回老口径（最早一个还有未打完场次的轮次），
    所以单场地/8 人及以下的赛事行为完全不变。
    """
    if not live_round_ids:
        return set()
    return (set(ongoing_round_ids) & set(live_round_ids)) | {live_round_ids[0]}


def _user_brief() -> tuple:
    """「只要用户基本字段」时的加载选项。

    `User` 的 4 个集合关系全是 `lazy="selectin"`：不显式关掉的话，**每次** `select(User)`
    都会再发 4 条查询把「他报名过的赛事 / 战绩 / 创建的赛事 / 执裁过的比赛」整批拉回来；
    而 `created_tournaments` 里的 Tournament 又自带 5 个 selectin 集合（registrations/
    courts/rounds/matches/player_stats），一条链能炸出几十条 SQL —— 实测一次 `/rounds`
    因此执行了 **136 条** 查询、本地就要 90ms+（线上经 frp 转发更明显）。

    这和 tournaments.py 的 `_brief_loads()` 是同一个坑、同一套解法：读接口自己用
    `id.in_(...)` 批量取需要的字段就够了，别让 ORM 顺藤摸瓜。

    必须在**调用时**构建：noload() 会立即触发 mapper 配置，import 阶段有些模型还没注册。
    """
    global _user_brief_cache
    if _user_brief_cache is None:
        _user_brief_cache = (
            noload(User.registrations), noload(User.player_stats),
            noload(User.created_tournaments), noload(User.refereed_matches),
        )
    return _user_brief_cache


def _pairing_brief() -> tuple:
    """配对只要两个选手 id：`matches_as_a/matches_as_b` 也是 selectin，一并关掉。"""
    global _pairing_brief_cache
    if _pairing_brief_cache is None:
        _pairing_brief_cache = (
            noload(RoundPairing.matches_as_a), noload(RoundPairing.matches_as_b),
        )
    return _pairing_brief_cache


def _court_brief() -> tuple:
    """场地只要名字：时间段由调用方单独批量查（Court.time_slots 也是 selectin）。"""
    global _court_brief_cache
    if _court_brief_cache is None:
        _court_brief_cache = (noload(Court.time_slots),)
    return _court_brief_cache


async def load_writable_tournament(db: AsyncSession, tournament_id: int) -> Tournament:
    """载入赛事并确保它处于进行中。

    赛事被提前结束（end-tournament）时，往往还会有没打完的比赛。
    这些比赛必须变成只读：否则仍能认领裁判、记分、交换场地，
    在已结束的赛事上继续改动数据。所有会改数据的比赛级接口都应先过这里。

    这里额外取一个**共享行锁**（FOR SHARE），把全局加锁顺序钉成「先赛事、后比赛」：
    - 比赛级接口都会改 matches 行；而退赛（withdraw_player）是先锁赛事行、再删 matches、
      同时改 player_stats。若比赛级接口先锁 matches 再锁赛事，就会出现
      「A 持 matches 等赛事 vs B 持赛事等 matches」的环路，PostgreSQL 直接报死锁（用户看到 500）。
    - 共享锁之间相容：多个裁判同时记分互不阻塞；只有退赛/开赛/结束这类持排他锁的操作
      与它们互斥 —— 这正是想要的语义：改赛事结构与改比分不并发。
      实测：持 FOR SHARE 时 FOR UPDATE 会被阻塞到前者提交。
    因此**调用点必须在锁比赛行之前调用本函数**，顺序反了这个保证就没了。

    `lazyload("*")`：这里只用到 status/creator_id 等列属性，而 Tournament 的 5 个集合都是
    `lazy="selectin"` —— 不显式关掉的话，一次 select 会连带把整张赛事图（轮次、比赛、
    配对、报名、战绩）拉出来，而这条查询每次记分/换边/认领都会跑，现在还在行锁持有期间。
    若将来这里要读关系，请显式加 eager 选项（别依赖默认行为，那正是性能陷阱）。
    """
    t = await db.execute(
        select(Tournament).where(Tournament.id == tournament_id)
        .with_for_update(read=True)
        .options(lazyload("*"))
    )
    tournament = t.scalar_one_or_none()
    if not tournament:
        raise HTTPException(status_code=404, detail="赛事不存在")
    if tournament.status != TournamentStatus.ONGOING:
        raise HTTPException(status_code=400, detail="赛事已结束，无法再修改比赛")
    return tournament


# 读取接口统一要求登录：对阵/单场/应援票数/积分榜都不再匿名可读。写操作的边界不受影响。
@router.get("/rounds", response_model=list[RoundOut])
async def list_rounds(
    tournament_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_user),
):
    # 轮次与比赛各查一次，比赛按 tournament_id 一次取全再在内存里按轮次分组。
    # 原先这里是「先查轮次、再对每一轮单独查一次比赛」，而且两处都没关掉 selectin
    # （Round.matches / Round.pairings 都是 selectin），一次请求因此要跑上百条 SQL。
    result = await db.execute(
        select(Round).where(Round.tournament_id == tournament_id)
        .order_by(Round.round_number)
        .options(noload(Round.matches), noload(Round.pairings))
    )
    rounds = result.scalars().all()

    # 读取赛事状态：已结束时前端据此把整页置为只读（这里不抛错，只用于展示）。
    # 只取 status 这一列 —— 整行 ORM 会把赛事的 5 个 selectin 集合也一起拉回来。
    status_row = await db.execute(
        select(Tournament.status).where(Tournament.id == tournament_id)
    )
    t_status_value = status_row.scalar_one_or_none()
    # 赛事不存在时返回 404，而不是「200 + 空数组」：
    # 后者会让前端把「赛事已被删除/链接失效」显示成「该赛事还没有赛程」
    if t_status_value is None:
        raise HTTPException(status_code=404, detail="赛事不存在")
    t_status = t_status_value.value

    # 必须显式按 id 排序：PostgreSQL 无 ORDER BY 时按堆内物理顺序返回，而 UPDATE（认领裁判、
    # 记分）会把行写成新版本并挪到堆尾，导致同一场比赛在刷新后位置漂移、对阵表顺序跳动。
    # 一条查询按 id 排全局顺序，下面按 round_id 分组时组内顺序自然也是 id 升序。
    matches_result = await db.execute(
        select(Match).where(Match.tournament_id == tournament_id).order_by(Match.id)
    )
    matches_by_round: dict[int, list[Match]] = {}
    for m in matches_result.scalars().all():
        matches_by_round.setdefault(m.round_id, []).append(m)

    bye_map = await _get_bye_players(rounds, db)
    # 当前批次：按"有未打完场次的轮次"（升序）和"有进行中场次的轮次"算出来，见 _current_round_ids
    current_round_ids = _current_round_ids(
        [r.id for r in rounds
         if any(m.status != MatchStatus.FINISHED for m in matches_by_round.get(r.id, []))],
        {r.id for r in rounds
         if any(m.status == MatchStatus.ONGOING for m in matches_by_round.get(r.id, []))},
    )

    # 一次批量组装所有比赛（内部按 id.in_ 批量查配对/用户/场地/时间段/应援），
    # 再按轮次分组拼装，避免每轮重复付一次批量查询的钱
    all_matches = [m for rid_matches in matches_by_round.values() for m in rid_matches]
    match_outs = await _build_matches_out(all_matches, db, user, t_status)
    out_by_id = {mo.id: mo for mo in match_outs}
    round_number_of = {
        m.id: r.round_number for r in rounds for m in matches_by_round.get(r.id, [])
    }
    # 「这场在几号场」= 真实分配到的那片场地（开赛按计划预填、开打那一刻可重分配，
    # 见 _assign_start_court）。court_name 就是从 matches.court_id 解析出来的名字。
    for mo in match_outs:
        mo.round_number = round_number_of.get(mo.id, 0)
        mo.court_label = mo.court_name

    # 多场地赛事：把「这场现在能不能开打」提前算出来给前端 ——
    # 否则裁判得点一下记分才知道"测试4 还在另一场"，现场很别扭。
    # 单场地赛事不算（那里一场一场打，不存在撞期，老行为一行不变）。
    if await is_multi_court_tournament(db, tournament_id):
        busy = await ongoing_map(db, tournament_id)
        if busy:
            players_by_match = await match_players(db, all_matches)
            names = await db.execute(
                select(User.id, User.username).where(User.id.in_(set(busy)))
            )
            name_of = {uid: uname for uid, uname in names.all()}
            number_of: dict[int, int] = {}
            seq = 0
            for r in rounds:                      # 与前端 withGlobalIndex 同一套编号
                for m in matches_by_round.get(r.id, []):
                    seq += 1
                    number_of[m.id] = seq
            for mo in match_outs:
                if mo.status != "pending":
                    continue
                mine = players_by_match.get(mo.id, set())
                hits = sorted(p for p in mine if p in busy)
                if hits:
                    mo.blocked_by = [
                        {"player_id": p, "username": name_of.get(p, ""),
                         "match_id": busy[p], "match_number": number_of.get(busy[p], 0)}
                        for p in hits
                    ]

    out = []
    for r in rounds:
        byes = bye_map.get(r.id) or []
        out.append(RoundOut(
            id=r.id,
            round_number=r.round_number,
            status=r.status.value,
            is_regenerated=r.is_regenerated,
            matches=[out_by_id[m.id] for m in matches_by_round.get(r.id, []) if m.id in out_by_id],
            # 新字段是列表（12 人一轮 4 人轮空）；老字段保留第一位，只为老客户端不炸
            bye_players=byes,
            bye_player=byes[0] if byes else None,
            is_current=r.id in current_round_ids,
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
    # 带上赛事状态，前端据此决定是否只读。
    # 只取 status 一列：整行 Tournament 会连带把它的 5 个 selectin 集合
    # （报名/场地/轮次/比赛/战绩）拉一遍 —— 记分页每 30s 刷新一次，白付 9 条查询
    t = await db.execute(select(Tournament.status).where(Tournament.id == tournament_id))
    t_status = t.scalar_one_or_none()
    out = await _build_match_out(m, db, user, t_status.value if t_status else None)
    # 「这场在几号场」就是 court_name（match.court_id 解析出来的真实场地）
    out.court_label = out.court_name
    if (await is_multi_court_tournament(db, tournament_id)) and m.status == MatchStatus.PENDING:
        # 还没开打时就把「谁在别场上、能不能开打」告诉记分页：别让裁判点了记分才弹窗
        out.blocked_by = await conflicts_for_match(db, tournament_id, m)
    return out


@router.put("/matches/{match_id}/score")
async def update_score(
    tournament_id: int,
    match_id: int,
    score: ScoreUpdate,
    request: Request,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    # 顺序：先取赛事的共享锁，再锁比赛行（见 load_writable_tournament 的说明）。
    # 反过来的话，与"退赛先锁赛事再删比赛"会形成环路而死锁。
    await load_writable_tournament(db, tournament_id)
    result = await db.execute(
        select(Match).where(Match.id == match_id, Match.tournament_id == tournament_id).with_for_update()
    )
    m = result.scalar_one_or_none()
    if not m:
        raise HTTPException(status_code=404, detail="比赛不存在")
    # 比赛已结束是另一回事：赛事进行中时，管理员/超管、或**本场记录的裁判**（谁记的分谁能改）
    # 仍可修正该场比分并重算胜负
    is_privileged = user.role in ("admin", "superadmin")
    is_match_referee = m.referee_id == user.id
    can_fix_finished = is_privileged or is_match_referee
    # 只有在任裁判能记分：已卸任的裁判（referee_id 仍保留作历史）不再有权限
    # 超级管理员兜底：裁判临时不在、手机没电、或根本没人认领时，管理员/超管可以直接代记分
    # （记第一分就会把比赛从 pending 变 ongoing，也就是"开始比赛"）
    if not (user.role in ("admin", "superadmin") or (m.referee_id == user.id and m.has_active_referee)):
        raise HTTPException(status_code=403, detail="只有本场裁判、管理员或超级管理员可以记分")
    if m.status == MatchStatus.FINISHED and not can_fix_finished:
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

    # 乐观锁：客户端带回来的基线版本已经过期（别人先改过这一场）。
    # 记分请求是「设成绝对值」，直接放行就会把别人刚记的那一分覆盖掉（丢分）；
    # 这里回 409 + 当前真实比分与版本，让客户端把**自己的增量**叠上去重试。
    # 不带 expected_version（旧客户端）时按老行为处理，升级期间不会互相阻断。
    current_version = m.score_version or 0
    if score.expected_version is not None and score.expected_version != current_version:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "比分已被其他人更新，请重试",
                "score_a": m.score_a or 0,
                "score_b": m.score_b or 0,
                "score_version": current_version,
            },
        )

    # 首次记分视为比赛开始。**多场地赛事**里另一片场地可能正在打同一批人、或者两片场地
    # 都还占着 —— 一个人不能同时站两片场地、场地也不能分身，所以开打前先过闸门
    # （见 _ensure_can_start）。单场地赛事不做（那里本来就是一场一场打）。
    if m.status == MatchStatus.PENDING:
        if await is_multi_court_tournament(db, tournament_id):
            await _ensure_can_start(db, tournament_id, m)
        m.status = MatchStatus.ONGOING
        m.started_at = datetime.now()

    m.score_a = score.score_a
    m.score_b = score.score_b
    # 每次改比分都 +1：客户端靠它判断自己的基线是否还新（见上面的 409 分支）
    m.score_version = current_version + 1

    # Only end via explicit force_end
    if score.force_end:
        # 已结束的比赛：管理员/超管可以再结束一次，用来在修正比分后重算胜负
        # （previous 非空 → _finalize_match 只做增量调整，不重复记账）
        if m.status == MatchStatus.FINISHED and not can_fix_finished:
            raise HTTPException(status_code=400, detail="比赛已结束")
        sa, sb = score.score_a, score.score_b
        if sa == sb:
            raise HTTPException(status_code=400, detail="比分相同，无法结束")
        winner = m.pairing_a_id if sa > sb else m.pairing_b_id
        await _finalize_match(m, winner, db, previous=previous)
        await _maybe_finish_tournament(tournament_id, db, user=user, request=request)
        # 先提交再广播（同 swap_sides）：广播是网络 IO，不应在持有行锁时进行；
        # 提交后订阅者立刻拉取也能读到最新比分与赛事状态。
        await db.commit()
        await _broadcast_match(m, tournament_id)
        # 审计放最后（业务提交与广播之后）：见 auth.update_profile 的说明 —— 写在提交前，
        # 业务回滚就会留下假日志；而 audit 对请求取消是向上抛的，写在广播前会让广播被跳过。
        await audit(
            user=user, action="match_force_end",
            target_type="match", target_id=m.id,
            detail={"score_a": sa, "score_b": sb, "winner_pairing_id": winner},
            ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
        )
        return {"ok": True, "finished": True, "score_version": m.score_version}

    # 同上：先提交再广播，缩短持锁时间（记分是最高频的写操作，影响最明显）
    await db.commit()
    await _broadcast_match(m, tournament_id)
    # 高频操作：默认不记录，由 AUDIT_HIGH_FREQ_ENABLED 控制；写也放在提交与广播之后
    await audit(
        user=user, action="match_score_update",
        target_type="match", target_id=m.id,
        detail={"score_a": score.score_a, "score_b": score.score_b},
        ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
        high_freq=True,
    )
    # 回带新的版本号：客户端据此更新自己的基线（不用猜"一定 +1"）
    return {"ok": True, "score_version": m.score_version}


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
    # 顺序：先赛事共享锁，再比赛行锁（同 update_score，见 load_writable_tournament）
    await load_writable_tournament(db, tournament_id)
    result = await db.execute(
        select(Match)
        .where(Match.id == match_id, Match.tournament_id == tournament_id)
        .with_for_update()
    )
    m = result.scalar_one_or_none()
    if not m:
        raise HTTPException(status_code=404, detail="比赛不存在")
    # 同上：已卸任的裁判不能再交换场地
    # 同 update_score：管理员/超级管理员也可交换场地
    if not (user.role in ("admin", "superadmin") or (m.referee_id == user.id and m.has_active_referee)):
        raise HTTPException(status_code=403, detail="只有本场裁判、管理员或超级管理员可以交换场地")
    if m.status == MatchStatus.FINISHED:
        raise HTTPException(status_code=400, detail="比赛已结束")

    m.is_swapped = (not m.is_swapped) if body.swapped is None else body.swapped
    # 先提交再广播：广播是可能阻塞的网络 IO，而此处仍持有该行的 FOR UPDATE 锁，
    # 拖长持锁时间会挡住同一场比赛的记分请求。提交后数据也已可见，
    # 订阅者收到广播再拉取时不会读到旧值。
    await db.commit()
    await manager.broadcast(tournament_id, {
        "type": "match_updated",
        "match_id": match_id,
        "is_swapped": m.is_swapped,
    })
    # 审计放最后（提交与广播之后），见 auth.update_profile 的说明
    await audit(
        user=user, action="match_swap_sides",
        target_type="match", target_id=m.id,
        detail={"is_swapped": m.is_swapped},
        ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
    )
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
    # 先提交再广播，避免订阅者回查时读到旧状态；审计随后（见 auth.update_profile 的说明）
    await db.commit()
    await manager.broadcast(tournament_id, {"type": "round_started", "round_id": round_id})
    await audit(user=user, action="round_start", target_type="round", target_id=round_id,
                detail={"tournament_id": tournament_id}, ip=get_client_ip(request), user_agent=request.headers.get("user-agent"))
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
    # 先提交再广播，避免订阅者回查时票数还是旧的；审计随后（见 auth.update_profile 的说明）
    await db.commit()
    await manager.broadcast(tournament_id, {
        "type": "support_updated",
        "match_id": match_id,
        "support_a": counts[0],
        "support_b": counts[1],
    })
    # 高频操作：默认不记录（AUDIT_HIGH_FREQ_ENABLED）
    await audit(user=user, action="support_vote", target_type="match", target_id=match_id,
                detail={"side": body.side, "via_retry": upserted_via_retry},
                ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
                high_freq=True)
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
        pairings = await db.execute(
            select(RoundPairing).where(RoundPairing.id.in_(pairing_ids)).options(*_pairing_brief())
        )
        pairings_map = {p.id: p for p in pairings.scalars().all()}

    user_ids = {m.referee_id for m in matches if m.referee_id}
    for p in pairings_map.values():
        user_ids.add(p.player_a_id)
        user_ids.add(p.player_b_id)

    users_map: dict[int, User] = {}
    if user_ids:
        users = await db.execute(select(User).where(User.id.in_(user_ids)).options(*_user_brief()))
        users_map = {u.id: u for u in users.scalars().all()}

    courts_map: dict[int, Court] = {}
    court_ids = {m.court_id for m in matches if m.court_id}
    if court_ids:
        courts = await db.execute(select(Court).where(Court.id.in_(court_ids)).options(*_court_brief()))
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
            score_version=m.score_version or 0,
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
        # 版本号随广播下发：各端据此判断"这条广播是不是比本地基线更新"，
        # 也是记分时填 expected_version 的依据
        "score_version": m.score_version or 0,
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


async def _maybe_finish_tournament(tournament_id: int, db: AsyncSession, user=None, request=None) -> None:
    """比赛全部结束后自动将赛事置为 finished 并广播。

    自动结束同样要写一条 `tournament_end` 审计：这是**最常见的结束路径**
    （正常打完最后一场，而不是谁去点「提前结束」）。原先这里静默改状态，
    管理面板里筛「结束赛事」一条都查不到，看起来就像日志丢了。
    detail.auto 标明是系统自动收尾，触发者是当时正在记分/结束比赛的那位。

    并发与事务边界（顺序很关键，这里踩过两次）：
    1. **先提交**本场结果、放掉 matches 行锁，再取 tournaments 行锁。否则会与
       withdraw_player（先锁 tournaments 再删 matches）形成反向加锁顺序而互相死锁 ——
       记分路径本来已持有 match 行锁。
    2. 拿到 tournaments 行锁**之后**才数剩余场次，数完才决定收尾。**不能图省事先数一次
       就提前返回**：两场「最后比赛」并发结束时，双方都会在对方提交前数到「还剩 1 场」
       而各自返回，赛事就永远停在 ongoing（既没有 tournament_end，也没有
       tournament_finished 广播）。
       代价：每次结束比赛都会多一次提交，并在调用方提交前短暂持有赛事行锁。
       这远小于「比赛全打完了却还显示进行中」的代价。
    """
    # 见注释 1
    await db.commit()
    # populate_existing：强制用新行覆盖 identity map 里可能已存在的同 id 对象。
    # **这一行在并发收尾时必须生效**：等 FOR UPDATE 的过程中，另一个请求（或
    # end-tournament）可能已经改过 status；不同步覆盖就会读到旧值 —— 表现为多写一条
    # tournament_end + 多广播一次 tournament_finished。
    # （load_writable_tournament 的 selectin 预加载会把父对象留在 identity map 里，
    #  所以不能指望"对象已被回收"。）
    # lazyload("*")：本函数只需要回写 status，别在这里把整张赛事图拉出来 —— 这段代码
    # 跑在行锁持有期间，多出的每条查询都在延长其它记分请求的等待。
    t = await db.execute(
        select(Tournament).where(Tournament.id == tournament_id)
        .with_for_update()
        .execution_options(populate_existing=True)
        .options(lazyload("*"))
    )
    tournament = t.scalar_one_or_none()
    if not tournament or tournament.status == TournamentStatus.FINISHED:
        return
    # 见注释 2：必须在拿到锁之后再数，且**只以这一次计数为准**
    remaining = await db.execute(
        select(func.count(Match.id)).where(
            Match.tournament_id == tournament_id,
            Match.status != MatchStatus.FINISHED,
        )
    )
    if (remaining.scalar() or 0) > 0:
        return
    tournament.status = TournamentStatus.FINISHED
    await db.flush()
    # 先提交再广播：订阅者收到 tournament_finished 后可能立刻回查赛事详情，
    # 若此时事务未提交，读到的是仍是 ongoing 的旧状态，这次实时更新就被吞掉了
    await db.commit()
    await manager.broadcast(tournament_id, {"type": "tournament_finished"})
    # 审计放在提交**和广播之后**：
    # - 提交之后：它用独立会话立即提交，写在提交前的话，业务事务一回滚就会留下
    #   「赛事已结束」的假日志；
    # - 广播之后：audit 遇到请求取消会向上抛（见 core/audit.py），写在广播前会让
    #   手机端"提交后立刻切页"直接跳过这次广播，别人一直看到旧状态。
    await audit(
        user=user, action="tournament_end",
        target_type="tournament", target_id=tournament_id,
        detail={"auto": True, "reason": "all_matches_finished"},
        ip=get_client_ip(request) if request is not None else None,
        user_agent=request.headers.get("user-agent") if request is not None else None,
    )


async def _ensure_can_start(db: AsyncSession, tournament_id: int,
                            match: Match) -> None:
    """开打前的闸门：**人齐 且 有场地空着**才允许开这场（多场地赛事）。

    - 人齐：这 4 个人里没有谁还在别的场上（`player_busy`）。刻意**不**用"必须等整轮
      打完"：模拟过 12 人 36 场、单场 5±1.5 分钟、2 片场地，整轮闸门要多花 12.7 分钟
      （场地累计空等 26 分钟），按人拦只要冲突那几场稍等。
    - 有场地：两片场地都还在打 → 物理上不可能再开一场（`no_court_free`）。这里**不给**
      "强制开打"的出口：真开出来就是"同一时刻两场在一片场地上"，那是假数据。
      现场若那场其实已经打完，正确操作是先把它结束（提示里就这么写）。

    并发保护：两个人同时给两场比赛记第一分时，双方都会读到"对方还没开打"而**都放行**，
    于是同一个人真的会同时出现在两片场地上。这里用一个事务级咨询锁按赛事把这次检查
    **连同场地分配一起**串行化。不能改成给赛事行加 FOR UPDATE：本事务已经持有 FOR SHARE
    （见 load_writable_tournament），两个记分事务互相等着对方升级，直接死锁。
    锁随事务提交/回滚释放，只罩住"首次记分"这一小段 —— 后续记分仍互不阻塞。
    """
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:ns, :tid)").bindparams(
            ns=START_GATE_LOCK_NS, tid=tournament_id)
    )
    conflicts = await conflicts_for_match(db, tournament_id, match)
    if conflicts:
        names = "、".join(c["username"] or f"#{c['player_id']}" for c in conflicts)
        raise HTTPException(status_code=400, detail={
            "code": "player_busy",
            "message": f"{names} 还在另一场没打完，等那场结束再开这场；"
                       "如果那场其实已经打完，先去结束它。",
            "conflicts": conflicts,
        })
    # 人要齐了才轮得到场地：把这场挂到一片此刻空着的场地上（写库，见 _assign_start_court）
    if not await _assign_start_court(db, tournament_id, match):
        usage = await _ongoing_court_usage(db, tournament_id, match.id)
        where = "、".join(
            f"{u['court_name'] or '未定场地'}（第{u['round_number']}轮）" for u in usage
        ) or "所有场地"
        raise HTTPException(status_code=400, detail={
            "code": "no_court_free",
            "message": f"{where}都还在打，等任一场结束后再开这场；"
                       "如果某场其实已经打完，先去结束它。",
            "courts": usage,
        })


def _pick_next_matches(ordered_matches, players_by_match: dict[int, set[int]],
                       busy: dict[int, int], slots: int):
    """从待打场次里挑出"现在就能上的那几场"（纯函数，便于单测）。

    顺序走一遍：**4 个人都空闲**的收进 `next`，有人在邻场没打完的记进 `skipped`
    并继续看后面 —— 这就是"跳场"：只改**上场顺序**，人员构成一个都不动，
    所以每人场次、搭档、对手、轮空全部不变。
    """
    next_ids: list[int] = []
    skipped: list[tuple] = []
    if slots <= 0:
        return next_ids, skipped
    for m in ordered_matches:
        if m.status != MatchStatus.PENDING:
            continue
        mine = players_by_match.get(m.id, set())
        waiting = sorted(p for p in mine if p in busy)
        if waiting:
            # 已经凑够这一批就不再往后找；被跳过的只报"挡在它前面的那些"
            skipped.append((m, waiting))
            continue
        next_ids.append(m.id)
        if len(next_ids) >= slots:
            break
    return next_ids, skipped


@router.get("/next-up")
async def next_up(
    tournament_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_user),
):
    """现在该开哪几场 —— 「跳场」的判定结果（多场地赛事用）。

    两片场地各打各的，谁先打完谁先上下一场。最早那场如果有人在邻场还没打完，
    就**跳过它**去打后面能打的（只改顺序，不改人员）。响应里 `skipped` 说明谁在等、
    等的是哪一场，前端据此提示"第 7 场等张三打完第 5 场，可先打第 8 场"。

    老路径（单场地 / 8 人及以下）没有并发：`courts=1`，永远只报最早那一场。
    """
    rows = (await db.execute(
        select(Match, Round.round_number)
        .join(Round, Round.id == Match.round_id)
        .where(Match.tournament_id == tournament_id)
        .order_by(Round.round_number, Match.id)
    )).all()
    if not rows:
        return {"ok": True, "courts": 0, "playing": [], "next": [], "skipped": []}

    matches = [m for m, _ in rows]
    # 「第 N 场」按 (轮次, 比赛 id) 编号 —— 与前端 withGlobalIndex、referee.py 的 ordinal 同源
    number = {m.id: idx for idx, (m, _) in enumerate(rows, start=1)}

    courts = (await db.execute(
        select(func.count(Court.id)).where(Court.tournament_id == tournament_id)
    )).scalar() or 0
    roster = (await db.execute(
        select(func.count(PlayerStats.id)).where(PlayerStats.tournament_id == tournament_id)
    )).scalar() or 0
    live_count = (await db.execute(
        select(func.count(PlayerStats.id)).where(
            PlayerStats.tournament_id == tournament_id,
            PlayerStats.is_active == True,
        )
    )).scalar() or 0

    multi = use_round_engine(roster, courts)
    courts_now = matches_per_round(live_count, courts) if multi else 1

    playing = [m for m in matches if m.status == MatchStatus.ONGOING]
    busy = await ongoing_map(db, tournament_id)
    players_by_match = await match_players(db, matches)
    slots = max(0, courts_now - len(playing))
    next_ids, skipped = _pick_next_matches(matches, players_by_match, busy, slots)

    wait_names: dict[int, str] = {}
    if skipped:
        wait_ids = {p for _m, waiting in skipped for p in waiting}
        named = await db.execute(select(User).where(User.id.in_(wait_ids)))
        wait_names = {u.id: u.username for u in named.scalars().all()}

    return {
        "ok": True,
        "courts": courts_now,
        "playing": [{"match_id": m.id, "match_number": number[m.id]} for m in playing],
        "next": [{"match_id": mid, "match_number": number[mid]} for mid in next_ids],
        "skipped": [
            {
                "match_id": m.id,
                "match_number": number[m.id],
                "waiting": [
                    {
                        "player_id": p,
                        "username": wait_names.get(p, ""),
                        "match_id": busy.get(p),
                        "match_number": number.get(busy.get(p), 0),
                    }
                    for p in waiting
                ],
            }
            for m, waiting in skipped
        ],
    }


async def _assign_start_court(db: AsyncSession, tournament_id: int,
                              match: Match) -> bool:
    """开打那一刻给这场分配一片**此刻空着的**场地，并写进 `matches.court_id`。

    返回是否"有场地可用"：单片场地（老路径，不做分配）恒为 True。

    为什么必须"开打时分配"而不是开赛时定死：现场两片场地节奏不一样 ——
    2 号场先打完，下一场就该换到 2 号场。计划值（开赛时按轮内位置预填的）一旦
    与现实脱节，界面上的场地号就会说假话（曾经按"轮内第几场"推导，结果两场
    同时都显示"2号场"）。

    规则：本场计划用的那片场地如果空着就沿用它；否则挑一片**没有进行中比赛**的。
    两片都占着 → 返回 False，调用方据此拒绝开打（物理上不可能两场同时在一片场地上）。

    必须在取到开打咨询锁之后调用（见 `_ensure_can_start`），
    否则两场同时开打可能都挑到同一片场地。
    """
    courts = (await db.execute(
        select(Court.id)
        .where(Court.tournament_id == tournament_id)
        .order_by(Court.sort_order, Court.id)
    )).scalars().all()
    if len(courts) < 2:
        return True
    ongoing = (await db.execute(
        select(Match.court_id).where(
            Match.tournament_id == tournament_id,
            Match.status == MatchStatus.ONGOING,
            Match.id != match.id,
        )
    )).scalars().all()
    occupied = {cid for cid in ongoing if cid is not None}
    if match.court_id in courts and match.court_id not in occupied:
        return True
    free = [cid for cid in courts if cid not in occupied]
    if not free:
        return False
    match.court_id = free[0]
    return True


async def _ongoing_court_usage(db: AsyncSession, tournament_id: int,
                               exclude_match_id: int) -> list[dict]:
    """此刻占着场地的是哪几场（给"没有空场地"的提示用）。"""
    rows = await db.execute(
        select(Match.id, Round.round_number, Court.name)
        .join(Round, Round.id == Match.round_id)
        .outerjoin(Court, Court.id == Match.court_id)
        .where(Match.tournament_id == tournament_id,
               Match.status == MatchStatus.ONGOING,
               Match.id != exclude_match_id)
        .order_by(Round.round_number, Match.id)
    )
    return [{"match_id": mid, "round_number": rn, "court_name": cn}
            for mid, rn, cn in rows.all()]


async def _get_bye_players(rounds: list[Round], db: AsyncSession) -> dict[int, list[PlayerInfo]]:
    """批量计算每轮的轮空选手，避免逐轮查询。

    返回**列表**：12 人 2 片场地时一轮有 4 个人轮空，只给一个名字会误导
    （而且"给哪一个"取决于 id 大小，固定偏向最早注册的账号）。
    `RoundOut.bye_player` 仍取第一位，纯粹为了老客户端不炸。
    """
    if not rounds:
        return {}

    round_ids = [r.id for r in rounds]
    paired_by_round: dict[int, set[int]] = {rid: set() for rid in round_ids}
    pairings = await db.execute(
        select(RoundPairing).where(RoundPairing.round_id.in_(round_ids))
        .options(*_pairing_brief())
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
        rows = await db.execute(select(User).where(User.id.in_(all_player_ids)).options(*_user_brief()))
        users_map = {u.id: u for u in rows.scalars().all()}

    result: dict[int, list[PlayerInfo]] = {}
    for r in rounds:
        bye_ids = all_player_ids - paired_by_round.get(r.id, set())
        # 按 id 排序（而不是 set 的迭代顺序）：同一份数据在不同环境/版本下必须显示同样的顺序
        bye_list: list[PlayerInfo] = []
        for uid in sorted(bye_ids):
            u = users_map.get(uid)
            if u:
                bye_list.append(PlayerInfo(
                    id=u.id,
                    username=u.username,
                    avatar=u.avatar or "",
                    gender=u.gender,
                ))
        result[r.id] = bye_list
    return result
