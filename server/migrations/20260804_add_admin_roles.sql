-- 角色扩展：现有 admin 升级为 superadmin，并给 registrations 加唯一约束
-- 适用于 PostgreSQL；执行前请先备份数据库

BEGIN;

UPDATE users SET role = 'superadmin' WHERE role = 'admin';

-- 清理历史重复报名后再加唯一约束
DELETE FROM registrations a
USING registrations b
WHERE a.id < b.id
  AND a.tournament_id = b.tournament_id
  AND a.user_id = b.user_id;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'uq_registrations_tournament_user'
  ) THEN
    ALTER TABLE registrations
      ADD CONSTRAINT uq_registrations_tournament_user
      UNIQUE (tournament_id, user_id);
  END IF;
END $$;

COMMIT;
