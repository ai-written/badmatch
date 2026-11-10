"""赛事内名次计算 —— 积分榜与「参赛排名趋势图」必须共用这一套口径。

抽出来的理由：`/tournaments/{id}/rankings`（这一场此刻的名次）和
`/auth/stats/{id}/history`（历史上每一场各自的名次）都在回答"这人第几名"。
两处各写一份排序，迟早出现「趋势图说第 3、积分榜说第 4」这种自相矛盾的
输出 —— 而这恰恰是用户最不可能原谅的那类 bug（同一个人的名次，两个页面
两个数字，会让人怀疑整套战绩都是错的）。

口径（要改的话，分享图/海报的展示也跟着变，一起看）：
- 排序：胜场 desc → 净胜分 desc → user_id asc。
  `user_id` 兜底是必须的：战绩与净胜分都相同时 PostgreSQL 不保证稳定顺序，
  同一条 SQL 两次执行可能给出不同排列，前端名次会在刷新之间跳变。
- 名次只对**在赛**选手递增（is_active=True）。退赛的人 rank=0 且排在最后 ——
  退赛前的成绩照常显示，但不能"占着第 2 名"让后面的人全部顺延。

纯逻辑、不碰 ORM：入参是普通元组，单测直接构造（见 tests/test_player_rank.py）。
"""
from typing import Iterable

# 一位参赛者的名次依据：(user_id, 胜场, 得分, 失分, 是否在赛)
RankEntry = tuple[int, int, int, int, bool]


def rank_key(
    user_id: int, matches_won: int, points_for: int, points_against: int
) -> tuple[int, int, int]:
    """名次排序键：胜场多者在前 → 净胜分高者在前 → user_id 小的在前（兜底）。"""
    return (-matches_won, -(points_for - points_against), user_id)


def assign_ranks(entries: Iterable[RankEntry]) -> list[tuple[int, int]]:
    """给一场赛事的全部参赛者算名次。

    入参：若干 `(user_id, 胜场, 得分, 失分, 是否在赛)`
    返回：`[(user_id, rank), ...]`，**已按名次排好序**（退赛的排在最后、rank=0）。

    退赛的人显式排到列表尾部（而不是留在战绩顺序里）：这里一并把展示顺序
    定下来，前端就不用再各自 sort 一遍，免得两个页面一个把退赛混在中间、
    一个沉到底，看起来像两份不同的名单。

    这里**不去重**：同一场赛事里同一个人出现两次时会各占一个名次。
    调用方负责保证一人一行 —— 现状是靠"开赛时一次性建 player_stats"这个约定
    （库里**没有** (tournament_id, user_id) 唯一约束，别以为 DB 会兜）。
    rankings.py 在调本函数前先按 user_id 去重，见那里的说明。
    """
    ordered = sorted(
        entries,
        # 在赛的在前；同一组内按战绩排
        key=lambda e: (0 if e[4] else 1, *rank_key(e[0], e[1], e[2], e[3])),
    )
    out: list[tuple[int, int]] = []
    rank = 0
    for user_id, _won, _pf, _pa, is_active in ordered:
        if is_active:
            rank += 1
        out.append((user_id, rank if is_active else 0))
    return out
