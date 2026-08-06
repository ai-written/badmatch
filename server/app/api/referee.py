from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.core.database import get_db
from app.core.security import require_user
from app.core.audit import audit, get_client_ip
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
    result = await db.execute(
        select(Match).where(Match.id == match_id, Match.tournament_id == tournament_id)
    )
    m = result.scalar_one_or_none()
    if not m:
        raise HTTPException(status_code=404, detail="比赛不存在")
    if m.status == MatchStatus.FINISHED:
        raise HTTPException(status_code=400, detail="比赛已结束")
    if m.referee_id is not None:
        raise HTTPException(status_code=400, detail="已有裁判认领本场比赛")

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
    if m.status == MatchStatus.FINISHED:
        raise HTTPException(status_code=400, detail="比赛已结束，无法取消裁判")

    m.referee_id = None
    await db.flush()
    from app.core.websocket import manager
    await manager.broadcast(tournament_id, {
        "type": "referee_released",
        "match_id": match_id,
    })
    await audit(user=user, action="referee_release", target_type="match", target_id=match_id,
                detail={"tournament_id": tournament_id}, ip=get_client_ip(request), user_agent=request.headers.get("user-agent"))
    return {"ok": True}
