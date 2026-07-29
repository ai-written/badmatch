from pydantic import BaseModel


class PlayerRanking(BaseModel):
    rank: int
    user_id: int
    username: str
    avatar: str
    matches_played: int
    matches_won: int
    matches_lost: int
    points_for: int
    points_against: int
    point_diff: int
    is_active: bool


class RankingResponse(BaseModel):
    tournament_id: int
    tournament_title: str
    rankings: list[PlayerRanking]
