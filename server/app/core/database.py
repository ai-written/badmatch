from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

settings = get_settings()

# pool_pre_ping：取用连接前先探活，发现已被对端关闭（数据库重启、连接被
# 中间设备或超时回收）就丢弃并重建，而不是把死连接交出去用。
# 不加这个的话，数据库重启后连接池里的旧连接仍会被使用，
# 表现为持续 500（asyncpg InterfaceError: connection is closed），
# 必须重启服务端才能恢复。
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    pool_pre_ping=True,
)
async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
