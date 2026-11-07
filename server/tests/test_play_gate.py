"""「按人拦截」这条链的单元测试。

盯的三件事：
1. 开打前只在**真有冲突**时拦（不冲突就放行，不退化成"必须等整轮打完"）；
2. 冲突的判定口径是"别的比赛 status=ongoing"，不是"同一轮"——
   跳场之后跨轮撞人才是真实形态；
3. 裁判只能从**本轮场下的人**里挑（多场地），老路径（8 人局互为候选人池）不能被改坏。

用假 db 钉住调用顺序与语句本身（与 test_auto_finish_audit.py 同一套写法）。
"""
import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import matches as matches_mod
from app.api import referee as referee_mod
from app.core import occupancy
from app.models.round import MatchStatus


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return self

    def all(self):
        return self._value


class _ScriptedDB:
    """按顺序返回预设结果；跑完再被调用就抛 IndexError（= 这一步不该被调用）。"""

    def __init__(self, results, labels=None):
        self._results = [_Result(r) for r in results]
        self._labels = list(labels or [])
        self.statements = []

    async def execute(self, stmt, *args, **kwargs):
        self.statements.append(stmt)
        if not self._results:
            raise IndexError("不该再查库了：调用顺序与预期不符")
        return self._results.pop(0)


def _run(coro):
    return asyncio.run(coro)


# ---- 当前批次（is_current 与服务端派裁判共用同一套判定）----

def test_current_round_ids_falls_back_to_earliest_live_round():
    """没有进行中的场次时 = 老口径：最早一个还有未打完场次的轮次。"""
    assert matches_mod._current_round_ids([3, 4, 5], set()) == {3}


def test_current_round_ids_includes_rounds_with_ongoing_matches():
    """跳场后：正在打的是第 5 轮、第 3 轮还一场没开打 → 两批都算"当前"。"""
    assert matches_mod._current_round_ids([3, 4, 5], {5}) == {3, 5}
    assert matches_mod._current_round_ids([3, 4], {4}) == {3, 4}


def test_current_round_ids_empty_when_all_finished():
    assert matches_mod._current_round_ids([], set()) == set()


# ---- 谁正在场上（occupancy）----

def test_ongoing_map_maps_players_to_their_match():
    """两只 ongoing 比赛里的 8 个人 → {player: match_id}。"""
    m1 = SimpleNamespace(id=11, pairing_a_id=1, pairing_b_id=2)
    m2 = SimpleNamespace(id=22, pairing_a_id=3, pairing_b_id=4)
    pairings = [
        SimpleNamespace(id=1, player_a_id=101, player_b_id=102),
        SimpleNamespace(id=2, player_a_id=103, player_b_id=104),
        SimpleNamespace(id=3, player_a_id=105, player_b_id=106),
        SimpleNamespace(id=4, player_a_id=107, player_b_id=108),
    ]
    db = _ScriptedDB([[m1, m2], pairings])
    result = _run(occupancy.ongoing_map(db, tournament_id=7))
    assert result == {101: 11, 102: 11, 103: 11, 104: 11,
                      105: 22, 106: 22, 107: 22, 108: 22}


def test_ongoing_map_short_circuits_when_nothing_ongoing():
    """没有进行中的比赛 → 只查一次，不额外查配对表。"""
    db = _ScriptedDB([[]])
    assert _run(occupancy.ongoing_map(db, tournament_id=7)) == {}
    assert len(db.statements) == 1


def test_busy_match_of_returns_the_other_match():
    m1 = SimpleNamespace(id=11, pairing_a_id=1, pairing_b_id=2)
    pairings = [SimpleNamespace(id=1, player_a_id=101, player_b_id=102),
                SimpleNamespace(id=2, player_a_id=103, player_b_id=104)]
    db = _ScriptedDB([[m1], pairings])
    assert _run(occupancy.busy_match_of(db, 7, 103)) == 11
    db = _ScriptedDB([[m1], pairings])
    assert _run(occupancy.busy_match_of(db, 7, 999)) is None


# ---- 开打闸门 ----

def _fake_match(match_id=5, ta=(1, 2), tb=(3, 4)):
    return SimpleNamespace(
        id=match_id, tournament_id=7,
        pairing_a_id=ta, pairing_b_id=tb,
        status=MatchStatus.PENDING,
    )


def _spy_assign(monkeypatch, ok: bool = True):
    """把"开打时分配场地"换成记录器：它自己有一组专门的测试。"""
    calls = []

    async def fake(db, tournament_id, match):
        calls.append(match.id)
        return ok
    monkeypatch.setattr(matches_mod, "_assign_start_court", fake)
    return calls


def test_gate_allows_start_when_nobody_is_on_court(monkeypatch):
    """人齐 + 有场地 -> 放行，并顺手分配场地。"""
    async def no_conflicts(db, tournament_id, match):
        return []
    monkeypatch.setattr(matches_mod, "conflicts_for_match", no_conflicts)
    calls = _spy_assign(monkeypatch)

    db = _ScriptedDB([None])          # 只应该有那把咨询锁
    _run(matches_mod._ensure_can_start(db, 7, _fake_match()))
    assert len(db.statements) == 1, "没冲突时除了咨询锁不该再查别的"
    assert calls == [5], "放行就该给它分配场地"


def test_gate_rejects_with_400_when_a_player_is_playing_elsewhere(monkeypatch):
    """有冲突 -> 400 + 明细，且**不是** 409（409 在客户端是"比分版本冲突"，
    会被 planScoreRetry 当成比分去重试）。"""
    async def two_conflicts(db, tournament_id, match):
        return [{"player_id": 3, "username": "张三", "match_id": 9, "round_number": 4}]
    monkeypatch.setattr(matches_mod, "conflicts_for_match", two_conflicts)

    calls = _spy_assign(monkeypatch)
    db = _ScriptedDB([None])
    with pytest.raises(HTTPException) as exc:
        _run(matches_mod._ensure_can_start(db, 7, _fake_match()))
    assert calls == [], "被拦下就不该分配场地"
    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == "player_busy"
    assert "张三" in exc.value.detail["message"]
    assert exc.value.detail["conflicts"][0]["match_id"] == 9


def test_gate_rejects_when_every_court_is_busy(monkeypatch):
    """人齐但两片场地都在打 -> 400 no_court_free，并说清是哪几场占着。

    这里**刻意没有**"强制开打"的出口：真开出来就是"同一时刻两场在一片场地上"，
    那是假数据；现场若那场已经打完，正确操作是先去结束它。
    """
    async def no_conflicts(db, tournament_id, match):
        return []
    monkeypatch.setattr(matches_mod, "conflicts_for_match", no_conflicts)
    _spy_assign(monkeypatch, ok=False)

    async def usage(db, tournament_id, exclude_match_id):
        return [{"match_id": 9, "round_number": 1, "court_name": "1号场"},
                {"match_id": 10, "round_number": 1, "court_name": "2号场"}]
    monkeypatch.setattr(matches_mod, "_ongoing_court_usage", usage)

    db = _ScriptedDB([None])
    with pytest.raises(HTTPException) as exc:
        _run(matches_mod._ensure_can_start(db, 7, _fake_match()))
    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == "no_court_free"
    assert "1号场" in exc.value.detail["message"] and "2号场" in exc.value.detail["message"]
    assert len(exc.value.detail["courts"]) == 2


def test_gate_takes_an_advisory_lock_before_reading(monkeypatch):
    """并发保护：必须先拿到事务级咨询锁，再去查"谁在场上"。

    两个人同时给两场比赛记第一分时，双方都会读到"对方还没开打"而都放行 ——
    共享锁（load_writable_tournament 的 FOR SHARE）拦不住，只能靠这把锁串行化；
    场地分配也在这把锁里做，否则两场同时开打会挑到同一片场地。
    """
    order = []

    async def conflict(db, tournament_id, match):
        order.append("read-conflicts")
        return []

    async def assign(db, tournament_id, match):
        order.append("assign-court")
        return True

    monkeypatch.setattr(matches_mod, "conflicts_for_match", conflict)
    monkeypatch.setattr(matches_mod, "_assign_start_court", assign)
    db = _ScriptedDB([None])
    _run(matches_mod._ensure_can_start(db, 7, _fake_match()))

    assert len(db.statements) == 1
    assert "pg_advisory_xact_lock" in str(db.statements[0])
    assert order == ["read-conflicts", "assign-court"]


# ---- 裁判候选人池 ----

def _live_match(match_id, pa, pb):
    return SimpleNamespace(id=match_id, pairing_a_id=pa, pairing_b_id=pb)


def test_round_pool_multicourt_only_picks_players_off_court():
    """多场地：本轮两场 8 个人在场上，只剩 4 个人能当裁判。"""
    live = [_live_match(1, 11, 12), _live_match(2, 13, 14)]
    pairings = {
        11: SimpleNamespace(id=11, player_a_id=1, player_b_id=2),
        12: SimpleNamespace(id=12, player_a_id=3, player_b_id=4),
        13: SimpleNamespace(id=13, player_a_id=5, player_b_id=6),
        14: SimpleNamespace(id=14, player_a_id=7, player_b_id=8),
    }
    pool = referee_mod.round_referee_pool(list(range(1, 13)), live, pairings, True)
    assert pool == [9, 10, 11, 12], "场上那 8 个人一个都不能进候选人池"


def test_round_pool_legacy_keeps_everyone():
    """老路径（单场地/8 人）：仍是全部在场选手，只由 assign_referees 排除本场 4 人。

    8 人局全员上场，改成"只能场下的人"就一场裁判都派不出来了。
    """
    live = [_live_match(1, 11, 12), _live_match(2, 13, 14)]
    pairings = {
        11: SimpleNamespace(id=11, player_a_id=1, player_b_id=2),
        12: SimpleNamespace(id=12, player_a_id=3, player_b_id=4),
        13: SimpleNamespace(id=13, player_a_id=5, player_b_id=6),
        14: SimpleNamespace(id=14, player_a_id=7, player_b_id=8),
    }
    pool = referee_mod.round_referee_pool(list(range(1, 9)), live, pairings, False)
    assert pool == list(range(1, 9))


# ---- 跳场：现在该开哪几场 ----

def _match(match_id, status=MatchStatus.PENDING):
    return SimpleNamespace(id=match_id, status=status)


def test_pick_next_takes_first_slots_when_nobody_is_busy():
    matches = [_match(1), _match(2), _match(3)]
    players = {1: {1, 2, 3, 4}, 2: {5, 6, 7, 8}, 3: {1, 5, 9, 10}}
    nxt, skipped = matches_mod._pick_next_matches(matches, players, {}, 2)
    assert nxt == [1, 2] and skipped == []


def test_pick_next_skips_a_blocked_match_and_reports_who_is_waiting():
    """第 1 场有人还在邻场 → 跳到第 2 场；第 1 场记进 skipped 并说明等谁。"""
    matches = [_match(1), _match(2)]
    players = {1: {1, 2, 3, 4}, 2: {5, 6, 7, 8}}
    busy = {2: 99}                       # 2 号选手正在 99 号场
    nxt, skipped = matches_mod._pick_next_matches(matches, players, busy, 1)
    assert nxt == [2]
    assert len(skipped) == 1
    blocked, waiting = skipped[0]
    assert blocked.id == 1 and waiting == [2]


def test_pick_next_returns_nothing_when_all_courts_busy():
    matches = [_match(1), _match(2)]
    players = {1: {1, 2, 3, 4}, 2: {5, 6, 7, 8}}
    assert matches_mod._pick_next_matches(matches, players, {}, 0) == ([], [])


def test_pick_next_ignores_started_and_finished_matches():
    matches = [_match(1, MatchStatus.ONGOING), _match(2, MatchStatus.FINISHED), _match(3)]
    players = {1: {1, 2}, 2: {3, 4}, 3: {5, 6, 7, 8}}
    nxt, _skipped = matches_mod._pick_next_matches(matches, players, {}, 1)
    assert nxt == [3]


def test_pick_next_does_not_change_who_plays():
    """跳场只改顺序：被选中/被跳过的场次，人员集合与输入完全一致（只是顺序变了）。"""
    matches = [_match(1), _match(2), _match(3)]
    players = {1: {1, 2, 3, 4}, 2: {5, 6, 7, 8}, 3: {9, 10, 11, 12}}
    busy = {1: 77}
    nxt, skipped = matches_mod._pick_next_matches(matches, players, busy, 2)
    picked = set(nxt) | {m.id for m, _ in skipped}
    assert picked == {1, 2, 3}
    for mid in (1, 2, 3):
        assert players[mid] == ({1, 2, 3, 4} if mid == 1 else
                                {5, 6, 7, 8} if mid == 2 else {9, 10, 11, 12})


# ---- 引擎分流（engine_api._round_engine_plan）----

def test_round_engine_plan_branches():
    """2 片场地 + >=9 人 -> 轮级；单场地或 8 人及以下 -> 老引擎、每轮 2 场。"""
    from app.api import engine_api

    assert _run(engine_api._round_engine_plan(_ScriptedDB([2, 12]), 7, 12)) == (True, 2)
    assert _run(engine_api._round_engine_plan(_ScriptedDB([1, 12]), 7, 12)) == (False, 2)
    assert _run(engine_api._round_engine_plan(_ScriptedDB([2, 8]), 7, 8)) == (False, 2)
    assert _run(engine_api._round_engine_plan(_ScriptedDB([0, 12]), 7, 12)) == (False, 2)


def test_round_engine_plan_downgrades_courts_when_players_are_few():
    """9 人 2 片场地：8 人上场只剩 1 个场下，只够 1 个裁判 -> 每轮降到 1 场。"""
    from app.api import engine_api

    assert _run(engine_api._round_engine_plan(_ScriptedDB([2, 9]), 7, 9)) == (True, 1)


def test_round_engine_plan_at_start_uses_passed_roster_size():
    """开赛那一刻 PlayerStats 还没落库（0 行），必须靠显式传进来的名单人数分流。

    漏掉这个参数的表现是：12 人 2 片场地的赛事开赛时一律退回老引擎，
    于是每份赛程又有几轮"同一人同时在两场"—— 这是最容易复发的一处接线。
    """
    from app.api import engine_api

    db = _ScriptedDB([2])          # 只查 courts：roster 由参数给，不该再去数一下
    assert _run(engine_api._round_engine_plan(db, 7, 12, roster_size=12)) == (True, 2)


def test_is_multi_court_tournament_reads_courts_and_roster():
    from app.api import engine_api

    assert _run(engine_api.is_multi_court_tournament(_ScriptedDB([2, 12]), 7)) is True
    assert _run(engine_api.is_multi_court_tournament(_ScriptedDB([1, 12]), 7)) is False
    assert _run(engine_api.is_multi_court_tournament(_ScriptedDB([2, 8]), 7)) is False


# ---- /next-up 接口接线（路由 + 响应结构，用假 db 走完整条查询链）----

def _make_world():
    """两轮：第 1 轮两场（m1 正在进行、m2 待打），第 2 轮一场（m3 待打）。

    m2 与 m1 共用 1 号选手（所以他被卡住），m3 全空闲 -> 应该跳过 m2 先打 m3。
    """
    m1 = SimpleNamespace(id=1, round_id=11, tournament_id=7, status=MatchStatus.ONGOING,
                         pairing_a_id=101, pairing_b_id=102)
    m2 = SimpleNamespace(id=2, round_id=11, tournament_id=7, status=MatchStatus.PENDING,
                         pairing_a_id=103, pairing_b_id=104)
    m3 = SimpleNamespace(id=3, round_id=12, tournament_id=7, status=MatchStatus.PENDING,
                         pairing_a_id=105, pairing_b_id=106)
    pairings = [
        SimpleNamespace(id=101, player_a_id=1, player_b_id=2),
        SimpleNamespace(id=102, player_a_id=3, player_b_id=4),
        SimpleNamespace(id=103, player_a_id=1, player_b_id=5),
        SimpleNamespace(id=104, player_a_id=6, player_b_id=7),
        SimpleNamespace(id=105, player_a_id=8, player_b_id=9),
        SimpleNamespace(id=106, player_a_id=10, player_b_id=11),
    ]
    return m1, m2, m3, pairings


def test_next_up_endpoint_skips_blocked_match(monkeypatch):
    """/next-up 的接线：跳场的判定结果要真的从接口出去（前端只读不算）。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.matches import router as matches_router
    from app.core.database import get_db
    from app.core.security import require_user

    m1, m2, m3, pairings = _make_world()
    user = SimpleNamespace(id=99, username="tester", role="user")
    app = FastAPI()
    app.include_router(matches_router)
    app.dependency_overrides[require_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: _ScriptedDB([
        [(m1, 1), (m2, 1), (m3, 2)],          # 全部比赛 + 轮次号（已排序）
        2,                                     # courts 数
        12,                                    # roster（开赛时人数）
        12,                                    # 当前在场人数
        [m1],                                  # ongoing_map: 进行中的比赛
        pairings,                              # ongoing_map: 配对
        pairings,                              # match_players: 配对
        [SimpleNamespace(id=1, username="张三")],   # 等待中的人名
    ])
    client = TestClient(app)

    res = client.get("/api/tournaments/7/next-up")
    assert res.status_code == 200
    body = res.json()
    assert body["courts"] == 2
    assert [p["match_id"] for p in body["playing"]] == [1]
    assert [n["match_id"] for n in body["next"]] == [3], "第 2 场被卡住，应跳过它先打第 3 场"
    assert body["skipped"][0]["match_id"] == 2
    assert body["skipped"][0]["waiting"][0]["username"] == "张三"
    # 「第 N 场」编号：按 (轮次, id) 顺序，供前端直接展示
    assert body["next"][0]["match_number"] == 3




# ---- 开打时分配场地（真实归属，落库）----

def test_assign_start_court_keeps_the_planned_court_when_free():
    """计划的那片场地空着 -> 沿用它（计划就是现实）。"""
    match = SimpleNamespace(id=5, court_id=11)
    db = _ScriptedDB([[11, 12], []])          # 场地 id、进行中比赛占用的场地
    _run(matches_mod._assign_start_court(db, 7, match))
    assert match.court_id == 11


def test_assign_start_court_moves_to_a_free_court_when_planned_is_taken():
    """计划的那片正被别的比赛占着 -> 换到空出来的那片（现场"哪片空谁先上"）。"""
    match = SimpleNamespace(id=5, court_id=11)
    db = _ScriptedDB([[11, 12], [11]])
    _run(matches_mod._assign_start_court(db, 7, match))
    assert match.court_id == 12


def test_assign_start_court_does_nothing_for_single_court():
    """一片场地不做分配（老路径）：连查询都不该多发。"""
    match = SimpleNamespace(id=5, court_id=None)
    db = _ScriptedDB([[11]])
    _run(matches_mod._assign_start_court(db, 7, match))
    assert match.court_id is None
    assert len(db.statements) == 1


def test_assign_start_court_keeps_value_when_every_court_is_busy():
    """两片都占着：不硬塞（人齐了就能打，场地现场协调），保持原值。"""
    match = SimpleNamespace(id=5, court_id=None)
    db = _ScriptedDB([[11, 12], [11, 12]])
    _run(matches_mod._assign_start_court(db, 7, match))
    assert match.court_id is None
