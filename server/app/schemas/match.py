from pydantic import BaseModel
from datetime import datetime, time


class PlayerInfo(BaseModel):
    avatar: str = ""
    id: int
    username: str
    gender: str | None = None


class RoundPairingOut(BaseModel):
    id: int
    player_a: PlayerInfo
    player_b: PlayerInfo

    class Config:
        from_attributes = True


class MatchOut(BaseModel):
    id: int
    round_id: int
    round_number: int
    pairing_a: RoundPairingOut
    pairing_b: RoundPairingOut
    court_name: str | None = None
    start_time: time | None = None
    end_time: time | None = None
    score_a: int | None = None
    score_b: int | None = None
    winner_pairing_id: int | None = None
    referee: PlayerInfo | None = None
    status: str
    can_referee: bool = False
    support_a: int = 0
    support_b: int = 0
    my_support: str | None = None
    support_a_users: list[str] = []
    support_b_users: list[str] = []
    is_swapped: bool = False
    duration_seconds: int | None = None
    # 比赛开始/结束时间：前端据此本地走字（配合 now 换算基准，不依赖本机时钟）
    started_at: datetime | None = None
    ended_at: datetime | None = None
    # 服务端当前时间：前端用 (now - started_at) 作为「已进行秒数」的基准，
    # 从而不依赖任何一台设备的本机时钟与所在时区
    now: datetime | None = None

    class Config:
        from_attributes = True


class RoundOut(BaseModel):
    id: int
    round_number: int
    status: str
    is_regenerated: bool
    matches: list[MatchOut] = []
    bye_player: PlayerInfo | None = None

    class Config:
        from_attributes = True


class ScoreUpdate(BaseModel):
    score_a: int
    score_b: int
    force_end: bool = False


class ClaimRefereeRequest(BaseModel):
    match_id: int


class SwapUpdate(BaseModel):
    """交换左右场地。不传 swapped 则按当前状态取反（幂等：连点不会卡死）。"""
    swapped: bool | None = None


class SupportUpdate(BaseModel):
    side: str  # 'a' or 'b'


class SupportResponse(BaseModel):
    support_a: int
    support_b: int
    my_side: str | None = None
