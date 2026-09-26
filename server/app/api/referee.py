from fastapi import APIRouter, Depends, HTTPException, Request
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.core.database import get_db
from app.core.security import require_user
from app.core.audit import audit, get_client_ip
from app.api.matches import load_writable_tournament
from app.models.user import User
from app.models.round import Match, MatchStatus, RoundPairing
from app.schemas.match import ClaimRefereeRequest

router = APIRouter(prefix="/api/tournaments/{tournament_id}", tags=["referee"])


@router.post("/matches/{match_id}/claim-referee")
async def claim_referee(
    tournament_id: int,
    match_id: int,
    request: Request,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    # 行锁：并发认领同一场比赛时串行化，否则两人都会读到「无裁判」，
    # 后提交者覆盖前者——前者收到「认领成功」却立刻失去权限。
    result = await db.execute(
        select(Match)
        .where(Match.id == match_id, Match.tournament_id == tournament_id)
        .with_for_update()
    )
    m = result.scalar_one_or_none()
    if not m:
        raise HTTPException(status_code=404, detail="比赛不存在")
    # 赛事已结束则该场只读，不允许再认领裁判
    await load_writable_tournament(db, tournament_id)
    if m.status == MatchStatus.FINISHED:
        raise HTTPException(status_code=400, detail="比赛已结束")
    if m.referee_id is not None:
        # referee_id 会作为执裁历史长期保留：
        # - 他人不可覆盖，避免历史记录人数与实际认领规则混乱
        # - 原裁判曾卸任（referee_released_at 非空）时，允许自己收回该场
        if m.has_active_referee:
            raise HTTPException(status_code=400, detail="已有裁判认领本场比赛")
        if m.referee_id != user.id:
            raise HTTPException(status_code=400, detail="该场已有裁判记录，不能再认领")

    # check user is not a player in this match
    pa = await db.execute(select(RoundPairing).where(RoundPairing.id == m.pairing_a_id))
    pb = await db.execute(select(RoundPairing).where(RoundPairing.id == m.pairing_b_id))
    pairing_a = pa.scalar_one()
    pairing_b = pb.scalar_one()
    match_player_ids = {
        pairing_a.player_a_id, pairing_a.player_b_id,
        pairing_b.player_a_id, pairing_b.player_b_id,
    }
    if user.id in match_player_ids:
        raise HTTPException(status_code=400, detail="参赛选手不能担任本场比赛裁判")

    m.referee_id = user.id
    # 自己收回曾卸任的场次时清空卸任时间，恢复在任状态
    m.referee_released_at = None
    await db.flush()
    from app.core.websocket import manager
    await manager.broadcast(tournament_id, {
        "type": "referee_claimed",
        "match_id": match_id,
        "referee_id": user.id,
    })
    await audit(user=user, action="referee_claim", target_type="match", target_id=match_id,
                detail={"tournament_id": tournament_id}, ip=get_client_ip(request), user_agent=request.headers.get("user-agent"))
    return {"ok": True}


@router.post("/matches/{match_id}/release-referee")
async def release_referee(
    tournament_id: int,
    match_id: int,
    request: Request,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Match).where(Match.id == match_id, Match.tournament_id == tournament_id)
    )
    m = result.scalar_one_or_none()
    if not m:
        raise HTTPException(status_code=404, detail="比赛不存在")
    if m.referee_id != user.id:
        raise HTTPException(status_code=403, detail="您不是本场比赛的裁判")
    if not m.has_active_referee:
        raise HTTPException(status_code=400, detail="您已卸任本场比赛裁判")
    # 比赛打完后不允许卸任：referee_id 要保留为「谁执裁过」的历史记录。
    # 除此外不受赛事状态限制——赛事提前结束后，认领了却没能执裁的人仍可退出。
    if m.status == MatchStatus.FINISHED:
        raise HTTPException(status_code=400, detail="比赛已结束，裁判记录已归档")

    # 只标记卸任时间，保留 referee_id 作为执裁历史；此后不再拥有记分等权限
    m.referee_released_at = datetime.now()
    await db.flush()
    from app.core.websocket import manager
    await manager.broadcast(tournament_id, {
        "type": "referee_released",
        "match_id": match_id,
    })
    await audit(user=user, action="referee_release", target_type="match", target_id=match_id,
                detail={"tournament_id": tournament_id}, ip=get_client_ip(request), user_agent=request.headers.get("user-agent"))
    return {"ok": True}
