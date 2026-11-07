"""造一份「12 人 · 2 片场地 · 已报名未开赛」的测试数据（本地 dev 用）。

用法（仓库根目录，dev 栈已启动）：

    docker compose -f docker-compose.dev.yml exec -T server python scripts/seed_multi_court.py

做了什么：
- 建（或复用）12 个测试选手 `测试1`~`测试12`，密码统一 `seed1234`（想登进去看也能登）；
- 删掉上一次用同一标题造的赛事（连同它的轮次/比赛/报名/场地），再重新造一份干净的；
- 新赛事：状态**报名中**、最大人数 12、总场次 **36**（12 人 2 片场地的最优档：
  每人 12 场，搭档/对手重复都贴着理论下限）、2 片场地（1号场/2号场，19:00-21:00）、
  12 条有效报名。

为什么总场次选 36：两片场地每轮 8 人上场、4 人轮空，36 场 = 18 轮，每人正好 12 场、
每人轮空 6 次。33 场也能排，但搭档重复会多 6 次（实测）。
"""
import asyncio
import os
import sys
from datetime import datetime, time, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import delete, select                             # noqa: E402

from app.core.database import async_session_factory                # noqa: E402
from app.core.security import hash_password                        # noqa: E402
from app.models.round import (                                     # noqa: E402
    Match, MatchSupport, Notification, Round, RoundPairing,
)
from app.models.tournament import (                                # noqa: E402
    Court, PlayerStats, Registration, TimeSlot, Tournament, TournamentStatus,
)
from app.models.user import User                                   # noqa: E402

TITLE = "多场地测试 · 12人2场地"
PLAYER_NAMES = ["测试1", "测试2", "测试3", "测试4", "测试5", "测试6",
                "测试7", "测试8", "测试9", "测试10", "测试11", "测试12"]
SEED_PASSWORD = "seed1234"
COURTS = 2                  # 场地数 = 同一时刻开几场
TOTAL_MATCHES = 36          # 12 人 2 片场地：每人 12 场、轮空 6 次，搭档/对手重复贴着下限
PLAYERS_PER_COURT = 5       # 与 scheduler.PLAYERS_PER_COURT 同口径（4 上场 + 1 场下执裁）


async def _get_or_create(db, model, defaults: dict, **where):
    row = (await db.execute(select(model).filter_by(**where))).scalar_one_or_none()
    if row:
        return row, False
    row = model(**where, **defaults)
    db.add(row)
    await db.flush()
    return row, True


async def _drop_previous(db, creator_id: int) -> int:
    """删掉上一次用同一标题造的赛事（连带轮次/比赛/报名/场地/战绩）。"""
    rows = (await db.execute(
        select(Tournament.id).where(Tournament.title == TITLE,
                                    Tournament.creator_id == creator_id)
    )).scalars().all()
    for tid in rows:
        round_ids = (await db.execute(
            select(Round.id).where(Round.tournament_id == tid)
        )).scalars().all()
        match_ids = (await db.execute(
            select(Match.id).where(Match.tournament_id == tid)
        )).scalars().all()
        if match_ids:
            await db.execute(delete(MatchSupport).where(MatchSupport.match_id.in_(match_ids)))
            await db.execute(delete(Match).where(Match.id.in_(match_ids)))
        if round_ids:
            await db.execute(delete(RoundPairing).where(RoundPairing.round_id.in_(round_ids)))
            await db.execute(delete(Round).where(Round.id.in_(round_ids)))
        court_ids = (await db.execute(
            select(Court.id).where(Court.tournament_id == tid)
        )).scalars().all()
        if court_ids:
            await db.execute(delete(TimeSlot).where(TimeSlot.court_id.in_(court_ids)))
            await db.execute(delete(Court).where(Court.id.in_(court_ids)))
        await db.execute(delete(Registration).where(Registration.tournament_id == tid))
        await db.execute(delete(PlayerStats).where(PlayerStats.tournament_id == tid))
        await db.execute(delete(Notification).where(Notification.tournament_id == tid))
        await db.execute(delete(Tournament).where(Tournament.id == tid))
    return len(rows)


async def main_async():
    async with async_session_factory() as db:
        # 房主：优先用已有的超管/管理员，这样界面上的「开始比赛」按钮直接可点
        boss = (await db.execute(
            select(User).where(User.role.in_(("superadmin", "admin")),
                               User.is_active == True).order_by(User.id)
        )).scalars().first()
        if boss is None:
            boss, _ = await _get_or_create(
                db, User,
                {"password_hash": hash_password(SEED_PASSWORD), "role": "superadmin",
                 "is_active": True, "email": "seed_admin@example.com"},
                username="seed_admin",
            )
            print(f"没有超管/管理员账号，已创建 {boss.username} / 密码 {SEED_PASSWORD}")

        removed = await _drop_previous(db, boss.id)
        if removed:
            print(f"已清理上一次造的 {removed} 个同标题赛事")

        players = []
        for i, name in enumerate(PLAYER_NAMES, start=1):
            p, created = await _get_or_create(
                db, User,
                {"password_hash": hash_password(SEED_PASSWORD), "role": "user",
                 "is_active": True, "email": f"seed{i}@example.com", "invited_by": boss.id},
                username=name,
            )
            players.append(p)
        print(f"选手：{len(players)} 人（{'新建若干' if True else ''}）")

        # 明天 19:00-21:00（naive 本地时间，与库里 TIMESTAMP 列一致）
        day = (datetime.now() + timedelta(days=1)).date()
        start_dt = datetime.combine(day, time(19, 0))
        end_dt = datetime.combine(day, time(21, 0))

        assert len(players) >= PLAYERS_PER_COURT * 2, "12 人 2 片场地需要至少 10 人"

        t = Tournament(
            creator_id=boss.id,
            title=TITLE,
            description=("测试用：12 人 2 片场地同时开打。开赛后每轮 8 人上场、4 人轮空，"
                         "每轮 2 场，裁判只从轮空的人里挑。"),
            location="奥体中心",
            start_date=start_dt,
            end_date=end_dt,
            max_participants=len(players),
            total_matches=TOTAL_MATCHES,
            plan_total_matches=TOTAL_MATCHES,
            points_to_win=11,
            status=TournamentStatus.OPEN,
        )
        db.add(t)
        await db.flush()

        # 正好 2 片场地（数量 = 同一时刻开几场；每片至少 5 人才能开赛）
        for i in range(COURTS):
            db.add(Court(tournament_id=t.id, name=f"{i + 1}号场", sort_order=i))
        await db.flush()
        court_ids = (await db.execute(
            select(Court.id).where(Court.tournament_id == t.id).order_by(Court.sort_order)
        )).scalars().all()
        for cid in court_ids:
            db.add(TimeSlot(court_id=cid, start_time=time(19, 0), end_time=time(21, 0)))

        for p in players:
            db.add(Registration(tournament_id=t.id, user_id=p.id, is_active=True))

        await db.commit()

        print("\n=== 造好了 ===")
        print(f"赛事 id      ：{t.id}")
        print(f"标题         ：{TITLE}")
        print(f"状态         ：报名中（未开赛）")
        print(f"房主         ：{boss.username}（{boss.role}，用这个账号登录就能点「开始比赛」）")
        print(f"报名         ：{len(players)}/{t.max_participants} 人")
        print(f"场地         ：2 片（1号场、2号场 19:00-21:00）")
        print(f"总场次       ：{TOTAL_MATCHES} 场（每人 12 场；开赛若提示档位不对，按 36 选）")
        print(f"\n打开：http://localhost:5173/tournament/{t.id}")
        print("然后点「开始比赛」→ 总场次选 36 → 开赛。")
        print("开赛后看：对阵表顶部应出现「本轮轮空：…」；记分页给同一轮两场记分都该放行；")
        print("给下一轮里与进行中那场共用选手的比赛记分，会弹「这场还不能开打」（可强制开打）。")


if __name__ == "__main__":
    asyncio.run(main_async())
