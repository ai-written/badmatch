from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.core.database import get_db
from app.core.ranking import assign_ranks
from app.core.security import require_user
from app.models.tournament import Tournament, PlayerStats
from app.models.user import User
from app.schemas.ranking import PlayerRanking, RankingResponse

router = APIRouter(prefix="/api/tournaments/{tournament_id}", tags=["rankings"])


@router.get("/rankings", response_model=RankingResponse)
async def get_rankings(
    tournament_id: int,
    db: AsyncSession = Depends(get_db),
    # 以前这是全站唯一的匿名只读接口（积分榜曾被当成"对外展示面"）。现在改成与其它
    # 读取接口一致：本站是邀请制封闭群，匿名不该看到选手姓名与战绩。
    # 分享图不受影响 —— 那是前端 canvas 画的 PNG，登录后生成、发到群里照样能看。
    user: User = Depends(require_user),
):
    # 只取「存在 + 标题」两列：整行 Tournament 会把它的 5 个 selectin 集合
    # （报名/场地/轮次/比赛/战绩）连带拉回来，而这里一个都用不上
    t = await db.execute(
        select(Tournament.id, Tournament.title).where(Tournament.id == tournament_id)
    )
    row = t.first()
    if row is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="赛事不存在")

    result = await db.execute(
        select(PlayerStats, User.username, User.avatar)
        .join(User, PlayerStats.user_id == User.id)
        .where(PlayerStats.tournament_id == tournament_id)
    )
    # 顺序与名次都交给 core/ranking：个人参赛趋势图用的是同一个函数，
    # 两处必须逐位一致（否则同一个人在两个页面上的名次会不一样）。
    # 这里不再写 order_by —— assign_ranks 内部已经排好序，少一条等价索引排序。
    #
    # 按 user_id 去重是**有意的**：库里 player_stats 没有 (tournament_id, user_id)
    # 唯一约束（只有两个单列索引），正常路径不会重复，但真出现重复行时
    # 必须只算一个人 —— 否则同一个人占两个名次、后面的人全部顺延，
    # 积分榜看着像有鬼。保留先读到的那一行。
    by_user = {stat.user_id: (stat, username, avatar)
               for (stat, username, avatar) in result.all()}
    ordered = assign_ranks([
        (stat.user_id, stat.matches_won, stat.points_for, stat.points_against, stat.is_active)
        for (stat, _username, _avatar) in by_user.values()
    ])

    rankings = []
    for user_id, rank in ordered:
        stat, username, avatar = by_user[user_id]
        rankings.append(PlayerRanking(
            rank=rank,
            user_id=stat.user_id,
            username=username,
            avatar=avatar,
            matches_played=stat.matches_played,
            matches_won=stat.matches_won,
            matches_lost=stat.matches_lost,
            points_for=stat.points_for,
            points_against=stat.points_against,
            point_diff=stat.points_for - stat.points_against,
            is_active=stat.is_active,
        ))

    return RankingResponse(
        tournament_id=tournament_id,
        tournament_title=row.title,
        rankings=rankings,
    )
