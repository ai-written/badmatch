from pydantic import BaseModel
from datetime import time


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
