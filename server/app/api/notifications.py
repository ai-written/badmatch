from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_user
from app.models.round import Notification
from app.models.user import User
from app.schemas.notification import NotificationOut, NotificationListOut

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("", response_model=NotificationListOut)
async def list_notifications(
    unread_only: bool = False,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    # id 兜底排序：同一批操作会在同一事务里写入多条通知，created_at 可能完全相同，
    # 只按 created_at 排序时并列行的顺序不稳定，列表顺序会莫名跳动
    query = (
        select(Notification)
        .where(Notification.user_id == user.id)
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .limit(100)
    )
    if unread_only:
        query = query.where(Notification.is_read == False)
    result = await db.execute(query)
    rows = result.scalars().all()

    # 未读数按全表统计（不受上面的 100 条上限影响），否则未读都落在更早的
    # 记录里时会被误判成「没有未读」
    unread = (await db.execute(
        select(func.count(Notification.id)).where(
            Notification.user_id == user.id,
            Notification.is_read == False,
        )
    )).scalar() or 0
    # 本页是否被 100 条上限截断
    total = (await db.execute(
        select(func.count(Notification.id)).where(Notification.user_id == user.id)
    )).scalar() or 0

    return NotificationListOut(
        items=[
            NotificationOut(
                id=n.id,
                tournament_id=n.tournament_id,
                type=n.type,
                message=n.message,
                is_read=n.is_read,
                created_at=n.created_at.isoformat() if n.created_at else "",
            )
            for n in rows
        ],
        unread_count=unread,
        has_more=total > len(rows),
    )


@router.get("/unread-count")
async def unread_count(
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(func.count(Notification.id)).where(
            Notification.user_id == user.id,
            Notification.is_read == False,
        )
    )
    return {"count": result.scalar() or 0}


@router.post("/read-all")
async def mark_all_read(
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    # 刻意**不写审计**（下面 mark_read 同理）：这是每个用户点开消息列表就会发生的
    # 高频低价值操作，写进去只会把操作日志刷满，和 ws-ticket 不写审计是同一个理由。
    # 管理面板「操作日志」的覆盖范围以 README 表格为准。
    await db.execute(
        update(Notification)
        .where(Notification.user_id == user.id, Notification.is_read == False)
        .values(is_read=True)
    )
    await db.flush()
    return {"ok": True}


@router.post("/{notification_id}/read")
async def mark_read(
    notification_id: int,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Notification).where(
            Notification.id == notification_id,
            Notification.user_id == user.id,
        )
    )
    notification = result.scalar_one_or_none()
    if not notification:
        raise HTTPException(status_code=404, detail="通知不存在")
    notification.is_read = True
    await db.flush()
    return {"ok": True}
