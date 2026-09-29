from fastapi import APIRouter, Depends, HTTPException, Request
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.core.database import get_db
from app.core.security import require_user
from app.core.audit import audit, get_client_ip
from app.api.matches import load_writable_tournament
from app.models.user import User
from app.models.round import Match, MatchStatus, Notification

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
    # 认领规则（按真实使用场景放宽，前端会先弹确认框再发请求）：
    # - 已有在任裁判时也允许认领，等于「顶替」：裁判临时有事要转让、或原裁判手机
    #   没电了让别人代为执裁，都会发生；被顶替的人会收到站内消息，不至于白跑一场。
    # - 参赛选手也允许认领：同样是为了「借别人的手机代认领」这种场景 ——
    #   接口层无法区分手机后面坐着的是谁，加限制只会挡住正常使用。
    # - 原裁判曾卸任（referee_released_at 非空）的场次同样任何人都能接手；
    #   原来「他人不可覆盖历史记录」会导致这种场次只有原裁判能收回。
    replaced_referee_id = None
    if m.referee_id is not None and m.referee_id != user.id and m.has_active_referee:
        replaced_referee_id = m.referee_id

    m.referee_id = user.id
    # 接手 / 收回都清空卸任时间，恢复在任状态
    m.referee_released_at = None
    if replaced_referee_id:
        # 被顶替的人要能知道：否则他会按约定去执裁，到场才发现已不是自己的场次
        db.add(Notification(
            user_id=replaced_referee_id,
            tournament_id=tournament_id,
            type="referee_replaced",
            message="您认领的裁判场次已由他人接手，如仍需执裁请重新认领。",
        ))

    await audit(user=user, action="referee_claim", target_type="match", target_id=match_id,
                detail={"tournament_id": tournament_id, "replaced_referee_id": replaced_referee_id},
                ip=get_client_ip(request), user_agent=request.headers.get("user-agent"))
    # 先提交再广播：订阅者收到 referee_claimed 后回查比赛详情时，
    # 未提交的话看到的仍是无裁判状态，这一次实时更新就被吞掉
    await db.commit()
    from app.core.websocket import manager
    await manager.broadcast(tournament_id, {
        "type": "referee_claimed",
        "match_id": match_id,
        "referee_id": user.id,
    })
    return {"ok": True}


@router.post("/matches/{match_id}/release-referee")
async def release_referee(
    tournament_id: int,
    match_id: int,
    request: Request,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    # 行锁：与 claim_referee 用同一把锁串行化。否则「A 点卸任」与「B 顶替」并发时，
    # B 先提交成为新裁判、A 的事务随后仍会把 referee_released_at 写上，
    # 最终变成「referee_id=B 但已卸任」——B 收到认领成功却没有记分权限。
    result = await db.execute(
        select(Match)
        .where(Match.id == match_id, Match.tournament_id == tournament_id)
        .with_for_update()
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
    await audit(user=user, action="referee_release", target_type="match", target_id=match_id,
                detail={"tournament_id": tournament_id}, ip=get_client_ip(request), user_agent=request.headers.get("user-agent"))
    # 先提交再广播（同上）
    await db.commit()
    from app.core.websocket import manager
    await manager.broadcast(tournament_id, {
        "type": "referee_released",
        "match_id": match_id,
    })
    return {"ok": True}
