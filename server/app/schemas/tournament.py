from pydantic import BaseModel, Field, field_validator, model_validator
from datetime import datetime, time
from zoneinfo import ZoneInfo

# 站点本地时区（与 docker-compose 的 TZ/PGTZ、audit 的 _parse_audit_dt 一致）
SITE_TZ = ZoneInfo("Asia/Shanghai")


class TimeSlotCreate(BaseModel):
    start_time: time
    end_time: time

    @model_validator(mode="after")
    def _check_range(self):
        if self.end_time <= self.start_time:
            raise ValueError("时间段结束时间必须晚于开始时间")
        return self


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
    # 长度/范围必须与 DB 列一致，否则会抛未捕获的 DataError（500）：
    # title 是 String(128)，整数列是 int32（超界会 "integer out of range"）
    title: str = Field(min_length=1, max_length=128)
    description: str | None = None
    location: str | None = None
    start_date: datetime
    end_date: datetime
    max_participants: int = Field(ge=4, le=64)
    total_matches: int | None = Field(default=None, ge=1, le=100)
    points_to_win: int = Field(default=11, ge=1, le=100)
    # 定时开放报名：启用时 registration_open_at 必填且必须晚于当前时间
    enable_scheduled_registration: bool = False
    registration_open_at: datetime | None = None
    courts: list[CourtCreate] = []
    preselect_player_ids: list[int] = []

    @field_validator("title")
    @classmethod
    def _strip_title(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("赛事名称不能为空")
        return v

    @field_validator("start_date", "end_date", "registration_open_at")
    @classmethod
    def _to_site_naive(cls, v: datetime | None) -> datetime | None:
        """把带时区的时间换算成站点本地时间并去掉 tzinfo。

        DB 列是 naive TIMESTAMP（会话时区 Asia/Shanghai），而 asyncpg 不接受
        offset-aware 的 datetime：以前接口直接把 `2026-10-20T19:00:00+08:00`
        这类输入打成 500（DataError: can't subtract offset-naive and offset-aware
        datetimes），而不是 422。这里换算掉，语义也更正确（给的是同一时刻）。
        前端提交的本来就是 naive 本地时间，行为不变。
        """
        if v is None or v.tzinfo is None:
            return v
        return v.astimezone(SITE_TZ).replace(tzinfo=None)

    @model_validator(mode="after")
    def _check_times(self):
        # 到这里 start/end 一定都是 naive 了（上面的字段校验器统一换算过）；
        # 仍保留一次防御性判断，避免将来有人绕过校验器直接构造模型
        if (self.start_date.tzinfo is None) == (self.end_date.tzinfo is None):
            if self.end_date <= self.start_date:
                raise ValueError("结束时间必须晚于开始时间")
        return self


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


class TournamentListOut(BaseModel):
    """列表分页响应。原先返回裸数组且没有分页，前端上拉加载永远拿不到第二页。"""
    items: list[TournamentBrief]
    total: int
    has_more: bool


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
    # 取消过报名的人数：>0 时详情页才显示「取消报名记录」入口，避免平时多一次请求
    cancelled_count: int = 0
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
    # 该账号是否可用（False = 已禁用）。前端「转让房主」等"选人"场景据此刻意排除，
    # 免得把房主交给一个登录不进来的人
    user_is_active: bool = True


class CancellationOut(BaseModel):
    """取消报名 / 赛中退赛记录（两者都是标记失效、不删行，所以查得到是谁）"""
    user_id: int
    username: str
    avatar: str
    # 老数据没有这个时间（该列是后来补的），前端按「未知时间」展示
    cancelled_at: str | None = None
    # cancel = 报名阶段取消；withdraw = 开赛后退出（该场已建过 PlayerStats）
    kind: str = "cancel"

    class Config:
        from_attributes = True
