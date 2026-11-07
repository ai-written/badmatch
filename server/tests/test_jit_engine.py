"""「优先场地」模式的对局挑选（纯逻辑）单测。

盯的是三条与产品约定一致的行为：
1. 凑不齐 4 个**空闲**的人 / 都打满了 → 不排（场地宁可空着，也不硬塞已打满的人）；
2. 先补场次落后的人（最少的一档；不够 4 个才放宽一档 = "允许差 1 场"）；
3. 同档内避开重复搭档/重复对局（与事前排程同一套权重）。
"""
from collections import Counter

from app.engine.jit import pick_court_first_match
from app.engine.scheduler import _pair_key


def _played(**kw):
    return {int(k[1:]): v for k, v in kw.items()}


def test_returns_none_when_fewer_than_four_free():
    assert pick_court_first_match([1, 2, 3], {1: 0, 2: 0, 3: 0}, 5) is None


def test_returns_none_when_everyone_reached_target():
    free = list(range(1, 9))
    played = {p: 5 for p in free}
    assert pick_court_first_match(free, played, 5) is None


def test_prefers_players_with_fewest_matches():
    """场次少的一档优先：已经打了 2 场的人不该被排上。"""
    free = list(range(1, 9))
    played = {1: 0, 2: 0, 3: 0, 4: 1, 5: 2, 6: 2, 7: 2, 8: 2}
    t1, t2 = pick_court_first_match(free, played, 5)
    assert set(t1 + t2) == {1, 2, 3, 4}


def test_relaxes_by_one_when_lowest_tier_is_too_small():
    """最少的一档只有 1 个人 → 放宽一档（这就是"每人场次允许差 1 场"）。"""
    free = [1, 2, 3, 4, 5]
    played = {1: 0, 2: 1, 3: 1, 4: 1, 5: 1}
    t1, t2 = pick_court_first_match(free, played, 5)
    assert 1 in t1 + t2, "场次最少的那个人必须被排上"


def test_avoids_repeating_a_partner():
    """同档内避开重复搭档（搭档权重 3，比对手 2 大）。"""
    free = list(range(1, 9))
    played = {p: 0 for p in free}
    partner = Counter({_pair_key(1, 2): 5})
    t1, t2 = pick_court_first_match(free, played, 5, partner_count=partner)
    assert _pair_key(*t1) != _pair_key(1, 2) and _pair_key(*t2) != _pair_key(1, 2)


def test_avoids_an_identical_match():
    """已经打过的"同一 4 人 + 同一分组"不该原样再来（重复对局权重最高 20）。"""
    free = list(range(1, 9))
    played = {p: 0 for p in free}
    # 先把 1/2 vs 3/4 记成"打过 3 次"，其余都是 0 → 不该再排出这一组
    match_c = Counter()
    from app.engine.scheduler import _match_key
    match_c[_match_key(_pair_key(1, 2), _pair_key(3, 4))] = 3
    t1, t2 = pick_court_first_match(free, played, 5, match_count=match_c)
    assert not (set(t1 + t2) == {1, 2, 3, 4} and {_pair_key(*t1), _pair_key(*t2)}
                == {_pair_key(1, 2), _pair_key(3, 4)})


def test_returns_two_sorted_teams():
    free = list(range(1, 9))
    played = {p: 0 for p in free}
    t1, t2 = pick_court_first_match(free, played, 5)
    assert t1[0] < t1[1] and t2[0] < t2[1], "每队内部按 (小 id, 大 id) 排列"
    assert len(set(t1 + t2)) == 4


# ---- 并列时的随机（开局所有人 0 场时，全靠这一条避免"永远按名单顺序"）----

def test_deterministic_without_rng():
    """不传 rng 就完全确定（单测/排障要稳定复现）。"""
    free = list(range(1, 13))
    played = {p: 0 for p in free}
    first = pick_court_first_match(free, played, 5)
    for _ in range(5):
        assert pick_court_first_match(free, played, 5) == first


def test_rng_breaks_ties_so_the_first_batch_is_not_the_roster_order():
    """传了 rng 之后，全员并列时不该永远是「名单前 4 个 vs 接着 4 个」。

    这是实测踩过的坑：12 人开赛永远是 测试1/2 vs 3/4、5/6 vs 7/8，
    因为所有人场次都是 0、没有任何历史 → 所有组合得分完全相同，取了第一个。
    """
    import random as _random
    free = list(range(1, 13))
    played = {p: 0 for p in free}
    seen = set()
    rng = _random.Random(7)
    for _ in range(40):
        t1, t2 = pick_court_first_match(free, played, 5, rng=rng)
        seen.add((t1, t2))
    assert len(seen) > 1, f"并列时应当随机，结果却只有一种：{seen}"
    # 且"名单顺序"那种组合不该垄断（0 次不可能，但也不该是唯一结果）
    assert len(seen) >= 2


def test_random_choice_still_respects_the_quota_tier():
    """随机只在**并列最优**之间挑，不会把场次多的人排上来。"""
    import random as _random
    free = [1, 2, 3, 4, 5, 6, 7, 8]
    played = {1: 0, 2: 0, 3: 0, 4: 0, 5: 3, 6: 3, 7: 3, 8: 3}
    rng = _random.Random(1)
    for _ in range(20):
        t1, t2 = pick_court_first_match(free, played, 5, rng=rng)
        assert set(t1 + t2) == {1, 2, 3, 4}


# ---- 模式落库（1 片场地必须回落，否则界面会骗人）----

def test_schedule_mode_falls_back_for_single_court():
    from app.api.tournaments import _resolve_schedule_mode
    assert _resolve_schedule_mode("court_first", ["A1"]) == "balanced"
    assert _resolve_schedule_mode(None, ["A1"]) == "balanced"


def test_schedule_mode_defaults_to_court_first_for_multi_court():
    from app.api.tournaments import _resolve_schedule_mode
    assert _resolve_schedule_mode("court_first", ["A1", "A2"]) == "court_first"
    # 不传（老客户端 / 老脚本）也按新默认走
    assert _resolve_schedule_mode(None, ["A1", "A2"]) == "court_first"


def test_schedule_mode_keeps_explicit_balanced():
    from app.api.tournaments import _resolve_schedule_mode
    assert _resolve_schedule_mode("balanced", ["A1", "A2"]) == "balanced"
