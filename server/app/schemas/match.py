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
    # 「这场在几号场」：多场地赛事按"轮内第 i 场 → 第 i 片场地"推导出来的展示标签。
    # 与 court_name 的区别：court_name 读的是 matches.court_id（永远为空 —— 排程不写它，
    # 因为编辑赛事时 courts 会整体删了重建）；court_label 不落库、纯推导，只用于显示。
    court_label: str | None = None
    start_time: time | None = None
    end_time: time | None = None
    score_a: int | None = None
    score_b: int | None = None
    # 比分版本号：客户端记分时原样带回来做乐观锁（见 ScoreUpdate.expected_version）
    score_version: int = 0
    winner_pairing_id: int | None = None
    # referee 是「谁执裁过」的历史记录，卸任后仍保留；
    # active_referee 才是当前在任裁判（为空表示该场暂无裁判可记分）
    referee: PlayerInfo | None = None
    active_referee: PlayerInfo | None = None
    referee_released_at: datetime | None = None
    status: str
    can_referee: bool = False
    support_a: int = 0
    support_b: int = 0
    my_support: str | None = None
    support_a_users: list[str] = []
    support_b_users: list[str] = []
    is_swapped: bool = False
    # 「这场现在还不能开打，因为这几个人还在别的场上」——提前告诉前端，别等裁判
    # 点一下记分才弹一个错误。空列表 = 可以开打（含已开打/已结束的场次）。
    # 只在**多场地赛事**里填（单场地赛事一场一场打，不存在撞期，老行为不变）。
    blocked_by: list[dict] = []
    duration_seconds: int | None = None
    # 比赛开始/结束时间：前端据此本地走字（配合 now 换算基准，不依赖本机时钟）
    started_at: datetime | None = None
    ended_at: datetime | None = None
    # 服务端当前时间：前端用 (now - started_at) 作为「已进行秒数」的基准，
    # 从而不依赖任何一台设备的本机时钟与所在时区
    now: datetime | None = None
    # 所属赛事状态：已结束（finished）时前端把整页置为只读
    tournament_status: str | None = None

    class Config:
        from_attributes = True


class RoundOut(BaseModel):
    id: int
    round_number: int
    status: str
    is_regenerated: bool
    matches: list[MatchOut] = []
    # 轮空名单。**列表**而不是单值：12 人 2 片场地时一轮有 4 个人轮空，
    # 老字段 bye_player 只放其中一位（取 id 最小），多人轮空时会误导。
    # 两个字段同时返回，老客户端继续读 bye_player 不受影响。
    bye_players: list[PlayerInfo] = []
    bye_player: PlayerInfo | None = None
    # 是否「当前这一批」：正在进行的场次所在轮次 + 最早一个还有未打完场次的轮次。
    # 由服务端算（不能各端自己算）：开启跳场后"最早未完成的场次"≠"正在打的场次"，
    # 前后端口径一旦不一致，就会出现「按钮给 X 场派裁判、裁判跑去了 Y 场」。
    is_current: bool = False

    class Config:
        from_attributes = True


class ScoreUpdate(BaseModel):
    score_a: int
    score_b: int
    force_end: bool = False
    # 乐观锁：客户端读到的 score_version 原样带回来。
    # 服务端发现版本已变（别人先改过）→ 409 + 当前比分与版本，客户端把增量叠上去重试。
    # 不传（旧客户端）就退化成原来的"后写覆盖"，保证升级期间不 500/不阻断。
    expected_version: int | None = None


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
