"""
赛程引擎: 贪心构造 + 多方案择优 + 换序重试

注意用词：这里**没有回溯**。贪心一次成型，走不通时靠打乱候选顺序重试
（见 generate_schedule 的 attempts）；"回溯改善"是早期文档留下的说法。

每场比赛从「剩余场次没打满的人」里挑 4 个，评分同时惩罚三件事：
重复搭档、重复对手、以及**完全相同的对局**（同 4 人 + 同分组）。
"""
import random
from collections import Counter, defaultdict
from itertools import combinations

# ---- 评分权重 ----
# 各项惩罚与 played/cool_down 是相加到一起比较的，量级必须可比：
#   played     0..4*target  让场次少的人先上（人人场次相等由 eligible 保证，这里只管节奏）
#   cool_down  0..N         让久等的人先上
#   搭档重复    每次 *3      搭档是最稀缺的资源（只有 C(N,2) 种可能），权重给足
#   对局重复    每次 *10     "又跟同一拨人、同样的分组打一次"，玩家一眼就能看出重复，权重最高
#   对手重复    每次 *2      对手组合比搭档多得多（每场就有 4 组），权重刻意低于搭档 ——
#                           等权重或更高会把搭档多样性挤掉（实测 8 人 16 场：搭档重复 6 → 8）
PARTNER_PENALTY = 3
OPPONENT_PENALTY = 2
DUPLICATE_PENALTY = 10

# 换序重排的最大次数（第 0 次不洗牌，其余用固定种子，因此整体仍是确定性的）
ATTEMPTS = 8


def _quality_attempts(num_players: int) -> int:
    """允许「多排几次挑最好」的次数。

    单次排程成本约 O(M * C(N,4) * 3)，但带精确剪枝后只展开其中一小部分。
    实测单次：8 人 28 场 6ms、16 人 100 场 0.32s、24 人 96 场 0.33s、
    32 人 96 场 0.77s、48 人 96 场 4.3s、64 人 64 场 13.3s。
    按这个量级分档，保证最坏情况下开一次赛也在几秒内（多数情况会提前收工）。
    注意死路重试不受这个限制 —— 排不出来就得换序一直试（见 generate_schedule）。
    """
    if num_players <= 24:
        return ATTEMPTS
    if num_players <= 32:
        return 4
    if num_players <= 48:
        return 2
    return 1


def _quality(schedule) -> tuple:
    """赛程质量，按优先级逐项比较（越小越好）：

    1. 完全相同的对局数 —— 玩家一眼就能看出来的重复，最优先
    2. 重复搭档数 —— 搭档是最稀缺的资源，其次
    3. 重复对手数 —— 最后
    """
    matches = Counter()
    partners = Counter()
    opponents = Counter()
    for (a, b), (c, d), *_ in schedule:
        t1, t2 = _pair_key(a, b), _pair_key(c, d)
        matches[_match_key(t1, t2)] += 1
        partners[t1] += 1
        partners[t2] += 1
        for u in t1:
            for v in t2:
                opponents[_pair_key(u, v)] += 1
    return (
        sum(v - 1 for v in matches.values()),
        sum(v - 1 for v in partners.values()),
        sum(v - 1 for v in opponents.values()),
    )


def _pair_key(x, y):
    """搭档/对手的统一键：(小 id, 大 id)，保证同一对只有一个键。"""
    return (x, y) if x <= y else (y, x)


def _splits(a, b, c, d):
    """4 个人分成两队 —— 3 种分法**全都要评估**。

    原实现只取组合顺序的前两个 vs 后两个，等于每场丢掉了 2/3 的候选；
    这正是"同一拨人第二次遇到时又排成一模一样的两队"的直接原因。
    """
    return (
        (_pair_key(a, b), _pair_key(c, d)),
        (_pair_key(a, c), _pair_key(b, d)),
        (_pair_key(a, d), _pair_key(b, c)),
    )


def _match_key(t1, t2) -> tuple:
    """一场比赛的规范键：同 4 人 + 同分组，且**两队之间也排序**。

    只把每队内部排序是不够的：4 个人在 eligible 里的相对顺序会随 cool_down 变化，
    同一场比赛可能以 ((1,2),(3,4)) 或 ((3,4),(1,2)) 两种形态出现 —— 不排序会让
    「完全相同的对局」惩罚漏判（_quality 用的就是这个规范形式，两边必须一致）。
    """
    return (t1, t2) if t1 <= t2 else (t2, t1)


def generate_schedule(players, total_matches, partner_history=None):
    N = len(players)
    target = (4 * total_matches) // N

    if partner_history is None:
        partner_history = defaultdict(int)

    # 第 0 次尝试不带 rng（不洗牌）→ 结果完全确定；后 7 次用固定种子，
    # 所以整个函数是**可复现**的：同一批人、同样顺序永远排出同一份赛程。
    #
    # 贪心是"一次成型"的，不同的候选顺序会走出不同结果（有的会撞死路，有的会留下
    # 完全相同的对局）。所以这里不再"第一次成功就返回"，而是把 8 次都排出来按
    # _quality 挑最好的一份；已经达到「没有相同对局 + 搭档不重复到理论下限」就提前收工。
    partner_floor = max(0, 2 * total_matches - N * (N - 1) // 2)
    quality_attempts = _quality_attempts(N)

    best = None
    best_key = None
    last_error: ValueError | None = None
    for attempt in range(ATTEMPTS):
        rng = random.Random(attempt) if attempt else None
        try:
            schedule = _greedy_build(players, total_matches, target, dict(partner_history), rng)
        except ValueError as e:
            # 死路：必须继续换序重试，不受 quality_attempts 限制
            last_error = e
            continue
        key = _quality(schedule)
        if best_key is None or key < best_key:
            best, best_key = schedule, key
        if best_key[0] == 0 and best_key[1] <= partner_floor:
            break
        if best is not None and attempt + 1 >= quality_attempts:
            break

    if best is None:
        raise last_error or ValueError("总场次无法为每名选手安排相同比赛场次")
    return best


def _greedy_build(players, total_matches, target, partner_history, rng=None):
    played = {p: 0 for p in players}
    # partner_history 的键来自库里的 RoundPairing（方向不保证），
    # 所以查询时两个方向都查一次，兼容历史数据
    partner_count = defaultdict(int, partner_history)
    opponent_count = defaultdict(int)
    match_count = defaultdict(int)
    cool_down = {p: 0 for p in players}
    schedule = []

    for _ in range(total_matches):
        eligible = [p for p in players if played[p] < target]
        eligible.sort(key=lambda p: -cool_down[p])
        # 打乱同 cool_down 的并列顺序，让每次重启走不同的选择路径
        if rng is not None:
            rng.shuffle(eligible)

        if len(eligible) < 4:
            # 剩余「还差场次」的人不足 4 个 —— 走到死路，交由上层重试
            raise ValueError(
                f"排程走入死路：第 {len(schedule) + 1} 场时只剩 {len(eligible)} 人可上场"
            )

        best_score = float("-inf")
        best_group = None

        for combo in combinations(eligible, 4):
            played_sum = played[combo[0]] + played[combo[1]] + played[combo[2]] + played[combo[3]]
            cool_sum = cool_down[combo[0]] + cool_down[combo[1]] + cool_down[combo[2]] + cool_down[combo[3]]
            # 精确剪枝：score = base - 各项惩罚，故 score <= base。
            # base 已经低于当前最好成绩时，这一组连同它的 3 种分组都不可能被选中
            # （选中要求 score > best，或 score == best 时掷随机数 —— 两者都需要 base >= best），
            # 直接跳过。它只省掉展开，不改变任何选择结果（含随机并列打破）。
            # 收益随人数放大：8 人时只有 15% 可跳过，32 人 64 场可跳过 96%（单次 6.4s → 0.55s）。
            base = -played_sum + cool_sum
            if base < best_score:
                continue
            for t1, t2 in _splits(*combo):
                # 搭档历史两个方向都查（库里的键方向不固定）
                partner_rep = (
                    partner_count[t1] + partner_count[t1[::-1]]
                    + partner_count[t2] + partner_count[t2[::-1]]
                )
                opp_rep = 0
                for u in t1:
                    for v in t2:
                        opp_rep += opponent_count[_pair_key(u, v)]
                score = (
                    - PARTNER_PENALTY * partner_rep
                    - DUPLICATE_PENALTY * match_count[_match_key(t1, t2)]
                    - OPPONENT_PENALTY * opp_rep
                    + base
                )
                # 同分时随机选一个（仅重试时），避免固定路径反复撞同一个死路
                if score > best_score or (rng is not None and score == best_score and rng.random() < 0.5):
                    best_score = score
                    best_group = (t1, t2)

        (pa, pb), (pc, pd) = best_group
        schedule.append(((pa, pb), (pc, pd), None, None))

        played[pa] += 1
        played[pb] += 1
        played[pc] += 1
        played[pd] += 1
        # 两个方向都记，兼容库里历史数据的键方向
        partner_count[(pa, pb)] += 1
        partner_count[(pb, pa)] += 1
        partner_count[(pc, pd)] += 1
        partner_count[(pd, pc)] += 1
        for u in (pa, pb):
            for v in (pc, pd):
                opponent_count[_pair_key(u, v)] += 1
        match_count[_match_key(*best_group)] += 1

        for p in players:
            cool_down[p] = 0 if p in (pa, pb, pc, pd) else cool_down[p] + 1

    return schedule


def compute_match_count(num_players):
    """Calculate minimum matches so 4*M is divisible by N, ensuring equal play time."""
    N = num_players
    M = 1
    while (4 * M) % N != 0:
        M += 1
        if M > N * 10:  # safety limit
            return N
    return M


def compute_rounds(matches, matches_per_round=2):
    """Group matches into rounds."""
    rounds = []
    for i in range(0, len(matches), matches_per_round):
        rounds.append(matches[i:i + matches_per_round])
    return rounds
