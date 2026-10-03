"""engine/scheduler 单元测试。"""
from collections import Counter

import pytest

from app.engine.scheduler import (
    _match_key,
    _pair_key,
    _quality,
    compute_match_count,
    compute_rounds,
    generate_schedule,
)


def test_compute_match_count_divisibility():
    for n in range(4, 41):
        M = compute_match_count(n)
        assert (4 * M) % n == 0, f"n={n}, M={M}"


def test_schedule_validity_and_equal_play():
    players = list(range(8))
    M = compute_match_count(len(players))
    schedule = generate_schedule(players, M)

    assert len(schedule) == M
    played = Counter()
    for (pa, pb), (pc, pd), *_ in schedule:
        four = {pa, pb, pc, pd}
        assert len(four) == 4, "每场比赛必须是 4 名不同选手"
        assert four <= set(players), "选手必须来自参赛名单"
        played[pa] += 1
        played[pb] += 1
        played[pc] += 1
        played[pd] += 1

    counts = set(played[p] for p in players)
    assert len(counts) == 1, f"每人场次必须相等: {dict(played)}"
    target = (4 * M) // len(players)
    assert counts == {target}


def test_schedule_uneven_player_count():
    for n in range(4, 31):
        players = list(range(n))
        M = compute_match_count(n)
        schedule = generate_schedule(players, M)
        assert len(schedule) == M
        played = Counter()
        for (pa, pb), (pc, pd), *_ in schedule:
            played[pa] += 1
            played[pb] += 1
            played[pc] += 1
            played[pd] += 1
        counts = set(played[p] for p in players)
        assert len(counts) == 1


def _pairing_repeats(schedule) -> int:
    """统计重复搭档的出现次数（同一组合出现第二次起计）。"""
    seen = set()
    repeats = 0
    for (pa, pb), (pc, pd), *_ in schedule:
        for pair in ((pa, pb), (pc, pd)):
            if pair in seen:
                repeats += 1
            else:
                seen.add(pair)
    return repeats


def test_partner_diversity_with_history():
    players = list(range(6))
    M = compute_match_count(len(players))
    first = generate_schedule(players, M)

    partner_history = Counter()
    for (pa, pb), (pc, pd), *_ in first:
        partner_history[(pa, pb)] += 1
        partner_history[(pb, pa)] += 1
        partner_history[(pc, pd)] += 1
        partner_history[(pd, pc)] += 1

    # 不携带历史时重排
    no_history = generate_schedule(players, M)
    # 携带历史时重排，应尽量复用更少的旧搭档
    with_history = generate_schedule(players, M, partner_history)

    assert _pairing_repeats(with_history) <= _pairing_repeats(no_history)


def test_history_does_not_break_equal_play():
    players = list(range(8))
    M = compute_match_count(len(players))
    partner_history = {(players[i], players[i + 1]): 1 for i in range(0, 7, 2)}
    schedule = generate_schedule(players, M, partner_history)
    assert len(schedule) == M
    played = Counter()
    for (pa, pb), (pc, pd), *_ in schedule:
        played[pa] += 1
        played[pb] += 1
        played[pc] += 1
        played[pd] += 1
    counts = set(played[p] for p in players)
    assert len(counts) == 1


def test_compute_rounds_groups_by_2():
    schedule = [f"m{i}" for i in range(10)]
    rounds = compute_rounds(schedule, 2)
    assert len(rounds) == 5
    assert all(len(r) == 2 for r in rounds)


# ---- 公平性回归：修复前 8 人 22 场必然出现一对完全相同的对局 ----

def _match_sig(match) -> tuple:
    """测试侧的对局签名：4 名选手 + 分组方式（不含左右顺序）。

    名字不能叫 _match_key —— 那会和 scheduler 里同名实现撞车，把导入的实现覆盖掉。
    """
    (a, b), (c, d), *_ = match
    return tuple(sorted([tuple(sorted((a, b))), tuple(sorted((c, d)))]))


def _match_sigs(schedule) -> list:
    return [_match_sig(m) for m in schedule]


def test_schedule_is_deterministic():
    """同一批人、同样顺序必须排出同一份赛程（第 0 次尝试不洗牌，重试也是固定种子）。"""
    assert generate_schedule(list(range(8)), 22) == generate_schedule(list(range(8)), 22)


def test_no_identical_match_in_long_schedule():
    """8 人 22 场：修复前 300 种报名顺序下**每种**都恰好出现 1 对完全相同的对局。"""
    schedule = generate_schedule(list(range(8)), 22)
    keys = _match_sigs(schedule)
    dup = [k for k, v in Counter(keys).items() if v > 1]
    assert not dup, f"出现完全相同的对局：{dup}"


@pytest.mark.parametrize("n,M", [(8, 20), (8, 28), (8, 40), (8, 16), (8, 12), (5, 15), (6, 12), (7, 21), (9, 18), (12, 33)])
def test_no_identical_match_across_sizes(n, M):
    schedule = generate_schedule(list(range(n)), M)
    assert len(schedule) == M
    keys = _match_sigs(schedule)
    dup = [k for k, v in Counter(keys).items() if v > 1]
    assert not dup, f"{n}人{M}场出现完全相同的对局：{dup}"


def test_partner_diversity_reaches_theoretical_floor():
    """搭档多样性不能被"对手不重复"挤掉：8 人 22 场的重复搭档数应正好是理论下限。

    44 个搭档槽位只有 28 种可能搭档，因此至少 16 次重复，不可能更少。
    """
    players = list(range(8))
    schedule = generate_schedule(players, 22)
    seen = set()
    repeats = 0
    for (a, b), (c, d), *_ in schedule:
        for pair in (tuple(sorted((a, b))), tuple(sorted((c, d)))):
            if pair in seen:
                repeats += 1
            else:
                seen.add(pair)
    assert repeats == 2 * 22 - 8 * 7 // 2 == 16, repeats


def test_opponent_meetings_are_even():
    """同一对对手碰面次数应压到理论下限：8 人 22 场时每人 11 场 = 22 个对手位分给 7 人 → ≥4。

    修复前实测最多 5 次。
    """
    schedule = generate_schedule(list(range(8)), 22)
    meetings = Counter()
    for (a, b), (c, d), *_ in schedule:
        for u in (a, b):
            for v in (c, d):
                meetings[tuple(sorted((u, v)))] += 1
    assert max(meetings.values()) <= 4, meetings.most_common(3)


def test_match_key_is_order_insensitive():
    """对局键必须在**两队之间**也排序。

    4 个人在 eligible 中的相对顺序会随 cool_down 变化，同一场比赛可能以
    ((1,2),(3,4)) 或 ((3,4),(1,2)) 两种形态出现；不排序会让去重惩罚漏判。
    """
    assert _match_key((1, 2), (3, 4)) == _match_key((3, 4), (1, 2))
    # 队内顺序由 _pair_key 负责（_splits 里已调用），两层合起来才是完整的规范键
    assert _match_key(_pair_key(2, 1), _pair_key(4, 3)) == _match_key((1, 2), (3, 4))


def test_quality_counts_duplicate_with_swapped_team_order():
    """_quality 也必须把「两队顺序相反」的同一场比赛算作重复（两边口径必须一致）。"""
    dup = [((1, 2), (3, 4), None, None), ((3, 4), (1, 2), None, None)]
    assert _quality(dup)[0] == 1
    distinct = [((1, 2), (3, 4), None, None), ((1, 3), (2, 4), None, None)]
    assert _quality(distinct)[0] == 0
