from pydantic import BaseModel


class NotificationOut(BaseModel):
    id: int
    tournament_id: int
    type: str
    message: str
    is_read: bool
    created_at: str


class NotificationListOut(BaseModel):
    """消息列表。

    unread_count 是**全表**未读数，不是本页/本列表内的未读数：
    列表最多返回 100 条，若未读都在更早的记录里，只看列表会误判成
    「没有未读」，于是「全部已读」按钮消失、那些未读永远清不掉。
    has_more 表示是否还有更多（当前上限 100 条）未返回。
    """
    items: list[NotificationOut]
    unread_count: int
    has_more: bool
