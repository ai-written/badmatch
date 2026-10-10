"""engine/referee_assign 单元测试（纯函数，不连库）。"""
import random
from collections import Counter

from app.engine.referee_assign import assign_referees, can_assign


# ---- 两种入口的权限规则（产品确认过，别改错）----

def test_current_scope_only_for_players_on_site():
    """对阵表那个按钮：只有本赛事在场选手；房主若不是在场选手也不行。"""
    kwargs = dict(user_id=9, role="user", creator_id=1, candidate_ids=[9, 10])
    assert can_assign("current", **kwargs) is True
    # 房主但不是在场选手
    assert can_assign("current", **dict(kwargs, user_id=1)) is False
    # 管理员/超管但不在场
    assert can_assign("current", **dict(kwargs, user_id=2, role="admin")) is False
    assert can_assign("current", **dict(kwargs, user_id=2, role="superadmin")) is False
    # 路人
    assert can_assign("current", **dict(kwargs, user_id=3)) is False


def test_all_scope_only_for_creator_or_admin():
    """详情页「一次性分配」：房主/管理员/超管；在场选手反而不行。"""
    kwargs = dict(user_id=9, role="user", creator_id=1, candidate_ids=[9, 10])
    assert can_assign("all", **kwargs) is False              # 普通在场选手
    assert can_assign("all", **dict(kwargs, user_id=1)) is True     # 房主（非选手）
    assert can_assign("all", **dict(kwargs, user_id=2, role="admin")) is True
    assert can_assign("all", **dict(kwargs, user_id=2, role="superadmin")) is True
    assert can_assign("all", **dict(kwargs, user_id=3)) is False    # 路人


def test_never_picks_a_player_of_that_match():
    """硬规则：本场 4 名选手不能被指派为本场裁判。"""
    matches = [(1, {1, 2, 3, 4}), (2, {5, 6, 7, 8})]
    for seed in range(50):
        picked = dict(assign_referees(matches, list(range(1, 13)), rng=random.Random(seed)))
        assert picked[1] not in {1, 2, 3, 4}, picked
        assert picked[2] not in {5, 6, 7, 8}, picked


def test_one_person_referees_at_most_one_match_per_round():
    """同一轮里不能把同一个人分到两场。"""
    matches = [(mid, {1, 2, 3, 4}) for mid in range(3)]
    for seed in range(50):
        picked = [uid for _mid, uid in assign_referees(matches, [10, 11, 12], rng=random.Random(seed))]
        assert sorted(picked) == [10, 11, 12], picked


def test_people_already_refereeing_this_round_are_excluded():
    matches = [(1, {1, 2, 3, 4})]
    # 9 已经在同轮另一场当裁判 → 只能选 10
    assert assign_referees(matches, [9, 10], busy_ids=[9], rng=random.Random(0)) == [(1, 10)]


def test_skips_match_when_nobody_is_available():
    # 在场就这 4 个人 → 没人能当裁判
    assert assign_referees([(1, {1, 2, 3, 4})], [1, 2, 3, 4]) == []
    # 只有 1 个场下人员、却有 2 场要分 → 第二场跳过
    picked = assign_referees([(1, {1, 2, 3, 4}), (2, {5, 6, 7, 8})], [9], rng=random.Random(0))
    assert picked == [(1, 9)], picked
    # 候选池里只有本场选手 → 跳过；多一个场下人员就能分出去
    assert assign_referees([(1, {1, 2, 3, 4, 5})], [1, 2, 3, 4, 5], rng=random.Random(0)) == []
    assert assign_referees([(1, {1, 2, 3, 4, 5})], [1, 2, 3, 4, 5, 6], rng=random.Random(0)) == [(1, 6)]


def test_is_truly_random_but_reproducible_with_a_seed():
    """真随机：多次运行会挑到不同的人；传固定种子时结果可复现。"""
    matches = [(1, {1, 2, 3, 4})]
    counts = Counter(
        assign_referees(matches, [5, 6, 7, 8], rng=random.Random(s))[0][1]
        for s in range(200)
    )
    assert set(counts) == {5, 6, 7, 8}, counts          # 每个人都可能被挑到
    assert max(counts.values()) < 200                   # 不是固定挑同一个

    assert (assign_referees(matches, [5, 6, 7, 8], rng=random.Random(7))
            == assign_referees(matches, [5, 6, 7, 8], rng=random.Random(7)))


def test_empty_input_and_empty_pool():
    assert assign_referees([], [1, 2, 3]) == []
    assert assign_referees([(1, {1, 2})], []) == []
