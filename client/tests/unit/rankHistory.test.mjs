/**
 * utils/rankHistory 单测：参赛排名趋势图的点位换算。
 *
 * 重点守住：
 * 1. 纵坐标是**相对位置**（第1名=0 在顶、最后一名=1 在底），不是绝对名次
 * 2. 退赛（rank=0）不参与名次、沉底、且断开折线（不伪造趋势）
 * 3. 单人赛 / 只打了一场的边界不除零（NaN 会让整条线消失）
 * 4. 点数恒等于参赛次数（点进来的数字和图上点数必须一致）
 */
import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildRankHistory, mmdd, pickRecent, RECENT_LIMIT, smoothPath, summarize,
} from '../../src/utils/rankHistory.ts'

const item = (over = {}) => ({
  tournament_id: 1,
  title: '周赛',
  start_date: '2026-11-01T19:00:00',
  status: 'finished',
  rank: 1,
  total_players: 8,
  dropped_players: 0,
  matches_played: 7,
  matches_won: 5,
  is_active: true,
  ...over,
})

test('空数据：0 场、没有名次点，不抛异常', () => {
  for (const v of [[], null, undefined]) {
    const h = buildRankHistory(v)
    assert.equal(h.total, 0)
    assert.deepEqual(h.points, [])
    assert.deepEqual(h.segments, [])
    assert.equal(h.hasRanked, false)
    assert.equal(h.best, null)
    assert.equal(h.worst, null)
  }
})

test('纵坐标按该场人数归一化：第1名在顶、末位在底、中间按比例', () => {
  const h = buildRankHistory([
    item({ tournament_id: 1, rank: 1, total_players: 8 }),
    item({ tournament_id: 2, rank: 3, total_players: 8 }),
    item({ tournament_id: 3, rank: 8, total_players: 8 }),
  ])
  assert.deepEqual(h.points.map((p) => p.y), [0, 2 / 7, 1])
})

test('人数不同的两场，同样第 3 名的高度不同（这正是归一化的意义）', () => {
  const h = buildRankHistory([
    item({ tournament_id: 1, rank: 3, total_players: 8 }),   // 2/7 ≈ 0.29
    item({ tournament_id: 2, rank: 3, total_players: 24 }),  // 2/23 ≈ 0.09
  ])
  assert.ok(h.points[0].y > h.points[1].y)
})

test('单人赛（total=1）不除零，画在顶部', () => {
  const h = buildRankHistory([item({ rank: 1, total_players: 1, matches_played: 0 })])
  assert.equal(h.hasRanked, true)
  assert.equal(h.points[0].y, 0)
  assert.equal(h.points[0].rankLabel, '1/1')
  // 只有一个点：横坐标居中，且不成线段
  assert.equal(h.points[0].x, 0.5)
  assert.deepEqual(h.segments, [])
})

test('横坐标等距：首点 0、末点 1，不看真实时间间隔', () => {
  const h = buildRankHistory([
    item({ tournament_id: 1, start_date: '2026-01-01T19:00:00' }),
    item({ tournament_id: 2, start_date: '2026-01-08T19:00:00' }),
    item({ tournament_id: 3, start_date: '2026-09-30T19:00:00' }),  // 隔了大半年也不多占宽
  ])
  assert.deepEqual(h.points.map((p) => p.x), [0, 0.5, 1])
})

test('乱序输入按赛事时间正序排，同一天按 id 兜底', () => {
  const h = buildRankHistory([
    item({ tournament_id: 3, start_date: '2026-11-08T19:00:00' }),
    item({ tournament_id: 1, start_date: '2026-11-01T19:00:00' }),
    item({ tournament_id: 2, start_date: '2026-11-01T09:00:00' }),
  ])
  assert.deepEqual(h.points.map((p) => p.id), [2, 1, 3])
})

test('退赛：沉底、标"-"、不连线；后面的点重新开一段', () => {
  const h = buildRankHistory([
    item({ tournament_id: 1, rank: 2 }),
    item({ tournament_id: 2, rank: 0, is_active: false, total_players: 7 }),
    item({ tournament_id: 3, rank: 4 }),
  ])
  const dropped = h.points[1]
  assert.equal(dropped.unranked, true)
  assert.equal(dropped.y, 1)
  assert.equal(dropped.rankLabel, '-')
  // 折线在退赛处断开：两段，各只有 1 个点 → 都不到成线的长度，被过滤掉
  assert.deepEqual(h.segments, [])
  assert.equal(h.hasRanked, true)   // 还有别的有效点，轴照画
})

test('连续 3 场有名次 → 一段折线；中间有进行中的 → 该段虚线', () => {
  const done = [
    item({ tournament_id: 1, rank: 2 }),
    item({ tournament_id: 2, rank: 3 }),
    item({ tournament_id: 3, rank: 5 }),
  ]
  const h1 = buildRankHistory(done)
  assert.equal(h1.segments.length, 1)
  assert.deepEqual(h1.segments[0].indexes, [0, 1, 2])
  assert.equal(h1.segments[0].dashed, false)

  const h2 = buildRankHistory([
    done[0],
    done[1],
    item({ tournament_id: 3, rank: 5, status: 'ongoing' }),
  ])
  assert.equal(h2.segments[0].dashed, true)
  assert.equal(h2.points[2].finished, false)
})

test('最好/最差按名次取（只算有名次的场次）', () => {
  const h = buildRankHistory([
    item({ tournament_id: 1, rank: 5 }),
    item({ tournament_id: 2, rank: 1 }),
    item({ tournament_id: 3, rank: 9 }),
    item({ tournament_id: 4, rank: 0, is_active: false }),
  ])
  assert.equal(h.best.id, 2)
  assert.equal(h.worst.id, 3)
})

test('点数恒等于参赛次数，标签带真实名次与人数', () => {
  const items = Array.from({ length: 12 }, (_, i) =>
    item({ tournament_id: i + 1, rank: (i % 8) + 1, total_players: 8 }))
  const h = buildRankHistory(items)
  assert.equal(h.total, 12)
  assert.equal(h.points.length, 12)       // 「参赛次数 12」点开就是 12 个点
  assert.equal(h.points[0].rankLabel, '1/8')
})

test('日期标签取 MM-DD，取不到给空串不崩', () => {
  assert.equal(mmdd('2026-11-08T19:00:00'), '11-08')
  assert.equal(mmdd('2026-11-08'), '11-08')
  assert.equal(mmdd(''), '')
  assert.equal(mmdd(null), '')
  assert.equal(mmdd('2026-1-8'), '')
})

/* ---------------- pickRecent：场次很多时折叠到最近 N 场 ----------------
 * 100 个点挤在 320px 宽的图上，点间距 3px、圆点直径 9px，互相盖住又点不准；
 * 所以超过上限默认只画最近的，"查看全部"才铺开。这里守的是"取哪一段"和
 * "折了多少"，别把最早的那几场当成最近的（顺序错就整张图错）。
 */
const series = (n) => Array.from({ length: n }, (_, i) => item({
  tournament_id: i + 1,
  start_date: `2026-${String(1 + Math.floor(i / 28)).padStart(2, '0')}-${String(1 + (i % 28)).padStart(2, '0')}T19:00:00`,
}))

test('pickRecent：不超过上限时全给，不折叠', () => {
  const s = pickRecent(series(5))
  assert.equal(s.items.length, 5)
  assert.equal(s.hidden, 0)
})

test('pickRecent：超过上限只给最近 N 场，折掉的场次数如实报告', () => {
  const s = pickRecent(series(100))
  assert.equal(s.items.length, RECENT_LIMIT)
  assert.equal(s.hidden, 100 - RECENT_LIMIT)
  // 留下的是**最后**（最近）那 24 场，不是最前面那 24 场
  assert.deepEqual(s.items.map((i) => i.tournament_id),
    Array.from({ length: RECENT_LIMIT }, (_, k) => 100 - RECENT_LIMIT + k + 1))
})

test('pickRecent：输入乱序也按时间取最近的', () => {
  const s = pickRecent(series(30).reverse(), 3)
  assert.deepEqual(s.items.map((i) => i.tournament_id), [28, 29, 30])
})

test('pickRecent：limit<=0 表示不限（查看全部用）', () => {
  const s = pickRecent(series(100), 0)
  assert.equal(s.items.length, 100)
  assert.equal(s.hidden, 0)
})

test('pickRecent：空输入安全', () => {
  for (const v of [[], null, undefined]) {
    assert.deepEqual(pickRecent(v), { items: [], hidden: 0 })
  }
})

test('折叠后再画图：点数 = 折叠后的场次数，且仍按时间正序', () => {
  const sliced = pickRecent(series(100))
  const h = buildRankHistory(sliced.items)
  assert.equal(h.total, RECENT_LIMIT)
  assert.equal(h.points[0].x, 0)
  assert.equal(h.points[h.points.length - 1].x, 1)
  assert.ok(h.points[0].id < h.points[h.points.length - 1].id)
})

/* ---------------- smoothPath：单调三次插值 ----------------
 * 用单调插值而不是普通圆角平滑，是因为后者会在两点之间拱出比两端更高的弧，
 * 看着像"那周更好"，而数据里根本没有那个值。这里守的就是"不许过冲"。
 */
test('smoothPath：0/1 个点画不出线，2 个点退化成直线段', () => {
  assert.equal(smoothPath([]), '')
  assert.equal(smoothPath([{ x: 5, y: 5 }]), '')
  assert.equal(smoothPath([{ x: 0, y: 0 }, { x: 10, y: 20 }]), 'M 0 0 L 10 20')
})

test('smoothPath：多点走贝塞尔，首尾锚在真实数据点上', () => {
  const d = smoothPath([{ x: 0, y: 80 }, { x: 10, y: 40 }, { x: 20, y: 60 }])
  assert.ok(d.startsWith('M 0 80'), d)
  assert.ok(d.includes('C'), d)
  assert.ok(d.endsWith('20 60'), d)
})

// 解析出所有 C 段，检查控制点没有越出相邻两点的纵向范围（= 不过冲）
function controlYs(d) {
  const nums = d.match(/-?\d+(?:\.\d+)?/g).map(Number)
  // M x y 之后每 6 个数是一段 C
  const out = []
  for (let i = 2; i + 5 < nums.length; i += 6) {
    out.push({ c1y: nums[i + 1], c2y: nums[i + 3], endY: nums[i + 5], startY: nums[i - 1] })
  }
  return out
}

test('smoothPath：控制点不过冲（不会画出数据里没有的高低点）', () => {
  // 锯齿数据：最容易让普通平滑鼓出假峰的形状
  const pts = [{ x: 0, y: 100 }, { x: 10, y: 10 }, { x: 20, y: 100 }, { x: 30, y: 10 }]
  const segs = controlYs(smoothPath(pts))
  assert.equal(segs.length, 3)
  for (const s of segs) {
    const lo = Math.min(s.startY, s.endY)
    const hi = Math.max(s.startY, s.endY)
    assert.ok(s.c1y >= lo - 0.01 && s.c1y <= hi + 0.01, JSON.stringify(s))
    assert.ok(s.c2y >= lo - 0.01 && s.c2y <= hi + 0.01, JSON.stringify(s))
  }
})

test('smoothPath：一条水平线不会被画成波浪', () => {
  const d = smoothPath([{ x: 0, y: 50 }, { x: 10, y: 50 }, { x: 20, y: 50 }])
  for (const s of controlYs(d)) {
    assert.equal(s.c1y, 50)
    assert.equal(s.c2y, 50)
  }
})

/* ---------------- summarize：图上方的"最好/最差/平均" ----------------
 * 退赛场次没有名次，混进"平均位置"会把平均水平拖到很后面，
 * 看着像退步，其实只是退了一次赛。
 */
test('summarize：空/全退赛时不编造平均值', () => {
  const empty = summarize(buildRankHistory([]))
  assert.equal(empty.avgPos, null)
  assert.equal(empty.best, null)
  assert.equal(empty.ranked, 0)

  const allDropped = summarize(buildRankHistory([
    item({ tournament_id: 1, rank: 0, is_active: false }),
  ]))
  assert.equal(allDropped.avgPos, null)
  assert.equal(allDropped.ranked, 0)
})

test('summarize：平均位置只算有名次的场次（退赛不拉低平均）', () => {
  const h = buildRankHistory([
    item({ tournament_id: 1, rank: 1, total_players: 8 }),   // y = 0
    item({ tournament_id: 2, rank: 8, total_players: 8 }),   // y = 1
    item({ tournament_id: 3, rank: 0, is_active: false }),   // 沉底但不参与
  ])
  const s = summarize(h)
  assert.equal(s.ranked, 2)
  assert.equal(s.avgPos, 0.5)      // (0 + 1) / 2，若算上退赛会大于 0.5
  assert.equal(s.best.id, 1)
  assert.equal(s.worst.id, 2)
})
