"""「优先场地」模式的对局生成（纯逻辑，不碰库，便于单测）。

与「优先公平性」（事前排完整赛程）的区别：这边**打完一场才排下一场**，所以

- 只能从**此刻空闲的人**里挑（否则场地要空等，与"优先场地"自相矛盾）；
- 每人场次**刻意允许差 1 场**：严格相等就得在尾段等人，那正是这个模式要避免的；
- 打分沿用同一套权重（搭档 3 / 完全相同的对局 20 / 对手 2），尽量少重复，
  但它天生是"近视"的 —— 看不到后面谁空出来，所以搭档重复会比事前排程多一些
  （实测 12 人 36 场：现状 6 次 = 理论下限，边打边排约 13 次）。

混双（`mixed_doubles=True`）：候选从"4 个空闲的人"换成"2 男 + 2 女"，分队也只有
"一男一女 vs 一男一女"那 2 种（与 scheduler._candidate_lineups 同一套枚举）。
凑不出 2 男 2 女就返回 None（场地先空着，等对面打完放人出来），绝不排一场非法对阵。
"""
from app.engine.scheduler import (
    FEMALE, MALE,
    _candidate_lineups, _lineup_score,
)

# 「场次少的人先上」这条的权重：pool 里最多差 1 个场次（差 4 个名额的量级），
# 给到 100 就足以压过任何搭档/对手惩罚，保证"先补齐落后的"。
PLAYED_WEIGHT = 100


def pick_court_first_match(free_players, played, target,
                           partner_count=None, opponent_count=None, match_count=None,
                           rng=None, mixed_doubles=False, genders=None):
    """从空闲的人里挑一场（4 人 + 分队）；凑不齐 4 个或都打满了返回 None。

    free_players: 此刻空闲的选手 id（调用方负责排除"正在打的人"）
    played: {player_id: 已打完场次数}
    target: 每人目标场次（4M/N）
    rng: 并列时的随机源。**不传就是确定性的**（单测要稳定）；
         实际补排时传 random 模块 —— 开局所有人都是 0 场、没有任何历史时，
         所有组合得分完全相同，不随机的话会永远排出"名单前 4 个 vs 接着 4 个"
         这种机械序列（实测踩过：12 人开赛永远是 1/2 vs 3/4、5/6 vs 7/8）。
    mixed_doubles + genders: 混双模式下候选只有"2 男 + 2 女"，分队只有"一男一女 vs
         一男一女"；空闲的人里凑不出 2 男 2 女就返回 None（宁可让这片场地先空着，
         也不排一场非混双的对局）。
    返回 ((a, b), (c, d))，两队内部都按 (小 id, 大 id) 排列；调用方可直接落库。
    """
    if partner_count is None:
        partner_count = {}
    if opponent_count is None:
        opponent_count = {}
    if match_count is None:
        match_count = {}
    if mixed_doubles and not genders:
        # 混双却没拿到性别表：宁可不排这一场（场地先空着），也不能排出非法对阵
        return None

    eligible = [p for p in free_players if played.get(p, 0) < target]
    if len(eligible) < 4:
        return None

    def _enough(pool):
        """这一档人够不够凑一场（混双要 2 男 2 女，普通双打 4 个人就行）。"""
        if not mixed_doubles:
            return len(pool) >= 4
        men = sum(1 for p in pool if genders.get(p) == MALE)
        women = sum(1 for p in pool if genders.get(p) == FEMALE)
        return men >= 2 and women >= 2

    # 场次最少的一档优先；不够 4 个人就放宽一档（这就是"允许差 1 场"的实现）。
    # 混双的"够不够"按 2 男 2 女算：低档里全是男的（或全是女的）时必须放宽，
    # 否则这一片场地会一直空着，直到那些人打满为止。
    lowest = min(played.get(p, 0) for p in eligible)
    pool = eligible
    for extra in (0, 1):
        narrowed = [p for p in eligible if played.get(p, 0) <= lowest + extra]
        if _enough(narrowed):
            pool = narrowed
            break
    else:                                    # 三级放宽都不够，就用全部合格的人
        pool = eligible
    if not _enough(pool):
        return None

    best: list = []                          # 并列最优的候选（开局时会有一大把）
    best_score = None
    for combo, splits in _candidate_lineups(sorted(pool), genders, mixed_doubles):
        base = -PLAYED_WEIGHT * sum(played.get(p, 0) for p in combo)
        for t1k, t2k in splits:
            score = _lineup_score(t1k, t2k, base, partner_count,
                                  opponent_count, match_count)
            if best_score is None or score > best_score:
                best, best_score = [(t1k, t2k)], score
            elif score == best_score:
                best.append((t1k, t2k))
    if not best:
        return None
    if rng is not None:
        return rng.choice(best)
    return best[0]
