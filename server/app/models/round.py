from datetime import datetime
from sqlalchemy import (
    UniqueConstraint,
    String, Integer, Boolean, DateTime, ForeignKey, func, Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
import enum

from app.core.database import Base


class RoundStatus(str, enum.Enum):
    PENDING = "pending"
    ONGOING = "ongoing"
    FINISHED = "finished"


class MatchStatus(str, enum.Enum):
    PENDING = "pending"
    ONGOING = "ongoing"
    FINISHED = "finished"


class Round(Base):
    __tablename__ = "rounds"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tournament_id: Mapped[int] = mapped_column(ForeignKey("tournaments.id"), nullable=False)
    round_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[RoundStatus] = mapped_column(
        SAEnum(RoundStatus), default=RoundStatus.PENDING
    )
    is_regenerated: Mapped[bool] = mapped_column(Boolean, default=False)
    is_frozen: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    tournament: Mapped["Tournament"] = relationship(back_populates="rounds")
    pairings: Mapped[list["RoundPairing"]] = relationship(back_populates="round", lazy="selectin")
    matches: Mapped[list["Match"]] = relationship(back_populates="round", lazy="selectin")


class RoundPairing(Base):
    __tablename__ = "round_pairings"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("rounds.id"), nullable=False)
    player_a_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    player_b_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)

    round: Mapped["Round"] = relationship(back_populates="pairings")
    # Matches where this pairing is team_a
    matches_as_a: Mapped[list["Match"]] = relationship(
        back_populates="pairing_a", foreign_keys="Match.pairing_a_id", lazy="selectin"
    )
    matches_as_b: Mapped[list["Match"]] = relationship(
        back_populates="pairing_b", foreign_keys="Match.pairing_b_id", lazy="selectin"
    )


class Match(Base):
    __tablename__ = "matches"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tournament_id: Mapped[int] = mapped_column(ForeignKey("tournaments.id"), nullable=False)
    round_id: Mapped[int] = mapped_column(ForeignKey("rounds.id"), nullable=False)
    pairing_a_id: Mapped[int] = mapped_column(ForeignKey("round_pairings.id"), nullable=False)
    pairing_b_id: Mapped[int] = mapped_column(ForeignKey("round_pairings.id"), nullable=False)
    court_id: Mapped[int | None] = mapped_column(ForeignKey("courts.id"), nullable=True)
    time_slot_id: Mapped[int | None] = mapped_column(ForeignKey("time_slots.id"), nullable=True)
    score_a: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score_b: Mapped[int | None] = mapped_column(Integer, nullable=True)
    winner_pairing_id: Mapped[int | None] = mapped_column(
        ForeignKey("round_pairings.id"), nullable=True
    )
    referee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    # 裁判卸任时间。referee_id 一旦写入就作为「谁执裁过」的历史保留，不再清空；
    # 是否仍在任由本字段判断：referee_id 有值且 referee_released_at 为空 = 当前裁判。
    # 这样卸任者的名字仍可展示，同时不会再拥有记分/交换场地等权限。
    referee_released_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[MatchStatus] = mapped_column(
        SAEnum(MatchStatus), default=MatchStatus.PENDING
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # 比赛开始（首次记分）
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # 比赛结束
    # 左右场地交换：由裁判操作、所有人共享（服务端为准，前端不再本地切换）
    is_swapped: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    tournament: Mapped["Tournament"] = relationship(back_populates="matches")
    round: Mapped["Round"] = relationship(back_populates="matches")
    pairing_a: Mapped["RoundPairing"] = relationship(foreign_keys=[pairing_a_id], back_populates="matches_as_a")
    pairing_b: Mapped["RoundPairing"] = relationship(foreign_keys=[pairing_b_id], back_populates="matches_as_b")
    referee: Mapped["User"] = relationship(back_populates="refereed_matches")

    @property
    def has_active_referee(self) -> bool:
        """当前是否有在任裁判（已卸任的裁判仍保留 referee_id 作为历史）。"""
        return self.referee_id is not None and self.referee_released_at is None


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    tournament_id: Mapped[int] = mapped_column(ForeignKey("tournaments.id"), nullable=False)
    type: Mapped[str] = mapped_column(String(32), default="schedule_changed")
    message: Mapped[str] = mapped_column(String(512))
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class MatchSupport(Base):
    __tablename__ = "match_supports"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    __table_args__ = (UniqueConstraint("match_id", "user_id"),)
    side: Mapped[str] = mapped_column(String(1), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
