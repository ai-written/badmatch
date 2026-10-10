"""随机指派裁判的纯逻辑（不碰数据库，便于单测）。

规则（与产品确认过）：
- 候选人 = 调用方给的候选人池里，**不参加被指派那一场**的人（"场下人员"）；
- **真随机**：不按执裁次数加权，直接在可用候选人里等概率挑一个；
- 同一轮次里一个人最多当一场的裁判（同时开打时人不可能分身）；
- 只填空缺：已经有在任裁判的场次由调用方排除，本函数不处理；
- 没有候选人（例如在场刚好只剩这一场的 4 人）就**跳过**该场，由调用方告诉用户。
"""
from __future__ import annotations

import random


def can_assign(scope, *, user_id, role, creator_id, candidate_ids):
    """谁能点「分配裁判」——两种入口两套规则（产品确认过，抽成纯函数便于单测）：

    - scope="current"（对阵表筛选行那个按钮，只分当前这一批）：**本赛事在场选手**；
    - scope="all"（赛事详情页的「一次性分配裁判」，一次分完整局）：
      **房主 / 管理员 / 超级管理员**。刻意不给「在场选手」开口子 ——
      一次性把整局分完影响面大，普通选手用按批次那个入口就够了。
    """
    if scope == "all":
        return user_id == creator_id or role in ("admin", "superadmin")
    return user_id in candidate_ids


def assign_referees(
    matches,
    candidate_ids,
    busy_ids=(),
    rng=None,
):
    """给一组比赛随机指派裁判。

    matches: [(match_id, {本场 4 名选手的 id}), ...]，按要指派的先后顺序
    candidate_ids: 候选人（通常是在场选手；本函数不查库）
    busy_ids: 本轮已经在执裁的人（已有在任裁判的场次），不能再被指派
    rng: random.Random 或 None（None = 模块级 random，即真随机；测试传固定种子）

    返回 [(match_id, user_id), ...]；被跳过的场次不出现在结果里。
    """
    if rng is None:
        rng = random
    busy = set(busy_ids)
    picked: list[tuple[int, int]] = []
    for match_id, players in matches:
        pool = [c for c in candidate_ids if c not in players and c not in busy]
        if not pool:
            continue
        chosen = rng.choice(pool)
        picked.append((match_id, chosen))
        busy.add(chosen)
    return picked
