from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.core.database import get_db
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
        # user_id 兜底：战绩与净胜分都相同时，PostgreSQL 不保证稳定顺序，
        # 同一条 SQL 两次执行可能给出不同排列，前端名次会在刷新间跳变
        .order_by(
            PlayerStats.matches_won.desc(),
            (PlayerStats.points_for - PlayerStats.points_against).desc(),
            PlayerStats.user_id.asc(),
        )
    )
    rows = result.all()

    rankings = []
    rank = 0
    for (stat, username, avatar) in rows:
        # 名次只对活跃选手递增；退赛选手显示在底部且不占名次
        if stat.is_active:
            rank += 1
        rankings.append(PlayerRanking(
            rank=rank if stat.is_active else 0,
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
