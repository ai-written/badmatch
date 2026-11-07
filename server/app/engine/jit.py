"""「优先场地」模式的对局生成（纯逻辑，不碰库，便于单测）。

与「优先公平性」（事前排完整赛程）的区别：这边**打完一场才排下一场**，所以

- 只能从**此刻空闲的人**里挑（否则场地要空等，与"优先场地"自相矛盾）；
- 每人场次**刻意允许差 1 场**：严格相等就得在尾段等人，那正是这个模式要避免的；
- 打分沿用同一套权重（搭档 3 / 完全相同的对局 20 / 对手 2），尽量少重复，
  但它天生是"近视"的 —— 看不到后面谁空出来，所以搭档重复会比事前排程多一些
  （实测 12 人 36 场：现状 6 次 = 理论下限，边打边排约 13 次）。
"""
from itertools import combinations

from app.engine.scheduler import (
    DUPLICATE_PENALTY, OPPONENT_PENALTY, PARTNER_PENALTY,
    _match_key, _pair_key, _splits,
)

# 「场次少的人先上」这条的权重：pool 里最多差 1 个场次（差 4 个名额的量级），
# 给到 100 就足以压过任何搭档/对手惩罚，保证"先补齐落后的"。
PLAYED_WEIGHT = 100


def pick_court_first_match(free_players, played, target,
                           partner_count=None, opponent_count=None, match_count=None,
                           rng=None):
    """从空闲的人里挑一场（4 人 + 分队）；凑不齐 4 个或都打满了返回 None。

    free_players: 此刻空闲的选手 id（调用方负责排除"正在打的人"）
    played: {player_id: 已打完场次数}
    target: 每人目标场次（4M/N）
    rng: 并列时的随机源。**不传就是确定性的**（单测要稳定）；
         实际补排时传 random 模块 —— 开局所有人都是 0 场、没有任何历史时，
         所有组合得分完全相同，不随机的话会永远排出"名单前 4 个 vs 接着 4 个"
         这种机械序列（实测踩过：12 人开赛永远是 1/2 vs 3/4、5/6 vs 7/8）。
    返回 ((a, b), (c, d))，两队内部都按 (小 id, 大 id) 排列；调用方可直接落库。
    """
    if partner_count is None:
        partner_count = {}
    if opponent_count is None:
        opponent_count = {}
    if match_count is None:
        match_count = {}

    eligible = [p for p in free_players if played.get(p, 0) < target]
    if len(eligible) < 4:
        return None

    # 场次最少的一档优先；不够 4 个人就放宽一档（这就是"允许差 1 场"的实现）
    lowest = min(played.get(p, 0) for p in eligible)
    pool = eligible
    for extra in (0, 1):
        narrowed = [p for p in eligible if played.get(p, 0) <= lowest + extra]
        if len(narrowed) >= 4:
            pool = narrowed
            break
    else:                                    # 三级放宽都不够，就用全部合格的人
        pool = eligible
    if len(pool) < 4:
        return None

    best: list = []                          # 并列最优的候选（开局时会有一大把）
    best_score = None
    for combo in combinations(sorted(pool), 4):
        base = -PLAYED_WEIGHT * sum(played.get(p, 0) for p in combo)
        for t1, t2 in _splits(*combo):
            t1k, t2k = _pair_key(*t1), _pair_key(*t2)
            score = (
                base
                - PARTNER_PENALTY * (partner_count.get(t1k, 0) + partner_count.get(t2k, 0))
                - DUPLICATE_PENALTY * match_count.get(_match_key(t1k, t2k), 0)
                - OPPONENT_PENALTY * sum(
                    opponent_count.get(_pair_key(u, v), 0) for u in t1k for v in t2k
                )
            )
            if best_score is None or score > best_score:
                best, best_score = [(t1k, t2k)], score
            elif score == best_score:
                best.append((t1k, t2k))
    if not best:
        return None
    if rng is not None:
        return rng.choice(best)
    return best[0]
