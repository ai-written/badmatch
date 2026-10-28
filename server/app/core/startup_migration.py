"""
幂等启动迁移：为老库补齐缺失的列和唯一约束。

create_all 只会创建新表，不会给已存在的表补列/约束。
这里在启动时检查并补齐，保证新库、老库都能正常运行。
"""
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

logger = logging.getLogger(__name__)

# 把每张表的自增序列对齐到 max(id)。
# 序列一旦落后于表里的最大 id，之后所有 INSERT 都会撞主键并报
# "duplicate key value violates unique constraint ..._pkey"；
# 常见成因是用显式 id 导过数据、或从 dump 恢复后没有重设序列。
# 这不是理论问题：本机 dev 库的 users 就落后过（last_value=6 / max(id)=8），
# 表现为注册一律失败，而接口把它报成「用户名或邮箱已被使用」，排查时会被完全带偏。
# 只在落后时 setval，正常情况下这条语句不做任何改动。
_SYNC_SEQUENCES_SQL = """
do $$
declare r record; mx bigint; lv bigint; ic boolean;
begin
  for r in
    select c.relname as tbl, a.attname as col,
           pg_get_serial_sequence(quote_ident(c.relname), a.attname) as seq
    from pg_class c
    join pg_attribute a on a.attrelid = c.oid
    where c.relkind = 'r' and a.attnum > 0 and not a.attisdropped
      and c.relnamespace = 'public'::regnamespace
      and pg_get_serial_sequence(quote_ident(c.relname), a.attname) is not null
  loop
    execute format('select coalesce(max(%I), 0) from %I', r.col, r.tbl) into mx;
    execute format('select last_value, is_called from %s', r.seq) into lv, ic;
    -- 判断「下一次 nextval 会不会撞上已有的 id」：is_called=false 时下一个 nextval
    -- 就是 last_value 本身（不是 last_value+1）。只看 last_value 会漏掉
    -- setval(seq, (select max(id) from t), false) 这种很常见的手工修法：
    -- 那时 last_value = max(id)、is_called = false，下一次 nextval 必然撞主键，
    -- 而 lv < mx 为假 → 不会被修正。
    if (case when ic then lv else lv - 1 end) < mx then
      execute format('select setval(%L, %s, false)', r.seq, mx + 1);
      raise notice 'migration: sequence % realigned to %', r.seq, mx + 1;
    end if;
  end loop;
end $$;
"""


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


# 早期建表时外键列一个索引都没有，而下面这些列都出现在热路径的 WHERE / JOIN 里：
#   matches.tournament_id   —— 对阵表、场次统计、赛事详情页的 pending_referee_count
#   matches.round_id        —— 每轮加载、退赛重排/删赛事时按轮删除
#   rounds.tournament_id    —— 读出该赛事所有轮次
#   round_pairings.round_id —— 追加重排的历史统计、删赛事
#   player_stats.tournament_id / user_id —— 积分榜、个人战绩
#   notifications.user_id / tournament_id —— 站内消息列表与未读计数、删赛事级联
# 名字必须与模型里 index=True 让 create_all 生成的一致（SQLAlchemy 默认 ix_<表>_<列>），
# 否则新库/老库会出现两条等价索引。
# 刻意**不加**：registrations.tournament_id 与 match_supports.match_id —— 已被各自的
# 唯一约束 (tournament_id,user_id) / (match_id,user_id) 的最左前缀覆盖；
# 其余外键列（court_id / time_slot_id / referee_id / winner_pairing_id / pairing_a_id 等）
# 不作为过滤条件（只在内存里按主键取用），加了只是给写入添负担。
_FK_INDEXES = (
    ("ix_matches_tournament_id", "matches", "tournament_id"),
    ("ix_matches_round_id", "matches", "round_id"),
    ("ix_rounds_tournament_id", "rounds", "tournament_id"),
    ("ix_round_pairings_round_id", "round_pairings", "round_id"),
    ("ix_player_stats_tournament_id", "player_stats", "tournament_id"),
    ("ix_player_stats_user_id", "player_stats", "user_id"),
    ("ix_notifications_user_id", "notifications", "user_id"),
    ("ix_notifications_tournament_id", "notifications", "tournament_id"),
)


async def _ensure_fk_indexes(conn: AsyncConnection) -> None:
    """补齐缺失的外键索引（幂等）。

    普通 CREATE INDEX（非 CONCURRENTLY，后者不能跑在事务里）会短暂持有表锁，
    本项目的表都很小，启动时瞬时完成；IF NOT EXISTS 让每次启动重复执行也无副作用。
    """
    for name, table, column in _FK_INDEXES:
        if not await _table_exists(conn, table):
            continue
        if not await _column_exists(conn, table, column):
            continue
        await conn.execute(
            text(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({column})")
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
    # 判断依据是「是否已有覆盖 email 的唯一索引」，而不是约束名：
    # create_all 建的新库里 PG 会自动命名成 users_email_key，而迁移加的叫 uq_users_email。
    # 只按名字查 → 新库第二次启动会再加一条完全重复的唯一约束，
    # 且 register 的 IntegrityError 分支也匹配不到（已让 auth.py 两个名字都认）。
    has_email_unique = (
        await conn.execute(
            text(
                "SELECT 1 FROM pg_index i "
                "JOIN pg_class c ON c.oid = i.indrelid "
                "JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = ANY(i.indkey) "
                "WHERE c.relname = 'users' AND i.indisunique AND a.attname = 'email' "
                "GROUP BY i.indexrelid HAVING count(*) = 1"
            )
        )
    ).scalar_one_or_none() is not None
    if not has_email_unique:
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

    # 账号禁用开关。默认 true，老库升级后所有人照常可用（不会误伤已有账号）。
    if not await _column_exists(conn, "users", "is_active"):
        logger.info("migration: adding users.is_active column")
        await conn.execute(
            text("ALTER TABLE users ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT true")
        )

    if await _table_exists(conn, "registrations"):
        if not await _column_exists(conn, "registrations", "cancelled_at"):
            logger.info("migration: adding registrations.cancelled_at column")
            await conn.execute(
                text("ALTER TABLE registrations ADD COLUMN cancelled_at TIMESTAMP")
            )
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
        # 比分版本号（乐观锁）：两个人同时记分时，用它发现"你的基线已经过期了"，
        # 而不是让后到的那次覆盖掉先到的那一分
        if not await _column_exists(conn, "matches", "score_version"):
            logger.info("migration: adding matches.score_version column")
            await conn.execute(
                text(
                    "ALTER TABLE matches "
                    "ADD COLUMN score_version INTEGER NOT NULL DEFAULT 0"
                )
            )
        # 裁判卸任时间：referee_id 保留为执裁历史，是否在任由本列判断
        if not await _column_exists(conn, "matches", "referee_released_at"):
            logger.info("migration: adding matches.referee_released_at column")
            await conn.execute(
                text("ALTER TABLE matches ADD COLUMN referee_released_at TIMESTAMP")
            )
        # matches.tournament_id 等外键列的索引由下面的 _ensure_fk_indexes 统一补（见那里）

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

    # 「计划总场次」：退赛重排的基准（语义见 models/tournament.py 的注释）。
    # 老库用当前 total_matches 回填 —— 那是当时能拿到的最好近似（重排把"真实场次数"
    # 写回 total_matches 的逻辑是后来才有的，老数据最多被污染过一次）。
    if not await _column_exists(conn, "tournaments", "plan_total_matches"):
        logger.info("migration: adding tournaments.plan_total_matches column")
        await conn.execute(
            text("ALTER TABLE tournaments ADD COLUMN plan_total_matches INTEGER")
        )
        await conn.execute(text("UPDATE tournaments SET plan_total_matches = total_matches"))

    # 自增序列对齐（幂等）：修一次就够，但每次启动都检查一遍，
    # 这样「用 dump 恢复过数据库」之后也不用记得手工执行 setval。
    await _ensure_fk_indexes(conn)
    await conn.execute(text(_SYNC_SEQUENCES_SQL))
