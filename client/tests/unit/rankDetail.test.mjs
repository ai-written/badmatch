/**
 * utils/rankDetail 单测：积分榜「点胜/负数字看明细」的筛选与口径。
 *
 * 重点守住几条：
 * 1. 只算**已结束**的比赛（进行中的不计），列表条数 = 积分榜上的数字
 * 2. 「第 N 场」与对阵表同一口径（轮次顺序 + 轮内 id，含未打完的场次也占编号）
 * 3. 已结束但没有胜方的历史脏数据跳过（不能算成"负"）
 * 4. 比分按我方视角给、搭挡/对手取对
 */
import assert from 'node:assert/strict'
import test from 'node:test'

import { buildRankDetail } from '../../src/utils/rankDetail.ts'

const P = (id, username) => ({ id, username, avatar: '' })
const pair = (id, a, b) => ({ id, player_a: P(...a), player_b: P(...b) })
const match = (id, pa, pb, winner, sa, sb, status = 'finished') => ({
  id,
  pairing_a: pa,
  pairing_b: pb,
  score_a: sa,
  score_b: sb,
  winner_pairing_id: winner,
  status,
  court_name: '1号场',
  start_time: '19:20:00',
})

// 我 = 1；第1场赢（搭挡 2）、第2场输（搭挡 3）、第3场赢（搭挡 4）、第4场还在打
const rounds = () => [
  {
    id: 1, round_number: 1, matches: [
      match(101, pair(1001, [1, '张三'], [2, '李四']), pair(1002, [3, '王五'], [4, '赵六']), 1001, 11, 7),
      match(102, pair(1003, [1, '张三'], [3, '王五']), pair(1004, [2, '李四'], [4, '赵六']), 1004, 5, 11),
    ],
  },
  {
    id: 2, round_number: 2, matches: [
      match(103, pair(1005, [1, '张三'], [4, '赵六']), pair(1006, [2, '李四'], [3, '王五']), 1005, 11, 9),
      match(104, pair(1007, [2, '李四'], [4, '赵六']), pair(1008, [1, '张三'], [3, '王五']), null, null, null, 'pending'),
    ],
  },
]

test('胜场明细：只列赢的、进行中的不计', () => {
  const { rows, finished } = buildRankDetail(rounds(), 1, 'win')
  assert.equal(rows.length, 2)
  assert.equal(finished, 3)                       // 已结束 3 场（第4场还在打）
  assert.deepEqual(rows.map((r) => r.no), [1, 3]) // 第N场与对阵表一致
  assert.deepEqual(rows.map((r) => r.partner), ['李四', '赵六'])
  assert.deepEqual(rows.map((r) => r.score), ['11 : 7', '11 : 9'])
  assert.ok(rows.every((r) => r.won))
})

test('负场明细：只列输的，比分按我方视角', () => {
  const { rows, finished } = buildRankDetail(rounds(), 1, 'lose')
  assert.equal(rows.length, 1)
  assert.equal(finished, 3)
  assert.equal(rows[0].no, 2)
  assert.equal(rows[0].partner, '王五')
  assert.equal(rows[0].score, '5 : 11')           // 我方 5、对手 11
  assert.equal(rows[0].won, false)
})

test('对手按另一侧的两人给出，并带场地与时间', () => {
  const { rows } = buildRankDetail(rounds(), 1, 'win')
  assert.deepEqual(rows[0].opponents, ['王五', '赵六'])
  assert.equal(rows[0].where, '1号场 19:20')
})

test('站在 B 队视角时比分不反、编号仍一致', () => {
  // 2 号在大多数场里是 A 队，这里专门取第2场（他是 B 队、且赢了）
  const { rows } = buildRankDetail(rounds(), 2, 'win')
  const m2 = rows.find((r) => r.no === 2)
  assert.ok(m2, JSON.stringify(rows))
  assert.equal(m2.score, '11 : 5')                // 他是 B 队 → 取 score_b 在前
  assert.equal(m2.partner, '赵六')
  assert.deepEqual(m2.opponents, ['张三', '王五'])
})

test('已结束但没有胜方的脏数据：跳过，不冤枉成"负"，也不计入已结束', () => {
  const dirty = [
    {
      id: 1, round_number: 1, matches: [
        match(201, pair(1, [1, '张三'], [2, '李四']), pair(2, [3, '王五'], [4, '赵六']), null, 11, 7),
        match(202, pair(3, [1, '张三'], [3, '王五']), pair(4, [2, '李四'], [4, '赵六']), 4, 5, 11),
      ],
    },
  ]
  const lose = buildRankDetail(dirty, 1, 'lose')
  assert.equal(lose.rows.length, 1)               // 只剩真正输掉的那场
  assert.equal(lose.finished, 1)                  // 脏数据不算"已结束"
})

test('没参赛的人 / 空赛程 / 缺 userId 都安全返回空', () => {
  assert.deepEqual(buildRankDetail(rounds(), 99, 'win'), { rows: [], finished: 0 })
  assert.deepEqual(buildRankDetail([], 1, 'win'), { rows: [], finished: 0 })
  assert.deepEqual(buildRankDetail(null, 1, 'win'), { rows: [], finished: 0 })
  assert.deepEqual(buildRankDetail(rounds(), undefined, 'win'), { rows: [], finished: 0 })
  assert.deepEqual(buildRankDetail(rounds(), 0, 'win'), { rows: [], finished: 0 })
})

test('场地/时间缺失时 where 为空串，不出现多余分隔符', () => {
  const noWhere = [
    { id: 1, round_number: 1, matches: [
      { ...match(301, pair(1, [1, '张三'], [2, '李四']), pair(2, [3, '王五'], [4, '赵六']), 1, 11, 3),
        court_name: null, start_time: null },
    ] },
  ]
  const { rows } = buildRankDetail(noWhere, 1, 'win')
  assert.equal(rows[0].where, '')
})
