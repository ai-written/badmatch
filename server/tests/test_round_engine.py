"""轮级构造（多场地）单元测试。

盯的是一条硬底线：**任意规模、任意合法档位、任意随机种子下，
同一轮里的 4*per_round 名选手都必须互不相同** —— 两片场地同时开打时，
"同一个人同时在两场"是物理上做不到的。老引擎（逐场贪心）做不到这一点：
12 人 33 场实测每份赛程都有 4 轮撞人。
"""
import random
from collections import Counter

import pytest

from app.engine.scheduler import (
    _pair_key,
    compute_rounds,
    court_count_effective,
    fairness_step,
    generate_round_schedule,
    matches_per_round,
    use_round_engine,
)


# ---- 分流判定（纯函数，engine_api 就是按它选引擎）----

def test_use_round_engine_branches_on_courts_and_roster():
    """>=2 片场地 且 开赛时人数 >=9 才走轮级构造。"""
    assert use_round_engine(12, 2) is True
    assert use_round_engine(9, 2) is True
    assert use_round_engine(8, 2) is False      # 8 人 2 场地：不支持，仍走老引擎
    assert use_round_engine(12, 1) is False     # 单场地：老引擎冻结
    assert use_round_engine(12, 0) is False     # 没填场地号 = 1 片
    assert use_round_engine(4, 3) is False


def test_court_count_effective_takes_the_tightest_cap():
    """场地数 / 人数//4 / 人数//5 三条上限取最小；不够就降级而不是报错。"""
    assert court_count_effective(12, 2) == 2
    assert court_count_effective(10, 2) == 2      # 10//5 = 2，刚好够
    assert court_count_effective(9, 2) == 1       # 9//5 = 1 → 只能开一片（场下只 1 人）
    assert court_count_effective(9, 3) == 1
    assert court_count_effective(24, 3) == 3
    assert court_count_effective(16, 3) == 3      # 16//5 = 3
    assert court_count_effective(14, 3) == 2      # 14//5 = 2
    assert court_count_effective(7, 2) == 1
    assert court_count_effective(4, 2) == 0
    assert court_count_effective(12, 0) == 0


def test_matches_per_round_never_zero():
    """每轮场数至少 1 场（否则排程会空转）。"""
    for n in range(4, 41):
        for courts in (0, 1, 2, 3, 4):
            assert matches_per_round(n, courts) >= 1, (n, courts)
    assert matches_per_round(12, 2) == 2
    assert matches_per_round(9, 2) == 1


# ---- 硬底线：同轮互斥 ----

def _check_schedule(schedule, players, total_matches, per_round):
    n = len(players)
    target = 4 * total_matches // n
    rounds = compute_rounds(schedule, per_round)

    assert len(schedule) == total_matches, "总场次必须正好等于请求值"
    assert len(rounds) == -(-total_matches // per_round), "轮数必须正好覆盖全部场次"

    played = Counter()
    rest = Counter()
    for idx, rnd in enumerate(rounds):
        on = []
        for t1, t2, *_ in rnd:
            on += list(t1) + list(t2)
        assert len(set(on)) == len(on), (
            f"第 {idx + 1} 轮有人同时出现在两场：{sorted(on)}"
        )
        assert len(set(on)) == 4 * len(rnd), f"第 {idx + 1} 轮人数不对"
        for p in on:
            played[p] += 1
        for p in set(players) - set(on):
            rest[p] += 1

    for p in players:
        assert played[p] == target, f"选手 {p} 打了 {played[p]} 场，应为 {target}"

    rest_values = [rest[p] for p in players]
    assert max(rest_values) - min(rest_values) == 0, (
        f"轮空次数不齐平：{min(rest_values)}~{max(rest_values)}"
    )
    return rounds


@pytest.mark.parametrize("n, per_round", [
    (9, 2), (10, 2), (11, 2), (12, 2), (13, 2), (14, 2), (16, 2), (20, 2), (24, 2),
    (15, 3), (20, 3),
])
def test_no_player_plays_twice_in_the_same_round(n, per_round):
    """核心断言：任意规模 × 多档场次 × 多个随机种子，每一轮都不撞人。"""
    step = fairness_step(n)
    budgets = [step, step * 3, step * 5]
    for total in budgets:
        if total > 60:
            continue
        for seed in range(3):
            players = list(range(1, n + 1))
            random.Random(seed).shuffle(players)
            schedule = generate_round_schedule(players, total, per_round)
            _check_schedule(schedule, players, total, per_round)


def test_single_court_round_uses_only_four_players():
    """per_round=1（退赛降级到单场地）时每轮只上 4 人。"""
    players = list(range(1, 13))
    schedule = generate_round_schedule(players, 12, 1)
    rounds = _check_schedule(schedule, players, 12, 1)
    for rnd in rounds:
        assert len(rnd) == 1


def test_three_courts_twelve_players_never_share():
    """3 片场地 15 人：每轮 12 人上场、3 场互斥（走逐场贪心那条分支）。"""
    players = list(range(1, 16))
    schedule = generate_round_schedule(players, 15, 3)
    rounds = _check_schedule(schedule, players, 15, 3)
    for rnd in rounds:
        assert len(rnd) == 3


# ---- 可复现性 ----

def test_same_input_same_schedule():
    """同一份名单顺序 + 同一档位 → 永远排出同一份赛程（与老引擎同口径）。"""
    players = list(range(1, 13))
    a = generate_round_schedule(players, 36, 2)
    b = generate_round_schedule(players, 36, 2)
    assert a == b


def test_history_does_not_change_player_load():
    """带历史只影响"跟历史撞不撞"，不影响每人场次 / 同轮互斥。"""
    players = list(range(1, 13))
    partner_history = Counter()
    for p in players:                      # 造一份"全都搭档过"的历史
        for q in players:
            if p != q:
                partner_history[_pair_key(p, q)] = 3
    schedule = generate_round_schedule(players, 12, 2, partner_history=partner_history)
    _check_schedule(schedule, players, 12, 2)


# ---- 非法输入必须显式失败，不准静默少排 ----

def test_invalid_total_matches_raises():
    players = list(range(1, 13))
    with pytest.raises(ValueError):
        generate_round_schedule(players, 14, 2)   # 14 场 12 人 → 每人场次不相等
    with pytest.raises(ValueError):
        generate_round_schedule(players, 0, 2)
    with pytest.raises(ValueError):
        generate_round_schedule(players, 12, 0)


def test_too_few_players_raises():
    with pytest.raises(ValueError):
        generate_round_schedule([1, 2, 3], 3, 2)


# ---- 与 compute_rounds 的约定：扁平顺序就是轮的顺序 ----

def test_flat_schedule_is_already_ordered_by_round():
    """调用方仍用 compute_rounds(schedule, per_round) 切轮，所以顺序必须天然分好组。"""
    players = list(range(1, 13))
    schedule = generate_round_schedule(players, 36, 2)
    rounds = compute_rounds(schedule, 2)
    # 前 N-1 轮必须满 2 场；且每轮的 8 人互异（已在 _check_schedule 里校验）
    assert all(len(r) == 2 for r in rounds[:-1])
    assert len(rounds[-1]) in (1, 2)


def test_round_engine_quality_is_reasonable():
    """12 人 36 场：搭档重复应当贴着理论下限走（不是随便排出来的）。"""
    players = list(range(1, 13))
    schedule = generate_round_schedule(players, 36, 2)
    partners = Counter()
    for t1, t2, *_ in schedule:
        partners[_pair_key(*t1)] += 1
        partners[_pair_key(*t2)] += 1
    repeats = sum(v - 1 for v in partners.values())
    floor = max(0, 2 * 36 - 12 * 11 // 2)
    assert repeats <= floor + 4, f"搭档重复 {repeats} 次，离理论下限 {floor} 太远"
