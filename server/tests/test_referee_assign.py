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


def test_all_scope_allows_creator_admin_and_on_site_players():
    """详情页「一次性分配」：房主/管理员/超管 + 本赛事在场选手（普通选手也能点）。"""
    kwargs = dict(user_id=9, role="user", creator_id=1, candidate_ids=[9, 10])
    assert can_assign("all", **kwargs) is True                      # 普通在场选手
    assert can_assign("all", **dict(kwargs, user_id=1)) is True     # 房主（非选手）
    assert can_assign("all", **dict(kwargs, user_id=2, role="admin")) is True
    assert can_assign("all", **dict(kwargs, user_id=2, role="superadmin")) is True
    assert can_assign("all", **dict(kwargs, user_id=3)) is False    # 路人
    # 退赛之后就出局了（候选人池里没有他），管理者身份仍然有效
    assert can_assign("all", **dict(kwargs, candidate_ids=[10])) is False
    assert can_assign("all", **dict(kwargs, user_id=2, role="admin", candidate_ids=[10])) is True


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
    """不传 served（纯随机那条路）：每个人都会被挑到，传固定种子结果可复现。"""
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


# ---- 均衡轮转 + 并列随机（取代"纯随机"）----

def test_served_only_picks_from_the_least_used_tier():
    """均衡：只有"已安排场次最少"的那一档会被挑到，次数高的那一档一次都轮不到。"""
    matches = [(1, {1, 2, 3, 4})]
    served = {5: 0, 6: 3, 7: 3, 8: 3}          # 只有 5 还没吹过
    for seed in range(50):
        assert assign_referees(matches, [5, 6, 7, 8], served=served,
                               rng=random.Random(seed)) == [(1, 5)]


def test_ties_are_broken_randomly():
    """并列者之间仍是等概率随机 —— 所以它还是"随机分配"，只是把次数拉平。"""
    matches = [(1, {1, 2, 3, 4})]
    served = {5: 2, 6: 2, 7: 2, 8: 2}
    picked = Counter(
        assign_referees(matches, [5, 6, 7, 8], served=served, rng=random.Random(s))[0][1]
        for s in range(200)
    )
    assert set(picked) == {5, 6, 7, 8}, picked        # 四个人都可能被挑到
    assert max(picked.values()) < 200                 # 不是固定挑同一个


def test_without_served_it_is_still_pure_random():
    """不传 served（老调用方）必须与改动前完全一致：没有"次数"这层优先级。"""
    matches = [(1, {1, 2, 3, 4})]
    assert (assign_referees(matches, [5, 6, 7, 8], served={}, rng=random.Random(3))
            == assign_referees(matches, [5, 6, 7, 8], rng=random.Random(3)))


def test_served_never_overrides_the_hard_rules():
    """均衡不能压过硬规则：本场选手、同一批已在执裁的人永远不进候选池。"""
    matches = [(1, {1, 2, 3, 4}), (2, {5, 6, 7, 8})]
    # 把 1~4 的计数压到最低也没用：他们是第 1 场的选手；第 2 场只能在 1~4 里挑
    served = {1: 0, 2: 0, 3: 0, 4: 0, 5: 9, 6: 9, 7: 9, 8: 9}
    picked = dict(assign_referees(matches, [1, 2, 3, 4, 5, 6, 7, 8], served=served,
                                  rng=random.Random(0)))
    assert picked[1] in {5, 6, 7, 8}, picked
    assert picked[2] in {1, 2, 3, 4}, picked
    assert picked[1] != picked[2], picked


def test_forbidden_keeps_the_just_released_referee_off_that_match():
    """刚在这场上卸任的人不会被自动分回同一场（否则均衡会把他立刻送回来）。"""
    matches = [(1, {1, 2, 3, 4})]
    served = {5: 0, 6: 0, 7: 9, 8: 9}          # 5、6 次数最少
    picked = {assign_referees(matches, [5, 6, 7, 8], served=served,
                              forbidden={1: {5}}, rng=random.Random(s))[0][1]
              for s in range(60)}
    assert picked == {6}, picked               # 5 明确说了不行 → 只能 6

    # 整批都被禁 → 跳过该场，而不是硬塞一个
    assert assign_referees(matches, [5, 6], served=served,
                           forbidden={1: {5, 6}}, rng=random.Random(0)) == []


def test_forbidden_is_per_match_not_global():
    """只禁止"这一场"：卸任过第 1 场的人照样可以吹第 2 场。"""
    matches = [(1, {1, 2, 3, 4}), (2, {5, 6, 7, 8})]
    picked = dict(assign_referees(matches, [1, 2, 3, 4, 5, 6, 7, 8], served={},
                                  forbidden={1: {5}}, rng=random.Random(0)))
    assert picked[1] in {6, 7, 8}              # 第 1 场排除 5
    assert picked[2] in {1, 2, 3, 4}           # 第 2 场不受影响


def test_served_is_read_only():
    """served 只读：跨批次的累加必须由调用方显式做（否则均衡会静默失效，这里钉住契约）。"""
    served = {5: 0, 6: 1}
    assign_referees([(1, {1, 2, 3, 4})], [5, 6], served=served, rng=random.Random(0))
    assert served == {5: 0, 6: 1}


def test_served_balances_over_a_whole_event():
    """整局视角：8 人 14 场（7 批 × 2 场），按批继续累加计数 → 每人 1~2 场，没人轮空。"""
    matches = [
        (1, {1, 2, 3, 4}), (2, {5, 6, 7, 8}),
        (3, {1, 2, 5, 6}), (4, {3, 4, 7, 8}),
        (5, {1, 3, 5, 7}), (6, {2, 4, 6, 8}),
        (7, {1, 4, 5, 8}), (8, {2, 3, 6, 7}),
        (9, {1, 2, 7, 8}), (10, {3, 4, 5, 6}),
        (11, {1, 6, 3, 8}), (12, {2, 5, 4, 7}),
        (13, {1, 8, 6, 3}), (14, {2, 7, 5, 4}),
    ]
    candidates = list(range(1, 9))
    for seed in range(30):
        rng = random.Random(seed)
        served: dict[int, int] = {}
        per_round: list[list[int]] = []
        for i in range(0, len(matches), 2):
            batch = matches[i:i + 2]
            picks = assign_referees(batch, candidates, served=served, rng=rng)
            per_round.append([uid for _mid, uid in picks])
            for _mid, uid in picks:
                served[uid] = served.get(uid, 0) + 1
        assert len(per_round) == 7
        assert all(len(set(r)) == len(r) for r in per_round)      # 同一批不重复
        assert sum(served.values()) == 14                          # 14 场都分出去了
        assert set(served) == set(candidates)                      # 没人整场轮空
        # 极差 ≤2：14 个名额分给 8 人，均值 1.75，理论上最好只能是「6 人 2 场 + 2 人 1 场」；
        # 偶发 2 是因为某一批的候选池恰好都已经被安排过了（池子只有另外 4 个人）
        assert max(served.values()) - min(served.values()) <= 2, served
