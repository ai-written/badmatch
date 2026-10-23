"""指派裁判的纯逻辑（不碰数据库，便于单测）。

规则（与产品确认过）：
- 候选人 = 调用方给的候选人池里，**不参加被指派那一场**的人（"场下人员"）；
- **均衡轮转 + 并列随机**：优先挑 `served` 里"已安排执裁场次"最少的人，
  次数并列的人之间**等概率随机**（所以还是"随机分配"，只是把次数拉平）；
  不传 `served` 时退化为纯随机（老行为，调用方不传就是它）；
- 同一轮次里一个人最多当一场的裁判（同时开打时人不可能分身）；
- 刚在某一场上卸任的人不会被自动分回**那一场**（他明确说了不行；想吹回来手动认领）；
- 只填空缺：已经有在任裁判的场次由调用方排除，本函数不处理；
- 没有候选人（例如在场刚好只剩这一场的 4 人）就**跳过**该场，由调用方告诉用户。
"""
from __future__ import annotations

import random


def can_assign(scope, *, user_id, role, creator_id, candidate_ids):
    """谁能点「分配裁判」——两种入口的口径（抽成纯函数便于单测）：

    - scope="current"（对阵表筛选行那个按钮，只分当前这一批）：**本赛事在场选手**；
    - scope="all"（赛事详情页的「一次性分配裁判」，一次分完整局）：
      **房主 / 管理员 / 超级管理员 / 本赛事在场选手**。
      在场选手这条是后加的（原先只给管理者）：整局分比赛本来就是场上这帮人的事
      —— 组织者常常就是选手本人，他得先退出比赛界面去详情页点，反而不如直接点。
      风险也可控：这个接口**只填空缺、从不覆盖已有裁判**（想换裁判走「顶替」），
      而且按钮上直接写着还剩几场、点之前还有确认弹窗，所以不存在"一键毁掉赛程"。
      `candidate_ids` 就是「在场选手」（未退赛、账号未禁用）—— 与候选人池同一口径，
      不额外查库；不在场的人（已退赛/路人）依然不行。
    """
    if scope == "all":
        return user_id == creator_id or role in ("admin", "superadmin") or user_id in candidate_ids
    return user_id in candidate_ids


def assign_referees(
    matches,
    candidate_ids,
    busy_ids=(),
    rng=None,
    served=None,
    forbidden=None,
):
    """给一组比赛指派裁判（均衡轮转 + 并列随机）。

    matches: [(match_id, {本场 4 名选手的 id}), ...]，按要指派的先后顺序
    candidate_ids: 候选人（通常是在场选手；本函数不查库）
    busy_ids: 本轮已经在执裁的人（已有在任裁判的场次），不能再被指派
    rng: random.Random 或 None（None = 模块级 random；测试传固定种子）
    served: {user_id: 已安排执裁场次数}。**只读，不会被本函数修改**：
            调用方传进来的是"到目前为止这个人要吹几场"，本函数只在
            "次数最少"的那些人里随机挑（所以是均衡轮转，不是纯抽签）。
            传 None / 空字典 = 退化成纯随机（等概率挑 pool 里的任何人）。
            跨批次要继续均衡的话，调用方要把**上一批的结果**累加进去再传下一批
            （本函数不改你的字典，就是为了让这件事在调用方显式发生）。
    forbidden: {match_id: {user_id, ...}} —— 这些人不能吹**这一场**。
            用途：刚在这卸任的人不该被自动分回来（他明确说了不行）。
            均衡计数是"在任场次"，卸任会让他变成"最少"的那个，
            不加这层就会立刻把他分回同一场；想吹回来可以手动认领。

    返回 [(match_id, user_id), ...]；被跳过的场次不出现在结果里。
    """
    if rng is None:
        rng = random
    busy = set(busy_ids)
    forbidden = forbidden or {}
    picked: list[tuple[int, int]] = []
    for match_id, players in matches:
        banned = forbidden.get(match_id) or ()
        pool = [c for c in candidate_ids
                if c not in players and c not in busy and c not in banned]
        if not pool:
            continue
        if served:
            # 均衡：只保留"已安排场次最少"的那一档候选人，再在他们之间随机。
            # 不按次数加权（那会让次数少的人被反复挑中），而是硬性分档 + 档内等概率。
            fewest = min(served.get(c, 0) for c in pool)
            pool = [c for c in pool if served.get(c, 0) == fewest]
        chosen = rng.choice(pool)
        picked.append((match_id, chosen))
        busy.add(chosen)
    return picked
