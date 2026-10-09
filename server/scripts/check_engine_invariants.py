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
    AddMatchesBody, add_matches, extra_match_options, start_tournament,
    withdraw_player, WithdrawBody,
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
    """进行中的赛事：total 场（前 played_rounds 轮标记为已结束）。

    referee_id 给定时：第 1 场（未打完）与第 2 场（已结束）都挂他当裁判，
    用来验证"只卸任未打完的"。
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
            r = Round(tournament_id=t.id, round_number=idx,
                      status=MatchStatus.FINISHED if done else MatchStatus.PENDING)
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

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    await check_add_matches()
    await check_start()
    await check_withdraw_and_totals()
    await check_owner_and_invite()

    await engine.dispose()
    print(f"\n共 {len(FAILED)} 项失败" + (f"：{FAILED}" if FAILED else "，全部通过 ✓"))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
