"""engine/scheduler 单元测试。"""
import random
from collections import Counter

import pytest

from app.engine.scheduler import (
    _match_key,
    _pair_key,
    _quality,
    compute_match_count,
    compute_rounds,
    fairness_step,
    generate_schedule,
    shuffle_sides,
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


# ---- 赛中追加比赛：把已有赛程的搭档/对手/对局喂给排程器当惩罚 ----

def test_fairness_step_divides_slots_evenly_and_is_minimal():
    """追加场次的步长：4K 必须能被 N 整除，且 step 是最小的那个 K。"""
    for n in range(4, 13):
        step = fairness_step(n)
        assert (4 * step) % n == 0, f"n={n} step={step}：每人场次没法均分"
        assert all((4 * k) % n != 0 for k in range(1, step)), f"n={n} 的步长不是最小"


def test_generate_schedule_empty_history_matches_no_history():
    """传空历史 == 不传历史：老调用方（开赛、退赛重排）的结果一点都不能变。"""
    players = list(range(8))
    assert generate_schedule(players, 22) == generate_schedule(players, 22, {}, {}, {})


def test_new_batch_avoids_repeating_existing_matchups():
    """追加的比赛不能与已有赛程出现完全相同的对局（同 4 人 + 同分组）。"""
    players = list(range(8))
    first = generate_schedule(players, compute_match_count(8))
    match_history = Counter(_match_sig(m) for m in first)

    second = generate_schedule(players, fairness_step(8), {}, {}, match_history)

    overlap = [k for k in (_match_sig(m) for m in second) if match_history.get(k)]
    assert not overlap, f"追加的比赛与已有对局重复：{overlap}"


def test_new_batch_avoids_repeating_partnerships():
    """搭档历史同样要生效：追加的场次不该把已经合作过的搭档再配一遍。"""
    players = list(range(8))
    first = generate_schedule(players, 2)
    partner_history = Counter()
    for (a, b), (c, d), *_ in first:
        for pair in (_pair_key(a, b), _pair_key(c, d)):
            partner_history[pair] += 1
            partner_history[pair[::-1]] += 1

    second = generate_schedule(players, 2, partner_history)

    repeated = [
        pair
        for (a, b), (c, d), *_ in second
        for pair in (_pair_key(a, b), _pair_key(c, d))
        if partner_history.get(pair)
    ]
    assert not repeated, f"追加的比赛重复了搭档：{repeated}"


def test_new_batch_keeps_equal_play_with_history():
    """带历史排追加场次，每人场次仍然必须完全相等（人数不整除 4 的组合也要成立）。"""
    for n in (6, 7, 9, 12):
        players = list(range(n))
        step = fairness_step(n)
        first = generate_schedule(players, step * 2)
        partner_history = Counter()
        opponent_history = Counter()
        match_history = Counter()
        for (a, b), (c, d), *_ in first:
            t1, t2 = _pair_key(a, b), _pair_key(c, d)
            for pair in (t1, t2):
                partner_history[pair] += 1
                partner_history[pair[::-1]] += 1
            match_history[_match_key(t1, t2)] += 1
            for u in t1:
                for v in t2:
                    opponent_history[_pair_key(u, v)] += 1

        second = generate_schedule(players, step, partner_history,
                                   opponent_history, match_history)

        played = Counter()
        for (a, b), (c, d), *_ in first + second:
            for p in (a, b, c, d):
                played[p] += 1
        assert len(played) == n, f"n={n}：有人一场没打 {sorted(set(players) - set(played))}"
        assert len(set(played.values())) == 1, f"n={n} 带历史追加后场次不等：{dict(played)}"


def _history_of(schedules) -> tuple:
    """把一份或多份赛程统计成 (搭档/对手/对局) 三个 Counter —— 与
    engine_api._load_history 同一口径（搭档两个方向各记一次）。"""
    partner_history: Counter = Counter()
    opponent_history: Counter = Counter()
    match_history: Counter = Counter()
    for schedule in schedules:
        for (a, b), (c, d), *_ in schedule:
            t1, t2 = _pair_key(a, b), _pair_key(c, d)
            for pair in (t1, t2):
                partner_history[pair] += 1
                partner_history[pair[::-1]] += 1
            match_history[_match_key(t1, t2)] += 1
            for u in t1:
                for v in t2:
                    opponent_history[_pair_key(u, v)] += 1
    return partner_history, opponent_history, match_history


def test_cross_event_history_avoids_same_matchups():
    """跨赛事避重：把「上一场赛事」的统计当历史，新赛事不该再排出一模一样的对局。

    这是同一群体每周固定打球的真实用法。注意搭档必然重复（8 人 14 场搭档组合只有
    C(8,2)=28 种、一周用满），能且只能避开的是「同 4 人 + 同分组」再来一次。
    """
    for n, m in ((8, 14), (9, 18), (12, 33)):
        players = list(range(n))
        prev = generate_schedule(players, m)
        partner_history, opponent_history, match_history = _history_of([prev])

        # 报名顺序变了（先报名的人换了）也要避开上一场
        nxt = generate_schedule(list(reversed(players)), m, partner_history,
                                opponent_history, match_history)

        repeated = set(_match_sigs(nxt)) & set(_match_sigs(prev))
        assert not repeated, f"n={n}：新赛事重复了上一场的对局 {repeated}"
        played = Counter(p for (a, b), (c, d), *_ in nxt for p in (a, b, c, d))
        assert len(played) == n and len(set(played.values())) == 1, \
            f"n={n}：带跨赛事历史后每人场次不等 {dict(played)}"


def test_cross_event_history_covers_the_last_three_events():
    """服务端往前看 RECENT_EVENT_HISTORY=3 场：这 3 场用过的对局都不该出现在第 4 场。"""
    players = list(range(8))
    recent = [generate_schedule(players, 14)]
    for _ in range(2):                      # 后两场都带着"更早几场"的历史排出来
        partner_history, opponent_history, match_history = _history_of(recent)
        recent.append(generate_schedule(players, 14, partner_history,
                                        opponent_history, match_history))

    partner_history, opponent_history, match_history = _history_of(recent)
    nxt = generate_schedule(players, 14, partner_history, opponent_history, match_history)

    used = set()
    for s in recent:
        used |= set(_match_sigs(s))
    assert not (set(_match_sigs(nxt)) & used), \
        f"最近 3 场用过的对局不该再出现：{set(_match_sigs(nxt)) & used}"


def test_history_dead_end_has_no_history_fallback():
    """5 人 5 场 + 两场同样的历史：带历史会走死路，而**不带历史能排出来**。

    这条不是"算法应该这样"，而是**调用点兜底**的依据：`generate_schedule` 带历史时
    可能一个组合都排不出来（历史只改打分，但会改变贪心的选择路径），所以开赛 / 追加比赛 /
    重排都得在排不出来时退到更少的历史再试同一档，而不是把用户选的场次改大或直接 400。
    算法将来变强、这里不再走死路时，本用例自动退化成"两边都能排出来"，不会误报。
    """
    players = [1, 2, 3, 4, 5]
    histories = _history_of([
        generate_schedule(players, 5),
        generate_schedule([3, 1, 2, 4, 5], 5),
    ])
    try:
        generate_schedule(players, 5, *histories)
        dead_end = False
    except ValueError:
        dead_end = True

    if dead_end:
        # 兜底的依据：不带历史（= 加跨赛事避重之前的行为）必须能排出来，且仍然合法
        schedule = generate_schedule(players, 5)
        played = Counter(p for (a, b), (c, d), *_ in schedule for p in (a, b, c, d))
        assert len(schedule) == 5 and len(played) == 5 and set(played.values()) == {4}


# ---- 落库前的左右随机翻边：抹掉「名单第一人永远站左队」 ----

def test_shuffle_sides_only_swaps_the_two_teams():
    """翻边只改 A/B 队归属：对阵双方、场次顺序、场地/时间槽都必须原样。"""
    schedule = generate_schedule(list(range(8)), 22)
    shuffled = shuffle_sides(schedule, random.Random(20261010))

    assert len(shuffled) == len(schedule)
    for (t1, t2, court, slot), (u1, u2, court2, slot2) in zip(schedule, shuffled):
        assert {t1, t2} == {u1, u2}, "对阵双方必须保持不变"
        assert (u1, u2) in ((t1, t2), (t2, t1)), "只能整队对调，不能拆散搭档"
        assert (court, slot) == (court2, slot2), "场地/时间槽不能被翻边改掉"


def test_shuffle_sides_flips_some_but_not_all():
    """既不能全翻也不能全不翻 —— 两者都是另一种「固定」。"""
    schedule = generate_schedule(list(range(8)), 22)
    shuffled = shuffle_sides(schedule, random.Random(1))
    flipped = sum(1 for a, b in zip(schedule, shuffled) if (b[0], b[1]) == (a[1], a[0]))
    assert 0 < flipped < len(schedule), f"翻边数异常：{flipped}/{len(schedule)}"


def test_shuffle_sides_removes_first_player_left_bias():
    """名单第一人不再永远站左队；多种子平均后应接近 50%。

    不翻边时这个比例是 100%（见 scheduler.shuffle_sides 的说明），所以这里看的是
    「平均」而不是单次：单次掷硬币本来就可能偏，重要的是不再有系统性偏置。
    """
    players = list(range(8))
    schedule = generate_schedule(players, 22)
    left = plays = 0
    for seed in range(200):
        for (t1, t2, *_rest) in shuffle_sides(schedule, random.Random(seed)):
            if players[0] in t1 or players[0] in t2:
                plays += 1
            if players[0] in t1:
                left += 1
    ratio = left / plays
    assert 0.45 <= ratio <= 0.55, f"名单第一人站左队仍系统性偏置：{ratio:.3f}（{left}/{plays}）"


def test_shuffle_sides_edge_cases():
    """空赛程不炸；rng 缺省时用模块级 random，结果仍是合法对阵。"""
    assert shuffle_sides([]) == []
    schedule = generate_schedule(list(range(6)), 3)
    for (u1, u2, *_), (t1, t2, *_) in zip(schedule, shuffle_sides(schedule)):
        assert {u1, u2} == {t1, t2}

