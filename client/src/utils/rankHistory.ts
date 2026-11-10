/**
 * 「参赛排名趋势图」的纯逻辑：把后端 `/auth/stats/{id}/history` 的点列
 * 换算成**单位坐标系**（0~1）上的折线，SVG 组件只负责乘宽高。
 *
 * 为什么不直接在组件里算：这里的规则全是边界条件（单人赛、退赛场次、
 * 只打了一场、名次为 0），画到屏幕上一眼看不出来对不对，必须能单测。
 * 参照 utils/rankDetail.ts 的做法：算点位不碰 DOM，测试直接喂数据。
 *
 * 三条口径（与后端 core/ranking.py 对齐）：
 * - 纵坐标 = **相对位置** `(rank-1)/(total-1)`，不是绝对名次。
 *   各场人数从 4 到 64 不等，绝对刻度会把"8 人第 3"和"24 人第 3"画得一样高。
 *   归一化后：第 1 名恒在顶（0），最后一名恒在底（1），跨场次可比。
 *   （真实名次与人数由 `rankLabel` 提供，如 "3/8"，标注在点上，信息不丢。）
 * - 退赛（rank=0）不参与名次：画在底部灰点、**断开折线**，不连出假趋势。
 * - 一场都没打完（0 场）照常给位置：后端名次与积分榜一致，前端只在提示里标注。
 */

/** 后端 `/auth/stats/{id}/history` 的单项（字段与 schemas/auth.py 一一对应） */
export interface RankHistoryItem {
  tournament_id: number
  title: string
  start_date: string
  status: string
  /** 0 = 无名次（该场退赛） */
  rank: number
  /** 名次的分母：该场在赛人数 */
  total_players: number
  dropped_players: number
  matches_played: number
  matches_won: number
  is_active: boolean
}

export interface RankPoint {
  id: number
  title: string
  /** 横坐标，0~1（等距排布，不按真实时间间隔 —— 空档几个月不该占半屏） */
  x: number
  /** 纵坐标，0 = 第 1 名（顶部）、1 = 最后一名（底部） */
  y: number
  rank: number
  total: number
  droppedPlayers: number
  /** "3/8"，退赛时为 "-" */
  rankLabel: string
  /** 轴标签，MM-DD */
  dateLabel: string
  /** 该场赛事是否已结束（没结束的名次还会变） */
  finished: boolean
  /** 退赛：不占名次、不连线 */
  unranked: boolean
  matchesPlayed: number
  matchesWon: number
}

export interface RankSegment {
  /** points 里的下标，至少 2 个点才成线 */
  indexes: number[]
  /** 这一段包含没结束的赛事 → 画虚线（名次还会变） */
  dashed: boolean
}

export interface RankHistory {
  points: RankPoint[]
  segments: RankSegment[]
  /** 参赛次数 = 点数（必须与用户点进来的那个数字相等） */
  total: number
  /** 名次最好 / 最差的那一场（只算有名次的），单场时为同一个 */
  best: RankPoint | null
  worst: RankPoint | null
  /** 有没有任何一场有真实名次（退赛→false，此时不该画轴） */
  hasRanked: boolean
}

/** 赛事开始时间正序；同一天的按 id 兜底，避免刷新后顺序互换 */
function byTime(a: RankHistoryItem, b: RankHistoryItem): number {
  const da = String(a.start_date || '')
  const db = String(b.start_date || '')
  if (da !== db) return da < db ? -1 : 1
  return a.tournament_id - b.tournament_id
}

/** "2026-11-08T19:00:00" → "11-08"（取不到就给空串，不假装有日期） */
export function mmdd(value: any): string {
  const s = typeof value === 'string' ? value : ''
  return s.length >= 10 ? s.slice(5, 10) : ''
}

/**
 * 把后端数据换算成单位坐标系上的点列。
 *
 * @param items 后端返回的 items（顺序不作要求，这里会排）
 */
export function buildRankHistory(items: RankHistoryItem[] | null | undefined): RankHistory {
  const list = (items || []).slice().sort(byTime)
  const n = list.length
  const points: RankPoint[] = list.map((it, i) => {
    const total = Math.max(0, Number(it.total_players) || 0)
    const rank = Math.max(0, Number(it.rank) || 0)
    const unranked = rank <= 0
    // 分母 total-1：第 1 名 → 0，最后一名 → 1。
    // total <= 1（单人赛/唯一在赛者）时没有"区间"可言，一律画在顶部，
    // 否则会除零得到 NaN/Infinity，整条折线消失。
    let y = 0
    if (unranked) y = 1        // 退赛：沉到最底部的独立条带（不连线）
    else if (total > 1) y = (rank - 1) / (total - 1)
    return {
      id: it.tournament_id,
      title: it.title || '',
      x: n <= 1 ? 0.5 : i / (n - 1),
      y,
      rank,
      total,
      droppedPlayers: Math.max(0, Number(it.dropped_players) || 0),
      rankLabel: unranked ? '-' : `${rank}/${total}`,
      dateLabel: mmdd(it.start_date),
      finished: it.status === 'finished',
      unranked,
      matchesPlayed: Math.max(0, Number(it.matches_played) || 0),
      matchesWon: Math.max(0, Number(it.matches_won) || 0),
    }
  })

  // 折线只在"有名次"的连续点之间连；退赛点是断开处（一段一根线）
  const segments: RankSegment[] = []
  let current: number[] = []
  points.forEach((p, i) => {
    if (p.unranked) {
      if (current.length) segments.push({ indexes: current, dashed: false })
      current = []
      return
    }
    current.push(i)
  })
  if (current.length) segments.push({ indexes: current, dashed: false })
  // 段内只要有没结束的赛事，这条线就是"还会动的"，画虚线
  for (const seg of segments) {
    seg.dashed = seg.indexes.some((i) => !points[i].finished)
  }
  // 只有一个点的段不画线（组件会自己判断），这里保留，让它也能被渲染成点
  const ranked = points.filter((p) => !p.unranked)
  let best: RankPoint | null = null
  let worst: RankPoint | null = null
  for (const p of ranked) {
    if (!best || p.rank < best.rank) best = p
    if (!worst || p.rank > worst.rank) worst = p
  }

  return {
    points,
    segments: segments.filter((s) => s.indexes.length >= 2),
    total: n,
    best,
    worst,
    hasRanked: ranked.length > 0,
  }
}

/**
 * 参赛排名趋势图的辅助统计（给图上方那排小数字用）。
 *
 * 只统计**有名次**的场次：退赛场次沉底、没有名次，混进"平均位置"会把
 * 平均水平拖到很后面，看着像退步，其实只是退了一次赛。
 */
export interface RankSummary {
  /** 最好的那一场（名次最小） */
  best: RankPoint | null
  /** 最差的那一场（名次最大） */
  worst: RankPoint | null
  /** 平均相对位置 0~1（0 = 常年第 1，1 = 常在末位）；没有有效场次时为 null */
  avgPos: number | null
  /** 有名次的场次数 */
  ranked: number
}

export function summarize(history: RankHistory): RankSummary {
  const ranked = history.points.filter((p) => !p.unranked)
  if (!ranked.length) return { best: null, worst: null, avgPos: null, ranked: 0 }
  const avg = ranked.reduce((s, p) => s + p.y, 0) / ranked.length
  return { best: history.best, worst: history.worst, avgPos: avg, ranked: ranked.length }
}

/**
 * 单调三次样条（Fritsch–Carlson）生成的平滑折线路径。
 *
 * 为什么不用普通的 Catmull-Rom / 二次贝塞尔：那种平滑会**过冲** ——
 * 在两个点之间拱出一个比两端更高的弧，看上去像"那周更好"，而数据里根本没有
 * 那个值。单调插值保证曲线落在相邻两点之间，图上读出来的高低都作数。
 *
 * 入参/返回值都只是数字（不用管像素还是 0~1），所以能单测：
 * 1 个点返回空串（画不出线），2 个点退化成直线段。
 */
export function smoothPath(points: { x: number; y: number }[]): string {
  const n = points.length
  if (n === 0) return ''
  const f = (v: number) => Math.round(v * 100) / 100
  if (n === 1) return ''
  if (n === 2) {
    return `M ${f(points[0].x)} ${f(points[0].y)} L ${f(points[1].x)} ${f(points[1].y)}`
  }

  // 相邻点斜率
  const dx: number[] = []
  const slope: number[] = []
  for (let i = 0; i < n - 1; i++) {
    dx.push(points[i + 1].x - points[i].x)
    slope.push((points[i + 1].y - points[i].y) / (dx[i] || 1))
  }
  // 每个点的切线：端点取单侧斜率，中间取两段斜率的加权平均；
  // 反号（局部极值）时切线强制为 0 —— 这样极值处不会冲出数据范围
  const tangent: number[] = [slope[0]]
  for (let i = 1; i < n - 1; i++) {
    tangent.push(slope[i - 1] * slope[i] <= 0 ? 0 : (slope[i - 1] + slope[i]) / 2)
  }
  tangent.push(slope[n - 2])

  let d = `M ${f(points[0].x)} ${f(points[0].y)}`
  for (let i = 0; i < n - 1; i++) {
    const h = dx[i]
    const c1x = points[i].x + h / 3
    const c1y = points[i].y + (tangent[i] * h) / 3
    const c2x = points[i + 1].x - h / 3
    const c2y = points[i + 1].y - (tangent[i + 1] * h) / 3
    d += ` C ${f(c1x)} ${f(c1y)}, ${f(c2x)} ${f(c2y)}, ${f(points[i + 1].x)} ${f(points[i + 1].y)}`
  }
  return d
}

/** 纵轴刻度（自上而下的相对位置）：0 = 第 1 名，1 = 最后一名 */
export const Y_TICKS = [0, 0.5, 1]

/**
 * 默认只画最近多少场。
 *
 * 为什么需要：一个人每周打一场，两年就破百场。100 个点挤在 320px 宽的图上，
 * 点间距只有 3px、圆点直径 9px —— 点上互相盖住、连点击热区也重叠，选不中
 * 特定一场；横轴只剩首尾两个日期标签，中间看不出是哪场。所以超过这个数量
 * 默认折叠到"最近 N 场"，想看全部再点开（那时会改为横向更密的画法）。
 *
 * 24 是按 320px / 约 12px 一个点倒推的：再密就没法点准了。
 */
export const RECENT_LIMIT = 24

export interface RecentSlice {
  /** 已按赛事时间正序 */
  items: RankHistoryItem[]
  /** 被折叠掉的场次数（0 = 全都显示） */
  hidden: number
}

/**
 * 取最近 limit 场（按赛事时间；输入顺序不作要求）。
 * `limit <= 0` 表示不限制（返回全量，供"查看全部"用）。
 */
export function pickRecent(
  items: RankHistoryItem[] | null | undefined,
  limit: number = RECENT_LIMIT,
): RecentSlice {
  const list = (items || []).slice().sort(byTime)
  if (limit <= 0 || list.length <= limit) return { items: list, hidden: 0 }
  return { items: list.slice(-limit), hidden: list.length - limit }
}
