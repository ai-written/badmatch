-- 混双演练数据：8 人报名（4 男 4 女），报名中（未开赛），1 片场地，每人 4 场
--
-- 用法（dev 的 docker compose）：
--   Get-Content server\scripts\seed_mixed_demo.sql |
--     docker compose -f docker-compose.dev.yml exec -T db psql -U badminton -d badminton
--
-- 注意：
--   · 可重复执行，但**会先删掉同名赛事再重建**（id 会变）；已在打的同名赛事别跑。
--   · 8 人只能开 1 片场地：「每片场地至少 5 人（4 人上场 + 1 人执裁）」这条规则决定的，
--     要两片场地得凑到 10 人（比如 5 男 5 女，才同时满足混双的男女相等）。
--   · 8 人 8 场 = 每人 4 场；混双搭档池是 男×女 = 16 种，正好够 16 个搭档名额 ——
--     这一档能排出"搭档完全不重复"的赛程（14 场就不行了，必然重复 12 次）。
--   · 所有账号密码统一 test1234；混双男1 是房主（点「开始比赛」会用到）。
\set ON_ERROR_STOP on
BEGIN;

-- 0) 清掉上一次跑出来的同名赛事（只删这一份测试数据）
DELETE FROM registrations WHERE tournament_id IN (
  SELECT id FROM tournaments WHERE title = '混双演练 · 8人4男4女'
);
DELETE FROM courts WHERE tournament_id IN (
  SELECT id FROM tournaments WHERE title = '混双演练 · 8人4男4女'
);
DELETE FROM tournaments WHERE title = '混双演练 · 8人4男4女';

-- 1) 8 名选手：4 男 4 女，密码统一 test1234
INSERT INTO users (username, password_hash, avatar, gender, role, is_active, created_at) VALUES
  ('混双男1', '$2b$12$h30DktbQxr58wuD/tuVIa.L9Y8HWOFTCrC1ZSBgjC3NQ/bMpJpbp2', '', 'M', 'user', true, now() - interval '10 days'),
  ('混双男2', '$2b$12$h30DktbQxr58wuD/tuVIa.L9Y8HWOFTCrC1ZSBgjC3NQ/bMpJpbp2', '', 'M', 'user', true, now() - interval '10 days'),
  ('混双男3', '$2b$12$h30DktbQxr58wuD/tuVIa.L9Y8HWOFTCrC1ZSBgjC3NQ/bMpJpbp2', '', 'M', 'user', true, now() - interval '10 days'),
  ('混双男4', '$2b$12$h30DktbQxr58wuD/tuVIa.L9Y8HWOFTCrC1ZSBgjC3NQ/bMpJpbp2', '', 'M', 'user', true, now() - interval '10 days'),
  ('混双女1', '$2b$12$h30DktbQxr58wuD/tuVIa.L9Y8HWOFTCrC1ZSBgjC3NQ/bMpJpbp2', '', 'F', 'user', true, now() - interval '10 days'),
  ('混双女2', '$2b$12$h30DktbQxr58wuD/tuVIa.L9Y8HWOFTCrC1ZSBgjC3NQ/bMpJpbp2', '', 'F', 'user', true, now() - interval '10 days'),
  ('混双女3', '$2b$12$h30DktbQxr58wuD/tuVIa.L9Y8HWOFTCrC1ZSBgjC3NQ/bMpJpbp2', '', 'F', 'user', true, now() - interval '10 days'),
  ('混双女4', '$2b$12$h30DktbQxr58wuD/tuVIa.L9Y8HWOFTCrC1ZSBgjC3NQ/bMpJpbp2', '', 'F', 'user', true, now() - interval '10 days')
ON CONFLICT (username) DO UPDATE
  SET gender = EXCLUDED.gender, is_active = true, password_hash = EXCLUDED.password_hash;

-- 2) 赛事：报名中 / 8 人 / 8 场（每人 4 场）/ 现在可开赛
--    注意：8 人只能开 1 片场地（court_count 规则是每片场地至少 5 人）
INSERT INTO tournaments (
  creator_id, title, description, location, start_date, end_date,
  total_matches, plan_total_matches, points_to_win, max_participants,
  status, schedule_mode, mixed_doubles
)
SELECT u.id,
       '混双演练 · 8人4男4女',
       '用于验证「开始比赛」时的混双选择框：4 男 4 女，条件满足；换成 5 男 3 女就不会弹框。',
       '城南体育馆 1 号场',
       date_trunc('day', now()) + interval '19 hours',
       date_trunc('day', now()) + interval '21 hours',
       8, 8, 11, 8,
       'OPEN', 'balanced', false
FROM users u
WHERE u.username = '混双男1';

-- 3) 报名（8 人，按「男1 女1 男2 女2 …」交错报名，模拟真实报名顺序）
INSERT INTO registrations (tournament_id, user_id, is_active, created_at)
SELECT t.id, u.id, true,
       now() - interval '2 hours' + (CASE u.username
         WHEN '混双男1' THEN 1 WHEN '混双女1' THEN 2
         WHEN '混双男2' THEN 3 WHEN '混双女2' THEN 4
         WHEN '混双男3' THEN 5 WHEN '混双女3' THEN 6
         WHEN '混双男4' THEN 7 WHEN '混双女4' THEN 8
       END) * interval '3 minutes'
FROM tournaments t
JOIN users u ON u.username IN (
  '混双男1', '混双女1', '混双男2', '混双女2',
  '混双男3', '混双女3', '混双男4', '混双女4'
)
WHERE t.title = '混双演练 · 8人4男4女'
ON CONFLICT (tournament_id, user_id) DO UPDATE
  SET is_active = true, cancelled_at = NULL, cancelled_by_id = NULL;

-- 4) 1 片场地
INSERT INTO courts (tournament_id, name, sort_order)
SELECT t.id, '1 号场', 0
FROM tournaments t
WHERE t.title = '混双演练 · 8人4男4女';

COMMIT;

-- 5) 结果核对
SELECT t.id AS tournament_id, t.title, t.status, t.max_participants, t.total_matches,
       t.schedule_mode, t.mixed_doubles
FROM tournaments t WHERE t.title = '混双演练 · 8人4男4女';

SELECT count(*) FILTER (WHERE u.gender = 'M') AS 男,
       count(*) FILTER (WHERE u.gender = 'F') AS 女,
       count(*) FILTER (WHERE u.gender IS NULL) AS 未设置,
       count(*) AS 报名总数
FROM registrations r
JOIN users u ON u.id = r.user_id
JOIN tournaments t ON t.id = r.tournament_id
WHERE t.title = '混双演练 · 8人4男4女' AND r.is_active;

SELECT u.username, u.gender, r.created_at
FROM registrations r
JOIN users u ON u.id = r.user_id
JOIN tournaments t ON t.id = r.tournament_id
WHERE t.title = '混双演练 · 8人4男4女'
ORDER BY r.created_at;
