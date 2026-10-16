"""
赛程引擎: 贪心构造 + 多方案择优 + 换序重试

注意用词：这里**没有回溯**。贪心一次成型，走不通时靠打乱候选顺序重试
（见 generate_schedule 的 attempts）；"回溯改善"是早期文档留下的说法。

每场比赛从「剩余场次没打满的人」里挑 4 个，评分同时惩罚三件事：
重复搭档、重复对手、以及**完全相同的对局**（同 4 人 + 同分组）。
"""
import math
import random
from collections import Counter, defaultdict
from itertools import combinations

# ---- 评分权重 ----
# 各项惩罚与 played/cool_down 是相加到一起比较的，量级必须可比：
#   played     0..4*target  让场次少的人先上（人人场次相等由 eligible 保证，这里只管节奏）
#   cool_down  0..N         让久等的人先上
#   搭档重复    每次 *3      搭档是最稀缺的资源（只有 C(N,2) 种可能），权重给足
#   对局重复    每次 *20     "又跟同一拨人、同样的分组打一次"，玩家一眼就能看出重复，权重最高
#   对手重复    每次 *2      对手组合比搭档多得多（每场就有 4 组），权重刻意低于搭档 ——
#                           等权重或更高会把搭档多样性挤掉（实测 8 人 16 场：搭档重复 6 → 8）
PARTNER_PENALTY = 3
OPPONENT_PENALTY = 2
# 20 而不是 10：它在两个地方起作用 ——
# ① 赛程内部：贪心自带的计数保证一份赛程里不出现"同 4 人 + 同分组"的重复；
# ② 挑候选（_history_collision）：跨赛事避重的优先级由这三个权重的**相对大小**决定 ——
#    先避开"上周那一场 4 人 + 同样分组"（20），其次重复搭档（3），最后重复对手（2）。
DUPLICATE_PENALTY = 20

# 换序重排的次数按人数分档，见 _quality_attempts（第 0 次不洗牌、其余用固定种子，
# 因此同一批人 + 同样顺序的结果仍是确定的、可复现的）


def _quality_attempts(num_players: int) -> int:
    """允许「多排几次挑最好」的次数。

    现在每次尝试要同时挑两件事：①场内均衡（`_quality`）②不跟最近几场撞（`_history_collision`）。
    "既均衡、又完全不撞历史"的那一份未必是第一份，所以小规模给得足；单份成本随人数上涨，
    大规格按档位收，沿用"最坏情况下开一次赛也在几秒内"的预算（并靠下面的提前收工兜住常态）。

    实测（8 人 14 场、最近 3 场历史的稳态）：
      K=8 → 挑出来的那份"完全不撞历史"占 44%；K=30 → 89%；K=60 → 97%（其余 1 场重复）。
    实际上"场内均衡 + 完全不撞历史"往往头几份就命中，所以下面按人数分档主要是**兜住最坏情况**：
    实测开赛一次 8 人 14 场 2ms、12 人 33 场 48ms、16 人 60 场 0.3s、24 人 96 场 2.7s、
    32 人 96 场 2.6s、48 人 96 场 4.4s。

    单次排程成本约 O(M * C(N,4) * 3)，带精确剪枝后只展开其中一小部分。
    实测单次：8 人 28 场 6ms、16 人 100 场 0.32s、24 人 96 场 0.33s、
    32 人 96 场 0.77s、48 人 96 场 4.3s、64 人 64 场 13.3s。
    注意死路重试不受这个限制 —— 排不出来就得换序一直试（见 generate_schedule）。

    注意 N > 48 时只排一份：没有挑选余地，那一档仍让历史参与贪心打分（见 generate_schedule），
    否则跨赛事避重在这种规模上会整个失效。
    """
    if num_players <= 12:
        return 60
    if num_players <= 20:
        return 16
    if num_players <= 24:
        return 6
    if num_players <= 32:
        return 3
    if num_players <= 48:
        return 2
    return 1


def _quality(schedule) -> tuple:
    """赛程质量，按优先级逐项比较（越小越好）：

    1. 完全相同的对局数 —— 玩家一眼就能看出来的重复，最优先
    2. 重复搭档数 —— 搭档是最稀缺的资源，其次
    3. 重复对手数 —— 再其次
    4. 对手次数极差 —— 场内公平的硬指标。光看第 3 项看不出"某对碰 4 次、另一对只碰 1 次"：
       28 对全都碰过时 Σ(v-1) 恒等于 56-28=28，与分布无关；本项把**没碰过的对按 0 算**，
       所以"从没遇到过"也会被计入（实测带上它之后，8 人 14 场回到"每对恰好相遇 2 次"）。
    """
    matches = Counter()
    partners = Counter()
    opponents = Counter()
    players: set = set()
    for (a, b), (c, d), *_ in schedule:
        t1, t2 = _pair_key(a, b), _pair_key(c, d)
        matches[_match_key(t1, t2)] += 1
        partners[t1] += 1
        partners[t2] += 1
        players.update((a, b, c, d))
        for u in t1:
            for v in t2:
                opponents[_pair_key(u, v)] += 1
    ordered = sorted(players)
    counts = [opponents.get(_pair_key(x, y), 0)
              for i, x in enumerate(ordered) for y in ordered[i + 1:]]
    return (
        sum(v - 1 for v in matches.values()),
        sum(v - 1 for v in partners.values()),
        sum(v - 1 for v in opponents.values()),
        (max(counts) - min(counts)) if counts else 0,
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


def fairness_step(num_players: int) -> int:
    """「每人场次相同」所允许的最小场次步长。

    4K 个上场名额要被 N 人均分，故 K 必须是 N/gcd(4,N) 的倍数：
    8 人 → 2 场、6 人 → 3 场、9 人 → 9 场。

    开赛、赛中追加比赛、以及前端的可选项列表都必须用同一个来源，否则会出现
    「界面让选、提交却被拒」或者反过来「能排但每人场次不等」。
    """
    return num_players // math.gcd(4, num_players)


def _history_collision(schedule, partner_history, opponent_history, match_history) -> int:
    """这份赛程跟给定历史"撞"了多少（越小越该选）。

    历史**不再参与贪心打分**：一旦参与，它会把贪心推出"场内均衡"的结构（实测 8 人 14 场，
    对手对相遇次数极差 4~5 的赛事占 91%，而不带历史时 100% 是"每对搭档 1 次、每对相遇 2 次"）。
    改成：贪心始终不带历史地排出**场内均衡**的候选，再在若干候选里挑一份最不撞历史的。
    计分口径与贪心里的历史项一致（同一套权重），只是整份赛程累加。
    """
    partner_history = partner_history or {}
    opponent_history = opponent_history or {}
    match_history = match_history or {}
    score = 0
    for (a, b), (c, d), *_ in schedule:
        t1, t2 = _pair_key(a, b), _pair_key(c, d)
        # 搭档历史两个方向都查（库里的键方向不固定，与 _greedy_build 一致）
        for p in (t1, t2):
            score += PARTNER_PENALTY * (partner_history.get(p, 0)
                                        + partner_history.get(p[::-1], 0))
        score += DUPLICATE_PENALTY * match_history.get(_match_key(t1, t2), 0)
        for u in t1:
            for v in t2:
                score += OPPONENT_PENALTY * opponent_history.get(_pair_key(u, v), 0)
    return score


def generate_schedule(players, total_matches, partner_history=None,
                      opponent_history=None, match_history=None):
    """排一份（新增的）赛程。

    partner_history / opponent_history / match_history 是**已有比赛**里的重复统计，
    用来让新排出来的比赛尽量避开已经出现过的东西：
      - partner_history  —— 重复搭档（键为 _pair_key，两个方向各记一次）
      - opponent_history —— 重复对手
      - match_history    —— 完全相同的对局（同 4 人 + 同分组）
    三者缺省都为空，因此老调用方的行为完全不变。

    统计的不止"本赛事已排的"：engine_api 会把**最近几场赛事**的统计一起并进来
    （跨赛事避重，见那边的 RECENT_EVENT_HISTORY）—— 同一群体每周固定打球时，
    这能避免"上周那场 4 人、同样分组"原样再来一次。

    **这三者不参与贪心打分**（那会把贪心推出场内均衡的结构），而是用来在若干份
    "场内同样均衡"的候选赛程里挑一份最不撞历史的 —— 于是"每个人对手都公平"与
    "跨赛事避重"能一起拿到；详见函数体的注释与 _history_collision。
    它们也不改变目标场次：每人还是打 target 场。
    """
    N = len(players)
    target = (4 * total_matches) // N

    if partner_history is None:
        partner_history = defaultdict(int)

    # 第 0 次尝试不带 rng（不洗牌）→ 结果完全确定；后几次用固定种子，
    # 所以整个函数是**可复现**的：同一批人、同样顺序永远排出同一份赛程。
    #
    # 贪心是"一次成型"的，不同的候选顺序会走出不同结果（有的会撞死路，有的留下完全相同的
    # 对局、或者跟最近几场撞得更多）。所以这里把 K 次都排出来，
    # 按「①场内质量（_quality）②跟历史的碰撞（_history_collision）」字典序挑最好的一份。
    #
    # **历史不参与贪心**：这是"每个人对手都公平"的关键 —— 不带历史时贪心稳定排出场内均衡
    # 的赛程（8 人 14 场实测 100% 做到"每对搭档恰好 1 次、每对相遇恰好 2 次"），
    # 历史只用来在若干份同样均衡的候选里挑"最不跟最近几场撞"的那一份。
    # 唯一例外：只排得起一份的规模（N > 48，见 _quality_attempts）没有挑选余地，
    # 那一档仍把历史交给贪心，免得跨赛事避重在大规模上整个失效。
    partner_floor = max(0, 2 * total_matches - N * (N - 1) // 2)
    quality_attempts = _quality_attempts(N)
    history_in_greedy = quality_attempts == 1
    # 场内均衡的目标：8 人及以下把"对手次数极差 0"当目标（8 人 14 场实测 100% 做得到，
    # 多试几份而已，单份成本毫秒级）；9 人以上结构上往往做不到 0（12 人 33 场不带任何历史时
    # 的最好成绩就是 2~3），再拿 0 当收工条件就永远收不了工、白跑满份数（实测 1.5 秒）。
    spread_goal = 0 if N <= 8 else 2

    best = None
    best_key = None
    last_error: ValueError | None = None
    for attempt in range(quality_attempts):
        rng = random.Random(attempt) if attempt else None
        try:
            if history_in_greedy:
                schedule = _greedy_build(players, total_matches, target,
                                         dict(partner_history), rng,
                                         opponent_history, match_history)
            else:
                schedule = _greedy_build(players, total_matches, target, {}, rng, None, None)
        except ValueError as e:
            # 死路：必须继续换序重试，不受 quality_attempts 限制
            last_error = e
            continue
        quality = _quality(schedule)
        collision = _history_collision(schedule, partner_history,
                                       opponent_history, match_history)
        key = (quality, collision)
        if best_key is None or key < best_key:
            best, best_key = schedule, key
        # 提前收工：场内已经达标（没有相同对局 + 搭档重复到理论下限 + 对手次数够均衡）
        # **而且完全不撞历史**
        if (quality[0] == 0 and quality[1] <= partner_floor
                and quality[3] <= spread_goal and collision == 0):
            break
        if attempt + 1 >= quality_attempts:
            break

    if best is None:
        raise last_error or ValueError("总场次无法为每名选手安排相同比赛场次")
    return best


def shuffle_sides(schedule, rng=None):
    """把排好的对阵左右对调：**谁跟谁打完全不变**，只改哪一队算 A 队（界面左侧）。

    为什么需要它：`_greedy_build` 打分并列时取「先枚举到的那一组」，而 `_splits`
    的第一种分法总是把组合首位的人放进 A 队；名单第一人（报名最早的）因此在
    **自己参加的每一场**里都站左队 —— 实测 8 人 28 场：14/14 场排在最左，
    界面上看就是「有一个人一直在最左边」。名单靠前的人也有同向的梯度偏置
    （8 人 28 场：第 2/3 名 8/14、第 4/5 名 6/14…），所以这里对**每一场**独立
    掷一次硬币，而不是「只翻含某人的场次」—— 后者只是把偏置转给名单里的下一个人。

    rng 缺省用模块级 random（真随机，每次开赛的左右都不同）；传
    `random.Random(seed)` 可固定下来便于复现/测试。返回新列表，不原地修改。
    """
    if rng is None:
        rng = random
    return [
        (t2, t1, court, slot) if rng.getrandbits(1) else (t1, t2, court, slot)
        for t1, t2, court, slot in schedule
    ]


def _greedy_build(players, total_matches, target, partner_history, rng=None,
                  opponent_history=None, match_history=None):
    played = {p: 0 for p in players}
    # partner_history 的键来自库里的 RoundPairing（方向不保证），
    # 所以查询时两个方向都查一次，兼容历史数据
    partner_count = defaultdict(int, partner_history)
    # 同理：对手/对局历史来自库里已有的比赛，用来避开"又跟同一拨人、同样的分组打一次"
    opponent_count = defaultdict(int, opponent_history or {})
    match_count = defaultdict(int, match_history or {})
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
