"""
赛程引擎: 贪心构造 + 多方案择优 + 换序重试

这里有两套构造，**由场地数分流**（见文件末尾「多场地：轮级构造」一节与
engine_api._round_engine_plan）：

1. `generate_schedule` —— 逐场贪心（老引擎，**冻结**）。最小单位是"一场"：
   从「剩余场次没打满的人」里挑 4 个，评分同时惩罚重复搭档、重复对手、
   以及**完全相同的对局**（同 4 人 + 同分组）。"轮"只是事后按 2 场切块
   （compute_rounds），切块不看人员 —— 单场地/8 人及以下的场景它质量最好，别动。

2. `generate_round_schedule` —— 轮级构造（多场地）。最小单位是"一轮"：
   先定这一轮上场的 4P 个人（互不重复），再把这些人切成 P 场。
   于是"同一个人同一轮出现在两场"在构造上就不可能 —— 这不是靠打分避开的。
   12 人 2 场地用老引擎实测每份赛程有 4 轮撞人（两片场地同时开打时物理上做不到）。

注意用词：两套都是**没有回溯**的贪心，走不通时靠打乱候选顺序重试
（见各自的 attempts）；"回溯改善"是早期文档留下的说法。
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
    长赛事（8 人 28 场）达不到 0，多试几份仍有收益：K=60 时与最近 3 场重复 17.9%
    （平均每场 5.0 场）、K=200 时 13.4%（3.8 场）—— 不做避重是 38.9%（10.9 场）。
    小规模单份成本毫秒级，所以 ≤8 人给到 200 份、≤12 人 120 份；再往上按档位收，
    兜住最坏情况（实测开赛一次 8 人 14 场 2ms、12 人 33 场 48ms、16 人 60 场 0.3s、
    24 人 96 场 2.7s、32 人 96 场 2.6s、48 人 96 场 4.4s）。

    单次排程成本约 O(M * C(N,4) * 3)，带精确剪枝后只展开其中一小部分。
    实测单次：8 人 28 场 6ms、16 人 100 场 0.32s、24 人 96 场 0.33s、
    32 人 96 场 0.77s、48 人 96 场 4.3s、64 人 64 场 13.3s。
    注意死路重试不受这个限制 —— 排不出来就得换序一直试（见 generate_schedule）。

    注意 N > 48 时只排一份：没有挑选余地，那一档仍让历史参与贪心打分（见 generate_schedule），
    否则跨赛事避重在这种规模上会整个失效。
    """
    if num_players <= 8:
        return 200
    if num_players <= 12:
        return 120
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
        # 搭档历史两个方向都查（与 _greedy_build 同一口径：_load_history 把每个搭档对
        # 两个方向各记一次，所以这里查到的值实际是"出现次数 ×2" —— 三个权重的**相对**
        # 大小才决定避重优先级，这个共同倍数不影响结论）
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


def nearest_valid_total(num_players, preferred, max_total=100):
    """「离 preferred 最近」的合法总场次（4M 能被 N 整除，即每人场次相等）。

    为什么需要它：库里的 `total_matches` 是**按当时的人数**选的（创建/编辑时的
    下拉档位）。人一变——编辑把人数改大、报名人数没报满、退赛后人数减少——旧值
    就不一定还能整除。这时开赛/重排必须换一个档，问题是换成哪个：

    - `compute_match_count(N)` 是**最小**合法值。真踩到过：8 人 14 场，报名期间把
      上限改成 12，报满后点「开始比赛」→ 14 场当场掉成 **3 场**（每人只打一场），
      用户看到的就是「改了个名额，场次没了」。
    - 这里取最接近旧值的档（并列取大的，宁可多排一场也别缩水），把「人数变了」
      的代价压到最小：14 场 + 12 人 → 15 场。

    纯函数（不查库），便于单测；调用方（开赛/重排/退赛/创建/编辑）共用同一个口径，
    否则会出现「界面显示 15 场、开赛排 12 场」这类口径分裂。
    """
    if not preferred or preferred < 1:
        return compute_match_count(num_players)
    step = fairness_step(num_players)
    if step <= 0:
        return 1
    # 合法值 = step 的正整数倍，且不超过硬上限（100 场）
    values = list(range(step, max_total + 1, step)) or [step]
    # 并列（preferred 正好落在两档中间）取大的：宁可多打一场，也不要莫名缩水
    return min(values, key=lambda v: (abs(v - preferred), -v))


def floor_valid_total(num_players, budget, max_total=100):
    """不超过 budget 的最大合法总场次（**0 合法**）。

    专给「退赛重排」用：冻结区已经用掉了一部分计划额度，这次最多还能排这么多场。

    两个设计点：
    - 取**不超过额度**的那一档，而不是就近 —— 计划总场次是上限，退一个人不该让赛事
      "多出几场"（就近取大在 6 人 8 场额度时会排出 9 场，总场次反而超过计划）；
    - 额度不到一个步长时返回 0 = 不再补排（赛事自然收尾），而不是凑到最小档再塞
      一整套：8 人 14 场打完 12 场后退一人，剩 7 人步长就是 7，凑最小档等于"还剩 2 场"
      变成"再给每人排 4 场"，赛事总场次从 14 涨到 26（实测踩过）。

    对比 `nearest_valid_total`（开赛/重排用）：那个必须至少排一档、并列取大，
    因为它解决的是"人数变了别把场次缩没"，这里的约束正好相反。
    """
    if not num_players or num_players < 1 or budget <= 0:
        return 0
    step = fairness_step(num_players)
    if step <= 0:
        return 0
    return (min(budget, max_total) // step) * step


def compute_rounds(matches, matches_per_round=2):
    """Group matches into rounds."""
    rounds = []
    for i in range(0, len(matches), matches_per_round):
        rounds.append(matches[i:i + matches_per_round])
    return rounds


# ============================================================================
# 多场地：轮级构造
# ============================================================================
#
# 为什么要另起一套：老引擎排的是**扁平的场次列表**，"轮"是事后两两切块
# （compute_rounds），切块完全不看人。于是 12 人 33 场实测**每份赛程都有 4 轮**
# 出现同一个人同时在两场里 —— 两片场地同时开打时这是物理上做不到的，
# 连带把轮空次数也打歪（该 6 次，实际 6~7 次）。
#
# 轮级构造把顺序反过来：**先定这一轮上场的 4P 个人（互不重复），再把这 4P 人切成 P 场**。
# 两场的人来自同一个 8 人池且被完全切开，交集恒为空 —— 撞人不再靠打分碰运气，
# 而是构造上不可能。代价是"从 8 人里怎么切两场"比"从 12 人里随便挑 4 个"自由度小，
# 实测量化：12 人 36 场搭档重复 7 次（理论下限 6），老引擎 12 人 33 场是 0/6。
#
# 分流规则（engine_api 那边实现，这里只给判定用的纯函数）：
#   · 场地数 = 1，或开赛时人数 ≤ 8  → 老引擎（原样不动）
#   · 场地数 ≥ 2 且开赛时人数 ≥ 9  → 轮级构造

# 走轮级构造的最少人数：8 人正好填满两场、没有轮空，老引擎在那档质量更好，
# 而且现场 8 个人一片场地也能打，没必要换。
MIN_PLAYERS_FOR_MULTI_COURT = 9

# 每片场地最少几个人：一场双打 4 人上场，至少还要留 1 人在场下当裁判。
PLAYERS_PER_COURT = 5


def use_round_engine(roster_size: int, courts: int) -> bool:
    """这个赛事该用轮级构造还是老的逐场贪心。

    `roster_size` 必须是**开赛时**的人数（库里 PlayerStats 的行数：退赛只把
    is_active 置 False、不删行，所以它整个赛事期间恒定），不能用当前在场人数 ——
    否则赛事中途退到 8 人以下就会换引擎，同一份赛程出现两套口径。
    """
    return int(courts or 0) >= 2 and int(roster_size or 0) >= MIN_PLAYERS_FOR_MULTI_COURT


def courts_allowed(num_players: int, courts: int) -> bool:
    """这么多片场地在这么多人的情况下是否成立。

    规则：**每片场地至少 `PLAYERS_PER_COURT` 人**（4 人上场 + 至少 1 人场下执裁）。
    一片场地不设这条（4 人打满全场、没有裁判是既有的现实用法）。

    创建/编辑赛事和开赛那一刻都用它做校验 —— 两处必须同一套口径，否则会出现
    「界面让建、开赛被拒」或者反过来「建得出来但没人执裁」。
    """
    courts = int(courts or 0)
    if courts < 2:
        return True
    return int(num_players or 0) >= PLAYERS_PER_COURT * courts


def court_count_effective(num_players: int, courts: int) -> int:
    """实际能同时开几片场地。

    三条上限取最小：
      - 赛事登记的场地数；
      - `人数 // 4`（一场双打要 4 个人）；
      - `人数 // 5` —— 每片场地至少 5 人（4 人上场 + 至少 1 人场下执裁）。

    人数不够时**降级**而不是报错：赛事进行中有人退赛是挡不住的，
    这时少开一片场地远好过让剩下的比赛排不出来。
    """
    courts = int(courts or 0)
    n = int(num_players or 0)
    if courts < 1 or n < 4:
        return 0
    return max(0, min(courts, n // 4, n // PLAYERS_PER_COURT))


def matches_per_round(num_players: int, courts: int) -> int:
    """一轮同时开几场（只在走轮级构造的赛事里有意义）。

    人数不够时自动降级（见 court_count_effective）：9 人 2 场地 → 每轮 1 场
    （4 上 5 歇，够 1 个裁判），而不是硬开两场没人执裁。
    """
    return max(1, court_count_effective(num_players, courts))


def _three_splits(idx4):
    """4 个人（下标）怎么分成两队 —— 3 种分法全都要评估。

    只取"前两个 vs 后两个"等于每场丢掉 2/3 的候选，这正是老引擎早期
    "同一拨人第二次遇到时又排成一模一样的两队"的直接原因（见 _splits）。
    """
    a, b, c, d = idx4
    return (((a, b), (c, d)), ((a, c), (b, d)), ((a, d), (b, c)))


def _build_two_match_options():
    """8 个人切成两场的**全部**方案：C(8,4)/2 = 35 种分组 × 每种两场各 3 种分队 = 315。

    下标相对于"这一轮上场的 8 个人"的排序，与具体是谁无关，所以全进程只算一次。
    """
    options = []
    seen = set()
    everyone = frozenset(range(8))
    for g1 in combinations(range(8), 4):
        s1 = frozenset(g1)
        key = frozenset((s1, everyone - s1))
        if key in seen:          # (A,B) 与 (B,A) 是同一种分组
            continue
        seen.add(key)
        g2 = tuple(i for i in range(8) if i not in s1)
        for split1 in _three_splits(g1):
            for split2 in _three_splits(g2):
                options.append((split1, split2))
    return tuple(options)


_TWO_MATCH_OPTIONS = _build_two_match_options()


def _as_ids(pair, on):
    """下标对 → 选手对（统一成 (小 id, 大 id)）。"""
    return _pair_key(on[pair[0]], on[pair[1]])


def _round_option_score(option, on, partner_count, opponent_count, match_count):
    """给"这一轮怎么切"的一个候选方案打分（越大越好）。

    权重与逐场贪心完全一致（搭档 3 / 对局 20 / 对手 2），口径也一致：
    搭档两个方向都查（库里历史数据的键方向不保证），对手按 _pair_key 归一。
    """
    score = 0
    for pair1, pair2 in option:
        t1 = _as_ids(pair1, on)
        t2 = _as_ids(pair2, on)
        score -= PARTNER_PENALTY * (partner_count[t1] + partner_count[t1[::-1]]
                                    + partner_count[t2] + partner_count[t2[::-1]])
        score -= DUPLICATE_PENALTY * match_count[_match_key(t1, t2)]
        for u in t1:
            for v in t2:
                score -= OPPONENT_PENALTY * opponent_count[_pair_key(u, v)]
    return score


def _pick_round(on, want_matches, partner_count, opponent_count, match_count, rng=None):
    """把这一轮的 `4 * want_matches` 个人切成 `want_matches` 场，取打分最好的切法。

    want_matches == 2 时全枚举 315 种（最优）；≥3 场全枚举是十几万种，
    改成"一场一场地贪心"—— 每挑完一场就把这 4 个人移出候选池，再挑下一场。
    """
    on = tuple(on)
    if want_matches == 1:
        best, best_score = None, None
        for split in _three_splits((0, 1, 2, 3)):
            s = _round_option_score((split,), on, partner_count, opponent_count, match_count)
            if best_score is None or s > best_score or (
                    rng is not None and s == best_score and rng.random() < 0.5):
                best, best_score = split, s
        return [(_as_ids(best[0], on), _as_ids(best[1], on))]

    if want_matches == 2:
        best, best_score = None, None
        for option in _TWO_MATCH_OPTIONS:
            s = _round_option_score(option, on, partner_count, opponent_count, match_count)
            if best_score is None or s > best_score or (
                    rng is not None and s == best_score and rng.random() < 0.5):
                best, best_score = option, s
        return [(_as_ids(pair1, on), _as_ids(pair2, on)) for pair1, pair2 in best]

    # ≥3 场（例如 3 片场地 12 人）：逐场贪心，且用**本轮临时计数**，
    # 免得同一轮里出现"同一对搭档被切两次"。
    rest = list(on)
    games = []
    local_partner = defaultdict(int, partner_count)
    local_opponent = defaultdict(int, opponent_count)
    local_match = defaultdict(int, match_count)
    for _ in range(want_matches):
        best, best_score = None, None
        for combo in combinations(range(len(rest)), 4):
            for split in _three_splits(combo):
                s = _round_option_score((split,), rest, local_partner,
                                        local_opponent, local_match)
                if best_score is None or s > best_score:
                    best, best_score = split, s
        t1 = _as_ids(best[0], rest)
        t2 = _as_ids(best[1], rest)
        games.append((t1, t2))
        local_partner[t1] += 1
        local_partner[t1[::-1]] += 1
        local_partner[t2] += 1
        local_partner[t2[::-1]] += 1
        local_match[_match_key(t1, t2)] += 1
        for u in t1:
            for v in t2:
                local_opponent[_pair_key(u, v)] += 1
        used = set(t1) | set(t2)
        rest = [p for p in rest if p not in used]
    return games


def _select_round_players(players, played, target, want, partner_count, rng=None):
    """挑这一轮上场的 `want` 个人。

    配额优先：剩余场次多的人先上（这是"每人场次相等"的保证 —— 和逐场贪心的
    played < target 是同一个硬约束）；
    同档内按「跟本轮已选的人搭档次数最少」排序、并列随机 —— 这一步决定跨组混合。

    注意**不能**用"谁歇得久"来排同档：那样会让同一批人反复凑在一起
    （16 人 2 场地实测退化成两组交替，搭档重复从 0 变 8 次）。
    """
    remaining = {p: target - played[p] for p in players}
    live = [p for p in players if remaining[p] > 0]
    picked = []
    while len(picked) < want and live:
        top = max(remaining[p] for p in live)
        level = [p for p in live if remaining[p] == top]
        level.sort(key=lambda p: (
            sum(partner_count[_pair_key(p, q)] + partner_count[_pair_key(q, p)]
                for q in picked),
            rng.random() if rng is not None else 0,
        ))
        take = level[:want - len(picked)]
        picked.extend(take)
        live = [p for p in live if p not in take]
    return picked


def _build_rounds_greedy(players, total_matches, target, per_round,
                         partner_count, opponent_count, match_count, rng=None):
    """一轮一轮地排：定人 → 切场 → 记账。走不通就抛 ValueError 交给上层换序重试。"""
    played = {p: 0 for p in players}
    schedule = []
    left = total_matches
    while left > 0:
        live_count = sum(1 for p in players if played[p] < target)
        want_matches = min(per_round, left, live_count // 4)
        if want_matches < 1:
            raise ValueError(
                f"排程走入死路：还剩 {left} 场时只有 {live_count} 人还没打满"
            )
        want = 4 * want_matches
        on = _select_round_players(players, played, target, want, partner_count, rng)
        if len(on) != want:
            raise ValueError(f"排程走入死路：本轮需要 {want} 人，只选出 {len(on)} 人")
        games = _pick_round(on, want_matches, partner_count, opponent_count,
                            match_count, rng)
        if len(games) != want_matches:
            raise ValueError("排程走入死路：本轮切不出足够的场次")
        for t1, t2 in games:
            schedule.append((t1, t2, None, None))
            partner_count[t1] += 1
            partner_count[t1[::-1]] += 1
            partner_count[t2] += 1
            partner_count[t2[::-1]] += 1
            match_count[_match_key(t1, t2)] += 1
            for u in t1:
                for v in t2:
                    opponent_count[_pair_key(u, v)] += 1
            for p in t1 + t2:
                played[p] += 1
        left -= want_matches

    # 不允许"静默少排"：宁可抛出去让上层换序重试，也不要落库一份残缺赛程
    if left:
        raise ValueError(f"排程走入死路：还剩 {left} 场没排出来")
    for p in players:
        if played[p] != target:
            raise ValueError(f"排程走入死路：选手 {p} 排了 {played[p]} 场，目标是 {target}")
    return schedule


def _round_attempts(num_players: int) -> int:
    """轮级构造允许「多排几次挑最好」的次数。

    每份成本比逐场贪心高（每轮要把 8 人切成两场的 315 种全部打分），
    所以档位收得更紧：实测 12 人 36 场单份约 14ms，K=40 → 约 0.6s，
    而且达标会提前收工（无重复对局 + 搭档到理论下限 + 对手够均衡 + 不撞历史）。
    """
    if num_players <= 12:
        return 40
    if num_players <= 20:
        return 16
    if num_players <= 32:
        return 6
    if num_players <= 48:
        return 3
    return 1


def generate_round_schedule(players, total_matches, per_round=2,
                            partner_history=None, opponent_history=None,
                            match_history=None):
    """排一份（新增的）赛程，**按轮为最小单位**。多场地专用。

    与 generate_schedule 的对外约定完全一致：返回扁平的
    `[(队伍a, 队伍b, None, None), ...]`，且**顺序就是轮的顺序**（每轮 per_round 场，
    最后一轮可能不足）—— 所以调用方仍然用 `compute_rounds(schedule, per_round)`
    切轮，落库代码一行都不用改。

    硬保证（单测锁死）：
      · 每一轮的 4*per_round 个人互不相同（同一时刻不会有人站两片场地）；
      · 每名选手恰好打 `4 * total_matches // num_players` 场；
      · 总场次恰好 `total_matches` 场（排不满就抛 ValueError，绝不静默少排）。

    partner/opponent/match_history 用法与老引擎一致：默认**不参与打分**，
    只用来在若干份"场内同样均衡"的候选里挑一份最不撞历史的（唯一例外是
    attempts == 1 的大规模档位，那时没有挑选余地，历史直接参与打分）。
    """
    N = len(players)
    if per_round < 1:
        raise ValueError("每轮场数至少 1 场")
    if not total_matches:
        raise ValueError("总场次必须大于 0")
    target = (4 * total_matches) // N
    if target * N != 4 * total_matches:
        raise ValueError(
            f"{N} 人打 {total_matches} 场无法让每人场次相等"
        )

    partner_history = partner_history or {}
    opponent_history = opponent_history or {}
    match_history = match_history or {}

    attempts = _round_attempts(N)
    history_in_greedy = attempts == 1
    partner_floor = max(0, 2 * total_matches - N * (N - 1) // 2)
    # 与老引擎同一口径：≤8 人把"对手次数极差 0"当目标，9 人以上结构上往往做不到 0
    spread_goal = 0 if N <= 8 else 2

    best = None
    best_key = None
    last_error = None
    for attempt in range(attempts):
        rng = random.Random(attempt) if attempt else None
        if history_in_greedy:
            pc = defaultdict(int, partner_history)
            oc = defaultdict(int, opponent_history)
            mc = defaultdict(int, match_history)
        else:
            pc, oc, mc = defaultdict(int), defaultdict(int), defaultdict(int)
        try:
            schedule = _build_rounds_greedy(players, total_matches, target, per_round,
                                            pc, oc, mc, rng)
        except ValueError as e:
            last_error = e
            continue
        quality = _quality(schedule)
        collision = _history_collision(schedule, partner_history,
                                       opponent_history, match_history)
        key = (quality, collision)
        if best_key is None or key < best_key:
            best, best_key = schedule, key
        if (quality[0] == 0 and quality[1] <= partner_floor
                and quality[3] <= spread_goal and collision == 0):
            break

    if best is None:
        raise last_error or ValueError("总场次无法为每名选手安排相同比赛场次")
    return best
