"""「这个人此刻是不是在场上」—— 一个人同一时刻只能占一片场地。

多场地赛事（≥2 片场地）里会有两场比赛**同时**进行，于是"谁正在打"必须能查清：

- 记分前（首次记分 = 开打，见 `api/matches.update_score`）：4 名选手里只要有人
  正在别的场上，就不让开打 —— 这就是"按人拦截"，**只在真冲突时**才拦；
  不冲突时两片场地各打各的，谁快谁慢互不影响（不等整轮，也不占场地）。
- 认领裁判前（`api/referee.claim_referee`）：正在打球的人不能同时去另一片场地执裁。

判定口径只有一条：**别的比赛的 `status == ongoing`**（首次记分那一刻置 ongoing、
结束比赛置 finished）。没开打的场次**不占人** —— 那只是先后顺序问题，不是撞期。

为什么不用"同一轮"来判断：赛场节奏一乱（A 场快、B 场慢）就会出现"某人第 3 轮的场
还没打完、第 4 轮已经开打"，跨轮撞人才是真实形态；按轮拦会把整片场地空着等。
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.round import Match, MatchStatus, Round, RoundPairing
from app.models.user import User


async def ongoing_map(db: AsyncSession, tournament_id: int,
                      exclude_match_id: int | None = None) -> dict[int, int]:
    """此刻正在打的人 → 他所在的那场 `match_id`。

    `exclude_match_id`：排除自己（记分的是自己这场比赛，不该算成"占用"）。
    一次按赛事查全部进行中的比赛 + 一次批量查配对，不做逐场 N+1。
    """
    stmt = select(Match).where(
        Match.tournament_id == tournament_id,
        Match.status == MatchStatus.ONGOING,
    )
    if exclude_match_id is not None:
        stmt = stmt.where(Match.id != exclude_match_id)
    matches = (await db.execute(stmt)).scalars().all()
    if not matches:
        return {}

    pairing_ids = {pid for m in matches for pid in (m.pairing_a_id, m.pairing_b_id)}
    rows = await db.execute(select(RoundPairing).where(RoundPairing.id.in_(pairing_ids)))
    pairing_map = {p.id: p for p in rows.scalars().all()}

    out: dict[int, int] = {}
    for m in matches:
        for pid in (m.pairing_a_id, m.pairing_b_id):
            pairing = pairing_map.get(pid)
            if pairing is None:
                continue
            out.setdefault(pairing.player_a_id, m.id)
            out.setdefault(pairing.player_b_id, m.id)
    return out


async def match_players(db: AsyncSession, matches) -> dict[int, set[int]]:
    """`{match_id: {该场 4 名选手}}`，一次批量查（配对 id 去重后查一遍）。"""
    matches = list(matches)
    if not matches:
        return {}
    pairing_ids = {pid for m in matches for pid in (m.pairing_a_id, m.pairing_b_id)}
    rows = await db.execute(select(RoundPairing).where(RoundPairing.id.in_(pairing_ids)))
    pairing_map = {p.id: p for p in rows.scalars().all()}
    out: dict[int, set[int]] = {}
    for m in matches:
        players: set[int] = set()
        for pid in (m.pairing_a_id, m.pairing_b_id):
            pairing = pairing_map.get(pid)
            if pairing is None:
                continue
            players.add(pairing.player_a_id)
            players.add(pairing.player_b_id)
        out[m.id] = players
    return out


async def conflicts_for_match(db: AsyncSession, tournament_id: int, match) -> list[dict]:
    """这场比赛里**此刻正在别的场上**的人。正常情况就是空列表。

    返回的每一项给前端直接用：谁（`username`）、在哪一轮（`round_number`）的哪一场
    （`match_id`）—— 前端把 `match_id` 换成它自己那套「第 N 场」编号，
    所以这里不去算序号（那要按轮次+id 排序全表扫一遍，而这条路径每次开打都会走）。
    """
    busy = await ongoing_map(db, tournament_id, exclude_match_id=match.id)
    if not busy:
        return []
    mine = (await match_players(db, [match])).get(match.id, set())
    hits = {pid: mid for pid, mid in busy.items() if pid in mine}
    if not hits:
        return []

    users = await db.execute(select(User).where(User.id.in_(set(hits))))
    name_map = {u.id: u.username for u in users.scalars().all()}
    other_ids = set(hits.values())
    round_map: dict[int, int] = {}
    if other_ids:
        other = await db.execute(
            select(Match.id, Round.round_number)
            .join(Round, Round.id == Match.round_id)
            .where(Match.id.in_(other_ids))
        )
        round_map = {mid: num for mid, num in other.all()}
    return [
        {
            "player_id": pid,
            "username": name_map.get(pid, ""),
            "match_id": mid,
            "round_number": round_map.get(mid),
        }
        for pid, mid in sorted(hits.items())
    ]


async def busy_match_of(db: AsyncSession, tournament_id: int, user_id: int,
                        exclude_match_id: int | None = None) -> int | None:
    """这个人此刻是不是在别的场上（是就返回那场 match_id）。"""
    busy = await ongoing_map(db, tournament_id, exclude_match_id=exclude_match_id)
    return busy.get(user_id)
