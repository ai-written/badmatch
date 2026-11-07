"""多场地双打的不变量检查（需要数据库，可选）。

守的是「两片场地同时开打」这条链上、纯单测覆盖不到的部分（真库 + 真接口）：

A. **12 人 2 片场地**（新路径）
   1. 开赛后每轮 8 人**互不相同** —— 同一个人不会同时在两片场地上；
   2. 每轮 4 人轮空、`bye_players` 给出 4 个名字、`is_current` 落在第 1 轮；
   3. `/next-up` 说 courts=2、next 有 2 场（跳场的判定从接口出去）；
   4. 同一轮两场都放行；跨轮撞人的那场被拦成 400 `player_busy`；
      `force_start` 放行并留下 `match_force_start` 审计。
B. **8 人 1 片场地**（老路径，行为必须与改动前一致）
   1. 每轮仍是 2 场；
   2. **不做按人拦截**：前一场还在进行时，后面与它共用选手的场次照常能开打
      （单场地本来就是一场一场打，"上一场打完了没人点结束"非常常见，
      多这一道只会卡住正常推进）。

用法（必须指向一个**一次性试点库**，脚本会 drop/create 全部表）：

    docker compose -f docker-compose.dev.yml exec -T \\
      -e CHECK_DB_URL=postgresql+asyncpg://badminton:badminton@db:5432/badmatch_probe \\
      server python scripts/check_multi_court.py

安全性：库名必须含 probe / test，否则拒绝运行（防手滑指向开发/生产库）。
退出码 0 = 全过；1 = 有失败；2 = 拒绝运行。
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta
from urllib.parse import urlparse

# ---- 库名护栏：必须在 import 应用模块之前把 DATABASE_URL 定下来 ----
db_url = os.environ.get("CHECK_DB_URL", "").strip()
if not db_url:
    print("需要 CHECK_DB_URL（指向一个一次性试点库），例如：\n"
          "  CHECK_DB_URL=postgresql+asyncpg://badminton:badminton@db:5432/badmatch_probe")
    sys.exit(2)
_db_name = urlparse(db_url).path.lstrip("/")
if not any(k in _db_name for k in ("probe", "test")):
    print(f"拒绝运行：库名 {_db_name!r} 不像一次性试点库（必须含 probe / test）")
    sys.exit(2)
os.environ["DATABASE_URL"] = db_url
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx                                                    # noqa: E402

from app.core.database import Base, async_session_factory, engine   # noqa: E402
from app.core.security import create_access_token                    # noqa: E402
from app.models.tournament import Registration                       # noqa: E402
from app.models.user import User                                     # noqa: E402
import main                                                          # noqa: E402


def _players_of(m):
    out = set()
    for side in ("pairing_a", "pairing_b"):
        out.add(m[side]["player_a"]["id"])
        out.add(m[side]["player_b"]["id"])
    return out


async def seed():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with async_session_factory() as db:
        boss = User(username="boss", email="boss@example.com", password_hash="x",
                    role="superadmin", is_active=True)
        db.add(boss)
        await db.flush()
        players = []
        for i in range(16):
            p = User(username=f"p{i}", email=f"p{i}@example.com", password_hash="x",
                     role="user", is_active=True, invited_by=boss.id)
            db.add(p)
            players.append(p)
        await db.commit()
        return boss, players


async def create_and_start(c, auth, players, courts, total, title):
    start = datetime.now() + timedelta(days=1)
    payload = {
        "title": title,
        "location": "奥体",
        "start_date": start.isoformat(),
        "end_date": (start + timedelta(hours=3)).isoformat(),
        "max_participants": len(players),
        "total_matches": total,
        "points_to_win": 11,
        "courts": [
            {"name": f"{i + 1}号场", "sort_order": i,
             "time_slots": [{"start_time": "19:00:00", "end_time": "21:00:00"}]}
            for i in range(courts)
        ],
    }
    r = await c.post("/api/tournaments", json=payload, headers=auth)
    assert r.status_code == 200, (r.status_code, r.text)
    tid = r.json()["id"]
    async with async_session_factory() as db:
        for p in players:
            db.add(Registration(tournament_id=tid, user_id=p.id, is_active=True))
        await db.commit()
    r = await c.post(f"/api/tournaments/{tid}/start", headers=auth)
    assert r.status_code == 200, (r.status_code, r.text)
    r = await c.get(f"/api/tournaments/{tid}/rounds", headers=auth)
    return tid, r.json(), r.json() and r.json()


async def phase_multi(c, auth, players):
    print("== A. 12 人 2 片场地（新路径）==")
    tid, rounds, _ = await create_and_start(c, auth, players, 2, 36, "多场地冒烟")
    bad = 0
    for rnd in rounds:
        ids = []
        for m in rnd["matches"]:
            ids += list(_players_of(m))
        if len(set(ids)) != len(ids):
            bad += 1
    print(f"   轮数={len(rounds)} 同轮撞人的轮次={bad}（必须 0）")
    assert bad == 0
    print(f"   第 1 轮 is_current={rounds[0]['is_current']} "
          f"轮空人数={len(rounds[0]['bye_players'])} "
          f"轮空={[p['username'] for p in rounds[0]['bye_players']]}")
    assert rounds[0]["is_current"] is True and len(rounds[0]["bye_players"]) == 4

    r = await c.get(f"/api/tournaments/{tid}/next-up", headers=auth)
    nxt = r.json()
    print(f"   next-up: courts={nxt['courts']} playing={len(nxt['playing'])} next={len(nxt['next'])}")
    assert nxt["courts"] == 2 and len(nxt["next"]) == 2

    m1, m2 = rounds[0]["matches"][0], rounds[0]["matches"][1]
    for m in (m1, m2):
        r = await c.put(f"/api/tournaments/{tid}/matches/{m['id']}/score",
                        json={"score_a": 1, "score_b": 0}, headers=auth)
        assert r.status_code == 200, ("同一轮的两场应当都放行", r.status_code, r.text)
    print("   同一轮两场同时开打：都放行")

    p1 = _players_of(m1)
    target = None
    for rnd in rounds[1:]:
        for m in rnd["matches"]:
            if _players_of(m) & p1:
                target = m
                break
        if target:
            break
    assert target is not None, "后面应当有与第 1 轮共用选手的比赛"
    r = await c.put(f"/api/tournaments/{tid}/matches/{target['id']}/score",
                    json={"score_a": 1, "score_b": 0}, headers=auth)
    print(f"   跨轮撞人的那场: {r.status_code} {r.json().get('detail', {}).get('code')}")
    assert r.status_code == 400 and r.json()["detail"]["code"] == "player_busy"

    # 「仍然开打」这个出口已经删掉了：人和场地都是物理约束，硬开只会造出假数据。
    # 老客户端塞 force_start 也不许放行。
    r = await c.put(f"/api/tournaments/{tid}/matches/{target['id']}/score",
                    json={"score_a": 1, "score_b": 0, "force_start": True}, headers=auth)
    assert r.status_code == 400, ("force_start 不该再放行", r.status_code, r.text)
    print("   带 force_start 再试：仍然 400（没有强制开打）")
    return tid


async def phase_no_court(c, auth, players):
    """两片场地都占着时，人齐的下一批也开不了（物理约束），且提示说清是哪几场占着。"""
    print("== C. 16 人 2 片场地：两片都在打 ==")
    tid, rounds, _ = await create_and_start(c, auth, players[:16], 2, 32, "无空场地冒烟")
    r1a, r1b = rounds[0]["matches"][0], rounds[0]["matches"][1]
    for m in (r1a, r1b):
        r = await c.put(f"/api/tournaments/{tid}/matches/{m['id']}/score",
                        json={"score_a": 1, "score_b": 0}, headers=auth)
        assert r.status_code == 200, (r.status_code, r.text)
    print("   先开满两片场地：都放行；场地 =",
          [x["court_label"] for x in (r1a, r1b)])

    # 第 2 轮的人全是轮空的 8 个人（与第 1 轮不相交）→ 人齐，但两片场地都占着
    busy = _players_of(r1a) | _players_of(r1b)
    target = None
    for rnd in rounds[1:]:
        for m in rnd["matches"]:
            if not (_players_of(m) & busy):
                target = m
                break
        if target:
            break
    assert target is not None, "第 2 轮应当有 4 人全空闲的比赛"
    r = await c.put(f"/api/tournaments/{tid}/matches/{target['id']}/score",
                    json={"score_a": 1, "score_b": 0}, headers=auth)
    detail = r.json().get("detail", {})
    print(f"   人齐但两片场地都占着 -> {r.status_code} {detail.get('code')}")
    assert r.status_code == 400 and detail.get("code") == "no_court_free", (r.status_code, r.text)
    assert len(detail.get("courts") or []) == 2, detail


async def phase_legacy(c, auth, players):
    print("== B. 8 人 1 片场地（老路径）==")
    tid, rounds, _ = await create_and_start(c, auth, players[:8], 1, 14, "单场地冒烟")
    assert all(len(r["matches"]) == 2 for r in rounds), "老路径每轮仍应是 2 场"
    print(f"   轮数={len(rounds)} 每轮场次={ {len(r['matches']) for r in rounds} }（应为 2）")

    m1 = rounds[0]["matches"][0]
    r = await c.put(f"/api/tournaments/{tid}/matches/{m1['id']}/score",
                    json={"score_a": 1, "score_b": 0}, headers=auth)
    assert r.status_code == 200, (r.status_code, r.text)

    # 找一场与 m1 共用选手的（单场地：8 人全员上场，第 2 轮必然共用）
    p1 = _players_of(m1)
    target = None
    for rnd in rounds[1:]:
        for m in rnd["matches"]:
            if _players_of(m) & p1:
                target = m
                break
        if target:
            break
    r = await c.put(f"/api/tournaments/{tid}/matches/{target['id']}/score",
                    json={"score_a": 1, "score_b": 0}, headers=auth)
    print(f"   老路径：前一场还在进行时开后面共用选手的一场 → {r.status_code}（应为 200，不做拦截）")
    assert r.status_code == 200, ("单场地赛事不该被按人拦截", r.status_code, r.text)


async def main_async():
    boss, players = await seed()
    token = create_access_token({"sub": str(boss.id)}, boss.token_version)
    auth = {"Authorization": f"Bearer {token}"}
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        await phase_multi(c, auth, players[:12])
        await phase_legacy(c, auth, players)
        await phase_no_court(c, auth, players)
    print("\nE2E OK")


if __name__ == "__main__":
    asyncio.run(main_async())
    sys.exit(0)
