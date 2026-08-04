from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_user
from app.models.round import Notification
from app.models.user import User
from app.schemas.notification import NotificationOut

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("", response_model=list[NotificationOut])
async def list_notifications(
    unread_only: bool = False,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(Notification)
        .where(Notification.user_id == user.id)
        .order_by(Notification.created_at.desc())
        .limit(100)
    )
    if unread_only:
        query = query.where(Notification.is_read == False)
    result = await db.execute(query)
    return [
        NotificationOut(
            id=n.id,
            tournament_id=n.tournament_id,
            type=n.type,
            message=n.message,
            is_read=n.is_read,
            created_at=n.created_at.isoformat() if n.created_at else "",
        )
        for n in result.scalars().all()
    ]


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
