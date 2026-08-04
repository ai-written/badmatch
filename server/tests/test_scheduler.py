"""engine/scheduler 单元测试。"""
from collections import Counter

import pytest

from app.engine.scheduler import (
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
