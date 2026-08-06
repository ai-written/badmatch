from sqlalchemy import String, DateTime, ForeignKey, Integer, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base
from datetime import datetime


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    email: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    avatar: Mapped[str] = mapped_column(String(512), default="")
    gender: Mapped[str | None] = mapped_column(String(1), nullable=True)  # M or F
    role: Mapped[str] = mapped_column(String(16), default="user")  # user or admin
    # 登出/作废机制：每次登出自增，旧 token 立即失效
    token_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    invite_code: Mapped[str | None] = mapped_column(String(32), unique=True, nullable=True)
    invited_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # Relations
    registrations: Mapped[list["Registration"]] = relationship(back_populates="user", lazy="selectin")
    player_stats: Mapped[list["PlayerStats"]] = relationship(back_populates="user", lazy="selectin")
    created_tournaments: Mapped[list["Tournament"]] = relationship(back_populates="creator", lazy="selectin")
    refereed_matches: Mapped[list["Match"]] = relationship(back_populates="referee", lazy="selectin")
