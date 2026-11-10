"""core/ranking 单元测试：赛事内名次口径。

这几条被钉住，是因为积分榜页和「参赛排名趋势图」共用它 ——
任何一条算错，两处会同时给出错误的（但至少是一致的）名次，
或者在两处给出不同的名次，后者更难查。
"""
import pytest

from app.core.ranking import assign_ranks, rank_key


def entry(user_id, won, pf=0, pa=0, active=True):
    """(user_id, 胜场, 得分, 失分, 是否在赛)"""
    return (user_id, won, pf, pa, active)


def test_rank_key_orders_by_wins_then_point_diff_then_user_id():
    # 胜场多的在前
    assert rank_key(2, 5, 0, 0) < rank_key(1, 3, 0, 0)
    # 胜场相同看净胜分
    assert rank_key(2, 5, 60, 20) < rank_key(1, 5, 55, 30)
    # 胜场、净胜分都相同（都是 0）时按 user_id 升序兜底
    assert rank_key(1, 5, 40, 20) < rank_key(2, 5, 40, 20)


def test_assign_ranks_full_order():
    """8 人：两人 5 胜（净胜分分先后）、一人 4 胜、其余 3 胜及以下。"""
    stats = [
        entry(7, 3, 60, 50),
        entry(3, 5, 70, 40),
        entry(1, 5, 80, 40),   # 净胜分 40，比 3 号的 30 高 → 第 1
        entry(9, 4, 55, 40),
        entry(2, 3, 60, 50),   # 与 7 号胜场、净胜分都相同 → user_id 小的在前
        entry(5, 1, 30, 70),
    ]
    got = assign_ranks(stats)
    assert [uid for uid, _ in got] == [1, 3, 9, 2, 7, 5]
    assert [rank for _, rank in got] == [1, 2, 3, 4, 5, 6]


def test_dropped_players_do_not_take_a_rank():
    """退赛的人排最后、rank=0，且**不占名次**——后面的人不顺延。

    这是产品口径：退赛前成绩照常显示，但他不能"占着第 2 名"。
    """
    stats = [
        entry(1, 5, 50, 30),
        entry(2, 4, 40, 30, active=False),   # 退赛，战绩比 3 号好
        entry(3, 3, 30, 40),
    ]
    got = assign_ranks(stats)
    assert got == [(1, 1), (3, 2), (2, 0)]


def test_all_dropped_gives_all_zero():
    assert assign_ranks([entry(1, 5, active=False), entry(2, 0, active=False)]) == [
        (1, 0), (2, 0),   # 仍按战绩排序，只是名次都是 0
    ]


def test_empty_input():
    assert assign_ranks([]) == []


def test_zero_played_players_rank_by_user_id():
    """整场都没打完（提前结束）时全是 0 胜：名次按 user_id 排，不炸。

    前端会在提示里标「0 场」，但名次本身和积分榜保持一致。
    """
    got = assign_ranks([entry(5, 0), entry(2, 0), entry(9, 0)])
    assert got == [(2, 1), (5, 2), (9, 3)]


@pytest.mark.parametrize("n", range(1, 13))
def test_ranks_are_contiguous_and_bounded(n):
    """在赛选手的名次恒为 1..n 且不重复（折线图按 rank 归一化，越界会画到框外）。"""
    stats = [entry(i, won=(i * 3) % 5, pf=i, pa=n - i) for i in range(1, n + 1)]
    ranks = [rank for _, rank in assign_ranks(stats) if rank > 0]
    assert sorted(ranks) == list(range(1, n + 1))
