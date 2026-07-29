"""
赛程引擎: 贪心构造 + 回溯改善 + 场地时段分配
"""
import random
from collections import defaultdict
from itertools import combinations


def generate_schedule(players, total_matches, partner_history=None, courts=None):
    N = len(players)
    target = (4 * total_matches) // N

    if partner_history is None:
        partner_history = defaultdict(int)

    schedule, played, partner_count, cool_down = _greedy_build(
        players, total_matches, target, dict(partner_history)
    )

    if courts:
        schedule = _assign_venues(schedule, courts)

    return schedule


def _greedy_build(players, total_matches, target, partner_history):
    played = {p: 0 for p in players}
    partner_count = defaultdict(int, partner_history)
    cool_down = {p: 0 for p in players}
    schedule = []

    for _ in range(total_matches):
        eligible = [p for p in players if played[p] < target]
        eligible.sort(key=lambda p: -cool_down[p])

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
            if score > best_score:
                best_score = score
                best_group = ((a, b), (c, d))

        if best_group is None:
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


def _assign_venues(matches, courts):
    flat_slots = []
    for court_id, slots in courts:
        for slot in slots:
            flat_slots.append((court_id, slot))

    assigned = []
    for i, match in enumerate(matches):
        idx = i % len(flat_slots) if flat_slots else 0
        cid, slot = flat_slots[idx] if flat_slots else (None, None)
        assigned.append((*match, cid, slot))
    return assigned


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
