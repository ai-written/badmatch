/**
 * 积分榜「点胜/负数字看明细」的纯逻辑（不碰 DOM、不查接口，便于单测）。
 *
 * 输入是 `/tournaments/{id}/rounds` 的原始赛程，输出某一档（胜/负）的场次列表。
 *
 * 几条口径（都对整齐过）：
 * - **只算已结束的比赛**：进行中的不计入，与 PlayerStats 的 `matches_won/lost`
 *   （结算时才累加）同源，所以「列表条数 = 数字」；
 * - **已结束但没有胜方**的场次直接跳过：那是历史脏数据，算成"负"是冤枉人，
 *   而且当年结算时也没为它记过账（`_finalize_match` 一定写 winner）；
 * - 「第 N 场」用与对阵表同一个 `withGlobalIndex`（轮次顺序 + 轮内 id），
 *   两处编号必须一致，否则用户拿着"第 3 场"去对阵表找不到；
 * - 比分按**我方视角**给（我这边在前），胜负用颜色区分更直观。
 */
// 显式带 .ts：单测用 node --test 直接跑源码（Node 的类型擦除按真实路径解析，	sconfig 已开 allowImportingTsExtensions）
import { withGlobalIndex } from './schedule.ts'

export interface RankMatchRow {
  matchId: number
  /** 与对阵表一致的「第 N 场」 */
  no: number
  won: boolean
  /** 我方比分在前，例如 "11 : 7" */
  score: string
  partner: string
  opponents: string[]
  /** 「1号场 19:20」，场地/时间没配时为空串 */
  where: string
}

export interface RankDetail {
  rows: RankMatchRow[]
  /** 该选手**已结束**的场次总数（弹层标题「已结束 N 场」用它，和 rows 自洽） */
  finished: number
}

/** HH:MM:SS / HH:MM[:SS] → HH:MM（没有就返回空串） */
function hhmm(value: any): string {
  const s = typeof value === 'string' ? value : ''
  return s.length >= 5 ? s.slice(0, 5) : ''
}

export function buildRankDetail(
  rounds: any[],
  userId: number | undefined | null,
  result: 'win' | 'lose',
): RankDetail {
  if (!userId) return { rows: [], finished: 0 }
  const rows: RankMatchRow[] = []
  let finished = 0

  for (const r of withGlobalIndex(rounds || [])) {
    for (const m of r.matches || []) {
      if (m.status !== 'finished') continue
      if (m.winner_pairing_id == null) continue
      const pa = m.pairing_a
      const pb = m.pairing_b
      const inA = pa?.player_a?.id === userId || pa?.player_b?.id === userId
      const inB = pb?.player_a?.id === userId || pb?.player_b?.id === userId
      if (!inA && !inB) continue

      finished += 1
      const mine = inA ? pa : pb
      const other = inA ? pb : pa
      const won = m.winner_pairing_id === mine.id
      if (won !== (result === 'win')) continue

      const partner = mine.player_a.id === userId ? mine.player_b : mine.player_a
      const mineScore = inA ? m.score_a : m.score_b
      const oppScore = inA ? m.score_b : m.score_a

      rows.push({
        matchId: m.id,
        no: m.globalIdx,
        won,
        score: `${mineScore ?? '-'} : ${oppScore ?? '-'}`,
        partner: partner?.username || '未知',
        opponents: [other?.player_a?.username, other?.player_b?.username].filter(Boolean) as string[],
        where: [m.court_name, hhmm(m.start_time)].filter(Boolean).join(' '),
      })
    }
  }
  return { rows, finished }
}
