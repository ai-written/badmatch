from pydantic import BaseModel


class NotificationOut(BaseModel):
    id: int
    tournament_id: int
    type: str
    message: str
    is_read: bool
    created_at: str
