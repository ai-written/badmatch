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
    # **计划**总场次：用户当初选的量（创建/编辑/开赛/追加比赛时写入），退赛重排与重排赛程
    # 都不改它。与 total_matches 分开存的原因是两者语义不同：
    #   total_matches       = **库里当前真实的场次数**（重排后会写回真实值，详情页"共 N 场"用它）
    #   plan_total_matches  = 这场赛事一共打算打多少场（算"这次还能排多少场"的基准）
    # 曾经拿 total_matches 当基准：退赛后重排会把一整套新赛程叠在已打完的上面，
    # 而写回的膨胀值又成为下次退赛的基准 —— 8 人 14 场连退两人实测变成 20 → 27 场。
    plan_total_matches: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
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
    # 取消报名的时间：取消是「标记 is_active=False」而不是删记录，所以行还在、这里补一个时间，
    # 用于「取消报名记录」展示。重新报名时清空（又回来了就不算取消记录）。
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)

    tournament: Mapped["Tournament"] = relationship(back_populates="registrations")
    user: Mapped["User"] = relationship(back_populates="registrations")


class PlayerStats(Base):
    __tablename__ = "player_stats"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # 两列都经常作为过滤条件：积分榜按 tournament_id 查、个人战绩按 user_id 查。
    # 加索引的理由与名字约定见 core/startup_migration._FK_INDEXES（老库由启动迁移补）
    tournament_id: Mapped[int] = mapped_column(
        ForeignKey("tournaments.id"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
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
