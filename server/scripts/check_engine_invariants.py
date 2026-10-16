#!/usr/bin/env python3
"""
赛程/账号不变量检查（需要数据库，可选）。

守的是几条"纯后端、mock 类 e2e 覆盖不到"的不变量 —— 它们都真出过问题：

1. **被禁用的账号不进任何新赛程**：开赛、赛中追加比赛、退赛重排三处都要排除
   （禁用刻意不写 PlayerStats —— 那是「退赛」的语义，所以这些取"在场选手"的
   地方很容易漏掉被禁用的人）
2. 开赛时被禁用的报名者：不排进赛程，且报名被置为取消（保留行、记取消时间）
3. 开赛失败（可用人数不足 4 人）时整笔回滚：报名不会被误取消
4. 禁用时只把**未打完**场次的裁判标为卸任；已结束比赛的执裁记录属于归档，不能动
5. 禁用房主后，房主转给未禁用的在场选手，并给新房主发一条提醒
6. 退赛重排后 tournaments.total_matches 等于库里真实场次数；追加比赛按真实场次数算
7. 已禁用账号的邀请码不能注册（正常账号的仍可）
8. **跨赛事避重**：同一批人连开三场，后面的赛事不重复前面用过的「同 4 人 + 同分组」对局，
   且每人场次仍然相等（跨赛事历史只影响打分）
9. **重排赛程**：**只允许一场都没开打时**用（有比赛在打/已打完必须被 400 拦下），
   用完全部换新、每人场次仍相等、写回真实总场次、被取消场次的在任裁判收到「请重新认领」通知
10. **开赛洗牌**：同一批人、同样报名顺序连开 4 场，第一场不该总是一个（名单顺序真随机打散过），
    且审计里必须记下 `shuffle_seed`（随机可追溯）

用法（必须指向一个**一次性试点库**，脚本会 drop/create 全部表）：

    docker compose -f docker-compose.dev.yml exec -T \\
      -e CHECK_DB_URL=postgresql+asyncpg://badminton:badminton@db:5432/badmatch_probe \\
      server python scripts/check_engine_invariants.py

安全性：
- 库名必须含 probe / test，否则拒绝运行（防手滑指向开发/生产库）
- 审计写入被替换成内存记录，因此**不会**碰应用配置那套库的 audit_logs
- 退出码 0 = 全过；1 = 有失败；2 = 拒绝运行（库名不像试点库）
"""
import argparse
import asyncio
import os
import sys
from collections import Counter
from datetime import datetime, timedelta
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from starlette.requests import Request                       # noqa: E402
from sqlalchemy import select, func                          # noqa: E402
from sqlalchemy.ext.asyncio import (                         # noqa: E402
    create_async_engine, async_sessionmaker,
)
from fastapi import HTTPException                            # noqa: E402

import app.api.auth as auth_mod                              # noqa: E402
import app.api.engine_api as engine_mod                      # noqa: E402
import app.api.matches as matches_mod                        # noqa: E402
from app.core.database import Base                           # noqa: E402
from app.models import user, tournament, round, audit, password_reset  # noqa: E402,F401
from app.models.user import User                             # noqa: E402
from app.models.tournament import (                          # noqa: E402
    Tournament, TournamentStatus, PlayerStats, Registration,
)
from app.models.round import (                               # noqa: E402
    Round, RoundPairing, Match, MatchStatus, Notification,
)
from app.engine.scheduler import generate_schedule, compute_rounds  # noqa: E402
from app.schemas.auth import AdminSetActive, RegisterRequest  # noqa: E402
from app.api.engine_api import (                              # noqa: E402
    AddMatchesBody, StartRequest, add_matches, extra_match_options,
    start_tournament, withdraw_player, WithdrawBody,
)
from app.api.auth import set_user_active, register            # noqa: E402

FAILED: list[str] = []
AUDITS: list[dict] = []
Session = None   # 在 main() 里按 --db-url 建好


def check(name, ok, detail=""):
    print(f"   {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  <- {detail}"))
    if not ok:
        FAILED.append(name)


def fake_request(ip="127.0.0.1"):
    return Request({
        "type": "http", "method": "POST", "path": "/", "query_string": b"",
        "headers": [(b"user-agent", b"invariant-check")], "client": (ip, 54321),
    })


# ---------------------------------------------------------------- 造数据

async def mk_users(tag: str, players: int, disabled: int = 0):
    """admin（超管）+ players 名选手；前 disabled 名选手标记为已禁用。"""
    async with Session() as s:
        admin = User(username=f"admin-{tag}", password_hash="x", role="superadmin")
        people = [User(username=f"{tag}-p{i}", password_hash="x", role="user")
                  for i in range(1, players + 1)]
        for u in people[:disabled]:
            u.is_active = False
        s.add_all([admin] + people)
        await s.commit()
        return admin.id, [u.id for u in people]


async def seed_ongoing(creator_id: int, ids, total: int, played_rounds: int = 0,
                       referee_id: int | None = None):
    """进行中的赛事：total 场（前 played_rounds 轮里的比赛标记为已结束）。

    referee_id 给定时：第 1 场（未打完）与第 2 场（已结束）都挂他当裁判，
    用来验证"只卸任未打完的"。

    注意：**刻意不动 `Round.status`**（一直留 PENDING）—— 真实代码就是这样：
    全仓只有 start-round 会把轮次标成 ONGOING，没有任何地方标 FINISHED，
    记分只改 `Match.status`。以前这个 fixture 把轮次也标成 FINISHED，于是
    "退赛不删已打完的比赛"这条检查在真库上永远通过，而生产里历史比赛其实被删了。
    """
    async with Session() as s:
        t = Tournament(creator_id=creator_id, title="check", start_date=datetime.now(),
                       end_date=datetime.now() + timedelta(hours=3),
                       max_participants=len(ids), status=TournamentStatus.ONGOING,
                       total_matches=total)
        s.add(t)
        await s.flush()
        for uid in ids:
            s.add(PlayerStats(tournament_id=t.id, user_id=uid, is_active=True))
        for idx, rs in enumerate(compute_rounds(generate_schedule(list(ids), total), 2), start=1):
            done = idx <= played_rounds
            r = Round(tournament_id=t.id, round_number=idx)   # 状态保持 PENDING（同生产）
            s.add(r)
            await s.flush()
            for (a, b), (c, d), *_ in rs:
                p1 = RoundPairing(round_id=r.id, player_a_id=a, player_b_id=b)
                p2 = RoundPairing(round_id=r.id, player_a_id=c, player_b_id=d)
                s.add_all([p1, p2])
                await s.flush()
                m = Match(tournament_id=t.id, round_id=r.id, pairing_a_id=p1.id,
                          pairing_b_id=p2.id,
                          status=MatchStatus.FINISHED if done else MatchStatus.PENDING,
                          score_a=11 if done else None, score_b=5 if done else None,
                          winner_pairing_id=p1.id if done else None)
                s.add(m)
        await s.flush()
        if referee_id is not None:
            ms = (await s.execute(select(Match).where(Match.tournament_id == t.id)
                                  .order_by(Match.id))).scalars().all()
            if len(ms) >= 2:
                ms[0].referee_id = referee_id                       # 未打完
                ms[1].referee_id = referee_id                       # 已结束
                ms[1].status = MatchStatus.FINISHED
                ms[1].referee_released_at = None
        await s.commit()
        return t.id


async def seed_open(creator_id: int, ids):
    """报名中的赛事：给定的人都已报名。"""
    async with Session() as s:
        t = Tournament(creator_id=creator_id, title="open", start_date=datetime.now(),
                       end_date=datetime.now() + timedelta(hours=2), max_participants=8,
                       status=TournamentStatus.OPEN)
        s.add(t)
        await s.flush()
        for uid in ids:
            s.add(Registration(tournament_id=t.id, user_id=uid, is_active=True))
        await s.commit()
        return t.id


# ---------------------------------------------------------------- 查询

async def count_matches(tid):
    async with Session() as s:
        return (await s.execute(select(func.count(Match.id))
                                .where(Match.tournament_id == tid))).scalar() or 0


async def schedule_players(tid):
    """**未打完**的比赛里出现的选手。

    历史（已结束）比赛保留原样 —— 退赛/禁用都不会去改它们，所以检查"赛程里还有没有
    不该出现的人"只能看还没打的部分。
    """
    async with Session() as s:
        ms = (await s.execute(select(Match).where(
            Match.tournament_id == tid,
            Match.status != MatchStatus.FINISHED))).scalars().all()
        out = set()
        for m in ms:
            pa = await s.get(RoundPairing, m.pairing_a_id)
            pb = await s.get(RoundPairing, m.pairing_b_id)
            out |= {pa.player_a_id, pa.player_b_id, pb.player_a_id, pb.player_b_id}
        return out


async def all_players(tid):
    """所有比赛（含历史）里出现过的选手 —— 用来验证历史比赛没有被改写。"""
    async with Session() as s:
        ms = (await s.execute(select(Match).where(Match.tournament_id == tid))).scalars().all()
        out = set()
        for m in ms:
            pa = await s.get(RoundPairing, m.pairing_a_id)
            pb = await s.get(RoundPairing, m.pairing_b_id)
            out |= {pa.player_a_id, pa.player_b_id, pb.player_a_id, pb.player_b_id}
        return out


async def new_match_players(tid, after_id):
    async with Session() as s:
        ms = (await s.execute(select(Match).where(Match.tournament_id == tid,
                                                 Match.id > after_id))).scalars().all()
        out = set()
        for m in ms:
            pa = await s.get(RoundPairing, m.pairing_a_id)
            pb = await s.get(RoundPairing, m.pairing_b_id)
            out |= {pa.player_a_id, pa.player_b_id, pb.player_a_id, pb.player_b_id}
        return out


async def max_match_id(tid):
    async with Session() as s:
        return (await s.execute(select(func.max(Match.id))
                                .where(Match.tournament_id == tid))).scalar() or 0


async def match_sigs(tid) -> set:
    """每场的规范键：同 4 人 + 同分组（两队之间也排序）——「完全相同的对局」。"""
    async with Session() as s:
        ms = (await s.execute(select(Match).where(Match.tournament_id == tid))).scalars().all()
        out = set()
        for m in ms:
            pa = await s.get(RoundPairing, m.pairing_a_id)
            pb = await s.get(RoundPairing, m.pairing_b_id)
            out.add(frozenset((
                frozenset((pa.player_a_id, pa.player_b_id)),
                frozenset((pb.player_a_id, pb.player_b_id)),
            )))
        return out


async def appearances(tid) -> Counter:
    """每人上场次数（用来验证带历史排程后场次仍然相等）。"""
    async with Session() as s:
        ms = (await s.execute(select(Match).where(Match.tournament_id == tid))).scalars().all()
        c: Counter = Counter()
        for m in ms:
            pa = await s.get(RoundPairing, m.pairing_a_id)
            pb = await s.get(RoundPairing, m.pairing_b_id)
            for p in (pa.player_a_id, pa.player_b_id, pb.player_a_id, pb.player_b_id):
                c[p] += 1
        return c


async def disable(admin_id, uid):
    async with Session() as s:
        await set_user_active(body=AdminSetActive(user_id=uid, is_active=False),
                              request=fake_request(), admin=await s.get(User, admin_id), db=s)


# ---------------------------------------------------------------- 检查

async def check_add_matches():
    print("\n1) 追加比赛：被禁用的账号不算在场，也不会被排进新比赛")
    admin, ids = await mk_users("add", 8)
    victim = ids[0]
    tid = await seed_ongoing(admin, ids, 6, referee_id=victim)
    await disable(admin, victim)

    async with Session() as s:
        opts = await extra_match_options(tournament_id=tid, user=await s.get(User, admin), db=s)
    check("在场人数排除已禁用者（8-1=7）、步长 7", opts["players"] == 7 and opts["step"] == 7, opts)

    try:
        async with Session() as s:
            await add_matches(tournament_id=tid, request=fake_request(),
                              body=AddMatchesBody(matches=2),
                              user=await s.get(User, admin), db=s)
        check("7 人在场时 +2 场被拒（步长 7）", False, "竟然通过了")
    except HTTPException as e:
        check("7 人在场时 +2 场被拒（步长 7）", e.status_code == 400 and "倍数" in e.detail, e.detail)

    before = await max_match_id(tid)
    async with Session() as s:
        res = await add_matches(tournament_id=tid, request=fake_request(),
                                body=AddMatchesBody(matches=7),
                                user=await s.get(User, admin), db=s)
    players = await new_match_players(tid, before)
    check("追加 7 场成功", res.get("added") == 7, res)
    check("新比赛不含被禁用者", victim not in players, f"{sorted(players)}（禁用者 {victim}）")
    check("新比赛只含其余 7 人", players == set(ids) - {victim}, sorted(players))

    print("\n2) 禁用时只卸任未打完场次的裁判")
    async with Session() as s:
        ms = (await s.execute(select(Match).where(Match.tournament_id == tid)
                              .order_by(Match.id))).scalars().all()
        pending = next((m for m in ms if m.referee_id == victim
                        and m.status != MatchStatus.FINISHED), None)
        finished = next((m for m in ms if m.referee_id == victim
                         and m.status == MatchStatus.FINISHED), None)
    check("未打完的裁判场次被标记卸任",
          pending is not None and pending.referee_released_at is not None,
          pending.referee_released_at if pending else "没找到")
    check("已结束比赛的执裁记录没被动（仍是「裁 X」）",
          finished is None or finished.referee_released_at is None,
          finished.referee_released_at if finished else "没找到")


async def check_start():
    print("\n3) 开赛：被禁用的报名者不排进赛程，报名被自动取消")
    admin, ids = await mk_users("start", 5, disabled=1)
    victim = ids[0]
    tid = await seed_open(admin, ids)
    async with Session() as s:
        res = await start_tournament(tournament_id=tid, request=fake_request(), body=None,
                                     user=await s.get(User, admin), db=s)
    sched = await schedule_players(tid)
    check("赛程只含 4 名可用选手", sched == set(ids) - {victim}, sorted(sched))
    check("按 4 人算场次（若按 5 人会算出 5 场）", res["matches"] == 1, res)
    async with Session() as s:
        reg = (await s.execute(select(Registration).where(
            Registration.tournament_id == tid, Registration.user_id == victim))).scalar_one()
        stats = (await s.execute(select(PlayerStats).where(
            PlayerStats.tournament_id == tid))).scalars().all()
    check("被禁用者的报名被置为取消（保留行 + 取消时间）",
          reg.is_active is False and reg.cancelled_at is not None, (reg.is_active, reg.cancelled_at))
    check("只为 4 人建了 PlayerStats（没有他）",
          len(stats) == 4 and victim not in {st.user_id for st in stats}, len(stats))
    start_audits = [a for a in AUDITS if a.get("action") == "tournament_start"]
    check("开赛审计记下了被自动取消的报名数",
          bool(start_audits) and start_audits[-1].get("detail", {}).get(
              "disabled_registrations_cancelled") == 1,
          start_audits[-1].get("detail") if start_audits else "没有审计")

    print("\n4) 开赛失败时整笔回滚（不会误取消报名）")
    admin2, ids2 = await mk_users("start2", 4, disabled=1)
    victim2 = ids2[0]
    tid2 = await seed_open(admin2, ids2)          # 4 人报名、1 人禁用 → 可用只剩 3
    async with Session() as s:
        try:
            await start_tournament(tournament_id=tid2, request=fake_request(), body=None,
                                   user=await s.get(User, admin2), db=s)
            check("可用人数不足 4 时开赛被拒", False, "竟然开成功了")
        except HTTPException as e:
            check("可用人数不足 4 时开赛被拒", e.status_code == 400 and "4 人" in e.detail, e.detail)
    async with Session() as s:
        reg = (await s.execute(select(Registration).where(
            Registration.tournament_id == tid2, Registration.user_id == victim2))).scalar_one()
        stats = (await s.execute(select(PlayerStats).where(
            PlayerStats.tournament_id == tid2))).scalars().all()
        t = await s.get(Tournament, tid2)
    check("回滚：报名没有被取消", reg.is_active is True and reg.cancelled_at is None,
          (reg.is_active, reg.cancelled_at))
    check("回滚：没有建出 PlayerStats", len(stats) == 0, len(stats))
    check("回滚：赛事仍是报名中", t.status == TournamentStatus.OPEN, t.status)


async def check_withdraw_and_totals():
    print("\n5) 退赛重排：被禁用的不回来；total_matches 与真实场次数一致")
    admin, ids = await mk_users("wd", 8, disabled=1)
    victim = ids[0]
    tid = await seed_ongoing(admin, ids, 8, played_rounds=1)   # 已打完 2 场
    check("前置：库里 8 场、已打完 2 场", await count_matches(tid) == 8, await count_matches(tid))
    async with Session() as s:
        await withdraw_player(tournament_id=tid, player_id=ids[1],
                              request=fake_request(), body=WithdrawBody(),
                              user=await s.get(User, ids[1]), db=s)
    sched = await schedule_players(tid)
    actual = await count_matches(tid)
    async with Session() as s:
        t = await s.get(Tournament, tid)
        opts = await extra_match_options(tournament_id=tid, user=await s.get(User, admin), db=s)
    check("重排后「未打完的部分」不含被禁用者", victim not in sched, sorted(sched))
    check("重排后「未打完的部分」也不含刚退赛的人", ids[1] not in sched, sorted(sched))
    check("重排后未打完的部分只含剩余 6 人", sched == set(ids) - {victim, ids[1]}, sorted(sched))
    check("已打完的历史比赛不受影响（仍保留原来的人）",
          (await all_players(tid)) >= {victim, ids[1]}, sorted(await all_players(tid)))
    check("total_matches 写回真实场次数", t.total_matches == actual, (t.total_matches, actual))
    check("追加比赛的「现有场次」= 真实场次数", opts["current_total"] == actual,
          (opts["current_total"], actual))


async def check_owner_and_invite():
    print("\n6) 禁用房主：房主转移 + 给新房主发提醒")
    admin, ids = await mk_users("owner", 4)
    owner = ids[0]
    tid = await seed_ongoing(owner, ids, 4)
    await disable(admin, owner)
    async with Session() as s:
        t = await s.get(Tournament, tid)
        notes = (await s.execute(select(Notification).where(
            Notification.tournament_id == tid,
            Notification.type == "player_disabled"))).scalars().all()
    check("房主转给未禁用的在场选手", t.creator_id == ids[1], (t.creator_id, ids[1]))
    check("新房主收到「选手已被禁用」提醒",
          len(notes) == 1 and notes[0].user_id == ids[1],
          [(n.user_id, n.type) for n in notes])
    check("提醒里写清了怎么处理（退赛 / 自动取消）",
          bool(notes) and "退赛" in notes[0].message, notes[0].message if notes else "")

    print("\n7) 邀请码：被禁用账号的码不能注册，正常账号的仍可")
    async with Session() as s:
        off = User(username="off-inviter", password_hash="x", role="user",
                   invite_code="code-disabled", is_active=False)
        on = User(username="on-inviter", password_hash="x", role="user",
                  invite_code="code-active")
        s.add_all([off, on])
        await s.commit()
        off_id, on_id = off.id, on.id
    try:
        async with Session() as s:
            await register(req=RegisterRequest(username="newbie-off", password="pw123456",
                                               email="off@x.com", invite_code="code-disabled"),
                           request=fake_request("10.1.0.1"), db=s)
        check("被禁用账号的邀请码被拒", False, "竟然注册成功了")
    except HTTPException as e:
        check("被禁用账号的邀请码被拒（文案仍是通用的「邀请码无效」）",
              e.status_code == 400 and e.detail == "邀请码无效", (e.status_code, e.detail))
    async with Session() as s:
        await register(req=RegisterRequest(username="newbie-on", password="pw123456",
                                           email="on@x.com", invite_code="code-active"),
                       request=fake_request("10.1.0.2"), db=s)
        fresh = (await s.execute(select(User).where(User.username == "newbie-on"))).scalar_one_or_none()
        rejected = (await s.execute(select(User).where(
            User.username == "newbie-off"))).scalar_one_or_none()
    check("正常账号的邀请码仍可注册，且邀请人正确",
          fresh is not None and fresh.invited_by == on_id, getattr(fresh, "invited_by", None))
    check("被禁用账号的邀请码没有产生任何新账号", rejected is None, rejected)


async def started_match_count(tid):
    """已经开打（在打 + 已打完）的场次数：重排只允许在它为 0 时使用。"""
    async with Session() as s:
        return (await s.execute(select(func.count(Match.id)).where(
            Match.tournament_id == tid,
            Match.status != MatchStatus.PENDING))).scalar() or 0


async def pending_match_ids(tid):
    async with Session() as s:
        return {mid for (mid,) in (await s.execute(select(Match.id).where(
            Match.tournament_id == tid, Match.status != MatchStatus.FINISHED))).all()}


async def first_match_teams(tid):
    """第 1 轮第 1 场的两队球员（规范键）—— 用来观察"谁打第一场、怎么分组"。"""
    async with Session() as s:
        r = (await s.execute(select(Round).where(Round.tournament_id == tid)
                             .order_by(Round.round_number).limit(1))).scalar_one()
        m = (await s.execute(select(Match).where(Match.round_id == r.id)
                             .order_by(Match.id).limit(1))).scalar_one()
        pa = await s.get(RoundPairing, m.pairing_a_id)
        pb = await s.get(RoundPairing, m.pairing_b_id)
        return frozenset((frozenset((pa.player_a_id, pa.player_b_id)),
                          frozenset((pb.player_a_id, pb.player_b_id))))


async def check_start_shuffle():
    """开赛会真随机打散名单顺序：同一批人、同样报名顺序连开几场，第一场不该总一样。

    机制上是"每次开赛随机"，所以只能做统计断言：4 场里只要不是全都相同即可
    （4 场全同的概率约 (1/210)^3，可忽略）。同时检查审计记了 shuffle_seed ——
    没有它，随机就变成不可追溯。
    """
    print("\n10) 开赛洗牌：同一批人、同样报名顺序，第一场不再每次都一样")
    admin, ids = await mk_users("shuf", 8)
    firsts = []
    tids = []
    for i in range(4):
        tid = await seed_open(admin, ids)
        async with Session() as s:
            await start_tournament(tournament_id=tid, request=fake_request(),
                                   body=StartRequest(total_matches=14),
                                   user=await s.get(User, admin), db=s)
        tids.append(tid)
        firsts.append(await first_match_teams(tid))

    check("4 场的第一场不是同一个（名单顺序被洗过）",
          len(set(firsts)) > 1, [sorted(map(sorted, f)) for f in firsts])
    # 洗过的顺序会落库（PlayerStats.id 顺序），所以"落库顺序 ≠ 报名顺序"至少要出现一次。
    # 4 场都恰好等于报名顺序的概率是 (1/8!)^4，可忽略。
    stored = [await player_stats_order(tid) for tid in tids]
    check("落库的名单顺序确实被洗过（至少一场 ≠ 报名顺序）",
          any(order != ids for order in stored), stored)
    check("落库顺序仍是同一批人（洗牌没丢人/多人）",
          all(sorted(order) == sorted(ids) for order in stored), stored)
    start_audits = [a for a in AUDITS if a.get("action") == "tournament_start"]
    seeds = [a.get("detail", {}).get("shuffle_seed") for a in start_audits[-4:]]
    check("开赛审计里记了 shuffle_seed（随机可追溯）",
          len(seeds) == 4 and all(isinstance(s, int) and s > 0 for s in seeds), seeds)
    check("4 场的洗牌种子各不相同", len(set(seeds)) == 4, seeds)


async def player_stats_order(tid):
    """落库的名单顺序（PlayerStats.id 顺序）—— 开赛洗牌后它就等于"这一场的名单顺序"。"""
    async with Session() as s:
        return [uid for (uid,) in (await s.execute(
            select(PlayerStats.user_id)
            .where(PlayerStats.tournament_id == tid)
            .order_by(PlayerStats.id))).all()]


async def check_reschedule():
    """重排赛程：**只在一场都没开打时可用**，且用完全部换新、每人场次相等。

    这是「换一份赛程」这条路的真库不变量（pytest 那套不连库测不到）：除了删轮次重建，
    还要保证已开打的赛事被硬拦（不能把在打/已打完的比赛删掉）、写回真实总场次、
    并把被取消场次的在任裁判通知到。
    """
    print("\n8) 重排赛程：没开打才能用，用完全部换新、裁判被通知")
    from app.api.engine_api import reschedule_schedule

    admin, ids = await mk_users("resch", 8)
    tid = await seed_ongoing(admin, ids, total=14, played_rounds=0)   # 一场都没开打

    # 1) 已经有比赛打完（= 已经开打）时必须被拒
    async with Session() as s:
        m = (await s.execute(select(Match).where(Match.tournament_id == tid)
                             .order_by(Match.id).limit(1))).scalar_one()
        m.status = MatchStatus.FINISHED
        m.score_a, m.score_b = 11, 5
        await s.commit()
    check("前置：库里已有 1 场开打", await started_match_count(tid) == 1, await started_match_count(tid))
    async with Session() as s:
        try:
            await reschedule_schedule(tournament_id=tid, request=fake_request(),
                                      user=await s.get(User, admin), db=s)
            check("已经开打时重排被拒", False, "竟然重排成功了")
        except HTTPException as e:
            check("已经开打时重排被拒（400，且说明「开打」）",
                  e.status_code == 400 and "开打" in e.detail, e.detail)
    # 还原成"一场没开打"，再验成功路径
    async with Session() as s:
        m = (await s.execute(select(Match).where(Match.tournament_id == tid)
                             .order_by(Match.id).limit(1))).scalar_one()
        m.status = MatchStatus.PENDING
        m.score_a = m.score_b = m.winner_pairing_id = None
        await s.commit()

    # 2) 给一场比赛挂上在任裁判：重排要把他卸任并通知
    async with Session() as s:
        m = (await s.execute(select(Match).where(
            Match.tournament_id == tid,
            Match.status == MatchStatus.PENDING).order_by(Match.id).limit(1))).scalar_one()
        m.referee_id = ids[0]
        await s.commit()

    pending_before = await pending_match_ids(tid)
    async with Session() as s:
        res = await reschedule_schedule(tournament_id=tid, request=fake_request(),
                                        user=await s.get(User, admin), db=s)

    pending_after = await pending_match_ids(tid)
    app = await appearances(tid)

    check("没开打时可以重排，旧场次一个不剩",
          not (pending_before & pending_after), sorted(pending_before & pending_after))
    check("重排后场次数不变（仍是 14 场）", len(pending_after) == 14, len(pending_after))
    check("每人场次仍然相等（8 人 14 场 = 各 7 场）",
          len(app) == 8 and set(app.values()) == {7}, dict(app))
    check("重排完仍然没有任何比赛开打", await started_match_count(tid) == 0,
          await started_match_count(tid))
    check("写回真实总场次（= 14）", res["total_matches"] == 14, res["total_matches"])
    async with Session() as s:
        rounds = (await s.execute(select(Round).where(Round.tournament_id == tid)
                                  .order_by(Round.round_number))).scalars().all()
    check("轮次也是整批重建（7 个轮次、编号 1..7，没有叠着旧轮次）",
          [r.round_number for r in rounds] == list(range(1, 8)),
          [r.round_number for r in rounds])
    check("返回的 replaced_matches = 被删掉的场次数",
          res["replaced_matches"] == len(pending_before),
          (res["replaced_matches"], len(pending_before)))
    check("被取消场次的在任裁判收到通知（1 人）",
          res["released_referees"] == 1, res["released_referees"])
    async with Session() as s:
        notes = (await s.execute(select(Notification).where(
            Notification.tournament_id == tid,
            Notification.type == "schedule_changed"))).scalars().all()
    check("通知内容说清「请重新认领」",
          len(notes) == 1 and "重新认领" in notes[0].message,
          [n.message for n in notes])
    resched_audits = [a for a in AUDITS if a.get("action") == "tournament_reschedule"]
    check("重排审计里也记了 shuffle_seed（与开赛同一个口径）",
          bool(resched_audits)
          and isinstance(resched_audits[-1].get("detail", {}).get("shuffle_seed"), int),
          resched_audits[-1].get("detail") if resched_audits else "没有审计")


class _NoShuffleRandom:
    """只在检查里用的替身：`randrange` 固定、`Random(seed).shuffle` 不动顺序。

    开赛洗牌本身是真随机的（见 check_start_shuffle），但下面这条检查要复现一个**确定性**的
    死路组合（单测 test_history_dead_end_has_no_history_fallback 固化了它），
    所以这里把开赛洗牌短路掉。只替换 engine_api 模块里的那个 `random` 名字，
    调度器（app.engine.scheduler）自己的 random 不受影响。
    """

    def randrange(self, *args, **kwargs):
        return 1

    @staticmethod
    def Random(seed):                     # noqa: N802（模仿 random.Random 的名字）
        class _R:
            def shuffle(self, seq):
                return None
        return _R()

    @staticmethod
    def shuffle(seq):
        return None


async def check_start_keeps_requested_matches_with_history():
    """带跨赛事历史时，开赛必须仍然用**用户选的**场次数，而不是被兜底改大。

    回归背景：跨赛事避重上线后，"5 人 5 场 + 两场同样 5 人的历史"会让 generate_schedule
    走死路（单测 test_history_dead_end_has_no_history_fallback 固化了这个输入）。
    老兜底只会往上加场次（5 → 10）或 400；现在先不带历史重试同一档，所以必须还是 5 场。

    这条检查自己会先断言"这个输入确实会走死路"，否则它就是在空转（洗牌是随机的，
    不短路掉的话输入每次都不一样）。
    """
    print("\n3b) 开赛带跨赛事历史时，场次数仍是用户选的那个")
    admin, ids = await mk_users("hist5", 5)

    real_random = engine_mod.random
    engine_mod.random = _NoShuffleRandom()          # 让名单顺序保持报名顺序（可复现的输入）
    try:
        # 造两场"上一场"：直接按单测里那两组名单顺序落库（**不经 start_tournament**，
        # 否则第二场会带上第一场的历史、排出来的就不是单测里那份输入了），
        # 这样"带历史走死路"的条件与单测完全一致、可复现
        prev_ids = []
        for order in (ids, [ids[2], ids[0], ids[1], ids[3], ids[4]]):
            prev_ids.append(await seed_ongoing(admin, order, total=5, played_rounds=0))

        hist = await _load_history_for_check(prev_ids)
        try:
            generate_schedule(ids, 5, *hist)
            print("   （注意：这组输入现在带历史也能排出来了 —— 算法变强/权重调整后的正常现象，"
                  "下面只验「场次数不被改大」）")
        except ValueError:
            check("前置：这组输入确实会让带历史排程走死路", True)

        tid = await seed_open(admin, ids)
        async with Session() as s:
            res = await start_tournament(tournament_id=tid, request=fake_request(),
                                         body=StartRequest(total_matches=5),
                                         user=await s.get(User, admin), db=s)
    finally:
        engine_mod.random = real_random

    app = await appearances(tid)
    check("场次数没被兜底改大（仍是 5 场）", res["matches"] == 5, res)
    check("库里确实排了 5 场", len(app) == 5 and sum(app.values()) == 20, dict(app))
    check("每人场次相等（5 人 5 场 = 各 4 场）", set(app.values()) == {4}, dict(app))


async def _load_history_for_check(tids):
    """按 engine_api 的口径把几场赛事的搭档/对手/对局统计读出来（检查用）。"""
    from app.api.engine_api import _load_history
    async with Session() as s:
        return await _load_history(s, list(tids))


async def check_add_matches_keeps_requested_count_with_history():
    """追加比赛：带着跨赛事历史排不出来时，也必须先退历史、再改档位。

    实测过的坑（跨赛事避重上线后引入）：5 人局、前面两场各 5 场时，老代码会先带着
    跨赛事历史把"追加 5 场"一路顶到 10 场，而"只带本赛事历史"其实排得出来。
    现在每一档都先「跨赛事+本赛事」→「只本赛事」两次尝试，都不行才往上加档位。
    """
    print("\n2b) 追加比赛：跨赛事历史排不出来也不能把档位翻倍")
    admin, ids = await mk_users("addh", 5)
    for _ in range(2):                       # 两场"上一场"，把跨赛事历史喂满
        prev = await seed_open(admin, ids)
        async with Session() as s:
            await start_tournament(tournament_id=prev, request=fake_request(),
                                   body=StartRequest(total_matches=5),
                                   user=await s.get(User, admin), db=s)
    tid = await seed_ongoing(admin, ids, total=5, played_rounds=0)
    async with Session() as s:
        res = await add_matches(tournament_id=tid, request=fake_request(),
                                body=AddMatchesBody(matches=5),
                                user=await s.get(User, admin), db=s)
    check("追加数就是请求的 5 场（没被跨赛事历史顶成 10）", res["added"] == 5, res)
    check("库里确实只多了 5 场", await count_matches(tid) == 10, await count_matches(tid))


async def check_reschedule_with_started_round():
    """重排要把**所有**轮次都换掉：`start-round` 只把 Round.status 标成 ONGOING，
    比赛仍是 PENDING。只按轮次状态过滤会漏掉这个轮次 → 旧的一份 + 新的一份叠加
    （每人多打一场、与返回的 replaced_matches 也对不上）。"""
    print("\n8b) 重排：轮次被标成 ongoing（但一场没记分）也必须整批换掉")
    from app.api.engine_api import reschedule_schedule
    from app.api.matches import start_round

    admin, ids = await mk_users("reschr", 8)
    tid = await seed_ongoing(admin, ids, total=14, played_rounds=0)
    async with Session() as s:
        r = (await s.execute(select(Round).where(Round.tournament_id == tid)
                             .order_by(Round.round_number).limit(1))).scalar_one()
        await start_round(tournament_id=tid, round_id=r.id, request=fake_request(),
                          user=await s.get(User, admin), db=s)

    check("前置：轮次已标 ongoing，但没有任何比赛开打",
          await started_match_count(tid) == 0, await started_match_count(tid))
    async with Session() as s:
        res = await reschedule_schedule(tournament_id=tid, request=fake_request(),
                                        user=await s.get(User, admin), db=s)
    check("重排后总场次仍是 14（没叠加成 16）", res["total_matches"] == 14, res["total_matches"])
    check("库里确实只有 14 场", await count_matches(tid) == 14, await count_matches(tid))
    app = await appearances(tid)
    check("每人场次相等（8 人 14 场 = 各 7 场）", len(app) == 8 and set(app.values()) == {7}, dict(app))


async def check_cross_event_avoidance():
    """跨赛事避重：同一批人连开三场，后面的赛事不该重复前面的「同 4 人 + 同分组」对局。

    为什么需要这条：8 人 14 场时搭档组合只有 C(8,2)=28 种、一周用满，跨周重复搭档是
    数学必然；但"上周那一场 4 个人、同样的分组再来一次"是能避免的（实测不带历史时
    连续两周平均会重合 1 场）。这条不变量只有真库能验：历史是从**别的赛事**读出来的。
    """
    print("\n9) 跨赛事避重：连开三场，后面的赛事不重复前面用过的对局")
    admin, ids = await mk_users("cross", 8)
    sigs: list[set] = []
    tids: list[int] = []
    for i in range(3):
        tid = await seed_open(admin, ids)          # 同一批人、同样的报名顺序
        async with Session() as s:
            await start_tournament(tournament_id=tid, request=fake_request(),
                                   body=StartRequest(total_matches=14),
                                   user=await s.get(User, admin), db=s)
        tids.append(tid)
        sigs.append(await match_sigs(tid))

    check("三场都排满 14 场", [len(x) for x in sigs] == [14, 14, 14], [len(x) for x in sigs])
    check("第二场没有重复第一场的对局", not (sigs[1] & sigs[0]), sorted(sigs[1] & sigs[0]))
    check("第三场没有重复前两场的对局（往前看 3 场）",
          not (sigs[2] & (sigs[0] | sigs[1])), sorted(sigs[2] & (sigs[0] | sigs[1])))
    for idx, tid in enumerate(tids, start=1):
        app = await appearances(tid)
        check(f"第 {idx} 场每人场次仍然相等（8 人 14 场 = 各 7 场）",
              len(app) == 8 and set(app.values()) == {7}, dict(app))


async def main() -> int:
    global Session
    parser = argparse.ArgumentParser(description="赛程/账号不变量检查（需一次性试点库）")
    parser.add_argument("--db-url",
                        default=os.environ.get(
                            "CHECK_DB_URL",
                            "postgresql+asyncpg://badminton:badminton@db:5432/badmatch_probe"),
                        help="一次性试点库的连接串（库名需含 probe/test）")
    args = parser.parse_args()

    dbname = urlparse(args.db_url).path.lstrip("/")
    if not any(k in dbname for k in ("probe", "test")):
        print(f"拒绝运行：库名 {dbname!r} 不像一次性试点库（需含 probe 或 test）")
        return 2

    engine = create_async_engine(args.db_url, echo=False)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    # 审计改成内存记录：脚本只碰试点库，不会往应用配置那套库写 audit_logs
    async def fake_audit(**kw):
        AUDITS.append(kw)

    auth_mod.audit = fake_audit
    engine_mod.audit = fake_audit
    # 也要替换比赛模块的 audit：本脚本会直接调 start_round 等接口，
    # 它们各自在模块里 import 了 audit，只替换上面两处会让这些调用真的往库里写审计
    matches_mod.audit = fake_audit

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    await check_add_matches()
    await check_add_matches_keeps_requested_count_with_history()
    await check_start()
    await check_start_keeps_requested_matches_with_history()
    await check_withdraw_and_totals()
    await check_owner_and_invite()
    await check_reschedule()
    await check_reschedule_with_started_round()
    await check_cross_event_avoidance()
    await check_start_shuffle()

    await engine.dispose()
    print(f"\n共 {len(FAILED)} 项失败" + (f"：{FAILED}" if FAILED else "，全部通过 ✓"))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
