from datetime import datetime, time
from sqlalchemy import (
    String, Integer, Boolean, Text, DateTime, Time, UniqueConstraint,
    ForeignKey, func, Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
import enum

from app.core.database import Base


class TournamentStatus(str, enum.Enum):
    OPEN = "open"          # 报名中
    ONGOING = "ongoing"    # 进行中
    FINISHED = "finished"  # 已结束


class Tournament(Base):
    __tablename__ = "tournaments"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    creator_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    location: Mapped[str | None] = mapped_column(String(256), nullable=True)
    start_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    total_matches: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)  # 总场次，null=自动计算
    points_to_win: Mapped[int] = mapped_column(Integer, default=11)
    end_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    max_participants: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[TournamentStatus] = mapped_column(
        SAEnum(TournamentStatus), default=TournamentStatus.OPEN
    )
    # 定时报名开放时间：None = 创建后立即开放报名
    registration_open_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # Relations
    creator: Mapped["User"] = relationship(back_populates="created_tournaments")
    registrations: Mapped[list["Registration"]] = relationship(back_populates="tournament", lazy="selectin")
    courts: Mapped[list["Court"]] = relationship(back_populates="tournament", lazy="selectin")
    rounds: Mapped[list["Round"]] = relationship(back_populates="tournament", lazy="selectin")
    matches: Mapped[list["Match"]] = relationship(back_populates="tournament", lazy="selectin")
    player_stats: Mapped[list["PlayerStats"]] = relationship(back_populates="tournament", lazy="selectin")


class Registration(Base):
    __tablename__ = "registrations"
    __table_args__ = (
        UniqueConstraint("tournament_id", "user_id", name="uq_registrations_tournament_user"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tournament_id: Mapped[int] = mapped_column(ForeignKey("tournaments.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    tournament: Mapped["Tournament"] = relationship(back_populates="registrations")
    user: Mapped["User"] = relationship(back_populates="registrations")


class PlayerStats(Base):
    __tablename__ = "player_stats"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tournament_id: Mapped[int] = mapped_column(ForeignKey("tournaments.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    matches_played: Mapped[int] = mapped_column(Integer, default=0)
    matches_won: Mapped[int] = mapped_column(Integer, default=0)
    matches_lost: Mapped[int] = mapped_column(Integer, default=0)
    points_for: Mapped[int] = mapped_column(Integer, default=0)
    points_against: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)  # 退赛标 False

    tournament: Mapped["Tournament"] = relationship(back_populates="player_stats")
    user: Mapped["User"] = relationship(back_populates="player_stats")


class Court(Base):
    __tablename__ = "courts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tournament_id: Mapped[int] = mapped_column(ForeignKey("tournaments.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(64))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    tournament: Mapped["Tournament"] = relationship(back_populates="courts")
    time_slots: Mapped[list["TimeSlot"]] = relationship(back_populates="court", lazy="selectin")


class TimeSlot(Base):
    __tablename__ = "time_slots"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    court_id: Mapped[int] = mapped_column(ForeignKey("courts.id"), nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)

    court: Mapped["Court"] = relationship(back_populates="time_slots")
