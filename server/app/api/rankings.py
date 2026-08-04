from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.core.database import get_db
from app.models.tournament import Tournament, PlayerStats
from app.models.user import User
from app.schemas.ranking import PlayerRanking, RankingResponse

router = APIRouter(prefix="/api/tournaments/{tournament_id}", tags=["rankings"])


@router.get("/rankings", response_model=RankingResponse)
async def get_rankings(
    tournament_id: int,
    db: AsyncSession = Depends(get_db),
):
    t = await db.execute(select(Tournament).where(Tournament.id == tournament_id))
    tournament = t.scalar_one_or_none()
    if not tournament:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="赛事不存在")

    result = await db.execute(
        select(PlayerStats, User.username, User.avatar)
        .join(User, PlayerStats.user_id == User.id)
        .where(PlayerStats.tournament_id == tournament_id)
        .order_by(
            PlayerStats.matches_won.desc(),
            (PlayerStats.points_for - PlayerStats.points_against).desc(),
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
        tournament_title=tournament.title,
        rankings=rankings,
    )
