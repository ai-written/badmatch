#!/usr/bin/env python3
"""
存量赛事的「左右翻边」脚本（一次性，需要数据库）。

背景：排程器在打分并列时总是取「先枚举到的那一组」，而 `_splits` 的第一种分法把组合
首位的人放进 A 队 —— 名单第一人（报名最早的）于是**在自己参加的每一场里都站左边**。
新代码已经在落库前对每一场随机翻边（`app/engine/scheduler.shuffle_sides`），
但那只管**新落库**的场次；已经开起来的老赛事，库里 `pairing_a/pairing_b` 还是旧的偏置。
本脚本就地把它翻过来。

用法（生产）：

    # 1) 先空跑：只打印会翻哪些场次，不写库、不加锁
    docker compose -f docker-compose.prod.yml exec server \
      python scripts/randomize_match_sides.py --tournament-id 12

    # 2) 只翻「还没开打」的场次（默认范围，安全）
    docker compose -f docker-compose.prod.yml exec server \
      python scripts/randomize_match_sides.py --tournament-id 12 --apply

    # 3) 连已开打 / 已结束的场次一起翻（比分会随队交换）
    docker compose -f docker-compose.prod.yml exec server \
      python scripts/randomize_match_sides.py --tournament-id 12 --apply --include-started

    # 已结束的赛事属于归档（产品规则「赛事结束=完全只读」），要动它必须再显式加 --yes-archived
    docker compose -f docker-compose.prod.yml exec server \
      python scripts/randomize_match_sides.py --tournament-id 12 --apply --include-started --yes-archived

要点：

- **并发安全（--apply 时加行锁）**：先对赛事行 `FOR UPDATE`，再对命中的比赛行
  `FOR UPDATE` —— 与应用侧同一加锁顺序（先赛事、后比赛，见 matches.load_writable_tournament）。
  于是它与「记分 / 交换场地 / 认领裁判 / 应援」（赛事 FOR SHARE + 比赛 FOR UPDATE）、
  「追加比赛 / 退赛重排 / 结束赛事」（赛事 FOR UPDATE）互斥：不会出现脚本读到
  `score_a=None`、裁判正好点下第一分、脚本再把比分覆盖回 NULL 这种静默丢分。
  代价是这个赛事的写操作会被短暂挡住（≤100 场，毫秒级），挑没人打球的空档跑更稳。
- **dry-run 是纯只读**：不加锁、不执行任何写语句。
- **幂等**：某一场「谁算 A 队」由 `--seed`（默认赛事 id）+ `match.id` 决定，与当前
  状态无关；重复执行结果一样（第二次会显示「需要调整：0 场」）。想换一种随机分布就换
  `--seed`；`--seed random` 每次不同（不可复现，仅用于随便翻一次）。
- 翻一场时同步交换三样东西，少一样都会写出错数据：
  1. `pairing_a_id ↔ pairing_b_id`（`winner_pairing_id` 指向 pair 行，不用动）
  2. `score_a ↔ score_b`（这两列就是「pairing_a 的得分 / pairing_b 的得分」）
  3. `match_supports.side` 的 `'a' ↔ 'b'`（应援票按边存；不换的话"我给 A 队投的票"
     会瞬间记到对面头上）
- **`is_swapped` 不动**：那是裁判在记分页显式按下的「交换场地」，属于现场操作，
  脚本不该覆盖。注意对阵表（ScheduleView）本来就不读这个字段，所以对阵表一定会变；
  记分页对 `is_swapped=true` 的场次显示不变（存储顺序与标志同时生效，视觉相抵）。
- 每场被改动的记录写一条审计（`action=match_sides_randomized`，匿名 user）。
- 脚本与 Web 服务**不是同一个进程**，无法广播 WebSocket：跑完让在场的人下拉刷新。
"""
import argparse
import asyncio
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import case, select, update  # noqa: E402

from app.core.audit import audit  # noqa: E402
from app.core.database import async_session_factory, engine  # noqa: E402

# 独立脚本必须把这些模型模块**全部导入**：SQLAlchemy 的关系是按类名字符串解析的
# （Match.referee -> "User"、Tournament.creator -> "User"、Round.tournament -> "Tournament"…），
# 只导入"用到的表"，第一次 execute 配置 mapper 时就会报
#   InvalidRequestError: expression 'User' failed to locate a name
# （应用侧不会有这个问题：main.py 把 routers 全导进来，模型顺带全注册了）。
# 用 `import app.models.x` 而非 `from app.models import x`，避免 `audit` 模块名
# 覆盖上面这个 core.audit 的 audit 函数。
import app.models.user        # noqa: E402,F401
import app.models.tournament  # noqa: E402,F401
import app.models.round       # noqa: E402,F401

from app.models.round import (  # noqa: E402
    Match, MatchStatus, MatchSupport, RoundPairing,
)
from app.models.tournament import Tournament, TournamentStatus  # noqa: E402


def left_pairing_of(seed: str, match_id: int, a_id: int, b_id: int) -> int:
    """这一场「谁算 A 队（左侧）」。

    只依赖 (seed, match_id, 两个 pairing id)，与库里当前状态无关 —— 因此重复执行
    是幂等的（翻过一次后再跑，want_left 会等于当前的 pairing_a_id，直接跳过）。
    锚点用两个 id 的较小者，是因为它稳定；它本身没有业务含义。
    """
    low, high = (a_id, b_id) if a_id < b_id else (b_id, a_id)
    return low if random.Random(f"{seed}:{match_id}").getrandbits(1) == 0 else high


async def run(args: argparse.Namespace) -> int:
    seed = args.seed or f"t{args.tournament_id}"
    if seed == "random":
        seed = random.SystemRandom().randrange(1 << 30)

    async with async_session_factory() as db:
        t_stmt = select(Tournament).where(Tournament.id == args.tournament_id)
        # 与应用同一加锁顺序：先赛事、后比赛（见 matches.load_writable_tournament 的说明）。
        # dry-run 不加锁：它不写任何东西，没必要挡住正在记分的人。
        if args.apply:
            t_stmt = t_stmt.with_for_update()
        t = (await db.execute(t_stmt)).scalar_one_or_none()
        if t is None:
            print(f"赛事 {args.tournament_id} 不存在")
            return 2

        if (args.include_started and t.status == TournamentStatus.FINISHED
                and not args.yes_archived):
            print(f"赛事 #{t.id}「{t.title}」已结束（归档、产品规则是完全只读）。"
                  f"确定要改就加 --yes-archived。")
            return 2

        m_stmt = (
            select(Match)
            .where(Match.tournament_id == args.tournament_id)
            .order_by(Match.id)
        )
        if not args.include_started:
            m_stmt = m_stmt.where(Match.status == MatchStatus.PENDING)
        if args.apply:
            m_stmt = m_stmt.with_for_update()
        matches = (await db.execute(m_stmt)).scalars().all()

        scope = "全部场次（含已开打/已结束）" if args.include_started else "仅未开打场次"
        print(f"赛事 #{t.id}「{t.title}」状态={t.status.value}")
        print(f"范围：{scope}；命中 {len(matches)} 场；seed={seed}"
              f"{'；已加行锁' if args.apply else '；dry-run 只读'}")
        if not matches:
            print("没有符合条件的场次，什么都没做")
            return 0

        # 先算「要改哪些」，dry-run 与 apply 共用这段（dry-run 只算不写）
        plan = []
        for m in matches:
            want_left = left_pairing_of(seed, m.id, m.pairing_a_id, m.pairing_b_id)
            if m.pairing_a_id != want_left:
                plan.append((m, want_left,
                             (m.pairing_a_id, m.pairing_b_id, m.score_a, m.score_b)))

        print(f"需要调整：{len(plan)} 场")
        if plan:
            ids = {pid for m, _wl, b in plan
                   for pid in (m.pairing_a_id, m.pairing_b_id, b[0], b[1])}
            rows = (await db.execute(
                select(RoundPairing).where(RoundPairing.id.in_(ids))
            )).scalars().all()
            name = {p.id: (p.player_a_id, p.player_b_id) for p in rows}
            for m, want_left, before in plan[:12]:
                print(f"  match#{m.id} status={m.status.value} "
                      f"左队 {name.get(want_left, '?')} (原 {name.get(before[0], '?')}) "
                      f"比分列 {before[2]}:{before[3]} -> {before[3]}:{before[2]}"
                      f"（列随队交换，队伍→得分不变）")
            if len(plan) > 12:
                print(f"  …另有 {len(plan) - 12} 场")
            print(f"  match_ids={[m.id for m, _wl, _b in plan]}")

        if not args.apply:
            await db.rollback()
            print("\n[dry-run] 未写库（也没执行写语句）。确认无误后加 --apply 真正执行。")
            return 0

        for m, _wl, _before in plan:
            m.pairing_a_id, m.pairing_b_id = m.pairing_b_id, m.pairing_a_id
            # score_a/score_b 就是「pairing_a 的得分 / pairing_b 的得分」，必须跟着换；
            # winner_pairing_id 指向 pair 行，不受影响
            m.score_a, m.score_b = m.score_b, m.score_a
            # 一条 SQL 里完成 a↔b：分两条 UPDATE 会把刚改过的行又翻回去
            await db.execute(
                update(MatchSupport)
                .where(MatchSupport.match_id == m.id)
                .values(side=case((MatchSupport.side == "a", "b"), else_="a"))
            )

        t_id, t_title, n = t.id, t.title, len(plan)
        match_ids = [m.id for m, _wl, _b in plan]
        await db.commit()

        await audit(
            user=None,
            action="match_sides_randomized",
            target_type="tournament",
            target_id=t_id,
            detail={"title": t_title, "scope": scope, "seed": str(seed),
                    "flipped": n, "match_ids": match_ids},
        )
        print(f"\n已提交：{n} 场左右已翻边（审计 action=match_sides_randomized）。")
        print("让在场的人下拉刷新即可看到新对阵表。")
        return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="存量赛事对阵左右随机翻边（幂等）")
    ap.add_argument("--tournament-id", type=int, required=True, help="目标赛事 id")
    ap.add_argument("--apply", action="store_true", help="真正写库；缺省只空跑（只读）")
    ap.add_argument("--include-started", action="store_true",
                    help="连进行中/已结束的场次一起翻（会同时交换比分与应援边）")
    ap.add_argument("--yes-archived", action="store_true",
                    help="确认要对已结束（归档）的赛事动手；缺省拒绝")
    ap.add_argument("--seed", default=None,
                    help="默认用赛事 id；传 random 则每次不同（不可复现）")
    args = ap.parse_args()

    async def _run() -> int:
        try:
            return await run(args)
        finally:
            await engine.dispose()

    return asyncio.run(_run())


if __name__ == "__main__":
    sys.exit(main())
