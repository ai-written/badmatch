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


async def _migration_applied(conn: AsyncConnection, name: str) -> bool:
    await conn.execute(
        text("CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY)")
    )
    result = await conn.execute(
        text("SELECT 1 FROM schema_migrations WHERE name = :name"),
        {"name": name},
    )
    return result.scalar_one_or_none() is not None


async def _mark_migration(conn: AsyncConnection, name: str) -> None:
    await conn.execute(
        text(
            "INSERT INTO schema_migrations (name) VALUES (:name) "
            "ON CONFLICT (name) DO NOTHING"
        ),
        {"name": name},
    )


async def run_startup_migrations(conn: AsyncConnection) -> None:
    if not await _table_exists(conn, "users"):
        # 全新库由 create_all 完整创建，无需补丁
        return

    # 老库 admin 角色升级为 superadmin：只执行一次，
    # 避免每次重启都把后来新设的 admin 再次升级
    if not await _migration_applied(conn, "upgrade_admin_to_superadmin"):
        logger.info("migration: upgrade admin -> superadmin (once)")
        await conn.execute(
            text("UPDATE users SET role = 'superadmin' WHERE role = 'admin'")
        )
        await _mark_migration(conn, "upgrade_admin_to_superadmin")

    if not await _column_exists(conn, "users", "email"):
        logger.info("migration: adding users.email column")
        await conn.execute(text("ALTER TABLE users ADD COLUMN email VARCHAR(255)"))
    if not await _constraint_exists(conn, "uq_users_email"):
        logger.info("migration: adding uq_users_email constraint")
        await conn.execute(
            text("ALTER TABLE users ADD CONSTRAINT uq_users_email UNIQUE (email)")
        )

    # 早期 update_profile 只 strip 不 lower，可能存下带大写的邮箱。
    # 统一规范成小写，否则「忘记密码」按小写查询命中不了这些账号。
    # 注意不能直接 UPDATE：若历史数据里已存在 A@x.com 与 a@x.com 各占一个账号
    # （唯一约束是大小写敏感的，重复能存进去），lower 后会违反唯一约束、
    # 导致启动失败。先检测冲突：保留 id 最小的那个，其余置空并告警。
    if not await _migration_applied(conn, "normalize_user_emails_lowercase"):
        dupes = (await conn.execute(text(
            "SELECT lower(email) AS e, count(*) AS c, min(id) AS keep_id "
            "FROM users WHERE email IS NOT NULL AND email <> '' "
            "GROUP BY lower(email) HAVING count(*) > 1"
        ))).all()
        for e, c, keep_id in dupes:
            logger.warning(
                "migration: 邮箱 %s 存在 %d 个大小写变体（保留 id=%s，其余置空）", e, c, keep_id
            )
            await conn.execute(
                text(
                    "UPDATE users SET email = NULL "
                    "WHERE email IS NOT NULL AND lower(email) = :e AND id <> :keep"
                ),
                {"e": e, "keep": keep_id},
            )
        result = await conn.execute(text(
            "UPDATE users SET email = lower(email) "
            "WHERE email IS NOT NULL AND email <> lower(email)"
        ))
        if result.rowcount:
            logger.info("migration: 已将 %d 个邮箱规范为小写", result.rowcount)
        await _mark_migration(conn, "normalize_user_emails_lowercase")

    if not await _column_exists(conn, "users", "token_version"):
        logger.info("migration: adding users.token_version column")
        await conn.execute(
            text("ALTER TABLE users ADD COLUMN token_version INTEGER NOT NULL DEFAULT 0")
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
        # 左右场地交换（裁判操作、所有人共享）
        if not await _column_exists(conn, "matches", "is_swapped"):
            logger.info("migration: adding matches.is_swapped column")
            await conn.execute(
                text(
                    "ALTER TABLE matches "
                    "ADD COLUMN is_swapped BOOLEAN NOT NULL DEFAULT false"
                )
            )
        # 裁判卸任时间：referee_id 保留为执裁历史，是否在任由本列判断
        if not await _column_exists(conn, "matches", "referee_released_at"):
            logger.info("migration: adding matches.referee_released_at column")
            await conn.execute(
                text("ALTER TABLE matches ADD COLUMN referee_released_at TIMESTAMP")
            )

    # 移除已废弃的报名费列
    if await _column_exists(conn, "tournaments", "entry_fee"):
        logger.info("migration: dropping tournaments.entry_fee column")
        await conn.execute(text("ALTER TABLE tournaments DROP COLUMN entry_fee"))

    # 定时报名开放时间列
    if not await _column_exists(conn, "tournaments", "registration_open_at"):
        logger.info("migration: adding tournaments.registration_open_at column")
        await conn.execute(
            text("ALTER TABLE tournaments ADD COLUMN registration_open_at TIMESTAMP")
        )
