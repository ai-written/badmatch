"""混双模式（每场搭档一男一女、对手也一男一女）单测。

产品约定（照抄实现里的说明，免得测试和实现各说一套）：

1. **只有**「男女人数相等 + 每名选手都设置了性别 + 男女各≥2」才允许开启混双；
   条件不满足时排程器直接抛 ValueError（接口层会先拦一次并给出可读提示）。
2. 每一场的两队**都必须是一男一女**，对手同理 —— 这是模式的定义，不是"尽量"。
3. 「每人场次相等」的硬约束、同轮互斥（多场地）在混双下不能松：
   每轮上场的男、女人数必须相等（各 2 * 每轮场数）。
4. 合法总场次的步长与普通双打**完全一致**（不必为混双另立一套档位）。
"""
from collections import Counter, defaultdict

import pytest

from app.engine.jit import pick_court_first_match
from app.engine.scheduler import (
    _pair_key,
    _partner_floor,
    compute_rounds,
    fairness_step,
    generate_round_schedule,
    generate_schedule,
    mixed_doubles_available,
    mixed_doubles_counts_ok,
)


def _genders(players, men_count):
    """前 men_count 个人是男的，其余是女的。"""
    return {p: ("M" if i < men_count else "F") for i, p in enumerate(players)}


# ---- 1. 开关条件 ----

def test_counts_ok_requires_equal_and_all_set():
    assert mixed_doubles_counts_ok(4, 4, 8) is True
    assert mixed_doubles_counts_ok(3, 3, 6) is True
    # 人数不等
    assert mixed_doubles_counts_ok(5, 4, 9) is False
    # 有人性别未设置（合计对不上）
    assert mixed_doubles_counts_ok(4, 3, 8) is False
    # 人数不足（各 1 人凑不出一场）
    assert mixed_doubles_counts_ok(1, 1, 2) is False
    assert mixed_doubles_counts_ok(0, 0, 0) is False


def test_available_uses_the_same_rule():
    assert mixed_doubles_available({1: "M", 2: "M", 3: "F", 4: "F"}) is True
    assert mixed_doubles_available({1: "M", 2: "M", 3: "F", 4: None}) is False
    assert mixed_doubles_available({1: "M", 2: "M", 3: "M", 4: "F"}) is False
    assert mixed_doubles_available({}) is False
    assert mixed_doubles_available(None) is False


def test_valid_total_steps_are_the_same_as_normal_doubles():
    """混双不需要另一套"合法总场次"口径：4M 能被 N 整除 ⇔ 2M 能被 N/2 整除。

    这条如果变了，界面上的档位（fairness_step）就会和接口校验打架。
    """
    import math
    for n in range(4, 33, 2):                    # 只看男女相等的偶数人数
        half = n // 2
        mixed_step = half // math.gcd(2, half)
        assert fairness_step(n) == mixed_step, n


# ---- 2. 逐场贪心（单场地 / 8 人及以下）----

def _assert_every_team_is_mixed(schedule, genders):
    for t1, t2, *_ in schedule:
        for team in (t1, t2):
            assert {genders[p] for p in team} == {"M", "F"}, f"不是混双搭档：{team}"


def test_greedy_mixed_schedule_pairs_one_man_one_woman():
    players = list(range(1, 9))
    genders = _genders(players, 4)
    schedule = generate_schedule(players, 14, mixed_doubles=True, genders=genders)

    assert len(schedule) == 14
    _assert_every_team_is_mixed(schedule, genders)

    played = Counter()
    for t1, t2, *_ in schedule:
        played.update(t1 + t2)
    assert set(played.values()) == {7}, "8 人 14 场每人应恰好 7 场"


def test_greedy_mixed_is_reproducible():
    players = list(range(1, 13))
    genders = _genders(players, 6)
    a = generate_schedule(players, 18, mixed_doubles=True, genders=genders)
    b = generate_schedule(players, 18, mixed_doubles=True, genders=genders)
    assert a == b


def test_greedy_mixed_rejects_unequal_or_unset_genders():
    players = list(range(1, 9))
    with pytest.raises(ValueError):
        generate_schedule(players, 14, mixed_doubles=True,
                          genders=_genders(players, 5))       # 5 男 3 女
    broke = _genders(players, 4)
    broke[players[0]] = None                                  # 有人没设置性别
    with pytest.raises(ValueError):
        generate_schedule(players, 14, mixed_doubles=True, genders=broke)


# ---- 3. 轮级构造（多场地）----

@pytest.mark.parametrize("n, men, per_round, total", [
    (12, 6, 2, 36),      # 2 片场地
    (12, 6, 1, 12),      # 退赛降级成单场地
    (20, 10, 3, 30),     # 3 片场地（走逐场贪心那条分支）
])
def test_round_engine_mixed(n, men, per_round, total):
    players = list(range(1, n + 1))
    genders = _genders(players, men)
    schedule = generate_round_schedule(players, total, per_round,
                                       mixed_doubles=True, genders=genders)

    assert len(schedule) == total
    _assert_every_team_is_mixed(schedule, genders)

    played = Counter()
    for rnd in compute_rounds(schedule, per_round):
        on = [p for t1, t2, *_ in rnd for p in (*t1, *t2)]
        assert len(set(on)) == len(on), "同一轮里有人出现在两场"
        assert len(set(on)) == 4 * len(rnd)
        # 每轮上场的男女人数相等 —— 混双每场 2 男 2 女
        assert sum(1 for p in on if genders[p] == "M") == 2 * len(rnd)
        played.update(on)

    target = 4 * total // n
    assert set(played.values()) == {target}, "每人场次必须相等"


def test_round_engine_mixed_rejects_unequal_roster():
    players = list(range(1, 13))
    with pytest.raises(ValueError):
        generate_round_schedule(players, 33, 2, mixed_doubles=True,
                                genders=_genders(players, 7))     # 7 男 5 女


# ---- 4. JIT（优先场地，边打边排）----

def test_jit_mixed_picks_one_man_one_woman_per_team():
    players = list(range(1, 9))
    genders = _genders(players, 4)
    played = {p: 0 for p in players}
    for _ in range(20):
        pick = pick_court_first_match(players, played, 7,
                                      mixed_doubles=True, genders=genders)
        assert pick is not None
        assert {genders[p] for p in pick[0]} == {"M", "F"}
        assert {genders[p] for p in pick[1]} == {"M", "F"}


def test_jit_mixed_returns_none_when_a_gender_runs_out():
    """空闲的人里只有 2 男 1 女：混双凑不出来 → 不排（场地先空着）。"""
    free = [1, 2, 3]
    genders = {1: "M", 2: "M", 3: "F"}
    assert pick_court_first_match(free, {p: 0 for p in free}, 7,
                                  mixed_doubles=True, genders=genders) is None

    free = [1, 2, 3, 4, 5, 6]
    genders = {1: "M", 2: "M", 3: "M", 4: "M", 5: "M", 6: "F"}
    assert pick_court_first_match(free, {p: 0 for p in free}, 7,
                                  mixed_doubles=True, genders=genders) is None


def test_jit_mixed_relaxes_the_quota_tier_instead_of_stalling():
    """最少的一档全是男的 → 必须放宽一档去凑女生，否则这片场地会空到他们打满。"""
    free = [1, 2, 3, 4, 5, 6]
    genders = {1: "M", 2: "M", 3: "M", 4: "M", 5: "F", 6: "F"}
    played = {1: 0, 2: 0, 3: 0, 4: 0, 5: 1, 6: 1}
    pick = pick_court_first_match(free, played, 7, mixed_doubles=True, genders=genders)
    assert pick is not None
    assert {genders[p] for p in pick[0]} == {"M", "F"}
    assert {genders[p] for p in pick[1]} == {"M", "F"}


@pytest.mark.parametrize("n", [4, 6, 8, 10, 12, 14, 16, 20])
def test_mixed_sweep_never_dead_ends(n):
    """人数 × 场次 × 场地数扫一遍：两套引擎都不许走死路、也不许排出非法对阵。

    混双的可行域比普通双打窄（每轮/每场都要凑齐男女），这里把"能不能排出来"
    钉住 —— 排不出来会抛 ValueError，赛事就开不起来了。
    """
    from app.engine.scheduler import matches_per_round

    players = list(range(1, n + 1))
    genders = _genders(players, n // 2)
    step = fairness_step(n)
    for total in (step, step * 3):
        if total > 60:
            continue
        # 单场地：老引擎（逐场贪心）
        schedule = generate_schedule(players, total, mixed_doubles=True, genders=genders)
        assert len(schedule) == total
        _assert_every_team_is_mixed(schedule, genders)
        played = Counter()
        for t1, t2, *_ in schedule:
            played.update(t1 + t2)
        assert set(played.values()) == {4 * total // n}

        # 多场地：轮级构造（2 片 / 3 片场地各排一次）
        for courts in (2, 3):
            per_round = matches_per_round(n, courts)
            schedule = generate_round_schedule(players, total, per_round,
                                               mixed_doubles=True, genders=genders)
            assert len(schedule) == total
            _assert_every_team_is_mixed(schedule, genders)
            for rnd in compute_rounds(schedule, per_round):
                on = [p for t1, t2, *_ in rnd for p in (*t1, *t2)]
                assert len(set(on)) == 4 * len(rnd), "同一轮里有人出现在两场"
                assert sum(1 for p in on if genders[p] == "M") == 2 * len(rnd)


# ---- 5. 搭档池是混双的真实代价：理论下限必须按「男×女」算 ----

def test_partner_floor_uses_the_mixed_partner_pool():
    # 8 人 14 场：普通双打搭档池 28 个够用（下限 0）；混双池只有 4*4=16 → 必然重复 12 次
    assert _partner_floor(14, 8, mixed_doubles=False) == 0
    assert _partner_floor(14, 8, mixed_doubles=True) == 12
    # 12 人 36 场：混双池 6*6=36，72 个名额 → 下限 36
    assert _partner_floor(36, 12, mixed_doubles=True) == 36


def test_mixed_schedule_never_repeats_a_match_and_hits_the_partner_floor():
    """一份 8 人 14 场的混双赛程：没有完全相同的对局，且搭档重复正好贴着下限。"""
    players = list(range(1, 9))
    genders = _genders(players, 4)
    schedule = generate_schedule(players, 14, mixed_doubles=True, genders=genders)

    partners = Counter()
    matches = Counter()
    for t1, t2, *_ in schedule:
        k1, k2 = _pair_key(*t1), _pair_key(*t2)
        partners[k1] += 1
        partners[k2] += 1
        matches[(k1, k2) if k1 <= k2 else (k2, k1)] += 1

    assert sum(v - 1 for v in matches.values()) == 0, "出现了完全相同的对局"
    repeats = sum(v - 1 for v in partners.values())
    assert repeats == _partner_floor(14, 8, mixed_doubles=True), (
        f"搭档重复 {repeats} 次，理论下限 {_partner_floor(14, 8, True)}"
    )


# ---- 6. 混双的"公平"：把可量化的几条锁成断言 ----
#
# 产品上的"公平"在这里落成三件事，混双必须和普通双打一样做到：
#   a) 每人场次严格相等（硬约束）
#   b) 搭档重复数贴住组合数学下限，且**分配均匀**：每名选手跟自己每个异性搭档的
#      次数差 ≤1（例：8 人 14 场每人 7 场、4 个搭档 → 2,2,2,1，人人如此）
#   c) 每人面对的对手总次数相等（每场固定 2 个对手），且没有重复对局
# 注：对手"具体到某个人"的相遇次数在混双里存在结构性下限 —— 同性对手只有 N/2-1 个
# 可分而名额更多（每场固定碰 1 个同性、1 个异性），所以必然比异性对手碰得多，
# 这是赛制决定的，不是排程偏心。

@pytest.mark.parametrize("n, total, per_round", [
    (8, 8, None), (8, 14, None), (8, 28, None),
    (12, 12, 2), (12, 36, 2), (16, 24, 2),
])
def test_mixed_fairness(n, total, per_round):
    players = list(range(1, n + 1))
    genders = _genders(players, n // 2)
    if per_round is None:
        schedule = generate_schedule(players, total, mixed_doubles=True, genders=genders)
    else:
        schedule = generate_round_schedule(players, total, per_round,
                                           mixed_doubles=True, genders=genders)

    target = 4 * total // n
    played = Counter()
    partner = defaultdict(Counter)
    opponents = Counter()
    matches = Counter()
    for t1, t2, *_ in schedule:
        k1, k2 = _pair_key(*t1), _pair_key(*t2)
        matches[(k1, k2) if k1 <= k2 else (k2, k1)] += 1
        for a, b in ((k1[0], k1[1]), (k2[0], k2[1])):
            partner[a][b] += 1
            partner[b][a] += 1
        for u in k1:
            for v in k2:
                opponents[_pair_key(u, v)] += 1
        played.update(k1 + k2)

    # a) 每人场次严格相等
    assert set(played.values()) == {target}, f"{n} 人 {total} 场：每人场次不相等"

    # b) 搭档重复贴下限 + 分配均匀（每人对每个异性搭档的次数差 ≤1）
    repeats = sum(sum(c.values()) - len(c) for c in partner.values()) // 2
    assert repeats == _partner_floor(total, n, mixed_doubles=True)
    for p in players:
        counts = [partner[p].get(q, 0) for q in players
                  if genders[q] != genders[p]]
        assert max(counts) - min(counts) <= 1, (
            f"选手 {p} 的搭档次数分布不均匀：{sorted(counts)}"
        )

    # c) 每人对手总次数相等 + 无重复对局
    totals = [sum(opponents.get(_pair_key(p, q), 0)
                  for q in players if q != p) for p in players]
    assert set(totals) == {2 * target}, f"对手总次数不等：{sorted(set(totals))}"
    assert sum(v - 1 for v in matches.values()) == 0, "出现了完全相同的对局"


# ---- 7. 接口层：条件不满足就拒绝，不传就当普通双打（行为与改动前一致）----

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
    """按顺序返回预设结果的假库（与 test_play_gate / test_auto_finish_audit 同一套写法）。"""

    def __init__(self, results):
        self._results = [_Result(r) for r in results]
        self.count = 0

    async def execute(self, stmt, *args, **kwargs):
        self.count += 1
        if not self._results:
            raise IndexError("不该再查库了：调用顺序与预期不符")
        return self._results.pop(0)


def _start_tournament_fake_db(genders):
    """把开赛接口走到「性别校验」那一步需要的查询按顺序摆好。

    顺序（见 engine_api.start_tournament）：赛事行锁 → 被禁用的报名 → 有效报名
    → （仅混双）选手性别 → 场地数校验 …
    """
    from types import SimpleNamespace

    tournament = SimpleNamespace(
        id=7, creator_id=1, status=None, total_matches=None, plan_total_matches=None,
        schedule_mode="balanced", mixed_doubles=False,
    )
    regs = [SimpleNamespace(user_id=uid) for uid in genders]
    return tournament, _ScriptedDB([
        tournament,       # select(Tournament).with_for_update()
        [],               # 被禁用账号的报名（没有）
        regs,             # 有效报名
        list(genders.items()),   # select(User.id, User.gender)
        # 后面就是场地数校验了（这两条用例都走不到）
    ])


def test_start_rejects_mixed_when_genders_are_unbalanced():
    """5 男 3 女：点"开启混双"必须被拒绝，且提示要说清人数。"""
    import asyncio
    from types import SimpleNamespace

    from fastapi import HTTPException

    from app.api import engine_api
    from app.models.tournament import TournamentStatus

    genders = {i: ("M" if i <= 5 else "F") for i in range(1, 9)}
    tournament, db = _start_tournament_fake_db(genders)
    tournament.status = TournamentStatus.OPEN
    user = SimpleNamespace(id=1, role="user")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(engine_api.start_tournament(
            tournament_id=7,
            request=SimpleNamespace(headers={}),
            body=engine_api.StartRequest(mixed_doubles=True),
            user=user, db=db,
        ))
    assert exc.value.status_code == 400
    assert "5 男 3 女" in exc.value.detail
    assert tournament.mixed_doubles is False, "拒绝时不能把标记改成 true"


def test_start_rejects_mixed_when_someone_has_no_gender():
    """有人没填性别 → 也不能开（会被当成"既不是男也不是女"）。"""
    import asyncio
    from types import SimpleNamespace

    from fastapi import HTTPException

    from app.api import engine_api
    from app.models.tournament import TournamentStatus

    genders = {1: "M", 2: "M", 3: "F", 4: "F", 5: "M", 6: None, 7: "F", 8: "M"}
    tournament, db = _start_tournament_fake_db(genders)
    tournament.status = TournamentStatus.OPEN
    user = SimpleNamespace(id=1, role="user")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(engine_api.start_tournament(
            tournament_id=7,
            request=SimpleNamespace(headers={}),
            body=engine_api.StartRequest(mixed_doubles=True),
            user=user, db=db,
        ))
    assert exc.value.status_code == 400
    assert "未设置性别" in exc.value.detail


def test_start_without_mixed_does_not_query_genders():
    """不传 mixed_doubles（= 改动前的老客户端/老流程）：不许为此多查一次性别。

    用"最后一条被查的是场地数、且报的是场地错误"来证明流程原样走过，
    没被混双那段截住。
    """
    import asyncio
    from types import SimpleNamespace

    from fastapi import HTTPException

    from app.api import engine_api
    from app.models.tournament import TournamentStatus

    genders = {i: ("M" if i <= 4 else "F") for i in range(1, 9)}
    tournament, db = _start_tournament_fake_db(genders)
    db._results = [_Result(tournament), _Result([]), _Result(
        [SimpleNamespace(user_id=uid) for uid in genders])]
    # 场地数校验：10 片场地 + 8 人 → 必然拒绝（这是"没走混双分支"的标志）
    db._results.append(_Result(10))
    tournament.status = TournamentStatus.OPEN

    with pytest.raises(HTTPException) as exc:
        asyncio.run(engine_api.start_tournament(
            tournament_id=7,
            request=SimpleNamespace(headers={}),
            body=None,
            user=SimpleNamespace(id=1, role="user"), db=db,
        ))
    assert exc.value.status_code == 400
    assert "场地" in exc.value.detail
    assert db.count == 4, "只该查 4 次（赛事/禁用报名/报名/场地数），不该插进性别查询"

