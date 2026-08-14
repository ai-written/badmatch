from pydantic import BaseModel
from datetime import datetime, time


class TimeSlotCreate(BaseModel):
    start_time: time
    end_time: time


class TimeSlotOut(BaseModel):
    id: int
    start_time: time
    end_time: time

    class Config:
        from_attributes = True


class CourtCreate(BaseModel):
    name: str
    sort_order: int = 0
    time_slots: list[TimeSlotCreate] = []


class CourtOut(BaseModel):
    id: int
    name: str
    sort_order: int
    time_slots: list[TimeSlotOut] = []

    class Config:
        from_attributes = True


class TournamentCreate(BaseModel):
    title: str
    description: str | None = None
    location: str | None = None
    start_date: datetime
    end_date: datetime
    max_participants: int
    total_matches: int | None = None
    points_to_win: int = 11
    # 定时开放报名：启用时 registration_open_at 必填且必须晚于当前时间
    enable_scheduled_registration: bool = False
    registration_open_at: datetime | None = None
    courts: list[CourtCreate] = []
    preselect_player_ids: list[int] = []


class TournamentBrief(BaseModel):
    id: int
    title: str
    location: str | None
    start_date: datetime
    end_date: datetime
    max_participants: int
    status: str
    registered_count: int = 0
    court_name: str | None = None
    total_matches: int | None = None
    points_to_win: int = 11
    registration_open_at: datetime | None = None
    created_at: str

    class Config:
        from_attributes = True


class TournamentDetail(BaseModel):
    id: int
    creator_id: int
    title: str
    description: str | None
    location: str | None
    start_date: datetime
    end_date: datetime
    max_participants: int
    status: str
    courts: list[CourtOut] = []
    registered_count: int = 0
    total_matches: int | None = None
    points_to_win: int = 11
    registration_open_at: datetime | None = None
    server_now: datetime
    is_registered: bool = False
    created_at: str

    class Config:
        from_attributes = True


class RegistrationOut(BaseModel):
    id: int
    user_id: int
    username: str
    avatar: str
    created_at: str

    class Config:
        from_attributes = True
