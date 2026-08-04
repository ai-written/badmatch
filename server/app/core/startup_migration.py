"""
幂等启动迁移：为老库补齐缺失的列和唯一约束。

create_all 只会创建新表，不会给已存在的表补列/约束。
这里在启动时检查并补齐，保证新库、老库都能正常运行。
"""
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

logger = logging.getLogger(__name__)


async def _table_exists(conn: AsyncConnection, table: str) -> bool:
    result = await conn.execute(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = :t"
        ),
        {"t": table},
    )
    return result.scalar_one_or_none() is not None


async def _column_exists(conn: AsyncConnection, table: str, column: str) -> bool:
    result = await conn.execute(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = :t AND column_name = :c"
        ),
        {"t": table, "c": column},
    )
    return result.scalar_one_or_none() is not None


async def _constraint_exists(conn: AsyncConnection, name: str) -> bool:
    result = await conn.execute(
        text(
            "SELECT 1 FROM pg_constraint "
            "WHERE conname = :name AND connamespace = 'public'::regnamespace"
        ),
        {"name": name},
    )
    return result.scalar_one_or_none() is not None


async def run_startup_migrations(conn: AsyncConnection) -> None:
    if not await _table_exists(conn, "users"):
        # 全新库由 create_all 完整创建，无需补丁
        return

    # 老库 admin 角色升级为 superadmin（幂等，可重复执行）
    logger.info("migration: upgrade admin -> superadmin")
    await conn.execute(
        text("UPDATE users SET role = 'superadmin' WHERE role = 'admin'")
    )

    if not await _column_exists(conn, "users", "email"):
        logger.info("migration: adding users.email column")
        await conn.execute(text("ALTER TABLE users ADD COLUMN email VARCHAR(255)"))
    if not await _constraint_exists(conn, "uq_users_email"):
        logger.info("migration: adding uq_users_email constraint")
        await conn.execute(
            text("ALTER TABLE users ADD CONSTRAINT uq_users_email UNIQUE (email)")
        )

    if await _table_exists(conn, "registrations"):
        if not await _constraint_exists(conn, "uq_registrations_tournament_user"):
            logger.info("migration: dedupe registrations + add unique constraint")
            await conn.execute(
                text(
                    "DELETE FROM registrations a USING registrations b "
                    "WHERE a.id < b.id "
                    "AND a.tournament_id = b.tournament_id "
                    "AND a.user_id = b.user_id"
                )
            )
            await conn.execute(
                text(
                    "ALTER TABLE registrations "
                    "ADD CONSTRAINT uq_registrations_tournament_user "
                    "UNIQUE (tournament_id, user_id)"
                )
            )

    if await _table_exists(conn, "matches"):
        for column in ("started_at", "ended_at"):
            if not await _column_exists(conn, "matches", column):
                logger.info("migration: adding matches.%s column", column)
                await conn.execute(
                    text(f"ALTER TABLE matches ADD COLUMN {column} TIMESTAMP")
                )

    # 移除已废弃的报名费列
    if await _column_exists(conn, "tournaments", "entry_fee"):
        logger.info("migration: dropping tournaments.entry_fee column")
        await conn.execute(text("ALTER TABLE tournaments DROP COLUMN entry_fee"))
