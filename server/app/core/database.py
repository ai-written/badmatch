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
# expire_on_commit=False 是**业务依赖**，不要顺手改成 True：
# 1) 全仓的成功路径都是「先 db.commit() 再写审计 / 再广播」，提交后还会读对象属性
#    （如 _broadcast_match 读 m.score_a、_tournament_detail 读 t.*、audit() 读 user.id）；
#    一旦过期，这些读取会触发同步懒加载，在异步上下文里直接 MissingGreenlet（成片 500）。
# 2) 该设置由 server/tests/test_audit_order.py 里的 test_session_factory_does_not_expire_on_commit 守着。
# 如果你确实要改成 True：必须把上述所有"提交后读 ORM"改成提交前取纯数据。
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
