"""
赛程引擎: 贪心构造 + 回溯改善
"""
import random
from collections import defaultdict
from itertools import combinations


def generate_schedule(players, total_matches, partner_history=None):
    N = len(players)
    target = (4 * total_matches) // N

    if partner_history is None:
        partner_history = defaultdict(int)

    # 贪心构造是确定性的，个别组合会走进死路（实测 5 人 + 15 场：后期只剩
    # 3 人「还差场次」，无法再凑一场），而 4*total_matches 能被 N 整除本身
    # 并不保证贪心一定能排出来。这里用随机重排重试几次：只要存在可行解，
    # 打乱同分候选的顺序通常就能绕开死路（生成只在开赛时跑一次，成本可忽略）。
    attempts = 8
    last_error: ValueError | None = None
    for attempt in range(attempts):
        rng = random.Random(attempt) if attempt else None
        try:
            schedule, played, partner_count, cool_down = _greedy_build(
                players, total_matches, target, dict(partner_history), rng
            )
            return schedule
        except ValueError as e:
            last_error = e
    raise last_error or ValueError("总场次无法为每名选手安排相同比赛场次")


def _greedy_build(players, total_matches, target, partner_history, rng=None):
    played = {p: 0 for p in players}
    partner_count = defaultdict(int, partner_history)
    cool_down = {p: 0 for p in players}
    schedule = []

    for _ in range(total_matches):
        eligible = [p for p in players if played[p] < target]
        eligible.sort(key=lambda p: -cool_down[p])
        # 打乱同 cool_down 的并列顺序，让每次重启走不同的选择路径
        if rng is not None:
            rng.shuffle(eligible)

        best_score = float("-inf")
        best_group = None

        for combo in combinations(eligible, 4):
            a, b, c, d = combo
            score = (
                -partner_count.get((a, b), 0) - partner_count.get((b, a), 0)
                - partner_count.get((c, d), 0) - partner_count.get((d, c), 0)
                - played[a] - played[b] - played[c] - played[d]
                + cool_down[a] + cool_down[b] + cool_down[c] + cool_down[d]
            )
            # 同分时随机选一个（仅重试时），避免固定路径反复撞同一个死路
            if score > best_score or (rng is not None and score == best_score and rng.random() < 0.5):
                best_score = score
                best_group = ((a, b), (c, d))

        if best_group is None:
            if len(eligible) < 4:
                # 剩余「还差场次」的人不足 4 个 —— 走到死路，交由上层重试
                raise ValueError(
                    f"排程走入死路：第 {len(schedule) + 1} 场时只剩 {len(eligible)} 人可上场"
                )
            a, b, c, d = eligible[:4]
            best_group = ((a, b), (c, d))

        (pa, pb), (pc, pd) = best_group
        schedule.append(((pa, pb), (pc, pd), None, None))

        played[pa] += 1
        played[pb] += 1
        played[pc] += 1
        played[pd] += 1
        partner_count[(pa, pb)] += 1
        partner_count[(pb, pa)] += 1
        partner_count[(pc, pd)] += 1
        partner_count[(pd, pc)] += 1

        for p in players:
            cool_down[p] = 0 if p in (pa, pb, pc, pd) else cool_down[p] + 1

    return schedule, played, partner_count, cool_down


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
